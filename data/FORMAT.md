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
> `world` 族的**实体项**已随 M1.2a（`docs/scenario/05` 阈界都市）落数据收紧为
> `additionalProperties: false`；M1.2b（`docs/scenario/06` 余烬纪元）沿用该字段集，
> 只补了三个可选字段（`ability.ap_note`、`faction.note`、`history.note`）。
> `random_tables` / `scenario` 两族的实体项当前仍为
> `additionalProperties: true` 基线，待 M1.2–M1.4 落数据时在各自 PR 内逐项收紧。

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

**`system.tactics_resolution`**（战术判定 / 助势骰 / 五档结果；`docs/system/02A` 第一至三节）

- `declared`：自述计数 `{advantage_sources:9, disadvantage_sources:7, outcome_grades:5,
  catastrophe_rows:20, max_edge_layers:3}`，便于机器核对。
- `resolution`：`{formula, components[], df_compare[], play_principle, play_principle_source?}`；
  `components[]` 为 `{name, detail, source}`；`df_compare[]` 为
  `{relation(gt|eq|lt), outcome, source}`。
- `skip_roll[]`：`{order, situation, source}`。
- `edge_dice`：`{die, rule, tiers[], max_layers, beyond_max, beyond_max_source?,
  disadvantage, cancellation, same_source_rule, …}`；`tiers[]` 为
  `{layer(1–3), name, dice, expectation, source}`。
- `advantage_sources[]` / `disadvantage_sources[]`：`{name, layers[], note?, source}`，
  `layers` 为正 / 负整数数组（优势 `1–3`、劣势 `−3–−1`）。
- `outcome_formula?`、`outcome_grades[]`：`{id, band, name, en, meaning, consequence, source}`。
- `catastrophe_table`：`{die, rows[], source}`，`rows[]` 为 `{roll, result, source}`。

**`system.tactics_difficulty`**（难度值参考与设定向导；第四节）

- `declared`：`{ladder_rows:10, party_level_rows:3}`。
- `improvised`：`{baseline, adjustments[]}`，`adjustments[]` 为 `{question, delta, source}`。
- `ladder[]`：`{df, name, analogy, open_ended?, source}`。
- `by_party_level[]`：`{level_range, typical_bonus, target_df, source}`。
- `split_rule`：`{threshold, rule, example, source}`。

**`system.tactics_combat`**（攻击判定 / 掩体 / 先攻与分组；第五、六节）

- `declared`：`{attack_grades:5, fumble_rows:6, cover_tiers:4}`。
- `attack_flow[]`：`{order, step, source}`。
- `attack_grades[]`：`{id, condition, name, en, effect, source}`。
- `fumble`：`{trigger, effects[], die, rows[], simplified, source}`，
  `rows[]` 为 `{roll, consequence, source}`。
- `damage`：`{note, weapon_classes[]}`，`weapon_classes[]` 为 `{id, name, bonus_rule, source}`。
- `cover[]`：`{id, name, guard_bonus, restriction, source}`，`guard_bonus` 可为 `null`（完全掩体）。
- `cover_damage`：`{margin_max, rule, source}`。
- `initiative`：`{formula, rules[], ambush, enemy_grouping[], source}`。

**`system.tactics_maneuvers`**（通用机动库；第七节）

- `declared`：`{maneuvers:30, core_nine:9}`。
- `learning`：`{starting_common, starting_discipline, per_level, teaching, source}`。
- `categories[]`：枚举 `近战类 | 远程类 | 移动与位置类 | 控制与辅助类`。
- `maneuvers[]`：`{id, no(1–30), name, category, ap, contest, effect, core, source}`。
- `core_nine`：`{count:9, numbers[], source}`，`numbers[]` 为机动 `no`。

**`system.tactics_disciplines`**（六大战术流派与能力树；第八节）

- `declared`：`{disciplines:6, layers:5, abilities_per_layer:2, abilities:60}`。
- `rules`：`{layers, per_layer, unlock, cross_branch, source}`。
- `disciplines[]`：`{id, name, en, role, feel, note?, abilities[], source}`；
  `abilities[]` 为 `{layer(1–5), name, effect, capstone, source}`。

**`system.tactics_enemies`**（敌人快速生成；第九节）

- `declared`：`{tiers:4, affixes:14, affix_limit:4}`。
- `tiers[]`：`{id(minion|standard|elite|boss), name, vitality, guard, poise, attack_bonus,
  damage, ap, df, budget, source}`。
- `scaling[]`：`{level_range, vitality, guard, attack_bonus, damage, source}`；
  另有 `scaling_note` / `scaling_note_source?`。
