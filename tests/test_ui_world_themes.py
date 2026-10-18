#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""世界主题语义不变式（``UI-WORLD-THEMES.md`` §6 / §6.2 / §4.6）：真浏览器 + 真服务。

把「语义色不被世界主题重定义」变成**自动断言**，而不是靠评审肉眼（W3，Issue #315）：

  1. **B 层语义令牌逐项相同**：`--danger` / `--success` 在
     「`data-world` 缺省与 `modern` / `fantasy` / `future`」×「明暗轴」
     的每个组合下**逐项相同**——世界主题块不得覆盖语义色（§4.3 / §6.1）。
     `--scope-public` / `--scope-party` / `--scope-private` 用同一条断言：
     三个令牌当前**尚未在任何文件登记**（§6.4 落点待拍板，`tokens.css` 头注
     亦写明「不登记」），此时以「跨世界同为未定义」通过；
     **登记落地后本断言自动获得覆盖**（令牌一出现就被纳入「逐项相同」比较），
     测试无需改动。
  2. **`--accent`（§6.2）**：色相 ∉ `[340°, 25°]`；与 `--danger` / `--success`
     的色相距离 ≥ 30°；`accent on bg-0` / `accent on bg-1` 对比度 ≥ 4.5:1。
     取色一律读浏览器的**计算值**（自定义属性 + 探针元素解析成 `rgb()`），
     色相与对比度一律在**测试内**计算——不手抄规范表格（表会过时，令牌不会）。
  3. **无外链断言保持**（UI 冒烟同款手法）：页面加载不发起任何跨源资源请求。
  4. **防呆（让上面三条不空转）**：世界轴与明暗轴必须**真的**在浏览器里生效——
     缺省与显式 `modern` 同值、三主题 `--bg-0` 互不相同、同一世界「强制档 =
     跟随档」且「暗 ≠ 明」。否则「跨世界取值相同」可能只是因为三个世界
     根本没被应用（断言会假装通过）。
  5. **W2 接线（Issue #334）**：会话加载后按快照的 `world_type` 写
     `html[data-world]`（三主题各验一次；未知 / 缺失落 `modern` 且不残留）；
     设置区**只读**显示「本局世界主题」且无切换控件；切存档（现代 → 奇幻 →
     未来 → 未绑定老档）时主题随之变化，**不重放叙事、不丢滚动位置、不动焦点**；
     显式 `modern` 与「不设属性」的计算样式逐项相同（零回归对照）。
     这条链路用真实大厅点击驱动（菜单 → 返回列表 → 点存档），不直接改 SID。

明暗轴用 CDP 的 ``Emulation.setEmulatedMedia`` 模拟系统偏好，并取四种组合：
「系统暗 · 跟随」「系统暗 · 强制明」「系统明 · 跟随」「系统明 · 强制暗」——
两个强制档、两个跟随档，外加两个**跨系统**的组合（强制覆盖系统，§4.7 的
``:not([data-theme="dark"])`` 就是为这条路径写的）。

## 本地跳过 vs 强制执行

前置条件（`static/index.html`、WebSocket 客户端、可用浏览器）任一缺失时：

  · **默认（本地）**：打印 `skip：…` 并以 0 退出——开发机常常没有无头浏览器，
    「跑不了」不等于「坏了」，不该因此判失败；
  · **强制模式（CI）**：设 `NOTDND_UI_WORLD_THEMES_MANDATORY=1`（`1` / `true` /
    `yes` / `on` 均可），前置缺失即打印 `FAIL（强制模式…）` 并以**非零**退出——
    与 `tests/test_ui_smoke.py` 的 `NOTDND_UI_SMOKE_MANDATORY` 同款机制。

强制模式且前置齐备时，打印 `世界主题不变式（强制模式）：前置条件齐备，真实执行`
与末尾的 `世界主题不变式真实执行并通过`，日志据此区分「真跑了且通过」与「前置缺失」。

## 自测

`main()` 每次都先跑一段自测（`_selftest_missing_paths`）：用子进程把本脚本再跑
一遍，注入「缺前置」，分别验证「强制 → 非零」「默认 → 零」两条处置路径。
子进程靠 `NOTDND_UI_WORLD_THEMES_SKIP_SELFTEST=1` 跳过自测，避免无限递归。

测试期依赖从简：只用标准库 + websocket-client（经 `_cdp` 使用），**不新增依赖**。

