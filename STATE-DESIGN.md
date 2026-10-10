# 状态与事件层 · 设计方案（STATE-DESIGN）

> 状态：**待 Master Agent 审阅**。本文只定设计与拆单，不含实现。
> 读者：Master Agent（拆单）、Execution Agent（照做）。
> 依据：`GDD.html` v1.1（§5.1 / §6.2–6.9 / §8.4 / §12 / §19 / §20.4 / §24.1 / §25.1）；
> `GDD-BASELINE.md`（§4.1 落点 `state.py`、§4.2 依赖方向、§4.3 四件契约、§6 通用验收、§7.1 本单）；
> 仓库实况只读审查：`notdnd_web.py`（`Session`）、`prism_core.py`（`RuleSession`）、
> `atlas.py`（`export_state` / `restore_state`）、`prism_guide.py`（`guide` 块），
> 均以 `master @ fa5366c` 为准。默认分支是 `master`。

本文件不是规则正文，不替代 `docs/`。它描述的是「世界事实的唯一落库口」——
所有系统把**已验证**的变更交给它，它负责排号、留痕、原子落盘、可重放、可迁移。

---

## 0. Master Agent 怎么用这份方案

1. 先审第 3 节的**设计决定**与第 4 节的**存储选型结论**。有不同意的，先改本文再拆单。
2. 再看第 15 节的**风险**。涉及存储格式、并发口径、`world_time` 语义的，要么本文件钉死，要么明确留给 M9 / M10。
3. 第 16 节的四条切片已按仓库模板写好（目标 / 验收 / 可改 / 禁改 / 依赖）。可以照抄开单，也可以合并或推迟，
   但**不要把「内核」和「存档接线」揉进同一条**——前者是纯库、后者要动 `notdnd_web.py`。
4. `M8-a`（`ACTION-DESIGN.md`）引用本文§12 的提交接口；`M9-a`（`CHRON-DESIGN.md`）的持久化契约跟随本文§12；
   `M10-a`（`MULTIPLAYER-DESIGN.md`）的并发口径跟随本文§8。
   （**注意**：这三份已于 2026-10-10 按本文 §12 的提交 / 读事件 / `world_time` 口径完成接口对齐，
   见各自文首的「接口对齐（2026-10-10）」标注。）
5. Execution Agent **不合并**。审查用四行建议。

> 未推送到远端的本文按 `AGENTS.md` 不能当作交接。它必须先送进 GitHub 才能被拆单。

---

## 1. 目标与非目标

### 1.1 目标

- **唯一提交出口**：世界事实的每一次变更都经 `state.py` 的提交接口写入，带事件留痕。
- **事件日志**：变更可追溯（谁、何时、因何、改了哪条事实、可见范围），满足 GDD §19.2 / §24.1「可溯源」。
- **快照与重建**：定期检查点加速恢复；「检查点 + 事件尾部回放」必须等于存活态（第 6 节）。
- **幂等**：重复提交同一意图不产生第二次副作用（GDD §19.3 / §24.1，风险 R2「物品复制」）。
- **原子提交**：一次提交要么整体落库、要么整体不落；中断只可能留下「可安全丢弃」的尾巴（第 8、9 节）。
- **存档迁移**：旧单文件 JSON 档惰性迁移到新档，往返无损，保留备份（GDD §19.4；`GDD-BASELINE.md` §6.6）。
- **可被 M8 / M9 / M10 直接依赖的接口**：提案携带 `base_seq` 与幂等键；世界时间是不透明对象；
  并发靠「战役级串行 + 乐观版本校验」（第 12 节）。

### 1.2 非目标（明确不做）

- 不做 CHRON：本文**不**定义世界时钟、不定义调度器、不定义行动生命周期状态机。`world_time` 只做**搬运**（M9 拥有语义）。
- 不做行动管线：不解析自然语言、不做风险分级、不做澄清、不定义 Action Proposal 字段（M8 拥有）。
  本文只规定**提案进入提交接口时**必须自带什么。
- 不做多人：不加房间、账号、频道、权限、重连（M10）。本文只保证**口径不挡路**（第 8、12 节）。
- 不做 NEURA、不做 UI、不改前端。
- **不改** `prism_core.py` / `prism_guide.py` / `atlas*.py` 的语义；**不改** `docs/`；**不引入第三方运行时依赖**
  （过渡期仍守 `AGENTS.md` 铁律 2；Python 3 标准库内的 `sqlite3`、`hashlib`、`json`、`os` 不算第三方）。

---

## 2. 现状盘点（只读输入）

下表只认代码，不认文档自述。列名「已具备」= 可直接复用，「缺口」= 本层要补。

| 现况 | 位置（`master @ fa5366c`） | 已具备 | 缺口 |
|---|---|---|---|
| 存档容器 | `notdnd_web.py` `Session`（L372 起） | 单文件 JSON、`to_dict` 白名单、`os.replace` 原子写（L431）、每局 `RLock`、内存缓存 + 双检加载 | 无事件、无版本、无幂等、无重放；`log`/`seq` 是**展示流**不是事实日志 |
| 惰性迁移 | `_SESSION_DEFAULTS`（L218）+ `Session.load`（L448） | 「只补不覆盖」+ 零参工厂防串档 + 脏值降级 | 依赖一个手写默认值表；无 `schema_version`、无往返测试、无备份 |
| 规则态 | `prism_core.RuleSession`（L414） | `snapshot()`（L454）/ `from_snapshot()`（L475）、`dirty` 标记、`tactical` 刻意不入快照 | 无版本号（快照不含 schema 版本） |
| 空间态 | `atlas.py` `export_state`（L445）/ `restore_state`（L497） | 自带 `ATLAS_STATE_VERSION = 1`（L63）、「种子 + 每帧 deltas」可重放、版本不支持就抛 | 版本只在块内；与顶层事件日志无关联 |
| 导引者态 | `prism_guide.py` `guide` 块（L689 `empty_guide` / L717 `guide_from` / L788 `bind`） | 按键还原、salt 保留、逐键清洗 | 纯展示/缓存态，**不是**权威事实；含 salt 与叙事文本 |
| 测试基建 | `tests/`（12 脚本）、`tests/README.md` | 随机端口 + 临时存档目录 + 必收摊；单脚本可前台跑 | 无状态层脚本 |

**关键澄清（本层的前提）**：现有 `Session.log` + `seq` 是**给浏览器看的叙事流**（`add_log`，L502），
它按 UI 事件追加，**不满足** GDD §19.2「涉及游戏事实的操作必须有审计记录」。本层新增的**事件日志**是另一条流，
两者**序号独立、文件独立**，命名上必须分得开（第 3 节 D3、第 10 节）。

---

## 3. 设计决定（先拍板，后拆单）

