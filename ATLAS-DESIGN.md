# 位置与自动地图 · 设计方案

> 状态：待主管审阅。本文只定设计与拆单，不含实现。
> 读者：主管 Agent。审过之后按第 8 节拆成 Issue，再指派实现 Agent。
> 依据：2026-10-09 已拍板的讨论。默认分支是 `master`。

本文件不是规则正文，不替代 `docs/`。它描述的是一套从**世界模组 JSON** 推出三维地图的程序，供以后增删世界时不必再写一份地图。

---

## 0. 主管怎么用这份方案

1. 审第 3 节的决定和第 7 节的风险。有不同意的，先改本文再拆单。
2. 第 8 节的六条 Issue 已按仓库模板写好（目标 / 验收 / 可改 / 禁改 / 依赖）。可以照抄开单，也可以合并或推迟，但不要把词表登记和内核揉进同一条。
3. 开单时打上每条写明的难度标签与能力标签。
4. 实现 Agent 不合并。审查用四行建议。
5. 现在开放的 Issue 是 **#59**（`prism_core.py`）、**#60**（`docs/` 的 S1、S5）、**#61**（剧本数据）。本方案的前三条不碰这些路径。

未推送到远端的本文件，按 `AGENTS.md` 不能当作交接。主管在另一会话接手之前，需要先有人把本文件送进 GitHub。

---

## 1. 目标

玩家在任一世界里有一处可保存的位置 `(帧, x, y, z)`。可以查看出口、可选地查看当前层的字符切片、按该世界自己的尺度花费时段移动。走进地点时生成楼层或路网，开战时能把近处投影成区域。换一个世界模组，或给现有模组增减区域，不改地图代码，也不另交一份坐标表。

做完后，使用者能感知的变化要到第 8 节的 I4 才出现在网页会话里。I1–I3 是库和数据，用测试证明。

### 不做

- 不按世界名写分支，不提交 `data/atlas/<世界>.json` 这种手摆坐标。
- 不读 `docs/` 长文，不调用模型。
- 不改三个世界的正文来迁就编译器。事实若只写在长文里、没进 JSON 短字段，第一版就编不出来。
- 不加图片、瓦片、画布小地图。
- 不把三个世界放进同一套米或同一张总图。
- 不做无限体素，不用米逐步模拟跨大陆旅行。
- 不做分队多位置的界面。数据上允许以后每个单位自带 locus，第一版全队一个位置。
- 不改 `docs/` 规则。行程仍是现有四档：短程、中程、远程、危险穿越。
- 不引入运行时依赖。只使用 Python 3 标准库。
- 不在本方案的实现里改 `prism_core.py`（#59 占用）或剧本数据（#61 占用）。接线另开，且排在它们合并之后。

---

## 2. 已拍板

| 决定 | 理由 |
|---|---|
| 三层都做：世界帧、地点帧、战术帧 | 讨论时选定。三层共用整数坐标，格的含义不同。 |
| 默认看出口列表；字符切片可选 | 产品是纯文本。切片只画当前 z、且只画已发现的格。 |
| 世界之间不共用尺度 | 尺度从该世界的短文本推出来，不手写三份配置。 |
| 格子是区域或房间，不是米格 | 对齐 `docs/system/02A`：战术图用区域，不用方格。 |
| 心象剧场保留 | 近 / 中 / 远、高地从本帧的邻接投影，不另造距离规则。 |
| 帧与帧之间不换算米 | 夹隙、根系、熵网若被认成另一套空间，只用连接相通。 |

代码只共享查询内核。一格有多大、图有多大、z 有哪些层、跨格算哪一档行程，都是编译结果。

---

## 3. 架构

三个模块，文件分开，便于按 Issue 并行或按依赖合并：

| 模块 | 文件 | 职责 |
|---|---|---|
| 内核 | `atlas.py` | 帧、地点、连接、移动、出口、切片、距离投影、增量、存档块。不知道世界名字。 |
| 编译器 | `atlas_compile.py` | 读一份 `world.module` 和词表，推出世界帧。 |
| 生成器 | `atlas_gen.py` | 按性状生成地点帧和战术帧。 |

