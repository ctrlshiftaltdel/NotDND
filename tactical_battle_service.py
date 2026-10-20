#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新式六角格战斗的服务端闭环（Issue #426，M13-B11）。

把 #446 战场生成、#447 TacticalContext、#419 移动、#421 物件与 #424/#425
规则适配器接成一条**可完成一场战斗**的服务端管线：客户端只提交行动意图
与目标（cell_id / target_id / object_id+action），骰点、命中档、伤害、
移动后坐标、AP 扣除与物件触发结果全部由服务端重算——本模块不接收任何
客户端结果字段作为权威输入。

分层（不重复造轮子）：

- **几何 / 查询 / 移动 / 物件**：只 import 既有战术模块（禁改禁复制）；
  空间事实一律经 #447 `build_context`，移动一律经 #419 `plan_move`，
  物件状态机一律经 #421 `interact` / `trigger_hazard`；
- **规则结算**：一律经 #424 `tactical_tactics_adapter`（d20）或
  #425 `tactical_forge_adapter`（骰池）调 #423 `prism_core`——本模块
  零公式，不写命中档 / 伤害 / 韧性逻辑；
- **权威写入**：战斗记录整体存进会话 `tactical` 块（新式战斗才创建），
  由网页层经**状态层原子提交**落盘（A0：一次提交 = 一条事件；
  幂等键 / `base_seq` 并发校验沿用既有机制，本模块不另造写入通道）；
- **队伍单位不复制**：角色属性 / 活力 / 状态 / 装备仍以 `rules.party`
  为权威（本模块只持有引用，结算时传入 live 会话里的原 dict）；
  敌体是战斗作用域实体，权威在本记录的 `units` 注册表（别处没有副本）。

回合生命周期（02C / docs/system/01 回合结构）：

- 先攻一次性掷骰（`prism_core.roll_initiative`，服务端确定性 RNG），
  整场战斗保持；`battle_state.round/turn/active_unit_id` 随结束回合推进；
- 每回合 AP 上限与各动作 AP 价一律读 `data/system/action_economy.json`
  （数据驱动，不写死）；
- 敌方回合是最小确定性策略（朝最近目标移动一格，相邻则攻击），
  不作 AI 承诺——复杂敌体 AI 属后续 Issue。

已知口径（登记，待数据单落地后替换）：

- **默认战场模板与物件定义**内置为本模块常量（`BATTLE_TEMPLATE` /
  `OBJECT_DEFINITIONS`），开战时**快照进战斗记录**——静态战场由
  模板快照 + seed 重建，模板 / 定义后续版本化到 `data/**` 后，
  进行中的战斗不会因数据更新而静默重生成（记录里带的是快照）；
- **基础攻击可达**：abstract 战场按相邻格（`hex_distance <= 1`）判定；
  武器射程表属世界模组装备数据（尚未落库），落地后从数据读取；
- **abstract 移动预算**：1 AP = 1 格（`move_speed` 的米制口径与格 / 步
  口径不互换，02C:39；换算表待数据单）；
- **metric 视线**：`system.tactical_space.sight` 未落 → 空间查询按
  #444 既有行为**结构化失败透传**（`reasons[]`，不静默取 0）；
  默认模板因此用 abstract 口径。
"""
import copy
import json
import os
import random

import prism_core
import tactical_battlefield
import tactical_battlefield_gen as battlefield_gen
import tactical_context
import tactical_movement
import tactical_objects
import tactical_tactics_adapter

__all__ = [
    "BATTLE_RECORD_KIND",
    "RECORD_SCHEMA_VERSION",
    "EVENT_TYPES",
    "TacticalError",
    "is_active",
    "party_actor",
    "start_battle",
    "restore_battlefield",
    "battle_view",
    "do_move",
    "do_attack",
    "do_interact",
    "do_end_turn",
    "do_flee",
    "scene_for",
    "ap_per_turn",
    "legal_actions",
]

BATTLE_RECORD_KIND = "tactical.battle_record"
RECORD_SCHEMA_VERSION = 1

# 事件类型与 data/schema/tactical.battle_event.schema.json 的受控枚举一致。
EVENT_TYPES = (
    "move_started", "move_committed", "attack_resolved",
    "object_state_changed", "hazard_triggered", "fall_resolved",
    "turn_started", "turn_ended", "combat_started", "combat_ended",
)

STATUS_ACTIVE = "active"
STATUS_ENDED = "ended"
OUTCOMES = ("victory", "defeat", "fled")

ENGINE = "tactics"

# AP 动作 id（data/system/action_economy.json 的 actions[].id）。
MOVE_AP_ACTION = "move"
INTERACT_AP_ACTION = "assist_action"
ATTACK_AP_ACTION = "basic_attack"

# 登记口径：abstract 战场基础攻击的可达判定（相邻格）。武器射程表属
# 世界模组装备数据，尚未落库；落地后本常量由数据替换。
ATTACK_REACH_CELLS = 1

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_ACTION_ECONOMY_PATH = os.path.join(_MODULE_DIR, "data", "system",
                                    "action_economy.json")
_ECONOMY_CACHE = None

# 文档行号引用（登记用，不是规则数值）。
_RULE_REFS = {
    "pipeline": "docs/system/02c-hex-battlefield.md:156",
    "occupancy": "docs/system/02c-hex-battlefield.md:65",
    "melee_reach": "docs/system/02c-hex-battlefield.md:144",
    "turn_structure": "docs/system/01-core.md:287",
    "action_economy": "data/system/action_economy.json",
}


class TacticalError(Exception):
    """战斗服务的结构化拒绝：固定短语 + HTTP 码 + 机器 reason + 可解释明细。

    `details` 会原样并入错误响应体（如 `reasons[]` / 当前
    `state_revision`），供客户端渲染与恢复。
    """

    def __init__(self, phrase, code=400, reason="", details=None):
        super().__init__(phrase)
        self.phrase = str(phrase)
        self.code = int(code)
        self.reason = str(reason)
        self.details = dict(details or {})


# ── AP 经济（数据驱动）──────────────────────────────────────────────────

def _action_economy():
    global _ECONOMY_CACHE
    if _ECONOMY_CACHE is None:
        with open(_ACTION_ECONOMY_PATH, encoding="utf-8") as handle:
            _ECONOMY_CACHE = json.load(handle)
    return _ECONOMY_CACHE


def ap_per_turn() -> int:
    """每回合 AP 上限（data/system/action_economy.json#/round/ap_per_turn）。"""
    return int(_action_economy()["round"]["ap_per_turn"])


