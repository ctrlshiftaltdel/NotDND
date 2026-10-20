#!/usr/bin/env python3
"""战场物件与机关状态机（Issue #421）。

职责边界（与本单验收一一对应）：

- **定义与实例分离**：定义（`definition_id`、允许的状态与转换、空间效果）由调用方
  以普通 dict 传入；实例来自 `battle_state["objects"]`。本模块不读 `data/**` 下的
  规则文件，也没有任何按世界名 / 物件名分支的代码——行为全部来自定义。
- **只出提案，不写权威状态**：`interact` / `move_object` / `pickup` / `trigger_hazard`
  只返回 `{state_delta, cell_deltas, inventory_proposals, events[]}` 提案；
  不修改传入的 `battle_state` / `battlefield` / 定义，不扣 AP，不改 HP 与库存。
  提案由服务端战斗事务校验后原子提交（02C 第三节「运行时战斗状态与权威」、
  第七节「事件与存档兼容」）。
- **状态机显式**：只有定义里 `transitions[]` 声明过的 `(from, trigger) → to` 才允许
  发生；其余一律返回结构化 `reasons[]`（`illegal_transition` / `unknown_trigger` /
  `unknown_state` / `ambiguous_transition`）。
- **幂等**：幂等键 = `(battle_id, event_sequence, object_id, trigger)`；命中调用方
  维护的 `applied_keys` 时返回 `duplicate=True` 且**不含**任何 delta / 事件。
  已提交实例的 `triggered=true` 再触发会被拒绝，是第二层保护。
- **空间属性同步**：状态声明的 `cells` 效果会与战场当前值逐格比对，只为**真正变化**
  的格产出 `{cell_id, set, unset}`；`cover_grade: "downgrade"` 按
  `data/system/tactical_space.json` 的 `cover.grades` 顺序降一档，已是最低档则
  移除该属性——档位顺序来自数据，代码不写死档名。
- **占格复校**：物件移动先走 `tactical_battlefield.validate_placement` /
  `footprint_cells`，越界、阻挡、互斥占用都转成结构化原因。

定义（caller 传入的 dict）形状：

```python
{
  "definition_id": "object-def-01",          # 必填，非空字符串
  "kinds": ["blocker", "mechanism"],         # 必填，取自 KINDS
  "initial_state": "closed",                 # 可选，必须是 states 的键
  "states": {                                # 必填，至少一个状态
    "closed": {
      "object": {"blocks_movement": True, "blocks_los": True},
      "cells": {"blocks_los": True},         # 只允许 CELL_EFFECT_FIELDS
    },
    "open": {"object": {"blocks_movement": False, "blocks_los": False}},
  },
  "transitions": [                           # 必填，允许的转换全集
    {"from": "closed", "to": "open", "trigger": "open"},
    {"from": ["open", "locked"], "to": "closed", "trigger": "close"},
  ],
  "hazard_refs": ["hazard-01"],              # 可选；与格上 hazard_refs 取交集
  "hazard_triggers": ["enter", "leave"],     # 可选；缺省 = TRIGGER_PHASES
  "pickup": {"item_refs": ["item-ref-01"]},  # 可选；原样进 inventory_proposals
  "source": "data/system/x.json#/objects/0", # 可选；写进事件的 source_rule_ref
}
```

事件形状严格遵循 `data/schema/tactical.battle_event.schema.json`：
`object_state_changed` / `hazard_triggered` 由本模块产出；事件枚举由 #416 冻结，
物件移动复用 `object_state_changed` 并在 `result.op` 标 `object_moved`。

零第三方依赖：仅 Python 3 标准库。
"""
import tactical_battlefield

__all__ = [
    "KINDS",
    "TRIGGER_PHASES",
    "OBJECT_EFFECT_FIELDS",
    "CELL_EFFECT_FIELDS",
    "RULE_ENGINES",
    "DOWNGRADE",
    "validate_definition",
    "validate_definitions",
    "definition_index",
    "object_by_id",
    "object_cells",
    "object_ids_at",
    "occupancy_map",
    "legal_triggers",
    "can_transition",
    "idempotency_key",
    "interact",
    "move_object",
    "pickup",
    "trigger_hazard",
]

# 物件类别（Issue #421 要求覆盖 blockers / cover / hazard / mechanism / boost /
# mobility / pickup / movable / destructible）。类别只是声明的标签，行为仍由状态机给出。
KINDS = (
    "blocker",
    "cover",
    "hazard",
    "mechanism",
    "boost",
    "mobility",
    "pickup",
    "movable",
    "destructible",
)

# 危险触发相位：进入 / 离开 / 互动（02C 第七节：由确定的空间事件触发，不靠叙事）。
TRIGGER_PHASES = ("enter", "leave", "interact")

# 状态可以声明的实例字段（均在 tactical.battle_state.schema.json 的 objects[] 形状内）。
OBJECT_EFFECT_FIELDS = ("blocks_movement", "blocks_los", "destructible", "movable", "triggered")

# 状态可以声明的**格**空间属性（与对象属性分离，02C 第三节不变式 2）。
CELL_EFFECT_FIELDS = ("blocks_movement", "blocks_los", "cover_grade", "movement_cost", "obstacle_height")

# 事件封套的 rule_engine 受控枚举（与 tactical.battle_event.schema.json 一致）。
RULE_ENGINES = ("tactics", "build")

# `cover_grade` 的降档指令：按 tactical_space.json 的 grades 顺序下一档。
DOWNGRADE = "downgrade"

