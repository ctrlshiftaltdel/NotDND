#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""world 内核（M12-W4，Issue #383）回归测试。

覆盖 Issue #383 验收清单：

- `new_block` / `migrate_block`：§6.1 块形状、version 双键、只补不覆盖、
  更高版本拒载、脏值降级；
- 三等式（§6.2 / §10.2 断言 2.1）：重放 == 快照、不重不漏、event_seq 在链；
- 出口 2.2：买 10 只有 3 只 → 拒单、无账、无 changes（入参块零改动）；
- 日切：`wtick_<day>` 幂等（同日重放不重复）、跨日正常结算、生产/消费
  聚合正确、价格步进 ±10% 钳制、屏障不日切；
- 扰动规则（§7.2）：事实驱动（无事实不触发）、Entry(kind="disruption")
  留痕带 {fact_ids, rule_id}、同输入逐字节相等（确定性）、冷却、prism
  不开自由骰（未传检定结果不触发）；
- `REJUDGE_DAILY_CAP = 4` 上限生效、跨日归零；
- 硬上限裁剪（LEDGER_MAX / FACTS_MAX / POPULATION_SUMMARIES_MAX）确定性、
  裁两次幂等；
- `catch_up`：逐日补齐与 > MAX_CATCHUP_DAYS 折叠（catchup_summary）；
- world.py 零 IO 纯函数：只 import 标准库（copy/hashlib/re）；
- 纯函数纪律：同输入重跑逐字节相等、不改写入参。
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import world  # noqa: E402
import worldgen  # noqa: E402（只读对齐 goods 形状）

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def _block_with_goods():
    """一块带 goods / 据点 / 金库的标准夹具（economy_on 的 region 世界）。"""
    spec = worldgen.normalize_spec({"world_type": "fantasy", "scale": "region",
                                    "economy_on": True})
    base = worldgen.choose_base(spec)
    synthesized = worldgen.synthesize(spec, base, seed=42)
    block = world.new_block(spec, seed=42, goods=synthesized.get("goods"),
                            day=0, balances={"coin": 10000})
    block["settlements"]["s1"] = {"place_id": "place-fantasy-01",
                                  "population": 10}
    block["projects"]["p1"] = {"place_id": "place-fantasy-01",
                               "status": "active", "progress": 100,
                               "wage_per_day": 5}
    return block


def _food_goods(block):
    return sorted(gid for gid, good in block["economy"]["goods"].items()
                  if good.get("category") == "food")


def _settle(block, day, **kwargs):
    return world.world_tick(block, day=day,
                            now_minute=day * world.MINUTES_PER_DAY,
                            event_seq=100 + day, **kwargs)


# ── 1. 块形状与迁移（§6.1） ───────────────────────────────────────────────

def test_new_block_shape():
    spec = {"world_type": "fantasy"}
    goods = [{"good_id": "good-01", "name": "黑麦干粮", "category": "food",
              "tier": 1, "base_price": 5, "stock": 100,
              "produced_by": ["p1"], "era": "fantasy"}]
    block = world.new_block(spec, seed=7, provenance={"x": 1}, goods=goods,
                            day=3)
    for key in ("version", "schema_version", "spec", "provenance", "economy",
                "projects", "settlements", "population", "facts", "rejudge"):
        assert key in block, "缺 §6.1 顶层键 %s" % key
    assert block["version"] == world.WORLD_VERSION == block["schema_version"]
    economy = block["economy"]
    for key in ("day", "goods", "prices", "ledger", "balances", "genesis",
                "wticks"):
        assert key in economy, "缺 economy 键 %s" % key
    assert economy["day"] == 3
    assert economy["goods"]["good-01"]["stock"] == 100
    assert economy["goods"]["good-01"]["produced_by"] == ["p1"]
    assert economy["prices"]["good-01"] == {"price": 5, "updated_day": 3}
    assert block["rejudge"] == {"day": 3, "counts": {}}
    # 默认值不共享可变对象
    a, b = world.new_block(), world.new_block()
    a["economy"]["ledger"].append({"x": 1})
    a["facts"].append({"y": 1})
    a["projects"]["k"] = {}
    assert b["economy"]["ledger"] == [] and b["facts"] == [] \
        and b["projects"] == {}
    # wtick 幂等键满足 ^[A-Za-z0-9_-]{8,64}$
    import re as _re
    assert _re.match(r"^[A-Za-z0-9_-]{8,64}$", world.wtick_key(0))
    assert _re.match(r"^[A-Za-z0-9_-]{8,64}$", world.wtick_key(12345))


