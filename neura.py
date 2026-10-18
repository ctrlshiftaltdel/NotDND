#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neura —— NEURA 内核（M11-N1 骨架 + M11-N2 硬约束过滤；全部纯函数）。

本模块实现 `NEURA-DESIGN.md` §4.2（运行时字段表）、§5.2 / §5.2.1（硬约束与
`view` 形状）、§9.1–§9.2（模块与 `blocks.neura` 不透明块）、§11 N1 与 §11 N2：

- `NEURA_VERSION` / `NeuraError`：块版本与模块自有异常；
- `empty_block()` / `empty_npc()`：可 JSON 序列化的空块与空 NPC 运行时态；
- `migrate_block()`：惰性迁移——缺 `needs` 六键补 50、已合法值**不覆盖**、
  更高版本**拒载**、脏块降级为空块；
- `prune_block()`：按 `MEMORY_MAX = 128`（每 NPC）与 `JOURNAL_MAX = 256`
  （每战役）**先裁最旧**，两次裁剪结果相同（幂等）；
- `stable_unit(*parts)`：确定性单位哈希，返回 `[0, 1)`，用于「同种子同结果」
  的确定性抖动（§5.3 / §10.1 1.5）；
- `hard_filter()` / `check_distance()` / `check_time()` / `check_resource()` /
  `check_law()` / `check_knowledge()`：**硬约束过滤**——候选先过距离 / 时间 /
  资源 / 法律 / 知识五道闸，拒单带固定错误码（N2）。

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

`hard_filter()` 的 `view` 由编排层按 §5.2.1 构造，`neura` **只读**：`view` 里
没有的东西就是不知道，本模块不访问任何全局 / 环境数据（不读盘、不联网、不读
环境变量）。N2 在 §5.2.1 的字段之外还读两个**补充键**（都由编排层注入）：

- `view["options"]`：本 tick 的**规则候选**池（`AI 候选` 在 ② 之后追加，
  同样要过单条 `check_*`）；缺键等价于空池；
- `view["budget"]`：`blocks.neura.budget` 的只读投影，仅用于判断「AI 候选是否
  还有额度」（§8.2），**不影响规则候选**。

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

# --------------------------------------------------------------------------
# §5.2 硬约束常量（M11-N2）
# --------------------------------------------------------------------------

# 固定错误码（§5.2 表格逐行对应；journal 靠它回答「他为什么没去」）。
E_DISTANCE = "E_DISTANCE"      # 距离：band 不通且 minutes 不够走完
E_TIME = "E_TIME"              # 时间：计划预算 / 不可打断日程 / 义务期限
E_RESOURCE = "E_RESOURCE"      # 资源：钱 / 存货不足，或到期义务未清
E_LAW = "E_LAW"                # 法律：命中禁令标签且落在禁令范围内
E_KNOWLEDGE = "E_KNOWLEDGE"    # 知识：缺有效认知或不知道地点
# §5.2：**不拒选项**，只禁 AI 候选（降级为规则候选，§8.3 L2）。
E_LAYER_BUDGET = "E_LAYER_BUDGET"
# N2 本地补充：候选形状不合法（缺 option_id / minutes 非整数等）。
# §5.2 表外的形状码——业务约束只认上面五个，形状问题不该被当成「规则拒绝」。
E_OPTION = "E_OPTION"

HARD_CODES = (E_DISTANCE, E_TIME, E_RESOURCE, E_LAW, E_KNOWLEDGE)

# §5.2 距离：这三个 band 视为「走得到」，不必再看分钟数。
BAND_PASS = ("same", "adjacent", "near")

# §5.2.1：`view.laws` 的来源标注；缺表即 `"none"`（§12.1 P8）。
LAW_DECLARED_SOURCES = ("world", "rules")
# P8 把 `laws` schema 留到 M12；v1 只认这两个显式「容忍」值，其余一律硬禁。
LAW_TOLERANT_THRESHOLDS = ("allow", "tolerate")

