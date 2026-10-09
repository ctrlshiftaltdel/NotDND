# MASTER.md —— 主管 / Master Agent 接手与运行手册

> 本文件是**主管 Agent（Master）**的入口。**接手只需一句话**：
> **「你是主管/Master Agent，继续工作」**。
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

1. 读 `AGENTS.md`（角色、三条铁律、Issue 循环、四行审查、合并约定）。
2. 读本文件 §2 当前状态 + §4 路线；**以 GitHub 为准**核对。
3. **有开放 PR** → 按 §3 审查；**已审通过且 CI 全绿就合并**；有问题写说明或开 Issue。
4. **无开放 PR** → 看 §4 下一切片是否已开 Issue；未开则开（**数据切片串行**）。
5. **每 1–2 个阶段**做一次 §6 全面检查。

---

## 1. 项目一句话

**NotDND**：完全原创、MIT、**天生公开**的 AI 跑团网页应用。规则「**棱镜 PRISM**」，
AI 任「**导引者**」，纯文本、无图片；**零第三方运行时依赖**（Python 3 标准库 + 原生 HTML/CSS/JS，无框架、无构建）。
仓库：<https://github.com/ctrlshiftaltdel/NotDND>（public）。

---

## 2. 当前状态快照（截至 2026-10-09）

> ⚠️ 快照会过时——**以 GitHub 为准**。每次里程碑/阶段收口后更新本表（§6 第 4 项）。

- **分支保护**：ruleset「master」为 `active`——须经 PR + **四道 required checks** + 禁 force push / 禁删除。
- **CI 四道门**（`.github/workflows/ci.yml`）：语法门 / 回归门 / 内容防火墙门 / **提交邮箱门**。
- **安全**：全历史 **0 真实邮箱**（2026-10-09 已重写历史 + 账号开启「keep email private」）；无密钥、无本机路径。
- **数据层**：`python3 tests/validate_data.py` → **79 个文件**（`data/system/**` + `data/worlds/**`（三册）+ `data/random_tables/**` + `data/scenarios/**`（15 册）+ `data/atlas/lexicon.json` + schema/registry）。
- **回归**：`bash tests/run_all.sh` → **11 个脚本**（`test_commit_email_gate` / `test_content_firewall` / `test_data` / `test_repo_layout` / `test_notdnd_web` / `test_prism_core` / `test_atlas_kernel` / `test_atlas_compile` / `test_atlas_gen` / `test_atlas_ledger_pins` / `test_prism_guide`）。
- **里程碑**：M0 ✅ · M1.0 ✅ · **M1.1 a–e ✅** · **M1.2 a–c ✅** · **M1.3 a–b ✅** · **M1.4 剧本 ✅（PR #58 / #74 / #77 数据↔docs 对齐）** · **M2 规则核心 a–c ✅（PR #57 / #70 / #76）** · M3 **a ✅（PR #56）/ b 待** · **ATLAS I1–I6 全部完成 ✅（PR #75 / #79 / #81 / #95 / #93 / #80；后续修复 PR #91）** · **M5 导引者 G0–G1 ✅（PR #83 / #92），G2 在审（PR #96，待 rebase）** · M4 / M2d / M6 待。

---

## 3. 主管的例行循环（摘要）

> 完整规范见 `AGENTS.md` 第 2–6 节。此处只列要点。

- **收需求为 Issue**：一个 Issue 只做一件事，写清 **目标 / 验收标准 / 可改路径 / 禁改路径 / 依赖的 PR**；
  并打上**难度**与**主要能力**标签（见下），供人按能力指派实现 Agent。
- **审查开放 PR**：对照验收标准**逐条**核对；看 CI 是否全绿；看是否只改了允许路径；
  有没有新依赖 / 密钥模样内容。在 PR 评论里留**四行建议**（建议 / 用户能感知的变化 / 没完成的验收项 / 需人拍板的风险）。
  **汇报与审查都写明 PR ↔ Issue 对应**（如 `PR #70（Issue #59）`、`Issue #59（PR #70）`）。
- **合并**：写「建议合」且 **CI 全绿** → **主管直接合并并删远端分支**（不再逐件请示人）。
  预审若**无需修改**，主管**直接 `gh pr ready` 代转并合并**，不来回等作者转 Ready（人 2026-10-09 明确）。
  有未完成项 / 问题 → 写四行说明或**开 Issue 跟踪**。
- **串并行的硬约束**：
  - **数据切片彼此串行**——`data/system/**`、`data/worlds/**`、`data/schema/registry.json`、`data/FORMAT.md` 是共享文件。
  - 不同文件 / 不同目录（如 `tests/**` 与 `data/**`）**可并行**。
- **尽量并行开单**：能并行就并行——只要**不与在办 PR 撞同一文件**，就同时开出多条 Issue 节约总工时
  （常规每轮 **2–4 条**）；**数据切片彼此串行**仍是硬约束。
