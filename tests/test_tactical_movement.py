#!/usr/bin/env python3
"""六角格战术移动回归（Issue #419）。

覆盖：
- 绕障、加权地形、多格单位（footprint）、互斥占用；
- 楼梯 / 坡道 / 电梯免授权，梯子 / 攀爬 / 跳跃需 `allowed_modes`，`requirements`
  需 `capabilities`；
- 连续坡面阈值前后（metric 厘米、abstract 层级）与「超过阈值且无链接」；
- 预算边界（刚好够 / 差一格）；
- 坠落提案：阈值前后与多档伤害，且断言**只出提案**——战场、占用表、角色状态都不变；
- metric 与 abstract 互不混淆（成本口径、无跨口径换算函数）；
- 同成本路径确定性（重复运行、打乱 cell 顺序）；非法目的地给出可解释原因；
- 模块卫生：无硬编码阈值、不 import PRISM / Web / ATLAS。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_tactical_movement.py` 运行。
"""
import copy
import os
import random
import re
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)

import tactical_battlefield as tb  # noqa: E402
import tactical_movement as tm  # noqa: E402

RULES = tb.space_rules()
HEX = RULES["scale"]["hex_size_cm"]
SLOPE = RULES["movement"]["slope_max_cm"]
FALL = RULES["fall"]
MODULE = os.path.join(ROOT, "tactical_movement.py")

UNIT = "unit-hero"
SINGLE = [{"q": 0, "r": 0}]
DOUBLE = [{"q": 0, "r": 0}, {"q": 1, "r": 0}]


# ── 样例 ────────────────────────────────────────────────────────────────

def metric_cell(cell_id, q, r, elevation=0, cost=None, blocks=False, surface="ground"):
    return {
        "cell_id": cell_id,
        "q": q,
        "r": r,
        "surface_id": surface,
        "elevation_cm": elevation,
        "terrain_id": "floor",
        "terrain_tags": ["open"],
        "movement_cost": HEX if cost is None else cost,
        "blocks_movement": blocks,
        "blocks_los": blocks,
        "hazard_refs": [],
        "feature_refs": [],
        "spawnable": not blocks,
    }


def abstract_cell(cell_id, q, r, level=0, cost=1, blocks=False, surface="ground"):
    return {
        "cell_id": cell_id,
        "q": q,
        "r": r,
        "surface_id": surface,
        "elevation_level": level,
        "terrain_id": "floor",
        "terrain_tags": ["open"],
        "movement_cost": cost,
        "blocks_movement": blocks,
        "blocks_los": blocks,
        "hazard_refs": [],
        "feature_refs": [],
        "spawnable": not blocks,
    }


def make_link(link_id, source, target, kind, cost=0, bidirectional=True, requirements=()):
    return {
        "link_id": link_id,
        "from_cell_id": source,
        "to_cell_id": target,
        "kind": kind,
        "movement_cost": cost,
        "bidirectional": bidirectional,
        "requirements": list(requirements),
    }


def build(cells, links=(), mode="metric"):
    """组装并走 #417 的正式校验（测试数据本身必须是合法战场）。"""
    battlefield = {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": "docs/system/02c-hex-battlefield.md:29",
        "battlefield_id": "bf-move-test",
        "world_id": "test",
        "site_id": "site-test",
        "seed": 419,
        "generator_version": "test-1",
        "geometry": {"type": "hex_axial"},
        "cells": cells,
        "vertical_links": list(links),
    }
    if mode == "metric":
        battlefield["scale"] = {"mode": "metric", "hex_size_cm": HEX}
    else:
        battlefield["scale"] = {"mode": "abstract",
                                "unit": RULES["scale"]["abstract_units"][0]}
    return tb.load_battlefield(battlefield)


def codes(result):
    return {(item["code"], item["cell_id"]) for item in result["blocked"]}


# ── 绕障 / 加权地形 ─────────────────────────────────────────────────────

