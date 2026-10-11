# CHRON · 半同步世界时间系统 · 设计方案

> 状态：Draft。本文只定设计与拆单，**不含实现**。
> 日期：2026-10-10。
> 依据：[`GDD.html`](GDD.html) §5.1 / §6（CHRON 半同步世界时间系统）/ §8.4 / §8.5 / §9.3 /
> §19 / §24.1 / §24.2；[`GDD-BASELINE.md`](GDD-BASELINE.md) §4.3 第 4 条与 §7.3（本单）；
> 仓库实况（`master @ fa5366c`，2026-10-10）。
> 读者：Master Agent。审过之后按文末「实现切片建议」拆成 Issue，再指派 Execution Agent。
> 预定落点：仓库根 `CHRON-DESIGN.md`（新建）。
> **依赖（已对齐，2026-10-10）**：持久化契约跟随 `STATE-DESIGN.md`（#136，§6 / §9 / §11 / §12）；
> 排程钩子与 `ACTION-DESIGN.md`（#137）§8.2 已写清两层映射（§4.3）。第 8 节的存储接口假设已按 STATE 定稿逐条核对。

本文不是规则正文，不替代 `docs/`。规则真相仍在 `prism_core.py`；空间归 ATLAS；
**世界事实的唯一提交出口是权威状态层（`state.py`）**——CHRON 只安排什么时候发生，
不自己改世界。本文提出的口径若与 `docs/` 有出入，**`docs/` 的修订单独开 PR**（见第 2.6 节）。

---

## 0. Master Agent 怎么用这份方案

1. 先读第 2 节。**时间单位换算必须先钉死**（基线 §4.3 第 4 条），否则后面所有调度都是沙上建塔。
   第 2.3 节是建议口径，第 2.6 节列出「要改哪些文件、为什么不在本单改」。
2. 第 4 节是全转换表，第 5 节是调度器，第 6 节是事件屏障。不同意第 2.3 节的某个取值，
   或不同意第 10 节的某条风险处置，**先改本文再拆单**。
3. 文末「实现切片建议」的 C1–C6 可以照抄开 Issue。**C1–C4 同占 `chron.py`，彼此串行**；
   C5 依赖 `STATE-DESIGN.md`，C6 依赖 `ACTION-DESIGN.md` 与 C1–C4。
4. 开单时打上每条写明的难度标签与能力标签。可改 / 禁改路径不要放宽。
5. Execution Agent 不合并。审查用四行建议。

---

## 1. 目标与非目标

### 1.1 目标

给「世界时间」建一套**可测试、可持久化、可恢复**的调度内核，落实 GDD §6：

- **一个权威世界时钟**（GDD §6.1「一个权威世界时钟」；§24.1「世界时钟只有一个权威来源」）。
- **行动异步执行**（GDD §6.1）：长行动不锁玩家（§6.5），到期结算不依赖玩家在线。
- **关键事件局部同步**（GDD §6.1）：只在互相影响的行动之间开一个**事件窗口**。
- **安全的时间压缩 + 事件屏障**（GDD §6.1 / §6.6）：能快进，但**不跳过会改变决策或世界事实的事件**。
- **重启恢复 + 幂等**（GDD §6.9）：唯一 ID、重复投递不重复副作用、服务器重启后排程可恢复。
- 与 PRISM / ATLAS / 状态层 / 导引者（LOREX）的**职责边界**（GDD §6.8、基线 §4.2）。

### 1.2 非目标

- **不做多人房间本身**（连接、成员、频道、认知分离、重连）——那是 `MULTIPLAYER-DESIGN.md`（M10）。
  CHRON 只对外提供「共同事件窗口」与「唯一时钟」两类接口，不实现房间。
- **不做 NPC 决策**（需求 / 目标 / 计划 / 日程）——那是 NEURA（M11）。CHRON 只按 NEURA 交来的
  行动任务排程，不替它决定做什么。
- **不做规则判定**：基础耗时、检定、战斗轮规则由 PRISM / 世界规则包给（GDD §6.8 表）。
- **不做空间计算**：行程的尺度归 ATLAS；CHRON 只接收「档位 → 分钟」。
- **不落库**：CHRON 不直接写世界事实，一律经权威状态层提交（GDD §5.1、基线 §4.2）。
- 不改 `docs/`、`data/**`、`prism_*.py`、`atlas*.py`、`notdnd_web.py`、`static/**`、`tests/**`。

### 1.3 一句话设计

> **世界时间是一条只增不减的整数分钟轴；每个行动是一张带「预计到期」与「中断条件」的任务卡；
> 调度器按「到期分钟 → 优先级 → 提交序」稳定排序到期卡；当中途出现会改变玩家决策或世界事实的
> 事件时，时间压缩在屏障前停下；一切提交经权威状态层，同一次到期用幂等键防重。**

---

## 2. 时间单位与换算口径（先把口径钉死）

### 2.1 三种时间概念（GDD §6.2）

GDD §6.2 把时间分成三种，各有负责人。CHRON 要做的第一件事就是让这三者**可互相换算**：

| 时间类型 | 定义 | 例子 | 负责人（GDD §6.2） |
|---|---|---|---|
| **世界时间** World Time | 统一日历与时间轴 | 第 100 天 10:00 | **CHRON 维护** |
| **行动时间** Action Time | 某行动的开始 / 预计结束 / 实际结束 / 中断 | 研究 3 小时，可能被打断 | **CHRON 调度**；PRISM 给基础耗时 |
| **规则时间** Rule Time | 某规则系统定义的轮次 / 窗口 / 时长 | 战斗轮、施法时间、反应窗口、短歇 / 长歇 | **PRISM 定义**；CHRON 映射到世界时间 |

一句话：**PRISM 说「这件事要花多少规则时间」，CHRON 把它换算成世界分钟并安排什么时候开始 / 结束。**

### 2.2 现状：口径不一致（必须先在本文钉死）

三处证据，互相冲突，**其中两处在已合并的数据层里**：

| # | 来源 | 原文 | 含义 |
|---|---|---|---|
| A | `docs/system/01-core.md:348`（镜像到 `data/system/action_economy.json:68`，`id: period`） | 「**时段** = 10 分钟」，用途「搜查一个房间、闲聊一段、短距移动」 | 时段 = **10 分钟** |
| B | `docs/system/03-interaction-layer.md:29`（镜像到 `data/system/watch.json`） | 「虚构时间以**时段（Watch）**为最小单位」，四段：晨 05–09 / 昼 09–17 / 昏 17–21 / 夜 21–05 | 时段 = **一天四段**（实际长 4/8/4/8 小时） |
| C | `docs/system/03-interaction-layer.md:46`（镜像到 `data/system/travel.json`） | 短程 **1 时段** / 中程 **半天（2 时段）** / 远程 **全天 +** | 时段 ≈ 6 名义小时 |
| D | `notdnd_web.py:236` | `BAND_HOURS = {"short": 1, "medium": 2, "long": 4, "dangerous": 4}`，注释「行程档 → **时段数**」 | 变量名说 hours，注释说时段数；1/2/4 实为**时段数** |

