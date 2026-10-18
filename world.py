# -*- coding: utf-8 -*-
"""world：世界运行时内核（M12-W4，WORLD-DESIGN.md §6 / §7.2 / §11 W4）。

纯函数模块：零 IO、零第三方依赖（只 import 标准库 copy/hashlib/re）；
不 import `notdnd_web` / `state` / `neura`（依赖方向：编排层调用 world，
world 不回头）；与 `worldgen.py` 互不 import（§9.1），goods 形状按其
`synthesize` 产物对齐（list[dict] 进、dict 键控存）。

对外面：
- `new_block()` / `migrate_block()` / `prune_block()`：`blocks.world` 不透明块
  （§6.1 字段级形状；version 双键；硬上限 + 确定性裁剪）；
- `settle_trade()`：玩家经济动作的结算（出口 2.2：库存/余额不为负，
  不足则拒单且**无** Entry——账随拒单一起消失）；
- `world_tick()`：日切结算纯函数入口（§6.3，幂等键 `wtick_<day>`，一天至多
  一次）；生产/消费聚合 → 工资/税/维护 → 价格步进 → 扰动判定（§7.2 五要素
  表，事实驱动）；**不含** `_chron_advance` 接线（归 W5/W6 队列 C）；
- `catch_up()`：离线补算（§6.3：逐日结算；超 `MAX_CATCHUP_DAYS` 折叠为末日
  一条 `catchup_summary`）；
- `verify_ledger()`：账本三等式校验（§6.2 / §10.2 断言 2.1）；
- `register_rejudge()`：远场重判预算（§8.4，`REJUDGE_DAILY_CAP` 按 NPC 计）；
- `record_fact()`：world.fact 写入（§8.2 三条路之一；调用方控制事实资格，
  AI 文本不进 facts）；
- `new_project()` / `project_create` / `project_queue` / `project_start` /
  `project_pause` / `project_resume` / `project_revoke` /
  `project_settle_milestone`：建造工程全生命周期（§7，W6；两条通道的
  汇合结算函数，§7.3）；
- `new_directive()` / `directive_add` / `directive_set_status` /
  `directive_explain`：委托指令（§6.5，W6；可解释六项 + 硬回问清单）。

新口径（与设计原文的出入 / 设计留白处的落法，已在 PR「风险与口径补充」登记）：
1. `wtick_key(day)` 零填充为 `wtick_%04d`（如 `wtick_0001`），保证幂等键满足
   `^[A-Za-z0-9_-]{8,64}$`（裸 `wtick_1` 只有 7 字符，越下限）。
2. 幂等键载体：`economy.wticks`（chron 块 `idempotency` 同位；§6.1 形状表未列，
   属块形状补充）。
3. `schema_version` 与 `version` 双键同值（照 `neura` / `chron` 块范本）。
4. goods 以 `{good_id: {...}}` 键控存储（worldgen 产物是 list，进块时转换，
   条目字段逐字保留）。
5. 生产/消费 v1 公式（§6.3 第 1 步的确定性落法）：
   - 生产：`SETTLEMENT_YIELD_PER_DAY` × 「位于该 good `produced_by` 地点的
     活跃据点数」；
   - 消费：必需类目（`ESSENTIAL_CATEGORIES`：食/住）按据点人口
     × `CONSUMPTION_PER_CAPITA` 均摊到该类目各 good（余数按 good_id 字典序
     给第一个），并受当日可用库存钳制（吃不起就少吃，不拒日切）；
   - 净额为 0 的 good 不记 Entry（不产零账噪声）。
6. 价格步进（§6.3 第 3 步）：`step = max(1, floor(price * PRICE_STEP_CLAMP))`，
   短缺（需求超出「库存+当日产量」）→ 涨，过剩（当日产量 > 需求）→ 跌，
   最低 1；库存缓冲内的正常消耗不动价。最小步长 1 是 ±10% 钳制在整数价格
   下的最低可动幅度（低价 good 一步可超 10%，登记为口径）。
7. 扰动概率层（§7.2 P7）：`mode="seed"` 用
   `sha256("world|<seed>|<rule_id>|<day>|<salt>")` 前 8 hex mod 1000
   （Python 内建 `hash()` 跨进程不稳定，禁用）；`mode="prism"` **不开自由骰**
   ——world 不 import prism，检定结果由编排层经 `prism_results` 显式传入，
   未传 = 不触发。
8. 扰动冷却存 `projects[pid].cooldowns[rule_id] = day`（§7.2 第 ④ 要素落点）。
9. 工资/维护付款来源 = `balances["coin"]` 中央金库（v1 宏观账单账户口径）；
   发不出 → 据点/工程置 `blocked_reason="funds"`（§7.2 资金断，确定性）且
   **无** Entry（不为负）。
10. `stopped_at` 屏障内不日切（§6.3 屏障口径）；补结算逐日补齐由编排层逐日
    调用 `world_tick`（W5 接线）。
11. 人口摘要档位：0=empty / 1-9=low / 10-49=mid / 50+=high（headcount_band
    是档位不是精确数，§8.3）。
12. 工程里程碑推进（§6.3 第 4 步）与建造材料结算归 W6（已随本片落地：
    `_advance_projects` + Project 结算函数）；本片保留工程工资结算与
    「所需 good 断供 → blocked」的结构化判定。
13. `catch_up` 折叠路径：价格只按末日步进一步；折叠日不写 `wtick` 键，
    `economy.day` 推进本身即幂等护栏（倒退日切被 `world_tick` 拒绝）。
14. `good_price_spike` 的作用对象 = 匹配事实的 `subject_ref`（须是块内
    good_id）——扰动对象由事实本身指认，进一步收紧事实驱动。

W6（建造与委托式管理，§7 / §6.5）新口径：
15. 工期折算：`labor_days / LABOR_EFFICIENCY_PER_DAY` 向上取整 ×
    `MINUTES_PER_DAY`——劳动效率是**速率常数**（人日/日），不是时间常数，
    时间单位只有 CHRON §2.3 的分钟。
16. 开工五条件（§7.2）中的条件四/五（地点合法 + 校验过的地图）由编排层经
    `place = {place_id, kind, validated}` 注入——world.py 不 import atlas
    （§9.1 依赖方向）；`place=None` = 编排层没给地图信息 → 条件不满足。
17. 劳动力账户 = `balances["labor"]`（人日池，§7.2 三来源的汇账户）：开工
    一次性承诺扣足 `labor_days`（Entry kind="labor_commit"，W6 扩展项）；
    指令劳力池按日注入（人日价 `DIRECTIVE_LABOR_COIN_PER_DAY`）。
18. 玩家工程开工/完工随所属提交落块（CHRON 卡到期结算或 M8 同步分支），
    不单独开事件（A0）；背景工程由日切第 4 步推进里程碑并自动开工
    （source ∈ {directive, npc}；`places` 未注入地点信息时不开工）。
19. 完工设施进 `settlements[].facilities[]`（place 无据点则建空据点承载），
    维护费并入据点到期义务从中央金库扣；维护欠费 → 据点 blocked(funds)
    （W4 既有）+ 设施逐座降级一格（`maintenance_level`，到底即停）。
20. 指令创建即 `active`（v1 无审阅通道）；`paused/blocked/revoked` 日切
    跳过；无 `cap_total` 的指令不自动花费；采买数量在提案生成时按
    `cap_total - spent_total` 钳制，单笔超 50% 命中硬回问 → 指令 blocked
    + `directive_ask` 事实留痕、无花费；`grain_days` 无消费人口时有粮 =
    无界哨兵（GRAIN_DAYS_UNBOUNDED）。
21. 扰动效果只作用于在场工程（`DISRUPTIBLE_STATUSES`：running + W4 夹具
    值 active）；`progress_loss` 吃 §7.1 的 `progress.labor_done_days`。
"""

import copy
import hashlib
import re

# ── 常量（一处可调；§6.1 / §6.3 / §7.2 / §8.4 / §12.1 P2/P3/P8） ──────────

WORLD_VERSION = 1

# 失败隔离（照 NEURA_SKIPPED_LOG_KIND 先例；接线归 W5/队列 C，本片先定常量）。
WORLD_SKIPPED_LOG_KIND = "world_skipped"

# 块体积硬上限（§6.1 / §12.1 P3：先单块 + 硬上限，实测 > 512 KiB 再评估分片）。
LEDGER_MAX = 512                  # 滚动明细条数
FACTS_MAX = 256                   # world.facts 条数
POPULATION_SUMMARIES_MAX = 128    # population.summaries 条数

# 价格步进钳制（§6.3 第 3 步 / §12.1 P2：±10%/日）。
PRICE_STEP_CLAMP = 0.10

# 远场重判预算（§8.4 / §12.1 P8：按 NPC 计，同日 ≤ 4 次，跨日归零）。
REJUDGE_DAILY_CAP = 4

# 离线补算折叠阈值（§6.3：超过直接跳到末日结算终值）。
MAX_CATCHUP_DAYS = 90

MINUTES_PER_DAY = 1440            # 口径读 CHRON §2.3（不新建时钟）

# 生产/消费 v1 公式参数（新口径 5）。
SETTLEMENT_YIELD_PER_DAY = 2
CONSUMPTION_PER_CAPITA = 1
ESSENTIAL_CATEGORIES = ("food", "shelter")

# 人口摘要档位（新口径 11）。
HEADCOUNT_BANDS = ((50, "high"), (10, "mid"), (1, "low"), (0, "empty"))

ENTRY_KINDS = (
    "produce", "consume", "trade", "wage", "tax", "debt",
    "build_material", "build_pay", "maintenance", "grant",
    "disruption",          # §7.2：扰动留痕（§6.2 kind 枚举的扩展项）
    "catchup_summary",     # §6.3：折叠补算的末日终值
    "labor_commit",        # §7.2：开工时劳动力人日承诺（W6 扩展项，PR 登记）
)
DISRUPTION_KINDS = ("weather", "supply", "hostility", "labor", "accident")
TRIGGER_OPS = ("exists", "absent", "eq", "gte", "lte")
EFFECT_KINDS = ("block_project", "pause_project", "cost_mult",
                "progress_loss", "good_price_spike")
SEVERITIES = ("light", "heavy")

IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

# 终态/停摆状态（这些状态不参与生产、不发工资；§7.3 中断语义自表达）。
# W6：Project 七态正式化（§7.1），completed / failed 并入不活跃集。
INACTIVE_STATUSES = ("paused", "blocked", "done", "revoked",
                     "completed", "failed")

# ── Project 与指令（§7.1 / §6.5；W6） ─────────────────────────────────────

PROJECT_STATUSES = ("draft", "queued", "running", "blocked", "paused",
                    "completed", "failed", "revoked")
PROJECT_SOURCES = ("player", "directive", "npc")
DIRECTIVE_STATUSES = ("draft", "active", "paused", "blocked", "revoked")
DIRECTIVE_METRICS = ("grain_days", "good_stock")
# goal.op 词形与符号形都收（§6.5 示例用 ">="；内部统一成词形）。
DIRECTIVE_COMPARE_OPS = ("gte", "lte", "eq")
DIRECTIVE_OP_ALIASES = {">=": "gte", "<=": "lte", "==": "eq", "=": "eq",
                        "gt": "gte", "lt": "lte"}

