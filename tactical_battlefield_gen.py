#!/usr/bin/env python3
"""六角格战场生成器（Issue #418，M13-B3）。

把 ATLAS 的地点、房间/场景要素和 seed，按**调用方传入的模板**转换成可玩、
可复现、可校验的局部六角格战场。ATLAS 的三层空间模型不被替换：本模块只从
ATLAS 场景 dict 里**读取**地点与要素，产出 `tactical.battlefield`（#416 Schema）
与战场对象**实例**；对象状态机、事件、revision 归 #421，本模块不实现。

──────────────────────────────────────────────────────────────────────────
## 零、边界（缺一不可）

- 零第三方、无 HTTP、**不写文件**、不 import `atlas*` / `prism_core` / `tests/*`；
- 只读消费 ATLAS：不改写传入的 scene / template dict，不回写任何六角格实现；
- 几何只走 `tactical_hex`（邻格 / 距离），战场格式只走 `tactical_battlefield`
  （`load_battlefield` 终校验、`space_rules()` 取阈值），可达性复用
  `tactical_movement.plan_move`（#419）——本模块不复制第二套几何 / 寻路 / 换算；
- 朝向：本模块只做格位布局，不做格中心坐标换算，因此**不涉及** 02C 第九节
  的朝向基准式（如需中心坐标由消费方走 `tactical_queries` 的纯函数）；
- 不读世界长文、不调用模型、不按 world key / 地形名 / 物件名写分支：
  所有差异只由模板与场景数据表达；
- **不落 `data/**`**（Issue #418 追加节）：模板字段表以 02C 第十一节为权威
  （#441 落文），冻结在本 docstring 与回归测试里；调用方以 dict 传入模板。
  「模板转正为数据」由后续独立 docs+data 单完成，本单不预做、不占位。

## 一、模板契约（字段表权威 = `docs/system/02c-hex-battlefield.md` 第十一节）

模板是**布局意图**声明，不是规则数据：不得内嵌任何阈值（坡面上限、坠落档、
高地阈值等一律引用 `system.tactical_space`，本模块逐项校验、不复制数值）。
同一 `template_id` + `template_version` + `seed` + `mode` 必须复现同一战场。

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `template_id` | str | 是 | 模板身份；进 `battlefield_id` 前缀 |
| `template_version` | str | 是 | 模板版本；进 `generator_version`，版本变化显式可见 |
| `shape` | dict | 是 | `{"kind": "room" / "outdoor" / "custom"}`，细则见 1.1 |
| `size` | dict | room 必填 | `{"width": 1..32, "height": 1..32}`（q / r 方向跨度） |
| `radius` | int | outdoor 必填 | 1..12，六角盘半径（`tactical_hex.hex_distance`） |
| `surfaces[]` | list | 否 | 1–3 层，缺省单层 `ground`；细则见 1.2 |
| `entry_cells[]` | list | 否 | 入口格集合 `{"q","r"}`（可带 `surface_id`，缺省首层） |
| `spawn_points[]` | list | 否 | 出生点集合 `{"q","r"}`（可带 `surface_id`，缺省首层）；受 02C 第三节不变式约束：不得不可达 / 重叠 / 必然无路 |
| `features[]` | list | 否 | 布局特征 + 场景要素映射，细则见 1.3 |
| `encounter_meta` | dict | 否 | 遭遇元数据，**原样透传**到结果，不参与几何判定 |

未知字段一律报 `unknown_field`（与 Schema `additionalProperties=false` 同口径）。
生成器容量上限（非规则阈值，属冻结的复现契约）：宽 / 高 ≤ 32、radius ≤ 12、
surfaces 1–3、features ≤ 128、单个 feature 的 `count` ≤ 64。

### 1.1 `shape`

- `room`：矩形房间，`q ∈ [0, width)`、`r ∈ [0, height)`，不剪枝；
- `outdoor`：半径 `radius` 的完整六角盘，再按 seed 从**边界环**确定性剪除
  约三分之一（`ceil(len/3)`：排序后 `rng.shuffle` 取前缀）——这是生成器策略、
  属冻结复现契约，**不是**规则阈值；剪除后只保留含锚点 `(0, 0)` 的连通分量，
  所以「断路」在成型阶段就不会出现；剪掉的格数记在
  `diagnostics.counts.cells_pruned`。irregular board 由此而来；
- `custom`：`shape.coords` 给出显式格位列表 `[{q,r},…]`（去重，≥ 1）。

### 1.2 `surfaces[]`（1–3 层）

| 字段 | 类型 | 缺省 | 说明 |
|---|---|---|---|
| `surface_id` | str | — | 层标识，进 `cells[].surface_id`；各层不得重名 |
| `elevation` | int | `0` | 该层基准高：metric 为厘米（`elevation_cm`）、abstract 为层级（`elevation_level`），口径随 `mode` 参数 |
| `elevation_step` | int | `0` | 层内逐格起伏步长；metric 必须 ≤ `system.tactical_space.movement.slope_max_cm`、abstract 必须 ≤ `tactical_movement.ABSTRACT_SLOPE_MAX_LEVEL`，否则报 `invalid_height`（阈值引用规则数据，不在模板内嵌） |
| `link` | dict | — | 仅第 2+ 层可带：`{"kind": <vertical_link.kind>, "at": {"q","r"}, "bidirectional": bool?}`，声明本层与上一层在同 `(q,r)` 格的垂直连接；`kind` 按规则数据 `vertical_links.kinds` 校验；`movement_cost` 取一格距离（02C:92） |

各层共享同一套 `(q, r)` 格位（`surface_id` 区分身份，02C:21）。层内起伏由
seed 确定性取样（每格 `0` 或 `+elevation_step`），相邻格高差因此不超过步长。

### 1.3 `features[]`（布局特征 + 场景要素映射）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `feature_id` | str | 是 | 模板内唯一 |
| `kind` | str | 是 | `"terrain"`（改写格地形字段）/ `"object"`（产出对象实例） |
| `source_feature` | str | 否 | ATLAS 场景要素 tag；给出时**每个匹配的场景要素实例落地一次**（0..N 次），没有匹配则不落地 |
| `at` | dict | 否 | 显式格位 `{"q","r"}` |
| `disk` | dict | 否 | 显式区域 `{"center": {"q","r"}, "radius": ≥0}`（`hex_distance ≤ radius`） |
| `count` | int | 否 | 自动放置个数（缺省 1；仅当无 `at` / `disk` 且无 `source_feature`） |
| `surface_id` | str | 否 | 缺省首层 |
| `terrain` | dict | terrain 必填 | `terrain_id` 必填；可选 `terrain_tags` / `blocks_movement` / `blocks_los` / `cover_grade`（按规则数据四档校验）/ `obstacle_height`（≥0 整数）/ `hazard_refs` |
| `object` | dict | object 必填 | `definition_id` 必填、`category` 必填（02C:308 九类受控枚举）；可选 `state`（缺省 `"intact"`）/ `blocks_movement` / `blocks_los` / `cover_grade` |

放置语义（确定性）：

- 显式 `at` / `disk`：**锚定**——同模板同格位，seed 不改变它们；
  目标格不存在 / 已被占 / （object 时）不可进入或压住入口 / 出生点 → 报错；
- 无 `at` / `disk` 且无 `source_feature`：**自动放置**——seed 从该层
  「可进入、无对象、非入口 / 出生点」的格中确定性取样 `count` 格，
  不同 seed 由此合理改变非锚定细节；
- 带 `source_feature`：按合并后的场景要素顺序逐实例落地；显式区域放不下时
  记入 `diagnostics.unplaced_features`（**受控降级**，不悄悄丢弃也不报错）；
  场景要素没有任何匹配声明时同样记入 `unmapped_features`；
- `terrain` 特征落地时把 `feature_id` 写进受影响格的 `feature_refs`；
  `object` 实例携带 `feature_refs`（命中的场景要素引用，保留来源可追溯）。

## 二、场景输入（ATLAS 只读快照，普通 dict）

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `world_id` | str | 是 | 写入 `battlefield.world_id`（ATLAS 的 world key 只透传，不分支） |
| `site_id` | str | 否 | 写入 `battlefield.site_id` |
| `atlas_ref` | str | 否 | ATLAS 来源引用，缺省取 `place.id` |
| `place` | dict | 是 | ATLAS 地点；只读 `id`（必填）/ `name` / `kind` / `traits` / `features` |
| `features` | list | 否 | 场景级追加要素，与 `place.features` 合并（先 place 后 scene） |

场景要素是 `{"tag": str, "text": str?}`（`atlas_gen._add_features` 的产出形态）；
要素如何变成地形与对象**只由模板 `features[]` 的 `source_feature` 声明决定**，
代码不认识任何具体 tag 字符串。

## 三、`mode` 与 seed

- `mode`（`"metric"` / `"abstract"`）是 `generate_battlefield` 的关键字参数
  （缺省 `"metric"`），**不是模板字段**——02C 第十一节字段表没有口径项，
  口径由战斗接线方决定；它决定 `scale`、高度字段与容量校验口径；
- `seed`：整数或非空字符串，透传 `battlefield.seed`；随机只走
  `random.Random(seed)`，消费顺序固定（surfaces 起伏 → outdoor 剪枝 →
  features 依声明顺序），同输入同输出；
- `generator_version = "tactical_battlefield_gen-<版本>+tpl-<template_version>"`，
  模板版本变更在战场数据上显式可见。

## 四、结果与错误

成功返回：

```
{
  "battlefield": <通过 tactical_battlefield.load_battlefield 的战场 dict>,
  "objects": [对象实例 dict（object_id / definition_id / category / cell_id /
              surface_id / state / blocks_movement / blocks_los / cover_grade /
              feature_refs）],
  "encounter_meta": <模板透传>,
  "diagnostics": {
    "counts": {"cells": n, "objects": n, "features_placed": n,
               "features_unplaced": n, "cells_pruned": n},
    "unplaced_features": [{"source": <feature_id>, "tag": <tag>, "reason": str}],
    "unmapped_features": [{"tag": str, "index": n}],
    "notes": [str],
  },
}
```

模板 / 场景 / 布局约束无法满足时抛 `BattlefieldGenError`（`ValueError` 子类），
`.errors` 是结构化错误列表（`code` / `path` / `message`），常用 code：
`unknown_field` / `missing_field` / `invalid_type` / `out_of_range` /
`duplicate` / `unknown_surface` / `cell_not_on_board` / `entry_blocked` /
`spawn_blocked` / `overlap` / `spawn_conflict` / `invalid_height` /
`spawn_unreachable` / `no_cells`。**不悄悄生成无效地图。**

## 五、生成管线

1. 校验模板与场景（未知字段、范围、枚举全部落错）；
2. 由 `shape` 成型格位集（outdoor 含 seed 剪枝 + 连通分量保留）；
3. 逐层生成格：`cell_id` 用顺序编号 `cell-<surface>-<NNN>`（**不由坐标拼出**，
   02C:21）、高度 = 层基准 + seed 起伏、统一一格移动成本（02C:92，metric
   为 `hex_size_cm`、abstract 为 1，读规则数据 / 战场 scale，不写字面量）；
4. 落 `features[]`（显式 → 自动 → 场景映射，全部确定性）；
5. 入口 / 出生点校验：格存在、可进入、互不冲突、不与对象冲突；
6. 可达性：复用 `tactical_movement.plan_move`（授权全部 link kind——生成期
   只问「有没有路」，授权语义归运行时）从首个入口（无入口则首个出生点）
   出发，检查全部入口与出生点可达（预算 = 全图成本上界）；
7. `tactical_battlefield.load_battlefield` 终校验后随结果返回。
"""
import random
import re