# Option 形状（§5.2）：`source` 只有这两档，AI 候选可被审计区分。
OPTION_SOURCES = ("rule", "ai")
MAX_OPTION_MINUTES = 1440          # 一天；更长的动作由编排层拆步
MINUTES_PER_DAY = 1440             # §4.2：schedule 的 tod 口径（0..1439）

# §8.2：日预算 H ≤ 6 次、M ≤ 30 次（N5 接真正的 token 账本，这里只判额度用尽）。
STRONG_CALLS_PER_DAY = 6
LIGHT_CALLS_PER_DAY = 30


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


# --------------------------------------------------------------------------
# §5.2 / §5.2.1 硬约束过滤（M11-N2）
#
# 五道闸按 §5.2 表格的**行序**短路：距离 → 时间 → 资源 → 法律 → 知识。
# 每个候选至多留一条 `rejected`（带码），全灭时 `ok == False`。
# 所有函数只读 `npc` / `option` / `view`，返回**新**对象，不改入参。
# --------------------------------------------------------------------------

def check_distance(npc, option, view):
    """§5.2 距离：`bands` 直通**或** `travel_minutes ≤ minutes`，否则 `E_DISTANCE`。

    两条路都要判：`same/adjacent/near` 不看分钟数（本节就是「近到不用算」）；
    band 为 `far` / `mid` 时，走得完（`travel_minutes ≤ option.minutes`）仍然放行。
    两条路都拿不到数据 → 按「不知道」拒（`view` 之外不猜距离）。
    """
    here = _npc_place(npc)
    there = _text(option.get("place_id"))
    if there == here:
        return None

    band = _band_of(view, here, there)
    if band in BAND_PASS:
        return None

    travel = _travel_minutes(view, there)
    minutes = option.get("minutes")
    if travel is not None and _is_int(minutes) and travel <= minutes:
        return None

    if band is None and travel is None:
        detail = "距离未知：view.bands 与 view.travel_minutes 都没有 %r（%r → %r）" \
                 % (there, here, there)
    elif travel is None:
        detail = "band=%r 不可直达，且 view.travel_minutes 缺 %r 的耗时" % (band, there)
    else:
        detail = "band=%r 不可直达，走到 %r 需 %d 分钟 > minutes=%d" \
                 % (band, there, travel, minutes if _is_int(minutes) else -1)
    return _reject(E_DISTANCE, detail, there)


def check_time(npc, option, view, now_minute):
    """§5.2 时间：三条判据依次短路，任一命中即 `E_TIME`。

    ① `option.minutes ≤ 当前计划剩余`（`plan.until_minute − now_minute`；无计划则不设限）；
    ② 与 `schedule` 的**不可打断** slot 冲突——时间窗重叠、且动作不在该 slot 的地点；
    ③ `now_minute + minutes` 越过某条 `resources.obligations[].due_minute` 时，
       只放行**指向该义务**的选项（`target_ref` 命中 `to`，或 `fulfill_duty` 无指定）。
    """
    minutes = option.get("minutes")
    if not _is_int(minutes):
        return None

    plan = npc.get("plan")
    remaining = _plan_remaining(plan, now_minute)
    if remaining is not None and minutes > remaining:
        return _reject(
            E_TIME,
            "当前计划只剩 %d 分钟，选项需要 %d 分钟" % (remaining, minutes),
            _text(plan.get("plan_id")) or None,
        )

    tod = now_minute % MINUTES_PER_DAY if _is_int(now_minute) else 0
    windows = _tod_windows(tod, minutes)
    schedule = npc.get("schedule")
    if windows and isinstance(schedule, list):
        place = _text(option.get("place_id"))
        for slot in schedule:
            if not isinstance(slot, dict) or slot.get("interruptible") is not False:
                continue
            start = slot.get("from_tod_minute")
            end = slot.get("to_tod_minute")
            if not (_is_int(start) and _is_int(end)) or start >= end:
                continue
            slot_place = _text(slot.get("place_id"))
            if slot_place and slot_place == place:
                continue          # 就在岗位上做，不算冲突
            if not any(_overlaps_window(a, b, start, end) for a, b in windows):
                continue
            return _reject(
                E_TIME,
                "与不可打断的日程 %r（%d–%d 分钟）冲突，选项却去 %r"
                % (_text(slot.get("slot_id")), start, end, place),
                _text(slot.get("slot_id")) or None,
            )

    if _is_int(now_minute):
        end_minute = now_minute + minutes
        for obligation in _obligations(npc):
            due = obligation.get("due_minute")
            if not _is_int(due) or end_minute <= due:
                continue
            if _serves_obligation(option, obligation):
                continue
            return _reject(
                E_TIME,
                "选项将在第 %d 分钟结束，已越过义务 %r 的期限 %d，且不指向该义务"
                % (end_minute, _text(obligation.get("to")), due),
                _text(obligation.get("to")) or None,
            )
    return None