- `affixes[]`：`{name, effect, budget, source}`。
- `affix_limit`：`{max:4, reason, source}`。
- `assembly_example?`：`{description, base, affixes[], budget, source}`。

**`system.tactics_encounter`**（遭遇预算系统；第十节）

- `declared`：`{strength_tiers:4, composition_limits:4}`。
- `formula` / `formula_source?`。
- `budget_cells[]`：`{party_size, level, budget, source}`（扁平单元格，便于校验）。
- `strength[]`：`{id, name, ratio, experience, timing, source}`。
- `composition_limits[]`：`{order, rule, source}`。
- `mass_combat`：`{threshold, rules[], source}`。

**`system.tactics_optional_rules`**（默认关闭的可选规则；第十一节）

- `note`、`declared`：`{wound_rows:12}`。
- `wound_table`：`{die, trigger, rows[], source}`，`rows[]` 为 `{roll, wound, source}`。
- `ammo`：`{rules[], source}`；`morale`：`{formula, triggers[], failure_outcome, source}`。
- `chase`：`{policies[], win_condition, round_note, source}`。
- `players_roll_all`：`{formula, note, source}`。
- `theater_vs_map`：`{modes[], source}`，`modes[]` 为
  `{id, name, description, recommended?, source}`。
- `single_enemy_die`：`{rule, note, source}`。

> **计数核对**（M1.1a 逐条比对 `docs/system/01`）：技能 29；伤害标签 11；性质标签 33；
> 减益 11 / 增益 5 / 派生状态 2；态势 4；动作价格 **10**；反应 5；过载 3；体型加值 5；
> 场地要素 d20 表 20 行；成长选择 6 类；通用专长 30。
> ⚠️ 动作价格表以 `docs/system/01` 第六节为准为 **10 行**（移动/姿态切换/辅助动作/基础攻击/
> 全额能力/快速能力/压制动作/巡查动作/协助/反应）；若别处记为「11」，以源文档为准。

> **计数核对**（M1.1b 逐条比对 `docs/system/02A`）：机动 **30**（核心九式 **9**）；流派 **6**、
> 能力 **60**（每流派 5 层 × 2 项，终结技 6）；敌人模板 **4** 档；等级缩放 6 行；
> 词缀 **14**（叠加上限 **4**）；遭遇预算表 5 人 × 6 级 = 30 格；强度 4；组成限制 4；
> DF 阶梯 **10**；按等级设 DF 3；攻击结果 **5**；掩体 **4**；严重失手 d6 **6**；伤口 d12 **12**；
> 优势来源 **9** / 劣势来源 **7**；五档结果 **5**；灾难 d20 **20**。

**`system.build_pool`**（骰池与成功；`docs/system/02B` 第一节）

- `declared`：`{pool_parts:4, die_rates:4, required_successes:8}`。
- `pool`：`{formula, parts[], note}`，`parts[]` 为 `{name, detail, source}`
  （主属性枚数 / 熟练枚数 / 专精 / 临时加成，共 4 条）。
- `success_threshold`：`{rule, threshold:5, default_die:"d10", die_rates[]}`，
  `die_rates[]` 为 `{die, rate, source}`（d6 33% / d8 50% / d10 60% / d12 67%）。
- `required_successes[]`：`{need, name, tactics_df, source}`（8 档；`need` 取 1/2/3/4/5/6/8/10）。
- `resolution_steps[]`：`{order, step, source}`（判定四步）。

**`system.build_outcomes`**（构建结果梯度；第二节）

- `declared`：`{grades:5, special_cases:2}`。
- `formula`：结果公式字符串（成功数 S − 需求成功数 N）。
- `grades[]`：`{id, band, name, en, consequence, momentum, source}`（凯旋/成功/险成/挫败/灾难）。
- `design_note` / `design_note_source?`：动量反向补偿的设计说明。
- `special_cases[]`：`{id, name, rule, source}`（全骰皆负、最大值爆发）。

**`system.build_die_ranks`**（骰阶与专精；第三节）

- `declared`：`{die_ranks:5, expected_rows:8}`。
- `ranks[]`：`{rank(I–V), die, unlock, source}`（骰阶 5，d6/d8/d10/d12/d20）。
- `starting` / `starting_source?`：起始骰阶 d8。
- `expected_dice[]`：列头（d8/d10/d12）。
- `expected_successes[]`：`{pool, d8, d10, d12, source}`（骰池 2–10 枚，8 行）。
- `advice` / `advice_source?`：配装建议。

