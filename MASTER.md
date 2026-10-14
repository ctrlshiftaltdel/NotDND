# MASTER.md —— Master Agent 接手与运行手册

> 本文件是 **Master Agent** 的入口。**接手只需一句话**：
> **「你是 Master Agent，接手 NotDND 项目继续工作。」**
>
> 接手后依次读：**本文件** → `AGENTS.md`（协作契约）→ **GitHub 的 Issues / PRs**（唯一事实来源）。
> 不依赖任何聊天历史。

---

## 0. 30 秒接手步骤

```bash
cd ~/Projects/NotDND
git fetch --prune && git pull --ff-only
gh issue list --state open
gh pr list --state open
```

（本机 `.env` 已配置时，AI 导引者 / TTS 可真调；密钥只存运行时——**永不回显、永不入库**。）

1. 读 `AGENTS.md`（角色、三条铁律、Issue 循环、四行审查、合并约定）与
   [`AGENT-ORCHESTRATION.md`](AGENT-ORCHESTRATION.md)（编排状态机 / 闸门 / 能力探测 / 降级 / 汇报）。
2. **从 GitHub 重建全局状态**（见 §3.1）：读本文件 §2 当前状态 + §4 路线，但**以 GitHub 为准**核对
   开放 Issues / PRs 与当前 `master`；**不依赖任何聊天记忆**，也**不假设上一轮会话仍然存活**。
3. **选择运行模式**（见 §3.2）：用户明确指定时照做；未指定时先探测运行时能力 C1–C4 定默认
   （具备则 `single-session`，缺失则降级为 `multi-session`），并在开工确认中**显式声明**。
4. **有开放 PR**（人只说「**读 PR**」时也是此意）→ 按 §3 审查；**已审通过且 CI 全绿就合并**；有问题写说明或开 Issue。
   **复核范围**（2026-10-10 人定，**保留**）：只处理**待审 PR 的分支**——不扫描、不预读尚未开 PR 的远端新分支；
   新分支以「PR 开出」为进入视野的信号。
5. **无开放 PR** → 看 §4 下一切片是否已开 Issue；未开则开（**数据切片串行**）。
6. **进入自动调度循环**（见 §3.3）：按依赖图与文件占用选可执行 Issue，能并行就并行、能推进就推进；
   **合并只在当前 head 的 CI 与全部闸门通过且已独立审查后执行**，不通过就停下 / 退回。
7. **每 1–2 个阶段**做一次 §6 全面检查；**阶段收口**按 §3.5 引用 `AGENT-ORCHESTRATION.md` 的出口条件判定并汇报。
8. **暂停 / 阻塞**：命中 §3.4 任一暂停条件 → 进入**可恢复的阻塞**（`BLOCKED` / `NEEDS_HUMAN_DECISION`）并汇报，
   **不得**把阻塞汇报成阶段完成；**未派单的实现动作不做**——Master **不做实现**（见 §3）。

---

## 1. 项目一句话

**NotDND**：完全原创、MIT、**天生公开**的 AI 跑团网页应用。规则「**棱镜 PRISM**」，
AI 任「**导引者**」，**纯文本运行 + 视觉占位框架**（D2：为视觉要素预留占位符与替换框架，
当前不引入图像管线、不新增图片资产）。
仓库：<https://github.com/ctrlshiftaltdel/NotDND>（public）。

- **依赖现状**：Python 3 标准库 + 原生 HTML/CSS/JS（无框架、无构建）。
- **依赖政策**（D3，2026-10-10 人拍板）：**技术选型效率优先**——以最好、最高效实现为准，
  **不设依赖上限**；引入依赖时在 PR 说明理由、权衡与替代方案。
- `AGENTS.md` 铁律 2 的政策口径已按 D3 同步生效（治理 `PR #156（Issue #144）` 已合并）。

---

## 2. 当前状态快照（截至 2026-10-14）

> ⚠️ 快照会过时——**以 GitHub 为准**。每次里程碑/阶段收口后更新本表（§6 第 4 项）。

- **分支保护**：ruleset「master」为 `active`——须经 PR + **四道 required checks** + 禁 force push / 禁删除。
- **CI 四道门**（`.github/workflows/ci.yml`）：语法门 / 回归门 / 内容防火墙门 / **提交邮箱门**。
- **安全**：全历史 **0 真实邮箱**（2026-10-09 已重写历史 + 账号开启「keep email private」）；无密钥、无本机路径。
- **数据层**：`python3 tests/validate_data.py` → **79 个文件**（`data/system/**` + `data/worlds/**`（三册）+ `data/random_tables/**` + `data/scenarios/**`（15 册）+ `data/atlas/lexicon.json` + schema/registry）。
- **回归**：`bash tests/run_all.sh` → **15 个脚本**（`test_atlas_compile` / `test_atlas_gen` / `test_atlas_kernel` / `test_atlas_ledger_pins` / `test_chron` / `test_commit_email_gate` / `test_content_firewall` / `test_data` / `test_notdnd_web` / `test_prism_core` / `test_prism_guide` / `test_repo_layout` / `test_state` / **`test_ui_world_themes`** / `test_ui_smoke`）。
- **里程碑**：**M0–M6 主体 ✅（已完成历史，见 §4）**；**M6a ✅（Issue #123 / PR #127）· M6b ✅（Issue #124 / PR #128）**——M6 收口。官方路线自 **M7** 起见 `GDD-BASELINE.md` §5。
- **M7 起实现切片（已合并）**：
  **M7** 状态层——M7-1 内核 ✅（Issue #163 / PR #214）· M7-2 快照与重放 ✅（Issue #267 / PR #272）· M7-3 存档接线与旧档迁移 ✅（Issue #276 / PR #281）· M7-4 只读状态视图 ✅（Issue #312 / PR #318）——**M7-1…M7-4 全清**；
  **M8** 行动管线——A1 提案 / 结果契约 ✅（Issue #202 / PR #213）· A2 NL→提案 ✅（Issue #268 / PR #271）· A3 管线编排与写接口 ✅（Issue #282 / PR #290）· A5 排程钩子接入 CHRON ✅（Issue #313 / PR #319）——**A1…A5 全清**；
  **M9** CHRON——C1 内核 ✅（Issue #165 / PR #176）· C2 生命周期状态机 ✅（Issue #269 / PR #270）· C3 调度器 ✅（Issue #277 / PR #280）· C4 事件屏障与时间压缩 ✅（Issue #283 / PR #289）· C5 持久化与恢复接线 ✅（Issue #292 / PR #297）· C6 接 M8 排程钩子与网页层 ✅（Issue #313 / PR #319，含修复轮 1）——**C1…C6 全清**；
  **M10** 多人——S1 房间与成员骨架 ✅（Issue #166 / PR #188）· S2 事件流与断线重连 ✅（Issue #314 / PR #320）——余 S3–S6。
