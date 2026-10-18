#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""neura —— NEURA 内核（M11-N1 骨架 + N2 硬约束 + N3 评分选计划 + N4 知识边界
+ N5 分层模拟；纯函数）。

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
  资源 / 法律 / 知识五道闸，拒单带固定错误码（N2）；
- `score_options()` / `choose()` / `decide()` / `to_proposal()` /
  `idempotency_key()`：**需求评分 → 计划选择 → 决策记录 → M8 提案**（N3，§5.3 /
  §5.4 / §5.6），评分逐需求留 `terms` 供手工复算；
- `select_context()` / `build_candidate_body()` / `learn()` / `supersede()` /
  `resolve_claim()`：**知识边界**（N4，§6）——五类 claim 分离、**唯一**上下文收口、
  修正链（不删旧 id），以及全文件**唯一**拼 `messages` 的入口；
- `layer_of()` / `derive_importance()` / `due_npcs()` / `can_call_ai()` /
  `charge()` / `degrade_tier()` / `catch_up()` / `tick()`：**分层模拟 +
  每日预算 + 降级阶梯 + 补算**（N5，§7 / §8 / §12.1 P4 / P5 / P7 / P10）——
  四层每 tick 现算、日预算 60,000 tokens 一处可调、L0–L3 单调降级、
  补算上限 8 步超限记 `catchup_deferred`；`tick` 签名照抄 §5.8，
  返回新块、不返回事件、不碰 `state`；`port` 是注入的回调（依赖注入）。

> N4 的 `purpose` 取 `decide` / `narrate` / `react`（Issue #357 正文的口径）；
> §6.2 的示例写的是 `decide` / `dialogue` / `memory_summary`，两处命名不同、
> 语义同源（都要「这个 NPC 知道什么」）。实现以实现单为准，差异记在 PR 说明里。

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


# --------------------------------------------------------------------------
# §5.3 / §5.4 / §5.6 需求评分 → 计划选择 → 决策记录（M11-N3）
#
# 一次 tick 的 ①→②→③ 在这里合起来：hard_filter（N2）→ score_options（§5.3）
# → choose（§5.4）→ decide（决策记录）；§5.6 的窄口结构由 to_proposal 与
# idempotency_key 落地。全部纯函数：只读 npc / view，返回新对象，不改入参；
# 无 IO、无随机源（jitter 只来自 stable_unit）、不 import 业务模块。
# --------------------------------------------------------------------------

# §5.4：计划切换理由的六个取值（机器可读，供「行为连续可解释」审计）。
SWITCH_REASONS = ("goal_done", "goal_failed", "hard_blocked",
                  "newly_known", "deadline_pressure", "need_spike")

# §5.3 的四个系数与两个映射（逐字照抄，**不自创权重**）。
JITTER_SPAN = 0.03                 # jitter = span × (2u − 1)
RISK_AVERSION_MIN = 0.2            # risk_aversion = min + span × w_safety
RISK_AVERSION_SPAN = 0.6
TIME_PENALTY_PER_HOUR = 0.02       # 每小时扣 0.02
HABIT_BONUS = 0.05                 # option.place_id == home_place

# §8.2：成本档与写死的 token 估算（N3 只记录，真实记账归 N5）。
COST_TIERS = ("rule", "H", "M")
COST_TOKENS = {"rule": 0, "H": 4000, "M": 1200}

# §7.1 / §4.2：层名（N5 现算后由编排层注入 view["layer"]，N3 只记录）。
LAYERS = ("near", "important_far", "background", "unexplored")
DEFAULT_LAYER = "background"

# §4.2：可沿用的当前计划状态（同目标同步骤 → 幂等键恒定）。
PLAN_ACTIVE = ("pending", "running")

# ACTION-DESIGN.md §4.2：提案版本与五级风险的数值分档。
PROPOSAL_SCHEMA_VERSION = 1
RISK_BANDS = (0.2, 0.4, 0.6, 0.8)   # [0,.2)→R0 … [.8,1]→R4（§5.2 的 Option 只给 0..1）


def risk_aversion(npc):
    """§5.3：`0.2 + 0.6 × need_weights.safety`（由既有字段线性映射，不新增字段）。"""
    return RISK_AVERSION_MIN + RISK_AVERSION_SPAN * _need_weight(npc, "safety")


def score_options(npc, options, *, seed, now_minute):
    """§5.3 需求评分：每个候选返回一条可解释记录（**不选**，只打分）。

    公式（逐字照 §5.3，权重全部来自 `need_weights` / 常数）：

    ```
    sat(option, n) = relieves[n] − aggravates[n]          # 截断到 [-1, 1]
    raw            = Σ_n need_weights[n] × level(npc, n) × sat(option, n)
    risk_penalty   = (0.2 + 0.6 × w_safety) × option.risk
    time_penalty   = 0.02 × ceil(option.minutes / 60)
    habit_bonus    = 0.05 if option.place_id == home_place else 0
    jitter         = 0.03 × (2 × stable_unit(seed, npc_id, now_minute, option_id) − 1)
    score          = raw − risk_penalty − time_penalty + habit_bonus + jitter
    ```

    记录字段：`terms`（六需求逐个 `weight / level / sat / product`，顺序固定为
    `NEED_KEYS`）、`raw`、`risk_penalty`、`time_penalty`、`penalty`（前两者之和）、
    `habit_bonus`、`jitter`、`score`。审查者按 `terms` 累加即得 `raw`，
    再按上式即得 `score`（`Σ product == raw`，浮点误差 ≤ 1e-9）。

    `jitter` **不是骰点**：它只为打破并列，**不进** `roll_refs`、**不进** PRISM
    账本、**不落**独立字段——可由 `(seed, npc_id, now_minute, option_id)` 随时重算。
    `home_place` 取首个带 `place_id` 的 `schedule` slot（无日程则无加成）。
    """
    npc = npc if isinstance(npc, dict) else {}
    npc_id = _npc_id(npc)
    seed = _seed_of(seed)
    minute = now_minute if _is_int(now_minute) else 0
    home = _home_place(npc)
    aversion = risk_aversion(npc)

    scores = []
    for raw_option in options if isinstance(options, list) else []:
        if not isinstance(raw_option, dict):
            continue
        option = _clone(raw_option)
        option_id = _text(option.get("option_id"))
        minutes = option.get("minutes")
        minutes = minutes if _is_int(minutes) and minutes >= 0 else 0

        terms = []
        raw_total = 0.0
        for key in NEED_KEYS:
            weight = _need_weight(npc, key)
            level = _need_level(npc, key)
            sat = _saturation(option, key)
            product = weight * level * sat
            raw_total += product
            terms.append({"need": key, "weight": weight, "level": level,
                          "sat": sat, "product": product})

        risk = _risk_of_option(option)
        risk_penalty = aversion * risk
        time_penalty = TIME_PENALTY_PER_HOUR * _hours_ceil(minutes)
        habit_bonus = HABIT_BONUS if home and _text(option.get("place_id")) == home else 0.0
        jitter = JITTER_SPAN * (2.0 * stable_unit(
            seed, npc_id, minute, option_id) - 1.0)
        penalty = risk_penalty + time_penalty

        scores.append({
            "option_id": option_id,
            "verb": _text(option.get("verb")),
            "target_ref": _text(option.get("target_ref")) or None,
            "target_kind": _text(option.get("target_kind")) or None,
            "goal_id": _goal_of_option(npc, option),
            "place_id": _text(option.get("place_id")),
            "minutes": minutes,
            "risk": risk,
            "source": option.get("source") if option.get("source") in OPTION_SOURCES
                      else "rule",
            "requires_claims": sorted(set(_string_list(option.get("requires_claims")))),
            "terms": terms,
            "raw": raw_total,
            "risk_penalty": risk_penalty,
            "time_penalty": time_penalty,
            "penalty": penalty,
            "habit_bonus": habit_bonus,
            "jitter": jitter,
            "score": raw_total - penalty + habit_bonus + jitter,
        })
    return scores


def choose(scores):
    """§5.4 计划选择：最高分；并列按 `(score, −risk, option_id)` 稳定取一。

    用 `min` 配 `(-score, risk, option_id)`——与「字典序取一」等价，且**与输入
    顺序无关**（候选集相同则结果相同）。空表 / 全是非法记录 → `None`。
    返回深拷贝，调用方改它不会污染 `scores`。
    """
    if not isinstance(scores, list):
        return None
    candidates = [item for item in scores
                  if isinstance(item, dict) and _is_number(item.get("score"))]
    if not candidates:
        return None
    best = min(candidates, key=lambda item: (
        -item["score"], _risk_of_score(item), _text(item.get("option_id"))))
    return _clone(best)


