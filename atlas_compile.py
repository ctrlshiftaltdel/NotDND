#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atlas_compile.py —— 世界编译器（ATLAS 切片 I2，Issue #64）

设计依据：`ATLAS-DESIGN.md` §4（词表与扫描）、§5（编译与生成）、§6.4（setting_rev）。
把一份 `world.module` JSON 和 `atlas.lexicon` 词表编译成世界帧：
增删区域只改这份 JSON（或词表），不改编译代码。

约定
  · 编译器**不知道任何世界名字**：不出现 `if world_key == ...`，
    源码不含任何世界的 key（§6.4）。一切差异都来自词表命中。
  · 扫描范围只限 §4.3 的短字段：regions 的 name / summary / atmosphere、
    key_places / events（性状、数字、锚，不取方位）、glossary 的 term /
    meaning（世界级性状）、world_rules 的字符串值（跳过 `*_source` 键与
    `docs/...md:行号` 形式的值）。**不读** history / factions / tracks /
    careers / abilities / equipment / enemies / random_tables / powers。
  · 全编译器只有两个长度常数（§5.1）：SETTLEMENT_CELL_KM / MARCH_CELL_KM。
  · 零第三方依赖，仅 Python 3 标准库；只调用 `atlas.py` 内核，不改内核。

对外接口
    compile_world(world, lexicon, seed=0) -> atlas
        atlas 是 `atlas.py` 的纯数据地图，额外附带三个编译信息键：
        scale（settlement | march）、world_traits（世界级性状）、
        region_info（每个区域的档位 / 脚印 / 地点帧规格 / 关键地点规格）。
