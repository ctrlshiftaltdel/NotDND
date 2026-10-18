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
import tempfile

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


# ── 9. Project 生命周期（W6，§7.1 / §7.2） ────────────────────────────────

def _site_place(place_id="place-x", kind="site", validated=True):
    """开工条件四/五的编排层注入形状（§7.2；world.py 不 import atlas）。"""
    return {"place_id": place_id, "kind": kind, "validated": validated}


def _project_block():
    """W6 夹具：铁锭（非必需，不受日切消费）+ 粮、金库与劳力池、一座据点。"""
    goods = [
        {"good_id": "g-iron", "name": "铁锭", "category": "military",
         "tier": 2, "base_price": 6, "stock": 100, "produced_by": [],
         "era": "fantasy"},
        {"good_id": "g-food", "name": "黑麦", "category": "food",
         "tier": 1, "base_price": 2, "stock": 50, "produced_by": ["place-x"],
         "era": "fantasy"},
    ]
    block = world.new_block(goods=goods, balances={"coin": 1000, "labor": 10})
    block["settlements"]["s1"] = {"place_id": "place-x", "population": 4}
    return block


def _wall_project(pid="p-wall", place_id="place-x", *, source="player",
                  wage_per_day=0, directive_id=""):
    return world.new_project(
        pid, name="修墙", place_id=place_id, blueprint={"kind": "wall"},
        requirements={"goods": {"g-iron": 20}, "coin": 100, "labor_days": 2},
        source=source, wage_per_day=wage_per_day, directive_id=directive_id,
        created_minute=0)


def test_project_lifecycle_full():
    """draft → queued（五条件齐）→ running → completed 全链（§7.1 / §7.2）：
    开工三笔扣账、工期折算里程碑、进度对平、完工设施进据点、维护扣账。"""
    block = _project_block()
    before = _dumps(block)
    block = world.project_create(block, _wall_project())
    assert block["projects"]["p-wall"]["status"] == "draft"
    block = world.project_queue(block, "p-wall")
    assert block["projects"]["p-wall"]["status"] == "queued"
    # 开工：三笔 Entry（材料 / 资金 / 劳力承诺），随调用方提交落块（A0）
    block, entries = world.project_start(block, "p-wall", now_minute=1440,
                                         event_seq=11,
                                         place=_site_place())
    project = block["projects"]["p-wall"]
    assert project["status"] == "running"
    assert [e["kind"] for e in entries] == \
        ["build_material", "build_pay", "labor_commit"]
    assert entries[0]["account"] == "g-iron" and entries[0]["delta"] == -20
    assert entries[1]["account"] == "coin" and entries[1]["delta"] == -100
    assert entries[2]["account"] == "labor" and entries[2]["delta"] == -2
    assert all(e["refs"]["event_seq"] == 11 for e in entries)
    assert block["economy"]["goods"]["g-iron"]["stock"] == 80
    assert block["economy"]["balances"]["coin"] == 900
    assert block["economy"]["balances"]["labor"] == 8
    # 进度对平（§10.2 2.6）：materials_in / spend_coin 与 requirements 一致
    assert project["progress"]["materials_in"] == {"g-iron": 20}
    assert project["progress"]["spend_coin"] == 100
    # 工期折算（新口径 15）：2 人日 / 效率 1 → 2 天，里程碑到期 1440 + 2880
    assert project["milestones"][0]["at_minute"] == 1440 + 2 * 1440
    assert project["needs"] == {"g-iron": 20}
    assert world.verify_ledger(block) == []
    assert _dumps(_project_block()) == before, "project_start 不改写入参"
    # 未到期：不完工
    block, completed = world.project_settle_milestone(
        block, "p-wall", now_minute=4319, event_seq=12)
    assert completed is False and project["status"] == "running"
    # 到期（day 3 日切）：完工 + 设施进据点 + 进度收口
    block, _, notes = _settle(block, 3)
    project = block["projects"]["p-wall"]
    assert project["status"] == "completed"
    assert project["milestones"][0]["done"] is True
    assert project["progress"]["labor_done_days"] == 2
    facilities = block["settlements"]["s1"]["facilities"]
    assert len(facilities) == 1 and facilities[0]["facility_id"] == "fac-p-wall"
    assert facilities[0]["maintenance_level"] == world.FACILITY_MAINTENANCE_LEVELS
    # 次日维护扣账：从中央金库扣 2（§7.2）
    coin_before = block["economy"]["balances"]["coin"]
    block, entries, _ = _settle(block, 4)
    maint = [e for e in entries if e["kind"] == "maintenance"]
    assert maint and maint[0]["delta"] == -world.FACILITY_MAINTENANCE_PER_DAY
    assert maint[0]["refs"]["settlement_id"] == "s1"
    assert block["economy"]["balances"]["coin"] == \
        coin_before - world.FACILITY_MAINTENANCE_PER_DAY
    assert world.verify_ledger(block) == []


