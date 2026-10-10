#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chron 内核（M9-C1）回归测试：世界时钟与时间单位（纯函数）。

覆盖 `CHRON-DESIGN.md` 切片 C1 的验收标准：

- `watch_of` 在 05:00 / 09:00 / 17:00 / 21:00 边界正确（晨 / 昼 / 昏 / 夜**不等长**）。
- 分钟 ↔ 日历**往返全等**；负 delta / 回拨被**显式拒绝**。
- `NOMINAL_WATCH_MINUTES == 360` 与 `MINOR_ACTION_MINUTES == 10`
  与 `data/system/watch.json` / `data/system/travel.json` 值**一致**。
- `chron.py` 纯函数：不 import 任何模块、不碰 IO（静态审查 + 行为断言）。

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