import tactical_battlefield
import tactical_hex
import tactical_movement
from tactical_movement import ABSTRACT_SLOPE_MAX_LEVEL

__all__ = [
    "GENERATOR_VERSION",
    "OBJECT_CATEGORIES",
    "MAX_SURFACES",
    "BattlefieldGenError",
    "generate_battlefield",
]

# 生成器版本（复现契约的一部分：版本或模板版本变化都体现在
# battlefield.generator_version 上，显式可见）。
GENERATOR_VERSION = "1.0.0"

# 02C:308 九类对象 category 受控枚举（冻结于 02C 第十二节，随 #421 数据化）。
OBJECT_CATEGORIES = (
    "blocker", "cover", "hazard", "mechanism", "boost",
    "mobility", "pickup", "movable", "destructible",
)

# 生成器容量上限（冻结复现契约，非规则阈值）。
MAX_SIZE = 32
MAX_RADIUS = 12
MAX_SURFACES = 3
MAX_FEATURES = 128
MAX_AUTO_COUNT = 64

_TEMPLATE_FIELDS = (
    "template_id", "template_version", "shape", "size", "radius",
    "surfaces", "entry_cells", "spawn_points", "features", "encounter_meta",
)
_SHAPE_FIELDS = ("kind", "coords")
_SURFACE_FIELDS = ("surface_id", "elevation", "elevation_step", "link")
_LINK_FIELDS = ("kind", "at", "bidirectional")
_FEATURE_FIELDS = (
    "feature_id", "kind", "source_feature", "at", "disk", "count",
    "surface_id", "terrain", "object",
)
_TERRAIN_FIELDS = (
    "terrain_id", "terrain_tags", "blocks_movement", "blocks_los",
    "cover_grade", "obstacle_height", "hazard_refs",
)
_OBJECT_FIELDS = (
    "definition_id", "category", "state", "blocks_movement", "blocks_los",
    "cover_grade",
)
_SHAPE_KINDS = ("room", "outdoor", "custom")

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