| 编号 | 决定 | 理由 |
|---|---|---|
| **S1** | **存储 = 每战役一个目录的 JSON 文件族**：检查点 + 追加式事件日志 + 幂等索引（第 4 节）。 | 零第三方依赖（过渡期安全）；与仓库「JSON 落盘」「加文件即加内容」同风格；人类可读可 diff；从现有单文件档迁移代价最小。 |
| **S2** | **事件日志与叙事流分开**：事件日志 = 事实（权威）；叙事流 = 展示（可丢）。 | GDD §19.2「并非每个 UI 点击都需要世界事件」；GDD §24.1「AI 文本本身不等于状态变化」。混流会让 AI 散文变成事实。 |
| **S3** | **唯一提交出口 `commit()`**；业务模块**不**直接改块；块内容对 `state.py` **不透明**。 | GDD §5.1 / §5.2「任何系统都只能通过受控接口提出或提交变更」；`GDD-BASELINE.md` §4.2「唯一提交」。 |
| **S4** | **`state.py` 不 import 任何业务模块**（含 `prism_core` / `atlas` / `prism_guide`）。 | `GDD-BASELINE.md` §4.2 依赖方向硬约束。它只搬不透明块 + 结构化变更，语义解释归调用方。 |
| **S5** | **原子性 = 「追加 + `fsync`」写日志，「写临时 + `os.replace`」写检查点**；不用数据库事务。 | 与现有 `Session.save()` 同一套已验证手法；崩溃只可能留下可丢弃的尾部（第 9 节）。 |
| **S6** | **版本分层**：档版本 `STATE_VERSION`（封套）、事件版本 `EVENT_VERSION`、块版本各自带（如 `atlas.version`、`rules` 由 prism_core 补）。读到**更高**版本一律**拒载**，不静默降级。 | GDD §19.4「存档需包含 schema 版本与迁移路径」；静默降级会丢字段。 |
| **S7** | **战役 id（`campaign_id`）与存档 id（`sid`）分离**；M7 阶段 `campaign_id = sid`（一局一战役）。 | GDD §19.1「Campaign Instance」是权威世界；M10 要 2–6 人共享一个战役、多个连接 id，提前留出口子，M7 不付复杂度。 |
| **S8** | **`world_time` 是不透明 JSON**，`state.py` 只搬运不改写；M7 允许 `null`。 | CHRON 属 M9（`GDD-BASELINE.md` §4.3 第 4 条：时间单位仍不一致，须 M9 先钉死）。 |
| **S9** | **幂等键由提交方提供**（M8 的提案必须自带），`state.py` 只查表、记账、判重。 | GDD §19.3；键的**语义**（哪个 `action_id`、哪个客户端令牌）属 M8，硬编码在状态层会把两层绑死。 |
| **S10** | **schema 不进 `data/`**：字段级 Schema 以本文的字段表 + `state.py` 内的校验器落地。 | `data/schema/registry.json` 与 `data/FORMAT.md` 是**共享串行文件**（`MASTER.md` §3）；为一层运行时校验去占它，会把 M7 与所有数据切片串起来，不划算。将来若要入数据层，单开 Issue。 |

---

## 4. 存储布局与选型

### 4.1 布局

```
web-saves/                          # 已 gitignore；本地运行物
├── <sid>.json                      # 【旧档】现状：单文件 Session 快照
└── campaigns/
    └── <campaign_id>/              # campaign_id ∈ ^[A-Za-z0-9_-]{1,64}$（沿用 SID_RE 口径，防 ../）
        ├── snapshot.json           # 检查点（第 6 节字段级 Schema）
        ├── snapshot.json.bak       # 上一次成功检查点（崩溃回退用；原子替换前先备份）
        ├── events.ndjson           # 追加式事件日志：一行一个事件（JSON Lines）
        └── idempotency.json        # 幂等索引（也内联进检查点，此处为独立可读副本）
```

- 目录名 `campaign_id` 必须过 `SID_RE`（`^[A-Za-z0-9_-]{1,64}$`）**再拼路径**——与 `Session.load`（L456）同一条边界，
  杜绝 `../` 探测。
- `events.ndjson` 用 **JSON Lines**（每行一个完整 JSON 对象 + `\n`）：追加是 O(1)，崩溃只需丢弃不完整末行（第 9 节）。
- 归档：事件超过 `EVENT_ARCHIVE_THRESHOLD`（建议 100 000 行）时，把 `≤ archived_upto` 的行搬到
  `events.<n>.ndjson` 并只在检查点里记 `archived_upto`；**归档不删事实**，重放仍可从归档拼回。

### 4.2 选型对比（结论：**JSON 文件族**）

| 维度 | 单文件 JSON（现状扩写） | **JSON 文件族（采用）** | SQLite（stdlib `sqlite3`） |
|---|---|---|---|
| 依赖 | stdlib | stdlib | stdlib（但引入二进制文件格式） |
| 事件日志 | 无（只能往大 JSON 里塞数组） | 追加式、O(1)、可截断 | 表 + 索引，可 `SELECT` 查询 |
| 原子提交 | 全量 `os.replace` | 追加 + `fsync`；检查点原子替换 | `BEGIN/COMMIT`（WAL），天然事务 |
| 幂等 | 手写查表 | 手写查表（`idempotency.json` + 内存索引） | `UNIQUE(key)` 直接约束 |
| 并发（多写者） | 需全进程锁 | 需**战役级**锁 + 乐观 `base_seq` | 行级锁 + 事务隔离 |
| 可读 / 可 diff / 手工修 | 好 | 好（NDJSON 逐行） | 差（需工具） |
| 迁移成本（从现档） | 低 | 低（旧字段逐块搬） | 中（建表 + 导入 + 逐字段映射） |
| 备份 / 回滚 | 复制文件 | 复制目录 | 需 `VACUUM INTO` 等专门做法 |
| 与仓库风格 | 一致 | 一致 | 不一致（破坏「纯文本可溯源」叙事） |
| 增长代价 | 每次写全量，日志越长越慢 | 日志只追加；检查点可控频率 | 均摊良好 |

**结论：采用 JSON 文件族。** 在 M7–M10 的现实负载下（单机、单进程、2–6 人、文本游戏、写入稀疏），
「战役级串行 + 追加日志 + 原子检查点」足以满足原子性与幂等；代价换来的是零依赖、可读、可从现状无缝迁移。

**何时改用 SQLite（触发条件，任一成立即另开 Issue 评估）**：

1. 出现**第二个写进程**（如把调度器拆成独立进程，或多机部署）——文件锁跨进程不可靠。
2. 单战役事件数长期 > 10^6，或重放（冷启动）墙钟 > 2 秒，且归档仍不够。
3. 需要**跨战役查询**（如全服统计、经济报表）——文件族没有索引。
4. M10 的并发测试暴露出「乐观校验重试风暴」（第 8 节）。

