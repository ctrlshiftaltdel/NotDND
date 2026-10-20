#!/usr/bin/env python3
"""空间攻击查询：距离、视线、阻挡与掩体（Issue #420）。

只产出**空间事实**，不计算命中、伤害或任何引擎特定骰值（02C:146）。

职责边界：

- 几何只走内核：距离 / 射线 / cube 换算一律调用 `tactical_hex`
  （`hex_distance`、`hex_line`、`axial_to_cube`），本模块不复制算法（02C:23）；
- 口径隔离：metric 只给厘米（格中心三维欧氏距离，02C:115），abstract 只给
  格 / 步（02C:117），**没有**跨口径换算；
- 三件事分开算：移动阻挡（不归本模块）、视线阻挡、障碍高度、`cover_grade`
  各自独立返回，掩体不折算成命中加值（02C:136 / 02C:138）；
- `high_ground` 只是布尔标签 + 阈值来源，命中加值留在规则数据（02C:84 / 02A:93）；
- 阈值与受控枚举只读 `data/system/tactical_space.json`（`space_rules()`），
  模块内不复制 150 / 100 / 50 / 300 / 1d6 一类数值；
- 不 import `prism_core` / `notdnd_web` / `atlas*`，不读写文件、不写会话。

视线高度（02C 第十节，#442 落契约）：

- 眼高与目标可见高度**必须**来自 `system.tactical_space.sight` 按体型五档
  （`small` / `medium` / `large` / `huge` / `boss`）的 `eye_height_cm` /
  `visible_height_cm`（02C:261-265）；本模块**不设**任何隐式默认值；
- 数据当前**尚无** `sight` 配置 → metric 视线查询**必须失败**并在原因中
  说明缺少 `sight` 配置，**不得**静默取 0（02C:267）；
- 射线在某格的高度按「两端高度 × 水平距离」**有理数线性插值**（`Fraction`，
  02C:269）：射线高度**高于**障碍顶 = 可越过；否则（低于或恰在顶面）= 被挡；
- abstract 模式不适用眼高：LOS 只看 `blocks_los`，不做高度插值，
  也不读取 `sight` 配置（02C:271）。

朝向（02C 第九节，#442 落契约）：

- `orientation` 受控枚举六值 `n` / `ne` / `se` / `s` / `sw` / `nw`（02C:222），
  缺省 `n`（尖顶类，02C:232；缺省值为建议值，最终以 data 单落数值为准）；
- 中心坐标换算是**纯函数**：`n` / `s` 用尖顶类基准式，`ne` / `nw` / `se` /
  `sw` 用平顶类基准式（02C:241-242，`S = scale.hex_size_cm`）；
  同类内各值因 60° 旋转对称格点集合重合（02C:230），换算只取决于类；
- 朝向**不影响**六角距离、hex-line 并列选格与六邻偏移（02C:251），
  本模块不为不同朝向复制第二套几何内核；
- 数据当前尚无 `orientation` 字段 → 按缺省 `n`（02C:232）。
"""
import math
from fractions import Fraction

import tactical_battlefield as battlefield_model
import tactical_hex

__all__ = [
    "ORIENTATIONS",
    "BODY_SIZES",
    "query_attack",
]

# 02C:222 受控枚举：n / s 尖顶类，ne / nw / se / sw 平顶类（02C:228-229）。
ORIENTATIONS = ("n", "ne", "se", "s", "sw", "nw")
_POINTY = ("n", "s")
_FLAT = ("ne", "nw", "se", "sw")
_DEFAULT_ORIENTATION = "n"  # 02C:232 缺省值（建议值，最终以 data 单为准）

# 02C:261-265 体型五档（与 combat.json 的 poise.body_sizes 一一对应，不增删）。
BODY_SIZES = ("small", "medium", "large", "huge", "boss")

# 单位换算，不是规则阈值：厘米 / 米（02C 的距离单位是格中心厘米）。
_CM_PER_M = 100