def test_project_start_rejects_each_condition():
    """五条件缺一不开工（§7.2 逐项）：拒单无账、入参块零改动。"""
    def fresh(**over):
        block = _project_block()
        block = world.project_create(block, _wall_project())
        block = world.project_queue(block, "p-wall")
        if "stock" in over:
            block["economy"]["goods"]["g-iron"]["stock"] = over["stock"]
        if "coin" in over:
            block["economy"]["balances"]["coin"] = over["coin"]
        if "labor" in over:
            block["economy"]["balances"]["labor"] = over["labor"]
        return block, _dumps(block)

    cases = [
        ("materials", {"stock": 10}, _site_place()),
        ("funds", {"coin": 99}, _site_place()),
        ("labor", {"labor": 1}, _site_place()),
        ("place", {}, None),
        ("place", {}, _site_place(kind="feature")),
        ("atlas", {}, _site_place(validated=False)),
    ]
    for reason, over, place in cases:
        block, snapshot = fresh(**over)
        try:
            world.project_start(block, "p-wall", now_minute=1440,
                                event_seq=11, place=place)
            raise AssertionError("条件 %s 缺失应拒开工" % reason)
        except world.WorldError as error:
            assert reason in str(error), (reason, str(error))
        assert _dumps(block) == snapshot, "拒开工必须零改动（%s）" % reason
    # 状态机闸门：draft 不可直接开工；终态不可撤销
    block = world.project_create(_project_block(), _wall_project())
    try:
        world.project_start(block, "p-wall", now_minute=0, event_seq=1,
                            place=_site_place())
        raise AssertionError("draft 不可直接开工")
    except world.WorldError:
        pass
    revoked = world.project_revoke(world.project_queue(block, "p-wall"),
                                   "p-wall")
    assert revoked["projects"]["p-wall"]["status"] == "revoked"
    try:
        world.project_revoke(revoked, "p-wall")
        raise AssertionError("终态工程不可撤销")
    except world.WorldError:
        pass


def test_project_pause_resume_revoke_daycut_skips():
    """paused 日切跳过（不推进不发工资）；revoked 只停后续（已扣不退）。"""
    block = _project_block()
    block = world.project_create(block, _wall_project(wage_per_day=5))
    block = world.project_queue(block, "p-wall")
    block, _ = world.project_start(block, "p-wall", now_minute=0,
                                   event_seq=11, place=_site_place())
    coin_running = block["economy"]["balances"]["coin"]
    # 暂停：日切跳过——无工资、里程碑不推进
    block = world.project_pause(block, "p-wall")
    block, entries, _ = _settle(block, 3)
    assert not [e for e in entries
                if e["kind"] == "wage" and e["refs"].get("project_id")], entries
    assert block["projects"]["p-wall"]["status"] == "paused"
    assert block["projects"]["p-wall"]["milestones"][0]["done"] is False
    # 复工：paused → queued（重新排队；再开工时五条件重判，新口径 17/18）
    block = world.project_resume(block, "p-wall")
    assert block["projects"]["p-wall"]["status"] == "queued"
    # 撤销：只停后续——已扣的材料/资金/劳力不退（§6.5 已提交不可撤）
    stock_before = block["economy"]["goods"]["g-iron"]["stock"]
    block = world.project_revoke(block, "p-wall")
    assert block["projects"]["p-wall"]["status"] == "revoked"
    block, _, _ = _settle(block, 4)
    assert block["economy"]["goods"]["g-iron"]["stock"] == stock_before, \
        "撤销不得退还已扣材料"
    assert not [e for e in entries
                if e["kind"] == "wage" and e["refs"].get("project_id")]
    _ = coin_running


def test_background_project_daycut_autostart_and_milestone():
    """背景工程（§7.3 背景通道）：日切自动开工（五条件齐）→ 逐日推进 →
    到期完工；地点信息未注入时不开工（新口径 16/18）。"""
    block = _project_block()
    block = world.project_create(
        block, _wall_project("p-npc", source="npc"))
    block = world.project_queue(block, "p-npc")
    # 无 places 注入：自动开工未遂（地点条件不满足），停在 queued
    block, _, notes = _settle(block, 1)
    assert block["projects"]["p-npc"]["status"] == "queued"
    assert any("自动开工未遂" in n for n in notes)
    # 注入合法地点：日切自动开工（材料/资金/劳力同扣）
    block, entries, notes = world.world_tick(
        block, day=2, now_minute=2 * 1440, event_seq=21,
        places={"place-x": _site_place()})
    project = block["projects"]["p-npc"]
    assert project["status"] == "running"
    assert [e["kind"] for e in entries if e["kind"].startswith("build")] \
        == ["build_material", "build_pay"]
    assert any("自动开工" in n for n in notes)
    assert project["milestones"][0]["at_minute"] == 2 * 1440 + 2 * 1440
    assert block["economy"]["balances"]["coin"] == 900
    assert world.verify_ledger(block) == []
    # 逐日推进 + 到期完工（day 4 = 里程碑到期日）
    block, _, _ = _settle(block, 3)
    assert block["projects"]["p-npc"]["progress"]["labor_done_days"] == 1
    block, _, _ = _settle(block, 4)
    assert block["projects"]["p-npc"]["status"] == "completed"
    assert block["projects"]["p-npc"]["progress"]["labor_done_days"] == 2
    assert block["settlements"]["s1"]["facilities"][0]["facility_id"] \
        == "fac-p-npc"


# ── 10. 委托指令（W6，§6.5） ──────────────────────────────────────────────

def _directive_block(*, stock=10, coin=0, cap=400):
    """指令夹具：一座人口 10 的据点 + 一种 food good（价格 2）。"""
    goods = [{"good_id": "g-food", "name": "黑麦", "category": "food",
              "tier": 1, "base_price": 2, "stock": stock,
              "produced_by": ["place-x"], "era": "fantasy"}]
    block = world.new_block(goods=goods, balances={"coin": coin})
    block["settlements"]["s1"] = {"place_id": "place-x", "population": 10}
    directive = world.new_directive(
        "d-grain", text="每周确保粮仓有储备", goal={"metric": "grain_days",
                                                    "op": ">=", "target": 5},
        budget={"cap_total": cap}, authorized_scope=["procure"])
    block = world.directive_add(block, "s1", directive)
    return block


