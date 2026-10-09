# DNDWEB-ASSETS.md —— 参考项目可复用资产清单与挖掘状态

> 参考项目：`~/Projects/DNDWeb`（**私有** D&D 5e 网页应用）。
> 本项目的规则：**可复用其代码与工程做法**（同一作者，无第三方版权问题）；
> **绝不引入其内容 / 数据 / 第三方表达**（含翻译、改名、微调派生）。
>
> **用途**：主管 / 实现 Agent 照此**逐项挖掘**，直到参考项目无借鉴价值。
> **维护**：每 1–2 阶段更新「状态」列（`MASTER.md` §6）。
> 状态图例：⬜ 未搬 ｜ 🟡 部分 ｜ ✅ 已搬 ｜ ➖ 不需要（说明原因）。

---

## 0. 红线（任何形式都不得进 NotDND）

- 规则书 / 模组原文：`reference/*.md`、`data/corpus/*`（含 RAG 语料）。
- 派生数据：`data/spells.json`、`data/monsters.json`、`data/species.json`、`data/backgrounds.json`、`data/campaigns/*.json`。
- 专有名词 / 品牌 / 术语：`D&D`（及其英文全称与商标）、`PHB/DMG/MM/SRD` 引用、具体法术/怪物/地名/角色名。
- 提取流水线 `tools/*.py` 的**输入与产物**（做法可学，数据禁带）。
- 本机路径（`/root/...`、`~/.workbuddy/...`）、`DND_WEB_*` 前缀、`DndWeb` 品牌串。

> **可复用的是「怎么做」，不是「做了什么」。** 一切内容按 PRISM 规则与世界观**原创重写**。

---

## 1. 后端 · HTTP 骨架与配置（→ 服务对象 M3）

| 资产 | 位置（DNDWeb） | 价值 | 复用方式 | 剥离红线 | 状态 |
|---|---|---|---|---|---|
| 单类路径链路由（GET 读 / POST 写，无框架） | `dnd_web.py` `Handler.do_GET/do_POST` | 高 | 照搬「helper + 路径 if 链」骨架，换路径名 | 路由里的 D&D 专名 | ✅ 落 `notdnd_web.py`（PR #48） |
| 响应 helper（`_send/_json/_err/_body/_sess`） | 同上 | 高 | 照搬：`Content-Length`/`no-store`/吞 `BrokenPipe`；`≤2MB` body；`X-Session` 头 | 无 | ✅ 落 `notdnd_web.py`（PR #48） |
| 静态资源 + 目录穿越防护 | `_static` | 高 | 照搬 `(DIR/rel).resolve()` 前缀校验 + MIME | 无 | ✅ 落 `notdnd_web.py`（PR #48） |
| 监听与端口约定（双环境变量 + 局域网 IP） | `HOST/PORT/lan_ip()` | 高 | 照搬「品牌端口优先 / 通用 `PORT` 回退」；保留局域网提示 | `DND_WEB_*` 前缀 | ✅ 落 `notdnd_web.py`（PR #48） |

## 2. 后端 · 会话 / 存档（→ M3）

| 资产 | 位置 | 价值 | 复用方式 | 剥离红线 | 状态 |
|---|---|---|---|---|---|
| `Session` 对象 + `to_dict` 白名单序列化 + 日志 `seq` 增量拉取 | `Session` 类 | 高 | 移植模型骨架；字段改 PRISM | D&D 字段语义 | ✅ 落 `notdnd_web.py`（PR #48）；规则快照接线见 #54 |
| 原子写落盘（`.tmp` + `os.replace`）+ 写后回查 | `Session.save` | 高 | 直接复用 | 无 | ✅ 落 `notdnd_web.py`（PR #48） |
| 加载 + **惰性迁移**（缺字段 `setdefault`，不写迁移脚本）+ 脏值降级 | `Session.load` | 高 | 照搬范式 | 缺省难度等业务语义 | ✅ 落 `notdnd_web.py`（PR #48） |
| 内存缓存 + 锁 + 惰性加载 | `get_session` | 高 | 照搬 | 无 | ✅ 落 `notdnd_web.py`（PR #48） |
| 存档列表：摘要行 + `total/truncated` 分离 + 上限 | `list_saves` | 高 | 移植「摘要 + 截断 + total」 | 战役/角色命名语义 | ✅ 落 `notdnd_web.py`（PR #48） |
| 存档改名/删除边界（不改 sid、不删当前档、`save_dir` 回传） | `do_POST /api/save/*` | 高 | 照搬边界 + `save_dir` 回传（供子进程测试） | 接口名 | ✅ 落 `notdnd_web.py`（PR #48） |

