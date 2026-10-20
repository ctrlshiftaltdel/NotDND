#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""六角格战斗 MVP 端到端验收（M13-B14，Issue #429）。

证明六角格战斗从生成、展示、移动、攻击、环境互动到持久化 / 终局是
**完整系统**，并证明原有两套规则引擎（PRISM d20 战术 / 构建骰池）没有
被简化或绕过——全部结算走真实模块管线，不以 mock 取代后端 / 引擎
（确定性 RNG 注入是核心预留的测试缝，不属于 mock）。

覆盖（对应 Issue #429 验收标准逐条）：

- **三种战场 fixture**（tests/fixtures/hex_e2e/）：室内多层（楼梯 / 门 /
  掩体）、户外危险地形、现代 / 未来对象配置——主题差异只由模板数据
  表达；同一模板换 world_id 产出完全相同的格位（代码不按世界名分支）；
- **端到端主流程**（HTTP）：从战役地点开战 → 合法 / 非法移动 → 攻击 /
  射程与 LOS → 敌方回合 → 逃跑收尾 → 断线重连恢复 → 旧档惰性迁移；
- **规则完整性**：d20 命中六档 / 自然 1 / 伤害标签（抗性 / 弱点）/
  韧性破韧 / 暴击附加状态 / 首领阶段；构建引擎骰池 / 阈值修正 / 动量 /
  应力 / 超载连锁；AP 经济、遭遇预算、逃跑与胜负——两引擎分别经
  #424 / #425 适配器完成**各自的**真实结算；
- **确定性 replay**：同 seed / rng_seed 两次开战记录逐字节一致，
  相同操作序列产出相同事件流；
- **客户端安全边界**：伪造位置 / 骰值 / 命中档 / 伤害 / 资源 / 对象
  状态 / 越权单位操作都不得成为事实（服务端重算一切）；
- **可追踪性**：空间查询与规则映射输出带 rule_ref / reasons[]；
  unsupported / not_mapped 显式可见；metric 缺 sight 结构化失败、
  abstract 可用（#453 登记口径）。

已知口径（登记，不重复验证）：movement_destination 显式参数；blocked
不掷骰；hazard enter 相位不可达（interact 相位结算，#415 登记）；
ranged_reach=相邻格（02C:144）；默认模板 service-default-room 单
surface 全 H0（#453 已登记）。桌面 / 移动 Web 主流程的 UI 证明由
tests/test_ui_battle.py（#453 真实浏览器测试）承担。

零依赖：仅 Python 3 标准库；直接 `python tests/test_hex_e2e_acceptance.py`。
"""
import copy
import json
import http.client
import os
import random
import shutil
import socket
import sys
import tempfile
import threading

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)

# 存档目录必须在 import notdnd_web 之前注入（SAVE_DIR 在 import 时定型）。
os.environ.setdefault("NOTDND_SAVE", tempfile.mkdtemp(prefix="notdnd-b14-"))

import notdnd_web  # noqa: E402
import prism_core  # noqa: E402
import tactical_battle_service as svc  # noqa: E402
import tactical_battlefield  # noqa: E402
import tactical_battlefield_gen as battlefield_gen  # noqa: E402
import tactical_context  # noqa: E402
import tactical_forge_adapter  # noqa: E402
import tactical_movement  # noqa: E402
import tactical_objects  # noqa: E402
import tactical_queries  # noqa: E402
import tactical_tactics_adapter  # noqa: E402

FIXTURE_DIR = os.path.join(TESTS, "fixtures", "hex_e2e")

_CRIT_MARGIN = prism_core.CRIT_MARGIN
_SOLID_MARGIN = prism_core.SOLID_MARGIN
_GRAZE_MARGIN = prism_core.GRAZE_MARGIN

# 现代风格战场（modern_future.json）的物件定义（#421 validate_definition
# 契约；既有 mech.blast-gate / hazard.steam-vent 复用服务端定义）。
E2E_DEFINITIONS = {
    **copy.deepcopy(svc.OBJECT_DEFINITIONS),
    "hazard.spike-cluster": {
        "definition_id": "hazard.spike-cluster",
        "kinds": ["hazard"],
        "initial_state": "armed",
        "states": {
            "armed": {"object": {"triggered": False}},
            "sprung": {"object": {"triggered": True}},
        },
        "transitions": [{"from": "armed", "to": "sprung", "trigger": "enter"}],
        "hazard_refs": ["spike-01"],
        "source": "tests/fixtures/hex_e2e/outdoor_hazard.json",
    },
    "prop.cargo-crate": {
        "definition_id": "prop.cargo-crate",
        "kinds": ["movable", "blocker"],
        "initial_state": "intact",
        "states": {"intact": {"object": {"blocks_movement": True}}},
        "transitions": [],
        "source": "tests/fixtures/hex_e2e/modern_future.json",
    },
    "prop.fuel-barrel": {
        "definition_id": "prop.fuel-barrel",
        "kinds": ["destructible", "blocker"],
        "initial_state": "intact",
        "states": {
            "intact": {"object": {"blocks_movement": True}},
            "destroyed": {"object": {"blocks_movement": False}},
        },
        "transitions": [{"from": "intact", "to": "destroyed",
                         "trigger": "interact"}],
        "source": "tests/fixtures/hex_e2e/modern_future.json",
    },
    "prop.data-chip": {
        "definition_id": "prop.data-chip",
        "kinds": ["pickup"],
        "initial_state": "intact",
        "states": {
            "intact": {"object": {"blocks_movement": False}},
            "collected": {"object": {"blocks_movement": False}},
        },
        "transitions": [{"from": "intact", "to": "collected",
                         "trigger": "pickup"}],
        "pickup": {"item_refs": ["item-data-chip-01"]},
        "source": "tests/fixtures/hex_e2e/modern_future.json",
    },
    "prop.energy-barrier": {
        "definition_id": "prop.energy-barrier",
        "kinds": ["mechanism"],
        "initial_state": "closed",
        "states": {
            "closed": {"object": {"blocks_movement": True}},
            "open": {"object": {"blocks_movement": False}},
        },
        "transitions": [{"from": "closed", "to": "open", "trigger": "interact"}],
        "source": "tests/fixtures/hex_e2e/modern_future.json",
    },
    "prop.arc-turret": {
        "definition_id": "prop.arc-turret",
        "kinds": ["hazard"],
        "initial_state": "armed",
        "states": {
            "armed": {"object": {"triggered": False}},
            "discharged": {"object": {"triggered": True}},
        },
        # 登记口径：物件占用自身格，enter 相位不可达（#415）；
        # 互动相位触发是既定权宜。
        "transitions": [{"from": "armed", "to": "discharged",
                         "trigger": "interact"}],
        "hazard_refs": ["arc-turret-01"],
        "source": "tests/fixtures/hex_e2e/modern_future.json",
    },
}


def _load_fixture(name):
    with open(os.path.join(FIXTURE_DIR, name), encoding="utf-8") as handle:
        return json.load(handle)


def _scene(world_id, place_id, tags=()):
    """ATLAS 场景只读快照（#446 _check_scene 契约的最小形态）。"""
    return {
        "world_id": world_id,
        "site_id": place_id,
        "atlas_ref": place_id,
        "place": {
            "id": place_id,
            "name": place_id,
            "kind": "room",
            "frame_id": world_id,
            "features": [{"tag": tag, "text": ""} for tag in tags],
        },
        "features": [],
    }


# ── 一、三种战场 fixture ────────────────────────────────────────────────

