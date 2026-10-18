# WORLD 设计：世界生成与经济运行时（M12-a）

> **设计单**：只产出文档，不实现代码。
> **状态**：待 Master Agent 审查（2026-10-18）。基线 `origin/master` @ `2343c4c`。
> **依据**：`GDD.md` §3 / §13 / §16；`GDD-BASELINE.md` §4.1 / §4.2 / §5「M12」行 / §6 / §10.1；
> `STATE-DESIGN.md` §5 / §6 / §7 / §11 / §12.1；`CHRON-DESIGN.md` §2.3 / §3.1 / §4 / §5 / §6 / §8.1；
> `NEURA-DESIGN.md` §4.2 / §5.2 / §7 / §8 / §9 / §12.1；`ATLAS-DESIGN.md` §3 / §4 / §5 / §6.4；
> `ACTION-DESIGN.md` §4.2 / §5.1 / §8 / §9。
> **范围**：把「描述生成世界、经济/据点运行时、ATLAS 生成校验扩展」翻译成**可拆实现单**的工程口径；
> 并把 **M11 移交项四类**（laws schema / unexplored 个体生成 / view 注入键数据源 / 事件触发重判）逐项接住。
> **一句话**：世界生成是一条「描述 → 结构」的**离线可校验**流水线（LLM 只填文本字段），
> 经济与据点是一个**事件溯源、有账可查**的宏观运行时（`blocks.world` 不透明块 + CHRON 停点日切），
> ATLAS 三文件零改动——校验器住在流水线里，生成物不过校验不能进战役。

---

## 0. 怎么用这份方案

### 0.1 给 Master Agent 的拆单口径

- 本文**不实现任何东西**；实现单见 §11 的 W1–W8，每条自带可改 / 禁改路径、依赖、标签。
- **文件独占是本里程碑的主要串行源**（`AGENTS.md` §4「同一文件不并行」）：
  - **队列 A**：W1 → W2 → W3 同占 `worldgen.py`（新文件，串行）；
  - **队列 B**：W4 → W6 → W7 同占 `world.py`（新文件，串行）；
  - **队列 C**：W3 → W5 → W7 同占 `notdnd_web.py`（串行；与 M11 的 N6–N8 同款约束）；
  - W7 里对 `neura.py` 的**最小扩展**（`view.force_due_ids` 一个键的读取，§8.4）独占 `neura.py`，
    不得与队列 A/B/C 内任何单片并行。
  - **队列 D（数据）**：W8 的 `data/schema/world.schema.json` 扩展（`laws` 数组）按 `AGENTS.md`
    数据切片纪律单独成片；`atlas.py` / `atlas_compile.py` / `atlas_gen.py` **全程零改动**（§5.3）。
- 三条代码队列之间**可以并行**（文件互斥）；W8（数据）依赖 W1 的 spec 形状冻结。
- 每条实现单都必须真跑 `bash tests/run_all.sh` 与 `bash tests/content_firewall.sh`，
  且**不得**把新文件写进 `tests/test_repo_layout.py` 的必需清单（沿用 `ACTION-DESIGN.md` §13 的实现约束）。
- **本文 §12 的 P1–P10 未拍板前**，W1–W8 的「可合并」判断以 §12 建议列为默认口径；
  拍板有出入时以拍板为准、在对应 PR 说明差异（照 `NEURA-DESIGN.md` §12.1 的回填模式）。

### 0.2 引用索引（只引用，不修改）

> 下表每一章节号均已在基线 `2343c4c` 上用 `git show origin/master:<file>` 逐一核对存在；
> 代码锚点为 worktree 行号，抽查可复核。**无编号出入需要登记。**

