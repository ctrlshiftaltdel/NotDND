#!/usr/bin/env python3
"""空间攻击查询回归（Issue #420）。

覆盖：
- 无遮挡：metric 三维距离（格中心水平分量 + 高度差）、米制换算、六角距离、LOS=true；
- 墙后完全遮断：`line_of_sight=false`、给出阻挡 cell_id、`executable=false`（不是低命中加值）；
- 部分掩体三档 `light` / `medium` / `heavy`：`cover_grade` 与来源格独立返回，不加命中；
- 多层高度：高差符号、`high_ground` 阈值（含规则数据注入）、高地不直接给骰；
- 边界射线并列：复用 02C:132 的 `A(0,0) → B(2,-1)`，断言过格 `(1,-1)` 并据此判遮断；
- 相同 `q/r` 不同 `surface_id`：以 cell_id 定位；跨 surface 的 LOS 报「无法判定」而非通畅；
- metric 与 abstract 各一组：口径不互换、abstract 必传格 / 步、跨口径换算不存在；
- 视线高度（02C 十节）：眼高 / 目标可见高度只从 `system.tactical_space.sight`
  按体型五档派生；**缺 sight 配置 → 明确失败**（02C:267），不静默取 0；
  abstract 不读 sight、LOS 只看 `blocks_los`（02C:271）；
- 朝向（02C 九节）：六值枚举、缺省 `n`；尖顶类 / 平顶类基准式等价性
  （02C:246-249 锚点：两类下相邻格中心距均 = S）；
- 确定性：同一调用 50 次结果逐字节相同；
- 模块只走 `tactical_hex` 内核、不 import 禁改模块、掩体档名与 `tactics_combat.json` 同源。

零依赖：仅 Python 3 标准库；直接 `python tests/test_tactical_queries.py` 运行。
"""
import json
import math
import os
import re
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)

import tactical_battlefield as tb  # noqa: E402
import tactical_hex  # noqa: E402
import tactical_queries as tq  # noqa: E402

RULES = tb.space_rules()
HEX = RULES["scale"]["hex_size_cm"]
GRADES = RULES["cover"]["grades"]
SOURCE = "docs/system/02c-hex-battlefield.md:29"
QUERY_MODULE = os.path.join(ROOT, "tactical_queries.py")
COMBAT_RULES = os.path.join(ROOT, "data", "system", "tactics_combat.json")

EYE = 160    # sight.medium.eye_height_cm（测试注入值，非规则阈值）
VISIBLE = 120  # sight.medium.visible_height_cm


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


DEFAULT_RULES = rules_with_sight()


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


def battlefield(*, mode="metric", hex_size_cm=HEX, radius=3,
                surfaces=("ground",), unit="cell"):
    """半径 3 的六角盘（37 格 / 层），id 顺序编号而非由坐标拼出（02C:21）。"""
    cells = []
    for floor, surface in enumerate(surfaces):
        prefix = "g" if surface == "ground" else "u"
        for order, (q, r) in enumerate(_disk(radius)):
            cells.append(make_cell("%s%02d" % (prefix, order), q, r,
                                   surface=surface, mode=mode))
    doc = {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": SOURCE,
        "battlefield_id": "bf-query-01",
        "world_id": "threshold",
        "site_id": "site-query-01",
        "atlas_ref": "threshold/region-01",
        "seed": 420,
        "generator_version": "1.0.0",
        "geometry": {"type": "hex_axial"},
        "cells": cells,
        "vertical_links": [],
    }
    if mode == "metric":
        doc["scale"] = {"mode": "metric", "hex_size_cm": hex_size_cm}
    else:
        doc["scale"] = {"mode": "abstract", "unit": unit}
    assert tb.validate_battlefield(doc) == [], tb.validate_battlefield(doc)
    return doc


def cell_id_at(doc, q, r, surface="ground"):
    cell = tb.cell_at(doc, q, r, surface)
    assert cell is not None, "样例战场缺少 (%d, %d) @ %s" % (q, r, surface)
    return cell["cell_id"]


