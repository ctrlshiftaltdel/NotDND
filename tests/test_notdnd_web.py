#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：notdnd_web 的规则会话接线与导引路由。

断言都是**真跑**出来的，不是对着实现抄一遍：

  1. 快照往返：prism_core.RuleSession 的快照进存档 → 原子落盘 → 重新载入
     → 逐字段一致；并与规则层的 `from_snapshot() → snapshot()` 契约对照（幂等）。
  2. 老档惰性迁移：缺 `rules` 的旧存档补成**空规则会话**，已有字段不被覆盖，
     磁盘上的老档不被就地改写，且两个老档拿到的**不是同一个可变对象**。
  3. GET /api/session：真起服务（随机端口 + 临时存档目录，退出时必收摊），
     经 HTTP 拉取规则视图（party / combat / pressure 等），且该请求不改写存档；
     未开会话仍按既有 400 语义。
  4. ATLAS 存档块（切片 I4，§3.4）：带块落盘 → 载入还原，位置还在；
     老档没有块 → 按当前世界懒编译不报错；世界删掉了队伍所在地点 →
     搬迁到仍存在的区域且日志有说明；HTTP 出口列表是文本，移动返回行程档。
  5. 导引者（G2）：`guide` 块四处接线与加载合同；`GET /api/guide/status`；
     `POST /api/guide/turn` 的长度 / 会话 / 速率 / 淡出整句 / 结算 / 分块流 /
     头写出之后的兜底。要换传输层的用例走**本进程**的服务线程（见 _LocalServer），
     其余仍用真子进程，保证启动路径也被覆盖。
  6. 导引者朗读（G3）：`turn` 切出的节拍写进 `last_beats`；`POST /api/guide/speak`
     的长度 / 会话 / 速率 / 语音可用 / 全文匹配，以及 HTTP/1.1 分块音频流
     （24 kHz + pcm16 头、无 Content-Length、每个音频 delta 单独 base64 解码后拼接）。
  7. 导引者工具环（G4）：`查规则` 那三句整句才开预通行；预通行是**唯一**一个
     思考开且 `stream` 为 false 的调用，叙事请求保持思考关并重拼 L0–L4（无
     `role: tool` / 无 `reasoning_content`）；工具里的 `ValueError` 只变成固定
     短语，不产生第二行 HTTP 状态；叙事带 `tool_calls` 不再开一轮。
  8. 导引者绑定与焦点（G5）：`POST /api/guide/bind` 的四种结果（400「没有这场
     战役」/ 400「世界对不上」/ 200 幂等 / 409「已经绑定」）都基于真剧本文件；
     绑定后叙事请求的第 2 条消息换成这场剧本的典范卡（含专名、无 salt / `docs/`），
     同一剧本的两份存档 L1 全等；`turn` 的可选 `location_id` 在状态行之前校验
     （非法 → 400「没有这个地点」且不开 SSE），合法时写入 `focus_location_id`
     并落盘；带 `location_id` 的回合在状态行之后、叙事之前调用一次
     `ensure_realization`（G6 起它会先发一次**非流式**实相请求，所以这一回合的上游
     不止叙事那一次；实相请求体与占位语义由 `tests/test_prism_guide.py` 自己测）。
  9. 导引者行为与对白（G7 接线）：焦点变化的回合先发**一次**场外节拍请求
     （非流式 + 思考关 + 无工具，user 只带 id / 名字 / drive / mask / lever / tell，
     **不含秘密全文**），再发叙事；玩家的整段叙事进 L3 与切拍之前先过秘密门
     （L3 里既没有秘密全文、也没有连续 8 码位）；`narration` 至多带一条 `trace`
     （痕迹揭开随同一次 `save()` 落盘）；`npc_flags` 为 `betrayed` 时痕迹用典范
     原文且**不发**场外请求；离线时不留痕、回合照常；`rules.scene.id` 变化时
     为**当前焦点**记一轮。

另有一条护栏：`import notdnd_web` 不碰磁盘（存档目录不被创建）。

零依赖：仅 Python 3 标准库。直接 `python3 tests/test_notdnd_web.py` 运行。
"""

import base64
import contextlib
import http.client
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⚠️ 顺序要求：NOTDND_SAVE 必须在 import notdnd_web **之前**设好——SAVE_DIR 是
# import 期常量。测试用自己的临时目录，绝不碰仓库里真实的 web-saves/。
_TMP = tempfile.mkdtemp(prefix="notdnd-web-test-")
os.environ["NOTDND_SAVE"] = _TMP

sys.path.insert(0, ROOT)                     # 让 tests/ 直接跑时也能 import 根模块
import notdnd_web   # noqa: E402
import prism_core   # noqa: E402
import prism_guide  # noqa: E402

_SALT_RE = re.compile(r"^[0-9a-f]{32}$")

# 一段合格的模型叙事：三段标题齐全，记法与「合计」都对得上结算结果。
_OK_NARRATION = ("【裁决】你把手按上门栓，锁簧弹开。\n"
                 "【叙事】铁屑落在脚边，走廊里安静了一拍。\n"
                 "【钩子】走廊尽头有脚步声。")


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
        # start_new_session：让服务自成一个会话 / 进程组，收摊时按进程组杀，
        # 连带它可能拉起的子进程一起收干净（否则 kill 直接子进程会留下一串孤儿）。
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "notdnd_web.py")],
            cwd=ROOT, env=env, stdout=self._log_handle,
            stderr=subprocess.STDOUT, start_new_session=True)
        # ⚠️ 起不来 / 提前退出时也必须收摊：`with` 只在 __enter__ 成功后才进
        # 正文，若这里直接抛出去，__exit__ 根本不会被调用，进程就泄漏了。
        try:
            self._wait_ready()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_exc):
        if self.proc is not None:
            # 按进程组收（负 PID = 进程组），失败再退回直接杀进程；无论成功
            # 与否都继续往下走到删临时目录，保证失败路径也收得干净。
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError,
                    AttributeError):
                # Windows 没有 killpg / getpgid（AttributeError）→ 退回杀本进程。
                self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except (OSError, AttributeError):
                    self.proc.kill()
                self.proc.wait(timeout=10)
            self.proc = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
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

    def post(self, path: str, payload: dict, sid: str = ""):
        """发一次 POST，返回 (状态码, 解析后的 JSON)。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if sid:
            headers["X-Session"] = sid
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, _load_json(response.read())
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
# 导引者（G2）脚手架：本进程服务线程 / 假传输 / SSE 解析
# --------------------------------------------------------------------------


