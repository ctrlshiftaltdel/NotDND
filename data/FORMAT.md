# data/ 数据格式说明（FORMAT）

> 本文件定义 `data/` 数据层的**统一结构、命名与校验口径**。
> 所有由 `docs/`（只读）整理出的 JSON 都必须遵守本文件，并可被
> `tests/validate_data.py` 用同一条命令验证通过。
>
> 本文件只描述**机制与短文本**如何落成 JSON；规则与剧情的**长篇散文**留在
> `docs/`，JSON 只以 `source` 指回（见第 5、6 节）。

## 1. 目录布局与文件命名

```
data/
├── FORMAT.md                 # 本文件：数据格式说明
├── schema/                   # JSON Schema（draft 2020-12）与登记表
│   ├── common.schema.json    # 跨族共用 $defs
│   ├── registry.json         # kind → schema 登记表
│   ├── system.schema.json    # 基线 system.attributes（历史文件名，见 4.1 裁定）
│   ├── system.<name>.schema.json  # system 族各 kind 的平铺 schema（见 4.1）
│   ├── world.schema.json
│   ├── random_tables.schema.json
│   └── scenario.schema.json
├── system/                   # 跨世界规则（源自 docs/system/）
├── worlds/                   # 世界模组（源自 docs/scenario/05、06、07）
├── scenarios/                # 剧本 / 战役（源自 docs/scenario/09、S8、S1–S6）
└── random_tables/            # 随机表（源自 docs/system/08 与 docs/scenario/S7）
```

- 数据文件名一律**小写 + 下划线 + `.json`**（例如 `system/attributes.json`）。
- `data/schema/` 存放 schema 与登记表，**不参与数据发现**；校验器扫描 `data/**/*.json`
  时会跳过整个 `data/schema/` 子树。

## 2. 文件约定（封套 envelope）

每个数据文件是一个 JSON 对象，顶层至少包含：

```json
{
  "schema_version": 1,
  "kind": "system.attributes",
  "...": "族载荷"
}
```

- **`schema_version`**：整数，schema 版本。当前为 `1`。
- **`kind`**：字符串，形如 `<family>.<name>`（族.名称），决定用哪份 schema 校验。
- 其余键为**族载荷**（payload），由对应族 schema 定义。

`data/schema/registry.json` 是 **`kind` → schema 文件**的唯一登记表：

| kind | schema 文件 |
|---|---|
| `system.attributes` | `system.schema.json` |
| `world.module` | `world.schema.json` |
| `random_tables.catalog` | `random_tables.schema.json` |
| `scenario.campaign` | `scenario.schema.json` |

> M1.1a 起 `system` 族按来源文档切片拆为多个 kind（`system.derived`、`system.skills`、
> `system.tags`、`system.conditions`、`system.stances`、`system.action_economy`、
> `system.combat`、`system.damage`、`system.resources`、`system.growth`），
> 每个 kind 一份平铺 schema，字段表见第 **4.1** 节。**权威登记表始终是
> `data/schema/registry.json`**；本表只列基线示例，不再逐条复制。

> 新增一类数据时，先在 `registry.json` 登记新的 `kind`，再在对应族 schema 中补
> 约束。校验器遇到**未登记的 `kind`** 即报错。

## 3. canonical 键与别名

`docs/` 内部字段命名不完全一致。**写入 JSON 时只用 canonical 键**；
**读取时**兼容下列别名（旧数据 / 直读 `docs/` 派生内容时归一）：

| 概念 | canonical（写入） | 别名（读取兼容） |
|---|---|---|
| 玩家角色集合 | `party` | `cast` |
| 未用钩子池 | `hooks` | `pending_hooks` |
| 敌对者集合 | `nemeses` | `nemesis` |
| NPC 对队伍好感 | `affinity` | `affinity_to_party` |
| NPC 对队伍敬畏 | `awe` | `awe_to_party` |
| 剧本册补充字段 | `flags` / `evidence` / `factions` | — |

## 4. 各实体族字段表与枚举清单

> 基线口径：`system` 族已由试点数据落定，schema 用 `additionalProperties: false` 严格约束；
> `world` / `random_tables` / `scenario` 三族的**实体项**当前 `additionalProperties: true`
> 作为基线（容纳各世界差异），M1.1–M1.4 落数据时在各自 PR 内逐项收紧。

### 4.1 system 族

