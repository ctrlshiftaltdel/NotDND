#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 冒烟：起服务 → 无头浏览器打开首页 → 检查几条最便宜的不变量。

只回答一个问题：「首页还能不能用」。四条断言都针对「整页级」的故障，
不碰任何具体交互（那些交给各自的专项用例）：

  1. 首页加载完成（document.readyState 到 complete）；
  2. `window.__ERRS` 为空——前端把**未捕获错误**与**未处理的 Promise 拒绝**
     都收集到这个数组里，非空即代表页面在初始化期就抛了错；
  3. 至少有一个 `.view` 处在 `is-on` 状态——视图系统靠这个 class 决定
     哪个界面可见，一个都没点亮说明页面白屏；
  4. 页面不发起任何**外部**（跨源）资源请求——前端约定「零外链」，
     不能出现 CDN 脚本、外链样式、Web Font、外链图片。同源请求
     （自己的 `/css/*` `/js/*` `/api/*`）不算，浏览器自动要的
     `/favicon.ico` 也是同源。

在以上整页级不变量之外，另加一组针对「跨片写动作单一在途」（§2.0）的
断言：用 fetch 桩制造一个可断言的写请求在途窗口，验证 core 统一守卫在
在途时**拒绝第二次写**、不扰动在途状态，且第一次写完成后守卫恢复。

## 本地跳过 vs CI 强制执行

前置条件（`static/index.html`、WebSocket 客户端、可用浏览器）任一缺失时：

  · **默认（本地）**：打印 `skip：…` 并以 0 退出——开发机常常没有无头浏览器，
    「跑不了」不等于「坏了」，不该因此判失败；
  · **强制模式（CI）**：设 `NOTDND_UI_SMOKE_MANDATORY=1`（`1` / `true` / `yes` / `on`
    均可），前置缺失即打印 `FAIL（强制模式…）` 并以**非零**退出。回归门要求 UI 冒烟
    **真实执行**——静默跳过会把浏览器这条路径悄悄移出 CI 覆盖（见 Issue #172）。

强制模式且前置齐备时，脚本会打印 `UI 冒烟（强制模式）：前置条件齐备，真实执行`
与末尾的 `UI 冒烟真实执行并通过`，CI 日志据此区分「真跑了且通过」与「前置缺失」。

## 自测

`main()` 每次都先跑一段**自测**（`_selftest_missing_paths`）：用子进程把本脚本再跑
一遍，注入「缺前置」，分别验证「强制 → 非零」「默认 → 零」两条处置路径——于是
「前置条件缺失」这条路径本身也有针对性测试，且不依赖运行环境。子进程靠
`NOTDND_UI_SMOKE_SKIP_SELFTEST=1` 跳过自测，避免无限递归。

测试期依赖从简：只用标准库 + websocket-client（经 `_cdp` 使用）。

除整页级不变量外，本脚本另跑一组**行动管线接线**（M8-A4，ACTION-DESIGN §9）的
浏览器断言：R0/R1 直通、R2+ 先弹确认、澄清只问一个问题、无 AI 时显式行动仍可用。
它们需要一局带场景行动的存档（在临时存档目录里现造）与一个**假上游**（澄清那条
路径要模型回一份封套），所以只在真浏览器可用时执行。

