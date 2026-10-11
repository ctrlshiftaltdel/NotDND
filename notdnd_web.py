#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notdnd_web.py —— NotDND 网页后端骨架（M3 前置）

只提供**工程骨架**，不含规则、数值与世界内容：
  · HTTP 响应 helper 与单类路径链路由（GET 只读 / POST 写入，无框架）
  · 静态资源服务（目录穿越防护 + MIME）
  · 监听与端口约定（NOTDND_HOST / NOTDND_PORT，回退通用 PORT）
  · 存档骨架：原子写 + 惰性迁移 + 缓存锁 + 列表 + 改名 / 删除边界
  · 规则会话接线：持有 prism_core.RuleSession 的快照（规则态的唯一真相在
    prism_core），老档惰性迁移出空规则会话，GET /api/session 只读拉取
  · ATLAS 自动地图接线（切片 I4）：Session 顶层 atlas 存档块（§3.4，与
    rules 平级，atlas.export_state / restore_state 进出），老档缺块时按
    当前世界懒编译；GET /api/atlas/exits 看出口（纯文本列表），
    POST /api/atlas/move 移动并返回行程档与名义分钟
  · 导引者接线（可选模块）：`guide` 状态块随存档落盘 / 按键还原；
    GET /api/guide/status 三个布尔；POST /api/guide/bind 记录显式的
    `world_key` / `scenario_id`（绑定之后 L1 才是这场剧本的典范卡）；
    POST /api/guide/turn 先结算、后叙事，回合内按需在叙事数组上开一次工具
    预通行（思考开 / 非流式 / 最多 3 轮，失败只把固定短语喂回模型），
    可选的 `location_id` 在状态行之前校验并写入 `focus_location_id`，
    状态行之后、叙事之前调用一次 `ensure_realization`（G5 是空实现），
    叙事走 HTTP/1.1 分块的事件流（上游失败只发 `fallback`，不再动 HTTP 状态）；
    POST /api/guide/speak 把上一回合存下的一拍读成 pcm16 流（24 kHz / mono /
    s16le，HTTP/1.1 分块、无 Content-Length；只接受 `last_beats` 里的全文）
  · 导引者行为与对白（G7 接线）：焦点离开某地点（或 `rules.scene.id` 变化）时，
    在实相之后、叙事之前为该处在场的至多 4 名具名 NPC 记一条场外痕迹
    （非流式 / 思考关，失败不留痕、不兜底）；玩家的整段叙事进 L3 与切拍之前
    先过秘密门（未放行的秘密换成「……」，不第二次叫模型）；`narration` 事件
    至多带一条服务端揭开的 `trace`；对白拍按 `npc.id` 分配白桦 / 茉莉
  · 多人房间骨架（M10-S1，MULTIPLAYER-DESIGN.md §3.2 / §4）：顶层 `room` 块
    随存档落盘（旧单人档惰性迁移成最小房间，M-16）；`POST /api/room/create` /
    `join` / `leave` / `kick` 走 `Authorization: Bearer` + `X-Room` / `X-Member`
    鉴权链（§4.1）；房间级角色矩阵（§4.2）与 6 人上限（容量满 → `409 room_full`）；
    令牌只存 `sha256`，明文仅在创建 / 加入时回一次，永不落盘 / 写日志

「规则会话核心」与「AI 导引者」分属独立模块；需要 PRISM 业务语义之处
一律留 TODO(M3)，由后续 Issue 按 PRISM 命名（六维 MGT / FIN / VIG / INS /
MND / PRE）补齐，本文件不臆造字段与数值。

启动：
    python3 notdnd_web.py                          # 默认 0.0.0.0:8600
    NOTDND_PORT=9000 python3 notdnd_web.py

手机 / 平板访问：与本机同一局域网，打开 http://<本机IP>:<端口>/
⚠️ 仅供本机 / 局域网使用，请勿直接暴露到公网。
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import pathlib
import random
import re
import secrets
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import atlas as atlas_kernel
import atlas_compile
import prism_core

# 导引者是**可选模块**（GUIDE-DESIGN.md §5.1）：依赖方向只允许
# notdnd_web → prism_guide → prism_core。import 失败（缺文件 / 语法错 / 缺依赖）
# 时网页层照常工作，导引路由返回固定 JSON 兜底，**不结算**。
try:
    import prism_guide
except Exception:      # noqa: BLE001 — 可选模块缺失不该带走整个服务
    prism_guide = None

HERE = pathlib.Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"

# 存档目录：默认落在本地运行物目录 web-saves/（禁入库）。
# ⚠️ 惰性创建（见 _ensure_save_dir）——import 本模块不得触碰磁盘，无副作用。
SAVE_DIR = pathlib.Path(os.environ.get("NOTDND_SAVE") or (HERE / "web-saves"))

# ⚠️ 两种环境变量都要认：
#   NOTDND_PORT —— 本机 / 局域网自用的品牌约定，方便本地覆盖
#   PORT        —— 部署平台注入的通用约定（发布时用它决定对外端口）
# 只认品牌变量的话，平台注入的 PORT 会被忽略，服务去抢默认端口，
# 而真实对外端口根本没被用上。品牌变量优先。
HOST = os.environ.get("NOTDND_HOST", "0.0.0.0")
PORT = int(os.environ.get("NOTDND_PORT") or os.environ.get("PORT") or "8600")

# 存档列表一次最多返回多少条；超出的旧存档仍在磁盘上，只是列表里不显示。
SAVE_LIST_LIMIT = int(os.environ.get("NOTDND_SAVE_LIST_LIMIT") or "40")

# 存档 sid 白名单：既是业务标识，也是**磁盘文件名安全边界**
# （不含路径分隔符与点号，杜绝 ../ 拼接）。
SID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

# ── 导引者（G2）常量 ─────────────────────────────────────────────────────
#
# 玩家文本上限（§5.10）：超过就是 400「这句话太长」。
GUIDE_TEXT_LIMIT = 2000

# 速率：每会话每 60 秒最多 12 次 turn / 30 次 speak（§5.10）。
# 只放内存，**不进存档**（to_dict 是白名单，速率不是局内状态）。
GUIDE_RATE_WINDOW_S = 60.0
GUIDE_TURN_LIMIT = 12
GUIDE_SPEAK_LIMIT = 30

# `POST /api/guide/speak` 回传的音频规格（§4.3：24 kHz、单声道、s16le /
# PCM16LE）。头里同时给出速率与格式，浏览器不必猜。
GUIDE_AUDIO_FORMAT = "pcm16"
GUIDE_AUDIO_RATE = "24000"

# 淡出与跳过是**整句相等**，不是关键字包含（§5.10）：单独的「跳过」不命中。
GUIDE_FADE_WORD = "淡出"
GUIDE_FADE_TEXT = "第二天早上，事情已经处理完了。"
GUIDE_SKIP_WORD = "跳过这段"
GUIDE_SKIP_TEXT = "这段先跳过。已经记下的规则结果还在，我们换一件事。"

# 结算异常 → (固定短语, 仅开头可用的 HTTP 码)（§5.2）。状态行写出之后
# 不再产生第二套 HTTP 状态，同一短语改当工具错误字符串（G4）。
GUIDE_SETTLE_ERRORS = {
    "行动不存在于当前场景": ("没有这个行动", 400),
    "单位不存在": ("没有这个角色", 400),
    "战斗还没结束，先打完这场": ("战斗还没结束", 409),
}
GUIDE_SETTLE_DEFAULT = ("这次结算不能做", 400)

# ── 导引者（G5）常量 ─────────────────────────────────────────────────────
#
# 显式绑定失败 → 固定短语 + HTTP 码（§6）。状态行之前返回，不打开 SSE。
# 短语与 `prism_guide.bind` 抛的 `ValueError` 同文（跨模块锁）。
GUIDE_BIND_ERRORS = {
    "没有这场战役": 400,
    "世界对不上": 400,
    "已经绑定": 409,
}
GUIDE_BIND_DEFAULT = 400

# `turn` 的 `location_id` 不是已绑定剧本里的地点 → 400「没有这个地点」（§2.1）。
GUIDE_NO_LOCATION = "没有这个地点"

# 导引者模块缺失时，绑定这一类**只有模块才能做**的路由给的固定错误短语（§5.1）。
GUIDE_ABSENT_ERROR = "导引者不在席"

# 模块缺失时的本地兜底句：与 `prism_guide.FALLBACK_NARRATION` 同文的常量
# （§5.1：「兜底句是模块级常量，不向模型现编」）。模块在时以它为准。
FALLBACK_NARRATION = "导引者这会儿不在席。刚才的规则结果已经生效，请按桌上的判定继续。"