## 3. 后端 · 规则组织（→ M2；**只搬组织，不搬数值**）

| 资产 | 位置 | 价值 | 复用方式 | 剥离红线 | 状态 |
|---|---|---|---|---|---|
| 掷骰器：白名单正则 + 规模上限 + 结构化返回 | `roll` / `roll_with_advantage` | 高（结构） | 移植结构；记法/暴击阈值按 PRISM 重写 | `d20`、优劣势 5e 语义 | 🟡 结构落 `prism_core.py`（PR #49）；数值待 M2a（#53） |
| **「一次调用完成全部结算」+ 权限边界宪法** | `perform_action` + 文件头注释 | 高 | **优先照搬**：玩家只传意图、服务端权威结算、显式能力清单 | PHB 引用 | 🟡 结构落 `prism_core.py`（PR #49）；结算数值待 M2a（#53） |
| 失败代价 / 资源经济集中结算 + 单一进出口 | `apply_failure/deal_damage/heal_pc/tick_clock` | 高（组织） | 移植「函数收敛 + 禁旁路」；数值按 PRISM | 力竭/焦点/take-20 | 🟡 结构落 `prism_core.py`（PR #49）；代价曲线待 M2a（#53） |
| 战斗状态机（落盘 + 跳过无行动力 + 卡死兜底 + 去重表） | `start_combat/_advance/_current_unit` | 高（状态机） | 移植骨架；先攻/命中/AC 重写 | 5e 数值、职业特性 | 🟡 结构落 `prism_core.py`（PR #49）；战斗数值待 M2b |
| 装备/防护派生值：分类器 + 统一重算 + breakdown | `compute_ac/ac_breakdown` | 中 | 移植「重算 + breakdown」；目录全换 | 护甲类别等 5e | 🟡 结构落 `prism_core.py`（PR #49）；目录接线待 M2b |
| 成长：查表 + 派生值重算 + 服务端发放点数 | `award_xp/recompute_pc/assign_ability` | 高（组织） | 移植组织；XP 表/熟练公式换 PRISM | 5e XP/熟练公式 | 🟡 结构落 `prism_core.py`（PR #49）；XP/成长表待 M2b |

## 4. 后端 · AI 导引者模块（→ M5）

| 资产 | 位置 | 价值 | 复用方式 | 剥离红线 | 状态 |
|---|---|---|---|---|---|
| **「可选模块 + 永不崩」降级契约** | `dnd_ai.py` 头 + import 守卫 | 高 | 照搬：无 Key 也能玩、只用标准库、超时预算、失败返 `None`+兜底 | D&D DM 人设 | ⬜ |
| Key 只走请求体、`/api/ai/status` 不回显 + 回归锁 | `_cfg_get/has_key/ai_check` | 高 | 照搬（即铁律 3 的实现样板） | 无 | ⬜ |
| 意图判定做成**纯函数**（可测） | `classify_intent` | 高（组织） | 移植做法；词表/DC 重写 | 六维英文键、5e DC | ⬜ |
| 兜底规则库（分档模板 + 随机挑选） | `rules_event/npc/narrative` | 高（组织） | 移植组织；**文案全部原创重写** | 现有文案 | ⬜ |
| Prompt/上下文三层（system 硬约束 + user 局势 + 输出后处理） | `dm_turn` | 高（组织） | 移植三层；人设约束按 PRISM | 5e 术语 | ⬜ |
| RAG 检索器（零依赖关键词打分 + 中英 token + top-N 截断） | `_load_corpus/_tokens/rag_context` | 高（做法） | **只搬检索器**；语料必须原创中文 | **corpus 数据（删）** | ⬜ |
| 快照最小化 + 复用同一加值/代价算法 | `ai_snapshot/free_input` | 高 | 照搬 | snapshot 字段语义 | ⬜ |