"""

from __future__ import annotations

import hashlib
import json
import random
import re

import atlas as al

# ════════════════════════════════════════════════════════════════════════
# 常数（§5.1：全编译器只有这两个长度常数，改手感只改这里）
# ════════════════════════════════════════════════════════════════════════

#: 聚落尺度下，一格约两公里（把文中的公里数换成脚印）。
SETTLEMENT_CELL_KM = 2
#: 旷野尺度下，一格约一日路程。
MARCH_CELL_KM = 30

#: 数字 token：阿拉伯数字（允许千分位逗号）或中文数字（零到千，含「两」）。
_NUM = r"(?:[0-9][0-9,]*|[零一二两三四五六七八九十百千]+)"

#: `docs/...md:行号` 形式的值不参与 world_rules 字符串扫描（§4.3）。
_SOURCE_RE = re.compile(r"^docs/(system|scenario)/[^:]+\.md:[0-9]+$")

#: 「围绕」摘录的截止标点（§4.4：标点之前、至多 12 个字）。
_PUNCT = set("，。、；：！？…—·～()（）[]「」『』《》〈〉【】"
             "\u201c\u201d\u2018\u2019")

#: 方位词（§4.4 末段）：按此顺序逐个尝试，先命中先生效；方位不进词表。
_DIRECTION_WORDS = ("西北", "东北", "西南", "东南",
                    "北方", "南方", "东方", "西方",
                    "中央", "中心", "北", "南", "东", "西")

#: 方位 → 锚点约束。
_DIRECTION_CONSTRAINT = {
    "西北": lambda x, y: x < 0 and y > 0,
    "东北": lambda x, y: x > 0 and y > 0,
    "西南": lambda x, y: x < 0 and y < 0,
    "东南": lambda x, y: x > 0 and y < 0,
    "北方": lambda x, y: y > 0,
    "南方": lambda x, y: y < 0,
    "东方": lambda x, y: x > 0,
    "西方": lambda x, y: x < 0,
    "中央": lambda x, y: True,
    "中心": lambda x, y: True,
    "北": lambda x, y: y > 0,
    "南": lambda x, y: y < 0,
    "东": lambda x, y: x > 0,
    "西": lambda x, y: x < 0,
}


# ════════════════════════════════════════════════════════════════════════
# 词表匹配（§4.2：按词长从长到短，先命中先生效）
# ════════════════════════════════════════════════════════════════════════

class _Lexicon:
    """编译期词表：term → trait 的扁平索引，按词长降序匹配。

    同一个词允许属于多个 trait（§4.4 初版词表即如此，如「山脉」既是
    wilderness 又是 barrier）；§4.2 只要求 terms 在**同一 trait 内**不重复。
    """

    def __init__(self, lexicon):
        if lexicon.get("kind") != "atlas.lexicon":
            raise ValueError("词表 kind 应为 atlas.lexicon: %r"
                             % (lexicon.get("kind"),))
        self.term_traits = {}
        for entry in lexicon.get("entries", []):
            trait = entry["trait"]
            seen = set()
            for term in entry["terms"]:
                if term in seen:
                    raise ValueError("trait %s 内词 %r 重复" % (trait, term))
                seen.add(term)
                self.term_traits.setdefault(term, set()).add(trait)
        self.sorted_terms = sorted(self.term_traits, key=len, reverse=True)

    def match(self, text):
        """一段文本命中的性状集合。"""
        traits = set()
        for term in self.sorted_terms:
            if term in text:
                traits |= self.term_traits[term]
        return traits


# ════════════════════════════════════════════════════════════════════════
# 数字识别（§4.5）
# ════════════════════════════════════════════════════════════════════════

_CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}


def _cn_to_int(token):
    """数字 token → int。阿拉伯（含千分位逗号）或中文（零到千、含「两」）。"""
    if token[:1].isdigit():
        return int(token.replace(",", ""))
    total, current, matched = 0, 0, False
    for ch in token:
        if ch in _CN_DIGIT:
            current = _CN_DIGIT[ch]
            matched = True
        elif ch in _CN_UNIT:
            if current == 0:
                current = 1          # 「十四」的十前没有数字
            total += current * _CN_UNIT[ch]
            current = 0
            matched = True
        else:
            return None
    return total + current if matched else None


def _mask(text, spans):
    """把已消费的区间盖掉，避免同一句被后一遍口径重复计数。"""
    chars = list(text)
    for start, end in spans:
        for i in range(start, end):
            chars[i] = "□"
    return "".join(chars)


def _scan_numbers(text):
    """单段文本的数字抽取。

    先认「公里」再认「米」（§4.5）；「第 N 层」是地标层、
    「负 N 层 / 地下 N 层」向下、「N 层」才是总层数——按此顺序
    逐遍扫描并遮盖已消费片段。「直径 N 公里」先换成半径再算。
    """
    out = {"radii": [], "total": 0, "down": 0, "landmark": 0, "height_m": None}
    s = text

    spans = []
    for m in re.finditer(r"(半径|直径)?\s*(%s)\s*公里" % _NUM, s):
        value = _cn_to_int(m.group(2))
        if value is None:
            continue
        out["radii"].append(value / 2.0 if m.group(1) == "直径" else float(value))
        spans.append(m.span())
    s = _mask(s, spans)

    spans = []
    for m in re.finditer(r"高约\s*(%s)\s*米" % _NUM, s):
        value = _cn_to_int(m.group(1))
        if value is not None:
            out["height_m"] = max(out["height_m"] or 0, value)
            spans.append(m.span())
    s = _mask(s, spans)

    spans = []
    for m in re.finditer(r"第\s*(%s)\s*层" % _NUM, s):
        value = _cn_to_int(m.group(1))
        if value is not None:
            out["landmark"] = max(out["landmark"], value)
            spans.append(m.span())
    s = _mask(s, spans)

    spans = []
    for m in re.finditer(r"(?:负|地下)\s*(%s)\s*层" % _NUM, s):
        value = _cn_to_int(m.group(1))
        if value is not None:
            out["down"] = max(out["down"], value)
            spans.append(m.span())
    s = _mask(s, spans)

    for m in re.finditer(r"(%s)\s*层" % _NUM, s):
        value = _cn_to_int(m.group(1))
        if value is not None:
            out["total"] = max(out["total"], value)
    return out


def _merge_numbers(scans):
    """多段文本的数字合并：半径 / 层数各取最大。"""
    merged = {"radius_km": None, "total": 0, "down": 0,
              "landmark": 0, "height_m": None}
    for scan in scans:
        if scan["radii"]:
            radius = max(scan["radii"])
            if merged["radius_km"] is None or radius > merged["radius_km"]:
                merged["radius_km"] = radius
        merged["total"] = max(merged["total"], scan["total"])
        merged["down"] = max(merged["down"], scan["down"])
        merged["landmark"] = max(merged["landmark"], scan["landmark"])
        if scan["height_m"] is not None:
            merged["height_m"] = max(merged["height_m"] or 0, scan["height_m"])
    return merged


# ════════════════════════════════════════════════════════════════════════
# 尺度、脚印与地点帧规格（§5.2 / §5.3）
# ════════════════════════════════════════════════════════════════════════

def _clamp(value, low, high):
    return max(low, min(high, value))


def _span_tier(traits, radius_km):
    """区域跨度档（§5.2）。"""
    if "span_country" in traits or (radius_km is not None and radius_km >= 20):
        return 3
    if "wilderness" in traits or (radius_km is not None and radius_km >= 5):
        return 2
    if "settlement" in traits:
        return 0
    return 1


def _footprint(tier, radius_km, kp_count):
    """脚印边长（格）与每格公里数（§5.3）。返回 (edge, cell_km)。"""
    if tier == 0:  # 聚落
        if radius_km is not None:
            edge = _clamp(int(round(radius_km / SETTLEMENT_CELL_KM)), 1, 8)
        else:
            edge = 1
        if kp_count >= 3:
            edge = max(edge, 2)
        return edge, SETTLEMENT_CELL_KM
    # 旷野口径（含档 1：没有公里数时其余为 1）
    if radius_km is not None:
        edge = _clamp(int(round(radius_km / MARCH_CELL_KM)), 1, 8)
    else:
        edge = {3: 3, 2: 2}.get(tier, 1)
    return edge, MARCH_CELL_KM


def _site_spec(nums, traits):
    """地点帧层数规格（§5.3）：z_lo / z_hi / floors。高度不进世界脚印。"""
    total, down = nums["total"], nums["down"]
    landmark, height_m = nums["landmark"], nums["height_m"]
    if down > 0:
        # 已有向下的层数：总层数若写明，向上部分扣除地下；否则只留地面入口。
        up = max(1, total - down) if total > 0 else 1
    else:
        candidates = [total, landmark]
        if height_m:
            candidates.append(int(round(height_m / 10.0)))  # 每层约 10 米
        candidates.append(3 if "vertical" in traits else 1)
        up = max(candidates)
        if "below" in traits:
            down = up  # 偏向负 z：同样的层数向负 z 生成
    up = _clamp(up, 1, 80)
    down = _clamp(down, 0, 80)
    return {"floors": up + down, "z_lo": -down, "z_hi": up}


# ════════════════════════════════════════════════════════════════════════
# 扫描（§4.3 / §4.4）
# ════════════════════════════════════════════════════════════════════════

def _walk_rule_strings(node, out):
    """world_rules 里的字符串值：跳过 `*_source` 键与 docs 溯源形式的值。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key.endswith("_source"):
                continue
            _walk_rule_strings(value, out)
    elif isinstance(node, list):
        for value in node:
            _walk_rule_strings(value, out)
    elif isinstance(node, str):
        if not _SOURCE_RE.match(node):
            out.append(node)


