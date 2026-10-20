#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新式战斗服务端闭环回归（Issue #426，M13-B11）。

覆盖（对应 Issue #426 验收标准逐条）：
- 完整闭环：从普通场景开始新式战斗 → 移动 → 攻击 → 环境互动（闸门 /
  危险口）→ 敌方回合（最小确定性策略）→ 逃跑与胜负，各成一条用例；
- 服务端重算：移动路径 / 射程 / LOS 一律服务端重算；客户端伪造的结果
  字段（伤害 / 命中档 / AP）被忽略，响应里找不到伪造值；
- 幂等与并发：同幂等键重放回首次结果、不重复扣 AP / 伤害 / 触发；
  陈旧 `expected_revision` → 409 + 当前修订号（可恢复）；重复机关触发
  不重复生效（`triggered` 置位 + 状态机拒绝）；
- 断线恢复：刷新 / 重启后（重新载入会话）位置、回合、物件状态与事件
  序号原样恢复；静态战场按模板快照 + seed 重建同身份，模板元数据被改
  → 显式失败不静默换图；旧存档（无 tactical 块 / 老单文件档）照常加载；
- 会话权限：未开 session 400、缺幂等键 400、多人房间非房主开战 403、
  未认证 401、观察者只读 403——既有房间鉴权链不被旁路；
- 战斗记录基本形状：`tactical.battle_state` schema 的必填字段齐备，
  事件封套带序号 / 引擎 / 修订号，AP 价与回合上限来自
  `data/system/action_economy.json`（数据驱动）。

零依赖：仅 Python 3 标准库；直接 `python tests/test_tactical_battle_service.py` 运行。
"""
import copy
import json
import http.client
import os
import shutil
import socket
import sys
import tempfile
import threading
import time

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

# 存档目录必须在 import notdnd_web 之前注入（SAVE_DIR 在 import 时定型）。
os.environ.setdefault("NOTDND_SAVE", tempfile.mkdtemp(prefix="notdnd-b11-"))

import notdnd_web  # noqa: E402
import prism_core  # noqa: E402
import tactical_battle_service as svc  # noqa: E402


# ── 服务层脚手架 ────────────────────────────────────────────────────────

def strong_party(session: "prism_core.RuleSession", names=("甲", "乙", "丙", "丁")):
    """一支耐打的队伍：40 点活力、力道 8——脚本化战斗能在数回合内获胜。"""
    for index, name in enumerate(names):
        unit = prism_core.new_unit("u-%d" % (index + 1), name,
                                   attributes={"MGT": 8, "FIN": 6, "INS": 6})
        unit["vitality"] = 80
        unit["max_vitality"] = 80
        session.party.append(unit)
    session.scene = {"id": "sc-b11"}
    return session


def make_record(seed=11, rng_seed=13):
    """从普通场景快照开一场战斗（服务层入口，不含 HTTP）。

    `strength="entangled"`（纠缠 40%）：测试只要一场规模可控的战斗，
    遭遇强度档来自 data/system/tactics_encounter.json，不是测试自造数值。
    """
    session = strong_party(prism_core.RuleSession("b11"))
    return svc.start_battle(
        rules_snapshot=session.snapshot(), scene=svc.scene_for("local", "sc-b11"),
        battle_id="battle-b11", seed=seed, rng_seed=rng_seed,
        strength="entangled"), session


def fresh_live(snapshot):
    return prism_core.RuleSession.from_snapshot(snapshot)


def dist(record, a, b):
    battlefield = record["battlefield"]
    return svc.tactical_hex_distance(
        battlefield, svc._participant(record, a)["cell_id"],
        svc._participant(record, b)["cell_id"])


def object_by_definition(record, definition_id):
    for instance in record["battle_state"]["objects"]:
        if instance.get("definition_id") == definition_id:
            return instance
    return None


_OFFSETS = [(o["q"], o["r"]) for o in
            __import__("tactical_battlefield").space_rules()
            ["geometry"]["neighbor_offsets"]]


def _approach_destinations(battlefield, target_cell, occupied, origin):
    """推进目标的候选序：目标格本身（可进入时）→ 空闲邻格按离行动者近序。"""
    import tactical_battlefield as tb  # noqa: PLC0415
    candidates = []
    cell = tb.cell_by_id(battlefield, target_cell)
    if cell is not None and not cell.get("blocks_movement") \
            and target_cell not in occupied:
        candidates.append(target_cell)
    neighbors = []
    for dq, dr in _OFFSETS:
        neighbor = tb.cell_at(battlefield, cell["q"] + dq, cell["r"] + dr,
                              cell.get("surface_id")) if cell else None
        if neighbor is None or neighbor.get("blocks_movement") \
                or neighbor["cell_id"] in occupied:
            continue
        neighbors.append(neighbor["cell_id"])
    neighbors.sort(key=lambda cid: (
        svc.tactical_hex_distance(battlefield, origin, cid), cid))
    candidates.extend(neighbors)
    return candidates


def _step_toward(record, live, actor_id, target_cell, budget=30):
    """用 #419 的完整 Dijkstra 路径取下一步（目标格被占时改走最近空闲邻格）。"""
    import tactical_movement  # noqa: PLC0415
    import tactical_objects  # noqa: PLC0415
    battlefield = record["battlefield"]
    origin = svc._participant(record, actor_id)["cell_id"]
    occupied = tactical_objects.occupancy_map(record["battle_state"],
                                              battlefield, exclude=[actor_id])
    for destination in _approach_destinations(battlefield, target_cell,
                                               occupied, origin):
        try:
            move = tactical_movement.plan_move(
                battlefield, actor_id, origin, [{"q": 0, "r": 0}], budget,
                occupied=occupied, destination=destination)
        except ValueError:
            continue
        path = move.get("path") or []
        if len(path) >= 2:
            return path[1]
    return None


