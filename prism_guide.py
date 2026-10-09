#!/usr/bin/env python3
"""AI 导引者 · 标准库客户端、前缀、叙事回合与 pcm16 语音代理（G1 / G2 / G3）。

设计依据：GUIDE-DESIGN.md（§2.2 角色行为与对白 / §4.3 已拍板 / §5.1 模块边界 /
§5.2 回合怎么走 / §5.3 环境 / §5.4 缓存导向的提示词 / §5.5 思考策略 / §5.6 工具 /
§5.9 语音管线 / §5.10 我们自己的 HTTP / §7 数据模型 / §10 可观测性 /
PR Plan G1–G3）。

G1（前缀与离线骨架）：

- `.env` 只解析 `BASE_URL` / `MODEL` / `API_KEY` 三个键，`os.environ` 优先；
- URL 拼接（API 根 + `/chat/completions`，容忍末尾斜杠与完整路径）；
- 叙事请求体（思考关闭、温度 0.7、700 token、§5.6 工具 JSON，密钥不入体）；
- L0 / L1 / L2 的常量与构建（产品路径不读 `data/scenarios/` 与 `data/worlds/`）；
- `guide` 状态字典（`empty_guide` / `guide_from`，§7 按键合同）；
- `cache_hit_ratio` 纯函数。

G2（回合）：

- `settle`：加锁 → `from_snapshot` → `perform_action`（不传 `unit_id`）→ 写回
  → `save()` → 解锁（§5.2）。不 import `notdnd_web`；
- 传输层 `TRANSMIT`：所有上游调用只走这一个入口，测试可换成**假传输**；
  真实实现是标准库 HTTP POST + SSE 组装（§5.3 / §5.10）；
- L0–L4 叙事消息数组（§5.4）与 L4 的本回合块；
- 骰子审查与 `【裁决】` 替换：记法 / 合计 / 带标签数字对不上就丢叙事（§5.2）。

G3（pcm16 语音代理，无新前端）：

- 切拍（§5.9）：整段叙事默认是**一拍旁白**；「名字（1–8 个汉字）＋冒号＋「…」」
  切出对白拍；一回合最多 6 拍，多出来的并进最后一拍旁白。**不按姓名推断音色**：
  无显式说话人只用白桦（按 `npc.id` 分配声线是 G7）；
- 平叙风格卡与 TTS 请求体（§5.9）：`mimo-v2.5-tts`、`stream` 为 true、台词在
  `assistant`、`user` 是常量「平叙」（不含台词 / `sid` / 日期）、`audio.format`
  为 `pcm16`、`voice` 为白桦或茉莉；送去合成之前删掉长度 1–12 的括号 / 方括号记号；
- 音频组装（§4.3 / Key Decision 11）：对每一个 `delta.audio.data` 非空的块
  **单独** base64 解码再拼接 PCM——禁止把 base64 文本接成一串再解码；
  `choices` 为空的块只读用量；
- `call_tts` 与叙事走**同一条**可替换传输入口 `TRANSMIT`（TTS 也是 Chat
  Completions），离线 / 失败返回空字节，**不重试**（§5.9）。

密钥只放请求头 `api-key`，不进 URL、不进 JSON、不进状态字典、不进日志；
异常字符串不携带上游响应体（§5.1 / §8）。
"""

import base64
import json
import os
import re
import secrets
import urllib.request

import prism_core

# ── 常量 ────────────────────────────────────────────────────────────────

# 模块缺失 / 上游失败时的兜底句（§5.1）。模块级常量，不向模型现编。
FALLBACK_NARRATION = "导引者这会儿不在席。刚才的规则结果已经生效，请按桌上的判定继续。"

# 密钥只放这个请求头（§4.3 已拍板）。
KEY_HEADER = "api-key"

# 叙事请求参数（§5.5 表：叙事行）。
NARRATIVE_TEMPERATURE = 0.7
NARRATIVE_MAX_TOKENS = 700

# 语音模型是代码常量，不是第四个环境变量（§4.3 已拍板）。
TTS_MODEL = "mimo-v2.5-tts"

# ── §5.9 语音管线常量 ───────────────────────────────────────────────────
#
# 音色只用这两个（§4.3 已拍板）。G3 的**无显式说话人**一律白桦：按 `npc.id`
# 分配声线是 G7 的事，本切片不推断姓名，也不给 NPC 加字段。
VOICE_NARRATOR = "白桦"
VOICE_ALT = "茉莉"
VOICES = (VOICE_NARRATOR, VOICE_ALT)

# 产品路径的风格卡（§5.9）：常量「平叙」，不含台词、不含 `sid`、不含日期。
# 它是 TTS 请求里那条稳定的 `user` 消息，应当命中前缀缓存。
TTS_STYLE_CARD = "平叙"

# 一回合最多几拍；多出来的并进最后一拍旁白（§5.9）。
MAX_BEATS = 6

# 音频规格（§4.3）：24 kHz、单声道、s16le（PCM16LE）。HTTP 头回传这两个值。
TTS_SAMPLE_RATE = 24000
TTS_FORMAT = "pcm16"

