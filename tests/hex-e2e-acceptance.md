# 六角格战斗 MVP 端到端验收 · 原规则保留 / 新空间能力映射对照表

Issue #429（M13-B14）验收文档。结论：**六角格战斗是完整系统**——生成、展示、
移动、攻击、环境互动、持久化 / 终局全部打通；**原有两套规则引擎没有被简化
或绕过**——d20 战术引擎与构建骰池引擎分别经 #424 / #425 适配器完成各自的
真实结算，全部公式仍只在 `prism_core`（#423），新空间层零公式。

- 测试入口：`tests/test_hex_e2e_acceptance.py`（12 项全过）
- 三种战场 fixture：`tests/fixtures/hex_e2e/`（室内多层 / 户外危险 / 现代风格）
- UI 主流程证明：`tests/test_ui_battle.py`（#453 真实浏览器测试，B1 fixture
  渲染 + B2 真实 API 全链路）

## 一、原规则保留（两引擎各走各的真实结算）

| 规则项 | 规则出处 | 六角格路径上的真实结算 | 验证用例 |
|---|---|---|---|
| d20 命中六档（暴击 / 重击 / 命中 / 擦过 / 落空 / 严重失手） | 02A 附录速查表 | `tactical_tactics_adapter.execute_attack` → `prism_core.resolve_attack`，档位由 margin + 自然值决定，零重写 | `check_rules_d20_settlement` |
| 自然 1 严重失手（无伤害 + 攻击者【失衡】+ 失手表） | 02A | 同上（真实骰点、固定 seed 复现） | 同上 |
| 伤害管线（唯一出口 `deal_damage`） | 01 第四节 | 抗性减半 / 弱点 +50%+【失衡】/ 免疫 / 吸收全在核心，适配器只传 tag | 同上 |
| 韧性削减与破韧（普通减半 / 暴击全额；归零 broken） | 01 第八节 | `resolve_attack` 内 `apply_poise_damage`，e2e 验证 broken 置位 | 同上 |
| 暴击横向状态【失衡】 | 02A:223 | 核心施加，e2e 断言目标条件 | 同上 |
| 首领阶段（绝望形态伤害 +2） | 01 第八节阶段表 | 核心公式，e2e 同 seed 对照 +2 | 同上 |
| 掩体表（轻 / 中 / 重 → 防护 +2/4/6；完全 = 无法被攻击） | 02A:257 / 02A:264 | `map_context_for_d20` 查 `COVER_GUARD`；full → 适配器 `blocked`（不掷骰） | `check_rules_d20_cover_and_high_ground` |
| 高地助势（+1 助势骰） | 02A:93 | `map_context_for_d20` → `advantage_layers +1`；高度不加伤害（not_mapped） | 同上 |
| 构建引擎骰池（属性 + 熟练折半 + 专精 + 临时） | 02B 第一节 | `tactical_forge_adapter.resolve_build_attack` → `build_pool`，e2e 断言构成 | `check_rules_build_settlement` |
| 需求成功数与封顶（掩体 / 态势每项 +1，最多 +2） | 02B:261 | `map_context_for_build` → `build_dodge_need_adjusted`（封顶在核心） | 同上 |
| 动量（五档增益 + 全骰皆负 +1） | 02B 第四节 | `build_roll` 返回 momentum，e2e 断言 | 同上 |
| 应力（四段惩罚 + 上限崩溃：疲惫 3 层 / strain +1 / 回落 50%） | 02B 第五节 | `prism_core.stress_penalty / stress_gain / stress_overload_event`，e2e 断言 | 同上 |
| 超载连锁（≥2×需求且盈余 ≥3；4 项；每场景 1 次） | 02B 第七节 | `build_overload_chain`，e2e 断言触发 / 限次 | 同上 |
| AP 经济（每回合上限与动作价目） | `data/system/action_economy.json` | 服务端扣减，测试对照数据文件（不写死） | `check_rules_economy_encounter_victory`、HTTP 主流程 |
| 遭遇预算（人数 ×(均级 +2)，强度档折减，首领 ≤60%） | 02A 第九、十节 | `build_encounter` 照旧生成敌体，e2e 重算预算并核对 | 同上 |
| 逃跑代价（压力 +1、无经验）/ 胜利结算（经验分配 + 压力） | 02A | `do_flee` / `_end_battle` 走 PRISM 既有出口，e2e 断言 | 同上、HTTP 主流程 |
| 连携（统帅系战术 / 连携窗口） | 02A 第五节 | **见缺口 G1**：六角格 MVP 管线无结算入口，适配器显式 `unsupported`，不静默降级 | `check_traceability_and_unmapped` |

## 二、新空间能力（M13 家族）→ 原规则的接入点