def decide(npc, view):
    """§5.4 一次决策的完整记录（**写 journal 的对象**，不落事件、不提交）。

    返回 §5.4 的十个字段：

    ```
    {"decision_id", "npc_id", "at_minute", "layer", "hard", "scores",
     "chosen", "switch_reason", "cost": {"tier", "tokens"}, "ai_used"}
    ```

    - 流水线：`hard_filter` → `score_options` → `choose`；**无候选**时
      `chosen is None` + `switch_reason == "hard_blocked"`，**仍返回记录**
      （写 journal 一条），不抛异常；
    - `seed` 取 `view["campaign_seed"]`（§5.8 的 `block["campaign_seed"]` 投影），
      `now_minute` 取 `view["now_minute"]`；两者缺省按 0（无种子也是确定性的）；
    - `layer` 取 `view["layer"]`（N5 现算后注入），缺省 `"background"`；
    - `ai_used` = 参评候选里出现 `source == "ai"`；`cost.tier` 为 `rule` / `H` / `M`
      （§8.2），**只记录**，扣账归 N5；
    - `hard` 只留审计需要的摘要（`rejected` / `law_source` / `ai_allowed` 等），
      过闸候选的完整分数在 `scores` 里，不重复存两份候选。
    """
    npc = npc if isinstance(npc, dict) else {}
    view = view if isinstance(view, dict) else {}
    now_minute = _now_of(view, npc)

    hard = hard_filter(npc, view, now_minute)
    scores = score_options(npc, hard["options"], seed=view.get("campaign_seed", 0),
                           now_minute=now_minute)
    chosen = choose(scores)
    ai_used = any(item["source"] == "ai" for item in scores)
    tier, tokens = _cost_of(view, ai_used)

    return {
        "decision_id": _decision_id(npc, now_minute, chosen),
        "npc_id": _npc_id(npc),
        "at_minute": now_minute,
        "layer": _layer_of_view(view),
        "hard": {
            "ok": hard["ok"],
            "kept": len(hard["options"]),
            "rejected": _clone(hard["rejected"]),
            "law_source": hard["law_source"],
            "ai_allowed": hard["ai_allowed"],
            "notes": _clone(hard["notes"]),
        },
        "scores": scores,
        "chosen": chosen,
        "switch_reason": _switch_reason(npc, chosen, now_minute),
        "cost": {"tier": tier, "tokens": tokens},
        "ai_used": ai_used,
    }


def to_proposal(decision, npc):
    """§5.6 窄口结构：把选中的计划转成 ACTION-DESIGN §4.2 的提案（**纯结构**）。

    字段与 §4.2 **逐项对齐**（14 个，封闭集合）：`schema_version` /
    `proposal_id` / `actor` / `intent` / `params` / `source` / `risk` /
    `time_cost` / `schedule_hint` / `interruptible` / `interrupt_conditions` /
    `expected_changes` / `raw_text` / `created_at`。

    NEURA 侧只填这些值，且**不产出叙事**（§12.1 P1）：

    - `actor` = `{"kind": "npc", "id": npc_id}`（§5.6；`source` 是 `"action"`，
      不是 `"ai_proposal"`——后者留给「AI 替玩家提的案」）；
    - `params.dc_override = None`（P2：第一版不掷骰）、`items_used = []`；
    - `schedule_hint.can_defer = false`（走 M8 同步结算，不进 CHRON 队列，P7）；
    - `risk` 由 `option.risk`（0..1）按 `RISK_BANDS` 五等分映射到 R0–R4；
    - `created_at` = `at_minute × 60000`（世界分钟换算的毫秒）：**不取墙钟**，
      否则同输入不再同输出，纯函数与「逐字节相等」都失效；
    - `proposal_id` = `p_<sha256(npc_id|plan_id|step_index)[:32]>`，同计划同步骤恒定。

    `chosen is None`（本次没选中任何计划）时**不生成提案**，抛 `NeuraError`——
    这是调用方顺序错误（§5.8 在 `chosen is None` 时 `continue`）。
    """
    decision = decision if isinstance(decision, dict) else {}
    npc = npc if isinstance(npc, dict) else {}
    chosen = decision.get("chosen")
    if not isinstance(chosen, dict):
        raise NeuraError("to_proposal：本次决策没有选中计划（chosen is None），不生成提案")

    at_minute = _now_of(decision, npc)
    npc_id = _npc_id(npc) or _text(decision.get("npc_id"))
    plan_id, step_index = _plan_step(npc, chosen, at_minute)
    minutes = chosen.get("minutes")
    minutes = minutes if _is_int(minutes) and minutes >= 0 else 0

    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "proposal_id": "p_" + _digest("neura.proposal", npc_id, plan_id,
                                      step_index)[:32],
        "actor": {"kind": "npc", "id": npc_id},
        "intent": {
            "verb": _text(chosen.get("verb")),
            "target": _intent_target(chosen, npc),
            "desired": _text(chosen.get("desired")) or _text(chosen.get("verb")),
            "means": _text(chosen.get("means")),
        },
        "params": {"dc_override": None, "items_used": []},
        "source": "action",
        "risk": _risk_level(chosen),
        "time_cost": {"value": minutes, "unit": "minute"},
        "schedule_hint": {"can_defer": False, "depends_on": [], "priority": 0},
        "interruptible": True,
        "interrupt_conditions": [],
        "expected_changes": [],
        "raw_text": "",
        "created_at": at_minute * 60000,
    }


def idempotency_key(decision, npc):
    """§5.6 幂等键：`np_<sha256(npc_id)[:8]>_<sha256(plan_id)[:10]>_<step:02d>`。

    过 `ACTION-DESIGN.md` §9.4 的 `^[A-Za-z0-9_-]{8,64}$`；**同计划同步骤恒定**
    ——同一计划步骤重投命中首次结果，不重复副作用（`STATE-DESIGN.md` §7）。
    `chosen is None` 时没有可提交的计划 → `NeuraError`（同 `to_proposal`）。
    """
    decision = decision if isinstance(decision, dict) else {}
    npc = npc if isinstance(npc, dict) else {}
    chosen = decision.get("chosen")
    if not isinstance(chosen, dict):
        raise NeuraError("idempotency_key：本次决策没有选中计划（chosen is None）")

    at_minute = _now_of(decision, npc)
    npc_id = _npc_id(npc) or _text(decision.get("npc_id"))
    plan_id, step_index = _plan_step(npc, chosen, at_minute)
    return "np_%s_%s_%02d" % (_sha256(npc_id)[:8], _sha256(plan_id)[:10],
                              step_index)


# --------------------------------------------------------------------------
# N3 的内部工具（纯函数；只做确定性推导与形状容错）
# --------------------------------------------------------------------------

def _clamp(value, low, high):
    return low if value < low else (high if value > high else value)


def _hours_ceil(minutes):
    """`ceil(minutes / 60)`，用整数运算（不 import `math`，保持依赖面）。"""
    return (minutes + 59) // 60


def _need_level(npc, key):
    """§5.3 的 `level(npc, n)` = `needs[n] / 100`；缺键按迁移口径补 50。"""
    needs = npc.get("needs")
    value = needs.get(key) if isinstance(needs, dict) else None
    if _is_int(value) and NEED_MIN <= value <= NEED_MAX:
        return value / 100.0
    return NEED_DEFAULT / 100.0


def _need_weight(npc, key):
    """§5.3 的 `need_weights[n]`；非法值按空态的 1.0（与 `empty_npc` 同口径）。"""
    weights = npc.get("need_weights")
    value = weights.get(key) if isinstance(weights, dict) else None
    if _is_number(value) and 0.0 <= value <= 1.0:
        return float(value)
    return 1.0


def _saturation(option, key):
    """`sat(option, n) = relieves[n] − aggravates[n]`，截断到 `[-1, 1]`。"""
    def amount(field):
        table = option.get(field)
        value = table.get(key) if isinstance(table, dict) else None
        return float(value) if _is_number(value) else 0.0

    return _clamp(amount("relieves") - amount("aggravates"), -1.0, 1.0)


def _risk_of_option(option):
    """`option.risk` 规范到 `[0, 1]`；非数值按 0（形状问题归 `_option_error`）。"""
    value = option.get("risk")
    if not _is_number(value):
        return 0.0
    return _clamp(float(value), 0.0, 1.0)


def _risk_of_score(item):
    value = item.get("risk")
    return float(value) if _is_number(value) else 0.0


def _risk_level(chosen):
    """§6.1 的 N3 落地口径：`option.risk`（0..1）五等分到 `R0`–`R4`。

    §5.2 的 `Option` 只给 `risk: float 0..1`；五级枚举见 `ACTION-DESIGN.md` §6.1。
    分档是**线性映射**，不新增权重；`risk_level` 若要显式覆盖，由 R 层再扩
    `Option` schema（本单不改契约）。
    """
    value = chosen.get("risk")
    risk = float(value) if _is_number(value) else 0.0
    for index, upper in enumerate(RISK_BANDS):
        if risk < upper:
            return "R%d" % index
    return "R%d" % len(RISK_BANDS)


def _home_place(npc):
    """§5.3：`home_place` = 首个带 `place_id` 的 `schedule` slot；无日程则无加成。"""
    schedule = npc.get("schedule")
    if isinstance(schedule, list):
        for slot in schedule:
            if isinstance(slot, dict):
                place = _text(slot.get("place_id"))
                if place:
                    return place
    return ""


def _npc_id(npc):
    """§4.2：`npc_id` 存在 `display_ref.npc_id`（块内键由编排层持有）。"""
    ref = npc.get("display_ref")
    if isinstance(ref, dict):
        return _text(ref.get("npc_id"))
    return ""


def _seed_of(seed):
    """`stable_unit` 只接受标量；其它形状按 0（不猜种子）。"""
    if isinstance(seed, bool) or not isinstance(seed, (int, float, str)):
        return 0
    return seed


def _now_of(source, npc):
    """`now_minute` 依次取 `view["now_minute"]` → `decision["at_minute"]` → `last_tick_minute`。"""
    for key in ("now_minute", "at_minute"):
        value = source.get(key) if isinstance(source, dict) else None
        if _is_int(value):
            return value
    value = npc.get("last_tick_minute")
    return value if _is_int(value) else 0


def _layer_of_view(view):
    layer = view.get("layer")
    return layer if layer in LAYERS else DEFAULT_LAYER


