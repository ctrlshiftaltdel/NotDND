#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""状态与事件层内核（STATE-DESIGN 的 M7-1 + M7-2 切片）。

只做库：不碰 HTTP，也不 import 任何业务模块（依赖方向硬约束，STATE-DESIGN §3 S4 /
`GDD-BASELINE.md` §4.2）——本模块只搬运**不透明块** + 结构化 `changes`，
语义解释归调用方；它不认识六维、地点或 NPC。

覆盖 STATE-DESIGN 的：
- 存储布局（§4.1）：每战役一个目录，检查点 + 追加式 NDJSON 事件日志（含归档段）+ 幂等索引；
- 事件封套（§5，含 A0「一次提交 = 一条事件 = 一行追加」）；
- 幂等（§7，含 7.4「索引可由事件日志重建」）；
- 提交时序（§8，含 8.3 崩溃点）；
- 检查点字段（§6.1）与写入时机（§6.3：事件数 / 时间 / 日志体积 / 优雅退出）；
- 加载顺序与重放（§9，含 `.bak` 回退、末行丢弃、中间断裂拒载、`.tmp` 清理）；
- 重建规则（§6.2：`replay == live` 在规范化 JSON 下逐字节相等）；
- 零依赖校验器（§5.4 `validate_event`）；
- 接口（§12.1 `open_campaign` / `Handle`，含 `replay()` / `flush()`）。

**`import state` 不触碰磁盘**（沿用 no-side-effects-on-import 口径，
`GDD-BASELINE.md` §6.5）：本模块顶层只做常量与函数定义。

M7-1 / M7-2 边界：M7-1 落「事件封套 + 原子提交 + 幂等 + 版本 + 骨架」，
M7-2（Issue #267）在既有实现上补齐 §6.3 检查点调度、§4.1 归档段与 §9 加载顺序——
归档段可被重放拼回（归档不删事实；整体改名搬迁，写入侧不产生重叠段）、
加载时按 §9 忽略 `seq ≤ 检查点 seq` 的重叠行、半写末行**落盘**截除
（否则下一次提交会与半行粘成一行，加载时把新事件一起丢掉）。

M7-3（Issue #276）在既有实现上补两块**接线所需**的能力（不改任何块的语义）：
- §10 存档迁移 `migrate_legacy()`：老单文件 JSON → 新档文件族（含 `.bak`），
  字段映射仍归调用方（§10.1 是网页层的旧格式知识），本模块只做落盘半边（§10.2 步骤 2–6）；
- §11 门面态同步 `Handle.stage()` / `Handle.meta()`：`stream`（展示流）与不透明块
  **不进事件溯源**，只能随检查点落盘——`commit()` 没有也不该有它们的入口。

存储布局（§4.1）::

    <base_dir>/campaigns/<campaign_id>/
        snapshot.json          检查点（§6.1 字段）
        snapshot.json.bak      上一次成功检查点（崩溃回退用）
        events.ndjson          追加式事件日志：一行一个事件（A0）
        events.<n>.ndjson      归档段（超过阈值时把活动日志整体改名搬入）
        idempotency.json       幂等索引的独立可读副本
"""

import copy
import hashlib
import json
import os
import re
import threading
import time

__all__ = [
    "STATE_VERSION",
    "EVENT_VERSION",
    "DEFAULT_SAVE_ROOT",
    "StateError",
    "Handle",
    "open_campaign",
    "migrate_legacy",
    "validate_event",
]

# ── 版本（§3 S6：版本分层，读到更高版本一律拒载）─────────────────────────────
STATE_VERSION = 1          # 档（检查点）版本
EVENT_VERSION = 1          # 事件封装版本（预留给后续迁移；M7-1 不写入事件体）

# ── 路径与布局（§4.1）───────────────────────────────────────────────────────
# 默认落本地运行物目录 web-saves/（禁入库）；与 notdnd_web.py 同口径：可被
# NOTDND_SAVE 覆盖，且**惰性创建**——import 不建目录、不碰磁盘。
DEFAULT_SAVE_ROOT = os.environ.get("NOTDND_SAVE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "web-saves")

CAMPAIGN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # 沿用 SID_RE 口径，防 ../
CAMP_DIRNAME = "campaigns"

SNAPSHOT_NAME = "snapshot.json"
SNAPSHOT_BAK_NAME = "snapshot.json.bak"
SNAPSHOT_TMP_NAME = "snapshot.json.tmp"
EVENTS_NAME = "events.ndjson"
IDEMPOTENCY_NAME = "idempotency.json"

# 迁移备份（§10.2 步骤 2，GDD §19.4「不可逆迁移保留备份」）：老档**原文**存这里。
LEGACY_BAK_NAME = "snapshot.json.pre-v0.bak"

# 归档段（§4.1）：`events.<n>.ndjson`，序号升序即时间顺序（不删事实）。
ARCHIVE_RE = re.compile(r"^events\.(\d+)\.ndjson$")

# ── 常量（§6.3 / §7 / §4.1）─────────────────────────────────────────────────
CHECKPOINT_EVERY = 32                       # 每 N 个事件写一次检查点
CHECKPOINT_MAX_AGE_S = 5.0                   # 距上次检查点超过该秒数也写
CHECKPOINT_MAX_LOG_BYTES = 1024 * 1024       # 日志增长越过该字节数也写
IDEM_TTL_DAYS = 90                           # 幂等记录默认存活天数
IDEM_KEEP = 4096                             # 幂等记录保留下限（条）
EVENT_ARCHIVE_THRESHOLD = 100000             # 活动日志超过该行数时归档（§4.1）

# ── 枚举（§5.1，初始可扩展）────────────────────────────────────────────────
_VISIBILITY_SCOPES = ("public", "team", "private", "gm")
_SOURCES = ("player", "npc", "system", "ai_proposal")
_OPS = ("set", "unset", "delta", "append", "replace_block")
_COMMIT_STATUS = "committed"
_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

# 幂等摘要覆盖的字段（§7.2 / §7.4）：提交入参 → request_digest。
# 不含 base_seq（并发基线）与 idempotency_key（键本身），二者不是载荷。
REQUEST_FIELDS = (
    "type", "actor", "target", "cause", "visibility", "changes",
    "rules_version", "roll_refs", "source", "world_time",
)
# 已提交形态的 CommitResult → result_digest（§7.2 / §7.4）。
RESULT_FIELDS = ("commit_id", "seq_from", "seq_to", "event_ids", "state_digest")

# 事件封套必填字段（§5.1；可为 null 的字段也必须在）。
_EVENT_FIELDS = (
    "event_id", "campaign_id", "seq", "commit_id", "world_time", "type",
    "actor", "target", "cause", "visibility", "changes", "rules_version",
    "roll_refs", "commit_status", "source", "idempotency_key", "ts",
)

_MISSING = object()   # 内部哨兵：区分「未提供」与「显式 null」


class StateError(Exception):
    """状态层不可恢复的错误（损坏日志 / 拒载高版本 / 非法入参）。"""


# ════════════════════════════════════════════════════════════════════════════
# 小工具：规范 JSON / 摘要 / 时间 / 指针
# ════════════════════════════════════════════════════════════════════════════

def _now_ms():
    """现实时间毫秒（**仅审计**；不参与任何权威判断，§5.1 `ts`）。

    独立成函数便于测试进程内注入受控时钟（`state._now_ms = lambda: ...`）。
    """
    return int(time.time() * 1000)


def _canonical(value):
    """规范化 JSON（§6.1）：`ensure_ascii=False, sort_keys=True`、无多余空白。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


