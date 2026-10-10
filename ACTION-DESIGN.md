# 行动管线 · 设计方案

> 状态：**待 Master Agent 审阅**。本文只定设计与拆单，不含实现。
> 读者：Master Agent（审阅 / 拆单）、Execution Agent（领单）。
> 依据：`GDD.html`（v1.1）§8 全文、§6.4 / §6.5、§9、§19、§20、§24；`GDD-BASELINE.md` §4.2 / §4.3 / §5（M8）/ §7.2。
> 对应 Issue：**#137（M8-a）**。可改路径仅本文件；不碰任何代码。
> **接口对齐（2026-10-10）**：状态层接口口径已按 `STATE-DESIGN.md`（#136）§12 定稿对齐
> （提交签名、幂等键名 `idempotency_key`、乐观并发序号 `base_seq`、读事件、`world_time`）；
> 排程钩子与 `CHRON-DESIGN.md`（#138）§4.3 已写清层数与映射（§8.2）。见 §2.2。
> 本文不是规则正文，不替代 `docs/`。它把 GDD 的「自由行动与解析管线」翻译成可拆实现单的接口与数据契约。

---

## 0. Master Agent 怎么用这份方案

1. 审第 2 节的**假设登记**（已按 `STATE-DESIGN.md` §12 定稿对齐，2026-10-10）。有不同意的，先改本文再拆单。
2. 审第 8 节的**排程钩子**——它是 M8 与 M9 的唯一接触面，M9 接入不得改本契约；有异议现在就改。
3. 第 13 节的切片已按仓库模板写好（目标 / 验收 / 可改 / 禁改 / 依赖）。可以照抄开单，也可以合并或推迟，但**不要把提案契约与写接口揉进同一条**。
4. 开单时打上每条写明的难度标签与能力标签。
5. Execution Agent **不合并**；审查用四行建议。
6. 本文未推送到远端前，按 `AGENTS.md` §4 不能当作交接。

---

## 1. 目标与非目标

### 1.1 目标

一条**与叙事解耦**的行动管线，让浏览器能完整走完一轮即时行动：

```
自然语言或显式行动
      │
      ▼
行动提案（结构化）──► 验证（PRISM 规则 + ATLAS 只读 + 权限）
      │                        │
      │（关键歧义：只问一个问题）│ 不成立 → 固定错误
      ▼                        ▼
   一次性澄清              同步结算（PRISM）
                               │
                               ▼
                        提交（状态层唯一出口）
                               │
                               ▼
                        叙事（可选，AI，失败则模板化兜底）
```

做完后，使用者能感知的变化要到第 13 节的 A3 才出现在网页会话里（即时行动走完一轮）。A1–A2 是库与契约，用测试证明。

管线在 M8 范围内**不接 CHRON**：长行动与中断属 M9。但本文必须把**排程钩子**（`time_cost` 语义与接口签名）定死，使 M9 不必回头改契约就能接入（GDD-BASELINE §5「M8 ↔ M9 的顺序说明」）。

### 1.2 不做（非目标）

- **不接 CHRON**：不建世界时钟、不做队列、不做时间压缩；`scheduled / blocked / interrupted` 语义由 M9 落（GDD §6.4 的异步路径）。
- **不新增图片、不做分镜**：纯文本（GDD-BASELINE D2）。
- **不让 AI 判定或改事实**：骰点只来自 PRISM；AI 只把 NL 映射成提案、只写叙事（GDD §9.1 / §20.1）。
- **不定义权威状态层的存储**：`state.py` 由 M7-a（`STATE-DESIGN.md`）定；本文只**消费**它的提交接口（见第 2.2 节假设）。
- **不重写 `perform_action`**：同步结算复用 `prism_core.perform_action`，管线只在其上包一层提案 / 结果 / 提交。
- **不改 `docs/` 规则**：行动结果与风险分级的数据向 GDD §8 对齐，不改既有规则正文。
- **不做多人并发**：房间、权限、认知分离属 M10；本文只留出「谁在行动」的字段位（`actor`）与幂等键。
- **不引入运行时依赖**：本单沿用现有实现（Python 3 标准库），**这是现状、不是硬约束**——`AGENTS.md` 铁律 2 已改名「技术选型效率优先」、**不设依赖上限**（GDD-BASELINE D3 已生效）；如确需引入运行时依赖，按铁律 2 在 PR 说明理由、权衡与替代方案。

---

## 2. 已拍板与假设

### 2.1 已拍板（承 GDD-BASELINE，不再重开）

| 编号 | 决定 | 来源 |
|---|---|---|
| P1 | 行动生命周期**一次定齐、分两阶段实现**：M8 落 `proposed → running → completed/failed`，`scheduled / blocked / interrupted / cancelled` 由 M9 接入 | §4.3 第 2 条 |
| P2 | 行动结果契约以 GDD §8.4 的字段为底，**带 Schema 版本与迁移钩子** | §4.3 第 3 条 |
| P3 | 唯一提交出口是状态层；PRISM 出「结果」，导引者 / NEURA 出「提案」，都不直接落库 | §4.2 |
| P4 | 降级：叙事模型不可用时，规则与调度照常，网页显示模板化文字 | §4.2 / GDD §20.4 |
| P5 | M8 不接 CHRON，但须预留排程钩子；M9 复用 M8 契约 | §5 |

### 2.2 假设登记（已对齐 `STATE-DESIGN.md` §12，2026-10-10）

Issue #137 起草时 `STATE-DESIGN.md`（M7-a）尚未合并，曾按 GDD §8.4 作最小假设。**现已按 `STATE-DESIGN.md`（#136）§12 定稿对齐**：提交签名、幂等键名（`idempotency_key`）、乐观并发序号（`base_seq`）、读事件与 `world_time` 口径一律以 STATE 为准（下表各条注明原始假设名）。四份设计单交叉引用一致后，**M9 接入不再需要改本契约**。

| 编号 | 假设（对齐后） | 依据 / 处置 |
|---|---|---|
| A1 | 状态层提供**唯一提交接口**，原子落库并返回提交结果。签名以 `STATE-DESIGN.md` §12.1 为准：`Handle.commit(*, base_seq, idempotency_key, type, actor, target=None, cause=None, visibility=None, changes=(), rules_version, roll_refs=(), world_time=None, source="player", revalidate=None) -> CommitResult{status, commit_id, seq_from, seq_to, event_ids, state_digest, error}`。 | GDD §19.2 / §19.3；§4.2「唯一提交」；`STATE-DESIGN.md` §12.1（2026-10-10 对齐） |
| A2 | 状态层有**乐观并发序号**：提交携带读取状态时的 `base_seq`，不匹配则返回 `status="conflict"`（网页层翻 409）。原名 `base_version`，**已按 STATE 统一为 `base_seq`**。 | GDD §19.3；`STATE-DESIGN.md` §8.2 / §12.1（2026-10-10 对齐） |
| A3 | 状态层提供**幂等键**：同一 `idempotency_key` 重复提交返回首次结果，不产生新副作用。 | GDD §19.3 / §24.1；`STATE-DESIGN.md` §7 / §12.1 |
| A4 | 事件封套字段以 GDD §19.5 为准（事件 ID、战役 ID、世界时间、序列号、类型、主体、目标、原因、可见范围、状态变更、规则版本、随机判定引用、提交状态）；字段级实现以 `STATE-DESIGN.md` §5.1 为准。 | §4.3 第 1 条；`STATE-DESIGN.md` §5.1 |
| A5 | **已对齐**：`STATE-DESIGN.md` §12.1 已定稿为「提交接口 + 乐观 `base_seq`」形态，A5 的适配预案无需启用；保留此行仅记录耦合边界——无论状态层形态如何，提案 / 结果 Schema 与排程钩子不变。 | 降低耦合；`STATE-DESIGN.md` §12.1（2026-10-10 对齐） |

