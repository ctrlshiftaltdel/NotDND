#!/usr/bin/env python3
"""六角格战场：加载 / 校验 / 查询（Issue #417）。

职责边界：

- **只读数据**：`load_battlefield` 接受 dict 或 JSON 路径，校验不通过时抛
  `BattlefieldError`，其 `errors` 是带 `$` 路径的错误列表——**不静默修正任何字段**；
- **几何只走内核**：距离、邻格、坐标合法性一律调用 `tactical_hex`，本模块不复制算法；
- **口径隔离**：metric 与 abstract 各有独立入口（`metric_elevation_cm` /
  `abstract_elevation_level`），**没有**`cm_to_cells` 一类的跨口径换算函数
  （02C:39：abstract 的格 / 步不得伪装成米）；
- **surface 隔离**：同一 `q/r` 允许在不同 `surface_id` 上共存，任何位置都以 `cell_id`
  为权威引用（02C:21），因此**不能**用 `q/r` 单独判定跨楼层可达；
- 不 import `atlas*` / `prism_core`，无 HTTP，不写文件。

数据契约见 `data/schema/tactical.battlefield.schema.json` 与 `data/FORMAT.md` 第 4.6 节；
阈值与受控枚举的唯一来源是 `data/system/tactical_space.json`。
"""
import json
import os
import random
import re

import tactical_hex

ROOT = os.path.dirname(os.path.abspath(__file__))
SPACE_RULES_PATH = os.path.join(ROOT, "data", "system", "tactical_space.json")

BATTLEFIELD_KIND = "tactical.battlefield"
SCHEMA_VERSION = 1
SOURCE_PATTERN = re.compile(r"^docs/(system|scenario)/[^:]+\.md:[0-9]+$")

_TOP_LEVEL_FIELDS = (
    "schema_version", "kind", "source", "battlefield_id", "world_id", "site_id",
    "atlas_ref", "seed", "generator_version", "geometry", "scale", "cells",
    "vertical_links",
)
_TOP_LEVEL_REQUIRED = (
    "schema_version", "kind", "source", "battlefield_id", "world_id", "geometry",
    "scale", "cells",
)
_SCALE_FIELDS = ("mode", "hex_size_cm", "unit")
_CELL_REQUIRED = (
    "cell_id", "q", "r", "surface_id", "movement_cost", "blocks_movement", "blocks_los",
)
_CELL_FIELDS = _CELL_REQUIRED + (
    "elevation_cm", "elevation_level", "terrain_id", "terrain_tags", "obstacle_height",
    "cover_grade", "hazard_refs", "feature_refs", "spawnable",
)
_LINK_REQUIRED = (
    "link_id", "from_cell_id", "to_cell_id", "kind", "movement_cost", "bidirectional",
    "requirements",
)
_LINK_FIELDS = _LINK_REQUIRED + ("effect_refs",)

# fixture 只选无版权含义的通用地形名（真实内容由各处数据定义）。
_FIXTURE_TERRAINS = ("floor", "rubble")


class BattlefieldError(ValueError):
    """战场数据未通过校验；`errors` 是带 `$` 路径的错误列表。"""

    def __init__(self, errors, label=None):
        self.errors = list(errors)
        self.label = label
        prefix = "%s: " % label if label else ""
        super().__init__(prefix + "; ".join(self.errors))


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_empty_str(value):
    return isinstance(value, str) and bool(value)


def _is_str_list(value):
    return (
        isinstance(value, list)
        and all(_is_non_empty_str(item) for item in value)
        and len(set(value)) == len(value)
    )


def space_rules(path=SPACE_RULES_PATH):
    """读取 `system.tactical_space`（阈值与受控枚举的唯一来源）。

    每次返回新解析的 dict，调用方改它不会污染后续调用。
    """
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# ── 跨字段不变式（原 tests/test_tactical_schema.py 的实现，唯一一份） ──────

