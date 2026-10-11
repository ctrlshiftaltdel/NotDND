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

 10. 多人房间（M10-S1，MPI §3.2 / §4）：`room` 顶层块随存档落盘、旧单人档惰性迁移
     （T11）；`POST /api/room/create|join|leave|kick` 与 `Authorization: Bearer` +
     `X-Room`/`X-Member` 鉴权链；容量满员 `409 room_full`（T7）、非房主踢人 `403`（T8）、
     坏令牌 / 缺成员头 `401`（T12）；令牌只存 `sha256`、明文不入存档。
     另有一条**确定性并发回归**：房主在「鉴权通过后、踢人变更前」离房时，踢人必须
     被拒（用锁代理插入交错，不靠 sleep 赌时序）。

 11. 建局写接口（M4-B1，UI-DESIGN §2.1 / §7 缺口 B1）：`POST /api/session/create`
     真起服务（随机端口 + 临时存档目录）建局 → 返回可用的新 `sid` 与新局最小视图
     → `GET /api/session` / `GET /api/log` 立即可用；导引者离线（无 `.env`）时同样成立；
     畸形 JSON 体 → 400；落盘失败 → 500 且不返回误导性成功（与 #170 同一口径）；
     重载往返（服务重启后从磁盘重新载入）。既有 `X-Session` / `?sid=` 语义不变。

 12. 回合落会话日志（M4-B2，UI-DESIGN §1.3.5 / §7 缺口 B2）：`/api/guide/turn`
     一回合结束后玩家的 `act`（自由文本或行动 label）、导引者 `narrate`（含
     离线 / 上游失败的兜底句）与揭开的 `trace` 写进 `Session.log`（`seq` 单调、
     锁内递增）；`GET /api/log?since=` 按 `seq` 增量取回；重载往返（回合日志已
     落盘）；叙事记的是秘密门**涂掉之后**的字，被删内容不因落日志重新可见；
     `result` 先于 `narration` 的既有事件顺序不变；离线 / 模块缺失 / 淡出 / 跳过
     这些短路回合同样落 `act` + `narrate`（真子进程覆盖启动路径）。

 13. 存档接线与老档迁移（M7-3，STATE-DESIGN §10 / §11）：`Session` 仍是网页层
     内存门面，`save()` / `load()` 委托 `state`——新档落在
     `campaigns/<sid>/snapshot.json`（检查点），展示流走 `stream.cursor`；
     老档（旧单文件 `<sid>.json`）在**第一次 save 时**才落成新档并留
     `snapshot.json.pre-v0.bak`（只读接口不改写它）；各块逐字搬、salt 不变；
     `?since=` / `/api/session` 的既有语义与用例保持通过；

 14. 行动管线写接口（M8-A3，ACTION-DESIGN §3.3 / §7 / §8 / §9）：`GET
     /api/action/catalog` 与 `POST /api/action/propose|confirm|commit`。离线
     （无 AI / 导引者模块缺失）时 `mode: "action"` 走完「验证 → 结算 → 提交 →
     叙事」一轮，`result` 一定早于叙事段，提交走状态层拿回真实 `event_ids` /
     `commit_ref`；第 9.5 节错误码总表逐条有断言，且**状态行写出之后**绝不出现
     第二行 HTTP 状态（叙事失败 / 门面落盘失败都只走 SSE）；幂等键（同键重放
     回首次结果、无重复副作用；同 `proposal_id` 换键 → 409）与排程钩子（M8
     空实现 `mode == "sync"`、恰好一次）各自成条。

另有一条护栏：`import notdnd_web` 不碰磁盘（存档目录不被创建）。

