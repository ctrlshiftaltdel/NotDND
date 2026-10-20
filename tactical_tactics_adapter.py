#!/usr/bin/env python3
"""PRISM 战术引擎 d20 适配器（Issue #424，M13-B9）。

把 #447 `TacticalContext` 转换成 d20 战术规则**已有**的输入，并调用 #423
PRISM 规则核心结算。几何事实（距离 / LOS / 高地 / 掩体 / 移动视图）一律以
context 为唯一事实来源，本模块**不重复计算**（委托 #422 聚合层 / #444 /
#419，不直接向几何内核重复提问）。

分层职责（与 #448 的边界，防止重复实现）：

- **映射复用**：`validate_tactical_context` / `map_context_for_d20` 已在
  `prism_core`（#448）落地——本模块 import 调用，**不重写映射**；
- **适配器职责**：context 校验入口 + 支持矩阵（普通攻击 / 防护 / 移动 /
  技能与战术机动 / 环境交互，各自明确支持或显式不支持）+ 调用编排
  （组装 context → 调核心入口结算）+ 结构化返回（适配输入、所用规则 id、
  结果摘要、未映射特征、可追踪错误）+ 未支持项显式登记（不得伪称完成）；
- **零公式**：命中档位 / 失手规则 / 伤害管线 / 韧性破韧 / 状态 / 首领
  机制全在核心（#423）；本模块不做任何规则运算。高地只是既有 +1 助势
  规则（02A:93），高度本身**不默认增加伤害**——距离 / 高度差逐项
  not_mapped（#448 映射已声明，本模块透传并补充地形 / 特征项）；
- **目标合法性归调用方**：核心不消费 `attack_blocked` 与「完全掩体」
  （02A:264 无法被攻击）——适配器就是调用方，在这里显式拦截并给出
  结构化原因，**不掷骰、不静默降级、不伪造完成**；
- **骰点只在服务端**：适配器不接收任何客户端骰点作为权威输入；
  `rng` 仅供服务端注入确定性随机源。

无地图场景：context 缺失时返回结构化 `rejected`（`context_required`），
提示继续使用既有战斗路径——本模块不改变旧路径的结果。
"""
import prism_core

__all__ = [
    "ADAPTER_SCHEMA_VERSION",
    "ADAPTER_KIND",
    "ADAPTER_STATUSES",
    "RESULT_FIELDS",
    "OPERATIONS",
    "OPERATION_SUPPORT",
    "SUPPORT_LEVELS",
    "FACT_DISPOSITIONS",
    "SUPPORT_MATRIX",
    "execute_attack",
    "resolve_guard",
    "read_movement",
    "execute_maneuver",
    "execute_interaction",
    "run",
]

ADAPTER_SCHEMA_VERSION = 1
ADAPTER_KIND = "tactical.tactics_adapter_result"

# status 受控枚举：
#   ok          结算 / 透传完成；
#   rejected    context 缺失 / 无效 / 过期（拒绝使用，不降级猜测）；
#   blocked     目标合法性拦截（视线被遮 / 无法判定 / 完全掩体）；
#   unsupported 操作在 d20 核心没有结算入口（显式不支持）。
ADAPTER_STATUSES = ("ok", "rejected", "blocked", "unsupported")

# 适配器结果顶层字段（键序冻结，同 #447 契约纪律）。
RESULT_FIELDS = (
    "schema_version",
    "kind",
    "status",
    "operation",
    "adapter_inputs",
    "mapped",
    "unsupported",
    "not_mapped",
    "summary",
    "result",
    "rule_refs",
    "reasons",
)

OPERATIONS = ("attack", "guard", "move", "maneuver", "interact")

# 操作级支持口径：
#   supported   调核心入口真实结算；
#   report_only 只透传 context 里的既有视图，不结算、不改状态；
#   unsupported 核心没有对应入口——显式登记，绝不伪称完成。
OPERATION_SUPPORT = {
    "attack": "supported",
    "guard": "supported",
    "move": "report_only",
    "maneuver": "unsupported",
    "interact": "unsupported",
}
SUPPORT_LEVELS = ("supported", "report_only", "unsupported")

FACT_DISPOSITIONS = ("mapped", "not_mapped", "report_only")