| 主题 | 出处（章节号 / 代码锚点） | 本文用法 |
|---|---|---|
| 三类世界 / 混合与自定义 | `GDD.md` §3.1 | §4.4 模板与 `world_type` 口径 |
| 模板与实例分离 | `GDD.md` §3.2 | §4.4 模板 = `world.module`，实例 = 种子 + 增量 + 块 |
| 三种开局方式 | `GDD.md` §3.3 | §4.4 三条入口与共享管线 |
| 规模与时长 | `GDD.md` §3.4 | §4.5 规模档与预估（不强制时长） |
| 内容生成流程 | `GDD.md` §3.5 | §4.1 七段管线的**需求来源** |
| 系统协作规则 | `GDD.md` §5.2 | §6.4 / §7.3「AI 文本 ≠ 事实」「骰点只来自 PRISM」 |
| 行动管线与结果契约 | `GDD.md` §8.2 / §8.4 | §6.4 玩家经济 / 建造动作必经 M8 管线 |
| 空间模型 | `GDD.md` §13.1 | §5.1 校验对象（结构化空间数据优先于表现层） |
| 混合空间精度 | `GDD.md` §13.2 | §5.1 三帧各自的校验口径 |
| 生成校验 | `GDD.md` §13.3 | §5 的**需求来源**（连通 / 出入口 / 唯一性 / 可达 / 成本） |
| 旅行与路径 | `GDD.md` §13.4 | §8.2 `travel_minutes` 投影的口径 |
| 建造规模阶梯 | `GDD.md` §16.1 | §6.6 / §7 首版范围（基地 → 据点） |
| 建造规则 | `GDD.md` §16.2 | §7 材料 / 劳力 / 时间 / 维护与三方分工 |
| 经济模拟 | `GDD.md` §16.3 | §6 的**需求来源**（局部供应链优先） |
| 委托式管理 | `GDD.md` §16.4 | §6.5 指令 → 目标 / 预算 / 授权边界 |
| 自动化与可解释性 | `GDD.md` §16.5 | §6.5 可解释字段与暂停 / 撤销 |
| 数据模型 / 事件最小字段 | `GDD.md` §19.1 / §19.5 | §6.2 / §9.2 账本事件与 `visibility` |
| 存档兼容 | `GDD.md` §19.4 | §6.1 惰性迁移 |
| 降级 | `GDD.md` §20.4 | §4.3 无模型时的离线路径 |
| 不变量 / 测试矩阵 / 可观测 | `GDD.md` §24.1 / §24.2 / §24.3 | §10 可测口径 |
| 模块落点与依赖方向 | `GDD-BASELINE.md` §4.1 / §4.2 | §9.1 新模块落点（名称待 Master 确认，§12 P1）、禁止反向 import |
| M12 出口条件 | `GDD-BASELINE.md` §5「M12」行 | §10 两条出口逐条断言化 |
| 通用验收 | `GDD-BASELINE.md` §6 | §11 每片验收的公共项 |
| P4 落点映射 | `GDD-BASELINE.md` §10.1 | §1.1 定位依据 |
| A0 一次提交一条事件 | `STATE-DESIGN.md` §5.0 | §6.2 / §8.4 每 tick 每域至多一条事件 |
| 事件封套 / `visibility` | `STATE-DESIGN.md` §5.1 | §9.2 事件类型登记口径 |
| 不透明块与惰性迁移 | `STATE-DESIGN.md` §6.1 / §10.1 / §11 | §6.1 `blocks.world` 是同级不透明块 |
| 幂等键 | `STATE-DESIGN.md` §7 | §6.4 / §7.1 工程与经济动作的键格式 |
| 提交接口 | `STATE-DESIGN.md` §12.1；`state.py:877` `Handle.commit` | §6.2 唯一提交出口 |
| 提交出口 / 重放 | `state.py:759` `view` / `:839` `replay` | §6.2 余额可由事件重放复核 |
| 任意块名机械写入 | `state.py:226` `_apply_change` / `:491` `_genesis_blocks` | §6.1 `blocks.world` **零改动 `state.py`** |
| 时间单位唯一真相 | `CHRON-DESIGN.md` §2.3 | §6.3 经济不定义任何分钟常数 |
| 世界时钟形状 | `CHRON-DESIGN.md` §3.1；`chron.py:2455` `world_time_minute` | §6.3 只读、提交时传入 |
| 行动生命周期（八状态） | `CHRON-DESIGN.md` §4.2 | §7.3 建造工程**进** `chron.actions`（与 NEURA 计划相反，理由在 §7.3） |
| 调度器入口 | `CHRON-DESIGN.md` §4.3；`chron.py:2057` `advance_to` | §6.3 日切 tick 的唯一驱动源 |
| 稳定到期排序 / 屏障 | `CHRON-DESIGN.md` §5.2 / §6.2；`chron.py:1985` `next_barrier` | §6.3 屏障处不跑经济日切（照 NEURA §7.3 口径） |
| `chron` 存档块范本 | `CHRON-DESIGN.md` §8.1 / Key Decisions 11；`chron.py:2283` `new_block` / `:2323` `migrate_block` | §6.1 `world` 块照同款形状（版本 / 惰性迁移 / 脏值降级） |
| NPC 运行时字段表 | `NEURA-DESIGN.md` §4.2 | §8.3 / §8.5 种群初始化的填充对象（字段级对齐） |
| NEURA 硬约束 / `view.laws` 消费方 | `NEURA-DESIGN.md` §5.2 / §5.2.1；`neura.py:111` | §8.1 laws schema 的**唯一消费方契约** |
| 四层判定 / 频率 | `NEURA-DESIGN.md` §7.1 / §7.2；`neura.py:2310` `layer_of` | §8.3 unexplored 口径 / §8.4 重判落点 |
| 补算 | `NEURA-DESIGN.md` §7.4 | §8.4 重判**不与补算合并**的理由 |
| 预算模型 | `NEURA-DESIGN.md` §8.2 | §4.3 生成预算沿用「记账 + 单调降级」模式 |
| NEURA 编排挂接 | `NEURA-DESIGN.md` §9.1 / §9.6；`notdnd_web.py:2997` `_neura_view` / `:3129` `_neura_tick` / `:3872` `_chron_advance` | §8.2 / §8.4 复用同一条 tick 链 |
| M11 移交登记 | `NEURA-DESIGN.md` §12.1 P8 / P10 | §8.1 / §8.2 的**移交来源** |
| 地点形状 / 标识 / 内核签名 | `ATLAS-DESIGN.md` §3.1 / §3.2 / §3.3 | §5.1 校验器的对象模型（不改签名） |
| 词表与扫描 | `ATLAS-DESIGN.md` §4.2 | §4.3 生成世界必须带 `atlas.lexicon` 覆盖项 |
| 世界尺度 / 摆放 / 地点生成 | `ATLAS-DESIGN.md` §5.2 / §5.4 / §5.5；`atlas_compile.py:421` `compile_world`；`atlas_gen.py:325` `generate_site` | §4.1 第 4 段直接复用 |
| 增删世界 | `ATLAS-DESIGN.md` §6.4 | §4.4 新世界 = 新 `world.module`，不改地图代码 |
| 提案 / 结果契约 | `ACTION-DESIGN.md` §4.2 / §5.1 | §6.4 / §7.1 提案**不新增字段** |
| 排程钩子 | `ACTION-DESIGN.md` §8.2 / §8.3 | §7.3 建造工程排程走既有两层钩子 |
| 写接口与幂等键 | `ACTION-DESIGN.md` §9.1 / §9.4 | §6.4 玩家经济动作的入口 |
| 每成员视角过滤 | `MULTIPLAYER-DESIGN.md` §6.2 / §6.3 / §6.4 | §9.2 经济事件的 `visibility` 与交付过滤 |
| 单回合预算 | `GUIDE-DESIGN.md` §5.11 | §4.3 生成预算的既有先例 |