| 空间能力 | 模块 | 映射到原规则的方式 | 验证用例 |
|---|---|---|---|
| 六角几何（axial / cube、距离、确定性 hex-line） | #417 `tactical_hex` | 纯几何，供查询与移动复用；不进公式 | 既有 #417 回归 + e2e 全流程 |
| 战场加载 / 校验 / 口径隔离（metric↔abstract 不互算） | #417 `tactical_battlefield` | 阈值唯一来源 `tactical_space.json` | `check_fixtures_three_battlefields` |
| 战场生成（模板 + seed，主题差异全来自数据） | #418 `tactical_battlefield_gen` | 产出通过既有加载器终校验；换 world_id 不改格位（无世界名分支） | `check_fixtures_three_battlefields`、`check_fixture_world_id_neutrality` |
| 移动（确定性 Dijkstra、占格互斥、垂直连接授权、坠落提案） | #419 `tactical_movement` | 服务端重算（`do_move`），客户端只给目标格；blocked 逐条带规则引用 | HTTP 主流程、`check_traceability_and_unmapped` |
| 空间查询（距离 / LOS / 掩体 / 高地，可解释 reasons + rule_refs） | #444 `tactical_queries` | 只产空间事实；掩体 / 高地经映射进入两引擎既有输入 | `check_rules_d20_cover_and_high_ground` |
| 物件状态机（机关 / 危险 / 可移动 / 可拾取，幂等键） | #421 `tactical_objects` | 提案 → 服务端原子应用；主题对象全部来自定义数据 | `check_rules_object_interactions_full` |
| TacticalContext 聚合（单一空间事实对象、revision） | #447 `tactical_context` | 两引擎共用电同一份事实；stale 整体拒绝 | 各规则用例 |
| d20 适配器（支持矩阵 + 拦截 + 编排） | #424 `tactical_tactics_adapter` | attack/guard 结算、move 透传、maneuver/interact 显式 unsupported | `check_traceability_and_unmapped` |
| 构建适配器（骰池侧） | #425 `tactical_forge_adapter` | 空间事实只影响防御侧 dodge_need（算法隔离，d20 概念不入骰池） | `check_rules_build_settlement` |
| 服务端战斗闭环（先攻 / 回合 / AP / 事件 / 原子提交） | #426 `tactical_battle_service` + 网页层 | 权威写入经状态层；骰点只在服务端 | HTTP 主流程、`check_e2e_restore_and_legacy_saves` |
| 敌方战术 AI（合法动作选择器） | #428 `tactical_ai` | 本验收以 #426 最小确定性敌方回合证明闭环；AI 接线属后续单 | HTTP 主流程（敌回合事件） |

## 三、端到端覆盖矩阵（验收标准逐条）

| 验收标准 | 证明 |
|---|---|
| 桌面 / 移动 Web 主流程可完整玩完一场小型战斗（几何 SVG 占位） | `tests/test_ui_battle.py` B1/B2（真实浏览器 + 真实服务）；本单 HTTP 主流程从战役地点开战到终局 |
| 2–3 名玩家友方单位 + 2 类敌方单位 | e2e 主流程断言 3 名友方、敌体按预算生成（≥2 类） |
| 高度差 / 障碍 / 两档以上掩体 / 危险 / 机关 / 可破坏可移动对象各有真实结算案例 | 高度差与高地：`check_rules_d20_cover_and_high_ground`（balcony 层差 → 助势）；障碍：`outdoor_hazard.json` obstacle_height / blocks_los + `indoor_multilevel.json` heavy 掩体柱；掩体两档以上：light/medium/heavy 格位 → d20 防护加值与构建需求成功数；危险：`check_rules_object_interactions_full`（电弧炮塔 interact 相位）；机关：能量屏障 / blast-gate 开闭改占格（HTTP 主流程 + 模块级）；可破坏 / 可移动：燃料桶摧毁、货箱挪格、数据芯片拾取 |
| 两引擎分别完成自己的真实规则结算；未映射能力显式可见 | 第四节（d20）与第五节（骰池）用例；not_mapped / unsupported 断言见 `check_traceability_and_unmapped` |
| 旧场景与旧存档回归；run_all 由 CI 门真实执行 | `check_e2e_restore_and_legacy_saves`（旧档惰性迁移、断线重连、终局恢复）；`bash tests/run_all.sh` 不在本机跑（锁树），由 CI 回归门执行 |

## 四、缺口与登记口径（不通过降低测试关闭）

- **G1 连携**：统帅系连携窗口 / 连锁层数属 02A 战术机动层，#423 核心无
  TacticalContext 结算入口 → 适配器显式 `unsupported`（reasons 带
  rule_ref），不静默降级为普通攻击。属登记缺口，需后续数据 / 核心 Issue。
- **G2 武器射程表**：世界模组装备数据未落库，基础攻击可达按相邻格
  （02C:144 登记口径，`ATTACK_REACH_CELLS` 常量显式标注来源）。
- **G3 sight 配置**：`system.tactical_space.sight` 未落 → metric 视线
  **结构化失败**（reasons 指明缺 sight，不静默取 0）；abstract 视线可用。
  e2e 断言两种行为并存。
- **G4 hazard enter 相位不可达**：物件按 #421 语义占用自身格，单位无法
  「进入」→ 危险触发走 interact 相位（#415 登记的权宜口径）。
- **G5 默认模板单 surface 全 H0**：`service-default-room` 为登记口径；
  多层 / 高度差的展示与结算由 fixture 与 #453 UI 测试覆盖。
- **G6 敌方 AI 接线**：#428 战术 AI 已交付独立入口，#426 闭环暂用最小
  确定性策略（登记口径，AI 接线属后续 Issue）。
