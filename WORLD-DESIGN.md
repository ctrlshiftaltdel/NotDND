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

---

## 4. 世界生成流水线（`GDD.md` §3.5 的可执行化）

### 4.1 七段管线（描述 → 战役）

`GDD.md` §3.5 的七步逐段落点如下；**每段都有纯函数形态**，模型只出现在 P1 与 P3 的文本字段：

| 段 | 名称 | 形态 | 落点 |
|---|---|---|---|
| P1 | **需求结构化** | 编排 + 模型端口 | `worldgen.extract_spec(text, *, port) -> spec`：LLM 把自由描述提炼成 `WorldSpec`（§4.2）；**离线路径**：`spec` 可由调用方直接给 JSON（§3.3「预制/混合」与测试都走这条），缺字段按 §4.2 的默认表补齐 |
| P2 | **模板 / 规则选择** | 纯函数 | `worldgen.choose_base(spec, catalog) -> {template_key, overrides}`：只认 `world.world_type` 显式枚举（§2.3 口径 1）；预制开局 = spec 直接指向既有模板；描述开局 = 按 `world_type + engine + scale` 从目录挑最近的模板作底 |
| P3 | **合成** | 纯函数 + 模型端口（可选） | `worldgen.synthesize(spec, base, *, seed, port) -> world.module`：结构字段（regions / key_places / laws / economy 开档）由纯函数从 spec + 种子确定性生成；文本字段（名称 / summary / atmosphere / glossary / history）优先取 `port`，不可用则取模板同字段或内置兜底短语 |
| P4 | **地图与实体生成** | 复用既有 | `atlas_compile.compile_world`（`atlas_compile.py:421`）+ `atlas_gen.generate_site` / `generate_tactical`；NPC 名册由 `worldgen.roster(spec, seed)` 按 `scenario.schema.json` `$defs.npc` 产出（§8.3） |
| P5 | **一致性检查** | 纯函数 | `worldgen.validate_world(world) -> [issue]`（world.module 形状）+ `worldgen.validate_atlas(atlas) -> [issue]`（§5.1）；**有 error 不得进入 P6** |
| P6 | **预览 / 修订** | 编排 | 摘要卡（区域数 / 关键地点 / 势力 / 法律条数 / NPC 预算 / 经济开档）+ overrides diff（§4.4）；修订 = 改 spec 重跑 P2–P5（幂等：同 spec 同种子同结果） |
| P7 | **创建战役** | 编排 | 会话初始化：`_genesis_blocks` 语义不变，编排层在同一批初始提交里写入 `atlas` 块（`atlas.py:445` `export_state`）、`world` 块（§6.1）、`neura` 块（§8.5 种群）；世界模组本体仍以**文件**入库（`data/worlds/`），战役只存 `world_key + seed + overrides` |

> **生成失败语义**（`GDD.md` §3.5 末句）：P5 的每个 `issue` 带 `code / place_id? / hint`，
> 编排层据此显示「校验问题 + 重试（重掷 seed）/ 降级（回退模板）」两条路；**不存在**
> 「带 error 的生成物静默进战役」的路径。

### 4.2 `WorldSpec`（需求的结构化形状）

`WorldSpec` 是流水线唯一的中介语言——P1 的输出、P2–P3 的输入。字段级定义：

```
WorldSpec: {
  world_type,          # "fantasy" | "modern" | "future"；P1 提不出来 → "modern"（§2.3 口径 1）
  engine,              # "tactics" | "build"；缺省 "tactics"
  scale,               # "sketch" | "region" | "domain"（§4.5）；缺省 "region"
  tone_terms,          # [str]：描述里的空间词（「港口」「地下」「群岛」…）；合成时必须映射成
                       #   atlas.lexicon 覆盖项（§4.3），词表没覆盖的词进 issues（warn，不 error）
  regions_req,         # {count: int, key_places_per_region: int}；缺省按 scale 默认表
  factions_req,        # {count: int}；缺省 2（scale=sketch）/ 3–4（其余）
  laws_req,            # [{name, forbid_tags[], scope?: "all"|"regions", threshold?}]；可为空（= 无法律，P8 同款「不设限」）
  economy_on,          # bool；缺省 scale != "sketch"
  npcs_req,            # {named: int}；缺省按 scale 默认表；上限见 §4.5
  secrets_required,    # bool；缺省 true（至少 1 个势力级秘密，供 GDD §12.2 秘密对象）
  seed_hint            # str | null；预览「重试」时用确定性派生，不让玩家手填数字
}
```

- **默认表与上限都是常量**（`worldgen.py` 模块级，一处可调）；越界值被 `normalize_spec` 钳制并记 `spec_notes`（进预览卡，可解释）。
- `WorldSpec` **可 JSON 序列化、进存档**（随战役存 `blocks.world.spec`，修订 / 扩建时复用——`GDD.md` §3.3「版本记录与差异预览」的载体）。

### 4.3 「描述 → 结构」的零依赖可测路径（铁律 2 的落法）

- **结构层无模型依赖**：`normalize_spec` / `choose_base` / `synthesize` / `roster` / `validate_*`
  全部是纯函数，测试只喂 JSON 断言输出——同 spec 同 seed 连跑 100 次**逐字节相等**（§10.1 断言 1.4）。
- **模型端口的形状**：`port = {suggest(spec, field) -> str | None}`，与 NEURA 的 `port.generate_candidates`
  同款鸭子类型（PR #364 裁定 10）；`port is None` 或方法缺失 = 离线正常路径（**不抛错**，
  `GDD.md` §20.4）。每个文本字段都有三级来源：`port` → 模板同字段 → 内置兜底短语表；
  来源记入 `worldgen_provenance`（预览卡可见，`GDD.md` §16.5 可解释性）。
- **模型调用预算**（V14）：会话级计数 `{calls, est_tokens}`，上限常量 `WORLDGEN_CALL_BUDGET`
  （建议默认 24 次调用 / 一次生成会话；拍板 2026-10-18，来源：人，§12.1 P4——原建议 12，
  以后视情况调整）；超限后 `port` 视为不可用（单调降级，同
  `NEURA-DESIGN.md` §8.3 的「不回回升档」）。**预算计数器不落盘**（生成发生在 genesis 之前，
  V14）。
- **词表闭环**：合成的 region 短文必须命中 `atlas.lexicon`（`compile_world` 的前提）；做法是
  `synthesize` 先为 `tone_terms` 生成 `atlas.lexicon` **覆盖项**（追加在战役级 lexicon 上），
  词表没有的比喻 → `issues`（warn）+ 该词落兜底 trait。**加词不加地图**（`ATLAS-DESIGN.md` §4.1 原话）。
- **内容边界**：模型产出的文本字段一律过既有内容防火墙（`tests/content_firewall.sh` 对新
  数据一视同仁）；`worldgen` 不做、也不该做内容审查——那是防火墙与 `GDD.md` §21.3 的职责。

### 4.4 模板与实例分离、三种开局（`GDD.md` §3.2 / §3.3）

- **模板** = `data/worlds/<key>.json`（`world.module`，含 `laws` / 经济开档与词表覆盖项）。
  「加文件即加世界」（`ATLAS-DESIGN.md` §6.4 已验证的路径）。
- **实例** = `(template_key, seed, overrides, blocks)`。同一模板多个实例互不影响（§3.2）；
  编译结果只取决于 `(world.module, lexicon, seed)`（`atlas_compile.py:421` 的既有保证）。
- **三种开局共享同一条管线**：
  | 开局 | P1 | P2 | P3 |
  |---|---|---|---|
  | 预制 | spec 由模板元数据直填 | 直接命中模板 | 跳过（模板即成品），只跑 P4–P5 |
  | 描述生成 | LLM / 手填 JSON | 按 `world_type` 挑底 | 全跑 |
  | 混合 | spec 由「模板 + 用户修改」合成 | 模板为底 | 只合成 **overrides 覆盖的字段**，其余继承 |
- **overrides 补丁**（V13，单层起步）：

  ```
  overrides: {                     # 声明式、可 diff、带版本
    version: 1,
    base: <template_key>,
    region_upserts: [{id?, name, summary?, key_places?: [...], ...}],
    region_removals: [id],
    faction_upserts: [...], law_upserts: [...], law_removals: [id],
    note: str                      # 修订说明，进 diff 预览与存档
  }
  ```

  应用顺序固定：模板 → upserts → removals；`apply_overrides(base, overrides) -> world.module`
  是纯函数，diff 预览 = 应用前后两次 `validate_world` + 字段级 JSON diff。**改地区 / 人物 /
  基调 / 冲突**都表达在这四类 upsert / removal 里；表达不了的（如「让北方更冷」）属 P3 文本
  字段重生成，不是 overrides 的职责。
- **保存与分享**（拍板 2026-10-18，来源：人，§12.1 P6）：玩家可把**完整世界实例**保存进
  **玩家存档**（随战役持久化，`blocks.world` 即载体）；**不入系统库**——仓库
  `data/worlds/` 只放官方数据，玩家生成物不得写进仓库数据目录。分享 = **v1 文件级导出 /
  导入**（导出 JSON 文件、导入时过 `validate_world` + `validate_data` + 内容防火墙）；
  「保存为模组入库」仍推 M13 编辑器一并拍板。

### 4.5 规模档与预估（`GDD.md` §3.4）

| 档 | 区域数 | 关键地点/区 | 具名 NPC 预算 | 经济开档 | 预估展示 |
|---|---|---|---|---|---|
| `sketch` | 2–3 | 2–3 | 8–15 | 关（只有人物级粗账，NEURA `resources` 兜底） | 「一晚」 |
| `region` | 4–6 | 3–5 | 20–40 | 开（据点层，§6.6） | 「一周–一月（游戏时间）」 |
| `domain` | 6–10 | 3–6 | 40–80 | 开（含城市 / 势力层，§6.6） | 「数月–数年（游戏时间）」 |

- 预估由 `worldgen.estimate(spec) -> {regions, npcs, sim_load}` 计算，P6 预览卡展示；
  **只展示、不强制时长**（`GDD.md` §3.4 明文「提供预估而不是强制固定时长」）。
- `domain` 档的 NPC 上限同时是 `NEURA-DESIGN.md` §8.2 成本模型的输入——80 具名 NPC 全
  background 层的日成本在 §8.2 公式下仍为 0（无模型调用），上限不是为了省钱而是为了
  journal / 块体积可控（§12 P3 关联）。

---

## 5. ATLAS 生成校验扩展（`GDD.md` §13.3）

### 5.1 校验器清单（全部离线可跑、纯函数）

校验对象是 `compile_world` / `generate_site` 的产物（`ATLAS-DESIGN.md` §3.1 的 place / link 形状），
**只读** `atlas.py` 公开结构，不改签名（V8）。`GDD.md` §13.3 列举的检查项逐条对应：