- **M4 前端切片（并行轨）**：U0 ✅（Issue #164 / PR #177）· U1 ✅（Issue #206 / PR #233）· U2 主循环接线 ✅（Issue #266 / PR #273）· U3 ✅（Issue #204 / PR #215）· U4 ✅（Issue #201 / PR #221）· U5 对白拍与语音 ✅（Issue #278 / PR #279）· U6 视觉占位框架 ✅（Issue #284 / PR #288）· A4 行动管线前端接线 ✅（Issue #291 / PR #296）· B1 ✅（Issue #203 / PR #219）· B2 ✅（Issue #205 / PR #224）——**M4 已开切片全部合并**；战役入口 ✅（Issue #222 / PR #311）。
- **M4 世界主题切片（并行轨，`UI-WORLD-THEMES.md`）**：W3 语义不变式测试 ✅（Issue #315 / PR #317）；余 W0（已拍板待派发）/ W2 / W4。
- **世界主题规范 ✅**（Issue #274 / PR #295）：`UI-WORLD-THEMES.md` + 可离线评审原型 `docs/prototypes/ui-world-themes.html`（奇幻 / 现代 / 未来三主题；`world_type` 契约见 §5）。
- **本轮实现推进（wave 1–4，2026-10-12；快照基线 `master` = `b04fac3`）**：13 切片全部 **MERGED**——M4-U2 / U5 / U6、M7-2 / M7-3、M8-A2 / A3、M9-C2 / C3 / C4、M4-A4、世界主题规范、M9-C5（逐片 PR 见上方各条）；
  wave 1–3（10 切片）收口报告见 **Issue #294**，wave 4 = M4-A4 / 世界主题规范 / M9-C5；快照基线复验：`run_all` 14/14、内容防火墙 0 命中、`validate_data` 79 文件。
  同一窗口治理 / 编排改进亦已合并：写动作在途守卫（Issue #218 / PR #241）、自主派发口径（Issue #242 / PR #247）、运行度量基线（Issue #246 / PR #248）、活动任务登记（Issue #244 / PR #249）、合并与清理分离（Issue #245 / PR #250）、可控失败与三轮上限演练（Issue #243 / PR #255）、清理待办跨会话发现（Issue #257 / PR #260）。
- **本轮实现推进（wave 5，2026-10-14；快照基线 `master` = `7289d7a`）**：5 切片全部 **MERGED**——M4 战役入口（Issue #222 / PR #311）、M4-W3 世界主题语义不变式（Issue #315 / PR #317）、M7-4 只读状态视图（Issue #312 / PR #318）、M9-C6 + M8-A5 排程钩子接线（Issue #313 / PR #319，含修复轮 1）、M10-S2 事件流与断线重连（Issue #314 / PR #320）；
  wave 5 收口报告见 **Issue #294**（§6 结论：本阶段完成，E1–E5 全满足）；快照基线复验：`run_all` 15/15、内容防火墙 0 命中、`validate_data` 79 文件、全历史 0 真实邮箱。
  **切片口径**：**M7（M7-1…M7-4）/ M8（A1…A5）/ M9（C1…C6）切片全清**；M10 余 S3–S6；UI 轨余 W0（已拍板待派发）/ W2 / W4。
- **GDD 基线**（PR #135）：`GDD.html`（v1.1 原件）与 `GDD-BASELINE.md` 入库；Markdown 版 `GDD.md`（Issue #139 / PR #150）。
- **角色分工**（Issue #141 / PR #142，2026-10-10 人定）：Master Agent 只把控方向 / 拆单派发 / 审批，**不做实现**（不编辑 / 不 commit / 不 push / 不开 PR）；Execution Agent 严格按 Issue 执行、**一律不合并**（细则见 `AGENTS.md` §3）。
- **设计单（六份全部入库）**：M7-a `STATE-DESIGN.md`（#136 / PR #152）· M8-a `ACTION-DESIGN.md`（#137 / PR #149）· M9-a `CHRON-DESIGN.md`（#138 / PR #151）· M10-a `MULTIPLAYER-DESIGN.md`（#143 / PR #146）· M4 正式 UI `UI-DESIGN.md`（#133 / PR #147，**含 D2 视觉占位框架要求**）。
- **已关单**：**#110**（要点节点 cites 自引专名，PR #148）；**#140**（2026-10-10 角色边界复盘产物，按人决定「不重做、不审阅」关闭）——其内容重开为 **#144**（D2/D3 治理口径同步）与 **#145**（即为本快照单）。
- **在办 / 待办**：**以 GitHub 为准**（`gh issue list --state open` / `gh pr list --state open`）——本快照不逐条维护开放清单，避免下次漂移。当前开放 **7 条**：
  **#316**（M4-W0 `world_type` 契约，**已拍板待派发**）· **#321**（M10-S3，**已派发在办**）· **#322 / #323**（M10-S4 / S5，待派发）· **#275**（**已收窄**为纯 README 精简，**已派发在办**）· **#294**（阶段跟踪）· **#324**（本快照单）；
  **wave 6 已开始（2026-10-14）**：**#321**（M10-S3）**已派发在办** · **#275**（README 精简）**已派发在办** · **#324**（本快照）；**#322 → #323 排队**（等前者合并）· **#316 排 S3–S5 后**；
  `notdnd_web.py` 独占队列**串行**（S3 / S4 / S5 共享该队列，开工前复查锁，见 #294 §6）；待人再议项（S6 / W4 / `--scope-*` 登记处）与阻塞项登记见 §5。

---

## 3. Master Agent 的例行循环与自主编排

> 完整规范见 `AGENTS.md` 第 2–6 节；编排机制（状态机 / 闸门 / 能力探测 / 降级 / 汇报）见
> [`AGENT-ORCHESTRATION.md`](AGENT-ORCHESTRATION.md)。此处只列要点，**不复制**状态机。

