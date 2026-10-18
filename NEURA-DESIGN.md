# NEURA 设计：NPC 自主行为与活体世界（M11-a）

> **设计单**：只产出文档，不实现代码。
> **状态**：待 Master Agent 审查（2026-10-18）。基线 `origin/master` @ `e032b71`。
> **依据**：`GDD.md` §11 / §12；`GDD-BASELINE.md` §4.1 / §4.2 / §5 / §6；`STATE-DESIGN.md` §5.1 / §6.1 / §10 / §11 / §12；
> `CHRON-DESIGN.md` §2.3 / §3.1 / §4.3 / §5 / §6 / §8；`ACTION-DESIGN.md` §4.2 / §5 / §8 / §9 / §13；
> `MULTIPLAYER-DESIGN.md` §6；`GUIDE-DESIGN.md` §2.2 / §5.8。
> **范围**：把「NPC 是自主角色而非剧情道具」翻译成**可拆实现单**的工程口径。
> **一句话**：NEURA 是**唯一会被时间推进驱动、且只产出提案**的角色智能层——它读得到什么由知识门决定，
> 它能做什么由硬约束决定，它做了什么只由 M8 管线与状态层决定。

---

## 0. 怎么用这份方案

### 0.1 给 Master Agent 的拆单口径

- 本文 **不实现任何东西**；实现单见 §11 的 N1–N8，每条自带可改 / 禁改路径、依赖、标签。
- **文件独占是本里程碑的主要串行源**：N1–N5 同占 `neura.py`（串行），N6–N8 同占 `notdnd_web.py`（串行）。
  两条队列内部不得并行（`AGENTS.md` §4「同一文件不并行」）。
- 每条实现单都必须真跑 `bash tests/run_all.sh` 与 `bash tests/content_firewall.sh`，
  且**不得**把新文件写进 `tests/test_repo_layout.py` 的必需清单（沿用 `ACTION-DESIGN.md` §13 的实现约束）。
- **§12 的 P1–P10 已于 2026-10-18 拍板（见 §12.1）；N1–N8 可据此开工。**

### 0.2 引用索引（只引用，不修改）

| 主题 | 出处（章节号） | 本文用法 |
|---|---|---|
| NPC 最低模型 | `GDD.md` §11.1 | §4 字段表的**需求来源**，逐项落字段 |
| 需求驱动决策 | `GDD.md` §11.2 | §5 四段流水线的**架构来源** |
| 自主行为举例 | `GDD.md` §11.3 | §11 切片验收的**场景样例** |
| 知识边界 | `GDD.md` §11.4 | §6 五类分离与知识门 |
| 模拟分层 | `GDD.md` §11.5 | §7 层表的**四层定义** |
| 连续性与成本 | `GDD.md` §11.6 | §8 预算与降级阶梯 |
| 事实 / 信念分离、秘密对象、线索、访问控制 | `GDD.md` §12.1–§12.4 | §6 的**语义定义**；§6.5 复用表 |
| 系统协作规则 | `GDD.md` §5.2 | 「AI 文本 ≠ 事实」在 §5.6 / §9 的落点 |
| 行动管线 | `GDD.md` §8.2 / §8.4 | §5.6 必经管线 |
| 事件封套 | `GDD.md` §19.5；`STATE-DESIGN.md` §5.1 | §9.6 事件类型与 `visibility` |
| 降级 | `GDD.md` §20.4 | §8.4 离线底线 |
| 不变量 / 测试矩阵 | `GDD.md` §24.1 / §24.2 | §10 可测口径 |
| 模块落点与依赖方向 | `GDD-BASELINE.md` §4.1 / §4.2 | §9.1 `neura.py` 落点、禁止反向 import |
| M11 出口条件 | `GDD-BASELINE.md` §5「M11」行 | §10.1 三条出口条件逐条断言化 |
| 通用验收 | `GDD-BASELINE.md` §6 | §11 每片验收的公共项 |
| **不透明块**与惰性迁移 | `STATE-DESIGN.md` §6.1（`blocks` 字段）/ §10.1 / §11 | §9.2 `blocks.neura` 是**同级别**不透明块 |
| 一次提交 = 一条事件（A0） | `STATE-DESIGN.md` §5.0 | §9.6 每 NPC 每 tick ≤ 1 条事件 |
| 提交接口 | `STATE-DESIGN.md` §12.1 | §5.6 / §9.2 调用面 |
| 时间单位与换算 | `CHRON-DESIGN.md` §2.3 / §2.4 | §5.2「NEURA 不定义任何时间常数」 |
| 世界时间形状 | `CHRON-DESIGN.md` §3.1 | §9.3 `world_time` 不透明、提交时传入 |
| 排程器入口 | `CHRON-DESIGN.md` §4.3 | §7.3 挂接点（只读） |
| 到期排序与屏障 | `CHRON-DESIGN.md` §5.2 / §6.2 | §7.3 / §10.1「时间推进可回溯」 |
| `chron` 存档块 | `CHRON-DESIGN.md` §8.1 / Key Decisions 11 | §9.2 块实现的**范本**（照抄形状，不照抄内容） |
| 提案 / 结果契约 | `ACTION-DESIGN.md` §4.2 / §5.1 | §5.6 提案结构**不新增字段** |
| 排程钩子 L1 / L2 | `ACTION-DESIGN.md` §8.2 / §8.3 | §7.3 「NPC 计划不塞进 CHRON 卡」的依据 |
| 写接口与幂等键 | `ACTION-DESIGN.md` §9.1 / §9.4 | §5.6 内部分支与幂等键格式 |
| 每成员视角过滤 | `MULTIPLAYER-DESIGN.md` §6.2 / §6.3 / §6.4 | §6.5 复用表；§9.4 交付过滤 |
| 知识门 / 秘密门 | `GUIDE-DESIGN.md` §2.2 / §5.8 | §6.5 复用表（**不改**判定语义） |

> **口径说明（一处出入，需要 Master 知悉）**：Issue #333 的依赖段写「`CHRON-DESIGN.md` §2.3 / §11」，
> 但该文件只有 §0–§10，其后是 `Key Decisions` 与「实现切片建议」两节（无 §11 编号）。
> 本文按**实际存在**的章节引用：§4.3（调度器入口）、§5.2（稳定排序）、§6.2（屏障）、§8.1（`chron` 块）、
> Key Decisions 9 / 11。**不改该文件**，仅在此登记编号出入。

---

## 1. 目标与非目标

### 1.1 目标

1. **NPC 的最低模型是可落盘的**：`GDD.md` §11.1 列的十三项（身份 / 性格 / 能力 / 需求 / 目标 / 日程 /
   位置 / 社交关系 / 知识（含错误认知）/ 秘密 / 记忆 / 当前计划 / 资源约束）在 §4.2 有字段级定义，
   且标明**权威态 / 派生视图 / 仅内存**。
2. **决策是可解释、可复现的**：同种子、同状态、同世界分钟 → 同一份决策与同一份计划（§5）。
3. **越权在工程上不可能，而不只靠提示词**：知识过滤发生在**构造 AI 上下文之前**（§6）。
4. **成本是算得出来的**：每游戏日有硬上限、有降级阶梯、有离线底线（§8）。
5. **时间推进可回溯**：NEURA 不新建时钟，只在 CHRON 的停点上工作（§7.3 / §9.3）。
6. **能拆成可执行单**：N1–N8 可按依赖串行合入（§11）。

### 1.2 非目标（明确不做）

- **不做地图生成**：未探索层的「按需生成」归 M12 / ATLAS（`GDD.md` §13.3），NEURA 只发信号。
- **不做叙事**：NPC 的对白、旁白仍归 LOREX / `prism_guide`；NEURA 只出**结构化提案**与**记忆摘要**。
- **不做规则判定**：骰点只来自 PRISM（`GDD-BASELINE.md` §4.2「AI 边界」）。
- **不做经济系统的账本**：NPC 的资源约束只做**可行性判断**与**粗账**，权威库存仍属规则包 / M12。
- **不改任何既有文件**（本设计单）：代码、`data/**`、`docs/**`、`tests/**`、治理与设计文档全部禁改。

### 1.3 一句话设计

> **NEURA = 一个纯函数式的「计划提议器」+ 一个不透明存档块 + 一条到 M8 管线的窄口。**
> 它读的知识是**过滤后的只读视图**，它写的东西**只有提案**，它推进的时间**只有 CHRON 给的分钟**。

---

## 2. 现状盘点（只读输入）

### 2.1 已经能复用的（代码证据）

| 能力 | 位置 | 复用方式 |
|---|---|---|
| 唯一提交出口 + 事件日志 + 重放 | `state.py:877` `Handle.commit` / `state.py:759` `view` / `state.py:839` `replay` | NEURA **只调用**，不 import `state` 的实现细节到 `neura.py`（§9.1） |
| 不透明块可加新块名 | `state.py:226` `_apply_change`（按 `block` 名机械写入）；`state.py:491` `_genesis_blocks` 只列 `rules/atlas/guide` | 提交 `{"block":"neura","op":"replace_block"}` 即可，**`state.py` 零改动** |
| `chron` 块的成熟形状 | `chron.py:2283` `new_block` / `chron.py:2323` `migrate_block` / `chron.py:2301` `block_from_scheduler` | §9.2 的 `neura` 块**照同一形状**实现（版本 / 惰性迁移 / 脏值降级） |
| 唯一世界时钟与停点 | `chron.py:2057` `advance_to` / `chron.py:1985` `next_barrier` / `chron.py:2455` `world_time_minute` | §7.3 只读挂接，**不新建时钟** |
| 行动管线（提案→验证→结算→提交） | `notdnd_web.py:2730` `_action_settle_commit`；提案 schema 见 `ACTION-DESIGN.md` §4.2 | §5.6 **内部分支**复用同一段代码，不新增结算路径 |
| 排程钩子 L1 | `notdnd_web.py` `_schedule_hook`（`ACTION-DESIGN.md` §8.3 的 `_SyncOnlySchedule` / `_ChronSchedule`） | NPC 计划**不塞进** CHRON 卡（§7.3 理由） |
| 秘密门 / 知识门 | `prism_guide.py:1814` `check_speech` / `:1829` `secret_allowed_for` / `:1900` `per_member_bound_secrets` / `:1927` `redact_for_member` | §6.5 **原样复用**，不改判定语义 |
| 每成员视角过滤 | `notdnd_web.py:742` `_room_event_visible` | §9.4 交付时过滤 |
| 空间可达性 | `atlas.py:335` `range_band`（`same/adjacent/near/mid/far`）/ `:223` `exits` / `:179` `find_place` | §5.2 距离约束的**唯一**来源（由调用方注入，`neura` 不 import `atlas`） |
| NPC 静态档案 | `data/schema/scenario.schema.json` `$defs.npc`（`id/name/alias/role/faction/drive/lever/mask/tell/threshold/secret/attitude/knows/knows_unresolved/if_dead_or_betrayed`） | §4.1 **只存 `id` 引用**，不复制、不改 schema |
| NPC 在场点（展示用） | `prism_guide.py` `guide.npc_at` / `npc_home` | §4.4 降级为**派生镜像**，权威迁到 NEURA |

### 2.2 缺口（本里程碑要补的）

| 缺口 | 现状 | 本文落点 |
|---|---|---|
| 没有「需求 / 目标 / 计划 / 日程」的运行时结构 | `scenario.npc` 只有静态性格与秘密，无需求、无日程 | §4.2 |
| 没有 NPC 的知识账本 | `npc.knows` 是一串线索 id，只有「知道 / 不知道」，没有**错误信念**、来源、置信度 | §6.1 |
| 没有「NPC 作为行动主体」的提交路径 | `_action_settle_commit` 把 `source` 写死 `"player"`（`notdnd_web.py:2804`）；提案的 `actor` 只由 `_action_actor(rules)`（`:2330`）从 `rules.active_unit_id` 取，NPC 没有入口 | §5.6 + N6 |
| 没有时间驱动的 NPC 步进 | `_chron_advance`（`notdnd_web.py:3333`）只结算玩家到期卡 | §7.3 + N7 |
| 没有成本账本 | 导引者有单回合预算（`GUIDE-DESIGN.md` §5.11），但 NPC 侧无预算、无降级 | §8 |
| 没有只读观察面 | 无法回答「这个 NPC 现在为什么这么做」 | §10.3（可观测）+ N8 |

### 2.3 需要小心的既有口径

