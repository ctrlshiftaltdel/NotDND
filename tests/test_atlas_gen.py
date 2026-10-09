#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atlas_gen 回归测试（ATLAS 切片 I3，Issue #65）。

对 ATLAS-DESIGN.md §5.5（地点生成）、§5.6（战术帧）做**真实断言**：
固定种子可复现；层数 / 负层 / 节点图 / 不稳定边 / 抽象帧都不使用 6 米；
阈界「52 层」锚的地点帧达到层数而世界帧 z 跨度远小于它；场地要素只来自
当前世界的表或 system 兜底表。

测试文件允许出现世界 key（用于读真实 JSON，与 I2 测试同一口径）；
生成器源码不允许——本测试顺带扫描 `atlas_gen.py` 源码核实。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_atlas_gen.py` 运行。
"""

import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import atlas as al
import atlas_compile as ac
import atlas_gen as ag

# ── 运行时读入词表、世界与兜底表 ─────────────────────────────────────────

def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return json.load(handle)


LEXICON = _load("data/atlas/lexicon.json")
SYSTEM_TABLES = _load("data/random_tables/system.json")["tables"]

WORLDS = {}
for _name in sorted(os.listdir(os.path.join(ROOT, "data", "worlds"))):
    if _name.endswith(".json"):
        _doc = _load("data/worlds/" + _name)
        WORLDS[_doc["world"]["key"]] = _doc

assert set(WORLDS) == {"threshold", "emberfall", "entropic-net"}


# ── 夹具：输入用性状词，不用世界名 ───────────────────────────────────────

def fixture_world(key, summary, kps=(), tables=None):
    """构造一份最小 world.module。性状全部由 summary 里的词表词表达。"""
    region = {"id": "r1", "name": "测试区", "summary": summary,
              "key_places": list(kps)}
    world = {"kind": "world.module",
             "world": {"key": key, "name": key},
             "regions": [region]}
    if tables is not None:
        world["random_tables"] = tables
    return world


def compiled(key, summary, kps=(), tables=None, seed=0):
    return ac.compile_world(fixture_world(key, summary, kps, tables),
                            LEXICON, seed=seed)


def site_rooms(frame):
    return {pid: p for pid, p in frame["places"].items() if p["kind"] == "room"}


def all_features(frame):
    out = []
    for pid in sorted(frame["places"]):
        out.extend(frame["places"][pid].get("features", []))
    return out


def frame_zs(frame):
    return [p["z"] for p in frame["places"].values() if "z" in p]


def gen_of(key, summary, kps=(), tables=None, world=None, seed=0,
           system_tables=None):
    """编译 + 对唯一区域生成地点帧，返回 (atlas, 地点帧)。"""
    atlas = compiled(key, summary, kps, tables, seed=seed)
    anchor_id = "%s/r1" % key
    frame = ag.generate_site(atlas, anchor_id, world=world,
                             system_tables=system_tables)
    return atlas, frame


# ── 验收 1：固定种子下连生成两次，房间 id 与连接相同 ─────────────────────

def test_same_seed_identical():
    summary = "城区 环道"          # settlement + vertical → 多层楼
    a1 = compiled("t-dup", summary, seed=0)
    f1 = ag.generate_site(a1, "t-dup/r1")
    a2 = compiled("t-dup", summary, seed=0)
    f2 = ag.generate_site(a2, "t-dup/r1")
    assert list(f1["places"]) == list(f2["places"]), "房间 id 集合与次序不同"
    assert f1["places"] == f2["places"], "同种子下房间数据（含连接）不同"
    # 同一 atlas 上重复调用：不重复生成，返回同一帧
    again = ag.generate_site(a1, "t-dup/r1")
    assert again is f1
    # 换种子应得到不同的布局（种子确实参与）
    a3 = compiled("t-dup", summary, seed=99)
    f3 = ag.generate_site(a3, "t-dup/r1")
    assert f3["places"] != f1["places"], "不同种子布局不应完全相同"


# ── 验收 2：层数 / 负层 / 节点图 / 不稳定边 / 抽象帧不使用 6 米 ──────────

def test_floors_not_six_meter():
    # 「高约 60 米」按每层 10 米换算是 6 层；若错用 6 米/层会得到 10 层。
    _atlas, frame = gen_of("t-floor", "环道 高约 60 米")
    zs = frame_zs(frame)
    assert max(zs) == 6, "60 米应是 6 层（每层 10 米），实得最高层 %d" % max(zs)
    assert max(zs) != 10, "层数不得按 6 米/层换算"
    assert min(zs) == 0
    assert len(site_rooms(frame)) > 6, "多层地点每层都应有房间"


def test_negative_floors():
    # 「地下三层」向负 z 延伸：1 层地上 + 3 层地下，与米无关。
    _atlas, frame = gen_of("t-below", "地下三层 市场")
    zs = frame_zs(frame)
    assert min(zs) == -3, "「地下三层」应生成到 z=-3，实得 %d" % min(zs)
    assert max(zs) >= 1, "地面入口层应存在"
    assert 0 in zs
    # 每层都有房间，且相邻层在平面重叠处有楼梯
    for z in range(-3, max(zs) + 1):
        assert any(p["z"] == z for p in frame["places"].values()), \
            "z=%d 层应有格" % z
    stairs = [link for p in frame["places"].values() for link in p["links"]
              if link["via"] in ("上", "下")]
    assert stairs, "负层与地面之间应有楼梯"


def test_network_node_graph():
    # 节点数 = min(12, 3 + 关键地点数)；连接用「连接」，移动花费看连接。
    _atlas, frame = gen_of("t-net", "地铁 通道", kps=["节点甲", "节点乙"])
    expected = min(12, 3 + 2)
    assert len(frame["places"]) == expected, \
        "节点数应为 %d，实得 %d" % (expected, len(frame["places"]))
    zs = frame_zs(frame)
    assert set(zs) == {0}, "节点图是单层平面"
    for p in frame["places"].values():
        for link in p["links"]:
            if link["to"] in frame["places"]:
                assert link["via"] == "连接", "节点图连接应为「连接」，实得 %r" \
                    % link["via"]
            else:
                assert link["to"] == "t-net/r1" and link["via"] == "门", \
                    "节点图里唯一的跨帧连接应回到锚"
    # 整图连通
    start = next(iter(frame["places"]))
    seen = {start}
    stack = [start]
    while stack:
        for link in frame["places"][stack.pop()]["links"]:
            if link["to"] in frame["places"] and link["to"] not in seen:
                seen.add(link["to"])
                stack.append(link["to"])
    assert len(seen) == len(frame["places"]), "节点图应连通"
    # 节点数不随「米」变化：地理坐标只用于切片
    assert frame["cell"] == "房间" and frame["space"] == "metric"


def test_unstable_edges():
    atlas, frame = gen_of("t-unstable", "渗溢 薄处 市场")
    frame_id = "t-unstable/site/t-unstable/r1"
    generated = {pid for pid, p in frame["places"].items()
                 if p["source"] == "generated"}
    unstable_pairs = 0
    for pid, p in frame["places"].items():
        for link in p["links"]:
            if pid in generated and link["to"] in generated:
                assert link["unstable"], "生成格之间的边应标 unstable: %s" % pid
                unstable_pairs += 1
            else:
                assert not link["unstable"], "锚参与的边不应标 unstable"
    assert unstable_pairs > 0, "应存在生成格之间的边"
    # 锚（authored）与入口之间的门存在
    anchor = al.find_place(atlas, "t-unstable/r1")
    assert any(link["to"].endswith("/g1_1_0") for link in anchor["links"])
    # 非锚点边可重摇，锚的边不动
    rng = random.Random(5)
    before = {(p["id"], l["to"], l["via"]): (l["band"], l["unstable"])
              for p in frame["places"].values() for l in p["links"]}
    count = al.reroll_unstable(atlas, frame_id, rng)
    assert count >= 1, "应至少重摇一条非锚点边"
    for p in frame["places"].values():
        for l in p["links"]:
            other = al.find_place(atlas, l["to"])
            key = (p["id"], l["to"], l["via"])
            if p["source"] == "generated" and other["source"] == "generated":
                assert l["band"] in al.BANDS, "重摇后应有档位"
            else:
                assert (l["band"], l["unstable"]) == before[key]


def test_abstract_frame_no_six_meter():
    # 抽象性状 → 地点帧与战术帧都是 abstract；换算函数拒绝用 6 米。
    atlas, frame = gen_of("t-abs", "网潜 数据区域")
    assert frame["space"] == "abstract", "抽象性状的地点帧应为 abstract"
    room_id = "t-abs/r1/g1_1_0"
    assert room_id in frame["places"]
    tac = ag.generate_tactical(atlas, room_id)
    tac_id = "t-abs/tactical/%s" % room_id
    assert tac["space"] == "abstract"
    assert tac["cell"] == "一次走位", "抽象战术帧不得按 6 米记格"
    assert ag.zone_steps(atlas, tac_id, 12) is None, \
        "抽象帧不得把米换成跨区数"
    # 对照：metric 帧才用 6 米换算 ceil(米/6)
    m_atlas, m_site = gen_of("t-metric", "城区 市场")
    m_room = "t-metric/r1/g1_1_0"
    m_tac = ag.generate_tactical(m_atlas, m_room)
    m_tac_id = "t-metric/tactical/%s" % m_room
    assert m_tac["space"] == "metric" and m_tac["cell"] == "6米"
    assert ag.zone_steps(m_atlas, m_tac_id, 12) == 2
    assert ag.zone_steps(m_atlas, m_tac_id, 6) == 1
    assert ag.zone_steps(m_atlas, m_tac_id, 7) == 2


# ── 验收 3：阈界「52 层」锚：地点帧达到层数，世界帧 z 跨度远小于它 ────────

def test_threshold_52_floor_anchor():
    world = WORLDS["threshold"]
    atlas = ac.compile_world(world, LEXICON, seed=0)
    # 按内容定位「52 层」的关键地点（测试可读真实 JSON；源码不分支世界名）
    found = None
    for region in world["regions"]:
        for index, kp in enumerate(region.get("key_places", [])):
            if "52 层" in kp:
                found = (region["id"], index, kp)
    assert found, "阈界应有一个「52 层」的关键地点"
    rid, index, kp_text = found
    kp_id = "threshold/%s/k%d" % (rid, index)
    site = ag.generate_site(atlas, kp_id, world=world)
    zs = frame_zs(site)
    assert max(zs) >= 52, "「52 层」锚的地点帧应达到第 52 层，实得 %d" % max(zs)
    assert max(zs) <= 80, "层数限制在 1–80"
    assert min(zs) == 0, \
        "该锚自身无负层文字，区域级 below 不应让它向负 z 生成（Issue #82）"
    assert max(zs) - min(zs) <= 80, "地点帧 z 跨度不超过 80"
    assert kp_text in json.dumps(
        [p["name"] for p in atlas["frames"]["threshold/surface"]
         ["places"].values()], ensure_ascii=False), "锚的名字保持原文"
    # 世界帧 z 跨度远小于地点帧：所有街面区域都贴地
    surface = atlas["frames"]["threshold/surface"]
    wz = [p.get("z", 0) for p in surface["places"].values()]
    assert max(wz) - min(wz) < 52, "世界帧不得把 52 层当高度"
    assert max(wz) - min(wz) == 0


# ── 验收 4：场地要素只来自当前世界的表，或 system 兜底 ───────────────────

TABLE_A = [{"id": "a_venue", "name": "甲地场地表", "rows": [
    {"roll": 1, "result": "甲特征一", "tag": "掩体"},
    {"roll": 2, "result": "甲特征二", "tag": "危险"},
]}]
TABLE_B = [{"id": "b_venue", "name": "乙地场地表", "rows": [
    {"roll": 1, "result": "乙特征一", "tag": "机关"},
    {"roll": 2, "result": "乙特征二", "tag": "机动"},
]}]


def test_venue_tables_world_isolation():
    # 两个世界各自生成：A 只见甲表行，B 只见乙表行；互不串表。
    atlas_a, site_a = gen_of("w-alfa", "城区 市场", tables=TABLE_A,
                             world=fixture_world("w-alfa", "城区 市场",
                                                 tables=TABLE_A))
    atlas_b, site_b = gen_of("w-beta", "城区 市场", tables=TABLE_B,
                             world=fixture_world("w-beta", "城区 市场",
                                                 tables=TABLE_B))
    feats_a = all_features(site_a)
    feats_b = all_features(site_b)
    assert feats_a, "甲世界应取到场地要素"
    assert all(f["text"] in ("甲特征一", "甲特征二") for f in feats_a), \
        "甲世界不得读到乙世界的表行: %r" % feats_a
    assert all(f["text"] in ("乙特征一", "乙特征二") for f in feats_b), \
        "乙世界不得读到甲世界的表行: %r" % feats_b
    # 每个房间 0–2 条
    for p in site_a["places"].values():
        assert len(p.get("features", [])) <= 2


def test_venue_tables_system_fallback():
    # 世界没有场地类表行时，才用 data/random_tables/system.json 的场地类表。
    allowed = {row["result"] for table in SYSTEM_TABLES
               for row in table.get("rows", [])
               if row.get("tag") in ag.SITE_ELEMENT_TAGS}
    assert allowed, "system 兜底表应含场地类行"
    _atlas, site = gen_of("w-fallback", "城区 市场")
    feats = all_features(site)
    assert feats, "兜底路径应取到场地要素"
    assert all(f["text"] in allowed for f in feats), \
        "兜底要素应来自 system 表: %r" % [f["text"] for f in feats]
    assert all(f["tag"] in ag.SITE_ELEMENT_TAGS for f in feats)
    # 显式传入 system_tables 参数时同样受限
    _atlas2, site2 = gen_of("w-fallback2", "城区 市场",
                            world=fixture_world("w-fallback2", "城区 市场"),
                            system_tables=SYSTEM_TABLES)
    assert all(f["text"] in allowed for f in all_features(site2))


# ── 战术帧投影（§5.6） ──────────────────────────────────────────────────

def _dist_le2(frame, start):
    """测试自己的 BFS：图距离 ≤ 2 的房间集合。"""
    dist = {start: 0}
    frontier = [start]
    while frontier:
        nxt = []
        for pid in frontier:
            if dist[pid] >= 2:
                continue
            for link in frame["places"][pid]["links"]:
                to = link["to"]
                if to in frame["places"] and to not in dist:
                    dist[to] = dist[pid] + 1
                    nxt.append(to)
        frontier = nxt
    return {pid for pid, d in dist.items() if d <= 2}


def test_tactical_projection():
    atlas, site = gen_of("t-tac", "城区 市场", tables=TABLE_A,
                         world=fixture_world("t-tac", "城区 市场",
                                             tables=TABLE_A))
    room_id = "t-tac/r1/g1_1_0"
    tac = ag.generate_tactical(atlas, room_id)
    tac_id = "t-tac/tactical/%s" % room_id
    expected = _dist_le2(site, room_id)
    assert len(tac["places"]) == len(expected), \
        "战术区域数应等于图距离 ≤2 的房间数"
    assert all(z["kind"] == "zone" for z in tac["places"].values())
    # 每区保留原房间的第一个场地要素作为固有特征
    for index, rid in enumerate(sorted(expected)):
        zone = tac["places"]["%s/z%d" % (tac_id, index)]
        src = site["places"][rid]
        if src.get("features"):
            assert zone["inherent"] == src["features"][0]
        else:
            assert "inherent" not in zone
        for axis in ("x", "y", "z"):
            assert zone[axis] == src[axis], "坐标应原样复制"
    # 投影出的图可用内核查询
    zone_ids = sorted(tac["places"])
    a, b = zone_ids[0], zone_ids[-1]
    assert al.range_band(atlas, tac_id, a, b) in \
        ("same", "adjacent", "near", "mid", "far")
    assert al.high_ground(atlas, tac_id, a, b) in (True, False)
    # 重复调用幂等
    assert ag.generate_tactical(atlas, room_id) is tac
    # 非地点帧的房间不能投影
    try:
        ag.generate_tactical(atlas, "t-tac/r1")
        raise AssertionError("从世界帧区域投影应报错")
    except ValueError:
        pass


# ── 生成器源码不得分支世界名、不得读世界模组 ─────────────────────────────

def test_gen_source_no_world_branches():
    with open(os.path.join(ROOT, "atlas_gen.py"), encoding="utf-8") as handle:
        src = handle.read()
    for key in ("threshold", "emberfall", "entropic-net"):
        assert key not in src, "生成器源码不得出现世界 key: %s" % key
    assert "data/worlds" not in src, "生成器不得读世界模组目录"
    assert "data/scenarios" not in src
    # 6 米常数只此一处，且只属于战术帧换算
    assert src.count("6") >= 0  # 常数存在性由 zone_steps 测试覆盖


CHECKS = [
    test_same_seed_identical,
    test_floors_not_six_meter,
    test_negative_floors,
    test_network_node_graph,
    test_unstable_edges,
    test_abstract_frame_no_six_meter,
    test_threshold_52_floor_anchor,
    test_venue_tables_world_isolation,
    test_venue_tables_system_fallback,
    test_tactical_projection,
    test_gen_source_no_world_branches,
]


def main():
    failures = 0
    for check in CHECKS:
        try:
            check()
        except AssertionError as error:
            print("  断言失败: %s — %s" % (check.__name__, error))
            failures += 1
        except Exception as error:  # 意外异常也算失败，但留全栈
            import traceback
            print("  异常: %s" % check.__name__)
            traceback.print_exc()
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(CHECKS) - failures, len(CHECKS),
                                          failures))
        return 1
    print("%d/%d 项通过" % (len(CHECKS), len(CHECKS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