def check_resource(npc, option, view):
    """§5.2 资源：钱 / 存货够不够，到期义务是否只放行 `fulfill_duty` 类。

    只做**可行性**判断（§4.2：权威库存仍在规则包，这里不同步账）。
    `now_minute` 由 `view` 提供（`check_resource` 的签名里没有它，§5.2）。
    """
    coin = _npc_coin(npc)
    stock = _npc_stock(npc)
    cost = option.get("cost")
    if not isinstance(cost, dict):
        cost = {}

    need_coin = cost.get("coin", 0)
    if _is_int(need_coin) and need_coin > coin:
        return _reject(
            E_RESOURCE,
            "需要 %d 枚硬币，身上只有 %d 枚" % (need_coin, coin),
            None,
        )

    cost_stock = cost.get("stock")
    if isinstance(cost_stock, dict):
        for item in sorted(cost_stock, key=str):
            need = cost_stock[item]
            if not _is_int(need):
                continue
            have = stock.get(item, 0)
            if need > have:
                return _reject(
                    E_RESOURCE,
                    "需要 %r × %d，身上只有 × %d" % (item, need, have),
                    item,
                )

    now_minute = view.get("now_minute") if isinstance(view, dict) else None
    if _is_int(now_minute) and not _is_duty(option):
        for obligation in _obligations(npc):
            due = obligation.get("due_minute")
            if not _is_int(due) or due > now_minute:
                continue
            return _reject(
                E_RESOURCE,
                "义务 %r 已于第 %d 分钟到期（现在第 %d 分钟），只允许 fulfill_duty 类选项"
                % (_text(obligation.get("to")), due, now_minute),
                _text(obligation.get("to")) or None,
            )
    return None


def check_law(npc, option, view):
    """§5.2 法律：`forbid_tags ∩ option.tags ≠ ∅` 且在禁令范围内 → `E_LAW`。

    §12.1 **P8：`view` 缺 `laws` 即空表（`law_source == "none"`）——不抛错、
    不误拒**；v1 的法律约束等价于「不设限」，这是**拍板结论**，不是漏实现。
    `threshold` 取容忍值（`allow` / `tolerate`）时降级为「高风险但可行」，
    由 `hard_filter` 在返回的候选副本上标 `law_risk`（交 ②③ 的 `risk` 处理）。
    缺/空 `scope_place_ids` 视为全域适用（一条不留范围的禁令不该变成空文）。
    """
    place = _text(option.get("place_id"))
    tags = set(_string_list(option.get("tags")))
    for law in _laws(view):
        hit = _law_hit_tags(law, tags)
        if not hit or not _law_covers(law, place):
            continue
        if _law_tolerated(law):
            continue
        return _reject(
            E_LAW,
            "命中禁令 %r 的标签（%s），且 %r 在其管辖区内"
            % (_text(law.get("id")), " / ".join(sorted(hit)), place),
            _text(law.get("id")) or None,
        )
    return None