> 这些是**判据**，不是承诺。M7 不预埋 SQLite 抽象层——过早抽象会把两种存储的最差部分都留下。

---

## 5. 事件封套（字段级 Schema）

一次提交写入**一个或多个**事件。同一提交写入的事件共享 `commit_id`、`seq` 连续。
GDD §19.5 点名的字段**全部覆盖**。

### 5.1 字段表

| 字段 | 类型 | 必填 | 语义 | 例 |
|---|---|---|---|---|
| `event_id` | string | 是 | 全局唯一、可确定性重建：`"{campaign_id}:{seq:012d}"` | `"yunji:e000000000042"` |
| `campaign_id` | string | 是 | 战役（权威世界）id，GDD §19.5「战役 ID」 | `"yunji"` |
| `seq` | int ≥ 1 | 是 | 战役内**单调递增、无空洞**的序列号，GDD §19.5「序列号」 | `42` |
| `commit_id` | string | 是 | 本次原子提交的分组：`"{campaign_id}:c{n:08d}"`；同 `commit_id` 的 `seq` 连续 | `"yunji:c00000031"` |
| `world_time` | object \| null | 是（可为 `null`） | GDD §19.5「世界时间」。**不透明**，语义与形状归 CHRON（`CHRON-DESIGN.md` §3.1；本文 §6.2 / §12.3），M7 允许 `null` | `null` 或 `{"world_minute":17310,"day":12,"tod":30}` |
| `type` | string | 是 | GDD §19.5「类型」。`"<domain>.<verb>"`，小写，`^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$` | `"core.commit"` / `"map.move"` / `"resource.spend"` |
| `actor` | string | 是 | GDD §19.5「主体」。实体 id；系统发起用 `"world"` | `"npc-01"` |
| `target` | string \| null | 是 | GDD §19.5「目标」 | `"loc-02"` |
| `cause` | string \| null | 是 | GDD §19.5「原因 / 触发事件」：上游 `event_id`；玩家发起的根因是 `null` | `"yunji:e000000000041"` |
| `visibility` | object | 是 | GDD §19.5「可见范围」+ §12.4 访问控制 | `{"scope":"private","subjects":["pc-1"]}` |
| `changes` | array | 是 | GDD §19.5「状态变化」。声明式变更清单（5.3） | 见 5.3 |
| `rules_version` | string | 是 | GDD §19.5「规则版本」：本次裁决所依据的规则 / 数据包修订号 | `"prism@data-79"` |
| `roll_refs` | array | 是（可空数组） | GDD §19.5「随机判定引用」：指向 PRISM 掷骰账本，骰点**只来自 PRISM**（§4.2 AI 边界） | `[{"roll_id":"r-7","seed":123,"detail":"d20=14"}]` |
| `commit_status` | string | 是 | GDD §19.5「提交状态」。落盘日志里**恒为** `"committed"` | `"committed"` |
| `source` | string | 是 | GDD §19.5「创建来源」：`player` / `npc` / `system` / `ai_proposal` | `"player"` |
| `idempotency_key` | string \| null | 是 | 产生本次提交的幂等键（第 7 节）；系统 tick 可 `null` | `"a1b2…"` |
| `ts` | int | 是 | 现实时间毫秒，**仅供审计**；不参与任何权威判断（GDD §6.9：不以客户端时钟为权威） | `1760000000000` |

**枚举（初始，可扩展）**

- `commit_status`：`committed`（落盘唯一取值）；`rejected` / `conflict` 只出现在**未落盘**的 `CommitResult` 里（第 8 节）。
- `source`：`player` / `npc` / `system` / `ai_proposal`。
- `visibility.scope`：`public`（全房间）/ `team`（同队）/ `private`（`subjects` 指定）/ `gm`（导引者内部）。
- `type` 的初始登记（M7 只强求第一条；其余由 M8 / M9 增补）：`core.commit`、`map.move`、`clock.advance`、
  `resource.spend`、`combat.damage`、`knowledge.gain`。

### 5.2 一条事件（示例）

```json
{
  "event_id": "yunji:e000000000042",
  "campaign_id": "yunji",
  "seq": 42,
  "commit_id": "yunji:c00000031",
  "world_time": null,
  "type": "resource.spend",
  "actor": "pc-1",
  "target": "item-0093",
  "cause": null,
  "visibility": {"scope": "team", "subjects": ["pc-1", "pc-2"]},
  "changes": [
    {"block": "rules", "path": "/party/0/inventory/item-0093", "op": "unset",
     "before": {"id": "item-0093", "n": 1}, "after": null}
  ],
  "rules_version": "prism@data-79",
  "roll_refs": [],
  "commit_status": "committed",
  "source": "player",
  "idempotency_key": "9f2c…",
  "ts": 1760000000000
}
```

### 5.3 `changes` 元素（声明式变更）

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `block` | string | 是 | 目标不透明块名：`rules` / `atlas` / `guide` / `chron`（M9 预留）/ `world`（未来） |
| `path` | string | 是 | 块内 **JSON Pointer**（RFC 6901），如 `/party/0/vitality`；根为 `""` |
| `op` | string | 是 | `set` / `unset` / `delta`（数值增量）/ `append`（列表追加）/ `replace_block`（整块替换） |
| `before` | any | 是（可 `null`） | 变更前值；`append` / `replace_block` 时为 `null`。审计与反演用 |
| `after` | any | 是（可 `null`） | 变更后值；`unset` 时为 `null` |

- `before` / `after` 让事件日志**可审计、可反演**（重放需要 `after`；回滚 / 排查需要 `before`）。
- `state.py` **不理解** `path` 的业务含义，只按 `op` 机械应用；含义归调用方（S4）。
- `delta` 只允许整数（避免浮点重放漂移；浮点字段一律存整数毫厘 / 毫秒，见第 6 节）。

### 5.4 机器可校验的 Schema 骨架

M7-1 在 `state.py` 内落一个**零依赖校验器** `validate_event(ev) -> list[str]`，覆盖：必填、类型、
`type` / `op` / `scope` / `source` 的枚举、`seq ≥ 1`、`event_id` 与 `(campaign_id, seq)` 一致、
`changes[*].path` 以 `/` 开头（或空串）。**不**新增 `data/schema/**` 文件（S10）。

---

## 6. 快照（检查点）与重建

