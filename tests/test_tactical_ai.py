#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""六角格敌方战术 AI 回归（Issue #428，M13-B13）。

覆盖（对应 Issue #428 验收标准逐条）：
- 字段冻结：决策与候选键序按 `DECISION_FIELDS` / `CANDIDATE_FIELDS`，
  输出可 JSON 序列化，同输入逐字节相同（确定性）；
- 人格分化：同一局面、六型人格 → 六种**不同**且可解释的选择
  （brute 近战 / controller 分断位 / trickster 借环境 / guardian 护友 /
  summoner 掩体 / commander 高地），`reasons` 人类可读；
- 避险与地利：站在危险区会撤离（avoid_hazard）；会利用合法掩体与
  高地优势（seek_cover / seek_high_ground 被选中，reasons 点名事实）；
- 堵死兜底：被堵死且无合法攻击时选择 `end_turn`（永远合法的备用
  方案），被拒候选逐个带结构化原因；
- blocked 不掷骰：视线被遮的远程候选按不可攻击拒绝（#449 裁定口径，
  #444 failure_reasons 原样透传），不产出任何骰值字段；
- 高地按规则数据 `high_ground.applies_to` 只加成远程候选；
- AP 经济：AP 不足的攻击候选被拒（数据驱动 AP 价），合法移动照选；
- 未获知信息：改变队伍单位的属性 / 活力（隐藏信息）→ 决策逐字节
  不变——评分只消费服务端可见事实；
- 确定性入口：`seed` + `policy.top_n_random` 的有种子选择可复现；
- 降级不阻塞：评分意外失败 → `status="fallback"` + 既有最小确定性
  策略（同 #451 `_enemy_step` 口径），战斗不停摆；
- 数据驱动：`reload` / `recover` 因 action_economy.json 无对应动作被
  显式登记（不伪造候选）；人格亲和表覆盖 enemy_personas.json 六型；
- 引擎中立：本模块不 import `prism_core` / 两套 adapter；同一攻击
  意图产生的 TacticalContext 同时通过 d20 与构建引擎 adapter 的校验
  ——AI 不统一骰制，结算各走各的 adapter。