def check_fixtures_three_battlefields():
    """三种主题战场：同一生成器、同一套代码路径，差异全部来自模板数据。"""
    plans = (
        ("indoor_multilevel.json", "abstract",
         {"surfaces": 2, "stairs": 1, "objects": 2}),
        ("outdoor_hazard.json", "metric",
         {"pruned": True, "objects": 1}),
        ("modern_future.json", "abstract",
         {"categories": {"movable", "destructible", "pickup",
                         "mechanism", "hazard"}}),
    )
    for name, mode, expect in plans:
        template = _load_fixture(name)
        scene = _scene("w-" + name.split("_")[0], "place-" + name.split("_")[0])
        first = battlefield_gen.generate_battlefield(scene, template,
                                                     seed="b14-" + name,
                                                     mode=mode)
        battlefield = first["battlefield"]
        # 生成结果必须通过既有加载器终校验（生成器自己也校验，这里独立复核）。
        assert tactical_battlefield.load_battlefield(copy.deepcopy(battlefield))
        assert battlefield["scale"]["mode"] == mode
        surfaces = {cell["surface_id"] for cell in battlefield["cells"]}
        links = battlefield["vertical_links"]
        objects = first["objects"]
        if "surfaces" in expect:
            assert len(surfaces) == expect["surfaces"], (name, surfaces)
        if "stairs" in expect:
            assert len(links) == expect["stairs"] \
                and links[0]["kind"] == "stairs", (name, links)
        if "pruned" in expect:
            assert first["diagnostics"]["counts"]["cells_pruned"] > 0, name
        if "objects" in expect:
            assert len(objects) == expect["objects"], (name, objects)
        if "categories" in expect:
            assert {obj["category"] for obj in objects} \
                == expect["categories"], name
        # 确定性：同模板同 seed 复现同一张图与同一批对象。
        again = battlefield_gen.generate_battlefield(scene, template,
                                                     seed="b14-" + name,
                                                     mode=mode)
        assert again["battlefield"] == battlefield, name
        assert again["objects"] == objects, name
        # 户外危险地形的危险 / 障碍语义确实落在格数据上。
        if name == "outdoor_hazard.json":
            hazard_cells = [cell for cell in battlefield["cells"]
                            if cell.get("hazard_refs")]
            blocked = [cell for cell in battlefield["cells"]
                       if cell.get("blocks_los")]
            assert hazard_cells and blocked, name
        if name == "indoor_multilevel.json":
            grades = {cell.get("cover_grade") for cell in battlefield["cells"]}
            assert {"medium", "heavy"} <= grades, name
            balcony = [cell for cell in battlefield["cells"]
                       if cell["surface_id"] == "balcony"]
            floor = [cell for cell in battlefield["cells"]
                     if cell["surface_id"] == "floor"]
            assert all(cell["elevation_level"] >= 1 for cell in balcony) \
                and all(cell["elevation_level"] == 0 for cell in floor), name


def check_fixture_world_id_neutrality():
    """主题差异来自数据而非世界名代码分支：换 world_id 不改变任何格位。"""
    template = _load_fixture("indoor_multilevel.json")
    base = battlefield_gen.generate_battlefield(
        _scene("w-alpha", "place-x"), template, seed=77, mode="abstract")
    other = battlefield_gen.generate_battlefield(
        _scene("w-totally-different", "place-x"), template, seed=77,
        mode="abstract")
    assert base["battlefield"]["cells"] == other["battlefield"]["cells"]
    assert base["battlefield"]["vertical_links"] \
        == other["battlefield"]["vertical_links"]
    assert base["objects"] == other["objects"]
    assert base["battlefield"]["world_id"] == "w-alpha"
    assert other["battlefield"]["world_id"] == "w-totally-different"
    # 反向：同一 world_id、不同模板数据 → 战场结构不同（差异由数据驱动）。
    outdoor = battlefield_gen.generate_battlefield(
        _scene("w-alpha", "place-y"), _load_fixture("outdoor_hazard.json"),
        seed=77, mode="metric")
    assert outdoor["battlefield"]["cells"] != base["battlefield"]["cells"]


def _fixture_battle_state(battlefield, objects, placements):
    """在 fixture 战场上构造最小 battle_state（#447 契约的真实输入）。"""
    return {
        "schema_version": 1,
        "kind": "tactical.battle_state",
        "battle_id": "battle-fixture-b14",
        "battlefield_id": battlefield["battlefield_id"],
        "round": 1,
        "turn": 0,
        "active_unit_id": "u-hero",
        "state_revision": 0,
        "last_event_sequence": 0,
        "rng_ref": "seed:b14",
        "participants": [
            {"unit_id": unit_id, "cell_id": cell_id}
            for unit_id, cell_id in placements
        ],
        "objects": [copy.deepcopy(obj) for obj in objects],
    }


def _pair_facts(battlefield, predicate):
    """按谓词找第一对同 surface 格位（确定性：按 cell_id 排序遍历）。"""
    cells = sorted(battlefield["cells"],
                   key=lambda cell: (cell["surface_id"], cell["cell_id"]))
    for source in cells:
        for target in cells:
            if source["surface_id"] != target["surface_id"] \
                    or source["cell_id"] == target["cell_id"]:
                continue
            query = tactical_queries.query_attack(
                battlefield, source["cell_id"], target["cell_id"],
                distance_unit="cell" if battlefield["scale"]["mode"]
                == "abstract" else None)
            if predicate(source, target, query):
                return source, target, query
    return None


# ── 二、端到端主流程（HTTP） ────────────────────────────────────────────

def strong_party(session, names=("甲", "乙", "丙")):
    """一支耐打的队伍（2–3 名玩家友方单位，验收口径下限之上）。"""
    for index, name in enumerate(names):
        unit = prism_core.new_unit("u-%d" % (index + 1), name,
                                   attributes={"MGT": 8, "FIN": 6, "INS": 6})
        unit["vitality"] = 80
        unit["max_vitality"] = 80
        session.party.append(unit)
    session.scene = {"id": "sc-b14"}
    return session


def make_record(seed=429, rng_seed=431):
    """服务层入口开一场战斗（不含 HTTP），供规则 / replay 用例复用。"""
    session = strong_party(prism_core.RuleSession("b14"))
    record = svc.start_battle(
        rules_snapshot=session.snapshot(),
        scene=svc.scene_for("local", "sc-b14"),
        battle_id="battle-b14", seed=seed, rng_seed=rng_seed,
        strength="entangled")
    return record, session


def fresh_live(snapshot):
    return prism_core.RuleSession.from_snapshot(snapshot)


def _hex_dist(record, a, b):
    return svc.tactical_hex_distance(
        record["battlefield"],
        svc._participant(record, a)["cell_id"],
        svc._participant(record, b)["cell_id"])


def _offsets():
    return [(o["q"], o["r"]) for o in tactical_battlefield.space_rules()
            ["geometry"]["neighbor_offsets"]]


def _approach_destinations(battlefield, target_cell, occupied, origin):
    """目标格（可进入时）→ 空闲邻格按离行动者近序（确定性）。"""
    candidates = []
    cell = tactical_battlefield.cell_by_id(battlefield, target_cell)
    if cell is not None and not cell.get("blocks_movement") \
            and target_cell not in occupied:
        candidates.append(target_cell)
    neighbors = []
    for dq, dr in _offsets():
        neighbor = tactical_battlefield.cell_at(
            battlefield, cell["q"] + dq, cell["r"] + dr,
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
    """按 #419 完整 Dijkstra 路径取下一步（目标被占改走最近空闲邻格）。"""
    battlefield = record["battlefield"]
    origin = svc._participant(record, actor_id)["cell_id"]
    occupied = tactical_objects.occupancy_map(
        record["battle_state"], battlefield, exclude=[actor_id])
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


def _nearest_enemy(record, live, actor_id):
    best = None
    best_key = None
    for enemy_id in svc._enemy_ids(record):
        unit, _side = svc._resolve_unit(record, live, enemy_id)
        if unit is None or not (not unit.get("dead")
                                and not unit.get("downed")):
            continue
        key = (_hex_dist(record, actor_id, enemy_id), enemy_id)
        if best_key is None or key < best_key:
            best_key = key
            best = enemy_id
    return best


def _conscious(record, live, unit_id):
    unit, _side = svc._resolve_unit(record, live, unit_id)
    return unit is not None and not unit.get("dead") and not unit.get("downed")


def _drive_to_adjacent(record, live, enemy_id, max_rounds=30):
    """确定性走位：轮到谁谁走位（花完 3 AP），直到与目标相邻。"""
    rev = lambda: record["battle_state"]["state_revision"]  # noqa: E731
    for _ in range(max_rounds):
        if record["status"] != svc.STATUS_ACTIVE:
            return False
        actor = record["battle_state"].get("active_unit_id") or ""
        entry = svc._unit_entry(record, actor) or {}
        if not entry.get("party"):
            return False
        if _conscious(record, live, enemy_id) \
                and _hex_dist(record, actor, enemy_id) <= 1:
            return True
        unit, _side = svc._resolve_unit(record, live, actor)
        if unit is None or unit.get("downed"):
            svc.do_end_turn(record, live, actor_id=actor,
                            expected_revision=rev())
            continue
        for _ap in range(3):
            if _conscious(record, live, enemy_id) \
                    and _hex_dist(record, actor, enemy_id) <= 1:
                break
            step = _step_toward(record, live, actor,
                                svc._participant(record, enemy_id)["cell_id"])
            if not step:
                break
            try:
                svc.do_move(record, live, actor_id=actor, to_cell_id=step,
                            expected_revision=rev())
            except svc.TacticalError:
                break
        if _conscious(record, live, enemy_id) \
                and _hex_dist(record, actor, enemy_id) <= 1:
            return True
        svc.do_end_turn(record, live, actor_id=actor, expected_revision=rev())
    return False


# ── HTTP 脚手架（本进程服务线程，沿用仓库既有模式）──────────────────────

def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class _Local:
    """本进程起一台 notdnd_web 服务（随机端口 + 独立存档目录）。"""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="notdnd-b14-http-")
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


