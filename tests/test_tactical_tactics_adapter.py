#!/usr/bin/env python3
"""PRISM 战术引擎 d20 适配器回归（Issue #424）。

覆盖：
- 支持矩阵契约：操作集 / 支持级别 / 事实处置枚举受控，逐操作逐标签
  有明确支持或不支持声明；JSON 可序列化；声明与行为一致；
- 零公式 + 映射复用 + 几何委托：模块只 import `prism_core`，源码无
  `query_attack` / `tactical_queries` / ATLAS / notdnd_web；
- 目标合法性：视线被遮 / 无法判定（跨 surface）/ 完全掩体 → `blocked`，
  **不掷骰**（rng 零消费）、不静默降级；
- context 校验入口：缺失（含无地图场景）/ 过期 / 无效 → `rejected`，
  来源 reasons 透传，不降级猜测；
- d20 结果走核心既有管线：命中档位 / 暴击 / 擦过 / 落空 / 严重失手 /
  韧性削减 / 掩体防护与 #448 映射逐项等价（映射逻辑不复写）；
- range / LOS / cover / high-ground 只从 context 读取：构建 context 后
  改动战场，适配结果仍以 context 为准（不重复查询）；
- 高度不默认加伤害：高地只有 +1 助势，距离 / 高度差逐项 not_mapped；
- 移动视图 report_only：原样透传、不改 context、不重算；未携带视图
  显式声明；机动 / 环境交互显式 unsupported；
- 结果形状冻结（RESULT_FIELDS 键序）、JSON 可序列化、rule_refs 为
  docs 行引用；骰点只在服务端（公开签名无客户端骰点参数）。

零依赖：仅 Python 3 标准库；直接 `python tests/test_tactical_tactics_adapter.py` 运行。
"""
import copy
import inspect
import json
import os
import re
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)

import prism_core as pc          # noqa: E402
import tactical_battlefield as tb  # noqa: E402
import tactical_context as tctx  # noqa: E402
import tactical_tactics_adapter as adapter  # noqa: E402


class SeqRandom:
    """确定性随机源：按预定序列返回 randint 结果，越界截断到 [low, high]。"""

    def __init__(self, *values):
        self.values = [int(v) for v in values]
        self.calls = 0

    def randint(self, low, high):
        value = self.values[self.calls % len(self.values)]
        self.calls += 1
        return max(low, min(high, value))


# ── 样例：真实 #447 context（契约来源只有一个） ─────────────────────────

HEX = tb.space_rules()["scale"]["hex_size_cm"]


def _rules_with_sight():
    rules = json.loads(json.dumps(tb.space_rules()))
    rules["sight"] = {
        "small":  {"eye_height_cm": 90,  "visible_height_cm": 70},
        "medium": {"eye_height_cm": 160, "visible_height_cm": 120},
        "large":  {"eye_height_cm": 220, "visible_height_cm": 180},
        "huge":   {"eye_height_cm": 320, "visible_height_cm": 280},
        "boss":   {"eye_height_cm": 450, "visible_height_cm": 400},
    }
    return rules


SIGHT_RULES = _rules_with_sight()


def _disk(radius):
    coords = []
    for q in range(-radius, radius + 1):
        low = max(-radius, -q - radius)
        high = min(radius, -q + radius)
        for r in range(low, high + 1):
            coords.append((q, r))
    return sorted(coords)


def _cell(cell_id, q, r, surface="ground", **overrides):
    cell = {
        "cell_id": cell_id,
        "q": q,
        "r": r,
        "surface_id": surface,
        "terrain_id": "floor",
        "terrain_tags": ["open"],
        "movement_cost": HEX,
        "blocks_movement": False,
        "blocks_los": False,
        "hazard_refs": [],
        "feature_refs": [],
        "spawnable": True,
        "elevation_cm": 0,
    }
    cell.update(overrides)
    return cell