- **收需求为 Issue**：一个 Issue 只做一件事，写清 **目标 / 验收标准 / 可改路径 / 禁改路径 / 依赖的 PR**；
  并打上**难度**与**主要能力**标签（见下），供**人指定**、或 **Master Agent 依 `AGENTS.md` §3 授权自主调度**
  Execution Agent（**人的指派权不变**）。
- **审查开放 PR**：对照验收标准**逐条**核对；看 CI 是否全绿；看是否只改了允许路径；
  有没有新依赖 / 密钥模样内容。在 PR 评论里留**四行建议**（建议 / 用户能感知的变化 / 没完成的验收项 / 需人拍板的风险）。
  **汇报与审查都写明 PR ↔ Issue 对应**：PR 标题用 `…（Issue #N）`（如 `feat(chron): 世界时钟纯函数（Issue #165）`）；
  评论 / 状态汇报 / 待办清单 / 复盘中提及 PR 一律写 `PR #N（Issue #M）`（反向 `Issue #M（PR #N）`）；
  多 Issue 列全，无关联写「无关联 Issue」及原因。历史出处索引（§2/§4/§5 既有 PR 记录）保留原编号、
  不作为格式示例（细则见 `AGENTS.md` §4、§6、§7）。
  复核前先看 `mergeable`：`CONFLICTING` → 请作者按 `AGENTS.md` §7「交回前 rebase」更新后再复核
  （**Master Agent 不代解冲突、不推实现分支**；CI 以 rebase 后为准）。
- **合并**：写「建议合」且 **CI 全绿** → **Master Agent 直接合并并删远端分支**（不再逐件请示人）。
  预审若**无需修改**，Master Agent **直接 `gh pr ready` 代转并合并**，不来回等作者转 Ready（人 2026-10-09 明确）；
  但**作者仍在推进的 Draft 先别抢**，等其推完再走流程；**未开 PR 的远端新分支不预读、不跟踪**（2026-10-10 人定）。
  有未完成项 / 问题 → 写四行说明或**开 Issue 跟踪**。
- **权限边界**（2026-10-10 人定，细则见 `AGENTS.md` §3）：Master Agent **不做实现**——
  不编辑文件、不 commit / push、不开 PR；职责只到「把控方向 / 拆单派发 / 审批」。
  **重写历史 / 动保护规则 / 改仓库可见性等大动作先问人**；**角色边界之外的动作，
  除非用户明确授权，否则不做**。
- **串并行的硬约束**：
  - **数据切片彼此串行**——`data/system/**`、`data/worlds/**`、`data/schema/registry.json`、`data/FORMAT.md` 是共享文件。
  - 不同文件 / 不同目录（如 `tests/**` 与 `data/**`）**可并行**。
- **尽量并行开单**：能并行就并行——只要**不与在办 PR 撞同一文件**，就同时开出多条 Issue 节约总工时
  （常规每轮 **2–4 条**）；**数据切片彼此串行**仍是硬约束。
- **Issue 标注（开单必填）**：每条 Issue 打两类 GitHub 标签：
  - **难度**（三档，单选）：`难度：低` / `难度：中` / `难度：高`。
  - **主要能力**（多选，1–3 个）：`能力：编程` / `能力：逻辑` / `能力：文学` / `能力：数据` /
    `能力：测试` / `能力：前端` / `能力：设计` / `能力：文档`。
  - **UI 设计注明**：涉及**界面 / 交互 / 视觉设计**的 Issue **必须**带 `能力：设计` 标签，并在正文首行写「含 UI 设计」——供人指派给有专属设计技能的 Agent。
- **Execution Agent 一律不合并**（含自主推进期）；合并只由 Master Agent 执行。

### 3.1 接手与现场恢复（状态一律从 GitHub 重建）

- 每次接手先 `git fetch --prune`，再读 **GitHub 的开放 Issues / PRs** 与**当前 `master`**，
  重建全局状态——**不依赖任何聊天记忆**，也**不假设上一轮某个会话仍然存活**。
- 从 Issue 正文 + PR 状态 / 评论重建每个任务的编排状态、审查轮次与**文件锁表**
  （开放 PR 触及的文件集合），并识别：**当前阶段 / 已完成项 / 阻塞项 / 可派发任务**。
- 现场标签（对应 `AGENT-ORCHESTRATION.md` §2）：**正执行**（`DISPATCHED` / `PR_OPEN`）、
  **等依赖**（`PLANNED` / `READY` 但依赖未合入 `master`）、**待审查**（`MASTER_REVIEW`）。
- 恢复流程与「需要恢复的信息 → 从何处恢复」映射见 `AGENT-ORCHESTRATION.md` §3.3；
  **禁止**把「某个会话说过什么」当作状态来源，只认 Issue / PR / 仓库文件。

### 3.2 运行模式选择（`multi-session` / `single-session`）

两种模式共用同一套编排规范（`AGENT-ORCHESTRATION.md`），区别只在**执行实例如何承载**：

| 模式 | 入口（可直接复制的运行指令） | 用途 | 运行时能力要求 | 降级行为 |
|---|---|---|---|---|
| **`single-session`**（单会话自动编排） | [`prompts/master-single-session.md`](prompts/master-single-session.md) | Master 在**同一会话**内 spawn / 等待 / 取回 Execution Subagent，循环推进直到阶段完成或阻塞 | **必须**先探测具备 `AGENT-ORCHESTRATION.md` §5.2 的 **C1–C4**（spawn / 隔离上下文 / 等待取回 / 结果区分） | 任一缺失 → **显式声明**并**降级为多会话交接**；**严禁**虚构派发 / 等待 / 结果 |
| **`multi-session`**（多会话交接） | [`prompts/master-multi-session.md`](prompts/master-multi-session.md) | 产出**自包含交接包**，由**人**在另一会话启动 Execution Agent；Master **不等待**，状态靠 GitHub 重建 | 无特殊要求（不依赖 spawn / wait 能力） | 本模式即 `AGENT-ORCHESTRATION.md` §5.4 的降级形态 |

- **用户明确指定模式时照做**；**未指定时的默认策略**：先探测 C1–C4——**全部具备**则默认
  `single-session`，**任一缺失**则默认 `multi-session`（即降级为多会话交接），并在开工确认中**显式声明**。
