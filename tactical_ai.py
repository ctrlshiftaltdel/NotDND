#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""六角格敌方战术 AI：合法行动候选与地形决策（Issue #428，M13-B13）。

让敌人依规则选择移动、找掩体 / 高地、避开危险、利用机关、保护队友、
控制目标或逃跑——**AI 是合法动作选择器，不是新的规则引擎**：

- **候选只由既有模块产生**：可达集 / 路径成本只走 #419 `plan_move`，
  空间事实（距离 / LOS / 掩体 / 高地 / 可结算性）只走 #444
  `query_attack`（与 #447 同一事实口径），占格互斥只走 #421
  `occupancy_map`，物件互动入口只走 #421 `legal_triggers`。本模块
  **零几何、零公式、零骰**——不新增第二套寻路或视线内核（02C:172）；
- **标准化意图**：选中的行动输出为 #426 服务端战斗管线既有入口的
  意图形状（`move` / `attack` / `interact` / `end_turn`）；实际结算
  仍走权限、revision、idempotency、TacticalContext 与 engine adapter。
  接线由后续 Issue 完成，本模块只交付可被服务层调用的独立入口；
- **引擎中立（不统一骰制）**：意图不携带任何引擎特定字段；本模块
  不 import `prism_core` / `tactical_tactics_adapter` /
  `tactical_forge_adapter`——d20 与构建引擎各自经既有 adapter 结算
  同一个意图（02C 第六节双引擎适配契约）；
- **只读服务端可见事实**：评分输入限于静态战场、服务端构造的
  battle_state（参与方位置 / 物件状态）、调用方传入的单位注册表中的
  **阵亡 / 倒下标志**，以及上述规则组件的返回数据。不读其他单位的
  属性与活力（未获知信息），人格目标偏好只用**位置代理**（同
  `prism_core._pick_enemy_target` 的既有口径：规则层没有伤害统计）；
- **评分可配置、可解释**：人格权重（offense / defense / safety /
  mission / cost 五分量）与候选亲和表都是模块级数据，可整体注入
  覆盖；每个候选携带 `score_breakdown` 与人类可读 `reasons[]`，
  被拒候选携带结构化 `reject_reasons[]`（含 #444 失败原样透传）；
- **确定性**：同输入同输出。候选排序键 `(−score, kind, 目标键)`；
  `seed` 只在策略 `top_n_random > 1` 时用于在最高分区做有种子选择
  （执行提醒 3「偶尔用一个次优行为」的确定性入口），缺省完全确定；
- **降级不阻塞**：候选生成 / 评分的意外异常被捕获，降级为既有
  最小确定性策略（朝最近目标挪一格，否则结束回合，同 #451
  `_enemy_step` 口径），`status="fallback"` + 原因登记——战斗不因
  AI 故障停摆；
- **数据未支持的动作显式登记**：`reload` / `recover` 等恢复类动作
  当前不在 `data/system/action_economy.json` 的 actions 里 → 每次决策在
  `unsupported_kinds` 登记原因，不伪造候选；数据落地后自动纳入
  （同一数据驱动检查）；
- **JSON 可序列化**：输出全部是 str / int / float / bool / None /
  list / dict，键序按 `DECISION_FIELDS` 冻结，同输入逐字节相同。

