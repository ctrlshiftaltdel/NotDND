#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atlas.py —— 自动地图内核（ATLAS 切片 I1，Issue #63）

设计依据：`ATLAS-DESIGN.md` §3.3 内核契约。本模块只提供**内核**：
帧、地点、连接、移动、出口、切片、图距离投影、增量与存档块。
不知道任何世界名字，不读 `data/worlds/`，不调用模型。

设计约定
  · 零第三方依赖（仅 Python 3 标准库）；`import atlas` **无副作用**。
  · 所有状态都是纯数据（dict / list / 基本类型），可直接 JSON 序列化，
    存档块（§3.4）与 `RuleSession.scene` 平级，由后续切片（I4）接线。
  · 坐标只在帧内有意义：帧与帧之间**不换算坐标**，只靠连接相通。
  · 移动只走 `link`（门 / 楼梯 / 地面连接都是 link）；对角不穿墙——
    没有连接的对角相邻格不可达，图距离照常计算。
  · 本模块不改 `prism_core`：行程时段、风险池由调用方处理，
    内核只返回档位 id（short / medium / long / dangerous）。

内核对外函数（签名由 §3.3 锁定，后续切片只调用、不改签名）
  new_atlas / add_frame / add_place / add_link
  move / exits / slice_text / range_band / high_ground / guide_card
  apply_delta / export_state / restore_state
  lost_destination / reroll_unstable / carry_across