def _world_scan(world, matcher):
    """世界级性状与文本（§4.3：glossary 的 term/meaning + world_rules 字符串）。"""
    glossary_texts = []
    for entry in world.get("glossary", []):
        if isinstance(entry, dict):
            glossary_texts.append(entry.get("term") or "")
            glossary_texts.append(entry.get("meaning") or "")
    rule_strings = []
    _walk_rule_strings(world.get("world_rules", {}), rule_strings)
    traits = set()
    for text in glossary_texts + rule_strings:
        traits |= matcher.match(text)
    return traits, glossary_texts, rule_strings


def _main_name(name):
    """区域主名：全角竖线之前的部分（§4.4）。"""
    return (name or "").split("｜", 1)[0]


def _around_target(summary, main_names, own_name):
    """summary 里「围绕」指向的区域主名；对不上就忽略（§4.4）。"""
    if not summary:
        return None
    idx = summary.find("围绕")
    if idx < 0:
        return None
    snippet = []
    for ch in summary[idx + 2:]:
        if ch in _PUNCT:
            break
        snippet.append(ch)
        if len(snippet) >= 12:
            break
    snippet = "".join(snippet)
    for name in sorted(main_names, key=len, reverse=True):
        if name and name != own_name and name in snippet:
            return name
    return None


