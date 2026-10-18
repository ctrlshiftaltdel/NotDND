#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neura —— NEURA 内核骨架（M11-N1）：块结构、惰性迁移、确定性裁剪与单位哈希（纯函数）。

本模块实现 `NEURA-DESIGN.md` §4.2（运行时字段表）、§9.1–§9.2（模块与
`blocks.neura` 不透明块）与 §11 N1：

- `NEURA_VERSION` / `NeuraError`：块版本与模块自有异常；
- `empty_block()` / `empty_npc()`：可 JSON 序列化的空块与空 NPC 运行时态；
- `migrate_block()`：惰性迁移——缺 `needs` 六键补 50、已合法值**不覆盖**、
  更高版本**拒载**、脏块降级为空块；
- `prune_block()`：按 `MEMORY_MAX = 128`（每 NPC）与 `JOURNAL_MAX = 256`
  （每战役）**先裁最旧**，两次裁剪结果相同（幂等）；
- `stable_unit(*parts)`：确定性单位哈希，返回 `[0, 1)`，用于「同种子同结果」
  的确定性抖动（§5.3 / §10.1 1.5）。

块形状（§4.1 / §9.2 / §5.8）::

    {
      "version": 1,              # 权威版本键（Issue #339 验收用这个名字）
      "schema_version": 1,       # 兼容别名（NEURA-DESIGN.md §9.2 的写法）
      "campaign_seed": 0,        # §5.8：确定性抖动的战役种子
      "npcs": {npc_id: NPC},     # §4.2 的每 NPC 运行时态
      "journal": [],             # 整战役决策留痕，上限 JOURNAL_MAX
      "budget": {day, strong_calls, light_calls, tokens},   # §8.2
    }

`NPC` 的 `needs` / `memory` / `plan` 等字段在 `npcs` 条目内（§4.2 的
`npc_id` 行明确「块内以 `npcs` 字典的键表达」）；`plan` 为空时**键不存在**
（§4.2 字段级不变量，不用 `null`）。