1. **`guide.npc_at` 与 NEURA 的 `location` 会重复**。`STATE-DESIGN.md` §11 已把 `guide` 定为**非权威展示块**，
   所以权威归 NEURA，`guide.npc_at` 降为派生镜像（§4.4）。**不得**出现「两处都能改位置」。
2. **`chron.Action` 不是 NPC 计划**。`CHRON-DESIGN.md` §4.2 的八状态带玩家语义（`cancelled` = 玩家主动取消、
   `interrupted` = 被打断）。把 NPC 计划塞进同一队列会污染这套语义（§7.3 详述）。
3. **`roll_refs` 只放 PRISM 掷骰**。NEURA 的决策抖动**不是**骰点（§5.3），不进 `roll_refs`，
   否则会与 `STATE-DESIGN.md` §5.1 的「骰点只来自 PRISM」冲突。

---

## 3. 设计决定（先拍板，后拆单）

| # | 决定 | 理由 / 依据 |
|---|---|---|
| **U1** | 新增 `neura.py`，只产出**提案与计划**，**不写状态、不落库、不 import 业务模块** | `GDD-BASELINE.md` §4.1 / §4.2；`GDD.md` §5 NEURA 权威边界 |
| **U2** | 运行时态全部落在 **`blocks.neura`**（状态层不透明块，与 `rules/atlas/guide/chron/room` 同级），带 `NEURA_VERSION` 与惰性迁移 | `STATE-DESIGN.md` §6.1 / §10.1 / §11；`state.py:226` 已支持任意块名 |
| **U3** | **静态档案不复制**：`neura` 块只存 `npc_id` 引用 + 运行时差异；`data/schema` 与 `data/scenarios` **零改动** | 「加文件即加内容」现状；避免双份真相 |
| **U4** | 位置权威归 NEURA，`guide.npc_at` 降为**派生镜像**，两者**在同一次提交**内更新（A0 允许跨块多路径） | `STATE-DESIGN.md` §5.0 / §5.1 / §11 |
| **U5** | NPC 计划**不进** `chron.actions` 队列；只用 `advance_to` 的**停点**作为唯一 tick 源 | §7.3；避免污染 `CHRON-DESIGN.md` §4.2 的玩家语义 |
| **U6** | NEURA **不定义**任何时间换算常数：一切分钟数由 CHRON / `travel.json` 口径给出 | `CHRON-DESIGN.md` §2.3「唯一真相」 |
| **U7** | 决策抖动用 **SHA-256 派生的稳定随机数**，不用 `random`、不用 `hash()` | §5.3；可复现是硬要求 |
| **U8** | 知识过滤**只有一个收口**：`neura.select_context()`；除它之外**任何函数都不得**拼 NPC 提示词 | `GDD.md` §12.4「在把上下文发给 AI 前先过滤」 |
| **U9** | 最终行动**必须**经 M8 管线（内部分支 `_neura_submit`），**不得**直接 `commit()` 世界事实 | `GDD.md` §5.2 / §8.2；`GDD-BASELINE.md` §4.2「唯一提交」 |
| **U10** | 需要掷骰而 PRISM 尚无 NPC 主体入口时，NEURA **拒绝执行并记录未执行计划**，不绕过管线、不伪造结果 | `GDD.md` §20.1；`GDD-BASELINE.md` §4.2「骰点只来自 PRISM」 |
| **U11** | 成本记账以**游戏日**为单位，计数器落盘（跨进程、跨重启一致） | §8.2；可断言 |
| **U12** | 一次 tick 对同一 NPC **最多 1 条事件**；不同 NPC 各自提交，各自 `visibility` | `STATE-DESIGN.md` §5.0（A0）/ §12.4（一条事件一份 `visibility`） |

---

## 4. NPC 的最低模型（GDD §11.1）

### 4.1 静态档案 vs 运行时态

| | 静态档案 | 运行时态 |
|---|---|---|
| 载体 | `data/scenarios/*.json` 的 `npcs[]`（既有 schema） | `snapshot.blocks.neura.npcs[<npc_id>]` |
| 权威方 | 内容作者（`data/**`，本里程碑不改） | NEURA（经状态层提交） |
| 内容 | 姓名 / 别名 / 角色 / 势力 / 驱动力 / 杠杆 / 面具 / 破绽 / 底线 / 秘密文本 / 态度 / `knows` | 需求水平 / 目标 / 日程 / 位置 / 关系 / 知识账本 / 秘密状态 / 记忆 / 计划 / 资源 |
| 读法 | 只读；**不复制进 `neura` 块** | 只存**与静态档案的差异**；缺省值由 `new_block()` 给 |

> 反面例子（禁止）：把 `name` / `secret` 抄进 `neura` 块。两份文本一旦漂移，秘密门会按旧文本判定——
> 这是**越权漏洞**，不只是冗余。

**`budget` 不在这一层**：成本账本是**块级**字段（`blocks.neura.budget`，每战役一份，与 `npcs` 平级），
**不是**每个 NPC 一份；见 §4.2 的「块级字段」与 §8.2。

### 4.2 运行时字段表（字段级）

图例：**权威** = 落盘且只能由提交改写；**派生** = 落盘但可完整重算；**内存** = 不落盘。

| 字段 | 类型 | 必填 | 落盘 | 类别 | 来源 / 说明 |
|---|---|---|---|---|---|
| `npc_id` | `str` | 是 | 是 | 权威（键） | 等于静态档案 `npcs[].id`；块内以 `npcs` 字典的键表达 |
| `display_ref` | `{"scenario_id": str, "npc_id": str}` | 是 | 是 | 权威 | 指向静态档案；**不存姓名文本** |
| `importance` | `float 0..1` | 是 | 是 | 权威 | 由静态 `role` / `faction` / 是否在主线 `threads` 派生一次后固定 |
| `abilities` | `{"mgt","fin","vig","ins","mnd","pre": int\|null}` | 否 | 是 | 权威 | PRISM 六维（力道 MGT / 灵巧 FIN / 体魄 VIG / 洞察 INS / 心智 MND / 气场 PRE）；`null` = 按规则包默认 10（`GDD.md` §9.1） |
| `needs` | `{name: int 0..100}`，键 ∈ `safety/wealth/power/relation/duty/curiosity` | 是 | 是 | 权威 | **未满足程度**（越高越急）；初始值由 `role` 模板 + 确定性抖动给 |
| `need_weights` | `{name: float 0..1}`，同上六键 | 是 | 是 | 权威 | 角色常量（性格 → 权重）；一旦生成不再逐日漂移 |
| `goals` | `[{goal_id, kind, target_ref, weight, deadline_minute, status}]` | 是 | 是 | 权威 | `kind ∈ {survive, prosper, dominate, belong, fulfill_duty, learn}`；`status ∈ {active, done, abandoned, failed}` |
| `schedule` | `[{slot_id, from_tod_minute, to_tod_minute, place_id, activity, interruptible}]` | 是 | 是 | 权威 | **日内模板**：`from/to` 是「当天第几分钟」（0..1439），**不是**绝对分钟（绝对分钟由 `day_start + tod` 算） |
| `location` | `{place_id, since_minute, band_to_party}` | 是 | 是 | 权威 | `band_to_party` 是**派生**（每次 tick 重算，落盘只为审计） |
| `relations` | `{npc_id: {trust, respect, fear, hostility, loyalty, debt, dependence, attraction, rivalry, affiliation}}`，值 `int -100..100` | 是 | 是 | 权威 | 复用 `GDD.md` §15.1 的多维关系模型；`affiliation` 是字符串，其余是整数 |
| `knowledge` | `[Claim]`（见 §6.1） | 是 | 是 | 权威 | 五类分离；**错误信念是一等公民** |
| `secrets` | `[{secret_id, claim_id, holders, reveal_conditions, evidence_refs, status}]` | 否 | 是 | 权威 | 对齐 `GDD.md` §12.2 的九项；`status ∈ {held, leaked, public, disproved}` |
| `memory` | `[Memory]`（见 §4.3） | 是 | 是 | 权威 | **摘要级**，不存原文；有上限与裁剪规则 |
| `plan` | `{plan_id, goal_id, step_index, steps[], started_minute, until_minute, status, proposal_id}` | 否 | 是 | 权威 | `status ∈ {pending, running, done, dropped}`；一次只有一个当前计划 |
| `resources` | `{coin, stock: {item_id: int}, obligations: [{to, kind, due_minute}]}` | 是 | 是 | 权威 | **粗账**；规则包权威库存不在此（§9.2） |
| `last_tick_minute` | `int` | 是 | 是 | 权威 | 幂等 / 补算的锚点；`== world_minute` 表示本分钟已 tick |
| `layer` | `str`，∈ `near/important_far/background/unexplored` | — | 否 | 内存 | 每次 tick 由 §7.1 现算，不落盘（避免层缓存与位置漂移） |
| `derived_view` | `dict` | — | 否 | 内存 | `select_context()` 的产物，只活在一次调用里 |

**块级字段（与 `npcs` 平级，不是每个 NPC 一份）**

- `budget`：`{day, strong_calls, light_calls, tokens}`，必填、落盘、权威。每战役**一个**成本账本，
  落 `blocks.neura.budget`（**块级，见 §8.2**）；跨日自动重置（重置本身也由提交落盘）。
  `npcs[<npc_id>]` 里**没有** `budget`。
- `journal`：`[DecisionRecord]`（见 §5.4），必填、落盘、**派生**（可完整重算）。决策留痕是**整战役**
  一条环（跨 NPC 共用），落 `blocks.neura.journal`；上限 `JOURNAL_MAX = 256` / 战役（与 §12.1 P3、
  `neura.py` 的常量一致）；超限按确定规则**先裁最旧**（按 `at_minute`，同分钟按 `decision_id` 字典序），
  裁剪实现见 §9.2。`npcs[<npc_id>]` 里**没有** `journal`。

**字段级不变量**

- `needs` 六键**必须全在**；缺键由 `migrate_block` 补中位数 50（不猜 0——0 会让 NPC 永远不动）。
- `need_weights` 生成后**不再变**：若允许逐日漂移，「同种子同结果」立刻失效。
- `schedule` 的 `from_tod_minute < to_tod_minute`；跨夜班用两个 slot 表达（**不开**跨 0 点的区间）。
- `plan.until_minute ≥ plan.started_minute`；`plan` 为空时 `plan` 键**不存在**（不用 `null`）。
- `memory`（每 NPC）/ `journal`（块级）超上限时**先裁最旧**（记忆按 `minute` + `memory_id`、留痕按
  `at_minute` + `decision_id` 字典序），保证裁剪是确定性的。

### 4.3 两个子结构

```json
// Claim（§6.1 的五类之一）
{
  "claim_id": "cl-7f3a91c2",
  "cls": "belief",
  "proposition": "守门人昨夜被换成了别人",
  "about_ref": "npc-05",
  "source_kind": "rumor",
  "source_ref": "npc-09",
  "confidence": 62,
  "learned_minute": 17120,
  "evidence_refs": ["ev-market-03"],
  "status": "active"
}
```

```json
// Memory（摘要级；不存原文）
{
  "memory_id": "mem-2b41d0",
  "minute": 17120,
  "kind": "observation",
  "summary": "看到守门人换岗时间比往常早了一刻钟",
  "participants": ["npc-05"],
  "salience": 40,
  "claim_refs": ["cl-7f3a91c2"]
}
```

- `summary` 上限 **80 字**（按码位），超出截断并置 `truncated: true`；**禁止**存 AI 原文。
- `salience` 由 `kind`（权重表）× 是否涉及自身目标 × 是否涉及 PC 决定，**纯规则算**，不调模型。

### 4.4 与 `guide.npc_at` 的关系（单向镜像）

- **权威**：`neura.npcs[npc].location.place_id`。
- **派生**：`guide.npc_at[npc_id]`，由编排层在**同一次提交**内写入（`changes` 两条，`block` 不同）。
- **判定冲突时的规则**：以 `neura` 为准；`guide.npc_at` 缺失或过期不影响任何判定，只影响提示词里的在场名单。
- **不改** `prism_guide` 的读取顺序（`_npc_locus` 仍是 `npc_at` 优先），因为提交后两者必然一致。

### 4.5 最小模型不变量（可在测试里断言）