# ── 多人房间（M10-S1）常量 ───────────────────────────────────────────────
#
# 依据 MULTIPLAYER-DESIGN.md §3.1 / §3.2 / §4.1 / §4.2：房间 == 存档
# （M-11：`room_id == sid`），成员身份 = 房间级**不透明令牌**（服务端只存
# `sha256(token)`，明文永不落盘 / 不写日志 / 不回显）。
ROOM_SCHEMA_VERSION = 1
ROOM_MAX_MEMBERS = 6                    # GDD §7.1 / §25.1：首发上限 6 名玩家角色
ROOM_ID_RE = SID_RE                     # 复用存档 sid 白名单（同一条磁盘安全边界）
MEMBER_ID_RE = re.compile(r"m-[A-Za-z0-9]{8,32}")
ROOM_TOKEN_BYTES = 32                   # 32 字节 → 64 位十六进制；只存 sha256
ROOM_ROLES = ("host", "player", "observer")
# §6.1 频道表；S1 只落常量，分流 / 扇出的实现归 S3。
ROOM_CHANNELS = ("world", "party", "org", "ooc", "host")


def _hash_token(token: str) -> str:
    """令牌只以 `sha256` 十六进制落盘（§4.1）。明文在服务端不驻留。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_member_id() -> str:
    """成员 id：`m-` + 8 位十六进制（过 `MEMBER_ID_RE`）。"""
    return "m-" + secrets.token_hex(4)


def _new_room_token() -> str:
    """下发一次的房间令牌明文（`secrets.token_hex(32)`）；此后只留 hash。"""
    return secrets.token_hex(ROOM_TOKEN_BYTES)


def _empty_room(sid: str = "") -> dict:
    """最小房间块（§3.2）：新建与**旧档惰性迁移**共用一份默认值工厂。

    `host_member_id` 为空串 = 尚无房主（旧单人档迁移态，M-16）；`room_id`
    由调用方对齐到存档 sid。`created_at` 为 0 表示「未知」（老档迁移）。
    """
    return {
        "schema_version": ROOM_SCHEMA_VERSION,
        "room_id": str(sid or ""),
        "host_member_id": "",
        "capacity": ROOM_MAX_MEMBERS,
        "created_at": 0.0,
        "paused": False,                # 房主暂停（GDD §7.6）；S1 只落字段
        "pvp_mode": "consent",          # "off" | "consent" | "on"（GDD §7.5）
        "world_key": "",                # 复用现有世界键；空 = 尚未绑定
        "scenario_id": "",              # 复用现有剧本键，可为空
        "settings": {
            # A5 / CHRON §3.4：房间无人时**默认不推进**世界。
            "offline_advance": "never",
            "content": {"violence": "medium", "adult": False},   # GDD §21.3
        },
        "members": {},
        "seq": 0,                       # 房间重连游标（M7 落地前由房间层自增）
    }


def _empty_member(member_id: str) -> dict:
    """成员记录的默认形状（§3.2 `room.members[*]`）。"""
    return {
        "member_id": member_id,
        "token_hash": "",               # 永不落明文令牌
        "display_name": "",
        "role": "player",               # "host" | "player" | "observer"
        "character_id": "",             # 归属指针；角色本体在 rules 里
        "state": "active",              # "active" | "left"
        "created_at": 0.0,
        "last_seen_at": 0.0,
    }


def _room_from(raw: object, sid: str) -> dict:
    """按键还原房间块（§3.2 加载合同）：坏键丢、缺键补，**不**因一个键换掉整块。

    `room_id` 恒等于存档 sid（M-11），`schema_version` / `capacity` 是契约常量；
    成员逐条清洗（坏 id / 非字典直接丢，未知令牌字段不会从磁盘被读进来），
    房主指向一个不存在的成员时房间回到「无主」态。
    """
    room = _empty_room(sid)
    if isinstance(raw, dict):
        for key in ("host_member_id", "created_at", "paused", "pvp_mode",
                    "world_key", "scenario_id", "settings", "seq", "members"):
            if key in raw:
                room[key] = raw[key]
    # 契约常量 / 规范清洗（不属于玩家值，永远对齐）。
    room["room_id"] = str(sid or "")
    room["schema_version"] = ROOM_SCHEMA_VERSION
    room["capacity"] = ROOM_MAX_MEMBERS
    if not isinstance(room.get("paused"), bool):
        room["paused"] = False
    if room.get("pvp_mode") not in ("off", "consent", "on"):
        room["pvp_mode"] = "consent"
    if not isinstance(room.get("settings"), dict):
        room["settings"] = _empty_room(sid)["settings"]
    if not isinstance(room.get("host_member_id"), str):
        room["host_member_id"] = ""
    room["created_at"] = _float_or(room.get("created_at"), 0.0)
    room["seq"] = max(0, _int_or(room.get("seq"), 0))
    clean: dict[str, dict] = {}
    raw_members = room.get("members")
    if isinstance(raw_members, dict):
        for mid, entry in raw_members.items():
            if not MEMBER_ID_RE.fullmatch(str(mid)) or not isinstance(entry, dict):
                continue
            member = _empty_member(str(mid))
            for key in ("token_hash", "display_name", "role", "character_id",
                        "state"):
                if isinstance(entry.get(key), str):
                    member[key] = entry[key]
            member["created_at"] = _float_or(entry.get("created_at"), 0.0)
            member["last_seen_at"] = _float_or(entry.get("last_seen_at"), 0.0)
            if member["role"] not in ROOM_ROLES:
                member["role"] = "player"
            if member["state"] not in ("active", "left"):
                member["state"] = "active"
            clean[str(mid)] = member
    room["members"] = clean
    if room["host_member_id"] and room["host_member_id"] not in clean:
        room["host_member_id"] = ""     # 房主记录已不在 → 房间回到无主态
    return room


def _room_seats(room: dict) -> list:
    """占座成员：`host` / `player` 且 `state=active`（observer 不计入上限，§4.4）。"""
    members = room.get("members") if isinstance(room, dict) else None
    if not isinstance(members, dict):
        return []
    return [m for m in members.values()
            if isinstance(m, dict) and m.get("state") == "active"
            and m.get("role") in ("host", "player")]


class _RoomAuthError(Exception):
    """鉴权链失败：带 HTTP 码与机器可读 reason（§4.1 / §7.5）。

    由 `Handler._room_auth` 抛出、端点捕获后翻成固定 JSON。**不回显**失败缘由，
    避免泄露成员是否存在。
    """

    def __init__(self, code: int, msg: str, reason: str):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.reason = reason


def _empty_guide() -> dict:
    """新存档的 `guide` 块（零参工厂：每次现造一份，含新 salt）。

    形状与加载合同都归 `prism_guide`（§7）；模块缺失时退化成一个最小空块，
    **不在这里再列一遍键**——两边各维护一套必然漂移。
    """
    if prism_guide is not None:
        return prism_guide.empty_guide()
    return {"salt": secrets.token_hex(16), "realizations": {}}


def _guide_from(raw: object) -> dict:
    """按键还原 `guide`（§7 加载合同）：只丢脏键，不因一个键把其余键删掉。"""
    if prism_guide is not None:
        return prism_guide.guide_from(raw)
    return _empty_guide()


def _guide_fallback_text(result=None) -> str:
    """叙事失败时的正文：保留服务端裁决，正文用兜底句（§5.2）。"""
    if prism_guide is not None:
        return prism_guide.fallback_text(result)
    return FALLBACK_NARRATION


def _guide_beats(narration, guide) -> list:
    """切出这一回合的节拍（§5.9）。模块缺失时没有拍——不朗读。"""
    if prism_guide is None:
        return []
    try:
        return prism_guide.beats_of(narration, guide)
    except Exception:      # noqa: BLE001 — 切拍失败不该带走整回合
        return []


# 速率窗口：{sid: {"turn": [时间戳…], "speak": […]}}。窗口滑动、只留 60 秒内的。
_guide_rates: dict[str, dict[str, list]] = {}
_guide_rates_lock = threading.RLock()


def _rate_ok(sid: str, kind: str, limit: int) -> bool:
    """记一次调用并判断是否超限；超限**不**记这一次（窗口自然回滚）。"""
    now = time.monotonic()
    with _guide_rates_lock:
        buckets = _guide_rates.setdefault(sid, {"turn": [], "speak": []})
        bucket = buckets.setdefault(kind, [])
        bucket[:] = [stamp for stamp in bucket
                     if now - stamp < GUIDE_RATE_WINDOW_S]
        if len(bucket) >= limit:
            return False
        bucket.append(now)
        return True


def _empty_rules_snapshot(sid: str = "") -> dict:
    """空规则会话的落盘快照；零参调用即 `_SESSION_DEFAULTS` 的默认值工厂。

    规则态的形状归 prism_core 定义，本文件只调用它的快照 / 还原接口，
    不另写一份字段名——两边各维护一套必然漂移。

    TODO(M3)：起始场景 / 起始队伍随剧本数据落地后，在这里按世界模组生成，
    老存档仍走「只补不覆盖」的惰性迁移拿到它。
    """
    return prism_core.RuleSession(str(sid or "")).snapshot()


# 会话字段默认值表：load() 的惰性迁移按此补缺。M3 新增 PRISM 字段时
# 在这里加一行并同步 Session.to_dict，老存档即自动获得默认值。
# 值 = 字面量（不可变）或**零参工厂**（可变）：工厂每次现造一个**新**对象，
# 绝不把同一个 list / dict 借给多个存档——否则一个存档的改动会顺着默认值
# 漏进另一个老存档，且这种串档在单存档测试里看不出来。
_SESSION_DEFAULTS: dict[str, object] = {
    "created": 0.0,                     # 建局时间戳；老档缺失按 0（未知）
    "log": list,                        # 叙事流条目（工厂：现造空列表）
    "seq": 0,                           # 日志序号游标（增量拉取用）
    "save_name": "",                    # 玩家自定义展示名
    "rules": _empty_rules_snapshot,     # PRISM 规则会话快照（工厂：现造空会话）
    "atlas": None,                      # ATLAS 存档块（§3.4）；老档缺失按 None，
                                        # 第一次用到地图时按当前世界懒编译
    "guide": _empty_guide,              # 导引者状态块（工厂：现造，salt 新掷）
    "room": _empty_room,                # 多人房间块（§3.2）；工厂：现造最小房间，
                                        # 老档缺失即惰性迁移成「单人房」（M-16）
}


# --------------------------------------------------------------------------
# ATLAS 自动地图接线（切片 I4，Issue #66）
# --------------------------------------------------------------------------

#: 行程档 → 名义分钟数（CHRON-DESIGN.md §2.3 / data/system/travel.json：
#: 短程 360、中程 720、远程 1440、危险穿越 1440，1 时段 = 360 名义分钟）。
BAND_MINUTES = {"short": 360, "medium": 720, "long": 1440, "dangerous": 1440}

#: 行程档的显示名；band=None 是门 / 连接——地点内部的走动不是行程，不计行程耗时。
BAND_NAMES = {"short": "短程", "medium": "中程", "long": "远程",
              "dangerous": "危险穿越", None: "门内"}

_LEXICON_CACHE: dict | None = None
_WORLD_CACHE: dict[str, dict] = {}
_DATA_LOCK = threading.Lock()


def _load_lexicon() -> dict:
    """读 data/atlas/lexicon.json（惰性 + 缓存：import 本模块不碰磁盘）。"""
    global _LEXICON_CACHE
    with _DATA_LOCK:
        if _LEXICON_CACHE is None:
            path = HERE / "data" / "atlas" / "lexicon.json"
            _LEXICON_CACHE = json.loads(path.read_text(encoding="utf-8"))
        return _LEXICON_CACHE


def _load_world(world_key: str) -> dict:
    """读 data/worlds/<key>.json（惰性 + 缓存）。

    world_key 会拼进文件路径，先过与 sid 同样的白名单，杜绝 ../ 探测；
    源码不出现任何世界的名字——默认世界由数据目录内容决定。
    """
    if not SID_RE.fullmatch(world_key or ""):
        raise ValueError("未知世界: %r" % (world_key,))
    with _DATA_LOCK:
        world = _WORLD_CACHE.get(world_key)
        if world is None:
            path = HERE / "data" / "worlds" / f"{world_key}.json"
            if not path.is_file():
                raise ValueError("未知世界: %s" % world_key)
            world = json.loads(path.read_text(encoding="utf-8"))
            _WORLD_CACHE[world_key] = world
        return world


def _default_world_key() -> str:
    """默认世界 = data/worlds/ 下字典序第一份世界模组。

    TODO(M3)：世界 / 剧本选择落地后，这里换成会话自己选的世界；
    存档块里已带 world_key 的局不受影响（还原一律按块里的来）。
    """
    names = sorted(p.stem for p in (HERE / "data" / "worlds").glob("*.json"))
    if not names:
        raise ValueError("data/worlds/ 下没有世界模组")
    return names[0]


def _compile_world(world_key: str, seed: int) -> dict:
    """把一份世界模组编译成地图（atlas_compile.compile_world，纯数据）。"""
    return atlas_compile.compile_world(_load_world(world_key),
                                       _load_lexicon(), seed)


def _new_seed() -> int:
    """新局的地图种子：系统熵源，随存档块落盘后即可复现。"""
    return random.SystemRandom().randrange(1, 2 ** 31)


def _starting_locus(atlas: dict) -> dict:
    """新地图的队伍起点：id 最小的 authored 区域；没有区域时退到
    第一个 authored 地点。编译结果里连 authored 都没有就报错——
    那是数据问题，不是会话层该兜的。"""
    fallback = None
    for fid in sorted(atlas["frames"]):
        for pid in sorted(atlas["frames"][fid]["places"]):
            place = atlas["frames"][fid]["places"][pid]
            if place.get("source") != "authored":
                continue
            locus = {"frame_id": fid, "place_id": pid}
            for axis in ("x", "y", "z"):
                if axis in place:
                    locus[axis] = place[axis]
            if place.get("kind") == "region":
                return locus
            if fallback is None:
                fallback = locus
    if fallback is not None:
        return fallback
    raise ValueError("世界编译结果里没有任何 authored 地点")


def _ensure_save_dir() -> None:
    """惰性创建存档目录：import 不碰磁盘，第一次需要写盘时才创建。"""
    SAVE_DIR.mkdir(parents=True, exist_ok=True)


def _int_or(value: object, default: int = 0) -> int:
    """脏值降级：转不成 int 就用默认值，不让坏数据把加载 / 列表打崩。"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _float_or(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _text_or(value: object, limit: int = 0) -> str:
    """转成字符串并裁到上限长度；None / 脏值 → 空串。"""
    out = str(value or "")
    return out[:limit] if limit else out


