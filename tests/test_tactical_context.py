#!/usr/bin/env python3
"""TacticalContext 统一空间上下文回归（Issue #422）。

覆盖：
- 冻结字段契约：TacticalContext / SpatialQueryResult 键序与 `CONTEXT_FIELDS` /
  `SPATIAL_FIELDS` 逐项一致（ok / stale / invalid 三种状态形状相同）；
- 两个适配器消费同一份上下文：距离 / LOS / 高度事实相同，且与直接调用
  #444 `query_attack` 的事实一致（单一事实来源，不写第二套几何）；
- 同输入稳定输出：重复构建逐字段相等，JSON 可序列化；
- 口径隔离：metric 只给 `metric_distance`（厘米），abstract 只给
  `abstract_distance`（格 / 步），`height_delta_unit` 显式声明，不混用；
- 可解释性：LOS 被遮给 `blocked_cells` + `line_of_sight_blocked`；
  目的地不可达给 `destination_unreachable` + `movement_blocked`；
- 失败行为：缺 sight 配置 / 缺体型档 → 结构化 `invalid_context`（#444 失败
  原样透传，不静默兜底）；跨 surface → `line_of_sight=None`（无法判定）；
- revision：`expected_revision` 不匹配 → `stale_context`，全部事实字段为空，
  不把新旧状态拼成一个结果；
- 旧场景不强制：battlefield 缺失返回结构化错误（不造新地图）；模块只 import
  `tactical_*` 聚合层，无 `atlas*` / `prism_core` / `notdnd_web` / HTTP；
- 物件视图与 `blocked_objects`（覆盖遮挡格的物件）、地形与特征引用聚合。

零依赖：仅 Python 3 标准库；直接 `python tests/test_tactical_context.py` 运行。
"""
import json
import os
import re
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)

import tactical_battlefield as tb  # noqa: E402
import tactical_context as tc  # noqa: E402
import tactical_queries as tq  # noqa: E402

RULES = tb.space_rules()
HEX = RULES["scale"]["hex_size_cm"]
SOURCE = "docs/system/02c-hex-battlefield.md:29"


# ── 规则注入 ────────────────────────────────────────────────────────────

def rules_with_sight():
    """当前数据尚未落 `sight` → 测试注入五档配置（结构与 02C:261-265 一致）。"""
    rules = json.loads(json.dumps(RULES))
    rules["sight"] = {
        "small":  {"eye_height_cm": 90,  "visible_height_cm": 70},
        "medium": {"eye_height_cm": 160, "visible_height_cm": 120},
        "large":  {"eye_height_cm": 220, "visible_height_cm": 180},
        "huge":   {"eye_height_cm": 320, "visible_height_cm": 280},
        "boss":   {"eye_height_cm": 450, "visible_height_cm": 400},
    }
    return rules


SIGHT_RULES = rules_with_sight()


# ── 样例 ────────────────────────────────────────────────────────────────

def _disk(radius):
    coords = []
    for q in range(-radius, radius + 1):
        low = max(-radius, -q - radius)
        high = min(radius, -q + radius)
        for r in range(low, high + 1):
            coords.append((q, r))
    return sorted(coords)


def make_cell(cell_id, q, r, surface="ground", mode="metric", **overrides):
    cell = {
        "cell_id": cell_id,
        "q": q,
        "r": r,
        "surface_id": surface,
        "terrain_id": "floor",
        "terrain_tags": ["open"],
        "movement_cost": HEX if mode == "metric" else 1,
        "blocks_movement": False,
        "blocks_los": False,
        "hazard_refs": [],
        "feature_refs": [],
        "spawnable": True,
    }
    if mode == "metric":
        cell["elevation_cm"] = 0
    else:
        cell["elevation_level"] = 0
    cell.update(overrides)
    return cell