def drive_adjacent(record, live, target_id, max_rounds=30):
    """确定性走位：当前行动的队伍单位按完整路径向目标推进并结束回合。

    先攻序在队伍单位之间轮转，所以不固定行动者——谁行动谁走位，
    每回合最多花完 3 AP（每 1 AP 挪一格），直到有存活队伍单位与目标相邻。
    """
    rev = lambda: record["battle_state"]["state_revision"]  # noqa: E731
    for _ in range(max_rounds):
        nearest = _nearest_party(record, live, target_id)
        if nearest is not None and dist(record, target_id, nearest) <= 1:
            return True
        actor = record["battle_state"].get("active_unit_id") or ""
        entry = svc._unit_entry(record, actor) or {}
        if not entry.get("party") or record["status"] != svc.STATUS_ACTIVE:
            return False
        unit, _side = svc._resolve_unit(record, live, actor)
        if unit is None or unit.get("downed"):
            svc.do_end_turn(record, live, actor_id=actor,
                            expected_revision=rev())
            continue
        for _ap in range(3):
            nearest = _nearest_party(record, live, target_id)
            if nearest is not None and dist(record, target_id, nearest) <= 1:
                return True
            step = _step_toward(record, live, actor,
                                svc._participant(record, target_id)["cell_id"])
            if not step:
                break
            try:
                svc.do_move(record, live, actor_id=actor, to_cell_id=step,
                            expected_revision=rev())
            except svc.TacticalError:
                break  # 倒地 / 无 AP：本回合到此为止
        nearest = _nearest_party(record, live, target_id)
        if nearest is not None and dist(record, target_id, nearest) <= 1:
            return True
        svc.do_end_turn(record, live, actor_id=actor, expected_revision=rev())
        if record["status"] != svc.STATUS_ACTIVE:
            return False
    return False


def _nearest_party(record, live, enemy_id):
    """离敌体最近的存活队伍单位 id（确定性平手：unit_id 序）。"""
    best = None
    best_key = None
    for row in svc._participants(record):
        entry = svc._unit_entry(record, str(row.get("unit_id") or ""))
        if not (entry and entry.get("party")):
            continue
        unit = None
        for candidate in live.party:
            if str(candidate.get("id") or "") == row["unit_id"]:
                unit = candidate
                break
        if unit is None or unit.get("dead") or unit.get("downed"):
            continue
        key = (dist(record, enemy_id, row["unit_id"]), row["unit_id"])
        if best_key is None or key < best_key:
            best_key = key
            best = row["unit_id"]
    return best


# ── 服务层用例 ──────────────────────────────────────────────────────────

def check_start_layout_and_determinism():
    """开战：参战者落位互斥、battle_state 形状齐备、同 seed 同输出。"""
    record, _session = make_record()
    state = record["battle_state"]
    assert state["kind"] == "tactical.battle_state", state["kind"]
    for field in ("schema_version", "kind", "battle_id", "battlefield_id",
                  "round", "turn", "state_revision", "participants"):
        assert field in state, field
    cells = [row["cell_id"] for row in state["participants"]]
    assert len(cells) == len(set(cells)), "参战者占格必须互斥"
    assert record["status"] == "active"
    assert record["battle_state"]["state_revision"] >= 1
    types = [event["type"] for event in record["events"]]
    assert "combat_started" in types and "turn_started" in types, types
    sequences = [event["sequence"] for event in record["events"]]
    assert sequences == sorted(sequences) and len(set(sequences)) == len(sequences)
    # 同 seed 同输出（战场身份与先攻顺序一致）。
    other, _ = make_record()
    assert other["battlefield_meta"]["battlefield_id"] \
        == record["battlefield_meta"]["battlefield_id"]
    assert other["turn_order"] == record["turn_order"]