**`system.build_momentum`**（动量；第四节）

- `declared`：`{gain_sources:7, spends:7}`。
- `pool`：`{rule, shared_start:0, shared_cap:10, per_person_cap:5, source}`。
- `timing` / `timing_source?`：任意时刻可花费（含他人回合）。
- `gain[]`：`{source_label, amount, note?, source}`（7 条）。
- `spends[]`：`{id, cost, name, effect, source}`（7 条）。
- `clear`：`{rule, source}`（每场景清零）。

**`system.build_stress`**（应力与超载；第五节）

- `declared`：`{gain_sources:5, penalty_bands:4, reduction_methods:4, overload_steps:4}`。
- `cap`：`{formula, typical_range, source}`（体魄 + 心智 + 5）。
- `gain[]`：`{behaviour, amount, source}`（5 条）。
- `penalty_bands[]`：`{band, effect, source}`（4 段）。
- `overload_event`：`{trigger, trigger_source?, steps[]}`，`steps[]` 为 `{order, step, source}`（4 步）。
- `reduction[]`：`{method, amount, note?, source}`（4 条；`amount` 可空）。

**`system.build_combat`**（攻击结算 / 闪避需求 / 命中次数 / 溢出；第六节）

- `declared`：`{attack_flow_steps:5, dodge_needs:5, hit_results:5, overflow_options:4}`。
- `note` / `note_source?`。
- `attack_flow[]`：`{order, step, source}`（5 步）。
- `dodge`：`{formula, formula_source?, modifier_note, modifier_source?, table[]}`，
  `table[]` 为 `{target_type, need, source}`（5 档）。
- `hit_results[]`：`{band, name, effect, source}`（5 档）。
- `overflow`：`{condition, rule, options[]}`，`options[]` 为 `{effect, source}`（4 项）。

**`system.build_overload_chain`**（超载连锁；第七节）

- `declared`：`{options:4, per_scene_limit:1}`。
- `trigger` / `trigger_source?`；`rule` / `rule_source?`。
- `options[]`：`{order, option, source}`（4 项）。
- `limit`：`{per_scene:1, rule, source}`。

**`system.build_glyph_slots`**（符纹插槽；第八节）

- `declared`：`{max_slots_per_faculty:3, slot_sources:5, glyph_sources:3}`。
- `concept`：`{basis, basis_source?, max_slots:3, max_slots_rule, swap_rule, source}`。
- `slot_sources[]`：`{origin, slots, source}`（5 源）。
- `slot_note` / `slot_note_source?`。
- `glyph_sources[]`：`{origin, detail, source}`（3 源）。
- `structure`：`{template, template_source?, note, note_source?}`。

**`system.build_growth`**（构建点与成长；第九节）

- `declared`：`{bp_per_level:2, spend_items:12, starting_items:6, routes:3}`。
- `bp_per_level`：固定 `2`；`bp_rule` / `bp_rule_source?`。
- `spend[]`：`{id, name, bp, note?, source}`（12 项）。
- `starting[]`：`{order, item, source}`（6 项）。
- `routes[]`：`{name, description, risk, source}`（3 路线）。

**`system.build_glyphs`**（符纹库；第十节）

- `declared`：`{glyphs:28, categories:6}`。
- `note` / `note_source?`。
- `categories[]`：字符串数组，取 `增幅类 | 效率类 | 转化类 | 触发类 | 连锁类 | 代价类`。
- `glyphs[]`：`{no(1–28), id, name, category, level(I|II|III), effect, cost, source}`（28 枚）。
- `level_limits[]`：`{level, min_character_level, rule, source}`（I 任意 / II ≥4 / III ≥7）。
- `install_limit` / `install_limit_source?`：同能力同名同类不可叠装。

**`system.build_conversion`**（双引擎互转对照；第十一节）

- `declared`：`{switch_scenarios:5, attribute_rows:5, proficiency_rows:4, difficulty_rows:8,
  edge_rows:4, enemy_defense_rows:5, damage_rows:5, migration_steps:5, mixing_limits:3}`。
- `note` / `note_source?`。
- `when_to_switch[]`、`attribute_conversion[]`、`proficiency_conversion[]`、
  `difficulty_conversion[]`、`edge_conversion[]`、`enemy_defense_conversion[]`、
  `damage_conversion[]`：各为对照表行（字段见 schema）。
- `difficulty_note` / `difficulty_note_source?`；`migration_steps[]`（5 步）；`mixing_limits[]`（3 条）。