_DEFAULT_FOOTPRINT = ({"q": 0, "r": 0},)
_KIND_REJECT_CODES = {"movable": "not_movable", "pickup": "not_pickup"}
_DEFINITION_FIELDS = (
    "definition_id", "kinds", "initial_state", "states", "transitions",
    "hazard_refs", "hazard_triggers", "pickup", "source",
)
_TRANSITION_FIELDS = ("from", "to", "trigger")


# ── 基础判定 ────────────────────────────────────────────────────────────

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_bool(value):
    return isinstance(value, bool)


def _is_non_empty_str(value):
    return isinstance(value, str) and bool(value)


def _is_str_list(value):
    return (
        isinstance(value, list)
        and all(_is_non_empty_str(item) for item in value)
        and len(set(value)) == len(value)
    )


def _cover_grades():
    """掩体档位顺序（唯一来源：`data/system/tactical_space.json#/cover/grades`）。"""
    return tuple(tactical_battlefield.space_rules()["cover"]["grades"])


def _reason(code, message, **extra):
    item = {"code": code, "message": message}
    item.update(extra)
    return item


# ── 定义校验（结构化，带 `$` 路径，不静默修正） ──────────────────────────

def validate_definition(definition):
    """校验单个物件定义，返回错误字符串列表（空列表 = 通过）。"""
    if not isinstance(definition, dict):
        return ["$: 定义必须是 JSON 对象"]
    errors = []
    for key in sorted(set(definition) - set(_DEFINITION_FIELDS)):
        errors.append("$.%s: 未知字段（定义只接受 %s）" % (key, " / ".join(_DEFINITION_FIELDS)))

    if not _is_non_empty_str(definition.get("definition_id")):
        errors.append("$.definition_id: 必须是非空字符串")

    kinds = definition.get("kinds")
    if not isinstance(kinds, list) or not kinds:
        errors.append("$.kinds: 必须是非空数组（可用类别 %s）" % " / ".join(KINDS))
    else:
        for kind in kinds:
            if kind not in KINDS:
                errors.append("$.kinds: 未知类别 %r（可用 %s）" % (kind, " / ".join(KINDS)))
        if len(set(kinds)) != len(kinds):
            errors.append("$.kinds: 类别不得重复")

    states = definition.get("states")
    if not isinstance(states, dict) or not states:
        errors.append("$.states: 必须是非空对象（状态名 → 空间效果）")
        states = {}
    else:
        for name in sorted(states):
            if not _is_non_empty_str(name):
                errors.append("$.states: 状态名必须是非空字符串（实为 %r）" % (name,))
                continue
            errors.extend(_state_errors(name, states[name]))

    if "initial_state" in definition and definition["initial_state"] not in states:
        errors.append("$.initial_state: 必须是 states 里已声明的状态（实为 %r）"
                      % (definition["initial_state"],))

    errors.extend(_transition_errors(definition.get("transitions"), states))

    if "hazard_refs" in definition and not _is_str_list(definition["hazard_refs"]):
        errors.append("$.hazard_refs: 必须是非空字符串数组且元素唯一")
    if "hazard_triggers" in definition:
        phases = definition["hazard_triggers"]
        if not isinstance(phases, list) or not phases:
            errors.append("$.hazard_triggers: 必须是非空数组（可用 %s）" % " / ".join(TRIGGER_PHASES))
        else:
            for phase in phases:
                if phase not in TRIGGER_PHASES:
                    errors.append("$.hazard_triggers: 未知相位 %r（可用 %s）"
                                  % (phase, " / ".join(TRIGGER_PHASES)))
            if len(set(phases)) != len(phases):
                errors.append("$.hazard_triggers: 相位不得重复")
    if "pickup" in definition and not isinstance(definition["pickup"], dict):
        errors.append("$.pickup: 必须是对象（原样进 inventory_proposals.payload）")
    if "source" in definition and not _is_non_empty_str(definition["source"]):
        errors.append("$.source: 必须是非空字符串")
    return errors


def _state_errors(name, state):
    path = "$.states.%s" % name
    if not isinstance(state, dict):
        return ["%s: 必须是对象（可含 object / cells）" % path]
    errors = []
    for key in sorted(set(state) - {"object", "cells"}):
        errors.append("%s.%s: 未知字段（只接受 object / cells）" % (path, key))

    effects = state.get("object")
    if effects is not None:
        if not isinstance(effects, dict):
            errors.append("%s.object: 必须是对象" % path)
        else:
            for field in sorted(effects):
                if field not in OBJECT_EFFECT_FIELDS:
                    errors.append("%s.object.%s: 未知字段（可用 %s）"
                                  % (path, field, " / ".join(OBJECT_EFFECT_FIELDS)))
                elif not _is_bool(effects[field]):
                    errors.append("%s.object.%s: 必须是布尔值（实为 %r）"
                                  % (path, field, effects[field]))

    effects = state.get("cells")
    if effects is not None:
        if not isinstance(effects, dict):
            errors.append("%s.cells: 必须是对象" % path)
        else:
            errors.extend(_cell_effect_errors(path + ".cells", effects))
    return errors


def _cell_effect_errors(path, effects):
    errors = []
    grades = _cover_grades()
    for field in sorted(effects):
        value = effects[field]
        if field not in CELL_EFFECT_FIELDS:
            errors.append("%s.%s: 未知字段（可用 %s）"
                          % (path, field, " / ".join(CELL_EFFECT_FIELDS)))
            continue
        if field in ("blocks_movement", "blocks_los"):
            if not _is_bool(value):
                errors.append("%s.%s: 必须是布尔值（实为 %r）" % (path, field, value))
        elif field == "cover_grade":
            if value is None or value == DOWNGRADE:
                continue
            if value not in grades:
                errors.append("%s.cover_grade: 必须是 %s 之一、'%s' 或 null（实为 %r）"
                              % (path, " / ".join(grades), DOWNGRADE, value))
        elif not _is_int(value) or value < 0:
            errors.append("%s.%s: 必须是不小于 0 的整数（实为 %r）" % (path, field, value))
    return errors