def _digest(value):
    """对规范化 JSON 取 SHA-256 十六进制。"""
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _event_id(campaign_id, seq):
    """§5.1：全局唯一、可确定性重建。"""
    return "%s:e%012d" % (campaign_id, seq)


def _commit_id(campaign_id, seq):
    """§5.1：由 seq 派生，与唯一事件 1:1（A0）。"""
    return "%s:c%08d" % (campaign_id, seq)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_pointer(path):
    """RFC 6901 JSON Pointer → 段列表；根 `""` → `[]`。"""
    if path == "":
        return []
    if not isinstance(path, str) or not path.startswith("/"):
        raise StateError("JSON Pointer 必须以 '/' 开头（或为空串）：%r" % (path,))
    return [seg.replace("~1", "/").replace("~0", "~")
            for seg in path[1:].split("/")]


def _ptr_read(doc, parts):
    cur = doc
    for part in parts:
        if isinstance(cur, list):
            cur = cur[int(part)]
        elif isinstance(cur, dict):
            cur = cur[part]
        else:
            raise StateError("指针路径无法下探：%r" % (parts,))
    return cur


def _ptr_write(container, parts, value):
    """在 container 的 pointer 处写 value；根（空 parts）返回新根。"""
    if not parts:
        return value
    cur = container
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        index = int(last)
        if index < 0:
            raise StateError("负数组下标不受支持：%r" % (last,))
        while len(cur) <= index:
            cur.append(None)
        cur[index] = value
    else:
        cur[last] = value
    return container


def _ptr_delete(container, parts):
    """删除 pointer 处的值；根（空 parts）返回 `_MISSING`。"""
    if not parts:
        return _MISSING
    cur = container
    for part in parts[:-1]:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    last = parts[-1]
    if isinstance(cur, list):
        del cur[int(last)]
    else:
        del cur[last]
    return container


# ════════════════════════════════════════════════════════════════════════════
# changes 的机械应用（§5.3：state.py 不理解 path 的业务含义）
# ════════════════════════════════════════════════════════════════════════════

def _apply_change(blocks, change):
    """按 `op` 机械应用一条变更到 `blocks`（原地）。"""
    block = change.get("block")
    if not isinstance(block, str) or not block:
        raise StateError("change.block 非法：%r" % (block,))
    op = change.get("op")
    parts = _parse_pointer(change.get("path", ""))

    if op == "replace_block":
        blocks[block] = copy.deepcopy(change.get("after"))
        return

    container = blocks.get(block)
    if container is None:
        # 块尚未存在：按路径首段猜容器类型（S4：块对 state 不透明，
        # 只能靠 JSON 结构；数字首段视为数组下标，其余视为对象键）。
        if not parts:
            container = [] if op == "append" else {}
        elif parts[0].isdigit():
            container = []
        else:
            container = {}
        blocks[block] = container

    if op == "set":
        result = _ptr_write(container, parts, copy.deepcopy(change.get("after")))
        if result is not container:
            blocks[block] = result
    elif op == "delta":
        # `delta` 只允许整数；`after` 是变更后值，重放以它为准（确定性）。
        before = change.get("before")
        after = change.get("after")
        if not _is_int(before) or not _is_int(after):
            raise StateError("delta 只允许整数：%r -> %r" % (before, after))
        result = _ptr_write(container, parts, after)
        if result is not container:
            blocks[block] = result
    elif op == "append":
        if not parts:
            target = container
        else:
            try:
                target = _ptr_read(container, parts)
            except (KeyError, IndexError, TypeError, ValueError, StateError):
                target = None
            if target is None:
                # 目标列表尚不存在：先建空列表再追加（块对 state 不透明）。
                target = []
                _ptr_write(container, parts, target)
        if not isinstance(target, list):
            raise StateError("append 目标不是列表：%r" % (change.get("path"),))
        target.append(copy.deepcopy(change.get("after")))
    elif op == "unset":
        result = _ptr_delete(container, parts)
        if result is _MISSING:
            blocks[block] = None
        elif result is not container:
            blocks[block] = result
    else:
        raise StateError("未知 op：%r" % (op,))


def apply_changes(blocks, changes):
    """一次性、整体应用一组变更（§9 步骤 3：行完整才进得来）。"""
    for change in changes:
        _apply_change(blocks, change)


# ════════════════════════════════════════════════════════════════════════════
# 校验器（§5.4）
# ════════════════════════════════════════════════════════════════════════════

def validate_event(ev):
    """零依赖事件校验器，返回错误说明列表（空列表 = 合法）。"""
    errors = []
    if not isinstance(ev, dict):
        return ["事件必须是对象"]

    for field in _EVENT_FIELDS:
        if field not in ev:
            errors.append("缺少必填字段：%s" % field)
    if errors:
        return errors

    campaign_id = ev["campaign_id"]
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.match(campaign_id):
        errors.append("campaign_id 非法：%r" % (campaign_id,))

    seq = ev["seq"]
    if not _is_int(seq) or seq < 1:
        errors.append("seq 必须是 ≥ 1 的整数：%r" % (seq,))

    if isinstance(campaign_id, str) and _is_int(seq) and seq >= 1:
        if ev["event_id"] != _event_id(campaign_id, seq):
            errors.append("event_id 与 (campaign_id, seq) 不一致：%r" % (ev["event_id"],))
        if ev["commit_id"] != _commit_id(campaign_id, seq):
            errors.append("commit_id 与 (campaign_id, seq) 不一致：%r" % (ev["commit_id"],))
    else:
        if not isinstance(ev["event_id"], str):
            errors.append("event_id 必须是字符串")
        if not isinstance(ev["commit_id"], str):
            errors.append("commit_id 必须是字符串")

    world_time = ev["world_time"]
    if world_time is not None and not isinstance(world_time, dict):
        errors.append("world_time 必须是对象或 null")

    ev_type = ev["type"]
    if not isinstance(ev_type, str) or not _TYPE_RE.match(ev_type):
        errors.append("type 非法（应为 <domain>.<verb>）：%r" % (ev_type,))

    if not isinstance(ev["actor"], str) or not ev["actor"]:
        errors.append("actor 必须是非空字符串")
    for field in ("target", "cause"):
        if ev[field] is not None and not isinstance(ev[field], str):
            errors.append("%s 必须是字符串或 null" % field)

    visibility = ev["visibility"]
    if not isinstance(visibility, dict):
        errors.append("visibility 必须是对象")
    else:
        if visibility.get("scope") not in _VISIBILITY_SCOPES:
            errors.append("visibility.scope 非法：%r" % (visibility.get("scope"),))
        subjects = visibility.get("subjects")
        if not isinstance(subjects, list) or any(
                not isinstance(s, str) for s in subjects):
            errors.append("visibility.subjects 必须是字符串列表")

    changes = ev["changes"]
    if not isinstance(changes, list):
        errors.append("changes 必须是数组")
    else:
        errors.extend(_validate_changes(changes))

    if not isinstance(ev["rules_version"], str) or not ev["rules_version"]:
        errors.append("rules_version 必须是非空字符串")
    if not isinstance(ev["roll_refs"], list):
        errors.append("roll_refs 必须是数组")

    if ev["commit_status"] != _COMMIT_STATUS:
        errors.append("commit_status 落盘必须为 %r：%r"
                      % (_COMMIT_STATUS, ev["commit_status"]))
    if ev["source"] not in _SOURCES:
        errors.append("source 非法：%r" % (ev["source"],))
    if ev["idempotency_key"] is not None and not isinstance(ev["idempotency_key"], str):
        errors.append("idempotency_key 必须是字符串或 null")
    if not _is_int(ev["ts"]):
        errors.append("ts 必须是整数毫秒")
    return errors