def _goal_of_option(npc, option):
    """候选归属的目标：显式 `goal_id` 优先，否则按 `target_ref` 匹配 active goal。

    §5.2 的 `Option` 没有 `goal_id`；§5.4 的切换判据需要「新目标 ≠ 旧目标」，
    因此这里只做**查找**（不补字段、不猜）：AI / 编排层给 `goal_id` 就用它，
    否则只有 `target_ref` 恰好等于某条 active `goal.target_ref` 时才认领。
    """
    explicit = _text(option.get("goal_id"))
    if explicit:
        return explicit
    target = _text(option.get("target_ref"))
    if not target:
        return ""
    goals = npc.get("goals")
    if isinstance(goals, list):
        for goal in goals:
            if not isinstance(goal, dict) or goal.get("status") != "active":
                continue
            if _text(goal.get("target_ref")) == target:
                return _text(goal.get("goal_id"))
    return ""


def _goal_of(npc, goal_id):
    goals = npc.get("goals")
    if isinstance(goals, list):
        for goal in goals:
            if isinstance(goal, dict) and _text(goal.get("goal_id")) == goal_id:
                return goal
    return {}


def _switch_reason(npc, chosen, now_minute):
    """§5.4 切换理由：**同目标续计划 / 首次决策 → `None`**，换目标必带六值之一。

    优先级（每条都是对既有字段的**事实判断**，不新增阈值）：

    1. `chosen is None` → `hard_blocked`（无候选，§5.4 明文）；
    2. 没有旧计划 / 旧目标 / 新目标无法判定 → `None`（不是切换，不编造理由）；
    3. 旧目标或旧计划 `done` → `goal_done`；
    4. 旧计划 `dropped`（被硬约束或管线丢弃）→ `hard_blocked`；
    5. 旧目标 `failed` / `abandoned` → `goal_failed`；
    6. 旧目标 `deadline_minute` 已到 → `deadline_pressure`；
    7. 选中候选依赖一条 `learned_minute ≥ 旧计划 started_minute` 的 active claim
       → `newly_known`（用 `Claim.learned_minute`，不加字段）；
    8. 其余 → `need_spike`：前七条证据都不在时，切换只能由需求推高解释
       （评分里 `raw` 就是需求的线性组合，这是**兜底口径**，不是新增阈值）。
    """
    if chosen is None:
        return "hard_blocked"

    plan = npc.get("plan")
    old_goal_id = _text(plan.get("goal_id")) if isinstance(plan, dict) else ""
    new_goal_id = _text(chosen.get("goal_id"))
    if not old_goal_id or not new_goal_id or new_goal_id == old_goal_id:
        return None

    goal = _goal_of(npc, old_goal_id)
    goal_status = _text(goal.get("status"))
    plan_status = _text(plan.get("status"))

    if goal_status == "done" or plan_status == "done":
        return "goal_done"
    if plan_status == "dropped":
        return "hard_blocked"
    if goal_status in ("failed", "abandoned"):
        return "goal_failed"

    deadline = goal.get("deadline_minute")
    if _is_int(deadline) and _is_int(now_minute) and now_minute >= deadline:
        return "deadline_pressure"

    if _newly_known(npc, plan, chosen):
        return "newly_known"
    return "need_spike"


def _newly_known(npc, plan, chosen):
    """选中候选是否依赖「旧计划开始后才学到」的 active claim（§6 的修正链口径）。"""
    required = set(_string_list(chosen.get("requires_claims")))
    if not required:
        return False
    started = plan.get("started_minute")
    started = started if _is_int(started) else 0
    knowledge = npc.get("knowledge")
    if not isinstance(knowledge, list):
        return False
    for claim in knowledge:
        if not isinstance(claim, dict) or claim.get("status") != "active":
            continue
        if _text(claim.get("claim_id")) not in required:
            continue
        learned = claim.get("learned_minute")
        if _is_int(learned) and learned >= started:
            return True
    return False


def _cost_of(view, ai_used):
    """§8.2 的 `cost`：没用 AI 候选即 `rule`；用了按注入档位，缺省 M。"""
    if not ai_used:
        return "rule", 0
    declared = view.get("cost_tier")
    tier = declared if declared in COST_TIERS and declared != "rule" else "M"
    tokens = view.get("ai_tokens")
    if not _is_int(tokens) or tokens < 0:
        tokens = COST_TOKENS[tier]
    return tier, tokens


def _intent_target(chosen, npc):
    """§4.2 的 `intent.target`：有 `target_ref` 才给对象；kind 用显式或关系表推断。"""
    target_ref = _text(chosen.get("target_ref"))
    if not target_ref:
        return None
    kind = _text(chosen.get("target_kind"))
    if not kind:
        relations = npc.get("relations")
        kind = "npc" if isinstance(relations, dict) and target_ref in relations \
            else "entity"
    return {"kind": kind, "id": target_ref}


def _plan_step(npc, chosen, at_minute):
    """本次提案属于哪个计划步骤 → `(plan_id, step_index)`。

    同目标且旧计划仍在 `pending` / `running` → **沿用**旧 `plan_id` 与
    `step_index`（幂等键因此恒定）；否则是**新计划**，`plan_id` 由
    `(npc_id, goal_id, at_minute)` 确定性派生、步骤从 0 起。
    """
    goal_id = _text(chosen.get("goal_id"))
    plan = npc.get("plan")
    if isinstance(plan, dict):
        plan_id = _text(plan.get("plan_id"))
        if plan_id and _text(plan.get("status")) in PLAN_ACTIVE \
                and _text(plan.get("goal_id")) == goal_id:
            step = plan.get("step_index")
            return plan_id, step if _is_int(step) and step >= 0 else 0
    return "pl_" + _digest("neura.plan", _npc_id(npc), goal_id, at_minute)[:12], 0


def _decision_id(npc, now_minute, chosen):
    """决策 ID：同输入同 ID（可重算、可去重）；无候选时也有一条可审计的 ID。"""
    option_id = _text(chosen.get("option_id")) if isinstance(chosen, dict) else ""
    return "dec_" + _digest("neura.decision", _npc_id(npc), now_minute,
                            option_id)[:16]


