#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP-over-WebSocket 最小浏览器助手（测试基座）。

浏览器用例真正需要的动作其实不多：起一个无头浏览器、开一个标签页、执行一段
JS、截图、轮询等待某个条件成立、判断元素有没有落在视口里。这几步会在多个
用例里重复出现，所以抽成本模块，**只此一份**——不要在各自的用例里各抄一遍。

设计取舍：

* **不引入重型浏览器自动化框架**：运行时依赖要守铁律 2，测试期依赖也「能不加
  就不加」。这里只用一个小巧的 WebSocket 客户端连浏览器自带的 DevTools 协议，
  够用、轻、零搬运成本。
* **缺东西一律判「不可用」，而不是抛崩溃**：机器上没装 WebSocket 客户端、或
  没有可用浏览器时，`available()` 返回假，构造 `Browser` 也会抛 `CDPUnavailable`。
  调用方据此**优雅跳过**（退出码 0）。CI 机器常常没有图形环境，
  「跑不了浏览器用例」不该等于「用例失败」。
* **浏览器路径可覆盖且不写死**：优先读环境变量 `NOTDND_CHROME`，其次是 `PATH`
  里几个常见可执行名、再是几个通用安装位置；都没有就判定不可用。这里刻意
  不写任何与本机绑定的绝对路径。
* **收摊按进程组**：浏览器以独立会话启动，退出时连同它的子进程一起收干净，
  异常路径也照收（`__exit__` 保证）。

用法（调用方负责在不可用时跳过）::

    import _cdp
    if not _cdp.available():
        print("skip：无 WebSocket 客户端或浏览器"); raise SystemExit(0)
    with _cdp.Browser("http://127.0.0.1:8600") as page:
        page.navigate("/")
        page.wait_for("return document.readyState === 'complete';")
        page.screenshot("home.png")
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request

try:                                    # 测试期唯一依赖：websocket-client
    import websocket                    # type: ignore
except Exception:                       # noqa: BLE001 — 缺依赖时优雅降级
    websocket = None


class CDPUnavailable(RuntimeError):
    """CDP 不可用（缺依赖 / 缺浏览器 / 起不来）。调用方据此优雅跳过。"""


