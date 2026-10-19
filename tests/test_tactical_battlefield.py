#!/usr/bin/env python3
"""六角格战场数据加载 / 校验 / 查询回归（Issue #417）。

覆盖：
- 正例：metric / abstract 战场、JSON 路径加载、surface 隔离（同 q/r 两层共存）、
  单格与多格 footprint、确定性 fixture 工厂；
- 负例：重复 cell_id、高度字段缺失 / 混用、metric 缺 hex_size_cm、abstract 缺 unit
  或误带 cm、悬空 vertical_links、未知 cover_grade / vertical_link.kind、非法坐标；
- `validate_placement`：越界、blocks_movement、互斥占用都要报出具体格；
- 边界：没有跨口径换算函数、模块不含硬编码阈值、不变式只有一份实现。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_tactical_battlefield.py` 运行。
"""
import json
import os
import re
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)

import tactical_battlefield as tb  # noqa: E402

RULES = tb.space_rules()
HEX = RULES["scale"]["hex_size_cm"]
GRADES = RULES["cover"]["grades"]
LINK_KINDS = RULES["vertical_links"]["kinds"]
SOURCE = "docs/system/02c-hex-battlefield.md:29"
BATTLEFIELD_MODULE = os.path.join(ROOT, "tactical_battlefield.py")


# ── 样例 ────────────────────────────────────────────────────────────────

def metric_cell(cell_id, q, r, **overrides):
    cell = {
        "cell_id": cell_id,
        "q": q,
        "r": r,
        "surface_id": "ground",
        "elevation_cm": 0,
        "terrain_id": "floor",
        "terrain_tags": ["open"],
        "movement_cost": HEX,
        "blocks_movement": False,
        "blocks_los": False,
        "hazard_refs": [],
        "feature_refs": [],
        "spawnable": True,
    }
    cell.update(overrides)
    return cell


def metric_battlefield():
    return {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": SOURCE,
        "battlefield_id": "bf-metric-01",
        "world_id": "threshold",
        "site_id": "site-metric-01",
        "atlas_ref": "threshold/region-01",
        "seed": 20261020,
        "generator_version": "1.0.0",
        "geometry": {"type": "hex_axial"},
        "scale": {"mode": "metric", "hex_size_cm": HEX},
        "cells": [
            metric_cell("c-0-0", 0, 0),
            metric_cell("c-1-0", 1, 0, elevation_cm=60, blocks_movement=True,
                        blocks_los=True, obstacle_height=120, cover_grade="full"),
            metric_cell("c-0-1", 0, 1, elevation_cm=30, movement_cost=2 * HEX,
                        terrain_id="rubble", terrain_tags=["difficult"]),
        ],
        "vertical_links": [
            {
                "link_id": "vl-ramp-01",
                "from_cell_id": "c-0-0",
                "to_cell_id": "c-0-1",
                "kind": "ramp",
                "movement_cost": HEX,
                "bidirectional": True,
                "requirements": [],
            }
        ],
    }


def abstract_battlefield():
    return {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": SOURCE,
        "battlefield_id": "bf-abstract-01",
        "world_id": "emberfall",
        "seed": "crypt-02-seed",
        "generator_version": "1.0.0",
        "geometry": {"type": "hex_axial"},
        "scale": {"mode": "abstract", "unit": "step"},
        "cells": [
            {
                "cell_id": "c-0-0", "q": 0, "r": 0, "surface_id": "crypt-floor",
                "elevation_level": 0, "terrain_id": "stone", "movement_cost": 1,
                "blocks_movement": False, "blocks_los": False, "spawnable": True,
            },
            {
                "cell_id": "c-0-1", "q": 0, "r": 1, "surface_id": "crypt-floor",
                "elevation_level": 1, "terrain_id": "stairs", "movement_cost": 2,
                "blocks_movement": False, "blocks_los": False, "spawnable": False,
            },
        ],
        "vertical_links": [
            {
                "link_id": "vl-stairs-01",
                "from_cell_id": "c-0-0",
                "to_cell_id": "c-0-1",
                "kind": "stairs",
                "movement_cost": 2,
                "bidirectional": True,
                "requirements": [],
            }
        ],
    }