| # | 校验 | `GDD.md` §13.3 对应词 | 判定 | 级别 |
|---|---|---|---|---|
| C1 | **位置唯一性** | 「位置唯一性」 | place id 无重复；同帧内 `(x,y,z)` 不被两个 place 占用（footprint 展开） | error |
| C2 | **道路连通性** | 「道路连通性」 | 每个 frame 的连接图强连通（无向视）；有向 `oneway` 边不破坏「从入口可达所有格」 | error |
| C3 | **出入口存在** | 「入口出口」 | 每个 site 帧恰有一个 `via="门"` 连到锚（`generate_site` 既有不变式，校验防外部破坏）；区域帧至少一条边 | error |
| C4 | **关键地点可达** | 「关键地点的可达性」 | 每个区域 `key_places` 的 place 从该区域锚点图距离可达；`discovered` 无关（校验的是结构不是探索态） | error |
| C5 | **建筑功能** | 「建筑功能」 | `kind ∈ {region,fill,site,room,zone,feature}` 合法；site 帧 room 的 venue 行（若带 feature）来自 `random_tables` 池 | warn |
| C6 | **资源合理性** | 「资源合理性」 | 经济开档时，region 短文声明的产出（`economy` 字段，§6.6）与 traits 不矛盾（「荒野」不产「铁锭」类白名单抽查） | warn |
| C7 | **移动成本一致** | 「移动成本」 | link 的 `band` 与两端点图距离/帧 `space` 相容：`metric` 帧上 `zone_steps`（`atlas_gen.py:447`）折算不与 band 矛盾；`short/medium/long/dangerous` 四档取值合法 | error |
| C8 | **世界级规格** | （§4.2 spec 兑现） | 区域数 / 关键地点数 / 势力数 / 法律条数与 `WorldSpec` 默认表一致（用户显式改过的字段以 spec 为准） | warn |

- 返回 `[issue]`，元素 `{code, level, place_id?, frame_id?, hint}`；**error 阻断、warn 放行**
  （warn 进预览卡）。三世界（`data/worlds/*.json`）+ `tests/fixtures/atlas_fiction_world.json`
  必须零 error 通过——这是校验器自身不误伤的回归锚（W2 验收）。
- `generate_site` / `generate_tactical` 之后跑一次 C1–C7；`compile_world` 之后跑 C1–C4 / C8。

### 5.2 局部修复（不是全图重掷）

`GDD.md` §13.3「允许局部修复」的落法：`worldgen.repair(atlas, issues, *, rng) -> {atlas, repaired}`——

- 只处理三类 **error**（其余提示人工改 spec）：
  - C2 断链：在最近两连通分量间补一条边（`via` 取两格相对方位，`band` 按距离取档）——
    复用 `atlas_gen.py:106` `_ensure_link` 同款手法（不 import 私有函数，重实现 3 行）；
  - C3 缺门：从锚到最近 room 补 `via="门"`；
  - C4 不可达关键地点：把它挂到同区域最近已连通格。
- 修复**只新增边 / 不删除、不移动**已有结构；修复后**重跑全量校验**，仍有 error → 该帧
  整体重掷（换子种子，`_derive_seed` 同款派生）；重掷 2 次仍失败 → 生成失败（§4.1 P5 语义）。
- 修复记录进 `worldgen_provenance`（修了什么、几次）——可解释性要求，`GDD.md` §16.5。

### 5.3 与既有三文件的关系（零改动，V8）

- `atlas.py`（649 行）/ `atlas_compile.py`（813 行）/ `atlas_gen.py`（458 行）**零改动**：
  校验器消费的是它们的**输出形状**（`ATLAS-DESIGN.md` §3.1 的 place dict），不是内部实现；
  唯一被「扩展」的是**生成流程的调用次序**（生成 → 校验 → 修复 → 再校验），次序编排住在
  `worldgen.py`。
- `tests/fixtures/atlas_fiction_world.json` 零改动，作为 W2 的回归输入（只读）。

---

## 6. 经济与据点运行时（`GDD.md` §16.3 / §16.4 的落点）

### 6.1 `blocks.world` 块形状（字段级）

照 `chron` 块范本（`chron.py:2283` `new_block` / `:2323` `migrate_block`；`CHRON-DESIGN.md` §8.1）：

```
blocks.world = {
  version: 1,                      # WORLD_VERSION；migrate_block 惰性迁移（§10.1 老档升级）
  spec: WorldSpec,                 # §4.2；扩建 / 修订的输入
  provenance: {…},                 # 生成来源记录（§4.3），创建时一次性写入
  economy: {
    day: int,                      # 当前已结算到的游戏日（world_minute // 1440，口径读 CHRON §2.3）
    goods: {good_id: {name, tier, base_price, stock, produced_by: [place_id]}},   # 局部供应链（§6.6）
    prices: {good_id: {price, updated_day}},      # 派生（供需弹性，§6.3）；落盘只为审计
    ledger: [Entry],               # 滚动明细，硬上限 LEDGER_MAX（§12 P3），超限裁最旧
    balances: {account_id: int},   # 派生快照（§6.2）
  },
  projects: {project_id: Project}, # §7.1
  settlements: {settlement_id: {…}},   # 据点层（§6.6）
  population: {                    # §8.3：unexplored 层的人口摘要信号在这里落地
    summaries: [{summary_id, place_id, minute, headcount_band, note_ref}]
  },
  facts: [{fact_id, minute, kind, subject_ref, text}],   # §8.2 world_facts 投影的来源
  rejudge: {day: int, counts: {npc_id: int}},            # §8.4 同日重判预算
}
```

- **不透明块纪律**：`state.py` 不解析它（`state.py:226` 按块名机械写入）；`world.py` 是唯一
  读写者；编排层只提交与展示。
- **goods 清单口径**（拍板 2026-10-18，来源：人，§12.1 P2）：goods **尽可能多、重要常用类目
  优先**，且**必须按世界特性生成**——清单由 `world.type` / 剧本 / 时代 tag 驱动合成
  （`worldgen.synthesize` 在开档时产出，替代固定常量表）；**衣食住行军等常用类目保底覆盖**
  （任何世界类型都必须产出这几类，合成器保底补齐）；`validate_world` 增加**时代违和校验**——
  goods 的时代档与 `world.type` 联动，越档 goods（如奇幻世界卖激光枪）→ **error**（进 §5.1
  同一条「有 error 不得进入 P6」的阻断路径）。
- **块体积**：`LEDGER_MAX`（建议 512 条）/ `facts`（建议 256 条）/ `population.summaries`
  （建议 128 条）硬上限 + 确定性裁剪（先最旧，同分钟按 id 字典序）——照 `NEURA-DESIGN.md`
  §4.2 journal 裁剪模式；§12 P3 留了体量复核项。

### 6.2 账本与「有账可查」（`GDD.md` §16.5）

**事件溯源是硬要求**（V4）：

- **每一笔**经济 / 建造变更 = 一条事件（A0：一次提交一条；`STATE-DESIGN.md` §5.0）。
  事件 `changes` 带 `{"block":"world","op":"replace_block"}`（块级）时，**必须**同时在
  事件 `refs` 里带 `ledger_entry_ids`——块是滚动的、会被裁剪，事件日志才是权威账本。
- `Entry` 形状（`blocks.world.economy.ledger` 元素）：

  ```
  { entry_id, at_minute, kind,            # kind ∈ {produce, consume, trade, wage, tax, debt,
                                          #          build_material, build_pay, maintenance, grant}
    account, delta,                       # account ∈ {coin, <good_id>, labor, ...}；delta 带符号
    reason,                               # 机器可读：{verb, actor, target_ref}
    refs: {event_seq, project_id?, settlement_id?} }
  ```

- **可查 = 三个等式**（§10.2 断言化）：
  1. `replay(genesis, events) ` 重放出的每账户余额 == `balances` 快照（差异即 bug，以事件为准）；
  2. 任何 `balances[a]` 的变化都能追到**恰好一条** `Entry`（不重不漏）；
  3. `Entry` 不可凭空出现：每条的 `refs.event_seq` 指向真实事件（`GDD.md` §19.2「涉及游戏
     事实的操作必须有足够的审计记录」）。
- **粒度克制**（§1.2）：只有「玩家行动直接影响的局部供应链」记账；背景 NPC 的吃穿不逐笔
  记（人口层聚合为日结算的净差额，§6.3）——否则 200 NPC × 一天能把 ledger 冲爆。
- **玩家可见账目**（钱袋 / 仓库）继续由 PRISM 规则包权威（`system.economy` 的持有资金 5 级）；
  `blocks.world` 记的是**宏观账**（据点库存 / 工程 / 税收 / 工资），两本账的关系见 §6.6，
  **不互相改写**——规则包库存零改动，这是 M8 结算的既有事实。

### 6.3 日切结算与 CHRON 挂接（不新建时钟）

- **驱动**：`_chron_advance`（`notdnd_web.py:3872`）锁内、`_neura_tick` 之后插 `_world_tick(session, *, stopped_at)`——
  与 NEURA 完全同款（`NEURA-DESIGN.md` §7.3 / §9.4）；**失败隔离**：`_world_tick` 抛错只记
  `world_skipped` 日志，不阻塞时间推进（照 `neura_skipped` 先例）。
- **日切结算内容**（幂等键 `wtick_<day>`，一天至多一次）：
  1. **生产 / 消费**：按 `goods[].produced_by` 与据点人口聚合出日净差额，逐 good 记 `Entry`；
  2. **工资 / 税 / 维护**：`projects` / `settlements` 到期义务结算（§7.2）；
  3. **价格更新**：`prices` 按近窗供需比移动一步（**确定性公式**，无随机——同状态同价格，
     §10.2 断言 2.5）；涨幅钳制常量一处可调；
  4. **工程里程碑推进**（§7.3 背景通道）。
- **屏障口径**：`stopped_at != None` 时不跑日切（照 NEURA §10.1 断言 2.4）；跨屏障漏掉的
  日切在下一个非屏障停点**补结算**——补结算是「把欠的 `wtick_<day>` 逐日补齐」（每天一条
  独立幂等键），不是把多天合成一笔（保证逐日可查）。
- **补算（离线推进数月）**：`world.catch_up(block, view, now_minute)` 与 `neura.catch_up`
  同款思路——只做「逐日结算 + 推进 `economy.day`」，**不做逐点决策**；价格按日步进公式
  收敛，超过 `MAX_CATCHUP_DAYS`（建议 90）的远期直接跳到末日再结算一次终值（账面守恒，
  中间日不逐笔留痕——`ledger` 只留末日 `Entry`，`kind="catchup_summary"`）。

### 6.4 玩家经济动作与 M8 管线

