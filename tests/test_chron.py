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

覆盖切片 C3 的验收标准（§5 调度器）：

- 同 `due_minute` 多卡**两次运行顺序全等**（§5.2 稳定排序键，插入顺序故意打乱）；
- 依赖**未完成不放行**（不进 `due_heap`，到点不触发）、成终态后放行并**立即**办；
- 有环提案被拒（`proposed → failed`，`reason = dependency_cycle`），不污染状态；
- 依赖失败**默认级联取消**（事件带 `reason`，传递到下游）；`keep` 策略改判 `blocked`；
- 同资源 / 同 place 的两行动触发**一个** `window.opened`（只识别、不裁决）；无键不开窗；
- 调度器纯函数：入参不被改写、同一输入两次结果全等、`due_heap` 可重建且幂等。

覆盖切片 C4 的验收标准（§6 时间压缩与事件屏障）：

- §6.5 的**五条**：穿屏障停在前、无屏障可压缩、不删因果、到期总停（B8）、同分钟屏障优先；
- §6.2 的 **B1–B8 判据逐条**可机读（`BARRIERS` 表 + `next_barrier` / `barriers_at` 各一条用例）；
- 压缩**不产生**「被跳过的模拟」事件：压缩日志（`compression_log`）只引用真实事件；
- `offline_advance` **只作入参**（默认 `False`，§3.4）——不判「谁在线」，也不改变推进结果。

覆盖切片 C5 的验收标准（§8 持久化与恢复）：

- `chron` 存档块（§8.1）尺寸 / 默认值工厂 / 惰性迁移（§8.4）：缺块补默认、脏值只降级它自己、
  更高版本**拒载**、只补不覆盖；`due_heap` / `blocks` / `running` 三个派生索引由 `actions` 重建；
- §8.2 重启时序：换句柄重开 → `restore` 之后 `world_minute`、到期顺序（`due_heap` 与下一次
  推进的事件流）、行动卡、窗口、幂等表与重启前**全等**；
- §8.3 幂等：同 `idempotency_key` 重投**副作用只发生一次**（CHRON 侧 `skipped` 闸 + 状态层侧
  `replayed` 闸；事件日志不长一行）；