def _transition_errors(transitions, states):
    if not isinstance(transitions, list):
        return ["$.transitions: 必须是数组（可以是空数组 = 不可交互）"]
    errors = []
    declared = {}
    for index, transition in enumerate(transitions):
        path = "$.transitions[%d]" % index
        if not isinstance(transition, dict):
            errors.append("%s: 必须是对象" % path)
            continue
        for key in sorted(set(transition) - set(_TRANSITION_FIELDS)):
            errors.append("%s.%s: 未知字段（只接受 %s）"
                          % (path, key, " / ".join(_TRANSITION_FIELDS)))
        sources = transition.get("from")
        if isinstance(sources, str):
            sources = [sources]
        if not isinstance(sources, list) or not sources:
            errors.append("%s.from: 必须是状态名或非空状态名数组" % path)
            continue
        for source in sources:
            if not _is_non_empty_str(source):
                errors.append("%s.from: 状态名必须是非空字符串（实为 %r）" % (path, source))
            elif source not in states:
                errors.append("%s.from: 引用了未声明的状态 %r" % (path, source))
        target = transition.get("to")
        if not _is_non_empty_str(target):
            errors.append("%s.to: 必须是非空字符串" % path)
        elif target not in states:
            errors.append("%s.to: 引用了未声明的状态 %r" % (path, target))
        trigger = transition.get("trigger")
        if not _is_non_empty_str(trigger):
            errors.append("%s.trigger: 必须是非空字符串（互动入口名）" % path)
            continue
        for source in sources:
            if not _is_non_empty_str(source):
                continue
            key = (source, trigger)
            if key in declared:
                errors.append("%s: (from=%r, trigger=%r) 与 $.transitions[%d] 重复声明（歧义）"
                              % (path, source, trigger, declared[key]))
            else:
                declared[key] = index
    return errors


def validate_definitions(definitions):
    """校验定义集合（`{id: dict}` 或 `[dict, …]`），返回错误字符串列表。"""
    errors = []
    seen = set()
    for definition in _iter_definitions(definitions, errors):
        definition_id = definition.get("definition_id") if isinstance(definition, dict) else None
        errors.extend("%s: %s" % (definition_id or "<anonymous>", item)
                      for item in validate_definition(definition))
        if _is_non_empty_str(definition_id):
            if definition_id in seen:
                errors.append("%s: definition_id 重复" % definition_id)
            else:
                seen.add(definition_id)
    return errors


def _iter_definitions(definitions, errors):
    if isinstance(definitions, dict):
        for key in sorted(definitions):
            definition = definitions[key]
            if isinstance(definition, dict) and definition.get("definition_id") != key:
                errors.append("%s: 定义映射的键与 definition_id 不一致（实为 %r）"
                              % (key, definition.get("definition_id")))
            yield definition
        return
    if isinstance(definitions, (list, tuple)):
        for definition in definitions:
            yield definition
        return
    raise TypeError("definitions 必须是 {definition_id: dict} 或 [dict, …]，实为 %r"
                    % (type(definitions).__name__,))


def definition_index(definitions):
    """把定义集合转成 `{definition_id: dict}`；重复 id 直接抛 `ValueError`。

    这里只做索引，不做完整校验——单条定义的错误由 `interact` 等入口转成结构化
    `reasons`，便于把「数据写错」与「操作非法」区分开。
    """
    errors = []
    index = {}
    for definition in _iter_definitions(definitions, errors):
        if not isinstance(definition, dict):
            continue
        definition_id = definition.get("definition_id")
        if not _is_non_empty_str(definition_id):
            continue
        if definition_id in index:
            raise ValueError("definition_id 重复: %r" % (definition_id,))
        index[definition_id] = definition
    if errors:
        raise ValueError("; ".join(errors))
    return index


# ── 实例检索 ────────────────────────────────────────────────────────────

def _objects(battle_state):
    objects = battle_state.get("objects") if isinstance(battle_state, dict) else None
    return objects if isinstance(objects, list) else []


def object_by_id(battle_state, object_id):
    """按稳定身份取物件实例；找不到返回 `None`（不按状态或定义兜底）。"""
    for instance in _objects(battle_state):
        if isinstance(instance, dict) and instance.get("object_id") == object_id:
            return instance
    return None


def _footprint_of(instance):
    footprint = instance.get("footprint") if isinstance(instance, dict) else None
    if isinstance(footprint, list) and footprint:
        return list(footprint)
    return [dict(item) for item in _DEFAULT_FOOTPRINT]


def object_cells(battlefield, instance):
    """物件 footprint 覆盖的格（按 `cell_id` 排序）；越界 / 悬空 origin 返回 `None`。

    只读：调用 `tactical_battlefield.footprint_cells`，不复制几何算法，也不跨 surface。
    """
    cell_id = instance.get("cell_id") if isinstance(instance, dict) else None
    if not _is_non_empty_str(cell_id):
        return None
    try:
        cells = tactical_battlefield.footprint_cells(battlefield, cell_id, _footprint_of(instance))
    except (ValueError, KeyError, TypeError):
        return None
    return sorted(cells, key=lambda cell: str(cell.get("cell_id")))