def _digest(*parts):
    """带标签的确定性摘要（`repr(tuple)` 编码无歧义，不用内置 `hash()`）。"""
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def _sha256(text):
    """§5.6 的幂等键就是 `sha256(<原始串>)` 的前缀，单独一个口子便于对照。"""
    return hashlib.sha256(_text(text).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------
# §6 / §11 N4 知识边界：五类 claim + 唯一收口 + 修正链（M11-N4）
#
# 分工（§6.2 / §12.1 P1）：
#   select_context()      **唯一**上下文收口——AI 只看这里，别处不再挑字段；
#   build_candidate_body  全文件**唯一**拼 `messages` 的地方（role 键只此一处）；
#   learn()              写入 / 合并一条认知（同 id 同命题才合并，别的都新建）；
#   supersede()          修正链：旧 claim 置 disproved + superseded_by，**不删**；
#   resolve_claim()      旧 id 仍可解析（§6.4，「他曾经这么以为」要答得出来）。
#
# NEURA **不产出**玩家可见文本（P1）：这里只把「这个人知道什么」投影成结构。
# 五类分离的落地只有一句话：`world_facts` 投影只收 `cls == "fact"` 且
# `visibility == "public"` 的项；`belief` / `rumor` / `inference` / `secret` 一律不收。
# --------------------------------------------------------------------------

# §6.1 的五类（顺序固定，测试与文档逐字对应）。
CLAIM_CLASSES = ("fact", "belief", "rumor", "inference", "secret")

# §6.1 硬规矩 1：这四类**永不**进 `world_facts` 投影（只有 fact 是）。
CLAIM_CLASSES_NEVER_WORLD_FACT = ("belief", "rumor", "inference", "secret")

# §6.1 / §6.4：claim 只有「有效」与「被推翻」两态；disproved 不删除、id 不复用。
CLAIM_STATUSES = ("active", "disproved")

# Issue #357：purpose 三值（见模块 docstring 的命名差异说明）。
PURPOSES = ("decide", "narrate", "react")

# 三值各自的**固定**字段集（不多不少；测试用集合相等断言）。
# `plan` 缺计划时为 `None`——块里「plan 键不存在」是落盘口径，上下文里必须有键。
CONTEXT_FIELDS = {
    "decide": ("purpose", "npc_id", "now_minute", "claims", "world_facts",
               "secret_flags", "goals", "plan"),
    "narrate": ("purpose", "npc_id", "now_minute", "claims", "world_facts",
                "secret_flags", "location", "memory"),
    "react": ("purpose", "npc_id", "now_minute", "claims", "world_facts",
              "secret_flags", "needs", "relations", "memory"),
}

# §4.3：记忆摘要在上下文里只带最近若干条（全量在 `npc["memory"]`，上限另由裁剪管）。
CONTEXT_MEMORY_LIMIT = 8

# `messages` 的两条角色（顺序固定：先指令、后上下文）。
CONTEXT_MESSAGE_ROLES = ("system", "user")

CONFIDENCE_MIN, CONFIDENCE_MAX = 0, 100
FACT_CONFIDENCE = 100        # §6.1 硬规矩 3：fact 的 confidence 恒为 100
MEMORY_SUMMARY_MAX = 80      # §4.3：summary 上限 80 字（按码位）
SALIENCE_CORRECTION = 60     # §4.3：salience 由规则算；修正是一等事件，给高档

# §12.1 P1：三个 purpose 的系统指令都**禁止**产出玩家可见文本。
_SYSTEM_PROMPTS = {
    "decide": (
        "你是 NPC 计划评估的上下文装配口：只依据给出的上下文判断该角色"
        "（npc_id）知道什么、想做什么，输出结构化判断。"
        "上下文之外一律视为不知道：不补写事实，不生成玩家可见叙事。"
    ),
    "narrate": (
        "你是叙事层的资料供给口：只按给出的上下文列出该角色（npc_id）"
        "确实知道、可以提及的事实与态度。"
        "secret_flags 只表示「存在 / 可否说」，不含秘密内容。"
        "不写句子，不生成玩家可见文本。"
    ),
    "react": (
        "你是 NPC 反应评估器：只依据给出的上下文给出该角色（npc_id）的"
        "情绪与立场判断。不补写事实，不生成玩家可见叙事。"
    ),
}


def learn(npc, claim):
    """§6.4 写入 / 合并一条认知，返回 `{"npc", "claim", "merged"}`（**新**对象）。

    - `claim` 走 `_normalize_claim`：`proposition` / `cls` / `confidence` 不合法即
      `NeuraError`（写入口严格——脏认知不该被静默降级成另一类）；
    - `claim_id` 缺省时由 `(cls, proposition, about_ref, source_kind)` 确定性派生：
      **同内容同 id**，因此重复学到同一件事会合并而不是堆两条；
    - 合并口径：`confidence` 取高、`learned_minute` 取早、`evidence_refs` 取并集；
      命题或类别不一致却共用 id → `NeuraError`（id 不复用，§6.4）；
    - 同 id 的旧记录已是 `disproved` → `NeuraError`：修正过的认知不复活，
      新的认知请用新 id（`supersede` 也是这个口径）。
    """
    npc = npc if isinstance(npc, dict) else {}
    normalized = _normalize_claim(claim, npc)

    written = _clone(npc)
    knowledge = written.get("knowledge")
    if not isinstance(knowledge, list):
        knowledge = []
        written["knowledge"] = knowledge

    index = _claim_index(knowledge, normalized["claim_id"])
    merged = False
    if index is None:
        knowledge.append(normalized)
        stored = normalized
    elif _text(knowledge[index].get("status")) != "active":
        raise NeuraError(
            "learn：claim_id=%r 已被推翻（status=%r），id 不复用（§6.4）"
            % (normalized["claim_id"], knowledge[index].get("status")))
    else:
        stored = _merge_claim(knowledge[index], normalized)
        knowledge[index] = stored
        merged = True

    knowledge.sort(key=lambda item: (_count(item.get("learned_minute")),
                                     _text(item.get("claim_id"))))
    return {"npc": written, "claim": _clone(stored), "merged": merged}


def supersede(npc, old_claim_id, new_claim, *, reason):
    """§6.4 修正链：旧 claim → `disproved` + `superseded_by`，并写一条 correction 记忆。

    返回 `{"npc", "old_claim", "new_claim", "memory"}`（**新**对象）：

    - 旧 claim **不删**、`claim_id` 不改——`resolve_claim()` 仍能取到它，
      历史 `journal` 里的引用重放时不会断；
    - `reason` 落到 correction 记忆的 `summary`（≤ 80 码位）；claim 上只加
      `superseded_by` 一个字段，不把理由塞进命题（§6.1：不原地改文本）；
    - 旧 id 不存在 / 已是被推翻态 / 新旧同 id → `NeuraError`（都是调用方错误）。
    """
    if not isinstance(old_claim_id, str) or not old_claim_id.strip():
        raise NeuraError("supersede：old_claim_id 非法（%r）" % (old_claim_id,))
    npc = npc if isinstance(npc, dict) else {}
    normalized = _normalize_claim(new_claim, npc)

    written = _clone(npc)
    knowledge = written.get("knowledge")
    if not isinstance(knowledge, list):
        knowledge = []
        written["knowledge"] = knowledge

    index = _claim_index(knowledge, old_claim_id)
    if index is None:
        raise NeuraError("supersede：找不到 claim_id=%r（旧 id 应可解析，§6.4）"
                         % (old_claim_id,))
    if _text(knowledge[index].get("status")) != "active":
        raise NeuraError("supersede：claim_id=%r 已是 %r，不能再次修正"
                         % (old_claim_id, knowledge[index].get("status")))
    if normalized["claim_id"] == old_claim_id:
        raise NeuraError("supersede：新旧 claim_id 相同（%r），id 不复用" % (old_claim_id,))
    if _claim_index(knowledge, normalized["claim_id"]) is not None:
        raise NeuraError("supersede：新 claim_id=%r 已存在，修正应产生新认知"
                         % (normalized["claim_id"],))

    old_claim = knowledge[index]
    old_claim["status"] = "disproved"
    old_claim["superseded_by"] = normalized["claim_id"]
    knowledge.append(normalized)
    knowledge.sort(key=lambda item: (_count(item.get("learned_minute")),
                                     _text(item.get("claim_id"))))

    memory = _correction_memory(written, old_claim_id, normalized, reason)
    entries = written.get("memory")
    if not isinstance(entries, list):
        entries = []
        written["memory"] = entries
    entries.append(memory)

    return {
        "npc": written,
        "old_claim": _clone(old_claim),
        "new_claim": _clone(normalized),
        "memory": _clone(memory),
    }


def resolve_claim(npc, claim_id):
    """§6.4：按 id 解析一条认知（**含已推翻的**）；查不到返回 `None`。

    这是「旧 id 仍可解析」的公开口子——修正链只改 `status` / `superseded_by`，
    从不删记录，所以历史决策记录里的 `claim_id` 永远重放得出来。
    """
    npc = npc if isinstance(npc, dict) else {}
    knowledge = npc.get("knowledge")
    if not isinstance(knowledge, list):
        return None
    index = _claim_index(knowledge, claim_id)
    return None if index is None else _clone(knowledge[index])


def select_context(npc, view, *, purpose):
    """§6.2 **唯一**上下文收口：AI 看到的最后一份结构，由这里决定。

    `purpose ∈ PURPOSES`，三值的字段集由 `CONTEXT_FIELDS` **固定**（不多不少）。
    保证（逐条对应 Issue #357 的硬约束）：

    1. `claims` 只含该 NPC 自己 `status == "active"` 的认知；带 `holder_id` 且不等于
       本人 id 的记录视为脏数据，**不投影**（不把别人的认知当成自己的）；
    2. `world_facts` 只收 `cls == "fact"` 且 `visibility == "public"` 的项——
       `belief` / `rumor` / `inference` / `secret` **永不**进入（§6.1 硬规矩 1）；
    3. `secret_flags` 只有 `secret_id` / `known` / `may_reveal` 三个键：
       秘密只以「存在 / 可否说」的形状出现，**不带** `proposition`、`reveal_conditions`；
    4. `view` 里没有的东西就是不知道——不读盘、不联网、不推断。
    """
    if purpose not in PURPOSES:
        raise NeuraError("select_context：purpose 非法（%r），须为 %s"
                         % (purpose, " / ".join(PURPOSES)))

    npc = npc if isinstance(npc, dict) else {}
    view = view if isinstance(view, dict) else {}
    npc_id = _npc_id(npc)

    parts = {
        "purpose": purpose,
        "npc_id": npc_id,
        "now_minute": _now_of(view, npc),
        "claims": _context_claims(npc, npc_id),
        "world_facts": _context_world_facts(view),
        "secret_flags": _context_secret_flags(npc, npc_id),
        "goals": _context_goals(npc),
        "plan": _context_plan(npc),
        "location": _context_location(npc),
        "needs": _context_needs(npc),
        "relations": _context_relations(npc),
        "memory": _context_memory(npc),
    }
    # 按 `CONTEXT_FIELDS` 取键：字段集与顺序都由那张表决定，调用方无法多加。
    return {key: parts[key] for key in CONTEXT_FIELDS[purpose]}


def build_candidate_body(context):
    """§6.2 全文件**唯一**拼 `messages` 的地方（role 键只在此处出现一次）。

    返回 `{"purpose", "npc_id", "messages"}`；`messages` 固定两条：
    `system`（该 purpose 的边界指令，P1：不许产出玩家可见文本）与
    `user`（`select_context()` 的产物，确定性序列化成文本）。

    `purpose` 不合法 → `NeuraError`：这是收口前的一道硬门，不认识的口径不许拼词。
    序列化自己走 `_stable_text`（键序固定）——不 import `json`，保持 §9.1 的依赖面，
    同样的上下文永远拼出同样的字节。
    """
    context = context if isinstance(context, dict) else {}
    purpose = context.get("purpose")
    if purpose not in PURPOSES:
        raise NeuraError("build_candidate_body：purpose 非法（%r），须为 %s"
                         % (purpose, " / ".join(PURPOSES)))

    system = _SYSTEM_PROMPTS[purpose]
    user = "上下文（只读；不在其中的即视为不知道）：\n" + _stable_text(context)
    return {
        "purpose": purpose,
        "npc_id": _text(context.get("npc_id")),
        "messages": [{"role": role, "content": text}
                     for role, text in zip(CONTEXT_MESSAGE_ROLES, (system, user))],
    }


# --------------------------------------------------------------------------
# N4 的内部工具（纯函数：只读入参、返回新对象、不猜缺失）
# --------------------------------------------------------------------------

def _claim_index(knowledge, claim_id):
    """在 `knowledge` 里定位 `claim_id`；返回下标或 `None`（不删、不去重）。"""
    target = _text(claim_id)
    if not target:
        return None
    for index, item in enumerate(knowledge):
        if isinstance(item, dict) and _text(item.get("claim_id")) == target:
            return index
    return None


def _normalize_claim(claim, npc):
    """写入口的 claim 规范化（**严格**：非法即 `NeuraError`）。

    与读路径（`_context_claims`）的容错不同：写错一条认知会被决策与叙事长期引用，
    静默降级比报错更难查。
    """
    if not isinstance(claim, dict):
        raise NeuraError("claim 必须是对象（%s）" % type(claim).__name__)

    proposition = claim.get("proposition")
    if not isinstance(proposition, str) or not proposition.strip():
        raise NeuraError("claim.proposition 缺失或为空")

    cls = claim.get("cls")
    if cls not in CLAIM_CLASSES:
        raise NeuraError("claim.cls 非法（%r），须为 %s"
                         % (cls, " / ".join(CLAIM_CLASSES)))

    status = claim.get("status", "active")
    if status != "active":
        raise NeuraError(
            "learn 只写入 status=\"active\" 的认知（%r）；修正旧认知走 supersede"
            % (status,))

    if cls == "fact":
        confidence = FACT_CONFIDENCE      # §6.1 硬规矩 3：不采信调用方给的 99
    else:
        confidence = claim.get("confidence")
        if not _is_int(confidence) or not CONFIDENCE_MIN <= confidence <= CONFIDENCE_MAX:
            raise NeuraError("claim.confidence 非法（%r），须为 %d..%d 的整数"
                             % (confidence, CONFIDENCE_MIN, CONFIDENCE_MAX))

    learned = claim.get("learned_minute")
    if not _is_int(learned) or learned < 0:
        learned = _count(npc.get("last_tick_minute"))

    claim_id = _text(claim.get("claim_id"))
    if not claim_id:
        claim_id = "cl-" + _digest("neura.claim", cls, proposition,
                                   _text(claim.get("about_ref")),
                                   _text(claim.get("source_kind")))[:8]

    return {
        "claim_id": claim_id,
        "cls": cls,
        "proposition": proposition,
        "about_ref": _text(claim.get("about_ref")) or None,
        "source_kind": _text(claim.get("source_kind")),
        "source_ref": _text(claim.get("source_ref")) or None,
        "confidence": confidence,
        "learned_minute": learned,
        "evidence_refs": sorted(set(_string_list(claim.get("evidence_refs")))),
        "status": "active",
    }


def _merge_claim(old, new):
    """同一 claim 再学一次：置信取高、时间取早、证据取并集（命题必须一致）。"""
    if _text(old.get("proposition")) != new["proposition"] \
            or old.get("cls") != new["cls"]:
        raise NeuraError(
            "同 claim_id=%r 却是不同命题 / 类别：id 不复用（§6.4）"
            % (new["claim_id"],))
    merged = _clone(old)
    merged["confidence"] = max(_claim_confidence(old), new["confidence"])
    merged["learned_minute"] = min(_count(old.get("learned_minute")),
                                   new["learned_minute"])
    merged["evidence_refs"] = sorted(
        set(_string_list(old.get("evidence_refs")))
        | set(new["evidence_refs"]))
    for key in ("about_ref", "source_kind", "source_ref"):
        if not merged.get(key):
            merged[key] = new[key]
    merged["status"] = "active"
    return merged


def _claim_confidence(claim):
    """读路径的置信：缺失 / 非法按 0（宁低不高，不替数据壮胆）。"""
    cls = claim.get("cls")
    if cls == "fact":
        return FACT_CONFIDENCE
    value = claim.get("confidence")
    if _is_int(value) and CONFIDENCE_MIN <= value <= CONFIDENCE_MAX:
        return value
    return 0


def _correction_memory(npc, old_claim_id, new_claim, reason):
    """§6.4 的 correction 记忆：只存摘要与两侧 claim 引用，不存命题原文。"""
    minute = new_claim["learned_minute"]
    npc_id = _npc_id(npc)
    summary = _text(reason)
    memory = {
        "memory_id": "mem-" + _digest("neura.memory", npc_id, minute,
                                      old_claim_id, new_claim["claim_id"])[:8],
        "minute": minute,
        "kind": "correction",
        "summary": summary[:MEMORY_SUMMARY_MAX],
        "participants": [npc_id] if npc_id else [],
        "salience": SALIENCE_CORRECTION,
        "claim_refs": [old_claim_id, new_claim["claim_id"]],
    }
    if len(summary) > MEMORY_SUMMARY_MAX:      # §4.3：截断要显式标出来
        memory["truncated"] = True
    return memory


def _context_claims(npc, npc_id):
    """只投影本人 `active` 的 claim（读路径容错：形状不对就跳过，不猜）。"""
    claims = []
    knowledge = npc.get("knowledge")
    if not isinstance(knowledge, list):
        return claims
    for raw in knowledge:
        if not isinstance(raw, dict) or raw.get("status") != "active":
            continue
        cls = raw.get("cls")
        if cls not in CLAIM_CLASSES:
            continue                      # 五类之外不投影（宁漏不猜）
        holder = raw.get("holder_id")
        if isinstance(holder, str) and holder and holder != npc_id:
            continue                      # 别人的认知不是我的认知
        proposition = _text(raw.get("proposition"))
        if not proposition:
            continue
        claims.append({
            "claim_id": _text(raw.get("claim_id")),
            "cls": cls,
            "proposition": proposition,
            "about_ref": _text(raw.get("about_ref")) or None,
            "source_kind": _text(raw.get("source_kind")),
            "confidence": _claim_confidence(raw),
            "learned_minute": _count(raw.get("learned_minute")),
            "evidence_refs": sorted(set(_string_list(raw.get("evidence_refs")))),
            "status": "active",
        })
    claims.sort(key=lambda item: (item["claim_id"], item["cls"]))
    return claims


def _context_world_facts(view):
    """§6.1 硬规矩 1 的落地：只收 `fact` + `public`，其余四类一律不投影。

    `view.world_facts` 的形状按 §5.2.1（`{id: {"proposition", "visibility"}}`，
    容器本身「仅公开事实」），所以**缺 `cls` 按 `fact`**；但一旦显式标了
    `belief` / `rumor` / `inference` / `secret`，哪怕 `visibility == "public"`
    也不收——宁可少给一条，不把「某人以为」当成「世界是」。输出键 `fact_id`
    就是 §5.2.1 的那个 id（`claim_id`），不复制文本之外的东西。
    """
    raw = view.get("world_facts")
    items = []
    if isinstance(raw, dict):
        pairs = sorted(raw.items(), key=lambda pair: str(pair[0]))
    elif isinstance(raw, list):
        pairs = [(entry.get("fact_id", ""), entry)
                 for entry in raw if isinstance(entry, dict)]
    else:
        return items

    for fact_id, entry in pairs:
        if not isinstance(entry, dict):
            continue
        if _text(entry.get("visibility")) != "public":
            continue
        if _text(entry.get("cls"), "fact") != "fact":
            continue
        proposition = _text(entry.get("proposition")) or _text(entry.get("text"))
        if not proposition:
            continue
        items.append({"fact_id": _text(fact_id), "proposition": proposition})
    items.sort(key=lambda item: item["fact_id"])
    return items


def _context_secret_flags(npc, npc_id):
    """秘密只以「存在 / 可否说」出现：三个键，绝无命题、绝无披露条件。"""
    flags = []
    secrets = npc.get("secrets")
    if not isinstance(secrets, list):
        return flags
    active = _active_claim_ids(npc)
    for raw in secrets:
        if not isinstance(raw, dict):
            continue
        secret_id = _text(raw.get("secret_id"))
        if not secret_id:
            continue
        claim_id = _text(raw.get("claim_id"))
        holders = set(_string_list(raw.get("holders")))
        flags.append({
            "secret_id": secret_id,
            "known": bool(claim_id) and claim_id in active,
            "may_reveal": npc_id in holders
                          and _text(raw.get("status"), "held") == "held",
        })
    flags.sort(key=lambda item: item["secret_id"])
    return flags


def _context_goals(npc):
    """活跃目标（已完成的旧目标不进上下文：AI 只回答「现在想做什么」）。"""
    goals = []
    raw = npc.get("goals")
    if not isinstance(raw, list):
        return goals
    for goal in raw:
        if not isinstance(goal, dict) or goal.get("status") != "active":
            continue
        weight = goal.get("weight")
        deadline = goal.get("deadline_minute")
        goals.append({
            "goal_id": _text(goal.get("goal_id")),
            "kind": _text(goal.get("kind")),
            "target_ref": _text(goal.get("target_ref")) or None,
            "weight": float(weight) if _is_number(weight) else 0.0,
            "deadline_minute": deadline if _is_int(deadline) else None,
            "status": "active",
        })
    goals.sort(key=lambda item: item["goal_id"])
    return goals


def _context_plan(npc):
    """当前计划：没有时给 `None`（键必须存在——字段集固定）。"""
    plan = npc.get("plan")
    return _clone(plan) if isinstance(plan, dict) and plan else None


def _context_location(npc):
    location = npc.get("location")
    if not isinstance(location, dict):
        return {"place_id": "", "since_minute": 0, "band_to_party": "same"}
    return {
        "place_id": _text(location.get("place_id")),
        "since_minute": _count(location.get("since_minute")),
        "band_to_party": _text(location.get("band_to_party"), "same"),
    }


def _context_needs(npc):
    needs = npc.get("needs")
    return {
        key: (needs.get(key) if isinstance(needs, dict)
              and _is_int(needs.get(key)) else NEED_DEFAULT)
        for key in NEED_KEYS
    }


def _context_relations(npc):
    """关系是数值表（§4.2），不含任何命题文本；原样深拷贝即可。"""
    relations = npc.get("relations")
    if not isinstance(relations, dict):
        return {}
    return _clone({key: value for key, value in relations.items()
                   if isinstance(key, str) and isinstance(value, dict)})


def _context_memory(npc):
    """§4.3 记忆摘要：最近 `CONTEXT_MEMORY_LIMIT` 条（按 minute + memory_id 定序）。"""
    raw = npc.get("memory")
    if not isinstance(raw, list):
        return []
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        entries.append({
            "memory_id": _text(item.get("memory_id")) or _text(item.get("id")),
            "minute": _count(item.get("minute")),
            "kind": _text(item.get("kind")),
            "summary": _text(item.get("summary")),
            "salience": _count(item.get("salience")),
            "claim_refs": sorted(set(_string_list(item.get("claim_refs")))),
        })
    entries.sort(key=lambda item: (item["minute"], item["memory_id"]))
    return entries[-CONTEXT_MEMORY_LIMIT:]


def _stable_text(value):
    """确定性文本序列化（键序固定、无 `json` import，保持 §9.1 的依赖面）。

    只用于拼 `build_candidate_body` 的 `user` 内容：同一份上下文永远拼出同一串，
    「同输入同输出」在提示词层也成立；`repr` 的浮点表示稳定，故数值直接用 `repr`。
    """
    if isinstance(value, dict):
        return "{" + ", ".join(
            "%s=%s" % (key, _stable_text(value[key]))
            for key in sorted(value, key=str)) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_stable_text(item) for item in value) + "]"
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return _text(value)