### 6.1 字段表（`snapshot.json`）

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `schema_version` | int | 是 | 档版本 `STATE_VERSION`（初值 `1`） |
| `kind` | string | 是 | 恒为 `"state.snapshot"` |
| `campaign_id` | string | 是 | 战役 id |
| `seq` | int ≥ 0 | 是 | 本检查点**已折入**的最大事件序号；`0` = 创世态 |
| `world_time` | object \| null | 是 | 不透明，见 5.1 |
| `created` | int | 是 | 建局现实时间毫秒（整数，避免浮点漂移） |
| `updated` | int | 是 | 本检查点写入的现实时间毫秒 |
| `save_name` | string | 是 | 展示名（沿用 `Session.save_name`，≤ 40 字符） |
| `stream` | object | 是 | **叙事流**（S2）：`{"log":[...], "cursor":<int>}`——沿用现有 `log` 与 `seq` 语义，游标改名 `cursor` 以与事件序号分开 |
| `blocks` | object | 是 | 不透明块：`{"rules": …, "atlas": …, "guide": …}`（外加 M9 的 `chron`） |
| `indexes` | object | 是 | `{"idempotency": {<key>: <record>}}`（第 7 节），以及 `{"archived_upto": <int>}` |
| `checksum` | string | 是 | 对**除 `checksum` 外**的载荷做规范化 JSON 后的 SHA-256 十六进制 |

- **规范化 JSON**（canonical）：`json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`，
  且**禁止浮点**（一律整数毫秒 / 整数毫厘）。规范化是 `checksum` 可比、重放可比的前提。
- `blocks` 里的 `rules` 就是 `prism_core.RuleSession.snapshot()` 的原样结果；`atlas` 就是
  `atlas.export_state()` 的原样结果；`guide` 就是 `prism_guide` 的 `guide` 块。**`state.py` 不解析它们。**

### 6.2 重建（replay）规则

```
存活态  ←  检查点(snapshot.json)  再按序折入  events.ndjson 中 seq > snapshot.seq 的事件
创世态  ←  seq = 0 的空检查点（blocks 为各方空快照，stream 为空）
```

- **不变量（可在测试里断言）**：`replay(genesis, events[1..N])` 与 `live(N)` 在规范化 JSON 下**逐字节相等**。
- 折入顺序：`seq` 严格递增（事件日志天然有序）；**同一 `world_time` 的排序由 CHRON 在写入前确定**，
  状态层不重排——它只保证「写入顺序 = `seq` 顺序 = 折入顺序」。
- 重放**只应用 `changes`**，**不重新掷骰**：`roll_refs` 只作引用（GDD §24.2「随机种子回放」由 PRISM 账本提供）。

### 6.3 检查点写入时机（偷懒是安全的）

崩溃后靠日志补齐，所以检查点**可以滞后**：

- 每 `CHECKPOINT_EVERY = 32` 个事件，或
- 距上次检查点 > `CHECKPOINT_MAX_AGE_S = 5.0` 秒，或
- `events.ndjson` 大小越过 `CHECKPOINT_MAX_LOG_BYTES = 1 MiB`，或
- 进程**优雅退出**（`atexit` / 收到停止信号）。

任一触发即：备份现有 `snapshot.json` → 写 `snapshot.json.tmp` → `os.replace`（S5）。

---

## 7. 幂等键（字段级 Schema）

### 7.1 键的派生（语义归 M8，此处钉死输入）

```
idempotency_key = sha256_hex(
    campaign_id + "\x1f" + actor + "\x1f" + action_id + "\x1f" + client_token
)
```

- `action_id`：M8 提案的唯一 id（必填）。
- `client_token`：客户端为「这一次点击」生成的随机串（建议 16 字节十六进制）；缺省时用空串——
  此时幂等退化为「同主体 + 同行动 = 同一次」。M8 应在契约里要求它，缺省只是兜底。
- 系统发起且无需幂等的事件（如 `clock.advance`）可用 `null` 键，**不进索引**。

### 7.2 记录（`indexes.idempotency[key]`）

| 字段 | 类型 | 必填 | 语义 |
|---|---|---|---|
| `key` | string | 是 | 幂等键（= 字典键，冗余存一份便于导出 / 排查） |
| `commit_id` | string | 是 | 首次提交的分组 id |
| `seq` | int | 是 | 首次提交写入的**首个**事件序号 |
| `event_ids` | array[string] | 是 | 首次提交写入的全部 `event_id` |
| `request_digest` | string | 是 | 对提交入参（`type`/`actor`/`target`/`changes`/…）规范化后的 SHA-256 |
| `result_digest` | string | 是 | 对 `CommitResult`（状态、事件、块摘要）规范化后的 SHA-256 |
| `ts` | int | 是 | 首次提交现实时间毫秒 |
| `expires_at` | int \| null | 是 | 淘汰时间（默认 `ts + IDEM_TTL_DAYS(90) * 86400_000`） |

### 7.3 行为

| 情形 | 判定 | 动作 |
|---|---|---|
| 键不存在 | 新提交 | 正常提交（第 8 节） |
| 键存在，`request_digest` 相同 | **重放** | **不写任何事件**；返回首次 `result_digest` 对应的结果，`status="replayed"` |
| 键存在，`request_digest` 不同 | **冲突** | 不写任何事件；`status="conflict"`，错误码 `幂等键冲突`（400/409，由网页层定） |
| 键存在但已过期（`now > expires_at`） | 视为新提交 | 正常提交并覆盖记录（过期只为省盘，不改变语义边界） |

- 淘汰：检查点写入时顺手剔除 `expires_at < now` 的记录；保留**最近 `IDEM_KEEP = 4096` 条**为硬下限。
- 这是「重复提交不重复副作用」（GDD §24.1、风险 R2）的**唯一**机制——不靠调用方自觉。

---

## 8. 提交时序与冲突处理

### 8.1 提交时序（单写者，单进程）

```
调用方（M8 / M9 / M10）
   │  ① 构造提交：base_seq、idempotency_key、type、actor、target、cause、
   │              visibility、changes、rules_version、roll_refs、source
   ▼
state.commit(...)                       ← 战役级 RLock 取锁（同 Session.lock 的粒度）
   ② 确保已加载 / 已迁移（第 10 节）
   ③ 幂等查表（第 7.3 节）── 命中重放 / 冲突 → 直接返回（不取事件号）
   ④ 版本校验：base_seq == 当前 seq ？
        否 → 冲突策略（8.2）→ 返回 status="conflict"
   ⑤ 生成事件：分配 seq（连续）、commit_id、event_id；计算块新值
   ⑥ 追加 events.ndjson（每行一条）+ flush + os.fsync
   ⑦ 应用到内存态（blocks / indexes / seq / world_time / stream）
   ⑧ 记幂等记录（内存 + 将来随检查点落盘）
   ⑨ 检查点？（按 6.3 的时机）
   ⑩ 释放锁，返回 CommitResult
```

**`CommitResult`**

```json
{
  "status": "committed | replayed | conflict | rejected",
  "commit_id": "yunji:c00000031",
  "seq_from": 42, "seq_to": 42,
  "event_ids": ["yunji:e000000000042"],
  "state_digest": "<对提交后 seq + blocks 摘要的 sha256>",
  "error": null
}
```

### 8.2 冲突策略（乐观并发）

提案必须携带 `base_seq` = 提案方读取状态时的 `seq`。