1. `neura.npcs` 的键集 ⊆ 该剧本 `npcs[].id` 集（不发明 NPC）。
2. 任何 `knowledge` 元素的 `proposition` **不得**包含该 NPC 未持有的秘密全文（§6.3 的过滤断言）。
3. `plan.goal_id` 必须存在于 `goals` 且 `status == "active"`。
4. `location.place_id` 必须能通过 `atlas.find_place` 找到（由编排层的 `view` 保证；`neura` 只校验「在 `view.places` 里」）。

---

## 5. 决策架构（GDD §11.2）

### 5.1 四段流水线 + 一条窄口

```
                    ┌─────────────────── 每次 tick（CHRON 停点）───────────────────┐
                    │                                                            │
  只读视图 view ───► │ ① 硬约束过滤  →  ② 需求评分  →  ③ 计划选择  →  ④ 规则复验  │
  (知识门已过滤)     │   hard_filter     score_options    choose        verify_plan  │
                    │        │                │               │              │      │
                    │        └─ 全灭 ─────────┴──► 记 journal（不提交）◄────┘      │
                    │                                        │                    │
                    └────────────────────────────────────────┼────────────────────┘
                                                             ▼
                                              ActingPort.submit(proposal)   ← M8 管线窄口
                                                             ▼
                                       M8 第 3–5 段：验证 → 结算 → 提交（source="npc"）
                                                             ▼
                                              结果回写记忆 / 需求 / 关系（下一次提交）
```

**四条硬规矩**

1. **AI 在 ③ 里只提候选**，候选必须再进 ②③④；**AI 不选、不定、不写**。
2. **④ 之后只有一条出路**：`ActingPort.submit()`。没有任何「直接 `commit()`」的分支。
3. **失败也是结果**：全灭 / 预算耗尽 / 管线拒绝 → 写 `journal`（不落事件），下次 tick 重来。
4. **每次 tick 只处理「该处理的 NPC」**（§7.1 的层 + `last_tick_minute`），不做全量扫描。

### 5.2 硬约束（可执行清单 + 函数签名）

```python
# neura.py（N2 落地）。全部纯函数：无 IO、无网络、不 import 业务模块。
Reject = dict   # {"code": str, "detail": str, "ref": str|None}

def hard_filter(npc: dict, view: dict, now_minute: int) -> dict:
    """返回 {"ok": bool, "options": [Option], "rejected": [{"option_id", "code", "detail"}]}。

    Option = {"option_id", "verb", "target_ref", "place_id", "minutes",
              "relieves": {need: float}, "aggravates": {need: float},
              "risk": float 0..1, "source": "rule"|"ai"}
    view 由编排层构造（§5.2.1），neura 只读。
    """

def check_distance(npc, option, view) -> Reject | None: ...
def check_time(npc, option, view, now_minute) -> Reject | None: ...
def check_resource(npc, option, view) -> Reject | None: ...
def check_law(npc, option, view) -> Reject | None: ...
def check_knowledge(npc, option, view) -> Reject | None: ...
```

| 约束 | 判据（可执行） | 固定错误码 | 数据来源 |
|---|---|---|---|
| **距离** | `view.bands[(npc.place, option.place_id)] ∈ {"same","adjacent","near"}`，**或** `view.travel_minutes(option.place_id) ≤ option.minutes` | `E_DISTANCE` | `atlas.range_band`（`atlas.py:335`）由编排层预计算；`travel_minutes` 用 `data/system/travel.json` 口径（`CHRON-DESIGN.md` §2.4），**NEURA 不自带换算** |
| **时间** | ① `option.minutes ≤ 当前计划剩余预算`；② 与该 NPC 的 `schedule` 冲突且 `slot.interruptible == false` → 拒；③ 若 `now_minute + option.minutes` 超过某条 `resources.obligations[].due_minute`，则**只放行指向该义务**的选项 | `E_TIME` | `neura` 块自身 + `now_minute` |
| **资源** | `resources.coin ≥ option.cost.coin` 且 `resources.stock[item] ≥ option.cost.stock[item]`；`obligations` 到期未清 → 只允许 `fulfill_duty` 类选项 | `E_RESOURCE` | `neura` 块；**权威库存仍在规则包**，这里只做可行性 |
| **法律** | `view.laws` 中 `forbid_tags ∩ option.tags ≠ ∅` 且 `option.place_id ∈ law.scope_place_ids` → 拒（`law.threshold` 允许时改为「高风险但可行」，交 ②③ 的 `risk` 处理） | `E_LAW` | 世界包 / 规则包投影；**缺失即空表**（`law_source: "none"`），不猜 |
| **知识边界** | `option.requires_claims` 里每条 claim 都必须在该 NPC 的 `knowledge` 中且 `status == "active"`；`option.requires_place` 必须在 `view.known_places` 里 | `E_KNOWLEDGE` | §6 的 `select_context` 同源 |
| **层级预算** | 该层当日 `strong_calls` / `light_calls` 已用尽 → **不拒绝选项**，只禁止 ③ 的 AI 候选（降级为规则候选） | `E_LAYER_BUDGET`（记 journal，不拒选项） | §8.2 |

> **为什么用错误码而不是布尔**：`journal` 要能回答「他为什么没去」；布尔值回答不了（§10.3 可观测）。

#### 5.2.1 `view` 的形状（由编排层注入，`neura` 不查询）

```python
view = {
  "campaign_id": str,
  "now_minute": int,
  "scenario_id": str,
  "party_places": [str],                 # 各 PC 当前 place_id
  "places": {place_id: {"name": str, "exits": [place_id]}},   # 仅玩家已知/可达
  "known_places": {place_id: True},      # 该 NPC 的已知地点（知识门过滤后）
  "bands": {(a, b): "same"|"adjacent"|"near"|"mid"|"far"},
  "travel_minutes": {place_id: int},     # 由调用方按 CHRON 口径算好
  "laws": [{"id", "forbid_tags": [str], "scope_place_ids": [str], "threshold": str}],
  "law_source": "world"|"rules"|"none",
  "world_facts": {claim_id: {"proposition": str, "visibility": str}},  # 仅公开事实
  "action_verbs": {verb: {"tags": [str], "risk": float, "check": bool}},  # 来自规则包动作表
}
```

**`view` 里没有的东西，NEURA 就永远不知道**——这是比「提示词里写一句不要越权」更强的保证。

### 5.3 需求评分（可解释 + 确定性）

**六维需求**（`GDD.md` §11.2 的六项）：`safety` 安全 / `wealth` 财富 / `power` 权力 /
`relation` 关系 / `duty` 责任 / `curiosity` 好奇。

```
raw(option)      = Σ_n  need_weights[n] × (needs[n] / 100) × sat(option, n)
risk_penalty     = risk_aversion(npc) × option.risk              # risk_aversion ∈ [0.2, 0.8]
time_penalty     = 0.02 × ceil(option.minutes / 60)              # 每小时 −0.02
habit_bonus      = 0.05 if option.place_id == npc.home_place else 0.0
jitter           = 0.03 × (2 × stable_unit(seed, npc_id, now_minute, option_id) − 1)
score(option)    = raw − risk_penalty − time_penalty + habit_bonus + jitter
```

- `sat(option, n) = relieves.get(n, 0) − aggravates.get(n, 0)`，取值 `[-1, 1]`；
- `risk_aversion(npc)` 由 `need_weights["safety"]` 线性映射（`0.2 + 0.6 × w_safety`），**不新增字段**；
- `home_place` 取该 NPC 首个 `schedule` slot 的 `place_id`（无日程则无加成）。

**确定性随机源**（U7）：

```python
import hashlib

def stable_unit(*parts: str) -> float:
    """[0, 1) 的确定性随机数：同输入同输出，跨进程 / 跨机器一致。

    不用 `random`（全局状态）、不用 `hash()`（PYTHONHASHSEED 随机化）、
    不用时间戳。输入必须全部来自权威状态。
    算法用 `blake2b`，不承诺无碰撞，只承诺同输入同输出与统计均匀（§9.2 裁定第 4 条）。
    """
    payload = repr(parts).encode("utf-8")      # 参数编码：标量走 repr(tuple)，无歧义
    digest = hashlib.blake2b(payload, digest_size=8).digest()
    return (int.from_bytes(digest, "big") >> 11) / float(1 << 53)   # 取前 53 位
```

**抖动不是骰点**：`jitter` 只为打破并列（否则同权重 NPC 会永远选同一个），
**不进** `roll_refs`、**不进** PRISM 账本、**不落独立字段**——它可由 `(seed, npc_id, minute, option_id)`
随时重算。`seed` 取 `campaign_seed`（战役创建时固定，落 `neura` 块顶层）。

**可解释记录**：每个候选都留下 `terms`（逐需求的 `weight / level / sat / product`）与 `raw / penalty / jitter / score`，
写进 `journal[].scores`。审查者能手工复算出同一个 `score`。

### 5.4 计划选择

```python
def choose(scores: list[dict]) -> dict | None:
    """返回得分最高的候选；并列时按 (score, -risk, option_id) 字典序稳定取一。"""

def decide(npc: dict, view: dict) -> dict:
    """一次决策的完整记录（写进 journal，不直接落事件）。

    返回 {"decision_id", "npc_id", "at_minute", "layer", "hard", "scores",
          "chosen", "switch_reason", "cost": {"tier", "tokens"}, "ai_used": bool}
    """
```

- **计划切换必须有理由**：新 `goal_id` ≠ 旧 `goal_id` 时，`switch_reason` 必填，取值
  `{goal_done, goal_failed, hard_blocked, newly_known, deadline_pressure, need_spike}`。
  这是「行为连续可解释」的**机器可读判据**（§10.1）。
- **无候选**：`chosen = None`，`switch_reason = "hard_blocked"`，只写 journal。

### 5.5 AI 只提候选（可选段）

```python
def generate_candidates(npc, view, *, budget) -> list[dict]:
    """只在 budget 允许且该 NPC 处于 near / important_far 层时调用；失败返回 []。"""
```

- **输入**：`select_context()` 的产物（§6.2），约 **900 tokens**；
- **输出**：严格 JSON，最多 3 条候选，字段固定
  `{"candidates":[{"verb","target_ref","place_id","minutes","relieves":[…],"why","confidence"}]}`；
- **校验**（不合格整条丢弃，不抛异常、不修补）：`verb ∈ view.action_verbs`、`target_ref ∈ view` 的实体集、
  `place_id ∈ view.known_places`、`minutes ∈ [0, 1440]` 且是 `MINOR_ACTION_MINUTES`（10）的整数倍、
  `relieves ⊆ 六需求键`；
- **超时 / 非法 JSON / 越权实体** → 记 `ai_failed`（journal），回退规则候选（`source: "rule"`），**不阻塞 tick**；
- **AI 候选的身份**：`source: "ai"`，在 `journal` 里可区分，便于统计「AI 候选采纳率」。采纳率是 N8 的可观测项。

> 关键：AI 候选**没有**任何特权分数。它和规则候选走同一个 ②③④。因此「模型幻觉出一个不存在的地点」
> 在 ① 就被 `E_KNOWLEDGE` / `E_DISTANCE` 干掉，而不是靠提示词祈祷。

### 5.6 进入 M8 行动管线（接口签名与时序）

**协议（`neura` 侧，纯鸭子类型，不 import `notdnd_web`）**

```python
class ActingPort(Protocol):
    def submit(self, proposal: dict, *, idempotency_key: str,
               source: str = "npc") -> dict:
        """把一条已通过硬约束的计划交给 M8 管线（第 3–5 段）。

        proposal: ACTION-DESIGN.md §4.2 的结构；NEURA 只填这些字段：
                  schema_version / proposal_id / actor{kind:"npc", id} /
                  intent{verb,target,desired,means} / params / source:"action" /
                  risk / time_cost / schedule_hint{can_defer:false} /
                  interruptible / interrupt_conditions / expected_changes / created_at
        返回 {"status": "committed"|"rejected"|"conflict"|"unsupported",
              "result": dict|None, "reason": str|None, "event_id": str|None}

        约束：不抛异常（失败也是返回值）；不做 HTTP；不产出叙事。
        """
```

**幂等键**（满足 `ACTION-DESIGN.md` §9.4 的 `^[A-Za-z0-9_-]{8,64}$`）：

```
np_<sha256(npc_id)[:8]>_<sha256(plan_id)[:10]>_<step_index:02d>     # 例：np_3f9a1c02_8b7d4e0a55_01
```

同一计划步骤重投 → 状态层 `idempotency_key` 命中 → 返回首次结果，**不重复副作用**（`STATE-DESIGN.md` §7）。

**时序（即时计划 vs 延迟计划）**