# --------------------------------------------------------------------------
# §7 / §8 / §11 N5：分层模拟 + 每日预算 + 降级阶梯 + 补算
#
# 分工（§7.1 / §7.2 / §7.4 / §8.2 / §8.3 / §8.4 / §12.1 P4 / P5 / P7 / P10）：
#   layer_of()           四层判定（每 tick 现算、不缓存，§7.1）；
#   derive_importance()  P10 的 importance 派生公式（显式覆盖优先）；
#   due_npcs()           层 × 频率 × last_tick_minute 去重（§7.1 / §7.2）；
#   can_call_ai()        预算 + 突发上限 + 降级档位的 AI 准入（§8.2 / §8.3）；
#   charge()             日预算记账（跨日归零由 _ensure_budget_day 做）；
#   degrade_tier()       L0–L3 降级阶梯（当天单调，§8.3）；
#   catch_up()           长推进后的补算收敛（上限 8 步，§7.4）；
#   tick()               一次 tick 的完整管线（§5.8 签名原样，返回新块）。
#
# `port` 是注入的回调对象（依赖注入，测试用记录型假 port）：
#   `submit(proposal, idempotency_key=…)` 必须实现；
#   `generate_candidates(npc, view, budget=…)` 可选——没有它就是「无 AI 配置」
#   的正常路径（§8.4），NPC 仍按日程移动、按需求阈值行动、硬约束照拦。
# --------------------------------------------------------------------------

