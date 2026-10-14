#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notdnd_web.py —— NotDND 网页后端骨架（M3 前置）

只提供**工程骨架**，不含规则、数值与世界内容：
  · HTTP 响应 helper 与单类路径链路由（GET 只读 / POST 写入，无框架）
  · 静态资源服务（目录穿越防护 + MIME）
  · 监听与端口约定（NOTDND_HOST / NOTDND_PORT，回退通用 PORT）
  · 存档骨架：落盘 / 加载**委托 `state`**（STATE-DESIGN §11）——`Session` 仍是网页层
    的**内存门面**（工程字段 + 不透明块），落盘走 `state` 的检查点（原子写 + checksum），
    老档（旧单文件 `<sid>.json`）在第一次保存时按 §10 惰性迁移成新档文件族并留 `.bak`；
    列表 / 改名 / 删除边界两种布局都认
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
  · 回合落会话日志（M4-B2，UI-DESIGN §1.3.5 / §7 缺口 B2）：`/api/guide/turn`
    结束后把玩家的 `act`、导引者 `narrate`（含离线 / 上游失败的兜底句）与
    揭开的 `trace` 写进 `Session.log`（`seq` 在锁内递增），`GET /api/log?since=`
    据此增量取回；叙事记的是秘密门**涂掉之后**的字，被删内容不因日志重新可见
  · 多人房间骨架（M10-S1，MULTIPLAYER-DESIGN.md §3.2 / §4）：顶层 `room` 块
    随存档落盘（旧单人档惰性迁移成最小房间，M-16）；`POST /api/room/create` /
    `join` / `leave` / `kick` 走 `Authorization: Bearer` + `X-Room` / `X-Member`
    鉴权链（§4.1）；房间级角色矩阵（§4.2）与 6 人上限（容量满 → `409 room_full`）；
    令牌只存 `sha256`，明文仅在创建 / 加入时回一次，永不落盘 / 写日志
  · 房间事件流与断线重连（M10-S2，MULTIPLAYER-DESIGN.md §5.4 / §5.5）：
    `GET /api/room/stream?since=` 按 `seq > since` 补发 + 活流推送（复用导引者
    的 `_begin_stream` / `_chunk` 口径；帧 = `id: <seq>` + `event: <type>` +
    一行 `data:`）；心跳每 15s 一行 `: keepalive`，socket 写超时 90s = 判死关流；
    每流一个**有界**出站队列，满则断开该流由客户端重连补发重建（不丢事件）；
    `Accept: application/json` 时同路径回一次性读取（降级短轮询，过滤与 `since`
    语义与流式一致）。补发与广播一律按事件的 `visibility` 过滤（§6.1 / §6.4）
  · CHRON 存档块（M9-C5）：`chron` 是又一块**不透明块**（CHRON-DESIGN §8.1）——
    本文件只提供它的默认值工厂（老档迁移时补空块）；块的加载 / 重放 / 重建 `due_heap` /
    幂等表全归 `chron.py`（C5），本文件不解析这块
  · CHRON 排程闭环（M9-C6，CHRON-DESIGN §4.3 / ACTION-DESIGN §8）：`SCHEDULE_HOOK`
    的默认实现是本文件的 **L1 适配器**（`_ChronScheduleHook`）——长行动
    （`schedule_hint.can_defer` 或非空 `interrupt_conditions`）不再同步结算，
    而是入 CHRON 队列（`POST /api/action/commit` 立即回 `scheduled` + `task_id`）；
    世界时间由 `POST /api/chron/advance`（`advance_to`）推进，**到期才结算**并出结果 /
    叙事；`GET /api/chron/next_barrier` 看下一个屏障，`POST /api/chron/cancel` 取消
    已排程行动。即时行动（`can_defer = false`）保持 M8 的同步路径不变
  · 状态层只读视图（M7-4，STATE-DESIGN §16）：`GET /api/state` 回当前战役摘要
    （`campaign_id` / `seq` / `world_time` / 各块存在性，只回存在性不回块内容）；
    `GET /api/events?since=` 按事件 `seq` 增量拉取（只回 `seq > since`，跨归档段
    由状态层拼接校验）。两条都**只读**：不落盘、不触发检查点、不为未迁移老档开
    句柄（老档在同一形状下按内存门面派生，`seq` 为 0）；非法 `since` → 400 固定短语。

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
import shutil
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import atlas as atlas_kernel
import atlas_compile
import chron
import prism_core
import state

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

# ── 行动管线（M8-A3，ACTION-DESIGN §8 / §9）──────────────────────────────
#
# 四条写接口（§9.1）：`GET /api/action/catalog` 与 `POST /api/action/`
# propose / confirm / commit。管线六段（§3.3）：解析 → 检索 → 验证 → 结算 →
# 提交 → 叙事；**第 3–5 段一行模型都不调**（§7.1）——`mode: "action"` 的
# 显式行动因此在没有 AI 配置时也能完整走完一轮；第 6 段才是可选的叙事段。
#
# 提交走状态层 `state.Handle.commit`（§10.1 目标态：M7 已把 `Session` 接线到
# `state`，无需再走 §10.2 的过渡出口），因此结果里 `event_ids` / `commit_ref`
# 是**真实**的提交引用，`seq_to` 即该行动的叙事基线版本（§7.5）。

# 排程钩子的世界时间占位（§8.3）：M8 没有世界时钟，世界分钟取纪元 0；
# M9 由 CHRON 提供真实值（本模块不 import `chron`）。
ACTION_EPOCH_MINUTE = 0

# 幂等键（§9.4）：`Idempotency-Key` 请求头，`^[A-Za-z0-9_-]{8,64}$`。
ACTION_IDEM_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
# 提案 ID（§4.2，与 A1 `prism_core` 同一形状）与确认令牌（§6.4 `cf_<32hex>`）。
ACTION_PROPOSAL_ID_RE = re.compile(r"^p_[0-9a-f]{32}$")
ACTION_CONFIRM_TOKEN_RE = re.compile(r"^cf_[0-9a-f]{32}$")
# §9.5 末段：每会话每 60 秒 12 次 commit、30 次 propose（复用 `_rate_ok` 的
# 60 秒窗口，与 turn / speak 同一把锁）。
ACTION_COMMIT_LIMIT = 12
ACTION_PROPOSE_LIMIT = 30
# 澄清与确认令牌都是**进程内短期记忆**（提案本身无状态透明，§9.2 / §10.3 不落盘）；
# 过期即 404 `没有这个提案` / 409 `需要确认`。
ACTION_CLARIFY_TTL_S = 300.0
ACTION_CONFIRM_TTL_S = 300.0
# 幂等记录的进程内 TTL（§9.4「键不过期或设长 TTL」）：状态层另有一份**持久**
# 幂等索引（§7.3 / §9.4），进程内这份只是「任何结算之前短路」的那一层。
ACTION_IDEM_TTL_S = 7 * 86400.0

# §9.5 错误码总表：固定短语 + **仅状态行写出之前**可用的 HTTP 码。
# 状态行写出之后一律走 SSE 事件（`fallback` / `done`），不再发第二行状态。
ACTION_NO_SESSION = "会话不存在或已过期"       # 400
ACTION_BAD_IDEM_KEY = "幂等键不合法"           # 400
ACTION_NO_ACTION = "没有这个行动"              # 404
ACTION_NO_PROPOSAL = "没有这个提案"            # 404
ACTION_NO_TARGET = "目标不存在"                # 404
ACTION_FORBIDDEN = "做不了这件事"              # 403
ACTION_NOT_NOW = "现在做不了"                  # 409
ACTION_COMBAT = "战斗还没结束"                 # 409
ACTION_STALE_SEQ = "状态已变，请重试"           # 409
ACTION_ALREADY_COMMITTED = "该提案已提交"       # 409
ACTION_NEED_CONFIRM = "需要确认"               # 409
ACTION_BAD_VERSION = "版本不支持"              # 409
ACTION_TOO_FREQUENT = "太频繁"                 # 429

# `propose` 回执里的 `type`（§9.2）与 `commit` 的 SSE 事件名（§9.3）。
ACTION_TYPE_PROPOSAL = "proposal"
ACTION_TYPE_CLARIFY = "clarify"
ACTION_TYPE_OFFLINE = "offline"
ACTION_TYPE_CANCELLED = "cancelled"

# §9.5 的固定短语 → 仅状态行之前可用的 HTTP 码（A2 `propose_nl` 的拒绝短语
# 与 §9.5 同文，网页层只做翻译，不改语义）。
ACTION_REJECT_CODES = {
    ACTION_NO_ACTION: 404,
    ACTION_NO_TARGET: 404,
    ACTION_FORBIDDEN: 403,
}

# ── CHRON 排程接线（M9-C6，CHRON-DESIGN §4.3 / ACTION-DESIGN §8）──────────
#
# L1（`ScheduleHook`，ACTION §8.2）与 L2（`chron.py` 的 `schedule` / `advance_to` /
# `next_barrier` / 取消）是**两层**：L1 的默认适配器在 `_ChronScheduleHook`，
# L2 的驱动入口在 `_chron_*` 函数与 `/api/chron/*` 路由。`can_defer = false`
# 的即时行动**不进**队列（同步结算逐字节不变，§4.3 关键约定）。
#
# `time_cost` 的 `unit` → 世界分钟换算（§8.1：权威刻度 = 整数分钟）：
# CHRON 只认整数分钟，换算在适配器里做；`watch` 取 `NOMINAL_WATCH_MINUTES`、
# `day` 取 `MINUTES_PER_DAY`（CHRON-DESIGN §2.4），规则时间（`round` / `turn`）
# 按最小排程步长（`MINOR_ACTION_MINUTES`）保守折算——规则包将来要更细的
# 规则时间换算，另开单（CHRON-DESIGN §11 C6 末条），不在这里发明刻度。
CHRON_TASK_PREFIX = "act_"
CHRON_UNIT_MINUTES = {
    "minute": 1,
    "hour": chron.MINUTES_PER_HOUR,
    "watch": chron.NOMINAL_WATCH_MINUTES,
    "day": chron.MINUTES_PER_DAY,
    "turn": chron.MINOR_ACTION_MINUTES,
    "round": chron.MINOR_ACTION_MINUTES,
}
# `/api/chron/*` 的速率与行动管线同口径（每会话每 60 秒），固定短语只在
# **状态行写出之前**用（与 §9.5 同款边界）。
CHRON_ADVANCE_LIMIT = 12
CHRON_CANCEL_LIMIT = 12
CHRON_BAD_TARGET = "推进入参不合法"              # 400
CHRON_TIME_BACKWARD = "世界时间只增不减"          # 400
CHRON_NO_TASK = "没有这个任务"                    # 400

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
# §6.1 频道表（S3 落完整分流）：`world / party / org / tell / npc / ooc / host`。
# 「私聊 / 私谈」在事件类型里以 `chat.tell` / `chat.npc` 表达（§5.4 的 `event:` 名）。
ROOM_CHANNELS = ("world", "party", "org", "tell", "npc", "ooc", "host")
# §6.1 频道 → **冻结受众**的 `visibility.scope`（STATE §5.1：public / team / private / gm）。
# `world` / `ooc` 对房内全部成员（public）；`party` / `org` 为队 / 组织范围（team）；
# `tell` / `npc` 只给点名成员（private）；`host` 只给房主（gm，管理消息）。
ROOM_CHANNEL_SCOPES = {
    "world": "public",
    "ooc": "public",
    "party": "team",
    "org": "team",
    "tell": "private",
    "npc": "private",
    "host": "gm",
}
# 聊天事件（§6.1）：`type = chat.<频道>`，正文是玩家原话；上下限各一。
ROOM_CHAT_EVENT_PREFIX = "chat."
ROOM_CHAT_TEXT_MAX = 2000
ROOM_CHAT_RULES_VERSION = "prism@room-chat"
# §5.5 的推送流参数（模块常量：测试可覆写成小值，真跑「心跳 / 判死 / 队列满」）。
ROOM_STREAM_HEARTBEAT_SECONDS = 15.0    # 每 15s 一行注释 `: keepalive`
ROOM_STREAM_DEAD_SECONDS = 90.0         # 90s 无任何帧（含心跳）→ 服务端关流
ROOM_STREAM_QUEUE_MAX = 256             # 每流出站队列（**帧数**）上限；满则断流


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


# ── 房间事件流（M10-S2，MULTIPLAYER-DESIGN §5.4 / §5.5）────────────────────
#
# 推送对外的**合同**只有两件：`seq`（战役全序，来自状态层事件日志）与
# **按 `visibility` 过滤后的事件封套**；SSE 只是编码（§5.4）。「流」= 一条
# 连接：有界出站队列 + 判活。流的引用 / 队列是**内存态**（§3.3），不进 `room`
# 块——那块只放可持久化的房间事实（§3.2，且要能 JSON 落盘）。

# §5.4 的 `visibility.scope` → 推送频道（**S2 兼容**：`chat.*` 之外的事件按 scope 兜底）。
ROOM_SCOPE_CHANNELS = {"public": "world", "team": "party", "private": "tell"}


def _room_event_channel(event) -> str:
    """事件封套 → 推送频道名（§5.4 / §6.1）。

    S3 的频道由事件 `type` 前缀显式标注：`chat.<频道>` → 该频道名（§5.4 的
    `event:` 即频道名）。没有频道标注的老事件（S2、行动 / CHRON 提交）按
    `visibility.scope` 兜底：`public` → `world`、`team` → `party`、`private` → `tell`。
    未知 scope / 未知频道一律回**空串 = 不下发**（失败关闭，§6.2）。
    """
    if isinstance(event, dict):
        ev_type = event.get("type")
        if isinstance(ev_type, str) and ev_type.startswith(ROOM_CHAT_EVENT_PREFIX):
            channel = ev_type[len(ROOM_CHAT_EVENT_PREFIX):]
            return channel if channel in ROOM_CHANNELS else ""
    visibility = event.get("visibility") if isinstance(event, dict) else None
    if not isinstance(visibility, dict):
        return ""
    return ROOM_SCOPE_CHANNELS.get(visibility.get("scope"), "")


def _room_event_visible(event, member_id: str, character_id: str) -> bool:
    """这条事件该不该推给该**成员**（§6.1 / §6.4 每成员视角过滤）。

    `world` / `ooc` 对房内全部成员可见；`party` / `org` / `tell` / `npc` / `host`
    只对 `visibility.subjects` 点名的成员可见——`subjects` 在**提交时冻结**，
    重连沿用同一份名单（§6.4）。成员 id 与角色 id 都可作点名（S2 事件点名角色、
    S3 聊天点名成员），任一命中即可见。`gm`（导引者内部）没有点名时对谁都不下发
    （失败关闭）；`host` 管理消息即 `gm` + 房主点名（只给房主）。
    """
    channel = _room_event_channel(event)
    if not channel:
        return False
    if channel in ("world", "ooc"):
        return True
    visibility = event.get("visibility") if isinstance(event, dict) else None
    subjects = visibility.get("subjects") if isinstance(visibility, dict) else None
    if not isinstance(subjects, list):
        return False
    mine = {str(x) for x in (member_id, character_id) if x}
    return bool(mine & {str(s) for s in subjects})


def _room_frame(event) -> dict:
    """§5.4 的 `data:` 载荷：`{seq, type, event, audience}`。

    `type` / `audience` 都是频道名（§5.4 的示例即同值），`event` 是事件封套
    **原样**透出——换传输（WS / 轮询）时只换编码，不改这里的语义。
    成员视角的秘密门（§6.2）在 `_room_member_frame` 里过，不在这一层。
    """
    channel = _room_event_channel(event)
    return {"seq": _int_or(event.get("seq"), 0), "type": channel,
            "event": event, "audience": channel}


def _room_active_members(room: dict) -> dict:
    """房内 `state=active` 的成员：`{member_id: member}`（§4.4：离开 / 被踢不计）。"""
    members = room.get("members") if isinstance(room, dict) else None
    if not isinstance(members, dict):
        return {}
    return {str(mid): m for mid, m in members.items()
            if isinstance(m, dict) and m.get("state") == "active"}


def _room_member_knows(session: Session, member: dict) -> list:
    """本成员角色的 `knows`（§6.2「本成员的角色 knows 集」）。

    从规则快照的 `party` 里按 `character_id` 找到该成员的角色卡，取 `knows`；
    找不到 / 没填 → 空集（不是「全知」）。秘密门把它与其他可见性输入并起来。
    """
    cid = str((member or {}).get("character_id") or "")
    if not cid:
        return []
    rules = session.rules if isinstance(session.rules, dict) else {}
    for unit in rules.get("party") or []:
        if isinstance(unit, dict) and str(unit.get("id") or "") == cid:
            knows = unit.get("knows")
            return [str(x) for x in knows] if isinstance(knows, list) else []
    return []


def _room_member_frame(session: Session, member: dict, event) -> dict:
    """按**该成员**的视角出一帧（§6.2 每成员各过一次秘密门）。

    聊天正文可能含 NPC 秘密：对该成员重跑一遍秘密门，未放行的片段涂成「……」
    后才进帧——服务端只推成员自己那份（§6.3）；非聊天事件原样透出。
    """
    channel = _room_event_channel(event)
    if (prism_guide is None or not channel or not isinstance(event, dict)
            or not str(event.get("type") or "").startswith(ROOM_CHAT_EVENT_PREFIX)):
        return _room_frame(event)
    text = event.get("cause")
    if not isinstance(text, str) or not text:
        return _room_frame(event)
    guide = session.guide if isinstance(session.guide, dict) else {}
    redacted = prism_guide.redact_for_member(
        text, guide, knows=_room_member_knows(session, member))
    if redacted == text:
        return _room_frame(event)
    safe = dict(event)
    safe["cause"] = redacted
    return _room_frame(safe)


def _room_frames(session: Session, member: dict, since: int) -> list:
    """`seq > since` 里该成员**有权看**的帧（§6.4：按序号读事件 + 同一判据过滤）。

    读取 / 链校验 / 跨归档拼接全在状态层（`Handle.events_since`，M7-4）；本层
    只做受众过滤——重连**绝不**因为「是补发」而多给（§6.4）；再按该成员的视角
    过一遍秘密门（§6.2）。状态层拒载时异常照常上抛（调用方翻成既有的固定 500）。
    """
    member_id = str((member or {}).get("member_id") or "")
    character_id = str((member or {}).get("character_id") or "")
    with session.lock:
        handle = session._handle
        if handle is None:
            return []                       # 还没有状态层日志（未迁移老档）
        events = handle.events_since(since)
    return [_room_member_frame(session, member, event) for event in events
            if _room_event_visible(event, member_id, character_id)]


def _room_chat_audience(room: dict, member: dict, channel: str,
                        target: str, members) -> tuple:
    """按频道算出**提交时冻结**的 `(scope, subjects)`（§6.1；subjects = 成员 id）。

    - `world` / `ooc`：房内全部成员（`public`，不点名）；
    - `party`：同队成员——S3 无独立队伍模型，取房内**在座**成员（`host` / `player`，
      与 `world` 的区别在 observer：observer 看 `world` 不看 `party`，§4.2）；
    - `org`：该组织成员**且显式授权可见**——由请求里 `members` 点名（组织共享 ≠
      全部秘密共享，GDD §7.2），并总是带发送者；
    - `tell`：仅收发双方（`target` 必须是房内在座成员）；
    - `npc`：与 NPC 私谈的参与者 + 合法目击 / 监听者——发送者 + 请求 `members` 里的目击者；
    - `host`：房主（管理消息）。
    非法频道 / 缺目标 → 回 `("", [])`（调用方翻成 400）。
    """
    active = _room_active_members(room)
    sender_id = str((member or {}).get("member_id") or "")
    if channel in ("world", "ooc"):
        return "public", []
    if channel == "party":
        seated = [mid for mid, m in active.items()
                  if m.get("role") in ("host", "player")]
        return "team", (seated or [sender_id])
    if channel in ("org", "npc"):
        ids = [sender_id] if sender_id else []
        for mid in members or []:
            mid = str(mid)
            if mid in active and mid not in ids:
                ids.append(mid)
        return ("team" if channel == "org" else "private"), ids
    if channel == "tell":
        ids = [sender_id] if sender_id else []
        if str(target) in active and str(target) != sender_id:
            ids.append(str(target))
        return "private", ids
    if channel == "host":
        hosts = [mid for mid, m in active.items() if m.get("role") == "host"]
        return "gm", (hosts or [sender_id])
    return "", []


