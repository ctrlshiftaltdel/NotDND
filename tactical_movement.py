#!/usr/bin/env python3
"""六角格战术移动：路径成本、可达集与坠落提案（Issue #419）。

职责边界：

- **纯计算**：不写会话、不读 HTTP、不 import `prism_core` / `notdnd_web` / `atlas*`；
  调用方拿到结果后自行决定是否提交状态或扣预算；
- **阈值只读规则数据**：坡面上限、坠落档位、骰型全部来自
  `data/system/tactical_space.json`（`tactical_battlefield.space_rules()`），
  模块里没有把这些默认值抄成字面量；
- **口径隔离**：metric 以厘米计成本与高差，abstract 以格 / 步计，二者各自独立，
  本模块没有 `cm_to_cells` 之类的跨口径换算；
- **坠落只提案**：坠落在结果里是结构化提案（高差、档数、骰型、倒地、规则引用），
  不扣活力 / AP、不改角色状态；伤害由 #426 经 PRISM 伤害入口结算。

数据契约见 `docs/system/02c-hex-battlefield.md` 第三节（占格不变式与状态权威）、
第四节（高度 / 坡面 / 坠落），以及 `data/schema/tactical.battlefield.schema.json`。

主要入口是 `plan_move()`：在已加载的战场（`tactical_battlefield.load_battlefield`）
上做确定性 Dijkstra，返回 `reachable` / `path` / `total_cost` / `blocked` / `falls`。
"""
import heapq
import re

import tactical_battlefield
import tactical_hex

# 文档行号引用（不是规则数值）：用于 `blocked[].rule` 的逐条溯源。
CELL_FIELD_RULE = "docs/system/02c-hex-battlefield.md:41"
OCCUPANCY_RULE = "docs/system/02c-hex-battlefield.md:65"
LINK_KIND_RULE = "docs/system/02c-hex-battlefield.md:45"
BUDGET_RULE = "docs/system/02c-hex-battlefield.md:90"

# `docs/system/02c-hex-battlefield.md:94` 要求：超过连续坡面高差上限时，普通走路必须
# 有可通行的 vertical_link；「梯子 / 攀爬 / 跳跃 / 飞行必须由能力授权」。数据文件只
# 列出 kind 枚举，没有给「哪些 kind 属于普通走路」的分类，所以这里显式声明免授权的
# 被动通路：楼梯 / 坡道 / 电梯（文档同句列举的三种）。其余 kind 一律要求调用方在
# `allowed_modes` 里授权，不会被普通寻路当成免费通路。
PASSIVE_LINK_KINDS = ("stairs", "ramp", "lift")

# abstract 口径的普通走路高差上限：相邻格高差不超过 1 个 elevation_level。
# `tactical_space.json` 的 `movement` 只定义了 metric 的 `slope_max_cm`，没有单列
# abstract 阈值；abstract 的高度单位是 elevation_level（02C:78 高地阈值同样以
# 1 层为单位），这里取 ±1 层为可走坡面，且**不**由 metric 阈值换算得来
# （02C:39 禁止跨口径换算）。
ABSTRACT_SLOPE_MAX_LEVEL = 1

FALL_NOTE = "仅提案：伤害由 #426 经 PRISM 结算，寻路不扣活力 / AP、不改角色状态"
ABSTRACT_FALL_NOTE = "abstract 口径未在规则数据中定义坠落伤害档，不得用厘米口径代替（02C:39）"

_DICE_PATTERN = re.compile(r"^([0-9]+)d([0-9]+)$")

__all__ = [
    "PASSIVE_LINK_KINDS",
    "ABSTRACT_SLOPE_MAX_LEVEL",
    "fall_proposal",
    "revision_matches",
    "plan_move",
]


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _as_set(value, label):
    """把授权模式 / 能力归一成 frozenset；字符串按单个值处理，避免被拆成字符。"""
    if value is None:
        return frozenset()
    if isinstance(value, str):
        return frozenset((value,))
    try:
        return frozenset(value)
    except TypeError:
        raise ValueError("%s 必须是字符串集合（实为 %r）" % (label, type(value).__name__))