零依赖：仅 Python 3 标准库；直接 `python tests/test_tactical_ai.py` 运行。
"""
import json
import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)

import tactical_ai  # noqa: E402
import tactical_battlefield as tb  # noqa: E402
import tactical_context  # noqa: E402

# 延迟 import（仅这两个用例消费 adapter / 核心；模块本体绝不 import 它们）。
import prism_core  # noqa: E402
import tactical_forge_adapter  # noqa: E402

BUDGET = 4
AP = 3
DISTANCE_UNIT = "cell"


# ── 共享抽象战场（手工构造，确定性；19 格半径 2 六角盘） ────────────────

def make_battlefield():
    """单 surface 抽象战场：墙 (-2,0) 与 (0,1)、掩体 (1,0)、危险区 (-2,2)、
    高地 (2,-2)（层级 1，经由 (2,-1) 可走坡面）。"""
    coords = sorted(
        (q, r)
        for q in range(-2, 3) for r in range(-2, 3)
        if max(abs(q), abs(r), abs(q + r)) <= 2)
    cells = []
    for index, (q, r) in enumerate(coords):
        cell = {
            "cell_id": "cell-g-%02d" % index,
            "q": q,
            "r": r,
            "surface_id": "ground",
            "terrain_id": "open",
            "terrain_tags": ["open"],
            "movement_cost": 1,
            "blocks_movement": False,
            "blocks_los": False,
            "hazard_refs": [],
            "feature_refs": [],
            "spawnable": True,
            "elevation_level": 0,
        }
        if (q, r) in ((-2, 0), (0, 1)):
            cell["blocks_movement"] = True
            cell["blocks_los"] = True
        if (q, r) == (1, 0):
            cell["cover_grade"] = "medium"
        if (q, r) == (0, -2):
            cell["hazard_refs"] = ["hz-1"]
        if (q, r) == (2, -2):
            cell["elevation_level"] = 1
        cells.append(cell)
    return {
        "schema_version": tb.SCHEMA_VERSION,
        "kind": tb.BATTLEFIELD_KIND,
        "battlefield_id": "bf-ai",
        "geometry": {"type": "hex_axial"},
        "scale": {"mode": "abstract", "unit": "cell"},
        "cells": cells,
        "vertical_links": [],
    }


BF = make_battlefield()


def cell(q, r):
    found = tb.cell_at(BF, q, r, "ground")
    assert found is not None, "fixture 缺格 (%d,%d)" % (q, r)
    return found["cell_id"]


C = {name: cell(*coord) for name, coord in {
    "origin": (0, 0), "cover": (1, 0), "hill": (2, -2), "hill_step": (2, -1),
    "hazard": (0, -2), "hazard_step": (-2, 1), "free_west": (-1, 0),
    "free_southwest": (-1, 1), "lever": (-1, -1), "free_north": (0, -1),
    "p1": (2, 0), "p2": (0, 2), "p3": (1, -1), "far_no_cover": (1, -2),
    "wall_north": (0, 1), "wall_west": (-2, 0),
}.items()}


def battle_state(placements, objects=None, revision=7):
    """placements: [(unit_id, cell_id)]；objects: 物件实例列表。"""
    return {
        "schema_version": 1,
        "kind": "tactical.battle_state",
        "battle_id": "battle-ai",
        "battlefield_id": "bf-ai",
        "state_revision": revision,
        "round": 1,
        "participants": [{"unit_id": unit_id, "cell_id": cell_id}
                         for unit_id, cell_id in placements],
        "objects": list(objects or []),
    }


def enemy_unit(persona="default", **extra):
    unit = {"persona": persona, "vitality": 80, "max_vitality": 80,
            "footprint": [{"q": 0, "r": 0}]}
    unit.update(extra)
    return unit


def party_unit(**extra):
    unit = {"dead": False, "downed": False, "vitality": 50,
            "max_vitality": 50, "attributes": {"MGT": 5, "FIN": 5}}
    unit.update(extra)
    return unit


def units_registry(actor_persona="default", placements=None):
    """缺省注册表：e1（被测敌体）、e2（队友）、p1/p2/p3（队伍）。"""
    return {
        "e1": {"party": False, "unit": enemy_unit(actor_persona)},
        "e2": {"party": False, "unit": enemy_unit("guardian")},
        "p1": {"party": True, "unit": party_unit()},
        "p2": {"party": True, "unit": party_unit()},
        "p3": {"party": True, "unit": party_unit()},
    }


def six_persona_state():
    """人格分化测试的共享局面：e1 在原点，e2 在西南远端，三个队伍目标。"""
    return battle_state([
        ("e1", C["origin"]), ("e2", C["free_southwest"]),
        ("p1", C["p1"]), ("p2", C["p2"]), ("p3", C["p3"]),
    ], objects=[
        {"object_id": "obj-lever", "definition_id": "def-lever",
         "state": "closed", "cell_id": C["free_north"]},
    ])


LEVER_DEFINITIONS = {
    "def-lever": {
        "definition_id": "def-lever",
        "kinds": ["mechanism"],
        "initial_state": "closed",
        "states": {"closed": {"object": {}}, "open": {"object": {}}},
        "transitions": [{"from": "closed", "to": "open", "trigger": "open"}],
    },
}


def decide(state, persona, *, definitions=None, **kwargs):
    units = units_registry(persona)
    kwargs.setdefault("distance_unit", DISTANCE_UNIT)
    return tactical_ai.decide_action(
        BF, state, units, actor_id="e1", movement_budget=BUDGET,
        ap_available=AP, definitions=definitions, **kwargs)


def candidate(decision, kind):
    for item in decision["candidates"]:
        if item["kind"] == kind:
            return item
    return None


def selected_kind(decision):
    return (decision["selected"] or {}).get("kind")


# ── 验收 1：不同人格对同一局面作出可解释的不同选择 ──────────────────────

def check_six_personas_diverge():
    state = six_persona_state()
    expected = {
        "brute": "melee_attack",
        "controller": "control_target",
        "trickster": "use_environment",
        "guardian": "protect_ally",
        "summoner": "seek_cover",
        "commander": "seek_high_ground",
    }
    selections = {}
    for persona, want in expected.items():
        decision = decide(state, persona, definitions=LEVER_DEFINITIONS)
        assert decision["status"] == "ok", (persona, decision["status"])
        assert decision["persona"] == persona
        got = selected_kind(decision)
        selections[persona] = got
        assert got == want, "%s 选中 %s，期望 %s" % (persona, got, want)
        # reasons 人类可读：非空字符串，且点名候选类型。
        reasons = decision["selected"]["reasons"]
        assert reasons and all(isinstance(r, str) and r for r in reasons)
        assert any(want in r for r in reasons), reasons
        # 评分依据可审计：五个分量齐全、带权重与贡献。
        breakdown = decision["selected"]["score_breakdown"]
        assert set(breakdown) == set(tactical_ai.WEIGHT_COMPONENTS)
    assert len(set(selections.values())) == 6, selections
    # 同一局面的候选全集：每个 CANDIDATE_KIND 都出现（scored 或 rejected）。
    decision = decide(state, "brute")
    kinds = {item["kind"] for item in decision["candidates"]}
    assert kinds == set(tactical_ai.CANDIDATE_KINDS), kinds
    # 掩体 / 高地事实被点名（验收：利用合法掩体 / 高度优势）。
    cover = candidate(decide(state, "summoner"), "seek_cover")
    assert cover["status"] == "scored"
    assert any("掩体" in r for r in cover["reasons"])
    high = candidate(decide(state, "commander"), "seek_high_ground")
    assert high["status"] == "scored"
    assert any("高地" in r for r in high["reasons"])


# ── 验收 2：避开明显致命危险 ────────────────────────────────────────────

def check_avoid_hazard():
    state = battle_state([
        ("e1", C["hazard"]), ("p1", C["p3"]),
    ], objects=[
        {"object_id": "obj-vent", "definition_id": "def-vent",
         "state": "armed", "cell_id": C["hazard"], "triggered": False},
    ])
    decision = decide(state, "default")
    assert selected_kind(decision) == "avoid_hazard", \
        decision["selected"]
    picked = decision["selected"]["intent"]["to_cell_id"]
    assert picked != C["hazard"]
    assert any("危险区" in r for r in decision["selected"]["reasons"])
    # 已触发的危险物件（triggered 置位，#421 状态机）→ 不再算危险区。
    state_safe = battle_state([
        ("e1", C["hazard"]), ("p1", C["p3"]),
    ], objects=[
        {"object_id": "obj-vent", "definition_id": "def-vent",
         "state": "spent", "cell_id": C["hazard"], "triggered": True},
    ])
    decision_safe = decide(state_safe, "default")
    avoid = candidate(decision_safe, "avoid_hazard")
    assert avoid["status"] == "rejected"
    assert any(r["code"] == "not_in_hazard" for r in avoid["reject_reasons"])


# ── 验收 3：被堵死 / 候选全非法时选合法备用方案 ─────────────────────────

def check_blocked_picks_legal_fallback():
    blocking = [
        {"object_id": "blk-1", "definition_id": "def-block", "state": "solid",
         "cell_id": C["cover"]},
        {"object_id": "blk-2", "definition_id": "def-block", "state": "solid",
         "cell_id": C["p3"]},
        {"object_id": "blk-3", "definition_id": "def-block", "state": "solid",
         "cell_id": C["free_west"]},
        {"object_id": "blk-4", "definition_id": "def-block", "state": "solid",
         "cell_id": C["free_southwest"]},
        {"object_id": "blk-5", "definition_id": "def-block", "state": "solid",
         "cell_id": C["free_north"]},
        {"object_id": "blk-6", "definition_id": "def-block", "state": "solid",
         "cell_id": C["lever"]},
    ]
    definitions = {
        "def-block": {
            "definition_id": "def-block", "kinds": ["blocker"],
            "initial_state": "solid",
            "states": {"solid": {"object": {"blocks_movement": True}}},
            "transitions": [],
        },
    }
    # 邻格全部被墙 / 物件占死；唯一「可见」目标隔着墙（LOS 被遮）。
    state = battle_state([("e1", C["origin"]), ("p1", C["p2"])],
                         objects=blocking)
    decision = decide(state, "brute", definitions=definitions)
    assert selected_kind(decision) == "end_turn", decision["selected"]
    melee = candidate(decision, "melee_attack")
    assert melee["status"] == "rejected"
    codes = {r["code"] for r in melee["reject_reasons"]}
    assert "no_target_in_reach" in codes, melee["reject_reasons"]
    approach = candidate(decision, "approach")
    assert approach["status"] == "rejected"
    assert any(r["code"] == "no_legal_move"
               for r in approach["reject_reasons"])


def check_los_blocked_ranged_not_rolled():
    """#449 冻结口径：视线遮 / full 掩体 → 按不可攻击拒绝，不掷骰。"""
    state = battle_state([("e1", C["origin"]), ("p1", C["p2"])])
    decision = decide(state, "summoner", ranged_reach=3)
    ranged = candidate(decision, "ranged_attack")
    assert ranged["status"] == "rejected"
    codes = {r["code"] for r in ranged["reject_reasons"]}
    assert "attack_not_executable" in codes, ranged["reject_reasons"]
    detail = next(r for r in ranged["reject_reasons"]
                  if r["code"] == "attack_not_executable")
    blocked = detail.get("failure_reasons") or []
    assert any(item.get("code") == "line_of_sight_blocked" for item in blocked)
    # 决策输出不含任何骰值字段（AI 不结算、不掷骰）。
    dump = json.dumps(decision, ensure_ascii=False)
    for forbidden in ("dice", "roll", "hit_chance", "damage", "notation"):
        assert forbidden not in dump, forbidden


