#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""战棋 UI 交互测试（M13-B12，Issue #427）。

在真浏览器 + 真服务里回答三个问题：

  1. **fixture 渲染**：把一份手工构造的 battle view（含双 surface、
     高度差、阻挡、掩体、危险、机关与四名单位）经 `window.__BATTLE.apply`
     注入渲染路径，断言画板把高度差 / 阻挡 / 危险 / 机关 / 选中态画出来；
  2. **意图提交与快照更新**：移动意图带 `expected_revision` 发给服务器，
     成功后以服务器 snapshot 重画；409 stale_revision 时就地提示并按
     服务器快照恢复；
  3. **真实 API 全链路**：起一场真战斗（#426 服务端闭环），点选可达格
     → 移动 → 攻击 / 结束回合，断言 revision 推进、预览与服务器结果
     一致、返回叙事视图不影响旧界面。

fixture 的形状与 `tactical_battle_service.battle_view` 的返回一致
（#426 契约）；默认服务端模板只有单 surface、零高度差（登记口径），
高度差 / 多 surface 的展示由 fixture 覆盖。

前置（static/index.html、websocket-client、浏览器）缺失时默认 skip（退出码 0）；
设 NOTDND_UI_BATTLE_MANDATORY=1 时强制为失败（CI 口径，同 test_ui_smoke）。

直接运行：python3 tests/test_ui_battle.py
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import _cdp  # noqa: E402
import test_ui_smoke as smoke  # noqa: E402  复用断言 / 播种辅助
from test_ui_smoke import (  # noqa: E402
    _click, _enter_session, _get_json, _offline_env, check,
)

MANDATORY_ENV = "NOTDND_UI_BATTLE_MANDATORY"

_TRUTHY = ("1", "true", "yes", "on")

# ── Windows 安全夹具 ────────────────────────────────────────────────────
#
# smoke 的 _Server 与 _cdp.Browser 的收摊都走 os.killpg（POSIX 进程组），
# Windows 上没有这个接口。这里用同一套形状的夹具、收摊改为「进程组优先、
# Windows 退回 terminate / taskkill」，其余行为一致；CI（Linux）上两条
# 路径等价。

import shutil  # noqa: E402
import signal  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import tempfile  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402


def _kill_tree(proc):
    """收掉一个子进程：POSIX 按进程组，Windows 退回 terminate（不行就 taskkill）。"""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            pass
    try:
        proc.wait(timeout=10)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (AttributeError, ProcessLookupError, PermissionError, OSError):
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass


class _Server:
    """起一个 notdnd_web 子进程（形状同 smoke._Server，收摊 Windows 安全）。"""

    def __init__(self, extra_env=None):
        self.dir = tempfile.mkdtemp(prefix="notdnd-battle-ui-")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = int(probe.getsockname()[1])
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
        # start_new_session：子进程自成进程组，收摊时 killpg 只杀它自己，
        # 不会把调用方（run_all.sh / 测试进程）一起带走（smoke._Server 同款）。
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
            _kill_tree(self.proc)
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
                raise RuntimeError("服务进程提前退出")
            try:
                with urllib.request.urlopen(url, timeout=2):
                    return
            except (urllib.error.URLError, OSError):
                time.sleep(0.1)
        raise RuntimeError("服务未在 30 秒内就绪")


class _Page(_cdp.Browser):
    """_cdp.Browser 的子类：只把收摊改成 Windows 安全（其余全部继承）。"""

    def __exit__(self, *_exc):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:                   # noqa: BLE001
                pass
            self._ws = None
        if self._proc is not None:
            _kill_tree(self._proc)
            self._proc = None
        shutil.rmtree(self._profile, ignore_errors=True)
        return False


def _truthy(raw):
    return (raw or "").strip().lower() in _TRUTHY


# ── fixture：与 battle_view 返回同形的战斗快照（手工构造）───────────────
#
# 战场 5×3，双 surface：
#   · ground：高度 H0，含一格阻挡 + 一格危险（蒸汽）+ 出生点；
#   · upper ：高度 H1/H2（同一 q/r 与 ground 重叠，表达「楼层」），
#     含掩体字段 cover_grade 与一格视线遮蔽。
# 单位四名（2 队伍 + 2 敌体），物件两个（机关 + 危险物）。

