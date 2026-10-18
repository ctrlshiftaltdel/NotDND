#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""worldgen 内核（M12-W1，Issue #379）回归测试。

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
"""

import copy
import json
import os
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


# ── main ──────────────────────────────────────────────────────────────────

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