# 无头浏览器在 PATH 里可能出现的名字，以及几个通用的安装位置。
# 全部是「跟本机无关」的常见路径，避免把开发机的目录写进仓库。
_CHROME_NAMES = (
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
    "chrome", "chrome-headless-shell",
)
_CHROME_PATHS = (
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/snap/bin/chromium",
    "/opt/google/chrome/chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser():
    """找一个可用的浏览器可执行文件；找不到返回 None。

    优先级：环境变量 `NOTDND_CHROME` > PATH 上的常见名字 > 通用安装位置。
    """
    override = os.environ.get("NOTDND_CHROME")
    if override and os.path.exists(override):
        return override
    for name in _CHROME_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for path in _CHROME_PATHS:
        if os.path.exists(path):
            return path
    return None


def available() -> bool:
    """CDP 是否可用：既有 WebSocket 客户端，也能找到浏览器。"""
    return websocket is not None and find_browser() is not None


def _free_port() -> int:
    """要一个当前空闲的端口：绑 0 让内核分配，读到号就释放。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _shots_dir() -> str:
    # tests/shots/ 已在 .gitignore 里，截图落这里不入库。
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shots")
    os.makedirs(d, exist_ok=True)
    return d


class Browser:
    """一个连到无头浏览器的会话（上下文管理器）。

    `js()` 里的表达式会被包进 `(async () => { ... })()` 执行，所以要写
    `return ...;` 作为结尾，例如 `page.js("return 1 + 1;")`；这样也能直接
    使用 `await`。`wait_for()` 接收同样形状的表达式，期望它返回真值。
    """

    def __init__(self, base_url, width=390, height=844, out_dir=None,
                 chrome=None, timeout=30.0):
        if websocket is None:
            raise CDPUnavailable("缺少 WebSocket 客户端（websocket-client）")
        self.chrome = chrome or find_browser()
        if not self.chrome:
            raise CDPUnavailable("找不到可用的浏览器（可设 NOTDND_CHROME 指定）")
        self.base = base_url.rstrip("/")
        self.width = int(width)
        self.height = int(height)
        self.out_dir = out_dir or _shots_dir()
        self.timeout = timeout
        self._proc = None
        self._ws = None
        self._mid = 0
        self._profile = tempfile.mkdtemp(prefix="notdnd-cdp-")

    # -- 生命周期 --------------------------------------------------------
    def __enter__(self):
        try:
            self._launch()
            self._open_tab()
            self.send("Page.enable")
            self.send("Runtime.enable")
            self.set_viewport(self.width, self.height)
        except BaseException:
            self.__exit__(None, None, None)     # 失败也收摊，不留孤儿
            raise
        return self

    def __exit__(self, *_exc):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:                   # noqa: BLE001
                pass
            self._ws = None
        if self._proc is not None:
            try:
                os.killpg(os.getpgid(self._proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
                except OSError:
                    self._proc.kill()
                self._proc.wait(timeout=10)
            self._proc = None
        shutil.rmtree(self._profile, ignore_errors=True)
        return False

    def _launch(self):
        port = _free_port()
        self._debug_port = port
        self._proc = subprocess.Popen(
            [self.chrome, "--headless=new",
             "--remote-debugging-port=%d" % port,
             # 新版浏览器默认拒绝带 Origin 的 WS 连接，必须显式放行
             "--remote-allow-origins=*",
             "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
             "--user-data-dir=%s" % self._profile, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise CDPUnavailable("浏览器启动后立即退出")
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/json/version" % port, timeout=1):
                    return
            except Exception:                   # noqa: BLE001
                time.sleep(0.4)
        raise CDPUnavailable("浏览器调试端口未在 %.0f 秒内就绪" % self.timeout)

    def _open_tab(self):
        # 新版 CDP 的 /json/new 只接受 PUT，GET 会返回 405
        req = urllib.request.Request(
            "http://127.0.0.1:%d/json/new?about:blank" % self._debug_port,
            method="PUT")
        with urllib.request.urlopen(req, timeout=5) as resp:
            tab = json.loads(resp.read().decode())
        self._ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=max(self.timeout, 40),
            max_size=40 * 1024 * 1024)

    # -- 底层协议 --------------------------------------------------------
    def send(self, method, **params):
        """发一条 CDP 命令，返回它的 result；命令报错则抛 RuntimeError。"""
        self._mid += 1
        mine = self._mid
        self._ws.send(json.dumps({"id": mine, "method": method, "params": params}))
        while True:
            msg = json.loads(self._ws.recv())
            if msg.get("id") == mine:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def js(self, body, wait=0.0):
        """执行一段 JS（body 里用 `return` 给出结果），返回其值。

        传 `wait` 则在取值后再睡一会儿，给渲染 / 网络留出时间。
        """
        result = self.send(
            "Runtime.evaluate", expression="(async()=>{%s})()" % body,
            awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            detail = json.dumps(result["exceptionDetails"], ensure_ascii=False)
            raise RuntimeError("页面 JS 抛异常：" + detail[:500])
        if wait:
            time.sleep(wait)
        return result.get("result", {}).get("value")

    def set_viewport(self, width, height, scale=2, mobile=True):
        """设置设备视口（默认按手机尺寸，方便对齐真机布局）。"""
        self.width, self.height = int(width), int(height)
        self.send("Emulation.setDeviceMetricsOverride",
                  width=self.width, height=self.height,
                  deviceScaleFactor=scale, mobile=mobile)

    def navigate(self, path, settle=1.6):
        """导航到 base_url + path（path 以 `/` 开头），随后稍作等待。"""
        if not path.startswith(("http://", "https://")):
            path = self.base + path
        self.send("Page.navigate", url=path)
        time.sleep(settle)

    def wait_for(self, body, timeout=15.0, step=0.35):
        """轮询执行 JS，直到结果为真或超时。避免固定 sleep 造成的时序误判。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.js(body):
                return True
            time.sleep(step)
        return False

    # -- 断言辅助 --------------------------------------------------------
    def in_viewport(self, selector, slack=1):
        """元素是否**完整落在视口内**；元素不存在时 `found` 为假。

        这是 UI 用例里最容易漏的一类问题：元素在 DOM 里、宽高也对，
        却被排到视口之外——所有「存在性」断言都过，界面却是空的。
        """
        body = (
            "const s=%d;"
            "const e=document.querySelector(%s);"
            "if(!e) return {found:false};"
            "const r=e.getBoundingClientRect();"
            "return {found:true,"
            " top:Math.round(r.top), left:Math.round(r.left),"
            " width:Math.round(r.width), height:Math.round(r.height),"
            " in_view: r.top >= -s && r.left >= -s &&"
            " r.bottom <= window.innerHeight + s &&"
            " r.right <= window.innerWidth + s};"
        ) % (int(slack), json.dumps(selector))
        return self.js(body)

    def screenshot(self, name, out_dir=None):
        """截当前视口，存到 out_dir（默认 tests/shots/），返回文件路径。"""
        data = self.send("Page.captureScreenshot", format="png")
        target_dir = out_dir or self.out_dir
        os.makedirs(target_dir, exist_ok=True)
        path = os.path.join(target_dir, name)
        with open(path, "wb") as handle:
            handle.write(base64.b64decode(data["data"]))
        return path
