#!/usr/bin/env python3
"""统一 TacticalContext：单一结构化空间事实对象（Issue #422，M13-B7）。

为服务端战斗、两套规则引擎（PRISM 战术 / 构建）、前端预览与敌人 AI 提供**一份**
聚合的空间事实（02C 第六节「双引擎适配契约」），杜绝各系统重复计算格距 / 高地 / 视线。

职责边界：

- **聚合层，不重写算法**：距离 / LOS 只走 #444 `tactical_queries.query_attack`，
  可达集 / 路径成本只走 #419 `tactical_movement.plan_move`，占格互斥只走
  #421 `tactical_objects`，格身份与口径访问只走 #417 `tactical_battlefield`。
  本模块没有第二套几何或寻路内核（02C:172）；
- **空间事实与规则解释分离**：不掷骰、不支付 AP、不结算伤害、不提交状态，
  也不为 d20 / 骰池引擎转换任何加值（02C:156 之后的固定管线第 4 步由适配器做）；
- **单一 revision**：每次上下文基于 `battle_state.state_revision` 的**一个**快照计算；
  `expected_revision` 不匹配时返回 `stale_context` 结构化结果，**不**把新旧状态
  拼成一个结果（02C:61）；
- **错误也是结构化对象**：未知参与者 / 未知格 / 缺 sight 配置 / 不可达都返回
  `status` + `reasons[]`，不抛异常打断消费方（#444 的失败行为原样透传进 reasons，
  不静默兜底——缺 sight 仍是显式失败，02C:267）；
- **口径隔离**：metric 只给 `metric_distance`（厘米，#444 的三维格中心距离），
  abstract 只给 `abstract_distance`（格 / 步），`signed_height_delta` 的单位由
  `height_delta_unit`（`cm` / `level`）显式声明，二者绝不互算（02C:115 / 02C:117 / 02C:39）；
- **JSON 可序列化**：输出全部是 str / int / float / bool / None / list / dict，
  同输入同输出（键序固定，见 `CONTEXT_FIELDS` / `SPATIAL_FIELDS`，契约测试冻结）；
- **不强制造图**：没有 tactical battlefield 的旧场景继续用旧 ATLAS 抽象查询；
  本模块不生成地图、不 import `atlas*` / `prism_core` / `notdnd_web`，无 HTTP。

字段清单（冻结，接口字段变更须先同步后续 adapter / API Issue，不得悄悄改名）：

- `CONTEXT_FIELDS`：TacticalContext 顶层字段（02C:156 至少字段全集 + 聚合视图）；
- `SPATIAL_FIELDS`：SpatialQueryResult 字段（actor → target 一对空间事实）。

朝向沿用 #444 既有纯函数口径：六值枚举、缺省 `n`（尖顶类基准式，02C:232）。
"""
import tactical_battlefield as battlefield_model
import tactical_movement
import tactical_objects
import tactical_queries

__all__ = [
    "CONTEXT_SCHEMA_VERSION",
    "CONTEXT_KIND",
    "SPATIAL_QUERY_KIND",
    "CONTEXT_FIELDS",
    "SPATIAL_FIELDS",
    "CONTEXT_STATUSES",
    "build_context",
    "spatial_query",
]

CONTEXT_SCHEMA_VERSION = 1
CONTEXT_KIND = "tactical.context"
SPATIAL_QUERY_KIND = "tactical.spatial_query_result"

# status 受控枚举：完整事实 / 过时（revision 不匹配）/ 无效（结构化错误）。
CONTEXT_STATUSES = ("ok", "stale_context", "invalid_context")

# TacticalContext 顶层字段（键序固定；契约测试逐项冻结，02C:156 + 聚合视图）。
CONTEXT_FIELDS = (
    "schema_version",
    "kind",
    "status",
    "battle_id",
    "battlefield_id",
    "state_revision",
    "actor_id",
    "target_id",
    "from_cell_id",
    "to_cell_id",
    "scale_mode",
    "hex_distance",
    "metric_distance",
    "abstract_distance",
    "distance_unit",
    "signed_height_delta",
    "height_delta_unit",
    "line_of_sight",
    "blocked_cells",
    "blocked_objects",
    "cover_grade",
    "high_ground",
    "reachable",
    "path",
    "path_cost",
    "movement_blocked",
    "terrain_tags",
    "active_feature_refs",
    "objects",
    "reasons",
    "rule_refs",
)