def battlefield_invariants(instance):
    """JSON Schema 子集无法表达的战场不变式。

    - 每格的高度字段恰好一个，且与 scale.mode 同口径（02C:47）；
    - 同一地图内 cell_id 唯一（cell_id 是稳定身份，02C:21）；
    - metric 必须给 hex_size_cm；abstract 必须给 unit 且不得给 hex_size_cm。

    错误文本与 `$` 路径格式是 #416 既有测试的契约，移动实现时保持不变。
    """
    errors = []
    scale = instance.get("scale")
    scale = scale if isinstance(scale, dict) else {}
    mode = scale.get("mode")
    if mode == "metric" and "hex_size_cm" not in scale:
        errors.append("$.scale.hex_size_cm: metric 模式必须给出相邻格中心距离（02C:37）")
    if mode == "abstract":
        if "unit" not in scale:
            errors.append("$.scale.unit: abstract 模式必须声明格 / 步口径（02C:39）")
        if "hex_size_cm" in scale:
            errors.append("$.scale.hex_size_cm: abstract 模式不得使用厘米口径（02C:39）")

    cells = instance.get("cells")
    if not isinstance(cells, list):
        return errors
    seen = set()
    for index, cell in enumerate(cells):
        if not isinstance(cell, dict):
            continue
        path = "$.cells[%d]" % index
        has_cm = "elevation_cm" in cell
        has_level = "elevation_level" in cell
        if has_cm and has_level:
            errors.append("%s.elevation_level: metric / abstract 高度字段混用（已有 elevation_cm）" % path)
        elif not has_cm and not has_level:
            errors.append("%s: 缺少高度字段（elevation_cm 与 elevation_level 必须恰好一个）" % path)
        elif mode == "metric" and has_level:
            errors.append("%s.elevation_level: scale.mode=metric 时不得使用 elevation_level（02C:47）" % path)
        elif mode == "abstract" and has_cm:
            errors.append("%s.elevation_cm: scale.mode=abstract 时不得使用 elevation_cm（02C:47）" % path)

        cell_id = cell.get("cell_id")
        if isinstance(cell_id, str):
            if cell_id in seen:
                errors.append("%s.cell_id: 重复 cell_id '%s'（cell_id 是稳定身份，02C:21）"
                              % (path, cell_id))
            else:
                seen.add(cell_id)
    return errors


# ── 结构校验 ────────────────────────────────────────────────────────────

def validate_battlefield(instance, rules=None):
    """校验一张静态战场，返回错误列表（带 `$` 路径；空列表 = 通过）。"""
    if rules is None:
        rules = space_rules()
    if not isinstance(instance, dict):
        return ["$: 战场必须是 JSON 对象"]
    errors = []
    errors.extend(_envelope_errors(instance, rules))
    errors.extend(_cell_errors(instance, rules))
    errors.extend(_link_errors(instance, rules))
    errors.extend(battlefield_invariants(instance))
    return errors


def _envelope_errors(instance, rules):
    errors = []
    for key in _TOP_LEVEL_REQUIRED:
        if key not in instance:
            errors.append("$: 缺少必填字段 '%s'" % key)
    for key in sorted(set(instance) - set(_TOP_LEVEL_FIELDS)):
        errors.append("$.%s: 未知字段（schema additionalProperties=false）" % key)

    if "schema_version" in instance:
        version = instance["schema_version"]
        if not _is_int(version) or version != SCHEMA_VERSION:
            errors.append("$.schema_version: 只支持 %d（实为 %r）" % (SCHEMA_VERSION, version))
    if "kind" in instance and instance["kind"] != BATTLEFIELD_KIND:
        errors.append("$.kind: 必须是 '%s'（实为 %r）" % (BATTLEFIELD_KIND, instance["kind"]))
    source = instance.get("source")
    if "source" in instance and not (isinstance(source, str) and SOURCE_PATTERN.search(source)):
        errors.append("$.source: 必须是 docs/<system|scenario>/<文件>.md:<行号>（实为 %r）"
                      % (source,))
    for key in ("battlefield_id", "world_id", "site_id", "atlas_ref", "generator_version"):
        if key in instance and not _is_non_empty_str(instance[key]):
            errors.append("$.%s: 必须是非空字符串" % key)
    if "seed" in instance:
        seed = instance["seed"]
        if not (_is_int(seed) or _is_non_empty_str(seed)):
            errors.append("$.seed: 必须是整数或非空字符串（实为 %r）" % (seed,))

    if "geometry" in instance:
        geometry = instance["geometry"]
        if not isinstance(geometry, dict):
            errors.append("$.geometry: 必须是对象")
        else:
            if geometry.get("type") != "hex_axial":
                errors.append("$.geometry.type: 必须是 'hex_axial'（实为 %r）"
                              % (geometry.get("type"),))
            for key in sorted(set(geometry) - {"type"}):
                errors.append("$.geometry.%s: 未知字段（schema additionalProperties=false）" % key)

    if "scale" in instance:
        scale = instance["scale"]
        if not isinstance(scale, dict):
            errors.append("$.scale: 必须是对象")
        else:
            errors.extend(_scale_errors(scale, rules))
    if "cells" in instance and not isinstance(instance["cells"], list):
        errors.append("$.cells: 必须是数组")
    if "vertical_links" in instance and not isinstance(instance["vertical_links"], list):
        errors.append("$.vertical_links: 必须是数组")
    return errors