1. **`reject`（默认）**：`base_seq != 当前 seq` → 返回 `conflict` + 当前 `seq` 与 `state_digest`，
   由调用方**重读后重新提案**。适用于「两人同时抢同一件物品」：先提交者赢，后者收到 conflict，
   在 M8 重新走一次「物品还在不在」的验证。
2. **`revalidate`（可选）**：调用方在提交时传一个纯函数 `revalidate(state) -> bool`（`state` 是只读视图）。
   版本不符时先调它：返回 `True` 则**在当前状态上继续**（事件号的分配与 `changes` 由调用方按新状态重算，
   因此 `revalidate` 只用于「结果与基线无关」的幂等动作）；返回 `False` 则按 1 拒绝。
   M7 **不实现**这个钩子，只在接口上留位（第 12 节）——M8 需要时再加，不进 M7 实现单。
3. **同 tick 排序**：写在前面的 `seq` 就是权威顺序。CHRON（M9）在写入前按
   `(world_time, priority, 提交到达序)` 排定；`priority` 由提案携带（默认 `0`，越大越先）。

### 8.3 崩溃点 → 持久状态

| 崩溃发生在 | 磁盘上 | 恢复结果 |
|---|---|---|
| ⑤ 之前 | 无新事件 | 提交未发生（幂等键未记，调用方可安全重试） |
| ⑥ 追加中途（半行） | 末行不完整 | 丢弃末行，提交**未发生**（半行无 `\n` 或 JSON 解析失败） |
| ⑥ 完成、⑦ 前 | 事件已落，内存未应用 | 重启重放该事件 → 提交**已发生**；幂等记录随检查点补齐（7.3 表按 `request_digest` 仍能挡重复） |
| ⑦ 后、⑨ 前 | 事件已落，检查点旧 | 重启：检查点 + 尾部重放 → 存活态一致 |
| ⑨ 中途（写 `.tmp` 时） | 旧 `snapshot.json` 完好 | 丢弃 `.tmp`，用旧检查点 + 重放 |
| ⑨ `os.replace` 时 | 新版或旧版二选一（原子） | 都一致（`os.replace` 不产生半个文件） |

> 关键性质：**「提交已发生」的判据是事件是否落进 `events.ndjson`**，检查点只影响恢复速度，不影响正确性。

### 8.4 反模式（禁止）

- 任何模块**直接**写 `snapshot.json` / `events.ndjson`（绕过 `commit()` 即绕过幂等与留痕）。
- 用客户端时间戳决定顺序或过没过期（GDD §6.9）。
- 把叙事文本、AI 建议、模型输出写进 `changes`（GDD §24.1：AI 文本 ≠ 状态变化）。

---

## 9. 崩溃恢复

**加载顺序**（`state.open(campaign_id)`）：

1. **检查点**：`snapshot.json` 存在且 `checksum` 校验通过 → 用它；校验失败 / 解析失败 → 退 `snapshot.json.bak`；
   都失败 → 退**创世态**并记一条 `state_corrupt` 说明（**绝不猜内容**）。
2. **事件日志**：逐行解析 `events.ndjson`（+ 归档段）。
   - 末行无 `\n` 或 JSON 解析失败 → **丢弃末行**（半写），记一条说明。
   - 中间行解析失败或 `seq` 不连续 → **拒载**（不自动跳过——中间断裂意味着日志被外部改坏，
     自动跳过会静默丢事实）；返回 `None` + 说明，交由人工 / 备份处理。
   - `seq ≤ snapshot.seq` 的行忽略（归档 / 重复段）。
3. **重放**：按 `seq` 顺序应用 `changes`（第 6.2 节）。
4. **清理**：`*.tmp` 一律删除（原子写残留）。

**测试锚点**：`kill -9` 落在 8.3 的每一行都要有一条用例（第 14 节）。

---

## 10. 存档迁移（旧单文件 JSON → 新档，GDD §19.4）

### 10.1 惰性迁移（不跑批处理脚本）

迁移发生在**第一次用新代码加载老档**时，在内存里完成，随后在**下一次正常提交 / 检查点**时落成新档。

| 老字段（`<sid>.json`） | 新位置 | 处理 |
|---|---|---|
| `sid` | `campaign_id`（S7：M7 阶段相等） | 逐字搬 |
| `created` | `created` | 秒（float）→ 毫秒（int），`round` 一次 |
| `save_name` | `save_name` | 截 40，逐字 |
| `log` / `seq` | `stream.log` / `stream.cursor` | **逐字搬**；叙事流语义不变，`/api/log?since=` 行为不变 |
| `rules` | `blocks.rules` | 逐字搬；形状由 `prism_core` 负责（`_restore_rules` 的「只补不覆盖」保留） |
| `atlas` | `blocks.atlas` | 逐字搬；`null` 保持 `null`（首次用图时仍走懒编译） |
| `guide` | `blocks.guide` | 逐字搬；salt **必须**保留（`guide_from` 的按键合同不变） |
| （无） | `schema_version` / `kind` / `seq`（事件） / `world_time` / `indexes` / `checksum` | 补默认：`STATE_VERSION`、事件 `seq = 0`、`world_time = null`、空索引、现算 `checksum` |

### 10.2 迁移步骤（在锁内、写盘前）

1. 读老档（沿用 `Session.load` 的脏值降级口径；读不了 → 返回 `None`）。
2. 备份：`<sid>.json` → `campaigns/<campaign_id>/snapshot.json.pre-v0.bak`（GDD §19.4：不可逆迁移保留备份）。
3. 建 `snapshot.json`（`schema_version = STATE_VERSION`，事件 `seq = 0`），写 `.tmp` + `os.replace` + **回读校验 `checksum`**。
4. `events.ndjson` 建成空文件（老档**没有**事实日志，历史只能从迁移点起算——第 15 节 R4）。
5. **两个文件都成功**之后，才删除老档（删除失败只记说明，不滚回——新档已可用，老档留 `.bak` 兜底）。
6. 记一条 `system` / `type="migration"` 事件（`source="system"`），让「这里发生过一次迁移」也可溯源。

### 10.3 版本边界

- `schema_version` **等于** `STATE_VERSION`：正常加载。
- **小于**：走版本迁移链（`v1 → v2 → …`），每步留备份；M7 只有 `v0 → v1`（就是 10.1）。
- **大于**：**拒载**（S6）——返回 `None` + 说明，**不写盘**、不降级，老文件原样保留。
- `atlas` 块内的 `version`（现为 `1`）**不由本层迁移**：它归 `atlas.restore_state`（L504 已在版本不符时抛），
  本层只负责把块搬过去。

---

## 11. 与既有存档块的关系