def _validate_changes(changes):
    errors = []
    for index, change in enumerate(changes):
        where = "changes[%d]" % index
        if not isinstance(change, dict):
            errors.append("%s 必须是对象" % where)
            continue
        if not isinstance(change.get("block"), str) or not change.get("block"):
            errors.append("%s.block 非法" % where)
        path = change.get("path", "")
        if not isinstance(path, str) or (path != "" and not path.startswith("/")):
            errors.append("%s.path 必须以 '/' 开头（或为空串）：%r" % (where, path))
        op = change.get("op")
        if op not in _OPS:
            errors.append("%s.op 非法：%r" % (where, op))
        if "before" not in change or "after" not in change:
            errors.append("%s 缺少 before / after" % where)
        if op == "delta":
            if not _is_int(change.get("before")) or not _is_int(change.get("after")):
                errors.append("%s.delta 只允许整数 before / after" % where)
    return errors


# ════════════════════════════════════════════════════════════════════════════
# 摘要与幂等记录（§7）
# ════════════════════════════════════════════════════════════════════════════

def _request_payload(source):
    """从提交入参或事件封套取出 request_digest 覆盖的字段。"""
    return {name: source.get(name) for name in REQUEST_FIELDS}


def _result_payload(result):
    """从 CommitResult（已提交形态）取出 result_digest 覆盖的字段。"""
    return {name: result.get(name) for name in RESULT_FIELDS}


def _state_digest(seq, blocks):
    """§8.1：对提交后 seq + blocks 的摘要。"""
    return _digest({"seq": seq, "blocks": blocks})


def _record_from_event(ev, state_digest):
    """§7.2 / §7.4：由事件封套重建一条幂等记录（日志是唯一真相）。"""
    event_ids = [ev["event_id"]]
    committed = {
        "commit_id": ev["commit_id"],
        "seq_from": ev["seq"],
        "seq_to": ev["seq"],
        "event_ids": event_ids,
        "state_digest": state_digest,
    }
    return {
        "key": ev["idempotency_key"],
        "commit_id": ev["commit_id"],
        "seq": ev["seq"],
        "event_ids": event_ids,
        "request_digest": _digest(_request_payload(ev)),
        "result_digest": _digest(_result_payload(committed)),
        "state_digest": state_digest,
        "ts": ev["ts"],
        "expires_at": ev["ts"] + IDEM_TTL_DAYS * 86400 * 1000,
    }


def _replayed_result(record):
    """§8.1：`replayed` 复用首次的 commit_id / seq_* / event_ids。"""
    seq = record.get("seq")
    return {
        "status": "replayed",
        "commit_id": record.get("commit_id"),
        "seq_from": seq,
        "seq_to": seq,
        "event_ids": list(record.get("event_ids") or []),
        "state_digest": record.get("state_digest"),
        "error": None,
    }


def _normalize_visibility(visibility):
    if visibility is None:
        return {"scope": "public", "subjects": []}
    if not isinstance(visibility, dict):
        raise StateError("visibility 必须是对象：%r" % (visibility,))
    vis = dict(visibility)
    vis.setdefault("scope", "public")
    vis.setdefault("subjects", [])
    return vis


# ════════════════════════════════════════════════════════════════════════════
# 持久化小工具
# ════════════════════════════════════════════════════════════════════════════

def _read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_atomic_bytes(path, data):
    """写 `<path>.tmp` → flush + fsync → os.replace（§3 S5；二进制载荷）。"""
    tmp = path + ".tmp"
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _write_atomic(path, text):
    """写 `<path>.tmp` → flush + fsync → os.replace（§3 S5）。"""
    _write_atomic_bytes(path, text.encode("utf-8"))


def _genesis_blocks():
    """创世态的不透明块（§6.1；chron 由 M9 预留，不在本层创建）。"""
    return {"rules": None, "atlas": None, "guide": None}


def _genesis_snapshot(campaign_id, save_name=None):
    now = _now_ms()
    payload = {
        "schema_version": STATE_VERSION,
        "kind": "state.snapshot",
        "campaign_id": campaign_id,
        "seq": 0,
        "world_time": None,
        "created": now,
        "updated": now,
        "save_name": (save_name or campaign_id)[:40],
        "stream": {"log": [], "cursor": 0},
        "blocks": _genesis_blocks(),
        "indexes": {"idempotency": {}, "archived_upto": 0},
    }
    payload["checksum"] = _digest(payload)
    return payload


def _verify_snapshot(data, campaign_id):
    """检查点可用性。返回错误说明；更高版本抛 StateError（拒载，§10.3）。"""
    if not isinstance(data, dict):
        return "不是对象"
    schema_version = data.get("schema_version")
    if not _is_int(schema_version):
        return "schema_version 非法：%r" % (schema_version,)
    if schema_version > STATE_VERSION:
        raise StateError("拒载：检查点 schema_version=%d 高于本实现 %d"
                         % (schema_version, STATE_VERSION))
    if schema_version < STATE_VERSION:
        return "schema_version=%d 低于本实现（需迁移）" % schema_version
    if data.get("kind") != "state.snapshot":
        return "kind 非法：%r" % (data.get("kind"),)
    if data.get("campaign_id") != campaign_id:
        return "campaign_id 不匹配：%r" % (data.get("campaign_id"),)
    checksum = data.get("checksum")
    if not isinstance(checksum, str):
        return "缺少 checksum"
    recomputed = _digest({k: v for k, v in data.items() if k != "checksum"})
    if checksum != recomputed:
        return "checksum 不匹配"
    return None


