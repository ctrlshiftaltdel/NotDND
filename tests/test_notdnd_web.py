#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试：notdnd_web 的规则会话接线与导引路由。

断言都是**真跑**出来的，不是对着实现抄一遍：

  1. 快照往返：prism_core.RuleSession 的快照进存档 → 原子落盘 → 重新载入
     → 逐字段一致；并与规则层的 `from_snapshot() → snapshot()` 契约对照（幂等）。
  2. 老档惰性迁移：缺 `rules` 的旧存档补成**空规则会话**，已有字段不被覆盖，
     磁盘上的老档不被就地改写，且两个老档拿到的**不是同一个可变对象**。
  3. GET /api/session：真起服务（随机端口 + 临时存档目录，退出时必收摊），
     经 HTTP 拉取规则视图（party / combat / pressure 等），且该请求不改写存档；
     未开会话仍按既有 400 语义。
  4. ATLAS 存档块（切片 I4，§3.4）：带块落盘 → 载入还原，位置还在；
     老档没有块 → 按当前世界懒编译不报错；世界删掉了队伍所在地点 →
     搬迁到仍存在的区域且日志有说明；HTTP 出口列表是文本，移动返回行程档。
  5. 导引者（G2）：`guide` 块四处接线与加载合同；`GET /api/guide/status`；
     `POST /api/guide/turn` 的长度 / 会话 / 速率 / 淡出整句 / 结算 / 分块流 /
     头写出之后的兜底。要换传输层的用例走**本进程**的服务线程（见 _LocalServer），
     其余仍用真子进程，保证启动路径也被覆盖。
  6. 导引者朗读（G3）：`turn` 切出的节拍写进 `last_beats`；`POST /api/guide/speak`
     的长度 / 会话 / 速率 / 语音可用 / 全文匹配，以及 HTTP/1.1 分块音频流
     （24 kHz + pcm16 头、无 Content-Length、每个音频 delta 单独 base64 解码后拼接）。
  7. 导引者工具环（G4）：`查规则` 那三句整句才开预通行；预通行是**唯一**一个
     思考开且 `stream` 为 false 的调用，叙事请求保持思考关并重拼 L0–L4（无
     `role: tool` / 无 `reasoning_content`）；工具里的 `ValueError` 只变成固定
     短语，不产生第二行 HTTP 状态；叙事带 `tool_calls` 不再开一轮。
  8. 导引者绑定与焦点（G5）：`POST /api/guide/bind` 的四种结果（400「没有这场
     战役」/ 400「世界对不上」/ 200 幂等 / 409「已经绑定」）都基于真剧本文件；
     绑定后叙事请求的第 2 条消息换成这场剧本的典范卡（含专名、无 salt / `docs/`），
     同一剧本的两份存档 L1 全等；`turn` 的可选 `location_id` 在状态行之前校验
     （非法 → 400「没有这个地点」且不开 SSE），合法时写入 `focus_location_id`
     并落盘；带 `location_id` 的回合在状态行之后、叙事之前调用一次
     `ensure_realization`（G6 起它会先发一次**非流式**实相请求，所以这一回合的上游
     不止叙事那一次；实相请求体与占位语义由 `tests/test_prism_guide.py` 自己测）。
  9. 导引者行为与对白（G7 接线）：焦点变化的回合先发**一次**场外节拍请求
     （非流式 + 思考关 + 无工具，user 只带 id / 名字 / drive / mask / lever / tell，
     **不含秘密全文**），再发叙事；玩家的整段叙事进 L3 与切拍之前先过秘密门
     （L3 里既没有秘密全文、也没有连续 8 码位）；`narration` 至多带一条 `trace`
     （痕迹揭开随同一次 `save()` 落盘）；`npc_flags` 为 `betrayed` 时痕迹用典范
     原文且**不发**场外请求；离线时不留痕、回合照常；`rules.scene.id` 变化时
     为**当前焦点**记一轮。

 10. 多人房间（M10-S1，MPI §3.2 / §4）：`room` 顶层块随存档落盘、旧单人档惰性迁移
     （T11）；`POST /api/room/create|join|leave|kick` 与 `Authorization: Bearer` +
     `X-Room`/`X-Member` 鉴权链；容量满员 `409 room_full`（T7）、非房主踢人 `403`（T8）、
     坏令牌 / 缺成员头 `401`（T12）；令牌只存 `sha256`、明文不入存档。
     另有一条**确定性并发回归**：房主在「鉴权通过后、踢人变更前」离房时，踢人必须
     被拒（用锁代理插入交错，不靠 sleep 赌时序）。

 11. 建局写接口（M4-B1，UI-DESIGN §2.1 / §7 缺口 B1）：`POST /api/session/create`
     真起服务（随机端口 + 临时存档目录）建局 → 返回可用的新 `sid` 与新局最小视图
     → `GET /api/session` / `GET /api/log` 立即可用；导引者离线（无 `.env`）时同样成立；
     畸形 JSON 体 → 400；落盘失败 → 500 且不返回误导性成功（与 #170 同一口径）；
     重载往返（服务重启后从磁盘重新载入）。既有 `X-Session` / `?sid=` 语义不变。

 12. 回合落会话日志（M4-B2，UI-DESIGN §1.3.5 / §7 缺口 B2）：`/api/guide/turn`
     一回合结束后玩家的 `act`（自由文本或行动 label）、导引者 `narrate`（含
     离线 / 上游失败的兜底句）与揭开的 `trace` 写进 `Session.log`（`seq` 单调、
     锁内递增）；`GET /api/log?since=` 按 `seq` 增量取回；重载往返（回合日志已
     落盘）；叙事记的是秘密门**涂掉之后**的字，被删内容不因落日志重新可见；
     `result` 先于 `narration` 的既有事件顺序不变；离线 / 模块缺失 / 淡出 / 跳过
     这些短路回合同样落 `act` + `narrate`（真子进程覆盖启动路径）。
     M4-B5 追加：`narrate` 条目带上该回合的 `beats`（无拍时不写空字段），
     全量 / `?since=` 增量与重载往返都一致——刷新后恢复的历史叙事仍能按拍切段。

 13. 存档接线与老档迁移（M7-3，STATE-DESIGN §10 / §11）：`Session` 仍是网页层
     内存门面，`save()` / `load()` 委托 `state`——新档落在
     `campaigns/<sid>/snapshot.json`（检查点），展示流走 `stream.cursor`；
     老档（旧单文件 `<sid>.json`）在**第一次 save 时**才落成新档并留
     `snapshot.json.pre-v0.bak`（只读接口不改写它）；各块逐字搬、salt 不变；
     `?since=` / `/api/session` 的既有语义与用例保持通过；

 14. 行动管线写接口（M8-A3，ACTION-DESIGN §3.3 / §7 / §8 / §9）：`GET
     /api/action/catalog` 与 `POST /api/action/propose|confirm|commit`。离线
     （无 AI / 导引者模块缺失）时 `mode: "action"` 走完「验证 → 结算 → 提交 →
     叙事」一轮，`result` 一定早于叙事段，提交走状态层拿回真实 `event_ids` /
     `commit_ref`；第 9.5 节错误码总表逐条有断言，且**状态行写出之后**绝不出现
     第二行 HTTP 状态（叙事失败 / 门面落盘失败都只走 SSE）；幂等键（同键重放
     回首次结果、无重复副作用；同 `proposal_id` 换键 → 409）与排程钩子（M8
     空实现 `mode == "sync"`、恰好一次）各自成条。

 15. 战役入口（M4，UI-DESIGN §2.1，Issue #222）：`GET /api/campaigns` 列出
     世界模组（`data/worlds/**`）与**属于该世界**的剧本（`meta.world` 逐字相等，
     与 `prism_guide.bind` 同一口径）；目录与真实数据两头核对（不多、不少、
     只投影白名单字段），未绑定会话视图的 `world` / `scenario` 为空块；跨世界
     绑定 400「世界对不上」且视图不变；正确绑定后会话视图带上世界 / 剧本名。
     全程在 `_Server(offline=True)`（无 AI 配置）下成立。

 16. 状态层只读接口（M7-4，STATE-DESIGN §16，Issue #312）：`GET /api/state`
     回当前战役摘要（`campaign_id` / `seq` / `world_time` / 各块**存在性**，
     不回块内容与元信息）；`GET /api/events?since=` 只回 `seq > since` 的事件，
     严格递增、不重不漏，跨归档段（`events.<n>.ndjson` + 活动日志）拼接正确；
     `since` 缺省 / 空 = 0、非法 → 400 固定短语；无会话 → 与 `/api/session`
     同口径的 400。两条都**只读**：调用前后存档目录指纹（size + mtime +
     sha256）不变、不触发检查点、内存态 `seq` 不变；老档（未迁移）不因这两条
     路由提前落盘迁移，响应里无密钥、无本机路径。

 17. 房间事件流与断线重连（M10-S2，MPI §5.4 / §5.5，Issue #314）：
     `GET /api/room/stream?since=` 复用导引者的 `_begin_stream` / `_chunk` 口径，
     帧 = `id: <seq>` + `event: <频道>` + 一行 `data: {seq,type,event,audience}`；
     补发按「`seq > since` 读事件 + 与广播同一 `visibility` 判据过滤」——重连
     **不重不漏**（T5）、也只给本人有权看的（`public` → `world` 给全房、
     `team` → `party`、`private` → `tell` 只给 `subjects` 点名角色、`gm` 不下发）；
     默认心跳 15s 一行 `: keepalive`（注释行、不是帧）、socket 写超时 90s = 判死
     关流；出站队列**有界**（默认 256 帧），灌满 → 断流 → 客户端重连补发重建，
     最终与全量读取一致（T13：无丢事件）；`Accept: application/json` 时**同一条
     路径**回一次性读取（降级短轮询，与流式逐帧一致）。鉴权与写接口同一条链
     （401 / 403 不开流）；非法的 `since` → 400。

 18. 运行模式、暂停与 PvP（M10-S5，MPI §5.6 / §7.3 / §7.4，Issue #323）：
     `room.mode` ∈ free / event / manage（缺省 free），`paused` 与 `pvp_mode`
     落盘并生效。非房主暂停 / 改设置 → 403 且磁盘房间块不变（不改 T8 踢人）。
     房主暂停后新的世界变更（房间行动、行动提交、地图移动、CHRON 推进 / 取消、
     带 `action_id` 的导引者结算）→ 409 `room_paused`，只读与聊天仍 200，同键
     重放仍回首次结果。对另一名在座玩家角色的敌意行动：`off` → 403
     `pvp_disabled`，`consent` 无该次令牌 → 403 `pvp_needs_consent`（拒绝不
     结算），`on` 不带令牌照常经规则与状态层。事件模式只保证串行提交，
     `chron_window` 恒为 false，不产生 `window.opened`。

 19. NEURA 只读观察面与长跑（M11-N8，NEURA-DESIGN §9.4 / §10.3 / §11 N8，
     Issue #373）：`GET /api/neura/view?npc_id=…` 只读回单个 NPC 的
     `layer` / `needs` / `plan` / 最近 5 条 journal（journal / plan 白名单
     投影，不含自由文本、不含 `secret.proposition`、不含 memory / knowledge /
     secrets）；房间成员走 `_room_auth` 鉴权链，host / player 只看
     `layer == "near"`、observer 一律 404，「不存在 / 块缺失 / 无权看」同一
     `404 {"status": "forbidden"}` 不泄露存在性。长跑：100 游戏日 × 20 NPC
     纯规则（固定种子、无模型）→ 事件数 ≤ 20×100×2、`blocks.neura` 序列化
     < 1 MiB、`replay` 相等、`blocks.guide.npc_at` 与 live 相等（D4 双写
     新口径）、耗时 < 30 秒（5 游戏日一步推进，口径见用例 docstring）。

 20. 世界生成会话编排（M12-W3，WORLD-DESIGN §4 / §11 W3，Issue #387）：
     `POST /api/world/generate` 离线全流程成功（预算计数 ≤ 24、离线为 0、
     上限 = 人拍板 P4 的 24），预览卡 + overrides diff 可读（change / add /
     remove 的 op + path），error 阻断（越档 goods → 422 + E_GOODS_ERA、
     无 session_id 可 commit）；`POST /api/world/commit` 一次性（重放 404），
     完整世界实例入**玩家存档** sidecar（不入系统库 `data/worlds/`，全程
     逐文件 sha256 不变；预览不落块——`GET /api/state` 无 world 块）；同
     seed_hint 同结果（修订幂等）；P1 模型端口经 GUIDE 网关既有通道
     （调用数 == 预算 calls、`est_tokens` 对齐真实 usage）；鉴权裁定：多人
     房间仅房主可生成 / 提交（player 403）；P6 导出（Content-Disposition
     attachment）/ 导入（validate_world 零 error + kind 检查，坏实例 400 /
     422 拒收）。

 21. 账本只读观察面与长跑（M12-W8，WORLD-DESIGN §9.1 / §10.3，Issue #396）：
     `GET /api/world/ledger` 只读回账本摘要（`economy.day` / `balances` /
     `prices` / `goods`）+ 最近 `WORLD_LEDGER_TAIL` 条 Entry + 工程 / 指令
     摘要（含 `blocked_reason`）；`?project_id=` 回单工程状态 + readiness +
     该项目名下 Entry（「这项工程为什么停」）；全程零状态变更（改盘指纹
     对照断言）；鉴权照 W3 同一裁定——多人房间仅房主可读（player 403）。
     长跑：100 游戏日 × 20 NPC 纯规则（固定种子、无 AI、5 游戏日一步）
     → 事件数 ≤ 日数 × 3、`blocks.world` 序列化 < 1 MiB（P3 实测点）、
     `replay` 相等、`blocks.guide.npc_at` 重放 = live（D4 口径）、三等式
     照常、背景工程 running → 完工 → 设施维护扣账全链在日切内发生。

另有一条护栏：`import notdnd_web` 不碰磁盘（存档目录不被创建）。

零依赖：仅 Python 3 标准库。直接 `python3 tests/test_notdnd_web.py` 运行。
"""

import base64
import builtins
import contextlib
import hashlib
import http.client
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ⚠️ 顺序要求：NOTDND_SAVE 必须在 import notdnd_web **之前**设好——SAVE_DIR 是
# import 期常量。测试用自己的临时目录，绝不碰仓库里真实的 web-saves/。
_TMP = tempfile.mkdtemp(prefix="notdnd-web-test-")
os.environ["NOTDND_SAVE"] = _TMP

sys.path.insert(0, ROOT)                     # 让 tests/ 直接跑时也能 import 根模块
import atlas as atlas_kernel   # noqa: E402  N7：stub 确定性小地图
import copy
import neura                   # noqa: E402  N7：接线断言（notdnd_web 顶层已 import）
import notdnd_web   # noqa: E402
import chron        # noqa: E402
import prism_core   # noqa: E402
import prism_guide  # noqa: E402
import state        # noqa: E402
import world        # noqa: E402  W5：接线断言（notdnd_web 顶层已 import）

_SALT_RE = re.compile(r"^[0-9a-f]{32}$")

# 一段合格的模型叙事：三段标题齐全，记法与「合计」都对得上结算结果。
_OK_NARRATION = ("【裁决】你把手按上门栓，锁簧弹开。\n"
                 "【叙事】铁屑落在脚边，走廊里安静了一拍。\n"
                 "【钩子】走廊尽头有脚步声。")


# --------------------------------------------------------------------------
# 脚手架：临时存档目录 / 真服务
# --------------------------------------------------------------------------


def _free_port() -> int:
    """要一个当前空闲的端口：绑 0 让内核分配，读到号就释放。"""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _save_path(sid: str) -> str:
    """老档（旧**单文件**布局）路径：写老档夹具 / 断言老档没被改动用。"""
    return os.path.join(notdnd_web.SAVE_DIR, "%s.json" % sid)


def _campaign_file(sid: str, name: str, save_dir: str = "") -> str:
    """新档（`campaigns/<sid>/`，M7-3 起）里某个文件的路径。"""
    return os.path.join(save_dir or notdnd_web.SAVE_DIR, "campaigns", sid, name)


def _write_save(path: str, payload: dict) -> None:
    """把一份存档 JSON 写到指定目录（模拟已有存档 / 老档）。"""
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False)


def _read_save(sid: str, save_dir: str = "") -> dict:
    """读磁盘上的存档，**翻成门面（老档）字段名**——新档优先、老档兜底。

    新档是 `state` 的检查点：块在 `blocks`、展示流在 `stream`（游标 `cursor`）、
    建局时间毫秒。断言因此不必认识两套布局（迁移插件 / 落盘口径的差异由本函数吸收）。
    """
    snap = _campaign_file(sid, "snapshot.json", save_dir)
    if os.path.isfile(snap):
        with open(snap, encoding="utf-8") as handle:
            d = json.load(handle)
        blocks = d.get("blocks") if isinstance(d.get("blocks"), dict) else {}
        stream = d.get("stream") if isinstance(d.get("stream"), dict) else {}
        return {
            "sid": d.get("campaign_id"),
            "created": (d.get("created") or 0) / 1000.0,
            "log": stream.get("log") or [],
            "seq": stream.get("cursor"),
            "save_name": d.get("save_name"),
            "rules": blocks.get("rules"),
            "atlas": blocks.get("atlas"),
            "guide": blocks.get("guide"),
            "room": blocks.get("room"),
        }
    with open(os.path.join(save_dir or notdnd_web.SAVE_DIR, "%s.json" % sid),
              encoding="utf-8") as handle:
        return json.load(handle)


def _save_ids(save_dir: str = "") -> set:
    """磁盘上「真的有一档」的 sid 集合——新档要有检查点，建局中途的残留不算。"""
    base = save_dir or notdnd_web.SAVE_DIR
    ids = {name[:-5] for name in os.listdir(base) if name.endswith(".json")}
    camp = os.path.join(base, "campaigns")
    if os.path.isdir(camp):
        ids |= {name for name in os.listdir(camp)
                if os.path.isfile(os.path.join(camp, name, "snapshot.json"))}
    return ids


def _canon_session(session) -> str:
    """门面视图的**规范化 JSON**：比对「落盘 → 重载」是否逐字段一致（§6.1 口径）。"""
    return json.dumps(session.to_dict(), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))


@contextlib.contextmanager
def _storage_fault(*, write: bool = False, replace: bool = False):
    """往存档落盘路径注入故障（只在**本进程**生效，用完即还原）。

    `write=True` → 写**临时文件**失败：`pathlib.Path.write_text` 与
    `builtins.open` 的写模式都抛 `OSError`（M7-3 起落盘走 `state` 的
    `.tmp` + `os.replace`，不再经过 `write_text`，两条路径都要堵上）；
    `replace=True` → `os.replace` 抛 `OSError`（原子换名失败）。
    `notdnd_web` / `state` 与测试进程共享同一批模块对象，直接换属性即可
    命中服务端（`_LocalServer` 就在本进程里处理请求）；读模式放行，故障
    只影响写盘。
    """
    def boom(*_args, **_kwargs):
        raise OSError("注入的落盘故障")

    def fake_open(file, mode="r", *args, **kwargs):
        if "w" in mode:
            boom()
        return saved_open(file, mode, *args, **kwargs)

    saved_write = pathlib.Path.write_text
    saved_open = builtins.open
    saved_replace = os.replace
    if write:
        pathlib.Path.write_text = boom
        builtins.open = fake_open
    if replace:
        os.replace = boom
    try:
        yield
    finally:
        pathlib.Path.write_text = saved_write
        builtins.open = saved_open
        os.replace = saved_replace


def _load_json(body: bytes):
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {}


class _Server:
    """真起一个 notdnd_web 服务：子进程 + 随机端口 + 自己的临时存档目录。

    子进程读不到测试进程之后再设的环境变量，所以存档目录在**构造时**注入；
    它也是「必收摊」的责任人——退出时关停进程并删掉临时目录。

    服务端把 save_dir 随 /api/saves 回传（见 notdnd_web.py 的注释），
    测试因此能顺带验证「问服务端才知道存档写在哪」这条通路。
    """

    def __init__(self, offline: bool = False):
        self.dir = tempfile.mkdtemp(prefix="notdnd-web-srv-")
        self.port = _free_port()
        self.log = os.path.join(self.dir, "server.log")
        self.proc = None
        self._log_handle = None
        self.offline = offline

    def __enter__(self):
        env = dict(os.environ)
        env.update(NOTDND_SAVE=self.dir, NOTDND_HOST="127.0.0.1",
                   NOTDND_PORT=str(self.port), PYTHONUNBUFFERED="1",
                   PYTHONIOENCODING="utf-8")
        if self.offline:
            # `parse_env` 里 `os.environ` 优先于 `.env`，空值即「未配置」——
            # 即便本机有 `.env`，这个子进程也确定离线，绝不真的出网。
            env.update(BASE_URL="", MODEL="", API_KEY="")
        # 服务端输出落文件：管道写满会死锁，事后读文件还能拿到崩溃证据。
        self._log_handle = open(self.log, "w", encoding="utf-8")
        # start_new_session：让服务自成一个会话 / 进程组，收摊时按进程组杀，
        # 连带它可能拉起的子进程一起收干净（否则 kill 直接子进程会留下一串孤儿）。
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "notdnd_web.py")],
            cwd=ROOT, env=env, stdout=self._log_handle,
            stderr=subprocess.STDOUT, start_new_session=True)
        # ⚠️ 起不来 / 提前退出时也必须收摊：`with` 只在 __enter__ 成功后才进
        # 正文，若这里直接抛出去，__exit__ 根本不会被调用，进程就泄漏了。
        try:
            self._wait_ready()
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_exc):
        if self.proc is not None:
            # 按进程组收（负 PID = 进程组），失败再退回直接杀进程；无论成功
            # 与否都继续往下走到删临时目录，保证失败路径也收得干净。
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError,
                    AttributeError):
                # Windows 没有 killpg / getpgid（AttributeError）→ 退回杀本进程。
                self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                except (OSError, AttributeError):
                    self.proc.kill()
                self.proc.wait(timeout=10)
            self.proc = None
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

    def _wait_ready(self):
        deadline = time.time() + 30
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("服务进程提前退出（exit=%s）：\n%s"
                                     % (self.proc.returncode, self.tail()))
            try:
                status, _ = self.get("/api/saves")
            except OSError:
                time.sleep(0.1)
                continue
            if status == 200:
                return
            time.sleep(0.1)
        raise AssertionError("服务未在 30 秒内就绪：\n%s" % self.tail())

    def tail(self) -> str:
        try:
            with open(self.log, encoding="utf-8") as handle:
                return handle.read()[-4000:]
        except OSError:
            return "（无服务端日志）"

    def get(self, path: str, sid: str = "", headers: dict | None = None):
        """发一次 GET，返回 (状态码, 解析后的 JSON)。

        `headers` 用来附加房间鉴权头（`X-Room` / `X-Member` /
        `Authorization`，§4.1）；`sid` 仍写 `X-Session`。
        """
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        request_headers = dict(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        try:
            conn.request("GET", path, headers=request_headers)
            response = conn.getresponse()
            body = response.read()
            return response.status, _load_json(body)
        finally:
            conn.close()

    def post(self, path: str, payload: dict, sid: str = "",
             headers: dict | None = None):
        """发一次 POST，返回 (状态码, 解析后的 JSON)。"""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        request_headers = {"Content-Type": "application/json; charset=utf-8"}
        request_headers.update(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        try:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            conn.request("POST", path, body=body, headers=request_headers)
            response = conn.getresponse()
            return response.status, _load_json(response.read())
        finally:
            conn.close()


def _sample_rules(sid: str) -> dict:
    """一份有内容的规则会话快照：覆盖 party / scene / pressure / log 等字段。"""
    rules = prism_core.RuleSession(sid)
    unit = prism_core.new_unit("u-1", "试炼者", attributes={"MGT": 6, "INS": 5})
    unit["vitality"] = 9
    unit["max_vitality"] = 12
    rules.party.append(unit)
    rules.scene = {"id": "sc-1",
                   "actions": [{"id": "a-1", "label": "勘察", "kind": "check", "df": 12}]}
    rules.active_unit_id = "u-1"
    rules.pressure = 2
    rules.done_actions["a-0"] = {"unit": "试炼者", "text": "已勘察", "reward": []}
    rules.action_fails["a-1"] = 1
    rules.add_log("check", "勘察 · 判定 → 成功")
    return rules.snapshot()


# --------------------------------------------------------------------------
# 导引者（G2）脚手架：本进程服务线程 / 假传输 / SSE 解析
# --------------------------------------------------------------------------


class _LocalServer:
    """在**本进程**里起一台服务线程（随机端口）。

    子进程服务（`_Server`）读不到测试进程后来打的补丁——凡是要替换
    `prism_guide.TRANSMIT`（假传输）或改模块全局的用例都走这一台；
    其余用例仍用真子进程，保证「从命令行启动」这条路径也被覆盖。
    """

    def __init__(self):
        self.server = None
        self.thread = None
        self.port = 0

    def __enter__(self):
        self.server = notdnd_web.ThreadingHTTPServer(
            ("127.0.0.1", 0), notdnd_web.Handler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        return False

    def get(self, path: str, sid: str = "", headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        request_headers = dict(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        try:
            conn.request("GET", path, headers=request_headers)
            response = conn.getresponse()
            return response.status, _load_json(response.read())
        finally:
            conn.close()

    def post(self, path: str, body: dict, sid: str = "",
             headers: dict | None = None):
        """POST 一次，返回 (状态码, JSON, **已解块**的响应体, 响应头字典)。

        `http.client` 会自己把分块体还原，所以这里拿到的 `raw` 直接交给
        `_parse_sse`；只有裸 socket 的 `_raw_http` 才需要 `_dechunk`。
        `headers` 用来附加房间鉴权头（§4.1）。
        """
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        request_headers = {"Content-Type": "application/json"}
        request_headers.update(headers or {})
        if sid:
            request_headers["X-Session"] = sid
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        try:
            conn.request("POST", path, body=payload, headers=request_headers)
            response = conn.getresponse()
            raw = response.read()
            heads = {key.lower(): value for key, value in response.getheaders()}
            return response.status, _load_json(raw), raw, heads
        finally:
            conn.close()


def _dechunk(raw: bytes) -> bytes:
    """把 HTTP/1.1 分块体还原（十六进制长度行 + CRLF + 数据）。"""
    _head, sep, rest = raw.partition(b"\r\n\r\n")
    if not sep:
        return b""
    out = []
    while True:
        line, sep, rest = rest.partition(b"\r\n")
        if not sep:
            break
        try:
            size = int(line.split(b";")[0].strip() or b"0", 16)
        except ValueError:
            break
        if size == 0:
            break
        out.append(rest[:size])
        rest = rest[size + 2:]
    return b"".join(out)


def _raw_http(port: int, path: str, body: dict, sid: str = "",
              headers: dict | None = None) -> bytes:
    """用裸 socket 发一次 POST，读到连接关闭——用来数有几行 HTTP 状态。

    `headers` 用来附加请求头（`Idempotency-Key` / `Accept` 等）。
    """
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    lines = ["POST %s HTTP/1.1" % path, "Host: 127.0.0.1",
             "Content-Type: application/json",
             "Content-Length: %d" % len(payload)]
    if sid:
        lines.append("X-Session: %s" % sid)
    for key, value in (headers or {}).items():
        lines.append("%s: %s" % (key, value))
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + payload
    with socket.create_connection(("127.0.0.1", port), timeout=15) as sock:
        sock.sendall(request)
        pieces = []
        while True:
            piece = sock.recv(65536)
            if not piece:
                break
            pieces.append(piece)
        return b"".join(pieces)


def _raw_body_http(port: int, path: str, raw: bytes, sid: str = "",
                   headers: dict | None = None,
                   content_length: int | None = None) -> bytes:
    """用裸 socket 发一段**原始字节**体（畸形 JSON / 超限体专用）。

    `content_length` 可覆盖头里声明的长度：测「请求体过大」时头里声明超限、
    体**不必真发**——服务端在读体之前就按 400 拒了，真发 2 MB 只会撞上 EPIPE。
    """
    length = len(raw) if content_length is None else int(content_length)
    lines = ["POST %s HTTP/1.1" % path, "Host: 127.0.0.1",
             "Content-Type: application/json",
             "Content-Length: %d" % length]
    if sid:
        lines.append("X-Session: %s" % sid)
    for key, value in (headers or {}).items():
        lines.append("%s: %s" % (key, value))
    request = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + raw
    with socket.create_connection(("127.0.0.1", port), timeout=30) as sock:
        sock.sendall(request)
        pieces = []
        while True:
            piece = sock.recv(65536)
            if not piece:
                break
            pieces.append(piece)
        return b"".join(pieces)


def _status_lines(raw: bytes) -> int:
    """原始响应里以 `HTTP/` 开头的行数——出现第二行就是「第二套 HTTP 状态」。"""
    text = raw.decode("utf-8", "replace")
    return sum(1 for line in text.splitlines() if line.startswith("HTTP/"))


def _parse_sse(raw: bytes):
    """解析 SSE 体，返回 [(event, payload), …]（按出现顺序）。

    逐行扫、只在 `event:` 之后取紧跟的 `data:`——这样即使分块边界把 `\\r\\n`
    夹在事件之间也不会把行首认错。
    """
    events = []
    event = None
    for line in raw.decode("utf-8").replace("\r", "").split("\n"):
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:") and event is not None:
            data = line[len("data:"):].strip()
            events.append((event, json.loads(data) if data else None))
            event = None
    return events


class _FakeTransport:
    """假传输：记录每次请求体，按脚本返回或抛错（默认返回一段合格叙事）。"""

    def __init__(self, script=None):
        self.calls = []
        self.script = list(script or [])

    def __call__(self, url, payload, headers, *, timeout):
        self.calls.append({"url": url, "payload": payload, "headers": headers,
                           "timeout": timeout})
        step = self.script.pop(0) if self.script else {
            "content": _OK_NARRATION, "tool_calls": [],
            "usage": {"prompt_tokens": 7, "cached_tokens": 6,
                      "completion_tokens": 3, "reasoning_tokens": 0}}
        if isinstance(step, Exception):
            raise step
        return dict(step)


@contextlib.contextmanager
def _guide_online(env: dict, transmit):
    """把 prism_guide 的环境解析与传输层换成测试替身（只在**本进程**生效）。

    只打补丁、不写 `os.environ`：否则真子进程服务会继承到「已配置」的
    环境变量并真的去连上游——测试绝不能出网。
    """
    saved_env = prism_guide.load_env
    saved_tx = prism_guide.TRANSMIT
    prism_guide.load_env = lambda path=".env": dict(env)
    prism_guide.TRANSMIT = transmit
    try:
        yield transmit
    finally:
        prism_guide.load_env = saved_env
        prism_guide.TRANSMIT = saved_tx


_ONLINE_ENV = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
               "API_KEY": "secret123"}
_OFFLINE_ENV = {"BASE_URL": "", "MODEL": "mm", "API_KEY": "secret123"}


def _set_last_beats(sid: str, beats: list) -> None:
    """把 `last_beats` 写进磁盘上的存档（服务端会自己载入；绕开内存缓存）。"""
    session = notdnd_web.Session.load(sid)
    session.guide["last_beats"] = beats
    session.save()
    notdnd_web._sessions.pop(sid, None)


def _set_guide(sid: str, patch: dict) -> None:
    """按 `patch` 改磁盘上 `guide` 块的若干键（服务端随后自己载入）。"""
    session = notdnd_web.Session.load(sid)
    session.guide.update(patch)
    session.save()
    notdnd_web._sessions.pop(sid, None)


def _set_scene(sid: str, scene_id: str) -> None:
    """改磁盘上规则快照的 `scene.id`（§2.2：原检查点仍有效，测试可直接改快照）。"""
    session = notdnd_web.Session.load(sid)
    scene = dict(session.rules.get("scene") or {})
    scene["id"] = scene_id
    session.rules["scene"] = scene
    session.save()
    notdnd_web._sessions.pop(sid, None)


# 非流式 + 思考关且**无工具**：只有场外节拍请求长这样（叙事是 stream=true；
# 实相与工具预通行都是 `thinking` 为 enabled）。
def _is_trace_call(call) -> bool:
    payload = call["payload"]
    return (payload.get("stream") is False
            and payload.get("thinking") == {"type": "disabled"})


_BAD_JSON = {"content": "这不是 JSON", "tool_calls": [], "usage": {}}


def _sse_reply(text: str) -> dict:
    return {"content": text, "tool_calls": [], "usage": {"prompt_tokens": 1}}


def _tts_transport(chunks: list):
    """假 TTS 传输：回**真实形状**的 SSE 体，交给 prism_guide 组装。

    与叙事共用 `TRANSMIT` 这一个入口，所以「传输未被调用」数的是同一个计数器。
    每个 delta 的 base64 自带填充——组装要能把它们**分别**解码再拼接。
    """
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers,
                      "timeout": timeout})
        lines = []
        for chunk in chunks:
            data = base64.b64encode(chunk).decode("ascii")
            lines.append('data: {"choices":[{"delta":{"audio":{"data":"%s"}}}]}'
                         % data)
            lines.append("")
        lines.append('data: {"choices":[],"usage":{"prompt_tokens":4,'
                     '"completion_tokens":0}}')
        lines.append("")
        lines.append("data: [DONE]")
        lines.append("")
        return prism_guide.assemble_chat_stream("\n".join(lines).encode("utf-8"))

    fake.calls = calls
    return fake


def _guide_session(sid: str, *, auto_pass: bool = True,
                   combat: bool = False) -> "notdnd_web.Session":
    """造一局带一条行动的存档，写到磁盘（服务端会自己载入）。"""
    session = notdnd_web.Session(sid)
    rules = prism_core.RuleSession(sid)
    rules.party.append(prism_core.new_unit("u-1", "试炼者",
                                           attributes={"MGT": 6, "INS": 7}))
    action = {"id": "a-lock", "label": "撬锁", "kind": "check", "df": 12,
              "on_pass": "锁簧弹开。"}
    if auto_pass:
        action["auto_pass"] = True
    rules.scene = {"id": "sc-1", "actions": [action]}
    if combat:
        rules.combat = {"over": False}
    session.rules = rules.snapshot()
    session.save()
    notdnd_web._sessions.pop(sid, None)     # 丢掉缓存，服务端走真实读盘
    return session



# --------------------------------------------------------------------------
# 断言
# --------------------------------------------------------------------------


def check_import_has_no_side_effects():
    """`import notdnd_web` 不碰磁盘：存档目录不被创建。"""
    probe = os.path.join(_TMP, "import-probe", "saves")
    env = dict(os.environ)
    env["NOTDND_SAVE"] = probe
    result = subprocess.run([sys.executable, "-c", "import notdnd_web"],
                            cwd=ROOT, env=env, capture_output=True, text=True)
    assert result.returncode == 0, "import notdnd_web 失败：%s" % result.stderr
    assert not os.path.exists(probe), "import notdnd_web 不得创建存档目录"


def check_roundtrip_snapshot():
    """建规则会话 → 落盘（新档检查点）→ 重新载入 → 快照一致。"""
    sid = "roundtrip-1"
    snapshot = _sample_rules(sid)

    before = notdnd_web.Session(sid)
    before.rules = snapshot
    before.add_log("narrative", "开场旁白")
    before.save()

    on_disk = _read_save(sid)                  # campaigns/<sid>/snapshot.json
    assert on_disk["rules"] == snapshot, "规则快照必须真的落盘"
    assert on_disk["rules"]["party"], "落盘的快照不能是空壳"

    after = notdnd_web.Session.load(sid)      # 绕过内存缓存，走真实读盘路径
    assert after is not None, "落盘的存档必须能载入"
    assert after.rules == snapshot, "载入后的规则快照必须与写入前一致"
    assert after.seq == 1 and after.log[0]["text"] == "开场旁白", "工程字段不受影响"

    # 与规则层的契约对照：存档层不做二次清洗，形状完全由 prism_core 定义。
    assert prism_core.RuleSession.from_snapshot(snapshot).snapshot() == snapshot, \
        "prism_core 的快照往返必须幂等"


def check_legacy_save_lazy_migration():
    """老档（无 rules）惰性补空规则会话：只补不覆盖，也不就地改写磁盘。"""
    legacy = {
        "sid": "legacy-1",
        "created": 1700000000.0,
        "log": [{"seq": 1, "kind": "narrative", "text": "旧档旁白",
                 "speaker": "", "ts": 1700000000000}],
        "seq": 1,
        "save_name": "旧档",
    }
    _write_save(_save_path("legacy-1"), legacy)
    _write_save(_save_path("legacy-2"), {"sid": "legacy-2"})

    first = notdnd_web.Session.load("legacy-1")
    second = notdnd_web.Session.load("legacy-2")
    assert first is not None and second is not None, "老档必须能载入"
    assert first.save_name == "旧档", "已有字段不得被默认值覆盖"
    assert first.log and first.log[0]["text"] == "旧档旁白"
    assert first.rules == prism_core.RuleSession("legacy-1").snapshot(), \
        "缺 rules 的老档应补成空规则会话（sid 与存档对齐）"
    assert first.rules["party"] == [] and first.rules["pressure"] == 0
    assert first.rules["combat"] is None

    # 串档防线：两个老档拿到的不能是同一个可变对象（默认值必须是工厂）。
    first.rules["pressure"] = 9
    assert second.rules["pressure"] == 0, "不同存档不得共享同一份默认规则会话"

    # 「只补不覆盖」的另一半：迁移只发生在内存，磁盘上的老档保持原样。
    with open(_save_path("legacy-1"), encoding="utf-8") as handle:
        assert json.load(handle) == legacy, "惰性迁移不得就地改写磁盘上的老档"


def check_state_wiring_new_layout():
    """M7-3：`save()` 委托 `state`——新档是检查点，展示流走 `stream.cursor`。

    断言落盘真的换成了新档（`campaigns/<sid>/snapshot.json`）、`save()` **不**伪造
    事件（展示流不是事实，§11 两条独立序列）、重载后门面逐字段一致（规范化 JSON），
    以及 `state.open_campaign` 能独立打开同一档（是「委托」，不是另起一套格式）。
    """
    sid = "wiring-1"
    session = notdnd_web.Session(sid)
    session.rules = _sample_rules(sid)
    session.save_name = "接线局"
    session.created = 1700000000.0          # 固定建局时间：毫秒化后仍可逐字段比对
    session.add_log("narrative", "开场旁白")
    session.add_log("act", "我推开门")
    session.guide["salt"] = "f" * 32
    session.save()

    snapshot_path = _campaign_file(sid, "snapshot.json")
    assert os.path.isfile(snapshot_path), "新档必须落成 campaigns/<sid>/snapshot.json"
    assert not os.path.exists(_save_path(sid)), "新档不该再写老单文件"
    with open(snapshot_path, encoding="utf-8") as handle:
        snap = json.load(handle)
    assert snap["campaign_id"] == sid and snap["kind"] == "state.snapshot"
    assert snap["stream"]["cursor"] == session.seq == 2, snap["stream"]
    assert [e["text"] for e in snap["stream"]["log"]] == ["开场旁白", "我推开门"]
    assert snap["blocks"]["rules"] == _sample_rules(sid)
    assert snap["blocks"]["guide"]["salt"] == "f" * 32
    assert snap["created"] == 1700000000000, "门面的秒 → 新档的毫秒（§10.1）"
    # 展示流不进事件溯源：save() 不写事件（老档迁移才有一条 system.migration）
    with open(_campaign_file(sid, state.EVENTS_NAME), encoding="utf-8") as handle:
        assert handle.read().strip() == "", "save() 不得为展示流伪造事件"

    # 重载：门面逐字段一致；state 也能独立打开同一档
    notdnd_web._sessions.pop(sid, None)
    again = notdnd_web.Session.load(sid)
    assert again is not None, "新档必须能载入"
    assert _canon_session(again) == _canon_session(session), "往返必须逐字段一致"
    assert again.seq == 2 and again.log[1]["text"] == "我推开门"
    handle = state.open_campaign(sid, base_dir=str(notdnd_web.SAVE_DIR))
    assert handle.view()["stream"]["cursor"] == 2
    assert handle.view()["blocks"]["guide"]["salt"] == "f" * 32
    assert handle.view()["seq"] == 0, "没有事实事件时事件 seq 保持 0"
    assert handle.meta()["save_name"] == "接线局"


def check_legacy_migration_on_first_save():
    """M7-3 / §10：老档在**第一次 save** 时落成新档并留 `.bak`；此前只读不改写磁盘。

    断言：只读加载不动磁盘 → 各块逐字无损（rules / atlas / guide / log，salt 不变）
    → save 后 `.bak` 与老档**逐字相同**、老档被删、往返规范化 JSON 相等。
    """
    sid = "legacy-migrate-1"
    guide = {"salt": "0123456789abcdef0123456789abcdef",
             "realizations": {"l1": {"key": "v"}}}
    legacy = {
        "sid": sid, "created": 1700000000.0,
        "log": [{"seq": 1, "kind": "narrative", "text": "旧档旁白",
                 "speaker": "旁白", "ts": 1700000000000}],
        "seq": 1, "save_name": "旧档",
        "rules": _sample_rules(sid),
        "atlas": {"version": 1, "world_key": "yunji", "seed": 7, "setting_rev": "r1",
                  "party_locus": {"frame_id": "yunji/surface",
                                  "place_id": "yunji/loc-01"},
                  "frames": {}},
        "guide": guide,
    }
    path = _save_path(sid)
    _write_save(path, legacy)
    with open(path, "rb") as handle:
        legacy_bytes = handle.read()

    session = notdnd_web.Session.load(sid)
    assert session is not None, "老档必须能载入"
    # 只读加载：磁盘不动（迁移推迟到第一次 save，§10.1）
    with open(path, "rb") as handle:
        assert handle.read() == legacy_bytes, "只读加载不得改写老档"
    assert not os.path.exists(_campaign_file(sid, "snapshot.json")), \
        "老档 save 之前不该出现新档"
    # 各块逐字无损、salt 不变
    assert session.rules == legacy["rules"], "rules 必须逐字无损"
    assert session.atlas_block == legacy["atlas"], "atlas 必须逐字无损"
    assert session.guide["salt"] == guide["salt"], "salt 必须保留"
    assert session.guide["realizations"] == guide["realizations"]
    assert session.log == legacy["log"], "展示流必须逐条无损"
    assert session.seq == 1 and session.save_name == "旧档"
    assert session.created == 1700000000.0

    session.add_log("narrate", "新的旁白")      # 迁移与落盘在同一次 save 里发生
    session.save()

    with open(_campaign_file(sid, state.LEGACY_BAK_NAME), "rb") as handle:
        assert handle.read() == legacy_bytes, ".bak 必须是老档原文（逐字）"
    assert not os.path.exists(path), "迁移成功后老档应被删掉"
    snap = _read_save(sid)
    assert snap["sid"] == sid and snap["seq"] == 2
    assert snap["rules"] == legacy["rules"], "落盘的 rules 不得被迁移改写"
    assert snap["atlas"] == legacy["atlas"], "落盘的 atlas 不得被迁移改写"
    assert snap["guide"]["salt"] == guide["salt"], "落盘的 salt 必须不变"
    assert [e["text"] for e in snap["log"]] == ["旧档旁白", "新的旁白"]

    # 往返：重新载入后与保存前的门面态**规范化 JSON** 相等
    notdnd_web._sessions.pop(sid, None)
    again = notdnd_web.Session.load(sid)
    assert again is not None, "迁移后的新档必须能载入"
    assert _canon_session(again) == _canon_session(session), \
        "迁移往返必须逐字段一致（规范化 JSON）"


def check_save_list_and_delete_new_layout():
    """M7-3：列表与删除**两种布局都认**——新档按检查点列，删除收走整个目录。"""
    new_sid, legacy_sid = "list-new-1", "list-legacy-1"
    session = notdnd_web.Session(new_sid)
    session.save_name = "新档"
    session.add_log("narrative", "一")
    session.save()
    _write_save(_save_path(legacy_sid), {"sid": legacy_sid, "save_name": "老档",
                                         "log": [{"seq": 1, "text": "旧"}], "seq": 1})

    rows, total = notdnd_web.list_saves()
    by_id = {row["id"]: row for row in rows}
    assert by_id[new_sid]["name"] == "新档" and by_id[new_sid]["log_count"] == 1, \
        by_id.get(new_sid)
    assert by_id[legacy_sid]["name"] == "老档" and by_id[legacy_sid]["log_count"] == 1, \
        by_id.get(legacy_sid)
    assert total >= 2

    with _LocalServer() as server:
        status, body, _raw, _heads = server.post("/api/save/delete", {"sid": new_sid})
        assert status == 200 and body.get("status") == "ok", (status, body)
        # 老档删除同样成立（未迁移的档也能删）
        status, body, _raw, _heads = server.post("/api/save/delete",
                                                 {"sid": legacy_sid})
        assert status == 200 and body.get("status") == "ok", (status, body)
    camp = os.path.dirname(_campaign_file(new_sid, "snapshot.json"))
    assert not os.path.isdir(camp), "新档应整目录收走（检查点 / 日志 / 幂等副本）"
    assert not os.path.exists(_save_path(legacy_sid)), "老档文件应被删掉"
    assert notdnd_web.Session.load(new_sid) is None
    assert new_sid not in {row["id"] for row in notdnd_web.list_saves()[0]}


def check_api_session_view():
    """GET /api/session：真起服务，经 HTTP 拉规则视图（正常路径 + 老档）。"""
    sid = "api-1"
    snapshot = _sample_rules(sid)
    with _Server() as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        save_dir = info["save_dir"]
        assert save_dir and os.path.isdir(save_dir), "服务端应回传可用的 save_dir"

        # 造一局：用存档层的白名单序列化写文件（本期没有写接口，
        # /api/action 等留待数据与数值到位后再开）。
        session = notdnd_web.Session(sid)
        session.rules = snapshot
        session.save_name = "试炼局"
        session.add_log("narrative", "开场旁白")
        path = os.path.join(save_dir, "%s.json" % sid)
        _write_save(path, session.to_dict())
        with open(path, encoding="utf-8") as handle:
            before_bytes = handle.read()

        status, view = server.get("/api/session", sid=sid)
        assert status == 200, "已开会话应 200，实际 %s / %s" % (status, view)
        assert view["sid"] == sid, "规则视图的 sid 应与存档 sid 对齐"
        assert view["party"][0]["id"] == "u-1", "party 应来自规则快照"
        assert view["party"][0]["name"] == "试炼者"
        assert view["scene"]["id"] == "sc-1"
        assert view["active_unit_id"] == "u-1"
        assert view["pressure"] == 2
        assert view["combat"] is None
        assert view["done_actions"]["a-0"]["text"] == "已勘察"
        assert view["action_fails"]["a-1"] == 1
        assert view["save"]["name"] == "试炼局" and view["save"]["seq"] == 1

        with open(path, encoding="utf-8") as handle:
            assert handle.read() == before_bytes, "只读接口不得改写存档"

        # 老档（无 rules）同样能被拉取：迁移发生在载入路径上。
        _write_save(os.path.join(save_dir, "api-legacy.json"),
                    {"sid": "api-legacy", "created": 1.0, "log": [], "seq": 0})
        status, legacy_view = server.get("/api/session", sid="api-legacy")
        assert status == 200, "老档应能拉取，实际 %s / %s" % (status, legacy_view)
        assert legacy_view["sid"] == "api-legacy"
        assert legacy_view["party"] == [] and legacy_view["pressure"] == 0
        assert legacy_view["save"]["named"] is False and legacy_view["save"]["name"]


def check_api_session_without_session_400():
    """未开会话：无 X-Session 头 / sid 不存在 → 既有 400 语义。"""
    with _Server() as server:
        status, body = server.get("/api/session")
        assert status == 400, "无会话应 400，实际 %s / %s" % (status, body)
        assert body.get("error"), "400 应带 error 文案：%s" % body

        status, body = server.get("/api/session", sid="never-opened")
        assert status == 400, "没开过的 sid 应 400，实际 %s / %s" % (status, body)
        assert body.get("error")

        # 对照组：同一台服务上 /api/log 的 400 语义一致，说明这不是端点专属行为。
        status, _ = server.get("/api/log")
        assert status == 400, "/api/log 同样应 400，实际 %s" % status


# --------------------------------------------------------------------------
# ATLAS 存档块（切片 I4，§3.4）
# --------------------------------------------------------------------------


def _atlas_log_texts(session) -> list:
    """会话日志里 kind=atlas 的条目文本（搬迁 / 懒编译说明走这条通道）。"""
    return [e.get("text", "") for e in session.log if e.get("kind") == "atlas"]


def check_atlas_snapshot_roundtrip():
    """atlas 块随会话落盘 → 重新载入 → 队伍位置还在，移动也被记住。"""
    sid = "atlas-roundtrip"
    session = notdnd_web.Session(sid)
    session.ensure_atlas()
    block = session.to_dict()["atlas"]
    assert isinstance(block, dict), "ensure_atlas 之后必须能导出 §3.4 存档块"
    assert block["version"] == 1
    assert set(block) >= {"world_key", "seed", "setting_rev",
                          "party_locus", "frames"}, block.keys()
    assert block["world_key"] == notdnd_web._default_world_key()

    # 移动一次（出口列表来自视图，移动必然成功）
    exits = session.atlas_view()["exits"]
    assert exits, "编译出的世界至少要有一个出口"
    moved = session.atlas_move(exits[0]["via"])
    assert moved["status"] == "ok" and moved["band"] == exits[0]["band"]
    place_after_move = moved["here"]["place_id"]

    session.save()
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None, "带 atlas 块的存档必须能载入"
    reloaded.ensure_atlas()
    assert reloaded.atlas_view()["here"]["place_id"] == place_after_move, \
        "重新载入后队伍位置必须还在（§1 目标）"
    assert not any("搬迁" in t for t in _atlas_log_texts(reloaded)), \
        "位置仍在时不该出现搬迁说明"


def check_atlas_legacy_save_lazy_compile():
    """老档（无 atlas 块）懒编译：不报错、按当前世界编译，磁盘不动。"""
    sid = "atlas-legacy"
    legacy = {
        "sid": sid,
        "created": 1700000000.0,
        "log": [],
        "seq": 0,
        "save_name": "老档",
        "rules": prism_core.RuleSession(sid).snapshot(),
    }
    path = _save_path(sid)
    _write_save(path, legacy)

    session = notdnd_web.Session.load(sid)
    view = session.atlas_view()          # 惰性编译发生在查看出口的路径上
    assert view["here"]["place_id"], "懒编译后必须有队伍位置"
    assert view["here"]["world_key"] == notdnd_web._default_world_key()
    assert view["here"]["kind"] == "region", "新位置应是 authored 区域"
    texts = _atlas_log_texts(session)
    assert any("懒编译" in t for t in texts), "懒编译应写进会话日志：%s" % texts

    # 迁移只发生在内存：磁盘上的老档保持原样
    with open(path, encoding="utf-8") as handle:
        assert json.load(handle) == legacy, "懒编译不得就地改写磁盘上的老档"


def check_atlas_relocation_on_deleted_place():
    """存档里的队伍位置在世界 JSON 里已不存在：搬迁到仍存在的锚点并写日志。"""
    sid = "atlas-relocate"
    world_key = notdnd_web._default_world_key()
    world = notdnd_web._load_world(world_key)
    key = world["world"]["key"]
    real_region_ids = {r["id"] for r in world.get("regions", [])}
    surface = "%s/surface" % key
    block = {
        "version": 1,
        "world_key": world_key,
        "seed": 12345,
        "setting_rev": "stale-rev",
        "party_locus": {"frame_id": surface,
                        "place_id": "%s/gone-place" % key,
                        "x": 3, "y": 3, "z": 0},
        "frames": {surface: {"seed": None, "deltas": [
            {"op": "set_trait", "place_id": "%s/also-gone" % key,
             "payload": {"trait": "wilderness"}},
        ]}},
    }
    _write_save(_save_path(sid), {
        "sid": sid, "created": 1.0, "log": [], "seq": 0, "atlas": block,
        "rules": prism_core.RuleSession(sid).snapshot(),
    })

    session = notdnd_web.Session.load(sid)
    view = session.atlas_view()
    place_id = view["here"]["place_id"]
    assert place_id != "%s/gone-place" % key, "不能站在已删除的地点上"
    assert place_id.rsplit("/", 1)[1] in real_region_ids, \
        "搬迁目的地必须是当前世界仍存在的区域：%s" % place_id
    texts = _atlas_log_texts(session)
    assert any("搬迁" in t for t in texts), "搬迁必须写进会话日志：%s" % texts
    assert any("丢弃增量" in t for t in texts), \
        "指向已删除地点的增量应被丢弃并记录：%s" % texts
    assert any("setting_rev" in t for t in texts), \
        "setting_rev 变化应写进会话日志：%s" % texts


def check_api_atlas_exits_and_move():
    """HTTP 路径：出口列表是文本（不是图片），移动返回行程档与名义分钟（§5.2）。"""
    sid = "atlas-api"
    session = notdnd_web.Session(sid)
    session.ensure_atlas()
    with _Server() as server:
        # 存档要写进**服务端**的存档目录（子进程有自己的 NOTDND_SAVE）。
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        srv_path = os.path.join(info["save_dir"], "%s.json" % sid)
        _write_save(srv_path, session.to_dict())
        status, view = server.get("/api/atlas/exits", sid=sid)
        assert status == 200, "出口查看应 200，实际 %s / %s" % (status, view)
        assert view["text"].startswith("当前位置："), "返回必须带文本出口列表"
        assert isinstance(view["exits"], list) and view["exits"]
        for entry in view["exits"]:
            assert set(entry) >= {"via", "name", "place_id", "band"}, entry
            assert entry["minutes"] == notdnd_web.BAND_MINUTES.get(entry["band"], 0)

        # 移动：优先挑一条带行程档的出口（跨区），否则退到第一条
        target = next((e for e in view["exits"] if e["band"]), view["exits"][0])
        status, moved = server.post("/api/atlas/move",
                                    {"via": target["via"]}, sid=sid)
        assert status == 200, "移动应 200，实际 %s / %s" % (status, moved)
        assert moved["status"] == "ok"
        assert moved["band"] == target["band"], "行程档应来自连接的 band"
        assert moved["minutes"] == notdnd_web.BAND_MINUTES.get(target["band"], 0), \
            "名义分钟数必须与 §2.3（1 时段 = 360 分钟）/ travel.json 一致"
        assert moved["here"]["place_id"] == target["place_id"]

        # 移动结果要随存档块落盘（服务端目录）：重新读取后位置仍在
        on_disk = _read_save(sid, info["save_dir"])
        assert on_disk["atlas"]["party_locus"]["place_id"] == target["place_id"], \
            "移动后位置应随 atlas 块落盘"

        # 没有这个出口 → 既有 400 语义
        status, err = server.post("/api/atlas/move", {"via": "不存在方向"}, sid=sid)
        assert status == 400 and err.get("error"), (status, err)
# 导引者（G2）
# --------------------------------------------------------------------------


def check_guide_block_wiring():
    """`guide` 块四处接线：新档掷 salt、落盘、重启不变、L2 里没有 salt。"""
    sid = "guide-wire-1"
    first = notdnd_web.Session(sid)
    salt = first.guide["salt"]
    assert _SALT_RE.match(salt), "新存档的 guide.salt 必须是 32 位十六进制"
    # 形状归 prism_guide：网页层不得自己再列一遍键。
    assert set(first.guide.keys()) == set(prism_guide.empty_guide().keys())
    assert first.guide["realizations"] == {}
    first.save()

    on_disk = _read_save(sid)                  # 新档检查点（campaigns/<sid>/）
    assert on_disk["guide"]["salt"] == salt, "guide 必须真的落盘"

    again = notdnd_web.Session.load(sid)      # 绕过缓存，走真实读盘路径
    assert again is not None and again.guide["salt"] == salt, "重新载入后 salt 不变"
    assert again.guide["l2"] == "【检查点】\n账本：无\n"
    assert salt not in again.guide["l2"], "G2 的 salt 不得进入 L2"
    assert salt not in prism_guide.L0 and salt not in prism_guide.L1_UNBOUND

    # 默认值必须是**工厂**：两个新档不能共享同一个 guide dict。
    other = notdnd_web.Session("guide-wire-2")
    assert other.guide is not first.guide and other.guide["salt"] != salt
    assert other.guide["stats"] is not first.guide["stats"]


def check_guide_load_contract():
    """§7 加载合同：不是 dict / 脏键 / 缺键三种老档都要**按键**还原。"""
    fixed = "0123456789abcdef0123456789abcdef"

    # 1) `guide` 不是 dict，原值本身是 32 位十六进制 → 加载后的 salt 就是这串。
    _write_save(_save_path("guide-str"), {"sid": "guide-str", "guide": fixed})
    s = notdnd_web.Session.load("guide-str")
    assert s is not None and s.guide["salt"] == fixed, s.guide.get("salt")
    assert s.guide["l2"] == "【检查点】\n账本：无\n"

    # 2) `guide` 是 dict：realizations 合法、旁边一个键类型不对 → 只丢那个键。
    _write_save(_save_path("guide-mixed"), {"sid": "guide-mixed", "guide": {
        "salt": fixed,
        "l2_scene_id": "sc-1",
        "realizations": {"loc-02": {"source": "model"}},
        "world_key": 123,           # 类型不对 → 丢
        "voices": "not-a-dict",     # 类型不对 → 丢
        "nonsense": [1, 2, 3],      # 不认识 → 丢
    }})
    s = notdnd_web.Session.load("guide-mixed")
    assert s.guide["salt"] == fixed, "合法 salt 不得重掷"
    assert s.guide["realizations"] == {"loc-02": {"source": "model"}}, \
        "一个脏键不得连带删掉 realizations"
    assert s.guide["l2_scene_id"] == "sc-1"
    assert s.guide["world_key"] == "" and s.guide["voices"] == {}
    assert "nonsense" not in s.guide

    # 3) 缺 guide 的老档 → 补一份**新** salt 的块，且两个老档不共享。
    _write_save(_save_path("guide-none"), {"sid": "guide-none", "created": 1.0})
    a = notdnd_web.Session.load("guide-none")
    b = notdnd_web.Session.load("guide-none")
    assert _SALT_RE.match(a.guide["salt"]) and a.guide["salt"] != b.guide["salt"]
    assert a.guide is not b.guide


def check_guide_status_endpoint():
    """GET /api/guide/status：三个布尔，不需要会话；空 BASE_URL 全假。"""
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            status, body = server.get("/api/guide/status")
            assert status == 200, status
            assert body == {"chat": True, "tts": True, "configured": True}, body
            dumped = json.dumps(body, ensure_ascii=False)
            assert "secret123" not in dumped and "BASE_URL" not in dumped
            assert "api.example.com" not in dumped
        with _guide_online(_OFFLINE_ENV, _FakeTransport()):
            status, body = server.get("/api/guide/status")
            assert status == 200, status
            assert body == {"chat": False, "tts": False, "configured": False}, body


def check_guide_turn_validation():
    """`turn` 的状态行之前四步：长度 → 会话 → 速率 → 淡出整句。"""
    with _LocalServer() as server:
        with _guide_online(_OFFLINE_ENV, _FakeTransport()):
            # 长度：超过 2000 字 → 400「这句话太长」。
            _guide_session("g2-len")
            status, body, _raw, heads = server.post(
                "/api/guide/turn", {"text": "字" * 2001}, sid="g2-len")
            assert status == 400 and body.get("error") == "这句话太长", body
            assert "event-stream" not in heads.get("content-type", "")
            # 边界：正好 2000 字不算超。
            status, _body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "字" * 2000}, sid="g2-len")
            assert status == 200, status

            # 会话：没有 X-Session → 400。
            status, body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "我看看"})
            assert status == 400 and body.get("error"), body

            # 淡出 / 跳过是**整句相等**，且**不结算**（不调用 perform_action）。
            _guide_session("g2-fade")
            seen = []
            saved_call = prism_core.perform_action

            def counting(*args, **kwargs):
                seen.append(args)
                return saved_call(*args, **kwargs)

            prism_core.perform_action = counting
            try:
                status, body, _raw, _heads = server.post(
                    "/api/guide/turn",
                    {"text": "淡出", "action_id": "a-lock"}, sid="g2-fade")
                assert status == 200, status
                assert body["narration"]["text"] == notdnd_web.GUIDE_FADE_TEXT, body
                status, body, _raw, _heads = server.post(
                    "/api/guide/turn", {"text": "跳过这段"}, sid="g2-fade")
                assert status == 200
                assert body["narration"]["text"] == notdnd_web.GUIDE_SKIP_TEXT, body
            finally:
                prism_core.perform_action = saved_call
            assert seen == [], "淡出 / 跳过整句不得结算"

            # 单独的「跳过」不命中整句，走正常回合（本用例离线 → 兜底流）。
            status, _body, raw, heads = server.post(
                "/api/guide/turn", {"text": "跳过"}, sid="g2-fade")
            assert status == 200 and "event-stream" in heads.get("content-type", "")
            assert [name for name, _ in _parse_sse(raw)][0] == "fallback"

            # 速率：每 60 秒最多 12 次 turn，第 13 次 429。
            _guide_session("g2-rate")
            codes = [server.post("/api/guide/turn", {"text": "淡出"},
                                 sid="g2-rate")[0] for _ in range(12)]
            assert codes == [200] * 12, codes
            status, body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "淡出"}, sid="g2-rate")
            assert status == 429 and body.get("error") == "太频繁", body


def check_guide_turn_settle_stream():
    """有 `action_id`：先结算（落盘）→ HTTP/1.1 分块 → 审查后的叙事进 L3。"""
    sid = "g2-turn-1"
    _guide_session(sid)
    fake = _FakeTransport()
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
            assert status == 200, status
            assert "event-stream" in heads.get("content-type", ""), heads
            assert heads.get("transfer-encoding") == "chunked", heads
            assert "content-length" not in heads, "分块响应不得带 Content-Length"

            events = _parse_sse(raw)
            assert [name for name, _ in events] == \
                ["result", "narration", "usage", "done"], events
            result = events[0][1]
            assert result["status"] == "resolved" and result["passed"] is True
            assert result["auto"] is True             # auto_pass 免骰
            assert result["rolled"] is False and result["roll"] is None

            text = events[1][1]["text"]
            # 免骰且达成 → 裁决是服务端的「判定：成功」，不是模型自己写的。
            assert text.startswith("【裁决】判定：成功"), text
            assert "铁屑落在脚边" in text
            assert events[2][1] == {"prompt_tokens": 7, "cached_tokens": 6,
                                    "completion_tokens": 3,
                                    "reasoning_tokens": 0}, events[2][1]

            # 假传输收到的是叙事请求体：思考关、流式、密钥不入体、L0 在最前。
            assert len(fake.calls) == 1, fake.calls
            payload = fake.calls[0]["payload"]
            assert payload["thinking"] == {"type": "disabled"}
            assert payload["stream"] is True and payload["temperature"] == 0.7
            assert payload["model"] == "mm"
            assert fake.calls[0]["headers"] == {prism_guide.KEY_HEADER: "secret123"}
            assert payload["messages"][0]["role"] == "system"
            assert payload["messages"][0]["content"] == prism_guide.L0
            assert payload["messages"][-1]["role"] == "user"
            assert "我按住门栓" in payload["messages"][-1]["content"]
            assert "secret123" not in json.dumps(payload, ensure_ascii=False)

    # 重启后：结算看得见，L3 是审查后的文本。
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None
    assert "a-lock" in reloaded.rules["done_actions"], "结算必须落盘"
    transcript = reloaded.guide["transcript"]
    assert transcript and transcript[-1] == {"role": "assistant",
                                             "content": text}, transcript
    assert reloaded.guide["salt"] not in reloaded.guide["l2"]


def check_guide_turn_stream_failure():
    """头写出之后失败：先 `result` 再 `fallback`，且没有第二行 HTTP 状态。"""
    sid = "g2-fail-1"
    _guide_session(sid)
    fake = _FakeTransport([prism_guide.TransportError("boom")])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            raw = _raw_http(server.port, "/api/guide/turn",
                            {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
    assert _status_lines(raw) == 1, "头写出之后不得再发第二行 HTTP 状态"
    assert b"event-stream" in raw.split(b"\r\n\r\n", 1)[0]
    events = _parse_sse(_dechunk(raw))
    assert [name for name, _ in events] == ["result", "fallback", "done"], events
    fallback = events[1][1]["text"]
    assert fallback.startswith("【裁决】判定：成功"), fallback
    assert prism_guide.FALLBACK_NARRATION in fallback
    assert len(fake.calls) == 1

    reloaded = notdnd_web.Session.load(sid)
    # 结算仍落盘了；兜底句**不进** L3（L3 只存审查之后的模型文本）。
    assert "a-lock" in reloaded.rules["done_actions"]
    assert reloaded.guide["transcript"] == []


def check_guide_turn_review_failure_keeps_verdict():
    """记法对不上：裁决仍是 `roll["detail"]`，叙事是兜底句。"""
    sid = "g2-notation"
    _guide_session(sid, auto_pass=False)
    bad = {"content": "【裁决】x\n【叙事】你掷出 99d99 = 999，石壁塌了。",
           "tool_calls": [], "usage": {}}
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, _FakeTransport([bad])):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
    assert status == 200, status
    events = _parse_sse(raw)
    assert [name for name, _ in events] == ["result", "fallback", "done"], events
    result = events[0][1]
    assert result["rolled"] is True and isinstance(result["roll"], dict)
    fallback = events[1][1]["text"]
    assert fallback.startswith("【裁决】" + result["roll"]["detail"]), fallback
    assert prism_guide.FALLBACK_NARRATION in fallback
    assert "石壁塌了" not in fallback


def check_guide_turn_settle_errors_before_stream():
    """开头的 `ValueError` 在 SSE 之前变成固定 JSON（含 409）。"""
    with _LocalServer() as server:
        with _guide_online(_OFFLINE_ENV, _FakeTransport()):
            # 战斗没结束 → 409「战斗还没结束」，没有 SSE 头。
            _guide_session("g2-combat", auto_pass=False, combat=True)
            status, body, _raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "a-lock"}, sid="g2-combat")
            assert status == 409, (status, body)
            assert body.get("error") == "战斗还没结束", body
            assert "event-stream" not in heads.get("content-type", "")
            raw = _raw_http(server.port, "/api/guide/turn",
                            {"text": "我按住门栓", "action_id": "a-lock"},
                            sid="g2-combat")
            assert _status_lines(raw) == 1

            # 行动不存在 → 400「没有这个行动」。
            _guide_session("g2-noaction")
            status, body, _raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "nope"}, sid="g2-noaction")
            assert status == 400, (status, body)
            assert body.get("error") == "没有这个行动", body
            assert "event-stream" not in heads.get("content-type", "")


def check_guide_missing_module():
    """模块缺失：状态全假；`turn` 返回固定 JSON 兜底且**不结算**。"""
    with _LocalServer() as server:
        saved_module = notdnd_web.prism_guide
        notdnd_web.prism_guide = None
        try:
            status, body = server.get("/api/guide/status")
            assert status == 200, status
            assert body == {"chat": False, "tts": False, "configured": False}, body

            _guide_session("g2-nomod")
            seen = []
            saved_call = prism_core.perform_action

            def counting(*args, **kwargs):
                seen.append(args)
                return saved_call(*args, **kwargs)

            prism_core.perform_action = counting
            try:
                status, body, _raw, heads = server.post(
                    "/api/guide/turn",
                    {"text": "我按住门栓", "action_id": "a-lock"}, sid="g2-nomod")
            finally:
                prism_core.perform_action = saved_call
            assert status == 200, status
            assert body["narration"]["text"] == notdnd_web.FALLBACK_NARRATION, body
            assert "event-stream" not in heads.get("content-type", "")
            assert seen == [], "模块缺失时不得结算"
        finally:
            notdnd_web.prism_guide = saved_module


# --------------------------------------------------------------------------
# 导引者朗读（G3，§5.9 / §5.10）
# --------------------------------------------------------------------------


def _beats_fixture() -> list:
    """两条拍：一条旁白（白桦）、一条由夹具标成茉莉的对白。"""
    return [
        {"text": "风停了。", "voice": notdnd_web.prism_guide.VOICE_NARRATOR,
         "tone": "平叙", "channel": "speech", "speaker": ""},
        {"text": "有人应了一声。", "voice": "茉莉", "tone": "平叙",
         "channel": "speech", "speaker": "npc-02"},
    ]


def check_guide_turn_writes_beats():
    """`turn` 切拍：随 `narration` 回给浏览器，并写进 `last_beats`（§5.8 / §5.9）。"""
    sid = "g3-turn-beats"
    _guide_session(sid)
    narration = ("【裁决】你把手按上门栓。\n"
                 "【叙事】风从巷口灌进来。霍砚：「别出声。」\n"
                 "【钩子】巷口有人影。")
    fake = _FakeTransport([{"content": narration, "tool_calls": [],
                            "usage": {"prompt_tokens": 3}}])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
    assert status == 200, status
    payload = dict(_parse_sse(raw))["narration"]
    assert payload["text"].startswith("【裁决】判定：成功"), payload["text"]
    beats = payload["beats"]
    assert [b["text"] for b in beats] == \
        ["风从巷口灌进来。", "别出声。", "\n\n巷口有人影。"], beats
    # G3 还不按 `npc.id` 分配声线（那是 G7）：一律白桦。
    assert all(b["voice"] == notdnd_web.prism_guide.VOICE_NARRATOR for b in beats)
    assert all(b["tone"] == "平叙" and b["channel"] == "speech" for b in beats)
    assert all(b["speaker"] == "" for b in beats)
    # 服务端的裁决那一行不朗读。
    assert not any("【裁决】" in b["text"] or "判定：" in b["text"] for b in beats)

    reloaded = notdnd_web.Session.load(sid)
    assert reloaded.guide["last_beats"] == beats, \
        "节拍必须随存档落盘——speak 只认 last_beats"


def check_guide_speak_validation():
    """`speak` 的状态行之前：长度 → 会话 → 速率 → 语音可用 → 全文匹配。

    验收：不在 `last_beats` 里的文本返回 400，**且传输未被调用**。
    """
    sid = "g3-speak-bad"
    _guide_session(sid)
    _set_last_beats(sid, _beats_fixture())
    fake = _tts_transport([b"\x01\x02"])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            # 不在 last_beats 里 → 400「没有可朗读的句子」，传输未被调用。
            status, body, _raw, heads = server.post(
                "/api/guide/speak", {"text": "换一句吧。"}, sid=sid)
            assert status == 400, (status, body)
            assert body.get("error") == "没有可朗读的句子", body
            assert "audio/" not in heads.get("content-type", ""), heads
            assert fake.calls == [], "没匹配上就不许调用传输"

            # 近似但不全等（少一个字 / 多一个空格 / 空文本）同样不算命中。
            for wrong in ("风停了", " 风停了。", ""):
                status, body, _raw, _heads = server.post(
                    "/api/guide/speak", {"text": wrong}, sid=sid)
                assert status == 400 and body.get("error") == "没有可朗读的句子", \
                    (wrong, status, body)
            assert fake.calls == []

            # 没有 X-Session → 400；超长 → 400「这句话太长」。
            status, body, _raw, heads = server.post("/api/guide/speak",
                                                    {"text": "风停了。"})
            assert status == 400 and body.get("error"), body
            assert "audio/" not in heads.get("content-type", "")
            status, body, _raw, _heads = server.post(
                "/api/guide/speak", {"text": "字" * 2001}, sid=sid)
            assert status == 400 and body.get("error") == "这句话太长", body
            assert fake.calls == []

        # 没配 BASE_URL / 密钥：语音不可用，仍然**不请求**（§5.1 没有第二套降级）。
        with _guide_online(_OFFLINE_ENV, fake):
            status, body, _raw, _heads = server.post(
                "/api/guide/speak", {"text": "风停了。"}, sid=sid)
            assert status == 400 and body.get("error") == "语音不可用", body
            assert fake.calls == []


def check_guide_speak_stream():
    """`speak` 合法路径：HTTP/1.1 + 分块 + 24 kHz / pcm16 头 + 拼接后的 PCM。"""
    sid = "g3-speak-ok"
    _guide_session(sid)
    _set_last_beats(sid, _beats_fixture())
    pcm_a, pcm_b = b"\x01\x02", b"\x03\x04\x05\x06"
    fake = _tts_transport([pcm_a, pcm_b])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, heads = server.post(
                "/api/guide/speak", {"text": "风停了。"}, sid=sid)
            assert status == 200, status
            assert raw == pcm_a + pcm_b, (raw, "每个 delta 单独解码后按序拼接")
            assert heads.get("content-type") == "audio/pcm16", heads
            assert heads.get("x-audio-format") == "pcm16", heads
            assert heads.get("x-audio-sample-rate") == "24000", heads
            assert heads.get("transfer-encoding") == "chunked", heads
            assert "content-length" not in heads, "分块响应不得带 Content-Length"

            # 请求体是 TTS 形状：台词在 assistant、user 是平叙卡、密钥不入体。
            payload = fake.calls[-1]["payload"]
            assert payload["model"] == "mimo-v2.5-tts"
            assert payload["stream"] is True
            assert payload["audio"] == {"format": "pcm16"}
            assert payload["messages"][0] == {"role": "user", "content": "平叙"}
            assert payload["messages"][1] == {"role": "assistant",
                                              "content": "风停了。"}
            assert payload["voice"] == notdnd_web.prism_guide.VOICE_NARRATOR
            assert fake.calls[-1]["headers"] == \
                {prism_guide.KEY_HEADER: "secret123"}
            assert "secret123" not in json.dumps(payload, ensure_ascii=False)

            # 夹具把这一拍标成茉莉：音色**取自那一拍**，不按 id 重算（分配是 G7）。
            status, _body, raw2, _heads = server.post(
                "/api/guide/speak", {"text": "有人应了一声。"}, sid=sid)
            assert status == 200 and raw2 == pcm_a + pcm_b, (status, raw2)
            assert fake.calls[-1]["payload"]["voice"] == "茉莉"
            assert fake.calls[-1]["payload"]["messages"][1]["content"] == \
                "有人应了一声。"

            # 客户端多传的字段被忽略（§5.8）。
            status, _body, raw3, _heads = server.post(
                "/api/guide/speak",
                {"text": "风停了。", "voice": "茉莉", "tone": "激昂"}, sid=sid)
            assert status == 200 and raw3 == pcm_a + pcm_b
            assert fake.calls[-1]["payload"]["voice"] == "白桦"

            # 裸 socket：整条响应只有**一行** HTTP 状态，头块里 24000 / pcm16 都在。
            raw_http = _raw_http(server.port, "/api/guide/speak",
                                 {"text": "风停了。"}, sid=sid)
    assert _status_lines(raw_http) == 1, "音频流也不许发第二行 HTTP 状态"
    head_block = raw_http.split(b"\r\n\r\n", 1)[0]
    assert b"HTTP/1.1 200" in head_block, head_block
    assert b"pcm16" in head_block and b"24000" in head_block, head_block
    assert b"Transfer-Encoding: chunked" in head_block, head_block
    assert _dechunk(raw_http) == pcm_a + pcm_b, "分块体还原后仍是 PCM 相接"


# --------------------------------------------------------------------------
# 导引者工具环（G4，§5.2 / §5.5 / §5.6）
# --------------------------------------------------------------------------


def _tool_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)}}


def _thinking_open_calls(fake) -> list:
    """「思考开且 stream 为 false」的调用——按 §5.5 只允许是工具预通行。"""
    return [call for call in fake.calls
            if call["payload"].get("stream") is False
            and (call["payload"].get("thinking") or {}).get("type") == "enabled"]


def _narrative_calls(fake) -> list:
    return [call for call in fake.calls if call["payload"].get("stream") is True]


def check_guide_tool_prepass_requests():
    """`查规则` 开一次预通行；`我想查规则` 不开。叙事请求保持思考关、无 role: tool。"""
    sid = "g4-prepass"
    _guide_session(sid)
    # 脚本：预通行第 1 轮要一只工具 → 第 2 轮不要 → 叙事。
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("lookup_rule",
                                                  {"kind": "system.guardrails"})],
         "usage": {}, "reasoning": "先查护栏"},
        {"content": "", "tool_calls": [], "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn", {"text": "查规则"}, sid=sid)
            assert status == 200, status
            assert dict(_parse_sse(raw))["narration"]["text"].startswith("【裁决】")

            assert len(fake.calls) == 3, [c["payload"].get("stream")
                                          for c in fake.calls]
            # 「思考开且 stream 为 false」的调用**全部**属于预通行：这里是它的
            # 两轮（第 1 轮要工具、第 2 轮收口），叙事请求在最后、思考是关的。
            opens = _thinking_open_calls(fake)
            assert opens == fake.calls[:2], [c["payload"].get("stream")
                                             for c in fake.calls]
            assert all(call["payload"]["tools"] == prism_guide.TOOLS
                       for call in opens)
            # 预通行请求体（§5.5 表）：思考开、无 temperature、1024、非流式、带工具。
            head = fake.calls[0]["payload"]
            assert head["thinking"] == {"type": "enabled"}
            assert "temperature" not in head
            assert head["max_completion_tokens"] == 1024
            assert head["stream"] is False
            assert head["tools"] == prism_guide.TOOLS
            assert head["model"] == "mm"
            assert fake.calls[0]["headers"] == {prism_guide.KEY_HEADER: "secret123"}
            # 第 2 轮的往返：助手消息带 reasoning_content，工具正文是规则摘要。
            round2 = fake.calls[1]["payload"]["messages"]
            assert round2[-2]["role"] == "assistant"
            assert round2[-2]["reasoning_content"] == "先查护栏"
            assert round2[-1]["role"] == "tool"
            assert round2[-1]["tool_call_id"] == "call-1"
            # 工具正文是那只 kind 的规则摘要（文件自己的 note 打头），且已截到上限。
            tool_text = round2[-1]["content"]
            assert tool_text.startswith("内容护栏"), tool_text[:40]
            assert len(tool_text) <= prism_guide.LOOKUP_RULE_LIMIT

            # 叙事请求：思考关、流式、重拼 L0–L4（无 role: tool、无 reasoning_content）。
            narrative = _narrative_calls(fake)
            assert len(narrative) == 1
            # 预通行确实挂在**叙事消息数组**上（两边逐条相同）。
            assert opens[0]["payload"]["messages"] == narrative[0]["payload"]["messages"]
            payload = narrative[0]["payload"]
            assert payload["thinking"] == {"type": "disabled"}
            assert payload["stream"] is True and payload["temperature"] == 0.7
            blob = json.dumps(payload, ensure_ascii=False)
            assert "先查护栏" not in blob and "reasoning_content" not in blob
            assert not any(msg.get("role") == "tool" for msg in payload["messages"])
            assert not any("tool_calls" in msg for msg in payload["messages"])

            # `我想查规则` 不是整句 → 不开预通行（只有叙事那一次调用）。
            fake.calls.clear()
            status, _body, raw, _heads = server.post(
                "/api/guide/turn", {"text": "我想查规则"}, sid=sid)
            assert status == 200, status
            assert len(fake.calls) == 1, [c["payload"].get("stream")
                                          for c in fake.calls]
            assert fake.calls[0]["payload"]["thinking"] == {"type": "disabled"}
            assert _thinking_open_calls(fake) == []

            # 本回合已经有机械结果（带 action_id）→ 也不开预通行。
            # （`a-lock` 是 auto_pass，免骰，叙事里没有新掷骰。）
            fake.calls.clear()
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "查规则", "action_id": "a-lock"}, sid=sid)
            assert status == 200, status
            assert len(fake.calls) == 1, [c["payload"].get("stream")
                                          for c in fake.calls]
            assert _thinking_open_calls(fake) == []


def check_guide_tool_error_never_new_status():
    """工具里的 `ValueError` 变成固定短语回给模型，**不**产生第二行 HTTP 状态。"""
    sid = "g4-tool-error"
    _guide_session(sid)
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("request_check",
                                                  {"action_id": "nope"})],
         "usage": {}},
        {"content": "", "tool_calls": [], "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            raw = _raw_http(server.port, "/api/guide/turn", {"text": "查规则"},
                            sid=sid)
    assert _status_lines(raw) == 1, "工具失败不得再写一行 HTTP 状态"
    assert b"event-stream" in raw.split(b"\r\n\r\n", 1)[0]
    events = _parse_sse(_dechunk(raw))
    assert [name for name, _ in events] == ["narration", "usage", "done"], events
    # 工具正文就是那三种固定短语之一（与网页层的映射同文）。
    assert len(fake.calls) == 3, fake.calls
    tool_msg = fake.calls[1]["payload"]["messages"][-1]
    assert tool_msg["role"] == "tool"
    assert tool_msg["content"] == "没有这个行动", tool_msg
    assert tool_msg["tool_call_id"] == "call-1"

    # 战斗没结束 → 「战斗还没结束」（同一张固定短语表）。
    sid2 = "g4-tool-error-combat"
    _guide_session(sid2, auto_pass=True, combat=True)
    fake2 = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("request_check",
                                                  {"action_id": "a-lock"})],
         "usage": {}},
        {"content": "", "tool_calls": [], "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake2):
            status, _body, raw2, _heads = server.post(
                "/api/guide/turn", {"text": "查规则"}, sid=sid2)
    assert status == 200, status
    assert fake2.calls[1]["payload"]["messages"][-1]["content"] == "战斗还没结束"


def check_guide_tool_prepass_rebuilds_l4():
    """§5.4：预通行里又 `settle` 了，叙事用的 L4 必须用**写回之后**的快照重拼。"""
    sid = "g4-rebuild"
    _guide_session(sid)
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("request_check",
                                                  {"action_id": "a-lock"})],
         "usage": {}},
        {"content": "", "tool_calls": [], "usage": {}},
    ])
    built = []
    saved = prism_guide.build_narrative_messages

    def counting(*args, **kwargs):
        built.append(args)
        return saved(*args, **kwargs)

    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            prism_guide.build_narrative_messages = counting
            try:
                status, _body, _raw, _heads = server.post(
                    "/api/guide/turn", {"text": "查规则"}, sid=sid)
            finally:
                prism_guide.build_narrative_messages = saved
    assert status == 200, status
    # 开预通行的那一回合构建两次 L0–L4：一次给预通行，一次是 settle 之后的重拼。
    assert len(built) == 2, "预通行 settle 之后必须重拼一次（§5.4）"
    # 工具里的结算真的落盘了（同一个 settle）。
    reloaded = notdnd_web.Session.load(sid)
    assert "a-lock" in reloaded.rules["done_actions"], "工具里的 request_check 要落盘"
    # 且没有因此多开一轮预通行（每回合最多一次）。
    assert len(_thinking_open_calls(fake)) == 2


def check_guide_narrative_tool_calls_no_second_round():
    """叙事完成带 `tool_calls` → 算叙事失败，**不再开一轮**（§5.2）。"""
    sid = "g4-narr-tool"
    _guide_session(sid)
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("lookup_rule",
                                                  {"kind": "system.adjudication"})],
         "usage": {}},
        {"content": "", "tool_calls": [], "usage": {}},
        # 叙事那一次带着 tool_calls 回来：只兜底，不再请求。
        {"content": "【叙事】风停了。",
         "tool_calls": [_tool_call("lookup_rule", {"kind": "system.tone_packs"})],
         "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn", {"text": "查规则"}, sid=sid)
    assert status == 200, status
    events = _parse_sse(raw)
    assert [name for name, _ in events] == ["fallback", "done"], events
    assert prism_guide.FALLBACK_NARRATION in events[0][1]["text"]
    assert len(fake.calls) == 3, "叙事带 tool_calls 不得再开一轮"
    # 兜底文本不进 L3。
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded.guide["transcript"] == []


def check_guide_speak_rate_limit():
    """每会话每 60 秒最多 30 次 `speak`，第 31 次 429「太频繁」（§5.10）。"""
    sid = "g3-speak-rate"
    _guide_session(sid)
    _set_last_beats(sid, _beats_fixture())
    fake = _tts_transport([b"\x01\x02"])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            codes = [server.post("/api/guide/speak", {"text": "风停了。"},
                                 sid=sid)[0] for _ in range(30)]
            assert codes == [200] * 30, codes
            status, body, _raw, heads = server.post(
                "/api/guide/speak", {"text": "风停了。"}, sid=sid)
            assert status == 429 and body.get("error") == "太频繁", (status, body)
            assert "audio/" not in heads.get("content-type", "")
            assert len(fake.calls) == 30, "超限那一次不得调用传输"


# --------------------------------------------------------------------------
# 导引者绑定与焦点（G5，§2.1 / §6）
# --------------------------------------------------------------------------


def check_guide_bind_endpoint():
    """`POST /api/guide/bind`：四种结果；绑定后 L1 才是这场剧本的典范卡。

    绑定走**真文件**（`data/scenarios/yunji.json`，`meta.world` = `yunji`），
    不伪造剧本；断言的都是「重启之后还在」的落盘状态。
    """
    sid = "g5-bind-1"
    _guide_session(sid)
    with _LocalServer() as server:
        # 没有 X-Session → 400（不允许匿名绑定）。
        status, body, _raw, _heads = server.post(
            "/api/guide/bind", {"world_key": "yunji", "scenario_id": "yunji"})
        assert status == 400 and body.get("error"), (status, body)

        # 文件不存在 / id 不是文件名主干 → 400「没有这场战役」。
        for bad in ("nope", "../etc/passwd", "Yunji", "九钥与元柜"):
            status, body, _raw, _heads = server.post(
                "/api/guide/bind", {"world_key": "yunji", "scenario_id": bad},
                sid=sid)
            assert status == 400 and body.get("error") == "没有这场战役", \
                (bad, status, body)

        # `world_key` 与 `meta.world` 不一致 → 400「世界对不上」，不写 scenario_id。
        status, body, _raw, _heads = server.post(
            "/api/guide/bind",
            {"world_key": "threshold", "scenario_id": "yunji"}, sid=sid)
        assert status == 400 and body.get("error") == "世界对不上", (status, body)
        session = notdnd_web.get_session(sid)
        assert session.guide["scenario_id"] == ""
        assert session.guide["world_key"] == ""
        assert session.guide["l1_key"] == "unloaded"
        assert prism_guide.l1_for(session.guide) == prism_guide.L1_UNBOUND

        salt = session.guide["salt"]
        status, body, _raw, _heads = server.post(
            "/api/guide/bind", {"world_key": "yunji", "scenario_id": "yunji"},
            sid=sid)
        assert status == 200, (status, body)
        assert body == {"status": "ok", "world_key": "yunji",
                        "scenario_id": "yunji"}, body
        session = notdnd_web.get_session(sid)
        assert session.guide["scenario_id"] == "yunji"
        assert session.guide["l1_key"] == "yunji"
        assert session.guide["salt"] == salt, "绑定不得重掷 salt"

        # 幂等：同一对 id 再绑一次仍 200，salt 不变。
        status, body, _raw, _heads = server.post(
            "/api/guide/bind", {"world_key": "yunji", "scenario_id": "yunji"},
            sid=sid)
        assert status == 200, (status, body)
        assert notdnd_web.get_session(sid).guide["salt"] == salt

        # 另一场（three_wooden_boxes 的 meta.world 也是 yunji）→ 409「已经绑定」。
        status, body, _raw, _heads = server.post(
            "/api/guide/bind",
            {"world_key": "yunji", "scenario_id": "three_wooden_boxes"}, sid=sid)
        assert status == 409 and body.get("error") == "已经绑定", (status, body)
        assert notdnd_web.get_session(sid).guide["scenario_id"] == "yunji"

        # 绑定之后，叙事请求的第 2 条消息就是这场剧本的典范卡。
        fake = _FakeTransport()
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        assert status == 200, status
        l1 = fake.calls[0]["payload"]["messages"][1]["content"]
        assert l1 == prism_guide.l1_for(notdnd_web.get_session(sid).guide), l1[:80]
        for needle in ("白壁", "绳会账房", "npc-01"):
            assert needle in l1, needle
        assert "尚未选择战役" not in l1
        assert salt not in l1 and "docs/" not in l1

    # 重启之后 binding 与焦点仍在（落盘为证）；两份存档的 L1 全等。
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None
    assert reloaded.guide["scenario_id"] == "yunji"
    assert reloaded.guide["world_key"] == "yunji"
    assert reloaded.guide["salt"] == salt
    other = _guide_session("g5-bind-2")
    other.guide["world_key"] = "yunji"
    other.guide["scenario_id"] = "yunji"
    other.save()
    assert prism_guide.l1_for(reloaded.guide) == prism_guide.l1_for(other.guide), \
        "同一剧本的两份存档，L1 必须全等"


def check_guide_bind_no_module():
    """导引者模块缺失：绑定给固定 400，不崩、不落盘。"""
    with _LocalServer() as server:
        saved_module = notdnd_web.prism_guide
        notdnd_web.prism_guide = None
        try:
            sid = "g5-bind-nomod"
            _guide_session(sid)
            status, body, _raw, heads = server.post(
                "/api/guide/bind",
                {"world_key": "yunji", "scenario_id": "yunji"}, sid=sid)
            assert status == 400 and body.get("error"), (status, body)
            assert "event-stream" not in heads.get("content-type", "")
            # 模块缺失时 `guide` 只有本地最小块：绑定当然没有落下任何 id。
            assert not notdnd_web.Session.load(sid).guide.get("scenario_id")
        finally:
            notdnd_web.prism_guide = saved_module


def check_guide_turn_location_focus():
    """`turn` 的可选 `location_id`：状态行之前校验、写焦点、恰调用一次实相。"""
    sid = "g5-focus-1"
    _guide_session(sid)
    seen = []
    saved_realization = prism_guide.ensure_realization

    def counting(session, location_id):
        seen.append(location_id)
        return saved_realization(session, location_id)

    with _LocalServer() as server:
        # 没绑定的会话带 location_id → 400（没有可对上的剧本），且不调用实相。
        _guide_session("g5-focus-unbound")
        prism_guide.ensure_realization = counting
        try:
            status, body, _raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我走进白壁", "location_id": "loc-02"},
                sid="g5-focus-unbound")
            assert status == 400 and body.get("error") == "没有这个地点", \
                (status, body)
            assert "event-stream" not in heads.get("content-type", ""), heads
            assert seen == [], seen

            status, body, _raw, _heads = server.post(
                "/api/guide/bind",
                {"world_key": "yunji", "scenario_id": "yunji"}, sid=sid)
            assert status == 200, (status, body)

            # 非法 id（包括战役节点名）→ 400，没有 SSE，实相没被调用。
            for bad in ("loc-99", "whitewall", "白壁"):
                status, body, _raw, heads = server.post(
                    "/api/guide/turn",
                    {"text": "我走进白壁", "location_id": bad}, sid=sid)
                assert status == 400 and body.get("error") == "没有这个地点", \
                    (bad, status, body)
                assert "event-stream" not in heads.get("content-type", ""), heads
            assert seen == [], seen

            # 合法 id：状态行之后、叙事之前调用一次；上游只有叙事那一次。
            fake = _FakeTransport()
            with _guide_online(_ONLINE_ENV, fake):
                status, _body, raw, _heads = server.post(
                    "/api/guide/turn",
                    {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
            assert status == 200, status
            assert seen == ["loc-02"], seen
            assert [name for name, _ in _parse_sse(raw)] == \
                ["narration", "usage", "done"], _parse_sse(raw)
            # G6 起，带 location_id 的回合会先发一次**非流式**实相请求（假传输
            # 第一次回的不是合法实相 JSON，服务端按 §5.7 重试一次），之后才是叙事
            # 请求。这里只锁「叙事请求恰有一次」；实相请求的形状与次数归
            # `tests/test_prism_guide.py` 自己测。
            narrative_calls = [call for call in fake.calls
                               if call["payload"].get("stream") is True]
            assert len(narrative_calls) == 1, \
                [call["payload"].get("stream") for call in fake.calls]
            assert notdnd_web.get_session(sid).guide["focus_location_id"] == "loc-02"

            # 不带 location_id：焦点不动，实相不再被调用。
            seen.clear()
            with _guide_online(_ONLINE_ENV, _FakeTransport()):
                status, _body, _raw, _heads = server.post(
                    "/api/guide/turn", {"text": "我看看四周"}, sid=sid)
            assert status == 200 and seen == [], seen
            assert notdnd_web.get_session(sid).guide["focus_location_id"] == "loc-02"
        finally:
            prism_guide.ensure_realization = saved_realization

    # 焦点落盘：重启之后（G6 的下一回合）还看得到队伍走到了哪里。
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded.guide["focus_location_id"] == "loc-02", \
        reloaded.guide["focus_location_id"]
    assert reloaded.guide["scenario_id"] == "yunji"


def _bind(server, sid):
    status, body, _raw, _heads = server.post(
        "/api/guide/bind", {"world_key": "yunji", "scenario_id": "yunji"},
        sid=sid)
    assert status == 200, (status, body)


def _npc(scenario_id, npc_id):
    data = prism_guide.load_scenario(scenario_id) or {}
    for item in data.get("npcs") or []:
        if item.get("id") == npc_id:
            return item
    raise AssertionError("找不到 %s 里的 %s" % (scenario_id, npc_id))


def check_guide_turn_traces_wiring():
    """G7 接线：焦点变化回合 = 1 次场外（思考关 / 非流式 / 无工具）+ 1 次叙事；
    `narration` 至多一条 `trace`；L3 无秘密（全文与 8 码位都查）。"""
    sid = "g7-trace-wire"
    _guide_session(sid)
    huo = _npc("yunji", "npc-01")
    secret = huo["secret"]
    window = secret[:prism_guide.SECRET_WINDOW]
    narration = ("【裁决】判定：成功\n\n"
                 "【叙事】霍砚低声说他是守钥人计霜的儿子。温苔：「灯还亮着。」\n\n"
                 "【钩子】泉声还在响。")
    with _LocalServer() as server:
        _bind(server, sid)
        # 回合 A：无焦点，只建立 scene 检查点（不记痕）。
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            assert server.post("/api/guide/turn", {"text": "我看看四周"},
                               sid=sid)[0] == 200
        # 回合 B：第一次设焦点 loc-02（previous 空 → 不算变化，不记痕）。
        with _guide_online(_ONLINE_ENV,
                           _FakeTransport([_BAD_JSON, _BAD_JSON,
                                           _sse_reply(_OK_NARRATION)])):
            assert server.post(
                "/api/guide/turn",
                {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)[0] == 200
        assert notdnd_web.get_session(sid).guide["focus_location_id"] == "loc-02"
        # 回合 C：焦点 loc-02 → loc-05，才是真正的「离开」。先埋一条未揭开的痕迹。
        _set_guide(sid, {"traces": [{"at": "loc-02", "id": "npc-01",
                                     "text": "他换了把新锁。", "revealed": False}]})
        fake = _FakeTransport([_BAD_JSON, _BAD_JSON, _BAD_JSON,
                               _sse_reply(narration)])
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我走进灰市", "location_id": "loc-05"}, sid=sid)
        assert status == 200, status
        streams = [call["payload"].get("stream") for call in fake.calls]
        trace_calls = [call for call in fake.calls if _is_trace_call(call)]
        narrative_calls = [call for call in fake.calls
                           if call["payload"].get("stream") is True]
        assert len(trace_calls) == 1, streams
        assert len(narrative_calls) == 1, streams
        body = trace_calls[0]["payload"]
        assert body["thinking"] == {"type": "disabled"}
        assert body["stream"] is False and "tools" not in body
        user = body["messages"][1]["content"]
        assert secret not in user, "秘密全文不得进场外请求的 user"
        assert "霍砚" in user and huo["drive"] in user, user
        # narration：至多一条 trace，且是埋下的那条。
        event = dict(_parse_sse(raw))["narration"]
        assert event["trace"] == "他换了把新锁。", event.get("trace")
        # 秘密门：玩家看到的叙事与 L3 都没有秘密全文、也没有 8 码位。
        assert "……" in event["text"], event["text"]
        assert secret not in event["text"] and window not in event["text"]
        assert "灯还亮着。" in event["text"], "点出别人名字的对白要留下"
        session = notdnd_web.get_session(sid)
        l3 = "".join(entry["content"] for entry in session.guide["transcript"])
        assert secret not in l3 and window not in l3, l3
        # 切拍从涂掉之后的字里来；说话人按 npc.id 分配声线。
        speakers = {beat["speaker"] for beat in event["beats"] if beat["speaker"]}
        assert speakers == {"npc-02"}, event["beats"]
        assert session.guide["voices"]["npc-02"] == \
            prism_guide.npc_voice("npc-02") == "茉莉"
        # `revealed` 随同一次 save() 落盘。
        assert session.guide["traces"][0]["revealed"] is True
        assert notdnd_web.Session.load(sid).guide["traces"][0]["revealed"] is True
        assert session.guide["traces"][0]["at"] == "loc-02"


def check_guide_turn_traces_betrayed():
    """`npc_flags` 为 betrayed：痕迹等于 `if_dead_or_betrayed` 原文，且无场外请求。"""
    sid = "g7-trace-betrayed"
    _guide_session(sid)
    huo = _npc("yunji", "npc-01")
    with _LocalServer() as server:
        _bind(server, sid)
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            server.post("/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        with _guide_online(_ONLINE_ENV,
                           _FakeTransport([_BAD_JSON, _BAD_JSON,
                                           _sse_reply(_OK_NARRATION)])):
            server.post("/api/guide/turn",
                        {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
        _set_guide(sid, {"npc_flags": {"npc-01": "betrayed"}})
        fake = _FakeTransport([_BAD_JSON, _BAD_JSON, _sse_reply(_OK_NARRATION)])
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我走进灰市", "location_id": "loc-05"}, sid=sid)
        assert status == 200, status
        assert [call for call in fake.calls if _is_trace_call(call)] == [], \
            "终局标志的 NPC 不得叫模型"
        event = dict(_parse_sse(raw))["narration"]
        assert event["trace"] == huo["if_dead_or_betrayed"], event.get("trace")


def check_guide_turn_traces_offline():
    """离线：场外与叙事都发不出去 → 不留痕、不走第二行 HTTP 状态、回合照常。"""
    sid = "g7-trace-offline"
    _guide_session(sid)
    with _LocalServer() as server:
        _bind(server, sid)
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            server.post("/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        with _guide_online(_ONLINE_ENV,
                           _FakeTransport([_BAD_JSON, _BAD_JSON,
                                           _sse_reply(_OK_NARRATION)])):
            server.post("/api/guide/turn",
                        {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
        offline = {"BASE_URL": "", "MODEL": "mm", "API_KEY": ""}
        fake = _FakeTransport()
        with _guide_online(offline, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我走进灰市", "location_id": "loc-05"}, sid=sid)
        assert status == 200, status
        assert fake.calls == [], "离线不得调用传输"
        session = notdnd_web.get_session(sid)
        assert session.guide["traces"] == [], session.guide["traces"]
        assert session.guide["focus_location_id"] == "loc-05"
        events = [name for name, _payload in _parse_sse(raw)]
        assert "fallback" in events and "done" in events, events


def check_guide_turn_scene_change_traces():
    """`rules.scene.id` 变化：为**当前焦点**记一轮场外痕迹（§2.2 原检查点）。"""
    sid = "g7-trace-scene"
    _guide_session(sid)
    with _LocalServer() as server:
        _bind(server, sid)
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            server.post("/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        with _guide_online(_ONLINE_ENV,
                           _FakeTransport([_BAD_JSON, _BAD_JSON,
                                           _sse_reply(_OK_NARRATION)])):
            server.post("/api/guide/turn",
                        {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
        # 直接改快照的 scene.id（§2.2：原检查点仍有效）。
        _set_scene(sid, "sc-2")
        fake = _FakeTransport([_BAD_JSON, _sse_reply(_OK_NARRATION)])
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "我继续查"}, sid=sid)
        assert status == 200, status
        trace_calls = [call for call in fake.calls if _is_trace_call(call)]
        assert len(trace_calls) == 1, \
            [call["payload"].get("stream") for call in fake.calls]
        user = trace_calls[0]["payload"]["messages"][1]["content"]
        assert "霍砚" in user, "scene.id 变化记的是**当前焦点**（loc-02）的在场 NPC"
        assert notdnd_web.get_session(sid).guide["l2_scene_id"] == "sc-2"


# --------------------------------------------------------------------------
# 多人房间（M10-S1，MPI §3.2 / §4）
# --------------------------------------------------------------------------


def _room_headers(room_id: str, member_id: str, token: str) -> dict:
    """房间鉴权头（§4.1）：`X-Room` + `X-Member` + `Authorization: Bearer`。"""
    return {"X-Room": room_id, "X-Member": member_id,
            "Authorization": "Bearer %s" % token}


def _read_room_block(save_dir: str, room_id: str) -> dict:
    """直接读服务端写出的存档，取顶层 `room` 块（绕开响应，验证**磁盘**事实）。"""
    return _read_save(room_id, save_dir)["room"]


def _save_bytes(save_dir: str, sid: str) -> str:
    """新档目录里**所有文件**的字节（解码后拼接）：查「明文令牌不得落盘」用。"""
    camp = os.path.join(save_dir, "campaigns", sid)
    out = []
    for name in sorted(os.listdir(camp)):
        with open(os.path.join(camp, name), "rb") as handle:
            out.append(handle.read().decode("utf-8", "replace"))
    return "".join(out)


# --------------------------------------------------------------------------
# 房间事件流（M10-S2，MPI §5.4 / §5.5）脚手架
# --------------------------------------------------------------------------


@contextlib.contextmanager
def _stream_tuning(*, heartbeat=None, dead=None, queue_max=None):
    """把房间流的**模块常量**临时改小，让「心跳 / 判死 / 队列满」秒级可复现。

    跑的还是同一条代码路径（常量都在调用点读），改的只是值；15s / 90s / 256 帧
    的**默认值**由 `check_room_stream_heartbeat_and_dead` 单独断言，不靠这里。
    """
    saved = (notdnd_web.ROOM_STREAM_HEARTBEAT_SECONDS,
             notdnd_web.ROOM_STREAM_DEAD_SECONDS,
             notdnd_web.ROOM_STREAM_QUEUE_MAX)
    if heartbeat is not None:
        notdnd_web.ROOM_STREAM_HEARTBEAT_SECONDS = heartbeat
    if dead is not None:
        notdnd_web.ROOM_STREAM_DEAD_SECONDS = dead
    if queue_max is not None:
        notdnd_web.ROOM_STREAM_QUEUE_MAX = queue_max
    try:
        yield
    finally:
        (notdnd_web.ROOM_STREAM_HEARTBEAT_SECONDS,
         notdnd_web.ROOM_STREAM_DEAD_SECONDS,
         notdnd_web.ROOM_STREAM_QUEUE_MAX) = saved


class _RoomStreamClient:
    """裸 socket 的房间推送流客户端：能读**增量**，也能读到**服务端断流**。

    `http.client` 读分块体要读到连接关闭才返回——流是长连接，用它会挂死，
    所以这里自己建连接、自己 `recv`：`wait_*` 都带期限，`ids` / `frames` 从
    已收到的字节里解析 `id:` / `event:` / `data:`（尾帧没收全就整帧丢弃）。
    `rcvbuf` 用来造**慢客户端**：接收缓冲压到最小 + 不读 = 服务端的写会阻塞。
    """

    def __init__(self, port: int, room_id: str, member_id: str, token: str,
                 since: int = 0, rcvbuf: int | None = None):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if rcvbuf:
            # 必须在 connect 之前设：窗口在握手时就按它通告。
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(rcvbuf))
        self.sock.settimeout(15)
        self.sock.connect(("127.0.0.1", port))
        self.raw = b""
        self.eof = False
        self.head = b""
        self.status = 0
        self.sock.sendall(
            ("GET /api/room/stream?since=%d HTTP/1.1\r\nHost: 127.0.0.1\r\n"
             "X-Room: %s\r\nX-Member: %s\r\nAuthorization: Bearer %s\r\n"
             "Accept: text/event-stream\r\nConnection: close\r\n\r\n"
             % (since, room_id, member_id, token)).encode("utf-8"))
        self._read_head()

    # ── 读 ─────────────────────────────────────────────────
    def _recv(self, timeout: float) -> bool:
        """收一段；返回是否收到字节（对端关闭 → `self.eof`）。"""
        self.sock.settimeout(timeout)
        try:
            piece = self.sock.recv(65536)
        except socket.timeout:
            return False
        if not piece:
            self.eof = True
            return False
        self.raw += piece
        return True

    def _read_head(self, deadline: float = 10.0):
        end = time.time() + deadline
        while b"\r\n\r\n" not in self.raw:
            if time.time() > end:
                raise AssertionError("流响应头未在期限内到达：%r" % self.raw[:200])
            if not self._recv(1.0):
                break
        self.head = self.raw.split(b"\r\n\r\n", 1)[0]
        self.status = int(self.head.split(b" ", 2)[1]) if self.head else 0

    def body(self) -> str:
        """已收到的**解块**体（不完整的尾帧留在里面，由解析方丢弃）。"""
        return _dechunk(self.raw).decode("utf-8", "replace")

    def ids(self) -> list:
        """已收到的帧号（按到达顺序）。"""
        out = []
        for line in self.body().replace("\r", "").split("\n"):
            if line.startswith("id:"):
                out.append(int(line[3:].strip()))
        return out

    def frames(self) -> list:
        """已收到的**完整**帧 `[{id, event, data}]`（尾帧没收全就丢整帧）。"""
        out, cur = [], None
        for line in self.body().replace("\r", "").split("\n"):
            if line.startswith("id:"):
                cur = {"id": int(line[3:].strip())}
            elif line.startswith("event:") and cur is not None:
                cur["event"] = line[6:].strip()
            elif line.startswith("data:") and cur is not None:
                try:
                    cur["data"] = json.loads(line[5:].strip() or "{}")
                except ValueError:
                    cur = None              # 这一帧还没收全：整帧丢弃
                    continue
                out.append(cur)
                cur = None
        return out

    def wait_frames(self, count: int, deadline: float = 8.0) -> list:
        """等到至少 `count` 个**完整**帧（或超时 / 对端关闭）。"""
        end = time.time() + deadline
        while len(self.frames()) < count and time.time() < end and not self.eof:
            self._recv(0.1)
        return self.frames()

    def wait_ids(self, count: int, deadline: float = 8.0) -> list:
        """等到至少 `count` 个帧号（或超时 / 对端关闭）——慢客户端用。"""
        end = time.time() + deadline
        while len(self.ids()) < count and time.time() < end and not self.eof:
            self._recv(0.1)
        return self.ids()

    def wait_eof(self, deadline: float = 15.0) -> bool:
        """持续读到服务端收尾（EOF）；返回是否真的读到。"""
        end = time.time() + deadline
        while not self.eof and time.time() < end:
            self._recv(0.2)
        return self.eof

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


def _new_room(server, room_id: str, *, character_id: str = "pc-host",
              display_name: str = "房主") -> dict:
    """建一个房间，返回房主的 `{member_id, token}`（唯一一次明文令牌，§4.1）。"""
    status, host = _post(server, "/api/room/create",
                         {"room_id": room_id, "display_name": display_name,
                          "character_id": character_id})
    assert status == 200, "%s / %s" % (status, host)
    return host


def _join_room(server, room_id: str, *, character_id: str = "pc-b",
               display_name: str = "乙") -> dict:
    """加入一个已存在的房间，返回 `{member_id, token}`（§4.1 鉴权链）。"""
    status, member = _post(server, "/api/room/join",
                           {"display_name": display_name,
                            "character_id": character_id},
                           headers={"X-Room": room_id})
    assert status == 200, "%s / %s" % (status, member)
    return member


def _room_event(session, *, key: str, visibility: dict | None = None) -> int:
    """落一条事件并按**提交后的扇出**投递（夹具）。

    事件走状态层的 `commit()`——行动管线 / CHRON 写事件用的是**同一个**出口；
    随后调 `_room_publish_after_commit`，也就是两条提交路径在提交点之后跑的那一步。
    返回事件的 `seq`。
    """
    handle = session._handle or session._open_handle()
    result = handle.commit(
        base_seq=handle.view()["seq"], idempotency_key=key, type="core.commit",
        actor="world", changes=[], rules_version="prism@test",
        visibility=dict(visibility or {"scope": "public", "subjects": []}),
        source="system")
    assert result["status"] == "committed", result
    notdnd_web._room_publish_after_commit(handle, result, session.sid)
    return result["seq_to"]


class _InterleavingLock:
    """测试用锁代理：在第 N 次 `with` **取到锁之前**插入一次确定性交错。

    用来复现「请求 A 鉴权通过 → 请求 B 改变房间 → A 的变更仍执行」这类竞态，
    不靠 `sleep` / 多线程时序赌博，故每次都跑在同一条交错上。只实现房间代码
    用到的 `with`（`__enter__` / `__exit__`），不冒充完整锁。
    """

    def __init__(self, real, at_acquire: int, hook):
        self._real = real
        self._at = at_acquire
        self._count = 0
        self._hook = hook

    def __enter__(self):
        self._count += 1
        if self._count == self._at and self._hook is not None:
            hook, self._hook = self._hook, None
            hook()                      # 在真正拿走锁之前制造交错
        self._real.acquire()
        return self

    def __exit__(self, *_exc):
        self._real.release()
        return False


def check_room_create_join_leave():
    """建房 → 加入 → 离开：令牌只存 hash、明文不入存档（§4.1）。"""
    rid = "room-party-1"
    with _Server() as server:
        status, body = server.post("/api/room/create",
                                   {"room_id": rid, "display_name": "阿雅"})
        assert status == 200, "建房应 200，实际 %s / %s" % (status, body)
        assert body["room_id"] == rid and body["role"] == "host", body
        host_id, host_tok = body["member_id"], body["token"]
        assert notdnd_web.MEMBER_ID_RE.fullmatch(host_id), host_id
        assert notdnd_web.ROOM_ID_RE.fullmatch(rid)

        # 存档里只有 hash：明文令牌不得出现在新档的**任何**字节里。
        raw = _save_bytes(server.dir, rid)
        assert host_tok not in raw, "明文令牌绝不得落盘"
        member = _read_save(rid, server.dir)["room"]["members"][host_id]
        assert member["token_hash"] == notdnd_web._hash_token(host_tok)
        assert "token" not in member, "成员记录只存 token_hash"

        # 第二名加入 → player。
        status, second = server.post("/api/room/join", {"display_name": "柏舟"},
                                     headers={"X-Room": rid})
        assert status == 200, "%s / %s" % (status, second)
        assert second["role"] == "player", second
        player_id, player_tok = second["member_id"], second["token"]

        # 玩家离开：200；此后同令牌请求被拒（403，令牌已不生效）。
        status, left = server.post("/api/room/leave", {},
                                   headers=_room_headers(rid, player_id, player_tok))
        assert status == 200, "%s / %s" % (status, left)
        assert left["member_id"] == player_id
        status, denied = server.post("/api/room/leave", {},
                                     headers=_room_headers(rid, player_id, player_tok))
        assert status == 403, "离开后同令牌应 403，实际 %s / %s" % (status, denied)
        assert denied.get("reason") == "member_left"

        # 磁盘上成员已是 left；房主仍在、房主位不变。
        block = _read_room_block(server.dir, rid)
        assert block["members"][player_id]["state"] == "left"
        assert block["members"][host_id]["state"] == "active"
        assert block["host_member_id"] == host_id


def check_room_legacy_lazy_migration():
    """T11：旧单人档载入 → 惰性迁移出最小 `room` 块；原 rules/guide 不变；往返一致。"""
    sid = "room-legacy-1"
    legacy = {
        "sid": sid, "created": 1700000000.0,
        "log": [{"seq": 1, "kind": "narrative", "text": "旧档旁白",
                 "speaker": "", "ts": 1700000000000}],
        "seq": 1, "save_name": "旧档",
        "rules": _sample_rules(sid),
        "guide": {"salt": "0123456789abcdef0123456789abcdef", "realizations": {}},
    }
    _write_save(_save_path(sid), legacy)

    s = notdnd_web.Session.load(sid)
    assert s is not None, "老档必须能载入"
    room = s.room
    assert room["room_id"] == sid, "room_id == sid（M-11）"
    assert room["schema_version"] == notdnd_web.ROOM_SCHEMA_VERSION == 1
    assert room["host_member_id"] == "", "迁移出的房间尚无房主（M-16）"
    assert room["members"] == {}, "迁移出的房间没有成员"
    assert room["capacity"] == 6 == notdnd_web.ROOM_MAX_MEMBERS
    assert room["settings"]["offline_advance"] == "never", "默认不推进（A5）"

    # 原 rules / guide 不变（迁移只碰 room 块）。
    assert s.rules == legacy["rules"], "迁移不得动 rules"
    assert s.guide["salt"] == legacy["guide"]["salt"], "迁移不得动 guide"

    # 磁盘上的老档没被就地改写（惰性迁移只发生在内存）。
    with open(_save_path(sid), encoding="utf-8") as handle:
        assert json.load(handle) == legacy, "惰性迁移不得就地改写老档"

    # 往返一致：save() 之后重新载入，room 块与 rules 逐字段相等。
    s.save()
    again = notdnd_web.Session.load(sid)
    assert again is not None
    assert again.to_dict()["room"] == room, "room 块往返必须一致"
    assert again.rules == legacy["rules"]
    assert again.guide["salt"] == legacy["guide"]["salt"]

    # 默认房间块必须是**工厂**：两个存档不共享同一个可变对象。
    other = notdnd_web.Session("room-legacy-2")
    assert other.room is not s.room
    other.room["members"]["m-00000000"] = {}
    assert "m-00000000" not in s.room["members"], "不同存档不得共享同一份房间块"


def check_room_capacity_full():
    """T7：第 7 名玩家 → 409 room_full；已用座位不被动摇。"""
    rid = "room-full-1"
    with _Server() as server:
        status, host = server.post("/api/room/create", {"room_id": rid})
        assert status == 200, "%s / %s" % (status, host)
        seating = [host["member_id"]]

        # 房主占 1 座，再加入 5 名玩家 = 满员 6。
        for i in range(5):
            status, joined = server.post("/api/room/join", {"display_name": "P%d" % i},
                                         headers={"X-Room": rid})
            assert status == 200, "第 %d 名应能加入：%s / %s" % (i + 2, status, joined)
            seating.append(joined["member_id"])
        assert len(notdnd_web._room_seats(_read_room_block(server.dir, rid))) == 6

        # 第 7 名 → room_full；房间人数不变。
        status, full = server.post("/api/room/join", {"display_name": "P6"},
                                   headers={"X-Room": rid})
        assert status == 409, "满员应 409，实际 %s / %s" % (status, full)
        assert full.get("reason") == "room_full"
        block = _read_room_block(server.dir, rid)
        assert len(notdnd_web._room_seats(block)) == 6, "已用座位不得被动摇"
        assert set(block["members"]) == set(seating)


def check_room_kick_authorization():
    """T8：非房主踢人 → 403 且房间不变；房主踢人 → 生效，被踢者后续 403。"""
    rid = "room-kick-1"
    with _Server() as server:
        status, host = server.post("/api/room/create", {"room_id": rid})
        assert status == 200, "%s / %s" % (status, host)
        host_id, host_tok = host["member_id"], host["token"]
        status, p1 = server.post("/api/room/join", {"display_name": "甲"},
                                 headers={"X-Room": rid})
        assert status == 200, "%s / %s" % (status, p1)
        status, p2 = server.post("/api/room/join", {"display_name": "乙"},
                                 headers={"X-Room": rid})
        assert status == 200, "%s / %s" % (status, p2)
        p1_id, p1_tok = p1["member_id"], p1["token"]
        p2_id, p2_tok = p2["member_id"], p2["token"]

        # 玩家甲踢玩家乙 → 403 not_authorized；乙仍在座，房主不变。
        status, denied = server.post("/api/room/kick", {"member_id": p2_id},
                                     headers=_room_headers(rid, p1_id, p1_tok))
        assert status == 403, "非房主应 403，实际 %s / %s" % (status, denied)
        assert denied.get("reason") == "not_authorized"
        block = _read_room_block(server.dir, rid)
        assert block["members"][p2_id]["state"] == "active", "403 不得改动房间"
        assert block["host_member_id"] == host_id

        # 房主踢乙 → 200；被踢者后续请求 403（§4.4）。
        status, kicked = server.post("/api/room/kick", {"member_id": p2_id},
                                     headers=_room_headers(rid, host_id, host_tok))
        assert status == 200, "房主应能踢人，实际 %s / %s" % (status, kicked)
        assert kicked["member_id"] == p2_id
        assert _read_room_block(server.dir, rid)["members"][p2_id]["state"] == "left"
        status, after = server.post("/api/room/leave", {},
                                    headers=_room_headers(rid, p2_id, p2_tok))
        assert status == 403, "被踢者应 403，实际 %s / %s" % (status, after)
        assert after.get("reason") == "member_left"

        # 房主踢自己 → 400（不改变房间）。
        status, self_kick = server.post("/api/room/kick", {"member_id": host_id},
                                        headers=_room_headers(rid, host_id, host_tok))
        assert status == 400, "%s / %s" % (status, self_kick)


def check_room_kick_host_check_is_atomic():
    """并发回归：房主在「鉴权通过之后、踢人变更之前」离房 → 踢人必须被拒。

    竞态用锁代理**确定性**复现：在踢人请求取走房间锁（第 2 次 `with`）之前，
    让房主离房。旧实现把房主校验留在锁外 → 这里会漏过并以 200 执行踢人；
    修好后校验与变更同处一个临界区 → 403，且目标成员不受影响。

    走**本进程**服务（`_LocalServer`）才能在测试进程里替换 `session.lock`。
    """
    rid = "room-kick-race-1"
    with _LocalServer() as server:
        status, _body, _raw, _heads = server.post("/api/room/create", {"room_id": rid})
        assert status == 200, "建房失败：%s" % status
        host_id = _body["member_id"]
        host_tok = _body["token"]
        status, victim, _raw, _heads = server.post("/api/room/join", {"display_name": "目标"},
                                                   headers={"X-Room": rid})
        assert status == 200, "加入失败：%s" % status
        victim_id = victim["member_id"]

        session = notdnd_web.get_session(rid)
        assert session is not None, "本进程服务应把会话留在缓存里"

        # 正对照：不插交错时房主踢人 200 —— 证明装置本身不坏。
        status, ok, _raw, _heads = server.post("/api/room/kick", {"member_id": victim_id},
                                               headers=_room_headers(rid, host_id, host_tok))
        assert status == 200, "正对照应 200，实际 %s / %s" % (status, ok)
        with session.lock:      # 复原被踢者，只为继续复现竞态
            session.room["members"][victim_id]["state"] = "active"

        # 竞态：踢人请求进入临界区之前，房主离房（同一次交错，确定性）。
        def host_leaves():
            session.room["host_member_id"] = ""
            session.room["members"][host_id]["state"] = "left"

        real_lock = session.lock
        session.lock = _InterleavingLock(real_lock, at_acquire=2, hook=host_leaves)
        try:
            status, denied, _raw, _heads = server.post(
                "/api/room/kick", {"member_id": victim_id},
                headers=_room_headers(rid, host_id, host_tok))
        finally:
            session.lock = real_lock
        assert status == 403, "房主已离房后不得再踢人，实际 %s / %s" % (status, denied)
        assert denied.get("reason") == "not_authorized"

        room = notdnd_web.get_session(rid).room
        assert room["members"][victim_id]["state"] == "active", "被拒的踢人不得改动目标"
        assert room["host_member_id"] == "", "房主位应保持为空"

        notdnd_web._sessions.pop(rid, None)     # 收摊：别把换过锁的会话留给后面的用例


def check_room_auth_chain():
    """T12：坏令牌 / 缺成员头 / 无凭据 → 401；响应体不泄露成员是否存在。"""
    rid = "room-auth-1"
    with _Server() as server:
        status, host = server.post("/api/room/create", {"room_id": rid})
        assert status == 200, "%s / %s" % (status, host)
        host_id, host_tok = host["member_id"], host["token"]

        status, nothing = server.post("/api/room/leave", {})
        assert status == 401, "无凭据应 401，实际 %s / %s" % (status, nothing)
        status, no_member = server.post("/api/room/leave", {}, headers={
            "X-Room": rid, "Authorization": "Bearer %s" % host_tok})
        assert status == 401, "缺 X-Member 应 401，实际 %s / %s" % (status, no_member)
        status, bad_token = server.post("/api/room/leave", {},
                                        headers=_room_headers(rid, host_id, "0" * 64))
        assert status == 401, "坏令牌应 401，实际 %s / %s" % (status, bad_token)
        status, no_such = server.post("/api/room/leave", {},
                                      headers=_room_headers(rid, "m-deadbeef", host_tok))
        assert status == 401, "成员不存在应 401，实际 %s / %s" % (status, no_such)

        # 先确认响应体真的是「固定 JSON」（不是解析失败回落成的空 dict，
        # 否则下面那条「几份响应体一致」会变成空断言）。
        assert nothing.get("error") and nothing.get("reason") == "token_invalid", nothing
        # 不泄露成员是否存在：几种 401 的响应体必须**完全一致**。
        shapes = {json.dumps(b, sort_keys=True, ensure_ascii=False)
                  for b in (nothing, no_member, bad_token, no_such)}
        assert len(shapes) == 1, "401 不得区分失败缘由：%s" % shapes

        # 房间不存在：形态合法但没建过 → 同样 401、同一响应体。
        status, absent = server.post("/api/room/leave", {},
                                     headers=_room_headers("room-nope-1", host_id, host_tok))
        assert status == 401, "%s / %s" % (status, absent)
        assert json.dumps(absent, sort_keys=True, ensure_ascii=False) in shapes

        # 失败请求不得改动房间：房主仍在座、房主位不变。
        block = _read_room_block(server.dir, rid)
        assert block["members"][host_id]["state"] == "active"
        assert block["host_member_id"] == host_id


# --------------------------------------------------------------------------
# 持久化失败不得静默（Issue #170）：save() 报错 + 有副作用调用点回滚
# --------------------------------------------------------------------------


def check_session_save_reports_failure():
    """`save()` 三态：正常落盘；临时写 / `os.replace` 失败都抛 `SaveError`。"""
    sid = "save-unit"
    session = notdnd_web.Session(sid)
    session.save_name = "甲"

    # 正常保存：不抛、返回 None，磁盘就是刚写的内容（新档检查点）。
    session.save()
    assert _read_save(sid)["save_name"] == "甲"

    # 临时写失败：抛 SaveError（带 sid 与底层 OSError），磁盘维持上一份。
    with _storage_fault(write=True):
        try:
            session.save()
            raised = False
        except notdnd_web.SaveError as exc:
            raised = True
            assert exc.sid == sid, exc.sid
            assert isinstance(exc.cause, OSError), exc.cause
        assert raised, "临时写失败必须抛 SaveError，不得静默"
    assert _read_save(sid)["save_name"] == "甲"

    # os.replace 失败：同样抛 SaveError，换名未发生、磁盘不变。
    session.save_name = "乙"
    with _storage_fault(replace=True):
        try:
            session.save()
            raised = False
        except notdnd_web.SaveError:
            raised = True
        assert raised, "os.replace 失败必须抛 SaveError，不得静默"
    assert _read_save(sid)["save_name"] == "甲", "换名失败不得改写磁盘"


def check_save_rename_failure_rolls_back():
    """改名：临时写 / os.replace 失败 → 500、磁盘不变、内存回滚；恢复后能改。"""
    sid = "save-fault-rename"
    session = notdnd_web.Session(sid)
    session.save_name = "旧名"
    session.save()
    notdnd_web._sessions.pop(sid, None)

    with _LocalServer() as server:
        # 正对照：正常保存成功 → 200，且真的落盘。
        status, body, _raw, _heads = server.post(
            "/api/save/rename", {"sid": sid, "name": "正常名"})
        assert status == 200 and body.get("name") == "正常名", (status, body)
        assert _read_save(sid)["save_name"] == "正常名"

        for label, kwargs in (("临时写失败", {"write": True}),
                              ("os.replace 失败", {"replace": True})):
            with _storage_fault(**kwargs):
                status, body, _raw, _heads = server.post(
                    "/api/save/rename", {"sid": sid, "name": "注入名"})
            assert status == 500, "%s 应回 500，实际 %s / %s" % (label, status, body)
            assert body.get("error"), (label, body)
            # 磁盘维持故障前那一份（未落盘）。
            assert _read_save(sid)["save_name"] == "正常名", label
            # 内存也回滚：不留「内存已改名、磁盘没改」的错位。
            cached = notdnd_web._sessions.get(sid)
            assert cached is not None and cached.save_name == "正常名", (label, cached)

        # 故障解除后重试成功：磁盘与内存都跟上。
        status, body, _raw, _heads = server.post(
            "/api/save/rename", {"sid": sid, "name": "重试名"})
        assert status == 200 and body.get("name") == "重试名", (status, body)
        assert _read_save(sid)["save_name"] == "重试名"

    notdnd_web._sessions.pop(sid, None)


def check_atlas_move_save_failure_rolls_back():
    """`/api/atlas/move` 落盘失败：500、磁盘与内存都留在原地；恢复后能走。"""
    sid = "atlas-save-fault"
    session = notdnd_web.Session(sid)
    session.ensure_atlas()
    view = session.atlas_view()
    origin = view["here"]["place_id"]
    target = next((e for e in view["exits"] if e["band"]), view["exits"][0])
    assert target["place_id"] != origin, "需要一个会真的改变位置的出口"
    _write_save(_save_path(sid), session.to_dict())
    notdnd_web._sessions.pop(sid, None)

    with _LocalServer() as server:
        with _storage_fault(replace=True):
            status, body, _raw, _heads = server.post(
                "/api/atlas/move", {"via": target["via"]}, sid=sid)
        assert status == 500, (status, body)
        assert body.get("error"), body
        # 磁盘维持原位置（移动 / 迁移都没落成）。
        on_disk = _read_save(sid)
        assert on_disk["atlas"]["party_locus"]["place_id"] == origin, on_disk["atlas"]
        # 内存也回滚：队伍没有移动。
        cached = notdnd_web._sessions.get(sid)
        assert cached is not None and cached._locus["place_id"] == origin, \
            (cached and cached._locus)

        # 故障解除后重试：真的走过去，且落盘。
        status, moved, _raw, _heads = server.post(
            "/api/atlas/move", {"via": target["via"]}, sid=sid)
        assert status == 200 and moved["status"] == "ok", (status, moved)
        on_disk = _read_save(sid)
        assert on_disk["atlas"]["party_locus"]["place_id"] == target["place_id"]

    notdnd_web._sessions.pop(sid, None)


def check_guide_turn_settle_save_failure():
    """结算落盘失败：状态行之前回 500、结算回滚、不叫模型、磁盘无该行动。"""
    sid = "g2-settle-save-fault"
    _guide_session(sid)
    fake = _FakeTransport()
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            with _storage_fault(replace=True):
                status, body, _raw, heads = server.post(
                    "/api/guide/turn",
                    {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
            assert status == 500, (status, body)
            assert body.get("error"), body
            assert "event-stream" not in heads.get("content-type", ""), heads
            assert fake.calls == [], "结算失败不得叫模型"
            cached = notdnd_web._sessions.get(sid)
            assert cached is not None and \
                "a-lock" not in cached.rules.get("done_actions", {}), \
                "结算失败必须回滚内存规则态"

    reloaded = notdnd_web.Session.load(sid)
    assert "a-lock" not in reloaded.rules.get("done_actions", {}), \
        "结算失败不得落盘"
    notdnd_web._sessions.pop(sid, None)


def check_guide_turn_focus_save_failure():
    """`location_id` 焦点落盘失败：状态行之前回 500、guide 回滚、磁盘焦点不变。"""
    sid = "g5-focus-save-fault"
    _guide_session(sid)
    with _LocalServer() as server:
        status, body, _raw, _heads = server.post(
            "/api/guide/bind",
            {"world_key": "yunji", "scenario_id": "yunji"}, sid=sid)
        assert status == 200, (status, body)

        with _storage_fault(replace=True):
            status, body, _raw, heads = server.post(
                "/api/guide/turn",
                {"text": "我走进白壁", "location_id": "loc-02"}, sid=sid)
        assert status == 500, (status, body)
        assert body.get("error"), body
        assert "event-stream" not in heads.get("content-type", ""), heads
        cached = notdnd_web._sessions.get(sid)
        assert cached is not None and not cached.guide.get("focus_location_id"), \
            "焦点未落盘必须回滚内存"
    reloaded = notdnd_web.Session.load(sid)
    assert not reloaded.guide.get("focus_location_id"), "磁盘不该有未落盘的焦点"
    notdnd_web._sessions.pop(sid, None)


def check_guide_tool_settle_save_failure():
    """工具预通行里的结算落盘失败：不再谎报成功，`done` 明确回报未持久化。"""
    sid = "g4-tool-save-fault"
    _guide_session(sid)
    fake = _FakeTransport([
        {"content": "", "tool_calls": [_tool_call("request_check",
                                                  {"action_id": "a-lock"})],
         "usage": {}},
    ])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            with _storage_fault(replace=True):
                status, _body, raw, _heads = server.post(
                    "/api/guide/turn", {"text": "查规则"}, sid=sid)
    assert status == 200, status
    events = _parse_sse(raw)
    assert [name for name, _ in events] == ["fallback", "done"], events
    assert events[1][1] == {"status": "error", "reason": "save_failed"}, events[1][1]
    # 结算失败已打断工具链：只发了工具那一轮，没有再叫叙事。
    assert len(fake.calls) == 1, fake.calls
    # 那次结算没落盘：磁盘上没有它。
    reloaded = notdnd_web.Session.load(sid)
    assert "a-lock" not in reloaded.rules.get("done_actions", {})
    notdnd_web._sessions.pop(sid, None)


def check_room_create_save_failure():
    """建房落盘失败：500、不进缓存、磁盘上没有可载入的档；恢复后能建房。"""
    rid = "room-save-fault"
    with _LocalServer() as server:
        with _storage_fault(replace=True):
            status, body, _raw, _heads = server.post(
                "/api/room/create", {"room_id": rid, "display_name": "甲"})
        assert status == 500, (status, body)
        assert body.get("error"), body
        assert rid not in notdnd_web._sessions, "半成品不得进缓存"
        assert not notdnd_web._save_exists(rid), "磁盘不得留下半成品存档"
        assert notdnd_web.Session.load(rid) is None, "半成品不得可载入"

        # 故障解除后重试：建房成功并落盘。
        status, body, _raw, _heads = server.post(
            "/api/room/create", {"room_id": rid, "display_name": "甲"})
        assert status == 200, (status, body)
        assert body.get("token") and body.get("role") == "host", body
        assert notdnd_web._save_exists(rid)
    notdnd_web._sessions.pop(rid, None)


# --------------------------------------------------------------------------
# 房间事件流与断线重连（M10-S2，MPI §5.4 / §5.5）
# --------------------------------------------------------------------------


def check_room_stream_frames_and_filter():
    """§5.4 / §6.1（S2 最小口径）：帧形状、频道映射、无权看的帧**一条都不出现**。

    `public` → `world`（房内人人有）；`private` → `tell`、`team` → `party`
    （只给 `visibility.subjects` 点名的角色）；`gm`（导引者内部）对谁都不下发。
    """
    rid = "room-stream-1"
    with _LocalServer() as server, _stream_tuning(heartbeat=0.2, dead=5.0):
        host = _new_room(server, rid, character_id="pc-host")
        other = _join_room(server, rid, character_id="pc-b")
        session = notdnd_web.get_session(rid)
        _room_event(session, key="f1")                     # seq 1 世界事件
        _room_event(session, key="f2",                     # seq 2 只给房主的私聊
                    visibility={"scope": "private", "subjects": ["pc-host"]})
        _room_event(session, key="f3",                     # seq 3 队伍范围
                    visibility={"scope": "team", "subjects": ["pc-host"]})
        _room_event(session, key="f4",                     # seq 4 导引者内部
                    visibility={"scope": "gm", "subjects": []})
        _room_event(session, key="f5",                     # seq 5 只给乙的私聊
                    visibility={"scope": "private", "subjects": ["pc-b"]})

        with _RoomStreamClient(server.port, rid, host["member_id"],
                               host["token"], since=0) as stream:
            frames = stream.wait_frames(3)
            assert [f["id"] for f in frames[:3]] == [1, 2, 3], frames
            # 帧 = `id: <seq>` + `event: <频道>` + `data: {seq,type,event,audience}`
            first = frames[0]
            assert first["event"] == "world", first
            assert set(first["data"]) == {"seq", "type", "event", "audience"}, first
            assert first["data"]["seq"] == first["id"] == first["data"]["event"]["seq"]
            assert first["data"]["type"] == first["data"]["audience"] == "world"
            assert first["data"]["event"]["event_id"] == "%s:e000000000001" % rid
            assert [f["event"] for f in frames[:3]] == ["world", "tell", "party"]
            assert frames[2]["data"]["event"]["visibility"]["scope"] == "team", frames[2]
            # `gm` 的那条（4）与给乙的私聊（5）在**补发**里就不该出现
            time.sleep(0.4)
            assert [f["id"] for f in stream.frames()] == [1, 2, 3], stream.frames()

        with _RoomStreamClient(server.port, rid, other["member_id"],
                               other["token"], since=0) as stream_b:
            frames_b = stream_b.wait_frames(2)
            assert [f["id"] for f in frames_b[:2]] == [1, 5], frames_b
            assert [f["event"] for f in frames_b[:2]] == ["world", "tell"], frames_b
            # 房主那条私聊的内容一个字都不给乙（越权不是「前端隐藏」）
            assert "pc-host" not in json.dumps(frames_b, ensure_ascii=False)


def check_room_stream_t5_reconnect_backfill():
    """T5：提交后断线，重连 `since=seq` 补齐错过的 `world` 事件；不重、不漏、不越权。

    §6.4 的口径：补发 = 「按 `seq > since` 读事件 + 与广播**同一** `visibility`
    判据过滤」，**绝不**因为「这是重连」而多给。
    """
    rid = "room-stream-t5-1"
    with _LocalServer() as server, _stream_tuning(heartbeat=0.2, dead=5.0):
        host = _new_room(server, rid, character_id="pc-host")
        other = _join_room(server, rid, character_id="pc-b")
        session = notdnd_web.get_session(rid)
        _room_event(session, key="t5-1")
        _room_event(session, key="t5-2")
        # 第一条流：拿到前两条就断线（手机切网 / 锁屏）
        first = _RoomStreamClient(server.port, rid, host["member_id"],
                                  host["token"], since=0)
        assert [f["id"] for f in first.wait_frames(2)] == [1, 2]
        first.close()

        # 断线期间：世界事件 → 给房主的秘密 → 给乙的秘密 → 又一条世界事件
        _room_event(session, key="t5-3")
        _room_event(session, key="t5-4",
                    visibility={"scope": "private", "subjects": ["pc-host"]})
        _room_event(session, key="t5-5",
                    visibility={"scope": "private", "subjects": ["pc-b"]})
        _room_event(session, key="t5-6")

        # 重连 `since=2`：恰好补齐 3 / 4 / 6——世界事件一条不少，乙的秘密（5）一步不给
        with _RoomStreamClient(server.port, rid, host["member_id"],
                               host["token"], since=2) as again:
            frames = again.wait_frames(3)
            assert [f["id"] for f in frames[:3]] == [3, 4, 6], frames
            assert [f["event"] for f in frames[:3]] == ["world", "tell", "world"], frames
            time.sleep(0.4)
            assert [f["id"] for f in again.frames()] == [3, 4, 6], again.frames()
        # 乙的那条：1 / 2 / 3 / 5 / 6——房主的秘密（4）不出现
        with _RoomStreamClient(server.port, rid, other["member_id"],
                               other["token"], since=0) as stream_b:
            frames_b = stream_b.wait_frames(5)
            assert [f["id"] for f in frames_b[:5]] == [1, 2, 3, 5, 6], frames_b


# --------------------------------------------------------------------------
# 频道与认知分离（M10-S3，MPI §6.1 / §6.2 / §6.4 / §12 S3）
# --------------------------------------------------------------------------


def _room_chat(server, room_id, member, *, channel, text, target="", members=None,
               headers=None):
    """发一条房间消息（`POST /api/room/chat`）：返回 (状态, JSON)。"""
    body = {"channel": channel, "text": text}
    if target:
        body["target"] = target
    if members is not None:
        body["members"] = members
    h = _room_headers(room_id, member["member_id"], member["token"])
    if headers:
        h = dict(h, **headers)
    return _post(server, "/api/room/chat", body, headers=h)


def check_room_chat_t6_tell_not_leaked():
    """T6：A 的 NPC 私谈 / `tell` 只出现在 A 的流；B 的流**0 命中**该内容（§6.1）。"""
    rid = "room-chat-t6"
    secret = "A-ONLY-NPC-SECRET-9d1"
    with _LocalServer() as server, _stream_tuning(heartbeat=0.2, dead=5.0):
        host = _new_room(server, rid, character_id="pc-a")
        other = _join_room(server, rid, character_id="pc-b")
        with _RoomStreamClient(server.port, rid, host["member_id"], host["token"],
                               since=0) as sa, \
                _RoomStreamClient(server.port, rid, other["member_id"],
                                  other["token"], since=0) as sb:
            # A 与 NPC 私谈（`npc` 频道：受众只有 A 自己）+ 一条世界消息。
            status, body = _room_chat(server, rid, host, channel="npc", text=secret)
            assert status == 200 and body["channel"] == "npc", (status, body)
            status, body = _room_chat(server, rid, host, channel="world",
                                      text="EVERYONE-SEES")
            assert status == 200 and body["channel"] == "world", (status, body)
            fa = sa.wait_frames(2)
            assert [f["event"] for f in fa[:2]] == ["npc", "world"], fa
            assert secret in json.dumps(fa, ensure_ascii=False), fa
            # B 只收到 world：私谈的内容一个字都不出现在乙的流里。
            fb = sb.wait_frames(1)
            assert [f["event"] for f in fb[:1]] == ["world"], fb
            time.sleep(0.3)
            assert secret not in json.dumps(sb.frames(), ensure_ascii=False), \
                sb.frames()


def check_room_chat_reconnect_no_overreach():
    """§6.4：受众在提交时**冻结**，重连沿用**同一判据**——重连不收越权内容。

    B 断线期间的 A 私谈（`npc`）不在 B 的重连补发里；世界消息（`world`）一条不漏。
    """
    rid = "room-chat-reconnect"
    secret = "A-ONLY-AFTER-RECONNECT-c4f"
    with _LocalServer() as server, _stream_tuning(heartbeat=0.2, dead=5.0):
        host = _new_room(server, rid, character_id="pc-a")
        other = _join_room(server, rid, character_id="pc-b")
        status, body = _room_chat(server, rid, host, channel="world", text="W1")
        assert status == 200, (status, body)
        # B 拿到第一条就断线（手机切网 / 锁屏）。
        first = _RoomStreamClient(server.port, rid, other["member_id"],
                                  other["token"], since=0)
        assert [f["id"] for f in first.wait_frames(1)] == [1]
        first.close()
        # 断线期间：A 与 NPC 私谈（B 无权看）+ 又一条世界消息。
        status, body = _room_chat(server, rid, host, channel="npc", text=secret)
        assert status == 200, (status, body)
        status, body = _room_chat(server, rid, host, channel="world", text="W2")
        assert status == 200, (status, body)
        # 重连 `since=0`：只补齐 B 有权看的 world（1 / 3），私谈（2）一步不给。
        with _RoomStreamClient(server.port, rid, other["member_id"],
                               other["token"], since=0) as again:
            frames = again.wait_frames(2)
            assert [f["id"] for f in frames[:2]] == [1, 3], frames
            assert secret not in json.dumps(frames, ensure_ascii=False), frames


def check_room_chat_channels_and_visibility_freeze():
    """§6.1：七个频道各按设计分流；受众**提交时冻结**进事件 `visibility`（§6.4）。"""
    rid = "room-chat-channels"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        other = _join_room(server, rid, character_id="pc-b")
        both = {host["member_id"], other["member_id"]}
        cases = [
            ("world", {}, "public", []),
            ("ooc", {}, "public", []),
            ("party", {}, "team", both),
            ("org", {"members": [other["member_id"]]}, "team", both),
            ("npc", {}, "private", {host["member_id"]}),
            ("host", {}, "gm", {host["member_id"]}),
        ]
        for channel, extra, scope, subjects in cases:
            status, body = _room_chat(server, rid, host, channel=channel,
                                      text="m-" + channel, **extra)
            assert status == 200, (channel, status, body)
            assert body["channel"] == channel, body
            assert body["scope"] == scope, (channel, body)
            assert set(body["subjects"]) == set(subjects), (channel, body)
        # tell：仅收发双方。
        status, tell = _room_chat(server, rid, host, channel="tell",
                                  text="m-tell", target=other["member_id"])
        assert status == 200 and tell["scope"] == "private", (status, tell)
        assert set(tell["subjects"]) == both, tell

        # 冻结的受众就是事件 `visibility`（落库载体，§6.4）；聊天不变更世界块。
        session = notdnd_web.get_session(rid)
        events = session._handle.events_since(0)
        by_type = {e["type"]: e for e in events}
        assert by_type["chat.tell"]["visibility"]["scope"] == "private", by_type
        assert set(by_type["chat.tell"]["visibility"]["subjects"]) == both
        assert by_type["chat.tell"]["cause"] == "m-tell"
        assert by_type["chat.tell"]["changes"] == [], "聊天不成事实（GDD §7.7）"
        assert by_type["chat.ooc"]["visibility"] == {"scope": "public", "subjects": []}

        # 各频道各走各的：乙的**同一判据**下只收到 world / ooc + 被点名的 tell / org / party。
        headers = dict(_room_headers(rid, other["member_id"], other["token"]),
                       Accept="application/json")
        status, poll = server.get("/api/room/stream?since=0", headers=headers)
        assert status == 200, (status, poll)
        seen = [f["type"] for f in poll["frames"]]
        assert "world" in seen and "ooc" in seen and "tell" in seen, seen
        assert "host" not in seen, seen          # 管理消息只给房主
        assert "npc" not in seen, seen           # A 的私谈不给乙


def check_room_chat_errors():
    """§4.2 / §6.1：非法频道 / 空正文 / 私聊对象不在房 / observer 只读，各回固定码。"""
    rid = "room-chat-errors"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        other = _join_room(server, rid, character_id="pc-b")
        status, bad = _room_chat(server, rid, host, channel="shout", text="x")
        assert status == 400 and bad.get("reason") == "bad_channel", (status, bad)
        status, bad = _room_chat(server, rid, host, channel="world", text="   ")
        assert status == 400 and bad.get("reason") == "bad_text", (status, bad)
        status, bad = _room_chat(server, rid, host, channel="tell", text="x",
                                 target="m-nosuchmember")
        assert status == 404 and bad.get("reason") == "member_not_found", (status, bad)
        # 非房主发管理消息 → 403；observer 只读 → 403。
        status, bad = _room_chat(server, rid, other, channel="host", text="x")
        assert status == 403 and bad.get("reason") == "not_authorized", (status, bad)
        # 未鉴权 → 401（不泄露频道判定）。
        status, raw = _post(server, "/api/room/chat",
                            {"channel": "world", "text": "x"},
                            headers={"X-Room": rid})
        assert status == 401, (status, raw)


def check_room_chat_secret_gate_per_member():
    """§6.2：`tell` / `npc` / `org` 正文按**每成员**过一遍秘密门；`world` 一次过。"""
    rid = "room-chat-gate"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        other = _join_room(server, rid, character_id="pc-b")
        secret = "霍砚低声说他是守钥人计霜的儿子。"   # yunji npc-01 的秘密全文
        # 绑定 yunji（走真文件），让秘密门有 NPC 可判（`bind` 认 `X-Session`）。
        status, body = _post(server, "/api/guide/bind",
                             {"world_key": "yunji", "scenario_id": "yunji"},
                             sid=rid)
        assert status == 200, (status, body)
        # 甲的角色 knows 命中 NPC-01 的 knows → 放行；乙无 knows → 涂掉。
        session = notdnd_web.get_session(rid)
        session.rules["party"] = [{"id": "pc-a", "knows": ["C-01"]},
                                  {"id": "pc-b"}]
        # `tell`（按成员各算）：甲对乙私聊，正文含秘密。
        status, body = _room_chat(server, rid, host, channel="tell",
                                  text=secret, target=other["member_id"])
        assert status == 200, (status, body)
        # 同一事件、同一 seq：甲的轮询见到秘密，乙的轮询只见「……」。
        ha = dict(_room_headers(rid, host["member_id"], host["token"]),
                  Accept="application/json")
        hb = dict(_room_headers(rid, other["member_id"], other["token"]),
                  Accept="application/json")
        status, pa = server.get("/api/room/stream?since=0", headers=ha)
        assert status == 200, (status, pa)
        status, pb = server.get("/api/room/stream?since=0", headers=hb)
        assert status == 200, (status, pb)
        frame_a = pa["frames"][-1]
        frame_b = pb["frames"][-1]
        assert frame_a["seq"] == frame_b["seq"], (frame_a, frame_b)  # 同一条事件
        assert "他是守钥人计霜的儿子" in frame_a["event"]["cause"], frame_a
        assert "他是守钥人计霜的儿子" not in frame_b["event"]["cause"], frame_b
        assert "……" in frame_b["event"]["cause"], frame_b
        # `world` 只过一次（对所有人相同）：两人看到**同一条**（已统一涂掉的）正文。
        status, body = _room_chat(server, rid, host, channel="world", text=secret)
        assert status == 200, (status, body)
        status, wa = server.get("/api/room/stream?since=%d" % frame_a["seq"],
                                headers=ha)
        status2, wb = server.get("/api/room/stream?since=%d" % frame_b["seq"],
                                 headers=hb)
        assert status == 200 and status2 == 200, (status, status2)
        assert wa["frames"][-1]["event"]["cause"] == wb["frames"][-1]["event"]["cause"]
        assert "他是守钥人计霜的儿子" not in wa["frames"][-1]["event"]["cause"]


# --------------------------------------------------------------------------
# 并发提交与幂等（M10-S4，MPI §7.2 / §7.3 / §12 S4；T1 / T3 / T4）
# --------------------------------------------------------------------------


def _room_action_post(server, room_id, member, *, action_id, base_seq, key,
                      params=None, extra_headers=None):
    """发一次房间级世界变更提交（`POST /api/room/action`）：返回 (状态, JSON)。"""
    body = {"action_id": action_id, "base_seq": base_seq}
    if params is not None:
        body["params"] = params
    headers = _room_headers(room_id, member["member_id"], member["token"])
    headers["Idempotency-Key"] = key
    if extra_headers:
        headers.update(extra_headers)
    return _post(server, "/api/room/action", body, headers=headers)


def _seed_room_scene(rid, *, character_ids, action_id="pickup", actions=None):
    """给房间的规则会话补一支队伍 + 一个场景行动（T1 的「拾取」用）。

    房间是「一档 = 一权威世界」，规则态仍归 `prism_core`——这里只造出结算所需
    的最小场景（队伍 + 一个 `check` 行动），不改房间块与状态层的语义。
    `actions` 给定时整表替换默认的那一条拾取（调用方要自己带上要用的 id）。
    """
    session = notdnd_web.get_session(rid)
    with session.lock:
        rules = prism_core.RuleSession(rid)
        for index, character_id in enumerate(character_ids):
            rules.party.append(prism_core.new_unit(
                character_id, "角色-%d" % index,
                attributes={"MGT": 6, "INS": 5}))
        rules.active_unit_id = character_ids[0]
        if actions is None:
            actions = [{"id": action_id, "label": "拾取",
                        "kind": "check", "df": 8}]
        rules.scene = {"id": "sc-room", "actions": actions}
        session.rules = rules.snapshot()
        session.save()
    return session


def _room_action_events(rid):
    """本房间事件日志里行动管线写下的那条（`type` = `ACTION_EVENT_TYPE`）。"""
    handle = notdnd_web.get_session(rid)._handle
    return [e for e in handle.events_since(0)
            if e["type"] == notdnd_web.ACTION_EVENT_TYPE]


def check_room_action_t1_pickup_conflict():
    """T1（§7.3a / §8.1）：两名成员**并发**提交「拾取同一件物品」——恰一人成功
    （200）并写**一条**事件，另一人 `409 conflict` + **当前 seq**、**不写事件**；
    物品唯一归属、可溯源。"""
    rid = "room-action-t1"
    host_char, other_char = "pc-a", "pc-b"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id=host_char)
        other = _join_room(server, rid, character_id=other_char)
        session = _seed_room_scene(rid, character_ids=(host_char, other_char))
        base_seq = session._handle.view()["seq"]
        results = {}

        def submit(member, key):
            results[member["member_id"]] = _room_action_post(
                server, rid, member, action_id="pickup", base_seq=base_seq,
                key=key)

        threads = [threading.Thread(target=submit,
                                    args=(host, "t1-key-host-000001")),
                   threading.Thread(target=submit,
                                    args=(other, "t1-key-other-00001"))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        assert sorted(results[m["member_id"]][0]
                      for m in (host, other)) == [200, 409], results
        # A0：一次提交 = 一条事件——物品的唯一归属落在**恰好一条**事件上。
        events = _room_action_events(rid)
        assert len(events) == 1, events
        winner = host if results[host["member_id"]][0] == 200 else other
        loser = other if winner is host else host
        winner_char = host_char if winner is host else other_char
        assert events[0]["actor"] == winner_char, (events[0], winner_char)
        # 输家：`409 conflict` + 当前 seq（= 赢家提交后的 seq），不落事件。
        code, body = results[loser["member_id"]]
        assert code == 409 and body.get("reason") == "conflict", (code, body)
        assert body["seq"] == events[0]["seq"], (body, events[0]["seq"])


def check_room_action_t3_same_key_replays():
    """T3（§7.3c / §7.7）：同一 `Idempotency-Key` 提交两次——第二次**不产生副作用**、
    回首次结果（`replayed`），资源 / 物品无重复变化（事件数不变）。"""
    rid = "room-action-t3"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        session = _seed_room_scene(rid, character_ids=("pc-a",))
        base_seq = session._handle.view()["seq"]
        status1, first = _room_action_post(server, rid, host, action_id="pickup",
                                           base_seq=base_seq, key="t3-key-00000001")
        assert status1 == 200 and first["status"] == "ok", (status1, first)
        assert first["replayed"] is False, first
        # 第二次：**同一** base_seq（早已过期）也回首次结果——幂等查表在任何结算之前。
        status2, again = _room_action_post(server, rid, host, action_id="pickup",
                                           base_seq=base_seq, key="t3-key-00000001")
        assert status2 == 200 and again["replayed"] is True, (status2, again)
        assert again["result"] == first["result"], (again, first)
        assert len(_room_action_events(rid)) == 1, _room_action_events(rid)


def check_room_action_t4_new_key_stale_conflict():
    """T4（§7.3c / §10 风险 2）：带**不同**幂等键、语义相同的重复点击——第二次因
    `base_seq` 过期被拒（409 + 当前 seq），**不出现双份副作用**。"""
    rid = "room-action-t4"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        session = _seed_room_scene(rid, character_ids=("pc-a",))
        base_seq = session._handle.view()["seq"]
        status1, first = _room_action_post(server, rid, host, action_id="pickup",
                                           base_seq=base_seq, key="t4-key-00000001")
        assert status1 == 200, (status1, first)
        # 换个键再赌一次：新键不命中幂等表，但 base_seq 已过期 → 409，不落事件。
        status2, again = _room_action_post(server, rid, host, action_id="pickup",
                                           base_seq=base_seq, key="t4-key-00000002")
        assert status2 == 409 and again.get("reason") == "conflict", (status2, again)
        assert again["seq"] == 1, again
        assert len(_room_action_events(rid)) == 1, _room_action_events(rid)


def check_room_action_errors():
    """§4.1 / §7.2：缺 / 非法幂等键 → 400；未鉴权 → 401；行动表没有的 id → 404。"""
    rid = "room-action-err"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        session = _seed_room_scene(rid, character_ids=("pc-a",))
        base_seq = session._handle.view()["seq"]
        headers = _room_headers(rid, host["member_id"], host["token"])
        # 缺 `Idempotency-Key` → 400（房间级写提交必须带键）。
        status, body = _post(server, "/api/room/action",
                             {"action_id": "pickup", "base_seq": base_seq},
                             headers=headers)
        assert status == 400 and body.get("reason") == "bad_idem_key", (status, body)
        # 非法键（太短）→ 400。
        status, body = _post(server, "/api/room/action",
                             {"action_id": "pickup", "base_seq": base_seq},
                             headers=dict(headers, **{"Idempotency-Key": "short"}))
        assert status == 400 and body.get("reason") == "bad_idem_key", (status, body)
        # 未鉴权（只有 `X-Room`，无成员 / 令牌）→ 401（不泄露房间内容）。
        status, body = _post(server, "/api/room/action",
                             {"action_id": "pickup", "base_seq": base_seq},
                             headers={"X-Room": rid,
                                      "Idempotency-Key": "err-key-00000001"})
        assert status == 401, (status, body)
        # 行动不在当前场景 → 404（前置条件面）。
        status, body = _room_action_post(server, rid, host, action_id="no-such",
                                         base_seq=base_seq, key="err-key-00000002")
        assert status == 404, (status, body)


def check_room_chat_idempotency_and_base_seq():
    """§7.2：房间聊天提交口也认 `Idempotency-Key`（同键重放回首次结果）与 `base_seq`
    （过期 → 409 + 当前 seq）——两条房间级写路径同一套并发口径（S3/S4 收口）。"""
    rid = "room-chat-idem"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        session = notdnd_web.get_session(rid)
        base_seq = session._handle.view()["seq"]
        headers = dict(_room_headers(rid, host["member_id"], host["token"]),
                       **{"Idempotency-Key": "chat-idem-key-0001"})
        status1, first = _post(server, "/api/room/chat",
                               {"channel": "world", "text": "hello",
                                "base_seq": base_seq}, headers=headers)
        assert status1 == 200, (status1, first)
        # 同键重放：回首次结果（同一 seq），不写新事件。
        status2, again = _post(server, "/api/room/chat",
                               {"channel": "world", "text": "hello",
                                "base_seq": base_seq}, headers=headers)
        assert status2 == 200 and again["seq"] == first["seq"], (status2, again)
        # 新键 + 过期 base_seq → 409 + 当前 seq，不写事件。
        headers2 = dict(_room_headers(rid, host["member_id"], host["token"]),
                        **{"Idempotency-Key": "chat-idem-key-0002"})
        status3, conflict = _post(server, "/api/room/chat",
                                  {"channel": "world", "text": "hello2",
                                   "base_seq": base_seq}, headers=headers2)
        assert status3 == 409 and conflict.get("reason") == "conflict", \
            (status3, conflict)
        assert conflict["seq"] == first["seq"], (conflict, first)
        assert len(session._handle.events_since(0)) == 1, \
            session._handle.events_since(0)


def check_room_stream_poll_equivalence():
    """§5.5 降级：**同一条路径**的短轮询（`Accept: application/json`）与流式逐帧一致。"""
    rid = "room-stream-poll-1"
    with _LocalServer() as server, _stream_tuning(heartbeat=0.2, dead=5.0):
        host = _new_room(server, rid, character_id="pc-host")
        session = notdnd_web.get_session(rid)
        for index in range(1, 4):
            _room_event(session, key="poll-%d" % index)
        _room_event(session, key="poll-4",
                    visibility={"scope": "private", "subjects": ["pc-host"]})

        with _RoomStreamClient(server.port, rid, host["member_id"],
                               host["token"], since=1) as stream:
            frames = [f for f in stream.wait_frames(3)[:3]]
            assert [f["id"] for f in frames] == [2, 3, 4], frames

        headers = dict(_room_headers(rid, host["member_id"], host["token"]),
                       Accept="application/json")
        status, poll = server.get("/api/room/stream?since=1", headers=headers)
        assert status == 200, (status, poll)
        # 同一 `since`、同一判据：轮询拿到的就是流式那三帧的 `data`
        assert poll["frames"] == [f["data"] for f in frames], poll
        assert poll["seq"] == 4, poll

        status, empty = server.get("/api/room/stream?since=4", headers=headers)
        assert status == 200 and empty == {"frames": [], "seq": 4}, (status, empty)


def check_room_stream_auth_and_since():
    """§4.1 / §5.1：流与轮询走同一条鉴权链；非法 `since` → 400（不静默当 0）。"""
    rid = "room-stream-auth-1"
    with _LocalServer() as server, _stream_tuning(heartbeat=0.2, dead=5.0):
        host = _new_room(server, rid, character_id="pc-host")
        status, body = server.get("/api/room/stream?since=0")
        assert status == 401 and body.get("reason") == "token_invalid", (status, body)
        status, bad = server.get("/api/room/stream?since=0",
                                 headers=_room_headers(rid, host["member_id"], "0" * 64))
        assert status == 401 and bad == body, (status, bad)
        status, bad = server.get("/api/room/stream?since=-1",
                                 headers=_room_headers(rid, host["member_id"],
                                                       host["token"]))
        assert status == 400 and "since" in (bad.get("error") or ""), (status, bad)

        status, left = _post(server, "/api/room/leave", {},
                             headers=_room_headers(rid, host["member_id"],
                                                   host["token"]))
        assert status == 200, (status, left)
        status, gone = server.get("/api/room/stream?since=0",
                                  headers=_room_headers(rid, host["member_id"],
                                                        host["token"]))
        assert status == 403 and gone.get("reason") == "member_left", (status, gone)


def check_room_stream_heartbeat_and_dead():
    """§5.5：默认 15s 心跳 / 90s 判死；心跳是注释行（无 `data`）；写不动 → 服务端关流。"""
    assert notdnd_web.ROOM_STREAM_HEARTBEAT_SECONDS == 15.0, "心跳默认 15s（§5.5）"
    assert notdnd_web.ROOM_STREAM_DEAD_SECONDS == 90.0, "判死默认 90s（§5.5）"
    assert notdnd_web.ROOM_STREAM_QUEUE_MAX > 0, "出站队列必须有界（§5.5）"
    rid = "room-stream-heartbeat-1"
    with _LocalServer() as server, _stream_tuning(heartbeat=0.15, dead=1.0):
        # 小**发送**缓冲（服务端监听套接字）+ 客户端不读 = 服务端的写会阻塞：
        # 「判死」必须由服务端触发，不能靠测试关连接来假造。
        server.server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        host = _new_room(server, rid, character_id="pc-host")
        session = notdnd_web.get_session(rid)

        # ① 心跳：没有事件也每 0.15s 一行 `: keepalive`；它不是帧（没有 `data`）
        with _RoomStreamClient(server.port, rid, host["member_id"],
                               host["token"], since=0) as stream:
            end = time.time() + 5
            while ": keepalive" not in stream.body() and time.time() < end:
                stream._recv(0.2)
            assert ": keepalive" in stream.body(), stream.body()[:200]
            assert stream.frames() == [], "心跳不该被当成帧（§5.4：注释行、无 data）"

        # ② 判死：客户端不读 → 补发的写阻塞 → socket 写超时（这里改成 1.0s）→ 关流
        for index in range(1, 61):
            _room_event(session, key="hb-%d" % index)
        dead = _RoomStreamClient(server.port, rid, host["member_id"],
                                 host["token"], since=0, rcvbuf=1024)
        time.sleep(2.0)         # 故意不读：写阻塞满 1.0s → 服务端判死关流
        assert dead.wait_eof(deadline=10.0), "写不动的流必须被服务端关掉（§5.5）"
        ids = dead.ids()
        assert ids == list(range(1, len(ids) + 1)), "断流前收到的必须是连续前缀：%r" % ids
        assert 0 < len(ids) < 60, "要么一帧没写、要么全写完，都不是判死用例的形态：%d" % len(ids)
        dead.close()


def check_room_stream_t13_slow_client():
    """T13：出站队列灌满的慢客户端 → 断流 → 重连补发，最终与快照一致（无丢事件）。"""
    rid = "room-stream-t13-1"
    with _LocalServer() as server, _stream_tuning(heartbeat=0.05, dead=30.0,
                                                  queue_max=2):
        # 小发送缓冲（服务端）+ 小接收缓冲（客户端）+ 不读 → 写线程阻塞，
        # 队列因此灌满；上限调到 2 让「灌满」秒级可复现（默认 256 由心跳那条断言）。
        server.server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
        host = _new_room(server, rid, character_id="pc-host")
        session = notdnd_web.get_session(rid)
        slow = _RoomStreamClient(server.port, rid, host["member_id"],
                                 host["token"], since=0, rcvbuf=1024)
        streams = [s for s in notdnd_web._room_stream_list(rid)
                   if s.member_id == host["member_id"]]
        assert len(streams) == 1, streams
        stream = streams[0]

        for index in range(1, 61):
            _room_event(session, key="t13-%d" % index)
        end = time.time() + 10
        while not stream.overflowed and time.time() < end:
            time.sleep(0.05)
        assert stream.overflowed, "慢客户端没把有界队列灌满（queue=%d）" % len(stream.queue)

        # 客户端开始读 → 写线程解阻塞 → 队列满被写线程看到 → 结束分块（断流）
        assert slow.wait_eof(deadline=15.0), "队列满之后该断流"
        ids = slow.ids()
        assert ids == list(range(1, len(ids) + 1)), "断流前收到的必须是连续前缀：%r" % ids
        slow.close()
        end = time.time() + 5
        while notdnd_web._room_stream_list(rid) and time.time() < end:
            time.sleep(0.05)
        assert notdnd_web._room_stream_list(rid) == [], "断流后流引用必须摘干净"

        # 重连：`since=last_seq` 补齐剩下的（不重不漏）
        with _RoomStreamClient(server.port, rid, host["member_id"], host["token"],
                               since=ids[-1]) as again:
            again.wait_frames(60 - len(ids))
            got = again.ids()
        merged = sorted(set(ids) | set(got))
        # 「最终与快照一致」：与同一条路径的全量读取（`since=0`）逐号对齐
        status, whole = server.get("/api/room/stream?since=0", headers=dict(
            _room_headers(rid, host["member_id"], host["token"]),
            Accept="application/json"))
        assert status == 200, (status, whole)
        assert merged == list(range(1, 61)), "1..60 一条都不能少：%r" % merged
        assert merged == [f["seq"] for f in whole["frames"]], "补发重建必须与快照一致"


def check_room_stream_live_push():
    """§5.5 提交后扇出：走**真写接口**（`/api/action/commit`）的一回合也进流。"""
    rid = "room-stream-live-1"
    saved_available = notdnd_web._action_guide_available
    notdnd_web._action_guide_available = lambda: False      # 叙事段离线：不碰网络
    try:
        with _LocalServer() as server, _stream_tuning(heartbeat=0.2, dead=5.0):
            host = _new_room(server, rid, character_id="pc-host")
            session = notdnd_web.get_session(rid)
            rules = prism_core.RuleSession(rid)
            rules.party.append(prism_core.new_unit("u-1", "试炼者",
                                                   attributes={"MGT": 6, "INS": 7}))
            rules.scene = {"id": "sc-1", "actions": [
                {"id": "a-lock", "label": "撬锁", "kind": "check", "df": 12,
                 "auto_pass": True, "on_pass": "锁簧弹开。", "risk": "R1"}]}
            session.rules = rules.snapshot()
            session.save()

            with _RoomStreamClient(server.port, rid, host["member_id"],
                                   host["token"], since=0) as stream:
                status, prop = _propose_action(server, rid)
                assert status == 200, (status, prop)
                status, out = _post(server, "/api/action/commit",
                                    _commit_body(prop["proposal"], prop["base_seq"]),
                                    sid=rid, headers={
                                        "Accept": "application/json",
                                        "Idempotency-Key": "s2-live-push-0001"})
                assert status == 200 and out.get("committed") is True, (status, out)
                frames = stream.wait_frames(1)
                assert [f["id"] for f in frames[:1]] == [1], frames
                payload = frames[0]["data"]
                assert payload["type"] == "world" and payload["audience"] == "world"
                # 推出去的就是**日志里那条信封**（逐字相等）——推送不改写事实
                status, events = server.get("/api/events?since=0", sid=rid)
                assert status == 200 and len(events["events"]) == 1, events
                assert payload["event"] == events["events"][0], payload
                assert payload["event"]["type"] == notdnd_web.ACTION_EVENT_TYPE
    finally:
        notdnd_web._action_guide_available = saved_available


# --------------------------------------------------------------------------
# 建局写接口（M4-B1，UI-DESIGN §2.1 / §7 缺口 B1）
# --------------------------------------------------------------------------


def check_session_create_endpoint():
    """`POST /api/session/create`：真起服务，建局 → 拉视图 → 日志（§2.1）。

    断言「建局返回的 sid 立刻可用、且磁盘上真有这一局」，并核对既有
    `X-Session` / `?sid=` 语义没被改动。
    """
    with _Server() as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        save_dir = info["save_dir"]

        status, view = server.post("/api/session/create", {})
        assert status == 200, "建局应 200，实际 %s / %s" % (status, view)
        sid = view.get("sid") or ""
        assert notdnd_web.SID_RE.fullmatch(sid), "sid 必须过白名单：%r" % sid
        assert view.get("status") == "ok", view
        # 新局最小视图与 GET /api/session 同形：空 party / 空场景 / save 块。
        assert view["party"] == [] and view["pressure"] == 0
        assert view["scene"] == {"id": "", "actions": []}
        assert view["save"]["name"] and view["save"]["named"] is False
        assert view["log"] == []

        # 磁盘上真有这一局（不是只在内存里）：新档检查点。
        assert os.path.isfile(os.path.join(save_dir, "campaigns", sid,
                                           "snapshot.json"))

        # 建局成功后：GET /api/session 200、GET /api/log 可用。
        status, got = server.get("/api/session", sid=sid)
        assert status == 200, "新局应能拉视图，实际 %s / %s" % (status, got)
        assert got["sid"] == sid
        status, log = server.get("/api/log", sid=sid)
        assert status == 200, (status, log)
        assert log.get("log") == []

        # 既有 X-Session / ?sid= 语义不变：无 sid 仍 400，?sid= 与 X-Session 等价。
        assert server.get("/api/session")[0] == 400
        status, via_q = server.get("/api/session?sid=%s" % sid)
        assert status == 200 and via_q["sid"] == sid, (status, via_q)

        # 列表里能看到这一局（current 走 X-Session）。
        status, saves = server.get("/api/saves", sid=sid)
        assert status == 200
        assert any(row["id"] == sid for row in saves["saves"]), saves["saves"]
        assert saves["current"] == sid

        # 再建一局：sid 不同（各局独立）。
        status, view2 = server.post("/api/session/create", {})
        assert status == 200, (status, view2)
        assert view2["sid"] != sid

        # 参数非法：畸形 JSON 体 → 400（沿用 _body() 的统一语义）。
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        conn.request("POST", "/api/session/create", body=b"{not json",
                     headers={"Content-Type": "application/json"})
        response = conn.getresponse()
        bad_status, bad_body = response.status, _load_json(response.read())
        conn.close()
        assert bad_status == 400 and bad_body.get("error"), (bad_status, bad_body)


def check_session_create_offline():
    """导引者离线（无 .env / 无密钥）：建局照常成立，进入可玩骨架（§2.1 兜底态）。"""
    saved_env = prism_guide.load_env
    prism_guide.load_env = lambda *_a, **_k: {}
    made = []
    try:
        with _LocalServer() as server:
            status, info = server.get("/api/guide/status")
            assert status == 200, (status, info)
            assert info == {"chat": False, "tts": False, "configured": False}, info

            status, view, _raw, _heads = server.post("/api/session/create", {})
            assert status == 200, "离线建局应 200，实际 %s / %s" % (status, view)
            sid = view["sid"]
            made.append(sid)
            assert view["party"] == [] and view["status"] == "ok"
            assert server.get("/api/session", sid=sid)[0] == 200
            assert server.get("/api/log", sid=sid)[0] == 200
    finally:
        prism_guide.load_env = saved_env
        for sid in made:
            notdnd_web._sessions.pop(sid, None)


def check_session_create_reload():
    """建局落盘 → 服务重启（同存档目录）→ 仍能拉视图 / 日志：往返一致。"""
    with _LocalServer() as server:
        status, view, _raw, _heads = server.post("/api/session/create", {})
        assert status == 200, (status, view)
        sid = view["sid"]
    # 服务实例已关；本进程的内存缓存也要摘掉，才逼出「从磁盘重新载入」。
    notdnd_web._sessions.pop(sid, None)
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None, "建局必须落盘，重启后才能载入"
    assert notdnd_web.rules_view(reloaded)["sid"] == sid
    with _LocalServer() as server:
        status, again = server.get("/api/session", sid=sid)
        assert status == 200, "重启后新局应仍可拉取，实际 %s / %s" % (status, again)
        assert again["sid"] == sid and again["party"] == []
        assert server.get("/api/log", sid=sid)[0] == 200
    notdnd_web._sessions.pop(sid, None)


def check_session_create_save_failure():
    """建局落盘失败：500、不进缓存、磁盘无可载入的档；恢复后能建局（#170 语义）。"""
    with _LocalServer() as server:
        before_sessions = set(notdnd_web._sessions)
        before_ids = _save_ids()
        with _storage_fault(replace=True):
            status, body, _raw, _heads = server.post("/api/session/create", {})
        assert status == 500, "落盘失败应 500，实际 %s / %s" % (status, body)
        assert body.get("error"), body
        assert set(notdnd_web._sessions) == before_sessions, "半成品不得进缓存"
        assert _save_ids() == before_ids, "磁盘不得留下半成品存档"

        # 故障解除后重试：建局成功并落盘。
        status, view, _raw, _heads = server.post("/api/session/create", {})
        assert status == 200, (status, view)
        sid = view["sid"]
        assert sid in _save_ids(), "建局后磁盘上必须真有一档"
        notdnd_web._sessions.pop(sid, None)


# --------------------------------------------------------------------------
# 战役入口（M4，UI-DESIGN §2.1，Issue #222）
# --------------------------------------------------------------------------


def check_campaign_catalog_and_bind():
    """`GET /api/campaigns` 与绑定流程：口径与真实数据一致（Issue #222）。

    口径（人的拍板结论）：先选世界（`data/worlds/**`），只列**属于该世界**的
    剧本（`meta.world` 与 `world.key` 逐字相等）；列表与绑定都按世界过滤。
    断言两头都核对：目录里的每条剧本都能在 `data/scenarios/**` 找到出处，
    反过来，真实数据里属于世界模组的剧本一条不少（不多、不少）。随后在
    真子进程 + 无 AI 配置（offline）下走完绑定流程：未绑定视图为空块 →
    跨世界 400「世界对不上」→ 正确绑定 200 → 视图带上世界 / 剧本名。
    """
    real_worlds = {}
    for path in sorted((pathlib.Path(ROOT) / "data" / "worlds").glob("*.json")):
        block = json.loads(path.read_text(encoding="utf-8"))["world"]
        real_worlds[block["key"]] = block["name"]
    real_scenarios = {}
    for path in sorted((pathlib.Path(ROOT) / "data" / "scenarios").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        real_scenarios[path.stem] = data.get("meta") or {}
    assert real_worlds, "data/worlds/ 下应当有世界模组"

    allowed = {"id", "campaign", "engine", "party_level", "party_size", "tone_pack"}
    with _Server(offline=True) as server:
        # 不需要会话：新局空态在**还没有会话内容**时也要能列目录。
        status, body = server.get("/api/campaigns")
        assert status == 200 and isinstance(body.get("worlds"), list), (status, body)
        listed_worlds = {w["key"]: w for w in body["worlds"]}
        assert list(listed_worlds) == sorted(real_worlds), \
            (list(listed_worlds), sorted(real_worlds))

        listed = {}
        for key, world in listed_worlds.items():
            assert world["name"] == real_worlds[key], (key, world)
            assert isinstance(world["scenarios"], list), world
            for row in world["scenarios"]:
                assert set(row) <= allowed, ("目录只许投影白名单字段", row)
                meta = real_scenarios.get(row["id"])
                assert meta is not None, row
                assert meta.get("world") == key, ("剧本必须属于该世界", row, meta)
                assert row["campaign"] == meta.get("campaign"), row
                assert row["engine"] == meta.get("engine"), row
                for opt in ("party_level", "party_size", "tone_pack"):
                    if opt in meta:
                        assert row.get(opt) == meta[opt], (row, opt)
                    else:
                        assert opt not in row, (row, opt)
                assert row["id"] not in listed, "同一剧本不得出现在两个世界"
                listed[row["id"]] = key

        # 反方向：真实数据里 `meta.world` 命中世界模组的剧本一条不少；
        # 其余剧本（没有对应世界模组）**不**出现在目录里。
        expected = {scenario_id: meta.get("world")
                    for scenario_id, meta in real_scenarios.items()
                    if meta.get("world") in real_worlds}
        assert listed == expected, (sorted(listed), sorted(expected))
        assert expected, "真实数据里应当至少有一则属于世界模组的剧本"

        # 绑定流程（离线）：建局 → 未绑定视图为空块。
        status, view = server.post("/api/session/create", {})
        assert status == 200, (status, view)
        sid = view["sid"]
        assert view["world"] == {} and view["scenario"] == {}, view
        assert view["world_type"] == "modern", view.get("world_type")

        # 跨世界：阈界都市不认熵网的剧本 → 400「世界对不上」，视图不变。
        status, bad = server.post("/api/guide/bind",
                                  {"world_key": "threshold",
                                   "scenario_id": "backup_forty_one"}, sid=sid)
        assert status == 400 and bad.get("error") == "世界对不上", (status, bad)
        status, still = server.get("/api/session", sid=sid)
        assert still["world"] == {} and still["scenario"] == {}, still
        assert still["world_type"] == "modern", still.get("world_type")

        # 正确绑定 → 200；会话视图带上世界 / 剧本名（场景卡直接用）。
        status, ok = server.post("/api/guide/bind",
                                 {"world_key": "entropic-net",
                                  "scenario_id": "backup_forty_one"}, sid=sid)
        assert status == 200 and ok.get("scenario_id") == "backup_forty_one", \
            (status, ok)
        status, bound = server.get("/api/session", sid=sid)
        assert status == 200, (status, bound)
        assert bound["world"] == {"key": "entropic-net", "name": "熵网"}, \
            bound["world"]
        assert bound["scenario"] == {"id": "backup_forty_one",
                                     "name": "第 41 次备份"}, bound["scenario"]
        # 熵网模板是 future；该剧本没有 world_type 覆盖（engine 是 build，不参与推导）。
        assert bound["world_type"] == "future", bound.get("world_type")


# --------------------------------------------------------------------------
# 回合落会话日志（M4-B2，UI-DESIGN §1.3.5 / §7 缺口 B2）
# --------------------------------------------------------------------------


def check_turn_log_wiring():
    """M4-B2：一回合的 `act` / `narrate`（涂掉之后）/ `trace` 进 `Session.log`。

    走 G7 焦点变化回合（会揭开一条痕迹）：核对日志内容与顺序、`?since=` 增量、
    秘密门对日志同样生效、以及重载往返（从磁盘重新载入日志仍在）。
    """
    sid = "b2-turn-log"
    _guide_session(sid)
    huo = _npc("yunji", "npc-01")
    secret = huo["secret"]
    window = secret[:prism_guide.SECRET_WINDOW]
    narration = ("【裁决】判定：成功\n\n"
                 "【叙事】霍砚低声说他是守钥人计霜的儿子。温苔：「灯还亮着。」\n\n"
                 "【钩子】泉声还在响。")
    with _LocalServer() as server:
        _bind(server, sid)
        # 回合 A：无焦点，只建立 scene 检查点（不记痕）。
        with _guide_online(_ONLINE_ENV, _FakeTransport()):
            assert server.post("/api/guide/turn", {"text": "我看看四周"},
                               sid=sid)[0] == 200
        # 回合 B：第一次设焦点 loc-02（previous 空 → 不算变化，不记痕）。
        with _guide_online(_ONLINE_ENV,
                           _FakeTransport([_BAD_JSON, _BAD_JSON,
                                           _sse_reply(_OK_NARRATION)])):
            assert server.post(
                "/api/guide/turn",
                {"text": "我走进白壁", "location_id": "loc-02"},
                sid=sid)[0] == 200
        # 回合 C：焦点 loc-02 → loc-05，是真正的「离开」。先埋一条未揭开的痕迹。
        _set_guide(sid, {"traces": [{"at": "loc-02", "id": "npc-01",
                                     "text": "他换了把新锁。", "revealed": False}]})
        fake = _FakeTransport([_BAD_JSON, _BAD_JSON, _BAD_JSON,
                               _sse_reply(narration)])
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我走进灰市", "location_id": "loc-05"}, sid=sid)
        assert status == 200, status
        event = dict(_parse_sse(raw))["narration"]
        assert event["trace"] == "他换了把新锁。", event.get("trace")

        # —— 内容与顺序：每回合 act → narrate，最后一回合多一条 trace ——
        status, body = server.get("/api/log", sid=sid)
        assert status == 200, (status, body)
        entries = body["log"]
        kinds = [e["kind"] for e in entries]
        assert kinds.count("act") == 3 and kinds.count("narrate") == 3, kinds
        assert kinds.count("trace") == 1, kinds
        seqs = [e["seq"] for e in entries]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs
        tail = entries[-3:]
        assert [e["kind"] for e in tail] == ["act", "narrate", "trace"], tail
        assert tail[0]["text"] == "我走进灰市", tail[0]
        # `narrate` 记的就是玩家在 `narration` 事件里看到的**涂掉之后**那段。
        assert tail[1]["text"] == event["text"], (tail[1]["text"], event["text"])
        assert tail[2]["text"] == event["trace"], tail[2]
        # 秘密门对日志同样生效：日志里的叙事不含秘密全文 / 连续 8 码位，仍有「……」。
        assert secret not in tail[1]["text"], tail[1]["text"]
        assert window not in tail[1]["text"], tail[1]["text"]
        assert "……" in tail[1]["text"], tail[1]["text"]

        # —— `?since=` 只回增量 ——
        cursor = tail[0]["seq"] - 1
        status, inc = server.get("/api/log?since=%d" % cursor, sid=sid)
        assert status == 200, (status, inc)
        assert [e["seq"] for e in inc["log"]] == \
            seqs[seqs.index(tail[0]["seq"]):], inc
        # 拉到队尾：没有新条目。
        status, empty = server.get("/api/log?since=%d" % seqs[-1], sid=sid)
        assert status == 200 and empty["log"] == [], empty

    # —— 重载往返：摘掉内存缓存，从磁盘重新载入，日志仍在（回合已落盘） ——
    notdnd_web._sessions.pop(sid, None)
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None, "回合必须把日志落盘"
    assert [e["kind"] for e in reloaded.log] == kinds, reloaded.log
    assert reloaded.log[-1]["text"] == event["trace"], reloaded.log[-1]
    notdnd_web._sessions.pop(sid, None)


def check_turn_log_narration_beats():
    """M4-B5：`narrate` 条目带上这一回合的 `beats`（无拍时不写空字段）。

    刷新 / 重进会话后前端从 `/api/log?since=` 恢复历史叙事，靠的就是这条：
    条目自带与即时渲染同一份 `beats`，切拍与朗读入口（`tts=true` 时）跟着回来。
    """
    sid = "b5-log-beats"
    _guide_session(sid)
    narration = ("【裁决】你把手按上门栓。\n"
                 "【叙事】风从巷口灌进来。霍砚：「别出声。」\n"
                 "【钩子】巷口有人影。")
    fake = _FakeTransport([_sse_reply(narration)])
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn",
                {"text": "我按住门栓", "action_id": "a-lock"}, sid=sid)
        assert status == 200, status
        payload = dict(_parse_sse(raw))["narration"]
        beats = payload["beats"]
        assert beats, payload

        # 全量：叙事条目带着与 SSE `narration` 事件**同一份** `beats`。
        status, body = server.get("/api/log", sid=sid)
        assert status == 200, (status, body)
        entries = body["log"]
        assert [e["kind"] for e in entries] == ["act", "narrate"], entries
        assert entries[1]["text"] == payload["text"], entries[1]
        assert entries[1]["beats"] == beats, (entries[1].get("beats"), beats)
        # 无拍的条目（`act`）不新增空字段——既有条目形状不变。
        assert "beats" not in entries[0], entries[0]

        # 增量：`?since=` 回来的同一条也带 `beats`（前端恢复走的就是这条）。
        status, inc = server.get("/api/log?since=%d" % entries[0]["seq"], sid=sid)
        assert status == 200, (status, inc)
        assert [e["seq"] for e in inc["log"]] == [entries[1]["seq"]], inc
        assert inc["log"][0]["beats"] == beats, inc

    # 重载往返：`beats` 随日志落盘，重启后恢复的历史叙事仍有拍。
    notdnd_web._sessions.pop(sid, None)
    reloaded = notdnd_web.Session.load(sid)
    assert reloaded is not None
    assert reloaded.log[1]["beats"] == beats, reloaded.log[1]
    notdnd_web._sessions.pop(sid, None)

    # 兜底 / 短路回合没有切拍：`narrate` 条目同样不带这个键。
    sid = "b5-log-beats-fallback"
    _guide_session(sid)
    with _LocalServer() as server:
        with _guide_online(_OFFLINE_ENV, _FakeTransport()):
            status, _body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        assert status == 200, status
        status, log = server.get("/api/log", sid=sid)
        assert [e["kind"] for e in log["log"]] == ["act", "narrate"], log["log"]
        assert all("beats" not in e for e in log["log"]), log["log"]
    notdnd_web._sessions.pop(sid, None)


def check_turn_log_offline_paths():
    """M4-B2：无 AI 配置 / 短路回合的日志——离线兜底、模块缺失、淡出 / 跳过。"""
    offline = {"BASE_URL": "", "MODEL": "mm", "API_KEY": ""}
    with _LocalServer() as server:
        # (a) 导引者在场但离线：走 SSE 兜底，`narrate` 记兜底句（含裁决），并落盘。
        sid = "b2-log-offline"
        _guide_session(sid)
        fake = _FakeTransport()
        with _guide_online(offline, fake):
            status, _body, raw, _heads = server.post(
                "/api/guide/turn", {"text": "我看看四周"}, sid=sid)
        assert status == 200, status
        assert fake.calls == [], "离线不得调用传输"
        events = [name for name, _ in _parse_sse(raw)]
        assert "fallback" in events and "done" in events, events
        status, log = server.get("/api/log", sid=sid)
        assert [e["kind"] for e in log["log"]] == ["act", "narrate"], log["log"]
        narrate = log["log"][1]["text"]
        assert narrate.startswith("【裁决】") and \
            notdnd_web.FALLBACK_NARRATION in narrate, narrate
        # 兜底路径也落了盘：摘缓存重载后日志还在。
        notdnd_web._sessions.pop(sid, None)
        assert [e["kind"] for e in notdnd_web.Session.load(sid).log] == \
            ["act", "narrate"]

        # (b) 淡出 / 跳过整句：非 SSE 的固定 JSON，同样记 `act` + `narrate`。
        sid = "b2-log-fixed"
        _guide_session(sid)
        with _guide_online(offline, _FakeTransport()):
            status, body, _raw, heads = server.post(
                "/api/guide/turn", {"text": "淡出", "action_id": "a-lock"},
                sid=sid)
            assert status == 200, status
            assert body["narration"]["text"] == notdnd_web.GUIDE_FADE_TEXT, body
            assert "event-stream" not in heads.get("content-type", "")
            status, body, _raw, _heads = server.post(
                "/api/guide/turn", {"text": "跳过这段"}, sid=sid)
            assert status == 200 and body["narration"]["text"] == \
                notdnd_web.GUIDE_SKIP_TEXT, body
        status, log = server.get("/api/log", sid=sid)
        assert [e["kind"] for e in log["log"]] == \
            ["act", "narrate", "act", "narrate"], log["log"]
        assert [e["text"] for e in log["log"]] == \
            ["淡出", notdnd_web.GUIDE_FADE_TEXT,
             "跳过这段", notdnd_web.GUIDE_SKIP_TEXT], log["log"]

        # (c) 模块缺失的离线兜底：固定 JSON，兜底句照记。
        sid = "b2-log-nomod"
        _guide_session(sid)
        saved_module = notdnd_web.prism_guide
        notdnd_web.prism_guide = None
        try:
            status, body, _raw, heads = server.post(
                "/api/guide/turn", {"text": "我按住门栓", "action_id": "a-lock"},
                sid=sid)
        finally:
            notdnd_web.prism_guide = saved_module
        assert status == 200, status
        assert body["narration"]["text"] == notdnd_web.FALLBACK_NARRATION, body
        assert "event-stream" not in heads.get("content-type", "")
        status, log = server.get("/api/log", sid=sid)
        assert [e["kind"] for e in log["log"]] == ["act", "narrate"], log["log"]
        assert log["log"][0]["text"] == "我按住门栓", log["log"][0]
        assert log["log"][1]["text"] == notdnd_web.FALLBACK_NARRATION, log["log"][1]
    for done in ("b2-log-offline", "b2-log-fixed", "b2-log-nomod"):
        notdnd_web._sessions.pop(done, None)


def check_turn_log_action_only():
    """M4-B2：只点行动按钮（无散文）时，`act` 记该行动的 `label`（§1.3.5）。"""
    sid = "b2-log-actionid"
    _guide_session(sid)
    with _LocalServer() as server:
        with _guide_online(_OFFLINE_ENV, _FakeTransport()):
            status, _body, _raw, _heads = server.post(
                "/api/guide/turn", {"action_id": "a-lock"}, sid=sid)
        assert status == 200, status
        status, log = server.get("/api/log", sid=sid)
        assert [e["kind"] for e in log["log"]] == ["act", "narrate"], log["log"]
        assert log["log"][0]["text"] == "撬锁", log["log"][0]
    notdnd_web._sessions.pop(sid, None)


def check_turn_log_subprocess():
    """M4-B2：真子进程 + 随机端口 + 临时存档目录——离线回合的日志落盘。

    这条不换传输层（离线本来就不出网），所以走**真子进程**，覆盖「从命令行
    启动」这条路径；日志直接读服务端写出的存档 JSON，证明已持久化。
    """
    with _Server(offline=True) as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        save_dir = info["save_dir"]

        status, view = server.post("/api/session/create", {})
        assert status == 200, (status, view)
        sid = view["sid"]

        status, _body = server.post("/api/guide/turn", {"text": "我看看四周"},
                                    sid=sid)
        assert status == 200, "离线回合应 200，实际 %s" % status

        status, log = server.get("/api/log", sid=sid)
        assert status == 200, (status, log)
        kinds = [e["kind"] for e in log["log"]]
        assert kinds == ["act", "narrate"], log["log"]
        assert log["log"][0]["text"] == "我看看四周", log["log"][0]
        assert notdnd_web.FALLBACK_NARRATION in log["log"][1]["text"], log["log"][1]
        seqs = [e["seq"] for e in log["log"]]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), seqs

        # 落盘：直接读服务端写出的存档（不是内存缓存）。
        saved = _read_save(sid, save_dir)
        assert [e["kind"] for e in saved["log"]] == kinds, saved["log"]
        assert saved["log"][0]["text"] == "我看看四周", saved["log"][0]


# --------------------------------------------------------------------------
# 行动管线写接口（M8-A3，ACTION-DESIGN §3.3 / §7 / §8 / §9）
# --------------------------------------------------------------------------


def _seed_action_session(save_dir: str, sid: str, *, risk: str = "R1",
                         party: bool = True, combat: bool = False,
                         action_id: str = "a-lock", label: str = "撬锁",
                         entry: dict | None = None) -> None:
    """在**指定存档目录**里造一局带一条行动的存档（子进程服务的目录也认）。

    `Session.save()` 读模块级 `SAVE_DIR`，所以先把常量指到目标目录再落盘——
    真子进程服务随后从磁盘载入这一局，新路由的「从命令行启动」路径也因此被
    覆盖。默认写成 R1（直通，不弹确认，§6.4）；要测确认就传 `risk="R3"`。
    `entry` 额外覆盖行动表的字段（长行动用：`time_cost` / `schedule_hint` /
    `interrupt_conditions` 都由行动表声明，§4.2）。
    """
    saved_dir = notdnd_web.SAVE_DIR
    notdnd_web.SAVE_DIR = pathlib.Path(save_dir)
    try:
        session = notdnd_web.Session(sid)
        rules = prism_core.RuleSession(sid)
        if party:
            rules.party.append(prism_core.new_unit(
                "u-1", "试炼者", attributes={"MGT": 6, "INS": 7}))
        scene_entry = {"id": action_id, "label": label, "kind": "check", "df": 12,
                       "auto_pass": True, "on_pass": "锁簧弹开。", "risk": risk}
        if isinstance(entry, dict):
            scene_entry.update(entry)
        rules.scene = {"id": "sc-1", "actions": [scene_entry]}
        if combat:
            rules.combat = {"over": False}
        session.rules = rules.snapshot()
        session.save()
    finally:
        notdnd_web.SAVE_DIR = saved_dir
    notdnd_web._sessions.pop(sid, None)     # 丢掉本进程缓存，逼出真实读盘


def _post(server, path, body, sid="", headers=None):
    """`_Server`（子进程）与 `_LocalServer`（本进程）统一的 POST：(状态, JSON)。"""
    out = server.post(path, body, sid=sid, headers=headers)
    return out[0], out[1]


def _propose_action(server, sid, action_id="a-lock"):
    """显式行动的 `propose`（§9.2 `mode: "action"`）：返回 (状态, 回执)。"""
    return _post(server, "/api/action/propose",
                 {"mode": "action", "action_id": action_id}, sid)


def _commit_body(proposal, base_seq, confirm_token="", **extra):
    """`commit` 的请求体（§9.3）：提案 + 并发基线 +（R2–R4 的）确认令牌。"""
    body = {"proposal": proposal, "base_seq": base_seq}
    if confirm_token:
        body["confirm_token"] = confirm_token
    body.update(extra)
    return body


def _raw_status(raw: bytes) -> int:
    """裸响应第一行的 HTTP 状态码（`_raw_http` 专用）。"""
    try:
        return int(raw.split(b"\r\n", 1)[0].split(b" ")[1])
    except (IndexError, ValueError):
        return 0


def _raw_json(raw: bytes) -> dict:
    """裸响应里的 JSON 体（`_raw_http` 专用）。"""
    _head, _sep, rest = raw.partition(b"\r\n\r\n")
    body = _dechunk(rest) if b"chunked" in _head.lower() else rest
    return _load_json(body)


def check_action_catalog_and_offline_round():
    """M8-A3：**真子进程 + 离线**——catalog / propose / commit 走完一轮。

    离线（无 AI 配置）也走得通（§7.1 离线底线）：`mode: "action"` 的第 3–5 段
    一行模型都不调；`result` 一定早于叙事段（§3.3 硬边界），离线时叙事段是
    `fallback` + 兜底句（§7.3），整条流只有**一行** HTTP 状态（§9.5）。
    """
    sid = "a3-offline-round"
    with _Server(offline=True) as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        _seed_action_session(info["save_dir"], sid)

        status, catalog = server.get("/api/action/catalog", sid=sid)
        assert status == 200, (status, catalog)
        assert catalog["actions"] == [{"id": "a-lock", "label": "撬锁",
                                       "risk": "R1"}], catalog

        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        assert prop["type"] == "proposal" and prop["risk"] == "R1", prop
        assert prop["needs_confirm"] is False, prop      # R1 直通（§6.4）
        assert prop["base_seq"] == 0, prop               # 还没提交过：基线 0
        assert prop["costs_preview"] == [], prop         # 行动表没给时间成本
        proposal = prop["proposal"]
        assert proposal["source"] == "action", proposal
        assert proposal["actor"] == {"kind": "player", "id": "u-1"}, proposal
        assert prism_core.validate_proposal(proposal) == [], proposal

        raw = _raw_http(
            server.port, "/api/action/commit",
            _commit_body(proposal, prop["base_seq"]), sid=sid,
            headers={"Idempotency-Key": "a3-offline-key-0001"})
        assert _status_lines(raw) == 1, "整条流只允许一行状态：%r" % raw[:160]
        events = _parse_sse(_dechunk(raw))
        names = [name for name, _payload in events]
        assert names[0] == "result", names
        assert names[1] in ("fallback", "narration"), names
        assert names[-1] == "done", names
        result = events[0][1]
        assert result["status"] == "success", result
        assert result["committed"] is True, result
        assert len(result["event_ids"]) == 1, result        # A0：一次提交一条事件
        assert result["commit_ref"]["seq_from"] == 1, result
        assert result["commit_ref"]["seq_to"] == 1, result
        fallback = dict(events[1][1])
        assert fallback["status"] == "offline", fallback
        assert fallback["action_id"] == result["action_id"], fallback
        assert fallback["seq_to"] == 1, fallback
        assert notdnd_web.FALLBACK_NARRATION in fallback["text"], fallback

        # 规则层真结算了：`done_actions` 进了磁盘上的存档（离线也要真改世界）
        saved = _read_save(sid, info["save_dir"])
        assert "a-lock" in saved["rules"]["done_actions"], saved["rules"]
        # 会话日志：玩家做了什么 + 玩家实际看到的正文
        status, log = server.get("/api/log", sid=sid)
        assert status == 200, (status, log)
        assert [entry["kind"] for entry in log["log"]] == ["act", "narrate"], log


def check_action_subprocess_error_rows():
    """新路由在**真子进程**下的固定错误（§9.5）：覆盖命令行启动路径。"""
    sid = "a3-subprocess"
    with _Server(offline=True) as server:
        status, info = server.get("/api/saves")
        assert status == 200, "服务未就绪：%s" % status
        _seed_action_session(info["save_dir"], sid)
        # 会话不存在 → 400（不是 404「未知接口」：路径确实接上了）
        status, body = _post(server, "/api/action/propose",
                             {"mode": "action", "action_id": "a-lock"}, "s-nope")
        assert (status, body.get("error")) == (400, "会话不存在或已过期"), (status, body)
        status, body = server.get("/api/action/catalog", sid="s-nope")
        assert (status, body.get("error")) == (400, "会话不存在或已过期"), (status, body)
        # 行动不在当前场景 → 404 没有这个行动
        status, body = _propose_action(server, sid, "no-such-action")
        assert (status, body.get("error")) == (404, "没有这个行动"), (status, body)
        # commit 缺幂等键 → 400（真跑一次写接口）
        status, body = _post(server, "/api/action/commit",
                             {"proposal": {}, "base_seq": 0}, sid)
        assert (status, body.get("error")) == (400, "幂等键不合法"), (status, body)
        # 提案形状过不了重验 → 404 没有这个提案
        status, body = _post(server, "/api/action/commit",
                             {"proposal": {"nope": True}, "base_seq": 0}, sid,
                             headers={"Idempotency-Key": "a3-subprocess-key-0001"})
        assert (status, body.get("error")) == (404, "没有这个提案"), (status, body)


def check_action_nl_module_missing():
    """导引者缺失：`mode: "nl"` 回 200 `type: "offline"`，显式行动照常走完（§9.2）。"""
    sid = "a3-nl-absent"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    saved_guide = notdnd_web.prism_guide
    notdnd_web.prism_guide = None
    try:
        with _LocalServer() as server:
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl", "text": "我看看"}, sid)
            assert (status, body) == (200, {"status": "offline",
                                            "type": "offline"}), (status, body)
            # 显式行动不需要 AI：catalog + propose + 第 3–5 段照常
            status, catalog = server.get("/api/action/catalog", sid=sid)
            assert status == 200 and catalog["actions"][0]["id"] == "a-lock", \
                (status, catalog)
            status, prop = _propose_action(server, sid)
            assert status == 200, (status, prop)
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-absent-key-0001"})
            assert status == 200, (status, body)
            events = _parse_sse(raw)
            assert [name for name, _payload in events] == ["result", "fallback",
                                                          "done"], events
            assert events[0][1]["committed"] is True, events[0]
            assert events[1][1]["status"] == "offline", events[1]
    finally:
        notdnd_web.prism_guide = saved_guide
    notdnd_web._sessions.pop(sid, None)


def check_action_nl_translation_and_rate():
    """`mode: "nl"`：澄清回路 / 拒绝 / 速率的 HTTP 翻译（§6.3 / §9.2 / §9.5）。"""
    sid = "a3-nl"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    clarify_out = {"clarify_id": "c_" + "1" * 32, "missing": "target",
                   "question": "你要说服谁？",
                   "options": [{"id": "npc-01", "label": "守卫"},
                               {"id": "npc-02", "label": "队长"}],
                   "proposal_draft": {"intent": {"verb": "a-lock"}}}
    calls = []
    proposal_out = {}

    def fake_propose(rules, guide, text, *, clarify=None, answer="", env=None,
                     transmit=None):
        """替身：第一次给澄清，带澄清 id 的第二次给提案，其后一律拒绝。"""
        calls.append({"text": text, "clarify": clarify, "answer": answer})
        if len(calls) == 1:
            return {"status": "ok", "type": "clarify", "clarify": clarify_out}
        if clarify is not None:
            return {"status": "ok", "type": "proposal",
                    "proposal": proposal_out["proposal"]}
        return {"status": "reject", "type": "reject", "reason": "做不了这件事"}

    saved_propose = prism_guide.propose_nl
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        proposal_out["proposal"] = prop["proposal"]
        prism_guide.propose_nl = fake_propose
        try:
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl", "text": "我想说服守卫"}, sid)
            assert (status, body["type"]) == (200, "clarify"), (status, body)
            assert body["clarify"]["clarify_id"] == clarify_out["clarify_id"], body
            # 带着澄清 id 回答：服务端取回那一次澄清，把回答并入草案（§6.3）
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl",
                                  "clarify_id": clarify_out["clarify_id"],
                                  "answer": "npc-01"}, sid)
            assert (status, body["type"]) == (200, "proposal"), (status, body)
            assert calls[1]["clarify"] == clarify_out, calls[1]
            assert calls[1]["answer"] == "npc-01", calls[1]
            assert calls[1]["text"] == "我想说服守卫", calls[1]   # 沿用第一次原文
            # 澄清不消耗资源、不落世界事件：基线还是 0
            assert body["base_seq"] == 0, body
            # 澄清不存在 / 过期 → 404 没有这个提案（§9.5）
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl", "text": "我看看",
                                  "clarify_id": "c_" + "9" * 32}, sid)
            assert (status, body.get("error")) == (404, "没有这个提案"), (status, body)
            # 拒绝 → §9.5 的固定短语 + 403
            status, body = _post(server, "/api/action/propose",
                                 {"mode": "nl", "text": "我要屠龙"}, sid)
            assert (status, body.get("error")) == (403, "做不了这件事"), (status, body)
            # 速率：每会话每 60 秒 30 次 propose，第 31 次起 429
            limited = None
            for _ in range(40):
                status, body = _post(server, "/api/action/propose",
                                     {"mode": "nl", "text": "再看看"}, sid)
                if status == 429:
                    limited = body
                    break
            assert limited is not None, "第 31 次 propose 必须被限流"
            assert limited.get("error") == "太频繁", limited
        finally:
            prism_guide.propose_nl = saved_propose
    notdnd_web._sessions.pop(sid, None)


def check_action_commit_errors_and_confirm():
    """`commit` 状态行**之前**的固定错误 + R2–R4 确认回路（§9.4 / §9.5）。"""
    sid = "a3-commit-errors"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, risk="R3")
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        assert prop["risk"] == "R3" and prop["needs_confirm"] is True, prop
        proposal = prop["proposal"]
        base_seq = prop["base_seq"]

        def expect(code, error, body, key="a3-err-key-0001", sid_=sid):
            """一次 commit：恰好一行 HTTP 状态 + 固定短语。"""
            raw = _raw_http(server.port, "/api/action/commit", body, sid=sid_,
                            headers={"Idempotency-Key": key})
            assert _status_lines(raw) == 1, raw[:160]
            assert _raw_status(raw) == code, (code, raw[:200])
            assert (_raw_json(raw).get("error") or "") == error, _raw_json(raw)
            return raw

        body = _commit_body(proposal, base_seq)
        # ① 会话不存在 / 过期 → 400（四条路由同一口径）
        expect(400, "会话不存在或已过期", body, sid_="s-does-not-exist")
        # ② 幂等键缺失 / 非法 → 400（缺失用裸 socket，绕开客户端的头）
        raw = _raw_http(server.port, "/api/action/commit", body, sid=sid)
        assert _raw_status(raw) == 400, raw[:160]
        assert _raw_json(raw)["error"] == "幂等键不合法", _raw_json(raw)
        expect(400, "幂等键不合法", body, key="short")
        expect(400, "幂等键不合法", body, key="x" * 65)
        # ③ 请求体畸形 / 超限 → 400（§9.5）
        malformed = _raw_body_http(server.port, "/api/action/commit", b"{not json",
                                   sid=sid,
                                   headers={"Idempotency-Key": "a3-err-key-0002"})
        assert _raw_status(malformed) == 400, malformed[:160]
        assert _raw_json(malformed)["error"] == "JSON 解析失败", _raw_json(malformed)
        oversize = _raw_body_http(server.port, "/api/action/commit", b"",
                                  sid=sid,
                                  headers={"Idempotency-Key": "a3-err-key-0003"},
                                  content_length=2_000_001)
        assert _raw_status(oversize) == 400, oversize[:160]
        assert _raw_json(oversize)["error"] == "请求体过大", _raw_json(oversize)
        # ④ 提案不存在 / 更高 schema_version / 未知行动 / 实体引用无效
        expect(404, "没有这个提案", _commit_body({"not": "a proposal"}, base_seq))
        expect(409, "版本不支持",
               _commit_body(dict(proposal, schema_version=2), base_seq))
        unknown = dict(proposal, proposal_id="p_" + "2" * 32,
                       intent=dict(proposal["intent"], verb="no-such-action"))
        expect(404, "没有这个行动", _commit_body(unknown, base_seq))
        bad_target = dict(proposal, proposal_id="p_" + "3" * 32,
                          intent=dict(proposal["intent"],
                                      target={"kind": "npc", "id": "npc-404"}))
        expect(404, "目标不存在", _commit_body(bad_target, base_seq))
        # ⑤ R3 缺确认令牌 / 令牌不对 / 令牌已失效 → 409 需要确认
        expect(409, "需要确认", body)
        expect(409, "需要确认",
               _commit_body(proposal, base_seq, confirm_token="cf_" + "0" * 32))
        status, confirmed = _post(server, "/api/action/confirm",
                                  {"proposal_id": proposal["proposal_id"]}, sid)
        assert status == 200, (status, confirmed)
        token = confirmed["confirm_token"]
        assert notdnd_web.ACTION_CONFIRM_TOKEN_RE.fullmatch(token), confirmed
        expect(409, "需要确认",
               _commit_body(proposal, base_seq, confirm_token="cf_" + "1" * 32))
        with notdnd_web._ACTION_LOCK:       # 把有效期拨到过去 = 确认已失效
            notdnd_web._action_confirms[(sid, proposal["proposal_id"])][
                "expires_at"] = 0
        expect(409, "需要确认",
               _commit_body(proposal, base_seq, confirm_token=token))
        # ⑥ 有了有效令牌后：旧 base_seq → 409 状态已变，请重试（不结算、不落事件）
        status, again = _post(server, "/api/action/confirm",
                              {"proposal_id": proposal["proposal_id"]}, sid)
        assert status == 200, (status, again)
        expect(409, "状态已变，请重试",
               _commit_body(proposal, base_seq + 7,
                            confirm_token=again["confirm_token"]))
        # ⑦ 取消：丢令牌——「取消」就是不提交，带旧令牌再来仍是 409
        status, cancelled = _post(server, "/api/action/confirm",
                                  {"proposal_id": proposal["proposal_id"],
                                   "decision": "cancel"}, sid)
        assert (status, cancelled["type"]) == (200, "cancelled"), (status, cancelled)
        expect(409, "需要确认",
               _commit_body(proposal, base_seq,
                            confirm_token=again["confirm_token"]))
        # ⑧ 确认不存在 / 过期 → 404 没有这个提案（提案 id 形状不对也算没有）
        status, body2 = _post(server, "/api/action/confirm",
                              {"proposal_id": "bogus"}, sid)
        assert (status, body2.get("error")) == (404, "没有这个提案"), (status, body2)
        # ⑨ 重新确认 + 正确基线 → 提交成功（机制没被前面的错误路径卡死）
        status, final = _post(server, "/api/action/confirm",
                              {"proposal_id": proposal["proposal_id"]}, sid)
        status, body3, raw3, _heads = server.post(
            "/api/action/commit",
            _commit_body(proposal, base_seq,
                         confirm_token=final["confirm_token"]), sid=sid,
            headers={"Idempotency-Key": "a3-err-key-0009"})
        assert status == 200, (status, body3)
        assert _parse_sse(raw3)[0][1]["committed"] is True, raw3[:300]
        # 同一提案 + 新键 → 409 该提案已提交（§9.4 提案级幂等）
        status, body4 = _post(server, "/api/action/commit",
                              _commit_body(proposal, base_seq,
                                           confirm_token=final["confirm_token"]),
                              sid, headers={"Idempotency-Key": "a3-err-key-0010"})
        assert (status, body4.get("error")) == (409, "该提案已提交"), (status, body4)
    notdnd_web._sessions.pop(sid, None)


def check_action_commit_combat_lock_and_server_error():
    """战斗锁 → 409；钩子炸了 → 500 且**不开** SSE（§9.5 末段）。"""
    sid = "a3-combat"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, combat=True)
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)        # propose 不结算（§7.5）
        raw = _raw_http(server.port, "/api/action/commit",
                        _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                        headers={"Idempotency-Key": "a3-combat-key-0001"})
        assert _status_lines(raw) == 1 and _raw_status(raw) == 409, raw[:200]
        assert _raw_json(raw)["error"] == "战斗还没结束", _raw_json(raw)
        assert b"event: result" not in raw, "状态行已写 409 就不再开 SSE"
    notdnd_web._sessions.pop(sid, None)

    sid = "a3-server-error"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    events_path = _campaign_file(sid, "events.ndjson")

    class BoomHook:
        def enqueue(self, proposal, draft, *, base_seq):
            raise RuntimeError("钩子炸了")

        def cancel(self, task_id):
            return False

    saved_hook = notdnd_web.SCHEDULE_HOOK
    notdnd_web.SCHEDULE_HOOK = BoomHook()
    try:
        with _LocalServer() as server:
            status, prop = _propose_action(server, sid)
            raw = _raw_http(server.port, "/api/action/commit",
                            _commit_body(prop["proposal"], prop["base_seq"]),
                            sid=sid,
                            headers={"Idempotency-Key": "a3-boom-key-0001"})
            assert _status_lines(raw) == 1 and _raw_status(raw) == 500, raw[:200]
            assert _raw_json(raw)["error"].startswith("服务器内部错误："), \
                _raw_json(raw)
            assert b"event: result" not in raw, "500 之后不许再开 SSE"
            assert open(events_path, encoding="utf-8").read().strip() == "", \
                "钩子失败时不该落任何事件"
    finally:
        notdnd_web.SCHEDULE_HOOK = saved_hook
    notdnd_web._sessions.pop(sid, None)


def check_action_schedule_hook():
    """排程钩子（§8.2 / §8.3）：空实现回的键固定六个；管线恰好调一次。"""
    empty = notdnd_web._SyncOnlySchedule()
    hook_out = empty.enqueue({"proposal_id": "p_x"}, {"draft": True}, base_seq=3)
    assert set(hook_out) == {"mode", "task_id", "start_at", "expected_end_at",
                             "barrier", "resume_token"}, hook_out
    assert hook_out == {"mode": "sync", "task_id": None, "start_at": 0,
                        "expected_end_at": 0, "barrier": False,
                        "resume_token": None}, hook_out
    assert empty.cancel("t-1") is False, "M8 的同步行动不可取消（§8.3）"

    sid = "a3-hook"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    calls = []

    class FakeHook:
        """假钩子：记录调用（深拷贝入参，管线之后还会改草案）。"""

        def enqueue(self, proposal, draft, *, base_seq):
            calls.append({"proposal": json.loads(json.dumps(proposal)),
                          "draft": json.loads(json.dumps(draft)),
                          "base_seq": base_seq})
            return empty.enqueue(proposal, draft, base_seq=base_seq)

        def cancel(self, task_id):
            return False

    saved_hook = notdnd_web.SCHEDULE_HOOK
    notdnd_web.SCHEDULE_HOOK = FakeHook()
    try:
        with _LocalServer() as server:
            status, prop = _propose_action(server, sid)
            assert status == 200, (status, prop)
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-hook-key-0001"})
            assert status == 200, (status, body)
            assert _parse_sse(raw)[0][1]["committed"] is True, raw[:300]
    finally:
        notdnd_web.SCHEDULE_HOOK = saved_hook
    assert len(calls) == 1, "一次提交只排一次，实际 %d 次" % len(calls)
    call = calls[0]
    assert call["proposal"]["proposal_id"] == prop["proposal"]["proposal_id"], call
    assert call["base_seq"] == prop["base_seq"], call
    # 钩子在**提交之前**收到草案：未提交、无事件引用
    assert call["draft"]["committed"] is False, call["draft"]
    assert call["draft"]["commit_ref"] is None, call["draft"]
    assert prism_core.validate_result(call["draft"]) == [], call["draft"]
    notdnd_web._sessions.pop(sid, None)


def check_action_idempotency():
    """幂等（§7.7 / §9.4）：同键重放回首次结果、不写新事件；换键 → 409。"""
    sid = "a3-idem"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    events_path = _campaign_file(sid, "events.ndjson")

    def event_lines() -> int:
        with open(events_path, encoding="utf-8") as handle:
            return len([line for line in handle if line.strip()])

    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        proposal, base_seq = prop["proposal"], prop["base_seq"]
        body = _commit_body(proposal, base_seq)
        status, head, raw, _heads = server.post(
            "/api/action/commit", body, sid=sid,
            headers={"Idempotency-Key": "a3-idem-key-0001"})
        assert status == 200, (status, head)
        first = _parse_sse(raw)[0][1]
        lines_after_first = event_lines()
        assert lines_after_first == 1, "首次提交应恰一条事件"
        # 同键同载荷（连 base_seq 已过期也照旧）：重放，不结算、不写事件
        status, head, raw, _heads = server.post(
            "/api/action/commit", body, sid=sid,
            headers={"Idempotency-Key": "a3-idem-key-0001"})
        assert status == 200, (status, head)
        replayed = _parse_sse(raw)
        assert [name for name, _payload in replayed] == ["result", "done"], replayed
        assert replayed[0][1] == first, replayed[0][1]
        assert replayed[-1][1] == {"status": "ok", "replayed": True}, replayed[-1]
        assert event_lines() == lines_after_first, "重放不得写新事件"
        # 换个键再提同一份提案 → 409 该提案已提交（防「换个键再赌一次」）
        status, body2, _raw, _heads = server.post(
            "/api/action/commit", body, sid=sid,
            headers={"Idempotency-Key": "a3-idem-key-0002"})
        assert (status, body2.get("error")) == (409, "该提案已提交"), (status, body2)
        # 新提案 + 新键 → 正常提交；同一行动第二次走 already_done（不重复发奖）
        status, prop2 = _propose_action(server, sid)
        assert status == 200, (status, prop2)
        assert prop2["base_seq"] == first["commit_ref"]["seq_to"], prop2
        status, head2, raw2, _heads = server.post(
            "/api/action/commit",
            _commit_body(prop2["proposal"], prop2["base_seq"]), sid=sid,
            headers={"Idempotency-Key": "a3-idem-key-0003"})
        assert status == 200, (status, head2)
        second = _parse_sse(raw2)[0][1]
        assert second["status"] == "success", second
        assert second["rule_ref"]["perf"] == "already_done", second["rule_ref"]
        assert second["commit_ref"]["seq_to"] == 2, second["commit_ref"]
        assert event_lines() == 2, "第二次提交是**新**提案：各得一条事件"
    notdnd_web._sessions.pop(sid, None)


def check_action_narration_and_frozen_snapshot():
    """第 6 段（§7.2 / §7.5）：在线叙事用提交后快照重拼，帧带 action_id / seq_to。"""
    sid = "a3-narrate"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    fake = _FakeTransport()
    with _guide_online(_ONLINE_ENV, fake):
        with _LocalServer() as server:
            status, prop = _propose_action(server, sid)
            assert status == 200, (status, prop)
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-narrate-key-0001"})
            assert status == 200, (status, body)
            events = _parse_sse(raw)
            names = [name for name, _payload in events]
            assert names == ["result", "narration", "usage", "done"], names
            result = events[0][1]
            narration = events[1][1]
            assert result["committed"] is True, result
            assert narration["action_id"] == result["action_id"], narration
            assert narration["seq_to"] == result["commit_ref"]["seq_to"], narration
            assert narration["text"].strip(), narration
            assert narration["beats"], narration
            assert events[2][1].get("prompt_tokens") == 7, events[2]
            # 叙事请求只发一次，且 L4 带的是**已提交**的判定与行动菜单（§7.2）
            assert len(fake.calls) == 1, fake.calls
            messages = fake.calls[0]["payload"]["messages"]
            l4 = messages[-1]["content"]
            assert "判定：" in l4 and result["action_id"] in l4, l4
            assert "行动菜单：a-lock 撬锁" in l4, l4
    # 冻结快照是**值快照**：提交之后世界再变，副本不受影响（§7.5）
    session = notdnd_web.Session.load(sid)
    snapshot = notdnd_web._action_snapshot(session.rules, session.guide)
    session.rules["scene"]["actions"].append({"id": "a-extra", "label": "加戏"})
    assert [item["id"] for item in snapshot["rules"]["scene"]["actions"]] == \
        ["a-lock"], snapshot["rules"]["scene"]["actions"]
    notdnd_web._sessions.pop(sid, None)


def check_action_narration_failure_keeps_result():
    """叙事失败 → `fallback`，`result` 仍已提交、不回滚（§7.3 / §7.7）。"""
    sid = "a3-narrate-fail"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    fake = _FakeTransport([RuntimeError("上游断线")])
    with _guide_online(_ONLINE_ENV, fake):
        with _LocalServer() as server:
            status, prop = _propose_action(server, sid)
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-narrate-fail-key-0001"})
            assert status == 200, (status, body)
            events = _parse_sse(raw)
            names = [name for name, _payload in events]
            assert names == ["result", "fallback", "done"], names
            assert events[0][1]["committed"] is True, events[0]
            assert notdnd_web.FALLBACK_NARRATION in events[1][1]["text"], events[1]
            assert events[-1][1] == {"status": "ok"}, events[-1]
            assert "a-lock" in _read_save(sid)["rules"]["done_actions"], "结果不得回滚"
    notdnd_web._sessions.pop(sid, None)


def check_action_save_failure_keeps_result():
    """已提交的结果不回滚（§7.7）：门面落盘失败只让 `done` 如实回报。

    这也是「状态行前 / 后行为分叉」的后半：状态行已经写出，失败只能走 SSE——
    绝不允许出现第二行 HTTP 状态。
    """
    sid = "a3-save-failure"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        with _storage_fault(replace=True):
            raw = _raw_http(
                server.port, "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "a3-save-key-0001"})
        assert _status_lines(raw) == 1, "落盘失败也不许发第二行状态：%r" % raw[:160]
        events = _parse_sse(_dechunk(raw))
        names = [name for name, _payload in events]
        assert names[0] == "result", names
        assert events[0][1]["committed"] is True, events[0]
        assert events[-1] == ("done", {"status": "error",
                                       "reason": "save_failed"}), events[-1]
    # 事件已经落盘：从磁盘重放（检查点 + 事件日志）仍能看到这次提交
    notdnd_web._sessions.pop(sid, None)
    reloaded = notdnd_web.Session.load(sid)
    assert "a-lock" in reloaded.rules["done_actions"], reloaded.rules["done_actions"]
    notdnd_web._sessions.pop(sid, None)


def check_action_commit_json_accept():
    """`Accept: application/json` → 非流式整段结果，没有 SSE（§9.3）。"""
    sid = "a3-json"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        status, head, raw, heads = server.post(
            "/api/action/commit",
            _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
            headers={"Idempotency-Key": "a3-json-key-0001",
                     "Accept": "application/json"})
        assert status == 200, (status, head)
        assert "text/event-stream" not in heads.get("content-type", ""), heads
        assert raw.lstrip().startswith(b"{"), raw[:80]      # 整段 JSON，不是 SSE
        assert head["status"] == "success" and head["committed"] is True, head
        assert head["commit_ref"]["seq_to"] == 1, head
    notdnd_web._sessions.pop(sid, None)


def check_action_precondition_no_party():
    """桌上还没有角色 → 409 `现在做不了`（§6.4 前置条件面，§9.5）。

    两条路径都要拦：`mode: "action"` 的提案构造（没有行动者可归属），以及
    `commit` 的回带重验（提案里的行动者不在本局队伍里）。
    """
    sid = "a3-no-party"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, party=False)
    with _LocalServer() as server:
        status, body = _propose_action(server, sid)
        assert (status, body.get("error")) == (409, "现在做不了"), (status, body)
    # 队伍里有角色：先拿一份合法提案，再把行动者换成不存在的 id 回带
    sid = "a3-ghost-actor"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        ghost = dict(prop["proposal"], proposal_id="p_" + "4" * 32,
                     actor={"kind": "player", "id": "u-ghost"})
        status, body = _post(server, "/api/action/commit",
                             _commit_body(ghost, prop["base_seq"]), sid,
                             headers={"Idempotency-Key": "a3-ghost-key-0001"})
        assert (status, body.get("error")) == (409, "现在做不了"), (status, body)
    notdnd_web._sessions.pop(sid, None)


def check_legacy_migration_adds_chron_block():
    """M9-C5：老档（无 `chron` 块）迁移后自带一块**空** CHRON 块，且 CHRON 读得动。

    `_LEGACY_DEFAULTS` 的「默认值工厂」是网页层唯一该管 CHRON 的地方：块形状归
    `chron.new_block()`（本文件不另写一份字段名），老档缺块时由迁移补上；后续 save
    只按块名覆盖 `rules` / `atlas` / `guide` / `room`，**不得**把这块抹掉（state.stage
    的按块合并语义）。
    """
    sid = "legacy-chron-1"
    _write_save(_save_path(sid), {"sid": sid, "created": 1700000000.0, "log": [], "seq": 0,
                                  "save_name": "老档"})
    session = notdnd_web.Session.load(sid)
    assert session is not None, "老档必须能载入"
    session.save()                                          # 第一次 save 落成新档
    assert not os.path.exists(_save_path(sid)), "迁移成功后老档应被删掉"

    handle = state.open_campaign(sid, base_dir=str(notdnd_web.SAVE_DIR))
    assert handle.view()["blocks"]["chron"] == chron.new_block(), handle.view()["blocks"]
    restored = chron.restore(handle)                        # C5 的加载路径读得动
    assert restored["block"] == chron.new_block(0, seq=handle.view()["seq"]), restored["block"]
    assert restored["block"]["actions"] == {} and restored["block"]["idempotency"] == {}
    assert restored["world_minute"] == 0

    # 再存一次：门面不改这块，engine 的按块合并也必须保住它。
    notdnd_web._sessions.pop(sid, None)
    again = notdnd_web.Session.load(sid)
    assert again is not None
    again.add_log("narrative", "重启后的一拍")
    again.save()
    after = state.open_campaign(sid, base_dir=str(notdnd_web.SAVE_DIR))
    assert after.view()["blocks"]["chron"] == chron.new_block(), \
        "后续 save 不得动 CHRON 块：%r" % (after.view()["blocks"]["chron"],)


# --------------------------------------------------------------------------
# CHRON 排程闭环（M9-C6，CHRON-DESIGN §4.3 / §6 / §8，Issue #313）
# --------------------------------------------------------------------------


def _long_entry(**over) -> dict:
    """长行动的行动表条目：30 分钟、允许推迟（§4.2 的三个排程字段）。"""
    entry = {"label": "守夜", "time_cost": {"value": 30, "unit": "minute"},
             "schedule_hint": {"can_defer": True, "depends_on": [], "priority": 0}}
    entry.update(over)
    return entry


def _chron_block(sid: str) -> dict:
    """本局 CHRON 块的**调度器状态**（C5 的恢复时序；测试只读，不落盘）。"""
    handle = state.open_campaign(sid, base_dir=str(notdnd_web.SAVE_DIR))
    return chron.restore(handle)["state"]


def _chron_card(sid: str, task_id: str) -> dict:
    """CHRON 行动卡（§4.1）；没有这张卡 → 空 dict。"""
    return _chron_block(sid)["actions"].get(task_id) or {}


def _chron_event_types(sid: str) -> list:
    """本局状态层事件类型（按 seq 升序）——「落了几条事件」的判据。"""
    path = _campaign_file(sid, "events.ndjson")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line)["type"] for line in handle if line.strip()]


def _schedule_long(server, sid, *, action_id="a-long", key="c6-long-key-0001"):
    """提案 + 排程一个长行动：返回 (proposal, scheduled 回执)。"""
    status, prop = _propose_action(server, sid, action_id=action_id)
    assert status == 200, (status, prop)
    status, body, raw, _heads = server.post(
        "/api/action/commit", _commit_body(prop["proposal"], prop["base_seq"]),
        sid=sid, headers={"Idempotency-Key": key})
    assert status == 200, (status, body)
    frames = _parse_sse(raw)
    assert [name for name, _payload in frames] == ["scheduled", "done"], frames
    return prop, frames[0][1]


def check_chron_long_action_round():
    """长行动：`commit` 立即回 `scheduled`；**推进到到期**才结算并出结果与叙事。

    覆盖验收 2 / 3 / 4：`enqueue` 的 async 结构（`task_id` / 世界分钟 / 屏障）、
    不锁玩家（这一回合不结算、不落规则事件）、推进入口（`advance_to` 与
    `next_barrier`）、离线叙事走兜底句、**重启后**（丢掉进程内会话缓存）照常推进。
    """
    sid = "c6-long-round"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry())
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid, action_id="a-long")
        assert status == 200, (status, prop)
        proposal = prop["proposal"]
        # 行动表的排程字段进了提案（§4.2 的既有字段，服务端只读不造）
        assert proposal["schedule_hint"] == {"can_defer": True, "depends_on": [],
                                             "priority": 0}, proposal
        assert proposal["time_cost"] == {"value": 30, "unit": "minute"}, proposal
        assert proposal["interrupt_conditions"] == [], proposal

        status, body, raw, _heads = server.post(
            "/api/action/commit", _commit_body(proposal, prop["base_seq"]),
            sid=sid, headers={"Idempotency-Key": "c6-long-key-0001"})
        assert status == 200, (status, body)
        frames = _parse_sse(raw)
        assert [name for name, _payload in frames] == ["scheduled", "done"], frames
        scheduled, done = frames[0][1], frames[1][1]
        assert done == {"status": "ok"}, done
        assert scheduled["status"] == "scheduled", scheduled
        assert scheduled["mode"] == "async", scheduled
        assert scheduled["task_id"] == "act_" + proposal["proposal_id"][2:], scheduled
        assert (scheduled["start_at"], scheduled["expected_end_at"]) == (0, 30), scheduled
        assert scheduled["barrier"] is False, scheduled
        assert scheduled["resume_token"] is None, scheduled
        # 排程事件已消耗 seq 1：回执里的基线是**提交后**的新序号
        assert scheduled["base_seq"] == 1, scheduled
        # **不锁玩家**：这一回合没有结算、没有规则后果，只有一条排程事件
        assert _chron_event_types(sid) == ["action.scheduled"], _chron_event_types(sid)
        card = _chron_card(sid, scheduled["task_id"])
        assert card["status"] == "scheduled" and card["due_minute"] == 30, card
        assert card["kind"] == "a-long" and card["actor"] == "u-1", card
        assert "done_actions" not in _read_save(sid)["rules"] or \
            not _read_save(sid)["rules"]["done_actions"], "排程不该结算规则"

        # 同键重放：同一份 `scheduled` 回执，不排第二次、不写事件
        status, body, raw, _heads = server.post(
            "/api/action/commit", _commit_body(proposal, prop["base_seq"]),
            sid=sid, headers={"Idempotency-Key": "c6-long-key-0001"})
        assert status == 200, (status, body)
        again = _parse_sse(raw)
        assert [name for name, _payload in again] == ["scheduled", "done"], again
        assert again[0][1] == scheduled, again[0][1]
        assert again[1][1] == {"status": "ok", "replayed": True}, again[1]
        assert _chron_event_types(sid) == ["action.scheduled"], "重放不得写事件"
        # 换键再提同一份提案 → 409 该提案已提交（提案已被排程消费）
        status, body, _raw, _heads = server.post(
            "/api/action/commit", _commit_body(proposal, prop["base_seq"]),
            sid=sid, headers={"Idempotency-Key": "c6-long-key-0002"})
        assert (status, body.get("error")) == (409, "该提案已提交"), (status, body)

        # 推进入口覆盖 `next_barrier`：下一个可停的分钟就是到期点 30
        status, barrier = server.get("/api/chron/next_barrier", sid=sid)
        assert status == 200, (status, barrier)
        assert barrier == {"status": "ok", "world_minute": 0,
                           "next_barrier": 30}, barrier

        # **重启**：丢掉进程内会话缓存（真实读盘 + C5 恢复时序）后照样推进结算
        notdnd_web._sessions.pop(sid, None)
        status, out, _raw, _heads = server.post("/api/chron/advance",
                                                {"target_minute": 30}, sid=sid)
        assert status == 200, (status, out)
        assert out["world_minute"] == 30 and out["target"] == 30, out
        assert out["next_barrier"] is None, out
        assert [ev["type"] for ev in out["events"]] == ["action.completed"], out["events"]
        assert len(out["settled"]) == 1, out["settled"]
        report = out["settled"][0]
        assert report["action_id"] == scheduled["task_id"], report
        assert report["status"] == "success", report
        result = report["result"]
        assert prism_core.validate_result(result) == [], result
        assert result["committed"] is True, result
        assert result["status"] == "success", result
        assert result["costs"] == [{"kind": "time", "value": 30, "unit": "minute"}], result
        assert result["commit_ref"]["seq_to"] == 2, result        # 排程 1 + 结算 1
        assert result["proposal_id"] == proposal["proposal_id"], result
        # 离线（无 AI 配置）叙事走既有兜底句，不依赖模型（§7.3 / P4）
        assert report["narration"]["event"] == "fallback", report
        assert report["narration"]["text"] == notdnd_web.FALLBACK_NARRATION, report
        assert report["narration"]["action_id"] == result["action_id"], report
        assert report["narration"]["seq_to"] == 2, report
        # 叙事落流：`/api/log?since=` 增量取得到这一拍
        status, log = server.get("/api/log?since=0", sid=sid)
        assert status == 200, (status, log)
        assert [entry["kind"] for entry in log["log"]] == ["narrate"], log
        assert log["log"][0]["text"] == notdnd_web.FALLBACK_NARRATION, log
        # 事件落库：排程 + 结算各一条；卡到终态、写实际结束分钟
        assert _chron_event_types(sid) == ["action.scheduled", "action.completed"], \
            _chron_event_types(sid)
        card = _chron_card(sid, scheduled["task_id"])
        assert card["status"] == "completed" and card["end_minute"] == 30, card
        # 规则真结算（离线也要真改世界）：`done_actions` 进了存档
        assert "a-long" in _read_save(sid)["rules"]["done_actions"], \
            _read_save(sid)["rules"].get("done_actions")

        # 再推到 60：区间内没有到期 → 只有一条 `clock.advance`（纯压缩留痕），
        # 世界时间前进且不重复结算（§3.3 / §8.2 第 2 步）
        status, out2, _raw, _heads = server.post("/api/chron/advance",
                                                 {"target_minute": 60}, sid=sid)
        assert status == 200, (status, out2)
        assert out2["settled"] == [] and out2["world_minute"] == 60, out2
        assert [ev["type"] for ev in out2["events"]] == ["clock.advance"], out2["events"]
        assert _chron_event_types(sid) == ["action.scheduled", "action.completed",
                                          "clock.advance"], _chron_event_types(sid)
        status, view = server.get("/api/state", sid=sid)
        assert view["world_time"]["world_minute"] == 60, view
        # 推进入参：回拨 → 400 `世界时间只增不减`；垃圾 → 400 `推进入参不合法`
        status, body = _post(server, "/api/chron/advance", {"target_minute": 30}, sid)
        assert (status, body.get("error")) == (400, "世界时间只增不减"), (status, body)
        for bad in (True, "30", None, -1, 1.5):
            status, body = _post(server, "/api/chron/advance",
                                 {"target_minute": bad}, sid)
            assert (status, body.get("error")) == (400, "推进入参不合法"), (bad, status, body)
    notdnd_web._sessions.pop(sid, None)


def check_chron_long_action_barrier():
    """屏障置位（验收 2）：`kind` / 中断条件命中 §6.2 的判据时 `barrier = true`。

    两条路径都进队列：① `can_defer = true`，且**行动名**是 CHRON 认得的
    `combat`（`KIND_BARRIERS` 的 B1：进入战斗）；② 没有 `schedule_hint`，
    但中断条件带 `enemy_appears`（B1 的中断侧）。CHRON 的 `kind` 取
    `intent.verb`（= 场景行动 id）：它既是 PRISM 结算的入口
    （`perform_action` 只认它），也是屏障判据认的「行动名」——按 CHRON §6.2
    「只认名字，名字由规则包给」。
    """
    sid = "c6-barrier"
    _seed_action_session(
        notdnd_web.SAVE_DIR, sid, action_id="combat",
        entry={"label": "迎战", "kind": "check", "auto_pass": True,
               "on_pass": "挡下了。", "time_cost": {"value": 10, "unit": "minute"},
               "schedule_hint": {"can_defer": True, "depends_on": [], "priority": 0}})
    with _LocalServer() as server:
        _prop, scheduled = _schedule_long(server, sid, action_id="combat",
                                         key="c6-barrier-key-0001")
        assert scheduled["barrier"] is True, scheduled
        assert (scheduled["start_at"], scheduled["expected_end_at"]) == (0, 10), scheduled
        card = _chron_card(sid, scheduled["task_id"])
        assert card["kind"] == "combat", card
        # 取消掉，别让它挂着（取消也顺带覆盖 §4.3 的映射）
        status, body = _post(server, "/api/chron/cancel",
                             {"task_id": scheduled["task_id"]}, sid)
        assert (status, body["cancelled"]) == (200, True), (status, body)
    notdnd_web._sessions.pop(sid, None)

    sid = "c6-interrupt"
    _seed_action_session(
        notdnd_web.SAVE_DIR, sid, action_id="a-watch",
        entry={"label": "盯梢", "time_cost": {"value": 30, "unit": "minute"},
               "interrupt_conditions": [{"kind": "enemy_appears"}]})
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid, action_id="a-watch")
        assert status == 200, (status, prop)
        # 没有 schedule_hint：默认 `can_defer = false`，但中断条件把它推进队列
        assert prop["proposal"]["schedule_hint"] == {"can_defer": False,
                                                     "depends_on": [],
                                                     "priority": 0}, prop
        assert prop["proposal"]["interrupt_conditions"] == [{"kind": "enemy_appears"}], prop
        status, body, raw, _heads = server.post(
            "/api/action/commit",
            _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
            headers={"Idempotency-Key": "c6-interrupt-key-0001"})
        assert status == 200, (status, body)
        scheduled = _parse_sse(raw)[0][1]
        assert scheduled["status"] == "scheduled", scheduled
        assert scheduled["barrier"] is True, scheduled
        card = _chron_card(sid, scheduled["task_id"])
        assert card["interrupt_conditions"] == [{"kind": "enemy_appears"}], card
    notdnd_web._sessions.pop(sid, None)


def check_chron_cancel_mapping():
    """`cancel` 对齐（验收 5）：L1 `cancel(task_id)` ↔ L2 的取消入口可用且有测试。

    L2 的取消入口是 `settle(state, action_id, "cancelled", …)`（§4.2 的
    `→ cancelled` 行），不是 §4.3 表里的 `cancel(action_id, at_minute)` —— 映射
    语义不变（`task_id` 即 `action_id`，返回 bool）。取消写一条 `action.cancelled`
    事件；幂等（再取消回 `cancelled: false`）。
    """
    sid = "c6-cancel"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry())
    session = notdnd_web.Session.load(sid)
    assert session is not None
    hook = notdnd_web._schedule_hook(session)
    assert hook.cancel("act_ffffffffffffffffffffffffffffffff") is False, \
        "没有这张卡：L1 `cancel` 回 False（§4.3 映射表末行）"
    notdnd_web._sessions.pop(sid, None)

    with _LocalServer() as server:
        _prop, scheduled = _schedule_long(server, sid, action_id="a-long",
                                          key="c6-cancel-key-0001")
        task_id = scheduled["task_id"]
        # 缺 `task_id` → 400 固定短语
        status, body = _post(server, "/api/chron/cancel", {}, sid)
        assert (status, body.get("error")) == (400, "没有这个任务"), (status, body)
        status, body = _post(server, "/api/chron/cancel", {"task_id": task_id}, sid)
        assert status == 200, (status, body)
        assert body == {"status": "ok", "task_id": task_id, "cancelled": True,
                        "world_minute": 0}, body
        card = _chron_card(sid, task_id)
        assert card["status"] == "cancelled" and card["end_minute"] == 0, card
        assert _chron_event_types(sid) == ["action.scheduled", "action.cancelled"], \
            _chron_event_types(sid)
        # 幂等：再取消回 `cancelled: false`，不写新事件
        status, body = _post(server, "/api/chron/cancel", {"task_id": task_id}, sid)
        assert status == 200 and body["cancelled"] is False, (status, body)
        assert _chron_event_types(sid) == ["action.scheduled", "action.cancelled"], \
            _chron_event_types(sid)
        # 取消之后没有可停的分钟，推进也不再结算它
        status, barrier = server.get("/api/chron/next_barrier", sid=sid)
        assert barrier["next_barrier"] is None, barrier
        status, out, _raw, _heads = server.post("/api/chron/advance",
                                                {"target_minute": 30}, sid=sid)
        assert status == 200 and out["settled"] == [], out
        assert _chron_event_types(sid)[-1] == "clock.advance", _chron_event_types(sid)
    notdnd_web._sessions.pop(sid, None)


def check_chron_hook_injection():
    """L1 适配器与注入选择（§8.3）：默认 = CHRON 适配器；替换过的实现原样用。

    单元面：`mode = "sync"` 的六键形状（含 `start_at` = 当前世界分钟）、
    `bind` 只对默认适配器生效、`_action_is_deferred` 的真值表、
    `time_cost` → 分钟的换算表（§8.1 只锁枚举，换算在适配器里）。
    """
    assert isinstance(notdnd_web.SCHEDULE_HOOK, notdnd_web._ChronScheduleHook), \
        "默认注入点是 CHRON 的 L1 适配器（§8.3）"
    assert hasattr(notdnd_web.SCHEDULE_HOOK, "bind"), notdnd_web.SCHEDULE_HOOK

    sid = "c6-inject"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    session = notdnd_web.Session.load(sid)
    assert session is not None
    hook = notdnd_web._schedule_hook(session)
    assert isinstance(hook, notdnd_web._ChronScheduleHook) and hook is not session, hook
    assert hook.session is session, "bind 取的是**本局**的绑定副本"
    out = hook.enqueue({"proposal_id": "p_x", "schedule_hint": {"can_defer": False}},
                       None, base_seq=0)
    assert set(out) == {"mode", "task_id", "start_at", "expected_end_at",
                        "barrier", "resume_token"}, out
    assert out == {"mode": "sync", "task_id": None, "start_at": 0,
                   "expected_end_at": 0, "barrier": False,
                   "resume_token": None}, out
    # 没有绑定本局（模块级单例 / session=None）也回 sync：绝不**悄悄**排程
    assert notdnd_web.SCHEDULE_HOOK.enqueue({"proposal_id": "p_x"}, None,
                                            base_seq=0)["mode"] == "sync"

    class BareHook:
        """没有 `bind` 的替换实现（A3 假钩子同款）：原样使用，不再包一层。"""

        def enqueue(self, proposal, draft, *, base_seq):
            return {"mode": "sync", "task_id": None, "start_at": 0,
                    "expected_end_at": 0, "barrier": False, "resume_token": None}

        def cancel(self, task_id):
            return False

    saved = notdnd_web.SCHEDULE_HOOK
    bare = BareHook()
    notdnd_web.SCHEDULE_HOOK = bare
    try:
        assert notdnd_web._schedule_hook(session) is bare, "替换过的实现原样使用"
    finally:
        notdnd_web.SCHEDULE_HOOK = saved
    notdnd_web._sessions.pop(sid, None)

    # `_action_is_deferred`：`can_defer` 或非空中断条件（§4.3 关键约定）
    assert notdnd_web._action_is_deferred({}) is False
    assert notdnd_web._action_is_deferred({"schedule_hint": {}}) is False
    assert notdnd_web._action_is_deferred(
        {"schedule_hint": {"can_defer": False}, "interrupt_conditions": []}) is False
    assert notdnd_web._action_is_deferred({"schedule_hint": {"can_defer": True}}) is True
    assert notdnd_web._action_is_deferred(
        {"interrupt_conditions": [{"kind": "hazard"}]}) is True
    # `time_cost` → 整数分钟（§8.1 的枚举 → §2.4 的刻度，都在 CHRON 里）
    assert notdnd_web._chron_time_minutes(
        {"time_cost": {"value": 5, "unit": "minute"}}) == 5
    assert notdnd_web._chron_time_minutes(
        {"time_cost": {"value": 2, "unit": "hour"}}) == 120
    assert notdnd_web._chron_time_minutes(
        {"time_cost": {"value": 1, "unit": "watch"}}) == chron.NOMINAL_WATCH_MINUTES
    assert notdnd_web._chron_time_minutes(
        {"time_cost": {"value": 1, "unit": "day"}}) == chron.MINUTES_PER_DAY
    assert notdnd_web._chron_time_minutes(
        {"time_cost": {"value": 3, "unit": "turn"}}) == 3 * chron.MINOR_ACTION_MINUTES
    assert notdnd_web._chron_time_minutes({}) == 0
    # `task_id` ↔ `proposal_id` 的派生是双向的（结算时由卡反推提案）
    proposal_id = "p_" + "a" * 32
    task_id = notdnd_web._chron_task_id({"proposal_id": proposal_id})
    assert task_id == "act_" + "a" * 32, task_id
    assert notdnd_web._chron_proposal_id(task_id) == proposal_id


def check_chron_dependency_semantics():
    """依赖语义（验收 2）：`depends_on` 透传到位；**拒单**翻 409 且不留痕（§5.3）。

    - 真路径：依赖 id 不存在 → `chron.schedule` 抛 `ScheduleError` → 409
      `现在做不了`（前置条件不满足，§9.5），**不落事件、不留卡**；
    - `rejected`（§5.3 的「依赖有环」）：C3 的 `deps_satisfied` 不变式要求
      已排期卡片的依赖**必须存在**，两卡互依赖的第二张永远进不了调度器——
      所以这条在**落盘状态**里不可构造。这里用替身把映射钉死：适配器必须把
      `rejected` 翻成同一个 409，**不落事件、不留卡**，绝不静默忽略。
    """
    sid = "c6-deps"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry())
    with _LocalServer() as server:
        ghost = prism_core.new_proposal(
            proposal_id="p_" + "d" * 32,
            actor={"kind": "player", "id": "u-1"},
            intent={"verb": "a-long", "target": None, "desired": "守夜", "means": ""},
            source="action", risk="R1",
            time_cost={"value": 30, "unit": "minute"},
            schedule_hint={"can_defer": True, "depends_on": ["act_nope"],
                           "priority": 0})
        status, body = _post(server, "/api/action/commit",
                             _commit_body(ghost, 0), sid,
                             headers={"Idempotency-Key": "c6-deps-key-0001"})
        assert (status, body.get("error")) == (409, "现在做不了"), (status, body)
        assert _chron_event_types(sid) == [], _chron_event_types(sid)
        assert _chron_block(sid)["actions"] == {}, _chron_block(sid)

        saved_schedule = chron.schedule
        def fake_schedule(state, draft, **kwargs):
            """替身：无论输入都回 `rejected`（§5.3 有环的语义形状）。"""
            return {"status": "rejected", "action_id": draft["action_id"],
                    "cycle": [draft["action_id"]], "state": state, "events": []}
        chron.schedule = fake_schedule
        try:
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(_cycled_proposal("e"), 0), sid,
                headers={"Idempotency-Key": "c6-deps-key-0002"})
        finally:
            chron.schedule = saved_schedule
        assert (status, body.get("error")) == (409, "现在做不了"), (status, body)
        assert b"event:" not in raw, "拒单不许开 SSE"
        assert _chron_event_types(sid) == [], _chron_event_types(sid)
        assert _chron_block(sid)["actions"] == {}, _chron_block(sid)
    notdnd_web._sessions.pop(sid, None)


def _cycled_proposal(hex_digit: str, **over) -> dict:
    """造一份**可推迟**的提案（`can_defer`，`intent.verb = a-long`）。"""
    proposal = prism_core.new_proposal(
        proposal_id="p_" + hex_digit * 32,
        actor={"kind": "player", "id": "u-1"},
        intent={"verb": "a-long", "target": None, "desired": "守夜", "means": ""},
        source="action", risk="R1",
        time_cost={"value": 30, "unit": "minute"},
        schedule_hint={"can_defer": True, "depends_on": ["act_x"], "priority": 0})
    proposal.update(over)
    assert prism_core.validate_proposal(proposal) == [], proposal
    return proposal


def check_chron_dependency_chain():
    """依赖**串行**：`depends_on` 到位前不进到期堆，前者结算后自动放行（§5.3）。

    这是 `schedule_hint.depends_on` **真的折进 CHRON** 的正向证据：第二张卡
    排得进去但**不进堆**；第一张到期结算后 `rebuild_heap` 放行第二张，
    下一次推进才结算它。
    """
    sid = "c6-chain"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry())
    with _LocalServer() as server:
        _prop, first = _schedule_long(server, sid, action_id="a-long",
                                      key="c6-chain-key-0001")
        second = _cycled_proposal("f",
                                  schedule_hint={"can_defer": True,
                                                 "depends_on": [first["task_id"]],
                                                 "priority": 0})
        status, body, raw, _heads = server.post(
            "/api/action/commit", _commit_body(second, first["base_seq"]), sid,
            headers={"Idempotency-Key": "c6-chain-key-0002"})
        assert status == 200, (status, body)
        scheduled = _parse_sse(raw)[0][1]
        assert scheduled["status"] == "scheduled", scheduled
        card = _chron_card(sid, scheduled["task_id"])
        assert card["depends_on"] == [first["task_id"]], card
        # 第二张卡与第一张**同刻安排**（start = 调度那一刻 0），但依赖未成：
        # 它**不进**到期堆（§5.3 首条），所以要等到第一张结算才被放行。
        state_now = _chron_block(sid)
        assert [entry[3] for entry in state_now["due_heap"]] == [first["task_id"]], \
            state_now["due_heap"]
        assert state_now["actions"][scheduled["task_id"]]["due_minute"] == 30, \
            state_now["actions"][scheduled["task_id"]]
        status, barrier = server.get("/api/chron/next_barrier", sid=sid)
        assert barrier["next_barrier"] == 30, barrier          # 只看到第一张的到期

        # 第一张到期结算 → `rebuild_heap` 放行第二张（放行时已到期 → 立即待办）
        status, out, _raw, _heads = server.post("/api/chron/advance",
                                                {"target_minute": 30}, sid=sid)
        assert status == 200, (status, out)
        assert [report["action_id"] for report in out["settled"]] == \
            [first["task_id"]], out["settled"]
        state_now = _chron_block(sid)
        assert [entry[3] for entry in state_now["due_heap"]] == \
            [scheduled["task_id"]], state_now["due_heap"]
        status, barrier = server.get("/api/chron/next_barrier", sid=sid)
        assert barrier["next_barrier"] == 30, barrier

        # 第二次推进才结算第二张：到期（B8）就是结算点，停在那一分钟
        status, out2, _raw, _heads = server.post("/api/chron/advance",
                                                 {"target_minute": 60}, sid=sid)
        assert status == 200, (status, out2)
        assert (out2["world_minute"], out2["stopped_at"]) == (30, 30), out2
        assert [report["action_id"] for report in out2["settled"]] == \
            [scheduled["task_id"]], out2["settled"]
        assert out2["settled"][0]["status"] == "success", out2["settled"]
        assert out2["settled"][0]["result"]["commit_ref"]["seq_to"] == 4, \
            out2["settled"][0]["result"]
        # 再推一次才到 60：区间里没有到期 → 一条 `clock.advance`
        status, out3, _raw, _heads = server.post("/api/chron/advance",
                                                 {"target_minute": 60}, sid=sid)
        assert status == 200 and out3["world_minute"] == 60, out3
        assert [ev["type"] for ev in out3["events"]] == ["clock.advance"], out3["events"]
        assert _chron_event_types(sid) == ["action.scheduled", "action.scheduled",
                                          "action.completed", "action.completed",
                                          "clock.advance"], _chron_event_types(sid)
    notdnd_web._sessions.pop(sid, None)


def check_chron_immediate_action_unchanged():
    """验收 1 的**同步对照**：`can_defer = false` 的即时行动仍走 M8 同步路径。

    A3 的既有用例（`check_action_*`）逐条盯着同步路径的响应形状；这里额外断言
    适配器对即时行动**不排程**：提交之后没有 `action.scheduled` 事件、没有
    CHRON 行动卡、没有世界时间推进，`result` 与 A3 时代同形（`mode` 概念只出现
    在钩子回执里，不进 `result` / `/api/action/*` 的响应）。
    """
    sid = "c6-immediate"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    with _LocalServer() as server:
        status, prop = _propose_action(server, sid)
        assert status == 200, (status, prop)
        assert prop["proposal"]["schedule_hint"] == {"can_defer": False,
                                                     "depends_on": [],
                                                     "priority": 0}, prop
        status, body, raw, _heads = server.post(
            "/api/action/commit",
            _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
            headers={"Idempotency-Key": "c6-immediate-key-0001"})
        assert status == 200, (status, body)
        frames = _parse_sse(raw)
        assert [name for name, _payload in frames] == ["result", "fallback", "done"], frames
        result = frames[0][1]
        assert result["committed"] is True and result["status"] == "success", result
        assert "task_id" not in result and "mode" not in result, "结果契约不变（§5.1）"
        # 同步行动**不推进世界时间**（§8.1「M8 语义」）：CHRON 一条事件都没落
        assert _chron_event_types(sid) == ["action.commit"], _chron_event_types(sid)
        assert _chron_block(sid)["world_minute"] == 0, _chron_block(sid)["world_minute"]
        assert _chron_block(sid)["actions"] == {}, _chron_block(sid)["actions"]
    notdnd_web._sessions.pop(sid, None)


def check_chron_advance_barrier_stop():
    """推进遇屏障就停（§6.2 / §6.3）：目标远在屏障之后也**不跨**。

    到期（B8）本身就是结算点：`target_minute = 100` 而到期在 30 → 结算 30 那一分钟
    的行动、`world_minute = 30`、`stopped_at = 30`，`barriers` 里能看到 B8 记录。
    """
    sid = "c6-barrier-stop"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry())
    with _LocalServer() as server:
        _prop, scheduled = _schedule_long(server, sid, action_id="a-long",
                                          key="c6-stop-key-0001")
        status, out, _raw, _heads = server.post("/api/chron/advance",
                                                {"target_minute": 100}, sid=sid)
        assert status == 200, (status, out)
        assert out["world_minute"] == 30, out
        assert out["stopped_at"] == 30, out
        assert any(record.get("id") == "B8" for record in out["barriers"]), out["barriers"]
        assert [ev["type"] for ev in out["events"]] == ["action.completed"], out["events"]
        assert out["settled"][0]["action_id"] == scheduled["task_id"], out["settled"]
        # 屏障到达之后再次推进：这次能到 100（已到达的屏障不再拦）
        status, out2, _raw, _heads = server.post("/api/chron/advance",
                                                 {"target_minute": 100}, sid=sid)
        assert status == 200 and out2["world_minute"] == 100, out2
        assert out2["settled"] == [], out2
        # `next_barrier` 的入参面：非法 → 400；早于当前 → 400（§3.2）
        for bad in ("abc", "-3", "1.5"):
            status, body = server.get("/api/chron/next_barrier?from=" + bad, sid=sid)
            assert (status, body.get("error")) == (400, "推进入参不合法"), \
                (bad, status, body)
        status, body = server.get("/api/chron/next_barrier?from=99", sid=sid)
        assert (status, body.get("error")) == (400, "世界时间只增不减"), (status, body)
        status, ok = server.get("/api/chron/next_barrier?from=100", sid=sid)
        assert ok["next_barrier"] is None and ok["world_minute"] == 100, ok
    notdnd_web._sessions.pop(sid, None)


def check_chron_next_barrier_legacy_readonly():
    """老档边界（修复轮 1）：只读 / 无操作的 CHRON 入口**不得**落盘迁移。

    `state_view`（M7-4）明确规避的那条边界：老档只有 `<sid>.json`，开状态层
    句柄 = `state.migrate_legacy(...)` 就地落盘迁移（§10.1：迁移只发生在第一次
    save）。`GET /api/chron/next_barrier` 与 `POST /api/chron/cancel` 都不该
    借读一个数把老档迁了——老档文件字节不变、`campaigns/<sid>/` 不出现。
    """
    sid = "c6-legacy-readonly"
    legacy = {"sid": sid, "created": 1700000000.0,
              "log": [{"seq": 1, "kind": "narrative", "text": "旧档旁白", "ts": 1}],
              "seq": 1, "save_name": "旧档"}
    with _Server() as server:
        save_dir = server.get("/api/saves")[1]["save_dir"]
        path = os.path.join(save_dir, "%s.json" % sid)
        _write_save(path, legacy)
        with open(path, "rb") as handle:
            before = handle.read()

        # 老档没有 CHRON 状态 → 没有屏障；200 且形状与「真的有句柄但没到期」一致
        status, body = server.get("/api/chron/next_barrier", sid=sid)
        assert (status, body) == (200, {"status": "ok", "world_minute": 0,
                                        "next_barrier": None}), (status, body)
        with open(path, "rb") as handle:
            assert handle.read() == before, "只读路由不得改写老档"
        assert not os.path.exists(_campaign_file(sid, "snapshot.json", save_dir)), \
            "只读路由不得触发老档迁移（不落盘）"

        # 无操作取消：同样不改盘、不迁移
        status, body = _post(server, "/api/chron/cancel",
                             {"task_id": "act_" + "a" * 32}, sid)
        assert (status, body) == (200, {"status": "ok", "task_id": "act_" + "a" * 32,
                                        "cancelled": False, "world_minute": 0}), \
            (status, body)
        with open(path, "rb") as handle:
            assert handle.read() == before, "无操作取消不得改写老档"
        assert not os.path.exists(_campaign_file(sid, "snapshot.json", save_dir)), \
            "无操作取消不得触发老档迁移（不落盘）"


def check_chron_new_layout_guard_control():
    """守卫的**对照**（修复轮 1）：新档（已迁移布局）上照常算屏障 / 取消。

    「未迁移老档」的判据是 `_handle is None` **且** `_legacy is not None`；
    新档尚未开句柄时开句柄只是纯读，**不得**被守卫误伤——这里造一局已落盘的
    新档（排了一张 30 分钟的卡）、把门面换成**未开句柄**的那一份，走 HTTP
    验证 `next_barrier` 与 `cancel` 照常生效。
    """
    sid = "c6-new-layout"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry())
    session = notdnd_web.Session.load(sid)
    assert session is not None
    proposal = notdnd_web._action_proposal_from_id(session, "a-long", {})
    scheduled = notdnd_web._chron_schedule(session, proposal, base_seq=0)
    task_id = scheduled["task_id"]
    notdnd_web._sessions.pop(sid, None)

    fresh = notdnd_web.Session(sid)          # 新档布局：未开句柄、也没有老档
    assert fresh._handle is None and fresh._legacy is None, (fresh._handle,
                                                             fresh._legacy)
    notdnd_web._sessions[sid] = fresh
    with _LocalServer() as server:
        status, body = server.get("/api/chron/next_barrier", sid=sid)
        assert (status, body) == (200, {"status": "ok", "world_minute": 0,
                                        "next_barrier": 30}), (status, body)
        assert fresh._handle is not None, "新档路径照常开句柄（纯读，不是迁移）"
        status, body = _post(server, "/api/chron/cancel", {"task_id": task_id}, sid)
        assert (status, body) == (200, {"status": "ok", "task_id": task_id,
                                        "cancelled": True, "world_minute": 0}), \
            (status, body)
        card = _chron_card(sid, task_id)
        assert card["status"] == "cancelled", card
    notdnd_web._sessions.pop(sid, None)


# --------------------------------------------------------------------------
# NEURA M8 内部分支（N6，NEURA-DESIGN §5.6 / §9.4 / §11 N6）
#
# `_neura_submit` = `ActingPort.submit` 的实现：复用 `_action_settle_commit`，
# 事件封套 `source="npc"`；四态返回值、**零异常逃逸**。全部走本进程直调
# （无 HTTP 路由——接线归 N7）；幂等键与 `created_at` 由 neura 侧填好，
# 这里用固定假键只测透传与分支。
# --------------------------------------------------------------------------


def _neura_proposal(action_id="a-lock", *, npc_id="npc-guard", **over) -> dict:
    """§5.6 提案形状（N3 `to_proposal` 的固定样本，字段与 §4.2 对齐）。"""
    proposal = {
        "schema_version": 1,
        "proposal_id": "p_" + "0" * 32,
        "actor": {"kind": "npc", "id": npc_id},
        "intent": {"verb": action_id, "target": None,
                   "desired": action_id, "means": ""},
        "params": {"dc_override": None, "items_used": []},
        "source": "action",
        "risk": "R1",
        "time_cost": {"value": 0, "unit": "minute"},
        "schedule_hint": {"can_defer": False, "depends_on": [], "priority": 0},
        "interruptible": True,
        "interrupt_conditions": [],
        "expected_changes": [],
        "raw_text": "",
        "created_at": 61440000,          # at_minute=1024 × 60000（不取墙钟）
    }
    proposal.update(over)
    return proposal


def _neura_session(sid: str) -> "notdnd_web.Session":
    """造一局带 `a-lock`（auto_pass）行动的存档并**从盘载入**本进程会话。"""
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    return notdnd_web.Session.load(sid)


def _neura_action_events(session) -> list:
    """状态层事件日志里行动管线写下的那些（`type` = ACTION_EVENT_TYPE）。"""
    handle = session._handle or session._open_handle()
    return [e for e in handle.events_since(0)
            if e["type"] == notdnd_web.ACTION_EVENT_TYPE]


def check_neura_submit_commit_and_replay():
    """N6：NPC 提案同步结算 → `source=="npc"` / `actor==npc_id`；同键重投
    回首次结果、事件数不变（状态层幂等命中，不重复副作用）。"""
    sid = "neura-n6-commit"
    session = _neura_session(sid)
    try:
        proposal = _neura_proposal()
        out = notdnd_web._neura_submit(
            session, proposal, idempotency_key="np_n6feed0001_0000000001_01")
        assert out["status"] == "committed", out
        assert out["reason"] is None, out
        assert isinstance(out["result"], dict), out
        event_id = out["event_id"]
        assert isinstance(event_id, str) and event_id, out
        assert out["result"]["event_ids"] == [event_id], out

        events = _neura_action_events(session)
        assert len(events) == 1, events
        assert events[0]["source"] == "npc", events[0]        # 事件封套（硬约束 3）
        assert events[0]["actor"] == "npc-guard", events[0]   # actor = npc_id
        assert events[0]["event_id"] == event_id, events[0]

        # `created_at` 透传链路：提案不被改写（世界分钟换算的毫秒不被污染）
        assert proposal["created_at"] == 61440000, proposal

        # 同键重投：结算重跑（确定性）→ 状态层命中 → 首次 event_id / 结果
        replay = notdnd_web._neura_submit(
            session, _neura_proposal(),
            idempotency_key="np_n6feed0001_0000000001_01")
        assert replay["status"] == "committed", replay
        assert replay["event_id"] == event_id, (replay, event_id)
        assert replay["result"] == out["result"], (replay, out)
        assert len(_neura_action_events(session)) == 1, "重投不得写新事件"
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_submit_stale_seq_conflict():
    """N6：`base_seq` 过期 → `conflict`（**不抛**），不落事件；调用方可重试。"""
    sid = "neura-n6-conflict"
    session = _neura_session(sid)
    try:
        def bump():
            # 在 `_action_settle_commit` 取到锁**之前**插一条真事件：把 seq 顶掉
            handle = session._handle or session._open_handle()
            result = handle.commit(
                base_seq=handle.view()["seq"], idempotency_key="neura-n6-bump-1",
                type="core.commit", actor="pc-1",
                changes=[{"block": "rules", "path": "/n", "op": "set",
                          "before": None, "after": 1}],
                rules_version="prism@data-79", source="player")
            assert result["status"] == "committed", result

        real = session.lock
        # 第 1 次 `with` 是 `_neura_submit` 读 base_seq；第 2 次在结算提交临界区
        # 入口——交错就插在那儿，复现「读 seq 之后、核验之前」被别人提交。
        session.lock = _InterleavingLock(real, at_acquire=2, hook=bump)
        try:
            out = notdnd_web._neura_submit(
                session, _neura_proposal(),
                idempotency_key="np_n6feed0001_0000000001_02")
        finally:
            session.lock = real
        assert out == {"status": "conflict", "result": None,
                       "reason": notdnd_web.ACTION_STALE_SEQ,
                       "event_id": None}, out
        assert len(_neura_action_events(session)) == 0, "冲突不落事件"

        # 同键重试（seq 已对齐）→ 照常 committed：调用方可重试的口径成立
        retry = notdnd_web._neura_submit(
            session, _neura_proposal(),
            idempotency_key="np_n6feed0001_0000000001_02")
        assert retry["status"] == "committed", retry
        assert len(_neura_action_events(session)) == 1, retry
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_submit_unsupported_no_event():
    """N6：动作表没有 / 需要掷骰 / 长行动 → `unsupported`，**不写事件**（U10）。"""
    sid = "neura-n6-unsupported"
    session = _neura_session(sid)
    dice_sid = sid + "-dice"
    _seed_action_session(notdnd_web.SAVE_DIR, dice_sid,
                         entry={"auto_pass": False, "df": 10})
    dice_session = notdnd_web.Session.load(dice_sid)
    try:
        before = len(_neura_action_events(session))

        # ① `kind` 不在动作表 → unsupported（§9.5 同判据：`_action_scene_entry`）
        out = notdnd_web._neura_submit(
            session, _neura_proposal("nope"),
            idempotency_key="np_n6feed0001_0000000001_03")
        assert out == {"status": "unsupported", "result": None,
                       "reason": notdnd_web.ACTION_NO_ACTION,
                       "event_id": None}, out

        # ② 需要掷骰（条目未声明 `auto_pass`）→ unsupported（P2：第一版不掷骰）
        out = notdnd_web._neura_submit(
            dice_session, _neura_proposal("a-lock", npc_id="npc-thief"),
            idempotency_key="np_n6feed0001_0000000001_04")
        assert out == {"status": "unsupported", "result": None,
                       "reason": notdnd_web.NEURA_NO_DICE,
                       "event_id": None}, out
        assert len(_neura_action_events(dice_session)) == 0, "掷骰提案不落事件"

        # ③ 长行动（can_defer=true）→ unsupported：NPC 提案永远同步（P7），
        #    长行动分叉（`scheduled` 回执）在进管线之前就被挡死。
        out = notdnd_web._neura_submit(
            session, _neura_proposal(schedule_hint={"can_defer": True}),
            idempotency_key="np_n6feed0001_0000000001_05")
        assert out == {"status": "unsupported", "result": None,
                       "reason": notdnd_web.NEURA_NO_DEFER,
                       "event_id": None}, out

        assert len(_neura_action_events(session)) == before, \
            "unsupported 一律不写事件"
    finally:
        notdnd_web._sessions.pop(sid, None)
        notdnd_web._sessions.pop(dice_sid, None)


def check_neura_submit_rejected_zero_escape():
    """N6：战斗中提案 → `rejected`；非预期异常也不逃逸——对**返回值形状**
    断言，而不是断言抛错（零异常逃逸口径）。"""
    sid = "neura-n6-rejected"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, combat=True)
    session = notdnd_web.Session.load(sid)
    try:
        before = len(_neura_action_events(session))
        out = notdnd_web._neura_submit(
            session, _neura_proposal(),
            idempotency_key="np_n6feed0001_0000000001_06")
        assert out["status"] == "rejected", out
        assert out["reason"] == notdnd_web.ACTION_COMBAT, out
        assert out["result"] is None and out["event_id"] is None, out
        assert len(_neura_action_events(session)) == before, "rejected 不写事件"

        # 任何意外异常同样只变成返回值（catch-all 分支）
        real = notdnd_web._action_settle_commit

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        notdnd_web._action_settle_commit = boom
        try:
            out = notdnd_web._neura_submit(
                session, _neura_proposal(),
                idempotency_key="np_n6feed0001_0000000001_07")
        finally:
            notdnd_web._action_settle_commit = real
        assert out["status"] == "rejected", out
        assert "boom" in (out["reason"] or ""), out
        assert out["result"] is None and out["event_id"] is None, out
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_action_settle_commit_source_default_player():
    """N6：`_action_settle_commit` 的 `source` 缺省 `"player"`——参数化只换
    NPC 路径的封套，玩家路径逐字节不变（硬约束 3 / 验收第 1 条）。"""
    sid = "neura-n6-player"
    session = _neura_session(sid)
    try:
        proposal = {
            "proposal_id": "p_" + "1" * 32,
            "actor": {"kind": "player", "id": "u-1"},
            "intent": {"verb": "a-lock", "target": None, "desired": "a-lock",
                       "means": ""},
            "source": "action", "risk": "R1", "raw_text": "",
            "time_cost": {"value": 0, "unit": "minute"},
            "schedule_hint": {"can_defer": False},
            "interrupt_conditions": [],
        }
        handle = session._handle or session._open_handle()
        out = notdnd_web._action_settle_commit(
            session, proposal, base_seq=handle.view()["seq"],
            idempotency_key="np_n6feed0001_0000000001_08")
        assert out["result"] and out["saved"] is True, out
        events = _neura_action_events(session)
        assert len(events) == 1, events
        assert events[0]["source"] == "player", events[0]
        assert events[0]["actor"] == "u-1", events[0]
    finally:
        notdnd_web._sessions.pop(sid, None)


# --------------------------------------------------------------------------
# NEURA 编排接线（N7，NEURA-DESIGN §9.4 / §11 N7）
#
# `_neura_tick` 在 `_chron_advance` 锁内、叙事段之前跑：view → `neura.tick`
# → 块级变更随 tick 末一次 `neura.tick` 事件落盘（A0：动作事件经
# `_neura_submit` 每 NPC ≤ 1 条；块级变更不单独开每 NPC 事件）。全部走
# 本进程直调 `_chron_advance`（接线本身无 HTTP 路由）；NPC 运行时态用
# 手工 neura 块夹具（种群初始化归 M12 内容切片），地图用确定性 stub。
# --------------------------------------------------------------------------


def _neura_seed_block(session, block: dict, key: str) -> None:
    """把手工 neura 块提交进状态层（测试夹具；`replace_block` 建键）。"""
    handle = session._handle or session._open_handle()
    out = handle.commit(
        base_seq=handle.view()["seq"], idempotency_key=key,
        type="core.commit", actor="pc-1",
        changes=[{"block": "neura", "op": "replace_block", "path": "",
                  "before": None, "after": block}],
        rules_version=notdnd_web.ACTION_RULES_VERSION, source="player")
    assert out["status"] == "committed", out


def _neura_block_of(session) -> dict:
    """本局状态层里的 neura 块（没有 → 空 dict）。"""
    handle = session._handle or session._open_handle()
    return (handle.view().get("blocks") or {}).get("neura") or {}


def _neura_stub_world(session) -> None:
    """确定性三地点小地图（market—gate—temple），队伍在 market。

    只填内存运行时态（`_atlas` / `_locus`），不碰存档——`_neura_view` 读
    的就是这两个字段；不触发 `ensure_atlas` 的懒编译（种子不可复现）。
    """
    atlas = atlas_kernel.new_atlas("w-n7", 1)
    atlas_kernel.add_frame(atlas, "f1", space="metric", z_meaning="街区",
                           cell="一次走位")
    for pid, name in (("market", "集市"), ("gate", "城门"), ("temple", "神庙")):
        atlas_kernel.add_place(atlas, {"id": pid, "name": name,
                                       "kind": "room", "frame_id": "f1"})
    atlas_kernel.add_link(atlas, "f1", "market", "gate", "北")
    atlas_kernel.add_link(atlas, "f1", "gate", "temple", "北")
    session._atlas = atlas
    session._locus = {"frame_id": "f1", "place_id": "market"}


def _neura_mover_npc(npc_id="npc-guard", place="market",
                     slot_place="gate") -> dict:
    """有全天日程 slot 的近场 NPC：每次 tick 都该按日程移动。"""
    npc = neura.empty_npc(npc_id, "sc-1")
    npc["location"]["place_id"] = place
    npc["schedule"] = [{"slot_id": "s1", "place_id": slot_place,
                        "from_tod_minute": 0, "to_tod_minute": 1440}]
    return npc


def _neura_all_events(session) -> list:
    """状态层全部事件（按 seq 升序的 (type, actor) 表）。"""
    handle = session._handle or session._open_handle()
    return [(e["type"], e.get("actor") or "")
            for e in handle.events_since(0)]


def check_neura_wired_import_and_tick_updates_block():
    """N7 验收 1 + 接线证明：`import neura` 只在 notdnd_web 顶层出现；
    `advance_to` 返回后 `blocks.neura` 已更新且 `last_tick_minute == world_minute`；
    A0：每 NPC 每 tick 最多 1 条动作事件 + 块级变更只落 1 条 `neura.tick`。"""
    # 接线断言（N6 的「删 neura.py 仍 import 成功」在本单反转为「接线后
    # import neura 且行为正确」；既有 114 项里没有该类断言，见 PR 正文说明）
    assert notdnd_web.neura is neura, "notdnd_web 必须在顶层 import neura"

    sid = "neura-n7-tick"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "neura-n7-seed-0001")

        out = notdnd_web._chron_advance(session, 10)
        assert out["status"] == "ok" and out["world_minute"] == 10, out
        assert out["stopped_at"] is None, out

        saved = _neura_block_of(session)
        guard = saved["npcs"]["npc-guard"]
        assert guard["last_tick_minute"] == out["world_minute"] == 10, guard
        # 日程移动真走了：位置到 slot 地点、有决策留痕
        assert guard["location"]["place_id"] == "gate", guard
        assert saved["journal"], "journal 必须有决策记录"

        # A0：每 NPC 每 tick ≤ 1 条动作事件（这里是 1 条）+ 块级变更 1 条
        events = _neura_all_events(session)
        action_events = [e for e in events
                         if e[0] == notdnd_web.ACTION_EVENT_TYPE]
        assert [actor for _t, actor in action_events] == ["npc-guard"], events
        assert [t for t, _a in events].count("neura.tick") == 1, events
        # 事件封套仍是 N6 的口径
        handle = session._handle or session._open_handle()
        npc_event = [e for e in handle.events_since(0)
                     if e["type"] == notdnd_web.ACTION_EVENT_TYPE][0]
        assert npc_event["source"] == "npc", npc_event
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_tick_failure_isolated():
    """N7 验收 3：`neura.tick` 抛异常 → `_chron_advance` 正常返回
    （`neura_skipped` 记录），玩家到期卡照常结算，世界时间照走。"""
    sid = "neura-n7-fail"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry())
    session = notdnd_web.Session.load(sid)
    try:
        proposal = notdnd_web._action_proposal_from_id(session, "a-long", {})
        scheduled = notdnd_web._chron_schedule(session, proposal, base_seq=0)
        task_id = scheduled["task_id"]

        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "neura-n7-seed-0002")

        real_tick = neura.tick

        def boom(*args, **kwargs):
            raise RuntimeError("neura boom")

        neura.tick = boom
        try:
            out = notdnd_web._chron_advance(session, 100)
        finally:
            neura.tick = real_tick
        # 推进照常返回、玩家到期卡已结算（§9.4：结算在 tick 之前）
        assert out["status"] == "ok" and out["world_minute"] == 30, out
        assert out["stopped_at"] == 30, out
        assert out["settled"] and out["settled"][0]["action_id"] == task_id, out
        # 失败隔离留痕
        kinds = [entry["kind"] for entry in session.log]
        assert "neura_skipped" in kinds, session.log
        # NPC 一动不动（块没被 tick 改写）
        saved = _neura_block_of(session)
        assert saved["npcs"]["npc-guard"]["last_tick_minute"] == 0, saved
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_barrier_skips_far_field():
    """N7 验收 4（§10.1 的 2.4）：屏障处（`stopped_at != None`）不跑远场——
    远场层 `last_tick_minute` 不变；跨过屏障后的非屏障推进照常到期。"""
    sid = "neura-n7-barrier"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry(
                             time_cost={"value": 1500, "unit": "minute"}))
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        proposal = notdnd_web._action_proposal_from_id(session, "a-long", {})
        notdnd_web._chron_schedule(session, proposal, base_seq=0)

        block = neura.empty_block()
        block["npcs"]["npc-near"] = _neura_mover_npc(npc_id="npc-near")
        far = neura.empty_npc("npc-far", "sc-1")
        far["importance"] = 0.9            # P5：≥ 0.7 → important_far
        far["location"]["place_id"] = "temple"
        block["npcs"]["npc-far"] = far
        _neura_seed_block(session, block, "neura-n7-seed-0003")

        # 屏障（B8 到期）在第二天：远场 NPC 按日切本该到期，但屏障处不跑
        out = notdnd_web._chron_advance(session, 3000)
        assert out["stopped_at"] == 1500 and out["world_minute"] == 1500, out
        saved = _neura_block_of(session)
        near = saved["npcs"]["npc-near"]
        far_npc = saved["npcs"]["npc-far"]
        assert near["last_tick_minute"] == 1500, near     # 近场照常
        assert far_npc["last_tick_minute"] == 0, far_npc  # 远场不动（2.4）
        # 远场没交出任何提案（无 npc 动作事件、journal 无 npc-far 决策）
        decisions = [e.get("npc_id") for e in saved.get("journal", [])
                     if isinstance(e, dict)]
        assert "npc-far" not in decisions, saved["journal"]

        # 已到过的屏障不再拦：非屏障推进里远场照常到期（日切差 ≥ 1）
        out2 = notdnd_web._chron_advance(session, 3000)
        assert out2["stopped_at"] is None and out2["world_minute"] == 3000, out2
        saved2 = _neura_block_of(session)
        assert saved2["npcs"]["npc-far"]["last_tick_minute"] == 3000, saved2
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_concurrent_advance_no_duplicate_events():
    """N7 验收 5：两个线程同时 `_chron_advance` → 不出现重复 neura 事件
    （单局锁串行化；`base_seq` 冲突被状态层幂等正确归类，不重复副作用）。"""
    sid = "neura-n7-concurrent"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "neura-n7-seed-0004")

        outs = []
        errors = []

        def run():
            try:
                outs.append(notdnd_web._chron_advance(session, 60))
            except Exception as exc:      # noqa: BLE001 — 收集后统一断言
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors, errors
        assert len(outs) == 2 and all(o["status"] == "ok" for o in outs), outs
        assert all(o["world_minute"] == 60 for o in outs), outs

        events = _neura_all_events(session)
        action_events = [actor for t, actor in events
                         if t == notdnd_web.ACTION_EVENT_TYPE]
        assert action_events.count("npc-guard") == 1, events  # 不重复提交
        assert [t for t, _a in events].count("neura.tick") == 1, events
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_restart_replay_identical():
    """N7 验收 6（§10.1 的 2.2）：重启后 `advance_to` 到同一分钟 →
    `neura` 块与事件序列全等（存档 → 重新载入 → 再推进不新增任何事件）。"""
    sid = "neura-n7-restart"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "neura-n7-seed-0005")

        out = notdnd_web._chron_advance(session, 10)
        assert out["world_minute"] == 10, out
        session.save()
        before_block = _neura_block_of(session)
        before_events = [
            e for e in (session._handle or session._open_handle()).events_since(0)]

        notdnd_web._sessions.pop(sid, None)     # 「重启」：丢缓存走真实读盘
        revived = notdnd_web.Session.load(sid)
        out2 = notdnd_web._chron_advance(revived, 10)
        assert out2["status"] == "ok" and out2["world_minute"] == 10, out2
        assert out2["events"] == [], out2       # 世界时间没动：无新事件

        handle = revived._handle or revived._open_handle()
        after_events = handle.events_since(0)
        assert len(after_events) == len(before_events), "重启不得新增事件"
        assert [e["event_id"] for e in after_events] == \
            [e["event_id"] for e in before_events], "事件序列必须全等"
        assert _neura_block_of(revived) == before_block, "neura 块必须全等"
        assert revived.log == session.log, "neura_skipped 不得混入日志"
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_empty_block_writes_nothing():
    """§9.7：`blocks.neura` 缺失（老档）→ 视为空块，首次 tick 不写任何东西；
    无 AI 配置 = 正常路径（验收：不因候选缺失报错、不阻塞时间推进）。"""
    sid = "neura-n7-empty"
    _seed_action_session(notdnd_web.SAVE_DIR, sid)
    session = notdnd_web.Session.load(sid)
    try:
        before_events = _neura_all_events(session)
        out = notdnd_web._chron_advance(session, 30)
        assert out["status"] == "ok" and out["world_minute"] == 30, out
        added = [t for t, _a in _neura_all_events(session)][len(before_events):]
        assert added == ["clock.advance"], added   # 只有时钟推进，没有 neura 事件
        handle = session._handle or session._open_handle()
        assert (handle.view().get("blocks") or {}).get("neura") is None, \
            "空块首次 tick 不得创建 neura 块"
        assert all(entry["kind"] != "neura_skipped" for entry in session.log)
    finally:
        notdnd_web._sessions.pop(sid, None)


# --------------------------------------------------------------------------
# guide.npc_at 严格双写（D4，NEURA-DESIGN §12.1 / §4.4，Issue #372）
#
# P6 落地补全：neura 权威移动 → `guide.npc_at` 随**同一条** `neura.tick`
# 提交落盘（changes 双块，A0 不单开事件）；无移动 / 位置未变 / 移动提交
# 失败不产生 guide 块变更（防噪音、N5 裁定 13 延伸）；重放后
# `blocks.guide.npc_at` 与 live 相等。`prism_guide.py` / `neura.py` 零改动。
# --------------------------------------------------------------------------


def _neura_tick_events(session) -> list:
    """本局全部 `neura.tick` 事件（按 seq 升序）。"""
    handle = session._handle or session._open_handle()
    return [e for e in handle.events_since(0)
            if e["type"] == notdnd_web.NEURA_TICK_EVENT_TYPE]


def check_neura_npc_at_dual_write_same_event():
    """D4 验收 1：NPC 移动提交后，**同一条** `neura.tick` 事件的 changes
    同时含 neura 与 guide 块变更，且 `guide.npc_at[npc_id] == 新 place_id`
    （live 与落盘一致——§12.1：不得只写一侧）。"""
    sid = "neura-d4-dual"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "neura-d4-seed-0001")

        out = notdnd_web._chron_advance(session, 10)
        assert out["status"] == "ok" and out["world_minute"] == 10, out

        tick_events = _neura_tick_events(session)
        assert len(tick_events) == 1, tick_events
        blocks_changed = [c["block"] for c in tick_events[0]["changes"]]
        assert blocks_changed == ["neura", "guide"], blocks_changed
        guide_change = tick_events[0]["changes"][1]
        assert guide_change["op"] == "replace_block", guide_change
        assert guide_change["after"]["npc_at"]["npc-guard"] == "gate", \
            guide_change["after"]
        # live 侧同提交更新（不得只写落盘一侧）
        assert session.guide["npc_at"]["npc-guard"] == "gate", session.guide
        # 状态层（checkpoint 前的块视图）也一致
        handle = session._handle or session._open_handle()
        saved_guide = (handle.view().get("blocks") or {}).get("guide") or {}
        assert saved_guide.get("npc_at") == {"npc-guard": "gate"}, saved_guide
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_npc_at_no_move_no_guide_change():
    """D4 验收 2：无移动 / 位置未变的 tick → changes 不含 guide 块（防噪音）。"""
    sid = "neura-d4-noise"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        # 已在岗（place == slot_place）：日程义务不触发移动，无移动提交
        block["npcs"]["npc-stay"] = _neura_mover_npc(
            npc_id="npc-stay", place="gate", slot_place="gate")
        _neura_seed_block(session, block, "neura-d4-seed-0002")

        out = notdnd_web._chron_advance(session, 10)
        assert out["status"] == "ok" and out["world_minute"] == 10, out
        tick_events = _neura_tick_events(session)
        assert len(tick_events) == 1, tick_events
        blocks_changed = [c["block"] for c in tick_events[0]["changes"]]
        assert blocks_changed == ["neura"], blocks_changed
        assert session.guide.get("npc_at") == {}, session.guide
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_npc_at_idempotent_recommit():
    """D4 验收 3：幂等——同一分钟再推进 → 事件数不变、npc_at 不重复变。"""
    sid = "neura-d4-idem"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "neura-d4-seed-0003")

        out = notdnd_web._chron_advance(session, 10)
        assert out["status"] == "ok", out
        handle = session._handle or session._open_handle()
        before_events = handle.events_since(0)
        at_before = dict(session.guide["npc_at"])

        out2 = notdnd_web._chron_advance(session, 10)
        assert out2["status"] == "ok" and out2["world_minute"] == 10, out2
        assert out2["events"] == [], out2
        after_events = handle.events_since(0)
        assert len(after_events) == len(before_events), "不得新增事件"
        assert [e["event_id"] for e in after_events] == \
            [e["event_id"] for e in before_events]
        assert session.guide["npc_at"] == at_before, session.guide
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_npc_at_move_failed_not_written():
    """D4 验收 5：移动提交没成功（这里是 `unsupported`：行动表被清空，
    与 `conflict` / `rejected` 走同一「非 committed 不写」分支）→
    `npc_at` 不变，tick 事件 changes 不含 guide 块。"""
    sid = "neura-d4-fail"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "neura-d4-seed-0004")

        # 行动表清空 → `move` 找不到条目 → `_neura_submit` 前置守卫拒绝
        session.rules["scene"]["actions"] = []

        out = notdnd_web._chron_advance(session, 10)
        assert out["status"] == "ok" and out["world_minute"] == 10, out
        tick_events = _neura_tick_events(session)
        assert len(tick_events) == 1, tick_events
        blocks_changed = [c["block"] for c in tick_events[0]["changes"]]
        assert blocks_changed == ["neura"], blocks_changed
        # npc_at 不写（移动提交没成功；neura 块 write_back 的前移照旧——
        # N7 现状，neura.py 一行不动，下个 tick 收敛时再双写）
        assert session.guide.get("npc_at") == {}, session.guide
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_npc_at_replay_matches_live():
    """D4 验收 4（§10.1 出口 2.1/2.2 的自然扩展）：重启后 `replay` 重建的
    `blocks.guide.npc_at` 必须与 live 相等。"""
    sid = "neura-d4-replay"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "neura-d4-seed-0005")

        out = notdnd_web._chron_advance(session, 10)
        assert out["status"] == "ok" and out["world_minute"] == 10, out
        assert session.guide["npc_at"] == {"npc-guard": "gate"}, session.guide
        session.save()

        # 「重启」：丢缓存走真实读盘，live 从检查点还原
        notdnd_web._sessions.pop(sid, None)
        revived = notdnd_web.Session.load(sid)
        assert revived.guide["npc_at"] == {"npc-guard": "gate"}, revived.guide
        # 纯事件重放：guide 块随事件落盘后必须与 live 相等
        handle = revived._handle or revived._open_handle()
        replayed = handle.replay()
        replay_guide = replayed["blocks"].get("guide") or {}
        assert replay_guide.get("npc_at") == revived.guide["npc_at"], \
            (replay_guide, revived.guide)
    finally:
        notdnd_web._sessions.pop(sid, None)


# --------------------------------------------------------------------------
# 只读观察面与 100 游戏日长跑（N8，NEURA-DESIGN §9.4 / §10.3 / §11 N8）
#
# `GET /api/neura/view?npc_id=…`：layer / needs / plan / 最近 5 条 journal，
# 不含自由文本、不含 secret.proposition；无权看 → 404 forbidden 不泄露存在性。
# payload 构造直调 `_neura_view_payload`（与路由同一入口）；HTTP 壳（会话
# 定位 / 成员鉴权链）用 `_LocalServer` 本进程服务覆盖。
# --------------------------------------------------------------------------

_NEURA_JOURNAL_VIEW_KEYS = {"decision_id", "npc_id", "at_minute", "layer",
                            "chosen", "switch_reason", "cost", "ai_used",
                            "hard"}


def _neura_leaky_npc(npc_id="npc-guard") -> dict:
    """带「不该泄露的内容」的近场 NPC：knowledge 命题 / 秘密 / memory 摘要。"""
    npc = _neura_mover_npc(npc_id)
    npc["knowledge"] = [{"claim_id": "cl-x", "cls": "secret",
                         "proposition": "机密命题XYZZY", "status": "active",
                         "confidence": 100, "learned_minute": 0,
                         "source_kind": "self"}]
    npc["secrets"] = [{"secret_id": "sec-1", "claim_id": "cl-x",
                       "holders": [npc_id], "status": "held"}]
    npc["memory"] = [{"memory_id": "mem-1", "minute": 0, "kind": "correction",
                      "summary": "私域记忆摘要QWERTY", "participants": [npc_id],
                      "salience": 1}]
    return npc


def check_neura_view_payload_gm_and_no_leak():
    """N8：单人档（GM 视角）返回四件套；journal / plan 白名单投影；
    knowledge 命题 / secrets / memory 一字不漏；不存在的 npc_id → 404
    forbidden（与「无权看」同响应，不泄露存在性）。"""
    sid = "neura-n8-view-gm"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_leaky_npc()
        _neura_seed_block(session, block, "neura-n8-seed-0001")
        notdnd_web._chron_advance(session, 10)   # 生成真 journal / plan 输入

        code, payload = notdnd_web._neura_view_payload(session, None,
                                                       "npc-guard")
        assert code == 200, (code, payload)
        assert payload["status"] == "ok" and payload["npc_id"] == "npc-guard"
        assert payload["layer"] == "near", payload               # §7.1 现算
        needs = payload["needs"]
        assert set(needs) == set(neura.NEED_KEYS), needs
        assert all(isinstance(v, int) for v in needs.values()), needs
        assert isinstance(payload["journal"], list) and payload["journal"]
        for entry in payload["journal"]:
            assert set(entry) <= _NEURA_JOURNAL_VIEW_KEYS, entry
            assert set(entry["hard"]) == {"ok", "kept", "rejected"}, entry
            assert all(isinstance(code_s, str) and " " not in code_s
                       for code_s in entry["hard"]["rejected"]), entry
        # 不泄露：自由文本（命题 / 摘要）与 secrets 键一个都不在响应里
        raw = json.dumps(payload, ensure_ascii=False)
        assert "XYZZY" not in raw and "QWERTY" not in raw, raw
        assert "proposition" not in raw and "summary" not in raw, raw
        assert "memory" not in payload and "knowledge" not in payload \
            and "secrets" not in payload, sorted(payload)

        # plan 投影：只有 `_write_back` 的六个固定键（有则投影、无则缺省）
        assert set(payload.get("plan") or {}) <= set(
            notdnd_web._NEURA_PLAN_KEYS), payload.get("plan")

        # 不存在 / 缺 npc_id / 块缺失 → 同一 404 forbidden
        assert notdnd_web._neura_view_payload(session, None, "npc-ghost") == \
            (404, {"status": "forbidden"})
        assert notdnd_web._neura_view_payload(session, None, "") == \
            (404, {"status": "forbidden"})
        empty_sid = sid + "-empty"
        _seed_action_session(notdnd_web.SAVE_DIR, empty_sid)
        empty_session = notdnd_web.Session.load(empty_sid)
        try:
            assert notdnd_web._neura_view_payload(
                empty_session, None, "npc-guard") == \
                (404, {"status": "forbidden"})
        finally:
            notdnd_web._sessions.pop(empty_sid, None)
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_view_payload_room_member_visibility():
    """N8 鉴权裁定：host / player 只看 `layer == "near"`；observer 一律 404；
    远场 NPC 对房间成员回**同一个** 404 forbidden（与不存在同响应）。"""
    sid = "neura-n8-view-room"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-near"] = _neura_mover_npc(npc_id="npc-near")
        far = neura.empty_npc("npc-far", "sc-1")
        far["importance"] = 0.9                       # P5：≥ 0.7 → important_far
        far["location"]["place_id"] = "temple"
        block["npcs"]["npc-far"] = far
        _neura_seed_block(session, block, "neura-n8-seed-0002")

        host = {"member_id": "m-1", "role": "host", "character_id": "pc-host"}
        player = {"member_id": "m-2", "role": "player",
                  "character_id": "pc-b"}
        observer = {"member_id": "m-3", "role": "observer", "character_id": ""}

        # host / player：近场可见
        for member in (host, player):
            code, payload = notdnd_web._neura_view_payload(
                session, member, "npc-near")
            assert code == 200 and payload["layer"] == "near", (member, code)
        # 远场：host / player 也是 404 forbidden（与不存在同响应）
        for member in (host, player):
            assert notdnd_web._neura_view_payload(session, member, "npc-far") \
                == (404, {"status": "forbidden"})
        # observer：近场也 404（观察面是玩家 / GM 视角数据）
        assert notdnd_web._neura_view_payload(session, observer, "npc-near") \
            == (404, {"status": "forbidden"})
        assert notdnd_web._neura_view_payload(session, observer, "npc-far") \
            == (404, {"status": "forbidden"})
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_view_route_http():
    """N8 HTTP 壳：X-Session 直达（单人语义）；房间块有成员记录的会话必须
    过成员鉴权链（缺令牌 / 令牌错 401）；无 X-Session → 400。"""
    sid = "neura-n8-route"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    notdnd_web._sessions[sid] = session   # HTTP 线程 get_session 命中同一实例
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "neura-n8-seed-0003")

        with _LocalServer() as server:
            status, body = server.get(
                "/api/neura/view?npc_id=npc-guard", sid=sid)
            assert status == 200, (status, body)
            assert body["status"] == "ok" and body["npc_id"] == "npc-guard"
            assert body["layer"] == "near" and body["journal"] == [], body

            # 未知 npc / 缺 npc_id → 404 forbidden（HTTP 壳同口径）
            status, body = server.get(
                "/api/neura/view?npc_id=npc-ghost", sid=sid)
            assert status == 404 and body == {"status": "forbidden"}, \
                (status, body)
            status, body = server.get("/api/neura/view", sid=sid)
            assert status == 404 and body == {"status": "forbidden"}, \
                (status, body)

            # 无会话 → 400（_sess 既有语义）
            status, body = server.get("/api/neura/view?npc_id=npc-guard")
            assert status == 400, (status, body)

            # 房间会话（有成员记录）：无成员头 → 401；令牌错 → 401
            rid = "room-n8-view"
            status, created = _post(server, "/api/room/create",
                                    {"room_id": rid})
            assert status == 200, (status, created)
            status, body = server.get("/api/neura/view?npc_id=npc-guard",
                                      sid=rid)
            assert status == 401, (status, body)
            status, body = server.get(
                "/api/neura/view?npc_id=npc-guard", sid=rid,
                headers={"X-Room": rid, "X-Member": created["member_id"],
                         "Authorization": "Bearer wrong-token"})
            assert status == 401, (status, body)

            # observer 成员（夹具直造）：鉴权过了也不给观察面 → 404 forbidden
            room_session = notdnd_web._sessions[rid]
            obs_token = "n8-observer-token"
            obs_member = notdnd_web._empty_member("m-obs")
            obs_member.update({"token_hash": notdnd_web._hash_token(obs_token),
                               "display_name": "观", "role": "observer",
                               "state": "active", "created_at": time.time(),
                               "last_seen_at": time.time()})
            room_session.room["members"]["m-obs"] = obs_member
            status, body = server.get(
                "/api/neura/view?npc_id=npc-guard", sid=rid,
                headers={"X-Room": rid, "X-Member": "m-obs",
                         "Authorization": "Bearer %s" % obs_token})
            assert status == 404 and body == {"status": "forbidden"}, \
                (status, body)
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_neura_soak_100_days():
    """N8 长跑（确定性、无 AI、固定种子）：100 游戏日 × 20 NPC 纯规则推进 →
    事件数 ≤ 20×100×2；`blocks.neura` 序列化 < 1 MiB；`replay` 相等；
    `blocks.guide.npc_at` 与 live 相等（D4 双写新口径）；耗时 < 30 秒。

    推进粒度取 **5 游戏日一步**（20 次 advance = 20 次 tick）：本机逐日
    100 次 `_chron_advance` ≈ 107s，大头是**每次推进**的既有恢复成本
    （`chron.restore` / 状态层视图深拷贝，禁改路径），不是 neura 模拟成本
    ——5 日步长下同跨度 20 次 tick 纯规则 ≈ 7s，远场 / 背景的日差判定
    （≥ 1 / ≥ 3，N5 裁定 1）照常触发，长跑断言全部成立（PR 登记口径）。
    """
    sid = "neura-n8-soak"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["campaign_seed"] = 20261018            # 固定种子：确定性
        for i in range(10):                          # 近场：market → gate 日程
            block["npcs"]["npc-near-%02d" % i] = _neura_mover_npc(
                npc_id="npc-near-%02d" % i)
        for i in range(10):                          # 远场：temple 常驻在岗
            far = neura.empty_npc("npc-far-%02d" % i, "sc-1")
            far["importance"] = 0.9
            far["location"]["place_id"] = "temple"
            far["schedule"] = [{"slot_id": "s1", "place_id": "temple",
                                "from_tod_minute": 0, "to_tod_minute": 1440}]
            block["npcs"]["npc-far-%02d" % i] = far
        _neura_seed_block(session, block, "neura-n8-soak-seed-0001")

        started = time.time()
        for step in range(1, 21):                    # 100 游戏日：5 日一步
            out = notdnd_web._chron_advance(session, step * 5 * 1440)
            assert out["status"] == "ok", (step, out)
            assert out["stopped_at"] is None, (step, out)
        elapsed = time.time() - started

        handle = session._handle or session._open_handle()
        events = handle.events_since(0)
        assert len(events) <= 20 * 100 * 2, len(events)
        saved = _neura_block_of(session)
        assert saved["npcs"], saved
        serialized = json.dumps(saved, ensure_ascii=False).encode("utf-8")
        assert len(serialized) < 1024 * 1024, len(serialized)

        # 重放相等（§10.1 出口 2.1）+ D4 双写新口径：guide.npc_at 重放 == live
        replayed = handle.replay()
        assert replayed["blocks"].get("neura") == saved, "重放 neura 块必须相等"
        live_at = session.guide.get("npc_at") \
            if isinstance(session.guide, dict) else {}
        assert len(live_at) == 10, live_at           # 10 个近场 NPC 各移动过一次
        replay_guide = replayed["blocks"].get("guide") or {}
        assert replay_guide.get("npc_at") == live_at, \
            (replay_guide.get("npc_at"), live_at)
        saved_guide = (handle.view().get("blocks") or {}).get("guide") or {}
        assert saved_guide.get("npc_at") == live_at, saved_guide

        assert elapsed < 30.0, elapsed
    finally:
        notdnd_web._sessions.pop(sid, None)



# --------------------------------------------------------------------------
# 状态层只读接口（M7-4，STATE-DESIGN §16，Issue #312）
# --------------------------------------------------------------------------


def _files_fingerprint(root: str) -> dict:
    """目录树只读指纹：相对路径 → (size, mtime_ns, sha256)。

    「只读不改盘」的判据——size + mtime + 内容摘要三者一起比对，任何
    检查点 / 归档 / 截除都会在这里现形。
    """
    prints = {}
    for base, _dirs, files in os.walk(root):
        for name in files:
            path = os.path.join(base, name)
            with open(path, "rb") as handle:
                raw = handle.read()
            prints[os.path.relpath(path, root)] = (
                len(raw), os.stat(path).st_mtime_ns,
                hashlib.sha256(raw).hexdigest())
    return prints


@contextlib.contextmanager
def _state_thresholds(**overrides):
    """临时改写 `state` 的常量（归档 / 检查点阈值），退出时逐项还原。"""
    saved = {name: getattr(state, name) for name in overrides}
    for name, value in overrides.items():
        setattr(state, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(state, name, value)


def _campaign_dir(sid: str) -> str:
    """新档目录（`campaigns/<sid>/`）——测试进程里 `SAVE_DIR` 就是临时目录。"""
    return os.path.dirname(_campaign_file(sid, state.SNAPSHOT_NAME))


def _commit_state_event(handle, index: int) -> None:
    """往状态层句柄提交一条真事件（供增量接口断言；`n` 即 seq）。"""
    result = handle.commit(
        base_seq=handle.view()["seq"], idempotency_key="m74-k-%d" % index,
        type="core.commit", actor="pc-1",
        changes=[{"block": "rules", "path": "/n", "op": "set",
                  "before": None, "after": index}],
        rules_version="prism@data-79", source="player")
    assert result["status"] == "committed", result


# ── 世界经济日切接线与玩家经济动作（M12-W5，WORLD-DESIGN §6.3 / §6.4）──────
#
# `_world_tick` 接线照 N7 模式断言（锁内时序 / 失败隔离 / 屏障 / 并发 /
# 幂等）；`econ.trade` 是 M8 内部分支 + 两本账双记（§6.6：同提交各记各的，
# 无交叉写）。`world.py` 只调用零改动——夹具直接用 `world.new_block` 造块。


def _world_seed_block(session, block: dict, key: str) -> None:
    """把手工 world 块提交进状态层（测试夹具；`replace_block` 建键）。"""
    handle = session._handle or session._open_handle()
    out = handle.commit(
        base_seq=handle.view()["seq"], idempotency_key=key,
        type="core.commit", actor="pc-1",
        changes=[{"block": "world", "op": "replace_block", "path": "",
                  "before": None, "after": block}],
        rules_version=notdnd_web.ACTION_RULES_VERSION, source="player")
    assert out["status"] == "committed", out


def _world_block_of(session) -> dict:
    """本局状态层里的 world 块（没有 → 空 dict）。"""
    handle = session._handle or session._open_handle()
    return (handle.view().get("blocks") or {}).get("world") or {}


def _world_goods_block(*, stock: int = 10, coin: int = 10000) -> dict:
    """带一种 food good 的 world 块（贸易 / 日切测试夹具）。"""
    return world.new_block(
        goods=[{"good_id": "grain", "name": "黑麦", "category": "food",
                "tier": 1, "base_price": 2, "stock": stock,
                "produced_by": ["p1"]}],
        day=0, balances={"coin": coin})


def _event_type_counts(session) -> dict:
    """状态层全部事件按 type 计数（按 seq 升序）。"""
    handle = session._handle or session._open_handle()
    counts: dict = {}
    for event in handle.events_since(0):
        counts[event["type"]] = counts.get(event["type"], 0) + 1
    return counts


def check_world_wired_import_and_tick_updates_block():
    """W5 验收：接线证明（`notdnd_web` 顶层 import world）+ 日切接线：
    `advance_to` 返回后 `blocks.world` 已日切更新；同日重放不重复（幂等）；
    A0：一天至多一条 `econ.day` 事件。"""
    assert notdnd_web.world is world, "notdnd_web 必须在顶层 import world"

    sid = "world-w5-tick"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        block = _world_goods_block()
        block["settlements"]["s1"] = {"place_id": "p1", "population": 10}
        _world_seed_block(session, block, "world-w5-seed-0001")

        out = notdnd_web._chron_advance(session, 1500)   # day 1（1440 起）
        assert out["status"] == "ok" and out["world_minute"] == 1500, out
        saved = _world_block_of(session)
        assert saved["economy"]["day"] == 1, saved["economy"]
        assert "wtick_0001" in saved["economy"]["wticks"], saved["economy"]
        kinds = [entry["kind"] for entry in saved["economy"]["ledger"]]
        assert "produce" in kinds and "consume" in kinds, kinds
        # 三等式①在日切后仍成立（§6.2；world.py 纯函数，编排层零改动调用）
        assert world.verify_ledger(saved) == [], world.verify_ledger(saved)

        # 幂等：同日再推进（wtick 命中）→ 不落第二条 econ.day、day 不动
        out2 = notdnd_web._chron_advance(session, 2000)
        assert out2["status"] == "ok", out2
        counts = _event_type_counts(session)
        assert counts.get("econ.day") == 1, counts
        assert _world_block_of(session)["economy"]["day"] == 1, \
            _world_block_of(session)["economy"]
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_tick_failure_isolated():
    """W5 验收：`world_tick` 抛异常（注入）→ `_chron_advance` 正常返回 +
    `world_skipped` 日志 + 玩家到期卡照常结算（照 `neura_skipped` 先例）。"""
    sid = "world-w5-fail"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry())
    session = notdnd_web.Session.load(sid)
    try:
        proposal = notdnd_web._action_proposal_from_id(session, "a-long", {})
        scheduled = notdnd_web._chron_schedule(session, proposal, base_seq=0)
        task_id = scheduled["task_id"]
        _world_seed_block(session, _world_goods_block(), "world-w5-seed-0002")

        real_tick = world.world_tick

        def boom(*args, **kwargs):
            raise RuntimeError("world boom")

        world.world_tick = boom
        try:
            # 第一段推进停在 B8 屏障（30）：到期卡先结算，日切还没到（day 0）
            out = notdnd_web._chron_advance(session, 2000)
            assert out["stopped_at"] == 30 and out["world_minute"] == 30, out
            assert out["settled"] \
                and out["settled"][0]["action_id"] == task_id, out
            # 第二段推进（非屏障，跨入 day 1）：注入的日切失败被隔离
            out2 = notdnd_web._chron_advance(session, 2000)
        finally:
            world.world_tick = real_tick
        # 推进照常返回、世界时间照走
        assert out2["status"] == "ok" and out2["world_minute"] == 2000, out2
        # 失败隔离留痕；世界块未被改写（day 仍 0）
        kinds = [entry["kind"] for entry in session.log]
        assert "world_skipped" in kinds, session.log
        assert _world_block_of(session)["economy"]["day"] == 0, \
            _world_block_of(session)["economy"]
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_barrier_skips_day_cut():
    """W5 验收：屏障处（`stopped_at != None`）不跑日切（W4 参数透传断言）；
    跨屏障漏掉的日切在下一个非屏障停点**逐日补结算**（每天一条独立幂等键、
    一条独立事件——不是把多天合成一笔）。"""
    sid = "world-w5-barrier"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="a-long",
                         entry=_long_entry(
                             time_cost={"value": 1500, "unit": "minute"}))
    session = notdnd_web.Session.load(sid)
    try:
        proposal = notdnd_web._action_proposal_from_id(session, "a-long", {})
        notdnd_web._chron_schedule(session, proposal, base_seq=0)
        _world_seed_block(session, _world_goods_block(), "world-w5-seed-0003")

        # 屏障（B8 到期）在 1500：停在屏障，日切被跳过（stopped_at 透传）
        out = notdnd_web._chron_advance(session, 3000)
        assert out["stopped_at"] == 1500 and out["world_minute"] == 1500, out
        assert _world_block_of(session)["economy"]["day"] == 0, \
            _world_block_of(session)["economy"]
        assert _event_type_counts(session).get("econ.day") is None, \
            _event_type_counts(session)

        # 非屏障推进：欠的日切逐日补齐（1500 → 3000 跨 day 1 与 day 2）
        out2 = notdnd_web._chron_advance(session, 3000)
        assert out2["stopped_at"] is None and out2["world_minute"] == 3000, out2
        saved = _world_block_of(session)
        assert saved["economy"]["day"] == 2, saved["economy"]
        assert {"wtick_0001", "wtick_0002"} <= set(saved["economy"]["wticks"]), \
            saved["economy"]["wticks"]
        counts = _event_type_counts(session)
        assert counts.get("econ.day") == 2, counts
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_concurrent_advance_no_duplicate_events():
    """W5 验收：两线程同时 `_chron_advance` → 不出现重复 `world` 事件
    （单局锁串行化 + `wtick_<day>` 状态层幂等兜底）。"""
    sid = "world-w5-concurrent"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _world_seed_block(session, _world_goods_block(), "world-w5-seed-0004")

        outs: list = []
        errors: list = []

        def run():
            try:
                outs.append(notdnd_web._chron_advance(session, 2000))
            except Exception as exc:      # noqa: BLE001 — 收集后统一断言
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert not errors, errors
        assert len(outs) == 2 and all(o["status"] == "ok" for o in outs), outs
        counts = _event_type_counts(session)
        assert counts.get("econ.day") == 1, counts
        assert _world_block_of(session)["economy"]["day"] == 1, \
            _world_block_of(session)["economy"]
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_absent_block_writes_nothing():
    """W5 口径（§9.7 同款）：`world` 块缺失（老档 / 未接世界）→ 推进不写
    任何 world 事件、不落 `world_skipped`。"""
    sid = "world-w5-absent"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        out = notdnd_web._chron_advance(session, 2000)
        assert out["status"] == "ok" and out["world_minute"] == 2000, out
        counts = _event_type_counts(session)
        assert counts.get("econ.day") is None, counts
        kinds = [entry["kind"] for entry in session.log]
        assert "world_skipped" not in kinds, session.log
        assert "world" not in ((session._handle or session._open_handle())
                               .view().get("blocks") or {}), "块缺失不得被创建"
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_econ_trade_commit_dual_ledger_http():
    """W5 验收：trade 提案全链路（HTTP 面）：玩家提案 → M8 结算（第 3–5 段）
    → 事件 `econ.trade` → 同提交 `world` 块 Entry 双记（`changes` 双块）；
    两本账不互改（§6.6）；同键重放不重复结算（§7.7）。"""
    sid = "econ-w5-trade"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="buy-grain",
                         label="买粮",
                         entry={"econ": {"kind": "trade", "good_id": "grain",
                                         "quantity": 10, "unit_price": 2}})
    try:
        with _LocalServer() as server:
            # HTTP 用例必须拿**服务端缓存里的同一份**会话（get_session），
            # 否则读的是互不知情的副本（Session.load 不进缓存）。
            session = notdnd_web.get_session(sid)
            _world_seed_block(session, _world_goods_block(stock=10, coin=100),
                              "econ-w5-seed-0001")
            status, prop = _propose_action(server, sid, action_id="buy-grain")
            assert status == 200, (status, prop)
            rules_before = copy.deepcopy(session.rules)
            base_seq = prop["base_seq"]

            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], base_seq), sid=sid,
                headers={"Idempotency-Key": "econ-w5-trade-0001"})
            assert status == 200, (status, body)
            frames = _parse_sse(raw)
            result = frames[0][1]
            assert result["committed"] is True, result
            # 经济部分进了结果契约（§6.4：既有 `effects` 字段装得下）
            pools = sorted((e["pool"], e["value"])
                           for e in result["effects"]
                           if e["kind"] == "resource")
            assert ("coin", -20) in pools and ("grain", -10) in pools, \
                result["effects"]

            # 事件封套：type econ.trade，changes 双块（规则块 + world 块）
            handle = session._handle or session._open_handle()
            events = handle.events_since(0)
            econ = [e for e in events if e["type"] == "econ.trade"]
            assert len(econ) == 1, [e["type"] for e in events]
            event = econ[0]
            assert [c["block"] for c in event["changes"]] == \
                ["rules", "world"], event["changes"]

            # world 块（宏观账）：两条 trade Entry、库存 / 金库已扣
            saved = _world_block_of(session)
            ledger = saved["economy"]["ledger"]
            assert [row["kind"] for row in ledger] == ["trade", "trade"], ledger
            assert (ledger[0]["account"], ledger[0]["delta"]) == \
                ("coin", -20), ledger
            assert (ledger[1]["account"], ledger[1]["delta"]) == \
                ("grain", -10), ledger
            assert all(row["refs"]["event_seq"] == event["seq"]
                       for row in ledger), "Entry 必须指向携带它的事件"
            assert saved["economy"]["balances"]["coin"] == 80, saved["economy"]
            assert saved["economy"]["goods"]["grain"]["stock"] == 0, \
                saved["economy"]["goods"]
            assert world.verify_ledger(saved) == [], world.verify_ledger(saved)

            # 两本账不互改（§6.6 铁律）：规则包侧只发生 M8 结算的既有事实
            # ——after 与**纯 PRISM 结算**（不含经济分支）的快照逐字节一致，
            # 经济分支没有碰它；world 块侧各记各的（无交叉写）。
            live = prism_core.RuleSession.from_snapshot(rules_before)
            prism_core.perform_action(live, "buy-grain", unit_id="u-1")
            rules_change = [c for c in event["changes"]
                            if c["block"] == "rules"][0]
            assert rules_change["before"] == rules_before, "结算基线被动过"
            assert rules_change["after"] == live.snapshot(), "规则包库存被交叉改写"

            # 同键重放：回首次结果，不新增事件（§7.7）
            status2, _body2, raw2, _h2 = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], base_seq), sid=sid,
                headers={"Idempotency-Key": "econ-w5-trade-0001"})
            assert status2 == 200, status2
            assert _parse_sse(raw2)[-1][1].get("replayed") is True, raw2[:300]
            counts = _event_type_counts(session)
            assert counts.get("econ.trade") == 1, counts
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_econ_trade_insufficient_rejected_http():
    """W5 验收：库存不足拒单（出口 2.2）：买 10 只有 3 只 → 409 拒、无账、
    无 changes（事件一个都不落），两本账都原样。"""
    sid = "econ-w5-short"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="buy-grain",
                         label="买粮",
                         entry={"econ": {"kind": "trade", "good_id": "grain",
                                         "quantity": 10, "unit_price": 2}})
    try:
        with _LocalServer() as server:
            session = notdnd_web.get_session(sid)
            seeded = _world_goods_block(stock=3, coin=100)
            _world_seed_block(session, seeded, "econ-w5-seed-0002")
            status, prop = _propose_action(server, sid, action_id="buy-grain")
            assert status == 200, (status, prop)
            rules_before = copy.deepcopy(session.rules)
            seq_before = (session._handle or session._open_handle()).view()["seq"]

            status, body, _raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "econ-w5-short-0001"})
            assert status == 409, (status, body)
            assert body.get("error") == notdnd_web.ECON_REJECTED, body

            # 无账、无 changes：一个事件都没落，两本账逐字节原样
            handle = session._handle or session._open_handle()
            assert handle.view()["seq"] == seq_before, "拒单不得落事件"
            assert _world_block_of(session) == seeded, "world 块被改写"
            assert session.rules == rules_before, "规则包被改写"
    finally:
        notdnd_web._sessions.pop(sid, None)


# ── 建造与委托式管理（M12-W6，WORLD-DESIGN §7 / §6.5）──────────────────────
#
# 两条通道（V7）：玩家 build 行动同步开工（econ.build 事件）或走 CHRON 卡
# 到期结算（状态变更随结算提交落块，A0 不单独开事件）；HTTP 管理面（P9：
# 创建/排队/开工/暂停/撤销 + 指令 + 可解释六项查询）。

# 会话默认世界（data/worlds/ 字典序第一份）的区域锚点——编译产物里世界帧
# 的地点 id 是确定性的（<world_key>/region-01）。
_PROJECT_PLACE = notdnd_web._default_world_key() + "/region-01"


def _world_project_block(*, stock=100, coin=1000, labor=10, status="queued",
                         project_id="p-wall", place=_PROJECT_PLACE):
    """带一个 queued 工程 + 铁/粮/金库/劳力池的 world 块（W6 夹具）。"""
    block = world.new_block(
        goods=[{"good_id": "g-iron", "name": "铁锭", "category": "military",
                "tier": 2, "base_price": 6, "stock": stock,
                "produced_by": []},
               {"good_id": "grain", "name": "黑麦", "category": "food",
                "tier": 1, "base_price": 2, "stock": 50,
                "produced_by": ["p1"]}],
        day=0, balances={"coin": coin, "labor": labor})
    project = world.new_project(
        project_id, name="修墙", place_id=place, blueprint={"kind": "wall"},
        requirements={"goods": {"g-iron": 20}, "coin": 100, "labor_days": 2},
        source="player", wage_per_day=5, created_minute=0)
    project["status"] = status
    block["projects"][project_id] = project
    block["settlements"]["s1"] = {"place_id": place, "population": 2}
    return block


def check_econ_build_start_commit_http():
    """W6 验收：build 提案同步开工全链路（HTTP 面）：五条件齐 → queued →
    running（三笔扣账 Entry），事件 `econ.build` + 同提交 `world` 块变更；
    结果契约 `effects` 装下经济部分（§6.4）。"""
    sid = "econ-w6-build"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="build-wall",
                         label="修墙",
                         entry={"econ": {"kind": "build",
                                         "project_id": "p-wall"}})
    try:
        with _LocalServer() as server:
            session = notdnd_web.get_session(sid)
            session.ensure_atlas()
            _world_seed_block(session, _world_project_block(),
                              "econ-w6-seed-0001")
            status, prop = _propose_action(server, sid,
                                           action_id="build-wall")
            assert status == 200, (status, prop)
            status, body, raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "econ-w6-build-0001"})
            assert status == 200, (status, body)
            result = _parse_sse(raw)[0][1]
            assert result["committed"] is True, result
            pools = {(e["pool"], e["value"]) for e in result["effects"]
                     if e["kind"] == "resource"}
            assert ("g-iron", -20) in pools and ("coin", -100) in pools \
                and ("labor", -2) in pools, result["effects"]

            # 事件封套：type econ.build，changes 双块；工程已 running
            handle = session._handle or session._open_handle()
            events = handle.events_since(0)
            econ = [e for e in events if e["type"] == "econ.build"]
            assert len(econ) == 1, [e["type"] for e in events]
            assert [c["block"] for c in econ[0]["changes"]] == \
                ["rules", "world"], econ[0]["changes"]
            saved = _world_block_of(session)
            project = saved["projects"]["p-wall"]
            assert project["status"] == "running", project
            assert project["progress"]["materials_in"] == {"g-iron": 20}
            assert project["progress"]["spend_coin"] == 100
            assert saved["economy"]["balances"]["coin"] == 900
            assert saved["economy"]["balances"]["labor"] == 8
            assert saved["economy"]["goods"]["g-iron"]["stock"] == 80
            assert world.verify_ledger(saved) == [], world.verify_ledger(saved)
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_econ_build_rejected_no_conditions_http():
    """W6 验收：开工五条件缺一 → 409 拒（拒单无账）：无事件、无 Entry、
    world 块逐字节原样。"""
    sid = "econ-w6-blocked"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="build-wall",
                         label="修墙",
                         entry={"econ": {"kind": "build",
                                         "project_id": "p-wall"}})
    try:
        with _LocalServer() as server:
            session = notdnd_web.get_session(sid)
            session.ensure_atlas()
            seeded = _world_project_block(stock=10)   # 材料不足（要 20）
            _world_seed_block(session, seeded, "econ-w6-seed-0002")
            status, prop = _propose_action(server, sid,
                                           action_id="build-wall")
            assert status == 200, (status, prop)
            seq_before = (session._handle or session._open_handle()) \
                .view()["seq"]
            status, body, _raw, _heads = server.post(
                "/api/action/commit",
                _commit_body(prop["proposal"], prop["base_seq"]), sid=sid,
                headers={"Idempotency-Key": "econ-w6-blocked-0001"})
            assert status == 409, (status, body)
            assert body.get("error") == notdnd_web.ECON_REJECTED, body
            handle = session._handle or session._open_handle()
            assert handle.view()["seq"] == seq_before, "拒开工不得落事件"
            assert _world_block_of(session) == seeded, "world 块被改写"
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_econ_build_chron_card_completes_project():
    """W6 验收（玩家工程 CHRON 卡通道，V7）：build 长行动 → 排程回执；
    卡到期结算 = 开工 + 里程碑完工（A0：状态变更随结算提交落块，不单独开
    econ.build 事件）；完工设施进据点。"""
    sid = "econ-w6-card"
    _seed_action_session(
        notdnd_web.SAVE_DIR, sid, action_id="build-wall", label="修墙",
        entry={"econ": {"kind": "build", "project_id": "p-wall"},
               "time_cost": {"value": 30, "unit": "minute"},
               "schedule_hint": {"can_defer": True, "depends_on": [],
                                 "priority": 0}})
    session = notdnd_web.Session.load(sid)
    try:
        session.ensure_atlas()
        notdnd_web._sessions[sid] = session     # 放回缓存：_chron_schedule 用它
        proposal = notdnd_web._action_proposal_from_id(session, "build-wall",
                                                       {})
        scheduled = notdnd_web._chron_schedule(session, proposal, base_seq=0)
        task_id = scheduled["task_id"]
        assert scheduled["mode"] == "async", scheduled
        _world_seed_block(session, _world_project_block(),
                          "econ-w6-seed-0003")

        out = notdnd_web._chron_advance(session, 2000)
        assert out["status"] == "ok", out
        assert out["settled"] and out["settled"][0]["action_id"] == task_id, \
            out["settled"]
        assert out["settled"][0]["status"] in ("success", "partial"), \
            out["settled"]
        # 卡到期 = 开工结算（三笔扣账落账；里程碑按工期折算到期）
        saved = _world_block_of(session)
        project = saved["projects"]["p-wall"]
        assert project["status"] == "running", project
        assert project["progress"]["materials_in"] == {"g-iron": 20}
        assert project["progress"]["spend_coin"] == 100
        assert project["milestones"][0]["at_minute"] == 30 + 2 * 1440, project
        assert world.verify_ledger(saved) == [], world.verify_ledger(saved)
        # A0：没有单独的 econ.build 事件——world 变更骑在卡结算提交上
        counts = _event_type_counts(session)
        assert counts.get("econ.build") is None, counts
        handle = session._handle or session._open_handle()
        with_world = [e for e in handle.events_since(0)
                      if "world" in [c.get("block")
                                     for c in (e.get("changes") or [])]]
        assert with_world, "world 变更必须随某条提交落盘"
        # 里程碑到期（日切第 4 步，两条通道汇合）→ 完工 + 设施进据点
        out2 = notdnd_web._chron_advance(session, 5000)
        assert out2["status"] == "ok", out2
        saved = _world_block_of(session)
        assert saved["projects"]["p-wall"]["status"] == "completed", \
            saved["projects"]["p-wall"]
        facilities = saved["settlements"]["s1"].get("facilities") or []
        assert [f["facility_id"] for f in facilities] == ["fac-p-wall"], \
            saved["settlements"]
        assert world.verify_ledger(saved) == [], world.verify_ledger(saved)
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_project_api_http():
    """W6 验收（P9 API 面）：POST /api/world/project 创建/排队/开工/暂停/
    复工/撤销 + GET 查询（readiness 缺项可读）；事件类型 econ.build /
    world.manage（PR 登记）；A0：一次操作一条事件。"""
    sid = "world-w6-api"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    try:
        with _LocalServer() as server:
            session = notdnd_web.get_session(sid)
            session.ensure_atlas()
            _world_seed_block(session, _world_goods_block(),
                              "econ-w6-seed-0004")
            block = _world_project_block(status="draft", project_id="p-api")
            block["projects"].pop("p-api", None)     # 只留经济夹具，工程由 API 建
            _world_seed_block(session, block, "econ-w6-seed-0005")

            def post(op, **extra):
                payload = {"op": op, "project_id": "p-api"}
                payload.update(extra)
                return _post(server, "/api/world/project", payload, sid=sid,
                             headers={"Idempotency-Key":
                                      "world-w6-api-%s" % op})

            # create：draft（econ.build）
            status, out = post("create", project={
                "project_id": "p-api", "name": "修墙",
                "place_id": _PROJECT_PLACE, "blueprint": {"kind": "wall"},
                "requirements": {"goods": {"g-iron": 20}, "coin": 100,
                                 "labor_days": 2}, "source": "player"})
            assert status == 200 and out["project"]["status"] == "draft", out
            # queue → start（五条件齐，atlas 注入）
            status, out = post("queue")
            assert status == 200 and out["project"]["status"] == "queued", out
            status, out = post("start")
            assert status == 200 and out["project"]["status"] == "running", out
            # GET 单个：readiness 缺项为空
            status, view = server.get(
                "/api/world/project?project_id=p-api", sid=sid)
            assert status == 200 and view["readiness"] == [], view
            # pause / resume / revoke（world.manage）
            status, out = post("pause")
            assert status == 200 and out["project"]["status"] == "paused", out
            status, out = post("resume")
            assert status == 200 and out["project"]["status"] == "queued", out
            status, out = post("revoke")
            assert status == 200 and out["project"]["status"] == "revoked", out
            # 事件类型：create/queue/start 三条 econ.build + 三条 world.manage
            counts = _event_type_counts(session)
            assert counts.get("econ.build") == 3, counts
            assert counts.get("world.manage") == 3, counts
            # 撤销后再开工 → 409（终态不可开工）
            status, out = post("start")
            assert status == 409, (status, out)
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_directive_api_and_explain_http():
    """W6 验收（P9 / §6.5）：POST /api/world/directive 创建 + 日切自动采买
    （cap_total 生成时钳制）+ GET 可解释六项；paused / revoked 日切跳过。"""
    sid = "world-w6-directive"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    try:
        with _LocalServer() as server:
            session = notdnd_web.get_session(sid)
            block = _world_goods_block(stock=10, coin=1000)
            block["settlements"]["s1"] = {"place_id": "p1", "population": 10}
            _world_seed_block(session, block, "econ-w6-seed-0006")
            status, out = _post(server, "/api/world/directive", {
                "op": "create", "settlement_id": "s1",
                "directive": {
                    "directive_id": "d-grain", "text": "确保粮仓储备",
                    "goal": {"metric": "grain_days", "op": ">=", "target": 5},
                    "budget": {"cap_total": 400},
                    "authorized_scope": ["procure"]}},
                sid=sid, headers={"Idempotency-Key": "world-w6-dir-0001"})
            assert status == 200, (status, out)

            # 日切：自动采买（目标 5 天 × 日耗 10 → 40 只 × 2 = 80 ≤ cap）
            out = notdnd_web._chron_advance(session, 2000)
            assert out["status"] == "ok", out
            saved = _world_block_of(session)
            directive = saved["settlements"]["s1"]["directives"]["d-grain"]
            assert directive["spent_total"] == 80, directive
            assert directive["status"] == "active", directive
            trades = [e for e in saved["economy"]["ledger"]
                      if (e.get("refs") or {}).get("directive_id")]
            assert [(e["kind"], e["delta"]) for e in trades] == \
                [("trade", -80), ("trade", 40)], trades
            # GET 可解释六项（§6.5 / §16.5）
            status, explain = server.get(
                "/api/world/directive?settlement_id=s1&directive_id=d-grain",
                sid=sid)
            assert status == 200, (status, explain)
            explain = explain["explain"]
            for key in ("goal", "budget", "projects", "blocked_reason",
                        "recent_decisions", "expected"):
                assert key in explain, key
            assert explain["budget"]["remaining"] == 320, explain["budget"]
            assert len(explain["recent_decisions"]) == 2, explain

            # paused：日切跳过
            status, _out = _post(server, "/api/world/directive",
                                 {"op": "pause", "settlement_id": "s1",
                                  "directive_id": "d-grain"}, sid=sid,
                                 headers={"Idempotency-Key":
                                          "world-w6-dir-pause"})
            assert status == 200, (status, _out)
            spent_before = _world_block_of(session)["settlements"]["s1"][
                "directives"]["d-grain"]["spent_total"]
            out = notdnd_web._chron_advance(session, 4000)
            assert out["status"] == "ok", out
            assert _world_block_of(session)["settlements"]["s1"][
                "directives"]["d-grain"]["spent_total"] == spent_before, \
                "paused 指令日切必须跳过"
            # revoked：只停后续
            status, _out = _post(server, "/api/world/directive",
                                 {"op": "revoke", "settlement_id": "s1",
                                  "directive_id": "d-grain"}, sid=sid,
                                 headers={"Idempotency-Key":
                                          "world-w6-dir-revoke"})
            assert status == 200, (status, _out)
            counts = _event_type_counts(session)
            assert counts.get("world.manage") == 3, counts   # create+pause+revoke
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_api_state_view():
    """M7-4：GET /api/state —— 只读摘要（campaign_id / seq / world_time / 块存在性）。

    真起子进程服务：新局摘要是固定的 200 形状（只回存在性，不回块内容 /
    元信息 / 本机路径）；无 X-Session / 没开过的 sid → 与 `/api/session`
    同口径的 400 固定短语。
    """
    with _Server() as server:
        status, created = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]
        save_dir = server.get("/api/saves")[1]["save_dir"]

        status, view = server.get("/api/state", sid=sid)
        assert status == 200, "已开会话应 200，实际 %s / %s" % (status, view)
        assert view["campaign_id"] == sid, "campaign_id 应与存档 sid 对齐"
        assert isinstance(view["seq"], int) and view["seq"] >= 0, view["seq"]
        assert view["world_time"] is None, "M7 还没有世界时间（M9 起才有值）"
        assert view["blocks"] == {"atlas": False, "guide": True,
                                  "room": True, "rules": True}, view["blocks"]
        raw = json.dumps(view, ensure_ascii=False)
        assert "salt" not in raw and "party" not in raw, "只回存在性，不回块内容"
        assert save_dir not in raw, "响应不得回本机路径"

        # 无 X-Session / 没开过的 sid：与 /api/session 同口径（400 + 固定短语）
        status, body = server.get("/api/state")
        assert status == 400 and body.get("error"), (status, body)
        status, unknown = server.get("/api/state", sid="never-opened")
        assert status == 400 and unknown.get("error"), (status, unknown)
        status, reference = server.get("/api/session")
        assert status == 400 and body.get("error") == reference.get("error"), \
            "「找不到会话」的语义与 /api/session 同口径"


def check_api_events_incremental():
    """M7-4：GET /api/events?since= —— 只回 `seq > since`；严格递增、不重不漏。

    缺省 / 空 `since` = 0（与 `/api/log?since=` 同口径，全量）；非法 `since`
    → 400 固定短语（不静默当 0）。走本进程服务，直接往会话句柄提交真事件。
    """
    with _LocalServer() as server:
        status, created, _raw, _heads = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]
        handle = notdnd_web._sessions[sid]._handle
        for index in range(3):
            _commit_state_event(handle, index)

        status, body = server.get("/api/events?since=0", sid=sid)
        assert status == 200, (status, body)
        assert [ev["seq"] for ev in body["events"]] == [1, 2, 3]
        assert all(ev["campaign_id"] == sid for ev in body["events"])
        status, body = server.get("/api/events?since=2", sid=sid)
        assert [ev["seq"] for ev in body["events"]] == [3]
        for since in (3, 99):
            status, body = server.get("/api/events?since=%d" % since, sid=sid)
            assert status == 200 and body["events"] == [], (since, status, body)

        # 缺省 / 空 = 0：与 /api/log?since= 同口径（全量）
        for path in ("/api/events", "/api/events?since="):
            status, body = server.get(path, sid=sid)
            assert status == 200, (path, status, body)
            assert [ev["seq"] for ev in body["events"]] == [1, 2, 3], path

        # 非法 since → 400 固定短语
        for bad in ("abc", "-1", "1.5", "1e2", "%203"):
            status, body = server.get("/api/events?since=" + bad, sid=sid)
            assert (status, body.get("error")) == (400, "since 需为非负整数"), \
                (bad, status, body)

        # 无会话：与 /api/session 同口径的 400
        status, body = server.get("/api/events?since=0")
        assert status == 400 and body.get("error"), (status, body)


def check_api_events_across_archives():
    """M7-4：跨归档段 —— `events.<n>.ndjson` + 活动日志拼接后仍不重不漏（§4.1 / §9）。"""
    with _LocalServer() as server:
        status, created, _raw, _heads = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]
        handle = notdnd_web._sessions[sid]._handle
        with _state_thresholds(CHECKPOINT_EVERY=10 ** 9,
                               CHECKPOINT_MAX_AGE_S=1e9,
                               CHECKPOINT_MAX_LOG_BYTES=10 ** 18,
                               EVENT_ARCHIVE_THRESHOLD=4):
            for index in range(6):
                _commit_state_event(handle, index)
            handle.flush()                          # 归档段：seq 1..6
            for index in range(6, 9):               # 活动日志：seq 7..9
                _commit_state_event(handle, index)
            camp = _files_fingerprint(_campaign_dir(sid))
            assert any(name.startswith("events.") and name.endswith(".ndjson")
                       for name in camp), "归档应真的发生：%r" % camp

            status, body = server.get("/api/events?since=0", sid=sid)
            assert status == 200, (status, body)
            assert [ev["seq"] for ev in body["events"]] == list(range(1, 10))
            status, body = server.get("/api/events?since=5", sid=sid)
            assert [ev["seq"] for ev in body["events"]] == [6, 7, 8, 9]

        # 重新载入（丢掉内存缓存）：跨归档拼接不依赖任何内存副本
        notdnd_web._sessions.pop(sid, None)
        status, body = server.get("/api/events?since=5", sid=sid)
        assert status == 200, (status, body)
        assert [ev["seq"] for ev in body["events"]] == [6, 7, 8, 9]


def check_api_state_readonly():
    """M7-4：两条接口**只读** —— 调用前后存档目录指纹不变、不触发检查点、
    内存态 `seq` 不变（验收 4）。"""
    with _LocalServer() as server:
        status, created, _raw, _heads = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]
        handle = notdnd_web._sessions[sid]._handle
        for index in range(2):
            _commit_state_event(handle, index)

        camp = _campaign_dir(sid)
        before = _files_fingerprint(camp)
        seq_before = handle.view()["seq"]

        status, view = server.get("/api/state", sid=sid)
        assert status == 200 and view["seq"] == seq_before, (status, view)
        for since in (0, 1, 2, 99):
            status, body = server.get("/api/events?since=%d" % since, sid=sid)
            assert status == 200, (since, status, body)
            assert str(notdnd_web.SAVE_DIR) not in json.dumps(body, ensure_ascii=False), \
                "响应不得回本机路径"

        assert _files_fingerprint(camp) == before, \
            "只读接口不得改盘（size / mtime / sha256 指纹必须不变）"
        assert handle.view()["seq"] == seq_before, "只读接口不得改内存态 seq"
        assert str(notdnd_web.SAVE_DIR) not in json.dumps(view, ensure_ascii=False)


def check_api_state_legacy_readonly():
    """M7-4：老档（未迁移）→ 同一形状的 200；**不**触发惰性迁移、不改写磁盘。

    老档没有状态层句柄：摘要按内存门面派生（`seq` 为 0——状态层还没有这条
    战役的事件日志），拒绝为读一个数落盘迁移（§10.1 的惰性迁移只发生在
    第一次 save）。
    """
    sid = "m74-legacy-1"
    legacy = {"sid": sid, "created": 1700000000.0,
              "log": [{"seq": 1, "kind": "narrative", "text": "旧档旁白", "ts": 1}],
              "seq": 1, "save_name": "旧档"}
    with _Server() as server:
        save_dir = server.get("/api/saves")[1]["save_dir"]
        path = os.path.join(save_dir, "%s.json" % sid)
        _write_save(path, legacy)
        with open(path, "rb") as handle:
            before = handle.read()

        status, view = server.get("/api/state", sid=sid)
        assert status == 200, (status, view)
        assert view["campaign_id"] == sid
        assert view["seq"] == 0, "状态层还没有这条战役的事件日志"
        assert view["world_time"] is None
        assert view["blocks"] == {"atlas": False, "guide": True,
                                  "room": True, "rules": True}, view["blocks"]

        status, body = server.get("/api/events?since=0", sid=sid)
        assert status == 200 and body["events"] == [], (status, body)

        with open(path, "rb") as handle:
            assert handle.read() == before, "只读接口不得改写老档"
        assert not os.path.exists(_campaign_file(sid, "snapshot.json", save_dir)), \
            "只读接口不得触发老档迁移（不落盘）"


def _disk_room(rid: str) -> dict:
    """本进程服务写出的房间块（检查点里的 `blocks.room`）。"""
    return _read_room_block(str(notdnd_web.SAVE_DIR), rid)


def _room_post(server, path, body, rid, member, extra_headers=None):
    """带房间鉴权头的 POST：(状态, JSON)。"""
    headers = _room_headers(rid, member["member_id"], member["token"])
    if extra_headers:
        headers.update(extra_headers)
    return _post(server, path, body, headers=headers)


def _room_poll(server, rid, member, since=0):
    """短轮询该成员看得到的帧。"""
    headers = dict(_room_headers(rid, member["member_id"], member["token"]),
                   Accept="application/json")
    return server.get("/api/room/stream?since=%d" % since, headers=headers)


def _rules_digest(rid: str) -> str:
    """规则快照的规范 JSON：拒绝路径用来证明「没有结算」。"""
    rules = notdnd_web.get_session(rid).rules
    return json.dumps(rules, sort_keys=True, ensure_ascii=False)


def _event_seq(rid: str) -> int:
    return notdnd_web.get_session(rid)._handle.view()["seq"]


def check_room_pause_non_host_t8():
    """非房主暂停 / 恢复 → 403，磁盘上的房间块逐字节不变（T8 的暂停半边）。

    既有 `check_room_kick_authorization` 仍只覆盖踢人，这里不改它的断言。
    """
    rid = "room-pause-t8"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        other = _join_room(server, rid, character_id="pc-b")
        before = _disk_room(rid)
        status, denied = _room_post(server, "/api/room/pause", {"paused": True},
                                    rid, other)
        assert status == 403 and denied.get("reason") == "not_authorized", \
            (status, denied)
        assert denied.get("error") == "只有房主能暂停或恢复", denied
        assert _disk_room(rid) == before, "非房主暂停不得改盘"
        # 房主先暂停，非房主再恢复：仍 403，暂停保持。
        status, paused = _room_post(server, "/api/room/pause", {"paused": True},
                                    rid, host)
        assert status == 200 and paused["paused"] is True, (status, paused)
        frozen = _disk_room(rid)
        assert frozen["paused"] is True
        status, resume = _room_post(server, "/api/room/pause", {"paused": False},
                                    rid, other)
        assert status == 403 and resume.get("reason") == "not_authorized", \
            (status, resume)
        assert _disk_room(rid) == frozen, "非房主恢复不得改盘"
        status, settings = _room_post(server, "/api/room/settings",
                                      {"mode": "event"}, rid, other)
        assert status == 403 and settings.get("reason") == "not_authorized", \
            (status, settings)
        assert settings.get("error") == "只有房主能改房间设置", settings
        assert _disk_room(rid) == frozen


def check_room_pause_t9():
    """T9：房主暂停后世界变更 409 `room_paused`，只读与聊天仍成功；恢复后照常。"""
    rid = "room-pause-t9"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        other = _join_room(server, rid, character_id="pc-b")
        session = _seed_room_scene(rid, character_ids=("pc-a", "pc-b"),
                                   action_id="pickup")
        base = session._handle.view()["seq"]
        status, first = _room_action_post(
            server, rid, host, action_id="pickup", base_seq=base,
            key="t9-before-pause-01")
        assert status == 200 and first["replayed"] is False, (status, first)
        actions_before = len(_room_action_events(rid))
        assert actions_before == 1

        status, bad = _room_post(server, "/api/room/pause", {"paused": "yes"},
                                 rid, host)
        assert status == 400 and bad.get("reason") == "bad_paused", (status, bad)

        status, paused = _room_post(server, "/api/room/pause", {"paused": True},
                                    rid, host)
        assert status == 200, (status, paused)
        assert paused["paused"] is True and paused["chron_window"] is False, paused
        assert _disk_room(rid)["paused"] is True
        seq_paused = _event_seq(rid)
        assert seq_paused == paused["seq"]
        host_events = [e for e in session._handle.events_since(0)
                       if e["type"] == "host.pause"]
        assert len(host_events) == 1, host_events
        assert host_events[0]["changes"][0]["path"] == "/paused"
        assert host_events[0]["changes"][0]["after"] is True
        # 再暂停一次：不另写事件。
        status, again = _room_post(server, "/api/room/pause", {"paused": True},
                                   rid, host)
        assert status == 200 and again["paused"] is True, (status, again)
        assert len([e for e in session._handle.events_since(0)
                    if e["type"] == "host.pause"]) == 1
        # 留痕只给房主。
        status, host_view = _room_poll(server, rid, host)
        status2, other_view = _room_poll(server, rid, other)
        assert status == 200 and status2 == 200, (status, status2)
        assert any(f["event"]["type"] == "host.pause"
                   for f in host_view["frames"]), host_view
        assert all(f["event"]["type"] != "host.pause"
                   for f in other_view["frames"]), other_view

        rules_at_pause = _rules_digest(rid)
        status, blocked = _room_action_post(
            server, rid, other, action_id="pickup", base_seq=0,
            key="t9-while-paused-01")
        assert status == 409 and blocked.get("reason") == "room_paused", \
            (status, blocked)
        assert blocked.get("error") == "房主已暂停", blocked
        assert _event_seq(rid) == seq_paused
        assert len(_room_action_events(rid)) == actions_before
        assert _rules_digest(rid) == rules_at_pause

        # 只读仍成功。
        status, view = server.get("/api/state", sid=rid)
        assert status == 200 and view["campaign_id"] == rid, (status, view)
        status, polled = _room_poll(server, rid, other)
        assert status == 200 and "frames" in polled, (status, polled)
        # 聊天不是世界事实，暂停期间仍可发。
        status, chat = _room_post(server, "/api/room/chat",
                                  {"channel": "world", "text": "先等一下"},
                                  rid, other)
        assert status == 200, (status, chat)
        # 暂停前那次提交的同键重放仍回首次结果。
        status, replay = _room_action_post(
            server, rid, host, action_id="pickup", base_seq=base,
            key="t9-before-pause-01")
        assert status == 200 and replay["replayed"] is True, (status, replay)
        assert replay["result"] == first["result"]
        assert len(_room_action_events(rid)) == actions_before

        # 旁路的世界变更同样被挡住（不改 chron.py / 地图 / 规则）。
        status, moved = _post(server, "/api/atlas/move", {"via": "north"}, sid=rid)
        assert status == 409 and moved.get("reason") == "room_paused", (status, moved)
        status, advanced = _post(server, "/api/chron/advance",
                                 {"target_minute": 30}, sid=rid)
        assert status == 409 and advanced.get("reason") == "room_paused", \
            (status, advanced)
        status, cancelled = _post(server, "/api/chron/cancel",
                                  {"task_id": "act_" + "ab" * 16}, sid=rid)
        assert status == 409 and cancelled.get("reason") == "room_paused", \
            (status, cancelled)
        status, committed = _post(
            server, "/api/action/commit", {}, sid=rid,
            headers={"Idempotency-Key": "t9-commit-paused-01"})
        assert status == 409 and committed.get("reason") == "room_paused", \
            (status, committed)
        status, guided = _post(server, "/api/guide/turn",
                               {"text": "我捡起来", "action_id": "pickup"}, sid=rid)
        assert status == 409 and guided.get("reason") == "room_paused", \
            (status, guided)
        # 淡出整句不相等结算，暂停不拦。
        status, faded = _post(server, "/api/guide/turn",
                              {"text": notdnd_web.GUIDE_FADE_WORD,
                               "action_id": "pickup"}, sid=rid)
        assert status == 200 and faded["status"] == "ok", (status, faded)
        assert len(_room_action_events(rid)) == actions_before

        # 丢掉内存缓存后仍从磁盘读到暂停（落盘生效，不只是内存旗标）。
        notdnd_web._sessions.pop(rid, None)
        status, still = _room_action_post(
            server, rid, host, action_id="pickup", base_seq=0,
            key="t9-after-reload-01")
        assert status == 409 and still.get("reason") == "room_paused", \
            (status, still)
        assert notdnd_web.get_session(rid).room["paused"] is True

        status, resumed = _room_post(server, "/api/room/pause", {"paused": False},
                                     rid, host)
        assert status == 200 and resumed["paused"] is False, (status, resumed)
        assert _disk_room(rid)["paused"] is False
        assert any(e["type"] == "host.resume"
                   for e in notdnd_web.get_session(rid)._handle.events_since(0))
        status, after = _room_action_post(
            server, rid, host, action_id="pickup",
            base_seq=_event_seq(rid), key="t9-after-resume-01")
        assert status == 200 and after["replayed"] is False, (status, after)
        assert len(_room_action_events(rid)) == actions_before + 1


def check_room_pvp_t10():
    """T10：对他人角色的敌意行动按 `pvp_mode` 拒绝或放行；拒绝不产生结算。"""
    rid = "room-pvp-t10"
    actions = [
        {"id": "aid", "label": "救助", "kind": "check", "df": 8, "auto_pass": True},
        {"id": "strike", "label": "攻击", "kind": "check", "df": 8, "hostile": True,
         "auto_pass": True},
        {"id": "poison", "label": "下毒", "kind": "check", "df": 8,
         "tags": ["poison"], "auto_pass": True},
        {"id": "steal", "label": "偷窃", "kind": "check", "df": 8,
         "tags": ["steal"], "auto_pass": True},
        {"id": "smite", "label": "惩戒", "kind": "check", "df": 8, "hostile": True,
         "auto_pass": True},
        {"id": "vent", "label": "自伤", "kind": "check", "df": 8, "hostile": True,
         "auto_pass": True},
        {"id": "duel", "label": "对决", "kind": "check", "df": 8, "hostile": True,
         "auto_pass": True},
        {"id": "finish", "label": "补刀", "kind": "check", "df": 8, "hostile": True,
         "auto_pass": True},
    ]
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        other = _join_room(server, rid, character_id="pc-b")
        _seed_room_scene(rid, character_ids=("pc-a", "pc-b"), actions=actions)
        target = {"target": {"kind": "player", "id": "pc-b"}}

        def strike(key, **kwargs):
            return _room_action_post(
                server, rid, host, action_id=kwargs.pop("action_id", "strike"),
                base_seq=_event_seq(rid), key=key, params=target, **kwargs)

        digest = _rules_digest(rid)
        seq = _event_seq(rid)
        n_actions = len(_room_action_events(rid))
        status, needs = strike("t10-no-token-00001")
        assert status == 403 and needs.get("reason") == "pvp_needs_consent", \
            (status, needs)
        assert needs.get("error") == "需要对方同意这次对抗", needs
        assert _rules_digest(rid) == digest and _event_seq(rid) == seq
        assert len(_room_action_events(rid)) == n_actions

        junk = "pvpc_" + "ab" * 16
        status, junked = strike(
            "t10-junk-token-0001",
            extra_headers={"X-Pvp-Consent": junk})
        assert status == 403 and junked.get("reason") == "pvp_needs_consent", \
            (status, junked)
        assert _rules_digest(rid) == digest and _event_seq(rid) == seq

        status, self_ok = _room_post(
            server, "/api/room/pvp/consent",
            {"action_id": "strike", "actor_member_id": host["member_id"]},
            rid, host)
        assert status == 400 and self_ok.get("reason") == "bad_target", \
            (status, self_ok)
        status, not_hostile = _room_post(
            server, "/api/room/pvp/consent",
            {"action_id": "aid", "actor_member_id": host["member_id"]},
            rid, other)
        assert status == 400 and not_hostile.get("reason") == "bad_action", \
            (status, not_hostile)

        status, issued = _room_post(
            server, "/api/room/pvp/consent",
            {"action_id": "strike", "actor_member_id": host["member_id"]},
            rid, other)
        assert status == 200, (status, issued)
        token = issued["consent_token"]
        assert notdnd_web.ROOM_PVP_CONSENT_RE.fullmatch(token), token
        assert token not in _save_bytes(str(notdnd_web.SAVE_DIR), rid)

        # 令牌对不上这次行动：不消耗。
        status, mismatched = _room_action_post(
            server, rid, host, action_id="poison", base_seq=_event_seq(rid),
            key="t10-mismatch-00001", params=target,
            extra_headers={"X-Pvp-Consent": token})
        assert status == 403 and mismatched.get("reason") == "pvp_needs_consent", \
            (status, mismatched)
        assert _rules_digest(rid) == digest
        # 头优先于 body：头是错的，body 里那枚对的也不该被烧掉。
        status, header_wins = _room_action_post(
            server, rid, host, action_id="strike", base_seq=_event_seq(rid),
            key="t10-header-wins-001",
            params={"target": {"kind": "player", "id": "pc-b"},
                    "pvp_consent": token},
            extra_headers={"X-Pvp-Consent": junk})
        assert status == 403 and header_wins.get("reason") == "pvp_needs_consent", \
            (status, header_wins)
        status, allowed = _room_action_post(
            server, rid, host, action_id="strike", base_seq=_event_seq(rid),
            key="t10-consent-ok-0001",
            params={"target": {"kind": "player", "id": "pc-b"},
                    "pvp_consent": junk},
            extra_headers={"X-Pvp-Consent": token})
        assert status == 200 and allowed["replayed"] is False, (status, allowed)
        assert "strike" in (notdnd_web.get_session(rid).rules.get("done_actions") or {})
        assert len(_room_action_events(rid)) == n_actions + 1
        assert token not in _save_bytes(str(notdnd_web.SAVE_DIR), rid)
        # 一次性：再用同一枚 → 拒绝，且不再结算。
        digest_after = _rules_digest(rid)
        seq_after = _event_seq(rid)
        status, reused = _room_action_post(
            server, rid, host, action_id="poison", base_seq=seq_after,
            key="t10-reuse-token-001", params=target,
            extra_headers={"X-Pvp-Consent": token})
        assert status == 403 and reused.get("reason") == "pvp_needs_consent", \
            (status, reused)
        assert _rules_digest(rid) == digest_after and _event_seq(rid) == seq_after

        # off 不看令牌、也不消耗。先在 consent 下签发，再关掉。
        status, issued_steal = _room_post(
            server, "/api/room/pvp/consent",
            {"action_id": "steal", "actor_member_id": host["member_id"]},
            rid, other)
        assert status == 200, (status, issued_steal)
        steal_token = issued_steal["consent_token"]
        status, off = _room_post(server, "/api/room/settings", {"pvp_mode": "off"},
                                 rid, host)
        assert status == 200 and off["pvp_mode"] == "off", (status, off)
        assert _disk_room(rid)["pvp_mode"] == "off"
        status, cannot_issue = _room_post(
            server, "/api/room/pvp/consent",
            {"action_id": "duel", "actor_member_id": host["member_id"]},
            rid, other)
        assert status == 403 and cannot_issue.get("reason") == "pvp_disabled", \
            (status, cannot_issue)
        digest_off = _rules_digest(rid)
        seq_off = _event_seq(rid)
        n_off = len(_room_action_events(rid))
        status, disabled = _room_action_post(
            server, rid, host, action_id="poison", base_seq=seq_off,
            key="t10-off-junk-000001", params=target,
            extra_headers={"X-Pvp-Consent": steal_token})
        assert status == 403 and disabled.get("reason") == "pvp_disabled", \
            (status, disabled)
        assert disabled.get("error") == "本房间未开启玩家对抗", disabled
        assert _rules_digest(rid) == digest_off and _event_seq(rid) == seq_off
        assert len(_room_action_events(rid)) == n_off
        # 回到 consent：刚才那枚偷盗令牌还在。
        status, back = _room_post(server, "/api/room/settings",
                                  {"pvp_mode": "consent"}, rid, host)
        assert status == 200 and back["pvp_mode"] == "consent", (status, back)
        status, stole = _room_action_post(
            server, rid, host, action_id="steal", base_seq=_event_seq(rid),
            key="t10-steal-ok-00001", params=target,
            extra_headers={"X-Pvp-Consent": steal_token})
        assert status == 200, (status, stole)
        assert "steal" in (notdnd_web.get_session(rid).rules.get("done_actions") or {})

        status, off2 = _room_post(server, "/api/room/settings", {"pvp_mode": "off"},
                                  rid, host)
        assert status == 200, (status, off2)
        # 非敌意、打 NPC、打自己：不是「对另一名玩家角色」，off 也放行。
        status, aided = _room_action_post(
            server, rid, host, action_id="aid", base_seq=_event_seq(rid),
            key="t10-aid-ok-0000001", params=target)
        assert status == 200, (status, aided)
        assert "aid" in (notdnd_web.get_session(rid).rules.get("done_actions") or {})
        status, smote = _room_action_post(
            server, rid, host, action_id="smite", base_seq=_event_seq(rid),
            key="t10-npc-ok-0000001",
            params={"target": {"kind": "npc", "id": "npc-bandit"}})
        assert status == 200, (status, smote)
        assert "smite" in (notdnd_web.get_session(rid).rules.get("done_actions") or {})
        status, vented = _room_action_post(
            server, rid, host, action_id="vent", base_seq=_event_seq(rid),
            key="t10-self-ok-000001",
            params={"target": {"kind": "player", "id": "pc-a"}})
        assert status == 200, (status, vented)

        status, on = _room_post(server, "/api/room/settings", {"pvp_mode": "on"},
                                rid, host)
        assert status == 200 and on["pvp_mode"] == "on", (status, on)
        assert _disk_room(rid)["pvp_mode"] == "on"
        status, no_need = _room_post(
            server, "/api/room/pvp/consent",
            {"action_id": "duel", "actor_member_id": host["member_id"]},
            rid, other)
        assert status == 409 and no_need.get("reason") == "pvp_not_required", \
            (status, no_need)
        before_duel = len(_room_action_events(rid))
        status, dueled = _room_action_post(
            server, rid, host, action_id="duel", base_seq=_event_seq(rid),
            key="t10-duel-on-000001", params=target)
        assert status == 200 and dueled["replayed"] is False, (status, dueled)
        assert dueled["result"]["committed"] is True, dueled["result"]
        rules = notdnd_web.get_session(rid).rules
        assert "duel" in (rules.get("done_actions") or {}), rules.get("done_actions")
        commits = _room_action_events(rid)
        assert len(commits) == before_duel + 1
        assert commits[-1]["actor"] == "pc-a", commits[-1]
        # 离开后的角色不再是在座玩家：off 也不拦。
        status, off3 = _room_post(server, "/api/room/settings", {"pvp_mode": "off"},
                                  rid, host)
        assert status == 200, (status, off3)
        status, left = _room_post(server, "/api/room/leave", {}, rid, other)
        assert status == 200, (status, left)
        status, finished = _room_action_post(
            server, rid, host, action_id="finish", base_seq=_event_seq(rid),
            key="t10-left-ok-000001", params=target)
        assert status == 200, (status, finished)
        assert "finish" in (notdnd_web.get_session(rid).rules.get("done_actions") or {})


def check_room_modes():
    """自由 / 事件 / 管理：落盘键是 `room.mode`；事件模式不开 CHRON 窗口。"""
    rid = "room-modes-1"
    with _LocalServer() as server:
        host = _new_room(server, rid, character_id="pc-a")
        assert "mode" not in host and "paused" not in host, host
        block = _disk_room(rid)
        assert block["mode"] == "free" and block["pvp_mode"] == "consent"
        assert block["paused"] is False
        other = _join_room(server, rid, character_id="pc-b")
        _seed_room_scene(rid, character_ids=("pc-a", "pc-b"), actions=[
            {"id": "pickup", "label": "拾取", "kind": "check", "df": 8,
             "auto_pass": True},
            {"id": "aid", "label": "救助", "kind": "check", "df": 8,
             "auto_pass": True},
        ])
        status, empty = _room_post(server, "/api/room/settings", {}, rid, host)
        assert status == 400 and empty.get("reason") == "bad_settings", (status, empty)
        status, bad_mode = _room_post(server, "/api/room/settings",
                                      {"mode": "ranked"}, rid, host)
        assert status == 400 and bad_mode.get("reason") == "bad_mode", (status, bad_mode)
        status, bad_pvp = _room_post(server, "/api/room/settings",
                                     {"pvp_mode": "maybe"}, rid, host)
        assert status == 400 and bad_pvp.get("reason") == "bad_pvp_mode", \
            (status, bad_pvp)
        assert _disk_room(rid)["mode"] == "free"

        session = notdnd_web.get_session(rid)
        chron_before = json.dumps(
            (session._handle.view().get("blocks") or {}).get("chron"),
            sort_keys=True, ensure_ascii=False)
        status, event_mode = _room_post(server, "/api/room/settings",
                                        {"mode": "event"}, rid, host)
        assert status == 200, (status, event_mode)
        assert event_mode["mode"] == "event", event_mode
        assert event_mode["settlement"] == "serial", event_mode
        assert event_mode["chron_window"] is False, event_mode
        assert "projects" not in event_mode
        assert _disk_room(rid)["mode"] == "event"
        chron_after = json.dumps(
            (session._handle.view().get("blocks") or {}).get("chron"),
            sort_keys=True, ensure_ascii=False)
        assert chron_after == chron_before, "切到事件模式不得改 chron 块"
        mode_events = [e for e in session._handle.events_since(0)
                       if e["type"] == "host.mode"]
        assert len(mode_events) == 1, mode_events
        assert mode_events[0]["changes"][0]["after"] == "event"
        status, seen = _room_poll(server, rid, host)
        status2, hidden = _room_poll(server, rid, other)
        assert status == 200 and status2 == 200
        assert any(f["event"]["type"] == "host.mode" for f in seen["frames"])
        assert all(f["event"]["type"] != "host.mode" for f in hidden["frames"])
        # 再切一次同一模式：不另写事件。
        status, noop = _room_post(server, "/api/room/settings", {"mode": "event"},
                                  rid, host)
        assert status == 200 and noop["settlement"] == "serial", (status, noop)
        assert len([e for e in session._handle.events_since(0)
                    if e["type"] == "host.mode"]) == 1

        # 事件模式仍是既有的单写者串行提交：两笔都 200，没有局部同步窗口。
        status, first = _room_action_post(
            server, rid, host, action_id="pickup", base_seq=_event_seq(rid),
            key="mode-event-act-0001")
        status2, second = _room_action_post(
            server, rid, other, action_id="aid", base_seq=_event_seq(rid),
            key="mode-event-act-0002")
        assert status == 200 and status2 == 200, (status, first, status2, second)
        assert first["seq"] != second["seq"]
        types = [e["type"] for e in session._handle.events_since(0)]
        assert "window.opened" not in types, types
        assert types.count(notdnd_web.ACTION_EVENT_TYPE) == 2

        chron_manage = json.dumps(
            (session._handle.view().get("blocks") or {}).get("chron"),
            sort_keys=True, ensure_ascii=False)
        status, manage = _room_post(
            server, "/api/room/settings",
            {"mode": "manage", "pvp_mode": "on"}, rid, host)
        assert status == 200, (status, manage)
        assert manage["mode"] == "manage" and manage["pvp_mode"] == "on", manage
        assert manage["settlement"] == "independent", manage
        assert manage["chron_window"] is False
        disk = _disk_room(rid)
        assert disk["mode"] == "manage" and disk["pvp_mode"] == "on"
        assert "projects" not in disk
        chron_now = json.dumps(
            (session._handle.view().get("blocks") or {}).get("chron"),
            sort_keys=True, ensure_ascii=False)
        assert chron_now == chron_manage, "管理模式不建长期项目、不改 chron"
        settings_events = [e for e in session._handle.events_since(0)
                           if e["type"] == "host.settings"]
        assert len(settings_events) == 1, settings_events
        paths = sorted(change["path"] for change in settings_events[0]["changes"])
        assert paths == ["/mode", "/pvp_mode"], settings_events[0]["changes"]
        # 管理模式下行动仍各自提交。
        status, third = _room_action_post(
            server, rid, host, action_id="pickup", base_seq=_event_seq(rid),
            key="mode-manage-act-001")
        assert status == 200, (status, third)
        assert [e["type"] for e in session._handle.events_since(0)
                ].count(notdnd_web.ACTION_EVENT_TYPE) == 3

        cleaned = notdnd_web._room_from(
            {"paused": "yes", "pvp_mode": "maybe", "mode": "ranked",
             "host_member_id": ""}, rid)
        assert cleaned["paused"] is False
        assert cleaned["pvp_mode"] == "consent"
        assert cleaned["mode"] == "free"


# --------------------------------------------------------------------------
# 世界生成会话编排（M12-W3，WORLD-DESIGN.md §4 / §11 W3，Issue #387）
# --------------------------------------------------------------------------


def _world_gen_body(**patch) -> dict:
    """一份合法的生成请求体（离线全流程可过校验的最小 spec）。"""
    body = {"world_type": "fantasy", "scale": "region",
            "tone_terms": ["港口"], "seed_hint": "seed-w3"}
    body.update(patch)
    return body


def _world_instances_file(save_dir: str, sid: str) -> str:
    """玩家存档内的实例 sidecar 路径（P6：实例存玩家战役存档内）。"""
    return os.path.join(save_dir, "campaigns", sid,
                        notdnd_web.WORLDGEN_INSTANCES_FILE)


def _worlds_dir_digest() -> dict:
    """`data/worlds/` 逐文件 sha256（验收：系统库零变化）。"""
    out = {}
    root = os.path.join(ROOT, "data", "worlds")
    for name in sorted(os.listdir(root)):
        if name.endswith(".json"):
            with open(os.path.join(root, name), "rb") as handle:
                out[name] = hashlib.sha256(handle.read()).hexdigest()
    return out


def check_world_generate_commit_round():
    """离线全流程：generate 200 → 预览卡 → commit 入玩家存档。

    断言：预算计数 ≤ 24（离线为 0）；预览不落块（`GET /api/state` 无 world
    块）；实例落玩家存档 sidecar；`data/worlds/` 逐文件不变；commit 一次性。
    """
    with _Server(offline=True) as server:
        status, created = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]

        worlds_before = _worlds_dir_digest()
        status, preview = server.post("/api/world/generate",
                                      _world_gen_body(), sid=sid)
        assert status == 200, (status, preview)
        assert preview["status"] == "ok", preview
        assert preview["session_id"].startswith("wg_"), preview
        # 离线：0 次调用；预算上限是人拍板 P4 的 24。
        assert preview["budget"] == {"calls": 0, "est_tokens": 0,
                                     "limit": 24}, preview["budget"]
        assert preview["world"]["key"] == "emberfall", preview["world"]
        assert preview["estimate"]["regions"] == 5, preview["estimate"]
        assert len(preview["regions"]) == 5, preview["regions"]
        assert preview["goods_count"] > 0, preview
        assert preview["overrides"]["diff"] == [], preview["overrides"]
        # 空的 seed_hint 请求也会拿到一个 seed（重试/重掷要靠它）。
        assert isinstance(preview["seed"], int), preview["seed"]

        status, state = server.get("/api/state", sid=sid)
        assert status == 200, (status, state)
        assert "world" not in state["blocks"], state      # 预览不落块

        status, committed = server.post("/api/world/commit",
                                        {"session_id": preview["session_id"]},
                                        sid=sid)
        assert status == 200, (status, committed)
        assert committed["instance_id"].startswith("wi_"), committed
        assert committed["world_name"] == preview["world"]["name"], committed

        # commit 一次性：同一 session_id 二次提交 → 404（防重复入库）。
        status, again = server.post("/api/world/commit",
                                    {"session_id": preview["session_id"]},
                                    sid=sid)
        assert status == 404, (status, again)

        # 实例在**玩家战役存档**里（sidecar），完整世界实例随存档持久。
        with open(_world_instances_file(server.dir, sid), encoding="utf-8") as fh:
            stored = json.load(fh)
        assert stored["version"] == 1 and len(stored["instances"]) == 1, stored
        instance = stored["instances"][0]
        assert instance["instance_id"] == committed["instance_id"], instance
        assert instance["world"]["kind"] == "world.module", instance
        assert instance["seed"] == preview["seed"], instance
        assert instance["spec"]["world_type"] == "fantasy", instance

        # 系统库零变化：generate/commit 全程没碰 `data/worlds/`。
        assert _worlds_dir_digest() == worlds_before


def check_world_generate_error_blocks_commit():
    """error 阻断（1.7）：越档 goods → 422 + issues，无预览、无会话可 commit。

    构造：奇幻世界 + goods_extra era=future（激光枪）→ E_GOODS_ERA error。
    """
    with _Server(offline=True) as server:
        status, created = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]

        status, body = server.post("/api/world/generate", _world_gen_body(
            goods_extra=[{"name": "激光枪", "era": "future",
                          "category": "military"}]), sid=sid)
        assert status == 422, (status, body)
        assert body["status"] == "error", body
        assert any(i["code"] == "E_GOODS_ERA" and i["level"] == "error"
                   for i in body["issues"]), body["issues"]
        assert "session_id" not in body, body      # 没有可 commit 的会话

        # 无预览可 commit：任意 session_id 都打不进存档。
        status, refused = server.post(
            "/api/world/commit",
            {"session_id": "wg_" + "0" * 32}, sid=sid)
        assert status == 404, (status, refused)
        assert not os.path.exists(_world_instances_file(server.dir, sid))


def check_world_generate_overrides_diff():
    """overrides diff 可读：改一处 / 加两处 / 删一处，op + path 一目了然。"""
    with _Server(offline=True) as server:
        status, created = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]

        overrides = {
            "region_upserts": [{"id": "region-01",
                                "summary": "改写后的区域短文。"},
                               {"name": "新地区", "summary": "新长的区域。"}],
            "region_removals": ["region-05"],
            "faction_upserts": [{"name": "新势力", "alignment": "中立"}],
            "law_upserts": [{"name": "宵禁", "forbid_tags": ["夜行"]}],
            "note": "改一处、加三处、删一处",
        }
        status, preview = server.post("/api/world/generate",
                                      _world_gen_body(overrides=overrides),
                                      sid=sid)
        assert status == 200, (status, preview)
        diff = preview["overrides"]["diff"]
        assert preview["overrides"]["note"] == "改一处、加三处、删一处"
        ops = {(d["op"], d["path"]) for d in diff}
        assert ("change", "regions[region-01].summary") in ops, diff
        assert ("remove", "regions[region-05]") in ops, diff
        assert any(op == "add" and path.startswith("regions[regions-new-")
                   for op, path in ops), diff
        assert any(op == "add" and path.startswith("factions[factions-new-")
                   for op, path in ops), diff
        assert any(op == "add" and path.startswith("laws[laws-new-")
                   for op, path in ops), diff
        assert len(preview["regions"]) == 5, preview["regions"]   # 4 保留 + 1 新增
        # diff 里的展示值带前后文（可读），且截断过（预览卡不被长文淹没）。
        changed = next(d for d in diff if d["path"] == "regions[region-01].summary")
        assert changed["before"] and changed["after"] == "改写后的区域短文。", changed

        status, committed = server.post("/api/world/commit",
                                        {"session_id": preview["session_id"]},
                                        sid=sid)
        assert status == 200, (status, committed)
        with open(_world_instances_file(server.dir, sid), encoding="utf-8") as fh:
            stored = json.load(fh)
        world = stored["instances"][0]["world"]
        assert len(world["regions"]) == 5, world["regions"]
        assert world["regions"][0]["summary"] == "改写后的区域短文。", world["regions"][0]
        assert any(law["name"] == "宵禁" for law in world["laws"]), world["laws"]


def check_world_generate_seed_determinism():
    """同 seed_hint 同结果（§4.4 修订幂等）；不同会话互不串号。"""
    with _Server(offline=True) as server:
        status, created = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]

        status, first = server.post("/api/world/generate",
                                    _world_gen_body(seed_hint="det-42"),
                                    sid=sid)
        status2, second = server.post("/api/world/generate",
                                      _world_gen_body(seed_hint="det-42"),
                                      sid=sid)
        assert status == 200 and status2 == 200, (status, status2)
        assert first["session_id"] != second["session_id"]
        assert first["seed"] == second["seed"], (first["seed"], second["seed"])
        assert first["world"]["name"] == second["world"]["name"]
        assert ([r["name"] for r in first["regions"]]
                == [r["name"] for r in second["regions"]])


def check_world_generate_budget_and_port():
    """P1 模型端口接线：文本走 GUIDE 网关既有通道，预算计数对齐真实用量。

    每次真实 usage = prompt 7 + completion 3 = 10 tokens；断言 calls ≤ 24、
    `est_tokens == calls × 10`（差值回填，不再按常量 128 估算）、文本来源
    记 `port`。
    """
    transport = _FakeTransport()
    with _LocalServer() as server:
        with _guide_online(_ONLINE_ENV, transport):
            status, created, _, _ = server.post("/api/session/create", {})
            assert status == 200, (status, created)
            sid = created["sid"]
            status, preview, _, _ = server.post(
                "/api/world/generate", _world_gen_body(
                    world_type="modern", scale="sketch", seed_hint="port-1"),
                sid=sid)
        assert status == 200, (status, preview)
        # 生成调用全部经假传输（GUIDE 网关同一条通道），没有第二个出口。
        assert len(transport.calls) == preview["budget"]["calls"], (
            len(transport.calls), preview["budget"])
        calls = preview["budget"]["calls"]
        assert 1 <= calls <= 24, calls
        assert preview["budget"]["limit"] == 24
        assert preview["budget"]["est_tokens"] == calls * 10, preview["budget"]
        assert preview["text_sources"]["world_name"] == "port", preview["text_sources"]
        assert preview["world"]["name"], preview["world"]


def check_world_generate_room_auth():
    """鉴权裁定（§6.5）：多人房间仅房主可生成 / 提交，player 无权。

    单人会话 `X-Session` 即可；房间有多名在座成员时必须带**房主**成员令牌。
    """
    with _Server(offline=True) as server:
        rid = "wroom-w3"
        status, host = server.post(
            "/api/room/create", {"room_id": rid, "display_name": "房主"})
        assert status == 200, (status, host)
        status, player = server.post(
            "/api/room/join", {"room_id": rid, "display_name": "玩家"})
        assert status == 200, (status, player)

        # 多人房间：只有 X-Session（无成员令牌）→ 401（鉴权链不过）。
        status, denied = server.post("/api/world/generate", _world_gen_body(),
                                     sid=rid)
        assert status == 401, (status, denied)
        assert denied.get("reason") == "token_invalid", denied

        # player 成员令牌 → 403（host 发起，player 不可见预览）。
        status, denied = server.post(
            "/api/world/generate", _world_gen_body(), sid=rid,
            headers={"X-Member": player["member_id"],
                     "Authorization": "Bearer %s" % player["token"]})
        assert status == 403, (status, denied)

        # 房主成员令牌 → 200，commit 也要房主。
        host_headers = {"X-Member": host["member_id"],
                        "Authorization": "Bearer %s" % host["token"]}
        status, preview = server.post("/api/world/generate", _world_gen_body(),
                                      sid=rid, headers=host_headers)
        assert status == 200, (status, preview)
        status, committed = server.post(
            "/api/world/commit", {"session_id": preview["session_id"]},
            sid=rid, headers=host_headers)
        assert status == 200, (status, committed)


def check_world_export_import_round():
    """P6 保存与分享（v1 文件级）：export 原样带 Content-Disposition；
    import 过 validate_world 零 error 后入玩家存档；坏实例拒收。"""
    with _Server(offline=True) as server:
        status, created = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]
        status, preview = server.post("/api/world/generate", _world_gen_body(),
                                      sid=sid)
        assert status == 200, (status, preview)
        status, committed = server.post("/api/world/commit",
                                        {"session_id": preview["session_id"]},
                                        sid=sid)
        assert status == 200, (status, committed)

        status, raw, _ = _export_instance(server, sid, committed["instance_id"])
        assert status == 200 and raw["instance_id"] == committed["instance_id"]
        assert raw["world"]["kind"] == "world.module", raw

        # 导入：同一份导出文件 → 新 instance_id，sidecar 里两份实例。
        status, imported = server.post("/api/world/import",
                                       {"instance": raw}, sid=sid)
        assert status == 200, (status, imported)
        assert imported["instance_id"].startswith("wi_"), imported
        assert imported["instance_id"] != committed["instance_id"]
        with open(_world_instances_file(server.dir, sid), encoding="utf-8") as fh:
            stored = json.load(fh)
        assert len(stored["instances"]) == 2, stored
        assert stored["instances"][1].get("imported") is True

        # 坏实例：不是 world.module → 400。
        status, bad = server.post("/api/world/import",
                                  {"instance": {"kind": "nope"}}, sid=sid)
        assert status == 400, (status, bad)
        # 越档 goods → validate_world error → 422（与生成同一条阻断）。
        status, bad = server.post("/api/world/import", {"instance": {
            "kind": "world.module",
            "world": {"key": "x", "engine": "tactics", "world_type": "fantasy"},
            "regions": [],
            "goods": [{"good_id": "g1", "name": "激光枪", "category": "military",
                       "tier": 1, "base_price": 5, "stock": 5,
                       "produced_by": [], "era": "future"}],
        }}, sid=sid)
        assert status == 422, (status, bad)
        assert any(i["code"] == "E_GOODS_ERA" for i in bad["issues"]), bad


def _export_instance(server, sid: str, instance_id: str):
    """POST /api/world/export 的裸响应：返回 (状态码, JSON, 响应头字典)。"""
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    body = json.dumps({"instance_id": instance_id}, ensure_ascii=False).encode()
    try:
        conn.request("POST", "/api/world/export", body=body,
                     headers={"Content-Type": "application/json",
                              "X-Session": sid})
        response = conn.getresponse()
        heads = {key.lower(): value for key, value in response.getheaders()}
        return response.status, _load_json(response.read()), heads
    finally:
        conn.close()


def check_world_export_disposition():
    """export 带 `Content-Disposition: attachment`（v1「文件下载」的落点）。"""
    with _Server(offline=True) as server:
        status, created = server.post("/api/session/create", {})
        assert status == 200, (status, created)
        sid = created["sid"]
        status, preview = server.post("/api/world/generate", _world_gen_body(),
                                      sid=sid)
        status, committed = server.post("/api/world/commit",
                                        {"session_id": preview["session_id"]},
                                        sid=sid)
        assert status == 200, (status, committed)
        status, raw, heads = _export_instance(server, sid,
                                              committed["instance_id"])
        assert status == 200, (status, raw)
        assert heads.get("content-disposition", "").startswith("attachment"), heads
        # 不存在的实例 → 404（不泄露其它存档的内容）。
        status, missing, _ = _export_instance(server, sid, "wi_" + "0" * 24)
        assert status == 404, (status, missing)


# ── W7：NEURA 挂接（§8：投影注入 / 重判注入；Issue #394）────────────────────


def check_world_view_projection_keys():
    """§8.2（V10）：_neura_view 五键由 world.projection 注入且含真实数据。"""
    sid = "world-w7-view"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "world-w7-view-neura-1")
        world_block = _world_goods_block()
        world_block["facts"].append(
            {"fact_id": "fact-w7-1", "minute": 0,
             "kind": world.FACT_KIND_IMPORTANCE_OVERRIDE,
             "subject_ref": "npc-guard", "text": "", "value": 0.8})
        _world_seed_block(session, world_block, "world-w7-view-world-1")
        session.guide["known_clues"] = ["C-02", "C-01", "C-01"]
        session.guide["scenario_id"] = "ashen_crown"     # 真剧本：threads/势力

        view = notdnd_web._neura_view(session, block)
        assert view["known_refs"] == ["C-01", "C-02"], view["known_refs"]
        assert view["main_thread_refs"], "ashen_crown threads 展开非空"
        assert view["faction_refs"], "faction 归属 NPC 非空"
        assert view["importance_overrides"] == {"npc-guard": 0.8}
        assert view["world_facts"], "facts 投影非空"
        # travel_minutes：gate 与 market 相邻 → short 档；temple 不在
        # 「块内地点 × 队伍地点」里（没有 NPC 引用它）→ 不投影。
        assert view["travel_minutes"].get("gate") == \
            notdnd_web.BAND_MINUTES["short"]
        assert "temple" not in view["travel_minutes"]
        # 无世界实例 → M11 laws 缺省（空表 + none）。
        assert view["laws"] == [] and view["law_source"] == "none"

        # 恰有一个世界实例（带 laws）→ view.laws + law_source="world"。
        module = {"kind": "world.module",
                  "world": {"key": "w7", "name": "W7 测试世界"},
                  "factions": [{"id": "faction-01", "name": "守冠庭"}],
                  "laws": [{"id": "law-01", "name": "禁武令",
                            "forbid_tags": ["武器"], "scope_place_ids": [],
                            "threshold": "forbid", "penalty": "没收",
                            "source": "docs/system/x.md:1"}]}
        notdnd_web._world_instances_save(sid, [
            {"instance_id": "wi_w7_1", "world": module}])
        view2 = notdnd_web._neura_view(session, block)
        assert view2["law_source"] == "world"
        assert [law["id"] for law in view2["laws"]] == ["law-01"]
        assert view2["laws"][0]["forbid_tags"] == ["武器"]
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_view_projection_defaults_without_data():
    """§8.2：无世界数据 / 无剧本 → 全部空键（M11 行为逐字节一致）。"""
    sid = "world-w7-view-empty"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        block = neura.empty_block()
        block["npcs"]["npc-guard"] = _neura_mover_npc()
        _neura_seed_block(session, block, "world-w7-view-empty-neura")
        view = notdnd_web._neura_view(session, block)
        assert view["known_refs"] == []
        assert view["main_thread_refs"] == []
        assert view["faction_refs"] == []
        assert view["importance_overrides"] == {}
        assert view["travel_minutes"] == {}       # 无地图：bands 空 → 投影空
        assert view["world_facts"] == {}
        assert view["laws"] == [] and view["law_source"] == "none"
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_rejudge_force_due_wired():
    """§8.4（V12）端到端：本停点动作事件匹配的远场 NPC 经预算注入
    force_due_ids → 本 tick 强制到期；预算变更随 tick 提交落盘（A0）。"""
    sid = "world-w7-rejudge"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        far = _neura_mover_npc("npc-far", place="temple", slot_place="temple")
        far["importance"] = 0.9                   # temple 距 market 两格 → far 层
        block = neura.empty_block()
        block["npcs"]["npc-far"] = far
        _neura_seed_block(session, block, "world-w7-rejudge-neura-1")
        _world_seed_block(session, _world_goods_block(),
                          "world-w7-rejudge-world-1")
        # 模拟本停点动作事件已被 `_world_rejudge_candidates` 收集（§8.4：
        # 编排层内存传递；层过滤与预算在 tick 侧）。
        session._neura_rejudge_pending = {"npc-far"}

        out = notdnd_web._chron_advance(session, 10)
        assert out["status"] == "ok", out
        saved = _neura_block_of(session)
        # 强制到期：同日 important_far 本不该判（last_day == day），注入后判了。
        assert saved["npcs"]["npc-far"]["last_tick_minute"] == 10
        assert saved["journal"], "强制到期必须有决策留痕"
        # 预算计数落进 world 块（同日第 1 次）。
        world_saved = _world_block_of(session)
        assert world_saved["rejudge"] == {"day": 0, "counts": {"npc-far": 1}}
        # A0：块级变更仍只随 1 条 neura.tick 事件（不单开重判事件）。
        events = _neura_all_events(session)
        assert [t for t, _a in events].count("neura.tick") == 1, events

        # 同日第 2 次：预算内继续注入。
        session._neura_rejudge_pending = {"npc-far"}
        notdnd_web._chron_advance(session, 20)
        world_saved = _world_block_of(session)
        assert world_saved["rejudge"]["counts"]["npc-far"] == 2

        # 预算打满（4/日）→ 第 5 次不注入、不报错、远场不动。
        handle = session._handle or session._open_handle()
        world_before = _world_block_of(session)
        world_before["rejudge"]["counts"]["npc-far"] = world.REJUDGE_DAILY_CAP
        out = handle.commit(
            base_seq=handle.view()["seq"], idempotency_key="world-w7-cap-1",
            type="core.commit", actor="pc-1",
            changes=[{"block": "world", "op": "replace_block", "path": "",
                      "before": None, "after": world_before}],
            rules_version=notdnd_web.ACTION_RULES_VERSION, source="player")
        assert out["status"] == "committed", out
        session._neura_rejudge_pending = {"npc-far"}
        notdnd_web._chron_advance(session, 30)
        saved = _neura_block_of(session)
        assert saved["npcs"]["npc-far"]["last_tick_minute"] == 20, \
            "超预算不得重判（last_tick 停在上一次）"
        assert _world_block_of(session)["rejudge"]["counts"]["npc-far"] == 4
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_rejudge_match_event_collects_candidates():
    """§8.4：_world_rejudge_candidates 把匹配表结果记入本停点候选（内存）。"""
    sid = "world-w7-match"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        block = neura.empty_block()
        block["npcs"]["npc-a"] = _neura_mover_npc("npc-a", place="gate",
                                                  slot_place="gate")
        _neura_seed_block(session, block, "world-w7-match-neura-1")
        _world_seed_block(session, _world_goods_block(),
                          "world-w7-match-world-1")
        handle = session._handle or session._open_handle()
        # 事件投影带卡面地点：gate 在场 NPC（npc-a）命中规则 1。
        notdnd_web._world_rejudge_candidates(
            session, handle,
            {"type": "action.settle", "target": "act-1", "subject": "act-1",
             "place_id": "gate"})
        assert getattr(session, "_neura_rejudge_pending", None) == {"npc-a"}
        # 无命中 → 候选不新增。
        notdnd_web._world_rejudge_candidates(
            session, handle,
            {"type": "action.settle", "target": "act-2", "subject": "act-2",
             "place_id": "nowhere"})
        assert session._neura_rejudge_pending == {"npc-a"}
    finally:
        notdnd_web._sessions.pop(sid, None)


# ── W8：账本只读观察面 + 100 日长跑（§9.1 / §10.3；Issue #396）──────────────


def check_world_ledger_view_http():
    """W8 验收：`GET /api/world/ledger` 只读观察面——缺省回账本摘要
    （day / balances / prices / goods）+ 最近 Entry + 工程摘要（含
    `blocked_reason`）；`?project_id=` 回「这项工程为什么停」（状态 +
    blocked_reason + readiness + 该项目名下 Entry）；全程零状态变更。"""
    sid = "world-w8-ledger"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    try:
        with _LocalServer() as server:
            session = notdnd_web.get_session(sid)
            session.ensure_atlas()
            block = _world_project_block(status="blocked")
            block["projects"]["p-wall"]["blocked_reason"] = "supply"
            block["economy"]["ledger"].append(world.make_entry(
                "e-ledger-1", at_minute=0, kind="produce", account="grain",
                delta=5,
                reason={"verb": "produce", "actor": "s1",
                        "target_ref": "grain"},
                event_seq=1, settlement_id="s1"))
            block["economy"]["ledger"].append(world.make_entry(
                "e-ledger-2", at_minute=0, kind="build_pay", account="coin",
                delta=-100,
                reason={"verb": "build", "actor": "player",
                        "target_ref": "p-wall"},
                event_seq=2, project_id="p-wall"))
            _world_seed_block(session, block, "world-w8-ledger-seed-0001")
            before = _files_fingerprint(notdnd_web.SAVE_DIR)

            # 缺省：粮账摘要（这个月的粮账）
            status, out = server.get("/api/world/ledger", sid=sid)
            assert status == 200 and out["status"] == "ok", (status, out)
            assert out["economy"]["day"] == 0, out
            assert out["economy"]["balances"]["coin"] == 1000, out
            assert out["economy"]["goods"]["grain"]["stock"] == 50, out
            assert out["economy"]["prices"]["grain"]["price"] == 2, out
            assert [e["kind"] for e in out["entries"]] == \
                ["produce", "build_pay"], out
            projects = {p["project_id"]: p for p in out["projects"]}
            assert projects["p-wall"]["status"] == "blocked", out
            assert projects["p-wall"]["blocked_reason"] == "supply", out

            # ?project_id=：这项工程为什么停
            status, out = server.get("/api/world/ledger?project_id=p-wall",
                                     sid=sid)
            assert status == 200 and out["project"]["status"] == "blocked", \
                (status, out)
            assert out["project"]["blocked_reason"] == "supply", out
            assert out["readiness"] == [], out
            assert [e["kind"] for e in out["entries"]] == ["build_pay"], out

            # 未知工程 → 404（不猜）
            status, out = server.get("/api/world/ledger?project_id=nope",
                                     sid=sid)
            assert status == 404 and out.get("reason") == "no_project", \
                (status, out)

            # 只读：全程不改盘（对照种块之后的状态指纹）
            after = _files_fingerprint(notdnd_web.SAVE_DIR)
            assert after == before, "观察面必须零状态变更"
    finally:
        notdnd_web._sessions.pop(sid, None)


def check_world_ledger_room_auth():
    """鉴权裁定（照 W3 `_world_host_gate` 同一模式，PR 登记）：账本是 GM /
    据点主人视角数据——单人会话 `X-Session` 即可；房间多名在座成员必须以
    **房主**成员令牌调用，player / observer 一律 403 `not_authorized`。"""
    with _Server(offline=True) as server:
        rid = "wroom-w8-ledger"
        status, host = server.post(
            "/api/room/create", {"room_id": rid, "display_name": "房主"})
        assert status == 200, (status, host)
        status, player = server.post(
            "/api/room/join", {"room_id": rid, "display_name": "玩家"})
        assert status == 200, (status, player)

        # 多人房间：只有 X-Session（无成员令牌）→ 401（鉴权链不过）。
        status, denied = server.get("/api/world/ledger", sid=rid)
        assert status == 401 and denied.get("reason") == "token_invalid", \
            (status, denied)

        # player 成员令牌 → 403（GM 视角数据不向 player 开）。
        status, denied = server.get(
            "/api/world/ledger", sid=rid,
            headers={"X-Member": player["member_id"],
                     "Authorization": "Bearer %s" % player["token"]})
        assert status == 403 and denied.get("reason") == "not_authorized", \
            (status, denied)

        # 房主成员令牌 → 鉴权通过（本房间无 world 块 → 404 no_world）。
        status, out = server.get(
            "/api/world/ledger", sid=rid,
            headers={"X-Member": host["member_id"],
                     "Authorization": "Bearer %s" % host["token"]})
        assert status == 404 and out.get("reason") == "no_world", \
            (status, out)


def check_world_soak_100_days():
    """W8 长跑（§10.3「长期模拟测试」行；确定性、无 AI、固定种子）：
    100 游戏日 × 20 NPC 纯规则推进（**5 游戏日一步**，N8 同款口径）→
    事件数 ≤ 日数 × 3；`blocks.world` 序列化 < 1 MiB（P3 实测点：
    > 512 KiB 须登记分片评估建议）；`replay` 相等；`blocks.guide.npc_at`
    重放 = live（D4 口径）；三等式照常成立；耗时如实登记进 PR
    （逐日 107s 为禁改路径既有成本，非 world 成本）。"""
    sid = "world-w8-soak"
    _seed_action_session(notdnd_web.SAVE_DIR, sid, action_id="move",
                         label="移动")
    session = notdnd_web.Session.load(sid)
    try:
        _neura_stub_world(session)
        nblock = neura.empty_block()
        nblock["campaign_seed"] = 20261018            # 固定种子：确定性
        for i in range(10):                           # 近场：market → gate 日程
            nblock["npcs"]["npc-near-%02d" % i] = _neura_mover_npc(
                npc_id="npc-near-%02d" % i)
        for i in range(10):                           # 远场：temple 常驻在岗
            far = neura.empty_npc("npc-far-%02d" % i, "sc-1")
            far["importance"] = 0.9
            far["location"]["place_id"] = "temple"
            far["schedule"] = [{"slot_id": "s1", "place_id": "temple",
                                "from_tod_minute": 0, "to_tod_minute": 1440}]
            nblock["npcs"]["npc-far-%02d" % i] = far
        _neura_seed_block(session, nblock, "world-w8-soak-neura-0001")

        wblock = world.new_block(
            goods=[{"good_id": "grain", "name": "黑麦", "category": "food",
                    "tier": 1, "base_price": 2, "stock": 400,
                    "produced_by": ["market"]}],
            day=0, balances={"coin": 100000, "labor": 1000})
        wblock["settlements"]["s1"] = {"place_id": "market", "population": 10}
        project = world.new_project(
            "p-soak", name="长墙", place_id="market",
            blueprint={"kind": "wall"},
            requirements={"goods": {"grain": 20}, "coin": 100,
                          "labor_days": 3},
            source="npc", wage_per_day=2, created_minute=0)
        # running + 里程碑到期：日切第 4 步推进进度并完工（HTTP 编排路径
        # 不注入 places，queued 背景工程自动开工未接线——W6 PR 登记口径，
        # 故长跑以 running 起步走「逐日推进 → 完工 → 维护扣账」链）。
        project["status"] = "running"
        project["milestones"][0]["at_minute"] = 10 * world.MINUTES_PER_DAY
        wblock["projects"]["p-soak"] = project
        _world_seed_block(session, wblock, "world-w8-soak-world-0001")

        started = time.time()
        for step in range(1, 21):                     # 100 游戏日：5 日一步
            out = notdnd_web._chron_advance(session, step * 5 * 1440)
            assert out["status"] == "ok", (step, out)
            assert out["stopped_at"] is None, (step, out)
        elapsed = time.time() - started

        handle = session._handle or session._open_handle()
        events = handle.events_since(0)
        assert len(events) <= 100 * 3, len(events)
        saved = _world_block_of(session)
        assert saved["economy"]["day"] == 100, saved["economy"]
        serialized = json.dumps(saved, ensure_ascii=False).encode("utf-8")
        assert len(serialized) < 1024 * 1024, len(serialized)
        assert world.verify_ledger(saved) == [], world.verify_ledger(saved)
        # 背景工程走完了日切通道（running → 逐日推进 → day 10 完工），
        # 完工设施进据点并从此按日扣维护费
        assert saved["projects"]["p-soak"]["status"] == "completed", \
            saved["projects"]["p-soak"]
        assert saved["settlements"]["s1"].get("facilities"), \
            saved["settlements"]["s1"]

        # 重放相等（§10.1 出口 2.8）+ D4 双写：guide.npc_at 重放 == live
        replayed = handle.replay()
        assert replayed["blocks"].get("world") == saved, "重放 world 块必须相等"
        live_at = session.guide.get("npc_at") \
            if isinstance(session.guide, dict) else {}
        assert len(live_at) == 10, live_at            # 10 个近场 NPC 各移动过
        replay_guide = replayed["blocks"].get("guide") or {}
        assert replay_guide.get("npc_at") == live_at, \
            (replay_guide.get("npc_at"), live_at)

        assert elapsed < 60.0, elapsed
    finally:
        notdnd_web._sessions.pop(sid, None)


CHECKS = (
    check_import_has_no_side_effects,
    check_roundtrip_snapshot,
    check_session_save_reports_failure,
    check_save_rename_failure_rolls_back,
    check_legacy_save_lazy_migration,
    check_state_wiring_new_layout,
    check_legacy_migration_on_first_save,
    check_legacy_migration_adds_chron_block,
    check_save_list_and_delete_new_layout,
    check_api_session_view,
    check_api_session_without_session_400,
    check_atlas_snapshot_roundtrip,
    check_atlas_legacy_save_lazy_compile,
    check_atlas_relocation_on_deleted_place,
    check_api_atlas_exits_and_move,
    check_atlas_move_save_failure_rolls_back,
    check_guide_block_wiring,
    check_guide_load_contract,
    check_guide_status_endpoint,
    check_guide_turn_validation,
    check_guide_turn_settle_stream,
    check_guide_turn_stream_failure,
    check_guide_turn_review_failure_keeps_verdict,
    check_guide_turn_settle_errors_before_stream,
    check_guide_turn_settle_save_failure,
    check_guide_missing_module,
    check_guide_tool_prepass_requests,
    check_guide_tool_prepass_rebuilds_l4,
    check_guide_tool_error_never_new_status,
    check_guide_tool_settle_save_failure,
    check_guide_narrative_tool_calls_no_second_round,
    check_guide_turn_writes_beats,
    check_guide_speak_validation,
    check_guide_speak_stream,
    check_guide_speak_rate_limit,
    check_guide_bind_endpoint,
    check_guide_bind_no_module,
    check_guide_turn_location_focus,
    check_guide_turn_focus_save_failure,
    check_guide_turn_traces_wiring,
    check_guide_turn_traces_betrayed,
    check_guide_turn_traces_offline,
    check_guide_turn_scene_change_traces,
    check_room_create_join_leave,
    check_room_create_save_failure,
    check_room_legacy_lazy_migration,
    check_room_capacity_full,
    check_room_kick_authorization,
    check_room_kick_host_check_is_atomic,
    check_room_auth_chain,
    check_room_stream_frames_and_filter,
    check_room_stream_t5_reconnect_backfill,
    check_room_chat_t6_tell_not_leaked,
    check_room_chat_reconnect_no_overreach,
    check_room_chat_channels_and_visibility_freeze,
    check_room_chat_errors,
    check_room_chat_secret_gate_per_member,
    check_room_action_t1_pickup_conflict,
    check_room_action_t3_same_key_replays,
    check_room_action_t4_new_key_stale_conflict,
    check_room_action_errors,
    check_room_pause_non_host_t8,
    check_room_pause_t9,
    check_room_pvp_t10,
    check_room_modes,
    check_room_chat_idempotency_and_base_seq,
    check_room_stream_poll_equivalence,
    check_room_stream_auth_and_since,
    check_room_stream_heartbeat_and_dead,
    check_room_stream_t13_slow_client,
    check_room_stream_live_push,
    check_session_create_endpoint,
    check_session_create_offline,
    check_session_create_reload,
    check_session_create_save_failure,
    check_campaign_catalog_and_bind,
    check_turn_log_wiring,
    check_turn_log_narration_beats,
    check_turn_log_offline_paths,
    check_turn_log_action_only,
    check_turn_log_subprocess,
    check_action_catalog_and_offline_round,
    check_action_subprocess_error_rows,
    check_action_nl_module_missing,
    check_action_nl_translation_and_rate,
    check_action_precondition_no_party,
    check_action_commit_errors_and_confirm,
    check_action_commit_combat_lock_and_server_error,
    check_action_schedule_hook,
    check_action_idempotency,
    check_action_narration_and_frozen_snapshot,
    check_action_narration_failure_keeps_result,
    check_action_save_failure_keeps_result,
    check_action_commit_json_accept,
    check_chron_hook_injection,
    check_chron_immediate_action_unchanged,
    check_chron_long_action_round,
    check_chron_long_action_barrier,
    check_chron_advance_barrier_stop,
    check_chron_cancel_mapping,
    check_chron_dependency_semantics,
    check_chron_dependency_chain,
    check_chron_next_barrier_legacy_readonly,
    check_chron_new_layout_guard_control,
    check_neura_submit_commit_and_replay,
    check_neura_submit_stale_seq_conflict,
    check_neura_submit_unsupported_no_event,
    check_neura_submit_rejected_zero_escape,
    check_action_settle_commit_source_default_player,
    check_neura_wired_import_and_tick_updates_block,
    check_neura_tick_failure_isolated,
    check_neura_barrier_skips_far_field,
    check_neura_concurrent_advance_no_duplicate_events,
    check_neura_restart_replay_identical,
    check_neura_empty_block_writes_nothing,
    check_neura_npc_at_dual_write_same_event,
    check_neura_npc_at_no_move_no_guide_change,
    check_neura_npc_at_idempotent_recommit,
    check_neura_npc_at_move_failed_not_written,
    check_neura_npc_at_replay_matches_live,
    check_neura_view_payload_gm_and_no_leak,
    check_neura_view_payload_room_member_visibility,
    check_neura_view_route_http,
    check_neura_soak_100_days,
    check_api_state_view,
    check_api_events_incremental,
    check_api_events_across_archives,
    check_api_state_readonly,
    check_api_state_legacy_readonly,
    check_world_generate_commit_round,
    check_world_generate_error_blocks_commit,
    check_world_generate_overrides_diff,
    check_world_generate_seed_determinism,
    check_world_generate_budget_and_port,
    check_world_generate_room_auth,
    check_world_export_import_round,
    check_world_export_disposition,
    check_world_wired_import_and_tick_updates_block,
    check_world_tick_failure_isolated,
    check_world_barrier_skips_day_cut,
    check_world_concurrent_advance_no_duplicate_events,
    check_world_absent_block_writes_nothing,
    check_econ_trade_commit_dual_ledger_http,
    check_econ_trade_insufficient_rejected_http,
    check_econ_build_start_commit_http,
    check_econ_build_rejected_no_conditions_http,
    check_econ_build_chron_card_completes_project,
    check_world_project_api_http,
    check_world_directive_api_and_explain_http,
    check_world_view_projection_keys,
    check_world_view_projection_defaults_without_data,
    check_world_rejudge_force_due_wired,
    check_world_rejudge_match_event_collects_candidates,
    check_world_ledger_view_http,
    check_world_ledger_room_auth,
    check_world_soak_100_days,
)


def main():
    failures = 0
    for check in CHECKS:
        try:
            check()
        except Exception as error:  # noqa: BLE001 — 一条断言崩了，后面的检查仍要跑
            print("  断言失败: %s: %s" % (type(error).__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(CHECKS) - failures, len(CHECKS), failures))
        return 1
    print("%d/%d 项通过" % (len(CHECKS), len(CHECKS)))
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_TMP, ignore_errors=True)     # 收摊：临时存档目录
    sys.exit(code)
