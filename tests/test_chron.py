#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chron 内核（M9-C1 / C2）回归测试：世界时钟与行动生命周期（纯函数）。

覆盖 `CHRON-DESIGN.md` 切片 C1 的验收标准：

- `watch_of` 在 05:00 / 09:00 / 17:00 / 21:00 边界正确（晨 / 昼 / 昏 / 夜**不等长**）。
- 分钟 ↔ 日历**往返全等**；负 delta / 回拨被**显式拒绝**。
- `NOMINAL_WATCH_MINUTES == 360` 与 `MINOR_ACTION_MINUTES == 10`
  与 `data/system/watch.json` / `data/system/travel.json` 值**一致**。
- `chron.py` 纯函数：不 import 任何模块、不碰 IO（静态审查 + 行为断言）。

覆盖切片 C2 的验收标准（§4 行动生命周期）：

- §4.2 表里**每一行**各一条用例（含 `interrupted → completed/failed` 拆成的两行）；
- **终态不可逆**；`interrupted → scheduled` 用**剩余耗时**重排；`blocked ⇄ scheduled` 往返；
- M8 同步路径 `proposed → running → completed/failed` **无 `scheduled`** 也能走；
- 产出事件含 GDD §19.5 的最小字段（id / 世界时间 / 类型 / 主体 / 原因 / 状态变化）；
- 每次转换**恰好一条**事件、非法转换**显式抛错**、入参不被改写（纯函数）。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_chron.py` 运行。
"""

import ast
import json
import os
import re
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import chron  # noqa: E402  (ROOT 已加入 sys.path)

CHRON_PY = os.path.join(ROOT, "chron.py")
WATCH_JSON = os.path.join(ROOT, "data", "system", "watch.json")
TRAVEL_JSON = os.path.join(ROOT, "data", "system", "travel.json")

# 四段时段的**期望边界**（分钟）：与 data/system/watch.json 的 05–09 / 09–17 / 17–21 / 21–05 同源。
EXPECTED_STARTS = {"dawn": 5 * 60, "day": 9 * 60, "dusk": 17 * 60, "night": 21 * 60}


def _load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _hours_span(label):
    """把 watch.json 的 `hours` 标签（如 '05–09' / '21–05'）折成小时跨度（含跨夜）。"""
    nums = re.findall(r"\d+", label)
    assert len(nums) >= 2, "hours 标签无法解析: %r" % label
    start, end = int(nums[0]), int(nums[1])
    return (end - start) % 24 or 24


# --------------------------------------------------------------------------
# §2.4 常量
# --------------------------------------------------------------------------

def check_minute_constants():
    """分钟常数自洽，且与 §2.4 表逐值一致。"""
    assert chron.SECONDS_PER_MINUTE == 60
    assert chron.MINUTES_PER_HOUR == 60
    assert chron.MINUTES_PER_DAY == 24 * chron.MINUTES_PER_HOUR == 1440
    assert chron.NOMINAL_WATCH_MINUTES == 360
    assert chron.MINOR_ACTION_MINUTES == 10
    assert chron.MAJOR_ACTION_MINUTES == (60, 180)
    assert chron.SESSION_WATCHES == (3, 6)
    # 四段时段恰好铺满一天，且集合与顺序表一致。
    assert sum(chron.WATCH_SPANS.values()) == chron.MINUTES_PER_DAY
    assert set(chron.WATCH_SPANS) == set(chron.WATCH_ORDER)
    # §2.5：第一版只预留字段、不实现。
    assert chron.RATE_DOMAINS == {}


def check_watch_spans_unequal():
    """四段时段**不等长**（不能当成等长单位做乘法）。"""
    assert chron.WATCH_SPANS == {"dawn": 240, "day": 480, "dusk": 240, "night": 480}
    assert len(set(chron.WATCH_SPANS.values())) == 2, "四段应不等长，不应全相等"
    assert chron.WATCH_SPANS["day"] != chron.WATCH_SPANS["dawn"]


def check_watch_boundaries():
    """`watch_of` 在 05:00 / 09:00 / 17:00 / 21:00 四个边界取「新段」。"""
    # 边界当分钟归属**新段**。
    assert chron.watch_of(5 * 60) == "dawn", "05:00 应属晨"
    assert chron.watch_of(9 * 60) == "day", "09:00 应属昼"
    assert chron.watch_of(17 * 60) == "dusk", "17:00 应属昏"
    assert chron.watch_of(21 * 60) == "night", "21:00 应属夜"
    # 边界前一分钟仍属**旧段**。
    assert chron.watch_of(5 * 60 - 1) == "night"
    assert chron.watch_of(9 * 60 - 1) == "dawn"
    assert chron.watch_of(17 * 60 - 1) == "day"
    assert chron.watch_of(21 * 60 - 1) == "dusk"
    # 跨夜首尾都属夜。
    assert chron.watch_of(0) == "night"
    assert chron.watch_of(chron.MINUTES_PER_DAY - 1) == "night"
    # 传入世界分钟（含整天偏移）与传入当日分钟同解（内部按当天展开）。
    assert chron.watch_of(3 * chron.MINUTES_PER_DAY + 5 * 60) == "dawn"


def check_watch_span_measurement():
    """逐分钟实测四段长度，必须**恰好**等于 WATCH_SPANS（证明边界是实际边界，非整除）。"""
    counts = {name: 0 for name in chron.WATCH_ORDER}
    for tod in range(chron.MINUTES_PER_DAY):
        counts[chron.watch_of(tod)] += 1
    assert counts == chron.WATCH_SPANS, "实测四段长度 %r != WATCH_SPANS %r" % (
        counts, chron.WATCH_SPANS)
    # 起始分钟即四个边界。
    assert chron.WATCH_START_MINUTES == EXPECTED_STARTS
    # 若按等长 360 整除，昼/夜长度会被算错——这里显式证伪。
    assert chron.watch_of(360) == "dawn", "360 分钟处仍是晨（昼自 540 起），整除口径会出错"


# --------------------------------------------------------------------------
# §3.1 日历 ↔ 分钟
# --------------------------------------------------------------------------

def check_calendar_roundtrip():
    """分钟 ↔ 日历**往返全等**，且派生字段取值范围正确。"""
    samples = [0, 1, 59, 60, 299, 300, 539, 540, 1019, 1020, 1259, 1260,
               1439, 1440, 1441, 17310, 123456, 10 ** 7 + 7]
    samples += list(range(0, chron.MINUTES_PER_DAY, 37))
    for minute in samples:
        cal = chron.to_calendar(minute)
        assert cal["world_minute"] == minute
        assert cal["day"] >= 1
        assert 0 <= cal["tod"] < chron.MINUTES_PER_DAY
        assert 0 <= cal["hh"] <= 23 and 0 <= cal["mm"] <= 59
        assert cal["watch"] == chron.watch_of(minute)
        back = chron.from_calendar(cal["day"], cal["hh"], cal["mm"])
        assert back == minute, "往返不等：%d -> %r -> %d" % (minute, cal, back)


def check_calendar_formula():
    """`day = minute // 1440 + 1`、`tod = minute % 1440`、`hh:mm = tod // 60 : tod % 60`。"""
    assert chron.to_calendar(0) == {
        "world_minute": 0, "day": 1, "tod": 0, "hh": 0, "mm": 0, "watch": "night"}
    assert chron.to_calendar(chron.MINUTES_PER_DAY) == {
        "world_minute": 1440, "day": 2, "tod": 0, "hh": 0, "mm": 0, "watch": "night"}
    # 17310 = 12*1440 + 30：按 §3.1 的**公式** day = 17310 // 1440 + 1 = 13。
    # （设计文档 §3.1 的示例对象把 day 写成 12，与其自身公式冲突，按公式为准；
    #   doc 的修订属另一个 PR，本单不改 docs/。）
    cal = chron.to_calendar(17310)
    assert cal["day"] == 13 and cal["tod"] == 30 and cal["hh"] == 0 and cal["mm"] == 30
    assert chron.from_calendar(1, 0, 0) == 0
    assert chron.from_calendar(1, 5, 0) == 5 * 60
    assert chron.from_calendar(2, 0, 0) == chron.MINUTES_PER_DAY