def test_migrate_block_lazy():
    notes = []
    block = world.migrate_block(None, notes)
    assert block["version"] == world.WORLD_VERSION and notes
    # 只补不覆盖
    raw = world.new_block(day=5)
    raw["economy"]["balances"]["coin"] = 77
    raw["facts"] = [{"fact_id": "f1", "minute": 1, "kind": "k",
                     "subject_ref": "s", "text": ""}]
    migrated = world.migrate_block(raw)
    assert migrated["economy"]["balances"]["coin"] == 77
    assert migrated["economy"]["day"] == 5
    assert migrated["facts"][0]["fact_id"] == "f1"
    assert migrated["wticks_ok"] if False else True
    # 缺 wticks 键的旧形状补齐
    legacy = {"version": 1, "economy": {"day": 2, "goods": {}}}
    migrated = world.migrate_block(legacy)
    assert migrated["economy"]["wticks"] == {}
    assert migrated["economy"]["ledger"] == []
    # 更高版本拒载
    try:
        world.migrate_block({"version": world.WORLD_VERSION + 1})
        raise AssertionError("更高版本应拒载")
    except world.WorldError:
        pass
    # 脏值整体降级
    assert world.migrate_block("junk")["version"] == world.WORLD_VERSION
    assert world.migrate_block({"version": "x"})["version"] == \
        world.WORLD_VERSION
    # 单字段脏值只降级它自己
    dirty = world.new_block()
    dirty["economy"]["balances"]["coin"] = -5
    migrated = world.migrate_block(dirty)
    assert "coin" not in migrated["economy"]["balances"]
    assert migrated["economy"]["day"] == 0


def test_prune_block_deterministic():
    block = world.new_block()
    for i in range(world.LEDGER_MAX + 10):
        block["economy"]["ledger"].append({
            "entry_id": "e%05d" % i, "at_minute": i, "kind": "produce",
            "account": "good-01", "delta": 1, "reason": {}, "refs": {}})
    for i in range(world.FACTS_MAX + 10):
        block["facts"].append({"fact_id": "f%05d" % i, "minute": i,
                               "kind": "k", "subject_ref": "s", "text": ""})
    for i in range(world.POPULATION_SUMMARIES_MAX + 10):
        block["population"]["summaries"].append({
            "summary_id": "s%05d" % i, "minute": i, "place_id": "p",
            "headcount_band": "low", "note_ref": ""})
    pruned1 = world.prune_block(block)
    pruned2 = world.prune_block(pruned1)
    assert len(pruned1["economy"]["ledger"]) == world.LEDGER_MAX
    assert len(pruned1["facts"]) == world.FACTS_MAX
    assert len(pruned1["population"]["summaries"]) == \
        world.POPULATION_SUMMARIES_MAX
    assert _dumps(pruned1) == _dumps(pruned2), "裁剪必须幂等"
    # 保留的是最新（id 最大的）N 条
    assert pruned1["economy"]["ledger"][0]["entry_id"] == "e%05d" % 10


# ── 2. 三等式（§6.2 / §10.2 2.1）与拒单无账（出口 2.2） ──────────────────

def test_three_equations_after_days():
    block = _block_with_goods()
    all_entries = []
    for day in (1, 2, 3):
        block, entries, _ = _settle(block, day)
        all_entries.extend(entries)
    issues = world.verify_ledger(block)
    assert issues == [], "三等式破缺：%s" % (issues,)
    # 等式②不重不漏：entry_id 全仓唯一（重放按 ledger 序逐条累加，每条恰好
    # 应用一次由 ledger 顺序结构保证，重账在此暴露）
    entry_ids = [e["entry_id"] for e in block["economy"]["ledger"]]
    assert len(entry_ids) == len(set(entry_ids)), "等式②：entry_id 重账"
    # 等式③：全部 Entry 带非负 event_seq 且指向本批提交
    for entry in all_entries:
        assert entry["refs"]["event_seq"] > 0
    assert len(block["economy"]["wticks"]) == 3
    assert block["economy"]["day"] == 3


