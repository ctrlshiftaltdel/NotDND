#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chron —— CHRON 内核（M9-C1）：世界时钟与时间单位（纯函数）。

本模块只实现 `CHRON-DESIGN.md` 第 2 节（时间单位与换算口径）与第 3 节
（权威世界时钟）的**纯函数**：

- 分钟常数（§2.4）：`SECONDS_PER_MINUTE` / `MINUTES_PER_HOUR` / `MINUTES_PER_DAY` /
  `WATCH_SPANS` / `NOMINAL_WATCH_MINUTES` / `MINOR_ACTION_MINUTES` /
  `MAJOR_ACTION_MINUTES` / `SESSION_WATCHES`。
- 世界时钟（§3.1–§3.3）：`watch_of` / `to_calendar` / `from_calendar` / `advance_clock`。
- §2.5 的 `RATE_DOMAINS` 第一版**只预留字段、不实现**域间流速换算（归 M11）。

**本单边界**：不排程、不落库、不碰 IO；不 import 任何业务模块（§7 依赖方向）。
世界时间是一条**自战役纪元起的整数分钟轴，只增不减**（§3.2）。

口径依据：
- `CHRON-DESIGN.md` §2.3「权威刻度 = 整数分钟」、§2.4 常量表、§3.1 表示法。
- 四段时段取自 `data/system/watch.json`：晨 240 / 昼 480 / 昏 240 / 夜 480（**不等长**）。
- 行程/预算口径的「时段」= 名义 360 分钟（`data/system/travel.json` 短程 360）。
"""

# --------------------------------------------------------------------------
# §2.4 换算表（CHRON 常量）
# --------------------------------------------------------------------------

SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 1440  # 1 天 = 24 小时

# 时段（Watch）= 一天的四段，**实际边界**取自 data/system/watch.json（晨 05–09 等）。
# 四段不等长是刻意的（叙事节奏），因此**任何换算都不以「时段」为单位做乘法**。
WATCH_SPANS = {
    "dawn": 240,   # 晨 05:00–09:00
    "day": 480,    # 昼 09:00–17:00
    "dusk": 240,   # 昏 17:00–21:00
    "night": 480,  # 夜 21:00–05:00（跨夜）
}

NOMINAL_WATCH_MINUTES = 360        # 行程 / 预算口径：1 时段 = 6 名义小时
MINOR_ACTION_MINUTES = 10          # 小事；最小排程步长（来自 watch.json budget）
MAJOR_ACTION_MINUTES = (60, 180)   # 大事：1–3 小时（watch.json budget）
SESSION_WATCHES = (3, 6)           # 一个典型会话 3–6 时段（watch.json pacing）

# 四段在一天内的**先后顺序**（夜跨夜收尾，故排在最后）。
WATCH_ORDER = ("dawn", "day", "dusk", "night")


def _watch_start_minutes():
    """由 WATCH_SPANS + WATCH_ORDER 推出每段的起始分钟（晨自 05:00 起）。

    单一真相是 WATCH_SPANS：起点 + 逐段累加，故四段边界恒为
    05:00 / 09:00 / 17:00 / 21:00，且总和恒等于 MINUTES_PER_DAY。
    """
    starts = {}
    cursor = 5 * MINUTES_PER_HOUR  # 晨自 05:00 起
    for name in WATCH_ORDER:
        starts[name] = cursor % MINUTES_PER_DAY
        cursor += WATCH_SPANS[name]
    return starts


# 每段起始分钟：{"dawn": 300, "day": 540, "dusk": 1020, "night": 1260}
WATCH_START_MINUTES = _watch_start_minutes()

# §2.5 世界特有的时间流速：第一版**只预留字段、不实现**域间并行换算（归 M11）。
# 权威世界时钟仍只有一条轴；各世界/各域的时间流速差异在此收敛，不产生第二条可写时间线。
RATE_DOMAINS = {}

__all__ = (
    "SECONDS_PER_MINUTE",
    "MINUTES_PER_HOUR",
    "MINUTES_PER_DAY",
    "WATCH_SPANS",
    "WATCH_ORDER",
    "WATCH_START_MINUTES",
    "NOMINAL_WATCH_MINUTES",
    "MINOR_ACTION_MINUTES",
    "MAJOR_ACTION_MINUTES",
    "SESSION_WATCHES",
    "RATE_DOMAINS",
    "watch_of",
    "to_calendar",
    "from_calendar",
    "advance_clock",
)


# --------------------------------------------------------------------------
# 输入校验（纯函数，显式拒绝非法输入，不静默）
# --------------------------------------------------------------------------

def _require_int(value, name):
    """要求 value 是 int（bool 不算；bool 是 int 的子类，属常见陷阱）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("%s 必须是 int，得到 %s" % (name, type(value).__name__))
    return value