人格（data/system/enemy_personas.json 六型）只影响**权重与亲和**，
不改变候选合法性——合法性全部由规则组件裁决。
"""
import json
import os
import random

import tactical_battlefield
import tactical_hex
import tactical_movement
import tactical_objects
import tactical_queries

__all__ = [
    "AI_SCHEMA_VERSION",
    "AI_KIND",
    "AI_STATUSES",
    "DECISION_FIELDS",
    "CANDIDATE_FIELDS",
    "CANDIDATE_KINDS",
    "RECOVERY_KINDS",
    "DEFAULT_WEIGHTS",
    "PERSONA_WEIGHTS",
    "PERSONA_AFFINITY",
    "WEIGHT_COMPONENTS",
    "supported_actions",
    "decide_action",
]

AI_SCHEMA_VERSION = 1
AI_KIND = "tactical.ai_decision"

# status 受控枚举：
#   ok        选出了评分最高的合法候选；
#   fallback  评分意外失败 → 降级到既有最小确定性策略（不阻塞战斗）；
#   no_action 无可行动（阵亡 / 倒下 / 参与方缺失）。
AI_STATUSES = ("ok", "fallback", "no_action")

# 决策顶层字段（键序冻结，契约测试逐项核对）。
DECISION_FIELDS = (
    "schema_version",
    "kind",
    "status",
    "battle_id",
    "battlefield_id",
    "state_revision",
    "actor_id",
    "persona",
    "seed",
    "selected",
    "candidates",
    "fallback_used",
    "reasons",
    "unsupported_kinds",
    "facts_basis",
    "rule_refs",
)

# 单个候选的字段（键序冻结）。
CANDIDATE_FIELDS = (
    "kind",
    "status",          # "scored" / "rejected"
    "intent",          # 标准化意图（#426 管线入口形状）
    "score",
    "score_breakdown",
    "reasons",
    "reject_reasons",
)

# 候选动作全集（Issue #428 必须完成清单 + 永远合法的兜底）。
CANDIDATE_KINDS = (
    "approach",
    "retreat",
    "melee_attack",
    "ranged_attack",
    "seek_cover",
    "seek_high_ground",
    "avoid_hazard",
    "use_environment",
    "protect_ally",
    "rescue_ally",
    "control_target",
    "flee",
    "end_turn",
)

# 恢复类候选：仅当 action_economy.json 的 actions 里存在同名动作 id
# 才生成（数据驱动；当前数据没有 → unsupported_kinds 显式登记）。
RECOVERY_KINDS = ("reload", "recover")

# 评分五分量（权重键，PERSONA_WEIGHTS / DEFAULT_WEIGHTS 的键集）。
WEIGHT_COMPONENTS = ("offense", "defense", "safety", "mission", "cost")

# ── 评分策略（AI 策略数据，可整体注入覆盖；不是规则数值） ────────────────

# 缺省权重：五分量等比重略偏安全；人格权重在此之上覆盖。
DEFAULT_WEIGHTS = {
    "offense": 1.0,
    "defense": 0.8,
    "safety": 1.0,
    "mission": 1.0,
    "cost": 0.5,
}

# 六型战术人格的权重覆盖（docs/system/04 敌体战术人格的数值代理；
# 只影响偏好，不影响合法性）。主 + 副人格混合由调用方合并后传入。
PERSONA_WEIGHTS = {
    "brute": {"offense": 1.6, "defense": 0.4, "safety": 0.3, "cost": 0.3},
    "controller": {"offense": 0.7, "defense": 0.6, "safety": 0.8,
                   "mission": 1.4},
    "trickster": {"offense": 0.6, "defense": 0.7, "safety": 0.8,
                  "mission": 1.5},
    "guardian": {"offense": 0.5, "defense": 0.7, "safety": 0.9,
                 "mission": 1.5},
    "summoner": {"offense": 0.7, "defense": 1.1, "safety": 1.1,
                 "mission": 1.1},
    "commander": {"offense": 0.8, "defense": 1.0, "safety": 0.8,
                  "mission": 1.2},
}

# 候选亲和表（mission 分量）：人格渴望 → 候选类型的 0..1 亲和度。
# 位置 / 行为代理与 docs/system/04 行为卡一一对应（A1 直取 / B4 分化 /
# C1 换位偷袭借环境 / D1 挡在中间 / E4 集群掩护 / F3 占位示范）。
PERSONA_AFFINITY = {
    "brute": {"melee_attack": 1.0, "approach": 0.8, "ranged_attack": 0.5},
    "controller": {"control_target": 1.0, "use_environment": 0.6,
                   "seek_high_ground": 0.4},
    "trickster": {"use_environment": 1.0, "retreat": 0.7, "avoid_hazard": 0.6,
                  "flee": 0.5},
    "guardian": {"protect_ally": 1.0, "rescue_ally": 0.9, "seek_cover": 0.5},
    "summoner": {"seek_cover": 1.0, "retreat": 0.6, "ranged_attack": 0.6,
                 "flee": 0.5},
    "commander": {"seek_high_ground": 1.0, "protect_ally": 0.5,
                  "ranged_attack": 0.6},
    "default": {},
}

# 策略缺省值（policy 参数可覆盖）。
POLICY_DEFAULTS = {
    "top_n_random": 1,            # >1 时用 seed 在最高分区做有种子选择
    "flee_vitality_ratio": 0.3,   # 自身活力比 ≤ 该值 → 逃逸权重上调
    "phase_offense_boost": 1.5,   # 首领阶段（boss_phase ≥ 1）攻击权重倍率
    "safety_distance_norm": 3.0,  # 安全改善的归一分母（格）
}

# 掩体档 → 防御分量值（序关系来自规则数据 cover.grades，这里只给
# AI 策略的归一档位；不是规则数值，无命中 / 防护结算含义）。
_COVER_DEFENSE_BASE = 0.25

# 登记口径：abstract 战场基础攻击 / 物件交互的可达判定（相邻格），
# 与 #451 服务层 `ATTACK_REACH_CELLS` 同源；武器射程表未落库 →
# `ranged_reach` 由调用方显式声明，缺省不生成远程候选。
DEFAULT_ATTACK_REACH = 1

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_ACTION_ECONOMY_PATH = os.path.join(_MODULE_DIR, "data", "system",
                                    "action_economy.json")
_ECONOMY_CACHE = None

# 文档 / 数据引用（登记用，不是规则数值）。
_RULE_REFS = {
    "candidates": "docs/system/04-conductor-os.md:603",
    "pipeline": "docs/system/02c-hex-battlefield.md:156",
    "adapter_contract": "docs/system/02c-hex-battlefield.md:172",
    "cover_grades": "docs/system/02c-hex-battlefield.md:138",
    "high_ground": "docs/system/02c-hex-battlefield.md:78",
    "hazard": "docs/system/02c-hex-battlefield.md:170",
    "action_economy": "data/system/action_economy.json",
    "personas": "data/system/enemy_personas.json",
    "movement_budget": "docs/system/02c-hex-battlefield.md:90",
}


def _action_economy():
    global _ECONOMY_CACHE
    if _ECONOMY_CACHE is None:
        with open(_ACTION_ECONOMY_PATH, encoding="utf-8") as handle:
            _ECONOMY_CACHE = json.load(handle)
    return _ECONOMY_CACHE


def supported_actions():
    """action_economy.json 里带 AP 价的动作 id → AP 价（只读数据）。"""
    return {str(entry.get("id") or ""): int(entry.get("ap") or 0)
            for entry in _action_economy().get("actions") or []
            if entry.get("ap") is not None}


def _reason(code, message, **extra):
    item = {"code": code, "message": message}
    item.update(extra)
    return item


def _ordered(mapping, fields):
    return {key: mapping.get(key) for key in fields}


def _hex_distance(battlefield, from_cell_id, to_cell_id):
    """六角距离（#417 既有内核；未知格返回 None）。"""
    a = tactical_battlefield.cell_by_id(battlefield, from_cell_id)
    b = tactical_battlefield.cell_by_id(battlefield, to_cell_id)
    if a is None or b is None:
        return None
    return tactical_hex.hex_distance(
        tactical_hex.axial((a["q"], a["r"])),
        tactical_hex.axial((b["q"], b["r"])))


def _merge(base, override):
    merged = dict(base)
    merged.update(override or {})
    return merged


def _weights_for(persona, override):
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(PERSONA_WEIGHTS.get(persona) or {})
    return _merge(weights, override)


def _affinity_for(persona, kind):
    return float((PERSONA_AFFINITY.get(persona)
                  or PERSONA_AFFINITY["default"]).get(kind, 0.0))


def _cover_rank(rules, grade):
    """掩体档序（0 = 无掩体；档名与顺序全部来自规则数据，不写死档名）。"""
    grades = list(rules["cover"]["grades"])
    return grades.index(grade) + 1 if grade in grades else 0


# ── 局面视图（全部来自服务端可见事实） ──────────────────────────────────