> **计数核对**（M1.1c 逐条比对 `docs/system/02B`）：骰阶 **5**（I–V）；需求成功数 **8**；
> 单骰成功率 **4**（d6/d8/d10/d12）；构建结果梯度 **5**（＋特例 2）；动量获取 **7** / 花费 **7**；
> 应力获取 **5** / 惩罚段 **4** / 降低手段 **4**；闪避需求 **5**；攻击流程 **5**；命中次数 **5**；
> 溢出购买 **4**；超载连锁选项 **4**；插槽来源 **5**；构建点花费项 **12**；符纹 **28**（六类）；
> 迁移五步 **5**；混用限制 **3**。

**`system.watch`**（互动层第一节：时段结构；`docs/system/03`）

- `note`：字符串。
- `declared`：`{watches:4}`。
- `watches[]`：`{id, name, hours, features, source}`（晨/昼/昏/夜 4 时段）。
- `budget`：`{source, major_actions, major_hours_min, major_hours_max, minor_minutes, rule}`。
- `pacing`：`{source, session_watches_min, session_watches_max, rule}`。

**`system.travel`**（行程与移动裁定 + 迷路；第一节）

- `note`；`declared`：`{routes:4}`。
- `routes[]`：`{id, name, scope, duration, check, source}`（短/中/远/危险穿越 4 档）。
- `lost_rules`：`{source, rules[]}`，`rules[]` 为 `{order, rule, source}`（迷路 3 条）。

**`system.risk_pool`**（风险池机制；第一节）

- `note`；`declared`：`{trigger_steps:3, die_adjustments:2}`。
- `steps[]`：`{order, step, source}`（3 步）。
- `trigger`：`{die, faces, effect, source}`。
- `die_adjustment[]`：`{id, zone, die, face, source}`（安全 d8 / 危险 d4）。
- `reward_rule`：`{rule, source}`。

**`system.tracking`**（追踪 DF 表；第一节）

- `note`；`declared`：`{freshness_levels:5}`。
- `levels[]`：`{id, freshness, df, note?, source}`（5 级，DF 10/13/16/19/22）。
- `consequences`：`{success, failure, source}`。

**`system.camping`**（扎营与安全；第一节）

- `note`；`declared`：`{camps:4}`。
- `camps[]`：`{id, name, effect, source}`（4 种营地）。

**`system.social`**（交涉论战；第二节）

- `note`；`declared`：`{use_conditions:3, player_actions:6, npc_responses:5, relation_meters:2, argument_paths:4, light_outcomes:4}`。
- `use_rule`：`{source, threshold, conditions[]}`，`conditions[]` 为 `{order, condition, source}`（3 条）。
- `setup`：`{source, stance_formula, persuasion_points_start}`。
- `player_actions[]`：`{id, name, cost, effect, source}`（6）；`npc_responses[]`：`{id, name, condition, effect, source}`（5）。
- `zero_stance`：`{source, rule, effects[]}`。
- `relation_meters[]`：`{id, name, en, range, meaning, source}`（好感 / 敬畏）。
- `relation_effects[]`：`{order, effect, source}`（4）。
- `argument_paths[]`：`{id, name, skills, effective_on, ineffective_on, source}`（利益/道义/恐惧/情感 4）。
- `gm_duty`：`{rule, source}`。
- `light_version`：`{source, check, outcomes[], recommendation, recommendation_source}`，`outcomes[]` 为 `{band, result, source}`（4）。

**`system.investigation`**（调查与线索；第三节）

- `note`；`declared`：`{rule_of_three_paths:3, actions:4, board_responses:3, pressure_types:4}`。
- `three_clue_rule`：`{source, statement, why, why_source, prep, prep_source, example}`。
- `clue_principle`、`scene_hint`：`{rule, source}`。
- `actions[]`：`{id, name, skills, gains, source}`（4）。
- `board`：`{source, steps[], responses[], note}`，`responses[]` 为 `{id, name, effect, source}`（3）。
- `misdirection`：`{source, rules[]}`（3 条）。
- `time_pressure[]`：`{id, name, mechanic, source}`（4）。

**`system.crafting`**（工艺与制造；第四节）

- `note`；`declared`：`{components:3, item_tiers:4, quality_grades:5, modifications:5, world_flavors:3, max_mods_per_item:3}`。
- `components`：`{source, items[], blueprint_rarities[], blueprint_rarity_source, material_qualities[], material_quality_source}`。
- `tiers[]`：`{id, name, examples, worktime, craft_df, requirement, source}`（4）。
- `quality_grades[]`：`{id, outcome, name, effect, source}`（5）。
- `modifications[]`：`{id, name, df, effect, source}`（5）。
- `mod_limit`：`{max:3, rule, source}`。
- `world_flavors[]`：`{world, craft_name, trait, source}`（3）。

