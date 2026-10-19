# 02C · 六角格战场（Hex Battlefield）

**战术空间：axial 六角格 ＋ 高度差　｜　位置：ATLAS 之上的局部战场**

> 本文是 M13 六角格战场的**仓库内设计基线**，逐条承接 Issue #415 的冻结契约。
> 本文只搬运 #415 已冻结的规则，不新增、不改写、不做「顺手优化」；
> 数据层的 `source` 只认 `docs/<system|scenario>/<文件>.md:<行号>`（见 `data/FORMAT.md` 第 6 节），
> 因此本文为后续数据条目提供**行号锚点**。旧 ATLAS 抽象投影与旧存档路径不受影响（见第七节）。

---

## 一、几何与坐标

六角格战场是 ATLAS 之上的**局部战术空间**，不取代世界地图，也不把一个 ATLAS zone 直接当成一个 hex。ATLAS 提供地点、房间、场景要素、种子与上下文；战术层负责本场战斗中的格位、移动、视线、高度与对象状态。

坐标使用 **axial 六角坐标** `q`、`r`，均为整数。每个格有六个水平邻格，偏移固定如下。

- **锚点 · 六邻偏移**：`(1,0)`、`(1,-1)`、`(0,-1)`、`(-1,0)`、`(-1,1)`、`(0,1)`。设计冻结值（#415）。
- **锚点 · 六角距离公式**：令 `dq = q2 - q1`、`dr = r2 - r1`，则距离为 `max(|dq|, |dr|, |dq+dr|)`。设计冻结值（#415）。

`cell_id` 是**稳定身份**：不能仅由坐标生成，也不能假设坐标永不改变。不同楼层或不同表面允许出现相同的 `q/r`，必须以 `surface_id` 区分；任何位置都以 `cell_id` 作为权威引用。

世界渲染的朝向属于 UI 表现，**不得改变**坐标与距离语义。寻路、视线与范围预览共用同一个几何内核，不得各自复制一份距离算法。

---

## 二、静态战场数据

静态战场的正式 `kind` 为 `tactical.battlefield`，`schema_version` 初始为 `1`。静态地图与运行时状态是两份数据，不混写在同一个块里。

**身份与来源**：`battlefield_id`、`world_id`、`site_id`、`atlas_ref`、`seed`、`generator_version`。随机生成必须以种子重现——同一 `seed` 与同一生成器版本必须得到同一战场。

**geometry**：`type = hex_axial`，采用第一节的坐标与距离内核。

**scale**：`mode` 只有两种口径。

- **锚点 · metric 相邻格中心距离**：`scale.hex_size_cm` 默认 **150 cm**，即相邻格中心距离。设计冻结值（#415）。

abstract 模式的距离与移动一律使用**明确的格 / 步单位**，不得伪装成米，也不得跨 ATLAS 帧自动换算。

**cells** 至少包含：`cell_id`、`q`、`r`、`surface_id`、`elevation`、`terrain_id` / `terrain_tags`、`movement_cost`、`blocks_movement`、`blocks_los`、`obstacle_height`、`cover_grade`、`hazard_refs`、`feature_refs`、`spawnable`。

**vertical_links** 至少包含：`link_id`、`from_cell_id`、`to_cell_id`、`kind`、`movement_cost`、`bidirectional`、`requirements`，以及可触发的检定 / 效果引用。

- **锚点 · vertical_link.kind 受控枚举**：`stairs` / `ramp` / `ladder` / `lift` / `climb` / `jump` / `fall` / `teleport`。设计冻结值（#415）。

**高度口径**：metric 模式使用整数厘米 `elevation_cm`，abstract 模式使用整数层级 `elevation_level`。两种口径不得混用。

**元数据**：`schema_version`、`seed`、生成器版本与可追溯的 ATLAS 来源。Schema 必须由 JSON Schema 校验，纳入 `data/schema/registry.json` 与 `data/FORMAT.md`，并提供版本迁移与旧档缺省行为的定义。