class _View:
    """一次决策的只读局面视图：参与方、威胁、队友、危险格、物件。"""

    def __init__(self, battlefield, battle_state, units, actor_id,
                 definitions, rules):
        self.battlefield = battlefield
        self.battle_state = battle_state
        self.rules = rules
        self.actor_id = actor_id
        self.definitions = definitions if isinstance(definitions, dict) else {}
        self.actor_cell = None
        self.actor_unit = {}
        self.actor_downed = False
        self.actor_dead = False
        self.threats = []        # [(unit_id, cell_id)] 意识清醒的队伍单位
        self.allies = []         # [(unit_id, cell_id)] 存活的其他敌体
        self.downed_allies = []  # [(unit_id, cell_id)] 倒下未死的队友
        for participant in battle_state.get("participants") or []:
            if not isinstance(participant, dict):
                continue
            unit_id = participant.get("unit_id")
            cell_id = participant.get("cell_id")
            entry = (units or {}).get(unit_id)
            entry = entry if isinstance(entry, dict) else {}
            unit = entry.get("unit")
            unit = unit if isinstance(unit, dict) else {}
            if unit_id == actor_id:
                self.actor_cell = cell_id
                self.actor_unit = unit
                self.actor_downed = bool(unit.get("downed"))
                self.actor_dead = bool(unit.get("dead"))
                continue
            if entry.get("party"):
                if not unit.get("dead") and not unit.get("downed"):
                    self.threats.append((unit_id, cell_id))
            elif not unit.get("dead"):
                if unit.get("downed"):
                    self.downed_allies.append((unit_id, cell_id))
                else:
                    self.allies.append((unit_id, cell_id))
        self.hazard_cells = self._hazard_cells()
        self.objects = [item for item in battle_state.get("objects") or []
                        if isinstance(item, dict)]

    def _hazard_cells(self):
        """危险格：cell.hazard_refs 非空的格（战场静态事实）。

        覆盖该格的危险物件全部已触发（#421 状态机置位 `triggered`，
        服务端可见事实）时不再计入。
        """
        triggered = {item.get("object_id") for item in
                     self.battle_state.get("objects") or []
                     if isinstance(item, dict) and item.get("triggered")}
        cells = set()
        for cell in self.battlefield.get("cells") or []:
            if not isinstance(cell, dict) or not (cell.get("hazard_refs") or []):
                continue
            cell_id = cell.get("cell_id")
            covering = tactical_objects.object_ids_at(
                self.battle_state, self.battlefield, cell_id)
            if covering and all(oid in triggered for oid in covering):
                continue
            cells.add(cell_id)
        return sorted(cells)

    def nearest_threat(self, cell_id):
        """距 `cell_id` 最近的清醒队伍目标 `(unit_id, cell_id, distance)`。"""
        best = None
        for unit_id, threat_cell in self.threats:
            distance = _hex_distance(self.battlefield, cell_id, threat_cell)
            if distance is None:
                continue
            if best is None or (distance, unit_id) < (best[2], best[0]):
                best = (unit_id, threat_cell, distance)
        return best

    def threat_min_distance(self, cell_id):
        """`cell_id` 到全部清醒威胁的最近距离（无威胁返回 None）。"""
        distances = [d for d in (_hex_distance(self.battlefield, cell_id, c)
                                 for _u, c in self.threats) if d is not None]
        return min(distances) if distances else None

    def threat_distance_sum(self, cell_id):
        """`cell_id` 到全部清醒威胁的距离和（控制候选的分断位目标函数）。"""
        distances = [d for d in (_hex_distance(self.battlefield, cell_id, c)
                                 for _u, c in self.threats) if d is not None]
        return sum(distances) if distances else None

    def is_hazardous(self, cell_id):
        return cell_id in self.hazard_cells

    def adjacent_to_hazard(self, cell_id):
        cell = tactical_battlefield.cell_by_id(self.battlefield, cell_id)
        if cell is None:
            return False
        if self.is_hazardous(cell_id):
            return True
        for nq, nr in tactical_hex.neighbors((cell["q"], cell["r"])):
            neighbor = tactical_battlefield.cell_at(
                self.battlefield, nq, nr, cell.get("surface_id"))
            if neighbor is not None and self.is_hazardous(neighbor.get("cell_id")):
                return True
        return False


def _plan_reach(view, budget, allowed_modes, capabilities):
    """一次 #419 plan_move（完整预算），返回可达格列表（排除原地）。"""
    occupied = tactical_objects.occupancy_map(
        view.battle_state, view.battlefield, exclude=[view.actor_id])
    footprint = (view.actor_unit or {}).get("footprint")
    if not isinstance(footprint, list) or not footprint:
        footprint = [{"q": 0, "r": 0}]
    move = tactical_movement.plan_move(
        view.battlefield, view.actor_id, view.actor_cell, footprint, budget,
        allowed_modes=allowed_modes, capabilities=capabilities,
        occupied=occupied, rules=view.rules)
    return [option for option in move.get("reachable") or []
            if option.get("cell_id") != view.actor_cell
            and int(option.get("cost") or 0) > 0]


def _query(view, from_cell_id, to_cell_id, args):
    """#444 空间事实查询；失败返回 `(None, reason)`（结构化透传，不静默）。"""
    try:
        return tactical_queries.query_attack(
            view.battlefield, from_cell_id, to_cell_id,
            actor_body_size=args["actor_body_size"],
            target_body_size=args["target_body_size"],
            distance_unit=args["distance_unit"], rules=view.rules), None
    except (tactical_queries.QueryError, ValueError) as error:
        return None, _reason(
            "spatial_query_failed",
            "空间查询失败（#444 既有失败行为透传，含缺 sight 配置）: %s"
            % (error,))


def _self_cover(view, threat_cell, cell_id, args):
    """`cell_id` 处对 `threat_cell` 方向的掩体档（#444 查询方向取反）。

    #444 的掩体事实描述「目标端」的掩体：从威胁所在格向候选格查询，
    返回的 `cover_grade` 即候选格站位者获得的掩体档。
    """
    query, _failure = _query(view, threat_cell, cell_id, args)
    if query is None:
        return None
    return query.get("cover_grade")


# ── 候选收集器（统一处理 AP 过滤与评分簿记） ────────────────────────────

class _Builder:
    """候选收集器：每个候选 = 意图 + 五分量 + 理由；被拒候选带原因。"""

    def __init__(self, view, args, weights):
        self.view = view
        self.args = args
        self.weights = weights
        self.candidates = []

    def reject(self, kind, intent, reasons):
        self.candidates.append(_ordered({
            "kind": kind,
            "status": "rejected",
            "intent": intent,
            "score": None,
            "score_breakdown": None,
            "reasons": [],
            "reject_reasons": list(reasons),
        }, CANDIDATE_FIELDS))

    def score(self, kind, intent, components, reasons):
        breakdown = {}
        total = 0.0
        for name in WEIGHT_COMPONENTS:
            value = float(components.get(name, 0.0))
            weight = float(self.weights.get(name, 0.0))
            breakdown[name] = {"value": round(value, 6), "weight": weight,
                               "contribution": round(value * weight, 6)}
            total += value * weight
        self.candidates.append(_ordered({
            "kind": kind,
            "status": "scored",
            "intent": intent,
            "score": round(total, 6),
            "score_breakdown": breakdown,
            "reasons": list(reasons),
            "reject_reasons": [],
        }, CANDIDATE_FIELDS))

    def ap_cost(self, action_id, kind):
        """动作 AP 价（数据驱动）；数据没有该动作或 AP 不足 → 拒绝并返回 None。"""
        actions = supported_actions()
        cost = actions.get(action_id)
        if cost is None:
            self.reject(kind, None, [_reason(
                "action_not_in_data",
                "action_economy.json 的 actions 里没有 %r：规则未支持该动作"
                % (action_id,))])
            return None
        if cost > self.args["ap_available"]:
            self.reject(kind, None, [_reason(
                "insufficient_ap",
                "%s 需要 %d AP，剩余 %d" % (kind, cost, self.args["ap_available"]),
                ap_cost=cost, ap_available=self.args["ap_available"])])
            return None
        return cost

    def cost_value(self, ap_cost, path_cost):
        """成本分量：AP 占比 + 路径成本占预算比（负值，下限 -1）。"""
        per_turn = max(1, int(_action_economy()["round"]["ap_per_turn"]))
        value = ap_cost / float(per_turn)
        budget = self.args["movement_budget"]
        if path_cost and budget:
            value += path_cost / float(budget)
        return -min(1.0, value)