零依赖：仅 Python 3 标准库。直接 `python3 tests/test_notdnd_web.py` 运行。
"""

import base64
import builtins
import contextlib
import http.client
import json
import os
import pathlib
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
import chron        # noqa: E402
import prism_core   # noqa: E402
import prism_guide  # noqa: E402
import state        # noqa: E402

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
    """老档（旧**单文件**布局）路径：写老档夹具 / 断言老档没被改动用。"""
    return os.path.join(notdnd_web.SAVE_DIR, "%s.json" % sid)


def _campaign_file(sid: str, name: str, save_dir: str = "") -> str:
    """新档（`campaigns/<sid>/`，M7-3 起）里某个文件的路径。"""
    return os.path.join(save_dir or notdnd_web.SAVE_DIR, "campaigns", sid, name)


def _write_save(path: str, payload: dict) -> None:
    """把一份存档 JSON 写到指定目录（模拟已有存档 / 老档）。"""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _read_save(sid: str, save_dir: str = "") -> dict:
    """读磁盘上的存档，**翻成门面（老档）字段名**——新档优先、老档兜底。

    新档是 `state` 的检查点：块在 `blocks`、展示流在 `stream`（游标 `cursor`）、
    建局时间毫秒。断言因此不必认识两套布局（迁移插件 / 落盘口径的差异由本函数吸收）。
    """
    snap = _campaign_file(sid, "snapshot.json", save_dir)
    if os.path.isfile(snap):
        with open(snap, encoding="utf-8") as handle:
            d = json.load(handle)
        blocks = d.get("blocks") if isinstance(d.get("blocks"), dict) else {}
        stream = d.get("stream") if isinstance(d.get("stream"), dict) else {}
        return {
            "sid": d.get("campaign_id"),
            "created": (d.get("created") or 0) / 1000.0,
            "log": stream.get("log") or [],
            "seq": stream.get("cursor"),
            "save_name": d.get("save_name"),
            "rules": blocks.get("rules"),
            "atlas": blocks.get("atlas"),
            "guide": blocks.get("guide"),
            "room": blocks.get("room"),
        }
    with open(os.path.join(save_dir or notdnd_web.SAVE_DIR, "%s.json" % sid),
              encoding="utf-8") as handle:
        return json.load(handle)


def _save_ids(save_dir: str = "") -> set:
    """磁盘上「真的有一档」的 sid 集合——新档要有检查点，建局中途的残留不算。"""
    base = save_dir or notdnd_web.SAVE_DIR
    ids = {name[:-5] for name in os.listdir(base) if name.endswith(".json")}
    camp = os.path.join(base, "campaigns")
    if os.path.isdir(camp):
        ids |= {name for name in os.listdir(camp)
                if os.path.isfile(os.path.join(camp, name, "snapshot.json"))}
    return ids


def _canon_session(session) -> str:
    """门面视图的**规范化 JSON**：比对「落盘 → 重载」是否逐字段一致（§6.1 口径）。"""
    return json.dumps(session.to_dict(), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


@contextlib.contextmanager
def _storage_fault(*, write: bool = False, replace: bool = False):
    """往存档落盘路径注入故障（只在**本进程**生效，用完即还原）。

    `write=True` → 写**临时文件**失败：`pathlib.Path.write_text` 与
    `builtins.open` 的写模式都抛 `OSError`（M7-3 起落盘走 `state` 的
    `.tmp` + `os.replace`，不再经过 `write_text`，两条路径都要堵上）；
    `replace=True` → `os.replace` 抛 `OSError`（原子换名失败）。
    `notdnd_web` / `state` 与测试进程共享同一批模块对象，直接换属性即可
    命中服务端（`_LocalServer` 就在本进程里处理请求）；读模式放行，故障
    只影响写盘。
    """
    def boom(*_args, **_kwargs):
        raise OSError("注入的落盘故障")

    def fake_open(file, mode="r", *args, **kwargs):
        if "w" in mode:
            boom()
        return saved_open(file, mode, *args, **kwargs)

    saved_write = pathlib.Path.write_text
    saved_open = builtins.open
    saved_replace = os.replace
    if write:
        pathlib.Path.write_text = boom
        builtins.open = fake_open
    if replace:
        os.replace = boom
    try:
        yield
    finally:
        pathlib.Path.write_text = saved_write
        builtins.open = saved_open
        os.replace = saved_replace


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

    def __init__(self, offline: bool = False):
        self.dir = tempfile.mkdtemp(prefix="notdnd-web-srv-")
        self.port = _free_port()
        self.log = os.path.join(self.dir, "server.log")
        self.proc = None
        self._log_handle = None
        self.offline = offline

    def __enter__(self):
        env = dict(os.environ)
        env.update(NOTDND_SAVE=self.dir, NOTDND_HOST="127.0.0.1",
                   NOTDND_PORT=str(self.port), PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
        if self.offline:
            # `parse_env` 里 `os.environ` 优先于 `.env`，空值即「未配置」——
            # 即便本机有 `.env`，这个子进程也确定离线，绝不真的出网。
            env.update(BASE_URL="", MODEL="", API_KEY="")
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

    def get(self, path: str, sid: str = "", headers: dict | None = None):
        """发一次 GET，返回 (状态码, 解析后的 JSON)。

        `headers` 用来附加房间鉴权头（`X-Room` / `X-Member` /
        `Authorization`，§4.1）；`sid` 仍写 `X-Session`。
        """
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        request_headers = dict(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        try:
            conn.request("GET", path, headers=request_headers)
            response = conn.getresponse()
            body = response.read()
            return response.status, _load_json(body)
        finally:
            conn.close()

    def post(self, path: str, payload: dict, sid: str = "",
             headers: dict | None = None):
        """发一次 POST，返回 (状态码, 解析后的 JSON)。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        request_headers = {"Content-Type": "application/json; charset=utf-8"}
        request_headers.update(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, body=body, headers=request_headers)
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

    def get(self, path: str, sid: str = "", headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        request_headers = dict(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        try:
            conn.request("GET", path, headers=request_headers)
            response = conn.getresponse()
            return response.status, _load_json(response.read())
        finally:
            conn.close()

    def post(self, path: str, body: dict, sid: str = "",
             headers: dict | None = None):
        """POST 一次，返回 (状态码, JSON, **已解块**的响应体, 响应头字典)。

        `http.client` 会自己把分块体还原，所以这里拿到的 `raw` 直接交给
        `_parse_sse`；只有裸 socket 的 `_raw_http` 才需要 `_dechunk`。
        `headers` 用来附加房间鉴权头（§4.1）。
        """
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            conn.request("POST", path, body=payload, headers=request_headers)
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


def _raw_http(port: int, path: str, body: dict, sid: str = "",
              headers: dict | None = None) -> bytes:
    """用裸 socket 发一次 POST，读到连接关闭——用来数有几行 HTTP 状态。

    `headers` 用来附加请求头（`Idempotency-Key` / `Accept` 等）。
    """
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    lines = ["POST %s HTTP/1.1" % path, "Host: 127.0.0.1",
             "Content-Type: application/json",
             "Content-Length: %d" % len(payload)]
    if sid:
        lines.append("X-Session: %s" % sid)
    for key, value in (headers or {}).items():
        lines.append("%s: %s" % (key, value))
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


def _raw_body_http(port: int, path: str, raw: bytes, sid: str = "",
                   headers: dict | None = None,
                   content_length: int | None = None) -> bytes:
    """用裸 socket 发一段**原始字节**体（畸形 JSON / 超限体专用）。

    `content_length` 可覆盖头里声明的长度：测「请求体过大」时头里声明超限、
    体**不必真发**——服务端在读体之前就按 400 拒了，真发 2 MB 只会撞上 EPIPE。
    """
    length = len(raw) if content_length is None else int(content_length)
    lines = ["POST %s HTTP/1.1" % path, "Host: 127.0.0.1",
             "Content-Type: application/json",
             "Content-Length: %d" % length]
    if sid:
        lines.append("X-Session: %s" % sid)
    for key, value in (headers or {}).items():
        lines.append("%s: %s" % (key, value))
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + raw
    with socket.create_connection(("127.0.0.1", port), timeout=30) as sock:
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
    """建规则会话 → 落盘（新档检查点）→ 重新载入 → 快照一致。"""
    sid = "roundtrip-1"
    snapshot = _sample_rules(sid)

    before = notdnd_web.Session(sid)
    before.rules = snapshot
    before.add_log("narrative", "开场旁白")
    before.save()

    on_disk = _read_save(sid)                  # campaigns/<sid>/snapshot.json
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


def check_state_wiring_new_layout():
    """M7-3：`save()` 委托 `state`——新档是检查点，展示流走 `stream.cursor`。

    断言落盘真的换成了新档（`campaigns/<sid>/snapshot.json`）、`save()` **不**伪造
    事件（展示流不是事实，§11 两条独立序列）、重载后门面逐字段一致（规范化 JSON），
    以及 `state.open_campaign` 能独立打开同一档（是「委托」，不是另起一套格式）。
    """
    sid = "wiring-1"
    session = notdnd_web.Session(sid)
    session.rules = _sample_rules(sid)
    session.save_name = "接线局"
    session.created = 1700000000.0          # 固定建局时间：毫秒化后仍可逐字段比对
    session.add_log("narrative", "开场旁白")
    session.add_log("act", "我推开门")
    session.guide["salt"] = "f" * 32
    session.save()

    snapshot_path = _campaign_file(sid, "snapshot.json")
    assert os.path.isfile(snapshot_path), "新档必须落成 campaigns/<sid>/snapshot.json"
    assert not os.path.exists(_save_path(sid)), "新档不该再写老单文件"
    with open(snapshot_path, encoding="utf-8") as handle:
        snap = json.load(handle)
    assert snap["campaign_id"] == sid and snap["kind"] == "state.snapshot"
    assert snap["stream"]["cursor"] == session.seq == 2, snap["stream"]
    assert [e["text"] for e in snap["stream"]["log"]] == ["开场旁白", "我推开门"]
    assert snap["blocks"]["rules"] == _sample_rules(sid)
    assert snap["blocks"]["guide"]["salt"] == "f" * 32
    assert snap["created"] == 1700000000000, "门面的秒 → 新档的毫秒（§10.1）"
    # 展示流不进事件溯源：save() 不写事件（老档迁移才有一条 system.migration）
    with open(_campaign_file(sid, state.EVENTS_NAME), encoding="utf-8") as handle:
        assert handle.read().strip() == "", "save() 不得为展示流伪造事件"

    # 重载：门面逐字段一致；state 也能独立打开同一档
    notdnd_web._sessions.pop(sid, None)
    again = notdnd_web.Session.load(sid)
    assert again is not None, "新档必须能载入"
    assert _canon_session(again) == _canon_session(session), "往返必须逐字段一致"
    assert again.seq == 2 and again.log[1]["text"] == "我推开门"
    handle = state.open_campaign(sid, base_dir=str(notdnd_web.SAVE_DIR))
    assert handle.view()["stream"]["cursor"] == 2
    assert handle.view()["blocks"]["guide"]["salt"] == "f" * 32
    assert handle.view()["seq"] == 0, "没有事实事件时事件 seq 保持 0"
    assert handle.meta()["save_name"] == "接线局"


def check_legacy_migration_on_first_save():
    """M7-3 / §10：老档在**第一次 save** 时落成新档并留 `.bak`；此前只读不改写磁盘。

    断言：只读加载不动磁盘 → 各块逐字无损（rules / atlas / guide / log，salt 不变）
    → save 后 `.bak` 与老档**逐字相同**、老档被删、往返规范化 JSON 相等。
    """
    sid = "legacy-migrate-1"
    guide = {"salt": "0123456789abcdef0123456789abcdef",
             "realizations": {"l1": {"key": "v"}}}
    legacy = {
        "sid": sid, "created": 1700000000.0,
        "log": [{"seq": 1, "kind": "narrative", "text": "旧档旁白",
                 "speaker": "旁白", "ts": 1700000000000}],
        "seq": 1, "save_name": "旧档",
        "rules": _sample_rules(sid),
        "atlas": {"version": 1, "world_key": "yunji", "seed": 7, "setting_rev": "r1",
                  "party_locus": {"frame_id": "yunji/surface",
                                  "place_id": "yunji/loc-01"},
                  "frames": {}},
        "guide": guide,
    }
    path = _save_path(sid)
    _write_save(path, legacy)
    with open(path, "rb") as handle:
        legacy_bytes = handle.read()

    session = notdnd_web.Session.load(sid)
    assert session is not None, "老档必须能载入"
    # 只读加载：磁盘不动（迁移推迟到第一次 save，§10.1）
    with open(path, "rb") as handle:
        assert handle.read() == legacy_bytes, "只读加载不得改写老档"
    assert not os.path.exists(_campaign_file(sid, "snapshot.json")), \
        "老档 save 之前不该出现新档"
    # 各块逐字无损、salt 不变
    assert session.rules == legacy["rules"], "rules 必须逐字无损"
    assert session.atlas_block == legacy["atlas"], "atlas 必须逐字无损"
    assert session.guide["salt"] == guide["salt"], "salt 必须保留"
    assert session.guide["realizations"] == guide["realizations"]
    assert session.log == legacy["log"], "展示流必须逐条无损"
    assert session.seq == 1 and session.save_name == "旧档"
    assert session.created == 1700000000.0

    session.add_log("narrate", "新的旁白")      # 迁移与落盘在同一次 save 里发生
    session.save()

    with open(_campaign_file(sid, state.LEGACY_BAK_NAME), "rb") as handle:
        assert handle.read() == legacy_bytes, ".bak 必须是老档原文（逐字）"
    assert not os.path.exists(path), "迁移成功后老档应被删掉"
    snap = _read_save(sid)
    assert snap["sid"] == sid and snap["seq"] == 2
    assert snap["rules"] == legacy["rules"], "落盘的 rules 不得被迁移改写"
    assert snap["atlas"] == legacy["atlas"], "落盘的 atlas 不得被迁移改写"
    assert snap["guide"]["salt"] == guide["salt"], "落盘的 salt 必须不变"
    assert [e["text"] for e in snap["log"]] == ["旧档旁白", "新的旁白"]

    # 往返：重新载入后与保存前的门面态**规范化 JSON** 相等
    notdnd_web._sessions.pop(sid, None)
    again = notdnd_web.Session.load(sid)
    assert again is not None, "迁移后的新档必须能载入"
    assert _canon_session(again) == _canon_session(session), \
        "迁移往返必须逐字段一致（规范化 JSON）"


def check_save_list_and_delete_new_layout():
    """M7-3：列表与删除**两种布局都认**——新档按检查点列，删除收走整个目录。"""
    new_sid, legacy_sid = "list-new-1", "list-legacy-1"
    session = notdnd_web.Session(new_sid)
    session.save_name = "新档"
    session.add_log("narrative", "一")
    session.save()
    _write_save(_save_path(legacy_sid), {"sid": legacy_sid, "save_name": "老档",
                                         "log": [{"seq": 1, "text": "旧"}], "seq": 1})

    rows, total = notdnd_web.list_saves()
    by_id = {row["id"]: row for row in rows}
    assert by_id[new_sid]["name"] == "新档" and by_id[new_sid]["log_count"] == 1, \
        by_id.get(new_sid)
    assert by_id[legacy_sid]["name"] == "老档" and by_id[legacy_sid]["log_count"] == 1, \
        by_id.get(legacy_sid)
    assert total >= 2

    with _LocalServer() as server:
        status, body, _raw, _heads = server.post("/api/save/delete", {"sid": new_sid})
        assert status == 200 and body.get("status") == "ok", (status, body)
        # 老档删除同样成立（未迁移的档也能删）
        status, body, _raw, _heads = server.post("/api/save/delete",
                                                 {"sid": legacy_sid})
        assert status == 200 and body.get("status") == "ok", (status, body)
    camp = os.path.dirname(_campaign_file(new_sid, "snapshot.json"))
    assert not os.path.isdir(camp), "新档应整目录收走（检查点 / 日志 / 幂等副本）"
    assert not os.path.exists(_save_path(legacy_sid)), "老档文件应被删掉"
    assert notdnd_web.Session.load(new_sid) is None
    assert new_sid not in {row["id"] for row in notdnd_web.list_saves()[0]}


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
    """HTTP 路径：出口列表是文本（不是图片），移动返回行程档与名义分钟（§5.2）。"""
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
            assert entry["minutes"] == notdnd_web.BAND_MINUTES.get(entry["band"], 0)

        # 移动：优先挑一条带行程档的出口（跨区），否则退到第一条
        target = next((e for e in view["exits"] if e["band"]), view["exits"][0])
        status, moved = server.post("/api/atlas/move",
                                    {"via": target["via"]}, sid=sid)
        assert status == 200, "移动应 200，实际 %s / %s" % (status, moved)
        assert moved["status"] == "ok"
        assert moved["band"] == target["band"], "行程档应来自连接的 band"
        assert moved["minutes"] == notdnd_web.BAND_MINUTES.get(target["band"], 0), \
            "名义分钟数必须与 §2.3（1 时段 = 360 分钟）/ travel.json 一致"
        assert moved["here"]["place_id"] == target["place_id"]

        # 移动结果要随存档块落盘（服务端目录）：重新读取后位置仍在
        on_disk = _read_save(sid, info["save_dir"])
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

    on_disk = _read_save(sid)                  # 新档检查点（campaigns/<sid>/）
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


# --------------------------------------------------------------------------
# 多人房间（M10-S1，MPI §3.2 / §4）
# --------------------------------------------------------------------------


def _room_headers(room_id: str, member_id: str, token: str) -> dict:
    """房间鉴权头（§4.1）：`X-Room` + `X-Member` + `Authorization: Bearer`。"""
    return {"X-Room": room_id, "X-Member": member_id,
            "Authorization": "Bearer %s" % token}


def _read_room_block(save_dir: str, room_id: str) -> dict:
    """直接读服务端写出的存档，取顶层 `room` 块（绕开响应，验证**磁盘**事实）。"""
    return _read_save(room_id, save_dir)["room"]


def _save_bytes(save_dir: str, sid: str) -> str:
    """新档目录里**所有文件**的字节（解码后拼接）：查「明文令牌不得落盘」用。"""
    camp = os.path.join(save_dir, "campaigns", sid)
    out = []
    for name in sorted(os.listdir(camp)):
        with open(os.path.join(camp, name), "rb") as handle:
            out.append(handle.read().decode("utf-8", "replace"))
    return "".join(out)


class _InterleavingLock:
    """测试用锁代理：在第 N 次 `with` **取到锁之前**插入一次确定性交错。

    用来复现「请求 A 鉴权通过 → 请求 B 改变房间 → A 的变更仍执行」这类竞态，
    不靠 `sleep` / 多线程时序赌博，故每次都跑在同一条交错上。只实现房间代码
    用到的 `with`（`__enter__` / `__exit__`），不冒充完整锁。
    """

    def __init__(self, real, at_acquire: int, hook):
        self._real = real
        self._at = at_acquire
        self._count = 0
        self._hook = hook

    def __enter__(self):
        self._count += 1
        if self._count == self._at and self._hook is not None:
            hook, self._hook = self._hook, None
            hook()                      # 在真正拿走锁之前制造交错
        self._real.acquire()
        return self

    def __exit__(self, *_exc):
        self._real.release()
        return False


def check_room_create_join_leave():
    """建房 → 加入 → 离开：令牌只存 hash、明文不入存档（§4.1）。"""
    rid = "room-party-1"
    with _Server() as server:
        status, body = server.post("/api/room/create",
                                   {"room_id": rid, "display_name": "阿雅"})
        assert status == 200, "建房应 200，实际 %s / %s" % (status, body)
        assert body["room_id"] == rid and body["role"] == "host", body
        host_id, host_tok = body["member_id"], body["token"]
        assert notdnd_web.MEMBER_ID_RE.fullmatch(host_id), host_id
        assert notdnd_web.ROOM_ID_RE.fullmatch(rid)

        # 存档里只有 hash：明文令牌不得出现在新档的**任何**字节里。
        raw = _save_bytes(server.dir, rid)
        assert host_tok not in raw, "明文令牌绝不得落盘"
        member = _read_save(rid, server.dir)["room"]["members"][host_id]
        assert member["token_hash"] == notdnd_web._hash_token(host_tok)
        assert "token" not in member, "成员记录只存 token_hash"

        # 第二名加入 → player。
        status, second = server.post("/api/room/join", {"display_name": "柏舟"},
                                     headers={"X-Room": rid})
        assert status == 200, "%s / %s" % (status, second)
        assert second["role"] == "player", second
        player_id, player_tok = second["member_id"], second["token"]

        # 玩家离开：200；此后同令牌请求被拒（403，令牌已不生效）。
        status, left = server.post("/api/room/leave", {},
                                   headers=_room_headers(rid, player_id, player_tok))
        assert status == 200, "%s / %s" % (status, left)
        assert left["member_id"] == player_id
        status, denied = server.post("/api/room/leave", {},
                                     headers=_room_headers(rid, player_id, player_tok))
        assert status == 403, "离开后同令牌应 403，实际 %s / %s" % (status, denied)
        assert denied.get("reason") == "member_left"

        # 磁盘上成员已是 left；房主仍在、房主位不变。
        block = _read_room_block(server.dir, rid)
        assert block["members"][player_id]["state"] == "left"
        assert block["members"][host_id]["state"] == "active"
        assert block["host_member_id"] == host_id


def check_room_legacy_lazy_migration():
    """T11：旧单人档载入 → 惰性迁移出最小 `room` 块；原 rules/guide 不变；往返一致。"""
    sid = "room-legacy-1"
    legacy = {
        "sid": sid, "created": 1700000000.0,
        "log": [{"seq": 1, "kind": "narrative", "text": "旧档旁白",
                 "speaker": "", "ts": 1700000000000}],
        "seq": 1, "save_name": "旧档",
        "rules": _sample_rules(sid),
        "guide": {"salt": "0123456789abcdef0123456789abcdef", "realizations": {}},
    }
    _write_save(_save_path(sid), legacy)

    s = notdnd_web.Session.load(sid)
    assert s is not None, "老档必须能载入"
    room = s.room
    assert room["room_id"] == sid, "room_id == sid（M-11）"
    assert room["schema_version"] == notdnd_web.ROOM_SCHEMA_VERSION == 1
    assert room["host_member_id"] == "", "迁移出的房间尚无房主（M-16）"
    assert room["members"] == {}, "迁移出的房间没有成员"
    assert room["capacity"] == 6 == notdnd_web.ROOM_MAX_MEMBERS
    assert room["settings"]["offline_advance"] == "never", "默认不推进（A5）"

    # 原 rules / guide 不变（迁移只碰 room 块）。
    assert s.rules == legacy["rules"], "迁移不得动 rules"
    assert s.guide["salt"] == legacy["guide"]["salt"], "迁移不得动 guide"

    # 磁盘上的老档没被就地改写（惰性迁移只发生在内存）。
    with open(_save_path(sid), encoding="utf-8") as handle:
        assert json.load(handle) == legacy, "惰性迁移不得就地改写老档"

    # 往返一致：save() 之后重新载入，room 块与 rules 逐字段相等。
    s.save()
    again = notdnd_web.Session.load(sid)
    assert again is not None
    assert again.to_dict()["room"] == room, "room 块往返必须一致"
    assert again.rules == legacy["rules"]
    assert again.guide["salt"] == legacy["guide"]["salt"]

    # 默认房间块必须是**工厂**：两个存档不共享同一个可变对象。
    other = notdnd_web.Session("room-legacy-2")
    assert other.room is not s.room
    other.room["members"]["m-00000000"] = {}
    assert "m-00000000" not in s.room["members"], "不同存档不得共享同一份房间块"


def check_room_capacity_full():
    """T7：第 7 名玩家 → 409 room_full；已用座位不被动摇。"""
    rid = "room-full-1"
    with _Server() as server:
        status, host = server.post("/api/room/create", {"room_id": rid})
        assert status == 200, "%s / %s" % (status, host)
        seating = [host["member_id"]]

        # 房主占 1 座，再加入 5 名玩家 = 满员 6。
        for i in range(5):
            status, joined = server.post("/api/room/join", {"display_name": "P%d" % i},
                                         headers={"X-Room": rid})
            assert status == 200, "第 %d 名应能加入：%s / %s" % (i + 2, status, joined)
            seating.append(joined["member_id"])
        assert len(notdnd_web._room_seats(_read_room_block(server.dir, rid))) == 6

        # 第 7 名 → room_full；房间人数不变。
        status, full = server.post("/api/room/join", {"display_name": "P6"},
                                   headers={"X-Room": rid})
        assert status == 409, "满员应 409，实际 %s / %s" % (status, full)
        assert full.get("reason") == "room_full"
        block = _read_room_block(server.dir, rid)
        assert len(notdnd_web._room_seats(block)) == 6, "已用座位不得被动摇"
        assert set(block["members"]) == set(seating)


def check_room_kick_authorization():
    """T8：非房主踢人 → 403 且房间不变；房主踢人 → 生效，被踢者后续 403。"""
    rid = "room-kick-1"
    with _Server() as server:
        status, host = server.post("/api/room/create", {"room_id": rid})
        assert status == 200, "%s / %s" % (status, host)
        host_id, host_tok = host["member_id"], host["token"]
        status, p1 = server.post("/api/room/join", {"display_name": "甲"},
                                 headers={"X-Room": rid})
        assert status == 200, "%s / %s" % (status, p1)
        status, p2 = server.post("/api/room/join", {"display_name": "乙"},
                                 headers={"X-Room": rid})
        assert status == 200, "%s / %s" % (status, p2)
        p1_id, p1_tok = p1["member_id"], p1["token"]
        p2_id, p2_tok = p2["member_id"], p2["token"]

        # 玩家甲踢玩家乙 → 403 not_authorized；乙仍在座，房主不变。
        status, denied = server.post("/api/room/kick", {"member_id": p2_id},
                                     headers=_room_headers(rid, p1_id, p1_tok))
        assert status == 403, "非房主应 403，实际 %s / %s" % (status, denied)
        assert denied.get("reason") == "not_authorized"
        block = _read_room_block(server.dir, rid)
        assert block["members"][p2_id]["state"] == "active", "403 不得改动房间"
        assert block["host_member_id"] == host_id

        # 房主踢乙 → 200；被踢者后续请求 403（§4.4）。
        status, kicked = server.post("/api/room/kick", {"member_id": p2_id},
                                     headers=_room_headers(rid, host_id, host_tok))
        assert status == 200, "房主应能踢人，实际 %s / %s" % (status, kicked)
        assert kicked["member_id"] == p2_id
        assert _read_room_block(server.dir, rid)["members"][p2_id]["state"] == "left"
        status, after = server.post("/api/room/leave", {},
                                    headers=_room_headers(rid, p2_id, p2_tok))
        assert status == 403, "被踢者应 403，实际 %s / %s" % (status, after)
        assert after.get("reason") == "member_left"

        # 房主踢自己 → 400（不改变房间）。
        status, self_kick = server.post("/api/room/kick", {"member_id": host_id},
                                        headers=_room_headers(rid, host_id, host_tok))
        assert status == 400, "%s / %s" % (status, self_kick)


def check_room_kick_host_check_is_atomic():
    """并发回归：房主在「鉴权通过之后、踢人变更之前」离房 → 踢人必须被拒。

    竞态用锁代理**确定性**复现：在踢人请求取走房间锁（第 2 次 `with`）之前，
    让房主离房。旧实现把房主校验留在锁外 → 这里会漏过并以 200 执行踢人；
    修好后校验与变更同处一个临界区 → 403，且目标成员不受影响。

    走**本进程**服务（`_LocalServer`）才能在测试进程里替换 `session.lock`。
    """
    rid = "room-kick-race-1"
    with _LocalServer() as server:
        status, _body, _raw, _heads = server.post("/api/room/create", {"room_id": rid})
        assert status == 200, "建房失败：%s" % status
        host_id = _body["member_id"]
        host_tok = _body["token"]
        status, victim, _raw, _heads = server.post("/api/room/join", {"display_name": "目标"},
                                                   headers={"X-Room": rid})
        assert status == 200, "加入失败：%s" % status
        victim_id = victim["member_id"]

        session = notdnd_web.get_session(rid)
        assert session is not None, "本进程服务应把会话留在缓存里"

        # 正对照：不插交错时房主踢人 200 —— 证明装置本身不坏。
        status, ok, _raw, _heads = server.post("/api/room/kick", {"member_id": victim_id},
                                               headers=_room_headers(rid, host_id, host_tok))
        assert status == 200, "正对照应 200，实际 %s / %s" % (status, ok)
        with session.lock:      # 复原被踢者，只为继续复现竞态
            session.room["members"][victim_id]["state"] = "active"

        # 竞态：踢人请求进入临界区之前，房主离房（同一次交错，确定性）。
        def host_leaves():
            session.room["host_member_id"] = ""
            session.room["members"][host_id]["state"] = "left"

        real_lock = session.lock
        session.lock = _InterleavingLock(real_lock, at_acquire=2, hook=host_leaves)
        try:
            status, denied, _raw, _heads = server.post(
                "/api/room/kick", {"member_id": victim_id},
                headers=_room_headers(rid, host_id, host_tok))
        finally:
            session.lock = real_lock
        assert status == 403, "房主已离房后不得再踢人，实际 %s / %s" % (status, denied)
        assert denied.get("reason") == "not_authorized"

        room = notdnd_web.get_session(rid).room
        assert room["members"][victim_id]["state"] == "active", "被拒的踢人不得改动目标"
        assert room["host_member_id"] == "", "房主位应保持为空"

        notdnd_web._sessions.pop(rid, None)     # 收摊：别把换过锁的会话留给后面的用例


def check_room_auth_chain():
    """T12：坏令牌 / 缺成员头 / 无凭据 → 401；响应体不泄露成员是否存在。"""
    rid = "room-auth-1"
    with _Server() as server:
        status, host = server.post("/api/room/create", {"room_id": rid})
        assert status == 200, "%s / %s" % (status, host)
        host_id, host_tok = host["member_id"], host["token"]

        status, nothing = server.post("/api/room/leave", {})
        assert status == 401, "无凭据应 401，实际 %s / %s" % (status, nothing)
        status, no_member = server.post("/api/room/leave", {}, headers={
            "X-Room": rid, "Authorization": "Bearer %s" % host_tok})
        assert status == 401, "缺 X-Member 应 401，实际 %s / %s" % (status, no_member)
        status, bad_token = server.post("/api/room/leave", {},
                                        headers=_room_headers(rid, host_id, "0" * 64))
        assert status == 401, "坏令牌应 401，实际 %s / %s" % (status, bad_token)
        status, no_such = server.post("/api/room/leave", {},
                                      headers=_room_headers(rid, "m-deadbeef", host_tok))
        assert status == 401, "成员不存在应 401，实际 %s / %s" % (status, no_such)

        # 先确认响应体真的是「固定 JSON」（不是解析失败回落成的空 dict，
        # 否则下面那条「几份响应体一致」会变成空断言）。
        assert nothing.get("error") and nothing.get("reason") == "token_invalid", nothing
        # 不泄露成员是否存在：几种 401 的响应体必须**完全一致**。
        shapes = {json.dumps(b, sort_keys=True, ensure_ascii=False)
                  for b in (nothing, no_member, bad_token, no_such)}
        assert len(shapes) == 1, "401 不得区分失败缘由：%s" % shapes

        # 房间不存在：形态合法但没建过 → 同样 401、同一响应体。
        status, absent = server.post("/api/room/leave", {},
                                     headers=_room_headers("room-nope-1", host_id, host_tok))
        assert status == 401, "%s / %s" % (status, absent)
        assert json.dumps(absent, sort_keys=True, ensure_ascii=False) in shapes

        # 失败请求不得改动房间：房主仍在座、房主位不变。
        block = _read_room_block(server.dir, rid)
        assert block["members"][host_id]["state"] == "active"
        assert block["host_member_id"] == host_id


# --------------------------------------------------------------------------
# 持久化失败不得静默（Issue #170）：save() 报错 + 有副作用调用点回滚
# --------------------------------------------------------------------------


def check_session_save_reports_failure():
    """`save()` 三态：正常落盘；临时写 / `os.replace` 失败都抛 `SaveError`。"""
    sid = "save-unit"
    session = notdnd_web.Session(sid)
    session.save_name = "甲"

    # 正常保存：不抛、返回 None，磁盘就是刚写的内容（新档检查点）。
    session.save()
    assert _read_save(sid)["save_name"] == "甲"

    # 临时写失败：抛 SaveError（带 sid 与底层 OSError），磁盘维持上一份。
    with _storage_fault(write=True):
        try:
            session.save()
            raised = False
        except notdnd_web.SaveError as exc:
            raised = True
            assert exc.sid == sid, exc.sid
            assert isinstance(exc.cause, OSError), exc.cause
        assert raised, "临时写失败必须抛 SaveError，不得静默"
    assert _read_save(sid)["save_name"] == "甲"

    # os.replace 失败：同样抛 SaveError，换名未发生、磁盘不变。
    session.save_name = "乙"
    with _storage_fault(replace=True):
        try:
            session.save()
            raised = False
        except notdnd_web.SaveError:
            raised = True
        assert raised, "os.replace 失败必须抛 SaveError，不得静默"
    assert _read_save(sid)["save_name"] == "甲", "换名失败不得改写磁盘"


def check_save_rename_failure_rolls_back():
    """改名：临时写 / os.replace 失败 → 500、磁盘不变、内存回滚；恢复后能改。"""
    sid = "save-fault-rename"
    session = notdnd_web.Session(sid)
    session.save_name = "旧名"
    session.save()
    notdnd_web._sessions.pop(sid, None)

    with _LocalServer() as server:
        # 正对照：正常保存成功 → 200，且真的落盘。
        status, body, _raw, _heads = server.post(
            "/api/save/rename", {"sid": sid, "name": "正常名"})
        assert status == 200 and body.get("name") == "正常名", (status, body)
        assert _read_save(sid)["save_name"] == "正常名"

        for label, kwargs in (("临时写失败", {"write": True}),
                              ("os.replace 失败", {"replace": True})):
            with _storage_fault(**kwargs):
                status, body, _raw, _heads = server.post(
                    "/api/save/rename", {"sid": sid, "name": "注入名"})
            assert status == 500, "%s 应回 500，实际 %s / %s" % (label, status, body)
            assert body.get("error"), (label, body)
            # 磁盘维持故障前那一份（未落盘）。
            assert _read_save(sid)["save_name"] == "正常名", label
            # 内存也回滚：不留「内存已改名、磁盘没改」的错位。
            cached = notdnd_web._sessions.get(sid)
            assert cached is not None and cached.save_name == "正常名", (label, cached)

        # 故障解除后重试成功：磁盘与内存都跟上。
        status, body, _raw, _heads = server.post(
            "/api/save/rename", {"sid": sid, "name": "重试名"})
        assert status == 200 and body.get("name") == "重试名", (status, body)
        assert _read_save(sid)["save_name"] == "重试名"

    notdnd_web._sessions.pop(sid, None)


def check_atlas_move_save_failure_rolls_back():
    """`/api/atlas/move` 落盘失败：500、磁盘与内存都留在原地；恢复后能走。"""
    sid = "atlas-save-fault"
    session = notdnd_web.Session(sid)
    session.ensure_atlas()
    view = session.atlas_view()
    origin = view["here"]["place_id"]
    target = next((e for e in view["exits"] if e["band"]), view["exits"][0])
    assert target["place_id"] != origin, "需要一个会真的改变位置的出口"
    _write_save(_save_path(sid), session.to_dict())
    notdnd_web._sessions.pop(sid, None)

    with _LocalServer() as server:
        with _storage_fault(replace=True):
            status, body, _raw, _heads = server.post(
                "/api/atlas/move", {"via": target["via"]}, sid=sid)
        assert status == 500, (status, body)
        assert body.get("error"), body
        # 磁盘维持原位置（移动 / 迁移都没落成）。
        on_disk = _read_save(sid)
        assert on_disk["atlas"]["party_locus"]["place_id"] == origin, on_disk["atlas"]
        # 内存也回滚：队伍没有移动。
        cached = notdnd_web._sessions.get(sid)
        assert cached is not None and cached._locus["place_id"] == origin, \
            (cached and cached._locus)

        # 故障解除后重试：真的走过去，且落盘。
        status, moved, _raw, _heads = server.post(
            "/api/atlas/move", {"via": target["via"]}, sid=sid)
        assert status == 200 and moved["status"] == "ok", (status, moved)
        on_disk = _read_save(sid)
        assert on_disk["atlas"]["party_locus"]["place_id"] == target["place_id"]

    notdnd_web._sessions.pop(sid, None)


def check_guide_turn_settle_save_failure():
    """结算落盘失败：状态行之前回 500、结算回滚、不叫模型、磁盘无该行动。"""
    sid = "g2-settle-save-fault"
    _guide_session(sid)
    fake = _FakeTransport()
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            with _storage_fault(replace=True):
                status, body, _raw, heads = server.post(
                    "/api/guide/turn",
                    {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
            assert status == 500, (status, body)
            assert body.get("error"), body
            assert "event-stream" not in heads.get("content-type", ""), heads
            assert fake.calls == [], "结算失败不得叫模型"
            cached = notdnd_web._sessions.get(sid)
            assert cached is not None and \
                "a-lock" not in cached.rules.get("done_actions", {}), \
                "结算失败必须回滚内存规则态"

    reloaded = notdnd_web.Session.load(sid)
    assert "a-lock" not in reloaded.rules.get("done_actions", {}), \
        "结算失败不得落盘"
    notdnd_web._sessions.pop(sid, None)


def check_guide_turn_focus_save_failure():
    """`location_id` 焦点落盘失败：状态行之前回 500、guide 回滚、磁盘焦点不变。"""
    sid = "g5-focus-save-fault"
    _guide_session(sid)
    with _LocalServer() as server:
        status, body, _raw, _heads = server.post(
            "/api/guide/bind",
            {"world_key": "yunji", "scenario_id": "yunji"}, sid=sid)
        assert status == 200, (status, body)

        with _storage_fault(replace=True):
            status, body, _raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
        assert status == 500, (status, body)
        assert body.get("error"), body
        assert "event-stream" not in heads.get("content-type", ""), heads
        cached = notdnd_web._sessions.get(sid)
        assert cached is not None and not cached.guide.get("focus_location_id"), \
            "焦点未落盘必须回滚内存"
    reloaded = notdnd_web.Session.load(sid)
    assert not reloaded.guide.get("focus_location_id"), "磁盘不该有未落盘的焦点"
    notdnd_web._sessions.pop(sid, None)


def check_guide_tool_settle_save_failure():
    """工具预通行里的结算落盘失败：不再谎报成功，`done` 明确回报未持久化。"""
    sid = "g4-tool-save-fault"
    _guide_session(sid)
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("request_check",
                                                  {"action_id": "a-lock"})],
         "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            with _storage_fault(replace=True):
                status, _body, raw, _heads = server.post(
                    "/api/guide/turn", {"text": "查规则"}, sid=sid)
    assert status == 200, status
    events = _parse_sse(raw)
    assert [name for name, _ in events] == ["fallback", "done"], events
    assert events[1][1] == {"status": "error", "reason": "save_failed"}, events[1][1]
    # 结算失败已打断工具链：只发了工具那一轮，没有再叫叙事。
    assert len(fake.calls) == 1, fake.calls
    # 那次结算没落盘：磁盘上没有它。
    reloaded = notdnd_web.Session.load(sid)
    assert "a-lock" not in reloaded.rules.get("done_actions", {})
    notdnd_web._sessions.pop(sid, None)


def check_room_create_save_failure():
    """建房落盘失败：500、不进缓存、磁盘上没有可载入的档；恢复后能建房。"""
    rid = "room-save-fault"
    with _LocalServer() as server:
        with _storage_fault(replace=True):
            status, body, _raw, _heads = server.post(
                "/api/room/create", {"room_id": rid, "display_name": "甲"})
        assert status == 500, (status, body)
        assert body.get("error"), body
        assert rid not in notdnd_web._sessions, "半成品不得进缓存"
        assert not notdnd_web._save_exists(rid), "磁盘不得留下半成品存档"
        assert notdnd_web.Session.load(rid) is None, "半成品不得可载入"

        # 故障解除后重试：建房成功并落盘。
        status, body, _raw, _heads = server.post(
            "/api/room/create", {"room_id": rid, "display_name": "甲"})
        assert status == 200, (status, body)
        assert body.get("token") and body.get("role") == "host", body
        assert notdnd_web._save_exists(rid)
    notdnd_web._sessions.pop(rid, None)


# --------------------------------------------------------------------------
# 建局写接口（M4-B1，UI-DESIGN §2.1 / §7 缺口 B1）
# --------------------------------------------------------------------------


def check_session_create_endpoint():
    """`POST /api/session/create`：真起服务，建局 → 拉视图 → 日志（§2.1）。

    断言「建局返回的 sid 立刻可用、且磁盘上真有这一局」，并核对既有
    `X-Session` / `?sid=` 语义没被改动。
    """
    with _Server() as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        save_dir = info["save_dir"]

        status, view = server.post("/api/session/create", {})
        assert status == 200, "建局应 200，实际 %s / %s" % (status, view)
        sid = view.get("sid") or ""
        assert notdnd_web.SID_RE.fullmatch(sid), "sid 必须过白名单：%r" % sid
        assert view.get("status") == "ok", view
        # 新局最小视图与 GET /api/session 同形：空 party / 空场景 / save 块。
        assert view["party"] == [] and view["pressure"] == 0
        assert view["scene"] == {"id": "", "actions": []}
        assert view["save"]["name"] and view["save"]["named"] is False
        assert view["log"] == []

        # 磁盘上真有这一局（不是只在内存里）：新档检查点。
        assert os.path.isfile(os.path.join(save_dir, "campaigns", sid,
                                           "snapshot.json"))

        # 建局成功后：GET /api/session 200、GET /api/log 可用。
        status, got = server.get("/api/session", sid=sid)
        assert status == 200, "新局应能拉视图，实际 %s / %s" % (status, got)
        assert got["sid"] == sid
        status, log = server.get("/api/log", sid=sid)
        assert status == 200, (status, log)
        assert log.get("log") == []

        # 既有 X-Session / ?sid= 语义不变：无 sid 仍 400，?sid= 与 X-Session 等价。
        assert server.get("/api/session")[0] == 400
        status, via_q = server.get("/api/session?sid=%s" % sid)
        assert status == 200 and via_q["sid"] == sid, (status, via_q)

        # 列表里能看到这一局（current 走 X-Session）。
        status, saves = server.get("/api/saves", sid=sid)
        assert status == 200
        assert any(row["id"] == sid for row in saves["saves"]), saves["saves"]
        assert saves["current"] == sid

        # 再建一局：sid 不同（各局独立）。
        status, view2 = server.post("/api/session/create", {})
        assert status == 200, (status, view2)
        assert view2["sid"] != sid

        # 参数非法：畸形 JSON 体 → 400（沿用 _body() 的统一语义）。
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        conn.request("POST", "/api/session/create", body=b"{not json",
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        bad_status, bad_body = response.status, _load_json(response.read())
        conn.close()
        assert bad_status == 400 and bad_body.get("error"), (bad_status, bad_body)


def check_session_create_offline():
    """导引者离线（无 .env / 无密钥）：建局照常成立，进入可玩骨架（§2.1 兜底态）。"""
    saved_env = prism_guide.load_env
    prism_guide.load_env = lambda *_a, **_k: {}
    made = []
    try:
        with _LocalServer() as server:
            status, info = server.get("/api/guide/status")
            assert status == 200, (status, info)
            assert info == {"chat": False, "tts": False, "configured": False}, info

            status, view, _raw, _heads = server.post("/api/session/create", {})
            assert status == 200, "离线建局应 200，实际 %s / %s" % (status, view)
            sid = view["sid"]
            made.append(sid)
            assert view["party"] == [] and view["status"] == "ok"
            assert server.get("/api/session", sid=sid)[0] == 200
            assert server.get("/api/log", sid=sid)[0] == 200
    finally:
        prism_guide.load_env = saved_env
        for sid in made:
            notdnd_web._sessions.pop(sid, None)


def check_session_create_reload():
    """建局落盘 → 服务重启（同存档目录）→ 仍能拉视图 / 日志：往返一致。"""
    with _LocalServer() as server:
        status, view, _raw, _heads = server.post("/api/session/create", {})
        assert status == 200, (status, view)
        sid = view["sid"]
    # 服务实例已关；本进程的内存缓存也要摘掉，才逼出「从磁盘重新载入」。
    notdnd_web._sessions.pop(sid, None)
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None, "建局必须落盘，重启后才能载入"
    assert notdnd_web.rules_view(reloaded)["sid"] == sid
    with _LocalServer() as server:
        status, again = server.get("/api/session", sid=sid)
        assert status == 200, "重启后新局应仍可拉取，实际 %s / %s" % (status, again)
        assert again["sid"] == sid and again["party"] == []
        assert server.get("/api/log", sid=sid)[0] == 200
    notdnd_web._sessions.pop(sid, None)


def check_session_create_save_failure():
    """建局落盘失败：500、不进缓存、磁盘无可载入的档；恢复后能建局（#170 语义）。"""
    with _LocalServer() as server:
        before_sessions = set(notdnd_web._sessions)
        before_ids = _save_ids()
        with _storage_fault(replace=True):
            status, body, _raw, _heads = server.post("/api/session/create", {})
        assert status == 500, "落盘失败应 500，实际 %s / %s" % (status, body)
        assert body.get("error"), body
        assert set(notdnd_web._sessions) == before_sessions, "半成品不得进缓存"
        assert _save_ids() == before_ids, "磁盘不得留下半成品存档"

        # 故障解除后重试：建局成功并落盘。
        status, view, _raw, _heads = server.post("/api/session/create", {})
        assert status == 200, (status, view)
        sid = view["sid"]
        assert sid in _save_ids(), "建局后磁盘上必须真有一档"
        notdnd_web._sessions.pop(sid, None)


# --------------------------------------------------------------------------
# 回合落会话日志（M4-B2，UI-DESIGN §1.3.5 / §7 缺口 B2）
# --------------------------------------------------------------------------


def check_turn_log_wiring():
    """M4-B2：一回合的 `act` / `narrate`（涂掉之后）/ `trace` 进 `Session.log`。

    走 G7 焦点变化回合（会揭开一条痕迹）：核对日志内容与顺序、`?since=` 增量、
    秘密门对日志同样生效、以及重载往返（从磁盘重新载入日志仍在）。
    """
    sid = "b2-turn-log"
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
                {"text": "我走进白壁", "location_id": "loc-02"},
                sid=sid)[0] == 200
        # 回合 C：焦点 loc-02 → loc-05，是真正的「离开」。先埋一条未揭开的痕迹。
        _set_guide(sid, {"traces": [{"at": "loc-02", "id": "npc-01",
                                     "text": "他换了把新锁。", "revealed": False}]})
        fake = _FakeTransport([_BAD_JSON, _BAD_JSON, _BAD_JSON,
                               _sse_reply(narration)])
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我走进灰市", "location_id": "loc-05"}, sid=sid)
        assert status == 200, status
        event = dict(_parse_sse(raw))["narration"]
        assert event["trace"] == "他换了把新锁。", event.get("trace")

        # —— 内容与顺序：每回合 act → narrate，最后一回合多一条 trace ——
        status, body = server.get("/api/log", sid=sid)
        assert status == 200, (status, body)
        entries = body["log"]
        kinds = [e["kind"] for e in entries]
        assert kinds.count("act") == 3 and kinds.count("narrate") == 3, kinds
        assert kinds.count("trace") == 1, kinds
        seqs = [e["seq"] for e in entries]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs
        tail = entries[-3:]
        assert [e["kind"] for e in tail] == ["act", "narrate", "trace"], tail
        assert tail[0]["text"] == "我走进灰市", tail[0]
        # `narrate` 记的就是玩家在 `narration` 事件里看到的**涂掉之后**那段。
        assert tail[1]["text"] == event["text"], (tail[1]["text"], event["text"])
        assert tail[2]["text"] == event["trace"], tail[2]
        # 秘密门对日志同样生效：日志里的叙事不含秘密全文 / 连续 8 码位，仍有「……」。
        assert secret not in tail[1]["text"], tail[1]["text"]
        assert window not in tail[1]["text"], tail[1]["text"]
        assert "……" in tail[1]["text"], tail[1]["text"]

        # —— `?since=` 只回增量 ——
        cursor = tail[0]["seq"] - 1
        status, inc = server.get("/api/log?since=%d" % cursor, sid=sid)
        assert status == 200, (status, inc)
        assert [e["seq"] for e in inc["log"]] == \
            seqs[seqs.index(tail[0]["seq"]):], inc
        # 拉到队尾：没有新条目。
        status, empty = server.get("/api/log?since=%d" % seqs[-1], sid=sid)
        assert status == 200 and empty["log"] == [], empty

    # —— 重载往返：摘掉内存缓存，从磁盘重新载入，日志仍在（回合已落盘） ——
    notdnd_web._sessions.pop(sid, None)
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None, "回合必须把日志落盘"
    assert [e["kind"] for e in reloaded.log] == kinds, reloaded.log
    assert reloaded.log[-1]["text"] == event["trace"], reloaded.log[-1]
    notdnd_web._sessions.pop(sid, None)


def check_turn_log_offline_paths():
    """M4-B2：无 AI 配置 / 短路回合的日志——离线兜底、模块缺失、淡出 / 跳过。"""
    offline = {"BASE_URL": "", "MODEL": "mm", "API_KEY": ""}
    with _LocalServer() as server:
        # (a) 导引者在场但离线：走 SSE 兜底，`narrate` 记兜底句（含裁决），并落盘。
        sid = "b2-log-offline"
        _guide_session(sid)
        fake = _FakeTransport()
        with _guide_online(offline, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        assert status == 200, status
        assert fake.calls == [], "离线不得调用传输"
        events = [name for name, _ in _parse_sse(raw)]
        assert "fallback" in events and "done" in events, events
        status, log = server.get("/api/log", sid=sid)
        assert [e["kind"] for e in log["log"]] == ["act", "narrate"], log["log"]
        narrate = log["log"][1]["text"]
        assert narrate.startswith("【裁决】") and \
            notdnd_web.FALLBACK_NARRATION in narrate, narrate
        # 兜底路径也落了盘：摘缓存重载后日志还在。
        notdnd_web._sessions.pop(sid, None)
        assert [e["kind"] for e in notdnd_web.Session.load(sid).log] == \
            ["act", "narrate"]

        # (b) 淡出 / 跳过整句：非 SSE 的固定 JSON，同样记 `act` + `narrate`。
        sid = "b2-log-fixed"
        _guide_session(sid)
        with _guide_online(offline, _FakeTransport()):
            status, body, _raw, heads = server.post(
                "/api/guide/turn", {"text": "淡出", "action_id": "a-lock"},
                sid=sid)
            assert status == 200, status
            assert body["narration"]["text"] == notdnd_web.GUIDE_FADE_TEXT, body
            assert "event-stream" not in heads.get("content-type", "")
            status, body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "跳过这段"}, sid=sid)
            assert status == 200 and body["narration"]["text"] == \
                notdnd_web.GUIDE_SKIP_TEXT, body
        status, log = server.get("/api/log", sid=sid)
        assert [e["kind"] for e in log["log"]] == \
            ["act", "narrate", "act", "narrate"], log["log"]
        assert [e["text"] for e in log["log"]] == \
            ["淡出", notdnd_web.GUIDE_FADE_TEXT,
             "跳过这段", notdnd_web.GUIDE_SKIP_TEXT], log["log"]

        # (c) 模块缺失的离线兜底：固定 JSON，兜底句照记。
        sid = "b2-log-nomod"
        _guide_session(sid)
        saved_module = notdnd_web.prism_guide
        notdnd_web.prism_guide = None
        try:
            status, body, _raw, heads = server.post(
                "/api/guide/turn", {"text": "我按住门栓", "action_id": "a-lock"},
                sid=sid)
        finally:
            notdnd_web.prism_guide = saved_module
        assert status == 200, status
        assert body["narration"]["text"] == notdnd_web.FALLBACK_NARRATION, body
        assert "event-stream" not in heads.get("content-type", "")
        status, log = server.get("/api/log", sid=sid)
        assert [e["kind"] for e in log["log"]] == ["act", "narrate"], log["log"]
        assert log["log"][0]["text"] == "我按住门栓", log["log"][0]
        assert log["log"][1]["text"] == notdnd_web.FALLBACK_NARRATION, log["log"][1]
    for done in ("b2-log-offline", "b2-log-fixed", "b2-log-nomod"):
        notdnd_web._sessions.pop(done, None)


def check_turn_log_action_only():
    """M4-B2：只点行动按钮（无散文）时，`act` 记该行动的 `label`（§1.3.5）。"""
    sid = "b2-log-actionid"
    _guide_session(sid)
    with _LocalServer() as server:
        with _guide_online(_OFFLINE_ENV, _FakeTransport()):
            status, _body, _raw, _heads = server.post(
                "/api/guide/turn", {"action_id": "a-lock"}, sid=sid)
        assert status == 200, status
        status, log = server.get("/api/log", sid=sid)
        assert [e["kind"] for e in log["log"]] == ["act", "narrate"], log["log"]
        assert log["log"][0]["text"] == "撬锁", log["log"][0]
    notdnd_web._sessions.pop(sid, None)


def check_turn_log_subprocess():
    """M4-B2：真子进程 + 随机端口 + 临时存档目录——离线回合的日志落盘。

    这条不换传输层（离线本来就不出网），所以走**真子进程**，覆盖「从命令行
    启动」这条路径；日志直接读服务端写出的存档 JSON，证明已持久化。
    """
    with _Server(offline=True) as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        save_dir = info["save_dir"]

        status, view = server.post("/api/session/create", {})
        assert status == 200, (status, view)
        sid = view["sid"]

        status, _body = server.post("/api/guide/turn", {"text": "我看看四周"},
                                    sid=sid)
        assert status == 200, "离线回合应 200，实际 %s" % status

        status, log = server.get("/api/log", sid=sid)
        assert status == 200, (status, log)
        kinds = [e["kind"] for e in log["log"]]
        assert kinds == ["act", "narrate"], log["log"]
        assert log["log"][0]["text"] == "我看看四周", log["log"][0]
        assert notdnd_web.FALLBACK_NARRATION in log["log"][1]["text"], log["log"][1]
        seqs = [e["seq"] for e in log["log"]]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs

        # 落盘：直接读服务端写出的存档（不是内存缓存）。
        saved = _read_save(sid, save_dir)
        assert [e["kind"] for e in saved["log"]] == kinds, saved["log"]
        assert saved["log"][0]["text"] == "我看看四周", saved["log"][0]


# --------------------------------------------------------------------------
# 行动管线写接口（M8-A3，ACTION-DESIGN §3.3 / §7 / §8 / §9）
# --------------------------------------------------------------------------


def _seed_action_session(save_dir: str, sid: str, *, risk: str = "R1",
                         party: bool = True, combat: bool = False,
                         action_id: str = "a-lock", label: str = "撬锁") -> None:
    """在**指定存档目录**里造一局带一条行动的存档（子进程服务的目录也认）。

    `Session.save()` 读模块级 `SAVE_DIR`，所以先把常量指到目标目录再落盘——
    真子进程服务随后从磁盘载入这一局，新路由的「从命令行启动」路径也因此被
    覆盖。默认写成 R1（直通，不弹确认，§6.4）；要测确认就传 `risk="R3"`。
    """
    saved_dir = notdnd_web.SAVE_DIR
    notdnd_web.SAVE_DIR = pathlib.Path(save_dir)
    try:
        session = notdnd_web.Session(sid)
        rules = prism_core.RuleSession(sid)
        if party:
            rules.party.append(prism_core.new_unit(
                "u-1", "试炼者", attributes={"MGT": 6, "INS": 7}))
        rules.scene = {"id": "sc-1", "actions": [
            {"id": action_id, "label": label, "kind": "check", "df": 12,
             "auto_pass": True, "on_pass": "锁簧弹开。", "risk": risk}]}
        if combat:
            rules.combat = {"over": False}
        session.rules = rules.snapshot()
        session.save()
    finally:
        notdnd_web.SAVE_DIR = saved_dir
    notdnd_web._sessions.pop(sid, None)     # 丢掉本进程缓存，逼出真实读盘


def _post(server, path, body, sid="", headers=None):
    """`_Server`（子进程）与 `_LocalServer`（本进程）统一的 POST：(状态, JSON)。"""
    out = server.post(path, body, sid=sid, headers=headers)
    return out[0], out[1]


def _propose_action(server, sid, action_id="a-lock"):
    """显式行动的 `propose`（§9.2 `mode: "action"`）：返回 (状态, 回执)。"""
    return _post(server, "/api/action/propose",
                 {"mode": "action", "action_id": action_id}, sid)


def _commit_body(proposal, base_seq, confirm_token="", **extra):
    """`commit` 的请求体（§9.3）：提案 + 并发基线 +（R2–R4 的）确认令牌。"""
    body = {"proposal": proposal, "base_seq": base_seq}
    if confirm_token:
        body["confirm_token"] = confirm_token
    body.update(extra)
    return body


def _raw_status(raw: bytes) -> int:
    """裸响应第一行的 HTTP 状态码（`_raw_http` 专用）。"""
    try:
        return int(raw.split(b"\r\n", 1)[0].split(b" ")[1])
    except (IndexError, ValueError):
        return 0


def _raw_json(raw: bytes) -> dict:
    """裸响应里的 JSON 体（`_raw_http` 专用）。"""
    _head, _sep, rest = raw.partition(b"\r\n\r\n")
    body = _dechunk(rest) if b"chunked" in _head.lower() else rest
    return _load_json(body)


def check_action_catalog_and_offline_round():
    """M8-A3：**真子进程 + 离线**——catalog / propose / commit 走完一轮。

    离线（无 AI 配置）也走得通（§7.1 离线底线）：`mode: "action"` 的第 3–5 段
    一行模型都不调；`result` 一定早于叙事段（§3.3 硬边界），离线时叙事段是
    `fallback` + 兜底句（§7.3），整条流只有**一行** HTTP 状态（§9.5）。
    """
    sid = "a3-offline-round"
    with _Server(offline=True) as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        _seed_action_session(info["save_dir"], sid)

        status, catalog = server.get("/api/action/catalog", sid=sid)
        assert status == 200, (status, catalog)
        assert catalog["actions"] == [{"id": "a-lock", "label": "撬锁",
                                       "risk": "R1"}], catalog

        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        assert prop["type"] == "proposal" and prop["risk"] == "R1", prop
        assert prop["needs_confirm"] is False, prop      # R1 直通（§6.4）
        assert prop["base_seq"] == 0, prop               # 还没提交过：基线 0
        assert prop["costs_preview"] == [], prop         # 行动表没给时间成本
        proposal = prop["proposal"]
        assert proposal["source"] == "action", proposal
        assert proposal["actor"] == {"kind": "player", "id": "u-1"}, proposal
        assert prism_core.validate_proposal(proposal) == [], proposal

        raw = _raw_http(
            server.port, "/api/action/commit",
            _commit_body(proposal, prop["base_seq"]), sid=sid,
            headers={"Idempotency-Key": "a3-offline-key-0001"})
        assert _status_lines(raw) == 1, "整条流只允许一行状态：%r" % raw[:160]
        events = _parse_sse(_dechunk(raw))
        names = [name for name, _payload in events]
        assert names[0] == "result", names
        assert names[1] in ("fallback", "narration"), names
        assert names[-1] == "done", names
        result = events[0][1]
        assert result["status"] == "success", result
        assert result["committed"] is True, result
        assert len(result["event_ids"]) == 1, result        # A0：一次提交一条事件
        assert result["commit_ref"]["seq_from"] == 1, result
        assert result["commit_ref"]["seq_to"] == 1, result
        fallback = dict(events[1][1])
        assert fallback["status"] == "offline", fallback
        assert fallback["action_id"] == result["action_id"], fallback
        assert fallback["seq_to"] == 1, fallback
        assert notdnd_web.FALLBACK_NARRATION in fallback["text"], fallback

        # 规则层真结算了：`done_actions` 进了磁盘上的存档（离线也要真改世界）
        saved = _read_save(sid, info["save_dir"])
        assert "a-lock" in saved["rules"]["done_actions"], saved["rules"]
        # 会话日志：玩家做了什么 + 玩家实际看到的正文
        status, log = server.get("/api/log", sid=sid)
        assert status == 200, (status, log)
        assert [entry["kind"] for entry in log["log"]] == ["act", "narrate"], log


def check_action_subprocess_error_rows():
    """新路由在**真子进程**下的固定错误（§9.5）：覆盖命令行启动路径。"""
    sid = "a3-subprocess"
    with _Server(offline=True) as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        _seed_action_session(info["save_dir"], sid)
        # 会话不存在 → 400（不是 404「未知接口」：路径确实接上了）
        status, body = _post(server, "/api/action/propose",
                             {"mode": "action", "action_id": "a-lock"}, "s-nope")
        assert (status, body.get("error")) == (400, "会话不存在或已过期"), (status, body)
        status, body = server.get("/api/action/catalog", sid="s-nope")
        assert (status, body.get("error")) == (400, "会话不存在或已过期"), (status, body)
        # 行动不在当前场景 → 404 没有这个行动
        status, body = _propose_action(server, sid, "no-such-action")
        assert (status, body.get("error")) == (404, "没有这个行动"), (status, body)
        # commit 缺幂等键 → 400（真跑一次写接口）
        status, body = _post(server, "/api/action/commit",
                             {"proposal": {}, "base_seq": 0}, sid)
        assert (status, body.get("error")) == (400, "幂等键不合法"), (status, body)
        # 提案形状过不了重验 → 404 没有这个提案
        status, body = _post(server, "/api/action/commit",
                             {"proposal": {"nope": True}, "base_seq": 0}, sid,
                             headers={"Idempotency-Key": "a3-subprocess-key-0001"})
        assert (status, body.get("error")) == (404, "没有这个提案"), (status, body)


def check_action_nl_module_missing():
    """导引者缺失：`mode: "nl"` 回 200 `type: "offline"`，显式行动照常走完（§9.2）。"""
    sid = "a3-nl-absent"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    saved_guide = notdnd_web.prism_guide
    notdnd_web.prism_guide = None
    try:
        with _LocalServer() as server:
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl", "text": "我看看"}, sid)
            assert (status, body) == (200, {"status": "offline",
                                            "type": "offline"}), (status, body)
            # 显式行动不需要 AI：catalog + propose + 第 3–5 段照常
            status, catalog = server.get("/api/action/catalog", sid=sid)
            assert status == 200 and catalog["actions"][0]["id"] == "a-lock", \
                (status, catalog)
            status, prop = _propose_action(server, sid)
            assert status == 200, (status, prop)
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-absent-key-0001"})
            assert status == 200, (status, body)
            events = _parse_sse(raw)
            assert [name for name, _payload in events] == ["result", "fallback",
                                                          "done"], events
            assert events[0][1]["committed"] is True, events[0]
            assert events[1][1]["status"] == "offline", events[1]
    finally:
        notdnd_web.prism_guide = saved_guide
    notdnd_web._sessions.pop(sid, None)


def check_action_nl_translation_and_rate():
    """`mode: "nl"`：澄清回路 / 拒绝 / 速率的 HTTP 翻译（§6.3 / §9.2 / §9.5）。"""
    sid = "a3-nl"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    clarify_out = {"clarify_id": "c_" + "1" * 32, "missing": "target",
                   "question": "你要说服谁？",
                   "options": [{"id": "npc-01", "label": "守卫"},
                               {"id": "npc-02", "label": "队长"}],
                   "proposal_draft": {"intent": {"verb": "a-lock"}}}
    calls = []
    proposal_out = {}

    def fake_propose(rules, guide, text, *, clarify=None, answer="", env=None,
                     transmit=None):
        """替身：第一次给澄清，带澄清 id 的第二次给提案，其后一律拒绝。"""
        calls.append({"text": text, "clarify": clarify, "answer": answer})
        if len(calls) == 1:
            return {"status": "ok", "type": "clarify", "clarify": clarify_out}
        if clarify is not None:
            return {"status": "ok", "type": "proposal",
                    "proposal": proposal_out["proposal"]}
        return {"status": "reject", "type": "reject", "reason": "做不了这件事"}

    saved_propose = prism_guide.propose_nl
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        proposal_out["proposal"] = prop["proposal"]
        prism_guide.propose_nl = fake_propose
        try:
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl", "text": "我想说服守卫"}, sid)
            assert (status, body["type"]) == (200, "clarify"), (status, body)
            assert body["clarify"]["clarify_id"] == clarify_out["clarify_id"], body
            # 带着澄清 id 回答：服务端取回那一次澄清，把回答并入草案（§6.3）
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl",
                                  "clarify_id": clarify_out["clarify_id"],
                                  "answer": "npc-01"}, sid)
            assert (status, body["type"]) == (200, "proposal"), (status, body)
            assert calls[1]["clarify"] == clarify_out, calls[1]
            assert calls[1]["answer"] == "npc-01", calls[1]
            assert calls[1]["text"] == "我想说服守卫", calls[1]   # 沿用第一次原文
            # 澄清不消耗资源、不落世界事件：基线还是 0
            assert body["base_seq"] == 0, body
            # 澄清不存在 / 过期 → 404 没有这个提案（§9.5）
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl", "text": "我看看",
                                  "clarify_id": "c_" + "9" * 32}, sid)
            assert (status, body.get("error")) == (404, "没有这个提案"), (status, body)
            # 拒绝 → §9.5 的固定短语 + 403
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl", "text": "我要屠龙"}, sid)
            assert (status, body.get("error")) == (403, "做不了这件事"), (status, body)
            # 速率：每会话每 60 秒 30 次 propose，第 31 次起 429
            limited = None
            for _ in range(40):
                status, body = _post(server, "/api/action/propose",
                                     {"mode": "nl", "text": "再看看"}, sid)
                if status == 429:
                    limited = body
                    break
            assert limited is not None, "第 31 次 propose 必须被限流"
            assert limited.get("error") == "太频繁", limited
        finally:
            prism_guide.propose_nl = saved_propose
    notdnd_web._sessions.pop(sid, None)