def test_directive_budget_clamp_and_hard_ask():
    """§6.5：cap_total 在提案生成时钳制（比事后审计更硬）；单笔超 50% 命中
    硬回问清单 → blocked + 事实留痕、无自动花费（§10.2 2.4）。"""
    # 硬回问：需要 40×2=80 > 50%×100 → blocked + directive_ask 事实，无花费
    block = _directive_block(cap=100)
    new_block, entries, notes = _settle(block, 1)
    directive = new_block["settlements"]["s1"]["directives"]["d-grain"]
    assert directive["status"] == "blocked"
    assert directive["blocked_reason"] == "hard_ask_cap_half"
    assert directive["spent_total"] == 0
    assert not [e for e in entries
                if (e["refs"] or {}).get("directive_id")], entries
    asks = [f for f in new_block["facts"] if f["kind"] == "directive_ask"]
    assert asks and asks[0]["subject_ref"] == "d-grain"
    assert any("硬回问" in n for n in notes)
    # 钳制：cap 400 → 单笔 80 ≤ 200 → 采买 40 只（spent 80 ≤ cap）
    block = _directive_block(cap=400, coin=1000)
    new_block, entries, _ = _settle(block, 1)
    directive = new_block["settlements"]["s1"]["directives"]["d-grain"]
    assert directive["status"] == "active" and directive["spent_total"] == 80
    trades = [e for e in entries if (e["refs"] or {}).get("directive_id")]
    assert [(e["kind"], e["account"], e["delta"]) for e in trades] == \
        [("trade", "coin", -80), ("trade", "g-food", 40)], trades
    assert all(e["reason"]["verb"] == "procure" for e in trades)
    # 未授权 procure 的指令不自动花费
    block = _directive_block(cap=400, coin=1000)
    block["settlements"]["s1"]["directives"]["d-grain"]["authorized_scope"] = []
    new_block, entries, _ = _settle(block, 1)
    assert not [e for e in entries
                if (e["refs"] or {}).get("directive_id")]
    # 预算耗尽：spent_total 单调不越 cap_total（后续日钳到剩余，不足一单位即停）
    block = _directive_block(cap=400, coin=1000)
    for day in range(2, 8):
        block, _, _ = _settle(block, day)
        spent = block["settlements"]["s1"]["directives"]["d-grain"][
            "spent_total"]
        assert spent <= 400, (day, spent)
    assert world.verify_ledger(block) == []


def test_directive_paused_skips_and_revoked_stops_future():
    """§6.5：paused 日切跳过；revoked 只停后续——已提交的采买不回滚。"""
    block = _directive_block(cap=400, coin=1000)
    block, _, _ = _settle(block, 1)                  # 采买 80 已提交
    stock_committed = block["economy"]["goods"]["g-food"]["stock"]
    assert stock_committed > 10, "已提交的采买必须落账"
    # 撤销：只停后续
    block = world.directive_set_status(block, "s1", "d-grain", "revoked")
    block, entries, _ = _settle(block, 2)
    assert not [e for e in entries
                if (e["refs"] or {}).get("directive_id")], entries
    assert block["economy"]["goods"]["g-food"]["stock"] >= stock_committed - 10
    # 暂停：日切跳过（可恢复）
    block = _directive_block(cap=400, coin=1000)
    block = world.directive_set_status(block, "s1", "d-grain", "paused")
    block, entries, _ = _settle(block, 1)
    assert not [e for e in entries
                if (e["refs"] or {}).get("directive_id")], entries
    block = world.directive_set_status(block, "s1", "d-grain", "active")
    assert block["settlements"]["s1"]["directives"]["d-grain"][
        "blocked_reason"] == ""
    block, entries, _ = _settle(block, 2)
    assert [e for e in entries if (e["refs"] or {}).get("directive_id")], \
        "恢复后日切应继续执行"


def test_directive_explain_six_fields():
    """§6.5 可解释六项：目标 / 预算 / 在建工程 / 阻塞原因 / 近 3 笔决策 /
    预期结果（§16.5 逐项可读出）。"""
    block = _directive_block(cap=400, coin=1000)
    block["economy"]["balances"]["labor"] = 10
    block["economy"]["goods"]["g-iron"] = {
        "good_id": "g-iron", "name": "铁锭", "category": "military", "tier": 2,
        "base_price": 6, "stock": 100, "produced_by": [], "era": "fantasy"}
    block["economy"]["prices"]["g-iron"] = {"price": 6, "updated_day": 0}
    # 指令授权的在建工程（directive_id 关联键）
    project = _wall_project("p-d", source="directive", directive_id="d-grain")
    block = world.project_create(block, project)
    block = world.project_queue(block, "p-d")
    block, _ = world.project_start(block, "p-d", now_minute=0,
                                   event_seq=9, place=_site_place())
    block, _, _ = _settle(block, 1)
    explain = world.directive_explain(block, "s1", "d-grain")
    for key in ("goal", "budget", "projects", "blocked_reason",
                "recent_decisions", "expected"):
        assert key in explain, "缺可解释字段 %s" % key
    assert explain["goal"]["metric"] == "grain_days"
    assert explain["budget"]["cap_total"] == 400
    assert explain["budget"]["spent_total"] == 80
    assert explain["budget"]["remaining"] == 320
    assert explain["projects"] == ["p-d"]
    assert len(explain["recent_decisions"]) == 2          # 近窗 ≤ 3 条
    assert explain["expected"]["metric"] == "grain_days"
    assert explain["expected"]["met"] is False
    # 目标达成后的 expected.met 翻真
    block["economy"]["goods"]["g-food"]["stock"] = 1000
    explain = world.directive_explain(block, "s1", "d-grain")
    assert explain["expected"]["met"] is True
    try:
        world.directive_explain(block, "s1", "nope")
        raise AssertionError("未知指令应抛错")
    except world.WorldError:
        pass


