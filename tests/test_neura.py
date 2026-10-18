#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neura 内核（M11-N1 骨架 + M11-N2 硬约束）回归测试。

逐条覆盖 `Issue #339`（N1）与 `Issue #348`（N2）的验收清单：

N1：

- `empty_block()` 经 `json.dumps` → `json.loads` 往返逐字段相等；
- 缺 `needs` 六键的旧块 → `migrate_block` 补 50；已有合法值（如 73）保持；
- `block["version"] > NEURA_VERSION` → 抛 `NeuraError`（有断言）；
- `prune_block` 对 300 条 journal / 200 条 memory 裁到上限，连续两次结果全等；
- `stable_unit("a","b")` 确定、不同输入不同输出、100 万次分布均匀（±1%）；
- `import neura` 无 IO、无副作用，未引入 `state` / `chron` / `notdnd_web` /
  `prism_core` / `prism_guide` / `atlas`。

N2：

- 五个 `E_*` 码各有一条**真实断言**（构造必然命中的输入，而不是空跑）；
- `view` 缺 `laws` → `law_source == "none"`，`ok` 不受影响、不抛错（P8）；
- 距离两条路都判（`bands` 直通 / `far` 但 `minutes` 够）；
- 全灭 → `{"ok": False, "options": []}`，`rejected` 每项都有 `code` + `option_id`；
- `E_LAYER_BUDGET` 只禁 AI 候选，规则候选仍出现；
- 纯函数：入参深比较前后相等，期间未调用 `open` / 未建 socket。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_neura.py` 运行。
"""

import ast
import builtins
import copy
import json
import os
import socket
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import neura  # noqa: E402  (ROOT 已加入 sys.path)

NEURA_PY = os.path.join(ROOT, "neura.py")
FORBIDDEN_MODULES = ("state", "chron", "notdnd_web", "prism_core",
                     "prism_guide", "atlas")


def _json_roundtrip(value):
    """JSON 往返（`ensure_ascii=False` 保持中文原样）。"""
    return json.loads(json.dumps(value, ensure_ascii=False))


def _block_with_npc(needs=None):
    block = neura.empty_block()
    npc = neura.empty_npc("npc-01", "sc-01")
    if needs is not None:
        npc["needs"] = needs
    block["npcs"]["npc-01"] = npc
    return block


# --------------------------------------------------------------------------
# §11 N1：常量与空结构
# --------------------------------------------------------------------------

def check_constants():
    """版本与两个硬上限就是 §12.1 P3 拍板值。"""
    assert isinstance(neura.NEURA_VERSION, int) and not isinstance(
        neura.NEURA_VERSION, bool)
    assert neura.NEURA_VERSION == 1
    assert neura.MEMORY_MAX == 128
    assert neura.JOURNAL_MAX == 256
    assert neura.NEED_DEFAULT == 50
    assert neura.NEED_KEYS == ("safety", "wealth", "power", "relation",
                               "duty", "curiosity")


def check_empty_block_json_roundtrip():
    """`empty_block()` 过 JSON 往返逐字段相等。"""
    block = neura.empty_block()
    decoded = _json_roundtrip(block)
    assert decoded == block
    # 块级形状：版本、战役种子、NPC 表、决策留痕、预算账本。
    for key in ("version", "schema_version", "campaign_seed", "npcs",
                "journal", "budget"):
        assert key in block, "empty_block 缺块级字段 %s" % key
    assert block["npcs"] == {}
    assert block["journal"] == []
    assert block["budget"] == {"day": 0, "strong_calls": 0,
                               "light_calls": 0, "tokens": 0}


def check_empty_block_fresh_defaults():
    """默认值不共享可变对象（改一个块不会漏进另一个）。"""
    first = neura.empty_block()
    second = neura.empty_block()
    first["npcs"]["npc-01"] = neura.empty_npc("npc-01", "sc-01")
    first["npcs"]["npc-01"]["needs"]["safety"] = 99
    first["journal"].append({"decision_id": "d-1"})
    first["budget"]["tokens"] = 4000
    assert second["npcs"] == {}
    assert second["journal"] == []
    assert second["budget"]["tokens"] == 0


def check_empty_npc_shape():
    """空 NPC 含 `needs` 六键 / `memory` / `budget` 之外的 §4.2 字段。"""
    npc = neura.empty_npc("npc-07", "sc-02")
    assert npc["needs"] == {key: 50 for key in neura.NEED_KEYS}
    assert npc["memory"] == []
    assert npc["display_ref"] == {"scenario_id": "sc-02", "npc_id": "npc-07"}
    assert npc["abilities"] == {key: None for key in neura.ABILITY_KEYS}
    assert npc["resources"] == {"coin": 0, "stock": {}, "obligations": []}
    # §4.2：plan 为空时键不存在，不用 null。
    assert "plan" not in npc
    assert _json_roundtrip(npc) == npc


# --------------------------------------------------------------------------
# §9.2：惰性迁移
# --------------------------------------------------------------------------

def check_migrate_fills_missing_needs():
    """缺 `needs` 六键的旧块补 50。"""
    block = _block_with_npc({"safety": 73})
    migrated = neura.migrate_block(block)
    needs = migrated["npcs"]["npc-01"]["needs"]
    assert needs["safety"] == 73, "已有的合法值被覆盖了"
    assert needs["wealth"] == 50
    assert needs["power"] == 50
    assert needs["relation"] == 50
    assert needs["duty"] == 50
    assert needs["curiosity"] == 50
    assert _json_roundtrip(migrated) == migrated


def check_migrate_preserves_legal_values():
    """已有的合法值逐字保留（含 0 / 100 两个边界）。"""
    values = {"safety": 0, "wealth": 100, "power": 73,
              "relation": 1, "duty": 99, "curiosity": 50}
    migrated = neura.migrate_block(_block_with_npc(dict(values)))
    assert migrated["npcs"]["npc-01"]["needs"] == values


def check_migrate_dirty_needs_to_default():
    """单个脏需求键只降级它自己，不影响其他键。"""
    dirty = {"safety": 101, "wealth": -1, "power": True,
             "relation": "50", "duty": 37.5}
    migrated = neura.migrate_block(_block_with_npc(dirty))
    needs = migrated["npcs"]["npc-01"]["needs"]
    assert needs["safety"] == 50
    assert needs["wealth"] == 50
    assert needs["power"] == 50
    assert needs["relation"] == 50
    assert needs["duty"] == 50
    assert needs["curiosity"] == 50


def check_migrate_rejects_higher_version():
    """版本高于本实现 → 抛 `NeuraError`（不静默降级）。"""
    for block in ({"version": neura.NEURA_VERSION + 1},
                  {"schema_version": neura.NEURA_VERSION + 1}):
        try:
            neura.migrate_block(block)
        except neura.NeuraError:
            pass
        else:
            raise AssertionError("更高版本未抛 NeuraError: %r" % (block,))


def check_migrate_missing_and_dirty_block():
    """缺块 / 非对象 / 版本脏值 → 空块；低版本 → 迁移到当前版本。"""
    assert neura.migrate_block(None) == neura.empty_block()
    assert neura.migrate_block("not-a-block") == neura.empty_block()
    assert neura.migrate_block({"version": "1"}) == neura.empty_block()
    stale = neura.migrate_block({"version": 0})
    assert stale["version"] == neura.NEURA_VERSION
    assert stale["schema_version"] == neura.NEURA_VERSION


def check_migrate_does_not_mutate_input():
    """迁移是纯函数：入参不被改写，返回的新块不共享可变对象。"""
    block = _block_with_npc({"safety": 73})
    snapshot = json.dumps(block, ensure_ascii=False, sort_keys=True)
    migrated = neura.migrate_block(block)
    assert json.dumps(block, ensure_ascii=False, sort_keys=True) == snapshot
    migrated["npcs"]["npc-01"]["needs"]["safety"] = 1
    migrated["journal"].append({"decision_id": "d-1"})
    assert block["npcs"]["npc-01"]["needs"]["safety"] == 73
    assert block["journal"] == []


# --------------------------------------------------------------------------
# §9.2 / §12.1 P3：裁剪
# --------------------------------------------------------------------------

def _prune_fixture():
    block = neura.empty_block()
    block["journal"] = [
        {"decision_id": "j-%03d" % index, "at_minute": index}
        for index in range(300)
    ]
    npc = neura.empty_npc("npc-01", "sc-01")
    npc["memory"] = [
        {"memory_id": "m-%03d" % index, "minute": index}
        for index in range(200)
    ]
    block["npcs"]["npc-01"] = npc
    return block


def check_prune_caps_and_keeps_newest():
    """300 条 journal / 200 条 memory 裁到 256 / 128，保留最新。"""
    pruned = neura.prune_block(_prune_fixture())
    assert len(pruned["journal"]) == neura.JOURNAL_MAX
    assert pruned["journal"][0]["decision_id"] == "j-044"
    assert pruned["journal"][-1]["decision_id"] == "j-299"
    assert len(pruned["npcs"]["npc-01"]["memory"]) == neura.MEMORY_MAX
    assert pruned["npcs"]["npc-01"]["memory"][0]["memory_id"] == "m-072"
    assert pruned["npcs"]["npc-01"]["memory"][-1]["memory_id"] == "m-199"


def check_prune_is_idempotent():
    """连续裁剪两次结果全等（块级 JSON 逐字节相等）。"""
    first = neura.prune_block(_prune_fixture())
    second = neura.prune_block(first)
    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)
    assert first == second


def check_prune_tie_break_is_deterministic():
    """同分钟按 id 字典序：保留字典序更大的（更新的）那一条。"""
    block = neura.empty_block()
    block["journal"] = [
        {"decision_id": "j-%03d" % index, "at_minute": 10}
        for index in range(neura.JOURNAL_MAX + 1)
    ]
    pruned = neura.prune_block(block)
    ids = [entry["decision_id"] for entry in pruned["journal"]]
    assert len(ids) == neura.JOURNAL_MAX
    assert "j-000" not in ids, "同分钟应按 id 字典序裁掉最小的一条"
    assert ids[0] == "j-001"
    assert ids[-1] == "j-256"


def check_prune_leaves_small_blocks_alone():
    """未超上限的块裁剪后不变（除迁移补默认外逐字段相等）。"""
    block = _block_with_npc()
    assert neura.prune_block(block) == block


def check_prune_does_not_mutate_input():
    """裁剪同样不改写入参。"""
    block = _prune_fixture()
    before = json.dumps(block, ensure_ascii=False, sort_keys=True)
    neura.prune_block(block)
    assert json.dumps(block, ensure_ascii=False, sort_keys=True) == before


# --------------------------------------------------------------------------
# §5.3 / §10.1：确定性单位哈希
# --------------------------------------------------------------------------

def check_stable_unit_determinism():
    """同输入同输出；不同输入不同输出；返回 `[0, 1)`。"""
    assert neura.stable_unit("a", "b") == neura.stable_unit("a", "b")
    assert neura.stable_unit("a", "c") != neura.stable_unit("a", "b")
    assert neura.stable_unit("a", "b") != neura.stable_unit("b", "a")
    # 拼接歧义必须区分：("a","bc") 与 ("ab","c")。
    assert neura.stable_unit("a", "bc") != neura.stable_unit("ab", "c")
    # 复合值也可确定。
    assert neura.stable_unit([1, 2], {"a": 1}) == \
        neura.stable_unit([1, 2], {"a": 1})
    for parts in ((), ("a",), ("a", "b"), (1, 2.5, True, None)):
        value = neura.stable_unit(*parts)
        assert isinstance(value, float)
        assert 0.0 <= value < 1.0


def check_stable_unit_distribution():
    """100 万次取值分 10 桶，每桶偏离期望 ≤ 1%。"""
    samples = 1000000
    buckets = 10
    counts = [0] * buckets
    for index in range(samples):
        counts[int(neura.stable_unit("dist", index) * buckets)] += 1
    expected = samples / buckets
    for position, count in enumerate(counts):
        deviation = abs(count - expected) / expected
        assert deviation <= 0.01, (
            "第 %d 桶 %d 次，偏离 %.3f%% 超过 1%%（期望 %.0f）"
            % (position, count, deviation * 100, expected))


# --------------------------------------------------------------------------
# §9.1：纯函数与依赖边界
# --------------------------------------------------------------------------

def check_module_is_pure():
    """`neura.py` 只 import 标准库、无 `open()`、不引入业务模块。"""
    with open(NEURA_PY, encoding="utf-8") as handle:
        source = handle.read()
    tree = ast.parse(source)

    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"copy", "hashlib"}, \
        "neura.py import 了非标准库模块: %s" % sorted(imported - {"copy", "hashlib"})
    assert not imported & set(FORBIDDEN_MODULES), \
        "neura.py import 了业务模块: %s" % sorted(imported & set(FORBIDDEN_MODULES))

    open_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "open"
    ]
    assert not open_calls, "neura.py 不应调用 open（无 IO）"

    for name in FORBIDDEN_MODULES:
        assert name not in sys.modules, "import neura 连带引入了业务模块 %s" % name


# --------------------------------------------------------------------------
# §5.2 / §5.2.1：硬约束过滤（M11-N2）
# --------------------------------------------------------------------------

def _view(**overrides):
    """§5.2.1 的 `view`（编排层构造）；`options` / `budget` 是 N2 的两个补充键。"""
    view = {
        "campaign_id": "cp-01",
        "now_minute": 600,
        "scenario_id": "sc-01",
        "party_places": ["market"],
        "places": {
            "gate": {"name": "城门", "exits": ["market"]},
            "market": {"name": "集市", "exits": ["gate", "temple"]},
            "temple": {"name": "神庙", "exits": ["market"]},
        },
        "known_places": {"gate": True, "market": True},
        "bands": {("gate", "market"): "adjacent", ("gate", "temple"): "far"},
        "travel_minutes": {"market": 5, "temple": 45},
        "laws": [],
        "law_source": "none",
        "world_facts": {},
        "action_verbs": {"move": {"tags": [], "risk": 0.1, "check": False}},
        "options": [],
    }
    view.update(overrides)
    return view


def _npc(place="gate", coin=20, stock=None, obligations=None, knowledge=None,
         schedule=None, plan=None):
    npc = neura.empty_npc("npc-01", "sc-01")
    npc["location"]["place_id"] = place
    npc["resources"]["coin"] = coin
    npc["resources"]["stock"] = dict(stock or {})
    npc["resources"]["obligations"] = list(obligations or [])
    npc["knowledge"] = list(knowledge or [])
    npc["schedule"] = list(schedule or [])
    if plan is not None:
        npc["plan"] = dict(plan)
    return npc


def _option(option_id="op-1", **overrides):
    option = {
        "option_id": option_id,
        "verb": "move",
        "target_ref": None,
        "place_id": "market",
        "minutes": 10,
        "relieves": {},
        "aggravates": {},
        "risk": 0.1,
        "source": "rule",
    }
    option.update(overrides)
    return option


def _codes(result):
    return [entry["code"] for entry in result["rejected"]]


def check_hard_codes_are_fixed():
    """§5.2 的五个错误码逐字固定；`E_LAYER_BUDGET` 不属于「拒单码」。"""
    assert neura.HARD_CODES == ("E_DISTANCE", "E_TIME", "E_RESOURCE",
                                "E_LAW", "E_KNOWLEDGE")
    assert neura.E_DISTANCE == "E_DISTANCE"
    assert neura.E_TIME == "E_TIME"
    assert neura.E_RESOURCE == "E_RESOURCE"
    assert neura.E_LAW == "E_LAW"
    assert neura.E_KNOWLEDGE == "E_KNOWLEDGE"
    assert neura.E_LAYER_BUDGET == "E_LAYER_BUDGET"
    assert neura.E_LAYER_BUDGET not in neura.HARD_CODES
    assert neura.BAND_PASS == ("same", "adjacent", "near")


def check_hard_filter_keeps_feasible_option():
    """没有触犯任何约束的候选原样通过；缺 `laws` 不影响 `ok`。"""
    result = neura.hard_filter(_npc(), _view(options=[_option("op-ok")]), 600)
    assert result["ok"] is True
    assert [item["option_id"] for item in result["options"]] == ["op-ok"]
    assert result["rejected"] == []
    assert result["law_source"] == "none"
    assert result["ai_allowed"] is True
    assert result["options"][0]["source"] == "rule"


def check_distance_bands_and_travel_minutes():
    """§5.2 距离：`bands` 直通与 `travel_minutes ≤ minutes` 两条路都要能判。"""
    npc = _npc()
    view = _view()

    for band in ("same", "adjacent", "near"):
        view["bands"][("gate", "temple")] = band
        assert neura.check_distance(
            npc, _option(place_id="temple", minutes=1), view) is None, band

    view["bands"][("gate", "temple")] = "far"
    assert neura.check_distance(
        npc, _option(place_id="temple", minutes=45), view) is None, "走得完就该放行"
    too_short = neura.check_distance(
        npc, _option(place_id="temple", minutes=44), view)
    assert too_short["code"] == neura.E_DISTANCE
    assert too_short["ref"] == "temple"

    # 同一地点不必看 band。
    assert neura.check_distance(npc, _option(place_id="gate", minutes=0), view) is None

    # 两条路都没数据 → 按「不知道」拒，不猜。
    view["travel_minutes"].pop("temple")
    view["bands"].pop(("gate", "temple"))
    unknown = neura.check_distance(npc, _option(place_id="temple", minutes=120), view)
    assert unknown["code"] == neura.E_DISTANCE

    # 端到端：`far` 但分钟数够 → `hard_filter` 放行。
    far_view = _view(options=[_option("op-trek", place_id="temple", minutes=50)])
    far_view["bands"][("gate", "temple")] = "far"
    assert neura.hard_filter(npc, far_view, 600)["ok"] is True


def check_time_rejects_over_plan_budget():
    """§5.2 时间①：`option.minutes ≤ 当前计划剩余`。"""
    npc = _npc(plan={"plan_id": "pl-1", "until_minute": 630})
    view = _view()
    rejected = neura.check_time(npc, _option(minutes=40), view, 600)
    assert rejected["code"] == neura.E_TIME
    assert rejected["ref"] == "pl-1"
    assert neura.check_time(npc, _option(minutes=30), view, 600) is None
    # 无计划 → 不设限。
    assert neura.check_time(_npc(), _option(minutes=600), view, 600) is None


def check_time_rejects_non_interruptible_slot():
    """§5.2 时间②：与不可打断的日程冲突即拒；岗位内动作与可打断日程不拦。"""
    npc = _npc(schedule=[{"slot_id": "sl-guard", "from_tod_minute": 540,
                          "to_tod_minute": 600, "place_id": "gate",
                          "activity": "guard", "interruptible": False}])
    view = _view()

    rejected = neura.check_time(npc, _option(place_id="market", minutes=30), view, 550)
    assert rejected["code"] == neura.E_TIME
    assert rejected["ref"] == "sl-guard"
    # 就在岗位上做，不算冲突。
    assert neura.check_time(npc, _option(place_id="gate", minutes=30), view, 550) is None
    # 时间窗不重叠不拦（600 起）。
    assert neura.check_time(npc, _option(place_id="market", minutes=30), view, 600) is None
    # 可打断的日程不拦。
    npc["schedule"][0]["interruptible"] = True
    assert neura.check_time(npc, _option(place_id="market", minutes=30), view, 550) is None


def check_time_rejects_crossing_obligation_due():
    """§5.2 时间③：动作越过义务期限时只放行指向该义务的选项。"""
    npc = _npc(obligations=[{"to": "npc-09", "kind": "deliver", "due_minute": 600}])
    view = _view()
    rejected = neura.check_time(npc, _option(minutes=60), view, 570)
    assert rejected["code"] == neura.E_TIME
    assert rejected["ref"] == "npc-09"
    # 指向该义务 → 放行。
    assert neura.check_time(
        npc, _option(minutes=60, verb="fulfill_duty", target_ref="npc-09"),
        view, 570) is None
    # 期限前结束 → 放行。
    assert neura.check_time(npc, _option(minutes=30), view, 570) is None


def check_resource_rejects_short_coin_and_stock():
    """§5.2 资源：钱与存货任一项不足即 `E_RESOURCE`。"""
    npc = _npc(coin=5, stock={"bread": 1})
    view = _view()

    rejected = neura.check_resource(npc, _option(cost={"coin": 10}), view)
    assert rejected["code"] == neura.E_RESOURCE
    assert neura.check_resource(
        npc, _option(cost={"coin": 5, "stock": {"bread": 1}}), view) is None

    short = neura.check_resource(npc, _option(cost={"stock": {"bread": 2}}), view)
    assert short["code"] == neura.E_RESOURCE
    assert short["ref"] == "bread"


def check_resource_duty_only_when_overdue():
    """§5.2 资源：义务到期未清 → 只允许 `fulfill_duty` 类（未到期不拦）。"""
    npc = _npc(obligations=[{"to": "npc-09", "kind": "deliver", "due_minute": 500}])
    view = _view(now_minute=600)

    rejected = neura.check_resource(npc, _option(), view)
    assert rejected["code"] == neura.E_RESOURCE
    assert rejected["ref"] == "npc-09"
    assert neura.check_resource(
        npc, _option(verb="fulfill_duty", target_ref="npc-09"), view) is None

    view["now_minute"] = 400
    assert neura.check_resource(npc, _option(), view) is None


def check_law_rejects_forbidden_tag_in_scope():
    """§5.2 法律：标签命中且落在管辖区 → `E_LAW`；范围外 / 不命中不拦。"""
    npc = _npc()
    law = {"id": "law-arms", "forbid_tags": ["weapons"],
           "scope_place_ids": ["market"], "threshold": "strict"}
    view = _view(laws=[law], law_source="world")

    rejected = neura.check_law(npc, _option(tags=["weapons"]), view)
    assert rejected["code"] == neura.E_LAW
    assert rejected["ref"] == "law-arms"
    assert neura.check_law(npc, _option(place_id="gate", tags=["weapons"]), view) is None
    assert neura.check_law(npc, _option(tags=["food"]), view) is None

    result = neura.hard_filter(
        npc, _view(laws=[law], law_source="world",
                   options=[_option("op-law", tags=["weapons"])]), 600)
    assert result["ok"] is False
    assert _codes(result) == [neura.E_LAW]
    assert result["law_source"] == "world"


def check_law_missing_source_is_none():
    """§12.1 P8：`view` 缺 `laws` → `law_source == "none"`，不抛错、不误拒。"""
    npc = _npc()
    view = _view(options=[_option("op-ok")])
    view.pop("laws")
    view.pop("law_source")

    result = neura.hard_filter(npc, view, 600)
    assert result["law_source"] == "none"
    assert result["ok"] is True
    assert result["rejected"] == []

    # 显式空表同样「不设限」。
    empty = neura.hard_filter(
        npc, _view(laws=[], law_source="none", options=[_option("op-ok")]), 600)
    assert empty["law_source"] == "none"
    assert empty["ok"] is True
    # 有表时按声明。
    declared = neura.hard_filter(
        npc, _view(laws=[], law_source="rules", options=[_option("op-ok")]), 600)
    assert declared["law_source"] == "rules"


def check_law_threshold_marks_risk_instead_of_reject():
    """§5.2：`threshold` 允许时改为「高风险但可行」，不拒选项。"""
    law = {"id": "law-curfew", "forbid_tags": ["night"],
           "scope_place_ids": ["market"], "threshold": "allow"}
    result = neura.hard_filter(
        _npc(), _view(laws=[law], law_source="world",
                      options=[_option("op-night", tags=["night"])]), 600)
    assert result["ok"] is True
    assert result["rejected"] == []
    assert result["options"][0]["law_risk"] == ["law-curfew"]


def check_knowledge_requires_active_claim_and_known_place():
    """§5.2 知识：`requires_claims` 须 active；`requires_place` 须已知。"""
    npc = _npc(knowledge=[
        {"claim_id": "cl-1", "cls": "belief", "proposition": "集市今天早开",
         "status": "active"},
        {"claim_id": "cl-2", "cls": "rumor", "proposition": "神庙夜里有人",
         "status": "disproved"},
    ])
    view = _view()

    assert neura.check_knowledge(npc, _option(requires_claims=["cl-1"]), view) is None
    missing = neura.check_knowledge(npc, _option(requires_claims=["cl-9"]), view)
    assert missing["code"] == neura.E_KNOWLEDGE
    stale = neura.check_knowledge(npc, _option(requires_claims=["cl-2"]), view)
    assert stale["code"] == neura.E_KNOWLEDGE
    unknown = neura.check_knowledge(npc, _option(requires_place=["temple"]), view)
    assert unknown["code"] == neura.E_KNOWLEDGE
    assert neura.check_knowledge(npc, _option(requires_place=["market"]), view) is None


def check_hard_filter_reports_each_code():
    """五个码各有一条**必然命中**的端到端断言。"""
    cases = (
        (neura.E_DISTANCE, _npc(),
         _view(options=[_option("op-far", place_id="temple", minutes=5)])),
        (neura.E_TIME, _npc(plan={"plan_id": "pl-1", "until_minute": 605}),
         _view(options=[_option("op-long", minutes=60)])),
        (neura.E_RESOURCE, _npc(coin=0),
         _view(options=[_option("op-poor", cost={"coin": 3})])),
        (neura.E_LAW, _npc(),
         _view(laws=[{"id": "law-1", "forbid_tags": ["weapons"],
                      "scope_place_ids": ["market"], "threshold": "strict"}],
               law_source="world",
               options=[_option("op-law", tags=["weapons"])])),
        (neura.E_KNOWLEDGE, _npc(),
         _view(options=[_option("op-blind", requires_claims=["cl-x"])])),
    )
    for code, npc, view in cases:
        result = neura.hard_filter(npc, view, 600)
        assert result["ok"] is False, code
        assert _codes(result) == [code], code
        assert result["rejected"][0]["option_id"].startswith("op-"), code


def check_hard_filter_all_rejected():
    """全灭：`{"ok": False, "options": []}`，且每个候选都留码不吞。"""
    npc = _npc(coin=0)
    options = [
        _option("op-poor", place_id="market", cost={"coin": 5}),
        _option("op-far", place_id="temple", minutes=5),
    ]
    result = neura.hard_filter(npc, _view(options=options), 600)
    assert result["ok"] is False
    assert result["options"] == []
    assert len(result["rejected"]) == 2
    for entry in result["rejected"]:
        assert entry["code"] in neura.HARD_CODES
        assert entry["option_id"]
        assert isinstance(entry["detail"], str) and entry["detail"]
    assert {(entry["option_id"], entry["code"]) for entry in result["rejected"]} == {
        ("op-poor", neura.E_RESOURCE),
        ("op-far", neura.E_DISTANCE),
    }


def check_hard_filter_rejects_malformed_option():
    """形状不合法的候选也带码进 `rejected`，不许静默丢弃。"""
    result = neura.hard_filter(_npc(), _view(options=[
        {"option_id": "op-bad-minutes", "verb": "move", "place_id": "market",
         "minutes": "10"},
        {"option_id": "op-ok", "verb": "move", "place_id": "market",
         "minutes": 10, "source": "rule"},
    ]), 600)
    assert [entry["option_id"] for entry in result["rejected"]] == ["op-bad-minutes"]
    assert result["rejected"][0]["code"] == neura.E_OPTION
    assert [item["option_id"] for item in result["options"]] == ["op-ok"]
    assert result["ok"] is True


def check_layer_budget_blocks_ai_not_rules():
    """§5.2 / §8.2：额度用尽只禁 AI 候选，规则候选照常出现。"""
    npc = _npc()
    view = _view(options=[_option("op-rule")],
                 budget={"day": 0, "strong_calls": neura.STRONG_CALLS_PER_DAY,
                         "light_calls": 0, "tokens": 24000})

    result = neura.hard_filter(npc, view, 600)
    assert result["ai_allowed"] is False
    assert [note["code"] for note in result["notes"]] == [neura.E_LAYER_BUDGET]
    assert result["rejected"] == [], "E_LAYER_BUDGET 不得拒规则候选"
    assert result["ok"] is True
    assert [item["option_id"] for item in result["options"]] == ["op-rule"]

    # 轻模型额度用尽同口径。
    view["budget"].update({"strong_calls": 0,
                           "light_calls": neura.LIGHT_CALLS_PER_DAY})
    assert neura.hard_filter(npc, view, 600)["ai_allowed"] is False

    # 跨日重置（§8.2）。
    assert neura.hard_filter(npc, view, 1440)["ai_allowed"] is True

    # 无账本 → 不在这里限流。
    view.pop("budget")
    assert neura.hard_filter(npc, view, 600)["ai_allowed"] is True


def check_hard_filter_is_pure():
    """纯函数：入参深比较前后相等；期间不读盘、不建 socket；返回的是副本。"""
    npc = _npc(coin=5, stock={"bread": 1},
               obligations=[{"to": "npc-09", "kind": "deliver", "due_minute": 700}],
               knowledge=[{"claim_id": "cl-1", "cls": "belief",
                           "proposition": "集市今天早开", "status": "active"}])
    view = _view(
        laws=[{"id": "law-1", "forbid_tags": ["weapons"],
               "scope_place_ids": ["market"], "threshold": "strict"}],
        law_source="world",
        options=[_option("op-ok"), _option("op-law", tags=["weapons"])])
    npc_before = copy.deepcopy(npc)
    view_before = copy.deepcopy(view)

    real_open, real_socket = builtins.open, socket.socket

    def guard_open(*args, **kwargs):
        raise AssertionError("hard_filter 不应读盘：%r" % (args[:1],))

    def guard_socket(*args, **kwargs):
        raise AssertionError("hard_filter 不应联网")

    builtins.open = guard_open
    socket.socket = guard_socket
    try:
        first = neura.hard_filter(npc, view, 600)
        second = neura.hard_filter(npc, view, 600)
    finally:
        builtins.open = real_open
        socket.socket = real_socket

    assert npc == npc_before, "hard_filter 改写了 npc"
    assert view == view_before, "hard_filter 改写了 view"
    assert json.dumps(first, sort_keys=True, ensure_ascii=False) == \
        json.dumps(second, sort_keys=True, ensure_ascii=False), "同输入不同输出"

    # 返回的候选是深拷贝：下游加字段不会漏回 view。
    assert first["options"][0]["option_id"] == "op-ok"
    first["options"][0]["minutes"] = 999
    first["options"][0]["relieves"]["safety"] = 1.0
    assert view["options"][0]["minutes"] == 10
    assert "safety" not in view["options"][0]["relieves"]


def main():
    checks = (
        check_constants,
        check_empty_block_json_roundtrip,
        check_empty_block_fresh_defaults,
        check_empty_npc_shape,
        check_migrate_fills_missing_needs,
        check_migrate_preserves_legal_values,
        check_migrate_dirty_needs_to_default,
        check_migrate_rejects_higher_version,
        check_migrate_missing_and_dirty_block,
        check_migrate_does_not_mutate_input,
        check_prune_caps_and_keeps_newest,
        check_prune_is_idempotent,
        check_prune_tie_break_is_deterministic,
        check_prune_leaves_small_blocks_alone,
        check_prune_does_not_mutate_input,
        check_stable_unit_determinism,
        check_stable_unit_distribution,
        check_module_is_pure,
        check_hard_codes_are_fixed,
        check_hard_filter_keeps_feasible_option,
        check_distance_bands_and_travel_minutes,
        check_time_rejects_over_plan_budget,
        check_time_rejects_non_interruptible_slot,
        check_time_rejects_crossing_obligation_due,
        check_resource_rejects_short_coin_and_stock,
        check_resource_duty_only_when_overdue,
        check_law_rejects_forbidden_tag_in_scope,
        check_law_missing_source_is_none,
        check_law_threshold_marks_risk_instead_of_reject,
        check_knowledge_requires_active_claim_and_known_place,
        check_hard_filter_reports_each_code,
        check_hard_filter_all_rejected,
        check_hard_filter_rejects_malformed_option,
        check_layer_budget_blocks_ai_not_rules,
        check_hard_filter_is_pure,
    )
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败 [%s]: %s" % (check.__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks), failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
