#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：notdnd_web 的规则会话接线（规则快照落盘 + 惰性迁移 + /api/session）。

断言都是**真跑**出来的，不是对着实现抄一遍：

  1. 快照往返：prism_core.RuleSession 的快照进存档 → 原子落盘 → 重新载入
     → 逐字段一致；并与规则层的 `from_snapshot() → snapshot()` 契约对照（幂等）。
  2. 老档惰性迁移：缺 `rules` 的旧存档补成**空规则会话**，已有字段不被覆盖，
     磁盘上的老档不被就地改写，且两个老档拿到的**不是同一个可变对象**。
  3. GET /api/session：真起服务（随机端口 + 临时存档目录，退出时必收摊），
     经 HTTP 拉取规则视图（party / combat / pressure 等），且该请求不改写存档；
     未开会话仍按既有 400 语义。

另有一条护栏：`import notdnd_web` 不碰磁盘（存档目录不被创建）。

零依赖：仅 Python 3 标准库。直接 `python3 tests/test_notdnd_web.py` 运行。
"""

import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⚠️ 顺序要求：NOTDND_SAVE 必须在 import notdnd_web **之前**设好——SAVE_DIR 是
# import 期常量。测试用自己的临时目录，绝不碰仓库里真实的 web-saves/。
_TMP = tempfile.mkdtemp(prefix="notdnd-web-test-")
os.environ["NOTDND_SAVE"] = _TMP

sys.path.insert(0, ROOT)                     # 让 tests/ 直接跑时也能 import 根模块
import notdnd_web   # noqa: E402
import prism_core   # noqa: E402


# --------------------------------------------------------------------------
# 脚手架：临时存档目录 / 真服务
# --------------------------------------------------------------------------


def _free_port() -> int:
    """要一个当前空闲的端口：绑 0 让内核分配，读到号就释放。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _save_path(sid: str) -> str:
    return os.path.join(notdnd_web.SAVE_DIR, "%s.json" % sid)