def check_from_calendar_rejects_bad_input():
    """非法日历字段显式拒绝（不静默）。"""
    for args in ((0, 0, 0), (-1, 0, 0), (1, 24, 0), (1, -1, 0), (1, 0, 60), (1, 0, -1)):
        try:
            chron.from_calendar(*args)
        except ValueError:
            continue
        raise AssertionError("from_calendar%r 应被拒绝" % (args,))
    for args in ((1.0, 0, 0), ("1", 0, 0), (True, 0, 0)):
        try:
            chron.from_calendar(*args)
        except TypeError:
            continue
        raise AssertionError("from_calendar%r 类型应被拒绝" % (args,))


# --------------------------------------------------------------------------
# §3.2 推进与单调性
# --------------------------------------------------------------------------

def check_advance_clock_monotonic():
    """`advance_clock` 只增不减；负 delta / 回拨被**显式拒绝**。"""
    assert chron.advance_clock(0, 0) == 0
    assert chron.advance_clock(100, 50) == 150
    assert chron.advance_clock(0, chron.MINUTES_PER_DAY) == chron.MINUTES_PER_DAY
    # 单调不减：任意非负 delta 下结果 ≥ 起点。
    for start in (0, 1, 1439, 1440, 99999):
        for delta in (0, 1, 10, 360, 1440):
            assert chron.advance_clock(start, delta) >= start
    # 回拨（负 delta）必须显式拒绝。
    for delta in (-1, -10, -1440):
        try:
            chron.advance_clock(1000, delta)
        except ValueError:
            continue
        raise AssertionError("负 delta %d 应被拒绝" % delta)