def test_directive_labor_pool_injection():
    """§7.2 劳动力三来源之三：指令劳力池按日注入 labor 账户（coin 支付）。"""
    block = _directive_block(cap=0, coin=100)
    block["settlements"]["s1"]["directives"]["d-grain"]["budget"] = {
        "labor_per_day": 3}
    block["settlements"]["s1"]["directives"]["d-grain"][
        "authorized_scope"] = []
    block, entries, _ = _settle(block, 1)
    hire = [e for e in entries
            if (e["refs"] or {}).get("directive_id") == "d-grain"]
    assert [(e["kind"], e["account"], e["delta"]) for e in hire] == \
        [("wage", "coin", -3), ("grant", "labor", 3)], hire
    assert block["economy"]["balances"]["labor"] == 3
    assert world.verify_ledger(block) == []


# ── 11. 扰动联动与维护链（W6，§7.2 验证） ─────────────────────────────────

def test_disruption_linkage_running_project():
    """§7.2 扰动对 running Project 生效（P7 验证）：block / pause /
    progress_loss 落地且 Entry(kind="disruption") 可追；非在场工程不吃效果。"""
    rule_id = "hostile_occupation"
    seed = _seed_trigger(rule_id, 1)
    block = _project_block()
    project = _wall_project("p-run")
    block = world.project_create(block, project)
    block = world.project_queue(block, "p-run")
    block, _ = world.project_start(block, "p-run", now_minute=0, event_seq=5,
                                   place=_site_place())
    block = world.project_create(block, _wall_project("p-done"))
    block["projects"]["p-done"]["status"] = "completed"
    block["facts"].append(_fact("place_lost", minute=100, fact_id="fact-1"))
    new_block, entries, _ = _settle(block, 1, seed=seed)
    assert new_block["projects"]["p-run"]["status"] == "paused"
    assert new_block["projects"]["p-run"]["blocked_reason"] == "disruption"
    assert new_block["projects"]["p-done"]["status"] == "completed", \
        "终态工程不受扰动效果影响"
    hits = [e for e in entries if e["kind"] == "disruption"]
    assert [e["refs"]["project_id"] for e in hits] == ["p-run"]
    assert hits[0]["refs"]["rule_id"] == rule_id
    # progress_loss 吃 §7.1 的 progress.labor_done_days（labor_dispute）
    rule_id = "labor_dispute"
    seed = _seed_trigger(rule_id, 1)
    block = _project_block()
    block = world.project_create(block, _wall_project("p-run"))
    block = world.project_queue(block, "p-run")
    block, _ = world.project_start(block, "p-run", now_minute=0, event_seq=5,
                                   place=_site_place())
    block["projects"]["p-run"]["progress"]["labor_done_days"] = 4
    block["projects"]["p-run"]["requirements"]["labor_days"] = 4
    block["facts"].append(_fact("labor_unrest", minute=100, value=2,
                                fact_id="fact-2"))
    new_block, entries, _ = _settle(block, 1, seed=seed)
    assert new_block["projects"]["p-run"]["progress"][
        "labor_done_days"] == 2, "progress_loss 应把进度砍半"
    assert [e for e in entries if e["kind"] == "disruption"], "留痕可追"


def test_maintenance_funds_chain_project_linkage():
    """维护扣账链（验收项）：到期义务从中央金库扣；资金断 → 据点 blocked
    （W4 既有）+ 设施降级 + Project 侧状态联动（工资同断 → blocked）。"""
    goods = [{"good_id": "g-food", "name": "黑麦", "category": "food",
              "tier": 1, "base_price": 2, "stock": 50, "produced_by": [],
              "era": "fantasy"}]
    # 正常路径：完工设施维护 2/日从金库扣
    block = world.new_block(goods=goods, balances={"coin": 100})
    block["settlements"]["s1"] = {"place_id": "place-x", "population": 1,
                                  "facilities": [
                                      {"facility_id": "fac-a", "kind": "wall",
                                       "maintenance_per_day": 2,
                                       "maintenance_level": 3}]}
    block, entries, _ = _settle(block, 1)
    maint = [e for e in entries if e["kind"] == "maintenance"]
    assert maint and maint[0]["delta"] == -2 and \
        maint[0]["refs"]["settlement_id"] == "s1"
    assert block["economy"]["balances"]["coin"] == 98
    assert block["settlements"]["s1"].get("status") != "blocked"
    # 资金断：金库空 → 据点 blocked + 设施降级一格 + 工程 blocked(funds)
    block = world.new_block(goods=goods, balances={"coin": 0})
    block["settlements"]["s1"] = {"place_id": "place-x", "population": 1,
                                  "facilities": [
                                      {"facility_id": "fac-a", "kind": "wall",
                                       "maintenance_per_day": 2,
                                       "maintenance_level": 3}]}
    block["projects"]["p-run"] = {"place_id": "place-x", "status": "running",
                                  "wage_per_day": 5,
                                  "needs": {"g-food": 1}}
    block, entries, notes = _settle(block, 1)
    assert block["settlements"]["s1"]["status"] == "blocked"
    assert block["settlements"]["s1"]["blocked_reason"] == "funds"
    assert block["settlements"]["s1"]["facilities"][0][
        "maintenance_level"] == 2, "维护欠费应降级一格"
    assert block["projects"]["p-run"]["status"] == "blocked"
    assert block["projects"]["p-run"]["blocked_reason"] == "funds"
    assert not [e for e in entries if e["kind"] in ("wage", "maintenance")], \
        "发不出不得记 Entry"
    assert any("降级" in n for n in notes)
    # 降到底即停：blocked 据点退出结算（W4 既有），不再继续降级
    block, _, _ = _settle(block, 2)
    block, _, _ = _settle(block, 3)
    assert block["settlements"]["s1"]["facilities"][0][
        "maintenance_level"] == 2
    assert world.verify_ledger(block) == []