# 单拍 TTS 的时间盒（§5.11：「首包 8 秒 / 总长 20 秒」）。不占叙事的 45 秒。
TTS_TIMEOUT_S = 20.0

# 节拍的 `tone` / `channel` 取值：设计文档只钉了元素键名（§5.8 的
# `{text, voice, tone, channel}`），没钉取值。这里取最小闭包：
# `tone` 就是产品路径的风格卡，「平叙」；G3 只有朗读这一条通道。
BEAT_TONE = TTS_STYLE_CARD
BEAT_CHANNEL = "speech"

# 叙事段的上游时间盒：§5.11 表「叙事 …… 最多 30 秒，且在回合 45 秒的叙事段之内」。
NARRATIVE_TIMEOUT_S = 30.0

# L4（本回合）的字符预算（§5.11：2_000 字）。
L4_LIMIT = 2000

# 环境变量三元组（§5.3）：旧名 NOTDND_AI_* 一律不认。
ENV_KEYS = ("BASE_URL", "MODEL", "API_KEY")

_CHAT_PATH = "/chat/completions"
_SALT_RE = re.compile(r"^[0-9a-f]{32}$")

# ── §5.3 环境 ───────────────────────────────────────────────────────────


def parse_env(text, environ=None):
    """解析 `.env` 文本，只写入三个键；`os.environ` 里已有的值优先。

    未知键一律忽略（含旧名 `NOTDND_AI_*`）。行内去首尾空白，忽略空行与
    `#` 注释行。返回的新字典不含任何其他键。
    """
    env = {key: "" for key in ENV_KEYS}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key in env:
            env[key] = value
    # 已在 os.environ 里的值优先，.env 不覆盖（§5.3）。
    for key in ENV_KEYS:
        if environ is None:
            environ = os.environ
        if key in environ:
            env[key] = str(environ[key] or "")
    return env


def load_env(path=".env"):
    """读取 `.env` 并与 `os.environ` 合并；文件缺失或读不了时静默走环境。

    错误信息不打印绝对路径，也不打印文件内容（§5.3 / 铁律 3）。
    """
    text = ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        text = ""
    return parse_env(text)


def chat_url(base_url):
    """由 API 根拼接聊天补全 URL（§5.3）。

    去掉一个末尾斜杠；调用方已写完整路径时先剥掉再拼接。
    空白输入返回空串。
    """
    base = (base_url or "").strip()
    if not base:
        return ""
    if base.endswith("/"):
        base = base[:-1]
    if base.endswith(_CHAT_PATH):
        base = base[: -len(_CHAT_PATH)]
        if base.endswith("/"):
            base = base[:-1]
    return base + _CHAT_PATH


def status(env=None):
    """三个布尔（§5.3）：`chat` / `tts` / `configured`。

    `chat` 要 BASE_URL、MODEL、API_KEY 都非空；`tts` 不看 MODEL；
    `configured` 是二者之或。BASE_URL 或密钥为空白时三个布尔都是假。
    状态体永不包含密钥、密钥长度、BASE_URL、请求头。
    """
    env = env if env is not None else load_env()
    base_ok = bool((env.get("BASE_URL") or "").strip()) and bool(
        (env.get("API_KEY") or "").strip())
    chat = base_ok and bool((env.get("MODEL") or "").strip())
    return {"chat": chat, "tts": base_ok, "configured": chat or base_ok}


# ── §5.6 工具 ───────────────────────────────────────────────────────────

# 三只工具的规范串（§5.6，713 字符）。顺序与 schema 字节在所有叙事与
# 预通行请求里相同；测试断言与 GUIDE-DESIGN.md §5.6 的规范串全等。
TOOLS_JSON = '[{"function":{"description":"按 kind 读取一条规则摘要。不改状态，不掷骰。","name":"lookup_rule","parameters":{"properties":{"kind":{"enum":["system.adjudication","system.antipatterns","system.decision_engine","system.guardrails","system.ledger_spec","system.tone_packs"],"type":"string"}},"required":["kind"],"type":"object"}},"type":"function"},{"function":{"description":"读取当前地点的名称、出口与一句尺度。没有地图则 available 为 false。","name":"read_place_card","parameters":{"properties":{},"required":[],"type":"object"}},"type":"function"},{"function":{"description":"结算场景里已有的行动。只传 action_id。骰点由规则核心产生。","name":"request_check","parameters":{"properties":{"action_id":{"type":"string"}},"required":["action_id"],"type":"object"}},"type":"function"}]'

TOOLS = json.loads(TOOLS_JSON)


def tools_json():
    """返回工具数组的规范序列化（与 TOOLS_JSON 全等）。"""
    return json.dumps(TOOLS, ensure_ascii=False, separators=(",", ":"))


# ── 叙事请求体（§5.5 叙事行：思考关、0.7、700、流式、带工具） ────────────


def build_narrative_body(messages, model=None, env=None):
    """构造叙事请求体。密钥不入体：调用方只把它放进请求头 `api-key`。"""
    env = env if env is not None else load_env()
    resolved = model if model is not None else (env.get("MODEL") or "").strip()
    return {
        "model": str(resolved),
        "messages": list(messages),
        "tools": TOOLS,
        "thinking": {"type": "disabled"},
        "temperature": NARRATIVE_TEMPERATURE,
        "max_completion_tokens": NARRATIVE_MAX_TOKENS,
        "stream": True,
    }