- 两个模式**不得在同一会话里含糊地同时承诺**；启动时写明本次模式。
- 开工确认 / 汇报 / 派发任务包的格式见上述两份提示词；**单任务执行**的运行指令见
  [`prompts/execution-subagent.md`](prompts/execution-subagent.md)（可选第二意见见 `prompts/reviewer-subagent.md`）。

### 3.3 自动调度循环（依赖图 + 文件占用）

按 `AGENT-ORCHESTRATION.md` §2 状态机推进（此处**不复制**状态机），循环如下：

1. **选任务**：从 `READY` 中按**依赖图**与**文件占用**（§3.1 锁表）挑出**可执行** Issue——
   依赖已**合入 `master`**（而非「已开 PR」），且其可改路径**不与任何开放 PR / 活动任务相交**。
2. **并行分派**：只为**互不冲突**（不同文件、无依赖）的任务并行分派；同一文件的多个任务**串行**
   （隐式串行依赖）。派发前必须做冲突检查（`gh pr list --state open` + 比对路径）。
3. **等待结果**：`single-session` 用 C3 / C4 等待并取回**状态 + 产物 + 证据**，区分成功 / 失败 / 取消；
   `multi-session` **不等待**，靠 GitHub 重建状态。
4. **读 GitHub 状态 → 独立审查**：对到达 `MASTER_REVIEW` 的 PR **自行审查**（**不采信自报**），
   逐条对照验收标准、允许路径、基线、**当前 head 的 CI** 与证据（即闸门 G1–G6）。
5. **退回修复 → 重新验证**：有缺陷 → 退回 `DISPATCHED`（修复轮次 +1，**默认上限 3 轮**），
   修复后**在当前 head 上重跑 CI 与回归**再重新审查。
6. **合并**：`APPROVED` 且 head 未变 → 由 **Master Agent** 执行合并并删除远端分支。
   **自动合并只在「当前 head 的 CI 四门全绿且全部闸门 G1–G6 通过、并已独立审查」后执行**；
   **不通过就停下 / 退回**，不得为「跑通」而降低验收标准或伪造证据。
7. **合并后核验（`MERGED → VERIFIED`）**：在合并后的 `master` 上重跑约定验证并记录证据。
8. **释放占用**：PR 合并 / 关闭后其文件占用**自动释放**；回到第 1 步**继续下一项**。

### 3.4 暂停条件（进入**可恢复的阻塞**，不得汇报成阶段完成）

出现下列任一情形，**立即停下**并登记为**可恢复的阻塞**状态，**不得**继续推进，也**不得**把它汇报成阶段完成：

- **平台不具备所需 Subagent 能力**（C1–C4 缺失）→ 按 §3.2 降级为多会话交接；连降级也不可行 → `BLOCKED`。
- **达到修复轮次上限**（默认 3 轮仍未通过）→ `BLOCKED`。
- **需求 / 规则冲突**（Issue 语义不清、依赖成环、与三条铁律或 `docs/` 规则冲突）→ `NEEDS_HUMAN_DECISION`。
- **破坏性风险**（重写历史、改仓库保护规则 / 可见性、删除数据等大动作）→ `NEEDS_HUMAN_DECISION`，**先问人**。
- **CI 或分支保护异常**（CI 红、`CONFLICTING`、保护规则被绕过或异常）→ `BLOCKED`，**不得强行合并**。
- **需要人类决策**（新依赖 / 迁移 / 权限 / 环境变量 / 公开行为变化，或无法自行取舍）→ `NEEDS_HUMAN_DECISION`。

状态名、终态语义与恢复条件以 `AGENT-ORCHESTRATION.md` §2.2 为准；`BLOCKED` / `NEEDS_HUMAN_DECISION` / `FAILED`
**都是终态（除非满足各自恢复条件）**，必须在汇报里**显式列出**，**不许静默搁置**。

### 3.5 阶段收口与汇报

- **阶段完成判定**：`AGENT-ORCHESTRATION.md` §6.1 的**出口条件 E1–E5 全部满足**才算阶段完成
  （阶段必需项均 `VERIFIED` / 依赖与 CI 满足要求 / 无未决人类决策 / 无未消解阻塞 / 下一阶段已登记）；
  **不因「时间到了」或「大部分做完」而放宽**，**不得**把阻塞汇报成阶段完成。
- **汇报最小格式**（`AGENT-ORCHESTRATION.md` §6.2 六项）：本阶段范围与状态；**已合并事项**
  （逐条 `PR #N（Issue #M）`，含**合并 SHA**）；仍阻塞事项（状态 / 原因 / 恢复条件）；**验证证据**
  （真跑过的命令 + 结果）；**未解决风险**（新依赖 / 迁移 / 权限 / 环境变量 / 公开行为变化）；
  **下一阶段 / 人类需要处理的动作**（明确「谁、做什么」，以及是否已降级为多会话交接）。
- 引用 PR 一律 `PR #N（Issue #M）`；**没有 PR 时写明当前状态，不得编造编号**。

### 常用命令

```bash
gh pr list --state open --json number,title,isDraft,headRefName
gh pr checks <n>
gh pr comment <n> --body-file review.md            # 四行审查
gh pr merge <n> --merge --delete-branch            # 已审通过 + CI 绿
gh pr ready <n>                                    # Draft → 可审查（如 Execution Agent 忘转）
gh issue create --title "…" --body-file issue.md
gh issue list --state open

bash tests/run_all.sh
python3 tests/validate_data.py
bash tests/content_firewall.sh
```

---

## 4. 路线图（M0–M6 已完成历史；M7 起见 `GDD-BASELINE.md`）

