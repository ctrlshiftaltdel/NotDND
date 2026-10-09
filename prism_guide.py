#!/usr/bin/env python3
"""AI 导引者 · 标准库客户端、前缀、叙事回合、工具环、pcm16 语音代理与战役绑定
（G1 / G2 / G3 / G4 / G5）。

设计依据：GUIDE-DESIGN.md（§2.1 走进地点的定义 / §2.2 角色行为与对白 /
§4.3 已拍板 / §5.1 模块边界 / §5.2 回合怎么走 / §5.3 环境 / §5.4 缓存导向的提示词 /
§5.5 思考策略 / §5.6 工具 / §5.9 语音管线 / §5.10 我们自己的 HTTP / §6 接口变化 /
§7 数据模型 / §10 可观测性 / PR Plan G1–G5）。

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

G4（工具环，只调用已有公开入口）：

- `needs_tool`：纯函数，只认 `查规则` / `这地方` / `有哪些出口` 三句**整句**，
  且本回合还没有机械结果（§5.5）；`我想查规则` 不算；
- `run_tool_pass`：挂在**叙事消息数组**上的工具预通行（§5.2 / §5.6）——
  思考开、不传温度、1024、`stream` 为 false，内部最多 3 轮，每回合最多一次。
  工具往返只活在这一次的**内存列表**里（带 `tool_calls` 的助手消息同时带
  `reasoning_content`，否则供应商返回 400）；调用方随后**重拼 L0–L4**，
  叙事请求既无 `reasoning_content` 也没有 `role: tool`（§5.5）；
- 三只工具只走已有公开入口：`lookup_rule` 只读 `data/system/` 那六个 kind 并
  截到 1500 字；`read_place_card` 只在内存里已有带 `places` 的帧时读
  `atlas.guide_card`，**不调用** `atlas.restore_state`；`request_check` 走
  同一个 `settle`，三种 `ValueError` 收成固定短语——状态行早已写出，工具失败
  只能把短语喂回模型，**不**产生第二行 HTTP 状态（§5.2）；
- 预通行不读 `data/scenarios/` 猜测战役，也不新增第四只工具（§5.6）。

G5（战役绑定与典范卡）：

- `bind`：`POST /api/guide/bind` 记录**显式**的 `world_key` / `scenario_id`（§6）。
  `scenario_id` 只能是 `data/scenarios/<id>.json` 的**文件名主干**；不扫描目录、
  不按中文战役名搜索。文件里 `meta.world` 必须等于 `world_key`，否则「世界对不上」
  并且**不写** `scenario_id`。幂等：同一对 id 再绑一次不重掷 salt；另一场则「已经绑定」；
- `build_l1` / `l1_for`（§5.4）：绑定后 L1 才从那一份剧本文件（与可选世界文件）
  生成典范卡——地点专名、NPC 的 id / 名字 / drive / secret / mask、战役节点的 id 与
  名字、顶层线索的 id 与名字。**纯函数**，两次构建字节相同；salt 不进 L1；
- `location_ok`：`turn` 的可选 `location_id` 必须等于已绑定剧本里某个地点的 `id`
  （§2.1）。对不上在状态行之前 400「没有这个地点」，不叫模型；
- `ensure_realization`：**G5 的函数体只有 `return`**——不打开套接字、不画街道。
  G6 才替换这个函数体（锁内写 `pending` → 锁外读上游 → 校验 → 换图或要点退回）。

密钥只放请求头 `api-key`，不进 URL、不进 JSON、不进状态字典、不进日志；
异常字符串不携带上游响应体（§5.1 / §8）。
"""

import base64
import json
import os
import pathlib
import re
import secrets
import urllib.request

import prism_core

try:
    import atlas as atlas_kernel
except Exception:      # noqa: BLE001 — 地点卡是可选读：缺了不带走整个导引者
    atlas_kernel = None

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

# ── §5.5 / §5.6 工具环常量 ──────────────────────────────────────────────
#
# `needs_tool` 只认这三句**整句**（§5.5）：`我想查规则` 不是子串命中，不算。
TOOL_TRIGGERS = ("查规则", "这地方", "有哪些出口")