def _battlefield(overrides=None, surfaces=("ground",)):
    """半径 2 的 metric 六角盘；返回 (战场 doc, {(surface, q, r): cell_id})。"""
    prefixes = {"ground": "g", "upper": "u"}
    cells = []
    ids = {}
    for surface in surfaces:
        prefix = prefixes.get(surface, surface[:1])
        for order, (q, r) in enumerate(_disk(2)):
            cell_id = "%s%02d" % (prefix, order)
            ids[(surface, q, r)] = cell_id
            cell = _cell(cell_id, q, r, surface=surface)
            if overrides and (surface, q, r) in overrides:
                cell.update(overrides[(surface, q, r)])
            cells.append(cell)
    return {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": "docs/system/02c-hex-battlefield.md:29",
        "battlefield_id": "bf-b9-01",
        "world_id": "threshold",
        "site_id": "site-b9-01",
        "atlas_ref": "threshold/region-01",
        "seed": 424,
        "generator_version": "1.0.0",
        "geometry": {"type": "hex_axial"},
        "scale": {"mode": "metric", "hex_size_cm": HEX},
        "cells": cells,
        "vertical_links": [],
    }, ids


def _state(ids, revision=7, hero="ground", orc="ground"):
    return {
        "schema_version": 1,
        "kind": "tactical.battle_state",
        "battle_id": "battle-b9-01",
        "battlefield_id": "bf-b9-01",
        "round": 1,
        "turn": 1,
        "state_revision": revision,
        "participants": [
            {"unit_id": "hero", "cell_id": ids[(hero, 0, 0)]},
            {"unit_id": "orc", "cell_id": ids[(orc, 2, 0)]},
        ],
        "objects": [],
    }


def _build(bf, ids, state, **kwargs):
    kwargs.setdefault("actor_id", "hero")
    kwargs.setdefault("target_id", "orc")
    kwargs.setdefault("actor_body_size", "medium")
    kwargs.setdefault("target_body_size", "medium")
    kwargs.setdefault("rules", SIGHT_RULES)
    return tctx.build_context(bf, state, **kwargs)


def _combat_pair():
    """一对最小战斗单位（命中路径需要活力 / 韧性 / 条件等字段）。"""
    attacker = pc.new_unit("a", "攻击者")
    target = pc.new_unit("t", "目标")
    attacker["vitality"] = attacker["max_vitality"] = 20
    target["vitality"] = target["max_vitality"] = 20
    target["poise"] = target["max_poise"] = 8
    return attacker, target


# ── 一、模块边界：零公式 + 映射复用 + 几何委托 ──────────────────────────

def check_module_boundary():
    with open(os.path.join(ROOT, "tactical_tactics_adapter.py"),
              encoding="utf-8") as handle:
        source = handle.read()
    import_lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import "):
            import_lines.append(stripped.split(" as ")[0].split()[1])
        elif stripped.startswith("from "):
            import_lines.append(stripped.split()[1])
    assert import_lines == ["prism_core"], \
        "适配器只能 import prism_core（映射复用 #448）: %r" % (import_lines,)
    for banned in ("query_attack", "tactical_queries", "plan_move",
                   "import atlas", "import notdnd_web", "tactical_high_ground"):
        assert banned not in source, \
            "适配器不得重复计算几何 / 越层 import: %r" % banned
    # 复用 #448 映射（不重写）：核心映射函数必须被调用。
    assert "map_context_for_d20" in source
    assert "validate_tactical_context" in source


# ── 二、支持矩阵契约 ────────────────────────────────────────────────────

def check_support_matrix_contract():
    assert adapter.OPERATIONS == tuple(adapter.SUPPORT_MATRIX), \
        "支持矩阵必须覆盖全部操作"
    assert set(adapter.OPERATION_SUPPORT) == set(adapter.OPERATIONS)
    assert set(adapter.OPERATION_SUPPORT.values()) <= set(adapter.SUPPORT_LEVELS)
    # 每个事实处置都在受控枚举内；blocked_values 可 JSON 序列化。
    for operation, entry in adapter.SUPPORT_MATRIX.items():
        assert entry["support"] in adapter.SUPPORT_LEVELS, (operation, entry)
        assert set(entry["facts"]) or entry["support"] == "unsupported", operation
        for fact, disposition in entry["facts"].items():
            assert disposition in adapter.FACT_DISPOSITIONS, (fact, disposition)
        for fact, values in entry["blocked_values"].items():
            assert fact in entry["facts"], (operation, fact)
            json.dumps(values)
    json.dumps(adapter.SUPPORT_MATRIX)
    # 明确的不支持项：机动 / 环境交互显式登记（不得伪称完成）。
    assert adapter.OPERATION_SUPPORT["maneuver"] == "unsupported"
    assert adapter.OPERATION_SUPPORT["interact"] == "unsupported"
    assert adapter.OPERATION_SUPPORT["move"] == "report_only"
    assert adapter.OPERATION_SUPPORT["attack"] == "supported"
    # 声明与行为一致：矩阵说不支持的操作，行为确实拒绝。
    for operation in ("maneuver", "interact"):
        out = adapter.run(operation, context=None)
        assert out["status"] == "unsupported", (operation, out)
        assert out["reasons"][0]["code"] == "unsupported_operation"
    # 未知操作同样显式 unsupported，不猜测。
    out = adapter.run("teleport", context=None)
    assert out["status"] == "unsupported"
    assert out["operation"] == "teleport"
    # 支持矩阵声明了拦截取值：完全掩体 / 视线被遮。
    attack = adapter.SUPPORT_MATRIX["attack"]
    assert attack["blocked_values"]["cover_grade"] == ["full"]
    assert attack["blocked_values"]["line_of_sight"] == [False, None]