def _move_rows(view, options, key):
    """可达格按 `(key, cost, cell_id)` 升序（确定性平手序）。"""
    rows = []
    for option in options:
        cell_id = option.get("cell_id")
        value = key(cell_id, int(option.get("cost") or 0))
        if value is None:
            continue
        rows.append((value, int(option.get("cost") or 0), cell_id))
    rows.sort()
    return rows


def _safety_gain(view, args, cell_id):
    """目标格相对当前位置的安全改善（威胁距离 + 危险邻近，AI 策略归一）。"""
    before = view.threat_min_distance(view.actor_cell)
    after = view.threat_min_distance(cell_id)
    gain = 0.0
    norm = float(args["policy"]["safety_distance_norm"])
    if before is not None and after is not None:
        gain += max(0.0, min(1.0, (after - before) / norm))
    if view.adjacent_to_hazard(view.actor_cell) \
            and not view.adjacent_to_hazard(cell_id):
        gain = min(1.0, gain + 0.5)
    if view.adjacent_to_hazard(cell_id):
        gain -= 0.25
    return gain


def _mission_affinity(builder, kind):
    return _affinity_for(builder.args["persona"], kind)


# ── 候选生成（全部经规则组件；每个 kind 一个入口） ──────────────────────

def _attack_facts(builder, target_id, target_cell, reach):
    """攻击候选的 #444 事实；不可结算时返回 `(None, reasons)`。"""
    view = builder.view
    args = builder.args
    distance = _hex_distance(view.battlefield, view.actor_cell, target_cell)
    if distance is None:
        return None, [_reason("unknown_cell",
                              "目标格不在本战场: %r" % (target_cell,))]
    if distance > reach:
        return None, [_reason(
            "out_of_range",
            "距目标 %s %d 格，超出可达口径 %d" % (target_id, distance, reach),
            hex_distance=distance, reach=reach)]
    query, failure = _query(view, view.actor_cell, target_cell, args)
    if failure is not None:
        return None, [failure]
    if query.get("executable") is not True:
        codes = [str(item.get("code")) for item in
                 query.get("failure_reasons") or []]
        return None, [_reason(
            "attack_not_executable",
            "目标 %s 不可结算（%s）：视线 / 掩体事实由 #444 给出，不掷骰"
            % (target_id, ", ".join(codes) or "executable=false"),
            failure_reasons=list(query.get("failure_reasons") or []))]
    return query, None


def _gen_attack(builder, kind, reach, ap_action="basic_attack"):
    """近战 / 远程攻击：对可达口径内的每个清醒威胁做 #444 可结算性检查，
    取 offense 最优的一个目标作为该 kind 的唯一候选（被拒目标逐个留痕）。"""
    view = builder.view
    if not view.threats:
        builder.reject(kind, None, [_reason("no_valid_target",
                                            "没有清醒的队伍目标")])
        return
    ap_cost = builder.ap_cost(ap_action, kind)
    if ap_cost is None:
        return
    best = None
    for target_id, target_cell in view.threats:
        distance = _hex_distance(view.battlefield, view.actor_cell, target_cell)
        if distance is None or distance > reach:
            continue
        query, reasons = _attack_facts(builder, target_id, target_cell, reach)
        if query is None:
            builder.reject(kind, {"type": "attack", "target_id": target_id},
                           reasons)
            continue
        offense = 0.5
        score_reasons = ["%s：距目标 %s %d 格，#444 executable=true"
                         % (kind, target_id, distance)]
        if query.get("cover_grade") is None:
            offense += 0.2
            score_reasons.append("目标无掩体")
        else:
            score_reasons.append("目标处于 %s 掩体（档序来自规则数据）"
                                 % (query.get("cover_grade"),))
        # 高地加成只按规则数据 `high_ground.applies_to` 生效（当前数据只列 ranged）。
        applies_to = list(view.rules["high_ground"]["applies_to"])
        if query.get("high_ground") and "ranged" in applies_to \
                and kind == "ranged_attack":
            offense += 0.2
            score_reasons.append("攻击方处于高地（#444 high_ground=true，"
                                 "规则数据 applies_to=%s）" % (applies_to,))
        if best is None or offense > best[0]:
            best = (offense, target_id, score_reasons)
    if best is None:
        builder.reject(kind, None, [_reason(
            "no_target_in_reach", "没有目标在可达口径 %d 格内" % (reach,))])
        return
    _offense, target_id, score_reasons = best
    builder.score(kind, {"type": "attack", "target_id": target_id},
                  {"offense": _offense,
                   "mission": _mission_affinity(builder, kind),
                   "cost": builder.cost_value(ap_cost, None)},
                  score_reasons)


def _gen_move_candidate(builder, kind, ap_action, key, reasons_for,
                        extra_components=None):
    """通用移动候选：在共享可达集里按 `key` 选最优格。"""
    ap_cost = builder.ap_cost(ap_action, kind)
    if ap_cost is None:
        return
    rows = _move_rows(builder.view, builder.args["reachable"], key)
    if not rows:
        builder.reject(kind, None, [_reason(
            "no_legal_move",
            "预算与地形 / 占用约束下没有满足 %s 条件的可达格" % (kind,))])
        return
    _value, path_cost, cell_id = rows[0]
    components = {
        "mission": _mission_affinity(builder, kind),
        "safety": _safety_gain(builder.view, builder.args, cell_id),
        "cost": builder.cost_value(ap_cost, path_cost),
    }
    components.update(extra_components or {})
    builder.score(kind, {"type": "move", "to_cell_id": cell_id}, components,
                  reasons_for(cell_id))


def _gen_approach(builder):
    """approach：逼近人格优先目标（位置代理 = 最近清醒威胁，不后退）。"""
    view = builder.view
    if not view.threats:
        builder.reject("approach", None, [_reason("no_valid_target",
                                                  "没有清醒的队伍目标")])
        return
    target_id, target_cell, current = view.nearest_threat(view.actor_cell)

    def key(cell_id, _cost):
        distance = _hex_distance(view.battlefield, cell_id, target_cell)
        if distance is None or distance >= current:
            return None  # 不比原地更近的移动没有意义（同 #451 不后退口径）
        return distance

    def reasons(cell_id):
        return ["approach：向最近目标 %s（原距离 %d）推进到 %s（距离 %d）"
                % (target_id, current, cell_id,
                   _hex_distance(view.battlefield, cell_id, target_cell))]

    _gen_move_candidate(builder, "approach", "move", key, reasons)