def patch(doc, cell_id, **overrides):
    cell = tb.cell_by_id(doc, cell_id)
    assert cell is not None, "未知 cell_id: %r" % (cell_id,)
    cell.update(overrides)
    return cell


def raise_at(doc, cell_id, elevation_cm):
    patch(doc, cell_id, elevation_cm=elevation_cm)


def ask(doc, actor, target, **overrides):
    kwargs = {"actor_body_size": "medium", "target_body_size": "medium"}
    kwargs.update(overrides)
    if "rules" not in kwargs:
        kwargs["rules"] = DEFAULT_RULES
    return tq.query_attack(doc, actor, target, **kwargs)


# ── 无遮挡 / 距离 ───────────────────────────────────────────────────────

def check_unobstructed_metric():
    """无遮挡：LOS=true、三维距离 = hypot(水平, 高差)、单位厘米且给米制。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    raise_at(doc, target, 200)
    result = ask(doc, actor, target)

    assert result["scale_mode"] == "metric"
    assert result["hex_distance"] == 2, result["hex_distance"]
    assert result["line_of_sight"] is True
    assert result["blocked_cells"] == []
    assert result["executable"] is True
    assert result["failure_reasons"] == []

    # 水平分量：axial 距离 2 的中心间距 = hex_size * sqrt(3)（格中心几何，非格数 × 边长）
    expected_horizontal = HEX * math.sqrt(3)
    assert abs(result["horizontal_distance_cm"] - expected_horizontal) < 1e-6, \
        "水平距离 %r != %r" % (result["horizontal_distance_cm"], expected_horizontal)
    assert result["signed_height_delta_cm"] == 200
    assert result["vertical_distance_cm"] == 200
    expected_total = math.hypot(expected_horizontal, 200)
    assert abs(result["distance"] - expected_total) < 1e-6, \
        "%r != %r" % (result["distance"], expected_total)
    assert result["distance_unit"] == "cm"
    assert result["distance_basis"] == "metric_3d"
    assert abs(result["distance_m"] - expected_total / 100) < 1e-6
    assert result["eye_from_cm"] == 0 + EYE
    assert result["visible_at_cm"] == 200 + VISIBLE


def check_hex_size_comes_from_battlefield():
    """`hex_size_cm` 读战场 `scale`，不读模块常量。"""
    doc = battlefield(hex_size_cm=200)
    result = ask(doc, cell_id_at(doc, 0, 0), cell_id_at(doc, 2, -1))
    assert abs(result["horizontal_distance_cm"] - 200 * math.sqrt(3)) < 1e-6, \
        result["horizontal_distance_cm"]


def check_orientation_default_and_enum():
    """缺省 `n`（02C:232）；六值枚举（02C:222）；非法值拒绝；结果回显朝向。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    default = ask(doc, actor, target)
    assert default["orientation"] == "n", default["orientation"]
    assert default == ask(doc, actor, target, orientation="n"), "缺省应等价 n"

    for orientation in ("ne", "se", "s", "sw", "nw"):
        result = ask(doc, actor, target, orientation=orientation)
        assert result["orientation"] == orientation, orientation
        # 朝向不影响六角距离与 hex-line（02C:251）
        assert result["hex_distance"] == default["hex_distance"]
        assert result["line_of_sight"] == default["line_of_sight"]

    for bad in ("diagonal", "N", "pointy", 0, False):
        try:
            ask(doc, actor, target, orientation=bad)
        except ValueError as error:
            assert "orientation" in str(error), str(error)
        else:
            raise AssertionError("非法 orientation %r 未被拒绝" % (bad,))