---

## 三、运行时战斗状态与权威

动态状态与静态地图分离。`battle_state` 可以作为会话快照中的独立块，至少包含：`battle_id`、`schema_version`、`battlefield_id`、`round` / `turn`、`active_unit_id`、`state_revision`、`rng` / 随机记录引用、参与者位置、战场对象动态状态与事件序号。

**participant** 只保存 `unit_id`、`cell_id`、朝向（仅在规则需要时）、占格 `footprint` 与战斗临时字段引用。角色的属性、活力、资源、抗性、状态、韧性 / 破韧、装备与库存**继续以现有 PRISM / 会话状态为权威来源**，不得复制出第二份会发生漂移的 HP 或 inventory。

**object instance** 保存 `object_id`、`definition_id`、`footprint` / 当前格、`state`、`durability`、是否可移动 / 可破坏、阻挡与视线属性、互动选项以及已触发状态。门、警报、炸药、可破坏掩体等使用显式状态机与事件记录，而不是只改描述文本。

所有状态变化产出结构化事件，`state_revision` 随之递增。行动支持 `expected_revision` 与 `idempotency_key`：重复请求不得重复扣 AP、伤害、掉落或触发机关。

**不变式**：

1. 同一时刻不允许两个实体占用互斥的实心占格；多格单位与对象按 `footprint` 校验。
2. 地形的移动阻挡、视线阻挡、掩体等级与危险 / 效果是**分离属性**，不得用一个 `wall` 布尔值代替。
3. 地形与场景物件的效果由**有版本的数据定义**；代码只提供通用机制，禁止把每种世界、每个地形名称写成硬编码分支。
4. 生成器不得把出生点放在不可达区域、实体重叠处或必然无路可走的位置；手工模板同样必须校验并可复现。

---

## 四、高度差与移动坠落

metric 地图以 `elevation_cm` 表示相对高度，abstract 地图以 `elevation_level` 表示相对层级。**高度差 = 目标高度 − 起点高度**，是有符号量。高度不是额外的二维坐标，也不允许用一个全局 `z` 值推断跨楼层的可达性。

### 高地阈值与 high_ground

- **锚点 · 高地阈值**：metric 默认 **100 cm**；abstract 默认相差至少 **1 个 elevation_level**。设计冻结值（#415）。

具体战役 / 世界规则可通过规则数据覆盖该阈值，**不能在 Python / JavaScript 中写死**。

攻击者高于目标达到阈值、且攻击视线有效时，输出 `high_ground = true`。

- **锚点 · 高地优势（指针）**：沿用 `docs/system/02a-tactics-engine.md` 第 93 行「优势来源表」的「使用高地的射手 +1」，对符合条件的远程攻击**只应用一次**。指针，不复制该表，非新值（#415）。

近战不因高度本身自动获得同一加值，除非对应规则数据明确规定。高地是**空间上下文**，不是直接扣 / 加伤害：不能跳过命中等级、抗性 / 弱点、状态、韧性与首领阶段结算。构建引擎必须按其原生规则数据解释空间标签，**不得**把 d20 的助势骰直接换算成构建引擎的骰池成功数。

### 移动与坡面

基础移动预算来自**现有单位派生移动能力**；战场移动内核只负责计算路径成本，不新增平行的角色移动属性。

metric 地图的普通可通行格，成本等于一格距离；地形可以显式提高成本。abstract 地图按格 / 步计成本。

- **锚点 · 连续可走坡面高差上限**：相邻格高度差不超过 **50 cm** 时可视为连续可走坡面（除非地形标记禁止）。设计冻结值（#415）。

超过该上限时，普通走路必须有可通行的楼梯 / 坡道 / 电梯等 `vertical_link`。梯子、攀爬、跳跃、飞行必须由单位能力、物件或规则数据授权，不能被普通寻路器自动当作免费通路。