def check_detour_around_blocked_cell():
    """直线被挡时绕行，路径与成本都按实际步数。"""
    battlefield = build([
        metric_cell("A", 0, 0),
        metric_cell("B", 1, 0, blocks=True),
        metric_cell("C", 2, 0),
        metric_cell("D", 1, -1),
        metric_cell("E", 2, -1),
    ])
    result = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX, destination="C")
    assert result["ok"] is True, result
    assert result["path"] == ["A", "D", "E", "C"], result["path"]
    assert result["total_cost"] == 3 * HEX, result["total_cost"]
    assert ("blocked_movement", "B") in codes(result), result["blocked"]


def check_weighted_terrain_picks_cheaper_route():
    """两条等长路线时按地形 movement_cost 选便宜的一条。"""
    expensive = [
        metric_cell("A", 0, 0),
        metric_cell("B", 1, 0, cost=2 * HEX),
        metric_cell("C", 1, -1),
        metric_cell("T", 2, -1),
    ]
    battlefield = build(expensive)
    result = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX, destination="T")
    assert result["path"] == ["A", "C", "T"], result["path"]
    assert result["total_cost"] == 2 * HEX, result["total_cost"]

    flipped = copy.deepcopy(expensive)
    for cell in flipped:
        if cell["cell_id"] == "B":
            cell["movement_cost"] = HEX
        if cell["cell_id"] == "C":
            cell["movement_cost"] = 2 * HEX
    battlefield = build(flipped)
    result = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX, destination="T")
    assert result["path"] == ["A", "B", "T"], result["path"]
    assert result["total_cost"] == 2 * HEX, result["total_cost"]


# ── 多格单位 / 占用 ─────────────────────────────────────────────────────

def check_multi_cell_unit_needs_room_for_footprint():
    """多格单位的 footprint 每格都要存在且可通行；单格单位可通过同一条走廊。"""
    cells = [
        metric_cell("A", 0, 0),
        metric_cell("B", 1, 0),
        metric_cell("C", 2, 0),
    ]
    battlefield = build(cells)
    wide = tm.plan_move(battlefield, UNIT, "A", DOUBLE, 10 * HEX, destination="C")
    assert wide["ok"] is False, wide
    assert ("out_of_bounds", "C") in codes(wide), wide["blocked"]

    narrow = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX, destination="C")
    assert narrow["ok"] is True, narrow
    assert narrow["path"] == ["A", "B", "C"], narrow["path"]
    assert narrow["total_cost"] == 2 * HEX, narrow["total_cost"]

    one_step = tm.plan_move(battlefield, UNIT, "A", DOUBLE, 10 * HEX, destination="B")
    assert one_step["ok"] is True, one_step
    assert one_step["total_cost"] == HEX, one_step["total_cost"]


def check_occupied_cells_are_exclusive():
    """被其它实体占用的格不能进入，也不能作为终点；自身占用不算冲突。"""
    battlefield = build([
        metric_cell("A", 0, 0),
        metric_cell("B", 1, 0),
        metric_cell("C", 2, 0),
    ])
    blocked = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX,
                           destination="C", occupied={"B": "goblin"})
    assert blocked["ok"] is False, blocked
    assert ("occupied", "B") in codes(blocked), blocked["blocked"]
    assert "B" not in [row["cell_id"] for row in blocked["reachable"]], blocked["reachable"]

    own = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX,
                       destination="B", occupied={"A": UNIT})
    assert own["ok"] is True, own
    assert own["total_cost"] == HEX, own["total_cost"]


# ── 垂直连接与授权 ──────────────────────────────────────────────────────