**冲突点**：`时段` 一词同时指 **10 分钟**（A）和 **四分之一天 / 6 小时**（B、C、D）。
B/C/D 相互自洽（「2 时段 = 半天」把 1 时段钉在 6 名义小时），**A 是唯一的异类**。

**其余同向证据**（都用「时段 = Watch」义）：`data/system/session.json:28`（每个时段一次风险判定）、
`data/system/investigation.json:69-71`（每 2 个时段 / N 个时段后 / 每时段恶化）、
`docs/system/04-conductor-os.md:104/151/978`。

`tests/test_notdnd_web.py:774` 还断言「时段数必须与 `data/system/travel.json` 一致」——
即现有回归**锁住的是 B/C/D 义**。

### 2.3 建议口径（本文采纳的「唯一真相」）

> **CHRON 内部一律以「整数分钟」为权威单位；产品层只保留「时段（Watch）」一个名词；
> 不再把「时段」用作 10 分钟。**

1. **权威刻度 = 整数分钟。** 世界时间 = 自战役纪元（第 1 天 00:00）起的分钟数，非负整数。
   日历另以 `第 D 天 HH:MM`（D ≥ 1）呈现。**1 天 = 24 小时 = 1440 分钟。**
2. **时段（Watch）** = 一天的四段，**不等长**，边界取 `data/system/watch.json`：
   晨 240 分 / 昼 480 分 / 昏 240 分 / 夜 480 分。**不等长是刻意的（叙事节奏）**，
   因此**任何换算都不以「时段」为单位做乘法**；需要定长时一律用**分钟**或**小时**。
   - 「时段」只回答两个问题：现在是**哪一段**（叙事用词）、本段还有**哪几件大事**没做（预算）。
3. **行程与预算口径的时段 = 名义 6 小时 = 360 分钟。**
   这是被 `travel.json`「中程 = 半天（2 时段）」唯一确定下来的取值，与晨/昼/昏/夜的实际边界无关。
4. **原 A 处的「时段 = 10 分钟」并入 `watch.json` 的 `budget.minor_minutes = 10`**，
   表述改成「一件**小事** ≈ 10 分钟」，**不再单列为一个叫「时段」的单位**。
   CHRON 内部把 10 分钟作为一个**排程常数**（最小步长），但**不对外叫「时段」**——
   避免与历史同名、也避免与传统「刻 = 15 分钟」撞车而另造名词。

### 2.4 换算表（CHRON 常量）

```
SECONDS_PER_MINUTE = 60
MINUTES_PER_HOUR   = 60
MINUTES_PER_DAY    = 1440          # 1 天
WATCH_SPANS = {                    # 实际边界，取自 data/system/watch.json
    "dawn":  240,   # 晨 05:00–09:00
    "day":   480,   # 昼 09:00–17:00
    "dusk":  240,   # 昏 17:00–21:00
    "night": 480,   # 夜 21:00–05:00
}
NOMINAL_WATCH_MINUTES = 360        # 行程/预算口径：1 时段 = 6 名义小时
MINOR_ACTION_MINUTES  = 10         # 小事；最小排程步长（来自 watch.json budget）
MAJOR_ACTION_MINUTES  = (60, 180)  # 大事：1–3 小时（watch.json budget）
SESSION_WATCHES       = (3, 6)     # 一个典型会话 3–6 时段（watch.json pacing）
```

| 换算 | 值 |
|---|---|
| 1 小时 | 60 分钟 |
| 1 天 | 1440 分钟 |
| 1 时段（行程 / 预算口径） | 360 分钟（名义） |
| 1 时段（日历口径） | 240 / 480 / 240 / 480 分钟，**因段而异** |
| 1 会话（现实时间） | 2–4 小时 → 虚构 **3–6 时段** ≈ 1080–2160 名义分钟 |

**规则时间 → 世界分钟（PRISM 提供数值，CHRON 只求和）：**

| 规则单位 | 世界分钟 | 来源 |
|---|---|---|
| 节拍（战斗中的一次行动） | **不硬编码**；由规则包给「一轮 = N 秒」，CHRON 求和 | GDD §9.3；`docs/system/01-core.md:347` |
| 喘息 | 1 | `docs/system/01-core.md:708` |
| 短歇 | 60 | `docs/system/01-core.md:709` |
| 长歇 | 480（8 小时） | `docs/system/01-core.md:710` |
| 间幕 | **会话之间**；由 CHRON 在切会话时按配置推进（默认 12 小时 / 720 分钟） | `docs/system/01-core.md:711` |

**行程档 → 世界分钟（对齐 `data/system/travel.json` 与 `BAND_HOURS`）：**

| 档位 | 进度 | 分钟 | 现有 `BAND_HOURS` |
|---|---|---|---|
| 短程 | 1 时段 | 360 | 1 |
| 中程 | 2 时段（半天） | 720 | 2 |
| 远程 | 4 时段（全天 +） | 1440 | 4 |
| 危险穿越 | 视情况 | **≥ 1440**，逐名义时段一次风险判定 | 4 |

> 注意：`BAND_HOURS` 的名字（hours）与它的含义（时段数）不符——**名字是错的，值是本文口径**。
> 改名的动作落在**另一个 PR**（见 2.6），CHRON 只吃「档位 → 分钟」的映射表。

### 2.5 世界特有的时间流速

`data/worlds/entropic-net.json:432` 已埋了一条**世界内时间膨胀**：
「网的十分钟是现实的一小时」。这说明世界模组**可以自带时间流速**。

CHRON 的处理：世界时钟存的是**单一权威轴**，各世界 / 各域的时间流速差异在**域**这一层收敛，
不产生第二条可写时间线（GDD §6.1「不能拥有互不相容的权威世界时间」）。第一版**只预留字段**
`rate_domains`，不实现域间的并行换算——它属 M11（活体世界）与内容切片，不在本单与 M9 实现内。

### 2.6 落地这些口径需要另一个 PR（本单不做）

第 2.3 节的建议若获采纳，下列**各文件的修订须单独开 Issue / PR**（`AGENTS.md` §8：
改 `docs/` 属内容变更、单独开单，不夹带实现 PR）：