# ── 验收 4：远程候选由调用方显式声明射程后生成；高地只加成远程 ──────────

def check_ranged_reach_and_high_ground():
    # 未声明射程：远程候选显式登记不生成（射程表未落库的登记口径）。
    state = six_persona_state()
    decision = decide(state, "summoner")
    ranged = candidate(decision, "ranged_attack")
    assert ranged["status"] == "rejected"
    assert any(r["code"] == "ranged_reach_not_declared"
               for r in ranged["reject_reasons"])
    # 声明射程：隔两格、射线无掩体的合法远程候选被选中（近战够不着；
    # 用 brute——无 seek_cover 亲和干扰，远程纯攻击意图胜出）。
    far_state = battle_state([("e1", C["origin"]), ("p1", C["far_no_cover"])])
    decision = decide(far_state, "brute", ranged_reach=3)
    assert selected_kind(decision) == "ranged_attack", decision["selected"]
    # 高地：攻击方在 (2,-2)（层级 1）→ #444 high_ground=true，
    # 按规则数据 applies_to=["ranged"] 只加成远程候选。
    hill_state = battle_state([("e1", C["hill"]), ("p1", C["p1"])])
    decision = decide(hill_state, "brute", ranged_reach=3)
    assert selected_kind(decision) == "ranged_attack"
    offense = decision["selected"]["score_breakdown"]["offense"]["value"]
    flat = decide(far_state, "brute", ranged_reach=3)
    flat_offense = flat["selected"]["score_breakdown"]["offense"]["value"]
    assert offense > flat_offense, (offense, flat_offense)
    assert any("高地" in r for r in decision["selected"]["reasons"])
    melee = candidate(decision, "melee_attack")
    assert melee["status"] == "rejected", melee  # (2,-2) 距 (2,0) 2 格