def check_stairs_cross_surface():
    """跨 surface 只能经 vertical_link；没有链接时不可达。"""
    cells = [
        metric_cell("G", 0, 0, elevation=0, surface="ground"),
        metric_cell("U", 0, 0, elevation=4 * HEX, surface="upper"),
    ]
    stairs = make_link("st-1", "G", "U", "stairs", cost=HEX)
    result = tm.plan_move(build(cells, [stairs]), UNIT, "G", SINGLE, 10 * HEX,
                          destination="U")
    assert result["ok"] is True, result
    assert result["path"] == ["G", "U"], result["path"]
    assert result["total_cost"] == HEX, result["total_cost"]

    without = tm.plan_move(build(cells), UNIT, "G", SINGLE, 10 * HEX, destination="U")
    assert without["ok"] is False, without
    assert ("no_route", "U") in codes(without), without["blocked"]


def check_passive_link_kinds_need_no_authorization():
    """楼梯 / 坡道 / 电梯属于普通走路可用的被动通路。"""
    for kind in tm.PASSIVE_LINK_KINDS:
        assert kind in RULES["vertical_links"]["kinds"], kind
        cells = [
            metric_cell("G", 0, 0, elevation=0, surface="ground"),
            metric_cell("U", 0, 0, elevation=4 * HEX, surface="upper"),
        ]
        link = make_link("vl-%s" % kind, "G", "U", kind, cost=HEX)
        result = tm.plan_move(build(cells, [link]), UNIT, "G", SINGLE, 10 * HEX,
                              destination="U")
        assert result["ok"] is True, (kind, result)


def check_ramp_overrides_steep_step():
    """同一 surface 的高差超过坡度上限时，坡道连接是唯一通路。"""
    cells = [
        metric_cell("A", 0, 0, elevation=0),
        metric_cell("B", 1, 0, elevation=SLOPE + 1),
    ]
    ramp = make_link("ramp-1", "A", "B", "ramp", cost=HEX)
    result = tm.plan_move(build(cells, [ramp]), UNIT, "A", SINGLE, 10 * HEX,
                          destination="B")
    assert result["ok"] is True, result
    assert result["path"] == ["A", "B"], result["path"]

    plain = tm.plan_move(build(cells), UNIT, "A", SINGLE, 10 * HEX, destination="B")
    assert plain["ok"] is False, plain
    assert ("elevation_step", "B") in codes(plain), plain["blocked"]


def check_ladder_requires_authorization():
    """梯子不会被普通寻路当作免费通路；授权后才可走。"""
    cells = [
        metric_cell("G", 0, 0, elevation=0, surface="ground"),
        metric_cell("U", 0, 0, elevation=4 * HEX, surface="upper"),
    ]
    ladder = make_link("ld-1", "G", "U", "ladder", cost=HEX)

    denied = tm.plan_move(build(cells, [ladder]), UNIT, "G", SINGLE, 10 * HEX,
                          destination="U")
    assert denied["ok"] is False, denied
    assert ("link_not_authorized", "U") in codes(denied), denied["blocked"]

    allowed = tm.plan_move(build(cells, [ladder]), UNIT, "G", SINGLE, 10 * HEX,
                           destination="U", allowed_modes=("ladder",))
    assert allowed["ok"] is True, allowed
    assert allowed["total_cost"] == HEX, allowed["total_cost"]


def check_link_requirements_need_capabilities():
    """links.requirements 需要单位能力覆盖，否则不可通行。"""
    cells = [
        metric_cell("G", 0, 0, elevation=0, surface="ground"),
        metric_cell("U", 0, 0, elevation=4 * HEX, surface="upper"),
    ]
    link = make_link("cl-1", "G", "U", "climb", cost=HEX, requirements=("climb-kit",))
    battlefield = build(cells, [link])

    missing = tm.plan_move(battlefield, UNIT, "G", SINGLE, 10 * HEX,
                           destination="U", allowed_modes=("climb",))
    assert missing["ok"] is False, missing
    assert ("link_requires_capability", "U") in codes(missing), missing["blocked"]

    ready = tm.plan_move(battlefield, UNIT, "G", SINGLE, 10 * HEX, destination="U",
                         allowed_modes=("climb",), capabilities=("climb-kit",))
    assert ready["ok"] is True, ready