| 块 | 权威方 | 在本层里的角色 | 不变量 |
|---|---|---|---|
| `rules` | `prism_core.RuleSession`（`snapshot()` L454 / `from_snapshot()` L475） | **不透明块**：原样存、原样取。规则态的唯一真相仍在 `prism_core`（现状口径不变） | 本层**不** import `prism_core`（S4）。规则态变更由调用方在锁内用 prism_core 公开 API 算出新快照，**作为 `changes` 的 `after` 交提交** |
| `atlas` | `atlas.py`（`export_state` L445 / `restore_state` L497） | **不透明块**：原样存。地图重建**仍走 `atlas.restore_state`**（种子 + deltas） | **同一事实只有一处权威回放**：地图增量以 `atlas` 块内 `deltas` 为准，顶层事件日志**不重复**存地图增量（否则两份真相必然漂移）。顶层只记「发生过一次 `map.move`」并带 place id 供审计 |
| `guide` | `prism_guide`（`empty_guide` L689 / `guide_from` L717） | **非权威展示块**：原样存；salt 必须保留 | `guide` **不进事件溯源**（S2）：`transcript`、`realizations`、AI 文本都不是事实（GDD §24.1）。秘密 / 线索的可见范围只以 `visibility` 表达，**不把秘密内容写进事件**（GDD §12.4） |
| `stream`（叙事流） | `notdnd_web.Session`（`add_log` L502） | 展示流，游标 `cursor`（原 `seq`） | 与事件 `seq` **两条独立序列**，禁止互相赋值；`/api/log?since=` 继续读 `cursor` |
| `chron` | **M9**（本层只留位） | 预留不透明块名 | M9 的排程 / 时钟态落在 `blocks.chron`；本层不解析（S8） |

> 一句话：**`state.py` 是「排号 + 留痕 + 原子落盘 + 可重放 + 可迁移」的通用层，不是第二个规则引擎。**
> 它不认识六维、不认识地点、不认识 NPC——认识它们的层把结果交进来。

---

## 12. 对 M8 / M9 / M10 的接口契约（钩子）

### 12.1 Python 接口（M7-1 落地，签名先钉死）

```python
STATE_VERSION = 1
EVENT_VERSION = 1

class Handle:
    campaign_id: str
    def view(self) -> dict: ...                     # 只读视图：深拷贝 {campaign_id, seq, world_time, blocks, stream}
    def commit(self, *, base_seq, idempotency_key, type, actor, target=None,
               cause=None, visibility=None, changes=(), rules_version,
               roll_refs=(), world_time=None, source="player",
               revalidate=None) -> dict: ...        # 返回 CommitResult（第 8.1 节）
    def replay(self, upto_seq=None) -> dict: ...     # 从创世或检查点重放出完整状态
    def flush(self) -> None: ...                     # 强制写检查点（优雅退出用）
    def close(self) -> None: ...

def open_campaign(campaign_id: str) -> Handle: ...   # 加载 + 惰性迁移；损坏则抛 StateError
def validate_event(ev: dict) -> list[str]: ...        # 5.4 的纯校验器
class StateError(Exception): ...
```

- 依赖方向：`Handle` 里**没有** `prism_core` / `atlas` / `prism_guide` 的符号（S4）。
- `import state` **不得**触碰磁盘（沿用「no side effects on import」口径，`GDD-BASELINE.md` §6.5）。

### 12.2 给 M8（`ACTION-DESIGN.md`）

- 提案（Action Proposal）在**进入提交**时必须自带：`base_seq`、`idempotency_key`、`type`、`actor`、
  `target`、`visibility`、`changes`、`rules_version`、`roll_refs`、`source`。
- M8 的「行动结果契约」（GDD §8.4）→ 本文的映射：`effects` / `consequences` → `changes`；
  `event_ids` ← `CommitResult.event_ids`；`time_cost` **不进本层**（属 CHRON / M9）；
  `status`（`success|partial|failure|blocked`）**不进本层**（它是行动语义，不是提交状态）。
- M8 **不得**直接写块；所有落库经 `commit()`。

### 12.3 给 M9（`CHRON-DESIGN.md`）

- `world_time` 是 5.1 的**不透明**字段：M9 定义其形状（`CHRON-DESIGN.md` §3.1；§6.2 三种时间概念）并**在提交时传入**；本层不改写。
- M9 的排程 / 时钟态放 `blocks.chron`（不透明）；到期事件由 CHRON 转换为一次 `commit()`（`type="clock.*"`）。
- 重启恢复：CHRON 从 `replay()` 得到的 `blocks.chron` + 事件日志重建队列；**本层不实现调度器**。
- M9 **必须**在自己设计单里先钉死时间单位换算（`GDD-BASELINE.md` §4.3 第 4 条），本层不碰。

### 12.4 给 M10（`MULTIPLAYER-DESIGN.md`）

- `campaign_id` 与连接 id 分离（S7）：M10 允许多个 `sid` 指向同一 `campaign_id`。
- 并发：M10 复用「战役级串行 + 乐观 `base_seq`」；抢物品 / 同时攻击 / 重复提交的测试清单直接打在 `commit()` 上。
- 权限：`visibility`（5.1）是**信息过滤的输入**，不是权限模型本身；M10 拥有权限模型（GDD §12.4）。
- 断线重连：客户端从 `view()`（或 `snapshot + 事件尾部`）恢复，与 GDD §18.4「从已提交事件序列或当前快照恢复」一致。

---

## 13. 不变量与验收（映射 GDD §24.1）

| GDD §24.1 不变量 | 本层的落点 |
|---|---|
| 物品唯一归属（不能被两人同时拥有） | 战役级串行提交 + `base_seq` 乐观校验（8.2）：同一物品的两次认领，必有一次拿到 `conflict` |
| 死亡 / 伤害 / 资源消耗 / 任务完成**可溯源** | 每个事实变更都有事件（`changes` + `roll_refs` + `cause`） |
| 唯一权威时钟；客户端本地时间不得改排程 | 本层**无时钟**（S8）；`ts` 仅审计；顺序只认 `seq` |
| 重复提交不重复副作用 | 幂等键（第 7 节） |
| AI 文本 ≠ 状态变化 | `guide` 块不进溯源（第 11 节）；`changes` 不得来自模型输出（8.4） |
| — （本层自定） | 事件 `seq` **无空洞**；检查点 `checksum` 一致；`replay == live`（6.2） |

**执行命令（每条实现单都要真跑）**

```bash
python3 tests/test_state.py          # 新增：本层的用例（第 14 节）
bash tests/run_all.sh                # 现有 12 脚本 + 新增脚本，全绿
bash tests/content_firewall.sh       # 0 命中
python3 -m py_compile state.py tests/test_state.py
```

---

## 14. 测试计划

新增 `tests/test_state.py`（沿用 `tests/README.md`：随机 / 临时存档目录、必收摊、无第三方依赖）。
算法契约至少覆盖下列四组（验收标准点名的三组 + 迁移）：