# ── 验收 5：AP 经济（数据驱动 AP 价） ───────────────────────────────────

def check_insufficient_ap():
    # 远距局面：ap=1 时攻击（2 AP）被拒，合法推进移动照选。
    far_state = battle_state([("e1", C["origin"]),
                              ("p1", C["far_no_cover"])])
    units = units_registry("brute")
    decision = tactical_ai.decide_action(
        BF, far_state, units, actor_id="e1", movement_budget=BUDGET,
        ap_available=1, distance_unit=DISTANCE_UNIT)
    melee = candidate(decision, "melee_attack")
    assert melee["status"] == "rejected"
    assert any(r["code"] == "insufficient_ap" for r in melee["reject_reasons"])
    assert selected_kind(decision) == "approach", decision["selected"]


# ── 验收 6：不读取未获知信息（隐藏属性 / 活力不影响决策） ────────────────

def check_no_hidden_info():
    state = six_persona_state()
    baseline = json.dumps(decide(state, "controller",
                                 definitions=LEVER_DEFINITIONS),
                          ensure_ascii=False, sort_keys=True)
    # 改动队伍单位的**隐藏**信息：属性、活力、装备加值。
    units = units_registry("controller")
    for party_id in ("p1", "p2", "p3"):
        unit = units[party_id]["unit"]
        unit["attributes"] = {"MGT": 99, "FIN": 1, "INS": 7}
        unit["vitality"] = 3
        unit["attack_bonus"] = 42
    mutated = tactical_ai.decide_action(
        BF, state, units, actor_id="e1", movement_budget=BUDGET,
        ap_available=AP, definitions=LEVER_DEFINITIONS,
        distance_unit=DISTANCE_UNIT)
    assert json.dumps(mutated, ensure_ascii=False, sort_keys=True) == baseline