def check_knowledge(npc, option, view):
    """§5.2 知识：`requires_claims` 每条须 `status == "active"`，`requires_place` 须已知。

    与 §6 的 `select_context` 同源：`view.known_places` 是**知识门过滤后**的
    该 NPC 已知地点，本函数不自己推算可达性。
    """
    active = _active_claim_ids(npc)
    for claim_id in _string_list(option.get("requires_claims")):
        if claim_id not in active:
            return _reject(
                E_KNOWLEDGE,
                "缺少有效认知 %r（不在 knowledge 中或 status 不是 active）" % claim_id,
                claim_id,
            )
    for place_id in _string_list(option.get("requires_place")):
        if not _knows_place(view, place_id):
            return _reject(
                E_KNOWLEDGE,
                "不知道地点 %r（不在 view.known_places）" % place_id,
                place_id,
            )
    return None


def hard_filter(npc, view, now_minute):
    """§5.2 硬约束过滤：返回 `{"ok", "options", "rejected", ...}`。

    - `options`：过了五道闸的候选（**深拷贝**，调用方可随意加字段）；
    - `rejected`：`[{"option_id", "code", "detail"}]`，**每个**候选都留码，不吞；
    - `ok`：`options` 非空即 `True`；全灭时 `{"ok": False, "options": []}`；
    - `law_source`：`"world"` / `"rules"` / `"none"`（缺 `laws` → `"none"`，P8）；
    - `ai_allowed`：当日 AI 额度是否还在（§8.2）；`False` 时只禁 AI 候选，
      **不拒规则候选**，并在 `notes` 里留 `E_LAYER_BUDGET`（记 journal 用）；
    - `notes`：不构成拒单的说明（目前只有 `E_LAYER_BUDGET`）。

    候选池取 `view["options"]`；`view` 不是对象 / 缺该键都按空池处理
    （「`view` 里没有的东西就是不知道」，不猜）。
    """
    view = view if isinstance(view, dict) else {}
    npc = npc if isinstance(npc, dict) else {}

    result = {
        "ok": False,
        "options": [],
        "rejected": [],
        "law_source": _law_source(view),
        "ai_allowed": True,
        "notes": [],
    }

    options = view.get("options")
    if not isinstance(options, list):
        options = []

    for raw in options:
        shape = _option_error(raw)
        if shape is not None:
            result["rejected"].append({
                "option_id": _text(raw.get("option_id")) if isinstance(raw, dict) else "",
                "code": E_OPTION,
                "detail": shape,
            })
            continue

        option = _clone(raw)
        option.setdefault("source", "rule")
        reject = (
            check_distance(npc, option, view)
            or check_time(npc, option, view, now_minute)
            or check_resource(npc, option, view)
            or check_law(npc, option, view)
            or check_knowledge(npc, option, view)
        )
        if reject is not None:
            result["rejected"].append({
                "option_id": option["option_id"],
                "code": reject["code"],
                "detail": reject["detail"],
            })
            continue

        risks = _law_risk_ids(option, view)
        if risks:
            option["law_risk"] = risks
        result["options"].append(option)

    allowed, note = _ai_allowed(view, now_minute)
    result["ai_allowed"] = allowed
    if note is not None:
        result["notes"].append(note)

    result["ok"] = bool(result["options"])
    return result


# --------------------------------------------------------------------------
# 硬约束的内部工具（全部纯函数，只做形状容错与只读投影）
# --------------------------------------------------------------------------

def _reject(code, detail, ref=None):
    """§5.2 的 `Reject`：`{"code", "detail", "ref"}`（布尔回答不了「为什么」）。"""
    return {"code": code, "detail": detail, "ref": ref}