| 组 | 用例 | 断言 |
|---|---|---|
| **幂等** | 同一 `idempotency_key` 提交两次 | 事件数不变（`+1` 非 `+2`）；第二次 `status="replayed"`；`result_digest` 与首次相同；**副作用只发生一次**（如物品只被移除一次） |
| | 同键、不同 `request_digest` | `status="conflict"`；**不写任何事件**；错误码固定 |
| | 键过期后再提交 | 视为新提交（可再写一次），语义边界与文档一致 |
| **快照重建 == 存活态** | 创世 + 全量事件回放 | 与存活态规范化 JSON **逐字节相等** |
| | 检查点 + 尾部回放 | 同上（检查点滞后不影响结果） |
| | 空检查点 / 只有检查点无事件 | 与创世态相等 |
| **崩溃恢复** | 追加中途崩（半行） | 末行被丢弃；提交未发生；`replay == live` |
| | 追加完成、检查点未写 | 重启重放即得提交后的态；幂等仍生效 |
| | 检查点损坏 + `.bak` 有效 | 用 `.bak` + 重放恢复；记说明 |
| | 检查点与 `.bak` 都坏 | 退创世态 + 记 `state_corrupt`；**不猜** |
| | 日志中间断裂（`seq` 跳号） | **拒载**（返回 `None` + 说明），不自动跳过 |
| | `.tmp` 残留 | 加载时清理；不影响结果 |
| **迁移** | 老单文件档（含 `rules`/`atlas`/`guide`/`log`）加载 | 各块逐字无损；salt 保留；`stream.cursor` 与老 `seq` 相等 |
| | 迁移往返 | 迁移→检查点→重载：规范化 JSON 相等；`.bak` 存在 |
| | `schema_version` 更高 | **拒载**且**不写盘**（文件指纹不变） |
| | 老档缺块 / 脏值 | 按「只补不覆盖 + 脏值降级」处理，不因一键删掉整块 |

另加两条**结构**断言（防越权）：

- `import state` 时**不触碰磁盘**（在空目录里 import 后目录仍为空）。
- `state.py` 的源码**不出现** `prism_core` / `prism_guide` / `atlas` 的 import（S4 的机械守卫）。

---

## 15. 风险与开放问题

| # | 风险 / 问题 | 处置 |
|---|---|---|
| R1 | 事件日志无界增长 | 检查点 + 归档段（4.1 `events.<n>.ndjson` + `archived_upto`）；归档不删事实 |
| R2 | 检查点是**全量**块，块大时写放大 | 检查点频率可偷懒（6.3）；若 `rules`/`atlas` 块过大，M7 之后再评估「块级增量检查点」（不在 M7） |
| R3 | 单进程假设：跨进程 / 多机时文件锁失效 | 明确列为**触发条件**（4.2）：出现第二写进程即评估 SQLite |
| R4 | 迁移点之前的历史**没有**事实日志（老档只有展示流） | 迁移时记一条 `migration` 事件；不伪造历史事件；文档写明「权威日志自迁移点起」 |
| R5 | `world_time` 语义未定（M9） | 先做不透明搬运（S8）；M9 定稿后**不改**本层接口 |
| R6 | `campaign_id = sid` 会在 M10 变化 | S7 已分离；M10 只改「哪个 sid 指向哪个 campaign_id」，不改本层格式 |
| R7 | 幂等键由调用方提供 → 调用方漏传即幂等失效 | M8 契约强制（12.2）；本层对非系统提交要求 `idempotency_key` 非空，否则 `rejected` |
| R8 | `changes` 的 `path` 是弱契约（字符串） | `before`/`after` 让错误**可发现**（应用后摘要不符即冲突）；M7 不做 schema 级 path 校验 |
| R9 | 并发重试风暴（多人抢资源） | 8.2 的 `revalidate` 钩子留位；M10 用并发测试清单量化 |
| R10 | 敏感内容进事件（秘密、私聊） | `visibility` 表达范围；秘密正文**不写进 `changes`**（第 11 节）；M10 权限模型收口 |

**开放问题（记录在案，不阻塞 M7）**

- 事件的保留期 / 是否需要压缩或加密（不入 M7）。
- 是否要一个跨战役的**只读事件索引**（供复盘 / 统计）——属 M13 平台化。
- `visibility.scope` 的最终取值集是否够用——由 M10 权限模型回答。

---

## 16. 实现切片建议（供 Master 直接开单）

> 四条。一条一个意图。都**不引入第三方运行时依赖**（过渡期守铁律 2）。都**不改** `docs/`。
> 前两条互不撞文件但**同占 `state.py`**，需串行；第三条动 `notdnd_web.py`，开单前确认无并行 PR。

### M7-1 · 状态层内核：事件封套 + 原子提交 + 幂等 + 版本

- **标签：** `难度：高`，`能力：编程`，`能力：逻辑`
- **依赖：** 无（`STATE-DESIGN.md` 已合并）。
- **文件：** `state.py`（新建）、`tests/test_state.py`（新建）
- **说明：** 落第 4.1 布局、第 5 节事件封套、第 7 节幂等、第 8 节提交时序（含 8.3 崩溃点）、第 6.1 检查点字段、
  `validate_event`（5.4）、`open_campaign` / `Handle` 骨架（12.1）。**只做库**，不碰 HTTP、不碰 `notdnd_web.py`。
- **可改：** `state.py`，`tests/test_state.py`
- **禁改：** `notdnd_web.py`，`prism_core.py`，`prism_guide.py`，`atlas.py`，`atlas_compile.py`，`atlas_gen.py`，
  `static/**`，`data/**`，`docs/**`，`.github/**`，`README.md`，`MASTER.md`，其它既有 `tests/**`
- **验收：** `python3 tests/test_state.py` 通过；覆盖第 14 节的**幂等**组与**崩溃恢复**组的全部用例；
  `seq` 无空洞；`import state` 不触碰磁盘；源码无业务模块 import；`bash tests/run_all.sh` 与
  `bash tests/content_firewall.sh` 通过。

### M7-2 · 快照与重放：检查点调度 + 重建 == 存活态

- **标签：** `难度：中`，`能力：编程`，`能力：逻辑`，`能力：测试`
- **依赖：** M7-1 已合并（同占 `state.py`，不得并行）。
- **文件：** `state.py`，`tests/test_state.py`
- **说明：** 落第 6.2 重放规则、6.3 检查点时机、第 9 节加载顺序（含 `.bak` 回退、末行丢弃、中间断裂拒载）、
  归档段（4.1）。加 `Handle.replay()` / `Handle.flush()`。
- **可改：** `state.py`，`tests/test_state.py`
- **禁改：** 同 M7-1
- **验收：** 覆盖第 14 节**快照重建**组与**崩溃恢复**组的剩余用例；`replay == live` 在规范化 JSON 下逐字节相等；
  检查点滞后 / `.tmp` 残留 / 双坏退创世均有断言；`bash tests/run_all.sh` 通过。