def ap_cost(action_id: str) -> int:
    """一个动作的 AP 价（action_economy.json#/actions）。未知动作显式报错。"""
    for entry in _action_economy()["actions"]:
        if str(entry.get("id") or "") == action_id:
            cost = entry.get("ap")
            if cost is None:
                raise TacticalError("这个动作不消耗行动点", 400, "no_ap_cost")
            return int(cost)
    raise TacticalError("没有这个动作", 404, "no_action")


def legal_actions() -> list:
    """当前合法动作与 AP 价（数据投影；错误码语义见各路由 docstring）。"""
    rows = []
    for action_id in (MOVE_AP_ACTION, ATTACK_AP_ACTION, INTERACT_AP_ACTION):
        rows.append({"action": action_id, "ap_cost": ap_cost(action_id)})
    return rows


# ── 内置战场模板与物件定义（开战时快照进记录）────────────────────────────

BATTLE_TEMPLATE = {
    "template_id": "service-default-room",
    "template_version": "1",
    "shape": {"kind": "room"},
    "size": {"width": 7, "height": 5},
    "entry_cells": [
        {"q": 0, "r": 1},
        {"q": 0, "r": 2},
        {"q": 0, "r": 3},
        {"q": 1, "r": 2},
        {"q": 1, "r": 1},
        {"q": 1, "r": 3},
    ],
    "spawn_points": [
        {"q": 6, "r": 2},
        {"q": 5, "r": 1},
        {"q": 5, "r": 3},
        {"q": 6, "r": 0},
        {"q": 6, "r": 4},
        {"q": 4, "r": 2},
    ],
    "features": [
        {
            "feature_id": "cover-line",
            "kind": "terrain",
            "at": {"q": 3, "r": 2},
            "terrain": {
                "terrain_id": "barricade-line",
                "terrain_tags": ["open"],
                "cover_grade": "medium",
                "obstacle_height": 90,
            },
        },
        {
            "feature_id": "vent-hazard-zone",
            "kind": "terrain",
            "at": {"q": 0, "r": 0},
            "terrain": {
                "terrain_id": "steam-vent-zone",
                "terrain_tags": ["open"],
                "hazard_refs": ["steam-vent-01"],
            },
        },
        {
            "feature_id": "steam-vent",
            "kind": "object",
            "at": {"q": 0, "r": 0},
            "object": {
                "definition_id": "hazard.steam-vent",
                "category": "hazard",
                "state": "armed",
            },
        },
        {
            "feature_id": "blast-gate",
            "kind": "object",
            "at": {"q": 5, "r": 2},
            "object": {
                "definition_id": "mech.blast-gate",
                "category": "mechanism",
                "state": "closed",
                "blocks_movement": True,
            },
        },
    ],
    "encounter_meta": {"enemy_min": 1, "enemy_max": 3, "waves": 1},
}

# 物件定义（#421 `validate_definition` 契约；开战时随模板一起快照进记录）。
OBJECT_DEFINITIONS = {
    "mech.blast-gate": {
        "definition_id": "mech.blast-gate",
        "kinds": ["mechanism"],
        "initial_state": "closed",
        "states": {
            "closed": {"object": {"blocks_movement": True}},
            "open": {"object": {"blocks_movement": False}},
        },
        "transitions": [{"from": "closed", "to": "open", "trigger": "interact"}],
        "source": "data/system/tactical_space.json",
    },
    "hazard.steam-vent": {
        "definition_id": "hazard.steam-vent",
        "kinds": ["hazard"],
        "initial_state": "armed",
        "states": {
            "armed": {"object": {"triggered": False}},
            "vented": {"object": {"triggered": True}},
        },
        # 物件按 #421 语义占用自身格（实心占格互斥），单位无法「进入」，
        # 所以蒸汽口走 interact 相位（走到相邻开启阀门）。
        "transitions": [{"from": "armed", "to": "vented", "trigger": "interact"}],
        "hazard_refs": ["steam-vent-01"],
        "source": "data/system/tactical_space.json",
    },
}

# 场景特征（gen 的 `_check_scene` 契约：非空 tag）。
SCENE_FEATURES = (
    {"tag": "service-default-battlefield", "text": "默认遭遇战场"},
)

# 模板里格子对象会带 surface_id / category / feature_refs——battle_state
# 的 objects[] 形状（schema additionalProperties=false）不收，投影时丢弃。
_BATTLEFIELD_OBJECT_FIELDS = (
    "object_id", "definition_id", "category", "cell_id", "surface_id",
    "state", "blocks_movement", "blocks_los", "cover_grade", "feature_refs",
)


def scene_for(world_id: str, place_id: str, place_name: str = "") -> dict:
    """战斗场景只读快照（#446 `_check_scene` 契约的最小形态）。"""
    return {
        "world_id": str(world_id or "local"),
        "site_id": str(place_id or "scene"),
        "atlas_ref": str(place_id or "scene"),
        "place": {
            "id": str(place_id or "scene"),
            "name": str(place_name or place_id or "scene"),
            "kind": "room",
            "frame_id": "local",
            "features": [dict(item) for item in SCENE_FEATURES],
        },
        "features": [],
    }


# ── 记录基础 ────────────────────────────────────────────────────────────

def _reason(code, message, **extra):
    item = {"code": code, "message": message}
    item.update(extra)
    return item


def is_active(record) -> bool:
    """当前是否有进行中的新式战斗。"""
    return isinstance(record, dict) and record.get("status") == STATUS_ACTIVE


def party_actor(record, unit_id) -> bool:
    """`unit_id` 是否是本战斗里的队伍单位（玩家只能指挥队伍单位）。"""
    entry = _unit_entry(record, str(unit_id or ""))
    return bool(entry and entry.get("party"))


def _battle_state(record) -> dict:
    return record.get("battle_state") if isinstance(record, dict) else {}


def _participants(record) -> list:
    state = _battle_state(record)
    rows = state.get("participants")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _participant(record, unit_id):
    for row in _participants(record):
        if row.get("unit_id") == unit_id:
            return row
    return None


def _unit_entry(record, unit_id):
    units = record.get("units") if isinstance(record, dict) else None
    if isinstance(units, dict):
        entry = units.get(unit_id)
        if isinstance(entry, dict):
            return entry
    return None


def _resolve_unit(record, live, unit_id):
    """解析一个参战单位的**权威 dict**：队伍 → live.party；敌体 → 记录注册表。

    返回 `(unit_dict, side)`；找不到返回 `(None, None)`。绝不复制——
    结算函数直接改这份 dict，落盘时随 rules 块 / 记录一起原子提交。
    """
    entry = _unit_entry(record, unit_id)
    if entry is None:
        return None, None
    if entry.get("party"):
        for unit in live.party:
            if str(unit.get("id") or "") == unit_id:
                return unit, "party"
        return None, None
    unit = entry.get("unit")
    return (unit if isinstance(unit, dict) else None), "enemy"