def _direction(main_texts):
    """方位词（只在 name / summary / atmosphere 里找，§4.3）。"""
    joined = "".join(main_texts)
    for word in _DIRECTION_WORDS:
        if word in joined:
            return word
    return None


# ════════════════════════════════════════════════════════════════════════
# 摆放（§5.4）
# ════════════════════════════════════════════════════════════════════════

def _ring_positions(limit=96):
    """从原点向外一圈圈产出候选锚点；同圈按 (|x|+|y|, x, y) 排序，确定性的。"""
    for ring in range(limit + 1):
        cells = [(x, y) for x in range(-ring, ring + 1)
                 for y in range(-ring, ring + 1)
                 if max(abs(x), abs(y)) == ring]
        cells.sort(key=lambda p: (abs(p[0]) + abs(p[1]), p[0], p[1]))
        for pos in cells:
            yield pos


def _cells_of(anchor, edge):
    """脚印占用的格（w = h = edge 的方形）。"""
    return {(anchor[0] + i, anchor[1] + j)
            for i in range(edge) for j in range(edge)}


def _spot_key(pos):
    return (max(abs(pos[0]), abs(pos[1])), abs(pos[0]) + abs(pos[1]), pos[0], pos[1])


def _find_spot(occupied, edge, constraint=None, adjacent_to=None):
    """给一个区域找锚点：先试围绕邻接位，再从原点螺旋找空位。"""
    if adjacent_to:
        candidates = set()
        for (cx, cy) in adjacent_to:
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                candidates.add((cx + dx, cy + dy))
        for anchor in sorted(candidates, key=_spot_key):
            cells = _cells_of(anchor, edge)
            if cells & occupied:
                continue
            if constraint and not constraint(*anchor):
                continue
            return anchor
    for anchor in _ring_positions():
        cells = _cells_of(anchor, edge)
        if cells & occupied:
            continue
        if constraint and not constraint(*anchor):
            continue
        return anchor
    raise RuntimeError("找不到可摆放的空位（edge=%d）" % edge)


# ════════════════════════════════════════════════════════════════════════
# 编译
# ════════════════════════════════════════════════════════════════════════

def _setting_rev(scan):
    """setting_rev（§6.4）：编译器所读字段的规范化 JSON 的 SHA-256 前 16 位。"""
    payload = json.dumps(scan, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:16]


def _add_link_traits(atlas, frame_id, a, b, via, traits, **kwargs):
    """建连接并给两端记录补 traits（内核 add_link 不带 traits 参数）。"""
    al.add_link(atlas, frame_id, a, b, via, **kwargs)
    if not traits:
        return
    pa = al.find_place(atlas, a)
    pb = al.find_place(atlas, b)
    for target, other_via in ((pa, via), (pb, al.VIA_OPPOSITE[via])):
        for record in target["links"]:
            if record["to"] == (b if target is pa else a) \
                    and record["via"] == other_via:
                record.setdefault("traits", [])
                for t in traits:
                    if t not in record["traits"]:
                        record["traits"].append(t)