def _cell(cell_id, q, r, surface, elev=0, **extra):
    cell = {
        "cell_id": cell_id, "q": q, "r": r, "surface_id": surface,
        "terrain_id": "open", "terrain_tags": ["open"],
        "movement_cost": 1, "blocks_movement": False, "blocks_los": False,
        "spawnable": False, "elevation_level": elev,
    }
    cell.update(extra)
    return cell


def _fixture_view():
    cells = []
    for r in range(3):
        for q in range(5):
            cells.append(_cell("cell-g-%d%d" % (q, r), q, r, "ground"))
    # upper 层：同 (q,r) 重叠，层级 H1（列 q=1）与 H2（列 q=3）。
    for r in range(3):
        for q in (1, 3):
            elev = 1 if q == 1 else 2
            cells.append(_cell(
                "cell-u-%d%d" % (q, r), q, r, "upper", elev=elev,
                cover_grade="medium" if q == 1 else "",
                **({"terrain_id": "barricade-line"} if q == 3 and r == 1 else {})))
    # 地形语义：一格阻挡、一格危险、一个出生点。
    for cell in cells:
        if cell["cell_id"] == "cell-g-20":
            cell["terrain_id"] = "barricade-line"
            cell["blocks_movement"] = True
            cell["blocks_los"] = True
        if cell["cell_id"] == "cell-g-02":
            cell["terrain_id"] = "steam-vent-zone"
            cell["hazard_refs"] = ["steam-vent-01"]
        if cell["cell_id"] in ("cell-g-00", "cell-g-01"):
            cell["spawnable"] = True

    return {
        "kind": "tactical.battle_record",
        "battle_id": "fixture-1",
        "status": "active",
        "outcome": "",
        "engine": "tactics",
        "battlefield_meta": {"battlefield_id": "bf-fixture", "mode": "abstract"},
        "state_revision": 7,
        "last_event_sequence": 12,
        "round": 2,
        "turn": 1,
        "active_unit_id": "u-hero",
        "turn_order": ["u-hero", "e-1", "u-ally", "e-2"],
        "turn_ap": {"u-hero": 3, "u-ally": 3},
        "participants": [
            {"unit_id": "u-hero", "cell_id": "cell-g-00", "party": True,
             "unit": {"name": "试炼者", "side": "party", "vitality": 12,
                      "max_vitality": 18, "downed": False, "dead": False}},
            {"unit_id": "u-ally", "cell_id": "cell-g-01", "party": True,
             "unit": {"name": "同伴", "side": "party", "vitality": 9,
                      "max_vitality": 15, "downed": False, "dead": False}},
            {"unit_id": "e-1", "cell_id": "cell-g-41", "party": False,
             "unit": {"name": "标准·1", "side": "enemy", "vitality": 20,
                      "max_vitality": 26, "downed": False, "dead": False}},
            {"unit_id": "e-2", "cell_id": "cell-u-32", "party": False,
             "unit": {"name": "标准·2", "side": "enemy", "vitality": 0,
                      "max_vitality": 26, "downed": False, "dead": True}},
        ],
        "battlefield": {
            "battlefield_id": "bf-fixture",
            "cells": cells,
            "vertical_links": [
                {"link_id": "vl-1", "kind": "stairs",
                 "from_cell_id": "cell-g-11", "to_cell_id": "cell-u-11"},
            ],
            "scale": {"mode": "abstract", "unit": "cell"},
            "geometry": {"type": "hex_axial"},
        },
        "objects": [
            {"object_id": "obj-gate", "definition_id": "mech.blast-gate",
             "cell_id": "cell-g-30", "state": "closed", "blocks_movement": True,
             "blocks_los": False, "triggered": False,
             "movable": False, "destructible": False},
            {"object_id": "obj-vent", "definition_id": "hazard.steam-vent",
             "cell_id": "cell-g-02", "state": "armed", "blocks_movement": False,
             "blocks_los": False, "triggered": False,
             "movable": False, "destructible": False},
        ],
        "object_definitions": {
            "mech.blast-gate": {
                "definition_id": "mech.blast-gate", "kinds": ["mechanism"],
                "initial_state": "closed",
                "states": {"closed": {}, "open": {}},
                "transitions": [{"from": "closed", "to": "open", "trigger": "interact"}],
            },
        },
        "legal_actions": [{"action": "move", "ap_cost": 1},
                          {"action": "basic_attack", "ap_cost": 2},
                          {"action": "assist_action", "ap_cost": 1}],
        "events_since": [],
        "recent_events": [],
    }


