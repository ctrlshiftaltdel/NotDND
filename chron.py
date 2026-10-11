#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chron —— CHRON 内核（M9-C1 / C2）：世界时钟、时间单位与行动生命周期（纯函数）。

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

**本单边界**：不排程（队列 / 到期排序归 C3）、不压缩时间（归 C4）、不落库、不碰 IO；
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

# §4.1 的行动卡字段。前 15 个与 §4.1 草案逐字对应；末尾两个是 §4.4「中断 / 恢复」
# 落地所需的最小补充（§4.1 未列，但「用剩余耗时重排」必须有停点与进度口径）：
#   - `interrupt_minute`：**开始后**被打断的世界分钟（未开始即 `None`）；
#   - `progress_kept`：中断时进度是否保留（§4.4；`None` = 尚未中断过）。
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
)


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
               interrupt_conditions=(), idempotency_key=None):
    """构造一张**尚未提议**的行动卡草稿（§4.1；`status = None`）。

    提议本身也是一次转换：`transition(draft, "proposed", {"world_minute": ...})`——
    该事件会写下 `created_minute`（= 提议时的世界时间）。
    `blocks` 是 `depends_on` 的反向冗余索引（§4.1），由调度器（C3）维护，此处留空列表。

    `time_cost` 由外部（PRISM）传入，CHRON **只读不判**（§7 边界）。
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