_KEY_COUNTER = [0]


def _next_key(prefix="b14"):
    _KEY_COUNTER[0] += 1
    return "%s%08d" % (prefix, _KEY_COUNTER[0])


def _new_session(http):
    status, body = http.post("/api/session/create", {})
    assert status == 200, (status, body)
    return body["sid"]


def _seed_party(http, sid):
    session = notdnd_web.get_session(sid)
    with session.lock:
        rules = prism_core.RuleSession(sid)
        strong_party(rules)
        session.rules = rules.snapshot()
        session.save()


def _rev(http, sid):
    status, body = http.get("/api/tactical/battle", sid=sid)
    assert status == 200, (status, body)
    return body["battle"]["state_revision"], body["battle"]


def _party_ids(battle):
    return {row["unit_id"] for row in battle["participants"] if row["party"]}


def _enemy_ids_view(battle):
    return [row["unit_id"] for row in battle["participants"]
            if not row["party"] and not (row.get("unit") or {}).get("dead")]


def _cell_of(battle, unit_id):
    return next(row["cell_id"] for row in battle["participants"]
                if row["unit_id"] == unit_id)


def _http_step_from_view(battle, actor, target_cell, budget=30):
    """HTTP 快照（只读）按 #419 完整路径取下一步。"""
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


def _http_move(http, sid, to_cell_id):
    revision, _battle = _rev(http, sid)
    status, body = http.post(
        "/api/tactical/move",
        {"to_cell_id": to_cell_id, "expected_revision": revision},
        sid=sid, headers={"Idempotency-Key": _next_key("b14mv")})
    return status, body


def _http_end_turn(http, sid):
    revision, _battle = _rev(http, sid)
    status, body = http.post(
        "/api/tactical/end_turn", {"expected_revision": revision},
        sid=sid, headers={"Idempotency-Key": _next_key("b14tn")})
    assert status == 200, (status, body)
    return body


def _http_drive_adjacent(http, sid, max_rounds=30):
    """HTTP 版走位：谁行动谁走位（每回合挪 1 格，留 2 AP 给攻击）。"""
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
        step = _http_step_from_view(battle, actor, _cell_of(battle, target))
        if step:
            status, _body = _http_move(http, sid, step)
            if status == 200:
                _revision, battle = _rev(http, sid)
                if any(svc.tactical_hex_distance(
                        battle["battlefield"], _cell_of(battle, actor),
                        _cell_of(battle, enemy)) <= 1
                       for enemy in _enemy_ids_view(battle)):
                    return True
        _http_end_turn(http, sid)
    return False


