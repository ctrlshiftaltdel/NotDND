#!/usr/bin/env python3
"""战场物件与机关状态机回归（Issue #421）。

覆盖：
- 门「关 → 开 → 上锁」，以及非法转换（`destroyed → open`、`locked → open`）；
- 警报 `inactive → armed → triggered`；炸药「引爆」与「失效」；
- 可破坏掩体：`intact → damaged` 的 `cover_grade` 降档 cell_delta、`damaged → destroyed`
  的 `blocks_los` / `blocks_movement` 关闭与 `cover_grade` 移除，降到底档再降即移除；
- 物件移动：成功、被占格拒绝、footprint 越界、落到阻挡格、自身格无变化、非 movable 拒绝；
- 拾取：只产出 `inventory_proposals`，不写库存、不改 `battle_state`；
- 危险格进入只触发一次：同一幂等键重复请求为空提案，提交后 `triggered=true` 再触发被拒；
- 所有交互对传入的 `battle_state` / `battlefield` / 定义**零修改**（每次调用后深比较不变）；
- 定义校验：未知类别、悬空状态、歧义转换、未知格效果字段、非法 cover_grade；
- 事件形状符合 `tactical.battle_event` 的字段集合与枚举；
- 无硬编码：模块不含世界 / 物件名分支，也不含规则阈值；运行期新定义照样生效。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_tactical_objects.py` 运行。
"""
import copy
import json
import os
import re
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)

import tactical_battlefield as tb  # noqa: E402
import tactical_objects as to  # noqa: E402

RULES = tb.space_rules()
HEX = RULES["scale"]["hex_size_cm"]
GRADES = RULES["cover"]["grades"]
SOURCE = "docs/system/02c-hex-battlefield.md:29"
TO_MODULE = os.path.join(ROOT, "tactical_objects.py")

EVENT_FIELDS = {
    "schema_version", "kind", "sequence", "type", "actor_id", "target_id",
    "from_cell_id", "to_cell_id", "rule_engine", "expected_revision", "result",
    "source_rule_ref",
}
EVENT_TYPES = {
    "move_started", "move_committed", "attack_resolved", "object_state_changed",
    "hazard_triggered", "fall_resolved", "turn_started", "turn_ended",
    "combat_started", "combat_ended",
}


# ── 样例：战场 / 定义 / 实例 ─────────────────────────────────────────────

# (cell_id, q, r, 覆盖字段)：只挑几个格，够覆盖阻挡、掩体、危险与占格即可。
CELL_SPECS = (
    ("cell-00", 0, 0, {}),
    ("cell-10", 1, 0, {}),
    ("cell-01", 0, 1, {}),
    ("cell-0m1", 0, -1, {
        "cover_grade": "heavy", "blocks_los": True, "blocks_movement": True,
        "obstacle_height": 120,
    }),
    ("cell-1m1", 1, -1, {}),
    ("cell-11", 1, 1, {}),
    ("cell-20", 2, 0, {"hazard_refs": ["hazard-other"]}),
    ("cell-21", 2, 1, {"hazard_refs": ["hazard-tripwire"]}),
    ("cell-30", 3, 0, {}),
)