规则核心与网页层只在后续 Issue 里调用它们。AI 导引者（M5，尚未开始）以后只读「地点卡」，不读整张图，也不在本方案里实现。

```
world.module JSON ──┐
atlas.lexicon JSON ─┼─► compile_world ─► 世界帧（区域、连接、尺度）
                    │                      │
                    │                      ▼
                    └─► generate_site ─► 地点帧（房间 / 路网 / 楼层）
                                           │
                                           ▼
                                    generate_tactical ─► 战术帧（区域）
                                           │
                                           ▼
                                    出口列表 / 字符切片 / 地点卡 / 行程档
```

### 3.1 地点

```
place: {
  id,                  # 见 3.2，不由坐标生成
  name,
  kind,                # region | fill | site | room | zone | feature
  frame_id,
  x, y, z,             # 整数；可以没有，只靠连接存在
  footprint,           # {w, h}，区域锚点可占多格；缺省为 1×1
  links: [{
    to,                # place id
    via,               # 北 | 南 | 东 | 西 | 上 | 下 | 门 | 连接
    band,              # short | medium | long | dangerous | null
    oneway,            # bool
    unstable           # bool
  }],
  traits,              # 字符串列表，见第 4 节
  discovered,          # unseen | seen | entered
  source               # authored | generated
}
```

邻接是平面四向加上、下。对角不穿墙。门和楼梯是一条 `link`，不是另一套坐标。

`space` 写在帧上，不写在每一格上：`metric` 或 `abstract`。`metric` 的战术格可以用米换算跨区；`abstract` 的一格就是一次走位。

### 3.2 标识

- 区域：`{world.key}/{region.id}`
- 关键地点：`{world.key}/{region.id}/k{序号}`，序号是该区域 `key_places` 数组下标
- 生成格：`{父 id}/g{x}_{y}_{z}`
- 帧：`{world.key}/surface`、`{world.key}/seam`、`{world.key}/abstract`、`{world.key}/site/{地点 id}`、`{world.key}/tactical/{地点 id}`

区域改名、挪坐标，id 仍在。区域从 JSON 删除后，重编译时该 id 消失。

### 3.3 内核对外函数

实现 I1 时按这个签名，后面的 Issue 只调用、不改签名。不够用时退回 I1，不在后续 Issue 里改 `atlas.py`。

- `new_atlas(world_key, seed) -> dict`
- `add_frame(atlas, frame_id, *, space, z_meaning, cell) -> dict`
- `add_place(atlas, place) -> dict`
- `add_link(atlas, frame_id, a, b, via, *, band=None, oneway=False, unstable=False)`
- `move(atlas, locus, via) -> {locus, place, error}`
- `exits(atlas, locus) -> [{via, name, place_id, band, unstable}]`
- `slice_text(atlas, locus, radius=8) -> str`
- `range_band(atlas, frame_id, a, b) -> same | adjacent | near | mid | far`
- `high_ground(atlas, frame_id, a, b) -> bool`
- `guide_card(atlas, locus) -> dict`
- `apply_delta(atlas, delta) -> None`
- `export_state(atlas, locus) -> dict`
- `restore_state(atlas, state) -> {atlas, locus, notes}`
- `lost_destination(atlas, locus, intended_id, rng) -> place_id`
- `reroll_unstable(atlas, frame_id, rng) -> int`
- `carry_across(atlas, link, feature) -> feature_id`

`locus` 是 `{frame_id, place_id, x, y, z}`。全队共用一个。

距离投影，只用本帧的图距离：0 同区，1 相邻，2 近，3–4 中，更远为远。`high_ground`：对方 z 更低，平面切比雪夫距离 ≤ 1，且存在上/下或相邻连接。

`lost_destination`：在已连通的地点里，选一个不在通往 `intended_id` 的最短路上的地点。调用方再扣 1 时段、风险池 +1。内核不改 `prism_core`。

`reroll_unstable`：重摇 `unstable` 且两端都不是 `authored` 的边。锚点的边保持不动。返回重摇条数。