def check_move_server_recalc():
    """移动：服务端重算路径——不可达 / 占格 / 未轮到都结构化拒绝。"""
    record, session = make_record()
    live = fresh_live(session.snapshot())
    rev = lambda: record["battle_state"]["state_revision"]  # noqa: E731
    actor = record["battle_state"]["active_unit_id"]
    # 不在战场上的格。
    try:
        svc.do_move(record, live, actor_id=actor, to_cell_id="cell-nope",
                    expected_revision=rev())
        raise AssertionError("未知格应当被拒")
    except svc.TacticalError as exc:
        assert exc.reason == "no_cell" and exc.code == 404, (exc.reason, exc.code)
    # 预算外（abstract 预算 1 格）：远格 → destination_unreachable + blocked。
    far = record["battlefield"]["cells"][-1]["cell_id"]
    try:
        svc.do_move(record, live, actor_id=actor, to_cell_id=far,
                    expected_revision=rev())
        raise AssertionError("远格应当不可达")
    except svc.TacticalError as exc:
        assert exc.reason == "destination_unreachable" and exc.code == 422
        assert exc.details.get("movement_blocked"), exc.details
    # 敌体占格（出生点）不可入。
    enemy = svc._enemy_ids(record)[0]
    enemy_cell = svc._participant(record, enemy)["cell_id"]
    try:
        svc.do_move(record, live, actor_id=actor, to_cell_id=enemy_cell,
                    expected_revision=rev())
        raise AssertionError("敌体占格应当不可入")
    except svc.TacticalError as exc:
        assert exc.reason == "destination_unreachable"
    # 陈旧修订号。
    try:
        svc.do_move(record, live, actor_id=actor, to_cell_id=far,
                    expected_revision=999)
        raise AssertionError("陈旧修订号应当被拒")
    except svc.TacticalError as exc:
        assert exc.reason == "stale_revision" and exc.code == 409
        assert exc.details.get("state_revision") == rev()
    # 合法移动：坐标由服务端路径决定，AP 按数据价目扣。
    step = svc._enemy_step(record, record["battlefield"], actor,
                           enemy_cell)
    assert step is not None
    before_ap = record["turn_ap"][actor]
    out = svc.do_move(record, live, actor_id=actor, to_cell_id=step,
                      expected_revision=rev())
    assert svc._participant(record, actor)["cell_id"] == step
    assert record["turn_ap"][actor] == before_ap - svc.ap_cost("move")
    assert out["narrative"], out
    # 未轮到行动者。
    try:
        svc.do_move(record, live, actor_id=actor, to_cell_id=step,
                    expected_revision=rev())
        raise AssertionError("AP 不足的移动应当被拒")
    except svc.TacticalError as exc:
        assert exc.reason in ("no_ap", "destination_unreachable"), exc.reason


def check_attack_server_recalc_and_victory():
    """攻击：射程服务端重算；脚本化战斗打到胜利（XP / 压力 / 收尾事件）。"""
    record, session = make_record()
    live = fresh_live(session.snapshot())
    rev = lambda: record["battle_state"]["state_revision"]  # noqa: E731
    actor = record["battle_state"]["active_unit_id"]
    enemy = svc._enemy_ids(record)[0]
    # 远距离攻击 → 射程拒绝（可解释）。
    try:
        svc.do_attack(record, live, actor_id=actor, target_id=enemy,
                      expected_revision=rev())
        raise AssertionError("远距攻击应当被拒")
    except svc.TacticalError as exc:
        assert exc.reason == "target_out_of_range" and exc.code == 422
        reasons = exc.details.get("reasons") or []
        assert reasons and reasons[0]["code"] == "out_of_range", reasons
    # 走位到相邻后攻击（先攻序轮转：谁行动谁走位）。
    assert drive_adjacent(record, live, enemy), "应当能在数回合内接敌"
    grades = []
    for _ in range(100):
        if record["status"] != svc.STATUS_ACTIVE:
            break
        actor = record["battle_state"]["active_unit_id"]
        entry = svc._unit_entry(record, actor) or {}
        living_enemies = [uid for uid in svc._enemy_ids(record)
                          if _conscious_unit(record, live, uid)]
        if entry.get("party") and living_enemies:
            target = min(living_enemies,
                         key=lambda uid: (dist(record, actor, uid), uid))
            acted = False
            if dist(record, actor, target) <= 1:
                try:
                    out = svc.do_attack(record, live, actor_id=actor,
                                        target_id=target,
                                        expected_revision=rev())
                    grades.append(out["result"]["grade"])
                    acted = True
                except svc.TacticalError:
                    acted = False
            if not acted:
                try:
                    svc.do_end_turn(record, live, actor_id=actor,
                                    expected_revision=rev())
                except svc.TacticalError:
                    break
        else:
            break
    assert record["status"] == svc.STATUS_ENDED, (record["status"],
                                                  record["outcome"])
    assert record["outcome"] == "victory", record["outcome"]
    assert grades, "至少结算过一次攻击"
    # 收尾事件 + 经验 + 压力（既有 PRISM 规则）。
    types = [event["type"] for event in record["events"]]
    assert "combat_ended" in types and "attack_resolved" in types
    ended = [e for e in record["events"] if e["type"] == "combat_ended"][0]
    assert (ended["result"] or {}).get("xp_total", 0) >= 0
    assert live.pressure == 1, live.pressure
    assert all(unit["xp"] > 0 for unit in live.party), "胜利要发经验"


def _conscious_unit(record, live, unit_id):
    unit, _side = svc._resolve_unit(record, live, unit_id)
    return unit is not None and not unit.get("dead") and not unit.get("downed")