def check_orientation_classes_share_point_set():
    """同类内格点集合重合（02C:230）：n 与 s、ne 与 sw 各自距离一致。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    north = ask(doc, actor, target, orientation="n")
    south = ask(doc, actor, target, orientation="s")
    assert abs(north["distance"] - south["distance"]) < 1e-6
    ne = ask(doc, actor, target, orientation="ne")
    sw = ask(doc, actor, target, orientation="sw")
    assert abs(ne["distance"] - sw["distance"]) < 1e-6


def check_center_formula_anchors():
    """02C:246-249 验证锚点：两类基准式下相邻格中心距均 = S。"""
    sqrt3 = math.sqrt(3.0)
    # 尖顶类（n / s）：(1,0) → (S, 0)；(0,1) → (S/2, S√3/2)
    x, y = tq._center_cm({"q": 1, "r": 0}, HEX, "n")
    assert abs(x - HEX) < 1e-9 and abs(y) < 1e-9, (x, y)
    x, y = tq._center_cm({"q": 0, "r": 1}, HEX, "n")
    assert abs(x - HEX / 2) < 1e-9 and abs(y - HEX * sqrt3 / 2) < 1e-9, (x, y)
    # 平顶类（ne / nw / se / sw）：(1,0) → (S√3/2, S/2)；(0,1) → (0, S)
    x, y = tq._center_cm({"q": 1, "r": 0}, HEX, "ne")
    assert abs(x - HEX * sqrt3 / 2) < 1e-9 and abs(y - HEX / 2) < 1e-9, (x, y)
    x, y = tq._center_cm({"q": 0, "r": 1}, HEX, "ne")
    assert abs(x) < 1e-9 and abs(y - HEX) < 1e-9, (x, y)

    # 全部六个邻格、两类基准式：中心距一律 = S（02C:246）
    for orientation in tq.ORIENTATIONS:
        for dq, dr in tactical_hex.NEIGHBOR_OFFSETS:
            x0, y0 = tq._center_cm({"q": 0, "r": 0}, HEX, orientation)
            x1, y1 = tq._center_cm({"q": dq, "r": dr}, HEX, orientation)
            gap = math.hypot(x1 - x0, y1 - y0)
            assert abs(gap - HEX) < 1e-6, (orientation, (dq, dr), gap)

    # 端到端：相邻格查询的水平距离 = S，与朝向无关
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 1, 0)
    for orientation in tq.ORIENTATIONS:
        result = ask(doc, actor, target, orientation=orientation)
        assert abs(result["horizontal_distance_cm"] - HEX) < 1e-6, \
            (orientation, result["horizontal_distance_cm"])


# ── 完全遮断 ────────────────────────────────────────────────────────────

def check_wall_blocks_line_of_sight():
    """墙后：LOS=false、给出阻挡格、攻击不可执行，且不产生任何命中修正。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    wall = cell_id_at(doc, 1, -1)
    patch(doc, wall, blocks_los=True, blocks_movement=True)

    result = ask(doc, actor, target)
    assert result["line_of_sight"] is False, "墙后仍判通畅"
    assert result["blocked_cells"] == [wall], result["blocked_cells"]
    assert result["blocked_reasons"][0]["code"] == "blocks_los"
    assert result["blocked_reasons"][0]["cell_id"] == wall
    assert result["executable"] is False, "完全遮断仍被报为可执行"
    codes = [item["code"] for item in result["failure_reasons"]]
    assert codes == ["line_of_sight_blocked"], codes
    assert result["failure_reasons"][0]["cell_id"] == wall

    # 只报事实：不出现命中 / 伤害 / 加值一类字段（02C:146）
    forbidden = ("bonus", "attack_bonus", "guard_bonus", "hit", "damage", "dice")
    hits = [key for key in result if any(token in key for token in forbidden)]
    assert not hits, "查询结果混入规则效果字段: %s" % hits