# ── 坡度阈值 / 预算边界 ─────────────────────────────────────────────────

def check_slope_threshold_boundary():
    """高差等于上限可走，超过一个单位就不可走；下降方向同样受限。"""
    within = build([
        metric_cell("A", 0, 0, elevation=0),
        metric_cell("B", 1, 0, elevation=SLOPE),
    ])
    result = tm.plan_move(within, UNIT, "A", SINGLE, 10 * HEX, destination="B")
    assert result["ok"] is True, result

    over = build([
        metric_cell("A", 0, 0, elevation=0),
        metric_cell("B", 1, 0, elevation=SLOPE + 1),
    ])
    down = tm.plan_move(over, UNIT, "B", SINGLE, 10 * HEX, destination="A")
    assert down["ok"] is False, down
    assert ("elevation_step", "A") in codes(down), down["blocked"]
    record = [item for item in down["blocked"] if item["code"] == "elevation_step"][0]
    assert record["rule"] == RULES["movement"]["source"], record


def check_budget_boundary():
    """刚好够预算可通过；差一格则不可达并给出 over_budget。"""
    battlefield = build([
        metric_cell("A", 0, 0),
        metric_cell("B", 1, 0),
        metric_cell("C", 2, 0),
    ])
    exact = tm.plan_move(battlefield, UNIT, "A", SINGLE, 2 * HEX, destination="C")
    assert exact["ok"] is True, exact
    assert exact["total_cost"] == 2 * HEX, exact["total_cost"]

    short = tm.plan_move(battlefield, UNIT, "A", SINGLE, 2 * HEX - 1, destination="C")
    assert short["ok"] is False, short
    assert ("over_budget", "C") in codes(short), short["blocked"]
    assert "C" not in [row["cell_id"] for row in short["reachable"]], short["reachable"]

    stay = tm.plan_move(battlefield, UNIT, "A", SINGLE, 0, destination="A")
    assert stay["ok"] is True, stay
    assert stay["path"] == ["A"], stay["path"]
    assert stay["total_cost"] == 0, stay["total_cost"]


# ── 坠落提案 ───────────────────────────────────────────────────────────

def _fall_battlefield(height_cm, link_kind="fall", bidirectional=False):
    cells = [
        metric_cell("L", 0, 0, elevation=height_cm, surface="ledge"),
        metric_cell("G", 0, 0, elevation=0, surface="ground"),
    ]
    link = make_link("fall-1", "L", "G", link_kind, cost=0, bidirectional=bidirectional)
    return build(cells, [link])


def _dice_expression(bands):
    match = re.match(r"^([0-9]+)d([0-9]+)$", FALL["dice"])
    assert match, FALL["dice"]
    count = int(match.group(1)) * bands
    sides = int(match.group(2))
    return "%dd%d" % (count, sides)