路径规划同时检查占格、移动阻挡、地形成本、垂直连接与单位能力，并返回可达格、实际路径、总成本与阻塞原因。客户端预览不等于权威结果，服务端必须重算。

### 坠落

坠落与跳跃不是普通移动的隐式捷径，必须是可验证的行动或连接，并写入事件。

- **锚点 · 坠落无伤害下限**：坠落高度**小于 150 cm** 不造成坠落伤害。设计冻结值（#415）。
- **锚点 · 坠落伤害档**：达到 **150 cm** 起，每 **300 cm**（不足一档向上取整）造成 **1d6** 坠落伤害，并使单位**倒地**。设计冻结值（#415）。

该表必须放在规则数据中，可由世界规则覆盖，不能硬编码在移动算法里。伤害最终仍经 PRISM 伤害结算管线。

---

## 五、距离、视线与掩体

### 距离

metric 模式的武器 / 能力距离使用**格中心的三维欧氏距离**：水平中心位置由 axial `q/r` 与 `hex_size_cm` 确定，垂直分量为高度差；结果换算到现有武器 / 能力的米制范围。

abstract 模式使用六角格距离；只有声明为 hex / step 口径的能力可以使用。

### 视线

视线使用**唯一、确定性的 axial hex-line 遍历**。射线从攻击者眼高射向目标可见部位高度，检查中途格、地形 / 对象的 `blocks_los` 与阻挡物高度。

- **锚点 · hex-line 并列选格**：按下方 6 条执行，实现与测试均以此为准。设计冻结值（#433）。

1. 起点 A、终点 B 先转 cube：`x = q`、`z = r`、`y = -x - z`。
2. 步数 `N = max(|dx|, |dy|, |dz|)`，即六角距离；`i = 0…N` 各取一次采样点。
3. 采样用**有理数**计算（整数分子 / 分母），不得用浮点插值——避免 `0.4999999` 一类漂移让同一输入产生不同结果。
4. 每个采样点对三个分量取整；若取整后 `x + y + z ≠ 0`，把**取整误差最大**的分量重算为 `-x - z`（并列时按 `x → y → z` 顺序取先者）。
5. **并列裁决**：当采样点恰好落在两格共边 / 共角上（两个候选格到射线距离相等），取 cube 坐标 `(x, z)` **字典序更小**的格。
6. 该规则只决定「穿边 / 穿角时把哪一格纳入 `blocked_cells` 检查」，**不改变**六角距离语义，也与 UI 渲染朝向无关。

**并列构造例子**：A 为 axial `(0, 0)` → cube `(0, 0, 0)`，B 为 axial `(2, -1)` → cube `(2, -1, -1)`，`N = max(|2|, |-1|, |-1|) = 2`。`i = 1` 的采样点为 `(1, -1/2, -1/2)`，恰好落在两格共边上，候选为 axial `(1, 0)`（cube `(1, -1, 0)`）与 axial `(1, -1)`（cube `(1, 0, -1)`），两者到采样点距离同为 `1/2`。按第 5 条比较 `(x, z)`：`x` 同为 `1`，`-1 < 0`，故选 axial `(1, -1)`。该例可直接改写为几何内核的测试用例。

### 掩体

移动阻挡、视线阻挡、阻挡高度与 `cover_grade` **分开计算**。

- **锚点 · 掩体四档（指针）**：沿用 `data/system/tactics_combat.json` 的既有四档定义与 `docs/system/02a-tactics-engine.md` 第 257–266 行「掩体」小节；档名为 `light`（轻度）/ `medium`（中度）/ `heavy`（重度）/ `full`（完全）。指针，不新造档位、不复制数值表，非新值（#415）。

视线被完全阻断时，普通远程攻击不得结算；部分掩体按当前引擎的数据规则应用。

### 近战可达性

近战可达性同时检查现有武器范围、格距离、垂直可达性与遮挡；不能因为两单位 `q/r` 相邻就忽略楼层、墙或大体积目标。