def layered_battlefield():
    """同一 q/r 在 ground / basement 两层共存（surface 隔离）。"""
    doc = metric_battlefield()
    doc["cells"].append(metric_cell("c-b-0-0", 0, 0, surface_id="basement",
                                    elevation_cm=-300))
    doc["cells"].append(metric_cell("c-b-0-1", 0, 1, surface_id="basement",
                                    elevation_cm=-240))
    return doc


def errors_of(instance):
    return tb.validate_battlefield(instance)


def assert_error(instance, path, marker):
    errors = errors_of(instance)
    assert errors, "%s: 未被判失败（期望含 %r）" % (path, marker)
    assert any(path in error and marker in error for error in errors), \
        "报错未同时含 %r / %r（实为 %s）" % (path, marker, errors)
    return errors


# ── 正例 ────────────────────────────────────────────────────────────────

def check_metric_battlefield():
    doc = metric_battlefield()
    assert errors_of(doc) == [], "metric 战场被判失败: %s" % errors_of(doc)
    assert tb.load_battlefield(doc) is doc, "load_battlefield(dict) 应返回同一份数据"


def check_abstract_battlefield():
    doc = abstract_battlefield()
    assert errors_of(doc) == [], "abstract 战场被判失败: %s" % errors_of(doc)


def check_load_from_json_path():
    with tempfile.TemporaryDirectory() as tmp:
        good = os.path.join(tmp, "battlefield.json")
        with open(good, "w", encoding="utf-8") as handle:
            json.dump(metric_battlefield(), handle, ensure_ascii=False)
        doc = tb.load_battlefield(good)
        assert doc["battlefield_id"] == "bf-metric-01", "从路径加载的数据不对"

        broken = os.path.join(tmp, "broken.json")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        try:
            tb.load_battlefield(broken)
        except tb.BattlefieldError as error:
            assert error.errors and "JSON" in error.errors[0], \
                "坏 JSON 的报错不明确: %s" % error.errors
        else:
            raise AssertionError("坏 JSON 未被拒绝")

        try:
            tb.load_battlefield(os.path.join(tmp, "missing.json"))
        except tb.BattlefieldError as error:
            assert error.errors and "无法读取" in error.errors[0], \
                "缺失文件的报错不明确: %s" % error.errors
        else:
            raise AssertionError("缺失文件未被拒绝")


def check_surface_isolation():
    """同 q/r 不同 surface 共存；查询与 footprint 都不跨层串味。"""
    doc = layered_battlefield()
    assert errors_of(doc) == [], "两层同 q/r 被判失败: %s" % errors_of(doc)

    at_origin = tb.cells_at(doc, 0, 0)
    assert len(at_origin) == 2, "cells_at(0,0) 应返回两层的格: %r" % (at_origin,)
    assert [cell["surface_id"] for cell in at_origin] == ["basement", "ground"], \
        "cells_at 未按 (surface_id, cell_id) 排序: %r" % (at_origin,)
    assert tb.cell_at(doc, 0, 0, "ground")["cell_id"] == "c-0-0"
    assert tb.cell_at(doc, 0, 0, "basement")["cell_id"] == "c-b-0-0"
    assert tb.cell_at(doc, 0, 0, "roof") is None, "不存在的 surface 不应回落到别的层"

    footprint = [{"q": 0, "r": 0}, {"q": 0, "r": 1}]
    basement_volume = [cell["cell_id"] for cell in tb.footprint_cells(doc, "c-b-0-0", footprint)]
    assert basement_volume == ["c-b-0-0", "c-b-0-1"], \
        "basement 的 footprint 串到了 ground: %r" % (basement_volume,)
    ground_volume = [cell["cell_id"] for cell in tb.footprint_cells(doc, "c-0-0", footprint)]
    assert ground_volume == ["c-0-0", "c-0-1"], \
        "ground 的 footprint 串到了 basement: %r" % (ground_volume,)