def check_fall_damage_bands_from_rules():
    """坠落档位读数据：无伤下限 / 整档 / 向上取整。"""
    below = tm.fall_proposal(FALL["no_damage_below_cm"] - 1)
    assert below["bands"] == 0 and below["dice"] is None, below
    assert below["prone"] is False, below

    at_threshold = tm.fall_proposal(FALL["no_damage_below_cm"])
    assert at_threshold["bands"] == 1, at_threshold
    assert at_threshold["dice"]["expression"] == _dice_expression(1), at_threshold

    one_full_step = tm.fall_proposal(FALL["step_cm"])
    assert one_full_step["bands"] == 1, one_full_step

    just_over = tm.fall_proposal(FALL["step_cm"] + 1)
    assert just_over["bands"] == 2, just_over
    assert just_over["dice"]["count"] == 2 * int(FALL["dice"].split("d")[0]), just_over

    for bad in (0, -1):
        try:
            tm.fall_proposal(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("fall_proposal(%r) 应拒绝非正高度" % (bad,))


def check_fall_149cm_proposes_no_damage_only():
    """149 cm：只出提案，零伤害档。"""
    battlefield = _fall_battlefield(149)
    before = copy.deepcopy(battlefield)
    occupied = {"L": "other-unit"}
    occupied_before = dict(occupied)
    result = tm.plan_move(battlefield, UNIT, "L", SINGLE, 10 * HEX,
                          destination="G", allowed_modes=("fall",), occupied=occupied)
    assert result["ok"] is True, result
    assert len(result["falls"]) == 1, result["falls"]
    proposal = result["falls"][0]
    assert proposal["height_cm"] == 149, proposal
    assert proposal["bands"] == 0 and proposal["dice"] is None, proposal
    assert proposal["prone"] is False, proposal
    assert proposal["proposal_only"] is True, proposal
    assert battlefield == before, "寻路不得修改战场"
    assert occupied == occupied_before, "寻路不得修改占用表"


def check_fall_150cm_and_750cm_bands():
    """150 cm 一档、750 cm 三档，且都只出提案。"""
    for height, bands in ((150, 1), (750, 3)):
        battlefield = _fall_battlefield(height)
        before = copy.deepcopy(battlefield)
        result = tm.plan_move(battlefield, UNIT, "L", SINGLE, 10 * HEX,
                              destination="G", allowed_modes=("fall",))
        assert result["ok"] is True, (height, result)
        proposal = result["falls"][0]
        assert proposal["bands"] == bands, (height, proposal)
        assert proposal["dice"]["expression"] == _dice_expression(bands), proposal
        assert proposal["dice"]["count"] == bands * int(FALL["dice"].split("d")[0]), proposal
        assert proposal["prone"] is FALL["prone"], proposal
        assert proposal["rule"] == FALL["damage_rule_source"], proposal
        assert proposal["threshold_rule"] == FALL["source"], proposal
        assert proposal["proposal_only"] is True, proposal
        assert "vitality" not in proposal and "hp" not in proposal, proposal
        assert battlefield == before, "寻路不得修改战场"


def check_fall_requires_authorization_and_never_writes_state():
    """未经授权不能借坠落连接通行；结果里没有任何可直接扣血的字段。"""
    battlefield = _fall_battlefield(4 * HEX)
    denied = tm.plan_move(battlefield, UNIT, "L", SINGLE, 10 * HEX, destination="G")
    assert denied["ok"] is False, denied
    assert ("link_not_authorized", "G") in codes(denied), denied["blocked"]
    assert denied["falls"] == [], denied["falls"]

    allowed = tm.plan_move(battlefield, UNIT, "L", SINGLE, 10 * HEX,
                           destination="G", allowed_modes=("fall",))
    for key in ("hp", "vitality", "damage", "ap", "state"):
        assert key not in allowed, "移动结果不应携带可直接结算的 %r: %r" % (key, allowed)
    assert allowed["falls"][0]["proposal_only"] is True, allowed["falls"]

    source = open(MODULE, encoding="utf-8").read()
    forbidden = re.findall(
        r"(?m)^[ \t]*(?:from|import)[ \t]+((?:prism_core|notdnd_web|atlas)[A-Za-z0-9_.]*)",
        source)
    assert not forbidden, "tactical_movement.py 不得 import: %s" % ", ".join(forbidden)


# ── 口径隔离 ───────────────────────────────────────────────────────────

def check_abstract_uses_levels_only():
    """abstract 按层级与格 / 步计，不出现厘米成本。"""
    cells = [
        abstract_cell("A", 0, 0, level=0),
        abstract_cell("B", 1, 0, level=1),
        abstract_cell("C", 2, 0, level=3),
    ]
    battlefield = build(cells, mode="abstract")
    one_level = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10, destination="B")
    assert one_level["ok"] is True, one_level
    assert one_level["total_cost"] == 1, one_level["total_cost"]

    two_levels = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10, destination="C")
    assert two_levels["ok"] is False, two_levels
    assert ("elevation_step", "C") in codes(two_levels), two_levels["blocked"]

    for name in ("cm_to_cells", "cells_to_cm", "cm_to_levels", "levels_to_cm"):
        assert not hasattr(tm, name), "不得提供跨口径换算 %s" % name