def check_obstacle_height_blocks_ray():
    """障碍高度独立判定：`blocks_los=false` 但顶面高于射线 → 遮断并给出高度。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    wall = cell_id_at(doc, 1, -1)
    patch(doc, wall, obstacle_height=400)  # 射线在该格约 240 cm，顶面 400 cm

    result = ask(doc, actor, target)
    assert result["line_of_sight"] is False
    assert result["blocked_reasons"][0]["code"] == "obstacle_height"
    obstacle = result["obstacles"][0]
    assert obstacle["cell_id"] == wall
    assert obstacle["obstacle_top_cm"] == 400
    assert obstacle["blocks_ray"] is True
    assert 0 < obstacle["ray_height_cm"] < obstacle["obstacle_top_cm"]
    assert result["executable"] is False

    # 抬高视线（注入更高的 sight 档位）后同一障碍不再遮断
    tall_rules = rules_with_sight()
    tall_rules["sight"]["medium"] = {"eye_height_cm": 600, "visible_height_cm": 600}
    tall = ask(doc, actor, target, rules=tall_rules)
    assert tall["line_of_sight"] is True, tall["blocked_reasons"]
    assert tall["obstacles"][0]["blocks_ray"] is False


def check_blocked_attack_is_not_a_penalty():
    """完全遮断报告为不可执行，而不是「低命中」：掩体档也不因阻挡被折算。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    wall = cell_id_at(doc, 1, -1)
    patch(doc, wall, blocks_los=True, cover_grade="heavy")

    result = ask(doc, actor, target)
    assert result["executable"] is False
    assert result["cover_grade"] is None, "阻挡格被当成了掩体来源"
    assert result["cover_candidates"] == []
    assert result["line_of_sight"] is False


# ── 部分掩体 ────────────────────────────────────────────────────────────

def check_partial_cover_three_grades():
    """`light` / `medium` / `heavy`：掩体档与来源格独立返回，且保持可执行。"""
    assert set(GRADES) >= {"light", "medium", "heavy", "full"}
    for grade in ("light", "medium", "heavy"):
        doc = battlefield()
        actor = cell_id_at(doc, 0, 0)
        target = cell_id_at(doc, 2, -1)
        cover_cell = cell_id_at(doc, 1, -1)
        patch(doc, cover_cell, cover_grade=grade)

        result = ask(doc, actor, target)
        assert result["line_of_sight"] is True, grade
        assert result["executable"] is True, grade
        assert result["cover_grade"] == grade, (grade, result["cover_grade"])
        assert result["cover_source_cell_id"] == cover_cell
        assert result["blocked_cells"] == []
        # 掩体不折算成加值：结果里没有数字化的命中修正
        assert not any("bonus" in key for key in result)


def check_cover_takes_highest_grade_nearest_target():
    """多处掩体：取最高档；同档取更靠近目标的一格（确定性裁决）。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 3, 0)
    first = cell_id_at(doc, 1, 0)
    second = cell_id_at(doc, 2, 0)
    patch(doc, first, cover_grade="heavy")
    patch(doc, second, cover_grade="light")

    result = ask(doc, actor, target)
    assert result["cover_grade"] == "heavy", result["cover_candidates"]
    assert result["cover_source_cell_id"] == first
    assert [item["cell_id"] for item in result["cover_candidates"]] == [second, first]

    patch(doc, second, cover_grade="heavy")
    tied = ask(doc, actor, target)
    assert tied["cover_source_cell_id"] == second, "同档未取靠近目标的一格"


def check_target_cell_cover_and_full():
    """目标所在格的掩体也计入；`full` → 不可执行（数据自身的限制，不是加值）。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    patch(doc, target, cover_grade="medium")
    result = ask(doc, actor, target)
    assert result["cover_grade"] == "medium"
    assert result["cover_source_cell_id"] == target
    assert result["executable"] is True

    patch(doc, target, cover_grade="full")
    full = ask(doc, actor, target)
    assert full["cover_grade"] == "full"
    assert full["executable"] is False
    assert [item["code"] for item in full["failure_reasons"]] == ["cover_full"]


def check_cover_grades_match_combat_rules():
    """档名与 `data/system/tactics_combat.json` 的 cover[].id 完全一致（不另造档位）。"""
    with open(COMBAT_RULES, encoding="utf-8") as handle:
        combat = json.load(handle)
    ids = [item["id"] for item in combat["cover"]]
    assert ids == list(GRADES), "掩体档名漂移: %r vs %r" % (ids, GRADES)


# ── 多层高度 / 高地 ─────────────────────────────────────────────────────

