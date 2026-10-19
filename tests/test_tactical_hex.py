#!/usr/bin/env python3
"""六角格几何内核回归（Issue #417）。

覆盖：
- 六邻偏移与 `data/system/tactical_space.json` 完全一致（防两处漂移）；
- 六邻对称、距离公式（原点 / 负坐标 / 跨原点 / 同格 = 0）；
- `hex_line` 的文档并列例子 `A=(0,0) → B=(2,-1)`（`i=1` 必须选 `(1,-1)`）、反向、
  共角并列、负坐标、长度 = 距离 + 1、相邻格恒相邻、同输入同输出；
- 坐标合法性：只接受整数（`bool` / 浮点 / 字符串一律拒绝），cube 和必须为 0；
- 几何内核里没有规则阈值（150 / 100 / 50 / 300 / 1d6）。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_tactical_hex.py` 运行。
"""
import json
import os
import re
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)

import tactical_hex  # noqa: E402

SPACE_RULES = os.path.join(ROOT, "data", "system", "tactical_space.json")
HEX_MODULE = os.path.join(ROOT, "tactical_hex.py")


def check_neighbor_offsets_match_space_rules():
    """模块常量与 system.tactical_space.geometry.neighbor_offsets 同序同值。"""
    with open(SPACE_RULES, encoding="utf-8") as handle:
        space = json.load(handle)
    expected = tuple((item["q"], item["r"])
                     for item in space["geometry"]["neighbor_offsets"])
    assert tactical_hex.NEIGHBOR_OFFSETS == expected, \
        "NEIGHBOR_OFFSETS 与 tactical_space.json 漂移: %r != %r" % (
            tactical_hex.NEIGHBOR_OFFSETS, expected)


def check_neighbors_are_six_symmetric_singletons():
    """六邻：6 个互不相同、距离恒为 1、关系对称。"""
    problems = []
    for origin in ((0, 0), (-2, 1), (3, -3), (1, -1)):
        found = tactical_hex.neighbors(origin)
        if len(found) != 6 or len(set(found)) != 6:
            problems.append("%r 的邻格不是 6 个互异格: %r" % (origin, found))
        for cell in found:
            if tactical_hex.hex_distance(origin, cell) != 1:
                problems.append("%r → %r 距离不是 1" % (origin, cell))
            if origin not in tactical_hex.neighbors(cell):
                problems.append("%r → %r 不对称" % (origin, cell))
    assert not problems, "; ".join(problems)


def check_distance_cases():
    """距离公式：同格 0、轴向 1、跨原点、负坐标、对称。"""
    cases = (
        ((0, 0), (0, 0), 0),
        ((2, -1), (2, -1), 0),
        ((0, 0), (1, 0), 1),
        ((0, 0), (0, -1), 1),
        ((0, 0), (-1, 1), 1),
        ((0, 0), (2, -1), 2),
        ((0, 0), (-2, 1), 2),
        ((2, -1), (-2, 1), 4),
        ((-3, 2), (1, -1), 4),
        ((1, -1), (0, 0), 1),
    )
    problems = []
    for first, second, expected in cases:
        actual = tactical_hex.hex_distance(first, second)
        if actual != expected:
            problems.append("distance%r = %d（期望 %d）" % ((first, second), actual, expected))
        back = tactical_hex.hex_distance(second, first)
        if back != expected:
            problems.append("distance%r 不对称（反向 %d）" % ((second, first), back))
    assert not problems, "; ".join(problems)


def check_hex_line_documented_tie_break():
    """文档例子：A=(0,0) → B=(2,-1)，N=2，i=1 必须选 (1,-1) 而不是 (1,0)。"""
    line = tactical_hex.hex_line((0, 0), (2, -1))
    assert tactical_hex.hex_distance((0, 0), (2, -1)) == 2, "文档例子的 N 应为 2"
    assert line == ((0, 0), (1, -1), (2, -1)), \
        "hex_line((0,0), (2,-1)) 应为 ((0,0), (1,-1), (2,-1))，实为 %r" % (line,)
    assert line[1] == (1, -1), "共边并列必须取 cube (x, z) 字典序更小的 (1,-1)"


def check_hex_line_reverse_is_mirror():
    """反向 B → A 是正向序列的倒序：并列裁决与行进方向无关。"""
    forward = tactical_hex.hex_line((0, 0), (2, -1))
    backward = tactical_hex.hex_line((2, -1), (0, 0))
    assert backward == tuple(reversed(forward)), \
        "反向序列不是倒序: %r vs %r" % (backward, forward)
    assert backward[1] == (1, -1), "反向的共边并列仍应取 (1,-1)"


def check_hex_line_corner_tie():
    """共角并列：(0,0) → (1,1) 的 i=1 候选 (0,1) / (1,0)，取 (x,z) 更小的 (0,1)。"""
    line = tactical_hex.hex_line((0, 0), (1, 1))
    assert line == ((0, 0), (0, 1), (1, 1)), "共角并列取错了格: %r" % (line,)


