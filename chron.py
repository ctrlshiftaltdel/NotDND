#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chron —— CHRON 内核（M9-C1–C4）：世界时钟、行动生命周期、调度器与时间压缩（纯函数）。

本模块实现 `CHRON-DESIGN.md` 的**纯函数**部分：

- 分钟常数（§2.4）：`SECONDS_PER_MINUTE` / `MINUTES_PER_HOUR` / `MINUTES_PER_DAY` /
  `WATCH_SPANS` / `NOMINAL_WATCH_MINUTES` / `MINOR_ACTION_MINUTES` /
  `MAJOR_ACTION_MINUTES` / `SESSION_WATCHES`。
- 世界时钟（§3.1–§3.3）：`watch_of` / `to_calendar` / `from_calendar` / `advance_clock`。
- §2.5 的 `RATE_DOMAINS` 第一版**只预留字段、不实现**域间流速换算（归 M11）。
- 行动生命周期（§4）：`Action` 数据结构（`ACTION_FIELDS` + `new_action`）、八状态
  （`ACTION_STATUSES`）、**全转换表**（`ACTION_TRANSITIONS`，一张显式表）、
  `transition`（每次转换产出**一条**事件；终态不可逆；非法转换**显式抛错**）、
  `apply_event`（把事件的 `changes` 落回行动卡）。`time_cost` 由外部传入，**不调 PRISM**。
- 调度器（§5）：`new_scheduler` / `schedule` / `rebuild_heap` / `advance_to`、
  `due_heap`（**稳定**排序键 `(due_minute, -priority, created_seq, action_id)`，§5.2）、
  依赖图（**无环**是硬约束，有环提案 `proposed → failed`；默认**级联取消**，§5.3）、
  `Window` 与冲突识别（同 `place` / 同 `resource` / `blocks`；只**识别**、**不裁决**，§5.4–§5.5）。
- 时间压缩与事件屏障（§6）：`BARRIERS`（**B1–B8 机读判据**一张表）、`next_barrier`
  （下一个必须停的分钟）、`barriers_at`（该分钟上的屏障记录）、`advance_to` 的**屏障语义**
  （停在屏障之前 / 到期是结算点）、`compression_log`（**只记真实事件**，不产生「被跳过」的模拟事件）。

**本单边界**：不判行动胜负（归 PRISM）、不关窗口（`window.closed` 归后续切片）、
不判「谁在线」（`offline_advance` 只作入参回显，开关落 M10，§3.4）、不落库、不碰 IO；
不 import 任何业务模块（§7 依赖方向）。
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
    # §4 行动生命周期（C2）
    "ChronError",
    "TransitionError",
    "ACTION_STATUSES",
    "ACTION_TERMINAL_STATUSES",
    "ACTION_TRIGGERS",
    "ACTION_FIELDS",
    "ACTION_TRANSITIONS",
    "new_action",
    "transition",
    "apply_event",
    # §4.1 依赖失败语义（§5.3）
    "DEFAULT_ON_DEP_FAIL",
    "ON_DEP_FAIL_POLICIES",
    # §5 调度器（C3）
    "ScheduleError",
    "DUE_KEY_FIELDS",
    "DUE_IDEMPOTENCY_RE_TEXT",
    "WINDOW_FIELDS",
    "WINDOW_OPEN_EVENT",
    "WINDOW_CLOSE_EVENT",
    "ACTION_DUE_EVENT",
    "SCHEDULER_FIELDS",
    "new_scheduler",
    "schedule",
    "settle",
    "cascade_dependents",
    "advance_to",
    "rebuild_heap",
    "sort_key",
    "created_seq",
    "deps_satisfied",
    "open_windows",
    "windows_at",
    "conflict_key",
    "blocked_by",
    "blocks_of",
    "dependents_of",
    # §6 时间压缩与事件屏障（C4）
    "BARRIERS",
    "BARRIER_IDS",
    "BARRIER_FIELDS",
    "KIND_BARRIERS",
    "INTERRUPT_BARRIERS",
    "NOW_BARRIER_IDS",
    "PAUSING_BARRIER_IDS",
    "barriers_at",
    "next_barrier",
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


# --------------------------------------------------------------------------
# §4 行动生命周期（Action Lifecycle）——Action 数据结构与八状态
# --------------------------------------------------------------------------
#
# §4.1 的 `Action` 用**纯 dict** 表达：可 JSON 落盘（§8.1 `blocks.chron`）、
# 无 import、无 IO。键集合由 `ACTION_FIELDS` 钉死；`new_action()` 是**新行动**的唯一
# 构造入口（落盘恢复（C5）按同一键集合重建即可，`_check_action()` 负责形状校验）。
#
# §4.2 的**全转换表**是本模块对该生命周期的单一真相：`ACTION_TRANSITIONS`
# 一行一转换（从 / 到 / 触发条件 / 谁 / 产出事件 / M8 同步路径是否支持 / 生效字段更新）。
# `transition()` 只做「查表 + 校验 + 产事件」，**不把转换规则散在 if 里**。

ACTION_STATUSES = (
    "proposed",     # 已构造提案，尚未排程（M8 同步路径可直接进 running）
    "scheduled",    # 已排入时间线（M9 异步路径）
    "running",      # 已开始
    "blocked",      # 前置条件不满足（可恢复）
    "interrupted",  # 被中断事件打断
    "completed",    # 终态：结算成功
    "cancelled",    # 终态：撤回 / 放弃
    "failed",       # 终态：结算失败 / 校验不通过
)

# §4.2 末行：**终态不可逆**——「后续计划」是**新行动**，不是原行动的新状态。
# 全转换表里不存在任何以终态为 `from` 的行（模块加载时自检，测试亦钉死）。
ACTION_TERMINAL_STATUSES = ("completed", "failed", "cancelled")

# §4.2「谁触发」列的行内取值：P=玩家/NPC，S=调度器，R=PRISM，T=状态层。
ACTION_TRIGGERS = ("P", "S", "R", "T")

# §4.1 的行动卡字段。前 15 个与 §4.1 草案逐字对应；末尾五个是「中断 / 恢复」（§4.4）与
# 「调度器」（§5.3–§5.5）落地所需的最小补充（§4.1 未列，但后续语义必须有落点）：
#   - `interrupt_minute`：**开始后**被打断的世界分钟（未开始即 `None`）；
#   - `progress_kept`：中断时进度是否保留（§4.4；`None` = 尚未中断过）；
#   - `on_dep_fail`：依赖未成时本卡怎么办（§5.3；默认 `cancel` = 级联取消，`keep` = 转 blocked）；
#   - `place` / `resource`：行动发生的地点与占用的资源（§5.4–§5.5 的冲突识别键；
#     `None` = 与地点 / 资源无关，不参与冲突识别。**判定**归规则包与空间层，CHRON 只读）。
ACTION_FIELDS = (
    "action_id",            # 唯一 ID（提交方生成，服务端校验唯一）
    "actor",                # 发起者（玩家角色 / NPC 的稳定 ID）
    "kind",                 # 行动种类（决定走哪条 PRISM 规则）
    "status",               # 八状态之一；`None` = 尚未提议（草稿）
    "created_minute",       # 提议时的世界时间（`proposed` 转换写下）
    "start_minute",         # 安排开始（`scheduled` 后有值）
    "due_minute",           # 预计结束（= start_minute + time_cost）
    "end_minute",           # 实际结束（结算 / 取消时写）
    "time_cost",            # 基础耗时（分钟；**PRISM 给**，CHRON 只读）
    "priority",             # 排序用（越大越先；默认 0，规则包可给）
    "depends_on",           # 依赖：这些行动完成前不得开始
    "blocks",               # 反向索引（由 depends_on 生成；C3 调度器维护）
    "interrupt_conditions",  # 中断条件（§5.4）
    "idempotency_key",      # 提交时传给状态层的幂等键（§8.3）
    "events",               # 该行动产出的可追溯事件 id（每次转换追加一条）
    "interrupt_minute",     # 【实现补充】开始后被中断的世界分钟（§4.4）
    "progress_kept",        # 【实现补充】中断时进度是否保留（§4.4）
    "on_dep_fail",          # 【实现补充】依赖未成：cancel（默认级联取消）/ keep（转 blocked）（§5.3）
    "place",                # 【实现补充】地点（§5.5 冲突识别键；None = 无关）
    "resource",             # 【实现补充】资源（§5.5 冲突识别键；None = 无关）
)

# §5.3：依赖的行动 `failed` / `cancelled` 时，默认**级联取消**（事件带 `reason`）；
# 规则包标 `on_dep_fail = "keep"` 的卡改判 `blocked`。
DEFAULT_ON_DEP_FAIL = "cancel"
ON_DEP_FAIL_POLICIES = ("cancel", "keep")


class ChronError(Exception):
    """CHRON 语义错误基类（纯函数层，不依赖任何业务模块）。"""


class TransitionError(ChronError):
    """非法状态转换（§4.2）：**显式抛错**，绝不静默忽略、绝不部分生效。"""


def _require_str(value, name):
    """要求非空字符串（类型错 → TypeError；空串 → ValueError）。"""
    if not isinstance(value, str):
        raise TypeError("%s 必须是字符串，得到 %s" % (name, type(value).__name__))
    if not value:
        raise ValueError("%s 不能为空字符串" % name)
    return value


def _require_optional_str(value, name):
    """要求 `None` 或非空字符串（§4.1 的 `place` / `resource` 可为空 = 与该维度无关）。"""
    if value is None:
        return None
    return _require_str(value, name)


def _require_choice(value, name, allowed):
    """要求取值落在 `allowed` 里（枚举显式拒绝，不静默取默认）。"""
    if value not in allowed:
        raise ValueError("%s 必须是 %s 之一，得到 %r" % (name, " / ".join(allowed), value))
    return value


def _require_bool(value, name):
    """要求 bool（bool 之外的任何类型都拒绝，含 0/1）。"""
    if not isinstance(value, bool):
        raise TypeError("%s 必须是 bool，得到 %s" % (name, type(value).__name__))
    return value


def _require_list(value, name):
    """要求 list / tuple，返回新的 list（不改写调用方的 list）。"""
    if not isinstance(value, (list, tuple)):
        raise TypeError("%s 必须是 list / tuple，得到 %s" % (name, type(value).__name__))
    return list(value)


def _require_str_list(value, name):
    """要求「非空字符串」组成的 list / tuple，返回新的 list。"""
    items = _require_list(value, name)
    for item in items:
        _require_str(item, "%s[]" % name)
    return items


def _require_status(value, name):
    """要求八状态之一，或 `None`（草稿：尚未提议）。"""
    if value is None:
        return None
    if value not in ACTION_STATUSES:
        raise ValueError("%s 必须是八状态之一或 None，得到 %r（可选：%s）"
                         % (name, value, " / ".join(ACTION_STATUSES)))
    return value


def _check_action(action):
    """校验行动卡形状与状态；返回原对象（只读，不改写）。"""
    if not isinstance(action, dict):
        raise TypeError("action 必须是 dict（用 new_action 构造），得到 %s"
                        % type(action).__name__)
    missing = [field for field in ACTION_FIELDS if field not in action]
    if missing:
        raise ValueError("Action 缺少字段：%s（用 new_action 构造）" % ", ".join(missing))
    _require_status(action["status"], "action.status")
    return action