def check_clock_input_validation():
    """世界分钟为负、类型不对时显式拒绝。"""
    for func, args in ((chron.watch_of, (-1,)),
                       (chron.to_calendar, (-1,)),
                       (chron.advance_clock, (-1, 10))):
        try:
            func(*args)
        except ValueError:
            continue
        raise AssertionError("%s%r 应因负分钟被拒绝" % (func.__name__, args))
    for func, args in ((chron.watch_of, (1.0,)),
                       (chron.to_calendar, ("0",)),
                       (chron.advance_clock, (0, 1.5))):
        try:
            func(*args)
        except TypeError:
            continue
        raise AssertionError("%s%r 应因类型被拒绝" % (func.__name__, args))


# --------------------------------------------------------------------------
# 与 data/ 的口径一致
# --------------------------------------------------------------------------

def check_data_consistency():
    """常量与 `data/system/watch.json` / `data/system/travel.json` 值一致。"""
    watch = _load_json(WATCH_JSON)
    travel = _load_json(TRAVEL_JSON)

    # 时段长度：watch.json 的 hours 标签折成分钟 == WATCH_SPANS。
    for entry in watch["watches"]:
        span = _hours_span(entry["hours"]) * chron.MINUTES_PER_HOUR
        assert span == chron.WATCH_SPANS[entry["id"]], (
            "watch.json %s(%s) = %d 分钟 != WATCH_SPANS %d"
            % (entry["id"], entry["hours"], span, chron.WATCH_SPANS[entry["id"]]))

    # 预算 / 节奏。
    budget = watch["budget"]
    assert chron.MINOR_ACTION_MINUTES == budget["minor_minutes"] == 10
    assert chron.MAJOR_ACTION_MINUTES == (
        budget["major_hours_min"] * chron.MINUTES_PER_HOUR,
        budget["major_hours_max"] * chron.MINUTES_PER_HOUR)
    pacing = watch["pacing"]
    assert chron.SESSION_WATCHES == (pacing["session_watches_min"], pacing["session_watches_max"])

    # 行程：短程 = 1 名义时段 = NOMINAL_WATCH_MINUTES；中程 = 2×；远程 = 1 天。
    routes = {route["id"]: route for route in travel["routes"]}
    assert routes["short"]["minutes"] == chron.NOMINAL_WATCH_MINUTES == 360
    assert routes["medium"]["minutes"] == 2 * chron.NOMINAL_WATCH_MINUTES
    assert routes["long"]["minutes"] == chron.MINUTES_PER_DAY
    assert routes["dangerous"]["minutes"] >= chron.MINUTES_PER_DAY


# --------------------------------------------------------------------------
# §4 行动生命周期（C2）
# --------------------------------------------------------------------------

BASE_MINUTE = 1000  # 用例的基准世界分钟（§3.1 只要求 ≥ 0）

# §4.2 全转换表逐行核对用：(from, to) → 产出事件。顺序即设计表顺序；
# 末行是 **M8 同步补充行**（`proposed → running`，基线 §4.3 第 2 条 / Key Decision 5）。
DESIGN_ROWS = (
    (None, "proposed", "action.proposed"),
    ("proposed", "scheduled", "action.scheduled"),
    ("proposed", "failed", "action.failed"),
    ("proposed", "cancelled", "action.cancelled"),
    ("scheduled", "running", "action.started"),
    ("scheduled", "cancelled", "action.cancelled"),
    ("scheduled", "blocked", "action.blocked"),
    ("scheduled", "interrupted", "action.interrupted"),
    ("blocked", "scheduled", "action.unblocked"),
    ("blocked", "cancelled", "action.cancelled"),
    ("running", "completed", "action.completed"),
    ("running", "failed", "action.failed"),
    ("running", "interrupted", "action.interrupted"),
    ("running", "cancelled", "action.cancelled"),
    ("interrupted", "scheduled", "action.resumed"),
    ("interrupted", "completed", "action.completed"),
    ("interrupted", "failed", "action.failed"),
    ("interrupted", "cancelled", "action.cancelled"),
    ("proposed", "running", "action.started"),  # M8 同步补充行
)