**`system.economy`**（经济与物价；第五节）

- `note`；`declared`：`{currency_tiers:3, wealth_levels:5, price_rows:7, haggle_outcomes:5, rarities:5, income_sources:4}`。
- `currency_tiers[]`：`{id, name, tier, relative_value, examples, source}`（3）。
- `wealth_levels`：`{levels[], rule, source}`（赤贫/拮据/普通/宽裕/富有 5 档）。
- `price_reference`：`{source, note, note_source, rows[]}`，`rows[]` 为 `{id, item, tier, source}`（7）。
- `haggle`：`{source, check, df, outcomes[], restriction, restriction_source}`，`outcomes[]` 为 `{id, outcome, effect, source}`（5）。
- `rarities[]`：`{id, name, acquisition, source}`（5）。
- `debt`：`{source, rules[]}`（4 条）。
- `income_sources[]`：`{id, name, income, source}`（4）。

**`system.reputation`**（声望与关系网；第六节）

- `note`；`declared`：`{reputation_axis:7, bond_levels:5, bond_rulings:3, nemesis_duties:3}`。
- `reputation[]`：`{value(−3–+3), label, expression, source}`（7 档）。
- `reputation_note`：`{rule, source}`。
- `bonds[]`：`{level(1–5), name, request, source}`（5）。
- `bond_rulings[]`：`{order, case, ruling, source}`（3）。
- `bond_decay`：`{rule, source}`；`bond_rupture`：`{causes[], rule, source}`。
- `nemesis`：`{trigger, trigger_source, duties[], ai_note}`。

**`system.stronghold`**（据点经营；第七节）

- `note`；`declared`：`{facilities:8, max_facilities:5, maintenance_failures:3, event_rows:20}`。
- `acquisition`：`{examples[], examples_source, cost, source}`。
- `facility_limit`：`{max:5, rule, source}`。
- `facilities[]`：`{id, name, cost, effect, source}`（8）。
- `maintenance`：`{cost, source, failures[], note}`（未缴 3 级恶化）。
- `events`：`{die:"d20", timing, timing_source, source, rows[]}`（20 行）。

**`system.interlude`**（休整与间幕；第八节）

- `note`；`declared`：`{timing_triggers:3, actions:9, vignette_examples:4, time_skip_steps:4, chapter_lines:4}`。
- `timing`：`{source, triggers[]}`（3）；`action_budget`：`{per_interlude:1, per_grand_interlude:2, rule, source}`。
- `actions[]`：`{id, name, effect, source}`（9）。
- `personal_note`：`{rule, source}`。
- `vignette`：`{source, length, examples[], note}`（4）。
- `time_skip`：`{source, steps[]}`（4 步）；`chapter_structure`：`{source, lines[]}`（4 行）。

**`system.teamwork`**（非战斗团队协作；第九节）

- `note`；`declared`：`{assist_rules:4, assist_max:2, collective_steps:3, collective_bands:4, division_actions:7}`。
- `assist_chain`：`{source, rules[]（4）, note}`。
- `collective_check`：`{source, steps[]（3）, bands[]（4）, note}`。
- `division_actions[]`：`{id, name, participants, effect, source}`（7）。
- `info_sharing`：`{source, default, default_source, exception, exception_source, note}`。

> **计数核对**（M1.1d 逐条比对 `docs/system/03`）：时段 **4**；行程 **4**；风险池触发步骤 **3**、骰面调整 **2**；
> 追踪 DF **5**；扎营 **4**；论战玩家行动 **6** / NPC 应对 **5**；论证路径 **4**；轻量版结果 **4**；好感/敬畏 **2**；
> 调查动作 **4**、推理板回应 **3**、时间压力 **4**；物品等级 **4**、品质 **5**、改造 **5**、世界风味 **3**、改造上限 **3**；
> 货币层级 **3**、持有资金 **5**、物价 **7**、讨价还价 **5**、稀有度 **5**、收入来源 **4**；
> 声望轴 **7**、Bond **5**、据点设施 **8**（上限 **5**）、据点事件 d20 **20**、间幕行动 **9**、分工动作 **7**。

**`system.architecture`**（三层架构 / 输出结构 / 四条守则；`docs/system/04` 第一节）