# ── 三、结果形状冻结 + JSON 可序列化 + rule_refs ────────────────────────

def check_result_shape_frozen():
    bf, ids = _battlefield()
    state = _state(ids)
    context = _build(bf, ids, state)
    session = pc.RuleSession()
    attacker, target = _combat_pair()

    outputs = (
        adapter.execute_attack(session, attacker, target, context,
                               rng=SeqRandom(12, 5)),
        adapter.resolve_guard(target, session, context),
        adapter.read_movement(context),
        adapter.execute_maneuver(context),
        adapter.execute_interaction(context),
        adapter.execute_attack(session, attacker, target, None),
        adapter.run("teleport", context=None),
    )
    for out in outputs:
        assert list(out.keys()) == list(adapter.RESULT_FIELDS), \
            "结果键序必须与 RESULT_FIELDS 冻结一致: %r" % (list(out.keys()),)
        assert out["schema_version"] == adapter.ADAPTER_SCHEMA_VERSION == 1
        assert out["kind"] == adapter.ADAPTER_KIND
        assert out["status"] in adapter.ADAPTER_STATUSES
        json.dumps(out)
    # rule_refs 全部是 docs 行引用。
    pattern = re.compile(r"^docs/system/[a-z0-9-]+\.md:[0-9]+$")
    for out in outputs:
        for key, value in out["rule_refs"].items():
            assert pattern.match(value), \
                "rule_ref %s=%s 不是 docs 行引用" % (key, value)


# ── 四、d20 结果走核心既有管线（档位 / 失手 / 韧性 / 状态） ─────────────

def _attack_pair_contexts(overrides=None):
    bf, ids = _battlefield(overrides)
    state = _state(ids)
    context = _build(bf, ids, state)
    return bf, ids, state, context


def check_attack_hits_core_pipeline():
    # ① 普通命中：伤害 5、韧性 −2（核心既有管线，适配器零公式）。
    bf, ids, state, context = _attack_pair_contexts()
    session = pc.RuleSession()
    attacker, target = _combat_pair()
    out = adapter.execute_attack(session, attacker, target, context,
                                 rng=SeqRandom(12, 5))
    assert out["status"] == "ok", out
    assert out["summary"]["grade"] == "hit"
    assert out["summary"]["damage"] == 5
    assert out["result"]["poise"]["poise"] == 6
    assert target["vitality"] == 15
    # 摘要与核心输出一致。
    for key in ("grade", "attack_total", "guard", "margin", "damage"):
        assert out["summary"][key] == out["result"][key], key

    # ② 暴击：两次伤害骰取高；韧性全额削减（8 − 6）；目标【失衡】。
    session = pc.RuleSession()
    attacker, target = _combat_pair()
    out = adapter.execute_attack(session, attacker, target, context,
                                 rng=SeqRandom(20, 6, 2))
    assert out["summary"]["grade"] == "crit"
    assert out["summary"]["damage"] == 6
    assert out["result"]["poise"]["poise"] == 2
    assert out["summary"]["poise_broken"] is False
    assert "失衡" in target["conditions"]

    # ③ 擦过：一半伤害，不触发韧性削减。
    session = pc.RuleSession()
    attacker, target = _combat_pair()
    out = adapter.execute_attack(session, attacker, target, context,
                                 rng=SeqRandom(9, 7))
    assert out["summary"]["grade"] == "graze"
    assert out["summary"]["damage"] == 3
    assert target["poise"] == 8 and target["conditions"] == []

    # ④ 落空：无伤害。
    session = pc.RuleSession()
    attacker, target = _combat_pair()
    out = adapter.execute_attack(session, attacker, target, context,
                                 rng=SeqRandom(5))
    assert out["summary"]["grade"] == "miss"
    assert out["summary"]["damage"] == 0
    assert target["vitality"] == 20

    # ⑤ 严重失手（自然 1）：无伤害，攻击者【失衡】，掷失手表。
    session = pc.RuleSession()
    attacker, target = _combat_pair()
    out = adapter.execute_attack(session, attacker, target, context,
                                 rng=SeqRandom(1, 3))
    assert out["summary"]["grade"] == "fumble"
    assert out["result"]["fumble_row"] == 3
    assert "失衡" in attacker["conditions"]
    assert out["summary"]["damage"] == 0