空间查询必须返回可解释原因（距离、垂直差、遮挡格、掩体档、能力限制），供 UI 预览与回归测试使用。

---

## 六、双引擎适配契约

ATLAS / 六角格层是**空间事实来源**；PRISM 战术引擎与构建引擎是两种不同的规则解释器。两者消费同一份 `TacticalContext`，但各自按原生规则与数据结算。

### TacticalContext 字段

`TacticalContext` 至少提供：`battlefield_id`、`actor_id`、`target_id`、`from` / `to` 的 `cell_id`、`hex_distance`、`metric_distance` 或 `abstract_distance`、`signed_height_delta`、`line_of_sight`、`blocked_cells`、`cover_grade`、`high_ground`、`reachable`、`path_cost`、`terrain_tags`、`active_feature_refs`、`state_revision`。

### 固定管线

1. 接收行动意图：actor、action / weapon / ability、target 或 destination、interaction、expected_revision、idempotency_key。客户端不得提交命中结果、骰点、伤害数值、资源变化或新权威位置。
2. 服务端读取当前 `battle_state` 与权威单位状态，校验行动合法性；空间内核计算 `TacticalContext`。
3. 按 session / world 当前的 `rule_engine` 选择适配器：tactics adapter 或 forge / build adapter。
4. 适配器把空间上下文转换为该引擎**已定义**的情境修正 / 效果输入；调用现有规则核心结算，不复制攻击、抗性、伤害、韧性、状态或资源公式。
5. 返回结构化 `RuleResult`：成功 / 失败、命中等级或构建引擎结果、伤害 / 状态 / 资源 delta、空间 / 场景效果提案、原因与事件；由权威状态层验证、原子提交并递增 revision。
6. 叙事层只解释已提交的事实，不得重新裁定结果。

### 引擎边界

- d20 战术引擎保留命中等级、自然 1 / 严重失手、现有 AP、掩体档、抗性 / 弱点 / 免疫 / 吸收、状态、韧性 / 破韧、连携与首领阶段。
- 构建引擎继续使用其骰池、成功阈值、动量、应力、符纹 / 构建能力与自身战斗结算；不得降格为 d20，也不得用 d20 modifier 伪装成骰池成功。
- 不支持的空间效果必须显式返回 `unsupported` / `unmapped`，并进入测试与规范待办，不得静默丢弃；若规则含义不同，使用引擎专属的数据映射。
- 空间规则（距离、视线、可达性、高度差）**不在两套规则中各写一份**；适配器只能转换事实，不能再判几何。
- 随机性必须来自服务端的可追溯 RNG 或现有 PRISM 设施；客户端与 LLM 均不是权威裁定者。

---

## 七、事件与存档兼容

### 事件语义

至少定义下列结构化事件（命名可统一，语义不可丢）：`move_started` / `move_committed`、`attack_resolved`、`object_state_changed`、`hazard_triggered`、`fall_resolved`、`turn_started` / `turn_ended`、`combat_started` / `combat_ended`。

事件包含 `sequence`、actor、目标、from / to、`rule_engine`、输入修订号、结果摘要与来源规则引用。

### 存档与兼容

旧存档**惰性迁移**；只有进入新式战斗时才新建 `battle_state`。保存静态地图的种子 / 模板版本与动态增量，能够在刷新、重连或服务端恢复后重建相同战场。

地图或模板版本变化**不能静默改变**已经在进行中的战斗。

无战术地图的旧场景继续走现有 ATLAS 抽象投影 / 心象剧场路径；新系统接线不得让旧存档无法载入，也不得让未进入战斗的场景强制生成棋盘。

---

## 八、行号锚点表

行号为本文交付时的**实际行号**（`grep -n` 实测）；`#416` 的数据条目按此写 `source`。