# ── 断言辅助 ──────────────────────────────────────────────

def _wait_board(page, expr, timeout=15):
    return page.wait_for(
        "return !!(window.__BATTLE && window.__BATTLE().loaded) && (%s);" % expr,
        timeout=timeout)


def _stub_move_ok(page, to_cell_id):
    """fetch 桩：/api/tactical/move 回一份「服务器已接受」的 payload。

    捕获请求体供断言（expected_revision / to_cell_id 必须由 UI 填）；
    其余请求透传。返回注入脚本字符串。
    """
    payload = _fixture_view()
    payload["state_revision"] = 8
    for row in payload["participants"]:
        if row["unit_id"] == "u-hero":
            row["cell_id"] = to_cell_id
    script = (
        "window.__REQS = [];"
        "window.__FETCH_ORIG = window.fetch;"
        "window.fetch = function (url, o) {"
        "  const u = String(url);"
        "  if (u.indexOf('/api/tactical/move') >= 0) {"
        "    window.__REQS.push({ url: u, body: o && o.body ? JSON.parse(o.body) : null });"
        "    return Promise.resolve(new Response(JSON.stringify(%s),"
        "      { status: 200, headers: { 'Content-Type': 'application/json' } }));"
        "  }"
        "  return window.__FETCH_ORIG.apply(this, arguments);"
        "};" % json.dumps({"status": "ok", "narrative": "试炼者 移动到 %s。" % to_cell_id,
                           "battle": payload}, ensure_ascii=False))
    return script


def _stub_stale_revision(page):
    """fetch 桩：移动回 409 stale_revision，GET battle 回服务器最新快照。

    409 的恢复路径是「提示刷新并以服务器 snapshot 恢复」——所以同一支桩
    也要接住恢复用的 GET /api/tactical/battle（revision 9 的同一战场）。
    """
    payload = _fixture_view()
    payload["state_revision"] = 9
    get_body = json.dumps({"status": "ok", "battle": payload}, ensure_ascii=False)
    script = (
        "window.__FETCH_ORIG = window.fetch;"
        "window.fetch = function (url, o) {"
        "  const u = String(url);"
        "  if (u.indexOf('/api/tactical/move') >= 0) {"
        "    return Promise.resolve(new Response(JSON.stringify({"
        "      error: '战场状态已变，请刷新后重试', reason: 'stale_revision',"
        "      state_revision: 9,"
        "      reasons: [{ code: 'revision_mismatch', message: 'expected_revision 不一致' }]"
        "    }), { status: 409, headers: { 'Content-Type': 'application/json' } }));"
        "  }"
        "  if (u.indexOf('/api/tactical/battle') >= 0) {"
        "    return Promise.resolve(new Response(JSON.stringify(%s),"
        "      { status: 200, headers: { 'Content-Type': 'application/json' } }));"
        "  }"
        "  return window.__FETCH_ORIG.apply(this, arguments);"
        "};" % get_body)
    return script


# ── 用例一：fixture 渲染 + 意图提交 + 409 恢复 ─────────────────────