class BattlefieldGenError(ValueError):
    """模板 / 场景 / 布局约束无法满足；`errors` 是结构化错误列表。"""

    def __init__(self, errors):
        self.errors = list(errors)
        detail = "; ".join(
            "[%s] %s: %s" % (e.get("code"), e.get("path"), e.get("message"))
            for e in self.errors
        )
        super().__init__(detail or "战场生成失败")


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _is_non_empty_str(value):
    return isinstance(value, str) and bool(value)


def _slug(text):
    return _SLUG_RE.sub("-", str(text)).strip("-") or "x"


def _err(code, path, message):
    return {"code": code, "path": path, "message": message}


def _as_pair(point):
    """`{"q","r"}` → `(q, r)`；形状不对返回 `None`。"""
    if isinstance(point, dict) and "q" in point and "r" in point:
        if _is_int(point["q"]) and _is_int(point["r"]):
            return (point["q"], point["r"])
    if (isinstance(point, (tuple, list)) and len(point) == 2
            and _is_int(point[0]) and _is_int(point[1])):
        return (point[0], point[1])
    return None


# ── 模板 / 场景校验 ──────────────────────────────────────────────────────

def _check_template(template, mode, rules, errors):
    """结构性校验；通过的部分归一成内部 spec（不修改调用方 dict）。"""
    if not isinstance(template, dict):
        errors.append(_err("invalid_type", "$", "模板必须是 dict"))
        return None
    for key in sorted(set(template) - set(_TEMPLATE_FIELDS)):
        errors.append(_err("unknown_field", "$.%s" % key,
                           "模板未知字段（02C 第十一节字段表之外）"))
    for key in ("template_id", "template_version"):
        if key not in template:
            errors.append(_err("missing_field", "$.%s" % key, "模板缺少必填字段"))
        elif not _is_non_empty_str(template[key]):
            errors.append(_err("invalid_type", "$.%s" % key, "必须是非空字符串"))
    if errors:
        return None

    spec = {
        "template_id": template["template_id"],
        "template_version": template["template_version"],
        "mode": mode,
        "encounter_meta": template.get("encounter_meta", {}),
        "surfaces": None,
        "entry_cells": [],
        "spawn_points": [],
        "features": [],
    }
    if not isinstance(template.get("encounter_meta", {}), dict):
        errors.append(_err("invalid_type", "$.encounter_meta", "必须是 dict"))

    _check_shape(template, spec, errors)
    _check_surfaces(template, spec, rules, errors)
    _check_points(template, spec, "entry_cells", errors)
    _check_points(template, spec, "spawn_points", errors)
    _check_features(template, spec, rules, errors)
    if errors:
        return None
    return spec


def _check_shape(template, spec, errors):
    shape = template.get("shape")
    if not isinstance(shape, dict):
        errors.append(_err("invalid_type", "$.shape", "必须是 dict"))
        return
    for key in sorted(set(shape) - set(_SHAPE_FIELDS)):
        errors.append(_err("unknown_field", "$.shape.%s" % key, "未知字段"))
    kind = shape.get("kind")
    if kind not in _SHAPE_KINDS:
        errors.append(_err("invalid_type", "$.shape.kind",
                           "必须是 %s 之一（02C 第十一节受控枚举）"
                           % " | ".join(_SHAPE_KINDS)))
        return
    spec["shape_kind"] = kind
    if kind == "room":
        size = template.get("size")
        if not isinstance(size, dict):
            errors.append(_err("missing_field", "$.size", "room 模板必须给 size"))
            return
        for key in sorted(set(size) - {"width", "height"}):
            errors.append(_err("unknown_field", "$.size.%s" % key, "未知字段"))
        dims = []
        for key in ("width", "height"):
            value = size.get(key)
            if not _is_int(value) or not 1 <= value <= MAX_SIZE:
                errors.append(_err("out_of_range", "$.size.%s" % key,
                                   "必须是 1..%d 的整数（实为 %r）" % (MAX_SIZE, value)))
            dims.append(value)
        if len(dims) == 2 and all(_is_int(d) for d in dims):
            spec["size"] = (dims[0], dims[1])
    elif kind == "outdoor":
        radius = template.get("radius")
        if not _is_int(radius) or not 1 <= radius <= MAX_RADIUS:
            errors.append(_err("out_of_range", "$.radius",
                               "必须是 1..%d 的整数（实为 %r）" % (MAX_RADIUS, radius)))
        else:
            spec["radius"] = radius
    else:  # custom
        coords = shape.get("coords")
        if not isinstance(coords, list) or not coords:
            errors.append(_err("missing_field", "$.shape.coords",
                               "custom 模板必须给非空 coords 列表"))
            return
        seen = []
        for index, point in enumerate(coords):
            pair = _as_pair(point)
            if pair is None:
                errors.append(_err("invalid_type", "$.shape.coords[%d]" % index,
                                   "必须是 {q, r} 整数格位"))
                continue
            if pair not in seen:
                seen.append(pair)
        if seen:
            spec["coords"] = sorted(seen)


def _check_surfaces(template, spec, rules, errors):
    surfaces = template.get("surfaces")
    if surfaces is None:
        spec["surfaces"] = [{"surface_id": "ground", "elevation": 0,
                             "elevation_step": 0, "link": None}]
        return
    if not isinstance(surfaces, list) or not surfaces:
        errors.append(_err("invalid_type", "$.surfaces",
                           "必须是非空数组（1..%d 层）" % MAX_SURFACES))
        return
    if len(surfaces) > MAX_SURFACES:
        errors.append(_err("out_of_range", "$.surfaces",
                           "最多 %d 层（实为 %d）" % (MAX_SURFACES, len(surfaces))))
        return
    slope_max = rules.get("movement", {}).get("slope_max_cm")
    specs = []
    seen_ids = set()
    for index, surface in enumerate(surfaces):
        path = "$.surfaces[%d]" % index
        if not isinstance(surface, dict):
            errors.append(_err("invalid_type", path, "必须是 dict"))
            continue
        for key in sorted(set(surface) - set(_SURFACE_FIELDS)):
            errors.append(_err("unknown_field", "%s.%s" % (path, key), "未知字段"))
        surface_id = surface.get("surface_id")
        if not _is_non_empty_str(surface_id):
            errors.append(_err("invalid_type", "%s.surface_id" % path,
                               "必须是非空字符串"))
        elif surface_id in seen_ids:
            errors.append(_err("duplicate", "%s.surface_id" % path,
                               "surface_id 重复：%r" % surface_id))
        else:
            seen_ids.add(surface_id)
        elevation = surface.get("elevation", 0)
        if not _is_int(elevation):
            errors.append(_err("invalid_type", "%s.elevation" % path,
                               "必须是整数（实为 %r）" % (elevation,)))
        step = surface.get("elevation_step", 0)
        if not _is_int(step) or step < 0:
            errors.append(_err("invalid_type", "%s.elevation_step" % path,
                               "必须是不小于 0 的整数（实为 %r）" % (step,)))
        elif step > 0:
            if spec["mode"] == "metric":
                if not _is_int(slope_max):
                    errors.append(_err("invalid_height", "%s.elevation_step" % path,
                                       "规则数据缺少 movement.slope_max_cm，"
                                       "无法校验坡面步长"))
                elif step > slope_max:
                    errors.append(_err(
                        "invalid_height", "%s.elevation_step" % path,
                        "metric 层内步长 %d 超过 system.tactical_space"
                        ".movement.slope_max_cm=%d（02C:94，阈值只引用不内嵌）"
                        % (step, slope_max)))
            elif step > ABSTRACT_SLOPE_MAX_LEVEL:
                errors.append(_err(
                    "invalid_height", "%s.elevation_step" % path,
                    "abstract 层内步长必须 ≤ %d 层（02C:39 格 / 步口径）"
                    % ABSTRACT_SLOPE_MAX_LEVEL))
        link = None
        if "link" in surface:
            link = _check_link(surface["link"], "%s.link" % path, rules, errors,
                               is_first=(index == 0))
        specs.append({"surface_id": surface_id, "elevation": elevation,
                      "elevation_step": step, "link": link})
    if specs and len(specs) == len(surfaces):
        spec["surfaces"] = specs