def check_abstract_fall_proposal_has_no_cm():
    """abstract 坠落提案只给层级差，不伪造厘米与骰型。"""
    cells = [
        abstract_cell("L", 0, 0, level=3, surface="ledge"),
        abstract_cell("G", 0, 0, level=0, surface="ground"),
    ]
    link = make_link("fall-a", "L", "G", "fall", cost=0, bidirectional=False)
    result = tm.plan_move(build(cells, [link], mode="abstract"), UNIT, "L", SINGLE, 10,
                          destination="G", allowed_modes=("fall",))
    assert result["ok"] is True, result
    proposal = result["falls"][0]
    assert proposal["height_cm"] is None, proposal
    assert proposal["height_levels"] == 3, proposal
    assert proposal["dice"] is None and proposal["bands"] is None, proposal
    assert proposal["proposal_only"] is True, proposal


# ── 确定性与非法目的地 ───────────────────────────────────────────────────

def check_equal_cost_path_is_deterministic():
    """同成本两条路线：按 (cost, q, r, cell_id) 稳定选一条，且与数组顺序无关。"""
    cells = [
        metric_cell("A", 0, 0),
        metric_cell("B", 1, 0),
        metric_cell("C", 1, -1),
        metric_cell("T", 2, -1),
    ]
    expected = ["A", "C", "T"]
    battlefield = build(cells)
    for _ in range(5):
        result = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX, destination="T")
        assert result["path"] == expected, result["path"]

    for seed in (1, 2, 3):
        shuffled = copy.deepcopy(cells)
        random.Random(seed).shuffle(shuffled)
        result = tm.plan_move(build(shuffled), UNIT, "A", SINGLE, 10 * HEX,
                              destination="T")
        assert result["path"] == expected, (seed, result["path"])

    costs = [(row["cost"], row["q"], row["r"], row["cell_id"])
             for row in result["reachable"]]
    assert costs == sorted(costs), costs


def check_illegal_destinations_explain_themselves():
    """未知 / 被挡 / 被占 / 无路的目的地都要给出原因。"""
    battlefield = build([
        metric_cell("A", 0, 0),
        metric_cell("B", 1, 0, blocks=True),
        metric_cell("C", 2, 0),
        metric_cell("D", 5, 5),
    ])
    unknown = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX, destination="nope")
    assert unknown["ok"] is False, unknown
    assert ("unknown_destination", "nope") in codes(unknown), unknown["blocked"]

    walled = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX, destination="B")
    assert ("blocked_movement", "B") in codes(walled), walled["blocked"]

    occupied = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX,
                            destination="B", occupied={"B": "goblin"})
    assert ("occupied", "B") in codes(occupied), occupied["blocked"]

    isolated = tm.plan_move(battlefield, UNIT, "A", SINGLE, 10 * HEX, destination="D")
    assert isolated["ok"] is False, isolated
    assert ("no_route", "D") in codes(isolated), isolated["blocked"]

    unknown_origin = tm.plan_move(battlefield, UNIT, "nope", SINGLE, 10 * HEX)
    assert unknown_origin["ok"] is False, unknown_origin
    assert ("unknown_origin", "nope") in codes(unknown_origin), unknown_origin["blocked"]