def check_attack_high_ground_maps_from_context():
    # 高地射手：context high_ground=True → 与手动 advantage=1 逐项等价
    #（同一确定性 RNG；映射复用 #448，适配器不复写 +1 规则）。
    bf, ids, state, context = _attack_pair_contexts(
        {("ground", 2, 0): {"elevation_cm": -150}})
    assert context["high_ground"] is True, context["high_ground"]

    session_a = pc.RuleSession()
    a1, t1 = _combat_pair()
    out_ctx = adapter.execute_attack(session_a, a1, t1, context,
                                     rng=SeqRandom(12, 5))
    session_b = pc.RuleSession()
    a2, t2 = _combat_pair()
    out_man = pc.resolve_attack(session_b, a2, t2, notation="1d6",
                                advantage=1, rng=SeqRandom(12, 5))
    for key in ("grade", "attack_total", "guard", "margin", "damage"):
        assert out_ctx["result"][key] == out_man[key], \
            (key, out_ctx["result"][key], out_man[key])
    assert out_ctx["adapter_inputs"]["advantage_layers"] == 1
    assert out_ctx["summary"]["high_ground"] is True
    mapped_facts = [item["fact"] for item in out_ctx["mapped"]]
    assert mapped_facts == ["high_ground"], out_ctx["mapped"]
    assert out_ctx["mapped"][0]["rule_ref"] == \
        pc.CONTEXT_RULE_REFS["high_ground_bonus"]


def check_attack_cover_guard_from_context():
    # 轻掩体：防护 +2（02A 掩体表），与核心 compute_guard 同源；不与声明档叠加。
    bf, ids, state, context = _attack_pair_contexts(
        {("ground", 2, 0): {"cover_grade": "light"}})
    session = pc.RuleSession()
    attacker, target = _combat_pair()
    out = adapter.execute_attack(session, attacker, target, context,
                                 rng=SeqRandom(12, 5))
    assert out["result"]["guard"] == 12, out["summary"]
    assert out["adapter_inputs"]["guard_bonus"] == 2
    assert out["mapped"][-1]["rule_ref"] == \
        pc.CONTEXT_RULE_REFS["cover_guard"]
    # 同一目标声明「重度」也不叠加：空间事实二选一。
    assert pc.compute_guard(dict(target, cover="重度"), session,
                            tactical_context=context) == 12

    # 防护操作（guard）：同一掩体事实 → +2；完全掩体 → blocked。
    guard_out = adapter.resolve_guard(target, session, context)
    assert guard_out["status"] == "ok"
    assert guard_out["summary"]["guard"] == 12
    assert guard_out["result"]["total"] == 12
    # guard 不消费高地 / 视线：逐项 not_mapped（矩阵声明一致）。
    facts = {item["fact"] for item in guard_out["not_mapped"]}
    assert "high_ground" in facts and "line_of_sight" in facts


