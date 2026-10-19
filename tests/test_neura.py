#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neura 内核（M11-N1 骨架 + M11-N2 硬约束 + N3 评分 + N4 知识边界 + N5 分层模拟）
回归测试。

逐条覆盖 `Issue #339`（N1）与 `Issue #348`（N2）的验收清单：

N1：

- `empty_block()` 经 `json.dumps` → `json.loads` 往返逐字段相等；
- 缺 `needs` 六键的旧块 → `migrate_block` 补 50；已有合法值（如 73）保持；
- `block["version"] > NEURA_VERSION` → 抛 `NeuraError`（有断言）；
- `prune_block` 对 300 条 journal / 200 条 memory 裁到上限，连续两次结果全等；
- `stable_unit("a","b")` 确定、不同输入不同输出、100 万次分布均匀（±1%）；
- `import neura` 无 IO、无副作用，未引入 `state` / `chron` / `notdnd_web` /
  `prism_core` / `prism_guide` / `atlas`。

N3：

- 六维 `terms` 逐项可复算、`Σ product == raw`（±1e-9），`risk_penalty` /
  `time_penalty` / `habit_bonus` / `jitter` 逐项对公式；
- 同输入连跑 10 次，决策记录**逐字节相等**；`jitter` 可由 `stable_unit` 重算，
  且不落 `roll_refs` / 不落独立骰点字段；
- 三个同分候选按 `(score, −risk, option_id)` 稳定取一，**与输入顺序无关**；
- `switch_reason` 六值全覆盖；同目标续计划 / 首次决策为 `None`；
- 无候选 → `chosen is None` + `hard_blocked`，仍有一条可写 journal 的记录；
- `to_proposal` 与 `ACTION-DESIGN.md` §4.2 的 14 个字段逐项对齐（字段名 / 类型 /
  必填性），并过 `prism_core.validate_proposal`（子进程调用，不污染 import 边界）；
- 幂等键 `^[A-Za-z0-9_-]{8,64}$`，同计划同步骤恒定；
- `decide` / `to_proposal` 纯函数：入参深比较不变、无 IO。

N2：

- 五个 `E_*` 码各有一条**真实断言**（构造必然命中的输入，而不是空跑）；
- `view` 缺 `laws` → `law_source == "none"`，`ok` 不受影响、不抛错（P8）；
- 距离两条路都判（`bands` 直通 / `far` 但 `minutes` 够）；
- 全灭 → `{"ok": False, "options": []}`，`rejected` 每项都有 `code` + `option_id`；
- `E_LAYER_BUDGET` 只禁 AI 候选，规则候选仍出现；
- 纯函数：入参深比较前后相等，期间未调用 `open` / 未建 socket。

N4（`Issue #357`）：

- 五类分离：`belief` / `rumor` / `inference` / `secret` **逐类**断言不进 `world_facts`
  投影，且其命题的任一 8 码位窗口都不出现在上下文里；
- §6.3 的双 NPC 例子：`npc-01` 可见秘密全文，`npc-05` **不含**该全文的任一 8 码位窗口；
- `purpose` 三值的字段集用**集合相等**断言（不是子集），且三值互不相同；
- 修正链：旧 claim `status="disproved"` + `superseded_by`，`resolve_claim(旧 id)` 仍可解析；
- `select_context` 输出不含其他 NPC 的 `proposition`、不含 `secrets[].proposition`；
- `neura.py` 里 role 键字面量只出现一次（唯一拼 `messages` 处的源码断言）。

N5（`Issue #361`）：

- 四层判定按 §7.1 短路顺序；玩家走远一步 → 同一 tick 内可见降层；
- 日预算公式 `4000*h + 1200*m ≤ 60000`（P4 反解 `h ≤ 6`、`m ≤ 30`）；
  跨日（`day` 变化）计数归零；
- 80% / 100% 两档降级各一条断言；降级单调不回弹；
- `catch_up` 上限 `MAX_CATCHUP_STEPS = 8`；超出记 `catchup_deferred`；
  所有到期计划都被收敛（断言终态）；幂等重跑不重复记账；