| 文件 | 现状 | 建议修订 |
|---|---|---|
| `docs/system/01-core.md:348` | 「时段 = 10 分钟」 | 改为「**小事** ≈ 10 分钟」，与 `watch.json.budget.minor_minutes` 对齐；不再把「时段」用作 10 分钟 |
| `docs/system/03-interaction-layer.md:29` | 「时段（Watch）为最小单位」 | 明确「时段」**只**指一天四段；行程耗时改用小时 / 分钟表述（短程 6 小时、中程 12 小时…） |
| `data/system/action_economy.json:68` | `{"id":"period","name":"时段","duration":"10 分钟"}` | 随 01-core 同步（`duration` 与 `name` 口径） |
| `data/system/travel.json` | `duration: "1 时段" / "半天（2 时段）"` | 补 `minutes` 字段（360 / 720 / 1440），保留人类可读的 `duration` |
| `notdnd_web.py:236` | `BAND_HOURS`（名不符实） | 改名 `BAND_MINUTES`，值 360 / 720 / 1440 / 1440；注释同步 |
| `tests/test_notdnd_web.py:774` | 断言「时段数一致」 | 随改名同步断言口径 |

> **本设计单不改以上任何一处**（禁改路径）。CHRON 的实现在内部用**分钟常数**表达，
> 因此即使上述口径 PR 尚未合并，CHRON 也能先落地、后对齐。

---

## 3. 权威世界时钟（World Clock）

### 3.1 表示

```
world_minute: int      # 自战役纪元起的分钟数，≥ 0，权威
day  = world_minute // 1440 + 1
tod  = world_minute %  1440          # 当天分钟
hh:mm = tod // 60 : tod % 60
watch = watch_of(tod)                # 由 WATCH_SPANS 的边界判定（不是整除！）
```

`watch_of(tod)` 必须按 `WATCH_SPANS` 的**实际边界**判定（05:00 / 09:00 / 17:00 / 21:00），
**不能用 `tod % 360` 之类整除**——因为四段不等长。

**事件封套的 `world_time`（形状由 CHRON 定义，唯一写法）**：`STATE-DESIGN.md` §5.1 把 `world_time`
定为**不透明字段**、状态层只搬运不改写（§12.3）；其**规范形状由本文钉死**，CHRON **在提交时传入**：

```json
{ "world_minute": 17310, "day": 13, "tod": 30 }
```

- `world_minute`：**权威值**（§3.1 的整数分钟，≥ 0）。
- `day` / `tod`：**派生只读**（§3.1 的公式现算），仅为人类可读与审计便利；**任何判断只认 `world_minute`**。
- 上例推导：`day = 17310 // 1440 + 1 = 13`，`tod = 17310 % 1440 = 30`（即第 13 天 00:30）。
  **公式是权威规则**；`day` / `tod` 一律由 `world_minute` 推导，**不得为迁就任何示例而改动公式**。
- 创世 / 尚未接入 CHRON 时 `world_time` 允许 `null`（`STATE-DESIGN.md` §5.1 / S8）。
- 该对象是**提交入参**（`state.Handle.commit(..., world_time=…)`），不是 CHRON 的存档块字段；CHRON 存档块只存
  `world_minute`（§8.1）。

### 3.2 单调性、唯一来源、客户端不可信

- 世界时间**只增不减**；任何「回拨」都是 bug（压缩是「向前推」，不是「回退」）。
- **唯一权威来源**是服务端（GDD §24.1）。**浏览器客户端时钟不参与排程**（GDD §6.9）。
- 客户端只显示「当前阶段 / 预计完成 / 剩余」，不能上传时间。

### 3.3 推进方式：事件驱动，不靠墙钟

CHRON **不做**「每 N 秒给世界加 1 分钟」的后台滴答。世界时间**只在提交事件时前进**：

- 玩家 / NPC 提交一个带 `time_cost` 的行动 → 世界时间推进到该行动的开始 / 结束。
- 时间压缩（第 6 节）→ 一次推进到下一个**屏障**前的第一个到期点。
- **游戏内时间与现实时间分离**（GDD §6.9）；现实时间只用于网络重试 / 超时，**不是权威**。

> 好处：无玩家在线的房间**不会**因为现实时间流逝而自动「过了一天」，
> 也就不会出现「玩家下线后角色承受不可预知损失」——这正是 GDD §6.7 要避免的。

### 3.4 「无人在线是否推进」的默认值

GDD §6.7 给了默认建议：「房间活动时推进 + 可配置离线推进」。CHRON 取：

- **默认 `offline_advance = False`**（基线 §8 开放问题之一）：无人在线时不推进世界时间。
- 开关与速率由房间设置给，**实现落在 M10**；CHRON 只提供 `advance_to()` 这个**纯推进入口**，
  不自己判断「有没有人在线」。

---

## 4. 行动生命周期（Action Lifecycle）

### 4.1 行动任务对象（草案）

```
Action {
  action_id:     str        # 唯一 ID（提交方生成，服务端校验唯一）
  actor:         str        # 发起者（玩家角色 / NPC 的稳定 ID）
  kind:          str        # 行动种类（决定走哪条 PRISM 规则）
  status:        str        # 见 4.2
  created_minute:int        # 提议时的世界时间
  start_minute:  int|None   # 安排开始（scheduled 后才有）
  due_minute:    int|None   # 预计结束（= start + time_cost）
  end_minute:    int|None   # 实际结束（结算时写）
  time_cost:     int        # 基础耗时（分钟；PRISM 给）
  priority:      int        # 排序用（越大越先；默认 0，规则包可给）
  depends_on:    [action_id]# 依赖：这些行动完成前不得开始
  blocks:        [action_id]# 反向索引（由 depends_on 生成，冗余便于查询）
  interrupt_conditions: []  # 中断条件（见 5.4）
  idempotency_key: str      # 幂等键（见 8.3；提交时传给状态层的 idempotency_key）
  events:        [event_id] # 该行动产出的可追溯事件（GDD §6.4 每次转换都产事件）
}
```

与 GDD §8.4「行动结果契约」的关系：`time_cost` 字段同源（`{"value": 20, "unit": "minute"}`），
CHRON 只读其中的**分钟数**；`status` 词表见 4.2；`interrupt_conditions` 由 CHRON 解释。

### 4.2 状态与全转换表（GDD §6.4）

GDD §6.4 的八个状态：
`proposed / scheduled / running / blocked / interrupted / completed / cancelled / failed`。

**全转换表**（「谁触发」：P=玩家/NPC，S=调度器，R=PRISM，T=状态层）：