```
tick(now_minute)
  ├─ decide()                       # 纯函数，无 IO
  ├─ to_proposal(decision)          # ACTION-DESIGN §4.2 结构
  └─ port.submit(proposal, key)     # 临界区：session.lock 内
        ├─ 第 3 段 验证（scene action 表 / 风险 / 实体引用）→ 不成立：rejected
        ├─ 第 4 段 结算（PRISM 纯规则；无掷骰计划直接成结果）→ 失败：failed
        ├─ 排程钩子 enqueue(can_defer=False) → mode="sync"（不排程）
        ├─ 第 5 段 提交：handle.commit(..., source="npc") → 一条事件
        └─ 返回 {"status":"committed","result":…,"event_id":…}
  └─ write_back(result)             # 记忆 / 需求 / 关系；随**下一次**提交落盘
```

- **禁止**：`neura` 直接调 `state.Handle.commit()`；直接改 `session.rules`；直接写 `session.guide`。
- **写回与提交的关系**：`write_back` 修改的是**内存里的下一版块**，由编排层在同一次或下一次 `commit()`
  随 `changes` 落盘。**不为了写记忆额外开一条事件**（A0：一次提交一条事件）。
- **`actor`**：`{"kind": "npc", "id": "<npc_id>"}`；`source` 必须是 `"npc"`（不是 `"ai_proposal"`——
  后者留给「AI 替玩家提的案」，两者的审计含义不同）。

### 5.7 结果回写记忆（连续性的一半）

| 结果 | 回写 |
|---|---|
| `status == "success"` | 计划 `status = "done"`；`needs` 按 `sat` 打折兑现（`needs[n] -= 0.5 × relieves[n] × 100` 截断到 0..100）；写 `memory(kind="outcome", salience=…)` |
| `status == "partial"` | 同上，兑现系数 ×0.5；`memory.kind = "partial"` |
| `status == "failure"` | `needs` **反向**上升（惩罚）；`memory(kind="failure")`；若 `goal_id` 的 `deadline` 已过 → `goal.status = "failed"` |
| `status == "rejected"` / `"unsupported"` | **不改** `needs`；写 `journal` + `memory(kind="blocked")`，`plan.status = "dropped"` |
| `status == "conflict"` | 计划保留 `pending`，下个 tick 重试（`base_seq` 变了） |

**为什么失败也要改需求**：不改的话，NPC 会永远重复同一个必败计划（「决策抖动」再大也救不了），
玩家会看到「这个人一直在撞墙」——这正是 `GDD.md` §11.6「连续性」要避免的。

### 5.8 伪代码（可直接对照实现）

```python
def tick(block, view, *, port, now_minute):
    block = prune_block(block)                       # journal / memory 裁剪（确定性）
    for npc_id in due_npcs(block, view, now_minute):  # §7.1：按层 + last_tick_minute
        npc = block["npcs"][npc_id]
        # ① 硬约束
        hard = hard_filter(npc, view, now_minute)
        # ② 候选（预算允许才调 AI）
        options = list(hard["options"])
        ai_used, tokens = False, 0
        if hard["ok"] and can_call_ai(npc, view, block["budget"]):
            cands, tokens = generate_candidates(npc, view, budget=block["budget"])
            options += [normalize(c) for c in cands if revalidate(c, npc, view)]
            ai_used = bool(cands)
            charge(block["budget"], "strong" if ai_used else "light", tokens)
        # ② 评分
        scores = score_options(npc, options, view, now_minute,
                               seed=block["campaign_seed"])
        # ③ 选择
        decision = decide_record(npc, hard, scores, now_minute, ai_used)
        block = journal_append(block, npc_id, decision)
        if decision["chosen"] is None:
            continue
        # ④ 复验（AI 候选必须重过一次；规则候选幂等）
        proposal = to_proposal(npc, decision, view)
        if verify_plan(proposal, npc, view) is not None:
            block = journal_reject(block, npc_id, decision, "E_REVERIFY")
            continue
        # 窄口
        out = port.submit(proposal, idempotency_key=key_of(npc, decision))
        block = write_back(block, npc_id, decision, out, now_minute)
    return block
```

> `tick` **不返回事件**、**不碰 `state`**：它返回**新的块**与一份「待提交清单」，
> 由编排层组装成 `changes` 并提交。这让 `neura` 全程可单测（给定 block + view → 断言新 block）。

---

## 6. 知识边界（GDD §11.4 / §12.1–§12.4）

### 6.1 五类分离

| 类 | 语义 | 谁能看到 | 会不会变成世界事实 |
|---|---|---|---|
| `fact` | 已验证的世界真相（**引用**世界事实的 id，不复制文本） | 持有该 claim 的角色 + 有权限的玩家 | 是（但事实本体在 LOREX / 世界包，不在 NEURA） |
| `belief` | 该角色**以为**是真的（可能是错的） | 只有持有者 | **否** |
| `rumor` | 听来的、未经证实 | 持有者；可继续传播给第三人（`source_kind="rumor"`） | 否 |
| `secret` | 被明确限定知情者的真相 | `secrets[].holders` 点名者 | 是（但**披露**是另一条事件） |
| `inference` | 该角色**推出来**的结论 | 只有持有者；带 `evidence_refs` | 否 |

**硬规矩**

1. `belief` / `rumor` / `inference` **永不**写入 `world_facts`；世界真相只由 LOREX / 规则包提供。
2. 同一个 `proposition` 在不同角色处可以是不同 `cls`（A 亲眼所见 = `fact`；B 听说 = `rumor`）。
3. `fact` 的 `confidence` 恒为 100；其余 `0..100`。
4. **错误信念可被修正**：新证据到达时，创建**新** claim（`status="active"`）并把旧的置
   `status="disproved"` + `superseded_by`。**不原地改文本**——否则记忆与决策记录会对不上。

### 6.2 过滤入口（唯一收口）

```python
def select_context(block: dict, npc_id: str, view: dict, *, purpose: str) -> dict:
    """构造**交给模型之前**的最后一份上下文。这是 NPC 侧唯一允许拼提示词的地方。

    purpose ∈ {"decide", "dialogue", "memory_summary"}

    保证：
      1. 只含该 NPC 的 knowledge 中 status == "active" 的 claim；
      2. 只含 view.world_facts 中 visibility == "public" 的事实；
      3. 不含任何其他角色的 belief / rumor / inference；
      4. 不含 secrets 的 proposition 文本（只含 secret_id 与"你知/不知"）；
      5. 不含其他玩家的私聊、其他 NPC 的秘密、未探索地区的细节。
    """
```

**调用链（谁都不能绕过）**

```
notdnd_web._neura_tick()                 # 编排层
  └─ neura.select_context(block, npc_id, view, purpose="decide")
       └─ build_candidate_body(ctx)      # 唯一组装 messages 的函数
            └─ prism_guide 的传输层（assemble_chat_stream）   # 复用，不新增传输
```

- `build_candidate_body` 与 `select_context` 都在 `neura.py`，**同文件、同 PR**（N4），
  避免「过滤在 A 文件、拼词在 B 文件」的经典漏洞。
- **输出侧还有第二道门**：AI 返回的文本若进入玩家可见叙事，必须再过
  `prism_guide.redact_for_member(...)`（`prism_guide.py:1927`）——输入过滤不替代输出过滤。

### 6.3 具体例子：同一事实对 A 可见、对 B 不可见

**事实**：`npc-01` 的秘密「她已在不同循环中写过七封退位书」（`data/scenarios/ashen_crown.json`，既有内容）。

| 角色 | `knowledge` 里的 claim | 过滤结果 |
|---|---|---|
| `npc-01`（本人） | `cl-a1 cls="secret"`，`source_kind="self"`，`confidence=100` | **可见**：`select_context` 放行该 claim 的 `proposition` |
| `npc-05`（守卫） | 无相关 claim | **不可见**：`select_context` 不返回任何相关文本；`view.world_facts` 里也没有（它不是公开事实） |

**过滤发生在哪一行 / 哪个函数**

| 层 | 位置 | 作用 |
|---|---|---|
| 1. 上下文组装前 | `neura.select_context()`（N4 新增，`neura.py`） | 只放行该 NPC 的 active claim + 公开事实；**这是「交给 AI 之前」的那一步** |
| 2. 秘密持有判定 | `prism_guide.secret_allowed_for`（`prism_guide.py:1829`） | 复用既有口径：`lever` 出现在玩家原文，或 `known_clues ∩ npc.knows` |
| 3. 对白窄门 | `prism_guide.check_speech`（`prism_guide.py:1814`） | 单句对白含秘密全文 / 连续 8 码位 → `DROP_SECRET` |
| 4. 整段叙事 | `prism_guide.redact_secrets`（`prism_guide.py:1854`） | 未放行的秘密与每个 8 码位窗口替换成「……」 |
| 5. 每成员交付 | `prism_guide.per_member_bound_secrets`（`:1900`）/ `redact_for_member`（`:1927`） | 多人时按成员各算一次 |
| 6. 事件扇出 | `notdnd_web._room_event_visible`（`notdnd_web.py:742`） | 按事件的 `visibility` 判「这条帧该不该推给这个成员」 |

> **可断言的测试思路**（N4 验收）：构造 `npc-01` 与 `npc-05` 两个 NPC，同一个 `claim` 只挂在 `npc-01` 上；
> 断言 `select_context(npc-01)` 的序列化结果**包含**秘密全文，`select_context(npc-05)` 的结果
> **不含**该全文的任何一个 8 码位窗口（复用 `_secret_hits` 的判据，避免「只要不含整句就算过」的假通过）。

### 6.4 错误信念的修正链

```
17120  npc-05 听到传闻 → claim(cl-7f3a, cls="rumor", confidence=62)
17180  npc-05 亲眼看到换岗 → claim(cl-9c11, cls="fact", confidence=100)
       └ 旧 claim → status="disproved", superseded_by="cl-9c11"
       └ memory 追加一条 kind="correction"，并保留旧 claim 的 id（可回溯「他曾经这么以为」）
```

- **不删旧 claim**：删了就回答不了「他为什么昨天那么做」。
- `journal` 里过去的 `decision` 引用旧 `claim_id`，重放时仍能解析（id 不复用）。

### 6.5 与既有实现的复用关系（不重造）

| 需要的能力 | 复用什么 | 不改什么 |
|---|---|---|
| 「这个 NPC 知不知道这条秘密」 | `prism_guide.secret_allowed_for`（`:1829`） | 判定语义（`lever` / `known_clues ∩ knows`）**不改** |
| 「别让秘密从嘴里漏出来」 | `check_speech`（`:1814`）+ `redact_secrets`（`:1854`） | 8 码位窗口的判据**不改** |
| 「多人时按成员过滤」 | `per_member_bound_secrets`（`:1900`）+ `redact_for_member`（`:1927`） | 不另写一套 |
| 「这条事件该给谁」 | `notdnd_web._room_event_visible`（`:742`） | 频道映射**不改** |
| 「线索 id 的既有命名」 | `guide.known_clues` + `npc.knows`（`GUIDE-DESIGN.md` §2.2） | **不新增**第二套线索命名；NEURA 的 `claim_id` 是**内部 id**，对外仍用既有线索专名 |
| 「世界真相从哪来」 | `threads[]` / `locations[]` / 世界包（LOREX 只读投影） | NEURA **不**读全文，只读 `view.world_facts` 的公开项 |

> NEURA 的 `claim_id` 与剧本的线索 id（`C-01` 等）是**两个命名空间**：
> `claim_id` 标识「某角色持有的某条认知」，线索 id 标识「世界里的某条线索」。
> 一条线索可以被多个角色以不同 `cls` 持有——这正是 `GDD.md` §12.1 要的分离。

---

## 7. 模拟分层（GDD §11.5）

### 7.1 四层的判定（`layer_of`）

```python
def layer_of(npc, view) -> str:
    """返回 near / important_far / background / unexplored。

    只看三件事：与 PC 的空间关系、该 NPC 的重要性、它是否在玩家已知地点集里。
    """
```

| 层 | 判定（按顺序短路） |
|---|---|
| `near` | `npc.location.place_id ∈ view.party_places`，或 `bands[(npc.place, party_place)] == "adjacent"` 且该地点有 PC |
| `important_far` | `npc.importance ≥ 0.7`，**或** 该 NPC 的 `active goal` 的 `target_ref` 指向某个 PC / 玩家已知线索 |
| `background` | 其余**已具名**的 NPC |
| `unexplored` | **不适用**：该层里没有具名 NPC。NEURA 在这一层的职责只有一条——记录「该地区人口摘要」并在玩家进入前一层时发信号（生成归 M12） |