---

## 1. 目标与非目标

### 1.1 目标

1. **「描述 → 世界」是一条可校验的流水线**：`GDD.md` §3.5 的七步（描述 → 需求结构化 →
   模板 / 规则选择 → 地图与实体生成 → 一致性检查 → 预览 / 修订 → 创建战役）每一步都有
   纯函数落点与**离线可跑**的校验（§4 / §5）；模型层只负责「文本字段」，结构永远由纯函数合成。
2. **生成物结构有效是断言，不是感觉**：连通性、出入口、位置唯一性、关键地点可达、
   移动成本一致性逐条机器可判（§5.1 / §10.1）；校验不过的地图不能进战役，允许局部修复。
3. **经济动作有账可查**：每一笔经济 / 建造变更都是一条事件（A0），余额与库存是**可由事件
   重放复核的派生值**；账本摘要落 `blocks.world`，硬上限、惰性迁移、可解释（§6.2）。
4. **经济运行时不新建时钟**：与 NEURA 同款——只在 CHRON 的停点上工作，日切结算、
   幂等、可回溯（§6.3）；玩家经济 / 建造动作必经 M8 管线（§6.4）。
5. **M11 移交项四类逐项有落点**：laws schema（§8.1）、unexplored 个体生成（§8.3）、
   view 注入键数据源（§8.2）、事件触发重判（§8.4）——另加两项随迁（种群初始化 / `travel_minutes`，§8.5）。