class _LocalServer:
    """在**本进程**里起一台服务线程（随机端口）。

    子进程服务（`_Server`）读不到测试进程后来打的补丁——凡是要替换
    `prism_guide.TRANSMIT`（假传输）或改模块全局的用例都走这一台；
    其余用例仍用真子进程，保证「从命令行启动」这条路径也被覆盖。
    """

    def __init__(self):
        self.server = None
        self.thread = None
        self.port = 0

    def __enter__(self):
        self.server = notdnd_web.ThreadingHTTPServer(
            ("127.0.0.1", 0), notdnd_web.Handler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False

    def get(self, path: str, sid: str = ""):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {"X-Session": sid} if sid else {}
        try:
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            return response.status, _load_json(response.read())
        finally:
            conn.close()

    def post(self, path: str, body: dict, sid: str = ""):
        """POST 一次，返回 (状态码, JSON, **已解块**的响应体, 响应头字典)。

        `http.client` 会自己把分块体还原，所以这里拿到的 `raw` 直接交给
        `_parse_sse`；只有裸 socket 的 `_raw_http` 才需要 `_dechunk`。
        """
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        headers = {"Content-Type": "application/json"}
        if sid:
            headers["X-Session"] = sid
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            conn.request("POST", path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            heads = {key.lower(): value for key, value in response.getheaders()}
            return response.status, _load_json(raw), raw, heads
        finally:
            conn.close()


def _dechunk(raw: bytes) -> bytes:
    """把 HTTP/1.1 分块体还原（十六进制长度行 + CRLF + 数据）。"""
    _head, sep, rest = raw.partition(b"\r\n\r\n")
    if not sep:
        return b""
    out = []
    while True:
        line, sep, rest = rest.partition(b"\r\n")
        if not sep:
            break
        try:
            size = int(line.split(b";")[0].strip() or b"0", 16)
        except ValueError:
            break
        if size == 0:
            break
        out.append(rest[:size])
        rest = rest[size + 2:]
    return b"".join(out)


def _raw_http(port: int, path: str, body: dict, sid: str = "") -> bytes:
    """用裸 socket 发一次 POST，读到连接关闭——用来数有几行 HTTP 状态。"""
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    lines = ["POST %s HTTP/1.1" % path, "Host: 127.0.0.1",
             "Content-Type: application/json",
             "Content-Length: %d" % len(payload)]
    if sid:
        lines.append("X-Session: %s" % sid)
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + payload
    with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
        sock.sendall(request)
        pieces = []
        while True:
            piece = sock.recv(65536)
            if not piece:
                break
            pieces.append(piece)
        return b"".join(pieces)


def _status_lines(raw: bytes) -> int:
    """原始响应里以 `HTTP/` 开头的行数——出现第二行就是「第二套 HTTP 状态」。"""
    text = raw.decode("utf-8", "replace")
    return sum(1 for line in text.splitlines() if line.startswith("HTTP/"))


def _parse_sse(raw: bytes):
    """解析 SSE 体，返回 [(event, payload), …]（按出现顺序）。

    逐行扫、只在 `event:` 之后取紧跟的 `data:`——这样即使分块边界把 `\\r\\n`
    夹在事件之间也不会把行首认错。
    """
    events = []
    event = None
    for line in raw.decode("utf-8").replace("\r", "").split("\n"):
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:") and event is not None:
            data = line[len("data:"):].strip()
            events.append((event, json.loads(data) if data else None))
            event = None
    return events


class _FakeTransport:
    """假传输：记录每次请求体，按脚本返回或抛错（默认返回一段合格叙事）。"""

    def __init__(self, script=None):
        self.calls = []
        self.script = list(script or [])

    def __call__(self, url, payload, headers, *, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers,
                           "timeout": timeout})
        step = self.script.pop(0) if self.script else {
            "content": _OK_NARRATION, "tool_calls": [],
            "usage": {"prompt_tokens": 7, "cached_tokens": 6,
                      "completion_tokens": 3, "reasoning_tokens": 0}}
        if isinstance(step, Exception):
            raise step
        return dict(step)


@contextlib.contextmanager
def _guide_online(env: dict, transmit):
    """把 prism_guide 的环境解析与传输层换成测试替身（只在**本进程**生效）。

    只打补丁、不写 `os.environ`：否则真子进程服务会继承到「已配置」的
    环境变量并真的去连上游——测试绝不能出网。
    """
    saved_env = prism_guide.load_env
    saved_tx = prism_guide.TRANSMIT
    prism_guide.load_env = lambda path=".env": dict(env)
    prism_guide.TRANSMIT = transmit
    try:
        yield transmit
    finally:
        prism_guide.load_env = saved_env
        prism_guide.TRANSMIT = saved_tx


_ONLINE_ENV = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
               "API_KEY": "secret123"}
_OFFLINE_ENV = {"BASE_URL": "", "MODEL": "mm", "API_KEY": "secret123"}


def _set_last_beats(sid: str, beats: list) -> None:
    """把 `last_beats` 写进磁盘上的存档（服务端会自己载入；绕开内存缓存）。"""
    session = notdnd_web.Session.load(sid)
    session.guide["last_beats"] = beats
    session.save()
    notdnd_web._sessions.pop(sid, None)


def _set_guide(sid: str, patch: dict) -> None:
    """按 `patch` 改磁盘上 `guide` 块的若干键（服务端随后自己载入）。"""
    session = notdnd_web.Session.load(sid)
    session.guide.update(patch)
    session.save()
    notdnd_web._sessions.pop(sid, None)


def _set_scene(sid: str, scene_id: str) -> None:
    """改磁盘上规则快照的 `scene.id`（§2.2：原检查点仍有效，测试可直接改快照）。"""
    session = notdnd_web.Session.load(sid)
    scene = dict(session.rules.get("scene") or {})
    scene["id"] = scene_id
    session.rules["scene"] = scene
    session.save()
    notdnd_web._sessions.pop(sid, None)


# 非流式 + 思考关且**无工具**：只有场外节拍请求长这样（叙事是 stream=true；
# 实相与工具预通行都是 `thinking` 为 enabled）。
def _is_trace_call(call) -> bool:
    payload = call["payload"]
    return (payload.get("stream") is False
            and payload.get("thinking") == {"type": "disabled"})


_BAD_JSON = {"content": "这不是 JSON", "tool_calls": [], "usage": {}}


def _sse_reply(text: str) -> dict:
    return {"content": text, "tool_calls": [], "usage": {"prompt_tokens": 1}}


def _tts_transport(chunks: list):
    """假 TTS 传输：回**真实形状**的 SSE 体，交给 prism_guide 组装。

    与叙事共用 `TRANSMIT` 这一个入口，所以「传输未被调用」数的是同一个计数器。
    每个 delta 的 base64 自带填充——组装要能把它们**分别**解码再拼接。
    """
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers,
                      "timeout": timeout})
        lines = []
        for chunk in chunks:
            data = base64.b64encode(chunk).decode("ascii")
            lines.append('data: {"choices":[{"delta":{"audio":{"data":"%s"}}}]}'
                         % data)
            lines.append("")
        lines.append('data: {"choices":[],"usage":{"prompt_tokens":4,'
                     '"completion_tokens":0}}')
        lines.append("")
        lines.append("data: [DONE]")
        lines.append("")
        return prism_guide.assemble_chat_stream("\n".join(lines).encode("utf-8"))

    fake.calls = calls
    return fake


def _guide_session(sid: str, *, auto_pass: bool = True,
                   combat: bool = False) -> "notdnd_web.Session":
    """造一局带一条行动的存档，写到磁盘（服务端会自己载入）。"""
    session = notdnd_web.Session(sid)
    rules = prism_core.RuleSession(sid)
    rules.party.append(prism_core.new_unit("u-1", "试炼者",
                                           attributes={"MGT": 6, "INS": 7}))
    action = {"id": "a-lock", "label": "撬锁", "kind": "check", "df": 12,
              "on_pass": "锁簧弹开。"}
    if auto_pass:
        action["auto_pass"] = True
    rules.scene = {"id": "sc-1", "actions": [action]}
    if combat:
        rules.combat = {"over": False}
    session.rules = rules.snapshot()
    session.save()
    notdnd_web._sessions.pop(sid, None)     # 丢掉缓存，服务端走真实读盘
    return session



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


# --------------------------------------------------------------------------
# ATLAS 存档块（切片 I4，§3.4）
# --------------------------------------------------------------------------


def _atlas_log_texts(session) -> list:
    """会话日志里 kind=atlas 的条目文本（搬迁 / 懒编译说明走这条通道）。"""
    return [e.get("text", "") for e in session.log if e.get("kind") == "atlas"]


def check_atlas_snapshot_roundtrip():
    """atlas 块随会话落盘 → 重新载入 → 队伍位置还在，移动也被记住。"""
    sid = "atlas-roundtrip"
    session = notdnd_web.Session(sid)
    session.ensure_atlas()
    block = session.to_dict()["atlas"]
    assert isinstance(block, dict), "ensure_atlas 之后必须能导出 §3.4 存档块"
    assert block["version"] == 1
    assert set(block) >= {"world_key", "seed", "setting_rev",
                          "party_locus", "frames"}, block.keys()
    assert block["world_key"] == notdnd_web._default_world_key()

    # 移动一次（出口列表来自视图，移动必然成功）
    exits = session.atlas_view()["exits"]
    assert exits, "编译出的世界至少要有一个出口"
    moved = session.atlas_move(exits[0]["via"])
    assert moved["status"] == "ok" and moved["band"] == exits[0]["band"]
    place_after_move = moved["here"]["place_id"]

    session.save()
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None, "带 atlas 块的存档必须能载入"
    reloaded.ensure_atlas()
    assert reloaded.atlas_view()["here"]["place_id"] == place_after_move, \
        "重新载入后队伍位置必须还在（§1 目标）"
    assert not any("搬迁" in t for t in _atlas_log_texts(reloaded)), \
        "位置仍在时不该出现搬迁说明"


def check_atlas_legacy_save_lazy_compile():
    """老档（无 atlas 块）懒编译：不报错、按当前世界编译，磁盘不动。"""
    sid = "atlas-legacy"
    legacy = {
        "sid": sid,
        "created": 1700000000.0,
        "log": [],
        "seq": 0,
        "save_name": "老档",
        "rules": prism_core.RuleSession(sid).snapshot(),
    }
    path = _save_path(sid)
    _write_save(path, legacy)

    session = notdnd_web.Session.load(sid)
    view = session.atlas_view()          # 惰性编译发生在查看出口的路径上
    assert view["here"]["place_id"], "懒编译后必须有队伍位置"
    assert view["here"]["world_key"] == notdnd_web._default_world_key()
    assert view["here"]["kind"] == "region", "新位置应是 authored 区域"
    texts = _atlas_log_texts(session)
    assert any("懒编译" in t for t in texts), "懒编译应写进会话日志：%s" % texts

    # 迁移只发生在内存：磁盘上的老档保持原样
    with open(path, encoding="utf-8") as handle:
        assert json.load(handle) == legacy, "懒编译不得就地改写磁盘上的老档"