**晋升 / 降级**：每 tick 现算，**不缓存**（`layer` 是内存字段，§4.2）。
玩家走远一步，NPC 就从 `near` 掉到 `important_far`——这必须立刻生效，否则成本会漏。

### 7.2 层 × 触发条件 × 频率 × 成本上限

| 层 | 触发条件 | 更新频率 | 精度 | 成本上限（每游戏日） |
|---|---|---|---|---|
| **近场精细** | `advance_to` 停点；或玩家完成一次行动后 | 每个停点**最多 1 次**（`last_tick_minute` 去重） | 逐 NPC、10 分钟粒度（`MINOR_ACTION_MINUTES`）；记忆逐事件 | **H ≤ 2 次 / M ≤ 8 次** = 17,600 tokens |
| **重要远场** | 日切（`world_minute // 1440` 变化）；或**事件触发**（该 NPC 的目标相关事件、玩家进入其势力范围） | 每游戏日 1 次 + 事件触发（同日 ≤ 4 次） | 时段粒度（`watch_of`，晨/昼/昏/夜四段）；只推进目标与计划，不做逐分钟移动 | **H ≤ 3 次 / M ≤ 12 次** = 26,400 tokens |
| **背景** | 日切；按**聚合**处理 | 每 3 游戏日 1 次 | 聚合统计（人口 / 物价 / 治安指数）；**不生成个体计划** | **0 次模型调用**（纯公式） |
| **未探索** | 玩家进入其**相邻**地点时发信号 | 一次性（每个地区首次） | 只生成人口摘要与约束，不生成 NPC 个体 | **0 次**（gen 归 M12；若 M12 调用模型，记在 M12 账上） |

- 上限合计：H = 2 + 3 = 5 次（= 20,000 tokens），M = 8 + 12 = 20 次（= 24,000 tokens），
  合计 **44,000**，日预算 **60,000** 留 **16,000** 余量给「玩家交互触发的即时反应」（§8.2）。
- **单次 tick 的突发上限**：`≤ 1 次 H`、`≤ 3 次 M`——防止一次大推进把所有预算烧光。

### 7.3 与 CHRON 的挂接（不新建时钟）

**挂接点（只读）**

| 点 | 调用 | 用途 |
|---|---|---|
| 推进 | `chron.advance_to(state, target)`（`chron.py:2057`）返回后 | 用 `world_minute` 作为 tick 的 `now_minute`；用 `fired_minutes` 判断「跨过了哪些到期点」 |
| 停点 | 返回值的 `stopped_at` / `barriers` | 屏障处**不**做远场批量推进（玩家正在做别的事） |
| 下一停点 | `chron.next_barrier(state)`（`chron.py:1985`） | 只用于**告诉玩家**「下次会自动停在哪」，不参与调度 |
| 时间解析 | `chron.world_time_minute(world_time)`（`chron.py:2455`） | 把不透明 `world_time` 折成整数分钟 |

**不做什么**

- **不新建时钟**、不自己数分钟、不读 `time.time()` 做世界时间（`CHRON-DESIGN.md` §3.2 / Key Decisions 9）。
- **不把 NPC 计划塞进 `chron.actions`**：`chron.Action` 的八状态带玩家语义（`cancelled` = 玩家取消、
  `interrupted` = 被打断、`depends_on` = 玩家依赖），`CHRON-DESIGN.md` §4.2 的转换表是**为玩家行动定的**。
  NPC 计划没有「玩家取消 / 确认 / 澄清」的语义，硬塞会让「谁取消了这张卡」无法回答。
- 因此 NPC 的「延迟执行」用**块内 `plan.until_minute`** 表达，到期由下一次 tick 的**补算**收敛（§7.4）。
  是否要把两者统一，见 §12 的 P1。

### 7.4 补算（catch-up）

一次 `advance_to` 可能跨过很多分钟（压缩、长时间推进）。NEURA 不能逐分钟重跑，但也不能漏。

```
crossed_minutes = [m for m in (start, now] 中每个 10 分钟的格点 if 有 NPC 计划落在 m]
                 ∪ [每个跨过的日界]

处理顺序：按 minute 升序；同一 minute 内按 (importance desc, npc_id) 稳定排序。
上限：MAX_CATCHUP_STEPS = 8。超过则：
  1. 先处理「计划已到期」的（必须收敛，否则永远卡住）；
  2. 其余记 journal(kind="catchup_deferred", count=N)；
  3. 远场层直接跳到最新分钟，不再逐点补（远场本来就是日粒度）。
```

- 补算**只读** CHRON 给的分钟，**不修改** `advance_to` 的语义、不改它的返回值。
- 补算的结果与「逐分钟跑」**不保证**逐步等价，但保证：**终态收敛**（所有到期计划都被处理）、
  **幂等**（同一 `last_tick_minute` 重跑不重复）、**可解释**（`journal` 里有 `catchup_deferred` 的记录）。
  这三条是 N5 的验收项。

---

## 8. 连续性与成本（GDD §11.6）

### 8.1 五个手段（对应 GDD §11.6 的五项）

| GDD 手段 | 本文落点 |
|---|---|
| **规则优先** | 默认 `tier = "rule"`：日程 + 需求阈值 + 硬约束。模型是**可选增强**（§8.4） |
| **事件触发** | `important_far` 只在事件命中时醒（§7.2），不做「每回合全员思考」 |
| **计划队列** | 每个 NPC 只有**一个**当前 `plan`（§4.2），`steps[]` 是队列；不做多计划调度 |
| **分层模型** | 四层四档精度四档预算（§7.2） |
| **记忆检索** | `memory` 按 `salience` 取前 K（默认 K=5）进上下文，不塞全量 |

### 8.2 成本预算模型（可计算）

**单次调用成本（token 估算，写死为常数便于算账）**

| 档 | 典型输入 | 典型输出 | 记账 `tokens` | 用途 |
|---|---|---|---|---|
| **H 强模型** | 3,000 | 1,000 | **4,000** | 关键决策候选、长对话、跨目标取舍 |
| **M 轻模型** | 900 | 300 | **1,200** | 日常候选、记忆摘要 |
| 规则 | — | — | **0** | 日程推进、需求阈值、硬约束 |

**账本结构（落 `blocks.neura.budget`）**

```json
{"day": 12, "strong_calls": 3, "light_calls": 7, "tokens": 20400}
```

**公式**

```
cost(day) = 4000 × strong_calls + 1200 × light_calls
day_cap   = NPC_DAILY_TOKEN_BUDGET = 60000
layer_cap = {"near": 17600, "important_far": 26400, "background": 0, "unexplored": 0}
reserve   = 60000 − (17600 + 26400) = 16000        # 玩家交互触发的即时反应
```

- **跨日重置**：`world_minute // 1440 != budget.day` → 新的一天，计数器归零（重置动作随下一次提交落盘）。
- **超限不抛错**：`cost + next > day_cap` → **不调模型**，走规则候选，`journal.cost.tier = "rule"`。
- **单次 tick 突发上限**：`≤ 1 H`、`≤ 3 M`（§7.2）。
- **`tokens` 是估算值，不是账单**：真实账单由 AI Gateway（`GDD.md` §18.5）统计；
  这里的常数只用于**预算约束与可测断言**，偏差不改变判定（超限即降级）。

**为什么以游戏日为单位**：现实会话长度不受控（玩家可能挂机 8 小时），
而世界时间只在提交时前进（`CHRON-DESIGN.md` Key Decisions 9）——
**游戏日**是唯一同时满足「可预算」与「不可刷」的刻度。

### 8.3 降级阶梯

| 阶梯 | 触发 | 行为 | 玩家能感知的变化 |
|---|---|---|---|
| L0 强模型 | 预算充足 + `near` 层关键决策 | H 档候选 + 规则复验 | NPC 行为更有「意图感」，理由更细 |
| L1 轻模型 | 预算 ≥ 80% 或 `important_far` 层 | 只允许 M 档 | 行为正确但更循规蹈矩 |
| L2 规则 | 预算耗尽 / 模型超时 / 非法 JSON / 无配置 | 日程 + 需求阈值 + 硬约束 | NPC 仍按点上班、按需采购、按令巡逻 |
| L3 冻结 | 该 NPC 的 `plan` 已 `dropped` 且无可行选项 | 只写 `journal(kind="idle")`，不提交 | 这个人「今天没做什么」——但位置与日程仍在走 |

- **降级是单调的**：当天一旦降到 L2，**不回到 L1**（避免「省下的预算被一阵风花掉」）。
- **降级必须留痕**：`journal.cost.tier` + `budget` 计数；`GET /api/neura/view`（N8）可看到。
- **L3 不是死锁**：`needs` 仍在涨（时间流逝），下一天重置预算后自然恢复。

### 8.4 离线可玩底线（GDD §20.4）

**无 AI 配置时，NPC 必须仍然「活着」**：

1. 日程照走：`schedule` slot 到点 → `npc.move` 提交 → 玩家能看到「铁匠开门了」。
2. 需求阈值照算：`needs.safety ≥ 80` → 逃向最近的安全地点；`needs.wealth ≥ 75` → 去市场。
3. 硬约束照拦：没知识就不会去（`E_KNOWLEDGE`），没钱就买不了（`E_RESOURCE`）。
4. 叙事用模板：复用 M8 的 `fallback` 通道（`ACTION-DESIGN.md` §7.3），**不新增兜底文案体系**。
5. **绝不**因为模型缺失而让 tick 抛错或跳过：`generate_candidates` 返回 `[]` 是**正常路径**。

**离线下的「可解释」不打折**：`journal` 仍然记 `terms` / `scores` / `chosen` / `switch_reason`，
只是 `cost.tier == "rule"`、`ai_used == false`。

### 8.5 成本核算示例（一个游戏日）

| 项 | 次数 | tokens |
|---|---|---|
| 近场：2 次 H（关键目标取舍） | 2 | 8,000 |
| 近场：6 次 M（日常候选） | 6 | 7,200 |
| 远场：3 次 H（势力领袖日决策） | 3 | 12,000 |
| 远场：10 次 M | 10 | 12,000 |
| 背景 / 未探索 | 0 | 0 |
| **小计** | 21 | **39,200** |
| 余量（玩家交互触发） | ≤ 13 次 M | 15,600（上限 16,000） |
| **日合计上限** | — | **60,000** |

> 断言口径：`4000*h + 1200*m ≤ 60000` 且 `h ≤ 6`、`m ≤ 30`（由 60,000 反解）。

---

## 9. 接口面与落点

### 9.1 模块与依赖

```
notdnd_web.py（编排：tick 挂接 / 内部分支 / 只读视图）
   │  注入 view（只读）          ▲ 返回新块 + 待提交清单
   ▼                             │
neura.py（纯函数；只 import 标准库）
   │  只读投影
   ├── atlas.range_band / exits / find_place   （由编排层算好塞进 view，neura 不 import）
   ├── chron.world_time_minute / advance_to     （同上，只传整数分钟）
   └── prism_guide.secret_allowed_for / redact_for_member （只在编排层与输出侧调用）
```

**硬约束（`GDD-BASELINE.md` §4.2 的落地）**

- `neura.py` **不得** import `state` / `notdnd_web` / `prism_core` / `atlas` / `chron` / `prism_guide`
  （只用标准库 + 鸭子类型的 `view` / `port`）。这条比 `GDD-BASELINE.md` §4.2 更严，理由见下。
- **为什么连 `atlas` / `chron` 都不 import**：
  ① 这两个模块的实现变化（例如 `range_band` 的 band 名）不应连带 NEURA 的单测；
  ② `import` 失败必须**不影响离线主路径**（Issue 第 6 项验收）——不 import 就没有失败面；
  ③ `view` 的形状是本文钉死的**契约**，比函数调用更好测（给定 dict → 断言 dict）。
- **反向依赖禁止**：`state` / `chron` / `atlas` / `prism_core` / `prism_guide` **不得** import `neura`。

### 9.2 `state.py`：`blocks.neura` 不透明块