def _alive(unit) -> bool:
    return unit is not None and not unit.get("dead") and not unit.get("downed")


def _conscious(unit) -> bool:
    return unit is not None and not unit.get("dead")


def _enemy_ids(record) -> list:
    return sorted(unit_id for unit_id, entry in
                  (record.get("units") or {}).items()
                  if isinstance(entry, dict) and not entry.get("party"))


def _party_ids(record) -> list:
    return sorted(unit_id for unit_id, entry in
                  (record.get("units") or {}).items()
                  if isinstance(entry, dict) and entry.get("party"))


def _unit_display(unit) -> dict:
    """参战单位的可渲染视图（活力 / 状态与既有 combat_view 同粒度）。"""
    if unit is None:
        return {}
    return {
        "name": unit.get("name"),
        "side": unit.get("side"),
        "vitality": int(unit.get("vitality") or 0),
        "max_vitality": int(unit.get("max_vitality") or 0),
        "downed": bool(unit.get("downed")),
        "dead": bool(unit.get("dead")),
    }


def _append_event(record, event) -> None:
    """落一条战斗事件：序号 = last_event_sequence + 1（单调，schema 契约）。"""
    state = _battle_state(record)
    sequence = int(state.get("last_event_sequence") or 0) + 1
    event["sequence"] = sequence
    state["last_event_sequence"] = sequence
    record.setdefault("events", []).append(event)


def _bump_revision(record) -> None:
    """状态修订 +1（每次原子提交一次；调用方在同一提交里带上全部变更）。"""
    state = _battle_state(record)
    state["state_revision"] = int(state.get("state_revision") or 0) + 1


def _check_expected_revision(record, expected_revision) -> None:
    current = int(_battle_state(record).get("state_revision") or 0)
    if expected_revision is None or int(expected_revision) != current:
        raise TacticalError(
            "战场状态已变，请刷新后重试", 409, "stale_revision",
            {"state_revision": current,
             "reasons": [_reason(
                 "revision_mismatch",
                 "expected_revision=%r 与当前 state_revision=%d 不一致"
                 % (expected_revision, current),
                 state_revision=current)]})


def _spend_ap(record, unit_id, action_id) -> int:
    """扣一次动作 AP（数据价目）；余额不足 → 409 `no_ap`。"""
    cost = ap_cost(action_id)
    turn_ap = record.get("turn_ap") or {}
    left = int(turn_ap.get(unit_id) or 0)
    if left < cost:
        raise TacticalError(
            "行动点不足", 409, "no_ap",
            {"reasons": [_reason(
                "no_ap", "%s 需要 %d AP，%s 本回合剩 %d AP"
                % (action_id, cost, unit_id, left),
                action_id=action_id, ap_cost=cost, ap_left=left)]})
    turn_ap[unit_id] = left - cost
    record["turn_ap"] = turn_ap
    return cost


def _require_active_turn(record, actor_id) -> None:
    if not is_active(record):
        raise TacticalError("没有进行中的战斗", 404, "no_battle")
    if not actor_id:
        raise TacticalError("缺少行动者", 400, "no_actor")
    if _battle_state(record).get("active_unit_id") != actor_id:
        raise TacticalError("还没轮到你行动", 409, "not_your_turn",
                            {"active_unit_id":
                             _battle_state(record).get("active_unit_id")})


# ── 开战 ────────────────────────────────────────────────────────────────

def _project_objects(instances, definitions) -> list:
    """gen 产出的物件实例 → battle_state.objects 的 schema 形状。"""
    rows = []
    for instance in sorted(instances, key=lambda item: str(item.get("object_id"))):
        if not isinstance(instance, dict):
            continue
        definition = (definitions or {}).get(instance.get("definition_id")) or {}
        row = {
            "object_id": instance.get("object_id"),
            "definition_id": instance.get("definition_id"),
            "cell_id": instance.get("cell_id"),
            "state": instance.get("state") or definition.get("initial_state")
                     or "intact",
            "blocks_movement": bool(instance.get("blocks_movement")),
            "blocks_los": bool(instance.get("blocks_los")),
            "triggered": False,
        }
        if definition.get("kinds"):
            row["movable"] = "movable" in definition["kinds"]
            row["destructible"] = "destructible" in definition["kinds"]
        rows.append(row)
    return rows


def _initiative_order(record, live, rng_seed) -> list:
    """先攻顺序：一次掷骰整场保持（02A:275）；确定性 RNG，平手按 unit_id。"""
    rng = random.Random("init:%s" % (rng_seed,))
    scores = {}
    for unit_id in sorted(record.get("units") or {}):
        unit, _side = _resolve_unit(record, live, unit_id)
        scores[unit_id] = (prism_core.roll_initiative(unit, rng=rng), unit_id)
    ranked = sorted(scores.items(), key=lambda item: (-item[1][0], item[1][1]))
    return [unit_id for unit_id, (_score, _uid) in ranked]