def _scale_errors(scale, rules):
    errors = []
    modes = rules["scale"]["modes"]
    if scale.get("mode") not in modes:
        errors.append("$.scale.mode: 必须是 %s 之一（实为 %r）"
                      % (" | ".join(modes), scale.get("mode")))
    for key in sorted(set(scale) - set(_SCALE_FIELDS)):
        errors.append("$.scale.%s: 未知字段（schema additionalProperties=false）" % key)
    if "hex_size_cm" in scale:
        hex_size = scale["hex_size_cm"]
        if not _is_int(hex_size) or hex_size < 1:
            errors.append("$.scale.hex_size_cm: 必须是正整数厘米（实为 %r）" % (hex_size,))
    if "unit" in scale and scale["unit"] not in rules["scale"]["abstract_units"]:
        errors.append("$.scale.unit: 必须是 %s 之一（实为 %r）"
                      % (" | ".join(rules["scale"]["abstract_units"]), scale["unit"]))
    return errors


def _cell_errors(instance, rules):
    cells = instance.get("cells")
    if not isinstance(cells, list):
        return []
    errors = []
    grades = rules["cover"]["grades"]
    for index, cell in enumerate(cells):
        path = "$.cells[%d]" % index
        if not isinstance(cell, dict):
            errors.append("%s: 必须是对象" % path)
            continue
        for key in _CELL_REQUIRED:
            if key not in cell:
                errors.append("%s: 缺少必填字段 '%s'" % (path, key))
        for key in sorted(set(cell) - set(_CELL_FIELDS)):
            errors.append("%s.%s: 未知字段（schema additionalProperties=false）" % (path, key))
        for key in ("cell_id", "surface_id", "terrain_id"):
            if key in cell and not _is_non_empty_str(cell[key]):
                errors.append("%s.%s: 必须是非空字符串" % (path, key))
        for key in ("q", "r", "elevation_cm", "elevation_level", "obstacle_height"):
            if key in cell and not _is_int(cell[key]):
                errors.append("%s.%s: 必须是整数（实为 %r）" % (path, key, cell[key]))
        for key in ("movement_cost", "obstacle_height"):
            if _is_int(cell.get(key)) and cell[key] < 0:
                errors.append("%s.%s: 不得小于 0（实为 %d）" % (path, key, cell[key]))
        for key in ("blocks_movement", "blocks_los", "spawnable"):
            if key in cell and not isinstance(cell[key], bool):
                errors.append("%s.%s: 必须是布尔值（实为 %r）" % (path, key, cell[key]))
        for key in ("terrain_tags", "hazard_refs", "feature_refs"):
            if key in cell and not _is_str_list(cell[key]):
                errors.append("%s.%s: 必须是非空字符串数组且元素唯一" % (path, key))
        if "cover_grade" in cell and cell["cover_grade"] not in grades:
            errors.append("%s.cover_grade: 必须是 %s 之一（实为 %r）"
                          % (path, " | ".join(grades), cell["cover_grade"]))
    return errors


def _link_errors(instance, rules):
    links = instance.get("vertical_links")
    if not isinstance(links, list):
        return []
    cells = instance.get("cells")
    known = set()
    if isinstance(cells, list):
        for cell in cells:
            if isinstance(cell, dict) and _is_non_empty_str(cell.get("cell_id")):
                known.add(cell["cell_id"])
    kinds = rules["vertical_links"]["kinds"]
    errors = []
    for index, link in enumerate(links):
        path = "$.vertical_links[%d]" % index
        if not isinstance(link, dict):
            errors.append("%s: 必须是对象" % path)
            continue
        for key in _LINK_REQUIRED:
            if key not in link:
                errors.append("%s: 缺少必填字段 '%s'" % (path, key))
        for key in sorted(set(link) - set(_LINK_FIELDS)):
            errors.append("%s.%s: 未知字段（schema additionalProperties=false）" % (path, key))
        for key in ("link_id", "from_cell_id", "to_cell_id"):
            if key in link and not _is_non_empty_str(link[key]):
                errors.append("%s.%s: 必须是非空字符串" % (path, key))
        if "movement_cost" in link:
            cost = link["movement_cost"]
            if not _is_int(cost) or cost < 0:
                errors.append("%s.movement_cost: 必须是不小于 0 的整数（实为 %r）" % (path, cost))
        if "bidirectional" in link and not isinstance(link["bidirectional"], bool):
            errors.append("%s.bidirectional: 必须是布尔值" % path)
        for key in ("requirements", "effect_refs"):
            if key in link and not _is_str_list(link[key]):
                errors.append("%s.%s: 必须是非空字符串数组且元素唯一" % (path, key))
        if "kind" in link and link["kind"] not in kinds:
            errors.append("%s.kind: 必须是 %s 之一（实为 %r）"
                          % (path, " | ".join(kinds), link["kind"]))
        if isinstance(cells, list):
            for key in ("from_cell_id", "to_cell_id"):
                if _is_non_empty_str(link.get(key)) and link[key] not in known:
                    errors.append("%s.%s: 指向不存在的 cell_id '%s'（悬空引用）"
                                  % (path, key, link[key]))
    return errors