def make_battlefield(mode="metric", radius=3, surfaces=("ground",), overrides=None):
    """半径 3 的六角盘；返回 (战场 doc, {(surface, q, r): cell_id})。

    `overrides` 按 `(surface, q, r)` 覆盖格字段（如墙、特征引用）。
    cell_id 顺序编号而非由坐标拼出（02C:21）。
    """
    prefixes = {"ground": "g", "upper": "u"}
    cells = []
    ids = {}
    for surface in surfaces:
        prefix = prefixes.get(surface, surface[:1])
        for order, (q, r) in enumerate(_disk(radius)):
            cell_id = "%s%02d" % (prefix, order)
            ids[(surface, q, r)] = cell_id
            cell = make_cell(cell_id, q, r, surface=surface, mode=mode)
            if overrides and (surface, q, r) in overrides:
                cell.update(overrides[(surface, q, r)])
            cells.append(cell)
    doc = {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": SOURCE,
        "battlefield_id": "bf-ctx-01",
        "world_id": "threshold",
        "site_id": "site-ctx-01",
        "atlas_ref": "threshold/region-01",
        "seed": 422,
        "generator_version": "1.0.0",
        "geometry": {"type": "hex_axial"},
        "cells": cells,
        "vertical_links": [],
    }
    if mode == "metric":
        doc["scale"] = {"mode": "metric", "hex_size_cm": HEX}
    else:
        doc["scale"] = {"mode": "abstract", "unit": "cell"}
    return doc, ids


def make_state(revision=7, participants=None, objects=None):
    return {
        "schema_version": 1,
        "kind": "tactical.battle_state",
        "battle_id": "battle-ctx-01",
        "battlefield_id": "bf-ctx-01",
        "round": 1,
        "turn": 1,
        "state_revision": revision,
        "participants": participants if participants is not None else [],
        "objects": objects if objects is not None else [],
    }


def hero_orc_state(ids, revision=7, objects=None, hero_cell=(0, 0),
                   orc_cell=(2, 0)):
    return make_state(
        revision=revision,
        participants=[
            {"unit_id": "hero", "cell_id": ids[("ground",) + hero_cell]},
            {"unit_id": "orc", "cell_id": ids[("ground",) + orc_cell]},
        ],
        objects=objects,
    )


def build_metric_context(bf, ids, state, **kwargs):
    kwargs.setdefault("actor_id", "hero")
    kwargs.setdefault("target_id", "orc")
    kwargs.setdefault("actor_body_size", "medium")
    kwargs.setdefault("target_body_size", "medium")
    kwargs.setdefault("rules", SIGHT_RULES)
    return tc.build_context(bf, state, **kwargs)


# ── 契约：冻结字段 ──────────────────────────────────────────────────────

def check_context_fields_frozen():
    bf, ids = make_battlefield()
    state = hero_orc_state(ids)
    context = build_metric_context(bf, ids, state)
    assert list(context.keys()) == list(tc.CONTEXT_FIELDS), \
        "TacticalContext 键序必须与 CONTEXT_FIELDS 完全一致: %r" % (list(context.keys()),)
    assert context["schema_version"] == tc.CONTEXT_SCHEMA_VERSION == 1
    assert context["kind"] == tc.CONTEXT_KIND == "tactical.context"
    assert context["status"] == "ok"
    assert context["status"] in tc.CONTEXT_STATUSES

    # 三种状态输出形状一致（消费方无需按状态改字段访问）。
    stale = build_metric_context(bf, ids, state, expected_revision=state["state_revision"] - 1)
    assert list(stale.keys()) == list(tc.CONTEXT_FIELDS)
    invalid = tc.build_context(None, state, actor_id="hero")
    assert list(invalid.keys()) == list(tc.CONTEXT_FIELDS)