6. **能拆成可执行单**：W1–W8 可按文件队列并行 / 串行合入（§11）。

### 1.2 非目标（明确不做）

- **不做全量全球经济**：`GDD.md` §16.3 明文「首发只需要玩家行动会直接影响的局部供应链」——
  不模拟每件商品的全球市场、不做跨区物流网络、不做金融衍生（税收 / 债务只在据点层记账）。
- **不做叙事**：世界描述文本、生成世界的开场白归 LOREX / `prism_guide`；`worldgen` 只出结构。
- **不做图片 / 瓦片地图**：`ATLAS-DESIGN.md` §1「不做」清单继续有效。
- **不做编辑器与模组 UI**：`GDD.md` §3.1「混合与自定义世界」的编辑器界面属 M13 平台化；
  本文只提供**结构上可编辑**的数据（模板 + overrides 补丁，§4.4）。
- **不做公网账号 / 审核**：M13。
- **不改 `prism_core.py` 的判定语义**：经济结算需要骰点时走既有 PRISM 入口，不为经济
  新增掷骰通道（同 `NEURA-DESIGN.md` §12.1 P2 的克制）。
- **不改任何既有文件**（本设计单）：代码、`data/**`、`docs/**`、`tests/**`、治理与设计文档全部禁改。

### 1.3 一句话设计

> **WORLD = 一条（`worldgen`）把描述变成「过了校验的世界模组」的纯函数流水线
> + 一个（`world.py`）把经济与工程变成「有账可查的事件」的宏观运行时。**
> 它生成的每张地图都过了同一套离线校验，它记的每一笔账都能用事件日志重放出来，
> 它推进的时间只有 CHRON 给的分钟，它接住了 M11 留下的每一个洞。

---

## 2. 现状盘点（只读输入）

### 2.1 已经能复用的（代码证据）

| 能力 | 位置 | 复用方式 |
|---|---|---|
| 唯一提交出口 + 事件日志 + 重放 | `state.py:877` `Handle.commit` / `:759` `view` / `:839` `replay` | §6.2 账本的**事件溯源地基**；只调用，不 import 实现细节 |
| 不透明块可加新块名 | `state.py:226` `_apply_change`（按 `block` 名机械写入）；`state.py:491` `_genesis_blocks` 只列 `rules/atlas/guide` | 提交 `{"block":"world","op":"replace_block"}` 即可，**`state.py` 零改动** |
| `chron` 块的成熟形状 | `chron.py:2283` `new_block` / `:2323` `migrate_block` | §6.1 `world` 块照同一形状（版本 / 惰性迁移 / 脏值降级） |
| 唯一世界时钟与停点 | `chron.py:2057` `advance_to` / `:1985` `next_barrier` / `:2455` `world_time_minute` | §6.3 日切只读挂接，**不新建时钟** |
| NEURA 的 tick 挂接先例 | `notdnd_web.py:3129` `_neura_tick`（插在 `_chron_advance:3872` 锁内玩家卡结算后） | §6.3 `_world_tick` **照同款时序**插入（neura 之后），失败隔离同款 |
| NEURA 的 view 构造先例 | `notdnd_web.py:2997` `_neura_view` | §8.2 世界事实投影的**注入点**（扩充该函数的缺省空键） |
| 行动管线（提案→验证→结算→提交） | `notdnd_web.py:2736` `_action_settle_commit`；提案 schema 见 `ACTION-DESIGN.md` §4.2 | §6.4 / §7.1 玩家经济 / 建造动作**内部分支**复用，不新增结算路径 |
| 世界模组编译 | `atlas_compile.py:421` `compile_world(world, lexicon, seed)`（同输入同结果，附 `scale/world_traits/region_info`） | §4.1 第 4 段直接复用；§4.3 合成器的**下游契约** |
| 地点帧 / 战术帧生成 | `atlas_gen.py:325` `generate_site` / `:378` `generate_tactical`（种子稳定、帧已存在不重复生成） | §4.1 / §5.2 直接复用 |
| 地图内核 | `atlas.py:120` `add_place` / `:149` `add_link` / `:179` `find_place` / `:223` `exits` / `:335` `range_band` / `:391` `apply_delta` / `:445` `export_state` / `:497` `restore_state` | §5.1 校验器**只读遍历**这些结构；§6.3 `range_band` 喂 `travel_minutes` |
| 词表机制 | `data/atlas/lexicon.json`；`ATLAS-DESIGN.md` §4.2 | §4.3 生成世界的 region 短文必须命中词表（新比喻 → 加词条） |
| 现有三个世界 + 测试夹具 | `data/worlds/{threshold,emberfall,entropic-net}.json`；`tests/fixtures/atlas_fiction_world.json` | §5.1 校验器的**回归对象**（三世界必须零 error 通过） |
| 世界类型显式声明 | `data/schema/world.schema.json` `world.world_type ∈ {fantasy, modern, future}`（缺失落 `modern`，不推导） | §4.4 模板选择只认该枚举 |
| 静态 NPC 档案 | `data/schema/scenario.schema.json` `$defs.npc`（id/name/role/faction/drive/…/secret/attitude/knows…） | §8.3 / §8.5 生成与初始化的**目标 schema**（生成 NPC 不发明新字段） |
| NPC 运行时块 | `NEURA-DESIGN.md` §4.2 字段表 | §8.5 种群初始化的填充对象 |
| 排程钩子两层 | `ACTION-DESIGN.md` §8.2 / §8.3 | §7.3 建造工程进 CHRON 的既有通道 |
| 每成员视角过滤 | `notdnd_web.py` `_room_event_visible`；`MULTIPLAYER-DESIGN.md` §6.2–§6.4 | §9.2 经济事件交付过滤（复用，不新建） |
| 导引者预算先例 | `GUIDE-DESIGN.md` §5.11；`NEURA-DESIGN.md` §8.2 | §4.3 生成预算沿用「记账 + 单调降级 + 离线兜底」三件套 |