def check_action_commit_errors_and_confirm():
    """`commit` 状态行**之前**的固定错误 + R2–R4 确认回路（§9.4 / §9.5）。"""
    sid = "a3-commit-errors"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, risk="R3")
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        assert prop["risk"] == "R3" and prop["needs_confirm"] is True, prop
        proposal = prop["proposal"]
        base_seq = prop["base_seq"]

        def expect(code, error, body, key="a3-err-key-0001", sid_=sid):
            """一次 commit：恰好一行 HTTP 状态 + 固定短语。"""
            raw = _raw_http(server.port, "/api/action/commit", body, sid=sid_,
                            headers={"Idempotency-Key": key})
            assert _status_lines(raw) == 1, raw[:160]
            assert _raw_status(raw) == code, (code, raw[:200])
            assert (_raw_json(raw).get("error") or "") == error, _raw_json(raw)
            return raw

        body = _commit_body(proposal, base_seq)
        # ① 会话不存在 / 过期 → 400（四条路由同一口径）
        expect(400, "会话不存在或已过期", body, sid_="s-does-not-exist")
        # ② 幂等键缺失 / 非法 → 400（缺失用裸 socket，绕开客户端的头）
        raw = _raw_http(server.port, "/api/action/commit", body, sid=sid)
        assert _raw_status(raw) == 400, raw[:160]
        assert _raw_json(raw)["error"] == "幂等键不合法", _raw_json(raw)
        expect(400, "幂等键不合法", body, key="short")
        expect(400, "幂等键不合法", body, key="x" * 65)
        # ③ 请求体畸形 / 超限 → 400（§9.5）
        malformed = _raw_body_http(server.port, "/api/action/commit", b"{not json",
                                   sid=sid,
                                   headers={"Idempotency-Key": "a3-err-key-0002"})
        assert _raw_status(malformed) == 400, malformed[:160]
        assert _raw_json(malformed)["error"] == "JSON 解析失败", _raw_json(malformed)
        oversize = _raw_body_http(server.port, "/api/action/commit", b"",
                                  sid=sid,
                                  headers={"Idempotency-Key": "a3-err-key-0003"},
                                  content_length=2_000_001)
        assert _raw_status(oversize) == 400, oversize[:160]
        assert _raw_json(oversize)["error"] == "请求体过大", _raw_json(oversize)
        # ④ 提案不存在 / 更高 schema_version / 未知行动 / 实体引用无效
        expect(404, "没有这个提案", _commit_body({"not": "a proposal"}, base_seq))
        expect(409, "版本不支持",
               _commit_body(dict(proposal, schema_version=2), base_seq))
        unknown = dict(proposal, proposal_id="p_" + "2" * 32,
                       intent=dict(proposal["intent"], verb="no-such-action"))
        expect(404, "没有这个行动", _commit_body(unknown, base_seq))
        bad_target = dict(proposal, proposal_id="p_" + "3" * 32,
                          intent=dict(proposal["intent"],
                                      target={"kind": "npc", "id": "npc-404"}))
        expect(404, "目标不存在", _commit_body(bad_target, base_seq))
        # ⑤ R3 缺确认令牌 / 令牌不对 / 令牌已失效 → 409 需要确认
        expect(409, "需要确认", body)
        expect(409, "需要确认",
               _commit_body(proposal, base_seq, confirm_token="cf_" + "0" * 32))
        status, confirmed = _post(server, "/api/action/confirm",
                                  {"proposal_id": proposal["proposal_id"]}, sid)
        assert status == 200, (status, confirmed)
        token = confirmed["confirm_token"]
        assert notdnd_web.ACTION_CONFIRM_TOKEN_RE.fullmatch(token), confirmed
        expect(409, "需要确认",
               _commit_body(proposal, base_seq, confirm_token="cf_" + "1" * 32))
        with notdnd_web._ACTION_LOCK:       # 把有效期拨到过去 = 确认已失效
            notdnd_web._action_confirms[(sid, proposal["proposal_id"])][
                "expires_at"] = 0
        expect(409, "需要确认",
               _commit_body(proposal, base_seq, confirm_token=token))
        # ⑥ 有了有效令牌后：旧 base_seq → 409 状态已变，请重试（不结算、不落事件）
        status, again = _post(server, "/api/action/confirm",
                              {"proposal_id": proposal["proposal_id"]}, sid)
        assert status == 200, (status, again)
        expect(409, "状态已变，请重试",
               _commit_body(proposal, base_seq + 7,
                            confirm_token=again["confirm_token"]))
        # ⑦ 取消：丢令牌——「取消」就是不提交，带旧令牌再来仍是 409
        status, cancelled = _post(server, "/api/action/confirm",
                                  {"proposal_id": proposal["proposal_id"],
                                   "decision": "cancel"}, sid)
        assert (status, cancelled["type"]) == (200, "cancelled"), (status, cancelled)
        expect(409, "需要确认",
               _commit_body(proposal, base_seq,
                            confirm_token=again["confirm_token"]))
        # ⑧ 确认不存在 / 过期 → 404 没有这个提案（提案 id 形状不对也算没有）
        status, body2 = _post(server, "/api/action/confirm",
                              {"proposal_id": "bogus"}, sid)
        assert (status, body2.get("error")) == (404, "没有这个提案"), (status, body2)
        # ⑨ 重新确认 + 正确基线 → 提交成功（机制没被前面的错误路径卡死）
        status, final = _post(server, "/api/action/confirm",
                              {"proposal_id": proposal["proposal_id"]}, sid)
        status, body3, raw3, _heads = server.post(
            "/api/action/commit",
            _commit_body(proposal, base_seq,
                         confirm_token=final["confirm_token"]), sid=sid,
            headers={"Idempotency-Key": "a3-err-key-0009"})
        assert status == 200, (status, body3)
        assert _parse_sse(raw3)[0][1]["committed"] is True, raw3[:300]
        # 同一提案 + 新键 → 409 该提案已提交（§9.4 提案级幂等）
        status, body4 = _post(server, "/api/action/commit",
                              _commit_body(proposal, base_seq,
                                           confirm_token=final["confirm_token"]),
                              sid, headers={"Idempotency-Key": "a3-err-key-0010"})
        assert (status, body4.get("error")) == (409, "该提案已提交"), (status, body4)
    notdnd_web._sessions.pop(sid, None)