def _copy_action(action):
    """浅拷贝行动卡，列表字段复制一层——`transition` / `apply_event` 不改写入参。"""
    copied = {}
    for field in ACTION_FIELDS:
        value = action[field]
        copied[field] = list(value) if isinstance(value, list) else value
    return copied


def _status_label(status):
    """状态的可读标签（`None` = 尚未提议）。"""
    return "（未提议）" if status is None else status


def _allowed_targets(status):
    """从某状态出发、全转换表允许的目标（按表内顺序；用于错误信息与自检）。"""
    return tuple(row["to"] for row in ACTION_TRANSITIONS if row["from"] == status)


# --------------------------------------------------------------------------
# §4 行动生命周期——「转换效果」：每一行只更新它该更新的字段
# --------------------------------------------------------------------------
#
# 效果函数签名统一为 `(action, ctx, world_minute) -> {字段: 新值}`：
# 它们**不改写**行动卡，只声明本次转换的字段更新；`transition()` 把更新
# 编成事件的 `changes`（STATE-DESIGN §5.3 形状），`apply_event()` 再落回行动卡。

def _effect_propose(action, ctx, world_minute):
    """— → proposed：记下提议时刻（§4.1 `created_minute`）。"""
    return {"created_minute": world_minute}


def _effect_schedule(action, ctx, world_minute):
    """proposed / blocked → scheduled：安排开始与预计结束（§4.1 `start_minute` / `due_minute`）。

    `ctx.start_minute` 缺省 = `world_minute`（立即开始）；`due_minute = start_minute + time_cost`，
    其中 `time_cost` **由外部（PRISM）传入**，CHRON 只加不判。
    """
    start = _require_non_negative(ctx.get("start_minute", world_minute), "ctx.start_minute")
    if start < world_minute:
        raise TransitionError("安排开始时间不得早于当前世界时间：start_minute=%d < world_minute=%d"
                              % (start, world_minute))
    return {
        "start_minute": start,
        "due_minute": advance_clock(start, action["time_cost"]),
    }


def _effect_begin(action, ctx, world_minute):
    """proposed → running：M8 同步路径，**不排程、立即开始**（§4.2 末列）。

    `start_minute` 即当前世界分钟；`due_minute` 仍按 `start_minute + time_cost` 记「预计结束」，
    实际结束由结算转换写 `end_minute`。
    """
    return {
        "start_minute": world_minute,
        "due_minute": advance_clock(world_minute, action["time_cost"]),
    }


def _effect_start(action, ctx, world_minute):
    """scheduled → running：世界时间到达 `start_minute`（§4.2）。"""
    start = action["start_minute"]
    if start is None or world_minute < start:
        raise TransitionError("未到安排开始时间：world_minute=%s < start_minute=%s（Action %s）"
                              % (world_minute, start, action["action_id"]))
    return {}


def _effect_block(action, ctx, world_minute):
    """scheduled → blocked：前置条件变为不满足（可恢复，保留 start / due 供审计）。"""
    return {}


def _effect_interrupt_pending(action, ctx, world_minute):
    """scheduled → interrupted：**开始前**中断，未消耗时间（停点留空）。"""
    return {"progress_kept": _require_bool(ctx.get("progress_kept", True), "ctx.progress_kept")}


def _effect_interrupt_running(action, ctx, world_minute):
    """running → interrupted：记停点（§4.4 恢复时据此算剩余耗时）。"""
    return {
        "interrupt_minute": world_minute,
        "progress_kept": _require_bool(ctx.get("progress_kept", True), "ctx.progress_kept"),
    }


def _remaining_minutes(action):
    """恢复重排时还剩多少分钟（§4.4：`progress_kept` 为真则「用剩余耗时重排」）。

    - 进度不保留（`progress_kept is False`）→ 从头开始，整份 `time_cost`；
    - 未实际开始过（`interrupt_minute` 为空）→ 整份 `time_cost`；
    - 否则 = `time_cost` − 已消耗（`interrupt_minute` − `start_minute`，夹在 `[0, time_cost]`）。
    """
    cost = action["time_cost"]
    if action["progress_kept"] is False:
        return cost
    start = action["start_minute"]
    stop = action["interrupt_minute"]
    if start is None or stop is None:
        return cost
    consumed = stop - start
    if consumed <= 0:
        return cost
    if consumed >= cost:
        return 0
    return cost - consumed


def _effect_resume(action, ctx, world_minute):
    """interrupted → scheduled：用**剩余耗时**重排开始（§4.4）。"""
    start = _require_non_negative(ctx.get("start_minute", world_minute), "ctx.start_minute")
    if start < world_minute:
        raise TransitionError("重排开始时间不得早于当前世界时间：start_minute=%d < world_minute=%d"
                              % (start, world_minute))
    return {
        "start_minute": start,
        "due_minute": advance_clock(start, _remaining_minutes(action)),
        "interrupt_minute": None,
    }


def _effect_settle(action, ctx, world_minute):
    """running / interrupted → completed / failed：结算点写实际结束（§4.1 `end_minute`）。"""
    start = action["start_minute"]
    if start is not None and world_minute < start:
        raise TransitionError("结算时间不得早于开始时间：world_minute=%d < start_minute=%d"
                              % (world_minute, start))
    return {"end_minute": world_minute}


def _effect_cancel(action, ctx, world_minute):
    """任意非终态 → cancelled：取消即结束，写实际结束（§4.1 `end_minute`）。"""
    return {"end_minute": world_minute}


# --------------------------------------------------------------------------
# §4.2 全转换表（**单一真相**；`transition()` 只查表）
# --------------------------------------------------------------------------
#
# 每行对应 `CHRON-DESIGN.md` §4.2 表的一行（`interrupted → completed/failed`
# 那一行两目标，拆成两行，`spec` 相同）；`m8_sync` 即 §4.2 末列
# 「M8 同步路径是否支持」。表尾另加一行 **M8 同步补充行**：`proposed → running`
# （基线 §4.3 第 2 条 / Key Decision 5：M8 只走 `proposed → running → completed/failed`，
# 不经 `scheduled`），C2 验收要求该路径**不排程也能走**。
# §4.2 末行「completed / failed / cancelled → —」由 `ACTION_TERMINAL_STATUSES`
# 表达：表中**没有**以终态为 `from` 的行，模块加载时自检、测试逐状态钉死。

ACTION_TRANSITIONS = (
    {
        "from": None,
        "to": "proposed",
        "trigger": "提出行动（NL 解析或直接 API）",
        "who": ("P",),
        "event": "action.proposed",
        "m8_sync": True,
        "effect": _effect_propose,
        "spec": "§4.2 第 1 行",
    },
    {
        "from": "proposed",
        "to": "scheduled",
        "trigger": "校验通过、算出 time_cost、安排开始",
        "who": ("R", "S"),
        "event": "action.scheduled",
        "m8_sync": False,
        "effect": _effect_schedule,
        "spec": "§4.2 第 2 行",
    },
    {
        "from": "proposed",
        "to": "failed",
        "trigger": "校验不通过（条件不成立）",
        "who": ("R",),
        "event": "action.failed",
        "m8_sync": True,
        "effect": _effect_settle,
        "spec": "§4.2 第 3 行",
    },
    {
        "from": "proposed",
        "to": "cancelled",
        "trigger": "玩家撤回 / 世界变化使其失去意义",
        "who": ("P", "T"),
        "event": "action.cancelled",
        "m8_sync": True,
        "effect": _effect_cancel,
        "spec": "§4.2 第 4 行",
    },
    {
        "from": "scheduled",
        "to": "running",
        "trigger": "世界时间到达 start_minute",
        "who": ("S",),
        "event": "action.started",
        "m8_sync": True,     # §4.2 末列「✅（M8：立即）」
        "effect": _effect_start,
        "spec": "§4.2 第 5 行",
    },
    {
        "from": "scheduled",
        "to": "cancelled",
        "trigger": "开始前被取消",
        "who": ("P", "T"),
        "event": "action.cancelled",
        "m8_sync": False,
        "effect": _effect_cancel,
        "spec": "§4.2 第 6 行",
    },
    {
        "from": "scheduled",
        "to": "blocked",
        "trigger": "前置条件变为不满足（可恢复）",
        "who": ("S",),
        "event": "action.blocked",
        "m8_sync": False,
        "effect": _effect_block,
        "spec": "§4.2 第 7 行",
    },
    {
        "from": "scheduled",
        "to": "interrupted",
        "trigger": "开始前中断事件命中",
        "who": ("S",),
        "event": "action.interrupted",
        "m8_sync": False,
        "effect": _effect_interrupt_pending,
        "spec": "§4.2 第 8 行",
    },
    {
        "from": "blocked",
        "to": "scheduled",
        "trigger": "前置条件恢复",
        "who": ("S",),
        "event": "action.unblocked",
        "m8_sync": False,
        "effect": _effect_schedule,   # 被阻塞的卡从未开始 → 重排仍用整份 time_cost
        "spec": "§4.2 第 9 行",
    },
    {
        "from": "blocked",
        "to": "cancelled",
        "trigger": "放弃",
        "who": ("P",),
        "event": "action.cancelled",
        "m8_sync": False,
        "effect": _effect_cancel,
        "spec": "§4.2 第 10 行",
    },
    {
        "from": "running",
        "to": "completed",
        "trigger": "到达结算点，PRISM 判成功",
        "who": ("R",),
        "event": "action.completed",
        "m8_sync": True,
        "effect": _effect_settle,
        "spec": "§4.2 第 11 行",
    },
    {
        "from": "running",
        "to": "failed",
        "trigger": "到达结算点，PRISM 判失败",
        "who": ("R",),
        "event": "action.failed",
        "m8_sync": True,
        "effect": _effect_settle,
        "spec": "§4.2 第 12 行",
    },
    {
        "from": "running",
        "to": "interrupted",
        "trigger": "中断事件命中（GDD §8.5）",
        "who": ("S",),
        "event": "action.interrupted",
        "m8_sync": False,
        "effect": _effect_interrupt_running,
        "spec": "§4.2 第 13 行",
    },
    {
        "from": "running",
        "to": "cancelled",
        "trigger": "玩家中途取消",
        "who": ("P",),
        "event": "action.cancelled",
        "m8_sync": False,
        "effect": _effect_cancel,
        "spec": "§4.2 第 14 行",
    },
    {
        "from": "interrupted",
        "to": "scheduled",
        "trigger": "恢复到剩余进度（重排开始）",
        "who": ("S",),
        "event": "action.resumed",
        "m8_sync": False,
        "effect": _effect_resume,
        "spec": "§4.2 第 15 行",
    },
    {
        "from": "interrupted",
        "to": "completed",
        "trigger": "就地结算已完成的部分（GDD §8.5）",
        "who": ("R",),
        "event": "action.completed",
        "m8_sync": False,
        "effect": _effect_settle,
        "spec": "§4.2 第 16 行（该行两目标之一）",
    },
    {
        "from": "interrupted",
        "to": "failed",
        "trigger": "就地结算已完成的部分（GDD §8.5）",
        "who": ("R",),
        "event": "action.failed",
        "m8_sync": False,
        "effect": _effect_settle,
        "spec": "§4.2 第 16 行（该行两目标之一）",
    },
    {
        "from": "interrupted",
        "to": "cancelled",
        "trigger": "中断后放弃",
        "who": ("P",),
        "event": "action.cancelled",
        "m8_sync": False,
        "effect": _effect_cancel,
        "spec": "§4.2 第 17 行",
    },
    {
        "from": "proposed",
        "to": "running",
        "trigger": "M8 同步路径：校验通过后立即结算（不排程）",
        "who": ("R", "S"),
        "event": "action.started",
        "m8_sync": True,
        "effect": _effect_begin,
        "spec": "§4.2 末列 + 基线 §4.3 第 2 条（M8 同步补充行）",
    },
)