_METRIC = "metric"
_ABSTRACT = "abstract"

_RULE_REFS = {
    "metric_distance": "docs/system/02c-hex-battlefield.md:115",
    "abstract_distance": "docs/system/02c-hex-battlefield.md:117",
    "line_of_sight": "docs/system/02c-hex-battlefield.md:121",
    "tie_break": "docs/system/02c-hex-battlefield.md:123",
    "cover_separation": "docs/system/02c-hex-battlefield.md:136",
    "cover_grades": "docs/system/02c-hex-battlefield.md:138",
    "los_blocked_no_attack": "docs/system/02c-hex-battlefield.md:140",
    "cell_id_identity": "docs/system/02c-hex-battlefield.md:21",
    "orientation_enum": "docs/system/02c-hex-battlefield.md:222",
    "orientation_default": "docs/system/02c-hex-battlefield.md:232",
    "orientation_formulas": "docs/system/02c-hex-battlefield.md:241",
    "orientation_scope": "docs/system/02c-hex-battlefield.md:251",
    "sight_config": "docs/system/02c-hex-battlefield.md:261",
    "sight_missing_fails": "docs/system/02c-hex-battlefield.md:267",
    "sight_interpolation": "docs/system/02c-hex-battlefield.md:269",
    "sight_abstract": "docs/system/02c-hex-battlefield.md:271",
    "high_ground": "docs/system/02c-hex-battlefield.md:78",
    "high_ground_label": "docs/system/02c-hex-battlefield.md:82",
    "high_ground_bonus": "docs/system/02c-hex-battlefield.md:84",
    "high_ground_rule": "docs/system/02a-tactics-engine.md:93",
    "cover_full": "docs/system/02a-tactics-engine.md:264",
    "high_ground_data": "system.tactical_space.high_ground",
    "cover_data": "system.tactical_space.cover.grades",
    "hex_size": "system.tactical_space.scale.hex_size_cm",
    "orientation_data": "system.tactical_space.scale.orientation",
    "sight_data": "system.tactical_space.sight",
}


class QueryError(ValueError):
    """查询参数或战场数据不足以给出空间事实。"""


# ── 参数校验 ────────────────────────────────────────────────────────────

def _require_orientation(orientation):
    """朝向受控枚举校验；缺省 `n`（尖顶类，02C:232）。

    数据当前没有 `scale.orientation` 字段 → 调用方不传时按缺省 `n`；
    缺省值是建议值，最终以 data 单落数值为准（02C:232）。
    """
    if orientation is None:
        return _DEFAULT_ORIENTATION
    if orientation not in ORIENTATIONS:
        raise QueryError("orientation 必须是 %s 之一（实为 %r）"
                         % (" | ".join(ORIENTATIONS), orientation))
    return orientation


def _require_body_size(name, body_size):
    """体型五档校验（02C:261-265）；metric 视线查询必传。"""
    if body_size is None:
        raise QueryError("%s 是必传参数：视线高度按体型五档从 sight 配置派生"
                         "（02C:261-265）" % (name,))
    if body_size not in BODY_SIZES:
        raise QueryError("%s 必须是 %s 之一（实为 %r）"
                         % (name, " | ".join(BODY_SIZES), body_size))
    return body_size