### 2.2 缺口（本里程碑要补的）

| 缺口 | 现状 | 本文落点 |
|---|---|---|
| 「描述 → 世界」没有结构化入口 | 玩家描述只能靠人工手写 `world.module`；无需求结构化、无模板选择、无合成器 | §4 |
| 生成 / 编译结果**没有校验器** | `compile_world` / `generate_site` 后无连通性、出入口、可达性、唯一性检查；坏图要等玩家走进死路才暴露 | §5 |
| **经济运行时为零** | `system.economy` / `system.stronghold` 只是 PRISM 规则数据（物价参考 / 维护等级），无任何运行时账本、库存、结算 | §6 |
| **建造运行时为零** | 无工程对象、无里程碑、无劳动力 / 维护扣账 | §7 |
| **M11 移交 ①：`laws` schema 未定**（P8） | `view.laws = []` + `law_source="none"`（`neura.py:111` 只认 `allow/tolerate` 两个容忍值）；世界包无结构化法律表 | §8.1 |
| **M11 移交 ②：unexplored 层个体生成** | `layer_of`（`neura.py:2310`）对未具名条目返回 `unexplored`、`due_npcs` 永不调度；人口摘要信号无接收方、个体生成无人做 | §8.3 |
| **M11 移交 ③：view 注入键无数据源** | `_neura_view`（`notdnd_web.py:2997`）把 `known_refs / main_thread_refs / faction_refs / importance_overrides` 写死为空（PR #364 裁定 4/5：threads / faction 档案 / 显式 importance 表归 M12）；`world_facts = {}`、`travel_minutes = {}` 同空 | §8.2 |
| **M11 移交 ④：事件触发远场重判未做**（PR #371 裁定 4） | `important_far` 只有日切触发（日差 ≥ 1）；「事件命中 → 同日重判 ≤ 4 次」缺匹配语义与预算载体 | §8.4 |
| **随迁 ⑤：NPC 种群初始化未做**（PR #371「没做什么」） | `blocks.neura.npcs` 生产会话恒为空（测试用手工夹具）；静态档案 → 运行时块的导入无人做 | §8.5 |
| **随迁 ⑥：`travel_minutes` 无口径** | view 留空 → 远距离移动全被 `E_DISTANCE` 拦下（PR #371 裁定：按图距离折算分钟属新时间常数口径） | §8.2 / §8.5 |
| 无只读观察面 | 无法回答「这个月的粮账 / 这项工程为什么停」 | §10.3（可观测）+ W8 |