- 玩家的买入 / 卖出 / 雇工 / 缴税 / 修墙 = **普通提案**走 M8 管线（`notdnd_web.py:2736`
  `_action_settle_commit` 内部分支），提案契约**不新增字段**（V6；`ACTION-DESIGN.md` §4.2）；
  结算结果里经济部分由 `world.py` 的结算函数给出 `effects`（结果契约 `ACTION-DESIGN.md` §5.1
  的既有字段装得下），账目变更随**同一次提交**落盘（A0）。
- **新事件类型**（§9.2 登记，`STATE-DESIGN.md` §5.1 枚举另开文档 PR 同步——P9 同款纪律）：
  `econ.day`（日切块级）、`econ.trade` / `econ.build`（玩家动作伴随）、`world.fact`（投影写入）。
- **幂等**：玩家动作用 M8 既有幂等键（`ACTION-DESIGN.md` §9.4）；日切用 `wtick_<day>`；
  重判预算变更随触发它的动作事件落盘（不单独开事件）。

### 6.5 委托式管理（`GDD.md` §16.4 / §16.5）

- **指令对象**（`blocks.world.settlements[].directives[]`）：

  ```
  { directive_id, text,                  # 「每周确保粮仓有三个月储备」
    goal: {metric, op, target},          # {metric: "grain_days", op: ">=", target: 90}
    budget: {coin_per_day?, labor_per_day?, cap_total?},
    priority: int, status: {draft|active|paused|revoked},
    authorized_scope: [verb],            # 授权边界：允许 world.tick 自主执行的动作动词表
    created_minute, created_by }
  ```

- **转化**：指令由玩家经 M8 提案创建（需要一次性澄清的照 `ACTION-DESIGN.md` §6.3）；
  `world.tick` 日切时按 `goal` 检查缺口 → 在 `budget` 与 `authorized_scope` 内自动发起
  规则提案（`source="npc"` 同款窄口）。
- **关键取舍 / 不可逆决策必须回问玩家**（`GDD.md` §16.4 明文）：定义**硬回问清单**——
  单笔超 `cap_total` 50%、卖掉 `tier <= 2` 的储备、拆除工程、解雇最后一个劳动力——命中即
  `directive.status="blocked"` + 事件留痕，**绝不先斩后奏**。
- **可解释性**（`GDD.md` §16.5 逐项）：每个 active 指令可读出 `目标 / 预算 / 正在执行的
  工程 / 阻塞原因（blocked_reason）/ 最近决策（ledger 近窗 3 条）/ 预期结果`；支持暂停
  （`paused`，日切跳过）、撤销（`revoked`，已提交的**不可撤**，只停后续——「撤销未提交
  任务」的落点）、调优先级。
- **不能让 AI 在玩家不知情的情况下无限花费**：`world.tick` 全程**无模型调用**（规则提案
  只有确定性公式），花费上限由 `budget.cap_total` 在提案生成时钳制——这比「事后审计」
  更硬（`GDD.md` §16.5 的禁令用结构满足，不靠提示词）。

### 6.6 规模阶梯与首版范围（`GDD.md` §16.1 / §16.3）

| 层 | 首版（M12）运行时 | 说明 |
|---|---|---|
| 个人基地 | ✅ 库存 + 维护扣账 | 复用 PRISM 据点规则的**数值口径**（`system.stronghold` 设施 8 种 / 维护 3 级），运行时账落 `blocks.world` |
| 聚落 / 组织据点 | ✅ 生产 / 消费 / 工资 / 指令 | §6.3 / §6.5 主战场 |
| 城市 | ◐ 只读聚合（人口 / 粮价展示） | `domain` 档才有；无自主城市 AI |
| 国家 / 星际 | ❌ 不做 | M13+ |

- **两本账的分工**（§6.2 遗留）：角色钱袋 / 随身库存 = PRISM 规则包权威（M8 结算改写）；
  据点 / 势力 / 工程的宏观账 = `blocks.world`。转移只发生在 M8 结算过的动作边界
  （例：「从仓库取 10 单位铁」= 玩家提案 → 规则包结算 + `world` 块 `Entry(kind="consume")`
  同提交双记）。

---

## 7. 建造（`GDD.md` §16.1 / §16.2）

### 7.1 工程对象与里程碑

```
Project: {
  project_id, name,
  place_id,                     # 建在哪（须过 atlas.find_place，atlas.py:179）
  blueprint: {kind, levels?},   # kind ∈ 设施 8 种（system.stronghold 口径）；扩 level 表达扩建
  requirements: {goods: {good_id: qty}, coin: int, labor_days: int},
  milestones: [{seq, at_minute?, label, done}],   # at_minute 到期才结算（§7.3）
  progress: {materials_in: {good_id: qty}, labor_done_days, spend_coin},
  status: {draft|queued|running|blocked|paused|completed|failed},
  blocked_reason?, source: {player|directive|npc}
}
```

- **开工条件**（`GDD.md` §16.2 逐项）：材料足、资金足、劳动力足、地点合法（place 存在且
  `kind != feature`）、**校验过的地图**（该 place 在 §5.1 零 error 的 atlas 里）——五者齐才
  能从 `queued` → `running`。
- **里程碑**：工期按 `labor_days / (劳动效率)` 折算分钟（口径读 `CHRON-DESIGN.md` §2.3 与
  行程档常数，**不定义新时间常数**）；里程碑只做「到期结算」不做「逐日施工动画」。

### 7.2 资源、劳动力与维护

- **材料 / 资金**：从 `blocks.world` 据点库存扣（`Entry(kind="build_material"/"build_pay")`）；
  个人钱袋部分走 M8 规则包结算（§6.6 两本账）。
- **劳动力**：`labor` 账户按人日计；来源三个——玩家队伍（占行动，走 M8）、雇佣（日切
  扣工资 `Entry(kind="wage")`）、指令授权的劳力池（§6.5 `budget.labor_per_day`）。
- **扰动**（`GDD.md` §16.2 列举的天气 / 供应链 / 敌对 / 管理能力）：首版只落**三类**可
  结构化判定的：供应链断（所需 good 库存 0 → `blocked`）、敌对占领（place 被 `world.facts`
  标记失守 → `paused`）、资金断（日切工资发不出 → `blocked`）。天气 / 劳资关系属事件钩子，
  留 §12 P7——**拍板 2026-10-18，来源：人（§12.1 P7）：v1 接入建造扰动**，设计见下。

- **扰动规则表**（§12.1 P7 拍板后的实现级设计，W6 可直接实现；随机性与合理性并重，
  **不能乱安排**）——扰动表五要素：

  ```
  DisruptionRule: {
    rule_id,                        # 如 "weather_storm"、 "labor_dispute"
    kind,                           # weather | supply | hostility | labor | accident（保留枚举，可扩展）
    trigger: {                      # ① 前置事实条件（全部满足才成为触发候选）
      fact_kind,                    # world.facts / blocks.world.facts 的 kind（§6.1）
      op,                           # exists | absent | eq | gte | lte（对事实投影值比较）
      ref?,                         # 作用域：place_id / project_id / good_id（缺省 = 全部工程）
      window_days?                  # 事实新鲜度：超过窗的旧事实不触发（防陈年事实乱触发）
    },
    probability: {                  # ② 概率参数（二选一；随机性只在这一层，不开自由骰）
      mode: "prism" | "seed",
      prism?: {stat, dc, actor_ref?},   # PRISM 检定：走既有检定入口，不为工程开新骰点通道（§7.3）
      seed?:  {p, salt}                 # 种子确定性公式：hash(world_seed, rule_id, day, salt) mod 1000 < p*1000
    },
    effect: {                       # ③ 效果公式（确定性应用，结算函数唯一入口）
      kind,                         # block_project | pause_project | cost_mult | progress_loss | good_price_spike
      params                        # 按 kind 定参：如 cost_mult {good_id, factor, days}、progress_loss {ratio}
    },
    cooldown_days,                  # ④ 冷却：同 project 同 rule 触发后 N 日内不重掷
    severity                        # ⑤ 严重度：light | heavy（journal 文案分级；heavy 进预览卡提醒）
  }
  ```

  - **事实驱动原则**：触发前提是 `blocks.world.facts`（§6.1）/ `world.facts` 投影（§8.2）里
    存在满足 `trigger` 的事实——**有因果依据才触发**；无事实支撑的扰动一律不发生。
    v1 内置规则表 `DISRUPTION_RULES` 为 `world.py` 模块级常量（一处可调；schema 预留
    世界包 / spec 覆盖项，将来按世界特性扩展）。
  - **随机性来源**：`mode="prism"` 走既有 PRISM 检定入口（`actor_ref` 指工程维护方，检定
    结果进事件日志）；`mode="seed"` 用 `hash(world_seed, rule_id, day, salt)` 的确定性公式——
    **不开自由骰**（无独立 RNG、无时钟依赖），同状态同日重放结果逐字节相等（§10.2 断言
    2.5 / 2.8 的同款确定性纪律）。
  - **可解释留痕**：每次触发写一条 journal 级 `Entry`（`kind="disruption"`，§6.2 形状内）：
    `reason` 记 `{verb:"disrupt", actor:"world", target_ref: project_id}`，`refs` 带
    `{fact_ids, rule_id}`——**什么事实、依据哪条规则、结果如何**三问在账本上可查；
    `severity="heavy"` 的结果同时进该工程 `blocked_reason`。
  - **触发时机**：挂在日切结算（§6.3 日切内容追加第 5 步「扰动判定」，逐 rule 评估
    trigger → 候选 → 概率 → 效果，与工资 / 维护结算同事务落账）；既有**三类结构化扰动**
    （供应链断 / 敌对占领 / 资金断）保持确定性判定不变，不入概率表。
- **维护**：完成后进入 `settlements[].facilities[]`，日切扣维护费（`system.stronghold` 3 级
  恶化口径）；维护欠费 → 设施降级一格（确定性），不再降到底即停——账目仍逐笔记。

### 7.3 与 CHRON / PRISM / 状态层的分工（`GDD.md` §16.2 末句的对齐）

`GDD.md` §16.2 明文「CHRON 负责建设项目的时序与里程碑，PRISM 负责相关检定或风险结算，
状态层负责库存和工程状态」——落法：

- **玩家发起的工程 → `chron.actions`**（V7）：玩家「修城墙（约 3 天）」是玩家语义的长行动，
  走 M8 排程钩子两层（`ACTION-DESIGN.md` §8.2 / §8.3）进队列，享受八状态语义（可取消 /
  可中断 / 到期稳定排序 `CHRON-DESIGN.md` §5.2）。**里程碑到期 = CHRON 卡到期**，结算由
  `_action_settle_commit` 内部分支完成。
- **背景工程（指令 / NPC 势力自建）→ 不进队列**（V7）：与 `NEURA-DESIGN.md` §12.1 P7
  同理由——CHRON 八状态带玩家语义，把几十个背景工程塞进同一队列会污染玩家的到期视图、
  拖累屏障判定。背景工程用 `blocks.world.projects[].milestones` + 日切推进（§6.3），
  中断语义用 `status: paused/blocked` 自表达。