> **裁定（随 M1.1a 落定）**：`kind` 一律用 `<family>.<name>`（族.名称）；
> **每个 kind 一份平铺 schema**——文件名 `data/schema/<kind>.schema.json`
> （即 `system.<name>.schema.json`），并在 `registry.json` 登记一条 `kind → schema`。
> 基线 `system.attributes` 是**唯一例外**：它沿用 `system.schema.json`
> （既有回归测试锁定该文件名），新 kind 一律不再并入该文件。
> 每个新 schema 只使用第 7 节的关键字子集，共用结构 `$ref` 到 `common.schema.json`。

**`system.attributes`**（六维属性 + 等级表 + 修正表）

- `attributes[]`：`{id, name, en, question, source}`
  - `id` 枚举：`MGT`（力道/Might）、`FIN`（灵巧/Finesse）、`VIG`（体魄/Vigor）、
    `INS`（洞察/Insight）、`MND`（心智/Mind）、`PRE`（气场/Presence）。
- `levels[]`：`{value, label, example?, source}`，`value` 为 1–10。
- `modifiers[]`：`{value, modifier, source}`，`value` 为 1–10，`modifier` 为 −2–3。
  属性修正换算：`1→−2｜2–3→−1｜4–5→0｜6–7→+1｜8–9→+2｜10→+3`。

**`system.derived`**（派生值 + 负载标签；`docs/system/01` 第二节）

- `formulas[]`：`{id, name, formula, note?, source}`，
  `id` 枚举 `vitality | guard | move | carry | initiative`。
- `load_labels[]`：字符串数组，取 `轻 | 中 | 重`。
- `load_rules[]`：`{id, label, min_count?, condition, effect, source}`。
- `note?`：字符串。

**`system.skills`**（29 项技能 + 合并别名；`docs/system/01` 第三节）

- `declared_total`：固定 `29`（自述计数，便于机器核对）。
- `proficiency_note`：字符串。
- `categories[]`：`{id, name, source}`，`id` 枚举
  `physical | mobility | perception | knowledge | survival | social`。
- `skills[]`：`{id, name, en, attributes[], category, use, source}`；
  `attributes` 恰好 2 项，取值 `MGT|FIN|VIG|INS|MND|PRE`，判定取两者较高一项。
- `aliases[]`：`{alias, target, source}`，记录合并项（察觉陷阱→察觉、荒野求生→生存）。

**`system.tags`**（伤害标签 11 + 性质标签 33 + 交互原则；`docs/system/01` 第四节）

- `note`：字符串。
- `damage_tags[]`：`{id, name, en, meaning, source}`（11 条）。
- `property_tags[]`：`{id, name, source}`（33 条）。
- `interaction_principles[]`：`{order, name, rule, source}`。

**`system.conditions`**（减益 11 + 增益 5 + 派生状态 2；`docs/system/01` 第五节）

- `note`：字符串。
- `debuffs[]`：`{id, name, en, effect, removal, source}`。
- `buffs[]`：`{id, name, en, effect, source}`。
- `derived[]`：`{id, name, trigger, effect, source}`（`weary` 疲惫、`overloading` 过载中）。
- `stacking_rules[]`：`{order, rule, source}`。

**`system.stances`**（态势 4；`docs/system/01` 第七节）

- `note`、`switch_cost`：字符串。
- `stances[]`：`{id, name, en, attack, guard, move, note, source}`，
  `id` 枚举 `aggressive | defensive | mobile | focused`。

**`system.action_economy`**（回合结构 / 动作价格 / 反应 / 过载 / 时间单位；第六节）

- `note`、`reaction_note`：字符串。
- `round`：`{ap_per_turn, carry_over_to_reaction, steps[], source}`。
- `actions[]`：`{id, name, ap, ap_note?, note, source}`；
  `ap` 为 ≥0 的整数或 `null`（反应动作用 `ap_note` 记「用保留的 AP」）。
- `reactions[]`：`{id, name, en?, cost, effect, source}`。
- `overload`：`{note, levels[], cost, cost_source}`，
  `levels[]` 为 `{level, extra_ap, next_ap, note?, source}`。
- `time_units[]`：`{id, name, en?, duration, use, source}`。

**`system.combat`**（战斗支柱：韧性破韧 / 连携 / 场地要素；第八至十节）

- `poise`：`{name, formula, note, body_sizes[], reduction[], reduction_caveat,
  recovery, recovery_source, break, boss_shield}`；
  `body_sizes[]` 为 `{id, name, bonus, extra?, source}`，体型加值 5 档（0/6/12/20/30）；
  `reduction[]` 为 `{id, method, amount, source}`；`break.effects[]` 为
  `{id, effect, value, source}`；`boss_shield` 为 `{layers, recover, note, phases[]}`。