# 走到各状态所需的**表内**合法转换序列。
PATHS = {
    "proposed": ("proposed",),
    "scheduled": ("proposed", "scheduled"),
    "running": ("proposed", "scheduled", "running"),
    "blocked": ("proposed", "scheduled", "blocked"),
    "interrupted": ("proposed", "scheduled", "running", "interrupted"),
    "completed": ("proposed", "scheduled", "running", "completed"),
    "failed": ("proposed", "scheduled", "running", "failed"),
    "cancelled": ("proposed", "cancelled"),
}

EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


def _draft(action_id="a1", time_cost=360):
    """一张尚未提议的行动卡（§4.1）。"""
    return chron.new_action(action_id, "pc-1", "travel", time_cost, priority=1)


def _to_state(status, *, start_minute=BASE_MINUTE, minute=BASE_MINUTE):
    """沿表内合法路径走到 `status`，返回行动卡（顺带验证 `apply_event` 可用）。"""
    action = _draft()
    now = minute
    for step in PATHS[status]:
        ctx = {"world_minute": now}
        if step == "scheduled":
            ctx["start_minute"] = start_minute
        action = chron.apply_event(action, chron.transition(action, step, ctx))
        if action["start_minute"] is not None:
            now = max(now, action["start_minute"])
    assert action["status"] == status, "路径 %r 未到达 %s" % (PATHS[status], status)
    return action


def _apply(action, to, ctx):
    """转换 + 落盘一步：返回新行动卡（不改写入参）。"""
    return chron.apply_event(action, chron.transition(action, to, ctx))


def _change_for(event, field, op=None):
    """取事件里针对某字段的变更（找不到返回 None）。"""
    suffix = "/%s" % field
    hits = [c for c in event["changes"] if c["path"].endswith(suffix)
            and (op is None or c["op"] == op)]
    assert len(hits) <= 1, "字段 %s 出现多条变更：%r" % (field, hits)
    return hits[0] if hits else None


def _check_event_shape(event, action, world_minute, to):
    """事件形状：GDD §19.5 最小字段 + STATE §5.3 风格的 changes（可被 `apply_event` 消费）。"""
    # 1) GDD §19.5 最小字段：id / 世界时间 / 类型 / 主体 / 原因 / 状态变化。
    for field in ("event_id", "world_time", "type", "actor", "target", "cause", "changes"):
        assert field in event, "事件缺少 GDD §19.5 字段：%s" % field
    assert isinstance(event["event_id"], str) and event["event_id"]
    assert EVENT_TYPE_RE.match(event["type"]), "type 不是 <domain>.<verb>：%r" % event["type"]
    assert event["actor"] == action["actor"], "事件主体应来自行动卡"
    assert event["target"] == action["action_id"], "事件目标应是行动卡 id"
    # 2) world_time 是 §3.1 的唯一写法（三键，day / tod 由 world_minute 现算）。
    world_time = event["world_time"]
    assert set(world_time) == {"world_minute", "day", "tod"}, "world_time 形状应固定"
    assert world_time["world_minute"] == world_minute
    calendar = chron.to_calendar(world_minute)
    assert world_time["day"] == calendar["day"] and world_time["tod"] == calendar["tod"]
    # 3) changes：逐条 STATE §5.3 形状，且含「status: before → after」。
    prefix = "/actions/%s/" % action["action_id"]
    for change in event["changes"]:
        assert change["block"] == "chron", "变更应落在 chron 块"
        assert change["path"].startswith(prefix), "变更路径应指向本行动"
        assert change["op"] in ("set", "append"), "只该用 set / append"
        assert "before" in change and "after" in change
        assert change["path"][len(prefix):] in chron.ACTION_FIELDS, "变更了未知字段"
    status_change = _change_for(event, "status", "set")
    assert status_change is not None, "每次转换都应声明 status 变更"
    assert status_change["before"] == action["status"] and status_change["after"] == to
    # 4) 事件的 `events` 追加记录本身也是变更（§4.1 `events`）。
    assert _change_for(event, "events", "append")["after"] == event["event_id"]