def check_e2e_full_battle_http():
    """HTTP 端到端：开战 → 非法/合法移动 → 攻击 → 敌方回合 → 逃跑 → 幂等。"""
    with _Local() as http:
        sid = _new_session(http)
        _seed_party(http, sid)
        status, body = http.post("/api/tactical/start", {"strength": "entangled"},
                                 sid=sid,
                                 headers={"Idempotency-Key": "b14start00000001"})
        assert status == 200, (status, body)
        battle = body["battle"]
        assert battle["status"] == "active" and battle["battle_id"]
        party = _party_ids(battle)
        assert 2 <= len(party) <= 3, party  # 验收：至少 2–3 名玩家友方单位
        assert len(_enemy_ids_view(battle)) >= 1
        cells = [row["cell_id"] for row in battle["participants"]]
        assert len(cells) == len(set(cells)), "参战者占格必须互斥"
        for field in ("battlefield", "objects", "object_definitions",
                      "state_revision", "turn_order", "turn_ap",
                      "legal_actions"):
            assert field in battle, field
        # AP 经济来自数据（验收：AP / 回合 / 遭遇预算）。
        assert battle["legal_actions"] == svc.legal_actions()
        assert set(battle["turn_ap"].values()) == {svc.ap_per_turn()}

        # ── 非法移动：未知格 / 预算外 / 敌体占格 / 陈旧修订号 ──
        actor = battle["active_unit_id"]
        status, body = http.post(
            "/api/tactical/move",
            {"to_cell_id": "cell-nope",
             "expected_revision": battle["state_revision"]},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status == 404 and body.get("reason") == "no_cell", (status, body)
        far = battle["battlefield"]["cells"][-1]["cell_id"]
        status, body = http.post(
            "/api/tactical/move",
            {"to_cell_id": far,
             "expected_revision": battle["state_revision"]},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status == 422 \
            and body.get("reason") == "destination_unreachable", (status, body)
        blocked = body.get("movement_blocked") or []
        assert blocked and all(entry.get("rule") for entry in blocked), body
        enemy = _enemy_ids_view(battle)[0]
        status, body = http.post(
            "/api/tactical/move",
            {"to_cell_id": _cell_of(battle, enemy),
             "expected_revision": battle["state_revision"]},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status == 422, (status, body)
        status, body = http.post(
            "/api/tactical/move",
            {"to_cell_id": far, "expected_revision": 99999},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status == 409 and body.get("reason") == "stale_revision" \
            and body.get("state_revision") == battle["state_revision"], body

        # ── 合法移动：服务端重算路径，AP 按数据价目扣 ──
        step = _http_step_from_view(battle, actor, _cell_of(battle, enemy))
        assert step, "应当存在合法的相邻步"
        before_ap = battle["turn_ap"][actor]
        move_key = _next_key("b14mv")
        revision, _battle = _rev(http, sid)
        status, body = http.post(
            "/api/tactical/move",
            {"to_cell_id": step, "expected_revision": revision},
            sid=sid, headers={"Idempotency-Key": move_key})
        assert status == 200, (status, body)
        revision, battle = _rev(http, sid)
        assert _cell_of(battle, actor) == step
        assert battle["turn_ap"][actor] == before_ap - svc.ap_cost("move")
        committed_revision = revision

        # ── 幂等重放：同键重投回放首次结果，修订号不再推进 ──
        status, replay = http.post(
            "/api/tactical/move", {"to_cell_id": step,
                                   "expected_revision": committed_revision},
            sid=sid, headers={"Idempotency-Key": move_key})
        assert status == 200 and replay.get("replayed") is True, (status, replay)
        _revision, battle = _rev(http, sid)
        assert battle["state_revision"] == committed_revision, \
            "重放不得推进修订号"

        # ── 攻击：远距结构化拒绝（射程与原因可追踪）──
        target = min(_enemy_ids_view(battle), key=lambda uid: (
            svc.tactical_hex_distance(battle["battlefield"],
                                      _cell_of(battle, actor),
                                      _cell_of(battle, uid)), uid))
        if svc.tactical_hex_distance(battle["battlefield"],
                                     _cell_of(battle, actor),
                                     _cell_of(battle, target)) > 1:
            status, body = http.post(
                "/api/tactical/attack",
                {"target_id": target,
                 "expected_revision": battle["state_revision"]},
                sid=sid, headers={"Idempotency-Key": _next_key()})
            assert status == 422 \
                and body.get("reason") == "target_out_of_range", (status, body)
            reasons = body.get("reasons") or []
            assert reasons and reasons[0].get("rule_ref"), body

        # ── 走位接敌 → 真实结算一次攻击 ──
        assert _http_drive_adjacent(http, sid), "应当能在数回合内接敌"
        revision, battle = _rev(http, sid)
        actor = battle["active_unit_id"]
        enemies = _enemy_ids_view(battle)
        target = min(enemies, key=lambda uid: (
            svc.tactical_hex_distance(battle["battlefield"],
                                      _cell_of(battle, actor),
                                      _cell_of(battle, uid)), uid))
        assert svc.tactical_hex_distance(battle["battlefield"],
                                         _cell_of(battle, actor),
                                         _cell_of(battle, target)) <= 1
        # 伪造结果字段：骰值 / 命中档 / 伤害 / 资源 / 对象状态全部被忽略。
        forged = {"target_id": target, "expected_revision": revision,
                  "damage": 987654, "grade": "crit", "attack_total": 876543,
                  "ap_cost": -5, "vitality": 987654, "poise": -987,
                  "state": "open"}
        status, out = http.post("/api/tactical/attack", forged, sid=sid,
                                headers={"Idempotency-Key":
                                         "b14atk0000000001"})
        assert status == 200, (status, out)
        dumped = json.dumps(out, ensure_ascii=False)
        for sentinel in ("987654", "876543", "-987"):
            assert sentinel not in dumped, "伪造值不得出现在响应里"
        assert out["result"]["grade"] in ("hit", "solid", "crit", "graze",
                                          "miss", "fumble"), out["result"]

        # ── 敌方回合：服务端就地结算（敌体会动、会打、会停）──
        before_positions = {row["unit_id"]: row["cell_id"]
                            for row in battle["participants"]}
        _http_end_turn(http, sid)
        revision, battle = _rev(http, sid)
        events = battle["recent_events"]
        enemy_acted = [event for event in events
                       if event.get("actor_id") not in party
                       and event.get("type") in ("move_committed",
                                                 "attack_resolved")]
        assert enemy_acted or battle["status"] != "active", events
        # 敌体回合事件必须由服务端产生（带引擎标记）。
        for event in enemy_acted:
            assert event.get("rule_engine") == svc.ENGINE, event

        # ── 逃跑收尾：代价真实（压力推进、无经验），战斗结束 ──
        revision, battle = _rev(http, sid)
        status, out = http.post(
            "/api/tactical/flee", {"expected_revision": revision},
            sid=sid, headers={"Idempotency-Key": "b14flee0000000001"})
        assert status == 200, (status, out)
        assert out["battle"]["status"] == "ended" \
            and out["battle"]["outcome"] == "fled", out["battle"]
        types = [event["type"] for event in out["battle"]["recent_events"]]
        assert "combat_ended" in types, types
        # 结束后再行动 → 404 no_battle。
        status, body = http.post(
            "/api/tactical/attack", {"target_id": enemy,
                                     "expected_revision": 0},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status == 404 and body.get("reason") == "no_battle", (status, body)
        return sid


def check_e2e_restore_and_legacy_saves():
    """断线重连 / 结束后恢复 / 旧档惰性迁移 / 战场重建同身份。"""
    with _Local() as http:
        sid = _new_session(http)
        _seed_party(http, sid)
        status, body = http.post("/api/tactical/start", {"strength": "entangled"},
                                 sid=sid,
                                 headers={"Idempotency-Key":
                                          "b14start00000002"})
        assert status == 200, (status, body)
        _http_drive_adjacent(http, sid, max_rounds=3)
        _revision, before = _rev(http, sid)
        # 模拟断线重连：清内存缓存，强制从磁盘重新载入。
        with notdnd_web._sessions_lock:
            notdnd_web._sessions.pop(sid, None)
        status, after = http.get("/api/tactical/battle", sid=sid)
        assert status == 200, (status, after)
        restored = after["battle"]
        for field in ("battle_id", "state_revision", "last_event_sequence",
                      "round", "active_unit_id", "turn_order", "objects"):
            assert restored[field] == before[field], field
        assert {row["unit_id"]: row["cell_id"]
                for row in restored["participants"]} \
            == {row["unit_id"]: row["cell_id"]
                for row in before["participants"]}
        # 恢复后的战斗照常可写。
        status, body = http.post(
            "/api/tactical/end_turn",
            {"expected_revision": restored["state_revision"]},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status == 200, (status, body)
        # 逃跑结束后再断线：终局记录原样恢复。
        revision, _battle = _rev(http, sid)
        status, out = http.post(
            "/api/tactical/flee", {"expected_revision": revision},
            sid=sid, headers={"Idempotency-Key": "b14flee0000000002"})
        assert status == 200, (status, out)
        with notdnd_web._sessions_lock:
            notdnd_web._sessions.pop(sid, None)
        status, after = http.get("/api/tactical/battle", sid=sid)
        assert status == 200 and after["battle"]["status"] == "ended" \
            and after["battle"]["outcome"] == "fled", (status, after)
        # 战场重建：模板快照 + seed 复现同一张图；元数据被改 → 显式失败。
        session = notdnd_web.get_session(sid)
        with session.lock:
            record = copy.deepcopy(session.tactical)
        rebuilt = svc.restore_battlefield(record)
        assert rebuilt == record["battlefield"]
        tampered = copy.deepcopy(record)
        tampered["battlefield_meta"]["battlefield_id"] = "bf-other"
        try:
            svc.restore_battlefield(tampered)
            raise AssertionError("身份不一致应当显式失败")
        except svc.TacticalError as exc:
            assert exc.reason == "battlefield_rebuild_mismatch"
        # 旧档（无 tactical 块）惰性迁移：照常加载、战斗面 404。
        legacy_sid = "legacy-b14-old"
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


# ── 三、确定性 replay ───────────────────────────────────────────────────

def check_e2e_deterministic_replay():
    """固定 seed：两次开战记录一致；相同操作序列产出相同事件流。"""
    session = strong_party(prism_core.RuleSession("b14"))
    snapshot = session.snapshot()
    record_a = svc.start_battle(
        rules_snapshot=copy.deepcopy(snapshot),
        scene=svc.scene_for("local", "sc-b14"),
        battle_id="battle-replay", seed=99, rng_seed=101,
        strength="entangled")
    record_b = svc.start_battle(
        rules_snapshot=copy.deepcopy(snapshot),
        scene=svc.scene_for("local", "sc-b14"),
        battle_id="battle-replay", seed=99, rng_seed=101,
        strength="entangled")
    # 同 seed 同输出：战场、先攻、事件流逐项一致。
    assert record_a["battlefield"] == record_b["battlefield"]
    assert record_a["turn_order"] == record_b["turn_order"]
    assert record_a["events"] == record_b["events"]
    assert record_a["battle_state"]["participants"] \
        == record_b["battle_state"]["participants"]
    live_a = fresh_live(copy.deepcopy(snapshot))
    live_b = fresh_live(copy.deepcopy(snapshot))
    # 相同操作序列：事件流（序号 / 类型 / 行动者 / 目标）保持一致。
    rev = lambda rec: rec["battle_state"]["state_revision"]  # noqa: E731
    for _round in range(3):
        if record_a["status"] != svc.STATUS_ACTIVE:
            break
        actor = record_a["battle_state"]["active_unit_id"]
        entry = svc._unit_entry(record_a, actor) or {}
        if not entry.get("party"):
            break
        enemy = _nearest_enemy(record_a, live_a, actor)
        if enemy is None:
            break
        step = _step_toward(record_a, live_a, actor,
                            svc._participant(record_a, enemy)["cell_id"])
        if step:
            svc.do_move(record_a, live_a, actor_id=actor, to_cell_id=step,
                        expected_revision=rev(record_a))
            svc.do_move(record_b, live_b, actor_id=actor, to_cell_id=step,
                        expected_revision=rev(record_b))
        svc.do_end_turn(record_a, live_a, actor_id=actor,
                        expected_revision=rev(record_a))
        svc.do_end_turn(record_b, live_b, actor_id=actor,
                        expected_revision=rev(record_b))
        key = lambda event: (event["sequence"], event["type"],  # noqa: E731
                             event.get("actor_id"), event.get("target_id"))
        assert [key(event) for event in record_a["events"]] \
            == [key(event) for event in record_b["events"]]


# ── 四、规则完整性：d20 战术引擎（经 #424 适配器 → #423 核心）───────────

def _d20_seed(natural, avoid=()):
    """找一批让 d20 掷出指定面值的确定性 seed（真实随机，固定可复现）。"""
    found = []
    for seed in range(1, 4000):
        roll = prism_core.roll("1d20", rng=random.Random(seed))
        if roll["natural"] == natural and seed not in avoid:
            found.append(seed)
            if len(found) >= 6:
                break
    assert found, natural
    return found


def _has_condition(unit, name):
    conditions = unit.get("conditions")
    if isinstance(conditions, dict):
        return name in conditions
    if isinstance(conditions, list):
        return any((item.get("name") if isinstance(item, dict) else item) == name
                   for item in conditions)
    return False


def _attack_case(*, natural, attack_bonus=0, damage_bonus=0, tag="",
                 prepare=None, seed_index=0):
    """一次经真实 context → 适配器 → 核心的攻击结算（独立 record 隔离）。

    `prepare(actor, target)` 在结算前改写单位数据（伤害标签关系 /
    韧性 / 首领阶段等）；`seed_index` 选取同一面值下的不同确定性 seed，
    保证对照用例（plain vs 抗性 / 弱点 / 首领阶段）消费同一条骰流。
    """
    record, session = make_record()
    live = fresh_live(session.snapshot())
    actor_id = "u-1"
    enemy_id = svc._enemy_ids(record)[0]
    actor, _side = svc._resolve_unit(record, live, actor_id)
    target, _side = svc._resolve_unit(record, live, enemy_id)
    if prepare is not None:
        prepare(actor, target)
    context = tactical_context.build_context(
        record["battlefield"], record["battle_state"],
        actor_id=actor_id, target_id=enemy_id, distance_unit="cell")
    assert context["status"] == "ok", context.get("reasons")
    seeds = _d20_seed(natural)
    out = tactical_tactics_adapter.execute_attack(
        live, actor, target, context, rng=random.Random(seeds[seed_index]),
        notation="1d6", damage_bonus=damage_bonus,
        attack_bonus=attack_bonus, tag=tag)
    assert out["status"] == "ok", out.get("reasons")
    return actor, target, out


def check_rules_d20_settlement():
    """d20 命中六档 / 自然 1 / 伤害标签 / 韧性破韧 / 首领阶段——真实结算。"""
    # 命中六档：margin 决定档位（用防护值反推加值，确定性命中每一档）。
    _actor, _target, probe = _attack_case(natural=11, attack_bonus=99,
                                          damage_bonus=5)
    guard = probe["summary"]["guard"]
    grade_of = lambda out: out["summary"]["grade"]  # noqa: E731
    _actor, _target, crit_out = _attack_case(natural=20, attack_bonus=99,
                                             damage_bonus=5)
    assert grade_of(crit_out) == "crit", crit_out["summary"]
    for natural, grade in ((11, "hit"), (11, "solid"), (11, "graze"),
                           (11, "miss")):
        if grade == "hit":
            bonus = guard - natural + 2
        elif grade == "solid":
            bonus = guard - natural + _SOLID_MARGIN + 1
        elif grade == "graze":
            bonus = guard - natural + _GRAZE_MARGIN
        else:
            bonus = -natural - guard - 3
        _actor, _target, out = _attack_case(natural=natural,
                                            attack_bonus=bonus,
                                            damage_bonus=5)
        assert grade_of(out) == grade, (grade, out["summary"])
    # 自然 1 严重失手：无伤害、攻击者获得【失衡】、掷失手表。
    actor, _target, out = _attack_case(natural=1, attack_bonus=99)
    assert grade_of(out) == "fumble", out["summary"]
    assert out["result"]["damage"] == 0
    assert 1 <= out["result"].get("fumble_row", 0) <= 6
    assert _has_condition(actor, "失衡"), actor.get("conditions")
    # 暴击：两骰取高 + 目标获得【失衡】。
    _actor, target, out = _attack_case(natural=20, attack_bonus=99,
                                       damage_bonus=5)
    assert grade_of(out) == "crit"
    assert _has_condition(target, "失衡"), target.get("conditions")
    # 抗性：同 seed 同骰流，火焰伤害减半（目标声明 resistance）。
    _actor, _target, plain = _attack_case(natural=11, attack_bonus=99,
                                          damage_bonus=5, seed_index=0)
    assert grade_of(plain) in ("hit", "solid", "crit")
    _actor, target, out = _attack_case(
        natural=11, attack_bonus=99, damage_bonus=5, tag="火焰",
        seed_index=0,
        prepare=lambda _a, target:
            target.update({"damage_relations": {"resistance": ["火焰"]}}))
    assert out["result"]["damage_result"]["relation"] == "resistance", out
    assert out["summary"]["damage"] < plain["summary"]["damage"]
    # 弱点：伤害 +50% 且目标获得【失衡】。
    _actor, target, out = _attack_case(
        natural=11, attack_bonus=99, damage_bonus=5, tag="火焰",
        seed_index=0,
        prepare=lambda _a, target:
            target.update({"damage_relations": {"weakness": ["火焰"]}}))
    assert out["result"]["damage_result"]["relation"] == "weakness"
    assert out["summary"]["damage"] > plain["summary"]["damage"]
    assert _has_condition(target, "失衡")
    # 韧性破韧：韧性归零 → broken（普通命中削一半，向下取整）。
    _actor, target, out = _attack_case(
        natural=11, attack_bonus=99, damage_bonus=9, seed_index=0,
        prepare=lambda _a, target: target.update({"max_poise": 2,
                                                  "poise": 2}))
    assert grade_of(out) in ("hit", "solid", "crit")
    assert (out["result"].get("poise") or {}).get("broken") is True, \
        out["result"].get("poise")
    assert target.get("poise_broken") is True
    # 首领阶段：绝望形态（boss_phase>=2）全部伤害 +2（同 seed 对照）。
    _actor, _target, plain_out = _attack_case(natural=11, attack_bonus=99,
                                              damage_bonus=5, seed_index=0)
    _actor, _target, boss_out = _attack_case(
        natural=11, attack_bonus=99, damage_bonus=5, seed_index=0,
        prepare=lambda actor, _t: actor.update({"boss_phase": 2}))
    assert grade_of(boss_out) == grade_of(plain_out)
    assert boss_out["summary"]["damage"] \
        == plain_out["summary"]["damage"] + prism_core.BOSS_PHASE2_DAMAGE


def check_rules_d20_cover_and_high_ground():
    """掩体档 → 防护加值；高地 → 助势骰；完全掩体 → 拦截（不掷骰）。"""
    template = _load_fixture("indoor_multilevel.json")
    generated = battlefield_gen.generate_battlefield(
        _scene("w-indoor", "place-indoor"), template, seed=5, mode="abstract")
    battlefield = generated["battlefield"]
    cover_pair = _pair_facts(
        battlefield,
        lambda _s, _t, q: q["line_of_sight"] is True
        and q["cover_grade"] in ("light", "medium", "heavy"))
    assert cover_pair, "fixture 必须提供带掩体的真实射线"
    source, target, _query = cover_pair
    state = _fixture_battle_state(
        battlefield, generated["objects"],
        (("u-hero", source["cell_id"]), ("e-orc", target["cell_id"])))
    context = tactical_context.build_context(
        battlefield, state, actor_id="u-hero", target_id="e-orc",
        distance_unit="cell")
    assert context["status"] == "ok" and context["cover_grade"], context
    session = prism_core.RuleSession("b14-cover")
    unit = prism_core.new_unit("u-hero", "射手")
    enemy = prism_core.new_unit("e-orc", "敌体")
    session.party.append(unit)
    plain = prism_core.guard_breakdown(enemy, session)["total"]
    result = tactical_tactics_adapter.resolve_guard(enemy, session, context)
    assert result["status"] == "ok", result
    grade = context["cover_grade"]
    expect_bonus = {"light": 2, "medium": 4, "heavy": 6}[grade]
    assert result["summary"]["guard"] == plain + expect_bonus, (
        grade, plain, result["summary"])
    mapped = [item for item in result["mapped"]
              if item["fact"] == "cover_grade"]
    assert mapped and mapped[0]["rule_ref"], result["mapped"]

    # 高地：同 surface 相邻层差 → high_ground → d20 助势 +1（02A:93）。
    high_pair = _pair_facts(
        battlefield,
        lambda s, t, q: q["line_of_sight"] is True
        and q.get("signed_level_delta") is not None
        and q["signed_level_delta"] <= -1)
    if high_pair:
        source, target, _query = high_pair
        state = _fixture_battle_state(
            battlefield, generated["objects"],
            (("u-hero", source["cell_id"]), ("e-orc", target["cell_id"])))
        context = tactical_context.build_context(
            battlefield, state, actor_id="u-hero", target_id="e-orc",
            distance_unit="cell")
        assert context["high_ground"] is True, context
        mapping = prism_core.map_context_for_d20(context)
        assert mapping["ok"] and mapping["inputs"]["advantage_layers"] == 1
        assert mapping["mapped"][0]["rule_ref"] \
            == prism_core.CONTEXT_RULE_REFS["high_ground_bonus"]
    # 完全掩体：d20 → blocked（不掷骰）；构建 → unsupported_target。
    full_pair = _pair_facts(
        battlefield,
        lambda _s, _t, q: q["cover_grade"] == "full")
    if full_pair:
        source, target, _query = full_pair
        state = _fixture_battle_state(
            battlefield, generated["objects"],
            (("u-hero", source["cell_id"]), ("e-orc", target["cell_id"])))
        context = tactical_context.build_context(
            battlefield, state, actor_id="u-hero", target_id="e-orc",
            distance_unit="cell")
        out = tactical_tactics_adapter.execute_attack(
            session, unit, enemy, context)
        assert out["status"] == "blocked" and out["result"] is None, out
        forge = tactical_forge_adapter.resolve_build_attack(
            context, attribute=5, prof_bonus=2, target_type="standard")
        assert forge["status"] == "unsupported_target", forge


# ── 五、规则完整性：构建引擎（经 #425 适配器 → #423 核心）───────────────

def check_rules_build_settlement():
    """骰池 / 阈值 / 动量 / 应力 / 超载连锁——真实结算且不混入 d20 概念。"""
    record, _session = make_record()
    enemy_id = svc._enemy_ids(record)[0]
    context = tactical_context.build_context(
        record["battlefield"], record["battle_state"], actor_id="u-1",
        target_id=enemy_id, distance_unit="cell")
    assert context["status"] == "ok"

    # 骰池构成（02B 第一节）：属性 + 熟练折半 + 专精 + 临时。
    pool = prism_core.build_pool(6, 2, specialization=True, temp=1)
    assert pool == {"attribute": 6, "proficiency": 1, "specialization": 1,
                    "temp": 1, "total": 9}, pool

    # 阈值修正：掩体 / 高地各 +1，封顶 +2（02B:261）。
    base_need = prism_core.build_dodge_need("standard")
    assert prism_core.build_dodge_need_adjusted("standard", modifiers=1) \
        == base_need + 1
    assert prism_core.build_dodge_need_adjusted("standard", modifiers=5) \
        == base_need + prism_core.BUILD_DODGE_MODIFIER_MAX

    # 掩体 context → 需求成功数 +1；结算五段全走核心。
    template = _load_fixture("indoor_multilevel.json")
    generated = battlefield_gen.generate_battlefield(
        _scene("w-indoor", "place-indoor"), template, seed=5, mode="abstract")
    battlefield = generated["battlefield"]
    cover_pair = _pair_facts(
        battlefield,
        lambda _s, _t, q: q["line_of_sight"] is True
        and q["cover_grade"] in ("light", "medium", "heavy"))
    assert cover_pair
    source, target, _query = cover_pair
    state = _fixture_battle_state(
        battlefield, generated["objects"],
        (("u-hero", source["cell_id"]), ("e-orc", target["cell_id"])))
    cover_context = tactical_context.build_context(
        battlefield, state, actor_id="u-hero", target_id="e-orc",
        distance_unit="cell")
    forge = tactical_forge_adapter.resolve_build_attack(
        cover_context, attribute=6, prof_bonus=2, specialization=True,
        target_type="standard", rng=random.Random(7))
    assert forge["status"] == "ok", forge
    settlement = forge["settlement"]
    assert settlement["pool"]["total"] == 8, settlement["pool"]
    assert settlement["dodge_need"] == base_need + 1, settlement["dodge_need"]
    assert settlement["roll"]["momentum"] \
        == prism_core.BUILD_OUTCOME_MOMENTUM[settlement["roll"]["outcome"]] \
        + (1 if settlement["roll"]["all_fail"] else 0)
    assert settlement["attack"]["result"] in (
        "pierce", "multi", "hit", "graze", "miss")
    assert settlement["overload"]["options"] \
        == list(prism_core.BUILD_OVERLOAD_CHAIN_OPTIONS)
    # 映射明细可追踪：mapped 带 rule_ref，距离 / 高度差逐项 not_mapped。
    mapped = [item for item in forge["mapping"]["mapped"]
              if item["fact"] == "cover_grade"]
    assert mapped and mapped[0]["rule_ref"]
    assert forge["mapping"]["not_mapped"], forge["mapping"]
    assert forge["unmapped_features"], "地形 / 特征应显式声明未映射"

    # 视线被遮 → attack_blocked（不掷骰）；stale context → 结构化拒绝。
    blocked_pair = _pair_facts(
        battlefield,
        lambda _s, _t, q: q["line_of_sight"] is False)
    if blocked_pair:
        source, target, _query = blocked_pair
        state = _fixture_battle_state(
            battlefield, generated["objects"],
            (("u-hero", source["cell_id"]), ("e-orc", target["cell_id"])))
        blocked_context = tactical_context.build_context(
            battlefield, state, actor_id="u-hero", target_id="e-orc",
            distance_unit="cell")
        forge = tactical_forge_adapter.resolve_build_attack(
            blocked_context, attribute=6, prof_bonus=2,
            target_type="standard")
        assert forge["status"] == "attack_blocked" \
            and forge["settlement"] is None, forge
    stale = tactical_context.build_context(
        battlefield, state, actor_id="u-hero", target_id="e-orc",
        expected_revision=99, distance_unit="cell")
    assert stale["status"] == "stale_context"
    forge = tactical_forge_adapter.resolve_build_attack(
        stale, attribute=6, prof_bonus=2, target_type="standard")
    assert forge["status"] == "context_rejected" \
        and forge["reasons"], forge

    # 动量：全骰皆负额外 +1（真实骰点、固定 seed）。
    all_fail_seed = None
    for seed in range(1, 3000):
        roll = prism_core.build_roll(2, base_need, sides=10,
                                     rng=random.Random(seed))
        if roll["all_fail"]:
            all_fail_seed = seed
            break
    assert all_fail_seed is not None
    forge = tactical_forge_adapter.resolve_build_attack(
        context, attribute=2, prof_bonus=0, target_type="standard",
        rng=random.Random(all_fail_seed))
    assert forge["settlement"]["roll"]["all_fail"] is True
    assert forge["settlement"]["roll"]["momentum"] \
        == prism_core.BUILD_OUTCOME_MOMENTUM["failure"] + 1

    # 超载连锁：成功数 ≥ 需求两倍且盈余 ≥3 → 触发；每场景限一次。
    chain = prism_core.build_overload_chain(8, 3)
    assert chain["triggered"] is True and chain["available"] is True
    assert len(chain["options"]) == 4
    assert prism_core.build_overload_chain(8, 3, used_this_scene=True)[
        "available"] is False
    assert prism_core.build_overload_chain(4, 3)["triggered"] is False

    # 应力（02B 第五节）：分段惩罚 + 上限崩溃（疲惫 3 层 / strain +1 / 回落）。
    session = prism_core.RuleSession("b14-stress")
    unit = prism_core.new_unit("s-1", "应压员",
                               attributes={"VIG": 6, "MND": 6})
    cap = prism_core.stress_cap(unit)
    assert cap == 6 + 6 + prism_core.STRESS_CAP_FLAT
    assert prism_core.stress_penalty(0, cap)["band"] == "0–49%"
    mid = prism_core.stress_penalty(int(cap * 0.6), cap)
    assert mid["pool_dice"] == -1 and mid["tolerance_need"] is None
    high = prism_core.stress_penalty(int(cap * 0.9), cap)
    assert high["pool_dice"] == -2 \
        and high["tolerance_need"] == prism_core.STRESS_TOLERANCE_NEED
    assert prism_core.stress_penalty(cap, cap)["collapse"] is True
    unit["stress"] = cap - 1
    out = prism_core.stress_gain(session, unit, 1)
    assert out["overload"], "达到上限必须立即崩溃"
    assert out["overload"]["fatigue_layers"] == 3
    assert out["overload"]["burden"] == 1
    assert unit["stress"] == cap // 2


# ── 六、AP 经济 / 遭遇预算 / 胜负终局 ───────────────────────────────────

def check_rules_economy_encounter_victory():
    """AP 与回合来自数据；遭遇预算真实约束；脚本化战斗打到胜利并持久化。"""
    with open(os.path.join(ROOT, "data", "system", "action_economy.json"),
              encoding="utf-8") as handle:
        economy = json.load(handle)
    assert svc.ap_per_turn() == economy["round"]["ap_per_turn"]
    costs = {row["id"]: row.get("ap") for row in economy["actions"]}
    for action_id in ("move", "basic_attack", "assist_action"):
        assert svc.ap_cost(action_id) == costs[action_id]
    # 遭遇预算：上限 = 人数 × (平均等级 + 2)，强度档折减（02A 第九、十节）。
    assert prism_core.encounter_budget(3, 1) == 3 * (1 + 2)
    party_size, avg_level = 3, 1
    total = prism_core.encounter_budget(party_size, avg_level)
    ratio = prism_core.ENCOUNTER_STRENGTH["entangled"]
    budget = -(-round(total * ratio * 100) // 100)
    record, session = make_record()
    live = fresh_live(session.snapshot())
    spent = 0
    for enemy_id in svc._enemy_ids(record):
        unit = (record["units"][enemy_id] or {}).get("unit") or {}
        tier = str(enemy_id).rsplit("-", 1)[0]
        spent += int(prism_core.ENEMY_TIERS[tier]["budget"])
        assert int(unit.get("xp") or 0) > 0
    assert spent <= budget, (spent, budget)
    # 脚本化战斗打到胜利：经验 / 压力 / 收尾事件 + 存档恢复。
    assert _drive_to_adjacent(record, live, svc._enemy_ids(record)[0]), \
        "应当能接敌"
    for _turn in range(200):
        if record["status"] != svc.STATUS_ACTIVE:
            break
        actor = record["battle_state"]["active_unit_id"]
        entry = svc._unit_entry(record, actor) or {}
        living = [uid for uid in svc._enemy_ids(record)
                  if _conscious(record, live, uid)]
        if entry.get("party") and living:
            target = min(living, key=lambda uid: (
                _hex_dist(record, actor, uid), uid))
            acted = False
            if _hex_dist(record, actor, target) <= 1:
                try:
                    svc.do_attack(record, live, actor_id=actor,
                                  target_id=target,
                                  expected_revision=record["battle_state"]
                                  ["state_revision"])
                    acted = True
                except svc.TacticalError:
                    acted = False
            if not acted:
                try:
                    svc.do_end_turn(record, live, actor_id=actor,
                                    expected_revision=record["battle_state"]
                                    ["state_revision"])
                except svc.TacticalError:
                    break
        else:
            break
    assert record["status"] == svc.STATUS_ENDED, record["outcome"]
    assert record["outcome"] == "victory", record["outcome"]
    types = [event["type"] for event in record["events"]]
    assert "combat_ended" in types and "attack_resolved" in types
    assert all(unit["xp"] > 0 for unit in live.party), "胜利要发经验"
    assert live.pressure == 1
    # 战斗结束后按记录恢复战场：同身份、不静默换图。
    assert svc.restore_battlefield(record) == record["battlefield"]


# ── 七、环境互动（物件状态机 / 危险 / 可移动 / 可拾取）──────────────────

def _apply_proposal_to_state(state, battlefield, proposal):
    """把 #421 已接受提案应用进最小 battle_state（与服务端同序）。"""
    for object_id, patch in (proposal.get("state_delta") or {}).items():
        for row in state["objects"]:
            if row["object_id"] == object_id:
                row.update(copy.deepcopy(patch))
    for delta in proposal.get("cell_deltas") or []:
        cell = tactical_battlefield.cell_by_id(battlefield,
                                               delta.get("cell_id"))
        if cell is None:
            continue
        for field, value in (delta.get("set") or {}).items():
            cell[field] = copy.deepcopy(value)
        for field in delta.get("unset") or ():
            cell.pop(field, None)
    for key in [proposal.get("idempotency_key")]:
        if key:
            state.setdefault("applied_keys", []).append(key)


def check_rules_object_interactions_full():
    """物件状态机全结案：机关 / 危险 / 可移动 / 可拾取 + 幂等。"""
    generated = battlefield_gen.generate_battlefield(
        _scene("w-modern", "place-modern"),
        _load_fixture("modern_future.json"), seed=11, mode="abstract")
    battlefield = generated["battlefield"]
    objects = generated["objects"]
    cells = sorted((cell for cell in battlefield["cells"]
                    if cell["surface_id"] == "deck"
                    and not cell["blocks_movement"]),
                   key=lambda cell: cell["cell_id"])
    state = _fixture_battle_state(
        battlefield, objects,
        (("u-hero", cells[0]["cell_id"]), ("e-orc", cells[1]["cell_id"])))
    state["battle_id"] = "battle-objects-b14"
    by_definition = {row["definition_id"]: row["object_id"]
                     for row in objects}

    def instance(object_id):
        for row in state["objects"]:
            if row["object_id"] == object_id:
                return row
        raise AssertionError(object_id)

    # 机关（能量屏障）：未知触发拒绝；合法转换打开占格。
    barrier = instance(by_definition["prop.energy-barrier"])
    proposal = tactical_objects.interact(
        state, battlefield, definitions=E2E_DEFINITIONS,
        object_id=barrier["object_id"], action="explode",
        actor_id="u-hero", rule_engine="tactics")
    assert not proposal["accepted"] \
        and proposal["reasons"][0]["code"] == "unknown_trigger", proposal
    proposal = tactical_objects.interact(
        state, battlefield, definitions=E2E_DEFINITIONS,
        object_id=barrier["object_id"], action="interact",
        actor_id="u-hero", rule_engine="tactics")
    assert proposal["accepted"] \
        and proposal["transition"] == {"from": "closed", "to": "open"}
    _apply_proposal_to_state(state, battlefield, proposal)
    assert instance(barrier["object_id"])["state"] == "open" \
        and instance(barrier["object_id"])["blocks_movement"] is False
    # 重复互动 → 状态机拒绝（无 open→open 的第二条转换）。
    proposal = tactical_objects.interact(
        state, battlefield, definitions=E2E_DEFINITIONS,
        object_id=barrier["object_id"], action="interact",
        actor_id="u-hero", rule_engine="tactics")
    assert not proposal["accepted"], proposal

    # 可破坏（燃料桶）：intact → destroyed，blocks_movement 解除。
    barrel = instance(by_definition["prop.fuel-barrel"])
    assert barrel["blocks_movement"] is True
    proposal = tactical_objects.interact(
        state, battlefield, definitions=E2E_DEFINITIONS,
        object_id=barrel["object_id"], action="interact",
        actor_id="u-hero", rule_engine="tactics")
    assert proposal["accepted"] \
        and proposal["transition"] == {"from": "intact", "to": "destroyed"}
    _apply_proposal_to_state(state, battlefield, proposal)
    assert instance(barrel["object_id"])["state"] == "destroyed" \
        and instance(barrel["object_id"])["blocks_movement"] is False

    # 可移动（货箱）：提案校验 footprint 与互斥占用后挪格。
    crate = instance(by_definition["prop.cargo-crate"])
    old_cell = crate["cell_id"]
    target_cell = next(cell["cell_id"] for cell in cells
                       if cell["cell_id"] not in (old_cell,)
                       and cell["cell_id"]
                       not in {row["cell_id"] for row in state["participants"]}
                       and cell["cell_id"] not in
                       {row["cell_id"] for row in state["objects"]})
    proposal = tactical_objects.move_object(
        state, battlefield, definitions=E2E_DEFINITIONS,
        object_id=crate["object_id"], to_cell_id=target_cell,
        actor_id="u-hero", rule_engine="tactics")
    assert proposal["accepted"], proposal.get("reasons")
    _apply_proposal_to_state(state, battlefield, proposal)
    assert instance(crate["object_id"])["cell_id"] == target_cell
    occupancy = tactical_objects.occupancy_map(state, battlefield)
    assert occupancy.get(target_cell) == crate["object_id"]
    assert old_cell not in occupancy or occupancy[old_cell] \
        != crate["object_id"]

    # 可拾取（数据芯片）：inventory_proposals 指向权威库存。
    chip = instance(by_definition["prop.data-chip"])
    proposal = tactical_objects.pickup(
        state, battlefield, definitions=E2E_DEFINITIONS,
        object_id=chip["object_id"], unit_id="u-hero", rule_engine="tactics")
    assert proposal["accepted"], proposal.get("reasons")
    inventory = proposal["inventory_proposals"][0]
    assert inventory["target"] == "authoritative_inventory"
    assert inventory["payload"] == {"item_refs": ["item-data-chip-01"]}
    _apply_proposal_to_state(state, battlefield, proposal)
    assert instance(chip["object_id"])["state"] == "collected"

    # 危险（电弧炮塔）：interact 相位一次性触发（#415 登记口径）；
    # 重复触发被 triggered 置位 + 状态机双重拒绝。
    turret = instance(by_definition["prop.arc-turret"])
    proposal = tactical_objects.trigger_hazard(
        state, battlefield, definitions=E2E_DEFINITIONS,
        unit_id="u-hero", cell_id=turret["cell_id"], phase="interact",
        rule_engine="tactics")
    assert proposal["accepted"], proposal.get("reasons")
    _apply_proposal_to_state(state, battlefield, proposal)
    assert instance(turret["object_id"])["state"] == "discharged" \
        and instance(turret["object_id"])["triggered"] is True
    proposal = tactical_objects.trigger_hazard(
        state, battlefield, definitions=E2E_DEFINITIONS,
        unit_id="u-hero", cell_id=turret["cell_id"], phase="interact",
        rule_engine="tactics")
    assert not proposal["accepted"] or proposal.get("duplicate") is True, \
        proposal


# ── 八、可追踪性与未映射能力 ────────────────────────────────────────────

def check_traceability_and_unmapped():
    """reasons / rule_ref / not_mapped / unsupported 显式可见；缺 sight 结构化失败。"""
    # 移动阻塞逐条带规则引用（#419）。
    record, _session = make_record()
    far = record["battlefield"]["cells"][-1]["cell_id"]
    live = fresh_live(_session.snapshot())
    actor = record["battle_state"]["active_unit_id"]
    try:
        svc.do_move(record, live, actor_id=actor, to_cell_id=far,
                    expected_revision=record["battle_state"]["state_revision"])
        raise AssertionError("远格应当不可达")
    except svc.TacticalError as exc:
        assert exc.reason == "destination_unreachable"
        blocked = exc.details.get("movement_blocked") or []
        assert blocked and all(entry.get("rule") for entry in blocked)
    # 射程拒绝带 02C:144 引用。
    enemy_id = svc._enemy_ids(record)[0]
    try:
        svc.do_attack(record, live, actor_id=actor, target_id=enemy_id,
                      expected_revision=record["battle_state"]["state_revision"])
        raise AssertionError("远距攻击应当被拒")
    except svc.TacticalError as exc:
        reasons = exc.details.get("reasons") or []
        assert reasons and reasons[0].get("rule_ref") \
            == "docs/system/02c-hex-battlefield.md:144", reasons
    # 适配器：战术机动 / 环境交互显式不支持；未知操作显式拒绝。
    for operation in ("maneuver", "interact"):
        out = tactical_tactics_adapter.run(operation)
        assert out["status"] == "unsupported" \
            and out["reasons"][0]["code"] == "unsupported_operation", out
        assert out["reasons"][0].get("rule_ref")
    out = tactical_tactics_adapter.run("no-such-op")
    assert out["status"] == "unsupported", out
    # 距离 / 高度差：两引擎均显式 not_mapped，不静默丢弃。
    context = tactical_context.build_context(
        record["battlefield"], record["battle_state"], actor_id=actor,
        target_id=enemy_id, distance_unit="cell")
    mapping = prism_core.map_context_for_d20(context)
    facts = {item["fact"] for item in mapping["not_mapped"]}
    assert {"hex_distance", "abstract_distance"} <= facts, mapping
    forge_mapping = prism_core.map_context_for_build(context)
    assert {item["fact"] for item in forge_mapping["not_mapped"]} >= {
        "hex_distance", "abstract_distance"}
    # 支持矩阵本身可序列化且声明完整（d20 / 骰池）。
    matrix = tactical_tactics_adapter.SUPPORT_MATRIX
    assert matrix["maneuver"]["support"] == "unsupported"
    assert matrix["attack"]["blocked_values"]["cover_grade"] == ["full"]
    assert any(entry["status"] == "unsupported"
               for entry in tactical_forge_adapter.forge_support_matrix())
    # 登记口径：sight 未落 → metric 视线结构化失败；abstract 可用。
    outdoor = battlefield_gen.generate_battlefield(
        _scene("w-outdoor", "place-outdoor"),
        _load_fixture("outdoor_hazard.json"), seed=13, mode="metric")
    metric_bf = outdoor["battlefield"]
    cells = sorted(metric_bf["cells"], key=lambda cell: cell["cell_id"])
    result = tactical_context.spatial_query(
        metric_bf, from_cell_id=cells[0]["cell_id"],
        to_cell_id=cells[1]["cell_id"])
    assert result["status"] == "invalid_context" \
        and result["reasons"], result
    assert "sight" in json.dumps(result["reasons"], ensure_ascii=False), \
        result["reasons"]
    indoor = battlefield_gen.generate_battlefield(
        _scene("w-indoor", "place-indoor"),
        _load_fixture("indoor_multilevel.json"), seed=13, mode="abstract")
    abstract_bf = indoor["battlefield"]
    cells = sorted(abstract_bf["cells"], key=lambda cell: cell["cell_id"])
    result = tactical_context.spatial_query(
        abstract_bf, from_cell_id=cells[0]["cell_id"],
        to_cell_id=cells[1]["cell_id"], distance_unit="cell")
    assert result["status"] == "ok", result
    assert result["rule_refs"], "空间事实必须带规则引用"


# ── 九、安全边界（HTTP，越权与伪造） ────────────────────────────────────

def check_security_boundaries_http():
    """伪造位置 / 单位 / 对象状态 / 越权操作都不得成为事实。"""
    with _Local() as http:
        sid = _new_session(http)
        _seed_party(http, sid)
        status, body = http.post("/api/tactical/start",
                                 {"strength": "entangled"},
                                 sid=sid,
                                 headers={"Idempotency-Key":
                                          "b14start00000003"})
        assert status == 200, (status, body)
        battle = body["battle"]
        party = _party_ids(battle)
        actor = battle["active_unit_id"]
        enemy = _enemy_ids_view(battle)[0]
        enemy_cell = _cell_of(battle, enemy)

        # 伪造敌体身份：单人会话回落到当前队伍行动者，敌体绝不因伪造而行动。
        revision, battle = _rev(http, sid)
        status, out = http.post(
            "/api/tactical/attack",
            {"unit_id": enemy, "target_id": enemy,
             "expected_revision": revision},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status in (200, 422), (status, out)
        revision, battle = _rev(http, sid)
        assert _cell_of(battle, enemy) == enemy_cell, "敌体不得被伪造操作移动"
        events = [event for event in battle["recent_events"]
                  if event.get("actor_id") == enemy]
        assert not events, "敌体不得成为伪造行动的 actor"

        # 伪造未轮到的队友身份：409 not_your_turn（回合权服务端裁定）。
        others = [uid for uid in party if uid != actor]
        if others:
            revision, battle = _rev(http, sid)
            status, out = http.post(
                "/api/tactical/move",
                {"unit_id": others[0],
                 "to_cell_id": _cell_of(battle, actor),
                 "expected_revision": revision},
                sid=sid, headers={"Idempotency-Key": _next_key()})
            assert status == 409 and out.get("reason") == "not_your_turn", \
                (status, out)

        # 自攻击拒绝。
        revision, battle = _rev(http, sid)
        status, out = http.post(
            "/api/tactical/attack",
            {"target_id": battle["active_unit_id"],
             "expected_revision": revision},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status == 422 and out.get("reason") == "bad_target", (status, out)

        # 伪造对象状态：隔空互动被射程拒绝；payload 里的 state 字段被忽略。
        gate = next(row for row in battle["objects"]
                    if row["definition_id"] == "mech.blast-gate")
        status, out = http.post(
            "/api/tactical/interact",
            {"object_id": gate["object_id"], "action": "interact",
             "state": "open", "blocks_movement": False,
             "expected_revision": battle["state_revision"]},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status == 422 \
            and out.get("reason") == "object_out_of_range", (status, out)
        revision, battle = _rev(http, sid)
        gate_now = next(row for row in battle["objects"]
                        if row["object_id"] == gate["object_id"])
        assert gate_now["state"] == gate["state"], "伪造状态不得生效"

        # 未知互动动作 → 状态机拒绝（不猜测语义）。
        status, out = http.post(
            "/api/tactical/interact",
            {"object_id": gate["object_id"], "action": "teleport",
             "expected_revision": battle["state_revision"]},
            sid=sid, headers={"Idempotency-Key": _next_key()})
        assert status in (422, 409), (status, out)

        # 伪造移动结果字段：path / total_cost 由服务端重算，伪造值被忽略。
        revision, battle = _rev(http, sid)
        actor = battle["active_unit_id"]
        step = _http_step_from_view(battle, actor,
                                    _cell_of(battle, _enemy_ids_view(battle)[0]))
        if step:
            status, out = http.post(
                "/api/tactical/move",
                {"to_cell_id": step, "expected_revision": revision,
                 "path": ["cell-forged-01"], "total_cost": -1,
                 "falls": [{"height_cm": 99999}]},
                sid=sid, headers={"Idempotency-Key": _next_key()})
            assert status == 200, (status, out)
            dumped = json.dumps(out, ensure_ascii=False)
            assert "cell-forged-01" not in dumped and "99999" not in dumped
            moved = out["battle"]["participants"]
            path_event = [event for event in out["battle"]["recent_events"]
                          if event["type"] == "move_committed"][-1]
            assert path_event["result"]["path"], path_event
            assert path_event["result"]["path"][-1] == step


# ── 运行器 ─────────────────────────────────────────────────────────────

def main():
    checks = (
        check_fixtures_three_battlefields,
        check_fixture_world_id_neutrality,
        check_e2e_full_battle_http,
        check_e2e_restore_and_legacy_saves,
        check_e2e_deterministic_replay,
        check_rules_d20_settlement,
        check_rules_d20_cover_and_high_ground,
        check_rules_build_settlement,
        check_rules_economy_encounter_victory,
        check_rules_object_interactions_full,
        check_traceability_and_unmapped,
        check_security_boundaries_http,
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