def check_action_commit_combat_lock_and_server_error():
    """战斗锁 → 409；钩子炸了 → 500 且**不开** SSE（§9.5 末段）。"""
    sid = "a3-combat"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, combat=True)
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)        # propose 不结算（§7.5）
        raw = _raw_http(server.port, "/api/action/commit",
                        _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                        headers={"Idempotency-Key": "a3-combat-key-0001"})
        assert _status_lines(raw) == 1 and _raw_status(raw) == 409, raw[:200]
        assert _raw_json(raw)["error"] == "战斗还没结束", _raw_json(raw)
        assert b"event: result" not in raw, "状态行已写 409 就不再开 SSE"
    notdnd_web._sessions.pop(sid, None)

    sid = "a3-server-error"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    events_path = _campaign_file(sid, "events.ndjson")

    class BoomHook:
        def enqueue(self, proposal, draft, *, base_seq):
            raise RuntimeError("钩子炸了")

        def cancel(self, task_id):
            return False

    saved_hook = notdnd_web.SCHEDULE_HOOK
    notdnd_web.SCHEDULE_HOOK = BoomHook()
    try:
        with _LocalServer() as server:
            status, prop = _propose_action(server, sid)
            raw = _raw_http(server.port, "/api/action/commit",
                            _commit_body(prop["proposal"], prop["base_seq"]),
                            sid=sid,
                            headers={"Idempotency-Key": "a3-boom-key-0001"})
            assert _status_lines(raw) == 1 and _raw_status(raw) == 500, raw[:200]
            assert _raw_json(raw)["error"].startswith("服务器内部错误："), \
                _raw_json(raw)
            assert b"event: result" not in raw, "500 之后不许再开 SSE"
            assert open(events_path, encoding="utf-8").read().strip() == "", \
                "钩子失败时不该落任何事件"
    finally:
        notdnd_web.SCHEDULE_HOOK = saved_hook
    notdnd_web._sessions.pop(sid, None)