> **下表 M0–M6 为「已完成历史」**，保留作回顾与出处索引。**M3b（写接口）已并入 M8**。
>
> **自 M7 起的官方路线以 [`GDD-BASELINE.md`](GDD-BASELINE.md) §5 为准**：
> **M7** 权威状态与事件层 → **M8** 行动管线（含写接口）→ **M9** CHRON 时间与调度 →
> **M10** 多人基础（局域网 2–6 人）→ **M11** NEURA 活体世界 → **M12** 世界生成与经济 →
> **M13** 平台化；**并行轨 · UI 与视觉占位**（`UI-DESIGN.md` 设计已入库 → 实现切片，D2 占位框架）。
> 每个里程碑的出口条件、切片拆单建议与验收要求见基线 §5–§7。

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0** | 仓库与协作基础设施（AGENTS/License/CI 三门/模板/gitignore） | ✅ |
| **M1** | 数据层：PRISM → JSON | ✅（M1.0–M1.4 全部完成） |
| 　M1.0 | 数据格式与校验基线（FORMAT + JSON Schema + 校验器） | ✅ |
| 　M1.1 | 系统规则数据（`data/system/`） | ✅ a 内核 / b 战术 / c 构建 / d 互动 / e 导引者 |
| 　M1.2 | 世界模组数据（`data/worlds/`） | a 阈界都市 ✅ / b 余烬纪元 ✅ / c 熵网 ✅ |
| 　M1.3 | 随机表（`data/random_tables/`：system 08 + S7 70 张；各模组表已在 `data/worlds/*`） | a 通用生成器（docs/system/08）✅ / b S7 70 张（docs/scenario/S7）✅ |
| 　M1.4 | 剧本（`data/scenarios/`：09/S8 + S1–S6 结构件 + 账本模板） | a S1–S6 结构件 ✅（PR #58）/ b 09/S8 战役 ✅（PR #74）——**完成** |
| **M2** | 规则核心（离线可玩） | a 判定 / 结果 / 代价 / 资源 ✅（PR #57）/ b 战斗 / 派生 / 成长 ✅（PR #70）/ c 构建引擎 ✅（PR #76）/ **d 构建引擎收尾 ✅（PR #130）——M2 完成**（02B 第六至十一节：战斗 / 超载连锁 / 符纹插槽 / 构建点 / 符纹库 / 互转） |
| **M3** | 会话与存档 + 后端 API | a 规则快照接线 ✅（PR #56）/ **b 写接口 → 已并入 M8**（通用行动提案写接口） |
| **M4** | 前端 UI（`static/`） | 骨架 ✅（PR #118）；正式 UI **设计 ✅**（`UI-DESIGN.md`，Issue #133 / PR #147，含 D2 视觉占位框架）——实现切片待开 |
| **M5** | AI 导引者（可选模块，import 失败不影响离线）。方案见 `GUIDE-DESIGN.md` | **G0–G7 ✅**（PR #83 / #92 / #96 / #99 / #101 / #106 / #108 / #119）；修复 #109 ✅（PR #112）；**时间盒修订 ✅（PR #126）**；收尾 **#110 ✅（PR #148，要点节点 cites 自引专名）——M5 收口** |
| **M6** | 测试与文档收口（README + HANDOVER + run_all 全量） | **#123 ✅（PR #127）· #124 ✅（PR #128）——M6 收口** |

> **ATLAS（自动地图）切片**：`ATLAS-DESIGN.md` §8 的六条 Issue **I1–I6** 与 M2–M4 交叉
> （内核 → 词表编译 → 地点/战术生成 → {会话接线 / 规则投影 / 账本 place id}）；
> 依赖顺序 **I1 → I2 → I3 → {I4 / I5 / I6}**。**进度**：**六片全部落地**——I1–I3 ✅（PR #75 / #79 / #81）、
> I4 ✅（PR #95）、I5 ✅（PR #93）、I6 ✅（PR #80）、I2 后续修复 ✅（PR #91）。
> **硬约束（历史）**：I2 独占 `data/schema/registry.json` + `data/FORMAT.md` 的时段随 #79 合并结束；
> I5 的前置 #59、I6 的前置 #61 均已合并。
>
> **导引者（GUIDE）切片**：`GUIDE-DESIGN.md` PR Plan 的八条 **G0–G7**（管道 G1–G4 → 绑定 / 实相 / 对白 G5–G7）。
> 依赖链 **G0 → G1 → G2 → {G3 / G4 / G5} → G6 → G7**；**同文件串行**：G3↔G4↔G5 与 G6↔G7 都占 `prism_guide.py`；
> G4 的前置 #67 已合并；G5 另占 `notdnd_web.py`，开工前确认无并行 PR。
> **进度**：**G0–G7 ✅**（PR #83 / #92 / #96 / #99 / #101 / #106 / #108 / **#119**）；**修复 #109 ✅（PR #112：非流式整段 JSON + 共享时间盒）**；收尾 **#110 ✅（PR #148）**；时间盒修订 ✅（PR #126，40→75）。

---

## 5. 关键约定与决策记录

> 全部为「已确认」；改动走新 Issue。详见 `AGENTS.md` §10。

- **三条铁律**：① 内容边界（禁第三方版权内容照抄/翻译/改名派生；**允许**借鉴通用机制与题材设定）；② 依赖政策——`AGENTS.md` 铁律 2 为「技术选型效率优先」（**不设依赖上限**；引入依赖在 PR 说明理由、权衡与替代方案；D3 口径已生效，见 §1 与下条 GDD 基线）；③ 密钥与隐私永不入库。
- **六维命名**：产品层统一 PRISM 名 **力道 MGT / 灵巧 FIN / 体魄 VIG / 洞察 INS / 心智 MND / 气场 PRE**；D&D 通用名仅作同义词。
- **`docs/` 可修订**：属内容变更，**单独开 Issue/PR**、不夹带在实现 PR。
- **并行粒度按文件**：同一文件有开放 PR 时不并行。
- **数据规范**（`data/FORMAT.md`）：封套 `{schema_version, kind, 载荷}`；`kind = <family>.<name>`，**每个 kind 一份平铺 schema + `registry.json` 登记**；每个实体带 `source:"docs/…:行号"` 溯源；散文不进 JSON。
- **Master Agent 自主运行**（2026-10-09 授权）：能合就合、有问题写说明或开 Issue、能推进就推进、能并行就并行，不逐件请示（**除非无法决定**）。
- **命名与越权**（2026-10-10 人定）：角色名统一为 **Master Agent** / **Execution Agent**（不再使用「执行 Agent」这类中英文混用名）；**越权动作除非用户明确授权，否则不做**；**涉及 UI 设计的 Issue 必须注明**（`能力：设计` 标签 + 正文首行「含 UI 设计」）。
- **GDD 基线**（2026-10-10 人拍板，PR #135）：`GDD.html`（v1.1 原件）与翻译层
  `GDD-BASELINE.md` 入库；**自 M7 起路线以基线 §5 为准**（M0–M6 为已完成历史）。
  四条已拍板决策（不再重开）：**D1 多人为一等目标**（状态/事件层 → 行动管线 → CHRON → 局域网 2–6 人 → NEURA；
  README「勿暴露到公网」警告暂不解除，认证属 M13）；**D2 视觉占位先行**（为视觉要素预留占位符与替换框架，
  当前不引入图像管线、不新增图片资产）；**D3 技术选型效率优先**（不设依赖上限，引入依赖在 PR 说明理由、权衡与替代）；
  **D4 交付形态**（`GDD.html` 原样入库，Markdown 拆分另立任务——`GDD.md` 已由 #139 / PR #150 落地）。