def load_battlefield(source, rules=None):
    """加载并校验静态战场；`source` 是战场 dict 或 JSON 文件路径。

    失败抛 `BattlefieldError`（`ValueError` 子类），`error.errors` 是结构化错误列表。
    成功返回**传入的同一份数据**（不做补全、不做默认值填充），调用方应视为只读。
    """
    if isinstance(source, dict):
        instance = source
        label = None
    elif isinstance(source, (str, os.PathLike)):
        label = os.fspath(source)
        try:
            with open(label, encoding="utf-8") as handle:
                instance = json.load(handle)
        except OSError as error:
            raise BattlefieldError(["$: 无法读取战场文件（%s）" % error], label)
        except json.JSONDecodeError as error:
            raise BattlefieldError(["$: 不是合法 JSON（%s）" % error], label)
    else:
        raise TypeError("source 必须是 dict 或 JSON 路径，实为 %r" % (type(source).__name__,))
    errors = validate_battlefield(instance, rules)
    if errors:
        raise BattlefieldError(errors, label)
    return instance


# ── 查询 ────────────────────────────────────────────────────────────────

def _cells(battlefield):
    cells = battlefield.get("cells") if isinstance(battlefield, dict) else None
    return cells if isinstance(cells, list) else []


def _cell_sort_key(cell):
    return (str(cell.get("surface_id")), str(cell.get("cell_id")))


def cell_by_id(battlefield, cell_id):
    """按稳定身份取格；找不到返回 `None`（不按坐标兜底）。"""
    for cell in _cells(battlefield):
        if isinstance(cell, dict) and cell.get("cell_id") == cell_id:
            return cell
    return None


def cells_at(battlefield, q, r):
    """同一 `q/r` 在**所有** surface 上的格，按 `(surface_id, cell_id)` 排序。"""
    found = [
        cell for cell in _cells(battlefield)
        if isinstance(cell, dict) and cell.get("q") == q and cell.get("r") == r
    ]
    return sorted(found, key=_cell_sort_key)


def cell_at(battlefield, q, r, surface_id):
    """指定 surface 上的 `(q, r)` 格；同一 surface 出现重号时取排序后的第一格。"""
    for cell in cells_at(battlefield, q, r):
        if cell.get("surface_id") == surface_id:
            return cell
    return None


def _resolve_footprint(battlefield, origin_cell_id, footprint):
    """把 footprint（相对 origin 的轴向偏移）解析到 origin 所在的 surface。

    返回 `(origin_cell, [(dq, dr, q, r, cell_or_None), …])`，保持 footprint 顺序。
    origin 不存在时返回 `(None, [])`；偏移本身非法时抛 `ValueError`。
    """
    origin = cell_by_id(battlefield, origin_cell_id)
    if origin is None:
        return None, []
    surface = origin.get("surface_id")
    base_q = origin.get("q")
    base_r = origin.get("r")
    resolved = []
    for offset in footprint:
        dq, dr = tactical_hex.axial(offset)
        q = base_q + dq
        r = base_r + dr
        resolved.append((dq, dr, q, r, cell_at(battlefield, q, r, surface)))
    return origin, resolved