# 支持矩阵（声明式，契约测试冻结）：每种操作对每种空间标签的处置。
# `blocked_values` 列出触发目标合法性拦截的事实取值（不掷骰）。
SUPPORT_MATRIX = {
    "attack": {
        "support": "supported",
        "facts": {
            "high_ground": "mapped",
            "cover_grade": "mapped",
            "line_of_sight": "mapped",
            "hex_distance": "not_mapped",
            "metric_distance": "not_mapped",
            "abstract_distance": "not_mapped",
            "signed_height_delta": "not_mapped",
            "terrain_tags": "not_mapped",
            "active_feature_refs": "not_mapped",
        },
        "blocked_values": {"cover_grade": ["full"],
                           "line_of_sight": [False, None]},
        "note": "高地 = +1 助势骰（02A:93）；轻 / 中 / 重掩体 = 防护 +2/4/6"
                "（02A:257）；完全掩体与视线被遮 / 无法判定 → 目标合法性"
                "拦截；距离 / 高度差不映射，高度不默认增加伤害",
    },
    "guard": {
        "support": "supported",
        "facts": {
            "cover_grade": "mapped",
            "high_ground": "not_mapped",
            "line_of_sight": "not_mapped",
            "hex_distance": "not_mapped",
            "metric_distance": "not_mapped",
            "abstract_distance": "not_mapped",
            "signed_height_delta": "not_mapped",
            "terrain_tags": "not_mapped",
            "active_feature_refs": "not_mapped",
        },
        "blocked_values": {"cover_grade": ["full"]},
        "note": "防护只消费掩体档（轻 / 中 / 重 → +2/4/6）；完全掩体无数值"
                "档 → 拦截；高地 / 视线不进防护",
    },
    "move": {
        "support": "report_only",
        "facts": {
            "reachable": "report_only",
            "path": "report_only",
            "path_cost": "report_only",
            "movement_blocked": "report_only",
            "hex_distance": "not_mapped",
            "metric_distance": "not_mapped",
            "abstract_distance": "not_mapped",
            "signed_height_delta": "not_mapped",
            "high_ground": "not_mapped",
            "cover_grade": "not_mapped",
            "line_of_sight": "not_mapped",
            "terrain_tags": "not_mapped",
            "active_feature_refs": "not_mapped",
        },
        "blocked_values": {},
        "note": "移动视图原样透传（#419 已算好）：不重算路径、不支付 AP、"
                "不提交状态；context 未携带移动视图时显式声明",
    },
    "maneuver": {
        "support": "unsupported",
        "facts": {},
        "blocked_values": {},
        "note": "技能与战术机动没有 d20 核心（#423）的 TacticalContext "
                "结算入口——显式不支持，不静默降级为普通攻击",
    },
    "interact": {
        "support": "unsupported",
        "facts": {},
        "blocked_values": {},
        "note": "环境交互没有 d20 核心的 TacticalContext 结算入口——"
                "显式不支持，不伪造完成",
    },
}


def _reason(code, message, **extra):
    item = {"code": code, "message": message}
    item.update(extra)
    return item


def _envelope(operation):
    """按冻结键序构建空结果壳（全部状态同形状）。"""
    return {
        "schema_version": ADAPTER_SCHEMA_VERSION,
        "kind": ADAPTER_KIND,
        "status": "ok",
        "operation": operation,
        "adapter_inputs": None,
        "mapped": [],
        "unsupported": [],
        "not_mapped": [],
        "summary": None,
        "result": None,
        "rule_refs": {},
        "reasons": [],
    }


def _ordered(result):
    return {key: result.get(key) for key in RESULT_FIELDS}


def _rule_refs(context):
    """结果携带的规则 id：#448 核心引用 + context 自带的 #447 引用。"""
    refs = dict(prism_core.CONTEXT_RULE_REFS)
    context_refs = (context or {}).get("rule_refs")
    if isinstance(context_refs, dict):
        refs.update(context_refs)
    return refs


def _context_not_mapped(context, facts, why):
    """适配器级 not_mapped 声明：context 里存在、但本操作不消费的事实。"""
    items = []
    for fact in facts:
        value = context.get(fact)
        if value is None or value == []:
            continue
        items.append({"fact": fact, "value": value, "reason": why})
    return items


def _reject_no_context(operation):
    result = _envelope(operation)
    result["status"] = "rejected"
    result["rule_refs"] = _rule_refs(None)
    result["reasons"] = [_reason(
        "context_required",
        "适配结算需要 #447 TacticalContext；没有战术地图的场景请继续使用"
        "既有战斗路径（本适配器不改变旧路径的结果）",
    )]
    return _ordered(result)


def _reject_invalid_context(operation, check):
    result = _envelope(operation)
    result["status"] = "rejected"
    result["rule_refs"] = _rule_refs(None)
    result["reasons"] = [dict(item) for item in check["reasons"]]
    return _ordered(result)