def cell(cell_id, q, r, **overrides):
    item = {
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
    item.update(copy.deepcopy(overrides))
    return item


def battlefield():
    return {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": SOURCE,
        "battlefield_id": "bf-objects-421",
        "world_id": "fixture",
        "site_id": "site-fixture",
        "atlas_ref": "fixture/region-00",
        "seed": 421,
        "generator_version": "fixture-1",
        "geometry": {"type": "hex_axial"},
        "scale": {"mode": "metric", "hex_size_cm": HEX},
        "cells": [cell(cell_id, q, r, **overrides)
                  for cell_id, q, r, overrides in CELL_SPECS],
        "vertical_links": [],
    }


def door_definition():
    return {
        "definition_id": "door-def",
        "kinds": ["blocker", "mechanism"],
        "initial_state": "closed",
        "states": {
            "closed": {"object": {"blocks_movement": True, "blocks_los": True},
                       "cells": {"blocks_los": True}},
            "open": {"object": {"blocks_movement": False, "blocks_los": False},
                     "cells": {"blocks_los": False}},
            "locked": {"object": {"blocks_movement": True, "blocks_los": True}},
            "destroyed": {"object": {"blocks_movement": False, "blocks_los": False}},
        },
        "transitions": [
            {"from": "closed", "to": "open", "trigger": "open"},
            {"from": "open", "to": "closed", "trigger": "close"},
            {"from": ["closed", "open"], "to": "locked", "trigger": "lock"},
            {"from": "locked", "to": "closed", "trigger": "unlock"},
            {"from": "closed", "to": "destroyed", "trigger": "smash"},
        ],
        "source": "data/system/tactics_optional_rules.json#/objects/door",
    }


def alarm_definition():
    return {
        "definition_id": "alarm-def",
        "kinds": ["hazard", "mechanism"],
        "initial_state": "inactive",
        "states": {
            "inactive": {"object": {"triggered": False}},
            "armed": {"object": {"triggered": False}},
            "triggered": {"object": {"triggered": True}},
            "disabled": {"object": {"triggered": False, "destructible": False}},
        },
        "transitions": [
            {"from": "inactive", "to": "armed", "trigger": "arm"},
            {"from": "armed", "to": "triggered", "trigger": "trigger"},
            {"from": "triggered", "to": "inactive", "trigger": "reset"},
            {"from": ["inactive", "armed", "triggered"], "to": "disabled", "trigger": "disable"},
        ],
    }


def charge_definition():
    return {
        "definition_id": "charge-def",
        "kinds": ["hazard", "destructible"],
        "initial_state": "armed",
        "states": {
            "armed": {},
            "triggered": {"object": {"triggered": True, "destructible": False}},
            "disabled": {"object": {"triggered": True, "destructible": False}},
        },
        "transitions": [
            {"from": "armed", "to": "triggered", "trigger": "detonate"},
            {"from": "armed", "to": "disabled", "trigger": "defuse"},
        ],
    }


def cover_definition():
    return {
        "definition_id": "cover-def",
        "kinds": ["cover", "blocker", "destructible"],
        "initial_state": "intact",
        "states": {
            "intact": {"object": {"blocks_los": True},
                       "cells": {"cover_grade": "heavy", "blocks_los": True,
                                 "blocks_movement": True}},
            "damaged": {"object": {"blocks_los": True},
                        "cells": {"cover_grade": to.DOWNGRADE}},
            "destroyed": {"object": {"blocks_los": False},
                          "cells": {"cover_grade": None, "blocks_los": False,
                                    "blocks_movement": False}},
        },
        "transitions": [
            {"from": "intact", "to": "damaged", "trigger": "damage"},
            {"from": "damaged", "to": "destroyed", "trigger": "damage"},
            {"from": "intact", "to": "destroyed", "trigger": "destroy"},
        ],
    }


def crate_definition():
    return {
        "definition_id": "crate-def",
        "kinds": ["movable", "pickup"],
        "initial_state": "intact",
        "states": {"intact": {}, "taken": {"object": {"movable": False}}},
        "transitions": [{"from": "intact", "to": "taken", "trigger": "pickup"}],
        "pickup": {"item_refs": ["item-ref-02"], "quantity": 1},
    }


def trap_definition():
    return {
        "definition_id": "trap-def",
        "kinds": ["hazard"],
        "hazard_refs": ["hazard-tripwire"],
        "hazard_triggers": ["enter"],
        "initial_state": "armed",
        "states": {
            "armed": {},
            "triggered": {"object": {"triggered": True}},
        },
        "transitions": [{"from": "armed", "to": "triggered", "trigger": "enter"}],
    }


def definitions():
    return {
        item["definition_id"]: item for item in (
            door_definition(), alarm_definition(), charge_definition(),
            cover_definition(), crate_definition(), trap_definition(),
        )
    }


def instance(object_id, definition_id, cell_id, state, **overrides):
    item = {
        "object_id": object_id,
        "definition_id": definition_id,
        "cell_id": cell_id,
        "footprint": [{"q": 0, "r": 0}],
        "state": state,
    }
    item.update(overrides)
    return item


def objects():
    return [
        instance("obj-door", "door-def", "cell-00", "closed",
                 blocks_movement=True, blocks_los=True),
        instance("obj-alarm", "alarm-def", "cell-10", "inactive", triggered=False),
        instance("obj-charge-a", "charge-def", "cell-1m1", "armed"),
        instance("obj-charge-b", "charge-def", "cell-30", "armed"),
        instance("obj-cover", "cover-def", "cell-0m1", "intact", blocks_los=True),
        instance("obj-crate", "crate-def", "cell-11", "intact", movable=True),
        instance("obj-trap", "trap-def", "cell-21", "armed", triggered=False),
    ]


def battle_state(revision=3, last_sequence=4):
    return {
        "schema_version": 1,
        "kind": "tactical.battle_state",
        "battle_id": "battle-421",
        "battlefield_id": "bf-objects-421",
        "round": 1,
        "turn": 1,
        "state_revision": revision,
        "last_event_sequence": last_sequence,
        "participants": [
            {"unit_id": "u-1", "cell_id": "cell-01", "footprint": [{"q": 0, "r": 0}]},
        ],
        "objects": objects(),
    }


# ── 通用工具 ────────────────────────────────────────────────────────────

def _snapshot(*parts):
    return tuple(copy.deepcopy(item) for item in parts)


def run(func, state, field, defs, **kwargs):
    """调用一个入口，并断言传入的 battle_state / battlefield / 定义逐字节未变。"""
    before = _snapshot(state, field, defs)
    proposal = func(state, field, definitions=defs, **kwargs)
    after = _snapshot(state, field, defs)
    assert before[0] == after[0] == state, "battle_state 被修改"
    assert before[1] == after[1] == field, "battlefield 被修改"
    assert before[2] == after[2] == defs, "definitions 被修改"
    assert state == before[0] and field == before[1], "传入对象不是调用前的那一份"
    return proposal


def apply_proposal(state, field, proposal):
    """模拟服务端事务提交：把提案写进**副本**（本模块与测试都不改传入对象）。"""
    committed_state = copy.deepcopy(state)
    committed_field = copy.deepcopy(field)
    for object_id, patch in proposal["state_delta"].items():
        target = to.object_by_id(committed_state, object_id)
        assert target is not None, "state_delta 指向不存在的物件 %r" % (object_id,)
        target.update(copy.deepcopy(patch))
    for delta in proposal["cell_deltas"]:
        target_cell = tb.cell_by_id(committed_field, delta["cell_id"])
        assert target_cell is not None, "cell_delta 指向不存在的格 %r" % (delta["cell_id"],)
        for key, value in delta["set"].items():
            target_cell[key] = copy.deepcopy(value)
        for key in delta["unset"]:
            target_cell.pop(key, None)
    return committed_state, committed_field


def assert_rejected(proposal, code):
    assert proposal["accepted"] is False, "应被拒绝: %r" % (proposal,)
    assert proposal["duplicate"] is False, "应是拒绝而不是幂等命中: %r" % (proposal,)
    codes = [reason["code"] for reason in proposal["reasons"]]
    assert code in codes, "拒绝原因应为 %r，实为 %r" % (code, codes)
    assert not proposal["state_delta"], "拒绝提案不得带 state_delta"
    assert not proposal["cell_deltas"], "拒绝提案不得带 cell_deltas"
    assert not proposal["events"], "拒绝提案不得带事件"
    return proposal


def assert_accepted(proposal, from_state=None, to_state=None):
    assert proposal["accepted"] is True, "应被接受: %r" % (proposal["reasons"],)
    assert proposal["duplicate"] is False
    assert not proposal["reasons"], "接受提案不应带 reasons"
    if from_state is not None:
        assert proposal["transition"] == {"from": from_state, "to": to_state}, \
            "转换应为 %r → %r，实为 %r" % (from_state, to_state, proposal["transition"])
    return proposal


# ── 门 ──────────────────────────────────────────────────────────────────

def check_door_closed_open_locked():
    state, field, defs = battle_state(), battlefield(), definitions()

    opened = assert_accepted(
        run(to.interact, state, field, defs, object_id="obj-door", action="open",
            actor_id="u-1"),
        "closed", "open")
    assert opened["state_delta"]["obj-door"]["state"] == "open"
    assert opened["state_delta"]["obj-door"]["blocks_movement"] is False
    assert opened["state_delta"]["obj-door"]["blocks_los"] is False
    assert opened["cell_deltas"] == [], "门开时格上 blocks_los 已是 false，不应产生 delta"
    assert opened["events"][0]["type"] == "object_state_changed"
    assert opened["events"][0]["actor_id"] == "u-1"
    assert opened["events"][0]["target_id"] == "obj-door"

    state, field = apply_proposal(state, field, opened)
    locked = assert_accepted(
        run(to.interact, state, field, defs, object_id="obj-door", action="lock"),
        "open", "locked")
    assert locked["state_delta"]["obj-door"]["state"] == "locked"
    assert locked["state_delta"]["obj-door"]["blocks_movement"] is True
    assert locked["cell_deltas"] == [], "locked 状态没有声明格效果，不应产生 delta"

    state, field = apply_proposal(state, field, locked)
    refused = assert_rejected(
        run(to.interact, state, field, defs, object_id="obj-door", action="open"),
        "illegal_transition")
    reason = refused["reasons"][0]
    assert reason["state"] == "locked" and reason["trigger"] == "open"
    assert reason["allowed_triggers"] == ["unlock"], reason["allowed_triggers"]

    # 解锁回到 closed：格上 blocks_los 由 false 变 true（空间属性随状态同步）。
    state, field = apply_proposal(state, field, locked)
    unlocked = assert_accepted(
        run(to.interact, state, field, defs, object_id="obj-door", action="unlock"),
        "locked", "closed")
    assert unlocked["cell_deltas"] == [
        {"cell_id": "cell-00", "set": {"blocks_los": True}, "unset": []}
    ], unlocked["cell_deltas"]


def check_door_destroyed_cannot_open():
    """非法转换 `destroyed → open` 必须被拒绝并给出原因。"""
    state, field, defs = battle_state(), battlefield(), definitions()
    smashed = run(to.interact, state, field, defs, object_id="obj-door", action="smash")
    assert_accepted(smashed, "closed", "destroyed")
    state, field = apply_proposal(state, field, smashed)
    assert to.object_by_id(state, "obj-door")["state"] == "destroyed"

    refused = assert_rejected(
        run(to.interact, state, field, defs, object_id="obj-door", action="open"),
        "illegal_transition")
    assert refused["reasons"][0]["state"] == "destroyed"
    assert refused["reasons"][0]["allowed_triggers"] == [], "destroyed 没有出边"


# ── 警报 / 炸药 ─────────────────────────────────────────────────────────

def check_alarm_inactive_armed_triggered():
    state, field, defs = battle_state(), battlefield(), definitions()

    armed = assert_accepted(
        run(to.interact, state, field, defs, object_id="obj-alarm", action="arm"),
        "inactive", "armed")
    assert armed["state_delta"]["obj-alarm"]["state"] == "armed"
    assert "triggered" not in armed["state_delta"]["obj-alarm"], \
        "armed 与实例现值一致，不应产生多余字段"
    state, field = apply_proposal(state, field, armed)

    triggered = assert_accepted(
        run(to.interact, state, field, defs, object_id="obj-alarm", action="trigger"),
        "armed", "triggered")
    assert triggered["state_delta"]["obj-alarm"]["triggered"] is True
    state, field = apply_proposal(state, field, triggered)

    assert_rejected(
        run(to.interact, state, field, defs, object_id="obj-alarm", action="trigger"),
        "illegal_transition")


def check_charge_detonate_and_disable():
    state, field, defs = battle_state(), battlefield(), definitions()

    detonated = assert_accepted(
        run(to.interact, state, field, defs, object_id="obj-charge-a", action="detonate"),
        "armed", "triggered")
    assert detonated["state_delta"]["obj-charge-a"]["triggered"] is True
    disabled = assert_accepted(
        run(to.interact, state, field, defs, object_id="obj-charge-b", action="defuse"),
        "armed", "disabled")
    assert disabled["state_delta"]["obj-charge-b"]["state"] == "disabled"
    assert disabled["state_delta"]["obj-charge-b"]["destructible"] is False

    triggered_state, _ = apply_proposal(state, field, detonated)
    refused = assert_rejected(
        run(to.interact, triggered_state, field, defs, object_id="obj-charge-a",
            action="defuse"),
        "illegal_transition")
    assert refused["reasons"][0]["allowed_triggers"] == []


# ── 可破坏掩体 ──────────────────────────────────────────────────────────

def check_cover_downgrade_and_destroy():
    state, field, defs = battle_state(), battlefield(), definitions()
    assert tb.cell_by_id(field, "cell-0m1")["cover_grade"] == "heavy"

    damaged = assert_accepted(
        run(to.interact, state, field, defs, object_id="obj-cover", action="damage"),
        "intact", "damaged")
    deltas = damaged["cell_deltas"]
    assert len(deltas) == 1, deltas
    assert deltas[0]["cell_id"] == "cell-0m1"
    assert deltas[0]["unset"] == []
    # heavy → medium：按 tactical_space.json 的 grades 顺序降一档，代码不写死档名。
    assert deltas[0]["set"] == {"cover_grade": GRADES[GRADES.index("heavy") - 1]}, deltas[0]

    state, field = apply_proposal(state, field, damaged)
    assert tb.cell_by_id(field, "cell-0m1")["cover_grade"] == "medium"
    assert tb.cell_by_id(field, "cell-0m1")["blocks_los"] is True

    destroyed = assert_accepted(
        run(to.interact, state, field, defs, object_id="obj-cover", action="damage"),
        "damaged", "destroyed")
    deltas = destroyed["cell_deltas"]
    assert len(deltas) == 1, deltas
    assert deltas[0]["set"] == {"blocks_los": False, "blocks_movement": False}, deltas[0]
    assert deltas[0]["unset"] == ["cover_grade"], "掩体被打掉后不得残留掩体档"

    state, field = apply_proposal(state, field, destroyed)
    target = tb.cell_by_id(field, "cell-0m1")
    assert "cover_grade" not in target, target
    assert target["blocks_los"] is False and target["blocks_movement"] is False
    # 提交后的战场仍是合法战场（空间查询读到的就是新属性）。
    assert tb.validate_battlefield(field) == []


def check_cover_downgrade_at_lowest_grade_unsets():
    """已是最低档再降 → 移除 cover_grade（不存在「第零档」）。"""
    state, field, defs = battle_state(), battlefield(), definitions()
    tb.cell_by_id(field, "cell-0m1")["cover_grade"] = GRADES[0]
    proposal = run(to.interact, state, field, defs, object_id="obj-cover", action="damage")
    assert_accepted(proposal, "intact", "damaged")
    assert proposal["cell_deltas"] == [
        {"cell_id": "cell-0m1", "set": {}, "unset": ["cover_grade"]}
    ], proposal["cell_deltas"]


# ── 物件移动 ────────────────────────────────────────────────────────────

def check_move_object_success():
    state, field, defs = battle_state(), battlefield(), definitions()

    proposal = run(to.move_object, state, field, defs, object_id="obj-crate",
                   to_cell_id="cell-20", actor_id="u-1")
    assert proposal["accepted"] is True, proposal["reasons"]
    assert proposal["state_delta"] == {"obj-crate": {"cell_id": "cell-20"}}
    assert proposal["cell_deltas"] == []
    event = proposal["events"][0]
    assert event["type"] == "object_state_changed"
    assert event["result"]["op"] == "object_moved"
    assert event["from_cell_id"] == "cell-11" and event["to_cell_id"] == "cell-20"

    state, field = apply_proposal(state, field, proposal)
    assert to.object_by_id(state, "obj-crate")["cell_id"] == "cell-20"


def check_move_object_rejects_bad_targets():
    state, field, defs = battle_state(), battlefield(), definitions()

    occupied = assert_rejected(
        run(to.move_object, state, field, defs, object_id="obj-crate",
            to_cell_id="cell-01"),
        "blocked_placement")
    codes = [item["code"] for item in occupied["reasons"][0]["errors"]]
    assert "occupied" in codes, occupied["reasons"]

    assert_rejected(
        run(to.move_object, state, field, defs, object_id="obj-crate",
            to_cell_id="cell-20", footprint=[{"q": 0, "r": 0}, {"q": 0, "r": -1}]),
        "out_of_bounds")

    assert_rejected(
        run(to.move_object, state, field, defs, object_id="obj-crate",
            to_cell_id="cell-11"),
        "same_position")

    assert_rejected(
        run(to.move_object, state, field, defs, object_id="obj-door", to_cell_id="cell-20"),
        "not_movable")

    state_marked = copy.deepcopy(state)
    state_marked["objects"].append(instance("obj-block", "crate-def", "cell-30", "intact",
                                            movable=False))
    assert_rejected(
        run(to.move_object, state_marked, field, defs, object_id="obj-block",
            to_cell_id="cell-00"),
        "not_movable")


def check_move_object_revalidates_footprint_and_blocking():
    """多格 footprint 移动时逐格复校：落到 blocks_movement 格必须被拒。"""
    field = {
        "schema_version": 1,
        "kind": "tactical.battlefield",
        "source": SOURCE,
        "battlefield_id": "bf-footprint-421",
        "world_id": "fixture",
        "geometry": {"type": "hex_axial"},
        "scale": {"mode": "metric", "hex_size_cm": HEX},
        "cells": [
            cell("cell-a", 0, 0),
            cell("cell-b", 1, 0),
            cell("cell-c", 0, 1, blocks_movement=True, blocks_los=True),
            cell("cell-d", 1, 1),
        ],
        "vertical_links": [],
    }
    assert tb.validate_battlefield(field) == []
    state = battle_state()
    state["participants"] = []
    state["objects"] = [instance("obj-long", "crate-def", "cell-a", "intact",
                                 movable=True,
                                 footprint=[{"q": 0, "r": 0}, {"q": 1, "r": 0}])]
    defs = definitions()

    blocked = run(to.move_object, state, field, defs, object_id="obj-long",
                  to_cell_id="cell-c")
    assert_rejected(blocked, "blocked_placement")
    codes = [item["code"] for item in blocked["reasons"][0]["errors"]]
    assert "blocked_movement" in codes, blocked["reasons"]

    # 目标 footprint 的第二个偏移越界（(2,0) 不存在）→ out_of_bounds。
    assert_rejected(
        run(to.move_object, state, field, defs, object_id="obj-long", to_cell_id="cell-b"),
        "out_of_bounds")

    # 自身旧格不参与占用判定：同格、footprint 不变才是 same_position。
    assert_rejected(
        run(to.move_object, state, field, defs, object_id="obj-long", to_cell_id="cell-a"),
        "same_position")


# ── 拾取 ────────────────────────────────────────────────────────────────

def check_pickup_proposes_inventory_only():
    state, field, defs = battle_state(), battlefield(), definitions()
    before = json.dumps(state, ensure_ascii=False, sort_keys=True)

    proposal = run(to.pickup, state, field, defs, object_id="obj-crate", unit_id="u-1")
    assert_accepted(proposal, "intact", "taken")
    assert proposal["inventory_proposals"] == [{
        "unit_id": "u-1",
        "object_id": "obj-crate",
        "definition_id": "crate-def",
        "target": "authoritative_inventory",
        "payload": {"item_refs": ["item-ref-02"], "quantity": 1},
    }], proposal["inventory_proposals"]
    assert proposal["state_delta"]["obj-crate"] == {"state": "taken", "movable": False}
    assert proposal["events"][0]["result"]["op"] == "object_picked_up"

    # 只出提案：battle_state 里不得被写入任何库存字段。
    assert json.dumps(state, ensure_ascii=False, sort_keys=True) == before
    assert "inventory" not in json.dumps(state, ensure_ascii=False).lower()

    assert_rejected(
        run(to.pickup, state, field, defs, object_id="obj-door", unit_id="u-1"),
        "not_pickup")


# ── 危险触发 ────────────────────────────────────────────────────────────

def check_hazard_enter_triggers_once():
    state, field, defs = battle_state(), battlefield(), definitions()

    proposal = run(to.trigger_hazard, state, field, defs, unit_id="u-1",
                   cell_id="cell-21", phase="enter")
    assert proposal["accepted"] is True, proposal["reasons"]
    assert proposal["hazard_refs"] == ["hazard-tripwire"]
    assert proposal["state_delta"]["obj-trap"] == {"state": "triggered", "triggered": True}
    event = proposal["events"][0]
    assert event["type"] == "hazard_triggered"
    assert event["actor_id"] == "u-1" and event["target_id"] == "obj-trap"
    assert event["to_cell_id"] == "cell-21"
    assert event["result"]["phase"] == "enter"
    expected_key = to.idempotency_key("battle-421", 5, "obj-trap", "enter")
    assert proposal["idempotency_key"] == expected_key

    # 同一幂等键重复请求：空提案，不重复结算。
    again = run(to.trigger_hazard, state, field, defs, unit_id="u-1",
                cell_id="cell-21", phase="enter", applied_keys={expected_key})
    assert again["duplicate"] is True and again["accepted"] is False
    assert again["state_delta"] == {} and again["cell_deltas"] == [] and again["events"] == []
    assert again["reasons"][0]["code"] == "duplicate_request"

    # 提交后 triggered=true：换一个新事件序号也不能二次触发。
    committed_state, committed_field = apply_proposal(state, field, proposal)
    third = run(to.trigger_hazard, committed_state, committed_field, defs, unit_id="u-2",
                cell_id="cell-21", phase="enter", event_sequence=99)
    assert_rejected(third, "no_hazard_matched")
    notes = third["reasons"][0]["notes"]
    assert any(note["code"] == "already_triggered" for note in notes), notes


def check_hazard_requires_matching_refs_and_phase():
    state, field, defs = battle_state(), battlefield(), definitions()

    # 格上没有 hazard_refs：不触发。
    assert_rejected(
        run(to.trigger_hazard, state, field, defs, unit_id="u-1",
            cell_id="cell-10", phase="enter"),
        "no_hazard_matched")
    # 格上有 hazard_refs 但引用不同：不触发（命中判定是交集，不是「有危险属性就算」）。
    assert_rejected(
        run(to.trigger_hazard, state, field, defs, unit_id="u-1",
            cell_id="cell-20", phase="enter"),
        "no_hazard_matched")
    # 物件只声明 enter：leave 相位不触发。
    leave = run(to.trigger_hazard, state, field, defs, unit_id="u-1",
                cell_id="cell-21", phase="leave")
    assert_rejected(leave, "no_hazard_matched")
    assert any(note["code"] == "phase_not_declared"
               for note in leave["reasons"][0]["notes"]), leave["reasons"]

    assert_rejected(
        run(to.trigger_hazard, state, field, defs, unit_id="u-1",
            cell_id="cell-nope", phase="enter"),
        "unknown_cell")


# ── 通用性 / 安全性 ─────────────────────────────────────────────────────

def check_unknown_ids_and_revision_guard():
    state, field, defs = battle_state(), battlefield(), definitions()

    assert_rejected(
        run(to.interact, state, field, defs, object_id="obj-nope", action="open"),
        "unknown_object")

    broken = copy.deepcopy(state)
    broken["objects"].append(instance("obj-orphan", "no-such-def", "cell-11", "intact"))
    assert_rejected(
        run(to.interact, broken, field, defs, object_id="obj-orphan", action="open"),
        "unknown_definition")

    assert_rejected(
        run(to.interact, state, field, defs, object_id="obj-door", action="open",
            expected_revision=2),
        "revision_mismatch")
    assert_rejected(
        run(to.interact, state, field, defs, object_id="obj-door", action="fly"),
        "unknown_trigger")


def check_duplicate_interaction_is_idempotent():
    state, field, defs = battle_state(), battlefield(), definitions()
    first = run(to.interact, state, field, defs, object_id="obj-door", action="open")
    assert_accepted(first)
    second = run(to.interact, state, field, defs, object_id="obj-door", action="open",
                 applied_keys={first["idempotency_key"]})
    assert second["duplicate"] is True
    assert second["state_delta"] == {} and second["events"] == []


def check_definitions_are_data_driven():
    """同一套代码接受运行期新定义，且模块里没有世界名 / 物件名分支。"""
    state, field = battle_state(), battlefield()
    lift = {
        "definition_id": "scene-lift-9",
        "kinds": ["mobility", "mechanism"],
        "initial_state": "down",
        "states": {
            "down": {"cells": {"movement_cost": HEX}},
            "up": {"cells": {"blocks_movement": False}},
        },
        "transitions": [{"from": "down", "to": "up", "trigger": "raise"}],
    }
    defs = {"scene-lift-9": lift}
    state["objects"].append(instance("obj-lift", "scene-lift-9", "cell-20", "down"))

    proposal = run(to.interact, state, field, defs, object_id="obj-lift", action="raise")
    assert_accepted(proposal, "down", "up")

    with open(TO_MODULE, encoding="utf-8") as handle:
        text = handle.read()
    lowered = text.lower()
    for name in ("door", "alarm", "charge", "barricade", "trap", "crate", "chest", "wall"):
        assert name not in lowered, "模块出现世界 / 物件名 %r（行为必须来自定义）" % name
    hits = [token for token in ("150", "100", "50", "300", "1d6")
            if re.search(r"(?<![0-9])%s(?![0-9])" % re.escape(token), text)]
    assert not hits, "tactical_objects.py 出现硬编码阈值: %s" % ", ".join(hits)


def check_kinds_cover_required_categories():
    required = {"blocker", "cover", "hazard", "mechanism", "boost", "mobility",
                "pickup", "movable", "destructible"}
    assert set(to.KINDS) == required, to.KINDS
    assert set(to.TRIGGER_PHASES) == {"enter", "leave", "interact"}
    assert set(to.RULE_ENGINES) == {"tactics", "build"}


def check_definition_validation():
    bad_kind = door_definition()
    bad_kind["kinds"] = ["doorway"]
    errors = to.validate_definition(bad_kind)
    assert any("$.kinds" in item for item in errors), errors

    dangling = door_definition()
    dangling["transitions"].append({"from": "closed", "to": "ajar", "trigger": "nudge"})
    errors = to.validate_definition(dangling)
    assert any("$.transitions[5].to" in item for item in errors), errors

    ambiguous = door_definition()
    ambiguous["transitions"].append({"from": "closed", "to": "open", "trigger": "open"})
    errors = to.validate_definition(ambiguous)
    assert any("重复声明" in item for item in errors), errors

    bad_cell = cover_definition()
    bad_cell["states"]["damaged"]["cells"] = {"blockz": True}
    errors = to.validate_definition(bad_cell)
    assert any("$.states.damaged.cells.blockz" in item for item in errors), errors

    bad_grade = cover_definition()
    bad_grade["states"]["intact"]["cells"] = {"cover_grade": "partial"}
    assert any("cover_grade" in item for item in to.validate_definition(bad_grade))

    bad_state = door_definition()
    bad_state["initial_state"] = "ajar"
    assert any("$.initial_state" in item for item in to.validate_definition(bad_state))

    # 完整 fixture 必须干净；非法定义在 interact 里转成结构化原因，而不是抛异常。
    assert to.validate_definitions(definitions()) == []
    assert to.validate_definitions(list(definitions().values())) == []

    invalid = door_definition()
    invalid["kinds"] = []
    defs = dict(definitions())
    defs["door-def"] = invalid
    proposal = run(to.interact, battle_state(), battlefield(), defs,
                   object_id="obj-door", action="open")
    assert_rejected(proposal, "invalid_definition")
    assert proposal["reasons"][0]["errors"]


def check_events_match_schema_fields():
    state, field, defs = battle_state(), battlefield(), definitions()
    proposals = [
        run(to.interact, state, field, defs, object_id="obj-door", action="open",
            actor_id="u-1"),
        run(to.interact, state, field, defs, object_id="obj-cover", action="damage"),
        run(to.move_object, state, field, defs, object_id="obj-crate", to_cell_id="cell-20"),
        run(to.pickup, state, field, defs, object_id="obj-crate", unit_id="u-1"),
        run(to.trigger_hazard, state, field, defs, unit_id="u-1",
            cell_id="cell-21", phase="enter"),
    ]
    for proposal in proposals:
        assert proposal["accepted"] is True, proposal["reasons"]
        for event in proposal["events"]:
            assert set(event) <= EVENT_FIELDS, "事件出现 schema 之外的字段: %r" % (
                set(event) - EVENT_FIELDS,)
            assert event["schema_version"] == 1
            assert event["kind"] == "tactical.battle_event"
            assert event["type"] in EVENT_TYPES
            assert event["rule_engine"] in to.RULE_ENGINES
            assert isinstance(event["sequence"], int) and event["sequence"] >= 1
            assert isinstance(event["expected_revision"], int)
            assert isinstance(event["result"]["summary"], str) and event["result"]["summary"]
            assert event["result"]["state_delta"] == proposal["state_delta"]
            assert event["result"]["cell_deltas"] == proposal["cell_deltas"]


def check_no_operation_mutates_inputs():
    """全套交互跑一遍，传入的 battle_state / battlefield / definitions 必须逐字节不变。"""
    state, field, defs = battle_state(), battlefield(), definitions()

    steps = [
        ("interact", "obj-door", "open"),
        ("interact", "obj-alarm", "arm"),
        ("interact", "obj-charge-a", "detonate"),
        ("interact", "obj-cover", "damage"),
        ("move", "obj-crate", "cell-20"),
        ("pickup", "obj-crate", None),
        ("hazard", None, "cell-21"),
    ]
    produced = 0
    for kind, object_id, extra in steps:
        if kind == "interact":
            proposal = run(to.interact, state, field, defs, object_id=object_id,
                           action=extra)
        elif kind == "move":
            proposal = run(to.move_object, state, field, defs, object_id=object_id,
                           to_cell_id=extra)
        elif kind == "pickup":
            proposal = run(to.pickup, state, field, defs, object_id=object_id,
                           unit_id="u-1")
        else:
            proposal = run(to.trigger_hazard, state, field, defs, unit_id="u-1",
                           cell_id=extra, phase="enter")
        assert proposal["accepted"] is True, (kind, object_id, proposal["reasons"])
        produced += 1

    assert produced == len(steps)
    assert state == battle_state(), "battle_state 与初始构造不一致"
    assert field == battlefield(), "battlefield 与初始构造不一致"
    assert defs == definitions(), "definitions 与初始构造不一致"


def main():
    checks = (
        check_door_closed_open_locked,
        check_door_destroyed_cannot_open,
        check_alarm_inactive_armed_triggered,
        check_charge_detonate_and_disable,
        check_cover_downgrade_and_destroy,
        check_cover_downgrade_at_lowest_grade_unsets,
        check_move_object_success,
        check_move_object_rejects_bad_targets,
        check_move_object_revalidates_footprint_and_blocking,
        check_pickup_proposes_inventory_only,
        check_hazard_enter_triggers_once,
        check_hazard_requires_matching_refs_and_phase,
        check_unknown_ids_and_revision_guard,
        check_duplicate_interaction_is_idempotent,
        check_definitions_are_data_driven,
        check_kinds_cover_required_categories,
        check_definition_validation,
        check_events_match_schema_fields,
        check_no_operation_mutates_inputs,
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