def footprint_cells(battlefield, origin_cell_id, footprint):
    """footprint 覆盖的格，按 `(surface_id, cell_id)` 排序返回。

    `footprint` 是相对 origin 的轴向偏移列表（`[{"q": 0, "r": 0}, …]`）。
    只在 origin 所在 surface 上解析：另一层的同 `q/r` 不会被当成同一体积（02C:21）。
    未知 origin 或任一偏移越界抛 `ValueError`——需要逐格诊断请用 `validate_placement`。
    """
    origin, resolved = _resolve_footprint(battlefield, origin_cell_id, footprint)
    if origin is None:
        raise ValueError("未知 origin cell_id: %r" % (origin_cell_id,))
    missing = [(q, r) for dq, dr, q, r, cell in resolved if cell is None]
    if missing:
        raise ValueError("footprint 越界：%r 在 surface %r 上没有 (q, r)=%r"
                         % (origin_cell_id, origin.get("surface_id"), missing[0]))
    return sorted([item[4] for item in resolved], key=_cell_sort_key)


def validate_placement(battlefield, unit_id, origin, footprint, occupied=None):
    """校验 `unit_id` 以 `footprint` 落在 `origin`（cell_id）是否合法。

    `occupied` 是可选映射 `cell_id -> 占用者 id`（单位或对象），用于互斥占用诊断。
    返回结构化错误列表（按 `(q, r, code, cell_id)` 确定性排序），空列表 = 可放置。
    每项含 `code` / `cell_id` / `q` / `r` / `message`；越界格没有 `cell_id`
    （02C:21 禁止由坐标生成 id），改用 origin + 偏移定位。
    """
    if not _is_non_empty_str(unit_id):
        raise ValueError("unit_id 必须是非空字符串")
    origin_cell, resolved = _resolve_footprint(battlefield, origin, footprint)
    if origin_cell is None:
        return [{
            "code": "unknown_origin",
            "cell_id": origin,
            "q": None,
            "r": None,
            "message": "origin 不是本战场的 cell_id: %r" % (origin,),
        }]
    if not resolved:
        return [{
            "code": "empty_footprint",
            "cell_id": origin,
            "q": origin_cell.get("q"),
            "r": origin_cell.get("r"),
            "message": "footprint 不能为空",
        }]
    occupied = occupied or {}
    errors = []
    for dq, dr, q, r, cell in resolved:
        if cell is None:
            errors.append({
                "code": "out_of_bounds",
                "cell_id": None,
                "q": q,
                "r": r,
                "message": "origin %s 的偏移 (dq=%d, dr=%d) → (q=%d, r=%d) 在 surface %r 上没有格"
                           % (origin, dq, dr, q, r, origin_cell.get("surface_id")),
            })
            continue
        cell_id = cell.get("cell_id")
        if cell.get("blocks_movement"):
            errors.append({
                "code": "blocked_movement",
                "cell_id": cell_id,
                "q": q,
                "r": r,
                "message": "%s: blocks_movement=true，不能进入" % cell_id,
            })
        occupant = occupied.get(cell_id)
        if occupant is not None and occupant != unit_id:
            errors.append({
                "code": "occupied",
                "cell_id": cell_id,
                "q": q,
                "r": r,
                "message": "%s: 已被 %r 占用（实心占格互斥，02C:65）" % (cell_id, occupant),
            })
    return sorted(errors, key=lambda item: (
        item["q"] if item["q"] is not None else 0,
        item["r"] if item["r"] is not None else 0,
        item["code"],
        item["cell_id"] or "",
    ))


# ── 口径访问（metric / abstract 各自独立，绝不互算） ────────────────────────

def scale_mode(battlefield):
    """战场的口径：`metric` / `abstract`（缺 scale 时返回 `None`）。"""
    scale = battlefield.get("scale") if isinstance(battlefield, dict) else None
    return scale.get("mode") if isinstance(scale, dict) else None


def metric_elevation_cm(battlefield, cell):
    """metric 口径的格高（整数厘米，可为负）；**只**接受 metric 战场。

    abstract 战场调用直接报错——厘米与层级之间没有隐式换算（02C:39 / 02C:47）。
    """
    mode = scale_mode(battlefield)
    if mode != "metric":
        raise ValueError("metric_elevation_cm 只适用于 metric 战场（实为 %r）" % (mode,))
    return cell.get("elevation_cm")


def abstract_elevation_level(battlefield, cell):
    """abstract 口径的格层（整数层级）；**只**接受 abstract 战场，不做跨口径换算。"""
    mode = scale_mode(battlefield)
    if mode != "abstract":
        raise ValueError("abstract_elevation_level 只适用于 abstract 战场（实为 %r）" % (mode,))
    return cell.get("elevation_level")


# ── 确定性 fixture 工厂（供 #418 / #419 / #420 与回归测试复用） ─────────────