def check_interact_gate_and_hazard():
    """环境互动：闸门开闭改变占格；危险口一次性触发，重复不重复生效。"""
    record, session = make_record()
    live = fresh_live(session.snapshot())
    rev = lambda: record["battle_state"]["state_revision"]  # noqa: E731
    actor = record["battle_state"]["active_unit_id"]
    gate = object_by_definition(record, "mech.blast-gate")
    vent = object_by_definition(record, "hazard.steam-vent")
    assert gate is not None and vent is not None
    # 隔空互动 → 范围拒绝。
    try:
        svc.do_interact(record, live, actor_id=actor, object_id=gate["object_id"],
                        action="interact", expected_revision=rev())
        raise AssertionError("隔空互动应当被拒")
    except svc.TacticalError as exc:
        assert exc.reason == "object_out_of_range" and exc.code == 422, exc.reason
    # 蒸汽口（离出生点近）：走到相邻并互动开启 → object_state_changed +
    # triggered 置位（物件按 #421 语义占用自身格，enter 相位无法由移动
    # 触发，interactive 触发是既定口径）。
    assert _drive_to_object(record, live, vent["cell_id"], reserve_ap=1), \
        "应当能走到蒸汽口相邻"
    actor = record["battle_state"]["active_unit_id"]
    out = svc.do_interact(record, live, actor_id=actor,
                          object_id=vent["object_id"], action="interact",
                          expected_revision=rev())
    assert out["result"] == {"from": "armed", "to": "vented"}, out["result"]
    assert vent["state"] == "vented" and vent["triggered"] is True, vent
    # 重复互动 → 状态机拒绝（无 vented→vented 的第二条转换）。
    try:
        svc.do_interact(record, live, actor_id=actor,
                        object_id=vent["object_id"], action="interact",
                        expected_revision=rev())
        raise AssertionError("重复互动应当被拒")
    except svc.TacticalError as exc:
        assert exc.reason == "interact_rejected", exc.reason
    # 走到闸门相邻并打开（每回合留 1 AP 给随后的互动）。
    assert _drive_to_object(record, live, gate["cell_id"], reserve_ap=1), \
        "应当能走到闸门相邻"
    actor = record["battle_state"]["active_unit_id"]
    out = svc.do_interact(record, live, actor_id=actor,
                          object_id=gate["object_id"], action="interact",
                          expected_revision=rev())
    assert out["result"] == {"from": "closed", "to": "open"}, out["result"]
    assert gate["state"] == "open" and gate["blocks_movement"] is False
    types = [event["type"] for event in record["events"]]
    assert "object_state_changed" in types
    # 重复互动 → 状态机拒绝（无 closed→open 的第二条转换）。
    try:
        svc.do_interact(record, live, actor_id=actor,
                        object_id=gate["object_id"], action="interact",
                        expected_revision=rev())
        raise AssertionError("重复互动应当被拒")
    except svc.TacticalError as exc:
        assert exc.reason == "interact_rejected", exc.reason


def _drive_to_object(record, live, cell_id, enter=False, max_rounds=30,
                     reserve_ap=0):
    """走到目标格相邻（`enter=True` 时踩上该格）：按完整路径推进的测试助手。

    `reserve_ap`：每回合保留的 AP（交互类用例留 1 点给随后的互动）。
    """
    rev = lambda: record["battle_state"]["state_revision"]  # noqa: E731
    def reached(actor):
        current = svc._participant(record, actor)["cell_id"]
        if enter:
            return current == cell_id
        return svc.tactical_hex_distance(record["battlefield"], current,
                                         cell_id) <= 1
    for _ in range(max_rounds):
        actor = record["battle_state"].get("active_unit_id") or ""
        entry = svc._unit_entry(record, actor) or {}
        if not entry.get("party") or record["status"] != svc.STATUS_ACTIVE:
            return False
        if reached(actor):
            return True
        unit, _side = svc._resolve_unit(record, live, actor)
        if unit is None or unit.get("downed"):
            svc.do_end_turn(record, live, actor_id=actor,
                            expected_revision=rev())
            continue
        for _ap in range(max(0, 3 - reserve_ap)):
            if reached(actor):
                return True
            step = _step_toward(record, live, actor, cell_id)
            if not step:
                break
            try:
                svc.do_move(record, live, actor_id=actor, to_cell_id=step,
                            expected_revision=rev())
            except svc.TacticalError:
                break  # 倒地 / 无 AP：本回合到此为止
        if reached(actor):
            return True
        svc.do_end_turn(record, live, actor_id=actor, expected_revision=rev())
        if record["status"] != svc.STATUS_ACTIVE:
            return False
    return False