def _fill_mapping(result, mapping):
    """#448 映射簿记 → 适配器结果（深拷贝，避免与调用方共享可变结构）。"""
    result["adapter_inputs"] = dict(mapping["inputs"])
    result["mapped"] = [dict(item) for item in mapping["mapped"]]
    result["unsupported"] = [dict(item) for item in mapping["unsupported"]]
    result["not_mapped"] = [dict(item) for item in mapping["not_mapped"]]


def _full_cover_reason(item):
    return _reason(
        "target_unattackable_full_cover",
        "TacticalContext cover_grade=full：完全掩体无法被攻击（02A 掩体表"
        "无数值档）；目标合法性在此拦截，不掷骰、不降级",
        rule_ref=prism_core.CONTEXT_RULE_REFS["cover_full"],
        source=dict(item),
    )


def execute_attack(session, attacker, target, context, *, notation="1d6",
                   damage_bonus=0, attack_bonus=0, advantage=0,
                   disadvantage=0, tag="", rng=None):
    """一次 d20 普通攻击的适配结算（调用核心 `resolve_attack`）。

    - context 缺失 / 无效 / 过期 → `rejected`（不降级为「当没有 context」，
      不静默改走旧路径）；
    - 视线被遮 / 无法判定 / 完全掩体 → `blocked`：目标合法性在此拦截，
      **不掷骰**（rng 不被消费）；
    - 其余情况 → `ok`：命中档位 / 失手 / 伤害 / 韧性 / 状态 / 首领机制
      全部由核心结算，`result` 是核心原样输出，`summary` 是摘要；
    - 骰点只在服务端：本入口不接收任何客户端骰点作为权威输入。
    """
    if context is None:
        return _reject_no_context("attack")
    check = prism_core.validate_tactical_context(context)
    if not check["ok"]:
        return _reject_invalid_context("attack", check)
    result = _envelope("attack")
    result["rule_refs"] = _rule_refs(context)
    mapping = prism_core.map_context_for_d20(context)
    _fill_mapping(result, mapping)
    result["not_mapped"].extend(_context_not_mapped(
        context, ("terrain_tags", "active_feature_refs"),
        "d20 攻击结算未定义地形 / 特征修正输入；随 context 报告，不参与结算"))
    inputs = mapping["inputs"]
    if inputs["attack_blocked"]:
        result["status"] = "blocked"
        result["reasons"] = [_reason(
            "attack_blocked",
            "TacticalContext line_of_sight=False：视线被遮，目标不可攻击"
            "（不掷骰、不降级）",
            rule_ref=prism_core.CONTEXT_RULE_REFS["line_of_sight"],
        )]
        return _ordered(result)
    if context.get("line_of_sight") is None:
        result["status"] = "blocked"
        result["reasons"] = [_reason(
            "line_of_sight_undefined",
            "TacticalContext line_of_sight=None：视线无法判定（如跨 "
            "surface），不能当作通畅（不掷骰、不降级）",
            rule_ref=prism_core.CONTEXT_RULE_REFS["line_of_sight"],
        )]
        return _ordered(result)
    for item in mapping["unsupported"]:
        if item["fact"] == "cover_grade":
            result["status"] = "blocked"
            result["reasons"] = [_full_cover_reason(item)]
            return _ordered(result)
    out = prism_core.resolve_attack(
        session, attacker, target, notation=notation,
        damage_bonus=damage_bonus, attack_bonus=attack_bonus,
        advantage=advantage, disadvantage=disadvantage, tag=tag,
        rng=rng, tactical_context=context)
    result["result"] = out
    result["summary"] = {
        "grade": out["grade"],
        "grade_zh": out["grade_zh"],
        "attack_total": out["attack_total"],
        "guard": out["guard"],
        "margin": out["margin"],
        "damage": out["damage"],
        "high_ground": bool(out.get("high_ground")),
        "poise_broken": bool((out.get("poise") or {}).get("broken")),
    }
    return _ordered(result)


def resolve_guard(unit, session, context):
    """目标防护的适配结算（调用核心 `guard_breakdown`，与结算同源）。

    只消费 context 的 `cover_grade` 事实（轻 / 中 / 重 → 防护 +2/4/6，
    与声明档二选一不叠加）；完全掩体 → `blocked`（无数值档）；其余
    空间事实不进防护，逐项 not_mapped 声明。
    """
    if context is None:
        return _reject_no_context("guard")
    check = prism_core.validate_tactical_context(context)
    if not check["ok"]:
        return _reject_invalid_context("guard", check)
    result = _envelope("guard")
    result["rule_refs"] = _rule_refs(context)
    mapping = prism_core.map_context_for_d20(context)
    _fill_mapping(result, mapping)
    result["not_mapped"].extend(_context_not_mapped(
        context,
        ("high_ground", "line_of_sight", "terrain_tags",
         "active_feature_refs"),
        "防护只消费掩体档；该事实不进防护（不参与修正）"))
    for item in mapping["unsupported"]:
        if item["fact"] == "cover_grade":
            result["status"] = "blocked"
            result["reasons"] = [_full_cover_reason(item)]
            return _ordered(result)
    breakdown = prism_core.guard_breakdown(unit, session,
                                           tactical_context=context)
    result["result"] = breakdown
    result["summary"] = {"guard": breakdown["total"]}
    return _ordered(result)