def _check_link(link, path, rules, errors, is_first):
    if not isinstance(link, dict):
        errors.append(_err("invalid_type", path, "必须是 dict"))
        return None
    for key in sorted(set(link) - set(_LINK_FIELDS)):
        errors.append(_err("unknown_field", "%s.%s" % (path, key), "未知字段"))
    if is_first:
        errors.append(_err("invalid_type", path, "第一层不能声明 link（连接指向上一层）"))
        return None
    kind = link.get("kind")
    kinds = rules.get("vertical_links", {}).get("kinds")
    if not isinstance(kinds, list):
        errors.append(_err("invalid_height", "%s.kind" % path,
                           "规则数据缺少 vertical_links.kinds，无法校验连接类型"))
    elif kind not in kinds:
        errors.append(_err("invalid_type", "%s.kind" % path,
                           "必须是规则数据 vertical_links.kinds 之一（实为 %r）" % (kind,)))
    at = _as_pair(link.get("at"))
    if at is None:
        errors.append(_err("invalid_type", "%s.at" % path,
                           "必须是 {q, r} 整数格位"))
    bidirectional = link.get("bidirectional", True)
    if not isinstance(bidirectional, bool):
        errors.append(_err("invalid_type", "%s.bidirectional" % path, "必须是布尔值"))
    if kind is None or at is None:
        return None
    return {"kind": kind, "at": at, "bidirectional": bidirectional}


def _check_points(template, spec, field, errors):
    points = template.get(field)
    if points is None:
        return
    if not isinstance(points, list):
        errors.append(_err("invalid_type", "$.%s" % field, "必须是数组"))
        return
    seen = set()
    norm = []
    for index, point in enumerate(points):
        path = "$.%s[%d]" % (field, index)
        if not isinstance(point, dict):
            errors.append(_err("invalid_type", path, "必须是 {q, r[, surface_id]}"))
            continue
        for key in sorted(set(point) - {"q", "r", "surface_id"}):
            errors.append(_err("unknown_field", "%s.%s" % (path, key), "未知字段"))
        pair = _as_pair(point)
        if pair is None:
            errors.append(_err("invalid_type", path, "q / r 必须是整数"))
            continue
        surface_id = point.get("surface_id")
        if surface_id is not None and not _is_non_empty_str(surface_id):
            errors.append(_err("invalid_type", "%s.surface_id" % path,
                               "必须是非空字符串"))
            continue
        key = (pair, surface_id)
        if key in seen:
            errors.append(_err("duplicate", path, "重复的格位声明"))
            continue
        seen.add(key)
        norm.append({"at": pair, "surface_id": surface_id})
    if norm:
        spec[field] = norm