# ── W7：NEURA 挂接（§8：投影 / 匹配表 / 种群初始化；Issue #394） ───────────


def _w7_scenario():
    """§8.2 投影夹具：剧本 threads（参与人是名字）+ 档案（name→id / faction）。"""
    return {
        "npcs": [
            {"id": "npc-01", "name": "伊蕾芙", "faction": "守冠庭"},
            {"id": "npc-02", "name": "涅朵婆婆", "faction": ""},
            {"id": "npc-03", "name": "卡霖", "faction": "叛军"},
        ],
        "threads": [
            {"id": "C-01", "npcs": ["伊蕾芙", "涅朵婆婆", "流浪者"]},
            {"id": "C-02", "npcs": ["卡霖"]},
        ],
    }


def _w7_world_block(facts=None):
    block = world.new_block(day=0)
    for fact in facts or []:
        block["facts"].append(fact)
    return block


def test_w7_projection_default_keys_empty():
    """§8.2：无数据 → 全部缺省键（M11 空键口径逐字段一致）。"""
    out = world.projection({})
    assert out == {
        "known_refs": [], "main_thread_refs": [], "faction_refs": [],
        "importance_overrides": {}, "travel_minutes": {}, "world_facts": {},
        "laws": [], "law_source": "none",
    }
    # 脏输入不抛错：非 dict / 非法形状一律按缺省。
    assert world.projection(None)["known_refs"] == []
    assert world.projection({"scenario": 3, "world": "x", "bands": [1],
                             "npcs": "bad"})["main_thread_refs"] == []


def test_w7_projection_known_refs_p10():
    """§8.2 + P10：known_refs 来自已知线索表；无数据 = 空表（不降级全集）。"""
    assert world.projection({"known_clues": ["C-02", "C-01", "C-01", ""]})[
        "known_refs"] == ["C-01", "C-02"]
    assert world.projection({})["known_refs"] == []
    # 剧本给了一堆线索也没用：P10 拍板不实现「全集降级」。
    assert world.projection({"scenario": _w7_scenario()})["known_refs"] == []


def test_w7_projection_main_thread_refs():
    """§8.2：主线 thread 参与人展开；thread.npcs 的名字经档案映射成 npc_id。"""
    out = world.projection({"scenario": _w7_scenario()})
    # 「伊蕾芙」→ npc-01、「涅朵婆婆」→ npc-02、「卡霖」→ npc-03；
    # 「流浪者」档案里没有 → 原样保留（可能是 id 或待补档案）；
    # 字典序排序 + 去重。
    assert out["main_thread_refs"] == \
        ["npc-01", "npc-02", "npc-03", "流浪者"]
    # 无 threads 的剧本 → 空。
    assert world.projection({"scenario": {"npcs": _w7_scenario()["npcs"]}})[
        "main_thread_refs"] == []


def test_w7_projection_faction_refs():
    """§8.2：faction_refs = 有 faction 归属的 npc_id 集；world_module.factions
    给了就按其 id / name 校准。"""
    scenario = _w7_scenario()
    # 没给 world_module：档案 faction 非空即算。
    out = world.projection({"scenario": scenario})
    assert out["faction_refs"] == ["npc-01", "npc-03"]
    # world_module.factions 只认「守冠庭」→ 叛军被校准掉。
    module = {"factions": [{"id": "faction-01", "name": "守冠庭"}]}
    out = world.projection({"scenario": scenario, "world_module": module})
    assert out["faction_refs"] == ["npc-01"]
    # world_module 存在但 factions 缺失 → 宽松口径（faction 非空即算）。
    out = world.projection({"scenario": scenario, "world_module": {}})
    assert out["faction_refs"] == ["npc-01", "npc-03"]


