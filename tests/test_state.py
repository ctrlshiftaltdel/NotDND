#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""状态层内核回归测试（M7-1，Issue #163；M7-2，Issue #267）。

对照 STATE-DESIGN.md §14 的用例，覆盖：

- **幂等**组：同键同载荷 → `replayed`（事件数不变、副作用一次）；同键不同载荷
  → `conflict`（不写事件、错误码固定）；键过期 → 视为新提交。
- **提交原子性（A0）**组：`commit()` 无多事件入口；一次提交只写一行、
  `seq_from == seq_to`、`len(event_ids) == 1`；一次提交内多项 `changes`
  （跨块、含 set/unset/delta/append）整体生效；行中崩（半行）整体不生效；
  重放区间内 `commit_id` 唯一。
- **快照重建 == 存活态**组（M7-2）：创世 + 全量事件回放、检查点 + 尾部回放（检查点滞后）、
  只有检查点无事件——三者与存活态在**规范化 JSON** 下逐字节相等；`replay(upto_seq=…)` 历史时点。
- **崩溃恢复**组：追加中途崩（半行）丢弃末行且**落盘截除**（后续提交可追加）；
  追加完成、检查点未写 → 重启重放 + 幂等索引由日志重建（§7.4）；检查点损坏 + `.bak` 有效 →
  回退；两者都坏 → 退创世态；日志中间断裂 → 拒载；归档重叠段（`seq ≤ 检查点 seq`）→ 不拒载、
  重放区间内重复 → 拒载；`.tmp` 残留 → 清理。
- **归档段与检查点调度**组（M7-2）：超阈值归档（`events.<n>.ndjson`，整体改名搬迁、
  归档不删事实、重放仍可拼回）；改名失败不丢事实；§6.3 三个触发条件（事件数 / 时间 /
  日志体积）与 `flush()` 优雅退出。
- **结构**断言：`import state` 不触碰磁盘；源码无业务模块 import；`seq` 无空洞。
- **只读增量**（M7-4，Issue #312）：`events_since(since)` 只回 `seq > since`、
  严格递增不重不漏；跨归档段拼接正确；只读不改盘（size + mtime + sha256 指纹不变）、
  不触发检查点；非法 `since` 抛 `StateError`；归档重叠段按「已出现过即跳过」去重。

零依赖：仅 Python 3 标准库。临时存档目录一律落 `tempfile.mkdtemp()`，退出前收摊。
时钟与检查点时机用**进程内注入**（`state._now_ms` / 模块常量）控制，不靠等待。
"""

import hashlib
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import state  # noqa: E402


CAMPAIGN = "camp-01"


# ════════════════════════════════════════════════════════════════════════════
# 小工具
# ════════════════════════════════════════════════════════════════════════════

def _camp_dir(tmp, cid=CAMPAIGN):
    return os.path.join(tmp, "campaigns", cid)


def _events_path(tmp, cid=CAMPAIGN):
    return os.path.join(_camp_dir(tmp, cid), state.EVENTS_NAME)


def _event_lines(tmp, cid=CAMPAIGN):
    path = _events_path(tmp, cid)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle.read().splitlines() if line.strip()]


def _lines_at(path):
    """读任意 NDJSON 段（活动日志 / 归档段）为事件列表。"""
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle.read().splitlines() if line.strip()]


def _archive_path(tmp, index=1, cid=CAMPAIGN):
    """归档段路径：`events.<n>.ndjson`（§4.1）。"""
    return os.path.join(_camp_dir(tmp, cid), "events.%d.ndjson" % index)


def _snapshot_on_disk(tmp, cid=CAMPAIGN):
    """读盘上的检查点（不写盘）；不存在返回 None。"""
    path = os.path.join(_camp_dir(tmp, cid), state.SNAPSHOT_NAME)
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _snapshot_seq(tmp, cid=CAMPAIGN):
    payload = _snapshot_on_disk(tmp, cid)
    return None if payload is None else payload["seq"]


def _canon(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _commit(handle, *, key="k-1", type="core.commit", actor="pc-1", changes=(),
            visibility=None, source="player", rules_version="prism@data-79",
            world_time=None, base_seq=None, target=None, cause=None, roll_refs=()):
    if base_seq is None:
        base_seq = handle.view()["seq"]
    return handle.commit(
        base_seq=base_seq, idempotency_key=key, type=type, actor=actor,
        target=target, cause=cause, visibility=visibility, changes=list(changes),
        rules_version=rules_version, roll_refs=list(roll_refs),
        world_time=world_time, source=source)


def _append_party(pid):
    """往 `rules.party` 追加一名成员（append 对折入次数敏感，能抓出重复折入）。"""
    return [{"block": "rules", "path": "/party", "op": "append", "before": None,
             "after": {"id": pid}}]


class _Thresholds:
    """按用例改写模块常量（检查点 / 归档阈值），退出时逐项还原。"""

    def __init__(self, **overrides):
        self._overrides = overrides
        self._saved = {}

    def __enter__(self):
        for name, value in self._overrides.items():
            self._saved[name] = getattr(state, name)
            setattr(state, name, value)

    def __exit__(self, *exc):
        for name, value in self._saved.items():
            setattr(state, name, value)
        return False


class _NoCheckpoint(_Thresholds):
    """临时关掉自动检查点，精确控制「检查点未写」的崩溃场景。"""

    def __init__(self):
        super().__init__(CHECKPOINT_EVERY=10 ** 9, CHECKPOINT_MAX_AGE_S=1e9,
                         CHECKPOINT_MAX_LOG_BYTES=10 ** 18)


def _set_block_after(value):
    return [{"block": "rules", "path": "/party", "op": "set", "before": None,
             "after": value}]


# ════════════════════════════════════════════════════════════════════════════
# 结构断言（防越权 / 防契约漂移，§14）
# ════════════════════════════════════════════════════════════════════════════

def check_no_business_imports():
    """state.py 源码不出现业务模块的 import（S4 机械守卫）。"""
    with open(os.path.join(ROOT, "state.py"), "r", encoding="utf-8") as handle:
        source = handle.read()
    for name in ("prism_core", "prism_guide", "atlas"):
        pattern = re.compile(r"^[ \t]*(?:import|from)[ \t]+%s\b" % re.escape(name),
                             re.MULTILINE)
        assert not pattern.search(source), "state.py 不应 import %s" % name


def check_import_no_disk():
    """`import state` 不触碰磁盘（在空目录里 import 后目录仍为空）。"""
    tmp = tempfile.mkdtemp()
    try:
        code = ("import sys; sys.path.insert(0, %r); import state; print('ok')"
                % ROOT)
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp,
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok", result.stdout
        assert os.listdir(tmp) == [], os.listdir(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_commit_signature_a0():
    """A0 契约守卫：`commit()` 不含 `events` 形参（多事件无法表达）。"""
    params = inspect.signature(state.Handle.commit).parameters
    assert "events" not in params, "commit() 不应有 events 形参"
    assert any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()) is False
    for name in ("base_seq", "idempotency_key", "changes", "source"):
        assert name in params, "commit() 缺少形参 %s" % name


def check_validate_event():
    """校验器：合法事件零错误；逐项破坏能被指出。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        _commit(handle, key="k-1", changes=_set_block_after([{"id": "pc-1"}]))
        ev = _event_lines(tmp)[0]
        assert state.validate_event(ev) == [], state.validate_event(ev)

        broken_seq = dict(ev, seq=0)
        assert state.validate_event(broken_seq), "seq<1 应报错"
        broken_type = dict(ev, type="BadType")
        assert state.validate_event(broken_type), "type 非法应报错"
        broken_actor = {k: v for k, v in ev.items() if k != "actor"}
        assert state.validate_event(broken_actor), "缺 actor 应报错"
        broken_path = dict(ev, changes=[
            {"block": "rules", "path": "no-slash", "op": "set",
             "before": None, "after": 1}])
        assert state.validate_event(broken_path), "path 不以 / 开头应报错"
        broken_scope = dict(ev, visibility={"scope": "nope", "subjects": []})
        assert state.validate_event(broken_scope), "scope 非法应报错"
        assert state.validate_event("not-a-dict"), "非对象应报错"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# 幂等组（§14）