- `declared`：`{layers:3, rules:4, output_sections:3}`。
- `layers[]`：`{id, name, en, responsibility, output, failure, source}`（叙事 Narration / 裁决 Adjudication / 模拟 Simulation）。
- `output_structure`：`{source, sections[]}`，`sections[]` 为 `{order, label, purpose, source}`（裁决 / 叙事 / 钩子）。
- `rules[]`：`{order, name, rule, source}`（4 条操作守则）。

**`system.adjudication`**（裁决优先级堆栈 + 默认裁决表；第二节）

- `declared`：`{stack_levels:6, default_rulings:7}`。
- `stack[]`：`{level(1–6), name, question, source}`。
- `default_table`：`{source, rows[]}`，`rows[]` 为 `{order, situation, default_rule, source}`（7 行）。
- `principle`：`{rule, source}`。

**`system.session`**（会话状态机 + 六种玩法模式；第三节）

- `declared`：`{states:6, modes:6}`。
- `states[]`：`{id, name, role, source}`（SESSION_BOOT … SESSION_WRAP）。
- `modes[]`：`{id, name, en, goal, default_action, advance, end_condition, pitfall, source}`。
- `announcement`：`{rule, source}`。

**`system.ledger_spec`**（世界账本规则 + 最小可行版本；第四节）

- `declared`：`{rules:3, min_fields:5}`。
- `ledger_schema_ref`：固定 `common.schema.json#/$defs/ledger`（账本骨架复用，不重复定义）。
- `rules[]`：`{order, rule, source}`（3 条）。
- `min_viable`：`{fields[], rule, source}`，`fields` 固定 5 项 `clock | threads | facts | npcs | hooks`。

**`system.decision_engine`**（意图—张力—回报决策引擎；第五节）

- `declared`：`{triggers:5, steps:4, dimensions:4}`。
- `triggers[]`：`{order, situation, source}`；`steps[]`：`{order, name, detail, source}`。
- `dimensions[]`：`{abbr(I|R|U|T), name, range, question, source}`。
- `formula`：`{formula, source}`，`formula` 固定 `I×2 + R×2 + U + T`；`alignment`：`{rule, source}`。

**`system.branching`**（三条分支法 + 八种故事引擎；第六节）

- `declared`：`{engines:8, routes_per_decision:3, pool_min:3, pool_max:5}`。
- `engines[]`：`{no(1–8), name, en, feel, pattern, source}`。
- `non_adjacent` / `unselected`：`{rule, source}`；`route_format`：`{fields[], source}`。
- `consistency_questions[]`：`{order, question, source}`（3）；`pool_rules[]`：`{order, rule, source}`（4）。

**`system.direction`**（导演剪接 / 镜头 / 长度；第七节）

- `declared`：`{principles:3, shots:5, lengths:4, closing_lines:3}`。
- `principles[]`：`{order, name, rule, source}`；`shots[]`：`{id, name, usage, example, source}`。
- `length_control[]`：`{position, length, source}`；`closing_lines[]`：`{order, line, source}`。

**`system.npc_drive`**（NPC 驱动轴 / d12 原型 / 推进阶梯；第八节）

- `declared`：`{fields:5, archetypes:12, ladder_steps:6}`。
- `fields[]`：`{id, name, question, requirement, source}`（Drive / Lever / Mask / Tell / Threshold）。
- `archetypes[]`：`{roll(1–12), name, default_drive, default_lever, source}`。
- `progression[]`：`{step(1–6), label, source}`。

**`system.enemy_personas`**（敌人战术人格 + 行为卡；第九节）

- `declared`：`{personas:6, cards:24, cards_per_persona:4}`。
- `personas[]`：`{id, name, en, desire, priority_target, dying, source}`。
- `cards[]`：`{id, persona, name, behavior, source}`（24 张，A1–F4）。
- `mixing`：`{rule, source}`；`execution_notes[]`：`{order, rule, source}`（3）。

**`system.clue_enforcement`**（三线索法则的执行；第十节）

- `declared`：`{prep_paths:4, relief_methods:3, avoidances:4}`。
- `prep_example`：`{source, goal, paths[]}`，`paths[]` 为 `{order, path, source}`（4）。
- `prep_rule` / `relief_rule`：`{rule, source}`；`relief[]`：`{id, name, method, source}`（3）。
- `avoid[]`：`{order, rule, source}`（4）。

**`system.failure`**（失败推进 Fivefold Fail；第十一节）

- `declared`：`{outcomes:5, patterns:6, retry_steps:3}`。
- `outcomes[]`：`{no(1–5), name, method, source}`；`priority`：`{rule, source}`。
- `attribution[]`：`{bad, good, source}`（3）；`patterns[]`：`{order, sentence, source}`（6）。
- `retry[]`：`{step, rule, source}`（3）。