def check_atlas_relocation_on_deleted_place():
    """存档里的队伍位置在世界 JSON 里已不存在：搬迁到仍存在的锚点并写日志。"""
    sid = "atlas-relocate"
    world_key = notdnd_web._default_world_key()
    world = notdnd_web._load_world(world_key)
    key = world["world"]["key"]
    real_region_ids = {r["id"] for r in world.get("regions", [])}
    surface = "%s/surface" % key
    block = {
        "version": 1,
        "world_key": world_key,
        "seed": 12345,
        "setting_rev": "stale-rev",
        "party_locus": {"frame_id": surface,
                        "place_id": "%s/gone-place" % key,
                        "x": 3, "y": 3, "z": 0},
        "frames": {surface: {"seed": None, "deltas": [
            {"op": "set_trait", "place_id": "%s/also-gone" % key,
             "payload": {"trait": "wilderness"}},
        ]}},
    }
    _write_save(_save_path(sid), {
        "sid": sid, "created": 1.0, "log": [], "seq": 0, "atlas": block,
        "rules": prism_core.RuleSession(sid).snapshot(),
    })

    session = notdnd_web.Session.load(sid)
    view = session.atlas_view()
    place_id = view["here"]["place_id"]
    assert place_id != "%s/gone-place" % key, "不能站在已删除的地点上"
    assert place_id.rsplit("/", 1)[1] in real_region_ids, \
        "搬迁目的地必须是当前世界仍存在的区域：%s" % place_id
    texts = _atlas_log_texts(session)
    assert any("搬迁" in t for t in texts), "搬迁必须写进会话日志：%s" % texts
    assert any("丢弃增量" in t for t in texts), \
        "指向已删除地点的增量应被丢弃并记录：%s" % texts
    assert any("setting_rev" in t for t in texts), \
        "setting_rev 变化应写进会话日志：%s" % texts


def check_api_atlas_exits_and_move():
    """HTTP 路径：出口列表是文本（不是图片），移动返回行程档与时段（§5.2）。"""
    sid = "atlas-api"
    session = notdnd_web.Session(sid)
    session.ensure_atlas()
    with _Server() as server:
        # 存档要写进**服务端**的存档目录（子进程有自己的 NOTDND_SAVE）。
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        srv_path = os.path.join(info["save_dir"], "%s.json" % sid)
        _write_save(srv_path, session.to_dict())
        status, view = server.get("/api/atlas/exits", sid=sid)
        assert status == 200, "出口查看应 200，实际 %s / %s" % (status, view)
        assert view["text"].startswith("当前位置："), "返回必须带文本出口列表"
        assert isinstance(view["exits"], list) and view["exits"]
        for entry in view["exits"]:
            assert set(entry) >= {"via", "name", "place_id", "band"}, entry
            assert entry["hours"] == notdnd_web.BAND_HOURS.get(entry["band"], 0)

        # 移动：优先挑一条带行程档的出口（跨区），否则退到第一条
        target = next((e for e in view["exits"] if e["band"]), view["exits"][0])
        status, moved = server.post("/api/atlas/move",
                                    {"via": target["via"]}, sid=sid)
        assert status == 200, "移动应 200，实际 %s / %s" % (status, moved)
        assert moved["status"] == "ok"
        assert moved["band"] == target["band"], "行程档应来自连接的 band"
        assert moved["hours"] == notdnd_web.BAND_HOURS.get(target["band"], 0), \
            "时段数必须与 §5.2 / travel.json 一致"
        assert moved["here"]["place_id"] == target["place_id"]

        # 移动结果要随存档块落盘（服务端目录）：重新读取后位置仍在
        with open(srv_path, encoding="utf-8") as handle:
            on_disk = json.load(handle)
        assert on_disk["atlas"]["party_locus"]["place_id"] == target["place_id"], \
            "移动后位置应随 atlas 块落盘"

        # 没有这个出口 → 既有 400 语义
        status, err = server.post("/api/atlas/move", {"via": "不存在方向"}, sid=sid)
        assert status == 400 and err.get("error"), (status, err)
# 导引者（G2）
# --------------------------------------------------------------------------


def check_guide_block_wiring():
    """`guide` 块四处接线：新档掷 salt、落盘、重启不变、L2 里没有 salt。"""
    sid = "guide-wire-1"
    first = notdnd_web.Session(sid)
    salt = first.guide["salt"]
    assert _SALT_RE.match(salt), "新存档的 guide.salt 必须是 32 位十六进制"
    # 形状归 prism_guide：网页层不得自己再列一遍键。
    assert set(first.guide.keys()) == set(prism_guide.empty_guide().keys())
    assert first.guide["realizations"] == {}
    first.save()

    with open(_save_path(sid), encoding="utf-8") as handle:
        on_disk = json.load(handle)
    assert on_disk["guide"]["salt"] == salt, "guide 必须真的落盘"

    again = notdnd_web.Session.load(sid)      # 绕过缓存，走真实读盘路径
    assert again is not None and again.guide["salt"] == salt, "重新载入后 salt 不变"
    assert again.guide["l2"] == "【检查点】\n账本：无\n"
    assert salt not in again.guide["l2"], "G2 的 salt 不得进入 L2"
    assert salt not in prism_guide.L0 and salt not in prism_guide.L1_UNBOUND

    # 默认值必须是**工厂**：两个新档不能共享同一个 guide dict。
    other = notdnd_web.Session("guide-wire-2")
    assert other.guide is not first.guide and other.guide["salt"] != salt
    assert other.guide["stats"] is not first.guide["stats"]


def check_guide_load_contract():
    """§7 加载合同：不是 dict / 脏键 / 缺键三种老档都要**按键**还原。"""
    fixed = "0123456789abcdef0123456789abcdef"

    # 1) `guide` 不是 dict，原值本身是 32 位十六进制 → 加载后的 salt 就是这串。
    _write_save(_save_path("guide-str"), {"sid": "guide-str", "guide": fixed})
    s = notdnd_web.Session.load("guide-str")
    assert s is not None and s.guide["salt"] == fixed, s.guide.get("salt")
    assert s.guide["l2"] == "【检查点】\n账本：无\n"

    # 2) `guide` 是 dict：realizations 合法、旁边一个键类型不对 → 只丢那个键。
    _write_save(_save_path("guide-mixed"), {"sid": "guide-mixed", "guide": {
        "salt": fixed,
        "l2_scene_id": "sc-1",
        "realizations": {"loc-02": {"source": "model"}},
        "world_key": 123,           # 类型不对 → 丢
        "voices": "not-a-dict",     # 类型不对 → 丢
        "nonsense": [1, 2, 3],      # 不认识 → 丢
    }})
    s = notdnd_web.Session.load("guide-mixed")
    assert s.guide["salt"] == fixed, "合法 salt 不得重掷"
    assert s.guide["realizations"] == {"loc-02": {"source": "model"}}, \
        "一个脏键不得连带删掉 realizations"
    assert s.guide["l2_scene_id"] == "sc-1"
    assert s.guide["world_key"] == "" and s.guide["voices"] == {}
    assert "nonsense" not in s.guide

    # 3) 缺 guide 的老档 → 补一份**新** salt 的块，且两个老档不共享。
    _write_save(_save_path("guide-none"), {"sid": "guide-none", "created": 1.0})
    a = notdnd_web.Session.load("guide-none")
    b = notdnd_web.Session.load("guide-none")
    assert _SALT_RE.match(a.guide["salt"]) and a.guide["salt"] != b.guide["salt"]
    assert a.guide is not b.guide


def check_guide_status_endpoint():
    """GET /api/guide/status：三个布尔，不需要会话；空 BASE_URL 全假。"""
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            status, body = server.get("/api/guide/status")
            assert status == 200, status
            assert body == {"chat": True, "tts": True, "configured": True}, body
            dumped = json.dumps(body, ensure_ascii=False)
            assert "secret123" not in dumped and "BASE_URL" not in dumped
            assert "api.example.com" not in dumped
        with _guide_online(_OFFLINE_ENV, _FakeTransport()):
            status, body = server.get("/api/guide/status")
            assert status == 200, status
            assert body == {"chat": False, "tts": False, "configured": False}, body