def _load_snapshot(campaign_dir, campaign_id, notes):
    """§9 步骤 1：snapshot.json → snapshot.json.bak → 创世态。"""
    primary = os.path.join(campaign_dir, SNAPSHOT_NAME)
    backup = os.path.join(campaign_dir, SNAPSHOT_BAK_NAME)
    for path, label in ((primary, SNAPSHOT_NAME), (backup, SNAPSHOT_BAK_NAME)):
        if not os.path.exists(path):
            continue
        try:
            data = _read_json(path)
        except (OSError, ValueError) as exc:
            notes.append("检查点解析失败（%s）：%s" % (label, exc))
            continue
        problem = _verify_snapshot(data, campaign_id)   # 高版本 StateError 直接冒泡
        if problem:
            notes.append("检查点校验失败（%s）：%s" % (label, problem))
            continue
        if label == SNAPSHOT_BAK_NAME:
            notes.append("主检查点不可用，已回退到 %s" % SNAPSHOT_BAK_NAME)
        return data
    if os.path.exists(primary) or os.path.exists(backup):
        notes.append("检查点与备份均不可用，退回创世态（state_corrupt）")
    return None


def _read_segment(path, notes, label, allow_partial_tail):
    """逐行解析一个 NDJSON 段；返回 `(events, cut)`（§9 步骤 2）。

    `cut` 非 `None` = 盘上末行应被截除的字节偏移。半写末行留在盘上时，下一次
    `commit()` 追加的行会与它粘成一行，再次加载就会连**新事件**一起丢掉——
    所以「丢弃末行」必须是**落盘**的（§8.3「行中」）。

    `allow_partial_tail` 只对**活动日志**为真：末行无 `\\n` / 解析失败 → 丢弃并给出
    截除点。归档段后面还有事件，末行残缺即「重放区间中间断裂」→ 拒载（不自动跳过）。
    """
    if not os.path.exists(path):
        return [], None
    with open(path, "rb") as handle:
        raw = handle.read()
    if not raw:
        return [], None

    lines = raw.split(b"\n")
    complete = lines[:-1]     # 以 \n 收尾的完整行
    tail = lines[-1]          # 最后一个 \n 之后的内容（不完整即使非空）

    events = []
    cut = None
    start = 0                 # 当前行的字节起点
    for index, bline in enumerate(complete):
        if bline.strip():
            try:
                events.append(json.loads(bline.decode("utf-8")))
            except (ValueError, UnicodeDecodeError) as exc:
                if allow_partial_tail and index == len(complete) - 1:
                    notes.append("事件日志末行解析失败，已丢弃（半写）")
                    cut = start
                    break
                raise StateError("%s 中间行解析失败（第 %d 行）：%s"
                                 % (label, index + 1, exc))
        start += len(bline) + 1
    if cut is None and tail.strip():
        if allow_partial_tail:
            notes.append("事件日志末行不完整，已丢弃（半写）")
            cut = start
        else:
            raise StateError("%s 末行不完整（缺结尾 \\n）：%s" % (label, path))
    return events, cut


def _read_events(events_path, notes):
    """§9 步骤 2：活动日志逐行解析；末行半写 → 丢弃并给出截除点。"""
    return _read_segment(events_path, notes, "事件日志", allow_partial_tail=True)


def _archive_paths(campaign_dir):
    """归档段（`events.<n>.ndjson`）按序号升序——序号即时间顺序。

    序号必须自 `1` 起连续：缺号意味一段事实拼不回（§4.1「归档不删事实」）→ 拒载。
    """
    numbered = []
    try:
        names = os.listdir(campaign_dir)
    except OSError:
        return []
    for name in names:
        match = ARCHIVE_RE.match(name)
        if match:
            numbered.append((int(match.group(1)), os.path.join(campaign_dir, name)))
    numbered.sort()
    numbers = [number for number, _ in numbered]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        raise StateError("归档段序号不连续（缺段）：%r" % (numbers,))
    return numbered


def _read_all_events(campaign_dir, notes):
    """按「归档段（升序）→ 活动日志」拼出完整事件序列（§9 步骤 2）。

    返回 `(events, archived_hint, live_cut)`：`archived_hint` 为归档段里的最大 `seq`
    （0 = 无归档）；`live_cut` 为活动日志应截除的字节偏移（`None` = 无需截除）。
    """
    events = []
    archived_hint = 0
    archives = _archive_paths(campaign_dir)
    for _, path in archives:
        segment, _ = _read_segment(path, notes,
                                   "归档段 %s" % os.path.basename(path),
                                   allow_partial_tail=False)
        events.extend(segment)
        for ev in segment:
            seq = ev.get("seq") if isinstance(ev, dict) else None
            if _is_int(seq) and seq > archived_hint:
                archived_hint = seq
    if archives:
        notes.append("已读取归档段 %d 个（归档不删事实）" % len(archives))
    live, live_cut = _read_events(os.path.join(campaign_dir, EVENTS_NAME), notes)
    events.extend(live)
    return events, archived_hint, live_cut


def _truncate_to(path, size):
    """把活动日志截到 `size` 字节；返回是否真的截除（§9 步骤 2 的「丢弃末行」落盘）。"""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        if size >= len(raw):
            return False
        _write_atomic_bytes(path, raw[:size])
        return True
    except OSError:
        return False


def _validate_chain(events, base_seq):
    """§9 步骤 2：重放区间 seq 严格递增（无跳号 / 回退），commit_id 唯一。"""
    last = base_seq
    seen_commits = set()
    for ev in events:
        seq = ev.get("seq")
        if not _is_int(seq):
            raise StateError("事件 seq 非法：%r" % (seq,))
        if seq <= base_seq:
            continue          # 归档段 / 与检查点重叠的重复段：唯一允许的重复
        if seq != last + 1:
            raise StateError("重放区间 seq 不连续（跳号 / 回退）：期望 %d，得到 %d"
                             % (last + 1, seq))
        errors = validate_event(ev)
        if errors:
            raise StateError("重放区间事件校验失败：%s" % "; ".join(errors))
        commit_id = ev.get("commit_id")
        if commit_id in seen_commits:
            raise StateError("重放区间 commit_id 重复：%r（A0 下本不可能）"
                             % (commit_id,))
        seen_commits.add(commit_id)
        last = seq
    return last


def _replay_events(events, base_seq, blocks, world_time):
    """§6.2 / §9 步骤 3-4：折入重放区间事件，并重建幂等索引。"""
    seq = base_seq
    idem = {}
    for ev in events:
        if ev["seq"] <= base_seq:
            continue
        apply_changes(blocks, ev["changes"])
        seq = ev["seq"]
        world_time = ev["world_time"]
        key = ev.get("idempotency_key")
        if key is not None:
            idem[key] = _record_from_event(ev, _state_digest(seq, blocks))
    return seq, world_time, idem


