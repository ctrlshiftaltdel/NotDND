# HANDOVER.md —— 交给下一位改造者

> 本文件面向要**改造这个仓库**的人（或 Agent）。它不复述使用说明——怎么跑看
> [`README.md`](README.md)，怎么协作看 [`AGENTS.md`](AGENTS.md)，主管怎么接手看
> [`MASTER.md`](MASTER.md)。这里只回答两个问题：**每个结构为什么是现在这样**，
> 以及**前人踩过哪些坑**。每条都给出处（文件 / PR），可逐条核对。
>
> 阅读顺序建议：本文件 §1 → 你要改的模块对应的 `.py` 文件头注释 → 相关设计文档
> （`GUIDE-DESIGN.md` / `ATLAS-DESIGN.md` / `data/FORMAT.md`）。

---

## 1. 模块地图与边界

| 模块 | 职责 | 不做什么 |
|---|---|---|
| `prism_core.py` | 离线规则核心：判定 / 战斗 / 构建 / 成长 / 派生值 | 不碰 HTTP、不碰叙事文案 |
| `prism_guide.py` | 可选 AI 导引者：提示词、传输、实相、对白、秘密门 | 不 import `notdnd_web`；不改 `prism_core.py` |
| `notdnd_web.py` | 会话 / 存档 / HTTP / SSE 接线 | 不含规则数值、不含世界内容 |
| `atlas.py` | 自动地图**内核**：帧 / 地点 / 连接 / 移动 / 投影 | 不知道任何世界名字，不读 `data/worlds/`，不调模型 |
| `atlas_compile.py` | 世界编译器：`world.module` JSON + 词表 → 世界帧 | 不出现 `if world_key == ...`，一切差异来自词表命中 |
| `atlas_gen.py` | 地点帧 / 战术帧生成器 | 不读世界模组，同种子同结果 |

**为什么「规则会话核心」与「AI 导引者」分成两个模块：**

1. **离线可玩是底线。** `prism_guide` 是**可选模块**：import 失败（缺文件 /
   语法错 / 缺依赖）时网页层照常工作，导引路由返回固定 JSON 兜底、不结算
   （`notdnd_web.py` 顶部 `try: import prism_guide` 一段）。规则核心若与导引者
   耦合，断网 = 产品废掉。
2. **依赖方向单向**：`notdnd_web → prism_guide → prism_core`，导引者的 `settle`
   走 `prism_core` 公开入口结算后写回（`prism_guide.py` 模块头 G2 节），反向
   import 一律不允许。
3. **规则结果只有一个出口。** 玩家侧永不提交结果——骰值、伤害、资源增减一律
   不接受；`deal_damage` / `heal_unit` / `change_resource` 是唯一出口
   （`prism_core.py` 模块头【权限边界】一节）。导引者无论多聪明，都只能通过这
   几个口子改状态，AI 说了不算数。
4. **规则函数只返回结构化结果，叙事归调用方**（`prism_core.py` 模块头「设计
   约定」）。这样规则核心可以被导引者、HTTP 层、未来任何前端复用。

**改造约定：** 三个规则/地图模块 `import` 均**无副作用**（不触磁盘、不起线程）；
所有跨请求状态都是可直接 JSON 序列化的纯数据，**不能只活在内存里**——存档要能
落盘、能还原（`prism_core.py` / `atlas.py` 模块头「设计约定」）。

---

## 2. 数据层：`docs/` → `data/`

结构：`docs/`（PRISM 规则与剧本**源文档**，可修订但走独立 Issue/PR）→
`data/`（JSON，「加文件即加内容」）→ `tests/validate_data.py`（零依赖校验器）。
口径全文见 [`data/FORMAT.md`](data/FORMAT.md)，要点：

- **封套**：每个数据文件顶层 `{schema_version, kind, 族载荷}`；`kind =
  <family>.<name>` 决定用哪份 schema（`data/FORMAT.md` §2）。
- **登记表**：`kind → schema` 的唯一权威是 `data/schema/registry.json`；每个
  kind 一份**平铺** schema。唯一例外 `system.attributes` 沿用历史文件名
  `system.schema.json`——既有回归测试锁定了它，不得改名（`data/FORMAT.md`
  §2、§4.1 裁定框）。
- **canonical 键与别名**：`docs/` 内部命名不一致，写 JSON 只用 canonical 键，
  读取时兼容别名（`data/FORMAT.md` §3）。
- **`source` 溯源**：每个实体带 `source: "docs/…md:行号"`，逐字指回源文档
  行；**散文不进 JSON**，长篇叙事留在 `docs/`（`data/FORMAT.md` 开头说明与
  §5–§6）。