def start_battle(*, rules_snapshot, scene, battle_id, seed, rng_seed,
                 strength="") -> dict:
    """开一场新式战斗，返回完整的战斗记录（写入 `tactical` 块）。

    - 敌体由服务端按遭遇预算生成（`prism_core.build_encounter`，02A 第九、
      十节）——玩家只能选择「打不打」；预算拿不到 / 生成不出敌体 → 422；
    - 静态战场由内置模板快照 + seed 生成（#446，确定性可复现）；
    - 队伍单位落入口格、敌体落入出生点（生成器保证可用且互斥）；
    - 先攻一次性掷骰；第一回合从先攻最高的单位开始（敌体回合就地跑完，
      直到轮到玩家）。

    本函数只构造记录，**不提交**——落盘走网页层的状态层原子提交。
    """
    live = prism_core.RuleSession.from_snapshot(rules_snapshot)
    try:
        squad = prism_core.build_encounter(live, str(strength or ""))
    except ValueError as exc:
        raise TacticalError(str(exc), 422, "no_enemy") from exc
    if not squad:
        raise TacticalError("这里没有可交战的东西", 422, "no_enemy")

    template = copy.deepcopy(BATTLE_TEMPLATE)
    try:
        generated = battlefield_gen.generate_battlefield(
            scene, template, seed, mode="abstract")
    except battlefield_gen.BattlefieldGenError as exc:
        raise TacticalError("战场生成失败", 422, "battlefield_gen_failed",
                            {"reasons": [_reason("gen_failed", str(exc))]}) from exc
    battlefield = generated["battlefield"]
    definitions = copy.deepcopy(OBJECT_DEFINITIONS)

    party = [unit for unit in live.party if not unit.get("dead")]
    if not party:
        raise TacticalError("队伍里没有能作战的单位", 422, "no_party")

    entry_coord = {("q%d-r%d" % (p["q"], p["r"])) for p in template["entry_cells"]}
    entry_cells = [cell for cell in battlefield["cells"]
                   if "q%d-r%d" % (cell["q"], cell["r"]) in entry_coord]
    by_coord = {(cell["q"], cell["r"]): cell for cell in battlefield["cells"]}
    spawn_cells = [by_coord[(p["q"], p["r"])] for p in template["spawn_points"]
                   if (p["q"], p["r"]) in by_coord]
    if len(entry_cells) != len(template["entry_cells"]) \
            or len(spawn_cells) != len(template["spawn_points"]) \
            or len(party) > len(entry_cells) or len(squad) > len(spawn_cells):
        raise TacticalError("战场落位失败", 500, "placement_failed")

    units = {}
    participants = []
    for index, unit in enumerate(party):
        unit_id = str(unit.get("id") or "")
        if not unit_id:
            raise TacticalError("队伍单位缺少 id", 422, "bad_party")
        units[unit_id] = {"party": True}
        participants.append({"unit_id": unit_id,
                             "cell_id": entry_cells[index % len(entry_cells)]["cell_id"]})
    for index, enemy in enumerate(squad):
        unit_id = str(enemy.get("id") or "")
        units[unit_id] = {"unit": enemy}
        participants.append({"unit_id": unit_id,
                             "cell_id": spawn_cells[index]["cell_id"]})

    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "kind": BATTLE_RECORD_KIND,
        "battle_id": str(battle_id),
        "status": STATUS_ACTIVE,
        "outcome": "",
        "engine": ENGINE,
        "rng_seed": rng_seed,
        "template": template,
        "object_definitions": definitions,
        "battlefield": battlefield,
        "battlefield_meta": {
            "battlefield_id": battlefield.get("battlefield_id"),
            "template_id": template["template_id"],
            "template_version": template["template_version"],
            "seed": seed,
            "mode": (battlefield.get("scale") or {}).get("mode"),
            "generator_version": battlefield.get("generator_version"),
        },
        "battle_state": {
            "schema_version": 1,
            "kind": "tactical.battle_state",
            "battle_id": str(battle_id),
            "battlefield_id": battlefield.get("battlefield_id"),
            "round": 1,
            "turn": 0,
            "active_unit_id": "",
            "state_revision": 0,
            "last_event_sequence": 0,
            "rng_ref": "seed:%s" % (rng_seed,),
            "participants": participants,
            "objects": _project_objects(generated["objects"], definitions),
        },
        "units": units,
        "turn_order": [],
        "turn_ap": {},
        "applied_keys": [],
        "events": [],
    }
    record["turn_order"] = _initiative_order(record, live, rng_seed)

    _append_event(record, {
        "schema_version": 1,
        "kind": "tactical.battle_event",
        "type": "combat_started",
        "rule_engine": ENGINE,
        "expected_revision": 0,
        "result": {
            "summary": "战斗开始：%d 名敌体，参战 %d 人。"
                       % (len(squad), len(party)),
            "enemy_count": len(squad),
            "party_count": len(party),
            "battlefield_id": battlefield.get("battlefield_id"),
            "seed": seed,
        },
    })
    # 推进到第一个可行动单位；敌体回合就地跑完（最小确定性策略）。
    _advance_to_next_actor(record, live, start=True)
    _bump_revision(record)
    return record


def restore_battlefield(record) -> dict:
    """按记录里的模板快照 + seed 重建静态战场（断线 / 重启恢复路径）。

    重建结果必须与记录里的 `battlefield` 同身份（`battlefield_id` 一致）；
    不一致说明模板或生成器变了 → 显式失败，**不静默换图**。校验通过后
    返回记录里存档的静态战场（权威副本；重建只是证明「模板 + seed 仍能
    复现同一张图」，动态对象 / 单位位置由记录与事件恢复，不参与重建）。
    """
    if not isinstance(record, dict):
        raise TacticalError("没有战斗记录", 404, "no_battle")
    template = record.get("template")
    meta = record.get("battlefield_meta") or {}
    try:
        generated = battlefield_gen.generate_battlefield(
            scene_for(str(meta.get("battlefield_id") or "scene"), "restore"),
            template, meta.get("seed"), mode=str(meta.get("mode") or "abstract"))
    except battlefield_gen.BattlefieldGenError as exc:
        raise TacticalError("战场重建失败", 500, "battlefield_rebuild_failed",
                            {"reasons": [_reason("gen_failed", str(exc))]}) from exc
    rebuilt = generated["battlefield"]
    if str(rebuilt.get("battlefield_id") or "") \
            != str(meta.get("battlefield_id") or ""):
        raise TacticalError(
            "战场重建结果与存档不一致，拒绝静默换图", 500,
            "battlefield_rebuild_mismatch",
            {"reasons": [_reason(
                "battlefield_id_mismatch",
                "重建 battlefield_id=%r 与记录 %r 不一致"
                % (rebuilt.get("battlefield_id"), meta.get("battlefield_id")))]})
    return record.get("battlefield")


# ── 视图 ────────────────────────────────────────────────────────────────

def battle_view(record, *, since_event=0, battlefield=None,
                party_units=None) -> dict:
    """可渲染战斗快照：身份 / 修订 / 回合 / 参战者 / 战场 / 物件 / 事件增量。

    `party_units`：网页层从 live 会话解析出的 `{unit_id: unit dict}`——
    队伍单位的属性 / 活力权威在 rules 块，视图只投影展示字段，不复制。
    """
    if not isinstance(record, dict):
        raise TacticalError("没有进行中的战斗", 404, "no_battle")
    state = _battle_state(record)
    if battlefield is None:
        battlefield = record.get("battlefield")
    events = record.get("events") or []
    party_units = party_units if isinstance(party_units, dict) else {}
    rows = []
    for row in _participants(record):
        unit_id = str(row.get("unit_id") or "")
        entry = _unit_entry(record, unit_id)
        if entry is not None and entry.get("party"):
            rows.append({"unit_id": unit_id,
                         "cell_id": row.get("cell_id"),
                         "party": True,
                         "unit": _unit_display(party_units.get(unit_id))})
            continue
        unit = (entry or {}).get("unit")
        rows.append({"unit_id": unit_id,
                     "cell_id": row.get("cell_id"),
                     "party": False,
                     "unit": _unit_display(unit if isinstance(unit, dict) else None)})
    return {
        "kind": BATTLE_RECORD_KIND,
        "battle_id": record.get("battle_id"),
        "status": record.get("status"),
        "outcome": record.get("outcome"),
        "engine": record.get("engine"),
        "battlefield_meta": record.get("battlefield_meta"),
        "state_revision": state.get("state_revision"),
        "last_event_sequence": state.get("last_event_sequence"),
        "round": state.get("round"),
        "turn": state.get("turn"),
        "active_unit_id": state.get("active_unit_id"),
        "turn_order": list(record.get("turn_order") or []),
        "turn_ap": dict(record.get("turn_ap") or {}),
        "participants": rows,
        "battlefield": copy.deepcopy(battlefield),
        "objects": copy.deepcopy(state.get("objects") or []),
        "object_definitions": copy.deepcopy(record.get("object_definitions") or {}),
        "legal_actions": legal_actions(),
        "events_since": [copy.deepcopy(event) for event in events
                         if int(event.get("sequence") or 0) > int(since_event or 0)],
        "recent_events": [copy.deepcopy(event) for event in events[-10:]],
    }