- **角色分工明确化**（2026-10-10 人定，Issue #141 / PR #142，细则见 `AGENTS.md` §3）：
  **Master Agent = 把控方向 / 阶段规划 / 拆单派发 / 审批，不做实现**（不编辑文件、不 commit / push、不开 PR）；
  **Execution Agent = 严格按 Issue 完成，绝不做未要求的任务或改动**，超范围先停下报告；
  **Execution Agent 一律不合并**，合并只由 Master Agent 在人同意后执行；文档中「由 Master Agent 执行 / 落地 / 维护」
  字样**不构成编辑授权**（旧口径已作废）。
- **工程骨架补全**（2026-10-10）：前端 `static/` 三视图骨架（PR #118）与测试基座
  （`tests/_cdp.py` / `cleanup_servers.sh` / UI 冒烟 / `tests/README.md`，PR #117）已落地；
  `run_all` 现有 **14 个脚本**。
- **M6 文档骨架（归档约定）**：`README.md` = 跑起来 → 结构取舍 → 测试 → 已知边界（含
  「别暴露到公网」警告）；`HANDOVER.md` = 每节答「为什么 + 踩过的坑」；测试约定见 `tests/README.md`。
- **阶段收口**（2026-10-10）：**M1.4 剧本 / M2（a–d）/ M3a / ATLAS I1–I6（全部）/ 导引者 G0–G7 已完成**
  （PR #58 / #74 / #77 / #57 / #70 / #76 / #56 / #75 / #79 / #81 / #95 / #93 / #80 / #91 / #83 / #92 / #96 / #99 / #101 / #106 / #108 / #112 / **#119** / #126 / #130）；
  在办与待办：**M6b（#124）已于 PR #128 合并**、**#110 已关（PR #148）**、**M4 正式 UI 设计（#133）已入库（PR #147）**；
  当前在办与待办以 §2 快照为准。
- **自动地图（ATLAS）**（2026-10-09 拍板）：跨 M2–M4 的**方向性设计**，方案入库 `ATLAS-DESIGN.md`
  （见 §7 文档地图）；拆六条切片 **I1–I6**（依赖见 §4 注）。**影响面**：新增 `atlas.py` /
  `atlas_compile.py` / `atlas_gen.py` 与 `data/atlas/lexicon.json`（新 kind）；I4 给会话加快照块 `atlas`；
  I5 接 `prism_core.py`；I6 把剧本账本地点钉到 `place_id`。**不改**既有 `docs/` 规则。
- **导引者（GUIDE）**（2026-10-09 人定稿）：M5 的方向性设计，方案入库 `GUIDE-DESIGN.md`（PR #83）。
  **三件用途**：把地点卡走成可印证的街道图（实相，每「存档 × 地点」只生成一次并落盘）、演已有人物
  （场外痕迹 / 对白 / 秘密门）、同世界同剧本**每局不同**（`guide.salt`；战役节点骨架不换）。
  拆八条 **G0–G7**（依赖见 §4 注）；新增 `prism_guide.py`（可选模块，import 失败不影响离线）；
  **不改** `prism_core.py`（G5–G7）与 `docs/`；实相**不写坐标**——地图仍归 ATLAS。
  **揭开口径**（2026-10-10 裁定）：痕迹 `at` = **离开的地点**；揭开在**同一回合**的叙事（`narration.trace`，服务端写；§2.2 笔误已校正）。
- **文件命名**（2026-10-09 人拍板）：仓库文件名一律**纯英文 + 数字**（ASCII；推荐小写 + `-`），
  不新增非 ASCII 文件名。既有 `docs/` 的 20 个中文文件名**已迁移完毕**（PR #104，Issue #102；
  全库非 ASCII 文件名 = 0）。
- **PR ↔ Issue 强制显式对应**（2026-10-11 人定，Issue #190）：PR 标题用 `<type>(<scope>): <摘要>（Issue #N）`
  （如 `feat(chron): 世界时钟纯函数（Issue #165）`，**不得**只写 `（#N）`；正文保留 `Closes #N`）；
  评论 / 汇报 / 待办 / 复盘中提及 PR 一律写 `PR #N（Issue #M）`（反向 `Issue #M（PR #N）`）；
  多 Issue 列全，无关联写明「无关联 Issue」及原因，不得编造；旧式仅 PR 编号写法停用（历史出处索引除外）。
  细则见 `AGENTS.md` §4、§6、§7。
- **D2 实施边界细化**（2026-10-11 人定，Issue #186 / PR #231）：D2 区分三类视觉资产——**必要 UI chrome**
  （界面框架 / 面板 / 边框 / 图标 / 控件状态；可用 CSS / 内嵌 SVG，必要时用原创、本地、可随仓库交付的静态素材）；
  **世界 / 故事相关资产**（角色 / 场景 / 物品 / 地点图继续支持占位与失败回退，正式资产按独立决策管理）；
  **少量生成式测试样张**（隔离标记，仅用于测试 / 原型）。保留零外链、无运行时生成、无构建步骤、占位回退；
  已同步 `GDD-BASELINE.md` D2 与 `UI-DESIGN.md` §0.2 / §4。
- **「时段」口径收尾**（2026-10-11，Issue #168 / PR #232 + PR #234；Issue #179 / PR #225）：判定基准
  `CHRON-DESIGN.md` §2.3——**「时段」只指一天四段（晨 / 昼 / 昏 / 夜）**；行程 / 预算 / 工时等**时长**
  一律用分钟 / 小时 / 天（四段**节奏义**合法，可保留并注明）。行程 / 迷路耗时改写为小时，
  `action_economy.json` 的 `id:"period"` 改名 `minor_action`，`docs/` 与 `data/` 镜像一致（docs 侧与 data
  镜像分 PR 串行）；ATLAS 接口响应键与常量口径为 `minutes` / `BAND_MINUTES`（PR #225 同步 `UI-DESIGN.md` / `CHRON-DESIGN.md`）。