`carry_across`：把要素复制到连接另一端所在的帧，不换算坐标。只有连接带有性状 `carry` 时才允许。

### 3.4 存档块

不放进 `RuleSession.scene`。与 `scene` 平级，留给 I4 塞进会话快照：

```
atlas: {
  version: 1,
  world_key,
  seed,
  setting_rev,             # 见 6.4
  party_locus,
  frames: { frame_id: { seed, deltas: [] } }
}
```

增量：

```
{op: "set_link" | "set_trait" | "discover" | "add_feature" | "remove",
 place_id, payload}
```

世界帧可以整份留在存档里。地点帧和战术帧能按种子重算的，只存种子和增量。

加载时用当前世界 JSON 重编译，再重放 id 仍然存在的增量。队伍站在已删除的地点上时，改放到仍存在的、坐标最接近的 `authored` 区域；没有旧坐标就放到第一个区域。`notes` 里写明搬迁，I4 把这句话写入会话日志。

---

## 4. 词表与扫描

### 4.1 为何用词表

世界作者维护的是 `data/worlds/*.json`，不是地图。编译器不能出现 `if world_key == "threshold"`。新世界只要短文本里使用词表中的说法，就会得到不同的尺度和构成。词表没覆盖的新比喻，加一行词，不加一份地图。

词表是通用空间词，不是某个世界的坐标，也不是从规则书摘来的引文。条目**不要**使用键名 `source`。校验器会把任何叫 `source` 的字符串当成 `docs/...md:行号`（见 `tests/validate_data.py`）。

### 4.2 文件与 kind

- 路径：`data/atlas/lexicon.json`
- `kind`：`atlas.lexicon`
- 在 `data/schema/registry.json` 登记，并新增 `data/schema/atlas.lexicon.schema.json`
- 在 `data/FORMAT.md` 加一小节，说明这个 kind 没有 `source`，以及扫描范围

封套：

```
{
  schema_version: 1,
  kind: "atlas.lexicon",
  entries: [
    {id, trait, terms: [非空字符串]}
  ]
}
```

`trait` 只用第 4.4 节的枚举。`terms` 在同一 trait 内不得重复。匹配时按词长从长到短，先命中先生效。实现者不得另加词表以外的单字规则。

### 4.3 扫描范围

只读这些短字段：

| 来源 | 用于 |
|---|---|
| `regions[]` 的 `name`、`summary`、`atmosphere` | 方位、围绕、性状、数字 |
| `regions[]` 的 `key_places[]`、`events[]` | 性状、数字、关键地点锚。不从这里取方位 |
| `glossary[]` 的 `term`、`meaning` | 世界级性状（另开一帧、可携带），不安到某一个区域上 |
| `world_rules` 里的字符串值 | 同上。跳过键名以 `_source` 结尾的值，跳过符合 `docs/...md:行号` 的值 |

不读：`history`、`factions`、`tracks`、`careers`、`abilities`、`equipment`、`enemies`、`random_tables`、`powers`。这些字段里的「6 米半径」「一层恐惧」「北方的巨人们」会污染尺度和方位。

`random_tables` 只在生成场地要素时另读，见第 5.4 节。

### 4.4 初版性状与词

这是第一版词表内容，写在 `lexicon.json` 里，不写进 Python。审阅时可以删词，不能改成按世界分列。

| trait | 作用 | 词 |
|---|---|---|
| `settlement` | 格子偏紧 | 城区、街区、港口、市场、住宅、都市、工业区、老港 |
| `wilderness` | 格子偏大 | 荒野、山脉、极地、平原、林城、地下林、废墟 |
| `span_country` | 跨度档 3 | 小国 |
| `vertical` | 地点往 z 叠，世界脚印不跟着长高 | 环道、崖壁、楼层 |
| `below` | 偏向负 z | 地下、负一层、负二层、负三层 |
| `network` | 节点图，不切房间 | 地铁、根系、桥、通道 |
| `water` | 格可标成水域，靠连接通行 | 群岛、水上 |
| `barrier` | 连接可升到危险档 | 山脉、封锁、雷区 |
| `unlisted` | 可以没有 x、y | 地图上没有、导航到不了 |
| `abstract` | 进入抽象帧 | 不存在的物理、无法解析、数据区域、网潜 |
| `unstable` | 非锚点的边可重摇 | 每次测量不同、渗溢、薄处、没有几何 |
| `carry` | 连接允许 `carry_across` | 带出来、成为现实 |
| `shortcut` | 这组连接比地面绕行短 | 而不是数天、穿行约需 |