def _restore_rules(raw: object, sid: str) -> dict:
    """把落盘的规则快照还原成规范形状；坏值降级为空会话，不抛。

    往返路径固定为 `from_snapshot()` → `snapshot()`：字段形状以 prism_core
    为准，存档层只负责调用（顺手把未知键剔掉），不在这里二次清洗。
    """
    if isinstance(raw, dict):
        try:
            snapshot = prism_core.RuleSession.from_snapshot(raw).snapshot()
        except Exception:
            snapshot = None      # 脏快照（如 pressure 是文字）→ 落到空会话
        if snapshot is not None:
            # 只补不覆盖：快照没写 sid 时与存档 sid 对齐，写了就不动。
            if not snapshot.get("sid"):
                snapshot["sid"] = str(sid)
            return snapshot
    return _empty_rules_snapshot(sid)


# --------------------------------------------------------------------------
# 存档骨架
# --------------------------------------------------------------------------


class Session:
    """一局游戏的持久化骨架（**存档容器**，不是规则运行时）。

    本类只定义**工程字段**（标识 / 日志 / 展示名）与 `rules` 快照。
    规则态没有第二套字段：角色卡六维、场景、战斗、压力……全都收在
    `rules` 里，形状由 prism_core.RuleSession 定义（规则运行时真相在那边，
    本类只负责把它原子落盘、惰性迁移、按请求透出）。
    新增字段时必须同时给出默认值（同步 _SESSION_DEFAULTS），load() 的
    惰性迁移会自动为老存档补齐。
    """

    def __init__(self, sid: str):
        self.sid = sid
        self.lock = threading.RLock()   # 单局锁：日志追加 / 落盘互斥
        self.created = time.time()
        self.log: list[dict] = []       # 叙事流；seq 单调递增，供增量拉取
        self.seq = 0
        # 展示名：玩家可改名。空串 = 用默认名（见 default_display_name）。
        # ⚠️ 改名只动这个字段，**不动磁盘文件名、也不动 self.sid**——
        # sid 是内存缓存的键、也是请求头里的值，动它会让在途请求全部失败。
        self.save_name = ""
        # 规则会话快照（JSON 可序列化）：新建即空规则会话，落盘 / 载入由
        # load() 与 to_dict() 负责，本类不解释其中的规则语义。
        self.rules: dict = _empty_rules_snapshot(sid)
        # ATLAS 存档块（§3.4，与 rules 平级）：落盘的是「种子 + 增量 + 队伍
        # 位置」；运行时地图（self._atlas / self._locus）由 ensure_atlas()
        # 懒编译 / 还原，不直接落盘。to_dict() 把运行时折回存档块。
        self.atlas_block: dict | None = None
        self._atlas: dict | None = None      # 运行时地图（atlas.py 纯数据）
        self._locus: dict | None = None      # 队伍位置 {frame_id, place_id, ...}
        # 导引者状态块（对局里的 salt / 前缀 / 实相等）：与 rules 平级，
        # 同样是**快照字典**。新存档在这一刻生成 salt（§7）。模块缺失时是
        # 最小空块；加载路径由 load() 调 _guide_from() 按键还原。
        self.guide: dict = _empty_guide()
        # 多人房间块（§3.2，与 rules / atlas / guide 平级）：新存档起最小房间
        # （无房主、无成员）；成员由 /api/room/* 写入。老档迁移路径见 load()。
        self.room: dict = _empty_room(sid)

    # ── 持久化 ────────────────────────────────────────────
    def to_dict(self) -> dict:
        """白名单序列化：**显式**列出落盘字段，防止内部属性被顺手写盘。"""
        return {
            "sid": self.sid,
            "created": self.created,
            "log": self.log,
            "seq": self.seq,
            "save_name": self.save_name,
            "rules": self.rules,
            "atlas": self._export_atlas_block(),
            "guide": self.guide,
            "room": self.room,
        }

    def _export_atlas_block(self) -> dict | None:
        """把运行时地图折回 §3.4 存档块（atlas.export_state）。

        还没碰过地图（未 ensure_atlas）时原样返回已存的块（老档是 None），
        不为序列化去触发编译——落盘不该有「顺手编译整个世界」的副作用。
        """
        if self._atlas is not None:
            self.atlas_block = atlas_kernel.export_state(self._atlas, self._locus)
        return copy.deepcopy(self.atlas_block)

    def save(self) -> None:
        """原子写：先写同目录 .tmp，再 os.replace 换名——中断不会留下半个 JSON。

        写入失败不抛（best-effort）：持久化路径与请求处理解耦；确实关心
        结果的调用方在 save() 之后**回读校验**（见 /api/save/rename）。
        """
        try:
            _ensure_save_dir()
            f = SAVE_DIR / f"{self.sid}.json"
            tmp = SAVE_DIR / f".{self.sid}.tmp"
            with self.lock:
                payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=1)
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, f)
        except Exception:
            pass

    @classmethod
    def load(cls, sid: str) -> "Session | None":
        """加载 + 惰性迁移（缺字段补默认、脏值降级），不写迁移脚本。

        原则：**只补不存在的，不动已有的合法值**——玩家或更新版本写过的
        字段不会被旧代码覆盖；老存档缺的字段在这一刻获得默认值。
        """
        # sid 先过白名单再碰文件系统：即使请求头被伪造，也无法用 ../ 探测路径。
        if not SID_RE.fullmatch(sid or ""):
            return None
        f = SAVE_DIR / f"{sid}.json"
        if not f.is_file():
            return None
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(d, dict):
            return None

        # 惰性迁移：原始 JSON 缺字段时在内存里补默认值（setdefault 语义：
        # 只补缺、不覆盖），无需在磁盘上跑迁移脚本。
        # 默认值是**工厂**时现造一个（可变默认值不共享，见 _SESSION_DEFAULTS）。
        for key, default in _SESSION_DEFAULTS.items():
            if key not in d:
                d[key] = default() if callable(default) else default

        s = cls(sid)
        # —— 逐字段清洗：类型不对就退回默认值（脏值降级）——
        s.created = _float_or(d.get("created"), 0.0)
        if isinstance(d.get("log"), list):
            s.log = [e for e in d["log"] if isinstance(e, dict)]
        s.seq = max(0, _int_or(d.get("seq"), 0))
        # seq 是增量拉取游标：脏数据里若 seq 落后于日志本身，取二者最大值，
        # 否则客户端重连后会把已读过的条目再推一遍。
        if s.log:
            s.seq = max(s.seq, max((_int_or(e.get("seq"), 0) for e in s.log), default=0))
        s.save_name = _text_or(d.get("save_name"), 40)
        # 规则会话：老档（缺 rules）在这一刻拿到空规则会话，形状按 prism_core
        # 的契约还原（未知键剔掉、脏值降级），不就地改写磁盘上的老档。
        s.rules = _restore_rules(d.get("rules"), sid)
        # ATLAS 存档块：老档（缺块 / 脏值）按 None 处理，第一次用到地图时
        # 由 ensure_atlas() 按当前世界懒编译，不报错、不改写磁盘老档。
        block = d.get("atlas")
        s.atlas_block = copy.deepcopy(block) if isinstance(block, dict) else None
        # 导引者块：**显式**在 rules 旁边还原（§7 加载合同，G2 就要做）。
        # 按键处理——旁边一个键脏了不会把整块换成空块（否则会删掉 realizations）。
        s.guide = _guide_from(d.get("guide"))
        # 多人房间块（§3.2 / M-16）：缺块的老档在这一刻惰性迁移成「单人房」
        # （host 空、members 空），只补不覆盖；磁盘上的老档不被就地改写。
        s.room = _room_from(d.get("room"), sid)
        # TODO(M3)：PRISM 存档层字段（世界 id / 进度索引等**索引 / 展示**用途的
        # 派生字段）的迁移规则加在这里（同样：只补不覆盖）；规则态本身不进这里，
        # 统一放 s.rules。
        return s

    # ── 日志 ──────────────────────────────────────────────
    def add_log(self, kind: str, text: str, speaker: str = "",
                extra: dict | None = None) -> dict:
        """追加一条叙事流记录并推进 seq。

        ⚠️ seq 必须在锁内递增：并发请求若读到同一个 seq，增量拉取（?since=）
        就会永久丢掉其中一条。TODO(M3)：kind 取值由 PRISM 会话核心定义，
        本骨架不预设。
        """
        with self.lock:
            self.seq += 1
            entry = {
                "seq": self.seq,
                "kind": kind,
                "text": text,
                "speaker": speaker,
                "ts": int(time.time() * 1000),
            }
            if extra:
                entry.update(extra)
            self.log.append(entry)
            return entry

    # ── ATLAS 自动地图（切片 I4）──────────────────────────
    def ensure_atlas(self) -> dict:
        """拿到运行时地图：有存档块就按块还原，没有就按当前世界懒编译。

        · 旧存档没有 atlas 块 → 懒编译默认世界，不报错，说明写进会话日志；
        · 世界 JSON 删掉了队伍所在地点 → atlas.restore_state 把队伍搬迁到
          坐标最接近的 authored 区域，搬迁说明写进会话日志（§3.4）；
        · 存档块本身损坏（版本不支持等）→ 降级为按当前世界重编，不抛。
        """
        with self.lock:
            if self._atlas is not None:
                return self._atlas
            notes: list[str] = []
            block = self.atlas_block if isinstance(self.atlas_block, dict) else None
            if block and block.get("world_key"):
                fresh = _compile_world(str(block["world_key"]),
                                       _int_or(block.get("seed"), 0))
                try:
                    restored = atlas_kernel.restore_state(fresh, block)
                except Exception:
                    restored = {"atlas": fresh, "locus": None,
                                "notes": ["地图存档块损坏，已按当前世界重新编译"]}
                self._atlas = restored["atlas"]
                notes.extend(restored.get("notes") or [])
                locus = restored.get("locus")
                if locus is None:
                    locus = _starting_locus(self._atlas)
                    notes.append("存档没有可用的队伍位置，落到起始区域")
                self._locus = locus
            else:
                world_key = _default_world_key()
                self._atlas = _compile_world(world_key, _new_seed())
                self._locus = _starting_locus(self._atlas)
                notes.append("没有地图存档，按世界「%s」懒编译了自动地图" % world_key)
            for note in notes:
                self.add_log("atlas", note)
            return self._atlas

    def atlas_view(self) -> dict:
        """出口查看视图：文本出口列表 + 结构化字段（GET /api/atlas/exits）。

        返回纯文本与 JSON 字段，不含任何图片（§2：默认看出口列表）。
        """
        self.ensure_atlas()
        with self.lock:
            place = atlas_kernel.find_place(self._atlas,
                                            self._locus.get("place_id"))
            exits = atlas_kernel.exits(self._atlas, self._locus)
            for entry in exits:
                entry["minutes"] = BAND_MINUTES.get(entry["band"], 0)
            frame = self._atlas["frames"][place["frame_id"]]
            here = {
                "place_id": place["id"],
                "name": place["name"],
                "kind": place["kind"],
                "frame_id": place["frame_id"],
                "space": frame["space"],
                "cell": frame["cell"],
                "world_key": self._atlas["world_key"],
                "scale": self._atlas.get("scale"),
            }
            lines = ["当前位置：%s（%s）" % (place["name"], place["id"])]
            if exits:
                lines.append("出口：")
                for entry in exits:
                    label = BAND_NAMES.get(entry["band"], "门内")
                    minutes = "· %d 分钟" % entry["minutes"] if entry["minutes"] else ""
                    flag = "（不稳）" if entry["unstable"] else ""
                    lines.append("  %s → %s（%s%s）%s"
                                 % (entry["via"], entry["name"], label,
                                    minutes, flag))
            else:
                lines.append("这里没有已知出口。")
            return {"here": here, "exits": exits, "text": "\n".join(lines)}

    def atlas_move(self, via: object) -> dict:
        """沿 via 移动队伍：返回行程档与名义分钟（POST /api/atlas/move）。

        行程档来自连接自身的 band（编译期定档，§5.2）；分钟换算按
        BAND_MINUTES（CHRON-DESIGN.md §2.3：1 时段 = 360 名义分钟）。内核
        （atlas.py）不改风险池，本层也暂不接风险判定——dangerous 档
        「每时段一次风险判定」留给 M3 的移动接线（prism_core.risk_roll 是纯函数）。
        """
        via = str(via or "").strip()
        if not via:
            raise ValueError("缺少移动方向 via")
        self.ensure_atlas()
        with self.lock:
            band = next((entry.get("band")
                         for entry in atlas_kernel.exits(self._atlas, self._locus)
                         if entry["via"] == via), None)
            result = atlas_kernel.move(self._atlas, self._locus, via)
            if result.get("error"):
                raise ValueError(str(result["error"]))
            self._locus = result["locus"]
            view = self.atlas_view()
            view["status"] = "ok"
            view["band"] = band
            view["minutes"] = BAND_MINUTES.get(band, 0)
            return view