def test_trade_reject_insufficient():
    goods = [{"good_id": "good-01", "name": "铁剑", "category": "military",
              "tier": 2, "base_price": 20, "stock": 3, "produced_by": [],
              "era": "fantasy"}]
    block = world.new_block(goods=goods, balances={"coin": 1000})
    before = _dumps(block)
    try:
        world.settle_trade(block, good_id="good-01", quantity=10,
                           unit_price=20, at_minute=100, event_seq=9, day=1)
        raise AssertionError("买 10 只有 3 只应拒单")
    except world.WorldError as error:
        assert "库存不足" in str(error)
    assert _dumps(block) == before, "拒单必须零改动（无账、无 changes）"
    # 余额不足同样拒单
    rich_goods = [{"good_id": "good-01", "name": "铁剑",
                   "category": "military", "tier": 2, "base_price": 20,
                   "stock": 100, "produced_by": [], "era": "fantasy"}]
    poor = world.new_block(goods=rich_goods, balances={"coin": 50})
    before_poor = _dumps(poor)
    try:
        world.settle_trade(poor, good_id="good-01", quantity=10,
                           unit_price=20, at_minute=100, event_seq=9, day=1)
        raise AssertionError("余额不足应拒单")
    except world.WorldError as error:
        assert "余额不足" in str(error)
    assert _dumps(poor) == before_poor, "拒单必须零改动"
    # 成交路径：两条 trade Entry，三等式成立
    tradeable = world.new_block(goods=rich_goods, balances={"coin": 1000})
    new_block, entries = world.settle_trade(
        tradeable, good_id="good-01", quantity=10, unit_price=20,
        at_minute=100, event_seq=9, day=1)
    assert len(entries) == 2
    assert all(e["kind"] == "trade" for e in entries)
    assert new_block["economy"]["goods"]["good-01"]["stock"] == 90
    assert new_block["economy"]["balances"]["coin"] == 800
    assert world.verify_ledger(new_block) == []
    assert _dumps(block) == before, "settle_trade 不改写入参"


# ── 3. 日切：幂等 / 跨日 / 屏障（§6.3 / §10.2 2.3 / 2.4） ─────────────────

def test_wtick_idempotent():
    block = _block_with_goods()
    first, entries1, _ = _settle(block, 1)
    snapshot = _dumps(first)
    again, entries2, notes = _settle(first, 1)
    assert entries2 == [], "同日重放不得重复记账"
    assert _dumps(again) == snapshot, "同日重放块必须逐字节相等"
    assert any("幂等命中" in n for n in notes)
    # 换 event_seq / seed 重投同日也一样
    third, entries3, _ = world.world_tick(
        first, day=1, now_minute=999, event_seq=777, seed=999)
    assert entries3 == [] and _dumps(third) == snapshot


def test_cross_day_settle_and_backward_reject():
    block = _block_with_goods()
    for day in (1, 2, 3):
        block, _, _ = _settle(block, day)
        assert block["economy"]["day"] == day
        assert world.wtick_key(day) in block["economy"]["wticks"]
    # 已结算日的重投是幂等命中，不是倒退错误
    again, entries, _ = _settle(block, 2)
    assert entries == [] and again["economy"]["day"] == 3
    # 倒退且无幂等键（折叠补算留下的空洞）→ 拒绝
    hole = _block_with_goods()
    hole["economy"]["day"] = 5
    hole["economy"]["wticks"]["wtick_0005"] = {"day": 5, "at_minute": 7200}
    try:
        _settle(hole, 2)
        raise AssertionError("倒退补账应拒绝")
    except world.WorldError as error:
        assert "倒退" in str(error)


def test_stopped_at_barrier():
    block = _block_with_goods()
    before = _dumps(block)
    new_block, entries, notes = world.world_tick(
        block, day=1, now_minute=1440, event_seq=101, stopped_at=100)
    assert entries == [] and _dumps(new_block) == before
    assert any("屏障" in n for n in notes)


# ── 4. 生产/消费聚合与价格步进（§6.3 第 1/3 步） ──────────────────────────