方位不进 trait，由编译器按词长匹配：西北、东北、西南、东南、北方、南方、东方、西方、中央、中心，然后才是单字北、南、东、西。

围绕关系用模式，不用词表整句：在 `summary` 里找「围绕」后面、标点之前的至多 12 个字；这段文字必须包含另一个区域的主名。主名是 `name` 里全角竖线 `｜` 之前的部分。对不上区域名就忽略。因此「围绕未完成」不会变成约束，「围绕巨干」「围绕中枢城」会。

### 4.5 数字

只认紧挨着单位的数，单位是：公里、米、层。先认「公里」，再认「米」，避免「公里」里的「米」被拆开。

- 阿拉伯数字，允许千分位逗号。
- 中文数字，覆盖零到千，以及「两」。第一版不要求「万」。
- `负N层`、`地下N层` 记为向下的层数。
- `第 N 层` 是地标层，不是总层数。地点帧至少包含这一层。
- `N 层`、`N层` 才是总层数。
- `半径 N 公里`、`直径 N 公里` 记入该区域的公里数。直径按半径的两倍参与脚印时，先换成半径再算，避免同一句算两次。

层数和米是地点帧的高度。公里是世界帧的脚印。读不到数字就用第 5 节的默认档，不臆造一个精确米数。

---

## 5. 编译与生成

### 5.1 两个长度常数

全编译器只有这两个常数，写在 `atlas_compile.py` 一处。它们不是某个世界的配置。

| 常数 | 值 | 含义 |
|---|---|---|
| `SETTLEMENT_CELL_KM` | 2 | 聚落尺度下，一格约两公里，只用于把文中的公里数换成脚印 |
| `MARCH_CELL_KM` | 30 | 旷野尺度下，一格约一日路程 |

改手感只改这两处，不给世界加字段。

### 5.2 世界尺度

每个区域一个跨度档：

- 命中 `span_country`，或半径 ≥ 20 公里：3
- 命中 `wilderness`，或半径 ≥ 5 公里：2
- 否则命中 `settlement`：0
- 都没有：1

世界尺度：若档 ≥ 2 的区域不少于档为 0 的区域，则为 `march`，否则为 `settlement`。

聚落图：跨一个区域边界是短程（1 时段）。同区域内部的走动不是行程，不扣时段。

旷野图：相邻一格中程（2 时段）；2–3 格远程（4 时段，对应规则里的「全天」）；更远或带 `barrier` 的边为危险穿越（4 时段，且调用方按每时段一次风险判定）。危险档不替代 `data/system/travel.json`，内核只返回档位 id。

`shortcut` 若出现在 `world_rules` 的字符串里：在 z = −1 另建一组连接，档位为短程。这组连接是跨越区域的路网，不替换地面连接。地面该多远还是多远。

### 5.3 脚印、层、帧

区域脚印边长：

- 聚落：`max(1, round(半径公里 / SETTLEMENT_CELL_KM))`，限制在 1–8。没有公里数时为 1；`key_places` 不少于 3 条时至少为 2。
- 旷野：`max(1, round(半径公里 / MARCH_CELL_KM))`，限制在 1–8。没有公里数时，跨度档 3 为 3，档 2 为 2，其余为 1。

高度不进入世界脚印。地点层数：

- 用文中的总层数，限制在 1–80。
- 只有「高约 N 米」时，按每层 10 米换算，同样限制在 1–80。八百米因此是地点帧里的多层，不是世界帧里的数百层。
- 没有数字但有 `vertical`：默认 3 层。
- `below` 且层数为负：向负 z 生成同样规则的层数。

帧：