def check_action_schedule_hook():
    """排程钩子（§8.2 / §8.3）：空实现回的键固定六个；管线恰好调一次。"""
    empty = notdnd_web._SyncOnlySchedule()
    hook_out = empty.enqueue({"proposal_id": "p_x"}, {"draft": True}, base_seq=3)
    assert set(hook_out) == {"mode", "task_id", "start_at", "expected_end_at",
                             "barrier", "resume_token"}, hook_out
    assert hook_out == {"mode": "sync", "task_id": None, "start_at": 0,
                        "expected_end_at": 0, "barrier": False,
                        "resume_token": None}, hook_out
    assert empty.cancel("t-1") is False, "M8 的同步行动不可取消（§8.3）"

    sid = "a3-hook"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    calls = []

    class FakeHook:
        """假钩子：记录调用（深拷贝入参，管线之后还会改草案）。"""

        def enqueue(self, proposal, draft, *, base_seq):
            calls.append({"proposal": json.loads(json.dumps(proposal)),
                          "draft": json.loads(json.dumps(draft)),
                          "base_seq": base_seq})
            return empty.enqueue(proposal, draft, base_seq=base_seq)

        def cancel(self, task_id):
            return False

    saved_hook = notdnd_web.SCHEDULE_HOOK
    notdnd_web.SCHEDULE_HOOK = FakeHook()
    try:
        with _LocalServer() as server:
            status, prop = _propose_action(server, sid)
            assert status == 200, (status, prop)
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-hook-key-0001"})
            assert status == 200, (status, body)
            assert _parse_sse(raw)[0][1]["committed"] is True, raw[:300]
    finally:
        notdnd_web.SCHEDULE_HOOK = saved_hook
    assert len(calls) == 1, "一次提交只排一次，实际 %d 次" % len(calls)
    call = calls[0]
    assert call["proposal"]["proposal_id"] == prop["proposal"]["proposal_id"], call
    assert call["base_seq"] == prop["base_seq"], call
    # 钩子在**提交之前**收到草案：未提交、无事件引用
    assert call["draft"]["committed"] is False, call["draft"]
    assert call["draft"]["commit_ref"] is None, call["draft"]
    assert prism_core.validate_result(call["draft"]) == [], call["draft"]
    notdnd_web._sessions.pop(sid, None)