**为什么校验器零依赖**：仓库铁律 2 是「零第三方运行时依赖」（`AGENTS.md` §2）。
校验器若引 `jsonschema`，每台开发机、每次 CI 都要装包，与铁律冲突；所以
`tests/validate_data.py` 自实现了一个**有穷的 JSON Schema 关键字子集**（16 个
校验关键字 + 4 个注解，`data/FORMAT.md` §7）。schema 里出现未支持关键字直接
报错——**写 schema 前先查第 7 节**，`minItems` / `maxItems` 等不在子集里。

**踩过的坑：**

- `docs/` 曾有 20 个中文文件名，内容防火墙按路径 grep 时中文名被静默跳过
  （fail-open）。已全量迁移为英文 slug（#102 / PR #104）。**新增 docs 文件一律
  ASCII `[a-z0-9-]`**；处理 git 输出必须 `git ls-files -z` 与
  `git -c core.quotepath=false grep`。
- 硬编码路径要同步两处：`tests/test_data.py` 拼 `docs/system/<file>.md` 的位置、
  `tests/fixtures/good/system_attributes_min.json` 的 `source`（校验器对它做
  存在性检查）。
- 数据切片**彼此串行**：`data/system/**`、`data/worlds/**`、
  `data/schema/registry.json`、`data/FORMAT.md` 是共享文件，并行改必然冲突
  （`MASTER.md` §3）。

---

## 3. 导引者关键决策（入口：[`GUIDE-DESIGN.md`](GUIDE-DESIGN.md)）

导引者要干三件事（§2）：把地点卡走成可印证的街道图（**实相**）、演已有人物
（场外痕迹 / 对白 / 秘密门）、同世界同剧本**每局不同**（`guide.salt`）。关键
决策与理由：

- **L0–L4 分层消息数组**（§5.4）：L0 世界切片、L1 典范卡（绑定后才从那一份
  剧本生成，纯函数、salt 不进 L1）、L2 实相摘要、L3 历史叙事、L4 本回合。
  为什么：**缓存导向**——稳定内容放前缀，`cache_hit_ratio` 直接决定费用
  （`prism_guide.py` 模块头 G1/G5 节）。改 L1/L2 构建必须同步相关回归测试的
  字节级断言。
- **实相每「存档 × 地点」只生成一次并落盘**（§5.7）：锁内写 `pending` 并
  `save`（claim 机制防并发重画），成功后存 `guide.realizations`，此时才把
  salt 与实相摘要写进 L2。为什么：一次生成要 4096 token 的非流式请求，
  重画一次烧一次钱。
- **秘密门**（§2.2）：NPC 的 `secret` 在进 L3 与切拍之前过
  `redact_narration`，未放行的命中换成「……」，**不**第二次叫模型。放行条件
  只有两条：玩家原文含该 NPC 的 `lever` 整段，或已知线索与该 NPC 的 `knows`
  相交（`prism_guide.py` 模块头 G7 节）。为什么：秘密只能被玩家**玩出来**，
  不能被模型随口漏掉。秘密用文件全文（非 L1 的截断版）匹配。
- **场外痕迹与揭开**：焦点**离开**某地点时为在场的至多 4 名具名 NPC 记痕迹；
  痕迹 `at` = **离开的地点**；揭开发生在**同一回合**的叙事里
  （`narration.trace`，服务端写）。（§2.2、`MASTER.md` §5 揭开口径裁定。）
- **对白声线按 `npc.id`**：FNV-1a 低位分配白桦 / 茉莉，按 `npc.id` 写进
  `guide.voices`，同存档不重算。**不按姓名推断音色、不按性别表**
  （`prism_guide.py` G3/G7 节）。`speak` 只接受 `last_beats` 里的全文——
  防止拿任意文本白嫖 TTS。
- **`bind` 显式绑定**：`scenario_id` 只能是 `data/scenarios/<id>.json` 的文件名
  主干，不扫目录、不按战役名搜索（G5 节）。为什么：读剧本只读点名那一份，
  不给路径遍历与猜测留口子。
- **所有上游调用走单一入口 `TRANSMIT`**：测试可整体换成假传输，不碰网络
  （G2 节）。密钥只放请求头 `api-key`，不进 URL / JSON / 状态 / 日志。
- **产品路径不读 `data/scenarios/` 与 `data/worlds/` 之外的东西**：绑定前不猜
  战役、实相不改 ATLAS 不带坐标——地图归 ATLAS，导引者只管「走成什么样」。

---

## 4. 踩坑清单（改导引者 / 测试前必读）

1. **非流式回包是整段 JSON，不是 SSE**（#109 → PR #112）。上游在
   `stream: false` 时返回一整个 JSON 对象（`choices[0].message` +
   顶层 `usage`），`stream: true` 时才是 `data:` 行。组装函数两种都要认：
   先判整段 JSON，再走 SSE 解析。**传输层测试必须同时喂两种字节。**
