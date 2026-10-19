#!/usr/bin/env python3
"""六角格战场数据定义回归（Issue #416）。

覆盖：
- 正例：metric / abstract 战场（含 -300 cm 地下室这一负高程用例）、battle_state、
  battle_event、data/system/tactical_space.json；
- 负例：高度口径混用、缺 cell_id、重复 cell_id、非法 vertical_link.kind、
  未知 cover_grade、非法 elevation 数值（带单位字符串 / 小数）、负的 movement_cost 与
  obstacle_height、把 HP 复制进 participant、未知事件 type；
- 交叉校验：04 空间规则数据的每个 source 行号仍指向 02C 的真实锚点。

schema 子集（data/FORMAT.md 第 7 节）表达不了跨字段不变式（同一个 cell_id 唯一、
高度字段与 scale.mode 一致），因此由 `tactical_battlefield.battlefield_invariants`
额外做这些检查——不是第二套格式，只是补 JSON Schema 子集缺的 `uniqueKeys` / 条件必填。
该不变式只有一份实现，位于 `tactical_battlefield.py`（Issue #417 搬入）。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_tactical_schema.py` 运行。
"""
import json
import os
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, TESTS)
sys.path.insert(0, ROOT)

import validate_data  # noqa: E402  (tests/ 已加入 sys.path)
from tactical_battlefield import battlefield_invariants  # noqa: E402  (仓库根已加入 sys.path)

SCHEMA_DIR = os.path.join(ROOT, "data", "schema")
BATTLEFIELD_SCHEMA = "tactical.battlefield.schema.json"
BATTLE_STATE_SCHEMA = "tactical.battle_state.schema.json"
BATTLE_EVENT_SCHEMA = "tactical.battle_event.schema.json"
SPACE_SCHEMA = "system.tactical_space.schema.json"

# 战场形态出处的固定锚点（02C 第二节开头）。
BATTLEFIELD_SOURCE = "docs/system/02c-hex-battlefield.md:29"