- **自主编排落地**（2026-10-11，Issue #194 / PR #200；Issue #195 / PR #207；Issue #196 / PR #209；
  Issue #197 / PR #211；Issue #198 / PR #212；Issue #210 / PR #223）：`AGENT-ORCHESTRATION.md`
  状态机 / 合并闸门 G1–G6 / 能力探测 C1–C4 / 阶段出口 E1–E5 入库；提示词库（`prompts/` 四份）落地；
  `AGENTS.md` §3 授权 Master Agent 自主调度 Execution Subagent；本文件接入模式选择 / 阶段循环 / 阶段汇报，
  并定义端到端验收与阶段收口清单。
- **实现推进 wave 1–4 关键口径**（2026-10-12；13 切片全部合并，wave 1–3 收口报告 **Issue #294**）：
  - **存档布局 `campaigns/<sid>`**（M7-3，Issue #276 / PR #281）：一局落成 `<存档目录>/campaigns/<sid>/snapshot.json`
    （+ `snapshot.json.bak` / `events.ndjson` / `idempotency.json`）；老档（旧单文件 `<sid>.json`）**惰性迁移**——
    只读加载**不改写磁盘**，**第一次保存**才迁成新档并留原文备份 `snapshot.json.pre-v0.bak`；HTTP 响应形状不变。
  - **写接口 `/api/action/*`**（M8-A3，Issue #282 / PR #290）：四条路由——`GET /api/action/catalog` 与
    `POST /api/action/{propose,confirm,commit}`；`propose →（R2+ 确认令牌 `cf_<32hex>`）→ commit`
    （`Idempotency-Key` + `base_seq` 乐观并发）；`result` 先于叙事、无 AI 时显式行动照常走通；
    同键重放回首次结果、不重复副作用；旧 `POST /api/guide/turn` 行为不变。
  - **`advance_to` 停点语义**（M9-C4，Issue #283 / PR #289）：停在**最早语义屏障之前**（B1/B3/B4）——
    那一分钟的内容不执行、留给玩家；无屏障则停在最早到期（B8 结算点）；同一分钟**屏障优先**；
    **抵达 ≠ 跨越**（停点 > 目标时推进到目标）；B2/B5 落在到期分钟上只标注、到期照常触发；
    被压缩的时段**不产生**「被跳过」的事件（`compression_log` 只记真实触发）。
  - **CHRON 持久化**（M9-C5，Issue #292 / PR #297）：`blocks.chron` 随检查点落盘，`chron.restore(handle)`
    重启后与重启前**逐字段全等**（含到期顺序）；同 `idempotency_key` 重投 no-op；老档迁移补空块。
  - **占位清单 `/assets.json`**（M4-U6，Issue #284 / PR #288）：`static/assets.json`（当前 `{}`）经**同源**
    `GET /assets.json`（静态回落，零外链）挂到 `data-slot` 占位符；加文件 + 登记一行即生效，不改组件、不重新构建。
  - **导引者状态点 / 会话失败错误卡**（M4-U2，Issue #266 / PR #273，含修复轮 1）：顶栏状态点读
    `GET /api/guide/status` → `{chat, tts, configured}`（灰 = 未配置 / accent = 仅文字 / accent-2 = 文字与语音 / danger = 失败）；
    会话加载失败 → 主画面**就地错误卡** + 输入区 / 行动区禁用，**不再**落演示数据。
  - **行动管线前端接线**（M4-A4，Issue #291 / PR #296）：行动按钮与输入框接 `/api/action/*`，确认以服务端
    `needs_confirm` 为准；整句口令（`淡出` / `跳过这段`）仍走旧 `/api/guide/turn`（它们不是一条行动）。
- **世界主题契约**（2026-10-12，Issue #274 / PR #295）：`UI-WORLD-THEMES.md` 入库——主题选择只认结构化字段
  `world_type ∈ {fantasy, modern, future}`、**缺省 `modern`**（现值即中性基线，主题化零回归）；不得从世界名 /
  叙事文本 / 模型输出推断，也不得让 AI 每回合切主题；语义色（`--danger` / `--success`）不进世界主题块；
  实现拆分建议 **W0**（`world_type` 数据 / 接口契约，前置）→ **W1** 令牌层 → **W2** 接线 → **W3** 不变式测试 → **W4** 可选；
  未决项（字段名与落点、三个模组归属、`--scope-*` 登记处等）见该文 §12，**待拍板**。
- **实现推进 wave 5 关键口径**（2026-10-14；5 切片全部合并，收口报告 **Issue #294**）：
  - **只读状态视图 `/api/state` + `/api/events?since=`**（M7-4，Issue #312 / PR #318，**加性公开行为**）：
    两条都**只读**——不 `save()`、不触发检查点、**不为未迁移老档开句柄**（开句柄即落盘迁移）；
    老档按内存门面派生同一形状（`seq` 0、`world_time` null、事件空）；`since` 缺省 / 空 = 0 = 全量。
    `state.py` 仅加最小只读入口 `Handle.events_since(since)`（逃生口），不改提交 / 落盘 / 写路径。
  - **排程钩子接线 `/api/chron/{advance,next_barrier,cancel}` + `scheduled` 分支**（M9-C6 + M8-A5，Issue #313 / PR #319，含修复轮 1，**加性公开行为**）：
    L1 适配器 `_ChronScheduleHook` 放 `notdnd_web.py`（`chron.py` 零改动、接口面保持冻结）；长行动在**结算之前**分叉，
    `POST /api/action/commit` 立即回 `scheduled`（SSE `scheduled` → `done`），**不结算、不提交规则**；同键重放回同一回执、换键 409。
    `POST /api/chron/advance`（遇屏障停、途中逐条结算 + 纯压缩补 `clock.advance`）· `GET /api/chron/next_barrier`（只读）·
    `POST /api/chron/cancel`（幂等）。即时行动路径**逐字节不变**（对照实验证据，归一化随机量后裸响应与事件日志逐字相同）。
  - **`next_barrier` 老档不迁移边界**（M9-C6，Issue #313 / PR #319 修复轮 1）：`GET /api/chron/next_barrier` 在**未迁移老档**上
    **不开句柄**（开 = 落盘迁移）——直接回「无屏障」（`next_barrier: null`、`world_minute` 取 `ACTION_EPOCH_MINUTE`），
    与 M7-4 只读口径一致（§10.1）。**读一个数不该把老档迁了。**
  - **`round` / `turn` → 10 分钟占位**（M9-C6，Issue #313 / PR #319）：`MINOR_ACTION_MINUTES` 为**占位**最小排程步长；
    `minute` / `hour` / `watch` / `day` 用 §2.4 常量，规则时间更细的刻度**归规则包**（CHRON-DESIGN §11 C6 末条，**另开单**）。
    适配器级裁定 `CHRON Action.kind = intent.verb`（屏障名由规则包给）同属绑定选择，不改 M8 契约。
  - **每流有界队列裁定**（M10-S2，Issue #314 / PR #320）：MULTIPLAYER-DESIGN §5.5「每成员一个有界队列」按**每流**落地
    （`ROOM_STREAM_QUEUE_MAX = 256` 帧/流，局域网 ≤6 人量级）；满 → 该流由写线程收尾断流，客户端重连 + `since=last_seq` 补发自愈（不丢事件）；
    队列 / 流引用为内存态，不进 `room` 块。`GET /api/room/stream?since=` 复用导引者 SSE 底座（其它流响应形状逐字节不变）。
  - **`--scope-*` 未登记现状**（M4-W3，Issue #315 / PR #317）：W3 把 `UI-WORLD-THEMES.md` §6 语义不变式变成自动断言
    （`tests/test_ui_world_themes.py`，真浏览器 67/67，`run_all` 脚本数 14 → 15）；`--scope-public/-party/-private` 三档
    **当前仍未在 `tokens.css` 登记**（落点未拍板，B 层缺席）——测试按「缺席即逐项相同」断言，登记处由上位文档补一行（独立 Issue，见该文 §12 第 3 项）。