# §12.1 P4：每游戏日 token 上限（常量、一处可调；上线前按实际单价复核）。
NPC_DAILY_TOKEN_BUDGET = 60000
# §12.1 P5：「重要远场」的 importance 阈值（用 N8 长跑数据回调，不改判定结构）。
IMPORTANCE_THRESHOLD = 0.7
# §12.1 P10：importance 派生公式（显式覆盖优先；三个权重写成常量）。
IMPORTANCE_WEIGHT_THREADS = 0.4    # 在主线 threads
IMPORTANCE_WEIGHT_FACTION = 0.3    # 有 faction
IMPORTANCE_WEIGHT_SECRET = 0.3     # 有 secret

# §7.4：补算步数上限与 10 分钟格点。
MAX_CATCHUP_STEPS = 8
CATCHUP_GRID_MINUTES = 10

# §8.3：降级阶梯（L0 强模型 → L1 轻模型 → L2 规则 → L3 冻结）。
DEGRADE_TIERS = ("L0", "L1", "L2", "L3")
BUDGET_WARN_RATIO = 0.8            # 预算 ≥ 80% 降 L1
# 档位 → 调用种类：L0 只允许 strong（H）、L1 只允许 light（M）、L2/L3 不调模型。
TIER_CALL_KIND = {"L0": "strong", "L1": "light"}
# §8.2：调用种类 × token 常数 / 当日次数上限（P4 反解：h ≤ 6、m ≤ 30）。
CALL_KIND_TOKENS = {"strong": 4000, "light": 1200, "rule": 0}
CALL_KIND_LIMITS = {"strong": STRONG_CALLS_PER_DAY, "light": LIGHT_CALLS_PER_DAY}
# §7.2：单 tick 突发上限 ≤ 1 次 H、≤ 3 次 M。
BURST_LIMITS = {"strong": 1, "light": 3}

# §7.1 / §7.2：层序（due_npcs 的确定性排序）与层频率（游戏日间隔）。
LAYER_ORDER = {"near": 0, "important_far": 1, "background": 2, "unexplored": 3}
LAYER_MIN_INTERVAL_DAYS = {"near": 0, "important_far": 1, "background": 3}


def _collection_has(collection, ref):
    """`ref` 是否在集合里（list / tuple / set / dict 键都认；其余视为不在）。"""
    if isinstance(collection, (dict, list, tuple, set)):
        return ref in collection
    return False


def _active_schedule_slot(npc, now_minute):
    """当前时刻命中的日程 slot（§4.2：slot 不跨 0 点）；没有则 `None`。"""
    schedule = npc.get("schedule")
    if not isinstance(schedule, list) or not _is_int(now_minute):
        return None
    tod = now_minute % MINUTES_PER_DAY
    for slot in schedule:
        if not isinstance(slot, dict):
            continue
        start = slot.get("from_tod_minute")
        end = slot.get("to_tod_minute")
        if _is_int(start) and _is_int(end) and start <= tod < end:
            return slot
    return None


def _schedule_needs_move(npc, now_minute):
    """§8.4 日程义务：到点不在岗（有活动 slot 且位置不符）。"""
    slot = _active_schedule_slot(npc, now_minute)
    if slot is None:
        return False
    place = _text(slot.get("place_id"))
    return bool(place) and place != _npc_place(npc)


def derive_importance(npc, view):
    """§12.1 P10：importance 派生公式 `0.4×主线 threads + 0.3×faction + 0.3×secret`。

    三个输入都是 0/1 的事实判断：在主线 threads 看 `view.main_thread_refs`、
    有 faction 看 `view.faction_refs`（编排层注入；缺键按「不在」）、
    有 secret 看 `npc.secrets` 非空。显式覆盖（静态档案给了 importance）
    由 `_effective_importance` 优先处理，本函数只算公式值。
    """
    npc = npc if isinstance(npc, dict) else {}
    view = view if isinstance(view, dict) else {}
    npc_id = _npc_id(npc)
    score = 0.0
    if _collection_has(view.get("main_thread_refs"), npc_id):
        score += IMPORTANCE_WEIGHT_THREADS
    if _collection_has(view.get("faction_refs"), npc_id):
        score += IMPORTANCE_WEIGHT_FACTION
    if npc.get("secrets"):
        score += IMPORTANCE_WEIGHT_SECRET
    return score


def _effective_importance(npc, view):
    """§12.1 P10：显式覆盖优先（静态档案 importance > 0 视为显式），否则公式。"""
    npc_id = _npc_id(npc)
    overrides = view.get("importance_overrides")
    if isinstance(overrides, dict):
        value = overrides.get(npc_id)
        if _is_number(value):
            return _clamp(float(value), 0.0, 1.0)
    explicit = npc.get("importance")
    if _is_number(explicit) and float(explicit) > 0.0:
        return _clamp(float(explicit), 0.0, 1.0)
    return derive_importance(npc, view)


def _goal_targets_known(npc, view):
    """§7.1：NPC 的 active goal `target_ref` 指向某个 PC / 玩家已知线索。"""
    goals = npc.get("goals")
    if not isinstance(goals, list):
        return False
    for goal in goals:
        if not isinstance(goal, dict) or goal.get("status") != "active":
            continue
        target = _text(goal.get("target_ref"))
        if not target:
            continue
        if _collection_has(view.get("party_refs"), target):
            return True
        if _collection_has(view.get("known_refs"), target):
            return True
    return False


def layer_of(npc, view):
    """§7.1 四层判定，按表短路：`near` → `important_far` → `background`。

    只看三件事：与 PC 的空间关系（`view.party_places` / `view.bands`）、
    重要性（P5：`importance ≥ IMPORTANCE_THRESHOLD`，或 active goal 指向
    PC / 玩家已知线索）、是否在玩家所在地点集里。未具名条目归 `unexplored`
    （§7.1：该层不承载具名 NPC，人口摘要信号归编排层，个体生成归 M12）。
    **每 tick 现算、不缓存**（§7.1）：玩家走远一步 → 同一 tick 内立刻降层，
    否则成本会漏。
    """
    npc = npc if isinstance(npc, dict) else {}
    view = view if isinstance(view, dict) else {}
    if not _npc_id(npc):
        return "unexplored"
    place = _npc_place(npc)
    party_places = _string_list(view.get("party_places"))
    bands = view.get("bands") if isinstance(view.get("bands"), dict) else {}
    if place and place in party_places:
        return "near"
    if place:
        for party_place in party_places:
            if bands.get((place, party_place)) == "adjacent":
                return "near"
    if _effective_importance(npc, view) >= IMPORTANCE_THRESHOLD:
        return "important_far"
    if _goal_targets_known(npc, view):
        return "important_far"
    return "background"