def object_ids_at(battle_state, battlefield, cell_id):
    """覆盖 `cell_id` 的物件 id，按 id 排序（确定性）。"""
    found = []
    for instance in _objects(battle_state):
        if not isinstance(instance, dict):
            continue
        cells = object_cells(battlefield, instance)
        if cells and any(cell.get("cell_id") == cell_id for cell in cells):
            found.append(instance.get("object_id"))
    return tuple(sorted(item for item in found if _is_non_empty_str(item)))


def occupancy_map(battle_state, battlefield, exclude=None):
    """`cell_id -> 占用者 id`（参与者与物件合并），用于互斥占格校验（02C:65）。

    只读。`exclude` 里的 id 不参与占用——移动物件时把自己的旧格排除，才能校验新格。
    """
    excluded = set(exclude or ())
    occupied = {}
    for participant in battle_state.get("participants") or []:
        if not isinstance(participant, dict):
            continue
        unit_id = participant.get("unit_id")
        if not _is_non_empty_str(unit_id) or unit_id in excluded:
            continue
        for cell in object_cells(battlefield, participant) or ():
            occupied.setdefault(cell.get("cell_id"), unit_id)
    for instance in _objects(battle_state):
        if not isinstance(instance, dict):
            continue
        object_id = instance.get("object_id")
        if not _is_non_empty_str(object_id) or object_id in excluded:
            continue
        for cell in object_cells(battlefield, instance) or ():
            occupied.setdefault(cell.get("cell_id"), object_id)
    return occupied


# ── 状态机 ──────────────────────────────────────────────────────────────

def _sources(transition):
    source = transition.get("from")
    return (source,) if isinstance(source, str) else tuple(source)


def legal_triggers(definition, state):
    """`state` 下允许的互动入口名（按名字排序，确定性）。"""
    transitions = definition.get("transitions") or []
    return tuple(sorted({
        transition["trigger"] for transition in transitions
        if isinstance(transition, dict) and _is_non_empty_str(transition.get("trigger"))
        and state in _sources(transition)
    }))


def can_transition(definition, from_state, trigger):
    """判定 `from_state --trigger--> ?` 是否被定义允许。

    返回 `(transition | None, reasons)`；`reasons` 为空即通过。
    """
    states = definition.get("states") or {}
    if from_state not in states:
        return None, [_reason(
            "unknown_state",
            "实例状态 %r 不在定义的状态集合里（%s）" % (from_state, " / ".join(sorted(states))),
            state=from_state,
        )]
    transitions = [item for item in (definition.get("transitions") or [])
                   if isinstance(item, dict)]
    if trigger not in {item.get("trigger") for item in transitions}:
        return None, [_reason(
            "unknown_trigger",
            "定义未声明互动入口 %r（可用 %s）"
            % (trigger, " / ".join(sorted({item.get("trigger") for item in transitions})) or "无"),
            trigger=trigger,
            triggers=legal_triggers(definition, from_state),
        )]
    allowed = legal_triggers(definition, from_state)
    for transition in transitions:
        if transition.get("trigger") == trigger and from_state in _sources(transition):
            return transition, []
    targets = sorted(
        transition.get("to") for transition in transitions
        if transition.get("trigger") == trigger and transition.get("to") in states
    )
    return None, [_reason(
        "illegal_transition",
        "状态 %r 不接受 %r；该入口只在 %s 可用"
        % (from_state, trigger, " / ".join(targets) or "未声明"),
        state=from_state,
        trigger=trigger,
        allowed_triggers=list(allowed),
    )]


def idempotency_key(battle_id, event_sequence, object_id, trigger):
    """幂等键：`(battle_id, event_sequence, object_id, trigger)` 的唯一编码。"""
    return "%s|%s|%s|%s" % (battle_id, event_sequence, object_id, trigger)


# ── 提案骨架 ────────────────────────────────────────────────────────────

def _battle_id(battle_state):
    if not isinstance(battle_state, dict):
        raise TypeError("battle_state 必须是 dict")
    battle_id = battle_state.get("battle_id")
    if not _is_non_empty_str(battle_id):
        raise ValueError("battle_state.battle_id 必须是非空字符串")
    return battle_id


def _sequence(battle_state, event_sequence):
    if event_sequence is None:
        last = battle_state.get("last_event_sequence", 0)
        last = last if _is_int(last) and last >= 0 else 0
        event_sequence = last + 1
    if not _is_int(event_sequence) or event_sequence < 1:
        raise ValueError("event_sequence 必须是不小于 1 的整数（实为 %r）" % (event_sequence,))
    return event_sequence


def _revision(battle_state, expected_revision):
    current = battle_state.get("state_revision", 0)
    current = current if _is_int(current) and current >= 0 else 0
    if expected_revision is None:
        return current
    if not _is_int(expected_revision) or expected_revision < 0:
        raise ValueError("expected_revision 必须是不小于 0 的整数（实为 %r）" % (expected_revision,))
    return expected_revision


def _check_engine(rule_engine):
    if rule_engine not in RULE_ENGINES:
        raise ValueError("rule_engine 必须是 %s 之一（实为 %r）"
                         % (" / ".join(RULE_ENGINES), rule_engine))
    return rule_engine


def _proposal(battle_id, object_id, definition_id, trigger, sequence, expected_revision):
    return {
        "battle_id": battle_id,
        "object_id": object_id,
        "definition_id": definition_id,
        "trigger": trigger,
        "idempotency_key": idempotency_key(battle_id, sequence, object_id, trigger),
        "event_sequence": sequence,
        "expected_revision": expected_revision,
        "accepted": False,
        "duplicate": False,
        "transition": None,
        "state_delta": {},
        "cell_deltas": [],
        "inventory_proposals": [],
        "events": [],
        "reasons": [],
    }


