#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atlas_gen.py —— 地点与战术生成器（ATLAS 切片 I3，Issue #65）

设计依据：`ATLAS-DESIGN.md` §5.5（地点生成）、§5.6（战术帧）、§3.2（标识）。
进入一个关键地点或区域时，按**性状与数字**生成地点帧（楼层 / 路网 /
开阔地）；开战时把近处房间投影成战术帧。生成结果是纯数据，不入库。

约定
  · 生成器不知道任何世界名字：源码不含任何世界的 key，不读世界模组；
    一切差异来自编译信息（atlas["region_info"]）里的性状与规格，
    以及调用方传入的世界表。
  · 关键地点是锚：名字用原文；生成器只填锚周围的格，不改锚的名字，
    锚与生成的入口格之间用一条「门」连接（跨帧连接，坐标不换算）。
  · 可复现：同一 atlas、同一锚、同一种子，房间 id 与连接完全相同；
    帧已存在时不重复生成，直接返回（按种子 + 增量可重算，§3.4）。
  · 场地要素只用规则里已有的六种：掩体 / 危险 / 机关 / 增幅 / 情绪 / 机动。
    优先取**当前世界** random_tables 里带这些 tag 的行；该世界没有这类行时
    才用 `data/random_tables/system.json` 的场地类表；绝不读其他世界的表。
  · 6 米只出现在 metric 战术帧（§5.6）：把移动米数换成跨区数。
    地点帧的层数 / 负层 / 节点图 / 不稳定边 / 抽象帧都不使用它——
    层数只来自编译规格（总层数、每层约 10 米、默认档），与米无关。
  · 零第三方依赖，仅 Python 3 标准库；只调用 `atlas.py` 内核，不改内核。

对外接口
    generate_site(atlas, anchor_id, *, world=None, system_tables=None, seed=None)
    generate_tactical(atlas, room_id, seed=None)
    zone_steps(atlas, frame_id, meters)