# 劳动效率（§7.1：工期按 labor_days / 劳动效率 折算分钟）：**速率常数**
# （人日/日），不是时间常数——时间单位只有 MINUTES_PER_DAY（CHRON §2.3）。
LABOR_EFFICIENCY_PER_DAY = 1
# 完工设施日维护（§7.2：system.stronghold 3 级恶化口径；一处可调）。
FACILITY_MAINTENANCE_PER_DAY = 2
FACILITY_MAINTENANCE_LEVELS = 3
# 硬回问清单之「单笔超 cap_total 50%」（§6.5）。
DIRECTIVE_HARD_ASK_RATIO = 0.5
# 指令自动采买在 authorized_scope 里的动词（§6.5 授权边界；PR 登记）。
DIRECTIVE_PROCURE_VERB = "procure"
# 指令劳力池人日价（coin/人日；一处可调）。
DIRECTIVE_LABOR_COIN_PER_DAY = 1
# 无消费人口时「储备天数」的无界哨兵（有粮就有无穷天）。
GRAIN_DAYS_UNBOUNDED = 10 ** 9

# 扰动效果的作用面：W4 夹具用 "active"、§7.1 正名为 "running"——两者都吃
# （PR 登记：draft/queued/completed 等不受扰动效果影响）。
DISRUPTIBLE_STATUSES = ("running", "active")


# ── v1 内置扰动规则表（§7.2 五要素；一处可调；schema 预留 spec 覆盖项） ────
# 事实驱动：trigger 全部对 `blocks.world.facts` 判定，无事实支撑一律不触发。

DISRUPTION_RULES = (
    {
        "rule_id": "weather_storm",
        "kind": "weather",
        "trigger": {"fact_kind": "storm_forecast", "op": "exists",
                    "window_days": 2},
        "probability": {"mode": "prism",
                        "prism": {"stat": "survival", "dc": 12}},
        "effect": {"kind": "block_project", "params": {}},
        "cooldown_days": 3,
        "severity": "heavy",
    },
    {
        "rule_id": "hostile_occupation",
        "kind": "hostility",
        "trigger": {"fact_kind": "place_lost", "op": "exists",
                    "window_days": 7},
        "probability": {"mode": "seed", "seed": {"p": 0.8,
                                                 "salt": "hostile"}},
        "effect": {"kind": "pause_project", "params": {}},
        "cooldown_days": 10,
        "severity": "heavy",
    },
    {
        "rule_id": "supply_scarcity",
        "kind": "supply",
        "trigger": {"fact_kind": "good_scarce", "op": "gte", "value": 1,
                    "window_days": 3},
        "probability": {"mode": "seed", "seed": {"p": 0.5,
                                                 "salt": "scarcity"}},
        "effect": {"kind": "good_price_spike", "params": {"factor": 2}},
        "cooldown_days": 5,
        "severity": "light",
    },
    {
        "rule_id": "labor_dispute",
        "kind": "labor",
        "trigger": {"fact_kind": "labor_unrest", "op": "gte", "value": 2,
                    "window_days": 5},
        "probability": {"mode": "seed", "seed": {"p": 0.4, "salt": "labor"}},
        "effect": {"kind": "progress_loss", "params": {"ratio": 0.5}},
        "cooldown_days": 7,
        "severity": "light",
    },
)


class WorldError(Exception):
    """world 模块自有异常（拒载 / 拒单 / 非法形状）。"""


# ── 小工具 ────────────────────────────────────────────────────────────────

def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value, default=""):
    return value if isinstance(value, str) else default


def _clone(value):
    return copy.deepcopy(value)


def _is_active(record):
    """据点/工程默认活跃；显式进入终态/停摆状态后退出结算。"""
    return isinstance(record, dict) and \
        record.get("status") not in INACTIVE_STATUSES


def wtick_key(day):
    """日切幂等键（新口径 1：零填充保证满足 8–64 字符约束）。"""
    key = "wtick_%04d" % int(day)
    if not IDEMPOTENCY_KEY_RE.match(key):
        raise WorldError("wtick 幂等键非法：%r" % (key,))
    return key


def _unit_hash(seed, rule_id, day, salt):
    """§7.2 种子确定性公式（新口径 7）：返回 [0, 1000) 的稳定整数。"""
    raw = "world|%s|%s|%d|%s" % (seed, rule_id, int(day), salt)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 1000


def _headcount_band(population):
    for threshold, band in HEADCOUNT_BANDS:
        if population >= threshold:
            return band
    return "empty"


# ── §6.1 空块与惰性迁移 ───────────────────────────────────────────────────

def new_block(spec=None, *, seed=0, provenance=None, goods=None, day=0,
              balances=None):
    """§6.1 的空 `blocks.world`（可 JSON 序列化；默认值不共享可变对象）。

    - `goods`：worldgen `synthesize` 产物形状（list[dict]，每条带 good_id）
      或 `{good_id: {...}}` 键控 dict；None = 空表；
    - `day`：初始已结算日（genesis 通常 0）；
    - `balances`：期初账户（如 `{"coin": 10000}`）；
    - `prices` 是派生快照：初值 = 各 good 的 `base_price`（落盘只为审计）；
    - `genesis`：三等式①的重放基线（初始库存/余额快照，创建时一次写入；
      新口径 15——「replay(genesis, events)」的 genesis 落点）。
    """
    goods_map = {}
    if isinstance(goods, list):
        for item in goods:
            if not isinstance(item, dict) or not item.get("good_id"):
                continue
            goods_map[str(item["good_id"])] = _clone(item)
    elif isinstance(goods, dict):
        for good_id, item in goods.items():
            if isinstance(item, dict):
                goods_map[str(good_id)] = _clone(item)

    balances_map = {}
    if isinstance(balances, dict):
        for account, value in balances.items():
            if _is_int(value) and value >= 0:
                balances_map[str(account)] = value

    prices = {}
    for good_id in sorted(goods_map):
        base = goods_map[good_id].get("base_price")
        prices[good_id] = {"price": base if _is_int(base) and base >= 1 else 1,
                           "updated_day": int(day)}
    return {
        "version": WORLD_VERSION,
        "schema_version": WORLD_VERSION,
        "seed": seed,
        "spec": _clone(spec) if isinstance(spec, dict) else {},
        "provenance": _clone(provenance) if isinstance(provenance, dict) else {},
        "economy": {
            "day": int(day),
            "goods": goods_map,
            "prices": prices,
            "ledger": [],
            "balances": _clone(balances_map),
            "genesis": {
                "balances": _clone(balances_map),
                "goods": {good_id: good.get("stock", 0)
                          for good_id, good in sorted(goods_map.items())
                          if _is_int(good.get("stock", 0))},
            },
            "wticks": {},
        },
        "projects": {},
        "settlements": {},
        "population": {"summaries": []},
        "facts": [],
        "rejudge": {"day": int(day), "counts": {}},
    }


def migrate_block(block, notes=None):
    """§6.1 惰性迁移：**只补不覆盖**；脏块降级；更高版本**拒载**。

    照 `neura.migrate_block` / `chron.migrate_block` 范本：
    - `block is None`（老档没有 world 块）→ 空块；
    - 不是对象 / version 脏值 → 整体降级为空块（一个坏形状不猜内容）；
    - version **高于** `WORLD_VERSION` → 抛 `WorldError`（不静默降级）；
    - 缺字段 → 只补该字段；已有合法值逐字保留；单个脏字段只降级它自己。
    返回**新块**，不改写入参；`notes` 给了就追加说明。
    """
    notes = [] if notes is None else notes
    if block is None:
        notes.append("world 块缺失：按惰性迁移补空块（WORLD-DESIGN §6.1）")
        return new_block()
    if not isinstance(block, dict):
        notes.append("world 块形状非法（%s）：整体降级为空块"
                     % type(block).__name__)
        return new_block()

    version = block.get("version", block.get("schema_version", WORLD_VERSION))
    if not _is_int(version):
        notes.append("world.version 脏值（%r）：整体降级为空块" % (version,))
        return new_block()
    schema = block.get("schema_version", version)
    if not _is_int(schema):
        notes.append("world.schema_version 脏值（%r）：按 version 迁移"
                     % (schema,))
        schema = version
    if version > WORLD_VERSION or schema > WORLD_VERSION:
        raise WorldError(
            "拒载：world 块 version=%d / schema_version=%d 高于本实现 %d"
            "（WORLD-DESIGN §6.1 / P10）" % (version, schema, WORLD_VERSION))
    if version < WORLD_VERSION or schema < WORLD_VERSION:
        notes.append("world 块 version=%d 低于本实现 %d：已按惰性迁移补齐"
                     % (min(version, schema), WORLD_VERSION))

    migrated = new_block(block.get("spec"), seed=block.get("seed", 0),
                         provenance=block.get("provenance"))
    migrated["spec"] = (_clone(block["spec"])
                        if isinstance(block.get("spec"), dict) else {})
    migrated["provenance"] = (_clone(block["provenance"])
                              if isinstance(block.get("provenance"), dict)
                              else {})

    raw_economy = block.get("economy")
    economy = migrated["economy"]
    if not isinstance(raw_economy, dict):
        if "economy" in block:
            notes.append("world.economy 脏值（%s）：降级为默认经济表"
                         % type(raw_economy).__name__)
        raw_economy = {}

    day = raw_economy.get("day", 0)
    economy["day"] = day if _is_int(day) and day >= 0 else 0
    if "day" in raw_economy and economy["day"] != day:
        notes.append("world.economy.day 脏值（%r）：降级为 0" % (day,))

    raw_goods = raw_economy.get("goods", {})
    if isinstance(raw_goods, dict):
        for good_id, item in raw_goods.items():
            if isinstance(item, dict):
                economy["goods"][str(good_id)] = _clone(item)
            else:
                notes.append("world.economy.goods[%s] 不是对象：已剔除"
                             % (good_id,))
    elif "goods" in raw_economy:
        notes.append("world.economy.goods 脏值（%s）：降级为空表"
                     % type(raw_goods).__name__)

    # prices 派生快照：只保留形状合法的条目（审计用，缺项可由 base_price 重建）。
    raw_prices = raw_economy.get("prices", {})
    if isinstance(raw_prices, dict):
        for good_id, item in raw_prices.items():
            if isinstance(item, dict) and _is_int(item.get("price")) \
                    and item["price"] >= 1:
                updated = item.get("updated_day", 0)
                economy["prices"][str(good_id)] = {
                    "price": item["price"],
                    "updated_day": updated if _is_int(updated) else 0,
                }
            elif good_id in raw_prices:
                notes.append("world.economy.prices[%s] 形状非法：已剔除"
                             % (good_id,))
    elif "prices" in raw_economy:
        notes.append("world.economy.prices 脏值（%s）：降级为空表"
                     % type(raw_prices).__name__)

    raw_ledger = raw_economy.get("ledger", [])
    if isinstance(raw_ledger, list):
        kept = [entry for entry in raw_ledger if isinstance(entry, dict)]
        if len(kept) != len(raw_ledger):
            notes.append("world.economy.ledger 有 %d 条不是对象：已剔除"
                         % (len(raw_ledger) - len(kept)))
        economy["ledger"] = kept
    elif "ledger" in raw_economy:
        notes.append("world.economy.ledger 脏值（%s）：降级为空表"
                     % type(raw_ledger).__name__)

    raw_balances = raw_economy.get("balances", {})
    if isinstance(raw_balances, dict):
        for account, value in raw_balances.items():
            if _is_int(value) and value >= 0:
                economy["balances"][str(account)] = value
            elif account in raw_balances:
                notes.append("world.economy.balances[%s] 非法（%r）：已剔除"
                             % (account, value))
    elif "balances" in raw_economy:
        notes.append("world.economy.balances 脏值（%s）：降级为零账本"
                     % type(raw_balances).__name__)

    raw_wticks = raw_economy.get("wticks", {})
    if isinstance(raw_wticks, dict):
        for key, item in raw_wticks.items():
            if isinstance(key, str) and IDEMPOTENCY_KEY_RE.match(key) \
                    and isinstance(item, dict):
                economy["wticks"][key] = item
            else:
                notes.append("world.economy.wticks[%r] 形状非法：已剔除"
                             % (key,))
    elif "wticks" in raw_economy:
        notes.append("world.economy.wticks 脏值（%s）：降级为空表"
                     % type(raw_wticks).__name__)

    # genesis 重放基线（新口径 15）：在则逐字保留；缺（v1 早期块）则用当前
    # 快照回填并记 note——只在此处回填一次，此后随迁移保留。
    raw_genesis = raw_economy.get("genesis")
    if isinstance(raw_genesis, dict):
        g_balances = raw_genesis.get("balances", {})
        g_goods = raw_genesis.get("goods", {})
        economy["genesis"] = {
            "balances": {str(a): v for a, v in g_balances.items()
                         if _is_int(v) and v >= 0}
            if isinstance(g_balances, dict) else {},
            "goods": {str(g): v for g, v in g_goods.items()
                      if _is_int(v) and v >= 0}
            if isinstance(g_goods, dict) else {},
        }
    else:
        economy["genesis"] = {
            "balances": dict(economy["balances"]),
            "goods": {good_id: good.get("stock", 0)
                      for good_id, good in sorted(economy["goods"].items())
                      if _is_int(good.get("stock", 0))},
        }
        notes.append("world.economy.genesis 缺失：用当前快照回填重放基线"
                     "（新口径 15）")

    for key, label in (("projects", "projects"),
                       ("settlements", "settlements")):
        raw = block.get(key, {})
        if isinstance(raw, dict):
            migrated[key] = {str(pid): _clone(item)
                             for pid, item in raw.items()
                             if isinstance(item, dict)}
            if len(migrated[key]) != len(raw):
                notes.append("world.%s 有 %d 条不是对象：已剔除"
                             % (label, len(raw) - len(migrated[key])))
        elif key in block:
            notes.append("world.%s 脏值（%s）：降级为空表"
                         % (label, type(raw).__name__))

    raw_population = block.get("population")
    if isinstance(raw_population, dict):
        summaries = raw_population.get("summaries", [])
        if isinstance(summaries, list):
            migrated["population"]["summaries"] = [
                _clone(item) for item in summaries if isinstance(item, dict)]
        elif "summaries" in raw_population:
            notes.append("world.population.summaries 脏值（%s）：降级为空表"
                         % type(summaries).__name__)
    elif "population" in block:
        notes.append("world.population 脏值（%s）：降级为空表"
                     % type(raw_population).__name__)

    raw_facts = block.get("facts", [])
    if isinstance(raw_facts, list):
        migrated["facts"] = [_clone(fact) for fact in raw_facts
                             if isinstance(fact, dict)]
        if len(migrated["facts"]) != len(raw_facts):
            notes.append("world.facts 有 %d 条不是对象：已剔除"
                         % (len(raw_facts) - len(migrated["facts"])))
    elif "facts" in block:
        notes.append("world.facts 脏值（%s）：降级为空表"
                     % type(raw_facts).__name__)

    raw_rejudge = block.get("rejudge")
    if isinstance(raw_rejudge, dict):
        day_value = raw_rejudge.get("day", 0)
        counts = raw_rejudge.get("counts", {})
        migrated["rejudge"] = {
            "day": day_value if _is_int(day_value) and day_value >= 0 else 0,
            "counts": {str(npc): n for npc, n in counts.items()
                       if _is_int(n) and n >= 0}
            if isinstance(counts, dict) else {},
        }
    elif "rejudge" in block:
        notes.append("world.rejudge 脏值（%s）：降级为默认预算"
                     % type(raw_rejudge).__name__)

    return migrated