def due_npcs(block, view, now_minute):
    """§7.1 / §7.2：本 tick 到期的 `npc_id` 表（按层 × 频率 × last_tick_minute 去重）。

    频率（§7.2）：near 每 tick；important_far 每游戏日 1 次（事件触发由编排层
    重复调 tick 表达，本函数只管日切）；background 每 3 游戏日 1 次；
    unexplored 不承载具名 NPC、永不到期。到点不在岗（日程违约）的 NPC 额外
    到期——日程移动是规则动作、零 token，不受层频率限制（§8.4 离线底线）。
    返回顺序确定：near → important_far → background，层内按 importance 降序、
    再按 npc_id 字典序（可重放）。

    §8.4（V12）：`view.force_due_ids`（缺省空表）里的 id 无视层频率直接到期
    ——编排层经 `world.match_event` + 重判预算（`world.register_rejudge`）
    注入；缺键 = M11 行为逐字节不变（WORLD-DESIGN §8.4 的口径）。
    """
    block = block if isinstance(block, dict) else {}
    view = view if isinstance(view, dict) else {}
    now_minute = now_minute if _is_int(now_minute) else 0
    npcs = block.get("npcs")
    if not isinstance(npcs, dict):
        return []
    forced = view.get("force_due_ids")
    forced = {str(item) for item in forced
              if isinstance(item, (str, int)) and not isinstance(item, bool)
              and str(item)} if isinstance(forced, (list, tuple, set)) else set()
    day = now_minute // MINUTES_PER_DAY
    due = []
    for npc_id in sorted(npcs, key=str):
        npc = npcs.get(npc_id)
        if not isinstance(npc, dict):
            continue
        layer = layer_of(npc, view)
        if layer == "unexplored":
            continue
        last = _count(npc.get("last_tick_minute"))
        last_day = last // MINUTES_PER_DAY
        if layer == "near":
            frequency_due = last < now_minute
        elif layer == "important_far":
            frequency_due = last_day < day
        else:
            frequency_due = \
                last_day + LAYER_MIN_INTERVAL_DAYS["background"] <= day
        schedule_due = last < now_minute and _schedule_needs_move(npc, now_minute)
        if frequency_due or schedule_due or npc_id in forced:
            due.append((LAYER_ORDER[layer],
                        -_effective_importance(npc, view), npc_id))
    due.sort()
    return [item[2] for item in due]


def _budget_day_of(source, npc):
    """账本游戏日：`now_minute // 1440`（P7：只读 CHRON 给的分钟，不新建时钟）。"""
    minute = _now_of(source, npc)
    if _is_int(minute) and minute >= 0:
        return minute // MINUTES_PER_DAY
    return None


def _budget_used(budget, day):
    """当日已用量 `(strong, light, tokens)`；账本是别的游戏日 → 视为零（§8.2 跨日重置）。"""
    budget = budget if isinstance(budget, dict) else {}
    recorded = budget.get("day")
    if day is not None and _is_int(recorded) and recorded != day:
        return 0, 0, 0
    return (_count(budget.get("strong_calls")),
            _count(budget.get("light_calls")),
            _count(budget.get("tokens")))


def _ensure_budget_day(budget, now_minute):
    """§8.2 跨日重置：`world_minute // 1440` 变化 → 计数器归零（返回新账本）。"""
    budget = budget if isinstance(budget, dict) else {}
    clean = {key: _count(budget.get(key)) for key in BUDGET_KEYS}
    if not _is_int(now_minute) or now_minute < 0:
        return clean
    day = now_minute // MINUTES_PER_DAY
    if clean["day"] == day:
        return clean
    return {"day": day, "strong_calls": 0, "light_calls": 0, "tokens": 0}


def can_call_ai(npc, view, budget):
    """§8.2 / §8.3：本次 tick 该 NPC 能否调模型（预算 + 突发上限 + 降级档位）。

    - `background` / `unexplored`：0 次模型调用（§7.2）；
    - `view.ai_configured is False`：无配置 = L2，走规则候选（§8.4 正常路径）；
    - 降级档位 L2 / L3：不调模型；L0 只允许 strong、L1 只允许 light；
    - 当日次数上限（P4 反解 h ≤ 6 / m ≤ 30）或 token 余量不足 → 不调、不抛错；
    - 单 tick 突发上限 ≤ 1 H / ≤ 3 M（§7.2）：本 tick 已用量由 `view.tick_burst`
      注入（`tick` 维护；直接调用时缺键按 0 计）。
    """
    npc = npc if isinstance(npc, dict) else {}
    view = view if isinstance(view, dict) else {}
    budget = budget if isinstance(budget, dict) else {}
    if layer_of(npc, view) in ("background", "unexplored"):
        return False
    if view.get("ai_configured") is False:
        return False
    kind = TIER_CALL_KIND.get(degrade_tier(npc, view, budget))
    if kind is None:
        return False
    strong, light, tokens = _budget_used(budget, _budget_day_of(view, npc))
    calls = strong if kind == "strong" else light
    if calls >= CALL_KIND_LIMITS[kind]:
        return False
    if tokens + CALL_KIND_TOKENS[kind] > NPC_DAILY_TOKEN_BUDGET:
        return False
    burst = view.get("tick_burst")
    if isinstance(burst, dict) and _count(burst.get(kind)) >= BURST_LIMITS[kind]:
        return False
    return True


def charge(budget, tier, tokens=None):
    """§8.2 记账：`tier ∈ strong / light / rule`；返回**新**账本（不改入参）。

    `tokens` 缺省按档位常数（strong=4000 / light=1200 / rule=0）；`tokens` 是
    估算值、不是账单（§8.2），真实账单由 AI Gateway 统计。
    """
    budget = budget if isinstance(budget, dict) else {}
    kind = tier if tier in CALL_KIND_TOKENS else "rule"
    amount = tokens if _is_int(tokens) and tokens >= 0 else CALL_KIND_TOKENS[kind]
    charged = {key: _count(budget.get(key)) for key in BUDGET_KEYS}
    if kind == "strong":
        charged["strong_calls"] += 1
    elif kind == "light":
        charged["light_calls"] += 1
    charged["tokens"] += amount
    return charged


def degrade_tier(npc, view, budget):
    """§8.3 降级阶梯：返回 `L0` / `L1` / `L2` / `L3`。

    判定顺序（自重到轻）：

    - **L3**：`plan` 已 `dropped` 且当前无可行选项（只写 idle journal；
      不是死锁——`needs` 仍在涨，次日预算重置后自然恢复）；
    - **L2**：无 AI 配置（`view.ai_configured is False`，§8.4 正常路径）
      或当日 token 用量 ≥ 100%；
    - **L1**：`important_far` 层，或当日 token 用量 ≥ 80%；
    - **L0**：其余（预算充足 + near 层关键决策）。

    单调性（§8.3「当天降到 L2 不回到 L1」）：token 用量当天只增不减
    （跨日归零除外），用量单调 ⇒ 档位单调，不需要额外的状态位。
    """
    npc = npc if isinstance(npc, dict) else {}
    view = view if isinstance(view, dict) else {}
    budget = budget if isinstance(budget, dict) else {}
    plan = npc.get("plan")
    if isinstance(plan, dict) and _text(plan.get("status")) == "dropped":
        if not hard_filter(npc, view, _now_of(view, npc))["ok"]:
            return "L3"
    if view.get("ai_configured") is False:
        return "L2"
    _, _, tokens = _budget_used(budget, _budget_day_of(view, npc))
    ratio = tokens / float(NPC_DAILY_TOKEN_BUDGET)
    if ratio >= 1.0:
        return "L2"
    if layer_of(npc, view) == "important_far" or ratio >= BUDGET_WARN_RATIO:
        return "L1"
    return "L0"


def _revalidate(npc, option, view, now_minute):
    """§5.8 ④：AI 候选必须重过一次硬约束（复验不过就丢，不豁免）。"""
    if _option_error(option) is not None:
        return False
    candidate = _clone(option)
    candidate.setdefault("source", "ai")
    reject = (
        check_distance(npc, candidate, view)
        or check_time(npc, candidate, view, now_minute)
        or check_resource(npc, candidate, view)
        or check_law(npc, candidate, view)
        or check_knowledge(npc, candidate, view)
    )
    return reject is None


def _write_back(npc, decision, now_minute):
    """§5.7 的最小回写：`move` → 更新位置；其余 → 写入 `plan`。

    P7：NPC 的延迟执行用块内 `plan.until_minute` 表达，**不进** `chron.actions`；
    到期由下一次 `catch_up` 收敛（§7.4）。`conflict` 不回写（§5.7：下个 tick 重试）。
    """
    chosen = decision.get("chosen")
    if not isinstance(chosen, dict):
        return
    if _text(chosen.get("verb")) == "move":
        place = _text(chosen.get("place_id"))
        if place:
            npc["location"]["place_id"] = place
            npc["location"]["since_minute"] = now_minute
        return
    plan_id, step_index = _plan_step(npc, chosen, now_minute)
    minutes = chosen.get("minutes")
    minutes = minutes if _is_int(minutes) and minutes >= 0 else 0
    npc["plan"] = {
        "plan_id": plan_id,
        "goal_id": _text(chosen.get("goal_id")),
        "step_index": step_index,
        "status": "running",
        "started_minute": now_minute,
        "until_minute": now_minute + minutes,
    }