# (from, to) → 行：`transition()` 的查表索引（规则不在 if 里）。
_TRANSITION_INDEX = {(row["from"], row["to"]): row for row in ACTION_TRANSITIONS}

assert len(_TRANSITION_INDEX) == len(ACTION_TRANSITIONS), "全转换表存在重复的 (from, to)"
assert not [row for row in ACTION_TRANSITIONS if row["from"] in ACTION_TERMINAL_STATUSES], \
    "终态不可逆：表中不得有以终态为 from 的行（§4.2 末行）"


def _world_time(world_minute):
    """§3.1 事件封套 `world_time` 的**唯一写法**：`{world_minute, day, tod}`。

    `day` / `tod` 是派生只读（由 `to_calendar` 现算），仅为人类可读；判断只认 `world_minute`。
    """
    calendar = to_calendar(world_minute)
    return {
        "world_minute": calendar["world_minute"],
        "day": calendar["day"],
        "tod": calendar["tod"],
    }


def _event_source(ctx):
    """GDD §19.5「创建来源」：`ctx.source` 优先；否则由触发者推（P → player，其余 → system）。"""
    source = ctx.get("source")
    if source is not None:
        return _require_str(source, "ctx.source")
    return "player" if ctx.get("who") == "P" else "system"


def new_action(action_id, actor, kind, time_cost, *, priority=0, depends_on=(),
               interrupt_conditions=(), idempotency_key=None,
               on_dep_fail=DEFAULT_ON_DEP_FAIL, place=None, resource=None):
    """构造一张**尚未提议**的行动卡草稿（§4.1；`status = None`）。

    提议本身也是一次转换：`transition(draft, "proposed", {"world_minute": ...})`——
    该事件会写下 `created_minute`（= 提议时的世界时间）。
    `blocks` 是 `depends_on` 的反向冗余索引（§4.1），由调度器（C3）维护，此处留空列表。

    `time_cost` 由外部（PRISM）传入，CHRON **只读不判**（§7 边界）。
    `on_dep_fail`（§5.3）默认 `cancel` = 依赖失败时级联取消；`keep` = 改判 `blocked`。
    `place` / `resource` 是 §5.5 的冲突识别键（`None` = 与该维度无关）。
    """
    return {
        "action_id": _require_str(action_id, "action_id"),
        "actor": _require_str(actor, "actor"),
        "kind": _require_str(kind, "kind"),
        "status": None,
        "created_minute": None,
        "start_minute": None,
        "due_minute": None,
        "end_minute": None,
        "time_cost": _require_non_negative(time_cost, "time_cost"),
        "priority": _require_int(priority, "priority"),
        "depends_on": _require_str_list(depends_on, "depends_on"),
        "blocks": [],
        "interrupt_conditions": _require_list(interrupt_conditions, "interrupt_conditions"),
        "idempotency_key": None if idempotency_key is None
                           else _require_str(idempotency_key, "idempotency_key"),
        "events": [],
        "interrupt_minute": None,
        "progress_kept": None,
        "on_dep_fail": _require_choice(on_dep_fail, "on_dep_fail", ON_DEP_FAIL_POLICIES),
        "place": _require_optional_str(place, "place"),
        "resource": _require_optional_str(resource, "resource"),
    }


def transition(action, to, ctx=None):
    """执行一次状态转换，返回**本次转换产出的唯一事件**（§4.2 不变式）。

    - `action`：`new_action` 构造的行动卡；`status is None`（草稿）**只**允许 `to="proposed"`。
    - `to`：目标状态（`ACTION_STATUSES` 之一）。
    - `ctx`：本次转换的上下文（dict，可选字段）：
        - `world_minute`（**必填**）：转换发生时的世界分钟（§3.2 权威时间）；
        - `who`（可选）：触发者 `P` / `S` / `R` / `T`——给了就必须是该行「谁」列之一；
        - `start_minute`（可选）：`scheduled` 的安排开始（缺省 = `world_minute`，立即）；
        - `cause`（可选）：上游事件 `event_id`（GDD §19.5「原因 / 触发事件」；缺省 `None` = 根因）；
        - `reason`（可选）：机器可读原因（如 `dependency_failed`），随事件带出；
        - `progress_kept`（可选，默认 `True`）：中断时进度是否保留（§4.4）；
        - `source`（可选）：GDD §19.5「创建来源」；缺省由 `who` 推。

    **不变式**（§4.2）：

    - 每次转换产出**恰好一条**事件；合法转换由 `ACTION_TRANSITIONS` 查表决定，不散在 `if` 里；
    - **终态不可逆**：`completed` / `failed` / `cancelled` 再转换 → `TransitionError`；
    - **非法转换显式抛错**（`TransitionError`），绝不静默忽略；
    - **不改写入参**：新状态由返回事件的 `changes` 声明，用 `apply_event(action, event)` 落回。

    返回的事件是**提交前草稿**（GDD §19.5 最小字段：`event_id` / `world_time` / `type` /
    `actor` / `cause` / `changes`）：`event_id` 与 `idempotency_key` 是**确定性局域值**
    （`<action_id>:e<序号>`；§8.3 幂等键口径），正式 `event_id = "{campaign_id}:{seq:012d}"`
    与 `visibility` / `rules_version` / `roll_refs` 由状态层提交时补齐（STATE-DESIGN §5.1）。
    """
    _check_action(action)
    if ctx is None:
        ctx = {}
    if not isinstance(ctx, dict):
        raise TypeError("ctx 必须是 dict，得到 %s" % type(ctx).__name__)
    target = _require_str(to, "to")
    if target not in ACTION_STATUSES:
        raise ValueError("to 必须是八状态之一，得到 %r（可选：%s）"
                         % (target, " / ".join(ACTION_STATUSES)))

    current = action["status"]
    if current in ACTION_TERMINAL_STATUSES:
        raise TransitionError(
            "终态不可逆：Action %s 已是 %s，不能再转 %s（§4.2 末行；后续计划请建新行动）"
            % (action["action_id"], current, target))
    rule = _TRANSITION_INDEX.get((current, target))
    if rule is None:
        raise TransitionError(
            "非法转换：%s → %s（Action %s）；从 %s 允许的目标：%s"
            % (_status_label(current), target, action["action_id"], _status_label(current),
               " / ".join(_allowed_targets(current)) or "无（终态）"))

    who = ctx.get("who")
    if who is not None and who not in rule["who"]:
        raise TransitionError(
            "触发者不匹配：%s → %s 的「谁」是 %s，得到 %r"
            % (_status_label(current), target, "/".join(rule["who"]), who))

    if "world_minute" not in ctx:
        raise ValueError("ctx.world_minute 必填：每次转换都锚在权威世界时间上（§3.2）")
    world_minute = _require_non_negative(ctx["world_minute"], "ctx.world_minute")
    created = action["created_minute"]
    if created is not None and world_minute < created:
        raise TransitionError(
            "转换时间不得早于提议时间：world_minute=%d < created_minute=%d（Action %s）"
            % (world_minute, created, action["action_id"]))

    updates = rule["effect"](action, ctx, world_minute)

    ordinal = len(action["events"]) + 1
    event_id = "%s:e%06d" % (action["action_id"], ordinal)
    changes = [{
        "block": "chron",
        "path": "/actions/%s/status" % action["action_id"],
        "op": "set",
        "before": current,
        "after": target,
    }]
    for field in updates:
        changes.append({
            "block": "chron",
            "path": "/actions/%s/%s" % (action["action_id"], field),
            "op": "set",
            "before": action[field],
            "after": updates[field],
        })
    changes.append({
        "block": "chron",
        "path": "/actions/%s/events" % action["action_id"],
        "op": "append",
        "before": None,
        "after": event_id,
    })

    return {
        "event_id": event_id,
        "world_time": _world_time(world_minute),
        "type": rule["event"],
        "actor": action["actor"],
        "target": action["action_id"],
        "cause": ctx.get("cause"),
        "changes": changes,
        "source": _event_source(ctx),
        "reason": ctx.get("reason"),
        "idempotency_key": "%s:%d:%s" % (action["action_id"], ordinal, rule["event"]),
    }


def apply_event(action, event):
    """把事件声明的状态变化（`changes`）落回行动卡，返回**新的** Action。

    - 只接受 CHRON 自己产出的变更形状：`block == "chron"`、
      `path` 形如 `/actions/<action_id>/<字段>`、`op ∈ {"set", "append"}`；
    - `set` 要求 `before` 与当前值**一致**（据此拒绝过期 / 乱序 / 重复应用）；
    - 其余形状（别的块、别的行动、未知字段 / op）**显式抛错**，不静默跳过；
    - 入参 `action` **不被改写**（列表字段复制一层）。
    """
    _check_action(action)
    if not isinstance(event, dict):
        raise TypeError("event 必须是 dict，得到 %s" % type(event).__name__)
    if event.get("target") != action["action_id"]:
        raise ValueError("事件 target=%r 与 Action %r 不匹配"
                         % (event.get("target"), action["action_id"]))
    changes = event.get("changes")
    if not isinstance(changes, list) or not changes:
        raise ValueError("事件缺少非空 changes 列表（STATE-DESIGN §5.3）")

    updated = _copy_action(action)
    prefix = "/actions/%s/" % action["action_id"]
    for change in changes:
        if not isinstance(change, dict):
            raise TypeError("changes 元素必须是 dict，得到 %s" % type(change).__name__)
        if change.get("block") != "chron":
            raise ValueError("仅接受 block=chron 的变更，得到 %r" % (change.get("block"),))
        path = change.get("path")
        if not isinstance(path, str) or not path.startswith(prefix):
            raise ValueError("变更路径必须形如 %s<字段>，得到 %r" % (prefix, path))
        field = path[len(prefix):]
        if "/" in field or field not in ACTION_FIELDS:
            raise ValueError("未知的 Action 字段：%r（路径 %s）" % (field, path))
        op = change.get("op")
        if op == "set":
            if updated[field] != change.get("before"):
                raise ValueError(
                    "事件与行动卡不一致：%s 的 before=%r，实际=%r（过期 / 乱序 / 重复应用？）"
                    % (path, change.get("before"), updated[field]))
            updated[field] = change.get("after")
        elif op == "append":
            if not isinstance(updated[field], list):
                raise ValueError("append 只能用于列表字段：%s" % path)
            updated[field].append(change.get("after"))
        else:
            raise ValueError("未知的变更 op：%r（只支持 set / append）" % (op,))
    return updated


