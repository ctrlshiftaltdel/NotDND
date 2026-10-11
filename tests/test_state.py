#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""状态层内核回归测试（M7-1，Issue #163）。

对照 STATE-DESIGN.md §14 的用例，覆盖：

- **幂等**组：同键同载荷 → `replayed`（事件数不变、副作用一次）；同键不同载荷
  → `conflict`（不写事件、错误码固定）；键过期 → 视为新提交。
- **提交原子性（A0）**组：`commit()` 无多事件入口；一次提交只写一行、
  `seq_from == seq_to`、`len(event_ids) == 1`；一次提交内多项 `changes`
  （跨块、含 set/unset/delta/append）整体生效；行中崩（半行）整体不生效；
  重放区间内 `commit_id` 唯一。
- **崩溃恢复**组：追加中途崩（半行）丢弃末行；追加完成、检查点未写 → 重启重放 +
  幂等索引由日志重建（§7.4）；检查点损坏 + `.bak` 有效 → 回退；两者都坏 → 退创世态；
  日志中间断裂 → 拒载；`.tmp` 残留 → 清理。
- **结构**断言：`import state` 不触碰磁盘；源码无业务模块 import；`seq` 无空洞。

零依赖：仅 Python 3 标准库。临时存档目录一律落 `tempfile.mkdtemp()`，退出前收摊。
时钟与检查点时机用**进程内注入**（`state._now_ms` / 模块常量）控制，不靠等待。
"""

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


class _NoCheckpoint:
    """临时关掉自动检查点，精确控制「检查点未写」的崩溃场景。"""

    def __enter__(self):
        self._every = state.CHECKPOINT_EVERY
        self._age = state.CHECKPOINT_MAX_AGE_S
        state.CHECKPOINT_EVERY = 10 ** 9
        state.CHECKPOINT_MAX_AGE_S = 1e9

    def __exit__(self, *exc):
        state.CHECKPOINT_EVERY = self._every
        state.CHECKPOINT_MAX_AGE_S = self._age
        return False


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
    """.tmp 残留：加载时清理，不影响结果。"""
    tmp = tempfile.mkdtemp()
    try:
        handle = state.open_campaign(CAMPAIGN, base_dir=tmp)
        _commit(handle, key="k-1", changes=_set_block_after([{"id": "pc-1"}]))
        camp = _camp_dir(tmp)
        for name in (state.SNAPSHOT_TMP_NAME, state.EVENTS_NAME + ".tmp"):
            with open(os.path.join(camp, name), "w", encoding="utf-8") as stray:
                stray.write("residue")

        reloaded = state.open_campaign(CAMPAIGN, base_dir=tmp)
        assert reloaded.view()["seq"] == 1
        assert reloaded.view()["blocks"]["rules"]["party"] == [{"id": "pc-1"}]
        for name in (state.SNAPSHOT_TMP_NAME, state.EVENTS_NAME + ".tmp"):
            assert not os.path.exists(os.path.join(camp, name)), name
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
    check_seq_no_gaps,
    check_view_is_deep_copy,
    check_r7_requires_idempotency_key,
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