- 无 AI 配置下仍产生日程移动提案（≥ 1 条 / 日 / 有日程 NPC）；
- 单 tick 突发上限 ≤ 1 次 H、≤ 3 次 M；
- `tick` 不返回事件、不改入参；假 `port` 的提交键与 `idempotency_key` 一致。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_neura.py` 运行。
"""

import ast
import builtins
import copy
import hashlib
import itertools
import json
import os
import re
import socket
import subprocess
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


# --------------------------------------------------------------------------
# §5.3 / §5.4 / §5.6：需求评分 → 计划选择 → 决策记录 → M8 提案（M11-N3）
# --------------------------------------------------------------------------

SEED = 20261018
NOW = 600
OLD_GOAL = "g-old"
NEW_GOAL = "g-new"

# ACTION-DESIGN.md §4.2 的字段表（字段名 / 类型 / 必填性）——逐项对照用。
ACTION_4_2_FIELDS = (
    ("schema_version", int, True),
    ("proposal_id", str, True),
    ("actor", dict, True),
    ("intent", dict, True),
    ("params", dict, False),
    ("source", str, True),
    ("risk", str, True),
    ("time_cost", dict, True),
    ("schedule_hint", dict, False),
    ("interruptible", bool, True),
    ("interrupt_conditions", list, False),
    ("expected_changes", list, False),
    ("raw_text", str, False),
    ("created_at", int, True),
)

KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
PROPOSAL_ID_RE = re.compile(r"^p_[0-9a-f]{32}$")


def _scoring_npc():
    """§5.3 评分夹具：六维需求 / 权重各不相同，日程给出 home_place=forge。"""
    npc = _npc()
    npc["needs"] = {"safety": 80, "wealth": 40, "power": 20,
                    "relation": 60, "duty": 100, "curiosity": 10}
    npc["need_weights"] = {"safety": 0.9, "wealth": 0.5, "power": 0.1,
                           "relation": 0.7, "duty": 0.3, "curiosity": 0.2}
    npc["schedule"] = [{"slot_id": "sl-forge", "from_tod_minute": 480,
                        "to_tod_minute": 720, "place_id": "forge",
                        "activity": "work", "interruptible": True}]
    return npc


def _goal(goal_id, status="active", deadline=None):
    return {"goal_id": goal_id, "kind": "belong", "target_ref": "",
            "weight": 1.0, "deadline_minute": deadline, "status": status}


def _switch_case(goal_status="active", plan_status="running", deadline=None,
                 knowledge=None, requires_claims=None, new_goal=NEW_GOAL):
    """构造「旧计划在跑、新候选指向另一个目标」的一次决策输入。"""
    npc = _npc(plan={"plan_id": "pl-old", "goal_id": OLD_GOAL, "step_index": 1,
                     "status": plan_status, "started_minute": 590,
                     "until_minute": 700})
    npc["goals"] = [_goal(OLD_GOAL, goal_status, deadline)]
    npc["knowledge"] = list(knowledge or [])
    option = _option("op-new", goal_id=new_goal, minutes=10,
                     requires_claims=list(requires_claims or []))
    view = _view(options=[option], campaign_seed=SEED)
    return npc, view


def _proposal_fixture():
    """一次「续计划」的决策：旧 plan 在跑、候选带目标与目标实体。"""
    npc = _npc(plan={"plan_id": "pl-9", "goal_id": "g-1", "step_index": 2,
                     "status": "running", "started_minute": 590,
                     "until_minute": 700})
    npc["goals"] = [_goal("g-1")]
    npc["relations"] = {"npc-09": {"trust": 10}}
    view = _view(options=[_option("op-9", verb="deliver", goal_id="g-1",
                                  target_ref="npc-09", minutes=15, risk=0.5)],
                 campaign_seed=SEED)
    return npc, view


def check_score_terms_recompute():
    """§5.3：六维 `terms` 逐项对公式，`Σ product == raw`，四项扣分/加成逐项可复算。"""
    npc = _scoring_npc()
    option = _option("op-forge", place_id="forge", minutes=90, risk=0.5,
                     relieves={"duty": 0.8, "safety": 0.4},
                     aggravates={"curiosity": 0.2})
    scores = neura.score_options(npc, [option], seed=SEED, now_minute=NOW)
    assert len(scores) == 1
    record = scores[0]

    # 六维全在、顺序固定。
    assert [term["need"] for term in record["terms"]] == list(neura.NEED_KEYS)
    for term in record["terms"]:
        assert abs(term["product"] -
                   term["weight"] * term["level"] * term["sat"]) <= 1e-12

    by_need = {term["need"]: term for term in record["terms"]}
    assert abs(by_need["safety"]["level"] - 0.8) < 1e-12
    assert abs(by_need["safety"]["weight"] - 0.9) < 1e-12
    assert abs(by_need["safety"]["sat"] - 0.4) < 1e-12
    assert abs(by_need["safety"]["product"] - 0.288) < 1e-12
    assert abs(by_need["duty"]["product"] - 0.24) < 1e-12
    assert abs(by_need["curiosity"]["sat"] + 0.2) < 1e-12
    assert abs(by_need["curiosity"]["product"] + 0.004) < 1e-12
    assert abs(by_need["wealth"]["sat"]) < 1e-12

    # Σ product == raw（±1e-9，验收口径）。
    total = 0.0
    for term in record["terms"]:
        total += term["product"]
    assert abs(total - record["raw"]) <= 1e-9, (total, record["raw"])
    assert abs(record["raw"] - 0.524) <= 1e-9

    # 四项逐项对公式。
    assert abs(neura.risk_aversion(npc) - 0.74) < 1e-12
    assert abs(record["risk_penalty"] - 0.37) < 1e-12
    assert abs(record["time_penalty"] - 0.04) < 1e-12      # ceil(90/60) = 2 小时
    assert abs(record["penalty"] -
               (record["risk_penalty"] + record["time_penalty"])) < 1e-12
    assert abs(record["habit_bonus"] - 0.05) < 1e-12, "place == home_place 该有加成"
    expected_jitter = 0.03 * (2.0 * neura.stable_unit(
        SEED, "npc-01", NOW, "op-forge") - 1.0)
    assert abs(record["jitter"] - expected_jitter) < 1e-15
    assert abs(record["score"] - (record["raw"] - record["penalty"] +
                                  record["habit_bonus"] + record["jitter"])) <= 1e-12

    # 非 home_place 无加成；无日程无加成。
    other = neura.score_options(npc, [_option("op-market", minutes=0)],
                                seed=SEED, now_minute=NOW)[0]
    assert other["habit_bonus"] == 0.0
    assert other["time_penalty"] == 0.0
    no_schedule = neura.score_options(_npc(), [_option("op-forge", place_id="forge")],
                                      seed=SEED, now_minute=NOW)[0]
    assert no_schedule["habit_bonus"] == 0.0


def check_jitter_is_recomputable_and_not_dice():
    """§5.3：抖动只来自 `stable_unit`，可重算；不落 `roll_refs` / 独立骰点字段。"""
    npc, view = _proposal_fixture()
    decision = neura.decide(npc, view)
    chosen = decision["chosen"]
    expected = 0.03 * (2.0 * neura.stable_unit(
        SEED, "npc-01", NOW, chosen["option_id"]) - 1.0)
    assert abs(chosen["jitter"] - expected) < 1e-15
    assert -0.03 <= chosen["jitter"] < 0.03

    payload = json.dumps(decision, ensure_ascii=False)
    assert "roll_refs" not in payload, "抖动不进 roll_refs"
    assert "dice" not in payload and "rng" not in payload
    # 抖动只出现在打分记录里（可直接重算的那种），不是独立账本字段。
    assert "jitter" not in decision
    assert all("jitter" not in score or isinstance(score["jitter"], float)
               for score in decision["scores"])


def check_choose_tie_break_is_order_independent():
    """§5.4：同分按 `(score, −risk, option_id)` 取一，输入顺序不影响结果。"""
    scores = [
        {"option_id": "op-b", "score": 1.0, "risk": 0.4},
        {"option_id": "op-a", "score": 1.0, "risk": 0.4},
        {"option_id": "op-c", "score": 1.0, "risk": 0.2},
    ]
    winners = set()
    for order in itertools.permutations(scores):
        winners.add(neura.choose(list(order))["option_id"])
    assert winners == {"op-c"}, "同分应先取 risk 最低的：%r" % (winners,)

    # risk 也相同 → 取 option_id 字典序最小。
    same_risk = [dict(item, risk=0.3) for item in scores]
    winners = {neura.choose(list(order))["option_id"]
               for order in itertools.permutations(same_risk)}
    assert winners == {"op-a"}, winners

    # 高分优先于低 risk。
    mixed = [{"option_id": "op-z", "score": 2.0, "risk": 0.9},
             {"option_id": "op-a", "score": 1.0, "risk": 0.0}]
    assert neura.choose(mixed)["option_id"] == "op-z"
    assert neura.choose([]) is None
    assert neura.choose([{"option_id": "op-x"}]) is None


def check_decide_is_deterministic():
    """§5.3 / §10.1：同输入连跑 10 次，决策记录**逐字节相等**。"""
    npc, view = _proposal_fixture()
    first = json.dumps(neura.decide(npc, view), ensure_ascii=False, sort_keys=True)
    for _ in range(9):
        again = json.dumps(neura.decide(npc, view), ensure_ascii=False,
                           sort_keys=True)
        assert again == first, "同输入不同输出"
    # 决策记录可 JSON 序列化（journal 要落盘）。
    decoded = _json_roundtrip(neura.decide(npc, view))
    assert decoded == _json_roundtrip(neura.decide(npc, view))
    # 换分钟 → 抖动变，但仍在区间内（证明抖动确实参与）。
    view_later = _view(options=view["options"], campaign_seed=SEED,
                       now_minute=601)
    npc_later = _npc(place="gate")
    npc_later["goals"] = [_goal("g-1")]
    later = neura.decide(npc_later, view_later)
    assert later["scores"][0]["jitter"] != json.loads(first)["scores"][0]["jitter"]


def check_decide_record_fields():
    """§5.4：决策记录恰好十个字段（多一个少一个都算漂移）。"""
    npc, view = _proposal_fixture()
    decision = neura.decide(npc, view)
    assert set(decision) == {"decision_id", "npc_id", "at_minute", "layer",
                             "hard", "scores", "chosen", "switch_reason",
                             "cost", "ai_used"}
    assert decision["npc_id"] == "npc-01"
    assert decision["at_minute"] == NOW
    assert decision["layer"] in neura.LAYERS
    assert set(decision["hard"]) == {"ok", "kept", "rejected", "law_source",
                                     "ai_allowed", "notes"}
    assert decision["hard"]["ok"] is True
    assert decision["hard"]["kept"] == 1
    assert set(decision["cost"]) == {"tier", "tokens"}
    assert decision["cost"] == {"tier": "rule", "tokens": 0}
    assert decision["ai_used"] is False
    assert decision["chosen"]["option_id"] == "op-9"
    # AI 候选参评才记 ai_used / 成本档（§8.2）。
    ai_view = _view(options=[_option("op-ai", source="ai")], campaign_seed=SEED)
    npc_ai = _npc()
    ai = neura.decide(npc_ai, ai_view)
    assert ai["ai_used"] is True
    assert ai["cost"] == {"tier": "M", "tokens": 1200}
    declared = neura.decide(npc_ai, _view(options=[_option("op-ai", source="ai")],
                                         campaign_seed=SEED, cost_tier="H",
                                         ai_tokens=3900))
    assert declared["cost"] == {"tier": "H", "tokens": 3900}


def check_switch_reason_six_values():
    """§5.4：目标切换必带六值之一；同目标续计划 / 首次决策为 `None`。"""
    seen = set()

    npc, view = _switch_case(goal_status="done")
    reason = neura.decide(npc, view)["switch_reason"]
    assert reason == "goal_done", reason
    seen.add(reason)

    npc, view = _switch_case(goal_status="failed")
    reason = neura.decide(npc, view)["switch_reason"]
    assert reason == "goal_failed", reason
    seen.add(reason)

    npc, view = _switch_case(plan_status="dropped")
    reason = neura.decide(npc, view)["switch_reason"]
    assert reason == "hard_blocked", reason
    seen.add(reason)

    npc, view = _switch_case(deadline=NOW)
    reason = neura.decide(npc, view)["switch_reason"]
    assert reason == "deadline_pressure", reason
    seen.add(reason)

    npc, view = _switch_case(
        knowledge=[{"claim_id": "cl-new", "cls": "belief",
                    "proposition": "码头夜里有船", "status": "active",
                    "learned_minute": 595}],
        requires_claims=["cl-new"])
    reason = neura.decide(npc, view)["switch_reason"]
    assert reason == "newly_known", reason
    seen.add(reason)

    npc, view = _switch_case()
    reason = neura.decide(npc, view)["switch_reason"]
    assert reason == "need_spike", reason
    seen.add(reason)

    assert seen == set(neura.SWITCH_REASONS), seen

    # 同目标续计划 → None。
    npc, view = _switch_case(new_goal=OLD_GOAL)
    assert neura.decide(npc, view)["switch_reason"] is None
    # 首次决策（没有旧计划）→ None，不是「切换」。
    fresh = _npc()
    fresh["goals"] = [_goal(NEW_GOAL)]
    fresh_view = _view(options=[_option("op-first", goal_id=NEW_GOAL)],
                       campaign_seed=SEED)
    assert neura.decide(fresh, fresh_view)["switch_reason"] is None
    # 计划已完成（status=done）同样算 goal_done。
    npc, view = _switch_case(plan_status="done")
    assert neura.decide(npc, view)["switch_reason"] == "goal_done"


def check_no_candidates_is_recorded():
    """§5.4：无候选 → `chosen is None` + `hard_blocked`，仍有一条 journal 记录。"""
    npc = _npc(coin=0)
    view = _view(options=[_option("op-poor", cost={"coin": 5})],
                 campaign_seed=SEED)
    decision = neura.decide(npc, view)
    assert decision["chosen"] is None
    assert decision["switch_reason"] == "hard_blocked"
    assert decision["scores"] == []
    assert decision["hard"]["ok"] is False
    assert [item["code"] for item in decision["hard"]["rejected"]] == [
        neura.E_RESOURCE]
    assert decision["decision_id"].startswith("dec_")
    assert _json_roundtrip(decision) == decision, "无候选的记录也要能落 journal"

    # 没有候选池（view 缺 options）同口径，不抛异常。
    bare = neura.decide(npc, {"now_minute": NOW, "campaign_seed": SEED})
    assert bare["chosen"] is None
    assert bare["switch_reason"] == "hard_blocked"

    # 无候选时不该生成提案 / 幂等键（调用方顺序错误）。
    for call in (lambda: neura.to_proposal(decision, npc),
                 lambda: neura.idempotency_key(decision, npc)):
        try:
            call()
        except neura.NeuraError:
            pass
        else:
            raise AssertionError("无选中计划时不应产出提案 / 幂等键")


def check_to_proposal_matches_action_design():
    """§5.6 / ACTION-DESIGN §4.2：14 个字段逐项对齐（名字 / 类型 / 必填性）。"""
    npc, view = _proposal_fixture()
    decision = neura.decide(npc, view)
    proposal = neura.to_proposal(decision, npc)

    assert set(proposal) == {name for name, _, _ in ACTION_4_2_FIELDS},         "提案字段集与 §4.2 不一致：%r" % (sorted(set(proposal) -
                                          {name for name, _, _ in ACTION_4_2_FIELDS}),)
    for name, kind, _required in ACTION_4_2_FIELDS:
        value = proposal[name]
        assert isinstance(value, kind), "%s 类型应为 %s，实际 %r" % (
            name, kind.__name__, type(value).__name__)
        if kind is int:
            assert not isinstance(value, bool), "%s 不该是 bool" % name

    assert proposal["schema_version"] == 1
    assert PROPOSAL_ID_RE.match(proposal["proposal_id"]), proposal["proposal_id"]
    assert proposal["actor"] == {"kind": "npc", "id": "npc-01"}
    assert proposal["intent"]["verb"] == "deliver"
    assert proposal["intent"]["target"] == {"kind": "npc", "id": "npc-09"}
    assert proposal["intent"]["desired"], "desired 必填且非空"
    assert isinstance(proposal["intent"]["means"], str)
    assert proposal["params"] == {"dc_override": None, "items_used": []}
    assert proposal["source"] == "action", "NPC 提案的 source 是 action（§4.2）"
    assert proposal["risk"] == "R2", proposal["risk"]      # 0.5 ∈ [0.4, 0.6)
    assert proposal["time_cost"] == {"value": 15, "unit": "minute"}
    assert proposal["schedule_hint"] == {"can_defer": False, "depends_on": [],
                                         "priority": 0}
    assert proposal["interruptible"] is True
    assert proposal["interrupt_conditions"] == []
    assert proposal["expected_changes"] == []
    assert proposal["raw_text"] == ""
    assert proposal["created_at"] == NOW * 60000, "created_at 由世界分钟换算，不取墙钟"
    assert _json_roundtrip(proposal) == proposal

    # 无目标实体 → intent.target 为 null（§4.2：视动作）。
    npc2 = _npc()
    view2 = _view(options=[_option("op-2", minutes=10)], campaign_seed=SEED)
    proposal2 = neura.to_proposal(neura.decide(npc2, view2), npc2)
    assert proposal2["intent"]["target"] is None


def check_proposal_passes_prism_validate():
    """§5.6：`to_proposal` 的输出过 `prism_core.validate_proposal`（真跑子进程）。

    用子进程而不是 `import prism_core`：`neura` 的依赖边界断言
    （`check_module_is_pure`）要求测试进程里不出现业务模块。
    """
    if not os.path.exists(os.path.join(ROOT, "prism_core.py")):
        print("  跳过：prism_core.py 不存在（只做 §4.2 字段对齐）")
        return
    npc, view = _proposal_fixture()
    proposal = neura.to_proposal(neura.decide(npc, view), npc)
    code = ("import json, sys\n"
            "import prism_core\n"
            "print(json.dumps(prism_core.validate_proposal("
            "json.loads(sys.stdin.read()))))\n")
    proc = subprocess.run([sys.executable, "-c", code],
                          input=json.dumps(proposal, ensure_ascii=False),
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    errors = json.loads(proc.stdout)
    assert errors == [], errors


def check_idempotency_key_format_and_stability():
    """§5.6：幂等键格式合规，**同计划同步骤恒定**（换分钟也不变）。"""
    npc, view = _proposal_fixture()
    decision = neura.decide(npc, view)
    key = neura.idempotency_key(decision, npc)
    assert KEY_RE.match(key), key
    assert len(key) <= 64
    npc_hex = hashlib.sha256(b"npc-01").hexdigest()[:8]
    plan_hex = hashlib.sha256(b"pl-9").hexdigest()[:10]
    assert key == "np_%s_%s_02" % (npc_hex, plan_hex), key

    later = neura.decide(npc, _view(options=view["options"], campaign_seed=SEED,
                                    now_minute=NOW + 5))
    assert neura.idempotency_key(later, npc) == key, "同计划同步骤应恒定"
    assert neura.to_proposal(later, npc)["proposal_id"] ==         neura.to_proposal(decision, npc)["proposal_id"]

    # 换到新计划（旧计划没有）→ 新键、步骤从 0 起，仍然合法。
    fresh = _npc()
    fresh["goals"] = [_goal(NEW_GOAL)]
    fresh_decision = neura.decide(
        fresh, _view(options=[_option("op-n", goal_id=NEW_GOAL)],
                     campaign_seed=SEED))
    fresh_key = neura.idempotency_key(fresh_decision, fresh)
    assert KEY_RE.match(fresh_key), fresh_key
    assert fresh_key.endswith("_00")
    assert fresh_key != key


def check_decide_and_proposal_are_pure():
    """N3 纯函数：入参深比较不变、返回副本、不读盘不联网、同输入同输出。"""
    npc, view = _proposal_fixture()
    npc_before = copy.deepcopy(npc)
    view_before = copy.deepcopy(view)

    real_open, real_socket = builtins.open, socket.socket

    def guard_open(*args, **kwargs):
        raise AssertionError("decide / to_proposal 不应读盘：%r" % (args[:1],))

    def guard_socket(*args, **kwargs):
        raise AssertionError("decide / to_proposal 不应联网")

    builtins.open = guard_open
    socket.socket = guard_socket
    try:
        decision = neura.decide(npc, view)
        proposal = neura.to_proposal(decision, npc)
        again = neura.decide(npc, view)
    finally:
        builtins.open = real_open
        socket.socket = real_socket

    assert npc == npc_before, "decide 改写了 npc"
    assert view == view_before, "decide 改写了 view"
    assert json.dumps(decision, sort_keys=True, ensure_ascii=False) == \
        json.dumps(again, sort_keys=True, ensure_ascii=False)
    assert json.dumps(proposal, sort_keys=True, ensure_ascii=False) == \
        json.dumps(neura.to_proposal(again, npc), sort_keys=True,
                   ensure_ascii=False)

    # 返回的是副本：改决策记录不会漏回入参或候选池。
    decision["chosen"]["minutes"] = 999
    decision["scores"][0]["terms"][0]["product"] = 123.0
    assert view["options"][0]["minutes"] == 15
    assert npc["needs"]["safety"] == 50


# --------------------------------------------------------------------------
# §6 / §11 N4：知识边界（五类分离 / 唯一收口 / 修正链）
# --------------------------------------------------------------------------

SCENARIO_FILE = os.path.join(ROOT, "data", "scenarios", "ashen_crown.json")
SECRET_WINDOW = 8          # §6.3：连续 8 码位算「半个秘密」


def _raises_neura(call):
    """断言调用抛出 `NeuraError`（写入口的严格性都要有断言，不能只看文档）。"""
    try:
        call()
    except neura.NeuraError:
        return True
    return False


def _text_of(value):
    """确定性文本（键排序 + 不转义中文），用于「整份上下文里有没有漏」的断言。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _scenario_secret(npc_id="npc-01"):
    """§6.3 的原文：直接读既有剧本，不在这里复制一遍秘密文本。"""
    with open(SCENARIO_FILE, encoding="utf-8") as handle:
        data = json.load(handle)
    for npc in data["npcs"]:
        if npc["id"] == npc_id:
            return npc["secret"]
    raise AssertionError("data/scenarios/ashen_crown.json 里没有 %s" % npc_id)


def _windows(text, size=SECRET_WINDOW):
    """滚动 8 码位窗口：只查整句会让「半个秘密」漏过去（§6.3 的判据）。"""
    assert len(text) >= size, "秘密文本短于 %d 码位，窗口断言无意义" % size
    return [text[index:index + size] for index in range(len(text) - size + 1)]


def _leaked_windows(text, haystack):
    return [window for window in _windows(text) if window in haystack]


def _claim(proposition, cls="belief", confidence=62, **overrides):
    claim = {
        "cls": cls,
        "proposition": proposition,
        "confidence": confidence,
        "learned_minute": 17120,
        "source_kind": "rumor",
        "evidence_refs": [],
        "status": "active",
    }
    claim.update(overrides)
    return claim


def _knowledge_view(**overrides):
    """N4 只读的那部分 `view`：世界事实投影 + 其他角色塞进来的东西。"""
    view = {
        "campaign_id": "cp-01",
        "now_minute": 17120,
        "scenario_id": "sc-01",
        "world_facts": {
            "wf-gate": {"cls": "fact", "visibility": "public",
                        "proposition": "城门在子夜关闭"},
        },
        "known_places": {"gate": True},
        "options": [],
    }
    view.update(overrides)
    return view


def _knowledge_npc(npc_id="npc-01", **overrides):
    npc = neura.empty_npc(npc_id, "sc-01")
    npc["last_tick_minute"] = 17120
    npc.update(overrides)
    return npc


def check_claim_classes_and_purposes():
    """五类 / 两态 / 三 purpose / 三张字段集：常量与文档逐字对齐。"""
    assert neura.CLAIM_CLASSES == ("fact", "belief", "rumor", "inference",
                                   "secret")
    assert neura.CLAIM_CLASSES_NEVER_WORLD_FACT == ("belief", "rumor",
                                                    "inference", "secret")
    assert neura.CLAIM_STATUSES == ("active", "disproved")
    assert neura.PURPOSES == ("decide", "narrate", "react")
    assert set(neura.CONTEXT_FIELDS) == set(neura.PURPOSES)
    for purpose in neura.PURPOSES:
        fields = neura.CONTEXT_FIELDS[purpose]
        assert len(fields) == len(set(fields)), "%s 的字段集有重复" % purpose
        assert "purpose" in fields and "claims" in fields
        assert "world_facts" in fields and "secret_flags" in fields
    # 「固定」要能区分：三值字段集两两不同，否则集合断言等于没断言。
    for left, right in itertools.combinations(neura.PURPOSES, 2):
        assert set(neura.CONTEXT_FIELDS[left]) != set(neura.CONTEXT_FIELDS[right])


def check_learn_writes_active_claim():
    """`learn()`：派生 id、补 learned_minute、fact 置信恒 100、入参不改。"""
    npc = _knowledge_npc()
    before = copy.deepcopy(npc)
    result = neura.learn(npc, {
        "cls": "belief",
        "proposition": "守门人昨夜被换成了别人",
        "confidence": 62,
    })

    claim = result["claim"]
    assert result["merged"] is False
    assert claim["claim_id"].startswith("cl-")
    assert len(claim["claim_id"]) == len("cl-") + 8
    assert claim["status"] == "active"
    assert claim["learned_minute"] == 17120        # 缺省取 last_tick_minute
    assert claim["confidence"] == 62
    assert result["npc"]["knowledge"] == [claim]
    assert npc == before, "learn 改写了入参"
    assert _json_roundtrip(result["npc"]) == result["npc"], "knowledge 不可 JSON 往返"

    # 同内容同 id：id 由 (cls, proposition, about_ref, source_kind) 确定性派生。
    again = neura.learn(_knowledge_npc(), {
        "cls": "belief",
        "proposition": "守门人昨夜被换成了别人",
        "confidence": 90,
    })
    assert again["claim"]["claim_id"] == claim["claim_id"]

    # §6.1 硬规矩 3：fact 的 confidence 恒为 100，不采信调用方给的 40。
    fact = neura.learn(npc, {
        "cls": "fact",
        "proposition": "城门在子夜关闭",
        "confidence": 40,
        "learned_minute": 17181,
    })
    assert fact["claim"]["confidence"] == neura.FACT_CONFIDENCE == 100


def check_learn_merges_same_claim():
    """同一命题再学一次：置信取高、时间取早、证据取并集，不堆两条。"""
    first = neura.learn(_knowledge_npc(), _claim(
        "守门人昨夜被换成了别人", confidence=40, learned_minute=100,
        evidence_refs=["ev-2", "ev-1"]))
    second = neura.learn(first["npc"], _claim(
        "守门人昨夜被换成了别人", confidence=80, learned_minute=200,
        evidence_refs=["ev-1", "ev-3"]))

    assert second["merged"] is True
    assert len(second["npc"]["knowledge"]) == 1
    claim = second["claim"]
    assert claim["confidence"] == 80
    assert claim["learned_minute"] == 100
    assert claim["evidence_refs"] == ["ev-1", "ev-2", "ev-3"]
    assert first["npc"]["knowledge"][0]["confidence"] == 40, "合并改写了旧结果"


def check_learn_rejects_bad_claims():
    """写入口严格：命题 / 类别 / 置信 / 状态不合法一律 `NeuraError`。"""
    npc = _knowledge_npc()
    assert _raises_neura(lambda: neura.learn(npc, "不是对象"))
    assert _raises_neura(lambda: neura.learn(npc, {"cls": "belief",
                                                   "confidence": 60}))
    assert _raises_neura(lambda: neura.learn(npc, {
        "cls": "belief", "proposition": "   ", "confidence": 60}))
    assert _raises_neura(lambda: neura.learn(npc, {
        "cls": "gossip", "proposition": "听来的", "confidence": 60}))
    assert _raises_neura(lambda: neura.learn(npc, {
        "cls": "belief", "proposition": "听来的"}))
    assert _raises_neura(lambda: neura.learn(npc, {
        "cls": "belief", "proposition": "听来的", "confidence": 140}))
    assert _raises_neura(lambda: neura.learn(npc, {
        "cls": "belief", "proposition": "听来的", "confidence": 60,
        "status": "disproved"}))

    # 同 id 不同命题：id 不复用（§6.4）。
    learned = neura.learn(npc, _claim("守门人昨夜被换成了别人"))
    claim_id = learned["claim"]["claim_id"]
    assert _raises_neura(lambda: neura.learn(learned["npc"], {
        "claim_id": claim_id, "cls": "belief", "proposition": "另一件事",
        "confidence": 60}))


def check_supersede_chain():
    """§6.4 修正链：旧 claim disproved + superseded_by，旧 id 仍可解析。"""
    npc = _knowledge_npc("npc-05")
    old = neura.learn(npc, _claim(
        "守门人昨夜被换成了别人", cls="rumor", confidence=62,
        learned_minute=17120, evidence_refs=["ev-market-03"]))
    old_id = old["claim"]["claim_id"]
    before = copy.deepcopy(old["npc"])

    new_claim = {
        "cls": "fact",
        "proposition": "守门人今天清晨换了岗",
        "learned_minute": 17180,
        "evidence_refs": ["ev-gate-01"],
    }
    result = neura.supersede(old["npc"], old_id, new_claim, reason="亲眼看到换岗")
    new_id = result["new_claim"]["claim_id"]

    assert result["old_claim"]["status"] == "disproved"
    assert result["old_claim"]["superseded_by"] == new_id
    assert result["new_claim"]["status"] == "active"
    assert result["new_claim"]["confidence"] == 100     # fact
    assert old["npc"] == before, "supersede 改写了入参"
    assert len(result["npc"]["knowledge"]) == 2        # 旧记录不删

    resolved = neura.resolve_claim(result["npc"], old_id)
    assert resolved is not None
    assert resolved["status"] == "disproved"
    assert resolved["superseded_by"] == new_id
    assert resolved["proposition"] == "守门人昨夜被换成了别人"
    assert neura.resolve_claim(result["npc"], new_id)["status"] == "active"
    assert neura.resolve_claim(result["npc"], "cl-nope") is None

    memory = result["npc"]["memory"][-1]
    assert memory["kind"] == "correction"
    assert memory["claim_refs"] == [old_id, new_id]
    assert memory["summary"] == "亲眼看到换岗"
    assert memory["salience"] == neura.SALIENCE_CORRECTION
    assert "truncated" not in memory

    # 旧 id 不能二次修正；不存在的 id / 新旧同 id 都是调用方错误。
    assert _raises_neura(lambda: neura.supersede(
        result["npc"], old_id, _claim("又一次修正"), reason="再说一次"))
    assert _raises_neura(lambda: neura.supersede(
        result["npc"], "cl-nope", _claim("凭空修正"), reason="没有旧记录"))
    assert _raises_neura(lambda: neura.supersede(
        result["npc"], new_id,
        {"claim_id": new_id, "cls": "fact", "proposition": "占位"},
        reason="同 id"))
    assert _raises_neura(lambda: neura.supersede(
        result["npc"], new_id, {"cls": "fact"}, reason="缺命题"))


def check_correction_memory_summary_cap():
    """§4.3：summary 上限 80 码位，超长截断并显式标 `truncated`。"""
    npc = _knowledge_npc("npc-05")
    old = neura.learn(npc, _claim("守门人昨夜被换成了别人", cls="rumor"))
    reason = "因为他亲眼看到了换岗的整个过程" * 10
    result = neura.supersede(old["npc"], old["claim"]["claim_id"],
                             _claim("守门人今天清晨换了岗", cls="fact"),
                             reason=reason)
    memory = result["memory"]
    assert len(memory["summary"]) == neura.MEMORY_SUMMARY_MAX == 80
    assert memory["truncated"] is True
    assert memory["summary"] == reason[:80]


def check_purpose_field_sets():
    """三值各自的字段集**集合相等**（不是子集），且非法 purpose 直接拒。"""
    npc = _knowledge_npc()
    npc["goals"] = [
        {"goal_id": "g-1", "kind": "fulfill_duty", "target_ref": "npc-02",
         "weight": 0.8, "deadline_minute": 17200, "status": "active"},
        {"goal_id": "g-0", "kind": "survive", "target_ref": None,
         "weight": 0.3, "deadline_minute": None, "status": "done"},
    ]
    npc["knowledge"] = [_claim("我认为守卫换了人")]
    view = _knowledge_view()

    for purpose in neura.PURPOSES:
        context = neura.select_context(npc, view, purpose=purpose)
        assert set(context) == set(neura.CONTEXT_FIELDS[purpose]), (
            "%s 的字段集不符：多 %s / 少 %s"
            % (purpose,
               sorted(set(context) - set(neura.CONTEXT_FIELDS[purpose])),
               sorted(set(neura.CONTEXT_FIELDS[purpose]) - set(context))))
        assert context["purpose"] == purpose
        assert context["npc_id"] == "npc-01"
        assert context["now_minute"] == 17120
        # 三值都带「这个人知道什么」，但各自的专属字段各不相同。
        assert context["claims"] and context["world_facts"]
        assert context["secret_flags"] == []

    decide = neura.select_context(npc, view, purpose="decide")
    assert decide["plan"] is None                       # 无计划：键在、值为 None
    assert [goal["goal_id"] for goal in decide["goals"]] == ["g-1"]
    narrate = neura.select_context(npc, view, purpose="narrate")
    assert narrate["location"]["place_id"] == ""
    assert narrate["memory"] == []
    react = neura.select_context(npc, view, purpose="react")
    assert set(react["needs"]) == set(neura.NEED_KEYS)

    assert _raises_neura(lambda: neura.select_context(npc, view,
                                                      purpose="dialogue"))
    assert _raises_neura(lambda: neura.select_context(npc, view, purpose=None))


def check_only_active_claims_reach_context():
    """`claims` 只收 `status == "active"` 且 `cls` 合法的记录。"""
    npc = _knowledge_npc()
    npc["knowledge"] = [
        {"claim_id": "cl-active", "cls": "belief", "proposition": "我认为守卫换了人",
         "confidence": 70, "learned_minute": 17120, "status": "active"},
        {"claim_id": "cl-old", "cls": "rumor", "proposition": "听说守卫没换人",
         "confidence": 40, "learned_minute": 17000, "status": "disproved",
         "superseded_by": "cl-active"},
        {"claim_id": "cl-odd", "cls": "hearsay", "proposition": "五类之外的记录",
         "confidence": 70, "learned_minute": 17130, "status": "active"},
    ]
    context = neura.select_context(npc, _knowledge_view(), purpose="decide")
    assert [claim["claim_id"] for claim in context["claims"]] == ["cl-active"]
    assert set(context["claims"][0]) == {
        "claim_id", "cls", "proposition", "about_ref", "source_kind",
        "confidence", "learned_minute", "evidence_refs", "status"}
    text = _text_of(context)
    assert "听说守卫没换人" not in text
    assert "五类之外的记录" not in text


def check_five_classes_never_in_world_facts():
    """§6.1 硬规矩 1：`belief` / `rumor` / `inference` / `secret` 永不进世界事实投影。"""
    propositions = {
        "fact": "城门在子夜关闭",
        "belief": "守门人相信城门永不关闭",
        "rumor": "集市里传说北门有鬼",
        "inference": "我从换岗时刻推断守卫不足",
        "secret": "她把账本藏在第三级台阶下",
    }
    world_facts = {}
    for cls, proposition in sorted(propositions.items()):
        world_facts["wf-" + cls] = {
            "cls": cls, "visibility": "public", "proposition": proposition}
    world_facts["wf-private"] = {"cls": "fact", "visibility": "private",
                                 "proposition": "密室里还有一份账本"}

    context = neura.select_context(_knowledge_npc(), _knowledge_view(
        world_facts=world_facts), purpose="decide")

    assert context["world_facts"] == [
        {"fact_id": "wf-fact", "proposition": propositions["fact"]}]
    text = _text_of(context)
    for cls in neura.CLAIM_CLASSES_NEVER_WORLD_FACT:
        assert propositions[cls] not in text, "%s 的命题漏进了上下文" % cls
        assert _leaked_windows(propositions[cls], text) == [], \
            "%s 的命题有 8 码位窗口漏进了上下文" % cls
    assert propositions["fact"] in text          # 反面校准：公开事实确实在
    assert "密室里还有一份账本" not in text       # 非 public 的 fact 也不收


def check_double_npc_secret_visibility():
    """§6.3：同一秘密对 A 可见、对 B 不可见（8 码位滚动窗口断言）。"""
    secret = _scenario_secret("npc-01")
    if len(secret) < SECRET_WINDOW:
        raise AssertionError("npc-01 的秘密短于 %d 码位" % SECRET_WINDOW)
    secret_claim_id = "cl-a1"

    holder = _knowledge_npc("npc-01")
    holder["knowledge"] = [{
        "claim_id": secret_claim_id, "cls": "secret", "proposition": secret,
        "about_ref": "npc-01", "source_kind": "self", "confidence": 100,
        "learned_minute": 17120, "evidence_refs": [], "status": "active",
    }]
    holder["secrets"] = [{
        "secret_id": "sec-crown-01", "claim_id": secret_claim_id,
        "holders": ["npc-01"], "status": "held",
        "reveal_conditions": ["lever 出现在玩家原文"],
        "proposition": secret,          # 故意塞进来：投影时必须剥掉
    }]

    guard = _knowledge_npc("npc-05")
    guard["secrets"] = [{
        "secret_id": "sec-crown-01", "claim_id": secret_claim_id,
        "holders": ["npc-01"], "status": "held",
        "reveal_conditions": ["lever 出现在玩家原文"],
        "proposition": secret,          # 脏数据：B 这里也有原文，仍不许漏
    }]

    view = _knowledge_view()
    for purpose in neura.PURPOSES:
        holder_context = neura.select_context(holder, view, purpose=purpose)
        holder_text = _text_of(holder_context)
        assert secret in holder_text, "持有者在 %s 看不到自己的秘密" % purpose
        assert all(window in holder_text for window in _windows(secret))

        guard_context = neura.select_context(guard, view, purpose=purpose)
        guard_text = _text_of(guard_context)
        assert _leaked_windows(secret, guard_text) == [], \
            "非持有者在 %s 的上下文里出现了 8 码位窗口：%r" \
            % (purpose, _leaked_windows(secret, guard_text))
        assert guard_context["claims"] == []
        assert guard_context["secret_flags"] == [{
            "secret_id": "sec-crown-01", "known": False, "may_reveal": False}]
        assert set(guard_context["secret_flags"][0]) == {
            "secret_id", "known", "may_reveal"}

        body = neura.build_candidate_body(guard_context)
        assert _leaked_windows(secret, _text_of(body)) == [], \
            "候选请求体在 %s 漏了秘密窗口" % purpose

    # 持有者本人：known + may_reveal 都为真（秘密只说「存在 / 可否说」）。
    holder_flags = neura.select_context(
        holder, view, purpose="decide")["secret_flags"]
    assert holder_flags == [{
        "secret_id": "sec-crown-01", "known": True, "may_reveal": True}]


def check_no_foreign_proposition():
    """不越权：不含其他 NPC 的 `proposition`，也不含 `secrets[].proposition`。"""
    own = "我认为守卫换了人"
    foreign = "守卫自以为没人发现他换岗"
    stuffed = "别的角色被塞进 view 的认知"

    npc = _knowledge_npc("npc-01")
    npc["knowledge"] = [
        {"claim_id": "cl-own", "cls": "belief", "proposition": own,
         "confidence": 70, "learned_minute": 17120, "status": "active"},
        {"claim_id": "cl-foreign", "cls": "belief", "proposition": foreign,
         "holder_id": "npc-05", "confidence": 70, "learned_minute": 17120,
         "status": "active"},
    ]

    view = _knowledge_view(
        claims=[{"claim_id": "cl-view", "cls": "belief",
                 "proposition": stuffed, "status": "active"}],
        other_npcs={"npc-05": {"knowledge": [
            {"claim_id": "cl-other", "cls": "belief",
             "proposition": foreign, "status": "active"}]}},
    )

    context = neura.select_context(npc, view, purpose="react")
    text = _text_of(context)
    assert own in text
    assert foreign not in text
    assert stuffed not in text
    assert [claim["claim_id"] for claim in context["claims"]] == ["cl-own"]
    assert "holder_id" not in context["claims"][0]


def check_context_memory_limit():
    """记忆摘要只带最近 8 条（按 minute + memory_id 定序），不把全量记忆喂进去。"""
    npc = _knowledge_npc("npc-05")
    npc["memory"] = [
        {"memory_id": "mem-%02d" % index, "minute": index, "kind": "observation",
         "summary": "第 %d 条摘要" % index, "salience": 10,
         "claim_refs": []}
        for index in range(20)
    ]
    context = neura.select_context(npc, _knowledge_view(), purpose="react")
    memory = context["memory"]
    assert len(memory) == neura.CONTEXT_MEMORY_LIMIT == 8
    assert [item["memory_id"] for item in memory] == \
        ["mem-%02d" % index for index in range(12, 20)]
    assert set(memory[0]) == {"memory_id", "minute", "kind", "summary",
                              "salience", "claim_refs"}


def check_context_is_deterministic_and_pure():
    """同输入同输出；入参不改；返回的是副本（改上下文漏不进块）。"""
    npc = _knowledge_npc()
    npc["knowledge"] = [_claim("我认为守卫换了人")]
    npc["memory"] = [{"memory_id": "mem-1", "minute": 17100, "kind": "observation",
                      "summary": "看到换岗", "salience": 20, "claim_refs": []}]
    view = _knowledge_view()
    npc_before, view_before = copy.deepcopy(npc), copy.deepcopy(view)

    first = neura.select_context(npc, view, purpose="react")
    second = neura.select_context(npc, view, purpose="react")
    assert _text_of(first) == _text_of(second)
    assert npc == npc_before, "select_context 改写了 npc"
    assert view == view_before, "select_context 改写了 view"

    first["claims"][0]["proposition"] = "被改写的命题"
    first["needs"]["safety"] = 99
    first["memory"][0]["summary"] = "被改写的摘要"
    assert npc["knowledge"][0]["proposition"] == "我认为守卫换了人"
    assert npc["needs"]["safety"] == neura.NEED_DEFAULT
    assert npc["memory"][0]["summary"] == "看到换岗"


def check_build_candidate_body_shape():
    """`build_candidate_body`：两条消息、内容为文本、同一上下文拼出同一串。"""
    npc = _knowledge_npc()
    npc["knowledge"] = [_claim("我认为守卫换了人")]
    context = neura.select_context(npc, _knowledge_view(), purpose="decide")
    body = neura.build_candidate_body(context)

    assert set(body) == {"purpose", "npc_id", "messages"}
    assert body["purpose"] == "decide"
    assert body["npc_id"] == "npc-01"
    messages = body["messages"]
    assert [message["role"] for message in messages] == ["system", "user"]
    for message in messages:
        assert set(message) == {"role", "content"}
        assert isinstance(message["content"], str) and message["content"]
    assert "我认为守卫换了人" in messages[1]["content"]
    assert "城门在子夜关闭" in messages[1]["content"]
    assert _text_of(neura.build_candidate_body(context)) == _text_of(body)

    assert _raises_neura(lambda: neura.build_candidate_body(
        {"purpose": "dialogue", "npc_id": "npc-01"}))
    assert _raises_neura(lambda: neura.build_candidate_body({}))


def check_role_literal_appears_once():
    """唯一收口：`neura.py` 里 role 键字面量与 `messages` 键各只出现一次。"""
    with open(NEURA_PY, encoding="utf-8") as handle:
        source = handle.read()
    assert source.count('"role":') == 1, \
        "role 键出现 %d 次（应只在 build_candidate_body 里）" % source.count('"role":')
    assert source.count('"messages":') == 1, \
        "messages 键出现 %d 次（应只在 build_candidate_body 里）" \
        % source.count('"messages":')
    # 拼装点的函数边界：那一行必须在 build_candidate_body 的函数体内。
    start = source.index("def build_candidate_body(")
    end = source.index("\ndef ", start)
    assert '"role":' in source[start:end]


# --------------------------------------------------------------------------
# §7 / §8 / §11 N5：分层模拟 + 每日预算 + 降级阶梯 + 补算（M11-N5）
# --------------------------------------------------------------------------

class _FakePort:
    """记录型假 port（依赖注入）：submit 必须实现；generate_candidates 可选。"""

    def __init__(self, ai_options=None):
        self.submits = []
        self.ai_calls = []
        self._ai_options = list(ai_options or [])

    def submit(self, proposal, idempotency_key=None):
        self.submits.append({"proposal": proposal, "key": idempotency_key})
        return {"status": "committed"}

    def generate_candidates(self, npc, view, budget=None):
        self.ai_calls.append(
            {"npc_id": npc.get("display_ref", {}).get("npc_id")})
        return [dict(item) for item in self._ai_options]


class _SubmitOnlyPort:
    """无 AI 配置的最小 port：没有 `generate_candidates` 属性（§8.4 正常路径）。"""

    def __init__(self):
        self.submits = []

    def submit(self, proposal, idempotency_key=None):
        self.submits.append({"proposal": proposal, "key": idempotency_key})
        return {"status": "committed"}


def _n5_npc(npc_id="npc-01", place="gate", importance=0.0, schedule=None,
            plan=None, goals=None, secrets=None):
    npc = neura.empty_npc(npc_id, "sc-01")
    npc["location"]["place_id"] = place
    npc["importance"] = importance
    if schedule is not None:
        npc["schedule"] = list(schedule)
    if plan is not None:
        npc["plan"] = dict(plan)
    if goals is not None:
        npc["goals"] = list(goals)
    if secrets is not None:
        npc["secrets"] = list(secrets)
    return npc


def _n5_block(npcs):
    block = neura.empty_block()
    for npc in npcs:
        block["npcs"][npc["display_ref"]["npc_id"]] = npc
    return block


def check_n5_constants():
    """P4 / P5 / §7.2 / §7.4 / §8.2 / P10 的常量逐字对齐。"""
    assert neura.NPC_DAILY_TOKEN_BUDGET == 60000
    assert neura.IMPORTANCE_THRESHOLD == 0.7
    assert neura.MAX_CATCHUP_STEPS == 8
    assert neura.CATCHUP_GRID_MINUTES == 10
    assert neura.BUDGET_WARN_RATIO == 0.8
    assert neura.DEGRADE_TIERS == ("L0", "L1", "L2", "L3")
    assert neura.BURST_LIMITS == {"strong": 1, "light": 3}
    assert neura.CALL_KIND_TOKENS == {"strong": 4000, "light": 1200, "rule": 0}
    # P4 反解：h ≤ 6、m ≤ 30，且 4000*h + 1200*m ≤ 60000。
    assert neura.STRONG_CALLS_PER_DAY == 6
    assert neura.LIGHT_CALLS_PER_DAY == 30
    assert 4000 * neura.STRONG_CALLS_PER_DAY + \
        1200 * neura.LIGHT_CALLS_PER_DAY == 60000
    assert 4000 * neura.STRONG_CALLS_PER_DAY + \
        1200 * neura.LIGHT_CALLS_PER_DAY <= neura.NPC_DAILY_TOKEN_BUDGET
    # P10：三个权重写成常量。
    assert (neura.IMPORTANCE_WEIGHT_THREADS, neura.IMPORTANCE_WEIGHT_FACTION,
            neura.IMPORTANCE_WEIGHT_SECRET) == (0.4, 0.3, 0.3)


def check_layer_of_short_circuit():
    """§7.1 四层判定按短路顺序；未具名条目归 unexplored。"""
    view = _view()     # party_places=["market"]；bands (gate,market)=adjacent

    # near：place ∈ party_places。
    assert neura.layer_of(_n5_npc(place="market"), view) == "near"
    # near：band adjacent 直通，且优先于重要性判定（短路顺序）。
    assert neura.layer_of(_n5_npc(place="gate", importance=1.0), view) == "near"
    # important_far：importance ≥ 0.7（P5）。
    far_view = _view()
    far_view["bands"] = {}
    assert neura.layer_of(
        _n5_npc(place="temple", importance=0.9), far_view) == "important_far"
    # background：其余已具名 NPC。
    assert neura.layer_of(
        _n5_npc(place="temple", importance=0.5), far_view) == "background"
    # unexplored：未具名条目不承载具名 NPC（§7.1）。
    assert neura.layer_of(_n5_npc(npc_id=""), view) == "unexplored"


def check_layer_of_goal_target_and_importance_formula():
    """§7.1 important_far 的第二条路（goal 指向 PC / 已知线索）+ P10 派生公式。"""
    view = _view()
    view["bands"] = {}
    view["party_refs"] = ["pc-1"]
    view["known_refs"] = ["clue-9"]

    goal = {"goal_id": "g-1", "kind": "belong", "target_ref": "pc-1",
            "weight": 1.0, "deadline_minute": None, "status": "active"}
    assert neura.layer_of(
        _n5_npc(place="temple", goals=[goal]), view) == "important_far"

    goal["target_ref"] = "clue-9"
    assert neura.layer_of(
        _n5_npc(place="temple", goals=[goal]), view) == "important_far"

    goal["target_ref"] = "npc-99"            # 不指向 PC / 已知线索
    assert neura.layer_of(
        _n5_npc(place="temple", goals=[goal]), view) == "background"

    # P10：显式覆盖（静态档案 importance > 0）优先于公式。
    npc = _n5_npc(place="temple", secrets=[{"secret_id": "s-1"}])
    view["main_thread_refs"] = ["npc-01"]
    view["faction_refs"] = ["npc-01"]
    assert neura.derive_importance(npc, view) == 1.0
    assert neura._effective_importance(dict(npc, importance=0.2), view) == 0.2
    # P10：覆盖表（编排层注入）同样优先。
    view["importance_overrides"] = {"npc-01": 0.5}
    assert neura._effective_importance(npc, view) == 0.5
    # 公式分项：0.4 / 0.3 / 0.3。
    assert neura.derive_importance(_n5_npc(place="temple"), _view()) == 0.0
    assert neura.derive_importance(
        _n5_npc(secrets=[{"secret_id": "s"}]), _view()) == 0.3


def check_layer_recomputed_same_tick():
    """§7.1：layer 每 tick 现算、不缓存——玩家走远一步，同一 tick 内立刻降层。"""
    view_near = _view()                      # gate 与 market 相邻
    npc = _n5_npc(place="gate")
    assert neura.layer_of(npc, view_near) == "near"
    assert neura.layer_of(npc, view_near) == "near"      # 同输入同输出（无缓存漂移）

    view_far = _view()
    view_far["party_places"] = ["dock"]      # 玩家走远一步：不在 gate 也不相邻
    view_far["bands"] = {}
    assert neura.layer_of(npc, view_far) == "background"
    # 同一 NPC 同一 tick 内前后两次判定，降层立刻可见。
    assert neura.layer_of(npc, view_near) == "near"
    assert neura.layer_of(npc, view_far) == "background"


def check_due_npcs_frequencies_and_order():
    """§7.2 频率去重 + 确定性排序 + 日程违约额外到期 + unexplored 永不到期。"""
    near_a = _n5_npc("npc-a", place="market", importance=0.2)
    near_b = _n5_npc("npc-b", place="market", importance=0.9)
    far = _n5_npc("npc-f", place="temple", importance=0.9)
    bg = _n5_npc("npc-g", place="temple")
    view = _view()
    view["bands"] = {}                       # temple 与 market 不相邻
    block = _n5_block([near_a, near_b, far, bg])

    # near 每 tick；important_far 日切才到期；background 每 3 游戏日；
    # 层内按 importance 降序（npc-b 0.9 在 npc-a 0.2 之前）。
    assert neura.due_npcs(block, view, 600) == ["npc-b", "npc-a"]
    assert neura.due_npcs(block, view, 1441) == ["npc-b", "npc-a", "npc-f"]
    assert neura.due_npcs(block, view, 4321) == \
        ["npc-b", "npc-a", "npc-f", "npc-g"]
    # 同 tick 内 last_tick 去重，不重复到期。
    block["npcs"]["npc-a"]["last_tick_minute"] = 600
    assert neura.due_npcs(block, view, 600) == ["npc-b"]

    # 日程违约：background 未到频率也额外到期（§8.4 日程移动零 token）。
    worker = _n5_npc("npc-w", place="gate",
                     schedule=[{"slot_id": "sl-forge", "from_tod_minute": 480,
                                "to_tod_minute": 720, "place_id": "forge",
                                "activity": "work", "interruptible": False}])
    block2 = _n5_block([worker])
    assert neura.due_npcs(block2, view, 600) == ["npc-w"]
    worker["location"]["place_id"] = "forge"
    assert neura.due_npcs(block2, view, 600) == []

    # unexplored 永不到期。
    anon = _n5_npc("", place="market")
    assert neura.due_npcs(_n5_block([anon]), view, 600) == []


def check_force_due_ids_forces_due():
    """§8.4（V12）：view.force_due_ids 注入 → 该 NPC 本 tick 强制到期。"""
    far = _n5_npc("npc-f", place="temple", importance=0.9)
    bg = _n5_npc("npc-g", place="temple")
    view = _view()
    view["bands"] = {}                       # temple 与 market 不相邻
    block = _n5_block([far, bg])
    # 同日已判过：缺注入键（M11 行为）→ important_far / background 都不到期。
    block["npcs"]["npc-f"]["last_tick_minute"] = 100
    block["npcs"]["npc-g"]["last_tick_minute"] = 100
    assert neura.due_npcs(block, view, 600) == []
    # 注入 → 强制到期（无视层频率）。
    view["force_due_ids"] = ["npc-f"]
    assert neura.due_npcs(block, view, 600) == ["npc-f"]
    # background 也认注入（编排层只发 important_far，neura 侧不重复过滤）。
    view["force_due_ids"] = ["npc-g"]
    assert neura.due_npcs(block, view, 600) == ["npc-g"]
    # 注入不存在的 id / 空 id / 非 string 不影响别人；空表 = 缺键同款。
    view["force_due_ids"] = ["npc-x", 123, "", None]
    assert neura.due_npcs(block, view, 600) == []
    view["force_due_ids"] = []
    assert neura.due_npcs(block, view, 600) == []


def check_force_due_ids_does_not_resurrect_unexplored():
    """§8.4：unexplored（未具名）不因注入复活——该层不承载具名 NPC。"""
    anon = _n5_npc("", place="temple")
    view = _view()
    view["bands"] = {}
    view["force_due_ids"] = ["", "npc-01"]
    assert neura.due_npcs(_n5_block([anon]), view, 600) == []


def check_charge_and_cross_day_reset():
    """§8.2 记账（纯函数、rule 不计数）+ 跨日（day 变化）计数归零。"""
    budget = {"day": 1, "strong_calls": 0, "light_calls": 0, "tokens": 0}
    snapshot = dict(budget)
    charged = neura.charge(budget, "strong")
    assert charged == {"day": 1, "strong_calls": 1, "light_calls": 0,
                       "tokens": 4000}
    assert budget == snapshot, "charge 改写了入参"
    assert neura.charge(budget, "light")["light_calls"] == 1
    assert neura.charge(budget, "rule") == snapshot, "rule 档不计数"
    assert neura.charge(budget, "strong", tokens=3900)["tokens"] == 3900
    assert neura.charge(budget, "unknown-tier") == snapshot, "非法档位按 rule"

    # 跨日归零：账本停在 day 0，tick 推进到 day 1 → 计数器归零（§8.2）。
    block = _n5_block([_n5_npc(place="market")])
    block["budget"] = {"day": 0, "strong_calls": 6, "light_calls": 30,
                       "tokens": 60000}
    ticked = neura.tick(block, _view(options=[]), port=_SubmitOnlyPort(),
                        now_minute=1440)
    assert ticked["budget"] == {"day": 1, "strong_calls": 0, "light_calls": 0,
                                "tokens": 0}


def check_can_call_ai_gates():
    """§8.2 / §8.3：预算 + 突发上限 + 档位的 AI 准入；超限不抛错。"""
    near = _n5_npc(place="market")
    fresh = {"day": 0, "strong_calls": 0, "light_calls": 0, "tokens": 0}
    view = _view()                           # npc 在 market → near

    assert neura.can_call_ai(near, view, fresh) is True
    # strong 次数用尽（P4 反解 h ≤ 6）。
    assert neura.can_call_ai(
        near, view, dict(fresh, strong_calls=6, tokens=24000)) is False, \
        "strong 次数用尽应拒绝"
    # token 余量不足：档位 ≥ 80% 已是 L1（light），light 超预算应拒绝；
    # 边界 58800 + 1200 == 60000 放行。
    assert neura.can_call_ai(near, view, dict(fresh, tokens=59000)) is False, \
        "light 超出日预算应拒绝"
    assert neura.can_call_ai(near, view, dict(fresh, tokens=58800)) is True
    # background 层 0 次模型调用（§7.2）。
    assert neura.can_call_ai(_n5_npc(place="temple"), view, fresh) is False
    # 无 AI 配置 = L2 正常路径（§8.4）。
    assert neura.can_call_ai(
        near, dict(view, ai_configured=False), fresh) is False
    # L3：plan dropped 且无可行选项。
    dropped = _n5_npc(place="market",
                      plan={"plan_id": "pl-x", "status": "dropped"})
    assert neura.can_call_ai(dropped, _view(options=[]), fresh) is False
    # 突发上限：本 tick 已用 1 次 strong → 拒（§7.2 ≤ 1 H）。
    assert neura.can_call_ai(
        near, dict(view, tick_burst={"strong": 1, "light": 0}), fresh) is False
    # L1 档走 light：strong 用尽不连坐；light 突发 3 次用尽 → 拒（≤ 3 M）。
    far_view = _view()
    far_view["bands"] = {}
    leader = _n5_npc(place="temple", importance=0.9)
    used = dict(fresh, strong_calls=6, tokens=24000)
    assert neura.can_call_ai(leader, far_view, used) is True
    assert neura.can_call_ai(
        leader, dict(far_view, tick_burst={"strong": 0, "light": 3}),
        used) is False


def check_degrade_ladder_and_monotonic():
    """§8.3：80% / 100% 两档降级 + 单调不回弹 + L3 判据。"""
    near = _n5_npc(place="market")
    view = _view()
    fresh = {"day": 0, "strong_calls": 0, "light_calls": 0, "tokens": 0}
    assert neura.degrade_tier(near, view, fresh) == "L0"
    # 80% 档：48000 / 60000 == 0.8 → L1。
    assert neura.degrade_tier(near, view, dict(fresh, tokens=48000)) == "L1"
    # important_far 恒 L1（预算未到 80% 也是，§8.3）。
    far_view = _view()
    far_view["bands"] = {}
    assert neura.degrade_tier(
        _n5_npc(place="temple", importance=0.9), far_view, fresh) == "L1"
    # 100% 档：tokens 60000 → L2。
    assert neura.degrade_tier(near, view, dict(fresh, tokens=60000)) == "L2"
    # 无 AI 配置 → L2（正常路径，不抛错，§8.4）。
    assert neura.degrade_tier(
        near, dict(view, ai_configured=False), fresh) == "L2"
    # L3：plan dropped 且无可行选项；dropped 但有可行选项 → 不冻结。
    dropped = _n5_npc(place="market",
                      plan={"plan_id": "pl-x", "status": "dropped"})
    assert neura.degrade_tier(dropped, _view(options=[]), fresh) == "L3"
    assert neura.degrade_tier(
        dropped, _view(options=[_option("op-ok")]), fresh) == "L0"
    # 单调：同一天内用量只增，档位不回弹（L2 → 再判定仍为 L2/L3）。
    sequence = [neura.degrade_tier(near, view, dict(fresh, tokens=tokens))
                for tokens in (0, 30000, 48000, 51000, 60000)]
    assert sequence == ["L0", "L0", "L1", "L1", "L2"]
    assert sequence == sorted(sequence), "档位应单调不回弹"


def check_tick_offline_schedule_move():
    """§8.4：无 AI 配置（port 无 generate_candidates）仍产生日程移动提案。"""
    npc = _n5_npc(place="gate",
                  schedule=[{"slot_id": "sl-forge", "from_tod_minute": 480,
                             "to_tod_minute": 720, "place_id": "forge",
                             "activity": "work", "interruptible": False}])
    block = _n5_block([npc])
    view = _view()
    view["bands"][("gate", "forge")] = "adjacent"
    port = _SubmitOnlyPort()
    block_before = copy.deepcopy(block)
    view_before = copy.deepcopy(view)

    result = neura.tick(block, view, port=port, now_minute=600)

    # tick 返回新块、不返回事件；入参不被改写。
    assert isinstance(result, dict) and "events" not in result
    assert result is not block
    assert block == block_before, "tick 改写了入参 block"
    assert view == view_before, "tick 改写了入参 view"

    assert len(port.submits) == 1
    proposal = port.submits[0]["proposal"]
    assert proposal["intent"]["verb"] == "move"
    assert proposal["intent"]["desired"] == "move"
    # 提交键与幂等键一致（用 journal 里同一条决策重算得到同一个键）。
    decision = result["journal"][-1]
    assert port.submits[0]["key"] == neura.idempotency_key(
        decision, result["npcs"]["npc-01"])
    # 移动后位置更新；离线路径 cost.tier == "rule"、ai_used == false。
    assert result["npcs"]["npc-01"]["location"]["place_id"] == "forge"
    assert result["npcs"]["npc-01"]["last_tick_minute"] == 600
    assert decision["cost"] == {"tier": "rule", "tokens": 0}
    assert decision["ai_used"] is False


def check_tick_schedule_moves_each_day():
    """§8.4：日程移动 ≥ 1 条 / 日 / 有日程 NPC（两个 slot 到点各移一次）。"""
    npc = _n5_npc(place="gate", schedule=[
        {"slot_id": "sl-forge", "from_tod_minute": 480, "to_tod_minute": 720,
         "place_id": "forge", "activity": "work", "interruptible": False},
        {"slot_id": "sl-gate", "from_tod_minute": 800, "to_tod_minute": 1000,
         "place_id": "gate", "activity": "guard", "interruptible": False},
    ])
    block = _n5_block([npc])
    view = _view()
    view["bands"][("gate", "forge")] = "adjacent"
    view["bands"][("forge", "gate")] = "adjacent"
    port = _SubmitOnlyPort()

    block = neura.tick(block, view, port=port, now_minute=600)   # 去 forge 上班
    block = neura.tick(block, view, port=port, now_minute=900)   # 回 gate 值岗

    assert len(port.submits) == 2
    assert all(submit["proposal"]["intent"]["verb"] == "move"
               for submit in port.submits)
    assert block["npcs"]["npc-01"]["location"]["place_id"] == "gate"


def check_tick_burst_and_charge():
    """§7.2 单 tick 突发上限 ≤ 1 次 H / ≤ 3 次 M；按降级档位记账。"""
    ai = _option("op-ai", source="ai")

    # 5 个 near NPC 抢 strong：只有 1 次调用、1 次记账。
    block = _n5_block([_n5_npc("npc-%d" % index, place="market")
                       for index in range(5)])
    port = _FakePort(ai_options=[ai])
    ticked = neura.tick(block, _view(options=[_option("op-rule")]),
                        port=port, now_minute=600)
    assert ticked["budget"]["strong_calls"] == 1
    assert ticked["budget"]["light_calls"] == 0
    assert ticked["budget"]["tokens"] == 4000
    assert len(port.ai_calls) == 1, "突发上限应在调用前拦住"

    # 4 个 important_far NPC（日切到期）抢 light：≤ 3 次。
    far_view = _view(options=[_option("op-rule")])
    far_view["bands"] = {}
    block2 = _n5_block([_n5_npc("npc-%d" % index, place="temple",
                                importance=0.9) for index in range(4)])
    port2 = _FakePort(ai_options=[ai])
    ticked2 = neura.tick(block2, far_view, port=port2, now_minute=1441)
    assert ticked2["budget"] == {"day": 1, "strong_calls": 0, "light_calls": 3,
                                 "tokens": 3600}
    assert len(port2.ai_calls) == 3


def check_tick_submit_keys_stable_and_idempotent():
    """§5.6：提交键与幂等键一致；同计划同步骤续投键恒定。"""
    block = _n5_block([_n5_npc(
        place="market",
        plan={"plan_id": "pl-9", "goal_id": "", "step_index": 0,
              "status": "running", "started_minute": 590,
              "until_minute": 700})])
    view = _view(options=[_option("op-work", verb="work")])
    port = _FakePort(ai_options=[])          # 有口子但返回空 → 规则候选

    ticked = neura.tick(block, view, port=port, now_minute=600)
    assert len(port.submits) == 1
    first_key = port.submits[0]["key"]
    assert first_key == neura.idempotency_key(ticked["journal"][-1],
                                              ticked["npcs"]["npc-01"])
    assert first_key.endswith("_00")
    # P7：延迟执行写在块内 plan.until_minute，不进 chron.actions。
    assert ticked["npcs"]["npc-01"]["plan"]["until_minute"] == 610
    # 计划仍在跑、同目标续投（更短的候选，过 check_time 的剩余预算）→
    # 幂等键恒定（§5.6 同计划同步骤）。
    short_view = _view(options=[_option("op-short", verb="work", minutes=3)])
    neura.tick(ticked, short_view, port=port, now_minute=601)
    assert len(port.submits) == 2
    assert port.submits[1]["key"] == first_key


def check_tick_l3_idle_journal():
    """§8.3 L3：只写 journal(kind="idle")，不提交。"""
    npc = _n5_npc(place="market",
                  plan={"plan_id": "pl-x", "goal_id": "g-1", "step_index": 0,
                        "status": "dropped", "started_minute": 500,
                        "until_minute": 700})
    block = _n5_block([npc])
    port = _SubmitOnlyPort()
    ticked = neura.tick(block, _view(options=[]), port=port, now_minute=600)

    entry = ticked["journal"][-1]
    assert entry["kind"] == "idle"
    assert entry["npc_id"] == "npc-01"
    assert entry["at_minute"] == 600
    assert port.submits == [], "L3 不提交任何计划"
    assert ticked["npcs"]["npc-01"]["last_tick_minute"] == 600
    assert _json_roundtrip(ticked) == ticked


def check_catch_up_converges_and_defers():
    """§7.4：上限 8 步；到期计划全部收敛（断言终态）；超出记 catchup_deferred。"""
    npcs = [_n5_npc("npc-%02d" % index, place="temple",
                    plan={"plan_id": "pl-%02d" % index, "goal_id": "",
                          "step_index": 0, "status": "running",
                          "started_minute": 0,
                          "until_minute": 100 + index * 10})
            for index in range(12)]
    block = _n5_block(npcs)

    result = neura.catch_up(block, _view(), 600)
    ticked = result["block"]

    # 终态收敛：没有 active 计划的 until_minute ≤ 600。
    for npc in ticked["npcs"].values():
        assert npc["plan"]["status"] == "done"
        assert npc["last_tick_minute"] == 600, "远场层直接跳到最新分钟"
    assert result["processed"] == 12
    # 60 个格点 > 8：未处理的记 catchup_deferred（count = 60 − 12）。
    deferred = [entry for entry in ticked["journal"]
                if entry.get("kind") == "catchup_deferred"]
    assert len(deferred) == 1
    assert deferred[0]["count"] == 48
    assert deferred[0]["at_minute"] == 600
    assert len([entry for entry in ticked["journal"]
                if entry.get("kind") == "catchup_plan_done"]) == 12
    assert _json_roundtrip(ticked) == ticked


def check_catch_up_small_gap_no_defer():
    """§7.4：步数 ≤ 8 不记 catchup_deferred；到期计划照常收敛。"""
    npcs = [
        _n5_npc("npc-a", place="temple",
                plan={"plan_id": "pl-a", "goal_id": "", "step_index": 0,
                      "status": "running", "started_minute": 0,
                      "until_minute": 50}),
        _n5_npc("npc-b", place="temple",
                plan={"plan_id": "pl-b", "goal_id": "", "step_index": 0,
                      "status": "running", "started_minute": 0,
                      "until_minute": 60}),
        _n5_npc("npc-c", place="temple"),          # 无计划：不被补算改动
    ]
    block = _n5_block(npcs)

    result = neura.catch_up(block, _view(), 80)
    ticked = result["block"]

    assert result["processed"] == 2 and result["deferred"] == 0
    assert not [entry for entry in ticked["journal"]
                if entry.get("kind") == "catchup_deferred"]
    assert ticked["npcs"]["npc-a"]["plan"]["status"] == "done"
    assert ticked["npcs"]["npc-b"]["plan"]["status"] == "done"
    assert ticked["npcs"]["npc-a"]["last_tick_minute"] == 50
    assert ticked["npcs"]["npc-c"]["last_tick_minute"] == 0, \
        "≤ 8 步不触发远场跳分钟"


def check_catch_up_idempotent():
    """§7.4：同一 last_tick_minute 重跑不重复记账、不重复 journal。"""
    plan = {"plan_id": "pl-a", "goal_id": "", "step_index": 0,
            "status": "running", "started_minute": 0, "until_minute": 120}

    # 远场层：收敛后 last_tick == now → 第二次跑整体早退。
    block = _n5_block([_n5_npc("npc-a", place="temple", plan=plan)])
    first = neura.catch_up(block, _view(), 600)
    snapshot = json.dumps(first["block"], ensure_ascii=False, sort_keys=True)
    second = neura.catch_up(first["block"], _view(), 600)
    assert second["processed"] == 0 and second["deferred"] == 0
    assert json.dumps(second["block"], ensure_ascii=False,
                      sort_keys=True) == snapshot

    # near 层：last_tick 停在到期分钟，重跑仍不重复 journal（guard 生效）。
    block2 = _n5_block([_n5_npc("npc-a", place="market", plan=plan)])
    first2 = neura.catch_up(block2, _view(), 600)
    snapshot2 = json.dumps(first2["block"], ensure_ascii=False, sort_keys=True)
    second2 = neura.catch_up(first2["block"], _view(), 600)
    assert json.dumps(second2["block"], ensure_ascii=False,
                      sort_keys=True) == snapshot2


def check_tick_is_pure():
    """tick 纯函数：入参深比较不变、返回值可 JSON 往返。"""
    npc = _n5_npc(place="gate",
                  schedule=[{"slot_id": "sl-forge", "from_tod_minute": 480,
                             "to_tod_minute": 720, "place_id": "forge",
                             "activity": "work", "interruptible": True}])
    block = _n5_block([npc])
    view = _view(options=[_option("op-rule")])
    view["bands"][("gate", "forge")] = "adjacent"
    block_before = copy.deepcopy(block)
    view_before = copy.deepcopy(view)

    result = neura.tick(block, view, port=_FakePort(), now_minute=600)

    assert block == block_before, "tick 改写了入参 block"
    assert view == view_before, "tick 改写了入参 view"
    assert result is not block
    assert _json_roundtrip(result) == result


# --------------------------------------------------------------------------
# §5.7 / Issue #400：回写跟随 M8 提交结果（committed / replayed 才回写）
# --------------------------------------------------------------------------

class _StatusPort(_SubmitOnlyPort):
    """按配置返回固定提交状态的假 port（四态覆盖，形状照抄 `_neura_submit`）。"""

    def __init__(self, status):
        _SubmitOnlyPort.__init__(self)
        self._status = status

    def submit(self, proposal, idempotency_key=None):
        self.submits.append({"proposal": proposal, "key": idempotency_key})
        return {"status": self._status, "result": None,
                "reason": "fake-%s" % self._status, "event_id": None}


_NOT_COMMITTED = ("conflict", "rejected", "unsupported")


def _schedule_move_fixture():
    """日程移动路径的公共夹具：gate → forge 的到点上班。"""
    npc = _n5_npc(place="gate",
                  schedule=[{"slot_id": "sl-forge", "from_tod_minute": 480,
                             "to_tod_minute": 720, "place_id": "forge",
                             "activity": "work", "interruptible": False}])
    view = _view()
    view["bands"][("gate", "forge")] = "adjacent"
    return npc, view


def _submit_notes(journal):
    return [entry for entry in journal
            if entry.get("kind") == "submit_not_committed"]


def check_tick_schedule_move_writeback_follows_submit():
    """#400：日程移动回写严格跟随提交结果。

    `committed`（含幂等重放 `replayed`）才更新 `location`；`conflict` /
    `rejected` / `unsupported` 不动 `place_id` / `since_minute`，journal 留
    可解释记录（下个 tick 重试）。
    """
    for status, expect_move in ([("committed", True), ("replayed", True)]
                                + [(s, False) for s in _NOT_COMMITTED]):
        npc, view = _schedule_move_fixture()
        block = _n5_block([npc])
        port = _StatusPort(status)

        ticked = neura.tick(block, view, port=port, now_minute=600)

        assert len(port.submits) == 1, status
        location = ticked["npcs"]["npc-01"]["location"]
        if expect_move:
            assert location["place_id"] == "forge", status
            assert location["since_minute"] == 600, status
            assert _submit_notes(ticked["journal"]) == [], status
        else:
            assert location["place_id"] == "gate", status
            assert location["since_minute"] == 0, status
            notes = _submit_notes(ticked["journal"])
            assert len(notes) == 1, status
            assert notes[0]["npc_id"] == "npc-01", status
            assert notes[0]["at_minute"] == 600, status
            assert notes[0]["status"] == status, status
            assert notes[0]["reason"] == "fake-%s" % status, status
        assert _json_roundtrip(ticked) == ticked, status


def check_tick_schedule_move_conflict_retries_next_tick():
    """§5.7：conflict 不回写但保留可重试态——下个 tick 重投成功后照常回写。"""
    npc, view = _schedule_move_fixture()
    block = _n5_block([npc])

    block = neura.tick(block, view, port=_StatusPort("conflict"),
                       now_minute=600)
    assert block["npcs"]["npc-01"]["location"]["place_id"] == "gate"

    block = neura.tick(block, view, port=_StatusPort("committed"),
                       now_minute=610)
    assert block["npcs"]["npc-01"]["location"]["place_id"] == "forge"
    assert block["npcs"]["npc-01"]["location"]["since_minute"] == 610


def check_tick_plan_writeback_follows_submit():
    """#400：普通计划回写严格跟随提交结果——未提交的长计划不变为 `running`。"""
    for status, expect_plan in ([("committed", True), ("replayed", True)]
                                + [(s, False) for s in _NOT_COMMITTED]):
        npc = _n5_npc(place="market")
        block = _n5_block([npc])
        view = _view(options=[_option("op-work", verb="work")])
        port = _StatusPort(status)

        ticked = neura.tick(block, view, port=port, now_minute=600)

        assert len(port.submits) == 1, status
        if expect_plan:
            plan = ticked["npcs"]["npc-01"]["plan"]
            assert plan["status"] == "running", status
            assert plan["until_minute"] == 610, status
            assert _submit_notes(ticked["journal"]) == [], status
        else:
            assert "plan" not in ticked["npcs"]["npc-01"], status
            notes = _submit_notes(ticked["journal"])
            assert len(notes) == 1, status
            assert notes[0]["npc_id"] == "npc-01", status
            assert notes[0]["status"] == status, status
        assert _json_roundtrip(ticked) == ticked, status


def check_tick_plan_not_committed_keeps_retryable_state():
    """#400：rejected / unsupported 不写 running——既有计划逐字保留（步骤
    索引不动），journal 留痕、可追溯、可重试。"""
    old_plan = {"plan_id": "pl-old", "goal_id": "g-1", "step_index": 1,
                "status": "running", "started_minute": 590,
                "until_minute": 700}
    for status in _NOT_COMMITTED:
        npc = _n5_npc(place="market", plan=dict(old_plan))
        block = _n5_block([npc])
        view = _view(options=[_option("op-work", verb="work")])

        ticked = neura.tick(block, view, port=_StatusPort(status),
                            now_minute=600)

        assert ticked["npcs"]["npc-01"]["plan"] == old_plan, status
        notes = _submit_notes(ticked["journal"])
        assert len(notes) == 1 and notes[0]["status"] == status, status
        assert _json_roundtrip(ticked) == ticked, status


# --------------------------------------------------------------------------
# §8.2 / Issue #366：AI 调用尝试即记账，失败不拖垮 tick
# --------------------------------------------------------------------------

class _RaisingPort(_FakePort):
    """`generate_candidates` 抛异常的假 port（失败隔离用例）。"""

    def __init__(self, message="模型超时"):
        _FakePort.__init__(self, ai_options=[])
        self.message = message

    def generate_candidates(self, npc, view, budget=None):
        self.ai_calls.append(
            {"npc_id": npc.get("display_ref", {}).get("npc_id")})
        raise RuntimeError(self.message)


class _BudgetPort(_FakePort):
    """记录每次调用看到的预算快照（同 tick 预算可见性用例）。"""

    def __init__(self, ai_options=None):
        _FakePort.__init__(self, ai_options=ai_options)
        self.budgets = []

    def generate_candidates(self, npc, view, budget=None):
        self.budgets.append(dict(budget or {}))
        return _FakePort.generate_candidates(self, npc, view, budget=budget)


def _ai_failed_entries(block):
    return [entry for entry in block["journal"]
            if entry.get("kind") == "ai_failed"]


def check_tick_charges_empty_candidates():
    """#366：模型返回空表也计一次调用 / token；突发上限照常生效。"""
    block = _n5_block([_n5_npc("npc-%d" % index, place="market")
                       for index in range(5)])
    port = _FakePort(ai_options=[])

    ticked = neura.tick(block, _view(options=[_option("op-rule")]),
                        port=port, now_minute=600)

    assert len(port.ai_calls) == 1, "空表也算一次尝试：突发上限应拦住其余 4 个"
    assert ticked["budget"] == {"day": 0, "strong_calls": 1, "light_calls": 0,
                                "tokens": 4000}
    assert _ai_failed_entries(ticked) == [], "空表不是失败，不写 ai_failed"
    for entry in ticked["journal"]:
        assert entry["ai_used"] is False
        assert entry["cost"] == {"tier": "rule", "tokens": 0}
    assert _json_roundtrip(ticked) == ticked


def check_tick_charges_rejected_candidates():
    """#366：AI 候选全被复验拒绝仍记账；决策不标成 AI。"""
    block = _n5_block([_n5_npc(place="market")])
    port = _FakePort(ai_options=[{"option_id": "op-bad"}])

    ticked = neura.tick(block, _view(options=[_option("op-rule")]),
                        port=port, now_minute=600)

    assert len(port.ai_calls) == 1
    assert ticked["budget"] == {"day": 0, "strong_calls": 1, "light_calls": 0,
                                "tokens": 4000}
    decision = [entry for entry in ticked["journal"]
                if "decision_id" in entry and entry.get("ai_used") is not None]
    assert len(decision) == 1
    assert decision[0]["ai_used"] is False
    assert decision[0]["cost"] == {"tier": "rule", "tokens": 0}
    assert _ai_failed_entries(ticked) == []
    assert _json_roundtrip(ticked) == ticked


def check_tick_ai_failure_charged_and_isolated():
    """#366：调用抛异常 → 记账 + ai_failed 记录 + 后续 NPC 照常处理。"""
    block = _n5_block([_n5_npc("npc-%d" % index, place="market")
                       for index in range(2)])
    port = _RaisingPort("模型超时")

    ticked = neura.tick(block, _view(options=[_option("op-rule")]),
                        port=port, now_minute=600)

    assert len(port.ai_calls) == 1, "失败不重试；突发上限内只尝试一次"
    assert ticked["budget"] == {"day": 0, "strong_calls": 1, "light_calls": 0,
                                "tokens": 4000}
    failed = _ai_failed_entries(ticked)
    assert len(failed) == 1
    assert failed[0]["npc_id"] == "npc-0"
    assert failed[0]["at_minute"] == 600
    assert failed[0]["tier"] == "L0"
    assert "RuntimeError" in failed[0]["error"]
    assert "模型超时" in failed[0]["error"]
    # 同 tick 的后一个 NPC 不受影响：仍写决策并提交规则提案。
    assert ticked["npcs"]["npc-0"]["last_tick_minute"] == 600
    assert ticked["npcs"]["npc-1"]["last_tick_minute"] == 600
    assert len(port.submits) == 2
    assert _json_roundtrip(ticked) == ticked


def check_tick_ai_budget_exhausted_skips_call():
    """#366：`can_call_ai` 为 false → 不调用、不记账、不涨 burst。"""
    used_calls = {"day": 0, "strong_calls": neura.STRONG_CALLS_PER_DAY,
                  "light_calls": 0, "tokens": 24000}
    block = _n5_block([_n5_npc(place="market")])
    block["budget"] = dict(used_calls)
    port = _FakePort(ai_options=[_option("op-ai", source="ai")])

    ticked = neura.tick(block, _view(options=[_option("op-rule")]),
                        port=port, now_minute=600)

    assert port.ai_calls == [], "当日 strong 次数已满不得调用"
    assert ticked["budget"] == used_calls
    assert _ai_failed_entries(ticked) == []

    # token 余量不足（59000 + 4000 > 60000）：同样不调用。
    block2 = _n5_block([_n5_npc(place="market")])
    block2["budget"] = {"day": 0, "strong_calls": 0, "light_calls": 0,
                        "tokens": 59000}
    port2 = _FakePort(ai_options=[_option("op-ai", source="ai")])
    ticked2 = neura.tick(block2, _view(options=[_option("op-rule")]),
                         port=port2, now_minute=600)
    assert port2.ai_calls == []
    assert ticked2["budget"]["tokens"] == 59000
    assert ticked2["budget"]["strong_calls"] == 0


def check_tick_burst_counts_failed_attempts():
    """#366：失败尝试也占突发名额（≤ 3 次 M / tick）。"""
    block = _n5_block([_n5_npc("npc-%d" % index, place="temple",
                               importance=0.9) for index in range(4)])
    view = _view(options=[_option("op-rule")])
    view["bands"] = {}
    port = _FakePort(ai_options=[])

    ticked = neura.tick(block, view, port=port, now_minute=1441)

    assert len(port.ai_calls) == 3, "light 突发上限 ≤ 3，空表也计数"
    assert ticked["budget"] == {"day": 1, "strong_calls": 0, "light_calls": 3,
                                "tokens": 3600}
    assert _ai_failed_entries(ticked) == []


def check_tick_budget_visible_to_later_npcs():
    """#366：同 tick 内后一个 NPC 看到前一个已消耗的预算（L0 → L1 降档）。"""
    block = _n5_block([_n5_npc("npc-%d" % index, place="market")
                       for index in range(2)])
    block["budget"] = {"day": 0, "strong_calls": 0, "light_calls": 0,
                       "tokens": 44000}
    port = _BudgetPort(ai_options=[])

    ticked = neura.tick(block, _view(options=[_option("op-rule")]),
                        port=port, now_minute=600)

    # 第一个 NPC 走 L0/strong（44000 → 48000）；第二个已到 80% → L1/light。
    assert [snapshot["tokens"] for snapshot in port.budgets] == [44000, 48000]
    assert ticked["budget"] == {"day": 0, "strong_calls": 1, "light_calls": 1,
                                "tokens": 49200}


def check_tick_budget_invariant_and_replay():
    """#366：预算恒等式成立；同输入重跑确定；同分钟重 tick 不重复记账。"""
    for ai_options in ([], [_option("op-ai", source="ai")]):
        block = _n5_block([_n5_npc(place="market")])
        view = _view(options=[_option("op-rule")])
        first = neura.tick(block, view, port=_FakePort(ai_options=ai_options),
                           now_minute=600)
        second = neura.tick(block, view, port=_FakePort(ai_options=ai_options),
                            now_minute=600)
        assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
            json.dumps(second, ensure_ascii=False, sort_keys=True), "重跑应确定"

        budget = first["budget"]
        assert 4000 * budget["strong_calls"] + \
            1200 * budget["light_calls"] <= neura.NPC_DAILY_TOKEN_BUDGET
        assert budget["tokens"] == 4000 * budget["strong_calls"] + \
            1200 * budget["light_calls"]

        # 同一分钟重 tick：NPC 不到期 → 不调用、不重复记账、不重复 journal。
        replay = neura.tick(first, view, port=_FakePort(ai_options=ai_options),
                            now_minute=600)
        assert replay["budget"] == budget
        assert len(replay["journal"]) == len(first["journal"])


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
        check_score_terms_recompute,
        check_jitter_is_recomputable_and_not_dice,
        check_choose_tie_break_is_order_independent,
        check_decide_is_deterministic,
        check_decide_record_fields,
        check_switch_reason_six_values,
        check_no_candidates_is_recorded,
        check_to_proposal_matches_action_design,
        check_proposal_passes_prism_validate,
        check_idempotency_key_format_and_stability,
        check_decide_and_proposal_are_pure,
        check_claim_classes_and_purposes,
        check_learn_writes_active_claim,
        check_learn_merges_same_claim,
        check_learn_rejects_bad_claims,
        check_supersede_chain,
        check_correction_memory_summary_cap,
        check_purpose_field_sets,
        check_only_active_claims_reach_context,
        check_five_classes_never_in_world_facts,
        check_double_npc_secret_visibility,
        check_no_foreign_proposition,
        check_context_memory_limit,
        check_context_is_deterministic_and_pure,
        check_build_candidate_body_shape,
        check_role_literal_appears_once,
        check_n5_constants,
        check_layer_of_short_circuit,
        check_layer_of_goal_target_and_importance_formula,
        check_layer_recomputed_same_tick,
        check_due_npcs_frequencies_and_order,
        check_force_due_ids_forces_due,
        check_force_due_ids_does_not_resurrect_unexplored,
        check_charge_and_cross_day_reset,
        check_can_call_ai_gates,
        check_degrade_ladder_and_monotonic,
        check_tick_offline_schedule_move,
        check_tick_schedule_moves_each_day,
        check_tick_burst_and_charge,
        check_tick_submit_keys_stable_and_idempotent,
        check_tick_l3_idle_journal,
        check_catch_up_converges_and_defers,
        check_catch_up_small_gap_no_defer,
        check_catch_up_idempotent,
        check_tick_is_pure,
        check_tick_schedule_move_writeback_follows_submit,
        check_tick_schedule_move_conflict_retries_next_tick,
        check_tick_plan_writeback_follows_submit,
        check_tick_plan_not_committed_keeps_retryable_state,
        check_tick_charges_empty_candidates,
        check_tick_charges_rejected_candidates,
        check_tick_ai_failure_charged_and_isolated,
        check_tick_ai_budget_exhausted_skips_call,
        check_tick_burst_counts_failed_attempts,
        check_tick_budget_visible_to_later_npcs,
        check_tick_budget_invariant_and_replay,
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
