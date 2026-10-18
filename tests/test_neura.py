#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neura 内核（M11-N1）回归测试：块结构、惰性迁移、确定性裁剪与单位哈希。

逐条覆盖 `Issue #339` 的验收清单：

- `empty_block()` 经 `json.dumps` → `json.loads` 往返逐字段相等；
- 缺 `needs` 六键的旧块 → `migrate_block` 补 50；已有合法值（如 73）保持；
- `block["version"] > NEURA_VERSION` → 抛 `NeuraError`（有断言）；
- `prune_block` 对 300 条 journal / 200 条 memory 裁到上限，连续两次结果全等；
- `stable_unit("a","b")` 确定、不同输入不同输出、100 万次分布均匀（±1%）；
- `import neura` 无 IO、无副作用，未引入 `state` / `chron` / `notdnd_web` /
  `prism_core` / `prism_guide` / `atlas`。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_neura.py` 运行。
"""

import ast
import json
import os
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