- **两条通道的汇合点**：都只通过 `world.py` 的**同一个结算函数**落账（材料扣减 / 进度推进 /
  状态迁移），保证「一项工程一套账」，不因通道不同而口径分叉。
- **PRISM 检定**：工程是否需要掷骰（质量检定 / 事故风险）——首版**不掷**（确定性推进），
  事故类风险留给事件钩子（§12 P7；拍板后 v1 经 §7.2 扰动表接入，`mode="prism"` 走既有
  检定入口）；需要掷时走既有 PRISM 入口，不为工程开新骰点通道
  （§1.2 与 `NEURA-DESIGN.md` §12.1 P2 同款克制）。

---

## 8. 与 NEURA · CHRON · STATE 的挂接（M11 移交项逐项接住）

> 本章是 Issue #376「M11 移交项 4 类逐项有落点」的验收章；另收两项随迁（PR #371
> 「没做什么」里的种群初始化与 `travel_minutes`——同为「M12 世界事实投影」承诺的一部分）。

### 8.1 `laws` schema（移交 ①，`NEURA-DESIGN.md` §12.1 P8）

- **数据落点**：`world.module` 顶层新增 `laws` 数组（V9）。条目形状**逐字段对齐
  `neura._laws` 的消费契约**（`neura.py:111` 的两个容忍值 + `_law_hit_tags` / `_law_covers`
  / `_law_tolerated` 读取的字段）：

  ```
  laws: [{
    id,                      # entity_id（common.schema.json $defs）
    name,                    # 展示名
    forbid_tags: [str],      # 命中标签（neura._law_hit_tags）
    scope_place_ids: [str],  # 空/缺 = 全域（neura._law_covers 的口径，不得改语义）
    threshold,               # "forbid"（硬禁，默认）| "tolerate" | "allow"
                             #   —— neura.py:111 只认 allow/tolerate 两个「容忍」值，其余一律硬禁
    penalty,                 # 展示/叙事用字符串；neura 不消费
    source                   # docs 引用（validate_data 既有纪律）
  }]
  ```

- **`law_source` 的取值链**：世界模组带 `laws` → `view.laws = [...], law_source = "world"`；
  规则包将来自带法律 → `"rules"`（`LAW_DECLARED_SOURCES` 已预留，本期不实现）；
  都没有 → `[]` + `"none"`（P8 的「空表不拒」不变）。**neura.py 对 laws 的判定逻辑零改动**
  ——schema 定义在数据侧，消费侧 M11 已定型。
- **规则包联动**：`system.economy` / `system.stronghold` 是 PRISM 数值口径，不含法律；
  法律只属世界（世界包作者的建模责任）。生成世界的 `laws_req`（§4.2）由 `synthesize`
  确定性展开成上述条目。
- **schema 落点**：`data/schema/world.schema.json` 加 `laws` 属性 + `$defs.law`；
  现有三世界 JSON **不加 laws 字段**（可选属性，缺失 = 无法律 = 现行为）——W8 数据片。

### 8.2 view 注入键数据源（移交 ②，PR #364 裁定 4/5）

`notdnd_web.py:2997` `_neura_view` 里写死的五个空键，由**世界事实投影**（V10）供数。
投影是纯函数：`world.projection(readonly_session_state) -> dict`，输入只有只读状态
（规则块 / 剧本数据 / atlas / neura 块 / `blocks.world`），**不 import 业务模块、不触发
懒编译**（`_neura_view` 既有纪律）：

| view 键 | 数据源 | 投影规则 |
|---|---|---|
| `known_refs` | 线索 / 知识事件（state 事件日志的 `clue.*` / 剧本 `clues[].id`） | 玩家已获知的线索 id 集 = 事件日志中 `visibility` 对玩家开放且 `kind="clue"` 的目标集；缺事件日志（过渡档）→ 剧本 `clues` 全集降级 |
| `main_thread_refs` | 剧本 `threads`（`scenario.schema.json`） | 主线 thread 涉及的 npc_id 集（thread 的参与人字段展开）；无 threads → 空 |
| `faction_refs` | `world.module.factions` + NPC 静态档案 `faction` 字段 | 有 faction 归属的 npc_id 集（P10 公式第三项的输入） |
| `importance_overrides` | `blocks.world.facts` 里 `kind="importance_override"` 的条目（世界作者 / 生成器显式给 0 或非默认值——PR #364 裁定 5 的「显式 0 须由覆盖表表达」） | `{npc_id: float}`，生成世界由 `roster` 写入，预制世界由手写 facts 表给 |
| `travel_minutes` | `atlas.range_band` × 行程档 | band → 分钟映射**复用 `notdnd_web.py` 既有 `BAND_HOURS` 口径换算**（不定义新时间常数，`CHRON-DESIGN.md` §2.3）；只对「块内地点 × 队伍地点」求值（同 `_neura_view` 现有 bands 的范围纪律）；`dangerous` 档给均值并标注风险由 PRISM 结算 |
| `world_facts` | `blocks.world.facts`（§6.1） | 投影成 `{subject_ref: [fact]}`；**法律事实**也走这里（laws 的运行时可见面） |

- **注入方式**：`_neura_view` 在构造 view 后调用投影并**覆盖**那五个缺省空键；投影抛错 /
  `blocks.world` 缺失 → 捕获、保持空键（M11 行为逐字节不变）——与 `neura_skipped` 同款
  失败隔离。
- **`world.facts` 的写入**：只有三条路——生成期（roster 的 importance_overrides）、
  M8 结算过的世界事件（如「粮仓失火」）、玩家可见的重大转折（经提案）；**AI 文本不得
  直接进 facts**（`GDD.md` §5.2「AI 文本 ≠ 事实」）。

### 8.3 unexplored 个体生成与人口摘要（移交 ③，`neura.py:2310` / PR #371 裁定 5）

- **触发**：编排层在 `ensure_atlas` 首次进入某 site 帧时（既有懒编译点，`notdnd_web.py:1805`），
  对该地点调用 `worldgen.individuals(place_id, *, seed, count_hint) -> [npc]`——**按需、
  每地点一次**（生成结果记入 `blocks.world.population`，重复进入不重生成）。
- **形状**：产物必须过 `scenario.schema.json` `$defs.npc`（不发明字段）；结构字段
  （role / faction / schedule 骨架 / needs 初值）由种子确定性合成；**模型只润色 name /
  drive 文本**（可选端口，同 §4.3 三级来源）；生成后走 §8.5 的导入路径进 `blocks.neura.npcs`，
  层判定（`neura.py:2310`）自然把它们从 `unexplored` 升入 background/near。
- **人口摘要信号**（接收方从此存在）：日切（§6.3）对「玩家当前地点 + 相邻」的未具名
  人群聚合出 `population.summaries` 条目（`{place_id, headcount_band, minute, note_ref}`，
  `headcount_band` 是档位不是精确数——减少假精度）；**个体生成与摘要互不触发**（摘要
  是「有多少人」，个体是「这个人是谁」）。
- **预算**：单地点个体数 ≤ `count_hint`（由 scale 默认表给，`sketch` 2–3 人 / `region` 3–6
  / `domain` 4–8）；累计具名 NPC 不得突破 §4.5 的规模上限——超限只出摘要、不再生成个体
  （可解释：预览卡与摘要里都写明「该地点人群未具名化」）。

### 8.4 事件触发的远场重判（移交 ④，PR #371 裁定 4）

- **匹配语义**（该裁定点名的缺口）：投影函数 `world.match_event(event, readonly) -> [npc_id]`——
  事件命中某 NPC 当且仅当满足其一：
  1. 事件的 `place_id` == 该 NPC `location.place_id` 或其 schedule 的 slot 地点；
  2. 事件 `target/subject` 引用该 NPC、其所属 faction、或其 active goal 的 `target_ref`；
  3. 事件被 `world.facts` 标记为该 NPC 所在势力级事件。
  只匹配 `important_far` 层候选（near 层本来每 tick 判；background 不值得为它付重判）。
- **预算载体**：`blocks.world.rejudge = {day, counts: {npc_id: n}}`（§6.1）；同日同一 NPC
  计数 ≥ 4（`REJUDGE_DAILY_CAP` 常量）即不再注入——**「同日 ≤ 4 次」由结构保证**，
  计数跨日随 `day` 归零。预算变更随触发它的动作事件落盘（不开独立事件，A0）。
- **注入通道**：编排层在相关动作事件提交后、下一个 `_chron_advance` 停点上，把
  `match_event` 的结果经 `view.force_due_ids` 传给 `neura.tick`；**neura.py 侧的改动 =
  读取一个缺省为空的 view 键**（`force_due_ids` 中的 id 无视层频率直接到期）——M11 行为
  在缺键时逐字节不变（V12）。这是本里程碑对 `neura.py` 的**唯一**改动（W7 内）。
- **与补算的关系**：重判**不与** `neura.catch_up` 合并——补算是「离线赶进度」（把
  `last_tick_minute` 收敛），重判是「在线提优先级」（把本日已判过的远场提前再判一次）；
  两者可在同一停点先后发生，但语义、幂等键、预算互不相干。

### 8.5 随迁：NPC 种群初始化与 `travel_minutes`（PR #371「没做什么」两条）

- **种群初始化**：`world.populate(neura_block_absent, roster_or_scenario, *, seed) -> neura_block`
  纯函数——把静态档案（预制世界：`data/scenarios` 的 `npcs[]`；生成世界：§8.3 的 roster）
  翻译成 `NEURA-DESIGN.md` §4.2 字段表要求的运行时块：`needs`（role 模板 + 确定性抖动）、
  `need_weights`、`goals`、`schedule`（tod 口径 0..1439）、`importance`（P10：显式覆盖
  优先，否则公式）、`location` 初值。**不变式照抄 §4.5**（键集 ⊆ 档案 id 集、schedule
  slot 有序、不发明 NPC）。编排层在战役 genesis 批里写入（§4.1 P7），此后 neura 自治。
- **`travel_minutes`**：§8.2 表内一行已给口径（`BAND_HOURS` 换算）；此处登记**边界**——
  `travel_minutes` 只喂 NEURA 的日程移动判定（消除 PR #371 的 `E_DISTANCE` 假拦截），
  **不改** `docs/` 行程四档的任何口径（`ATLAS-DESIGN.md` §1「不改 docs/ 规则」）。

---

## 9. 接口面与落点

### 9.1 模块与依赖方向

```
notdnd_web.py（编排：生成会话 / _world_tick / 投影注入 / 生成预览路由）
      │ 只调用
      ▼
worldgen.py ──调用──► atlas*.py（只读公开函数，零改动）
    │  ▲
    │  └─ port（模型端口，编排层注入；缺省 = 离线）
world.py ──只读──► state.py（commit/view/replay 接口面，§12.1）
    │
    └─ 不 import：prism_core / chron / neura / atlas（一切业务输入经 view 注入）
```