def _check_fixture(page):
    page.navigate("/", settle=2.0)
    page.js("localStorage.removeItem('prism.sid'); return 1;")
    page.wait_for("return document.readyState === 'complete';", timeout=15)
    loaded = page.wait_for(
        "return typeof window.__BATTLE === 'function';", timeout=15)
    check("B1：战棋片已加载（window.__BATTLE 就绪）", loaded is True,
          "errs=%r" % (page.js("return window.__ERRS || [];"),))
    injected = page.js(
        "window.__BATTLE.apply(%s); return window.__BATTLE().loaded;"
        % json.dumps(_fixture_view(), ensure_ascii=False))
    check("B1：fixture 经渲染路径注入成功", injected is True,
          "snapshot=%r" % (page.js("return window.__BATTLE ? window.__BATTLE() : null;"),))
    on = page.js(
        "return !!document.getElementById('v-battle').classList.contains('is-on');")
    check("B1：战棋视图点亮（v-battle is-on）", on is True, "on=%r" % (on,))

    counts = page.js(
        "return {"
        " cells: document.querySelectorAll('#battleBoard .b-cell').length,"
        " surfaces: document.querySelectorAll('#battleBoard .b-surface').length,"
        " walls: document.querySelectorAll('#battleBoard .b-wall').length,"
        " elevLabels: document.querySelectorAll('#battleBoard .b-elev-label').length,"
        " hazards: document.querySelectorAll('#battleBoard .b-glyph-hazard').length,"
        " crosses: document.querySelectorAll('#battleBoard .b-glyph-cross').length,"
        " objects: document.querySelectorAll('#battleBoard .b-object').length,"
        " units: document.querySelectorAll('#battleBoard .b-unit').length,"
        " activeRing: document.querySelectorAll('#battleBoard .b-active-ring').length,"
        " reach: document.querySelectorAll('#battleBoard .b-reach').length,"
        " vlinks: document.querySelectorAll('#battleBoard .b-vlink').length };")
    check("B1：画板渲染 15+6=21 格（双 surface）",
          counts and counts.get("cells") == 21, "counts=%r" % (counts,))
    check("B1：两条 surface 层带（相同 q/r 各自成层）",
          counts and counts.get("surfaces") == 2, "counts=%r" % (counts,))
    check("B1：高度差画出台阶侧壁（b-wall > 0）",
          counts and counts.get("walls", 0) > 0, "counts=%r" % (counts,))
    check("B1：每格都有高度数值标签（H0/H1/H2）",
          counts and counts.get("elevLabels") == 21, "counts=%r" % (counts,))
    check("B1：危险格有警示记号", counts and counts.get("hazards", 0) >= 1,
          "counts=%r" % (counts,))
    check("B1：阻挡格有交叉记号", counts and counts.get("crosses", 0) >= 1,
          "counts=%r" % (counts,))
    check("B1：机关 / 危险物各有徽记（2 个物件）",
          counts and counts.get("objects") == 2, "counts=%r" % (counts,))
    check("B1：四名单位令牌", counts and counts.get("units") == 4,
          "counts=%r" % (counts,))
    check("B1：当前行动单位有回合环", counts and counts.get("activeRing") == 1,
          "counts=%r" % (counts,))
    check("B1：移动可达格有覆盖标记", counts and counts.get("reach", 0) >= 1,
          "counts=%r" % (counts,))
    check("B1：跨层垂直连接有连接线", counts and counts.get("vlinks") == 1,
          "counts=%r" % (counts,))

    # 语义槽位（后续素材替换点）：terrain / unit / object 都有 data-asset-*。
    slots = page.js(
        "return {"
        " terrain: document.querySelectorAll('#battleBoard [data-asset-kind=terrain]').length,"
        " unit: document.querySelectorAll('#battleBoard [data-asset-kind=unit]').length,"
        " object: document.querySelectorAll('#battleBoard [data-asset-kind=object]').length };")
    check("B1：地形 / 单位 / 物件都留有稳定素材槽位（data-asset-*）",
          slots and slots.get("terrain") == 21 and slots.get("unit") == 4
          and slots.get("object") == 2, "slots=%r" % (slots,))

    # 选中敌体：信息面板给出距离 / 高地关系 / LOS 预览，攻击按钮出现。
    _click(page, '[data-select-unit="e-1"]')
    page.wait_for(
        "return !!document.querySelector('#battleInfo [data-info=target]');", timeout=10)
    target_info = page.js(
        "const c = document.querySelector('#battleInfo [data-info=target]');"
        "return c ? c.textContent : '';")
    check("B1：选中敌体显示距离与预览说明",
          "距离" in target_info and "预览" in target_info,
          "info=%r" % (target_info,))
    attack_on = page.js(
        "return !document.getElementById('btnBattleAttack').hidden;")
    check("B1：选中敌体后攻击按钮出现", attack_on is True, "attack=%r" % (attack_on,))

    # 选中上层敌体（e-2 已死亡）：信息面板点名掩体 / 高度差。
    _click(page, '[data-select-unit="e-2"]')
    downed = page.js(
        "const c = document.querySelector('#battleInfo [data-info=unit]');"
        "return c ? c.textContent : '';")
    check("B1：阵亡单位在信息面板标注已死亡", "已死亡" in downed, "info=%r" % (downed,))

    # 键盘可达：参战单位是 <button>（可 Tab 聚焦），画板工具钮有 aria-label。
    a11y = page.js(
        "const btns = document.querySelectorAll('#battleTargets [data-select-unit]');"
        "const zoom = ['btnBattleZoomIn','btnBattleZoomOut','btnBattleZoomReset']"
        "  .map(id => (document.getElementById(id) || {}).getAttribute('aria-label'));"
        "return { unitButtons: btns.length,"
        "         allButtons: Array.prototype.every.call(btns, b => b.tagName === 'BUTTON'),"
        "         zoomLabels: zoom };")
    check("B1：参战单位列表是键盘可聚焦的 button",
          a11y and a11y.get("allButtons") and a11y.get("unitButtons") == 4,
          "a11y=%r" % (a11y,))
    check("B1：画板缩放钮都有 aria-label",
          a11y and all(a11y.get("zoomLabels") or []), "a11y=%r" % (a11y,))

    # ── 意图提交：点可达格 → 移动按钮 → 请求带 expected_revision ──
    reach_id = page.js(
        "const r = document.querySelector('#battleBoard .b-reach');"
        "return r ? r.dataset.cellId : '';")
    check("B1：可达格带 cell_id 供点选", bool(reach_id), "reach=%r" % (reach_id,))
    if not reach_id:
        return
    page.js(_stub_move_ok(page, reach_id))
    page.js(
        "const r = document.querySelector('#battleBoard .b-reach');"
        "r.dispatchEvent(new MouseEvent('click', { bubbles: true })); return 1;")
    move_btn = page.wait_for(
        "return !document.getElementById('btnBattleMove').hidden"
        " && document.getElementById('btnBattleMove').textContent.indexOf(%s) >= 0;"
        % json.dumps(reach_id), timeout=10)
    check("B1：点可达格后移动按钮出现并点名目标格", move_btn is True,
          "btn=%r" % (page.js(
              "const b = document.getElementById('btnBattleMove');"
              "return { hidden: b.hidden, text: b.textContent };"),))
    _click(page, "#btnBattleMove")
    updated = page.wait_for(
        "return window.__BATTLE().revision === 8"
        " && window.__BATTLE().selCell === %s;" % json.dumps(reach_id), timeout=10)
    check("B1：移动后以服务器快照重画（revision 7→8、选中格更新）",
          updated is True, "snapshot=%r" % (page.js("return window.__BATTLE();"),))
    reqs = page.js("return window.__REQS || [];")
    body = (reqs or [{}])[0].get("body") or {}
    check("B1：移动意图只带 to_cell_id + expected_revision（不带结果字段）",
          body.get("to_cell_id") == reach_id and body.get("expected_revision") == 7
          and set(body.keys()) == {"to_cell_id", "expected_revision"},
          "body=%r" % (body,))
    feedback = page.js(
        "const f = document.getElementById('battleFeedback');"
        "return f && !f.hidden ? f.textContent : '';")
    check("B1：服务器叙事反馈就地展示", "移动到" in feedback, "feedback=%r" % (feedback,))

    # ── 409 stale_revision：就地提示 + 以服务器快照恢复 ──
    page.js(_stub_stale_revision(page))
    page.js(
        "const r = document.querySelector('#battleBoard .b-reach');"
        "if (r) r.dispatchEvent(new MouseEvent('click', { bubbles: true })); return 1;")
    _click(page, "#btnBattleMove")
    recovered = page.wait_for(
        "const box = document.getElementById('battleError');"
        "return !!(box && !box.hidden"
        " && box.textContent.indexOf('战场状态已变') >= 0)"
        " && window.__BATTLE().revision === 9;", timeout=10)
    check("B1：409 就地展示原因并按服务器 revision 恢复", recovered is True,
          "err=%r snap=%r" % (page.js(
              "const b = document.getElementById('battleError');"
              "return b ? b.textContent : '';"), page.js("return window.__BATTLE();")))
    page.js("window.fetch = window.__FETCH_ORIG; return 1;")

    # ── 返回叙事：旧界面不受影响 ──
    _click(page, "#btnBattleBack")
    back = page.wait_for(
        "return document.getElementById('v-play').classList.contains('is-on')"
        " && !document.getElementById('v-battle').classList.contains('is-on');",
        timeout=10)
    check("B1：返回叙事视图（v-play 点亮、战棋熄灭）", back is True,
          "views=%r" % (page.js(
              "return Array.prototype.map.call(document.querySelectorAll('.view'),"
              " v => ({ id: v.id, on: v.classList.contains('is-on') }));"),))
    play_ok = page.js(
        "return !!document.getElementById('sceneCard')"
        " && !!document.querySelector('#battleEntry');")
    check("B1：叙事界面结构完整、战棋入口仍在", play_ok is True, "play=%r" % (play_ok,))