def _normalize_footprint(footprint):
    """把 footprint（相对 origin 的轴向偏移）归一为 `((dq, dr), …)`。"""
    if footprint is None:
        raise ValueError("footprint 不能为空（至少包含原点偏移 {q:0, r:0}）")
    try:
        offsets = tuple(tactical_hex.axial(offset) for offset in footprint)
    except (TypeError, ValueError):
        raise ValueError("footprint 必须是轴向偏移列表（每项含整数 q / r）: %r" % (footprint,))
    if not offsets:
        raise ValueError("footprint 不能为空（至少包含原点偏移 {q:0, r:0}）")
    return offsets


def _index(battlefield):
    """按 cell_id 与 `(surface_id, q, r)` 建索引，排序语义与 `cell_at` 一致。

    `tactical_battlefield.cell_at` 在同一 surface 出现重号坐标时取排序后的第一格；
    这里用同样的 `(surface_id, cell_id)` 排序后 `setdefault`，保证寻路结果不随
    `cells` 数组顺序变化。
    """
    cells = {}
    by_coord = {}
    ordered = sorted(
        (cell for cell in battlefield.get("cells") or [] if isinstance(cell, dict)),
        key=lambda cell: (str(cell.get("surface_id")), str(cell.get("cell_id"))),
    )
    for cell in ordered:
        cells.setdefault(cell["cell_id"], cell)
        by_coord.setdefault((cell.get("surface_id"), cell.get("q"), cell.get("r")), cell)
    return cells, by_coord


def _links_by_cell(battlefield):
    """`cell_id -> [(link, other_cell_id), …]`，按 `(link_id, other_cell_id)` 排序。

    `bidirectional` 为真时双向可走；`fall` 例外——坠落只能从高往低，反向不是可授权
    的坠落通路，因此只保留 `from_cell_id → to_cell_id` 一个方向。
    """
    links = {}
    for link in battlefield.get("vertical_links") or []:
        if not isinstance(link, dict):
            continue
        source = link["from_cell_id"]
        target = link["to_cell_id"]
        links.setdefault(source, []).append((link, target))
        if link.get("bidirectional") and link.get("kind") != "fall":
            links.setdefault(target, []).append((link, source))
    for entries in links.values():
        entries.sort(key=lambda item: (str(item[0].get("link_id")), str(item[1])))
    return links


def _height_gap(battlefield, source, target, mode):
    """目标高度 − 起点高度（有符号，02C 第四节）；两个口径各走各的访问器。"""
    if mode == "metric":
        return (tactical_battlefield.metric_elevation_cm(battlefield, target)
                - tactical_battlefield.metric_elevation_cm(battlefield, source))
    return (tactical_battlefield.abstract_elevation_level(battlefield, target)
            - tactical_battlefield.abstract_elevation_level(battlefield, source))


def _walk_gap_ok(gap, mode, rules):
    """普通走路的高差上限：metric 读数据，abstract 按层级。"""
    if mode == "metric":
        return abs(gap) <= rules["movement"]["slope_max_cm"]
    return abs(gap) <= ABSTRACT_SLOPE_MAX_LEVEL


def _placement_errors(battlefield, unit_id, target_cell_id, offsets, occupied):
    """复用 #417 的占格校验（footprint / blocks_movement / 互斥占用）。"""
    return tactical_battlefield.validate_placement(
        battlefield, unit_id, target_cell_id, offsets, occupied)


def _blocked_records(battlefield, unit_id, target, offsets, occupied):
    """把 #417 的占格错误转成阻塞记录；一个错误一条，便于逐条解释。"""
    records = []
    for error in _placement_errors(battlefield, unit_id, target["cell_id"], offsets, occupied):
        code = error["code"]
        rule = OCCUPANCY_RULE if code == "occupied" else CELL_FIELD_RULE
        records.append(_blocked_record(target, code, error["message"], rule))
    return records


def _blocked_record(target, code, message, rule):
    return {
        "code": code,
        "cell_id": target.get("cell_id"),
        "q": target.get("q"),
        "r": target.get("r"),
        "message": message,
        "rule": rule,
    }