# ── 验收 7：确定性入口（固定 seed / 候选分数） ──────────────────────────

def check_deterministic_and_seeded():
    state = six_persona_state()
    first = decide(state, "brute")
    second = decide(state, "brute")
    assert json.dumps(first, ensure_ascii=False) == \
        json.dumps(second, ensure_ascii=False)
    # 有种子选择：top_n_random=2 + 固定 seed → 同 seed 同结果。
    kwargs = {"policy": {"top_n_random": 2}, "seed": "seed-42"}
    a = decide(state, "brute", **kwargs)
    b = decide(state, "brute", **kwargs)
    assert json.dumps(a, ensure_ascii=False) == \
        json.dumps(b, ensure_ascii=False)
    assert a["seed"] == "seed-42"
    assert a["selected"]["status"] == "scored"


# ── 验收 8：降级不阻塞战斗 ──────────────────────────────────────────────

def check_fallback_on_scoring_failure():
    broken = json.loads(json.dumps(BF))
    for cell_data in broken["cells"]:
        if cell_data["cell_id"] == C["cover"]:
            del cell_data["movement_cost"]  # 畸形格 → #419 规划抛错 → AI 降级
    state = battle_state([("e1", C["origin"]), ("p1", C["p3"])])
    decision = tactical_ai.decide_action(
        broken, state, units_registry("brute"), actor_id="e1",
        movement_budget=BUDGET, ap_available=AP,
        distance_unit=DISTANCE_UNIT)
    assert decision["status"] == "fallback"
    assert decision["fallback_used"] is True
    assert any(r["code"] == "ai_scoring_failed" for r in decision["reasons"])
    intent = decision["selected"]["intent"]
    assert intent["type"] in ("move", "end_turn")
    if intent["type"] == "move":
        # 降级策略 = 既有最小确定性策略：朝最近目标挪一格。
        import tactical_hex  # noqa: PLC0415
        from_cell = tb.cell_by_id(BF, C["origin"])
        to_cell = tb.cell_by_id(BF, intent["to_cell_id"])
        distance = tactical_hex.hex_distance(
            tactical_hex.axial((from_cell["q"], from_cell["r"])),
            tactical_hex.axial((to_cell["q"], to_cell["r"])))
        assert distance == 1


# ── 验收 9：无可行动状态 ────────────────────────────────────────────────

def check_no_action_states():
    state = six_persona_state()
    units = units_registry("brute")
    units["e1"]["unit"]["dead"] = True
    decision = tactical_ai.decide_action(
        BF, state, units, actor_id="e1", movement_budget=BUDGET,
        ap_available=AP, distance_unit=DISTANCE_UNIT)
    assert decision["status"] == "no_action"
    assert decision["selected"] is None
    units["e1"]["unit"] = enemy_unit("brute", downed=True)
    decision = tactical_ai.decide_action(
        BF, state, units, actor_id="e1", movement_budget=BUDGET,
        ap_available=AP, distance_unit=DISTANCE_UNIT)
    assert decision["status"] == "no_action"
    decision = tactical_ai.decide_action(
        BF, state, units, actor_id="ghost", movement_budget=BUDGET,
        ap_available=AP, distance_unit=DISTANCE_UNIT)
    assert decision["status"] == "no_action"
    assert any(r["code"] == "unknown_actor" for r in decision["reasons"])


# ── 验收 10：数据驱动（恢复类动作登记 + 人格表覆盖数据六型） ────────────

def check_data_driven_unsupported_and_personas():
    decision = decide(six_persona_state(), "brute")
    registered = {item["kind"]: item for item in decision["unsupported_kinds"]}
    assert set(registered) == {"reload", "recover"}
    for kind, item in registered.items():
        assert "action_economy.json" in item["reason"]
        assert item["rule_ref"] == "data/system/action_economy.json"
    actions = tactical_ai.supported_actions()
    assert "reload" not in actions and "recover" not in actions
    assert actions["move"] == 1 and actions["basic_attack"] == 2 \
        and actions["assist_action"] == 1
    # 人格亲和 / 权重表覆盖 enemy_personas.json 的六型。
    with open(os.path.join(ROOT, "data", "system", "enemy_personas.json"),
              encoding="utf-8") as handle:
        personas = json.load(handle)
    ids = {entry["id"] for entry in personas["personas"]}
    assert ids <= set(tactical_ai.PERSONA_AFFINITY)
    assert ids <= set(tactical_ai.PERSONA_WEIGHTS)