def check_guide_turn_validation():
    """`turn` 的状态行之前四步：长度 → 会话 → 速率 → 淡出整句。"""
    with _LocalServer() as server:
        with _guide_online(_OFFLINE_ENV, _FakeTransport()):
            # 长度：超过 2000 字 → 400「这句话太长」。
            _guide_session("g2-len")
            status, body, _raw, heads = server.post(
                "/api/guide/turn", {"text": "字" * 2001}, sid="g2-len")
            assert status == 400 and body.get("error") == "这句话太长", body
            assert "event-stream" not in heads.get("content-type", "")
            # 边界：正好 2000 字不算超。
            status, _body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "字" * 2000}, sid="g2-len")
            assert status == 200, status

            # 会话：没有 X-Session → 400。
            status, body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "我看看"})
            assert status == 400 and body.get("error"), body

            # 淡出 / 跳过是**整句相等**，且**不结算**（不调用 perform_action）。
            _guide_session("g2-fade")
            seen = []
            saved_call = prism_core.perform_action

            def counting(*args, **kwargs):
                seen.append(args)
                return saved_call(*args, **kwargs)

            prism_core.perform_action = counting
            try:
                status, body, _raw, _heads = server.post(
                    "/api/guide/turn",
                    {"text": "淡出", "action_id": "a-lock"}, sid="g2-fade")
                assert status == 200, status
                assert body["narration"]["text"] == notdnd_web.GUIDE_FADE_TEXT, body
                status, body, _raw, _heads = server.post(
                    "/api/guide/turn", {"text": "跳过这段"}, sid="g2-fade")
                assert status == 200
                assert body["narration"]["text"] == notdnd_web.GUIDE_SKIP_TEXT, body
            finally:
                prism_core.perform_action = saved_call
            assert seen == [], "淡出 / 跳过整句不得结算"

            # 单独的「跳过」不命中整句，走正常回合（本用例离线 → 兜底流）。
            status, _body, raw, heads = server.post(
                "/api/guide/turn", {"text": "跳过"}, sid="g2-fade")
            assert status == 200 and "event-stream" in heads.get("content-type", "")
            assert [name for name, _ in _parse_sse(raw)][0] == "fallback"

            # 速率：每 60 秒最多 12 次 turn，第 13 次 429。
            _guide_session("g2-rate")
            codes = [server.post("/api/guide/turn", {"text": "淡出"},
                                 sid="g2-rate")[0] for _ in range(12)]
            assert codes == [200] * 12, codes
            status, body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "淡出"}, sid="g2-rate")
            assert status == 429 and body.get("error") == "太频繁", body


def check_guide_turn_settle_stream():
    """有 `action_id`：先结算（落盘）→ HTTP/1.1 分块 → 审查后的叙事进 L3。"""
    sid = "g2-turn-1"
    _guide_session(sid)
    fake = _FakeTransport()
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
            assert status == 200, status
            assert "event-stream" in heads.get("content-type", ""), heads
            assert heads.get("transfer-encoding") == "chunked", heads
            assert "content-length" not in heads, "分块响应不得带 Content-Length"

            events = _parse_sse(raw)
            assert [name for name, _ in events] == \
                ["result", "narration", "usage", "done"], events
            result = events[0][1]
            assert result["status"] == "resolved" and result["passed"] is True
            assert result["auto"] is True             # auto_pass 免骰
            assert result["rolled"] is False and result["roll"] is None

            text = events[1][1]["text"]
            # 免骰且达成 → 裁决是服务端的「判定：成功」，不是模型自己写的。
            assert text.startswith("【裁决】判定：成功"), text
            assert "铁屑落在脚边" in text
            assert events[2][1] == {"prompt_tokens": 7, "cached_tokens": 6,
                                    "completion_tokens": 3,
                                    "reasoning_tokens": 0}, events[2][1]

            # 假传输收到的是叙事请求体：思考关、流式、密钥不入体、L0 在最前。
            assert len(fake.calls) == 1, fake.calls
            payload = fake.calls[0]["payload"]
            assert payload["thinking"] == {"type": "disabled"}
            assert payload["stream"] is True and payload["temperature"] == 0.7
            assert payload["model"] == "mm"
            assert fake.calls[0]["headers"] == {prism_guide.KEY_HEADER: "secret123"}
            assert payload["messages"][0]["role"] == "system"
            assert payload["messages"][0]["content"] == prism_guide.L0
            assert payload["messages"][-1]["role"] == "user"
            assert "我按住门栓" in payload["messages"][-1]["content"]
            assert "secret123" not in json.dumps(payload, ensure_ascii=False)

    # 重启后：结算看得见，L3 是审查后的文本。
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None
    assert "a-lock" in reloaded.rules["done_actions"], "结算必须落盘"
    transcript = reloaded.guide["transcript"]
    assert transcript and transcript[-1] == {"role": "assistant",
                                             "content": text}, transcript
    assert reloaded.guide["salt"] not in reloaded.guide["l2"]


def check_guide_turn_stream_failure():
    """头写出之后失败：先 `result` 再 `fallback`，且没有第二行 HTTP 状态。"""
    sid = "g2-fail-1"
    _guide_session(sid)
    fake = _FakeTransport([prism_guide.TransportError("boom")])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            raw = _raw_http(server.port, "/api/guide/turn",
                            {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
    assert _status_lines(raw) == 1, "头写出之后不得再发第二行 HTTP 状态"
    assert b"event-stream" in raw.split(b"\r\n\r\n", 1)[0]
    events = _parse_sse(_dechunk(raw))
    assert [name for name, _ in events] == ["result", "fallback", "done"], events
    fallback = events[1][1]["text"]
    assert fallback.startswith("【裁决】判定：成功"), fallback
    assert prism_guide.FALLBACK_NARRATION in fallback
    assert len(fake.calls) == 1

    reloaded = notdnd_web.Session.load(sid)
    # 结算仍落盘了；兜底句**不进** L3（L3 只存审查之后的模型文本）。
    assert "a-lock" in reloaded.rules["done_actions"]
    assert reloaded.guide["transcript"] == []


def check_guide_turn_review_failure_keeps_verdict():
    """记法对不上：裁决仍是 `roll["detail"]`，叙事是兜底句。"""
    sid = "g2-notation"
    _guide_session(sid, auto_pass=False)
    bad = {"content": "【裁决】x\n【叙事】你掷出 99d99 = 999，石壁塌了。",
           "tool_calls": [], "usage": {}}
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, _FakeTransport([bad])):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
    assert status == 200, status
    events = _parse_sse(raw)
    assert [name for name, _ in events] == ["result", "fallback", "done"], events
    result = events[0][1]
    assert result["rolled"] is True and isinstance(result["roll"], dict)
    fallback = events[1][1]["text"]
    assert fallback.startswith("【裁决】" + result["roll"]["detail"]), fallback
    assert prism_guide.FALLBACK_NARRATION in fallback
    assert "石壁塌了" not in fallback


def check_guide_turn_settle_errors_before_stream():
    """开头的 `ValueError` 在 SSE 之前变成固定 JSON（含 409）。"""
    with _LocalServer() as server:
        with _guide_online(_OFFLINE_ENV, _FakeTransport()):
            # 战斗没结束 → 409「战斗还没结束」，没有 SSE 头。
            _guide_session("g2-combat", auto_pass=False, combat=True)
            status, body, _raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "a-lock"}, sid="g2-combat")
            assert status == 409, (status, body)
            assert body.get("error") == "战斗还没结束", body
            assert "event-stream" not in heads.get("content-type", "")
            raw = _raw_http(server.port, "/api/guide/turn",
                            {"text": "我按住门栓", "action_id": "a-lock"},
                            sid="g2-combat")
            assert _status_lines(raw) == 1

            # 行动不存在 → 400「没有这个行动」。
            _guide_session("g2-noaction")
            status, body, _raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "nope"}, sid="g2-noaction")
            assert status == 400, (status, body)
            assert body.get("error") == "没有这个行动", body
            assert "event-stream" not in heads.get("content-type", "")


def check_guide_missing_module():
    """模块缺失：状态全假；`turn` 返回固定 JSON 兜底且**不结算**。"""
    with _LocalServer() as server:
        saved_module = notdnd_web.prism_guide
        notdnd_web.prism_guide = None
        try:
            status, body = server.get("/api/guide/status")
            assert status == 200, status
            assert body == {"chat": False, "tts": False, "configured": False}, body

            _guide_session("g2-nomod")
            seen = []
            saved_call = prism_core.perform_action

            def counting(*args, **kwargs):
                seen.append(args)
                return saved_call(*args, **kwargs)

            prism_core.perform_action = counting
            try:
                status, body, _raw, heads = server.post(
                    "/api/guide/turn",
                    {"text": "我按住门栓", "action_id": "a-lock"}, sid="g2-nomod")
            finally:
                prism_core.perform_action = saved_call
            assert status == 200, status
            assert body["narration"]["text"] == notdnd_web.FALLBACK_NARRATION, body
            assert "event-stream" not in heads.get("content-type", "")
            assert seen == [], "模块缺失时不得结算"
        finally:
            notdnd_web.prism_guide = saved_module