def _gen_away(builder, kind):
    """retreat / flee：移动到比当前位置更远离全部威胁的格（远者优先）。"""
    view = builder.view
    if not view.threats:
        builder.reject(kind, None, [_reason("no_valid_target",
                                            "没有清醒的队伍目标")])
        return
    current = view.threat_min_distance(view.actor_cell)

    def key(cell_id, _cost):
        after = view.threat_min_distance(cell_id)
        if after is None or (current is not None and after <= current):
            return None
        return -after

    def reasons(cell_id):
        return ["%s：最近威胁距离 %s → %s（远离全部清醒威胁）"
                % (kind, current, view.threat_min_distance(cell_id))]

    # 逃逸的自保分量只读**自身**活力（自身状态不是未获知信息）。
    vitality = int((view.actor_unit or {}).get("vitality") or 0)
    max_vitality = int((view.actor_unit or {}).get("max_vitality") or 0) or 1
    ratio = vitality / float(max_vitality)
    threshold = float(builder.args["policy"]["flee_vitality_ratio"])

    def wrapped(cell_id):
        items = reasons(cell_id)
        if kind == "flee":
            items.append("flee：自身活力 %d/%d（只读自身状态，低活力抬升"
                         "逃逸权重）" % (vitality, max_vitality))
        return items

    if kind == "flee":
        def extra_gain(cell_id):
            base = _safety_gain(view, builder.args, cell_id)
            return min(1.0, base + max(0.0, (threshold - ratio)
                                       / max(threshold, 1e-9)) * 0.5)

        # flee 的 safety 在缺省值上叠加自保项：包装 safety 计算。
        ap_cost = builder.ap_cost("move", kind)
        if ap_cost is None:
            return
        rows = _move_rows(view, builder.args["reachable"], key)
        if not rows:
            builder.reject(kind, None, [_reason(
                "no_legal_move", "可达集中没有比当前位置更远离威胁的格")])
            return
        _value, path_cost, cell_id = rows[0]
        builder.score(kind, {"type": "move", "to_cell_id": cell_id},
                      {"mission": _mission_affinity(builder, kind),
                       "safety": extra_gain(cell_id),
                       "cost": builder.cost_value(ap_cost, path_cost)},
                      wrapped(cell_id))
        return
    _gen_move_candidate(builder, kind, "move", key, wrapped)


def _gen_seek_cover(builder):
    """seek_cover：移动到对最近威胁掩体档更高的格（#444 方向取反查询）。"""
    view = builder.view
    args = builder.args
    if not view.threats:
        builder.reject("seek_cover", None, [_reason("no_valid_target",
                                                    "没有清醒的队伍目标")])
        return
    ap_cost = builder.ap_cost("move", "seek_cover")
    if ap_cost is None:
        return
    threat_id, threat_cell, _distance = view.nearest_threat(view.actor_cell)
    current_cover = _self_cover(view, threat_cell, view.actor_cell, args)
    current_rank = _cover_rank(view.rules, current_cover)
    best = None
    for option in args["reachable"]:
        cell_id = option.get("cell_id")
        if view.is_hazardous(cell_id):
            continue
        cover = _self_cover(view, threat_cell, cell_id, args)
        rank = _cover_rank(view.rules, cover)
        if rank <= current_rank:
            continue
        entry = (-rank, int(option.get("cost") or 0), cell_id)
        if best is None or entry < best[0]:
            best = (entry, cell_id, cover, rank, int(option.get("cost") or 0))
    if best is None:
        builder.reject("seek_cover", None, [_reason(
            "no_better_cover",
            "可达集中没有比对 %s 掩体更高的格（当前档 %s）"
            % (threat_id, current_cover or "无"))])
        return
    _entry, cell_id, cover, _rank, path_cost = best
    builder.score("seek_cover", {"type": "move", "to_cell_id": cell_id},
                  {"mission": _mission_affinity(builder, "seek_cover"),
                   "defense": _COVER_DEFENSE_BASE * _rank,
                   "safety": _safety_gain(view, args, cell_id),
                   "cost": builder.cost_value(ap_cost, path_cost)},
                  ["seek_cover：从掩体档 %s 移动到 %s（对威胁 %s 方向，"
                   "#444 查询方向取反）"
                   % (current_cover or "无", cover, threat_id)])


def _gen_seek_high_ground(builder):
    """seek_high_ground：移动到对最近威胁 high_ground=true 的格（#444 事实）。"""
    view = builder.view
    args = builder.args
    if not view.threats:
        builder.reject("seek_high_ground", None, [_reason(
            "no_valid_target", "没有清醒的队伍目标")])
        return
    ap_cost = builder.ap_cost("move", "seek_high_ground")
    if ap_cost is None:
        return
    threat_id, threat_cell, _distance = view.nearest_threat(view.actor_cell)
    best = None
    for option in args["reachable"]:
        cell_id = option.get("cell_id")
        query, _failure = _query(view, cell_id, threat_cell, args)
        if query is None or not query.get("high_ground"):
            continue
        entry = (int(option.get("cost") or 0), cell_id)
        if best is None or entry < best[0]:
            best = (entry, cell_id, int(option.get("cost") or 0))
    if best is None:
        builder.reject("seek_high_ground", None, [_reason(
            "no_high_ground_reachable",
            "可达集中没有对 %s 达成高地阈值（#444 high_ground）的格"
            % (threat_id,))])
        return
    _entry, cell_id, path_cost = best
    builder.score("seek_high_ground", {"type": "move", "to_cell_id": cell_id},
                  {"mission": _mission_affinity(builder, "seek_high_ground"),
                   "defense": 1.0,
                   "safety": _safety_gain(view, args, cell_id),
                   "cost": builder.cost_value(ap_cost, path_cost)},
                  ["seek_high_ground：%s 对威胁 %s 满足高地阈值"
                   "（#444 high_ground=true）" % (cell_id, threat_id)])


def _gen_avoid_hazard(builder):
    """avoid_hazard：当前格在危险区时，撤到最近的非危险可达格。"""
    view = builder.view
    if not view.is_hazardous(view.actor_cell):
        builder.reject("avoid_hazard", None, [_reason(
            "not_in_hazard",
            "当前格 %s 不在危险区（hazard_refs 为空），无需规避"
            % (view.actor_cell,))])
        return
    ap_cost = builder.ap_cost("move", "avoid_hazard")
    if ap_cost is None:
        return
    rows = _move_rows(
        view, builder.args["reachable"],
        lambda cell_id, cost: None if view.is_hazardous(cell_id) else cost)
    if not rows:
        builder.reject("avoid_hazard", None, [_reason(
            "no_safe_cell_reachable", "可达集中没有非危险格（被堵死在危险区）")])
        return
    _value, path_cost, cell_id = rows[0]
    builder.score("avoid_hazard", {"type": "move", "to_cell_id": cell_id},
                  {"mission": _mission_affinity(builder, "avoid_hazard"),
                   # 脱离危险区是明确的安全改善：固定高于一切地形收益的
                   # 安全分量（是否进攻由人格攻击权重另行竞争——猛攻型
                   # 可能留在危险区强攻，可解释）。
                   "safety": 2.0,
                   "cost": builder.cost_value(ap_cost, path_cost)},
                  ["avoid_hazard：当前格 %s 在危险区（hazard_refs），撤离到 %s"
                   % (view.actor_cell, cell_id)])