def check_spatial_fields_frozen():
    bf, ids = make_battlefield()
    result = tc.spatial_query(
        bf, from_cell_id=ids[("ground", 0, 0)], to_cell_id=ids[("ground", 2, 0)],
        actor_id="hero", target_id="orc",
        actor_body_size="medium", target_body_size="medium", rules=SIGHT_RULES)
    assert list(result.keys()) == list(tc.SPATIAL_FIELDS), \
        "SpatialQueryResult 键序必须与 SPATIAL_FIELDS 完全一致: %r" % (list(result.keys()),)
    assert result["schema_version"] == 1
    assert result["kind"] == "tactical.spatial_query_result"
    assert result["status"] == "ok"

    failed = tc.spatial_query(
        bf, from_cell_id=ids[("ground", 0, 0)], to_cell_id=ids[("ground", 2, 0)],
        rules=RULES)  # 缺 sight 配置
    assert list(failed.keys()) == list(tc.SPATIAL_FIELDS)
    assert failed["status"] == "invalid_context"


def check_rule_refs_documented():
    bf, _ids = make_battlefield()
    state = make_state(participants=[{"unit_id": "hero", "cell_id": "g00"}])
    context = tc.build_context(bf, state, actor_id="hero")
    refs = context["rule_refs"]
    assert isinstance(refs, dict) and refs
    pattern = re.compile(r"^docs/system/[a-z0-9-]+\.md:[0-9]+$")
    doc_path = os.path.join(ROOT, "docs", "system", "02c-hex-battlefield.md")
    with open(doc_path, encoding="utf-8") as handle:
        line_count = sum(1 for _ in handle)
    for key, value in refs.items():
        assert pattern.match(value), "rule_ref %s=%s 不是 docs 行引用" % (key, value)
        assert int(value.rsplit(":", 1)[1]) <= line_count, \
            "rule_ref %s=%s 超出文档行数" % (key, value)


# ── 验收 1：两个适配器消费同一份上下文 ────────────────────────────────────

def check_two_adapters_share_one_context():
    bf, ids = make_battlefield()
    state = hero_orc_state(ids)
    context = build_metric_context(bf, ids, state)

    # 模拟两个引擎适配器：各自从同一份上下文取事实，不得各自重算几何。
    def adapter_tactics(ctx):
        return {
            "distance": ctx["metric_distance"],
            "los": ctx["line_of_sight"],
            "height": ctx["signed_height_delta"],
            "hex": ctx["hex_distance"],
            "cover": ctx["cover_grade"],
            "high_ground": ctx["high_ground"],
        }

    def adapter_forge(ctx):
        return {
            "distance": ctx["metric_distance"],
            "los": ctx["line_of_sight"],
            "height": ctx["signed_height_delta"],
            "hex": ctx["hex_distance"],
            "cover": ctx["cover_grade"],
            "high_ground": ctx["high_ground"],
        }

    facts_a = adapter_tactics(context)
    facts_b = adapter_forge(context)
    assert facts_a == facts_b

    # 且与直接调用 #444 内核的事实一致（同一事实来源，02C:172）。
    query = tq.query_attack(
        bf, ids[("ground", 0, 0)], ids[("ground", 2, 0)],
        actor_body_size="medium", target_body_size="medium", rules=SIGHT_RULES)
    assert facts_a["hex"] == query["hex_distance"]
    assert facts_a["distance"] == query["distance"]
    assert facts_a["los"] == query["line_of_sight"]
    assert facts_a["height"] == query["signed_height_delta_cm"]
    assert facts_a["high_ground"] == query["high_ground"]

    # 平地 (0,0) → (2,0)：三维距离 = 2 × 相邻格中心距 = 300 cm。
    assert facts_a["distance"] == 2 * HEX
    assert facts_a["hex"] == 2
    assert facts_a["height"] == 0


# ── 验收 2：同输入稳定 + JSON 可序列化 ──────────────────────────────────

def check_deterministic_and_json():
    bf, ids = make_battlefield()
    state = hero_orc_state(ids, objects=[
        {"object_id": "door-01", "definition_id": "def-door",
         "cell_id": ids[("ground", 1, 0)], "state": "closed",
         "blocks_movement": True, "blocks_los": True},
    ])
    first = build_metric_context(bf, ids, state, movement_budget=HEX * 6)
    json.dumps(first)  # 可序列化
    first_text = json.dumps(first, sort_keys=True, ensure_ascii=False)
    for _ in range(20):
        again = build_metric_context(bf, ids, state, movement_budget=HEX * 6)
        assert again == first, "同输入必须返回稳定结果"
        assert json.dumps(again, sort_keys=True, ensure_ascii=False) == first_text
    spatial = tc.spatial_query(
        bf, from_cell_id=ids[("ground", 0, 0)], to_cell_id=ids[("ground", 2, 0)],
        actor_body_size="medium", target_body_size="medium", rules=SIGHT_RULES)
    json.dumps(spatial)