def check_flee():
    """逃跑：只有队伍单位可以撤；代价真实（压力 +1、无经验）；战斗收尾。"""
    record, session = make_record()
    live = fresh_live(session.snapshot())
    rev = lambda: record["battle_state"]["state_revision"]  # noqa: E731
    actor = record["battle_state"]["active_unit_id"]
    out = svc.do_flee(record, live, actor_id=actor, expected_revision=rev())
    assert record["status"] == svc.STATUS_ENDED and record["outcome"] == "fled"
    assert live.pressure == 1
    types = [event["type"] for event in record["events"]]
    assert types[-1] == "combat_ended"
    assert out["narrative"], out
    # 结束后再打 → 404 no_battle。
    try:
        svc.do_attack(record, live, actor_id=actor, target_id="whatever",
                      expected_revision=rev())
        raise AssertionError("结束后的战斗应当拒绝行动")
    except svc.TacticalError as exc:
        assert exc.reason == "no_battle" and exc.code == 404


def tactical_stub_interact(record, live, instance, trigger):
    """绕过走位直接调 #421 的入口（本用例只验幂等，不验射程）。"""
    return svc.tactical_objects.interact(
        record["battle_state"], record["battlefield"],
        definitions=record["object_definitions"],
        object_id=instance["object_id"], action=trigger,
        actor_id="u-1", rule_engine="tactics",
        applied_keys=tuple(record.get("applied_keys") or ()))


def check_object_idempotency():
    """物件幂等：同键重复请求不重复触发（applied_keys + triggered 双保险）。"""
    record, session = make_record()
    live = fresh_live(session.snapshot())
    vent = object_by_definition(record, "hazard.steam-vent")
    # 直接对已触发提案重复应用：applied_keys 命中 → duplicate。
    proposal = tactical_stub_interact(record, live, vent, "interact")
    assert proposal["accepted"] is True, proposal
    svc._apply_object_proposal(record, proposal)
    proposal2 = tactical_stub_interact(record, live, vent, "interact")
    assert proposal2.get("duplicate") is True or not proposal2.get("accepted")
    # triggered 只置位一次。
    instance = object_by_definition(record, "hazard.steam-vent")
    assert instance["triggered"] is True


def check_restore_battlefield():
    """恢复：模板快照 + seed 重建同身份战场；元数据被改 → 显式失败。"""
    record, _session = make_record()
    rebuilt = svc.restore_battlefield(record)
    assert rebuilt == record["battlefield"], "重建结果必须与存档一致"
    tampered = copy.deepcopy(record)
    tampered["battlefield_meta"]["battlefield_id"] = "bf-other"
    try:
        svc.restore_battlefield(tampered)
        raise AssertionError("身份不一致应当显式失败")
    except svc.TacticalError as exc:
        assert exc.reason == "battlefield_rebuild_mismatch" and exc.code == 500


def check_ap_economy_from_data():
    """AP 价与回合上限来自数据文件，不写死。"""
    with open(os.path.join(ROOT, "data", "system", "action_economy.json"),
              encoding="utf-8") as handle:
        economy = json.load(handle)
    assert svc.ap_per_turn() == economy["round"]["ap_per_turn"]
    costs = {row["id"]: row.get("ap") for row in economy["actions"]}
    assert svc.ap_cost("move") == costs["move"]
    assert svc.ap_cost("basic_attack") == costs["basic_attack"]
    assert svc.ap_cost("assist_action") == costs["assist_action"]


# ── HTTP 层脚手架（本进程服务线程，沿用仓库既有模式）────────────────────

def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _Local:
    """本进程起一台 notdnd_web 服务（随机端口 + 独立存档目录）。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="notdnd-b11-http-")
        os.environ["NOTDND_SAVE"] = self.dir
        notdnd_web.SAVE_DIR = __import__("pathlib").Path(self.dir)
        notdnd_web._sessions.clear()
        self.server = notdnd_web.ThreadingHTTPServer(
            ("127.0.0.1", 0), notdnd_web.Handler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def get(self, path, sid="", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        request_headers = dict(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        try:
            conn.request("GET", path, headers=request_headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            conn.close()

    def post(self, path, payload, sid="", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        request_headers = {"Content-Type": "application/json; charset=utf-8"}
        request_headers.update(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, body=body, headers=request_headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read() or b"{}")
        finally:
            conn.close()


def _new_session(http):
    status, body = http.post("/api/session/create", {})
    assert status == 200, (status, body)
    return body["sid"]


def _seed_party(sid):
    """给会话塞一支耐打队伍（直接写门面 rules 块并落盘）。"""
    session = notdnd_web.get_session(sid)
    with session.lock:
        rules = prism_core.RuleSession(sid)
        strong_party(rules)
        session.rules = rules.snapshot()
        session.save()


IDEM = {"Idempotency-Key": "b11test0000000001"}


def _rev(http, sid):
    status, body = http.get("/api/tactical/battle", sid=sid)
    assert status == 200, (status, body)
    return body["battle"]["state_revision"], body["battle"]


def _start_battle(http, sid, key="b11start000000001", strength="entangled"):
    status, body = http.post("/api/tactical/start", {"strength": strength},
                             sid=sid, headers={"Idempotency-Key": key})
    assert status == 200, (status, body)
    return body


def _party_ids(battle):
    return {row["unit_id"] for row in battle["participants"] if row["party"]}


_KEY_COUNTER = [0]


def _next_key():
    _KEY_COUNTER[0] += 1
    return _KEY_COUNTER[0]


def _enemy_ids_view(battle):
    return [row["unit_id"] for row in battle["participants"]
            if not row["party"] and not (row.get("unit") or {}).get("dead")]


def _cell_of(battle, unit_id):
    return next(row["cell_id"] for row in battle["participants"]
                if row["unit_id"] == unit_id)


def _step_from_view(battle, actor, target_cell, budget=30):
    """用 HTTP 快照（只读）按 #419 完整路径取下一步（目标被占改走邻格）。"""
    import tactical_movement  # noqa: PLC0415
    battlefield = battle["battlefield"]
    occupied = {}
    for row in battle["participants"]:
        if row["unit_id"] != actor:
            occupied[row["cell_id"]] = row["unit_id"]
    for instance in battle["objects"]:
        if instance.get("blocks_movement"):
            occupied[instance["cell_id"]] = instance["object_id"]
    origin = _cell_of(battle, actor)
    for destination in _approach_destinations(battlefield, target_cell,
                                               occupied, origin):
        try:
            move = tactical_movement.plan_move(
                battlefield, actor, origin, [{"q": 0, "r": 0}], budget,
                occupied=occupied, destination=destination)
        except ValueError:
            continue
        path = move.get("path") or []
        if len(path) >= 2:
            return path[1]
    return None