def _gen_use_environment(builder):
    """use_environment：对交互可达范围内物件的合法转换生成互动候选。"""
    view = builder.view
    args = builder.args
    ap_cost = builder.ap_cost("assist_action", "use_environment")
    if ap_cost is None:
        return
    if not view.definitions:
        builder.reject("use_environment", None, [_reason(
            "object_definitions_not_provided",
            "调用方未提供物件定义集合（#421 legal_triggers 需要定义），"
            "不猜测互动入口")])
        return
    best = None
    for instance in view.objects:
        object_id = instance.get("object_id")
        distance = _hex_distance(view.battlefield, view.actor_cell,
                                 instance.get("cell_id"))
        if distance is None or distance > args["interact_reach"]:
            continue
        definition = view.definitions.get(instance.get("definition_id")) or {}
        for trigger in tactical_objects.legal_triggers(definition,
                                                       instance.get("state")):
            entry = (distance, str(object_id), trigger)
            if best is None or entry < best[0]:
                best = (entry, object_id, trigger, distance)
    if best is None:
        builder.reject("use_environment", None, [_reason(
            "no_interactable_in_reach",
            "交互可达口径 %d 格内没有处于合法转换态的物件"
            % (args["interact_reach"],))])
        return
    _entry, object_id, trigger, distance = best
    builder.score("use_environment",
                  {"type": "interact", "object_id": object_id,
                   "action": trigger},
                  {"mission": _mission_affinity(builder, "use_environment"),
                   "cost": builder.cost_value(ap_cost, None)},
                  ["use_environment：物件 %s 距离 %d 格，处于合法转换态"
                   "（#421 legal_triggers：%s）" % (object_id, distance, trigger)])


def _gen_protect_ally(builder):
    """protect_ally：移动到「队友相邻格且比当前位置更接近该队友的威胁」的格。

    位置代理 docs/system/04 D1「挡在中间」：守护人格把自己插到威胁
    与守护对象之间；规则层没有仇恨数据，威胁 = 队友的最近清醒队伍目标。
    """
    view = builder.view
    if not view.allies:
        builder.reject("protect_ally", None, [_reason(
            "no_valid_target", "没有存活的敌方队友可保护")])
        return
    ap_cost = builder.ap_cost("move", "protect_ally")
    if ap_cost is None:
        return
    best = None
    for ally_id, ally_cell in view.allies:
        threat = view.nearest_threat(ally_cell)
        if threat is None:
            continue
        threat_id, threat_cell, _distance = threat
        actor_to_threat = _hex_distance(view.battlefield, view.actor_cell,
                                        threat_cell)
        for option in builder.args["reachable"]:
            cell_id = option.get("cell_id")
            if _hex_distance(view.battlefield, cell_id, ally_cell) != 1:
                continue  # 只考虑贴身位（相邻队友）
            new_to_threat = _hex_distance(view.battlefield, cell_id, threat_cell)
            if new_to_threat is None or (actor_to_threat is not None
                                         and new_to_threat >= actor_to_threat):
                continue
            entry = (new_to_threat, int(option.get("cost") or 0), cell_id)
            if best is None or entry < best[0]:
                best = (entry, cell_id, ally_id, threat_id,
                        int(option.get("cost") or 0))
    if best is None:
        builder.reject("protect_ally", None, [_reason(
            "no_intercept_position", "可达集中没有可改善保护态势的贴身位")])
        return
    _entry, cell_id, ally_id, threat_id, path_cost = best
    builder.score("protect_ally", {"type": "move", "to_cell_id": cell_id},
                  {"mission": _mission_affinity(builder, "protect_ally"),
                   "safety": _safety_gain(view, builder.args, cell_id),
                   "cost": builder.cost_value(ap_cost, path_cost)},
                  ["protect_ally：移动到 %s，插到队友 %s 与威胁 %s 之间"
                   % (cell_id, ally_id, threat_id)])


def _gen_rescue_ally(builder):
    """rescue_ally：向倒下的队友推进（规则当前无复活动作 → 只做贴近移动）。"""
    view = builder.view
    if not view.downed_allies:
        builder.reject("rescue_ally", None, [_reason(
            "no_valid_target", "没有倒下未死的队友可接近")])
        return
    ap_cost = builder.ap_cost("move", "rescue_ally")
    if ap_cost is None:
        return
    best = None
    for ally_id, ally_cell in view.downed_allies:
        current = _hex_distance(view.battlefield, view.actor_cell, ally_cell)
        for option in builder.args["reachable"]:
            cell_id = option.get("cell_id")
            distance = _hex_distance(view.battlefield, cell_id, ally_cell)
            if distance is None or (current is not None and distance >= current):
                continue
            entry = (distance, int(option.get("cost") or 0), cell_id)
            if best is None or entry < best[0]:
                best = (entry, cell_id, ally_id, current, distance,
                        int(option.get("cost") or 0))
    if best is None:
        builder.reject("rescue_ally", None, [_reason(
            "no_legal_move", "可达集中没有更接近倒下队友的格")])
        return
    _entry, cell_id, ally_id, current, distance, path_cost = best
    builder.score("rescue_ally", {"type": "move", "to_cell_id": cell_id},
                  {"mission": _mission_affinity(builder, "rescue_ally"),
                   "cost": builder.cost_value(ap_cost, path_cost)},
                  ["rescue_ally：向倒下的队友 %s 推进（距离 %s → %s；"
                   "规则当前无复活动作，只做合法贴近移动）"
                   % (ally_id, current, distance)])


def _gen_control_target(builder):
    """control_target：控场人格的分断位（到全部清醒威胁距离和最小的格）。

    位置代理 docs/system/04 B4「分化」：切断目标间通路；不读任何
    队伍单位属性——目标函数只用位置事实。
    """
    view = builder.view
    if not view.threats:
        builder.reject("control_target", None, [_reason(
            "no_valid_target", "没有清醒的队伍目标可控制")])
        return
    current = view.threat_distance_sum(view.actor_cell)

    def key(cell_id, _cost):
        after = view.threat_distance_sum(cell_id)
        if after is None or (current is not None and after >= current):
            return None
        return after

    def reasons(cell_id):
        return ["control_target：移动到分断位 %s（到全部清醒威胁距离和 "
                "%s → %s，切断目标间通路）"
                % (cell_id, current, view.threat_distance_sum(cell_id))]

    _gen_move_candidate(builder, "control_target", "move", key, reasons)