"""

from __future__ import annotations

import copy

# ════════════════════════════════════════════════════════════════════════
# 词表常量（§3.1 / §3.3）
# ════════════════════════════════════════════════════════════════════════

#: 连接方向。北南东西是平面四向，上下是竖向，门 / 连接不分方向。
VIA_CARDINAL = ("北", "南", "东", "西")
VIA_VERTICAL = ("上", "下")
VIA_PORTAL = ("门", "连接")
VIA_ALL = VIA_CARDINAL + VIA_VERTICAL + VIA_PORTAL

#: via 的反向。门 / 连接自反。
VIA_OPPOSITE = {"北": "南", "南": "北", "东": "西", "西": "东",
                "上": "下", "下": "上", "门": "门", "连接": "连接"}

#: 平面四向的坐标步进（北 = y+1，即切片里朝上）。
VIA_DELTA = {"北": (0, 1), "南": (0, -1), "东": (1, 0), "西": (-1, 0)}

#: 行程档位（§5.2：档位 id 由内核返回，时段换算归调用方）。
BANDS = ("short", "medium", "long", "dangerous")

#: 帧的空间口径（§3.1）：metric 可用米换算跨区；abstract 一格一次走位。
SPACES = ("metric", "abstract")

#: 发现层级（§3.1）。unseen 不进切片；entered 高于 seen，不降级。
DISCOVERED = ("unseen", "seen", "entered")

#: 地点种类（§3.1）。
KINDS = ("region", "fill", "site", "room", "zone", "feature")

#: 增量操作（§3.4）。
DELTA_OPS = ("set_link", "set_trait", "discover", "add_feature", "remove")

#: 存档块版本（§3.4）。
ATLAS_STATE_VERSION = 1


# ════════════════════════════════════════════════════════════════════════
# 建构：atlas / frame / place / link
# ════════════════════════════════════════════════════════════════════════

def new_atlas(world_key, seed):
    """新建一张空地图。seed 供后续生成器（I3）复现地点帧。"""
    return {
        "version": ATLAS_STATE_VERSION,
        "world_key": world_key,
        "seed": seed,
        "setting_rev": None,          # §6.4：由编译器（I2）填
        "frames": {},
    }


def add_frame(atlas, frame_id, *, space, z_meaning, cell):
    """新增一帧。space ∈ metric|abstract；z_meaning / cell 是给切片
    表头与地点卡用的短说明（如「楼层」「街区」「日路程」「一次走位」）。"""
    if frame_id in atlas["frames"]:
        raise ValueError("帧已存在: %s" % frame_id)
    if space not in SPACES:
        raise ValueError("未知 space: %r" % (space,))
    frame = {
        "space": space,
        "z_meaning": z_meaning,
        "cell": cell,
        "seed": None,                 # 生成器（I3）可回填，存档块导出
        "deltas": [],                 # §3.4：本帧的增量流水
        "places": {},
    }
    atlas["frames"][frame_id] = frame
    return frame


def normalize_place(place, *, default_source="authored"):
    """补齐 §3.1 的字段默认值；缺坐标的地点允许没有 x / y / z。"""
    out = dict(place)
    for key in ("id", "name", "kind", "frame_id"):
        if not out.get(key):
            raise ValueError("place 缺少字段 %s: %r" % (key, place.get("id")))
    if out["kind"] not in KINDS:
        raise ValueError("未知 kind: %r" % (out["kind"],))
    out.setdefault("footprint", {"w": 1, "h": 1})
    out.setdefault("links", [])
    out.setdefault("traits", [])
    out.setdefault("discovered", "unseen")
    if out["discovered"] not in DISCOVERED:
        raise ValueError("未知 discovered: %r" % (out["discovered"],))
    out.setdefault("source", default_source)
    if out["source"] not in ("authored", "generated"):
        raise ValueError("未知 source: %r" % (out["source"],))
    return out


def add_place(atlas, place):
    """登记一个地点。id 全图唯一（§3.2 标识不由坐标生成）。"""
    place = normalize_place(place)
    frame = atlas["frames"].get(place["frame_id"])
    if frame is None:
        raise ValueError("帧不存在: %s" % place["frame_id"])
    if find_place(atlas, place["id"]) is not None:
        raise ValueError("地点 id 已存在: %s" % place["id"])
    frame["places"][place["id"]] = place
    return place


def _link_norm(**kwargs):
    link = {
        "to": kwargs["to"],
        "via": kwargs["via"],
        "band": kwargs.get("band"),
        "oneway": bool(kwargs.get("oneway", False)),
        "unstable": bool(kwargs.get("unstable", False)),
    }
    if link["via"] not in VIA_ALL:
        raise ValueError("未知 via: %r" % (link["via"],))
    if link["band"] is not None and link["band"] not in BANDS:
        raise ValueError("未知 band: %r" % (link["band"],))
    if kwargs.get("traits"):
        link["traits"] = list(kwargs["traits"])
    return link


def add_link(atlas, frame_id, a, b, via, *, band=None, oneway=False, unstable=False):
    """在 a、b 之间建立一条连接。门 / 楼梯 / 地面连接都是 link。

    frame_id 是这条连接所属的帧（§3.3）。同帧连接是常态；跨帧连接
    （抽象帧 ↔ 街面，§5.3 / §6.3）也允许，此时 frame_id 记录声明方。
    非 oneway 时，两端各存一条方向相反的记录。返回 a 端的 link。
    """
    if frame_id not in atlas["frames"]:
        raise ValueError("帧不存在: %s" % frame_id)
    pa = find_place(atlas, a)
    pb = find_place(atlas, b)
    if pa is None:
        raise ValueError("地点不存在: %s" % a)
    if pb is None:
        raise ValueError("地点不存在: %s" % b)
    if a == b:
        raise ValueError("自环连接无意义: %s" % a)
    link = _link_norm(to=b, via=via, band=band, oneway=oneway,
                      unstable=unstable)
    pa["links"].append(link)
    if not oneway:
        pb["links"].append(_link_norm(to=a, via=VIA_OPPOSITE[via], band=band,
                                      oneway=False, unstable=unstable))
    return link


# ════════════════════════════════════════════════════════════════════════
# 查询：地点定位 / 出口 / 移动 / 地点卡
# ════════════════════════════════════════════════════════════════════════

def find_place(atlas, place_id):
    """跨帧按 id 找地点（§3.2 id 全图唯一）。找不到返回 None。"""
    for frame in atlas["frames"].values():
        place = frame["places"].get(place_id)
        if place is not None:
            return place
    return None


def _place_coords(place):
    """地点坐标三元组；缺坐标返回 None。"""
    if all(key in place for key in ("x", "y", "z")):
        return (place["x"], place["y"], place["z"])
    return None


def _locus_at(place):
    """由地点生成一个 locus；缺坐标的地点不带 x / y / z。"""
    locus = {"frame_id": place["frame_id"], "place_id": place["id"]}
    coords = _place_coords(place)
    if coords is not None:
        locus["x"], locus["y"], locus["z"] = coords
    return locus


def move(atlas, locus, via):
    """沿 via 移动。只走 link：没有对应出口就不动，返回 error。

    返回 {locus, place, error}；成功时 locus 已落到目的地坐标
    （缺坐标的目的地不带坐标），place 是目的地点位。
    """
    place = find_place(atlas, locus.get("place_id"))
    if place is None:
        return {"locus": locus, "place": None, "error": "队伍所在地点不存在"}
    for link in place["links"]:
        if link["via"] == via:
            dest = find_place(atlas, link["to"])
            if dest is None:
                return {"locus": locus, "place": None,
                        "error": "出口的另一端已不存在: %s" % link["to"]}
            return {"locus": _locus_at(dest), "place": dest, "error": None}
    return {"locus": locus, "place": None, "error": "这里没有通往「%s」的出口" % via}


def exits(atlas, locus):
    """当前地点的出口列表：[{via, name, place_id, band, unstable}]。"""
    place = find_place(atlas, locus.get("place_id"))
    if place is None:
        return []
    result = []
    for link in place["links"]:
        dest = find_place(atlas, link["to"])
        if dest is None:
            continue
        result.append({
            "via": link["via"],
            "name": dest["name"],
            "place_id": dest["id"],
            "band": link.get("band"),
            "unstable": bool(link.get("unstable")),
        })
    return result


def guide_card(atlas, locus):
    """地点卡（§3 导言：导引者只读地点卡，不读整张图）。"""
    place = find_place(atlas, locus.get("place_id"))
    if place is None:
        return None
    frame = atlas["frames"][place["frame_id"]]
    return {
        "locus": dict(locus),
        "place": copy.deepcopy(place),
        "frame": {"space": frame["space"], "z_meaning": frame["z_meaning"],
                  "cell": frame["cell"]},
        "exits": exits(atlas, locus),
    }


# ════════════════════════════════════════════════════════════════════════
# 切片与投影
# ════════════════════════════════════════════════════════════════════════

def slice_text(atlas, locus, radius=8):
    """当前层字符切片（§2：只画当前 z、只画已发现的格）。

    北朝上（y 大的在上一行）。@ 是队伍，其余格取地点名首字，
    未发现的格与其他 z 一律不画。
    """
    frame = atlas["frames"].get(locus.get("frame_id"))
    place = find_place(atlas, locus.get("place_id"))
    if frame is None or place is None:
        return "（没有可显示的地图）"
    here = _place_coords(place)
    if here is None:
        return "（当前地点没有坐标，无法切片）"
    lx, ly, lz = here
    radius = max(0, int(radius))

    # 收集要画的格：本帧、本 z、已发现、在半径内。 footprint 按 w×h 铺开。
    cells = {}
    for pid in sorted(frame["places"]):
        p = frame["places"][pid]
        coords = _place_coords(p)
        if coords is None or coords[2] != lz:
            continue
        if p["discovered"] == "unseen":
            continue
        if max(abs(coords[0] - lx), abs(coords[1] - ly)) > radius:
            continue
        w = max(1, int(p.get("footprint", {}).get("w", 1)))
        h = max(1, int(p.get("footprint", {}).get("h", 1)))
        for dx in range(w):
            for dy in range(h):
                cells[(coords[0] + dx, coords[1] + dy)] = p["name"][0]
    cells[(lx, ly)] = "@"  # 队伍始终可见

    xs = [x for x, _ in cells]
    ys = [y for _, y in cells]
    lines = ["%s · %s=%d · %s" % (locus["frame_id"], frame["z_meaning"], lz,
                                  frame["cell"])]
    for y in range(max(ys), min(ys) - 1, -1):   # 北（y+1）在上一行
        lines.append("".join(cells.get((x, y), "·")
                             for x in range(min(xs), max(xs) + 1)))
    lines.append("图例：@ 队伍　· 空　其他=地点名首字")
    return "\n".join(lines)


def _graph_distances(atlas, frame_id, start_id, *, reverse=False):
    """本帧内从 start 出发的图距离（BFS）。跨帧连接不计入——
    距离投影「只用本帧的图距离」（§3.3）。reverse=True 时沿反向边
    （用于问「到 intended 还有多远」）。不可达的地点不在结果里。"""
    frame = atlas["frames"].get(frame_id)
    if frame is None or start_id not in frame["places"]:
        return {}
    dist = {start_id: 0}
    frontier = [start_id]
    while frontier:
        nxt = []
        for pid in frontier:
            if reverse:
                # 反向边：收集所有指向 pid 的连接（oneway 边不能倒走）
                links = [{"to": other["id"]}
                         for other in frame["places"].values()
                         for l in other["links"] if l["to"] == pid]
            else:
                links = frame["places"][pid]["links"]
            for link in links:
                to = link["to"]
                if to in frame["places"] and to not in dist:
                    dist[to] = dist[pid] + 1
                    nxt.append(to)
        frontier = nxt
    return dist


def range_band(atlas, frame_id, a, b):
    """图距离投影（§3.3）：0 同区，1 相邻，2 近，3–4 中，更远为远。

    只用本帧的连接求图距离，与坐标米数无关（abstract 帧同样适用）。
    """
    da = _graph_distances(atlas, frame_id, a)
    if b not in da:
        return "far"
    d = da[b]
    if d == 0:
        return "same"
    if d == 1:
        return "adjacent"
    if d == 2:
        return "near"
    if d <= 4:
        return "mid"
    return "far"


def high_ground(atlas, frame_id, a, b):
    """a 是否对 b 占高地（§3.3）：b 的 z 更低、平面切比雪夫距离 ≤ 1，
    且两者之间存在上 / 下或相邻连接。缺坐标一律 False。"""
    pa = find_place(atlas, a)
    pb = find_place(atlas, b)
    if pa is None or pb is None:
        return False
    ca, cb = _place_coords(pa), _place_coords(pb)
    if ca is None or cb is None:
        return False
    if cb[2] >= ca[2]:
        return False
    if max(abs(ca[0] - cb[0]), abs(ca[1] - cb[1])) > 1:
        return False
    for link in pa["links"]:
        if link["to"] != b:
            continue
        if link["via"] in VIA_VERTICAL or link["via"] in VIA_CARDINAL:
            return True
    return False


# ════════════════════════════════════════════════════════════════════════
# 增量与存档块（§3.4）
# ════════════════════════════════════════════════════════════════════════

def _record_delta(atlas, place_id, op, payload):
    """把增量记入该地点所在帧的流水，供 export_state 带走。"""
    place = find_place(atlas, place_id)
    frame_id = place["frame_id"] if place else None
    delta = {"op": op, "place_id": place_id, "payload": payload}
    if frame_id is not None:
        atlas["frames"][frame_id]["deltas"].append(copy.deepcopy(delta))
    return delta


def apply_delta(atlas, delta):
    """应用一条增量：{op, place_id, payload}。这是增量进图的唯一入口，
    应用即记入所在帧的 deltas 流水（§3.4）。op 见 DELTA_OPS。"""
    op = delta["op"]
    if op not in DELTA_OPS:
        raise ValueError("未知增量 op: %r" % (op,))
    place_id = delta["place_id"]
    payload = delta.get("payload") or {}

    if op == "add_feature":
        if find_place(atlas, place_id) is not None:
            raise ValueError("地点 id 已存在: %s" % place_id)
        place = dict(payload)
        place["id"] = place_id
        add_place(atlas, place)                    # 先入图，再记流水
        _record_delta(atlas, place_id, op, copy.deepcopy(payload))
        return place

    place = find_place(atlas, place_id)
    if place is None:
        raise ValueError("地点不存在: %s" % place_id)

    if op == "remove":
        # 删除后 find_place 就找不到帧了，先记流水再摘除
        _record_delta(atlas, place_id, op, copy.deepcopy(payload))
        frame = atlas["frames"][place["frame_id"]]
        del frame["places"][place_id]
        for other in atlas["frames"].values():
            for p in other["places"].values():
                p["links"] = [l for l in p["links"] if l["to"] != place_id]
        return place

    if op == "set_link":
        link = _link_norm(**payload)
        place["links"].append(link)
    elif op == "set_trait":
        trait = payload["trait"]
        if payload.get("value", True):
            if trait not in place["traits"]:
                place["traits"].append(trait)
        else:
            place["traits"] = [t for t in place["traits"] if t != trait]
    elif op == "discover":
        level = payload.get("level", "seen")
        if level not in ("seen", "entered"):
            raise ValueError("未知发现层级: %r" % (level,))
        # entered 不降级为 seen
        order = {name: i for i, name in enumerate(DISCOVERED)}
        if order[level] > order[place["discovered"]]:
            place["discovered"] = level

    _record_delta(atlas, place_id, op, copy.deepcopy(payload))


def export_state(atlas, locus):
    """导出 §3.4 存档块。世界帧可整份留在存档里；地点帧 / 战术帧
    能按种子重算的只存种子和增量——内核统一导出「种子 + 增量」，
    由 I4 接线时决定哪一帧整份内联。"""
    return {
        "version": ATLAS_STATE_VERSION,
        "world_key": atlas["world_key"],
        "seed": atlas["seed"],
        "setting_rev": atlas.get("setting_rev"),
        "party_locus": copy.deepcopy(locus),
        "frames": {
            fid: {"seed": frame.get("seed"),
                  "deltas": copy.deepcopy(frame["deltas"])}
            for fid, frame in atlas["frames"].items()
        },
    }


def _relocate_party(atlas, old_locus):
    """队伍原地点已不存在：改放到同帧里坐标最接近的 authored 区域；
    没有旧坐标就放同帧第一个区域。返回 (place, note) 或 (None, None)。"""
    frame = atlas["frames"].get(old_locus.get("frame_id"))
    frames = [frame] if frame else list(atlas["frames"].values())
    old = None
    if old_locus is not None and all(k in old_locus for k in ("x", "y", "z")):
        old = (old_locus["x"], old_locus["y"], old_locus["z"])
    for candidate_frames in (frames, list(atlas["frames"].values())):
        regions = []
        for f in candidate_frames:
            for pid in sorted(f["places"]):
                p = f["places"][pid]
                if p["source"] != "authored" or p["kind"] != "region":
                    continue
                coords = _place_coords(p)
                if coords is None:
                    continue
                d = max(abs(coords[i] - old[i]) for i in range(3)) if old else 0
                regions.append((d, pid, p))
        if regions:
            regions.sort(key=lambda item: (item[0], item[1]))
            return regions[0][2], None
        if old is None:
            break  # 没有旧坐标也不必跨帧再找一轮
    # 连带坐标的区域都没有：退到第一个 authored 地点
    for f in atlas["frames"].values():
        for pid in sorted(f["places"]):
            p = f["places"][pid]
            if p["source"] == "authored":
                return p, None
    return None, None


def restore_state(atlas, state):
    """把存档块重放到**当前**地图上（调用方先用编译结果建好 atlas）。

    id 仍然存在的增量照常重放；地点已删除的增量被丢弃并写进 notes；
    队伍站在已删除的地点上时，按 §3.4 搬迁到最近的 authored 区域。
    返回 {atlas, locus, notes}；atlas 就地修改。
    """
    if state.get("version") != ATLAS_STATE_VERSION:
        raise ValueError("存档块版本不支持: %r" % (state.get("version"),))
    notes = []
    old_rev = state.get("setting_rev")
    new_rev = atlas.get("setting_rev")
    if old_rev and new_rev and old_rev != new_rev:
        notes.append("世界设定已更新（setting_rev 变化），按当前世界重放增量")

    for fid, fstate in state.get("frames", {}).items():
        if fid not in atlas["frames"]:
            notes.append("帧 %s 已不存在，丢弃 %d 条增量"
                         % (fid, len(fstate.get("deltas", []))))
            continue
        for delta in fstate.get("deltas", []):
            pid = delta.get("place_id")
            if delta["op"] == "add_feature":
                if find_place(atlas, pid) is not None:
                    notes.append("跳过增量 add_feature %s（地点已存在）" % pid)
                    continue
            elif find_place(atlas, pid) is None:
                notes.append("丢弃增量 %s %s（地点已不存在）"
                             % (delta["op"], pid))
                continue
            apply_delta(atlas, delta)

    locus = None
    old_locus = state.get("party_locus")
    if old_locus and find_place(atlas, old_locus.get("place_id")) is not None:
        locus = copy.deepcopy(old_locus)
    elif old_locus:
        place, _ = _relocate_party(atlas, old_locus)
        if place is not None:
            locus = _locus_at(place)
            notes.append("队伍原所在地点 %s 已不存在，搬迁至 %s（%s）"
                         % (old_locus.get("place_id"), place["id"], place["name"]))
    return {"atlas": atlas, "locus": locus, "notes": notes}


# ════════════════════════════════════════════════════════════════════════
# 特殊边与要素
# ════════════════════════════════════════════════════════════════════════

def lost_destination(atlas, locus, intended_id, rng):
    """迷路（§3.3）：在已连通的地点里，选一个不在通往 intended_id
    的最短路上的地点。调用方再扣 1 时段、风险池 +1；内核不动账。

    intended 不可达时，在可达集合里选（本来就到不了，谈不上偏航）。
    没有任何可选地点时返回 None。
    """
    cur = locus.get("place_id")
    frame_id = locus.get("frame_id")
    d_from = _graph_distances(atlas, frame_id, cur)
    d_to = _graph_distances(atlas, frame_id, intended_id, reverse=True)
    total = d_from.get(intended_id)
    candidates = []
    for pid, _d in sorted(d_from.items()):
        if pid == cur or pid == intended_id:
            continue
        if total is not None and d_from[pid] + d_to.get(pid, 10 ** 9) == total:
            continue  # 在最短路上
        candidates.append(pid)
    if not candidates:
        return None
    return rng.choice(candidates)


def reroll_unstable(atlas, frame_id, rng):
    """重摇本帧里 unstable 且两端都不是 authored 的边（§3.3）。

    锚点（authored 地点）的边保持不动。重摇 = 重新掷该边的行程档，
    边仍标 unstable，下次还能重摇。返回重摇条数（一条双边只计一次）。
    """
    frame = atlas["frames"].get(frame_id)
    if frame is None:
        return 0
    seen = set()
    count = 0
    for pid in sorted(frame["places"]):
        place = frame["places"][pid]
        for link in place["links"]:
            other = find_place(atlas, link["to"])
            if other is None:
                continue
            if place["source"] == "authored" or other["source"] == "authored":
                continue
            # 一条双边连接在两端各存一条记录（via 互为反向），归一后只计一次
            pair_via = tuple(sorted((link["via"], VIA_OPPOSITE[link["via"]])))
            key = (min(pid, link["to"]), max(pid, link["to"]), pair_via)
            if key in seen:
                continue
            seen.add(key)
            new_band = rng.choice(list(BANDS))
            link["band"] = new_band
            # 同一条边的另一端记录同步
            for back in other["links"]:
                if back["to"] == pid and back["via"] == VIA_OPPOSITE[link["via"]]:
                    back["band"] = new_band
            count += 1
    return count


def carry_across(atlas, link, feature):
    """把要素复制到连接另一端所在的帧，**不换算坐标**（§3.3）。

    只有连接带有性状 carry 时才允许——连接自身的 traits，或任一端
    地点带 carry 性状，都算数。link 是 {from, to[, via]}。
    返回副本的 feature_id。坐标原样复制，格的含义由目标帧自己解释。
    """
    src = find_place(atlas, link.get("from"))
    dst = find_place(atlas, link.get("to"))
    if src is None or dst is None:
        raise ValueError("连接端点不存在: %r" % (link,))
    stored = None
    for candidate in src["links"]:
        if candidate["to"] == link["to"] and \
                (not link.get("via") or candidate["via"] == link["via"]):
            stored = candidate
            break
    if stored is None:
        raise ValueError("找不到这条连接: %r" % (link,))
    has_carry = ("carry" in stored.get("traits", [])
                 or "carry" in src.get("traits", [])
                 or "carry" in dst.get("traits", []))
    if not has_carry:
        raise ValueError("这条连接不带 carry 性状，不能携带要素")

    # 「另一端」= 与要素当前所在帧相对的那一端；两端同帧时取 to 端
    ends = [src, dst]
    others = [p for p in ends if p["frame_id"] != feature.get("frame_id")]
    target = others[0] if others else dst
    frame_id = target["frame_id"]
    new_id = "%s@%s" % (feature["id"], frame_id)
    n = 2
    while find_place(atlas, new_id) is not None:
        new_id = "%s@%s#%d" % (feature["id"], frame_id, n)
        n += 1
    copy_place = copy.deepcopy(feature)
    copy_place["id"] = new_id
    copy_place["frame_id"] = frame_id
    copy_place["kind"] = "feature"
    copy_place["links"] = []
    copy_place["discovered"] = "unseen"
    copy_place["source"] = "generated"
    # 不换算坐标：x / y / z 原样保留
    add_place(atlas, copy_place)
    return new_id