def _check_features(template, spec, rules, errors):
    features = template.get("features")
    if features is None:
        return
    if not isinstance(features, list):
        errors.append(_err("invalid_type", "$.features", "必须是数组"))
        return
    if len(features) > MAX_FEATURES:
        errors.append(_err("out_of_range", "$.features",
                           "最多 %d 条（实为 %d）" % (MAX_FEATURES, len(features))))
        return
    grades = rules.get("cover", {}).get("grades")
    seen_ids = set()
    norm = []
    for index, feature in enumerate(features):
        path = "$.features[%d]" % index
        if not isinstance(feature, dict):
            errors.append(_err("invalid_type", path, "必须是 dict"))
            continue
        for key in sorted(set(feature) - set(_FEATURE_FIELDS)):
            errors.append(_err("unknown_field", "%s.%s" % (path, key), "未知字段"))
        feature_id = feature.get("feature_id")
        if not _is_non_empty_str(feature_id):
            errors.append(_err("invalid_type", "%s.feature_id" % path,
                               "必须是非空字符串"))
        elif feature_id in seen_ids:
            errors.append(_err("duplicate", "%s.feature_id" % path,
                               "feature_id 重复：%r" % feature_id))
        else:
            seen_ids.add(feature_id)
        kind = feature.get("kind")
        if kind not in ("terrain", "object"):
            errors.append(_err("invalid_type", "%s.kind" % path,
                               "必须是 'terrain' 或 'object'（实为 %r）" % (kind,)))
            continue
        source_feature = feature.get("source_feature")
        if source_feature is not None and not _is_non_empty_str(source_feature):
            errors.append(_err("invalid_type", "%s.source_feature" % path,
                               "必须是非空字符串"))
        at = _as_pair(feature["at"]) if "at" in feature else None
        if "at" in feature and at is None:
            errors.append(_err("invalid_type", "%s.at" % path,
                               "必须是 {q, r} 整数格位"))
        disk = None
        if "disk" in feature:
            disk_spec = feature["disk"]
            if not isinstance(disk_spec, dict):
                errors.append(_err("invalid_type", "%s.disk" % path,
                                   "必须是 {center: {q,r}, radius: ≥0}"))
            else:
                for key in sorted(set(disk_spec) - {"center", "radius"}):
                    errors.append(_err("unknown_field", "%s.disk.%s" % (path, key),
                                       "未知字段"))
                center = _as_pair(disk_spec.get("center"))
                radius = disk_spec.get("radius")
                if center is None or not _is_int(radius) or radius < 0:
                    errors.append(_err("invalid_type", "%s.disk" % path,
                                       "center 必须是 {q,r}，radius 必须是 ≥0 整数"))
                else:
                    disk = {"center": center, "radius": radius}
        count = feature.get("count", 1)
        if not _is_int(count) or not 1 <= count <= MAX_AUTO_COUNT:
            errors.append(_err("out_of_range", "%s.count" % path,
                               "必须是 1..%d 的整数（实为 %r）" % (MAX_AUTO_COUNT, count)))
        surface_id = feature.get("surface_id")
        if surface_id is not None and not _is_non_empty_str(surface_id):
            errors.append(_err("invalid_type", "%s.surface_id" % path,
                               "必须是非空字符串"))
        terrain = None
        obj = None
        if kind == "terrain":
            terrain = feature.get("terrain")
            if not isinstance(terrain, dict):
                errors.append(_err("missing_field", "%s.terrain" % path,
                                   "kind=terrain 必须给 terrain 声明"))
            else:
                for key in sorted(set(terrain) - set(_TERRAIN_FIELDS)):
                    errors.append(_err("unknown_field", "%s.terrain.%s" % (path, key),
                                       "未知字段"))
                if not _is_non_empty_str(terrain.get("terrain_id")):
                    errors.append(_err("invalid_type", "%s.terrain.terrain_id" % path,
                                       "必须是非空字符串"))
                tags = terrain.get("terrain_tags", ["open"])
                if not (isinstance(tags, list)
                        and all(_is_non_empty_str(t) for t in tags)):
                    errors.append(_err("invalid_type", "%s.terrain.terrain_tags" % path,
                                       "必须是非空字符串数组"))
                for key in ("blocks_movement", "blocks_los"):
                    if key in terrain and not isinstance(terrain[key], bool):
                        errors.append(_err("invalid_type", "%s.terrain.%s" % (path, key),
                                           "必须是布尔值"))
                grade = terrain.get("cover_grade")
                if grade is not None:
                    if not isinstance(grades, list):
                        errors.append(_err("invalid_height", "%s.terrain.cover_grade" % path,
                                           "规则数据缺少 cover.grades，无法校验掩体档"))
                    elif grade not in grades:
                        errors.append(_err("invalid_type", "%s.terrain.cover_grade" % path,
                                           "必须是规则数据 cover.grades 之一（实为 %r）" % (grade,)))
                height = terrain.get("obstacle_height")
                if height is not None and (not _is_int(height) or height < 0):
                    errors.append(_err("invalid_type", "%s.terrain.obstacle_height" % path,
                                       "必须是不小于 0 的整数（实为 %r）" % (height,)))
                hazard_refs = terrain.get("hazard_refs", [])
                if not (isinstance(hazard_refs, list)
                        and all(_is_non_empty_str(h) for h in hazard_refs)):
                    errors.append(_err("invalid_type", "%s.terrain.hazard_refs" % path,
                                       "必须是非空字符串数组"))
        else:
            obj = feature.get("object")
            if not isinstance(obj, dict):
                errors.append(_err("missing_field", "%s.object" % path,
                                   "kind=object 必须给 object 声明"))
            else:
                for key in sorted(set(obj) - set(_OBJECT_FIELDS)):
                    errors.append(_err("unknown_field", "%s.object.%s" % (path, key),
                                       "未知字段"))
                if not _is_non_empty_str(obj.get("definition_id")):
                    errors.append(_err("invalid_type", "%s.object.definition_id" % path,
                                       "必须是非空字符串"))
                if obj.get("category") not in OBJECT_CATEGORIES:
                    errors.append(_err("invalid_type", "%s.object.category" % path,
                                       "必须是 02C:308 九类之一（实为 %r）"
                                       % (obj.get("category"),)))
                state = obj.get("state", "intact")
                if not _is_non_empty_str(state):
                    errors.append(_err("invalid_type", "%s.object.state" % path,
                                       "必须是非空字符串"))
                for key in ("blocks_movement", "blocks_los"):
                    if key in obj and not isinstance(obj[key], bool):
                        errors.append(_err("invalid_type", "%s.object.%s" % (path, key),
                                           "必须是布尔值"))
                grade = obj.get("cover_grade")
                if grade is not None:
                    if not isinstance(grades, list):
                        errors.append(_err("invalid_height", "%s.object.cover_grade" % path,
                                           "规则数据缺少 cover.grades，无法校验掩体档"))
                    elif grade not in grades:
                        errors.append(_err("invalid_type", "%s.object.cover_grade" % path,
                                           "必须是规则数据 cover.grades 之一（实为 %r）" % (grade,)))
        norm.append({
            "feature_id": feature_id, "kind": kind,
            "source_feature": source_feature, "at": at, "disk": disk,
            "count": count, "surface_id": surface_id,
            "terrain": terrain, "object": obj,
        })
    if len(norm) == len(features):
        spec["features"] = norm


def _check_scene(scene, errors):
    """场景只读快照校验 + 场景要素归一（不修改调用方 dict）。"""
    if not isinstance(scene, dict):
        errors.append(_err("invalid_type", "scene", "场景必须是 dict"))
        return None
    world_id = scene.get("world_id")
    if not _is_non_empty_str(world_id):
        errors.append(_err("missing_field", "scene.world_id",
                           "必须是非空字符串（写入 battlefield.world_id）"))
    place = scene.get("place")
    if not isinstance(place, dict) or not _is_non_empty_str(place.get("id")):
        errors.append(_err("missing_field", "scene.place.id",
                           "场景必须携带 ATLAS 地点（至少含非空 id）"))
        place = None
    merged = []
    for origin, features in (("place", (place or {}).get("features")),
                             ("scene", scene.get("features"))):
        if features is None:
            continue
        if not isinstance(features, list):
            errors.append(_err("invalid_type", "scene.%s.features" % origin,
                               "必须是数组"))
            continue
        for index, feature in enumerate(features):
            if not isinstance(feature, dict) or not _is_non_empty_str(feature.get("tag")):
                errors.append(_err(
                    "invalid_type", "scene.%s.features[%d].tag" % (origin, index),
                    "场景要素必须是非空 tag（atlas_gen 产出形态 {tag, text?}）"))
                continue
            merged.append({"tag": feature["tag"],
                           "text": feature.get("text"),
                           "ref": "feat-%s-%03d" % (origin, len(merged))})
    if errors:
        return None
    return {
        "world_id": world_id,
        "site_id": scene.get("site_id")
        if _is_non_empty_str(scene.get("site_id")) else None,
        "atlas_ref": scene.get("atlas_ref")
        if _is_non_empty_str(scene.get("atlas_ref")) else (place["id"] if place else None),
        "place_id": place["id"],
        "features": merged,
    }


# ── 格位成型 ─────────────────────────────────────────────────────────────