def prune_block(block, notes=None):
    """§6.1 裁剪（返回**新块**）：`LEDGER_MAX` / `FACTS_MAX` /
    `POPULATION_SUMMARIES_MAX`。

    - 超上限**先裁最旧**（ledger 按 (at_minute, entry_id) 字典序，facts 按
      (minute, fact_id)，summaries 按 (minute, summary_id)），保留最新 N 条；
    - 裁剪是确定性的：同一块连续调用两次结果全等（幂等）；
    - 入参不改写；`notes` 给了就追加说明。
    """
    notes = [] if notes is None else notes
    pruned = migrate_block(block, notes)

    ledger = pruned["economy"]["ledger"]
    if len(ledger) > LEDGER_MAX:
        kept = sorted(ledger, key=lambda e: (_entry_minute(e), _entry_id(e)))[
            -LEDGER_MAX:]
        notes.append("world.economy.ledger 裁剪：%d → %d 条（§12.1 P3）"
                     % (len(ledger), len(kept)))
        pruned["economy"]["ledger"] = kept

    facts = pruned["facts"]
    if len(facts) > FACTS_MAX:
        kept = sorted(facts, key=lambda f: (_fact_minute(f),
                                            _text(f.get("fact_id"))))[
            -FACTS_MAX:]
        notes.append("world.facts 裁剪：%d → %d 条" % (len(facts), len(kept)))
        pruned["facts"] = kept

    summaries = pruned["population"]["summaries"]
    if len(summaries) > POPULATION_SUMMARIES_MAX:
        kept = sorted(summaries, key=lambda s: (_summary_minute(s),
                                                _text(s.get("summary_id"))))[
            -POPULATION_SUMMARIES_MAX:]
        notes.append("world.population.summaries 裁剪：%d → %d 条"
                     % (len(summaries), len(kept)))
        pruned["population"]["summaries"] = kept

    return pruned


def _entry_minute(entry):
    value = entry.get("at_minute")
    return value if _is_int(value) else 0


def _entry_id(entry):
    return _text(entry.get("entry_id"))


def _fact_minute(fact):
    value = fact.get("minute")
    return value if _is_int(value) else 0


def _summary_minute(summary):
    value = summary.get("minute")
    return value if _is_int(value) else 0


# ── Entry 与三等式（§6.2 / §10.2 断言 2.1） ───────────────────────────────

def make_entry(entry_id, *, at_minute, kind, account, delta, reason=None,
               event_seq=0, project_id=None, settlement_id=None,
               extra_refs=None):
    """§6.2 的 `Entry` 形状工厂（确定性；不碰块）。"""
    if kind not in ENTRY_KINDS:
        raise WorldError("Entry.kind 非法：%r" % (kind,))
    if not _is_int(delta):
        raise WorldError("Entry.delta 必须是整数：%r" % (delta,))
    refs = {"event_seq": int(event_seq)}
    if project_id is not None:
        refs["project_id"] = str(project_id)
    if settlement_id is not None:
        refs["settlement_id"] = str(settlement_id)
    if extra_refs:
        refs.update(extra_refs)
    reason = reason if isinstance(reason, dict) else {}
    reason = {
        "verb": _text(reason.get("verb")),
        "actor": _text(reason.get("actor")),
        "target_ref": _text(reason.get("target_ref")),
    }
    return {
        "entry_id": str(entry_id),
        "at_minute": int(at_minute),
        "kind": kind,
        "account": str(account),
        "delta": delta,
        "reason": reason,
        "refs": refs,
    }


def _apply_entry(block, entry):
    """把一条 Entry 落到块上（库存/余额；不为负守卫）。

    `balances` 账户（coin / labor / …）进 `balances`；`<good_id>` 进
    `goods[good_id].stock`。会变负 → `WorldError`（拒单无账）。
    """
    account = entry["account"]
    delta = entry["delta"]
    goods = block["economy"]["goods"]
    if account in goods:
        stock = goods[account].get("stock", 0)
        if not _is_int(stock):
            stock = 0
        if stock + delta < 0:
            raise WorldError("库存不足：%s（%d %+d）" % (account, stock, delta))
        goods[account]["stock"] = stock + delta
        return
    balances = block["economy"]["balances"]
    current = balances.get(account, 0)
    if not _is_int(current):
        current = 0
    if current + delta < 0:
        raise WorldError("余额不足：%s（%d %+d）" % (account, current, delta))
    balances[account] = current + delta


def replay_balances(block):
    """三等式①的重放侧：`genesis 基线 + ledger 重放` 的账户余额与库存。

    返回 `{"balances": {account: total}, "goods": {good_id: total}}`；
    与 `block.economy.balances` / `goods[].stock` 全等即等式①成立
    （replay(genesis, events) 的纯函数落法，新口径 15）。
    """
    genesis = block["economy"].get("genesis", {})
    balances = {str(a): v for a, v in genesis.get("balances", {}).items()
                if _is_int(v)} if isinstance(genesis, dict) else {}
    goods = {str(g): v for g, v in genesis.get("goods", {}).items()
             if _is_int(v)} if isinstance(genesis, dict) else {}
    for entry in block["economy"]["ledger"]:
        if not isinstance(entry, dict):
            continue
        account = str(entry.get("account"))
        delta = entry.get("delta")
        if not _is_int(delta):
            continue
        if account in block["economy"]["goods"]:
            goods[account] = goods.get(account, 0) + delta
        else:
            balances[account] = balances.get(account, 0) + delta
    return {"balances": balances, "goods": goods}


def verify_ledger(block):
    """三等式校验（§6.2），返回 issue 列表（空 = 三条全成立）。

    ① 重放余额 == balances 快照、重放库存 == goods[].stock；
    ② 不重不漏：同一 entry_id 不得出现两次（每条 Entry 恰好应用一次，
       漏账由①的重放合计兜住）；
    ③ 每条 Entry 的 `refs.event_seq` 指向真实事件（纯函数侧校验：键在、值
       为非负整数；「事件真实存在」由编排层提交时保证）。
    """
    issues = []
    replay = replay_balances(block)
    for account, value in sorted(block["economy"]["balances"].items()):
        if replay["balances"].get(account, 0) != value:
            issues.append("等式①失衡：balances[%s]=%d，重放=%d"
                          % (account, value, replay["balances"].get(account, 0)))
    for account, total in sorted(replay["balances"].items()):
        if account not in block["economy"]["balances"] and total != 0:
            issues.append("等式①失衡：重放 balances[%s]=%d 但快照缺账户"
                          % (account, total))
    for good_id, item in sorted(block["economy"]["goods"].items()):
        stock = item.get("stock", 0) if isinstance(item, dict) else 0
        if replay["goods"].get(good_id, 0) != stock:
            issues.append("等式①失衡：goods[%s].stock=%r，重放=%d"
                          % (good_id, stock, replay["goods"].get(good_id, 0)))

    seen = set()
    for entry in block["economy"]["ledger"]:
        entry_id = _entry_id(entry)
        if entry_id in seen:
            issues.append("等式②重账：entry_id=%s 重复出现" % (entry_id,))
        seen.add(entry_id)

    for entry in block["economy"]["ledger"]:
        refs = entry.get("refs")
        seq = refs.get("event_seq") if isinstance(refs, dict) else None
        if not _is_int(seq) or seq < 0:
            issues.append("等式③断链：entry_id=%s 无合法 event_seq"
                          % (_entry_id(entry),))
    return issues


# ── 玩家经济动作（出口 2.2：库存/余额不为负，拒单无账） ──────────────────