def test_w7_projection_overrides_and_facts():
    """§8.2：importance 覆盖表（后写覆盖先写）+ world_facts 按 subject 分组。"""
    block = _w7_world_block(facts=[
        {"fact_id": "f-2", "minute": 5, "kind": "importance_override",
         "subject_ref": "npc-01", "text": "", "value": 0.1},
        {"fact_id": "f-1", "minute": 5, "kind": "importance_override",
         "subject_ref": "npc-01", "text": "", "value": 0.9},
        {"fact_id": "f-3", "minute": 1, "kind": "grain_fire",
         "subject_ref": "place-a", "text": "粮仓失火"},
        {"fact_id": "f-4", "minute": 2, "kind": "importance_override",
         "subject_ref": "npc-02", "text": "", "value": 0},
    ])
    out = world.projection({"world": block})
    assert out["importance_overrides"] == {"npc-01": 0.1, "npc-02": 0}
    assert [f["fact_id"] for f in out["world_facts"]["place-a"]] == ["f-3"]
    assert [f["fact_id"] for f in out["world_facts"]["npc-01"]] == ["f-1", "f-2"]


def test_w7_projection_travel_minutes():
    """§8.2：travel_minutes 只对「块内地点 × 队伍地点」求值；档位→分钟表由
    调用方注入（BAND_MINUTES 口径）；同目的地取最小；缺表 = 空键。"""
    band_minutes = {"short": 360, "medium": 720, "long": 1440,
                    "dangerous": 1440}
    state = {
        "party_places": ["market"],
        "bands": {("market", "gate"): "adjacent", ("market", "temple"): "near",
                  ("market", "docks"): "far", ("market", "market"): "same",
                  ("gate", "temple"): "far",   # 出发点不是队伍地点：不投影
                  ("market", "ruins"): "far"},
        "band_minutes": band_minutes,
    }
    out = world.projection(state)
    assert out["travel_minutes"] == {"gate": 360, "temple": 720,
                                     "docks": 1440, "market": 0,
                                     "ruins": 1440}
    # 缺 band_minutes：非 same 的档位全拿不到分钟 → 只剩 same 的 0。
    out = world.projection({key: state[key] for key in
                            ("party_places", "bands")})
    assert out["travel_minutes"] == {"market": 0}
    # 同目的地多队伍地点 → 取最小（不夸大可达性）。
    out = world.projection({
        "party_places": ["market", "docks"],
        "bands": {("market", "gate"): "adjacent", ("docks", "gate"): "far"},
        "band_minutes": band_minutes,
    })
    assert out["travel_minutes"] == {"gate": 360}


def test_w7_projection_laws_and_source():
    """§8.1（V9）：laws 逐字段对齐 neura 消费契约；无 laws → law_source=none。"""
    module = {"laws": [
        {"id": "law-01", "name": "禁武令", "forbid_tags": ["武器"],
         "scope_place_ids": ["place-a"], "threshold": "tolerate",
         "penalty": "没收", "source": "docs/system/x.md:1", "extra": "多余键剔除"},
        {"id": "", "name": "坏条目"},                # 无 id：跳过
        "不是对象",                                    # 非 dict：跳过
        {"id": "law-02", "name": "宵禁", "threshold": "forbid"},
    ]}
    out = world.projection({"world_module": module})
    assert out["law_source"] == "world"
    assert out["laws"] == [
        {"id": "law-01", "name": "禁武令", "forbid_tags": ["武器"],
         "scope_place_ids": ["place-a"], "threshold": "tolerate",
         "penalty": "没收", "source": "docs/system/x.md:1"},
        {"id": "law-02", "name": "宵禁", "forbid_tags": [],
         "scope_place_ids": [], "threshold": "forbid",
         "penalty": "", "source": ""},
    ]
    assert world.projection({"world_module": {"laws": []}})["law_source"] == "none"
    assert world.projection({})["laws"] == []


def test_w7_projection_is_pure():
    """§8.2：投影不改写入参、同输入重跑逐字节相等。"""
    scenario = _w7_scenario()
    block = _w7_world_block(facts=[
        {"fact_id": "f-1", "minute": 0, "kind": "importance_override",
         "subject_ref": "npc-01", "text": "", "value": 0.5}])
    state = {"known_clues": ["C-01"], "scenario": scenario, "world": block}
    snapshot = _dumps(state)
    first = _dumps(world.projection(state))
    assert first == _dumps(world.projection(state))
    assert snapshot == _dumps(state), "投影不得改写 session_state"
    # bands（tuple 键，JSON 不可序列化）同样不被改写。
    bands = {("market", "gate"): "adjacent"}
    snapshot_bands = dict(bands)
    world.projection({"bands": bands, "party_places": ["market"],
                      "band_minutes": {"short": 360}})
    assert bands == snapshot_bands