def _shape_coords(spec, rng, diagnostics):
    """按 shape 产出排序后的 `(q, r)` 格位列表（确定性）。"""
    kind = spec["shape_kind"]
    if kind == "room":
        width, height = spec["size"]
        return sorted((q, r) for q in range(width) for r in range(height))
    if kind == "custom":
        return list(spec["coords"])
    radius = spec["radius"]
    disk = tactical_battlefield._hex_disk(radius)
    # 剪枝只动边界环（到中心 hex_distance == radius 的格）；中心永在。
    ring = sorted(c for c in disk
                  if tactical_hex.hex_distance(c, (0, 0)) == radius)
    order = list(ring)
    rng.shuffle(order)
    prune_count = -(-len(ring) // 3)  # ceil(len/3)：生成器策略，冻结复现契约
    pruned = set(order[:prune_count])
    kept_set = set(disk) - pruned
    # 只保留含锚点 (0, 0) 的连通分量 → 成型阶段即无断路。
    component = set()
    stack = [(0, 0)]
    while stack:
        cell = stack.pop()
        if cell in component:
            continue
        component.add(cell)
        for neighbor in tactical_hex.neighbors(cell):
            if neighbor in kept_set and neighbor not in component:
                stack.append(neighbor)
    kept = [c for c in kept_set if c in component]
    diagnostics["counts"]["cells_pruned"] = len(disk) - len(kept)
    return sorted(kept)


# ── 主入口 ──────────────────────────────────────────────────────────────

def generate_battlefield(scene, template, seed, *, mode="metric", rules=None):
    """按模板与场景生成一张可复现的六角格战场（结果与校验契约见模块 docstring）。

    - `scene`：ATLAS 场景只读快照（模块 docstring 第二节）；
    - `template`：战场模板 dict（第一节，02C 第十一节字段表）；
    - `seed`：整数或非空字符串；
    - `mode`：`"metric"` / `"abstract"`（口径归战斗接线方，不是模板字段）；
    - `rules`：`system.tactical_space`，缺省读 `tactical_battlefield.space_rules()`。

    约束无法满足时抛 `BattlefieldGenError`；场景要素放不下走受控降级
    （`diagnostics.unplaced_features` / `unmapped_features`），不悄悄丢弃。
    """
    if rules is None:
        rules = tactical_battlefield.space_rules()
    if mode not in rules["scale"]["modes"]:
        raise BattlefieldGenError([_err(
            "invalid_type", "mode",
            "必须是规则数据 scale.modes 之一（实为 %r）" % (mode,))])
    if not (_is_int(seed) or _is_non_empty_str(seed)):
        raise BattlefieldGenError([_err(
            "invalid_type", "seed", "必须是整数或非空字符串（实为 %r）" % (seed,))])

    errors = []
    spec = _check_template(template, mode, rules, errors)
    scene_spec = _check_scene(scene, errors)
    if errors:
        raise BattlefieldGenError(errors)

    rng = random.Random(seed)
    diagnostics = {
        "counts": {"cells": 0, "objects": 0, "features_placed": 0,
                   "features_unplaced": 0, "cells_pruned": 0},
        "unplaced_features": [],
        "unmapped_features": [],
        "notes": [],
    }
    coords = _shape_coords(spec, rng, diagnostics)
    if not coords:
        raise BattlefieldGenError([_err("no_cells", "$.shape", "成型后的格位集为空")])

    cells, links, surfaces = _build_cells_and_links(spec, coords, rng, rules, errors)
    if errors:
        raise BattlefieldGenError(errors)

    index = _CellIndex(cells, surfaces)
    objects = []
    occupied = {}
    _place_features(spec, scene_spec, index, rng, objects, occupied,
                    diagnostics, errors)
    if errors:
        raise BattlefieldGenError(errors)

    _validate_entry_and_spawns(spec, index, errors)
    if errors:
        raise BattlefieldGenError(errors)

    battlefield = _assemble_battlefield(spec, scene_spec, seed, mode, cells,
                                        links, rules)
    _validate_reachability(battlefield, spec, index, objects, diagnostics)
    # 终校验：产出的战场必须能通过既有加载器，绝不悄悄生成无效地图。
    tactical_battlefield.load_battlefield(battlefield, rules)

    diagnostics["counts"]["cells"] = len(cells)
    diagnostics["counts"]["objects"] = len(objects)
    return {
        "battlefield": battlefield,
        "objects": objects,
        "encounter_meta": spec["encounter_meta"],
        "diagnostics": diagnostics,
    }


# ── 格与垂直连接 ────────────────────────────────────────────────────────

def _build_cells_and_links(spec, coords, rng, rules, errors):
    is_metric = spec["mode"] == "metric"
    hex_size = rules["scale"]["hex_size_cm"] if is_metric else 1
    cells = []
    pending_links = []
    surfaces = []
    coord_order = {coord: number for number, coord in enumerate(coords)}
    for order, surface in enumerate(spec["surfaces"]):
        surface_id = surface["surface_id"]
        surfaces.append(surface_id)
        step = surface["elevation_step"]
        for coord in coords:
            q, r = coord
            variation = rng.randrange(0, 2) * step if step else 0
            cell = {
                "cell_id": "cell-%s-%03d" % (_slug(surface_id), coord_order[coord]),
                "q": q,
                "r": r,
                "surface_id": surface_id,
                "terrain_id": "open",
                "terrain_tags": ["open"],
                "movement_cost": hex_size,
                "blocks_movement": False,
                "blocks_los": False,
                "hazard_refs": [],
                "feature_refs": [],
                "spawnable": True,
            }
            if is_metric:
                cell["elevation_cm"] = surface["elevation"] + variation
            else:
                cell["elevation_level"] = surface["elevation"] + variation
            cells.append(cell)
        if order > 0 and surface["link"] is not None:
            pending_links.append((order, surface["link"]))
    # link 的 cell_id 要等两层格都建好后才能解析。
    links = []
    coord_set = set(coords)
    for order, link in pending_links:
        at = link["at"]
        if at not in coord_set:
            errors.append(_err(
                "cell_not_on_board", "$.surfaces[%d].link.at" % order,
                "垂直连接格位 (q=%d, r=%d) 不在成型后的格位集内" % at))
            continue
        below = spec["surfaces"][order - 1]["surface_id"]
        above = spec["surfaces"][order]["surface_id"]
        links.append({
            "link_id": "vl-%s-%s" % (_slug(below), _slug(above)),
            "from_cell_id": "cell-%s-%03d" % (_slug(below), coord_order[at]),
            "to_cell_id": "cell-%s-%03d" % (_slug(above), coord_order[at]),
            "kind": link["kind"],
            "movement_cost": hex_size,
            "bidirectional": link["bidirectional"],
            "requirements": [],
        })
    if errors:
        return None, None, None
    return cells, links, surfaces


class _CellIndex:
    """格位索引：全部查询确定性（按构造顺序或排序后位置）。"""

    def __init__(self, cells, surfaces):
        self.cells = cells
        self.surfaces = surfaces
        self.by_surface = {s: [] for s in surfaces}
        self.by_id = {}
        self.by_coord = {}
        for cell in cells:
            self.by_surface[cell["surface_id"]].append(cell)
            self.by_id[cell["cell_id"]] = cell
            self.by_coord[(cell["surface_id"], cell["q"], cell["r"])] = cell

    def cell_at(self, surface_id, coord):
        return self.by_coord.get((surface_id, coord[0], coord[1]))

    def resolve_surface(self, surface_id):
        """缺省首层。"""
        if surface_id is None:
            return self.surfaces[0]
        return surface_id

    def disk_cells(self, surface_id, center, radius):
        out = []
        for cell in self.by_surface[surface_id]:
            if tactical_hex.hex_distance((cell["q"], cell["r"]), center) <= radius:
                out.append(cell)
        return sorted(out, key=lambda c: (c["q"], c["r"]))


# ── 特征落地 ────────────────────────────────────────────────────────────

def _place_features(spec, scene_spec, index, rng, objects, occupied,
                    diagnostics, errors):
    scene_features = scene_spec["features"]
    matched = [False] * len(scene_features)
    protected = _protected_cells(spec, index)

    for feature in spec["features"]:
        surface_id = index.resolve_surface(feature["surface_id"])
        if surface_id not in index.by_surface:
            errors.append(_err("unknown_surface", "$.features",
                               "surface_id %r 不在本模板 surfaces 里" % (surface_id,)))
            return
        if feature["source_feature"] is None:
            targets = _explicit_targets(feature, index, surface_id, errors)
            if errors:
                return
            if targets is None:  # 自动放置（锚定细节之外的 seed 变化来源）
                count = feature["count"]
                eligible = _eligible_cells(index, surface_id, occupied, protected)
                if len(eligible) < count:
                    diagnostics["counts"]["features_unplaced"] += count
                    diagnostics["unplaced_features"].append({
                        "source": feature["feature_id"], "tag": None,
                        "reason": "可放置格不足（需要 %d，只有 %d）"
                                  % (count, len(eligible)),
                    })
                    continue
                chosen = rng.sample(sorted(eligible), count)
                targets = [index.cell_at(surface_id, coord) for coord in chosen]
            _apply_feature(feature, targets, surface_id, None, index, objects,
                           occupied, protected, diagnostics, errors)
            if errors:
                return
        else:
            for scene_index, scene_feature in enumerate(scene_features):
                if scene_feature["tag"] != feature["source_feature"]:
                    continue
                matched[scene_index] = True
                targets = _explicit_targets(feature, index, surface_id, errors)
                if errors:
                    return
                if targets:
                    _apply_feature(feature, targets, surface_id, scene_feature,
                                   index, objects, occupied, protected,
                                   diagnostics, errors)
                else:
                    cell = _auto_one(index, surface_id, occupied, rng, protected)
                    if cell is None:
                        diagnostics["counts"]["features_unplaced"] += 1
                        diagnostics["unplaced_features"].append({
                            "source": feature["feature_id"],
                            "tag": scene_feature["tag"],
                            "reason": "无自动放置空位",
                        })
                        continue
                    _apply_feature(feature, [cell], surface_id, scene_feature,
                                   index, objects, occupied, protected,
                                   diagnostics, errors)
                if errors:
                    return

    for scene_index, scene_feature in enumerate(scene_features):
        if not matched[scene_index]:
            diagnostics["unmapped_features"].append({
                "tag": scene_feature["tag"], "index": scene_index,
            })


def _explicit_targets(feature, index, surface_id, errors):
    """显式 at / disk 的目标格列表；无显式声明返回 `None`。"""
    if feature["at"] is None and feature["disk"] is None:
        return None
    if feature["at"] is not None:
        cell = index.cell_at(surface_id, feature["at"])
        if cell is None:
            errors.append(_err(
                "cell_not_on_board", "$.features",
                "feature %r 的显式格位 (q=%d, r=%d) 不在 surface %r 上"
                % (feature["feature_id"], feature["at"][0], feature["at"][1],
                   surface_id)))
            return []
        return [cell]
    return index.disk_cells(surface_id, feature["disk"]["center"],
                            feature["disk"]["radius"])


def _protected_cells(spec, index):
    """入口与出生点格位（surface, (q, r)）集合——对象不得压住。"""
    protected = set()
    for field in ("entry_cells", "spawn_points"):
        for point in spec[field]:
            protected.add((index.resolve_surface(point["surface_id"]), point["at"]))
    return protected


def _eligible_cells(index, surface_id, occupied, protected):
    out = []
    for cell in index.by_surface[surface_id]:
        if cell["blocks_movement"]:
            continue
        if (surface_id, (cell["q"], cell["r"])) in protected:
            continue
        if cell["cell_id"] in occupied:
            continue
        out.append((cell["q"], cell["r"]))
    return out


def _auto_one(index, surface_id, occupied, rng, protected):
    eligible = _eligible_cells(index, surface_id, occupied, protected)
    if not eligible:
        return None
    return index.cell_at(surface_id, rng.choice(sorted(eligible)))


def _apply_feature(feature, target_cells, surface_id, scene_feature, index,
                   objects, occupied, protected, diagnostics, errors):
    """把一条特征落到目标格；场景映射放不下时逐格受控降级。

    `protected` 是入口 / 出生点格位集合——对象（无论显式还是场景映射）
    都不得压住（02C 第三节不变式：出生点不得重叠 / 必然无路）。
    """
    for cell in target_cells:
        cell_id = cell["cell_id"]
        refs = [scene_feature["ref"]] if scene_feature is not None else []
        if cell_id in occupied:
            if scene_feature is not None:
                diagnostics["counts"]["features_unplaced"] += 1
                diagnostics["unplaced_features"].append({
                    "source": feature["feature_id"],
                    "tag": scene_feature["tag"],
                    "reason": "格 %s 已被占用" % cell_id,
                })
                continue
            errors.append(_err(
                "overlap", "$.features",
                "feature %r 的显式目标格 %s 已被占用" % (feature["feature_id"], cell_id)))
            return
        if feature["kind"] == "terrain":
            declaration = feature["terrain"]
            cell["terrain_id"] = declaration["terrain_id"]
            cell["terrain_tags"] = list(declaration.get("terrain_tags", ["open"]))
            for key in ("blocks_movement", "blocks_los"):
                if key in declaration:
                    cell[key] = declaration[key]
            for key in ("cover_grade", "obstacle_height"):
                if key in declaration:
                    cell[key] = declaration[key]
            cell["hazard_refs"] = list(declaration.get("hazard_refs", []))
            cell["feature_refs"] = sorted(set(cell["feature_refs"])
                                          | {feature["feature_id"]})
            diagnostics["counts"]["features_placed"] += 1
        else:
            declaration = feature["object"]
            if (surface_id, (cell["q"], cell["r"])) in protected:
                if scene_feature is not None:
                    diagnostics["counts"]["features_unplaced"] += 1
                    diagnostics["unplaced_features"].append({
                        "source": feature["feature_id"],
                        "tag": scene_feature["tag"],
                        "reason": "格 %s 是入口 / 出生点" % cell_id,
                    })
                    continue
                errors.append(_err(
                    "spawn_conflict", "$.features",
                    "feature %r 的对象不能压住入口 / 出生点格 %s"
                    % (feature["feature_id"], cell_id)))
                return
            if cell["blocks_movement"]:
                if scene_feature is not None:
                    diagnostics["counts"]["features_unplaced"] += 1
                    diagnostics["unplaced_features"].append({
                        "source": feature["feature_id"],
                        "tag": scene_feature["tag"],
                        "reason": "格 %s 不可进入" % cell_id,
                    })
                    continue
                errors.append(_err(
                    "spawn_blocked", "$.features",
                    "feature %r 的对象不能落在不可进入格 %s"
                    % (feature["feature_id"], cell_id)))
                return
            serial = len([o for o in objects
                          if o["definition_id"] == declaration["definition_id"]])
            object_id = "obj-%s-%03d" % (_slug(feature["feature_id"]), serial)
            objects.append({
                "object_id": object_id,
                "definition_id": declaration["definition_id"],
                "category": declaration["category"],
                "cell_id": cell_id,
                "surface_id": surface_id,
                "state": declaration.get("state", "intact"),
                "blocks_movement": declaration.get("blocks_movement", False),
                "blocks_los": declaration.get("blocks_los", False),
                "cover_grade": declaration.get("cover_grade"),
                "feature_refs": refs,
            })
            occupied[cell_id] = object_id
            cell["spawnable"] = False
            diagnostics["counts"]["features_placed"] += 1


# ── 入口 / 出生点 / 可达性 ───────────────────────────────────────────────

def _validate_entry_and_spawns(spec, index, errors):
    for field in ("entry_cells", "spawn_points"):
        for order, point in enumerate(spec[field]):
            path = "$.%s[%d]" % (field, order)
            surface_id = index.resolve_surface(point["surface_id"])
            if surface_id not in index.by_surface:
                errors.append(_err("unknown_surface", path,
                                   "surface_id %r 不在本模板 surfaces 里"
                                   % (surface_id,)))
                continue
            cell = index.cell_at(surface_id, point["at"])
            if cell is None:
                errors.append(_err(
                    "cell_not_on_board", path,
                    "(q=%d, r=%d) 不在 surface %r 上"
                    % (point["at"][0], point["at"][1], surface_id)))
                continue
            if cell["blocks_movement"]:
                code = "entry_blocked" if field == "entry_cells" else "spawn_blocked"
                label = "入口格" if field == "entry_cells" else "出生格"
                errors.append(_err(code, path,
                                   "%s %s 不可进入" % (label, cell["cell_id"])))
    spawns = {(index.resolve_surface(p["surface_id"]), p["at"])
              for p in spec["spawn_points"]}
    entries = {(index.resolve_surface(p["surface_id"]), p["at"])
               for p in spec["entry_cells"]}
    clash = spawns & entries
    if clash:
        errors.append(_err("spawn_conflict", "$.spawn_points",
                           "出生点与入口格位冲突：%r" % (sorted(clash),)))


def _validate_reachability(battlefield, spec, index, objects, diagnostics):
    """入口 → 全部入口 / 出生点可达（复用 #419 plan_move，不另写寻路）。"""
    entries = spec["entry_cells"]
    spawns = spec["spawn_points"]
    if not entries and not spawns:
        diagnostics["notes"].append("模板未声明入口与出生点，跳过可达性检查")
        return
    total = sum(cell["movement_cost"] for cell in index.cells)
    total += sum(link["movement_cost"] for link in battlefield["vertical_links"]) * 2
    # 生成期只问「有没有路」：授权全部 link kind，运行时授权语义归移动方。
    allowed = tuple(tactical_battlefield.space_rules()["vertical_links"]["kinds"])
    blocking = {obj["cell_id"]: obj["object_id"] for obj in objects
                if obj["blocks_movement"]}
    if entries:
        root = index.cell_at(index.resolve_surface(entries[0]["surface_id"]),
                             entries[0]["at"])
    else:
        root = index.cell_at(index.resolve_surface(spawns[0]["surface_id"]),
                             spawns[0]["at"])
    if root is None:  # 校验阶段已报 cell_not_on_board，这里兜底
        return
    result = tactical_movement.plan_move(
        battlefield, "gen-check", root["cell_id"],
        [{"q": 0, "r": 0}], total,
        allowed_modes=allowed, occupied=blocking)
    reached = {item["cell_id"] for item in result["reachable"]}
    for field in ("entry_cells", "spawn_points"):
        for point in spec[field]:
            cell = index.cell_at(index.resolve_surface(point["surface_id"]),
                                 point["at"])
            if cell is not None and cell["cell_id"] not in reached:
                raise BattlefieldGenError([_err(
                    "spawn_unreachable", "$.%s" % field,
                    "%s 格 %s 从入口不可达（复用 plan_move 全量可达集校验）"
                    % (field, cell["cell_id"]))])


# ── 组装 ────────────────────────────────────────────────────────────────

def _assemble_battlefield(spec, scene_spec, seed, mode, cells, links, rules):
    battlefield = {
        "schema_version": tactical_battlefield.SCHEMA_VERSION,
        "kind": tactical_battlefield.BATTLEFIELD_KIND,
        "source": "docs/system/02c-hex-battlefield.md:281",
        "battlefield_id": "bf-%s-%s" % (_slug(spec["template_id"]), _slug(seed)),
        "world_id": scene_spec["world_id"],
        "seed": seed,
        "generator_version": "tactical_battlefield_gen-%s+tpl-%s"
                             % (GENERATOR_VERSION, spec["template_version"]),
        "geometry": {"type": "hex_axial"},
        "cells": cells,
        "vertical_links": links,
    }
    if scene_spec["site_id"]:
        battlefield["site_id"] = scene_spec["site_id"]
    if scene_spec["atlas_ref"]:
        battlefield["atlas_ref"] = scene_spec["atlas_ref"]
    if mode == "metric":
        battlefield["scale"] = {"mode": "metric",
                                "hex_size_cm": rules["scale"]["hex_size_cm"]}
    else:
        battlefield["scale"] = {"mode": "abstract",
                                "unit": rules["scale"]["abstract_units"][0]}
    return battlefield