# ════════════════════════════════════════════════════════════════════════════

def check_idempotent_replay():
    """同键同载荷：事件数不变、replayed、副作用只发生一次。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        changes = _set_block_after([{"id": "item-9"}])
        first = _commit(handle, key="k-dup", changes=changes)
        assert first["status"] == "committed", first
        assert len(_event_lines(tmp)) == 1

        second = _commit(handle, key="k-dup", changes=changes)
        assert second["status"] == "replayed", second
        assert second["commit_id"] == first["commit_id"]
        assert second["seq_from"] == second["seq_to"] == 1
        assert second["event_ids"] == first["event_ids"]
        assert second["state_digest"] == first["state_digest"]

        # 事件数不变（+1 而非 +2）
        assert len(_event_lines(tmp)) == 1
        # 副作用只发生一次（物品只有一份）
        assert handle.view()["blocks"]["rules"]["party"] == [{"id": "item-9"}]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_idempotent_conflict():
    """同键不同载荷：conflict、不写事件、错误码固定。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        before = _commit(handle, key="k", changes=_set_block_after([{"id": "a"}]))
        assert before["status"] == "committed"
        conflicting = _commit(handle, key="k", changes=_set_block_after([{"id": "b"}]))
        assert conflicting["status"] == "conflict", conflicting
        assert conflicting["error"]["code"] == "幂等键冲突"
        assert conflicting["commit_id"] is None
        assert conflicting["event_ids"] == []
        # 不写任何事件，状态不变
        assert len(_event_lines(tmp)) == 1
        assert handle.view()["blocks"]["rules"]["party"] == [{"id": "a"}]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_idempotent_expiry():
    """键过期后再提交 → 视为新提交（可再写一次）。"""
    tmp = tempfile.mkdtemp()
    saved_now = state._now_ms
    clock = [1_000_000_000_000]
    state._now_ms = lambda: clock[0]
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        changes = _set_block_after([{"id": "x"}])
        first = _commit(handle, key="k-ttl", changes=changes)
        assert first["status"] == "committed"
        clock[0] += state.IDEM_TTL_DAYS * 86400 * 1000 + 1
        again = _commit(handle, key="k-ttl", changes=changes)
        assert again["status"] == "committed", again
        assert len(_event_lines(tmp)) == 2
    finally:
        state._now_ms = saved_now
        shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# 提交原子性（A0）组（§14）
# ════════════════════════════════════════════════════════════════════════════

def _multi_changes():
    return [
        {"block": "rules", "path": "/party", "op": "set", "before": None,
         "after": [{"id": "pc-1", "vitality": 10}]},
        {"block": "rules", "path": "/party/0/vitality", "op": "delta",
         "before": 10, "after": 7},
        {"block": "rules", "path": "/flags", "op": "append", "before": None,
         "after": "started"},
        {"block": "atlas", "path": "/frames", "op": "set", "before": None,
         "after": {"w/surface": {"deltas": []}}},
        {"block": "guide", "path": "/notes", "op": "set", "before": None,
         "after": {"x": 1}},
        {"block": "guide", "path": "/notes/x", "op": "unset", "before": 1,
         "after": None},
    ]


def check_a0_one_event_multi_changes():
    """一次提交 = 一行、seq_from==seq_to、event_ids 长度 1；多项 changes 整体生效。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        result = _commit(handle, key="k-1", changes=_multi_changes())
        assert result["status"] == "committed", result
        assert result["seq_from"] == result["seq_to"] == 1
        assert len(result["event_ids"]) == 1
        assert len(_event_lines(tmp)) == 1        # 只多一行

        blocks = handle.view()["blocks"]
        assert blocks["rules"]["party"] == [{"id": "pc-1", "vitality": 7}]
        assert blocks["rules"]["flags"] == ["started"]
        assert blocks["atlas"]["frames"] == {"w/surface": {"deltas": []}}
        assert blocks["guide"]["notes"] == {}

        # 行完整时 replay == live（规范化 JSON 逐字节相等）
        live = handle.view()
        rebuilt = handle.replay()
        assert _canon(rebuilt["blocks"]) == _canon(live["blocks"])
        assert rebuilt["seq"] == live["seq"] == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_log_commit_id_unique():
    """重放区间内同一 commit_id 只出现一次（A0 的日志守卫）。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(4):
                result = _commit(handle, key="k-%d" % index,
                                 changes=[{"block": "rules", "path": "/n",
                                           "op": "append", "before": None,
                                           "after": index}])
                assert result["status"] == "committed"
            events = _event_lines(tmp)
            assert [ev["seq"] for ev in events] == [1, 2, 3, 4]
            commit_ids = [ev["commit_id"] for ev in events]
            assert len(commit_ids) == len(set(commit_ids))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# 崩溃恢复组（§14 / §8.3 / §9）
# ════════════════════════════════════════════════════════════════════════════