- 始终有 `surface`。聚落帧 `space = metric`，`cell` 记为街区；旷野帧 `space = metric`，`cell` 记为日路程。
- 世界级文本命中 `abstract`，或任一区域自己的文本命中 `abstract`：另开 `abstract` 帧，`space = abstract`，不和街面换算格宽。只有自身文本命中的区域放进这一帧。
- 世界级或区域文本命中 `unstable`：另开 `seam` 帧。命中的区域用一条 `unstable` 连接进去。夹隙里非锚点的边可重摇。
- 区域命中 `unlisted`：不分配街面坐标，只保留连接。

区域可以同时出现在街面和另一帧。两帧之间只有连接，没有坐标变换。世界级文本命中 `carry` 时，抽象帧与街面之间的连接带 `carry`。

### 5.4 摆放

1. 被「围绕」指到的区域先放。没有方位的，放在靠近原点的空位。
2. 有方位的区域放进对应象限，并与其约束目标保持相邻（围绕）或至少不重叠。
3. 其余区域按脚印面积从大到小，螺旋填入剩余空位。
4. 图的边界随脚印生长，不使用固定的 9×9 或 24×24。
5. 脚印相接的区域建立地面连接。若整图不连通，在最近的两块之间补 `kind = fill` 的格，或补一条连接。`fill` 的名字用「途经」，性状从两端复制危险与地貌，不新造专名。

同一种子、同一份 JSON、同一份词表，摆放结果必须相同。种子改变可以改变无约束区域的相对次序，不能改变有方位、有围绕关系的区域落在哪一侧。

### 5.5 地点生成

`generate_site` 在第一次进入该关键地点或区域时调用。参数只来自性状和数字：

| 条件 | 生成 |
|---|---|
| `network` | 节点图。节点数取 `min(12, 3 + 关键地点数)`。x、y 只为了能画切片，移动花费看连接。 |
| `wilderness` 且没有 `vertical` | 开阔地：1 层，房间少，走廊短。 |
| 其他 | 每层撒房间，曼哈顿距离上求一棵生成树并凿走廊，再以约 15% 的边加环路。层与层之间只在平面重叠处放楼梯。 |
| `unstable` | 非锚点的边标 `unstable`。 |
| 帧为 `abstract` | 该地点帧 `space = abstract`。 |

关键地点是锚：名字用原文。生成器只填锚之间的空格，不改锚的名字。

场地要素类型只用规则里已有的六种：掩体、危险、机关、增幅、情绪、机动。优先从**当前世界**的 `random_tables` 里取行；该世界没有这类行时，才用 `data/random_tables/system.json` 的场地类表。禁止读取另一个世界的表。每个房间 0–2 条。

### 5.6 战术帧

`generate_tactical` 取当前房间以及图距离 ≤ 2 的房间，每间成为一个区域，保留它的一个场地要素作为固有特征。

`metric`：一区按 6 米计，只用于把规则里的移动米数换成跨区数，`ceil(米 / 6)`。`abstract`：不使用这个换算，一次走位跨一区。

默认战斗可以不展示这张图，只使用 `range_band` 和 `high_ground`。

---

## 6. 三个现有世界：验收性质

测试不断言某个区域的 x、y。坐标可以随种子和正文改变。下列性质必须能从**当前 JSON 短字段**推出来。只写在 `docs/` 长文、没有进扫描范围的句子，不作为失败条件。

### 6.1 阈界都市 `data/worlds/threshold.json`

- 尺度为 `settlement`。跨区档位短于余烬纪元的邻格。
- `key_places` 含「52 层」的地点，地点帧 z 跨度至少覆盖 52，且不超过 80。世界帧不用这 52 层当高度。
- 含「负三层」或「地下三层」的地点向负 z 延伸。
- 术语表有「薄处」「渗溢」「夹层」。区域短文里写到薄处或渗溢的，有进入 `seam` 的连接，且夹隙中非锚点的边可以重摇。
- 关键地点「长度每次测量不同」所在的地点，边可以标 `unstable`。

### 6.2 余烬纪元 `data/worlds/emberfall.json`