# ── §5.4 缓存导向的提示词（G1：L0 / L1 / L2） ────────────────────────────

# L0：一条 system，构建期宪法。无时钟、无 sid、无日期、无 salt、无战役名，
# 不粘贴 docs/，不含某一场剧本的地点专名。只随发版变化。
L0 = """你是「导引者」，主持一场纯文本跑团冒险。规则判定一律由规则核心给出，你负责把结果演成场面。

每次回复严格使用三段标题：
【裁决】一行，概述本回合的判定结果；
【叙事】一至四段，描写行动的后果与场面变化；
【钩子】一行，留下一个玩家可以追下去的钩子。

不替玩家做主：不描写玩家的决定、心思或对白，只呈现世界如何回应。
失败必须改变局势：判定失败时局面要真的变坏，不许用「有惊无险」收场。
裁决会被换掉：【裁决】由服务端按规则核心的掷骰结果重写，不要在叙事里另编数字或结果。
淡出边界：玩家要求淡出或跳过时，时间直接过去，不描写被略过的内容。
不输出表演记号：不用舞台指示、镜头语言、括号动作或旁白标记。

不得改写典范专名，不得用近一字的新名替换地点名或要点名。
不主动说出秘密。面具是说出来的那一面，欲望不是。
不在场的事只留痕迹，不写场外独白。"""

# L0 的字符预算（§5.11：1_500 字）。
L0_LIMIT = 1500

# L1 未绑定常量（§5.4）：没有绑定的会话，L1 就是这一句。
# G1 的产品路径不读剧本目录，绑定属 G5。
L1_UNBOUND = "【世界卡】\n尚未选择战役\n"

# L2 末行固定（§5.4 / §5.12：ledger_spec 不用来填 L2）。
L2_TAIL = "账本：无"

# 六维展示名（§5.4 检查点形状；与 prism_core 的 ATTRIBUTES 对应）。
_ATTR_LABELS = (
    ("MGT", "力道"),
    ("FIN", "灵巧"),
    ("VIG", "体魄"),
    ("INS", "洞察"),
    ("MND", "心智"),
    ("PRE", "气场"),
)


def build_l2(rules):
    """由规则快照构建检查点（L2）。

    未提交过实相时不含 salt、声线与实相摘要（G1 永未提交）；
    队员为空则整段是 `【检查点】\\n账本：无\\n`。纯函数：同一输入
    两次构建字节相同。
    """
    rules = rules or {}
    lines = ["【检查点】"]
    for unit in rules.get("party") or []:
        if not isinstance(unit, dict):
            continue
        attrs = unit.get("attributes") or {}
        fields = [
            str(unit.get("id") or ""),
            str(unit.get("name") or ""),
            "等级 " + str(int(unit.get("level") or 1)),
        ]
        for attr_id, label in _ATTR_LABELS:
            try:
                value = int(attrs.get(attr_id) or 0)
            except (TypeError, ValueError):
                value = 0
            fields.append(label + " " + str(value))
        lines.append("队员：" + " / ".join(fields))
    lines.append(L2_TAIL)
    return "\n".join(lines) + "\n"


def ensure_l2(guide, rules):
    """`scene.id` 变化时重写 L2 并清空 L3（transcript）；否则原样复用。

    只改等级或六维、不改 `scene.id` 时，L2 原样复用（§5.4）。
    返回当前 L2 文本。
    """
    scene = (rules or {}).get("scene") or {}
    scene_id = str(scene.get("id") or "")
    if guide.get("l2_scene_id") != scene_id:
        guide["l2_scene_id"] = scene_id
        guide["l2"] = build_l2(rules)
        guide["transcript"] = []
    return guide["l2"]


# ── §7 guide 状态字典 ───────────────────────────────────────────────────

# stats 计数键（§7）。只在这些键上累加，不夹带别的东西。
STAT_KEYS = (
    "calls",
    "prompt_tokens",
    "cached_tokens",
    "completion_tokens",
    "reasoning_tokens",
    "narration_rejected",
    "fallbacks",
    "realization_fallbacks",
    "nodes_dropped",
    "speech_rejected",
)

_STR_KEYS = ("l1_key", "l2_scene_id", "l2", "world_key", "scenario_id",
             "focus_location_id", "here")
_LIST_KEYS = ("transcript", "last_beats", "traces", "known_clues")
_DICT_KEYS = ("realizations", "voices", "npc_home", "npc_at", "npc_flags")


def empty_guide():
    """零参工厂：新建一份 guide 状态字典（§7 形状，salt 新掷）。

    每次调用都生成新 salt，禁止多个会话共享同一个 dict。
    不含密钥，不含 BASE_URL。
    """
    return {
        "salt": secrets.token_hex(16),
        "l1_key": "unloaded",
        "l2_scene_id": "",
        "l2": "【检查点】\n" + L2_TAIL + "\n",
        "transcript": [],
        "last_beats": [],
        "world_key": "",
        "scenario_id": "",
        "focus_location_id": "",
        "here": "",
        "realizations": {},
        "voices": {},
        "traces": [],
        "npc_home": {},
        "npc_at": {},
        "npc_flags": {},
        "known_clues": [],
        "stats": {key: 0 for key in STAT_KEYS},
    }