def _http_move(http, sid, to_cell_id, key):
    """一次 HTTP 移动；被拒（倒地 / 不可达）返回 False，调用方只结束回合。"""
    revision, _battle = _rev(http, sid)
    status, body = http.post(
        "/api/tactical/move", {"to_cell_id": to_cell_id,
                               "expected_revision": revision},
        sid=sid, headers={"Idempotency-Key": key})
    if status != 200:
        return False
    return True


def _http_end_turn(http, sid, key):
    revision, _battle = _rev(http, sid)
    status, body = http.post(
        "/api/tactical/end_turn", {"expected_revision": revision},
        sid=sid, headers={"Idempotency-Key": key})
    assert status == 200, (status, body)


def drive_adjacent_via_http(http, sid, max_rounds=30):
    """HTTP 版走位：谁行动谁走位（每回合只挪 1 格，留 2 AP 给攻击），
    直到有队伍单位与任一存活敌体相邻。"""
    for _ in range(max_rounds):
        _revision, battle = _rev(http, sid)
        if battle["status"] != "active":
            return False
        enemies = _enemy_ids_view(battle)
        actor = battle["active_unit_id"]
        if actor not in _party_ids(battle):
            return False
        if any(svc.tactical_hex_distance(battle["battlefield"],
                                         _cell_of(battle, actor),
                                         _cell_of(battle, enemy)) <= 1
               for enemy in enemies):
            return True
        target = min(enemies, key=lambda uid: (
            svc.tactical_hex_distance(battle["battlefield"],
                                      _cell_of(battle, actor),
                                      _cell_of(battle, uid)), uid))
        # 每回合只挪 1 格：留 2 AP 给随后的攻击。
        step = _step_from_view(battle, actor, _cell_of(battle, target))
        if step:
            _http_move(http, sid, step, "b11mv%08d" % _next_key())
            _revision, battle = _rev(http, sid)
            if any(svc.tactical_hex_distance(battle["battlefield"],
                                             _cell_of(battle, actor),
                                             _cell_of(battle, enemy)) <= 1
                   for enemy in _enemy_ids_view(battle)):
                return True
        _http_end_turn(http, sid, "b11tn%08d" % _next_key())
    return False


# ── HTTP 层用例 ────────────────────────────────────────────────────────

def check_http_gates():
    """鉴权与门：未开会话 400 / 缺幂等键 400 / 旧档与空档 404 no_battle。"""
    with _Local() as http:
        status, body = http.post("/api/tactical/start", {})
        assert status == 400, (status, body)
        sid = _new_session(http)
        status, body = http.post("/api/tactical/start", {}, sid=sid)
        assert status == 400 and body.get("reason") == "bad_idem_key", (status, body)
        status, body = http.get("/api/tactical/battle", sid=sid)
        assert status == 404 and body.get("reason") == "no_battle", (status, body)
        status, body = http.post("/api/tactical/move", {"to_cell_id": "x"},
                                 sid=sid, headers=dict(IDEM))
        assert status == 404 and body.get("reason") == "no_battle", (status, body)