# SpatialQueryResult 字段（actor → target 一对空间事实，键序固定）。
SPATIAL_FIELDS = (
    "schema_version",
    "kind",
    "status",
    "battlefield_id",
    "actor_id",
    "target_id",
    "from_cell_id",
    "to_cell_id",
    "scale_mode",
    "hex_distance",
    "metric_distance",
    "abstract_distance",
    "distance_unit",
    "signed_height_delta",
    "height_delta_unit",
    "line_of_sight",
    "blocked_cells",
    "cover_grade",
    "high_ground",
    "executable",
    "reasons",
    "rule_refs",
)

# 文档行号引用（不是规则数值）。
_RULE_REFS = {
    "context_fields": "docs/system/02c-hex-battlefield.md:156",
    "adapter_contract": "docs/system/02c-hex-battlefield.md:172",
    "cell_id_identity": "docs/system/02c-hex-battlefield.md:21",
    "revision": "docs/system/02c-hex-battlefield.md:61",
    "occupancy": "docs/system/02c-hex-battlefield.md:65",
    "metric_distance": "docs/system/02c-hex-battlefield.md:115",
    "abstract_distance": "docs/system/02c-hex-battlefield.md:117",
    "line_of_sight": "docs/system/02c-hex-battlefield.md:121",
    "high_ground": "docs/system/02c-hex-battlefield.md:78",
    "cover_grades": "docs/system/02c-hex-battlefield.md:138",
    "movement_budget": "docs/system/02c-hex-battlefield.md:90",
}


def _reason(code, message, **extra):
    item = {"code": code, "message": message}
    item.update(extra)
    return item


def _ordered(mapping, fields):
    """按冻结键序重组；缺失键补 `None`，保证输出形状稳定且可 JSON 序列化。"""
    return {key: mapping.get(key) for key in fields}


def _empty_facts():
    """空间事实的空值形状（错误 / 过时结果同样携带全部字段，值为 None / 空表）。"""
    return {
        "scale_mode": None,
        "hex_distance": None,
        "metric_distance": None,
        "abstract_distance": None,
        "distance_unit": None,
        "signed_height_delta": None,
        "height_delta_unit": None,
        "line_of_sight": None,
        "blocked_cells": [],
        "cover_grade": None,
        "high_ground": None,
    }


def _spatial_envelope(battlefield_id, actor_id, target_id, from_cell_id, to_cell_id):
    data = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "kind": SPATIAL_QUERY_KIND,
        "status": "invalid_context",
        "battlefield_id": battlefield_id,
        "actor_id": actor_id,
        "target_id": target_id,
        "from_cell_id": from_cell_id,
        "to_cell_id": to_cell_id,
        "executable": None,
        "reasons": [],
        "rule_refs": dict(_RULE_REFS),
    }
    data.update(_empty_facts())
    return _ordered(data, SPATIAL_FIELDS)


def _map_query_facts(query):
    """把 #444 的查询结果映射到冻结字段；metric / abstract 各取各的口径，不互算。"""
    mode = query["scale_mode"]
    if mode == "metric":
        metric_distance = query["distance"]
        abstract_distance = None
        signed_delta = query["signed_height_delta_cm"]
        height_unit = "cm"
    else:
        metric_distance = None
        abstract_distance = query["distance"]
        signed_delta = query["signed_level_delta"]
        height_unit = "level"
    return {
        "scale_mode": mode,
        "hex_distance": query["hex_distance"],
        "metric_distance": metric_distance,
        "abstract_distance": abstract_distance,
        "distance_unit": query["distance_unit"],
        "signed_height_delta": signed_delta,
        "height_delta_unit": height_unit,
        "line_of_sight": query["line_of_sight"],
        "blocked_cells": list(query["blocked_cells"]),
        "cover_grade": query["cover_grade"],
        "high_ground": query["high_ground"],
    }


def _run_query(battlefield, from_cell_id, to_cell_id, *, actor_body_size,
               target_body_size, orientation, distance_unit, rules):
    """调 #444 内核；失败（含缺 sight 配置）原样转成结构化原因，不静默兜底。"""
    try:
        return tactical_queries.query_attack(
            battlefield, from_cell_id, to_cell_id,
            actor_body_size=actor_body_size,
            target_body_size=target_body_size,
            orientation=orientation,
            distance_unit=distance_unit,
            rules=rules,
        ), None
    except (tactical_queries.QueryError, ValueError) as error:
        return None, _reason(
            "query_failed",
            "空间查询失败（#444 既有失败行为透传）: %s" % (error,),
        )