def settle_trade(block, *, good_id, quantity, unit_price, at_minute,
                 event_seq, day=0, buyer_account="coin"):
    """玩家买入的结算（§6.4；纯函数）。

    - 库存足够且金库足够 → 扣款 + 扣库存，记两条 `trade` Entry；
    - 不足（买 10 只有 3 只）→ 抛 `WorldError`，**无** Entry、入参块零改动
      （出口 2.2：拒单、无账、无 changes）。
    """
    if not _is_int(quantity) or quantity <= 0:
        raise WorldError("购买数量非法：%r" % (quantity,))
    if not _is_int(unit_price) or unit_price < 0:
        raise WorldError("单价非法：%r" % (unit_price,))
    block = migrate_block(block)
    goods = block["economy"]["goods"]
    if good_id not in goods:
        raise WorldError("未知 good：%r" % (good_id,))
    stock = goods[good_id].get("stock", 0)
    if not _is_int(stock):
        stock = 0
    if stock < quantity:
        raise WorldError("拒单：库存不足（%s 只有 %d 只，要 %d 只）"
                         % (good_id, stock, quantity))
    total = quantity * unit_price
    coin = block["economy"]["balances"].get(buyer_account, 0)
    if not _is_int(coin):
        coin = 0
    if coin < total:
        raise WorldError("拒单：余额不足（%s 有 %d，需 %d）"
                         % (buyer_account, coin, total))

    seq = len(block["economy"]["ledger"])
    pay = make_entry("we-%s-%04d" % (day, seq), at_minute=at_minute,
                     kind="trade", account=str(buyer_account), delta=-total,
                     reason={"verb": "buy", "actor": "player",
                             "target_ref": str(good_id)},
                     event_seq=event_seq,
                     extra_refs={"quantity": quantity, "unit_price": unit_price})
    take = make_entry("we-%s-%04d" % (day, seq + 1), at_minute=at_minute,
                      kind="trade", account=str(good_id), delta=-quantity,
                      reason={"verb": "buy", "actor": "player",
                              "target_ref": str(good_id)},
                      event_seq=event_seq)
    _apply_entry(block, pay)
    _apply_entry(block, take)
    block["economy"]["ledger"].append(pay)
    block["economy"]["ledger"].append(take)
    return prune_block(block), [pay, take]


# ── Project：建造工程对象（§7.1 / §7.2 / §7.3；W6） ───────────────────────
#
# 两条通道（V7）的**汇合点**（§7.3）：无论玩家工程（CHRON 卡）还是背景工程
# （日切推进），开工 / 完工都只走本节的同一组结算函数落账——「一项工程一套
# 账」。Project 状态变更随所属提交落块，不单独开事件（A0）。

def new_project(project_id, *, name, place_id, blueprint, requirements,
                milestones=None, source="player", directive_id="",
                wage_per_day=0, created_minute=0):
    """§7.1 的 Project 形状工厂（确定性；不碰块；形状非法抛 `WorldError`）。

    - `requirements: {goods: {good_id: qty}, coin: int, labor_days: int}`；
    - `milestones` 缺省 = 单里程碑 `[{seq: 1, at_minute: None, label, done}]`，
      `at_minute` 在开工时按 `labor_days / LABOR_EFFICIENCY_PER_DAY` 折算填入
      （§7.1：只做「到期结算」不做逐日施工动画）；
    - `source ∈ {player, directive, npc}`；`source="directive"` 时 `directive_id`
      指回授权指令（§6.5 可解释字段「正在执行的工程」的关联键）；
    - `wage_per_day`：运行期日切从中央金库扣的工资（§7.2 雇佣，W4 既有通道）。
    """
    if not _text(project_id):
        raise WorldError("project_id 必填")
    if not _text(name):
        raise WorldError("name 必填")
    if not _text(place_id):
        raise WorldError("place_id 必填")
    blueprint = blueprint if isinstance(blueprint, dict) else {}
    if not _text(blueprint.get("kind")):
        raise WorldError("blueprint.kind 必填（system.stronghold 设施口径）")
    if source not in PROJECT_SOURCES:
        raise WorldError("source 非法：%r（§7.1）" % (source,))
    req = requirements if isinstance(requirements, dict) else {}
    raw_goods = req.get("goods") if isinstance(req.get("goods"), dict) else {}
    goods_req = {}
    for good_id, qty in raw_goods.items():
        if not _is_int(qty) or qty <= 0:
            raise WorldError("requirements.goods[%r] 必须是正整数：%r"
                             % (good_id, qty))
        goods_req[str(good_id)] = qty
    coin_req = req.get("coin", 0)
    if not _is_int(coin_req) or coin_req < 0:
        raise WorldError("requirements.coin 必须是非负整数：%r" % (coin_req,))
    labor_days = req.get("labor_days", 0)
    if not _is_int(labor_days) or labor_days < 0:
        raise WorldError("requirements.labor_days 必须是非负整数：%r"
                         % (labor_days,))
    if wage_per_day != 0 and (not _is_int(wage_per_day) or wage_per_day < 0):
        raise WorldError("wage_per_day 必须是非负整数：%r" % (wage_per_day,))
    milestone_list = []
    if milestones is not None:
        if not isinstance(milestones, list) or not milestones:
            raise WorldError("milestones 必须是非空数组（§7.1）")
        for seq, item in enumerate(milestones, start=1):
            if not isinstance(item, dict) or not _text(item.get("label")):
                raise WorldError("milestones[%d] 形状非法（需 {label, …}）" % seq)
            milestone_list.append({"seq": seq, "at_minute": None,
                                   "label": str(item["label"]),
                                   "done": False})
    if not milestone_list:
        milestone_list = [{"seq": 1, "at_minute": None, "label": str(name),
                           "done": False}]
    return {
        "project_id": str(project_id),
        "name": str(name),
        "place_id": str(place_id),
        "blueprint": {"kind": str(blueprint.get("kind")),
                      **({"levels": blueprint["levels"]}
                         if _is_int(blueprint.get("levels")) else {})},
        "requirements": {"goods": goods_req, "coin": coin_req,
                         "labor_days": labor_days},
        "milestones": milestone_list,
        "progress": {"materials_in": {}, "labor_done_days": 0,
                     "spend_coin": 0},
        "status": "draft",
        "source": source,
        "directive_id": str(directive_id or ""),
        "wage_per_day": int(wage_per_day),
        "created_minute": int(created_minute),
    }


def _project(block, project_id):
    project = block["projects"].get(str(project_id or ""))
    if not isinstance(project, dict):
        raise WorldError("没有这个工程：%r" % (project_id,))
    return project


def project_create(block, project):
    """把 `new_project` 产物写入块（`status="draft"`；project_id 唯一）。"""
    block = migrate_block(block)
    if not isinstance(project, dict) or not _text(project.get("project_id")):
        raise WorldError("project 形状非法（需带 project_id）")
    pid = str(project["project_id"])
    if pid in block["projects"]:
        raise WorldError("工程已存在：%s" % pid)
    stored = _clone(project)
    stored["status"] = "draft"
    block["projects"][pid] = stored
    return prune_block(block)


def project_queue(block, project_id):
    """draft → queued（排队；§7.2：开工五条件在 queued → running 时判）。"""
    block = migrate_block(block)
    project = _project(block, project_id)
    if project.get("status") != "draft":
        raise WorldError("只有 draft 可排队：%s（%s）"
                         % (project_id, project.get("status")))
    project["status"] = "queued"
    return prune_block(block)


def project_readiness(block, project, *, place=None, coin_available=None,
                      labor_available=None, stock_override=None):
    """开工条件五项（§7.2 逐项），返回缺项列表（空 = 五者齐）。

    - 条件四/五（地点合法 + 校验过的地图）由编排层经 `place` 注入——world.py
      不 import atlas（§9.1 依赖方向）：`place = {place_id, kind, validated}`，
      `kind != "feature"` 且 `validated is True` 才算过；
    - `coin_available` / `labor_available` / `stock_override`：日切序贯结算的
      模拟值（§6.3 义务扣账与指令花费在同一事务里先后生效），缺省读块内实值。
    """
    project = project if isinstance(project, dict) else {}
    req = project.get("requirements") if \
        isinstance(project.get("requirements"), dict) else {}
    goods = block["economy"]["goods"]
    balances = block["economy"]["balances"]
    unmet = []
    # ① 材料足
    stock_override = stock_override if isinstance(stock_override, dict) else {}
    for good_id in sorted(req.get("goods") or {}):
        if good_id in stock_override:
            stock = stock_override[good_id]
        else:
            stock = goods.get(good_id, {}).get("stock", 0)
        if not _is_int(stock) or stock < req["goods"][good_id]:
            unmet.append("materials")
            break
    # ② 资金足（中央金库口径，新口径 9）
    coin = coin_available if coin_available is not None \
        else balances.get("coin", 0)
    if not _is_int(coin) or coin < (req.get("coin", 0) or 0):
        unmet.append("funds")
    # ③ 劳动力足（labor 人日池，§7.2 劳动力三来源的汇账户）
    labor = labor_available if labor_available is not None \
        else balances.get("labor", 0)
    if not _is_int(labor) or labor < (req.get("labor_days", 0) or 0):
        unmet.append("labor")
    # ④⑤ 地点合法 + 校验过的地图（编排层注入，world.py 不碰 atlas）
    if not isinstance(place, dict) or not _text(place.get("place_id")) \
            or str(place.get("kind") or "") == "feature":
        unmet.append("place")
    if not (isinstance(place, dict) and place.get("validated") is True):
        unmet.append("atlas")
    return unmet