| 项 | 结论 |
|---|---|
| 块名 | `"neura"`（与 `rules` / `atlas` / `guide` / `chron` / `room` 同级） |
| 谁解析 | **没人解析**。`state.py` 只按 `changes` 机械写入（`state.py:226`），`neura.py` 自己解析自己写的内容 |
| 创世态 | `_genesis_blocks()`（`state.py:491`）**不改**：`neura` 块在**首次提交**时出现（`replace_block` 会建键，`state.py:235`） |
| 版本 | `NEURA_VERSION = 1`；块内 **`version` 与 `schema_version` 两键同值**——`version` 是**权威键**，`schema_version` 只是兼容别名（两者不一致时以 `version` 为准）；任一键高于 `NEURA_VERSION` 都拒载 |
| 迁移 | 惰性（`STATE-DESIGN.md` §10.1）：`migrate_block(raw, notes=None)` 补缺键、不覆盖合法值；**更高版本拒载**（抛 `NeuraError`），与 `chron.migrate_block`（`chron.py:2323`）同款 |
| 失败降级 | 脏块（解析失败 / 校验不过）→ 丢弃该块并重建空块，记 `notes`；**不阻塞世界时间推进**（与 `chron.migrate_block` 的脏值降级同口径） |
| 大小上限 | `JOURNAL_MAX = 256`（整战役）、`MEMORY_MAX = 128` / NPC；超过按 §4.2 规则裁剪。估算：200 NPC × (0.5 KB 记忆 + 0.2 KB 状态) + 256 × 0.4 KB ≈ 240 KB——与 `STATE-DESIGN.md` §6.3 的 1 MiB 量级阈值同一数量级，可接受；若实测越过 512 KiB 再评估分片（§12 P3） |

**N1 实现裁定（2026-10-18 回填；源头：PR #345（Issue #339）的「风险」节）**

> 下列 5 条是 N1（`neura.py`）**已经落地**的口径，回填单为 Issue #346。N2–N8 一律按此写；
> 与本文其它段落冲突时**以本节为准**，并另开文档 PR 修正原文，**不得**在实现里分叉。

1. **版本键双写**：`version` 与 `schema_version` **同值**，`version` 为准——唯一口径见本节上表「版本」行。
2. **`budget` 是块级**：每战役**一个**账本，落 `blocks.neura.budget`，与 `npcs` 平级；
   `npcs[<npc_id>]` 里**没有** `budget`（§4.2「块级字段」、§8.2、§5.8 的 `block["budget"]`）。
3. **`needs` / `memory` / `plan` 在 `npcs` 条目内**：由 `empty_npc()` 提供；块顶层**不复制**
   这些字段（两份真相会漂移）。依据 §4.2 的 `npc_id` 行「块内以 `npcs` 字典的键表达」。
4. **`stable_unit` 用 `blake2b`**：内置 `hash()` 按进程加盐（`PYTHONHASHSEED`），跨进程漂移会破坏
   「同种子同结果」；只承诺同输入同输出与统计均匀，**不承诺**绝对无碰撞（§5.3）。
5. **`plan` 空态不写 `null`**：为空时 `plan` 键**不存在**（§4.2 字段级不变量）；`migrate_block`
   遇到 `null` / 空 dict 会丢弃该键并记 `notes`，不保留 `null`。

**提交形态（跨块，A0 合规）**

```json
{"base_seq": 41, "idempotency_key": "np_3f9a1c02_8b7d4e0a55_01",
 "type": "npc.move", "actor": "npc-05", "target": "gate_north",
 "cause": "yunji:e000000000040",
 "visibility": {"scope": "public", "subjects": []},
 "changes": [
   {"block": "neura", "op": "replace_block", "path": "",
    "before": {"…": "旧块"}, "after": {"…": "新块"}},
   {"block": "guide", "op": "set", "path": "/npc_at/npc-05",
    "before": "market_row", "after": "gate_north"}
 ],
 "rules_version": "prism@data-79", "roll_refs": [],
 "world_time": {"world_minute": 17130, "day": 12, "tod": 90},
 "source": "npc"}
```

> 一条事件、两个块（U4）。`before` 用于审计，`after` 是折叠的依据（`STATE-DESIGN.md` §5.3）。

### 9.3 `chron.py`：零改动（只读）

| 用途 | 调用 | 改动 |
|---|---|---|
| tick 时间 | `advance_to` 的返回值 `world_minute` | 无 |
| 跨过的到期点 | 返回值 `compression_log.fired_minutes` | 无 |
| 屏障 | 返回值 `stopped_at` / `barriers` | 无 |
| 世界时间折分钟 | `chron.world_time_minute(world_time)` | 无 |
| `world_time` 形状 | 提交时由调用方从 CHRON 状态取（`CHRON-DESIGN.md` §3.1） | 无 |

- **结论**：`CHRON-DESIGN.md` 的「零改动或只读」在此**完全成立**——NEURA 不新增 `chron` 的 API，
  也不改 `chron` 块。若将来要把 NPC 计划并入 `chron.actions`，那是**另一条设计单**（§12 P1）。

### 9.4 `notdnd_web.py`：三处、都很薄

| 位置 | 改动 | 风险 |
|---|---|---|
| `_action_settle_commit`（`:2730`） | 新增关键字参数 `source: str = "player"`，替换 `:2804` 的字面量 `"player"` | **最低**：默认值保持玩家路径逐字节不变 |
| `_neura_tick(session, view)`（新增，N7） | 在 `_chron_advance`（`:3333`）的**锁内**、叙事段（锁外）之前调用；结果组装成 `changes` 提交 | 中：必须持锁提交，且不能与 `_chron_advance` 的 `seq` 竞争 |
| `GET /api/neura/view`（新增，N8，可选） | 只读：返回某个 NPC 的 `layer / needs / plan / 最近 5 条 journal`（按 `visibility` 过滤） | 低：纯读 |

**时序约束（N7 的核心）**

```
_chron_advance(session, target)
  └ 锁内：advance_to + 结算玩家到期卡 + clock.advance（既有，不动）
  └ 锁内：_neura_tick(session)            ← 新增：同一次持锁内，紧接着
       ├─ view = build_view(session, handle.view())
       ├─ block = neura.tick(handle.view()["blocks"].get("neura"), view, port=…)
       └─ handle.commit(changes=[neura 块])  ← 每个 NPC 一次提交（A0）
  └ 锁外：叙事（既有，不动）
```

- **为什么在锁内**：tick 的输入是 `handle.view()`，输出要 `commit()`；跨锁会读到过期 `seq`（409 风暴）。
- **为什么在叙事之前**：玩家该看到「守卫已经走到门口」，而不是「先讲完故事再挪人」。
- **失败隔离**：`neura.tick` 抛任何异常 → 捕获 + `log` + **不阻塞** `_chron_advance` 返回
  （离线底线：NPC 不动，世界照走）。

### 9.5 `atlas.py`：只读查询，零改动

| 用途 | 调用 | 谁调 |
|---|---|---|
| 距离档 | `atlas.range_band(atlas, frame_id, a, b)`（`:335`） | 编排层（构造 `view.bands`） |
| 可达性 | `atlas.exits(atlas, locus)`（`:223`） | 编排层（构造 `view.places[].exits`） |
| 存在性 | `atlas.find_place(atlas, place_id)`（`:179`） | 编排层（校验 `view` 自洽） |

- NEURA **不改地图**、**不加地点**、**不写 delta**：NPC 的位置是 `neura` 块里的一个字段，不是地图实体。
- 地图级移动（把某个地点标记为「已探索」）仍由 ATLAS 的既有入口负责，NEURA 只发信号（§7.1 未探索层）。

### 9.6 事件类型与 `visibility`

| `type` | 何时产出 | `changes` | 默认 `visibility` |
|---|---|---|---|
| `npc.plan` | 计划落定 / 切换 / 丢弃 | `neura` 块（`plan` + `journal`） | `{"scope":"gm","subjects":[]}`（计划内部态不下发） |
| `npc.move` | 位置变更 | `neura.location` + `guide.npc_at` | 玩家在该地点或相邻 → `public`；否则 `gm` |
| `npc.knowledge` | 获得 / 修正 claim、秘密流转 | `neura.knowledge` + `neura.secrets` | `gm`（披露另走 `tell` / 叙事） |
| `npc.memory` | 记忆追加 / 裁剪 | `neura.memory` | `gm` |

- `type` 形如 `<domain>.<verb>`，登记进 `STATE-DESIGN.md` §5.1 的**可扩展**枚举（`clock.advance` 等已登记；
  这四条是**新增的域**，不改 state 的校验规则，只在该文档的枚举清单里追加——**本单不改该文档**，
  由 N6 的实现单在 PR 说明里登记）。
- **一次 tick 一个 NPC 一条事件**：`plan` 与 `move` 若同时发生，合并成一条 `npc.move`（`changes` 里两处路径），
  `type` 取「更可见」的那个。**不得**为同一次决策写两条事件（A0）。

### 9.7 `import` 失败与降级

| 情形 | 行为 |
|---|---|
| `neura.py` 缺失 / import 失败 | `_neura_tick` 直接返回 `None`；`_chron_advance` 与 M8 管线**完全不受影响**（等价于「本局没有活体世界」） |
| `blocks.neura` 缺失（老档） | 视为空块；**首次 tick 不写任何东西**（因为没有 NPC 运行时态），等 N5 的「初始化」路径或内容切片 |
| `blocks.neura` 损坏 | 丢块 + `notes`，不抛；下个 tick 重建空块 |
| `view` 构造失败（atlas 不可用） | 跳过 tick，记 `neura_skipped`；NPC 停在原地，日程不推进 |
| 模型不可用 | L2 规则档（§8.3） |

> 这一节的验收方式：**删掉 `neura.py` 后 `bash tests/run_all.sh` 仍然全绿**（N6 的回归断言）。

---

## 10. 可测口径（M11 出口条件 → 断言）

### 10.1 三条出口条件逐条断言化

**出口 1 · NPC 行为连续可解释**（`GDD-BASELINE.md` §5 M11 行）

| # | 断言 | 测试思路 |
|---|---|---|
| 1.1 | 每条 `decision` 的 `scores` 非空，且 `chosen ∈ scores[].option_id` | 构造 2–3 个候选，断言选择来自候选集 |
| 1.2 | `chosen != None` 时，若 `plan.goal_id` 与上一计划不同，则 `switch_reason` ∈ 六个合法值 | 连跑两个 tick，强制切换目标，断言理由存在 |
| 1.3 | 每个 `chosen` 都能在 `journal` 里追到 `terms`（逐需求分解），且 `Σ terms.product == raw`（整数容差） | 手工复算一条决策 |
| 1.4 | `E_*` 拒绝码只出现在 `journal.rejected`，**不出现**在事件 `changes` 里 | 构造「没钱买马」→ 断言无 `npc.move` 事件、有 `E_RESOURCE` |
| 1.5 | 同一 `(seed, npc_id, minute, option_id)` 连算 100 次 → `stable_unit` 全等 | 纯函数单测 |
| 1.6 | 同一块 + 同一 `view` 连跑 10 次 `tick` → 序列化结果逐字节相等 | 纯函数单测（无 IO） |

**出口 2 · 时间推进可回溯**

| # | 断言 | 测试思路 |
|---|---|---|
| 2.1 | `replay(genesis, events[1..N])` 的 `blocks.neura` 与 `live(N)` 在规范化 JSON 下相等 | 跑 100 个 tick 后重放（`STATE-DESIGN.md` §6.2 不变量） |
| 2.2 | 同一 `seg` 的两次 `advance_to`（含重启）→ `neura` 块与事件序列全等 | 保存 → 重开句柄 → 断言 |
| 2.3 | tick 处理的分钟集合 ⊆ `advance_to` 覆盖的分钟区间，且**每个到期计划都被处理** | 构造 3 个不同 `until_minute` 的计划，跨 1 天推进，断言全部收敛 |
| 2.4 | 屏障处不执行远场批量推进：`stopped_at != None` 时，远场层的 `last_tick_minute` 不变 | 造一个 B1 屏障，断言 |
| 2.5 | `journal` 裁剪是确定性的：同一序列裁两次结果相同；裁剪后 `replay` 仍相等 | 造 300 条决策，断言 `len ≤ 256` 且两次结果相同 |
| 2.6 | 幂等：同一 `idempotency_key` 重投 → 事件数不变、`needs` / `memory` 不重复变 | 状态层 `indexes.idempotency` 命中（`STATE-DESIGN.md` §7） |

**出口 3 · 成本可控**