| 锚点 | 行号 | 供 #416 使用的字段 |
|---|---|---|
| 六邻偏移 | 18 | `geometry.neighbor_offsets` / `hex_neighbors` |
| 六角距离公式 | 19 | `geometry.hex_distance` |
| metric 相邻格中心距离 150 cm | 37 | `scale.hex_size_cm` |
| vertical_link.kind 受控枚举 | 45 | `vertical_links[].kind` |
| 高地阈值 100 cm / 1 level | 78 | `high_ground.threshold_cm` / `high_ground.threshold_level` |
| 连续可走坡面高差上限 50 cm | 94 | `movement.slope_max_cm` |
| 坠落无伤害下限 150 cm | 104 | `fall.no_damage_below_cm` |
| 坠落伤害档（150 cm 起，每 300 cm 1d6 并倒地） | 105 | `fall.step_cm` / `fall.dice` / `fall.prone` |
| 高地优势 +1（指针） | 84 | `high_ground.bonus_layers`（`docs/system/02a-tactics-engine.md:93`） |
| hex-line 并列选格 | 123 | `line_of_sight.tie_break` |
| 掩体四档（指针） | 138 | `cover_grade`（`docs/system/02a-tactics-engine.md:257`） |
| 地形朝向换算公式 | 225 | `scale.orientation`（pointy / flat 中心坐标换算） |
| 视线高度判定机制 | 251 | `sight`（体型五档派生 / 缺省失败 / 有理数插值判定） |
| 战场模板字段表 | 263 | 战场模板字段（后续落 `data/atlas/tactical_templates/**`） |
| 对象定义目录 | 290 | 对象定义字段（后续落 `data/system/tactical_objects.json`） |

---

## 九、地形朝向

第二节 `scale` 除 `hex_size_cm`（相邻格中心距离）外，增加**朝向**字段 `tactical.battlefield.scale.orientation`，受控枚举 `pointy`（尖顶）/ `flat`（平顶）。缺省值为 `pointy`（**缺省值为建议值，待确认**）。

**中心坐标换算**：以 `S = hex_size_cm`（相邻格中心距离）为基准，axial 坐标 `(q, r)` 的格中心在战场平面内为：

- `pointy`（尖顶）：`x = S × (q + r/2)`，`y = S × (√3 / 2) × r`
- `flat`（平顶）：`x = S × (√3 / 2) × q`，`y = S × (r + q/2)`

**验证锚点**：两种朝向下，相邻格中心距均等于 `S`。

- `pointy`：`(0,0)` 中心为 `(0, 0)`；`(1,0)` 中心为 `(S, 0)`，与原点距离 `S`；`(0,1)` 中心为 `(S/2, S × √3 / 2)`，与原点距离 `√(S²/4 + 3S²/4) = S`。
- `flat`：`(0,0)` 中心为 `(0, 0)`；`(1,0)` 中心为 `(S × √3 / 2, 0)`，与原点距离 `S`；`(0,1)` 中心为 `(0, S)`，与原点距离 `S`。

**作用范围**：朝向**只**影响 metric 模式的水平距离换算与 UI 渲染；**不影响**六邻偏移（行号 18）、六角距离公式（行号 19）与 `hex_line` 并列选格（行号 123）——三者的规则不因朝向改变，实现不得为不同朝向复制第二套几何内核。

**覆盖层级**：缺省值放 `system.tactical_space.scale.orientation`；世界规则、战场模板可覆盖。字段的 schema / registry / FORMAT 落地由后续 data 单完成，本节只写契约。

---

## 十、视线高度

第五节「视线」中「从攻击者眼高射向目标可见部位高度」的两个高度，**必须**来自 `system.tactical_space.sight` 配置（metric 口径）。本节是契约：只定结构与机制，**不写任何具体数值**——数值进数据。

**体型五档派生**：`sight` 按体型五档各派生两个字段，档位与 `data/system/combat.json` 的 `poise.body_sizes` 一一对应，不得增删档位：

| 体型档 | 派生字段 |
|---|---|
| `small` / `medium` / `large` / `huge` / `boss` | `eye_height_cm`（攻击者眼高）、`visible_height_cm`（目标可见部位高度） |