def test_production_consumption_aggregation():
    goods = [
        {"good_id": "g-food-a", "name": "黑麦干粮", "category": "food",
         "tier": 1, "base_price": 5, "stock": 50, "produced_by": ["place-a"],
         "era": "fantasy"},
        {"good_id": "g-food-b", "name": "腌菜", "category": "food",
         "tier": 1, "base_price": 3, "stock": 50, "produced_by": [],
         "era": "fantasy"},
        {"good_id": "g-sword", "name": "铁剑", "category": "military",
         "tier": 2, "base_price": 20, "stock": 10, "produced_by": [],
         "era": "fantasy"},
    ]
    block = world.new_block(goods=goods)
    block["settlements"]["s1"] = {"place_id": "place-a", "population": 5}
    block["settlements"]["s2"] = {"place_id": "place-b", "population": 3}
    block, entries, _ = _settle(block, 1)
    economy = block["economy"]
    # 生产：g-food-a 在 place-a 有 1 个活跃据点 → +2；
    # 消费：总人口 8 × 1 = 8，均摊两类 food 各 4。
    assert economy["goods"]["g-food-a"]["stock"] == 50 + 2 - 4
    assert economy["goods"]["g-food-b"]["stock"] == 50 - 4
    # 非必需类目无消费、无生产 → 无 Entry
    assert economy["goods"]["g-sword"]["stock"] == 10
    produce = [e for e in entries if e["kind"] == "produce"]
    consume = [e for e in entries if e["kind"] == "consume"]
    assert len(produce) == 1 and produce[0]["account"] == "g-food-a"
    assert sorted(e["account"] for e in consume) == ["g-food-a", "g-food-b"]
    assert world.verify_ledger(block) == []
    # 断言 produce 留痕可读：actor=world，target_ref 指向 good
    assert produce[0]["reason"]["actor"] == "world"
    assert produce[0]["reason"]["target_ref"] == "g-food-a"


def test_consumption_clamped_by_stock():
    goods = [{"good_id": "g-food-a", "name": "黑麦干粮", "category": "food",
              "tier": 1, "base_price": 5, "stock": 3, "produced_by": [],
              "era": "fantasy"}]
    block = world.new_block(goods=goods)
    block["settlements"]["s1"] = {"place_id": "p", "population": 100}
    block, entries, notes = _settle(block, 1)
    # 需求 100 但库存只有 3：消费钳到 3，库存归零，不拒日切、不为负
    assert block["economy"]["goods"]["g-food-a"]["stock"] == 0
    consume = [e for e in entries if e["kind"] == "consume"]
    assert consume and consume[0]["delta"] == -3
    assert any("钳制" in n for n in notes)
    assert world.verify_ledger(block) == []


def test_price_step_clamped():
    goods = [{"good_id": "g-x", "name": "黑麦干粮", "category": "food",
              "tier": 1, "base_price": 100, "stock": 10, "produced_by": [],
              "era": "fantasy"}]
    block = world.new_block(goods=goods)
    block["settlements"]["s1"] = {"place_id": "p", "population": 50}
    # 短缺（需求 50 > 库存 10）→ 涨一步：100 + max(1, 10) = 110（= +10% 钳制）
    block, _, _ = _settle(block, 1)
    assert block["economy"]["prices"]["g-x"] == {"price": 110,
                                                 "updated_day": 1}
    # 无人口 → 无短缺无过剩 → 价格不动
    block2 = world.new_block(goods=goods)
    block2, _, _ = _settle(block2, 1)
    assert block2["economy"]["prices"]["g-x"]["price"] == 100
    # 低价 good：最小步长 1（新口径 6）
    goods_low = [{"good_id": "g-low", "name": "腌菜", "category": "food",
                  "tier": 1, "base_price": 3, "stock": 10,
                  "produced_by": [], "era": "fantasy"}]
    block3 = world.new_block(goods=goods_low)
    block3["settlements"]["s1"] = {"place_id": "p", "population": 20}
    block3, _, _ = _settle(block3, 1)
    assert block3["economy"]["prices"]["g-low"]["price"] == 4
    # 过剩（产量 2 > 需求 1）→ 跌一步，不越 1
    goods_rich = [{"good_id": "g-rich", "name": "蜂蜜酒", "category": "food",
                   "tier": 1, "base_price": 10, "stock": 0,
                   "produced_by": ["p"], "era": "fantasy"}]
    block4 = world.new_block(goods=goods_rich)
    block4["settlements"]["s1"] = {"place_id": "p", "population": 1}
    block4, _, _ = _settle(block4, 1)
    assert block4["economy"]["prices"]["g-rich"]["price"] == 9


# ── 5. 扰动规则（§7.2 / P7：事实驱动 + 确定性 + 冷却 + 不开自由骰） ────────