def _require_non_negative(value, name):
    """要求 value 是 ≥ 0 的整数分钟。"""
    _require_int(value, name)
    if value < 0:
        raise ValueError("%s 必须 ≥ 0（世界时间只增不减），得到 %d" % (name, value))
    return value


# --------------------------------------------------------------------------
# §3.1 权威世界时钟（World Clock）
# --------------------------------------------------------------------------

def watch_of(minute):
    """返回 `minute` 所处的时段（晨 / 昼 / 昏 / 夜），按 WATCH_SPANS 的**实际边界**判定。

    `minute` 为世界分钟（≥ 0）；内部取当日分钟 `minute % MINUTES_PER_DAY`，
    因此传入当日分钟（[0, 1440)）结果相同。**边界取自 05:00 / 09:00 / 17:00 / 21:00**，
    四段不等长（240 / 480 / 240 / 480），**不能用 `tod % 360` 之类的整除**。
    """
    tod = _require_non_negative(minute, "minute") % MINUTES_PER_DAY
    watch = "night"  # [00:00, 05:00) 属夜：夜自 21:00 跨夜至次日 05:00
    for name in WATCH_ORDER:
        if tod >= WATCH_START_MINUTES[name]:
            watch = name
    return watch


def to_calendar(minute):
    """把世界分钟展开为日历字段（§3.1）。

    返回 dict：

    - `world_minute`：权威值，原样回填。
    - `day`：`minute // 1440 + 1`（第 1 天起，D ≥ 1）。
    - `tod`：当日分钟 `minute % 1440`。
    - `hh` / `mm`：`tod // 60` 与 `tod % 60`。
    - `watch`：`watch_of(minute)`，由实际边界判定。

    其中 `day` / `tod` / `hh` / `mm` / `watch` 均为**派生只读**，任何判断只认
    `world_minute`（§3.1）。
    """
    world_minute = _require_non_negative(minute, "minute")
    day = world_minute // MINUTES_PER_DAY + 1
    tod = world_minute % MINUTES_PER_DAY
    return {
        "world_minute": world_minute,
        "day": day,
        "tod": tod,
        "hh": tod // MINUTES_PER_HOUR,
        "mm": tod % MINUTES_PER_HOUR,
        "watch": watch_of(world_minute),
    }


def from_calendar(day, hh, mm):
    """`to_calendar` 的逆：由（第几天, 时, 分）还原世界分钟（§3.1）。

    `day ≥ 1`（第 1 天 = 0 分钟起）；`0 ≤ hh ≤ 23`；`0 ≤ mm ≤ 59`。
    与 `to_calendar` 构成往返全等（同一次转换内 `day/hh/mm` 互逆）。
    """
    _require_int(day, "day")
    _require_int(hh, "hh")
    _require_int(mm, "mm")
    if day < 1:
        raise ValueError("day 必须 ≥ 1（战役自第 1 天起），得到 %d" % day)
    if not 0 <= hh <= 23:
        raise ValueError("hh 必须在 0–23，得到 %d" % hh)
    if not 0 <= mm <= 59:
        raise ValueError("mm 必须在 0–59，得到 %d" % mm)
    return (day - 1) * MINUTES_PER_DAY + hh * MINUTES_PER_HOUR + mm


def advance_clock(minute, delta):
    """向前推进 `delta` 分钟，返回新的世界分钟（§3.2 单调性）。

    - `minute` 为当前世界分钟（≥ 0）。
    - `delta` 为增量，必须为 **≥ 0** 的整数；**负 delta（回拨）被显式拒绝**
      （时间压缩是「向前推」，不是「回退」）。
    - 返回值恒 **≥** `minute`（单调不减）；`delta == 0` 允许（原地不动）。
    """
    start = _require_non_negative(minute, "minute")
    _require_int(delta, "delta")
    if delta < 0:
        raise ValueError("delta 必须 ≥ 0：世界时间只增不减，禁止回拨（得到 %d）" % delta)
    result = start + delta
    assert result >= start, "单调性被破坏：世界时间不得回退"
    return result