直接运行：python3 tests/test_ui_smoke.py
强制运行：NOTDND_UI_SMOKE_MANDATORY=1 python3 tests/test_ui_smoke.py
"""

import os
import json
import pathlib
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import _cdp  # noqa: E402
import notdnd_web  # noqa: E402
import prism_core  # noqa: E402

STATIC_INDEX = os.path.join(ROOT, "static", "index.html")

# 强制模式开关（CI 置 1）：前置缺失时「跳过」还是「失败」。
MANDATORY_ENV = "NOTDND_UI_SMOKE_MANDATORY"
# 自测注入钩子：强制报告某一项前置缺失，让缺失路径可被确定性测试。
MISSING_ENV = "NOTDND_UI_SMOKE_MISSING"
# 自测子进程据此跳过自测，避免无限递归。
SKIP_SELFTEST_ENV = "NOTDND_UI_SMOKE_SKIP_SELFTEST"

_TRUTHY = ("1", "true", "yes", "on")

MISSING_INDEX_REASON = "没有 static/index.html（前端尚未落地）"
MISSING_CDP_REASON = "无 WebSocket 客户端或浏览器"

_oks = []
_fails = []


def check(name, cond, detail=""):
    if cond:
        _oks.append(name)
        print("  ok   %s" % name)
    else:
        _fails.append(name)
        print("  FAIL %s  %s" % (name, detail))


def _truthy(raw):
    return (raw or "").strip().lower() in _TRUTHY


def _mandatory():
    return _truthy(os.environ.get(MANDATORY_ENV))


def _precondition_failure():
    """返回唯一的前置缺失原因；前置齐备则返回 None。

    自测可用 `NOTDND_UI_SMOKE_MISSING=index|cdp` 注入一个「缺失」，
    从而在不依赖真实环境的前提下测试缺失路径。
    """
    forced = (os.environ.get(MISSING_ENV) or "").strip().lower()
    if forced == "index":
        return MISSING_INDEX_REASON
    if forced == "cdp":
        return MISSING_CDP_REASON
    if not os.path.isfile(STATIC_INDEX):
        return MISSING_INDEX_REASON
    if not _cdp.available():
        return MISSING_CDP_REASON
    return None


def _verdict_missing(reason, mandatory):
    """前置缺失时的处置：返回进程退出码，并把区分性字样写进日志。

    强制模式 → 非零（CI 必须红）；默认 → 零（本地优雅跳过）。
    """
    if mandatory:
        print("FAIL（强制模式 %s=1）：UI 冒烟前置条件缺失：%s" % (MANDATORY_ENV, reason))
        print("强制模式要求 UI 冒烟真实执行；缺前置即失败（退出码 1）。")
        return 1
    print("skip：%s（退出码 0，不判失败）" % reason)
    return 0


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _Server:
    """起一个 notdnd_web 子进程：随机端口 + 自己的临时存档目录。

    `start_new_session=True` 让它自成一个会话 / 进程组，收摊时按进程组杀，
    连带子进程一起收干净；`__enter__` 失败也照样收摊，不留孤儿。

    `extra_env` 叠加在进程环境之上（后写覆盖）：行动管线的用例用它把 AI 配置
    显式清空（离线）或指向假上游——**不依赖开发机上有没有 `.env` / 环境变量**。
    """

    def __init__(self, extra_env=None):
        self.dir = tempfile.mkdtemp(prefix="notdnd-web-smoke-")
        self.port = _free_port()
        self.log_path = os.path.join(self.dir, "server.log")
        self.proc = None
        self._log = None
        self.extra_env = dict(extra_env or {})

    def __enter__(self):
        env = dict(os.environ, NOTDND_SAVE=self.dir, NOTDND_HOST="127.0.0.1",
                   NOTDND_PORT=str(self.port), PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
        env.update(self.extra_env)
        self._log = open(self.log_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "notdnd_web.py")],
            cwd=ROOT, env=env, stdout=self._log, stderr=subprocess.STDOUT,
            start_new_session=True)
        try:
            self._wait_ready()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_exc):
        if self.proc is not None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except OSError:
                    self.proc.kill()
                self.proc.wait(timeout=10)
            self.proc = None
        if self._log is not None:
            self._log.close()
            self._log = None
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def _wait_ready(self):
        deadline = time.time() + 30
        url = "http://127.0.0.1:%d/api/saves" % self.port
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("服务进程提前退出：\n%s" % self._tail())
            try:
                with urllib.request.urlopen(url, timeout=2):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise RuntimeError("服务未在 30 秒内就绪：\n%s" % self._tail())

    def _tail(self):
        try:
            with open(self.log_path, encoding="utf-8") as handle:
                return handle.read()[-2000:]
        except OSError:
            return "（无服务端日志）"


# ── M8-A4：行动管线接线的浏览器断言（ACTION-DESIGN §9）────────────────────
#
# 四条路径各给一条真断言，全部在**真浏览器 + 真服务**里跑：
#   ① R0/R1 直通（不弹确认，判定卡直接出现）；
#   ② R2+ 确认（先弹 L2；取消不出结果，确认后才提交）；
#   ③ 澄清：只呈现**一个**问题与选项，答完（clarify_id 回路）出判定卡；
#   ④ 无 AI 时显式行动仍可用 + 输入框的一句话明确提示离线。
# ①②④ 所在的服务器**显式清空** AI 配置（与开发机上的 `.env` / 环境变量无关）；
# ③ 需要一个 OpenAI 兼容的**假上游**：澄清的语义仍由服务端产出，桩只回封套。

# 假上游对「解析段」请求回的封套（§6.3）：一次澄清，两个候选都在行动表里。
STUB_CLARIFY_ENVELOPE = {
    "type": "clarify",
    "missing": "verb",
    "question": "你想做的是哪一种？",
    "options": [{"id": "a-lock", "label": "撬锁"},
                {"id": "a-pry", "label": "硬撬"}],
}
# 假上游对叙事请求回的正文（不含骰子记法，过得了 review_narration）。
STUB_NARRATION = "锁簧弹起，门缝里透出一线冷风。"


class _ModelStub:
    """OpenAI 兼容的假上游（只本机、只这条用例）。

    按请求体分流：带 `tools`（叙事请求）回一段正文；否则（解析段请求）回
    `STUB_CLARIFY_ENVELOPE` 的 JSON。请求体记在 `self.requests` 里，供断言
    「澄清只问了一次」。
    """

    def __init__(self):
        self.requests = []
        stub = self

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):        # 不打日志，保持测试输出干净
                pass

            def do_POST(self):                    # noqa: N802 — 标准库命名
                raw = self.rfile.read(
                    int(self.headers.get("Content-Length") or 0))
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    body = {}
                stub.requests.append(body)
                is_narrative = "tools" in (body or {})
                content = STUB_NARRATION if is_narrative else json.dumps(
                    STUB_CLARIFY_ENVELOPE, ensure_ascii=False)
                payload = json.dumps(
                    {"choices": [{"message": {"role": "assistant",
                                              "content": content}}]},
                    ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        return False


def _offline_env():
    """把 AI 配置显式清空：`load_env` 里 `os.environ` 优先，空串也算「有」。"""
    return {"BASE_URL": "", "MODEL": "", "API_KEY": ""}


def _stub_env(port):
    """把 AI 配置指向假上游（三个键齐 = `status().chat` 为真）。"""
    return {"BASE_URL": "http://127.0.0.1:%d" % port, "MODEL": "stub-model",
            "API_KEY": "stub-key"}


def _seed_action_session(save_dir, sid, actions):
    """在**子进程服务的存档目录**里造一局带场景行动的存档（同 #282 的手法）。

    `Session.save()` 读模块级 `SAVE_DIR`，所以先把常量指到目标目录再落盘；
    真子进程服务随后从磁盘载入这一局（缓存里没有，必然读盘）。
    """
    saved_dir = notdnd_web.SAVE_DIR
    notdnd_web.SAVE_DIR = pathlib.Path(save_dir)
    try:
        session = notdnd_web.Session(sid)
        rules = prism_core.RuleSession(sid)
        rules.party.append(prism_core.new_unit(
            "u-1", "试炼者", attributes={"MGT": 6, "INS": 7}))
        rules.scene = {"id": "sc-1", "actions": list(actions)}
        session.rules = rules.snapshot()
        session.save()
    finally:
        notdnd_web.SAVE_DIR = saved_dir
    notdnd_web._sessions.pop(sid, None)     # 丢掉本进程缓存（本进程本来也不服务它）


def _get_json(port, path):
    """取一次只读 JSON（本脚本只在播种前读 `/api/saves` 拿存档目录）。"""
    url = "http://127.0.0.1:%d%s" % (port, path)
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _click(page, selector):
    """点一个元素；找不到返回 False（断言里就能看出是「没这个按钮」）。"""
    return page.js(
        "const e = document.querySelector(%s);"
        "if (!e) return false; e.click(); return true;" % json.dumps(selector))


def _enter_session(page, sid, base="/"):
    """把这一局写进 localStorage 再重进主画面（同用户「继续这一局」的口径）。

    `base` 是这次重进的绝对地址：`_cdp.Browser.navigate()` 带的相对路径会拼到
    浏览器启动时的那个 base 上，所以跨服务器的用例必须显式给绝对地址。
    """
    page.js("localStorage.setItem('prism.sid', %s); return 1;" % json.dumps(sid))
    page.navigate(base)


def _type_and_send(page, text):
    """在输入区打字并点发送（模拟玩家真实输入：input 事件会走本片的校验）。"""
    return page.js(
        "const ta = document.getElementById('turnInput');"
        "const btn = document.getElementById('turnSend');"
        "if (!ta || !btn) return false;"
        "ta.value = %s;"
        "ta.dispatchEvent(new Event('input', { bubbles: true }));"
        "btn.click();"
        "return true;" % json.dumps(text))


def _results(page):
    """判定卡的条数与最后一条的状态（专用 `data-outcome`，不看颜色 / 文案）。"""
    return page.js(
        "const cards = document.querySelectorAll('#stream .msg-result');"
        "const last = cards.length ? cards[cards.length - 1] : null;"
        "return { count: cards.length,"
        "         outcome: last ? (last.dataset.outcome || '') : '',"
        "         action: last && last.querySelector('.res-action')"
        "                 ? last.querySelector('.res-action').textContent : '' };")


def _sheet(page):
    """确认 / 澄清模态的当前状态（专用 `data-kind` / `data-risk`）。"""
    return page.js(
        "const dlg = document.getElementById('actionSheet');"
        "const body = document.getElementById('actionSheetBody');"
        "if (!dlg || !body) return { present: false };"
        "return { present: !dlg.hidden,"
        "         kind: dlg.dataset.kind || '',"
        "         risk: dlg.dataset.risk || '',"
        "         title: (document.getElementById('actionSheetTitle') || {}).textContent || '',"
        "         questions: body.querySelectorAll('.confirm-line').length,"
        "         options: body.querySelectorAll('[data-clarify-option]').length,"
        "         text: body.textContent || '' };")


def _check_offline_actions(page, server):
    """①②④：无 AI 时 R1 直通 / R3 先确认 / NL 明确提示离线。"""
    sid = "a4-ui-offline"
    _seed_action_session(_get_json(server.port, "/api/saves")["save_dir"], sid, [
        # R0：只读、无副作用（§6.1）——纯叙述行动，直通、不确认。
        {"id": "a-look", "label": "查看锁孔", "kind": "narrate",
         "text": "锁孔边缘有新划痕。", "risk": "R0"},
        {"id": "a-lock", "label": "撬锁", "kind": "check", "df": 12,
         "auto_pass": True, "on_pass": "锁簧弹开。", "risk": "R1",
         "time_cost": {"value": 5, "unit": "minute"}},
        {"id": "a-force", "label": "硬撬", "kind": "check", "df": 12,
         "auto_pass": True, "on_pass": "锁舌崩开。", "risk": "R3",
         "time_cost": {"value": 30, "unit": "minute"}},
    ])
    _enter_session(page, sid)
    ready = page.wait_for(
        "return !!(window.__TURN && !window.__TURN().sessionError"
        " && document.querySelectorAll('#actList [data-act]').length === 3);",
        timeout=15)
    check("A4：主画面进得去、行动区有三条行动", ready,
          "snapshot=%r" % (page.js("return window.__TURN ? window.__TURN() : null;"),))
    if not ready:
        return

    # 风险角标来自 `GET /api/action/catalog`：R3 有「需确认」，R0 / R1 都没有。
    chip = page.wait_for(
        "const b = document.querySelectorAll('#actList [data-act]');"
        "let risk = false, plain = true;"
        "b.forEach(function (x) {"
        "  if (x.dataset.act === 'a-force') risk = !!x.querySelector('.act-risk');"
        "  if (x.dataset.act !== 'a-force' && x.querySelector('.act-risk')) plain = false;"
        "});"
        "return risk && plain;", timeout=10)
    check("A4：行动单只给 R2+ 标「需确认」（R0/R1 直通）", chip,
          "buttons=%r" % (page.js(
              "return Array.prototype.map.call("
              "document.querySelectorAll('#actList [data-act]'),"
              "function (b) { return b.textContent; });"),))

    # ① R0 直通：只读行动也不弹确认（R0 / R1 都是「无确认」这条分支）。
    before = _results(page)
    _click(page, '#actList [data-act="a-look"]')
    got = page.wait_for(
        "const r = document.querySelectorAll('#stream .msg-result');"
        "return r.length === %d && r[r.length - 1].dataset.outcome === 'success';"
        % (before["count"] + 1), timeout=20)
    check("A4①：R0 直通出判定卡（成功、无确认）",
          got and not _sheet(page)["present"], "results=%r sheet=%r" % (_results(page), _sheet(page)))

    # ① R1 直通：点一下直接出判定卡，全程没有确认模态。
    before = _results(page)
    _click(page, '#actList [data-act="a-lock"]')
    got = page.wait_for(
        "const r = document.querySelectorAll('#stream .msg-result');"
        "return r.length === %d && r[r.length - 1].dataset.outcome === 'success';"
        % (before["count"] + 1), timeout=20)
    after = _results(page)
    check("A4①：R1 直通出判定卡（成功）", got and after["outcome"] == "success",
          "results=%r" % (after,))
    check("A4①：R1 没有弹确认", _sheet(page)["present"] is False,
          "sheet=%r" % (_sheet(page),))
    check("A4①：判定卡点名这条行动", after["action"] == "撬锁",
          "results=%r" % (after,))
    card = page.js(
        "const r = document.querySelectorAll('#stream .msg-result');"
        "return r.length ? r[r.length - 1].textContent : '';")
    check("A4①：判定卡列出已结算的代价（时间 5 分钟）",
          "时间" in card and "5 分钟" in card, "card=%r" % (card,))
    narrate = page.js(
        "const n = document.querySelectorAll('#stream .msg-narrate');"
        "return n.length ? n[n.length - 1].textContent : '';")
    check("A4④：离线时显式行动的叙事是服务端兜底句（result 与 narration 都在）",
          notdnd_web.FALLBACK_NARRATION in str(narrate or ""),
          "narration=%r" % (narrate,))

    # ② R2+ 确认：先弹 L2（点名这一步 + 风险级），取消 = 不提交。
    before = _results(page)
    _click(page, '#actList [data-act="a-force"]')
    sheet = page.wait_for(
        "const d = document.getElementById('actionSheet');"
        "return !!(d && !d.hidden && d.dataset.kind === 'confirm');", timeout=15)
    check("A4②：R3 先弹确认（L2 模态）", sheet,
          "sheet=%r" % (_sheet(page),))
    shown = _sheet(page)
    check("A4②：确认框带上风险级与这一步的说明",
          shown.get("risk") == "R3" and "硬撬" in shown.get("text", ""),
          "sheet=%r" % (shown,))
    check("A4②：确认框列出成本预览（耗时 30 分钟）",
          "30 分钟" in shown.get("text", "") and "高风险" in shown.get("text", ""),
          "sheet=%r" % (shown,))
    _click(page, '#actionSheetBody [data-action-cancel]')
    time.sleep(0.4)
    check("A4②：取消后不提交（判定卡条数不变、模态收起）",
          _results(page)["count"] == before["count"] and not _sheet(page)["present"],
          "results=%r sheet=%r" % (_results(page), _sheet(page)))

    # ② 再来一次，这回确认 → 提交 → 判定卡。
    _click(page, '#actList [data-act="a-force"]')
    sheet = page.wait_for(
        "const d = document.getElementById('actionSheet');"
        "return !!(d && !d.hidden && d.dataset.kind === 'confirm');", timeout=15)
    _click(page, '#actionSheetBody [data-action-go]')
    got = page.wait_for(
        "const r = document.querySelectorAll('#stream .msg-result');"
        "return r.length === %d && r[r.length - 1].dataset.outcome === 'success';"
        % (before["count"] + 1), timeout=20)
    after = _results(page)
    check("A4②：确认后提交并出判定卡（成功）", sheet and got,
          "results=%r" % (after,))
    check("A4②：提交后确认模态收起", _sheet(page)["present"] is False,
          "sheet=%r" % (_sheet(page),))

    # ④ NL 离线：这句话没法变成行动 → 输入区就地提示离线，且不产生任何判定卡。
    before = _results(page)
    _type_and_send(page, "我推门进去看看")
    offline = page.wait_for(
        "const box = document.getElementById('turnError');"
        "return !!(box && !box.hidden && "
        "  /离线|自定义|未配置/.test(box.textContent));", timeout=10)
    detail = page.js(
        "return { error: (document.getElementById('turnError') || {}).textContent || '',"
        "         text: (document.getElementById('turnInput') || {}).value || '' };")
    check("A4④：NL 离线时就地提示「导引者未配置」", offline, "composer=%r" % (detail,))
    check("A4④：NL 离线不产生判定卡（没结算、没提交）", 
          _results(page)["count"] == before["count"],
          "results=%r" % (_results(page),))
    check("A4④：玩家的原话还留在输入框里（不丢字）",
          "我推门进去看看" in detail.get("text", ""), "composer=%r" % (detail,))
    check("A4④：离线不影响显式行动（前面两条都真走完了）",
          _results(page)["count"] == before["count"] and before["count"] >= 2,
          "results=%r" % (before,))


def _check_clarify(page):
    """③：澄清只呈现一个问题与选项，答完（clarify_id 回路）出判定卡。"""
    sid = "a4-ui-clarify"
    with _ModelStub() as stub:
        with _Server(extra_env=_stub_env(stub.port)) as server:
            _seed_action_session(
                _get_json(server.port, "/api/saves")["save_dir"], sid, [
                    {"id": "a-lock", "label": "撬锁", "kind": "check", "df": 12,
                     "auto_pass": True, "on_pass": "锁簧弹开。", "risk": "R1"},
                    {"id": "a-pry", "label": "硬撬", "kind": "check", "df": 12,
                     "auto_pass": True, "on_pass": "锁舌崩开。", "risk": "R1"},
                ])
            page.navigate("http://127.0.0.1:%d/" % server.port)
            _enter_session(page, sid, base="http://127.0.0.1:%d/" % server.port)
            ready = page.wait_for(
                "return !!(window.__TURN && window.__TURN().guide.chat"
                " && document.querySelectorAll('#actList [data-act]').length === 2);",
                timeout=15)
            check("A4③：有 AI 配置的会话进得去（能力探测为真）", ready,
                  "snapshot=%r" % (page.js("return window.__TURN ? window.__TURN() : null;"),))
            if not ready:
                return

            before = _results(page)
            _type_and_send(page, "我想开这把锁")
            asked = page.wait_for(
                "const d = document.getElementById('actionSheet');"
                "return !!(d && !d.hidden && d.dataset.kind === 'clarify');",
                timeout=20)
            shown = _sheet(page)
            check("A4③：歧义只呈现一个澄清问题", asked and shown["questions"] == 1,
                  "sheet=%r" % (shown,))
            check("A4③：澄清给出服务端的选项（两个）",
                  shown["options"] == 2 and "你想做的是哪一种？" in shown["text"],
                  "sheet=%r" % (shown,))
            check("A4③：澄清没有提交任何东西（判定卡条数不变）",
                  _results(page)["count"] == before["count"],
                  "results=%r" % (_results(page),))

            # 回答第二个选项：带 `clarify_id` 重新提案 → 直通提交 → 判定卡。
            picked = page.js(
                "const opts = document.querySelectorAll('#actionSheetBody [data-clarify-option]');"
                "if (opts.length < 2) return false;"
                "opts[1].click(); return true;")
            got = page.wait_for(
                "const r = document.querySelectorAll('#stream .msg-result');"
                "return r.length === %d && r[r.length - 1].dataset.outcome === 'success';"
                % (before["count"] + 1), timeout=25)
            after = _results(page)
            check("A4③：答题（clarify_id 回路）后出判定卡（成功）",
                  picked and got, "results=%r" % (after,))
            check("A4③：提交的是选中那条行动（硬撬），不是第一项",
                  after["action"] == "硬撬", "results=%r" % (after,))
            check("A4③：问答完成后确认 / 澄清模态收起",
                  _sheet(page)["present"] is False, "sheet=%r" % (_sheet(page),))
            check("A4③：解析段只问了一次（没有二次追问）",
                  len([1 for body in stub.requests if "tools" not in body]) == 1,
                  "requests=%d propose=%d" % (
                      len(stub.requests),
                      len([1 for body in stub.requests if "tools" not in body])))


def _selftest_env(overrides):
    """自测子进程的环境：清掉两个会干扰的开关，再叠加要注入的值。"""
    env = dict(os.environ)
    for key in (MANDATORY_ENV, MISSING_ENV):
        env.pop(key, None)
    env[SKIP_SELFTEST_ENV] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(overrides)
    return env


def _run_selftest_child(overrides):
    """用子进程把本脚本再跑一遍（注入 overrides），返回 CompletedProcess。"""
    return subprocess.run(
        [sys.executable, os.path.abspath(__file__)],
        cwd=ROOT, env=_selftest_env(overrides),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        encoding="utf-8", errors="replace", timeout=180)


def _selftest_missing_paths():
    """针对「前置条件缺失」这条路径的自测，不依赖真实浏览器 / 服务。"""
    print("自测：前置条件缺失时的处置（强制 vs 默认）")

    strict = _run_selftest_child({MANDATORY_ENV: "1", MISSING_ENV: "cdp"})
    check("强制模式 + 缺前置 → 退出码非零", strict.returncode != 0,
          "rc=%s，输出尾=%r" % (strict.returncode, strict.stdout[-200:]))
    check("强制模式日志点明强制失败",
          "FAIL" in strict.stdout and MANDATORY_ENV in strict.stdout,
          "输出尾=%r" % (strict.stdout[-200:],))

    loose = _run_selftest_child({MISSING_ENV: "cdp"})
    check("默认（未强制）+ 缺前置 → 退出码 0", loose.returncode == 0,
          "rc=%s，输出尾=%r" % (loose.returncode, loose.stdout[-200:]))
    check("默认缺前置日志是 skip", "skip" in loose.stdout,
          "输出尾=%r" % (loose.stdout[-200:],))

    strict_index = _run_selftest_child({MANDATORY_ENV: "1", MISSING_ENV: "index"})
    check("强制模式 + 缺 index → 退出码非零", strict_index.returncode != 0,
          "rc=%s" % (strict_index.returncode,))


def _smoke():
    """真实跑一遍冒烟（前置已确认齐备）；返回进程退出码。"""
    if _mandatory():
        print("UI 冒烟（强制模式）：前置条件齐备，真实执行")
    print("UI 冒烟：起服务 → CDP 打开首页")
    with _Server(extra_env=_offline_env()) as server:
        base = "http://127.0.0.1:%d" % server.port
        try:
            page = _cdp.Browser(base).__enter__()
        except _cdp.CDPUnavailable as exc:
            # 前置检查说「齐备」了，起浏览器仍失败 → 按同一口径处置
            return _verdict_missing(str(exc), _mandatory())
        try:
            page.navigate("/", settle=2.0)
            check("首页加载完成",
                  page.wait_for("return document.readyState === 'complete';",
                                timeout=15) is True)
            on_views = page.js("return document.querySelectorAll('.view.is-on').length;")
            check("至少有一个视图点亮 is-on", bool(on_views),
                  "is-on 视图数=%r" % (on_views,))
            errs = page.js("return window.__ERRS || [];")
            check("window.__ERRS 为空", not errs,
                  "捕获到未捕获错误：%r" % (errs,))
            # 外部（跨源）资源请求：只认 http(s) 且 origin 与页面不同的。
            # 同源的 /css/* /js/* /api/*、以及浏览器自动要的 /favicon.ico 都不算。
            ext = page.js(
                "const o = location.origin;"
                "return performance.getEntriesByType('resource')"
                "  .map(e => e.name)"
                "  .filter(u => /^https?:/i.test(u) && new URL(u).origin !== o);")
            check("页面不发起外部资源请求", not ext,
                  "外部请求：%r" % (ext,))
            # 跨片写动作「单一在途」（§2.0）：在途时第二次写被 core 统一守卫拒绝。
            # 用 fetch 桩把第一次写（/api/session/create）拖慢，制造一个可断言的
            # 在途窗口；随后第二次写必须在**发往网络之前**被拒，且不扰动在途状态；
            # 第一次写完成后守卫必须恢复（可再次写）。
            guard = page.js(
                "const orig = window.fetch;"
                "window.fetch = function (url, o) {"
                "  if (String(url).indexOf('/api/session/create') >= 0) {"
                "    return new Promise(function (resolve) {"
                "      setTimeout(function () {"
                "        resolve(new Response(JSON.stringify({ sid: 'guard-probe' }),"
                "          { status: 200, headers: { 'Content-Type': 'application/json' } }));"
                "      }, 400);"
                "    });"
                "  }"
                "  return orig.apply(this, arguments);"
                "};"
                "const first = window.api('/api/session/create', { method: 'POST', body: {} });"
                "await new Promise(function (r) { setTimeout(r, 30); });"
                "const inflight = window.__WRITE().inflight;"
                "let rejected = false, message = '';"
                "try { await window.api('/api/save/delete', { method: 'POST', body: { sid: 'x' } }); }"
                "catch (e) { rejected = true; message = String((e && e.message) || e); }"
                "const stillInflight = window.__WRITE().inflight;"
                "try { await first; } catch (e) {}"
                "window.fetch = orig;"
                "const recovered = !window.__WRITE().inflight;"
                "return { inflight: inflight, rejected: rejected, message: message,"
                "         stillInflight: stillInflight, recovered: recovered };")
            check("写请求在途：统一守卫登记为在途",
                  bool(guard) and guard.get("inflight") is True,
                  "guard=%r" % (guard,))
            check("写请求在途：第二次写被统一守卫拒绝",
                  bool(guard) and guard.get("rejected") is True,
                  "guard=%r" % (guard,))
            check("写请求在途：被拒的第二次写不扰动在途状态",
                  bool(guard) and guard.get("stillInflight") is True,
                  "guard=%r" % (guard,))
            check("写请求完成后：统一守卫恢复（可再次写）",
                  bool(guard) and guard.get("recovered") is True,
                  "guard=%r" % (guard,))
            # M8-A4：行动管线接线（R0/R1 直通 / R2+ 确认 / 澄清 / 离线）。
            _check_offline_actions(page, server)
            _check_clarify(page)
            if not _fails:
                try:
                    saved = page.screenshot("ui-smoke.png")
                    print("  截图 %s" % saved)
                except Exception as exc:        # noqa: BLE001 — 截图失败不判失败
                    print("  （截图跳过：%s）" % exc)
        finally:
            page.__exit__(None, None, None)

    print()
    print("通过 %d / 共 %d" % (len(_oks), len(_oks) + len(_fails)))
    if _fails:
        return 1
    if _mandatory():
        print("UI 冒烟真实执行并通过")
    return 0


def main():
    if not _truthy(os.environ.get(SKIP_SELFTEST_ENV)):
        _selftest_missing_paths()
        print()
        if _fails:
            print("自测未通过：%d 项失败" % len(_fails))
            print("通过 %d / 共 %d" % (len(_oks), len(_oks) + len(_fails)))
            return 1

    reason = _precondition_failure()
    if reason is not None:
        return _verdict_missing(reason, _mandatory())
    return _smoke()


if __name__ == "__main__":
    sys.exit(main())