- `worldgen.py` 与 `world.py` **互不 import**（生成期与运行期是两个生命周期；
  共享的 `WorldSpec` / `Entry` 形状定义在 `worldgen.py`，`world.py` 只做 `isinstance`
  级校验——若 Master 更愿共享常量模块，见 §12 P1 的备选）。
- 新事件 `type`（在实现 PR 说明里登记，`STATE-DESIGN.md` §5.1 枚举另开文档 PR 同步）：
  `world.genesis`（创建战役的世界块初始化）、`econ.day`、`econ.trade`、`econ.build`、
  `world.fact`。
- `visibility` 口径（`STATE-DESIGN.md` §5.1 / `MULTIPLAYER-DESIGN.md` §6.4）：`econ.day`
  与 `world.genesis` = `{"scope":"gm"}`（账本是 GM/据点主人视角）；`econ.trade` /
  `econ.build` 随玩家动作事件的既有可见性；`population.summaries` 交付时过
  `_room_event_visible`（复用，不新建门）。
- 新增 HTTP 面（薄路由，风格同 `ACTION-DESIGN.md` §9.1）：`POST /api/world/generate`
  （描述 → spec + 预览卡）、`POST /api/world/commit`（确认创建战役）、
  `GET /api/world/ledger`（账目只读观察面，§10.3）。UI 不在本里程碑（§1.2），路由先服务
  测试与后续 UI 轨——**拍板 2026-10-18，来源：人（§12.1 P9）：UI 确定要做，M12 交付
  API 面 + 块内可解释字段不变，UI 派发单由 Master 在 W6 合并后开出**（依赖 UI-DESIGN
  轨排期权已获人授权）。

### 9.2 数据文件与 schema 落点

| 文件 | 动作 | 切片 |
|---|---|---|
| `data/schema/world.schema.json` | 扩展：顶层 `laws` 可选属性 + `$defs.law` + `regions[].economy`（产出声明，§5.1 C6 用） | W8 |
| `data/FORMAT.md` | laws 字段说明一小节（照 `atlas.lexicon` 先例） | W8 |
| `data/atlas/lexicon.json` | **零改动**（战役级覆盖项在运行时拼接，不改词表文件） | — |
| 生成世界入库 | `data/worlds/<key>.json`（玩家「保存我的世界」= 把合成结果写成文件，过 `validate_data`）；命名禁碰三个既有 key | W3 之后（可选项） |

- 生成世界**是否入库存文件**是产品决策（临时战役可以只存 `overrides + seed`），见 §12 P6
  ——**拍板 2026-10-18，来源：人（§12.1 P6）：不入系统库（`data/worlds/` 只放官方数据），
  玩家存档保存完整世界实例，v1 文件级导出 / 导入分享（§4.4「保存与分享」）**。

---

## 10. 可测口径（M12 出口条件 → 断言）

### 10.1 出口 1 · 生成物结构有效（`GDD-BASELINE.md` §5「M12」行第一条）

| # | 断言 | 测试思路 |
|---|---|---|
| 1.1 | `validate_atlas(compile_world(W, L, s))` 对三个既有世界 + `atlas_fiction_world.json` 零 error | W2 回归：遍历 `data/worlds/*.json` + fixture，断言 `error` 级 issue 为空（warn 允许） |
| 1.2 | 生成的 `world.module` 过 `validate_data` 既有校验（schema / kind / source 纪律） | 合成结果落临时文件 → 复用 `tests/validate_data.py` 的校验入口断言通过 |
| 1.3 | 流水线产物无死路：`generate_site` 后对每个 site 帧断言「从入口格出发 BFS 可达全部 room」（C2/C3/C4 的运行时化身） | 构造 5 个不同 seed 的生成世界，逐帧 BFS |
| 1.4 | 同一 `(spec, seed)` 连跑 100 次 `normalize_spec → choose_base → synthesize → compile_world`，产物序列化**逐字节相等**；换 seed 则区域布局不同 | 纯函数单测（无 IO、无时钟） |
| 1.5 | 修复收敛：人工构造含 C2 断链 / C3 缺门 / C4 不可达的残图 → `repair` 后全量校验零 error，且修复**只新增边**（边集单调增） | 残图夹具（从 fiction world 删边构造），断言重跑收敛 ≤ 2 轮 |
| 1.6 | 离线路径可用：`port=None` 时 `extract_spec` 走结构化 JSON 直填、`synthesize` 文本字段落兜底短语，整条流水线零模型调用完成且过 1.3/1.4 | 显式传 `port=None`（同 `NEURA-DESIGN.md` §10.1 断言 3.5 的手法） |
| 1.7 | error 阻断：带 error 的生成物无法进入 P6/P7（`commit` 路由对未过校验的预览返回错误码） | W3 编排测试：篡改预览结果强投 → 断言拒绝 |
| 1.8 | 生成世界入库（可选路径）后 `tests/validate_data.py` 全仓仍通过 | 落盘 → 跑既有校验脚本 |

### 10.2 出口 2 · 经济动作有账可查（`GDD-BASELINE.md` §5「M12」行第二条）

| # | 断言 | 测试思路 |
|---|---|---|
| 2.1 | **三个等式**（§6.2）：跑 30 个游戏日（含交易 / 工程 / 日切）后，`replay` 重放事件日志重算的余额 == `balances` 快照；每个余额变动追到恰一条 `Entry`；每条 `Entry.refs.event_seq` 指向真实事件 | 长跑后逐账户复算；`Entry` 反查事件存在性 |
| 2.2 | 库存不为负：任何 `Entry` 落地后 `goods[].stock >= 0`、`balances >= 0`（不足则动作被拒且**无** Entry） | 构造「买 10 只有 3 只」→ 断言拒单、无账、事件无 changes |
| 2.3 | 幂等：同 `idempotency_key` 重投玩家交易 → 事件数不变、余额不变；同日重跑日切 → `wtick_<day>` 命中、不重复结算 | 状态层幂等索引（`STATE-DESIGN.md` §7）+ 日切键单测 |
| 2.4 | 委托指令的账目可解释：active 指令在任意时刻能读出 §6.5 六项（目标 / 预算 / 在建 / 阻塞原因 / 近 3 笔 / 预期）；命中硬回问清单 → `blocked` + 留痕、**无自动花费** | 构造「粮仓 ≥ 90 天」指令 + 断供场景，断言 blocked 而非越权采买 |
| 2.5 | 价格确定性：同块同 view 同日两次日切 → `prices` 全等（无随机、无时钟依赖）；涨幅不越钳制常量 | 纯函数单测 |
| 2.6 | 工程账实相符：玩家工程（CHRON 通道）与背景工程（日切通道）完成时，`requirements` 与 `progress` 逐项对平；材料退回（fail 场景）有反向 `Entry` | 两条通道各造一例，含中断失败回滚 |
| 2.7 | 屏障不日切：`stopped_at != None` 时 `economy.day` 不变（照 NEURA §10.1 断言 2.4）；跨屏障后补结算逐日补齐（欠 3 天 = 3 条独立事件） | 造长行动屏障场景断言 |
| 2.8 | 重放相等：`replay(genesis, events[1..N])` 的 `blocks.world` 与 live 规范化 JSON 相等（含 ledger 裁剪确定性：裁两次结果相同） | 跑 60 日 + 手动裁剪复跑（同 NEURA §10.1 断言 2.1/2.5 手法） |
| 2.9 | 重判预算：同 NPC 同日第 5 次 `match_event` 命中不再注入 `force_due_ids`；跨日归零 | 构造 5 连事件断言计数 |

### 10.3 测试清单（对应 `GDD.md` §24.2）

| 类别 | 本里程碑覆盖 | 交给谁 |
|---|---|---|
| 规则单元测试 | ◐ 经济结算公式（价格步进 / 工期折算）为纯函数单测；不掷骰 | 本文 + `prism_core` |
| 时间调度测试 | ✅ 2.7（日切 × 屏障 × 补结算）；日切只读 CHRON 不改它 | 本文 + `CHRON-DESIGN.md` §9 |
| 多人并发测试 | ✅ 账目交付过滤（`econ.day` gm 视角）；双人同时买断最后库存（2.3 幂等 + 库存不为负） | 本文 + `MULTIPLAYER-DESIGN.md` §11 |
| 知识隔离测试 | ◐ `world_facts` 经既有过滤门下发；无新知识结构 | 本文 |
| 存档测试 | ✅ 2.8 / 2.1；`blocks.world` 迁移往返（v1 起步，迁移骨架照 chron 块） | 本文 + `STATE-DESIGN.md` §14 |
| 长期模拟测试 | ✅ 100 游戏日：ledger ≤ 上限、事件数 ≤ 日数 × 常量、重放相等、块 < 1 MiB | 本文（W8 长跑） |
| AI 故障测试 | ✅ 1.6（生成零模型）；`world.tick` 全程无模型调用（结构保证，断言 port 缺失不抛错） | 本文 |
| 视觉 / UX 测试 | ❌ 无 UI 改动（预览卡以 JSON/API 面交付） | UI 轨 |

### 10.4 不测什么（明确边界）

- 不测「生成的世界好不好玩 / 文本美不美」——玩法验收（`GDD.md` §24.4），需真人试玩。
- 不测模型结构化抽取的准确率——只测「提不出来时落默认值、非法 spec 被钳制」。
- 不测全球市场联动——首版没有（§1.2）。

---

## 11. 实现切片建议 W1…W8

> **公共约束**（每条都适用）：从最新 `master` 开分支；尽早开 Draft PR；**不合并**；
> 真跑 `bash tests/run_all.sh` 与 `bash tests/content_firewall.sh` 并把命令与结果写进 PR；
> 新文件不进 `tests/test_repo_layout.py` 必需清单；新增事件 `type` 在 PR 说明登记。

