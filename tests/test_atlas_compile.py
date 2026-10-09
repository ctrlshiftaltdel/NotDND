#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atlas_compile 回归测试（ATLAS 切片 I2，Issue #64）。

对 ATLAS-DESIGN.md §6 三条世界性质与 §6.4 虚构世界增删做**真实断言**：
坐标不写死，断言的是能从当前 JSON 短字段推出的性质。测试文件允许
出现三个世界的 key（用于读真实 JSON）；编译器源码不允许——本测试
顺带扫描 `atlas_compile.py` 源码核实这一点。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_atlas_compile.py` 运行。
"""

import copy
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import atlas as al
import atlas_compile as ac

# ── 运行时读入词表与世界（key 来自 JSON，不写死在源码里的部分用内容定位） ──
def _load(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
        return json.load(handle)


LEXICON = _load("data/atlas/lexicon.json")
WORLDS = {}
for _name in sorted(os.listdir(os.path.join(ROOT, "data", "worlds"))):
    if _name.endswith(".json"):
        _doc = _load("data/worlds/" + _name)
        WORLDS[_doc["world"]["key"]] = _doc

W_TH = "threshold"
W_EF = "emberfall"
W_EN = "entropic-net"

assert set(WORLDS) == {W_TH, W_EF, W_EN}, "世界清单与预期不符: %s" % sorted(WORLDS)


def compile_of(key, seed=0):
    return ac.compile_world(WORLDS[key], LEXICON, seed=seed)


def region_by_text(key, needle):
    """按短文本内容定位区域（不把区域 id 写死进测试源码）。"""
    for region in WORLDS[key]["regions"]:
        text = "".join((region.get("name") or "", region.get("summary") or "",
                        region.get("atmosphere") or ""))
        if needle in text:
            return region
    raise AssertionError("世界 %s 找不到含 %r 的区域" % (key, needle))


def region_with_kp(key, needle):
    """按关键地点文本定位（区域, 下标, 原文）。"""
    for region in WORLDS[key]["regions"]:
        for index, kp in enumerate(region.get("key_places", [])):
            if needle in kp:
                return region, index, kp
    raise AssertionError("世界 %s 找不到含 %r 的关键地点" % (key, needle))


def place_of(atlas, region):
    return al.find_place(atlas, "%s/%s" % (atlas["world_key"], region["id"]))


def frame_id_of(atlas, suffix):
    return "%s/%s" % (atlas["world_key"], suffix)


def info_of(atlas, region):
    return atlas["region_info"]["%s/%s"
                                % (atlas["world_key"], region["id"])]


def touching(cells_a, cells_b):
    for (x, y) in cells_a:
        if ((x + 1, y) in cells_b or (x - 1, y) in cells_b
                or (x, y + 1) in cells_b or (x, y - 1) in cells_b):
            return True
    return False


def footprint_cells(place):
    edge_w = max(1, int(place.get("footprint", {}).get("w", 1)))
    edge_h = max(1, int(place.get("footprint", {}).get("h", 1)))
    return {(place["x"] + i, place["y"] + j)
            for i in range(edge_w) for j in range(edge_h)}


def ground_links(atlas):
    """世界帧里区域 → 区域的地面连接（两端都是区域、平面方向）。"""
    result = []
    for place in atlas["frames"][frame_id_of(atlas, "surface")]["places"].values():
        if place["kind"] != "region":
            continue
        for link in place["links"]:
            dest = al.find_place(atlas, link["to"])
            if dest is not None and dest["kind"] == "region" \
                    and link["via"] in al.VIA_CARDINAL:
                result.append((place, link, dest))
    return result


def links_into_frame(atlas, place, target_frame):
    return [link for link in place["links"]
            if al.find_place(atlas, link["to"]) is not None
            and al.find_place(atlas, link["to"])["frame_id"] == target_frame]


# ── §4.2 / 验收：词表文件本身 ────────────────────────────────────────────

def test_lexicon_shape_and_no_source():
    assert LEXICON["kind"] == "atlas.lexicon"
    with open(os.path.join(ROOT, "data", "atlas", "lexicon.json"),
              encoding="utf-8") as handle:
        raw = handle.read()
    assert '"source"' not in raw, "词表不得出现 source 键"
    expected = {"settlement", "wilderness", "span_country", "vertical",
                "below", "network", "water", "barrier", "unlisted",
                "abstract", "unstable", "carry", "shortcut"}
    seen = set()
    for entry in LEXICON["entries"]:
        assert entry["trait"] in expected
        seen.add(entry["trait"])
        assert entry["terms"] and all(term for term in entry["terms"])
    assert seen == expected


# ── §6.1 阈界都市 ────────────────────────────────────────────────────────

def test_threshold_settlement_and_bands():
    th = compile_of(W_TH)
    ef = compile_of(W_EF)
    assert th["scale"] == "settlement"
    assert ef["scale"] == "march"
    th_ground = [link["band"] for _, link, _ in ground_links(th)]
    ef_ground = [link["band"] for _, link, _ in ground_links(ef)]
    assert th_ground and "short" in th_ground, "阈界跨区档位应为短程"
    assert ef_ground and all(band in ("medium", "dangerous")
                             for band in ef_ground), "余烬邻格不是短程"


def test_threshold_52_floors_site_not_world():
    th = compile_of(W_TH)
    region, index, _kp = region_with_kp(W_TH, "52 层")
    spec = info_of(th, region)["kp_specs"][index]["spec"]
    assert spec["z_hi"] >= 52, "地点帧 z 跨度至少覆盖 52"
    assert spec["z_hi"] <= 80, "层数限制在 1–80"
    assert spec["z_lo"] == 0, \
        "区域级 below 不应镜像进关键地点规格（52 层的楼没有 52 层地下）"
    # 世界帧不用这 52 层当高度：所有街面区域 z 都是 0
    for place in th["frames"][frame_id_of(th, "surface")]["places"].values():
        if place["kind"] == "region" and "z" in place:
            assert place["z"] == 0


def test_threshold_negative_floors():
    th = compile_of(W_TH)
    region, index, _kp = region_with_kp(W_TH, "地下三层")
    spec = info_of(th, region)["kp_specs"][index]["spec"]
    assert spec["z_lo"] <= -3, "「地下三层」的地点向负 z 延伸"


def test_region_below_not_inherited_by_kp():
    """区域级 below 不镜像进关键地点规格；自身命中 below 的关键地点仍镜像。

    区域短文里的「地下」不该让区域内每栋楼都长出地下室（Issue #82）；
    但区域自己的 site 规格保留 below 偏向（现有行为不变）。
    """
    world = {
        "kind": "world.module",
        "world": {"key": "t-below-split", "name": "测试世界"},
        "regions": [{
            "id": "r-below", "name": "地下街区",
            "summary": "地下 市场环道。",   # 区域自身命中 below（+ vertical）
            "key_places": ["空中连廊", "地下市场"],
        }],
    }
    atlas = ac.compile_world(world, LEXICON, seed=0)
    info = atlas["region_info"]["t-below-split/r-below"]
    # 区域自己的 site 规格：below 偏向保留，仍向负 z 镜像
    assert info["site"]["z_lo"] < 0, "区域级 below 仍作用于区域 site 规格"
    # 关键地点 0 自身不命中 below、无负层数文字 → 不向负 z 镜像
    spec0 = info["kp_specs"][0]["spec"]
    assert spec0["z_lo"] == 0, \
        "区域级 below 不应镜像进自身无 below 的关键地点规格"
    assert spec0["z_hi"] >= 1, "关键地点仍保留其余性状带来的层数"
    # 关键地点 1 自身命中 below 且无数字 → 仍向负 z 镜像
    spec1 = info["kp_specs"][1]["spec"]
    assert spec1["z_lo"] < 0, \
        "自身命中 below 且无数字的关键地点仍应向负 z 镜像"


def test_threshold_seam():
    th = compile_of(W_TH)
    glossary = "".join(g["term"] + g["meaning"]
                       for g in WORLDS[W_TH].get("glossary", []))
    for term in ("薄处", "渗溢", "夹层"):
        assert term in glossary, "术语表应有 %s" % term
    seam = frame_id_of(th, "seam")
    assert seam in th["frames"], "应有 seam 帧"
    assert th["frames"][seam]["space"] == "abstract"
    # 区域短文里写到薄处或渗溢的，有进入 seam 的 unstable 连接
    hits = 0
    for region in WORLDS[W_TH]["regions"]:
        text = "".join((region.get("name") or "", region.get("summary") or "",
                        region.get("atmosphere") or ""))
        if not ("薄处" in text or "渗溢" in text):
            continue
        hits += 1
        place = place_of(th, region)
        entering = links_into_frame(th, place, seam)
        assert entering, "区域 %s 应有进入夹隙的连接" % region["id"]
        assert all(link["unstable"] for link in entering)
    assert hits >= 3, "阈界至少三个区域写到薄处，实得 %d" % hits
    # 夹隙中非锚点的边可以重摇；锚点的边不动
    rng = random.Random(7)
    before = {}
    for place in th["frames"][seam]["places"].values():
        for link in place["links"]:
            before[(place["id"], link["to"], link["via"])] = link["band"]
    count = al.reroll_unstable(th, seam, rng)
    assert count >= 1, "夹隙内应有可重摇的非锚点边"
    for place in th["frames"][seam]["places"].values():
        for link in place["links"]:
            other = al.find_place(th, link["to"])
            key = (place["id"], link["to"], link["via"])
            if place["source"] == "authored" or other["source"] == "authored":
                assert link["band"] == before[key], "锚点参与的边不应被重摇"
            else:
                assert link["band"] in al.BANDS, "非锚点边应被重摇出档位"


def test_threshold_unstable_key_place():
    th = compile_of(W_TH)
    region, index, kp_text = region_with_kp(W_TH, "每次测量不同")
    kp_place = al.find_place(th, "%s/%s/k%d"
                             % (th["world_key"], region["id"], index))
    assert kp_place is not None and kp_place["name"] == kp_text
    assert "unstable" in kp_place["traits"], "该关键地点的边可以标 unstable"


# ── §6.2 余烬纪元 ────────────────────────────────────────────────────────

def test_emberfall_around_bole():
    ef = compile_of(W_EF)
    around = region_by_text(W_EF, "围绕巨干")
    target = region_by_text(W_EF, "直径两公里")
    assert around["id"] != target["id"]
    pa, pb = place_of(ef, around), place_of(ef, target)
    assert not (footprint_cells(pa) & footprint_cells(pb)), "不与巨干重叠"
    assert touching(footprint_cells(pa), footprint_cells(pb)), "落在巨干外围相邻"


def test_emberfall_directions():
    ef = compile_of(W_EF)
    nw = place_of(ef, region_by_text(W_EF, "大陆西北"))
    south = place_of(ef, region_by_text(W_EF, "南方群岛"))
    north = place_of(ef, region_by_text(W_EF, "北方的极地"))
    assert nw["x"] < 0 and nw["y"] > 0, "西北象限"
    assert south["y"] < 0, "南方"
    assert north["y"] > 0, "北方"


def test_emberfall_bole_footprint_and_height():
    ef = compile_of(W_EF)
    bole = region_by_text(W_EF, "直径两公里")
    info = info_of(ef, bole)
    assert info["edge"] == 1, "两公里直径在旷野常数下接近 1 格"
    assert info["site"]["z_hi"] == 80, "高约八百米 → 地点帧 80 层"
    assert info["site"]["floors"] <= 80
    place = place_of(ef, bole)
    assert place["z"] == 0, "八百米不进世界帧的 z"


def test_emberfall_span_country():
    ef = compile_of(W_EF)
    big = info_of(ef, region_by_text(W_EF, "相当于一个小国"))
    plain = info_of(ef, region_by_text(W_EF, "把问题留着"))     # 半影：无跨度词
    military = info_of(ef, region_by_text(W_EF, "军事王国"))    # 无跨度词
    assert big["edge"] > plain["edge"]
    assert big["edge"] > military["edge"]


def test_emberfall_shortcut_network():
    ef = compile_of(W_EF)
    surface = ef["frames"][frame_id_of(ef, "surface")]
    under = [p for p in surface["places"].values()
             if p["source"] == "generated" and p.get("z") == -1]
    assert len(under) >= 2, "z = −1 应有路网节点"
    node_ids = {p["id"] for p in under}
    short_hops = []
    for place in under:
        for link in place["links"]:
            if link["to"] in node_ids:
                short_hops.append(link["band"])
    assert short_hops and all(band == "short" for band in short_hops), \
        "z = −1 路网连接为短程"
    ef_ground = [link["band"] for _, link, _ in ground_links(ef)]
    assert "medium" in ef_ground, "地面邻格仍是中程，路网短于地面"
    # 「地下林」区域接在这条路网上（或出现在负 z 的地点帧规格里）
    forest = place_of(ef, region_by_text(W_EF, "地下林"))
    down_links = [link for link in forest["links"]
                  if link["via"] == "下"
                  and al.find_place(ef, link["to"]) is not None
                  and al.find_place(ef, link["to"]).get("z") == -1]
    spec = info_of(ef, region_by_text(W_EF, "地下林"))["site"]
    assert down_links or spec["z_lo"] < 0, "地下林出现在负 z，或接在路网上"


# ── §6.3 熵网 ────────────────────────────────────────────────────────────

def test_entropic_frames():
    en = compile_of(W_EN)
    surface = en["frames"][frame_id_of(en, "surface")]
    abstract = en["frames"][frame_id_of(en, "abstract")]
    assert surface["space"] == "metric"
    assert abstract["space"] == "abstract"
    assert abstract["cell"] == "一次走位"


def test_entropic_radius_footprint():
    en = compile_of(W_EN)
    crater = info_of(en, region_by_text(W_EN, "半径 12 公里"))
    # 同图里没有公里数的城区（聚落档）
    docks = info_of(en, region_by_text(W_EN, "自动化港口"))
    assert crater["footprint_km"] > docks["footprint_km"], \
        "12 公里半径的区域脚印大于没有公里数的城区（%s vs %s 公里）" \
        % (crater["footprint_km"], docks["footprint_km"])


def test_entropic_unlisted():
    en = compile_of(W_EN)
    place = place_of(en, region_by_text(W_EN, "地图上没有"))
    assert "x" not in place and "y" not in place, "没有街面坐标"


def test_entropic_abstract_and_carry():
    en = compile_of(W_EN)
    abstract_id = frame_id_of(en, "abstract")
    dive = place_of(en, region_by_text(W_EN, "不存在的物理空间"))
    assert dive["frame_id"] == abstract_id, "抽象帧"
    # 找一条 抽象帧 ↔ 街面 的连接
    carry_links = []
    for link in dive["links"]:
        dest = al.find_place(en, link["to"])
        if dest is not None and dest["frame_id"] != abstract_id:
            carry_links.append((link, dest))
    assert carry_links, "抽象帧应有连接通向街面"
    assert all("carry" in (link.get("traits") or [])
               for link, _ in carry_links), "世界级 carry → 连接带 carry"
    link, dest = carry_links[0]
    feature = {"id": "test-feature", "name": "试件", "kind": "feature",
               "frame_id": abstract_id}
    new_id = al.carry_across(en, {"from": dive["id"], "to": dest["id"]}, feature)
    copy_place = al.find_place(en, new_id)
    assert copy_place is not None and copy_place["frame_id"] == dest["frame_id"]
    # 抽象帧上 range_band 仍可用（图距离投影与坐标米数无关）
    kp_id = dive["id"] + "/k0"
    assert al.find_place(en, kp_id) is not None
    assert al.range_band(en, abstract_id, dive["id"], kp_id) == "adjacent"


# ── 确定性（验收：同一输入连编两次） ─────────────────────────────────────

def _snapshot(atlas):
    snap = {}
    for fid, frame in atlas["frames"].items():
        snap[fid] = {}
        for pid, place in frame["places"].items():
            snap[fid][pid] = {
                "anchor": tuple(place[k] for k in ("x", "y", "z")
                                if k in place),
                "links": sorted((l["to"], l["via"], l["band"],
                                 bool(l["unstable"]))
                                for l in place["links"]),
            }
    return snap


def test_same_seed_identical():
    for key in sorted(WORLDS):
        first = _snapshot(compile_of(key, seed=42))
        second = _snapshot(compile_of(key, seed=42))
        assert first == second, "世界 %s 同种子连编两次结果不同" % key


def test_seed_changes_only_unconstrained():
    """种子可以改变无约束区域的相对次序，但方位与围绕关系不能变（§5.4）。"""
    for seed in (1, 2, 3, 99):
        ef = compile_of(W_EF, seed=seed)
        around = place_of(ef, region_by_text(W_EF, "围绕巨干"))
        target = place_of(ef, region_by_text(W_EF, "直径两公里"))
        cells_a, cells_b = footprint_cells(around), footprint_cells(target)
        assert not (cells_a & cells_b) and touching(cells_a, cells_b), \
            "种子 %d：灰原仍应围绕巨干" % seed
        nw = place_of(ef, region_by_text(W_EF, "大陆西北"))
        assert nw["x"] < 0 and nw["y"] > 0, "种子 %d：西北不变" % seed
        south = place_of(ef, region_by_text(W_EF, "南方群岛"))
        assert south["y"] < 0, "种子 %d：南方不变" % seed
        north = place_of(ef, region_by_text(W_EF, "北方的极地"))
        assert north["y"] > 0, "种子 %d：北方不变" % seed


# ── §6.4 虚构世界：增删与 setting_rev ────────────────────────────────────

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "atlas_fiction_world.json")


def test_fixture_compiles_without_key_branches():
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    atlas = ac.compile_world(fixture, LEXICON, seed=5)
    assert atlas["world_key"] == fixture["world"]["key"]
    core = fixture["world"]["key"] + "/r-core"
    assert al.find_place(atlas, core) is not None
    # 世界级 abstract（词表「不存在的物理」）→ 另开 abstract 帧
    assert fixture["world"]["key"] + "/abstract" in atlas["frames"]
    # world_rules 命中 shortcut → z = −1 路网
    under = [p for p in
             atlas["frames"]["%s/surface" % fixture["world"]["key"]]
             ["places"].values()
             if p.get("z") == -1]
    assert len(under) >= 2


def test_fixture_remove_region():
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    key = fixture["world"]["key"]
    dead = key + "/r-deep"
    first = ac.compile_world(fixture, LEXICON, seed=5)
    # 在将被删除的区域上留一条增量，队伍也站在那里
    al.apply_delta(first, {"op": "set_trait", "place_id": dead,
                           "payload": {"trait": "unstable"}})
    locus = {"frame_id": key + "/surface", "place_id": dead,
             "x": 0, "y": 0, "z": 0}
    state = al.export_state(first, locus)

    trimmed = copy.deepcopy(fixture)
    trimmed["regions"] = [r for r in trimmed["regions"] if r["id"] != "r-deep"]
    second = ac.compile_world(trimmed, LEXICON, seed=5)
    assert al.find_place(second, dead) is None, "旧 id 消失"
    assert first["setting_rev"] != second["setting_rev"], "设定变更 → rev 变"

    result = al.restore_state(second, state)
    assert al.find_place(second, dead) is None
    assert any("丢弃" in note and "r-deep" in note
               for note in result["notes"]), result["notes"]
    assert any("搬迁" in note for note in result["notes"]), \
        "队伍原地点已删 → 搬迁说明"
    assert result["locus"]["place_id"] != dead


def test_fixture_add_region():
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    key = fixture["world"]["key"]
    grown = copy.deepcopy(fixture)
    grown["regions"].append({
        "id": "r-new", "name": "新区",
        "summary": "南方的群岛市场，水上贸易。",
        "key_places": ["新码头", "新市场", "新灯塔"],
    })
    atlas = ac.compile_world(grown, LEXICON, seed=5)
    assert al.find_place(atlas, key + "/r-new") is not None, "新区域出现"


def test_scan_range_excludes_forbidden_fields():
    """history 等未扫描字段不影响编译结果（§4.3）。"""
    with open(FIXTURE, encoding="utf-8") as handle:
        fixture = json.load(handle)
    poisoned = copy.deepcopy(fixture)
    poisoned["history"] = [{"id": "h-x", "title": "污染",
                            "text": "荒野 山脉 废墟 极地"}]
    poisoned["factions"] = [{"id": "f-x", "name": "污染势力",
                             "summary": "地图上没有 导航到不了"}]
    clean = ac.compile_world(fixture, LEXICON, seed=5)
    dirty = ac.compile_world(poisoned, LEXICON, seed=5)
    assert clean["setting_rev"] == dirty["setting_rev"], \
        "未扫描字段不进 setting_rev"
    assert clean["world_traits"] == dirty["world_traits"]
    for rid, info in clean["region_info"].items():
        assert info["traits"] == dirty["region_info"][rid]["traits"]


def test_setting_rev_stable():
    a = compile_of(W_TH, seed=1)
    b = compile_of(W_TH, seed=2)
    assert a["setting_rev"] and a["setting_rev"] == b["setting_rev"]
    assert len(a["setting_rev"]) == 16
    assert a["setting_rev"] != compile_of(W_EF)["setting_rev"]


def test_compiler_source_has_no_world_keys():
    with open(os.path.join(ROOT, "atlas_compile.py"), encoding="utf-8") as handle:
        source = handle.read()
    for key in WORLDS:
        assert key not in source, "编译器源码不得包含世界 key: %s" % key


CHECKS = [
    test_lexicon_shape_and_no_source,
    test_threshold_settlement_and_bands,
    test_threshold_52_floors_site_not_world,
    test_threshold_negative_floors,
    test_region_below_not_inherited_by_kp,
    test_threshold_seam,
    test_threshold_unstable_key_place,
    test_emberfall_around_bole,
    test_emberfall_directions,
    test_emberfall_bole_footprint_and_height,
    test_emberfall_span_country,
    test_emberfall_shortcut_network,
    test_entropic_frames,
    test_entropic_radius_footprint,
    test_entropic_unlisted,
    test_entropic_abstract_and_carry,
    test_same_seed_identical,
    test_seed_changes_only_unconstrained,
    test_fixture_compiles_without_key_branches,
    test_fixture_remove_region,
    test_fixture_add_region,
    test_scan_range_excludes_forbidden_fields,
    test_setting_rev_stable,
    test_compiler_source_has_no_world_keys,
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
        print("%d/%d 项通过，%d 项失败" % (len(CHECKS) - failures, len(CHECKS), failures))
        return 1
    print("%d/%d 项通过" % (len(CHECKS), len(CHECKS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