def check_hex_line_negative_coordinates():
    """负坐标 / 跨原点同样按同一规则，且与正坐标平移一致。"""
    cases = (
        ((-2, 1), (1, -1), ((-2, 1), (-1, 0), (0, 0), (1, -1))),
        ((-3, 2), (0, 0), ((-3, 2), (-2, 1), (-1, 1), (0, 0))),
        ((0, 0), (-2, 2), ((0, 0), (-1, 1), (-2, 2))),
    )
    problems = []
    for first, second, expected in cases:
        actual = tactical_hex.hex_line(first, second)
        if actual != expected:
            problems.append("%r → %r: %r（期望 %r）" % (first, second, actual, expected))
    assert not problems, "; ".join(problems)


def check_hex_line_invariants():
    """同一批坐标：长度 = 距离 + 1、端点正确、相邻必相邻、整数、可复现、反向为倒序。"""
    coords = [(q, r) for q in range(-2, 3) for r in range(-2, 3)]
    problems = []
    for start in coords:
        for end in coords:
            line = tactical_hex.hex_line(start, end)
            distance = tactical_hex.hex_distance(start, end)
            if len(line) != distance + 1:
                problems.append("%r → %r 长度 %d != 距离 %d + 1"
                                % (start, end, len(line), distance))
                continue
            if line[0] != start or line[-1] != end:
                problems.append("%r → %r 端点错误: %r" % (start, end, line))
            for cell in line:
                if not tactical_hex.is_valid_axial(cell) or not all(type(v) is int for v in cell):
                    problems.append("%r → %r 出现非整数格 %r" % (start, end, cell))
            for left, right in zip(line, line[1:]):
                if tactical_hex.hex_distance(left, right) != 1:
                    problems.append("%r → %r 出现不相邻步 %r → %r" % (start, end, left, right))
            if tactical_hex.hex_line(start, end) != line:
                problems.append("%r → %r 两次调用结果不同" % (start, end))
            if tactical_hex.hex_line(end, start) != tuple(reversed(line)):
                problems.append("%r → %r 反向不是倒序" % (start, end))
    assert not problems, "; ".join(problems[:5])


def check_axial_cube_conversion():
    """axial ↔ cube：x=q、z=r、y=-x-z；非法输入一律拒绝。"""
    assert tactical_hex.axial_to_cube((2, -1)) == (2, -1, -1), "cube 转换公式不符（02C:125）"
    assert tactical_hex.axial_to_cube({"q": 0, "r": 0}) == (0, 0, 0)
    assert tactical_hex.cube_to_axial((2, -1, -1)) == (2, -1)
    for q in range(-3, 4):
        for r in range(-3, 4):
            cube = tactical_hex.axial_to_cube((q, r))
            assert cube[0] + cube[1] + cube[2] == 0, "cube 和不为 0: %r" % (cube,)
            assert tactical_hex.is_valid_cube(cube)
            assert tactical_hex.cube_to_axial(cube) == (q, r), "往返不一致: %r" % ((q, r),)


def check_coordinate_validation():
    """坐标合法性：只接受整数；bool / 浮点 / 字符串 / 长度不对一律非法。"""
    valid = ((0, 0), (-3, 2), (4, -4), {"q": 1, "r": -1}, [2, 3])
    invalid = ((0,), (0, 0, 0), (1.0, 0), (0, 0.5), (True, 0), ("1", 0), (None, 0),
               "01", {"q": 1}, {"q": 1, "r": "1"})
    for value in valid:
        assert tactical_hex.is_valid_axial(value), "合法坐标被判非法: %r" % (value,)
    for value in invalid:
        assert not tactical_hex.is_valid_axial(value), "非法坐标被判合法: %r" % (value,)
        try:
            tactical_hex.hex_distance((0, 0), value)
        except ValueError:
            pass
        else:
            raise AssertionError("hex_distance 未拒绝非法坐标: %r" % (value,))

    assert tactical_hex.is_valid_cube((0, 0, 0))
    for bad in ((1, 1, 1), (0, 0, 1), (1.5, -1.5, 0), (0, 0), (True, -1, 0)):
        assert not tactical_hex.is_valid_cube(bad), "非法 cube 被判合法: %r" % (bad,)
    try:
        tactical_hex.cube_to_axial((1, 1, 1))
    except ValueError:
        pass
    else:
        raise AssertionError("cube_to_axial 未拒绝和不为 0 的坐标")


def check_no_rule_thresholds_in_module():
    """几何内核不得硬编码规则阈值（唯一来源是 tactical_space.json）。"""
    with open(HEX_MODULE, encoding="utf-8") as handle:
        text = handle.read()
    hits = [token for token in ("150", "100", "50", "300", "1d6")
            if re.search(r"(?<![0-9])%s(?![0-9])" % re.escape(token), text)]
    assert not hits, "tactical_hex.py 出现硬编码阈值: %s" % ", ".join(hits)


def main():
    checks = (
        check_neighbor_offsets_match_space_rules,
        check_neighbors_are_six_symmetric_singletons,
        check_distance_cases,
        check_hex_line_documented_tie_break,
        check_hex_line_reverse_is_mirror,
        check_hex_line_corner_tie,
        check_hex_line_negative_coordinates,
        check_hex_line_invariants,
        check_axial_cube_conversion,
        check_coordinate_validation,
        check_no_rule_thresholds_in_module,
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