def check_queries():
    doc = metric_battlefield()
    assert tb.cell_by_id(doc, "c-1-0")["q"] == 1
    assert tb.cell_by_id(doc, "c-9-9") is None, "未知 cell_id 不应按坐标兜底"
    assert [cell["cell_id"] for cell in tb.cells_at(doc, 1, 0)] == ["c-1-0"]
    assert tb.cells_at(doc, 5, 5) == [], "空坐标应返回空列表"
    assert tb.cell_at(doc, 5, 5, "ground") is None
    single = tb.footprint_cells(doc, "c-0-0", [{"q": 0, "r": 0}])
    assert [cell["cell_id"] for cell in single] == ["c-0-0"], "单格 footprint 解析错误"
    try:
        tb.footprint_cells(doc, "c-0-0", [{"q": 5, "r": 5}])
    except ValueError as error:
        assert "越界" in str(error), "越界报错不明确: %s" % error
    else:
        raise AssertionError("越界 footprint 未被拒绝")
    try:
        tb.footprint_cells(doc, "c-9-9", [{"q": 0, "r": 0}])
    except ValueError:
        pass
    else:
        raise AssertionError("未知 origin 未被拒绝")


def check_scale_accessors():
    """metric / abstract 各有独立入口；跨口径访问直接报错，不做隐式换算。"""
    metric = metric_battlefield()
    abstract = abstract_battlefield()
    assert tb.scale_mode(metric) == "metric"
    assert tb.scale_mode(abstract) == "abstract"
    assert tb.metric_elevation_cm(metric, metric["cells"][1]) == 60
    assert tb.abstract_elevation_level(abstract, abstract["cells"][1]) == 1
    cross_calls = (
        ("metric_elevation_cm", lambda: tb.metric_elevation_cm(abstract, abstract["cells"][0])),
        ("abstract_elevation_level",
         lambda: tb.abstract_elevation_level(metric, metric["cells"][0])),
    )
    for name, call in cross_calls:
        try:
            call()
        except ValueError:
            pass
        else:
            raise AssertionError("%s 在异口径战场上没有报错" % name)


def check_fixture_factory():
    """同 seed 同输出；metric / abstract / 多层都通过校验。"""
    first = tb.fixture_battlefield(11)
    second = tb.fixture_battlefield(11)
    other = tb.fixture_battlefield(12)
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True), \
        "同 seed 产生了不同战场"
    assert json.dumps(first, sort_keys=True) != json.dumps(other, sort_keys=True), \
        "不同 seed 产生了相同战场"
    for mode in ("metric", "abstract"):
        for radius in (0, 1, 2):
            doc = tb.fixture_battlefield(5, mode=mode, radius=radius)
            problems = errors_of(doc)
            assert not problems, "fixture(%s, radius=%d) 未通过校验: %s" % (
                mode, radius, problems)
    layered = tb.fixture_battlefield(5, surfaces=("ground", "upper"))
    assert errors_of(layered) == [], "多层 fixture 未通过校验: %s" % errors_of(layered)
    assert len(tb.cells_at(layered, 0, 0)) == 2, "多层 fixture 的 q/r 没有跨层复用"
    ids = [cell["cell_id"] for cell in layered["cells"]]
    assert len(ids) == len(set(ids)), "fixture 的 cell_id 不唯一"


# ── 负例 ────────────────────────────────────────────────────────────────

def check_duplicate_cell_id():
    doc = metric_battlefield()
    doc["cells"][1]["cell_id"] = "c-0-0"
    assert_error(doc, "$.cells[1].cell_id", "重复 cell_id")