def check_status_wordlist_and_table_shape():
    """八状态词表与 GDD §6.4 一致；转换表逐行可核对、终态无出边。"""
    assert chron.ACTION_STATUSES == ("proposed", "scheduled", "running", "blocked",
                                     "interrupted", "completed", "cancelled", "failed")
    assert chron.ACTION_TERMINAL_STATUSES == ("completed", "failed", "cancelled")
    assert set(chron.ACTION_TRIGGERS) == {"P", "S", "R", "T"}
    assert set(chron.ACTION_FIELDS) >= {
        "action_id", "actor", "kind", "status", "created_minute", "start_minute",
        "due_minute", "end_minute", "time_cost", "priority", "depends_on", "blocks",
        "interrupt_conditions", "idempotency_key", "events"}
    table = chron.ACTION_TRANSITIONS
    assert [(row["from"], row["to"], row["event"]) for row in table] == list(DESIGN_ROWS), \
        "全转换表与 §4.2 逐行不一致"
    for row in table:
        assert row["to"] in chron.ACTION_STATUSES
        assert row["from"] is None or row["from"] in chron.ACTION_STATUSES
        assert row["from"] not in chron.ACTION_TERMINAL_STATUSES, "终态不得有出边（§4.2 末行）"
        assert set(row["who"]) <= set(chron.ACTION_TRIGGERS), "「谁」列取值非法：%r" % (row["who"],)
        assert isinstance(row["m8_sync"], bool)
        assert isinstance(row["trigger"], str) and row["trigger"]
        assert callable(row["effect"])
    # 八状态都出现在表里（作为 from 或 to）。
    touched = set()
    for row in table:
        touched.update((row["from"], row["to"]))
    touched.discard(None)
    assert touched == set(chron.ACTION_STATUSES), "有状态没被转换表覆盖：%r" % (
        set(chron.ACTION_STATUSES) - touched,)


def check_every_table_row_has_a_case():
    """§4.2 表里**每一行**各一条用例：走一次该转换，核对事件与目标状态。"""
    for row in chron.ACTION_TRANSITIONS:
        action = _draft() if row["from"] is None else _to_state(row["from"])
        snapshot = {key: (list(value) if isinstance(value, list) else value)
                    for key, value in action.items()}
        ctx = {"world_minute": BASE_MINUTE, "who": row["who"][0]}
        event = chron.transition(action, row["to"], ctx)
        assert event["type"] == row["event"], "%r 的产出事件应是 %s" % (
            (row["from"], row["to"]), row["event"])
        _check_event_shape(event, action, BASE_MINUTE, row["to"])
        # 每次转换**恰好一条**事件（追加一条 id），且新状态 == 表里的 to。
        updated = chron.apply_event(action, event)
        assert updated["status"] == row["to"]
        assert len(updated["events"]) == len(action["events"]) + 1
        assert updated["events"][-1] == event["event_id"]
        # 不搞身份魔法：返回的是**新**行动卡，入参原封不动（纯函数）。
        assert action == snapshot, "transition / apply_event 不得改写传入的行动卡"
        assert updated is not action


def check_terminal_states_are_irreversible():
    """终态不可逆：completed / failed / cancelled 再转换一律被拒（§4.2 末行）。"""
    for terminal in chron.ACTION_TERMINAL_STATUSES:
        action = _to_state(terminal)
        snapshot = {key: (list(value) if isinstance(value, list) else value)
                    for key, value in action.items()}
        for target in chron.ACTION_STATUSES:
            for extra in ({}, {"who": "R"}, {"reason": "reopen"}):
                ctx = {"world_minute": BASE_MINUTE + 10}
                ctx.update(extra)
                try:
                    chron.transition(action, target, ctx)
                except chron.TransitionError:
                    continue
                raise AssertionError("终态 %s → %s 应被显式拒绝" % (terminal, target))
        assert action == snapshot, "被拒的转换不得留下任何改动"


def check_illegal_transitions_raise():
    """表里没有的转换一律显式抛 `TransitionError`——不静默忽略、不部分生效。"""
    for state in chron.ACTION_STATUSES:
        if state in chron.ACTION_TERMINAL_STATUSES:
            continue
        action = _to_state(state)
        legal = {row["to"] for row in chron.ACTION_TRANSITIONS if row["from"] == state}
        for target in chron.ACTION_STATUSES:
            if target in legal:
                continue
            try:
                chron.transition(action, target, {"world_minute": BASE_MINUTE})
            except chron.TransitionError as error:
                assert state in str(error) and target in str(error), \
                    "错误信息应写清 from → to：%s" % error
                continue
            raise AssertionError("%s → %s 不在转换表里，应抛 TransitionError" % (state, target))
    # 草稿（status = None）只允许 proposed。
    for target in chron.ACTION_STATUSES:
        if target == "proposed":
            continue
        try:
            chron.transition(_draft(), target, {"world_minute": 0})
        except chron.TransitionError:
            continue
        raise AssertionError("草稿 → %s 应被拒绝" % target)
    # 目标状态不在八状态里 → ValueError（入参错误，不是转换错误）。
    for target in ("", "unknown", None, 7):
        try:
            chron.transition(_to_state("proposed"), target, {"world_minute": 0})
        except (TypeError, ValueError):
            continue
        raise AssertionError("非法目标状态 %r 应被拒绝" % (target,))