| 从 | 到 | 触发条件 | 谁 | 产出事件 | M8 同步路径是否支持 |
|---|---|---|---|---|---|
| — | `proposed` | 提出行动（NL 解析或直接 API） | P | `action.proposed` | ✅ |
| `proposed` | `scheduled` | 校验通过、算出 `time_cost`、安排开始 | R+S | `action.scheduled` | ⛔（M8 直接 running） |
| `proposed` | `failed` | 校验不通过（条件不成立） | R | `action.failed` | ✅ |
| `proposed` | `cancelled` | 玩家撤回 / 世界变化使其失去意义 | P/T | `action.cancelled` | ✅ |
| `scheduled` | `running` | 世界时间到达 `start_minute` | S | `action.started` | ✅（M8：立即） |
| `scheduled` | `cancelled` | 开始前被取消 | P/T | `action.cancelled` | ⛔ |
| `scheduled` | `blocked` | 前置条件变为不满足（可恢复） | S | `action.blocked` | ⛔ |
| `scheduled` | `interrupted` | 开始前中断事件命中 | S | `action.interrupted` | ⛔ |
| `blocked` | `scheduled` | 前置条件恢复 | S | `action.unblocked` | ⛔ |
| `blocked` | `cancelled` | 放弃 | P | `action.cancelled` | ⛔ |
| `running` | `completed` | 到达结算点，PRISM 判成功 | R | `action.completed` | ✅ |
| `running` | `failed` | 到达结算点，PRISM 判失败 | R | `action.failed` | ✅ |
| `running` | `interrupted` | 中断事件命中（GDD §8.5） | S | `action.interrupted` | ⛔ |
| `running` | `cancelled` | 玩家中途取消 | P | `action.cancelled` | ⛔ |
| `interrupted` | `scheduled` | 恢复到剩余进度（重排开始） | S | `action.resumed` | ⛔ |
| `interrupted` | `completed` / `failed` | 就地结算已完成的部分（GDD §8.5） | R | `action.completed` / `action.failed` | ⛔ |
| `interrupted` | `cancelled` | 中断后放弃 | P | `action.cancelled` | ⛔ |
| `completed` / `failed` / `cancelled` | — | **终态，不再转换** | — | — | ✅ |

**不变式**：
- **每次转换都是一条事件**（GDD §6.4「每次转换都应产生可追溯事件」）。
- 终态不可逆；「后续计划」是**新行动**，不是原行动的新状态。
- M8 的同步路径只走 `proposed → running → completed/failed`（基线 §4.3 第 2 条），
  **不需要** `scheduled`/`blocked`/`interrupted`；M9 在同一份契约上补齐异步路径——
  两次实现**不改状态词表**，只增加可达转换。

### 4.3 与 M8 排程钩子的对接（**两层**：管线钩子 ↔ 调度器入口）

基线 §5 与 §7.2 要求 `ACTION-DESIGN.md` **预留排程钩子**，M9 不需改动契约即可接入。
**已与 `ACTION-DESIGN.md` §8.2 对齐，写清为两层**（不是一层，签名不合并）：

- **L1 · 管线钩子（`ScheduleHook`，`ACTION-DESIGN.md` §8.2 定义）**：结算管线在状态层提交**之前**调用的鸭子类型对象；管线不 import `chron`。CHRON 用**适配器**实现它。
- **L2 · 调度器公开入口（本文定义，`chron.py`）**：由游戏循环 / 网页层 / M10 驱动，**不由结算管线直接调用**。

**CHRON 需要的输入（L1 经提案透传）**：

```
# M8 侧写（提案里带的字段，ACTION-DESIGN §4.2 / §8.2）
"time_cost": {"value": int, "unit": "minute"}     # GDD §8.4
"schedule_hint": {                                 # 可选；ACTION-DESIGN §4.2 新增
    "can_defer": bool,        # 是否允许异步（长行动）
    "depends_on": [action_id],
    "priority": int
}
```

**L2 公开入口（M9 实现的签名；`chron.py`）**：

```
schedule(action) -> action_id          # 建任务，安排 start/due
advance_to(world_minute) -> [event]    # 推进到某分钟，返回途中触发的到期事件
next_barrier(from_minute) -> int|None  # 下一个屏障分钟（第 6 节）
cancel(action_id, at_minute) -> event
```

**L1 ↔ L2 映射（M9 的适配器按此实现，不改 `ACTION-DESIGN.md` 的契约）**：

| L1（`ScheduleHook`，ACTION §8.2） | L2（本文 §4.3） | 映射规则 |
|---|---|---|
| `enqueue(proposal, draft, *, base_seq)` | `schedule(action) -> action_id` | 适配器用 `proposal` + `draft` 构造 `Action`（`time_cost` 折成整数分钟，`schedule_hint` 透传），调 `schedule`；返回 `mode="async"`、`task_id=action_id`、`start_at=Action.start_minute`、`expected_end_at=Action.due_minute` |
| `enqueue(...)`（`can_defer == False`） | —（不进队列） | 返回 `mode="sync"`，**不**调 `schedule` |
| `cancel(task_id)` | `cancel(action_id, at_minute) -> event` | `task_id` 即 `action_id` |
| —（L1 不暴露） | `advance_to` / `next_barrier` | 由游戏循环 / 网页层 / M10 驱动，**不挂 `ScheduleHook`** |

**字段名对齐（两文档一致）**：`time_cost = {"value","unit"}`、`schedule_hint.can_defer` / `depends_on` / `priority`、`barrier`（ACTION §8.2 返回 ↔ 本文 §6.2 屏障）、`resume_token`（ACTION 保留；CHRON 当前不消费，中断重排见 §4.4）。**`base_seq` 为提交入参**（`STATE-DESIGN.md` §12.1），不在本映射内。

**关键约定**：`can_defer = False` 的行动走 M8 的**同步结算**（不进调度队列）；
`can_defer = True` 或带 `interrupt_conditions` 的行动才进 CHRON。默认 `can_defer = False`，
保证 M8 行为不变。

### 4.4 中断语义（GDD §8.5）

GDD §8.5 要求中断必须有明确规则：**已消耗的时间与资源是否返还、进度是否保留、角色是否承担风险、
后续如何恢复**。CHRON 只负责**机械部分**，把口径留给规则包：

| 问题 | CHRON 的字段 / 行为 | 口径归属 |
|---|---|---|
| 已消耗时间是否返还 | `interrupt_refund: "none" | "partial" | "full"`（默认 `none`） | PRISM / 规则包 |
| 已消耗资源是否返还 | 由 PRISM 结算时决定 | PRISM |
| 进度是否保留 | `progress_kept: bool`；为真时 `interrupted → scheduled` 用剩余耗时重排 | PRISM 给，CHRON 执行 |
| 角色是否承担风险 | `interrupt_risk: [...]`（触发即交给 PRISM 结算） | PRISM |
| 如何恢复 | `interrupted → scheduled`（重排）或 `→ cancelled` | 玩家 / 规则包 |

---

## 5. 调度器（Scheduler）

### 5.1 数据结构