### 2.3 需要小心的既有口径

1. **`world.world_type` 不推导**：`data/schema/world.schema.json` 明文「缺失或不认识时由读取层落成
   `modern`，不得用 engine、名称或 tone_pack 推导」。§4.4 的模板选择**只认显式枚举**；
   描述生成的第一步若没能从描述里结构化出 `world_type`，落 `modern`，不猜。
2. **`engine ∈ {tactics, build}` 决定规则包，不决定空间结构**：编译器（`atlas_compile`）对两种
   engine 一视同仁。§4.3 的合成器不得按 engine 分叉空间结构。
3. **词表条目禁用键名 `source`**（`ATLAS-DESIGN.md` §4.2）：生成世界产出的 `atlas.lexicon`
   覆盖项同样遵守——校验器会把它当 `docs/...md:行号` 处理（`tests/validate_data.py`）。
4. **`chron.Action` 的八状态带玩家语义**：NEURA 计划**不进** `chron.actions`（`NEURA-DESIGN.md`
   §12.1 P7）；建造工程**进**（§7.3）——两者并列时不得互相「顺手统一」。
5. **NEURA 的预算账本 4 键形状已被验收钉死**（`{day, strong_calls, light_calls, tokens}`，
   PR #364 裁定 3）：经济的账本**另立**在 `blocks.world`，不塞进 `blocks.neura.budget`。
6. **`neura.tick` 的幂等键是 `ntick_<分钟>`**（PR #371）：`world` 块的日切事件用**独立键格式**
   `wtick_<分钟>`，避免同一分钟两条域级提交互相覆盖语义。
7. **`tests/test_repo_layout.py` 的必需清单不得加新文件**（沿用 ACTION-DESIGN §13 约束）：
   `worldgen.py` / `world.py` 是「删除仍可 import」的增强模块，不是必需件。

---

## 3. 设计决定（先拍板，后拆单）