# ── 用例二：真实 API 全链路（#426 服务端闭环）────────────────────────

def _seed_session(save_dir, sid):
    smoke._seed_action_session(save_dir, sid, [
        {"id": "a-look", "label": "查看四周", "kind": "narrate",
         "text": "四下无人。", "risk": "R0"},
    ])


def _check_real_api(page, server):
    sid = "b12-battle-real"
    _seed_session(_get_json(server.port, "/api/saves")["save_dir"], sid)
    _enter_session(page, sid, base="http://127.0.0.1:%d/" % server.port)
    in_play = page.wait_for(
        "return document.getElementById('v-play').classList.contains('is-on');",
        timeout=25)
    check("B2：会话加载后进入主画面", in_play is True,
          "diag=%r" % (page.js(
              "return { view: (typeof VIEW !== 'undefined') ? VIEW : '?',"
              " err: (document.getElementById('playError') || {}).textContent || '',"
              " sid: localStorage.getItem('prism.sid') || '' };"),))
    ready = page.wait_for(
        "return !!(window.__BATTLE"
        " && document.getElementById('battleEntry')"
        " && !document.getElementById('battleEntry').hidden);", timeout=15)
    check("B2：主画面出现战棋入口", ready is True,
          "entry=%r" % (page.js(
              "const e = document.getElementById('battleEntry');"
              "return e ? { hidden: e.hidden, text: e.textContent } : null;"),))
    _click(page, "#btnBattleEnter")
    started = page.wait_for(
        "return window.__BATTLE && window.__BATTLE().loaded"
        " && document.getElementById('v-battle').classList.contains('is-on');",
        timeout=25)
    check("B2：点入口后进入战棋（真实 API 起战）", started is True,
          "snap=%r errs=%r" % (page.js("return window.__BATTLE ? window.__BATTLE() : null;"),
                               page.js("return window.__ERRS || [];")))
    if not started:
        return
    snap = page.js("return window.__BATTLE();")
    check("B2：服务端战场落在画板上（35 格、多单位）",
          snap.get("cells") == 35 and snap.get("status") == "active"
          and snap.get("revision") >= 1, "snap=%r" % (snap,))

    counts = page.js(
        "return { objects: document.querySelectorAll('#battleBoard .b-object').length,"
        " units: document.querySelectorAll('#battleBoard .b-unit').length,"
        " activeRing: document.querySelectorAll('#battleBoard .b-active-ring').length };")
    check("B2：机关与危险物徽记都在（blast-gate + steam-vent）",
          counts and counts.get("objects") == 2, "counts=%r" % (counts,))
    check("B2：参战单位令牌 ≥ 2 且当前行动环点亮",
          counts and counts.get("units", 0) >= 2
          and counts.get("activeRing") == 1, "counts=%r" % (counts,))

    top = page.js(
        "return { round: document.getElementById('battleRound').textContent,"
        " turn: document.getElementById('battleTurn').textContent,"
        " ap: document.getElementById('battleAp').textContent };")
    check("B2：顶栏给出回合 / 行动者 / AP", all(
        (top or {}).get(k) for k in ("round", "turn", "ap")), "top=%r" % (top,))

    # 移动全链路：点一个可达格 → 移动 → 服务器接受（revision 推进、单位换格）。
    before = page.js("return window.__BATTLE();")
    reach_id = page.js(
        "const r = document.querySelector('#battleBoard .b-reach');"
        "return r ? r.dataset.cellId : '';")
    check("B2：存在可达格供移动", bool(reach_id), "reach=%r" % (reach_id,))
    if reach_id:
        page.js(
            "const r = document.querySelector('#battleBoard .b-reach');"
            "r.dispatchEvent(new MouseEvent('click', { bubbles: true })); return 1;")
        _click(page, "#btnBattleMove")
        moved = page.wait_for(
            "return window.__BATTLE().revision > %d;" % int(before["revision"]), timeout=20)
        after = page.js("return window.__BATTLE();")
        check("B2：移动被服务器接受（revision 推进）", moved is True,
              "before=%r after=%r" % (before, after))
        cell_now = page.js(
            "const sel = document.querySelector('#battleInfo [data-info=cell]');"
            "return sel ? sel.textContent : '';")
        check("B2：移动预览与服务器结果一致（单位落在所选格）",
              moved is True and reach_id in cell_now, "cell=%r" % (cell_now,))

    # 攻击：选中敌体提交（射程 / LOS 由服务器裁定；拒绝也必须就地可读）。
    enemy_btn = page.js(
        "const btns = document.querySelectorAll('#battleTargets [data-select-unit]');"
        "for (const b of btns) { if (b.textContent.indexOf('敌体') >= 0) return b.dataset.selectUnit; }"
        "return '';")
    if enemy_btn:
        rev_before = page.js("return window.__BATTLE().revision;")
        _click(page, '[data-select-unit="%s"]' % enemy_btn)
        _click(page, "#btnBattleAttack")
        settled = page.wait_for(
            "return window.__BATTLE().revision > %d"
            " || (document.getElementById('battleError')"
            " && !document.getElementById('battleError').hidden);" % int(rev_before),
            timeout=20)
        err = page.js(
            "const b = document.getElementById('battleError');"
            "return b && !b.hidden ? b.textContent : '';")
        check("B2：攻击有确定结果（revision 推进或服务器拒绝原因可读）",
              settled is True and (page.js("return window.__BATTLE().revision;") > rev_before
                                   or "范围" in err or "视线" in err or "掩体" in err
                                   or "拒绝" in err or "无法" in err),
              "err=%r rev=%r" % (err, page.js("return window.__BATTLE().revision;")))

    # 结束回合：敌方回合就地结算，之后轮到队伍单位。
    rev_before = page.js("return window.__BATTLE().revision;")
    _click(page, "#btnBattleEndTurn")
    advanced = page.wait_for(
        "return window.__BATTLE().revision > %d;" % int(rev_before), timeout=25)
    after_end = page.js("return window.__BATTLE();")
    check("B2：结束回合后回合推进（revision 推进）", advanced is True,
          "snap=%r" % (after_end,))

    # 返回叙事：旧界面不受影响。
    _click(page, "#btnBattleBack")
    back = page.wait_for(
        "return document.getElementById('v-play').classList.contains('is-on');",
        timeout=10)
    play_ok = page.js(
        "return document.querySelectorAll('#actList [data-act]').length >= 1"
        " && !document.getElementById('battleEntry').hidden;")
    check("B2：战斗中返回叙事，旧界面与战棋入口都在", back is True and play_ok is True,
          "play=%r" % (play_ok,))
    check("B2：全流程没有未捕获错误", not page.js("return window.__ERRS || [];"),
          "errs=%r" % (page.js("return window.__ERRS || [];"),))