_sessions: dict[str, Session] = {}
_sessions_lock = threading.RLock()


def get_session(sid: str) -> Session | None:
    """内存缓存 + 锁 + 惰性加载：命中即返回；未命中去磁盘载入并缓存。

    读盘放在全局锁外：磁盘慢，别让一个慢请求卡住全部会话。
    双检用 setdefault：并发同时 miss 时只保留先到实例，返回的永远是缓存里
    **同一个**可变对象——否则同一局会出现两个互不知情的副本，更新互相丢失。
    """
    with _sessions_lock:
        s = _sessions.get(sid)
        if s:
            return s
    s = Session.load(sid)
    if s is None:
        return None
    with _sessions_lock:
        return _sessions.setdefault(sid, s)


def default_display_name(sid: str) -> str:
    """未命名存档的默认展示名。刻意实时计算、不写进存档字段。

    TODO(M3)：默认名加入世界 / 角色 / 进度等 PRISM 摘要信息，
    这样推进剧情后列表里会跟着更新，不必每次 save() 都改写磁盘。
    """
    return f"存档 {sid}"


def list_saves() -> tuple[list[dict], int]:
    """扫描存档目录，返回 (摘要列表, 实际总条数)。

    列表只返回最近 SAVE_LIST_LIMIT 条（按 mtime 倒序），但总条数照实返回，
    前端才能提示「还有更多旧存档未显示」，而不是假装只有这些。
    磁盘文件一律不删——只截断返回给前端的部分。
    """
    rows: list[dict] = []
    try:
        files = sorted(SAVE_DIR.glob("*.json"))
    except Exception:
        return [], 0
    for f in files:
        if f.name.startswith("."):
            continue            # 原子写留下的 .tmp 等临时文件
        sid = f.stem
        if not SID_RE.fullmatch(sid):
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue            # 半个 / 手改坏的 JSON 不进列表，也不让整次扫描失败
        if not isinstance(d, dict):
            continue
        try:
            mt = f.stat().st_mtime
        except OSError:
            mt = 0.0
        log = d.get("log") if isinstance(d.get("log"), list) else []
        save_name = _text_or(d.get("save_name"), 40)
        rows.append({
            "id": sid,
            "name": save_name or default_display_name(sid),
            "named": bool(save_name),
            "mtime": mt,
            "mtime_text": time.strftime("%m-%d %H:%M", time.localtime(mt)) if mt else "—",
            "log_count": len(log),
            # TODO(M3)：PRISM 摘要字段（世界 / 角色 / 进度……）在此追加。
        })
    rows.sort(key=lambda r: -r["mtime"])
    total = len(rows)
    return rows[:SAVE_LIST_LIMIT], total