# ── 空间结算（移动 / 攻击共用）──────────────────────────────────────────

def _build_attack_context(record, battlefield, actor_id, target_id) -> dict:
    """一次攻击的 TacticalContext（#447 冻结契约；abstract 需显式格口径）。"""
    mode = tactical_battlefield.scale_mode(battlefield)
    kwargs = {}
    if mode == "abstract":
        kwargs["distance_unit"] = "cell"
    context = tactical_context.build_context(
        battlefield, _battle_state(record), actor_id=actor_id,
        target_id=target_id, **kwargs)
    if context.get("status") != "ok":
        raise TacticalError(
            "空间事实不可用，行动被拒绝", 422, "invalid_context",
            {"reasons": list(context.get("reasons") or []),
             "state_revision": _battle_state(record).get("state_revision")})
    return context


def _attack_inputs(unit) -> dict:
    """一次基础攻击的输入：敌体用模板数值；队伍单位用力道修正（02A:253）。

    伤害骰缺省 1d6——武器表属世界模组装备数据（尚未落库），登记口径。
    """
    if unit.get("side") == "enemy":
        return {"notation": str(unit.get("damage_notation") or "1d6"),
                "damage_bonus": int(unit.get("damage_bonus") or 0),
                "attack_bonus": int(unit.get("attack_bonus") or 0)}
    attrs = unit.get("attributes") or {}
    modifier = prism_core.attribute_modifier(int(attrs.get("MGT", 4) or 4))
    return {"notation": "1d6", "damage_bonus": modifier,
            "attack_bonus": modifier}


def _settle_attack(record, live, battlefield, actor_id, target_id, *, rng) -> dict:
    """一次攻击的完整结算：context → 适配器 → 核心管线（零公式）。

    适配器 `blocked` / `rejected`（视线被遮、完全掩体、context 无效）不掷骰、
    不扣 AP——目标合法性拦截发生在任何副作用之前。
    """
    actor, actor_side = _resolve_unit(record, live, actor_id)
    target, _target_side = _resolve_unit(record, live, target_id)
    if actor is None or not _conscious(actor):
        raise TacticalError("行动者无法行动", 409, "actor_down")
    if target is None or not _conscious(target):
        raise TacticalError("目标不存在", 404, "no_target")
    context = _build_attack_context(record, battlefield, actor_id, target_id)
    reach = int(context.get("hex_distance") or 0)
    if reach > ATTACK_REACH_CELLS:
        raise TacticalError(
            "目标不在可达范围内", 422, "target_out_of_range",
            {"reasons": [_reason(
                "out_of_range",
                "hex_distance=%d 超过基础攻击可达口径 %d（02C:144 近战可达性；"
                "武器射程表待数据单）" % (reach, ATTACK_REACH_CELLS),
                hex_distance=reach, reach=ATTACK_REACH_CELLS,
                rule_ref=_RULE_REFS["melee_reach"])],
             "state_revision": _battle_state(record).get("state_revision")})
    inputs = _attack_inputs(actor)
    out = tactical_tactics_adapter.execute_attack(
        live, actor, target, context, rng=rng, **inputs)
    if out.get("status") == "blocked":
        raise TacticalError(
            "目标无法被攻击", 422, "attack_blocked",
            {"reasons": list(out.get("reasons") or []),
             "state_revision": _battle_state(record).get("state_revision")})
    if out.get("status") == "rejected":
        raise TacticalError(
            "空间上下文被拒绝", 422, "context_rejected",
            {"reasons": list(out.get("reasons") or []),
             "state_revision": _battle_state(record).get("state_revision")})
    if out.get("status") != "ok":
        raise TacticalError("服务器内部错误：适配器未完成结算", 500,
                            "adapter_failed",
                            {"reasons": list(out.get("reasons") or [])})
    return out


def _attack_event(record, live, actor_id, target_id, adapter_out) -> dict:
    """把一次已结算的攻击落成 `attack_resolved` 事件（schema 封套）。"""
    summary = adapter_out.get("summary") or {}
    state = _battle_state(record)
    actor, _ = _resolve_unit(record, live, actor_id)
    text = "%s 攻击 %s：%s" % (
        (actor or {}).get("name") or actor_id, target_id,
        summary.get("grade_zh") or summary.get("grade") or "结算完成")
    if summary.get("damage"):
        text += "，伤害 %s" % summary["damage"]
    return {
        "schema_version": 1,
        "kind": "tactical.battle_event",
        "type": "attack_resolved",
        "actor_id": actor_id,
        "target_id": target_id,
        "rule_engine": ENGINE,
        "expected_revision": int(state.get("state_revision") or 0),
        "result": {
            "summary": text,
            "grade": summary.get("grade"),
            "damage": summary.get("damage"),
            "high_ground": summary.get("high_ground"),
            "poise_broken": summary.get("poise_broken"),
            "ap_action": ATTACK_AP_ACTION,
        },
        "source_rule_ref": "data/system/tactics_combat.json",
    }


# ── 移动 ────────────────────────────────────────────────────────────────

def _plan_move(record, battlefield, actor_id, unit, to_cell_id):
    """服务端重算移动（#419）：预算 1 AP = 1 格（abstract，登记口径）。"""
    mode = tactical_battlefield.scale_mode(battlefield)
    if mode == "abstract":
        budget = 1
    else:
        budget = max(1, prism_core.move_speed(unit)) * 100
    occupied = tactical_objects.occupancy_map(_battle_state(record), battlefield,
                                              exclude=[actor_id])
    try:
        return tactical_movement.plan_move(
            battlefield, actor_id,
            _participant(record, actor_id).get("cell_id"),
            [{"q": 0, "r": 0}], budget, occupied=occupied,
            destination=to_cell_id)
    except ValueError as exc:
        raise TacticalError("移动提案不合法", 422, "bad_move",
                            {"reasons": [_reason("plan_failed", str(exc))],
                             "state_revision":
                             _battle_state(record).get("state_revision")}) from exc