2. **`urllib` 的 `timeout` 是每次 socket 读的超时，不是墙钟上限**。实相请求
   开思考 + 4096 token 一次非流式实测可达数十秒（见 `GUIDE-DESIGN.md` §5.11
   「75 秒的来源」），因此各请求分级设时间盒——**具体秒数以 `prism_guide.py`
   的 `*_TIMEOUT_S` 常量现值为准**，不在此抄写（实相时间盒 40→75 的来龙去脉
   见 #113 → PR #126）。实相的「硬失败再请求一次」两次**共用**同一个时间盒：
   第一次用满，第二次只剩剩余预算，总预算不翻倍。调整时间盒是行为变更，
   要同步 `GUIDE-DESIGN.md` §5.11。
3. **`thinking` 开关按请求类型区别**：叙事 = 思考关 + 温度 0.7 + 700 token；
   工具预通行 = 思考开、不带温度、1024、非流式、最多 3 轮；实相 = 思考开 +
   4096、非流式、无工具；痕迹 = 思考关 + 512（§5.5 表）。带 `tool_calls` 的
   助手消息**必须**同时带 `reasoning_content`，否则供应商返回 400。
4. **仓库文件名一律 ASCII**（2026-10-09 拍板，#102 / PR #104 迁移完毕）。
   新增文件（含 docs、数据、测试）不要引入非 ASCII 文件名。
5. **内容边界**（铁律 1）：允许借鉴通用机制与题材设定；禁止照抄 / 翻译 /
   改名 / 微调第三方出版物的表达。CI 的内容防火墙门对禁止路径、高风险品牌
   标识清单、密钥与隐私模式做 grep（`tests/content_firewall.sh`）。六维属性
   用 PRISM 名（力道 MGT / 灵巧 FIN / 体魄 VIG / 洞察 INS / 心智 MND /
   气场 PRE）；通用属性名仅作同义词，**不**列入防火墙侵权标识。
6. **测试收摊的进程组加固**（PR #117 / #119）：子进程起服务必须整组收摊。
   Windows 没有 `os.killpg`，收摊代码的 except 元组必须同时接住
   `AttributeError`——否则收摊失败不被捕获，本机假红（POSIX 行为不变，
   PR #119 修过一次）。起服务的手法照抄 `tests/test_notdnd_web.py`：
   子进程 + 随机端口 + 临时存档目录 + 必收摊。
7. **内容防火墙的探针残留**：`tests/test_content_firewall.py` 跑完会撤销探针；
   若测试进程被 SIGTERM 打断（例如前台跑全量回归超时被杀），探针文件会留在
   磁盘并已 `git add -N`，之后**连环假红**，且探针逻辑对已入索引的路径不会
   自动清理。修法：`git reset -q -- <探针路径>` 再删文件，重跑即绿。全量回归
   约 3 分钟，跑的时候别放在会被超时打断的前台。
8. **工作区不干净会让防火墙测试误报**：`check_workspace_clean` 断言工作区干净，
   任何未提交改动都会触发「探针撤销后仍有残留」的报错——先提交再判断是不是
   真回归。

---

## 5. 测试与协作

- **一键回归**：`bash tests/run_all.sh`——收集 `tests/test_*.py` 共 12 个脚本
  串行执行，退出码即判据（`tests/run_all.sh`）。写作约定（零依赖、随机端口 +
  临时目录 + 收摊、概率断言守则、浏览器测试优雅跳过）见 [`tests/README.md`](tests/README.md)；
  无头浏览器 / WebSocket 共用助手在 `tests/_cdp.py`。
- **CI 四道门**（`.github/workflows/ci.yml`）：语法门（`py_compile`）→ 回归门
  （`run_all.sh`）→ 内容防火墙门 → 提交邮箱门（作者邮箱一律 GitHub noreply，
  铁律 3）。只读权限、不引用 secrets、不用第三方 marketplace action。
- **协作契约**（[`AGENTS.md`](AGENTS.md)）：三条铁律（内容边界 / 零第三方运行时
  依赖 / 密钥与隐私永不入库）、一个 Issue 一件事、一个意图 = 一个分支 = 一个
  PR、四行审查格式、**实现 Agent 不合并**。主管的接手与路线图在
  [`MASTER.md`](MASTER.md)。
- **交回前 rebase**：分支落后时先按 `AGENTS.md` §7 更新，CI 以 rebase 后为准
  （`MASTER.md` §3）。
- **文档分工**：`README.md` = 跑起来（面向使用者）；本文件 = 为什么 + 坑（面向
  改造者）；`tests/README.md` = 测试约定；设计取舍的全文在
  `GUIDE-DESIGN.md`（导引者）与 `ATLAS-DESIGN.md`（自动地图）。
  注意：`tests/test_repo_layout.py` 的 `REQUIRED_FILES` 锁定的是仓库骨架最小集，
  **不含**本文件——别把它加进必需清单去「顺手完善」。

---

*发现本文件与代码行为不符？以代码为准修文档，单独开 Issue/PR，别夹带在功能改动里。*