```
chron = {
  world_minute: int,
  actions:  { action_id: Action },
  due_heap: [ (due_minute, -priority, created_seq, action_id) ],   # 最小堆，稳定排序
  deps:     { action_id: [action_id, ...] },      # 依赖图（可由 Action.depends_on 重建）
  windows:  [ Window ],                            # 进行中的共同事件窗口（5.4）
  seq:      int,                                   # 已提交事件序号
}
```

`due_heap` 是**派生索引**，可由 `actions` 全量重建——所以它**不必**落盘（见 8.1）。

### 5.2 到期排序（稳定是硬要求，GDD §6.9）

同一到期分钟可能有多张卡。排序键（**字典序**）：

```
(due_minute, -priority, created_seq, action_id)
```

- `due_minute` 升序：先到先办。
- `priority` 降序：规则包可给高优先级（例如「中毒每时段恶化」压过「闲聊」）。
- `created_seq` 升序：先提出的先办（**确定性**，不随插入顺序 / 哈希漂移）。
- `action_id` 升序：最后的确定性 tie-break。

> **为什么必须显式 tie-break**：GDD §6.9「事件顺序要稳定；同时发生的事件应有明确的排序、
> 优先级或冲突解决规则」。裸用堆 / 字典遍历会因实现细节抖序，测试也就抓不住回归。

### 5.3 依赖

- `depends_on` 为真时，行动**不进入 due_heap**，直到全部依赖 `completed`。
- 依赖的行动 `failed` / `cancelled` → 依 `depends_on` 语义处理：
  默认 **级联取消**（转为 `cancelled`，事件里带 `reason: dependency_failed`）；
  若规则包标 `on_dep_fail: "keep"`，则改判 `blocked`。
- **无环**是硬约束：建图时检测环，有环即拒绝该提案（`proposed → failed`）。

### 5.4 事件窗口（局部同步，GDD §6.1 / §6.2）

当两个及以上行动**互相影响**时，只为**相关参与者**开一个 `Window`：

```
Window {
  window_id, participants: [actor_id], place: place_id|None,
  resource: resource_id|None, open_minute, close_minute,
}
```

进入窗口的情形（GDD §6.1）：同一场战斗、同一场谈判、争夺同一物品、同一地点同时遭遇危险。
窗口内：
- 参与的玩家必须**同步**推进（每人一轮）；
- 窗口外的行动**照常异步**，不因窗口被冻结；
- 窗口开关本身是**事件**（`window.opened` / `window.closed`）。

### 5.5 冲突解决（GDD §6.8）

CHRON **只识别**「这两个行动在时序上需要协调」，**不判胜负**：

- 识别依据：同一 `place`、同一 `resource`、或一方 `blocks` 另一方。
- 识别后：**开窗口**交给 PRISM 按规则与有效状态得出结论；
  物品归属 / 伤害 / 死亡等**事实**由权威状态层**原子校验**提交（GDD §6.8 表；§19.3）。

---

## 6. 时间压缩与事件屏障（Time Compression & Event Barrier）

### 6.1 什么时候可以压缩（GDD §6.1 / §6.6）

CHRON 可以批量推进时间 **当且仅当**该时段内：

1. 没有**重要交互**（没有玩家输入待处理、没有待确认的 `proposed`）；
2. 没有**冲突**（没有进行中的窗口、没有需要协调的共同目标）；
3. 没有**因果依赖**（推进不会使某个行动的结果失去意义）。

满足时，压缩可**少跑无关模拟步骤**，但**不能删除会影响因果链的事件**（GDD §6.6 原话）。

### 6.2 屏障判据（暂停自动快进的充要清单）

压缩推进过程中，**遇到下列任一情形必须在它之前停下**：

| # | 屏障 | 判据（机读） | GDD |
|---|---|---|---|
| B1 | **进入战斗** | 到期事件类型 ∈ `{combat.start}`；或某行动 `kind = combat` 开始 | §6.6、§9.3 |
| B2 | **发现关键线索** | 事件 `discovered_facts` 含 `significant: true` | §6.6 |
| B3 | **玩家角色安全受威胁** | 事件的 `risk` 触及某 PC 的伤害 / 死亡 / 被俘 | §6.6 |
| B4 | **角色遭遇** | 新 NPC / 另一玩家在同一 `place` 出现（`encounter` 事件） | §6.6 |
| B5 | **世界状态冲突** | 两行动争同一 `resource` / 同一 `place`（5.5 的识别命中） | §6.6 |
| B6 | **玩家有未决决策** | 存在 `status = proposed` 且 `needs_confirmation = true`（GDD §8.3 风险分级） | §6.5 |
| B7 | **行动转 blocked / interrupted** | 状态机产生这两类转换 | §6.4 |
| B8 | **有行动到期** | `due_minute ≤ 目标分钟`——到期本身**总是**要停（这是结算点） | §6.4 |

**非屏障**（可压缩）：赶路、休息、等待、无交互的 NPC 背景活动、资源再生、无风险的例行事务。

### 6.3 压缩算法

```
advance_to(target_minute):
    loop:
        nxt = min(最早到期分钟, 最早窗口事件, 最早屏障时间)   # 用 5.2 的稳定排序取
        if nxt == None or nxt > target_minute: break
        if is_barrier(nxt): 
            world_minute = nxt            # 停在屏障**之前**（nxt 本身不执行）
            return [屏障事件]
        fire_all_due_at(nxt)              # 全部同分钟到期卡按 5.2 顺序执行 + 提交
        world_minute = nxt
    world_minute = target_minute
    return 途中事件
```

**硬约束**：
- 停在屏障**之前**（屏障那一分钟的内容留给玩家）。
- `fire_all_due_at` 必须**逐条**走状态层提交（不批量绕过校验），且带幂等键（第 9 节）。
- 压缩**不写**「跳过」的模拟步骤为事件——只写**真实发生**的事件；
  被压缩掉的无关模拟**不产生**因果事件（GDD §6.6「少运行无关模拟步骤」）。

### 6.4 不允许压缩的情况

- 有任何窗口进行中（GDD §6.1：窗口内必须同步）。
- `offline_advance = False` 且当前无人在线（3.4）。
- 目标分钟跨越了屏障（B1–B8 任一）。

### 6.5 测试计划（屏障）

1. **行程穿屏障**：一段 6 小时（360 分钟）的短程，途中第 180 分钟有 `combat.start`。
   断言：`advance_to(360)` 返回时 `world_minute == 180`，**不是** 360；180 之前的到期事件全部已触发。
2. **无屏障可压缩**：同样 6 小时行程但无任何屏障 → `world_minute == 360`，中途事件按序触发。
3. **屏障不删因果**：把「到达时会发现线索」的动作排在 200 分钟，
   屏障在 180 → 断言线索事件**没有**被提前触发，也没有被丢弃。