def check_attack_blocked_target_legality():
    # ① 视线被遮：blocked，不掷骰（rng 零消费），不降级为普通攻击。
    bf, ids, state, context = _attack_pair_contexts(
        {("ground", 1, 0): {"blocks_los": True}})
    assert context["line_of_sight"] is False
    session = pc.RuleSession()
    attacker, target = _combat_pair()
    rng = SeqRandom(12, 5)
    out = adapter.execute_attack(session, attacker, target, context, rng=rng)
    assert out["status"] == "blocked", out
    assert out["result"] is None and out["summary"] is None
    assert rng.calls == 0, "blocked 不得掷骰"
    assert out["reasons"][0]["code"] == "attack_blocked"
    assert out["reasons"][0]["rule_ref"] == \
        pc.CONTEXT_RULE_REFS["line_of_sight"]
    assert out["adapter_inputs"]["attack_blocked"] is True
    assert target["vitality"] == 20

    # ② 视线无法判定（跨 surface）：同样 blocked，不当作通畅。
    bf2, ids2 = _battlefield(surfaces=("ground", "upper"))
    state2 = _state(ids2, orc="upper")
    context2 = _build(bf2, ids2, state2)
    assert context2["line_of_sight"] is None
    session2 = pc.RuleSession()
    attacker2, target2 = _combat_pair()
    rng2 = SeqRandom(12, 5)
    out2 = adapter.execute_attack(session2, attacker2, target2, context2,
                                  rng=rng2)
    assert out2["status"] == "blocked"
    assert rng2.calls == 0
    assert out2["reasons"][0]["code"] == "line_of_sight_undefined"

    # ③ 完全掩体：blocked（无数值档，目标合法性在此拦截）。
    bf3, ids3, state3, context3 = _attack_pair_contexts(
        {("ground", 2, 0): {"cover_grade": "full"}})
    session3 = pc.RuleSession()
    attacker3, target3 = _combat_pair()
    rng3 = SeqRandom(12, 5)
    out3 = adapter.execute_attack(session3, attacker3, target3, context3,
                                  rng=rng3)
    assert out3["status"] == "blocked"
    assert rng3.calls == 0
    assert out3["reasons"][0]["code"] == "target_unattackable_full_cover"
    assert out3["reasons"][0]["rule_ref"] == \
        pc.CONTEXT_RULE_REFS["cover_full"]
    # unsupported 项显式登记在结果里（不静默丢弃）。
    assert out3["unsupported"][0]["fact"] == "cover_grade"
    # 防护操作对完全掩体同样 blocked（compute_guard 的 ValueError 语义
    # 在适配器边界转成结构化结果）。
    guard_out = adapter.resolve_guard(target3, session3, context3)
    assert guard_out["status"] == "blocked"
    assert guard_out["reasons"][0]["code"] == "target_unattackable_full_cover"


def check_context_rejected_paths():
    bf, ids = _battlefield()
    state = _state(ids)
    session = pc.RuleSession()
    attacker, target = _combat_pair()

    # ① context 缺失（无地图场景）：rejected + 显式指引走既有路径。
    rng = SeqRandom(12, 5)
    out = adapter.execute_attack(session, attacker, target, None, rng=rng)
    assert out["status"] == "rejected"
    assert rng.calls == 0
    assert out["reasons"][0]["code"] == "context_required"
    assert "既有战斗路径" in out["reasons"][0]["message"]
    assert adapter.resolve_guard(target, session, None)["status"] == "rejected"
    assert adapter.read_movement(None)["status"] == "rejected"

    # ② stale context：整体拒绝，来源 reasons 原样透传。
    stale = _build(bf, ids, state,
                   expected_revision=state["state_revision"] - 1)
    out = adapter.execute_attack(session, attacker, target, stale,
                                 rng=SeqRandom(12, 5))
    assert out["status"] == "rejected"
    assert out["reasons"][0]["code"] == "stale_context"
    assert out["reasons"][0]["source_reasons"] == stale["reasons"]
    assert adapter.resolve_guard(target, session, stale)["status"] == "rejected"

    # ③ invalid context（来源非法）：拒绝，不降级猜测。
    invalid = tctx.build_context(None, state, actor_id="hero")
    out = adapter.execute_attack(session, attacker, target, invalid)
    assert out["status"] == "rejected"
    assert out["reasons"][0]["code"] == "invalid_context"