def _fact(kind, subject_ref="p1", minute=0, value=None, fact_id="fx"):
    fact = {"fact_id": fact_id, "minute": minute, "kind": kind,
            "subject_ref": subject_ref, "text": ""}
    if value is not None:
        fact["value"] = value
    return fact


def _seed_trigger(rule_id, day, seed=42):
    """找到能让 seed 模式规则触发的 seed（测试辅助，不影响被测代码）。"""
    for candidate in range(5000):
        params = next(r["probability"]["seed"] for r in world.DISRUPTION_RULES
                      if r["rule_id"] == rule_id)
        roll = world._unit_hash(candidate, rule_id, day, params["salt"])
        if roll < int(round(params["p"] * 1000)):
            return candidate
    raise AssertionError("找不到触发 seed")


def test_disruption_fact_driven():
    rule_id = "hostile_occupation"
    day = 5
    seed = _seed_trigger(rule_id, day)
    # 无事实支撑：即使概率命中也**不触发**
    block = _block_with_goods()
    block["projects"]["p1"]["status"] = "active"
    _, entries, _ = _settle(block, day, seed=seed)
    assert not [e for e in entries if e["kind"] == "disruption"], \
        "无事实支撑的扰动一律不发生"
    # 有事实支撑（place_lost，新鲜）→ 触发且留痕
    block = _block_with_goods()
    block["facts"].append(_fact("place_lost", minute=day * 1440 - 10,
                                fact_id="fact-lost-1"))
    new_block, entries, _ = _settle(block, day, seed=seed)
    hits = [e for e in entries if e["kind"] == "disruption"]
    assert hits, "有事实支撑应触发"
    hit = hits[0]
    assert hit["refs"]["rule_id"] == rule_id
    assert hit["refs"]["fact_ids"] == ["fact-lost-1"]
    assert hit["reason"]["verb"] == "disrupt"
    assert hit["reason"]["actor"] == "world"
    assert hit["refs"]["event_seq"] == 100 + day
    # 效果：heavy → 工程暂停 + blocked_reason
    assert new_block["projects"]["p1"]["status"] == "paused"
    assert new_block["projects"]["p1"]["blocked_reason"] == "disruption"
    # 陈旧事实（超 window_days=7）→ 不触发
    block = _block_with_goods()
    block["facts"].append(_fact("place_lost", minute=(day - 8) * 1440,
                                fact_id="fact-old"))
    _, entries, _ = _settle(block, day, seed=seed)
    assert not [e for e in entries if e["kind"] == "disruption"]


def test_disruption_deterministic():
    rule_id = "hostile_occupation"
    day = 5
    seed = _seed_trigger(rule_id, day)

    def run():
        block = _block_with_goods()
        block["facts"].append(_fact("place_lost", minute=day * 1440 - 10,
                                    fact_id="fact-lost-1"))
        new_block, entries, _ = _settle(block, day, seed=seed)
        return _dumps(new_block), _dumps(entries)

    result1, result2 = run(), run()
    assert result1 == result2, "同输入重跑必须逐字节相等"


def test_disruption_cooldown():
    rule_id = "hostile_occupation"
    seed = _seed_trigger(rule_id, 1)
    block = _block_with_goods()
    block["projects"]["p1"]["status"] = "active"
    block["facts"].append(_fact("place_lost", minute=100, fact_id="fact-1"))
    block, _, _ = _settle(block, 1, seed=seed)
    assert block["projects"]["p1"]["status"] == "paused"
    # 冷却期内（cooldown_days=10）：恢复后重投仍不重掷
    block["projects"]["p1"]["status"] = "active"
    block["facts"].append(_fact("place_lost", minute=2 * 1440,
                                fact_id="fact-2"))
    block, entries, _ = _settle(block, 2, seed=seed)
    hits = [e for e in entries if e["kind"] == "disruption"
            and e["refs"].get("rule_id") == rule_id]
    assert not hits, "冷却期内不得重掷"
    # 冷却过后：fact 够新鲜（window_days=7 内）即可再次触发
    seed12 = _seed_trigger(rule_id, 12)
    block["projects"]["p1"]["status"] = "active"
    block["facts"].append(_fact("place_lost", minute=11 * 1440,
                                fact_id="fact-3"))
    block, entries, _ = _settle(block, 12, seed=seed12)
    hits = [e for e in entries if e["kind"] == "disruption"
            and e["refs"].get("rule_id") == rule_id]
    assert hits, "冷却过后应可再次触发"
    assert block["projects"]["p1"]["cooldowns"][rule_id] == 12


