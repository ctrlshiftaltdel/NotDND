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
- **数据层**：`python3 tests/validate_data.py` → **60 个文件**（`data/system/**` + `data/worlds/**` + schema/registry）。
- **回归**：`bash tests/run_all.sh` → 4 个脚本（`test_commit_email_gate` / `test_content_firewall` / `test_data` / `test_repo_layout`）。
- **里程碑**：M0 ✅ · M1.0 ✅ · **M1.1 系统规则 a–e ✅** · M1.2 世界模组 a ✅ b ✅ **c ← 进行中（Issue #34）** · M1.3 / M1.4 待 · M2–M6 待。

---

## 3. 主管的例行循环（摘要）

> 完整规范见 `AGENTS.md` 第 2–6 节。此处只列要点。

- **收需求为 Issue**：一个 Issue 只做一件事，写清 **目标 / 验收标准 / 可改路径 / 禁改路径 / 依赖的 PR**。
- **审查开放 PR**：对照验收标准**逐条**核对；看 CI 是否全绿；看是否只改了允许路径；
  有没有新依赖 / 密钥模样内容。在 PR 评论里留**四行建议**（建议 / 用户能感知的变化 / 没完成的验收项 / 需人拍板的风险）。
- **合并**：写「建议合」且 **CI 全绿** → **主管直接合并并删远端分支**（不再逐件请示人）。
  有未完成项 / 问题 → 写四行说明或**开 Issue 跟踪**。
- **串并行的硬约束**：
  - **数据切片彼此串行**——`data/system/**`、`data/worlds/**`、`data/schema/registry.json`、`data/FORMAT.md` 是共享文件。
  - 不同文件 / 不同目录（如 `tests/**` 与 `data/**`）**可并行**。
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
| 　M1.2 | 世界模组数据（`data/worlds/`） | a 阈界都市 ✅ / b 余烬纪元 ✅ / **c 熵网（#34）** |
| 　M1.3 | 随机表（`data/random_tables/`：system 08 + S7 70 张 + 各模组表） | 待 |
| 　M1.4 | 剧本（`data/scenarios/`：09/S8 + S1–S6 结构件 + 账本模板） | 待（**需先做 docs/ 修订**：C-xx 线索主表、flag 归一） |
| **M2** | 规则核心（离线可玩） | 待 |
| **M3** | 会话与存档 + 后端 API | 待 |
| **M4** | 前端 UI（`static/`） | 待 |
| **M5** | AI 导引者（可选模块，import 失败不影响离线） | 待 |
| **M6** | 测试与文档收口（README + HANDOVER + run_all 全量） | 待 |

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
| `README.md` | 使用者 | 怎么跑、定位、**「别暴露到公网」警告** |
| `data/FORMAT.md` | 数据贡献者 | 数据格式 / canonical 键 / schema 子集 |
| `HANDOVER.md` | 改造者 | 架构交接（M6 落地） |
| `docs/` | 规则与剧本 | PRISM 原创内容（**可修订，走独立 Issue/PR**） |

---

*本文件是治理文档，与 `AGENTS.md` 同级维护。*