def _apply_move(record, live, battlefield, actor_id, to_cell_id) -> dict:
    """移动结算 + `move_committed` 事件；返回事件摘要。"""
    unit, _side = _resolve_unit(record, live, actor_id)
    if unit is None or not _alive(unit):
        raise TacticalError("行动者无法行动", 409, "actor_down")
    plan = _plan_move(record, battlefield, actor_id, unit, to_cell_id)
    if plan.get("total_cost") is None:
        raise TacticalError(
            "目标格不可达", 422, "destination_unreachable",
            {"reasons": [_reason(
                "destination_unreachable",
                "%s 在预算与地形 / 占格约束下不可达" % (to_cell_id,),
                cell_id=to_cell_id)],
             "movement_blocked": list(plan.get("blocked") or []),
             "state_revision": _battle_state(record).get("state_revision")})
    path = list(plan.get("path") or [])
    falls = list(plan.get("falls") or [])
    participant = _participant(record, actor_id)
    from_cell_id = participant.get("cell_id")
    participant["cell_id"] = path[-1] if path else to_cell_id
    state = _battle_state(record)
    _append_event(record, {
        "schema_version": 1,
        "kind": "tactical.battle_event",
        "type": "move_committed",
        "actor_id": actor_id,
        "from_cell_id": from_cell_id,
        "to_cell_id": participant["cell_id"],
        "rule_engine": ENGINE,
        "expected_revision": int(state.get("state_revision") or 0),
        "result": {
            "summary": "%s 移动到 %s（路径成本 %s）"
                       % (actor_id, participant["cell_id"], plan.get("total_cost")),
            "path": path,
            "path_cost": plan.get("total_cost"),
            "falls": falls,
            "ap_action": MOVE_AP_ACTION,
        },
        "source_rule_ref": "data/system/tactical_space.json#/movement",
    })
    # 坠落 / 危险地形是提案层：伤害结算属后续管线；这里只记录提案。
    return {"path": path, "path_cost": plan.get("total_cost"), "falls": falls}


def _trigger_hazards(record, battlefield, actor_id, cell_id) -> int:
    """进入格后的危险结算（#421 `trigger_hazard`；一次性效果幂等）。"""
    applied = 0
    while True:
        proposal = tactical_objects.trigger_hazard(
            _battle_state(record), battlefield,
            definitions=record.get("object_definitions") or {},
            unit_id=actor_id, cell_id=cell_id, phase="enter",
            applied_keys=tuple(record.get("applied_keys") or ()))
        if not proposal.get("accepted"):
            return applied
        _apply_object_proposal(record, proposal)
        applied += 1
        if not proposal.get("deferred_object_ids"):
            return applied


def _apply_object_proposal(record, proposal) -> None:
    """把 #421 的已接受提案应用进记录：对象补丁 + 格增量 + 事件封套。"""
    state = _battle_state(record)
    for object_id, patch in (proposal.get("state_delta") or {}).items():
        for instance in state.get("objects") or []:
            if instance.get("object_id") == object_id:
                instance.update(copy.deepcopy(patch))
    if proposal.get("cell_deltas"):
        battlefield = record.get("battlefield")
        by_id = {cell.get("cell_id"): cell for cell in battlefield.get("cells") or []}
        for delta in proposal["cell_deltas"]:
            cell = by_id.get(delta.get("cell_id"))
            if cell is None:
                continue
            for field, value in (delta.get("set") or {}).items():
                cell[field] = copy.deepcopy(value)
            for field in delta.get("unset") or ():
                cell.pop(field, None)
    for key in [proposal.get("idempotency_key")]:
        if key:
            record.setdefault("applied_keys", []).append(key)
    for event in proposal.get("events") or []:
        _append_event(record, copy.deepcopy(event))


# ── 胜负与回合推进 ──────────────────────────────────────────────────────

def _end_battle(record, live, outcome) -> dict:
    """战斗收尾：胜负判定 / 经验与压力结算（PRISM 既有规则）+ 收尾事件。"""
    record["status"] = STATUS_ENDED
    record["outcome"] = str(outcome)
    state = _battle_state(record)
    summary_text = {
        "victory": "战斗胜利。",
        "defeat": "队伍全灭，这一程到此为止。",
        "fled": "队伍脱离了战斗（没有获得经验）。",
    }.get(str(outcome), "战斗结束。")
    result = {"summary": summary_text, "outcome": str(outcome)}
    if outcome == "victory":
        xp_total = 0
        for unit_id in _enemy_ids(record):
            unit = (_unit_entry(record, unit_id) or {}).get("unit") or {}
            if unit.get("dead"):
                xp_total += int(unit.get("xp") or 0)
        alive = [unit for unit in live.party if not unit.get("dead")]
        share = xp_total // max(1, len(alive))
        for unit in alive:
            prism_core.award_xp(live, unit, share)
        result["xp_total"] = xp_total
        result["xp_each"] = share
        prism_core.tick_pressure(live, 1)
    elif outcome == "fled":
        prism_core.tick_pressure(live, 1)
    _append_event(record, {
        "schema_version": 1,
        "kind": "tactical.battle_event",
        "type": "combat_ended",
        "rule_engine": ENGINE,
        "expected_revision": int(state.get("state_revision") or 0),
        "result": result,
        "source_rule_ref": "data/system/tactics_encounter.json",
    })
    return result


def _check_battle_end(record, live):
    """共用胜负检查点（口径同 #423 `_check_combat_end`）：别停在死局里。"""
    if not is_active(record):
        return True
    enemies = [(_unit_entry(record, unit_id) or {}).get("unit") or {}
               for unit_id in _enemy_ids(record)]
    if enemies and all(unit.get("dead") for unit in enemies):
        _end_battle(record, live, "victory")
        return True
    if all(unit.get("dead") for unit in live.party):
        _end_battle(record, live, "defeat")
        return True
    return False


def _refresh_ap(record, unit_id) -> None:
    record.setdefault("turn_ap", {})[unit_id] = ap_per_turn()