def guide_from(raw):
    """按键还原 guide（§7 加载合同）：不因旁边一个键脏了就整块退回。

    - `raw` 不是 dict：若本身是 32 位十六进制就当 salt 用（不另掷），
      否则换成 `empty_guide()`。
    - `raw` 是 dict：只丢掉类型不对的键和不认识的键。salt 已是 32 位
      十六进制则原样保留；缺失或不合法才用新掷的那一枚补上。
    """
    guide = empty_guide()
    if not isinstance(raw, dict):
        if isinstance(raw, str) and _SALT_RE.match(raw):
            guide["salt"] = raw
        return guide
    salt = raw.get("salt")
    if isinstance(salt, str) and _SALT_RE.match(salt):
        guide["salt"] = salt
    for key in _STR_KEYS:
        if isinstance(raw.get(key), str):
            guide[key] = raw[key]
    for key in _LIST_KEYS:
        if isinstance(raw.get(key), list):
            guide[key] = list(raw[key])
    for key in _DICT_KEYS:
        if isinstance(raw.get(key), dict):
            guide[key] = dict(raw[key])
    stats = raw.get("stats")
    if isinstance(stats, dict):
        for key in STAT_KEYS:
            value = stats.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                guide["stats"][key] = value
    return guide


# ── §10 可观测性 ────────────────────────────────────────────────────────


def cache_hit_ratio(prompt_tokens, cached_tokens):
    """前缀缓存命中率。分母 ≤ 0 时返回 0.0；不访问网络。"""
    try:
        prompt = int(prompt_tokens)
        cached = int(cached_tokens)
    except (TypeError, ValueError):
        return 0.0
    if prompt <= 0 or cached <= 0:
        return 0.0
    return cached / prompt


# ════════════════════════════════════════════════════════════════════════
# §5.2 回合 · 先结算、后叙事
# ════════════════════════════════════════════════════════════════════════


class TransportError(Exception):
    """上游调用失败（网络 / 超时 / 读不到响应体）。

    ⚠️ 刻意**不携带**上游响应体 / 密钥：异常字符串会被写进日志与兜底路径。
    """


def settle(web_session, action_id, rng=None):
    """回合开头的结算（§5.2）：加锁 → 注水 → 结算 → 写回 → `save()` → 解锁。

    不 import `notdnd_web`（只按鸭子类型用 `lock` / `rules` / `save`），
    不传 `unit_id`——行动者是 `active_unit_id`，空则 `party[0]`。
    状态行写出之前的 `ValueError` 由网页层变成固定 JSON；这里**照原样抛**，
    因为「哪种失败对应哪个状态码」是网页层的合同。
    """
    with web_session.lock:
        live = prism_core.RuleSession.from_snapshot(web_session.rules)
        result = prism_core.perform_action(live, action_id, rng=rng)
        web_session.rules = live.snapshot()
        web_session.save()
    return result


# ── 传输层（§5.3 / §5.10）：所有上游调用只走这一个入口 ──────────────────


