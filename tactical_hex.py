#!/usr/bin/env python3
"""六角格几何内核（Issue #417）。

纯几何：axial / cube 坐标换算、六角距离、六邻格与确定性 hex-line 遍历。
本模块不读写文件、不访问网络、不 import 任何业务模块，也不含任何规则阈值——
六邻偏移与距离公式的唯一数值来源是 `data/system/tactical_space.json`
（`NEIGHBOR_OFFSETS` 只是几何恒等式，`tests/test_tactical_hex.py` 断言它与该文件完全一致）。

坐标约定（`docs/system/02c-hex-battlefield.md` 第一节）：

- axial：`(q, r)`，两个整数；
- cube：`(x, y, z)`，`x = q`、`z = r`、`y = -x - z`，恒有 `x + y + z == 0`。

hex-line 实现依据 02C:123 起的 6 条冻结规则（Issue #433）：采样全程用
`fractions.Fraction` 做有理数运算，不出现浮点，所以同一输入在任何平台、任何
渲染朝向下都得到同一序列。
"""
from fractions import Fraction
from math import ceil, floor

# 六邻偏移（02C:18，与 system.tactical_space.geometry.neighbor_offsets 同序同值）。
NEIGHBOR_OFFSETS = (
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, 0),
    (-1, 1),
    (0, 1),
)

__all__ = [
    "NEIGHBOR_OFFSETS",
    "axial",
    "is_valid_axial",
    "axial_to_cube",
    "is_valid_cube",
    "cube_to_axial",
    "hex_distance",
    "neighbors",
    "hex_line",
]


def _is_int(value):
    """整数判定；bool 是 int 的子类，但坐标不接受 True / False。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _as_pair(value):
    """把轴向输入摊平成二元组；不接受其它长度。"""
    if isinstance(value, dict):
        return (value.get("q"), value.get("r")) if "q" in value and "r" in value else None
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return (value[0], value[1])
    return None


def _as_triple(value):
    """把立方输入摊平成三元组；不接受其它长度。"""
    if isinstance(value, dict):
        return (value.get("x"), value.get("y"), value.get("z"))
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return (value[0], value[1], value[2])
    return None


def is_valid_axial(value):
    """`(q, r)` 是否是合法轴向坐标（两个整数；也接受 `{"q": …, "r": …}`）。"""
    pair = _as_pair(value)
    return pair is not None and _is_int(pair[0]) and _is_int(pair[1])


def axial(value):
    """归一化为轴向二元组；非法坐标抛 `ValueError`，不做任何隐式取整。"""
    pair = _as_pair(value)
    if pair is None or not is_valid_axial(pair):
        raise ValueError("非法轴向坐标（需要两个整数 q / r）: %r" % (value,))
    return pair


def is_valid_cube(value):
    """`(x, y, z)` 是否是合法立方坐标（三个整数且和为 0）。"""
    triple = _as_triple(value)
    if triple is None or not all(_is_int(item) for item in triple):
        return False
    return triple[0] + triple[1] + triple[2] == 0


def axial_to_cube(value):
    """轴向 → 立方：`x = q`、`z = r`、`y = -x - z`（02C:125）。"""
    q, r = axial(value)
    return (q, -q - r, r)


def cube_to_axial(value):
    """立方 → 轴向：`(q, r) = (x, z)`；和不为 0 的立方坐标直接拒绝。"""
    if not is_valid_cube(value):
        raise ValueError("非法立方坐标（需要三个整数且和为 0）: %r" % (value,))
    triple = _as_triple(value)
    return (triple[0], triple[2])


def hex_distance(first, second):
    """六角距离：`max(|dq|, |dr|, |dq+dr|)`（02C:19），与渲染朝向无关。"""
    a = axial(first)
    b = axial(second)
    dq = b[0] - a[0]
    dr = b[1] - a[1]
    return max(abs(dq), abs(dr), abs(dq + dr))


def neighbors(value):
    """六邻格，按 `NEIGHBOR_OFFSETS` 的固定顺序返回（确定性，不随哈希序变化）。"""
    q, r = axial(value)
    return tuple((q + dq, r + dr) for dq, dr in NEIGHBOR_OFFSETS)


def hex_line(start, end):
    """起点 A 到终点 B 的确定性轴向格序列（含 A、B，长度 = 六角距离 + 1）。

    严格按 02C:123 的 6 条执行（见 `_round_cube` 对第 4、5 条的落地方式）。
    文档里的并列例子：

    >>> hex_line((0, 0), (2, -1))
    ((0, 0), (1, -1), (2, -1))

    `N = 2`，`i = 1` 的采样点 `(1, -1/2, -1/2)` 落在两格共边上，候选 axial
    `(1, 0)` / `(1, -1)` 等距；按第 5 条比较 cube `(x, z)`：`(1, -1) < (1, 0)`，
    故选 `(1, -1)`。反向 `B → A` 的同一采样点仍选中该格，因此反向序列是正向序列的倒序。
    """
    a = axial(start)
    b = axial(end)
    ax, ay, az = axial_to_cube(a)
    bx, by, bz = axial_to_cube(b)
    dx = bx - ax
    dy = by - ay
    dz = bz - az
    steps = max(abs(dx), abs(dy), abs(dz))
    if steps == 0:
        return (a,)
    line = []
    for index in range(steps + 1):
        # 有理数采样（02C:127）：t = i / N，三个分量都用 Fraction 精确表示。
        ratio = Fraction(index, steps)
        sample = (
            Fraction(ax) + ratio * dx,
            Fraction(ay) + ratio * dy,
            Fraction(az) + ratio * dz,
        )
        line.append(cube_to_axial(_round_cube(*sample)))
    return tuple(line)


def _round_cube(sx, sy, sz):
    """把有理数采样点取整为最近的合法立方坐标（02C:128 与 02C:129）。

    第 4 条「三个分量分别取整；若和不为 0，把取整误差最大的分量重算为 `-x - z`」
    等价于「在 `x + y + z == 0` 的整数点里取与采样点距离最近者」——误差最大分量
    正是修正后能最小化误差的那一个。这里直接枚举各分量 floor / ceil 的组合并
    按 `Fraction` 精确比较平方误差，一次落地第 4 条；第 5 条再规定并列（采样点
    落在两格共边 / 共角、两个候选等距）时取 cube `(x, z)` 字典序更小的格，
    所以排序键是 `(误差, x, z, y)`：`x` 与 `z` 对应第 5 条的字典序，`y` 只是
    兜底以保证结果唯一。
    """
    candidates = [
        (x, y, z)
        for x in sorted({floor(sx), ceil(sx)})
        for y in sorted({floor(sy), ceil(sy)})
        for z in sorted({floor(sz), ceil(sz)})
        if x + y + z == 0
    ]
    if not candidates:  # 不可达：A、B 的立方和均为 0，采样点也必为 0
        raise ValueError("采样点无法取整为合法立方坐标: %r" % ((sx, sy, sz),))
    return min(
        candidates,
        key=lambda cell: (
            (sx - cell[0]) ** 2 + (sy - cell[1]) ** 2 + (sz - cell[2]) ** 2,
            cell[0],
            cell[2],
            cell[1],
        ),
    )