def _reject(proposal, reasons):
    proposal["accepted"] = False
    proposal["reasons"] = list(reasons)
    return proposal


def _duplicate(proposal):
    """命中幂等键：不产生任何 delta / 事件，明确标记 duplicate。"""
    proposal["accepted"] = False
    proposal["duplicate"] = True
    proposal["reasons"] = [_reason(
        "duplicate_request",
        "幂等键 %s 已结算，重复请求不再产生 delta 或事件" % proposal["idempotency_key"],
        idempotency_key=proposal["idempotency_key"],
    )]
    return proposal


def _check_revision(proposal, current):
    if proposal["expected_revision"] != current:
        return _reject(proposal, [_reason(
            "revision_mismatch",
            "expected_revision=%r 与 battle_state.state_revision=%r 不一致（陈旧输入）"
            % (proposal["expected_revision"], current),
            expected_revision=proposal["expected_revision"],
            state_revision=current,
        )])
    return None


# ── 空间效果 → delta ────────────────────────────────────────────────────

def _resolve_cell_value(field, declared, cell):
    """把状态声明解析成目标值；`None` 表示移除该属性。"""
    if declared is None:
        return None
    if declared != DOWNGRADE:
        return declared
    grades = _cover_grades()
    current = cell.get("cover_grade")
    if current not in grades:
        return None
    index = grades.index(current)
    return grades[index - 1] if index else None


def _state_object_patch(instance, state_effects, to_state):
    patch = {"state": to_state}
    for field in sorted(state_effects.get("object") or {}):
        value = state_effects["object"][field]
        if instance.get(field) != value:
            patch[field] = value
    return patch


def _state_cell_deltas(cells, cell_effects):
    deltas = []
    for cell in sorted(cells or (), key=lambda item: str(item.get("cell_id"))):
        sets = {}
        unsets = []
        for field in sorted(cell_effects):
            wanted = _resolve_cell_value(field, cell_effects[field], cell)
            if wanted is None:
                if field in cell:
                    unsets.append(field)
            elif cell.get(field) != wanted:
                sets[field] = wanted
        if sets or unsets:
            deltas.append({"cell_id": cell.get("cell_id"), "set": sets, "unset": sorted(unsets)})
    return deltas


def _event(event_type, *, sequence, expected_revision, rule_engine, result,
           actor_id=None, target_id=None, from_cell_id=None, to_cell_id=None,
           source_rule_ref=None):
    """构造 `tactical.battle_event` 封套（只放 schema 允许的字段）。"""
    event = {
        "schema_version": 1,
        "kind": "tactical.battle_event",
        "sequence": sequence,
        "type": event_type,
        "rule_engine": rule_engine,
        "expected_revision": expected_revision,
        "result": result,
    }
    if _is_non_empty_str(actor_id):
        event["actor_id"] = actor_id
    if _is_non_empty_str(target_id):
        event["target_id"] = target_id
    if _is_non_empty_str(from_cell_id):
        event["from_cell_id"] = from_cell_id
    if _is_non_empty_str(to_cell_id):
        event["to_cell_id"] = to_cell_id
    if _is_non_empty_str(source_rule_ref):
        event["source_rule_ref"] = source_rule_ref
    return event


# ── 公共前置：实例 / 定义 / 幂等 / 状态机 ────────────────────────────────

def _resolve_request(battle_state, battlefield, definitions, object_id, trigger, *,
                     event_sequence, expected_revision, rule_engine, applied_keys,
                     required_kind=None):
    """解析一次物件操作请求。

    返回 `(proposal, context)`；`context` 为 `None` 表示已拒绝 / 幂等命中，
    调用方只需把 `proposal` 交回即可。
    """
    battle_id = _battle_id(battle_state)
    sequence = _sequence(battle_state, event_sequence)
    expected = _revision(battle_state, expected_revision)
    _check_engine(rule_engine)
    if not _is_non_empty_str(object_id):
        raise ValueError("object_id 必须是非空字符串")
    if not _is_non_empty_str(trigger):
        raise ValueError("trigger 必须是非空字符串")

    index = definition_index(definitions)
    instance = object_by_id(battle_state, object_id)
    proposal = _proposal(
        battle_id, object_id,
        instance.get("definition_id") if isinstance(instance, dict) else None,
        trigger, sequence, expected,
    )
    proposal["rule_engine"] = rule_engine

    if instance is None:
        return _reject(proposal, [_reason(
            "unknown_object", "battle_state.objects 里没有 object_id=%r" % (object_id,),
            object_id=object_id,
        )]), None

    definition = index.get(instance.get("definition_id"))
    if definition is None:
        return _reject(proposal, [_reason(
            "unknown_definition",
            "定义集合里没有 definition_id=%r" % (instance.get("definition_id"),),
            definition_id=instance.get("definition_id"),
        )]), None

    errors = validate_definition(definition)
    if errors:
        return _reject(proposal, [_reason(
            "invalid_definition", "定义未通过校验", errors=errors,
            definition_id=definition.get("definition_id"),
        )]), None

    rejected = _check_revision(proposal, battle_state.get("state_revision", 0))
    if rejected is not None:
        return rejected, None

    if required_kind is not None and required_kind not in (definition.get("kinds") or []):
        return _reject(proposal, [_reason(
            _KIND_REJECT_CODES.get(required_kind, "missing_kind"),
            "定义 %r 未声明 %s 类别" % (definition.get("definition_id"), required_kind),
            definition_id=definition.get("definition_id"),
            required_kind=required_kind,
        )]), None

    if proposal["idempotency_key"] in (applied_keys or ()):
        return _duplicate(proposal), None

    transition, reasons = can_transition(definition, instance.get("state"), trigger)
    if transition is None:
        return _reject(proposal, reasons), None

    cells = object_cells(battlefield, instance)
    cell_effects = (definition.get("states") or {}).get(transition["to"], {}).get("cells") or {}
    if cell_effects and cells is None:
        return _reject(proposal, [_reason(
            "out_of_bounds",
            "物件 %s 的 footprint 无法在战场上解析（cell_id=%r）"
            % (object_id, instance.get("cell_id")),
            object_id=object_id,
        )]), None

    return proposal, {
        "instance": instance,
        "definition": definition,
        "transition": transition,
        "cells": cells,
    }


