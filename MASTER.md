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
4. **无开放 PR** → 看 §4 下一切片是否已开 Issue；未开则开（**数据切片串行**）。
5. **每 1–2 个阶段**做一次 §6 全面检查。

---

## 1. 项目一句话

**NotDND**：完全原创、MIT、**天生公开**的 AI 跑团网页应用。规则「**棱镜 PRISM**」，
AI 任「**导引者**」，纯文本运行为主、视觉占位框架先行（D2）；当前零第三方依赖（Python 3 标准库 + 原生 HTML/CSS/JS，无框架、无构建），依赖策略为**技术选型效率优先**（D3）。
仓库：<https://github.com/ctrlshiftaltdel/NotDND>（public）。

---

## 2. 当前状态快照（截至 2026-10-10）

> ⚠️ 快照会过时——**以 GitHub 为准**。每次里程碑/阶段收口后更新本表（§6 第 4 项）。

- **分支保护**：ruleset「master」为 `active`——须经 PR + **四道 required checks** + 禁 force push / 禁删除。
- **CI 四道门**（`.github/workflows/ci.yml`）：语法门 / 回归门 / 内容防火墙门 / **提交邮箱门**。
- **安全**：全历史 **0 真实邮箱**（2026-10-09 已重写历史 + 账号开启「keep email private」）；无密钥、无本机路径。
- **数据层**：`python3 tests/validate_data.py` → **79 个文件**（`data/system/**` + `data/worlds/**`（三册）+ `data/random_tables/**` + `data/scenarios/**`（15 册）+ `data/atlas/lexicon.json` + schema/registry）。
- **回归**：`bash tests/run_all.sh` → **12 个脚本**（`test_commit_email_gate` / `test_content_firewall` / `test_data` / `test_repo_layout` / `test_notdnd_web` / `test_prism_core` / `test_atlas_kernel` / `test_atlas_compile` / `test_atlas_gen` / `test_atlas_ledger_pins` / `test_prism_guide` / `test_ui_smoke`）。
- **里程碑**：M0 ✅ · M1 ✅（M1.0–M1.4；剧本 PR #58 / #74 / #77）· **M2 ✅（a–d：PR #57 / #70 / #76 / #130）** · M3 **a ✅（PR #56）/ b 并入 M8**（通用行动提案写接口）· **ATLAS I1–I6 ✅（PR #75 / #79 / #81 / #95 / #93 / #80；修复 PR #91）** · **M5 G0–G7 ✅（PR #83 / #92 / #96 / #99 / #101 / #106 / #108 / #119；时间盒 PR #126）** · **M4 骨架 ✅（PR #118）/ 正式 UI 设计单 #133**（`能力：设计`）· **M6 ✅（a PR #127 / b PR #128）** · **GDD v1.1 + 工程基线 ✅（PR #135）——路线自 M7 起以 [`GDD-BASELINE.md`](GDD-BASELINE.md) §5 为准**。
- **并行分道**（2026-10-10）：**#113 ✅（PR #126）· M2d ✅（PR #130）· M6a ✅（PR #127 / #123）· M6b ✅（PR #128）· GDD 基线 ✅（PR #135）**；**首批设计单已开**：M7-a / M8-a / M9-a（**#136 / #137 / #138**）、候补单 GDD→Markdown（**#139**）、M4 设计单 **#133**（已追加 D2 视觉占位要求）；在办/待办：**#110**（待人派发）、治理同步（PR #140）。

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
  但**作者仍在推进的 Draft 先别抢**——预读可以，等其推完再走流程。
  有未完成项 / 问题 → 写四行说明或**开 Issue 跟踪**。
- **权限边界**：日常治理（MASTER 快照、流程细化）可自主以 PR 落地；
  **重写历史 / 动保护规则 / 改仓库可见性等大动作先问人**；
  **角色边界之外的动作，除非用户明确授权，否则不做**（见 `AGENTS.md` §3）。
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

## 4. 路线图（M0–M6 + 数据切片）