def _write_save(path: str, payload: dict) -> None:
    """把一份存档 JSON 写到指定目录（模拟已有存档 / 老档）。"""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _load_json(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {}


class _Server:
    """真起一个 notdnd_web 服务：子进程 + 随机端口 + 自己的临时存档目录。

    子进程读不到测试进程之后再设的环境变量，所以存档目录在**构造时**注入；
    它也是「必收摊」的责任人——退出时关停进程并删掉临时目录。

    服务端把 save_dir 随 /api/saves 回传（见 notdnd_web.py 的注释），
    测试因此能顺带验证「问服务端才知道存档写在哪」这条通路。
    """

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="notdnd-web-srv-")
        self.port = _free_port()
        self.log = os.path.join(self.dir, "server.log")
        self.proc = None
        self._log_handle = None

    def __enter__(self):
        env = dict(os.environ)
        env.update(NOTDND_SAVE=self.dir, NOTDND_HOST="127.0.0.1",
                   NOTDND_PORT=str(self.port), PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
        # 服务端输出落文件：管道写满会死锁，事后读文件还能拿到崩溃证据。
        self._log_handle = open(self.log, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "notdnd_web.py")],
            cwd=ROOT, env=env, stdout=self._log_handle,
            stderr=subprocess.STDOUT)
        self._wait_ready()
        return self

    def __exit__(self, *_exc):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self._log_handle is not None:
            self._log_handle.close()
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def _wait_ready(self):
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("服务进程提前退出（exit=%s）：\n%s"
                                     % (self.proc.returncode, self.tail()))
            try:
                status, _ = self.get("/api/saves")
            except OSError:
                time.sleep(0.1)
                continue
            if status == 200:
                return
            time.sleep(0.1)
        raise AssertionError("服务未在 30 秒内就绪：\n%s" % self.tail())

    def tail(self) -> str:
        try:
            with open(self.log, encoding="utf-8") as handle:
                return handle.read()[-4000:]
        except OSError:
            return "（无服务端日志）"

    def get(self, path: str, sid: str = ""):
        """发一次 GET，返回 (状态码, 解析后的 JSON)。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"X-Session": sid} if sid else {}
        try:
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            body = response.read()
            return response.status, _load_json(body)
        finally:
            conn.close()


def _sample_rules(sid: str) -> dict:
    """一份有内容的规则会话快照：覆盖 party / scene / pressure / log 等字段。"""
    rules = prism_core.RuleSession(sid)
    unit = prism_core.new_unit("u-1", "试炼者", attributes={"MGT": 6, "INS": 5})
    unit["vitality"] = 9
    unit["max_vitality"] = 12
    rules.party.append(unit)
    rules.scene = {"id": "sc-1",
                   "actions": [{"id": "a-1", "label": "勘察", "kind": "check", "df": 12}]}
    rules.active_unit_id = "u-1"
    rules.pressure = 2
    rules.done_actions["a-0"] = {"unit": "试炼者", "text": "已勘察", "reward": []}
    rules.action_fails["a-1"] = 1
    rules.add_log("check", "勘察 · 判定 → 成功")
    return rules.snapshot()


# --------------------------------------------------------------------------
# 断言
# --------------------------------------------------------------------------


def check_import_has_no_side_effects():
    """`import notdnd_web` 不碰磁盘：存档目录不被创建。"""
    probe = os.path.join(_TMP, "import-probe", "saves")
    env = dict(os.environ)
    env["NOTDND_SAVE"] = probe
    result = subprocess.run([sys.executable, "-c", "import notdnd_web"],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, "import notdnd_web 失败：%s" % result.stderr
    assert not os.path.exists(probe), "import notdnd_web 不得创建存档目录"


def check_roundtrip_snapshot():
    """建规则会话 → 落盘 → 重新载入 → 快照一致（含磁盘上那份字节）。"""
    sid = "roundtrip-1"
    snapshot = _sample_rules(sid)

    before = notdnd_web.Session(sid)
    before.rules = snapshot
    before.add_log("narrative", "开场旁白")
    before.save()

    with open(_save_path(sid), encoding="utf-8") as handle:
        on_disk = json.load(handle)
    assert on_disk["rules"] == snapshot, "规则快照必须真的落盘"
    assert on_disk["rules"]["party"], "落盘的快照不能是空壳"

    after = notdnd_web.Session.load(sid)      # 绕过内存缓存，走真实读盘路径
    assert after is not None, "落盘的存档必须能载入"
    assert after.rules == snapshot, "载入后的规则快照必须与写入前一致"
    assert after.seq == 1 and after.log[0]["text"] == "开场旁白", "工程字段不受影响"

    # 与规则层的契约对照：存档层不做二次清洗，形状完全由 prism_core 定义。
    assert prism_core.RuleSession.from_snapshot(snapshot).snapshot() == snapshot, \
        "prism_core 的快照往返必须幂等"


def check_legacy_save_lazy_migration():
    """老档（无 rules）惰性补空规则会话：只补不覆盖，也不就地改写磁盘。"""
    legacy = {
        "sid": "legacy-1",
        "created": 1700000000.0,
        "log": [{"seq": 1, "kind": "narrative", "text": "旧档旁白",
                 "speaker": "", "ts": 1700000000000}],
        "seq": 1,
        "save_name": "旧档",
    }
    _write_save(_save_path("legacy-1"), legacy)
    _write_save(_save_path("legacy-2"), {"sid": "legacy-2"})

    first = notdnd_web.Session.load("legacy-1")
    second = notdnd_web.Session.load("legacy-2")
    assert first is not None and second is not None, "老档必须能载入"
    assert first.save_name == "旧档", "已有字段不得被默认值覆盖"
    assert first.log and first.log[0]["text"] == "旧档旁白"
    assert first.rules == prism_core.RuleSession("legacy-1").snapshot(), \
        "缺 rules 的老档应补成空规则会话（sid 与存档对齐）"
    assert first.rules["party"] == [] and first.rules["pressure"] == 0
    assert first.rules["combat"] is None

    # 串档防线：两个老档拿到的不能是同一个可变对象（默认值必须是工厂）。
    first.rules["pressure"] = 9
    assert second.rules["pressure"] == 0, "不同存档不得共享同一份默认规则会话"

    # 「只补不覆盖」的另一半：迁移只发生在内存，磁盘上的老档保持原样。
    with open(_save_path("legacy-1"), encoding="utf-8") as handle:
        assert json.load(handle) == legacy, "惰性迁移不得就地改写磁盘上的老档"


def check_api_session_view():
    """GET /api/session：真起服务，经 HTTP 拉规则视图（正常路径 + 老档）。"""
    sid = "api-1"
    snapshot = _sample_rules(sid)
    with _Server() as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        save_dir = info["save_dir"]
        assert save_dir and os.path.isdir(save_dir), "服务端应回传可用的 save_dir"

        # 造一局：用存档层的白名单序列化写文件（本期没有写接口，
        # /api/action 等留待数据与数值到位后再开）。
        session = notdnd_web.Session(sid)
        session.rules = snapshot
        session.save_name = "试炼局"
        session.add_log("narrative", "开场旁白")
        path = os.path.join(save_dir, "%s.json" % sid)
        _write_save(path, session.to_dict())
        with open(path, encoding="utf-8") as handle:
            before_bytes = handle.read()

        status, view = server.get("/api/session", sid=sid)
        assert status == 200, "已开会话应 200，实际 %s / %s" % (status, view)
        assert view["sid"] == sid, "规则视图的 sid 应与存档 sid 对齐"
        assert view["party"][0]["id"] == "u-1", "party 应来自规则快照"
        assert view["party"][0]["name"] == "试炼者"
        assert view["scene"]["id"] == "sc-1"
        assert view["active_unit_id"] == "u-1"
        assert view["pressure"] == 2
        assert view["combat"] is None
        assert view["done_actions"]["a-0"]["text"] == "已勘察"
        assert view["action_fails"]["a-1"] == 1
        assert view["save"]["name"] == "试炼局" and view["save"]["seq"] == 1

        with open(path, encoding="utf-8") as handle:
            assert handle.read() == before_bytes, "只读接口不得改写存档"

        # 老档（无 rules）同样能被拉取：迁移发生在载入路径上。
        _write_save(os.path.join(save_dir, "api-legacy.json"),
                    {"sid": "api-legacy", "created": 1.0, "log": [], "seq": 0})
        status, legacy_view = server.get("/api/session", sid="api-legacy")
        assert status == 200, "老档应能拉取，实际 %s / %s" % (status, legacy_view)
        assert legacy_view["sid"] == "api-legacy"
        assert legacy_view["party"] == [] and legacy_view["pressure"] == 0
        assert legacy_view["save"]["named"] is False and legacy_view["save"]["name"]


def check_api_session_without_session_400():
    """未开会话：无 X-Session 头 / sid 不存在 → 既有 400 语义。"""
    with _Server() as server:
        status, body = server.get("/api/session")
        assert status == 400, "无会话应 400，实际 %s / %s" % (status, body)
        assert body.get("error"), "400 应带 error 文案：%s" % body

        status, body = server.get("/api/session", sid="never-opened")
        assert status == 400, "没开过的 sid 应 400，实际 %s / %s" % (status, body)
        assert body.get("error")

        # 对照组：同一台服务上 /api/log 的 400 语义一致，说明这不是端点专属行为。
        status, _ = server.get("/api/log")
        assert status == 400, "/api/log 同样应 400，实际 %s" % status


CHECKS = (
    check_import_has_no_side_effects,
    check_roundtrip_snapshot,
    check_legacy_save_lazy_migration,
    check_api_session_view,
    check_api_session_without_session_400,
)


def main():
    failures = 0
    for check in CHECKS:
        try:
            check()
        except Exception as error:  # noqa: BLE001 — 一条断言崩了，后面的检查仍要跑
            print("  断言失败: %s: %s" % (type(error).__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(CHECKS) - failures, len(CHECKS), failures))
        return 1
    print("%d/%d 项通过" % (len(CHECKS), len(CHECKS)))
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)     # 收摊：临时存档目录
    sys.exit(code)