def spatial_query(battlefield, *, from_cell_id, to_cell_id, actor_id=None,
                  target_id=None, actor_body_size=None, target_body_size=None,
                  orientation=None, distance_unit=None, rules=None):
    """查一对 cell_id 的空间事实，返回版本化 `SpatialQueryResult`。

    - `battlefield`：已加载的静态战场（`tactical.battlefield`，只读）；
    - `from_cell_id` / `to_cell_id`：权威定位（02C:21），内部全部走 #444；
    - `actor_id` / `target_id`：调用方的实体身份标签（可选，仅进结果便于追溯）；
    - `orientation` / `distance_unit` / 体型档与 `rules` 语义同 #444；
    - 失败（未知格、缺 sight 配置、口径声明不符等）返回
      `status="invalid_context"` + `reasons[]` 的结构化对象，不抛异常；
    - 同输入同输出，键序按 `SPATIAL_FIELDS` 冻结。
    """
    battlefield_id = battlefield.get("battlefield_id") if isinstance(battlefield, dict) else None
    result = _spatial_envelope(
        battlefield_id, actor_id, target_id, from_cell_id, to_cell_id)
    if not isinstance(battlefield, dict):
        result["reasons"] = [_reason(
            "invalid_battlefield",
            "battlefield 必须是已加载的战场 dict（实为 %r）" % (type(battlefield).__name__,),
        )]
        return result
    query, failure = _run_query(
        battlefield, from_cell_id, to_cell_id,
        actor_body_size=actor_body_size,
        target_body_size=target_body_size,
        orientation=orientation,
        distance_unit=distance_unit,
        rules=rules,
    )
    if failure is not None:
        result["reasons"] = [failure]
        return result
    result.update(_map_query_facts(query))
    result["status"] = "ok"
    # #444 的 failure_reasons（视线被遮 / 跨 surface / full 掩体）原样进入 reasons。
    result["reasons"] = list(query["failure_reasons"])
    result["executable"] = query["executable"]
    return result


def _find_participant(battle_state, unit_id):
    for participant in battle_state.get("participants") or []:
        if isinstance(participant, dict) and participant.get("unit_id") == unit_id:
            return participant
    return None


def _objects_view(battle_state):
    """物件实例的只读视图，按 object_id 排序（确定性），不改状态。"""
    instances = [item for item in battle_state.get("objects") or []
                 if isinstance(item, dict)]
    view = []
    for instance in sorted(instances, key=lambda item: str(item.get("object_id"))):
        footprint = instance.get("footprint")
        view.append({
            "object_id": instance.get("object_id"),
            "definition_id": instance.get("definition_id"),
            "state": instance.get("state"),
            "cell_id": instance.get("cell_id"),
            "footprint": [dict(item) for item in footprint]
                         if isinstance(footprint, list) else None,
            "blocks_movement": instance.get("blocks_movement"),
            "blocks_los": instance.get("blocks_los"),
            "triggered": instance.get("triggered"),
        })
    return view


def _blocked_objects(battle_state, battlefield, blocked_cells):
    """footprint 覆盖任一遮挡格的物件（只做覆盖关系聚合，不新增几何判定）。

    物件的 LOS / 阻挡效果由服务端把 `cell_deltas` 应用到战场后经 `blocked_cells`
    体现；这里只把「哪个物件盖在遮挡格上」讲清楚。
    """
    blocked = set(blocked_cells)
    if not blocked:
        return []
    instances = [item for item in battle_state.get("objects") or []
                 if isinstance(item, dict)]
    result = []
    for instance in sorted(instances, key=lambda item: str(item.get("object_id"))):
        cells = tactical_objects.object_cells(battlefield, instance) or []
        covered = sorted({cell.get("cell_id") for cell in cells
                          if cell.get("cell_id") in blocked})
        if covered:
            result.append({
                "object_id": instance.get("object_id"),
                "cell_ids": covered,
                "reason": "covers_blocked_cell",
            })
    return result


def _active_feature_refs(battlefield, from_cell, to_cell, blocked_cells):
    """相关格（起点 / 终点 / 遮挡格）的 feature_refs 并集，排序去重。"""
    cells = [from_cell]
    if to_cell is not None:
        cells.append(to_cell)
    for cell_id in blocked_cells:
        cell = battlefield_model.cell_by_id(battlefield, cell_id)
        if cell is not None:
            cells.append(cell)
    return sorted({ref for cell in cells
                   for ref in (cell.get("feature_refs") or [])})


def _invalid_context(context, reasons):
    """结构化错误结果：全部事实字段保持空值，不带任何半成品事实。"""
    context["status"] = "invalid_context"
    context["reasons"] = list(reasons)
    return context