# 预通行的参数（§5.5 表「回合内的工具预通行」）：思考开、**不传温度**、
# 1024、非流式。内部最多 3 轮，每回合最多开一次（§5.2）。
TOOL_PASS_MAX_ROUNDS = 3
TOOL_PASS_MAX_TOKENS = 1024
TOOL_PASS_TIMEOUT_S = 20.0

# `lookup_rule` 只读这六个 kind（§5.6 / §5.12 表），正文截到 1500 字。
LOOKUP_RULE_KINDS = {
    "system.adjudication": "adjudication.json",
    "system.antipatterns": "antipatterns.json",
    "system.decision_engine": "decision_engine.json",
    "system.guardrails": "guardrails.json",
    "system.ledger_spec": "ledger_spec.json",
    "system.tone_packs": "tone_packs.json",
}
LOOKUP_RULE_LIMIT = 1500

# 工具错误字符串（§5.2 表）：与网页层 `GUIDE_SETTLE_ERRORS` 的固定短语同文
# （`tests/test_prism_guide.py` 有一条跨模块断言把两边锁在一起）。状态行已经
# 写出，工具里的失败只能把短语喂回模型，**不**再写第二行 HTTP 状态。
TOOL_ERRORS = {
    "行动不存在于当前场景": "没有这个行动",
    "单位不存在": "没有这个角色",
    "战斗还没结束，先打完这场": "战斗还没结束",
}
TOOL_ERROR_DEFAULT = "这次结算不能做"
TOOL_UNKNOWN = "没有这只工具"
TOOL_UNKNOWN_KIND = "没有这条规则"
TOOL_NO_RULE = "读不到这条规则"

# 叙事段的上游时间盒：§5.11 表「叙事 …… 最多 30 秒，且在回合 45 秒的叙事段之内」。
NARRATIVE_TIMEOUT_S = 30.0

# L4（本回合）的字符预算（§5.11：2_000 字）。
L4_LIMIT = 2000

# 环境变量三元组（§5.3）：旧名 NOTDND_AI_* 一律不认。
ENV_KEYS = ("BASE_URL", "MODEL", "API_KEY")

_CHAT_PATH = "/chat/completions"
_SALT_RE = re.compile(r"^[0-9a-f]{32}$")

# ── §5.4 / §6 显式绑定与典范卡（G5）─────────────────────────────────────

# 剧本 id 是**文件名主干**（§6）。它既是业务标识，也是磁盘文件名安全边界：
# 只允许小写字母 / 数字 / `-` / `_`，杜绝 `../` 探测。世界 key 走同一张白名单
# （它只用来拼 `data/worlds/<key>.json`，且必须与 `meta.world` 逐字相等）。
SCENARIO_ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")

# 绑定后才读的两个目录：`data/scenarios/<id>.json`（点名的那一份）与
# `data/worlds/<key>.json`（存在才读，不存在不算失败，§5.12）。
SCENARIO_DIR = pathlib.Path(__file__).resolve().parent / "data" / "scenarios"
WORLD_DIR = pathlib.Path(__file__).resolve().parent / "data" / "worlds"

# L1 的字符预算（§5.11）：未绑定的常量远小于 2_000，绑定路径用 8_000。
L1_LIMIT = 8000

# L1 里每个字段截到 120 个码位（§5.4）。秘密泄漏校验用文件全文，不用这句截断。
L1_FIELD_LIMIT = 120

# 世界文件里最多带几行「术语：含义」（§5.4）。
L1_GLOSSARY_LIMIT = 8

# 显式绑定的三种失败（§6）：前两种 400，第三种 409。网页层把它翻成固定 JSON。
BIND_NO_SCENARIO = "没有这场战役"
BIND_WORLD_MISMATCH = "世界对不上"
BIND_ALREADY_BOUND = "已经绑定"

# 实相占位的时间盒（§5.2）：活着的那次调用（含一次重试）不能被当成进程已死。
REALIZATION_CLAIM_S = 120

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