def _fill_transition_output(proposal, context, *, actor_id, rule_engine, extra_result=None):
    """把状态机结果写进提案：state_delta / cell_deltas / events。"""
    instance = context["instance"]
    definition = context["definition"]
    transition = context["transition"]
    from_state = instance.get("state")
    to_state = transition["to"]
    state_effects = (definition.get("states") or {}).get(to_state) or {}

    object_patch = _state_object_patch(instance, state_effects, to_state)
    cell_deltas = _state_cell_deltas(context["cells"], state_effects.get("cells") or {})

    proposal["transition"] = {"from": from_state, "to": to_state}
    proposal["state_delta"] = {instance.get("object_id"): object_patch}
    proposal["cell_deltas"] = cell_deltas
    proposal["accepted"] = True

    result = {
        "summary": "%s: %s → %s（%s）"
                   % (instance.get("object_id"), from_state, to_state, proposal["trigger"]),
        "op": "object_state_changed",
        "object_id": instance.get("object_id"),
        "definition_id": definition.get("definition_id"),
        "from_state": from_state,
        "to_state": to_state,
        "trigger": proposal["trigger"],
        "state_delta": proposal["state_delta"],
        "cell_deltas": cell_deltas,
    }
    if extra_result:
        result.update(extra_result)

    proposal["events"] = [_event(
        "object_state_changed",
        sequence=proposal["event_sequence"],
        expected_revision=proposal["expected_revision"],
        rule_engine=rule_engine,
        actor_id=actor_id,
        target_id=instance.get("object_id"),
        from_cell_id=instance.get("cell_id"),
        source_rule_ref=definition.get("source"),
        result=result,
    )]
    return proposal


# ── 对外入口 1：互动（开门 / 上锁 / 引爆 / 破坏 / 断电 …） ─────────────────

def interact(battle_state, battlefield, *, definitions, object_id, action,
             event_sequence=None, expected_revision=None, actor_id=None,
             rule_engine="tactics", applied_keys=None):
    """对物件发起一次互动，返回**提案**（不写权威状态、不扣 AP）。

    `action` 必须命中定义里声明的转换 `trigger`；非法转换返回
    `accepted=False` + `reasons[]`（`illegal_transition` / `unknown_trigger` /
    `duplicate_request` / `revision_mismatch` / …）。
    """
    proposal, context = _resolve_request(
        battle_state, battlefield, definitions, object_id, action,
        event_sequence=event_sequence,
        expected_revision=expected_revision,
        rule_engine=_check_engine(rule_engine),
        applied_keys=applied_keys,
    )
    if context is None:
        return proposal
    return _fill_transition_output(proposal, context, actor_id=actor_id, rule_engine=rule_engine)


# ── 对外入口 2：物件移动（复校 footprint 与占用） ─────────────────────────