| # | 断言 | 测试思路 |
|---|---|---|
| 3.1 | `4000*h + 1200*m ≤ 60000` 恒成立（每游戏日） | 桩替身统计调用次数；跑 5 个游戏日 |
| 3.2 | 预算 100% 用尽后 `tier == "rule"`，模型调用次数 = 0 | 把 `budget.tokens` 预置为 60000，跑一天 |
| 3.3 | 预算 ≥ 80% 时只允许 M 档（`strong_calls` 不增） | 预置 48000，跑一天 |
| 3.4 | 降级单调：当天降到 L2 后不再回 L1 | 造「中途超时」，断言后续 `tier` 不回升 |
| 3.5 | 无 AI 配置：全日模型调用 0，NPC 仍产生 `npc.move` 事件（≥ 1 条/日/有日程的 NPC） | 传 `port=None` / `ai=None` |
| 3.6 | 单次 tick 突发上限：`strong_calls` 增量 ≤ 1、`light_calls` 增量 ≤ 3 | 造 20 个近场 NPC 同时到期 |

### 10.2 测试清单（对应 `GDD.md` §24.2 的测试类别）

| 类别 | 本里程碑覆盖 | 交给谁 |
|---|---|---|
| 规则单元测试 | ❌ 不涉及（NEURA 不掷骰） | `prism_core` |
| 时间调度测试 | ✅ 2.2 / 2.3 / 2.4（只读 CHRON，不改它） | 本文 + `CHRON-DESIGN.md` §9 |
| 多人并发测试 | ✅ 每成员知识隔离（§6.3 的 8 码位断言） | 本文 + `MULTIPLAYER-DESIGN.md` §11 |
| 知识隔离测试 | ✅ 主战场：§6.3 / 6.4 的断言 | 本文 |
| 存档测试 | ✅ 2.1 / 2.5 / 迁移往返 | 本文 + `STATE-DESIGN.md` §14 |
| 长期模拟测试 | ✅ 100 游戏日：事件数 ≤ NPC 数 × 日数 × 2；块大小 < 1 MiB；重放相等 | 本文（N8 的长跑） |
| AI 故障测试 | ✅ 超时 / 非法 JSON / 越权实体 / 无配置（§5.5 / §8.3） | 本文 |
| 视觉 / UX 测试 | ❌ 本里程碑无 UI 改动 | UI 轨 |

### 10.3 可观测（`GDD.md` §24.3）

`journal` 是**唯一**的 NPC 决策留痕，字段固定（§5.4 的 `DecisionRecord`）：

```
decision_id / npc_id / at_minute / layer / hard{ok,options,rejected[]} /
scores[{option_id, terms[], raw, penalty, jitter, score}] /
chosen / switch_reason / cost{tier,tokens} / ai_used / ai_failed
```

- 一次玩家输入的追踪链因此完整：
  `玩家请求 → 行动事件(seq) → CHRON 推进 → neura.decision(journal) → npc.* 事件(seq) → 叙事`。
- **不含**自由文本（`why` 字段只在 AI 候选里，且**不进 journal**——避免把 AI 文本当审计依据）。

### 10.4 不测什么（明确边界）

- 不测「NPC 的行为是否符合人类直觉」——那是玩法验收（`GDD.md` §24.4），需真人试玩。
- 不测模型质量（候选好不好）——只测「候选非法时被丢弃」（§5.5）。

---

## 11. 实现切片建议 N1…N8

> **公共约束**（每条都适用）：从最新 `master` 开分支；尽早开 Draft PR；**不合并**；
> 真跑 `bash tests/run_all.sh` 与 `bash tests/content_firewall.sh` 并把命令与结果写进 PR；
> 不把新文件写进 `tests/test_repo_layout.py` 的必需清单。
>
> **串行关系**：N1→N2→N3→N4→N5 同占 `neura.py`（**独占，隐式串行**）；
> N6→N7→N8 同占 `notdnd_web.py`（**独占，隐式串行**）。两条队列之间 N6 只依赖 N3 的产出结构，
> 因此 N6 可在 N4/N5 合并前**起草**，但**合并顺序**必须是 N3 → N6。

### N1 · NEURA 内核：块结构 + 惰性迁移 + 纯函数骨架

- **目标**：`neura.py` 落地 `NEURA_VERSION`、`empty_block()`、`migrate_block()`、`prune_block()`、
  `stable_unit()`；`state.py` **零改动**（验证「新块名可写」）。
- **验收标准**
  - [ ] `empty_block()` 返回的块过 JSON 往返（`json.dumps` → `loads`）逐字段相等。
  - [ ] 缺 `needs` 六键的旧块 → `migrate_block` 补 50；已有的合法值**不被覆盖**；更高版本抛 `NeuraError`。
  - [ ] `prune_block` 对 300 条 journal / 200 条 memory 裁到上限，且**两次裁剪结果相同**。
  - [ ] `stable_unit("a","b")` 与 `stable_unit("a","b")` 全等；`stable_unit("a","c")` 不同；100 万次分布均匀（±1%）。
  - [ ] `import neura` 无副作用、无 IO、不 import 业务模块（用 `sys.modules` 断言）。
  - [ ] `bash tests/run_all.sh` / 内容防火墙 退出码 0。
- **可改路径**：`neura.py`（新建）、`tests/test_neura.py`（新建）
- **禁改路径**：`state.py`、`chron.py`、`atlas*.py`、`prism_core.py`、`prism_guide.py`、`notdnd_web.py`、
  `static/**`、`data/**`、`docs/**`、`tests/test_repo_layout.py`
- **依赖**：无
- **标签**：`难度：高`；`能力：编程`、`能力：数据`、`能力：测试`

### N2 · 硬约束过滤（距离 / 时间 / 资源 / 法律 / 知识）

- **目标**：`hard_filter` 与五个 `check_*`；固定错误码；`view` 契约落地（§5.2.1）。
- **验收标准**
  - [ ] 五个 `E_*` 码各有一条真实断言（构造必然命中的输入）。
  - [ ] `view` 缺 `laws` → `law_source == "none"`，**不**抛错、不误拒。
  - [ ] 距离用 `bands` 与 `travel_minutes` 两条路都能判（`same/adjacent/near` 直通；`far` 但 `minutes` 够 → 通）。
  - [ ] 全灭时返回 `{"ok": False, "options": []}`，`rejected` 里**每个**候选都有码，不吞。
  - [ ] 纯函数：不读盘、不联网、不改入参（深比较入参前后相等）。
- **可改路径**：`neura.py`、`tests/test_neura.py`
- **禁改路径**：同 N1
- **依赖**：N1 已合并
- **标签**：`难度：高`；`能力：逻辑`、`能力：编程`、`能力：测试`

### N3 · 需求评分 + 计划选择 + 决策记录

- **目标**：`score_options` / `choose` / `decide` / `to_proposal`（M8 提案结构）；`journal` 结构。
- **验收标准**
  - [ ] 六个需求的 `terms` 逐项可复算，`Σ product == raw`（±1e-9）。
  - [ ] 同输入同输出（连跑 10 次逐字节相等）。
  - [ ] 并列分数用 `(score, -risk, option_id)` 稳定取一（构造三个同分候选）。
  - [ ] 目标切换必须带 `switch_reason`（六值之一）；同目标续计划时 `switch_reason == None`。
  - [ ] `to_proposal` 的输出过 `prism_core.validate_proposal`（若已存在）或与 `ACTION-DESIGN.md` §4.2 的
        必填字段集逐项对齐（字段名、类型、必填性）。
  - [ ] 幂等键格式过 `^[A-Za-z0-9_-]{8,64}$` 且同计划同步骤恒定。
- **可改路径**：`neura.py`、`tests/test_neura.py`
- **禁改路径**：同 N1（**不得**因为要过校验去改 `prism_core.py`；不够就退回开单）
- **依赖**：N2 已合并
- **标签**：`难度：高`；`能力：逻辑`、`能力：编程`、`能力：测试`

### N4 · 知识边界：五类 claim + `select_context` + 修正链

- **目标**：`Claim` / `Memory` 结构；`select_context()`（**唯一**上下文收口）；`learn()` / `supersede()`；
  `build_candidate_body()`。
- **验收标准**
  - [ ] 五类分离：`belief/rumor/inference` **永不**出现在 `world_facts` 投影里（断言）。
  - [ ] §6.3 的双 NPC 例子：A 可见、B 不可见；B 的结果**不含**秘密的任一 8 码位窗口。
  - [ ] `purpose` 三种取值的上下文**字段集**各自固定（不多不少）。
  - [ ] 修正链：旧 claim `status="disproved"` + `superseded_by`；旧 id 仍可解析。
  - [ ] `select_context` 的输出里**不含**任何其他 NPC 的 `proposition`、不含 `secrets[].proposition`。
  - [ ] 除 `build_candidate_body` 外，`neura.py` 没有第二处拼 `messages`（grep 断言：`"role":` 只出现一次）。
- **可改路径**：`neura.py`、`tests/test_neura.py`
- **禁改路径**：同 N1 + `prism_guide.py`（只调用，不改判定）
- **依赖**：N3 已合并
- **标签**：`难度：高`；`能力：逻辑`、`能力：数据`、`能力：测试`

### N5 · 分层模拟 + 每日预算 + 降级阶梯 + 补算

- **目标**：`layer_of`、`due_npcs`、`budget` 记账、`degrade_tier`、`catch_up`（§7.4）。
- **验收标准**
  - [ ] 四层的判定按 §7.1 短路顺序；`player` 走远一步 → 立刻降层（同一 tick 内可见）。
  - [ ] 日预算公式：`4000h + 1200m ≤ 60000`；跨日重置（`day` 变化 → 计数归零）。
  - [ ] 80% / 100% 两档降级各一条断言；降级**单调**不回弹。
  - [ ] `catch_up` 上限 8 步；超出记 `catchup_deferred`；**所有到期计划都被收敛**（断言终态）。
  - [ ] 无 AI 配置下仍产生日程移动事件（≥ 1 条 / 日 / 有日程 NPC）。
  - [ ] 单 tick 突发上限（`≤1 H` / `≤3 M`）。
- **可改路径**：`neura.py`、`tests/test_neura.py`
- **禁改路径**：同 N1
- **依赖**：N4 已合并
- **标签**：`难度：高`；`能力：逻辑`、`能力：数据`、`能力：测试`

### N6 · M8 内部分支：`_neura_submit` + `source` 参数化

- **目标**：`notdnd_web._neura_submit(session, proposal, *, idempotency_key)` 复用 `_action_settle_commit`；
  `_action_settle_commit` 增加 `source="player"` 参数（默认不变）；**不加 HTTP 路由**。
- **验收标准**
  - [ ] 玩家路径逐字节不变：既有 `tests/test_notdnd_web.py` 全绿，且 `commit` 事件的 `source == "player"`。
  - [ ] NPC 路径：`source == "npc"`、`actor == npc_id`、事件 `changes` 同时含 `neura` 与 `guide`（若走动）。
  - [ ] 幂等：同键重投 → 返回首次结果、事件数不变。
  - [ ] `base_seq` 过期 → `conflict`（不抛），调用方可重试。
  - [ ] 不支持的计划（需要掷骰 / `kind` 不在动作表）→ `unsupported` / `rejected`，**不写事件**（U10）。
  - [ ] 删掉 `neura.py` 后 `bash tests/run_all.sh` 仍全绿。
- **可改路径**：`notdnd_web.py`、`tests/test_notdnd_web.py`
- **禁改路径**：`prism_core.py`、`prism_guide.py`、`chron.py`、`state.py`、`atlas*.py`、
  `static/**`、`data/**`、`docs/**`、`ACTION-DESIGN.md`
- **依赖**：N3 已合并（消费提案结构）；**独占 `notdnd_web.py`**
- **标签**：`难度：高`；`能力：编程`、`能力：逻辑`、`能力：测试`

### N7 · 编排接线：`_chron_advance` 之后 tick

- **目标**：`_neura_tick(session)`：构造 `view` → `neura.tick` → 逐 NPC 提交；失败隔离。
- **验收标准**
  - [ ] `advance_to` 返回后，`blocks.neura` 已更新且 `last_tick_minute == world_minute`。
  - [ ] 每个 NPC 每次 tick **最多 1 条事件**（A0 断言：`event_ids` 长度）。
  - [ ] `neura.tick` 抛异常 → `_chron_advance` 正常返回（`neura_skipped` 记录），玩家到期卡照常结算。
  - [ ] 屏障处（`stopped_at != None`）不跑远场补算（§10.1 的 2.4）。
  - [ ] 并发：两个线程同时 `_chron_advance` → 不出现重复 `neura` 事件（`base_seq` 冲突被正确归类）。
  - [ ] 重启后 `advance_to` 到同一分钟 → `neura` 块与事件序列全等（§10.1 的 2.2）。