- 尺度为 `march`。邻格不是短程。
- `summary` 含「围绕巨干」的区域，落在主名「巨干」的区域外围，不与它重叠。
- `summary` 含「大陆西北」「南方群岛」「北方的极地」的区域，分别落在西北、南、北。
- 「直径两公里、高约八百米」：世界脚印按两公里在旷野常数下接近 1 格；八百米进地点帧，不进世界帧的 z 层数。
- 「面积相当于一个小国」的区域，脚印边长大于没有跨度词的区域。
- `world_rules` 写明根系通道「穿行约需 1 个时段（而不是数天的路程）」：z = −1 的路网连接为短程，短于地面邻格。
- `summary` 含「地下林」的区域出现在负 z，或接在这条路网上。

### 6.3 熵网 `data/worlds/entropic-net.json`

- 同时存在 `metric` 帧和 `abstract` 帧，二者不做公里换算。
- 「半径 12 公里」的区域，脚印大于同图里没有公里数的城区。
- `atmosphere` 为「地图上没有，导航到不了，但确实存在」的区域，没有街面 x、y。
- `atmosphere` 含「不存在的物理空间」的区域在抽象帧。
- 术语「回响体」的释义含「从网里带出来」：抽象帧与街面之间的连接允许 `carry_across`。
- 抽象帧上 `range_band` 仍可用，但战术换算不使用 6 米。

### 6.4 增删世界

`setting_rev` 是编译器所读字段的规范化 JSON（键排序、UTF-8）的 SHA-256 前 16 位十六进制。测试夹具里放一份虚构的 `world.module`，不入库到 `data/worlds/`：

- 编译成功，代码路径里不出现这个虚构世界的 key 比较。
- 删掉其中一个区域再编译：旧 id 消失；该 id 的增量被 `restore_state` 丢弃；新区域若加进去则出现。
- `git diff` 意义上，实现 PR 的 Python 源码不得包含 `threshold`、`emberfall`、`entropic-net` 这三个字符串。测试文件可以包含它们，用来读真实 JSON。

---

## 7. 风险（主管审查时不能写「无」的那些）

1. **两个长度常数是设计选择。** 2 公里和 30 公里决定脚印手感。它们对所有世界用同一公式，不是三份预设。若玩起来不对，改常数，不给单个世界开特例。
2. **长文里的空间事实第一版会丢。** 例如正文写阈界「没有几何」，JSON 术语表写的是「夹层」和「渗溢」。编译器只见 JSON。不够时先补词表；仍不够再单独开内容 Issue 改 `docs/` 与世界 JSON，不塞进实现 PR。
3. **短文会误伤。** 「围绕未完成」已规定对不上区域名就忽略。若某区域的 summary 本身有歧义，地图跟着正文走，视为设定的一部分。
4. **本文件是否入库。** 它不在 `AGENTS.md` 第 8 节的目录表里。可以随一条文档 PR 入库并补上目录表，也可以只作拆单附件、不入库。这是主管该拍的板，不要由实现 Agent 默默加进 `AGENTS.md`。
5. **无新依赖、无密钥、无环境变量。** 存档块只增加字段，不改现有 `scene` 的形状。

---

## 8. 建议 Issue

六条分开。I1 与 I2 文件不重叠，可以同时派工；**合并顺序必须是 I1 先于 I2**。I2 独占 `registry.json` 与 `data/FORMAT.md`，期间不要再开别的数据切片改这两个文件。

实现约束，每条都适用：从最新 `master` 开分支；尽早开 Draft PR；不合并；验证命令真跑，把命令和结果写进 PR；内容防火墙与 `bash tests/run_all.sh` 通过。不要把新文件写进 `tests/test_repo_layout.py` 的必需清单。

### I1 · 地图内核

- 标签：`难度：中`，`能力：编程`，`能力：测试`
- 依赖的 PR：无

**目标**

程序里可以建立带 x、y、z 的帧和地点，查询出口，投影近中远与高地，打印当前层切片，并用增量保存后再读回。

**验收标准**