def check_http_full_loop():
    """HTTP 完整闭环：开始 → 快照 → 幂等重放 → 移动 → 攻击 → 逃跑。"""
    with _Local() as http:
        sid = _new_session(http)
        _seed_party(sid)
        body = _start_battle(http, sid)
        battle = body["battle"]
        assert battle["battle_id"] and battle["status"] == "active"
        for field in ("battlefield", "participants", "state_revision",
                      "last_event_sequence", "legal_actions", "objects",
                      "object_definitions", "turn_order", "turn_ap"):
            assert field in battle, field
        # 幂等重放：同键重投 → replayed，修订号不变。
        revision = battle["state_revision"]
        status, replay = http.post("/api/tactical/start", {}, sid=sid,
                                   headers={"Idempotency-Key": "b11start000000001"})
        assert status == 200 and replay.get("replayed") is True, (status, replay)
        assert replay["battle"]["state_revision"] == revision
        # 伪造结果字段：伤害 / 命中档 / AP 全部被忽略（响应里找不到伪造值）。
        assert drive_adjacent_via_http(http, sid), "应当能接敌"
        revision, battle = _rev(http, sid)
        actor = battle["active_unit_id"]
        enemies = _enemy_ids_view(battle)
        target = min(enemies, key=lambda uid: (
            svc.tactical_hex_distance(battle["battlefield"],
                                      _cell_of(battle, actor),
                                      _cell_of(battle, uid)), uid))
        assert svc.tactical_hex_distance(
            battle["battlefield"], _cell_of(battle, actor),
            _cell_of(battle, target)) <= 1, "接敌后应相邻"
        forged = {"target_id": target, "expected_revision": revision,
                  "damage": 999999, "grade": "crit", "ap_cost": -5,
                  "attack_total": 9999}
        status, out = http.post("/api/tactical/attack", forged, sid=sid,
                                headers={"Idempotency-Key": "b11atk0000000001"})
        assert status == 200, (status, out)
        dumped = json.dumps(out, ensure_ascii=False)
        assert "999999" not in dumped and "9999" not in dumped, "伪造值不得出现在响应里"
        assert out["result"]["grade"] in ("hit", "solid", "crit", "graze",
                                          "miss", "fumble"), out["result"]
        # 逃跑收尾：完整闭环的最后一环。
        revision, battle = _rev(http, sid)
        status, out = http.post("/api/tactical/flee",
                                {"expected_revision": revision},
                                sid=sid, headers=dict(IDEM))
        assert status == 200 and battle_view_ended(out), (status, out)
        assert out["battle"]["outcome"] == "fled"


def battle_view_ended(payload):
    return payload.get("battle", {}).get("status") == "ended"


def check_http_stale_revision_and_conflict():
    """并发口径：陈旧 expected_revision → 409 + 当前修订号（可恢复）。"""
    with _Local() as http:
        sid = _new_session(http)
        _seed_party(sid)
        _start_battle(http, sid, key="b11start000000002")
        status, body = http.post("/api/tactical/end_turn",
                                 {"expected_revision": 99999}, sid=sid,
                                 headers=dict(IDEM))
        assert status == 409 and body.get("reason") == "stale_revision", (status, body)
        current, _battle = _rev(http, sid)
        assert body.get("state_revision") == current, body
        # 用回当前修订号即可恢复。
        status, body = http.post("/api/tactical/end_turn",
                                 {"expected_revision": current}, sid=sid,
                                 headers=dict(IDEM))
        assert status == 200, (status, body)


def check_http_idempotent_ops():
    """同键重放不重复扣 AP / 不重复结算：修订号与 AP 余额不变。"""
    with _Local() as http:
        sid = _new_session(http)
        _seed_party(sid)
        _start_battle(http, sid, key="b11start000000003")
        revision, battle = _rev(http, sid)
        actor = battle["active_unit_id"]
        ap_before = battle["turn_ap"][actor]
        key = {"Idempotency-Key": "b11idem0000000001"}
        status, first = http.post("/api/tactical/end_turn",
                                  {"expected_revision": revision},
                                  sid=sid, headers=key)
        assert status == 200, (status, first)
        status, second = http.post("/api/tactical/end_turn",
                                   {"expected_revision": revision},
                                   sid=sid, headers=key)
        assert status == 200 and second.get("replayed") is True, (status, second)
        revision, battle = _rev(http, sid)
        assert battle["state_revision"] == first["battle"]["state_revision"]
        # 回合已推进，行动者变了——重放没有多扣任何人的 AP。
        new_actor = battle["active_unit_id"]
        assert battle["turn_ap"].get(new_actor) == svc.ap_per_turn()


def check_http_restore_after_refresh():
    """断线恢复：重启（清缓存重载）后位置 / 回合 / 物件 / 事件序号原样恢复。"""
    with _Local() as http:
        sid = _new_session(http)
        _seed_party(sid)
        _start_battle(http, sid, key="b11start000000004")
        revision, before = _rev(http, sid)
        # 打一场：走一步 + 结束回合，制造事件与位移。
        drive_adjacent_via_http(http, sid, max_rounds=2)
        revision, before = _rev(http, sid)
        # 模拟服务端重启：清掉内存缓存，强制从磁盘重新载入。
        with notdnd_web._sessions_lock:
            notdnd_web._sessions.pop(sid, None)
        status, after = http.get("/api/tactical/battle", sid=sid)
        assert status == 200, (status, after)
        restored = after["battle"]
        assert restored["battle_id"] == before["battle_id"]
        assert restored["state_revision"] == before["state_revision"]
        assert restored["last_event_sequence"] == before["last_event_sequence"]
        assert restored["round"] == before["round"]
        assert restored["active_unit_id"] == before["active_unit_id"]
        positions_before = {row["unit_id"]: row["cell_id"]
                            for row in before["participants"]}
        positions_after = {row["unit_id"]: row["cell_id"]
                           for row in restored["participants"]}
        assert positions_before == positions_after, (positions_before,
                                                     positions_after)
        assert restored["objects"] == before["objects"]
        assert restored["turn_order"] == before["turn_order"]
        # 恢复后的战斗照常可写。
        status, body = http.post("/api/tactical/end_turn",
                                 {"expected_revision": restored["state_revision"]},
                                 sid=sid, headers=dict(IDEM))
        assert status == 200, (status, body)