直接运行：python3 tests/test_ui_world_themes.py
强制运行：NOTDND_UI_WORLD_THEMES_MANDATORY=1 python3 tests/test_ui_world_themes.py
"""

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
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _cdp  # noqa: E402

STATIC_INDEX = os.path.join(ROOT, "static", "index.html")

# 强制模式开关：前置缺失时「跳过」还是「失败」。
MANDATORY_ENV = "NOTDND_UI_WORLD_THEMES_MANDATORY"
# 自测注入钩子：强制报告某一项前置缺失，让缺失路径可被确定性测试。
MISSING_ENV = "NOTDND_UI_WORLD_THEMES_MISSING"
# 自测子进程据此跳过自测，避免无限递归。
SKIP_SELFTEST_ENV = "NOTDND_UI_WORLD_THEMES_SKIP_SELFTEST"

_TRUTHY = ("1", "true", "yes", "on")

MISSING_INDEX_REASON = "没有 static/index.html（前端尚未落地）"
MISSING_CDP_REASON = "无 WebSocket 客户端或浏览器"

# 取色名单：B 层语义令牌（世界主题一律不得覆盖）+ `--accent`（唯一允许改色的
# 语义相关令牌）+ 两个底（accent 的对比度基准）。
TOKEN_NAMES = ("--danger", "--success", "--scope-public", "--scope-party",
               "--scope-private", "--accent", "--bg-0", "--bg-1")
SEMANTIC_TOKENS = ("--danger", "--success")
SCOPE_TOKENS = ("--scope-public", "--scope-party", "--scope-private")

# 世界主题：缺省（不设属性）与三个显式取值（§4.3 / §4.5）。
WORLDS = (("缺省", None), ("modern", "modern"), ("fantasy", "fantasy"),
          ("future", "future"))
NAMED_WORLDS = ("modern", "fantasy", "future")

# 明暗轴（§4.7）：(键, 日志名, 模拟的系统明暗, data-theme 取值[None = 跟随系统])。
# 四种组合覆盖两个强制档、两个跟随档，以及两个**跨系统**的强制组合。
DARK_KEYS = ("sys-dark/follow", "sys-light/forced-dark")
LIGHT_KEYS = ("sys-dark/forced-light", "sys-light/follow")
APPEARANCES = (
    (DARK_KEYS[0], "系统暗 · 跟随", "dark", None),
    (LIGHT_KEYS[0], "系统暗 · 强制明", "dark", "light"),
    (DARK_KEYS[1], "系统明 · 强制暗", "light", "dark"),
    (LIGHT_KEYS[1], "系统明 · 跟随", "light", None),
)

MIN_CONTRAST = 4.5          # §4.6：文本类组合的下限
MIN_HUE_DISTANCE = 30.0     # §6.2：accent 与 danger / success 的色相距离下限
RED_BAND = (340.0, 25.0)    # §6.2：不得落入的红带（跨 0°，闭区间）

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

    自测可用 `NOTDND_UI_WORLD_THEMES_MISSING=index|cdp` 注入一个「缺失」，
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
    """前置缺失时的处置：返回进程退出码，并把区分性字样写进日志。"""
    if mandatory:
        print("FAIL（强制模式 %s=1）：世界主题不变式前置条件缺失：%s"
              % (MANDATORY_ENV, reason))
        print("强制模式要求真实执行；缺前置即失败（退出码 1）。")
        return 1
    print("skip：%s（退出码 0，不判失败）" % reason)
    return 0


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _close_page(page):
    """收掉浏览器：`_cdp` 按进程组杀（`os.killpg`），Windows 没有这个 API
    （AttributeError，已知平台坑）。这里兜住它并显式杀掉残留进程——收尾
    异常不该把「全部断言已通过」的结论带走，也不留孤儿浏览器。
    """
    try:
        page.__exit__(None, None, None)
    except AttributeError:
        proc = getattr(page, "_proc", None)
        if proc is not None:
            try:
                proc.kill()
                proc.wait(timeout=10)
            except (OSError, subprocess.TimeoutExpired):
                pass


class _Server:
    """起一个 notdnd_web 子进程：随机端口 + 自己的临时存档目录。

    `start_new_session=True` 让它自成一个会话 / 进程组，收摊时按进程组杀，
    连带子进程一起收干净；`__enter__` 失败也照样收摊，不留孤儿。
    AI 三个键显式清空：与开发机上的 `.env` / 环境变量无关，这个子进程确定离线。
    """

    def __init__(self, seed=None):
        self.dir = tempfile.mkdtemp(prefix="notdnd-web-world-themes-")
        self.port = _free_port()
        self.log_path = os.path.join(self.dir, "server.log")
        self.seed = seed
        self.proc = None
        self._log = None

    def __enter__(self):
        # 播种必须在服务起来之前落到存档目录：服务端按需从磁盘载入，
        # 起来后再写盘只会碰到它自己的缓存。
        if self.seed is not None:
            self.seed(self.dir)
        env = dict(os.environ, NOTDND_SAVE=self.dir, NOTDND_HOST="127.0.0.1",
                   NOTDND_PORT=str(self.port), PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8",
                   BASE_URL="", MODEL="", API_KEY="")
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
            except (ProcessLookupError, PermissionError, OSError,
                    AttributeError):
                # Windows 没有 killpg / getpgid（AttributeError）→ 退回杀本进程。
                # 与 test_notdnd_web.py 同一处置；不吞掉结果、也不留孤儿服务。
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


# ── 取色（浏览器计算值）────────────────────────────────────────────────
#
# 只做一件事：按 (world, theme) 设置 `html` 上的属性，然后把名单里每个自定义
# 属性的**计算值**读出来。自定义属性本身可能是任意颜色写法（hex / var() 链），
# 所以借隐藏探针元素把它交给浏览器解析成 `rgb()` —— 测试只解析标准形状。
#
# 探针**每个令牌新建一个**、并等两帧再读：Windows 无头 Edge 里对同一个元素
# 连续「改色 → 读 getComputedStyle」会拿到**上一次**的值（Linux Chrome 无此现象），
# 复用探针会让整张矩阵退化成同一个色值——断言随之假通过 / 假失败。
# 新建元素没有旧的计算样式缓存，两帧保证这一次的样式重算已经完成。
_READ_JS = """
window.__wtRead = async function (world, theme) {
  const root = document.documentElement;
  if (world === null) { root.removeAttribute('data-world'); }
  else { root.setAttribute('data-world', world); }
  if (theme === null) { root.removeAttribute('data-theme'); }
  else { root.setAttribute('data-theme', theme); }
  const names = %s;
  const style = getComputedStyle(root);
  const host = document.body || root;
  const nextFrame = function () {
    return new Promise(function (resolve) {
      requestAnimationFrame(function () {
        requestAnimationFrame(function () { resolve(); });
      });
    });
  };
  const raw = {}, value = {};
  for (const name of names) {
    const r = (style.getPropertyValue(name) || '').trim();
    raw[name] = r;
    value[name] = '';
    if (!r) continue;
    const probe = document.createElement('span');
    probe.setAttribute('aria-hidden', 'true');
    probe.style.position = 'absolute';
    probe.style.visibility = 'hidden';
    probe.style.color = r;
    host.appendChild(probe);
    await nextFrame();
    value[name] = getComputedStyle(probe).color || '';
    probe.remove();
  }
  return { raw: raw, value: value,
           world: root.getAttribute('data-world'),
           theme: root.getAttribute('data-theme'),
           system_light:
             window.matchMedia('(prefers-color-scheme: light)').matches };
};
return 1;
""" % json.dumps(list(TOKEN_NAMES))


# ── 颜色 / 对比度 / 色相（全部在测试内计算）────────────────────────────


def _parse_color(text):
    """把浏览器给出的颜色解析成 `(r, g, b, a)`；解析不了返回 None。

    支持 `rgb()` / `rgba()`（逗号或空格、`/` 分隔，分量允许百分号）以及
    `#rgb` / `#rgba` / `#rrggbb` / `#rrggbbaa`。
    """
    if not text:
        return None
    text = text.strip()
    if text.startswith("#"):
        body = text[1:]
        if len(body) in (3, 4):
            body = "".join(ch * 2 for ch in body)
        if len(body) not in (6, 8):
            return None
        try:
            vals = [int(body[i:i + 2], 16) for i in range(0, len(body), 2)]
        except ValueError:
            return None
        alpha = vals[3] / 255.0 if len(vals) == 4 else 1.0
        return (vals[0], vals[1], vals[2], alpha)
    match = re.match(r"^rgba?\(([^)]*)\)$", text, re.IGNORECASE)
    if not match:
        return None
    parts = [p for p in re.split(r"[,\s/]+", match.group(1).strip()) if p]
    if len(parts) not in (3, 4):
        return None
    comps = []
    for part in parts:
        is_percent = part.endswith("%")
        try:
            number = float(part[:-1] if is_percent else part)
        except ValueError:
            return None
        comps.append((number, is_percent))
    # 分量百分比按 255 归一，alpha 百分比按 1 归一（CSS 的两套比例）。
    def scale(item):
        number, is_percent = item
        return number * 255.0 / 100.0 if is_percent else number

    rgb = [min(255.0, max(0.0, scale(item))) for item in comps[:3]]
    if len(comps) == 4:
        number, is_percent = comps[3]
        alpha = number / 100.0 if is_percent else number
    else:
        alpha = 1.0
    return (int(rgb[0]), int(rgb[1]), int(rgb[2]),
            min(1.0, max(0.0, alpha)))


def _hue(rgb):
    """sRGB 颜色的色相（度，0–360）；无彩色（delta = 0）返回 None。"""
    r, g, b = (c / 255.0 for c in rgb[:3])
    mx, mn = max(r, g, b), min(r, g, b)
    delta = mx - mn
    if delta == 0:
        return None
    if mx == r:
        hue = 60.0 * (((g - b) / delta) % 6.0)
    elif mx == g:
        hue = 60.0 * (((b - r) / delta) + 2.0)
    else:
        hue = 60.0 * (((r - g) / delta) + 4.0)
    return hue % 360.0


def _hue_distance(hue_a, hue_b):
    """两个色相在色环上的最近距离（0–180）。"""
    delta = abs(hue_a - hue_b) % 360.0
    return min(delta, 360.0 - delta)


def _luminance(rgb):
    """WCAG 2.1 相对亮度（§4.6 同口径）。"""

    def channel(value):
        c = value / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return (0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1])
            + 0.0722 * channel(rgb[2]))


def _contrast(fg, bg):
    """`(L_亮 + 0.05) / (L_暗 + 0.05)`；两个颜色都必须不透明。"""
    lum_a, lum_b = _luminance(fg), _luminance(bg)
    if lum_a < lum_b:
        lum_a, lum_b = lum_b, lum_a
    return (lum_a + 0.05) / (lum_b + 0.05)


# ── 断言 ───────────────────────────────────────────────────────────────


def _cells(matrix, keys, token):
    """取出若干 (外观, 世界) 格子上的某个令牌值，返回 [(外观, 世界, 值)]。"""
    return [(key, world_label, matrix[(key, world_label)]["value"].get(token))
            for key in keys for world_label, _ in WORLDS]


def _same(cells):
    first = cells[0][2]
    return bool(first) and all(value and value == first for _, _, value in cells)


def _describe(cells):
    return "；".join("%s/%s=%s" % (key, world, value or "（未定义）")
                    for key, world, value in cells)


def _check_emulation(matrix):
    for key, label, media, _theme in APPEARANCES:
        want_light = media == "light"
        got = {world: matrix[(key, world)]["system_light"]
               for world, _ in WORLDS}
        check("模拟 prefers-color-scheme=%s 在页面内生效（%s）" % (media, label),
              all(value is want_light for value in got.values()),
              "matchMedia 结果=%r" % (got,))


def _check_semantic(matrix):
    """验收 1：B 层语义令牌跨世界逐项相同（暗组 / 明组各一次）。"""
    groups = (("暗", DARK_KEYS), ("明", LIGHT_KEYS))
    for group_name, keys in groups:
        for token in SEMANTIC_TOKENS:
            cells = _cells(matrix, keys, token)
            check("B 层 %s 逐项相同（%s色：跨世界 + 强制/跟随）"
                  % (token, group_name), _same(cells), _describe(cells))
        for token in SCOPE_TOKENS:
            cells = _cells(matrix, keys, token)
            if all(not value for _, _, value in cells):
                # 三令牌尚未登记（§12 第 3 项待拍板）——以「跨世界同为未定义」
                # 通过；登记落地后本断言自动获得覆盖（一出现就纳入比较）。
                check("B 层 %s 一致（登记落地后自动获得覆盖）" % token, True,
                      "当前未定义：跨世界同为未定义")
            else:
                check("B 层 %s 逐项相同（%s色：跨世界 + 强制/跟随）"
                      % (token, group_name), _same(cells), _describe(cells))


def _check_sanity(matrix):
    """防呆：世界轴与明暗轴必须真的生效，否则上面几条会空转。"""
    for key, label, _media, _theme in APPEARANCES:
        missing = matrix[(key, "缺省")]["value"]
        modern = matrix[(key, "modern")]["value"]
        diff = [name for name in TOKEN_NAMES if missing.get(name) != modern.get(name)]
        check("缺省与显式 modern 同值（%s）" % label, not diff,
              "不一致的令牌=%r" % (diff,))

        values = {world: matrix[(key, world)]["value"].get("--bg-0")
                  for world in NAMED_WORLDS}
        check("世界轴生效：三主题 --bg-0 互不相同（%s）" % label,
              all(values.values()) and len(set(values.values())) == len(NAMED_WORLDS),
              "--bg-0=%r" % (values,))

    for world_label, _ in WORLDS:
        dark = [matrix[(key, world_label)]["value"].get("--bg-0")
                for key in DARK_KEYS]
        light = [matrix[(key, world_label)]["value"].get("--bg-0")
                 for key in LIGHT_KEYS]
        check("明暗轴生效：强制档 = 跟随档、且暗 ≠ 明（%s）" % world_label,
              all(dark) and all(light) and dark[0] == dark[1]
              and light[0] == light[1] and dark[0] != light[0],
              "暗=%r 明=%r" % (dark, light))

    for token in SEMANTIC_TOKENS:
        changed = []
        for world_label, _ in WORLDS:
            dark = matrix[(DARK_KEYS[0], world_label)]["value"].get(token)
            light = matrix[(LIGHT_KEYS[0], world_label)]["value"].get(token)
            changed.append(bool(dark) and bool(light) and dark != light)
        check("B 层 %s 随明暗改变（四个世界都是两套值）" % token, all(changed),
              "逐世界=%r" % (changed,))


def _accent_metrics(value):
    """按一格上的计算值算出 accent 的色相 / 距离 / 对比度。

    返回 `(指标 dict, 问题列表)`：任一取色不可解析就判不出指标（问题列表非空），
    由调用方按「取色失败」处置——不猜、不放过。
    """
    problems = []
    accent = _parse_color(value.get("--accent"))
    if accent is None:
        return None, ["--accent 不是可解析的颜色：%r" % (value.get("--accent"),)]
    if accent[3] < 1.0:
        problems.append("--accent 带透明度 alpha=%.2f，对比度无法直接判定"
                        % accent[3])
    hue = _hue(accent)
    if hue is None:
        problems.append("--accent 无彩色（delta = 0），色相约束无法满足")

    reference_hues = {}
    for name in ("--danger", "--success"):
        color = _parse_color(value.get(name))
        if color is None:
            problems.append("%s 不是可解析的颜色：%r" % (name, value.get(name)))
        elif _hue(color) is None:
            problems.append("%s 无彩色（delta = 0），色相距离无法计算" % name)
        else:
            reference_hues[name] = _hue(color)
    for name in ("--bg-0", "--bg-1"):
        color = _parse_color(value.get(name))
        if color is None:
            problems.append("%s 不是可解析的颜色：%r" % (name, value.get(name)))
        elif color[3] < 1.0:
            problems.append("%s 带透明度 alpha=%.2f" % (name, color[3]))
    if problems:
        return None, problems
    return {
        "hue": hue,
        "d_danger": _hue_distance(hue, reference_hues["--danger"]),
        "d_success": _hue_distance(hue, reference_hues["--success"]),
        "c_bg0": _contrast(accent, _parse_color(value["--bg-0"])),
        "c_bg1": _contrast(accent, _parse_color(value["--bg-1"])),
    }, []


def _check_accent(matrix):
    """验收 2：`--accent` 的色相约束（§6.2）与对比度（§4.6）。"""
    table = {}
    for key, label, _media, _theme in APPEARANCES:
        for world_label, _world in WORLDS:
            value = matrix[(key, world_label)]["value"]
            metrics, problems = _accent_metrics(value)
            name = "%s / %s" % (label, world_label)
            table[name] = (value, metrics)
            if metrics is None:
                check("accent 色相约束（§6.2）：%s" % name, False,
                      "取色失败：" + "；".join(problems))
                check("accent 对比度 ≥4.5:1（§4.6）：%s" % name, False,
                      "取色失败：" + "；".join(problems))
                continue

            hue, d_danger, d_success = (metrics["hue"], metrics["d_danger"],
                                        metrics["d_success"])
            in_red_band = hue >= RED_BAND[0] or hue <= RED_BAND[1]
            hue_ok = (not in_red_band and d_danger is not None
                      and d_danger >= MIN_HUE_DISTANCE
                      and d_success is not None
                      and d_success >= MIN_HUE_DISTANCE)
            check("accent 色相约束（§6.2）：%s" % name, hue_ok,
                  "色相=%.1f°（红带 [%.0f°, %.0f°]）Δdanger=%s Δsuccess=%s"
                  % (hue, RED_BAND[0], RED_BAND[1],
                     "%.1f°" % d_danger if d_danger is not None else "n/a",
                     "%.1f°" % d_success if d_success is not None else "n/a"))
            check("accent 对比度 ≥4.5:1（§4.6）：%s" % name,
                  metrics["c_bg0"] >= MIN_CONTRAST
                  and metrics["c_bg1"] >= MIN_CONTRAST,
                  "accent/bg-0=%.2f:1 accent/bg-1=%.2f:1（下限 %.1f）"
                  % (metrics["c_bg0"], metrics["c_bg1"], MIN_CONTRAST))
    return table


def _print_tables(matrix, accent_table):
    print("取值矩阵（浏览器计算值；世界 × 外观）：")
    print("  %-22s %-18s %-18s %s" % ("外观 / 世界", "--danger", "--success",
                                      "--scope-public / -party / -private"))
    for key, label, _media, _theme in APPEARANCES:
        for world_label, _world in WORLDS:
            value = matrix[(key, world_label)]["value"]
            scopes = " / ".join(value.get(name) or "（未定义）"
                                for name in SCOPE_TOKENS)
            print("  %-22s %-18s %-18s %s"
                  % ("%s / %s" % (label, world_label),
                     value.get("--danger") or "（未定义）",
                     value.get("--success") or "（未定义）", scopes))
    print()
    print("--accent（§6.2 / §4.6 的计算依据）：")
    print("  %-22s %-18s %8s %10s %10s %10s %10s"
          % ("外观 / 世界", "--accent", "色相", "Δdanger", "Δsuccess",
             "对比 bg-0", "对比 bg-1"))
    for name, (value, metrics) in accent_table.items():
        if metrics is None:
            print("  %-22s %-18s %s" % (name, value.get("--accent"), "（无法计算）"))
            continue
        print("  %-22s %-18s %7.1f° %9.1f° %9.1f° %9.2f:1 %9.2f:1"
              % (name, value.get("--accent"), metrics["hue"], metrics["d_danger"],
                 metrics["d_success"], metrics["c_bg0"], metrics["c_bg1"]))


def _collect_matrix(page):
    """按外观 × 世界取满矩阵；返回 {(外观键, 世界名): 读数}。"""
    page.js(_READ_JS)
    matrix = {}
    for key, _label, media, theme in APPEARANCES:
        page.send("Emulation.setEmulatedMedia",
                  features=[{"name": "prefers-color-scheme", "value": media}])
        time.sleep(0.2)
        for world_label, world_value in WORLDS:
            matrix[(key, world_label)] = page.js(
                "return window.__wtRead(%s, %s);"
                % (json.dumps(world_value), json.dumps(theme)))
    # 收尾：属性与模拟都还原，不给后续用例留下被改过的页面。
    page.js("const r = document.documentElement;"
            "r.removeAttribute('data-world'); r.removeAttribute('data-theme');"
            "return 1;")
    try:
        page.send("Emulation.setEmulatedMedia", features=[])
    except RuntimeError:
        pass
    return matrix


# ── W2 接线（Issue #334）───────────────────────────────────────────────
#
# 播种四局：三主题各一局（主题来自**世界模板的 world_type 声明**），外加一局
# **未绑定**的老档快照（没有可用的 world_type → modern）。每局十四条叙事，
# 用来把「切档不重放叙事 / 不丢滚动位置 / 不动焦点」变成可复现的断言。
SEED_WORLDS = (
    ("w-fantasy", "emberfall", "well_temperature", "fantasy", "F"),
    ("w-modern", "threshold", "rain_line_seven", "modern", "M"),
    ("w-future", "entropic-net", "backup_forty_one", "future", "U"),
    ("w-legacy", "", "", "modern", "L"),
)
SEED_LOG_COUNT = 14
WORLD_LABEL = {"fantasy": "奇幻", "modern": "现代", "future": "未来"}

_WIRING_JS = """
window.__w2 = {};
window.__w2.state = function () {
  const root = document.documentElement;
  const sc = document.getElementById('playScroll');
  const msgs = document.querySelectorAll('#stream .msg');
  return {
    world: root.getAttribute('data-world'),
    bg0: getComputedStyle(root).getPropertyValue('--bg-0').trim(),
    view: window.VIEW,
    sid: window.SID,
    count: msgs.length,
    texts: Array.prototype.map.call(msgs, function (m) { return m.textContent; }),
    scrollTop: sc ? Math.round(sc.scrollTop) : -1,
    scrollMax: sc ? Math.round(sc.scrollHeight - sc.clientHeight) : -1,
    active: document.activeElement
      ? (document.activeElement.id || document.activeElement.tagName) : ''
  };
};
/* focus() 计数：切档 / 主题投影都不该动焦点，这是「不动焦点」的可测口径。 */
window.__w2.focusWatch = function () {
  if (!window.__focusPatched) {
    const orig = HTMLElement.prototype.focus;
    HTMLElement.prototype.focus = function () {
      window.__focusCalls += 1;
      return orig.apply(this, arguments);
    };
    window.__focusPatched = true;
  }
  window.__focusCalls = 0;
  return 1;
};
window.__w2.settingsRow = function () {
  const el = document.getElementById('setWorldTheme');
  if (!el) return { present: false };
  const row = el.closest('.set-row');
  const label = row ? row.querySelector('.set-label') : null;
  return {
    present: true,
    value: (el.textContent || '').trim(),
    theme: el.getAttribute('data-world-theme'),
    label: label ? label.textContent.replace(/\\s+/g, '') : '',
    controls: row
      ? row.querySelectorAll('button,input,select,textarea,[contenteditable]').length : -1,
    worldControls: document.querySelectorAll(
      '#settingsForm [data-set="world"], #settingsForm [data-toggle="world"],'
      + ' #settingsForm [data-world-switch]').length
  };
};
/* 零回归对照用的计算样式签名：只看会随令牌变的那几项。 */
window.__w2.signature = function () {
  const sels = ['html', 'body', '#v-play .top', '#stream', '#stream .msg',
                '#actZone', '#turnComposer'];
  const out = {};
  for (const sel of sels) {
    const el = document.querySelector(sel);
    if (!el) { out[sel] = null; continue; }
    const cs = getComputedStyle(el);
    out[sel] = {
      bg: cs.backgroundColor, color: cs.color,
      bg0: cs.getPropertyValue('--bg-0').trim(),
      accent: cs.getPropertyValue('--accent').trim(),
      font: cs.fontFamily
    };
  }
  return out;
};
window.__w2.nextFrame = function () {
  return new Promise(function (resolve) {
    requestAnimationFrame(function () {
      requestAnimationFrame(function () { resolve(1); });
    });
  });
};
return 1;
"""


def _click(page, selector):
    """点一个元素；找不到返回 False（断言里就能看出「没这个按钮」）。"""
    return page.js(
        "const e = document.querySelector(%s);"
        "if (!e) return false; e.click(); return true;" % json.dumps(selector))


def _seed_sessions(save_dir):
    """在子进程服务的存档目录里播种四局（同 test_ui_smoke 的播种手法）。"""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    import notdnd_web                      # 延迟导入：不跑接线用例就不碰后端模块

    saved_dir = notdnd_web.SAVE_DIR
    notdnd_web.SAVE_DIR = pathlib.Path(save_dir)
    try:
        for sid, world_key, scenario_id, _world, prefix in SEED_WORLDS:
            session = notdnd_web.Session(sid)
            session.save_name = "播种 · %s" % prefix
            session.guide["world_key"] = world_key
            session.guide["scenario_id"] = scenario_id
            for i in range(1, SEED_LOG_COUNT + 1):
                session.log.append({
                    "seq": i, "kind": "narrate",
                    "text": "%s-%02d 这一段叙事只属于这一局，用来核对切档有没有重放。"
                            % (prefix, i),
                })
            session.seq = SEED_LOG_COUNT
            session.save()                 # 落盘后由子进程服务按需载入
    finally:
        notdnd_web.SAVE_DIR = saved_dir
        for sid, _w, _s, _t, _p in SEED_WORLDS:
            notdnd_web._sessions.pop(sid, None)


def _check_wiring(page):
    """验收：按 world_type 写 html[data-world]、设置区只读、切档无副作用。"""
    print()
    print("W2 接线（Issue #334）：按 world_type 写 html[data-world] / 设置区只读 / 切档副作用")
    page.js("try { localStorage.removeItem('prism.sid');"
            " localStorage.removeItem('prism.settings'); } catch (e) {} return 1;")
    page.navigate("/", settle=1.2)
    listed = page.wait_for(
        "return document.querySelectorAll('[data-open]').length >= %d;"
        % len(SEED_WORLDS), timeout=20)
    check("大厅列出播种的四局存档", listed is True, "存档行不足 %d 条" % len(SEED_WORLDS))
    page.js(_WIRING_JS)

    seen_bg = {}
    previous_prefix = None
    for sid, _world_key, _scenario, world, prefix in SEED_WORLDS:
        label = WORLD_LABEL[world]
        # 真实切档路径：菜单 → 返回会话列表 → 点这一局的存档行
        if page.js("return window.VIEW;") != "v-lobby":
            _click(page, "#btnMenu")
            _click(page, "#btnBackToLobby")
            page.wait_for("return window.VIEW === 'v-lobby';", timeout=10)
        page.js("window.__w2.focusWatch(); return 1;")
        opened = page.js(
            "const b = document.querySelector('[data-open=%s]');"
            "if (!b) return false; b.click(); return true;" % json.dumps(sid))
        landed = page.wait_for(
            "return window.SID === %s && window.VIEW === 'v-play'"
            " && document.documentElement.getAttribute('data-world') === %s"
            " && Array.prototype.some.call(document.querySelectorAll('#stream .msg'),"
            "      function (m) { return m.textContent.indexOf(%s) === 0; });"
            % (json.dumps(sid), json.dumps(world), json.dumps(prefix + "-01")),
            timeout=25)
        check("切到存档 %s（%s）并在主画面列出本局叙事" % (sid, label),
              bool(opened and landed), "未进入主画面 / 叙事条数不足")
        if not (opened and landed):
            continue

        state = page.js("return window.__w2.state();")
        focus_calls = page.js("return window.__focusCalls;")
        texts = state["texts"] or []
        mine = [t for t in texts if t.startswith(prefix + "-")]
        check("会话加载后 html[data-world]=%s（%s）" % (world, sid),
              state["world"] == world, "实际=%r" % (state["world"],))
        check("叙事只含本局内容且每条只出现一次（%s）" % sid,
              len(texts) == SEED_LOG_COUNT and len(mine) == SEED_LOG_COUNT
              and len(set(mine)) == SEED_LOG_COUNT,
              "总条数=%d 本局=%d 去重后=%d"
              % (len(texts), len(mine), len(set(mine))))
        if previous_prefix:
            leftover = [t for t in texts if t.startswith(previous_prefix + "-")]
            check("上一局（%s）的叙事没有残留（%s）" % (previous_prefix, sid),
                  not leftover, "仍含 %d 条 %s-*" % (len(leftover), previous_prefix))
        check("切档过程没有调用 focus()（%s）" % sid, focus_calls == 0,
              "focus 调用 %r 次" % (focus_calls,))
        seen_bg[sid] = state["bg0"]

        # 设置区：只读展示本局主题，无切换控件、不写回偏好
        _click(page, "#btnPlaySettings")
        page.wait_for("return window.VIEW === 'v-settings';", timeout=10)
        row = page.js("return window.__w2.settingsRow();")
        check("设置区只读显示「本局世界主题：%s」（%s）" % (label, sid),
              row.get("present") and row.get("value") == label
              and row.get("theme") == world and "本局世界主题" in (row.get("label") or ""),
              "读数=%r" % (row,))
        check("设置区没有主题切换控件（%s）" % sid,
              row.get("controls") == 0 and row.get("worldControls") == 0,
              "行内控件=%r 全局控件=%r" % (row.get("controls"), row.get("worldControls")))
        _click(page, "#btnSettingsBack")
        page.wait_for("return window.VIEW === 'v-lobby';", timeout=10)
        previous_prefix = prefix

    themed_bg = [seen_bg.get(sid) for sid, _w, _s, _t, _p in SEED_WORLDS[:3]]
    check("三主题的实际底色互不相同（主题真的生效，不只是属性）",
          all(themed_bg) and len(set(themed_bg)) == 3,
          "--bg-0=%r" % (themed_bg,))

    # 设置页的返回落在会话列表：再点末局（未绑定老档）回主画面做副作用对照。
    _click(page, '[data-open="w-legacy"]')
    back = page.wait_for(
        "return window.VIEW === 'v-play' && window.SID === 'w-legacy'"
        " && Array.prototype.some.call(document.querySelectorAll('#stream .msg'),"
        "      function (m) { return m.textContent.indexOf('L-01') === 0; });",
        timeout=25)
    check("从设置退回列表再进末局（未绑定老档）", back is True, "未回到主画面")

    # 主题重投影：换一个 world_type 再走一遍 applyState，叙事 / 滚动 / 焦点都不动。
    # 先关掉「自动置底」——它是既有设置，applyState 末尾本来就会滚到最新一段，
    # 与主题投影无关；关掉它，「主题这一步不动滚动」才量得准。
    page.js("window.SETTINGS.autoScroll = false; return 1;")
    page.js(
        "const sc = document.getElementById('playScroll');"
        "sc.scrollTop = Math.round((sc.scrollHeight - sc.clientHeight) / 2);"
        "window.__w2mark = document.querySelector('#stream .msg');"
        "const ta = document.getElementById('turnInput'); if (ta) ta.focus();"
        "window.__w2.focusWatch(); return 1;")
    time.sleep(0.3)
    before = page.js("return window.__w2.state();")
    settings_before = page.js("return localStorage.getItem('prism.settings');")
    applied = page.js(
        "window.applyState(Object.assign({}, window.ST, {world_type: 'future'}));"
        "return document.documentElement.getAttribute('data-world');")
    page.js("return window.__w2.nextFrame();")
    after = page.js("return window.__w2.state();")
    kept_node = page.js(
        "return document.querySelector('#stream .msg') === window.__w2mark;")
    focus_calls = page.js("return window.__focusCalls;")
    settings_after = page.js("return localStorage.getItem('prism.settings');")

    check("主题重投影把 html[data-world] 改成 future", applied == "future",
          "实际=%r" % (applied,))
    check("主题重投影没有重画叙事（DOM 节点仍是同一批）",
          kept_node is True and after["count"] == before["count"],
          "节点保留=%r 条数=%r→%r" % (kept_node, before["count"], after["count"]))
    check("主题重投影没有丢滚动位置（关闭自动置底后逐像素比对）",
          after["scrollTop"] == before["scrollTop"] and after["scrollTop"] > 0,
          "scrollTop=%r→%r（max=%r）"
          % (before["scrollTop"], after["scrollTop"], after["scrollMax"]))
    check("主题重投影没有动焦点", after["active"] == "turnInput"
          and after["active"] == before["active"] and focus_calls == 0,
          "active=%r→%r focus=%r" % (before["active"], after["active"], focus_calls))
    check("主题重投影没有写回偏好 / 存档", settings_after == settings_before,
          "prism.settings=%r→%r" % (settings_before, settings_after))

    # 未知值 / 缺失值都落 modern，且不残留上一局的取值。
    unknown = page.js(
        "window.applyState(Object.assign({}, window.ST, {world_type: 'scifi'}));"
        "return document.documentElement.getAttribute('data-world');")
    check("未知 world_type（scifi）落 modern 且不残留 future", unknown == "modern",
          "实际=%r" % (unknown,))
    missing = page.js(
        "const s = Object.assign({}, window.ST); delete s.world_type;"
        "window.applyState(s);"
        "return document.documentElement.getAttribute('data-world');")
    check("缺失 world_type 落 modern", missing == "modern", "实际=%r" % (missing,))

    # 零回归对照：末局是未绑定的老档（快照里没有可用的 world_type），
    # 「显式 modern」与「不设属性」的计算样式必须逐项相同。
    page.js("document.documentElement.setAttribute('data-world', 'modern');"
            "return window.__w2.nextFrame();")
    explicit = page.js("return window.__w2.signature();")
    page.js("document.documentElement.removeAttribute('data-world');"
            "return window.__w2.nextFrame();")
    unset = page.js("return window.__w2.signature();")
    diff = [sel for sel in explicit if explicit[sel] != unset[sel]]
    check("显式 modern 与未设置属性的计算样式逐项相同（零回归对照）",
          not diff and bool(explicit.get("html")), "不一致的选择器=%r" % (diff,))
    print("  对照选择器：%s" % "、".join(explicit))
    print("  html 计算样式（显式 modern）：%r" % (explicit.get("html"),))


def _run_checks():
    """真实跑一遍（前置已确认齐备）；返回进程退出码。"""
    if _mandatory():
        print("世界主题不变式（强制模式）：前置条件齐备，真实执行")
    print("世界主题不变式：起服务 → CDP 打开首页 → 按主题矩阵取计算值")
    with _Server(seed=_seed_sessions) as server:
        base = "http://127.0.0.1:%d" % server.port
        try:
            page = _cdp.Browser(base).__enter__()
        except _cdp.CDPUnavailable as exc:
            # 前置检查说「齐备」了，起浏览器仍失败 → 按同一口径处置
            return _verdict_missing(str(exc), _mandatory())
        try:
            page.navigate("/", settle=2.0)
            loaded = page.wait_for("return document.readyState === 'complete';",
                                   timeout=15)
            check("首页加载完成", loaded is True, "readyState 未到 complete")
            if not loaded:
                return 1

            # 验收 3：无外链断言（UI 冒烟同款）——只认跨源 http(s) 请求。
            ext = page.js(
                "const o = location.origin;"
                "return performance.getEntriesByType('resource')"
                "  .map(e => e.name)"
                "  .filter(u => /^https?:/i.test(u) && new URL(u).origin !== o);")
            check("页面加载不发起外部资源请求", not ext,
                  "外部请求：%r" % (ext,))

            matrix = _collect_matrix(page)
            print()
            _check_emulation(matrix)
            print()
            print("--accent 的色相 / 对比度（均由本测试从计算值算出）：")
            accent_table = _check_accent(matrix)
            print()
            print("B 层语义令牌与防呆：")
            _check_semantic(matrix)
            _check_sanity(matrix)
            print()
            _print_tables(matrix, accent_table)
            _check_wiring(page)
        finally:
            _close_page(page)

    print()
    print("通过 %d / 共 %d" % (len(_oks), len(_oks) + len(_fails)))
    if _fails:
        return 1
    if _mandatory():
        print("世界主题不变式真实执行并通过")
    return 0


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
    return _run_checks()


if __name__ == "__main__":
    sys.exit(main())