def _int_value(value):
    """脏值降级：转不成 int 就用 0。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def read_usage(raw):
    """用量四元组（§6）：缺字段按 0，不猜别名。"""
    raw = raw if isinstance(raw, dict) else {}
    prompt_details = raw.get("prompt_tokens_details") or {}
    completion_details = raw.get("completion_tokens_details") or {}
    return {
        "prompt_tokens": _int_value(raw.get("prompt_tokens")),
        "cached_tokens": _int_value(
            (prompt_details or {}).get("cached_tokens")),
        "completion_tokens": _int_value(raw.get("completion_tokens")),
        "reasoning_tokens": _int_value(
            (completion_details or {}).get("reasoning_tokens")),
    }


def _b64_pcm(data):
    """把一个音频 delta 的 base64 块解码成 PCM 字节。

    **每块单独解码**（§5.9 / Key Decision 11）：`data` 自带填充，合法时读
    多少字节就是多少字节。缺填充也能容忍（补到 4 的倍数再解）；坏块返回
    空字节——一个坏音频块不该把整拍变成失败。
    """
    if not isinstance(data, str) or not data:
        return b""
    text = data.strip()
    if not text:
        return b""
    try:
        return base64.b64decode(text + "=" * (-len(text) % 4))
    except Exception:  # noqa: BLE001 — 坏块跳过，不抛
        return b""


def assemble_chat_stream(raw):
    """把 Chat Completions 的响应体组装成 `{content, tool_calls, usage[, audio]}`。

    流式（`delta`）与非流式（`message`）回包都认：只读 `data:` 行，
    `[DONE]` 结束，畸形块跳过不抛（一次坏块不该把整回合变成兜底）。
    `reasoning_content` 刻意不收集——它不进存档，也不进 L3（§5.4）。

    `audio` 是 TTS（§5.9）的 PCM，**只在这条流真的带过音频块时才出现**，
    值是 `bytes`；所以纯叙事回包仍是三键、JSON 可序列化的字典，
    而带音频的回包由 `call_tts` 消费。每个 `delta.audio.data` 非空的块
    **单独** base64 解码后按到达顺序拼起来；`choices` 为空的块只读用量。
    """
    content_parts: list[str] = []
    tool_calls: list = []
    usage: dict = {}
    audio = bytearray()
    for line in (raw or b"").decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        chunk = line[5:].strip()
        if not chunk or chunk == "[DONE]":
            continue
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        choices = obj.get("choices") or []
        first = choices[0] if choices and isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        message = (first.get("message")
                   if isinstance(first.get("message"), dict) else {})
        for piece_holder in (delta, message):
            piece = piece_holder.get("content")
            if isinstance(piece, str):
                content_parts.append(piece)
            calls = piece_holder.get("tool_calls")
            if isinstance(calls, list):
                tool_calls.extend(calls)
            sound = piece_holder.get("audio")
            if isinstance(sound, dict):
                audio.extend(_b64_pcm(sound.get("data")))
        if isinstance(obj.get("usage"), dict):
            usage = read_usage(obj["usage"])
    out = {"content": "".join(content_parts), "tool_calls": tool_calls,
           "usage": usage}
    if audio:
        out["audio"] = bytes(audio)
    return out


def http_transmit(url, payload, headers, *, timeout):
    """真实传输：标准库 HTTP POST，返回 `{content, tool_calls, usage}`。

    密钥只在这个请求头（`api-key`），不进 URL、不进 JSON、不进日志。
    失败抛 `TransportError`，**不带**上游响应体与密钥。
    """
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(str(key), str(value))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except Exception as exc:  # noqa: BLE001 — 只留异常类型名，不回显上游内容
        raise TransportError(type(exc).__name__) from None
    return assemble_chat_stream(raw)


# 可替换的传输层：签名 `(url, payload, headers, *, timeout) -> dict`。
# 测试把它换成**假传输**（记录请求体、返回夹带或抛错），全程不开套接字。
TRANSMIT = http_transmit


def call_narrative(messages, model=None, env=None, transmit=None):
    """经传输层取一次叙事完成（§5.2）。

    离线（空 URL / 空模型 / 空密钥）时**不请求**，直接返回 None——
    「没有密钥 …… 时，对局继续，浏览器只看到固定兜底」（§1）。
    """
    env = env if env is not None else load_env()
    url = chat_url(env.get("BASE_URL"))
    key = (env.get("API_KEY") or "").strip()
    resolved = model if model is not None else (env.get("MODEL") or "").strip()
    if not url or not key or not str(resolved or "").strip():
        return None
    body = build_narrative_body(messages, model=resolved, env=env)
    sender = transmit if transmit is not None else TRANSMIT
    try:
        got = sender(url, body, {KEY_HEADER: key},
                     timeout=NARRATIVE_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — 上游失败一律走兜底，不把原文回给浏览器
        return None
    if not isinstance(got, dict):
        return None
    got.setdefault("content", "")
    got.setdefault("tool_calls", [])
    got.setdefault("usage", {})
    return got


# ════════════════════════════════════════════════════════════════════════
# §5.9 语音管线 · 切拍 → 平叙风格卡 → pcm16
# ════════════════════════════════════════════════════════════════════════


# 对白拍的记号：「名字（1–8 个汉字）＋冒号＋「……」」（§5.9）。
_BEAT_DIALOGUE_RE = re.compile(r"([\u4e00-\u9fff]{1,8})[：:]\s*「([^」]*)」")

# 送去合成之前要删掉的记号：长度 1–12 的括号与方括号（§5.9）。
# 刻意**不含** `【】`：三段标题 `【裁决】`/`【叙事】`/`【钩子】` 是叙事合同，
# 不是表演记号。
_MARK_RE = re.compile(
    r"[（(][^（()）]{1,12}[)）]|\[[^\[\]]{1,12}\]|［[^［］]{1,12}］")


def strip_marks(line):
    """删掉送去合成前的括号 / 方括号记号（§5.9）。不改 `last_beats` 里的字。"""
    return _MARK_RE.sub("", str(line or ""))


def narration_beat(text, voice=VOICE_NARRATOR, speaker=""):
    """一拍旁白（或未标记的对白）：`{text, voice, tone, channel, speaker}`。

    `speaker` 是**附加**键：§5.9 要求对白拍带上说话人 id（G7 还要用它写
    `npc_at`），而元素键名表 `{text, voice, tone, channel}` 里没有它。
    """
    return {"text": str(text or ""), "voice": voice, "tone": BEAT_TONE,
            "channel": BEAT_CHANNEL, "speaker": str(speaker or "")}


def split_beats(text, *, speakers=None, voices=None, limit=MAX_BEATS):
    """把一段（已涂掉秘密的）正文切成节拍（§5.9）。

    - 默认整段是**一拍旁白**，音色白桦；
    - 「名字（1–8 个汉字）＋冒号＋「…」」切出对白拍；切不出就保持旁白；
    - `speakers` 是「名字 → 说话人 id」（在场 NPC / 路人），G3 的调用方
      不传——所以对白拍一律 `speaker=""`、音色白桦，**不按姓名推断**；
      传了映射时，`speaker` 用那个 id，音色取 `voices[id]`（没记录则白桦）；
    - 一回合最多 `limit` 拍；多出来的并进最后一拍旁白；
    - 纯函数：不改 `text`、不改 `voices`。空文本返回空列表。
    """
    text = str(text or "")
    if not text.strip():
        return []
    speakers = speakers if isinstance(speakers, dict) else {}
    voices = voices if isinstance(voices, dict) else {}

    beats = []
    cursor = 0
    for match in _BEAT_DIALOGUE_RE.finditer(text):
        head = text[cursor:match.start()]
        if head.strip():
            beats.append(narration_beat(head))
        name, line = match.group(1), match.group(2)
        speaker = str(speakers.get(name) or "")
        voice = str(voices.get(speaker) or "") if speaker else ""
        if voice not in VOICES:
            voice = VOICE_NARRATOR
        beats.append(narration_beat(line, voice=voice, speaker=speaker))
        cursor = match.end()
    tail = text[cursor:]
    if tail.strip():
        beats.append(narration_beat(tail))

    if not beats:
        beats = [narration_beat(text)]
    if limit and len(beats) > limit:
        # 多出来的并进最后一拍旁白：前 limit-1 拍照留，剩下的一律合成
        # 一拍白桦旁白——文本一字不丢，音色不再按原来的说话人猜。
        merged = "".join(item["text"] for item in beats[limit - 1:])
        beats = beats[:limit - 1] + [narration_beat(merged)]
    return beats


def beats_of(narration, guide=None):
    """从玩家可见的整段叙事里切出这一回合的节拍（§5.9）。

    `guide` 只用来取 `voices`（G3 里恒为空）与 `npc_at` / 剧本名字表
    （G5 起才有）。**裁决那一行不朗读**：`【裁决】` 是服务端按掷骰重写的
    机械句，这里只切「正文 + 钩子」——与 §5.8 对「玩家将要看到的整段
    （正文和钩子）」的划法一致。
    """
    parts = sections(narration)
    body = parts["narration"].strip()
    hook = parts["hook"].strip()
    spoken = "\n\n".join(item for item in (body, hook) if item)
    if not spoken:
        # 模型没照三段格式写（`sections` 把整段当正文）时的兜底路径。
        spoken = str(narration or "").strip()
    voices = (guide or {}).get("voices") if isinstance(guide, dict) else None
    return split_beats(spoken, voices=voices)


# ── TTS 请求体与调用（§5.9）──────────────────────────────────────────────


def build_tts_body(line, voice=VOICE_NARRATOR, style=TTS_STYLE_CARD):
    """TTS 请求体（§5.9）：台词在 `assistant`，`user` 是稳定风格卡。

    形状：`model` = `mimo-v2.5-tts`、`stream` 为 true、`audio.format` 为
    `pcm16`、`voice` 为白桦或茉莉。密钥不入体（调用方只放进请求头
    `api-key`）。`tone` 为「平叙」时 `style` 就是那条常量卡。
    """
    if voice not in VOICES:
        voice = VOICE_NARRATOR
    return {
        "model": TTS_MODEL,
        "messages": [
            {"role": "user", "content": str(style or TTS_STYLE_CARD)},
            {"role": "assistant", "content": strip_marks(line)},
        ],
        "audio": {"format": TTS_FORMAT},
        "voice": voice,
        "stream": True,
    }


def call_tts(line, voice=VOICE_NARRATOR, env=None, transmit=None):
    """经传输层把一段已定稿的台词合成成 pcm16 字节（§5.9）。

    与叙事**同一条**可替换入口 `TRANSMIT`（TTS 也是 Chat Completions）。
    离线（空 `BASE_URL` / 空密钥）或上游失败时返回空字节，**不重试**——
    「TTS 失败不重试。文本已经通过叙事 SSE 给过浏览器」（§5.9）。
    只看 `tts` 那两个键：TTS 不需要 `MODEL`（§5.3）。
    """
    env = env if env is not None else load_env()
    url = chat_url(env.get("BASE_URL"))
    key = (env.get("API_KEY") or "").strip()
    if not url or not key:
        return b""
    body = build_tts_body(line, voice=voice)
    sender = transmit if transmit is not None else TRANSMIT
    try:
        got = sender(url, body, {KEY_HEADER: key}, timeout=TTS_TIMEOUT_S)
    except Exception:  # noqa: BLE001 — 上游失败不回显、不重试
        return b""
    if not isinstance(got, dict):
        return b""
    audio = got.get("audio")
    if isinstance(audio, (bytes, bytearray)):
        return bytes(audio)
    return b""


# ── 【裁决】与骰子审查（§5.2）────────────────────────────────────────────

HEADING_VERDICT = "【裁决】"
HEADING_NARRATION = "【叙事】"
HEADING_HOOK = "【钩子】"

# 五档中文写在这里，**不**导入 `prism_core._OUTCOME_ZH`（§5.2 Key Decision 10）。
OUTCOME_LABELS = {
    "triumph": "凯旋",
    "success": "成功",
    "narrow": "险成",
    "failure": "挫败",
    "catastrophe": "灾难",
}

# 没有可换的掷骰时（如纯叙述行动）的裁决句。
NO_ROLL_VERDICT = "本回合没有新的掷骰。"

# 带标签的数字 → 快照里的字段 / 资源池（§5.2 最后一段）。
_LABELED_UNITS = (
    ("活力", "vitality"),
    ("防护", "guard"),
    ("韧性", "poise"),
    ("专注", "focus"),
    ("气势", "tempo"),
    ("负担", "strain"),
    ("决意", "resolve"),
)

# `掷出` / `骰出` / `合计` 后面那一段：可能是一条记法，也可能是一个整数。
# 记法分支写在前：`1d20+5` 必须整段当记法比，不能被整数分支切成 `1`。
_TOTAL_TOKEN_RE = re.compile(
    r"(?:掷出|骰出|合计)\s*[:：]?\s*"
    r"([0-9]{0,3}d[0-9]{1,3}(?:[+-][0-9]{1,3})?|[0-9]+)", re.IGNORECASE)


def verdict_line(result):
    """`【裁决】` 的服务端句子，**无论叙事过不过都换掉**（§5.2 取句顺序）。

    1. `rolled` 为真且 `result["roll"]` 是字典 → `roll["detail"]`；
    2. 否则顶层 `message`；3. 否则顶层 `text`；
    4. 否则 `outcome` 属于 `prism_core.OUTCOMES` → `判定：` + 中文档位；
    5. 否则 `本回合没有新的掷骰。`。
    """
    result = result if isinstance(result, dict) else {}
    roll = result.get("roll")
    if result.get("rolled") and isinstance(roll, dict):
        detail = roll.get("detail")
        if detail:
            return str(detail)
    if result.get("message"):
        return str(result["message"])
    if result.get("text"):
        return str(result["text"])
    outcome = str(result.get("outcome") or "")
    if outcome in prism_core.OUTCOMES:
        return "判定：" + OUTCOME_LABELS.get(outcome, outcome)
    return NO_ROLL_VERDICT


def _labeled_value(unit, label, field):
    """带标签数字在快照里的期望值；取不到（字段缺失）返回 None。"""
    unit = unit if isinstance(unit, dict) else {}
    if field in ("vitality", "guard", "poise"):
        value = unit.get(field)
    else:
        value = (unit.get("resources") or {}).get(field)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def notation_ok(text, result):
    """骰子审查（§5.2）：对不上就丢叙事，**不改数字、不再请求**。

    - 记法必须与 `roll["notation"]` 整段全等（`掷出` / `骰出` / `合计`
      后面带记法时；禁止无锚点搜索）；
    - `掷出` / `骰出` / `合计` 后面的整数必须是 `roll["total"]`；
    - 带标签的活力 / 防护 / 韧性 / 专注 / 气势 / 负担 / 决意必须对上
      `result["unit"]`（单位里没有这个字段时不判死，只跳过）。
    """
    text = str(text or "")
    result = result if isinstance(result, dict) else {}
    roll = result.get("roll")
    roll = roll if isinstance(roll, dict) else {}
    notation = str(roll.get("notation") or "")
    total = roll.get("total")
    unit = result.get("unit")

    for match in _TOTAL_TOKEN_RE.finditer(text):
        token = match.group(1)
        if "d" in token.lower():
            if token.lower() != notation.lower():
                return False
        elif total is None or _int_value(token) != _int_value(total):
            return False
    for label, field in _LABELED_UNITS:
        pattern = re.compile(re.escape(label) + r"\s*[:：]?\s*([0-9]+)")
        for match in pattern.finditer(text):
            expected = _labeled_value(unit, label, field)
            if expected is None:
                continue
            if _int_value(match.group(1)) != expected:
                return False
    return True


def sections(text):
    """按三段标题切分模型输出。

    缺标题时把整段当叙事（模型不照格式写时仍有正文可用）；标题顺序按
    在正文里出现的位置，不按硬编码顺序。
    """
    text = str(text or "")
    marks = ((HEADING_VERDICT, "verdict"), (HEADING_NARRATION, "narration"),
             (HEADING_HOOK, "hook"))
    found = []
    for mark, key in marks:
        index = text.find(mark)
        if index >= 0:
            found.append((index, key, mark))
    found.sort()
    out = {"verdict": "", "narration": "", "hook": ""}
    if not found:
        out["narration"] = text.strip()
        return out
    for position, (index, key, mark) in enumerate(found):
        start = index + len(mark)
        end = found[position + 1][0] if position + 1 < len(found) else len(text)
        out[key] = text[start:end].strip()
    return out


def compose_narration(result, body, hook=""):
    """拼出玩家可见的整段：服务端裁决 + 正文（+ 可选钩子）。"""
    parts = [HEADING_VERDICT + verdict_line(result),
             HEADING_NARRATION + str(body)]
    if str(hook or "").strip():
        parts.append(HEADING_HOOK + str(hook).strip())
    return "\n\n".join(parts)


def fallback_text(result):
    """叙事失败时的正文：**保留服务端裁决**，正文用兜底句（§5.2）。"""
    return compose_narration(result, FALLBACK_NARRATION)


def review_narration(result, completion):
    """审查一次叙事完成；通过返回最终整段，不通过返回 None。

    不通过的四种情形（§5.2）：完成不是字典、带非空 `tool_calls`、
    去掉空白后没有正文、骰子记法 / 合计 / 带标签数字对不上。
    """
    if not isinstance(completion, dict):
        return None
    if completion.get("tool_calls"):
        return None
    raw = str(completion.get("content") or "")
    if not raw.strip():
        return None
    if not notation_ok(raw, result):
        return None
    parts = sections(raw)
    body = parts["narration"].strip()
    if not body:
        return None
    return compose_narration(result, body, parts["hook"])


# ── §5.4 L4：本回合块 ────────────────────────────────────────────────────


def _active_unit(rules):
    """当前行动角色：`active_unit_id`，空则 `party[0]`（与规则核心同口径）。"""
    rules = rules if isinstance(rules, dict) else {}
    party = rules.get("party") or []
    target = str(rules.get("active_unit_id") or "")
    if target:
        for unit in party:
            if isinstance(unit, dict) and str(unit.get("id") or "") == target:
                return unit
    for unit in party:
        if isinstance(unit, dict):
            return unit
    return None


def _unit_snapshot_line(unit):
    """活快照一行：等级、六维、活力、四项资源、状态（§5.4 L4）。"""
    unit = unit if isinstance(unit, dict) else {}
    attrs = unit.get("attributes") or {}
    parts = [str(unit.get("id") or ""), str(unit.get("name") or ""),
             "等级 " + str(_int_value(unit.get("level")) or 1)]
    for attr_id, label in _ATTR_LABELS:
        parts.append(label + " " + str(_int_value(attrs.get(attr_id))))
    pools = unit.get("resources") or {}
    parts.append("活力 %s/%s" % (_int_value(unit.get("vitality")),
                                _int_value(unit.get("max_vitality"))))
    for pool, label in (("focus", "专注"), ("tempo", "气势"),
                        ("strain", "负担"), ("resolve", "决意")):
        parts.append(label + " " + str(_int_value(pools.get(pool))))
    conditions = [str(item) for item in (unit.get("conditions") or [])]
    parts.append("状态：" + ("、".join(conditions) if conditions else "无"))
    return " ".join(parts)


def build_l4(rules, guide, player_text, result=None):
    """本回合块（L4）：玩家原文、机械结果、活快照、行动菜单。

    超长时**先截玩家原文的尾部**（§5.4）。`here` 的节点名、痕迹与钩子
    属 G6 / G7，本切片没有实相与绑定，故省略。
    """
    rules = rules if isinstance(rules, dict) else {}
    guide = guide if isinstance(guide, dict) else {}
    text = str(player_text or "")
    lines = ["【本回合】", "玩家原文：" + text]
    if result:
        lines.append("判定：" + json.dumps(result, ensure_ascii=False,
                                         sort_keys=True))
    else:
        lines.append("判定：无判定")
    unit = _active_unit(rules)
    if unit:
        lines.append("活快照：" + _unit_snapshot_line(unit))
    actions = (rules.get("scene") or {}).get("actions") or []
    menu = ["%s %s" % (item.get("id"), item.get("label") or "")
            for item in actions
            if isinstance(item, dict) and item.get("id")]
    if menu:
        lines.append("行动菜单：" + "｜".join(menu))
    combat = rules.get("combat")
    lines.append("战斗中：" + ("是" if combat and not combat.get("over") else "否"))
    blob = "\n".join(lines)
    if len(blob) > L4_LIMIT:
        over = len(blob) - L4_LIMIT
        keep = max(0, len(text) - over - 1)
        lines[1] = "玩家原文：" + text[:keep] + "…"
        blob = "\n".join(lines)
        if len(blob) > L4_LIMIT:
            blob = blob[:L4_LIMIT]
    return blob


def build_narrative_messages(rules, guide, player_text, result=None):
    """叙事消息数组 L0–L4（§5.4）。

    L1 在未绑定时**仍是**那句常量「尚未选择战役」（绑定属 G5）；
    L2 取 `guide["l2"]`（由 `ensure_l2` 负责在 `scene.id` 变化时重写）；
    L3 只放 `guide["transcript"]`（审查之后的文本）。纯函数：不改 guide。
    """
    guide = guide if isinstance(guide, dict) else {}
    messages = [
        {"role": "system", "content": L0},
        {"role": "user", "content": L1_UNBOUND},
        {"role": "assistant", "content": "已载入世界卡。"},
        {"role": "user", "content": str(guide.get("l2") or "")},
        {"role": "assistant", "content": "已载入检查点。"},
    ]
    for entry in guide.get("transcript") or []:
        if not isinstance(entry, dict):
            continue
        role = entry.get("role")
        content = entry.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content})
    messages.append({"role": "user",
                     "content": build_l4(rules, guide, player_text, result)})
    return messages