def read_movement(context):
    """移动视图透传（`report_only`）：不重算路径、不支付 AP、不提交状态。

    `reachable` / `path` / `path_cost` / `movement_blocked` 原样取自
    context（#419 已按预算 / 地形 / 高度 / 授权算好）；context 未携带
    移动视图时显式声明 `movement_view_absent`，不猜、不重算。
    """
    if context is None:
        return _reject_no_context("move")
    check = prism_core.validate_tactical_context(context)
    if not check["ok"]:
        return _reject_invalid_context("move", check)
    result = _envelope("move")
    result["rule_refs"] = _rule_refs(context)
    view = {
        "reachable": list(context.get("reachable") or []),
        "path": list(context.get("path") or []),
        "path_cost": context.get("path_cost"),
        "movement_blocked": list(context.get("movement_blocked") or []),
    }
    result["result"] = view
    result["summary"] = {
        "reachable_count": len(view["reachable"]),
        "path_cell_count": len(view["path"]),
        "path_cost": view["path_cost"],
    }
    # context 的 reasons 原样透传（含 destination_unreachable / LOS 事实）。
    result["reasons"] = [dict(item) for item in (context.get("reasons") or [])]
    if not view["reachable"] and not view["path"] \
            and view["path_cost"] is None:
        absent = _reason(
            "movement_view_absent",
            "context 未携带移动视图（构建时未给 movement_budget 或预算为"
            " 0）；本适配器只透传 #419 视图，不重算路径")
        ref = (context.get("rule_refs") or {}).get("movement_budget")
        if ref:
            absent["rule_ref"] = ref
        result["reasons"].append(absent)
    result["not_mapped"] = _context_not_mapped(
        context,
        ("hex_distance", "metric_distance", "abstract_distance",
         "signed_height_delta", "high_ground", "cover_grade",
         "line_of_sight", "terrain_tags", "active_feature_refs"),
        "移动视图只透传 #419 结果；该事实不参与移动适配（不重算）")
    return _ordered(result)


def _unsupported(operation, message):
    result = _envelope(operation)
    result["status"] = "unsupported"
    result["rule_refs"] = _rule_refs(None)
    result["reasons"] = [_reason(
        "unsupported_operation", message,
        rule_ref=prism_core.CONTEXT_RULE_REFS["adapter_contract"],
        support=OPERATION_SUPPORT.get(operation, "unsupported"),
    )]
    return _ordered(result)


def execute_maneuver(context=None):
    """技能与战术机动：**显式不支持**（核心无对应 TacticalContext 结算入口）。

    绝不静默降级为普通攻击、不伪造完成、不掷骰。`context` 参数仅为
    `run` 调度一致性，不被消费。
    """
    return _unsupported(
        "maneuver",
        "技能与战术机动没有 d20 核心（#423）的 TacticalContext 结算入口；"
        "本适配器显式不支持——不静默降级为普通攻击、不伪造完成、不掷骰")


def execute_interaction(context=None):
    """环境交互：**显式不支持**（核心无对应 TacticalContext 结算入口）。"""
    return _unsupported(
        "interact",
        "环境交互没有 d20 核心（#423）的 TacticalContext 结算入口；"
        "本适配器显式不支持——不伪造完成、不掷骰")


_HANDLERS = {
    "attack": execute_attack,
    "guard": resolve_guard,
    "move": read_movement,
    "maneuver": execute_maneuver,
    "interact": execute_interaction,
}


def run(operation, *, context=None, **kwargs):
    """按支持矩阵调度操作；未知操作同样显式 `unsupported`。

    - `attack`：kwargs 透传 `session` / `attacker` / `target` 及
      `execute_attack` 的其余关键字参数；
    - `guard`：kwargs 透传 `unit` / `session`；
    - `move` / `maneuver` / `interact`：无额外参数。
    """
    handler = _HANDLERS.get(operation)
    if handler is None:
        return _unsupported(
            str(operation),
            "未知操作 %r：不在支持矩阵 %s 内（显式拒绝，不猜测）"
            % (operation, list(OPERATIONS)))
    return handler(context=context, **kwargs)