def load_schema(name):
    with open(os.path.join(SCHEMA_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ── 跨字段不变式（实现见 tactical_battlefield.py，不在本文件留副本） ──────────

def document_errors(instance, schema_name):
    """schema 错误 + 跨字段不变式（只对 battlefield 生效）。"""
    errors = validate_data.check_schema(instance, load_schema(schema_name), SCHEMA_DIR)
    if schema_name == BATTLEFIELD_SCHEMA:
        errors.extend(battlefield_invariants(instance))
    return errors


def file_errors(instance, schema_name):
    """按线上路径校验：registry 取 schema、kind 命中、source 行号在文件内。"""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sample.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(instance, handle, ensure_ascii=False)
        errors = validate_data.validate_file(path, SCHEMA_DIR, ROOT)
    errors = [error.replace(path + ": ", "") for error in errors]
    if schema_name == BATTLEFIELD_SCHEMA:
        errors.extend(battlefield_invariants(instance))
    return errors


# ── 合法样例 ────────────────────────────────────────────────────────────

def metric_battlefield():
    return {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": BATTLEFIELD_SOURCE,
        "battlefield_id": "bf-threshold-yard-01",
        "world_id": "threshold",
        "site_id": "site-yard-01",
        "atlas_ref": "threshold/region-01",
        "seed": 20261020,
        "generator_version": "1.0.0",
        "geometry": {"type": "hex_axial"},
        "scale": {"mode": "metric", "hex_size_cm": 150},
        "cells": [
            {
                "cell_id": "c-0-0",
                "q": 0,
                "r": 0,
                "surface_id": "ground",
                "elevation_cm": 0,
                "terrain_id": "concrete",
                "terrain_tags": ["open"],
                "movement_cost": 150,
                "blocks_movement": False,
                "blocks_los": False,
                "cover_grade": "light",
                "hazard_refs": [],
                "feature_refs": [],
                "spawnable": True
            },
            {
                "cell_id": "c-1-0",
                "q": 1,
                "r": 0,
                "surface_id": "ground",
                "elevation_cm": 60,
                "terrain_id": "rubble",
                "terrain_tags": ["difficult"],
                "movement_cost": 300,
                "blocks_movement": False,
                "blocks_los": True,
                "obstacle_height": 120,
                "cover_grade": "medium",
                "hazard_refs": [],
                "feature_refs": ["feature-crate-01"],
                "spawnable": False
            }
        ],
        "vertical_links": [
            {
                "link_id": "vl-yard-ramp",
                "from_cell_id": "c-0-0",
                "to_cell_id": "c-1-0",
                "kind": "ramp",
                "movement_cost": 150,
                "bidirectional": True,
                "requirements": []
            }
        ]
    }


def abstract_battlefield():
    return {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": BATTLEFIELD_SOURCE,
        "battlefield_id": "bf-emberfall-crypt-02",
        "world_id": "emberfall",
        "site_id": "site-crypt-02",
        "atlas_ref": "emberfall/region-03",
        "seed": "crypt-02-seed",
        "generator_version": "1.0.0",
        "geometry": {"type": "hex_axial"},
        "scale": {"mode": "abstract", "unit": "step"},
        "cells": [
            {
                "cell_id": "c-0-0",
                "q": 0,
                "r": 0,
                "surface_id": "crypt-floor",
                "elevation_level": 0,
                "terrain_id": "stone",
                "terrain_tags": ["open"],
                "movement_cost": 1,
                "blocks_movement": False,
                "blocks_los": False,
                "spawnable": True
            },
            {
                "cell_id": "c-0-1",
                "q": 0,
                "r": 1,
                "surface_id": "crypt-floor",
                "elevation_level": 1,
                "terrain_id": "stairs",
                "terrain_tags": ["difficult"],
                "movement_cost": 2,
                "blocks_movement": False,
                "blocks_los": False,
                "cover_grade": "heavy",
                "spawnable": False
            }
        ],
        "vertical_links": [
            {
                "link_id": "vl-crypt-stairs",
                "from_cell_id": "c-0-0",
                "to_cell_id": "c-0-1",
                "kind": "stairs",
                "movement_cost": 2,
                "bidirectional": True,
                "requirements": []
            }
        ]
    }


def battle_state():
    return {
        "schema_version": 1,
        "kind": "tactical.battle_state",
        "battle_id": "battle-001",
        "battlefield_id": "bf-threshold-yard-01",
        "round": 1,
        "turn": 1,
        "active_unit_id": "unit-pc-01",
        "state_revision": 3,
        "last_event_sequence": 4,
        "rng_ref": "prism.rng:battle-001:3",
        "participants": [
            {
                "unit_id": "unit-pc-01",
                "cell_id": "c-0-0",
                "facing": 0,
                "footprint": [{"q": 0, "r": 0}],
                "temp_refs": ["combat.ap.spent"]
            }
        ],
        "objects": [
            {
                "object_id": "obj-yard-door",
                "definition_id": "object-threshold-steel-door",
                "cell_id": "c-1-0",
                "state": "closed",
                "durability": 20,
                "movable": False,
                "destructible": True,
                "blocks_movement": True,
                "blocks_los": True,
                "triggered": False
            }
        ]
    }


def battle_event():
    return {
        "schema_version": 1,
        "kind": "tactical.battle_event",
        "sequence": 4,
        "type": "move_committed",
        "actor_id": "unit-pc-01",
        "from_cell_id": "c-0-0",
        "to_cell_id": "c-1-0",
        "rule_engine": "tactics",
        "expected_revision": 3,
        "result": {"summary": "沿坡道进入 c-1-0，消耗 150 cm 移动。"},
        "source_rule_ref": "data/system/tactical_space.json#/movement"
    }


# ── 正例检查 ────────────────────────────────────────────────────────────

def check_metric_battlefield():
    """metric 战场：schema、registry、kind、source 行号全过。"""
    errors = file_errors(metric_battlefield(), BATTLEFIELD_SCHEMA)
    assert not errors, "metric 战场被判失败: %s" % "; ".join(errors)


def check_abstract_battlefield():
    """abstract 战场：格 / 步口径，不得出现厘米高度。"""
    errors = file_errors(abstract_battlefield(), BATTLEFIELD_SCHEMA)
    assert not errors, "abstract 战场被判失败: %s" % "; ".join(errors)


def check_battle_state():
    """battle_state 合法样例通过，且与静态地图分离。"""
    errors = document_errors(battle_state(), BATTLE_STATE_SCHEMA)
    assert not errors, "battle_state 被判失败: %s" % "; ".join(errors)


def check_negative_elevation_supported():
    """负高程正例：地下室 / 洼地合法（#415 §3.1 有符号高度差，02C:47 只要求整数）。"""
    doc = metric_battlefield()
    doc["cells"].append({
        "cell_id": "c-0-1",
        "q": 0,
        "r": 1,
        "surface_id": "basement",
        "elevation_cm": -300,
        "terrain_id": "concrete",
        "terrain_tags": ["enclosed"],
        "movement_cost": 150,
        "blocks_movement": False,
        "blocks_los": True,
        "spawnable": False
    })
    doc["vertical_links"].append({
        "link_id": "vl-yard-stairs-down",
        "from_cell_id": "c-0-0",
        "to_cell_id": "c-0-1",
        "kind": "stairs",
        "movement_cost": 150,
        "bidirectional": True,
        "requirements": []
    })
    errors = document_errors(doc, BATTLEFIELD_SCHEMA)
    assert not errors, "负高程（-300 cm）被判失败: %s" % "; ".join(errors)
    assert doc["cells"][2]["elevation_cm"] == -300, "负高程样例被改写"


def check_battle_event():
    """battle_event 合法样例通过。"""
    errors = document_errors(battle_event(), BATTLE_EVENT_SCHEMA)
    assert not errors, "battle_event 被判失败: %s" % "; ".join(errors)


def check_space_rules_data():
    """data/system/tactical_space.json 通过 validate_file，且与既有掩体档一致。"""
    path = os.path.join(ROOT, "data", "system", "tactical_space.json")
    assert os.path.isfile(path), "缺少 data/system/tactical_space.json"
    errors = validate_data.validate_file(path, SCHEMA_DIR, ROOT)
    assert not errors, "空间规则数据未过校验: %s" % "; ".join(errors)
    space = load_json(path)
    combat = load_json(os.path.join(ROOT, "data", "system", "tactics_combat.json"))
    expected = [tier["id"] for tier in combat["cover"]]
    assert space["cover"]["grades"] == expected, \
        "cover.grades 与 tactics_combat.cover[].id 不一致: %s" % space["cover"]["grades"]
    assert space["vertical_links"]["kinds"] == [
        "stairs", "ramp", "ladder", "lift", "climb", "jump", "fall", "teleport"], \
        "vertical_link.kind 受控枚举与 02C:45 不一致"


# 每个 source 行号必须真的落在 02C 的对应锚点上（防止文档行号漂移后数据静默错位）。
SPACE_ANCHORS = (
    ("docs/system/02c-hex-battlefield.md:5", "M13 六角格战场"),
    ("docs/system/02c-hex-battlefield.md:18", "(1,0)"),
    ("docs/system/02c-hex-battlefield.md:19", "max(|dq|, |dr|, |dq+dr|)"),
    ("docs/system/02c-hex-battlefield.md:33", "hex_axial"),
    ("docs/system/02c-hex-battlefield.md:35", "两种口径"),
    ("docs/system/02c-hex-battlefield.md:37", "150"),
    ("docs/system/02c-hex-battlefield.md:39", "格 / 步单位"),
    ("docs/system/02c-hex-battlefield.md:45", "stairs"),
    ("docs/system/02c-hex-battlefield.md:78", "100"),
    ("docs/system/02c-hex-battlefield.md:84", "使用高地的射手"),
    ("docs/system/02c-hex-battlefield.md:94", "50"),
    ("docs/system/02c-hex-battlefield.md:104", "150"),
    ("docs/system/02c-hex-battlefield.md:105", "1d6"),
    ("docs/system/02c-hex-battlefield.md:138", "light"),
    ("docs/system/02a-tactics-engine.md:93", "高地的射手"),
)


def check_space_sources_hit_anchors():
    """空间规则的每个 source 行号命中真实锚点文本。"""
    problems = []
    for source, marker in SPACE_ANCHORS:
        file_part, _, line_part = source.rpartition(":")
        with open(os.path.join(ROOT, file_part), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        text = lines[int(line_part) - 1] if int(line_part) <= len(lines) else ""
        if marker not in text:
            problems.append("%s 未命中 %r（实为 %r）" % (source, marker, text))
    assert not problems, "锚点漂移: %s" % "; ".join(problems)


# ── 负例 ────────────────────────────────────────────────────────────────

def broken_metric():
    """按 name 返回改坏的战场样例。"""
    cases = {}

    mixed = metric_battlefield()
    mixed["cells"][0]["elevation_level"] = 2
    cases["height-mixing"] = mixed

    missing_id = metric_battlefield()
    del missing_id["cells"][0]["cell_id"]
    cases["missing-cell-id"] = missing_id

    duplicate = metric_battlefield()
    duplicate["cells"][1]["cell_id"] = "c-0-0"
    cases["duplicate-cell-id"] = duplicate

    bad_link = metric_battlefield()
    bad_link["vertical_links"][0]["kind"] = "escalator"
    cases["bad-vertical-link"] = bad_link

    bad_cover = metric_battlefield()
    bad_cover["cells"][0]["cover_grade"] = "partial"
    cases["bad-cover-grade"] = bad_cover

    bad_elevation = metric_battlefield()
    bad_elevation["cells"][0]["elevation_cm"] = "150cm"
    cases["bad-elevation-unit"] = bad_elevation

    fractional_elevation = metric_battlefield()
    fractional_elevation["cells"][0]["elevation_cm"] = 150.5
    cases["bad-elevation-fraction"] = fractional_elevation

    negative_cost = metric_battlefield()
    negative_cost["cells"][0]["movement_cost"] = -150
    cases["negative-movement-cost"] = negative_cost

    negative_height = metric_battlefield()
    negative_height["cells"][0]["obstacle_height"] = -1
    cases["negative-obstacle-height"] = negative_height

    return cases


NEGATIVE_MARKERS = {
    "height-mixing": ("$.cells[0].elevation_level", "混用"),
    "missing-cell-id": ("$.cells[0]", "cell_id"),
    "duplicate-cell-id": ("$.cells[1].cell_id", "重复 cell_id"),
    "bad-vertical-link": ("$.vertical_links[0].kind", "enum"),
    "bad-cover-grade": ("$.cells[0].cover_grade", "enum"),
    "bad-elevation-unit": ("$.cells[0].elevation_cm", "integer"),
    "bad-elevation-fraction": ("$.cells[0].elevation_cm", "integer"),
    "negative-movement-cost": ("$.cells[0].movement_cost", "minimum"),
    "negative-obstacle-height": ("$.cells[0].obstacle_height", "minimum"),
}


def check_negative_battlefield_cases():
    """九类坏战场都被拒绝，且报错能定位到具体字段。"""
    problems = []
    for name, case in sorted(broken_metric().items()):
        errors = document_errors(case, BATTLEFIELD_SCHEMA)
        path, marker = NEGATIVE_MARKERS[name]
        if not errors:
            problems.append("%s: 未被判失败" % name)
        elif not any(path in e and marker in e for e in errors):
            problems.append("%s: 报错未同时含 %r / %r（实为 %s）"
                            % (name, path, marker, errors))
        else:
            print("  负例 %-22s → %s" % (name, errors[0]))
    assert not problems, "; ".join(problems)


def check_battle_state_rejects_copied_vitals():
    """participant 不得复制 HP / 资源（#415 §2.3）：多余键必须被拒。"""
    state = battle_state()
    state["participants"][0]["vitality"] = 12
    errors = document_errors(state, BATTLE_STATE_SCHEMA)
    assert errors, "participant 带 vitality 未被判失败"
    assert any("participants[0]" in e and "vitality" in e for e in errors), \
        "报错未定位到 participants[0].vitality: %s" % errors


def check_battle_event_rejects_unknown_type():
    """事件 type 只接受冻结枚举。"""
    event = battle_event()
    event["type"] = "move_teleported"
    errors = document_errors(event, BATTLE_EVENT_SCHEMA)
    assert errors, "未知事件 type 未被判失败"
    assert any("$.type" in e and "enum" in e for e in errors), \
        "报错未定位到 $.type: %s" % errors


def check_registry_entries():
    """四个新 kind 已登记，且指向存在的 schema 文件。"""
    with open(os.path.join(SCHEMA_DIR, "registry.json"), encoding="utf-8") as handle:
        kinds = json.load(handle)["kinds"]
    expected = {
        "tactical.battlefield": BATTLEFIELD_SCHEMA,
        "tactical.battle_state": BATTLE_STATE_SCHEMA,
        "tactical.battle_event": BATTLE_EVENT_SCHEMA,
        "system.tactical_space": SPACE_SCHEMA,
    }
    for kind, filename in expected.items():
        assert kinds.get(kind) == filename, "registry 未登记 %s → %s" % (kind, filename)
        assert os.path.isfile(os.path.join(SCHEMA_DIR, filename)), \
            "registry 指向不存在的 schema: %s" % filename


def check_new_schemas_keyword_clean():
    """新增四份 schema 只用文档化关键字子集。"""
    bad = []
    for filename in (BATTLEFIELD_SCHEMA, BATTLE_STATE_SCHEMA, BATTLE_EVENT_SCHEMA, SPACE_SCHEMA):
        for where, keyword in validate_data.collect_unsupported(load_schema(filename)):
            bad.append("%s %s: %s" % (filename, where, keyword))
    assert not bad, "新增 schema 使用了未支持的关键字: %s" % "; ".join(bad)


def main():
    checks = (
        check_metric_battlefield,
        check_abstract_battlefield,
        check_negative_elevation_supported,
        check_battle_state,
        check_battle_event,
        check_space_rules_data,
        check_space_sources_hit_anchors,
        check_negative_battlefield_cases,
        check_battle_state_rejects_copied_vitals,
        check_battle_event_rejects_unknown_type,
        check_registry_entries,
        check_new_schemas_keyword_clean,
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