def check_height_field_rules():
    missing = metric_battlefield()
    del missing["cells"][0]["elevation_cm"]
    assert_error(missing, "$.cells[0]", "缺少高度字段")

    mixed = metric_battlefield()
    mixed["cells"][0]["elevation_level"] = 2
    assert_error(mixed, "$.cells[0].elevation_level", "混用")

    metric_with_level = metric_battlefield()
    del metric_with_level["cells"][0]["elevation_cm"]
    metric_with_level["cells"][0]["elevation_level"] = 1
    assert_error(metric_with_level, "$.cells[0].elevation_level", "metric")

    abstract_with_cm = abstract_battlefield()
    del abstract_with_cm["cells"][0]["elevation_level"]
    abstract_with_cm["cells"][0]["elevation_cm"] = HEX
    assert_error(abstract_with_cm, "$.cells[0].elevation_cm", "abstract")


def check_scale_requirements():
    metric_without_size = metric_battlefield()
    del metric_without_size["scale"]["hex_size_cm"]
    assert_error(metric_without_size, "$.scale.hex_size_cm", "metric")

    abstract_without_unit = abstract_battlefield()
    del abstract_without_unit["scale"]["unit"]
    assert_error(abstract_without_unit, "$.scale.unit", "abstract")

    abstract_with_size = abstract_battlefield()
    abstract_with_size["scale"]["hex_size_cm"] = HEX
    assert_error(abstract_with_size, "$.scale.hex_size_cm", "abstract")

    unknown_mode = metric_battlefield()
    unknown_mode["scale"]["mode"] = "imperial"
    assert_error(unknown_mode, "$.scale.mode", "metric")


def check_dangling_vertical_links():
    bad_to = metric_battlefield()
    bad_to["vertical_links"][0]["to_cell_id"] = "c-9-9"
    assert_error(bad_to, "$.vertical_links[0].to_cell_id", "c-9-9")

    bad_from = metric_battlefield()
    bad_from["vertical_links"][0]["from_cell_id"] = "c-8-8"
    assert_error(bad_from, "$.vertical_links[0].from_cell_id", "悬空引用")


def check_controlled_enums():
    bad_cover = metric_battlefield()
    bad_cover["cells"][0]["cover_grade"] = "partial"
    assert_error(bad_cover, "$.cells[0].cover_grade", GRADES[0])

    bad_kind = metric_battlefield()
    bad_kind["vertical_links"][0]["kind"] = "escalator"
    assert_error(bad_kind, "$.vertical_links[0].kind", LINK_KINDS[0])


def check_invalid_coordinates():
    for bad in (1.0, "1", None, True):
        doc = metric_battlefield()
        doc["cells"][0]["q"] = bad
        assert_error(doc, "$.cells[0].q", "整数")

    missing = metric_battlefield()
    del missing["cells"][0]["r"]
    assert_error(missing, "$.cells[0]", "缺少必填字段 'r'")