**`system.tension`**（张力曲线管理器；第十二节）

- `declared`：`{bands:5, heat_signals:5, cool_signals:5, fatigue_rules:4, climax_elements:3}`。
- `bands[]`：`{id, name, feeling, scene, source}`（5 档，`feeling` 记 0–10 区间）。
- `heat_signals[]` / `cool_signals[]`：`{order, signal, source}`（各 5）。
- `fatigue[]`：`{order, rule, source}`（4）；`climax_elements[]`：`{order, element, source}`（3）。

**`system.tone_packs`**（语气包；第十三节）

- `declared`：`{packs:8, switch_triggers:3}`。
- `usage`：`{rule, source}`。
- `packs[]`：`{id, name, en, prose, dialogue, events, taboo, source}`（8 种）。
- `switching[]`：`{order, situation, source}`（3）。

**`system.guardrails`**（护栏与一致性校验；第十四节）

- `declared`：`{boundary_items:5, termination_mechanisms:3, boundary_supplements:3, scene_self_checks:8, numeric_invariants:5}`。
- `boundary_checklist[]`：`{order, item, source}`（5）；`termination[]`：`{id, name, usage, response, source}`（3）。
- `boundary_supplements[]`：`{order, rule, source}`（3）；`scene_self_check[]`：`{order, check, source}`（8）。
- `numeric_invariants[]`：`{order, invariant, meaning, source}`（5）。
- `fact_invariant`：`{rule, example_bad, example_good, source}`；`npc_consistency`：`{rule, source}`。

**`system.antipatterns`**（反模式清单；第十五节）

- `declared`：`{patterns:15}`。
- `patterns[]`：`{no(1–15), name, manifestation, fix, source}`。

**世界账本（复用，不重复定义）**：第四节的世界账本字段骨架由
`common.schema.json#/$defs/ledger` 承载；`system.ledger_spec` 只登记更新规则与最小可行版本，
并以 `ledger_schema_ref` 指向该 `$defs`。

> **计数核对**（M1.1e 逐条比对 `docs/system/04`）：三层架构 **3**；操作守则 **4**；输出结构 **3**；
> 裁决堆栈 **6**；默认裁决 **7**；会话状态 **6**；玩法模式 **6**；账本规则 **3** / 最小字段 **5**；
> 决策触发 **5** / 四步 **4** / 打分维度 **4**；故事引擎 **8**；路线池 **3–5**；
> 镜头 **5**；长度 **4**；NPC 字段 **5**；NPC 原型 d12 **12**；推进阶梯 **6**；
> 敌人人格 **6**（行为卡 **24**）；三线索准备途径 **4** / 救济 **3**；五重失败 **5**；
> 句式库 **6**；张力档 **5**；升温 / 降温信号各 **5**；疲劳规则 **4**；高潮要素 **3**；
> 语气包 **8**；终止机制 **3**；场景自检 **8**；数值不变式 **5**；反模式 **15**。


### 4.2 world 族

**`world.module`**（世界模组；源自 `docs/scenario/05`、`06`、`07`；三册共享同一机械语法、仅名称不同，
故 `world.schema.json` 覆盖其**公共字段集**，实体项均为 `additionalProperties: false`）

族载荷（顶层）：

- `world`：`{key, name, engine, source?}`；`engine` 枚举 `tactics | build`。
- `glossary[]`：术语表，`{term, meaning, source}`。
- `factions[]`：`{id, name, alignment?, summary, note?, source}`；`alignment` 为开放字符串——
  05 记阵营分组（`官方 | 半民间 | 反体制 | 资本 | 其他`），06 记原文「性质」列并另以
  `神祇` 标记沉默的诸神；`note?` 用于标注原文瑕疵（重复行等）。
- `regions[]`：地理区域，`{id, name, summary?, atmosphere?, key_places[]?, powers[]?, events[]?, source}`。
- `history[]`：历史事件，`{id, year?, name, detail, note?, source}`；`note?` 用于标注
  原文瑕疵（重复行、乱码、表格缺行而取自正文的条目）。
- `tracks[]`（世界专属资源轨，见 4.4 的 `world_rules.tracks`；如 05 的「阈丝度」）。
- `careers[]`：职途，`{id, name, role, focus[], vitality_bonus, skills[], gear[],
  abilities[{name, cost?, effect}], specializations[], note?, source}`。