| 里程碑 | 内容 | 状态 |
|---|---|---|
| **M0** | 仓库与协作基础设施（AGENTS/License/CI 三门/模板/gitignore） | ✅ |
| **M1** | 数据层：PRISM → JSON | 进行中 |
| 　M1.0 | 数据格式与校验基线（FORMAT + JSON Schema + 校验器） | ✅ |
| 　M1.1 | 系统规则数据（`data/system/`） | ✅ a 内核 / b 战术 / c 构建 / d 互动 / e 导引者 |
| 　M1.2 | 世界模组数据（`data/worlds/`） | a 阈界都市 ✅ / b 余烬纪元 ✅ / c 熵网 ✅ |
| 　M1.3 | 随机表（`data/random_tables/`：system 08 + S7 70 张；各模组表已在 `data/worlds/*`） | a 通用生成器（docs/system/08）✅ / b S7 70 张（docs/scenario/S7）✅ |
| 　M1.4 | 剧本（`data/scenarios/`：09/S8 + S1–S6 结构件 + 账本模板） | a S1–S6 结构件 ✅（PR #58）/ b 09/S8 战役 ✅（PR #74）——**完成** |
| **M2** | 规则核心（离线可玩） | a 判定 / 结果 / 代价 / 资源 ✅（PR #57）/ b 战斗 / 派生 / 成长 ✅（PR #70）/ c 构建引擎 ✅（PR #76）/ **d 构建引擎收尾 ✅（PR #130）——M2 完成**（02B 第六至十一节：战斗 / 超载连锁 / 符纹插槽 / 构建点 / 符纹库 / 互转） |
| **M3** | 会话与存档 + 后端 API | a 规则快照接线 ✅（PR #56）/ **b 并入 M8**（通用行动提案写接口） |
| **M4** | 前端 UI（`static/`） | 骨架 ✅（PR #118）；正式 UI **设计单 #133**（先出设计再拆单）（含 UI 设计） |
| **M5** | AI 导引者（可选模块，import 失败不影响离线）。方案见 `GUIDE-DESIGN.md` | **G0–G7 ✅**（PR #83 / #92 / #96 / #99 / #101 / #106 / #108 / #119）；修复 #109 ✅（PR #112）；**时间盒修订 ✅（PR #126）**；收尾 **#110**（要点 cites 专规） |
| **M6** | 测试与文档收口（README + HANDOVER + run_all 全量） | **#123 ✅（PR #127） / #124 ✅（PR #128）——完成** |

> **M7 起路线**见 [`GDD-BASELINE.md`](GDD-BASELINE.md) §5：M7 状态与事件层 → M8 行动管线（含写接口）→ M9 CHRON → M10 多人（局域网 2–6 人）→ M11 NEURA → M12 世界生成与经济 → M13 平台化；并行轨 = M4 UI（#133）。
>
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
> **进度**：**G0–G7 ✅**（PR #83 / #92 / #96 / #99 / #101 / #106 / #108 / **#119**）；**修复 #109 ✅（PR #112：非流式整段 JSON + 共享时间盒）**；收尾 **#110（要点 cites 专规）**；时间盒修订 ✅（PR #126，40→75）。

---

## 5. 关键约定与决策记录

> 全部为「已确认」；改动走新 Issue。详见 `AGENTS.md` §10。