# ════════════════════════════════════════════════════════════════════════════
# Handle（§12.1）
# ════════════════════════════════════════════════════════════════════════════

class Handle:
    """一个战役的句柄：唯一提交出口（§3 S3）。

    只改允许路径：本类不 import 任何业务模块，也不直接改块——
    调用方把「不透明新块 / 声明式 changes」交进来，本类负责排号、留痕、
    原子落盘、重放。
    """

    def __init__(self, campaign_id, campaign_dir, snapshot):
        self.campaign_id = campaign_id
        self._dir = campaign_dir
        self._events_path = os.path.join(campaign_dir, EVENTS_NAME)
        self._lock = threading.RLock()
        self._closed = False
        self.notes = []          # 加载 / 检查点过程的说明（含 state_corrupt）

        self._blocks = copy.deepcopy(snapshot.get("blocks") or _genesis_blocks())
        self._seq = snapshot.get("seq", 0)
        self._world_time = snapshot.get("world_time")
        self._stream = copy.deepcopy(snapshot.get("stream") or {"log": [], "cursor": 0})
        # `created` 的 0 是合法值（§10.1：老档「未知」逐字搬），不能被 `or` 换成当前时间；
        # 只有缺失 / 脏值才落到 `_now_ms()`。
        created = snapshot.get("created")
        self._created = created if _is_int(created) and created >= 0 else _now_ms()
        self._updated = snapshot.get("updated") or self._created
        # 空串 `save_name` 是**合法值**（= 未命名，展示时按 `campaign_id` 回退，§6.1），
        # 同样不能被 `or` 换成 `campaign_id`——否则门面重载后「未命名」会变成「已命名」。
        save_name = snapshot.get("save_name")
        self._save_name = save_name if isinstance(save_name, str) else campaign_id
        indexes = snapshot.get("indexes") or {}
        self._idem = copy.deepcopy(indexes.get("idempotency") or {})
        archived_upto = indexes.get("archived_upto")
        self._archived_upto = (archived_upto
                               if _is_int(archived_upto) and archived_upto >= 0
                               else 0)

        self._events_since_checkpoint = 0
        self._last_checkpoint_ms = _now_ms()
        self._last_checkpoint_bytes = self._log_size()

    # ── 只读视图（§12.1）────────────────────────────────────────────────────
    def view(self):
        """只读视图：深拷贝 {campaign_id, seq, world_time, blocks, stream}。"""
        with self._lock:
            return {
                "campaign_id": self.campaign_id,
                "seq": self._seq,
                "world_time": copy.deepcopy(self._world_time),
                "blocks": copy.deepcopy(self._blocks),
                "stream": copy.deepcopy(self._stream),
            }

    # ── 门面态同步（§11：展示流与不透明块不进事件溯源）──────────────────────
    def stage(self, *, blocks=None, stream=None, save_name=None, created=None):
        """把调用方（网页层门面）的内存态推入句柄——**不写事件**。

        §11 把 `stream`（叙事流）与不透明块的权威留在调用方：它们不是事实，
        与事件 `seq` 是**两条独立序列**（禁止互相赋值），只能随检查点落盘；
        `commit()` 因此没有也不该有它们的入口。

        `blocks` 按**块名**逐块覆盖（未提到的块原样保留，未来块不被旧调用方抹掉）；
        `stream` 整体替换；`save_name` / `created`（毫秒，§6.1）给了才动。
        同步本身不落盘——落盘由下一次检查点 / `flush()` 负责。
        """
        with self._lock:
            if blocks:
                for name, value in blocks.items():
                    self._blocks[name] = copy.deepcopy(value)
            if stream is not None:
                self._stream = copy.deepcopy(stream)
            if save_name is not None:
                self._save_name = str(save_name)
            if created is not None:
                self._created = int(created)
            self._updated = _now_ms()

    def meta(self):
        """只读元信息：`created` / `updated` / `save_name`（毫秒 / 毫秒 / 串）。

        §12.1 的 `view()` 只含 {campaign_id, seq, world_time, blocks, stream}，
        展示字段不在其中；门面重建需要它们，故单列一个只读入口（形参 / 返回值
        都只是拷贝，句柄内部态不被外部改写）。
        """
        with self._lock:
            return {
                "created": self._created,
                "updated": self._updated,
                "save_name": self._save_name,
            }

    def replay(self, upto_seq=None):
        """从创世（seq 0）按事件日志重放出完整状态（§6.2 / §12.1）。

        - 读「归档段 + 活动日志」拼出的完整历史（归档不删事实）；
        - `replay()` 是**从创世的纯日志折叠**：日志自身必须无重复——重放区间内的
          跳号 / 回退一律按 §9 拒载（「按检查点忽略重叠」是加载路径的规则，
          由 `_validate_chain(events, 检查点 seq)` 承担）；
        - `upto_seq` 只折入 `seq ≤ upto_seq` 的事件（历史时点重放；`None` = 到最新）；
        - 重放**只应用 `changes`**，不重掷骰（§6.2）；`stream` 非事件溯源（§11），
          重放结果恒为空流。

        不变量（§6.2）：在写入侧产出的（无重叠）日志上，`replay()` 与 `view()` 的
        `seq` / `blocks` 在**规范化 JSON** 下逐字节相等。
        """
        if upto_seq is not None and (not _is_int(upto_seq) or upto_seq < 0):
            raise StateError("upto_seq 必须是 ≥ 0 的整数或 null：%r" % (upto_seq,))
        with self._lock:
            notes = []
            events, _archived_hint, _live_cut = _read_all_events(self._dir, notes)
            _validate_chain(events, 0)
            blocks = _genesis_blocks()
            seq = 0
            world_time = None
            for ev in events:
                if upto_seq is not None and ev["seq"] > upto_seq:
                    break
                apply_changes(blocks, ev["changes"])
                seq = ev["seq"]
                world_time = ev["world_time"]
            return {
                "campaign_id": self.campaign_id,
                "seq": seq,
                "world_time": world_time,
                "blocks": blocks,
                "stream": {"log": [], "cursor": 0},
            }

    # ── 唯一提交出口（§8）──────────────────────────────────────────────────
    def commit(self, *, base_seq, idempotency_key, type, actor, target=None,
               cause=None, visibility=None, changes=(), rules_version,
               roll_refs=(), world_time=None, source="player", revalidate=None):
        """一次调用恰好产出一条事件（A0）；返回 CommitResult 字典（§8.1）。

        **没有** `events` 形参——多事件提交在接口层无法表达（§5.0）。
        `revalidate` 钩子按 M7 约定**不实现**，仅在签名上留位（§8.2）。
        """
        del revalidate   # M7 不实现可选的 revalidate 钩子（§8.2）
        with self._lock:
            if self._closed:
                return self._result("rejected", code="已关闭",
                                    message="句柄已关闭")
            try:
                changes = copy.deepcopy(list(changes or ()))
                roll_refs = copy.deepcopy(list(roll_refs or ()))
                vis = _normalize_visibility(visibility)
            except StateError as exc:
                return self._result("rejected", code="入参非法", message=str(exc))

            request = {
                "type": type, "actor": actor, "target": target, "cause": cause,
                "visibility": vis, "changes": changes,
                "rules_version": rules_version, "roll_refs": roll_refs,
                "source": source, "world_time": world_time,
            }
            request_digest = _digest(_request_payload(request))

            # ③ 幂等查表（§7.3 / §8.1）：命中重放 / 冲突 → 直接返回，不取事件号
            record = self._idem.get(idempotency_key) if idempotency_key else None
            if record is not None and _now_ms() > record.get("expires_at", 0):
                record = None     # 过期 → 视为新提交（§7.3）
            if record is not None:
                if request_digest == record.get("request_digest"):
                    return _replayed_result(record)
                return self._result("conflict", code="幂等键冲突",
                                    message="同一幂等键的载荷不同")

            # R7：非系统提交必须自带幂等键，否则 rejected（§15 R7）
            if source != "system" and not idempotency_key:
                return self._result("rejected", code="缺少幂等键",
                                    message="非系统提交必须提供 idempotency_key")

            # ④ 版本校验（§8.2 乐观并发，默认 reject）
            if base_seq != self._seq:
                return self._result("conflict", code="版本冲突",
                                    message="base_seq=%r 与当前 seq=%d 不符"
                                            % (base_seq, self._seq))

            # ⑤ 生成唯一一条事件（A0）
            new_seq = self._seq + 1
            event = {
                "event_id": _event_id(self.campaign_id, new_seq),
                "campaign_id": self.campaign_id,
                "seq": new_seq,
                "commit_id": _commit_id(self.campaign_id, new_seq),
                "world_time": world_time,
                "type": type,
                "actor": actor,
                "target": target,
                "cause": cause,
                "visibility": vis,
                "changes": changes,
                "rules_version": rules_version,
                "roll_refs": roll_refs,
                "commit_status": _COMMIT_STATUS,
                "source": source,
                "idempotency_key": idempotency_key,
                "ts": _now_ms(),
            }
            errors = validate_event(event)
            if errors:
                return self._result("rejected", code="事件校验失败",
                                    message="; ".join(errors))

            new_blocks = copy.deepcopy(self._blocks)
            try:
                apply_changes(new_blocks, changes)
            except StateError as exc:
                return self._result("rejected", code="变更应用失败",
                                    message=str(exc))
            state_digest = _state_digest(new_seq, new_blocks)

            # ⑥ 追加一行 + flush + fsync（原子提交；失败则内存不动）
            self._append_event(event)

            # ⑦ 应用到内存态
            self._blocks = new_blocks
            self._seq = new_seq
            self._world_time = world_time
            self._updated = event["ts"]

            # ⑧ 记幂等记录（落盘由检查点副本 + 恢复时日志重建双保，§7.4）
            if idempotency_key:
                self._idem[idempotency_key] = _record_from_event(event, state_digest)

            # ⑨ 检查点？（§6.3 时机）
            self._events_since_checkpoint += 1
            self._maybe_checkpoint()

            # ⑩ 返回（A0：seq_from == seq_to == new_seq，event_ids 长度 1）
            return {
                "status": "committed",
                "commit_id": event["commit_id"],
                "seq_from": new_seq,
                "seq_to": new_seq,
                "event_ids": [event["event_id"]],
                "state_digest": state_digest,
                "error": None,
            }

    def flush(self):
        """强制写检查点（优雅退出用，§6.3）：把滞后归零，可重复调用。"""
        with self._lock:
            self._write_checkpoint()

    def close(self):
        """写检查点并标记关闭（优雅退出）。"""
        with self._lock:
            if self._closed:
                return
            try:
                self._write_checkpoint()
            except OSError as exc:
                self.notes.append("关闭时写检查点失败：%s" % exc)
            self._closed = True

    # ── 内部：结果构造 ─────────────────────────────────────────────────────
    def _result(self, status, code=None, message=None):
        """conflict / rejected：不写任何事件，seq_* 返回当前 seq（§8.1）。"""
        return {
            "status": status,
            "commit_id": None,
            "seq_from": self._seq,
            "seq_to": self._seq,
            "event_ids": [],
            "state_digest": _state_digest(self._seq, self._blocks),
            "error": {"code": code, "message": message},
        }

    # ── 内部：持久化 ───────────────────────────────────────────────────────
    def _append_event(self, event):
        line = json.dumps(event, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n"
        with open(self._events_path, "a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())

    def _log_size(self):
        try:
            return os.path.getsize(self._events_path)
        except OSError:
            return 0

    def _snapshot_payload(self):
        payload = {
            "schema_version": STATE_VERSION,
            "kind": "state.snapshot",
            "campaign_id": self.campaign_id,
            "seq": self._seq,
            "world_time": copy.deepcopy(self._world_time),
            "created": self._created,
            "updated": _now_ms(),
            "save_name": self._save_name,
            "stream": copy.deepcopy(self._stream),
            "blocks": copy.deepcopy(self._blocks),
            "indexes": {
                "idempotency": copy.deepcopy(self._idem),
                "archived_upto": self._archived_upto,
            },
        }
        payload["checksum"] = _digest(payload)
        return payload

    def _evict_idempotency(self):
        """淘汰过期记录；保留最近 IDEM_KEEP 条为硬下限（§7.3）。"""
        now = _now_ms()
        live = {k: v for k, v in self._idem.items()
                if v.get("expires_at", 0) >= now}
        if len(live) > IDEM_KEEP:
            ordered = sorted(live.items(), key=lambda kv: kv[1].get("ts", 0))
            live = dict(ordered[-IDEM_KEEP:])
        self._idem = live

    def _write_checkpoint(self):
        """备份现有检查点 → 写 .tmp → os.replace；并写幂等独立副本（§6.3）。

        归档（§4.1）按崩溃安全顺序：① 先写检查点（其 `seq` 已覆盖待归档区间，并记
        `archived_upto`）→ ② 活动日志整体原子改名为归档段。崩溃在 ① 与 ② 之间 = 事实
        仍在活动日志；崩溃在 ② 之后 = 事实在归档段——**两边都不会有重叠段**，于是
        §9「唯一允许的重复」只在加载既有的重叠档时才被用到，写入侧从不制造它。
        """
        self._evict_idempotency()
        plan = self._archive_plan()                    # 只读：定阈值、算归档内容
        if plan is not None:
            # 先记「承诺」：本检查点的 seq 已 ≥ 归档区间，故 ② 即便失败也自洽
            # （下一轮归档以实际文件为准，不看这个值）。
            self._archived_upto = max(self._archived_upto, plan["seq"])
        payload = self._snapshot_payload()             # ①（含 archived_upto）
        text = _canonical(payload)
        primary = os.path.join(self._dir, SNAPSHOT_NAME)
        backup = os.path.join(self._dir, SNAPSHOT_BAK_NAME)
        tmp = os.path.join(self._dir, SNAPSHOT_TMP_NAME)
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        if os.path.exists(primary):
            os.replace(primary, backup)
        os.replace(tmp, primary)

        idem_text = _canonical({
            "schema_version": STATE_VERSION,
            "kind": "state.idempotency",
            "campaign_id": self.campaign_id,
            "indexes": self._idem,
        })
        _write_atomic(os.path.join(self._dir, IDEMPOTENCY_NAME), idem_text)

        if plan is not None:                           # ②（失败只记说明，下次再试）
            self._archive_apply(plan)

        self._last_checkpoint_ms = _now_ms()
        self._last_checkpoint_bytes = self._log_size()
        self._events_since_checkpoint = 0

    # ── 内部：归档段（§4.1）────────────────────────────────────────────────
    def _archive_plan(self):
        """活动日志超过 `EVENT_ARCHIVE_THRESHOLD` 行时给出归档计划（只读，不落盘）。

        计划 = `{"path": 新归档段路径, "count": 完整行数, "seq": 末行 seq}`；未达阈值 /
        读不了 / 行不完整或不可解析 = `None`——归档失败不影响正确性。
        """
        try:
            with open(self._events_path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            self.notes.append("归档读取活动日志失败（不影响正确性）：%s" % exc)
            return None
        if raw and not raw.endswith(b"\n"):
            # 半写末行只可能来自上次崩溃；加载时已截除，这里保守跳过（不改动它）。
            self.notes.append("归档跳过：活动日志末行不完整（不影响正确性）")
            return None
        lines = raw.split(b"\n")[:-1] if raw else []
        if len(lines) <= EVENT_ARCHIVE_THRESHOLD:
            return None
        seq = self._seq
        for index, bline in enumerate(lines):
            try:
                event = json.loads(bline.decode("utf-8"))
                line_seq = event["seq"]
            except (ValueError, UnicodeDecodeError, KeyError, TypeError):
                self.notes.append("归档跳过：活动日志第 %d 行不可解析（不影响正确性）"
                                  % (index + 1))
                return None
            if _is_int(line_seq) and line_seq >= 0:
                seq = line_seq
        try:
            path = self._next_archive_path()
        except OSError as exc:
            self.notes.append("归档跳过：无法确定段序号（不影响正确性）：%s" % exc)
            return None
        return {"path": path, "count": len(lines), "seq": seq}

    def _next_archive_path(self):
        """下一个归档段：`events.<n>.ndjson`，`n` 取现有最大序号 + 1。"""
        highest = 0
        for name in os.listdir(self._dir):
            match = ARCHIVE_RE.match(name)
            if match:
                highest = max(highest, int(match.group(1)))
        return os.path.join(self._dir, "events.%d.ndjson" % (highest + 1))

    def _archive_apply(self, plan):
        """② 活动日志**整体原子改名**为归档段（§4.1「搬」；归档不删事实）。

        一次 `os.replace` 同时完成「归档段出现」与「活动日志去前缀」，不存在
        「段已写、前缀未去」的中间态——于是任何崩溃点都不会留下重叠段：
        崩溃在改名之前 = 事实仍在活动日志（检查点已覆盖）；之后 = 事实在归档段。
        失败只记说明、**不**动活动日志（下一次检查点再试）。
        """
        target = plan["path"]
        if os.path.exists(target):
            self.notes.append("归档跳过：目标段已存在 %s" % os.path.basename(target))
            return
        try:
            os.replace(self._events_path, target)
        except OSError as exc:
            self.notes.append("归档改名失败（不影响正确性）：%s" % exc)
            return
        self.notes.append("已归档 %d 行到 %s（归档不删事实）"
                          % (plan["count"], os.path.basename(target)))

    def _maybe_checkpoint(self):
        """§6.3：按事件数 / 日志体积 / 时间任一触发；失败只记说明（可滞后）。"""
        age_s = (_now_ms() - self._last_checkpoint_ms) / 1000.0
        size = self._log_size()
        due = (self._events_since_checkpoint >= CHECKPOINT_EVERY
               or size - self._last_checkpoint_bytes >= CHECKPOINT_MAX_LOG_BYTES
               or age_s > CHECKPOINT_MAX_AGE_S)
        if not due:
            return
        try:
            self._write_checkpoint()
        except OSError as exc:
            self.notes.append("检查点写入失败（不影响正确性）：%s" % exc)


# ════════════════════════════════════════════════════════════════════════════
# open_campaign（§9 / §12.1）
# ════════════════════════════════════════════════════════════════════════════

def _cleanup_tmp(campaign_dir):
    """§9 步骤 5：清除原子写残留 `*.tmp`。"""
    try:
        names = os.listdir(campaign_dir)
    except OSError:
        return
    for name in names:
        if name.endswith(".tmp"):
            try:
                os.unlink(os.path.join(campaign_dir, name))
            except OSError:
                pass


def open_campaign(campaign_id, *, base_dir=None, save_name=None):
    """加载（+ 惰性迁移）一个战役，损坏则抛 `StateError`（§9 / §12.1）。

    `base_dir` 为可选扩展：默认落 `DEFAULT_SAVE_ROOT`（`web-saves/`），
    测试可指向临时目录（保持 §12.1 `open_campaign(campaign_id)` 契约不变）。
    """
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.match(campaign_id):
        raise StateError("非法 campaign_id（须匹配 ^[A-Za-z0-9_-]{1,64}$）：%r"
                         % (campaign_id,))
    root = os.path.join(base_dir or DEFAULT_SAVE_ROOT, CAMP_DIRNAME)
    campaign_dir = os.path.join(root, campaign_id)
    os.makedirs(campaign_dir, exist_ok=True)

    _cleanup_tmp(campaign_dir)          # §9 步骤 5（先于加载，残留不影响结果）

    notes = []
    snapshot = _load_snapshot(campaign_dir, campaign_id, notes)
    fresh = snapshot is None
    if fresh:
        snapshot = _genesis_snapshot(campaign_id, save_name=save_name)

    events_path = os.path.join(campaign_dir, EVENTS_NAME)
    events, archived_hint, live_cut = _read_all_events(campaign_dir, notes)
    _validate_chain(events, snapshot.get("seq", 0))
    if live_cut is not None:
        # §9 步骤 2：「丢弃末行」必须落盘，否则下次提交会与半行粘成一行。
        if _truncate_to(events_path, live_cut):
            notes.append("活动日志半写末行已从盘上截除（下次提交可正常追加）")
        else:
            notes.append("活动日志半写末行截除失败；继续提交前须人工检查该日志")

    blocks = copy.deepcopy(snapshot.get("blocks") or _genesis_blocks())
    seq, world_time, rebuilt = _replay_events(
        events, snapshot.get("seq", 0), blocks, snapshot.get("world_time"))
    snapshot["blocks"] = blocks
    snapshot["seq"] = seq
    snapshot["world_time"] = world_time
    # §9 步骤 4：日志是唯一真相，其重建的记录覆盖检查点里的加速副本。
    indexes = snapshot.get("indexes") or {}
    merged = copy.deepcopy(indexes.get("idempotency") or {})
    merged.update(rebuilt)
    prior_archived = indexes.get("archived_upto")
    if not _is_int(prior_archived) or prior_archived < 0:
        prior_archived = 0
    snapshot["indexes"] = {
        "idempotency": merged,
        # 归档段仍在盘上（归档不删事实）：以归档实际覆盖的 seq 兜底。
        "archived_upto": max(prior_archived, archived_hint),
    }

    handle = Handle(campaign_id, campaign_dir, snapshot)
    handle.notes.extend(notes)
    if fresh and not os.path.exists(events_path):
        # 全新战役：落创世检查点 + 空日志（老档迁移走 migrate_legacy()）。
        open(events_path, "a", encoding="utf-8").close()
        handle._last_checkpoint_ms = _now_ms()
        handle._last_checkpoint_bytes = 0
        handle._write_checkpoint()
    return handle


# ════════════════════════════════════════════════════════════════════════════
# migrate_legacy（§10.2：老档 → 新档文件族）
# ════════════════════════════════════════════════════════════════════════════

def _remove_legacy(path, notes):
    """§10.2 步骤 5：删除老档；失败只记说明（新档已可用，老档留作兜底）。"""
    if not path:
        return
    try:
        os.unlink(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        notes.append("老档删除失败（新档已可用，不影响正确性）：%s" % exc)


def migrate_legacy(campaign_id, legacy_path, *, blocks, stream=None,
                   save_name=None, created=None, base_dir=None):
    """§10.2：把老档（旧单文件 JSON）落成新档文件族，返回可用的 `Handle`。

    §10.1 的**字段映射**（`sid` → `campaign_id`、`log` / `seq` → `stream`、秒 → 毫秒、
    「只补不覆盖」……）是调用方（网页层）的旧格式知识，由调用方按表算好
    `blocks` / `stream` / `save_name` / `created` 传进来；本函数只做**落盘半边**
    （§10.2 步骤 2–6）：

    2. 老档**原文**备份为 `snapshot.json.pre-v0.bak`（GDD §19.4：不可逆迁移留备份）；
    3. 建检查点（事件 `seq = 0`）→ 写 `.tmp` + `os.replace` + **回读校验 `checksum`**；
    4. 建空 `events.ndjson`——老档没有事实日志，历史自迁移点起算（§15 R4），不伪造；
    5. 两个文件都成功之后才删老档（删不掉只记说明，**不滚回**：新档已可用）；
    6. 记一条 `system.migration` 事件（`source="system"`），让「这里发生过一次迁移」可溯源。

    崩溃安全 / 幂等：老档在**新检查点写成功之后**才删；若检查点已在盘上（上次迁移在
    删除老档之前中断），直接返回既有战役并清掉残留老档——**不覆盖**已落盘的新档。
    """
    if not isinstance(campaign_id, str) or not CAMPAIGN_ID_RE.match(campaign_id):
        raise StateError("非法 campaign_id（须匹配 ^[A-Za-z0-9_-]{1,64}$）：%r"
                         % (campaign_id,))
    root = os.path.join(base_dir or DEFAULT_SAVE_ROOT, CAMP_DIRNAME)
    campaign_dir = os.path.join(root, campaign_id)
    os.makedirs(campaign_dir, exist_ok=True)

    if os.path.exists(os.path.join(campaign_dir, SNAPSHOT_NAME)):
        handle = open_campaign(campaign_id, base_dir=base_dir)
        _remove_legacy(legacy_path, handle.notes)
        return handle

    # ② 备份老档原文（逐字搬；不做任何再序列化）
    try:
        with open(legacy_path, "rb") as source:
            raw = source.read()
    except OSError as exc:
        raise StateError("迁移读不到老档 %r：%s" % (legacy_path, exc))
    _write_atomic_bytes(os.path.join(campaign_dir, LEGACY_BAK_NAME), raw)

    # ③ 检查点（§6.1 字段；事件 seq = 0）。`created` 的 0 = 未知也是合法值，
    #    只有缺失 / 脏值才降级成当前时间。
    now = _now_ms()
    payload = {
        "schema_version": STATE_VERSION,
        "kind": "state.snapshot",
        "campaign_id": campaign_id,
        "seq": 0,
        "world_time": None,
        "created": created if _is_int(created) and created >= 0 else now,
        "updated": now,
        "save_name": save_name if isinstance(save_name, str) else campaign_id,
        "stream": copy.deepcopy(stream or {"log": [], "cursor": 0}),
        "blocks": copy.deepcopy(blocks or _genesis_blocks()),
        "indexes": {"idempotency": {}, "archived_upto": 0},
    }
    payload["checksum"] = _digest(payload)
    snapshot_path = os.path.join(campaign_dir, SNAPSHOT_NAME)
    _write_atomic(snapshot_path, _canonical(payload))
    try:
        written = _read_json(snapshot_path)
    except (OSError, ValueError) as exc:
        raise StateError("迁移回读失败（%s）：%s" % (campaign_id, exc))
    problem = _verify_snapshot(written, campaign_id)
    if problem:
        raise StateError("迁移回读校验失败（%s）：%s" % (campaign_id, problem))

    # ④ 空事件日志（老档没有事实日志，不伪造历史）
    events_path = os.path.join(campaign_dir, EVENTS_NAME)
    if not os.path.exists(events_path):
        open(events_path, "a", encoding="utf-8").close()

    handle = open_campaign(campaign_id, base_dir=base_dir)

    # ⑤ 新档可用了才删老档（删不掉就留着，绝不滚回）
    _remove_legacy(legacy_path, handle.notes)

    # ⑥ 迁移事件：A0（一次提交一条事件），`changes` 为空——状态在检查点里，
    #    这条事件只作「迁移发生过」的溯源锚点。`rules_version` 取老档格式版本
    #    （§10.3：M7 只有 v0 → v1）；`type` 必须过 §5.1 的 `<domain>.<verb>`。
    result = handle.commit(base_seq=0, idempotency_key=None,
                           type="system.migration", actor="world",
                           rules_version="v0", changes=[], source="system")
    if result.get("status") != "committed":
        handle.notes.append("迁移事件未落盘：%s" % (result.get("error"),))
    return handle