def _tick_schedule_move(block, npc_id, slot, npc_view, port, now_minute):
    """§8.4 日程义务：到点不在岗 → 生成规则移动提案并提交（离线也走）。

    返回 `True` 表示本 tick 该 NPC 已被日程步骤处理（无论移动是否被硬约束
    拦下——拦下也要留 journal，回答「他为什么没去上班」）。
    """
    npc = block["npcs"][npc_id]
    place = _text(slot.get("place_id"))
    travel = _travel_minutes(npc_view, place)
    minutes = travel if travel is not None else 0
    option = {
        "option_id": "sched_%s_%s" % (npc_id, _text(slot.get("slot_id")) or place),
        "verb": "move",
        "place_id": place,
        "minutes": min(minutes, MAX_OPTION_MINUTES),
        "source": "rule",
        "risk": 0.0,
    }
    npc_view["options"] = [option]
    decision = decide(npc, npc_view)
    block["journal"].append(decision)
    npc["last_tick_minute"] = now_minute
    if decision["chosen"] is None:
        return True          # 硬约束拦下（如不认识路）：留痕、可解释
    proposal = to_proposal(decision, npc)
    port.submit(proposal, idempotency_key=idempotency_key(decision, npc))
    _write_back(npc, decision, now_minute)
    return True


def catch_up(block, view, now_minute):
    """§7.4 补算：长推进后把跨过的到期计划一次收敛。

    返回 `{"block", "deferred", "processed"}`（新块；`processed` = 收敛的计划数，
    `deferred` = 被跳过的格点数）。

    - 步进集合 = `(start, now]` 内的 10 分钟格点 ∪ 跨过的日界；`start` 取
      全块最小的 `last_tick_minute`（§7.4 的 `crossed_minutes`）；
    - 处理顺序：按 minute 升序，同 minute 按 (importance 降序, npc_id)（§7.4）；
    - 上限 `MAX_CATCHUP_STEPS = 8`：超限时**到期计划一律收敛**（第 1 条：
      必须收敛，否则永远卡住），其余格点记
      `journal(kind="catchup_deferred", count=N)`（第 2 条），远场层直接跳到
      最新分钟、不再逐点补（第 3 条：远场本来就是日粒度）；
    - 保证（§7.4 / 验收）：**终态收敛**（没有 active 计划的 `until_minute ≤ now`）、
      **幂等**（同一 `last_tick_minute` 重跑不重复记账、不重复 journal）、
      **可解释**（`journal` 里有 `catchup_deferred`）；
    - 只读 CHRON 给的分钟（P7）：不改 `advance_to` 的语义、不把计划塞进
      `chron.actions`；跨过的日界顺手做预算归零（§8.2）。
    """
    notes = []
    block = migrate_block(block, notes)
    view = view if isinstance(view, dict) else {}
    result = {"deferred": 0, "processed": 0}
    if not _is_int(now_minute) or now_minute < 0:
        result["block"] = block
        return result
    npcs = block["npcs"]

    starts = [_count(npc.get("last_tick_minute")) for npc in npcs.values()]
    start = min(starts) if starts else now_minute
    if start >= now_minute:
        result["block"] = block
        return result

    steps = set()
    grid = (start // CATCHUP_GRID_MINUTES + 1) * CATCHUP_GRID_MINUTES
    while grid <= now_minute:
        steps.add(grid)
        grid += CATCHUP_GRID_MINUTES
    for day in range(start // MINUTES_PER_DAY + 1,
                     now_minute // MINUTES_PER_DAY + 1):
        steps.add(day * MINUTES_PER_DAY)

    # 到期计划（必须全部收敛，§7.4 第 1 条）：minute 升序，
    # 同 minute 按 (importance 降序, npc_id) 稳定排序。
    expiries = []
    for npc_id in sorted(npcs, key=str):
        npc = npcs[npc_id]
        plan = npc.get("plan")
        if not isinstance(plan, dict) or _text(plan.get("status")) not in PLAN_ACTIVE:
            continue
        until = plan.get("until_minute")
        if not _is_int(until) or until > now_minute:
            continue
        minute = max(until, _count(npc.get("last_tick_minute")) + 1)
        expiries.append((minute, -_effective_importance(npc, view), npc_id))
    expiries.sort()

    for minute, _neg_importance, npc_id in expiries:
        npc = npcs[npc_id]
        plan = npc["plan"]
        plan["status"] = "done"
        npc["last_tick_minute"] = max(_count(npc.get("last_tick_minute")), minute)
        block["journal"].append({
            "kind": "catchup_plan_done",
            "npc_id": npc_id,
            "plan_id": _text(plan.get("plan_id")),
            "at_minute": minute,
            "decision_id": "cat_" + _digest("neura.catchup.done", npc_id,
                                            _text(plan.get("plan_id")),
                                            minute)[:16],
        })
        result["processed"] += 1

    if len(steps) > MAX_CATCHUP_STEPS:
        # §7.4 超限：其余格点记 catchup_deferred（幂等：同一时刻只记一次），
        # 远场层直接跳到最新分钟。
        deferred = len(steps) - min(len(expiries), len(steps))
        if deferred > 0 and not any(
                entry.get("kind") == "catchup_deferred"
                and entry.get("at_minute") == now_minute
                for entry in block["journal"] if isinstance(entry, dict)):
            block["journal"].append({
                "kind": "catchup_deferred",
                "count": deferred,
                "at_minute": now_minute,
                "decision_id": "cat_" + _digest("neura.catchup.deferred",
                                                start, now_minute,
                                                deferred)[:16],
            })
            result["deferred"] = deferred
        for npc_id in sorted(npcs, key=str):
            npc = npcs[npc_id]
            if layer_of(npc, view) in ("important_far", "background"):
                npc["last_tick_minute"] = now_minute

    block["budget"] = _ensure_budget_day(block["budget"], now_minute)
    result["block"] = block
    return result


def tick(block, view, *, port, now_minute):
    """§5.8 一次 tick（签名照抄设计，不改）：返回**新块**，不返回事件、不碰 state。

    每个到期 NPC（`due_npcs`）依次走：

    ① 日程义务：到点不在岗 → 规则移动提案并提交（离线也走，§8.4）；
    ② 降级阶梯 L0–L3：L3 只写 `journal(kind="idle")`、不提交（§8.3）；
    ③ 硬约束过滤；`hard["ok"]` 且预算 / 突发上限允许时，经
       `port.generate_candidates` 取 AI 候选并**逐条复验**（§5.8 ④），
       按档位 `charge` 记账（L0 → strong、L1 → light）；
    ④ 需求评分 → 选择 → journal → `port.submit(proposal, idempotency_key)`。

    `port` 是注入的回调对象（依赖注入）：`submit` 必须实现；
    `generate_candidates` 可选——没有它 = 无 AI 配置的正常路径（§8.4），
    NPC 仍按日程移动、按需求阈值行动、硬约束照拦。本函数不改写入参
    （block / view），不 import 业务模块、不新建时钟（P7：`now_minute`
    只来自 CHRON）。
    """
    view = view if isinstance(view, dict) else {}
    block = prune_block(block)
    block["budget"] = _ensure_budget_day(block["budget"], now_minute)
    budget = block["budget"]
    burst = {"strong": 0, "light": 0}

    for npc_id in due_npcs(block, view, now_minute):
        npc = block["npcs"].get(npc_id)
        if not isinstance(npc, dict):
            continue
        npc_view = dict(view)
        npc_view["now_minute"] = now_minute            # P7：不新建时钟
        npc_view["campaign_seed"] = _count(block.get("campaign_seed"))
        npc_view["layer"] = layer_of(npc, npc_view)    # N3 的 decide 读它
        npc_view["tick_burst"] = dict(burst)
        npc_view["budget"] = dict(budget)
        outer_options = view.get("options")
        npc_view["options"] = list(outer_options) if isinstance(outer_options, list) else []

        # ① 日程义务（§8.4：无 AI 也照走）。
        slot = _active_schedule_slot(npc, now_minute)
        slot_place = _text(slot.get("place_id")) if isinstance(slot, dict) else ""
        if slot_place and slot_place != _npc_place(npc):
            if _tick_schedule_move(block, npc_id, slot, npc_view, port, now_minute):
                budget = block["budget"]
                continue

        # ② 降级阶梯（§8.3）。
        tier = degrade_tier(npc, npc_view, budget)
        if tier == "L3":
            block["journal"].append({
                "kind": "idle",
                "npc_id": npc_id,
                "at_minute": now_minute,
                "decision_id": "idle_" + _digest("neura.idle", npc_id,
                                                 now_minute)[:16],
                "reason": "plan 已 dropped 且无可行选项（§8.3 L3）",
            })
            npc["last_tick_minute"] = now_minute
            continue

        # ③ 硬约束 + AI 候选（预算允许才调，§5.8 ②）。
        hard = hard_filter(npc, npc_view, now_minute)
        options = list(hard["options"])
        kind = TIER_CALL_KIND.get(tier)
        if hard["ok"] and kind and hasattr(port, "generate_candidates") \
                and can_call_ai(npc, npc_view, budget):
            raw = port.generate_candidates(npc, npc_view, budget=budget)
            cands = [item for item in (raw if isinstance(raw, list) else [])
                     if _revalidate(npc, item, npc_view, now_minute)]
            if cands:
                npc_view["cost_tier"] = "H" if kind == "strong" else "M"
                npc_view["ai_tokens"] = CALL_KIND_TOKENS[kind]
                options.extend(_clone(cands))
                block["budget"] = budget = charge(budget, kind)
                burst[kind] += 1
                npc_view["tick_burst"] = dict(burst)
                npc_view["budget"] = dict(budget)
        npc_view["options"] = options

        # ④ 评分 → 选择 → journal → 窄口提交（§5.8 ③④）。
        decision = decide(npc, npc_view)
        block["journal"].append(decision)
        npc["last_tick_minute"] = now_minute
        if decision["chosen"] is None:
            continue
        proposal = to_proposal(decision, npc)
        port.submit(proposal, idempotency_key=idempotency_key(decision, npc))
        _write_back(npc, decision, now_minute)

    return block