## 5. 后端 · 数据加载（→ M2/M3）

| 资产 | 位置 | 价值 | 复用方式 | 剥离红线 | 状态 |
|---|---|---|---|---|---|
| 容错加载 + 进程缓存 + 预建索引 + 摘要列表 | `_load_ruledata/load_campaign` | 高（组织） | 移植四件套；数据全部由 NotDND 自产 | `data/*.json` 全部 | ⬜ |

## 6. 前端（→ M4；`static/`）

| 资产 | 位置 | 价值 | 复用方式 | 剥离红线 | 状态 |
|---|---|---|---|---|---|
| **单一滚动容器**主画面（剧情卡 → 叙事流 → 行动区） | `index.html` `#playScroll` + `app.css` | 高 | 移植整套 `flex/min-height:0/overflow-y:auto` 骨架 | 卡内文案 | ⬜ |
| 视图系统（`.view`/`.is-on` class 切换） | `index.html`/`app.css` | 高 | 照搬；测试断言 `is-on` 而非 `hidden` | 视图内容 | ⬜ |
| 设计令牌（`:root` 语义变量 + `color-mix` 派生） | `app.css` `:root` | 高 | 照搬命名规范，重调色板 | 奇幻语义色名 | ⬜ |
| `[hidden]{display:none!important}` | `app.css` | 高 | **必须保留** | 无 | ⬜ |
| 焦点可见 / 动效开关（prefers-reduced-motion） | `app.css` | 高 | 照搬 | 无 | ⬜ |
| `--dock-h` 实测回写（`ResizeObserver`） | `app.css`/`app.js` | 高 | 照搬「实测高度而非硬编码」 | 无 | ⬜ |
| **API 封装** `api(path)`（自动 JSON/`X-Session`/会话失效清理） | `app.js` | 高 | 几乎零改动照搬 | 会话失效关键词 | ⬜ |
| 状态→渲染分层 + `applyState` 单一入口 | `app.js` `render*` | 高 | 移植分层；各 render 内容换 PRISM | 展示内容 | ⬜ |
| 设置系统（持久化 + 坏数据回退 + 写 `<html data-*>`） | `SETTINGS_DEF/*` | 高 | 移植；设置项换 | 设置项语义 | ⬜ |
| `boot()` 竞态防护（`want=SID` 校验迟到响应） | `boot` | 高 | 照搬 | 无 | ⬜ |
| 浮层互斥状态机（`SHEETS/openSheet/…`）、通用辅助弹层、标签面板 | `app.js`/`index.html` | 高 | 照搬组件机制 | 掷骰层等交互 | ⬜ |
| 键盘/无障碍（数字快捷键、Esc 逐层、`aria-*`、焦点语义） | `app.js`/`index.html` | 高 | 照搬 | 按键含义按 PRISM | ⬜ |
| XSS `esc()` + toast + `aria-live` | `app.js` | 高 | 照搬（安全底线） | 无 | ⬜ |
| **未捕获错误收集** `window.__ERRS` | `app.js` | 高 | 照搬（低成本可观测性） | 无 | ⬜ |
| 战斗全屏覆盖视图 / 抽屉菜单 | `index.html` | 中 | 移植「覆盖层 + 独立滚动 + 结束回退」模式 | 战斗 UI 数值 | ⬜ |

## 7. 测试工程（→ M6）

