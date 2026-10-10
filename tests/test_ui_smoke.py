#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UI 冒烟：起服务 → 无头浏览器打开首页 → 检查几条最便宜的不变量。

只回答一个问题：「首页还能不能用」。三条断言都针对「整页级」的故障，
不碰任何具体交互（那些交给各自的专项用例）：

  1. 首页加载完成（document.readyState 到 complete）；
  2. `window.__ERRS` 为空——前端把**未捕获错误**与**未处理的 Promise 拒绝**
     都收集到这个数组里，非空即代表页面在初始化期就抛了错；
  3. 至少有一个 `.view` 处在 `is-on` 状态——视图系统靠这个 class 决定
     哪个界面可见，一个都没点亮说明页面白屏。

前置条件任一缺失即**优雅跳过**并返回 0（CI 不红）：
  · 没有 `static/index.html`（前端还没落地，或这份测试被单独拎出来跑）；
  · 没有 WebSocket 客户端（websocket-client）；
  · 机器上没有可用浏览器。
「跑不了」不等于「坏了」，所以这里打印 `skip：…` 后以 0 退出。

测试期依赖从简：只用标准库 + websocket-client（经 `_cdp` 使用）。

直接运行：python3 tests/test_ui_smoke.py
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

_oks = []
_fails = []


def check(name, cond, detail=""):
    if cond:
        _oks.append(name)
        print("  ok   %s" % name)
    else:
        _fails.append(name)
        print("  FAIL %s  %s" % (name, detail))


def _skip(reason):
    print("skip：%s（退出码 0，不判失败）" % reason)
    raise SystemExit(0)


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


def main():
    if not os.path.isfile(STATIC_INDEX):
        _skip("没有 static/index.html（前端尚未落地）")
    if not _cdp.available():
        _skip("无 WebSocket 客户端或浏览器")

    print("UI 冒烟：起服务 → CDP 打开首页")
    with _Server() as server:
        base = "http://127.0.0.1:%d" % server.port
        try:
            page = _cdp.Browser(base).__enter__()
        except _cdp.CDPUnavailable as exc:
            _skip(str(exc))
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
    return 1 if _fails else 0


if __name__ == "__main__":
    sys.exit(main())