# ── §5.4 绑定后的典范卡（L1，G5）─────────────────────────────────────────
#
# 未绑定时 L1 就是那句常量；**只有** `guide.scenario_id` 非空时才读剧本目录，
# 而且只读点名的这一份（§5.12：不扫描目录，不按中文战役名搜索）。

# 剧本 / 世界文件按 id 缓存解析结果：同一进程里反复构建 L1 不再重复读盘。
# 只在**读成功**时写入；读失败不写缓存（下次仍可重试）。
_SCENARIO_CACHE: dict[str, dict] = {}
_WORLD_CACHE: dict[str, dict] = {}


def _read_data_json(directory, name, cache):
    """读 `data/<directory>/<name>.json`：name 先过白名单，再惰性 + 缓存。

    import 本模块**不碰磁盘**（只有在第一次绑定 / 构建 L1 时才读）。
    文件缺失 / 读不了 / 顶层不是字典 → None；调用方把它翻成固定短语。
    """
    if not isinstance(name, str) or not SCENARIO_ID_RE.fullmatch(name):
        return None
    cached = cache.get(name)
    if cached is not None:
        return cached
    try:
        raw = json.loads((directory / (name + ".json")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    cache[name] = raw
    return raw


def load_scenario(scenario_id):
    """`data/scenarios/<id>.json`；读不到返回 None（**不**扫描目录找替代）。"""
    return _read_data_json(SCENARIO_DIR, scenario_id, _SCENARIO_CACHE)


def load_world(world_key):
    """`data/worlds/<key>.json`；没有这份世界文件返回 None（不算绑定失败）。"""
    return _read_data_json(WORLD_DIR, world_key, _WORLD_CACHE)


def _clip(text, limit=L1_FIELD_LIMIT):
    """各字段截到 120 个码位（§5.4）；脏值降级成空串。"""
    return str(text or "")[:limit]


def key_place_heads(location):
    """`key_places` 条目的**专名**：第一个全角破折号 `——` 之前的子串。

    没有破折号就用整串，两端去空白（§2.1）。条目按数组顺序，不去重、不排序。
    """
    heads = []
    for entry in (location or {}).get("key_places") or []:
        text = str(entry or "")
        head = text.split("——", 1)[0].strip()
        heads.append(head or text.strip())
    return heads


def _mask_for(npc, ledger_by_name):
    """`mask`：npc 对象上有就用对象上的；没有才用账本模板里**同名**的那一条。

    模板里的短 drive 不进这里，也不当「与典范矛盾」的对照句（§2.2）。
    """
    mask = (npc or {}).get("mask")
    if isinstance(mask, str) and mask.strip():
        return mask
    other = ledger_by_name.get(str((npc or {}).get("name") or "")) or {}
    return str(other.get("mask") or "")


def build_l1(scenario_id, world_key=""):
    """由**那一份**剧本文件与可选世界文件构建典范卡（L1）。**纯函数**。

    §5.4：排序固定（地点 / NPC / 节点 / 线索都按 id，术语按 `term`）；去掉一切
    `source` 键；不放 `encounters`；不放账本模板里的短 drive、短线索名；各字段
    截到 120 码位。salt 不进 L1，`docs/` 路径也不进（`source` 全部丢掉）。

    读不到剧本时退回未绑定常量：绑定路径已经验过文件存在，这只是防御性降级，
    绝不改成「随便找一份来读」。
    """
    data = load_scenario(scenario_id)
    if data is None:
        return L1_UNBOUND
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    key = str(world_key or "") or str(meta.get("world") or "")

    ledger_by_name = {}
    for entry in (data.get("ledger_template") or {}).get("npcs") or []:
        if isinstance(entry, dict):
            ledger_by_name[str(entry.get("name") or "")] = entry

    lines = ["【世界卡】",
             "剧本：" + str(scenario_id or ""),
             "战役：" + _clip(meta.get("campaign")),
             "世界：" + _clip(meta.get("world")),
             "引擎：" + _clip(meta.get("engine"))]

    lines.append("术语：")
    world = load_world(key)
    terms = []
    for entry in (world or {}).get("glossary") or []:
        if not isinstance(entry, dict):
            continue
        terms.append((str(entry.get("term") or ""), _clip(entry.get("meaning"))))
    terms.sort(key=lambda item: item[0])
    for term, meaning in terms[:L1_GLOSSARY_LIMIT]:
        lines.append(term + "：" + meaning)

    lines.append("地点：")
    locations = [item for item in (data.get("locations") or [])
                 if isinstance(item, dict)]
    for location in sorted(locations, key=lambda item: str(item.get("id") or "")):
        head = "%s %s" % (str(location.get("id") or ""),
                          _clip(location.get("name")))
        for place in key_place_heads(location):
            head += "｜" + _clip(place)
        lines.append(head)

    lines.append("人物：")
    npcs = [item for item in (data.get("npcs") or []) if isinstance(item, dict)]
    for npc in sorted(npcs, key=lambda item: str(item.get("id") or "")):
        lines.append("｜".join([
            "%s %s" % (str(npc.get("id") or ""), _clip(npc.get("name"))),
            "欲望：" + _clip(npc.get("drive")),
            "面具：" + _clip(_mask_for(npc, ledger_by_name)),
            "秘密：" + _clip(npc.get("secret")),
        ]))

    lines.append("节点：")
    nodes = [item for item in (data.get("nodes") or []) if isinstance(item, dict)]
    for node in sorted(nodes, key=lambda item: str(item.get("id") or "")):
        lines.append("%s %s" % (str(node.get("id") or ""),
                                _clip(node.get("name"))))

    lines.append("线索：")
    threads = [item for item in (data.get("threads") or [])
               if isinstance(item, dict)]
    for thread in sorted(threads, key=lambda item: str(item.get("id") or "")):
        lines.append("%s %s" % (str(thread.get("id") or ""),
                                _clip(thread.get("name"))))

    return ("\n".join(lines) + "\n")[:L1_LIMIT]


def l1_for(guide):
    """当前会话的 L1：未绑定仍是「尚未选择战役」，绑定后是这场剧本的典范卡。

    只看 `scenario_id`——它非空即「已经绑定」，与 `l1_key` 同义（§7）。
    """
    guide = guide if isinstance(guide, dict) else {}
    scenario_id = str(guide.get("scenario_id") or "")
    if not scenario_id:
        return L1_UNBOUND
    return build_l1(scenario_id, str(guide.get("world_key") or ""))


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


# ── §6 显式绑定与焦点（G5）────────────────────────────────────────────────


def bind(web_session, world_key, scenario_id):
    """记录**显式**的 `world_key` / `scenario_id`（§6）。失败抛 `ValueError`。

    「哪种失败对应哪个状态码」仍是网页层的合同，这里只抛固定短语：

    · `scenario_id` 不是文件名主干（`^[a-z0-9_-]{1,64}$`）、或文件不存在 /
      读不了 → `没有这场战役`；
    · 文件里 `meta.world` 与 `world_key` 不逐字相等 → `世界对不上`，
      并且**不写** `scenario_id`（绑定没有发生一半）；
    · 已绑定**同一对** id：幂等返回，不重掷 salt、不重画实相；
    · 已绑定**别的** id：`已经绑定`（换一场要开新存档）。

    同时把 `l1_key` 置成 `scenario_id`（§7：绑定后 L1 的键就是它）。
    没绑定的会话照旧不读剧本目录，L1 仍是那句常量。
    """
    world_key = str(world_key or "").strip()
    scenario_id = str(scenario_id or "").strip()
    guide = web_session.guide if isinstance(web_session.guide, dict) else {}
    current = str(guide.get("scenario_id") or "")
    if current:
        same = (scenario_id == current
                and world_key == str(guide.get("world_key") or ""))
        if not same:
            raise ValueError(BIND_ALREADY_BOUND)
        return {"world_key": world_key, "scenario_id": scenario_id}
    if not SCENARIO_ID_RE.fullmatch(scenario_id):
        raise ValueError(BIND_NO_SCENARIO)
    data = load_scenario(scenario_id)
    if data is None:
        raise ValueError(BIND_NO_SCENARIO)
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    if str(meta.get("world") or "") != world_key:
        raise ValueError(BIND_WORLD_MISMATCH)
    with web_session.lock:
        web_session.guide["world_key"] = world_key
        web_session.guide["scenario_id"] = scenario_id
        web_session.guide["l1_key"] = scenario_id
        web_session.save()
    return {"world_key": world_key, "scenario_id": scenario_id}


def location_ok(guide, location_id):
    """`location_id` 必须等于**已绑定剧本**里某个地点的 `id`（§2.1）。**纯函数**。

    没绑定就没有可对上的剧本，一律不合法——禁止从玩家散文里猜「他是不是进了白壁」。
    """
    scenario_id = str((guide or {}).get("scenario_id") or "")
    wanted = str(location_id or "")
    if not scenario_id or not wanted:
        return False
    data = load_scenario(scenario_id)
    if data is None:
        return False
    for location in data.get("locations") or []:
        if isinstance(location, dict) and str(location.get("id") or "") == wanted:
            return True
    return False


def ensure_realization(web_session, location_id):
    """确保焦点地点的实相（地点图）已经生成（§2.1 / §5.2）。

    **G5 的函数体只有 `return`**：不打开套接字、不画街道、一个字都不写。
    位置（在状态行之后、叙事之前调用）、写 `pending` 的顺序与 `claim` 的语义
    由 G6 决定；G6 只替换这个函数体，不改 `notdnd_web.py`，也不再开 SSE 分支。
    """
    return


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
    """把 Chat Completions 的响应体组装成 `{content, tool_calls, usage[, audio][, reasoning]}`。

    流式（`delta`）与非流式（`message`）回包都认：只读 `data:` 行，
    `[DONE]` 结束，畸形块跳过不抛（一次坏块不该把整回合变成兜底）。

    `audio` 是 TTS（§5.9）的 PCM，**只在这条流真的带过音频块时才出现**，
    值是 `bytes`；`reasoning` 是思考态回包里的 `reasoning_content`（§5.5），
    **只在真的带过时才出现**。所以纯叙事回包仍是三键、JSON 可序列化的字典，
    带音频 / 思考的回包分别由 `call_tts` 与工具预通行消费。

    ⚠️ `reasoning` 是**运行期**的东西：只有预通行的那一次内存列表要把它放回
    `tool_calls` 的助手消息（否则供应商返回 400）。它不进 `guide`、不进 L3、
    不进 `to_dict`，叙事请求也重拼 L0–L4、不带这个字段（§5.5）。

    每个 `delta.audio.data` 非空的块**单独** base64 解码后按到达顺序拼起来；
    `choices` 为空的块只读用量。
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
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
            think = piece_holder.get("reasoning_content")
            if isinstance(think, str):
                reasoning_parts.append(think)
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
    if reasoning_parts:
        out["reasoning"] = "".join(reasoning_parts)
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


# ════════════════════════════════════════════════════════════════════════
# §5.5 / §5.6 工具环 · 只调用已有公开入口
# ════════════════════════════════════════════════════════════════════════


# `data/system/` 的目录：惰性读，**import 不碰磁盘**。
SYSTEM_DIR = pathlib.Path(__file__).resolve().parent / "data" / "system"

# lookup_rule 的正文缓存（按文件名）。只在读成功时写入。
_LOOKUP_CACHE: dict[str, str] = {}


def needs_tool(text, has_mechanical):
    """要不要开工具预通行（§5.5）。**纯函数**。

    为真当且仅当本回合还没有机械结果，且 `text.strip()` **整句等于**
    `查规则` / `这地方` / `有哪些出口` 之一。不是子串——`我想查规则` 不算。
    """
    if has_mechanical:
        return False
    return str(text or "").strip() in TOOL_TRIGGERS


def _compact_json(obj):
    """紧凑 JSON：给模型的工具正文用，不写进任何存档。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def lookup_rule_text(kind):
    """`lookup_rule` 的正文（§5.6 / §5.12）：只读那六个 kind，截到 1500 字。

    返回文件自己的 `note`（人写的一句话摘要）加紧凑载荷；读不到就给固定
    短语，**不抛**——工具正文不该把整回合变成兜底。
    """
    name = LOOKUP_RULE_KINDS.get(str(kind or ""))
    if not name:
        return TOOL_UNKNOWN_KIND
    cached = _LOOKUP_CACHE.get(name)
    if cached is None:
        try:
            raw = json.loads((SYSTEM_DIR / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return TOOL_NO_RULE
        if not isinstance(raw, dict):
            return TOOL_NO_RULE
        note = str(raw.get("note") or "").strip()
        payload = {key: value for key, value in raw.items()
                   if key not in ("schema_version", "kind", "note")}
        cached = (note + "\n" if note else "") + _compact_json(payload)
        _LOOKUP_CACHE[name] = cached
    return cached[:LOOKUP_RULE_LIMIT]


def _scale_line(frame):
    """帧的一句尺度（§5.6 工具描述「一句尺度」）。取值全是帧自己的字段。"""
    frame = frame if isinstance(frame, dict) else {}
    parts = [str(frame.get("space") or "")]
    cell = str(frame.get("cell") or "")
    zed = str(frame.get("z_meaning") or "")
    if cell:
        parts.append("一格：" + cell)
    if zed:
        parts.append("z：" + zed)
    return "；".join(item for item in parts if item)


def place_card(web_session):
    """`read_place_card` 的正文（§5.6 / §5.12）。

    只有**内存里已经有**带 `places` 的帧时才读 `atlas.guide_card`：
    还没物化、或手里只有 `{seed, deltas}` 那种存档块，一律
    `{"available": false}`，并**不调用** `atlas.restore_state`，也不兜底。
    深拷贝的整份 `place` **不进提示词**——只回地点名、出口与一句尺度。
    """
    atlas = getattr(web_session, "_atlas", None)
    locus = getattr(web_session, "_locus", None)
    if atlas_kernel is None or not isinstance(atlas, dict) or not isinstance(locus, dict):
        return {"available": False}
    frame = (atlas.get("frames") or {}).get(locus.get("frame_id"))
    if not isinstance(frame, dict) or not isinstance(frame.get("places"), dict):
        # `{seed, deltas}` 存档块的帧没有 `places`：这里就是那条 KeyError 防线。
        return {"available": False}
    card = atlas_kernel.guide_card(atlas, locus)
    if not isinstance(card, dict):
        return {"available": False}
    place = card.get("place") if isinstance(card.get("place"), dict) else {}
    exits = [{"via": entry.get("via"), "name": entry.get("name"),
              "band": entry.get("band")}
             for entry in (card.get("exits") or []) if isinstance(entry, dict)]
    return {"available": True, "place_id": place.get("id"),
            "name": place.get("name"), "exits": exits,
            "scale": _scale_line(card.get("frame"))}


def tool_call_parts(call):
    """拆一只工具调用：`(name, arguments)`，畸形输入降级成空。"""
    call = call if isinstance(call, dict) else {}
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(fn.get("name") or "")
    args = fn.get("arguments")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = {}
    return name, (args if isinstance(args, dict) else {})


def run_tool_call(web_session, call):
    """执行一只工具调用，返回 `(工具正文, 是否发生了结算)`。**永不抛**。

    `request_check` 走的就是回合开头那个 `settle`；三种 `ValueError` 收成
    §5.2 的固定短语——状态行早已写出，这里只把短语喂回模型当**工具错误
    字符串**，不产生第二行 HTTP 状态。同一个 action_id 再问一次由
    `prism_core` 的 `done_actions` 短路（`already_done`），不掷第二次。
    """
    name, args = tool_call_parts(call)
    if name == "lookup_rule":
        return lookup_rule_text(args.get("kind")), False
    if name == "read_place_card":
        return _compact_json(place_card(web_session)), False
    if name == "request_check":
        try:
            result = settle(web_session, str(args.get("action_id") or ""))
        except ValueError as exc:
            return TOOL_ERRORS.get(str(exc), TOOL_ERROR_DEFAULT), False
        return _compact_json(result), True
    return TOOL_UNKNOWN, False


def build_tool_body(messages, model=None, env=None):
    """预通行请求体（§5.5 表）：思考开、**不传温度**、1024、非流式、带工具。

    工具数组与叙事请求逐字节相同（§5.6）；密钥不入体。
    """
    env = env if env is not None else load_env()
    resolved = model if model is not None else (env.get("MODEL") or "").strip()
    return {
        "model": str(resolved),
        "messages": list(messages),
        "tools": TOOLS,
        "thinking": {"type": "enabled"},
        "max_completion_tokens": TOOL_PASS_MAX_TOKENS,
        "stream": False,
    }


def _send_once(url, body, key, timeout, transmit):
    """经传输入口发一次；离线 / 失败 / 形状不对一律 None（不抛）。"""
    sender = transmit if transmit is not None else TRANSMIT
    try:
        got = sender(url, body, {KEY_HEADER: key}, timeout=timeout)
    except Exception:  # noqa: BLE001 — 上游失败不回显、不重试
        return None
    return got if isinstance(got, dict) else None


def call_tool_pass(messages, model=None, env=None, transmit=None):
    """一次预通行请求（§5.5）。离线 / 上游失败返回 None。

    预通行失败**不**把整回合改成兜底句：后面的叙事请求照走（§5.2）。
    """
    env = env if env is not None else load_env()
    url = chat_url(env.get("BASE_URL"))
    key = (env.get("API_KEY") or "").strip()
    resolved = model if model is not None else (env.get("MODEL") or "").strip()
    if not url or not key or not str(resolved or "").strip():
        return None
    body = build_tool_body(messages, model=resolved, env=env)
    return _send_once(url, body, key, TOOL_PASS_TIMEOUT_S, transmit)


def run_tool_pass(web_session, messages, model=None, env=None, transmit=None,
                  *, max_rounds=TOOL_PASS_MAX_ROUNDS):
    """工具预通行（§5.2 / §5.5 / §5.6）：挂在**叙事消息数组**上，内部最多 3 轮。

    返回一份**运行期**统计 `{"rounds", "tools", "settled"}`，调用方不看也行。
    工具往返只活在这一次的**内存列表**里：带 `tool_calls` 的助手消息必须
    同时带 `reasoning_content`（否则供应商返回 400）。调用方随后**重拼
    L0–L4** 再发叙事请求——叙事那一份既无 `reasoning_content`，也没有
    `role: tool`（§5.5）。全程不写 `guide`，所以 `reasoning` 不可能进 `to_dict`。
    """
    trace = {"rounds": 0, "tools": [], "settled": False}
    env = env if env is not None else load_env()
    convo = list(messages or [])
    for _ in range(max(0, int(max_rounds or 0))):
        got = call_tool_pass(convo, model=model, env=env, transmit=transmit)
        if got is None:
            break
        calls = [call for call in (got.get("tool_calls") or [])
                 if isinstance(call, dict)]
        trace["rounds"] += 1
        if not calls:
            break
        assistant = {"role": "assistant",
                     "content": str(got.get("content") or ""),
                     "tool_calls": calls}
        reasoning = got.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            assistant["reasoning_content"] = reasoning
        convo.append(assistant)
        for call in calls:
            name, _args = tool_call_parts(call)
            content, did_settle = run_tool_call(web_session, call)
            trace["tools"].append(name)
            trace["settled"] = trace["settled"] or did_settle
            convo.append({"role": "tool",
                          "tool_call_id": str(call.get("id") or ""),
                          "content": content})
    return trace


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

    L1 未绑定时**仍是**那句常量「尚未选择战役」（`l1_for`），绑定之后换成这场
    剧本的典范卡——同一剧本的每一局字节相同，salt 不进 L1；
    L2 取 `guide["l2"]`（由 `ensure_l2` 负责在 `scene.id` 变化时重写）；
    L3 只放 `guide["transcript"]`（审查之后的文本）。纯函数：不改 guide。
    """
    guide = guide if isinstance(guide, dict) else {}
    messages = [
        {"role": "system", "content": L0},
        {"role": "user", "content": l1_for(guide)},
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