def compile_world(world, lexicon, seed=0):
    """把一份 world.module 编译成世界帧（atlas）。

    world：world.module dict；lexicon：atlas.lexicon dict；seed：无约束区域的
    摆放松弛次序。同一输入连编两次，区域 id 与有约束的相对方位相同
    （§5.4 / 验收）。返回 atlas.py 形状的纯数据地图，附带
    scale / world_traits / region_info 三个编译信息键。
    """
    if world.get("kind") != "world.module":
        raise ValueError("world kind 应为 world.module: %r" % (world.get("kind"),))
    matcher = _Lexicon(lexicon)

    winfo = world.get("world") or {}
    key = winfo.get("key")
    if not key:
        raise ValueError("world.world.key 缺失")

    regions = world.get("regions", [])
    main_names = [_main_name(r.get("name")) for r in regions]
    world_traits, glossary_texts, rule_strings = _world_scan(world, matcher)

    # ── 逐区域分析（§4.3 / §4.4 / §4.5） ──────────────────────────────
    analysis = {}
    scan_regions = []
    for region in regions:
        rid = region.get("id")
        name = region.get("name") or ""
        summary = region.get("summary") or ""
        atmosphere = region.get("atmosphere") or ""
        kps = [kp for kp in region.get("key_places", []) if isinstance(kp, str)]
        events = [ev for ev in region.get("events", []) if isinstance(ev, str)]
        main_texts = [name, summary, atmosphere]
        aux_texts = kps + events

        traits = set()
        for text in main_texts + aux_texts:
            traits |= matcher.match(text)

        nums = _merge_numbers([_scan_numbers(t) for t in main_texts + aux_texts])
        tier = _span_tier(traits, nums["radius_km"])
        edge, cell_km = _footprint(tier, nums["radius_km"], len(kps))
        site = _site_spec(nums, traits)

        kp_specs = []
        for index, kp in enumerate(kps):
            kp_traits = set(matcher.match(kp))
            kp_nums = _merge_numbers([_scan_numbers(kp)])
            kp_specs.append({
                "index": index,
                "name": kp,
                "traits": sorted(kp_traits),
                "spec": _site_spec(kp_nums, traits | kp_traits),
            })

        analysis[rid] = {
            "id": rid,
            "full_id": "%s/%s" % (key, rid),
            "name": name,
            "main_name": _main_name(name),
            "traits": traits,
            "direction": _direction(main_texts),
            "around": _around_target(summary, main_names, _main_name(name)),
            "tier": tier,
            "edge": edge,
            "cell_km": cell_km,
            "footprint_km": edge * cell_km,
            "site": site,
            "kp_specs": kp_specs,
            # 自己的文本命中才有帧级后果（§5.3：只有自身文本命中的区域进抽象帧）
            "own_abstract": "abstract" in traits,
            "own_unlisted": "unlisted" in traits,
            "own_unstable": "unstable" in traits,
        }
        scan_regions.append({
            "id": rid, "name": name, "summary": summary,
            "atmosphere": atmosphere, "key_places": kps, "events": events,
        })

    # 世界尺度（§5.2）：档 ≥2 的区域不少于档 0 的区域 → march
    tier2 = sum(1 for a in analysis.values() if a["tier"] >= 2)
    tier0 = sum(1 for a in analysis.values() if a["tier"] == 0)
    scale = "march" if tier2 >= tier0 else "settlement"

    # ── 建帧（§5.3） ──────────────────────────────────────────────────
    atlas = al.new_atlas(key, seed)
    atlas["scale"] = scale
    atlas["world_traits"] = sorted(world_traits)
    atlas["setting_rev"] = _setting_rev({
        "key": key,
        "regions": scan_regions,
        "glossary": glossary_texts,
        "world_rules_strings": sorted(rule_strings),
    })

    surface_id = "%s/surface" % key
    al.add_frame(atlas, surface_id, space="metric", z_meaning="层",
                 cell="街区" if scale == "settlement" else "日路程")

    abstract_id = "%s/abstract" % key
    has_abstract = "abstract" in world_traits or \
        any(a["own_abstract"] for a in analysis.values())
    if has_abstract:
        al.add_frame(atlas, abstract_id, space="abstract", z_meaning="抽象层",
                     cell="一次走位")

    seam_id = "%s/seam" % key
    has_seam = "unstable" in world_traits or \
        any(a["own_unstable"] for a in analysis.values())
    if has_seam:
        al.add_frame(atlas, seam_id, space="abstract", z_meaning="夹隙",
                     cell="一跳")

    # ── 摆放（§5.4） ──────────────────────────────────────────────────
    placeable = [a for a in analysis.values()
                 if not a["own_abstract"] and not a["own_unlisted"]]
    placed = {}          # rid -> (x, y)
    occupied = set()     # 街面已占格

    def place(a, constraint=None, adjacent_to=None):
        anchor = _find_spot(occupied, a["edge"], constraint, adjacent_to)
        occupied.update(_cells_of(anchor, a["edge"]))
        placed[a["id"]] = anchor
        return anchor

    name_to_rid = {a["main_name"]: a["id"] for a in analysis.values()}
    targets = sorted({a["around"] for a in analysis.values() if a["around"]})

    # 1. 被「围绕」指到的区域先放（没有方位的，靠近原点）
    for target_name in targets:
        rid = name_to_rid.get(target_name)
        if rid is not None and rid in analysis and rid not in placed \
                and analysis[rid] in placeable:
            place(analysis[rid])
    # 2. 围绕者：贴着目标放（保持相邻）
    for a in sorted((a for a in placeable if a["around"]),
                    key=lambda x: x["full_id"]):
        if a["id"] in placed:
            continue
        target_rid = name_to_rid.get(a["around"])
        adjacent = None
        if target_rid in placed:
            adjacent = _cells_of(placed[target_rid], analysis[target_rid]["edge"])
        place(a, _DIRECTION_CONSTRAINT.get(a["direction"]), adjacent)
    # 3. 有方位的区域放进对应象限
    for a in sorted((a for a in placeable
                     if a["direction"] and a["id"] not in placed),
                    key=lambda x: x["full_id"]):
        place(a, _DIRECTION_CONSTRAINT.get(a["direction"]))
    # 4. 其余按脚印面积从大到小；面积相同时的相对次序由种子决定
    rest = [a for a in placeable if a["id"] not in placed]
    random.Random(seed).shuffle(rest)
    rest.sort(key=lambda a: -(a["edge"] * a["edge"]))  # 稳定排序保留种子次序
    for a in rest:
        place(a)

    # ── 登记区域与关键地点（§3.2 标识 / §5.5 关键地点是锚） ───────────
    region_info = {}
    surface_regions = []      # 有街面坐标的区域 id（摆了坐标的）
    for a in sorted(analysis.values(), key=lambda x: x["full_id"]):
        rid_full = a["full_id"]
        frame_id = abstract_id if a["own_abstract"] else surface_id
        place_data = {
            "id": rid_full,
            "name": a["name"],
            "kind": "region",
            "frame_id": frame_id,
            "traits": sorted(a["traits"]),
            "source": "authored",
        }
        if a["id"] in placed:
            anchor = placed[a["id"]]
            place_data.update({"x": anchor[0], "y": anchor[1], "z": 0,
                               "footprint": {"w": a["edge"], "h": a["edge"]}})
            surface_regions.append(rid_full)
        al.add_place(atlas, place_data)

        for kp in a["kp_specs"]:
            kp_id = "%s/k%d" % (rid_full, kp["index"])
            al.add_place(atlas, {
                "id": kp_id,
                "name": kp["name"],
                "kind": "site",
                "frame_id": frame_id,
                "traits": list(kp["traits"]),
                "source": "authored",
            })
            al.add_link(atlas, frame_id, rid_full, kp_id, "门")

        region_info[rid_full] = {
            "tier": a["tier"],
            "edge": a["edge"],
            "cell_km": a["cell_km"],
            "footprint_km": a["footprint_km"],
            "traits": sorted(a["traits"]),
            "direction": a["direction"],
            "around": a["around"],
            "site": a["site"],
            "kp_specs": a["kp_specs"],
        }

    # ── 地面连接：脚印相接的区域建立连接（§5.4 第 5 条） ────────────────
    ground_band = "short" if scale == "settlement" else "medium"
    cells_by_rid = {a["full_id"]: _cells_of(placed[a["id"]], a["edge"])
                    for a in placeable if a["id"] in placed}
    traits_by_rid = {a["full_id"]: a["traits"] for a in analysis.values()}
    ordered_rids = sorted(cells_by_rid)
    for i, rid_a in enumerate(ordered_rids):
        for rid_b in ordered_rids[i + 1:]:
            for delta, via in (((0, 1), "北"), ((0, -1), "南"),
                               ((1, 0), "东"), ((-1, 0), "西")):
                if any((x + delta[0], y + delta[1]) in cells_by_rid[rid_b]
                       for (x, y) in cells_by_rid[rid_a]):
                    band = ground_band
                    if scale == "march" and (
                            "barrier" in traits_by_rid[rid_a]
                            or "barrier" in traits_by_rid[rid_b]):
                        band = "dangerous"  # 带 barrier 的边升到危险档（§5.2）
                    al.add_link(atlas, surface_id, rid_a, rid_b, via, band=band)

    # 整图不连通时补「途经」格，或补一条连接（§5.4 第 5 条）
    _bridge_components(atlas, key, surface_id, analysis, placed,
                       cells_by_rid, ground_band)

    # ── 夹隙（seam）：命中的区域用一条 unstable 连接进去（§5.3 / §6.1） ─
    if has_seam:
        seam_nodes = []
        for a in sorted(analysis.values(), key=lambda x: x["full_id"]):
            if not a["own_unstable"]:
                continue
            node_id = "%s/g0_0_0" % a["full_id"]
            al.add_place(atlas, {
                "id": node_id, "name": "夹隙", "kind": "fill",
                "frame_id": seam_id, "traits": ["unstable"],
                "source": "generated",
            })
            # 命中区域 → 夹隙：unstable 连接；区域是锚点，这条边不会被重摇
            al.add_link(atlas, seam_id, a["full_id"], node_id, "门",
                        unstable=True)
            seam_nodes.append(node_id)
        for prev, node in zip(seam_nodes, seam_nodes[1:]):
            # 夹隙内部：非锚点的边可重摇（reroll_unstable 的对象）
            al.add_link(atlas, seam_id, prev, node, "连接", unstable=True)

    # ── 抽象帧与街面之间的连接（§5.3 / §6.3：世界级 carry 时带 carry） ─
    if has_abstract and surface_regions:
        for a in sorted(analysis.values(), key=lambda x: x["full_id"]):
            if not a["own_abstract"]:
                continue
            traits = ["carry"] if "carry" in world_traits else []
            _add_link_traits(atlas, abstract_id, a["full_id"],
                             surface_regions[0], "门", traits)

    # ── z = −1 的捷径路网（§5.2：world_rules 命中 shortcut 时） ────────
    if "shortcut" in world_traits:
        network = []
        for rid_full in ordered_rids:
            anchor = placed[rid_full.rsplit("/", 1)[1]]
            node_id = "%s/g%d_%d_-1" % (rid_full, anchor[0], anchor[1])
            al.add_place(atlas, {
                "id": node_id, "name": "途经", "kind": "fill",
                "frame_id": surface_id, "traits": ["network"],
                "source": "generated",
                "x": anchor[0], "y": anchor[1], "z": -1,
            })
            al.add_link(atlas, surface_id, rid_full, node_id, "下",
                        band="short")
            network.append(node_id)
        for prev, node in zip(network, network[1:]):
            al.add_link(atlas, surface_id, prev, node, "连接", band="short")

    atlas["region_info"] = region_info
    return atlas


