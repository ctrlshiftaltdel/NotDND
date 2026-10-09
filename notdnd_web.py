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
import json
import os
import pathlib
import re
import socket
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import prism_core

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
}


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
        }

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

    def _err(self, msg: str, code: int = 400):
        self._json({"error": msg}, code)

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

    # ── GET（只读）─────────────────────────────────────────
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                return self._static("index.html")
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

            # TODO(M3)：PRISM 会话 / 结算等写入接口在此追加（路径 if 链）。
            return self._err("未知接口", 404)
        except ValueError as e:
            self._err(str(e), 400)
        except Exception as e:  # noqa: BLE001
            self._err(f"服务器内部错误：{e}", 500)


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