def move_object(battle_state, battlefield, *, definitions, object_id, to_cell_id,
                footprint=None, event_sequence=None, expected_revision=None,
                actor_id=None, rule_engine="tactics", applied_keys=None, occupied=None):
    """把可移动物件挪到 `to_cell_id`，返回提案（不写权威状态）。

    先 `footprint_cells` 复校目标 footprint，再 `validate_placement` 复校越界 /
    `blocks_movement` / 互斥占用；自身旧格不参与占用判定。放置建议由调用方提交，
    本模块**不**改战场。
    """
    battle_id = _battle_id(battle_state)
    sequence = _sequence(battle_state, event_sequence)
    expected = _revision(battle_state, expected_revision)
    _check_engine(rule_engine)
    if not _is_non_empty_str(object_id):
        raise ValueError("object_id 必须是非空字符串")
    if not _is_non_empty_str(to_cell_id):
        raise ValueError("to_cell_id 必须是非空字符串")

    index = definition_index(definitions)
    instance = object_by_id(battle_state, object_id)
    proposal = _proposal(
        battle_id, object_id,
        instance.get("definition_id") if isinstance(instance, dict) else None,
        "move", sequence, expected,
    )
    proposal["rule_engine"] = rule_engine
    proposal["to_cell_id"] = to_cell_id

    if instance is None:
        return _reject(proposal, [_reason(
            "unknown_object", "battle_state.objects 里没有 object_id=%r" % (object_id,),
            object_id=object_id,
        )])
    definition = index.get(instance.get("definition_id"))
    if definition is None:
        return _reject(proposal, [_reason(
            "unknown_definition",
            "定义集合里没有 definition_id=%r" % (instance.get("definition_id"),),
            definition_id=instance.get("definition_id"),
        )])
    errors = validate_definition(definition)
    if errors:
        return _reject(proposal, [_reason(
            "invalid_definition", "定义未通过校验", errors=errors,
            definition_id=definition.get("definition_id"),
        )])
    rejected = _check_revision(proposal, battle_state.get("state_revision", 0))
    if rejected is not None:
        return rejected
    if proposal["idempotency_key"] in (applied_keys or ()):
        return _duplicate(proposal)

    if "movable" not in (definition.get("kinds") or []):
        return _reject(proposal, [_reason(
            "not_movable", "定义 %r 未声明 movable 类别，不可移动"
            % (definition.get("definition_id"),),
            definition_id=definition.get("definition_id"),
        )])
    if instance.get("movable") is False:
        return _reject(proposal, [_reason(
            "not_movable", "实例 movable=false，禁止移动", object_id=object_id,
        )])

    target_footprint = list(footprint) if isinstance(footprint, list) and footprint \
        else _footprint_of(instance)
    try:
        target_cells = tactical_battlefield.footprint_cells(
            battlefield, to_cell_id, target_footprint)
    except (ValueError, KeyError, TypeError) as error:
        return _reject(proposal, [_reason(
            "out_of_bounds", "目标 footprint 无法解析: %s" % (error,),
            to_cell_id=to_cell_id,
        )])

    occupancy = occupancy_map(battle_state, battlefield, exclude=[object_id])
    occupancy.update(occupied or {})
    placement = tactical_battlefield.validate_placement(
        battlefield, object_id, to_cell_id, target_footprint, occupancy)
    if placement:
        return _reject(proposal, [_reason(
            "blocked_placement",
            "目标格不可放置: %s" % "; ".join(item["message"] for item in placement),
            errors=placement,
        )])

    if instance.get("cell_id") == to_cell_id and _footprint_of(instance) == target_footprint:
        return _reject(proposal, [_reason(
            "same_position", "物件已在 %r，footprint 未变化" % (to_cell_id,),
            cell_id=to_cell_id,
        )])

    patch = {"cell_id": to_cell_id}
    if target_footprint != _footprint_of(instance):
        patch["footprint"] = [dict(item) for item in target_footprint]
    proposal["state_delta"] = {object_id: patch}
    proposal["accepted"] = True
    proposal["events"] = [_event(
        "object_state_changed",
        sequence=sequence,
        expected_revision=expected,
        rule_engine=rule_engine,
        actor_id=actor_id,
        target_id=object_id,
        from_cell_id=instance.get("cell_id"),
        to_cell_id=to_cell_id,
        source_rule_ref=definition.get("source"),
        result={
            "summary": "%s: %s → %s（物件移动）"
                       % (object_id, instance.get("cell_id"), to_cell_id),
            "op": "object_moved",
            "object_id": object_id,
            "definition_id": definition.get("definition_id"),
            "from_cell_id": instance.get("cell_id"),
            "to_cell_id": to_cell_id,
            "state_delta": proposal["state_delta"],
            "cell_deltas": [],
        },
    )]
    return proposal


# ── 对外入口 3：拾取（只出「进入权威库存」的提案） ────────────────────────

def pickup(battle_state, battlefield, *, definitions, object_id, unit_id,
           event_sequence=None, expected_revision=None, rule_engine="tactics",
           applied_keys=None):
    """拾取物件：返回 `inventory_proposals`（目标系统由调用方决定），不改库存。

    必须由定义声明 `pickup` 入口的转换（`{"from": …, "to": …, "trigger": "pickup"}`）；
    定义里的 `pickup` 块原样进 `payload`。
    """
    proposal, context = _resolve_request(
        battle_state, battlefield, definitions, object_id, "pickup",
        event_sequence=event_sequence,
        expected_revision=expected_revision,
        rule_engine=_check_engine(rule_engine),
        applied_keys=applied_keys,
        required_kind="pickup",
    )
    if context is None:
        return proposal
    if not _is_non_empty_str(unit_id):
        raise ValueError("unit_id 必须是非空字符串")

    definition = context["definition"]
    inventory = {
        "unit_id": unit_id,
        "object_id": context["instance"].get("object_id"),
        "definition_id": definition.get("definition_id"),
        "target": "authoritative_inventory",
        "payload": dict(definition.get("pickup") or {}),
    }
    _fill_transition_output(
        proposal, context,
        actor_id=unit_id,
        rule_engine=proposal["rule_engine"],
        extra_result={"op": "object_picked_up", "unit_id": unit_id,
                      "inventory_proposal": inventory},
    )
    proposal["inventory_proposals"] = [inventory]
    return proposal


# ── 对外入口 4：危险触发（进入 / 离开 / 互动，幂等） ──────────────────────

def _hazard_objects(battle_state, battlefield, definitions, cell, phase):
    """挑出对 `cell` 的 `phase` 事件有效的危险物件（按 object_id 排序）。"""
    index = definition_index(definitions)
    cell_refs = set(cell.get("hazard_refs") or [])
    matched = []
    notes = []
    for instance in sorted(_objects(battle_state), key=lambda item: str(item.get("object_id"))):
        if not isinstance(instance, dict):
            continue
        object_id = instance.get("object_id")
        definition = index.get(instance.get("definition_id"))
        if definition is None:
            notes.append(_reason("unknown_definition",
                                 "%r 的 definition_id 不在定义集合里" % (object_id,),
                                 object_id=object_id))
            continue
        if "hazard" not in (definition.get("kinds") or []):
            continue
        refs = set(definition.get("hazard_refs") or [])
        if not refs & cell_refs:
            continue  # 危险引用未命中：该物件与此格无关
        phases = definition.get("hazard_triggers") or TRIGGER_PHASES
        if phase not in phases:
            notes.append(_reason("phase_not_declared",
                                 "%r 未声明 %r 相位" % (object_id, phase),
                                 object_id=object_id))
            continue
        cells = object_cells(battlefield, instance)
        if not cells or not any(item.get("cell_id") == cell.get("cell_id") for item in cells):
            notes.append(_reason("cell_mismatch",
                                 "%r 的 footprint 不覆盖 %r"
                                 % (object_id, cell.get("cell_id")),
                                 object_id=object_id))
            continue
        if instance.get("triggered") is True:
            notes.append(_reason("already_triggered",
                                 "%r 的 triggered=true，一次性效果不再触发" % (object_id,),
                                 object_id=object_id))
            continue
        transition, reasons = can_transition(definition, instance.get("state"), phase)
        if transition is None:
            notes.append(_reason("state_not_armed",
                                 "%r 在状态 %r 下不接受 %r"
                                 % (object_id, instance.get("state"), phase),
                                 object_id=object_id, detail=reasons))
            continue
        matched.append({"instance": instance, "definition": definition,
                        "transition": transition, "cells": cells})
    return matched, notes