- **待办 / 待人拍板 / 阻塞**（2026-10-14 登记，**以 GitHub 为准**）：
  - **#316**（M4-W0 `world_type` 数据 / 接口契约）——**已拍板待派发**（2026-10-14 人拍板）：字段名沿用 `world_type`，
    按设计建议 §2.5 分层（世界模板声明、战役实例可覆盖）；三世界模组归属 `threshold → modern` / `emberfall → fantasy` /
    `entropic-net → future`。因占 `notdnd_web.py` 独占串行队列，派发排在 M10-S3 / S4 / S5 之后。
  - **#275**（README 精简）——**已收窄**（2026-10-14 人裁决）：本单收窄为纯 README 精简，暂不含三种世界 UI 概念图
    （图片来源 / 是否入库另行处理）；人依赖阻塞**解除**，并入 wave 6 派发。
  - **#321 / #322 / #323**（M10-S3 频道与认知分离 / S4 并发提交与幂等 / S5 运行模式、暂停与 PvP 门）——wave 6：
    **#321 已派发（在办）；#322 → #323 排队（等前者合并）**，共享 `notdnd_web.py` 串行队列。
  - **暂不推进（待人再议）**：MULTIPLAYER §12 **S6**（是否纳入 M10 首发）· UI-WORLD-THEMES **W4**（可选主题切片）·
    **`--scope-*` 登记处**（§12 第 3 项）——均不阻塞主线，由人可选裁决。
- **全面检查**：每 1–2 阶段一次（§6）。

---

## 6. 全面检查清单（每 1–2 个阶段做一次）

- [ ] **项目健康**：CI 四门全绿；`run_all` 绿；`validate_data` 绿；
      `git log --format='%ae%n%ce' | grep -i gmail` **为空**；无密钥 / 本机路径。
- [ ] **流程健康**：无长期挂起的开放 PR；每个 Issue 单一意图；无「撞同一文件」的并行。
- [ ] **引用格式**：PR 标题与所有审查 / 状态汇报 / 待办引用均显式标注关联 Issue（`PR #N（Issue #M）`，见 §3）。
- [ ] **数据一致性**：抽查若干 `source` 行号与 `docs/` 对齐；计数与文档一致。
- [ ] **文档**：更新本文件 §2 快照、§5 决策记录。
- [ ] **下一步**：按 §4 开下一切片 Issue（数据切片串行）。

---

## 7. 文档地图

| 文件 | 面向 | 内容 |
|---|---|---|
| `AGENTS.md` | 所有 Agent | 角色 / 三条铁律 / Issue 循环 / 四行审查 / 合并约定 / 目录边界 |
| **`MASTER.md`** | **Master Agent** | **本文件**：接手 + 状态 + 路线 + 检查清单 |
| `AGENT-ORCHESTRATION.md` | Master Agent / Execution Agent | **编排机制**：任务状态机、并行 / 互斥、独立审查与合并闸门 G1–G6、平台能力探测 C1–C4 与降级、阶段出口 E1–E5 与汇报格式 |
| `prompts/` | Master Agent / Execution Agent | **可直接复制的运行指令**：`master-multi-session.md`（多会话交接）/ `master-single-session.md`（单会话自动编排）/ `execution-subagent.md`（单任务执行）/ `reviewer-subagent.md`（可选第二意见） |
| **`GDD.html`** | 人 / Master Agent | **产品设计基线原件**（GDD v1.1，原样入库，PR #135）——产品愿景与系统语义的最终依据 |
| **`GDD-BASELINE.md`** | Master Agent / Execution Agent | **GDD → 工程翻译层**：已拍板决策 D1–D4、审计、目标架构、**M7 起路线依据**（PR #135） |
| **`ATLAS-DESIGN.md`** | Master Agent / Execution Agent | 自动地图（ATLAS）设计方案：帧 / 词表 / 编译 / 生成 / 六条拆单依据 |
| **`GUIDE-DESIGN.md`** | Master Agent / Execution Agent | 导引者：把地点卡走成可印证的街道、演已有人物、同一剧本每局不同；Chat Completions、前缀缓存、pcm16；G0–G7 |
| `README.md` | 使用者 | 怎么跑、定位、**「别暴露到公网」警告** |
| `data/FORMAT.md` | 数据贡献者 | 数据格式 / canonical 键 / schema 子集 |
| `HANDOVER.md` | 改造者 | 架构交接（M6 落地） |
| `docs/` | 规则与剧本 | PRISM 原创内容（**可修订，走独立 Issue/PR**） |

---

*本文件是治理文档，与 `AGENTS.md` 同级维护。*