def check_action_idempotency():
    """幂等（§7.7 / §9.4）：同键重放回首次结果、不写新事件；换键 → 409。"""
    sid = "a3-idem"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    events_path = _campaign_file(sid, "events.ndjson")

    def event_lines() -> int:
        with open(events_path, encoding="utf-8") as handle:
            return len([line for line in handle if line.strip()])

    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        proposal, base_seq = prop["proposal"], prop["base_seq"]
        body = _commit_body(proposal, base_seq)
        status, head, raw, _heads = server.post(
            "/api/action/commit", body, sid=sid,
            headers={"Idempotency-Key": "a3-idem-key-0001"})
        assert status == 200, (status, head)
        first = _parse_sse(raw)[0][1]
        lines_after_first = event_lines()
        assert lines_after_first == 1, "首次提交应恰一条事件"
        # 同键同载荷（连 base_seq 已过期也照旧）：重放，不结算、不写事件
        status, head, raw, _heads = server.post(
            "/api/action/commit", body, sid=sid,
            headers={"Idempotency-Key": "a3-idem-key-0001"})
        assert status == 200, (status, head)
        replayed = _parse_sse(raw)
        assert [name for name, _payload in replayed] == ["result", "done"], replayed
        assert replayed[0][1] == first, replayed[0][1]
        assert replayed[-1][1] == {"status": "ok", "replayed": True}, replayed[-1]
        assert event_lines() == lines_after_first, "重放不得写新事件"
        # 换个键再提同一份提案 → 409 该提案已提交（防「换个键再赌一次」）
        status, body2, _raw, _heads = server.post(
            "/api/action/commit", body, sid=sid,
            headers={"Idempotency-Key": "a3-idem-key-0002"})
        assert (status, body2.get("error")) == (409, "该提案已提交"), (status, body2)
        # 新提案 + 新键 → 正常提交；同一行动第二次走 already_done（不重复发奖）
        status, prop2 = _propose_action(server, sid)
        assert status == 200, (status, prop2)
        assert prop2["base_seq"] == first["commit_ref"]["seq_to"], prop2
        status, head2, raw2, _heads = server.post(
            "/api/action/commit",
            _commit_body(prop2["proposal"], prop2["base_seq"]), sid=sid,
            headers={"Idempotency-Key": "a3-idem-key-0003"})
        assert status == 200, (status, head2)
        second = _parse_sse(raw2)[0][1]
        assert second["status"] == "success", second
        assert second["rule_ref"]["perf"] == "already_done", second["rule_ref"]
        assert second["commit_ref"]["seq_to"] == 2, second["commit_ref"]
        assert event_lines() == 2, "第二次提交是**新**提案：各得一条事件"
    notdnd_web._sessions.pop(sid, None)