| # | 决定 | 理由 / 依据 |
|---|---|---|
| **V1** | 新增 `worldgen.py`（世界生成流水线）：需求结构化、模板选择、合成、**校验与修复**全部是纯函数；模型层只通过注入的端口填**文本字段**（名称 / 摘要 / 术语 / 历史），LLM 不可用时整条流水线仍可跑（离线兜底：结构化输入直填 + 模板文本库） | `GDD.md` §20.4；`GDD-BASELINE.md` §4.2 依赖方向；铁律 2（零第三方运行时依赖） |
| **V2** | 新增 `world.py`（经济 / 据点 / 建造运行时）：**只产出账本变更与工程里程碑提案**，不写状态、不落库、不 import 业务模块；编排层构造 `view`、执行提交（与 NEURA 同款纪律） | `GDD-BASELINE.md` §4.1 / §4.2；`NEURA-DESIGN.md` §0.1 同款先例 |
| **V3** | 运行时态全部落在 **`blocks.world`**（与 `rules/atlas/guide/chron/neura` 同级的不透明块），带 `WORLD_VERSION` 与惰性迁移，块形状照 `chron` 块范本 | `STATE-DESIGN.md` §6.1 / §10.1 / §11；`state.py:226` 已支持任意块名 |
| **V4** | **账本 = 事件溯源**：每一笔经济 / 建造变更都是一条事件；`blocks.world.ledger` 只存**滚动摘要**（余额快照 + 近窗明细），权威是事件日志——`replay` 重放出的余额必须与摘要一致，不一致以事件为准并触发迁移告警 | `STATE-DESIGN.md` §5.0 / §6.2；`GDD.md` §16.5 可解释性 |
| **V5** | **不新建时钟**：经济日切由 `advance_to` 停点驱动（`_chron_advance` 内、`_neura_tick` 之后插 `_world_tick`）；屏障处（`stopped_at != None`）不跑日切，跨屏障的日切在下一个非屏障停点**补结算一次**（幂等键 `wtick_<day>`） | `CHRON-DESIGN.md` §4.3 / §6.2；`NEURA-DESIGN.md` §7.3 同款 |
| **V6** | 玩家发起的经济 / 建造动作**必经 M8 管线**（`_action_settle_commit` 内部分支），提案契约**不新增字段**；NPC / 势力的自发经济行为由 `world.tick` 以规则提案走同一条管线（`source="npc"`，NEURA 先例） | `GDD.md` §8.2 / §5.2；`ACTION-DESIGN.md` §4.2；`NEURA-DESIGN.md` §5.6 U9 |
| **V7** | **建造工程进 `chron.actions`**（玩家可见的长行动，走排程钩子两层）；纯背景工程（NPC 势力自建）不进队列，用 `blocks.world` 内 `project.plan` + 日切推进（P7 同款）——**两条通道并存，理由见 §7.3** | `CHRON-DESIGN.md` §4.2；`ACTION-DESIGN.md` §8.2；对照 `NEURA-DESIGN.md` §12.1 P7 的反例 |
| **V8** | ATLAS 三文件（`atlas.py` / `atlas_compile.py` / `atlas_gen.py`）**零改动**：校验器与修复器住在 `worldgen.py`，只读 `atlas.py` 的公开数据结构与查询函数 | `ATLAS-DESIGN.md` §3.3「后续 Issue 只调用、不改签名」；避免与空间队列冲突 |
| **V9** | `laws` schema 定为 `world.module` 顶层 `laws` 数组：条目形状**逐字段对齐 `neura._laws` 的消费契约**（`id/name/forbid_tags/scope_place_ids/threshold/penalty`），`law_source="world"`；`data/schema/world.schema.json` 扩展为独立数据片 | `NEURA-DESIGN.md` §12.1 P8；`neura.py:111` 两个容忍值契约；§8.1 |
| **V10** | view 注入键的数据源 = **世界事实投影**（`world.py` 提供纯函数 `projection(session_state) -> dict`，编排层在 `_neura_view` 里调用）：`known_refs` 来自线索事件、`main_thread_refs` 来自剧本 threads、`faction_refs` 来自势力成员表、`importance_overrides` 来自显式覆盖表、`travel_minutes` 来自 `range_band` × 行程档、`world_facts` 来自 `blocks.world.facts` | PR #364 裁定 4/5（数据源归 M12）；`GDD.md` §13.4；§8.2 |
| **V11** | unexplored 个体生成 = **种子确定性合成 + 可选模型润色**：进入相邻地点时按种子生成「够用即可」的具名 NPC（过 `scenario.schema.json` `$defs.npc`），模型只润色姓名 / 传闻文本；人口摘要信号以 `world.py` 为接收方 | PR #371 裁定 5（接收方归 M12）；`GDD.md` §11.5 模拟分层；§8.3 |
| **V12** | 事件触发重判 = 投影层的**匹配表**（事件 → 受影响 npc_id 集）+ 同日预算计数器（`blocks.world.rejudge`）+ `view.force_due_ids` 注入；NEURA 侧**只加一个缺省为空的 view 键读取**，缺键 = 行为与 M11 逐字节一致 | PR #371 裁定 4（≤ 4 次/日）；`GDD.md` §11.5；§8.4 |
| **V13** | 模板 = `data/worlds/*.json`（`world.module`）；实例 = 种子 + 增量 + 块；混合开局 = 模板 + **overrides 补丁**（声明式、带版本、可 diff 预览）；模板继承不做多层，单层覆盖起步 | `GDD.md` §3.2 / §3.3；`ATLAS-DESIGN.md` §6.4；§4.4 |
| **V14** | 生成预算：一次生成会话的模型调用记在**会话级临时账本**（不落 `blocks.world`——生成发生在战役创建之前，还没有块可落），上限常量一处可调；超限走模板兜底文本 | `GDD.md` §20.5；`GUIDE-DESIGN.md` §5.11 先例；§4.3 |
| **V15** | 规模档 `scale ∈ {sketch, region, domain}`（对应 §3.4 短篇 / 中型 / 长期沙盒）：决定区域数、关键地点数、NPC 预算与经济开档档位；**预估只展示、不强制**（`GDD.md` §3.4 明文） | `GDD.md` §3.4 / §16.1；§4.5 |