def _project_start_changes(block, project, *, now_minute, event_seq, place,
                           sims=None):
    """开工结算（queued → running）的变更生成器：Entry + 状态迁移。

    五条件不齐 → `WorldError`（缺项列表在消息里；**拒单无账**）。`sims` 是
    日切序贯模拟器 `{"coin", "labor", "stock"}`（绝对值），给了就随扣减更新。
    """
    project_id = str(project.get("project_id") or "")
    unmet = project_readiness(block, project, place=place,
                              coin_available=None if sims is None
                              else sims["coin"],
                              labor_available=None if sims is None
                              else sims["labor"],
                              stock_override=None if sims is None
                              else sims["stock"])
    if unmet:
        raise WorldError("开工条件不足（%s）：%s"
                         % (project_id, "、".join(unmet)))
    req = project.get("requirements") or {}
    day = now_minute // MINUTES_PER_DAY
    base = len(block["economy"]["ledger"])
    entries = []
    index = 0

    def add(kind, account, delta, verb):
        nonlocal index
        entries.append(make_entry(
            "", at_minute=now_minute, kind=kind, account=account, delta=delta,
            reason={"verb": verb, "actor": "player", "target_ref": project_id},
            event_seq=event_seq, project_id=project_id,
            extra_refs={"day": day, "slot": index}))
        index += 1

    goods_req = req.get("goods") or {}
    for good_id in sorted(goods_req):
        add("build_material", good_id, -goods_req[good_id], "build")
    coin_req = req.get("coin", 0) or 0
    if coin_req > 0:
        add("build_pay", "coin", -coin_req, "build")
    labor_days = req.get("labor_days", 0) or 0
    if labor_days > 0:
        add("labor_commit", "labor", -labor_days, "build")
    # 状态迁移（§7.1）：needs 挂上 W4 的断供判定；里程碑按工期折算到期。
    project["status"] = "running"
    project["needs"] = dict(goods_req)
    progress = project.setdefault(
        "progress", {"materials_in": {}, "labor_done_days": 0, "spend_coin": 0})
    progress["materials_in"] = dict(goods_req)
    progress["spend_coin"] = coin_req
    duration_days = max(1, -(-labor_days // LABOR_EFFICIENCY_PER_DAY)) \
        if labor_days > 0 else 1
    for milestone in project.get("milestones") or []:
        if milestone.get("at_minute") is None:
            milestone["at_minute"] = now_minute + duration_days * MINUTES_PER_DAY
    if sims is not None:
        sims["coin"] -= coin_req
        sims["labor"] -= labor_days
        for good_id, qty in goods_req.items():
            sims["stock"][good_id] = sims["stock"].get(good_id, 0) - qty
    return entries


def project_start(block, project_id, *, now_minute, event_seq, place=None):
    """queued → running（§7.2：五者齐才开工；拒单无账）。

    返回 `(new_block, entries)`；Entry = 材料（`build_material`）+ 资金
    （`build_pay`）+ 劳动力承诺（`labor_commit`），随调用方的提交落块（A0）。
    """
    block = migrate_block(block)
    project = _project(block, project_id)
    if project.get("status") != "queued":
        raise WorldError("只有 queued 可开工：%s（%s）"
                         % (project_id, project.get("status")))
    entries = _project_start_changes(block, project, now_minute=now_minute,
                                     event_seq=event_seq, place=place)
    day = now_minute // MINUTES_PER_DAY
    base = len(block["economy"]["ledger"])
    for index, entry in enumerate(entries):
        entry["entry_id"] = "we-%d-%04d" % (day, base + index)
    for entry in entries:
        _apply_entry(block, entry)
        block["economy"]["ledger"].append(entry)
    return prune_block(block), entries


def project_pause(block, project_id):
    """running/queued → paused（§6.5：日切跳过——不推进、不发工资）。"""
    block = migrate_block(block)
    project = _project(block, project_id)
    if project.get("status") not in ("queued", "running"):
        raise WorldError("只有 queued/running 可暂停：%s（%s）"
                         % (project_id, project.get("status")))
    project["status"] = "paused"
    return prune_block(block)


def project_resume(block, project_id):
    """paused → queued（复工重新排队；五条件在再开工时重判）。"""
    block = migrate_block(block)
    project = _project(block, project_id)
    if project.get("status") != "paused":
        raise WorldError("只有 paused 可复工：%s（%s）"
                         % (project_id, project.get("status")))
    project["status"] = "queued"
    return prune_block(block)


def project_revoke(block, project_id):
    """撤销（§6.5）：只停后续——**已扣的材料/资金/劳力不退**（已提交不可撤）。"""
    block = migrate_block(block)
    project = _project(block, project_id)
    if project.get("status") in ("completed", "failed", "revoked"):
        raise WorldError("终态工程不可撤销：%s（%s）"
                         % (project_id, project.get("status")))
    project["status"] = "revoked"
    project.pop("needs", None)          # 断供判定随之失效
    return prune_block(block)


def _milestone_settle(block, project, *, now_minute):
    """里程碑到期结算的**原地**变更（world_tick 序贯路径与公开入口共用）。

    running 且末座里程碑 `at_minute <= now_minute` → 里程碑落 done、状态
    completed、设施进 `settlements[].facilities[]`（§7.2 维护扣账的载体）；
    进度对平：`labor_done_days` 收口到 `labor_days`。返回是否完工。
    """
    if project.get("status") != "running":
        return False
    milestones = project.get("milestones") or []
    final = milestones[-1] if milestones else None
    due = final.get("at_minute") if isinstance(final, dict) else None
    if not _is_int(due) or due > now_minute:
        return False
    for milestone in milestones:
        at = milestone.get("at_minute")
        if not _is_int(at) or at <= now_minute:
            milestone["done"] = True
    project["status"] = "completed"
    req = project.get("requirements") or {}
    progress = project.setdefault(
        "progress", {"materials_in": {}, "labor_done_days": 0, "spend_coin": 0})
    done = progress.get("labor_done_days", 0)
    progress["labor_done_days"] = max(
        done if _is_int(done) else 0, req.get("labor_days", 0) or 0)
    _materialize_facility(block, project)
    return True


def project_settle_milestone(block, project_id, *, now_minute, event_seq):
    """里程碑到期结算（§7.1：只做「到期结算」；两条通道汇合的完工入口）。

    running 且末座里程碑到期 → completed + 设施进据点；未到期 / 非 running
    → 原样返回（completed=False）。返回 `(new_block, completed)`。
    """
    block = migrate_block(block)
    project = _project(block, project_id)
    completed = _milestone_settle(block, project, now_minute=now_minute)
    return prune_block(block), completed


def _materialize_facility(block, project):
    """完工设施进据点（§7.2：完成后进入 settlements[].facilities[]）。

    该地点已有据点 → 追加；没有 → 以 `s-<project_id>` 建一个空据点承载
    （population=0，不参与生产/消费）。
    """
    settlements = block["settlements"]
    target = None
    for settlement_id in sorted(settlements):
        record = settlements[settlement_id]
        if isinstance(record, dict) \
                and record.get("place_id") == project.get("place_id"):
            target = record
            break
    if target is None:
        target = settlements.setdefault("s-%s" % project["project_id"],
                                        {"place_id": project.get("place_id"),
                                         "population": 0})
    target.setdefault("facilities", []).append({
        "facility_id": "fac-%s" % project["project_id"],
        "kind": (project.get("blueprint") or {}).get("kind", ""),
        "maintenance_per_day": FACILITY_MAINTENANCE_PER_DAY,
        "maintenance_level": FACILITY_MAINTENANCE_LEVELS,
    })


# ── 指令对象：委托式管理（§6.5；W6） ─────────────────────────────────────

def new_directive(directive_id, *, text, goal, budget=None, priority=0,
                  authorized_scope=(), created_minute=0, created_by="player"):
    """§6.5 的指令对象工厂（确定性；形状非法抛 `WorldError`）。

    - `goal: {metric, op, target, good?}`；`metric ∈ {grain_days, good_stock}`
      （v1 确定性指标；`good_stock` 需 `good` 指 good_id）；
    - `budget: {coin_per_day?, labor_per_day?, cap_total?}`——`cap_total` 是
      自动花费的**硬上界**（钳制发生在提案生成时，§6.5：比事后审计更硬）；
    - `authorized_scope`：授权动词表；v1 唯一自动动词是 `procure`（采买）；
    - 创建即 `active`（§6.5 的「转化」结果；v1 无审阅通道，PR 登记）。
    """
    if not _text(directive_id):
        raise WorldError("directive_id 必填")
    goal = goal if isinstance(goal, dict) else {}
    metric = goal.get("metric")
    if metric not in DIRECTIVE_METRICS:
        raise WorldError("goal.metric 非法：%r（%s）"
                         % (metric, "|".join(DIRECTIVE_METRICS)))
    op = goal.get("op")
    op = DIRECTIVE_OP_ALIASES.get(op, op)
    if op not in DIRECTIVE_COMPARE_OPS:
        raise WorldError("goal.op 非法：%r" % (goal.get("op"),))
    target = goal.get("target")
    if not _is_int(target):
        raise WorldError("goal.target 必须是整数：%r" % (target,))
    if metric == "good_stock" and not _text(goal.get("good")):
        raise WorldError("good_stock 指标需要 goal.good（good_id）")
    budget = budget if isinstance(budget, dict) else {}
    clean_budget = {}
    for key in ("coin_per_day", "labor_per_day", "cap_total"):
        value = budget.get(key)
        if value is None:
            continue
        if not _is_int(value) or value < 0:
            raise WorldError("budget.%s 必须是非负整数：%r" % (key, value))
        clean_budget[key] = value
    scope = [str(verb) for verb in authorized_scope
             if isinstance(verb, str) and verb] \
        if isinstance(authorized_scope, (list, tuple)) else []
    return {
        "directive_id": str(directive_id),
        "text": str(text or ""),
        "goal": {"metric": metric, "op": op, "target": target,
                 **({"good": str(goal["good"])}
                    if _text(goal.get("good")) else {})},
        "budget": clean_budget,
        "priority": priority if _is_int(priority) else 0,
        "status": "active",
        "authorized_scope": scope,
        "created_minute": int(created_minute),
        "created_by": str(created_by or "player"),
        "spent_total": 0,
        "blocked_reason": "",
    }


def directive_add(block, settlement_id, directive):
    """把指令挂到据点（§6.5：指令属据点；settlement 必须已存在）。"""
    block = migrate_block(block)
    settlement = block["settlements"].get(str(settlement_id or ""))
    if not isinstance(settlement, dict):
        raise WorldError("没有这个据点：%r" % (settlement_id,))
    if not isinstance(directive, dict) or not _text(directive.get("directive_id")):
        raise WorldError("directive 形状非法（需带 directive_id）")
    directives = settlement.setdefault("directives", {})
    did = str(directive["directive_id"])
    if did in directives:
        raise WorldError("指令已存在：%s" % did)
    directives[did] = _clone(directive)
    return prune_block(block)


def directive_set_status(block, settlement_id, directive_id, status):
    """暂停 / 恢复 / 撤销（§6.5）：`paused` 日切跳过；`revoked` 只停后续。"""
    if status not in DIRECTIVE_STATUSES:
        raise WorldError("directive.status 非法：%r" % (status,))
    block = migrate_block(block)
    settlement = block["settlements"].get(str(settlement_id or ""))
    directives = settlement.get("directives") if \
        isinstance(settlement, dict) else None
    if not isinstance(directives, dict) or directive_id not in directives:
        raise WorldError("没有这条指令：%r" % (directive_id,))
    directive = directives[directive_id]
    if status == "active":
        directive["blocked_reason"] = ""       # 恢复 = 硬回问已被玩家处置
    directive["status"] = status
    return prune_block(block)


def _directive_metric(block, goal, stock_delta=None):
    """goal 指标的确定性求值；未知指标返回 `None`（跳过并记 note）。

    - `grain_days`：food 类目库存 / 日消耗（人口 × CONSUMPTION_PER_CAPITA）；
      无消费人口时：有粮 = 无界（GRAIN_DAYS_UNBOUNDED），无粮 = 0；
    - `good_stock`：`goal.good` 的库存（含日切序贯模拟增量）。
    """
    metric = goal.get("metric")
    goods = block["economy"]["goods"]
    stock_delta = stock_delta if isinstance(stock_delta, dict) else {}

    def stock_of(good_id):
        stock = goods.get(good_id, {}).get("stock", 0)
        if not _is_int(stock):
            stock = 0
        return stock + stock_delta.get(good_id, 0)

    if metric == "good_stock":
        return stock_of(str(goal.get("good") or ""))
    if metric == "grain_days":
        food_stock = sum(stock_of(good_id) for good_id in sorted(goods)
                         if goods[good_id].get("category") == "food")
        population = 0
        for settlement_id in sorted(block["settlements"]):
            record = block["settlements"][settlement_id]
            if _is_active(record):
                value = record.get("population", 0)
                if _is_int(value) and value > 0:
                    population += value
        daily = population * CONSUMPTION_PER_CAPITA
        if daily <= 0:
            return GRAIN_DAYS_UNBOUNDED if food_stock > 0 else 0
        return food_stock // daily
    return None


def _directive_compare(current, op, target):
    if op not in DIRECTIVE_COMPARE_OPS:
        return False
    if op == "gte":
        return current >= target
    if op == "lte":
        return current <= target
    return current == target


def _procure_target(block, goal, stock_delta):
    """自动采买的标的：`(good_id, price, qty_needed)`；无可执行标的给空值。"""
    metric = goal.get("metric")
    goods = block["economy"]["goods"]
    prices = block["economy"]["prices"]
    if metric == "good_stock":
        candidates = [str(goal.get("good") or "")]
    else:
        candidates = sorted(good_id for good_id in goods
                            if goods[good_id].get("category") == "food")
    for good_id in candidates:
        if good_id not in goods:
            continue
        price = prices.get(good_id, {}).get("price", 0)
        if not _is_int(price) or price < 1:
            continue
        if metric == "good_stock":
            stock = goods[good_id].get("stock", 0)
            if not _is_int(stock):
                stock = 0
            stock += (stock_delta or {}).get(good_id, 0)
            qty_needed = max(0, goal["target"] - stock)
        else:
            current = _directive_metric(block, goal, stock_delta)
            if current is None or current >= GRAIN_DAYS_UNBOUNDED:
                continue
            population = 0
            for settlement_id in sorted(block["settlements"]):
                record = block["settlements"][settlement_id]
                if _is_active(record):
                    value = record.get("population", 0)
                    if _is_int(value) and value > 0:
                        population += value
            daily = population * CONSUMPTION_PER_CAPITA
            qty_needed = max(0, goal["target"] - current) * daily
        return good_id, price, qty_needed
    return "", 0, 0


def directive_explain(block, settlement_id, directive_id):
    """§6.5 可解释六项（§16.5 逐项）：目标 / 预算 / 在建工程 / 阻塞原因 /
    最近决策（ledger 近窗 3 条）/ 预期结果。指令不存在 → `WorldError`。"""
    block = migrate_block(block)
    settlement = block["settlements"].get(str(settlement_id or ""))
    directives = settlement.get("directives") if \
        isinstance(settlement, dict) else None
    if not isinstance(directives, dict) or directive_id not in directives:
        raise WorldError("没有这条指令：%r" % (directive_id,))
    directive = directives[directive_id]
    goal = directive.get("goal") or {}
    projects = sorted(pid for pid, project in block["projects"].items()
                      if isinstance(project, dict)
                      and project.get("directive_id") == directive_id
                      and project.get("status") in ("queued", "running"))
    recent = []
    for entry in reversed(block["economy"]["ledger"]):
        if len(recent) >= 3:
            break
        if isinstance(entry, dict) \
                and (entry.get("refs") or {}).get("directive_id") == directive_id:
            recent.append({"entry_id": entry.get("entry_id"),
                           "kind": entry.get("kind"),
                           "account": entry.get("account"),
                           "delta": entry.get("delta")})
    current = _directive_metric(block, goal)
    budget = directive.get("budget") or {}
    cap = budget.get("cap_total")
    spent = directive.get("spent_total", 0)
    return {
        "directive_id": directive_id,
        "goal": _clone(goal),
        "budget": {"cap_total": cap, "spent_total": spent,
                   "labor_per_day": budget.get("labor_per_day", 0),
                   "remaining": (cap - spent) if _is_int(cap) else None},
        "projects": projects,
        "blocked_reason": _text(directive.get("blocked_reason")),
        "recent_decisions": recent,
        "expected": {"metric": goal.get("metric"), "op": goal.get("op"),
                     "target": goal.get("target"), "current": current,
                     "met": current is not None
                     and _directive_compare(current, goal.get("op"),
                                            goal.get("target"))},
    }


# ── 日切结算（§6.3；幂等键 wtick_<day>） ──────────────────────────────────

def world_tick(block, *, day, now_minute, event_seq, seed=None,
               stopped_at=None, new_facts=None, prism_results=None,
               places=None):
    """日切结算纯函数（§6.3；一天至多一次）。

    返回 `(new_block, entries, notes)`：
    - 幂等：`wtick_<day>` 已在 → 原样返回（同日重放不重复记账）；
    - `day < economy.day`（倒退补账）→ `WorldError`（逐日补齐请逐日调用，
      或走 `catch_up`）；
    - `stopped_at is not None` → 屏障内不日切，原样返回（新口径 10）；
    - `new_facts`：本日随事件落盘的世界事实（M8 结算过的事件），先入
      `facts` 再参与扰动判定；`prism_results`：`{rule_id: bool}`，prism
      模式规则未传结果 = 不触发（新口径 7）；
    - `places`：背景工程自动开工（§7.3）的地点合法性注入
      `{place_id: {place_id, kind, validated}}`（编排层从 atlas 投影；
      缺省 None = 不做自动开工，工程里程碑推进不受影响）。
    """
    notes = []
    block = migrate_block(block, notes)
    if stopped_at is not None:
        notes.append("stopped_at 屏障内：不日切（WORLD-DESIGN §6.3）")
        return block, [], notes
    if not _is_int(day) or day < 0:
        raise WorldError("day 非法：%r" % (day,))
    if not _is_int(now_minute) or now_minute < 0:
        raise WorldError("now_minute 非法：%r" % (now_minute,))

    economy = block["economy"]
    key = wtick_key(day)
    if key in economy["wticks"]:
        notes.append("wtick 幂等命中：%s 已结算，跳过" % (key,))
        return block, [], notes
    if day < economy["day"]:
        raise WorldError("日切不可倒退：economy.day=%d > %d（补账走 catch_up）"
                         % (economy["day"], day))
    if seed is None:
        seed = block.get("seed", 0)

    entries = []

    # 0. 本日新事实入块（先于扰动判定；§8.2 facts 三条写入路之二）。
    for fact in (new_facts or []):
        if isinstance(fact, dict):
            block["facts"].append(_clone(fact))

    # 1. 生产/消费聚合（新口径 5）：逐 good 净差额，正=produce 负=consume。
    #    `stock_sim` 是**绝对值**模拟（实际库存 + 本日已生成未落地 Entry），
    #    供后续步骤的开工五条件 / 指令指标在序贯结算里看到一致快照。
    pressure, stock_sim = _settle_production_consumption(
        block, day, now_minute, event_seq, entries, notes)

    # 2. 工资 / 税 / 维护（§6.3 第 2 步；资金断 → blocked 且无 Entry）。
    #    `coin_sim` 同理：中央金库的序贯模拟值。
    coin_sim = _settle_obligations(block, day, now_minute, event_seq,
                                   entries, notes)

    # 2.5 委托指令评估（§6.5；日切自动行为：劳力池注入 + 缺口采买 +
    #     硬回问清单；cap_total 在提案生成时钳制）。
    coin_sim, stock_sim, labor_sim = _evaluate_directives(
        block, day, now_minute, event_seq, entries, notes,
        coin_sim, stock_sim)

    # 3. 价格步进（§6.3 第 3 步；确定性公式，无随机）。
    _update_prices(block, pressure, day, notes)

    # 4. 工程里程碑推进（§6.3 第 4 步 / §7.3 背景通道）：背景 queued 工程
    #    五条件齐即自动开工；running 工程按日推进进度、到期结算里程碑
    #    （与玩家 CHRON 通道共用同一组结算函数——两条通道汇合点）。
    coin_sim, stock_sim, labor_sim = _advance_projects(
        block, day, now_minute, event_seq, entries, notes,
        coin_sim, stock_sim, labor_sim, places=places)

    # 5. 扰动判定（§7.2 五要素表；事实驱动 + 概率层 + 冷却 + 留痕）。
    entries.extend(_evaluate_disruptions(block, day, now_minute, event_seq,
                                         seed, prism_results, notes))

    # 5. 人口摘要（§8.3：档位不是精确数）。
    _write_population_summaries(block, day, now_minute, notes)

    # 6. entry_id 终编（确定性：落账序号；一次提交一条事件，全日共享
    #    event_seq——A0）。
    base = len(economy["ledger"])
    for index, entry in enumerate(entries):
        entry["entry_id"] = "we-%d-%04d" % (day, base + index)

    # 7. Entry 落地（顺序应用，不为负守卫在 _apply_entry）。
    for entry in entries:
        _apply_entry(block, entry)
        economy["ledger"].append(entry)

    # 8. 收尾：day 推进、幂等键登记、rejudge 跨日归零（§8.4）。
    economy["day"] = day
    economy["wticks"][key] = {"day": day, "at_minute": now_minute}
    if block["rejudge"].get("day") != day:
        block["rejudge"] = {"day": day, "counts": {}}

    return prune_block(block), entries, notes


def _settle_production_consumption(block, day, now_minute, event_seq,
                                   entries, notes):
    """§6.3 第 1 步：生产/消费聚合（新口径 5 的确定性公式）。

    只生成 Entry（entry_id 占位空串，由 `world_tick` 终编）；库存变更由
    `world_tick` 统一落地。返回 `(pressure, stock_sim)`：逐 good 价格压力
    （新口径 6：正=短缺涨，负=过剩跌，0=不动）与**绝对值**库存模拟
    （实际库存 + 本日已生成未落地 Entry）。
    """
    goods = block["economy"]["goods"]
    settlements = block["settlements"]

    production = {good_id: 0 for good_id in goods}
    for good_id, good in sorted(goods.items()):
        produced_by = good.get("produced_by")
        if not isinstance(produced_by, list):
            continue
        for settlement_id in sorted(settlements):
            record = settlements[settlement_id]
            if not _is_active(record):
                continue
            if record.get("place_id") in produced_by:
                production[good_id] += SETTLEMENT_YIELD_PER_DAY

    wanted = {good_id: 0 for good_id in goods}
    for category in ESSENTIAL_CATEGORIES:
        category_goods = sorted(
            good_id for good_id, good in goods.items()
            if good.get("category") == category)
        if not category_goods:
            continue
        total_pop = 0
        for settlement_id in sorted(settlements):
            record = settlements[settlement_id]
            if not _is_active(record):
                continue
            population = record.get("population", 0)
            if _is_int(population) and population > 0:
                total_pop += population
        demand = total_pop * CONSUMPTION_PER_CAPITA
        base, remainder = divmod(demand, len(category_goods))
        for index, good_id in enumerate(category_goods):
            wanted[good_id] += base + (1 if index < remainder else 0)

    available = {}
    for good_id in sorted(goods):
        stock = goods[good_id].get("stock", 0)
        available[good_id] = stock if _is_int(stock) and stock > 0 else 0

    pressure = {}
    consumed_map = {}
    for good_id in sorted(goods):
        produced = production[good_id]
        desired = wanted[good_id]
        supply = available[good_id] + produced
        consumed = min(desired, supply)
        consumed_map[good_id] = consumed
        if desired > supply:
            pressure[good_id] = desired - supply          # 短缺 → 涨
        elif produced > desired:
            pressure[good_id] = -(produced - desired)     # 过剩 → 跌
        else:
            pressure[good_id] = 0                         # 库存缓冲内 → 不动
        if produced > 0:
            entries.append(make_entry(
                "", at_minute=now_minute, kind="produce", account=good_id,
                delta=produced,
                reason={"verb": "produce", "actor": "world",
                        "target_ref": good_id},
                event_seq=event_seq,
                extra_refs={"settlements": _producers(goods[good_id],
                                                      settlements)}))
        if consumed > 0:
            entries.append(make_entry(
                "", at_minute=now_minute, kind="consume", account=good_id,
                delta=-consumed,
                reason={"verb": "consume", "actor": "population",
                        "target_ref": good_id},
                event_seq=event_seq, extra_refs={"desired": desired}))
        if desired > supply:
            notes.append("good %s 日需求 %d 超供给 %d：消费受库存钳制（新口径 5）"
                         % (good_id, desired, supply))
    stock_sim = {good_id: available[good_id] + production[good_id]
                 - consumed_map.get(good_id, 0)
                 for good_id in sorted(goods)}
    return pressure, stock_sim


def _producers(good, settlements):
    """位于该 good `produced_by` 地点的活跃据点 id 列表（字典序）。"""
    produced_by = good.get("produced_by") or []
    if not isinstance(produced_by, list):
        return []
    return sorted(sid for sid in sorted(settlements)
                  if _is_active(settlements[sid])
                  and settlements[sid].get("place_id") in produced_by)


def _facilities_maintenance(record):
    """据点设施的日维护合计（§7.2：完工设施进 facilities[] 后按日扣）。"""
    facilities = record.get("facilities")
    total = 0
    for facility in facilities if isinstance(facilities, list) else ():
        if isinstance(facility, dict) and _is_int(
                facility.get("maintenance_per_day")) \
                and facility["maintenance_per_day"] > 0:
            total += facility["maintenance_per_day"]
    return total


def _degrade_facilities(record, notes, owner_id):
    """维护欠费 → 每座设施降级一格（§7.2：确定性，降到底即停）。"""
    for facility in record.get("facilities") or []:
        if not isinstance(facility, dict):
            continue
        level = facility.get("maintenance_level", 0)
        if _is_int(level) and level > 0:
            facility["maintenance_level"] = level - 1
            notes.append("%s 设施 %s 维护欠费降级：%d → %d（§7.2）"
                         % (owner_id, facility.get("facility_id"),
                            level, level - 1))


def _settle_obligations(block, day, now_minute, event_seq, entries, notes):
    """§6.3 第 2 步：工资/税/维护到期义务（新口径 9：coin 中央金库）。

    用本地 coin 模拟序贯付款（与 `world_tick` 第 7 步的落地顺序一致）做
    资金断判定；发不出 → blocked 且**不生成** Entry（不为负 / 拒单无账）。
    维护费含完工设施（`facilities[].maintenance_per_day`）；维护欠费时设施
    逐座降级一格（确定性，降到底即停）。返回模拟终值 coin（供后续步骤
    继续序贯结算）。
    """
    coin = block["economy"]["balances"].get("coin", 0)
    if not _is_int(coin):
        coin = 0

    def collect(candidates, is_settlement):
        nonlocal coin
        for owner_id in sorted(candidates):
            record = candidates[owner_id]
            if not isinstance(record, dict) or not _is_active(record):
                continue
            for field, kind, verb in (
                    ("wage_per_day", "wage", "pay_wage"),
                    ("maintenance_per_day", "maintenance", "maintain"),
                    ("tax_per_day", "tax", "collect_tax")):
                amount = record.get(field, 0)
                if not _is_int(amount) or amount < 0:
                    amount = 0
                if is_settlement and kind == "maintenance":
                    amount += _facilities_maintenance(record)
                if amount <= 0:
                    continue
                if kind == "tax":
                    coin += amount
                    entries.append(make_entry(
                        "", at_minute=now_minute, kind=kind, account="coin",
                        delta=amount,
                        reason={"verb": verb, "actor": "world",
                                "target_ref": owner_id},
                        event_seq=event_seq,
                        settlement_id=owner_id if is_settlement else None,
                        project_id=None if is_settlement else owner_id))
                    continue
                if coin >= amount:
                    coin -= amount
                    entries.append(make_entry(
                        "", at_minute=now_minute, kind=kind, account="coin",
                        delta=-amount,
                        reason={"verb": verb, "actor": "world",
                                "target_ref": owner_id},
                        event_seq=event_seq,
                        settlement_id=owner_id if is_settlement else None,
                        project_id=None if is_settlement else owner_id))
                else:
                    record["status"] = "blocked"
                    record["blocked_reason"] = "funds"
                    if is_settlement and kind == "maintenance":
                        _degrade_facilities(record, notes, owner_id)
                    notes.append("%s 工资/维护发不出（缺 %d）：资金断 → "
                                 "blocked（无 Entry，§7.2）"
                                 % (owner_id, amount))

    collect(block["settlements"], is_settlement=True)
    collect(block["projects"], is_settlement=False)

    # 结构化判定：工程所需 good 断供 → blocked（§7.2 三类之一，确定性）。
    goods = block["economy"]["goods"]
    for project_id in sorted(block["projects"]):
        project = block["projects"][project_id]
        if not isinstance(project, dict) or not _is_active(project):
            continue
        needs = project.get("needs")
        if not isinstance(needs, dict):
            continue
        for good_id in sorted(needs):
            stock = goods.get(good_id, {}).get("stock", 0)
            if _is_int(stock) and stock <= 0:
                project["status"] = "blocked"
                project["blocked_reason"] = "supply"
                notes.append("工程 %s 断供（%s 库存 0）：blocked（§7.2）"
                             % (project_id, good_id))
                break

    return coin


def _update_prices(block, pressure, day, notes):
    """§6.3 第 3 步：价格按日供需压力移动一步（新口径 6）。"""
    prices = block["economy"]["prices"]
    goods = block["economy"]["goods"]
    for good_id in sorted(pressure):
        net = pressure[good_id]
        current = prices.get(good_id)
        if isinstance(current, dict) and _is_int(current.get("price")) \
                and current["price"] >= 1:
            price = current["price"]
        else:
            base = goods.get(good_id, {}).get("base_price", 1)
            price = base if _is_int(base) and base >= 1 else 1
        if net == 0:
            continue
        step = max(1, int(price * PRICE_STEP_CLAMP))
        new_price = price + step if net > 0 else max(1, price - step)
        if new_price != price:
            prices[good_id] = {"price": new_price, "updated_day": day}
            notes.append("价格步进：%s %d → %d（压力 %+d，day=%d）"
                         % (good_id, price, new_price, net, day))


def _evaluate_directives(block, day, now_minute, event_seq, entries, notes,
                         coin, stock_sim):
    """§6.5：日切指令评估（委托式管理的自动行为；无模型调用）。

    逐据点逐 active 指令：
    1. 劳力池注入：`budget.labor_per_day` > 0 → 按人日价从中央金库雇人入
       `labor` 账户（coin 不够则跳过，不透支）；
    2. 缺口采买：goal 未达成且 `authorized_scope` 授权 `procure` → **在提案
       生成时**按 `cap_total - spent_total` 钳制数量（§6.5：比事后审计更硬）
       ——单笔花费超 `cap_total` 50% 命中硬回问清单 → 指令置 `blocked` +
       `directive_ask` 事实留痕，**绝不先斩后奏**（无任何花费）。
    返回序贯模拟终值 `(coin, stock_sim, labor_sim)`。
    """
    labor_sim = block["economy"]["balances"].get("labor", 0)
    if not _is_int(labor_sim):
        labor_sim = 0

    for settlement_id in sorted(block["settlements"]):
        settlement = block["settlements"][settlement_id]
        if not _is_active(settlement):
            continue
        directives = settlement.get("directives") \
            if isinstance(settlement.get("directives"), dict) else {}
        for directive_id in sorted(directives):
            directive = directives[directive_id]
            if not isinstance(directive, dict) \
                    or directive.get("status") != "active":
                continue
            budget = directive.get("budget") if \
                isinstance(directive.get("budget"), dict) else {}

            # ① 劳力池注入（§7.2 劳动力三来源之三）。
            per_day = budget.get("labor_per_day", 0)
            if _is_int(per_day) and per_day > 0:
                cost = per_day * DIRECTIVE_LABOR_COIN_PER_DAY
                if coin >= cost:
                    coin -= cost
                    labor_sim += per_day
                    directive["spent_total"] = \
                        (directive.get("spent_total", 0) or 0) + cost
                    entries.append(make_entry(
                        "", at_minute=now_minute, kind="wage", account="coin",
                        delta=-cost,
                        reason={"verb": "hire", "actor": "world",
                                "target_ref": directive_id},
                        event_seq=event_seq, settlement_id=settlement_id,
                        extra_refs={"directive_id": directive_id}))
                    entries.append(make_entry(
                        "", at_minute=now_minute, kind="grant",
                        account="labor", delta=per_day,
                        reason={"verb": "hire", "actor": "world",
                                "target_ref": directive_id},
                        event_seq=event_seq, settlement_id=settlement_id,
                        extra_refs={"directive_id": directive_id}))
                else:
                    notes.append("指令 %s 劳力池发不出（缺 %d）：跳过（不透支）"
                                 % (directive_id, cost))

            # ② 缺口评估与采买。
            goal = directive.get("goal") if \
                isinstance(directive.get("goal"), dict) else {}
            current = _directive_metric(block, goal, stock_sim)
            if current is None:
                notes.append("指令 %s 指标不可求值（%r）：跳过"
                             % (directive_id, goal.get("metric")))
                continue
            if _directive_compare(current, goal.get("op"), goal.get("target")):
                continue                                  # 目标已达成
            if DIRECTIVE_PROCURE_VERB not in (directive.get("authorized_scope")
                                              or []):
                continue                                  # 未授权自动采买
            cap = budget.get("cap_total")
            if not _is_int(cap) or cap <= 0:
                notes.append("指令 %s 无 cap_total：不自动花费（§6.5）"
                             % (directive_id,))
                continue
            remaining = cap - (directive.get("spent_total", 0) or 0)
            if remaining <= 0:
                continue                                  # 预算耗尽
            good_id, price, qty_needed = _procure_target(block, goal, stock_sim)
            if not good_id or price < 1 or qty_needed <= 0:
                notes.append("指令 %s 缺口无可执行采买标的：跳过"
                             % (directive_id,))
                continue
            desired = qty_needed * price
            if desired > cap * DIRECTIVE_HARD_ASK_RATIO:
                # 硬回问清单（§6.5）：命中即 blocked + 事实留痕，无自动花费。
                directive["status"] = "blocked"
                directive["blocked_reason"] = "hard_ask_cap_half"
                block["facts"].append({
                    "fact_id": "ask-%s-%04d" % (directive_id, day),
                    "minute": now_minute, "kind": "directive_ask",
                    "subject_ref": directive_id,
                    "text": "单笔 %d 超 cap_total %s 的 50%%，回问玩家"
                            % (desired, cap)})
                notes.append("指令 %s 命中硬回问（单笔 %d > 50%%×%d）："
                             "blocked + 留痕，无花费" % (directive_id, desired,
                                                        cap))
                continue
            qty = min(qty_needed, remaining // price)
            if qty < 1:
                notes.append("指令 %s 剩余预算不足一单位（remaining=%d，"
                             "price=%d）：跳过" % (directive_id, remaining,
                                                  price))
                continue
            spend = qty * price
            coin -= spend
            stock_sim[good_id] = stock_sim.get(good_id, 0) + qty
            directive["spent_total"] = \
                (directive.get("spent_total", 0) or 0) + spend
            entries.append(make_entry(
                "", at_minute=now_minute, kind="trade", account="coin",
                delta=-spend,
                reason={"verb": "procure", "actor": "world",
                        "target_ref": good_id},
                event_seq=event_seq, settlement_id=settlement_id,
                extra_refs={"directive_id": directive_id,
                            "quantity": qty, "unit_price": price}))
            entries.append(make_entry(
                "", at_minute=now_minute, kind="trade", account=good_id,
                delta=qty,
                reason={"verb": "procure", "actor": "world",
                        "target_ref": good_id},
                event_seq=event_seq, settlement_id=settlement_id,
                extra_refs={"directive_id": directive_id,
                            "quantity": qty, "unit_price": price}))
    return coin, stock_sim, labor_sim


def _advance_projects(block, day, now_minute, event_seq, entries, notes,
                      coin, stock_sim, labor_sim, places=None):
    """§6.3 第 4 步：工程里程碑推进（背景通道；与 CHRON 通道共用结算函数）。

    - 背景 queued 工程（source ∈ {directive, npc}）五条件齐即自动开工
      （`places` 缺地点信息时地点条件不满足 → 不开工，记 note）；
    - running 工程按日推进 `labor_done_days`（+LABOR_EFFICIENCY_PER_DAY，
      封顶 labor_days）；末座里程碑到期 → `project_settle_milestone` 完工
      （设施进据点，维护费从下一日起扣）。
    返回序贯模拟终值 `(coin, stock_sim, labor_sim)`。
    """
    places = places if isinstance(places, dict) else {}
    started_now = set()
    for project_id in sorted(block["projects"]):
        project = block["projects"][project_id]
        if not isinstance(project, dict):
            continue
        if project.get("status") == "queued" \
                and project.get("source") in ("directive", "npc"):
            place = places.get(project.get("place_id"))
            sims = {"coin": coin, "labor": labor_sim, "stock": stock_sim}
            try:
                start_entries = _project_start_changes(
                    block, project, now_minute=now_minute,
                    event_seq=event_seq, place=place, sims=sims)
            except WorldError as exc:
                notes.append("背景工程 %s 自动开工未遂：%s" % (project_id, exc))
                continue
            coin = sims["coin"]                      # 序贯模拟继续（int 回读）
            labor_sim = sims["labor"]
            started_now.add(project_id)
            entries.extend(start_entries)
            notes.append("背景工程 %s 自动开工（§7.3 背景通道）" % (project_id,))
        if project.get("status") == "running" and project_id not in started_now:
            # 开工当日的日切不推进进度（次日才开始施工，工期口径一致）。
            req = project.get("requirements") or {}
            labor_days = req.get("labor_days", 0) or 0
            progress = project.setdefault(
                "progress", {"materials_in": {}, "labor_done_days": 0,
                             "spend_coin": 0})
            done = progress.get("labor_done_days", 0)
            if _is_int(done):
                progress["labor_done_days"] = min(
                    labor_days, done + LABOR_EFFICIENCY_PER_DAY)
            _milestone_settle(block, project, now_minute=now_minute)
    return coin, stock_sim, labor_sim


def _matching_facts(block, trigger, now_minute):
    """§7.2 trigger ①：对 facts 投影判定（含新鲜度窗口）。"""
    matches = []
    window = trigger.get("window_days")
    for fact in block["facts"]:
        if not isinstance(fact, dict):
            continue
        if fact.get("kind") != trigger.get("fact_kind"):
            continue
        ref = trigger.get("ref")
        if ref is not None and fact.get("subject_ref") != ref:
            continue
        if window is not None and _is_int(window):
            if not _is_int(fact.get("minute")) \
                    or now_minute - fact["minute"] > window * MINUTES_PER_DAY:
                continue
        matches.append(fact)
    return matches


def _trigger_passes(matches, trigger):
    op = trigger.get("op")
    if op not in TRIGGER_OPS:
        return False
    if op == "exists":
        return len(matches) > 0
    if op == "absent":
        return len(matches) == 0
    threshold = trigger.get("value")
    if not _is_int(threshold) and not isinstance(threshold, float):
        return False
    for fact in matches:
        value = fact.get("value")
        if not _is_int(value) and not isinstance(value, float):
            continue
        if op == "eq" and value == threshold:
            return True
        if op == "gte" and value >= threshold:
            return True
        if op == "lte" and value <= threshold:
            return True
    return False


def _evaluate_disruptions(block, day, now_minute, event_seq, seed,
                          prism_results, notes):
    """§7.2 第 5 步：逐 rule 评估 trigger → 候选 → 概率 → 效果 → 留痕。

    只生成 Entry（entry_id 占位，由 `world_tick` 终编）；工程状态/价格的
    效果直接应用到迁移副本上（确定性）。
    """
    entries = []
    prism_results = prism_results if isinstance(prism_results, dict) else {}
    goods = block["economy"]["goods"]

    for rule in DISRUPTION_RULES:
        rule_id = rule["rule_id"]
        trigger = rule.get("trigger", {})
        matches = _matching_facts(block, trigger, now_minute)
        if not _trigger_passes(matches, trigger):
            continue

        probability = rule.get("probability", {})
        mode = probability.get("mode")
        if mode == "seed":
            params = probability.get("seed", {})
            p = params.get("p", 0)
            roll = _unit_hash(seed, rule_id, day, params.get("salt", ""))
            if not (roll < int(round(p * 1000))):
                continue
        elif mode == "prism":
            if prism_results.get(rule_id) is not True:
                notes.append("扰动 %s：prism 检定结果未提供 → 不触发"
                             "（不开自由骰，新口径 7）" % (rule_id,))
                continue
        else:
            continue

        effect = rule.get("effect", {})
        effect_kind = effect.get("kind")
        params = effect.get("params", {}) if isinstance(effect, dict) else {}
        severity = rule.get("severity") if rule.get("severity") in SEVERITIES \
            else "light"
        fact_ids = sorted(_text(f.get("fact_id")) for f in matches
                          if _text(f.get("fact_id")))
        ref = trigger.get("ref")

        if effect_kind == "good_price_spike":
            # 新口径 14：作用对象 = 匹配事实指认的 good（subject_ref）。
            targets = sorted({str(f.get("subject_ref")) for f in matches
                              if str(f.get("subject_ref")) in goods})
        else:
            # 扰动效果只作用于在场工程（running；W4 夹具值 "active" 兼容，
            # PR 登记）：draft/queued/completed 等不受扰动效果影响。
            targets = sorted(
                pid for pid, project in block["projects"].items()
                if isinstance(project, dict)
                and project.get("status") in DISRUPTIBLE_STATUSES
                and (ref is None or project.get("place_id") == ref))

        triggered_any = False
        for target in targets:
            if effect_kind == "good_price_spike":
                current = block["economy"]["prices"].get(target, {})
                price = current.get("price", 1)
                if not _is_int(price) or price < 1:
                    continue
                new_price = max(1, int(price * params.get("factor", 1)))
                block["economy"]["prices"][target] = {
                    "price": new_price, "updated_day": day}
                triggered_any = True
                entries.append(make_entry(
                    "", at_minute=now_minute, kind="disruption",
                    account="coin", delta=0,
                    reason={"verb": "disrupt", "actor": "world",
                            "target_ref": target},
                    event_seq=event_seq,
                    extra_refs={"fact_ids": fact_ids, "rule_id": rule_id,
                                "severity": severity,
                                "effect": effect_kind,
                                "price_from": price, "price_to": new_price}))
                continue

            project = block["projects"].get(target)
            if not isinstance(project, dict):
                continue
            cooldowns = project.setdefault("cooldowns", {})
            last = cooldowns.get(rule_id)
            cooldown_days = rule.get("cooldown_days", 0)
            if _is_int(last) and day - last < cooldown_days:
                notes.append("扰动 %s → %s：冷却中（%d 日 < %d）"
                             % (rule_id, target, day - last, cooldown_days))
                continue
            cooldowns[rule_id] = day
            _apply_effect(project, effect_kind, params, day, severity)
            triggered_any = True
            entries.append(make_entry(
                "", at_minute=now_minute, kind="disruption", account="coin",
                delta=0,
                reason={"verb": "disrupt", "actor": "world",
                        "target_ref": target},
                event_seq=event_seq, project_id=target,
                extra_refs={"fact_ids": fact_ids, "rule_id": rule_id,
                            "severity": severity, "effect": effect_kind}))

        if triggered_any:
            notes.append("扰动触发：%s（%s，severity=%s，facts=%d）"
                         % (rule_id, effect_kind, severity, len(fact_ids)))
    return entries


def _apply_effect(project, effect_kind, params, day, severity):
    """§7.2 ③：效果公式（确定性应用）。"""
    if effect_kind == "block_project":
        project["status"] = "blocked"
        if severity == "heavy":
            project["blocked_reason"] = "disruption"
    elif effect_kind == "pause_project":
        project["status"] = "paused"
        if severity == "heavy":
            project["blocked_reason"] = "disruption"
    elif effect_kind == "progress_loss":
        ratio = params.get("ratio", 0)
        if not isinstance(ratio, (int, float)):
            return
        progress = project.get("progress")
        if isinstance(progress, dict):
            # §7.1 progress 形状（W6）：吃 labor_done_days。
            done = progress.get("labor_done_days", 0)
            if _is_int(done):
                progress["labor_done_days"] = max(
                    0, int(done * (1.0 - ratio)))
        elif _is_int(progress):
            project["progress"] = max(0, int(progress * (1.0 - ratio)))
    elif effect_kind == "cost_mult":
        project["cost_mult"] = {
            "factor": params.get("factor", 1),
            "until_day": day + int(params.get("days", 0)),
        }


def _write_population_summaries(block, day, now_minute, notes):
    """§8.3：日切对据点人群聚合出档位摘要（headcount_band）。"""
    summaries = block["population"]["summaries"]
    for settlement_id in sorted(block["settlements"]):
        record = block["settlements"][settlement_id]
        if not isinstance(record, dict):
            continue
        population = record.get("population", 0)
        if not _is_int(population) or population < 0:
            population = 0
        summaries.append({
            "summary_id": "popsum-%04d-%s" % (day, settlement_id),
            "place_id": _text(record.get("place_id")),
            "minute": now_minute,
            "headcount_band": _headcount_band(population),
            "note_ref": "",
        })
    if len(summaries) > POPULATION_SUMMARIES_MAX:
        kept = sorted(summaries, key=lambda s: (_summary_minute(s),
                                                _text(s.get("summary_id"))))[
            -POPULATION_SUMMARIES_MAX:]
        notes.append("population.summaries 裁剪：%d → %d 条"
                     % (len(summaries), len(kept)))
        block["population"]["summaries"] = kept


# ── 远场重判预算（§8.4 / P8：按 NPC 计，同日 ≤ REJUDGE_DAILY_CAP） ─────────

def register_rejudge(block, npc_ids, *, day):
    """登记一次远场重判；同 NPC 同日计满 `REJUDGE_DAILY_CAP` 即拒。

    返回 `(new_block, injected)`：injected 是预算内实际注入的 npc_id 列表。
    跨日归零由 `world_tick` 收尾步骤完成（rejudge.day 翻日即清空 counts）。
    """
    block = migrate_block(block)
    if block["rejudge"].get("day") != day:
        block["rejudge"] = {"day": int(day), "counts": {}}
    counts = block["rejudge"]["counts"]
    injected = []
    for npc_id in npc_ids:
        npc_id = str(npc_id)
        count = counts.get(npc_id, 0)
        if not _is_int(count) or count < 0:
            count = 0
        if count >= REJUDGE_DAILY_CAP:
            continue
        counts[npc_id] = count + 1
        injected.append(npc_id)
    return block, injected


# ── 世界事实写入（§8.2：三条路之一——M8 结算过的世界事件） ────────────────

def record_fact(block, *, kind, subject_ref, minute, text="", value=None,
                fact_id=None):
    """写一条 world.fact（确定性 id；调用方控制事实资格，AI 文本不进 facts）。"""
    block = migrate_block(block)
    if not _text(kind):
        raise WorldError("fact.kind 必填")
    if fact_id is None:
        digest = hashlib.sha256(
            ("fact|%s|%s|%d|%s" % (kind, subject_ref, int(minute),
                                   text)).encode("utf-8")).hexdigest()
        fact_id = "fact-%010x" % int(digest[:10], 16)
    fact = {"fact_id": str(fact_id), "minute": int(minute), "kind": str(kind),
            "subject_ref": str(subject_ref), "text": _text(text)}
    if value is not None and (_is_int(value) or isinstance(value, float)):
        fact["value"] = value
    block["facts"].append(fact)
    return prune_block(block), fact


# ── 离线补算（§6.3：catch_up） ────────────────────────────────────────────

def catch_up(block, *, target_day, now_minute, event_seq, seed=None):
    """§6.3 补算：只做「逐日结算 + 推进 economy.day」，不做逐点决策。

    - 欠 ≤ `MAX_CATCHUP_DAYS` 天：逐日 `world_tick`（每天独立幂等键）；
    - 欠 > `MAX_CATCHUP_DAYS` 天：中间日折叠，直接结算末日终值，entries
      `kind="catchup_summary"`（账面守恒，中间日不逐笔留痕；新口径 13）。
    """
    notes = []
    block = migrate_block(block, notes)
    if not _is_int(target_day) or target_day < 0:
        raise WorldError("target_day 非法：%r" % (target_day,))
    if target_day <= block["economy"]["day"]:
        notes.append("catch_up 无欠账：economy.day=%d ≥ target_day=%d"
                     % (block["economy"]["day"], target_day))
        return block, [], notes

    span = target_day - block["economy"]["day"]
    if span > MAX_CATCHUP_DAYS:
        block, entries, tick_notes = world_tick(
            block, day=target_day, now_minute=now_minute, event_seq=event_seq,
            seed=seed)
        for entry in entries:
            if entry["kind"] in ("produce", "consume"):
                entry["kind"] = "catchup_summary"
        notes.append("catch_up 折叠：%d 天 → 末日 %d 一次终值（%d 条 "
                     "catchup_summary）" % (span, target_day, len(entries)))
        notes.extend(tick_notes)
        return block, entries, notes

    all_entries = []
    for day in range(block["economy"]["day"] + 1, target_day + 1):
        block, entries, tick_notes = world_tick(
            block, day=day, now_minute=day * MINUTES_PER_DAY,
            event_seq=event_seq, seed=seed)
        all_entries.extend(entries)
        notes.extend(tick_notes)
    return block, all_entries, notes