def check_action_narration_and_frozen_snapshot():
    """第 6 段（§7.2 / §7.5）：在线叙事用提交后快照重拼，帧带 action_id / seq_to。"""
    sid = "a3-narrate"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    fake = _FakeTransport()
    with _guide_online(_ONLINE_ENV, fake):
        with _LocalServer() as server:
            status, prop = _propose_action(server, sid)
            assert status == 200, (status, prop)
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-narrate-key-0001"})
            assert status == 200, (status, body)
            events = _parse_sse(raw)
            names = [name for name, _payload in events]
            assert names == ["result", "narration", "usage", "done"], names
            result = events[0][1]
            narration = events[1][1]
            assert result["committed"] is True, result
            assert narration["action_id"] == result["action_id"], narration
            assert narration["seq_to"] == result["commit_ref"]["seq_to"], narration
            assert narration["text"].strip(), narration
            assert narration["beats"], narration
            assert events[2][1].get("prompt_tokens") == 7, events[2]
            # 叙事请求只发一次，且 L4 带的是**已提交**的判定与行动菜单（§7.2）
            assert len(fake.calls) == 1, fake.calls
            messages = fake.calls[0]["payload"]["messages"]
            l4 = messages[-1]["content"]
            assert "判定：" in l4 and result["action_id"] in l4, l4
            assert "行动菜单：a-lock 撬锁" in l4, l4
    # 冻结快照是**值快照**：提交之后世界再变，副本不受影响（§7.5）
    session = notdnd_web.Session.load(sid)
    snapshot = notdnd_web._action_snapshot(session.rules, session.guide)
    session.rules["scene"]["actions"].append({"id": "a-extra", "label": "加戏"})
    assert [item["id"] for item in snapshot["rules"]["scene"]["actions"]] == \
        ["a-lock"], snapshot["rules"]["scene"]["actions"]
    notdnd_web._sessions.pop(sid, None)


