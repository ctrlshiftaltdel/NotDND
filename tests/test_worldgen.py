#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""worldgen 内核（M12-W1/W2，Issue #379 / #382）回归测试。

覆盖 Issue #379 验收清单：

- `normalize_spec → choose_base → synthesize → validate_world` 全链路：
  三类世界各一条 + 自定义 spec 一条，全部零 error；
- 同 `(spec, seed)` 两次合成逐字节相等；换 seed 区域布局不同；
- `port=None` 离线合成成功且校验通过（离线是正常路径）；
- goods 按世界特性生成：三个官方世界类型的 goods 清单互不相同、保底五类
  覆盖；fantasy 世界混入 future 档 goods → error（时代违和）；
- 缺保底类目 → error；
- `WORLDGEN_CALL_BUDGET = 24` 就位；超预算走兜底文本不报错（计数器断言）；
- 三个官方世界 + `tests/fixtures/atlas_fiction_world.json` 对 validate_world
  零 error（warn 允许）——W2 回归锚；
- `worldgen` 只 import 标准库（copy/hashlib/random）、零 IO 纯函数。

覆盖 Issue #382（M12-W2）验收清单：

- `validate_atlas` C1–C7 每条专测：构造破损输入 → 断言对应 error/warn；
- 回归锚扩展到 C 系列：三官方世界 + fixture 编译、全部 site 帧生成、
  战术帧投影之后 validate_atlas 零 error（warn 允许）；
- `repair` 三类（C2 断链 / C3 缺门 / C4 不可达关键地点）各有单测：
  构造破损 → repair → 全量校验零 error，且修复只新增边（边集单调增）；
  不可修类（C1 等）不误修、如实保留；