# ==========================================================================
# §5 调度器（Scheduler）——M9-C3
# ==========================================================================
#
# 状态形状（§5.1，纯 dict，可 JSON 落盘）：
#
#   scheduler_state = {
#       world_minute: int,                    # 权威世界时间（§3.1）
#       actions:      {action_id: Action},    # §4.1 行动卡（含终态，供审计与重建）
#       due_heap:     [due_key, ...],         # 最小堆；按 §5.2 排序键**升序**排列
#       windows:      [Window, ...],          # 事件窗口（§5.4）
#       blocks:       {action_id: [action_id]},  # 反向索引（§5.1 `deps` / §4.1 `blocks`）
#       running:      {action_id: key},       # 进行中行动的冲突键（§5.5 识别用，派生）
#       seq:          int,                    # 已提交事件序号（接 C5 的幂等 / 重放）
#       keys:         {idempotency_key: seq}, # 幂等键表（§8.3）
#   }
#
# §5.1 的 `deps` 在这里**合并进 `blocks`**：同一条 `depends_on` 边，正向查（`dependents_of`）
# 与反向查（`blocked_by`）都走同一张表，避免两份真相漂移。
# `due_heap` / `blocks` / `running` 都是 `actions` 的**派生索引**，可由 `actions` 全量重建
# （`rebuild_heap`），所以**不必**落盘（§8.1 / §8.2 第 4 步）。
#
# **本切片只识别、不裁决**（§5.5 / Key Decision 7）：调度器产出「该开窗口 / 该到期 / 该开始」
# 的判断与事件；胜负、物品归属、伤害与死亡等**事实**由 PRISM 与权威状态层负责。
# 屏障与时间压缩（`next_barrier`、B1–B8）见下方 §6（C4）。

# §5.2 排序键的四段（字典序）。放在一处，测试与文档都引用它，避免口径散落。
DUE_KEY_FIELDS = ("due_minute", "-priority", "created_seq", "action_id")

# §5.4 的 `Window` 形状（JSON 友好；键顺序即落盘顺序）。
WINDOW_FIELDS = (
    "window_id",        # 唯一 ID（确定性：`<分钟>:<冲突键>`）
    "key",              # 冲突键（`conflict_key` 的规范化串）
    "participants",     # 参与的行动 id（**升序**，确定性）
    "actors",           # 参与者的 actor（与 participants 对应，便于叙事层直接用）
    "place",            # 地点（`None` = 该窗口不由地点触发）
    "resource",         # 资源（`None` = 该窗口不由资源触发）
    "open_minute",      # 开窗分钟（识别到冲突的那一刻）
    "close_minute",     # 关窗分钟（`None` = 仍开着；关窗归 C4 的结算路径）
    "reason",           # 识别依据：`place` / `resource` / `blocks`
)

WINDOW_OPEN_EVENT = "window.opened"
WINDOW_CLOSE_EVENT = "window.closed"

# 到期事件类型（§8.3 幂等键 `f"{action_id}:{due_minute}:{kind}"` 里的 `kind`）。
# 名字**不叫** `action.completed`：到期 = 「结算点到了」，判成功 / 失败归 PRISM（§7 边界）。
ACTION_DUE_EVENT = "action.due"

# 幂等键的**确定性形状**（§8.3）：`<action_id>:<due_minute>:<kind>`。
# 可用它区分「调度器自己的到期键」与「行动转换产出的 `<action_id>:eNNNNNN:<event>` 键」，
# 也供 C5 的 `blocks.chron.idempotency` 表做重建 / 裁剪。
DUE_IDEMPOTENCY_RE_TEXT = r"^(?P<action_id>[^:]+):(?P<minute>\d+):(?P<kind>[^:]+)$"


class ScheduleError(ChronError):
    """调度器**调用错误**：显式抛错，绝不静默忽略、绝不部分生效。

    注意区分两种「拒绝」：

    - **语义拒绝**（依赖有环的提案）→ **不是**异常，而是 `schedule()` 返回
      `status = "rejected"` + 事件（`proposed → failed`，§5.3）——这是设计要求的路径；
    - **调用错误**（重复 id、行动非草稿、依赖 id 不存在、时间倒流、自依赖）→ 本异常。
    """


# --------------------------------------------------------------------------
# §5.1 构造 / 形状校验
# --------------------------------------------------------------------------

SCHEDULER_FIELDS = ("world_minute", "actions", "due_heap", "windows", "blocks",
                    "running", "seq", "keys")


def new_scheduler(world_minute=0):
    """新建一个空调度器状态（§5.1）。

    `world_minute` 是权威世界时间（≥ 0）；`actions` / `due_heap` / `windows` / `blocks` /
    `running` / `keys` 全部为空，`seq = 0`。世界时间只在事件推进时前进（§3.3）。
    """
    return {
        "world_minute": _require_non_negative(world_minute, "world_minute"),
        "actions": {},
        "due_heap": [],
        "windows": [],
        "blocks": {},
        "running": {},
        "seq": 0,
        "keys": {},
    }


def _check_scheduler(state):
    """校验调度器状态形状；返回原对象（只读，不改写）。"""
    if not isinstance(state, dict):
        raise TypeError("state 必须是 dict（用 new_scheduler 构造），得到 %s"
                        % type(state).__name__)
    missing = [field for field in SCHEDULER_FIELDS if field not in state]
    if missing:
        raise ValueError("调度器状态缺少字段：%s（用 new_scheduler 构造）" % ", ".join(missing))
    _require_non_negative(state["world_minute"], "state.world_minute")
    for field in ("actions", "blocks", "running", "keys"):
        if not isinstance(state[field], dict):
            raise TypeError("state.%s 必须是 dict，得到 %s" % (field, type(state[field]).__name__))
    for field in ("due_heap", "windows"):
        if not isinstance(state[field], list):
            raise TypeError("state.%s 必须是 list，得到 %s" % (field, type(state[field]).__name__))
    return state


# --------------------------------------------------------------------------
# §5.2 到期排序键（**稳定是硬要求**，GDD §6.9）
# --------------------------------------------------------------------------