def check_multi_level_height_and_high_ground():
    """多层高度：高差有符号；达到阈值且 LOS 有效才标 `high_ground`。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    raise_at(doc, actor, 250)
    raise_at(doc, target, 100)

    above = ask(doc, actor, target)
    assert above["signed_height_delta_cm"] == -150
    assert above["vertical_distance_cm"] == 150
    assert above["high_ground"] is True
    assert above["high_ground_threshold"] == RULES["high_ground"]["threshold_cm"]
    assert above["high_ground_threshold_unit"] == "cm"
    assert above["high_ground_applies_to"] == RULES["high_ground"]["applies_to"]

    below = ask(doc, target, actor)
    assert below["signed_height_delta_cm"] == 150
    assert below["high_ground"] is False, "低于目标却被标为高地"

    # 差一点点不到阈值 → 不是高地
    raise_at(doc, target, 160)
    short = ask(doc, actor, target)
    assert short["signed_height_delta_cm"] == -90, short["signed_height_delta_cm"]
    assert short["high_ground"] is False, short["high_ground_threshold"]

    # 高地不因高度差本身追加骰值或伤害字段
    assert not any("dice" in key or "damage" in key for key in above)


def check_high_ground_threshold_is_data_driven():
    """阈值来自 `system.tactical_space`：注入不同阈值后标签随之变化。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    raise_at(doc, actor, 400)
    assert ask(doc, actor, target)["high_ground"] is True

    custom = rules_with_sight()
    custom["high_ground"] = dict(custom["high_ground"], threshold_cm=500)
    result = ask(doc, actor, target, rules=custom)
    assert result["high_ground"] is False, "阈值未从规则数据读取"
    assert result["high_ground_threshold"] == 500