def _string_list(value):
    """只保留字符串元素；非列表一律当空表（形状问题由 `_option_error` 拦）。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _npc_place(npc):
    location = npc.get("location")
    if isinstance(location, dict):
        return _text(location.get("place_id"))
    return ""


def _npc_coin(npc):
    resources = npc.get("resources")
    if isinstance(resources, dict):
        return _count(resources.get("coin"))
    return 0


def _npc_stock(npc):
    resources = npc.get("resources")
    if isinstance(resources, dict):
        stock = resources.get("stock")
        if isinstance(stock, dict):
            return {key: value for key, value in stock.items()
                    if isinstance(key, str) and _is_int(value) and value >= 0}
    return {}


def _obligations(npc):
    resources = npc.get("resources")
    if isinstance(resources, dict):
        obligations = resources.get("obligations")
        if isinstance(obligations, list):
            return [item for item in obligations if isinstance(item, dict)]
    return []


def _band_of(view, here, there):
    """§5.2.1：`bands` 的键是 `(a, b)` 元组（编排层内存构造，非 JSON 往返）。"""
    bands = view.get("bands")
    if isinstance(bands, dict):
        band = bands.get((here, there))
        if isinstance(band, str):
            return band
    return None


def _travel_minutes(view, place):
    travel = view.get("travel_minutes")
    if isinstance(travel, dict):
        value = travel.get(place)
        if _is_int(value) and value >= 0:
            return value
    return None


def _plan_remaining(plan, now_minute):
    """§5.2 ①：「当前计划剩余预算」= `plan.until_minute − now_minute`；无计划不设限。"""
    if not isinstance(plan, dict) or not _is_int(now_minute):
        return None
    until = plan.get("until_minute")
    if not _is_int(until):
        return None
    return max(0, until - now_minute)


def _tod_windows(start_tod, minutes):
    """把 `[start, start+minutes)` 按当日内切段（§4.2：slot 不跨 0 点）。"""
    windows = []
    remaining = minutes
    cursor = start_tod % MINUTES_PER_DAY
    while remaining > 0:
        chunk = min(remaining, MINUTES_PER_DAY - cursor)
        windows.append((cursor, cursor + chunk))
        remaining -= chunk
        cursor = 0
    return windows


def _overlaps_window(start, end, slot_start, slot_end):
    return start < slot_end and slot_start < end


def _serves_obligation(option, obligation):
    """选项是否「指向」这条义务（`check_time` ③ 的唯一放行口）。"""
    target = _text(option.get("target_ref"))
    if target and target in (_text(obligation.get("to")),
                             _text(obligation.get("obligation_id"))):
        return True
    return _is_duty(option) and not target


def _is_duty(option):
    """`fulfill_duty` 类：§5.2 用它作为「到期义务」的唯一白名单动词。"""
    return option.get("verb") == "fulfill_duty"


def _laws(view):
    laws = view.get("laws")
    if isinstance(laws, list):
        return [law for law in laws if isinstance(law, dict)]
    return []


def _law_source(view):
    """§12.1 P8：缺 `laws` → `"none"`；有表时按声明，未声明按 `"world"`。"""
    laws = view.get("laws")
    declared = view.get("law_source")
    if not isinstance(laws, list) or not laws:
        if isinstance(laws, list) and declared in LAW_DECLARED_SOURCES:
            return declared
        return "none"
    return declared if declared in LAW_DECLARED_SOURCES else "world"


def _law_hit_tags(law, tags):
    forbid = set(_string_list(law.get("forbid_tags")))
    return forbid & tags


def _law_covers(law, place):
    """缺 / 空 `scope_place_ids` = 全域（一条不留范围的禁令不该变成空文）。"""
    scope = _string_list(law.get("scope_place_ids"))
    return not scope or place in scope


def _law_tolerated(law):
    threshold = law.get("threshold")
    return isinstance(threshold, str) and threshold.strip().lower() in LAW_TOLERANT_THRESHOLDS


def _law_risk_ids(option, view):
    """被 `threshold` 容忍的禁令：改为「高风险但可行」，返回排序后的 law id 表。"""
    place = _text(option.get("place_id"))
    tags = set(_string_list(option.get("tags")))
    hits = []
    for law in _laws(view):
        if not _law_hit_tags(law, tags) or not _law_covers(law, place):
            continue
        if _law_tolerated(law):
            law_id = _text(law.get("id"))
            if law_id:
                hits.append(law_id)
    return sorted(set(hits))


def _active_claim_ids(npc):
    active = set()
    knowledge = npc.get("knowledge")
    if isinstance(knowledge, list):
        for claim in knowledge:
            if not isinstance(claim, dict) or claim.get("status") != "active":
                continue
            claim_id = claim.get("claim_id")
            if isinstance(claim_id, str) and claim_id:
                active.add(claim_id)
    return active


def _knows_place(view, place_id):
    known = view.get("known_places")
    if isinstance(known, dict):
        return bool(known.get(place_id))
    if isinstance(known, (list, tuple, set)):
        return place_id in known
    return False


def _ai_allowed(view, now_minute):
    """§8.2：当日 `strong_calls` / `light_calls` 是否还有额度。

    返回 `(allowed, note)`；`note` 为 `E_LAYER_BUDGET` 的 journal 说明。
    无账本（`view` 缺 `budget`）→ 不在这里限流，交给编排层判断。
    """
    budget = view.get("budget")
    if not isinstance(budget, dict):
        return True, None

    day = now_minute // MINUTES_PER_DAY if _is_int(now_minute) else None
    recorded = budget.get("day")
    if _is_int(recorded) and day is not None and recorded != day:
        return True, None          # 跨日重置：计数器归零（§8.2）

    strong = _count(budget.get("strong_calls"))
    light = _count(budget.get("light_calls"))
    if strong >= STRONG_CALLS_PER_DAY:
        return False, {
            "code": E_LAYER_BUDGET,
            "detail": "当日 strong_calls 已用尽（%d/%d）：只禁 AI 候选，规则候选照常"
                      % (strong, STRONG_CALLS_PER_DAY),
        }
    if light >= LIGHT_CALLS_PER_DAY:
        return False, {
            "code": E_LAYER_BUDGET,
            "detail": "当日 light_calls 已用尽（%d/%d）：只禁 AI 候选，规则候选照常"
                      % (light, LIGHT_CALLS_PER_DAY),
        }
    return True, None


def _option_error(option):
    """§5.2 `Option` 形状校验：返回错误说明（`str`）或 `None`。

    只校验**形状**，不校验可行性（可行性归五个 `check_*`）；`relieves` /
    `aggravates` / `risk` 的取值校验归 N3（评分段），此处不越界。
    """
    if not isinstance(option, dict):
        return "候选不是对象（%s）" % type(option).__name__

    option_id = option.get("option_id")
    if not isinstance(option_id, str) or not option_id.strip():
        return "option_id 缺失或非法（%r）" % (option_id,)

    verb = option.get("verb")
    if not isinstance(verb, str) or not verb.strip():
        return "verb 缺失或非法（%r）" % (verb,)

    place_id = option.get("place_id", "")
    if not isinstance(place_id, str):
        return "place_id 非法（%r）" % (place_id,)

    minutes = option.get("minutes")
    if not _is_int(minutes) or not 0 <= minutes <= MAX_OPTION_MINUTES:
        return "minutes 非法（%r），须为 0..%d 的整数" % (minutes, MAX_OPTION_MINUTES)

    for field in ("tags", "requires_claims", "requires_place"):
        value = option.get(field)
        if value is None:
            continue
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            return "%s 须为字符串表（%r）" % (field, value)

    source = option.get("source", "rule")
    if source not in OPTION_SOURCES:
        return "source 非法（%r），须为 %s" % (source, " / ".join(OPTION_SOURCES))

    cost = option.get("cost")
    if cost is not None:
        if not isinstance(cost, dict):
            return "cost 非法（%s）" % type(cost).__name__
        coin = cost.get("coin", 0)
        if not _is_int(coin) or coin < 0:
            return "cost.coin 非法（%r）" % (coin,)
        stock = cost.get("stock", {})
        if not isinstance(stock, dict):
            return "cost.stock 非法（%s）" % type(stock).__name__
        for item in sorted(stock, key=str):
            quantity = stock[item]
            if not isinstance(item, str) or not _is_int(quantity) or quantity < 0:
                return "cost.stock[%r] 非法（%r）" % (item, quantity)
    return None