def check_spatial_facts_read_from_context_only():
    """range / LOS / cover / high-ground 只从 context 读取（不重复查询）。

    构建 context 后**改动战场**（拆掉高地 / 加墙）：适配结果仍以 context
    为准——若适配器重新查询几何，结果必然不同。
    """
    bf, ids, state, context = _attack_pair_contexts(
        {("ground", 2, 0): {"elevation_cm": -150}})
    assert context["high_ground"] is True
    # 战场事后变动：目标格高地拆除 + 起终点之间立墙。
    for cell in bf["cells"]:
        if cell["cell_id"] == ids[("ground", 2, 0)]:
            cell["elevation_cm"] = 0
        if cell["cell_id"] == ids[("ground", 1, 0)]:
            cell["blocks_los"] = True

    session = pc.RuleSession()
    attacker, target = _combat_pair()
    out = adapter.execute_attack(session, attacker, target, context,
                                 rng=SeqRandom(12, 5))
    # 仍按 context 的高地事实 +1 助势（等价手动 advantage=1），
    # 且没有因墙变 blocked——context 是唯一事实来源。
    assert out["status"] == "ok", out
    assert out["adapter_inputs"]["advantage_layers"] == 1
    session_b = pc.RuleSession()
    a2, t2 = _combat_pair()
    out_man = pc.resolve_attack(session_b, a2, t2, notation="1d6",
                                advantage=1, rng=SeqRandom(12, 5))
    for key in ("grade", "attack_total", "guard", "margin", "damage"):
        assert out["result"][key] == out_man[key], key
    # 防护同理：掩体事实以 context 为准（战场事后变动不影响）。
    bf_c, ids_c, state_c, cover_ctx = _attack_pair_contexts(
        {("ground", 2, 0): {"cover_grade": "heavy"}})
    for cell in bf_c["cells"]:
        if cell["cell_id"] == ids_c[("ground", 2, 0)]:
            cell["cover_grade"] = "light"
    guard_out = adapter.resolve_guard(target, session, cover_ctx)
    assert guard_out["summary"]["guard"] == 16, guard_out["summary"]  # 重 +6


def check_height_never_damage_bonus():
    """高度本身不默认增加伤害：高地只有既有 +1 助势规则。"""
    bf, ids, state, context = _attack_pair_contexts(
        {("ground", 2, 0): {"elevation_cm": -150}})
    assert context["signed_height_delta"] == -150  # 目标低于攻击者 150 cm
    session = pc.RuleSession()
    attacker, target = _combat_pair()
    out = adapter.execute_attack(session, attacker, target, context,
                                 rng=SeqRandom(12, 5))
    # 高地攻击与「无高地 + 手动 advantage=1」伤害完全一致（无额外伤害加成）。
    session_b = pc.RuleSession()
    a2, t2 = _combat_pair()
    out_man = pc.resolve_attack(session_b, a2, t2, notation="1d6",
                                advantage=1, rng=SeqRandom(12, 5))
    assert out["summary"]["damage"] == out_man["damage"]
    assert out["summary"]["attack_total"] == out_man["attack_total"]
    # 高度差逐项 not_mapped：显式声明，不静默丢弃。
    not_mapped = {item["fact"]: item for item in out["not_mapped"]}
    assert "signed_height_delta" in not_mapped
    assert "metric_distance" in not_mapped and "hex_distance" in not_mapped
    # mapped 里没有任何伤害加成条目（高地条目的效果只是助势骰）。
    for item in out["mapped"]:
        assert item["fact"] != "signed_height_delta"
    high = [item for item in out["mapped"] if item["fact"] == "high_ground"]
    assert high and "助势" in high[0]["effect"], high


# ── 五、移动 report_only + 显式不支持项 ─────────────────────────────────