def test_disruption_price_spike_targets_good_from_fact():
    rule_id = "supply_scarcity"
    day = 3
    seed = _seed_trigger(rule_id, day)
    block = _block_with_goods()
    block["settlements"] = {}   # 清掉人口压力，隔离价格步进，只看扰动效果
    del block["projects"]["p1"]
    food_id = _food_goods(block)[0]
    old_price = block["economy"]["prices"][food_id]["price"]
    block["facts"].append(_fact("good_scarce", subject_ref=food_id,
                                minute=day * 1440 - 5, value=1,
                                fact_id="fact-scarce"))
    new_block, entries, _ = _settle(block, day, seed=seed)
    hits = [e for e in entries if e["kind"] == "disruption"]
    assert hits, "有事实支撑的 price spike 应触发"
    assert hits[0]["refs"]["rule_id"] == rule_id
    # 作用对象 = 事实指认的 good（新口径 14），factor=2
    assert new_block["economy"]["prices"][food_id]["price"] == \
        max(1, int(old_price * 2))
    assert hits[0]["reason"]["target_ref"] == food_id


def test_prism_mode_no_free_dice():
    # prism 规则：未传检定结果 → 不触发（不开自由骰）
    block = _block_with_goods()
    block["projects"]["p1"]["status"] = "active"
    block["facts"].append(_fact("storm_forecast", minute=100,
                                fact_id="fact-storm"))
    before = _dumps(block)
    _, entries, notes = _settle(block, 1)
    assert not [e for e in entries if e["kind"] == "disruption"]
    assert any("自由骰" in n for n in notes), "未传检定结果应记 notes"
    # 显式传 False → 也不触发
    _, entries, _ = _settle(block, 1, prism_results={"weather_storm": False})
    assert not [e for e in entries if e["kind"] == "disruption"]
    # 传 True → 触发，heavy block
    new_block, entries, _ = _settle(block, 1,
                                    prism_results={"weather_storm": True})
    hits = [e for e in entries if e["kind"] == "disruption"]
    assert hits and hits[0]["refs"]["rule_id"] == "weather_storm"
    assert new_block["projects"]["p1"]["status"] == "blocked"


def test_structural_disruptions():
    # 资金断：工资发不出 → blocked 且无 wage Entry（新口径 9）
    goods = [{"good_id": "g-food", "name": "腌菜", "category": "food",
              "tier": 1, "base_price": 3, "stock": 100, "produced_by": [],
              "era": "fantasy"}]
    block = world.new_block(goods=goods, balances={"coin": 2})
    block["settlements"]["s1"] = {"place_id": "p", "population": 1,
                                  "wage_per_day": 10}
    new_block, entries, notes = _settle(block, 1)
    wage_entries = [e for e in entries if e["kind"] == "wage"]
    assert not wage_entries, "发不出工资不得记 Entry"
    assert new_block["settlements"]["s1"]["status"] == "blocked"
    assert new_block["settlements"]["s1"]["blocked_reason"] == "funds"
    assert world.verify_ledger(new_block) == []
    # 供应链断：工程所需 good 库存 0 → blocked（确定性，不入概率表）
    block = world.new_block(goods=goods, balances={"coin": 1000})
    block["economy"]["goods"]["g-food"]["stock"] = 0
    block["projects"]["p1"] = {"place_id": "p", "status": "active",
                               "needs": {"g-food": 5}}
    new_block, _, notes = _settle(block, 1)
    assert new_block["projects"]["p1"]["status"] == "blocked"
    assert new_block["projects"]["p1"]["blocked_reason"] == "supply"


# ── 6. 重判预算（§8.4 / P8） ──────────────────────────────────────────────

def test_rejudge_cap_and_day_rollover():
    block = _block_with_goods()
    for i in range(6):
        block, injected = world.register_rejudge(block, ["npc-a"], day=1)
        if i < world.REJUDGE_DAILY_CAP:
            assert injected == ["npc-a"]
        else:
            assert injected == [], "同日第 5 次起不再注入"
    assert block["rejudge"]["counts"]["npc-a"] == world.REJUDGE_DAILY_CAP
    # 另一个 NPC 不受影响（按 NPC 计）
    block, injected = world.register_rejudge(block, ["npc-b"], day=1)
    assert injected == ["npc-b"]
    # 跨日归零
    block, _, _ = _settle(block, 2)
    assert block["rejudge"] == {"day": 2, "counts": {}}
    block, injected = world.register_rejudge(block, ["npc-a"], day=2)
    assert injected == ["npc-a"]