# ── 验收 3：metric / abstract 不混用 ─────────────────────────────────────

def check_metric_abstract_not_mixed():
    bf, ids = make_battlefield(mode="metric")
    state = hero_orc_state(ids)
    context = build_metric_context(bf, ids, state)
    assert context["scale_mode"] == "metric"
    assert context["metric_distance"] == 2 * HEX
    assert context["abstract_distance"] is None
    assert context["distance_unit"] == "cm"
    assert context["signed_height_delta"] == 0
    assert context["height_delta_unit"] == "cm"

    abf, aids = make_battlefield(mode="abstract")
    astate = hero_orc_state(aids)
    acontext = build_metric_context(
        abf, aids, astate, distance_unit="cell")
    assert acontext["scale_mode"] == "abstract"
    assert acontext["metric_distance"] is None
    assert acontext["abstract_distance"] == 2
    assert acontext["distance_unit"] == "cell"
    assert acontext["signed_height_delta"] == 0
    assert acontext["height_delta_unit"] == "level"

    # abstract 必须声明格 / 步口径（#444 既有行为透传 → 结构化失败）。
    missing = build_metric_context(abf, aids, astate)
    assert missing["status"] == "invalid_context"
    assert missing["reasons"][0]["code"] == "query_failed"


# ── 验收 4：遮挡 / 不可达可解释 ─────────────────────────────────────────

def check_blocked_los_explained():
    wall = ("ground", 1, 0)
    bf, ids = make_battlefield(overrides={wall: {"blocks_los": True}})
    state = hero_orc_state(ids)
    context = build_metric_context(bf, ids, state)
    assert context["status"] == "ok"
    assert context["line_of_sight"] is False
    assert context["blocked_cells"] == [ids[wall]]
    codes = [reason["code"] for reason in context["reasons"]]
    assert "line_of_sight_blocked" in codes
    assert context["hex_distance"] == 2  # 事实仍完整：被遮不等于数据缺失


def check_unreachable_explained():
    bf, ids = make_battlefield()
    state = hero_orc_state(ids)
    far = ids[("ground", 0, 2)]  # 与起点相距 2 格的空格（目标格被 orc 实心占用，02C:65）
    ok = build_metric_context(
        bf, ids, state, movement_budget=HEX * 6, movement_destination=far)
    assert ok["path_cost"] == 2 * HEX
    assert len(ok["path"]) == 3
    assert ok["reachable"][0]["cell_id"] == ids[("ground", 0, 0)]
    assert ok["reachable"][0]["cost"] == 0

    broke = build_metric_context(
        bf, ids, state, movement_budget=HEX, movement_destination=far)
    assert broke["path_cost"] is None
    assert broke["path"] == []
    codes = [reason["code"] for reason in broke["reasons"]]
    assert "destination_unreachable" in codes
    details = [reason for reason in broke["reasons"]
               if reason["code"] == "destination_unreachable"][0]["details"]
    assert details and details[0]["code"] == "over_budget"
    assert broke["reachable"][0]["cell_id"] == ids[("ground", 0, 0)]


# ── 验收 5：无视线 / 跨 surface / 过时 revision ─────────────────────────

def check_cross_surface_los_undefined():
    bf, ids = make_battlefield(surfaces=("ground", "upper"))
    state = make_state(participants=[
        {"unit_id": "hero", "cell_id": ids[("ground", 0, 0)]},
        {"unit_id": "orc", "cell_id": ids[("upper", 0, 0)]},
    ])
    context = build_metric_context(bf, ids, state)
    assert context["status"] == "ok"
    assert context["line_of_sight"] is None  # 无法判定，不当作通畅
    codes = [reason["code"] for reason in context["reasons"]]
    assert "line_of_sight_undefined" in codes