def check_invalid_arguments_are_rejected():
    """预算 / 口径 / footprint / unit_id 的非法输入直接报错。"""
    battlefield = build([metric_cell("A", 0, 0)])
    for bad_budget in (-1, "150", 1.5, True):
        try:
            tm.plan_move(battlefield, UNIT, "A", SINGLE, bad_budget)
        except ValueError:
            pass
        else:
            raise AssertionError("budget=%r 应被拒绝" % (bad_budget,))

    for bad_unit in ("", None, 7):
        try:
            tm.plan_move(battlefield, bad_unit, "A", SINGLE, 10 * HEX)
        except ValueError:
            pass
        else:
            raise AssertionError("unit_id=%r 应被拒绝" % (bad_unit,))

    for bad_footprint in (None, [], [{"q": 0}], [{"q": "0", "r": 0}]):
        try:
            tm.plan_move(battlefield, UNIT, "A", bad_footprint, 10 * HEX)
        except ValueError:
            pass
        else:
            raise AssertionError("footprint=%r 应被拒绝" % (bad_footprint,))

    no_mode = build([metric_cell("A", 0, 0)])
    del no_mode["scale"]
    try:
        tm.plan_move(no_mode, UNIT, "A", SINGLE, 10 * HEX)
    except ValueError:
        pass
    else:
        raise AssertionError("缺少 scale 的战场应被拒绝")


# ── 模块卫生 ───────────────────────────────────────────────────────────

def check_module_has_no_hardcoded_thresholds():
    """规则阈值只能来自 tactical_space.json，不得在代码里抄成字面量。"""
    with open(MODULE, encoding="utf-8") as handle:
        text = handle.read()
    hits = [token for token in ("150", "100", "50", "300", "1d6")
            if re.search(r"(?<![0-9])%s(?![0-9])" % re.escape(token), text)]
    assert not hits, "tactical_movement.py 出现硬编码阈值: %s" % ", ".join(hits)


def check_revision_check_interface():
    """状态 revision 前置检查是纯比较：不读会话、不改状态。"""
    assert tm.revision_matches(7, 7) is True
    assert tm.revision_matches(6, 7) is False
    assert tm.revision_matches(None, 7) is True
    for bad in ("7", 7.0, True):
        try:
            tm.revision_matches(bad, 7)
        except ValueError:
            pass
        else:
            raise AssertionError("revision=%r 应被拒绝" % (bad,))
    try:
        tm.revision_matches(7, None)
    except ValueError:
        pass
    else:
        raise AssertionError("state_revision=None 应被拒绝")


def check_reachable_view_includes_origin_at_zero():
    """可达集含起点、成本 0，且只收录预算内的格。"""
    battlefield = build([
        metric_cell("A", 0, 0),
        metric_cell("B", 1, 0),
        metric_cell("C", 2, 0),
    ])
    result = tm.plan_move(battlefield, UNIT, "A", SINGLE, HEX, destination=None)
    assert result["ok"] is True, result
    assert result["path"] == [] and result["total_cost"] is None, result
    rows = {row["cell_id"]: row["cost"] for row in result["reachable"]}
    assert rows == {"A": 0, "B": HEX}, rows


def main():
    checks = (
        check_detour_around_blocked_cell,
        check_weighted_terrain_picks_cheaper_route,
        check_multi_cell_unit_needs_room_for_footprint,
        check_occupied_cells_are_exclusive,
        check_stairs_cross_surface,
        check_passive_link_kinds_need_no_authorization,
        check_ramp_overrides_steep_step,
        check_ladder_requires_authorization,
        check_link_requirements_need_capabilities,
        check_slope_threshold_boundary,
        check_budget_boundary,
        check_fall_damage_bands_from_rules,
        check_fall_149cm_proposes_no_damage_only,
        check_fall_150cm_and_750cm_bands,
        check_fall_requires_authorization_and_never_writes_state,
        check_abstract_uses_levels_only,
        check_abstract_fall_proposal_has_no_cm,
        check_equal_cost_path_is_deterministic,
        check_illegal_destinations_explain_themselves,
        check_invalid_arguments_are_rejected,
        check_module_has_no_hardcoded_thresholds,
        check_revision_check_interface,
        check_reachable_view_includes_origin_at_zero,
    )
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败 [%s]: %s" % (check.__name__, error))
            failures += 1
    print("%d/%d 项通过" % (len(checks) - failures, len(checks)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