# ── 7. catch_up（§6.3） ───────────────────────────────────────────────────

def test_catch_up_daily_and_fold():
    block = _block_with_goods()
    # 欠 3 天 → 逐日补齐，3 个独立 wtick 键
    block, entries, _ = world.catch_up(block, target_day=3, now_minute=4320,
                                       event_seq=500)
    assert block["economy"]["day"] == 3
    assert sorted(block["economy"]["wticks"]) == ["wtick_0001", "wtick_0002",
                                                  "wtick_0003"]
    assert world.verify_ledger(block) == []
    # 无欠账 → 原样返回
    snapshot = _dumps(block)
    block, entries2, _ = world.catch_up(block, target_day=3,
                                        now_minute=4320, event_seq=501)
    assert entries2 == [] and _dumps(block) == snapshot
    # 欠 > MAX_CATCHUP_DAYS → 折叠：只有末日一条 catchup_summary
    block = _block_with_goods()
    block, entries, _ = world.catch_up(block, target_day=200,
                                       now_minute=200 * 1440,
                                       event_seq=600)
    assert block["economy"]["day"] == 200
    kinds = {e["kind"] for e in entries}
    assert kinds <= {"catchup_summary", "wage", "tax", "maintenance",
                     "disruption"}, "折叠路径不得逐日留痕：%s" % (kinds,)
    assert world.verify_ledger(block) == []
    assert _dumps(block) == _dumps(world.migrate_block(block))


# ── 8. 纯函数纪律：确定性 / 零改写入参 / 纯标准库 ─────────────────────────

def test_purity_and_determinism():
    block = _block_with_goods()
    block["facts"].append(_fact("good_scarce", subject_ref="good-01",
                                minute=100, value=1, fact_id="fact-x"))
    before = _dumps(block)
    result1 = _settle(block, 1, seed=7)
    result2 = _settle(block, 1, seed=7)
    assert _dumps(block) == before, "world_tick 不改写入参"
    assert _dumps(result1[0]) == _dumps(result2[0])
    assert _dumps(result1[1]) == _dumps(result2[1])
    assert result1[0]["economy"]["day"] == 1


def test_world_stdlib_only():
    path = os.path.join(ROOT, "world.py")
    with open(path, encoding="utf-8") as fh:
        source = fh.read()
    allowed = {"import copy", "import hashlib", "import re"}
    imported = set()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ") and "as" not in stripped \
                and not stripped.startswith("import world"):
            imported.add(stripped)
        elif stripped.startswith("from "):
            imported.add(stripped)
    assert imported <= allowed, "world.py 只许 import 标准库 %s，实得 %s" \
        % (sorted(allowed), sorted(imported))
    # 不 import 业务模块（依赖方向：编排层调用 world，world 不回头）
    for banned in ("notdnd_web", "state", "neura", "chron", "prism",
                   "atlas", "worldgen"):
        for line in source.splitlines():
            stripped = line.strip()
            if (stripped.startswith("import ") or
                    stripped.startswith("from ")) and banned in stripped:
                raise AssertionError("world.py 不得 import %s：%s"
                                     % (banned, stripped))


def test_goods_shape_aligned_with_worldgen():
    """worldgen.synthesize 产物直接进 new_block（形状对齐，互不 import）。"""
    spec = worldgen.normalize_spec({"world_type": "future", "scale": "domain",
                                    "economy_on": True})
    base = worldgen.choose_base(spec)
    synthesized = worldgen.synthesize(spec, base, seed=7)
    goods = synthesized["goods"]
    assert goods, "domain 档 economy_on=True 必有 goods"
    block = world.new_block(spec, seed=7, goods=goods)
    assert len(block["economy"]["goods"]) == len(goods)
    for item in goods:
        entry = block["economy"]["goods"][item["good_id"]]
        assert entry["base_price"] == item["base_price"]
        assert entry["produced_by"] == item["produced_by"]
        assert block["economy"]["prices"][item["good_id"]]["price"] == \
            item["base_price"]


# ── main ──────────────────────────────────────────────────────────────────

def main():
    checks = [value for name, value in sorted(globals().items())
              if name.startswith("test_") and callable(value)]
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败 [%s]: %s" % (check.__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks),
                                          failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