- [ ] `atlas.py` 提供第 3.3 节的函数。不读取 `data/worlds/`。
- [ ] 测试用手工造的两帧（一帧 `metric`，一帧 `abstract`）覆盖：四向与上下、对角不穿墙、缺坐标的地点只靠连接存在、`range_band`、`high_ground`、切片不含未发现格和其他 z、`lost_destination` 不在最短路上、`reroll_unstable` 不动锚点边、`carry_across` 不换算坐标、增量在地点删除后被丢弃。
- [ ] `python3 tests/test_atlas_kernel.py` 退出码 0。
- [ ] `bash tests/run_all.sh` 与 `bash tests/content_firewall.sh` 退出码 0。
- [ ] 无新依赖。

**可改路径**

- `atlas.py`
- `tests/test_atlas_kernel.py`

**禁改路径**

- `prism_core.py`、`notdnd_web.py`、`data/**`、`docs/**`、`AGENTS.md`、`MASTER.md`

### I2 · 词表与世界编译

- 标签：`难度：高`，`能力：编程`，`能力：逻辑`，`能力：数据`
- 依赖的 PR：I1 已合并

**目标**

一份世界模组 JSON 能编译成世界帧。增删区域只改这份 JSON（或词表），不改编译代码。三个现有世界表现出第 6 节的性质。

**验收标准**

- [ ] `data/atlas/lexicon.json` 的 kind 为 `atlas.lexicon`，内容来自第 4.4 节，无 `source` 键。
- [ ] `registry.json` 已登记；`FORMAT.md` 写明扫描范围与「本 kind 无 source」。
- [ ] `python3 tests/validate_data.py` 通过，文件数比合并前多 1（词表）。
- [ ] 编译器扫描范围符合第 4.3 节。Python 源码不含三个世界的 key。
- [ ] 第 6 节三条世界性质和第 6.4 节虚构世界的增删，在 `python3 tests/test_atlas_compile.py` 里为真实断言。
- [ ] 同一输入连编两次，区域 id 与有约束的相对方位相同。
- [ ] `bash tests/run_all.sh` 与内容防火墙通过。

**可改路径**

- `atlas_compile.py`
- `data/atlas/lexicon.json`
- `data/schema/atlas.lexicon.schema.json`
- `data/schema/registry.json`
- `data/FORMAT.md`
- `tests/test_atlas_compile.py`
- `tests/fixtures/` 下仅本测试使用的虚构世界 JSON

**禁改路径**

- `atlas.py`（只调用。签名不够就退回 I1）
- `data/worlds/**`、`docs/**`、`prism_core.py`、`notdnd_web.py`

### I3 · 地点与战术生成

- 标签：`难度：高`，`能力：编程`，`能力：逻辑`
- 依赖的 PR：I2 已合并

**目标**

进入一个地点会按性状生成楼层、路网或开阔地；战术帧能从近处房间投影出来。生成结果不入库。

**验收标准**

- [ ] 固定种子下，同一地点连生成两次，房间 id 与连接相同。
- [ ] 层数、负层、节点图、不稳定边、抽象帧不使用 6 米，各有一条测试，输入用性状而不是世界名。
- [ ] 用阈界的「52 层」锚生成时，地点帧达到该层数且世界帧 z 跨度远小于它。这条测试可以读真实世界 JSON，但生成器源码仍不得分支世界名。
- [ ] 场地要素只来自当前世界的表，或 `data/random_tables/system.json`。测试构造两个世界，证明不会读到另一个世界的表行。
- [ ] `python3 tests/test_atlas_gen.py`、`bash tests/run_all.sh`、内容防火墙通过。

**可改路径**

- `atlas_gen.py`
- `tests/test_atlas_gen.py`

**禁改路径**

- `atlas.py`、`atlas_compile.py`、`data/schema/registry.json`、`data/FORMAT.md`、`data/worlds/**`、`docs/**`、`prism_core.py`、`notdnd_web.py`

### I4 · 会话里的位置

- 标签：`难度：中`，`能力：编程`
- 依赖的 PR：I3 已合并
- 与 #59 文件不重叠，可以并行。不改 `scene` 的现有字段。