# --------------------------------------------------------------------------
# 导引者朗读（G3，§5.9 / §5.10）
# --------------------------------------------------------------------------


def _beats_fixture() -> list:
    """两条拍：一条旁白（白桦）、一条由夹具标成茉莉的对白。"""
    return [
        {"text": "风停了。", "voice": notdnd_web.prism_guide.VOICE_NARRATOR,
         "tone": "平叙", "channel": "speech", "speaker": ""},
        {"text": "有人应了一声。", "voice": "茉莉", "tone": "平叙",
         "channel": "speech", "speaker": "npc-02"},
    ]


def check_guide_turn_writes_beats():
    """`turn` 切拍：随 `narration` 回给浏览器，并写进 `last_beats`（§5.8 / §5.9）。"""
    sid = "g3-turn-beats"
    _guide_session(sid)
    narration = ("【裁决】你把手按上门栓。\n"
                 "【叙事】风从巷口灌进来。霍砚：「别出声。」\n"
                 "【钩子】巷口有人影。")
    fake = _FakeTransport([{"content": narration, "tool_calls": [],
                            "usage": {"prompt_tokens": 3}}])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
    assert status == 200, status
    payload = dict(_parse_sse(raw))["narration"]
    assert payload["text"].startswith("【裁决】判定：成功"), payload["text"]
    beats = payload["beats"]
    assert [b["text"] for b in beats] == \
        ["风从巷口灌进来。", "别出声。", "\n\n巷口有人影。"], beats
    # G3 还不按 `npc.id` 分配声线（那是 G7）：一律白桦。
    assert all(b["voice"] == notdnd_web.prism_guide.VOICE_NARRATOR for b in beats)
    assert all(b["tone"] == "平叙" and b["channel"] == "speech" for b in beats)
    assert all(b["speaker"] == "" for b in beats)
    # 服务端的裁决那一行不朗读。
    assert not any("【裁决】" in b["text"] or "判定：" in b["text"] for b in beats)

    reloaded = notdnd_web.Session.load(sid)
    assert reloaded.guide["last_beats"] == beats, \
        "节拍必须随存档落盘——speak 只认 last_beats"


def check_guide_speak_validation():
    """`speak` 的状态行之前：长度 → 会话 → 速率 → 语音可用 → 全文匹配。

    验收：不在 `last_beats` 里的文本返回 400，**且传输未被调用**。
    """
    sid = "g3-speak-bad"
    _guide_session(sid)
    _set_last_beats(sid, _beats_fixture())
    fake = _tts_transport([b"\x01\x02"])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            # 不在 last_beats 里 → 400「没有可朗读的句子」，传输未被调用。
            status, body, _raw, heads = server.post(
                "/api/guide/speak", {"text": "换一句吧。"}, sid=sid)
            assert status == 400, (status, body)
            assert body.get("error") == "没有可朗读的句子", body
            assert "audio/" not in heads.get("content-type", ""), heads
            assert fake.calls == [], "没匹配上就不许调用传输"

            # 近似但不全等（少一个字 / 多一个空格 / 空文本）同样不算命中。
            for wrong in ("风停了", " 风停了。", ""):
                status, body, _raw, _heads = server.post(
                    "/api/guide/speak", {"text": wrong}, sid=sid)
                assert status == 400 and body.get("error") == "没有可朗读的句子", \
                    (wrong, status, body)
            assert fake.calls == []

            # 没有 X-Session → 400；超长 → 400「这句话太长」。
            status, body, _raw, heads = server.post("/api/guide/speak",
                                                    {"text": "风停了。"})
            assert status == 400 and body.get("error"), body
            assert "audio/" not in heads.get("content-type", "")
            status, body, _raw, _heads = server.post(
                "/api/guide/speak", {"text": "字" * 2001}, sid=sid)
            assert status == 400 and body.get("error") == "这句话太长", body
            assert fake.calls == []

        # 没配 BASE_URL / 密钥：语音不可用，仍然**不请求**（§5.1 没有第二套降级）。
        with _guide_online(_OFFLINE_ENV, fake):
            status, body, _raw, _heads = server.post(
                "/api/guide/speak", {"text": "风停了。"}, sid=sid)
            assert status == 400 and body.get("error") == "语音不可用", body
            assert fake.calls == []


def check_guide_speak_stream():
    """`speak` 合法路径：HTTP/1.1 + 分块 + 24 kHz / pcm16 头 + 拼接后的 PCM。"""
    sid = "g3-speak-ok"
    _guide_session(sid)
    _set_last_beats(sid, _beats_fixture())
    pcm_a, pcm_b = b"\x01\x02", b"\x03\x04\x05\x06"
    fake = _tts_transport([pcm_a, pcm_b])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, heads = server.post(
                "/api/guide/speak", {"text": "风停了。"}, sid=sid)
            assert status == 200, status
            assert raw == pcm_a + pcm_b, (raw, "每个 delta 单独解码后按序拼接")
            assert heads.get("content-type") == "audio/pcm16", heads
            assert heads.get("x-audio-format") == "pcm16", heads
            assert heads.get("x-audio-sample-rate") == "24000", heads
            assert heads.get("transfer-encoding") == "chunked", heads
            assert "content-length" not in heads, "分块响应不得带 Content-Length"

            # 请求体是 TTS 形状：台词在 assistant、user 是平叙卡、密钥不入体。
            payload = fake.calls[-1]["payload"]
            assert payload["model"] == "mimo-v2.5-tts"
            assert payload["stream"] is True
            assert payload["audio"] == {"format": "pcm16"}
            assert payload["messages"][0] == {"role": "user", "content": "平叙"}
            assert payload["messages"][1] == {"role": "assistant",
                                              "content": "风停了。"}
            assert payload["voice"] == notdnd_web.prism_guide.VOICE_NARRATOR
            assert fake.calls[-1]["headers"] == \
                {prism_guide.KEY_HEADER: "secret123"}
            assert "secret123" not in json.dumps(payload, ensure_ascii=False)

            # 夹具把这一拍标成茉莉：音色**取自那一拍**，不按 id 重算（分配是 G7）。
            status, _body, raw2, _heads = server.post(
                "/api/guide/speak", {"text": "有人应了一声。"}, sid=sid)
            assert status == 200 and raw2 == pcm_a + pcm_b, (status, raw2)
            assert fake.calls[-1]["payload"]["voice"] == "茉莉"
            assert fake.calls[-1]["payload"]["messages"][1]["content"] == \
                "有人应了一声。"

            # 客户端多传的字段被忽略（§5.8）。
            status, _body, raw3, _heads = server.post(
                "/api/guide/speak",
                {"text": "风停了。", "voice": "茉莉", "tone": "激昂"}, sid=sid)
            assert status == 200 and raw3 == pcm_a + pcm_b
            assert fake.calls[-1]["payload"]["voice"] == "白桦"

            # 裸 socket：整条响应只有**一行** HTTP 状态，头块里 24000 / pcm16 都在。
            raw_http = _raw_http(server.port, "/api/guide/speak",
                                 {"text": "风停了。"}, sid=sid)
    assert _status_lines(raw_http) == 1, "音频流也不许发第二行 HTTP 状态"
    head_block = raw_http.split(b"\r\n\r\n", 1)[0]
    assert b"HTTP/1.1 200" in head_block, head_block
    assert b"pcm16" in head_block and b"24000" in head_block, head_block
    assert b"Transfer-Encoding: chunked" in head_block, head_block
    assert _dechunk(raw_http) == pcm_a + pcm_b, "分块体还原后仍是 PCM 相接"


# --------------------------------------------------------------------------
# 导引者工具环（G4，§5.2 / §5.5 / §5.6）
# --------------------------------------------------------------------------


def _tool_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _thinking_open_calls(fake) -> list:
    """「思考开且 stream 为 false」的调用——按 §5.5 只允许是工具预通行。"""
    return [call for call in fake.calls
            if call["payload"].get("stream") is False
            and (call["payload"].get("thinking") or {}).get("type") == "enabled"]


def _narrative_calls(fake) -> list:
    return [call for call in fake.calls if call["payload"].get("stream") is True]