def check_high_ground_requires_line_of_sight():
    """视线被完全遮断时不给高地标签（02C:82 要求视线有效）。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    raise_at(doc, actor, 400)
    patch(doc, cell_id_at(doc, 1, -1), blocks_los=True)
    result = ask(doc, actor, target)
    assert result["line_of_sight"] is False
    assert result["high_ground"] is False


# ── 边界射线并列 ────────────────────────────────────────────────────────

def check_boundary_tie_break_uses_documented_cell():
    """02C:132 的并列例子：`(0,0) → (2,-1)` 的过格必须是 `(1,-1)`。"""
    line = tactical_hex.hex_line((0, 0), (2, -1))
    assert line == ((0, 0), (1, -1), (2, -1)), line

    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    picked = cell_id_at(doc, 1, -1)
    other = cell_id_at(doc, 1, 0)
    patch(doc, picked, blocks_los=True)

    result = ask(doc, actor, target)
    assert result["blocked_cells"] == [picked], result["blocked_cells"]
    assert other not in result["blocked_cells"]

    # 另一候选格不影响任何结果：并列只裁决「检查哪一格」
    clean = battlefield()
    patch(clean, cell_id_at(clean, 1, 0), blocks_los=True)
    assert ask(clean, cell_id_at(clean, 0, 0), cell_id_at(clean, 2, -1))[
        "line_of_sight"] is True


def check_boundary_tie_break_reverse():
    """反向射线仍选中同一格：`(2,-1) → (0,0)` 的过格也是 `(1,-1)`。"""
    line = tactical_hex.hex_line((2, -1), (0, 0))
    assert line == ((2, -1), (1, -1), (0, 0)), line


# ── 相同 q/r 不同 surface ───────────────────────────────────────────────

def check_same_qr_different_surface():
    """以 cell_id 为准：同 q/r 的两层格互不串味；跨 surface 的 LOS 不可推断。"""
    doc = battlefield(surfaces=("ground", "upper"))
    ground = cell_id_at(doc, 0, 0, "ground")
    upper = cell_id_at(doc, 0, 0, "upper")
    assert ground != upper
    assert tb.cell_by_id(doc, ground)["surface_id"] == "ground"
    assert tb.cell_by_id(doc, upper)["surface_id"] == "upper"

    # 同 q/r 相邻层：几何距离为 0，但视线不能由 q/r 推断（02C:21）
    result = ask(doc, ground, upper)
    assert result["hex_distance"] == 0
    assert result["line_of_sight"] is None, "跨 surface 的视线被擅自判为通畅"
    assert result["executable"] is False
    assert [item["code"] for item in result["failure_reasons"]] == [
        "line_of_sight_undefined"]
    assert result["unresolved_cells"] == []

    # 同层仍然正常判定：视线只查 actor 所在 surface 的格
    upper_target = cell_id_at(doc, 2, -1, "upper")
    patrol = ask(doc, upper, upper_target)
    assert patrol["from_surface_id"] == "upper"
    assert patrol["line_of_sight"] is True


# ── metric / abstract ───────────────────────────────────────────────────

def check_metric_and_abstract_are_separate():
    """metric 给厘米 + 米；abstract 只给格 / 步，且字段不互相伪装。"""
    metric = battlefield()
    metric_result = ask(metric, cell_id_at(metric, 0, 0), cell_id_at(metric, 2, -1))
    assert metric_result["distance_unit"] == "cm"
    assert metric_result["distance_m"] is not None
    assert metric_result["signed_level_delta"] is None

    abstract = battlefield(mode="abstract")
    abstract_result = ask(abstract, cell_id_at(abstract, 0, 0),
                          cell_id_at(abstract, 2, -1), distance_unit="cell")
    assert abstract_result["scale_mode"] == "abstract"
    assert abstract_result["distance"] == 2
    assert abstract_result["distance_unit"] == "cell"
    assert abstract_result["distance_basis"] == "hex"
    assert abstract_result["distance_m"] is None
    assert abstract_result["horizontal_distance_cm"] is None
    assert abstract_result["vertical_distance_cm"] is None
    assert abstract_result["signed_height_delta_cm"] is None

    step = ask(abstract, cell_id_at(abstract, 0, 0),
               cell_id_at(abstract, 2, -1), distance_unit="step")
    assert step["distance_unit"] == "step"


def check_abstract_levels_and_high_ground():
    """abstract：高差按 uplift 层级，空地阈值用 `threshold_level`。"""
    doc = battlefield(mode="abstract")
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    patch(doc, actor, elevation_level=2)
    result = ask(doc, actor, target, distance_unit="cell")
    assert result["signed_level_delta"] == -2
    assert result["high_ground"] is True
    assert result["high_ground_threshold"] == RULES["high_ground"]["threshold_level"]
    assert result["high_ground_threshold_unit"] == "level"


def check_scale_declaration_is_enforced():
    """abstract 必须声明格 / 步口径；metric 不得混用该口径。"""
    abstract = battlefield(mode="abstract")
    actor = cell_id_at(abstract, 0, 0)
    target = cell_id_at(abstract, 2, -1)
    for bad in (None, "meter", "hex"):
        try:
            ask(abstract, actor, target, distance_unit=bad)
        except ValueError as error:
            assert "distance_unit" in str(error) or "格 / 步" in str(error), str(error)
        else:
            raise AssertionError("abstract 战场接受了非法口径 %r" % (bad,))

    metric = battlefield()
    try:
        ask(metric, cell_id_at(metric, 0, 0), cell_id_at(metric, 2, -1),
            distance_unit="cell")
    except ValueError as error:
        assert "metric" in str(error), str(error)
    else:
        raise AssertionError("metric 战场接受了格 / 步口径")


# ── sight 配置（02C 第十节） ────────────────────────────────────────────

def check_missing_sight_fails_explicitly():
    """缺 `sight` 配置 → metric 视线查询明确失败（02C:267），不静默取 0。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)

    # 当前仓库数据本身就没有 sight → 直接走真实 space_rules()
    try:
        tq.query_attack(doc, actor, target, actor_body_size="medium",
                        target_body_size="medium")
    except ValueError as error:
        message = str(error)
        assert "sight" in message, message
        assert "缺少" in message, message
    else:
        raise AssertionError("缺 sight 配置未被拒绝（静默取了默认值？）")

    # sight 为空 dict / 非法结构同样失败
    for broken in ({}, [], "nope"):
        rules = json.loads(json.dumps(RULES))
        rules["sight"] = broken
        try:
            ask(doc, actor, target, rules=rules)
        except ValueError as error:
            assert "sight" in str(error), str(error)
        else:
            raise AssertionError("非法 sight 结构 %r 未被拒绝" % (broken,))