> 过渡策略：`state.py` 落地前，同步路径可先复用现有 `Session.rules` 快照 + `Session.save()` 作为临时提交出口（见第 10.2 节），但**必须在 PR 里写明这是过渡态**，且不新增第二套字段。

---

## 3. 架构与落点

### 3.1 模块落点（承 GDD-BASELINE §4.1）

| 职责 | 落点 | 动作 |
|---|---|---|
| 提案与结果契约、同步结算 | `prism_core.py` | 扩展：把 `perform_action` 的结果包装成 §8.4 契约（**不重写结算**） |
| NL → 行动提案、叙事 | `prism_guide.py` | 扩展：新增 NL→提案解析（可选模块，import 失败不影响离线） |
| 管线编排、写接口、幂等键缓存 | `notdnd_web.py` | 扩展：`/api/action/*` 路由与编排（接口由本文定） |
| 权威状态与事件（唯一提交出口） | `state.py`（新） | **M7-a 已定稿**（`STATE-DESIGN.md` §12）；本文只消费其提交接口，签名以 §12.1 为准（见 §2.2 A1） |
| 时间与调度 | `chron.py`（新） | **M9**；本文只定钩子签名（第 8 节） |
| 空间只读查询 | `atlas*.py` | 保持；验证阶段只读取可达性 / 出口，不写 |

### 3.2 依赖方向（硬，承 §4.2）

- **单向**：管线编排（`notdnd_web.py`）→ {`prism_core` / `prism_guide` / `atlas*`} → `state`。谁都不许反向 import。
- **唯一提交**：只有状态层能改世界事实。PRISM 出结果、导引者出提案 / 叙事、CHRON 出安排，都不直接落库。
- **AI 边界**：导引者的文本不是事实；骰点只来自 PRISM；叙事不得发明结果（GDD §9.1 / §20.1）。
- **可选模块**：`prism_guide` 缺失时，管线仍能**显式行动**结算（离线底线，P4）。

### 3.3 管线六段（GDD §8.2 的工程化）

| 段 | 输入 | 输出 | 依赖 AI？ |
|---|---|---|---|
| 1 解析 | NL 文本 / 显式 `action_id` | 候选提案或一次性澄清 | NL 路径需要；显式路径不需要 |
| 2 检索 | 提案 | 上下文切片（能力 / 物品 / 地点 / 可见状态 / 规则） | 否（数据查询） |
| 3 验证 | 提案 + 上下文 | 通过 / 不成立（固定错误）/ 需澄清 | 否（PRISM + ATLAS 只读） |
| 4 结算 | 通过后的提案 | 行动结果（§8.4） | **否**（PRISM 纯规则） |
| 5 提交 | 结果 + 幂等键 | 事件 ID / 新版本 | 否（状态层） |
| 6 叙事 | 结果 + 提交后快照 | 叙事文本（可选） | 是（失败则模板化兜底） |

**第 4 段与第 6 段之间是硬边界**：结果在第 6 段开始前就已定稿并提交。叙事只能描述已成立的结果（第 7 节）。

---

## 4. 行动提案契约（GDD §8.2 / §8.4 前置）

### 4.1 生命周期状态（GDD §6.4）

GDD §6.4 的八个状态，M8 只实现其中四个（P1）：

| 状态 | M8 | 语义 | 转换条件 |
|---|---|---|---|
| `proposed` | ✅ | 已构造提案，尚未验证 | 解析产出 |
| `scheduled` | ⤴ M9 | 已排入时间线 | 由排程钩子接管（第 8 节） |
| `running` | ✅ | 验证通过、正在同步结算 | `proposed` 验证通过 |
| `blocked` | ⤴ M9 | 条件阻塞（依赖未满足 / 前置未就绪） | 验证发现外加约束 |
| `interrupted` | ⤴ M9 | 被外部事件打断 | 中断条件命中（第 8.4 节） |
| `completed` | ✅ | 结算成功并已提交 | `running` 结算成功 |
| `cancelled` | ⤴ M9 | 玩家主动取消（长行动） | M8 的同步行动不可取消 |
| `failed` | ✅ | 结算失败并已提交 | `running` 结算失败 |

**每次状态转换都产生一条可追溯事件**（GDD §6.4）。M8 只产出 `proposed → running → completed/failed` 三段；M9 在钩子后追加异步段。

### 4.2 提案 Schema（字段级）

提案是**尚不代表已执行**的结构化描述（GDD 附录 A「Action Proposal」）。字段名可在实现阶段微调，但**语义与必填性**由本文锁定：