**缺省行为**：没有 `sight` 配置时，metric 视线查询**必须失败**并在原因中说明缺少 `sight` 配置；**不得**静默取 0 或任何隐式默认值。

**判定机制**：射线在某格处的高度按**「两端高度 × 水平距离」的有理数线性插值**得出（与第五节 hex-line 采样同用有理数口径，不得用浮点），再与该格 `obstacle_height`（与 `elevation` 同口径）比较：射线高度**高于**障碍顶 = 可越过；**低于** = 被挡。遮挡判定只做这一次高度比较，不引入第二套阻挡语义。

**abstract 模式**：不适用眼高——abstract 的 LOS 只检查 `blocks_los`，不做高度插值，也不读取 `sight` 配置。

---

## 十一、战场模板

战场模板是**布局意图**的可复用声明，供生成器与手工地图共用；模板不是规则数据，不承载任何阈值。

**模板字段表**：

| 字段 | 含义 |
|---|---|
| `template_id` | 模板身份 |
| `template_version` | 模板版本；与 `template_id`、`seed` 共同决定复现（见下） |
| `shape` | 受控枚举：`room` / `outdoor` / `custom` |
| `size` / `radius` | 战场范围描述，按 `shape` 选用 |
| `surfaces[]` | 表面集合；格的 `surface_id` 指向此处 |
| `entry_cells[]` | 入口格集合 |
| `spawn_points[]` | 出生点集合；受第三节不变式约束（不得不可达 / 重叠 / 必然无路） |
| `features[]` | 布局特征；落地时映射为 cell 的 `terrain_id` / `terrain_tags` / `cover_grade` / `hazard_refs` / `feature_refs`，以及 `objects[]` **实例**（定义见第十二节） |
| `encounter_meta` | 遭遇元数据，供遭遇 / 叙事层读取，不参与几何判定 |

**约束**：

- 同 `template_id` + `template_version` + `seed` 必须复现同一战场（与第二节「种子重现」同口径）。
- 模板只描述**布局意图**，**不得内嵌规则数值**；阈值、成本、档位一律引用 `system.tactical_space` 及相应 system 数据，不得在模板中复制第二份。

**数据化路径**：本单合并后另开 data 单落 `data/atlas/tactical_templates/**`（kind 与 schema 由该单定义）；本节为其提供行号锚点。

---

## 十二、对象定义目录

**定义与实例分离**：本节规定的是**对象定义**（definition）——与具体战场无关的通用机制；战场上出现的是**实例**（见第三节 object instance，持 `definition_id` 与战场上下文）。定义描述「这类对象如何运作」，实例描述「这一个对象现在的状态」。

**definition 结构**：每个定义以 `definition_id` 标识，并声明 `category`。`category` 受控枚举九类：

`blocker` / `cover` / `hazard` / `mechanism` / `boost` / `mobility` / `pickup` / `movable` / `destructible`

**每个定义必须声明**：

| 声明项 | 含义 |
|---|---|
| 允许状态集 | 对象可处于的状态集合；显式状态机，不靠描述文本 |
| 允许转换 | 状态间合法迁移；典型转换链含 `intact→damaged→destroyed`、`closed→open→locked`、`inactive→armed→triggered→disabled` |
| `spatial_effects` | 破坏 / 状态变化对 `blocks_los`、`blocks_movement`、`cover_grade` 的影响 |
| `durability` | 耐久口径（数值进数据，本节不写数值） |
| 交互成本与检定 / 效果引用 | 交互的代价，以及可触发的检定 / 效果对既有规则数据的引用 |

**铁律**：**不得按世界名或物件名写分支**。定义是通用机制，对所有世界一致；世界的风味、命名与上下文由实例与 ATLAS 层携带，不进入定义。

**数据化路径**：后续 data 单落 `data/system/tactical_objects.json`（kind `system.tactical_objects`）+ schema / registry / FORMAT；本节为其提供行号锚点。