def _half_line_scenario(truncated):
    """末行半写（不完整 / 无 \\n）→ 丢弃；本次提交整体不生效；seq 不前进。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            _commit(handle, key="k-1", changes=_set_block_after([{"id": "pc-1"}]))
            assert len(_event_lines(tmp)) == 1

            # 伪造「第 2 次提交正在追加本行时崩溃」：完整事件内容，但**无结尾 \n**。
            fake = {
                "event_id": "camp-01:e000000000002", "campaign_id": "camp-01",
                "seq": 2, "commit_id": "camp-01:c00000002", "world_time": None,
                "type": "core.commit", "actor": "pc-1", "target": None,
                "cause": None, "visibility": {"scope": "public", "subjects": []},
                "changes": [{"block": "rules", "path": "/applied", "op": "set",
                             "before": None, "after": "SHOULD-NOT-APPLY"}],
                "rules_version": "prism@data-79", "roll_refs": [],
                "commit_status": "committed", "source": "player",
                "idempotency_key": "k-2", "ts": 1,
            }
            payload = json.dumps(fake, ensure_ascii=False)
            if truncated:
                payload = payload[: len(payload) // 2]      # 截断（JSON 解析失败）
            with open(_events_path(tmp), "ab") as raw:
                raw.write(payload.encode("utf-8"))

            reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert reloaded.view()["seq"] == 1, reloaded.view()["seq"]
            assert reloaded.view()["blocks"]["rules"] == {"party": [{"id": "pc-1"}]}
            assert "applied" not in json.dumps(reloaded.view()["blocks"])
            assert any("丢弃" in note or "不完整" in note for note in reloaded.notes), \
                reloaded.notes
            assert _canon(reloaded.replay()["blocks"]) == _canon(reloaded.view()["blocks"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_crash_half_line_truncated():
    """追加中途崩：截断的末行 → 丢弃，整体不生效，replay == live。"""
    _half_line_scenario(truncated=True)


def check_crash_half_line_no_newline():
    """追加中途崩：合法 JSON 但缺结尾 \\n（未完成落盘）→ 同样丢弃。"""
    _half_line_scenario(truncated=False)


def check_crash_append_complete_no_checkpoint():
    """行后·⑦ 前：事件完整落盘、检查点未写 → 重启重放；幂等索引由日志重建。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            changes = _set_block_after([{"id": "pc-1"}])
            first = _commit(handle, key="k-1", changes=changes)
            _commit(handle, key="k-2", changes=[
                {"block": "rules", "path": "/party", "op": "append",
                 "before": None, "after": {"id": "pc-2"}}])
            # 模拟崩溃：不 close（不写检查点），直接丢弃句柄
            del handle

            reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert reloaded.view()["seq"] == 2
            assert reloaded.view()["blocks"]["rules"]["party"] == [
                {"id": "pc-1"}, {"id": "pc-2"}]

            # 幂等索引由日志重建（§7.4）→ 同键重试 = replayed，不重复副作用
            before = len(_event_lines(tmp))
            retry = _commit(reloaded, key="k-1", changes=changes)
            assert retry["status"] == "replayed", retry
            assert retry["commit_id"] == first["commit_id"]
            assert retry["state_digest"] == first["state_digest"]
            assert len(_event_lines(tmp)) == before
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_checkpoint_bak_fallback():
    """检查点损坏 + .bak 有效：用 .bak + 重放恢复，记说明。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        _commit(handle, key="k-1", changes=_set_block_after([{"id": "pc-1"}]))
        handle.flush()                     # snapshot=seq1；bak=创世
        _commit(handle, key="k-2", changes=[
            {"block": "rules", "path": "/party", "op": "append",
             "before": None, "after": {"id": "pc-2"}}])
        handle.flush()                     # snapshot=seq2；bak=seq1

        primary = os.path.join(_camp_dir(tmp), state.SNAPSHOT_NAME)
        with open(primary, "w", encoding="utf-8") as broken:
            broken.write("{ this is not json")

        reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
        assert reloaded.view()["seq"] == 2
        assert reloaded.view()["blocks"]["rules"]["party"] == [
            {"id": "pc-1"}, {"id": "pc-2"}]
        assert any(state.SNAPSHOT_BAK_NAME in note or "回退" in note
                   for note in reloaded.notes), reloaded.notes
        assert _canon(reloaded.view()["blocks"]) == _canon(handle.view()["blocks"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_checkpoint_both_corrupt_genesis():
    """检查点与 .bak 都坏：退创世态 + 记 state_corrupt；不猜内容。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        _commit(handle, key="k-1", changes=_set_block_after([{"id": "pc-1"}]))
        handle.flush()
        _commit(handle, key="k-2", changes=[
            {"block": "rules", "path": "/party", "op": "append",
             "before": None, "after": {"id": "pc-2"}}])
        handle.flush()

        for name in (state.SNAPSHOT_NAME, state.SNAPSHOT_BAK_NAME):
            with open(os.path.join(_camp_dir(tmp), name), "w",
                      encoding="utf-8") as broken:
                broken.write("garbage")

        reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
        assert any("state_corrupt" in note for note in reloaded.notes), reloaded.notes
        # 全量重放事件仍能重建（日志是唯一真相）
        assert reloaded.view()["seq"] == 2
        assert _canon(reloaded.view()["blocks"]) == _canon(handle.view()["blocks"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_midlog_break_rejected():
    """重放区间内 seq 跳号 / commit_id 重复 → 拒载（不自动跳过）。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(3):
                _commit(handle, key="k-%d" % index, changes=[
                    {"block": "rules", "path": "/n", "op": "append",
                     "before": None, "after": index}])
            events = _event_lines(tmp)
            assert [ev["seq"] for ev in events] == [1, 2, 3]

            # 制造「跳号」：抹掉第 2 行
            with open(_events_path(tmp), "w", encoding="utf-8") as out:
                out.write(json.dumps(events[0]) + "\n")
                out.write(json.dumps(events[2]) + "\n")
            try:
                state.open_campaign(CAMPAIGN, base_dir=tmp)
            except state.StateError:
                pass
            else:
                raise AssertionError("seq 跳号应拒载")

            # 制造「commit_id 重复」：把第 3 行的 commit_id 改成第 1 行的
            duplicate = dict(events[2], commit_id=events[0]["commit_id"])
            with open(_events_path(tmp), "w", encoding="utf-8") as out:
                for ev in (events[0], events[1], duplicate):
                    out.write(json.dumps(ev) + "\n")
            try:
                state.open_campaign(CAMPAIGN, base_dir=tmp)
            except state.StateError:
                pass
            else:
                raise AssertionError("commit_id 重复应拒载")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_tmp_residue_cleaned():
    """.tmp 残留（含归档段残留）：加载时清理，不影响结果。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        _commit(handle, key="k-1", changes=_set_block_after([{"id": "pc-1"}]))
        camp = _camp_dir(tmp)
        residues = (state.SNAPSHOT_TMP_NAME, state.EVENTS_NAME + ".tmp",
                    "events.1.ndjson.tmp")
        for name in residues:
            with open(os.path.join(camp, name), "w", encoding="utf-8") as stray:
                stray.write("residue")
        # 归档段本体不是残留：不得被清理
        with open(_archive_path(tmp), "w", encoding="utf-8") as keeper:
            keeper.write("")

        reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
        assert reloaded.view()["seq"] == 1
        assert reloaded.view()["blocks"]["rules"]["party"] == [{"id": "pc-1"}]
        for name in residues:
            assert not os.path.exists(os.path.join(camp, name)), name
        assert os.path.exists(_archive_path(tmp)), "归档段不应被当作 .tmp 清理"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# 快照重建 == 存活态（§14 / §6.2，M7-2）
# ════════════════════════════════════════════════════════════════════════════

def _mixed_changes(index):
    """跨块、多 op 的变更（append / delta），折入次序与次数都敏感。"""
    return [
        {"block": "rules", "path": "/party", "op": "append", "before": None,
         "after": {"id": "pc-%d" % index}},
        {"block": "rules", "path": "/tick", "op": "delta",
         "before": max(index - 1, 0), "after": index},
        {"block": "atlas", "path": "/deltas", "op": "append", "before": None,
         "after": {"x": index}},
    ]


def check_replay_genesis_equals_live():
    """创世 + 全量事件回放 == 存活态（规范化 JSON 逐字节相等，§6.2 不变量）。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():          # 检查点完全滞后：重建只能靠日志
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(6):
                result = _commit(handle, key="k-%d" % index,
                                 changes=_mixed_changes(index))
                assert result["status"] == "committed", result
            live = handle.view()

            rebuilt = handle.replay()
            assert rebuilt["seq"] == live["seq"] == 6
            assert _canon(rebuilt["blocks"]) == _canon(live["blocks"])
            assert rebuilt["world_time"] == live["world_time"]
            assert rebuilt["campaign_id"] == live["campaign_id"]

            reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert _canon(reloaded.view()["blocks"]) == _canon(live["blocks"])
            assert _canon(reloaded.replay()["blocks"]) == _canon(reloaded.view()["blocks"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_replay_only_checkpoint_no_events():
    """只有检查点、没有事件 → 与创世态相等（§14「空检查点」）。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        handle.flush()
        assert _event_lines(tmp) == []
        assert _snapshot_seq(tmp) == 0

        reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
        assert reloaded.view()["seq"] == 0
        assert reloaded.view()["blocks"] == {"rules": None, "atlas": None, "guide": None}
        assert reloaded.view()["world_time"] is None
        rebuilt = reloaded.replay()
        assert rebuilt["seq"] == 0
        assert _canon(rebuilt["blocks"]) == _canon(reloaded.view()["blocks"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_checkpoint_lag_tail_replay():
    """检查点滞后于日志：重载 = 检查点 + 尾部回放，结果 == 存活态（§6.3 / §9）。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        with _NoCheckpoint():
            for index in range(3):
                _commit(handle, key="a-%d" % index,
                        changes=_append_party("pc-%d" % index))
        handle.flush()                                       # 检查点停在 seq 3
        assert _snapshot_seq(tmp) == 3

        with _NoCheckpoint():
            for index in range(3, 6):
                _commit(handle, key="a-%d" % index,
                        changes=_append_party("pc-%d" % index))
        assert _snapshot_seq(tmp) == 3                       # 滞后：盘上仍是 3
        assert len(_event_lines(tmp)) == 6                   # 日志已有 6 行
        live = handle.view()

        reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
        assert reloaded.view()["seq"] == 6
        assert _canon(reloaded.view()["blocks"]) == _canon(live["blocks"])
        assert _canon(reloaded.replay()["blocks"]) == _canon(live["blocks"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_replay_upto_seq():
    """`replay(upto_seq=…)` 只折入 ≤ 该序号的事件；非法入参报错（§12.1）。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(4):
                _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))

            at_two = handle.replay(upto_seq=2)
            assert at_two["seq"] == 2
            assert at_two["blocks"]["rules"]["party"] == [{"id": "pc-0"}, {"id": "pc-1"}]
            genesis = handle.replay(upto_seq=0)
            assert genesis["seq"] == 0
            assert genesis["blocks"] == {"rules": None, "atlas": None, "guide": None}
            try:
                handle.replay(upto_seq="2")
            except state.StateError:
                pass
            else:
                raise AssertionError("非整数 upto_seq 应报错")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# 归档段（§4.1）与检查点调度（§6.3）（M7-2）
# ════════════════════════════════════════════════════════════════════════════

def check_archive_segments_reassemble():
    """超阈值：前缀搬进 events.<n>.ndjson、活动日志去前缀；重放仍从归档拼回（§4.1）。"""
    tmp = tempfile.mkdtemp()
    with _Thresholds(CHECKPOINT_EVERY=10 ** 9, CHECKPOINT_MAX_AGE_S=1e9,
                     CHECKPOINT_MAX_LOG_BYTES=10 ** 18,
                     EVENT_ARCHIVE_THRESHOLD=4):
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(6):
                _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))
            assert len(_event_lines(tmp)) == 6

            handle.flush()                                   # 检查点 + 归档
            assert os.path.exists(_archive_path(tmp)), os.listdir(_camp_dir(tmp))
            assert [ev["seq"] for ev in _lines_at(_archive_path(tmp))] == [1, 2, 3, 4, 5, 6]
            assert _event_lines(tmp) == []                   # 活动日志已去前缀
            assert _snapshot_seq(tmp) == 6
            assert _snapshot_on_disk(tmp)["indexes"]["archived_upto"] == 6

            live = handle.view()
            reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert reloaded.view()["seq"] == 6
            assert _canon(reloaded.view()["blocks"]) == _canon(live["blocks"])
            assert _canon(reloaded.replay()["blocks"]) == _canon(live["blocks"])

            # 归档后新提交照常追加、再次加载不丢
            appended = _commit(reloaded, key="k-6", changes=_append_party("pc-6"))
            assert appended["status"] == "committed", appended
            again = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert again.view()["seq"] == 7
            assert [item["id"] for item in again.view()["blocks"]["rules"]["party"]] == [
                "pc-%d" % index for index in range(7)]
            assert _canon(again.replay()["blocks"]) == _canon(again.view()["blocks"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_archive_overlap_ignored_below_checkpoint():
    """归档段与活动日志重叠、且重叠部分 `seq ≤ 检查点 seq` → 不拒载（§9 唯一允许的重复）。

    写入侧用「整体改名」搬迁（不会产生重叠），所以这里手工造一份重叠档，
    验证加载器对 §9 规定的唯一重复仍然容忍：忽略重叠行、不重复折入。
    """
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        for index in range(3):
            _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))
        handle.flush()                                       # 检查点 seq = 3
        assert _snapshot_seq(tmp) == 3
        live = handle.view()

        # 手工造「搬迁完成、截断未做」式的重叠：活动日志整体复制成归档段。
        with open(_archive_path(tmp), "w", encoding="utf-8") as out:
            for ev in _event_lines(tmp):
                out.write(json.dumps(ev, ensure_ascii=False) + "\n")
        assert [ev["seq"] for ev in _lines_at(_archive_path(tmp))] == [1, 2, 3]
        assert [ev["seq"] for ev in _event_lines(tmp)] == [1, 2, 3]      # 仍在

        reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)           # 不拒载
        assert reloaded.view()["seq"] == 3
        # 只读增量对 §9 允许的重叠段按「已出现过即跳过」处理：不拒载、不重复
        assert [ev["seq"] for ev in reloaded.events_since(0)] == [1, 2, 3]
        assert _canon(reloaded.view()["blocks"]) == _canon(live["blocks"])
        assert [item["id"] for item in reloaded.view()["blocks"]["rules"]["party"]] == [
            "pc-0", "pc-1", "pc-2"]                    # 重叠行只折入一次

        # 重叠档存在时，后续提交照常追加、再次加载不丢也不重复
        result = _commit(reloaded, key="k-3", changes=_append_party("pc-3"))
        assert result["status"] == "committed", result
        again = state.open_campaign(CAMPAIGN, base_dir=tmp)
        assert again.view()["seq"] == 4
        assert [item["id"] for item in again.view()["blocks"]["rules"]["party"]] == [
            "pc-%d" % index for index in range(4)]

        # `replay()` 是**从创世的纯日志折叠**，没有检查点可依据：重叠段在它眼里就是
        # 「重放区间内重复」→ 拒载（加载路径仍按 §9 容忍 `seq ≤ 检查点 seq` 的重叠）。
        try:
            again.replay()
        except state.StateError:
            pass
        else:
            raise AssertionError("重叠档的纯日志折叠应拒载（无检查点可依据）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_replay_range_duplicate_rejected():
    """重放区间（`seq > 检查点 seq`）内出现重复 → 拒载（§9：唯一允许的重复在检查点之下）。

    检查点停在创世时，重叠档整体落进重放区间——同一事实出现两次即日志被外部改坏，
    按 §9 拒载；不自动跳过、不猜内容。
    """
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(3):
                _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))
            assert _snapshot_seq(tmp) == 0                   # 创世检查点：全在重放区间

            with open(_archive_path(tmp), "w", encoding="utf-8") as out:
                for ev in _event_lines(tmp):
                    out.write(json.dumps(ev, ensure_ascii=False) + "\n")
            try:
                state.open_campaign(CAMPAIGN, base_dir=tmp)
            except state.StateError:
                pass
            else:
                raise AssertionError("重放区间内重复（跳号 / 回退）应拒载")

            # `replay()` 是从创世的纯日志折叠，同样拒载（无检查点可依据的重叠）
            try:
                handle.replay()
            except state.StateError:
                pass
            else:
                raise AssertionError("重放区间内重复时 replay() 应拒载")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_archive_rename_failure_keeps_facts():
    """归档改名失败（②）：活动日志原样保留、事实不丢；下次检查点再试即归档成功。"""
    tmp = tempfile.mkdtemp()
    with _Thresholds(CHECKPOINT_EVERY=10 ** 9, CHECKPOINT_MAX_AGE_S=1e9,
                     CHECKPOINT_MAX_LOG_BYTES=10 ** 18,
                     EVENT_ARCHIVE_THRESHOLD=2):
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(3):
                _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))
            live = handle.view()

            real_replace = state.os.replace

            def flaky_replace(src, dst):
                if state.ARCHIVE_RE.match(os.path.basename(dst)):
                    raise OSError("模拟：归档改名失败")
                return real_replace(src, dst)

            state.os.replace = flaky_replace
            try:
                handle.flush()                               # 检查点写成功、归档改名失败
            finally:
                state.os.replace = real_replace

            assert any("改名失败" in note for note in handle.notes), handle.notes
            assert not os.path.exists(_archive_path(tmp))
            assert [ev["seq"] for ev in _event_lines(tmp)] == [1, 2, 3]
            assert _snapshot_seq(tmp) == 3                   # 检查点已覆盖事实

            reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert _canon(reloaded.view()["blocks"]) == _canon(live["blocks"])
            assert _canon(reloaded.replay()["blocks"]) == _canon(live["blocks"])

            # 下一次检查点重试：归档成功，且事实一份不少
            reloaded.flush()
            assert os.path.exists(_archive_path(tmp)), os.listdir(_camp_dir(tmp))
            assert [ev["seq"] for ev in _lines_at(_archive_path(tmp))] == [1, 2, 3]
            assert _event_lines(tmp) == []
            assert _canon(reloaded.replay()["blocks"]) == _canon(reloaded.view()["blocks"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_archive_gap_rejected():
    """归档段与活动日志拼起来跳号 / 归档末行残缺 → 拒载（§9 中间断裂）。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(3):
                _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))
            events = _event_lines(tmp)

            # ① 归档段 [1] + 活动日志 [3] → 缺 seq 2 → 拒载
            with open(_archive_path(tmp), "w", encoding="utf-8") as out:
                out.write(json.dumps(events[0], ensure_ascii=False) + "\n")
            with open(_events_path(tmp), "w", encoding="utf-8") as out:
                out.write(json.dumps(events[2], ensure_ascii=False) + "\n")
            try:
                state.open_campaign(CAMPAIGN, base_dir=tmp)
            except state.StateError:
                pass
            else:
                raise AssertionError("归档段与活动日志跳号应拒载")

            # ② 归档段末行残缺（缺结尾 \n）→ 后面还有事件 → 拒载
            with open(_archive_path(tmp), "w", encoding="utf-8") as out:
                out.write(json.dumps(events[0], ensure_ascii=False) + "\n")
                out.write(json.dumps(events[1], ensure_ascii=False))     # 无 \n
            with open(_events_path(tmp), "w", encoding="utf-8") as out:
                out.write(json.dumps(events[2], ensure_ascii=False) + "\n")
            try:
                state.open_campaign(CAMPAIGN, base_dir=tmp)
            except state.StateError:
                pass
            else:
                raise AssertionError("归档段末行残缺应拒载")

            # ③ 归档段序号不连续（events.1 + events.3 → 缺 2）→ 拒载
            with open(_archive_path(tmp, 1), "w", encoding="utf-8") as out:
                out.write(json.dumps(events[0], ensure_ascii=False) + "\n")
            with open(_archive_path(tmp, 3), "w", encoding="utf-8") as out:
                out.write(json.dumps(events[1], ensure_ascii=False) + "\n")
            with open(_events_path(tmp), "w", encoding="utf-8") as out:
                out.write(json.dumps(events[2], ensure_ascii=False) + "\n")
            try:
                state.open_campaign(CAMPAIGN, base_dir=tmp)
            except state.StateError:
                pass
            else:
                raise AssertionError("归档段序号不连续应拒载")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def _partial_tail_scenario(payload):
    """末行崩（`payload` 为盘上残留的半行字节）：丢弃 + **落盘截除** + 可继续提交。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            _commit(handle, key="k-1", changes=_append_party("pc-1"))
            with open(_events_path(tmp), "ab") as raw:
                raw.write(payload)

            reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert reloaded.view()["seq"] == 1
            with open(_events_path(tmp), "rb") as raw:
                assert raw.read().endswith(b"\n"), "半写末行应从盘上截除"

            result = _commit(reloaded, key="k-2", changes=_append_party("pc-2"))
            assert result["status"] == "committed", result
            assert len(_event_lines(tmp)) == 2

            again = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert again.view()["seq"] == 2
            assert [item["id"] for item in again.view()["blocks"]["rules"]["party"]] == [
                "pc-1", "pc-2"]
            assert _canon(again.replay()["blocks"]) == _canon(again.view()["blocks"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_partial_tail_then_commit_durable():
    """行中崩：截断的半行（无 \\n）**落盘截除**，后续提交可追加且再次加载不丢（§8.3）。"""
    _partial_tail_scenario(b'{"event_id":"camp-01:e000000000002"')


def check_partial_tail_bad_complete_line():
    """行中崩的另一种落盘形态：末行以 \\n 收尾但 JSON 解析失败 → 同样丢弃并截除。"""
    _partial_tail_scenario(b'{"event_id":"camp-01:e000000000002","seq":2\n')


def check_checkpoint_every_n_events():
    """§6.3 事件数触发：每 CHECKPOINT_EVERY 个事件写一次检查点。"""
    tmp = tempfile.mkdtemp()
    with _Thresholds(CHECKPOINT_EVERY=3, CHECKPOINT_MAX_AGE_S=1e9,
                     CHECKPOINT_MAX_LOG_BYTES=10 ** 18):
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert _snapshot_seq(tmp) == 0                   # 创世检查点
            _commit(handle, key="k-1", changes=_append_party("pc-1"))
            _commit(handle, key="k-2", changes=_append_party("pc-2"))
            assert _snapshot_seq(tmp) == 0                   # 未到阈值：检查点滞后
            _commit(handle, key="k-3", changes=_append_party("pc-3"))
            assert _snapshot_seq(tmp) == 3
            assert len(_event_lines(tmp)) == 3
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_checkpoint_age_trigger():
    """§6.3 时间触发：距上次检查点超过 CHECKPOINT_MAX_AGE_S 也写。"""
    tmp = tempfile.mkdtemp()
    saved_now = state._now_ms
    clock = [1_000_000_000_000]
    state._now_ms = lambda: clock[0]
    try:
        with _Thresholds(CHECKPOINT_EVERY=10 ** 9, CHECKPOINT_MAX_AGE_S=5.0,
                         CHECKPOINT_MAX_LOG_BYTES=10 ** 18):
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            _commit(handle, key="k-1", changes=_append_party("pc-1"))
            assert _snapshot_seq(tmp) == 0                   # 同龄 → 不写
            clock[0] += 6_000                                # +6 秒 > 5.0
            _commit(handle, key="k-2", changes=_append_party("pc-2"))
            assert _snapshot_seq(tmp) == 2
    finally:
        state._now_ms = saved_now
        shutil.rmtree(tmp, ignore_errors=True)


def check_checkpoint_log_bytes_trigger():
    """§6.3 日志体积触发：events.ndjson 增长越过 CHECKPOINT_MAX_LOG_BYTES 也写。"""
    tmp = tempfile.mkdtemp()
    with _Thresholds(CHECKPOINT_EVERY=10 ** 9, CHECKPOINT_MAX_AGE_S=1e9,
                     CHECKPOINT_MAX_LOG_BYTES=64):
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            result = _commit(handle, key="k-1", changes=_append_party("pc-1"))
            assert result["status"] == "committed", result
            assert os.path.getsize(_events_path(tmp)) > 64
            assert _snapshot_seq(tmp) == 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_flush_forces_checkpoint():
    """§6.3 优雅退出：`flush()` 强制写检查点把滞后归零；重载与存活态一致。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(3):
                _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))
            assert _snapshot_seq(tmp) == 0                   # 全靠偷懒：滞后
            assert handle.flush() is None
            assert _snapshot_seq(tmp) == 3                   # flush 补齐
            reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert reloaded.view()["seq"] == 3
            assert _canon(reloaded.view()["blocks"]) == _canon(handle.view()["blocks"])
            assert _canon(reloaded.replay()["blocks"]) == _canon(reloaded.view()["blocks"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# 门面态同步与老档迁移（M7-3，§10 / §11）
# ════════════════════════════════════════════════════════════════════════════

def check_stage_state_and_meta_roundtrip():
    """§11 门面态同步：`stage()` 推入的块 / 展示流 / 展示字段随检查点落盘、可重载。

    展示流（`stream`）与不透明块**不进事件溯源**——`stage()` 不写事件、不动事件
    `seq`（§11：两条独立序列，禁止互相赋值），落盘只走检查点。
    """
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        blocks = {"rules": {"party": [{"id": "pc-1"}]}, "atlas": None,
                  "guide": {"salt": "a" * 32}, "room": {"host_member_id": ""}}
        stream = {"log": [{"seq": 1, "text": "旁白"}], "cursor": 1}
        handle.stage(blocks=blocks, stream=stream, save_name="", created=0)
        assert handle.flush() is None
        assert _canon(handle.view()["blocks"]) == _canon(blocks)
        assert handle.view()["stream"] == stream
        assert handle.meta()["save_name"] == "" and handle.meta()["created"] == 0
        # 不走事件：日志为空、事件 seq 仍是 0
        assert _event_lines(tmp) == []
        assert handle.view()["seq"] == 0

        # 未提到的块原样保留（逐块覆盖，不清空别的块）
        handle.stage(blocks={"rules": {"party": []}})
        assert handle.view()["blocks"]["guide"] == {"salt": "a" * 32}
        handle.flush()                    # 把合并后的状态落盘，供下面的重载对照

        reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
        assert _canon(reloaded.view()["blocks"]) == _canon(handle.view()["blocks"])
        assert reloaded.view()["stream"] == stream
        # 空串 save_name / 0 created 是合法值：不得被换成 campaign_id / 当前时间
        assert reloaded.meta()["save_name"] == ""
        assert reloaded.meta()["created"] == 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _write_legacy(path, text):
    with open(path, "w", encoding="utf-8") as out:
        out.write(text)


def check_migrate_legacy_writes_new_layout():
    """§10.2：老档 → 新档文件族（`.bak` 逐字 + 检查点 seq=0 + 空日志 + 迁移事件）。"""
    tmp = tempfile.mkdtemp()
    cid = "s-1"
    try:
        legacy_path = os.path.join(tmp, "%s.json" % cid)
        legacy_raw = ('{"created":1700000000.0,"log":[{"seq":1,'
                      '"kind":"narrative","text":"旧档旁白"}],"save_name":"旧档",'
                      '"seq":1,"sid":"s-1"}')
        _write_legacy(legacy_path, legacy_raw)
        blocks = {"rules": {"party": [{"id": "pc-1"}]}, "atlas": None,
                  "guide": {"salt": "b" * 32}, "room": {"host_member_id": ""}}
        stream = {"log": [{"seq": 1, "kind": "narrative", "text": "旧档旁白"}],
                  "cursor": 1}
        handle = state.migrate_legacy(cid, legacy_path, blocks=blocks, stream=stream,
                                      save_name="旧档", created=1700000000000,
                                      base_dir=tmp)
        camp = _camp_dir(tmp, cid)
        # ② 备份是**原文逐字**（不做任何再序列化）
        with open(os.path.join(camp, state.LEGACY_BAK_NAME), encoding="utf-8") as fh:
            assert fh.read() == legacy_raw, "老档备份必须逐字"
        # ③ 检查点：事件 seq = 0，块与展示流逐字搬（state 不认识块的语义）
        snap = _snapshot_on_disk(tmp, cid)
        assert snap["seq"] == 0 and snap["created"] == 1700000000000, snap
        assert snap["save_name"] == "旧档"
        assert _canon(snap["blocks"]) == _canon(blocks)
        assert _canon(snap["stream"]) == _canon(stream)
        # ④ 空日志 → ⑥ 迁移事件（A0：恰好一条，source=system）
        lines = _event_lines(tmp, cid)
        assert [ev["type"] for ev in lines] == ["system.migration"], lines
        assert lines[0]["source"] == "system" and lines[0]["seq"] == 1
        assert lines[0]["changes"] == [], "迁移事件只作溯源锚点，不改状态"
        # ⑤ 老档已删；句柄状态就是搬过来的那份
        assert not os.path.exists(legacy_path), "迁移成功后老档应被删掉"
        assert _canon(handle.view()["blocks"]) == _canon(blocks)
        assert handle.view()["stream"] == stream
        assert handle.meta()["save_name"] == "旧档"

        # 重开：状态不变（检查点 + 迁移事件都还在）
        reloaded = state.open_campaign(cid, base_dir=tmp)
        assert _canon(reloaded.view()["blocks"]) == _canon(blocks)
        assert reloaded.view()["stream"] == stream
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_migrate_legacy_idempotent():
    """迁移幂等：检查点已在盘上时**不覆盖**新档，只清掉残留老档（§10.2 崩溃窗口）。"""
    tmp = tempfile.mkdtemp()
    cid = "s-2"
    try:
        legacy_path = os.path.join(tmp, "%s.json" % cid)
        _write_legacy(legacy_path, '{"sid":"s-2"}')
        handle = state.migrate_legacy(cid, legacy_path, blocks={"rules": {"v": 1}},
                                      stream={"log": [], "cursor": 0}, base_dir=tmp)
        handle.stage(blocks={"rules": {"v": 2}})
        handle.flush()
        with open(os.path.join(_camp_dir(tmp, cid), state.LEGACY_BAK_NAME),
                  "r", encoding="utf-8") as fh:
            bak = fh.read()

        _write_legacy(legacy_path, '{"sid":"s-2","residual":true}')   # 残留老档
        again = state.migrate_legacy(cid, legacy_path, blocks={"rules": {"v": 99}},
                                     stream={"log": [], "cursor": 0}, base_dir=tmp)
        assert again.view()["blocks"]["rules"] == {"v": 2}, "已落盘的新档不得被覆盖"
        assert not os.path.exists(legacy_path), "残留老档应被清掉"
        with open(os.path.join(_camp_dir(tmp, cid), state.LEGACY_BAK_NAME),
                  "r", encoding="utf-8") as fh:
            assert fh.read() == bak, "已有备份不得被后来的残留老档覆盖"
        assert [ev["type"] for ev in _event_lines(tmp, cid)] == ["system.migration"], \
            "幂等路径不得再记一条迁移事件"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_migrate_legacy_missing_source_rejected():
    """老档读不到 → `StateError`，且**不建检查点**（半成品会挡住重试，必须没有）。"""
    tmp = tempfile.mkdtemp()
    try:
        try:
            state.migrate_legacy(CAMPAIGN, os.path.join(tmp, "nope.json"),
                                 blocks={"rules": {}}, base_dir=tmp)
        except state.StateError as exc:
            assert "老档" in str(exc), exc
        else:
            raise AssertionError("老档读不到时必须抛 StateError")
        assert _snapshot_on_disk(tmp, CAMPAIGN) is None, "失败不得留下检查点"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_migrate_legacy_delete_failure_keeps_new():
    """§10.2 步骤 5：老档删不掉只记说明——新档已可用，**不滚回**。"""
    tmp = tempfile.mkdtemp()
    cid = "s-3"
    try:
        legacy_path = os.path.join(tmp, "%s.json" % cid)
        _write_legacy(legacy_path, '{"sid":"s-3"}')
        real_unlink = state.os.unlink

        def stubborn(path, *args, **kwargs):
            if os.path.abspath(path) == os.path.abspath(legacy_path):
                raise OSError("模拟：删不掉老档")
            return real_unlink(path, *args, **kwargs)

        state.os.unlink = stubborn
        try:
            handle = state.migrate_legacy(cid, legacy_path, blocks={"rules": {}},
                                          base_dir=tmp)
        finally:
            state.os.unlink = real_unlink
        assert os.path.exists(legacy_path), "删不掉就留着（老档是兜底备份）"
        assert any("删除失败" in note for note in handle.notes), handle.notes
        assert _snapshot_on_disk(tmp, cid) is not None, "新档必须已可用"
        assert [ev["type"] for ev in _event_lines(tmp, cid)] == ["system.migration"]
        assert state.open_campaign(cid, base_dir=tmp).view()["seq"] == 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# 其它不变量
# ════════════════════════════════════════════════════════════════════════════

def check_seq_no_gaps():
    """`seq` 单调递增、无空洞，且与已提交事件一一对应。"""
    tmp = tempfile.mkdtemp()
    with _NoCheckpoint():
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(5):
                _commit(handle, key="k-%d" % index, changes=[
                    {"block": "rules", "path": "/n", "op": "append",
                     "before": None, "after": index}])
            seqs = [ev["seq"] for ev in _event_lines(tmp)]
            assert seqs == [1, 2, 3, 4, 5], seqs
            assert handle.view()["seq"] == 5
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_view_is_deep_copy():
    """`view()` 返回深拷贝：改动视图不污染句柄内部。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        _commit(handle, key="k-1", changes=_set_block_after([{"id": "pc-1"}]))
        view = handle.view()
        view["seq"] = 999
        view["blocks"]["rules"]["party"] = "mutated"
        assert handle.view()["seq"] == 1
        assert handle.view()["blocks"]["rules"]["party"] == [{"id": "pc-1"}]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ════════════════════════════════════════════════════════════════════════════
# 只读增量 `events_since`（M7-4，STATE §16）
# ════════════════════════════════════════════════════════════════════════════

def _fingerprints(campaign_dir):
    """目录树指纹：相对路径 → (size, mtime_ns, sha256)——证明「只读不改盘」。"""
    prints = {}
    for base, _dirs, files in os.walk(campaign_dir):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                raw = handle.read()
            prints[os.path.relpath(path, campaign_dir)] = (
                len(raw), os.stat(path).st_mtime_ns,
                hashlib.sha256(raw).hexdigest())
    return prints


def check_events_since_incremental():
    """只回 `seq > since`；严格递增、不重不漏；只读（指纹 / 内存 seq 不变）。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        for index in range(3):
            _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))
        handle.flush()
        before = _fingerprints(_camp_dir(tmp))
        live_seq = handle.view()["seq"]

        assert [ev["seq"] for ev in handle.events_since(0)] == [1, 2, 3]
        assert [ev["seq"] for ev in handle.events_since(1)] == [2, 3]
        assert [ev["seq"] for ev in handle.events_since(2)] == [3]
        assert handle.events_since(3) == [] and handle.events_since(99) == []
        # 事件封套原样透出（与日志里的行逐字一致），不是重造的壳
        assert handle.events_since(0)[0] == _event_lines(tmp)[0]
        seqs = [ev["seq"] for ev in handle.events_since(0)]
        assert seqs == sorted(set(seqs)) == list(range(1, len(seqs) + 1))

        # 只读：不改盘（size + mtime + sha256）、不触发检查点、内存态 seq 不变
        assert _fingerprints(_camp_dir(tmp)) == before
        assert handle.view()["seq"] == live_seq

        # 非法 since → StateError（与 replay(upto_seq=…) 同口径）
        for bad in (-1, "1", None, 1.0, True):
            try:
                handle.events_since(bad)
            except state.StateError:
                pass
            else:
                raise AssertionError("非法 since 应抛 StateError：%r" % (bad,))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_events_since_across_archives():
    """跨归档段：`events.<n>.ndjson` + 活动日志拼接后仍不重不漏（§4.1 / §9）。"""
    tmp = tempfile.mkdtemp()
    with _Thresholds(CHECKPOINT_EVERY=10 ** 9, CHECKPOINT_MAX_AGE_S=1e9,
                     CHECKPOINT_MAX_LOG_BYTES=10 ** 18,
                     EVENT_ARCHIVE_THRESHOLD=4):
        try:
            handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
            for index in range(6):
                _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))
            handle.flush()                               # 归档段 1：seq 1..6
            assert [ev["seq"] for ev in _lines_at(_archive_path(tmp))] == [1, 2, 3, 4, 5, 6]
            assert _event_lines(tmp) == []
            for index in range(6, 9):                    # 活动日志：seq 7..9
                _commit(handle, key="k-%d" % index, changes=_append_party("pc-%d" % index))
            before = _fingerprints(_camp_dir(tmp))

            assert [ev["seq"] for ev in handle.events_since(0)] == list(range(1, 10))
            assert [ev["seq"] for ev in handle.events_since(5)] == [6, 7, 8, 9]
            assert [ev["seq"] for ev in handle.events_since(9)] == []

            # 重新加载出的句柄给出同一份增量（不依赖内存里的任何缓存）
            reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
            assert [ev["seq"] for ev in reloaded.events_since(5)] == [6, 7, 8, 9]
            assert _canon(reloaded.events_since(0)) == _canon(handle.events_since(0))
            assert _fingerprints(_camp_dir(tmp)) == before
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def check_r7_requires_idempotency_key():
    """R7：非系统提交缺幂等键 → rejected（不写事件）。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        result = _commit(handle, key=None, source="player",
                         changes=_set_block_after([{"id": "pc-1"}]))
        assert result["status"] == "rejected", result
        assert result["error"]["code"] == "缺少幂等键"
        assert len(_event_lines(tmp)) == 0
        # 系统提交允许无键
        systematic = _commit(handle, key=None, source="system",
                             type="clock.advance",
                             changes=[{"block": "rules", "path": "/tick",
                                       "op": "set", "before": None, "after": 1}])
        assert systematic["status"] == "committed", systematic
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


CHECKS = (
    check_no_business_imports,
    check_import_no_disk,
    check_commit_signature_a0,
    check_validate_event,
    check_idempotent_replay,
    check_idempotent_conflict,
    check_idempotent_expiry,
    check_a0_one_event_multi_changes,
    check_log_commit_id_unique,
    check_crash_half_line_truncated,
    check_crash_half_line_no_newline,
    check_crash_append_complete_no_checkpoint,
    check_checkpoint_bak_fallback,
    check_checkpoint_both_corrupt_genesis,
    check_midlog_break_rejected,
    check_tmp_residue_cleaned,
    check_replay_genesis_equals_live,
    check_replay_only_checkpoint_no_events,
    check_checkpoint_lag_tail_replay,
    check_replay_upto_seq,
    check_archive_segments_reassemble,
    check_archive_overlap_ignored_below_checkpoint,
    check_replay_range_duplicate_rejected,
    check_archive_rename_failure_keeps_facts,
    check_archive_gap_rejected,
    check_partial_tail_then_commit_durable,
    check_partial_tail_bad_complete_line,
    check_checkpoint_every_n_events,
    check_checkpoint_age_trigger,
    check_checkpoint_log_bytes_trigger,
    check_flush_forces_checkpoint,
    check_seq_no_gaps,
    check_view_is_deep_copy,
    check_events_since_incremental,
    check_events_since_across_archives,
    check_r7_requires_idempotency_key,
    check_stage_state_and_meta_roundtrip,
    check_migrate_legacy_writes_new_layout,
    check_migrate_legacy_idempotent,
    check_migrate_legacy_missing_source_rejected,
    check_migrate_legacy_delete_failure_keeps_new,
)


def main():
    failures = 0
    for check in CHECKS:
        try:
            check()
        except AssertionError as error:
            print("  断言失败: %s — %s" % (check.__name__, error))
            failures += 1
        except Exception:  # 意外异常也算失败，但留全栈
            print("  异常: %s" % check.__name__)
            traceback.print_exc()
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(CHECKS) - failures, len(CHECKS), failures))
        return 1
    print("%d/%d 项通过" % (len(CHECKS), len(CHECKS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