4. **到期总停（B8）**：即使没有任何 B1–B7，只要有行动到期也必须停。
5. **屏障优先级**：同一分钟既有到期又有屏障 → 屏障优先（停）。

---

## 7. 与 PRISM / ATLAS / 状态层的边界（GDD §6.8）

| 职责 | 责任方 | 对 CHRON 的约束 |
|---|---|---|
| 行动基础耗时、检定、战斗轮规则 | **PRISM / 世界规则包** | CHRON 只收 `time_cost`（分钟），**不重算** |
| 安排行动的开始 / 结束 / 队列 / 依赖 | **CHRON** | 不重新判定胜负，不绕过规则判断 |
| 判断两个行动是否在时序上需要协调 | **CHRON** | 识别共同目标 / 地点 / 资源 / 因果依赖（5.5） |
| 解决需要规则判断的争夺、攻击、竞争 | **PRISM** | CHRON 开窗口，不判结果 |
| 提交物品归属、伤害、死亡等事实 | **权威状态层** | 原子校验；防重复提交（§19.3） |
| 空间距离 / 可达性 / 行程尺度 | **ATLAS** | CHRON 只接「档位 → 分钟」，不读地图 |
| 叙事呈现（玩家有资格知道什么） | **LOREX / `prism_guide.py`** | CHRON 不改叙事文本 |
| NPC 想做什么 | **NEURA** | CHRON 只排程它交来的行动 |

**依赖方向（基线 §4.2 硬约束）**：`chron.py` **不 import** 任何业务模块；
它只依赖权威状态层的**提交 / 读事件**接口，以及 PRISM 传入的纯数据。

---

## 8. 持久化与恢复（**已按 `STATE-DESIGN.md` 对齐，2026-10-10**）

> ✅ **本节已对齐** `STATE-DESIGN.md`（#136）§6 / §9 / §11 / §12：`chron` 块是状态层快照里的
> **不透明块**（`blocks.chron`，STATE §6.1 / §11），存储接口按 STATE §12.1 定稿核对；`seq` / 重放 / `.bak`
> 口径均以 STATE 为准（逐条见下）。CHRON 的**语义**不依赖具体存储实现，只有**接线**依赖。

### 8.1 `chron` 存档块（已对齐）

`chron` 是**状态层快照里的一块不透明块**：落在 `snapshot.json` 的 `blocks.chron`（`STATE-DESIGN.md`
§6.1 / §11），与 `rules` / `atlas` / `guide` **同为 `blocks` 内层块**，**state.py 原样存取、不解析**（STATE S4 / S8）。
CHRON 仍按 `notdnd_web.py` `_SESSION_DEFAULTS` 的「新增字段须给默认值 + 惰性迁移」约定惰性补默认（STATE §10.1）。

```
chron: {
  version: 1,
  world_minute: int,          # 权威世界时间（§3.1）
  seq: int,                   # = snapshot 顶层 seq（STATE §6.1，状态层已折入的最大事件序号）；恢复以状态层为准
  actions: { action_id: Action },   # 见 4.1（终态行动可裁剪，见 8.4）
  windows: [ Window ],
  idempotency: { idempotency_key: committed_seq },  # 已提交的幂等键（同名口径见 STATE §7；防重放）
  # due_heap 不落盘：加载时由 actions 重建（5.1）
}
```

**接口（已对齐 `STATE-DESIGN.md` §12.1 的具体形态，不再「假设」）**：
- **提交**：`state.Handle.commit(*, base_seq, idempotency_key, …, world_time=…) -> CommitResult{status, commit_id, seq_from, seq_to, event_ids, state_digest, error}`（STATE §12.1 / §8.1）。
- **重放**：`state.Handle.replay(upto_seq=None)`（STATE §12.1）按 `seq` 折入事件（STATE §6.2 / §9），CHRON 从中取 `blocks.chron` 重建队列（STATE §12.3）。
- **按序号范围读事件**：事件日志可读 `seq > since` 的区间（STATE §12.4「snapshot + 事件尾部」；只读增量接口见 STATE §16 M7-4 的 `GET /api/events?since=<seq>`）。
- **版本 / 校验**：快照带 `schema_version` / `checksum`（STATE §6.1）；读到**更高**版本**拒载**（STATE S6）。

### 8.2 重启恢复时序（已对齐 STATE §9）

```
1. state.open / 载入快照 → 取 blocks.chron（缺 → 惰性补默认 world_minute = 0；STATE §5.1 允许 world_time = null）
   快照校验按 STATE §9：snapshot.json 校验不过 → 退 snapshot.json.bak → 都失败退创世并记 state_corrupt
2. 校验 world_minute ≤ 最后一条已提交事件的 world_time.world_minute（否则拒载并备份；§3.2 单调性）
3. 用 Handle.replay() 按 seq > snapshot.seq 折入事件（STATE §6.2 / §9）→ 重建 actions / windows / idempotency
4. 由 actions 重建 due_heap（按 5.2 排序键）
5. 从 world_minute 继续 advance_to()
```

### 8.3 幂等到期事件（GDD §6.9 / §24.1）

- 每张行动卡与每个到期事件有**唯一 ID**。
- 触发一个到期点时生成事件，幂等键（**即提交时传给状态层的 `idempotency_key`**，`STATE-DESIGN.md` §7 / §12.1）：
  `idempotency_key = f"{action_id}:{due_minute}:{kind}"`（确定性，重放得同键）。
- 收到已存在的 `idempotency_key` → **no-op**，不重复扣费 / 发奖 / 伤害（GDD §6.9、§24.1）。
- `idempotency` 表随 `blocks.chron` 落盘；长期可由事件日志重建并裁剪。

### 8.4 迁移与兼容（基线 §6 第 6 条、GDD §19.4）

- 加字段一律**只补不覆盖**（沿用 `_SESSION_DEFAULTS` 的惰性迁移，STATE §10.1）。
- `version` 递增时提供迁移函数；不可逆迁移**先备份**（口径同 STATE §4.1 / §6.3 / §10.2：检查点用 `snapshot.json.bak`，迁移用 `snapshot.json.pre-v0.bak`）。
- 裁剪规则：终态（`completed`/`failed`/`cancelled`）行动超过 N 天后折叠为事件引用；
  事件日志的归档编号见 STATE §4.1（`events.<n>.ndjson` + `archived_upto`；归档不删事实）。

---

## 9. 测试计划（对照 GDD §24.2「时间调度测试」行）

GDD §24.2 的关键案例：**长行动并行、任务取消、到期事件排序、时间压缩屏障、服务器重启恢复**。
逐条落点：