def check_guide_tool_prepass_requests():
    """`查规则` 开一次预通行；`我想查规则` 不开。叙事请求保持思考关、无 role: tool。"""
    sid = "g4-prepass"
    _guide_session(sid)
    # 脚本：预通行第 1 轮要一只工具 → 第 2 轮不要 → 叙事。
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("lookup_rule",
                                                  {"kind": "system.guardrails"})],
         "usage": {}, "reasoning": "先查护栏"},
        {"content": "", "tool_calls": [], "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn", {"text": "查规则"}, sid=sid)
            assert status == 200, status
            assert dict(_parse_sse(raw))["narration"]["text"].startswith("【裁决】")

            assert len(fake.calls) == 3, [c["payload"].get("stream")
                                          for c in fake.calls]
            # 「思考开且 stream 为 false」的调用**全部**属于预通行：这里是它的
            # 两轮（第 1 轮要工具、第 2 轮收口），叙事请求在最后、思考是关的。
            opens = _thinking_open_calls(fake)
            assert opens == fake.calls[:2], [c["payload"].get("stream")
                                             for c in fake.calls]
            assert all(call["payload"]["tools"] == prism_guide.TOOLS
                       for call in opens)
            # 预通行请求体（§5.5 表）：思考开、无 temperature、1024、非流式、带工具。
            head = fake.calls[0]["payload"]
            assert head["thinking"] == {"type": "enabled"}
            assert "temperature" not in head
            assert head["max_completion_tokens"] == 1024
            assert head["stream"] is False
            assert head["tools"] == prism_guide.TOOLS
            assert head["model"] == "mm"
            assert fake.calls[0]["headers"] == {prism_guide.KEY_HEADER: "secret123"}
            # 第 2 轮的往返：助手消息带 reasoning_content，工具正文是规则摘要。
            round2 = fake.calls[1]["payload"]["messages"]
            assert round2[-2]["role"] == "assistant"
            assert round2[-2]["reasoning_content"] == "先查护栏"
            assert round2[-1]["role"] == "tool"
            assert round2[-1]["tool_call_id"] == "call-1"
            # 工具正文是那只 kind 的规则摘要（文件自己的 note 打头），且已截到上限。
            tool_text = round2[-1]["content"]
            assert tool_text.startswith("内容护栏"), tool_text[:40]
            assert len(tool_text) <= prism_guide.LOOKUP_RULE_LIMIT

            # 叙事请求：思考关、流式、重拼 L0–L4（无 role: tool、无 reasoning_content）。
            narrative = _narrative_calls(fake)
            assert len(narrative) == 1
            # 预通行确实挂在**叙事消息数组**上（两边逐条相同）。
            assert opens[0]["payload"]["messages"] == narrative[0]["payload"]["messages"]
            payload = narrative[0]["payload"]
            assert payload["thinking"] == {"type": "disabled"}
            assert payload["stream"] is True and payload["temperature"] == 0.7
            blob = json.dumps(payload, ensure_ascii=False)
            assert "先查护栏" not in blob and "reasoning_content" not in blob
            assert not any(msg.get("role") == "tool" for msg in payload["messages"])
            assert not any("tool_calls" in msg for msg in payload["messages"])

            # `我想查规则` 不是整句 → 不开预通行（只有叙事那一次调用）。
            fake.calls.clear()
            status, _body, raw, _heads = server.post(
                "/api/guide/turn", {"text": "我想查规则"}, sid=sid)
            assert status == 200, status
            assert len(fake.calls) == 1, [c["payload"].get("stream")
                                          for c in fake.calls]
            assert fake.calls[0]["payload"]["thinking"] == {"type": "disabled"}
            assert _thinking_open_calls(fake) == []

            # 本回合已经有机械结果（带 action_id）→ 也不开预通行。
            # （`a-lock` 是 auto_pass，免骰，叙事里没有新掷骰。）
            fake.calls.clear()
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "查规则", "action_id": "a-lock"}, sid=sid)
            assert status == 200, status
            assert len(fake.calls) == 1, [c["payload"].get("stream")
                                          for c in fake.calls]
            assert _thinking_open_calls(fake) == []


def check_guide_tool_error_never_new_status():
    """工具里的 `ValueError` 变成固定短语回给模型，**不**产生第二行 HTTP 状态。"""
    sid = "g4-tool-error"
    _guide_session(sid)
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("request_check",
                                                  {"action_id": "nope"})],
         "usage": {}},
        {"content": "", "tool_calls": [], "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            raw = _raw_http(server.port, "/api/guide/turn", {"text": "查规则"},
                            sid=sid)
    assert _status_lines(raw) == 1, "工具失败不得再写一行 HTTP 状态"
    assert b"event-stream" in raw.split(b"\r\n\r\n", 1)[0]
    events = _parse_sse(_dechunk(raw))
    assert [name for name, _ in events] == ["narration", "usage", "done"], events
    # 工具正文就是那三种固定短语之一（与网页层的映射同文）。
    assert len(fake.calls) == 3, fake.calls
    tool_msg = fake.calls[1]["payload"]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["content"] == "没有这个行动", tool_msg
    assert tool_msg["tool_call_id"] == "call-1"

    # 战斗没结束 → 「战斗还没结束」（同一张固定短语表）。
    sid2 = "g4-tool-error-combat"
    _guide_session(sid2, auto_pass=True, combat=True)
    fake2 = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("request_check",
                                                  {"action_id": "a-lock"})],
         "usage": {}},
        {"content": "", "tool_calls": [], "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake2):
            status, _body, raw2, _heads = server.post(
                "/api/guide/turn", {"text": "查规则"}, sid=sid2)
    assert status == 200, status
    assert fake2.calls[1]["payload"]["messages"][-1]["content"] == "战斗还没结束"


def check_guide_tool_prepass_rebuilds_l4():
    """§5.4：预通行里又 `settle` 了，叙事用的 L4 必须用**写回之后**的快照重拼。"""
    sid = "g4-rebuild"
    _guide_session(sid)
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("request_check",
                                                  {"action_id": "a-lock"})],
         "usage": {}},
        {"content": "", "tool_calls": [], "usage": {}},
    ])
    built = []
    saved = prism_guide.build_narrative_messages

    def counting(*args, **kwargs):
        built.append(args)
        return saved(*args, **kwargs)

    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            prism_guide.build_narrative_messages = counting
            try:
                status, _body, _raw, _heads = server.post(
                    "/api/guide/turn", {"text": "查规则"}, sid=sid)
            finally:
                prism_guide.build_narrative_messages = saved
    assert status == 200, status
    # 开预通行的那一回合构建两次 L0–L4：一次给预通行，一次是 settle 之后的重拼。
    assert len(built) == 2, "预通行 settle 之后必须重拼一次（§5.4）"
    # 工具里的结算真的落盘了（同一个 settle）。
    reloaded = notdnd_web.Session.load(sid)
    assert "a-lock" in reloaded.rules["done_actions"], "工具里的 request_check 要落盘"
    # 且没有因此多开一轮预通行（每回合最多一次）。
    assert len(_thinking_open_calls(fake)) == 2


def check_guide_narrative_tool_calls_no_second_round():
    """叙事完成带 `tool_calls` → 算叙事失败，**不再开一轮**（§5.2）。"""
    sid = "g4-narr-tool"
    _guide_session(sid)
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("lookup_rule",
                                                  {"kind": "system.adjudication"})],
         "usage": {}},
        {"content": "", "tool_calls": [], "usage": {}},
        # 叙事那一次带着 tool_calls 回来：只兜底，不再请求。
        {"content": "【叙事】风停了。",
         "tool_calls": [_tool_call("lookup_rule", {"kind": "system.tone_packs"})],
         "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn", {"text": "查规则"}, sid=sid)
    assert status == 200, status
    events = _parse_sse(raw)
    assert [name for name, _ in events] == ["fallback", "done"], events
    assert prism_guide.FALLBACK_NARRATION in events[0][1]["text"]
    assert len(fake.calls) == 3, "叙事带 tool_calls 不得再开一轮"
    # 兜底文本不进 L3。
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded.guide["transcript"] == []


def check_guide_speak_rate_limit():
    """每会话每 60 秒最多 30 次 `speak`，第 31 次 429「太频繁」（§5.10）。"""
    sid = "g3-speak-rate"
    _guide_session(sid)
    _set_last_beats(sid, _beats_fixture())
    fake = _tts_transport([b"\x01\x02"])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            codes = [server.post("/api/guide/speak", {"text": "风停了。"},
                                 sid=sid)[0] for _ in range(30)]
            assert codes == [200] * 30, codes
            status, body, _raw, heads = server.post(
                "/api/guide/speak", {"text": "风停了。"}, sid=sid)
            assert status == 429 and body.get("error") == "太频繁", (status, body)
            assert "audio/" not in heads.get("content-type", "")
            assert len(fake.calls) == 30, "超限那一次不得调用传输"


# --------------------------------------------------------------------------
# 导引者绑定与焦点（G5，§2.1 / §6）
# --------------------------------------------------------------------------