def check_sight_missing_size_or_field_fails():
    """sight 缺某体型档 / 缺派生字段 / 非法数值 → 明确失败（02C:261-265）。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)

    missing_size = rules_with_sight()
    del missing_size["sight"]["medium"]
    missing_field = rules_with_sight()
    del missing_field["sight"]["medium"]["eye_height_cm"]
    negative = rules_with_sight()
    negative["sight"]["medium"]["visible_height_cm"] = -5
    non_int = rules_with_sight()
    non_int["sight"]["medium"]["eye_height_cm"] = 1.6

    for broken in (missing_size, missing_field, negative, non_int):
        try:
            ask(doc, actor, target, rules=broken)
        except ValueError as error:
            assert "sight" in str(error), str(error)
        else:
            raise AssertionError("损坏的 sight 配置未被拒绝")


def check_body_size_required_and_validated():
    """metric 视线查询必传体型五档之一；非法 / 缺失一律 `ValueError`。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    missing_actor = {"actor_body_size": None}
    missing_target = {"target_body_size": None}
    illegal_actor = {"actor_body_size": "colossal"}
    illegal_target = {"target_body_size": "gigantic"}
    for kwargs in (missing_actor, missing_target, illegal_actor, illegal_target):
        try:
            ask(doc, actor, target, **kwargs)
        except ValueError as error:
            assert "body_size" in str(error), str(error)
        else:
            raise AssertionError("非法体型参数 %r 未被拒绝" % (kwargs,))

    # 双双缺省也必须失败
    try:
        tq.query_attack(doc, actor, target, rules=DEFAULT_RULES)
    except ValueError as error:
        assert "body_size" in str(error), str(error)
    else:
        raise AssertionError("缺省体型参数未被拒绝")

    # 五档全部可用且回显
    for size in tq.BODY_SIZES:
        result = ask(doc, actor, target, actor_body_size=size)
        assert result["actor_body_size"] == size
        assert result["target_body_size"] == "medium"


def check_sight_derives_heights():
    """眼高 / 目标可见高度按体型档派生：不同档位 → 不同的视线端点。"""
    doc = battlefield()
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)

    medium = ask(doc, actor, target)
    assert medium["eye_height_cm"] == 160 and medium["target_height_cm"] == 120
    assert medium["eye_from_cm"] == 160 and medium["visible_at_cm"] == 120

    large_actor = ask(doc, actor, target, actor_body_size="large")
    assert large_actor["eye_height_cm"] == 220
    assert large_actor["eye_from_cm"] == 220
    assert large_actor["target_height_cm"] == 120
    # 攻击者更高 → 射线在中途格的高度更高（Fraction 插值端点变了）
    assert large_actor["obstacles"] == [] and medium["obstacles"] == []

    huge_target = ask(doc, actor, target, target_body_size="huge")
    assert huge_target["target_height_cm"] == 280
    assert huge_target["visible_at_cm"] == 280


# ── 参数契约 ────────────────────────────────────────────────────────────

def check_abstract_does_not_read_sight():
    """abstract 模式不适用眼高：不读 sight、不要求体型，LOS 只看 blocks_los（02C:271）。"""
    doc = battlefield(mode="abstract")
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    patch(doc, cell_id_at(doc, 1, -1), blocks_los=True)

    # 真实数据（无 sight 配置）+ 不传体型：abstract 查询照常工作
    result = tq.query_attack(doc, actor, target, distance_unit="cell",
                             rules=RULES)
    assert result["line_of_sight"] is False
    assert result["eye_height_cm"] is None and result["target_height_cm"] is None
    assert result["eye_from_cm"] is None and result["visible_at_cm"] is None
    assert result["actor_body_size"] is None
    assert any("abstract" in note for note in result["notes"]), result["notes"]

    # 传入体型也不影响 abstract 判定：结果不随体型变化
    with_size = ask(doc, actor, target, distance_unit="cell",
                    actor_body_size="boss", target_body_size="small")
    assert with_size["line_of_sight"] is False
    assert with_size["hex_distance"] == result["hex_distance"]