**硬约束**：纯函数、无 IO、无副作用；只 import 标准库 `copy` / `hashlib`，
**不得** import `state` / `chron` / `notdnd_web` / `prism_*` / `atlas*`
（§9.1）。本模块不写盘、不迁移磁盘上的老档；落盘迁移由 N5 / N7 接线时调用。
"""

import copy
import hashlib

# --------------------------------------------------------------------------
# §11 N1 常量
# --------------------------------------------------------------------------

NEURA_VERSION = 1          # §9.2：块版本；更高版本由 migrate_block 拒载
MEMORY_MAX = 128           # §12.1 P3：每 NPC 的记忆硬上限
JOURNAL_MAX = 256          # §12.1 P3：每战役的决策留痕硬上限

NEED_KEYS = ("safety", "wealth", "power", "relation", "duty", "curiosity")
NEED_DEFAULT = 50          # §4.2：缺失的 needs 键补中位数 50（不猜 0）
NEED_MIN, NEED_MAX = 0, 100

ABILITY_KEYS = ("mgt", "fin", "vig", "ins", "mnd", "pre")

BUDGET_KEYS = ("day", "strong_calls", "light_calls", "tokens")

# §4.2：plan 为空时键不存在；这些字段是每 NPC 的「空态」。
_NPC_LIST_FIELDS = ("goals", "schedule", "knowledge", "secrets", "memory")


class NeuraError(Exception):
    """NEURA 块自身错误：版本高于本实现等（与 `chron.ChronError` 同款口径）。"""


# --------------------------------------------------------------------------
# 小工具（纯函数）
# --------------------------------------------------------------------------

def _is_int(value):
    """真正的整数（`bool` 是 `int` 子类，但块里不把它当计数）。"""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _count(value, default=0):
    """非负整数；非法值降级为 `default`。"""
    return value if _is_int(value) and value >= 0 else default


def _clone(value):
    """深拷贝，保证返回的新块不与入参共享可变对象（纯函数口径）。"""
    return copy.deepcopy(value)


def _text(value, default=""):
    return value if isinstance(value, str) else default


# --------------------------------------------------------------------------
# §9.2 空块与空 NPC
# --------------------------------------------------------------------------

def empty_block():
    """§9.2 的空 `blocks.neura`：可 JSON 序列化，默认值不共享可变对象。

    `version` 是 Issue #339 验收使用的权威版本键；`schema_version` 是
    `NEURA-DESIGN.md` §9.2 的同义写法，两者写入同值并由 `migrate_block` 保持同步。
    `npcs` 为空；新 NPC 用 `empty_npc()` 生成（见 `migrate_block` 的迁移口径）。
    """
    return {
        "version": NEURA_VERSION,
        "schema_version": NEURA_VERSION,
        "campaign_seed": 0,
        "npcs": {},
        "journal": [],
        "budget": {key: 0 for key in BUDGET_KEYS},
    }


def empty_npc(npc_id="", scenario_id=""):
    """§4.2 的空 NPC 运行时态（键 = 静态档案的 `npcs[].id`）。

    - `needs` 六键全在，初值 50（`migrate_block` 对旧块同口径）；
    - `need_weights` 六键全在，空态用 1.0（等权；角色常量由 N5 按性格生成）；
    - `memory` / `journal` 的上限裁剪由 `prune_block` 负责；
    - `plan` **不出现**：§4.2 要求「为空时键不存在」，不用 `null`。
    """
    return {
        "display_ref": {"scenario_id": _text(scenario_id), "npc_id": _text(npc_id)},
        "importance": 0.0,
        "abilities": {key: None for key in ABILITY_KEYS},
        "needs": {key: NEED_DEFAULT for key in NEED_KEYS},
        "need_weights": {key: 1.0 for key in NEED_KEYS},
        "goals": [],
        "schedule": [],
        "location": {"place_id": "", "since_minute": 0, "band_to_party": "same"},
        "relations": {},
        "knowledge": [],
        "secrets": [],
        "memory": [],
        "resources": {"coin": 0, "stock": {}, "obligations": []},
        "last_tick_minute": 0,
    }


# --------------------------------------------------------------------------
# §9.2 惰性迁移
# --------------------------------------------------------------------------

def migrate_block(block, notes=None):
    """§9.2 惰性迁移：**只补不覆盖**；脏块降级；更高版本**拒载**。

    - `block is None`（老档没有 `neura` 块）→ 空块；
    - 不是对象 / 版本脏值 → 整体降级为空块（一个坏形状不猜内容）；
    - 版本**高于** `NEURA_VERSION` → 抛 `NeuraError`（不静默降级）；
    - 缺字段 → 只补该字段；已有的合法值（如 `needs.safety == 73`）逐字保留；
      单个脏字段只降级它自己；
    - 返回**新块**，不改写入参；`notes` 给了就追加说明（调用方回显「这里迁移过」）。
    """
    notes = [] if notes is None else notes

    if block is None:
        notes.append("neura 块缺失：按惰性迁移补空块（NEURA-DESIGN §9.2）")
        return empty_block()
    if not isinstance(block, dict):
        notes.append("neura 块形状非法（%s）：整体降级为空块" % type(block).__name__)
        return empty_block()

    version = block.get("version", block.get("schema_version", NEURA_VERSION))
    if not _is_int(version):
        notes.append("neura.version 脏值（%r）：整体降级为空块" % (version,))
        return empty_block()
    schema = block.get("schema_version", version)
    if not _is_int(schema):
        notes.append("neura.schema_version 脏值（%r）：按 version 迁移" % (schema,))
        schema = version
    if version > NEURA_VERSION or schema > NEURA_VERSION:
        raise NeuraError(
            "拒载：neura 块 version=%d / schema_version=%d 高于本实现 %d（NEURA-DESIGN §9.2）"
            % (version, schema, NEURA_VERSION)
        )
    if version < NEURA_VERSION or schema < NEURA_VERSION:
        notes.append(
            "neura 块 version=%d 低于本实现 %d：已按惰性迁移补齐"
            % (min(version, schema), NEURA_VERSION)
        )

    migrated = empty_block()
    migrated["campaign_seed"] = _count(block.get("campaign_seed", 0))
    if block.get("campaign_seed") is not None and \
            migrated["campaign_seed"] != block.get("campaign_seed"):
        notes.append("neura.campaign_seed 脏值（%r）：降级为 0"
                     % (block.get("campaign_seed"),))

    raw_npcs = block.get("npcs", {})
    if isinstance(raw_npcs, dict):
        for npc_id in sorted(raw_npcs, key=lambda key: (type(key).__name__, str(key))):
            if not isinstance(npc_id, str):
                notes.append("neura.npcs 的键 %r 不是字符串：已剔除" % (npc_id,))
                continue
            raw_npc = raw_npcs[npc_id]
            if not isinstance(raw_npc, dict):
                notes.append("neura.npcs[%s] 不是对象：已剔除" % npc_id)
                continue
            migrated["npcs"][npc_id] = _migrate_npc(raw_npc, npc_id, notes)
    elif "npcs" in block:
        notes.append("neura.npcs 脏值（%s）：降级为空表" % type(raw_npcs).__name__)

    raw_journal = block.get("journal", [])
    if isinstance(raw_journal, list):
        kept = [entry for entry in raw_journal if isinstance(entry, dict)]
        if len(kept) != len(raw_journal):
            notes.append("neura.journal 有 %d 条不是对象：已剔除"
                         % (len(raw_journal) - len(kept)))
        migrated["journal"] = _clone(kept)
    elif "journal" in block:
        notes.append("neura.journal 脏值（%s）：降级为空表"
                     % type(raw_journal).__name__)

    raw_budget = block.get("budget", {})
    if isinstance(raw_budget, dict):
        for key in BUDGET_KEYS:
            value = raw_budget.get(key, 0)
            if _is_int(value) and value >= 0:
                migrated["budget"][key] = value
            else:
                if key in raw_budget:
                    notes.append("neura.budget.%s 脏值（%r）：降级为 0" % (key, value))
    elif "budget" in block:
        notes.append("neura.budget 脏值（%s）：降级为零账本"
                     % type(raw_budget).__name__)

    return migrated


def _migrate_npc(raw, npc_id, notes):
    """单个 NPC 的惰性迁移（由 `migrate_block` 调用；返回新对象）。"""
    ref = raw.get("display_ref")
    scenario_id = ""
    if isinstance(ref, dict):
        scenario_id = _text(ref.get("scenario_id"))
    npc = empty_npc(npc_id=npc_id, scenario_id=scenario_id)

    importance = raw.get("importance", 0.0)
    if _is_number(importance) and 0.0 <= importance <= 1.0:
        npc["importance"] = importance
    elif "importance" in raw:
        notes.append("neura.npcs[%s].importance 非法（%r）：降级为 0.0"
                     % (npc_id, importance))

    abilities = raw.get("abilities", {})
    if isinstance(abilities, dict):
        for key in ABILITY_KEYS:
            value = abilities.get(key)
            if value is None or _is_int(value):
                npc["abilities"][key] = value
            else:
                notes.append("neura.npcs[%s].abilities.%s 非法（%r）：降级为 null"
                             % (npc_id, key, value))
    elif "abilities" in raw:
        notes.append("neura.npcs[%s].abilities 脏值（%s）：六维全部 null"
                     % (npc_id, type(abilities).__name__))

    needs = raw.get("needs", {})
    if not isinstance(needs, dict):
        notes.append("neura.npcs[%s].needs 脏值（%s）：六键全部补 %d"
                     % (npc_id, type(needs).__name__, NEED_DEFAULT))
        needs = {}
    missing = [key for key in NEED_KEYS if key not in needs]
    if missing:
        notes.append("neura.npcs[%s].needs 缺 %d 键（%s）：补 %d"
                     % (npc_id, len(missing), " / ".join(missing), NEED_DEFAULT))
    for key in NEED_KEYS:
        value = needs.get(key, NEED_DEFAULT)
        if _is_int(value) and NEED_MIN <= value <= NEED_MAX:
            npc["needs"][key] = value
        else:
            notes.append("neura.npcs[%s].needs.%s 非法（%r）：降级为 %d"
                         % (npc_id, key, value, NEED_DEFAULT))
    extra = sorted(set(needs) - set(NEED_KEYS), key=str)
    if extra:
        notes.append("neura.npcs[%s].needs 有未知键（%s）：已剔除"
                     % (npc_id, " / ".join(map(str, extra))))

    weights = raw.get("need_weights", {})
    if not isinstance(weights, dict):
        notes.append("neura.npcs[%s].need_weights 脏值（%s）：六键全部补 1.0"
                     % (npc_id, type(weights).__name__))
        weights = {}
    for key in NEED_KEYS:
        value = weights.get(key, 1.0)
        if _is_number(value) and 0.0 <= value <= 1.0:
            npc["need_weights"][key] = value
        else:
            notes.append("neura.npcs[%s].need_weights.%s 非法（%r）：降级为 1.0"
                         % (npc_id, key, value))

    for field in _NPC_LIST_FIELDS:
        value = raw.get(field, [])
        if isinstance(value, list):
            kept = [item for item in value if isinstance(item, dict)]
            if len(kept) != len(value):
                notes.append("neura.npcs[%s].%s 有 %d 条不是对象：已剔除"
                             % (npc_id, field, len(value) - len(kept)))
            npc[field] = _clone(kept)
        elif field in raw:
            notes.append("neura.npcs[%s].%s 脏值（%s）：降级为空表"
                         % (npc_id, field, type(value).__name__))

    if "plan" in raw:
        plan = raw["plan"]
        if isinstance(plan, dict) and plan:
            npc["plan"] = _clone(plan)
        elif plan is not None:
            notes.append("neura.npcs[%s].plan 为空或形状非法（%s）：已丢弃"
                         % (npc_id, type(plan).__name__))

    location = raw.get("location", {})
    if isinstance(location, dict):
        npc["location"]["place_id"] = _text(location.get("place_id"))
        npc["location"]["since_minute"] = _count(location.get("since_minute"))
        npc["location"]["band_to_party"] = _text(
            location.get("band_to_party"), "same")
    elif "location" in raw:
        notes.append("neura.npcs[%s].location 脏值（%s）：降级为默认"
                     % (npc_id, type(location).__name__))

    relations = raw.get("relations", {})
    if isinstance(relations, dict):
        kept = {key: value for key, value in relations.items()
                if isinstance(key, str) and isinstance(value, dict)}
        if len(kept) != len(relations):
            notes.append("neura.npcs[%s].relations 有 %d 条形状非法：已剔除"
                         % (npc_id, len(relations) - len(kept)))
        npc["relations"] = _clone(kept)
    elif "relations" in raw:
        notes.append("neura.npcs[%s].relations 脏值（%s）：降级为空表"
                     % (npc_id, type(relations).__name__))

    resources = raw.get("resources", {})
    if isinstance(resources, dict):
        npc["resources"]["coin"] = _count(resources.get("coin"))
        stock = resources.get("stock", {})
        if isinstance(stock, dict):
            npc["resources"]["stock"] = {
                key: value for key, value in stock.items()
                if isinstance(key, str) and _is_int(value) and value >= 0
            }
        obligations = resources.get("obligations", [])
        if isinstance(obligations, list):
            npc["resources"]["obligations"] = _clone(
                [item for item in obligations if isinstance(item, dict)])
    elif "resources" in raw:
        notes.append("neura.npcs[%s].resources 脏值（%s）：降级为默认"
                     % (npc_id, type(resources).__name__))

    npc["last_tick_minute"] = _count(raw.get("last_tick_minute"))
    return npc


# --------------------------------------------------------------------------
# §9.2 / §12.1 P3 裁剪
# --------------------------------------------------------------------------

def _minute_of(entry, *names):
    for name in names:
        value = entry.get(name)
        if _is_int(value):
            return value
    return 0


def _id_of(entry, *names):
    for name in names:
        value = entry.get(name)
        if isinstance(value, str):
            return value
    return ""


def _journal_key(entry):
    """§4.2：按分钟裁最旧；同分钟按 id 字典序（确定性）。"""
    return (_minute_of(entry, "at_minute", "minute"),
            _id_of(entry, "decision_id", "id"))


def _memory_key(entry):
    return (_minute_of(entry, "minute"),
            _id_of(entry, "memory_id", "id"))


def prune_block(block, notes=None):
    """§9.2 裁剪（返回**新块**）：`JOURNAL_MAX`（战役）/ `MEMORY_MAX`（每 NPC）。

    - 超上限时**先裁最旧**（按 `minute`，同分钟按 id 字典序），保留最新 N 条；
    - 裁剪是确定性的：同一个块连续调用两次，结果**全等**（幂等）；
    - 入参不改写；`notes` 给了就追加说明。
    """
    notes = [] if notes is None else notes
    pruned = migrate_block(block, notes)

    journal = pruned["journal"]
    if len(journal) > JOURNAL_MAX:
        kept = sorted(journal, key=_journal_key)[-JOURNAL_MAX:]
        notes.append("neura.journal 裁剪：%d → %d 条（保留最新 %d 条，§12.1 P3）"
                     % (len(journal), len(kept), JOURNAL_MAX))
        pruned["journal"] = kept

    for npc_id in sorted(pruned["npcs"]):
        memory = pruned["npcs"][npc_id]["memory"]
        if len(memory) > MEMORY_MAX:
            kept = sorted(memory, key=_memory_key)[-MEMORY_MAX:]
            notes.append("neura.npcs[%s].memory 裁剪：%d → %d 条（保留最新 %d 条，§12.1 P3）"
                         % (npc_id, len(memory), len(kept), MEMORY_MAX))
            pruned["npcs"][npc_id]["memory"] = kept

    return pruned


# --------------------------------------------------------------------------
# §5.3 / §10.1 确定性单位哈希
# --------------------------------------------------------------------------

_UNIT53 = float(1 << 53)


def _encode_part(part):
    """无歧义的紧凑编码：类型标记 + 长度前缀，避免 `("a","bc")` / `("ab","c")` 碰撞。"""
    if isinstance(part, str):
        raw = part.encode("utf-8")
        return b"s%d:" % len(raw) + raw
    if isinstance(part, bool):
        return b"b1" if part else b"b0"
    if isinstance(part, int):
        raw = str(part).encode("ascii")
        return b"i%d:" % len(raw) + raw
    if isinstance(part, float):
        raw = repr(part).encode("ascii")
        return b"f%d:" % len(raw) + raw
    if part is None:
        return b"n"
    if isinstance(part, (list, tuple)):
        items = b"".join(_encode_part(item) for item in part)
        return b"l%d:" % len(items) + items
    if isinstance(part, dict):
        items = []
        for key in sorted(part, key=str):
            if not isinstance(key, str):
                raise TypeError("stable_unit 的 dict 键必须是字符串：%r" % (key,))
            items.append(_encode_part(key))
            items.append(_encode_part(part[key]))
        joined = b"".join(items)
        return b"d%d:" % len(joined) + joined
    raise TypeError("stable_unit 不支持的类型：%s" % type(part).__name__)


def stable_unit(*parts):
    """确定性单位哈希：同输入同输出，返回 `[0, 1)`。

    用 `blake2b` 而不是内置 `hash()`——后者按进程随机加盐，跨重启会漂移，
    直接破坏「同种子同结果」（§5.3 / §10.1 1.5）。标量参数走 `repr(tuple)` 快路径
    （表示无歧义：`("a","bc")` 与 `("ab","c")` 不同）；列表 / 元组 / 字典等复合值
    走带类型标记与长度前缀的规范编码。
    """
    if all(type(part) in (str, int, float, bool) or part is None for part in parts):
        payload = repr(parts).encode("utf-8")
    else:
        payload = b"".join(_encode_part(part) for part in parts)
    # 取前 53 位：结果严格落在 [0, 1)，且不依赖浮点舍入到 1.0。
    return (int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(),
                           "big") >> 11) / _UNIT53