| # | 名称 | 内容 | 可改路径 | 禁改路径 | 依赖 | 标签 |
|---|---|---|---|---|---|---|
| **W1** | worldgen 内核：spec + 合成 + 世界校验 | `normalize_spec` / `choose_base` / `synthesize`（含词表覆盖项与三级文本来源）/ `roster` / `estimate` / `validate_world`；`WORLDGEN_CALL_BUDGET` 等常量 | `worldgen.py`（新）、`tests/test_worldgen.py`（新） | 一切既有文件 | 无 | 能力：逻辑 / 难度：高 |
| **W2** | ATLAS 校验与修复 | `validate_atlas`（C1–C7）/ `repair`（三类 error）/ provenance；三世界 + fixture 零 error 回归 | `worldgen.py`、`tests/test_worldgen.py` | `atlas*.py` 三文件（V8 零改动） | W1 | 能力：逻辑 / 难度：中 |
| **W3** | 生成会话编排 | `POST /api/world/generate` / `commit`；P1 模型端口接线（GUIDE 网关复用）；预览卡 / overrides diff；error 阻断（1.7） | `notdnd_web.py`、`tests/test_notdnd_web.py` | `worldgen.py`（只调用） | W2（同占 notdnd_web 队列 C 起点） | 能力：逻辑+文学 / 难度：高 |
| **W4** | world 内核：块 + 账本 + 日切 | `blocks.world` 形状与 `new_block`/`migrate_block`；`Entry` / 三等式 / 日切结算纯函数；`_world_tick` 骨架（失败隔离、`wtick_<day>`） | `world.py`（新）、`tests/test_world.py`（新） | `state.py` / `chron.py` / `neura.py` | W1（Entry/WorldSpec 形状） | 能力：逻辑 / 难度：高 |
| **W5** | 玩家经济动作接线 | M8 管线内部分支（trade / build 提案结算）；`econ.trade` / `econ.build` 事件；两本账双记（§6.6）；幂等 | `notdnd_web.py`、`tests/test_notdnd_web.py` | `world.py`（只调用）、提案契约 | W4 | 能力：逻辑 / 难度：高 |
| **W6** | 建造与委托式管理 | Project 对象 / 两条通道（CHRON 卡 + 日切）/ 指令对象与硬回问清单 / 维护扣账 | `world.py`、`notdnd_web.py`（两队列各一段，先 B 后 C 串行）、`tests/test_world.py` | `chron.py`（既有钩子只调用） | W5 | 能力：逻辑 / 难度：中 |
| **W7** | NEURA 挂接（M11 移交收尾） | 投影五键（§8.2）注入 `_neura_view`；`world.populate` 种群初始化；`worldgen.individuals` + 人口摘要；重判匹配 + `blocks.world.rejudge` + **`neura.py` 唯一改动**（`view.force_due_ids` 缺省空键）；`laws` 投影（`law_source="world"`） | `world.py`、`worldgen.py`、`notdnd_web.py`、`neura.py`（仅 §8.4 一处）、`tests/*` | `chron.py`、`state.py`、提案契约 | W4（W6 完成 projection 依赖的项目事实更完整，但非硬依赖） | 能力：逻辑 / 难度：高 |
| **W8** | 数据片 + 观察面 + 长跑 | `world.schema.json` 扩展 laws / region economy（§9.2）；`FORMAT.md` 小节；`GET /api/world/ledger`；100 日长跑（2.8 / 10.3 长期模拟行） | `data/schema/world.schema.json`、`data/FORMAT.md`、`notdnd_web.py`、`tests/*` | 三个既有 `data/worlds/*.json` 正文 | W5（观察面）、W1（schema 形状） | 能力：逻辑 / 难度：中 |

### 附：切片依赖图

```
W1 ──► W2 ──► W3（队列 A → C）
W1 ──► W4 ──► W5 ──► W6（队列 B → C）
              W4 ──► W7（投影 + populate + 重判；neura.py 一段独占）
W1 ──────────────────► W8（数据 + 观察面 + 长跑）
```

- **并行推荐**：W1 → W2 → W3 与 W4 → W5 → W6 两条主线在 W1 完成后可并行
  （`worldgen.py` 与 `world.py` 互斥文件）；W3 / W5 / W7 同占 `notdnd_web.py`，三片串行；
  W7 的 `neura.py` 段不得与任何其它片并行。
- **最小可玩出口**：W1+W2（生成物结构有效）与 W4+W5（经济有账可查）各自独立满足
  M12 两条出口条件之一——Master 可按出口拆里程碑验收。

---

## 12. 未决与待拍板

> 以下每一项都**不阻塞** W1–W5 的起草（纯函数层），但**阻塞**对应实现单的「可合并」判断。
> 建议由人（或人授权 Master Agent）逐条给结论，写回本 Issue 或另开决策 Issue
> （照 `NEURA-DESIGN.md` §12 的模式；拍板后本文按 §12.1 风格回填记录）。

| # | 事项 | 为什么需要人（而不是 Agent 自决） | 建议 | 阻塞哪片 |
|---|---|---|---|---|
| **P1** | **新模块命名**：`worldgen.py` / `world.py`（本文口径）vs `econ.py`（只做经济）vs 合一模块 | `GDD-BASELINE.md` §4.1 明文「新模块名暂以本表为准；设计单如提出更名，说明理由并请 Master Agent 确认」——命名权在 Master | **两模块方案**（V1/V2）：生成与运行时生命周期不同、文件队列可并行、`world.py` 以后可挂据点 UI 数据；若嫌名字泛，`worldsim.py` 亦佳 | 全部 |
| **P2** | **经济首版数值规模**：goods 数量（建议 8–12 种）、价格步进幅度（建议 ±10%/日钳制）、`LEDGER_MAX`（建议 512） | 数值手感是玩法判断； Agent 只有结构没有体验反馈 | 按建议值做常量起步，W8 长跑数据回调；全部一处可调 | W4 / W8 |
| **P3** | **块体量上限**：`domain` 档 80 NPC + 90 日补算下 `blocks.world` 实测大小；是否需要 ledger 分片 | 涉及存档体积与迁移成本（`NEURA-DESIGN.md` §12.1 P3 的同款问题在宏观账上更大） | 先落单块 + 硬上限；实测 > 512 KiB 再评估分片（另开单） | W4 / W8 |
| **P4** | **生成模型调用预算**：`WORLDGEN_CALL_BUDGET`（本文建议 12 次）与降级后兜底文本的质量底线 | 商业决策（模型单价 × 生成频次）；兜底文本质量影响首发观感 | 12 次起步；兜底短语表由 W3 实现时给最小可用版，**文案打磨另开内容单**（能力：文学） | W3 |
| **P5** | **`threshold` 是否扩档**：`neura.py:111` 只认 `allow/tolerate`；要不要加「罚金可违反」档（`penalty` 数值化） | 扩档 = 改 NEURA 消费侧契约（M11 已定型），跨里程碑变更 | **不扩**：v1 三值（forbid/tolerate/allow），`penalty` 保持展示字段；违反处置走 M8 管线的既有风险结算 | W7 / W8 |
| **P6** | **生成世界是否落库存文件**（`data/worlds/`）+ 命名规范（用户自建 key 与官方三 key 的冲突策略） | 涉及仓库内容治理（防火墙 / validate_data 对用户生成内容的责任边界）与 `GDD.md` §21 版权条款 | v1 不入库（实例只存 overrides + seed）；「保存为模组」推 M13 编辑器一并拍板 | W3（可选项） |
| **P7** | **建造扰动的事件钩子**：天气 / 劳资关系 / 事故掷骰何时接入（本文 §7.2 只落三类结构化扰动） | 玩法深度与 PRISM 检定契约相关（涉及给工程开骰点通道与否，§1.2 的克制边界） | v1 不接；把「事件钩子」列为 `blocked_reason` 的一个保留枚举值，将来以 `world.facts` 驱动 | W6 |
| **P8** | **重判上限的粒度**：`REJUDGE_DAILY_CAP=4` 按 NPC 计（本文）还是按「NPC × 事件类别」计 | 影响远场活性的手感（太少 = 世界死，太多 = 日成本漂移）——与 `NEURA-DESIGN.md` §12.1 P5 同性质 | 按 NPC 计 4 次起步，用 W8 长跑的 `important_far` 实际重判率回调 | W7 |
| **P9** | **委托式管理的 UI 面**：M12 只交付 API + 块内可解释字段（本文 §9.1），指令的创建 / 回问 / 暂停界面归谁 | UI 优先级是产品决策（并行轨的排期权在人） | M12 交付 API 面 + JSON 可观测；UI 单另开（依赖 UI-DESIGN 轨），回问暂用 M8 既有的一次性澄清通道 | W6（验收口径） |
| **P10** | **`known_refs` 的过渡口径**：本文 §8.2 用事件日志反推玩家已知线索；无事件日志的过渡档降级为「剧本全集」（过宽） | 「玩家已知道什么」直接改变 NEURA 决策与 P10 公式输入，口径过宽会让远场 NPC 过度活跃 | 事件日志已是 M7 交付物、过渡档仅存在于测试夹具——建议**不实现降级**，无日志按空表 | W7 |

### 12.1 拍板记录（2026-10-18，来源：人）

> 人于 2026-10-18 22:57 对 P1–P10 逐条拍板（派发单 Issue #378），作为 M12 的实现口径。
> §12 原表格**逐字保留**（含「为什么需要人」「建议」「阻塞哪片」三列）；本节只补「结论 +
> 对实现单的强制约束」。其中 **P2 / P4 / P6 / P7 / P9 / P10 与 §12 建议存在实质偏离**，
> 受影响章节已按拍板修正（§4.3 / §4.4 / §6.1 / §7.2 / §7.3 / §9.1 / §9.2）。
> 以下约束对 W1–W8 中对应实现单**强制生效**；若与实现单正文冲突，**以本节为准**，
> 并在该 PR 说明差异。