| 用例 | 断言要点 |
|---|---|
| 长行动并行 | 两个 `can_defer=True` 行动同时开始，互不阻塞；各自按 `due_minute` 结算 |
| 任务取消 | `scheduled`/`running` 取消产生 `action.cancelled`；资源按 `interrupt_refund` 处理 |
| 到期事件排序 | 同一 `due_minute` 的多卡按 `(-priority, created_seq, action_id)` 稳定排序，两次运行结果**全等** |
| 压缩屏障 | 6.5 的五条（停在屏障前、不删因果、到期总停…） |
| 重启恢复 | 8.2 时序；重启后 `world_minute` 与 `due_heap` 与重启前**全等** |
| 幂等 | 同 `idempotency_key` 投递两次，副作用只发生一次 |
| 唯一时钟 | 客户端上传的时间**不影响** `world_minute` |
| 离线底线 | 无 AI 配置时，`advance_to` / `schedule` 照常可用（基线 §6 第 3 条） |
| 迁移 | 老档（无 `chron` 块）载入 → 补默认 → 再存 → 新档可读 |

新增测试**不碰**现有 12 个脚本的断言（除 2.6 所列的 `test_notdnd_web.py:774`，
其口径修订走另一个 PR）。

---

## 10. 风险与未决问题

**风险**（Master Agent 审查时不能写「无」的那几条）：

1. **第 2.3 节的命名 / 取值是设计选择，不是事实。** 把「时段」定为 Watch、把 10 分钟并入
   `minor_minutes`，是本文的**建议**；它要改 `docs/` + `data/`（2.6 节），属独立 PR。
   **需人拍板**：是否接受「时段 = 一天四段」为唯一含义。CHRON 内部用分钟常数，
   这一拍板**不阻塞** C1–C4。
2. **`STATE-DESIGN.md` 已定稿**（#136，2026-10-10）。第 8 节已按其 §6 / §9 / §12 逐条对齐；
   状态层提供 `replay()` 与按序号读事件（STATE §12.1 / §12.4），恢复时序（8.2）无需改。**C5 前提已满足。**
3. **`ACTION-DESIGN.md` 已定稿**（#137，2026-10-10）。排程钩子（4.3）已与 `ACTION-DESIGN.md` §8.2 写清**两层映射**，字段名（`time_cost` / `schedule_hint` / `can_defer` / `barrier` / `resume_token`）一致。
4. **战斗与世界时间的映射常数未定**（2.4 的「节拍」）。CHRON **不硬编码**，由规则包给
   「一轮 = N 秒」。若规则包拿不出值，CHRON 需要一个兜底常数——**需人拍板**。
5. **压缩的「无关模拟」边界模糊**（§6.6 的表述是原则不是算法）。6.2 的判据是机读清单，
   但仍可能把「其实有关」的事件当无关而压掉 → 用 6.5 的测试与 §24.2 的回归兜。
6. **`BAND_HOURS` 改名连带测试**（2.6）。改名 PR 会动 `notdnd_web.py` 与
   `test_notdnd_web.py:774`，属独立单；在此之前两份口径并存。
7. **无新依赖、无密钥、无环境变量。** 本设计只增加 `chron.py` 与一个存档块，
   **不改** `docs/`、`data/`、现有 `.py`、`static/`、`tests/`。

**未决问题**（不阻塞 C1–C4，记录在案）：

- 战斗中的**非参与者**可做哪些并行行动（基线 §8 开放问题；GDD §25.2）→ 由 M10 权限模型 + 本文 5.4 回答。
- 房间无人在线时世界是否推进（基线 §8）→ 本文 3.4 给默认 `offline_advance = False`，开关落 M10。
- 哪些事件需**全员确认**、哪些可自动（基线 §8）→ GDD §8.3 风险分级 + M10 权限模型。
- 时间流速域（2.5，熵网「网内 10 分钟 = 现实 1 小时」）的第一版实现 → 归 M11 / 内容切片。

---

## Key Decisions

1. **权威单位是整数分钟。** 世界时间 = 自纪元起的分钟数，只增不减；日历以 `第 D 天 HH:MM` 呈现。
   1 天 = 1440 分钟。**客户端时钟不参与排程。**
2. **「时段」只指一天四段（晨/昼/昏/夜），不等长（240/480/240/480）；不做以「时段」为单位的乘法。**
   需要定长换算式时一律用分钟。原「时段 = 10 分钟」并入 `watch.json.budget.minor_minutes`，
   表述为「小事 ≈ 10 分钟」，不再单列单位。
3. **行程 / 预算口径的「时段」 = 名义 6 小时 = 360 分钟**，由 `travel.json`「中程 = 半天（2 时段）」
   唯一确定。行程分钟：短 360 / 中 720 / 远 1440 / 危险 ≥ 1440。
4. **`docs/`、`data/`、`notdnd_web.py` 的口径修订单独开 PR**（2.6 列了六个文件）；
   本设计单不改它们。CHRON 内部只认分钟常数，可先落地后对齐。
5. **八状态全转换表按 GDD §6.4 定齐；每次转换产事件；终态不可逆；后续计划是新行动。**
   M8 只走 `proposed → running → completed/failed`，M9 在同契约上补异步路径。
6. **到期排序键 = `(due_minute, -priority, created_seq, action_id)`**，确定性、可测。
   依赖图无环是硬约束；默认依赖失败级联取消。
7. **冲突只识别、不裁决**：CHRON 开**事件窗口**，胜负交 PRISM，事实交权威状态层原子提交。
8. **压缩在屏障前停下**；屏障 = B1–B8 的机读清单（战斗 / 关键线索 / PC 安全 / 遭遇 /
   世界冲突 / 未决决策 / blocked-interrupted / 到期）。压缩可少跑无关步骤，**不删因果事件**。
9. **世界时间只在提交事件时前进**，不做后台滴答；默认 `offline_advance = False`。
10. **每个到期事件带确定性幂等键** `action_id:due_minute:kind`；重放 no-op。
11. **`chron` 块是状态层快照 `blocks` 内的不透明块**（`STATE-DESIGN.md` §6.1 / §11，与 `rules`/`atlas`/`guide` 同级），
    加 `_SESSION_DEFAULTS` + 惰性迁移；存储接口**已按 `STATE-DESIGN.md` §12.1 对齐定稿**（第 8 节）。
12. **新模块 `chron.py`；不 import 业务模块；不落库；无新依赖、无密钥、无环境变量。**

---

## 实现切片建议