- `combo`：`{triggers[], limit, limit_source, responses[], chain, pursuit, pursuit_source}`；
  `triggers[]` 6 条 `{order, name, description, source}`；
  `responses[]` 3 条 `{id, name, en, effect, source}`。
- `site_elements`：`{count_rule, count_source, adopt_conditions[], format, table[],
  player_request}`；`table[]` 是通用 d20 表 20 行，`{roll, name, type, df, effect, source}`，
  `type` 与 `df` 口径同 `common.site_element`（`类型` 取 `掩体 | 危险 | 机关 | 增幅 | 情绪 | 机动`，
  `df` 可为 `null`）。

**`system.damage`**（命中与抗性 / 濒危与创伤；第十一节）

- `resolution_flow[]`：`{order, step, source}`。
- `outcomes[]`：`{id, name, condition, effect, source}`（命中/暴击/擦过/严重失手）。
- `relations[]`：`{id, name, en, effect, source}`（抗性/弱点/免疫/吸收）。
- `downed` / `fade` / `trauma`：见 schema；各短文本字段成对带 `*_source`。

**`system.resources`**（四大资源池 + 休息 + 医疗；第十二、十三节）

- `pools`：固定键 `focus | tempo | strain | resolve`，各有 `name/en/role` 与池专属字段
  （`focus.notes[]`、`tempo.spends[]` + `zero_rule`、`strain.levels[]` + `sources`、
  `resolve.spends[]` + `recovery[]`）。
- `rest[]`：`{id, name, en, duration, condition, recovery, source}`。
- `medical[]`：`{id, rule, source}`。

**`system.growth`**（十二级三层制 / 成长选择 6 类 / 通用专长 30；第十四节）

- `declared`：`{tiers, proficiency_tiers, choice_categories, feats}`，自述计数便于机器核对。
- `tiers[]`、`levelup[]`、`per_level[]`、`proficiency_bonus[]`。
- `choices[]`：`{id, label, name, effect, source}`，`label` 枚举 `A–F`（6 类）。
- `choice_restriction` / `choice_restriction_source`。
- `feat_categories[]`：`{id, name, source}`，`id` 枚举
  `combat | mobility | mind | social | resilience`。
- `feats[]`：`{order, id, name, category, effect, source}`（30 项，`order` 1–30）。

> **计数核对**（M1.1a 逐条比对 `docs/system/01`）：技能 29；伤害标签 11；性质标签 33；
> 减益 11 / 增益 5 / 派生状态 2；态势 4；动作价格 **10**；反应 5；过载 3；体型加值 5；
> 场地要素 d20 表 20 行；成长选择 6 类；通用专长 30。
> ⚠️ 动作价格表以 `docs/system/01` 第六节为准为 **10 行**（移动/姿态切换/辅助动作/基础攻击/
> 全额能力/快速能力/压制动作/巡查动作/协助/反应）；若别处记为「11」，以源文档为准。


### 4.2 world 族

**`world.module`**（世界模组；族载荷允许后续 M1.2 收紧）

- `world`：`{key, name, engine}`，`engine` 枚举 `tactics | build`。
- `factions[]`、`careers[]`、`abilities[]`、`equipment[]`、`enemies[]`：
  每项至少 `{id, name, source}`（实体基座）。
- `equipment[]` 的 `kind` 枚举：`weapon | armor | gear`；`load` 枚举 `轻 | 中 | 重`；
  `price_tier` 枚举 `碎银 | 标准 | 贵重`。
- `tracks[]`（世界专属资源，见 4.4 的 `world_rules.tracks`）。
- `random_tables[]`（见 4.3 的 `random_table`）。

### 4.3 random_tables 族

**`random_tables.catalog`**

- `resource`：固定为 `prism.random_tables`。
- `tables[]`：`{id, name, die, purpose?, hook_density?, rows[], use?, on_repeat?, interface?, source}`。
  - `die` 形如 `d20` / `d100`（pattern `^d[0-9]+$`）。
  - `rows[]`：`{roll, result, example?, tag?, tier?, effect?}`，`roll` 从 1 起。
- `generators[]`：`{id, name, steps[], budget_ref?, optional_flavor?}`。
- `ledger_bridge`：对象，把表映射到账本字段。

### 4.4 scenario 族

**`scenario.campaign`**（剧本 / 战役；**世界账本 ledger 模板**亦在此族）

- `meta`：`{world, engine, session?, party_level?, party_size?, tone_pack?}`，
  `engine` 枚举 `tactics | build`；`party_level` 为 1–12。