def rules_view(session: Session) -> dict:
    """规则会话的只读视图（`GET /api/session` 直接吃这份）。

    字段形状**全部**来自 prism_core.RuleSession.snapshot()（party / scene /
    active_unit_id / log / done_actions / action_fails / pressure / combat），
    这里只做深拷贝——存档层另立一套字段名就必然与规则层漂移。可见性由
    规则层决定：当前还没有需要隐藏的内部字段，故不做额外裁剪。

    额外附一个 `save` 块：规则视图本身不带展示名 / 日志游标，前端拿到
    整局视图时不必再多打一次 /api/saves。
    """
    rules = session.rules if isinstance(session.rules, dict) else {}
    view = copy.deepcopy(rules)
    view.setdefault("sid", session.sid)
    view["save"] = {
        "name": session.save_name or default_display_name(session.sid),
        "named": bool(session.save_name),
        "created": session.created,
        "seq": session.seq,
        "log_count": len(session.log),
    }
    return view


# --------------------------------------------------------------------------
# HTTP 骨架
# --------------------------------------------------------------------------

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "NotDND/0.1"

    def log_message(self, fmt, *args):  # 静音默认访问日志
        pass

    # ── 响应 helper ────────────────────────────────────────
    def _send(self, code: int, body: bytes,
              ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 状态 / 存档一律 no-store：手机浏览器爱缓存，缓存住一次旧状态
        # 会让玩家看到过期界面而毫无提示。
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass                # 客户端中途断线（锁屏 / 切网）不该让服务端报错

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _err(self, msg: str, code: int = 400, reason: str = ""):
        """错误响应：`error` 是人话，`reason` 是机器可读原因（§7.5）。

        `reason` 缺省不写，保持既有端点的响应形状不变；多人房间端点按
        §7.5 一律带上（`room_full` / `not_authorized` / `token_invalid` …）。
        """
        body = {"error": msg}
        if reason:
            body["reason"] = reason
        self._json(body, code)

    def _body(self) -> dict:
        """读取并解析 JSON 请求体：上限 2MB；畸形体抛 ValueError → 400。"""
        n = _int_or(self.headers.get("Content-Length"), 0)
        if n <= 0:
            return {}
        if n > 2_000_000:
            raise ValueError("请求体过大")
        raw = self.rfile.read(n)
        try:
            d = json.loads(raw.decode("utf-8"))
        except Exception:
            raise ValueError("JSON 解析失败")
        return d if isinstance(d, dict) else {}

    def _sess(self) -> Session:
        """从 X-Session 头或 ?sid= 取会话；取不到抛 ValueError → 400。"""
        sid = (self.headers.get("X-Session") or "").strip()
        if not sid and self.path.startswith("/api/") and "?sid=" in self.path:
            sid = self.path.split("sid=", 1)[1].split("&")[0]
        s = get_session(sid) if sid else None
        if not s:
            raise ValueError("会话不存在或已过期")
        return s

    def _static(self, rel: str):
        """服务 static/ 下的文件；resolve 后做边界校验，阻断 ../ 目录穿越。"""
        base = STATIC_DIR.resolve()
        p = (base / rel).resolve()
        # relative_to 抛 ValueError = 解析后跑到了 static/ 之外。
        # 比字符串前缀比较稳：前缀比较会把 static-other/ 误判成 static/ 内部。
        try:
            p.relative_to(base)
        except ValueError:
            self._err("资源不存在", 404)
            return
        if not p.is_file():
            self._err("资源不存在", 404)
            return
        self._send(200, p.read_bytes(), MIME.get(p.suffix, "application/octet-stream"))

    # ── 流式响应（导引者专用，§5.10）────────────────────────
    def _begin_stream(self):
        """打开导引者的流式响应：HTTP/1.1 + 分块，**只调用一次**。

        `protocol_version` 在 `send_response` 之前设成 HTTP/1.1（默认 HTTP/1.0）；
        JSON / 4xx / 429 / 淡出仍走 HTTP/1.0。没有 `Content-Length`——
        长度未知，只能分块。
        """
        self.protocol_version = "HTTP/1.1"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _begin_audio_stream(self):
        """打开导引者的音频流式响应（§5.9 / §5.10）：HTTP/1.1 + 分块。

        与叙事同一条流式口径（无 `Content-Length`，`Connection: close`），
        但体是 pcm16 字节而不是 SSE。速率与格式都写进头：浏览器不必猜
        24 kHz / mono / s16le。
        """
        self.protocol_version = "HTTP/1.1"
        self.send_response(200)
        self.send_header("Content-Type", "audio/" + GUIDE_AUDIO_FORMAT)
        self.send_header("X-Audio-Format", GUIDE_AUDIO_FORMAT)
        self.send_header("X-Audio-Sample-Rate", GUIDE_AUDIO_RATE)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

    def _chunk(self, data: bytes) -> None:
        """写一个 HTTP/1.1 分块：十六进制长度 + CRLF + 数据 + CRLF。"""
        self.wfile.write(("%x\r\n" % len(data)).encode("ascii") + data + b"\r\n")

    def _sse(self, event: str, obj) -> None:
        """写一个 SSE 事件：`event:` + 一行 JSON `data:` + 空行。"""
        payload = ("event: %s\ndata: %s\n\n"
                   % (event, json.dumps(obj, ensure_ascii=False)))
        self._chunk(payload.encode("utf-8"))

    def _sse_close(self) -> None:
        """写零长度块收尾——**不再**发第二行 HTTP 状态（§5.2）。"""
        self.wfile.write(b"0\r\n\r\n")

    # ── GET（只读）─────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path == "/api/guide/status":
                # 三个布尔，不需要会话（§5.10）。模块缺失时三个都是假。
                if prism_guide is None:
                    return self._json({"chat": False, "tts": False,
                                       "configured": False})
                return self._json(prism_guide.status())
            if path == "/api/saves":
                # 开始界面还没有会话也要能列存档：故意不调 _sess()。
                cur = (self.headers.get("X-Session") or "").strip()
                if "?sid=" in self.path:
                    cur = self.path.split("sid=", 1)[1].split("&")[0] or cur
                saves, total = list_saves()
                # save_dir 回传：测试子进程读不到启动者的环境变量，
                # 需要「问服务端」才知道存档写到了哪里。
                return self._json({
                    "save_dir": str(SAVE_DIR),
                    "current": cur,
                    "saves": saves,
                    "total": total,
                    "truncated": max(0, total - len(saves)),
                })
            if path == "/api/log":
                # seq 增量拉取：只回 ?since= 之后的条目，重连不必重传全量。
                s = self._sess()
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                since = _int_or((q.get("since") or ["0"])[0], 0)
                return self._json(
                    {"log": [e for e in s.log if _int_or(e.get("seq"), 0) > since]})
            if path == "/api/session":
                # 规则会话只读视图：sid 走 X-Session 头（同 /api/log），
                # 未开会话由 _sess() 抛 ValueError → 既有的 400 语义。
                # 取快照要在单局锁内：写盘 / 追日志是别的线程在做，
                # 不然可能读到一个改了一半的回合。
                s = self._sess()
                with s.lock:
                    view = rules_view(s)
                return self._json(view)
            if path == "/api/atlas/exits":
                # 查看当前地点的出口：返回文本出口列表（不是图片，§2）。
                # 只读接口：不 save()，不改写存档。
                s = self._sess()
                return self._json(s.atlas_view())
            # TODO(M3)：PRISM 世界 / 剧本等其余只读接口在此追加（路径 if 链）。
            # 其余路径一律按静态资源找；找不到就 404。
            rel = path.lstrip("/")
            if rel:
                return self._static(rel)
            self._err("未知路径", 404)
        except ValueError as e:
            self._err(str(e), 400)
        except Exception as e:  # noqa: BLE001 — 单个请求出错不该带走整个服务
            self._err(f"服务器内部错误：{e}", 500)

    # ── POST（写入）────────────────────────────────────────
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            b = self._body()

            if path == "/api/guide/turn":
                return self._guide_turn(b)

            if path == "/api/guide/bind":
                return self._guide_bind(b)

            if path == "/api/guide/speak":
                return self._guide_speak(b)

            if path == "/api/room/create":
                return self._room_create(b)

            if path == "/api/room/join":
                return self._room_join(b)

            if path == "/api/room/leave":
                return self._room_leave(b)

            if path == "/api/room/kick":
                return self._room_kick(b)

            if path == "/api/save/rename":
                # 目标 sid 来自请求体，不是 X-Session 头（头里是当前打开那局）。
                sid = str(b.get("sid") or "").strip()
                name = str(b.get("name") or "").strip()
                if not SID_RE.fullmatch(sid):
                    return self._err("存档不存在", 404)
                if not name or len(name) > 40:
                    return self._err("存档名需为 1-40 个字符", 400)
                s = get_session(sid)
                if not s:
                    return self._err("存档不存在", 404)
                # 界线 1：改名只改展示名，**不动 sid、不动磁盘文件名**。
                with s.lock:
                    s.save_name = name
                    s.save()
                # 界线 2：save() 失败不抛，这里回读校验——显示「改名成功」
                # 却实际没落盘，是最难查的一类假成功。
                try:
                    d = json.loads((SAVE_DIR / f"{sid}.json").read_text(encoding="utf-8"))
                except Exception:
                    d = {}
                if str(d.get("save_name") or "") != name:
                    return self._err("保存失败，请重试", 500)
                return self._json({"status": "ok", "sid": sid, "name": name})

            if path == "/api/save/delete":
                sid = str(b.get("sid") or "").strip()
                if not SID_RE.fullmatch(sid):
                    return self._err("存档不存在", 404)
                # 界线：不能删掉当前正在使用的存档（前端用 X-Session 表明自己那局）。
                if sid == (self.headers.get("X-Session") or "").strip():
                    return self._err("不能删除正在进行的存档", 400)
                f = SAVE_DIR / f"{sid}.json"
                if not f.is_file():
                    return self._err("存档不存在", 404)
                with _sessions_lock:
                    _sessions.pop(sid, None)    # 先摘缓存，再删磁盘，避免脏读
                try:
                    f.unlink(missing_ok=True)
                    (SAVE_DIR / f".{sid}.tmp").unlink(missing_ok=True)
                except OSError:
                    return self._err("删除失败", 500)
                return self._json({"status": "ok", "sid": sid})

            if path == "/api/atlas/move":
                # 沿 via 移动队伍：返回行程档 + 名义分钟 + 新位置的出口视图。
                # 方向不存在 / 没有该出口时 atlas_move 抛 ValueError → 400。
                s = self._sess()
                result = s.atlas_move(b.get("via"))
                s.save()        # 位置改变要落盘：重新加载后队伍还在原地
                return self._json(result)

            # TODO(M3)：PRISM 会话 / 结算等写入接口在此追加（路径 if 链）。
            return self._err("未知接口", 404)
        except ValueError as e:
            self._err(str(e), 400)
        except Exception as e:  # noqa: BLE001
            self._err(f"服务器内部错误：{e}", 500)

    # ── 导引者绑定（G5，§6）────────────────────────────────
    def _guide_bind(self, body: dict):
        """`POST /api/guide/bind`：记录显式的 `world_key` / `scenario_id`。

        体只有 `{"world_key", "scenario_id"}`，都走 `X-Session` 那一局。
        校验与写盘都归 `prism_guide.bind`（它自己加锁并 `save()`），这里只把
        `ValueError` 翻成固定 JSON：`没有这场战役` / `世界对不上` 是 400，
        `已经绑定` 是 409（§6）。没有 SSE，也没有第二套状态码。
        """
        sid = (self.headers.get("X-Session") or "").strip()
        session = get_session(sid) if sid else None
        if not session:
            return self._err("会话不存在或已过期", 400)
        if prism_guide is None:
            return self._err(GUIDE_ABSENT_ERROR, 400)
        world_key = str(body.get("world_key") or "").strip()
        scenario_id = str(body.get("scenario_id") or "").strip()
        try:
            bound = prism_guide.bind(session, world_key, scenario_id)
        except ValueError as exc:
            return self._err(str(exc),
                             GUIDE_BIND_ERRORS.get(str(exc), GUIDE_BIND_DEFAULT))
        return self._json({"status": "ok", **bound})

    # ── 导引者回合（G2，§5.2 / §5.10）───────────────────────
    def _guide_turn(self, body: dict):
        """`POST /api/guide/turn`：先结算，后叙事。

        顺序（§5.2）：长度 → 会话 → 速率 → 淡出整句——四步都还没写状态行。
        可选的 `location_id` 在**状态行之前**校验（不是已绑定剧本里的地点就是
        400 `没有这个地点`，不叫模型）；有 `action_id` 时同样在状态行之前结算：
        `ValueError` 变成固定 JSON。只有这些都通过之后才打开 HTTP/1.1 分块；
        头写出之后的失败一律走 `fallback` 事件，`_err` 不再出现（否则就是第二行
        HTTP 状态）。

        `location_id` 非空时：写入 `focus_location_id` 并落盘（焦点是显式的，
        不从玩家散文里猜），然后在状态行之后、叙事之前调用一次
        `ensure_realization`（G5 是空实现，不会为实相再开一次 SSE 分支）。

        G7：焦点**离开**某地点（或 `rules.scene.id` 变化）时，在实相之后、叙事
        之前记一轮场外痕迹；玩家可见的整段叙事在进 L3 与切拍之前过秘密门；
        该回合的 `narration` 事件至多带一条服务端揭开的 `trace`。三件事都只动
        `prism_guide`，失败一律吞掉，绝不把整回合改成兜底句或第二行 HTTP 状态。
        """
        text = str(body.get("text") or "")
        if len(text) > GUIDE_TEXT_LIMIT:
            return self._err("这句话太长", 400)
        sid = (self.headers.get("X-Session") or "").strip()
        session = get_session(sid) if sid else None
        if not session:
            return self._err("会话不存在或已过期", 400)
        if not _rate_ok(sid, "turn", GUIDE_TURN_LIMIT):
            return self._err("太频繁", 429)

        stripped = text.strip()
        if stripped == GUIDE_FADE_WORD:
            # 淡出 / 跳过是整句相等：**不结算**，也不叫模型（§5.10）。
            return self._json({"status": "ok",
                               "narration": {"text": GUIDE_FADE_TEXT}})
        if stripped == GUIDE_SKIP_WORD:
            return self._json({"status": "ok",
                               "narration": {"text": GUIDE_SKIP_TEXT}})

        if prism_guide is None:
            # 模块缺失：状态行之前返回固定 JSON 兜底，不结算（§5.1）。
            return self._json({"status": "offline",
                               "narration": {"text": FALLBACK_NARRATION}})

        # 焦点的校验在状态行之前（§2.1 / §5.10）：不是已绑定剧本里的地点就是
        # 400，不叫模型。合法时才在下面写 `focus_location_id`。
        location_id = str(body.get("location_id") or "")
        if location_id and not prism_guide.location_ok(session.guide,
                                                       location_id):
            return self._err(GUIDE_NO_LOCATION, 400)

        action_id = str(body.get("action_id") or "")
        result = None
        if action_id:
            try:
                result = prism_guide.settle(session, action_id)
            except ValueError as exc:
                phrase, code = GUIDE_SETTLE_ERRORS.get(str(exc),
                                                       GUIDE_SETTLE_DEFAULT)
                return self._err(phrase, code)

        # 变动之前先把焦点与检查点各留一份：G7 的场外痕迹与「痕迹揭开」看的是
        # **离开的地点**（§2.2），不是写完之后的那个新焦点。
        previous_focus = str(session.guide.get("focus_location_id") or "")
        # L2 只在 scene.id 变化时重写（并清空 L3）；焦点变化要立刻落盘——
        # 这一回合即使叙事失败，G6 的下一回合也要看得到队伍走到了哪里。
        with session.lock:
            scene_before = str(session.guide.get("l2_scene_id") or "")
            prism_guide.ensure_l2(session.guide, session.rules)
            if location_id:
                session.guide["focus_location_id"] = location_id
                session.save()
        current_focus = str(session.guide.get("focus_location_id") or "")
        scene_now = str((session.rules.get("scene") or {}).get("id") or "")
        # 两条触发线（§2.2）：焦点换了一处（只有**离开**的那一处才算），或
        # `rules` 的 `scene.id` 换了（原检查点仍有效，测试可直接改快照触发）。
        # 同一回合两件事都发生，也只记一轮——焦点优先。
        focus_changed = (bool(location_id) and bool(previous_focus)
                         and location_id != previous_focus)
        scene_changed = bool(scene_now) and scene_now != scene_before

        try:
            self._begin_stream()
        except (BrokenPipeError, ConnectionResetError):
            return

        try:
            if result is not None:
                self._sse("result", result)
            if location_id:
                # 状态行已经写出、叙事之前（§5.2）：本回合的焦点是显式的，先让
                # `ensure_realization` 管实相。G5 的函数体只有 `return`；G6 才在
                # 函数体内读上游。实相失败**不**把整回合改成兜底句，所以这里吞掉
                # 异常——真正的时间盒与降级都归 G6 自己管。
                try:
                    prism_guide.ensure_realization(session, location_id)
                except Exception:      # noqa: BLE001
                    pass
            # 场外痕迹（G7 / §5.8）：离开的那一处（或 scene.id 变化时的当前焦点）
            # 在场 NPC 各记一条。失败**不**把整回合改成兜底句，所以这里吞掉异常。
            record_at = (previous_focus if focus_changed
                         else (current_focus if scene_changed else ""))
            if record_at:
                try:
                    prism_guide.record_traces(session, record_at)
                except Exception:      # noqa: BLE001
                    pass
            completion = None
            try:
                env = prism_guide.load_env()
                messages = prism_guide.build_narrative_messages(
                    session.rules, session.guide, text, result)
                if prism_guide.needs_tool(text, result is not None):
                    # 工具预通行（§5.2 / §5.6）：挂在**叙事消息数组**上，思考开、
                    # 非流式、最多 3 轮。只在状态行已经写出、且本回合还没有
                    # 机械结果时才开；工具里的 `ValueError` 收成固定短语当
                    # **工具错误字符串**回给模型，不会再写第二行 HTTP 状态。
                    prism_guide.run_tool_pass(session, messages, env=env)
                    # 工具里可能又 settle 了：叙事用的 L4 必须用**写回之后**的
                    # 快照重做（§5.4）。叙事请求重拼 L0–L4，所以既不带
                    # `reasoning_content`，也没有 `role: tool`（§5.5）。
                    messages = prism_guide.build_narrative_messages(
                        session.rules, session.guide, text, result)
                completion = prism_guide.call_narrative(messages, env=env)
            except Exception:   # noqa: BLE001 — 上游读取失败不改整回合为 _err
                completion = None
            reviewed = None
            if completion is not None:
                try:
                    reviewed = prism_guide.review_narration(result, completion)
                except Exception:   # noqa: BLE001
                    reviewed = None
            if reviewed is None:
                # 头已写出：失败只能用 fallback，拿不到第二行 HTTP 状态。
                self._sse("fallback", {"text": _guide_fallback_text(result)})
            else:
                # 秘密门（G7 / §5.8）：玩家将要看到的整段在进 L3 与切拍之前过一遍；
                # L3 与朗读都用**涂掉之后**的字（不第二次叫模型）。痕迹的揭开也在
                # 这一次锁内完成，`revealed` 随同一次 `save()` 落盘。
                with session.lock:
                    redacted = prism_guide.redact_narration(
                        reviewed, session.guide, player_text=text,
                        rules=session.rules)
                    beats = _guide_beats(redacted, session.guide)
                    session.guide["transcript"].append(
                        {"role": "assistant", "content": redacted})
                    session.guide["last_beats"] = beats
                    trace = (prism_guide.reveal_next_trace(session.guide,
                                                           record_at)
                             if record_at else "")
                    stats = session.guide.get("stats")
                    if isinstance(stats, dict):
                        stats["calls"] = int(stats.get("calls") or 0) + 1
                    session.save()      # L3 与 revealed 要跟着落盘
                payload = {"text": redacted, "beats": beats}
                if trace:
                    payload["trace"] = trace
                self._sse("narration", payload)
                self._sse("usage", completion.get("usage") or {})
            self._sse("done", {"status": "ok"})
            self._sse_close()
        except (BrokenPipeError, ConnectionResetError):
            pass                # 客户端中途断线（锁屏 / 切网）不该让服务端报错

    # ── 导引者朗读（G3，§5.9 / §5.10）───────────────────────
    def _guide_speak(self, body: dict):
        """`POST /api/guide/speak`：把**上一回合存下的**一拍读成 pcm16。

        体只有 `{"text"}`。状态行之前依次判：长度 → 会话 → 速率 → 语音是否
        可用 → 文本是否等于 `last_beats` 里某一拍的**全文**。任何一条不过
        都是固定 JSON（4xx），**不调用传输**，也不打开音频流。
        音色**取自那一拍**，忽略客户端多传的字段（§5.8）。

        头写出之后上游失败只有一条路：不发第二行 HTTP 状态，直接收尾——
        TTS 不重试（§5.9），文本已经通过叙事 SSE 给过浏览器。
        """
        text = str(body.get("text") or "")
        if len(text) > GUIDE_TEXT_LIMIT:
            return self._err("这句话太长", 400)
        sid = (self.headers.get("X-Session") or "").strip()
        session = get_session(sid) if sid else None
        if not session:
            return self._err("会话不存在或已过期", 400)
        if not _rate_ok(sid, "speak", GUIDE_SPEAK_LIMIT):
            return self._err("太频繁", 429)
        if prism_guide is None or not prism_guide.status().get("tts"):
            # 模块缺失 / 没配 BASE_URL 或密钥：没有第二套降级（§5.1）。
            return self._err("语音不可用", 400)

        with session.lock:
            beats = session.guide.get("last_beats")
            beats = list(beats) if isinstance(beats, list) else []
        matched = next((entry for entry in beats
                        if isinstance(entry, dict) and entry.get("text") == text),
                       None)
        if matched is None:
            return self._err("没有可朗读的句子", 400)
        voice = str(matched.get("voice") or prism_guide.VOICE_NARRATOR)

        try:
            self._begin_audio_stream()
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            pcm = prism_guide.call_tts(text, voice=voice)
            if pcm:
                self._chunk(pcm)
            self._sse_close()       # 零长度块收尾：空合成也是一条完整的流
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ── 多人房间（M10-S1，§4）────────────────────────────────
    def _bearer_token(self) -> str:
        """从 `Authorization: Bearer <token>` 取令牌明文；缺失 / 形态不对回空串。"""
        raw = (self.headers.get("Authorization") or "").strip()
        if not raw:
            return ""
        parts = raw.split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return ""
        return parts[1].strip()

    def _header_room(self) -> str:
        """当前请求指向的房间号：`X-Room` 头（§4.1）。"""
        return (self.headers.get("X-Room") or "").strip()

    def _room_auth(self, room_id: str):
        """§4.1 鉴权链（每个请求都走）：读房间 → 认成员 → 比对令牌 → 查状态。

        顺序：`room_id` 过白名单（也读得到房间）→ `member_id` 命中 `room.members`
        → `sha256(token)` 与 `member.token_hash` **常量时间比较** → 成员状态。
        任一不过一律 `401`（未认证）/ `403`（已认证但无权），**不回显**为何失败
        （不泄露成员是否存在）。通过时返回 `(session, member)`。
        """
        if not ROOM_ID_RE.fullmatch(room_id or ""):
            raise _RoomAuthError(401, "未认证", "token_invalid")
        session = get_session(room_id)
        if session is None:
            raise _RoomAuthError(401, "未认证", "token_invalid")
        member_id = (self.headers.get("X-Member") or "").strip()
        token = self._bearer_token()
        with session.lock:
            member = (session.room.get("members") or {}).get(member_id)
            # 三种失败回**同一个**响应：成员不存在 / 无令牌 / 令牌不匹配。
            if (not isinstance(member, dict) or not token
                    or not member.get("token_hash")
                    or not hmac.compare_digest(_hash_token(token),
                                               str(member["token_hash"]))):
                raise _RoomAuthError(401, "未认证", "token_invalid")
            if member.get("state") != "active":
                # 已离开 / 被踢：令牌仍能对上（hash 保留以便识别），但不给权限。
                raise _RoomAuthError(403, "已不在房间", "member_left")
            member["last_seen_at"] = time.time()
            return session, member

    def _room_create(self, body: dict):
        """`POST /api/room/create`：建房（= 建一份新存档，M-11）+ 首位成员当房主。

        体：`{room_id, display_name?, character_id?, world_key?, scenario_id?}`。
        名单里已有同名房间 → `409 room_exists`。返回**一次性**明文令牌（此后
        服务端只留 `sha256`，§4.1）。建房即在磁盘落盘，随后的读接口即可用。
        """
        room_id = str(body.get("room_id") or "").strip()
        if not ROOM_ID_RE.fullmatch(room_id):
            return self._err("房间号不合法", 400, "bad_room_id")
        display_name = _text_or(body.get("display_name"), 40).strip()
        # 建房必须**原子**：同一房间号不能既命中缓存又落到磁盘上两份。
        with _sessions_lock:
            if room_id in _sessions or (SAVE_DIR / f"{room_id}.json").is_file():
                return self._err("房间已存在", 409, "room_exists")
            # 新会话此刻还不对外可见（尚未进 _sessions），构造成员记录不与他人竞争。
            session = Session(room_id)
            now = time.time()
            session.room["created_at"] = now
            session.room["world_key"] = _text_or(body.get("world_key"), 64).strip()
            session.room["scenario_id"] = _text_or(body.get("scenario_id"), 64).strip()
            member_id = _new_member_id()
            token = _new_room_token()
            member = _empty_member(member_id)
            member.update({
                "token_hash": _hash_token(token),
                "display_name": display_name or "房主",
                "role": "host",
                "character_id": _text_or(body.get("character_id"), 64).strip(),
                "state": "active",
                "created_at": now,
                "last_seen_at": now,
            })
            session.room["members"][member_id] = member
            session.room["host_member_id"] = member_id
            session.room["seq"] = _int_or(session.room.get("seq"), 0) + 1
            session.save()
            _sessions[room_id] = session
        return self._json({
            "status": "ok",
            "room_id": room_id,
            "member_id": member_id,
            "token": token,             # 唯一一次回明文；服务端只留 hash
            "role": "host",
            "capacity": ROOM_MAX_MEMBERS,
        })

    def _room_join(self, body: dict):
        """`POST /api/room/join`：加入一个已存在的房间。

        `room_id` 取 `X-Room` 头或请求体；体：`{display_name?, character_id?}`。
        座位 = `host` / `player` 且 `active`（observer 不计入，§4.4）；
        满员 → `409 room_full`（T7），房间不存在 → `404`。旧单人档（无房主）的
        首位加入者成为房主（M-16）。
        """
        room_id = self._header_room() or str(body.get("room_id") or "").strip()
        if not ROOM_ID_RE.fullmatch(room_id):
            return self._err("房间号不合法", 400, "bad_room_id")
        session = get_session(room_id)
        if session is None:
            return self._err("房间不存在", 404, "room_not_found")
        display_name = _text_or(body.get("display_name"), 40).strip()
        with session.lock:
            if len(_room_seats(session.room)) >= int(
                    session.room.get("capacity") or ROOM_MAX_MEMBERS):
                return self._err("房间已满", 409, "room_full")
            now = time.time()
            member_id = _new_member_id()
            token = _new_room_token()
            # 尚无房主（老档迁移态）→ 首位加入者补上房主位（M-16）。
            role = "host" if not session.room.get("host_member_id") else "player"
            member = _empty_member(member_id)
            member.update({
                "token_hash": _hash_token(token),
                "display_name": display_name or "玩家",
                "role": role,
                "character_id": _text_or(body.get("character_id"), 64).strip(),
                "state": "active",
                "created_at": now,
                "last_seen_at": now,
            })
            session.room["members"][member_id] = member
            if role == "host":
                session.room["host_member_id"] = member_id
            session.room["seq"] = _int_or(session.room.get("seq"), 0) + 1
            session.save()
        return self._json({
            "status": "ok",
            "room_id": room_id,
            "member_id": member_id,
            "token": token,
            "role": role,
            "capacity": ROOM_MAX_MEMBERS,
        })

    def _room_leave(self, body: dict):
        """`POST /api/room/leave`：本人离开房间（§4.2「进/离房间」）。

        走完整鉴权链（`X-Room` / `X-Member` / Bearer）。成员 `state=left`，令牌
        随之失效（后续请求 `403 member_left`）。房主离开时清掉房主位，
        房间回到「无主」（可由下一位加入者接管，M-16）；房主**移交**另开单。
        """
        room_id = self._header_room()
        try:
            session, member = self._room_auth(room_id)
        except _RoomAuthError as exc:
            return self._err(exc.msg, exc.code, exc.reason)
        with session.lock:
            member["state"] = "left"
            member["last_seen_at"] = time.time()
            if session.room.get("host_member_id") == member["member_id"]:
                session.room["host_member_id"] = ""
            session.room["seq"] = _int_or(session.room.get("seq"), 0) + 1
            session.save()
        return self._json({"status": "ok", "room_id": room_id,
                           "member_id": member["member_id"]})

    def _room_kick(self, body: dict):
        """`POST /api/room/kick`：房主踢出成员（§4.2 角色矩阵）。

        体：`{member_id}`。非房主 → `403 not_authorized`（T8，房间状态不变）；
        被踢者 `state=left`、其请求此后得 `403 member_left`（§4.4）。
        """
        room_id = self._header_room()
        try:
            session, member = self._room_auth(room_id)
        except _RoomAuthError as exc:
            return self._err(exc.msg, exc.code, exc.reason)
        if (member.get("role") != "host"
                or session.room.get("host_member_id") != member["member_id"]):
            return self._err("只有房主能踢人", 403, "not_authorized")
        target_id = str(body.get("member_id") or "").strip()
        with session.lock:
            target = (session.room.get("members") or {}).get(target_id)
            if not isinstance(target, dict):
                return self._err("成员不存在", 404, "member_not_found")
            if target_id == member["member_id"]:
                return self._err("不能踢出自己", 400, "bad_target")
            target["state"] = "left"
            target["last_seen_at"] = time.time()
            session.room["seq"] = _int_or(session.room.get("seq"), 0) + 1
            session.save()
        return self._json({"status": "ok", "room_id": room_id,
                           "member_id": target_id})


def lan_ip() -> str:
    """猜测本机在局域网中的 IP；失败退回 127.0.0.1（只影响启动提示文案）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))      # UDP 不发包，只借路由表选出网卡
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def main() -> None:
    _ensure_save_dir()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("=" * 58)
    print("  NotDND 网页后端骨架已启动")
    print("=" * 58)
    print(f"  本机访问：http://127.0.0.1:{PORT}/")
    print(f"  局域网访问：http://{lan_ip()}:{PORT}/   （需同一局域网）")
    print(f"  存档目录：{SAVE_DIR}")
    print("\n  Ctrl+C 停止")
    print("=" * 58)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
        srv.server_close()


if __name__ == "__main__":
    main()