def check_transition_ctx_validation():
    """转换上下文显式校验：缺 `world_minute` / 时间倒流 / 未到开始时间都抛错。"""
    proposed = _to_state("proposed")
    for ctx in (None, {}, {"world_minute": None}, {"world_minute": -1}):
        try:
            chron.transition(proposed, "scheduled", ctx)
        except (TypeError, ValueError):
            continue
        raise AssertionError("ctx=%r 应被拒绝（world_minute 必填且 ≥ 0）" % (ctx,))
    # `who` 给了但不在该行「谁」列 → 是转换错误（TransitionError），不是入参错误。
    try:
        chron.transition(proposed, "scheduled", {"world_minute": BASE_MINUTE, "who": "T"})
    except chron.TransitionError:
        pass
    else:
        raise AssertionError("触发者不在「谁」列时应抛 TransitionError")
    try:
        chron.transition(proposed, "scheduled", {"world_minute": BASE_MINUTE - 1})
    except chron.TransitionError:
        pass
    else:
        raise AssertionError("转换时间早于提议时间应被拒绝")
    # 非 dict 的 ctx。
    try:
        chron.transition(proposed, "scheduled", [("world_minute", BASE_MINUTE)])
    except TypeError:
        pass
    else:
        raise AssertionError("ctx 不是 dict 时应抛 TypeError")
    # 未到 start_minute 就想 running；安排在过去也是非法的。
    scheduled = _to_state("scheduled", start_minute=BASE_MINUTE + 100)
    try:
        chron.transition(scheduled, "running", {"world_minute": BASE_MINUTE})
    except chron.TransitionError:
        pass
    else:
        raise AssertionError("早于 start_minute 的 running 应被拒绝")
    try:
        chron.transition(_to_state("proposed"), "scheduled",
                         {"world_minute": BASE_MINUTE, "start_minute": BASE_MINUTE - 1})
    except chron.TransitionError:
        pass
    else:
        raise AssertionError("把 start_minute 安排在过去的转换应被拒绝")


def check_who_column_is_enforced():
    """给了 `ctx.who` 就必须落在该行「谁」列里（§4.2 的「谁」不是装饰）。"""
    proposed = _to_state("proposed")
    for who in ("R", "S"):
        chron.transition(proposed, "scheduled", {"world_minute": BASE_MINUTE, "who": who})
    for who in ("P", "T"):
        try:
            chron.transition(proposed, "scheduled", {"world_minute": BASE_MINUTE, "who": who})
        except chron.TransitionError:
            continue
        raise AssertionError("proposed → scheduled 的「谁」不含 %s，应被拒绝" % who)
    scheduled = _to_state("scheduled")
    try:
        chron.transition(scheduled, "running", {"world_minute": BASE_MINUTE, "who": "P"})
    except chron.TransitionError:
        pass
    else:
        raise AssertionError("scheduled → running 只许 S 触发")
    # 不给 who 则不校验（调用方自证）。
    chron.transition(scheduled, "running", {"world_minute": BASE_MINUTE})


def check_resume_uses_remaining_time():
    """`interrupted → scheduled` 用**剩余耗时**重排（§4.4）；不保留进度则整份重来。"""
    start = BASE_MINUTE
    action = _to_state("scheduled", start_minute=start)
    action = _apply(action, "running", {"world_minute": start + 60, "who": "S"})
    action = _apply(action, "interrupted", {"world_minute": start + 110, "who": "S"})
    assert action["interrupt_minute"] == start + 110
    assert action["progress_kept"] is True
    resumed = _apply(action, "scheduled", {"world_minute": start + 200, "who": "S"})
    consumed = (start + 110) - start  # 已消耗的分钟数
    assert resumed["start_minute"] == start + 200
    assert resumed["due_minute"] == start + 200 + (action["time_cost"] - consumed), \
        "恢复重排必须用剩余耗时（已消耗 %d 分钟）" % consumed
    assert resumed["interrupt_minute"] is None, "重排后停点应清空"

    # 进度不保留（progress_kept=False）→ 从头开始，用整份 time_cost。
    fresh = _to_state("scheduled", start_minute=BASE_MINUTE)
    fresh = _apply(fresh, "running", {"world_minute": BASE_MINUTE + 60, "who": "S"})
    fresh = _apply(fresh, "interrupted",
                   {"world_minute": BASE_MINUTE + 110, "who": "S", "progress_kept": False})
    assert fresh["progress_kept"] is False
    fresh = _apply(fresh, "scheduled", {"world_minute": BASE_MINUTE + 200, "who": "S"})
    assert fresh["due_minute"] == BASE_MINUTE + 200 + fresh["time_cost"]

    # 开始前中断（未消耗）→ 也是整份 time_cost。
    pending = _to_state("scheduled", start_minute=BASE_MINUTE + 500)
    pending = _apply(pending, "interrupted", {"world_minute": BASE_MINUTE + 10, "who": "S"})
    assert pending["interrupt_minute"] is None, "未开始过就没有停点"
    pending = _apply(pending, "scheduled", {"world_minute": BASE_MINUTE + 20, "who": "S"})
    assert pending["due_minute"] == BASE_MINUTE + 20 + pending["time_cost"]