- **可改路径**：`notdnd_web.py`、`tests/test_notdnd_web.py`
- **禁改路径**：同 N6
- **依赖**：N5 已合并；N6 已合并；**独占 `notdnd_web.py`**
- **标签**：`难度：高`；`能力：编程`、`能力：逻辑`、`能力：测试`

### N8 · 只读观察面与长跑回归（可选）

- **目标**：`GET /api/neura/view?npc_id=…`（只读、按 `visibility` 过滤）；100 游戏日长跑测试。
- **验收标准**
  - [ ] 端点返回 `layer / needs / plan / 最近 5 条 journal`；**不含**自由文本、不含 `secret.proposition`。
  - [ ] 无权看的 NPC（`gm` 范围）→ 404 或 `{"status":"forbidden"}`，不泄露存在性。
  - [ ] 长跑：100 游戏日、20 个 NPC、无 AI → 事件数 ≤ 20 × 100 × 2；`blocks.neura` 序列化 < 1 MiB；`replay` 相等。
  - [ ] 长跑耗时 < 30 秒（纯规则下不进模型；`tests/run_all.sh` 可承受）。
- **可改路径**：`notdnd_web.py`、`tests/test_notdnd_web.py`
- **禁改路径**：同 N6
- **依赖**：N7 已合并
- **标签**：`难度：中`；`能力：编程`、`能力：测试`

### 附：切片依赖图

```
N1 ─► N2 ─► N3 ─┬─► N4 ─► N5 ─┐
                └─► N6 ────────┴─► N7 ─► N8
```

- `neura.py` 独占：N1–N5（**不得并行**）。
- `notdnd_web.py` 独占：N6–N8（**不得并行**）。
- N6 只消费 N3 的**提案结构**，因此可在 N4/N5 合并前起草；但**合并顺序** N3 < N6。

---

## 12. 未决与待拍板

> 以下每一项都**不阻塞** N1–N5 的起草（纯函数层），但**阻塞**对应实现单的「可合并」判断。
> 建议由人（或人授权 Master Agent）逐条给结论，写回本条 Issue 或另开决策 Issue。

| # | 事项 | 为什么需要人（而不是 Agent 自决） | 建议 | 阻塞哪片 |
|---|---|---|---|---|
| **P1** | **NEURA 与 LOREX 的边界**：NPC 的对白、旁白、秘密「说出口」的措辞归谁？ | 这是**产品语义**：一旦 NEURA 开始生成文本，就会与 `prism_guide` 的叙事预算、缓存策略、内容边界（铁律 1）交叉；Agent 单方面扩大 NEURA 的职责会破坏「叙事不是事实」的架构 | **NEURA 只出结构化计划与记忆摘要，不出一句玩家可见文本**；对白归 LOREX（`prism_guide`），NEURA 只通过 `select_context` 提供「这个人知道什么」。本文按此写 | N4（若改为 NEURA 出文本，N4 要重写上下文契约） |
| **P2** | **NPC 是否需要掷骰判定**（说服玩家、偷窃、潜行）？ | 影响 **PRISM 是否要新增「NPC 作为行动主体」的入口**（`perform_action` 目前只认 `rules.active_unit_id` 与场景动作表，`prism_core.py:1007`）。这是跨里程碑的契约扩张 | **M11 第一版不做掷骰**：NPC 只做「无判定」计划（移动 / 工作 / 等待 / 报告 / 交易意向）。需要判定时**降级为提案被拒 + 记录未执行**（U10）。掷骰入口另开 M11-b 设计单 | N6（`unsupported` 分支的语义）、N7 |
| **P3** | **记忆的存储载体**：`blocks.neura.memory`（本文）vs 独立文件 vs 将来 SQLite？ | 涉及**存档体积与迁移成本**：`STATE-DESIGN.md` §4.2 选的是 JSON 文件族，但记忆是唯一会**持续增长**的数据。选错会让检查点膨胀到不可用 | **先落 `blocks.neura`（不透明块），并设硬上限**（`MEMORY_MAX=128`/NPC、`JOURNAL_MAX=256`/战役）。若 200 NPC 的实测块 > 512 KiB，再评估「记忆分片到 `neura-memory.json` + 块内只留索引」（需改 STATE 契约，另开单） | N1（块结构）、N5（裁剪） |
| **P4** | **每游戏日成本上限的具体数值**（本文建议 60,000 tokens / 日：H ≤ 6、M ≤ 30） | 这是**商业决策**（模型单价 × 目标留存），不是工程常数；Agent 无定价信息，也不应替产品定成本 | 60,000 tokens/日 为**建议默认**，做成常量（`NPC_DAILY_TOKEN_BUDGET`）便于一处调参；上线前按实际单价复核 | N5 |
| **P5** | **「重要远场」的重要性阈值**（本文建议 `importance ≥ 0.7`） | 影响**内容作者负担**与**性能**：阈值太低 → 全员近场（成本爆炸）；太高 → 世界「死」。这需要玩法判断 | 0.7 起步，用 N8 的长跑数据（每日 H/M 使用率）回调；阈值写成常量 | N5 |
| **P6** | **`guide.npc_at` 的权威迁移**（U4：NEURA 权威、guide 派生） | 会改变**既有提示词的在场名单来源**（`prism_guide._npc_locus`）。虽是展示层，但会影响玩家可见的对白归属 | 接受 U4；N7 在同一提交内双写，保证不漂移。若担心风险，可先做「只读镜像」（NEURA 写、guide 只读不写回），观察一版再切换 | N7 |
| **P7** | **NPC 计划是否并入 `chron.actions`** | 决定**是否为 CHRON 增加「非玩家行动」的语义**（影响 `CHRON-DESIGN.md` §4.2 的八状态转换表） | **本文不并入**（U5），NPC 用 `plan.until_minute` + tick 补算。若第二版需要「NPC 长行动被玩家打断」的完整语义，再开 CHRON 契约变更单 | N5（补算）/ 将来 |
| **P8** | **法律约束的数据来源** | 世界包目前没有结构化的「法律」表；`view.laws` 的 schema 一旦定死，内容侧要跟着改 | 第一版允许 `law_source = "none"`（空表、不拒），把 `view.laws` 的 schema 留到 M12 世界生成时定 | N2 |
| **P9** | **`neura.*` 事件类型是否登记进 `STATE-DESIGN.md` §5.1 的枚举** | 该文档是**禁改路径**；新增枚举要不要改文档，是治理问题（`AGENTS.md` §8 `docs/` 单独成 PR 的纪律） | 实现单在 PR 说明里登记「新增四个 `type`」；`STATE-DESIGN.md` 的枚举清单另开文档 PR 同步（不夹带） | N6 |
| **P10** | **`importance` 的派生算法**（role/faction/threads 权重） | 内容判断：谁是「重要 NPC」应由作者决定还是公式推导？错了会让主线角色被当背景模拟 | 第一版用**显式覆盖优先**（若静态档案将来有 `importance` 就用它），否则公式：`0.4×在主线 threads + 0.3×有 faction + 0.3×有 secret` | N5 |

### 12.1 拍板记录（2026-10-18）

> 人于 2026-10-18 授权「按序进行」= **按 §12 的建议逐条采纳** P1–P10（Issue #340），作为 M11 的实现口径。
> §12 原表格**逐字保留**（含「为什么需要人」「建议」「阻塞哪片」三列）；本节只补「结论 + 对实现单的强制约束」。
> 以下约束对 N1–N8 中对应实现单**强制生效**；若与实现单正文冲突，**以本节为准**，并在该 PR 说明差异。

| # | 结论（采纳 §12 建议） | 对实现单的强制约束 |
|---|---|---|
| **P1** | NEURA **只出结构化计划与记忆摘要**，不出一句玩家可见文本；对白 / 旁白归 LOREX（`prism_guide`） | N4 的上下文契约按此写（`select_context` 只回答「这个 NPC 知道什么」）；任何实现单**不得**让 NEURA 直接生成叙事或玩家可见文本 |
| **P2** | **M11 第一版不做掷骰**：NPC 只做无判定计划（移动 / 工作 / 等待 / 报告 / 交易意向）；需要判定时降级为**提案被拒 + 记录未执行** | N6 必须有 `unsupported` 分支；**不得**扩 `prism_core.perform_action` 的契约，也不得为 NPC 新增掷骰入口（掷骰另开 M11-b 设计单） |
| **P3** | 记忆先落 **`blocks.neura`（不透明块）**，硬上限 `MEMORY_MAX=128`/NPC、`JOURNAL_MAX=256`/战役；若 200 NPC 实测块 > 512 KiB，再评估分片（另开单） | N1 的块结构与 `prune_block` 必须按这两个常量裁剪；N5 的裁剪必须**幂等**；本里程碑**不引入**独立记忆文件 |
| **P4** | 每游戏日成本上限 **60,000 tokens**，常量 `NPC_DAILY_TOKEN_BUDGET`，一处可调；上线前按实际单价复核 | N5 的预算判定**只读该常量**，不得散落魔数；调参不改调用点 |
| **P5** | 「重要远场」阈值 **`importance ≥ 0.7`**，写成常量，用 N8 长跑数据回调 | N5 的分层判定必须引用该常量；调阈值不得改动 `layer_of` 的判定结构 |
| **P6** | 接受 U4：**NEURA 权威、`guide.npc_at` 派生** | N7 必须在**同一提交内双写**（NEURA 侧与 `guide.npc_at`），保证不漂移；**不得只写一侧** |
| **P7** | NPC 计划**不并入** `chron.actions`；用 `plan.until_minute` + tick 补算 | N5 / N7 **零改动 `chron.py`**；NPC 计划不得写成 CHRON 行动 |
| **P8** | 第一版 `law_source = "none"`（空表、不拒）；`view.laws` 的 schema 留到 M12 | N2 **不得**因缺 laws 抛错或拒单；法律约束在 v1 等价于「不设限」，须在代码与测试里显式标注，避免被误读为「已实现」 |
| **P9** | 新增四个 `neura.*` 事件 `type` 由实现单在 PR 说明登记；`STATE-DESIGN.md` §5.1 枚举清单**另开文档 PR** 同步 | 实现 PR **不得夹带** `STATE-DESIGN.md` 改动；PR 说明必须列出四个 `type` 全名 |
| **P10** | `importance` 第一版**显式覆盖优先**（静态档案有就用），否则公式 `0.4×在主线 threads + 0.3×有 faction + 0.3×有 secret` | N5 必须实现该公式并把三个权重写成常量；「显式覆盖 > 公式」的优先级须有测试断言 |

**拍板后实现顺序不变**：N1 → N5（`neura.py` 串行）、N6 → N8（`notdnd_web.py` 串行），见 §11「附：切片依赖图」。

---

## 附录 A · 与其它设计单的接口一览（便于交叉审查）

| 对方 | 我给它的 | 它给我的 | 冲突风险 |
|---|---|---|---|
| `STATE-DESIGN.md` | 一个新块名 `neura`、四条事件 `type`、每 tick 每 NPC 一条事件 | `commit()` / `view()` / `replay()` / 惰性迁移口径 | 低（块是不透明的，state 不解析） |
| `CHRON-DESIGN.md` | **无**（不新增 API） | `world_minute` / `advance_to` 停点 / `next_barrier` / 分钟口径 | 低（纯只读）；P7 是唯一潜在扩张 |
| `ACTION-DESIGN.md` | 一个内部分支的消费者（`source="npc"`） | 提案 Schema / 结果契约 / 幂等键格式 / 排程钩子语义 | 中（`source` 参数是唯一改动，P2 决定是否要掷骰） |
| `MULTIPLAYER-DESIGN.md` | 每 NPC 事件的 `visibility` | 每成员视角过滤 / 受众冻结 | 低（复用既有门） |
| `GUIDE-DESIGN.md` | 「这个 NPC 现在知道什么」的只读视图 | 秘密门 / 知识门 / 传输层 | 中（P1 边界、P6 `npc_at` 归属） |
| `ATLAS-DESIGN.md` | 未探索层的「该生成了」信号 | `range_band` / `exits` / `find_place` | 低（只读） |

---

**（文末）§12 的 P1–P10 已于 2026-10-18 拍板（见 §12.1）；本文不含任何代码改动。**
