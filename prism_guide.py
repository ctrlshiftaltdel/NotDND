#!/usr/bin/env python3
"""AI 导引者 · 标准库客户端与前缀（G1 · 离线骨架，无路由）。

设计依据：GUIDE-DESIGN.md（§5.3 环境 / §5.4 缓存导向的提示词 /
§5.5 思考策略 / §5.6 工具 / §7 数据模型 / §10 可观测性 / PR Plan G1）。

本切片只做离线部分：

- `.env` 只解析 `BASE_URL` / `MODEL` / `API_KEY` 三个键，`os.environ` 优先；
- URL 拼接（API 根 + `/chat/completions`，容忍末尾斜杠与完整路径）；
- 叙事请求体（思考关闭、温度 0.7、700 token、§5.6 工具 JSON，密钥不入体）；
- L0 / L1 / L2 的常量与构建（产品路径不读 `data/scenarios/` 与 `data/worlds/`）；
- `guide` 状态字典（`empty_guide` / `guide_from`，§7 按键合同）；
- `cache_hit_ratio` 纯函数。

本切片不发起任何网络请求，不打开套接字；路由与回合编排属 G2。
密钥只放请求头 `api-key`，不进 URL、不进 JSON、不进状态字典、不进日志。
"""

import json
import os
import re
import secrets

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