def check_unknown_cell_id_raises():
    """未知 cell_id 立即报错，不按坐标兜底（02C:21）。"""
    doc = battlefield()
    for bad in ("nope", "", None):
        try:
            ask(doc, bad, cell_id_at(doc, 2, -1))
        except ValueError as error:
            assert "cell_id" in str(error), str(error)
        else:
            raise AssertionError("未知 actor cell_id %r 未被拒绝" % (bad,))


# ── 确定性 / 边界 ───────────────────────────────────────────────────────

def check_deterministic_repeated_calls():
    """同一输入 50 次结果逐字节相同（遍历顺序与取整都固定）。"""
    doc = battlefield(surfaces=("ground", "upper"))
    actor = cell_id_at(doc, 0, 0)
    target = cell_id_at(doc, 2, -1)
    raise_at(doc, actor, 250)
    patch(doc, cell_id_at(doc, 1, -1), cover_grade="medium", obstacle_height=200)

    baseline = json.dumps(ask(doc, actor, target), sort_keys=True,
                          ensure_ascii=False)
    for _ in range(50):
        again = json.dumps(ask(doc, actor, target), sort_keys=True,
                           ensure_ascii=False)
        assert again == baseline, "同一输入出现不同结果"
    json.dumps(ask(doc, actor, target))  # 结果可 JSON 序列化（UI 预览用）


def check_same_cell_query():
    """同格查询：距离 0、无阻挡、不因自身格判遮挡。"""
    doc = battlefield()
    cell = cell_id_at(doc, 0, 0)
    patch(doc, cell, blocks_los=True, cover_grade="heavy")
    result = ask(doc, cell, cell)
    assert result["hex_distance"] == 0
    assert result["distance"] == 0
    assert result["line_of_sight"] is True
    assert result["blocked_cells"] == []
    assert result["high_ground"] is False


def check_module_reuses_kernel_only():
    """只走内核：不复制距离 / 射线算法，不 import 禁改模块。"""
    with open(QUERY_MODULE, encoding="utf-8") as handle:
        text = handle.read()
    for needed in ("tactical_hex.hex_line", "tactical_hex.hex_distance",
                   "tactical_battlefield"):
        assert needed in text, "未复用 %r" % (needed,)
    for forbidden in ("prism_core", "notdnd_web", "atlas"):
        pattern = r"(?m)^\s*(?:import|from)\s+%s" % re.escape(forbidden)
        assert not re.search(pattern, text), \
            "tactical_queries.py 出现禁改依赖 %r" % (forbidden,)
    for duplicated in ("def hex_line", "def hex_distance", "def axial_to_cube",
                       "def neighbors"):
        assert duplicated not in text, "tactical_queries.py 复制了几何内核: %r" % (duplicated,)
    assert "open(" not in text, "查询模块不应直接读文件（阈值统一走 space_rules）"


def main():
    checks = (
        check_unobstructed_metric,
        check_hex_size_comes_from_battlefield,
        check_orientation_default_and_enum,
        check_orientation_classes_share_point_set,
        check_center_formula_anchors,
        check_wall_blocks_line_of_sight,
        check_obstacle_height_blocks_ray,
        check_blocked_attack_is_not_a_penalty,
        check_partial_cover_three_grades,
        check_cover_takes_highest_grade_nearest_target,
        check_target_cell_cover_and_full,
        check_cover_grades_match_combat_rules,
        check_multi_level_height_and_high_ground,
        check_high_ground_threshold_is_data_driven,
        check_high_ground_requires_line_of_sight,
        check_boundary_tie_break_uses_documented_cell,
        check_boundary_tie_break_reverse,
        check_same_qr_different_surface,
        check_metric_and_abstract_are_separate,
        check_abstract_levels_and_high_ground,
        check_abstract_does_not_read_sight,
        check_scale_declaration_is_enforced,
        check_missing_sight_fails_explicitly,
        check_sight_missing_size_or_field_fails,
        check_body_size_required_and_validated,
        check_sight_derives_heights,
        check_unknown_cell_id_raises,
        check_deterministic_repeated_calls,
        check_same_cell_query,
        check_module_reuses_kernel_only,
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