def created_seq(action):
    """行动的**创建序号**（§5.2 的 `created_seq`）= 「提议」在 `events` 里的序号。

    每次转换都向 `events` 追加一条（§4.2 不变式），故第 1 条（`action.proposed`）的序号
    就是创建序号。同一分钟提议多张卡时，它给出与插入顺序 / 哈希无关的稳定次序。
    """
    _check_action(action)
    events = action["events"]
    if not events:
        raise ScheduleError("Action %s 尚无事件（events 为空），拿不到 created_seq"
                            % action["action_id"])
    parts = str(events[0]).rsplit(":e", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        raise ScheduleError("Action %s 的首条事件 id 形状异常：%r"
                            % (action["action_id"], events[0]))
    return int(parts[1])


def sort_key(action):
    """§5.2 的排序键（字典序）：`(due_minute, -priority, created_seq, action_id)`。

    四段各司其职，缺一不可：

    - `due_minute` **升序**：先到先办；
    - `priority` **降序**（取负）：规则包可让「中毒每时段恶化」压过「闲聊」；
    - `created_seq` **升序**：先提出的先办——**确定性**，不随插入顺序 / 哈希漂移；
    - `action_id` **升序**：最后的确定性 tie-break。

    同一张行动卡两次调用恒得同一键（`created_seq` 就在 `events` 里），所以「同 `due_minute`
    多卡两次运行顺序全等」不是靠运气，而是**由键唯一决定**。
    """
    _check_action(action)
    due = action["due_minute"]
    if due is None:
        raise ScheduleError("Action %s 尚未安排（due_minute 为空），无法计算排序键"
                            % action["action_id"])
    return (due, -action["priority"], created_seq(action), action["action_id"])


def _scheduling_key(action, world_minute):
    """§5.2 排序键，但把 `due_minute` 折成**最早可办分钟** `max(due_minute, world_minute)`。

    依赖把行动挡在队列外的这段时间里，世界时间会往前走；依赖成终态后行动被放行时，
    它的「预计结束」可能已经过去——那时**立刻办**（`world_minute` 到期的第一件事），
    而不是被静默丢掉。`due_minute <= world_minute` 的卡因此在同一分钟里按
    `(-priority, created_seq, action_id)` 排定顺序（`due_minute` 不再区分先后）。
    """
    due, priority, created, action_id = sort_key(action)
    return (max(due, world_minute), priority, created, action_id)


# --------------------------------------------------------------------------
# §5.3 依赖图（**无环是硬约束**）
# --------------------------------------------------------------------------

def dependents_of(state, action_id):
    """反向索引（§4.1 `blocks` / §5.1 `deps`）：依赖 `action_id` 的行动 id（升序）。"""
    _check_scheduler(state)
    return sorted(state["blocks"].get(action_id, ()))


def blocks_of(state, action_id):
    """`dependents_of` 的别名，取 §4.1 `Action.blocks` 的语义（被 `action_id` 挡住的行动）。"""
    return dependents_of(state, action_id)


def blocked_by(action):
    """依赖清单（§4.1 `depends_on`）；「一方 `blocks` 另一方」即从这里判定（§5.5）。"""
    _check_action(action)
    return sorted(action["depends_on"])


def _dependency_cycle(state, action_id, deps):
    """在「现有依赖图 + 本提案的新边」上做 DFS：**有环**返回环上的 id 序列，否则 `None`。

    环检测在**建图时**做（§5.3 硬约束）：有环即拒绝该提案（`proposed → failed`）。
    自依赖（`depends_on` 含自己）在调用方已被拦下，所以走到「回到自己」必然经过至少一条边。
    起点是 `action_id`，因此返回的环**必然经过本提案**（新提案不可能属于别的环）。
    """
    def _deps(node):
        if node == action_id:
            return list(deps)
        return list(state["actions"].get(node, {}).get("depends_on", ()))

    stack = [(action_id, [action_id])]
    seen = set()
    while stack:
        node, path = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        for nxt in _deps(node):
            if nxt == action_id:
                return path + [action_id]
            if nxt not in seen:
                stack.append((nxt, path + [nxt]))
    return None


def _missing_dependencies(state, deps):
    """列出图里不存在的依赖 id（提交顺序错误，不是依赖失败）。"""
    return [dep for dep in deps if dep not in state["actions"]]


def deps_satisfied(state, action):
    """依赖是否**全部**成终态（§5.3 首条：依赖未完成不得进入队列）。"""
    _check_action(action)
    for dep in action["depends_on"]:
        dep_card = state["actions"].get(dep)
        if dep_card is None:
            raise ScheduleError("依赖不存在：Action %s 依赖 %s，调度器里没有"
                                % (action["action_id"], dep))
        if dep_card["status"] not in ACTION_TERMINAL_STATUSES:
            return False
    return True


# --------------------------------------------------------------------------
# §5.2 派生索引：由 `actions` 全量重建到期队列
# --------------------------------------------------------------------------

def rebuild_heap(state):
    """由 `actions` 全量重建 `due_heap`（§8.2 第 4 步：重启恢复必须能重建）。

    放行条件（§5.3 首条 + §5.2）：

    - 行动**在跑**（`running`）或**已排期**（`scheduled`）；
    - 依赖**全部**已成终态（`completed` / `failed` / `cancelled`）——否则**不进堆**，
      等依赖结算后由 `rebuild_heap` 放行。

    返回按**最早可办分钟**（`_scheduling_key`）**升序**排列的 `due_heap`（最小在前）：
    条目形状 `[minute, -priority, created_seq, action_id]`，其中 `minute = max(due_minute,
    world_minute)`。依赖放行时已经「过期」的卡不会被丢掉，而是排在当前分钟立即待办。

    这是**确定性**列表：同一 `actions` 两次重建结果全等（与插入顺序 / 获得顺序无关）；
    行动因依赖被挡住的期间，也不占用堆位（§5.3 首条）。
    """
    entries = []
    for action in _check_scheduler(state)["actions"].values():
        if action["status"] not in ("scheduled", "running"):
            continue
        if not deps_satisfied(state, action):
            continue
        entries.append(list(_scheduling_key(action, state["world_minute"])))
    return sorted(entries)


# --------------------------------------------------------------------------
# §5.5 冲突识别（只识别，不裁决）
# --------------------------------------------------------------------------

def conflict_key(action):
    """行动的冲突键（§5.5）：同一 `place` 或同一 `resource` 即需协调。

    返回规范化的键串（确定性、可在两次运行间比较 / 落盘）：

    - `resource` 优先：`"resource:<id>"`；
    - 其次 `place`：`"place:<id>"`；
    - 两者都没有 → `None`（该行动**不参与**冲突识别）。

    单一键（而不是给 place 与 resource 各开一个窗口）保证「同资源两行动」只命中**一个**
    识别结论，下游便只会看到**一个** `window.opened`。

    第三种情形「一方 `blocks` 另一方」（§5.5）由 §5.3 的依赖图表达：被依赖的一方未成终态
    时不进队列（`rebuild_heap`），二者不会同时在场——所以不需要第二个窗口。
    """
    _check_action(action)
    resource = action["resource"]
    if resource is not None:
        return "resource:%s" % resource
    place = action["place"]
    if place is not None:
        return "place:%s" % place
    return None


def _open_windows_for(state, key):
    """该冲突键上**仍开着**（`close_minute is None`）的窗口。"""
    return [window for window in state["windows"]
            if window["key"] == key and window["close_minute"] is None]


def open_windows(state):
    """当前开着的窗口（按 `window_id` 升序，确定性）。"""
    _check_scheduler(state)
    return sorted((window for window in state["windows"] if window["close_minute"] is None),
                  key=lambda window: window["window_id"])


def windows_at(state, action_id):
    """`action_id` 参与的窗口 id（按 `window_id` 升序）。"""
    _check_scheduler(state)
    return sorted(window["window_id"] for window in state["windows"]
                  if action_id in window["participants"])


def _window_record(key, participants, actors, place, resource, minute):
    """构造一个 `Window`（§5.4 的字段 + `key` / `actors` / `reason` 三个实现补充）。"""
    return {
        "window_id": "%d:%s" % (minute, key),
        "key": key,
        "participants": sorted(participants),
        "actors": actors,
        "place": place,
        "resource": resource,
        "open_minute": minute,
        "close_minute": None,
        "reason": key.split(":", 1)[0],
    }


def _window_event(window, world_minute):
    """开窗事件的封套（GDD §19.5 最小字段 + STATE §5.3 `changes` 形状）。

    `target` 留空：窗口**不属于任何一张行动卡**，参与者记在 `after` 里（§5.4
    `Window.participants`）——所以开窗事件**不能**交给 `apply_event` 落卡，
    它只作「识别结论」的凭据交给状态层提交。
    """
    return {
        "event_id": "%s:%s" % (window["window_id"], WINDOW_OPEN_EVENT),
        "world_time": _world_time(world_minute),
        "type": WINDOW_OPEN_EVENT,
        "actor": window["actors"][0],
        "target": None,
        "cause": None,
        "changes": [{
            "block": "chron",
            "path": "/windows/%s" % window["window_id"],
            "op": "set",
            "before": None,
            "after": dict(window),
        }],
        "source": "system",
        "reason": window["reason"],
        "idempotency_key": "%s:%s" % (WINDOW_OPEN_EVENT, window["window_id"]),
    }


def _spawn_window(state, action_id, minute):
    """识别「该开窗口」：维护冲突窗口，并把「**该开窗**」的那一刻变成**恰好一个**事件。

    三种情形（§5.4 / §5.5）：

    - **没有冲突键**（`place` / `resource` 均为 `None`）→ 不参与识别，返回 `None`；
    - **该键上已有开着的窗口** → 把本行动**并入**该窗口的参与者，返回 `None`；
    - **该键上有其它进行中的行动** → 窗口到期即开，返回 `window.opened`（**恰好一次**）；
    - **该键上只有自己** → 还谈不上「互相影响」，不开窗（§5.4 要求两个及以上行动）。

    窗口写入 `state["windows"]`；本函数只在 `advance_to` 的**新状态**上被调用。
    """
    key = conflict_key(state["actions"][action_id])
    if key is None:
        return None
    open_here = _open_windows_for(state, key)
    if open_here:
        _join_window(state, open_here[0], action_id)
        return None
    peers = sorted(other_id for other_id, other_key in state["running"].items()
                   if other_key == key and other_id != action_id)
    if not peers:
        return None  # 只有自己：还谈不上「互相影响」，不开窗（§5.4）
    new_action = state["actions"][action_id]
    participants = peers + [action_id]
    actors = sorted(state["actions"][other]["actor"] for other in participants)
    window = _window_record(key, participants, actors,
                            new_action["place"], new_action["resource"], minute)
    state["windows"] = state["windows"] + [window]
    return _window_event(window, minute)


def _join_window(state, window, action_id):
    """把 `action_id` 并入已开着的窗口（参与者 / 参与者 actor 升序，不变更 `open_minute`）。"""
    if action_id in window["participants"]:
        return
    updated = dict(window,
                   participants=sorted(window["participants"] + [action_id]),
                   actors=sorted(window["actors"] + [state["actions"][action_id]["actor"]]))
    state["windows"] = [updated if other["window_id"] == window["window_id"] else other
                        for other in state["windows"]]


def _due_event(action, world_minute):
    """`action.due` 事件的草稿（§8.3 幂等键 `<action_id>:<due_minute>:<类型>`）。

    调度器只宣告「结算点到了」；**判成功 / 失败归 PRISM**（§7 边界），所以这不是
    `action.completed`。事件不落卡（行动仍是 `running`），只交给状态层提交。
    """
    return {
        "event_id": "%s:due%06d" % (action["action_id"], action["due_minute"]),
        "world_time": _world_time(world_minute),
        "type": ACTION_DUE_EVENT,
        "actor": action["actor"],
        "target": action["action_id"],
        "cause": action["events"][-1] if action["events"] else None,
        "changes": [],
        "source": "system",
        "reason": None,
        "idempotency_key": "%s:%d:%s" % (action["action_id"], action["due_minute"],
                                         ACTION_DUE_EVENT),
    }


# --------------------------------------------------------------------------
# §5.3–§5.5 调度入口与推进
# --------------------------------------------------------------------------

def _advance_apply(action, to, ctx):
    """一次转换 = 产事件 + 落卡，返回 `(新卡, 事件)`（`transition` 的产物原样保留）。

    这样调度器既能拿到**事件对象**（交给状态层提交），也不丢 `transition` 的封套
    （`event_id` / `world_time` / `changes` / `cause` / `reason`）。
    """
    event = transition(action, to, ctx)
    return apply_event(action, event), event


class _MinuteTracker(object):
    """把**单调不减**的世界分钟戳在事件上：同分钟取同一值，绝不回退（§3.2）。

    调度器会在一次 `advance_to` 里补出多个事件，而动作转换的世界分钟与到期分钟可能不同
    （例如 `start_minute` 排在未来）。`max(last, minute)` 保证事件封套里的时间不会倒退。
    """

    def __init__(self, minute):
        self.minute = minute

    def at(self, minute):
        self.minute = max(self.minute, minute)
        return self.minute


def _tail_events(action, before):
    """`before`（追加前的事件 id 列表）之后新追加的事件 id，按产出顺序返回。"""
    return list(action["events"][len(before):])


def schedule(state, draft, *, world_minute, start_minute=None,
             place=None, resource=None):
    """把一张草稿排入时间线（§5.3；对应 §4.3 的 `schedule(action) -> action_id`）。

    返回**结果 dict**（拒 / 纳两条路都不抛异常，便于按事件归因）：

    ```
    {"status": "scheduled" | "rejected", "action_id": str,
     "state": 新调度器状态, "events": [本次产出的事件, ...]}
    ```

    `status = "rejected"` 的**唯一**语义拒绝是**依赖有环**（§5.3 硬约束）：提案先正常
    `proposed`（留下可追溯事件），再 `proposed → failed`，两条事件都在 `events` 里；
    **被拒的卡不进 `actions`**（不留污染，同一 `action_id` 修正依赖后可重试）。

    依赖**暂时未完成**的卡**不在此列**：它照常 `scheduled`，但**不进 `due_heap`**
    ——到期排序只放行依赖已全部成终态的行动（§5.3 首条）。

    入参：

    - `draft`：`new_action` 构造的草稿（`status is None`）；
    - `world_minute`（**必填**）：本次调度发生的世界分钟（§3.2 权威时间）；
    - `start_minute`（可选）：安排开始（缺省 = `world_minute`，立即）；
    - `place` / `resource`（可选）：冲突识别键（§5.5），**给了就覆盖**草稿里的值。

    调用错误（**抛 `ScheduleError`**）：`action_id` 重复、行动不是草稿、依赖 id **不存在**
    （应先提交依赖）、`start_minute` 早于 `world_minute`、自依赖。这些都是**提交顺序 /
    入参**问题，不是「提案该失败」——不该被 `proposed → failed` 掩盖。
    """
    _check_scheduler(state)
    _check_action(draft)
    if draft["status"] is not None:
        raise ScheduleError("schedule 只接草稿（status is None），Action %s 已是 %s"
                            % (draft["action_id"], _status_label(draft["status"])))
    action_id = draft["action_id"]
    if action_id in state["actions"]:
        raise ScheduleError("action_id 重复：%s 已在调度器里（status=%s）"
                            % (action_id, state["actions"][action_id]["status"]))
    now = _require_non_negative(world_minute, "world_minute")
    if now < state["world_minute"]:
        raise ScheduleError("调度时间不得早于调度器当前世界时间：world_minute=%d < %d"
                            % (now, state["world_minute"]))
    action = _copy_action(draft)
    if place is not None:
        action["place"] = _require_optional_str(place, "place")
    if resource is not None:
        action["resource"] = _require_optional_str(resource, "resource")
    start = _require_non_negative(start_minute if start_minute is not None else now,
                                  "start_minute")
    if start < now:
        raise ScheduleError("安排开始不得早于本次调度时间：start_minute=%d < world_minute=%d"
                            % (start, now))

    deps = list(action["depends_on"])
    if action_id in deps:
        raise ScheduleError("自依赖：Action %s 的 depends_on 含自己（§5.3 无环约束）"
                            % action_id)
    missing = _missing_dependencies(state, deps)
    if missing:
        raise ScheduleError("依赖不存在：Action %s 依赖 %s 不在调度器里（先提交依赖再排）"
                            % (action_id, " / ".join(missing)))
    cycle = _dependency_cycle(state, action_id, deps) if deps else None

    # 1) 提议（`None → proposed`）。环判定在建图**之前**完成，所以拒单也能留下可追溯事件。
    action, propose_event = _advance_apply(action, "proposed",
                                           {"world_minute": now, "who": "P"})
    events = [propose_event]
    if cycle is not None:
        action, fail_event = _advance_apply(action, "failed",
                                            {"world_minute": now, "who": "R",
                                             "reason": "dependency_cycle",
                                             "cause": propose_event["event_id"]})
        events.append(fail_event)
        return {"status": "rejected", "action_id": action_id, "cycle": cycle,
                "state": state, "events": events}

    # 2) 排期（`proposed → scheduled`）：写下 start / due（§4.1）。
    action, schedule_event = _advance_apply(action, "scheduled",
                                            {"world_minute": now, "who": "S",
                                             "start_minute": start})
    events.append(schedule_event)

    # 3) 纳图：写行动卡、维护反向索引（§4.1 `blocks` 是 `depends_on` 的冗余索引）。
    actions = dict(state["actions"])
    actions[action_id] = action
    blocks = {key: list(value) for key, value in state["blocks"].items()}
    for dep in deps:
        dependents = blocks.setdefault(dep, [])
        if action_id not in dependents:
            dependents.append(action_id)
        dep_card = actions[dep]
        if action_id not in dep_card["blocks"]:
            dep_card = dict(dep_card, blocks=list(dep_card["blocks"]) + [action_id])
            actions[dep] = dep_card

    # 4) 重建派生索引；依赖未成终态者留在 `actions` 但不进堆（§5.3 首条）。
    new_state = dict(state, world_minute=now, actions=actions, blocks=blocks)
    new_state["due_heap"] = rebuild_heap(new_state)
    return {"status": "scheduled", "action_id": action_id, "state": new_state,
            "events": events}

    # 3) 纳图：写行动卡、维护反向索引（§4.1 `blocks` 是 `depends_on` 的冗余索引）。
    actions = dict(state["actions"])
    actions[action_id] = action
    blocks = {key: list(value) for key, value in state["blocks"].items()}
    for dep in deps:
        dependents = blocks.setdefault(dep, [])
        if action_id not in dependents:
            dependents.append(action_id)
        dep_card = actions[dep]
        if action_id not in dep_card["blocks"]:
            dep_card = dict(dep_card, blocks=list(dep_card["blocks"]) + [action_id])
            actions[dep] = dep_card

    # 4) 重建派生索引；依赖未成终态者留在 `actions` 但不进堆（§5.3 首条）。
    new_state = dict(state, world_minute=now, actions=actions, blocks=blocks)
    new_state["due_heap"] = rebuild_heap(new_state)
    return {"status": "scheduled", "action_id": action_id, "state": new_state,
            "events": events}


def _cascade_dependents(state, minute, known_bad):
    """§5.3 级联（`settle` / `advance_to` 内部用，见公开的 `cascade_dependents`）。"""
    events = {}
    known_bad = set(known_bad)
    handled = set()
    changed = True
    while changed:
        changed = False
        for action_id in sorted(state["actions"]):
            if action_id in handled:
                continue
            action = state["actions"][action_id]
            if action["status"] not in ("scheduled", "blocked"):
                continue
            bad = [dep for dep in action["depends_on"] if dep in known_bad]
            if not bad:
                continue
            target = "blocked" if action["on_dep_fail"] == "keep" else "cancelled"
            ctx = {"world_minute": minute, "who": "S" if target == "blocked" else "T",
                   "reason": "dependency_failed"}
            handled.add(action_id)
            if action["status"] == target:
                # 已经是目标态（blocked → blocked 不在转换表里）：只登记，不产二次事件。
                changed = True
                continue
            updated, event = _advance_apply(action, target, ctx)
            state["actions"][action_id] = updated
            state["running"].pop(action_id, None)
            events.setdefault(action_id, []).append(event)
            changed = True
            if target == "cancelled":
                known_bad.add(action_id)
    return events


def _settle_who(from_status, to):
    """`settle` 该以谁的名义结算：从 §4.2 全转换表里取**该行允许**的触发者。

    同一目标状态在不同起点下「谁」列不同（`running → cancelled` 只许 `P`，
    `scheduled → cancelled` 也收 `T`；结算 `P` 优先——取消首先是**玩家意图**）。
    表里一行都收不到 `who` 时返回 `None`（`transition` 不做「谁」校验）。
    """
    allowed = tuple(row["who"] for row in ACTION_TRANSITIONS
                    if row["from"] == from_status and row["to"] == to)
    if not allowed:
        return None
    for candidate in ("P", "R", "S", "T"):
        if all(candidate in who for who in allowed):
            return candidate
    return None


def cascade_dependents(state, minute, known_bad):
    """§5.3 级联：依赖 `failed` / `cancelled` 的行动按 `on_dep_fail` 处理（公开入口）。

    - 默认 `on_dep_fail = "cancel"`：转 `cancelled`，事件带 `reason: dependency_failed`
      （机器可读，供上层归因）；
    - 标了 `"keep"` 的卡改判 `blocked`（`scheduled → blocked`，可恢复；**不**继续往它的下游传）；
    - 级联**传递**（B 依赖 A、C 依赖 B，A 失败则 B、C 一起取消）；
    - 覆盖「依赖失败时这条依赖链上还没轮到自己进堆」的行动——它们省了整轮，但这一步照常处理。

    返回 `{"state": 新状态, "events": {action_id: [事件, ...]}}`；**不改写入参 `state`**。
    `known_bad` 是已知失败 / 被取消的行动 id 集合（`settle` 会把刚落终态的卡放进去）。
    """
    _check_scheduler(state)
    minute = _require_non_negative(minute, "minute")
    new_state = dict(state,
                     actions=dict(state["actions"]),
                     blocks=dict(state["blocks"]),
                     running=dict(state["running"]))
    new_state["world_minute"] = max(new_state["world_minute"], minute)
    events = _cascade_dependents(new_state, minute, known_bad)
    new_state["due_heap"] = rebuild_heap(new_state)
    return {"state": new_state, "events": events}


def settle(state, action_id, to, *, world_minute, reason=None):
    """结算 / 取消一张行动卡，并**级联**处理它的下游（§5.3）。

    `to` 是终态之一（`completed` / `failed` / `cancelled`）；`reason` 随事件带出
    （如依赖失败时的 `dependency_failed`）。返回
    `{"state": 新状态, "events": {action_id: [事件, ...]}}`——事件以**行动**为键，
    因为一次结算可能牵动多张卡（级联取消）；同一行动的事件按产出顺序排列。

    结算会让依赖成终态，于是原本卡在「依赖未完成」的行动可以进堆——本函数最后重建
    `due_heap`，把这类行动放行（§5.3）。**不改写入参 `state`**。
    """
    _check_scheduler(state)
    if to not in ACTION_TERMINAL_STATUSES:
        raise ScheduleError("settle 的 to 必须是终态（%s），得到 %r"
                            % (" / ".join(ACTION_TERMINAL_STATUSES), to))
    if action_id not in state["actions"]:
        raise ScheduleError("settle 的行动不在调度器里：%s" % action_id)
    minute = _require_non_negative(world_minute, "world_minute")
    new_state = dict(state,
                     actions=dict(state["actions"]),
                     blocks=dict(state["blocks"]),
                     running=dict(state["running"]),
                     windows=[dict(window) for window in state["windows"]])
    new_state["world_minute"] = max(new_state["world_minute"], minute)
    action = new_state["actions"][action_id]
    ctx = {"world_minute": minute, "who": _settle_who(action["status"], to)}
    if reason is not None:
        ctx["reason"] = reason
    updated, event = _advance_apply(action, to, ctx)
    events = {action_id: [event]}
    new_state["actions"][action_id] = updated
    new_state["running"].pop(action_id, None)
    if to in ("failed", "cancelled"):
        cascade = _cascade_dependents(new_state, minute, {action_id})
        for other_id, other_events in cascade.items():
            events.setdefault(other_id, []).extend(other_events)
    new_state["due_heap"] = rebuild_heap(new_state)
    return {"state": new_state, "events": events}


def _fire_dues_upto(state, limit_minute, tracker, events):
    """逐条触发堆上 **≤ `limit_minute`** 的到期（§5.2 稳定堆序），返回**真实触发过的分钟**。

    - 不批量绕过校验（§6.3 硬约束）：逐个到期点走 `_fire_one`；
    - 这一步里有行动失败 / 被取消 → 立刻级联它的下游（§5.3）；
    - 只改 `state` 的派生索引与行动卡（调用方已在新状态上操作）。
    """
    fired = []
    while state["due_heap"]:
        due_minute = state["due_heap"][0][0]
        if due_minute > limit_minute:
            break
        bad_before = {action_id for action_id, action in state["actions"].items()
                      if action["status"] in ("failed", "cancelled")}
        events.extend(_fire_one(state, due_minute, tracker))
        fired.append(due_minute)
        bad_after = {action_id for action_id, action in state["actions"].items()
                     if action["status"] in ("failed", "cancelled")}
        if bad_after - bad_before:
            cascade = _cascade_dependents(state, state["world_minute"], bad_after)
            for action_id in sorted(cascade):
                events.extend(cascade[action_id])
    return fired


# ==========================================================================
# §6 时间压缩与事件屏障（Time Compression & Event Barrier）——M9-C4
# ==========================================================================
#
# §6.1 的充要条件（该时段内没有重要交互 / 冲突 / 因果依赖）在 §6.2 落成 **B1–B8 八条
# 机读判据**。本切片把八条判据做成**一张表**（`BARRIERS`：编号 / 名称 / 设计原句 /
# 机读落地），识别的产物统一是 `Barrier` 记录（`BARRIER_FIELDS`）——「哪一条判据在拦」
# 因此可核对、可测试，而不是散在 `if` 里的隐式判断。
#
# 两条硬口径（§6.3）：
#
# 1. **停在屏障之前**：语义屏障（B1–B7）的那一分钟**不执行**——`world_minute` 停在
#    屏障分钟返回，该分钟的内容（`action.started` / 开窗 / 到期）留给玩家；到达之后
#    再次推进即表示「玩家已处理，继续」，否则屏障会永久卡死压缩（§6.4 的「不允许压缩」
#    只在**尚未到达**时拦）。
# 2. **到期是结算点**（B8）：到期**逐条触发**（绝不跳过、绝不延迟到别的分钟），然后
#    `advance_to` **停在该分钟**——结算未完成前压缩不前进（§6.4；「这是结算点」）。
#
# 判据里带**内容判断**的部分（战斗 / 线索 / 危险 / 遭遇）由**规则包**翻成行动声明
# （`kind` / `interrupt_conditions`）：CHRON 只认名字、只按名字停（§7 依赖方向）。
# CHRON 自己的事件词表只有 `action.*` / `window.*`（没有 `combat.start` /
# `discovered_facts`），所以「事件类」判据在 CHRON 侧的落地就是**行动声明**。
#
# **不在本切片**：不判「谁在线」（`offline_advance` 只作入参回显，开关落 M10，§3.4）、
# 不判行动胜负（归 PRISM）、不关窗口（`window.closed` 归后续切片）。

BARRIER_IDS = ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8")

# §6.2 八条判据：`criterion` 是设计文档的机读判据原句，`machine` 是 CHRON 侧的落地
# （手里有什么就用什么；两者都进表，审查时一眼能对照）。
BARRIERS = (
    {
        "id": "B1",
        "name": "进入战斗",
        "criterion": "到期事件类型 ∈ {combat.start}；或某行动 kind = combat 开始",
        "machine": "kind = combat 的行动开始；或进行中的战斗行动（已在战斗）",
    },
    {
        "id": "B2",
        "name": "发现关键线索",
        "criterion": "事件 discovered_facts 含 significant: true",
        "machine": "kind = discover 的行动**到期**（到达时发现；是否关键由规则包用这个 kind 声明）"
                   "——标注在该结算点上，到期照常触发",
    },
    {
        "id": "B3",
        "name": "玩家角色安全受威胁",
        "criterion": "事件的 risk 触及某 PC 的伤害 / 死亡 / 被俘",
        "machine": "kind = hazard 或带 hazard 中断条件的行动开始；或这类行动进行中",
    },
    {
        "id": "B4",
        "name": "角色遭遇",
        "criterion": "新 NPC / 另一玩家在同一 place 出现（encounter 事件）",
        "machine": "kind = encounter 的行动开始（CHRON 看不出 actor 是 PC 还是 NPC，名字由规则包给）",
    },
    {
        "id": "B5",
        "name": "世界状态冲突",
        "criterion": "两行动争同一 resource / 同一 place（§5.5 的识别命中）",
        "machine": "窗口开着且参与者仍在飞 → 立刻停；两张同键的卡都在飞 → 把「较晚的到期分钟」"
                   "标注为识别命中点（开窗就在那一刻，§5.4）",
    },
    {
        "id": "B6",
        "name": "玩家有未决决策",
        "criterion": "存在 status = proposed 且 needs_confirmation = true（GDD §8.3 风险分级）",
        "machine": "存在 status = proposed 的卡；卡上显式 needs_confirmation = False 时不停",
    },
    {
        "id": "B7",
        "name": "行动转 blocked / interrupted",
        "criterion": "状态机产生这两类转换",
        "machine": "存在 status ∈ {blocked, interrupted} 的卡（等规则 / 玩家处理）",
    },
    {
        "id": "B8",
        "name": "有行动到期",
        "criterion": "due_minute ≤ 目标分钟——到期本身总是要停（这是结算点）",
        "machine": "rebuild_heap 上最早的可办到期分钟（≥ 起点），逐条列在答案里",
    },
)

_BARRIER_BY_ID = {row["id"]: row for row in BARRIERS}

assert tuple(row["id"] for row in BARRIERS) == BARRIER_IDS, "BARRIERS 表的编号必须与 BARRIER_IDS 一致"

# 屏障记录的字段（JSON 友好；`subject` = 相关行动 id / 窗口 id，`None` = 无单一主体）。
BARRIER_FIELDS = ("id", "name", "minute", "reason", "subject")

# §6.2 判据的**行动声明**侧（规则包给名字，CHRON 只认名字）：
#   - `KIND_BARRIERS`：行动 `kind` → 屏障编号；
#   - `INTERRUPT_BARRIERS`：中断条件 `kind`（ACTION-DESIGN §8.4 词表）→ 屏障编号。
KIND_BARRIERS = {
    "combat": "B1",      # 战斗行动：一开始即「进入战斗」
    "discover": "B2",    # 发现类行动：**到期**时即「发现关键线索」（§6.5 第 3 条的例子）
    "hazard": "B3",      # 危险类行动：一开始即「玩家角色安全受威胁」
    "encounter": "B4",   # 遭遇类行动：一开始即「角色遭遇」
}

INTERRUPT_BARRIERS = {
    "enemy_appears": "B1",   # 敌人出现 = 进入战斗
    "hazard": "B3",          # 危险 = 玩家角色安全受威胁
}

# 一次性拦在**当前分钟**的判据（§6.4「不允许压缩」）：B5 窗口开着 / B6 未决提案 /
# B7 blocked、interrupted。上表里的 B1 / B3 对**进行中**行动同样立刻拦。
NOW_BARRIER_IDS = ("B1", "B3", "B5", "B6", "B7")

# 声明型屏障里**会暂停**的编号（停点 = 它，停在它**之前**、那一分钟的内容不执行）：
# B1 / B3 / B4 是「那一分钟会发生一件值得玩家在场的事」（战斗开始 / 危险开始 / 遭遇），
# 该分钟**留给玩家**；B2（发现）与 B5（冲突识别）落点就在**到期分钟**上——那一刻本来
# 就是结算点（B8），它们只**标注**（「这个结算点是线索 / 是冲突」），到期照常触发。
PAUSING_BARRIER_IDS = ("B1", "B3", "B4")


def _barrier_record(minute, barrier_id, reason, subject=None):
    """一条机读屏障记录（`BARRIER_FIELDS`；确定性、可 JSON 落盘）。"""
    return {
        "id": barrier_id,
        "name": _BARRIER_BY_ID[barrier_id]["name"],
        "minute": minute,
        "reason": reason,
        "subject": subject,
    }


def _sorted_barrier_records(found):
    """把 `(minute, id, reason, subject)` 集合折成**确定性**的记录列表（按编号 / 原因 / 主体）。"""
    records = [_barrier_record(minute, barrier_id, reason, subject)
               for minute, barrier_id, reason, subject in found]
    return sorted(records, key=lambda record: (record["id"], record["reason"],
                                               record["subject"] or ""))


def _interrupt_kinds(action):
    """行动 `interrupt_conditions` 里声明的条件 `kind`（只读 `kind`；其他形状忽略，不判）。"""
    kinds = []
    for item in action["interrupt_conditions"]:
        if isinstance(item, dict):
            kind = item.get("kind")
        elif isinstance(item, str):
            kind = item
        else:
            kind = None
        if isinstance(kind, str) and kind:
            kinds.append(kind)
    return kinds


def _in_flight(state, action_id):
    """行动是否「在飞」（已排期 / 进行中）——只有它们才可能撞上未来的屏障。"""
    card = state["actions"].get(action_id)
    return card is not None and card["status"] in ("scheduled", "running")


def _conflict_groups(state):
    """按冲突键（§5.5）分组的**在飞**行动：`{key: [action_id, ...]}`（升序、确定性）。"""
    groups = {}
    for action_id in sorted(state["actions"]):
        action = state["actions"][action_id]
        if action["status"] not in ("scheduled", "running"):
            continue
        key = conflict_key(action)
        if key is not None:
            groups.setdefault(key, []).append(action_id)
    return groups


def _declared_barriers(state):
    """**声明型**屏障：`{minute: [记录, ...]}`（B1–B5；由 `kind` / 中断条件 / 冲突键推出）。

    - B1 / B3 / B4：行动**开始**的那一分钟；
    - B2：行动**到期**的那一分钟（「到达时会发现线索」——发现发生在到达时）；
    - B5：同一冲突键的两张卡都在飞 → 识别命中的分钟 = 两者**较晚的到期分钟**
      （此刻第二张卡开始、第一张还在飞 → §5.4 开窗）。它**只标注**（不暂停，见
      `PAUSING_BARRIER_IDS`）：那一刻本来就是到期点（B8），窗口照常开出来。

    已到达的分钟也如实列出（调用方按 `> 起点` 过滤）——「到达过」不等于「不存在」。
    依赖未成（暂不进 `due_heap`）的行动也算「已排期」：略早停一次是**安全**的
    （代价只是少压缩，不会删因果）。
    """
    found = set()
    for action_id in sorted(state["actions"]):
        action = state["actions"][action_id]
        if action["status"] not in ("scheduled", "running"):
            continue
        for kind, barrier_id in sorted(KIND_BARRIERS.items()):
            if action["kind"] != kind:
                continue
            minute = action["due_minute"] if barrier_id == "B2" else action["start_minute"]
            if minute is not None:
                found.add((minute, barrier_id, "kind:%s" % kind, action_id))
        for condition, barrier_id in sorted(INTERRUPT_BARRIERS.items()):
            if condition in _interrupt_kinds(action) and action["start_minute"] is not None:
                found.add((action["start_minute"], barrier_id,
                           "interrupt:%s" % condition, action_id))
    for key, action_ids in sorted(_conflict_groups(state).items()):
        if len(action_ids) < 2:
            continue
        minute = max(state["actions"][action_id]["due_minute"] for action_id in action_ids)
        if minute is not None:
            found.add((minute, "B5", "conflict:%s" % key, key))
    grouped = {}
    for record in _sorted_barrier_records(found):
        grouped.setdefault(record["minute"], []).append(record)
    return grouped


def _due_barriers(state, minute):
    """该分钟上的**到期**屏障（B8）：逐条记录（按 §5.2 堆序 → 确定性）。"""
    records = []
    for entry in rebuild_heap(state):
        if entry[0] == minute:
            records.append(_barrier_record(minute, "B8", "action_due", entry[3]))
    return records


def _now_barriers(state):
    """**当前就成立**的屏障（`minute` = 当前世界分钟）：压缩**不允许**（§6.4）。

    - B1 / B3：**进行中**的战斗 / 危险行动（已在战斗 / 已在危险中，不能快进）；
    - B5：**开着的窗口且参与者仍在飞**（§6.4 第 1 条：窗口内必须同步；参与者全部落定后
      窗口不再拦压缩——关窗本身（`window.closed`）不在本切片）；
    - B6：停在 `proposed` 的卡 = 玩家有未决决策（显式 `needs_confirmation = False` 时不停）；
    - B7：`blocked` / `interrupted` 的卡 = 状态机已产生这两类转换，等规则 / 玩家处理。
    """
    minute = state["world_minute"]
    found = set()
    for action_id in sorted(state["actions"]):
        action = state["actions"][action_id]
        if action["status"] == "running":
            conditions = set(_interrupt_kinds(action))
            for kind, barrier_id in sorted(KIND_BARRIERS.items()):
                if action["kind"] == kind and barrier_id in ("B1", "B3"):
                    found.add((minute, barrier_id, "running:%s" % kind, action_id))
            for condition, barrier_id in sorted(INTERRUPT_BARRIERS.items()):
                if condition in conditions:
                    found.add((minute, barrier_id, "running:interrupt:%s" % condition,
                               action_id))
        elif action["status"] == "proposed":
            if action.get("needs_confirmation", True) is not False:
                found.add((minute, "B6", "proposal_pending", action_id))
        elif action["status"] in ("blocked", "interrupted"):
            found.add((minute, "B7", "status:%s" % action["status"], action_id))
    for window in open_windows(state):
        if [action_id for action_id in window["participants"] if _in_flight(state, action_id)]:
            found.add((minute, "B5", "window_open", window["window_id"]))
    return _sorted_barrier_records(found)


def barriers_at(state, minute):
    """该分钟上命中的**机读屏障记录**（§6.2 B1–B8；确定性、按编号 / 原因 / 主体升序）。

    - **声明型**（B1–B5）：行动 `kind` / 中断条件 / 冲突键推出的分钟；
    - **到期**（B8）：`rebuild_heap` 上落在该分钟的到期，**逐条**一条记录；
    - **当前型**（B1 / B3 / B5 / B6 / B7）：只在 `minute == state["world_minute"]` 时计入
      ——它们说的是「**现在**就不能压缩」。

    这是**只读判断**：不改写状态、不产生事件。`advance_to` 的返回里也带同一形状的记录
    （`barriers` = 造成本次停下的记录；同一分钟上的其它判据也如实列出，如 B1 与 B8 同分钟
    时两条都在——§6.5 第 5 条的「屏障优先」据此可见）。
    """
    _check_scheduler(state)
    minute = _require_non_negative(minute, "minute")
    found = set()
    for record in _declared_barriers(state).get(minute, ()):
        found.add((minute, record["id"], record["reason"], record["subject"]))
    for record in _due_barriers(state, minute):
        found.add((minute, record["id"], record["reason"], record["subject"]))
    if minute == state["world_minute"]:
        for record in _now_barriers(state):
            found.add((minute, record["id"], record["reason"], record["subject"]))
    return _sorted_barrier_records(found)


def _pausing_barrier_minute(state, after_minute):
    """`after_minute` 之后**最早**的语义屏障分钟（B1 / B3 / B4，见 `PAUSING_BARRIER_IDS`）。"""
    minutes = [minute for minute, records in _declared_barriers(state).items()
               if minute > after_minute
               and any(record["id"] in PAUSING_BARRIER_IDS for record in records)]
    return min(minutes) if minutes else None


def _earliest_due_minute(state, from_minute):
    """`from_minute` 起**最早**的到期分钟（B8 结算点；`rebuild_heap` 上的最小 ≥ 起点）。"""
    minutes = [entry[0] for entry in rebuild_heap(state) if entry[0] >= from_minute]
    return min(minutes) if minutes else None


def next_barrier(state, from_minute=None):
    """下一个**屏障 / 结算点**分钟：从 `from_minute` 起**最早可能停住**的那一分钟。

    = min(**最早的语义屏障**（B1 / B3 / B4，停在它之前）, **最早的到期**（B8，结算点）)；
    没有可停的分钟就返回 `None`。对**给定目标**的实际停点看 `advance_to` 的 `stopped_at`
    （有语义屏障时先停屏障：途中到期照常触发，屏障之后不再推进）。

    - `from_minute` 省略 = 当前世界分钟；不得早于当前（世界时间只增不减，§3.2）→ 否则
      显式抛 `ScheduleError`；
    - **现在就不能压缩**（§6.4）→ 返回 `from_minute` 本身（屏障就是「当前这一分钟」）：
      B1 / B3 的进行中行动、B5 的开着窗口（参与者仍在飞）、B6 的未决提案、B7 的
      blocked / interrupted 卡。**该判断只对 `from_minute == 当前世界分钟` 生效**
      （它们说的是「现在」）；
    - **已到达**的屏障分钟不再拦（屏障 = 停一次、把控制权交回）：到达后再次推进即可
      得到下一个屏障——否则任何屏障都会永久卡死压缩；
    - 到期（B8）与语义屏障落在同一分钟时**屏障优先**（§6.5 第 5 条）：本函数只给分钟，
      「停在屏障之前（不执行）」与「停在结算点上（已触发）」的区别看 `barriers_at`
      与 `advance_to` 的 `stopped_at` / `barriers`。
    """
    _check_scheduler(state)
    now = state["world_minute"]
    start = now if from_minute is None else _require_non_negative(from_minute, "from_minute")
    if start < now:
        raise ScheduleError("next_barrier 的起点不得早于当前世界时间：from_minute=%d < "
                            "world_minute=%d" % (start, now))
    if start == now and _now_barriers(state):
        return start
    minutes = [minute for minute, records in _declared_barriers(state).items()
               if minute > start and any(record["id"] in PAUSING_BARRIER_IDS
                                         for record in records)]
    minutes.extend(entry[0] for entry in rebuild_heap(state) if entry[0] >= start)
    return min(minutes) if minutes else None


def _stop_barriers(state, stop, start):
    """停在 `stop` 时**真正命中**的屏障记录（已到达的声明型屏障不算，见 `next_barrier`）。"""
    found = set()
    if stop > start:
        for record in _declared_barriers(state).get(stop, ()):
            found.add((stop, record["id"], record["reason"], record["subject"]))
    for record in _due_barriers(state, stop):
        found.add((stop, record["id"], record["reason"], record["subject"]))
    return _sorted_barrier_records(found)


def _advance_result(state, start, target, offline_advance, events, fired_minutes,
                    stopped_at, barriers):
    """`advance_to` 的结果封套 + **压缩日志**（§6.3 末条：只记真实事件）。

    `compression_log` 里只有**真实发生**的事件的引用（`fired_minutes` = 真实触发过到期的
    分钟，`event_ids` = 本次产出的事件 id，与 `events` 一一对应）；被压缩掉的时段
    **不**产生条目、更不产生「被跳过」的模拟事件。
    """
    return {
        "state": state,
        "world_minute": state["world_minute"],
        "target": target,
        "stopped_at": stopped_at,
        "barriers": list(barriers),
        "events": events,
        "compression_log": {
            "from_minute": start,
            "to_minute": state["world_minute"],
            "target_minute": target,
            "stopped_at": stopped_at,
            "offline_advance": offline_advance,
            "fired_minutes": sorted(set(fired_minutes)),
            "event_ids": [event["event_id"] for event in events],
        },
    }


def advance_to(state, target_minute, *, offline_advance=False):
    """推进世界时间到 `target_minute`——**遇屏障就停**（§6.2 / §6.3；M9-C4）。

    返回：

    ```
    {"state": 新状态, "world_minute": 实际到达分钟, "target": 目标分钟,
     "stopped_at": 停住的分钟 / None（抵达目标）,
     "barriers": [造成本次停下的屏障记录, ...],
     "events": [途中事件, ...],
     "compression_log": {……只含真实事件……}}
    ```

    语义（§6.3 / §6.4 两条硬口径）：

    - **停点 = 下一个屏障**：区间内有语义屏障（B1 / B3 / B4）就以**最早的那个**为停点，
      **停在它之前**——该分钟的内容（`action.started` / 开窗 / 到期）留给玩家；严格早于
      它的到期照常**逐条触发**（§6.3 的 `fire_all_due_at` 在屏障前照跑）；
    - **没有语义屏障 → 停在最早的到期**（B8：到期是结算点）：触发那一分钟的到期，然后
      停住——结算未完成前压缩**不再前进**（§6.4；目标分钟不得跨越屏障，B1–B8 任一）；
    - **屏障优先**（§6.5 第 5 条）：同一分钟既有到期又有语义屏障 → 屏障优先，该分钟的到期
      **不**触发；
    - **抵达不算跨越**：停点 `> target` 时推进到 `target`（`stopped_at is None`）；停点
      `== target` 且是到期时同样算抵达（到期已触发）；
    - **已到达的屏障不再拦**：屏障 = 停一次、把控制权交回；到达后再次推进即「玩家已处理，
      继续」，否则任何屏障都会永久卡死压缩；
    - **压缩不产生假事件**：被压缩掉的时段不产出任何事件，`compression_log` 只引用真实事件。

    `offline_advance` **只作入参**（默认 `False`，沿用 §3.4 的口径）：本切片**不判**
    「有没有人在线」（那是房间设置 / M10 的事），所以它**不改变**推进结果，只回显在
    `compression_log` 里。

    性质：**不改写入参 `state`**（`actions` / `blocks` / `running` / `windows` 都在新状态上
    复制后再改），返回的新状态可以与入参安全共存。`target_minute` 早于当前世界时间 →
    显式抛 `ScheduleError`（世界时间只增不减，§3.2）；`offline_advance` 非 bool → `TypeError`。
    """
    _check_scheduler(state)
    target = _require_non_negative(target_minute, "target_minute")
    if target < state["world_minute"]:
        raise ScheduleError("世界时间只增不减：target_minute=%d < world_minute=%d"
                            % (target, state["world_minute"]))
    flag = _require_bool(offline_advance, "offline_advance")
    new_state = dict(state,
                     actions=dict(state["actions"]),
                     blocks=dict(state["blocks"]),
                     running=dict(state["running"]),
                     windows=[dict(window) for window in state["windows"]],
                     due_heap=[])
    # 派生索引先重建（§8.2 第 4 步）：屏障判据与触发顺序都基于它，因此与入参堆的陈旧无关。
    new_state["due_heap"] = rebuild_heap(new_state)
    start = new_state["world_minute"]
    tracker = _MinuteTracker(start)
    events = []
    now = _now_barriers(new_state)
    if now:
        # §6.4「不允许压缩」：当前这一分钟就停（不动时间、不产事件）。
        return _advance_result(new_state, start, target, flag, events, [], start, now)
    # 停点：**有语义屏障（B1 / B3 / B4）就以它为停点**（把它的那一分钟留给玩家；严格早于
    # 它的到期照常逐条触发，§6.3）；没有语义屏障则以**最早的到期**（B8 结算点）为停点。
    barrier_minute = _pausing_barrier_minute(new_state, start)
    if barrier_minute is not None and barrier_minute <= target:
        records = _stop_barriers(new_state, barrier_minute, start)
        fired = _fire_dues_upto(new_state, barrier_minute - 1, tracker, events)
        new_state["world_minute"] = max(new_state["world_minute"], barrier_minute)
        new_state["due_heap"] = rebuild_heap(new_state)
        return _advance_result(new_state, start, target, flag, events, fired,
                               barrier_minute, records)
    due_stop = _earliest_due_minute(new_state, start)
    if due_stop is None or due_stop > target:
        # 区间内没有到期（也没有语义屏障）→ 纯压缩：抵达目标、不产事件。
        fired = _fire_dues_upto(new_state, target, tracker, events)
        new_state["world_minute"] = target
        new_state["due_heap"] = rebuild_heap(new_state)
        return _advance_result(new_state, start, target, flag, events, fired, None, [])
    records = _stop_barriers(new_state, due_stop, start)
    fired = _fire_dues_upto(new_state, due_stop, tracker, events)
    new_state["world_minute"] = max(new_state["world_minute"], due_stop)
    new_state["due_heap"] = rebuild_heap(new_state)
    if due_stop == target:
        return _advance_result(new_state, start, target, flag, events, fired, None, [])
    return _advance_result(new_state, start, target, flag, events, fired, due_stop, records)


def _fire_one(state, due_minute, tracker):
    """处理**最早一个到期点**（`due_minute`），返回途中事件（按执行顺序）。

    - 到达 `due_minute` 仍 `scheduled` 的卡在此转 `running`（`action.started`）；
    - 同分钟有多张卡时，严格按 `_scheduling_key` 的**堆序**逐张处理（不批量绕过校验）；
    - 出现 §5.5 的冲突（同 `place` / 同 `resource`）→ 开窗（**恰好一个** `window.opened`）；
    - 到期本身产出 `action.due`（结算点由 PRISM 判，调度器只说「到了」）。

    `due_minute` 是堆上的**最早可办分钟**，可能晚于 `action.due_minute`（依赖放行时已过期）：
    事件一律戳在**实际处理的那一分钟**上。
    """
    state["world_minute"] = max(state["world_minute"], due_minute)
    events = []
    while state["due_heap"] and state["due_heap"][0][0] == due_minute:
        entry = list(state["due_heap"].pop(0))
        action_id = entry[3]
        action = state["actions"].get(action_id)
        if action is None or action["status"] not in ("scheduled", "running"):
            continue
        if list(_scheduling_key(action, due_minute)) != entry:
            continue  # 堆序已被改写，等重建（不用过期条目）
        if action["status"] == "scheduled":
            started, started_event = _advance_apply(
                action, "running", {"world_minute": due_minute, "who": "S"})
            state["actions"][action_id] = started
            events.append(started_event)
            key = conflict_key(started)
            if key is not None:
                state["running"][action_id] = key
                window = _spawn_window(state, action_id, due_minute)
                if window is not None:
                    events.append(window)
        events.append(_due_event(state["actions"][action_id], tracker.at(due_minute)))
    return events