def check_action_narration_failure_keeps_result():
    """叙事失败 → `fallback`，`result` 仍已提交、不回滚（§7.3 / §7.7）。"""
    sid = "a3-narrate-fail"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    fake = _FakeTransport([RuntimeError("上游断线")])
    with _guide_online(_ONLINE_ENV, fake):
        with _LocalServer() as server:
            status, prop = _propose_action(server, sid)
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-narrate-fail-key-0001"})
            assert status == 200, (status, body)
            events = _parse_sse(raw)
            names = [name for name, _payload in events]
            assert names == ["result", "fallback", "done"], names
            assert events[0][1]["committed"] is True, events[0]
            assert notdnd_web.FALLBACK_NARRATION in events[1][1]["text"], events[1]
            assert events[-1][1] == {"status": "ok"}, events[-1]
            assert "a-lock" in _read_save(sid)["rules"]["done_actions"], "结果不得回滚"
    notdnd_web._sessions.pop(sid, None)


def check_action_save_failure_keeps_result():
    """已提交的结果不回滚（§7.7）：门面落盘失败只让 `done` 如实回报。

    这也是「状态行前 / 后行为分叉」的后半：状态行已经写出，失败只能走 SSE——
    绝不允许出现第二行 HTTP 状态。
    """
    sid = "a3-save-failure"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        with _storage_fault(replace=True):
            raw = _raw_http(
                server.port, "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-save-key-0001"})
        assert _status_lines(raw) == 1, "落盘失败也不许发第二行状态：%r" % raw[:160]
        events = _parse_sse(_dechunk(raw))
        names = [name for name, _payload in events]
        assert names[0] == "result", names
        assert events[0][1]["committed"] is True, events[0]
        assert events[-1] == ("done", {"status": "error",
                                       "reason": "save_failed"}), events[-1]
    # 事件已经落盘：从磁盘重放（检查点 + 事件日志）仍能看到这次提交
    notdnd_web._sessions.pop(sid, None)
    reloaded = notdnd_web.Session.load(sid)
    assert "a-lock" in reloaded.rules["done_actions"], reloaded.rules["done_actions"]
    notdnd_web._sessions.pop(sid, None)


def check_action_commit_json_accept():
    """`Accept: application/json` → 非流式整段结果，没有 SSE（§9.3）。"""
    sid = "a3-json"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        status, head, raw, heads = server.post(
            "/api/action/commit",
            _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
            headers={"Idempotency-Key": "a3-json-key-0001",
                     "Accept": "application/json"})
        assert status == 200, (status, head)
        assert "text/event-stream" not in heads.get("content-type", ""), heads
        assert raw.lstrip().startswith(b"{"), raw[:80]      # 整段 JSON，不是 SSE
        assert head["status"] == "success" and head["committed"] is True, head
        assert head["commit_ref"]["seq_to"] == 1, head
    notdnd_web._sessions.pop(sid, None)


def check_action_precondition_no_party():
    """桌上还没有角色 → 409 `现在做不了`（§6.4 前置条件面，§9.5）。

    两条路径都要拦：`mode: "action"` 的提案构造（没有行动者可归属），以及
    `commit` 的回带重验（提案里的行动者不在本局队伍里）。
    """
    sid = "a3-no-party"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, party=False)
    with _LocalServer() as server:
        status, body = _propose_action(server, sid)
        assert (status, body.get("error")) == (409, "现在做不了"), (status, body)
    # 队伍里有角色：先拿一份合法提案，再把行动者换成不存在的 id 回带
    sid = "a3-ghost-actor"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        ghost = dict(prop["proposal"], proposal_id="p_" + "4" * 32,
                     actor={"kind": "player", "id": "u-ghost"})
        status, body = _post(server, "/api/action/commit",
                             _commit_body(ghost, prop["base_seq"]), sid,
                             headers={"Idempotency-Key": "a3-ghost-key-0001"})
        assert (status, body.get("error")) == (409, "现在做不了"), (status, body)
    notdnd_web._sessions.pop(sid, None)


def check_legacy_migration_adds_chron_block():
    """M9-C5：老档（无 `chron` 块）迁移后自带一块**空** CHRON 块，且 CHRON 读得动。

    `_LEGACY_DEFAULTS` 的「默认值工厂」是网页层唯一该管 CHRON 的地方：块形状归
    `chron.new_block()`（本文件不另写一份字段名），老档缺块时由迁移补上；后续 save
    只按块名覆盖 `rules` / `atlas` / `guide` / `room`，**不得**把这块抹掉（state.stage
    的按块合并语义）。
    """
    sid = "legacy-chron-1"
    _write_save(_save_path(sid), {"sid": sid, "created": 1700000000.0, "log": [], "seq": 0,
                                  "save_name": "老档"})
    session = notdnd_web.Session.load(sid)
    assert session is not None, "老档必须能载入"
    session.save()                                          # 第一次 save 落成新档
    assert not os.path.exists(_save_path(sid)), "迁移成功后老档应被删掉"

    handle = state.open_campaign(sid, base_dir=str(notdnd_web.SAVE_DIR))
    assert handle.view()["blocks"]["chron"] == chron.new_block(), handle.view()["blocks"]
    restored = chron.restore(handle)                        # C5 的加载路径读得动
    assert restored["block"] == chron.new_block(0, seq=handle.view()["seq"]), restored["block"]
    assert restored["block"]["actions"] == {} and restored["block"]["idempotency"] == {}
    assert restored["world_minute"] == 0

    # 再存一次：门面不改这块，engine 的按块合并也必须保住它。
    notdnd_web._sessions.pop(sid, None)
    again = notdnd_web.Session.load(sid)
    assert again is not None
    again.add_log("narrative", "重启后的一拍")
    again.save()
    after = state.open_campaign(sid, base_dir=str(notdnd_web.SAVE_DIR))
    assert after.view()["blocks"]["chron"] == chron.new_block(), \
        "后续 save 不得动 CHRON 块：%r" % (after.view()["blocks"]["chron"],)


CHECKS = (
    check_import_has_no_side_effects,
    check_roundtrip_snapshot,
    check_session_save_reports_failure,
    check_save_rename_failure_rolls_back,
    check_legacy_save_lazy_migration,
    check_state_wiring_new_layout,
    check_legacy_migration_on_first_save,
    check_legacy_migration_adds_chron_block,
    check_save_list_and_delete_new_layout,
    check_api_session_view,
    check_api_session_without_session_400,
    check_atlas_snapshot_roundtrip,
    check_atlas_legacy_save_lazy_compile,
    check_atlas_relocation_on_deleted_place,
    check_api_atlas_exits_and_move,
    check_atlas_move_save_failure_rolls_back,
    check_guide_block_wiring,
    check_guide_load_contract,
    check_guide_status_endpoint,
    check_guide_turn_validation,
    check_guide_turn_settle_stream,
    check_guide_turn_stream_failure,
    check_guide_turn_review_failure_keeps_verdict,
    check_guide_turn_settle_errors_before_stream,
    check_guide_turn_settle_save_failure,
    check_guide_missing_module,
    check_guide_tool_prepass_requests,
    check_guide_tool_prepass_rebuilds_l4,
    check_guide_tool_error_never_new_status,
    check_guide_tool_settle_save_failure,
    check_guide_narrative_tool_calls_no_second_round,
    check_guide_turn_writes_beats,
    check_guide_speak_validation,
    check_guide_speak_stream,
    check_guide_speak_rate_limit,
    check_guide_bind_endpoint,
    check_guide_bind_no_module,
    check_guide_turn_location_focus,
    check_guide_turn_focus_save_failure,
    check_guide_turn_traces_wiring,
    check_guide_turn_traces_betrayed,
    check_guide_turn_traces_offline,
    check_guide_turn_scene_change_traces,
    check_room_create_join_leave,
    check_room_create_save_failure,
    check_room_legacy_lazy_migration,
    check_room_capacity_full,
    check_room_kick_authorization,
    check_room_kick_host_check_is_atomic,
    check_room_auth_chain,
    check_session_create_endpoint,
    check_session_create_offline,
    check_session_create_reload,
    check_session_create_save_failure,
    check_turn_log_wiring,
    check_turn_log_offline_paths,
    check_turn_log_action_only,
    check_turn_log_subprocess,
    check_action_catalog_and_offline_round,
    check_action_subprocess_error_rows,
    check_action_nl_module_missing,
    check_action_nl_translation_and_rate,
    check_action_precondition_no_party,
    check_action_commit_errors_and_confirm,
    check_action_commit_combat_lock_and_server_error,
    check_action_schedule_hook,
    check_action_idempotency,
    check_action_narration_and_frozen_snapshot,
    check_action_narration_failure_keeps_result,
    check_action_save_failure_keeps_result,
    check_action_commit_json_accept,
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