def _gen_end_turn(builder):
    """end_turn：永远合法的兜底候选（0 分基线）。"""
    builder.score("end_turn", {"type": "end_turn"}, {},
                  ["end_turn：结束回合（总是合法的兜底）"])


def _unsupported_kinds():
    """恢复类候选的数据支持检查（数据驱动，不写死结论）。"""
    actions = supported_actions()
    return [{
        "kind": kind,
        "reason": "action_economy.json 的 actions 里没有 %r 动作：规则当前"
                  "不支持，不伪造候选；数据落地后自动纳入" % (kind,),
        "rule_ref": _RULE_REFS["action_economy"],
    } for kind in RECOVERY_KINDS if kind not in actions]


# ── 主入口 ──────────────────────────────────────────────────────────────

def decide_action(battlefield, battle_state, units, *, actor_id,
                  movement_budget, ap_available,
                  attack_reach=DEFAULT_ATTACK_REACH,
                  ranged_reach=None, interact_reach=DEFAULT_ATTACK_REACH,
                  definitions=None, movement_allowed_modes=(),
                  movement_capabilities=(), rules=None, weights=None,
                  policy=None, seed=None, distance_unit=None,
                  actor_body_size=None, target_body_size=None):
    """为一个敌体单位做一次战术决策（确定性；同输入同输出）。

    参数：

    - `battlefield`：已加载的静态战场（`tactical.battlefield`，只读）；
    - `battle_state`：运行时战斗状态（`tactical.battle_state`，只读）；
    - `units`：单位注册表 `{unit_id: {"party": bool, "unit": dict}}`
      （同 #451 服务层 `record["units"]` 形状；队伍单位传 live.party 的
      原 dict 引用即可——本模块只读 `dead` / `downed` 标志，
      **不读**其他单位的属性与活力）；
    - `actor_id`：做决策的敌体 `unit_id`；
    - `movement_budget`：本回合移动预算（metric 厘米 / abstract 格步，
      口径同 #419）；
    - `ap_available`：本回合剩余 AP（动作 AP 价读 action_economy.json）；
    - `attack_reach` / `interact_reach`：近战与交互可达口径（登记口径，
      缺省相邻格，同 #451 `ATTACK_REACH_CELLS`）；
    - `ranged_reach`：远程射程（**必须由调用方显式声明**——武器射程表
      属世界模组数据尚未落库；`None` 时远程候选显式登记不生成）；
    - `definitions`：物件定义集合（#421 `legal_triggers` 需要；
      缺省无 → 互动候选显式登记不生成）；
    - `movement_allowed_modes` / `movement_capabilities`：同 #419；
    - `rules`：`system.tactical_space`，缺省读 `space_rules()`；
    - `weights`：评分权重覆盖（对人格权重的部分覆盖）；
    - `policy`：策略覆盖（`top_n_random` / `flee_vitality_ratio` /
      `phase_offense_boost` / `safety_distance_norm`）；
    - `seed`：确定性测试入口；只在 `policy.top_n_random > 1` 时消费；
    - `distance_unit` / `*_body_size`：#444 查询口径（abstract 战场必须
      声明格 / 步口径；metric 战场需体型档，缺 sight 配置时空间候选
      结构化失败透传——冻结口径）。

    返回 dict（键序按 `DECISION_FIELDS` 冻结）：

    - `status`：`ok` / `fallback` / `no_action`；
    - `selected`：选中候选（`candidates` 之一的副本）或 `None`；
    - `candidates`：**全部**候选（含被拒的），scored 按评分降序在前、
      rejected 在后，各带 `score_breakdown` / `reasons` /
      `reject_reasons`；
    - `unsupported_kinds`：规则数据未支持的候选类型及原因；
    - `facts_basis`：本次评分消费的规则组件清单（可审计「只用了
      服务端可见事实」）。

    意图形状（#426 管线入口）：`{"type": "move", "to_cell_id"}` /
    `{"type": "attack", "target_id"}` / `{"type": "interact", "object_id",
    "action"}` / `{"type": "end_turn"}`——不携带任何引擎特定字段；
    实际结算仍由服务层经权限、revision、idempotency、TacticalContext
    与 d20 / 构建 engine adapter 完成。
    """
    if rules is None:
        rules = tactical_battlefield.space_rules()
    merged_policy = _merge(POLICY_DEFAULTS, policy)
    state = battle_state if isinstance(battle_state, dict) else {}
    field = battlefield if isinstance(battlefield, dict) else {}
    decision = {
        "schema_version": AI_SCHEMA_VERSION,
        "kind": AI_KIND,
        "status": "ok",
        "battle_id": state.get("battle_id"),
        "battlefield_id": state.get("battlefield_id")
                          or field.get("battlefield_id"),
        "state_revision": state.get("state_revision"),
        "actor_id": actor_id,
        "persona": None,
        "seed": seed,
        "selected": None,
        "candidates": [],
        "fallback_used": False,
        "reasons": [],
        "unsupported_kinds": _unsupported_kinds(),
        "facts_basis": [],
        "rule_refs": dict(_RULE_REFS),
    }

    view = _View(field, state, units, actor_id, definitions, rules)
    if view.actor_cell is None:
        decision["status"] = "no_action"
        decision["reasons"].append(_reason(
            "unknown_actor", "参与方里没有 actor_id=%r" % (actor_id,)))
        return _ordered(decision, DECISION_FIELDS)
    if view.actor_dead or view.actor_downed:
        decision["status"] = "no_action"
        decision["reasons"].append(_reason(
            "unit_cannot_act", "%s 已%s，无法行动"
            % (actor_id, "阵亡" if view.actor_dead else "倒下")))
        return _ordered(decision, DECISION_FIELDS)

    persona = str((view.actor_unit or {}).get("persona") or "") or "default"
    decision["persona"] = persona
    weights_map = _weights_for(persona, weights)
    # 首领阶段（docs/system/01 阶段表；#451 结算里 boss_phase ≥ 1 攻击
    # +1 骰的既有口径）：AI 侧只上调攻击权重，不改合法性。
    if int((view.actor_unit or {}).get("boss_phase") or 0) >= 1:
        weights_map = dict(weights_map)
        weights_map["offense"] = float(
            weights_map.get("offense", 1.0)) * float(
            merged_policy["phase_offense_boost"])
        decision["reasons"].append(_reason(
            "boss_phase", "首领阶段（boss_phase ≥ 1）：攻击权重 ×%s"
            % (merged_policy["phase_offense_boost"],)))
    vitality = int((view.actor_unit or {}).get("vitality") or 0)
    max_vitality = int((view.actor_unit or {}).get("max_vitality") or 0) or 1
    if max_vitality and vitality / float(max_vitality) \
            <= float(merged_policy["flee_vitality_ratio"]):
        decision["reasons"].append(_reason(
            "low_vitality", "自身活力比 ≤ %s：逃逸权重上调（只读自身状态）"
            % (merged_policy["flee_vitality_ratio"],)))

    args = {
        "persona": persona,
        "policy": merged_policy,
        "attack_reach": int(attack_reach),
        "ranged_reach": ranged_reach,
        "interact_reach": int(interact_reach),
        "distance_unit": distance_unit,
        "actor_body_size": actor_body_size,
        "target_body_size": target_body_size,
        "movement_budget": movement_budget,
        "ap_available": int(ap_available),
        "reachable": [],
    }

    try:
        reachable = _plan_reach(view, movement_budget,
                                movement_allowed_modes,
                                movement_capabilities)
        # 危险格整体排除（验收：避开明显致命危险）；进入危险区的提案
        # 不进入评分——撤离与绕行仍由其余候选承担。
        args["reachable"] = [option for option in reachable
                             if not view.is_hazardous(option.get("cell_id"))]
        builder = _Builder(view, args, weights_map)
        _gen_attack(builder, "melee_attack", args["attack_reach"])
        if ranged_reach is None:
            # 武器射程表未落库：调用方未声明射程 → 远程候选显式登记不生成。
            builder.reject("ranged_attack", None, [_reason(
                "ranged_reach_not_declared",
                "武器射程表未落库，调用方未声明 ranged_reach：远程候选不生成"
                "（登记口径同服务层近战可达常量）")])
        else:
            _gen_attack(builder, "ranged_attack",
                        args["attack_reach"] + int(ranged_reach))
        _gen_approach(builder)
        _gen_away(builder, "retreat")
        _gen_seek_cover(builder)
        _gen_seek_high_ground(builder)
        _gen_avoid_hazard(builder)
        _gen_use_environment(builder)
        _gen_protect_ally(builder)
        _gen_rescue_ally(builder)
        _gen_control_target(builder)
        _gen_away(builder, "flee")
        _gen_end_turn(builder)
        decision["facts_basis"].extend([
            "tactical_movement.plan_move",
            "tactical_queries.query_attack",
            "tactical_objects.occupancy_map",
            "tactical_objects.legal_triggers",
            "battle_state.participants/objects（服务端可见事实）",
        ])
    except Exception as error:  # noqa: BLE001 —— 降级不阻塞战斗（口径同 #451）
        decision["status"] = "fallback"
        decision["fallback_used"] = True
        decision["reasons"].append(_reason(
            "ai_scoring_failed",
            "候选生成 / 评分意外失败，降级到既有最小确定性策略: %s: %s"
            % (type(error).__name__, error)))
        return _ordered(_fallback_intent(decision, view, allowed_modes=movement_allowed_modes,
                                         capabilities=movement_capabilities),
                        DECISION_FIELDS)

    decision["candidates"] = _select_and_order(builder.candidates,
                                               merged_policy, seed)
    for candidate in decision["candidates"]:
        if candidate["status"] == "scored":
            decision["selected"] = candidate
            break
    if decision["selected"] is None:
        decision["status"] = "fallback"
        decision["fallback_used"] = True
        decision["reasons"].append(_reason(
            "no_scored_candidate",
            "没有任何候选获得评分（理论不可达：end_turn 永远合法）"))
    return _ordered(decision, DECISION_FIELDS)