def check_validate_placement():
    doc = metric_battlefield()
    blocked = "c-1-0"

    assert tb.validate_placement(doc, "unit-a", "c-0-0", [{"q": 0, "r": 0}]) == [], \
        "合法单格放置被判失败"
    assert tb.validate_placement(doc, "unit-a", "c-0-0", [{"q": 0, "r": 0}, {"q": 0, "r": 1}]) == [], \
        "合法多格放置被判失败"

    single = tb.validate_placement(doc, "unit-a", blocked, [{"q": 0, "r": 0}])
    assert single and single[0]["code"] == "blocked_movement", \
        "阻挡格未报 blocked_movement: %r" % (single,)
    assert single[0]["cell_id"] == blocked, "阻挡报错没有给出具体 cell_id: %r" % (single,)

    multi = tb.validate_placement(doc, "unit-a", "c-0-0", [{"q": 0, "r": 0}, {"q": 1, "r": 0}])
    assert any(item["code"] == "blocked_movement" and item["cell_id"] == blocked
               for item in multi), "多格 footprint 未报出被阻挡的具体格: %r" % (multi,)

    out = tb.validate_placement(doc, "unit-a", "c-0-0", [{"q": 0, "r": 0}, {"q": 5, "r": 5}])
    assert out and out[-1]["code"] == "out_of_bounds", "越界未报 out_of_bounds: %r" % (out,)
    assert out[-1]["q"] == 5 and out[-1]["r"] == 5 and out[-1]["cell_id"] is None, \
        "越界报错没有落到具体坐标: %r" % (out,)
    assert "c-0-0" in out[-1]["message"], "越界报错没有指出 origin: %r" % (out,)

    taken = tb.validate_placement(doc, "unit-a", "c-0-0",
                                  [{"q": 0, "r": 0}, {"q": 0, "r": 1}],
                                  occupied={"c-0-1": "unit-other"})
    assert len(taken) == 1 and taken[0]["code"] == "occupied", \
        "占用冲突未报 occupied: %r" % (taken,)
    assert taken[0]["cell_id"] == "c-0-1", "占用报错没有给出具体 cell_id: %r" % (taken,)

    own = tb.validate_placement(doc, "unit-a", "c-0-0", [{"q": 0, "r": 0}],
                                occupied={"c-0-0": "unit-a"})
    assert own == [], "自己占用自己的格被误报: %r" % (own,)

    unknown = tb.validate_placement(doc, "unit-a", "c-9-9", [{"q": 0, "r": 0}])
    assert unknown and unknown[0]["code"] == "unknown_origin", \
        "未知 origin 未报 unknown_origin: %r" % (unknown,)

    empty = tb.validate_placement(doc, "unit-a", "c-0-0", [])
    assert empty and empty[0]["code"] == "empty_footprint", \
        "空 footprint 未报 empty_footprint: %r" % (empty,)

    assert tb.validate_placement(doc, "unit-a", "c-0-0", [{"q": 0, "r": 0}]) == \
        tb.validate_placement(doc, "unit-a", "c-0-0", [{"q": 0, "r": 0}]), \
        "同一输入的放置诊断不稳定"


# ── 边界护栏 ────────────────────────────────────────────────────────────

def check_no_cross_scale_conversion():
    """metric / abstract 不互相换算：模块不提供任何跨口径 helper。"""
    banned = ("cm_to_cells", "cells_to_cm", "cm_to_steps", "steps_to_cm",
              "metric_to_abstract", "abstract_to_metric", "to_metric", "to_cells")
    found = [name for name in banned if hasattr(tb, name)]
    assert not found, "出现了跨口径换算函数: %s" % ", ".join(found)


def check_no_rule_thresholds_in_module():
    """阈值只能来自 tactical_space.json，模块里不得写死。"""
    with open(BATTLEFIELD_MODULE, encoding="utf-8") as handle:
        text = handle.read()
    hits = [token for token in ("150", "100", "50", "300", "1d6")
            if re.search(r"(?<![0-9])%s(?![0-9])" % re.escape(token), text)]
    assert not hits, "tactical_battlefield.py 出现硬编码阈值: %s" % ", ".join(hits)


def check_invariants_have_single_implementation():
    """不变式只在本模块实现；schema 测试改为 import，不得保留副本。"""
    with open(os.path.join(TESTS, "test_tactical_schema.py"), encoding="utf-8") as handle:
        text = handle.read()
    assert "from tactical_battlefield import battlefield_invariants" in text, \
        "tests/test_tactical_schema.py 未 import 模块里的不变式"
    assert "def battlefield_invariants" not in text, \
        "tests/test_tactical_schema.py 仍保留第二份不变式实现"


def main():
    checks = (
        check_metric_battlefield,
        check_abstract_battlefield,
        check_load_from_json_path,
        check_surface_isolation,
        check_queries,
        check_scale_accessors,
        check_fixture_factory,
        check_duplicate_cell_id,
        check_height_field_rules,
        check_scale_requirements,
        check_dangling_vertical_links,
        check_controlled_enums,
        check_invalid_coordinates,
        check_validate_placement,
        check_no_cross_scale_conversion,
        check_no_rule_thresholds_in_module,
        check_invariants_have_single_implementation,
    )
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败: %s" % error)
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks), failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