- §8.4 裁剪：幂等表保留最新 `keep` 条、终态行动折叠为事件引用（还有人依赖的不折）；
- 存储接口只用状态层的公开面（`Handle.view` / `Handle.replay` / `Handle.commit`）；
  `chron.py` 仍**零 import、无 IO**（纯函数断言照旧）。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_chron.py` 运行。
"""

import ast
import json
import os
import re
import shutil
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import chron  # noqa: E402  (ROOT 已加入 sys.path)
import state  # noqa: E402  (C5：块的落盘 / 重放走状态层的公开面)

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
# §5 调度器（C3）
# --------------------------------------------------------------------------

# 调度器用例的基准世界分钟；与 C2 的 BASE_MINUTE 同轴，但**独立一份**避免互相牵动。
SCHED_MINUTE = 1000

# 每个用例都要真实跑一遍的最小/典型 ctx。
SCHED_CTX = {"world_minute": SCHED_MINUTE}


def _draft_action(action_id, *, actor=None, kind="travel", time_cost=60, priority=0,
                  depends_on=(), on_dep_fail=chron.DEFAULT_ON_DEP_FAIL,
                  place=None, resource=None):
    """一张**未提议**的调度草稿（§4.1）：调度器会自己走 `proposed → scheduled`。"""
    return chron.new_action(action_id, actor or ("pc-%s" % action_id), kind, time_cost,
                            priority=priority, depends_on=depends_on,
                            on_dep_fail=on_dep_fail, place=place, resource=resource)


def _schedule(state, draft, *, start_minute=None, minute=SCHED_MINUTE,
              expect="scheduled", **keys):
    """排一张卡并断言结果状态；返回排期后的**新**调度器状态。"""
    result = chron.schedule(state, draft, world_minute=minute, start_minute=start_minute,
                            **keys)
    assert result["status"] == expect, "schedule(%s) 期望 %s，得到 %r（事件 %r）" % (
        draft["action_id"], expect, result["status"], result["events"])
    return result["state"]


def _window_opens(events):
    """事件流里的 `window.opened`（按出现顺序）。"""
    return [event for event in events if event["type"] == chron.WINDOW_OPEN_EVENT]


def _check_window_shape(window):
    """`Window` 逐字段（§5.4 的四字段 + 实现补充）与类型。"""
    missing = [field for field in chron.WINDOW_FIELDS if field not in window]
    assert not missing, "Window 缺少字段：%r" % missing
    assert isinstance(window["window_id"], str) and window["window_id"]
    assert isinstance(window["key"], str) and window["key"]
    assert window["reason"] in ("place", "resource")
    assert window["key"].startswith(window["reason"] + ":")
    assert window["participants"] == sorted(window["participants"]), "参与者应升序（确定性）"
    assert len(window["participants"]) >= 2, "窗口至少要两个参与者"
    assert isinstance(window["actors"], list) and len(window["actors"]) == len(
        window["participants"])
    assert window["place"] is None or isinstance(window["place"], str)
    assert window["resource"] is None or isinstance(window["resource"], str)
    assert window["open_minute"] >= 0
    assert window["close_minute"] is None, "未结算的窗口仍开着（关窗归 C4）"


def check_scheduler_state_shape():
    """§5.1 状态形状：`new_scheduler` 的键集合 / 默认值，非法状态显式拒绝。"""
    state = chron.new_scheduler()
    assert state == {"world_minute": 0, "actions": {}, "due_heap": [], "windows": [],
                     "blocks": {}, "running": {}, "seq": 0, "keys": {}}
    assert set(chron.SCHEDULER_FIELDS) == set(state)
    assert chron.new_scheduler(1440)["world_minute"] == 1440
    for bad in (-1, 1.5, True, "0"):
        try:
            chron.new_scheduler(bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError("new_scheduler(%r) 应被拒绝" % (bad,))
    # 形状不对 / 缺字段 → 显式拒绝；`due_heap` / `deps` 是派生索引，缺了就重建的入口。
    for bad in (None, "state", {}, {"world_minute": 0}):
        try:
            chron.rebuild_heap(bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError("非法调度器状态 %r 应被拒绝" % (bad,))
    for bad in (None, "state", [], 7):
        try:
            chron.open_windows(bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError("open_windows(%r) 应被拒绝" % (bad,))


def check_due_order_is_stable():
    """验收 1：同 `due_minute` 多卡**两次运行顺序全等**（§5.2 稳定排序）。

    插入顺序**故意打乱**：若键里少一段（只剩 `due_minute`），结果就会随插入 / 哈希漂移。
    """
    def build(order):
        state = chron.new_scheduler(SCHED_MINUTE)
        drafts = {
            "a": _draft_action("a", time_cost=60, priority=1),
            "b": _draft_action("b", time_cost=60, priority=1),
            "c": _draft_action("c", time_cost=60, priority=5),
            "d": _draft_action("d", time_cost=60, priority=-1),
        }
        for key in order:
            state = _schedule(state, drafts[key])
        return state

    forward = build(("a", "b", "c", "d"))
    backward = build(("d", "c", "b", "a"))
    shuffled = build(("c", "a", "d", "b"))
    assert forward["due_heap"] == backward["due_heap"] == shuffled["due_heap"], \
        "同 due_minute 的堆序必须与插入顺序无关：%r / %r / %r" % (
            forward["due_heap"], backward["due_heap"], shuffled["due_heap"])
    # 排序键四段逐段核对：priority 降序 → created_seq 升序 → action_id 升序。
    assert [entry[3] for entry in forward["due_heap"]] == ["c", "a", "b", "d"]
    # `-priority` 在**负数 priority** 下是正数——所以只断言「堆是升序列表（-priority 越大越先）」。
    assert sorted(forward["due_heap"]) == forward["due_heap"], "堆必须是升序列表"
    assert [entry[1] for entry in forward["due_heap"]] == [-5, -1, -1, 1], \
        "priority 段应为 -priority：%r" % ([entry[1] for entry in forward["due_heap"]],)
    # 两次 `advance_to` 的事件流**全等**（顺序全等的可观测形式）。
    first = chron.advance_to(forward, SCHED_MINUTE + 60)["events"]
    second = chron.advance_to(backward, SCHED_MINUTE + 60)["events"]
    assert first == second, "两次运行的到期事件流应全等"
    assert [event["target"] for event in first
            if event["type"] == "action.due"] == ["c", "a", "b", "d"]


def check_sort_key_segments():
    """§5.2 排序键逐段可核对：`(due_minute, -priority, created_seq, action_id)`。"""
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("early", actor="pc-1", time_cost=10))
    state = _schedule(state, _draft_action("late", actor="pc-2", time_cost=20))
    early = state["actions"]["early"]
    late = state["actions"]["late"]
    assert chron.sort_key(early) == (SCHED_MINUTE + 10, 0, 1, "early")
    assert chron.sort_key(late) == (SCHED_MINUTE + 20, 0, 1, "late")
    # priority 降序（取负）与 action_id 末位 tie-break。
    high = _schedule(state, _draft_action("zz", actor="pc-3", time_cost=10, priority=7))
    assert chron.sort_key(high["actions"]["zz"]) == (SCHED_MINUTE + 10, -7, 1, "zz")
    assert chron.sort_key(high["actions"]["zz"]) < chron.sort_key(high["actions"]["early"])
    # created_seq 升序：后提议的卡排后（同一 `due_minute`、同 priority）。
    later = _schedule(high, _draft_action("aa", actor="pc-4", time_cost=10))
    assert later["actions"]["aa"]["events"][0] == "aa:e000001"
    assert sorted(later["due_heap"]) == later["due_heap"], "堆必须是升序列表（确定性）"
    assert chron.ACTION_DUE_EVENT == "action.due"


def check_dependency_gates_release():
    """验收 2（前半）：依赖**未完成不放行**——不进 `due_heap`，到点也不 triggered。"""
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("dep", actor="pc-1", time_cost=30))
    state = _schedule(state, _draft_action("child", actor="pc-2", time_cost=30,
                                           depends_on=("dep",)))
    assert state["actions"]["child"]["status"] == "scheduled", "依赖未成也要先排期（§5.3）"
    assert [entry[3] for entry in state["due_heap"]] == ["dep"], \
        "依赖未完成的卡不得进入 due_heap"
    # 反向索引 `blocks`（§4.1）由调度器维护，行动卡上也同步。
    assert state["actions"]["dep"]["blocks"] == ["child"]
    assert chron.dependents_of(state, "dep") == ["child"]
    assert chron.blocks_of(state, "dep") == chron.dependents_of(state, "dep")
    assert chron.blocked_by(state["actions"]["child"]) == ["dep"]
    # 世界时间越过 child 的 due_minute，但依赖没结算：child **不得**触发。
    passed = chron.advance_to(state, SCHED_MINUTE + 60)
    assert [event["target"] for event in passed["events"]
            if event["type"] == "action.due"] == ["dep"], \
        "依赖未完成时 child 不得触发：%r" % (passed["events"],)
    assert [entry[3] for entry in passed["state"]["due_heap"]] == ["dep"]
    assert passed["state"]["actions"]["child"]["status"] == "scheduled"
    assert chron.deps_satisfied(passed["state"], passed["state"]["actions"]["child"]) is False
    # 依赖结算（completed）→ 放行：进入堆，并在下一次推进触发。
    settled = chron.settle(passed["state"], "dep", "completed",
                           world_minute=SCHED_MINUTE + 60)
    released = settled["state"]
    assert [entry[3] for entry in released["due_heap"]] == ["child"], \
        "依赖成终态后下游应放行：%r" % (released["due_heap"],)
    assert chron.deps_satisfied(released, released["actions"]["child"]) is True
    assert settled["events"]["dep"][0]["type"] == "action.completed"
    # 放行时 child 的 due 已经过去：仍要**立即**办（不静默丢掉）。
    fired = chron.advance_to(released, SCHED_MINUTE + 90)
    assert [(event["type"], event["target"]) for event in fired["events"]] == \
        [("action.started", "child"), ("action.due", "child")], fired["events"]
    # 依赖成终态后放行的卡**不是**「事件顺序漂移」：两次重建结果全等。
    assert chron.rebuild_heap(released) == chron.rebuild_heap(dict(released))


def check_dependency_cycle_rejected():
    """验收 2（后半）：有环提案被拒（`proposed → failed`），且不污染状态。"""
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("b", actor="pc-1", time_cost=30))
    # 造环：让既有的 b 依赖（未来的）c —— 只有脏图 / 半成品恢复能造出这种边。
    dirty = dict(state, actions=dict(state["actions"]))
    dirty["actions"]["b"] = dict(dirty["actions"]["b"], depends_on=["c"])
    before = {key: (list(value) if isinstance(value, list) else dict(value)
                    if isinstance(value, dict) else value) for key, value in dirty.items()}
    result = chron.schedule(dirty, _draft_action("c", depends_on=("b",)),
                            world_minute=SCHED_MINUTE)
    assert result["status"] == "rejected", "有环提案应被拒：%r" % (result,)
    assert result["cycle"] == ["c", "b", "c"], "应指出环上的 id：%r" % (result["cycle"],)
    types = [event["type"] for event in result["events"]]
    assert types == ["action.proposed", "action.failed"], "拒单也留下可追溯事件：%r" % types
    failed = result["events"][1]
    assert failed["reason"] == "dependency_cycle"
    assert failed["cause"] == result["events"][0]["event_id"], "failed 应指回 proposed"
    status_change = _change_for(failed, "status", "set")
    assert status_change["before"] == "proposed" and status_change["after"] == "failed"
    # 被拒的卡**不进** actions；入参状态原封不动（无部分生效）。
    assert "c" not in result["state"]["actions"], "被拒的卡不得留在调度器里"
    assert result["state"] is dirty, "拒单不得替换状态"
    assert dirty == before, "拒单不得改写入参状态"
    assert len(dirty["actions"]["b"]["events"]) == 2, "b 的事件数不该变"


def check_dependency_failure_cascades():
    """验收 3：依赖失败**默认级联取消**（事件带 `reason`）；`keep` 策略改判 `blocked`。"""
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("dep", actor="pc-1", time_cost=30))
    state = _schedule(state, _draft_action("mid", actor="pc-2", time_cost=30,
                                           depends_on=("dep",)))
    state = _schedule(state, _draft_action("leaf", actor="pc-3", time_cost=30,
                                           depends_on=("mid",)))
    state = _schedule(state, _draft_action("keep", actor="pc-4", time_cost=30,
                                           depends_on=("dep",), on_dep_fail="keep"))
    advanced = chron.advance_to(state, SCHED_MINUTE + 30)
    settled = chron.settle(advanced["state"], "dep", "failed",
                           world_minute=SCHED_MINUTE + 31, reason="rule_failed")
    events = settled["events"]
    assert events["dep"][0]["type"] == "action.failed" and \
        events["dep"][0]["reason"] == "rule_failed", "结算原因应透传"
    for action_id in ("mid", "leaf"):
        assert events[action_id][0]["type"] == "action.cancelled", \
            "%s 应被级联取消：%r" % (action_id, events[action_id])
        assert events[action_id][0]["reason"] == "dependency_failed", \
            "级联事件必须带 reason"
    assert events["keep"][0]["type"] == "action.blocked", "keep 策略应改判 blocked"
    assert events["keep"][0]["reason"] == "dependency_failed"
    statuses = {key: settled["state"]["actions"][key]["status"]
                for key in ("dep", "mid", "leaf", "keep")}
    assert statuses == {"dep": "failed", "mid": "cancelled", "leaf": "cancelled",
                        "keep": "blocked"}, statuses
    assert settled["state"]["due_heap"] == [], "级联后不得留下可办条目"
    # 级联是**传递**的：mid 取消后 leaf 也取消（不是因为 dep 直接挡它）。
    assert settled["state"]["actions"]["leaf"]["action_id"] == "leaf"
    # 依赖被取消同样级联（`cancelled` 也是「依赖没成」）。
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("dep2", actor="pc-1", time_cost=30))
    state = _schedule(state, _draft_action("wait2", actor="pc-2", time_cost=30,
                                           depends_on=("dep2",)))
    state = chron.advance_to(state, SCHED_MINUTE + 30)["state"]
    cancelled = chron.settle(state, "dep2", "cancelled", world_minute=SCHED_MINUTE + 31)
    assert cancelled["events"]["wait2"][0]["type"] == "action.cancelled"
    assert cancelled["events"]["wait2"][0]["reason"] == "dependency_failed"


def check_resource_conflict_opens_one_window():
    """验收 4：同资源两行动触发**一个** `window.opened`（§5.4 / §5.5 只识别、不裁决）。"""
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("loot-a", actor="pc-1", time_cost=30,
                                           resource="silver-idol"))
    state = _schedule(state, _draft_action("loot-b", actor="pc-2", time_cost=30,
                                           resource="silver-idol"))
    fired = chron.advance_to(state, SCHED_MINUTE + 30)
    opened = _window_opens(fired["events"])
    assert len(opened) == 1, "同资源两行动应恰好开一个窗：%r" % (opened,)
    assert opened[0]["reason"] == "resource"
    assert opened[0]["type"] == chron.WINDOW_OPEN_EVENT
    # 事件的窗口快照 = 两个参与者。
    snapshot = opened[0]["changes"][0]["after"]
    _check_window_shape(snapshot)
    assert snapshot["participants"] == ["loot-a", "loot-b"]
    assert snapshot["actors"] == ["pc-1", "pc-2"]
    assert snapshot["resource"] == "silver-idol" and snapshot["place"] is None
    # 开窗分钟 = 触发识别的**第二个**行动开始的那一分钟（此刻冲突才成立）。
    assert snapshot["open_minute"] == SCHED_MINUTE + 30 == fired["world_minute"]
    assert opened[0]["idempotency_key"] == "window.opened:%s" % snapshot["window_id"]
    # CHRON 只产出「该开窗口」的判断：事件里**没有**任何「谁赢」的字段。
    assert "winner" not in opened[0] and "outcome" not in opened[0]
    # 状态里的窗口与事件快照一致（同一份结构）。
    windows = chron.open_windows(fired["state"])
    assert len(windows) == 1 and windows[0] == snapshot
    assert chron.windows_at(fired["state"], "loot-a") == [snapshot["window_id"]]
    assert chron.windows_at(fired["state"], "loot-b") == [snapshot["window_id"]]


def check_window_identification_rules():
    """§5.5 三条识别依据：同 place / 同 resource / 无键不开窗；重复冲突不重复开窗。"""
    # 1) 同一 place（无 resource）→ 开窗，reason = place。
    state = chron.new_scheduler(SCHED_MINUTE)
    for index, action_id in enumerate(("p1", "p2", "p3")):
        state = _schedule(state, _draft_action(action_id, actor="pc-%d" % (index + 1),
                                               time_cost=30, place="broken-bridge"))
    fired = chron.advance_to(state, SCHED_MINUTE + 30)
    opened = _window_opens(fired["events"])
    assert len(opened) == 1, "同 place 的三个行动只开一个窗：%r" % (opened,)
    window = chron.open_windows(fired["state"])[0]
    _check_window_shape(window)
    assert window["reason"] == "place" and window["place"] == "broken-bridge"
    assert window["participants"] == ["p1", "p2", "p3"], \
        "同键的后来者应并入已开的窗口：%r" % (window["participants"],)
    # 2) 无 place / 无 resource → 不参与识别，不开窗。
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("n1", time_cost=30))
    state = _schedule(state, _draft_action("n2", time_cost=30))
    fired = chron.advance_to(state, SCHED_MINUTE + 30)
    assert _window_opens(fired["events"]) == []
    assert chron.open_windows(fired["state"]) == []
    assert chron.conflict_key(fired["state"]["actions"]["n1"]) is None
    # 3) 不同冲突键 → 各自开窗（互不影响）。
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("x1", time_cost=30, resource="rope"))
    state = _schedule(state, _draft_action("x2", time_cost=30, resource="rope"))
    state = _schedule(state, _draft_action("y1", time_cost=30, place="cellar"))
    state = _schedule(state, _draft_action("y2", time_cost=30, place="cellar"))
    # resource 优先于 place：x1 同时带 place 时按 resource 识别。
    state = _schedule(state, _draft_action("z1", time_cost=30, place="cellar",
                                           resource="rope"))
    fired = chron.advance_to(state, SCHED_MINUTE + 30)
    opened = _window_opens(fired["events"])
    assert len(opened) == 2, "两个键各开一个窗：%r" % (opened,)
    windows = {window["key"]: window for window in chron.open_windows(fired["state"])}
    assert set(windows) == {"resource:rope", "place:cellar"}, windows
    assert windows["resource:rope"]["participants"] == ["x1", "x2", "z1"]
    assert windows["place:cellar"]["participants"] == ["y1", "y2"]
    # 4) 窗口快照里的 actor 与 participants 一一对应。
    for window in windows.values():
        _check_window_shape(window)
        assert window["actors"] == sorted(fired["state"]["actions"][action_id]["actor"]
                                          for action_id in window["participants"])


def meter_events(state):
    """`advance_to` 到「堆上最远到期」的**一次**推进所得事件流（测试便利函数）。

    C4 起一次调用最多推进到**第一个**停点：堆上同分钟多卡时即该分钟的全部到期；
    堆上还有更晚的分钟时，需要按停点逐次推进（先结算上一个到期点，B8 才会放行）。
    """
    farthest = max(entry[0] for entry in state["due_heap"])
    return chron.advance_to(state, farthest)["events"]


def check_advance_to_fires_in_order():
    """`advance_to` 的推进语义：到期即触发、同分钟按堆序、窗口先于到期。

    C4 起还要**停在停点**（§6.3 / §6.5 第 4 条）：一次调用最多推进到**第一个**屏障或
    结算点；越过结算点要**逐停点**推进（先结算上一个到期，B8 才放行）——下例即该调用模式。
    """
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("first", time_cost=10, priority=0,
                                           resource="rope"))
    state = _schedule(state, _draft_action("second", time_cost=20, priority=0,
                                           resource="rope"))
    state = _schedule(state, _draft_action("peer", actor="pc-3", time_cost=20,
                                           resource="rope"))
    # 目标早于最早到期 → 不触发，但世界时间前进（没有屏障 = 可以压缩）。
    early = chron.advance_to(state, SCHED_MINUTE + 5)
    assert early["events"] == [] and early["world_minute"] == SCHED_MINUTE + 5
    assert early["stopped_at"] is None and early["barriers"] == []
    assert early["state"]["world_minute"] == SCHED_MINUTE + 5
    # 目标跨过第一个到期（+10）→ 停在**结算点**：该分钟的到期已触发（B8），不越过。
    middle = chron.advance_to(state, SCHED_MINUTE + 15)
    assert [(event["type"], event["target"]) for event in middle["events"]] == \
        [("action.started", "first"), ("action.due", "first")]
    assert middle["world_minute"] == SCHED_MINUTE + 10
    assert middle["stopped_at"] == SCHED_MINUTE + 10
    assert [record["id"] for record in middle["barriers"]] == ["B8"]
    assert middle["state"]["windows"] == [], "只有一个行动时不开窗（§5.4）"
    # 结算未完成前压缩不再前进（§6.5 第 4 条：到期总停）——只重播同一结算点（幂等键相同）。
    stuck = chron.advance_to(middle["state"], SCHED_MINUTE + 15)
    assert stuck["world_minute"] == SCHED_MINUTE + 10
    assert stuck["stopped_at"] == SCHED_MINUTE + 10
    assert [record["id"] for record in stuck["barriers"]] == ["B8"]
    assert [(event["type"], event["target"]) for event in stuck["events"]] == \
        [("action.due", "first")]
    assert stuck["events"][0]["idempotency_key"] == \
        middle["events"][-1]["idempotency_key"], "重播必须同键（§8.3 幂等）"
    # 结算 first 后继续推进：+20 那一分钟的到期触发，窗口**恰好一个**（同资源）。
    settled = chron.settle(middle["state"], "first", "completed",
                           world_minute=SCHED_MINUTE + 10)["state"]
    full = chron.advance_to(settled, SCHED_MINUTE + 100)
    assert full["world_minute"] == SCHED_MINUTE + 20, "越过下一个到期即停（B8）"
    assert full["stopped_at"] == SCHED_MINUTE + 20
    assert [event["target"] for event in _window_opens(full["events"])] == [None]
    assert len(_window_opens(full["events"])) == 1
    assert chron.open_windows(full["state"])[0]["participants"] == ["peer", "second"]
    # 世界时间只增不减：目标早于当前 → 《ScheduleError》；类型 / 负值 → TypeError / ValueError。
    try:
        chron.advance_to(full["state"], SCHED_MINUTE + 10)
    except chron.ScheduleError:
        pass
    else:
        raise AssertionError("回拨世界时间应抛 ScheduleError")
    for bad in (-1, 1.5, True, "x"):
        try:
            chron.advance_to(full["state"], bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError("advance_to(%r) 应被拒绝" % (bad,))
    # 到期事件带确定性幂等键（§8.3）：`<action_id>:<due_minute>:<type>`。
    due = [event for event in full["events"] if event["type"] == chron.ACTION_DUE_EVENT]
    assert due, "应有到期事件"
    for event in due:
        assert event["idempotency_key"] == "%s:%d:%s" % (
            event["target"], settled["actions"][event["target"]]["due_minute"],
            chron.ACTION_DUE_EVENT)


def check_scheduler_events_shape():
    """调度器产出的事件形状：GDD §19.5 最小字段 + 同一行动的事件序号连续。"""
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("a1", time_cost=10, resource="rope"))
    state = _schedule(state, _draft_action("a2", time_cost=10, resource="rope"))
    events = meter_events(state)
    assert events, "应有事件"
    for event in events:
        for field in ("event_id", "world_time", "type", "actor", "target", "cause",
                      "changes", "source", "reason", "idempotency_key"):
            assert field in event, "事件缺少字段 %s：%r" % (field, event)
        assert isinstance(event["event_id"], str) and event["event_id"]
        assert EVENT_TYPE_RE.match(event["type"]), "type 不是 <domain>.<verb>：%r" % event["type"]
        assert set(event["world_time"]) == {"world_minute", "day", "tod"}
        assert event["world_time"]["world_minute"] >= SCHED_MINUTE
        assert isinstance(event["changes"], list), event
        if event["type"] != "action.due":
            # 到期事件只作「结算点到了」的宣告，没有字段更新；其余事件必须带变更。
            assert event["changes"], event
        for change in event["changes"]:
            assert change["block"] == "chron", change
            assert change["op"] in ("set", "append"), change
            assert "before" in change and "after" in change
    # 同一个行动的转换事件序号连续（e000003 起，因为 proposed/scheduled 各占一条）。
    started = [event for event in events if event["type"] == "action.started"]
    due = [event for event in events if event["type"] == "action.due"]
    assert len(started) == len(due) == 2
    for event in started + due:
        assert event["target"] in ("a1", "a2")
        assert event["actor"] in ("pc-a1", "pc-a2")
    assert [event["event_id"] for event in started] == ["a1:e000003", "a2:e000003"]
    assert [event["event_id"] for event in due] == ["a1:due001010", "a2:due001010"]
    # 窗口事件不指向任何行动卡（它不属于单个 action）：不能交给 apply_event。
    opened = [event for event in events if event["type"] == chron.WINDOW_OPEN_EVENT]
    assert len(opened) == 1
    assert opened[0]["target"] is None and opened[0]["actor"] in ("pc-a1", "pc-a2")
    assert opened[0]["changes"][0]["path"].startswith("/windows/")


def check_scheduler_is_pure_and_deterministic():
    """调度器纯函数：入参不被改写、同一输入两次结果全等。"""
    def build():
        state = chron.new_scheduler(SCHED_MINUTE)
        state = _schedule(state, _draft_action("a1", time_cost=10, resource="rope"))
        state = _schedule(state, _draft_action("a2", time_cost=10, resource="rope"))
        return state

    state = build()
    snapshot = {key: (list(value) if isinstance(value, list) else dict(value)
                      if isinstance(value, dict) else value) for key, value in state.items()}
    deep = {action_id: dict(action, blocks=list(action["blocks"]), events=list(action["events"]))
            for action_id, action in state["actions"].items()}
    fired = chron.advance_to(state, SCHED_MINUTE + 10)
    assert state == snapshot, "advance_to 不得改写入参状态"
    assert state["actions"] == deep, "advance_to 不得改写行动卡"
    assert state["windows"] == [] and fired["state"]["windows"], "窗口应写在新状态上"
    settled = chron.settle(fired["state"], "a1", "completed", world_minute=SCHED_MINUTE + 10)
    assert fired["state"]["actions"]["a1"]["status"] == "running", "settle 不得改写入参"
    assert settled["state"]["actions"]["a1"]["status"] == "completed"
    # 全等：同一输入两次运行产出全等的事件流与全等的末态。
    again = chron.advance_to(build(), SCHED_MINUTE + 10)
    assert again["events"] == fired["events"], "两次运行事件流应全等"
    assert again["state"] == fired["state"], "两次运行末态应全等"
    settled_again = chron.settle(again["state"], "a1", "completed",
                                 world_minute=SCHED_MINUTE + 10)
    assert settled_again["state"] == settled["state"]
    assert settled_again["events"] == settled["events"]
    # 重建 due_heap 是幂等的（§8.2 第 4 步：重启恢复按同一 actions 重建）。
    assert chron.rebuild_heap(fired["state"]) == fired["state"]["due_heap"]


def check_schedule_input_errors():
    """调度入参错误**显式抛 `ScheduleError`**（不静默、不部分生效）。"""
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("a1", time_cost=10))
    cases = (
        ("重复 id", _draft_action("a1"), {"world_minute": SCHED_MINUTE}),
        ("依赖不存在", _draft_action("a9", depends_on=("nope",)),
         {"world_minute": SCHED_MINUTE}),
        ("自依赖", _draft_action("a9", depends_on=("a9",)), {"world_minute": SCHED_MINUTE}),
        ("时间倒流", _draft_action("a9"), {"world_minute": SCHED_MINUTE - 1}),
        ("start 在过去", _draft_action("a9"),
         {"world_minute": SCHED_MINUTE, "start_minute": SCHED_MINUTE - 1}),
    )
    for label, draft, kwargs in cases:
        for target_state in (state,):
            try:
                chron.schedule(target_state, draft, **kwargs)
            except chron.ScheduleError:
                continue
            raise AssertionError("%s 应抛 ScheduleError" % label)
    # 已排期的卡不能再排（schedule 只接草稿）。
    try:
        chron.schedule(state, state["actions"]["a1"], world_minute=SCHED_MINUTE)
    except chron.ScheduleError:
        pass
    else:
        raise AssertionError("非草稿应抛 ScheduleError")
    # 非法 `on_dep_fail` / 空的 place / resource 在构造或排期时显式拒绝。
    for kwargs in ({"on_dep_fail": "ignore"}, {"on_dep_fail": None}):
        try:
            chron.new_action("x", "pc", "k", 5, **kwargs)
        except (TypeError, ValueError):
            continue
        raise AssertionError("new_action(%r) 应被拒绝" % (kwargs,))
    for kwargs in ({"place": ""}, {"resource": ""}):
        try:
            chron.new_action("x", "pc", "k", 5, **kwargs)
        except ValueError:
            continue
        raise AssertionError("new_action(%r) 应被拒绝" % (kwargs,))
    # `settle` 只接终态、只接已知行动。
    for args in (("a1", "scheduled"), ("a1", "running")):
        try:
            chron.settle(state, *args, world_minute=SCHED_MINUTE)
        except chron.ScheduleError:
            continue
        raise AssertionError("settle%r 应被拒绝" % (args,))
    try:
        chron.settle(state, "nope", "completed", world_minute=SCHED_MINUTE)
    except chron.ScheduleError:
        pass
    else:
        raise AssertionError("settle 未知行动应被拒绝")


def check_blocked_policy_roundtrip():
    """`keep` 策略的卡停在 `blocked`；依赖恢复后重排 → 再次进堆（§5.3）。"""
    state = chron.new_scheduler(SCHED_MINUTE)
    state = _schedule(state, _draft_action("dep", time_cost=10))
    state = _schedule(state, _draft_action("keep", time_cost=10, depends_on=("dep",),
                                           on_dep_fail="keep"))
    fired = chron.advance_to(state, SCHED_MINUTE + 10)
    blocked = chron.settle(fired["state"], "dep", "failed", world_minute=SCHED_MINUTE + 10)
    assert blocked["state"]["actions"]["keep"]["status"] == "blocked"
    # 再级联一次是幂等的：已经在 blocked 的卡不产二次事件。
    again = chron.cascade_dependents(blocked["state"], SCHED_MINUTE + 11, {"dep"})
    assert "keep" not in again["events"], "已在目标态不得重复产事件"
    # 依赖恢复（PRISM 重排）→ blocked → scheduled，重新进堆。
    resumed = chron.apply_event(blocked["state"]["actions"]["keep"],
                                chron.transition(blocked["state"]["actions"]["keep"],
                                                 "scheduled",
                                                 {"world_minute": SCHED_MINUTE + 20,
                                                  "who": "S"}))
    assert resumed["status"] == "scheduled"
    # 把恢复后的卡放回状态后重建堆 → 重新可办。
    restored = dict(blocked["state"], actions=dict(blocked["state"]["actions"]))
    restored["actions"]["keep"] = resumed
    restored["due_heap"] = chron.rebuild_heap(restored)
    assert [entry[3] for entry in restored["due_heap"]] == ["keep"], restored["due_heap"]


# --------------------------------------------------------------------------
# §6 时间压缩与事件屏障（C4）
# --------------------------------------------------------------------------

# C4 用例的基准世界分钟（与 C2 / C3 同轴，但**独立一份**避免互相牵动）。
COMPRESS_MINUTE = 5000

# `compression_log` 的字段（压缩日志只记真实事件，形状固定）。
COMPRESSION_LOG_FIELDS = ("from_minute", "to_minute", "target_minute", "stopped_at",
                          "offline_advance", "fired_minutes", "event_ids")

# 已知事件词表：压缩**只**产出这些真实事件——没有任何「被跳过 / 模拟」类事件。
KNOWN_EVENT_TYPES = ({row["event"] for row in chron.ACTION_TRANSITIONS}
                     | {chron.ACTION_DUE_EVENT, chron.WINDOW_OPEN_EVENT,
                        chron.WINDOW_CLOSE_EVENT})

BARRIER_NAMES = {row["id"]: row["name"] for row in chron.BARRIERS}


def _scheduled(state, draft, *, minute=COMPRESS_MINUTE, **kwargs):
    """在指定分钟排一张卡，返回**新**调度器状态（`_schedule` 的显式分钟版本）。"""
    return _schedule(state, draft, minute=minute, **kwargs)


def _check_barrier_record(record, minute=None):
    """屏障记录逐字段（§6.2）：形状固定、编号受词表约束、名称一致。"""
    assert set(record) == set(chron.BARRIER_FIELDS), record
    assert record["id"] in chron.BARRIER_IDS, record
    assert record["name"] == BARRIER_NAMES[record["id"]], record
    assert isinstance(record["reason"], str) and record["reason"], record
    assert record["subject"] is None or isinstance(record["subject"], str), record
    if minute is not None:
        assert record["minute"] == minute, record
    return record


def _barrier_ids(records):
    """记录里的判据编号（按出现顺序；确定性）。"""
    return [record["id"] for record in records]


def check_barrier_table_is_complete():
    """§6.2 的 **B1–B8 判据表**：八条齐备、字段非空、编号 / 词表自洽（机读判据可审查）。"""
    assert chron.BARRIER_IDS == ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8")
    assert tuple(row["id"] for row in chron.BARRIERS) == chron.BARRIER_IDS
    for row in chron.BARRIERS:
        assert set(row) == {"id", "name", "criterion", "machine"}, row
        assert row["name"] and row["criterion"] and row["machine"], row
    assert BARRIER_NAMES == {
        "B1": "进入战斗", "B2": "发现关键线索", "B3": "玩家角色安全受威胁", "B4": "角色遭遇",
        "B5": "世界状态冲突", "B6": "玩家有未决决策", "B7": "行动转 blocked / interrupted",
        "B8": "有行动到期",
    }
    assert chron.BARRIER_FIELDS[0] == "id"
    assert set(chron.BARRIER_FIELDS) == {"id", "name", "minute", "reason", "subject"}
    for ids in (chron.NOW_BARRIER_IDS, chron.PAUSING_BARRIER_IDS):
        assert set(ids) <= set(chron.BARRIER_IDS), ids
    for table in (chron.KIND_BARRIERS, chron.INTERRUPT_BARRIERS):
        assert set(table.values()) <= set(chron.BARRIER_IDS), table
    assert chron.KIND_BARRIERS["combat"] == "B1"
    assert chron.KIND_BARRIERS["discover"] == "B2"
    assert chron.INTERRUPT_BARRIERS["enemy_appears"] == "B1"


def check_every_barrier_criterion_has_a_case():
    """§6.2 **B1–B8 逐条**各一条用例：判据能在状态上机读命中（`next_barrier` / `barriers_at`）。"""
    # B1 进入战斗：战斗行动安排在 +180 开始 → 停点 = 它的开始分钟（还没开始，停在之前）。
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE), _draft_action("trip", time_cost=600))
    state = _scheduled(state, _draft_action("fight", kind="combat", time_cost=60),
                       start_minute=COMPRESS_MINUTE + 180)
    assert chron.next_barrier(state) == COMPRESS_MINUTE + 180
    hit = [record for record in chron.barriers_at(state, COMPRESS_MINUTE + 180)
           if record["id"] == "B1"]
    assert len(hit) == 1 and hit[0]["reason"] == "kind:combat" and hit[0]["subject"] == "fight"
    _check_barrier_record(hit[0], COMPRESS_MINUTE + 180)
    # B1（进行中）：战斗一旦开始，就在「现在」拦（不能快进战斗）。
    paused = chron.advance_to(state, COMPRESS_MINUTE + 360)
    assert paused["world_minute"] == COMPRESS_MINUTE + 180
    running = chron.advance_to(paused["state"], COMPRESS_MINUTE + 360)
    assert running["state"]["actions"]["fight"]["status"] == "running"
    assert chron.next_barrier(running["state"]) == running["world_minute"]
    now_hit = [record for record in chron.barriers_at(running["state"], running["world_minute"])
               if record["id"] == "B1"]
    assert [record["reason"] for record in now_hit] == ["running:combat"]
    # B2 发现关键线索：发现类行动的**到期**分钟（「到达时发现」）。
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE),
                       _draft_action("clue", kind="discover", time_cost=200))
    assert chron.next_barrier(state) == COMPRESS_MINUTE + 200
    hit = [record for record in chron.barriers_at(state, COMPRESS_MINUTE + 200)
           if record["id"] == "B2"]
    assert len(hit) == 1 and hit[0]["reason"] == "kind:discover" and hit[0]["subject"] == "clue"
    # B3 玩家角色安全受威胁：危险类行动的开始；带 `hazard` 中断条件同样命中。
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE),
                       _draft_action("risk", kind="hazard", time_cost=90),
                       start_minute=COMPRESS_MINUTE + 90)
    assert chron.next_barrier(state) == COMPRESS_MINUTE + 90
    assert [record["reason"] for record in chron.barriers_at(state, COMPRESS_MINUTE + 90)
            if record["id"] == "B3"] == ["kind:hazard"]
    guarded = chron.new_action("guarded", "pc-2", "travel", 90,
                              interrupt_conditions=[{"kind": "hazard", "expr": "storm"}])
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE), guarded,
                       start_minute=COMPRESS_MINUTE + 90)
    assert [record["reason"] for record in chron.barriers_at(state, COMPRESS_MINUTE + 90)
            if record["id"] == "B3"] == ["interrupt:hazard"]
    # B4 角色遭遇：遭遇类行动的开始。
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE),
                       _draft_action("meet", kind="encounter", time_cost=120),
                       start_minute=COMPRESS_MINUTE + 120)
    assert chron.next_barrier(state) == COMPRESS_MINUTE + 120
    assert [record["reason"] for record in chron.barriers_at(state, COMPRESS_MINUTE + 120)
            if record["id"] == "B4"] == ["kind:encounter"]
    # B5 世界状态冲突：两个同资源 / 同地点的行动 → 识别命中在**较晚的到期分钟**。
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE),
                       _draft_action("loot-a", time_cost=30, resource="idol"))
    state = _scheduled(state, _draft_action("loot-b", time_cost=30, resource="idol"),
                       minute=COMPRESS_MINUTE)
    hit = [record for record in chron.barriers_at(state, COMPRESS_MINUTE + 30)
           if record["id"] == "B5"]
    assert [record["reason"] for record in hit] == ["conflict:resource:idol"]
    assert hit[0]["subject"] == "resource:idol"
    # B5（窗口开着且参与者仍在飞）→ 在「现在」拦；参与者全部落定后不再拦（可压缩）。
    opened = chron.advance_to(state, COMPRESS_MINUTE + 30)
    assert len(chron.open_windows(opened["state"])) == 1
    assert chron.next_barrier(opened["state"]) == opened["world_minute"]
    assert [record["reason"] for record in
            chron.barriers_at(opened["state"], opened["world_minute"])
            if record["reason"] == "window_open"]
    left = chron.settle(opened["state"], "loot-a", "completed", world_minute=opened["world_minute"])
    assert chron.next_barrier(left["state"]) == opened["world_minute"], "另一个参与者还在飞"
    done = chron.settle(left["state"], "loot-b", "completed", world_minute=opened["world_minute"])
    assert chron.next_barrier(done["state"]) is None, "参与者都落定后窗口不再拦压缩"
    # B6 玩家有未决决策：停在 `proposed` 的卡；显式 `needs_confirmation = False` 时不拦。
    draft = chron.new_action("idea", "pc-1", "travel", 30)
    proposed = chron.apply_event(draft, chron.transition(draft, "proposed",
                                                         {"world_minute": COMPRESS_MINUTE}))
    state = chron.new_scheduler(COMPRESS_MINUTE)
    state["actions"]["idea"] = proposed
    assert chron.next_barrier(state) == COMPRESS_MINUTE
    assert [record["reason"] for record in chron.barriers_at(state, COMPRESS_MINUTE)
            if record["id"] == "B6"] == ["proposal_pending"]
    state["actions"]["idea"] = dict(proposed, needs_confirmation=False)
    assert chron.next_barrier(state) is None, "规则包显式声明无需确认时不拦"
    # B7 行动转 blocked / interrupted：状态机产生这两类转换 → 现在就拦。
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE), _draft_action("wait", time_cost=30))
    for status in ("blocked", "interrupted"):
        card = _to_state(status, start_minute=COMPRESS_MINUTE, minute=COMPRESS_MINUTE)
        broken = chron.new_scheduler(COMPRESS_MINUTE)
        broken["actions"]["wait"] = card
        broken["due_heap"] = []
        assert chron.next_barrier(broken) == COMPRESS_MINUTE
        assert [record["reason"] for record in chron.barriers_at(broken, COMPRESS_MINUTE)
                if record["id"] == "B7"] == ["status:%s" % status]
    # B8 有行动到期：只要有到期就是停点（逐条列出）。
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE), _draft_action("mend", time_cost=30))
    assert chron.next_barrier(state) == COMPRESS_MINUTE + 30
    due = [record for record in chron.barriers_at(state, COMPRESS_MINUTE + 30)
           if record["id"] == "B8"]
    assert len(due) == 1 and due[0]["reason"] == "action_due" and due[0]["subject"] == "mend"
    # `next_barrier` 空状态 → 无屏障；`barriers_at` 空分钟 → 无记录。
    assert chron.next_barrier(chron.new_scheduler(COMPRESS_MINUTE)) is None
    assert chron.barriers_at(state, COMPRESS_MINUTE + 1) == []


def check_compression_stops_before_barrier():
    """§6.5 第 1 条：行程穿屏障 —— 停在屏障**之前**，之前的到期全部已触发。

    一段 6 小时（360 分钟）的短程，途中第 180 分钟开始战斗：`advance_to(360)` 必须
    停在 180，且 180 之前的到期事件全部已触发；180 那一刻的内容（战斗开始）**不执行**。
    """
    state = chron.new_scheduler(COMPRESS_MINUTE)
    state = _scheduled(state, _draft_action("scout", time_cost=60))           # 180 之前到期
    state = _scheduled(state, _draft_action("trip", time_cost=360))           # 6 小时短程
    state = _scheduled(state, _draft_action("fight", kind="combat", time_cost=60),
                       start_minute=COMPRESS_MINUTE + 180)
    fired = chron.advance_to(state, COMPRESS_MINUTE + 360)
    assert fired["world_minute"] == COMPRESS_MINUTE + 180, "应停在屏障之前，不是目标 360"
    assert fired["stopped_at"] == COMPRESS_MINUTE + 180
    assert fired["target"] == COMPRESS_MINUTE + 360
    assert _barrier_ids(fired["barriers"]) == ["B1"]
    assert fired["barriers"][0]["subject"] == "fight"
    # 180 之前的到期事件全部已触发（scout 在自己的到期分钟）。
    due = [event for event in fired["events"] if event["type"] == chron.ACTION_DUE_EVENT]
    assert [event["target"] for event in due] == ["scout"]
    assert [event["world_time"]["world_minute"] for event in due] == [COMPRESS_MINUTE + 60]
    # 屏障那一分钟的内容**不执行**：战斗还没开始，也没有它的到期事件。
    assert fired["state"]["actions"]["fight"]["status"] == "scheduled"
    assert [event["target"] for event in fired["events"] if event["target"] == "fight"] == []
    # 6 小时行程的到期（+360）没有被提前触发。
    assert fired["state"]["actions"]["trip"]["status"] == "scheduled"


def check_compression_without_barrier():
    """§6.5 第 2 条：无屏障可压缩 —— 抵达目标，途中事件按序触发。"""
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE), _draft_action("trip", time_cost=360))
    fired = chron.advance_to(state, COMPRESS_MINUTE + 360)
    assert fired["world_minute"] == COMPRESS_MINUTE + 360
    assert fired["stopped_at"] is None and fired["barriers"] == []
    assert [(event["type"], event["target"]) for event in fired["events"]] == \
        [("action.started", "trip"), (chron.ACTION_DUE_EVENT, "trip")]
    # 纯压缩（没有到期的长段）：到达目标但不产生任何事件（不编造「被跳过」的步骤）。
    quiet = chron.advance_to(state, COMPRESS_MINUTE + 120)
    assert quiet["world_minute"] == COMPRESS_MINUTE + 120
    assert quiet["events"] == [] and quiet["stopped_at"] is None
    assert quiet["compression_log"]["fired_minutes"] == []


def check_compression_keeps_causality():
    """§6.5 第 3 条：屏障不删因果 —— 线索没有被**提前**触发，也没有被**丢弃**。"""
    state = chron.new_scheduler(COMPRESS_MINUTE)
    state = _scheduled(state, _draft_action("trip", time_cost=360))
    state = _scheduled(state, _draft_action("clue", kind="discover", time_cost=200))
    state = _scheduled(state, _draft_action("fight", kind="combat", time_cost=60),
                       start_minute=COMPRESS_MINUTE + 180)
    fired = chron.advance_to(state, COMPRESS_MINUTE + 360)
    assert fired["world_minute"] == COMPRESS_MINUTE + 180
    assert [event["target"] for event in fired["events"]] == []
    # 线索动作仍在时间线上（未被丢弃、状态未变），只是还**没**到它自己的分钟。
    assert fired["state"]["actions"]["clue"]["status"] == "scheduled"
    assert "clue" in [entry[3] for entry in fired["state"]["due_heap"]]
    # 继续推进（玩家处理完屏障后）：线索恰好在**自己的到期分钟**触发——没有提前。
    resumed = chron.advance_to(fired["state"], COMPRESS_MINUTE + 360)
    assert resumed["stopped_at"] == COMPRESS_MINUTE + 200
    assert [(event["type"], event["target"]) for event in resumed["events"]] == \
        [("action.started", "clue"), (chron.ACTION_DUE_EVENT, "clue")]
    assert [event["world_time"]["world_minute"] for event in resumed["events"]] == \
        [COMPRESS_MINUTE + 200, COMPRESS_MINUTE + 200]
    # 屏障（B1）仍在：停在 +200 之后，战斗仍在它自己的开始分钟等着。
    assert resumed["state"]["actions"]["fight"]["status"] == "scheduled"


def check_due_always_stops():
    """§6.5 第 4 条：到期总停（B8）—— 没有任何 B1–B7，只要有到期也必须停。"""
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE),
                       _draft_action("mend", kind="rest", time_cost=200))
    fired = chron.advance_to(state, COMPRESS_MINUTE + 360)
    assert fired["world_minute"] == COMPRESS_MINUTE + 200, "到期即停，不是目标 360"
    assert fired["stopped_at"] == COMPRESS_MINUTE + 200
    assert _barrier_ids(fired["barriers"]) == ["B8"]
    assert [(event["type"], event["target"]) for event in fired["events"]] == \
        [("action.started", "mend"), (chron.ACTION_DUE_EVENT, "mend")]
    # 结算点未处理完 → 压缩原地停住（只重播同一结算点，幂等键不变）。
    stuck = chron.advance_to(fired["state"], COMPRESS_MINUTE + 360)
    assert stuck["world_minute"] == COMPRESS_MINUTE + 200 and stuck["stopped_at"] == \
        COMPRESS_MINUTE + 200
    assert [event["idempotency_key"] for event in stuck["events"]] == \
        [event["idempotency_key"] for event in fired["events"]][-1:]
    # 结算完成后放行：压缩继续到目标（没有下一个屏障）。
    settled = chron.settle(fired["state"], "mend", "completed",
                           world_minute=COMPRESS_MINUTE + 200)
    after = chron.advance_to(settled["state"], COMPRESS_MINUTE + 360)
    assert after["world_minute"] == COMPRESS_MINUTE + 360 and after["stopped_at"] is None


def check_barrier_wins_same_minute():
    """§6.5 第 5 条：屏障优先 —— 同一分钟既有到期又有屏障 → 屏障优先（停）。"""
    state = chron.new_scheduler(COMPRESS_MINUTE)
    state = _scheduled(state, _draft_action("scout", time_cost=60))            # 到期 +60
    state = _scheduled(state, _draft_action("fight", kind="combat", time_cost=60),
                       start_minute=COMPRESS_MINUTE + 60)                     # B1 在 +60
    fired = chron.advance_to(state, COMPRESS_MINUTE + 120)
    assert fired["world_minute"] == COMPRESS_MINUTE + 60
    assert fired["stopped_at"] == COMPRESS_MINUTE + 60
    assert _barrier_ids(fired["barriers"]) == ["B1", "B8"], "同分钟的到期也要如实列出"
    # 屏障优先：该分钟的到期**不执行**（scout 还没开始），也没有任何事件产出。
    assert fired["events"] == []
    assert fired["state"]["actions"]["scout"]["status"] == "scheduled"
    assert fired["state"]["actions"]["fight"]["status"] == "scheduled"
    # 到达屏障后再次推进 = 玩家已处理、继续：该分钟的到期照常触发（不删因果）。
    resumed = chron.advance_to(fired["state"], COMPRESS_MINUTE + 120)
    assert [(event["type"], event["target"]) for event in resumed["events"]] == \
        [("action.started", "scout"), (chron.ACTION_DUE_EVENT, "scout")]
    assert resumed["world_minute"] == COMPRESS_MINUTE + 60
    assert resumed["stopped_at"] == COMPRESS_MINUTE + 60


def check_compression_log_records_only_real_events():
    """§6.3 末条：压缩**只记真实事件**，不产生「被跳过的模拟」事件。"""
    state = _scheduled(chron.new_scheduler(COMPRESS_MINUTE),
                       _draft_action("trip", time_cost=10000))
    # 5000 分钟的压缩里没有任何到期：**不产出任何事件**，日志也没有凭空条目。
    quiet = chron.advance_to(state, COMPRESS_MINUTE + 5000)
    assert quiet["events"] == [], "被压缩掉的时段不得产生事件"
    assert quiet["world_minute"] == COMPRESS_MINUTE + 5000
    assert set(quiet["compression_log"]) == set(COMPRESSION_LOG_FIELDS)
    log = quiet["compression_log"]
    assert log["from_minute"] == COMPRESS_MINUTE
    assert log["to_minute"] == COMPRESS_MINUTE + 5000
    assert log["target_minute"] == COMPRESS_MINUTE + 5000
    assert log["stopped_at"] is None
    assert log["fired_minutes"] == [] and log["event_ids"] == []
    # 有真实事件时，日志的 `event_ids` 与 `events` **一一对应**（不多不少、顺序相同）。
    fired = chron.advance_to(state, COMPRESS_MINUTE + 10000)
    log = fired["compression_log"]
    assert log["event_ids"] == [event["event_id"] for event in fired["events"]]
    assert log["fired_minutes"] == [COMPRESS_MINUTE + 10000]
    assert log["stopped_at"] is None and log["to_minute"] == COMPRESS_MINUTE + 10000
    # 事件词表固定：整个推进里没有任何「跳过 / 模拟」类事件。
    assert fired["events"], "到期必须被触发"
    for event in fired["events"]:
        assert event["type"] in KNOWN_EVENT_TYPES, "压缩产出了词表外的事件：%r" % event["type"]
        assert "skip" not in event["type"] and "sim" not in event["type"], event["type"]
    # 同一份状态再推进一次：全等（确定性、日志亦然）。
    assert chron.advance_to(state, COMPRESS_MINUTE + 10000) == fired


def check_offline_advance_is_input_only():
    """`offline_advance` **只作入参**（默认 `False`，§3.4）——不判「谁在线」，也不改结果。"""
    def build():
        state = chron.new_scheduler(COMPRESS_MINUTE)
        return _scheduled(state, _draft_action("trip", time_cost=60))

    off = chron.advance_to(build(), COMPRESS_MINUTE + 60)
    on = chron.advance_to(build(), COMPRESS_MINUTE + 60, offline_advance=True)
    assert off["events"] == on["events"] and off["state"] == on["state"]
    assert off["compression_log"]["offline_advance"] is False, "默认 False（§3.4）"
    assert on["compression_log"]["offline_advance"] is True
    for bad in (0, 1, "false", None, "true"):
        try:
            chron.advance_to(build(), COMPRESS_MINUTE + 60, offline_advance=bad)
        except TypeError:
            continue
        raise AssertionError("offline_advance=%r 应被拒绝（只收 bool）" % (bad,))


def check_compression_is_pure_and_validates_input():
    """C4 纯函数性质：入参不被改写、两次运行全等、非法输入**显式拒绝**。"""
    def build():
        state = chron.new_scheduler(COMPRESS_MINUTE)
        state = _scheduled(state, _draft_action("trip", time_cost=360))
        state = _scheduled(state, _draft_action("fight", kind="combat", time_cost=60),
                           start_minute=COMPRESS_MINUTE + 180)
        return state

    state = build()
    snapshot = {key: (list(value) if isinstance(value, list) else dict(value)
                      if isinstance(value, dict) else value) for key, value in state.items()}
    deep = {action_id: dict(action, blocks=list(action["blocks"]), events=list(action["events"]))
            for action_id, action in state["actions"].items()}
    before = chron.next_barrier(state)
    at = chron.barriers_at(state, COMPRESS_MINUTE + 180)
    fired = chron.advance_to(state, COMPRESS_MINUTE + 360)
    assert state == snapshot, "advance_to / next_barrier / barriers_at 不得改写入参状态"
    assert state["actions"] == deep, "advance_to 不得改写行动卡"
    assert chron.next_barrier(state) == before, "只读判断不得改状态"
    assert chron.barriers_at(state, COMPRESS_MINUTE + 180) == at
    # 同一输入两次运行：事件流、末态、屏障记录、压缩日志全等。
    again = chron.advance_to(build(), COMPRESS_MINUTE + 360)
    assert again == fired, "两次运行应全等（确定性）"
    assert again is not fired and again["state"] is not fired["state"]
    # 入参校验：起点 / 目标 / 分钟的类型与范围。
    for bad in (-1, 1.5, True, "x", None):
        for call in (lambda value=bad: chron.advance_to(build(), value),
                     lambda value=bad: chron.barriers_at(build(), value)):
            try:
                call()
            except (TypeError, ValueError):
                continue
            raise AssertionError("非法分钟 %r 应被拒绝" % (bad,))
    for bad in (-1, 1.5, True, "x"):
        try:
            chron.next_barrier(build(), bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError("非法起点 %r 应被拒绝" % (bad,))
    # 起点省略 = 当前世界分钟（`None` 是合法缺省，不是非法值）。
    assert chron.next_barrier(build(), None) == chron.next_barrier(build())
    for bad_state in (None, "state", {}, {"world_minute": 0}):
        for call in (lambda value=bad_state: chron.next_barrier(value),
                     lambda value=bad_state: chron.barriers_at(value, 0),
                     lambda value=bad_state: chron.advance_to(value, 0)):
            try:
                call()
            except (TypeError, ValueError):
                continue
            raise AssertionError("非法调度器状态 %r 应被拒绝" % (bad_state,))
    # `next_barrier` 的起点不得早于当前世界时间（时间只增不减，§3.2）。
    try:
        chron.next_barrier(state, COMPRESS_MINUTE - 1)
    except chron.ScheduleError:
        pass
    else:
        raise AssertionError("next_barrier 起点回拨应抛 ScheduleError")
    # 停点之后继续推进：世界时间单调不减、堆可重建且幂等。
    assert fired["state"]["world_minute"] >= state["world_minute"]
    assert chron.rebuild_heap(fired["state"]) == fired["state"]["due_heap"]


# --------------------------------------------------------------------------
# §8 持久化与恢复（C5）
# --------------------------------------------------------------------------

# 迷你局的基准世界分钟（与 C2 / C3 / C4 的基准错开，互不牵动）。
PERSIST_MINUTE = 0


def _c5_store():
    """C5 用例的临时存档根（随机目录；调用方负责收摊）。"""
    return tempfile.mkdtemp(prefix="notdnd-chron-c5-")


def _c5_snapshot_path(root, campaign_id):
    """新档检查点路径（`campaigns/<id>/snapshot.json`，STATE §4.1）。"""
    return os.path.join(root, "campaigns", campaign_id, state.SNAPSHOT_NAME)


def _c5_read_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _c5_event_lines(root, campaign_id):
    """活动事件日志的行数（幂等用例：重投不得让日志变长）。"""
    path = os.path.join(root, "campaigns", campaign_id, state.EVENTS_NAME)
    with open(path, encoding="utf-8") as handle:
        return len([line for line in handle if line.strip()])


def _c5_steps(result):
    """一步产出的事件摊平成列表（`settle` / `cascade_dependents` 给的是按行动分组的 dict）。"""
    events = result["events"]
    if isinstance(events, dict):
        events = [event for action_id in sorted(events) for event in events[action_id]]
    return events


def _c5_commit(handle, block, result):
    """把一步产出的事件逐条提交（§8.3：一条事件 = 一次提交），返回**提交后**的块。"""
    for event in _c5_steps(result):
        outcome = chron.commit_event(handle, event, block=block, state=result["state"])
        assert outcome["status"] == "committed", (event["type"], outcome)
        block = outcome["block"]
    handle.stage(blocks={chron.CHRON_BLOCK_NAME: block})
    handle.flush()
    return block


def _c5_scenario(handle):
    """跑一遍「排程 → 到期 → 结算 → 纯压缩」的迷你局，返回 `(state, block)`。

    留下 `c`（到期 300）没结算：恢复之后到期队列必须非空，才能验证「到期顺序全等」。
    """
    block = chron.new_block(PERSIST_MINUTE)
    handle.stage(blocks={chron.CHRON_BLOCK_NAME: block})
    handle.flush()

    scheduler = chron.new_scheduler(PERSIST_MINUTE)
    for draft in (chron.new_action("a1", "pc-1", "travel", 60, place="room"),
                  chron.new_action("a2", "pc-2", "study", 60, place="room"),
                  chron.new_action("b", "pc-3", "rest", 30, priority=5),
                  chron.new_action("c", "pc-4", "travel", 300)):
        result = chron.schedule(scheduler, draft, world_minute=scheduler["world_minute"])
        scheduler = result["state"]
        block = _c5_commit(handle, block, result)

    result = chron.advance_to(scheduler, 30)
    scheduler = result["state"]
    block = _c5_commit(handle, block, result)
    result = chron.settle(scheduler, "b", "completed", world_minute=30)
    scheduler = result["state"]
    block = _c5_commit(handle, block, result)
    result = chron.advance_to(scheduler, 60)
    scheduler = result["state"]
    block = _c5_commit(handle, block, result)
    for action_id in ("a1", "a2"):
        result = chron.settle(scheduler, action_id, "completed", world_minute=60)
        scheduler = result["state"]
        block = _c5_commit(handle, block, result)

    result = chron.advance_to(scheduler, 240)          # 纯压缩：区间内没有到期
    scheduler = result["state"]
    assert result["events"] == [], result["events"]
    outcome = chron.commit_event(handle, chron.advance_event(scheduler["world_minute"]),
                                 block=block, state=scheduler)
    assert outcome["status"] == "committed", outcome
    block = outcome["block"]
    handle.stage(blocks={chron.CHRON_BLOCK_NAME: block})
    handle.flush()
    return scheduler, block


def check_chron_block_shape():
    """§8.1：块形状 / 默认值工厂 / 脏值降级 / 版本拒载，以及派生索引重建。"""
    block = chron.new_block()
    assert set(block) == set(chron.CHRON_BLOCK_FIELDS) | {chron.CHRON_FOLDED_NAME}, block
    assert block["version"] == chron.CHRON_BLOCK_VERSION == 1
    assert (block["world_minute"], block["seq"]) == (0, 0)
    assert block["actions"] == {} and block["windows"] == []
    assert block["idempotency"] == {} and block[chron.CHRON_FOLDED_NAME] == {}
    assert chron.new_block(1440, seq=7)["world_minute"] == 1440
    assert chron.new_block(1440, seq=7)["seq"] == 7
    for bad in (-1, 1.5, True, "0"):
        try:
            chron.new_block(bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError("new_block(%r) 应被拒绝" % (bad,))

    # 默认值工厂：两次现造不共享可变对象（否则一个存档的改动会漏进另一个）。
    first, second = chron.new_block(), chron.new_block()
    first["actions"]["x"] = {"status": None}
    first["idempotency"]["k"] = 1
    assert second["actions"] == {} and second["idempotency"] == {}, second

    # 缺块（老档）→ 补默认并留说明；已有值**逐字保留**（只补不覆盖）。
    notes = []
    assert chron.migrate_block(None, notes) == chron.new_block()
    assert notes, "缺块必须留下一条迁移说明"
    raw = {"version": 1, "world_minute": 500, "seq": 9, "actions": {},
           "windows": [], "idempotency": {"k": 3}}
    block = chron.migrate_block(raw, notes)
    assert block["world_minute"] == 500 and block["seq"] == 9
    assert block["idempotency"] == {"k": 3}
    assert block[chron.CHRON_FOLDED_NAME] == {}, "缺的字段按惰性迁移补默认"
    assert chron.CHRON_FOLDED_NAME not in raw, "迁移不得改写调用方的入参"

    # 脏值降级：坏字段只降级它自己，不因一键丢掉整块。
    notes = []
    dirty = {"version": 1, "world_minute": "五百", "seq": -1, "actions": ["不是表"],
             "windows": [{"window_id": "w"}, "不是对象"],
             "idempotency": {"k": "3", "j": 4}}
    block = chron.migrate_block(dirty, notes)
    assert block["world_minute"] == 0 and block["seq"] == 0
    assert block["actions"] == {}
    assert block["windows"] == [{"window_id": "w"}], block["windows"]
    assert block["idempotency"] == {"j": 4}, block["idempotency"]
    assert len(notes) >= 4, notes
    assert dirty["windows"] == [{"window_id": "w"}, "不是对象"], "迁移不得改写入参"

    # 更高版本 → 拒载（STATE §3 S6：不静默降级）。
    try:
        chron.migrate_block({"version": chron.CHRON_BLOCK_VERSION + 1})
    except chron.ChronError:
        pass
    else:
        raise AssertionError("更高版本的 chron 块必须拒载")

    # 块 ↔ 调度器状态：`due_heap` / `blocks` / `running` 全部按 `actions` 重建。
    scheduler = chron.new_scheduler(SCHED_MINUTE)
    scheduler = _schedule(scheduler, _draft_action("dep", actor="pc-1", time_cost=30,
                                                   place="room"))
    scheduler = _schedule(scheduler, _draft_action("child", actor="pc-2", time_cost=30,
                                                   depends_on=("dep",), place="room"))
    scheduler = _schedule(scheduler, _draft_action("solo", actor="pc-3", time_cost=10))
    block = chron.block_from_scheduler(scheduler)
    assert block["actions"] == scheduler["actions"], "行动卡逐字进块"
    assert "due_heap" not in block, "due_heap 是派生索引，不落盘（§8.1）"

    rebuilt = chron.scheduler_from_block(block)
    assert rebuilt["world_minute"] == scheduler["world_minute"]
    assert rebuilt["actions"] == scheduler["actions"]
    assert rebuilt["windows"] == scheduler["windows"]
    assert rebuilt["keys"] == scheduler["keys"]
    assert rebuilt["due_heap"] == scheduler["due_heap"], "到期顺序必须与重启前全等"
    assert rebuilt["blocks"] == scheduler["blocks"], "依赖反向索引按卡重建"
    assert rebuilt["running"] == scheduler["running"], "在飞冲突键按卡重建"
    assert chron.scheduler_from_block(block) == rebuilt, "重建必须确定性"

    # 在飞行动的 `running`：开始之后才登记（`scheduled` 不算）。
    flying = chron.new_scheduler(SCHED_MINUTE)
    flying = _schedule(flying, _draft_action("f", actor="pc-1", time_cost=10,
                                             place="room"))
    flying = _schedule(flying, _draft_action("g", actor="pc-2", time_cost=10,
                                             place="room"))
    assert chron.scheduler_from_block(chron.block_from_scheduler(flying))["running"] == {}
    started = chron.advance_to(flying, SCHED_MINUTE + 10)["state"]
    assert started["running"] == {"f": "place:room", "g": "place:room"}, started["running"]
    assert chron.scheduler_from_block(chron.block_from_scheduler(started))["running"] == \
        started["running"]


def check_merge_snapshot_and_log():
    """§8.2 第 1 / 3 步的合并口径：日志为准、幂等表只加不减、折叠优先。"""
    old_card = {"action_id": "x", "status": "scheduled", "events": ["x:e000001"],
                "due_minute": 130, "priority": 0, "depends_on": []}
    new_card = dict(old_card, status="running", events=["x:e000001", "x:e000002"])
    folded = {"action_id": "y", "status": "completed", "end_minute": 90,
              "events": ["y:e000001"]}

    snapshot = chron.new_block(100)
    snapshot["actions"]["x"] = old_card
    snapshot["idempotency"]["k1"] = 1
    log = chron.new_block(150)
    log["actions"]["x"] = new_card
    log["actions"]["y"] = {"action_id": "y", "status": "scheduled",
                           "events": ["y:e000001"], "due_minute": 160, "priority": 0,
                           "depends_on": []}
    log["idempotency"]["k2"] = 2
    log[chron.CHRON_FOLDED_NAME]["y"] = folded

    merged = chron.merge_blocks(snapshot, log)
    assert merged["world_minute"] == 150, "时钟取大（世界时间只增不减）"
    assert merged["seq"] == max(snapshot["seq"], log["seq"])
    assert merged["actions"]["x"] == new_card, "同一张卡取「最后一条事件序号」更大的一边"
    assert "y" not in merged["actions"], "折叠优先：裁剪过的行动不得被翻回来"
    assert merged[chron.CHRON_FOLDED_NAME]["y"] == folded
    assert merged["idempotency"] == {"k1": 1, "k2": 2}, "幂等表只加不减"
    # 只有一边有的卡照收（快照里 staged、日志里没有的同样是事实）。
    snapshot["actions"]["z"] = {"action_id": "z", "status": "scheduled",
                                "events": ["z:e000001"], "due_minute": 110,
                                "priority": 0, "depends_on": []}
    merged = chron.merge_blocks(snapshot, log)
    assert merged["actions"]["z"] == snapshot["actions"]["z"]
    # 窗口：按 `window_id` 去重，保留首次出现的次序；同一 id 取**参与者更多**的一边
    # （C3 的「并入窗口」不进事件，快照侧往往更完整）。
    snapshot["windows"] = [{"window_id": "2:b", "key": "b", "participants": ["x"]},
                           {"window_id": "1:a", "key": "a", "participants": ["x", "y"]}]
    log["windows"] = [{"window_id": "1:a", "key": "a", "participants": ["x"]}]
    merged = chron.merge_blocks(snapshot, log)
    assert [window["window_id"] for window in merged["windows"]] == ["2:b", "1:a"]
    assert merged["windows"][1]["participants"] == ["x", "y"], merged["windows"]


def check_restart_roundtrip():
    """验收 1（§8.2 时序）：重启后 `world_minute` 与到期顺序与重启前**全等**。"""
    root = _c5_store()
    try:
        handle = state.open_campaign("persist-a", base_dir=root)
        live_state, live_block = _c5_scenario(handle)
        assert live_state["world_minute"] == 240, live_state["world_minute"]
        assert [entry[3] for entry in live_state["due_heap"]] == ["c"], live_state["due_heap"]

        # 「重启前」的证据：块真的落在了检查点里（不是只在内存 / 只在日志里）。
        snapshot = _c5_read_json(_c5_snapshot_path(root, "persist-a"))
        assert snapshot["blocks"][chron.CHRON_BLOCK_NAME] == live_block, "块必须随检查点落盘"
        seq_before = snapshot["seq"]
        handle.close()

        # 重启：新句柄 + §8.2 的恢复时序
        again = state.open_campaign("persist-a", base_dir=root)
        notes = []
        restored = chron.restore(again, notes=notes)
        assert notes == [], "正常档不该有迁移 / 降级 / 抬钟说明：%r" % (notes,)
        assert restored["seq"] == again.view()["seq"] == seq_before
        assert restored["world_minute"] == live_state["world_minute"]
        assert restored["block"] == live_block, "恢复出的块必须与重启前全等"
        for field in ("world_minute", "actions", "windows", "due_heap", "blocks", "running"):
            assert restored["state"][field] == live_state[field], \
                "调度器状态 %s 必须全等" % field
        # `seq` / `keys` 以**块**（= 状态层已折入的事件序号 / 幂等表）为准（§8.1），
        # 不是内存里那份调度器自己的（它只由调用方在提交后同步）。
        assert restored["state"]["seq"] == restored["block"]["seq"] == seq_before
        assert restored["state"]["keys"] == restored["block"]["idempotency"]
        assert restored["block"]["idempotency"], "幂等表必须随块活下来"

        # 到期顺序全等：用恢复出的状态继续推进，事件流与重启前一致。
        live_next = chron.advance_to(live_state, 300)
        restored_next = chron.advance_to(restored["state"], 300)
        assert [event["idempotency_key"] for event in restored_next["events"]] == \
            [event["idempotency_key"] for event in live_next["events"]]
        assert [event["type"] for event in restored_next["events"]] == \
            ["action.started", chron.ACTION_DUE_EVENT]
        assert [event["target"] for event in restored_next["events"]] == ["c", "c"]
        assert restored_next["state"]["world_minute"] == 300
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_idempotency_no_double_effect():
    """验收 2（§8.3）：同 `idempotency_key` 重投，副作用只发生一次。"""
    root = _c5_store()
    try:
        handle = state.open_campaign("persist-b", base_dir=root)
        block = chron.new_block(PERSIST_MINUTE)
        handle.stage(blocks={chron.CHRON_BLOCK_NAME: block})
        handle.flush()

        scheduler = chron.new_scheduler(PERSIST_MINUTE)
        draft = chron.new_action("t1", "pc-1", "rest", 30)
        result = chron.schedule(scheduler, draft, world_minute=PERSIST_MINUTE)
        scheduler = result["state"]
        block = _c5_commit(handle, block, result)

        # 推进到到期：`scheduled → running` + `action.due` 两条事件；逐条提交并记下
        # 每条事件**提交前**的块（下面要用同一条载荷复现状态层的重放结果）。
        step = chron.advance_to(scheduler, 30)
        scheduler = step["state"]
        step_events = _c5_steps(step)
        before_each = []
        for event in step_events:
            before_each.append(block)
            outcome = chron.commit_event(handle, event, block=block, state=step["state"])
            assert outcome["status"] == "committed", (event["type"], outcome)
            block = outcome["block"]
        handle.stage(blocks={chron.CHRON_BLOCK_NAME: block})
        handle.flush()
        due_index = [index for index, event in enumerate(step_events)
                     if event["type"] == chron.ACTION_DUE_EVENT][0]
        due = step_events[due_index]
        seq_committed = handle.view()["seq"]
        lines_committed = _c5_event_lines(root, "persist-b")

        # ① CHRON 侧闸：键已在块里 → `skipped`，连状态层都不惊动。
        outcome = chron.commit_event(handle, due, block=block, state=step["state"])
        assert outcome["status"] == "skipped", outcome
        assert handle.view()["seq"] == seq_committed, "重投不得再排一次事件号"
        assert _c5_event_lines(root, "persist-b") == lines_committed, "重投不得写第二行事件"
        assert chron.pending_events(block, step_events) == []
        assert chron.committed_seq(block, due["idempotency_key"]) == seq_committed
        assert chron.is_committed(block, due["idempotency_key"])

        # ② 状态层侧闸（STATE §7.4）：块只是**加速副本**——把该键从副本里抹掉再投，
        #    同键同载荷仍返回 `replayed`，不写事件、副作用只发生一次。
        stripped = json.loads(json.dumps(before_each[due_index]))
        assert due["idempotency_key"] not in stripped["idempotency"], "夹具应缺这条键"
        outcome = chron.commit_event(handle, due, block=stripped, state=step["state"])
        assert outcome["status"] == "replayed", outcome
        assert handle.view()["seq"] == seq_committed
        assert _c5_event_lines(root, "persist-b") == lines_committed
        # 重放结果把键重新记回块里（自愈）。
        assert outcome["block"]["idempotency"][due["idempotency_key"]] == seq_committed

        # ③ 幂等表随块落盘：重启后仍在（`restore` 还能从日志折回来）。
        handle.stage(blocks={chron.CHRON_BLOCK_NAME: outcome["block"]})
        handle.flush()
        handle.close()
        restored = chron.restore(state.open_campaign("persist-b", base_dir=root))
        assert restored["state"]["keys"] == outcome["block"]["idempotency"]
        assert restored["block"]["idempotency"][due["idempotency_key"]] == seq_committed
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_missing_block_defaults_and_rewrite():
    """验收 3（§8.4）：老档（无 `chron`）载入补默认 → 再存 → 新档可读。"""
    root = _c5_store()
    try:
        # 老档形态：检查点里**没有** chron 块（其余不透明块都在）。
        handle = state.open_campaign("persist-legacy", base_dir=root)
        handle.stage(blocks={"rules": {"party": []}}, stream={"log": [], "cursor": 0})
        handle.flush()
        handle.close()
        snapshot_path = _c5_snapshot_path(root, "persist-legacy")
        assert chron.CHRON_BLOCK_NAME not in _c5_read_json(snapshot_path)["blocks"], \
            "夹具应当没有 chron 块"

        notes = []
        restored = chron.restore(state.open_campaign("persist-legacy", base_dir=root),
                                 notes=notes)
        assert restored["block"] == chron.new_block(), restored["block"]
        assert restored["world_minute"] == 0 and restored["seq"] == 0
        assert restored["state"]["due_heap"] == [] and restored["state"]["actions"] == {}
        assert any("缺失" in note for note in notes), notes

        # 补默认 → 再存 → 新档可读（块随检查点落盘，形状仍是 §8.1 的那一块）。
        again = state.open_campaign("persist-legacy", base_dir=root)
        again.stage(blocks={chron.CHRON_BLOCK_NAME: restored["block"]})
        again.flush()
        again.close()
        third = chron.restore(state.open_campaign("persist-legacy", base_dir=root),
                              notes=[])
        assert third["block"] == chron.new_block()
        assert _c5_read_json(snapshot_path)["blocks"][chron.CHRON_BLOCK_NAME] == \
            chron.new_block()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def check_prune_and_fold():
    """§8.4 裁剪：幂等表保留最新 `keep` 条；终态行动折叠为事件引用。"""
    notes = []
    block = chron.new_block(SCHED_MINUTE)
    block["idempotency"] = {"k%d" % index: index for index in range(5)}
    pruned = chron.prune_block(block, keep=2, notes=notes)
    assert pruned["idempotency"] == {"k4": 4, "k3": 3}, pruned["idempotency"]
    assert block["idempotency"] == {"k%d" % index: index for index in range(5)}, \
        "裁剪不得改写调用方的入参"
    assert notes, "裁剪必须留下说明"
    for bad in (-1, True, "8"):
        try:
            chron.prune_block(block, keep=bad)
        except (TypeError, ValueError):
            continue
        raise AssertionError("keep=%r 应被拒绝" % (bad,))

    # 终态折叠：早结算的卡折成事件引用；**还有人依赖的卡不折**。
    scheduler = chron.new_scheduler(SCHED_MINUTE)
    scheduler = _schedule(scheduler, _draft_action("done", actor="pc-1", time_cost=10))
    scheduler = _schedule(scheduler, _draft_action("heir", actor="pc-2", time_cost=10,
                                                   depends_on=("done",),
                                                   on_dep_fail="keep"))
    scheduler = _schedule(scheduler, _draft_action("old", actor="pc-3", time_cost=10))
    # 先推进到两者的到期分钟（到期即「开始」，`scheduled → running` 才能结算）。
    scheduler = chron.advance_to(scheduler, SCHED_MINUTE + 10)["state"]
    scheduler = chron.settle(scheduler, "old", "completed",
                             world_minute=SCHED_MINUTE + 10)["state"]
    scheduler = chron.settle(scheduler, "done", "completed",
                             world_minute=SCHED_MINUTE + 20)["state"]
    assert scheduler["actions"]["done"]["status"] == "completed"

    folded = chron.prune_block(chron.block_from_scheduler(scheduler),
                               fold_terminals_before=SCHED_MINUTE + 20, notes=notes)
    assert "old" not in folded["actions"], "终态且无人依赖的卡应折叠"
    assert folded[chron.CHRON_FOLDED_NAME]["old"] == {
        "action_id": "old", "status": "completed", "end_minute": SCHED_MINUTE + 10,
        "events": scheduler["actions"]["old"]["events"],
    }, folded[chron.CHRON_FOLDED_NAME]["old"]
    assert "done" in folded["actions"], "还有行动依赖它的卡不得折叠"
    assert "heir" in folded["actions"], "非终态卡不得折叠"

    # 折叠后的块仍是合法调度器状态的来源：依赖解析与到期队列都照常。
    rebuilt = chron.scheduler_from_block(folded)
    assert "old" not in rebuilt["actions"]
    assert [entry[3] for entry in rebuilt["due_heap"]] == ["heir"], rebuilt["due_heap"]

    # 没有 `end_minute` 的终态卡（脏数据）不折。
    dirty = chron.block_from_scheduler(scheduler)
    dirty["actions"]["old"] = dict(dirty["actions"]["old"], end_minute=None)
    kept = chron.prune_block(dirty, fold_terminals_before=SCHED_MINUTE + 999)
    assert "old" in kept["actions"], "拿不到 end_minute 的终态卡不猜、不折"


def check_commit_changes_are_self_contained():
    """§8.3：提交用的 `changes` 是**单段**路径（块还不存在时也成立），且不改写入参。"""
    before = chron.new_block(0)
    after = chron.new_block(60, seq=1)
    after["actions"]["a"] = {"action_id": "a", "status": "running",
                             "events": ["a:e000001"], "due_minute": 90, "priority": 0,
                             "depends_on": []}
    changes = chron.block_changes(before, after)
    assert [change["path"] for change in changes] == ["/world_minute", "/seq", "/actions"], \
        changes
    for change in changes:
        assert change["block"] == chron.CHRON_BLOCK_NAME
        assert change["op"] == "set"
        assert change["path"].count("/") == 1, "必须是**单段**路径：块不存在时也成立"
        assert "before" in change and "after" in change
    assert before == chron.new_block(0), "折 changes 不得改写事件前的块"
    assert after["actions"]["a"]["status"] == "running", "折 changes 不得改写事件后的块"
    # 字段在「事件后」消失 → `unset`（否则老块里的脏字段永远删不掉）。
    shrunk = chron.new_block(0)
    del shrunk["actions"]
    removed = [change for change in chron.block_changes(after, shrunk)
               if change["path"] == "/actions"]
    assert removed and removed[0]["op"] == "unset" and removed[0]["after"] is None
    # `advance_event` 是 §8.3 的确定性形状：同一分钟重投 = 同一次事实。
    event = chron.advance_event(90)
    assert event["type"] == chron.CLOCK_ADVANCE_EVENT
    assert event["idempotency_key"] == "clock:90:%s" % chron.CLOCK_ADVANCE_EVENT
    assert event["world_time"] == {"world_minute": 90, "day": 1, "tod": 90}
    assert event["source"] == "system" and event["actor"] == "world"
    assert chron.advance_event(90) == event


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
        # §5 调度器（C3）
        check_scheduler_state_shape,
        check_due_order_is_stable,
        check_sort_key_segments,
        check_dependency_gates_release,
        check_dependency_cycle_rejected,
        check_dependency_failure_cascades,
        check_resource_conflict_opens_one_window,
        check_window_identification_rules,
        check_advance_to_fires_in_order,
        check_scheduler_events_shape,
        check_scheduler_is_pure_and_deterministic,
        check_schedule_input_errors,
        check_blocked_policy_roundtrip,
        # §6 时间压缩与事件屏障（C4）
        check_barrier_table_is_complete,
        check_every_barrier_criterion_has_a_case,
        check_compression_stops_before_barrier,
        check_compression_without_barrier,
        check_compression_keeps_causality,
        check_due_always_stops,
        check_barrier_wins_same_minute,
        check_compression_log_records_only_real_events,
        check_offline_advance_is_input_only,
        check_compression_is_pure_and_validates_input,
        # §8 持久化与恢复（C5）
        check_chron_block_shape,
        check_merge_snapshot_and_log,
        check_restart_roundtrip,
        check_idempotency_no_double_effect,
        check_missing_block_defaults_and_rewrite,
        check_prune_and_fold,
        check_commit_changes_are_self_contained,
        check_module_is_pure,
    )
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败 [%s]: %s" % (check.__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks), failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