def _slug(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-") or "x"


def _hex_disk(radius):
    """半径内的完整六角盘，按 `(q, r)` 排序（确定性遍历顺序）。"""
    coords = []
    for q in range(-radius, radius + 1):
        low = max(-radius, -q - radius)
        high = min(radius, -q + radius)
        for r in range(low, high + 1):
            coords.append((q, r))
    return tuple(sorted(coords))


def fixture_battlefield(seed=417, *, mode="metric", radius=1, surfaces=("ground",)):
    """按显式 seed 生成一张最小的、可直接校验通过的战场。

    - 只由 `random.Random(seed)` 取样，且按排序后的坐标依次取样，所以同 seed 同输出；
    - 每层 surface 是半径 `radius` 的六角盘；`cell_id` 用顺序编号
      （`cell-<surface>-NN`），**不**由坐标拼出（02C:21 要求 id 不依赖坐标）；
    - metric 的格尺寸、高度步长与移动成本都从 `tactical_space.json` 派生，
      abstract 用层级与格 / 步，二者结构互不换算；
    - 每层固定保留一格 `blocks_movement` / `blocks_los`，便于阻挡与视线回归；
    - 多层时在相邻两层的原点格之间挂一条 `stairs` 垂直连接。
    """
    if not _is_int(radius) or radius < 0:
        raise ValueError("radius 必须是不小于 0 的整数（实为 %r）" % (radius,))
    surfaces = tuple(surfaces)
    if not surfaces or not all(_is_non_empty_str(item) for item in surfaces):
        raise ValueError("surfaces 必须是非空的 surface 名列表")
    rules = space_rules()
    if mode not in rules["scale"]["modes"]:
        raise ValueError("mode 必须是 %s 之一（实为 %r）"
                         % (" | ".join(rules["scale"]["modes"]), mode))

    is_metric = mode == "metric"
    hex_size = rules["scale"]["hex_size_cm"] if is_metric else 1
    height_step = max(1, hex_size // 10)
    coords = _hex_disk(radius)
    blocked_coord = (radius, 0) if radius else None
    rng = random.Random(seed)

    cells = []
    origin_ids = {}
    for floor_index, surface in enumerate(surfaces):
        base = floor_index * hex_size
        prefix = "cell-%s-" % _slug(surface)
        for order, (q, r) in enumerate(coords):
            terrain = rng.choice(_FIXTURE_TERRAINS)
            height_roll = rng.randrange(0, 4)
            level_roll = rng.randrange(0, 2)
            difficult = rng.random() < 0.3
            blocked = (q, r) == blocked_coord
            cell = {
                "cell_id": "%s%02d" % (prefix, order),
                "q": q,
                "r": r,
                "surface_id": surface,
                "terrain_id": terrain,
                "terrain_tags": ["difficult"] if difficult else ["open"],
                "movement_cost": (2 if difficult else 1) * hex_size,
                "blocks_movement": blocked,
                "blocks_los": blocked,
                "hazard_refs": [],
                "feature_refs": [],
                "spawnable": not blocked,
            }
            if is_metric:
                cell["elevation_cm"] = base + height_roll * height_step
            else:
                cell["elevation_level"] = base + level_roll
            if (q, r) == (0, 0):
                origin_ids[surface] = cell["cell_id"]
            cells.append(cell)

    links = []
    for index in range(1, len(surfaces)):
        links.append({
            "link_id": "vl-%s-%s" % (_slug(surfaces[index - 1]), _slug(surfaces[index])),
            "from_cell_id": origin_ids[surfaces[index - 1]],
            "to_cell_id": origin_ids[surfaces[index]],
            "kind": "stairs",
            "movement_cost": hex_size,
            "bidirectional": True,
            "requirements": [],
        })

    battlefield = {
        "schema_version": SCHEMA_VERSION,
        "kind": BATTLEFIELD_KIND,
        "source": "docs/system/02c-hex-battlefield.md:29",
        "battlefield_id": "bf-fixture-%s" % _slug(seed),
        "world_id": "fixture",
        "site_id": "site-fixture",
        "atlas_ref": "fixture/region-00",
        "seed": seed,
        "generator_version": "fixture-1",
        "geometry": {"type": "hex_axial"},
        "cells": cells,
        "vertical_links": links,
    }
    if is_metric:
        battlefield["scale"] = {"mode": "metric", "hex_size_cm": hex_size}
    else:
        battlefield["scale"] = {"mode": "abstract",
                                "unit": rules["scale"]["abstract_units"][0]}
    return battlefield