class _RoomStream:
    """一条推送流的运行时态：**有界**出站队列 + 判活（§5.5）。

    - `queue` 只装**活流**要推的帧；补发（`seq > since`）由读线程直接读日志
      写出，不进队列——补发是「追平」，队列管的是「别让慢客户端把内存撑爆」；
    - 队列满 → `overflowed`：写线程收尾断流（结束分块），客户端重连用
      `since=last_seq` 补发重建（T13：不丢事件）；
    - `last_sent` = **已写进 socket** 的最大 `seq`，是去重的唯一依据：补发与
      活流队列可能重叠（登记与补发读之间提交的事件），同一条事件绝不写两遍；
    - §5.5 的「每成员一个有界队列」按**每流**落地：一个慢标签页只断自己那条
      流，不会把同一成员另一条流的帧冲掉（多标签页 = 多条流）。
    """

    def __init__(self, room_id: str, member_id: str, character_id: str,
                 since: int):
        self.room_id = room_id
        self.member_id = member_id
        self.character_id = character_id
        self.since = since
        self.last_sent = since              # 未写到任何一帧前，游标就是 since
        self.queue: list[dict] = []
        self.cond = threading.Condition()
        self.overflowed = False
        self.closed = False

    def enqueue(self, frame: dict) -> None:
        """投递一帧；队列满 → 标 `overflowed`（由写线程断流）。"""
        with self.cond:
            if self.closed or self.overflowed:
                return
            if len(self.queue) >= ROOM_STREAM_QUEUE_MAX:
                self.overflowed = True
                self.cond.notify_all()
                return
            self.queue.append(frame)
            self.cond.notify_all()

    def drain(self, timeout: float) -> tuple[list, bool]:
        """取一批待写帧：`(batch, stop)`——`stop=True` = 该收尾断流。

        空批 + `stop=False` = 等超时（写线程据此发心跳）。
        """
        with self.cond:
            if self.overflowed or self.closed:
                return [], True
            if not self.queue:
                self.cond.wait(timeout)
            if self.overflowed:
                return [], True
            batch, self.queue = self.queue, []
            return batch, bool(self.closed)

    def close(self) -> None:
        """收尾：清队列、唤醒写线程（连接结束后不留残余引用）。"""
        with self.cond:
            self.closed = True
            self.queue = []
            self.cond.notify_all()


_room_streams: dict[str, dict[str, list]] = {}       # room_id → member_id → [流]
_room_streams_lock = threading.Lock()


def _room_stream_register(stream: _RoomStream) -> None:
    """登记一条流：**先登记再补发**，登记与补发读之间的事件因此进队列不漏。"""
    with _room_streams_lock:
        bucket = _room_streams.setdefault(stream.room_id, {})
        bucket.setdefault(stream.member_id, []).append(stream)


def _room_stream_unregister(stream: _RoomStream) -> None:
    """摘除一条流；空桶一并清掉（房间不再有流时不留下空条目）。"""
    with _room_streams_lock:
        bucket = _room_streams.get(stream.room_id)
        if not bucket:
            return
        streams = bucket.get(stream.member_id)
        if streams and stream in streams:
            streams.remove(stream)
        if not streams:
            bucket.pop(stream.member_id, None)
        if not bucket:
            _room_streams.pop(stream.room_id, None)


def _room_stream_list(room_id: str) -> list:
    """本房间当前所有活流的快照（调用方拿拷贝，不在锁里做推送）。"""
    with _room_streams_lock:
        bucket = _room_streams.get(room_id) or {}
        return [stream for streams in bucket.values() for stream in streams]


def _room_publish(session: Session, events) -> None:
    """提交成功后按受众扇出（§5.5）：一条事件只投给有权看的流。

    受众过滤（§6.1）与**每成员视角**的秘密门（§6.2）都按该流的成员算——所以
    这里要拿到 `session`（成员记录 / 规则快照）。读 `stream.last_sent` 只是
    **少投一次**的优化（无锁读，可能与写线程的更新交错）；真正管「不重」的是
    写线程写帧时的去重（`_write_room_frame`）。
    """
    rid = str(getattr(session, "sid", "") or "")
    targets = _room_stream_list(rid)
    if not targets or not events:
        return
    members = _room_active_members(session.room)
    for stream in targets:
        member = members.get(stream.member_id) or {"member_id": stream.member_id,
                                                   "character_id": stream.character_id}
        for event in events:
            seq = _int_or(event.get("seq"), 0)
            if seq <= stream.last_sent:
                continue        # 已经写进 socket（补发 / 上一批）：不重发
            if not _room_event_visible(event, stream.member_id, stream.character_id):
                continue        # 无权看：**绝不下发**（§6.1）
            stream.enqueue(_room_member_frame(session, member, event))


def _room_publish_after_commit(handle, commit, room_id: str = "") -> None:
    """一次提交之后的扇出（§5.5：提交**成功**之后）。

    `room_id` 缺省取句柄的 `campaign_id`：M-11（`room_id == sid == campaign_id`）
    是 S1 已落的不变式，排程 / 推进那几处因此不必把 `session` 一路传进来；
    扇出要按成员过滤（§6.1 / §6.2），故由 `room_id` 取回会话（取不到就只当没有流）。
    推送只是**交付**：读日志 / 投递失败都不影响已提交的事实（客户端靠
    `since` 补发自愈），所以这里恒定不抛。
    """
    if handle is None:
        return
    seq_from = _int_or((commit or {}).get("seq_from"), 0)
    if seq_from <= 0:
        return              # conflict / rejected / 幂等重放未落事件：没有新事实
    room_id = room_id or str(getattr(handle, "campaign_id", "") or "")
    if not _room_stream_list(room_id):
        return
    session = get_session(room_id)
    if session is None:
        return
    try:
        events = handle.events_since(seq_from - 1)
    except state.StateError:
        return              # 读日志失败只影响这一轮推送：不打断提交路径
    _room_publish(session, events)


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


def _turn_act_text(session: "Session", text: object, action_id: object) -> str:
    """这一回合玩家「做了什么」的日志正文（M4-B2 / UI-DESIGN §1.3.5 的 `act`）。

    有自由文本就用它的 `strip()`；只有 `action_id`（点行动按钮、不带散文）时，
    用该行动的 `label`（从 `rules.scene.actions` 里按 id 找），拿不到 label 就
    退回 `action_id` 本身——日志里**不出现空正文**。两者都没有时返回空串
    （调用方据此不记 `act`）。
    """
    stripped = str(text or "").strip()
    if stripped:
        return stripped
    action_id = str(action_id or "")
    if not action_id:
        return ""
    scene = session.rules.get("scene") if isinstance(session.rules, dict) else None
    actions = (scene or {}).get("actions") if isinstance(scene, dict) else None
    for action in actions or []:
        if isinstance(action, dict) and str(action.get("id") or "") == action_id:
            label = str(action.get("label") or "")
            if label:
                return label
            break
    return action_id


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
    """空规则会话的落盘快照；零参调用即 `_LEGACY_DEFAULTS` 的默认值工厂。

    规则态的形状归 prism_core 定义，本文件只调用它的快照 / 还原接口，
    不另写一份字段名——两边各维护一套必然漂移。

    TODO(M3)：起始场景 / 起始队伍随剧本数据落地后，在这里按世界模组生成，
    老存档仍走「只补不覆盖」的惰性迁移拿到它。
    """
    return prism_core.RuleSession(str(sid or "")).snapshot()


def _empty_chron_block() -> dict:
    """空 CHRON 存档块（`chron`，CHRON-DESIGN §8.1）；零参调用即默认值工厂。

    块的形状归 `chron` 定义（`state.py` 只把它当**不透明块**原样存取，STATE §6.1 /
    S8），本文件只调用它的工厂，不另写一份字段名——与 `_empty_rules_snapshot`
    同款口径。世界时钟 / 行动卡 / 幂等表都由 CHRON 自己填（C5 / C6）。
    """
    return chron.new_block()


