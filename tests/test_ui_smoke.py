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

直接运行：python3 tests/test_ui_smoke.py
强制运行：NOTDND_UI_SMOKE_MANDATORY=1 python3 tests/test_ui_smoke.py
"""

import os
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _cdp  # noqa: E402

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
    """

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="notdnd-web-smoke-")
        self.port = _free_port()
        self.log_path = os.path.join(self.dir, "server.log")
        self.proc = None
        self._log = None

    def __enter__(self):
        env = dict(os.environ, NOTDND_SAVE=self.dir, NOTDND_HOST="127.0.0.1",
                   NOTDND_PORT=str(self.port), PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
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
    with _Server() as server:
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