# ════════════════════════════════════════════════════════════════════════
# 连通性补偿（§5.4 第 5 条）
# ════════════════════════════════════════════════════════════════════════

def _touching(cells_a, cells_b):
    """两块脚印是否正交相接。"""
    for (x, y) in cells_a:
        if ((x + 1, y) in cells_b or (x - 1, y) in cells_b
                or (x, y + 1) in cells_b or (x, y - 1) in cells_b):
            return True
    return False


def _via_between(cells_a, cells_b):
    """从 a 的格心指向 b 的格心的基本方向。"""
    ax = sum(x for x, _ in cells_a) / len(cells_a)
    ay = sum(y for _, y in cells_a) / len(cells_a)
    bx = sum(x for x, _ in cells_b) / len(cells_b)
    by = sum(y for _, y in cells_b) / len(cells_b)
    if by > ay:
        return "北"
    if by < ay:
        return "南"
    if bx > ax:
        return "东"
    return "西"


def _find_bridge_cell(occupied, cells_a, cells_b):
    """找一块同时邻接两块的空格放「途经」；找不到返回 None。"""
    candidates = set()
    for cells in (cells_a, cells_b):
        for (x, y) in cells:
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                pos = (x + dx, y + dy)
                if pos not in occupied:
                    candidates.add(pos)
    for pos in sorted(candidates, key=_spot_key):
        if _touching({pos}, cells_a) and _touching({pos}, cells_b):
            return pos
    return None