"""

from __future__ import annotations

import json
import math
import os
import random
import zlib

import atlas as al

# ════════════════════════════════════════════════════════════════════════
# 常数（§5.6：6 米只属于 metric 战术帧；六种场地要素见 §5.5）
# ════════════════════════════════════════════════════════════════════════

#: metric 战术帧里一区按 6 米计，只用于 ceil(米 / 6) 的跨区换算。
TACTICAL_CELL_METERS = 6

#: 场地要素的六种类型（§5.5：只来自规则里已有的类型）。
SITE_ELEMENT_TAGS = ("掩体", "危险", "机关", "增幅", "情绪", "机动")

#: 该世界没有场地类表行时的兜底数据（system 随机表目录，非世界模组）。
_SYSTEM_TABLES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                   "data", "random_tables", "system.json")

#: 平面撒点的候选格（§5.5：图的边界随内容生长，这里只约束单层撒点范围）。
#: 坐标用 1 起（入口固定在 (1,1,0)），避免与编译器的 seam 节点
#: {区域}/g0_0_0 撞 id。
_PLAN_SPOTS = [(x, y) for x in range(1, 6) for y in range(1, 6)]
_PLAN_SPOTS_OPEN = [(x, y) for x in range(1, 4) for y in range(1, 4)]
_PLAN_SPOTS_NET = [(x, y) for x in range(1, 7) for y in range(1, 7)]

#: 地点帧的入口格（锚用「门」连到这里）。
ENTRANCE_SPOT = (1, 1)

_DELTA_VIA = {(0, 1): "北", (0, -1): "南", (1, 0): "东", (-1, 0): "西"}


# ════════════════════════════════════════════════════════════════════════
# 小工具
# ════════════════════════════════════════════════════════════════════════

def _derive_seed(atlas, anchor_id, seed):
    """地点帧种子：显式 seed 优先；否则由 atlas 种子 + 锚 id 稳定导出
   （crc32 与 Python 内建 hash 不同，跨进程可复现）。"""
    if seed is not None:
        return int(seed)
    base = int(atlas.get("seed") or 0)
    return (base * 2654435761 ^ zlib.crc32(anchor_id.encode("utf-8"))) & 0x7FFFFFFF


def _via_of(cur, prev):
    """平面上从 prev 走到 cur 的基本方向（两格必然正交相邻）。"""
    return _DELTA_VIA[(cur[0] - prev[0], cur[1] - prev[1])]


def _ensure_link(atlas, frame_id, a, b, via):
    """a→b 已有同向连接就不重复建（凿走廊可能路过已有格）。"""
    place = al.find_place(atlas, a)
    for link in place["links"]:
        if link["to"] == b and link["via"] == via:
            return
    al.add_link(atlas, frame_id, a, b, via)


def _add_cell(atlas, frame_id, anchor_id, pos, name, kind):
    """放一格生成格。id 按 §3.2：{父 id}/g{x}_{y}_{z}。"""
    x, y, z = pos
    pid = "%s/g%d_%d_%d" % (anchor_id, x, y, z)
    al.add_place(atlas, {
        "id": pid, "name": name, "kind": kind, "frame_id": frame_id,
        "source": "generated", "x": x, "y": y, "z": z,
    })
    return pid


def _mst_edges(points):
    """平面点集在曼哈顿距离下的最小生成树（Prim，遍历次序固定 → 确定性）。"""
    count = len(points)
    edges = []
    if count <= 1:
        return edges
    reached = [0]
    remaining = list(range(1, count))
    while remaining:
        best = None
        for i in reached:
            for j in remaining:
                d = (abs(points[i][0] - points[j][0])
                     + abs(points[i][1] - points[j][1]))
                cand = (d, i, j)
                if best is None or cand < best:
                    best = cand
        _d, i, j = best
        edges.append((i, j))
        reached.append(j)
        remaining.remove(j)
    return edges


# ════════════════════════════════════════════════════════════════════════
# 三种地貌（§5.5：network → 节点图；wilderness 无 vertical → 开阔地；其他 → 楼层）
# ════════════════════════════════════════════════════════════════════════

def _carve(atlas, frame_id, anchor_id, grid, z, a, b):
    """在 a、b 之间凿 L 形走廊（先 x 后 y），走廊格是 fill。"""
    ax, ay = a
    bx, by = b
    path = []
    if bx != ax:
        sx = 1 if bx > ax else -1
        # 同排时不含 b 本身；拐弯时 x 段走到 (bx, ay) 转角
        end = bx + sx if by != ay else bx
        path.extend((x, ay) for x in range(ax + sx, end, sx))
    if by != ay:
        sy = 1 if by > ay else -1
        path.extend((bx, y) for y in range(ay + sy, by, sy))
    prev, prev_id = a, grid[(ax, ay, z)]
    for cell in path:
        cid = grid.get((cell[0], cell[1], z))
        if cid is None:
            cid = _add_cell(atlas, frame_id, anchor_id,
                            (cell[0], cell[1], z), "走廊", "fill")
            grid[(cell[0], cell[1], z)] = cid
        _ensure_link(atlas, frame_id, prev_id, cid, _via_of(cell, prev))
        prev, prev_id = cell, cid
    _ensure_link(atlas, frame_id, prev_id, grid[(bx, by, z)], _via_of(b, prev))


def _connect_plane(atlas, frame_id, anchor_id, grid, z, spots, rng, loop_rate):
    """同层撒点连成一体：曼哈顿距离上求一棵生成树并凿走廊，
    再以 loop_rate 的比例对剩余边凿走廊加环路（§5.5）。"""
    tree = _mst_edges(spots)
    for i, j in tree:
        _carve(atlas, frame_id, anchor_id, grid, z, spots[i], spots[j])
    tree = set(tree)
    for i in range(len(spots)):
        for j in range(i + 1, len(spots)):
            if (i, j) in tree:
                continue
            if rng.random() < loop_rate:
                _carve(atlas, frame_id, anchor_id, grid, z,
                       spots[i], spots[j])


def _gen_floors(atlas, frame_id, anchor_id, rng, spec):
    """默认地貌：每层撒房间，层与层之间只在平面重叠处放楼梯。"""
    grid = {}
    for z in range(spec["z_lo"], spec["z_hi"] + 1):
        count = 3 + rng.randint(0, 2)
        spots = [ENTRANCE_SPOT] + rng.sample(
            [s for s in _PLAN_SPOTS if s != ENTRANCE_SPOT], count - 1)
        for (x, y) in spots:
            grid[(x, y, z)] = _add_cell(atlas, frame_id, anchor_id,
                                        (x, y, z), "房间", "room")
        _connect_plane(atlas, frame_id, anchor_id, grid, z, spots, rng, 0.15)
    # 楼梯：相邻两层在 (x, y) 重叠处（房间或走廊格）连接
    for z in range(spec["z_lo"], spec["z_hi"]):
        lower = {(x, y) for (x, y, gz) in grid if gz == z}
        upper = {(x, y) for (x, y, gz) in grid if gz == z + 1}
        for (x, y) in sorted(lower & upper):
            al.add_link(atlas, frame_id, grid[(x, y, z)],
                        grid[(x, y, z + 1)], "上")
    return grid


def _gen_open(atlas, frame_id, anchor_id, rng):
    """开阔地：1 层，房间少，走廊短（§5.5）。"""
    grid = {}
    count = 2 + rng.randint(0, 1)
    spots = [ENTRANCE_SPOT] + rng.sample(
        [s for s in _PLAN_SPOTS_OPEN if s != ENTRANCE_SPOT], count - 1)
    for (x, y) in spots:
        grid[(x, y, 0)] = _add_cell(atlas, frame_id, anchor_id,
                                    (x, y, 0), "空地", "room")
    for i, j in _mst_edges(spots):
        _carve(atlas, frame_id, anchor_id, grid, 0, spots[i], spots[j])
    return grid


def _gen_network(atlas, frame_id, anchor_id, rng, kp_count):
    """节点图：节点数取 min(12, 3 + 关键地点数)（§5.5）。

    x、y 只为了能画切片；连接一律用「连接」，移动花费看连接，
    与格间米数无关。
    """
    grid = {}
    count = min(12, 3 + kp_count)
    spots = [ENTRANCE_SPOT] + rng.sample(
        [s for s in _PLAN_SPOTS_NET if s != ENTRANCE_SPOT], count - 1)
    for (x, y) in spots:
        grid[(x, y, 0)] = _add_cell(atlas, frame_id, anchor_id,
                                    (x, y, 0), "节点", "room")
    ids = [grid[(x, y, 0)] for (x, y) in spots]
    for i, j in _mst_edges(spots):
        al.add_link(atlas, frame_id, ids[i], ids[j], "连接")
    return grid


# ════════════════════════════════════════════════════════════════════════
# 性状后处理（§5.5：unstable 边）与场地要素
# ════════════════════════════════════════════════════════════════════════

def _mark_unstable(atlas, frame_id):
    """非锚点的边标 unstable（锚是 authored 地点，其参与的边不动）。"""
    frame = atlas["frames"][frame_id]
    for pid in sorted(frame["places"]):
        place = frame["places"][pid]
        if place["source"] != "generated":
            continue
        for link in place["links"]:
            other = al.find_place(atlas, link["to"])
            if other is not None and other["frame_id"] == frame_id \
                    and other["source"] == "generated":
                link["unstable"] = True


def _venue_rows(tables):
    """一组表里带六种场地要素 tag 的行（内容判定，不看表名 / 世界名）。"""
    rows = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        for row in table.get("rows", []):
            if isinstance(row, dict) and row.get("tag") in SITE_ELEMENT_TAGS:
                rows.append(row)
    return rows


def _load_system_tables():
    """兜底场地类表：data/random_tables/system.json（缺失时返回空）。"""
    try:
        with open(_SYSTEM_TABLES_PATH, encoding="utf-8") as handle:
            return json.load(handle).get("tables", [])
    except OSError:
        return []


def _venue_pool(world, system_tables):
    """场地要素行池（§5.5）：优先当前世界的 random_tables；
    该世界没有这类行时才用 system 兜底。池子只由调用方给的表构成，
    生成器没有任何途径读到另一个世界的表。"""
    rows = []
    if world is not None:
        rows = _venue_rows(world.get("random_tables") or [])
    if not rows:
        tables = system_tables
        if tables is None:
            tables = _load_system_tables()
        rows = _venue_rows(tables)
    return [{"tag": row["tag"], "text": row.get("result", "")} for row in rows]


def _add_features(atlas, frame_id, rng, pool):
    """每个房间 0–2 条场地要素（§5.5）。"""
    frame = atlas["frames"][frame_id]
    for pid in sorted(frame["places"]):
        place = frame["places"][pid]
        if place["kind"] != "room":
            continue
        count = rng.randint(0, 2)
        place["features"] = [dict(rng.choice(pool)) for _ in range(count)]


# ════════════════════════════════════════════════════════════════════════
# 地点生成（§5.5）
# ════════════════════════════════════════════════════════════════════════

def _anchor_spec(atlas, anchor):
    """从编译信息取锚的规格：返回 (region_info, site_spec, traits, kp 数)。

    关键地点锚用自己那条 kp 规格（编译时已并入区域性状）；区域锚用
    区域规格。生成器只认 region_info，不做任何世界名比较。
    """
    info_map = atlas.get("region_info") or {}
    anchor_id = anchor["id"]
    if anchor["kind"] == "site":
        parent, _, suffix = anchor_id.rpartition("/k")
        if not parent or not suffix.isdigit() or parent not in info_map:
            raise ValueError("关键地点缺少编译信息: %s" % anchor_id)
        info = info_map[parent]
        specs = info.get("kp_specs") or []
        index = int(suffix)
        if index >= len(specs):
            raise ValueError("关键地点下标越界: %s" % anchor_id)
        kp = specs[index]
        traits = set(info.get("traits", [])) | set(kp.get("traits", []))
        return info, kp["spec"], traits, len(specs)
    if anchor_id not in info_map:
        raise ValueError("区域缺少编译信息: %s" % anchor_id)
    info = info_map[anchor_id]
    return info, info["site"], set(info.get("traits", [])), \
        len(info.get("kp_specs") or [])


def generate_site(atlas, anchor_id, *, world=None, system_tables=None,
                  seed=None):
    """进入一个关键地点或区域时生成地点帧（§5.5）。

    atlas 是 `atlas_compile.compile_world` 的结果（需要 region_info）；
    anchor_id 是区域或关键地点的 place id。world 是**当前世界**的
    world.module dict（只读它的 random_tables 取场地要素行）；
    system_tables 显式给兜底表，缺省读 `data/random_tables/system.json`；
    seed 缺省由 atlas 种子 + 锚 id 稳定导出。

    帧已存在时不重复生成，直接返回。返回地点帧 dict。
    """
    anchor = al.find_place(atlas, anchor_id)
    if anchor is None:
        raise ValueError("锚地点不存在: %s" % anchor_id)
    if anchor["kind"] not in ("region", "site"):
        raise ValueError("只能从区域或关键地点生成地点帧: %s" % anchor["kind"])
    frame_id = "%s/site/%s" % (atlas["world_key"], anchor_id)
    if frame_id in atlas["frames"]:
        return atlas["frames"][frame_id]      # 同种子可复现，不重复生成

    _info, spec, traits, kp_count = _anchor_spec(atlas, anchor)
    space = "abstract" if "abstract" in traits else "metric"
    al.add_frame(atlas, frame_id, space=space, z_meaning="楼层", cell="房间")
    site_seed = _derive_seed(atlas, anchor_id, seed)
    atlas["frames"][frame_id]["seed"] = site_seed
    rng = random.Random(site_seed)

    if "network" in traits:
        grid = _gen_network(atlas, frame_id, anchor_id, rng, kp_count)
    elif "wilderness" in traits and "vertical" not in traits:
        grid = _gen_open(atlas, frame_id, anchor_id, rng)
    else:
        grid = _gen_floors(atlas, frame_id, anchor_id, rng, spec)

    entrance = grid.get((ENTRANCE_SPOT[0], ENTRANCE_SPOT[1], 0))
    if entrance is None:
        raise RuntimeError("地点帧缺少入口格: %s" % frame_id)
    # 锚是关键地点 / 区域本身（authored），与入口格之间用门连接
    al.add_link(atlas, frame_id, anchor_id, entrance, "门")

    if "unstable" in traits:
        _mark_unstable(atlas, frame_id)
    pool = _venue_pool(world, system_tables)
    if pool:
        _add_features(atlas, frame_id, rng, pool)
    return atlas["frames"][frame_id]


# ════════════════════════════════════════════════════════════════════════
# 战术帧（§5.6）
# ════════════════════════════════════════════════════════════════════════

def generate_tactical(atlas, room_id, seed=None):
    """把当前房间以及图距离 ≤ 2 的房间投影成战术帧（§5.6）。

    每间成为一个区域（kind=zone），保留它的第一个场地要素作为固有
    特征（inherent）。帧的空间口径与所在地点帧一致：metric 的格记
    「6米」，abstract 记「一次走位」。坐标从房间原样复制，不换算。
    返回战术帧 dict；帧已存在时直接返回。
    """
    room = al.find_place(atlas, room_id)
    if room is None:
        raise ValueError("房间不存在: %s" % room_id)
    site_fid = room["frame_id"]
    parts = site_fid.split("/")
    if len(parts) < 3 or parts[1] != "site":
        raise ValueError("战术帧只能从地点帧的房间投影: %s" % site_fid)
    tac_id = "%s/tactical/%s" % (atlas["world_key"], room_id)
    if tac_id in atlas["frames"]:
        return atlas["frames"][tac_id]

    frame = atlas["frames"][site_fid]
    dist = {room_id: 0}
    frontier = [room_id]
    while frontier:
        nxt = []
        for pid in frontier:
            if dist[pid] >= 2:
                continue
            for link in frame["places"][pid]["links"]:
                to = link["to"]
                if to in frame["places"] and to not in dist:
                    dist[to] = dist[pid] + 1
                    nxt.append(to)
        frontier = nxt

    space = frame["space"]
    al.add_frame(atlas, tac_id, space=space, z_meaning="战区",
                 cell="一次走位" if space == "abstract"
                 else "%d米" % TACTICAL_CELL_METERS)
    atlas["frames"][tac_id]["seed"] = _derive_seed(atlas, room_id, seed)

    zones = {}
    for index, rid in enumerate(sorted(dist)):
        src = frame["places"][rid]
        zone = {
            "id": "%s/z%d" % (tac_id, index),
            "name": src["name"],
            "kind": "zone",
            "frame_id": tac_id,
            "links": [],
            "traits": list(src.get("traits", [])),
            "discovered": "seen",
            "source": "generated",
        }
        for axis in ("x", "y", "z"):
            if axis in src:
                zone[axis] = src[axis]
        if src.get("features"):
            zone["inherent"] = dict(src["features"][0])
        al.add_place(atlas, zone)
        zones[rid] = zone["id"]

    for rid in sorted(dist):
        for link in frame["places"][rid]["links"]:
            to = link["to"]
            if to in zones and rid < to:      # 双边只建一次
                al.add_link(atlas, tac_id, zones[rid], zones[to], link["via"])
    return atlas["frames"][tac_id]


def zone_steps(atlas, frame_id, meters):
    """把规则里的移动米数换成战术帧的跨区数（§5.6）。

    metric：ceil(米 / 6)。abstract：**不使用这个换算**——一次走位跨
    一区，米数不参与，返回 None。
    """
    frame = atlas["frames"].get(frame_id)
    if frame is None:
        raise ValueError("帧不存在: %s" % frame_id)
    if frame["space"] == "abstract":
        return None
    return int(math.ceil(float(meters) / float(TACTICAL_CELL_METERS)))