| # | 结论（拍板 2026-10-18，来源：人） | 对实现单的强制约束 |
|---|---|---|
| **P1** | **两模块方案**：`worldgen.py` / `world.py`（Master 裁定） | W1 与 W4 按两模块拆分（互不 import，§9.1 依赖图）；实现单**不得**合并为单模块或另起名 |
| **P2** | goods **尽可能多、重要常用类目优先**，且**必须按世界特性生成**——清单由世界类型 / 剧本 / 时代 tag 驱动合成；**时代违和是校验项**（不能出现「奇幻世界卖激光枪」）。**偏离**原建议「固定 8–12 种常量起步」 | `worldgen.synthesize` 按世界特性合成 goods 清单（衣食住行军保底覆盖，§6.1）；`validate_world` 必须含时代违和校验（越档 → **error**，进 P5 阻断路径）；**不得**实现固定常量清单 |
| **P3** | 采纳建议：先单块 + 硬上限，实测 > 512 KiB 再评估分片（另开单） | W4 先落单块 + `LEDGER_MAX` 等硬上限；W8 长跑记录实测块体积，超 512 KiB **另开单**、不在实现 PR 内夹带分片 |
| **P4** | `WORLDGEN_CALL_BUDGET` = **24**，以后视情况调整。**偏离**原建议 12 | W1 的常量默认值 = 24（§4.3 已同步）；调参只改常量一处，不改调用点 |
| **P5** | 暂不扩档（v1 三值 forbid/tolerate/allow），**但 schema 必须为扩展留可能性**——不同世界法律不同，甚至**不同区域 / 国家不同**，要能体现（`scope_place_ids` 方向是对的，不得把法律做成写死全局的结构） | W8 的 `world.schema.json` laws 定义必须保留按地区/国家扩展的可能性（`scope_place_ids` 方向）；**不得**把 laws 做成全局写死结构 |
| **P6** | **可入当前玩家的库、可分享给别人，默认不入系统库**（仓库 `data/worlds/` 官方数据不动）——玩家存档内保存完整世界实例；分享 = 导出 / 导入 JSON 文件（v1 文件级）。**偏离**原建议「v1 不入库（只存 overrides+seed）」 | 玩家可保存完整实例入**玩家存档**（`blocks.world` 载体，§4.4「保存与分享」）；实现**不得**把玩家生成物写进仓库 `data/worlds/`；分享走 v1 文件级导出 / 导入（导入过 `validate_world` + `validate_data` + 内容防火墙） |
| **P7** | **必须接入建造扰动**——这是游戏乐趣之一；**随机性与合理性并重，不能乱安排**：扰动由 `world.facts` / 世界状态事实驱动（有因果依据才触发），随机性走 PRISM 检定或种子确定性公式，每次扰动可解释（journal 留痕：什么事实、依据什么、结果如何）。**反转**原建议「v1 不接、只留保留枚举」 | W6 按 §7.2 扰动表实现（类型 / 前置事实条件 / 概率参数 / 效果公式 / 留痕五要素齐）：事实驱动（无事实支撑不触发）、随机性只在概率层（PRISM 检定或种子公式，**不开自由骰**）、每次触发 journal 留痕；实现**不得**只留保留枚举不接实现 |
| **P8** | 按 NPC 计 4 次 / 日（`REJUDGE_DAILY_CAP=4`），W8 长跑回调 | W7 按 NPC 计数实现重判预算（§8.4）；常量一处可调，W8 长跑记录实际重判率回调 |
| **P9** | **需要 UI，Master 自行排期**：M12 交付 API 面 + 块内可解释字段不变，UI 派发单由 Master 在 W6 合并后开出（依赖 UI-DESIGN 轨排期权已获人授权）。**偏离**原建议「UI 另开、回问用 M8 通道」 | M12 验收口径不变（API 面 + JSON 可观测，§9.1 已同步排期决定）；实现单**不因 UI 拍板而扩大 M12 范围**，UI 属后续派发单 |
| **P10** | **不考虑旧档**：开发阶段不向下兼容，破坏性更新直接作废（删除）旧档——`known_refs` 无降级路径（无日志场景在真实档中不存在）。**偏离并升格为全局口径** | **全局口径，适用于所有里程碑**：后续设计文档与实现单**不再把存档迁移 / 兼容列为约束项**，破坏性 schema / 块形状变更直接作废旧档（删除），不写迁移代码；M7 的迁移机制保留给**同一大版本内**的增量演进 |

**拍板后实现顺序不变**：W1 → W2 → W3 与 W4 → W5 → W6 两条主线（W1 完成后可并行），
见 §11「附：切片依赖图」。

### 12.2 实现裁定记录（W1–W7）

> M12-W8（Issue #396）按派发单职责，把 W1–W7 各实现 PR「风险与口径补充」节的
> 设计留白裁定统一回填入册（**每条注明来源 PR**；本文正文其余章节不因回填改动，
> 章节号不重排）。后续实现单与审查以本节为实现口径的权威索引。

**W1（世界生成内核，PR #381）**

1. **import 清单**：`worldgen.py` 只 import 标准库 `copy` / `hashlib` / `random`；测试内断言 import 白名单与「纯函数入参不改写」。（来源：PR #381 口径 1）
2. **goods 校验只在 world 带 `goods` 键时生效**：三个官方世界与 fixture 没有 goods，`validate_world` 对它们保持零 error（W2 回归锚）；`spec.economy_on=True` 而合成结果缺 goods 仍是 error（`E_SPEC_ECONOMY`）。（来源：PR #381 口径 2）
3. **era 联动规则**：`era ∈ {fantasy, modern, future}`；goods.era **高于** `world.type` → `E_GOODS_ERA` error（奇幻世界卖激光枪）；**低于** → `W_GOODS_ERA_LOW` warn（未来世界有老物件，可接受）——设计原文只给「越档 → error」的方向性示例，「低于 = warn」为本片拍板。（来源：PR #381 口径 3）
4. **goods 落点与顶层附加键**：goods 暂放 `world.module` 顶层 `goods` 数组（条目 `{good_id, name, category, tier, base_price, stock, produced_by, era}`）；`worldgen_provenance` / `lexicon_overrides` 同为顶层附加键——已由 W8 转正进 `world.schema.json`（§9.2）。（来源：PR #381 口径 4；转正：PR #396 对应实现）
5. **est_tokens 估算**：每次端口调用固定 `+128`（`WORLDGEN_TOKENS_PER_CALL`）；W3 接真实网关后以真实 usage 差值回填，不改 worldgen.py。（来源：PR #381 口径 5）
6. **laws 非法 threshold**：合成期归一为 `forbid`；校验器对直接构造的非法值报 `E_LAW_THRESHOLD` error（`neura.py:111` 契约：只认 allow/tolerate 两个容忍值）。（来源：PR #381 口径 6）
7. **ATLAS 零改动（V8）**：`atlas*.py` 三文件一行未动；W1 不调用 `compile_world`，校验对象是 `world.module` 形状本身。（来源：PR #381 口径 7）

**W2（ATLAS 校验器，PR #385；修复 PR #388）**

1. **C2 落地为「逐帧连通 + 零坐标分量豁免」**：unlisted / abstract 区域与其关键地点是零坐标分量（帧间只靠连接相通，经跨帧门进图，合法图外浮岛）；口径：帧内**有坐标**的分量必须并入本帧主路网，断连报 error。（来源：PR #385 新口径 7）
2. **C5 venue 抽查只在世界 random_tables 有场地行时执行**（世界无场地行时生成器落 system 兜底表，worldgen 零 IO 读不到、无法核对）。（来源：PR #385 新口径 8）
3. **C6 白名单抽查表为最小实现**：`C6_SPOT_CHECKS` 精确名匹配（wilderness × 铁锭/聚变电池/脉冲步枪/激光枪 等）；`regions[].economy` 的 `produces` 声明同表抽查（该字段 W8 转正前为可选）。（来源：PR #385 新口径 9）
4. **C7「zone_steps 折算」**：metric 地点/战术帧上相邻一格 = 1 跨区，带档只能是 short；世界帧不查几何——编译器连接的是脚印边缘而非锚点格（防误伤）。（来源：PR #385 新口径 10）
5. **repair 边界**：只修三类 error（C2 断链补边 / C3 缺门补门 / C4 不可达挂接），**只新增边**；C3 多门不修（违反只新增边原则）如实报；「该帧整体重掷」由生成流水线编排（§5.2），repair 本身确定性执行。（来源：PR #385 新口径 11）
6. **`_bridge_components` 键归一**：`placed` / `analysis`（short rid）与 `cells_by_rid`（full id）两形态经局部 `_lookup` 归一（直接命中，否则取 full id 尾段 short rid 查表），不改 `components()` 返回形态——修复合成世界地面触碰图断连补桥时的 KeyError。（来源：PR #388）

**W3（生成会话编排，PR #389）**

1. **鉴权裁定（§6.5）**：生成 / 提交 / 导出 / 导入是**管理性**操作——单人会话 `X-Session` 即可；房间多名在座成员必须以**房主**成员令牌调用，player / observer 一律 403 `not_authorized`；预览只回给调用者、不进房间事件流。（来源：PR #389 口径 1）
2. **会话过期口径**：进程内 TTL `WORLDGEN_SESSION_TTL_S = 3600` 惰性清理；commit 一次性（成功即销毁会话，防重复入库）；服务重启 = 会话全失（重新 generate 即可，生成幂等可重跑）；预算计数器不落盘（V14）。（来源：PR #389 口径 2）
3. **导出 / 导入**：已落地（P6 v1 文件级）；运行时导入校验 v1 = `validate_world` 零 error + kind 检查；`validate_data` / 内容防火墙是 CI 对已跟踪仓库文件的门，运行时导入不跑 shell（v1 边界）。（来源：PR #389 口径 3）
4. **预算计数对齐**：端口每次调用后用网关回的**真实 usage**（prompt + completion）差值回填 `est_tokens`；离线时计数恒 `{calls: 0, est_tokens: 0}`。（来源：PR #389 口径 4）
5. **离线口径**：离线时给 worldgen 传 **`port=None`**（不是不可用端口对象）——「端口对象存在」会被当作在线记账记出幽灵调用；`None` 才是 worldgen 语义里的「无模型」。（来源：PR #389 口径 5）
6. **overrides 覆盖面**：v1 只覆盖 regions / factions / laws；goods 不进 overrides（属 P3 文本 / 结构重生成，另单）；已存在条目的 `scope_place_ids` 不被 upsert 改写。（来源：PR #389 口径 6）
7. **导入实例的 `spec` 不参与校验**：`validate_world(world)` 不带 spec（导入物可能来自别处，C8 规格核对只对合成路径有意义）。（来源：PR #389 口径 7）

**W4（world 内核，PR #384）**

1. **幂等键形状**：`wtick_key(day) = "wtick_%04d"`（如 `wtick_0001`）——零填充对齐 A0 幂等键 `^[A-Za-z0-9_-]{8,64}$`（裸 `wtick_1` 只有 7 字符）。（来源：PR #384 口径 1）
2. **幂等键载体**：`economy.wticks`（chron 块 `idempotency` 同位；§6.1 形状表的块形状补充）。（来源：PR #384 口径 2）
3. **genesis 重放基线**：`economy.genesis = {balances, goods}` 创建时一次写入——三等式①「replay(genesis, events)」的 genesis 落点；旧形状块迁移时用当前快照回填一次并记 note。（来源：PR #384 口径 3）
4. **goods 键控存储**：worldgen 产物是 list，进块转 `{good_id: {...}}`，条目字段逐字保留。（来源：PR #384 口径 4）
5. **生产/消费 v1 公式**：生产 = `SETTLEMENT_YIELD_PER_DAY(2)` × 位于 `produced_by` 地点的活跃据点数；消费 = 食/住两类必需品按人口 × `CONSUMPTION_PER_CAPITA(1)` 均摊（余数按 good_id 字典序），受当日可用库存钳制（吃不起就少吃，不拒日切）；净额为 0 不记 Entry。（来源：PR #384 口径 5）
6. **价格步进**：短缺 → 涨、过剩 → 跌、库存缓冲内不动；`step = max(1, floor(price × 0.10))`——最小步长 1 是整数价格下 ±10% 钳制的最低可动幅度（低价 good 一步可超 10%）。（来源：PR #384 口径 6）
7. **seed 哈希**：`sha256("world|<seed>|<rule_id>|<day>|<salt>")` 前 8 hex mod 1000（内建 `hash()` 跨进程不稳定，禁用）。（来源：PR #384 口径 7）
8. **prism 模式不开自由骰**：world 不 import prism；检定结果由编排层经 `prism_results={rule_id: bool}` 显式传入，未传 = 不触发。（来源：PR #384 口径 8）
9. **工资/维护付款来源** = `balances["coin"]` 中央金库（v1 宏观账单账户口径）；发不出 → `blocked_reason="funds"` 且无 Entry。（来源：PR #384 口径 9）
10. **工程里程碑推进与建造材料结算归 W6**；W4 只做工程工资结算与「所需 good 断供 → blocked」的结构化判定（供应链断/资金断两类；敌对占领经扰动表 `place_lost` 事实驱动）。（来源：PR #384 口径 10）
11. **扰动冷却落点**：`projects[pid].cooldowns[rule_id] = day`。（来源：PR #384 口径 11）
12. **good_price_spike 作用对象** = 匹配事实的 `subject_ref`（须是块内 good_id）——扰动对象由事实本身指认。（来源：PR #384 口径 12）
13. **人口摘要档位**：0=empty / 1–9=low / 10–49=mid / 50+=high。（来源：PR #384 口径 13）
14. **catch_up 折叠路径**：价格只按末日步进一步；折叠日不写 wtick 键，`economy.day` 推进本身即幂等护栏（倒退日切被 `world_tick` 拒绝）。（来源：PR #384 口径 14）