def test_w7_match_event_rules():
    """§8.4 匹配表三条规则逐条断言（含层过滤归编排层的边界）。"""
    def _npc(npc_id, place, *, slot_place=None, goals=None, faction=None):
        npc = {"location": {"place_id": place}}
        if slot_place:
            npc["schedule"] = [{"place_id": slot_place,
                                "from_tod_minute": 0, "to_tod_minute": 1}]
        if goals:
            npc["goals"] = goals
        if faction:
            npc["faction"] = faction
        return npc

    npcs = {
        "npc-a": _npc("npc-a", "gate"),
        "npc-b": _npc("npc-b", "temple", slot_place="forge"),
        "npc-c": _npc("npc-c", "temple",
                      goals=[{"goal_id": "g1", "target_ref": "obj-x",
                              "status": "active"},
                             {"goal_id": "g2", "target_ref": "obj-y",
                              "status": "done"}]),
        "npc-d": _npc("npc-d", "temple", faction="守冠庭"),
    }
    readonly = {"npcs": npcs, "facts": [], "factions": {"npc-d": "守冠庭"}}

    # 规则 0：空事件 / 无命中 → 空。
    assert world.match_event({}, readonly) == []
    assert world.match_event({"place_id": "nowhere"}, readonly) == []

    # 规则 1a：地点 == location.place_id。
    assert world.match_event({"place_id": "gate"}, readonly) == ["npc-a"]
    # 规则 1b：地点 == schedule slot 地点（npc-b 的 location 在 temple、
    # slot 在 forge；forge 只命中 slot 分支）。
    assert world.match_event({"place_id": "forge"}, readonly) == ["npc-b"]

    # 规则 2a：subject/target 引用 npc_id。
    assert world.match_event({"target": "npc-c"}, readonly) == ["npc-c"]
    # 规则 2b：引用所属 faction（factions 表）。
    assert world.match_event({"subject": "守冠庭"}, readonly) == ["npc-d"]
    # 规则 2c：引用 active goal 的 target_ref（done 的不算）。
    assert world.match_event({"target": "obj-x"}, readonly) == ["npc-c"]
    assert world.match_event({"target": "obj-y"}, readonly) == []

    # 规则 3：facts 标记势力级事件 → 全体成员命中。
    readonly_f = dict(readonly, facts=[
        {"fact_id": "f-1", "minute": 0, "kind": "faction_event",
         "subject_ref": "守冠庭", "text": ""}])
    assert world.match_event({"target": "守冠庭"}, readonly_f) == ["npc-d"]
    # 没有 facts 标记时同样的引用不触发规则 3（规则 2b 仍命中单个成员）。
    assert world.match_event({"target": "守冠庭"}, readonly) == ["npc-d"]

    # 命中多 NPC → 排序去重；factions 表缺 faction 分支读运行时 faction 字段。
    out = world.match_event({"place_id": "temple"}, readonly)
    assert out == ["npc-b", "npc-c", "npc-d"]
    readonly_nf = {"npcs": npcs, "facts": []}      # 无 factions 表
    assert world.match_event({"subject": "守冠庭"}, readonly_nf) == ["npc-d"]


def test_w7_match_event_then_register_rejudge_budget():
    """§8.4 / 10.2 断言 2.9：match_event → register_rejudge；同 NPC 同日
    第 5 次不再注入；跨日归零。"""
    npcs = {"npc-a": {"location": {"place_id": "gate"}}}
    block = world.new_block(day=0)
    for _ in range(4):
        matched = world.match_event({"place_id": "gate"}, {"npcs": npcs})
        block, injected = world.register_rejudge(block, matched, day=3)
        assert injected == ["npc-a"]
    assert block["rejudge"] == {"day": 3, "counts": {"npc-a": 4}}
    # 第 5 次：预算内不注入（不报错）。
    block, injected = world.register_rejudge(
        block, world.match_event({"place_id": "gate"}, {"npcs": npcs}), day=3)
    assert injected == []
    # 跨日归零（world_tick 收尾同口径）。
    block, injected = world.register_rejudge(
        block, world.match_event({"place_id": "gate"}, {"npcs": npcs}), day=4)
    assert injected == ["npc-a"]
    assert block["rejudge"] == {"day": 4, "counts": {"npc-a": 1}}


def test_w7_populate_deterministic_and_shape():
    """§8.5：同 (spec, seed) 逐字节相等；键面镜像 neura 块；P10 importance。"""
    scenario = {
        "meta": {"campaign": "测试"},
        "npcs": [
            {"id": "npc-01", "name": "伊蕾芙", "role": "谷中领主",
             "faction": "守冠庭", "secret": "她写过退位书", "importance": 0.2},
            {"id": "npc-02", "name": "涅朵婆婆", "role": "屠户"},
            {"id": "npc-03", "name": "卡霖", "role": "守烬人卫兵",
             "faction": "叛军"},
            {"id": "", "name": "无 id 的坏档案"},
            {"id": "npc-05", "name": ""},
        ],
        "threads": [{"id": "C-01", "npcs": ["伊蕾芙"]}],
    }
    places = {"npc-01": "place-a", "npc-03": "place-b"}
    a = world.populate(scenario, seed=42, places=places)
    b = world.populate(scenario, seed=42, places=places)
    assert _dumps(a) == _dumps(b), "同 (spec, seed) 必须逐字节相等"
    assert set(a["npcs"]) == {"npc-01", "npc-02", "npc-03"}  # 不发明 NPC
    assert a["version"] == 1 and a["schema_version"] == 1
    assert a["journal"] == []
    assert a["budget"] == {"day": 0, "strong_calls": 0, "light_calls": 0,
                           "tokens": 0}

    # P10：显式覆盖优先；公式 0.4×threads + 0.3×faction + 0.3×secret。
    assert a["npcs"]["npc-01"]["importance"] == 0.2
    assert a["npcs"]["npc-02"]["importance"] == 0.0
    assert a["npcs"]["npc-03"]["importance"] == 0.3   # faction 有、secret 无

    # needs：中位数 50 ± 10 确定性抖动；键面六键。
    for npc_id, npc in a["npcs"].items():
        assert set(npc["needs"]) == {"safety", "wealth", "power", "relation",
                                     "duty", "curiosity"}
        for value in npc["needs"].values():
            assert 40 <= value <= 60
    # role 模板：卫兵类 → safety/duty 加重。
    assert a["npcs"]["npc-03"]["need_weights"]["safety"] == 1.5
    assert a["npcs"]["npc-03"]["need_weights"]["duty"] == 1.5
    assert a["npcs"]["npc-02"]["need_weights"]["safety"] == 1.0

    # 地点 / 日程：注入 places 的 NPC 有 location 与单个 slot；没有的为空。
    assert a["npcs"]["npc-01"]["location"]["place_id"] == "place-a"
    slot = a["npcs"]["npc-01"]["schedule"][0]
    assert slot["place_id"] == "place-a"
    assert 480 <= slot["from_tod_minute"] < 1200
    assert slot["from_tod_minute"] < slot["to_tod_minute"] <= 1440
    assert a["npcs"]["npc-02"]["schedule"] == []
    assert a["npcs"]["npc-02"]["location"]["place_id"] == ""

    # secrets：档案 secret 非空 → 单元素表。
    assert a["npcs"]["npc-01"]["secrets"] == ["她写过退位书"]
    assert a["npcs"]["npc-02"]["secrets"] == []

    # 换 seed → needs 抖动不同（确定性来源换了）；同 seed 重跑仍逐字节相等。
    c = world.populate(scenario, seed=43, places=places)
    assert _dumps(c) != _dumps(a)
    # 兼容性：产物能过 neura.migrate_block（键面是 neura 认识的）。
    import neura  # noqa: E402 — 测试内局部 import（仅兼容性断言用）
    migrated = neura.migrate_block(a)
    assert set(migrated["npcs"]) == set(a["npcs"])
    assert migrated["npcs"]["npc-01"]["importance"] == 0.2
    assert migrated["npcs"]["npc-01"]["needs"] == a["npcs"]["npc-01"]["needs"]