def check_movement_report_only():
    bf, ids = _battlefield()
    state = _state(ids)
    far = ids[("ground", 0, 2)]  # 目标格被 orc 实心占用，选显式相邻外格
    context = _build(bf, ids, state, movement_budget=HEX * 6,
                     movement_destination=far)
    snapshot = copy.deepcopy(context)
    out = adapter.read_movement(context)
    assert out["status"] == "ok"
    # 视图原样透传（#419 已算好），适配器不改 context、不重算。
    assert out["result"]["reachable"] == context["reachable"]
    assert out["result"]["path"] == context["path"]
    assert out["result"]["path_cost"] == context["path_cost"]
    assert out["result"]["movement_blocked"] == context["movement_blocked"]
    assert out["summary"]["path_cost"] == 2 * HEX
    assert out["summary"]["reachable_count"] == len(context["reachable"])
    assert context == snapshot, "read_movement 不得修改 context"

    # 目的地不可达：destination_unreachable 透传，不猜、不兜底。
    broke = _build(bf, ids, state, movement_budget=HEX,
                   movement_destination=far)
    out2 = adapter.read_movement(broke)
    assert out2["result"]["path_cost"] is None
    codes2 = [reason["code"] for reason in out2["reasons"]]
    assert "destination_unreachable" in codes2

    # context 未携带移动视图：显式声明，不重算路径。
    plain = _build(bf, ids, state)
    out3 = adapter.read_movement(plain)
    assert out3["result"]["reachable"] == []
    codes3 = [reason["code"] for reason in out3["reasons"]]
    assert "movement_view_absent" in codes3


def check_unsupported_operations_explicit():
    context = _build(*_attack_pair_contexts()[:3])
    for operation, handler in (("maneuver", adapter.execute_maneuver),
                               ("interact", adapter.execute_interaction)):
        out = handler(context)
        assert out["status"] == "unsupported", (operation, out)
        assert out["result"] is None and out["summary"] is None
        assert out["adapter_inputs"] is None
        reason = out["reasons"][0]
        assert reason["code"] == "unsupported_operation"
        assert reason["support"] == "unsupported"
        assert "降级" in reason["message"] or "伪造" in reason["message"], reason
        # 矩阵声明与行为一致。
        assert adapter.OPERATION_SUPPORT[operation] == "unsupported"
        assert adapter.SUPPORT_MATRIX[operation]["support"] == "unsupported"


# ── 六、骰点只在服务端 + 无地图兼容 ─────────────────────────────────────

def check_no_client_dice_surface():
    sig = inspect.signature(adapter.execute_attack)
    names = set(sig.parameters)
    assert not names & {"roll", "dice", "natural", "total", "roll_value",
                        "natural_roll"}, "不得暴露可伪造的骰点参数"
    # rng 是关键字-only 的服务端注入参数。
    assert sig.parameters["rng"].kind is inspect.Parameter.KEYWORD_ONLY
    for func in (adapter.resolve_guard, adapter.read_movement,
                 adapter.execute_maneuver, adapter.execute_interaction):
        assert not set(inspect.signature(func).parameters) & \
            {"roll", "dice", "natural", "total"}


def check_no_map_legacy_path_unchanged():
    """无地图场景：核心既有路径结果不变（适配器不参与、不改行为）。"""
    session_a = pc.RuleSession()
    a1, t1 = _combat_pair()
    out_default = pc.resolve_attack(session_a, a1, t1, rng=SeqRandom(15, 7))
    session_b = pc.RuleSession()
    a2, t2 = _combat_pair()
    out_none = pc.resolve_attack(session_b, a2, t2,
                                 tactical_context=None, rng=SeqRandom(15, 7))
    assert out_default == out_none, "显式 None 必须与不传完全一致"
    assert "context_mapping" not in out_default
    # 适配器对 None context 显式拒绝，不代为伪造旧路径结果。
    out = adapter.execute_attack(session_a, a1, t1, None, rng=SeqRandom(15, 7))
    assert out["status"] == "rejected"
    # 未接战术地图的会话：核心高地投影路径照旧（I5 golden 不受影响）。
    session_c = pc.RuleSession()
    a3, t3 = _combat_pair()
    out_golden = pc.resolve_attack(session_c, a3, t3, rng=SeqRandom(10, 5))
    assert "high_ground" not in out_golden


# ── 入口 ────────────────────────────────────────────────────────────────

def main():
    checks = (
        check_module_boundary,
        check_support_matrix_contract,
        check_result_shape_frozen,
        check_attack_hits_core_pipeline,
        check_attack_high_ground_maps_from_context,
        check_attack_cover_guard_from_context,
        check_attack_blocked_target_legality,
        check_context_rejected_paths,
        check_spatial_facts_read_from_context_only,
        check_height_never_damage_bonus,
        check_movement_report_only,
        check_unsupported_operations_explicit,
        check_no_client_dice_surface,
        check_no_map_legacy_path_unchanged,
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