# ── 验收 11：引擎中立 + d20 / 构建引擎双 adapter 消费同一意图 ───────────

def check_engine_agnostic_and_adapters():
    # 只检查真正的 import 语句（docstring 提及模块名不算引用）。
    source = open(os.path.join(ROOT, "tactical_ai.py"), encoding="utf-8").read()
    import_lines = [line.strip() for line in source.splitlines()
                    if line.strip().startswith(("import ", "from "))]
    allowed = ("import json", "import os", "import random",
               "import tactical_battlefield", "import tactical_hex",
               "import tactical_movement", "import tactical_objects",
               "import tactical_queries")
    assert all(line.startswith(allowed) for line in import_lines), import_lines
    # brute 的攻击意图 → TacticalContext → 两个 adapter 都能消费。
    state = six_persona_state()
    decision = decide(state, "brute")
    assert selected_kind(decision) == "melee_attack"
    intent = decision["selected"]["intent"]
    assert set(intent) <= {"type", "target_id", "to_cell_id", "object_id",
                           "action"}
    context = tactical_context.build_context(
        BF, state, actor_id="e1", target_id=intent["target_id"],
        distance_unit=DISTANCE_UNIT)
    assert context["status"] == "ok", context["reasons"]
    check = prism_core.validate_tactical_context(context)
    assert check["ok"], check["reasons"]
    forge = tactical_forge_adapter.check_context(context)
    assert forge["ok"], forge["reasons"]


# ── 验收 12：metric 战场缺 sight 配置 → 结构化失败透传（冻结口径） ──────

def check_metric_sight_failure_transparent():
    battlefield = tb.fixture_battlefield(seed=7, mode="metric", radius=2)
    state = battle_state([("e1", "cell-ground-09"), ("p1", "cell-ground-10")])
    units = units_registry("brute")
    decision = tactical_ai.decide_action(
        battlefield, state, units, actor_id="e1", movement_budget=30,
        ap_available=AP)  # metric 战场不给格 / 步口径
    assert decision["status"] == "ok"
    melee = candidate(decision, "melee_attack")
    assert melee["status"] == "rejected"
    codes = {r["code"] for r in melee["reject_reasons"]}
    assert "spatial_query_failed" in codes, melee["reject_reasons"]
    detail = next(r for r in melee["reject_reasons"]
                  if r["code"] == "spatial_query_failed")
    assert "sight" in detail["message"]


# ── 字段冻结与 JSON 可序列化 ────────────────────────────────────────────

def check_fields_frozen_and_json():
    decision = decide(six_persona_state(), "brute")
    assert list(decision.keys()) == list(tactical_ai.DECISION_FIELDS)
    for item in decision["candidates"]:
        assert list(item.keys()) == list(tactical_ai.CANDIDATE_FIELDS)
    for item in decision["unsupported_kinds"]:
        assert set(item) == {"kind", "reason", "rule_ref"}
    dump = json.dumps(decision, ensure_ascii=False, sort_keys=False)
    again = json.loads(dump)
    assert again == json.loads(json.dumps(
        decide(six_persona_state(), "brute"), ensure_ascii=False))


# ── 入口 ────────────────────────────────────────────────────────────────

def main():
    checks = (
        check_six_personas_diverge,
        check_avoid_hazard,
        check_blocked_picks_legal_fallback,
        check_los_blocked_ranged_not_rolled,
        check_ranged_reach_and_high_ground,
        check_insufficient_ap,
        check_no_hidden_info,
        check_deterministic_and_seeded,
        check_fallback_on_scoring_failure,
        check_no_action_states,
        check_data_driven_unsupported_and_personas,
        check_engine_agnostic_and_adapters,
        check_metric_sight_failure_transparent,
        check_fields_frozen_and_json,
    )
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败: %s" % error)
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks),
                                          failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