- provenance 断言：修复过的连接带 `source: worldgen:repair`，可识别来源；
- `atlas*.py` 三文件零改动（V8）：worldgen 只消费输出形状，不 import atlas。
"""

import copy
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import worldgen  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OFFICIAL_WORLDS = ["threshold", "emberfall", "entropic-net"]
WORLD_TYPE_TO_KEY = {"fantasy": "emberfall", "modern": "threshold", "future": "entropic-net"}


def _load_world(key):
    with open(os.path.join(ROOT, "data", "worlds", key + ".json"), encoding="utf-8") as fh:
        return json.load(fh)


def _load_fixture():
    path = os.path.join(ROOT, "tests", "fixtures", "atlas_fiction_world.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_lexicon():
    path = os.path.join(ROOT, "data", "atlas", "lexicon.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _load_system_tables():
    path = os.path.join(ROOT, "data", "random_tables", "system.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["tables"]


def _templates():
    return {key: _load_world(key) for key in OFFICIAL_WORLDS}


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _error_codes(issues):
    return [i["code"] for i in issues if i.get("level") == "error"]


# ── 1. 全链路：三类世界 + 自定义 spec，零 error ──────────────────────────

def test_full_chain_three_types():
    templates = _templates()
    for wt in ("fantasy", "modern", "future"):
        spec = worldgen.normalize_spec({
            "world_type": wt, "engine": "tactics", "scale": "region",
            "tone_terms": ["港口", "地下", "群岛"] if wt != "fantasy" else ["荒野", "地下"],
        })
        base = worldgen.choose_base(spec, templates=templates)
        assert base["template_key"] == WORLD_TYPE_TO_KEY[wt], base
        world = worldgen.synthesize(spec, base, seed=1001)
        assert world["kind"] == "world.module"
        assert world["world"]["world_type"] == wt
        issues = worldgen.validate_world(world, spec=spec)
        assert not worldgen.has_errors(issues), (wt, issues)


def test_full_chain_custom_spec():
    templates = _templates()
    spec = worldgen.normalize_spec({
        "world_type": "fantasy", "engine": "build", "scale": "domain",
        "tone_terms": ["山脉", "地下"],
        "regions_req": {"count": 6, "key_places_per_region": 3},
        "factions_req": {"count": 3},
        "laws_req": [{"name": "禁火令", "forbid_tags": ["火焰"]},
                     {"name": "通行税", "forbid_tags": [], "threshold": "tolerate"}],
        "economy_on": True,
        "npcs_req": {"named": 20},
    })
    base = worldgen.choose_base(spec, templates=templates)
    world = worldgen.synthesize(spec, base, seed=42)
    issues = worldgen.validate_world(world, spec=spec)
    assert not worldgen.has_errors(issues), issues
    assert len(world["regions"]) == 6
    assert len(world["factions"]) == 3
    assert len(world["laws"]) == 2
    assert world["laws"][0]["threshold"] == "forbid"
    assert world["laws"][1]["threshold"] == "tolerate"
    assert worldgen.roster(spec, 42)  # 名册可产出
    est = worldgen.estimate(spec)
    assert est["regions"] == 6 and est["npcs"] == 20 and est["economy_on"]


# ── 2. 确定性：同 (spec, seed) 逐字节相等；换 seed 不同 ──────────────────

def test_determinism_same_seed():
    templates = _templates()
    spec = worldgen.normalize_spec({"world_type": "modern", "scale": "region"})
    base = worldgen.choose_base(spec, templates=templates)
    a = worldgen.synthesize(spec, base, seed=7)
    b = worldgen.synthesize(spec, base, seed=7)
    assert _dumps(a) == _dumps(b), "同 (spec, seed) 两次合成应逐字节相等"


def test_determinism_diff_seed():
    # 不给模板：区域名走兜底短语池（种子驱动），换 seed 应布局不同。
    spec = worldgen.normalize_spec({"world_type": "fantasy", "scale": "domain"})
    base = worldgen.choose_base(spec)
    a = worldgen.synthesize(spec, base, seed=1)
    b = worldgen.synthesize(spec, base, seed=2)
    assert _dumps(a) != _dumps(b), "不同 seed 应产生不同布局"
    names_a = [r["name"] for r in a["regions"]]
    names_b = [r["name"] for r in b["regions"]]
    assert names_a != names_b


def test_roster_estimate_deterministic():
    spec = worldgen.normalize_spec({"world_type": "future", "scale": "region"})
    a = worldgen.roster(spec, 99)
    b = worldgen.roster(spec, 99)
    assert _dumps(a) == _dumps(b)
    assert len(a) == spec["npcs_req"]["named"]
    assert a[0].get("secret"), "secrets_required=True 时首个 NPC 应带秘密"
    assert worldgen.estimate(spec)["duration"] == "一周–一月（游戏时间）"


# ── 3. 离线全通：port=None 零模型调用 ─────────────────────────────────────

def test_offline_port_none():
    templates = _templates()
    spec = worldgen.normalize_spec({"world_type": "modern", "scale": "region"})
    base = worldgen.choose_base(spec, templates=templates)
    world = worldgen.synthesize(spec, base, seed=5, port=None)
    issues = worldgen.validate_world(world, spec=spec)
    assert not worldgen.has_errors(issues), issues
    prov = world["worldgen_provenance"]
    assert prov["budget"]["calls"] == 0, "离线路径不应产生模型调用"
    assert set(prov["texts"].values()) <= {"template", "builtin"}, prov["texts"]
    # 兜底短语表可独立走通（不给模板）。
    bare_base = worldgen.choose_base(spec)
    assert bare_base["template"] is None
    bare = worldgen.synthesize(spec, bare_base, seed=5)
    assert not worldgen.has_errors(worldgen.validate_world(bare, spec=spec))
    assert all(r["summary"] for r in bare["regions"])


def test_port_with_budget():
    templates = _templates()
    spec = worldgen.normalize_spec({"world_type": "modern", "scale": "region"})

    def suggest(sp, field):
        return "端口文本·%s" % field

    budget = worldgen.new_session()
    base = worldgen.choose_base(spec, templates=templates)
    world = worldgen.synthesize(spec, base, seed=5, port={"suggest": suggest}, budget=budget)
    assert not worldgen.has_errors(worldgen.validate_world(world, spec=spec))
    assert budget["calls"] <= worldgen.WORLDGEN_CALL_BUDGET
    assert budget["est_tokens"] == budget["calls"] * worldgen.WORLDGEN_TOKENS_PER_CALL
    assert any(v == "port" for v in world["worldgen_provenance"]["texts"].values())
    # 同端口同预算重跑仍确定（budget 新鲜）。
    budget2 = worldgen.new_session()
    world2 = worldgen.synthesize(spec, base, seed=5, port={"suggest": suggest}, budget=budget2)
    assert _dumps(world) == _dumps(world2)


# ── 4. 预算 24：常量就位；超预算走兜底不报错 ──────────────────────────────

def test_budget_constant_and_degradation():
    assert worldgen.WORLDGEN_CALL_BUDGET == 24
    templates = _templates()
    spec = worldgen.normalize_spec({"world_type": "future", "scale": "domain"})

    def suggest(sp, field):
        return "端口文本"

    # 计数器已打满：端口视为不可用，走模板/兜底，不报错。
    budget = {"calls": worldgen.WORLDGEN_CALL_BUDGET, "est_tokens": 99999}
    base = worldgen.choose_base(spec, templates=templates)
    world = worldgen.synthesize(spec, base, seed=11, port={"suggest": suggest}, budget=budget)
    assert budget["calls"] == worldgen.WORLDGEN_CALL_BUDGET, "超预算后不应再发起调用"
    assert not worldgen.has_errors(worldgen.validate_world(world, spec=spec))
    assert world["worldgen_provenance"]["degraded"] is True
    assert all(v in ("template", "builtin")
               for v in world["worldgen_provenance"]["texts"].values())


# ── 5. goods：世界特性驱动 + 保底覆盖 + 时代违和 ──────────────────────────

def _goods(world):
    return world.get("goods") or []


def test_goods_per_world_type():
    templates = _templates()
    lists = {}
    for wt in ("fantasy", "modern", "future"):
        spec = worldgen.normalize_spec({"world_type": wt, "scale": "region"})
        base = worldgen.choose_base(spec, templates=templates)
        world = worldgen.synthesize(spec, base, seed=3)
        goods = _goods(world)
        assert goods, "%s 世界应产出 goods" % wt
        names = {g["name"] for g in goods}
        categories = {g["category"] for g in goods}
        assert set(worldgen.GOODS_REQUIRED_CATEGORIES) <= categories, (wt, categories)
        assert all(g["era"] == wt for g in goods), "%s 的 goods era 应与世界类型一致" % wt
        lists[wt] = names
        # 时代锚点：奇幻出火绒类、现代出罐头/汽油、未来出聚变电池——互不串档。
    assert "灯油" in lists["fantasy"] and "汽油" not in lists["fantasy"], "奇幻世界不能卖汽油"
    assert "罐头食品" in lists["modern"] and "聚变电池" not in lists["modern"], "现代世界不能卖聚变电池"
    assert "聚变电池" in lists["future"] and "灯油" not in lists["future"]
    # 三个官方世界类型的 goods 清单互不相同。
    keys = list(lists)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            assert lists[keys[i]] != lists[keys[j]], "%s 与 %s 的 goods 应互不相同" % (keys[i], keys[j])


def test_goods_extra_upper_uncapped():
    templates = _templates()
    spec = worldgen.normalize_spec({
        "world_type": "modern", "scale": "sketch",
        "economy_on": True,
        "goods_extra": [{"name": "定制货物甲", "category": "luxury"},
                        {"name": "定制货物乙", "category": "food"}],
    })
    base = worldgen.choose_base(spec, templates=templates)
    world = worldgen.synthesize(spec, base, seed=3)
    names = {g["name"] for g in _goods(world)}
    assert {"定制货物甲", "定制货物乙"} <= names, "spec.goods_extra 应可上不封顶追加"
    assert not worldgen.has_errors(worldgen.validate_world(world, spec=spec))


def test_goods_era_violation_error():
    templates = _templates()
    spec = worldgen.normalize_spec({"world_type": "fantasy", "scale": "region"})
    base = worldgen.choose_base(spec, templates=templates)
    world = worldgen.synthesize(spec, base, seed=3)
    # 构造「奇幻世界卖激光枪」：混入 future 档 goods → error。
    world["goods"].append({
        "good_id": "good-99", "name": "激光枪", "category": "military",
        "tier": 3, "base_price": 500, "stock": 3, "produced_by": [],
        "era": "future",
    })
    codes = _error_codes(worldgen.validate_world(world, spec=spec))
    assert "E_GOODS_ERA" in codes, codes


def test_goods_coverage_error():
    templates = _templates()
    spec = worldgen.normalize_spec({"world_type": "modern", "scale": "region"})
    base = worldgen.choose_base(spec, templates=templates)
    world = worldgen.synthesize(spec, base, seed=3)
    world["goods"] = [g for g in world["goods"] if g["category"] != "food"]
    codes = _error_codes(worldgen.validate_world(world, spec=spec))
    assert "E_GOODS_COVER" in codes, codes
    # 经济开档但 goods 整个缺失 → error。
    del world["goods"]
    codes = _error_codes(worldgen.validate_world(world, spec=spec))
    assert "E_SPEC_ECONOMY" in codes, codes
    # 经济关档（sketch 默认）→ 无 goods 键、无 error。
    spec2 = worldgen.normalize_spec({"world_type": "modern", "scale": "sketch"})
    assert spec2["economy_on"] is False
    world2 = worldgen.synthesize(spec2, worldgen.choose_base(spec2, templates=templates), seed=3)
    assert "goods" not in world2
    assert not worldgen.has_errors(worldgen.validate_world(world2, spec=spec2))


# ── 6. 回归锚：三官方世界 + fixture 零 error ─────────────────────────────

def test_no_false_positives_on_existing_worlds():
    for key in OFFICIAL_WORLDS:
        issues = worldgen.validate_world(_load_world(key))
        errors = _error_codes(issues)
        assert not errors, (key, errors)
    fixture = _load_fixture()
    issues = worldgen.validate_world(fixture)
    errors = _error_codes(issues)
    assert not errors, ("fixture", errors)


# ── 7. normalize_spec 钳制与默认表 ────────────────────────────────────────

def test_normalize_spec_clamps_and_defaults():
    spec = worldgen.normalize_spec({
        "world_type": "steampunk", "scale": "巨大",
        "regions_req": {"count": 999, "key_places_per_region": 0},
        "npcs_req": {"named": 100000},
        "factions_req": {"count": -3},
        "tone_terms": ["港口", "", None, 42],
    })
    assert spec["world_type"] == "modern", "不认识的 world_type 落 modern（不推导）"
    assert spec["scale"] == "region"
    assert spec["regions_req"]["count"] == 10
    assert spec["regions_req"]["key_places_per_region"] == 1
    assert spec["npcs_req"]["named"] == 80
    assert spec["factions_req"]["count"] == 0
    assert spec["tone_terms"] == ["港口"]
    notes = spec.get("spec_notes") or []
    assert notes, "钳制应记 spec_notes（可解释）"
    # 缺省：region 档默认表 + economy_on=True。
    spec2 = worldgen.normalize_spec({})
    assert spec2["world_type"] == "modern" and spec2["engine"] == "tactics"
    assert spec2["regions_req"] == {"count": 5, "key_places_per_region": 4}
    assert spec2["npcs_req"] == {"named": 30}
    assert spec2["economy_on"] is True
    assert spec2["secrets_required"] is True
    # sketch 档：经济默认关。
    assert worldgen.normalize_spec({"scale": "sketch"})["economy_on"] is False


def test_law_contract():
    templates = _templates()
    spec = worldgen.normalize_spec({
        "world_type": "fantasy",
        "laws_req": [{"name": "禁魔令", "forbid_tags": ["秘法"], "threshold": "tolerate"},
                     {"name": "坏法律", "forbid_tags": ["x"], "threshold": "jail"}],
    })
    base = worldgen.choose_base(spec, templates=templates)
    world = worldgen.synthesize(spec, base, seed=8)
    # 合成期把非法 threshold 归一为 forbid。
    assert world["laws"][1]["threshold"] == "forbid"
    assert not worldgen.has_errors(worldgen.validate_world(world, spec=spec))
    # 直接构造非法 threshold → 校验器报 error（neura 契约：只认 allow/tolerate）。
    world["laws"][0]["threshold"] = "jail"
    codes = _error_codes(worldgen.validate_world(world))
    assert "E_LAW_THRESHOLD" in codes, codes


def test_lexicon_overrides_and_tone_warn():
    spec = worldgen.normalize_spec({"world_type": "modern", "tone_terms": ["港口", "地下", "完全没这个词"]})
    overrides, uncovered = worldgen._lexicon_overrides(spec)
    traits = {o["trait"] for o in overrides}
    assert {"settlement", "below"} <= traits, traits
    assert uncovered == ["完全没这个词"]
    base = worldgen.choose_base(spec)
    world = worldgen.synthesize(spec, base, seed=1)
    assert world["lexicon_overrides"], "覆盖项应随合成产出（加词不加地图）"
    codes = [i["code"] for i in worldgen.validate_world(world) if i["level"] == "warn"]
    assert "W_TONE_UNCOVERED" in codes, codes


# ── 8. 纯函数性：只 import 标准库、输入不被改写 ──────────────────────────

def test_import_purity():
    source_path = os.path.join(ROOT, "worldgen.py")
    with open(source_path, encoding="utf-8") as fh:
        source = fh.read()
    allowed = {"copy", "hashlib", "random"}
    imports = set()
    for line in source.splitlines():
        line = line.strip()
        if line.startswith("import "):
            imports.add(line.split()[1].split(".")[0])
        elif line.startswith("from ") and " import " in line:
            imports.add(line.split()[1].split(".")[0])
    assert imports <= allowed, "worldgen 只允许标准库 import：%s" % (imports - allowed)
    for banned in ("notdnd_web", "state", "neura", "atlas", "prism_core", "urllib", "http"):
        assert banned not in imports


def test_pure_function_inputs_untouched():
    templates = _templates()
    spec = worldgen.normalize_spec({"world_type": "future", "scale": "domain"})
    spec_before = _dumps(spec)
    templates_before = _dumps(templates)
    base = worldgen.choose_base(spec, templates=templates)
    worldgen.synthesize(spec, base, seed=9)
    assert _dumps(spec) == spec_before, "synthesize 不应改写 spec"
    assert _dumps(templates) == templates_before, "synthesize 不应改写模板"


# ── 9. W2：ATLAS 校验器（Issue #382 C1–C7） ───────────────────────────────

import atlas as al  # noqa: E402  （测试允许 import atlas；worldgen 不行，V8）
import atlas_compile as ac  # noqa: E402
import atlas_gen as ag  # noqa: E402

_LEXICON = _load_lexicon()
_SYSTEM_TABLES = _load_system_tables()


def _world_doc(key):
    """官方世界或 fixture 的 world.module dict（每次新读，防串改）。"""
    if key == "fixture":
        return _load_fixture()
    return _load_world(key)


def _compiled(key, seed=0):
    """(atlas, world)：编译好的 atlas 与其 world.module。"""
    world = _world_doc(key)
    return ac.compile_world(world, _LEXICON, seed=seed), world


def _anchors(atlas):
    return sorted(pid for frame in atlas["frames"].values()
                  for pid, place in frame["places"].items()
                  if place["kind"] in ("region", "site"))


def _generate_all_sites(atlas, world):
    for anchor in _anchors(atlas):
        ag.generate_site(atlas, anchor, world=world, system_tables=_SYSTEM_TABLES)


def _error_issues(issues):
    return [i for i in issues if i.get("level") == "error"]


def _edge_records(atlas):
    """全部连接记录的元组集（修边单调性断言用）。"""
    out = set()
    for frame in atlas["frames"].values():
        for pid, place in frame["places"].items():
            for link in place["links"]:
                out.add((pid, link["to"], link["via"], link.get("band"),
                         bool(link.get("oneway"))))
    return out


def _remove_records(atlas, a_id, b_id):
    """删掉 a↔b 之间的全部连接记录（构造破损输入用，仅测试）。"""
    for place in (al.find_place(atlas, a_id), al.find_place(atlas, b_id)):
        if place is not None:
            place["links"] = [l for l in place["links"]
                              if l["to"] not in (a_id, b_id)]


def _in_frame_components(frame):
    pids = set(frame["places"])
    adj = {pid: set() for pid in pids}
    for pid in pids:
        for link in frame["places"][pid]["links"]:
            if link["to"] in adj:
                adj[pid].add(link["to"])
                adj[link["to"]].add(pid)
    seen, comps = set(), []
    for pid in sorted(pids):
        if pid in seen:
            continue
        stack, comp = [pid], set()
        while stack:
            cur = stack.pop()
            if cur in comp:
                continue
            comp.add(cur)
            stack.extend(adj[cur] - comp)
        seen |= comp
        comps.append(comp)
    return comps


def _break_c2(atlas):
    """在某个 site 帧里删一条桥边制造断连（本地判定找边，确定性）。"""
    for fid in sorted(atlas["frames"]):
        frame = atlas["frames"][fid]
        if "/site/" not in fid or len(_in_frame_components(frame)) != 1:
            continue
        for pid in sorted(frame["places"]):
            for link in list(frame["places"][pid]["links"]):
                to = link["to"]
                if to not in frame["places"] or pid >= to:
                    continue
                links_a = list(frame["places"][pid]["links"])
                links_b = list(frame["places"][to]["links"])
                frame["places"][pid]["links"] = [l for l in links_a if l["to"] != to]
                frame["places"][to]["links"] = [l for l in links_b if l["to"] != pid]
                if len(_in_frame_components(frame)) > 1:
                    return fid, pid, to
                frame["places"][pid]["links"] = links_a
                frame["places"][to]["links"] = links_b
    raise AssertionError("找不到可断链的桥边")


def test_atlas_validate_regression_anchor():
    """W2 回归锚：三官方世界 + fixture，编译 / 全量 site 生成 / 战术帧
    投影三个时点上 validate_atlas 零 error（warn 允许）。"""
    for key in OFFICIAL_WORLDS + ["fixture"]:
        atlas, world = _compiled(key)
        issues = worldgen.validate_atlas(atlas, world=world)
        assert not _error_issues(issues), (key, "compile", _error_issues(issues))
        issues = worldgen.validate_atlas(atlas, world=world,
                                         checks=worldgen.ATLAS_CHECKS_POST_COMPILE)
        assert not _error_issues(issues), (key, "post-compile", _error_issues(issues))
        _generate_all_sites(atlas, world)
        rooms = sorted(pid for frame in atlas["frames"].values()
                       for pid, place in frame["places"].items()
                       if place["kind"] == "room")[:4]
        for room in rooms:
            ag.generate_tactical(atlas, room)
        issues = worldgen.validate_atlas(atlas, world=world)
        assert not _error_issues(issues), (key, "generated", _error_issues(issues))


def test_atlas_checks_timing_constants():
    """§5.1 时点表：compile 之后 C1–C4（C8 在 validate_world），site /
    tactical 生成之后 C1–C7。C7 破损在 POST_COMPILE 时点下不报。"""
    assert worldgen.ATLAS_CHECKS_POST_COMPILE == ("C1", "C2", "C3", "C4")
    assert worldgen.ATLAS_CHECKS_POST_GENERATE == tuple("C%d" % n for n in range(1, 8))
    atlas, _world = _compiled("fixture")
    link = next(l for frame in atlas["frames"].values()
                for place in frame["places"].values() for l in place["links"])
    link["band"] = "far"
    assert not _error_issues(worldgen.validate_atlas(
        atlas, checks=worldgen.ATLAS_CHECKS_POST_COMPILE))
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C7_BAND" in codes, codes


def test_c1_duplicate_positions():
    atlas, _world = _compiled("fixture")
    surface = atlas["frames"]["fiction-test/surface"]
    region = surface["places"]["fiction-test/r-core"]
    # 同帧同格（footprint 展开后重叠）→ error。
    al.add_place(atlas, {"id": "fiction-test/r-core/g0_0_9", "name": "叠格",
                         "kind": "fill", "frame_id": "fiction-test/surface",
                         "x": region["x"], "y": region["y"], "z": 0})
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C1_CELL_DUP" in codes, codes
    # id 字段与登记键不一致 → error。
    some_pid = next(iter(surface["places"]))
    surface["places"][some_pid]["id"] = "mismatched-id"
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C1_DUP_ID" in codes, codes
    # 跨帧重复登记同一 id → error。
    atlas["frames"]["fiction-test/abstract"]["places"]["fiction-test/r-core"] = \
        copy.deepcopy(region)
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C1_DUP_ID" in codes, codes


def test_c2_connectivity():
    # 有向 oneway：a→b、c→b 时无起点能遍历全帧 → error；补成环路后通过。
    tiny = al.new_atlas("tiny", 1)
    al.add_frame(tiny, "tiny/surface", space="metric", z_meaning="层", cell="街区")
    for i, pid in enumerate(("a", "b", "c")):
        al.add_place(tiny, {"id": pid, "name": pid, "kind": "room",
                            "frame_id": "tiny/surface", "x": i, "y": 0, "z": 0})
    al.add_link(tiny, "tiny/surface", "a", "b", "东", oneway=True)
    al.add_link(tiny, "tiny/surface", "c", "b", "西", oneway=True)
    codes = [i["code"] for i in worldgen.validate_atlas(tiny)]
    assert "C2_ONEWAY" in codes, codes
    al.add_link(tiny, "tiny/surface", "b", "c", "东", oneway=True)
    assert not _error_issues(worldgen.validate_atlas(tiny)), codes
    # site 帧断连 → error。
    atlas, world = _compiled("fixture")
    _generate_all_sites(atlas, world)
    fid, pid, to = _break_c2(atlas)
    issues = worldgen.validate_atlas(atlas)
    errors = _error_issues(issues)
    assert any(i["code"] == "C2_DISCONNECTED" for i in errors), errors
    assert errors[0]["frame_id"] == fid


def test_c2_unlisted_float_islands_exempt():
    """entropic-net 的 unlisted 区域与其关键地点是零坐标浮岛（合法）：
    校验器不得误伤（entropic-net 实测有 region-09 浮岛）。"""
    atlas, world = _compiled("entropic-net")
    issues = worldgen.validate_atlas(atlas, world=world)
    assert not _error_issues(issues), _error_issues(issues)


def test_c3_gate_existence():
    anchor_id = "fiction-test/r-core"
    fid = "fiction-test/site/fiction-test/r-core"

    def fresh():
        atlas, world = _compiled("fixture")
        _generate_all_sites(atlas, world)
        return atlas, world

    # 缺门：删掉锚门两端的记录 → error。
    atlas, world = fresh()
    anchor = al.find_place(atlas, anchor_id)
    frame = atlas["frames"][fid]
    gate_to = next(l["to"] for l in anchor["links"]
                   if l["via"] == "门" and l["to"] in frame["places"])
    _remove_records(atlas, anchor_id, gate_to)
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C3_NO_GATE" in codes, codes
    # 多门（应恰有一条）→ error。
    atlas, _w = fresh()
    anchor = al.find_place(atlas, anchor_id)
    frame = atlas["frames"][fid]
    other_cell = next(pid for pid in sorted(frame["places"])
                      if pid != gate_to and frame["places"][pid]["kind"] == "room")
    al.add_link(atlas, fid, anchor_id, other_cell, "门")
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C3_GATE_DUP" in codes, codes
    # 入口格缺回程门 → error。
    atlas, _w = fresh()
    entrance = al.find_place(atlas, gate_to)
    entrance["links"] = [l for l in entrance["links"]
                         if not (l["to"] == anchor_id and l["via"] == "门")]
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C3_NO_BACK" in codes, codes
    # 区域帧至少一条边：孤立区域 → error。
    atlas, _w = fresh()
    al.find_place(atlas, "fiction-test/r-ring")["links"] = []
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C3_REGION_EDGE" in codes, codes


def test_c4_key_places_reachable():
    atlas, world = _compiled("fixture")
    _generate_all_sites(atlas, world)
    region_id, kp_id = "fiction-test/r-north", "fiction-test/r-north/k0"
    # discovered 与校验无关：全部 unseen 也不报（校验结构不是探索态）。
    for frame in atlas["frames"].values():
        for place in frame["places"].values():
            place["discovered"] = "unseen"
    assert not _error_issues(worldgen.validate_atlas(atlas))
    # 删掉 kp 与区域之间的门 → 关键地点不可达 → error。
    _remove_records(atlas, region_id, kp_id)
    issues = worldgen.validate_atlas(atlas)
    errors = _error_issues(issues)
    assert any(i["code"] == "C4_KP_UNREACHABLE" and i["place_id"] == kp_id
               for i in errors), errors


def test_c5_kind_and_venue():
    craft = {
        "kind": "world.module", "world": {"key": "venue-test", "name": "场地测试"},
        "regions": [{"id": "r1", "name": "测试区", "summary": "普通街区"}],
        "random_tables": [{"rows": [{"tag": "掩体", "result": "木箱堆"},
                                    {"tag": "危险", "result": "塌陷的地面"}]}],
    }
    atlas, _w = _compiled("fixture")
    ag.generate_site(atlas, "fiction-test/r-core", world=craft,
                     system_tables=_SYSTEM_TABLES)
    fid = "fiction-test/site/fiction-test/r-core"
    frame = atlas["frames"][fid]
    # 生成器从池里取 venue → 无 C5 warn。
    issues = worldgen.validate_atlas(atlas, world=craft)
    assert "C5_VENUE" not in [i["code"] for i in issues], issues
    # 不在池里的 venue 行 → warn（不阻断）。
    room = next(frame["places"][pid] for pid in sorted(frame["places"])
                if frame["places"][pid]["kind"] == "room")
    room.setdefault("features", []).append({"tag": "危险", "text": "来历不明之物"})
    issues = worldgen.validate_atlas(atlas, world=craft)
    warns = [i for i in issues if i["code"] == "C5_VENUE"]
    assert warns and all(i["level"] == "warn" for i in warns), issues
    # kind 不合法 → warn；且 warn 不阻断。
    frame["places"]["bad-kind"] = {"id": "bad-kind", "name": "怪", "kind": "castle",
                                   "frame_id": fid, "links": [], "traits": [],
                                   "discovered": "unseen", "source": "authored"}
    issues = worldgen.validate_atlas(atlas, world=craft)
    kind_warns = [i for i in issues if i["code"] == "C5_KIND"]
    assert kind_warns and kind_warns[0]["level"] == "warn", issues
    assert not worldgen.has_errors(issues)


def test_c6_economy_contradiction():
    atlas, world = _compiled("fixture")
    assert "wilderness" in atlas["region_info"]["fiction-test/r-north"]["traits"]
    # 经济开档：荒野区域产出「铁锭」→ warn（白名单抽查）。
    world["goods"] = [{"good_id": "good-01", "name": "铁锭", "category": "tool",
                       "tier": 2, "base_price": 12, "stock": 40,
                       "produced_by": ["r-north"], "era": "fantasy"}]
    issues = worldgen.validate_atlas(atlas, world=world)
    warns = [i for i in issues if i["code"] == "C6_GOODS_TRAIT"]
    assert warns and warns[0]["level"] == "warn", issues
    # regions[].economy 产出声明同样抽查（§6.6 W8 转正前可选字段）；
    # C6 的门槛是经济开档（world 带 goods），声明抽查同门槛生效。
    world2 = _world_doc("fixture")
    world2["goods"] = [{"good_id": "good-00", "name": "黑麦干粮", "category": "food",
                        "tier": 1, "base_price": 3, "stock": 80,
                        "produced_by": [], "era": "fantasy"}]
    world2["regions"][0]["economy"] = {"produces": ["激光枪"]}
    issues = worldgen.validate_atlas(atlas, world=world2)
    assert any(i["code"] == "C6_GOODS_TRAIT" for i in issues), issues
    # 未开档 / 产出指向不存在区域 → 不报。
    assert not any(i["code"].startswith("C6")
                   for i in worldgen.validate_atlas(atlas, world=_world_doc("fixture")))
    world3 = _world_doc("fixture")
    world3["goods"] = [{"good_id": "good-02", "name": "铁锭", "category": "tool",
                        "tier": 2, "base_price": 12, "stock": 40,
                        "produced_by": ["不存在的区域"], "era": "fantasy"}]
    assert not any(i["code"].startswith("C6")
                   for i in worldgen.validate_atlas(atlas, world=world3))


def test_c7_movement_cost():
    atlas, world = _compiled("fixture")
    _generate_all_sites(atlas, world)
    fid = next(f for f in sorted(atlas["frames"])
               if "/site/" in f and atlas["frames"][f]["space"] == "metric")
    frame = atlas["frames"][fid]
    # 档位取值非法 → error。
    link = next(l for pid in sorted(frame["places"])
                for l in frame["places"][pid]["links"]
                if l["to"] in frame["places"])
    link["band"] = "far"
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C7_BAND" in codes, codes
    link["band"] = "long"  # 单端改档：双边不一致 → error。
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C7_BAND_MISMATCH" in codes, codes
    link["band"] = None
    # metric 地点帧：cardinal via 指向不相邻格 → error（几何不一致）。
    cells = sorted(pid for pid in frame["places"]
                   if all(k in frame["places"][pid] for k in ("x", "y", "z")))
    far_pair = next((a, b) for a in cells for b in cells
                    if a != b and abs(frame["places"][a]["x"] - frame["places"][b]["x"])
                    + abs(frame["places"][a]["y"] - frame["places"][b]["y"]) > 1)
    al.add_link(atlas, fid, far_pair[0], far_pair[1], "北")
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C7_GEOMETRY" in codes, codes
    # 相邻一格（1 跨区）上带 medium 档 → 与 zone_steps 折算矛盾。
    delta_via = {(0, 1): "北", (0, -1): "南", (1, 0): "东", (-1, 0): "西"}
    adj_pair = None
    for a in cells:
        for b in cells:
            if a == b:
                continue
            delta = (frame["places"][b]["x"] - frame["places"][a]["x"],
                     frame["places"][b]["y"] - frame["places"][a]["y"])
            if delta in delta_via:
                adj_pair = (a, b, delta_via[delta])
                break
        if adj_pair:
            break
    assert adj_pair, cells
    al.add_link(atlas, fid, adj_pair[0], adj_pair[1], adj_pair[2], band="medium")
    codes = [i["code"] for i in worldgen.validate_atlas(atlas)]
    assert "C7_GEOMETRY" in codes, codes
    # 干净 atlas 不误报。
    atlas2, _w2 = _compiled("fixture")
    _generate_all_sites(atlas2, _w2)
    assert not any(i["code"].startswith("C7")
                   for i in worldgen.validate_atlas(atlas2, world=_w2))


# ── 10. W2：repair 三类局部修复 + provenance（§5.2） ─────────────────────

def test_repair_c2_bridge():
    atlas, world = _compiled("fixture")
    _generate_all_sites(atlas, world)
    fid, pid, to = _break_c2(atlas)
    issues = worldgen.validate_atlas(atlas)
    assert any(i["code"] == "C2_DISCONNECTED" for i in _error_issues(issues))
    broken_edges = _edge_records(atlas)
    result = worldgen.repair(atlas, issues, rng=random.Random(0))
    assert not _error_issues(worldgen.validate_atlas(atlas, world=world)), \
        result["provenance"]
    repaired_edges = _edge_records(atlas)
    assert broken_edges <= repaired_edges, "修复不应删除已有边"
    assert len(repaired_edges) > len(broken_edges), "修复应只新增边"
    assert all(item["code"] == "C2" for item in result["repaired"])
    assert result["provenance"]["count"] >= 1
    # provenance：修复过的连接可识别来源。
    new_links = [l for frame in atlas["frames"].values()
                 for place in frame["places"].values() for l in place["links"]
                 if l.get("source") == "worldgen:repair"]
    assert new_links, "修复边应带 worldgen:repair 来源标注"
    prov = worldgen.atlas_provenance(atlas)
    assert prov["repaired_links"] == len(new_links)
    assert prov["places"]["authored"] > 0 and prov["places"]["generated"] > 0


def test_repair_c3_missing_gate():
    atlas, world = _compiled("fixture")
    _generate_all_sites(atlas, world)
    fid = "fiction-test/site/fiction-test/r-core"
    anchor_id = "fiction-test/r-core"
    anchor = al.find_place(atlas, anchor_id)
    gate_to = next(l["to"] for l in anchor["links"]
                   if l["via"] == "门" and l["to"] in atlas["frames"][fid]["places"])
    _remove_records(atlas, anchor_id, gate_to)
    issues = worldgen.validate_atlas(atlas)
    assert any(i["code"] == "C3_NO_GATE" for i in _error_issues(issues))
    broken_edges = _edge_records(atlas)
    result = worldgen.repair(atlas, issues, rng=random.Random(0))
    assert not _error_issues(worldgen.validate_atlas(atlas, world=world)), \
        result["provenance"]
    assert broken_edges <= _edge_records(atlas)
    assert [item["code"] for item in result["repaired"]] == ["C3"]
    assert result["repaired"][0]["endpoints"] == [anchor_id, gate_to], \
        "缺门修复应挂回 (1,1,0) 入口格"


def test_repair_c4_unreachable_kp():
    atlas, world = _compiled("fixture")
    _generate_all_sites(atlas, world)
    region_id, kp_id = "fiction-test/r-north", "fiction-test/r-north/k0"
    _remove_records(atlas, region_id, kp_id)
    issues = worldgen.validate_atlas(atlas)
    assert any(i["code"] == "C4_KP_UNREACHABLE" for i in _error_issues(issues))
    broken_edges = _edge_records(atlas)
    result = worldgen.repair(atlas, issues, rng=random.Random(0))
    assert not _error_issues(worldgen.validate_atlas(atlas, world=world)), \
        result["provenance"]
    assert broken_edges <= _edge_records(atlas)
    assert [item["code"] for item in result["repaired"]] == ["C4"]
    assert result["repaired"][0]["endpoints"] == [kp_id, region_id], \
        "无坐标关键地点应优先挂回区域锚点"


def test_repair_unrepairable_not_touched():
    """C1 不在三类修复范围：不误修、如实保留在 provenance.unrepaired。"""
    atlas, world = _compiled("fixture")
    _generate_all_sites(atlas, world)
    surface = atlas["frames"]["fiction-test/surface"]
    region = surface["places"]["fiction-test/r-core"]
    al.add_place(atlas, {"id": "fiction-test/dup-cell", "name": "叠格",
                         "kind": "fill", "frame_id": "fiction-test/surface",
                         "x": region["x"], "y": region["y"], "z": 0})
    _break_c2(atlas)
    issues = worldgen.validate_atlas(atlas)
    assert any(i["code"] == "C1_CELL_DUP" for i in _error_issues(issues))
    result = worldgen.repair(atlas, issues, rng=random.Random(0))
    issues_after = worldgen.validate_atlas(atlas, world=world)
    codes = [i["code"] for i in _error_issues(issues_after)]
    assert codes == ["C1_CELL_DUP"], codes
    assert "C1_CELL_DUP" in result["provenance"]["unrepaired"]
    assert all(item["code"] == "C2" for item in result["repaired"])


def test_repair_deterministic():
    atlas_a, _ = _compiled("fixture")
    _generate_all_sites(atlas_a, _world_doc("fixture"))
    atlas_b = copy.deepcopy(atlas_a)
    fid_a, pid, to = _break_c2(atlas_a)
    _break_c2(atlas_b)
    issues_a = worldgen.validate_atlas(atlas_a)
    issues_b = worldgen.validate_atlas(atlas_b)
    worldgen.repair(atlas_a, issues_a, rng=random.Random(1))
    worldgen.repair(atlas_b, issues_b, rng=random.Random(999))
    assert json.dumps(atlas_a, sort_keys=True, ensure_ascii=False) == \
        json.dumps(atlas_b, sort_keys=True, ensure_ascii=False), \
        "repair 应确定性（rng 只留给 W3 的重掷编排）"


def test_atlas_provenance_summary():
    atlas, _world = _compiled("fixture")
    prov = worldgen.atlas_provenance(atlas)
    assert prov["repaired_links"] == 0
    assert prov["places"]["authored"] > 0
    assert prov["places"]["generated"] >= 0
    assert set(prov["places"]) == {"authored", "generated", "other"}




def main():
    checks = [value for name, value in sorted(globals().items())
              if name.startswith("test_") and callable(value)]
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败 [%s]: %s" % (check.__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks), failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