def check_missing_sight_fails_structured():
    bf, ids = make_battlefield()
    state = hero_orc_state(ids)
    # 数据缺 sight 配置 → #444 显式失败，透传为结构化结果，不静默取 0。
    context = build_metric_context(bf, ids, state, rules=RULES)
    assert context["status"] == "invalid_context"
    reason = context["reasons"][0]
    assert reason["code"] == "query_failed"
    assert "sight" in reason["message"]
    assert context["line_of_sight"] is None
    assert context["hex_distance"] is None
    assert context["metric_distance"] is None

    # 缺体型档同样失败（metric 视线查询必传）。
    no_size = build_metric_context(bf, ids, state, target_body_size=None)
    assert no_size["status"] == "invalid_context"
    assert "actor_body_size" in no_size["reasons"][0]["message"] or \
        "必传参数" in no_size["reasons"][0]["message"]


def check_stale_revision_discards_everything():
    bf, ids = make_battlefield()
    state = hero_orc_state(ids, revision=7)
    context = build_metric_context(bf, ids, state, expected_revision=6)
    assert context["status"] == "stale_context"
    assert context["state_revision"] == 7
    reason = context["reasons"][0]
    assert reason["code"] == "stale_context"
    assert reason["expected_revision"] == 6
    # 不把新旧状态拼成一个结果：全部事实字段为空。
    for field in ("hex_distance", "metric_distance", "signed_height_delta",
                  "line_of_sight", "cover_grade", "path_cost"):
        assert context[field] is None, "stale_context 不得携带事实字段 %s" % field
    for field in ("blocked_cells", "reachable", "path", "objects",
                  "blocked_objects", "active_feature_refs"):
        assert context[field] == [], "stale_context 不得携带聚合视图 %s" % field

    # revision 匹配 / 放弃检查（None）都正常产出。
    ok = build_metric_context(bf, ids, state, expected_revision=7)
    assert ok["status"] == "ok"
    skipped = build_metric_context(bf, ids, state, expected_revision=None)
    assert skipped["status"] == "ok"


# ── 验收 6：旧场景不强制造图 + 模块边界 ─────────────────────────────────

def check_no_battlefield_structured_and_no_map_forcing():
    bf, ids = make_battlefield()
    state = hero_orc_state(ids)
    # battlefield 缺失：返回结构化错误（提示走旧 ATLAS 抽象查询），不造新地图。
    context = tc.build_context(None, state, actor_id="hero")
    assert context["status"] == "invalid_context"
    reason = context["reasons"][0]
    assert reason["code"] == "invalid_battlefield"
    assert "ATLAS" in reason["message"]

    # 模块只 import 聚合层兄弟模块；没有 atlas / prism_core / notdnd_web / HTTP。
    with open(os.path.join(ROOT, "tactical_context.py"), encoding="utf-8") as handle:
        source = handle.read()
    import_lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            import_lines.append(stripped.split(" as ")[0].split()[1])
        elif stripped.startswith("from "):
            import_lines.append(stripped.split()[1])
    allowed = {"tactical_battlefield", "tactical_movement",
               "tactical_objects", "tactical_queries"}
    assert all(line in allowed for line in import_lines), \
        "tactical_context 只能 import tactical_* 聚合层: %r" % (import_lines,)
    for banned in ("import atlas", "import prism_core", "import notdnd_web",
                   "http.server", "fixture_battlefield"):
        assert banned not in source, "tactical_context 不得出现 %r" % banned


# ── 结构化错误：未知参与者 / 格 ──────────────────────────────────────────