- `abilities[]`：能力（共鸣式 / 秘仪 / 协议），`{id, name, category, tier, ap, costs[], effect, source}`；
  `tier` 枚举 `轻 | 中 | 重`；`ap` 为非负整数，反应动作为 `null` 并以 `ap_note` 记
  「反应：消耗 N 点保留 AP」（口径同 4.1 `system.action_economy`）；
  `costs[]` 用通用**能力代价**结构（见 4.4）
  表达各世界不同的施法资源（05 专注 / 阈丝、06 烬、07 同步）。
- `equipment[]`：`{id, name, kind, damage?, ap?, range?, tags[]?, guard?, load?, restriction?, effect?, price_tier, source}`；
  `kind` 枚举 `weapon | armor | gear`；`price_tier` 为**开放字符串**——基础档 `碎银 | 标准 | 贵重`，
  允许带修饰（如 `贵重 ×2`、`标准（黑市）`、`标准～贵重`）；`load` 为开放字符串（`轻 | 中 | 重`，可空 `—`）。
- `enemies[]`：敌体，`{id, name, tier, vitality, guard, poise, attack_bonus, damage, persona, trait, source}`；
  `tier` 枚举 `杂兵 | 杂兵（群） | 标准 | 精锐 | 首领`；`persona` 为**战术人格六型**
  （见 `docs/system/04` 第九节）`狡诈 | 猛攻 | 召唤 | 守护 | 控场 | 领袖`。
- `world_rules`：对象，容纳各世界独有的特则（05 阈丝度 / 回声 / 薄处 / 抑噪器；
  06 烬值 / 燃痕 / 月相 / 根系 / 奉献；07 同步率 / 纯度 / 义体 / 网潜），**允许各世界自定义键**。
- `random_tables[]`（见 4.3 的 `random_table`）。

> **计数核对**（M1.2b 逐条比对 `docs/scenario/06` 余烬纪元）：职途 **17**（16 + 旅人）；
> 秘仪 **100**（9 类：火焰与余烬 16 / 生命与治疗 12 / 心智与幻觉 12 / 移动与空间 10 /
> 防护与反制 12 / 亡灵与骨骼 12 / 自然与根系 12 / 神术 8 / 禁忌 6）；装备 **28**
> （近战 9 / 远程 5 / 护甲 6 / 施法器材与消耗 8）；敌体 **80**（A 灰潮生物 22 / B 人类与类人 18 /
> C 亡灵与骨骼 14 / D 巨兽与野兽 14 / E 要角与首领 12）；区域 **9**；术语 **7**；
> 势力 **10**（诸神表 5 行——含 1 行重复——＋ 主要势力 5）；历史 **8**（七纪表 7 行——
> 含第三纪、第五纪各 1 行重复——＋取自正文「树倒（第四纪末）」的第四纪 1 条）；随机表 **7**。

> 实体项的 `id` 用稳定 slug（如 `career-01`、`resonance-001`、`arcana-001`、`enemy-a01`）；同一顶层数组内
> `id` 不得重复。长文（世界观描述、十条信条、朗读段）留在 `docs/`，JSON 只留机制与短文本（见第 5 节）。

### 4.3 random_tables 族

**`random_tables.catalog`**

- `resource`：固定为 `prism.random_tables`。
- `tables[]`：`{id, name, die, purpose?, hook_density?, rows[], use?, on_repeat?, interface?, source}`。
  - `die` 形如 `d20` / `d100`（pattern `^d[0-9]+$`）。
  - `rows[]`：`{roll, result, example?, tag?, tier?, effect?, df?, source?}`，`roll` 从 1 起。
    - `df`：可空整数，用于「场地要素」类行（如 `docs/system/08` 扩展表）；具体数值口径
      仍以内核 `01-内核CORE.md` 第十节为准，此处只存该行自带的难度值。
    - `source`：**行级**溯源（可选）；逐行指回 `docs/` 的行号，便于机器核对。
  - `interface?`：与内核的唯一接口；`type` 说明结果落到哪一类字段，数值一律回查
    `01`／`02A`／`03`，本族不复制内核数值表。
- `generators[]`：`{id, name, steps[], budget_ref?, optional_flavor?, source?}`；
  `steps[]` 为 `{order, table, role?}`，`table` 指向 `tables[].id`。
- `ledger_bridge`：对象，把表 `id` 映射到账本字段（`npcs` / `locations` / `hooks` / `threads` /
  `facts` / `factions` 等，见 4.4 的世界账本）。
- 表与行都可带 `source`；每一行也可另带行级 `source`（M1.3 起）。

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