def _sight_heights(rules, actor_body_size, target_body_size):
    """从 `system.tactical_space.sight` 派生眼高与目标可见高度（02C:261-265）。

    没有 `sight` 配置时**必须失败**并在原因中说明，不得静默取 0
    或任何隐式默认值（02C:267）。
    """
    sight = rules.get("sight")
    if not isinstance(sight, dict) or not sight:
        raise QueryError(
            "缺少 sight 配置：system.tactical_space.sight 未落地，"
            "眼高与目标可见高度无法按体型五档派生（02C:267 禁止静默取 0）"
        )
    heights = []
    for role, body_size, field in (
        ("attacker", actor_body_size, "eye_height_cm"),
        ("target", target_body_size, "visible_height_cm"),
    ):
        entry = sight.get(body_size)
        if not isinstance(entry, dict) or field not in entry:
            raise QueryError(
                "sight 配置缺少体型档 %s 的 %s（02C:261-265 五档必须齐备）"
                % (body_size, field)
            )
        value = entry[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise QueryError(
                "sight.%s.%s 必须是非负整数（实为 %r）" % (body_size, field, value)
            )
        heights.append(value)
    return heights[0], heights[1]


def _cell(battlefield, cell_id, role):
    if not isinstance(cell_id, str) or not cell_id:
        raise QueryError("%s 的 cell_id 必须是非空字符串（实为 %r）" % (role, cell_id))
    cell = battlefield_model.cell_by_id(battlefield, cell_id)
    if cell is None:
        raise QueryError("%s 的 cell_id 不在本战场：%r" % (role, cell_id))
    return cell


# ── 几何 ────────────────────────────────────────────────────────────────

def _center_cm(cell, hex_size_cm, orientation):
    """格中心的水平坐标（厘米），以 axial `(0, 0)` 的中心为原点（02C:241-242）。

    `S = hex_size_cm` 是**相邻格中心距离**（02C:37）。按 02C:241-242 的
    基准式直接以 `S` 表达；朝向只决定用哪一类基准式——`n` / `s` 尖顶类，
    `ne` / `nw` / `se` / `sw` 平顶类（同类内 60° 旋转对称格点集合重合，
    02C:230，换算只取决于类）。验证锚点（02C:246-249）：两类下相邻格
    中心距均等于 `S`。这是纯函数；朝向不影响六角距离 / hex-line（02C:251）。
    """
    q = cell["q"]
    r = cell["r"]
    if orientation in _POINTY:
        # 尖顶类基准式：x = S × (q + r/2)，y = S × (√3/2) × r
        return (hex_size_cm * (q + r / 2.0),
                hex_size_cm * (math.sqrt(3.0) / 2.0) * r)
    # 平顶类基准式：x = S × (√3/2) × q，y = S × (r + q/2)
    return (hex_size_cm * (math.sqrt(3.0) / 2.0) * q,
            hex_size_cm * (r + q / 2.0))


def _round6(value):
    return round(float(value), 6)


def _ray_height_cm(start_cm, end_cm, index, steps):
    """射线上第 `index / steps` 处的高度，用 `Fraction` 精确表示（02C:127）。"""
    ratio = Fraction(index, steps)
    return Fraction(start_cm) + ratio * (end_cm - start_cm)


def _hex_size_cm(battlefield, rules):
    """metric 相邻格中心距离：优先取战场 `scale`，缺省回落规则数据。"""
    scale = battlefield.get("scale") if isinstance(battlefield, dict) else None
    if isinstance(scale, dict) and isinstance(scale.get("hex_size_cm"), int):
        return scale["hex_size_cm"]
    return rules["scale"]["hex_size_cm"]


# ── 主入口 ──────────────────────────────────────────────────────────────

def query_attack(battlefield, actor_cell_id, target_cell_id, *,
                 actor_body_size=None, target_body_size=None,
                 orientation=None, distance_unit=None, rules=None):
    """查询一次攻击的空间事实，返回确定性 dict（同输入同输出）。

    参数：

    - `battlefield`：已加载的静态战场（`tactical.battlefield`）；
    - `actor_cell_id` / `target_cell_id`：权威定位，**不**由 `q/r` 推断（02C:21）；
    - `actor_body_size` / `target_body_size`：体型五档（02C:261-265），
      metric 视线查询必传——眼高 / 目标可见高度按档从 `sight` 配置派生；
      abstract 模式不读 `sight`，可省略；
    - `orientation`：六朝向 `n` / `ne` / `se` / `s` / `sw` / `nw`（02C:222），
      缺省 `n`（02C:232；数据尚未落 `scale.orientation` 字段）；
    - `distance_unit`：abstract 战场专用的格 / 步口径（`cell` / `step`），
    只有明确声明该口径的能力才可调用；metric 战场必须不给；
    - `rules`：`system.tactical_space`，缺省读 `space_rules()`（测试可注入）。

    返回（键序固定）：

    `scale_mode` / `from_cell_id` / `to_cell_id` / `from_surface_id` /
    `to_surface_id` / `hex_distance` / `distance` / `distance_unit` /
    `distance_basis` / `distance_m` / `horizontal_distance_cm` /
    `vertical_distance_cm` / `signed_height_delta_cm` / `signed_level_delta` /
    `eye_from_cm` / `visible_at_cm` / `line_of_sight` / `blocked_cells` /
    `blocked_reasons` / `obstacles` / `cover_grade` / `cover_source_cell_id` /
    `cover_candidates` / `high_ground` / `high_ground_threshold` /
    `high_ground_threshold_unit` / `high_ground_applies_to` / `executable` /
    `failure_reasons` / `unresolved_cells` / `notes` / `rule_refs`。

    `line_of_sight` 为 `None` 表示**无法判定**（跨 surface 或地图缺格），
    与「判定为被遮断」的 `False` 严格区分（02C:21）。
    """
    if rules is None:
        rules = battlefield_model.space_rules()
    orientation = _require_orientation(orientation)

    mode = battlefield_model.scale_mode(battlefield)
    if mode not in (rules["scale"]["modes"]):
        raise QueryError("战场 scale.mode 非法（实为 %r）" % (mode,))
    if mode == _METRIC and distance_unit is not None:
        raise QueryError("metric 战场不得声明格 / 步口径（02C:115 / 02C:117）")
    if mode == _ABSTRACT:
        if distance_unit is None:
            raise QueryError("abstract 战场必须由能力声明格 / 步口径（02C:117）")
        if distance_unit not in rules["scale"]["abstract_units"]:
            raise QueryError("distance_unit 必须是 %s 之一（实为 %r）"
                             % (" | ".join(rules["scale"]["abstract_units"]),
                                distance_unit))

    actor = _cell(battlefield, actor_cell_id, "actor")
    target = _cell(battlefield, target_cell_id, "target")

    # 视线高度（02C:261-267）：只从 sight 配置按体型五档派生；
    # abstract 模式不适用眼高、不读 sight（02C:271）。
    if mode == _METRIC:
        actor_body_size = _require_body_size("actor_body_size", actor_body_size)
        target_body_size = _require_body_size("target_body_size", target_body_size)
        eye_height_cm, target_height_cm = _sight_heights(
            rules, actor_body_size, target_body_size)
    else:
        eye_height_cm = target_height_cm = None

    actor_qr = tactical_hex.axial((actor["q"], actor["r"]))
    target_qr = tactical_hex.axial((target["q"], target["r"]))
    steps = tactical_hex.hex_distance(actor_qr, target_qr)
    line = tactical_hex.hex_line(actor_qr, target_qr)

    notes = []
    unresolved = []
    mids = []
    for index, coord in enumerate(line):
        if index == 0 or index == len(line) - 1:
            continue  # 端点不是遮挡物：那是参战双方的所在格
        cell = battlefield_model.cell_at(
            battlefield, coord[0], coord[1], actor.get("surface_id"))
        if cell is None:
            unresolved.append({"index": index, "q": coord[0], "r": coord[1]})
        else:
            mids.append((index, cell))

    distance = _distance_facts(
        battlefield, actor, target, steps, mode, distance_unit,
        orientation, rules)
    sight = _sight_facts(
        battlefield, actor, target, mids, unresolved, steps, mode,
        eye_height_cm, target_height_cm, notes)
    cover = _cover_facts(target, actor_cell_id, target_cell_id, mids,
                         sight["blocked_cells"], rules)

    failures = []
    if sight["line_of_sight"] is False:
        blockers = [reason["cell_id"] for reason in sight["blocked_reasons"]]
        failures.append({
            "code": "line_of_sight_blocked",
            "message": "视线被 %s 完全遮断，普通远程攻击不得结算（02C:140）"
                       % (", ".join(blockers) if blockers else "中途格"),
            "cell_id": blockers[0] if blockers else None,
            "rule_ref": _RULE_REFS["los_blocked_no_attack"],
        })
    elif sight["line_of_sight"] is None:
        failures.append({
            "code": "line_of_sight_undefined",
            "message": "视线无法判定（跨 surface 或地图缺格），不能当作可行攻击",
            "cell_id": None,
            "rule_ref": _RULE_REFS["cell_id_identity"],
        })
    if cover["cover_grade"] == "full":
        failures.append({
            "code": "cover_full",
            "message": "目标处于 full 掩体：无法被攻击，你也无法攻击（02A:264）",
            "cell_id": cover["cover_source_cell_id"],
            "rule_ref": _RULE_REFS["cover_full"],
        })

    executable = sight["line_of_sight"] is True and cover["cover_grade"] != "full"
    high_ground = _high_ground_facts(
        distance, sight["line_of_sight"], steps, rules)

    return {
        "scale_mode": mode,
        "from_cell_id": actor.get("cell_id"),
        "to_cell_id": target.get("cell_id"),
        "from_surface_id": actor.get("surface_id"),
        "to_surface_id": target.get("surface_id"),
        "hex_distance": steps,
        "distance": distance["distance"],
        "distance_unit": distance["distance_unit"],
        "distance_basis": distance["distance_basis"],
        "distance_m": distance["distance_m"],
        "horizontal_distance_cm": distance["horizontal_distance_cm"],
        "vertical_distance_cm": distance["vertical_distance_cm"],
        "signed_height_delta_cm": distance["signed_height_delta_cm"],
        "signed_level_delta": distance["signed_level_delta"],
        "eye_from_cm": sight["eye_from_cm"],
        "visible_at_cm": sight["visible_at_cm"],
        "line_of_sight": sight["line_of_sight"],
        "blocked_cells": sight["blocked_cells"],
        "blocked_reasons": sight["blocked_reasons"],
        "obstacles": sight["obstacles"],
        "cover_grade": cover["cover_grade"],
        "cover_source_cell_id": cover["cover_source_cell_id"],
        "cover_candidates": cover["cover_candidates"],
        "high_ground": high_ground["value"],
        "high_ground_threshold": high_ground["threshold"],
        "high_ground_threshold_unit": high_ground["threshold_unit"],
        "high_ground_applies_to": list(rules["high_ground"]["applies_to"]),
        "executable": executable,
        "failure_reasons": failures,
        "unresolved_cells": unresolved,
        "notes": notes,
        "rule_refs": dict(_RULE_REFS),
        "orientation": orientation,
        "actor_body_size": actor_body_size,
        "target_body_size": target_body_size,
        "eye_height_cm": eye_height_cm,
        "target_height_cm": target_height_cm,
    }


# ── 距离 ────────────────────────────────────────────────────────────────

def _distance_facts(battlefield, actor, target, steps, mode, distance_unit,
                    orientation, rules):
    """metric：格中心三维欧氏距离；abstract：只给六角格距离（不换算）。"""
    if mode == _ABSTRACT:
        actor_level = battlefield_model.abstract_elevation_level(battlefield, actor)
        target_level = battlefield_model.abstract_elevation_level(battlefield, target)
        return {
            "distance": steps,
            "distance_unit": distance_unit,
            "distance_basis": "hex",
            "distance_m": None,
            "horizontal_distance_cm": None,
            "vertical_distance_cm": None,
            "signed_height_delta_cm": None,
            "signed_level_delta": target_level - actor_level,
        }

    hex_size_cm = _hex_size_cm(battlefield, rules)
    from_x, from_y = _center_cm(actor, hex_size_cm, orientation)
    to_x, to_y = _center_cm(target, hex_size_cm, orientation)
    horizontal_cm = math.hypot(to_x - from_x, to_y - from_y)

    actor_cm = battlefield_model.metric_elevation_cm(battlefield, actor)
    target_cm = battlefield_model.metric_elevation_cm(battlefield, target)
    signed_delta_cm = target_cm - actor_cm
    total_cm = math.hypot(horizontal_cm, signed_delta_cm)
    return {
        "distance": _round6(total_cm),
        "distance_unit": "cm",
        "distance_basis": "metric_3d",
        "distance_m": _round6(total_cm / _CM_PER_M),
        "horizontal_distance_cm": _round6(horizontal_cm),
        "vertical_distance_cm": abs(signed_delta_cm),
        "signed_height_delta_cm": signed_delta_cm,
        "signed_level_delta": None,
    }


# ── 视线 ────────────────────────────────────────────────────────────────

def _sight_facts(battlefield, actor, target, mids, unresolved, steps, mode,
                 eye_height_cm, target_height_cm, notes):
    """沿 `tactical_hex.hex_line` 逐格检查 `blocks_los` 与 `obstacle_height`。"""
    same_surface = actor.get("surface_id") == target.get("surface_id")
    eye_from_cm = None
    visible_at_cm = None
    start_cm = end_cm = None

    if mode == _METRIC:
        start_cm = battlefield_model.metric_elevation_cm(battlefield, actor) + eye_height_cm
        end_cm = battlefield_model.metric_elevation_cm(battlefield, target) + target_height_cm
        eye_from_cm = start_cm
        visible_at_cm = end_cm
    else:
        notes.append("abstract 模式不适用眼高：LOS 只看 blocks_los，"
                     "不做高度插值，也不读取 sight 配置（02C:271）")

    obstacles = []
    blocked_reasons = []
    blocked_cells = []

    if steps > 0:
        for index, cell in mids:
            cell_id = cell.get("cell_id")
            ray_cm = None
            if mode == _METRIC:
                ray_cm = _ray_height_cm(start_cm, end_cm, index, steps)
            blocked_code = None
            obstacle_top_cm = None
            obstacle_height = cell.get("obstacle_height")
            if mode == _METRIC and isinstance(obstacle_height, int) and obstacle_height > 0:
                cell_cm = battlefield_model.metric_elevation_cm(battlefield, cell)
                obstacle_top_cm = cell_cm + obstacle_height
            if cell.get("blocks_los") is True:
                blocked_code = "blocks_los"
            elif obstacle_top_cm is not None and ray_cm <= obstacle_top_cm:
                blocked_code = "obstacle_height"

            if obstacle_top_cm is not None:
                obstacles.append({
                    "cell_id": cell_id,
                    "line_index": index,
                    "elevation_cm": obstacle_top_cm - obstacle_height,
                    "obstacle_height": obstacle_height,
                    "obstacle_top_cm": obstacle_top_cm,
                    "ray_height_cm": _round6(ray_cm),
                    "blocks_ray": blocked_code == "obstacle_height",
                })

            if blocked_code is None:
                continue
            blocked_cells.append(cell_id)
            blocked_reasons.append({
                "code": blocked_code,
                "cell_id": cell_id,
                "line_index": index,
                "message": _block_message(blocked_code, cell, obstacle_top_cm, ray_cm),
                "rule_ref": _RULE_REFS["line_of_sight"],
                "ray_height_cm": None if ray_cm is None else _round6(ray_cm),
                "obstacle_top_cm": obstacle_top_cm,
            })

    if not same_surface:
        line_of_sight = None
        notes.append("参战双方不在同一 surface：02C:21 禁止用 q/r 推断跨层关系，"
                     "视线不可判定（需要 vertical_link / 传送等显式数据）")
    elif unresolved:
        line_of_sight = None
        notes.append("射线经过地图上不存在的格：视线不可判定，不当作通畅")
    elif blocked_reasons:
        line_of_sight = False
    else:
        line_of_sight = True

    return {
        "line_of_sight": line_of_sight,
        "blocked_cells": blocked_cells,
        "blocked_reasons": blocked_reasons,
        "obstacles": obstacles,
        "eye_from_cm": eye_from_cm,
        "visible_at_cm": visible_at_cm,
    }


def _block_message(code, cell, obstacle_top_cm, ray_cm):
    if code == "blocks_los":
        return "%s: blocks_los=true，视线在此完全遮断" % cell.get("cell_id")
    return ("%s: 障碍顶面 %d cm 不低于射线高度 %s cm，视线被挡住（obstacle_height=%d）"
            % (cell.get("cell_id"), obstacle_top_cm, _round6(ray_cm),
               cell.get("obstacle_height")))


# ── 掩体（独立于阻挡与高度，不折算命中加值） ────────────────────────────

def _cover_facts(target, actor_cell_id, target_cell_id, mids, blocked_cells,
                 rules):
    """掩体只取「射线经过且未完全遮断的格」与目标所在格，按规则档序取最高档。"""
    grades = list(rules["cover"]["grades"])
    rank = {grade: index for index, grade in enumerate(grades)}
    blocked = set(blocked_cells)

    candidates = []
    for index, cell in mids:
        grade = cell.get("cover_grade")
        if grade in rank and cell.get("cell_id") not in blocked:
            candidates.append({
                "cell_id": cell.get("cell_id"),
                "cover_grade": grade,
                "line_index": index,
            })
    if actor_cell_id != target_cell_id:
        grade = target.get("cover_grade")
        if grade in rank:
            candidates.append({
                "cell_id": target.get("cell_id"),
                "cover_grade": grade,
                "line_index": None,
            })

    if not candidates:
        return {"cover_grade": None, "cover_source_cell_id": None,
                "cover_candidates": []}

    # 档位高者优先；同档取更靠近目标的一格（line_index 大者），保证唯一且可解释。
    chosen = max(candidates, key=lambda item: (
        rank[item["cover_grade"]],
        -1 if item["line_index"] is None else item["line_index"],
    ))
    candidates.sort(key=lambda item: (
        rank[item["cover_grade"]],
        -1 if item["line_index"] is None else item["line_index"],
        str(item["cell_id"]),
    ))
    return {
        "cover_grade": chosen["cover_grade"],
        "cover_source_cell_id": chosen["cell_id"],
        "cover_candidates": candidates,
    }


# ── 高地标签（只给布尔 + 阈值来源，不加骰） ──────────────────────────────

def _high_ground_facts(distance, line_of_sight, steps, rules):
    """02C:82：高于目标达到阈值**且**视线有效时 `high_ground = true`。

    只输出布尔标签与阈值来源；「+1 层」留在 02A:93 / `high_ground.applies_to`，
    本模块不加骰（02C:84）。
    """
    high_ground_rules = rules["high_ground"]
    if distance["distance_basis"] == "metric_3d":
        threshold = high_ground_rules["threshold_cm"]
        unit = "cm"
        delta = distance["signed_height_delta_cm"]
    else:
        threshold = high_ground_rules["threshold_level"]
        unit = "level"
        delta = distance["signed_level_delta"]
    if steps == 0 or line_of_sight is not True or delta is None:
        return {"value": False, "threshold": threshold, "threshold_unit": unit}
    # 有符号高度差是「目标 − 起点」（02C:74）；高地要求「攻击者 − 目标」达到阈值。
    return {
        "value": -delta >= threshold,
        "threshold": threshold,
        "threshold_unit": unit,
    }