| 资产 | 位置 | 价值 | 复用方式 | 剥离红线 | 状态 |
|---|---|---|---|---|---|
| `req()/check()` 微型断言 + 汇总退出码 | `tests/e2e.py` 等 | 高 | 抽成共享基座 | 用例文案 | ⬜ |
| `run_all.sh`：独立存档目录 + `trap` 收摊 + 串行 + 汇总 | `tests/run_all.sh` | 高 | 照搬骨架（改脚本名/变量名） | D&D 套件 | ⬜ |
| **进程组精确收摊**（`kill -- -$PID`）+ 端口硬校验 + 随机注入开关 | `run_all.sh` | 高 | 照搬（含坑注释） | 发布端口号 | ⬜ |
| `cleanup_servers.sh`：只杀「测试端口 + /tmp 存档」的进程 | `tests/cleanup_servers.sh` | 高 | 整脚本移植（血泪事故记录） | 服务名/端口 | ⬜ |
| **CDP-over-WebSocket** 浏览器测试（抽 `tests/_cdp.py`） | `tests/ui_check.py` 等 7 份 | 高 | 抽取共享助手，勿复制 | UI 选择器/文案 | ⬜ |
| 视口内断言 + 截图体积判据；`wait_for` 轮询 | 同上 | 高 | 照搬 | 无 | ⬜ |
| **进程内注入可控随机**（`W.roll = …`）消除 flaky | `tests/penalty_check.py` | 高 | 移植范式 | 字段名 | ⬜ |
| 存档目录「问服务端」（子进程读不到 env） | `tests/saves_check.py` | 高 | 依赖 §2 的 `save_dir` 回传 | 接口名 | ⬜ |
| 安全契约测试（骰子白名单/注入/会话隔离/路径穿越）+ AI 无 Key 测试 | `tests/e2e.py`/`ai_check.py` | 高 | 移植思路 | 骰子记法 | ⬜ |
| 概率型断言守则（先问「几次有效机会」、循环开新局、两条合法分支都收） | `handover.md` 9.x | 高 | 提炼进本项目测试约定 | 具体数值 | ⬜ |

## 8. CI / 协作模板（→ M0 已有雏形；可再对齐）

| 资产 | 位置 | 价值 | 复用方式 | 剥离红线 | 状态 |
|---|---|---|---|---|---|
| workflow：语法门先跑 + 测试期唯一依赖 + 最小权限 + 并发取消 + 失败重试 | `.github/workflows/tests.yml` | 高 | 对齐结构（NotDND 已有四门） | 被测文件名 | ➖（已自建四门，仅借鉴重试/并发） |
| PR 模板（做了什么/怎么验证/没做什么/风险/关联 Issue） | `.github/pull_request_template.md` | 高 | 已照搬 | 无 | ✅ |
| Issue 模板（目标/验收/可改/禁改/依赖） | `.github/ISSUE_TEMPLATE/task.md` | 高 | 已照搬 | 无 | ✅ |

## 9. 文档做法（→ M6）

| 资产 | 位置 | 价值 | 复用方式 | 剥离红线 | 状态 |
|---|---|---|---|---|---|
| `README.md`（面向使用者：跑起来 → 结构取舍 → 测试 → 已知边界；⚠️ 标注） | DNDWeb `README.md` | 高（结构/风格） | 同构产出 NotNND `README.md` | D&D 术语 | ⬜ |
| `handover.md`（面向改造者：每节答「为什么 + 踩过什么坑」） | DNDWeb `handover.md` | 高 | 同构产出 `HANDOVER.md` | 规则出处/数据 | ⬜ |
| **踩坑注释风格**（就地写事故与判据） | 多处 | 高 | 整体沿用 | 事故中的服务名/端口 | ⬜ |

---

## 10. 建议移植顺序（按里程碑）

1. **M2 规则核心**：§3 的**组织**（掷骰结构、权限边界宪法、代价单出入口、战斗状态机、成长重算）——**搬函数切分，重写数值/术语**。
2. **M3 会话/后端**：§1 骨架 + §2 存档模型 + §5 容错加载。
3. **M4 前端**：§6 骨架（先基础：api/状态/渲染/令牌/单一滚动容器/浮层/无障碍）。
4. **M5 AI**：§4 全部（**语料与文案原创重写**）。
5. **M6 测试/文档**：§7 测试基座 + §9 文档；对齐 §8 CI。

> 每搬一项，把本表该行状态改为 ✅ 并注明落在哪个文件/里程碑；每 1–2 阶段复查一次整表。