def check_guide_bind_endpoint():
    """`POST /api/guide/bind`：四种结果；绑定后 L1 才是这场剧本的典范卡。

    绑定走**真文件**（`data/scenarios/yunji.json`，`meta.world` = `yunji`），
    不伪造剧本；断言的都是「重启之后还在」的落盘状态。
    """
    sid = "g5-bind-1"
    _guide_session(sid)
    with _LocalServer() as server:
        # 没有 X-Session → 400（不允许匿名绑定）。
        status, body, _raw, _heads = server.post(
            "/api/guide/bind", {"world_key": "yunji", "scenario_id": "yunji"})
        assert status == 400 and body.get("error"), (status, body)

        # 文件不存在 / id 不是文件名主干 → 400「没有这场战役」。
        for bad in ("nope", "../etc/passwd", "Yunji", "九钥与元柜"):
            status, body, _raw, _heads = server.post(
                "/api/guide/bind", {"world_key": "yunji", "scenario_id": bad},
                sid=sid)
            assert status == 400 and body.get("error") == "没有这场战役", \
                (bad, status, body)

        # `world_key` 与 `meta.world` 不一致 → 400「世界对不上」，不写 scenario_id。
        status, body, _raw, _heads = server.post(
            "/api/guide/bind",
            {"world_key": "threshold", "scenario_id": "yunji"}, sid=sid)
        assert status == 400 and body.get("error") == "世界对不上", (status, body)
        session = notdnd_web.get_session(sid)
        assert session.guide["scenario_id"] == ""
        assert session.guide["world_key"] == ""
        assert session.guide["l1_key"] == "unloaded"
        assert prism_guide.l1_for(session.guide) == prism_guide.L1_UNBOUND

        salt = session.guide["salt"]
        status, body, _raw, _heads = server.post(
            "/api/guide/bind", {"world_key": "yunji", "scenario_id": "yunji"},
            sid=sid)
        assert status == 200, (status, body)
        assert body == {"status": "ok", "world_key": "yunji",
                        "scenario_id": "yunji"}, body
        session = notdnd_web.get_session(sid)
        assert session.guide["scenario_id"] == "yunji"
        assert session.guide["l1_key"] == "yunji"
        assert session.guide["salt"] == salt, "绑定不得重掷 salt"

        # 幂等：同一对 id 再绑一次仍 200，salt 不变。
        status, body, _raw, _heads = server.post(
            "/api/guide/bind", {"world_key": "yunji", "scenario_id": "yunji"},
            sid=sid)
        assert status == 200, (status, body)
        assert notdnd_web.get_session(sid).guide["salt"] == salt

        # 另一场（three_wooden_boxes 的 meta.world 也是 yunji）→ 409「已经绑定」。
        status, body, _raw, _heads = server.post(
            "/api/guide/bind",
            {"world_key": "yunji", "scenario_id": "three_wooden_boxes"}, sid=sid)
        assert status == 409 and body.get("error") == "已经绑定", (status, body)
        assert notdnd_web.get_session(sid).guide["scenario_id"] == "yunji"

        # 绑定之后，叙事请求的第 2 条消息就是这场剧本的典范卡。
        fake = _FakeTransport()
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        assert status == 200, status
        l1 = fake.calls[0]["payload"]["messages"][1]["content"]
        assert l1 == prism_guide.l1_for(notdnd_web.get_session(sid).guide), l1[:80]
        for needle in ("白壁", "绳会账房", "npc-01"):
            assert needle in l1, needle
        assert "尚未选择战役" not in l1
        assert salt not in l1 and "docs/" not in l1

    # 重启之后 binding 与焦点仍在（落盘为证）；两份存档的 L1 全等。
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None
    assert reloaded.guide["scenario_id"] == "yunji"
    assert reloaded.guide["world_key"] == "yunji"
    assert reloaded.guide["salt"] == salt
    other = _guide_session("g5-bind-2")
    other.guide["world_key"] = "yunji"
    other.guide["scenario_id"] = "yunji"
    other.save()
    assert prism_guide.l1_for(reloaded.guide) == prism_guide.l1_for(other.guide), \
        "同一剧本的两份存档，L1 必须全等"


def check_guide_bind_no_module():
    """导引者模块缺失：绑定给固定 400，不崩、不落盘。"""
    with _LocalServer() as server:
        saved_module = notdnd_web.prism_guide
        notdnd_web.prism_guide = None
        try:
            sid = "g5-bind-nomod"
            _guide_session(sid)
            status, body, _raw, heads = server.post(
                "/api/guide/bind",
                {"world_key": "yunji", "scenario_id": "yunji"}, sid=sid)
            assert status == 400 and body.get("error"), (status, body)
            assert "event-stream" not in heads.get("content-type", "")
            # 模块缺失时 `guide` 只有本地最小块：绑定当然没有落下任何 id。
            assert not notdnd_web.Session.load(sid).guide.get("scenario_id")
        finally:
            notdnd_web.prism_guide = saved_module