def check_blocked_scheduled_roundtrip():
    """`blocked ⇄ scheduled` 往返：阻塞未消耗时间，恢复后按整份耗时重排。"""
    action = _to_state("scheduled", start_minute=BASE_MINUTE)
    action = _apply(action, "blocked", {"world_minute": BASE_MINUTE + 5, "who": "S"})
    assert action["status"] == "blocked"
    action = _apply(action, "scheduled", {"world_minute": BASE_MINUTE + 9, "who": "S"})
    assert action["status"] == "scheduled"
    assert action["start_minute"] == BASE_MINUTE + 9
    assert action["due_minute"] == BASE_MINUTE + 9 + action["time_cost"]
    # 再阻塞一次、再恢复（往返第二圈）。
    action = _apply(action, "blocked", {"world_minute": BASE_MINUTE + 12, "who": "S"})
    assert action["status"] == "blocked"
    action = _apply(action, "scheduled", {"world_minute": BASE_MINUTE + 20, "who": "S"})
    assert action["status"] == "scheduled"
    assert action["start_minute"] == BASE_MINUTE + 20
    assert action["due_minute"] == BASE_MINUTE + 20 + action["time_cost"]
    assert action["interrupt_minute"] is None, "阻塞恢复不涉及中断停点"
    action = _apply(action, "running", {"world_minute": BASE_MINUTE + 20, "who": "S"})
    assert action["status"] == "running"


def check_m8_sync_path_without_scheduled():
    """M8 同步路径：`proposed → running → completed/failed`，**不经过** scheduled。"""
    for final in ("completed", "failed"):
        action = _draft()
        assert action["status"] is None
        seen = []
        action = _apply(action, "proposed", {"world_minute": BASE_MINUTE, "who": "P"})
        seen.append(action["status"])
        action = _apply(action, "running", {"world_minute": BASE_MINUTE, "who": "R"})
        seen.append(action["status"])
        assert action["start_minute"] == BASE_MINUTE, "同步路径应「立即」开始"
        assert action["due_minute"] == BASE_MINUTE + action["time_cost"]
        action = _apply(action, final, {"world_minute": BASE_MINUTE, "who": "R"})
        seen.append(action["status"])
        assert seen == ["proposed", "running", final], "M8 路径不得经过 scheduled：%r" % (seen,)
        assert "scheduled" not in seen
        assert action["end_minute"] == BASE_MINUTE
        # 同步路径的终点仍是终态 —— 不可逆。
        try:
            chron.transition(action, "running", {"world_minute": BASE_MINUTE + 1})
        except chron.TransitionError:
            pass
        else:
            raise AssertionError("M8 终态后不得再转换")


def check_event_fields_follow_ctx_and_are_deterministic():
    """事件的 `cause` / `reason` / `source` 随 ctx；同一输入两次产**全等**事件。"""
    action = _to_state("scheduled")
    ctx = {"world_minute": BASE_MINUTE, "cause": "c1:e000001", "reason": "interrupt_condition"}
    event = chron.transition(action, "running", ctx)
    assert event["cause"] == "c1:e000001", "cause 应透传上游事件 id"
    assert event["reason"] == "interrupt_condition"
    assert event["source"] == "system", "无 who / source 时默认 system"
    again = chron.transition(action, "running", ctx)
    assert again == event, "同一输入应产出全等事件（确定性）"
    assert again is not event
    assert event["idempotency_key"] == "%s:3:action.started" % action["action_id"], \
        "幂等键应确定性可重建（§8.3）"
    assert event["event_id"] == "%s:e000003" % action["action_id"]
    # 玩家发起的根因 cause=None；source 由 who 推。
    proposed = _to_state("proposed")
    player_event = chron.transition(proposed, "cancelled", {"world_minute": BASE_MINUTE, "who": "P"})
    assert player_event["cause"] is None
    assert player_event["source"] == "player"
    # ctx.source 优先（枚举由状态层判，本层只要求非空字符串）。
    system_event = chron.transition(proposed, "cancelled",
                                    {"world_minute": BASE_MINUTE, "source": "ai_proposal"})
    assert system_event["source"] == "ai_proposal"
    # 世界时间是权威轴上的转换时刻（§3.2）。
    later = chron.transition(_to_state("interrupted"), "cancelled",
                             {"world_minute": BASE_MINUTE + 1440, "who": "P"})
    assert later["world_time"] == {"world_minute": BASE_MINUTE + 1440,
                                  "day": chron.to_calendar(BASE_MINUTE + 1440)["day"],
                                  "tod": chron.to_calendar(BASE_MINUTE + 1440)["tod"]}