# 老档字段默认值表：**迁移层**（`_legacy_parts`）按此补缺——「只补不覆盖」。
# 新档（`campaigns/<sid>/snapshot.json`）走 `state` 的加载路径（缺块即创世块），
# 不再经过这里：这条表只服务「旧单文件 `<sid>.json` → 新档」的一次性映射。
# 值 = 字面量（不可变）或**零参工厂**（可变）：工厂每次现造一个**新**对象，
# 绝不把同一个 list / dict 借给多个存档——否则一个存档的改动会顺着默认值
# 漏进另一个老档，且这种串档在单存档测试里看不出来。
_LEGACY_DEFAULTS: dict[str, object] = {
    "created": 0.0,                     # 建局时间戳；老档缺失按 0（未知）
    "log": list,                        # 叙事流条目（工厂：现造空列表）
    "seq": 0,                           # 日志序号游标（增量拉取用）
    "save_name": "",                    # 玩家自定义展示名
    "rules": _empty_rules_snapshot,     # PRISM 规则会话快照（工厂：现造空会话）
    "atlas": None,                      # ATLAS 存档块（§3.4）；老档缺失按 None，
                                        # 第一次用到地图时按当前世界懒编译
    "guide": _empty_guide,              # 导引者状态块（工厂：现造，salt 新掷）
    "room": _empty_room,                # 多人房间块（§3.2）；工厂：现造最小房间，
                                        # 老档缺失即迁移成「单人房」（M-16）
    "chron": _empty_chron_block,        # CHRON 存档块（CHRON-DESIGN §8.1）；工厂：现造
                                        # 空块（world_minute=0、无行动、无幂等键）。
                                        # M7 之前的老档都没有这块，本该由 CHRON（C5）
                                        # 惰性补默认；这里顺手在迁移时补上，新档一起步
                                        # 就有形状正确的不透明块（STATE §6.1）。
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


# ── 战役目录与绑定简报（M4，UI-DESIGN §2.1 / Issue #222）────────────────────
#
# 「先选世界，再选该世界下的剧本」的只读数据面：世界 = `data/worlds/**` 的
# 世界模组（key / name），剧本 = `data/scenarios/**` 里 `meta.world` 与所选
# 世界 key **逐字相等**的那批——与 `prism_guide.bind` 的「世界对不上」同一
# 口径（列表与绑定都按世界过滤）。只投影白名单字段，剧本正文（含秘密）永远
# 不出现在这里。

_SCENARIO_META_CACHE: dict[str, dict | None] = {}


def _scenario_meta(scenario_id: str) -> dict | None:
    """读 data/scenarios/<id>.json 的 `meta`（惰性 + 缓存；读不了回 None）。

    id 走与 sid 相同的白名单（它要拼进文件路径，杜绝 ../ 探测）。本函数只
    服务**只读展示**；绑定语义仍归 `prism_guide.bind`（它自己验文件与
    `meta.world`）。
    """
    if not SID_RE.fullmatch(scenario_id or ""):
        return None
    with _DATA_LOCK:
        if scenario_id in _SCENARIO_META_CACHE:
            return _SCENARIO_META_CACHE[scenario_id]
        path = HERE / "data" / "scenarios" / f"{scenario_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        meta = data.get("meta") if isinstance(data, dict) else None
        meta = meta if isinstance(meta, dict) else None
        _SCENARIO_META_CACHE[scenario_id] = meta
        return meta


def campaign_catalog() -> dict:
    """`GET /api/campaigns` 的响应体：世界 → 该世界下的剧本（只读）。

    口径（Issue #222 的拍板结论）：

    · 世界 = `data/worlds/*.json` 的世界模组，按 `world.key` 字典序；
    · 剧本 = `data/scenarios/*.json` 中 `meta.world` 与该世界 `key` 逐字相等
      的那批，按文件名主干字典序；
    · `meta.world` 不在 `data/worlds/` 里的剧本**不出现**（没有对应世界，
      绑定必然「世界对不上」，不列出来）；「科幻剧本不能用于奇幻世界」就是
      这条过滤 + `prism_guide.bind` 的同一校验；
    · 每条剧本只投影 `id` / `campaign` / `engine` 与可选的 `party_level` /
      `party_size` / `tone_pack`（`meta` 里已有字段，不发明格式）；
      剧本正文不出现。
    """
    worlds: list[dict] = []
    by_key: dict[str, dict] = {}
    for path in sorted((HERE / "data" / "worlds").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        block = data.get("world") if isinstance(data, dict) else None
        block = block if isinstance(block, dict) else {}
        key = str(block.get("key") or path.stem)
        if key in by_key:
            continue
        entry = {"key": key,
                 "name": str(block.get("name") or key),
                 "scenarios": []}
        by_key[key] = entry
        worlds.append(entry)
    for path in sorted((HERE / "data" / "scenarios").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        meta = data.get("meta") if isinstance(data, dict) else None
        meta = meta if isinstance(meta, dict) else {}
        world = by_key.get(str(meta.get("world") or ""))
        if world is None:
            continue
        scenario = {"id": path.stem,
                    "campaign": str(meta.get("campaign") or path.stem),
                    "engine": str(meta.get("engine") or "")}
        for field in ("party_level", "party_size", "tone_pack"):
            value = meta.get(field)
            if value is not None:
                scenario[field] = value
        world["scenarios"].append(scenario)
    return {"worlds": worlds}


def campaign_briefs(session: Session) -> dict:
    """会话当前绑定的只读简报：`world` / `scenario` 两个块（场景卡用）。

    未绑定（`guide` 里没有 key / id）时为两个空块 `{}`。名字来自数据文件；
    读不到时回退成 key / id 本身——**不编造名字**，也不读 `docs/`。只投影
    key / name 两个字段，`guide` 的其余内容（salt / 秘密 / 转录）永不出现。
    """
    guide = session.guide if isinstance(session.guide, dict) else {}
    world_key = str(guide.get("world_key") or "")
    scenario_id = str(guide.get("scenario_id") or "")
    world: dict = {}
    if world_key:
        try:
            data = _load_world(world_key)
        except (ValueError, OSError):
            data = None
        block = data.get("world") if isinstance(data, dict) else None
        block = block if isinstance(block, dict) else {}
        world = {"key": world_key, "name": str(block.get("name") or world_key)}
    scenario: dict = {}
    if scenario_id:
        meta = _scenario_meta(scenario_id) or {}
        scenario = {"id": scenario_id,
                    "name": str(meta.get("campaign") or scenario_id)}
    return {"world": world, "scenario": scenario}


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


def _new_sid() -> str:
    """新存档的 sid：`s-` + 16 位十六进制。

    过 `SID_RE`（`[A-Za-z0-9_-]{1,64}`）——它既是内存缓存的键，也是磁盘文件名，
    白名单保证不含路径分隔符（杜绝 `../` 拼接）。
    """
    return "s-" + secrets.token_hex(8)


class SaveError(Exception):
    """存档落盘失败（目录创建 / 临时写 / `os.replace` / 状态层拒载）。

    取代旧的「无条件吞异常」：`Session.save()` 失败时**抛这个异常**，调用方
    **必须显式处理**——要么回滚内存态到写入前，要么如实回报失败；不得把
    未持久化的状态当作已成功。
    只带 `sid` 与底层异常（`OSError` / `state.StateError`，见 `__cause__`），
    **不**携带存档内容或密钥。
    """

    def __init__(self, sid: str, cause: BaseException | None = None):
        super().__init__("存档 %s 落盘失败" % sid)
        self.sid = sid
        self.cause = cause
        if cause is not None:
            self.__cause__ = cause


def _campaign_dir(sid: str) -> pathlib.Path:
    """新档目录（`state` 的 §4.1 布局）：`<存档目录>/campaigns/<sid>/`。"""
    return SAVE_DIR / state.CAMP_DIRNAME / sid


def _campaign_state_path(sid: str) -> pathlib.Path | None:
    """新档里**可加载的检查点**路径（主 → `.bak`），没有就返回 None。

    只认「有检查点」：建局中途失败可能留下一个只有空事件日志 / `.tmp` 的目录，
    它不是一档存档（不被列表、撞击检测与加载认作已存在）。
    """
    for name in (state.SNAPSHOT_NAME, state.SNAPSHOT_BAK_NAME):
        path = _campaign_dir(sid) / name
        if path.is_file():
            return path
    return None


def _save_exists(sid: str) -> bool:
    """磁盘上是否已有这一档：**老档文件 / 新档检查点都算**。"""
    return ((SAVE_DIR / f"{sid}.json").is_file()
            or _campaign_state_path(sid) is not None)


def _legacy_parts(sid: str, d: dict) -> tuple[dict, dict, str, int]:
    """§10.1 的字段映射：老档（单文件 JSON）→ `(blocks, stream, save_name, created_ms)`。

    这里是「**只补不覆盖**」的落点：缺的键按 `_LEGACY_DEFAULTS` 补（可变默认值用
    零参工厂现造），已有的值一律**逐字搬**——脏值降级只发生在内存门面
    （`Session._apply_state`），迁移不在这里改写磁盘内容。

    `room` 不在 §10.1 的表内（该表成文于多人房间之前），按既有加载合同
    `_room_from` 规范化成最小房间——它的契约常量（`room_id` == sid 等）在盘上也必须成立。
    """
    for key, default in _LEGACY_DEFAULTS.items():
        if key not in d:
            d[key] = default() if callable(default) else default
    atlas = d.get("atlas")
    blocks = {
        "rules": copy.deepcopy(d.get("rules")),
        "atlas": copy.deepcopy(atlas) if isinstance(atlas, dict) else None,
        "guide": copy.deepcopy(d.get("guide")),
        "room": _room_from(d.get("room"), sid),
        # CHRON 块（§8.1）：老档没有 → 已由上面的默认值工厂补成空块；有则逐字搬。
        # 块的**语义**归 CHRON（脏值降级 / 版本迁移在 `chron.migrate_block`，
        # 由 C5 的加载路径负责），本文件只当一个不透明块搬运（STATE §6.1 / S8）。
        "chron": copy.deepcopy(d.get("chron")),
    }
    log = d.get("log") if isinstance(d.get("log"), list) else []
    return (
        blocks,
        {"log": copy.deepcopy(log), "cursor": max(0, _int_or(d.get("seq"), 0))},
        _text_or(d.get("save_name"), 40),
        int(round(_float_or(d.get("created"), 0.0) * 1000)),
    )


class Session:
    """一局游戏的**网页层内存门面**（不是规则运行时，也不是状态层）。

    门面持有：工程字段（标识 / 展示名 / 叙事流 + 游标）与四个**不透明块**
    （`rules` / `atlas` / `guide` / `room`）。块的形状各归其权威模块
    （prism_core / atlas / prism_guide / 多人房间），本类只负责按请求透出、
    并在 `save()` 时整块交给 `state`——语义一律不改。

    落盘 / 加载**委托 `state`**（STATE-DESIGN §11）：新档是每个战役一个目录的
    检查点 + 事件日志（`<存档目录>/campaigns/<sid>/`）；老档（旧单文件
    `<sid>.json`）在**第一次保存时**按 §10 惰性迁移成新档并留 `.bak`，
    只读加载不改写磁盘。门面与句柄的内存态在 `save()` / `load()` 两个方向上对齐，
    但两者**不是**同一份：句柄另有一层事件 `seq`（与展示流游标 `self.seq` 无关，
    §11：两条独立序列，禁止互相赋值）。
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
        # load() 与 save() 负责，本类不解释其中的规则语义。
        self.rules: dict = _empty_rules_snapshot(sid)
        # ATLAS 存档块（§3.4，与 rules 平级）：落盘的是「种子 + 增量 + 队伍
        # 位置」；运行时地图（self._atlas / self._locus）由 ensure_atlas()
        # 懒编译 / 还原，不直接落盘。_blocks_view() 把运行时折回存档块。
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
        # 状态层句柄（惰性打开：import / 建对象不碰磁盘，第一次 save 才开）。
        self._handle = None
        # 老档路径 + §10.1 映射结果：加载老档时记下，第一次 save 时落成新档
        # （§10.1：迁移在内存里完成，随后在下一次正常提交 / 检查点时落成新档）。
        self._legacy: pathlib.Path | None = None
        self._legacy_parts: tuple[dict, dict, str, int] | None = None

    # ── 持久化 ────────────────────────────────────────────
    def to_dict(self) -> dict:
        """门面视图（白名单，**老档形状**）：只读消费与老档测试夹具用。

        新档落盘由 `state` 负责（`save()` 写检查点）；这里保留老档的字段名
        （`sid` / `seq` / `atlas`）是为了让老档夹具与既有调用方不必认识 §4.1
        的新布局。字段与 `_LEGACY_DEFAULTS` 一一对应。
        """
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

    def _blocks_view(self) -> dict:
        """当前内存态的不透明块（逐块交给 `state`，语义不改）。"""
        return {
            "rules": copy.deepcopy(self.rules),
            "atlas": self._export_atlas_block(),
            "guide": copy.deepcopy(self.guide),
            "room": copy.deepcopy(self.room),
        }

    def _stream_view(self) -> dict:
        """展示流（§11）：`cursor` 承接门面的 `seq`（老字段名），日志逐条带着走。

        ⚠️ 与事件 `seq` 是**两条独立序列**：这里给的是叙事流的游标，
        `/api/log?since=` 读的也是它，禁止拿状态层的事件 `seq` 赋值（§11）。
        """
        return {"log": copy.deepcopy(self.log), "cursor": max(0, _int_or(self.seq, 0))}

    def _created_ms(self) -> int:
        """门面的 `created` 是秒（float）；新档里是毫秒（int，§10.1）。"""
        return int(round(_float_or(self.created, 0.0) * 1000))

    def _open_handle(self):
        """打开本局的状态层句柄：老档在这一步落成新档（§10.2），否则直接开。

        失败（磁盘 / 状态层拒载）转成 `SaveError`，并**保持** `_legacy` 未清——
        下一次 save 重试迁移（老档仍在磁盘上，不丢数据）。
        """
        legacy = self._legacy
        try:
            if legacy is not None:
                blocks, stream, save_name, created = self._legacy_parts
                handle = state.migrate_legacy(
                    self.sid, str(legacy), blocks=blocks, stream=stream,
                    save_name=save_name, created=created, base_dir=str(SAVE_DIR))
                self._legacy = None            # 迁移成功才清（失败可重试）
                self._legacy_parts = None
            else:
                handle = state.open_campaign(self.sid, base_dir=str(SAVE_DIR))
        except (OSError, state.StateError) as exc:
            raise SaveError(self.sid, exc) from exc
        self._handle = handle
        return handle

    def save(self) -> None:
        """落盘：把门面态交给 `state` 写成**检查点**（§11）。

        - 不透明块（`rules` / `atlas` / `guide` / `room`）与展示流（`log` / `cursor`）
          **不进事件溯源**（§11：它们不是事实，两条序列禁止互相赋值），只随检查点
          落盘——落盘由 `state._write_checkpoint` 用「`.tmp` + fsync + `os.replace`」
          完成，并带 `checksum` 与 `snapshot.json.bak` 回退；
        - 老档（旧单文件）在**第一次 save 时**才落成新档：先留 `.bak`、再写检查点、
          再删老档（§10.2）——只读加载不改写磁盘；
        - 落盘失败**抛 `SaveError`**（与改动前同一契约）：调用方据此回滚内存态或
          如实回报失败，绝不把未持久化的状态当作已成功。失败时磁盘维持写入前的
          旧档（`os.replace` 未执行；老档也还在）。
        """
        with self.lock:
            handle = self._handle or self._open_handle()
            handle.stage(blocks=self._blocks_view(), stream=self._stream_view(),
                         save_name=self.save_name, created=self._created_ms())
            try:
                handle.flush()
            except OSError as exc:
                raise SaveError(self.sid, exc) from exc

    def _apply_state(self, blocks: dict, stream: dict, save_name, created_ms) -> None:
        """把 `state` 的视图 / 元信息抄进内存门面——**逐字段清洗，脏值降级**。

        与老档加载走同一条路（`load()` 的两个分支都汇到这里），因此新档 / 老档
        的加载口径一致：类型不对就退回默认值，一个坏键不换掉整块。
        """
        blocks = blocks if isinstance(blocks, dict) else {}
        stream = stream if isinstance(stream, dict) else {}
        self.created = _float_or(created_ms, 0.0) / 1000.0
        raw_log = stream.get("log")
        if isinstance(raw_log, list):
            self.log = [e for e in raw_log if isinstance(e, dict)]
        self.seq = max(0, _int_or(stream.get("cursor"), 0))
        # seq 是增量拉取游标：脏数据里若 seq 落后于日志本身，取二者最大值，
        # 否则客户端重连后会把已读过的条目再推一遍。
        if self.log:
            self.seq = max(self.seq, max((_int_or(e.get("seq"), 0) for e in self.log),
                                         default=0))
        self.save_name = _text_or(save_name, 40)
        # 规则会话：缺块 / 脏值的老档在这一刻拿到空规则会话，形状按 prism_core
        # 的契约还原（未知键剔掉、脏值降级），不就地改写磁盘上的块。
        self.rules = _restore_rules(blocks.get("rules"), self.sid)
        # ATLAS 存档块：缺块 / 脏值按 None 处理，第一次用到地图时由
        # ensure_atlas() 按当前世界懒编译，不报错、不改写磁盘上的块。
        block = blocks.get("atlas")
        self.atlas_block = copy.deepcopy(block) if isinstance(block, dict) else None
        # 导引者块：**显式**在 rules 旁边还原（§7 加载合同，G2 就要做）。
        # 按键处理——旁边一个键脏了不会把整块换成空块（否则会删掉 realizations）。
        self.guide = _guide_from(blocks.get("guide"))
        # 多人房间块（§3.2 / M-16）：缺块 / 脏值按最小「单人房」处理
        # （host 空、members 空），只补不覆盖。
        self.room = _room_from(blocks.get("room"), self.sid)

    @classmethod
    def load(cls, sid: str) -> "Session | None":
        """加载（新档走 `state`，老档按 §10.1 在内存里映射）。

        老档的**落盘**推迟到下一次 `save()`（§10.1：迁移在内存里完成，随后在
        下一次正常提交 / 检查点时落成新档），因此只读接口不改写磁盘上的老档。
        读不了（坏档 / 状态层拒载）→ 返回 `None`，沿用既有 400 语义。
        """
        # sid 先过白名单再碰文件系统：即使请求头被伪造，也无法用 ../ 探测路径。
        if not SID_RE.fullmatch(sid or ""):
            return None
        s = cls(sid)
        if _campaign_state_path(sid) is not None:     # 新档：委托 state 加载
            try:
                handle = state.open_campaign(sid, base_dir=str(SAVE_DIR))
            except (OSError, state.StateError):
                return None
            s._handle = handle
            view = handle.view()
            meta = handle.meta()
            s._apply_state(view.get("blocks") or {}, view.get("stream") or {},
                           meta.get("save_name"), meta.get("created"))
            return s
        legacy = SAVE_DIR / f"{sid}.json"        # 老档：内存里映射，save() 时落成新档
        if not legacy.is_file():
            return None
        try:
            d = json.loads(legacy.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(d, dict):
            return None
        blocks, stream, save_name, created_ms = _legacy_parts(sid, d)
        s._legacy = legacy
        s._legacy_parts = (blocks, stream, save_name, created_ms)
        s._apply_state(blocks, stream, save_name, created_ms)
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


def _save_summary(sid: str) -> dict | None:
    """一条存档摘要（**新档 / 老档两种布局都认**）；没有可读的存档返回 None。

    新档读检查点 `campaigns/<sid>/snapshot.json`，缺失 / 读不了再退
    `snapshot.json.bak`（与 `state` 的加载顺序一致），最后才是老档
    `<sid>.json`；两者都在时以新档为准（迁移中途的老档残留不再多出一行）。
    日志条数取「展示流」的 `log`（老档在顶层、新档在 `stream.log`）。
    """
    candidates = (
        _campaign_dir(sid) / state.SNAPSHOT_NAME,
        _campaign_dir(sid) / state.SNAPSHOT_BAK_NAME,
        SAVE_DIR / f"{sid}.json",
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue            # 半个 / 手改坏的 JSON 不进列表，也不让整次扫描失败
        if not isinstance(d, dict):
            continue
        stream = d.get("stream") if isinstance(d.get("stream"), dict) else {}
        log = stream.get("log") if isinstance(stream.get("log"), list) else None
        if log is None:
            log = d.get("log") if isinstance(d.get("log"), list) else []
        try:
            mt = path.stat().st_mtime
        except OSError:
            mt = 0.0
        save_name = _text_or(d.get("save_name"), 40)
        return {
            "id": sid,
            "name": save_name or default_display_name(sid),
            "named": bool(save_name),
            "mtime": mt,
            "mtime_text": time.strftime("%m-%d %H:%M", time.localtime(mt)) if mt else "—",
            "log_count": len(log),
            # TODO(M3)：PRISM 摘要字段（世界 / 角色 / 进度……）在此追加。
        }
    return None


def list_saves() -> tuple[list[dict], int]:
    """扫描存档目录，返回 (摘要列表, 实际总条数)——**新档与老档都列**。

    列表只返回最近 SAVE_LIST_LIMIT 条（按 mtime 倒序），但总条数照实返回，
    前端才能提示「还有更多旧存档未显示」，而不是假装只有这些。
    磁盘文件一律不删——只截断返回给前端的部分。
    """
    sids: set[str] = set()
    try:
        for f in SAVE_DIR.glob("*.json"):
            if not f.name.startswith("."):      # 原子写留下的临时文件
                if SID_RE.fullmatch(f.stem):
                    sids.add(f.stem)
        for entry in (SAVE_DIR / state.CAMP_DIRNAME).iterdir():
            if entry.is_dir() and SID_RE.fullmatch(entry.name):
                sids.add(entry.name)
    except Exception:
        return [], 0
    rows = [row for row in (_save_summary(sid) for sid in sorted(sids)) if row]
    rows.sort(key=lambda r: -r["mtime"])
    total = len(rows)
    return rows[:SAVE_LIST_LIMIT], total


def rules_view(session: Session) -> dict:
    """规则会话的只读视图（`GET /api/session` 直接吃这份）。

    字段形状**全部**来自 prism_core.RuleSession.snapshot()（party / scene /
    active_unit_id / log / done_actions / action_fails / pressure / combat），
    这里只做深拷贝——存档层另立一套字段名就必然与规则层漂移。可见性由
    规则层决定：当前还没有需要隐藏的内部字段，故不做额外裁剪。

    额外附两个块：`save`（展示名 / 日志游标，前端不必再多打一次 /api/saves）
    与 `world` / `scenario`（当前绑定；未绑定为空块，见 `campaign_briefs`，
    UI-DESIGN §7 的「快照里没有 world / scenario」缺口在此补齐——只读派生，
    不写回存档）。
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
    briefs = campaign_briefs(session)
    view["world"] = briefs["world"]
    view["scenario"] = briefs["scenario"]
    return view


# --------------------------------------------------------------------------
# 状态层只读视图（M7-4，STATE-DESIGN §16）：GET /api/state 与 GET /api/events
#
# 两条都**只读**：不 save()、不触发检查点、不改状态（存档文件指纹不变），
# 也不为「尚未迁移的老档」开句柄——开句柄本身就会落盘迁移（§10.2）。
# --------------------------------------------------------------------------


def _block_presence(blocks) -> dict:
    """块存在性：块名 → 值是否非 `null`（只回存在性，**不回**块内容）。"""
    if not isinstance(blocks, dict):
        return {}
    return {name: blocks[name] is not None for name in sorted(blocks)}


def state_view(session: Session) -> dict:
    """`GET /api/state` 的响应体：当前战役的状态层只读摘要（STATE §16 M7-4）。

    `campaign_id` / `seq` / `world_time` 原样取自状态层（`world_time` 是不透明
    字段，形状归 M9）；`blocks` 只回**存在性**（`view()['blocks']` 的各键是否非
    `null`），不回块内容。

    有句柄（新档 / 已迁移老档）时取 `handle.view()`；老档尚未迁移时**不**开句柄
    （开 = 落盘迁移），改从内存门面派生同一形状——此时状态层还没有这条战役的
    事件日志，`seq` 恒为 0、`world_time` 为 null。
    """
    with session.lock:
        handle = session._handle
        if handle is not None:
            view = handle.view()
            return {
                "campaign_id": view.get("campaign_id") or session.sid,
                "seq": max(0, _int_or(view.get("seq"), 0)),
                "world_time": view.get("world_time"),
                "blocks": _block_presence(view.get("blocks")),
            }
        return {
            "campaign_id": session.sid,
            "seq": 0,
            "world_time": None,
            "blocks": _block_presence(session._blocks_view()),
        }


def events_view(session: Session, since: int) -> dict:
    """`GET /api/events?since=` 的响应体：只回 `seq > since` 的事件（STATE §16 M7-4）。

    读取 / 链校验 / 跨归档段拼接全在状态层（`Handle.events_since`，§9），本层不
    复制一套归档逻辑；老档尚未迁移时状态层还没有事件日志 → 空列表。事件封套
    原样透出（§5.1），不做可见性过滤（那是 M10 权限模型的事，§12.4）。
    """
    with session.lock:
        handle = session._handle
        if handle is None:
            return {"events": []}
        return {"events": handle.events_since(since)}


def _since_param(path: str) -> int:
    """解析 `?since=`（STATE §16 M7-4）：缺省 / 空 = 0（与 `/api/log?since=` 同口径）。

    合法值 = 纯十进制数字串（1–18 位；上限防超长整数撞 `int()` 的位数保护）；
    `-1` / `1.5` / `1e2` / 带空白等一律抛 `ValueError` → 400 固定短语，
    **不**静默当 0（那是 `/api/log` 的历史行为，本接口不沿用非法值兜底）。
    """
    query = urllib.parse.parse_qs(urllib.parse.urlparse(path).query)
    raw = (query.get("since") or [""])[0]
    if raw == "":
        return 0
    if not re.fullmatch(r"[0-9]{1,18}", raw):
        raise ValueError("since 需为非负整数")
    return int(raw)


# --------------------------------------------------------------------------
# 行动管线（M8-A3，ACTION-DESIGN §3.3 / §7 / §8 / §9）
#
# 六段（§3.3）：解析（`propose` 的第 1 段）→ 检索 → 验证 → 结算 → 提交
# （`commit` 的第 3–5 段）→ 叙事（第 6 段）。**只有第 1 段与第 6 段碰模型**；
# 第 3–5 段一行都不调，所以 `mode: "action"` 在没有 AI 配置时照常走完一轮。
# --------------------------------------------------------------------------


class _ActionError(Exception):
    """写接口的固定错误（§9.5）：固定短语 + **仅状态行之前**可用的 HTTP 码。

    状态行写出之后不再有第二行 HTTP 状态——那时失败一律走 SSE `fallback` /
    `done`，所以本异常只在 `_begin_stream()` 之前抛出，由 `do_POST` 翻成
    唯一一行状态（正文 `{"error": <固定短语>}`）。
    """

    def __init__(self, phrase: str, code: int):
        super().__init__(phrase)
        self.phrase = phrase
        self.code = code


class _SyncOnlySchedule:
    """M8 的排程钩子空实现（§8.3）：同步行动**不推进世界时间**。

    协议是 §8.2 的 L1 `ScheduleHook`——`enqueue` 的**返回键固定六个**，M9
    只在值上做文章（`mode` 转 `"async"`、`task_id` 取 CHRON 的行动 id、
    `start_at` / `expected_end_at` 取世界分钟、`barrier` 按事件屏障置位）。
    `cancel` 在 M8 恒为 `False`：同步行动不可取消（§8.4 的中断语义归 M9）。
    """

    def enqueue(self, proposal, draft, *, base_seq):
        """把一次已验证的行动交给时间线（§8.2 L1）。

        `proposal` 是第 4 节的提案；`draft` 是第 4 段结算出的结果草案
        （尚未提交）；`base_seq` 是提交方的乐观并发基线。本实现只回执
        `mode == "sync"` 的固定形状，不改调用方任何状态、不碰磁盘。
        """
        return {"mode": "sync", "task_id": None,
                "start_at": ACTION_EPOCH_MINUTE,
                "expected_end_at": ACTION_EPOCH_MINUTE,
                "barrier": False, "resume_token": None}

    def cancel(self, task_id):
        """取消一个已排程行动：M8 的同步行动不可取消（恒 `False`）。"""
        return False


class _ChronScheduleHook:
    """L1 `ScheduleHook` 的 **CHRON 适配器**（ACTION §8.2 / CHRON §4.3）。

    模块级注入点 `SCHEDULE_HOOK` 装的就是它；L1 的**返回键固定六个**
    （`mode` / `task_id` / `start_at` / `expected_end_at` / `barrier` /
    `resume_token`），M9 只在值上做文章：

    - **即时行动**（`can_defer = false` 且无中断条件）→ `mode = "sync"`：
      **不**调 `chron.schedule`、不碰磁盘（同步路径逐字节不变，§4.3 关键约定）；
    - **长行动** → `mode = "async"`：由 `_chron_schedule` 折叠 `time_cost` /
      `schedule_hint` / 中断条件入 CHRON 队列并**提交排程事件**，
      `task_id` = CHRON 的行动 id（`act_<proposal 的 32hex>`）。

    适配器本身**无绑定**（可被多线程共用）；本局上下文由管线用 `bind(session)`
    取一份绑定副本，再调用——见 `_schedule_hook`。`draft` 参数对长行动是
    `None`：排程发生在**结算之前**，那一刻还没有结果草案（§8.2 的调用时机
    在同步路径上仍是「结算之后、提交之前」）。
    """

    def __init__(self, session=None):
        self.session = session

    def bind(self, session):
        """返回一份**绑定本局**的适配器（不改动自己：单例可被并发请求共用）。"""
        return _ChronScheduleHook(session) if session is not None else self

    def enqueue(self, proposal, draft, *, base_seq):
        """把一次已验证的行动交给时间线（§8.2 L1）。

        长行动走 `_chron_schedule`（CHRON 的 L2 `schedule`）；即时行动（含
        「没有绑定本局」的情形）只回 `mode = "sync"` 的固定形状——**不排程、
        不碰磁盘**，调用方据此继续走 M8 的同步结算。
        """
        session = self.session
        if session is None or not _action_is_deferred(proposal):
            return _sync_hook_result(_chron_world_minute(session))
        return _chron_schedule(session, proposal, base_seq=base_seq)

    def cancel(self, task_id):
        """取消一个已排程行动（§4.3 的 L1 ↔ L2 映射）：成功与否返回 bool。"""
        if self.session is None:
            return False
        return _chron_cancel(self.session, task_id)


def _sync_hook_result(world_minute) -> dict:
    """`mode = "sync"` 的固定六键（§8.2）：即时行动不推进世界时间（§8.1）。"""
    minute = _int_or(world_minute, ACTION_EPOCH_MINUTE)
    return {"mode": "sync", "task_id": None, "start_at": minute,
            "expected_end_at": minute, "barrier": False, "resume_token": None}


# 排程钩子的**注入点**（§8.3）：默认 = CHRON 的 L1 适配器（M9 接入）；
# 替换成别的实现（含 M8 的空实现）就整体走那一份——测试与自定义注入都走这里。
SCHEDULE_HOOK = _ChronScheduleHook()


def _schedule_hook(session):
    """取本次调用该用的排程钩子（§8.3 的注入选择）。

    默认适配器提供 `bind`：按**本局**取一份绑定副本（本局的句柄 / 锁）。
    替换过的实现（测试的假钩子 / 自定义）没有 `bind`，**原样**使用——
    与 A3 时代 `SCHEDULE_HOOK.enqueue(...)` 的调用面逐字一致。
    """
    bind = getattr(SCHEDULE_HOOK, "bind", None)
    return bind(session) if callable(bind) else SCHEDULE_HOOK


# 事件类型与规则版本（状态层事件封套的必填字段，STATE §5.1）：
# `type` 走 `<domain>.<verb>`；`rules_version` 与 A1 的 `rule_ref.version` 同文。
ACTION_EVENT_TYPE = "action.commit"
ACTION_RULES_VERSION = "prism-core@0.1"

# 管线用到的**进程内**短期记忆（§9.2 / §9.4 的口径：不落盘、不新增第二套
# 持久化字段；权威事实仍在状态层的事件日志与它的持久幂等索引里）。
_action_idem: dict = {}        # (sid, Idempotency-Key) → {"payload": 首次结果, "ts"}
_action_committed: dict = {}   # (sid, proposal_id) → {"ts"}（防「换个键再赌一次」）
_action_clarifies: dict = {}   # (sid, clarify_id) → {"clarify", "text", "ts"}
_action_confirms: dict = {}    # (sid, proposal_id) → {"token", "expires_at"}
_ACTION_LOCK = threading.RLock()


def _action_now_ms() -> int:
    """当前毫秒时间戳（管线内部记录用）。"""
    return int(time.time() * 1000)


def _action_prune(now_ms: int) -> None:
    """顺手清掉过期的进程内记录（在 `_ACTION_LOCK` 内调用）。

    只按时间清；记录量只与在办会话数相关，没有容量问题。
    """
    for store, ttl_s in ((_action_idem, ACTION_IDEM_TTL_S),
                         (_action_clarifies, ACTION_CLARIFY_TTL_S)):
        for key, record in list(store.items()):
            if now_ms - int(record.get("ts") or 0) > ttl_s * 1000:
                store.pop(key, None)
    for key, record in list(_action_confirms.items()):
        if now_ms > int(record.get("expires_at") or 0):
            _action_confirms.pop(key, None)


# ── 上下文查询（第 2–3 段的读面：全是纯读，不结算）───────────────────────


def _action_menu(rules) -> list:
    """当前场景行动单（§9.1 `catalog`）：`[{"id", "label", "risk"}]`。

    形状归 `prism_guide.action_menu`（只认带非空 id 的条目，`risk` 只留 §6.1
    的五级之一，其余留空串）；导引者模块缺失时按**同一口径**本地读一遍规则
    快照——catalog 与显式行动在离线（无 AI）时也必须可用（§7.1）。
    """
    if prism_guide is not None:
        return prism_guide.action_menu(rules)
    rules = rules if isinstance(rules, dict) else {}
    scene = rules.get("scene")
    actions = scene.get("actions") if isinstance(scene, dict) else None
    menu = []
    for item in actions or []:
        if not isinstance(item, dict):
            continue
        action_id = str(item.get("id") or "")
        if not action_id:
            continue
        risk = str(item.get("risk") or "")
        menu.append({"id": action_id,
                     "label": str(item.get("label") or action_id),
                     "risk": risk if risk in prism_core.ACTION_RISKS else ""})
    return menu


def _action_scene_entry(rules, action_id):
    """场景行动表里 id 命中的**原始条目**（没有 → `None`）。

    §9.5「没有这个行动」的判据就是它：`intent.verb` 的取值域是当前场景的
    行动表（§4.2），不是任意串。
    """
    rules = rules if isinstance(rules, dict) else {}
    scene = rules.get("scene")
    actions = scene.get("actions") if isinstance(scene, dict) else None
    wanted = str(action_id or "")
    for item in actions or []:
        if isinstance(item, dict) and str(item.get("id") or "") == wanted:
            return item
    return None


def _action_risk(rules, action_id) -> str:
    """提案的风险级（§6.1）：行动表写死的优先，否则取最保守的 R2。"""
    entry = _action_scene_entry(rules, action_id)
    risk = str((entry or {}).get("risk") or "")
    return risk if risk in prism_core.ACTION_RISKS else "R2"


def _action_is_confirm_risk(risk) -> bool:
    """R2–R4 需要确认（§6.4）：R0 / R1 直通，其余默认保守必确认。"""
    return str(risk or "") in ("R2", "R3", "R4")


def _action_time_cost(entry) -> dict:
    """提案的 `time_cost`（§8.1）：行动表给的优先，形状不对退回默认值。

    只认 `{value: int>=0, unit: 枚举}`；单位换算（到世界分钟）归 CHRON / M9，
    M8 只把它当**结果字段**记录下来（§8.1「M8 语义」）。
    """
    raw = (entry or {}).get("time_cost")
    if isinstance(raw, dict):
        value = raw.get("value")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0 \
                and str(raw.get("unit") or "") in prism_core.TIME_COST_UNITS:
            return {"value": value, "unit": str(raw["unit"])}
    return {"value": 0, "unit": "minute"}


def _action_actor(rules) -> dict:
    """行动者（§4.2）：`active_unit_id`，空则 `party[0]`（同 `prism_guide.settle`）。"""
    rules = rules if isinstance(rules, dict) else {}
    party = [unit for unit in (rules.get("party") or []) if isinstance(unit, dict)]
    active = str(rules.get("active_unit_id") or "")
    picked = None
    for unit in party:
        if active and str(unit.get("id") or "") == active:
            picked = unit
            break
    if picked is None and party:
        picked = party[0]
    return {"kind": "player", "id": str((picked or {}).get("id") or "")}


def _action_party_ids(rules) -> list:
    """本局队伍里的角色 id（§6.4 前置条件面：行动者必须是桌上的人）。"""
    rules = rules if isinstance(rules, dict) else {}
    return [str(unit.get("id") or "") for unit in (rules.get("party") or [])
            if isinstance(unit, dict)]


def _action_entity_ids(guide) -> set:
    """本场可引用的实体 id（§9.5「目标不存在」的判据）。

    来源是已绑定剧本的地点 / 人物 / 节点 / 线索四张表（`prism_guide.entity_index`）。
    导引者模块缺失、或本局还没绑定剧本时是**空集**——此刻服务端无法证明
    目标存在，按 §4.2「目标须能过实体引用校验」判 404，不猜。
    """
    if prism_guide is None:
        return set()
    try:
        return set(prism_guide.entity_index(guide))
    except Exception:      # noqa: BLE001 — 实体表读不出来不等于目标存在
        return set()


def _action_target_ref(value):
    """把客户端给的 `params.target` 规范成 §4.2 的实体引用（没有 → `None`）。"""
    if isinstance(value, dict):
        entity_id = str(value.get("id") or "")
        if not entity_id:
            return None
        return {"kind": str(value.get("kind") or "npc"), "id": entity_id}
    entity_id = str(value or "").strip()
    return {"kind": "npc", "id": entity_id} if entity_id else None


# ── CHRON 排程的读面：提案 ↔ 行动卡（M9-C6，ACTION §8 / CHRON §4.3）────────


def _action_is_deferred(proposal) -> bool:
    """这一份提案是不是**长行动**（§4.3 关键约定）？

    `schedule_hint.can_defer = true` **或**带非空 `interrupt_conditions` 才进
    CHRON 队列；其余（含缺 `schedule_hint` 的默认值）走 M8 的同步结算。
    """
    proposal = proposal if isinstance(proposal, dict) else {}
    hint = proposal.get("schedule_hint")
    can_defer = bool(hint.get("can_defer")) if isinstance(hint, dict) else False
    conditions = proposal.get("interrupt_conditions")
    return can_defer or bool(conditions if isinstance(conditions, list) else [])


def _chron_task_id(proposal) -> str:
    """CHRON 的行动 id（= L1 的 `task_id`）：由 `proposal_id` 派生，同一提案恒定。

    `p_<32hex>` → `act_<32hex>`：与结果契约的 `act_p_<32hex>`（A1 的
    `new_action_id`）不同名，两套 id 不混；派生使「同一提案的重复请求得到同一
    `task_id`」，也让到期结算能由 `task_id` 反推回 `proposal_id`（见
    `_chron_proposal_id`）——排程与结算可能隔着重启，卡上就只有这个 id。
    """
    pid = str((proposal or {}).get("proposal_id") or "")
    tail = pid[len("p_"):] if pid.startswith("p_") else pid
    return CHRON_TASK_PREFIX + tail


def _chron_proposal_id(task_id) -> str:
    """`task_id` → 结算用的 `proposal_id`（`_chron_task_id` 的逆映射）。"""
    task_id = str(task_id or "")
    tail = task_id[len(CHRON_TASK_PREFIX):] if task_id.startswith(CHRON_TASK_PREFIX) \
        else task_id
    return "p_" + tail


def _chron_time_minutes(proposal) -> int:
    """`time_cost` → 整数分钟（§8.1 / CHRON §2.3）：CHRON 只认分钟（≥ 0）。"""
    time_cost = proposal.get("time_cost") if isinstance(proposal, dict) else None
    time_cost = time_cost if isinstance(time_cost, dict) else {}
    value = time_cost.get("value")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return 0
    unit = str(time_cost.get("unit") or "minute")
    return value * CHRON_UNIT_MINUTES.get(unit, 1)


def _chron_world_minute(session) -> int:
    """本局当前世界分钟（§3.1）：**只读、不碰磁盘**。

    状态层句柄已开就按它的 `world_time` 读（创世 / 未接入 CHRON 时是 `None`，
    退回纪元 `ACTION_EPOCH_MINUTE`）；句柄没开就按纪元——`mode = "sync"` 的
    回执**不值得**为读一个数去开句柄（开句柄会碰磁盘、还会迁移老档）。
    """
    handle = getattr(session, "_handle", None) if session is not None else None
    if handle is None:
        return ACTION_EPOCH_MINUTE
    minute = chron.world_time_minute(handle.view().get("world_time"))
    return ACTION_EPOCH_MINUTE if minute is None else minute


def _chron_card(proposal) -> dict:
    """提案 → CHRON 行动卡草稿（§4.1 / §4.3 的 L1 → L2 映射）。

    - `kind` = `intent.verb`（当前场景的行动 id，§4.2）；`actor` = 提案的行动者；
    - `time_cost` 折成整数分钟（`_chron_time_minutes`）；
    - `priority` / `depends_on` 由 `schedule_hint` 透传（依赖是**其它 CHRON
      行动 id**；缺失 / 形状不对按契约默认值）；
    - `interrupt_conditions` 原样透传（CHRON 只读其中的 `kind`，§6.2 判据）；
    - `idempotency_key` 留空：C5 的幂等以**事件键**（`<action_id>:e<序号>:<类型>`）
      为准，卡上的该字段在 C3 / C4 不被读取——不在这里发明第二种键形。
    - `place` / `resource`（§5.5 的冲突识别键）暂不接线：规则包还没有给这两个
      维度的声明，**不臆造**（将来由规则包 / M10 给，另开单）。
    """
    hint = proposal.get("schedule_hint") if isinstance(proposal, dict) else None
    hint = hint if isinstance(hint, dict) else {}
    priority = hint.get("priority")
    if not isinstance(priority, int) or isinstance(priority, bool):
        priority = 0
    deps = [str(dep) for dep in (hint.get("depends_on") or []) if str(dep)]
    conditions = proposal.get("interrupt_conditions") \
        if isinstance(proposal, dict) else None
    return chron.new_action(
        _chron_task_id(proposal),
        actor=str((proposal.get("actor") or {}).get("id") or ""),
        kind=str((proposal.get("intent") or {}).get("verb") or ""),
        time_cost=_chron_time_minutes(proposal),
        priority=priority, depends_on=deps,
        interrupt_conditions=list(conditions if isinstance(conditions, list) else []),
    )


def _chron_proposal_from_card(card, session) -> dict:
    """行动卡 → 到期结算用的提案（§4.2）：重启后也照常重建（不读进程内缓存）。

    `desired` / `risk` 按**当前**场景行动表补齐（叙事与确认口径以结算那一刻的
    规则为准）；`time_cost` 以卡上的整数分钟为准（单位回写成 `minute`）。
    """
    action_id = str(card.get("kind") or "")
    entry = _action_scene_entry(session.rules, action_id) or {}
    return prism_core.new_proposal(
        proposal_id=_chron_proposal_id(card.get("action_id")),
        actor={"kind": "player", "id": str(card.get("actor") or "")},
        intent={"verb": action_id, "target": None,
                "desired": str(entry.get("label") or action_id), "means": ""},
        source="action",
        risk=_action_risk(session.rules, action_id),
        time_cost={"value": int(card.get("time_cost") or 0), "unit": "minute"},
        interrupt_conditions=list(card.get("interrupt_conditions") or []),
        created_at=_action_now_ms(),
    )


# ── 第 1 段（显式路径）与服务端重验 ─────────────────────────────────────


def _action_proposal_from_id(session, action_id, params) -> dict:
    """显式行动 → 提案（§9.2 `mode: "action"`）：**一行模型都不调**（§7.1）。

    行动表里没有这个 id → 404 `没有这个行动`；桌上还没有角色 → 409
    `现在做不了`（§6.4 的前置条件面）。`params` 只透传 A1 契约里的
    `dc_override` / `items_used` 与一个可选 `target` / `desired` / `means`
    （§4.2 `params` 是**结构参数**，不是自由文本）。

    排程相关的三个字段（`schedule_hint` / `interruptible` /
    `interrupt_conditions`）是 §4.2 的**既有**提案字段，由行动表（世界规则包）
    声明，这里**只读不造**：`schedule_hint` 形状不对按契约默认值
    （`can_defer = false`，同步路径不变），中断条件只收对象 / 字符串项
    （与 CHRON §6.2 的「只认 `kind`、其余形状忽略」同口径）。
    """
    entry = _action_scene_entry(session.rules, action_id)
    if entry is None:
        raise _ActionError(ACTION_NO_ACTION, 404)
    actor = _action_actor(session.rules)
    if not actor["id"]:
        raise _ActionError(ACTION_NOT_NOW, 409)
    params = params if isinstance(params, dict) else {}
    proposal = prism_core.new_proposal(
        actor=actor,
        intent={"verb": str(entry.get("id") or action_id),
                "target": _action_target_ref(params.get("target")),
                "desired": str(params.get("desired") or entry.get("label")
                               or action_id),
                "means": str(params.get("means") or "")},
        params={"dc_override": params.get("dc_override"),
                "items_used": list(params.get("items_used") or [])},
        source="action",
        risk=_action_risk(session.rules, action_id),
        time_cost=_action_time_cost(entry),
        schedule_hint=_action_schedule_hint(entry),
        interruptible=entry.get("interruptible") is not False,
        interrupt_conditions=_action_interrupt_conditions(entry),
        created_at=_action_now_ms(),
    )
    return proposal


def _action_schedule_hint(entry) -> dict:
    """行动表的 `schedule_hint`（§4.2）：缺失 / 形状不对 → 契约默认值。

    默认 `can_defer = false`（M8 行为不变，§4.3 关键约定）；`depends_on`/
    `priority` 只收合法形状（字符串列表 / 整数），脏值逐项丢弃，不猜。
    """
    raw = (entry or {}).get("schedule_hint")
    raw = raw if isinstance(raw, dict) else {}
    deps = raw.get("depends_on")
    priority = raw.get("priority")
    return {
        "can_defer": bool(raw.get("can_defer")),
        "depends_on": [str(dep) for dep in deps if str(dep)]
        if isinstance(deps, list) else [],
        "priority": priority if isinstance(priority, int)
        and not isinstance(priority, bool) else 0,
    }


def _action_interrupt_conditions(entry) -> list:
    """行动表的 `interrupt_conditions`（§4.2 / §8.4）：只收对象 / 字符串项。"""
    raw = (entry or {}).get("interrupt_conditions")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, (dict, str))]


def _action_prepare_proposal(session, raw) -> dict:
    """服务端**重验**客户端回带的提案（§9.2：永不信任回带的形状与实体）。

    - 不是对象 / 形状过不了 A1 `validate_proposal` → 404 `没有这个提案`；
    - 未知的更高 `schema_version` → 409 `版本不支持`（A1 的迁移惰性补齐
      缺键、不覆盖已有值、不猜更高版本）；
    - `intent.verb` 不在当前场景的行动表里 → 404 `没有这个行动`；
    - `intent.target` 的实体引用在本场实体表里落不了地 → 404 `目标不存在`；
    - 行动者不是本局队伍里的角色 → 409 `现在做不了`。
    """
    if not isinstance(raw, dict):
        raise _ActionError(ACTION_NO_PROPOSAL, 404)
    try:
        proposal = prism_core.migrate_proposal(raw)
    except ValueError:
        raise _ActionError(ACTION_BAD_VERSION, 409)
    if prism_core.validate_proposal(proposal):
        raise _ActionError(ACTION_NO_PROPOSAL, 404)
    intent = proposal.get("intent") if isinstance(proposal.get("intent"), dict) else {}
    if _action_scene_entry(session.rules, intent.get("verb")) is None:
        raise _ActionError(ACTION_NO_ACTION, 404)
    target = intent.get("target")
    if isinstance(target, dict) and str(target.get("id") or "") \
            and str(target["id"]) not in _action_entity_ids(session.guide):
        raise _ActionError(ACTION_NO_TARGET, 404)
    actor_id = str((proposal.get("actor") or {}).get("id") or "")
    if actor_id not in _action_party_ids(session.rules):
        raise _ActionError(ACTION_NOT_NOW, 409)
    return proposal


# ── 第 4 段（结算）→ §8.2 钩子 → 第 5 段（提交）──────────────────────────


def _action_roll_ref(roll) -> str:
    """随机判定引用（§5.1 `rule_ref.roll_ref`）。

    M8 没有随机流编号（事件层的 `roll_refs` 叙事归 M9 之后）；这里用**骰面
    明细的规范化 JSON 摘要前 12 位**当引用——同一份骰面永远得到同一个
    `roll_ref`，复盘时可逐字比对。没有掷骰（`auto_pass` / 纯叙述 / 幂等回放）
    时是空串。
    """
    if not isinstance(roll, dict) or not roll:
        return ""
    blob = json.dumps(roll, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
    return "rand_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _action_result(proposal, perf, status, entry) -> dict:
    """把结算产出拼成 §5.1 的行动结果（**提交之前**的草案）。

    `costs` / `effects` 只放规则层真结算出来的**结构化**条目（时间 / 资源 /
    活力 / 状态 / 奖励），不写自由文本；`event_ids` / `commit_ref` /
    `committed` 由第 5 段（状态层提交）回填。险成（`partial`）的代价内容
    在 M8 还没有裁定钩子（§5.2 / TODO(M3)），按契约在 `rule_ref` 注明
    `pending_judgement`。
    """
    actor_id = str((proposal.get("actor") or {}).get("id") or "")
    time_cost = proposal.get("time_cost") if isinstance(
        proposal.get("time_cost"), dict) else {"value": 0, "unit": "minute"}
    if int(time_cost.get("value") or 0) > 0:
        costs = [{"kind": "time", "value": int(time_cost["value"]),
                  "unit": str(time_cost.get("unit") or "minute")}]
    else:
        costs = []
    failure = perf.get("cost") if isinstance(perf.get("cost"), dict) else {}
    detail = failure.get("cost") if isinstance(failure.get("cost"), dict) else {}
    for pool in ("focus", "strain"):
        used = int(detail.get(pool) or 0)
        if used:
            costs.append({"kind": "resource", "pool": pool, "value": used,
                          "target": actor_id})
    damage = failure.get("damage") if isinstance(failure.get("damage"), dict) else {}
    lost = int(damage.get("lost") or 0)
    if lost:
        costs.append({"kind": "vitality", "value": lost, "target": actor_id})
    effects = []
    for name in detail.get("conditions") or []:
        effects.append({"kind": "condition", "target": actor_id, "add": str(name)})
    for item in perf.get("granted") or []:
        effects.append({"kind": "reward", "target": actor_id,
                        "add": item.get("name", item) if isinstance(item, dict)
                        else item})
    roll = perf.get("roll") if isinstance(perf.get("roll"), dict) else None
    rule_ref = {"version": ACTION_RULES_VERSION,
                "df": int((entry or {}).get("df") or 0),
                "roll_ref": _action_roll_ref(roll)}
    if status == "partial":
        rule_ref["pending_judgement"] = True
    perf_status = str(perf.get("status") or "")
    if perf_status and perf_status != "resolved":
        rule_ref["perf"] = perf_status
    return prism_core.new_result(
        proposal_id=str(proposal.get("proposal_id") or ""),
        actor=proposal.get("actor") or {"kind": "player", "id": ""},
        status=status, costs=costs, effects=effects, time_cost=time_cost,
        interrupt_conditions=list(proposal.get("interrupt_conditions") or []),
        rule_ref=rule_ref,
    )


def _action_event_target(proposal):
    """事件封套的 `target`（字符串或 `None`）：取提案的实体引用 id。"""
    target = (proposal.get("intent") or {}).get("target")
    entity_id = str((target or {}).get("id") or "") if isinstance(target, dict) else ""
    return entity_id or None


def _action_apply_commit(session, result, commit, new_rules) -> None:
    """把状态层的 `CommitResult` 填进结果（§5.1 `event_ids` / `commit_ref`）。

    - `committed`：事件已落盘 → 本局 `rules` 换成**提交后**的快照；
    - `replayed`：状态层**持久**幂等索引命中了同一把键（本进程的临时层没
      记住，例如重启后）——不写新事件、不改状态，只回填首次提交的引用
      （§7.7：不重复掷骰 / 扣费 / 发奖）；
    - 其余（`conflict` / `rejected`）：按 §9.5 的固定短语抛出。
    """
    commit = commit if isinstance(commit, dict) else {}
    status = str(commit.get("status") or "")
    if status not in ("committed", "replayed"):
        code = str(commit.get("code") or "")
        message = str(commit.get("message") or "")
        if status == "conflict" and code == "幂等键冲突":
            # 同一把键 + 不同载荷：本进程的临时幂等层没记住（重启），但该
            # 提案**确实**已经提交过——按提案级幂等同口径拒绝，防重复副作用。
            raise _ActionError(ACTION_ALREADY_COMMITTED, 409)
        if status == "conflict":
            raise _ActionError(ACTION_STALE_SEQ, 409)
        raise _ActionError(
            "服务器内部错误：状态层拒绝提交（%s）" % (message or code), 500)
    if status == "committed":
        session.rules = copy.deepcopy(new_rules)
    result["event_ids"] = list(commit.get("event_ids") or [])
    result["commit_ref"] = {
        "commit_id": commit.get("commit_id"),
        "seq_from": commit.get("seq_from"),
        "seq_to": commit.get("seq_to"),
        "state_digest": commit.get("state_digest"),
    }
    result["committed"] = True


def _action_snapshot(rules, guide) -> dict:
    """提交点冻结快照（§7.5）：在席时用 `prism_guide.commit_snapshot` 的值快照。"""
    if prism_guide is not None:
        return prism_guide.commit_snapshot(rules, guide)
    return {"rules": copy.deepcopy(rules), "guide": copy.deepcopy(guide)}


def _action_settle_commit(session, proposal, *, base_seq, idempotency_key) -> dict:
    """第 3–5 段 + 提交点冻结快照（§7 / §8.2 / §10.1），全程持**单局锁**。

    「结算 → 提交 → 取提交后快照」是设计里那一小段临界区（§7.4 锁边界）；
    模型调用不在其中——叙事段拿到的是值快照，在锁外跑。任何状态行之前的
    失败都以 `_ActionError` 抛出。**已提交的结果不回滚**（§7.7）：落盘失败
    只把 `saved` 置假，由流末的 `done` 如实回报。

    长行动（§4.3 关键约定）在**结算之前**就分叉：恰好一次 `enqueue`，返回
    `scheduled` 回执，**不结算、不提交规则**——到期由推进入口结算
    （`_chron_advance`）。即时行动走下面的同步路径，逐字节不变。

    返回 `{"result":…, "snapshot":…, "saved": bool}`；长行动另加
    `"scheduled": {…}`（`result` / `snapshot` 为 `None`）。
    """
    with session.lock:
        handle = session._handle or session._open_handle()   # 开句柄在锁内：并发不重开
        current = _int_or(handle.view().get("seq"), 0)
        if base_seq != current:
            # §7.4：`base_seq` 过期 = 并发 / 传输层拒绝——**不结算、不落事件**。
            raise _ActionError(ACTION_STALE_SEQ, 409)
        action_id = str((proposal.get("intent") or {}).get("verb") or "")
        entry = _action_scene_entry(session.rules, action_id)
        if entry is None:
            raise _ActionError(ACTION_NO_ACTION, 404)
        # §4.3 关键约定：长行动（`can_defer` / 非空中断条件）**不**同步结算——
        # 交给 CHRON 队列。钩子在这里被咨商**一次**（`draft = None`：结算尚未发生）；
        # 它回 `sync`（替换过的实现 / 没有绑定本局）时按 M8 同步路径继续。
        deferred = None
        if _action_is_deferred(proposal):
            deferred = _schedule_hook(session).enqueue(proposal, None,
                                                       base_seq=base_seq)
            deferred = deferred if isinstance(deferred, dict) else {}
            if deferred.get("mode") == "async":
                return {"result": None, "snapshot": None, "saved": True,
                        "scheduled": _action_scheduled_payload(
                            session, proposal, deferred, handle=handle)}
        # 4. 结算：PRISM 纯规则，一行模型都不调（§7.1）
        live = prism_core.RuleSession.from_snapshot(session.rules)
        try:
            perf = prism_core.perform_action(live, action_id)
        except prism_core.CombatLocked:
            raise _ActionError(ACTION_COMBAT, 409)
        except ValueError:
            raise _ActionError(ACTION_NO_ACTION, 404)
        status = prism_core.action_status(perf)
        new_rules = live.snapshot()
        result = _action_result(proposal, perf, status, entry)
        errors = prism_core.validate_result(result)
        if errors:
            # 结果契约不过关就**不进提交**：状态层一旦落事件就不可回滚（§7.7）。
            raise _ActionError(
                "服务器内部错误：结果契约不成立（%s）" % "；".join(errors), 500)
        # §8.2：结算之后、**提交之前**，排程钩子恰好一次
        # （长行动已在上面咨商过，不重复调用——一次提案只 `enqueue` 一次）
        if deferred is None:
            _schedule_hook(session).enqueue(proposal, result, base_seq=base_seq)
        # 5. 提交：状态层是唯一能改世界事实的出口（§10.1）——一次提交 = 一条事件
        change = {"block": "rules", "op": "replace_block", "path": "",
                  "before": copy.deepcopy(session.rules),
                  "after": copy.deepcopy(new_rules)}
        commit = handle.commit(
            base_seq=base_seq, idempotency_key=idempotency_key,
            type=ACTION_EVENT_TYPE,
            actor=str((proposal.get("actor") or {}).get("id") or ""),
            target=_action_event_target(proposal), changes=[change],
            rules_version=ACTION_RULES_VERSION,
            roll_refs=[result["rule_ref"]["roll_ref"]]
            if result["rule_ref"].get("roll_ref") else [],
            source="player")
        # §5.5：**提交成功之后**扇出（M10-S2）——推送只是交付，不影响提交。
        _room_publish_after_commit(handle, commit, session.sid)
        _action_apply_commit(session, result, commit, new_rules)
        # 6. 的输入：**提交点**的冻结快照（§7.5）——叙事只读这份副本
        snapshot = _action_snapshot(session.rules, session.guide)
        act_text = _turn_act_text(session, "", action_id)
        if act_text:
            session.add_log("act", act_text)
        saved = True
        try:
            session.save()
        except SaveError:
            saved = False
    return {"result": result, "snapshot": snapshot, "saved": saved}


# ── 第 6 段（叙事，可选后置）与进程内短期记忆 ─────────────────────────────


def _action_scheduled_payload(session, proposal, hook_out, *, handle) -> dict:
    """长行动的回合回执（`scheduled`）：L1 的六个键 + 客户端用得上的身份与基线。

    §9.3 的 `result` / `narration` 属于**已结算**的行动，长行动这一回合还**没有**
    结果——回执以 `status = "scheduled"` 标记「已排入时间线」，`task_id` 是后续
    推进入口与取消入口的凭据（§4.3 的 `task_id` 即 CHRON 的 `action_id`）。
    `base_seq` 是**排程事件提交后**的新序号（读自状态层，不是客户端带来的旧值）：
    排程本身也消耗一个事件号，客户端下一次 `commit` 的乐观基线上移（§8.3）。
    """
    return {
        "status": "scheduled",
        "mode": "async",
        "task_id": str(hook_out.get("task_id") or ""),
        "action_id": str((proposal.get("intent") or {}).get("verb") or ""),
        "proposal_id": str(proposal.get("proposal_id") or ""),
        "start_at": _int_or(hook_out.get("start_at"), ACTION_EPOCH_MINUTE),
        "expected_end_at": _int_or(hook_out.get("expected_end_at"),
                                   ACTION_EPOCH_MINUTE),
        "barrier": bool(hook_out.get("barrier")),
        "resume_token": hook_out.get("resume_token"),
        "world_minute": _chron_world_minute(session),
        "base_seq": _int_or(handle.view().get("seq"), 0),
    }


def _action_seq_to(result):
    """结果里的提交版本（§7.5 `commit_ref.seq_to`）：叙事的归位基线。"""
    ref = result.get("commit_ref")
    return ref.get("seq_to") if isinstance(ref, dict) else None


def _action_is_scheduled(payload) -> bool:
    """这份幂等记录是**长行动的回执**（`scheduled`）而不是结算结果吗？

    判据是 `task_id`：结果契约（§5.1）里没有这个键，而 `scheduled` 回执一定有
    （§4.3 的 `task_id`）。同键重放据此回同一形状，不把排程回执塞进结果帧。
    """
    return isinstance(payload, dict) and bool(payload.get("task_id"))


def _action_narrate(session, snapshot, result, text) -> dict:
    """第 6 段（§7.2 / §7.3）：叙事只读**提交点冻结快照**，不改规则事实。

    返回 `{"event": "narration"|"fallback", "payload": {…}, "usage": {…}}`。
    `payload` 一定带 `action_id` 与 `seq_to`（§9.3：客户端据此把叙事挂回它
    所属的行动、按提交版本归位）。失败 / 离线一律走 `fallback`（§7.3）：
    `result` 已发、已提交，叙事绝不回滚它。
    """
    bind = {"action_id": result.get("action_id"), "seq_to": _action_seq_to(result)}
    if prism_guide is None or not _action_guide_available():
        return {"event": "fallback",
                "payload": {"text": FALLBACK_NARRATION,
                            "status": ACTION_TYPE_OFFLINE, **bind},
                "usage": {}}
    completion = None
    try:
        env = prism_guide.load_env()
        messages = prism_guide.build_commit_messages(snapshot, text, result)
        completion = prism_guide.call_narrative(messages, env=env)
    except Exception:      # noqa: BLE001 — 上游失败不改整轮的已提交结果
        completion = None
    reviewed = None
    if completion is not None:
        try:
            reviewed = prism_guide.review_narration(result, completion)
        except Exception:   # noqa: BLE001
            reviewed = None
    if reviewed is None:
        return {"event": "fallback",
                "payload": {"text": _guide_fallback_text(result), **bind},
                "usage": {}}
    try:
        redacted = prism_guide.redact_narration(reviewed, snapshot.get("guide"),
                                                player_text=text,
                                                rules=snapshot.get("rules"))
    except Exception:       # noqa: BLE001 — 秘密门失败退回未经涂改的整段
        redacted = reviewed
    return {"event": "narration",
            "payload": {"text": redacted,
                        "beats": _guide_beats(redacted, snapshot.get("guide")),
                        **bind},
            "usage": (completion or {}).get("usage") or {}}


def _action_guide_available() -> bool:
    """叙事段可不可用（§5.3）：要 BASE_URL / MODEL / API_KEY 都非空。"""
    try:
        return bool(prism_guide.status().get("chat"))
    except Exception:       # noqa: BLE001 — 环境读不出来按离线处理
        return False


def _action_store_narration(session, event, payload) -> bool:
    """把叙事段写进会话（`narrate` 日志 + L3 transcript），返回是否落盘。

    与旧 `turn` 路径同一口径：日志记的是**涂掉之后**的字（秘密门对日志同样
    生效）；兜底句也记（玩家实际看到的就是它）。落盘失败不回滚已提交的结果
    （§7.7），由流末的 `done` 如实回报。
    """
    with session.lock:
        if event == "narration":
            transcript = session.guide.get("transcript")
            if not isinstance(transcript, list):
                transcript = session.guide["transcript"] = []
            transcript.append({"role": "assistant",
                               "content": str(payload.get("text") or "")})
            session.guide["last_beats"] = payload.get("beats") or []
            stats = session.guide.get("stats")
            if isinstance(stats, dict):
                stats["calls"] = int(stats.get("calls") or 0) + 1
        session.add_log("narrate", str(payload.get("text") or ""))
        try:
            session.save()
        except SaveError:
            return False
    return True


def _action_base_seq(session) -> int:
    """当前状态层的 `seq`（§12.1）——提案 / 提交的乐观并发基线。

    句柄没打开时**就地打开**：新档建创世检查点、老档按 §10 惰性迁移，下一句
    读到的才是真实序号（迁移会补一条事件，`seq` 从 0 变 1——猜一个「大概是 0」
    会让第一次 commit 直接吃 409）。本函数只在 `propose` 里调用（POST）；
    只读路由（catalog）不为读一个数碰磁盘。
    """
    handle = session._handle or session._open_handle()
    return _int_or(handle.view().get("seq"), 0)


def _action_costs_preview(proposal) -> list:
    """`costs_preview`（§9.2 / §6.4）：只列**已知**的成本与耗时。

    资源 / 状态代价要等结算（M8 不预支规则判定），所以这里只有时间成本。
    """
    time_cost = proposal.get("time_cost")
    time_cost = time_cost if isinstance(time_cost, dict) else {}
    value = int(time_cost.get("value") or 0)
    return ([{"kind": "time", "value": value,
              "unit": str(time_cost.get("unit") or "minute")}]
            if value > 0 else [])


def _action_propose_payload(session, proposal) -> dict:
    """提案回执（§9.2）：提案 + 风险级 + 是否要确认 + 成本预览 + 并发基线。"""
    errors = prism_core.validate_proposal(proposal)
    if errors:
        raise _ActionError(
            "服务器内部错误：提案契约不成立（%s）" % "；".join(errors), 500)
    risk = str(proposal.get("risk") or "")
    with session.lock:
        # 基线要在同一把锁里读（句柄可能还没打开，开它要碰磁盘 / 迁移老档）。
        base_seq = _action_base_seq(session)
    return {"status": "ok", "type": ACTION_TYPE_PROPOSAL,
            "proposal": proposal, "needs_confirm": _action_is_confirm_risk(risk),
            "risk": risk, "costs_preview": _action_costs_preview(proposal),
            "base_seq": base_seq}


def _action_clarify_put(sid, clarify, text) -> str:
    """记下一次澄清（§6.3）：回答时要拿它的原文并入草案。返回 `clarify_id`。"""
    if not isinstance(clarify, dict):
        return ""
    clarify_id = str(clarify.get("clarify_id") or "")
    if not clarify_id:
        return ""
    now = _action_now_ms()
    with _ACTION_LOCK:
        _action_prune(now)
        _action_clarifies[(str(sid), clarify_id)] = {
            "clarify": copy.deepcopy(clarify), "text": str(text or ""), "ts": now}
    return clarify_id


def _action_clarify_take(sid, clarify_id) -> dict:
    """取一次未过期的澄清；过期 / 没有过按「不存在」处理（404 `没有这个提案`）。"""
    now = _action_now_ms()
    key = (str(sid), str(clarify_id))
    with _ACTION_LOCK:
        record = _action_clarifies.get(key)
        if record is None:
            return None
        if now - int(record.get("ts") or 0) > ACTION_CLARIFY_TTL_S * 1000:
            _action_clarifies.pop(key, None)
            return None
        return copy.deepcopy(record)


def _action_confirm_issue(sid, proposal_id) -> dict:
    """发一枚确认令牌（§6.4）：同一提案重复确认复用未过期的那一枚。"""
    now = _action_now_ms()
    key = (str(sid), str(proposal_id))
    with _ACTION_LOCK:
        _action_prune(now)
        record = _action_confirms.get(key)
        if record is not None:
            return copy.deepcopy(record)
        record = {"token": "cf_" + secrets.token_hex(16),
                  "expires_at": now + int(ACTION_CONFIRM_TTL_S * 1000)}
        _action_confirms[key] = dict(record)
        return dict(record)


def _action_confirm_check(sid, proposal_id, token) -> bool:
    """校验确认令牌：缺 / 错 / 过期一律不通过（§9.5 → 409 `需要确认`）。"""
    now = _action_now_ms()
    key = (str(sid), str(proposal_id))
    with _ACTION_LOCK:
        record = _action_confirms.get(key)
        if record is None or now > int(record.get("expires_at") or 0):
            _action_confirms.pop(key, None)
            return False
        return bool(token) and str(record.get("token") or "") == str(token)


def _action_confirm_cancel(sid, proposal_id) -> None:
    """取消一次待确认（§6.4）：丢掉令牌——「取消」就是不提交。"""
    with _ACTION_LOCK:
        _action_confirms.pop((str(sid), str(proposal_id)), None)


def _action_idem_get(sid, key):
    """幂等查表（§7.7）：**任何结算之前**调用；命中即返回首次结果。"""
    now = _action_now_ms()
    record_key = (str(sid), str(key))
    with _ACTION_LOCK:
        record = _action_idem.get(record_key)
        if record is None:
            return None
        if now - int(record.get("ts") or 0) > ACTION_IDEM_TTL_S * 1000:
            _action_idem.pop(record_key, None)
            return None
        return copy.deepcopy(record.get("payload"))


def _action_idem_put(sid, key, payload) -> None:
    """记下首次结果（§9.4）：同会话 + 同键的重复 `commit` 回这一份。"""
    now = _action_now_ms()
    with _ACTION_LOCK:
        _action_prune(now)
        _action_idem[(str(sid), str(key))] = {
            "payload": copy.deepcopy(payload), "ts": now}


def _action_is_committed(sid, proposal_id) -> bool:
    """同一 `proposal_id` 是否已提交过（§9.4 提案级幂等：防「换个键再赌一次」）。"""
    now = _action_now_ms()
    with _ACTION_LOCK:
        record = _action_committed.get((str(sid), str(proposal_id)))
    return bool(record) and now - int(record.get("ts") or 0) \
        <= ACTION_IDEM_TTL_S * 1000


def _action_mark_committed(sid, proposal_id) -> None:
    """记下已提交的 `proposal_id`（与首次结果同一生命周期）。"""
    now = _action_now_ms()
    with _ACTION_LOCK:
        _action_committed[(str(sid), str(proposal_id))] = {"ts": now}


# ── CHRON 排程闭环（M9-C6，CHRON-DESIGN §4.3 / §5 / §6 / §8）──────────────
#
# 这一节是 **L2 的驱动层**：把 `chron.py` 的纯函数接到本局的**状态层句柄**上，
# 并用 §8.3 的幂等键口径提交事件（C5 的 `commit_event` 同款两道闸）。分工：
#
#   L1（`ScheduleHook`，ACTION §8.2）→ `_ChronScheduleHook` → `_chron_schedule`
#   L2（`chron.py`）                 → `schedule` / `advance_to` / `next_barrier`
#                                       + 取消（`settle(..., "cancelled")`）
#   网页层推进入口                    → `POST /api/chron/advance`
#
# **结算发生在推进里**：`chron.advance_to` 只到「该结算了」（`action.due`，B8），
# 判胜负仍走 PRISM（§8.4：与普通行动同一条管线，只是由 M9 触发），提交把
# 「规则后果 + 行动卡新状态」放进**同一条事件**（A0：一次提交 = 一条事件，
# 多项变更加进 `changes[]`，ACTION §7.4）。


def _chron_restore(session, handle) -> dict:
    """§8.2 的恢复时序（C5）：由状态层句柄恢复出 CHRON 块 + 调度器状态。

    排程 / 推进 / 取消都从这一步开始——**不**在会话门面上缓存 CHRON 状态，
    权威始终是状态层里的 `chron` 块（重启后照常恢复，§8.2）。
    """
    return chron.restore(handle)


def _chron_commit(handle, event, *, block, state, base_seq, source=None,
                  extra_changes=(), rules_version=ACTION_RULES_VERSION,
                  roll_refs=()) -> dict:
    """把一条 CHRON 事件交给状态层提交（§8.3），返回 `{status, result, block, seq}`。

    **只有** `extra_changes` 非空的结算事件才走本地镜像；其余（排程 / 取消 /
    纯压缩的 `clock.advance`）逐字委托 C5 的 `chron.commit_event`——两边同为
    「幂等两道闸 + 块回写 + 预测序号校正」。

    镜像的那一条与 `commit_event` 口径一致，差别只有一处：结算**同时**带着
    别的块的变更（例如这次的规则快照）——一次提交承载多项变更（A0），因此
    `changes = 块差异 + extra_changes`，块的搬运 / 幂等表 / `seq` 回写同款。
    """
    if not extra_changes:
        out = chron.commit_event(handle, event, block=block, state=state,
                                 base_seq=base_seq, source=source,
                                 rules_version=rules_version,
                                 roll_refs=list(roll_refs or ()))
        # §5.5：排程 / 取消 / 纯时钟推进也是房间事件（M10-S2）；`room_id` 缺省
        # 取句柄的 `campaign_id`——M-11 保证它 == 房间号（`Session.sid`）。
        _room_publish_after_commit(handle, out["result"])
        return {"status": out["status"], "result": out["result"],
                "block": out["block"], "seq": out["seq"]}
    key = str(event.get("idempotency_key") or "")
    base = chron.migrate_block(block)
    if chron.is_committed(base, key):          # 第一道闸：CHRON 侧（同键 no-op）
        return {"status": "skipped", "result": None, "block": base,
                "seq": base["seq"]}
    predicted = base_seq + 1
    after = chron.block_from_scheduler(state, seq=predicted,
                                       folded=base.get(chron.CHRON_FOLDED_NAME))
    keys = dict(after["idempotency"])
    keys.update(base["idempotency"])
    keys[key] = predicted
    after["idempotency"] = keys
    result = handle.commit(
        base_seq=base_seq, idempotency_key=key, type=event["type"],
        actor=event["actor"], target=event.get("target"),
        cause=event.get("cause"),
        changes=chron.block_changes(base, after) + [dict(c) for c in extra_changes],
        rules_version=rules_version, roll_refs=list(roll_refs or ()),
        world_time=event.get("world_time"),
        source=source or event.get("source") or "system")
    status = str((result or {}).get("status") or "")
    if status in ("committed", "replayed"):     # 第二道闸由状态层自己把关
        _room_publish_after_commit(handle, result)      # §5.5：提交后扇出（M10-S2）
        updated = chron.migrate_block(after)
        seq_to = result.get("seq_to")
        if isinstance(seq_to, int) and not isinstance(seq_to, bool):
            updated["seq"] = seq_to
        updated = chron.mark_committed(updated, key, updated["seq"])
    else:
        updated = base                          # 没写事件 → 块回到事件前
    return {"status": status, "result": result, "block": updated,
            "seq": updated["seq"]}


def _chron_require_commit(out) -> dict:
    """排程 / 取消这类**必须先落**的 CHRON 提交：没落就抛 500（不谎报成功）。

    与 A3 同步路径的 `_action_apply_commit` 同一口径：`conflict` /
    `rejected` 一律不当作成功——客户端拿到的回执必须对应一条**已提交**的事件。
    """
    if str((out or {}).get("status") or "") not in ("committed", "replayed"):
        result = (out or {}).get("result")
        message = str((result or {}).get("message") or "") \
            or str((out or {}).get("status") or "")
        raise _ActionError(
            "服务器内部错误：状态层拒绝提交（%s）" % (message or "未知原因"), 500)
    return out


def _chron_schedule(session, proposal, *, base_seq) -> dict:
    """L1 `enqueue` 的落地：L2 `schedule` + 提交排程事件（§4.3 映射表）。

    返回 §8.2 的**六个键**：`mode = "async"`、`task_id` = 行动 id、
    `start_at` / `expected_end_at` = 世界分钟、`barrier` = 命中屏障与否
    （`barriers_at`，§6.2）、`resume_token = None`（CHRON 当前不消费，§4.3）。

    依赖有环 → `chron.schedule` 的 `rejected` 语义（§5.3）：**不**入队、不留卡，
    翻成 409 `现在做不了`（前置条件不满足，§9.5）；依赖 id 不存在 / 自依赖 /
    提交顺序类问题抛 `ScheduleError`，同样翻 409——两类都**不**落任何事件。
    """
    handle = session._handle or session._open_handle()
    restored = _chron_restore(session, handle)
    state = restored["state"]
    now = state["world_minute"]
    card = _chron_card(proposal)
    try:
        out = chron.schedule(state, card, world_minute=now, start_minute=now)
    except chron.ScheduleError as exc:
        raise _ActionError(ACTION_NOT_NOW, 409) from exc
    if out["status"] == "rejected":                 # §5.3：依赖有环
        raise _ActionError(ACTION_NOT_NOW, 409)
    _chron_require_commit(_chron_commit(
        handle, out["events"][-1], block=restored["block"], state=out["state"],
        base_seq=base_seq, source="player"))
    # 排期后的卡在**新状态**里（`chron.schedule` 不改写入参草稿）：start / due
    # 从它读，别从草稿读（草稿的 `start_minute` 恒为 `None`）。
    card = out["state"]["actions"][card["action_id"]]
    start = card["start_minute"]
    return {"mode": "async", "task_id": card["action_id"],
            "start_at": int(start), "expected_end_at": int(card["due_minute"]),
            "barrier": bool(chron.barriers_at(out["state"], start)),
            "resume_token": None}


def _chron_cancel(session, task_id) -> bool:
    """L1 `cancel(task_id)` → L2 的取消入口（§4.3 映射表末行）。

    C2 / C3 合并后的 L2 把「取消」表达为 `settle(state, action_id,
    "cancelled", …)`（§4.2 全转换表的 `→ cancelled` 行），**没有**单独的
    `cancel(action_id, at_minute)`；映射表的语义不变：`task_id` 即 `action_id`，
    返回是否成功取消。取消只动行动卡（§4.4 的返还 / 风险口径归规则包，
    C6 不实现返还——**不臆造**退款语义）。

    **不改盘**：未迁移老档上没有 CHRON 状态、也没有可取消的行动——直接回
    `False`，**不**开句柄（开 = 落盘迁移，§10.1）。
    """
    if _chron_legacy_unmigrated(session):
        return False
    with session.lock:
        handle = session._handle or session._open_handle()
        restored = _chron_restore(session, handle)
        state = restored["state"]
        card = state["actions"].get(str(task_id or ""))
        if card is None or card["status"] in chron.ACTION_TERMINAL_STATUSES:
            return False
        out = chron.settle(state, card["action_id"], "cancelled",
                           world_minute=state["world_minute"],
                           reason="player_cancel")
        event = out["events"][card["action_id"]][0]
        _chron_require_commit(_chron_commit(
            handle, event, block=restored["block"], state=out["state"],
            base_seq=restored["seq"], source="player"))
        return True


def _chron_settle_due(session, handle, state, block, event, *, seq) -> dict:
    """一个到期点（`action.due`，B8）的结算：PRISM → `chron.settle` → 一次提交。

    返回 `{"state", "block", "seq", "report"}`：状态 / 块 / 序号都要往下传
    （同一次推进可能连着结几张卡）。`report` 是给推进入口的回执条目：

    - 结算成功 → `{action_id, status, result, narration?}`；
    - 行动已不在当前场景行动表 → 按 §4.2 的 `failed` 收尾（`reason =
      action_unavailable`，只动行动卡，**不**产出结果）；
    - 战斗锁 / 未知行动 → 卡留在 `running`，回执 `status = "pending"`：
      这一轮**不**结算（下一次推进仍会到它）；
    - 结果契约不过关（内部不变量） → 同样留待下一轮，回执写明原因。
    """
    action_id = str(event.get("target") or "")
    card = state["actions"].get(action_id)
    minute = chron.world_time_minute(event.get("world_time"))

    def stuck(mark: str, reason: str) -> dict:
        """这一轮不动它：状态 / 块 / 序号原样带回，只如实回报原因。"""
        return {"state": state, "block": block, "seq": seq,
                "report": {"action_id": action_id, "status": mark,
                           "reason": reason}}

    if card is None or minute is None:
        return stuck("error", "行动卡缺失")
    entry = _action_scene_entry(session.rules, card.get("kind"))
    if entry is None:
        # 场景行动表里已经没有这个名字（规则包换了行动表）：按 §4.2 的
        # `failed` 收尾——只动行动卡，**不**产出结果（没有判定依据）。
        try:
            out = chron.settle(state, action_id, "failed", world_minute=minute,
                               reason="action_unavailable")
        except chron.ScheduleError:
            return stuck("error", "无法结算")
        settle_event = out["events"][action_id][0]
        commit = _chron_commit(handle, settle_event, block=block,
                               state=out["state"], base_seq=seq, source="player")
        return {"state": out["state"], "block": commit["block"],
                "seq": commit["seq"],
                "report": {"action_id": action_id, "status": "failed",
                           "reason": "action_unavailable"}}
    live = prism_core.RuleSession.from_snapshot(session.rules)
    try:
        perf = prism_core.perform_action(live, card["kind"])
    except prism_core.CombatLocked:
        return stuck("pending", ACTION_COMBAT)
    except ValueError:
        return stuck("pending", ACTION_NO_ACTION)
    status = prism_core.action_status(perf)
    new_rules = live.snapshot()
    proposal = _chron_proposal_from_card(card, session)
    result = _action_result(proposal, perf, status, entry)
    errors = prism_core.validate_result(result)
    if errors:
        # 结果契约不过关就**不进提交**（§7.7 同口径）：卡留在 running，
        # 由回执如实回报，绝不把坏结果写进事实。
        return stuck("error", "结果契约不成立")
    to = "completed" if status in ("success", "partial") else "failed"
    try:
        settled = chron.settle(state, action_id, to, world_minute=minute)
    except chron.ScheduleError:
        return stuck("error", "无法结算")
    settle_event = settled["events"][action_id][0]
    roll_ref = str((result.get("rule_ref") or {}).get("roll_ref") or "")
    change = {"block": "rules", "op": "replace_block", "path": "",
              "before": copy.deepcopy(session.rules),
              "after": copy.deepcopy(new_rules)}
    commit = _chron_commit(handle, settle_event, block=block,
                           state=settled["state"], base_seq=seq, source="player",
                           extra_changes=[change],
                           roll_refs=[roll_ref] if roll_ref else [])
    if commit["status"] == "skipped":           # CHRON 侧同键 no-op：事实已落
        result["committed"] = True
    else:                                       # committed / replayed：同一路径回填
        _action_apply_commit(session, result, commit["result"], new_rules)
    committed_ids = (commit["result"] or {}).get("event_ids") \
        if isinstance(commit["result"], dict) else None
    record = {"event_id": (committed_ids or [settle_event["event_id"]])[0],
              "type": settle_event["type"],
              "world_time": settle_event["world_time"], "seq": commit["seq"]}
    return {"state": settled["state"], "block": commit["block"],
            "seq": commit["seq"], "event": record,
            "report": {"action_id": action_id, "status": status,
                       "result": result}}


def _chron_advance(session, target_minute, *, offline_advance=False) -> dict:
    """网页层推进入口（L2 `advance_to`）：推进世界时间并**在到期点结算**。

    返回 `{world_minute, target, stopped_at, barriers, next_barrier, settled,
    events, saved}`：

    - `advance_to` 的停点由 §6.2 的屏障判据决定（B1–B8；停点不跨屏障）；
    - 途中每个 `action.due`（B8）**逐条**结算（§6.3 硬约束：不批量绕过校验）；
    - 结算事件各自带 §8.3 的确定的幂等键（`<action_id>:e<序号>:<类型>`）；
    - 推进到目标而没有到期时提交一条 `clock.advance`（§8.2 第 2 步的单调校验
      据此恒成立：块时钟不得超前于最后一条已提交事件的世界时间）；
    - 叙事只读**提交点冻结快照**并写进会话日志（§7.5 / §9.3 的 `action_id`
      与 `seq_to` 同样带着走）；离线时是 `fallback` + 既有兜底句（§7.3 / P4）。
    """
    target = target_minute
    if isinstance(target, bool) or not isinstance(target, int) or target < 0:
        raise _ActionError(CHRON_BAD_TARGET, 400)
    with session.lock:
        handle = session._handle or session._open_handle()
        restored = _chron_restore(session, handle)
        state, block, seq = restored["state"], restored["block"], restored["seq"]
        try:
            out = chron.advance_to(state, target,
                                   offline_advance=bool(offline_advance))
        except chron.ScheduleError:
            # §3.2：世界时间只增不减——回拨一律是调用方的错，**不**静默夹住。
            raise _ActionError(CHRON_TIME_BACKWARD, 400)
        state = out["state"]
        settled = []
        events = []
        for event in out["events"]:
            if str(event.get("type") or "") != chron.ACTION_DUE_EVENT:
                continue
            step = _chron_settle_due(session, handle, state, block, event,
                                     seq=seq)
            state, block, seq = step["state"], step["block"], step["seq"]
            settled.append(step["report"])
            if step.get("event"):
                events.append(step["event"])
        # 世界时间前进过（纯压缩 / 停在屏障）就留一条 `clock.advance`（§3.3）：
        # 否则下一次 `restore` 的单调校验会看到「块时钟超前于最后一条事件」。
        if chron.world_time_minute(handle.view().get("world_time")) != \
                state["world_minute"]:
            clock = chron.advance_event(state["world_minute"])
            commit = _chron_commit(handle, clock, block=block, state=state,
                                   base_seq=seq, source="system")
            block, seq = commit["block"], commit["seq"]
            events.append({"event_id": clock["event_id"], "type": clock["type"],
                           "world_time": clock["world_time"]})
        # 第 6 段（叙事）在**锁外**跑（§7.4：模型调用不持锁）；先取冻结快照
        # （提交点的那一份，§7.5）与要叙事的行动清单。
        frozen = []
        for report in settled:
            result = report.get("result")
            if isinstance(result, dict):
                frozen.append((report, result, _action_snapshot(session.rules,
                                                                session.guide)))
        saved = True
        try:
            session.save()
        except SaveError:
            saved = False                        # 已提交的结果不回滚（§7.7）
        minute = state["world_minute"]
        next_barrier = chron.next_barrier(state)
    for report, result, snapshot in frozen:
        narration = _action_narrate(session, snapshot, result, "")
        report["narration"] = {
            "event": narration["event"],
            "text": str(narration["payload"].get("text") or ""),
            "action_id": result.get("action_id"),
            "seq_to": _action_seq_to(result),
        }
        # `narrate` 日志 + L3 transcript（与同步路径同一口径）；内部各自 save()，
        # 失败只置假，不回滚已提交的结算（§7.7）。
        if not _action_store_narration(session, narration["event"],
                                       narration["payload"]):
            saved = False
    return {"status": "ok", "world_minute": minute, "target": target,
            "stopped_at": out["stopped_at"], "barriers": out["barriers"],
            "next_barrier": next_barrier, "settled": settled, "events": events,
            "saved": saved}


def _chron_legacy_unmigrated(session) -> bool:
    """这一局是不是**未迁移的老档**（读 CHRON 前先看这个）？

    老档只有一份单文件 `<sid>.json`、还没有状态层句柄——此时「开句柄」=
    `state.migrate_legacy(...)` **就地落盘迁移**（§10.1：迁移只发生在第一次
    save）。只读 / 无操作的 CHRON 入口因此**不得**借读一个数把老档迁了
    （M7-3「只读加载不改写磁盘」、M7-4 `state_view` 的明确规避同款）。
    新档（已迁移布局）不受影响：开句柄只是纯读，照常走。
    """
    return getattr(session, "_handle", None) is None \
        and getattr(session, "_legacy", None) is not None


def _chron_next_barrier(session, from_minute=None) -> dict:
    """只读：下一个必须停下的分钟（L2 `next_barrier`，§6.2 / §6.4）。

    `from_minute` 省略 = 当前世界分钟；早于当前 → 400（世界时间只增不减）。
    **不改盘**：未迁移老档上不开句柄（开 = 落盘迁移），直接回「没有屏障」。
    """
    if _chron_legacy_unmigrated(session):
        # 老档还没有 CHRON 块（迁移时才补空块，§8.1）→ 没有屏障可看；也**不**
        # 为读一个数把它迁了（§10.1 / M7-4 口径）。`from` 的入参校验在路由层。
        return {"status": "ok", "world_minute": ACTION_EPOCH_MINUTE,
                "next_barrier": None}
    with session.lock:
        handle = session._handle or session._open_handle()
        state = _chron_restore(session, handle)["state"]
        try:
            minute = chron.next_barrier(state, from_minute)
        except chron.ScheduleError as exc:
            raise _ActionError(CHRON_TIME_BACKWARD, 400) from exc
        return {"status": "ok", "world_minute": state["world_minute"],
                "next_barrier": minute}


def _wants_json(accept) -> bool:
    """`Accept: application/json`（且没点名 SSE）时回**非流式**结果（§9.3）。"""
    text = str(accept or "").lower()
    return "application/json" in text and "text/event-stream" not in text


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

    def _sse_frame(self, seq: int, event: str, obj) -> None:
        """写一个带 `id:` 的 SSE 帧（§5.4：`id: <seq>` + `event: <type>` + `data:`）。

        与 `_sse` 只差那行 `id:`——房间流的断线补发靠它：客户端记下最后一个
        `id`（=`seq`），重连时作为 `?since=` 送回。房间流之外的流（导引者回合）
        保持 `_sse` 不动，响应形状逐字节不变。
        """""
        payload = ("id: %d\nevent: %s\ndata: %s\n\n"
                   % (seq, event, json.dumps(obj, ensure_ascii=False)))
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
            if path == "/api/state":
                # 状态层只读摘要（M7-4，STATE §16）：只回存在性，不回块内容 /
                # 元信息；不开未迁移老档的句柄（开句柄 = 落盘迁移）。
                s = self._sess()
                return self._json(state_view(s))
            if path == "/api/events":
                # 状态层事件增量（M7-4）：只回 seq > since；缺省 since = 0。
                # 非法 since → 400 固定短语（不静默当 0）。
                s = self._sess()
                since = _since_param(self.path)
                return self._json(events_view(s, since))
            if path == "/api/action/catalog":
                # 当前可用行动单（§9.1）：只读，不结算也不开状态层句柄。
                return self._action_catalog()
            if path == "/api/chron/next_barrier":
                # 下一个必须停下的分钟（M9-C6，CHRON §6.2）：只读，不推进时间。
                return self._chron_next_barrier_route()
            if path == "/api/campaigns":
                # 战役目录（Issue #222）：世界 → 该世界下的剧本。只读数据面，
                # 不需要会话（新局空态在**还没有会话内容**时也要能列出来）。
                return self._json(campaign_catalog())
            if path == "/api/atlas/exits":
                # 查看当前地点的出口：返回文本出口列表（不是图片，§2）。
                # 只读接口：不 save()，不改写存档。
                s = self._sess()
                return self._json(s.atlas_view())
            if path == "/api/room/stream":
                # 房间事件流（M10-S2，§5.1 / §5.4 / §5.5）：SSE 推送 + 同一路径
                # 的短轮询降级；鉴权 / 补发 / 过滤全在 _room_stream_route 里。
                return self._room_stream_route()
            # TODO(M3)：PRISM 世界 / 剧本等其余只读接口在此追加（路径 if 链）。
            # 其余路径一律按静态资源找；找不到就 404。
            rel = path.lstrip("/")
            if rel:
                return self._static(rel)
            self._err("未知路径", 404)
        except _ActionError as e:
            # 行动管线的固定错误（§9.5）：短语 + 仅状态行之前可用的码。
            self._err(e.phrase, e.code)
        except SaveError:
            # 存档落盘失败（含开句柄 / 老档迁移）：如实回报，不谎报成功。
            self._err("保存失败，请重试", 500)
        except state.StateError:
            # 状态层拒载 / 日志损坏（说明里可能带本机路径）：固定短语，
            # 不回显内部信息（§16 M7-4 的响应边界）。
            self._err("状态读取失败", 500)
        except chron.ChronError:
            # CHRON 拒载（块时钟超前 / 块版本更高，§8.2 / §8.4）与状态层
            # 同一响应边界：固定短语，不回显内部信息。
            self._err("状态读取失败", 500)
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

            if path == "/api/action/propose":
                return self._action_propose(b)

            if path == "/api/action/confirm":
                return self._action_confirm(b)

            if path == "/api/action/commit":
                return self._action_commit(b)

            if path == "/api/chron/advance":
                return self._chron_advance_route(b)

            if path == "/api/chron/cancel":
                return self._chron_cancel_route(b)

            if path == "/api/room/create":
                return self._room_create(b)

            if path == "/api/room/join":
                return self._room_join(b)

            if path == "/api/room/leave":
                return self._room_leave(b)

            if path == "/api/room/kick":
                return self._room_kick(b)

            if path == "/api/room/chat":
                return self._room_chat(b)

            if path == "/api/session/create":
                # 建局（M4-B1，UI-DESIGN §2.1 / §7 缺口 B1）：还没有会话时要能开一局，
                # 所以**不**调 _sess()——本路由不读 X-Session，只回一个新 sid。
                return self._session_create()

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
                    previous_name = s.save_name
                    s.save_name = name
                    try:
                        s.save()
                    except SaveError:
                        # 落盘失败：回滚内存里的展示名，内存态与磁盘一致，
                        # 客户端拿到 500、绝不会显示「改名成功」的假象。
                        s.save_name = previous_name
                        return self._err("保存失败，请重试", 500)
                return self._json({"status": "ok", "sid": sid, "name": name})

            if path == "/api/save/delete":
                sid = str(b.get("sid") or "").strip()
                if not SID_RE.fullmatch(sid):
                    return self._err("存档不存在", 404)
                # 界线：不能删掉当前正在使用的存档（前端用 X-Session 表明自己那局）。
                if sid == (self.headers.get("X-Session") or "").strip():
                    return self._err("不能删除正在进行的存档", 400)
                legacy = SAVE_DIR / f"{sid}.json"
                camp = _campaign_dir(sid)
                if not legacy.is_file() and not camp.is_dir():
                    return self._err("存档不存在", 404)
                with _sessions_lock:
                    _sessions.pop(sid, None)    # 先摘缓存，再删磁盘，避免脏读
                try:
                    legacy.unlink(missing_ok=True)      # 老档（尚未迁移 / 残留）
                    (SAVE_DIR / f".{sid}.tmp").unlink(missing_ok=True)
                    if camp.is_dir():
                        # 新档：整目录一起删（检查点 + `.bak` + 事件日志 + 幂等副本）
                        shutil.rmtree(camp)
                except OSError:
                    return self._err("删除失败", 500)
                return self._json({"status": "ok", "sid": sid})

            if path == "/api/atlas/move":
                # 沿 via 移动队伍：返回行程档 + 名义分钟 + 新位置的出口视图。
                # 方向不存在 / 没有该出口时 atlas_move 抛 ValueError → 400。
                s = self._sess()
                s.ensure_atlas()        # 先编译好地图，位置快照才是真实的起点
                with s.lock:
                    previous_locus = copy.deepcopy(s._locus)
                result = s.atlas_move(b.get("via"))
                try:
                    s.save()        # 位置改变要落盘：重新加载后队伍还在原地
                except SaveError:
                    # 位置没落盘：回滚内存里的队伍位置，内存与磁盘都留在原处，
                    # 客户端拿到 500，不会以为「已经走过去」。
                    with s.lock:
                        s._locus = previous_locus
                    return self._err("保存失败，请重试", 500)
                return self._json(result)

            # TODO(M3)：PRISM 会话 / 结算等写入接口在此追加（路径 if 链）。
            return self._err("未知接口", 404)
        except _ActionError as e:
            # 行动管线的固定错误（§9.5）：短语 + 仅状态行之前可用的码。
            self._err(e.phrase, e.code)
        except chron.ChronError:
            # CHRON 拒载 / 拒绝调度（§8.2 / §8.4）：与状态层同一响应边界。
            self._err("状态读取失败", 500)
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
        `已经绑定` 是 409（§6）。没有 SSE，也没有第二套状态码。绑定写盘失败
        （`SaveError`）回滚 `guide` 后回 500，不会让「已经绑定」在内存里生效。
        """
        sid = (self.headers.get("X-Session") or "").strip()
        session = get_session(sid) if sid else None
        if not session:
            return self._err("会话不存在或已过期", 400)
        if prism_guide is None:
            return self._err(GUIDE_ABSENT_ERROR, 400)
        world_key = str(body.get("world_key") or "").strip()
        scenario_id = str(body.get("scenario_id") or "").strip()
        with session.lock:
            previous_guide = copy.deepcopy(session.guide)
        try:
            bound = prism_guide.bind(session, world_key, scenario_id)
        except ValueError as exc:
            return self._err(str(exc),
                             GUIDE_BIND_ERRORS.get(str(exc), GUIDE_BIND_DEFAULT))
        except SaveError:
            # 绑定没落盘：回滚 guide，客户端拿到 500；后续重试不会因为
            # 内存里残留的绑定而误报「已经绑定」。
            with session.lock:
                session.guide = previous_guide
            return self._err("保存失败，请重试", 500)
        return self._json({"status": "ok", **bound})

    # ── 导引者回合（G2，§5.2 / §5.10）───────────────────────
    def _log_fixed_turn(self, session, text, action_id, narration) -> None:
        """不叫模型、直接回 JSON 的回合落日志（M4-B2 / UI-DESIGN §1.3.5）。

        覆盖三条短路：淡出 / 跳过整句（§5.10）与模块缺失的离线兜底（§5.1）——
        它们这一刻已经把玩家可见的正文定稿，所以记 `act` + `narrate`。这几条
        路径不发状态行、不结算，因此只 `add_log`（`seq` 仍在锁内递增），由随后
        任一次 `save()` 一并持久化——与既有 `ensure_atlas` 说明条目同一口径。
        """
        act_text = _turn_act_text(session, text, action_id)
        if act_text:
            session.add_log("act", act_text)
        session.add_log("narrate", narration)

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

        M4-B2：这一回合的玩家输入（`act`）、导引者叙事（`narrate`，含兜底句）
        与揭开的痕迹（`trace`）写进 `Session.log`（`seq` 沿 `add_log` 锁内递增），
        `GET /api/log?since=` 据此增量取回；叙事落的是**涂掉之后**的字，秘密门
        对日志同样生效，被删内容不会经日志重新可见。日志不改响应结构（SSE 事件
        与顺序不变），落盘沿用本回合既有的 `save()`。

        M4-B5：`narrate` 条目还带上这一回合的 `beats`（切拍 / 朗读入口随历史恢复
        一起回来）；没有拍时不写这个键，条目形状与 B2 一致。
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

        action_id = str(body.get("action_id") or "")
        stripped = text.strip()
        if stripped == GUIDE_FADE_WORD:
            # 淡出 / 跳过是整句相等：**不结算**，也不叫模型（§5.10）。
            # M4-B2：玩家看到的这句仍进会话日志，刷新后叙事流不丢这一段。
            self._log_fixed_turn(session, text, action_id, GUIDE_FADE_TEXT)
            return self._json({"status": "ok",
                               "narration": {"text": GUIDE_FADE_TEXT}})
        if stripped == GUIDE_SKIP_WORD:
            self._log_fixed_turn(session, text, action_id, GUIDE_SKIP_TEXT)
            return self._json({"status": "ok",
                               "narration": {"text": GUIDE_SKIP_TEXT}})

        if prism_guide is None:
            # 模块缺失：状态行之前返回固定 JSON 兜底，不结算（§5.1）。
            # M4-B2 验收：无 AI 配置时回合同样落日志（兜底句照记）。
            self._log_fixed_turn(session, text, action_id, FALLBACK_NARRATION)
            return self._json({"status": "offline",
                               "narration": {"text": FALLBACK_NARRATION}})

        # 焦点的校验在状态行之前（§2.1 / §5.10）：不是已绑定剧本里的地点就是
        # 400，不叫模型。合法时才在下面写 `focus_location_id`。
        location_id = str(body.get("location_id") or "")
        if location_id and not prism_guide.location_ok(session.guide,
                                                       location_id):
            return self._err(GUIDE_NO_LOCATION, 400)

        result = None
        if action_id:
            with session.lock:
                previous_rules = copy.deepcopy(session.rules)
            try:
                result = prism_guide.settle(session, action_id)
            except ValueError as exc:
                phrase, code = GUIDE_SETTLE_ERRORS.get(str(exc),
                                                       GUIDE_SETTLE_DEFAULT)
                return self._err(phrase, code)
            except SaveError:
                # 结算没落盘：回滚规则态到本回合之前，状态行之前用固定 JSON
                # 报失败——绝不让玩家以为「这个行动已经结算成功」。
                with session.lock:
                    session.rules = previous_rules
                return self._err("保存失败，请重试", 500)

        # 变动之前先把焦点与检查点各留一份：G7 的场外痕迹与「痕迹揭开」看的是
        # **离开的地点**（§2.2），不是写完之后的那个新焦点。
        previous_focus = str(session.guide.get("focus_location_id") or "")
        # L2 只在 scene.id 变化时重写（并清空 L3）；焦点变化要立刻落盘——
        # 这一回合即使叙事失败，G6 的下一回合也要看得到队伍走到了哪里。
        with session.lock:
            scene_before = str(session.guide.get("l2_scene_id") or "")
            previous_guide = copy.deepcopy(session.guide) if location_id else None
            prism_guide.ensure_l2(session.guide, session.rules)
            if location_id:
                session.guide["focus_location_id"] = location_id
                try:
                    session.save()
                except SaveError:
                    # 焦点没落盘：回滚 guide（连 ensure_l2 的改写一起），回到
                    # 状态行之前用固定 JSON 报失败。
                    session.guide = previous_guide
                    return self._err("保存失败，请重试", 500)
        current_focus = str(session.guide.get("focus_location_id") or "")
        scene_now = str((session.rules.get("scene") or {}).get("id") or "")
        # 两条触发线（§2.2）：焦点换了一处（只有**离开**的那一处才算），或
        # `rules` 的 `scene.id` 换了（原检查点仍有效，测试可直接改快照触发）。
        # 同一回合两件事都发生，也只记一轮——焦点优先。
        focus_changed = (bool(location_id) and bool(previous_focus)
                         and location_id != previous_focus)
        scene_changed = bool(scene_now) and scene_now != scene_before

        # M4-B2 / UI-DESIGN §1.3.5：玩家这一回合「做了什么」进会话日志（`act`）。
        # 放在状态行之前——所有会返回 4xx 的校验（长度 / 会话 / 速率 / 地点 /
        # 结算）都已过去，这一回合确定走 200；`seq` 在 `add_log` 内锁内递增。
        # 这一条随后由本回合的 `save()`（叙事 / 兜底路径）一并落盘。
        act_text = _turn_act_text(session, text, action_id)
        if act_text:
            session.add_log("act", act_text)

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
            save_failed = False
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
            except SaveError:
                # 工具预通行里的结算没落盘：头已写出，不能改 HTTP 状态；照旧
                # 走兜底叙事，但记下「本回合未完全持久化」，最终 `done` 如实回报。
                save_failed = True
                completion = None
            except Exception:   # noqa: BLE001 — 上游读取失败不改整回合为 _err
                completion = None
            reviewed = None
            if completion is not None:
                try:
                    reviewed = prism_guide.review_narration(result, completion)
                except Exception:   # noqa: BLE001
                    reviewed = None
            saved = not save_failed
            if reviewed is None:
                # 头已写出：失败只能用 fallback，拿不到第二行 HTTP 状态。
                # M4-B2：兜底句是玩家实际看到的正文，同样落日志——并落盘，离线
                # 回合刷新 / 重载后不丢。落盘失败走下面的 `done` 如实回报。
                fallback = _guide_fallback_text(result)
                with session.lock:
                    session.add_log("narrate", fallback)
                    try:
                        session.save()
                    except SaveError:
                        saved = False
                self._sse("fallback", {"text": fallback})
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
                    # M4-B2 / §1.3.5：叙事落日志——记的是**涂掉之后**的字
                    # （`redacted`），秘密门对日志同样生效：被删掉的秘密不会经
                    # `/api/log` 重新可见（§2.3「无痕删除」）。痕迹只在真正揭开
                    # （玩家可见）时才记一条。
                    # M4-B5：这一回合的 `beats` 一并落进条目——刷新 / 重进后
                    # 从 `/api/log?since=` 恢复的历史叙事仍能按拍切段、保留朗读
                    # 入口。没有拍时不写这个键，条目形状与 B2 一致。
                    session.add_log("narrate", redacted,
                                    extra={"beats": beats} if beats else None)
                    if trace:
                        session.add_log("trace", trace)
                    try:
                        session.save()      # L3 与 revealed 要跟着落盘
                    except SaveError:
                        # 头已写出：不能改 HTTP 状态，也**不**回滚——本回合的
                        # L3 / last_beats 是一份可恢复的提交态，下一次成功保存
                        # 即落盘。用下面的 `done` 明确回报未持久化，不谎报成功。
                        saved = False
                payload = {"text": redacted, "beats": beats}
                if trace:
                    payload["trace"] = trace
                self._sse("narration", payload)
                self._sse("usage", completion.get("usage") or {})
            if saved:
                self._sse("done", {"status": "ok"})
            else:
                self._sse("done", {"status": "error", "reason": "save_failed"})
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

    # ── 行动管线（M8-A3，ACTION-DESIGN §9）──────────────────
    def _action_catalog(self):
        """`GET /api/action/catalog`（§9.1）：当前可用行动单（id / 标签 / 风险级）。

        只读：不结算、不提交、**不为读一个数打开状态层句柄**（开句柄会碰磁盘，
        老档还会就地迁移）。离线（无 AI / 导引者模块缺失）时照常可用——行动单
        直接来自规则快照的场景行动表（§7.1）。
        """
        session = self._sess()
        with session.lock:
            actions = _action_menu(session.rules)
        return self._json({"status": "ok", "actions": actions})

    def _action_propose(self, body: dict):
        """`POST /api/action/propose`（§9.2）：NL 或显式动作 → 提案 / 澄清 / 拒绝。

        `mode: "action"`（显式 `action_id`）**一行模型都不调**，所以没有 AI
        配置时也走得通（§7.1 离线底线）；`mode: "nl"` 交 `prism_guide.propose_nl`
        （第 1 段），导引者缺失或没有 AI 配置时回 `{"status": "offline",
        "type": "offline"}`（200，**不是错误**，§9.5）。

        回执里的 `base_seq` 是**这一刻**的状态层序号：客户端 `commit` 时原样
        带回，状态层按它做乐观并发（§9.3 / §7.4）。提案本身无状态透明——
        服务端不做提案缓存，`commit` 时重验回带的提案（§9.2）。
        """
        session = self._sess()
        if not _rate_ok(session.sid, "action_propose", ACTION_PROPOSE_LIMIT):
            return self._err(ACTION_TOO_FREQUENT, 429)
        mode = str(body.get("mode")
                   or ("action" if body.get("action_id") else "nl"))
        if mode == "action":
            with session.lock:
                proposal = _action_proposal_from_id(session, body.get("action_id"),
                                                    body.get("params"))
            return self._json(_action_propose_payload(session, proposal))
        return self._action_propose_nl(session, body)

    def _action_propose_nl(self, session, body: dict):
        """`mode: "nl"` 的第 1 段：交 `prism_guide.propose_nl`，本层只翻 HTTP。

        带 `clarify_id` 时是**同一个澄清回合的回答**（§6.3）：取回那次澄清
        （过期 / 没见过 → 404 `没有这个提案`），把回答并入草案重新解析；仍歧义
        由 A2 降级为拒绝，本层不再二次追问。
        """
        if prism_guide is None:
            # §9.5：导引者缺失**不是错误**——200 + `type: "offline"`，提示改用
            # `mode: "action"`（显式行动不需要 AI）。
            return self._json({"status": ACTION_TYPE_OFFLINE,
                               "type": ACTION_TYPE_OFFLINE})
        text = str(body.get("text") or "")
        clarify = None
        answer = ""
        clarify_id = str(body.get("clarify_id") or "").strip()
        if clarify_id:
            record = _action_clarify_take(session.sid, clarify_id)
            if record is None:
                return self._err(ACTION_NO_PROPOSAL, 404)
            clarify = record["clarify"]
            text = str(record.get("text") or "")     # 回答路径沿用第一次的原文
            answer = str(body.get("answer") or body.get("text") or "")
        with session.lock:
            rules = copy.deepcopy(session.rules)
            guide = copy.deepcopy(session.guide)
        out = prism_guide.propose_nl(rules, guide, text,
                                     clarify=clarify, answer=answer)
        out = out if isinstance(out, dict) else {}
        kind = str(out.get("type") or "")
        if kind == ACTION_TYPE_PROPOSAL:
            return self._json(_action_propose_payload(session, out.get("proposal")))
        if kind == ACTION_TYPE_CLARIFY:
            _action_clarify_put(session.sid, out.get("clarify"), text)
            return self._json({"status": "ok", "type": ACTION_TYPE_CLARIFY,
                               "clarify": out.get("clarify")})
        if kind == ACTION_TYPE_OFFLINE:
            return self._json({"status": ACTION_TYPE_OFFLINE,
                               "type": ACTION_TYPE_OFFLINE})
        # 拒绝：A2 给的是 §9.5 的固定短语，本层只翻状态码（404 / 404 / 403）。
        reason = str(out.get("reason") or ACTION_FORBIDDEN)
        return self._err(reason, ACTION_REJECT_CODES.get(reason, 403))

    def _action_confirm(self, body: dict):
        """`POST /api/action/confirm`（§6.4 / §9.1）：R2–R4 提案的确认或取消。

        `decision: "confirm"`（缺省即确认）发一枚确认令牌，客户端把它带进
        `commit`；`decision: "cancel"` 丢弃该提案的令牌——R2–R4 的「取消」就是
        不提交（M8 的同步行动没有可取消的已排程行动，§4.1）。

        提案是**无状态透明**的（服务端不做提案缓存），所以这里只校验 id 形状：
        形状不对 → 404 `没有这个提案`；确认令牌的有效期由本层管（过期 →
        `commit` 回 409 `需要确认`）。
        """
        session = self._sess()
        proposal_id = str(body.get("proposal_id") or "")
        if not ACTION_PROPOSAL_ID_RE.fullmatch(proposal_id):
            return self._err(ACTION_NO_PROPOSAL, 404)
        if str(body.get("decision") or "confirm") == "cancel":
            _action_confirm_cancel(session.sid, proposal_id)
            return self._json({"status": "ok", "type": ACTION_TYPE_CANCELLED,
                               "proposal_id": proposal_id})
        record = _action_confirm_issue(session.sid, proposal_id)
        return self._json({"status": "ok", "proposal_id": proposal_id,
                           "confirm_token": record["token"],
                           "expires_at": record["expires_at"]})

    def _action_commit(self, body: dict):
        """`POST /api/action/commit`（§9.3）：验证 → 结算 → 提交 →（可选）叙事。

        状态行（唯一一行 HTTP 状态）写出**之前**依次做完（§9.5 / §7.7）：会话
        → 幂等键 → **幂等查表**（任何结算之前短路，命中即回首次结果）→ 提案
        重验 → 速率 → 提案级幂等（同 `proposal_id` 无新键 → 409）→ 确认令牌
        → 第 3–5 段（结算 + 状态层提交）。此后一律走 SSE：`result` 一定早于
        `narration`（§3.3 硬边界），叙事失败只发 `fallback`，**绝不发第二行
        HTTP 状态**。
        """
        sid = (self.headers.get("X-Session") or "").strip()
        session = get_session(sid) if sid else None
        if not session:
            return self._err(ACTION_NO_SESSION, 400)
        key = str(self.headers.get("Idempotency-Key") or "").strip()
        if not ACTION_IDEM_KEY_RE.fullmatch(key):
            return self._err(ACTION_BAD_IDEM_KEY, 400)
        replay = _action_idem_get(sid, key)
        if replay is not None:
            # §7.7：同会话 + 同键的重放**不结算、不写事件**，直接回首次结果。
            # 长行动的首次回执是 `scheduled`：按同一形状重放（也不再排一次队）。
            if _action_is_scheduled(replay):
                return self._action_scheduled_respond(replay, replayed=True)
            return self._action_respond(session, {"result": replay, "snapshot": None,
                                                 "saved": True}, "", replayed=True)
        proposal = _action_prepare_proposal(session, body.get("proposal"))
        if not _rate_ok(sid, "action_commit", ACTION_COMMIT_LIMIT):
            return self._err(ACTION_TOO_FREQUENT, 429)
        proposal_id = str(proposal.get("proposal_id") or "")
        if _action_is_committed(sid, proposal_id):
            # §9.4 提案级幂等：换个键再赌一次也不行（防误连点 / 防重复发奖）。
            return self._err(ACTION_ALREADY_COMMITTED, 409)
        if _action_is_confirm_risk(proposal.get("risk")):
            token = str(body.get("confirm_token") or "")
            if not ACTION_CONFIRM_TOKEN_RE.fullmatch(token) \
                    or not _action_confirm_check(sid, proposal_id, token):
                return self._err(ACTION_NEED_CONFIRM, 409)
        base_seq = body.get("base_seq")
        if not isinstance(base_seq, int) or isinstance(base_seq, bool) \
                or base_seq < 0:
            # §9.3 的 `base_seq` 是「提交方读到的最新 seq」——回 `propose` 给的
            # 那一个；缺 / 不是整数一律按过期处理（重读状态再提，§7.4）。
            return self._err(ACTION_STALE_SEQ, 409)
        out = _action_settle_commit(session, proposal, base_seq=int(base_seq),
                                    idempotency_key=key)
        scheduled = out.get("scheduled")
        if isinstance(scheduled, dict):
            # 长行动（§4.3）：本回合到此为止——到期由推进入口结算。
            # 提案同样**消费掉**（换键不能重排一次），同键重放回同一份回执。
            _action_idem_put(sid, key, scheduled)
            _action_mark_committed(sid, proposal_id)
            return self._action_scheduled_respond(scheduled, replayed=False)
        _action_idem_put(sid, key, out["result"])
        _action_mark_committed(sid, proposal_id)
        text = str(proposal.get("raw_text")
                   or (proposal.get("intent") or {}).get("desired") or "")
        return self._action_respond(session, out, text, replayed=False)

    def _action_scheduled_respond(self, payload, *, replayed):
        """长行动的回执（默认 SSE，`Accept: application/json` 回整段）。

        SSE 形状：`scheduled` → `done`（`replayed` 时在 `done` 上标出）——
        `scheduled` 是**已排入时间线**，不是结算，所以这一轮不发 `result` /
        `narration`（它们属于到期结算那一回合，见 `POST /api/chron/advance`）。
        """
        if _wants_json(self.headers.get("Accept") or ""):
            return self._json(payload)
        try:
            self._begin_stream()
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            self._sse("scheduled", payload)
            if replayed:
                self._sse("done", {"status": "ok", "replayed": True})
            else:
                self._sse("done", {"status": "ok"})
            self._sse_close()
        except (BrokenPipeError, ConnectionResetError):
            pass                # 客户端中途断线不该让服务端报错

    def _action_respond(self, session, out, text, *, replayed):
        """把（已提交的）结果发回去：默认 SSE，`Accept: application/json` 回整段。

        SSE 顺序（§9.3）：`result` → `narration`（或 `fallback`）→ `usage` →
        `done`。幂等重放（`replayed`）**不再跑叙事段**（§7.7 重放 ≠ 重结算），
        只回首次结果并在 `done` 上标 `replayed`；`saved` 为假（提交已成功但门面
        没落盘）时 `done` 如实回报 `save_failed`，绝不谎报成功。
        """
        result = out["result"]
        if _wants_json(self.headers.get("Accept") or ""):
            return self._json(result)
        try:
            self._begin_stream()
        except (BrokenPipeError, ConnectionResetError):
            return
        try:
            self._sse("result", result)
            if replayed:
                self._sse("done", {"status": "ok", "replayed": True})
                self._sse_close()
                return
            narration = _action_narrate(session, out.get("snapshot"), result, text)
            self._sse(narration["event"], narration["payload"])
            if narration["event"] == "narration":
                self._sse("usage", narration["usage"])
            saved = bool(out.get("saved")) and _action_store_narration(
                session, narration["event"], narration["payload"])
            if saved:
                self._sse("done", {"status": "ok"})
            else:
                self._sse("done", {"status": "error", "reason": "save_failed"})
            self._sse_close()
        except (BrokenPipeError, ConnectionResetError):
            pass                # 客户端中途断线（锁屏 / 切网）不该让服务端报错

    # ── CHRON 推进入口（M9-C6，CHRON-DESIGN §4.3 / §6）─────────────────
    def _chron_advance_route(self, body: dict):
        """`POST /api/chron/advance`：推进世界时间，**在到期点结算**（§6.3）。

        请求体：`{"target_minute": <世界分钟>, "offline_advance": <bool，可选>}`。
        `advance_to` 遇屏障就停（§6.2 B1–B8），途中每个 `action.due` 逐条走
        「PRISM 判胜负 → 一条事件提交 → 叙事」；回执含 `world_minute` /
        `stopped_at` / `barriers` / `next_barrier` / `settled`（每条含结果与
        叙事，离线时是兜底句）/ `events` / `saved`。命名与形状由本单自定
        （Issue #313 验收 3），形状稳定在此处写死，客户端据此驱动回合。
        """
        session = self._sess()
        if not _rate_ok(session.sid, "chron_advance", CHRON_ADVANCE_LIMIT):
            return self._err(ACTION_TOO_FREQUENT, 429)
        return self._json(_chron_advance(
            session, body.get("target_minute"),
            offline_advance=bool(body.get("offline_advance"))))

    def _chron_cancel_route(self, body: dict):
        """`POST /api/chron/cancel`：取消一个已排程 / 进行中的行动（§4.3 映射表）。

        请求体 `{"task_id": "act_<32hex>"}`（= 排程回执里的 `task_id`）。
        取消是**幂等**的：不存在的 / 已终态的卡回 `cancelled: false`（200），
        缺 `task_id` 才是 400 `没有这个任务`；**未迁移老档**上同样回
        `cancelled: false` 且**不改盘**（不开句柄 = 不触发落盘迁移，§10.1）。
        """
        session = self._sess()
        task_id = str(body.get("task_id") or "").strip()
        if not task_id:
            return self._err(CHRON_NO_TASK, 400)
        if not _rate_ok(session.sid, "chron_cancel", CHRON_CANCEL_LIMIT):
            return self._err(ACTION_TOO_FREQUENT, 429)
        cancelled = _schedule_hook(session).cancel(task_id)
        return self._json({"status": "ok", "task_id": task_id,
                           "cancelled": bool(cancelled),
                           "world_minute": _chron_world_minute(session)})

    def _chron_next_barrier_route(self):
        """`GET /api/chron/next_barrier[?from=<世界分钟>]`：下一个屏障 / 结算点。

        只读（不落盘、不推进）：`from` 省略 = 当前世界分钟；非法 / 早于当前 →
        400（世界时间只增不减，§3.2）。没有可停的分钟 → `next_barrier: null`；
        **未迁移老档**上不开句柄（开 = 落盘迁移），同样回 `null`（§10.1）。
        """
        session = self._sess()
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        raw = str((q.get("from") or [""])[0]).strip()
        from_minute = None
        if raw:
            try:
                from_minute = int(raw)
            except (TypeError, ValueError):
                return self._err(CHRON_BAD_TARGET, 400)
            if from_minute < 0:
                return self._err(CHRON_BAD_TARGET, 400)
        return self._json(_chron_next_barrier(session, from_minute))

    # ── 建局（M4-B1，UI-DESIGN §2.1 / §7 缺口 B1）──────────
    def _session_create(self):
        """`POST /api/session/create`：开一局——建一份新存档并返回新 `sid`。

        §2.1 的第一步（大厅「开始新的一局」）今天没有路由（`_sess()` 找不到会话
        即 400）；本路由补上它：**不带** `X-Session`，服务端生成一个新 `sid`，
        落盘成功后返回 `sid` 与**新局最小视图**（与 `GET /api/session` 同一份
        `rules_view()`，只是多一个 `status: "ok"`，客户端可直接当整局视图用；
        也可以只取 `sid` 存进 `localStorage('prism.sid')` 再拉一次）。

        体可以为空（没有任何必填参数）；畸形 JSON / 超过 2MB → 400（沿用
        `_body()` 的统一语义）。落盘失败（`SaveError`）→ 500「保存失败，请重试」，
        且**不**把半成品塞进缓存——与 #170 的结论一致：建局失败绝不返回误导性成功。

        新局是**未绑定**态（`guide.scenario_id == ""`）：世界 / 剧本由
        `POST /api/guide/bind` 显式绑定，本路由不猜、不扫 `data/scenarios/`，
        也不返回可选战役列表（那是需要产品级决策的另一单，见 PR 说明）。
        全程不碰导引者模块，故无 AI 配置（离线）时照常可用。
        """
        with _sessions_lock:
            # sid 自己生成：和已有的内存会话 / 磁盘存档都不能撞（撞了会覆盖别人的档）。
            for _ in range(8):
                sid = _new_sid()
                if sid not in _sessions and not _save_exists(sid):
                    break
            else:
                # 8 次都撞（实际不可能）：如实报失败，不返回一个没落盘的 sid。
                return self._err("建局失败，请重试", 500)
            # 建局必须**原子**：save() 落盘成功之后才进缓存，客户端拿到 sid 时
            # 磁盘上已经有这份存档（随后 GET /api/session 必然 200）。
            session = Session(sid)
            try:
                session.save()
            except SaveError:
                # 没落盘：不把半成品塞进缓存，客户端拿到 500 可重试。
                return self._err("保存失败，请重试", 500)
            _sessions[sid] = session
        with session.lock:
            view = rules_view(session)
        view["status"] = "ok"
        return self._json(view)

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
            if room_id in _sessions or _save_exists(room_id):
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
            try:
                session.save()
            except SaveError:
                # 建房没落盘：**不**把半成品塞进缓存，客户端拿到 500 可重试。
                return self._err("保存失败，请重试", 500)
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
            previous_room = copy.deepcopy(session.room)
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
            try:
                session.save()
            except SaveError:
                # 入房没落盘：回滚房间块，内存与磁盘一致；令牌作废，客户端重试。
                session.room = previous_room
                return self._err("保存失败，请重试", 500)
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
            previous_room = copy.deepcopy(session.room)
            member["state"] = "left"
            member["last_seen_at"] = time.time()
            if session.room.get("host_member_id") == member["member_id"]:
                session.room["host_member_id"] = ""
            session.room["seq"] = _int_or(session.room.get("seq"), 0) + 1
            try:
                session.save()
            except SaveError:
                # 离房没落盘：回滚房间块；成员仍在座，客户端拿到 500 可重试。
                session.room = previous_room
                return self._err("保存失败，请重试", 500)
        return self._json({"status": "ok", "room_id": room_id,
                           "member_id": member["member_id"]})

    def _room_kick(self, body: dict):
        """`POST /api/room/kick`：房主踢出成员（§4.2 角色矩阵）。

        体：`{member_id}`。非房主 → `403 not_authorized`（T8，房间状态不变）；
        被踢者 `state=left`、其请求此后得 `403 member_left`（§4.4）。

        ⚠️ **房主授权检查与变更必须同处一个 `session.lock` 临界区**：`_room_auth`
        返回时锁已释放，若把「房主仍为当前 active host」的检查留在锁外，并发的
        离房请求可在检查通过之后、变更之前落地 —— 踢人仍会执行（越权）。
        """
        room_id = self._header_room()
        try:
            session, member = self._room_auth(room_id)
        except _RoomAuthError as exc:
            return self._err(exc.msg, exc.code, exc.reason)
        target_id = str(body.get("member_id") or "").strip()
        with session.lock:
            # 授权判定读的是**锁内**的房间 / 成员当前值，与下面的变更原子。
            if (member.get("role") != "host"
                    or member.get("state") != "active"
                    or session.room.get("host_member_id") != member["member_id"]):
                return self._err("只有房主能踢人", 403, "not_authorized")
            target = (session.room.get("members") or {}).get(target_id)
            if not isinstance(target, dict):
                return self._err("成员不存在", 404, "member_not_found")
            if target_id == member["member_id"]:
                return self._err("不能踢出自己", 400, "bad_target")
            previous_room = copy.deepcopy(session.room)
            target["state"] = "left"
            target["last_seen_at"] = time.time()
            session.room["seq"] = _int_or(session.room.get("seq"), 0) + 1
            try:
                session.save()
            except SaveError:
                # 踢人没落盘：回滚房间块；被踢者仍在座，客户端拿到 500 可重试。
                session.room = previous_room
                return self._err("保存失败，请重试", 500)
        return self._json({"status": "ok", "room_id": room_id,
                           "member_id": target_id})

    def _room_chat(self, body: dict):
        """`POST /api/room/chat`：按频道发一条消息并**广播**（§6.1 / §6.2 / §6.4）。

        体：`{channel, text, target?, members?}`——`channel` ∈ §6.1 的七个频道；
        `target`（`tell` 用）是收件成员 id；`members`（`org` / `npc` 用）是显式
        点名的成员 id 列表。服务端在**提交时算出并冻结受众**（`visibility` 的
        `scope` / `subjects`，写进事件封套）；聊天正文放在事件 `cause`（事件封套无
        自由文本字段，STATE §5.1），且不变更任何世界块（`changes=[]`：聊天内容
        **不自动成为世界事实**，GDD §7.7）。
        提交成功后再按受众扇出（§5.5）；每个成员收到的是**自己那份**（§6.3）。
        observer 只读 → `403`（§4.2）。
        """
        room_id = self._header_room()
        try:
            session, member = self._room_auth(room_id)
        except _RoomAuthError as exc:
            return self._err(exc.msg, exc.code, exc.reason)
        channel = str(body.get("channel") or "").strip().lower()
        if channel not in ROOM_CHANNELS:
            return self._err("频道不合法", 400, "bad_channel")
        if member.get("role") == "observer":
            return self._err("观察者只读", 403, "not_authorized")
        text = _text_or(body.get("text"), ROOM_CHAT_TEXT_MAX).strip()
        if not text:
            return self._err("消息不能为空", 400, "bad_text")
        target = str(body.get("target") or "").strip()
        raw_members = body.get("members")
        members = [str(m) for m in raw_members] if isinstance(raw_members, list) else []
        if channel == "host" and member.get("role") != "host":
            return self._err("只有房主能发管理消息", 403, "not_authorized")
        with session.lock:
            if channel == "tell" and target not in _room_active_members(session.room):
                return self._err("对象不在房间", 404, "member_not_found")
            scope, subjects = _room_chat_audience(session.room, member, channel,
                                                  target, members)
            if not scope:
                return self._err("频道不合法", 400, "bad_channel")
            handle = session._handle or session._open_handle()
            base_seq = _int_or(handle.view().get("seq"), 0)
            # 幂等键：优先 `Idempotency-Key` 头（§7.2）；缺省由服务端补一个，
            # 真正的重放去重归 S4（本切片只保证一次提交 = 一条事件）。
            key = str(self.headers.get("Idempotency-Key") or "").strip()
            if not ACTION_IDEM_KEY_RE.fullmatch(key):
                key = "chat_" + secrets.token_hex(16)
            commit = handle.commit(
                base_seq=base_seq, idempotency_key=key,
                type=ROOM_CHAT_EVENT_PREFIX + channel,
                actor=str(member.get("member_id") or ""),
                target=(target or None), cause=text, changes=[],
                rules_version=ROOM_CHAT_RULES_VERSION,
                visibility={"scope": scope, "subjects": subjects},
                source="player")
            status = str((commit or {}).get("status") or "")
            if status not in ("committed", "replayed"):
                return self._err("状态已变，请重试", 409, "commit_conflict")
        # §5.5：**提交成功之后**扇出——推送只是交付，不影响已提交的事实。
        _room_publish_after_commit(handle, commit, session.sid)
        return self._json({"status": "ok", "room_id": room_id,
                           "channel": channel, "seq": commit.get("seq_to"),
                           "scope": scope, "subjects": subjects})

    # ── 房间事件流（M10-S2，§5.4 / §5.5）────────────────────
    def _room_stream_route(self):
        """`GET /api/room/stream?since=`：事件流（SSE）+ **同一路径**的短轮询降级。

        缺省回 `text/event-stream`（复用导引者的 `_begin_stream` / `_chunk` 口径）；
        `Accept: application/json`（且没点名 SSE）时回一次性读取——降级短轮询用
        （§5.5），过滤判据与 `since` 语义与流式**完全一致**，客户端换的只是怎么取。
        鉴权与写接口同一条链（§4.1）：坏令牌 / 缺成员头 / 已离开 → 401 / 403，
        **不开流**。
        """
        room_id = self._header_room()
        try:
            session, member = self._room_auth(room_id)
        except _RoomAuthError as exc:
            return self._err(exc.msg, exc.code, exc.reason)
        since = _since_param(self.path)
        if _wants_json(self.headers.get("Accept")):
            frames = _room_frames(session, member, since)
            return self._json({"frames": frames,
                               "seq": frames[-1]["seq"] if frames else since})
        self._room_stream(session, member, since)

    def _room_stream(self, session: Session, member: dict, since: int) -> None:
        """一条推送流的服务端：补发 → 活流（心跳 / 队列）→ 断流收尾。

        - **先登记再补发**：登记与补发读之间提交的事件进队列，写线程按
          `last_sent` 去重——两条路径都不漏，也不重（§6.4 / T5）；
        - 心跳：每 `ROOM_STREAM_HEARTBEAT_SECONDS` 一行注释 `: keepalive`（无 `data`）；
        - 判死：socket **写超时** = `ROOM_STREAM_DEAD_SECONDS`。「90s 无任何帧
          （含心跳）」只可能发生在一个写不动的连接上（写阻塞 = 帧没出去），
          写超时抛错即关流（§5.5）；客户端侧的「90s 没收到帧就重连」是前端
          判活（`static/**`，不在本单范围）；
        - 队列满：`drain` 回 `stop=True` → 正常结束分块（零长度块），客户端
          照常重连补发（T13）。
        """
        stream = _RoomStream(str(session.sid),
                             str(member.get("member_id") or ""),
                             str(member.get("character_id") or ""), since)
        _room_stream_register(stream)
        try:
            try:
                frames = _room_frames(session, member, since)
            except state.StateError:
                # 状态行还没写：按既有「状态读取失败」回 500，**不**开一条空流。
                return self._err("状态读取失败", 500)
            try:
                self._begin_stream()
            except (BrokenPipeError, ConnectionResetError, OSError):
                return                  # 客户端在开流前就断了：静默收尾
            self.connection.settimeout(ROOM_STREAM_DEAD_SECONDS)
            for frame in frames:
                self._write_room_frame(stream, frame)
            while True:
                batch, stop = stream.drain(ROOM_STREAM_HEARTBEAT_SECONDS)
                if stop:
                    break               # 队列满 / 已收尾：结束分块，让客户端重连
                for frame in batch:
                    self._write_room_frame(stream, frame)
                if not batch:
                    self._chunk(b": keepalive\n\n")     # §5.5 心跳：无 data
            self._sse_close()
        except (BrokenPipeError, ConnectionResetError, socket.timeout,
                TimeoutError, OSError):
            pass            # 客户端断线 / 90s 写不动：收尾即可，不再动 HTTP 状态
        finally:
            stream.close()
            _room_stream_unregister(stream)

    def _write_room_frame(self, stream: _RoomStream, frame: dict) -> None:
        """写一帧（§5.4）；`seq ≤ last_sent` 不重写（补发 / 队列重叠时去重）。"""
        seq = _int_or(frame.get("seq"), 0)
        if seq <= stream.last_sent:
            return
        self._sse_frame(seq, str(frame.get("type") or ""), frame)
        stream.last_sent = seq


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