- `acts[]`、`nodes[]`、`threads[]`、`npcs[]`、`locations[]`、`endings[]`：
  每项至少 `{id, name, source}`。
- `factions`、`flags`、`evidence`：剧本册补充结构。
- `ledger_template`：世界账本骨架（见下），顶层带 `source`。

**世界账本 ledger**（canonical 键，兼容 `docs/system/04-导引者操作系统.md`）：

- 必有：`meta`、`clock`、`party`、`npcs`、`threads`、`facts`、`hooks`。
- 可选：`locations`、`debts`、`nemeses`、`factions`、`flags`，以及各世界模组
  独有的节（如 `depth`、`frontline`、`supply`）——允许 `additionalProperties`，
  以便「不强求三套模组同构」。

**场地要素**（site element）统一为对象：

```
{ name, type, position?, df?, effect?, durability? }
```

`type` 枚举 = `掩体 | 危险 | 机关 | 增幅 | 情绪 | 机动`。

**能力代价**归一为数组：

```
costs: [ { unit, amount, raw? } ]
```

`unit` 为开放字符串（`focus | 阈丝 | 烬 | 消耗 | 同步 | …`），不限死，
以便各世界扩展；`raw` 保留原始列文本。

**世界专属资源**用通用容器，不强求三套同构：

```
world_rules.tracks: [ { key, name, range?, effects[], recovery[] } ]
```

## 5. 散文策略

JSON 只存**机制与短文本**字段：名称 / 效果 / 一句话 / NPC 驱动五要素 /
对白文本。**长篇散文**——世界观描述、十条信条、朗读段、AI 导览简报全文——
**留在 `docs/`**，JSON 以 `source` 指回，不复制进 `data/`。

## 6. `source` 格式（溯源）

每个实体 / 条目带一个 `source`，指回它来自 `docs/` 的哪个文件第几行：

```
docs/<system|scenario>/<文件名>.md:<行号>
```

- 正则：`^docs/(system|scenario)/[^:]+\.md:[0-9]+$`
- 例：`docs/system/01-内核CORE.md:36`
- 校验器会检查：`source` 指向的文件**确实存在**，且行号**在文件行数之内**
  （行号等于文件总行数允许，超过即报错）。
- 账本模板等聚合结构，在**顶层**带一个 `source` 即可，内部条目可省略。

## 7. 校验器支持的 JSON Schema 关键字子集

校验器（`tests/validate_data.py`）只实现下列**有穷子集**；
它**额外允许四个注解**（不做校验）：`$schema`、`$id`、`title`、`description`。

校验关键字（16 个）：

`type`、`required`、`properties`、`additionalProperties`、`items`、`enum`、
`const`、`pattern`、`minLength`、`minimum`、`maximum`、`uniqueItems`、
`$ref`、`$defs`、`anyOf`、`oneOf`。

> **未支持的关键字不得出现在 schema 中。** 校验器在加载 schema 时会对
> 每个 schema 扫描关键字，命中未支持的关键字即报错；
> `tests/test_data.py` 另有一次针对 `data/schema/*.schema.json` 的全量扫描。

语义要点：

- `type`：字符串，或字符串数组（可含 `"null"`）；可空字段优先用
  `anyOf: [{type: …}, {type: "null"}]` 表达。
- `pattern`：等价于「在字符串中搜索匹配」（`re.search`），不是整串匹配。
- `uniqueItems`：按 JSON 规范化比较判重。
- `oneOf`：**恰好一个**子 schema 通过（0 个或 ≥2 个都算失败）。

## 8. `$ref` 形式

只支持两种形式：

- 同文件：`#/$defs/<名字>`
- 同目录相对文件：`<文件名>#/$defs/<名字>`，例如
  `common.schema.json#/$defs/source`。

文件部分**相对 schema 文件所在目录**解析。不支持远程 URL 与指针以外的 JSON Pointer。

## 9. 交叉引用

- 世界账本字段与规则：`docs/system/04-导引者操作系统.md`（第四节）。
- 每册 12 节结构与账本 JSON 骨架：`docs/scenario/S0-系列总纲与编写规范.md`。
- 六维属性 / 等级 / 修正的来源：`docs/system/01-内核CORE.md`（第一、二节）。
- 随机表 JSON 结构：`docs/scenario/S7-随机生成器.md`（数字工具附录）。

---

*本文件是 NotDND 数据层的原创格式约定；以 MIT 许可发布，见仓库根 `LICENSE` 与 `NOTICE`。*