def _main():
    mandatory = _truthy(os.environ.get(MANDATORY_ENV))
    if not os.path.isfile(smoke.STATIC_INDEX):
        if mandatory:
            print("FAIL（强制模式）：缺 static/index.html")
            return 1
        print("skip：没有 static/index.html")
        return 0
    if not _cdp.available():
        if mandatory:
            print("FAIL（强制模式）：无 WebSocket 客户端或浏览器")
            return 1
        print("skip：无 WebSocket 客户端或浏览器")
        return 0

    print("战棋 UI 测试：起服务 → CDP 断言 fixture 渲染 + 真实 API 全链路")
    with _Server(extra_env=_offline_env()) as server:
        base = "http://127.0.0.1:%d" % server.port
        try:
            page = _Page(base).__enter__()
        except _cdp.CDPUnavailable as exc:
            if mandatory:
                print("FAIL（强制模式）：%s" % exc)
                return 1
            print("skip：%s" % exc)
            return 0
        try:
            _check_fixture(page)
            _check_real_api(page, server)
        finally:
            page.__exit__(None, None, None)

    print()
    print("通过 %d / 共 %d" % (len(smoke._oks), len(smoke._oks) + len(smoke._fails)))
    return 1 if smoke._fails else 0


if __name__ == "__main__":
    sys.exit(_main())