def _bridge_components(atlas, key, surface_id, analysis, placed,
                       cells_by_rid, ground_band):
    """地面图不连通时：补一块「途经」fill 格，或补一条连接（§5.4 第 5 条）。

    fill 的名字用「途经」，性状从两端复制危险与地貌，不新造专名。
    """
    occupied = set()
    for cells in cells_by_rid.values():
        occupied |= cells

    def components():
        adjacency = {rid: set() for rid in cells_by_rid}
        rids = sorted(cells_by_rid)
        for i, a in enumerate(rids):
            for b in rids[i + 1:]:
                if _touching(cells_by_rid[a], cells_by_rid[b]):
                    adjacency[a].add(b)
                    adjacency[b].add(a)
        seen, groups = set(), []
        for rid in rids:
            if rid in seen:
                continue
            stack, group = [rid], set()
            while stack:
                cur = stack.pop()
                if cur in group:
                    continue
                group.add(cur)
                stack.extend(adjacency[cur] - group)
            seen |= group
            groups.append(sorted(group))
        return groups

    groups = components()
    while len(groups) > 1:
        # 找锚点距离最近的两块（确定性）
        best = None
        for i, group_a in enumerate(groups):
            for group_b in groups[i + 1:]:
                for ra in group_a:
                    for rb in group_b:
                        pa, pb = placed[ra], placed[rb]
                        dist = max(abs(pa[0] - pb[0]), abs(pa[1] - pb[1]))
                        cand = (dist, ra, rb)
                        if best is None or cand < best:
                            best = cand
        dist, ra, rb = best
        fill_anchor = _find_bridge_cell(occupied, cells_by_rid[ra],
                                        cells_by_rid[rb])
        if fill_anchor is not None:
            shared = analysis[ra]["traits"] & analysis[rb]["traits"]
            fill_traits = sorted(shared & {"barrier", "wilderness",
                                           "settlement", "water"})
            fill_id = "%s/g%d_%d_0" % (key, fill_anchor[0], fill_anchor[1])
            al.add_place(atlas, {
                "id": fill_id, "name": "途经", "kind": "fill",
                "frame_id": surface_id, "traits": fill_traits,
                "source": "generated",
                "x": fill_anchor[0], "y": fill_anchor[1], "z": 0,
            })
            cells_by_rid[fill_id] = _cells_of(fill_anchor, 1)
            analysis[fill_id] = {"traits": set(fill_traits)}
            placed[fill_id] = fill_anchor
            occupied |= _cells_of(fill_anchor, 1)
            for endpoint in (ra, rb):
                via = _via_between(cells_by_rid[fill_id], cells_by_rid[endpoint])
                al.add_link(atlas, surface_id, fill_id, endpoint, via,
                            band=ground_band)
        else:
            via = _via_between(cells_by_rid[ra], cells_by_rid[rb])
            al.add_link(atlas, surface_id, ra, rb, via, band=ground_band)
        groups = components()