- **Issue 标注（开单必填）**：每条 Issue 打两类 GitHub 标签：
  - **难度**（三档，单选）：`难度：低` / `难度：中` / `难度：高`。
  - **主要能力**（多选，1–3 个）：`能力：编程` / `能力：逻辑` / `能力：文学` / `能力：数据` /
    `能力：测试` / `能力：前端` / `能力：文档`。
- **DNDWeb 资产取用（多 Agent 协作）**：Issue 若需取用 `~/Projects/DNDWeb` 的**代码 / 工程做法**，
  而实现 Agent 无该目录访问权，主管**先派一个有访问权的 subagent**把相关资产**脱敏迁移**到本项目：
  只搬「怎么做」（结构 / 函数切分 / 工程范式），**重写命名与术语、剔除第三方专名与数据**，
  产物落**最终目标路径**（根 `.py` / `static/`）并仍须过内容防火墙；
  随后在需要它的 Issue 里写清**取用指引**（「资产已迁移：`<路径>`，见 PR #N」）。
- **实现 Agent 一律不合并**（含自主推进期）；合并只由主管执行。

### 常用命令

```bash
gh pr list --state open --json number,title,isDraft,headRefName
gh pr checks <n>
gh pr comment <n> --body-file review.md            # 四行审查
gh pr merge <n> --merge --delete-branch            # 已审通过 + CI 绿
gh pr ready <n>                                    # Draft → 可审查（如实现者忘转）
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
| **M2** | 规则核心（离线可玩） | a 判定 / 结果 / 代价 / 资源 ✅（PR #57）/ b 战斗 / 派生 / 成长 ✅（PR #70）/ c 构建引擎 ✅（PR #76）；M2d（符纹 / 插槽 / 构建点 / 互转）待 |
| **M3** | 会话与存档 + 后端 API | a 规则快照接线 ✅（PR #56）/ b 写接口 / API 待 |
| **M4** | 前端 UI（`static/`） | 待 |
| **M5** | AI 导引者（可选模块，import 失败不影响离线）。方案见 `GUIDE-DESIGN.md` | **进行中**：G0 ✅ / G1 ✅（PR #83 / #92）；**G2 在审（PR #96）**；G3–G7 已开单（#86–#90） |
| **M6** | 测试与文档收口（README + HANDOVER + run_all 全量） | 待 |

> **ATLAS（自动地图）切片**：`ATLAS-DESIGN.md` §8 的六条 Issue **I1–I6** 与 M2–M4 交叉
> （内核 → 词表编译 → 地点/战术生成 → {会话接线 / 规则投影 / 账本 place id}）；
> 依赖顺序 **I1 → I2 → I3 → {I4 / I5 / I6}**。**进度**：**六片全部落地**——I1–I3 ✅（PR #75 / #79 / #81）、
> I4 ✅（PR #95）、I5 ✅（PR #93）、I6 ✅（PR #80）、I2 后续修复 ✅（PR #91）。
> **硬约束（历史）**：I2 独占 `data/schema/registry.json` + `data/FORMAT.md` 的时段随 #79 合并结束；
> I5 的前置 #59、I6 的前置 #61 均已合并。
>
> **导引者（GUIDE）切片**：`GUIDE-DESIGN.md` PR Plan 的八条 **G0–G7**（管道 G1–G4 → 绑定 / 实相 / 对白 G5–G7）。
> 依赖链 **G0 → G1 → G2 → {G3 / G4 / G5} → G6 → G7**；**同文件串行**：G3↔G4↔G5 与 G6↔G7 都占 `prism_guide.py`；
> G2 / G5 不与改 `notdnd_web.py` 的 PR 并行（#95 已合并），G4 不与改 `prism_core.py` 的 PR 并行（#67 已合并）。
> **进度**：G0 ✅（PR #83）· G1 ✅（PR #92）；**G2 在审（PR #96，待 rebase #95 后合）**；G3–G7 待办（#86–#90）。

---

## 5. 关键约定与决策记录

> 全部为「已确认」；改动走新 Issue。详见 `AGENTS.md` §10。

- **三条铁律**：① 内容边界（禁第三方版权内容照抄/翻译/改名派生；**允许**借鉴通用机制与题材设定）；② 零第三方运行时依赖（测试期从简）；③ 密钥与隐私永不入库。
- **参考项目 DNDWeb 的代码与工程做法可复用**；其**内容 / 数据**（reference、corpus、spells/monsters/species/backgrounds、campaigns）**一律不得入库**。
- **六维命名**：产品层统一 PRISM 名 **力道 MGT / 灵巧 FIN / 体魄 VIG / 洞察 INS / 心智 MND / 气场 PRE**；D&D 通用名仅作同义词。
- **`docs/` 可修订**：属内容变更，**单独开 Issue/PR**、不夹带在实现 PR。
- **并行粒度按文件**：同一文件有开放 PR 时不并行。
- **数据规范**（`data/FORMAT.md`）：封套 `{schema_version, kind, 载荷}`；`kind = <family>.<name>`，**每个 kind 一份平铺 schema + `registry.json` 登记**；每个实体带 `source:"docs/…:行号"` 溯源；散文不进 JSON。
- **主管自主运行**（2026-10-09 授权）：能合就合、有问题写说明或开 Issue、能推进就推进、能并行就并行，不逐件请示（**除非无法决定**）。
- **DNDWeb 脱敏迁移**（2026-10-09）：M2/M3 的**工程骨架**已由主管派的迁移 Agent 完成并合并
  （`prism_core.py` / `notdnd_web.py`，PR #49 / #48）；实现 Agent 直接在其上继续即可，
  **无需** DNDWeb 访问权（流程见 §3）。`DNDWEB-ASSETS.md` §1/§2 状态已改 ✅、§3 已改 🟡（结构）。
- **阶段收口**（2026-10-09）：**M1.4 剧本 / M2（a–c）/ M3a / ATLAS I1–I6（全部）/ 导引者 G0–G1 已完成**
  （PR #58 / #74 / #77 / #57 / #70 / #76 / #56 / #75 / #79 / #81 / #95 / #93 / #80 / #91 / #83 / #92）；
  在办与待办：**导引者 G2 在审（PR #96，待 rebase）**、**G3–G7（#86–#90）**、M2d。
- **自动地图（ATLAS）**（2026-10-09 拍板）：跨 M2–M4 的**方向性设计**，方案入库 `ATLAS-DESIGN.md`
  （见 §7 文档地图）；拆六条切片 **I1–I6**（依赖见 §4 注）。**影响面**：新增 `atlas.py` /
  `atlas_compile.py` / `atlas_gen.py` 与 `data/atlas/lexicon.json`（新 kind）；I4 给会话加快照块 `atlas`；
  I5 接 `prism_core.py`；I6 把剧本账本地点钉到 `place_id`。**不改**既有 `docs/` 规则。
- **导引者（GUIDE）**（2026-10-09 人定稿）：M5 的方向性设计，方案入库 `GUIDE-DESIGN.md`（PR #83）。
  **三件用途**：把地点卡走成可印证的街道图（实相，每「存档 × 地点」只生成一次并落盘）、演已有人物
  （场外痕迹 / 对白 / 秘密门）、同世界同剧本**每局不同**（`guide.salt`；战役节点骨架不换）。
  拆八条 **G0–G7**（依赖见 §4 注）；新增 `prism_guide.py`（可选模块，import 失败不影响离线）；
  **不改** `prism_core.py`（G5–G7）与 `docs/`；实相**不写坐标**——地图仍归 ATLAS。
- **全面检查**：每 1–2 阶段一次（§6），并持续挖掘 DNDWeb 资产（§DNDWeb-ASSETS）。

---

## 6. 全面检查清单（每 1–2 个阶段做一次）

- [ ] **DNDWeb 资产再盘点**：对照 `DNDWEB-ASSETS.md`，更新每项「未搬 / 部分 / 已搬 / 不再需要」；
      当所有高价值项都已搬或明确不需要时，记录「DNDWeb 已无借鉴价值」并结案。
- [ ] **项目健康**：CI 四门全绿；`run_all` 绿；`validate_data` 绿；
      `git log --format='%ae%n%ce' | grep -i gmail` **为空**；无密钥 / 本机路径。
- [ ] **流程健康**：无长期挂起的开放 PR；每个 Issue 单一意图；无「撞同一文件」的并行。
- [ ] **数据一致性**：抽查若干 `source` 行号与 `docs/` 对齐；计数与文档一致。
- [ ] **文档**：更新本文件 §2 快照、§5 决策记录、`DNDWEB-ASSETS.md` 状态列。
- [ ] **下一步**：按 §4 开下一切片 Issue（数据切片串行）。

---

## 7. 文档地图

| 文件 | 面向 | 内容 |
|---|---|---|
| `AGENTS.md` | 所有 Agent | 角色 / 三条铁律 / Issue 循环 / 四行审查 / 合并约定 / 目录边界 |
| **`MASTER.md`** | **主管 Agent** | **本文件**：接手 + 状态 + 路线 + 检查清单 |
| `DNDWEB-ASSETS.md` | 主管 / 实现 | DNDWeb 可复用资产与挖掘状态 + 剥离红线 |
| **`ATLAS-DESIGN.md`** | 主管 / 实现 | 自动地图（ATLAS）设计方案：帧 / 词表 / 编译 / 生成 / 六条拆单依据 |
| **`GUIDE-DESIGN.md`** | 主管 / 实现 | 导引者：把地点卡走成可印证的街道、演已有人物、同一剧本每局不同；Chat Completions、前缀缓存、pcm16；G0–G7 |
| `README.md` | 使用者 | 怎么跑、定位、**「别暴露到公网」警告** |
| `data/FORMAT.md` | 数据贡献者 | 数据格式 / canonical 键 / schema 子集 |
| `HANDOVER.md` | 改造者 | 架构交接（M6 落地） |
| `docs/` | 规则与剧本 | PRISM 原创内容（**可修订，走独立 Issue/PR**） |

---

*本文件是治理文档，与 `AGENTS.md` 同级维护。*