def check_guide_turn_location_focus():
    """`turn` 的可选 `location_id`：状态行之前校验、写焦点、恰调用一次实相。"""
    sid = "g5-focus-1"
    _guide_session(sid)
    seen = []
    saved_realization = prism_guide.ensure_realization

    def counting(session, location_id):
        seen.append(location_id)
        return saved_realization(session, location_id)

    with _LocalServer() as server:
        # 没绑定的会话带 location_id → 400（没有可对上的剧本），且不调用实相。
        _guide_session("g5-focus-unbound")
        prism_guide.ensure_realization = counting
        try:
            status, body, _raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我走进白壁", "location_id": "loc-02"},
                sid="g5-focus-unbound")
            assert status == 400 and body.get("error") == "没有这个地点", \
                (status, body)
            assert "event-stream" not in heads.get("content-type", ""), heads
            assert seen == [], seen

            status, body, _raw, _heads = server.post(
                "/api/guide/bind",
                {"world_key": "yunji", "scenario_id": "yunji"}, sid=sid)
            assert status == 200, (status, body)

            # 非法 id（包括战役节点名）→ 400，没有 SSE，实相没被调用。
            for bad in ("loc-99", "whitewall", "白壁"):
                status, body, _raw, heads = server.post(
                    "/api/guide/turn",
                    {"text": "我走进白壁", "location_id": bad}, sid=sid)
                assert status == 400 and body.get("error") == "没有这个地点", \
                    (bad, status, body)
                assert "event-stream" not in heads.get("content-type", ""), heads
            assert seen == [], seen

            # 合法 id：状态行之后、叙事之前调用一次；上游只有叙事那一次。
            fake = _FakeTransport()
            with _guide_online(_ONLINE_ENV, fake):
                status, _body, raw, _heads = server.post(
                    "/api/guide/turn",
                    {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
            assert status == 200, status
            assert seen == ["loc-02"], seen
            assert [name for name, _ in _parse_sse(raw)] == \
                ["narration", "usage", "done"], _parse_sse(raw)
            # G6 起，带 location_id 的回合会先发一次**非流式**实相请求（假传输
            # 第一次回的不是合法实相 JSON，服务端按 §5.7 重试一次），之后才是叙事
            # 请求。这里只锁「叙事请求恰有一次」；实相请求的形状与次数归
            # `tests/test_prism_guide.py` 自己测。
            narrative_calls = [call for call in fake.calls
                               if call["payload"].get("stream") is True]
            assert len(narrative_calls) == 1, \
                [call["payload"].get("stream") for call in fake.calls]
            assert notdnd_web.get_session(sid).guide["focus_location_id"] == "loc-02"

            # 不带 location_id：焦点不动，实相不再被调用。
            seen.clear()
            with _guide_online(_ONLINE_ENV, _FakeTransport()):
                status, _body, _raw, _heads = server.post(
                    "/api/guide/turn", {"text": "我看看四周"}, sid=sid)
            assert status == 200 and seen == [], seen
            assert notdnd_web.get_session(sid).guide["focus_location_id"] == "loc-02"
        finally:
            prism_guide.ensure_realization = saved_realization

    # 焦点落盘：重启之后（G6 的下一回合）还看得到队伍走到了哪里。
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded.guide["focus_location_id"] == "loc-02", \
        reloaded.guide["focus_location_id"]
    assert reloaded.guide["scenario_id"] == "yunji"


def _bind(server, sid):
    status, body, _raw, _heads = server.post(
        "/api/guide/bind", {"world_key": "yunji", "scenario_id": "yunji"},
        sid=sid)
    assert status == 200, (status, body)


def _npc(scenario_id, npc_id):
    data = prism_guide.load_scenario(scenario_id) or {}
    for item in data.get("npcs") or []:
        if item.get("id") == npc_id:
            return item
    raise AssertionError("找不到 %s 里的 %s" % (scenario_id, npc_id))


def check_guide_turn_traces_wiring():
    """G7 接线：焦点变化回合 = 1 次场外（思考关 / 非流式 / 无工具）+ 1 次叙事；
    `narration` 至多一条 `trace`；L3 无秘密（全文与 8 码位都查）。"""
    sid = "g7-trace-wire"
    _guide_session(sid)
    huo = _npc("yunji", "npc-01")
    secret = huo["secret"]
    window = secret[:prism_guide.SECRET_WINDOW]
    narration = ("【裁决】判定：成功\n\n"
                 "【叙事】霍砚低声说他是守钥人计霜的儿子。温苔：「灯还亮着。」\n\n"
                 "【钩子】泉声还在响。")
    with _LocalServer() as server:
        _bind(server, sid)
        # 回合 A：无焦点，只建立 scene 检查点（不记痕）。
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            assert server.post("/api/guide/turn", {"text": "我看看四周"},
                               sid=sid)[0] == 200
        # 回合 B：第一次设焦点 loc-02（previous 空 → 不算变化，不记痕）。
        with _guide_online(_ONLINE_ENV,
                           _FakeTransport([_BAD_JSON, _BAD_JSON,
                                           _sse_reply(_OK_NARRATION)])):
            assert server.post(
                "/api/guide/turn",
                {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)[0] == 200
        assert notdnd_web.get_session(sid).guide["focus_location_id"] == "loc-02"
        # 回合 C：焦点 loc-02 → loc-05，才是真正的「离开」。先埋一条未揭开的痕迹。
        _set_guide(sid, {"traces": [{"at": "loc-02", "id": "npc-01",
                                     "text": "他换了把新锁。", "revealed": False}]})
        fake = _FakeTransport([_BAD_JSON, _BAD_JSON, _BAD_JSON,
                               _sse_reply(narration)])
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我走进灰市", "location_id": "loc-05"}, sid=sid)
        assert status == 200, status
        streams = [call["payload"].get("stream") for call in fake.calls]
        trace_calls = [call for call in fake.calls if _is_trace_call(call)]
        narrative_calls = [call for call in fake.calls
                           if call["payload"].get("stream") is True]
        assert len(trace_calls) == 1, streams
        assert len(narrative_calls) == 1, streams
        body = trace_calls[0]["payload"]
        assert body["thinking"] == {"type": "disabled"}
        assert body["stream"] is False and "tools" not in body
        user = body["messages"][1]["content"]
        assert secret not in user, "秘密全文不得进场外请求的 user"
        assert "霍砚" in user and huo["drive"] in user, user
        # narration：至多一条 trace，且是埋下的那条。
        event = dict(_parse_sse(raw))["narration"]
        assert event["trace"] == "他换了把新锁。", event.get("trace")
        # 秘密门：玩家看到的叙事与 L3 都没有秘密全文、也没有 8 码位。
        assert "……" in event["text"], event["text"]
        assert secret not in event["text"] and window not in event["text"]
        assert "灯还亮着。" in event["text"], "点出别人名字的对白要留下"
        session = notdnd_web.get_session(sid)
        l3 = "".join(entry["content"] for entry in session.guide["transcript"])
        assert secret not in l3 and window not in l3, l3
        # 切拍从涂掉之后的字里来；说话人按 npc.id 分配声线。
        speakers = {beat["speaker"] for beat in event["beats"] if beat["speaker"]}
        assert speakers == {"npc-02"}, event["beats"]
        assert session.guide["voices"]["npc-02"] == \
            prism_guide.npc_voice("npc-02") == "茉莉"
        # `revealed` 随同一次 save() 落盘。
        assert session.guide["traces"][0]["revealed"] is True
        assert notdnd_web.Session.load(sid).guide["traces"][0]["revealed"] is True
        assert session.guide["traces"][0]["at"] == "loc-02"


def check_guide_turn_traces_betrayed():
    """`npc_flags` 为 betrayed：痕迹等于 `if_dead_or_betrayed` 原文，且无场外请求。"""
    sid = "g7-trace-betrayed"
    _guide_session(sid)
    huo = _npc("yunji", "npc-01")
    with _LocalServer() as server:
        _bind(server, sid)
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            server.post("/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        with _guide_online(_ONLINE_ENV,
                           _FakeTransport([_BAD_JSON, _BAD_JSON,
                                           _sse_reply(_OK_NARRATION)])):
            server.post("/api/guide/turn",
                        {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
        _set_guide(sid, {"npc_flags": {"npc-01": "betrayed"}})
        fake = _FakeTransport([_BAD_JSON, _BAD_JSON, _sse_reply(_OK_NARRATION)])
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我走进灰市", "location_id": "loc-05"}, sid=sid)
        assert status == 200, status
        assert [call for call in fake.calls if _is_trace_call(call)] == [], \
            "终局标志的 NPC 不得叫模型"
        event = dict(_parse_sse(raw))["narration"]
        assert event["trace"] == huo["if_dead_or_betrayed"], event.get("trace")


def check_guide_turn_traces_offline():
    """离线：场外与叙事都发不出去 → 不留痕、不走第二行 HTTP 状态、回合照常。"""
    sid = "g7-trace-offline"
    _guide_session(sid)
    with _LocalServer() as server:
        _bind(server, sid)
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            server.post("/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        with _guide_online(_ONLINE_ENV,
                           _FakeTransport([_BAD_JSON, _BAD_JSON,
                                           _sse_reply(_OK_NARRATION)])):
            server.post("/api/guide/turn",
                        {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
        offline = {"BASE_URL": "", "MODEL": "mm", "API_KEY": ""}
        fake = _FakeTransport()
        with _guide_online(offline, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我走进灰市", "location_id": "loc-05"}, sid=sid)
        assert status == 200, status
        assert fake.calls == [], "离线不得调用传输"
        session = notdnd_web.get_session(sid)
        assert session.guide["traces"] == [], session.guide["traces"]
        assert session.guide["focus_location_id"] == "loc-05"
        events = [name for name, _payload in _parse_sse(raw)]
        assert "fallback" in events and "done" in events, events


def check_guide_turn_scene_change_traces():
    """`rules.scene.id` 变化：为**当前焦点**记一轮场外痕迹（§2.2 原检查点）。"""
    sid = "g7-trace-scene"
    _guide_session(sid)
    with _LocalServer() as server:
        _bind(server, sid)
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            server.post("/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        with _guide_online(_ONLINE_ENV,
                           _FakeTransport([_BAD_JSON, _BAD_JSON,
                                           _sse_reply(_OK_NARRATION)])):
            server.post("/api/guide/turn",
                        {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
        # 直接改快照的 scene.id（§2.2：原检查点仍有效）。
        _set_scene(sid, "sc-2")
        fake = _FakeTransport([_BAD_JSON, _sse_reply(_OK_NARRATION)])
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "我继续查"}, sid=sid)
        assert status == 200, status
        trace_calls = [call for call in fake.calls if _is_trace_call(call)]
        assert len(trace_calls) == 1, \
            [call["payload"].get("stream") for call in fake.calls]
        user = trace_calls[0]["payload"]["messages"][1]["content"]
        assert "霍砚" in user, "scene.id 变化记的是**当前焦点**（loc-02）的在场 NPC"
        assert notdnd_web.get_session(sid).guide["l2_scene_id"] == "sc-2"


CHECKS = (
    check_import_has_no_side_effects,
    check_roundtrip_snapshot,
    check_legacy_save_lazy_migration,
    check_api_session_view,
    check_api_session_without_session_400,
    check_atlas_snapshot_roundtrip,
    check_atlas_legacy_save_lazy_compile,
    check_atlas_relocation_on_deleted_place,
    check_api_atlas_exits_and_move,
    check_guide_block_wiring,
    check_guide_load_contract,
    check_guide_status_endpoint,
    check_guide_turn_validation,
    check_guide_turn_settle_stream,
    check_guide_turn_stream_failure,
    check_guide_turn_review_failure_keeps_verdict,
    check_guide_turn_settle_errors_before_stream,
    check_guide_missing_module,
    check_guide_tool_prepass_requests,
    check_guide_tool_prepass_rebuilds_l4,
    check_guide_tool_error_never_new_status,
    check_guide_narrative_tool_calls_no_second_round,
    check_guide_turn_writes_beats,
    check_guide_speak_validation,
    check_guide_speak_stream,
    check_guide_speak_rate_limit,
    check_guide_bind_endpoint,
    check_guide_bind_no_module,
    check_guide_turn_location_focus,
    check_guide_turn_traces_wiring,
    check_guide_turn_traces_betrayed,
    check_guide_turn_traces_offline,
    check_guide_turn_scene_change_traces,
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