def _enemy_turn(record, live, battlefield, enemy_id) -> list:
    """敌方回合（最小确定性策略）：朝最近目标移动，相邻则攻击。

    不作 AI 承诺——复杂敌体战术人格 / 行为卡属后续 Issue；这里只保证
    服务端自己会动、会打、会停（AP 用完 / 无人可打 / 战斗结束）。
    """
    enemy, _side = _resolve_unit(record, live, enemy_id)
    if enemy is None or not _alive(enemy):
        return []
    _refresh_ap(record, enemy_id)
    while True:
        if not _alive(enemy):
            return []
        targets = []
        for row in _participants(record):
            unit_id = str(row.get("unit_id") or "")
            unit, side = _resolve_unit(record, live, unit_id)
            if side == "party" and _conscious(unit):
                targets.append((unit_id, row.get("cell_id")))
        if not targets:
            return []
        from_cell = _participant(record, enemy_id).get("cell_id")
        ranked = sorted(
            targets,
            key=lambda item: (tactical_hex_distance(battlefield, from_cell,
                                                    item[1]), item[0]))
        target_id, target_cell = ranked[0]
        distance = tactical_hex_distance(battlefield, from_cell, target_cell)
        try:
            if distance <= ATTACK_REACH_CELLS \
                    and int((record.get("turn_ap") or {}).get(enemy_id) or 0) \
                    >= ap_cost(ATTACK_AP_ACTION):
                _spend_ap(record, enemy_id, ATTACK_AP_ACTION)
                out = _settle_attack(record, live, battlefield, enemy_id,
                                     target_id, rng=_event_rng(record))
                _append_event(record,
                              _attack_event(record, live, enemy_id, target_id, out))
            elif distance > ATTACK_REACH_CELLS \
                    and int((record.get("turn_ap") or {}).get(enemy_id) or 0) \
                    >= ap_cost(MOVE_AP_ACTION):
                _spend_ap(record, enemy_id, MOVE_AP_ACTION)
                step = _enemy_step(record, battlefield, enemy_id, target_cell)
                if step is None:
                    # 无路可走：本回合剩余 AP 作废，避免死循环。
                    record["turn_ap"][enemy_id] = 0
                    return []
                _apply_move(record, live, battlefield, enemy_id, step)
            else:
                return []
        except TacticalError:
            # 敌体回合的结构化拒绝（如视线被遮 / 无合法动作）：这一跳放弃，
            # 剩余 AP 作废——敌体回合失败不阻塞玩家的结束回合请求。
            record.setdefault("turn_ap", {})[enemy_id] = 0
            return []


def _enemy_step(record, battlefield, enemy_id, target_cell):
    """敌体向目标挪一格：可达集里挑「离目标最近」的格（确定性平手序）。

    排除原地格（成本 0 不是移动）；候选不比当前格更近时放弃（不后退），
    由调用方把剩余 AP 作废——宁可站着也不来回踱步。
    """
    occupied = tactical_objects.occupancy_map(_battle_state(record), battlefield,
                                              exclude=[enemy_id])
    origin = _participant(record, enemy_id).get("cell_id")
    current = tactical_hex_distance(battlefield, origin, target_cell)
    try:
        move = tactical_movement.plan_move(
            battlefield, enemy_id, origin,
            [{"q": 0, "r": 0}], 1, occupied=occupied)
    except ValueError:
        return None
    best = None
    best_key = None
    for option in move.get("reachable") or []:
        if option.get("cell_id") == origin or int(option.get("cost") or 0) < 1:
            continue  # 原地格与零成本项不是移动
        key = (tactical_hex_distance(battlefield, option["cell_id"], target_cell),
               int(option.get("cost") or 0),
               int(option.get("q") or 0), int(option.get("r") or 0),
               option["cell_id"])
        if best_key is None or key < best_key:
            best_key = key
            best = option["cell_id"]
    if best is None or best_key[0] > current:
        return None
    return best


def _advance_to_next_actor(record, live, *, start=False) -> None:
    """推进到下一个需要玩家操作的位置（口径同 #423 `_advance`）。

    敌体回合就地跑完；跨回合计数；胜负检查点贯穿全程。战斗结束后
    `active_unit_id` 保持最后状态，调用方据 `status` 判定。
    """
    state = _battle_state(record)
    order = list(record.get("turn_order") or [])
    if not order or not is_active(record):
        return
    battlefield = record.get("battlefield")
    index = int(state.get("turn") or 0)
    if not start and state.get("active_unit_id"):
        # 上一个单位已经行动过（或主动结束回合）→ 从下一个开始。
        index += 1
    guard = 0
    limit = len(order) * 3 + 12
    while guard < limit:
        guard += 1
        if index >= len(order):
            state["round"] = int(state.get("round") or 1) + 1
            index = 0
        unit_id = order[index]
        unit, _side = _resolve_unit(record, live, unit_id)
        if unit is None or not _conscious(unit):
            index += 1
            continue
        if _check_battle_end(record, live):
            return
        state["turn"] = index
        state["active_unit_id"] = unit_id
        _append_event(record, {
            "schema_version": 1,
            "kind": "tactical.battle_event",
            "type": "turn_started",
            "actor_id": unit_id,
            "rule_engine": ENGINE,
            "expected_revision": int(state.get("state_revision") or 0),
            "result": {"summary": "轮到 %s（回合 %d）"
                                 % (unit_id, state["round"]),
                       "round": state["round"]},
        })
        _refresh_ap(record, unit_id)
        entry = _unit_entry(record, unit_id) or {}
        if not entry.get("party"):
            _enemy_turn(record, live, battlefield, unit_id)
            if _check_battle_end(record, live):
                return
            index += 1
            continue
        return  # 轮到玩家：把操作权交回去


# ── 对外写操作 ──────────────────────────────────────────────────────────

def do_move(record, live, *, actor_id, to_cell_id, expected_revision) -> dict:
    """移动提案与提交（一跳完成；服务端重算路径，客户端只给目标格）。"""
    _require_active_turn(record, actor_id)
    _check_expected_revision(record, expected_revision)
    if not isinstance(to_cell_id, str) or not to_cell_id:
        raise TacticalError("缺少目标格", 400, "bad_destination")
    battlefield = record.get("battlefield")
    if tactical_battlefield.cell_by_id(battlefield, to_cell_id) is None:
        raise TacticalError("目标格不在本战场", 404, "no_cell",
                            {"state_revision":
                             _battle_state(record).get("state_revision")})
    _spend_ap(record, actor_id, MOVE_AP_ACTION)
    result = _apply_move(record, live, battlefield, actor_id, to_cell_id)
    _trigger_hazards(record, battlefield, actor_id, to_cell_id)
    _bump_revision(record)
    return {"result": result,
            "narrative": "%s 移动到 %s。" % (actor_id, to_cell_id)}