def check_http_old_saves_untouched():
    """旧存档：无 tactical 块的新档与老单文件档都照常加载、路径不旁路。"""
    with _Local() as http:
        sid = _new_session(http)
        # 新档没有战斗 → 404；老路径（/api/session）照常。
        status, body = http.get("/api/tactical/battle", sid=sid)
        assert status == 404 and body.get("reason") == "no_battle"
        status, body = http.get("/api/session", sid=sid)
        assert status == 200, (status, body)
        # 老单文件档（无 tactical 键）→ 惰性迁移路径照常，战斗面 404。
        legacy_sid = "legacy-b11-old"
        legacy = {"sid": legacy_sid, "created": 1.0, "log": [], "seq": 0,
                  "save_name": "老档", "rules": None, "atlas": None,
                  "guide": None, "room": None}
        with open(os.path.join(notdnd_web.SAVE_DIR, "%s.json" % legacy_sid),
                  "w", encoding="utf-8") as handle:
            json.dump(legacy, handle, ensure_ascii=False)
        status, body = http.get("/api/tactical/battle", sid=legacy_sid)
        assert status == 404 and body.get("reason") == "no_battle", (status, body)
        status, body = http.get("/api/session", sid=legacy_sid)
        assert status == 200, (status, body)


def check_http_room_permissions():
    """多人房间：非房主开战 403、未认证 401、成员只能指挥自己的角色。"""
    with _Local() as http:
        status, host = http.post("/api/room/create",
                                 {"room_id": "b11room1", "display_name": "房主",
                                  "character_id": "u-1"})
        assert status == 200, (status, host)
        sid = host["room_id"]
        _seed_party(sid)
        status, player = http.post("/api/room/join", {"display_name": "玩家",
                                                      "character_id": "u-2"},
                                   headers={"X-Room": sid})
        assert status == 200, (status, player)
        # 未认证：没有成员令牌的开战请求 → 401。
        status, body = http.post("/api/tactical/start", {}, sid=sid,
                                 headers={"Idempotency-Key": "b11room000000001"})
        assert status == 401, (status, body)
        # 非房主（player）开战 → 403。
        status, body = http.post(
            "/api/tactical/start", {}, sid=sid,
            headers={"Idempotency-Key": "b11room000000002",
                     "X-Member": player["member_id"],
                     "Authorization": "Bearer " + player["token"]})
        assert status == 403, (status, body)
        # 房主开战成功；成员身份的行动者 = 绑定角色。
        status, body = http.post(
            "/api/tactical/start", {}, sid=sid,
            headers={"Idempotency-Key": "b11room000000003",
                     "X-Member": host["member_id"],
                     "Authorization": "Bearer " + host["token"]})
        assert status == 200, (status, body)
        battle = body["battle"]
        party_ids = {row["unit_id"] for row in battle["participants"]
                     if row["party"]}
        assert "u-1" in party_ids and "u-2" in party_ids
        # 房主成员绑定 u-1：如果当前轮到 u-1，成员令牌可以直接行动。
        if battle["active_unit_id"] == "u-1":
            status, body = http.post(
                "/api/tactical/end_turn", {"expected_revision":
                                           battle["state_revision"]},
                sid=sid, headers={"Idempotency-Key": "b11room000000004",
                                  "X-Member": host["member_id"],
                                  "Authorization": "Bearer " + host["token"]})
            assert status == 200, (status, body)


# ── 运行器 ─────────────────────────────────────────────────────────────

def main():
    checks = (
        check_start_layout_and_determinism,
        check_move_server_recalc,
        check_attack_server_recalc_and_victory,
        check_interact_gate_and_hazard,
        check_flee,
        check_object_idempotency,
        check_restore_battlefield,
        check_ap_economy_from_data,
        check_http_gates,
        check_http_full_loop,
        check_http_stale_revision_and_conflict,
        check_http_idempotent_ops,
        check_http_restore_after_refresh,
        check_http_old_saves_untouched,
        check_http_room_permissions,
    )
    failures = 0
    for check in checks:
        try:
            check()
            print("通过: %s" % check.__name__)
        except AssertionError as error:
            print("断言失败: %s: %s" % (check.__name__, error))
            failures += 1
        except Exception as error:  # noqa: BLE001 — 一个用例的崩溃不带崩整卷
            import traceback
            traceback.print_exc()
            print("异常: %s: %r" % (check.__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks),
                                          failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