> 六条，一条一个意图。**C1–C4 同占 `chron.py`，彼此串行**（同一文件不并行，`AGENTS.md` §4）。
> **C5 依赖 `STATE-DESIGN.md`（#136）**，**C6 依赖 `ACTION-DESIGN.md`（#137）**——**两份均已定稿（2026-10-10）**，C5 / C6 的前提已满足（仍受「C1–C4 已合并」「同一文件不并行」约束）。
> 各单均可改路径只含 `chron.py`（+ 对应 `tests/`），禁改路径一律：
> `docs/**`、`data/**`、`prism_core.py`、`prism_guide.py`、`atlas*.py`、`notdnd_web.py`、`static/**`。
> 每条都必须真跑 `bash tests/run_all.sh` 与 `bash tests/content_firewall.sh`。

### C1 · CHRON 内核：世界时钟与时间单位（纯函数）

- **标签：** `难度：高`，`能力：编程`，`能力：逻辑`，`能力：测试`
- **依赖：** 无（可先开）。**不与 C2–C4 并行**（同 `chron.py`）。
- **文件：** 新增 `chron.py`，`tests/test_chron.py`
- **说明：** 只做第 2、3 节的纯函数：分钟常数（2.4 的表）、`watch_of(minute)`（按实际边界）、
  `to_calendar(minute)`、`from_calendar(day, hh, mm)`、`advance_clock(minute, delta)`（单调性断言）。
  不排程、不落库、不碰 IO。第一版 `rate_domains` 只留字段不实现（2.5）。
- **验收：** `watch_of` 在 05:00/09:00/17:00/21:00 边界正确（不等长）；分钟 ↔ 日历往返全等；
  负 delta / 回拨被显式拒绝；`NOMINAL_WATCH_MINUTES == 360` 与 `MINOR_ACTION_MINUTES == 10`
  与 `watch.json` / `travel.json` 值一致；无网络、无 IO；`bash tests/run_all.sh` 通过。

### C2 · 行动生命周期状态机

- **标签：** `难度：高`，`能力：逻辑`，`能力：编程`，`能力：测试`
- **依赖：** C1 已合并。
- **文件：** `chron.py`，`tests/test_chron.py`
- **说明：** 第 4 节：`Action` 数据结构、八状态、**全转换表**作为一张显式表（不散在 if 里）、
  `transition(action, to, ctx)` 返回产出事件或错误；每次转换产一条事件；终态不可逆；
  非法转换显式抛错而不是静默忽略。`time_cost` 由外部传入（不调 PRISM）。
- **验收：** 4.2 表里**每一行**各一条用例；终态再转换被拒；`interrupted → scheduled` 用剩余耗时重排；
  `blocked ⇄ scheduled` 往返；M8 同步路径 `proposed → running → completed/failed` 无 `scheduled` 也能走；
  产出事件字段含 GDD §19.5 的最小字段（id/世界时间/类型/主体/原因/状态变更）。
  纯函数、无 IO；`bash tests/run_all.sh` 通过。

### C3 · 调度器：稳定到期排序 + 依赖 + 中断条件

- **标签：** `难度：高`，`能力：编程`，`能力：逻辑`，`能力：测试`
- **依赖：** C2 已合并。
- **文件：** `chron.py`，`tests/test_chron.py`
- **说明：** 第 5 节：`due_heap`、排序键 `(due_minute, -priority, created_seq, action_id)`、
  依赖图（无环检测、默认级联取消）、`Window` 与冲突识别（同 place / 同 resource / blocks）。
  只产出「该开窗口」的判断与事件，不判结果。
- **验收：** 同 `due_minute` 多卡两次运行顺序**全等**；依赖未完成不放行；有环提案被拒（`proposed → failed`）；
  依赖失败默认级联取消（事件带 `reason`）；同资源两行动触发一个 `window.opened` 事件；
  `bash tests/run_all.sh` 通过。

### C4 · 事件屏障与时间压缩

- **标签：** `难度：高`，`能力：逻辑`，`能力：编程`，`能力：测试`
- **依赖：** C3 已合并。
- **文件：** `chron.py`，`tests/test_chron.py`
- **说明：** 第 6 节：`next_barrier(from)`、`advance_to(target)`、B1–B8 机读判据、
  压缩日志（只记真实事件）。`offline_advance` 只作为入参，不在本单判定「有无人在线」。
- **验收：** 6.5 的**五条**全过（穿屏障停在前、无屏障可压缩、不删因果、到期总停、屏障优先）；
  压缩不产生「被跳过的模拟」事件；`bash tests/run_all.sh` 通过。

### C5 · 持久化与恢复接线

- **标签：** `难度：高`，`能力：编程`，`能力：数据`，`能力：测试`
- **依赖：** `STATE-DESIGN.md`（#136）**已定稿**；C1–C4 已合并。
- **文件：** `chron.py`，`notdnd_web.py`（仅加 `chron` 块的 `_SESSION_DEFAULTS` 默认值工厂），
  `tests/test_chron.py`，`tests/test_notdnd_web.py`
- **说明：** 第 8 节：`chron` 存档块、加载 / 重放 / 重建 `due_heap`、幂等键表落盘与裁剪、
  惰性迁移。存储接口**按 `STATE-DESIGN.md` 的结论**实现（`Handle.commit` / `Handle.replay` / 按序号读事件）；
  若状态层实际不提供，停下报告、不自行发明接口。
- **验收：** 8.2 时序；重启后 `world_minute` 与到期顺序与重启前全等；
  同 `idempotency_key` 重投副作用只发生一次；老档（无 `chron`）载入补默认再存可读；
  `bash tests/run_all.sh` 通过。

### C6 · 接 M8 排程钩子与网页层

- **标签：** `难度：中`，`能力：编程`，`能力：测试`
- **依赖：** `ACTION-DESIGN.md`（#137）**已定稿**；C4 已合并（C5 可选、但建议先）。
- **文件：** `notdnd_web.py`，`prism_guide.py`（仅接钩子，不改判定），`tests/test_notdnd_web.py`
- **说明：** 4.3 的 `schedule / advance_to / next_barrier / cancel` 接到 M8 的行动管线；
  `can_defer = False` 的行动**保持 M8 同步结算不变**；只有 `can_defer = True` 或带
  `interrupt_conditions` 的行动进调度。导引者失败时的兜底叙事不受影响（基线 §6 第 3 条）。
- **验收：** 即时行动（`can_defer=False`）行为与合并前**逐字节相同**（回归不破）；
  长行动 `can_defer=True` 不锁玩家：回合立即返回 `scheduled`，下一次 `advance_to` 才结算；
  离线（无 AI）时排程与结算仍可用；`bash tests/run_all.sh` 通过。

> C6 之后若要「时间流速域」（2.5）或「战斗轮 ↔ 世界秒」的具体常数，**另开 Issue**
> （改规则包 / 内容），不夹在 C1–C6 里。

---

*本文件是设计文档；实现按文末切片拆单。修订走新 Issue，不夹带在实现 PR。*