- **三条铁律**：① 内容边界（禁第三方版权内容照抄/翻译/改名派生；**允许**借鉴通用机制与题材设定）；② **技术选型效率优先**（不设依赖上限；引入依赖在 PR 说明理由与替代；测试期从简——2026-10-10 由「零第三方运行时依赖」改写，D3）；③ 密钥与隐私永不入库。
- **GDD v1.1 + 工程基线**（2026-10-10）：[`GDD.html`](GDD.html)（原件）与 [`GDD-BASELINE.md`](GDD-BASELINE.md)（审计 / D1–D4 / 目标架构 / M7–M13 / 拆单依据）入库（PR #135）；治理同步（铁律 2、README、HANDOVER、tests 口径、本快照与路线）随 PR #140 落地；首批设计单 #136–#138、候补单 #139 已开。
- **六维命名**：产品层统一 PRISM 名 **力道 MGT / 灵巧 FIN / 体魄 VIG / 洞察 INS / 心智 MND / 气场 PRE**；D&D 通用名仅作同义词。
- **`docs/` 可修订**：属内容变更，**单独开 Issue/PR**、不夹带在实现 PR。
- **并行粒度按文件**：同一文件有开放 PR 时不并行。
- **数据规范**（`data/FORMAT.md`）：封套 `{schema_version, kind, 载荷}`；`kind = <family>.<name>`，**每个 kind 一份平铺 schema + `registry.json` 登记**；每个实体带 `source:"docs/…:行号"` 溯源；散文不进 JSON。
- **Master Agent 自主运行**（2026-10-09 授权）：能合就合、有问题写说明或开 Issue、能推进就推进、能并行就并行，不逐件请示（**除非无法决定**）。
- **命名与越权**（2026-10-10 人定）：角色名统一为 **Master Agent** / **Execution Agent**（不再使用「执行 Agent」这类中英文混用名）；**越权动作除非用户明确授权，否则不做**；**涉及 UI 设计的 Issue 必须注明**（`能力：设计` 标签 + 正文首行「含 UI 设计」）。
- **工程骨架补全**（2026-10-10）：前端 `static/` 三视图骨架（PR #118）与测试基座
  （`tests/_cdp.py` / `cleanup_servers.sh` / UI 冒烟 / `tests/README.md`，PR #117）已落地；
  `run_all` 现有 **12 个脚本**。
- **M6 文档骨架（归档约定）**：`README.md` = 跑起来 → 结构取舍 → 测试 → 已知边界（含
  「别暴露到公网」警告）；`HANDOVER.md` = 每节答「为什么 + 踩过的坑」；测试约定见 `tests/README.md`。
- **阶段收口**（2026-10-10）：**M1.4 剧本 / M2（a–d）/ M3a / ATLAS I1–I6（全部）/ 导引者 G0–G7 / M6 已完成**
  （PR #58 / #74 / #77 / #57 / #70 / #76 / #56 / #75 / #79 / #81 / #95 / #93 / #80 / #91 / #83 / #92 / #96 / #99 / #101 / #106 / #108 / #112 / **#119** / #126 / #130 / #127 / #128 / **#135**）；
  **新路线自 M7 起**（基线 §5）；在办与待办：**#110（要点 cites 专规，待人派发）**、**M4 正式 UI（设计单 #133）**、**首批设计单 #136–#138（待派发）**、**治理同步（PR #140）**。
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
| **`ATLAS-DESIGN.md`** | Master Agent / Execution Agent | 自动地图（ATLAS）设计方案：帧 / 词表 / 编译 / 生成 / 六条拆单依据 |
| **`GUIDE-DESIGN.md`** | Master Agent / Execution Agent | 导引者：把地点卡走成可印证的街道、演已有人物、同一剧本每局不同；Chat Completions、前缀缓存、pcm16；G0–G7 |
| **`GDD.html`** | 人 / 所有 Agent | 产品设计基线 v1.1（原样入库，PR #135；转 Markdown = 候补单 #139） |
| **`GDD-BASELINE.md`** | Master Agent / Execution Agent | GDD → 工程基线：审计 / 决策 D1–D4 / 目标架构 / M7–M13 / 首批拆单（**Master Agent 维护**） |
| `README.md` | 使用者 | 怎么跑、定位、**「别暴露到公网」警告** |
| `data/FORMAT.md` | 数据贡献者 | 数据格式 / canonical 键 / schema 子集 |
| `HANDOVER.md` | 改造者 | 架构交接（M6 落地） |
| `docs/` | 规则与剧本 | PRISM 原创内容（**可修订，走独立 Issue/PR**） |

---

*本文件是治理文档，与 `AGENTS.md` 同级维护。*