def _fall_proposal_metric(height_cm, rules):
    """metric 坠落提案（纯函数，不触碰角色状态）。"""
    fall = rules["fall"]
    bands = 0 if height_cm < fall["no_damage_below_cm"] else -(-height_cm // fall["step_cm"])
    match = _DICE_PATTERN.match(str(fall["dice"]))
    if match is None:
        raise ValueError("fall.dice 必须是 NdM 形式（实为 %r）" % (fall["dice"],))
    count = int(match.group(1)) * bands
    sides = int(match.group(2))
    return {
        "height_cm": height_cm,
        "height_levels": None,
        "bands": bands,
        "dice": None if bands == 0 else {
            "count": count,
            "sides": sides,
            "expression": "%dd%d" % (count, sides),
        },
        "prone": bool(fall["prone"]) if bands else False,
        "no_damage_below_cm": fall["no_damage_below_cm"],
        "step_cm": fall["step_cm"],
        "rule": fall["damage_rule_source"],
        "threshold_rule": fall["source"],
        "proposal_only": True,
        "note": FALL_NOTE,
    }


def fall_proposal(height_cm, rules=None):
    """把 metric 坠落高度换算成结构化提案（只描述，不结算）。

    `height_cm` 是有符号高差：必须为正（坠落）；小于 `fall.no_damage_below_cm`
    时不产生伤害档，达到阈值后每 `fall.step_cm` 一档（不足一档向上取整），
    每档 `fall.dice` 颗骰并使单位倒地。全部数值来自规则数据。
    """
    if not _is_int(height_cm):
        raise ValueError("height_cm 必须是整数厘米（实为 %r）" % (height_cm,))
    if height_cm <= 0:
        raise ValueError("height_cm 必须为正（坠落高度差，实为 %r）" % (height_cm,))
    if rules is None:
        rules = tactical_battlefield.space_rules()
    return _fall_proposal_metric(height_cm, rules)


def revision_matches(expected_revision, state_revision):
    """移动提交前的 `state_revision` 前置检查（02C 第三节），**不读会话**。

    服务端在写入移动事实前，用调用方给的 `expected_revision` 与当前 revision 比对；
    `expected_revision` 为 `None` 表示调用方主动放弃并发检查（单人脚本 / 回放）。
    幂等键与事件序号由会话层负责，本模块只提供纯比较。
    """
    if expected_revision is None:
        return True
    if not _is_int(expected_revision) or not _is_int(state_revision):
        raise ValueError("revision 必须是整数（实为 %r / %r）"
                         % (expected_revision, state_revision))
    return expected_revision == state_revision


def _fall_proposal_abstract(levels, rules):
    """abstract 坠落提案：只给层级差，不伪造厘米 / 骰型。"""
    fall = rules["fall"]
    return {
        "height_cm": None,
        "height_levels": levels,
        "bands": None,
        "dice": None,
        "prone": None,
        "no_damage_below_cm": None,
        "step_cm": None,
        "rule": fall["damage_rule_source"],
        "threshold_rule": fall["source"],
        "proposal_only": True,
        "note": ABSTRACT_FALL_NOTE,
    }


def _reachable_view(best, cells):
    """可达集视图，按 `(cost, q, r, cell_id)` 排序（同成本确定性）。"""
    rows = [
        {
            "cell_id": cell_id,
            "q": cells[cell_id].get("q"),
            "r": cells[cell_id].get("r"),
            "surface_id": cells[cell_id].get("surface_id"),
            "cost": cost,
        }
        for cell_id, cost in best.items()
    ]
    rows.sort(key=lambda row: (row["cost"], row["q"], row["r"], row["cell_id"]))
    return rows


def _blocked_sort_key(record):
    return (
        record["q"] if record["q"] is not None else 0,
        record["r"] if record["r"] is not None else 0,
        record["code"],
        record["cell_id"] or "",
    )


def _path_to(destination, prev):
    chain = [destination]
    while chain[-1] in prev:
        chain.append(prev[chain[-1]])
    chain.reverse()
    return chain


def _fall_for_edge(battlefield, mode, rules, cells, source_id, target_id, link_id):
    """为一次坠落连接产出提案。

    高度差按 02C 的定义是「目标 − 起点」，坠落时它是负值；提案里的高度取
    `起点高度 − 目标高度`（正数）。不为正说明不是下落，返回 `None`。
    """
    gap = _height_gap(battlefield, cells[source_id], cells[target_id], mode)
    drop = -gap
    if drop <= 0:
        return None
    if mode == "metric":
        proposal = _fall_proposal_metric(drop, rules)
    else:
        proposal = _fall_proposal_abstract(drop, rules)
    proposal["link_id"] = link_id
    proposal["from_cell_id"] = source_id
    proposal["to_cell_id"] = target_id
    return proposal


def plan_move(battlefield, unit_id, origin, footprint, budget, *,
              allowed_modes=(), capabilities=(), occupied=None,
              destination=None, rules=None):
    """在战场 `battlefield` 上为 `unit_id` 规划一次移动（确定性 Dijkstra）。

    参数：

    - `battlefield`：已通过 `tactical_battlefield.load_battlefield` 的战场 dict（只读）；
    - `unit_id`：移动的实体 id；占格互斥时排除自身；
    - `origin` / `destination`：`cell_id`（`destination` 为空时只算可达集）；
    - `footprint`：相对 origin 的轴向偏移列表（`[{"q": 0, "r": 0}, …]`）；
    - `budget`：移动预算上限，metric 为厘米、abstract 为格 / 步（整数）；
    - `allowed_modes`：调用方授权的移动方式（`vertical_link.kind` 取值，如 `climb` /
      `jump` / `fall`）；`PASSIVE_LINK_KINDS` 不需要授权；
    - `capabilities`：单位具备的能力 / 装备 id；必须覆盖 `link.requirements`；
    - `occupied`：`cell_id -> 占用者 id`（含其它单位与物件，02C:65 互斥占格）；
    - `rules`：规则数据（默认读 `data/system/tactical_space.json`）。

    返回 dict：

    - `ok`：`destination` 可达（未给 `destination` 时表示搜索正常完成）；
    - `reachable`：`[{cell_id, q, r, surface_id, cost}]`，按 `(cost, q, r, cell_id)` 排序，
      含起点（成本 0）；
    - `path`：`cell_id` 序列（起点 → 终点）；不可达时为 `[]`；
    - `total_cost`：路径总成本；不可达时为 `None`；
    - `blocked`：`[{code, cell_id, q, r, message, rule}]`，含被拒格与目的地原因；
    - `falls`：路径上坠落连接的结构化提案（**只提案**，见 `fall_proposal`）。

    同成本多路径时按弹出顺序 `(cost, q, r, cell_id)` 确定性地选一条：堆序固定、
    只在严格更优时更新前驱，因此同输入在任何平台都得到同一路径。

    服务端必须对每次移动重算本函数；客户端可达格预览只是辅助。多格单位只校验移动
    后的目标 footprint（不模拟格子扫掠），一致性仍由 `validate_placement` 保证。
    """
    if not isinstance(unit_id, str) or not unit_id:
        raise ValueError("unit_id 必须是非空字符串")
    if not _is_int(budget) or budget < 0:
        raise ValueError("budget 必须是不小于 0 的整数（实为 %r）" % (budget,))
    if rules is None:
        rules = tactical_battlefield.space_rules()
    mode = tactical_battlefield.scale_mode(battlefield)
    modes = rules["scale"]["modes"]
    if mode not in modes:
        raise ValueError("战场口径必须是 %s 之一（实为 %r）" % (" | ".join(modes), mode))
    offsets = _normalize_footprint(footprint)
    allowed = _as_set(allowed_modes, "allowed_modes")
    capabilities = _as_set(capabilities, "capabilities")
    occupied = dict(occupied or {})

    cells, by_coord = _index(battlefield)
    links = _links_by_cell(battlefield)
    result = {
        "ok": False,
        "mode": mode,
        "unit_id": unit_id,
        "origin": origin,
        "destination": destination,
        "budget": budget,
        "reachable": [],
        "path": [],
        "total_cost": None,
        "blocked": [],
        "falls": [],
    }

    start = cells.get(origin)
    if start is None:
        result["blocked"].append({
            "code": "unknown_origin",
            "cell_id": origin,
            "q": None,
            "r": None,
            "message": "origin 不是本战场的 cell_id: %r" % (origin,),
            "rule": CELL_FIELD_RULE,
        })
        return result

    rejected = {}

    def reject(target, code, message, rule):
        key = (target.get("cell_id"), code)
        if key not in rejected:
            rejected[key] = _blocked_record(target, code, message, rule)

    best = {origin: 0}
    prev = {}
    prev_link = {}
    settled = set()
    heap = [(0, start["q"], start["r"], origin)]

    def relax(source_id, target, new_cost, link):
        target_id = target["cell_id"]
        known = best.get(target_id)
        if known is not None and new_cost >= known:
            return
        if new_cost > budget:
            reject(target, "over_budget",
                   "%s 需要成本 %d，超过移动预算 %d，已剪枝"
                   % (target_id, new_cost, budget),
                   BUDGET_RULE)
            return
        best[target_id] = new_cost
        prev[target_id] = source_id
        prev_link[target_id] = link
        heapq.heappush(heap, (new_cost, target["q"], target["r"], target_id))

    while heap:
        cost, _q, _r, cell_id = heapq.heappop(heap)
        if cell_id in settled or cost != best.get(cell_id):
            continue
        settled.add(cell_id)
        cell = cells[cell_id]

        # ① 同一 surface 的六邻格：普通走路，高差受连续坡面上限约束。
        surface = cell.get("surface_id")
        for nq, nr in tactical_hex.neighbors((cell.get("q"), cell.get("r"))):
            target = by_coord.get((surface, nq, nr))
            if target is None:
                continue  # 越界没有身份（02C:21），不产出噪声条目
            gap = _height_gap(battlefield, cell, target, mode)
            if not _walk_gap_ok(gap, mode, rules):
                reject(target, "elevation_step",
                       "%s → %s 高差 %d 超过连续坡面上限，普通走路不可通行"
                       % (cell_id, target["cell_id"], gap),
                       rules["movement"]["source"])
                continue
            blockers = _blocked_records(battlefield, unit_id, target, offsets, occupied)
            for record in blockers:
                rejected.setdefault((record["cell_id"], record["code"]), record)
            if blockers:
                continue
            relax(cell_id, target, cost + int(target["movement_cost"]), None)

        # ② 垂直连接：跨 surface 只能走这里；kind 与 requirements 决定授权。
        for link, other_id in links.get(cell_id, ()):
            target = cells.get(other_id)
            if target is None:
                continue
            kind = link.get("kind")
            if kind == "fall" and link.get("from_cell_id") != cell_id:
                continue  # 坠落只能从高往低，反向不是可通行通路
            if kind not in PASSIVE_LINK_KINDS and kind not in allowed:
                reject(target, "link_not_authorized",
                       "%s（kind=%s）需要调用方通过 allowed_modes 授权，普通寻路不提供免费通路"
                       % (link.get("link_id"), kind), LINK_KIND_RULE)
                continue
            missing = sorted(set(link.get("requirements") or ()) - capabilities)
            if missing:
                reject(target, "link_requires_capability",
                       "%s 需要单位具备 %s" % (link.get("link_id"), ", ".join(missing)),
                       LINK_KIND_RULE)
                continue
            blockers = _blocked_records(battlefield, unit_id, target, offsets, occupied)
            for record in blockers:
                rejected.setdefault((record["cell_id"], record["code"]), record)
            if blockers:
                continue
            relax(cell_id, target, cost + int(link["movement_cost"]), link)

    result["reachable"] = _reachable_view(best, cells)
    result["blocked"] = sorted(
        (record for (cell_id, _code), record in rejected.items() if cell_id not in best),
        key=_blocked_sort_key,
    )

    if destination is None:
        result["ok"] = True
        return result

    if destination not in cells:
        result["blocked"].append({
            "code": "unknown_destination",
            "cell_id": destination,
            "q": None,
            "r": None,
            "message": "destination 不是本战场的 cell_id: %r" % (destination,),
            "rule": CELL_FIELD_RULE,
        })
        result["blocked"].sort(key=_blocked_sort_key)
        return result

    if destination in best:
        result["ok"] = True
        result["path"] = _path_to(destination, prev)
        result["total_cost"] = best[destination]
        for index in range(1, len(result["path"])):
            link = prev_link.get(result["path"][index])
            if link is None or link.get("kind") != "fall":
                continue
            proposal = _fall_for_edge(
                battlefield, mode, rules, cells,
                result["path"][index - 1], result["path"][index], link["link_id"])
            if proposal is not None:
                result["falls"].append(proposal)
        return result

    if not any(record["cell_id"] == destination for record in result["blocked"]):
        result["blocked"].append(_blocked_record(
            cells[destination], "no_route",
            "%s 在预算与地形 / 高度 / 授权约束下没有可达路径" % (destination,),
            rules["movement"]["source"]))
        result["blocked"].sort(key=_blocked_sort_key)
    return result