def check_apply_event_rejects_foreign_changes():
    """`apply_event` 只吃自己的变更形状：别的块 / 行动 / 字段 / op / 过期事件都显式抛错。"""
    action = _to_state("proposed")
    event = chron.transition(action, "scheduled", {"world_minute": BASE_MINUTE})
    good = chron.apply_event(action, event)
    assert good["status"] == "scheduled" and action["status"] == "proposed", "不得改写原卡"
    bad_events = (
        dict(event, target="other"),                                   # 别的行动
        dict(event, changes=[{"block": "rules", "path": "/actions/a1/status",
                              "op": "set", "before": None, "after": "scheduled"}]),
        dict(event, changes=[{"block": "chron", "path": "/actions/a1/nope",
                              "op": "set", "before": None, "after": 1}]),
        dict(event, changes=[{"block": "chron", "path": "/actions/a1/status",
                              "op": "bogus", "before": None, "after": "scheduled"}]),
        dict(event, changes=[{"block": "chron", "path": "/actions/a1/status",
                              "op": "set", "before": "running", "after": "scheduled"}]),
        dict(event, changes=[{"block": "chron", "path": "/actions/A1/status",
                              "op": "set", "before": None, "after": "scheduled"}]),
        dict(event, changes=[]),
        dict(event, changes="nope"),
    )
    for bad in bad_events:
        try:
            chron.apply_event(action, bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError("非法事件应被拒绝：%r" % (bad,))
    # 重复应用同一事件 → before 不一致 → 显式拒绝（防重放）。
    try:
        chron.apply_event(good, event)
    except ValueError:
        pass
    else:
        raise AssertionError("重复应用同一事件应被拒绝")


def check_new_action_validation():
    """行动卡构造与形状校验：非法入参 / 缺字段 / 非法状态都显式拒绝。"""
    assert _draft()["status"] is None
    draft = chron.new_action("a9", "npc-2", "scout", 10, depends_on=("a1",), priority=-1)
    assert draft["depends_on"] == ["a1"] and draft["blocks"] == [] and draft["events"] == []
    for args in (("", "pc", "k", 1), ("a", "", "k", 1), ("a", "pc", "", 1), ("a", "pc", "k", -1)):
        try:
            chron.new_action(*args)
        except (TypeError, ValueError):
            continue
        raise AssertionError("new_action%r 应被拒绝" % (args,))
    for kwargs in ({"time_cost": 1.5}, {"time_cost": True}, {"priority": 1.0},
                   {"depends_on": "a1"}, {"depends_on": (1,)}, {"interrupt_conditions": 7},
                   {"idempotency_key": ""}):
        base = {"action_id": "a", "actor": "pc", "kind": "k", "time_cost": 5}
        base.update(kwargs)
        try:
            chron.new_action(**base)
        except (TypeError, ValueError):
            continue
        raise AssertionError("new_action(%r) 应被拒绝" % (kwargs,))
    # 行动卡形状不对 / 状态非法 → 显式拒绝（不静默补默认）。
    for bad in (None, "action", {}, {"action_id": "a", "status": "running"},
                dict(draft, status="flying")):
        try:
            chron.transition(bad, "proposed", {"world_minute": 0})
        except (TypeError, ValueError):
            continue
        raise AssertionError("非法行动卡 %r 应被拒绝" % (bad,))


# --------------------------------------------------------------------------
# 纯函数（无依赖、无 IO）
# --------------------------------------------------------------------------

def check_module_is_pure():
    """`chron.py` 不 import 任何模块、不调用 `open`（静态审查）。"""
    with open(CHRON_PY, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)
    imports = [node for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not imports, "chron.py 不应 import 任何模块，发现 %d 处" % len(imports)
    assert not re.search(r"\bopen\s*\(", source), "chron.py 不应调用 open（无 IO）"


def main():
    checks = (
        check_minute_constants,
        check_watch_spans_unequal,
        check_watch_boundaries,
        check_watch_span_measurement,
        check_calendar_roundtrip,
        check_calendar_formula,
        check_from_calendar_rejects_bad_input,
        check_advance_clock_monotonic,
        check_clock_input_validation,
        check_data_consistency,
        # §4 行动生命周期（C2）
        check_status_wordlist_and_table_shape,
        check_every_table_row_has_a_case,
        check_terminal_states_are_irreversible,
        check_illegal_transitions_raise,
        check_transition_ctx_validation,
        check_who_column_is_enforced,
        check_resume_uses_remaining_time,
        check_blocked_scheduled_roundtrip,
        check_m8_sync_path_without_scheduled,
        check_event_fields_follow_ctx_and_are_deterministic,
        check_apply_event_rejects_foreign_changes,
        check_new_action_validation,
        check_module_is_pure,
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