**目标**

网页会话能记住队伍在哪，并回答「这里有哪些出口」。重新加载后位置还在；世界 JSON 若已删掉该地点，队伍落到附近仍存在的锚点，日志里有一句说明。

**验收标准**

- [ ] 会话快照增加第 3.4 节的 `atlas` 块。旧存档没有这块时，按当前世界懒编译，不报错。
- [ ] 至少有一个本机可调用的查看出口的路径（现有 HTTP 风格即可）。返回文本是出口列表，不是图片。
- [ ] 移动返回行程档。短程 / 中程的时段数与第 5.2 节一致。内核仍不直接改风险池；若本 Issue 调用了现有风险池，只在跨区移动时加标记，并在 PR 里写明调用了哪个函数。
- [ ] `python3 tests/test_notdnd_web.py` 覆盖：新快照、旧快照迁移、地点被删后的搬迁。
- [ ] 不把 AI 密钥写入仓库或响应。

**可改路径**

- `notdnd_web.py`
- `tests/test_notdnd_web.py`

**禁改路径**

- `prism_core.py`、`data/scenarios/**`、`data/worlds/**`、`docs/**`、`atlas.py`、`atlas_compile.py`、`atlas_gen.py`

若查看出口必须改静态页，先在 PR 里说明并请主管追加 `static/**`，不要默默改。

### I5 · 战术投影接到规则核心

- 标签：`难度：中`，`能力：编程`，`能力：逻辑`
- 依赖的 PR：I3 已合并，且 **#59 已合并**（同一文件 `prism_core.py`，禁止并行）

**目标**

战斗仍可按心象剧场进行。规则核心在需要距离或高地时，向地图询问投影，而不是再维护一套口头坐标。

**验收标准**

- [ ] 只调用 `range_band` 与 `high_ground`（以及已有的战术帧）。没有地图时，行为与 #59 合并后的默认一致。
- [ ] 测试：两格距离得到第 3.3 节的档；z 更高且相邻为高地；`abstract` 帧不把米换成跨区。
- [ ] 不改战斗公式本身。公式仍以 `docs/system/02A` 与 `data/system/` 为准。
- [ ] `python3 tests/test_prism_core.py` 与 `bash tests/run_all.sh` 通过。

**可改路径**

- `prism_core.py`
- `tests/test_prism_core.py`

**禁改路径**

- `notdnd_web.py`、`data/**`、`docs/**`、`atlas.py`、`atlas_compile.py`、`atlas_gen.py`

### I6 · 账本地点改用 place id

- 标签：`难度：中`，`能力：数据`，`能力：编程`
- 依赖的 PR：I2 已合并，且 **#61 已合并**（剧本数据同一批文件，禁止并行）
- 可以晚于 I4、I5。第一版地图不依赖它。

**目标**

账本和 NPC 的位置除显示名外，能对上地图里的 `place_id`。对不上的旧字符串保留显示名，并标成未钉住，不臆造坐标。

**验收标准**

- [ ] 钉住规则写在 PR 里：用区域主名或关键地点全文做包含匹配。匹配不到就保持原字符串。
- [ ] 不改 `docs/`。若必须改 `data/FORMAT.md` 的账本字段，本 Issue 要等 I2 合并之后，并且当时没有别的 PR 改 `FORMAT.md`。
- [ ] `python3 tests/validate_data.py` 通过。
- [ ] 内容防火墙通过。

**可改路径**

- `data/scenarios/**` 中与地点字段相关的 JSON
- `data/FORMAT.md`（仅当字段说明必须变，且不与其他数据 PR 同时）
- `tests/` 中对应该改动的测试

**禁改路径**

- `data/worlds/**`、`docs/**`、`prism_core.py`、`atlas.py`

---

## 9. 合并之后主管要核对的

- 三个世界的测试仍是性质断言，没有人把坐标写死进源码分支。
- `data/worlds/` 的 diff 为空。
- CI 四门通过。
- 若 I4 改变了玩家能调用的 HTTP 行为，四行审查里的「需要人拍板的风险」不能写「无」。