def _select_and_order(candidates, policy, seed):
    """候选排序：scored 按 (−score, kind, 目标键)，rejected 在后。

    `top_n_random > 1` 且给了 `seed` 时，在最高分区做有种子选择——
    「偶尔用一个次优行为」的确定性入口（执行提醒 3）。
    """
    def sort_key(candidate):
        if candidate["status"] != "scored":
            return (1, 0, candidate["kind"], "")
        intent = candidate.get("intent") or {}
        target = str(intent.get("target_id") or intent.get("to_cell_id")
                     or intent.get("object_id") or "")
        return (0, -candidate["score"], candidate["kind"], target)

    ordered = sorted(candidates, key=sort_key)
    scored = [item for item in ordered if item["status"] == "scored"]
    top_n = int(policy.get("top_n_random") or 1)
    if top_n > 1 and seed is not None and len(scored) > 1:
        rng = random.Random(str(seed))
        top_score = scored[0]["score"]
        pool = [item for item in scored if item["score"] == top_score]
        if len(pool) < 2:
            pool = scored[:min(top_n, len(scored))]
        choice = rng.choice(pool)
        ordered = [choice] + [item for item in ordered if item is not choice]
    return ordered


def _fallback_intent(decision, view, *, allowed_modes=(), capabilities=()):
    """降级路径：既有最小确定性策略（朝最近目标挪一格，否则结束回合）。

    与 #451 `_enemy_step` 同口径；同样失败 → end_turn。战斗不因 AI
    故障阻塞。
    """
    fallback = None
    try:
        if view.threats and view.actor_cell is not None:
            target_id, target_cell, current = view.nearest_threat(view.actor_cell)
            options = _plan_reach(view, 1, allowed_modes, capabilities)
            best = None
            for option in options:
                cell_id = option.get("cell_id")
                distance = _hex_distance(view.battlefield, cell_id, target_cell)
                if distance is None or distance >= current:
                    continue
                entry = (distance, int(option.get("cost") or 0), cell_id)
                if best is None or entry < best[0]:
                    best = (entry, cell_id)
            if best is not None:
                fallback = _ordered({
                    "kind": "approach",
                    "status": "scored",
                    "intent": {"type": "move", "to_cell_id": best[1]},
                    "score": 0.0,
                    "score_breakdown": {},
                    "reasons": ["降级最小策略：朝最近目标 %s 挪一格"
                                "（同 #451 _enemy_step 口径）" % (target_id,)],
                    "reject_reasons": [],
                }, CANDIDATE_FIELDS)
    except Exception:  # noqa: BLE001 —— 降级里的降级：结束回合
        fallback = None
    if fallback is None:
        fallback = _ordered({
            "kind": "end_turn",
            "status": "scored",
            "intent": {"type": "end_turn"},
            "score": 0.0,
            "score_breakdown": {},
            "reasons": ["降级兜底：结束回合（总是合法）"],
            "reject_reasons": [],
        }, CANDIDATE_FIELDS)
    decision["candidates"] = [fallback]
    decision["facts_basis"].append("tactical_movement.plan_move（降级路径）")
    decision["selected"] = fallback
    return decision