def trigger_hazard(battle_state, battlefield, *, definitions, unit_id, cell_id, phase,
                   event_sequence=None, expected_revision=None, rule_engine="tactics",
                   applied_keys=None):
    """单位 `unit_id` 因 `phase`（enter / leave / interact）踏入 `cell_id` 的结算提案。

    只有「格上 `hazard_refs` ∩ 定义 `hazard_refs` 非空、footprint 覆盖该格、状态机
    接受该相位」的物件才会触发；每次调用只结算**一个**物件（按 object_id 确定性取
    首个），其余 id 放在 `deferred_object_ids` 供调用方继续请求——这样幂等键
    `(battle_id, event_sequence, object_id, phase)` 始终唯一。
    """
    if phase not in TRIGGER_PHASES:
        raise ValueError("phase 必须是 %s 之一（实为 %r）"
                         % (" / ".join(TRIGGER_PHASES), phase))
    battle_id = _battle_id(battle_state)
    sequence = _sequence(battle_state, event_sequence)
    expected = _revision(battle_state, expected_revision)
    _check_engine(rule_engine)
    if not _is_non_empty_str(unit_id):
        raise ValueError("unit_id 必须是非空字符串")
    if not _is_non_empty_str(cell_id):
        raise ValueError("cell_id 必须是非空字符串")

    cell = tactical_battlefield.cell_by_id(battlefield, cell_id)
    if cell is None:
        proposal = _proposal(battle_id, None, None, phase, sequence, expected)
        proposal["rule_engine"] = rule_engine
        proposal["cell_id"] = cell_id
        proposal["unit_id"] = unit_id
        return _reject(proposal, [_reason(
            "unknown_cell", "战场里没有 cell_id=%r" % (cell_id,), cell_id=cell_id,
        )])

    matched, notes = _hazard_objects(battle_state, battlefield, definitions, cell, phase)
    if not matched:
        proposal = _proposal(battle_id, None, None, phase, sequence, expected)
        proposal["rule_engine"] = rule_engine
        proposal["cell_id"] = cell_id
        proposal["unit_id"] = unit_id
        proposal["hazard_refs"] = list(cell.get("hazard_refs") or [])
        return _reject(proposal, [_reason(
            "no_hazard_matched",
            "%r 上没有对 %r 有效的危险物件（hazard_refs=%r）"
            % (cell_id, phase, cell.get("hazard_refs") or []),
            cell_id=cell_id, phase=phase, notes=notes,
        )])

    chosen = matched[0]
    instance = chosen["instance"]
    definition = chosen["definition"]
    proposal = _proposal(battle_id, instance.get("object_id"),
                         definition.get("definition_id"), phase, sequence, expected)
    proposal["rule_engine"] = rule_engine
    proposal["cell_id"] = cell_id
    proposal["unit_id"] = unit_id
    proposal["hazard_refs"] = sorted(set(definition.get("hazard_refs") or [])
                                     & set(cell.get("hazard_refs") or []))
    proposal["deferred_object_ids"] = [item["instance"].get("object_id")
                                       for item in matched[1:]]

    rejected = _check_revision(proposal, battle_state.get("state_revision", 0))
    if rejected is not None:
        return rejected
    if proposal["idempotency_key"] in (applied_keys or ()):
        return _duplicate(proposal)

    from_state = instance.get("state")
    to_state = chosen["transition"]["to"]
    state_effects = (definition.get("states") or {}).get(to_state) or {}
    object_patch = _state_object_patch(instance, state_effects, to_state)
    # 一次性效果天然置位 triggered，形成第二层幂等保护（重复请求不重复触发）。
    object_patch.setdefault("triggered", True)
    cell_deltas = _state_cell_deltas(chosen["cells"], state_effects.get("cells") or {})

    proposal["transition"] = {"from": from_state, "to": to_state}
    proposal["state_delta"] = {instance.get("object_id"): object_patch}
    proposal["cell_deltas"] = cell_deltas
    proposal["accepted"] = True
    proposal["events"] = [_event(
        "hazard_triggered",
        sequence=sequence,
        expected_revision=expected,
        rule_engine=rule_engine,
        actor_id=unit_id,
        target_id=instance.get("object_id"),
        to_cell_id=cell_id,
        source_rule_ref=definition.get("source"),
        result={
            "summary": "%s 在 %r 触发危险 %r（%s）"
                       % (instance.get("object_id"), cell_id, proposal["hazard_refs"], phase),
            "op": "hazard_triggered",
            "object_id": instance.get("object_id"),
            "definition_id": definition.get("definition_id"),
            "unit_id": unit_id,
            "cell_id": cell_id,
            "phase": phase,
            "hazard_refs": proposal["hazard_refs"],
            "from_state": from_state,
            "to_state": to_state,
            "state_delta": proposal["state_delta"],
            "cell_deltas": cell_deltas,
        },
    )]
    return proposal