**W5（玩家经济动作接线，PR #391）**

1. **事件 type 枚举**：`econ.trade`（玩家 trade 提案伴随）、`econ.build`（玩家 build 提案伴随；常量已登记、结算路径 W6 接）、`econ.day`（日切块级提交，幂等键 `wtick_<day>`，`source="system"`、`visibility=gm`）。（来源：PR #391 口径 1）
2. **行动表 `econ` 载荷**：世界规则包在行动表条目上声明交易参数（提案契约零改动）；缺失 / 形状不对 / 未知 kind → 按普通行动结算（「脏值逐项丢弃，不猜」口径）。（来源：PR #391 口径 2）
3. **双记组装点**：`changes` 双块在 `_action_settle_commit` 内组装（`[rules_change] + [world_change]`），一次 `handle.commit`（A0）；`Entry.refs.event_seq` 指向携带它的事件。（来源：PR #391 口径 3）
4. **接线时序**：与 NEURA 完全同款，唯一新口径是**逐日补结算循环**——已过日期按日初（`day × 1440`）结算、当前日按实际世界分钟；同日不重复由「`economy.day` 循环条件 + 块内 `wticks` 命中」两层保证。（来源：PR #391 口径 4）
5. **`world` 块缺失 = 未接世界**：老档 / 未开世界的会话推进不创建 `world` 块、不落任何事件（§9.7 同款「无事可做不落事件」）。（来源：PR #391 口径 5）
6. **econ 分支只在同步路径**（`_action_settle_commit`）；长行动到期结算的经济分支归 W6（工程 / 指令是长周期动作）。（来源：PR #391 口径 6）
7. **贸易的 coin 账户语义**：`settle_trade` 扣的是 `blocks.world.economy.balances["coin"]`（§7.2 中央金库口径的宏观账），**不是** PRISM 角色钱袋——两本账各记各的，转移只发生在 M8 结算过的动作边界（§6.6）。（来源：PR #391 口径 7）

**W6（建造与委托式管理，PR #393）**

1. **工期折算是速率常数**：`ceil(labor_days / LABOR_EFFICIENCY_PER_DAY) × MINUTES_PER_DAY`——劳动效率是人日/日的速率常数，不是时间常数；时间单位只有 CHRON §2.3 的分钟。（来源：PR #393 风险节 2）
2. **卡时长与工期解耦**：CHRON 卡到期 = **开工**结算（材料/资金/劳力承诺）；里程碑到期（开工 + 工期）由日切第 4 步完工——行动表声明行动时长，工期由 requirements 折算，两者互不绑定。（来源：PR #393 风险节 2）
3. **新事件 type `world.manage`**：管理类变更（暂停/复工/撤销/指令操作）走薄路由 `POST /api/world/project` / `POST /api/world/directive` + `world.manage` 事件；建造本体仍走既有通道（M8 提案 `econ.build`；CHRON 卡到期结算不开独立事件，A0）——管理操作不是「行动」，塞进 M8 提案需要伪造行动表条目。（来源：PR #393 风险节 1）
4. **开工条件四/五由编排层注入**：`place = {place_id, kind, validated}`（`_world_place_view` 从本局 atlas 投影 + C1–C4 零 error 缓存判定）；world.py 零 import atlas（§9.1 依赖方向）。HTTP 编排路径的日切（`_world_tick`）不注入 places——queued 背景工程自动开工在编排面未接线，纯函数层以 `places` 参数验证（后续开档流程接线时收紧）。（来源：PR #393 风险节 2；编排面注记：PR #396 长跑实测）
5. **扰动联动作用面**：扰动效果只作用于**在场工程**（`DISRUPTIBLE_STATUSES = (running, active)`；`active` 为 W4 夹具兼容值）；`progress_loss` 吃 `progress.labor_done_days`（int 形状保留兼容分支）；劳动力账户 = `balances["labor"]`（人日池，开工一次性承诺扣足 `labor_days`，新 Entry kind `labor_commit`）。（来源：PR #393 风险节 2/3）

**W7（NEURA 挂接，PR #395）**

1. **`known_refs` = `guide.known_clues`**：M7 起玩家已知线索的运行时权威就是它（state 事件日志没有 clue 事件类型——§8.2 表「线索事件」在现实现中的落点）；P10：无日志按空表，**不实现**「剧本全集降级」。（来源：PR #395 投影节）
2. **`main_thread_refs` / `faction_refs`**：主线 thread 参与人存的是**名字**，经档案 `name→id` 映射（映射不到的原样保留）；faction_refs = 静态档案 faction 非空 npc_id 集，`world_module.factions` 给了就按其 id/name 校准（不在势力表的不计）。（来源：PR #395 投影节）
3. **`importance_overrides` / `world_facts`**：来自 `blocks.world.facts` 里 `kind="importance_override"` 条目（后写覆盖先写，facts 按 (minute, fact_id) 有序）；`world_facts` 投影成 `{subject_ref: [fact]}`，法律事实同走此处。（来源：PR #395 投影节）
4. **`travel_minutes` 不定义新时间常数**：档位→分钟表由编排层把 `BAND_MINUTES` 原样注入（same→0 / adjacent→short / near→medium / mid→long / far→dangerous）；同目的地多出发点取最小；`dangerous` 档给常量值，风险归 PRISM。（来源：PR #395 投影节）
5. **laws 恰一实例才采用**：世界实例（sidecar）的 `world.module.laws` 逐字段清洗进 view；**恰有一个**实例时采用（campaign-scoped；零个 = 无法律 M11 行为；多个 = 歧义不猜，等开档流程绑定实例后收紧）。（来源：PR #395 投影节）
6. **重判匹配与预算落盘**：`match_event` 三规则（地点 / 引用 / 势力级事实）；层过滤归编排层（world.py 不 import neura）；只挂主结算路径（`stuck`/`failed` 收尾路径不收集——失败回执无世界语义）；`register_rejudge` 的 counts 变更随本 tick 的块级提交落盘，不单开重判事件（A0）；`force_due_ids` 是**下一次** tick 的输入。（来源：PR #395 重判节）
7. **populate 不接 genesis 批**：`populate` 交付为纯函数 + 测试；编排层「战役 genesis 批」接线待开档流程（W3 裁定不接 `blocks.world`，实例绑定尚不存在）；运行时块键面镜像 `neura.empty_npc`（extra 键会被 `neura.migrate_block` 丢弃）；needs 抖动用 sha256 种子公式（不 import random）；日程 slot 仅在 `places` 注入地点时生成。（来源：PR #395 populate 节）
8. **individuals 保留 docs 指针**：产物 `source` = `docs/scenario/worldgen-individuals.md:1`（$defs.npc 的 source 是 docs 指针纪律，生成个体没有真实 docs 落点）；`count_hint` 缺省按 `SCALE_INDIVIDUALS["region"]`（3..6）确定性抽取；模型润色（name/drive）归生成会话流端口，v1 只做离线确定性合成；`unexplored` 层不产生具名 NPC。（来源：PR #395 populate 节；`docs/` 指针落点遗留问题见本节末待办）

**W8 待办（本单登记，不在本 PR 处理）**

- W7 遗留：`docs/scenario/worldgen-individuals.md:1` 是**指针性 source**（$defs.npc 纪律要求 docs 指针，但该文档尚不存在/未含个体生成节）——按派发单禁改 `docs/**`，由 Master 后续**独自成 PR** 的文档单处理（`docs/` 新文档不得夹带在实现 PR 内）。（来源：PR #395 populate 节；PR #396 登记）

---

## 附录 A · 与其它设计单的接口一览（便于交叉审查）

| 对方 | 我给它的 | 它给我的 | 冲突风险 |
|---|---|---|---|
| `STATE-DESIGN.md` | 新块名 `world`、五个事件 `type`（§9.1）、每 tick 每域至多一条事件 | `commit()` / `view()` / `replay()` / 幂等索引 / 惰性迁移口径 | 低（块不透明；枚举登记照 P9 纪律） |
| `CHRON-DESIGN.md` | 一条工程通道消费者（玩家工程进 `chron.actions`） | `world_minute` / `advance_to` 停点 / `next_barrier` / 分钟口径 | 中（V7 与 NEURA P7 反向：一进一出不混用，§7.3 已论证） |
| `ACTION-DESIGN.md` | 两个内部分支消费者（trade / build 结算）+ 生成会话两路由 | 提案 Schema / 结果契约 / 幂等键 / 排程钩子语义 | 低（提案不新增字段） |
| `NEURA-DESIGN.md` | view 五键 + `force_due_ids`（缺省空 = M11 行为不变）、种群初始化后的完整块 | 四段流水线复用 / tick 挂接先例 / P8 laws 消费契约 / P10 公式 | 低（neura.py 仅 §8.4 一处改动，缺键逐字节兼容） |
| `MULTIPLAYER-DESIGN.md` | `econ.*` 事件的 `visibility` | 每成员视角过滤 / 受众冻结 | 低（复用既有门） |
| `ATLAS-DESIGN.md` | **无改动**——校验与修复只读其输出形状 | `compile_world` / `generate_site` / `generate_tactical` / `range_band` / 词表机制 | 低（V8 零改动承诺；唯一扩展是调用次序，住在 worldgen） |
| `GUIDE-DESIGN.md` | 生成文本字段的模型端口消费者 | AI 网关 / 单回合预算先例 | 低（端口注入，缺省离线） |

---

**（文末）本文不含任何代码改动；§12 的 P1–P10 已于 2026-10-18 拍板（见 §12.1），拍板结论与 §12 建议冲突处以 §12.1 为准。**