def check_unknown_actor_target_cell():
    bf, ids = make_battlefield()
    state = hero_orc_state(ids)
    missing_actor = tc.build_context(bf, state, actor_id="ghost")
    assert missing_actor["status"] == "invalid_context"
    assert missing_actor["reasons"][0]["code"] == "unknown_actor"

    missing_target = build_metric_context(bf, ids, state, target_id="ghost")
    assert missing_target["status"] == "invalid_context"
    assert missing_target["reasons"][0]["code"] == "unknown_target"

    off_map = make_state(participants=[
        {"unit_id": "hero", "cell_id": "cell-nowhere"},
    ])
    bad_cell = tc.build_context(bf, off_map, actor_id="hero")
    assert bad_cell["status"] == "invalid_context"
    assert bad_cell["reasons"][0]["code"] == "unknown_cell"


# ── 物件视图 / blocked_objects / 地形与特征引用 ──────────────────────────

def check_objects_view_and_blocked_objects():
    wall = ("ground", 1, 0)
    door = {"object_id": "door-01", "definition_id": "def-door",
            "cell_id": None, "state": "closed",
            "blocks_movement": True, "blocks_los": True, "triggered": False}
    bf, ids = make_battlefield(overrides={wall: {"blocks_los": True}})
    door["cell_id"] = ids[wall]
    state = hero_orc_state(ids, objects=[door])
    context = build_metric_context(bf, ids, state)
    assert len(context["objects"]) == 1
    view = context["objects"][0]
    assert view["object_id"] == "door-01"
    assert view["definition_id"] == "def-door"
    assert view["state"] == "closed"
    assert view["blocks_los"] is True
    assert context["blocked_objects"] == [{
        "object_id": "door-01",
        "cell_ids": [ids[wall]],
        "reason": "covers_blocked_cell",
    }]

    # 无遮挡时不产出 blocked_objects。
    plain_bf, plain_ids = make_battlefield()
    plain = build_metric_context(plain_bf, plain_ids, hero_orc_state(plain_ids))
    assert plain["blocked_objects"] == []
    assert plain["objects"] == []


def check_terrain_tags_and_feature_refs():
    actor = ("ground", 0, 0)
    target = ("ground", 2, 0)
    wall = ("ground", 1, 0)
    bf, ids = make_battlefield(overrides={
        actor: {"terrain_tags": ["rubble", "open"],
                "feature_refs": ["feat-b", "feat-a"]},
        target: {"feature_refs": ["feat-c"]},
        wall: {"blocks_los": True, "feature_refs": ["feat-a"]},
    })
    state = hero_orc_state(ids)
    context = build_metric_context(bf, ids, state)
    assert context["terrain_tags"] == ["rubble", "open"]
    # 起点 / 终点 / 遮挡格的并集，排序去重。
    assert context["active_feature_refs"] == ["feat-a", "feat-b", "feat-c"]


# ── 无 target 的上下文（只聚合移动视图） ────────────────────────────────

def check_no_target_context():
    bf, ids = make_battlefield()
    state = hero_orc_state(ids)
    context = build_metric_context(bf, ids, state, target_id=None,
                                   movement_budget=HEX * 6)
    assert context["status"] == "ok"
    assert context["target_id"] is None
    assert context["to_cell_id"] is None
    assert context["hex_distance"] is None
    assert context["line_of_sight"] is None
    assert context["reasons"] == []
    assert len(context["reachable"]) > 1
    assert context["path"] == []
    assert context["path_cost"] is None


# ── 入口 ────────────────────────────────────────────────────────────────

def main():
    checks = (
        check_context_fields_frozen,
        check_spatial_fields_frozen,
        check_rule_refs_documented,
        check_two_adapters_share_one_context,
        check_deterministic_and_json,
        check_metric_abstract_not_mixed,
        check_blocked_los_explained,
        check_unreachable_explained,
        check_cross_surface_los_undefined,
        check_missing_sight_fails_structured,
        check_stale_revision_discards_everything,
        check_no_battlefield_structured_and_no_map_forcing,
        check_unknown_actor_target_cell,
        check_objects_view_and_blocked_objects,
        check_terrain_tags_and_feature_refs,
        check_no_target_context,
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