```json
{
  "schema_version": 1,
  "proposal_id": "p_<32hex>",
  "actor": {"kind": "player", "id": "unit_01"},
  "intent": {
    "verb": "persuade",
    "target": {"kind": "npc", "id": "guard_gate"},
    "desired": "让守卫放行",
    "means": "口头交涉"
  },
  "params": {"dc_override": null, "items_used": []},
  "source": "nl",
  "risk": "R2",
  "time_cost": {"value": 5, "unit": "minute"},
  "schedule_hint": {"can_defer": false, "depends_on": [], "priority": 0},
  "interruptible": true,
  "interrupt_conditions": [],
  "expected_changes": [],
  "raw_text": "我想说服守卫让我进去",
  "created_at": 1710000000
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | int | 是 | 当前 `1`。迁移见 4.3。 |
| `proposal_id` | str | 是 | 服务端生成，`^p_[0-9a-f]{32}$`；幂等与澄清都挂在它上面。 |
| `actor` | obj | 是 | 谁在行动。M8 单角色，`id` 取 `rules.active_unit_id`（空则 `party[0]`，同 `prism_guide.settle` 口径）。M10 扩成房间成员。 |
| `intent.verb` | str | 是 | 动作类，取自世界规则包的动作表（§9.5），不是任意串。 |
| `intent.target` | obj\|null | 视动作 | 目标实体；须能过状态层的实体引用校验（GDD §20.3）。 |
| `intent.desired` | str | 是 | 期望结果（人话）。仅叙事用，**不参与判定**。 |
| `intent.means` | str | 否 | 所用手段（人话）。 |
| `params` | obj | 否 | 结构参数（覆盖 DC、消耗的物品等）。`dc_override` 仅在世界规则允许时生效。 |
| `source` | enum | 是 | `"nl"` 或 `"action"`。`"action"` = 客户端直接给 `action_id`，**无需 AI**。 |
| `risk` | enum | 是 | `R0`–`R4`（第 6 节）。 |
| `time_cost` | obj | 是 | `{"value": int≥0, "unit": ...}`。语义见 8.1。M8 只记录，不排程。 |
| `schedule_hint` | obj | 否 | 排程提示（M9 消费）：`{"can_defer": bool, "depends_on": [action_id], "priority": int}`。字段名与 `CHRON-DESIGN.md` §4.3 一致；默认 `{"can_defer": false, "depends_on": [], "priority": 0}`。`can_defer=false` 走 M8 同步结算，`can_defer=true` 才进 CHRON 队列。 |
| `interruptible` | bool | 是 | 是否可被打断。M9 消费；M8 只落契约。 |
| `interrupt_conditions` | array | 否 | 中断条件（8.4 定义）。 |
| `expected_changes` | array | 否 | 预期状态变更草案（供验证与叙事引用，不是事实）。 |
| `raw_text` | str | 否 | NL 原文，仅日志与叙事用，**不进规则判定**。 |
| `created_at` | int | 是 | 服务器时间戳（毫秒）。 |

**不做**：提案里不放骰点、不放判定结果、不放最终状态变更——那些属结果契约（第 5 节）。提案是「意图」，结果才是「事实」。

### 4.3 版本与迁移

- 提案与结果**各自独立**带 `schema_version`（当前都从 `1` 起）。
- 迁移**惰性**：与 `notdnd_web.Session.load` 同一口径——**只补不存在的键、不动已有的合法值**，不为迁移在磁盘上跑脚本。
- 迁移入口（实现期）：
  - `prism_core.migrate_proposal(raw) -> dict`
  - `prism_core.migrate_result(raw) -> dict`
  - 未知的更高版本：**拒绝并给固定错误**（不猜），由状态层返回 `409 版本不支持`。
- **往返测试**：迁移后的对象再序列化，字段集与目标版本一致；旧档（`schema_version` 缺失）能补默认值并继续。

---

## 5. 行动结果契约（GDD §8.4）

### 5.1 字段级 Schema

以 GDD §8.4 的概念示例为底，补版本、状态枚举与来源引用：

```json
{
  "schema_version": 1,
  "action_id": "act_p_<32hex>",
  "proposal_id": "p_<32hex>",
  "actor": {"kind": "player", "id": "unit_01"},
  "status": "success",
  "costs": [
    {"kind": "time", "value": 5, "unit": "minute"},
    {"kind": "resource", "pool": "supply", "value": 1}
  ],
  "effects": [
    {"kind": "condition", "target": "unit_01", "add": "fatigued"}
  ],
  "consequences": [
    {"kind": "relation", "target": "guard_gate", "delta": -1}
  ],
  "discovered_facts": [
    {"fact_id": "f_gate_shift", "scope": "personal"}
  ],
  "event_ids": ["evt_000123"],
  "time_cost": {"value": 5, "unit": "minute"},
  "interrupt_conditions": [],
  "rule_ref": {"version": "prism-core@0.1", "df": 12, "roll_ref": "rand_0007"},
  "committed": true
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema_version` | int | 是 | 当前 `1`。 |
| `action_id` | str | 是 | 结果自身的唯一 ID（幂等与事件引用）。 |
| `proposal_id` | str | 是 | 指回提案。 |
| `actor` | obj | 是 | 行动者。 |
| `status` | enum | 是 | `success` / `partial` / `failure` / `blocked`（GDD §8.4）。映射见 5.2。 |
| `costs` | array | 是 | 已发生代价（时间 / 资源 / 状态）。**已结算的代价不得靠叙事改写**。 |
| `effects` | array | 是 | 状态变更（已通过提交的）。 |
| `consequences` | array | 是 | 后果（关系、声誉、后续机会）。可空数组。 |
| `discovered_facts` | array | 是 | 本行动新发现的事实 / 线索，带可见范围（GDD §12.4）。**只记「发现了什么」，不在此处展开内容**。 |
| `event_ids` | array | 是 | 状态层提交后返回的事件 ID（假设 A1）。M8 过渡态可为空数组。 |
| `time_cost` | obj | 是 | 实际耗时，与提案的 `time_cost` 口径一致（8.1）。 |
| `interrupt_conditions` | array | 否 | 本行动在 M9 下需要监听的中断条件。 |
| `rule_ref` | obj | 是 | 规则版本 + DF + 随机判定引用（GDD §9.2 / §19.5），供复盘。 |
| `committed` | bool | 是 | 是否已提交状态层。**未提交的结果不得进入叙事 as 事实**。 |

> `costs` / `effects` / `consequences` / `discovered_facts` 的元素是**结构化条目**（带 `kind`），不是自由文本。自由文本只在叙事段产生。

### 5.2 状态映射（`perform_action` → §8.4）

现有 `prism_core.perform_action` 的返回是规则层结果，需映射成契约的 `status`：

| `perform_action` 返回 | §8.4 `status` | 说明 |
|---|---|---|
| `resolved` + `passed` 且非 narrow | `success` | 标准成功 |
| `resolved` + 险成（`narrow`，按 02A 第三节） | `partial` | 达成 + 一个代价 |
| `resolved` + 失败（未超重试上限） | `failure` | 失败代价已结算 |
| `exhausted` | `failure` | 机会耗尽，代价必须真结算（现有 `perform_action` 已做） |
| `already_done` | `success` | 幂等回放：不重复掷骰 / 不重复发奖（现有短路） |
| 战斗锁（抛 `ValueError`） | `blocked` | 网页层翻成 409（第 9.5 节） |
| 未知行动（抛 `ValueError`） | — | 404，不产出结果 |

**险成（narrow）的代价内容由导引者裁定**（02A 第三节，`resolve_check` 的 TODO(M3) 钩子）；M8 若未接线，`partial` 的 `consequences` 允许为空数组，并在 `rule_ref` 注明 `pending_judgement: true`。

### 5.3 版本与迁移

同 4.3：惰性、只补不覆盖、未知更高版本拒绝。结果契约是**跨 M8–M13 的稳定面**，改动走独立 PR 并升 `schema_version`。

---

## 6. 风险分级与一次性澄清（GDD §8.3）

### 6.1 五级风险

Issue #137 要求**五级**风险分级。GDD §8.3 给的是四行示例表（低 / 中 / 高 / 歧义），其中「歧义或权限不足」本质是**另一条轴**（处置方式），不是风险强度。本设计把风险轴**细化为五级**，并把「歧义 / 权限」作为正交的**处置轴**：

| 级别 | 含义 | 示例 | 交互策略 |
|---|---|---|---|
| **R0** | 只读、无副作用 | 查看背包、询问营业时间、查看出口 | **直接执行**，不确认、不落世界事件 |
| **R1** | 低风险、可逆 | 整理物品、短距移动、一般社交 | **直接执行**，事后可回看 |
| **R2** | 消耗资源 / 有成本 | 购买物品、开始训练、旅行、投入材料 | **展示成本与耗时**；依用户设置决定是否确认 |
| **R3** | 高风险、不可逆（个人尺度） | 永久处决、销毁重要建筑、重大财产转移 | **明确告知后果并要求确认**，除非已事先授权该类自动行为 |
| **R4** | 灾难 / 世界级不可逆 | 发动战争、改变世界事实、影响其他玩家 | **强制确认**；多人场景下需相关玩家 / 房主确认（M10 落权限） |

正交处置轴（对应 GDD §8.3 第 4 行）：

| 处置 | 触发 | 交互 |
|---|---|---|
| **澄清** | 目标不明确 / 无法判断关键歧义 | 见 6.3，一次只问最重要的一个 |
| **拒绝** | 无权操作 / 规则不允许 | 固定错误（第 9.5 节），不产出提案 |

### 6.2 与 GDD §8.3 四行的映射（给审查对齐用）

| GDD §8.3 行 | 本设计的级别 |
|---|---|
| 低风险、可逆 | R0 + R1（拆成两级：无副作用 / 有副作用但可逆） |
| 中风险或消耗资源 | R2 |
| 高风险、不可逆 | R3（个人）+ R4（世界级） |
| 歧义或权限不足 | 处置轴（澄清 / 拒绝），**不是风险级** |

### 6.3 一次性澄清流程（GDD §8.2 第 3 步）

- 解析 / 验证阶段若发现**影响结果的关键歧义**，**只提出最重要的一个问题**，不连环追问。
- 澄清以**提案的替代产物**返回，不是错误：

```json
{
  "type": "clarify",
  "clarify_id": "c_<32hex>",
  "question": "你要说服的是城门守卫，还是巡逻队长？",
  "options": [{"id": "guard_gate", "label": "城门守卫"},
              {"id": "captain_patrol", "label": "巡逻队长"}],
  "proposal_draft": { "...": "已确定的部分，待补 target" }
}
```

- 玩家回答后，**带着 `clarify_id` 重新解析**，服务端把答案并入 `proposal_draft` 产出正式提案。澄清**不消耗资源、不落世界事件、不掷骰**。
- 一个澄清回合只允许一次澄清；若回答后仍有歧义，**降级为拒绝**（固定错误），不再二次追问——避免澄清循环。
- 澄清**不能**用于规避 R3 / R4 的确认：高风险确认（6.4）与澄清是两件事，前者必答，后者只在歧义时出现。

### 6.4 确认策略与用户设置

- R0 / R1：无确认。
- R2 / R3：默认返回 `需要确认`，携带 `risk`、`costs_preview`、`time_cost`；客户端确认后再 `commit`。
- R4：同 R2 / R3，另需相关方确认（M10 之前，单机下退化为一次显式确认）。
- **用户预先授权**（GDD §8.3「除非用户已事先授权该类自动行为」）：以**离线可读的设置**记录（如 `guide.settings.auto_confirm_up_to = "R2"`），默认最保守（`R2` 以上必确认）。设置不进 `docs/`，归会话块。

---

## 7. 结算与叙事解耦（GDD §8.2 / §20.4）

### 7.1 结算路径不依赖 AI

- 第 3–5 段（验证 → 结算 → 提交）**只走 PRISM + ATLAS 只读 + 状态层**，一行模型都不调。
- **显式行动**（`source: "action"`）与 **NL 行动**（`source: "nl"`）的差别只在第 1 段：显式行动无需 `prism_guide`，因此在导引者模块缺失时仍能完整结算（离线底线，P4）。
- 无 AI 配置时，`POST /api/action/commit` 的 `result` 事件照常产出。

### 7.2 叙事是可选后置段

- 第 6 段才需要 `prism_guide`。叙事消息数组的 L4 用**提交之后**的快照重拼（同 `_guide_turn` 的口径）。
- 叙事只能描述 `status` 为 `success` / `partial` / `failure` 的**已提交结果**；不得新增结果、不得改写 `costs` / `effects`（GDD §20.1）。
- `blocked` 结果的叙事是**说明限制**，不是「失败」。

### 7.3 降级（GDD §20.4）

| 情形 | 行为 |
|---|---|
| `prism_guide` 缺失 | `result` 照常返回；叙事段返回 `status: "offline"` + 模板化兜底句（复用现有 `FALLBACK_NARRATION` 口径） |
| 叙事调用失败 / 超时 | 同一条流上发 `fallback` 事件，不产出第二行 HTTP 状态（复用现有 SSE 口径） |
| 叙事输出不合格（Schema / 实体 / 权限） | 有限重试后退回确定性提示（GDD §20.3），不无限循环 |

---

## 8. 排程钩子（M8 ↔ M9 边界，GDD §6.5）

这是本文**最不能含糊**的一节：M9 接入时不得回头改 M8 的契约（GDD-BASELINE §5）。

### 8.1 `time_cost` 语义

- `time_cost = {"value": int ≥ 0, "unit": enum}`；`unit ∈ {round, turn, minute, hour, watch, day}`。
  - `round` / `turn` 是**规则时间**（战斗轮、场景回合），由 PRISM 定义、CHRON 映射到世界时间（GDD §6.2）。
  - `minute` / `hour` / `watch` / `day` 是**世界时间**量。
  - ⚠️ 历史口径不一致（`docs/system/01-core.md`「时段 = 10 分钟」vs `docs/system/03-interaction-layer.md` 的「时段（Watch）」= 四分之一天 vs `notdnd_web.py` 的 `BAND_HOURS`）。**换算表已由 M9-a（`CHRON-DESIGN.md` §2.3 / §2.4）钉死**：权威刻度 = 整数分钟，`NOMINAL_WATCH_MINUTES = 360`、`MINOR_ACTION_MINUTES = 10`；M9 的钉法不影响本节签名——本契约只要求 `unit` 是枚举成员，换算在 CHRON 内部。
- **世界时间表示（唯一写法）**：钩子与调度层内部一律用 CHRON 的**整数分钟** `world_minute`（`CHRON-DESIGN.md` §3.1）表达世界时间；事件封套里的 `world_time` 是**不透明对象**，其形状由 CHRON 定义、**提交时传入**（`CHRON-DESIGN.md` §3.1 / `STATE-DESIGN.md` §5.1 / §12.3），状态层不改写。本节钩子返回的 `start_at` / `expected_end_at` 即这两个整数分钟。
- **M8 语义**：同步行动的世界时间**不推进**（管线只记录 `time_cost` 作为结果字段）。需要「世界时钟前进」的效果一律等 M9。
- **M9 语义**：钩子按 `time_cost` 计算 `start_at` / `expected_end_at`，把行动放进队列；屏障命中时暂停（GDD §6.6）。

### 8.2 钩子协议签名（两层：管线钩子 ↔ 调度器入口）

**先写清层数**（避免 M9 接入时两套签名打架）：本钩子与 `CHRON-DESIGN.md` §4.3 的入口是**两层**，职责不同、**不是同一层**：

- **L1 · 管线钩子（`ScheduleHook`，本文定义）**：结算管线在**状态层提交之前**调用的**鸭子类型对象**（风格同 `prism_guide.settle` 只按 `lock` / `rules` / `save` 用鸭子类型）；管线**不 import `chron`**。M8 用空实现，M9 用 `chron.py` 的**适配器**实现。
- **L2 · 调度器公开入口（`CHRON-DESIGN.md` §4.3，`chron.py`）**：`schedule / advance_to / next_barrier / cancel`，由游戏循环 / 网页层 / M10 驱动，**不由结算管线直接调用**。

**L1 签名（本文唯一口径，`base_seq` 已对齐 `STATE-DESIGN.md` §12.1）**：

```python
# 管线只认这个协议；具体实现在 chron.py（M9 的适配器）或空实现（M8）
class ScheduleHook(Protocol):
    def enqueue(self, proposal: dict, draft: dict, *,
                base_seq: int) -> dict:
        """把一次已验证的行动交给时间线。

        proposal: 第 4 节提案（已过验证，含 time_cost / schedule_hint）
        draft:    第 4 段结算出的结果草案（尚未提交）
        base_seq: 状态层乐观并发序号（假设 A2；= STATE-DESIGN.md §12.1 的 base_seq）

        返回（键固定，M9 只在值上做文章）：
        {
          "mode": "sync" | "async",
          "task_id": str | None,          # async 时非空（= CHRON 的 action_id）
          "start_at": int,                # 世界分钟 world_minute（M8 = 当前世界时间，默认 0）
          "expected_end_at": int,         # 世界分钟；M8 = start_at
          "barrier": bool,                # 是否命中事件屏障（CHRON §6.2 B1–B8；M8 恒 False）
          "resume_token": str | None      # async 恢复用；M8 恒 None，M9 目前也不用（中断重排见 CHRON §4.4）
        }
        """
        ...

    def cancel(self, task_id: str) -> bool:
        """取消一个已排程行动。M8 恒返回 False（同步行动不可取消）。"""
        ...
```

**L1 ↔ L2 映射（M9 的适配器按此实现，不改本契约）**：

| L1（`ScheduleHook`，本文） | L2（`chron.py`，`CHRON-DESIGN.md` §4.3） | 映射规则 |
|---|---|---|
| `enqueue(proposal, draft, *, base_seq)` | `schedule(action) -> action_id` | 适配器用 `proposal` + `draft` 构造 CHRON 的 `Action`（`time_cost` 折成整数分钟，`schedule_hint.priority` / `depends_on` 透传），调 `schedule`；返回 `mode="async"`、`task_id=action_id`、`start_at=Action.start_minute`、`expected_end_at=Action.due_minute`、`barrier=False` |
| `enqueue(...)`（`schedule_hint.can_defer == False`） | —（不进队列） | 返回 `mode="sync"`；**不**调 `schedule`，保持 M8 同步结算不变（CHRON §4.3 关键约定） |
| `cancel(task_id)` | `cancel(action_id, at_minute) -> event` | `task_id` 即 `action_id`；返回是否成功取消 |
| —（L1 不暴露） | `advance_to(world_minute)` / `next_barrier(from_minute)` | 由游戏循环 / 网页层 / M10 驱动，**不挂在 `ScheduleHook` 上** |

**字段名对齐（两文档一致）**：`time_cost = {"value", "unit"}`（提案字段；CHRON 读其中的分钟数）、`schedule_hint.can_defer` / `depends_on` / `priority`（提案字段，`CHRON-DESIGN.md` §4.3）、`barrier`（L1 返回 / CHRON §6.2 屏障）、`resume_token`（L1 预留）。**M9 接入只实现适配器，不改提案 / 结果 Schema、不改写接口路径。**

### 8.3 M8 的空实现与 M9 的接入

- **M8 空实现**（`notdnd_web.py` 内的常量 / 小类，不新建 `chron.py`）：

```python
class _SyncOnlySchedule:
    def enqueue(self, proposal, draft, *, base_seq):
        now = 0    # M8 无世界时钟：世界分钟取纪元 0 占位（CHRON §3.1）；M9 由 CHRON 提供
        return {"mode": "sync", "task_id": None, "start_at": now,
                "expected_end_at": now, "barrier": False,
                "resume_token": None}
    def cancel(self, task_id):
        return False
```

- **M9 接入**：`chron.py` 实现 **L2** 公开入口（§8.2），并提供一个**适配器**满足 **L1** 协议；管线在启动时把适配器注入（或在 `notdnd_web` 里按「`chron` 可导入则用、否则回退 `_SyncOnlySchedule`」选择，与 `prism_guide` 的可选导入同风格）。**M9 不改本契约、不改提案 / 结果 Schema、不改写接口路径**。
- **两条里程碑无循环依赖**：M8 不等待 M9；M9 复用 M8 的契约（GDD-BASELINE §5）。

### 8.4 中断语义（GDD §8.5）预留

GDD §8.5：行动可能因敌人出现、天气变化、目标离开、材料不足、玩家取消、更高优先级事件而中断；中断必须有明确规则：已消耗的时间与资源是否返还、进度是否保留、角色是否承担风险、后续如何恢复。

- **M8 只落契约，不实现中断**（同步行动不被打断）。契约位：
  - 提案的 `interruptible: bool` 与 `interrupt_conditions: [{kind, expr}]`。
  - 结果的 `interrupt_conditions` 回显。
- **条件项 `kind ∈ {enemy_appears, weather_change, target_leaves, resource_short, hazard, player_cancel, higher_priority}`**（对齐 GDD §8.5 列举）。
- **返还策略**是**提案字段**：`params.on_interrupt ∈ {"refund_all", "refund_none", "refund_partial", "keep_progress"}`，默认 `"keep_progress"`（GDD §6.5「长行动默认不锁玩家」的保守取向）。**M9 消费此字段**；M8 只透传、不解释。
- 中断的**结算**走 PRISM（不新增判定路径）、**提交**走状态层——与普通行动同一条管线，只是由 M9 触发。

---

## 9. 写接口契约（GDD §8 / §19.3）

### 9.1 路径总表（新增，风格同 `GUIDE-DESIGN.md` §5.10）

| 方法与路径 | 作用 | 会话 |
|---|---|---|
| `GET  /api/action/catalog` | 当前可用行动单（`source: "action"` 用）：动作 id / 标签 / 风险级 | `X-Session` |
| `POST /api/action/propose` | NL 或显式动作 → 提案 **或** 一次性澄清 | `X-Session` |
| `POST /api/action/confirm` | 对 R2–R4 提案回「确认」或「取消」 | `X-Session` |
| `POST /api/action/commit` | 提案 + 幂等键 → 同步结算 → 提交 →（可选）叙事 | `X-Session` |

**不加 WebSocket**（同现有口径）。`commit` 的叙事段复用现有 SSE 事件名（`result` / `narration` / `usage` / `fallback` / `done`）。

> 既有路由**行为不变**。`POST /api/guide/turn` 保留其「先结算、后叙事」的旧路径；新前端走 `/api/action/*`。两者最终**共用同一套提案 / 结果契约与提交出口**（`turn` 的 `settle` 是 `commit` 的一个特例）。

### 9.2 `POST /api/action/propose`

请求体（`nl` 模式）：

```json
{
  "mode": "nl",
  "text": "我想说服守卫让我进去",
  "location_id": "gate_north",
  "clarify_id": null
}
```

请求体（`action` 模式，**无需 AI**）：

```json
{ "mode": "action", "action_id": "persuade_guard", "params": {} }
```

响应（提案）：

```json
{
  "status": "ok",
  "type": "proposal",
  "proposal": { "...": "第 4.2 节，含 proposal_id" },
  "needs_confirm": true,
  "risk": "R2",
  "costs_preview": [{"kind": "time", "value": 5, "unit": "minute"}]
}
```

响应（澄清）：

```json
{ "status": "ok", "type": "clarify", "clarify": { "...": "第 6.3 节" } }
```

- `mode: "nl"` 且 `prism_guide` 缺失 → `{"status": "offline", "type": "offline"}`（200），提示改用 `mode: "action"`；**不结算、不报 5xx**。
- 提案是**无状态透明的**：服务端返回提案内容（含 `proposal_id`），客户端 `commit` 时把提案**原样带回**；服务端**不做提案缓存**（避免第二套持久化）。若需要防篡改，由客户端签名的 `proposal_id` + 服务端重验保证——**服务端永远重验，不信任回带的提案**。

### 9.3 `POST /api/action/commit`

请求头：`X-Session`、`Idempotency-Key`（见 9.4）。

请求体：

```json
{
  "proposal": { "...": "propose 原样带回的提案" },
  "confirm_token": "cf_<32hex>",     // R2–R4 必填；R0/R1 省略
  "base_seq": 7                      // 假设 A2；= STATE-DESIGN.md §12.1 的 base_seq；旧值触发 409
}
```

响应（非流式结算，`Accept: application/json`）：第 5 节的行动结果。

响应（流式，默认）：SSE，事件顺序：

```
event: result     data: <行动结果（§8.4）>
event: narration  data: {"text": ..., "beats": [...]}
event: usage      data: {...}
event: done       data: {"status": "ok"}
```

- **`result` 一定在 `narration` 之前**（第 3.3 节的硬边界）。`result` 可在模型返回前发出。
- 叙事失败 → `fallback` 事件；`result` 已发、已提交，不受影响。

### 9.4 幂等键

- `Idempotency-Key` 请求头，格式 `^[A-Za-z0-9_-]{8,64}$`；缺失 / 非法 → `400 幂等键不合法`。
- 语义（GDD §19.3）：**同一会话 + 同一键**的重复 `commit`，返回**首次结果**，不重复掷骰、不重复扣费 / 发奖 / 造成伤害。
- 存储：进程内 `{key: {result, ts}}` + 落盘（随会话存档一起，键不过期或设长 TTL）。**这是 M8 的临时幂等层**；`STATE-DESIGN.md` 落地后，幂等下沉到状态层（假设 A3），网页层只做透传。
- **提案级幂等**：`proposal_id` 在会话内唯一；同一 `proposal_id` 重复 `commit` 而未带新键 → `409 该提案已提交`（防误连点）。
- 与现有 `Session.done_actions` 的关系：后者是**规则级**「同一场景行动不重复发奖」（`perform_action` 已做）；幂等键是**传输级**「同一请求不重复副作用」。两者互补，都保留。

### 9.5 错误码总表

固定短语 + HTTP 码，风格同现有 `GUIDE_SETTLE_ERRORS`。**状态行（首次响应）写出前**才用 HTTP 码；流式开始后一律走 SSE 事件，不再发第二行状态。

| 情况 | HTTP | 固定短语 |
|---|---|---|
| 会话不存在 / 过期 | 400 | `会话不存在或已过期` |
| 请求体畸形 / 超限 | 400 | `请求体过大` / `JSON 解析失败` |
| `Idempotency-Key` 缺失 / 非法 | 400 | `幂等键不合法` |
| `mode: "nl"` 但导引者模块缺失 | 200 | （非错误，`type: "offline"`） |
| 行动不存在于当前场景 | 404 | `没有这个行动` |
| 提案 / 澄清不存在或过期 | 404 | `没有这个提案` |
| 实体引用无效（目标 NPC / 物品不存在） | 404 | `目标不存在` |
| 关键歧义 → 澄清（非错误） | 200 | （`type: "clarify"`） |
| 权限不足 / 规则不允许 | 403 | `做不了这件事` |
| 前置条件不满足（材料不足、位置不对） | 409 | `现在做不了` |
| 战斗未结束 | 409 | `战斗还没结束` |
| 状态序号冲突（`base_seq` 过期） | 409 | `状态已变，请重试` |
| 同一 `proposal_id` 重复提交（无新幂等键） | 409 | `该提案已提交` |
| R2–R4 缺 `confirm_token` / 确认已失效 | 409 | `需要确认` |
| 结果 Schema 版本不支持 | 409 | `版本不支持` |
| 速率限制 | 429 | `太频繁` |
| 服务器内部错误 | 500 | `服务器内部错误：…` |

速率：与现有 `turn` 口径对齐（每会话每 60 秒 12 次 `commit`、30 次 `propose`），常量集中定义，便于治理 PR 统一调整。

### 9.6 与 `prism_guide` 的边界

- `prism_guide` 只负责两件事：**NL → 提案**（第 1 段）与**叙事**（第 6 段）。**它不结算、不提交**（与 `settle` 的现有职责划清：`settle` 保留给旧 `turn` 路径，新路径的结算在 `prism_core`）。
- 导引者的 `ValueError` 在状态行前翻成固定 JSON，与现有 `GUIDE_SETTLE_ERRORS` 同机制；状态行后收成 SSE `fallback`。
- 密钥不进任何请求 / 响应 JSON（`AGENTS.md` 铁律 3）。

---

## 10. 与状态层 / 存档的关系

### 10.1 `state.py` 落地后（目标态）

- 提交步骤：管线把「已通过验证的结果 → 一批状态变更（`changes`）+ 一条事件」交给 `state.Handle.commit(*, base_seq, idempotency_key, …)`（假设 A1，签名见 `STATE-DESIGN.md` §12.1），拿回 `CommitResult`（`seq_from` / `seq_to` / `event_ids`），写入结果的 `event_ids`、把 `committed` 置 `true`。
- 只有 `state.py` 能改世界事实（§4.2）。管线、PRISM、导引者都不直接写。

### 10.2 `state.py` 落地前（过渡态）

- 同步路径复用现有 `Session.rules` 快照 + `Session.save()` 作为**临时提交出口**（同 `prism_guide.settle` 的手法：加锁 → `from_snapshot` → `perform_action` → 写回 → `save()`）。
- 幂等键先存会话块（9.4）。`event_ids` 允许为空数组，`rule_ref.roll_ref` 记录随机判定引用以补事件溯源。
- **必须在实现 PR 里写明这是过渡态**，且不新增第二套持久化字段；M7-a 落地后由一条适配 PR 切到 `state.commit`（假设 A5）。

### 10.3 存档迁移

- 提案是**无状态透明**的（9.2），**不落盘**。
- 结果**不单独落盘**；它体现为 `Session.log` 里的事件条目与规则快照的变更。若需回放，走状态层的事件日志（M7-a），不由本管线另存。

---

## 11. 测试计划与不变量（GDD §24）

### 11.1 测试矩阵（映射 GDD §24.2）

| 测试类别 | 关键案例 |
|---|---|
| 契约单元测试 | 提案 / 结果 Schema 校验；版本迁移往返；未知高版本被拒 |
| 风险分级 | R0–R4 各自路由到「直接执行 / 需确认 / 强制确认 / 澄清」；用户设置上限生效 |
| 澄清 | 歧义只问一次；回答后成提案；仍歧义则降级为拒绝，不二次追问 |
| 结算解耦 | **无 AI 配置**下 `source: "action"` 完整走完 `propose → commit`；`result` 早于 `narration` |
| 幂等 | 同键重复 `commit` 返回同一结果、无重复副作用；同 `proposal_id` 二次提交 → 409 |
| 状态映射 | `perform_action` 六种返回（5.2 表）各自映射到正确 `status` |
| 降级 | `prism_guide` 缺失 / 叙事超时 / 叙事非法输出 |
| 排程钩子 | M8 空实现 `mode == "sync"`；用假钩子断言 `enqueue` 被调一次、签名吻合 |
| 接口 | 错误码总表逐条；状态行前后行为分叉（SSE 不出现第二行 HTTP） |

### 11.2 必须守住的系统不变量（GDD §24.1，本管线相关）

- 重复提交同一事件不重复发奖 / 扣费 / 造成伤害（幂等键 + 规则级 `done_actions`）。
- AI 文本不等于状态变化；状态变更必须经验证与提交。
- 叙事不得发明结果；`result` 与叙事同源。
- 没有权限的角色不得自动知晓秘密；`discovered_facts` 带可见范围。
- 世界时钟只有一个权威来源；M8 不改时钟，M9 之后由 CHRON 唯一维护。

### 11.3 验证命令（每个实现单）

- `bash tests/run_all.sh` 全绿；
- `bash tests/content_firewall.sh` 0 命中；
- 新增测试：`python3 tests/test_action_pipeline.py`（建议文件名，随实现单定）。

---

## 12. 风险与开放问题

| # | 风险 / 问题 | 处置 |
|---|---|---|
| AR1 | **`STATE-DESIGN.md` 是否定稿**（原风险） | **已对齐（2026-10-10）**：`STATE-DESIGN.md` §12 已定稿，A1–A4 按其口径落定；第 10.2 节的过渡态仍适用于 M7-3 接线前 |
| AR2 | **风险分级加入确认后拖慢体验** | R0/R1 无确认；R2 起「按用户设置」；默认保守但可调 |
| AR3 | **「五级」与 GDD §8.3「四行」对不齐** | 6.2 给出映射表；歧义 / 权限作正交轴，不自造第六级 |
| AR4 | **NL→提案的准确率** | `mode: "action"` 永远可用作兜底；澄清只问一次；提案经服务端重验，不信任回带 |
| AR5 | **过渡态幂等层与状态层幂等重复** | 10.2 明确过渡；M7-a 落地后下沉，网页层只透传 |
| AR6 | **`time_cost` 单位换算未定**（三处口径冲突） | **已钉死（`CHRON-DESIGN.md` §2.3 / §2.4，2026-10-10）**；8.1 只锁枚举与签名，换算在 CHRON 内部；M8 不推进时钟 |
| AR7 | **新写接口与旧 `turn` 并存** | 9.1 声明旧路由行为不变；两者共用契约与提交出口；前端迁移单独立单 |

**开放问题**（不阻塞本设计）：

- 险成（narrow）的代价内容由导引者裁定——M8 未接线时 `partial` 的 `consequences` 允许为空并标注（5.2）。
- 哪些事件需要全员确认（GDD-BASELINE §8 开放问题）→ M8 风险分级 + M10 权限模型共同回答；本文只留 R4 的确认位。
- 叙事模型失败时的兜底文案标准 → 复用现有 `FALLBACK_NARRATION` 口径，必要时另立。

---

## 13. 实现切片建议（≥3 条）

按依赖顺序排列。**A1 与 A2 可并行**（不同文件）；A3 依赖 A1；A4 依赖 A3；A5 依赖 A1。

实现约束（每条都适用）：从最新 `master` 开分支；尽早开 Draft PR；**不合并**；验证命令真跑并把命令与结果写进 PR；`bash tests/run_all.sh` 与 `bash tests/content_firewall.sh` 通过；不把新文件写进 `tests/test_repo_layout.py` 的必需清单。

### A1 · 提案与结果契约（`prism_core.py` 扩展）

- 标签：`难度：中`，`能力：编程`，`能力：逻辑`
- 依赖的 PR：无
- 可并行：与 A2 不重叠。

**目标**

`prism_core.py` 能构造 / 校验**行动提案**与**行动结果**两种结构，带 `schema_version` 与惰性迁移；把 `perform_action` 的返回映射成 §8.4 的 `status`。

**验收标准**

- [ ] 提案 / 结果 Schema 以**纯函数**实现（构造 / 校验 / 迁移），无第三方依赖，`import` 无副作用。
- [ ] 5.2 表的六种映射各有一条真实断言（含 `already_done` 回放不重复发奖）。
- [ ] 迁移：缺 `schema_version` 的旧结构能补默认；未知更高版本被拒。
- [ ] 往返测试：迁移后序列化的字段集 == 目标版本字段集。
- [ ] `python3 tests/test_prism_core.py` 与 `bash tests/run_all.sh`、内容防火墙退出码 0。

**可改路径**

- `prism_core.py`
- `tests/test_prism_core.py`

**禁改路径**

- `prism_guide.py`、`notdnd_web.py`、`atlas*.py`、`static/**`、`data/**`、`docs/**`、`tests/test_repo_layout.py`

### A2 · NL → 提案解析与一次性澄清（`prism_guide.py` 扩展）

- 标签：`难度：中`，`能力：编程`，`能力：逻辑`
- 依赖的 PR：A1 已合并（消费提案 Schema）
- 可并行：与 A1 起草阶段文件不重叠，但合并顺序 A1 先。

**目标**

`prism_guide.py` 能把玩家 NL 变成 A1 的提案；关键歧义时产出**一个**澄清问题；叙事消息数组的 L4 用提交后快照重拼。

**验收标准**

- [ ] NL→提案输出过 A1 的 Schema 校验；实体引用无效时产出澄清或拒绝，不产出坏提案。
- [ ] 歧义只问一次；回答后成提案；仍歧义 → 拒绝，不二次追问（6.3）。
- [ ] `prism_guide` 缺失时本模块不参与（由网页层兜底），import 失败不影响离线。
- [ ] 复用现有传输层（`assemble_chat_stream` 同时认 SSE 与整段 JSON），不新增传输路径。
- [ ] `python3 tests/test_prism_guide.py` 与 `run_all` / 内容防火墙退出码 0。

**可改路径**

- `prism_guide.py`
- `tests/test_prism_guide.py`

**禁改路径**

- `prism_core.py`（只调用。签名不够就退回 A1）、`notdnd_web.py`、`static/**`、`data/**`、`docs/**`

### A3 · 管线编排与写接口（`notdnd_web.py` 扩展）

- 标签：`难度：高`，`能力：编程`，`能力：逻辑`
- 依赖的 PR：A1 已合并；若走 NL 路径则 A2 已合并
- **独占** `notdnd_web.py`：开工前确认无并行 PR。

**目标**

`POST /api/action/propose` / `confirm` / `commit` 与 `GET /api/action/catalog` 落地；`commit` 走「验证 → 结算 → 提交 → 叙事」，`result` 早于 `narration`；幂等键生效。离线（无 AI）时 `mode: "action"` 能完整走完一轮。

**验收标准**

- [ ] 第 9.5 节错误码总表**逐条**有测试（状态行前 / 后的行为分叉：SSE 不出现第二行 HTTP）。
- [ ] **无 AI 配置**下 `mode: "action"` 的 `propose → commit` 完整走完，`result` 先于 `narration`。
- [ ] 幂等：同键重复 `commit` 返回首次结果、无重复副作用；同 `proposal_id` 二次提交（无新键）→ 409 `该提案已提交`。
- [ ] 排程钩子：M8 空实现 `mode == "sync"`；用假钩子断言 `enqueue` 被调一次、返回键与 8.2 一致。
- [ ] 复用 `_Server` 真子进程脚手架（`tests/test_notdnd_web.py`）覆盖新路由；测试必收摊。
- [ ] `python3 tests/test_notdnd_web.py` 与 `run_all` / 内容防火墙退出码 0。

**可改路径**

- `notdnd_web.py`
- `tests/test_notdnd_web.py`

**禁改路径**

- `prism_core.py`、`prism_guide.py`、`atlas*.py`、`static/**`、`data/**`、`docs/**`

### A4 · 前端接线即时行动（`static/`）

- 标签：`难度：中`，`能力：前端`
- 依赖的 PR：A3 已合并
- 含 UI 交互（非视觉设计）；若涉及视觉设计另走 #133。

**目标**

主画面的行动按钮与输入框接到 `/api/action/*`；确认 / 澄清按风险级呈现；叙事流显示 `result` 与 `narration`。

**验收标准**

- [ ] 即时行动在浏览器里走完一轮（R0/R1 直通；R2+ 出现确认）。
- [ ] 澄清只呈现一个问题与选项。
- [ ] 无 AI 时显式行动仍可用，界面明确提示离线。
- [ ] UI 冒烟（`tests/test_ui_smoke.py`）与 `run_all` 通过。

**可改路径**

- `static/**`
- `tests/test_ui_smoke.py`

**禁改路径**

- `prism_core.py`、`prism_guide.py`、`notdnd_web.py`（只调接口）、`data/**`、`docs/**`

### A5 · 排程钩子接入 CHRON（M9 首片，跨里程碑）

- 标签：`难度：高`，`能力：编程`，`能力：逻辑`
- 依赖的 PR：A3 已合并；**`CHRON-DESIGN.md`（M9-a）已定稿**（换算表）
- 本条**属 M9**，此处只登记接口面：M9 实现 `chron.py`（L2 入口，`CHRON-DESIGN.md` §4.3）并用**适配器**满足 §8.2 的 L1 协议，管线按「可导入则用」装入；**不改提案 / 结果 Schema、不改写接口路径**。

**验收标准**

- [ ] `chron.enqueue` 返回 `mode == "async"` 的合法结构；`task_id` 非空。
- [ ] 长行动不锁玩家；同时到期排序稳定；重启后可恢复（M9 出口条件，GDD §6）。
- [ ] **未修改** M8 的提案 / 结果 Schema 与 `/api/action/*` 路径（用 A3 的测试回归证明）。

**可改路径**

- `chron.py`（新建）、`notdnd_web.py`（仅注入点）、`tests/test_chron.py`（新建）

**禁改路径**

- `ACTION-DESIGN.md`（契约不动）、`prism_core.py` 的契约函数、`docs/**`

---

## 14. 参考（GDD 章节号）

| 主题 | GDD 章节 |
|---|---|
| 行动管线七步 | §8.2 |
| 风险分级与确认 | §8.3 |
| 行动结果契约 | §8.4 |
| 行动被打断 | §8.5 |
| 自然语言不是直接指令 | §8.1 |
| 规则核心与随机骰子 | §9.1 / §9.2 |
| 失败与代价 | §9.4 |
| 行动生命周期八状态 | §6.4 |
| 玩家等待问题处理 | §6.5 |
| 时间压缩与事件屏障 | §6.6 |
| 三种时间概念 | §6.2 |
| 幂等 / 并发 / 事务 | §19.3 |
| 事件记录最小字段 | §19.5 |
| AI 权责边界 / 输出验证 / 故障降级 | §20.1 / §20.3 / §20.4 |
| 系统不变量 / 测试矩阵 | §24.1 / §24.2 |
| 可观测性（关联 ID） | §24.3 |
| 术语表（Action Proposal / Authoritative State / Idempotency） | 附录 A |
| 仓库侧决策与依赖顺序 | `GDD-BASELINE.md` §4.2 / §4.3 / §5 / §7.2 |

---

*本文由 Execution Agent 按 Issue #137 起草；修订走独立 PR，先开 Issue。*
