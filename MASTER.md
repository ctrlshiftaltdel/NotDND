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

1. 读 `AGENTS.md`（角色、三条铁律、Issue 循环、四行审查、合并约定）。
2. 读本文件 §2 当前状态 + §4 路线；**以 GitHub 为准**核对。
3. **有开放 PR**（人只说「**读 PR**」时也是此意）→ 按 §3 审查；**已审通过且 CI 全绿就合并**；有问题写说明或开 Issue。
   **复核范围**（2026-10-10 人定）：只处理**待审 PR 的分支**——不扫描、不预读尚未开 PR 的远端新分支；
   新分支以「PR 开出」为进入视野的信号。
4. **无开放 PR** → 看 §4 下一切片是否已开 Issue；未开则开（**数据切片串行**）。
5. **每 1–2 个阶段**做一次 §6 全面检查。
6. **收束点**：处理完开放 PR / 本期应开的单后，**停下向人汇报**；未派单的实现动作不做（见 §3）。

---

## 1. 项目一句话

**NotDND**：完全原创、MIT、**天生公开**的 AI 跑团网页应用。规则「**棱镜 PRISM**」，
AI 任「**导引者**」，**纯文本运行 + 视觉占位框架**（D2：为视觉要素预留占位符与替换框架，
当前不引入图像管线、不新增图片资产）。
仓库：<https://github.com/ctrlshiftaltdel/NotDND>（public）。

- **依赖现状**：Python 3 标准库 + 原生 HTML/CSS/JS（无框架、无构建）。
- **依赖政策**（D3，2026-10-10 人拍板）：**技术选型效率优先**——以最好、最高效实现为准，
  **不设依赖上限**；引入依赖时在 PR 说明理由、权衡与替代方案。
- 现行 `AGENTS.md` 铁律 2 的政策口径同步见 **#144**（治理 PR）；**其合并前，
  实现 PR 仍遵守现行铁律 2**（D3 过渡期约束）。

---

## 2. 当前状态快照（截至 2026-10-10）

> ⚠️ 快照会过时——**以 GitHub 为准**。每次里程碑/阶段收口后更新本表（§6 第 4 项）。

- **分支保护**：ruleset「master」为 `active`——须经 PR + **四道 required checks** + 禁 force push / 禁删除。
- **CI 四道门**（`.github/workflows/ci.yml`）：语法门 / 回归门 / 内容防火墙门 / **提交邮箱门**。
- **安全**：全历史 **0 真实邮箱**（2026-10-09 已重写历史 + 账号开启「keep email private」）；无密钥、无本机路径。
- **数据层**：`python3 tests/validate_data.py` → **79 个文件**（`data/system/**` + `data/worlds/**`（三册）+ `data/random_tables/**` + `data/scenarios/**`（15 册）+ `data/atlas/lexicon.json` + schema/registry）。
- **回归**：`bash tests/run_all.sh` → **12 个脚本**（`test_commit_email_gate` / `test_content_firewall` / `test_data` / `test_repo_layout` / `test_notdnd_web` / `test_prism_core` / `test_atlas_kernel` / `test_atlas_compile` / `test_atlas_gen` / `test_atlas_ledger_pins` / `test_prism_guide` / `test_ui_smoke`）。
- **里程碑**：**M0–M6 主体 ✅（已完成历史，见 §4）**；**M6a ✅（Issue #123 / PR #127）· M6b ✅（Issue #124 / PR #128）**——M6 收口。官方路线自 **M7** 起见 `GDD-BASELINE.md` §5。
- **GDD 基线**（PR #135）：`GDD.html`（v1.1 原件）与 `GDD-BASELINE.md` 入库；Markdown 版 `GDD.md`（Issue #139 / PR #150）。
- **角色分工**（Issue #141 / PR #142，2026-10-10 人定）：Master Agent 只把控方向 / 拆单派发 / 审批，**不做实现**（不编辑 / 不 commit / 不 push / 不开 PR）；Execution Agent 严格按 Issue 执行、**一律不合并**（细则见 `AGENTS.md` §3）。
- **设计单（六份全部入库）**：M7-a `STATE-DESIGN.md`（#136 / PR #152）· M8-a `ACTION-DESIGN.md`（#137 / PR #149）· M9-a `CHRON-DESIGN.md`（#138 / PR #151）· M10-a `MULTIPLAYER-DESIGN.md`（#143 / PR #146）· M4 正式 UI `UI-DESIGN.md`（#133 / PR #147，**含 D2 视觉占位框架要求**）。
- **已关单**：**#110**（要点节点 cites 自引专名，PR #148）；**#140**（2026-10-10 角色边界复盘产物，按人决定「不重做、不审阅」关闭）——其内容重开为 **#144**（D2/D3 治理口径同步）与 **#145**（即为本快照单）。
- **在办 / 待办**（开放 PR：**无**）：**#144**（D2/D3 口径同步：`AGENTS.md` 铁律 2 + `README.md` / `HANDOVER.md` / `tests`）· **#153**（Master 复核范围收窄：不预读未开 PR 的新分支；**与 #145 同文件 `MASTER.md`，二单串行**）· **#154**（STATE / ACTION / CHRON / MULTIPLAYER 四份设计单接口对齐，拆实现单前）· **#155**（CHRON §2.6 时间口径修订，待人拍板后开工）。