def do_attack(record, live, *, actor_id, target_id, expected_revision) -> dict:
    """一次基础攻击：context → 适配器 → 核心结算（骰点只在服务端）。"""
    _require_active_turn(record, actor_id)
    _check_expected_revision(record, expected_revision)
    if not isinstance(target_id, str) or not target_id:
        raise TacticalError("缺少目标", 400, "bad_target")
    if target_id == actor_id:
        raise TacticalError("不能攻击自己", 422, "bad_target")
    battlefield = record.get("battlefield")
    _spend_ap(record, actor_id, ATTACK_AP_ACTION)
    out = _settle_attack(record, live, battlefield, actor_id, target_id,
                         rng=_event_rng(record))
    event = _attack_event(record, live, actor_id, target_id, out)
    _append_event(record, event)
    _check_battle_end(record, live)
    _bump_revision(record)
    summary = out.get("summary") or {}
    return {"result": {"grade": summary.get("grade"),
                       "damage": summary.get("damage"),
                       "attack_total": summary.get("attack_total"),
                       "guard": summary.get("guard")},
            "narrative": (event.get("result") or {}).get("summary")}


def do_interact(record, live, *, actor_id, object_id, action,
                expected_revision) -> dict:
    """环境交互：物件状态机（#421）结算；非法转换结构化拒绝。

    可达口径与基础攻击一致（相邻格，`ATTACK_REACH_CELLS`）——隔着战场
    开门是越权操作；物件交互射程数据未落库，登记同口径。
    """
    _require_active_turn(record, actor_id)
    _check_expected_revision(record, expected_revision)
    if not isinstance(object_id, str) or not object_id \
            or not isinstance(action, str) or not action:
        raise TacticalError("缺少物件或互动动作", 400, "bad_interact")
    battlefield = record.get("battlefield")
    instance = None
    for candidate in _battle_state(record).get("objects") or []:
        if isinstance(candidate, dict) and candidate.get("object_id") == object_id:
            instance = candidate
            break
    if instance is None:
        raise TacticalError("没有这个物件", 404, "no_object",
                            {"state_revision":
                             _battle_state(record).get("state_revision")})
    actor_cell = _participant(record, actor_id).get("cell_id")
    reach = tactical_hex_distance(battlefield, actor_cell,
                                  instance.get("cell_id"))
    if reach > ATTACK_REACH_CELLS:
        raise TacticalError(
            "物件不在可交互范围内", 422, "object_out_of_range",
            {"reasons": [_reason(
                "object_out_of_range",
                "%s 距离物件 %s %d 格，超过交互可达口径 %d"
                % (actor_id, object_id, reach, ATTACK_REACH_CELLS),
                object_id=object_id, hex_distance=reach,
                reach=ATTACK_REACH_CELLS)],
             "state_revision": _battle_state(record).get("state_revision")})
    # 先过状态机（纯函数，不改状态）再扣 AP：拒绝不消耗行动点。
    proposal = tactical_objects.interact(
        _battle_state(record), battlefield,
        definitions=record.get("object_definitions") or {},
        object_id=object_id, action=action,
        actor_id=actor_id, rule_engine=ENGINE,
        applied_keys=tuple(record.get("applied_keys") or ()))
    if proposal.get("duplicate"):
        raise TacticalError("这个效果已经触发过了", 409, "duplicate_request",
                            {"reasons": list(proposal.get("reasons") or [])})
    if not proposal.get("accepted"):
        raise TacticalError(
            "环境交互被拒绝", 422, "interact_rejected",
            {"reasons": list(proposal.get("reasons") or []),
             "state_revision": _battle_state(record).get("state_revision")})
    _spend_ap(record, actor_id, INTERACT_AP_ACTION)
    _apply_object_proposal(record, proposal)
    _bump_revision(record)
    event = (proposal.get("events") or [{}])[0]
    return {"result": proposal.get("transition"),
            "narrative": (event.get("result") or {}).get("summary")
                         or "环境发生了变化。"}


def do_end_turn(record, live, *, actor_id, expected_revision) -> dict:
    """结束当前回合：推进到下一个行动者（敌方回合就地结算）。"""
    _require_active_turn(record, actor_id)
    _check_expected_revision(record, expected_revision)
    state = _battle_state(record)
    _append_event(record, {
        "schema_version": 1,
        "kind": "tactical.battle_event",
        "type": "turn_ended",
        "actor_id": actor_id,
        "rule_engine": ENGINE,
        "expected_revision": int(state.get("state_revision") or 0),
        "result": {"summary": "%s 结束了回合（剩余 AP %d）"
                             % (actor_id,
                                int((record.get("turn_ap") or {})
                                    .get(actor_id) or 0)),
                   "round": state.get("round")},
    })
    _advance_to_next_actor(record, live)
    _bump_revision(record)
    active = state.get("active_unit_id")
    if is_active(record):
        narrative = "轮到 %s 行动。" % (active or "?")
    else:
        narrative = {"victory": "战斗胜利！", "defeat": "队伍全灭。",
                     "fled": "队伍脱离了战斗。"}.get(record.get("outcome"),
                                                    "战斗结束。")
    return {"result": {"active_unit_id": active,
                       "round": state.get("round"),
                       "status": record.get("status"),
                       "outcome": record.get("outcome")},
            "narrative": narrative}


def do_flee(record, live, *, actor_id, expected_revision) -> dict:
    """逃跑（撤退）：代价真实——推进压力、不结算经验（02A 既有口径）。"""
    _require_active_turn(record, actor_id)
    _check_expected_revision(record, expected_revision)
    entry = _unit_entry(record, actor_id) or {}
    if not entry.get("party"):
        raise TacticalError("只有队伍单位可以撤退", 403, "not_authorized")
    result = _end_battle(record, live, "fled")
    _bump_revision(record)
    return {"result": result, "narrative": "队伍脱离了战斗（没有获得经验）。"}


# ── 小工具 ──────────────────────────────────────────────────────────────

def _event_rng(record):
    """按 (rng_seed, 事件序号) 派生的确定性 RNG：同输入同骰点，可追溯。"""
    state = _battle_state(record)
    return random.Random("%s:%s" % (record.get("rng_seed"),
                                    int(state.get("last_event_sequence") or 0) + 1))


def tactical_hex_distance(battlefield, from_cell_id, to_cell_id):
    """两个 cell_id 的六角距离（水平；跨 surface 也按 q/r 计）。

    #417 / #419 的既有口径是「同 surface 内」的格距；本助手只服务于
    「敌体选最近目标」这一确定性排序，不进入任何规则结算——距离事实
    一律仍以 #444 / #447 的结构化查询为准。
    """
    from_cell = tactical_battlefield.cell_by_id(battlefield, from_cell_id)
    if from_cell is None or not isinstance(to_cell_id, str):
        return 999
    to_cell = tactical_battlefield.cell_by_id(battlefield, to_cell_id)
    if to_cell is None:
        return 999
    dq = int(to_cell["q"]) - int(from_cell["q"])
    dr = int(to_cell["r"]) - int(from_cell["r"])
    return max(abs(dq), abs(dr), abs(dq + dr))