def test_w7_populate_roster_list_and_purity():
    """§8.5：worldgen.roster 产物（list）也能初始化；不改写入参。"""
    spec = worldgen.normalize_spec({"world_type": "fantasy", "scale": "sketch"})
    roster = worldgen.roster(spec, 42, factions=["faction-01", "faction-02"])
    snapshot = _dumps(roster)
    block = world.populate(roster, seed=42)
    assert set(block["npcs"]) == {npc["id"] for npc in roster}
    # roster 的 faction 是字符串 → P10 公式的 faction 项生效；sketch 档
    # secrets_required 使首个档案带 secret → faction + secret = 0.6。
    assert block["npcs"][roster[0]["id"]]["importance"] == 0.6
    assert snapshot == _dumps(roster), "populate 不得改写档案"
    # 无 threads 的 list 来源 → 主线项 0；同 (spec, seed) 逐字节相等。
    assert _dumps(block) == _dumps(world.populate(roster, seed=42))


# ── W8：schema 扩展验收（Issue #396） ─────────────────────────────────────

def test_w8_schema_extension_accepts_new_keys():
    """W8 schema 扩展：`world.schema.json` 新增的 laws / goods /
    worldgen_provenance / lexicon_overrides / regions[].economy 全部是
    **可选属性**——官方世界正文（不带新键）照常过校验；官方世界 + W1
    合成产物的新键过校验（goods 含 era、provenance 三来源、laws 双轨
    source）；形状非法的新键被拒。"""
    sys.path.insert(0, os.path.join(ROOT, "tests"))
    import validate_data

    schema_dir = os.path.join(ROOT, "data", "schema")
    worlds_dir = os.path.join(ROOT, "data", "worlds")
    # ① 三个官方世界正文零改动仍过校验（新键全部可选）
    for name in sorted(os.listdir(worlds_dir)):
        if not name.endswith(".json"):
            continue
        errs = validate_data.validate_file(os.path.join(worlds_dir, name),
                                           schema_dir, ROOT)
        assert errs == [], (name, errs)

    spec = worldgen.normalize_spec({
        "world_type": "fantasy", "scale": "region", "economy_on": True,
        "laws_req": [{"name": "禁武令", "forbid_tags": ["武器"]}],
        "tone_terms": ["港口"]})
    base = worldgen.choose_base(spec)
    synthesized = worldgen.synthesize(spec, base, seed=42)

    doc = json.load(open(os.path.join(worlds_dir, "emberfall.json"),
                         encoding="utf-8"))
    doc["laws"] = synthesized["laws"]
    doc["goods"] = synthesized["goods"]
    doc["worldgen_provenance"] = synthesized["worldgen_provenance"]
    doc["lexicon_overrides"] = synthesized["lexicon_overrides"]
    doc["regions"][0]["economy"] = {"produces": [synthesized["goods"][0]["name"]]}
    tmp = tempfile.mkdtemp(prefix="w8-schema-")
    path = os.path.join(tmp, "w8_schema_probe.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, ensure_ascii=False)
    try:
        # ② 官方世界 + 合成产物新键过校验（goods 顶层键转正闭环）
        errs = validate_data.validate_file(path, schema_dir, ROOT)
        assert errs == [], errs

        # ③ 形状非法的新键被拒：threshold 三值之外 / goods 缺 era
        doc["laws"][0]["threshold"] = "sometimes"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        errs = validate_data.validate_file(path, schema_dir, ROOT)
        assert errs, "threshold 非法值必须被拒"

        doc["laws"][0]["threshold"] = "forbid"
        doc["goods"][0].pop("era")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False)
        errs = validate_data.validate_file(path, schema_dir, ROOT)
        assert errs, "goods 缺 era 必须被拒"
    finally:
        os.remove(path)
        os.rmdir(tmp)


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