---

## 3. Master Agent 的例行循环（摘要）

> 完整规范见 `AGENTS.md` 第 2–6 节。此处只列要点。

- **收需求为 Issue**：一个 Issue 只做一件事，写清 **目标 / 验收标准 / 可改路径 / 禁改路径 / 依赖的 PR**；
  并打上**难度**与**主要能力**标签（见下），供人按能力指派 Execution Agent。
- **审查开放 PR**：对照验收标准**逐条**核对；看 CI 是否全绿；看是否只改了允许路径；
  有没有新依赖 / 密钥模样内容。在 PR 评论里留**四行建议**（建议 / 用户能感知的变化 / 没完成的验收项 / 需人拍板的风险）。
  **汇报与审查都写明 PR ↔ Issue 对应**（如 `PR #70（Issue #59）`、`Issue #59（PR #70）`）。
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

- **三条铁律**：① 内容边界（禁第三方版权内容照抄/翻译/改名派生；**允许**借鉴通用机制与题材设定）；② 依赖政策——**现行** `AGENTS.md` 铁律 2 为「零第三方运行时依赖」，**已按 D3 改为「技术选型效率优先」的政策口径待 #144 同步**（见 §1 与下条 GDD 基线）；③ 密钥与隐私永不入库。
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
  当前不引入图像管线、不新增图片资产）；**D3 技术选型效率优先**（不设依赖上限，引入依赖在 PR 说明理由、权衡与替代；
  过渡期约束见 §1）；**D4 交付形态**（`GDD.html` 原样入库，Markdown 拆分另立任务——`GDD.md` 已由 #139 / PR #150 落地）。
- **角色分工明确化**（2026-10-10 人定，Issue #141 / PR #142，细则见 `AGENTS.md` §3）：
  **Master Agent = 把控方向 / 阶段规划 / 拆单派发 / 审批，不做实现**（不编辑文件、不 commit / push、不开 PR）；
  **Execution Agent = 严格按 Issue 完成，绝不做未要求的任务或改动**，超范围先停下报告；
  **Execution Agent 一律不合并**，合并只由 Master Agent 在人同意后执行；文档中「由 Master Agent 执行 / 落地 / 维护」
  字样**不构成编辑授权**（旧口径已作废）。
- **工程骨架补全**（2026-10-10）：前端 `static/` 三视图骨架（PR #118）与测试基座
  （`tests/_cdp.py` / `cleanup_servers.sh` / UI 冒烟 / `tests/README.md`，PR #117）已落地；
  `run_all` 现有 **12 个脚本**。
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
- **全面检查**：每 1–2 阶段一次（§6）。

---

## 6. 全面检查清单（每 1–2 个阶段做一次）

- [ ] **项目健康**：CI 四门全绿；`run_all` 绿；`validate_data` 绿；
      `git log --format='%ae%n%ce' | grep -i gmail` **为空**；无密钥 / 本机路径。
- [ ] **流程健康**：无长期挂起的开放 PR；每个 Issue 单一意图；无「撞同一文件」的并行。
- [ ] **数据一致性**：抽查若干 `source` 行号与 `docs/` 对齐；计数与文档一致。
- [ ] **文档**：更新本文件 §2 快照、§5 决策记录。
- [ ] **下一步**：按 §4 开下一切片 Issue（数据切片串行）。

---

## 7. 文档地图

| 文件 | 面向 | 内容 |
|---|---|---|
| `AGENTS.md` | 所有 Agent | 角色 / 三条铁律 / Issue 循环 / 四行审查 / 合并约定 / 目录边界 |
| **`MASTER.md`** | **Master Agent** | **本文件**：接手 + 状态 + 路线 + 检查清单 |
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