### M7-3 · 存档接线与旧档迁移（动 `notdnd_web.py`）

- **标签：** `难度：高`，`能力：编程`，`能力：数据`，`能力：测试`
- **依赖：** M7-1、M7-2 已合并。**并且**开单时没有其它开放 PR 改 `notdnd_web.py` / `tests/test_notdnd_web.py`。
- **文件：** `notdnd_web.py`，`tests/test_notdnd_web.py`，`tests/test_state.py`，`state.py`
- **说明：** `Session` 保留为网页层内存门面，`save()` / `load()` 改为委托 `state`（第 11 节）；
  `_SESSION_DEFAULTS` 的「只补不覆盖」语义移交迁移层；`stream.cursor` 承接原 `seq`，`/api/log?since=` 行为不变；
  `prism_core` / `atlas` / `guide` 三块按不透明块搬运。落第 10 节迁移与 `.bak`。**不改**任何块的语义。
- **可改：** 上列四个路径
- **禁改：** `prism_core.py`，`prism_guide.py`，`atlas.py`，`atlas_compile.py`，`atlas_gen.py`，
  `static/**`，`data/**`，`docs/**`，`.github/**`
- **验收：** 老档（含 `rules`/`atlas`/`guide`/`log`）加载后各块逐字无损、salt 不变；迁移往返规范化 JSON 相等、
  `.bak` 存在；`.env` 无对局时可离线跑；`/api/log?since=` 与 `/api/session` 行为与改动前一致（用既有用例证明）；
  `bash tests/run_all.sh` 与 `bash tests/content_firewall.sh` 通过。

### M7-4 · 只读状态视图接口（可选，可被 M7-3 吸收）

- **标签：** `难度：低`，`能力：编程`，`能力：测试`
- **依赖：** M7-3 已合并。
- **文件：** `notdnd_web.py`，`tests/test_notdnd_web.py`
- **说明：** `GET /api/state`（当前战役的 `view()` 摘要：`campaign_id` / `seq` / `world_time` / 各块是否存在）
  与 `GET /api/events?since=<seq>`（只读增量拉取，只回 `seq > since` 的事件）。**只读**，不落盘、不改状态。
- **可改：** `notdnd_web.py`，`tests/test_notdnd_web.py`
- **禁改：** `state.py`（本条如需改 `state.py`，说明理由并回退到 M7-1/2 的裁决），其余同 M7-3
- **验收：** `?since=` 返回的条目严格递增且不重不漏；接口不触发检查点（磁盘 mtime 不变）；
  无密钥 / 无本机路径出现在响应；`bash tests/run_all.sh` 通过。

> 若 Master 只想开三条：**合并 M7-4 进 M7-3**，但 M7-3 的验收要显式加上「只读接口不落盘」这一条。

---

## 附录 A·参考

- `GDD.html`：§5.1（状态层定位）、§5.2（系统协作）、§6.2–6.9（时间 / 行动状态 / 幂等 / 重启恢复 /
  客户端时钟不权威）、§8.3–8.5（风险分级 / 结果契约 / 中断）、§12.1–12.4（事实信念分离 / 秘密 / 访问控制）、
  §18.4（实时通信与重连恢复）、§19.1–19.5（数据域 / 事件与快照 / 幂等并发事务 / 存档兼容 / 事件最小字段）、
  §20.4（故障降级）、§24.1（不变量）、§24.2（测试矩阵）、§25.1（已锁定决策）。
- `GDD-BASELINE.md`：§4.1（`state.py` 落点）、§4.2（依赖方向 / 唯一提交）、§4.3（四件核心契约）、
  §5（M7 出口条件）、§6（通用验收：回归 / 防火墙 / 离线底线 / 不变量 / 可序列化 / 迁移 / 文档同步）、
  §7.1（本设计单）、§8（R2 重复提交 / R4 迁移破坏旧档）。
- `notdnd_web.py`（`master @ fa5366c`）：`Session`（L372）、`_SESSION_DEFAULTS`（L218）、
  `to_dict`（L408）、`save`（L431，`os.replace` 原子写）、`load`（L448，惰性迁移 + `SID_RE` 边界）、
  `add_log`（L502）、`ensure_atlas`（L525）、`_restore_rules`（L348）。
- `prism_core.py`：`RuleSession`（L414）、`snapshot`（L454）、`from_snapshot`（L475）、`dirty` 标记。
- `atlas.py`：`ATLAS_STATE_VERSION`（L63）、`export_state`（L445）、`restore_state`（L497）、`apply_delta`。
- `prism_guide.py`：`empty_guide`（L689）、`guide_from`（L717）、`bind`（L788，`session.save()` 落盘口径）。
- `data/FORMAT.md`：封套 `{schema_version, kind, 载荷}`、`kind` 与 `registry.json`、JSON Schema 关键字子集。
- `AGENTS.md`：三条铁律（内容边界 / 依赖 / 密钥）、Issue 循环、同一文件不并行、`docs/` 单独成 PR。
- `MASTER.md`：§3（共享串行文件清单）、§4（路线图指向 `GDD-BASELINE.md`）。

---

## 附录 B·Key Decisions

1. **存储 = 每战役一个目录的 JSON 文件族**（检查点 + 追加式 NDJSON 事件日志 + 幂等索引）；不用 SQLite，
   但写下改用 SQLite 的四条触发条件（4.2）。
2. **事实与展示分家**：事件日志（权威）与叙事流 `stream`（可丢）两条独立序列，序号互不赋值。
3. **唯一提交出口**：业务模块只交「不透明新块 + 声明式 `changes`」，`state.py` 只排号 / 留痕 / 落盘 / 重放 / 迁移。
4. **`state.py` 零业务依赖**：不 import `prism_core` / `atlas` / `prism_guide`，机械守卫写进测试。
5. **原子提交 = 追加 + fsync（日志）与 临时写 + `os.replace`（检查点）**；「提交已发生」只由日志判定。
6. **幂等键由提交方提供**（`sha256(campaign_id, actor, action_id, client_token)`），状态层只查表与判重；
   同键不同载荷 → 冲突；同键同载荷 → 重放。
7. **版本分层且拒载高版本**：`STATE_VERSION` / `EVENT_VERSION` / 块内版本各自管理，读到更高版本不降级。
8. **`world_time` 不透明**，语义归 M9；`campaign_id` 与 `sid` 分离，M7 相等、M10 解耦。
9. **惰性迁移**，保留 `.bak`，老档的展示流无损搬进 `stream`，权威日志自迁移点起算（不伪造历史）。
10. **地图不与事件日志双写**：地图重放仍以 `atlas` 块内的 `deltas` 为唯一权威路径。

---

*本文件为设计文档，修订走独立 PR（只改 `STATE-DESIGN.md`）。*