def build_context(battlefield, battle_state, *, actor_id, target_id=None,
                  expected_revision=None, actor_body_size=None,
                  target_body_size=None, orientation=None, distance_unit=None,
                  movement_budget=None, movement_destination=None,
                  movement_allowed_modes=(), movement_capabilities=(),
                  rules=None):
    """基于**单一** `battle_state` 快照构建版本化 TacticalContext。

    参数：

    - `battlefield`：已加载的静态战场（`tactical.battlefield`，只读；没有战术地图的
      旧场景**不要**调用本函数，继续使用旧 ATLAS 抽象查询——本模块不强制造图）；
    - `battle_state`：运行时战斗状态（`tactical.battle_state`，只读）；
    - `actor_id` / `target_id`：`battle_state.participants` 里的 `unit_id`
      （`target_id` 可省——省略时不产出距离 / LOS 事实，移动仍可只算可达集）；
    - `expected_revision`：调用方持有的修订号；与 `battle_state.state_revision`
      不一致时返回 `status="stale_context"`（全部事实字段为空，不拼新旧状态）；
      `None` 表示主动放弃并发检查（单人脚本 / 回放，口径同 #419 `revision_matches`）；
    - `actor_body_size` / `target_body_size`：体型五档，metric 视线查询必传（#444）；
    - `orientation`：六朝向，缺省 `n`（#444 既有口径，02C:232）；
    - `distance_unit`：abstract 战场必须由能力声明格 / 步口径（`cell` / `step`），
      metric 战场必须不给（#444）；
    - `movement_budget`：移动预算（metric 厘米 / abstract 格步）；`None` 表示本次
      不聚合移动视图（`reachable` / `path` 为空表）；
    - `movement_destination`：可达性检查的目的地 `cell_id`（可选）。**不**默认指向
      目标所在格——实心占格互斥（02C:65）下目标格总被目标占用；要检查「能否抵达
      某格」时由调用方显式给出；
    - `movement_allowed_modes` / `movement_capabilities`：同 #419 `plan_move`；
    - `rules`：`system.tactical_space`，缺省读 `space_rules()`（测试可注入）。

    返回 dict（键序按 `CONTEXT_FIELDS` 冻结）：

    - `status`：`ok` / `stale_context` / `invalid_context`（`CONTEXT_STATUSES`）；
    - 空间事实字段、移动视图（`reachable` / `path` / `path_cost` / `movement_blocked`）、
      物件视图（`objects` / `blocked_objects`）、地形与特征引用，以及可解释
      `reasons[]`（#444 的 failure_reasons 原样并入，移动不可达追加
      `destination_unreachable`）。

    纯聚合：不掷骰、不扣 AP、不结算伤害、不提交状态（02C:156 管线第 4 步起
    由规则适配器负责）。
    """
    state = battle_state if isinstance(battle_state, dict) else {}
    current_revision = state.get("state_revision", 0)
    if not isinstance(current_revision, int) or isinstance(current_revision, bool):
        current_revision = 0  # 口径同 #421 `_revision`：非法值按 0 处理，不静默升位。

    data = {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "kind": CONTEXT_KIND,
        "status": "ok",
        "battle_id": state.get("battle_id"),
        "battlefield_id": state.get("battlefield_id"),
        "state_revision": current_revision,
        "actor_id": actor_id,
        "target_id": target_id,
        "from_cell_id": None,
        "to_cell_id": None,
        "reachable": [],
        "path": [],
        "path_cost": None,
        "movement_blocked": [],
        "blocked_objects": [],
        "objects": [],
        "terrain_tags": [],
        "active_feature_refs": [],
        "reasons": [],
        "rule_refs": dict(_RULE_REFS),
    }
    data.update(_empty_facts())

    # ① revision 前置：不匹配就整体作废，绝不把新旧状态拼成一个结果（02C:61）。
    if expected_revision is not None and expected_revision != current_revision:
        data["status"] = "stale_context"
        data["reasons"] = [_reason(
            "stale_context",
            "expected_revision=%r 与 battle_state.state_revision=%r 不一致"
            "（上下文整体作废，不产出任何空间事实）" % (expected_revision, current_revision),
            expected_revision=expected_revision,
            state_revision=current_revision,
            rule_ref=_RULE_REFS["revision"],
        )]
        return _ordered(data, CONTEXT_FIELDS)

    # ② 战场与参与方解析（结构化错误，不抛异常）。
    if not isinstance(battlefield, dict):
        return _ordered(_invalid_context(data, [_reason(
            "invalid_battlefield",
            "battlefield 必须是已加载的战场 dict（实为 %r）；没有战术地图的旧场景"
            "应继续使用旧 ATLAS 抽象查询" % (type(battlefield).__name__,),
        )]), CONTEXT_FIELDS)
    if "battlefield_id" not in data or data["battlefield_id"] is None:
        data["battlefield_id"] = battlefield.get("battlefield_id")

    actor = _find_participant(state, actor_id)
    if actor is None:
        return _ordered(_invalid_context(data, [_reason(
            "unknown_actor",
            "battle_state.participants 里没有 unit_id=%r" % (actor_id,),
            actor_id=actor_id,
        )]), CONTEXT_FIELDS)
    from_cell_id = actor.get("cell_id")
    data["from_cell_id"] = from_cell_id

    to_cell_id = None
    to_cell = None
    if target_id is not None:
        target = _find_participant(state, target_id)
        if target is None:
            return _ordered(_invalid_context(data, [_reason(
                "unknown_target",
                "battle_state.participants 里没有 unit_id=%r" % (target_id,),
                target_id=target_id,
            )]), CONTEXT_FIELDS)
        to_cell_id = target.get("cell_id")
        data["to_cell_id"] = to_cell_id

    from_cell = battlefield_model.cell_by_id(battlefield, from_cell_id)
    if from_cell is None:
        return _ordered(_invalid_context(data, [_reason(
            "unknown_cell",
            "actor 的 cell_id 不在本战场：%r" % (from_cell_id,),
            actor_id=actor_id,
            cell_id=from_cell_id,
            rule_ref=_RULE_REFS["cell_id_identity"],
        )]), CONTEXT_FIELDS)
    if to_cell_id is not None:
        to_cell = battlefield_model.cell_by_id(battlefield, to_cell_id)
        if to_cell is None:
            return _ordered(_invalid_context(data, [_reason(
                "unknown_cell",
                "target 的 cell_id 不在本战场：%r" % (to_cell_id,),
                target_id=target_id,
                cell_id=to_cell_id,
                rule_ref=_RULE_REFS["cell_id_identity"],
            )]), CONTEXT_FIELDS)

    data["scale_mode"] = battlefield_model.scale_mode(battlefield)

    # ③ 空间事实：全部走 #444（缺 sight / 跨 surface 等失败行为原样透传）。
    if to_cell_id is not None:
        query, failure = _run_query(
            battlefield, from_cell_id, to_cell_id,
            actor_body_size=actor_body_size,
            target_body_size=target_body_size,
            orientation=orientation,
            distance_unit=distance_unit,
            rules=rules,
        )
        if failure is not None:
            return _ordered(_invalid_context(data, [failure]), CONTEXT_FIELDS)
        data.update(_map_query_facts(query))
        data["reasons"] = list(query["failure_reasons"])
    else:
        data["blocked_cells"] = []

    # ④ 移动视图：全部走 #419（占格互斥用 #421 的 occupancy_map，排除自身）。
    if movement_budget is not None:
        occupied = tactical_objects.occupancy_map(state, battlefield,
                                                  exclude=[actor_id])
        footprint = actor.get("footprint")
        if not isinstance(footprint, list) or not footprint:
            footprint = [{"q": 0, "r": 0}]
        try:
            move = tactical_movement.plan_move(
                battlefield, actor_id, from_cell_id, footprint, movement_budget,
                allowed_modes=movement_allowed_modes,
                capabilities=movement_capabilities,
                occupied=occupied,
                destination=movement_destination,
                rules=rules,
            )
        except ValueError as error:
            return _ordered(_invalid_context(data, [_reason(
                "movement_failed",
                "移动规划失败（#419 既有失败行为透传）: %s" % (error,),
            )]), CONTEXT_FIELDS)
        data["reachable"] = move["reachable"]
        data["path"] = move["path"]
        data["path_cost"] = move["total_cost"]
        data["movement_blocked"] = move["blocked"]
        if movement_destination is not None and move["total_cost"] is None:
            data["reasons"].append(_reason(
                "destination_unreachable",
                "%s 在预算与地形 / 高度 / 授权约束下不可达（详见 movement_blocked）"
                % (movement_destination,),
                cell_id=movement_destination,
                details=list(move["blocked"]),
            ))

    # ⑤ 物件视图与特征引用（只读聚合，不改任何状态）。
    data["objects"] = _objects_view(state)
    data["blocked_objects"] = _blocked_objects(state, battlefield,
                                               data["blocked_cells"])
    data["terrain_tags"] = list(from_cell.get("terrain_tags") or [])
    data["active_feature_refs"] = _active_feature_refs(
        battlefield, from_cell, to_cell, data["blocked_cells"])

    return _ordered(data, CONTEXT_FIELDS)
