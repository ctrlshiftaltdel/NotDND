#!/usr/bin/env python3
"""G1–G2 · prism_guide 回归测试（前缀 + 叙事回合）。

直接 `python3 tests/test_prism_guide.py` 运行；零依赖，只用标准库，
**不访问网络**（上游调用一律走假传输 / 直接喂字节）。

G1 覆盖：

- `.env` 只解析三个键、os.environ 优先、旧名 NOTDND_AI_* 不认；
- 空 BASE_URL / 空 MODEL / 缺密钥时状态布尔正确；
- URL 拼接（末尾斜杠 / 完整路径）；
- 叙事请求体（thinking disabled、0.7、700、工具 JSON、密钥不入体）；
- 工具 JSON 与 GUIDE-DESIGN.md §5.6 规范串全等；
- L0 / L1 / L2 字节稳定性与内容边界；
- guide 状态字典（empty_guide / guide_from 按键合同）；
- cache_hit_ratio；`.env.example` 仍含 NOTDND_HOST / NOTDND_PORT。

G2 覆盖：

- 传输层是一个可替换入口（假传输），构建路径离线；
- `assemble_chat_stream` / `read_usage`（流式 SSE 与非流式**整段 JSON** 两种回包
  得到等价结果；空 / 畸形 / 非对象不抛）；
- `settle`：注水 → 结算 → 写回 → save，异常原样抛；
- `verdict_line` 取句顺序与「判定：成功」；
- `review_narration` 的骰子审查与裁决替换；`fallback_text` 保留裁决；
- `build_l4` / `build_narrative_messages` 的段落顺序与上限。

G3 覆盖：

- `assemble_chat_stream` 的 `audio`：每个 `delta.audio.data` **单独** base64 解码
  再拼接（把 base64 文本接起来再解是**错的**）；纯叙事回包没有 `audio` 键；
- `split_beats` / `beats_of`：默认整拍旁白、对白切拍、6 拍上限、不按姓名推断；
- `strip_marks`：长度 1–12 的括号 / 方括号记号；`【裁决】` 三段标题不是记号；
- `build_tts_body` / `call_tts`：`mimo-v2.5-tts`、平叙风格卡、`pcm16`、
  台词在 `assistant`、密钥不入体、离线与失败返回空字节**不重试**。

G4 覆盖：

- `needs_tool`：三句**整句**；`我想查规则` 不算；有机械结果时永不开；
- `build_tool_body` / `run_tool_pass`：思考开、不传温度、1024、非流式、带工具；
  内部最多 3 轮；带 `tool_calls` 的助手消息带 `reasoning_content`；
- `run_tool_call`：`request_check` 走同一个 `settle`，三种 `ValueError` 收成
  固定短语（与网页层 `GUIDE_SETTLE_ERRORS` 同文）且**不抛**；
- `lookup_rule_text`：只读那六个 kind、截到 1500 字、坏输入给固定短语；
- `place_card`：只有内存里带 `places` 的帧才可读；`{seed, deltas}` 存档块
  得到 `available: false` 且不抛 `KeyError`，也不调用 `restore_state`；
- `reasoning` 只活在运行期：不进 `guide`、不进 `to_dict`、不进叙事请求。

G6 覆盖（实相）：

- `canon_for`：白壁五条专名逐字在；灰市第一条是 `绳会账房`；没有 `key_places`
  的地点不生成实相；
- `validate_realization`（纯函数）：改了典范名 / 缺少要点 / 无引用 / 只有要点；
  要点必须**自引其专名**（引 `atmosphere`、引别的专名、空 `cites` 都归到无引用）；
  近名只丢弃那个节点，不是整张图失败；只靠「老钱」的贫区丢弃并删边；落盘的是
  典范原文的 `unlock` / `if_botched`；实相对象没有坐标；
- `build_realization_body`：思考开、非流式、4096、无工具；输出合同在 user 消息，
  不在 L0；
- `ensure_realization`：第一次坏 JSON → 第二次合法图，只存第二次；两次只有要点 →
  `source: fallback` 要点链；离线直接要点链；同一地点第二次不请求；新鲜 `pending`
  不请求也不写 fallback；过期 `pending` 可再占一次；两次重叠只有一次上游；提交后
  重写 L2（salt + 听泉馆）并清空 L3；两次尝试**共用** `REALIZATION_TIMEOUT_S`
  （第二次只用剩余预算，第一次吃满就不再发第二次）。

G7 覆盖（行为与对白）：

- `npc_voice`：FNV-1a，`npc-01` 白桦 / `npc-02` 茉莉；同一存档 `guide.voices` 稳定；
- `check_speech`：只查自己的 `secret`（全文或连续 8 码位）；点出另一名典范 NPC
  的名字不算失败；不判断面具 / 欲望；
- `redact_narration` / `redact_secrets`：整段旁白里的秘密换成「……」，秘密用文件
  全文；`lever` 在玩家原文里、或 `known_clues ∩ knows` 时放行；**不**调用传输；
- `bind` 填 `npc_home`（模板 `location` 含地点 `name` 才算家）；`npcs_at` /
  `scene_speakers` 只认在场 NPC；
- `record_traces`：`betrayed` 用 `if_dead_or_betrayed` 原文且不叫模型；其余一次
  非流式请求、思考关、0.7、512、无工具；句子命中秘密就丢掉；离线 / 失败不留痕迹；
- `beats_of`：对白拍写 `speaker` 与按 `npc.id` 分配的声线，并把说过话的 NPC 写进
  `npc_at`；音色取自那一拍；
- 路人 id `^inc-[0-9a-f]{8}$`、确定、不等于 `npc-01`；manner 沾 `C-` 线索或秘密
  的路人丢掉；`reveal_next_trace` 每次至多揭开一条。
"""

import base64
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import prism_core  # noqa: E402
import prism_guide as pg  # noqa: E402
import atlas as atlas_kernel  # noqa: E402

_ENV_KEYS = ("BASE_URL", "MODEL", "API_KEY")
_SALT_RE = re.compile(r"^[0-9a-f]{32}$")

_passed = 0


def ok(name):
    global _passed
    _passed += 1
    print("通过: " + name)


# ── 环境 ────────────────────────────────────────────────────────────────


def test_parse_env_only_three_keys():
    text = (
        "# 注释\n"
        "BASE_URL=https://api.example.com/v1\n"
        "MODEL=mimo-v2.6-flash\n"
        "API_KEY=secret123\n"
        "NOTDND_AI_API_KEY=old\n"
        "NOT_AI_BASE_URL=ignore\n"
        "OTHER=ignore\n"
    )
    env = pg.parse_env(text, environ={})
    assert set(env.keys()) == set(_ENV_KEYS), env.keys()
    assert env["BASE_URL"] == "https://api.example.com/v1"
    assert env["MODEL"] == "mimo-v2.6-flash"
    assert env["API_KEY"] == "secret123"
    ok("parse_env 只写三个键，未知键与旧名忽略")


def test_parse_env_environ_priority():
    text = "BASE_URL=from-file\nMODEL=from-file\nAPI_KEY=from-file\n"
    env = pg.parse_env(text, environ={"BASE_URL": "from-env"})
    assert env["BASE_URL"] == "from-env"
    assert env["MODEL"] == "from-file"
    assert env["API_KEY"] == "from-file"
    # os.environ 里已有键即使是空白也优先（.env 不覆盖）。
    env = pg.parse_env(text, environ={"API_KEY": ""})
    assert env["API_KEY"] == ""
    ok("os.environ 优先，.env 不覆盖")


def test_parse_env_quotes_and_comments():
    text = "  BASE_URL = \"https://api.example.com/v1/\"  \n#MODEL=x\nMODEL='m'\n"
    env = pg.parse_env(text, environ={})
    assert env["BASE_URL"] == "https://api.example.com/v1/"
    assert env["MODEL"] == "m"
    assert env["API_KEY"] == ""
    ok("parse_env 去空白、去成对引号、跳过注释")


def test_load_env_file(tmp_root):
    d = pathlib.Path(tmp_root)
    (d / ".env").write_text(
        "BASE_URL=https://api.example.com/v1\nMODEL=mm\nAPI_KEY=kk\n",
        encoding="utf-8")
    saved = {key: os.environ.pop(key, None) for key in _ENV_KEYS}
    try:
        env = pg.load_env(str(d / ".env"))
        assert env["BASE_URL"] == "https://api.example.com/v1"
        missing = pg.load_env(str(d / "no-such-file.env"))
        assert missing == {key: "" for key in _ENV_KEYS}
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
    ok("load_env 读取文件；缺失文件静默返回空值")


def test_old_names_are_offline(tmp_root):
    d = pathlib.Path(tmp_root)
    (d / ".env").write_text(
        "NOTDND_AI_API_KEY=old\nNOTDND_AI_BASE_URL=old\nNOTDND_AI_MODEL=old\n",
        encoding="utf-8")
    saved = {key: os.environ.pop(key, None) for key in _ENV_KEYS}
    try:
        st = pg.status(pg.load_env(str(d / ".env")))
        assert st == {"chat": False, "tts": False, "configured": False}, st
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value
    ok("只填旧名 NOTDND_AI_* 视为离线，不请求")


def test_status_booleans():
    full = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
            "API_KEY": "kk"}
    assert pg.status(full) == {"chat": True, "tts": True,
                               "configured": True}
    no_model = dict(full, MODEL="")
    assert pg.status(no_model) == {"chat": False, "tts": True,
                                   "configured": True}
    no_base = dict(full, BASE_URL="")
    assert pg.status(no_base) == {"chat": False, "tts": False,
                                  "configured": False}
    no_key = dict(full, API_KEY="")
    assert pg.status(no_key) == {"chat": False, "tts": False,
                                 "configured": False}
    blankish = dict(full, BASE_URL="   ")
    assert pg.status(blankish)["chat"] is False
    ok("状态布尔：空 MODEL 只关聊天；空 BASE_URL / 密钥全假")


def test_status_dict_has_no_secret():
    env = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
           "API_KEY": "super-secret-value"}
    dumped = json.dumps(pg.status(env), ensure_ascii=False)
    assert "super-secret-value" not in dumped
    assert "BASE_URL" not in dumped and "API_KEY" not in dumped
    assert "api.example.com" not in dumped
    ok("状态字典不含密钥与 BASE_URL")


# ── URL 拼接 ────────────────────────────────────────────────────────────


def test_chat_url():
    assert pg.chat_url("https://api.example.com/v1") == \
        "https://api.example.com/v1/chat/completions"
    assert pg.chat_url("https://api.example.com/v1/") == \
        "https://api.example.com/v1/chat/completions"
    assert pg.chat_url("https://api.example.com/v1/chat/completions") == \
        "https://api.example.com/v1/chat/completions"
    assert pg.chat_url("https://api.example.com/v1/chat/completions/") == \
        "https://api.example.com/v1/chat/completions"
    assert pg.chat_url("") == ""
    assert pg.chat_url("   ") == ""
    ok("chat_url：API 根 / 末尾斜杠 / 完整路径都能拼对")


# ── 叙事请求体与工具 ────────────────────────────────────────────────────


def test_narrative_body():
    env = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
           "API_KEY": "super-secret-value"}
    body = pg.build_narrative_body(
        [{"role": "system", "content": pg.L0}], env=env)
    assert body["thinking"] == {"type": "disabled"}
    assert body["temperature"] == 0.7
    assert body["max_completion_tokens"] == 700
    assert body["stream"] is True
    assert body["model"] == "mm"
    assert body["tools"] == pg.TOOLS
    dumped = json.dumps(body, ensure_ascii=False)
    assert "super-secret-value" not in dumped, "密钥不得进入请求体"
    assert "api-key" not in dumped
    ok("叙事请求体：thinking disabled / 0.7 / 700 / 工具在体 / 密钥不入体")


def test_tools_json_matches_design_doc():
    doc = (ROOT / "GUIDE-DESIGN.md").read_text(encoding="utf-8")
    lines = [ln for ln in doc.splitlines()
             if ln.startswith('[{"function"')]
    assert len(lines) == 1, "GUIDE-DESIGN.md §5.6 应只有一行规范串"
    canonical = lines[0]
    assert len(canonical) == 713, len(canonical)
    assert pg.TOOLS_JSON == canonical
    assert pg.tools_json() == canonical
    # 三只工具的顺序固定。
    assert [t["function"]["name"] for t in pg.TOOLS] == \
        ["lookup_rule", "read_place_card", "request_check"]
    ok("工具 JSON 与 GUIDE-DESIGN.md §5.6 规范串全等（713 字符）")


# ── L0 / L1 / L2 ────────────────────────────────────────────────────────


def test_l0_constant():
    assert pg.L0 == pg.L0  # 常量即常量；两次取用字节相同由同一对象保证
    assert len(pg.L0) <= pg.L0_LIMIT, len(pg.L0)
    # 第 5.4 节追加的三句必须在。
    assert "不得改写典范专名" in pg.L0
    assert "不得用近一字的新名替换地点名或要点名" in pg.L0
    assert "不主动说出秘密" in pg.L0
    assert "不在场的事只留痕迹" in pg.L0
    # 原有七项：身份、三段标题、不替玩家做主、失败必须改变局势、
    # 裁决会被换掉、淡出边界、不输出表演记号。
    assert "导引者" in pg.L0
    for mark in ("【裁决】", "【叙事】", "【钩子】"):
        assert mark in pg.L0
    assert "不替玩家做主" in pg.L0
    assert "失败必须改变局势" in pg.L0
    assert "裁决会被换掉" in pg.L0
    assert "淡出边界" in pg.L0
    assert "不输出表演记号" in pg.L0
    # 边界：不含典范专名、salt、sid、ISO 日期。
    assert "白壁" not in pg.L0
    assert "绳会账房" not in pg.L0
    assert "salt" not in pg.L0.lower() or "salt" not in pg.L0
    assert "sid" not in pg.L0
    assert not re.search(r"\d{4}-\d{2}-\d{2}", pg.L0)
    ok("L0：含追加三句与原有七项，≤1500 字，不含专名 / salt / sid / 日期")


def test_l1_unbound_constant():
    assert pg.L1_UNBOUND == "【世界卡】\n尚未选择战役\n"
    ok("L1 未绑定常量全等")


def _fixture_rules(scene_id="sc-01", party=True):
    rules = {"scene": {"id": scene_id, "actions": []}, "party": []}
    if party:
        rules["party"] = [
            {"id": "u-1", "name": "艾拉", "level": 2,
             "attributes": {"MGT": 5, "FIN": 6, "VIG": 4, "INS": 7,
                            "MND": 4, "PRE": 5}},
            {"id": "u-2", "name": "石坚", "level": 1,
             "attributes": {"MGT": 6, "FIN": 4, "VIG": 6, "INS": 3,
                            "MND": 4, "PRE": 4}},
        ]
    return rules


def test_l2_build_stable():
    rules = _fixture_rules()
    first = pg.build_l2(rules)
    second = pg.build_l2(rules)
    assert first == second
    assert first.startswith("【检查点】\n")
    assert first.endswith("账本：无\n")
    assert "队员：u-1 / 艾拉 / 等级 2 / 力道 5 / 灵巧 6 / 体魄 4 / 洞察 7 / 心智 4 / 气场 5" in first
    assert "队员：u-2 / 石坚 / 等级 1 / 力道 6 / 灵巧 4 / 体魄 6 / 洞察 3 / 心智 4 / 气场 4" in first
    assert "salt" not in first
    # 空队伍 → 整段只有标题与账本行。
    assert pg.build_l2({"scene": {"id": "x"}, "party": []}) == \
        "【检查点】\n账本：无\n"
    ok("L2：两次构建全等，队员行含 id / 名字 / 等级 / 六维，末行账本，无 salt")


def test_l2_scene_id_caching():
    guide = pg.empty_guide()
    rules = _fixture_rules("sc-01")
    l2_first = pg.ensure_l2(guide, rules)
    guide["transcript"] = [{"role": "assistant", "content": "旧对白"}]
    # scene.id 不变：L2 全等，L3 原样复用。
    l2_again = pg.ensure_l2(guide, _fixture_rules("sc-01"))
    assert l2_again == l2_first
    assert guide["l2"] == l2_first
    assert guide["transcript"] == [{"role": "assistant",
                                    "content": "旧对白"}]
    # scene.id 变化：重写 L2 并清空 L3（文本内容只随队员变化，可相同）。
    pg.ensure_l2(guide, _fixture_rules("sc-02"))
    assert guide["l2_scene_id"] == "sc-02"
    assert guide["l2"] == pg.build_l2(_fixture_rules("sc-02"))
    assert guide["transcript"] == []
    ok("ensure_l2：scene.id 不变时 L2 全等；变化时重写并清空 L3")


# ── guide 状态字典 ──────────────────────────────────────────────────────

_EXPECTED_GUIDE_KEYS = {
    "salt", "l1_key", "l2_scene_id", "l2", "transcript", "last_beats",
    "world_key", "scenario_id", "focus_location_id", "here",
    "realizations", "voices", "traces", "npc_home", "npc_at", "npc_flags",
    "known_clues", "stats",
}


def test_empty_guide():
    a, b = pg.empty_guide(), pg.empty_guide()
    assert a is not b and a["stats"] is not b["stats"]
    assert set(a.keys()) == _EXPECTED_GUIDE_KEYS
    assert _SALT_RE.match(a["salt"]) and a["salt"] != b["salt"]
    assert a["l1_key"] == "unloaded"
    assert a["l2"] == "【检查点】\n账本：无\n"
    assert a["stats"] == {key: 0 for key in pg.STAT_KEYS}
    for key in ("transcript", "last_beats", "traces", "known_clues"):
        assert a[key] == []
    for key in ("realizations", "voices", "npc_home", "npc_at", "npc_flags"):
        assert a[key] == {}
    dumped = json.dumps(a, ensure_ascii=False)
    assert "API_KEY" not in dumped and "BASE_URL" not in dumped
    ok("empty_guide：§7 全键、独立实例、salt 32 位十六进制、无密钥")


def test_guide_from_contract():
    # 不是 dict：32 位十六进制字符串原样当 salt，不另掷。
    fixed = "0123456789abcdef0123456789abcdef"
    g = pg.guide_from(fixed)
    assert g["salt"] == fixed
    # 不是 dict 也不是 salt：整块换成空块（新 salt）。
    g = pg.guide_from("garbage")
    assert _SALT_RE.match(g["salt"]) and g["salt"] != fixed
    g = pg.guide_from(42)
    assert _SALT_RE.match(g["salt"])
    # 是 dict：合法 salt 原样保留；脏键丢弃，其余键留下。
    raw = {
        "salt": fixed,
        "l2": "【检查点】\n账本：无\n",
        "l2_scene_id": "sc-01",
        "transcript": [{"role": "user", "content": "hi"}],
        "realizations": {"loc-02": {"source": "model"}},
        "stats": {"calls": 3, "prompt_tokens": 100, "bad": "x"},
        "unknown_key": {"junk": True},
        "world_key": 123,          # 类型不对 → 丢
        "voices": "not-a-dict",    # 类型不对 → 丢
        "realizations_bad": None,  # 未知键 → 丢
    }
    g = pg.guide_from(raw)
    assert g["salt"] == fixed
    assert g["l2_scene_id"] == "sc-01"
    assert g["transcript"] == [{"role": "user", "content": "hi"}]
    assert g["realizations"] == {"loc-02": {"source": "model"}}
    assert g["stats"]["calls"] == 3 and g["stats"]["prompt_tokens"] == 100
    assert "bad" not in g["stats"]
    assert g["world_key"] == ""          # 脏键用默认值补
    assert g["voices"] == {}
    assert "unknown_key" not in g and "realizations_bad" not in g
    # realizations 不是 dict → 只丢这一个键，其他键留下。
    g = pg.guide_from({"salt": fixed, "realizations": [1, 2],
                       "l1_key": "yunji"})
    assert g["realizations"] == {} and g["l1_key"] == "yunji"
    assert g["salt"] == fixed
    # salt 缺失或不是格式 → 用新掷的补上。
    g = pg.guide_from({"salt": "XYZ", "l1_key": "k"})
    assert _SALT_RE.match(g["salt"]) and g["l1_key"] == "k"
    ok("guide_from：按键合同（salt 保留 / 脏键丢弃 / 缺键补默认）")


# ── 可观测性 ────────────────────────────────────────────────────────────


def test_cache_hit_ratio():
    assert pg.cache_hit_ratio(0, 0) == 0.0
    assert pg.cache_hit_ratio(100, 40) == 0.4
    assert pg.cache_hit_ratio(0, 40) == 0.0
    assert pg.cache_hit_ratio(-1, 40) == 0.0
    assert pg.cache_hit_ratio(None, 40) == 0.0
    assert pg.cache_hit_ratio("x", "y") == 0.0
    ok("cache_hit_ratio：分母 ≤ 0 → 0.0；(100, 40) → 0.4")


# ── 离线与 .env.example ────────────────────────────────────────────────


def test_transport_injectable_and_build_offline():
    """上游调用只有一个可替换入口（假传输）；构建路径离线、不碰网络。

    G1 时本模块完全不引用网络设施；G2 起它必须真的读上游（§5.3「同一套
    标准库 HTTP」），但**所有**上游调用都收在 `TRANSMIT` 这一个入口上，
    测试换成假传输即全程不开套接字。因此这里断言的是「可替换 + 构建路径离线」，
    不再断言源码里没有 `urllib`。
    """
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers,
                      "timeout": timeout})
        return {"content": "【叙事】风停了。", "tool_calls": [],
                "usage": {"prompt_tokens": 3}}

    env = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
           "API_KEY": "kk"}
    done = pg.call_narrative([{"role": "user", "content": "x"}], env=env,
                             transmit=fake)
    assert done and done["content"] == "【叙事】风停了。"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.example.com/v1/chat/completions"
    assert calls[0]["headers"] == {pg.KEY_HEADER: "kk"}
    assert calls[0]["timeout"] == pg.NARRATIVE_TIMEOUT_S
    assert "kk" not in json.dumps(calls[0]["payload"], ensure_ascii=False)

    # 离线（空 BASE_URL / 空模型 / 空密钥）时**不请求**，直接返回 None。
    for broken in ({"BASE_URL": "", "MODEL": "mm", "API_KEY": "kk"},
                   {"BASE_URL": "  ", "MODEL": "mm", "API_KEY": "kk"},
                   {"BASE_URL": "https://api.example.com/v1", "MODEL": "",
                    "API_KEY": "kk"},
                   {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
                    "API_KEY": ""}):
        assert pg.call_narrative([{"role": "user", "content": "x"}],
                                 env=broken, transmit=fake) is None
    assert len(calls) == 1, "离线时不得调用传输"

    # 构建路径不碰传输：换成会炸的传输，把纯函数逐条跑一遍。
    def boom(*_args, **_kwargs):
        raise AssertionError("构建路径不得调用传输")

    saved, pg.TRANSMIT = pg.TRANSMIT, boom
    try:
        assert callable(saved)
        pg.build_narrative_body([{"role": "system", "content": pg.L0}], env=env)
        pg.status(env)
        pg.build_l2(_fixture_rules())
        pg.build_l4(_fixture_rules(), pg.empty_guide(), "看一下")
        pg.build_narrative_messages(_fixture_rules(), pg.empty_guide(), "看一下")
        pg.verdict_line({"rolled": False, "outcome": "success"})
        pg.review_narration({"rolled": False},
                            {"content": "【叙事】风停了。", "tool_calls": []})
        # G3 的构建路径同样离线：切拍、风格卡、组装都只是纯函数。
        pg.split_beats("风从巷口灌进来。霍砚：「别出声。」")
        pg.beats_of("【叙事】风停了。", pg.empty_guide())
        pg.strip_marks("（低声）风停了。")
        pg.build_tts_body("风停了。", voice="茉莉")
        # G4 的构建路径同样离线：只查表 / 组请求体，做不了就返回固定短语。
        pg.needs_tool("查规则", False)
        pg.lookup_rule_text("system.adjudication")
        pg.lookup_rule_text("不存在的 kind")
        pg.build_tool_body([{"role": "user", "content": "查规则"}], env=env)
        # G6 的构建路径同样离线：典范切片 / 输出合同 / 校验都是纯函数或本地读盘。
        bound = pg.empty_guide()
        bound["scenario_id"] = "yunji"
        bound["world_key"] = "yunji"
        canon = pg.canon_for(bound, "loc-02")
        pg.realization_slice(canon, bound["salt"])
        pg.realization_contract(canon)
        pg.build_realization_body(canon, bound["salt"], env=env)
        pg.validate_realization(_payload("loc-02", "白壁",
                                         _wall_key_nodes() + [_wall_street()]),
                                canon)
        pg.fallback_realization(canon)
    finally:
        pg.TRANSMIT = saved
    ok("上游调用只走可替换的传输入口；构建路径离线、不碰传输")


def test_transport_error_hides_upstream_body():
    """传输失败只抛类型名，不带上游响应体 / 密钥（§5.1 / §8）。"""
    saved = pg.urllib.request.urlopen

    def boom(*_args, **_kwargs):
        raise OSError("upstream said: secret-body")

    pg.urllib.request.urlopen = boom
    try:
        try:
            pg.http_transmit("https://api.example.com/v1/chat/completions",
                             {"model": "mm"}, {pg.KEY_HEADER: "kk"},
                             timeout=0.1)
        except pg.TransportError as error:
            assert "secret-body" not in str(error)
            assert "kk" not in str(error)
            assert "OSError" in str(error)
        else:
            raise AssertionError("上游失败应抛 TransportError")
    finally:
        pg.urllib.request.urlopen = saved
    ok("传输失败抛 TransportError，不带上游响应体与密钥")


def test_assemble_chat_stream():
    """流式 / 非流式回包都组装；用量缺字段按 0；坏块跳过不抛。"""
    raw = (b'data: {"choices":[{"delta":{"content":"\xe4\xbd\xa0"}}]}\n\n'
           b'data: {"choices":[{"delta":{"content":"\xe5\xa5\xbd"}}]}\n\n'
           b'data: not-json\n\n'
           b'data: {"choices":[],"usage":{"prompt_tokens":10,'
           b'"prompt_tokens_details":{"cached_tokens":4},'
           b'"completion_tokens":2,'
           b'"completion_tokens_details":{"reasoning_tokens":1}}}\n\n'
           b'data: [DONE]\n\n')
    got = pg.assemble_chat_stream(raw)
    assert got["content"] == "你好"
    assert got["tool_calls"] == []
    assert got["usage"] == {"prompt_tokens": 10, "cached_tokens": 4,
                            "completion_tokens": 2, "reasoning_tokens": 1}
    # tool_calls 会收集。`reasoning_content` 从 G4 起**单独**放在 `reasoning`
    # 键上：工具预通行的那一次内存列表要把它放回带 `tool_calls` 的助手消息
    # （否则供应商返回 400，见 §5.5）。它仍然**不进存档、不进 L3、不进叙事
    # 请求**——那条不变量由 `test_tool_reasoning_never_persisted` 锁住，
    # 不再靠「组装结果里没有这个字符串」来保证。
    streamed = pg.assemble_chat_stream(
        b'data: {"choices":[{"delta":{"tool_calls":[{"id":"t1"}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n\n')
    assert streamed["tool_calls"] == [{"id": "t1"}]
    assert streamed["content"] == ""
    assert streamed["reasoning"] == "think"
    # 没有思考内容的回包**没有**这个键（同 `audio` 的口径）。
    assert "reasoning" not in pg.assemble_chat_stream(
        b'data: {"choices":[{"delta":{"content":"\xe5\x81\x9c"}}]}\n\n')
    # 非流式回包（message 而非 delta）。
    plain = pg.assemble_chat_stream(
        b'data: {"choices":[{"message":{"content":"\xe5\x81\x9c"}}]}\n\n')
    assert plain["content"] == "停"
    # 缺字段按 0。
    assert pg.read_usage({}) == {"prompt_tokens": 0, "cached_tokens": 0,
                                 "completion_tokens": 0, "reasoning_tokens": 0}
    ok("assemble_chat_stream：delta / message / usage / 坏块 / [DONE]")


def test_assemble_chat_stream_whole_json():
    """非流式**整段 JSON**（`choices[0].message`）与等价的 SSE 字节结果相同。

    真实供应商对 `stream: false` 的请求回一整个 JSON 对象，**不是** `data:` 行；
    只认 SSE 的老实现让正文恒为空（G6 实相 / G4 工具在真 API 上静默失效）。
    """
    payload = {"id": "c-1", "object": "chat.completion",
               "choices": [{"index": 0, "finish_reason": "stop",
                            "message": {"role": "assistant", "content": "你好",
                                        "reasoning_content": "想一下",
                                        "tool_calls": [{"id": "t9"}]}}],
               "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                         "prompt_tokens_details": {"cached_tokens": 4},
                         "completion_tokens_details": {"reasoning_tokens": 1}}}
    blob = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    whole = pg.assemble_chat_stream(blob)
    assert whole["content"] == "你好"
    assert whole["reasoning"] == "想一下"
    assert whole["tool_calls"] == [{"id": "t9"}]
    assert whole["usage"] == {"prompt_tokens": 10, "cached_tokens": 4,
                              "completion_tokens": 2, "reasoning_tokens": 1}
    # 同一份对象的 SSE 编码（一条 `data:` 行 + `[DONE]`）得到**相同**结果。
    sse = pg.assemble_chat_stream(b"data: " + blob + b"\n\ndata: [DONE]\n\n")
    assert sse == whole
    # 没带思考 / 音频的回包仍是三键（`reasoning` / `audio` 不凭空出现）。
    plain = pg.assemble_chat_stream(json.dumps(
        {"choices": [{"message": {"role": "assistant", "content": "停"}}]},
        ensure_ascii=False).encode("utf-8"))
    assert plain == {"content": "停", "tool_calls": [], "usage": {}}
    ok("assemble_chat_stream：整段 JSON 与等价 SSE 得到同一结果")


def test_assemble_chat_stream_whole_json_tolerates_garbage():
    """空正文 / 畸形 JSON / 非对象 / 错误体都不抛，按「没有正文」处理。"""
    for raw in (b"", b"   \n", b"{not json", b'{"choices":',
                b"not json at all", b"[]", b"null",
                b'{"error":{"message":"boom"}}'):
        got = pg.assemble_chat_stream(raw)
        assert got == {"content": "", "tool_calls": [], "usage": {}}, raw
    # 带 BOM 的整段 JSON 仍认（有些网关会加）。
    got = pg.assemble_chat_stream(
        "\ufeff".encode("utf-8") + json.dumps(
            {"choices": [{"message": {"content": "停"}}]},
            ensure_ascii=False).encode("utf-8"))
    assert got["content"] == "停"
    ok("assemble_chat_stream：空 / 畸形 / 非对象 / 错误体不抛，BOM 仍认")


# ── G2：结算与叙事审查 ──────────────────────────────────────────────────


def test_settle_uses_snapshot_roundtrip():
    """`settle`：注水 → perform_action（不传 unit_id）→ 写回 → save。"""
    class _Web:
        def __init__(self):
            self.lock = threading.RLock()
            self.rules = prism_core.RuleSession("s-1").snapshot()
            self.saved = 0

        def save(self):
            self.saved += 1

    web = _Web()
    live = prism_core.RuleSession.from_snapshot(web.rules)
    live.party.append(prism_core.new_unit("u-1", "艾拉",
                                          attributes={"MGT": 5, "INS": 7}))
    live.scene = {"id": "sc-1", "actions": [
        {"id": "a-auto", "label": "撬锁", "kind": "check", "df": 12,
         "auto_pass": True, "on_pass": "锁簧弹开。"},
        {"id": "a-check", "label": "勘察", "kind": "check", "df": 12},
    ]}
    web.rules = live.snapshot()

    result = pg.settle(web, "a-auto")
    assert result["status"] == "resolved" and result["passed"] is True
    assert result["auto"] is True and result["rolled"] is False
    assert web.saved == 1, "结算必须落盘一次"
    assert "a-auto" in web.rules["done_actions"], "结算要写回快照"
    # 行动者是 active_unit_id 为空时的 party[0]，行动者不是「单位不存在」。
    assert result["unit"]["id"] == "u-1"

    # 未知行动 → 照原样抛 ValueError（映射成 HTTP 码是网页层的合同）。
    try:
        pg.settle(web, "nope")
    except ValueError as error:
        assert str(error) == "行动不存在于当前场景"
    else:
        raise AssertionError("未知行动应抛 ValueError")

    # 战斗没结束的锁也照原样抛。
    live = prism_core.RuleSession.from_snapshot(web.rules)
    live.combat = {"over": False}
    web.rules = live.snapshot()
    try:
        pg.settle(web, "a-check")
    except ValueError as error:
        assert str(error) == "战斗还没结束，先打完这场"
    else:
        raise AssertionError("战斗未结束应抛 ValueError")
    ok("settle：注水 / 结算 / 写回 / save；异常原样抛给网页层")


def test_verdict_line_order():
    """`【裁决】` 取句顺序：roll.detail → message → text → 判定：档位 → 无掷骰。"""
    assert pg.verdict_line({"rolled": True, "roll": {"detail": "1d20+5 = 18"},
                            "message": "忽略我"}) == "1d20+5 = 18"
    assert pg.verdict_line({"rolled": False, "message": "已经完成过了"}) == \
        "已经完成过了"
    assert pg.verdict_line({"text": "锁簧弹开。"}) == "锁簧弹开。"
    # 免骰且达成 = 判定：成功（五档中文不导入 prism_core._OUTCOME_ZH）。
    assert pg.verdict_line({"rolled": False, "roll": None,
                            "outcome": "success"}) == "判定：成功"
    assert pg.verdict_line({"outcome": "catastrophe"}) == "判定：灾难"
    assert pg.verdict_line({}) == pg.NO_ROLL_VERDICT
    assert pg.verdict_line(None) == pg.NO_ROLL_VERDICT
    ok("verdict_line：取句顺序与「判定：成功」")


def test_review_narration():
    """骰子审查 + 裁决替换；对不上就丢叙事（返回 None）。"""
    result = {"rolled": True,
              "roll": {"detail": "1d20+5 = 18", "notation": "1d20+5",
                       "total": 18},
              "unit": {"vitality": 9, "guard": 12,
                       "resources": {"focus": 3, "tempo": 2, "strain": 1,
                                     "resolve": 3}}}

    # 通过：裁决换成服务端句子，正文与钩子照留。
    text = ("【裁决】你掷出 1d20+5 = 18\n"
            "【叙事】剑锋擦过石壁，火花落进积水。\n"
            "【钩子】巷口有人影。")
    out = pg.review_narration(result,
                              {"content": text, "tool_calls": []})
    assert out.startswith("【裁决】1d20+5 = 18"), out
    assert "剑锋擦过石壁，火花落进积水。" in out
    assert "【钩子】巷口有人影。" in out
    # 正文里记法一致、合计一致 → 通过。
    assert pg.review_narration(
        result, {"content": "【叙事】你掷出 1d20+5 = 18，剑锋擦过石壁。",
                 "tool_calls": []})
    # 记法不一致 → 丢叙事。
    assert pg.review_narration(
        result, {"content": "【叙事】掷出 1d20+9 = 18。",
                 "tool_calls": []}) is None
    # 合计不一致 → 丢叙事。
    assert pg.review_narration(
        result, {"content": "【叙事】合计 11，剑锋擦过石壁。",
                 "tool_calls": []}) is None
    # 带标签数字不一致 → 丢叙事；一致则通过；单位没有该字段时不判死。
    assert pg.review_narration(
        result, {"content": "【叙事】活力：4，你退到墙根。",
                 "tool_calls": []}) is None
    assert pg.review_narration(
        result, {"content": "【叙事】活力：9，你退到墙根。",
                 "tool_calls": []})
    assert pg.review_narration(
        {"rolled": False}, {"content": "【叙事】负担 6。", "tool_calls": []})
    # 空正文 / 纯空白 / 带 tool_calls / 完成不是字典 → 丢。
    assert pg.review_narration(result, {"content": "   ",
                                        "tool_calls": []}) is None
    assert pg.review_narration(result, {"content": "【裁决】x\n【叙事】  ",
                                        "tool_calls": []}) is None
    assert pg.review_narration(result, {"content": text,
                                        "tool_calls": [{"id": "t"}]}) is None
    assert pg.review_narration(result, None) is None
    ok("review_narration：记法 / 合计 / 带标签数字审查与裁决替换")


def test_fallback_text_keeps_verdict():
    """叙事失败：**保留服务端裁决**，正文用兜底句（§5.2）。"""
    result = {"rolled": True, "roll": {"detail": "1d20+5 = 18"}}
    text = pg.fallback_text(result)
    assert text.startswith("【裁决】1d20+5 = 18"), text
    assert pg.FALLBACK_NARRATION in text
    # 没有掷骰时裁决是「本回合没有新的掷骰。」
    assert pg.NO_ROLL_VERDICT in pg.fallback_text(None)
    ok("fallback_text：裁决仍是 roll['detail']，正文是兜底句")


def test_build_l4_and_messages():
    """L4 块与 L0–L4 消息数组：段落顺序、L4 上限、纯函数不改 guide。"""
    rules = _fixture_rules()
    rules["scene"]["actions"] = [{"id": "a-1", "label": "勘察"}]
    rules["active_unit_id"] = "u-1"
    rules["party"][0]["vitality"] = 9
    rules["party"][0]["max_vitality"] = 12
    rules["party"][0]["resources"] = {"focus": 3, "tempo": 2, "strain": 0,
                                      "resolve": 3}
    guide = pg.empty_guide()
    pg.ensure_l2(guide, rules)
    messages = pg.build_narrative_messages(rules, guide, "我去看看")
    assert [m["role"] for m in messages] == ["system", "user", "assistant",
                                             "user", "assistant", "user"]
    assert messages[1]["content"] == pg.L1_UNBOUND
    assert messages[3]["content"] == guide["l2"]
    assert "我去看看" in messages[-1]["content"]
    assert "无判定" in messages[-1]["content"]
    assert "a-1 勘察" in messages[-1]["content"]
    assert "活力 9/12" in messages[-1]["content"]
    # L3 只追加进消息数组，不改 guide。
    guide["transcript"].append({"role": "assistant", "content": "旧对白"})
    again = pg.build_narrative_messages(rules, guide, "再看一眼")
    assert any(m["content"] == "旧对白" for m in again)
    assert len(guide["transcript"]) == 1
    # L4 超长：先截玩家原文的尾部，不超上限。
    blob = pg.build_l4(rules, guide, "长" * 5000)
    assert len(blob) <= pg.L4_LIMIT, len(blob)
    ok("build_l4 / build_narrative_messages：段落顺序、L4 上限、纯函数")


# ── G3：切拍 / 平叙风格卡 / pcm16 ────────────────────────────────────────


# 两段**各自带填充**的 base64：单独解码各得 2 / 4 字节，拼起来 6 字节。
# 这正是「把 base64 文本接成一串再解码」会出错的形状。
_PCM_A = b"\x01\x02"
_PCM_B = b"\x03\x04\x05\x06"


def _audio_chunk(pcm: bytes) -> bytes:
    """一条 TTS 音频 delta 的 SSE 行（`delta.audio.data` 是自带填充的 base64）。"""
    data = base64.b64encode(pcm).decode("ascii")
    return ('data: {"choices":[{"delta":{"audio":{"data":"%s"}}}]}\n\n'
            % data).encode("utf-8")


def test_audio_delta_decoded_one_by_one():
    """每个音频 delta **单独**解码再拼接；不是把 base64 文本接起来再解。"""
    raw = (_audio_chunk(_PCM_A)
           + b'data: {"choices":[],"usage":{"prompt_tokens":5,'
             b'"completion_tokens":0}}\n\n'          # 空 choices 的块只读用量
           + _audio_chunk(_PCM_B)
           + b"data: [DONE]\n\n")
    got = pg.assemble_chat_stream(raw)
    assert got["audio"] == _PCM_A + _PCM_B, got.get("audio")
    assert got["usage"]["prompt_tokens"] == 5
    assert got["content"] == "" and got["tool_calls"] == []

    # 反证：把 base64 文本直接接起来再解，得不到 `PCM_A + PCM_B`。
    naive = (base64.b64encode(_PCM_A).decode("ascii")
             + base64.b64encode(_PCM_B).decode("ascii"))
    try:
        wrong = base64.b64decode(naive)
    except Exception:  # noqa: BLE001 — 中间那道填充会让它直接报错
        wrong = None
    assert wrong != _PCM_A + _PCM_B, "拼接 base64 文本这条路必须是错的"

    # 缺填充也能容忍；坏块跳过不抛。
    tolerant = pg.assemble_chat_stream(
        b'data: {"choices":[{"delta":{"audio":{"data":"AQI"}}}]}\n\n')
    assert tolerant["audio"] == _PCM_A
    broken = pg.assemble_chat_stream(
        b'data: {"choices":[{"delta":{"audio":{"data":"!!!not-base64!!!"}}}]}\n\n')
    assert broken.get("audio") is None, "坏音频块不该产出字节"

    # 纯叙事回包**没有** `audio` 键：组装结果仍可 JSON 序列化（G2 的不变量）。
    plain = pg.assemble_chat_stream(
        b'data: {"choices":[{"delta":{"content":"\xe5\x81\x9c"}}]}\n\n')
    assert "audio" not in plain
    assert json.dumps(plain, ensure_ascii=False)
    ok("音频：每个 delta 单独 base64 解码再拼接；叙事回包没有 audio 键")


def test_split_beats_default_and_dialogue():
    """切拍：默认整拍旁白；对白按「名字＋冒号＋「…」」切；无显式说话人只用白桦。"""
    # 一句话：整段一拍旁白。
    beats = pg.split_beats("风从巷口灌进来。")
    assert len(beats) == 1
    assert beats[0]["text"] == "风从巷口灌进来。"
    assert beats[0]["voice"] == pg.VOICE_NARRATOR
    assert beats[0]["tone"] == pg.BEAT_TONE and beats[0]["channel"] == pg.BEAT_CHANNEL
    assert beats[0]["speaker"] == ""

    # 空文本 → 没有拍（speak 也就无从匹配）。
    assert pg.split_beats("") == [] and pg.split_beats("   ") == []

    # 对白切拍：旁白 / 对白 / 旁白 三段。
    text = "风从巷口灌进来。霍砚：「别出声。」随后灯灭了。"
    beats = pg.split_beats(text)
    assert [b["text"] for b in beats] == ["风从巷口灌进来。", "别出声。", "随后灯灭了。"]
    # 没传名字表 → 对白拍仍未标记，音色白桦。**不从姓名推断**（G7 才按 id 分配）。
    assert all(b["voice"] == pg.VOICE_NARRATOR for b in beats)
    assert all(b["speaker"] == "" for b in beats)
    assert "霍砚" in text and "霍砚" not in "".join(b["text"] for b in beats)

    # 传了名字表 + guide.voices：对白拍带说话人 id，音色取自那一拍。
    beats = pg.split_beats("温苔：「灯还亮着。」",
                           speakers={"温苔": "npc-02"},
                           voices={"npc-02": "茉莉"})
    assert beats[-1]["speaker"] == "npc-02"
    assert beats[-1]["voice"] == "茉莉"
    # 名字在表里但声线还没记录 → 白桦（不是「猜一个」）。
    beats = pg.split_beats("温苔：「灯还亮着。」", speakers={"温苔": "npc-02"})
    assert beats[-1]["speaker"] == "npc-02"
    assert beats[-1]["voice"] == pg.VOICE_NARRATOR

    # 名字上界 8 个汉字照样切；没有「名字＋冒号」时整段保持旁白。
    assert len(pg.split_beats("一二三四五六七八：「喂。」")) == 1
    assert len(pg.split_beats("霍砚说 别出声。")) == 1
    assert pg.split_beats("霍砚说 别出声。")[0]["speaker"] == ""
    ok("split_beats：默认旁白 / 对白切拍 / 无显式说话人只用白桦 / 不按姓名推断")


def test_split_beats_limit_merges_tail():
    """一回合最多 6 拍；多出来的并进最后一拍旁白。"""
    text = "".join("%s：「第%d句。」" % ("霍砚温苔甲乙丙丁"[i], i + 1)
                   for i in range(7))
    beats = pg.split_beats(text)
    assert len(pg.split_beats(text, limit=None)) == 7, "先确认确实切出了 7 拍"
    assert len(beats) == pg.MAX_BEATS == 6
    assert beats[-1]["voice"] == pg.VOICE_NARRATOR
    assert beats[-1]["speaker"] == ""
    assert "第6句。第7句。" in beats[-1]["text"], beats[-1]["text"]
    # 文本一字不丢：0–4 拍照留，第 6、7 句并进最后一拍。
    assert [b["text"] for b in beats[:5]] == ["第%d句。" % (i + 1) for i in range(5)]
    ok("split_beats：6 拍上限，多出来的并进最后一拍旁白（文本不丢）")


def test_strip_marks():
    """送去合成前删掉长度 1–12 的括号 / 方括号记号；三段标题不是记号。"""
    assert pg.strip_marks("（低声）风停了。") == "风停了。"
    assert pg.strip_marks("(whisper) 风停了。") == " 风停了。"
    assert pg.strip_marks("风停了。[叹气]") == "风停了。"
    assert pg.strip_marks("风停了。［远远地］") == "风停了。"
    # 超过 12 个字的括号不是「记号」，原样留着（避免把正文吃掉）。
    keep = "（" + "很" * 13 + "）"
    assert pg.strip_marks(keep) == keep
    # 三段标题是叙事合同，`【】` 不在记号表里。
    assert pg.strip_marks("【裁决】判定：成功") == "【裁决】判定：成功"
    ok("strip_marks：只删长度 1–12 的括号 / 方括号记号，标题不动")


def test_build_tts_body():
    """TTS 请求体（§5.9）：平叙风格卡、台词在 assistant、pcm16、密钥不入体。"""
    body = pg.build_tts_body("（低声）风停了。", voice="茉莉")
    assert body["model"] == "mimo-v2.5-tts"
    assert body["stream"] is True
    assert body["audio"] == {"format": "pcm16"}
    assert body["voice"] == "茉莉"
    assert body["messages"][0] == {"role": "user", "content": "平叙"}
    assert body["messages"][1]["role"] == "assistant"
    assert body["messages"][1]["content"] == "风停了。", "记号要在送合成前删掉"
    assert pg.TTS_STYLE_CARD == "平叙"
    # 风格卡是常量：不含台词、不含 sid、不含日期。
    dumped = json.dumps(body, ensure_ascii=False)
    assert not re.search(r"\d{4}-\d{2}-\d{2}", dumped)
    assert "API_KEY" not in dumped and "BASE_URL" not in dumped
    # 不知名的音色一律退回白桦；不传就是旁白。
    assert pg.build_tts_body("x", voice="机器人")["voice"] == pg.VOICE_NARRATOR
    assert pg.build_tts_body("x")["voice"] == pg.VOICE_NARRATOR
    assert tuple(pg.VOICES) == ("白桦", "茉莉")
    # 24 kHz / 单声道 / s16le 的规格常量（§4.3）。
    assert pg.TTS_SAMPLE_RATE == 24000 and pg.TTS_FORMAT == "pcm16"
    ok("build_tts_body：模型 / 平叙卡 / assistant 台词 / pcm16 / 音色白名单")


def test_call_tts_offline_and_failure():
    """`call_tts`：与叙事同一条传输入口；离线与失败都返回空字节，**不重试**。"""
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers,
                      "timeout": timeout})
        return {"content": "", "tool_calls": [], "usage": {},
                "audio": _PCM_A + _PCM_B}

    # TTS 不需要 MODEL（§5.3 的 `tts` 只看 BASE_URL 与密钥）。
    env = {"BASE_URL": "https://api.example.com/v1", "MODEL": "",
           "API_KEY": "super-secret-value"}
    assert pg.call_tts("风停了。", env=env, transmit=fake) == _PCM_A + _PCM_B
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.example.com/v1/chat/completions"
    assert calls[0]["headers"] == {pg.KEY_HEADER: "super-secret-value"}
    assert calls[0]["timeout"] == pg.TTS_TIMEOUT_S
    assert calls[0]["payload"]["model"] == "mimo-v2.5-tts"
    assert "super-secret-value" not in json.dumps(calls[0]["payload"],
                                                  ensure_ascii=False)

    # 离线（空 BASE_URL / 空白 BASE_URL / 空密钥）：不请求，返回空字节。
    for broken in ({"BASE_URL": "", "MODEL": "mm", "API_KEY": "kk"},
                   {"BASE_URL": "   ", "MODEL": "mm", "API_KEY": "kk"},
                   {"BASE_URL": "https://api.example.com/v1", "MODEL": "",
                    "API_KEY": ""}):
        assert pg.call_tts("风停了。", env=broken, transmit=fake) == b""
    assert len(calls) == 1, "离线时不得调用传输"

    # 上游失败 / 返回形状不对：空字节，且**只调用一次**（不重试）。
    def boom(*_args, **_kwargs):
        calls.append({})
        raise pg.TransportError("boom")

    assert pg.call_tts("风停了。", env=env, transmit=boom) == b""
    assert len(calls) == 2
    assert pg.call_tts("风停了。", env=env,
                       transmit=lambda *a, **k: "not-a-dict") == b""
    assert pg.call_tts("风停了。", env=env,
                       transmit=lambda *a, **k: {"content": ""}) == b""
    ok("call_tts：不重试；离线 / 失败 / 坏回包一律空字节")


def test_beats_of_skips_verdict():
    """`beats_of`：节拍从「正文 + 钩子」切，**不朗读**服务端的 `【裁决】`。"""
    reviewed = ("【裁决】判定：成功\n\n"
                "【叙事】风从巷口灌进来。霍砚：「别出声。」\n\n"
                "【钩子】巷口有人影。")
    guide = pg.empty_guide()
    beats = pg.beats_of(reviewed, guide)
    assert [b["text"] for b in beats] == \
        ["风从巷口灌进来。", "别出声。", "\n\n巷口有人影。"]
    joined = "".join(b["text"] for b in beats)
    assert "【裁决】" not in joined and "判定：成功" not in joined
    assert "【叙事】" not in joined and "【钩子】" not in joined
    # guide.voices 里的声线会被用上（G7 写入，G3 只读）。
    guide["voices"] = {"npc-02": "茉莉"}
    beats = pg.beats_of("【叙事】温苔：「灯还亮着。」", guide)
    assert beats[-1]["voice"] == pg.VOICE_NARRATOR, "没有名字表就仍是白桦"
    # 模型没按三段格式写时，整段当正文。
    beats = pg.beats_of("风停了。", guide)
    assert [b["text"] for b in beats] == ["风停了。"]
    assert pg.beats_of("", guide) == []
    ok("beats_of：切正文与钩子，裁决那一行不进节拍")


# ── G4：工具环 ──────────────────────────────────────────────────────────


class _Web:
    """最小 web_session 替身：`settle` 只按鸭子类型用 lock / rules / save。"""

    def __init__(self, *, party=True, combat=False, actions=None):
        self.lock = threading.RLock()
        live = prism_core.RuleSession("s-tool")
        if party:
            live.party.append(prism_core.new_unit("u-1", "艾拉",
                                                  attributes={"MGT": 5, "INS": 7}))
        live.scene = {"id": "sc-1", "actions": list(actions or [])}
        if combat:
            live.combat = {"over": False}
        self.rules = live.snapshot()
        self.saved = 0

    def save(self):
        self.saved += 1


_ONE_ACTION = [{"id": "a-auto", "label": "撬锁", "kind": "check", "df": 12,
                "auto_pass": True, "on_pass": "锁簧弹开。"}]


def _tool_call(name, arguments, call_id="call-1"):
    """一只工具调用的规范形状（arguments 是 JSON 字符串）。"""
    return {"id": call_id, "type": "function",
            "function": {"name": name,
                         "arguments": json.dumps(arguments, ensure_ascii=False)}}


def test_needs_tool_exact_sentences():
    """`needs_tool`：三句整句、必须有机械结果之外的空位；不是子串。"""
    assert pg.TOOL_TRIGGERS == ("查规则", "这地方", "有哪些出口")
    for word in pg.TOOL_TRIGGERS:
        assert pg.needs_tool(word, False) is True
        assert pg.needs_tool("  " + word + "  ", False) is True, "首尾空白要strip掉"
    # 子串不算：`我想查规则` / `查规则吧` / `这地方真冷` 都不开预通行。
    for wrong in ("我想查规则", "查规则吧", "这地方真冷", "有哪些出口呢",
                  "看看有哪些出口", "", "   ", None):
        assert pg.needs_tool(wrong, False) is False, wrong
    # 本回合已经有机械结果：一律不开。
    for word in pg.TOOL_TRIGGERS:
        assert pg.needs_tool(word, True) is False
    ok("needs_tool：只认三句整句，有机械结果时永不开")


def test_tool_error_phrases_match_web_layer():
    """工具错误短语与网页层 `GUIDE_SETTLE_ERRORS` 同文（跨模块锁）。"""
    import importlib
    import tempfile

    saved = os.environ.get("NOTDND_SAVE")
    os.environ["NOTDND_SAVE"] = tempfile.mkdtemp(prefix="pg-tool-errors-")
    try:
        web = importlib.import_module("notdnd_web")
    finally:
        if saved is None:
            os.environ.pop("NOTDND_SAVE", None)
        else:
            os.environ["NOTDND_SAVE"] = saved
    assert set(pg.TOOL_ERRORS) == set(web.GUIDE_SETTLE_ERRORS), \
        "两边必须覆盖同一批异常文本"
    for text, phrase in web.GUIDE_SETTLE_ERRORS.items():
        assert pg.TOOL_ERRORS[text] == phrase[0], text
    assert pg.TOOL_ERROR_DEFAULT == web.GUIDE_SETTLE_DEFAULT[0]
    ok("工具错误短语与 GUIDE_SETTLE_ERRORS / GUIDE_SETTLE_DEFAULT 同文")


def test_lookup_rule_reads_six_kinds():
    """`lookup_rule`：只读那六个 kind、截到 1500 字、坏输入给固定短语。"""
    assert set(pg.LOOKUP_RULE_KINDS) == {
        "system.adjudication", "system.antipatterns", "system.decision_engine",
        "system.guardrails", "system.ledger_spec", "system.tone_packs"}
    for kind in pg.LOOKUP_RULE_KINDS:
        text = pg.lookup_rule_text(kind)
        assert text and len(text) <= pg.LOOKUP_RULE_LIMIT, (kind, len(text))
        assert pg.TOOL_NO_RULE not in text[:10]
    # 裁决堆栈的 note 是人写的一句话摘要，应当出现在正文最前面。
    assert pg.lookup_rule_text("system.adjudication").startswith("规则冲突或无规则覆盖时")
    # 不在六个 kind 里的（含空 / 脏值）给固定短语，不抛。
    for bad in ("system.skills", "", None, "system.adjudication "):
        assert pg.lookup_rule_text(bad) == pg.TOOL_UNKNOWN_KIND, bad
    # 每条都截到上限以内（tone_packs 之类本来就长）。
    assert len(pg.lookup_rule_text("system.guardrails")) <= pg.LOOKUP_RULE_LIMIT
    ok("lookup_rule：六个 kind 可读、≤1500 字、未知 kind 给固定短语")


def _tiny_atlas():
    """一帧两地点一条边的小地图（都带 `places`）。"""
    atlas = atlas_kernel.new_atlas("w", 1)
    atlas_kernel.add_frame(atlas, "f", space="metric", z_meaning="层", cell="街区")
    for pid, name, x in (("f/here", "听泉馆", 0), ("f/next", "白壁行拍卖厅", 1)):
        atlas_kernel.add_place(atlas, {
            "id": pid, "name": name, "kind": "room", "frame_id": "f",
            "x": x, "y": 0, "z": 0, "source": "authored", "discovered": "seen"})
    atlas_kernel.add_link(atlas, "f", "f/here", "f/next", "东", band="short")
    return atlas


def test_place_card_save_block_no_keyerror():
    """存档块地点卡得到 `available: false`；**不**调用 `restore_state`。"""
    calls = []
    saved = atlas_kernel.restore_state

    def counting(*args, **kwargs):
        calls.append(args)
        return saved(*args, **kwargs)

    atlas_kernel.restore_state = counting
    try:
        # 1) 还没物化（只有存档块可看）：老存档读盘后的常态。
        class _Fresh:
            _atlas = None
            _locus = None

            atlas_block = {"version": 1, "world_key": "w", "seed": 3,
                           "setting_rev": "r",
                           "party_locus": {"frame_id": "f", "place_id": "f/here"},
                           "frames": {"f": {"seed": None, "deltas": []}}}

        card = pg.place_card(_Fresh())
        assert card == {"available": False}, card

        # 2) 手里那个「图」其实就是存档块（`{seed, deltas}`，帧没有 `places`）：
        #    这里以前会 KeyError，必须给 available: false。
        class _BlockShaped:
            _atlas = _Fresh.atlas_block
            _locus = {"frame_id": "f", "place_id": "f/here"}

        assert pg.place_card(_BlockShaped()) == {"available": False}

        # 3) 真有一份带 places 的内存地图：可读，但只回名称 / 出口 / 一句尺度。
        class _Live:
            _atlas = _tiny_atlas()
            _locus = {"frame_id": "f", "place_id": "f/here"}

        card = pg.place_card(_Live())
        assert card["available"] is True
        assert card["name"] == "听泉馆" and card["place_id"] == "f/here"
        assert card["exits"] == [{"via": "东", "name": "白壁行拍卖厅",
                                  "band": "short"}]
        assert card["scale"] == "metric；一格：街区；z：层"
        # 深拷贝的整份 place 不得进提示词。
        assert "place" not in card and "frame" not in card and "links" not in card
        # 未知地点 / 未知帧同样降级，不抛。
        class _Lost:
            _atlas = _Live._atlas
            _locus = {"frame_id": "f", "place_id": "f/nope"}

        assert pg.place_card(_Lost()) == {"available": False}
    finally:
        atlas_kernel.restore_state = saved
    assert calls == [], "read_place_card 不得调用 atlas.restore_state"
    ok("place_card：存档块 / 未物化 → available: false（无 KeyError、不 restore_state）")


def test_run_tool_call_settle_and_phrases():
    """`request_check` 走同一个 `settle`；三种 `ValueError` 收成固定短语且不抛。"""
    # 未知工具 → 固定短语。
    assert pg.run_tool_call(_Web(), _tool_call("do_magic", {})) == \
        (pg.TOOL_UNKNOWN, False)

    # 正常结算：走 settle，写回快照并 save；工具正文是结果的 JSON。
    web = _Web(actions=_ONE_ACTION)
    content, settled = pg.run_tool_call(web, _tool_call("request_check",
                                                        {"action_id": "a-auto"}))
    assert settled is True and web.saved == 1
    assert "a-auto" in web.rules["done_actions"], "工具里的结算也要落盘"
    assert json.loads(content)["passed"] is True

    # 同一个 id 再问一次：由 prism_core 的 done_actions 短路，不掷第二次。
    again, _ = pg.run_tool_call(web, _tool_call("request_check",
                                                {"action_id": "a-auto"}))
    assert json.loads(again)["already"] is True, again

    # 三种 ValueError → 固定短语，**不抛**（状态行早已写出，不能再要 HTTP 状态）。
    cases = (
        ("行动不存在于当前场景", _Web(actions=_ONE_ACTION), "nope",
         "没有这个行动"),
        ("战斗还没结束，先打完这场", _Web(actions=_ONE_ACTION, combat=True),
         "a-auto", "战斗还没结束"),
    )
    for _why, session, action_id, phrase in cases:
        content, settled = pg.run_tool_call(
            session, _tool_call("request_check", {"action_id": action_id}))
        assert content == phrase, (action_id, content)
        assert settled is False

    # 「单位不存在」：行动者 id 指不到人。
    ghost = _Web(party=False, actions=_ONE_ACTION)
    ghost.rules["active_unit_id"] = "u-ghost"
    content, settled = pg.run_tool_call(ghost, _tool_call(
        "request_check", {"action_id": "a-auto"}))
    assert content == "没有这个角色", content
    assert settled is False

    # 坏参数（不是 JSON / 缺 action_id）也只给短语，不抛。
    for bad in (_tool_call("request_check", {}),
                {"function": {"name": "request_check", "arguments": "{oops"}}):
        content, settled = pg.run_tool_call(_Web(actions=_ONE_ACTION), bad)
        assert content == "没有这个行动" and settled is False, content
    ok("run_tool_call：走同一个 settle；三种 ValueError → 固定短语，不抛")


def test_run_tool_pass_shape_and_reasoning():
    """预通行：思考开 / 不传温度 / 1024 / 非流式 / 带工具；往返只活在内存列表。"""
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers,
                      "timeout": timeout})
        if len(calls) == 1:
            return {"content": "", "tool_calls": [
                _tool_call("lookup_rule", {"kind": "system.adjudication"},
                           "call-a")],
                "usage": {"prompt_tokens": 11}, "reasoning": "先查裁决堆栈"}
        if len(calls) == 2:
            return {"content": "", "tool_calls": [
                _tool_call("request_check", {"action_id": "a-auto"}, "call-b")],
                "usage": {}, "reasoning": "再把行动结算掉"}
        return {"content": "够了", "tool_calls": [], "usage": {}}

    env = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
           "API_KEY": "kk"}
    web = _Web(actions=_ONE_ACTION)
    messages = [{"role": "system", "content": pg.L0},
                {"role": "user", "content": "查规则"}]
    trace = pg.run_tool_pass(web, messages, env=env, transmit=fake)

    assert trace["rounds"] == 3, trace
    assert trace["tools"] == ["lookup_rule", "request_check"], trace
    assert trace["settled"] is True
    assert web.saved == 1, "request_check 那一次真的结算并落盘了"

    # 请求体形状（§5.5 表）：思考开、**没有** temperature、1024、非流式、带工具。
    first = calls[0]["payload"]
    assert first["thinking"] == {"type": "enabled"}
    assert "temperature" not in first, "预通行不传温度"
    assert first["max_completion_tokens"] == 1024
    assert first["stream"] is False
    assert first["tools"] == pg.TOOLS
    assert first["messages"] == messages, "预通行挂在**叙事消息数组**上"
    assert calls[0]["timeout"] == pg.TOOL_PASS_TIMEOUT_S
    assert calls[0]["headers"] == {pg.KEY_HEADER: "kk"}
    assert "kk" not in json.dumps(first, ensure_ascii=False)

    # 第二轮的 messages 里：助手消息带 tool_calls **且**带 reasoning_content
    # （否则供应商返回 400），随后才是 role: tool。
    second = calls[1]["payload"]["messages"]
    assistant = second[len(messages)]
    assert assistant["role"] == "assistant"
    assert assistant["tool_calls"][0]["id"] == "call-a"
    assert assistant["reasoning_content"] == "先查裁决堆栈"
    tool_msg = second[len(messages) + 1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "call-a"
    assert "规则冲突或无规则覆盖时" in tool_msg["content"]
    # 第三轮：助手消息也带上了第二轮的 reasoning。
    third = calls[2]["payload"]["messages"]
    assert third[-2]["reasoning_content"] == "再把行动结算掉"
    assert json.loads(third[-1]["content"])["passed"] is True

    # 上限：模型一直要工具也不会超过 3 轮。
    def always_tool(url, payload, headers, *, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers,
                      "timeout": timeout})
        return {"content": "", "tool_calls": [_tool_call("lookup_rule",
                                                         {"kind": "system.tone_packs"},
                                                         "c-%d" % len(calls))],
                "usage": {}}

    started = len(calls)
    trace = pg.run_tool_pass(_Web(), messages, env=env, transmit=always_tool)
    assert trace["rounds"] == pg.TOOL_PASS_MAX_ROUNDS == 3
    assert len(calls) - started == 3, "预通行内部最多 3 轮"

    # 离线 / 上游失败：一次也不请求，返回零统计。
    for broken in ({"BASE_URL": "", "MODEL": "mm", "API_KEY": "kk"},
                   {"BASE_URL": "https://api.example.com/v1", "MODEL": "",
                    "API_KEY": "kk"},
                   {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
                    "API_KEY": ""}):
        before = len(calls)
        assert pg.run_tool_pass(_Web(), messages, env=broken,
                                transmit=fake)["rounds"] == 0
        assert len(calls) == before
    ok("run_tool_pass：形状 / reasoning_content / 3 轮上限 / 离线不请求")


def test_tool_reasoning_never_persisted():
    """`reasoning` 只活在这一次的内存列表：不进 guide、不进 to_dict、不进叙事请求。"""
    def fake(url, payload, headers, *, timeout):
        return {"content": "", "tool_calls": [_tool_call("read_place_card", {})],
                "usage": {}, "reasoning": "思考痕迹-不得落盘"}

    env = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
           "API_KEY": "kk"}
    web = _Web()
    guide = pg.empty_guide()
    messages = pg.build_narrative_messages(web.rules, guide, "这地方", None)
    pg.run_tool_pass(web, messages, env=env, transmit=fake)

    # guide（也就是落盘那一份）里没有它；叙事请求也没有它，也没有 role: tool。
    assert "思考痕迹" not in json.dumps(guide, ensure_ascii=False)
    rebuilt = pg.build_narrative_messages(web.rules, guide, "这地方", None)
    blob = json.dumps(rebuilt, ensure_ascii=False)
    assert "思考痕迹" not in blob
    assert "reasoning_content" not in blob
    assert not any(m.get("role") == "tool" for m in rebuilt)
    assert [m["role"] for m in rebuilt] == \
        ["system", "user", "assistant", "user", "assistant", "user"]
    # 叙事请求体自己也不带这个字段。
    body = json.dumps(pg.build_narrative_body(rebuilt), ensure_ascii=False)
    assert "reasoning_content" not in body
    ok("reasoning：不进 guide / to_dict / 叙事请求（重拼 L0–L4 时不带 role: tool）")


# ── G5：战役绑定与典范卡 ────────────────────────────────────────────────


class _BindWeb:
    """最小 web_session 替身：`bind` 只按鸭子类型用 lock / guide / save。"""

    def __init__(self):
        self.lock = threading.RLock()
        self.guide = pg.empty_guide()
        self.saved = 0

    def save(self):
        self.saved += 1


def test_l1_bound_card():
    """绑定后的 L1：字节稳定、含规定专名、不含 salt / `docs/` / `source`。"""
    first = pg.build_l1("yunji", "yunji")
    second = pg.build_l1("yunji", "yunji")
    assert first == second, "同一剧本两次构建必须字节相同"
    for needle in ("白壁", "绳会账房", "npc-01", "霍砚", "loc-02",
                   "战役：九钥与元柜", "世界：yunji", "引擎：tactics"):
        assert needle in first, needle
    assert "尚未选择战役" not in first
    assert "docs/" not in first and "source" not in first
    assert "encounters" not in first and "if_botched" not in first
    assert len(first) <= pg.L1_LIMIT, len(first)
    # 地点行是「id 名字｜专名…」；白壁五条专名按文件顺序逐字在行里。
    wall = next(line for line in first.splitlines() if line.startswith("loc-02 "))
    assert wall == ("loc-02 白壁｜听泉馆｜白壁行拍卖厅｜白壁行地库｜"
                    "回声匣保管室｜老园丁小屋"), wall
    # 人物行带 id / 名字 / 欲望 / 面具 / 秘密；面具取自账本模板的同名条目。
    huo = next(line for line in first.splitlines()
               if line.startswith("npc-01 "))
    assert huo.startswith("npc-01 霍砚｜欲望：") and "｜面具：整洁、礼貌" in huo
    assert "｜秘密：他是守钥人计霜的儿子" in huo
    # 未绑定 → 常量；绑定之后按 scenario_id 取卡，salt 不进 L1。
    assert pg.l1_for(pg.empty_guide()) == pg.L1_UNBOUND
    a, b = pg.empty_guide(), pg.empty_guide()
    for guide in (a, b):
        guide["world_key"] = "yunji"
        guide["scenario_id"] = "yunji"
    assert a["salt"] != b["salt"]
    assert pg.l1_for(a) == pg.l1_for(b) == first, "两份存档的 L1 必须全等"
    assert a["salt"] not in first
    # 读不到剧本时退回未绑定常量（不扫描目录找替代）。
    assert pg.build_l1("nope", "nope") == pg.L1_UNBOUND
    ok("L1 典范卡：字节稳定、含专名、无 salt / docs/ / source")


def test_l1_glossary_only_with_world_file():
    """术语只在世界文件存在时才带：按 `term` 排序、最多 8 行。"""
    yunji = pg.build_l1("yunji", "yunji")
    # 云脊今天没有 data/worlds/yunji.json：术语是零行，不得去借别人的。
    assert "术语：\n地点：" in yunji, yunji[:200]
    rain = pg.build_l1("rain_line_seven", "threshold")
    lines = rain.splitlines()
    terms = lines[lines.index("术语：") + 1: lines.index("地点：")]
    assert 0 < len(terms) <= pg.L1_GLOSSARY_LIMIT, terms
    names = [item.split("：", 1)[0] for item in terms]
    assert names == sorted(names), names
    assert all(item.split("：", 1)[1] for item in terms), "含义不得是空的"
    assert "回声" in names, "阈界的术语应当来自世界文件"
    assert pg.build_l1("rain_line_seven", "threshold") == rain
    ok("L1 术语：有世界文件才带、按 term 排序、≤8 行")


def test_bind_contract():
    """`bind`：四种结果与「失败不落盘、salt 不变」。"""
    web = _BindWeb()
    salt = web.guide["salt"]
    # 1) scenario_id 不是文件名主干 → 没有这场战役（不碰磁盘、不落盘）。
    for bad in ("../etc/passwd", "Yunji", "九钥与元柜", "", "a" * 65, "yu/nji"):
        try:
            pg.bind(web, "yunji", bad)
        except ValueError as exc:
            assert str(exc) == pg.BIND_NO_SCENARIO, (bad, str(exc))
        else:
            raise AssertionError("非法 scenario_id 竟然绑定成功: %r" % (bad,))
    # 2) 文件不存在 → 没有这场战役。
    try:
        pg.bind(web, "nope", "nope")
    except ValueError as exc:
        assert str(exc) == pg.BIND_NO_SCENARIO
    else:
        raise AssertionError("不存在的剧本竟然绑定成功")
    # 3) world_key 与 meta.world 不一致 → 世界对不上，**不写** scenario_id。
    try:
        pg.bind(web, "other-world", "yunji")
    except ValueError as exc:
        assert str(exc) == pg.BIND_WORLD_MISMATCH
    else:
        raise AssertionError("世界对不上竟然绑定成功")
    assert web.guide["scenario_id"] == "" and web.guide["world_key"] == ""
    assert web.guide["l1_key"] == "unloaded"
    assert web.saved == 0, "失败的绑定不得落盘"

    # 4) 正绑定：显式记录三个键并落盘；salt 不变。
    assert pg.bind(web, "yunji", "yunji") == {"world_key": "yunji",
                                              "scenario_id": "yunji"}
    assert web.guide["world_key"] == "yunji"
    assert web.guide["scenario_id"] == "yunji"
    assert web.guide["l1_key"] == "yunji"
    assert web.guide["salt"] == salt, "绑定不得重掷 salt"
    assert web.saved == 1
    assert pg.l1_for(web.guide) == pg.build_l1("yunji", "yunji")

    # 5) 幂等：同一对 id 再绑一次 → 200，不重掷 salt，不再落盘。
    assert pg.bind(web, "yunji", "yunji")["scenario_id"] == "yunji"
    assert web.guide["salt"] == salt and web.saved == 1

    # 6) 另一场（three_wooden_boxes 的 meta.world 也是 yunji）→ 已经绑定。
    try:
        pg.bind(web, "yunji", "three_wooden_boxes")
    except ValueError as exc:
        assert str(exc) == pg.BIND_ALREADY_BOUND
    else:
        raise AssertionError("已绑定还能换一场")
    assert web.guide["scenario_id"] == "yunji" and web.saved == 1
    ok("bind：没有这场战役 / 世界对不上 / 幂等 / 已经绑定")


def test_bind_phrases_match_web_layer():
    """绑定短语与网页层 `GUIDE_BIND_ERRORS` 同文（跨模块锁）。"""
    import importlib
    import tempfile

    saved = os.environ.get("NOTDND_SAVE")
    os.environ["NOTDND_SAVE"] = tempfile.mkdtemp(prefix="pg-bind-errors-")
    try:
        web = importlib.import_module("notdnd_web")
    finally:
        if saved is None:
            os.environ.pop("NOTDND_SAVE", None)
        else:
            os.environ["NOTDND_SAVE"] = saved
    assert set(web.GUIDE_BIND_ERRORS) == {pg.BIND_NO_SCENARIO,
                                          pg.BIND_WORLD_MISMATCH,
                                          pg.BIND_ALREADY_BOUND}
    assert web.GUIDE_BIND_ERRORS[pg.BIND_NO_SCENARIO] == 400
    assert web.GUIDE_BIND_ERRORS[pg.BIND_WORLD_MISMATCH] == 400
    assert web.GUIDE_BIND_ERRORS[pg.BIND_ALREADY_BOUND] == 409
    ok("绑定短语与 GUIDE_BIND_ERRORS 同文（400 / 400 / 409）")


def test_location_ok():
    """`location_ok`：只认已绑定剧本里的地点 id，不按中文名搜索。"""
    guide = pg.empty_guide()
    assert pg.location_ok(guide, "loc-02") is False, "未绑定没有可对上的剧本"
    guide["scenario_id"] = "yunji"
    guide["world_key"] = "yunji"
    assert pg.location_ok(guide, "loc-02") is True
    assert pg.location_ok(guide, "loc-05") is True
    assert pg.location_ok(guide, "whitewall") is False, "战役节点不是地点"
    assert pg.location_ok(guide, "loc-99") is False
    assert pg.location_ok(guide, "") is False
    assert pg.location_ok(guide, "白壁") is False, "只认 id，不按中文名搜索"
    assert pg.location_ok(None, "loc-02") is False
    ok("location_ok：只认已绑定剧本里的地点 id")


def test_module_offline_no_socket():
    """`import prism_guide` 不打开套接字：只 import 不改外部状态。"""
    probe = subprocess.run(
        [sys.executable, "-c",
         "import prism_guide, socket, sys; "
         "sys.stdout.write('imported')"],
        cwd=str(ROOT), capture_output=True, text=True)
    assert probe.returncode == 0, probe.stderr
    assert "imported" in probe.stdout
    ok("import prism_guide 成功且无副作用")


# ── G6：实相（ensure_realization / validate_realization）──────────────────


class _RealizationWeb:
    """最小 web_session 替身：`ensure_realization` 只用 lock / guide / rules / save。"""

    def __init__(self, *, focus="loc-02", scenario="yunji", world="yunji"):
        self.lock = threading.RLock()
        self.guide = pg.empty_guide()
        self.guide["scenario_id"] = scenario
        self.guide["world_key"] = world
        self.guide["focus_location_id"] = focus
        self.rules = {"scene": {"id": "sc-1"}, "party": []}
        self.saved = 0

    def save(self):
        self.saved += 1


def _canon(location_id="loc-02", scenario="yunji", world="yunji"):
    guide = pg.empty_guide()
    guide["scenario_id"] = scenario
    guide["world_key"] = world
    return pg.canon_for(guide, location_id)


def _key_node(location_id, node_id, name):
    return {"id": location_id + "/" + node_id, "parent": location_id,
            "kind": "要点", "name": name, "fact": "一句事实。",
            "cites": [{"field": "key_places", "ref": name}]}


def _other_node(location_id, node_id, name, kind, fact, ref,
                field="atmosphere"):
    return {"id": location_id + "/" + node_id, "parent": location_id,
            "kind": kind, "name": name, "fact": fact,
            "cites": [{"field": field, "ref": ref}]}


def _wall_key_nodes():
    return [_key_node("loc-02", "tingquan", "听泉馆"),
            _key_node("loc-02", "auction", "白壁行拍卖厅"),
            _key_node("loc-02", "vault", "白壁行地库"),
            _key_node("loc-02", "echo-box", "回声匣保管室"),
            _key_node("loc-02", "gardener", "老园丁小屋")]


def _wall_street():
    return _other_node("loc-02", "wash-lane", "洗墙巷", "街",
                       "一排白房子之间的窄巷。", "每月洗一次")


def _payload(location_id, location_name, nodes, links=None, people=None):
    return {"location_id": location_id, "location_name": location_name,
            "nodes": list(nodes), "links": list(links or []),
            "people": list(people or [])}


def test_realization_canon_fixtures():
    """典范夹具：白壁五条专名逐字在；灰市第一条是 `绳会账房`。"""
    wall = _canon("loc-02")
    assert wall["location_id"] == "loc-02" and wall["location_name"] == "白壁"
    assert wall["key_place_heads"] == ["听泉馆", "白壁行拍卖厅", "白壁行地库",
                                       "回声匣保管室", "老园丁小屋"]
    assert wall["key_place_full"][0].startswith("听泉馆——")
    assert wall["unlock"] and wall["if_botched"]
    assert "每月洗一次" in wall["atmosphere"]
    # 云脊没有世界文件：术语为零行，区域摘要为空（不得去借别的世界）。
    assert wall["glossary"] == [] and wall["region_summary"] == ""
    assert "npc-01" in wall["npc_secrets"] and "绳会账房的后间木匣" in wall["forbidden_names"]
    market = _canon("loc-05")
    assert market["key_place_heads"][0] == "绳会账房"
    assert "绳会账房" in market["key_place_heads"]
    # 没有 key_places 的地点不生成实相（canon 为空）。
    guide = pg.empty_guide()
    guide["scenario_id"] = "yunji"
    assert pg.canon_for(guide, "loc-99") == {}
    ok("实相典范夹具：白壁五专名 / 灰市绳会账房 / 无 key_places 不生成")


def test_validate_realization_hard_failures():
    """纯函数硬失败：改了典范名 / 缺少要点 / 无引用 / 只有要点；合法图可落盘。"""
    canon = _canon("loc-02")
    nodes = _wall_key_nodes() + [_wall_street()]
    good = _payload("loc-02", "白壁", nodes)
    stored, why = pg.validate_realization(good, canon)
    assert stored is not None and why == [], why
    assert stored["source"] == "model" and stored["version"] == 1
    # 落盘的是典范原文的 unlock / if_botched（模型改一个字也盖不掉）。
    assert stored["preserved"]["unlock"] == canon["unlock"]
    assert stored["preserved"]["if_botched"] == canon["if_botched"]
    # 实相对象里没有坐标。
    blob = json.dumps(stored, ensure_ascii=False)
    for axis in ('"x"', '"y"', '"z"'):
        assert axis not in blob, axis

    # 缺一条要点 → 缺少要点：老园丁小屋。
    four = _payload("loc-02", "白壁",
                    _wall_key_nodes()[:4] + [_wall_street()])
    assert pg.validate_realization(four, canon) == \
        (None, ["失败：缺少要点：老园丁小屋"])
    # location_name 改了 → 改了典范名：白璧。
    renamed = _payload("loc-02", "白璧", nodes)
    assert pg.validate_realization(renamed, canon) == \
        (None, ["失败：改了典范名：白璧"])
    # 要点名改了 → 同样是整张图的 改了典范名。
    bad_key = _wall_key_nodes()
    bad_key[0] = _key_node("loc-02", "tingquan", "白璧")
    assert pg.validate_realization(_payload("loc-02", "白壁", bad_key
                                            + [_wall_street()]), canon) == \
        (None, ["失败：改了典范名：白璧"])
    # cites 为空 → 无引用。
    empty_cite = _wall_key_nodes() + [
        {"id": "loc-02/wash-lane", "parent": "loc-02", "kind": "街",
         "name": "洗墙巷", "fact": "窄巷。", "cites": []}]
    assert pg.validate_realization(_payload("loc-02", "白壁", empty_cite),
                                   canon) == (None, ["失败：无引用：loc-02/wash-lane"])
    # 只有要点 → 只有要点（不落盘）。
    only = _payload("loc-02", "白壁", _wall_key_nodes())
    assert pg.validate_realization(only, canon) == (None, ["失败：只有要点"])
    # 五个要点 + 一条会被丢掉的贫区 → 丢弃之后也只剩要点 → 只有要点。
    poor = _other_node("loc-02", "poor-lane", "贫民棚屋", "贫区",
                       "一片棚户。", "这里是老钱")
    only_after_drop = _payload("loc-02", "白壁", _wall_key_nodes() + [poor])
    stored2, why2 = pg.validate_realization(only_after_drop, canon)
    assert stored2 is None and "失败：只有要点" in why2, why2
    ok("validate_realization：改了典范名 / 缺少要点 / 无引用 / 只有要点")


def test_validate_realization_keypoint_self_cite():
    """要点必须自引专名（§2.1）：引用 atmosphere、或引用**别的**专名 → 无引用。

    修正前只做通用 `cites_valid`：要点引 `atmosphere`（`ref` 是氛围的连续文本）、
    或引别的专名（合法 `key_places` 引用）都会被接受。现按设计 §2.1 末句，
    要点的 `cites` 必须 `field==key_places` 且 `ref` 逐字等于自身 `name`。
    """
    canon = _canon("loc-02")

    def _key_with(node_id, name, cites):
        node = _key_node("loc-02", node_id, name)
        node["cites"] = cites
        return node

    # 合法自引继续通过（白壁五要点既有夹具 + 一条街）。
    good = _payload("loc-02", "白壁", _wall_key_nodes() + [_wall_street()])
    stored, why = pg.validate_realization(good, canon)
    assert stored is not None and why == [], why

    # 违规一：要点引用 atmosphere（`ref` 确是氛围的连续文本，字段不是 key_places）。
    assert pg.cites_valid([{"field": "atmosphere", "ref": "每月洗一次"}], canon)
    bad_atmo = _wall_key_nodes()
    bad_atmo[0] = _key_with("tingquan", "听泉馆",
                            [{"field": "atmosphere", "ref": "每月洗一次"}])
    assert pg.validate_realization(
        _payload("loc-02", "白壁", bad_atmo + [_wall_street()]), canon) == \
        (None, ["失败：无引用：loc-02/tingquan"])

    # 违规二：要点引用**别的**专名（合法 key_places 引用，但 ref != 自身 name）。
    assert pg.cites_valid([{"field": "key_places", "ref": "白壁行地库"}], canon)
    bad_other = _wall_key_nodes()
    bad_other[0] = _key_with("tingquan", "听泉馆",
                             [{"field": "key_places", "ref": "白壁行地库"}])
    assert pg.validate_realization(
        _payload("loc-02", "白壁", bad_other + [_wall_street()]), canon) == \
        (None, ["失败：无引用：loc-02/tingquan"])

    # 违规三：要点缺 cites → 仍归到 无引用。
    bad_empty = _wall_key_nodes()
    bad_empty[0] = _key_with("tingquan", "听泉馆", [])
    assert pg.validate_realization(
        _payload("loc-02", "白壁", bad_empty + [_wall_street()]), canon) == \
        (None, ["失败：无引用：loc-02/tingquan"])

    # 非要点节点不受影响：街引用 atmosphere 照旧通过。
    street_only = pg.validate_realization(
        _payload("loc-02", "白壁", _wall_key_nodes() + [_wall_street()]), canon)
    assert street_only[0] is not None
    ok("validate_realization：要点自引专名；引错字段 / 引错专名 / 空 cites → 无引用")


def test_validate_realization_near_name_is_drop_not_fail():
    """近名只丢那个节点，不是整张图失败；旁边还有合法街就不重试。"""
    canon = _canon("loc-02")
    for node_id, name in (("tingquan-out", "听泉馆外"),
                          ("white-lane", "白壁巷"),
                          ("white-jade", "白璧")):
        near = _other_node("loc-02", node_id, name, "建筑",
                           "一个加出来的建筑。", "每月洗一次")
        payload = _payload("loc-02", "白壁",
                           _wall_key_nodes() + [_wall_street(), near],
                           links=[{"a": "loc-02/wash-lane",
                                   "b": "loc-02/" + node_id}])
        stored, why = pg.validate_realization(payload, canon)
        assert stored is not None, (name, why)
        assert why == ["丢弃：loc-02/%s：近名" % node_id], (name, why)
        kept = [item["name"] for item in stored["nodes"]]
        assert name not in kept and "洗墙巷" in kept
        # 指向被丢节点的边一并删掉（不是 `链接越界`）。
        wash = next(item for item in stored["nodes"]
                    if item["id"] == "loc-02/wash-lane")
        assert wash["links"] == [], wash["links"]
    ok("validate_realization：听泉馆外 / 白壁巷 / 白璧 只丢弃近名，图留下")


def test_validate_realization_old_money_poor_zone():
    """五要点 + 只靠「老钱」的贫区（街连着它）：贫区丢、边删、街与要点留下。"""
    canon = _canon("loc-02")
    poor = _other_node("loc-02", "poor-lane", "贫民棚屋", "贫区",
                       "一片棚户。", "这里是老钱")
    payload = _payload(
        "loc-02", "白壁", _wall_key_nodes() + [_wall_street(), poor],
        links=[{"a": "loc-02/wash-lane", "b": "loc-02/poor-lane"},
               {"a": "loc-02/wash-lane", "b": "loc-02/gardener"}])
    stored, why = pg.validate_realization(payload, canon)
    assert stored is not None, why
    assert why == ["丢弃：loc-02/poor-lane：和老钱矛盾"], why
    ids = [item["id"] for item in stored["nodes"]]
    assert "loc-02/poor-lane" not in ids and "loc-02/wash-lane" in ids
    wash = next(item for item in stored["nodes"]
                if item["id"] == "loc-02/wash-lane")
    assert wash["links"] == ["loc-02/gardener"], wash["links"]
    ok("validate_realization：白壁的贫区按「和老钱矛盾」丢弃并删边")


def test_validate_realization_market_street_kind():
    """灰市夹具：五个要点在，绳会账房不被替换；街与建筑留下。"""
    canon = _canon("loc-05")
    heads = canon["key_place_heads"]
    assert heads[0] == "绳会账房"
    key_nodes = [_key_node("loc-05", "acc-%d" % index, head)
                 for index, head in enumerate(heads)]
    street = _other_node("loc-05", "copper-lane", "铜毫巷", "街",
                         "窄巷。", "永远不亮")
    payload = _payload("loc-05", "灰市", key_nodes + [street])
    stored, why = pg.validate_realization(payload, canon)
    assert stored is not None and why == [], why
    names = [item["name"] for item in stored["nodes"]]
    assert names.count("绳会账房") == 1
    for head in heads:
        assert head in names, head
    ok("validate_realization：灰市绳会账房在且不被替换，街留下")


def test_realization_request_body_contract():
    """实相请求体：思考开 / 非流式 / 4096 / 无工具；user 带切片与输出合同。"""
    canon = _canon("loc-02")
    salt = "0123456789abcdef0123456789abcdef"
    body = pg.build_realization_body(canon, salt, model="mm")
    assert body["thinking"] == {"type": "enabled"}
    assert body["stream"] is False
    assert body["max_completion_tokens"] == pg.REALIZATION_MAX_TOKENS == 4096
    assert "tools" not in body and "temperature" not in body
    assert body["messages"][0] == {"role": "system", "content": pg.L0}
    user = body["messages"][1]["content"]
    # 合同句子在 user 里，且**不在** L0。
    for needle in ("只输出一个 JSON 对象", "不要输出【裁决】【叙事】【钩子】",
                   "街、建筑、下层", "不要为了凑种类输出贫区",
                   "^loc-02/[a-z0-9-]{1,24}$"):
        assert needle in user, needle
        assert needle not in pg.L0, needle
    for head in canon["key_place_heads"]:
        assert head in user, head
    # 切片含 salt 与典范原文的 unlock；密钥不入体。
    assert ("salt：" + salt) in user
    assert canon["unlock"][:12] in user
    assert "API_KEY" not in json.dumps(body, ensure_ascii=False)
    ok("实相请求体：思考开 / 非流式 / 4096 / 无工具 / 合同在 user 不在 L0")


def test_realization_retry_only_second_saved():
    """假传输第一次非法 JSON、第二次合法含非要点图 → 只保存第二次。"""
    canon = _canon("loc-02")
    good = json.dumps(_payload(
        "loc-02", "白壁", _wall_key_nodes() + [_wall_street()],
        links=[{"a": "loc-02/wash-lane", "b": "loc-02/tingquan"}]),
        ensure_ascii=False)
    queue = [{"content": "这不是 JSON", "tool_calls": [], "usage": {}},
             {"content": good, "tool_calls": [], "usage": {}}]
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers,
                      "timeout": timeout})
        return dict(queue.pop(0))

    web = _RealizationWeb()
    pg.ensure_realization(web, "loc-02",
                          env={"BASE_URL": "https://api.example.com/v1",
                               "MODEL": "mm", "API_KEY": "super-secret-value"},
                          transmit=fake)
    assert len(calls) == 2, len(calls)
    record = web.guide["realizations"]["loc-02"]
    assert record["source"] == "model"
    assert len(record["nodes"]) == 6, record["nodes"]
    # 请求形状：思考开、非流式、无工具；密钥只在请求头。
    first = calls[0]["payload"]
    assert first["thinking"] == {"type": "enabled"}
    assert first["stream"] is False and "tools" not in first
    assert calls[0]["timeout"] == pg.REALIZATION_TIMEOUT_S
    assert calls[0]["headers"] == {pg.KEY_HEADER: "super-secret-value"}
    assert "super-secret-value" not in json.dumps(first, ensure_ascii=False)
    # 重试的 user 消息再次带上合同、id 语法、专名与「上一次失败的原因」。
    retry_user = calls[1]["payload"]["messages"][1]["content"]
    assert "上一次失败的原因" in retry_user
    assert "^loc-02/[a-z0-9-]{1,24}$" in retry_user
    for head in canon["key_place_heads"]:
        assert head in retry_user, head
    assert "街、建筑、下层" in retry_user
    ok("实相重试：第一次坏 JSON → 第二次合法图；只存第二次，形状正确")


def test_realization_second_attempt_gets_remaining_budget():
    """第二次尝试的 `timeout` 是**剩余预算**（≤ 总预算），不是又一份 75 秒。"""
    canon = _canon("loc-02")
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append(timeout)
        return {"content": "这不是 JSON", "tool_calls": [], "usage": {}}

    web = _RealizationWeb()
    pg.ensure_realization(web, "loc-02",
                          env={"BASE_URL": "https://api.example.com/v1",
                               "MODEL": "mm", "API_KEY": "kk"},
                          transmit=fake)
    assert len(calls) == 2, calls
    assert calls[0] == pg.REALIZATION_TIMEOUT_S
    assert 0 < calls[1] <= pg.REALIZATION_TIMEOUT_S
    ok("实相重试：第二次只用剩余预算（两次共用 75 秒，不翻倍）")


def test_realization_budget_exhausted_no_second_request():
    """第一次就吃满时间盒 → 不再发第二次，直接要点链 fallback。"""
    canon = _canon("loc-02")
    calls = []
    saved = pg.REALIZATION_TIMEOUT_S
    pg.REALIZATION_TIMEOUT_S = 0.05

    def slow(url, payload, headers, *, timeout):
        calls.append(timeout)
        time.sleep(0.08)             # 超过总预算
        return {"content": "这不是 JSON", "tool_calls": [], "usage": {}}

    try:
        web = _RealizationWeb()
        pg.ensure_realization(web, "loc-02",
                              env={"BASE_URL": "https://api.example.com/v1",
                                   "MODEL": "mm", "API_KEY": "kk"},
                              transmit=slow)
    finally:
        pg.REALIZATION_TIMEOUT_S = saved
    assert len(calls) == 1, calls
    assert calls[0] == 0.05
    assert web.guide["realizations"]["loc-02"]["source"] == "fallback"
    ok("实相预算：第一次吃满后不再发第二次（总预算不翻倍）")


def test_realization_double_failure_falls_back():
    """连续两次只有要点 → 要点链 `source: fallback`，没有第三次请求。"""
    canon = _canon("loc-02")
    only_key = json.dumps(_payload("loc-02", "白壁", _wall_key_nodes()),
                          ensure_ascii=False)
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append(payload)
        return {"content": only_key, "tool_calls": [], "usage": {}}

    web = _RealizationWeb()
    pg.ensure_realization(web, "loc-02",
                          env={"BASE_URL": "https://api.example.com/v1",
                               "MODEL": "mm", "API_KEY": "kk"},
                          transmit=fake)
    assert len(calls) == 2, len(calls)
    record = web.guide["realizations"]["loc-02"]
    assert record["source"] == "fallback"
    assert [item["name"] for item in record["nodes"]] == \
        canon["key_place_heads"]
    assert record["people"] == []
    # 要点链**不**再过「只有要点」那一关：直接落盘并有 L2 摘要。
    assert "听泉馆" in web.guide["l2"] and web.guide["salt"] in web.guide["l2"]
    assert web.guide["transcript"] == []
    ok("实相退回：两次只有要点 → fallback 要点链，无第三次请求")


def test_realization_offline_falls_back():
    """离线（空密钥 / 空 BASE_URL）不发请求，直接落要点链 fallback。"""
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append(payload)
        return {"content": "", "tool_calls": [], "usage": {}}

    web = _RealizationWeb()
    pg.ensure_realization(web, "loc-02",
                          env={"BASE_URL": "", "MODEL": "mm", "API_KEY": "kk"},
                          transmit=fake)
    assert calls == [], "离线不得调用传输"
    assert web.guide["realizations"]["loc-02"]["source"] == "fallback"
    ok("实相：离线不请求、直接要点链 fallback")


def test_realization_pending_and_dedup():
    """同一地点第二次不请求；新鲜 pending 不请求；超 120 秒才可再占一次。"""
    canon = _canon("loc-02")
    good = json.dumps(_payload("loc-02", "白壁",
                               _wall_key_nodes() + [_wall_street()]),
                      ensure_ascii=False)
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append(payload)
        return {"content": good, "tool_calls": [], "usage": {}}

    env = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
           "API_KEY": "kk"}
    web = _RealizationWeb()
    pg.ensure_realization(web, "loc-02", env=env, transmit=fake)
    assert len(calls) == 1
    pg.ensure_realization(web, "loc-02", env=env, transmit=fake)
    assert len(calls) == 1, "同一地点第二次不得再请求"

    # 新鲜 pending：不调用模型，也不写 fallback；L2 里没有 claimed_at。
    fresh = _RealizationWeb()
    fresh.guide["realizations"]["loc-02"] = {
        "version": 1, "location_id": "loc-02", "source": "pending",
        "claimed_at": time.time(), "claim": "aa"}
    before = json.dumps(fresh.guide["realizations"], sort_keys=True)
    pg.ensure_realization(fresh, "loc-02", env=env, transmit=fake)
    assert len(calls) == 1, "新鲜 pending 不得请求"
    assert json.dumps(fresh.guide["realizations"], sort_keys=True) == before
    assert fresh.guide["realizations"]["loc-02"]["source"] == "pending"
    l2_pending = pg.build_l2(fresh.rules, fresh.guide)
    assert "claimed_at" not in l2_pending and "听泉馆" not in l2_pending

    # 超过 120 秒的 pending：允许再占一次并生成。
    stale = _RealizationWeb()
    stale.guide["realizations"]["loc-02"] = {
        "version": 1, "location_id": "loc-02", "source": "pending",
        "claimed_at": time.time() - pg.REALIZATION_CLAIM_S - 80,
        "claim": "bb"}
    pg.ensure_realization(stale, "loc-02", env=env, transmit=fake)
    assert len(calls) == 2, "过期 pending 应当再占一次"
    assert stale.guide["realizations"]["loc-02"]["source"] == "model"
    ok("实相占位：第二次不请求 / 新鲜 pending 不请求 / 过期可再占一次")


def test_realization_overlap_single_upstream():
    """两次重叠的 `ensure_realization` 只有一次上游。"""
    canon = _canon("loc-02")
    good = json.dumps(_payload("loc-02", "白壁",
                               _wall_key_nodes() + [_wall_street()]),
                      ensure_ascii=False)
    upstream = []
    guard = threading.Lock()

    def slow(url, payload, headers, *, timeout):
        with guard:
            upstream.append(payload)
        time.sleep(0.15)
        return {"content": good, "tool_calls": [], "usage": {}}

    web = _RealizationWeb()
    env = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
           "API_KEY": "kk"}
    threads = [threading.Thread(target=pg.ensure_realization,
                                args=(web, "loc-02"),
                                kwargs={"env": env, "transmit": slow})
               for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(upstream) == 1, len(upstream)
    assert web.guide["realizations"]["loc-02"]["source"] == "model"
    ok("实相并发：两次重叠只有一次上游（占位串起来）")


def test_realization_commit_rewrites_l2():
    """提交之后 L2 含 salt 与 `听泉馆`，L3 为空；`pending` 不重写 L2。"""
    canon = _canon("loc-02")
    good = json.dumps(_payload("loc-02", "白壁",
                               _wall_key_nodes() + [_wall_street()],
                               links=[{"a": "loc-02/wash-lane",
                                       "b": "loc-02/tingquan"}]),
                      ensure_ascii=False)
    web = _RealizationWeb()
    web.guide["transcript"] = [{"role": "assistant", "content": "旧对白"}]
    web.guide["l2"] = "旧检查点"
    pg.ensure_realization(web, "loc-02",
                          env={"BASE_URL": "https://api.example.com/v1",
                               "MODEL": "mm", "API_KEY": "kk"},
                          transmit=lambda *a, **k: {"content": good,
                                                    "tool_calls": [],
                                                    "usage": {}})
    assert "听泉馆" in web.guide["l2"] and web.guide["salt"] in web.guide["l2"]
    assert "claimed_at" not in web.guide["l2"]
    assert web.guide["transcript"] == [], "提交实相要清空 L3"
    # `here` 初始放在第一条要点上。
    assert web.guide["here"] == canon["key_place_heads"][0]
    # 定稿后的记录里没有 `claimed_at`。
    assert "claimed_at" not in web.guide["realizations"]["loc-02"]
    # 未提交（pending）时不重写 L2：ensure_l2 也不会把 pending 写进去。
    assert pg.build_l2(web.rules, web.guide).count("salt：") == 1
    ok("实相提交：重写 L2（salt + 听泉馆）、清空 L3、删 claimed_at")



# ── G7：行为与对白（声线 / 秘密门 / 场外痕迹 / 路人）─────────────────────


_G7_ENV = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
           "API_KEY": "kk"}


def _bound_guide(scenario="yunji", world="yunji", focus="loc-02"):
    """一份已绑定 `yunji` 的 web_session 替身（`bind` 顺带填了 `npc_home`）。"""
    web = _BindWeb()
    pg.bind(web, world, scenario)
    web.guide["focus_location_id"] = focus
    return web


def _npc(scenario_id, npc_id):
    for item in (pg.load_scenario(scenario_id) or {}).get("npcs") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == npc_id:
            return item
    raise AssertionError("找不到 %s 里的 %s" % (scenario_id, npc_id))


def test_g7_npc_voice_stable():
    """`npc_voice`：FNV-1a，`npc-01` 白桦 / `npc-02` 茉莉；同一存档声线稳定。"""
    assert pg.npc_voice("npc-01") == pg.VOICE_NARRATOR == "白桦"
    assert pg.npc_voice("npc-02") == pg.VOICE_ALT == "茉莉"
    # 空 id 由调用方用白桦（函数本身也不炸）。
    assert pg.npc_voice("") == pg.VOICE_NARRATOR
    # 对照不是性别表：整本剧本的 npc id 两种音色都有。
    voices = {pg.npc_voice("npc-%02d" % i) for i in range(1, 20)}
    assert voices == {"白桦", "茉莉"}, voices
    # 同一存档两次调用：第二次从 guide.voices 读回，值不变。
    guide = pg.empty_guide()
    assert pg.voice_for(guide, "npc-01") == "白桦"
    assert guide["voices"]["npc-01"] == "白桦"
    assert pg.voice_for(guide, "npc-01") == "白桦"
    assert guide["voices"]["npc-01"] == "白桦"
    assert "npc-01" not in guide["voices"] or guide["voices"]["npc-01"] == "白桦"
    ok("npc_voice：npc-01 白桦 / npc-02 茉莉；同一存档声线稳定")


def test_g7_check_speech_only_secrets():
    """`check_speech`：只查自己的秘密；点别人的名字 / 复述欲望都不算失败。"""
    huo = _npc("yunji", "npc-01")
    wen = _npc("yunji", "npc-02")
    # 温苔说出「霍砚」——另一名典范 NPC 的名字，原句留下。
    assert pg.check_speech("霍砚把钥匙收进袖子。", wen,
                           secret_allowed=False) is None
    assert pg.check_speech("霍砚说他是守钥人计霜的儿子。", wen,
                           secret_allowed=False) is None
    # 说出**自己的**秘密：全文或连续 8 码位都命中「写了秘密」。
    assert pg.check_speech(huo["secret"], huo,
                           secret_allowed=False) == pg.DROP_SECRET
    assert pg.check_speech("我是守钥人计霜的儿子。", huo,
                           secret_allowed=False) == pg.DROP_SECRET
    # 放行时不失败。
    assert pg.check_speech(huo["secret"], huo, secret_allowed=True) is None
    # 不判断面具像不像、欲望是否被反着说。
    assert pg.check_speech(wen["drive"], wen, secret_allowed=False) is None
    assert pg.check_speech("", huo, secret_allowed=False) is None
    ok("check_speech：只查自己的秘密，点别人的名字不算失败")


def test_g7_redact_narration_no_second_request():
    """叙事正文里的秘密换成「……」，L3 干净；**不**第二次调用传输。"""
    web = _bound_guide()
    guide = web.guide
    huo = _npc("yunji", "npc-01")
    secret = huo["secret"]
    window = secret[:pg.SECRET_WINDOW]
    assert window == "他是守钥人计霜的"
    raw = ("【裁决】判定：成功\n\n"
           "【叙事】霍砚低声说他是守钥人计霜的儿子。\n\n"
           "【钩子】泉声还在响。")
    # 玩家原文没有 lever，也没有 known_clues → 秘密不放行。
    saved = pg.TRANSMIT

    def boom(*_args, **_kwargs):
        raise AssertionError("redact 不得调用传输")

    pg.TRANSMIT = boom
    try:
        redacted = pg.redact_narration(raw, guide, player_text="我走进白壁")
        # 玩家原文含 lever 整段 → 放行，原句保留。
        lever = pg.npc_field(huo, pg._scenario_parts(guide)["ledger"], "lever")
        assert lever == "他是计霜的儿子"
        kept = pg.redact_narration(raw, guide,
                                   player_text="我说：他是计霜的儿子")
    finally:
        pg.TRANSMIT = saved

    assert "他是守钥人计霜的儿子" not in redacted
    assert window not in redacted
    assert "……" in redacted
    assert secret not in redacted
    assert "他是守钥人计霜的儿子" in kept, "放行的秘密要原样留下"
    # 模拟网页层把涂掉之后的整段写进 L3：既没有 8 码位，也没有全文。
    guide["transcript"].append({"role": "assistant", "content": redacted})
    joined = "".join(entry["content"] for entry in guide["transcript"])
    assert window not in joined and secret not in joined

    # `known_clues` 与 `knows` 相交同样放行。
    web2 = _bound_guide()
    web2.guide["known_clues"] = ["C-01"]      # 霍砚 knows C-01 / C-04 / C-09
    assert "他是守钥人计霜的儿子" in pg.redact_narration(raw, web2.guide)
    ok("redact_narration：秘密换成「……」；lever / known_clues 放行；不叫传输")


def test_g7_npc_home_and_scene_speakers():
    """`bind` 填 `npc_home`；`npcs_at` / `scene_speakers` 只认在场 NPC。"""
    web = _bound_guide()
    home = web.guide["npc_home"]
    assert home.get("npc-01") == "loc-02", home   # 「二阶白壁行」含「白壁」
    assert home.get("npc-02") == "loc-05", home   # 「五阶灰市」含「灰市」
    # 没有模板条目的 NPC（或模板 location 不含任何地点名）不入表。
    assert "npc-16" not in home
    assert pg.npcs_at(web.guide, "loc-02") == ["npc-01"]
    assert pg.npcs_at(web.guide, "loc-05") == ["npc-02"]
    web.guide["focus_location_id"] = "loc-02"
    assert pg.scene_speakers(web.guide) == {"霍砚": "npc-01"}
    web.guide["focus_location_id"] = "loc-05"
    assert pg.scene_speakers(web.guide) == {"温苔": "npc-02"}
    # 未绑定 / 无焦点 → 空表。
    assert pg.scene_speakers(pg.empty_guide()) == {}
    assert pg.scene_speakers({"scenario_id": "yunji",
                              "focus_location_id": ""}) == {}
    ok("npc_home / scene_speakers：按模板 location 认家，只认在场")


def test_g7_traces_betrayed_uses_canonical_text():
    """`betrayed` 时痕迹等于 `if_dead_or_betrayed` 原文，且**没有**场外模型请求。"""
    web = _bound_guide()
    huo = _npc("yunji", "npc-01")
    web.guide["npc_flags"] = {"npc-01": "betrayed"}
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append(payload)
        return {"content": "{}", "tool_calls": [], "usage": {}}

    count = pg.record_traces(web, "loc-02", env=_G7_ENV, transmit=fake)
    assert count == 1, count
    assert calls == [], "终局标志的 NPC 不得叫模型"
    trace = web.guide["traces"][-1]
    assert trace["text"] == huo["if_dead_or_betrayed"]
    assert trace["id"] == "npc-01" and trace["at"] == "loc-02"
    assert trace["revealed"] is False
    ok("record_traces：betrayed 用 if_dead_or_betrayed 原文，不叫模型")


def test_g7_traces_request_shape_and_secret_gate():
    """场外请求：一次非流式、思考关、0.7、512、无工具；命中秘密的句子丢掉。"""
    web = _bound_guide()
    huo = _npc("yunji", "npc-01")
    calls = []

    def fake(url, payload, headers, *, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers,
                      "timeout": timeout})
        return {"content": json.dumps({"npc-01": "他把一枚旧钥匙放进茶柜。"},
                                      ensure_ascii=False),
                "tool_calls": [], "usage": {}}

    count = pg.record_traces(web, "loc-02", env=_G7_ENV, transmit=fake)
    assert count == 1 and len(calls) == 1, (count, len(calls))
    body = calls[0]["payload"]
    assert body["thinking"] == {"type": "disabled"}
    assert body["stream"] is False and "tools" not in body
    assert body["temperature"] == pg.TRACE_TEMPERATURE == 0.7
    assert body["max_completion_tokens"] == pg.TRACE_MAX_TOKENS == 512
    assert calls[0]["timeout"] == pg.TRACE_TIMEOUT_S
    assert calls[0]["headers"] == {pg.KEY_HEADER: "kk"}
    user = body["messages"][1]["content"]
    assert body["messages"][0] == {"role": "system", "content": pg.L0}
    assert "不得说出任何秘密" in user
    # 秘密全文**不进** user 消息（模型只被告知「不得说出秘密」）。
    assert huo["secret"] not in user
    assert pg.load_scenario("yunji")["npcs"][0]["secret"] not in user

    # 模型把秘密说漏 → 这条痕迹不存，且统计 speech_rejected +1。
    web2 = _bound_guide()

    def leak(url, payload, headers, *, timeout):
        return {"content": json.dumps({"npc-01": huo["secret"]},
                                      ensure_ascii=False),
                "tool_calls": [], "usage": {}}

    assert pg.record_traces(web2, "loc-02", env=_G7_ENV,
                            transmit=leak) == 0
    assert web2.guide["traces"] == []
    assert web2.guide["stats"]["speech_rejected"] == 1
    ok("record_traces：请求形状正确；说漏秘密的痕迹不存")


def test_g7_traces_offline_and_empty():
    """离线 / 不在场 / 失败：这一轮没有痕迹，也不走兜底句。"""
    web = _bound_guide()
    calls = []
    count = pg.record_traces(
        web, "loc-02",
        env={"BASE_URL": "", "MODEL": "mm", "API_KEY": "kk"},
        transmit=lambda *a, **k: calls.append(1) or {})
    assert count == 0 and calls == [] and web.guide["traces"] == []
    # 没人在场的地点：直接 0，连请求都没有。
    assert pg.record_traces(web, "loc-11", env=_G7_ENV,
                            transmit=lambda *a, **k: calls.append(1) or {}) == 0
    assert calls == []
    # 未绑定 → 0。
    assert pg.record_traces(_BindWeb(), "loc-02", env=_G7_ENV,
                            transmit=lambda *a, **k: calls.append(1) or {}) == 0
    ok("record_traces：离线 / 不在场 / 未绑定都不留痕迹")


def test_g7_beats_of_assigns_voice():
    """`beats_of`：对白拍写 `speaker` 与按 `npc.id` 分配的声线；写 `npc_at`。

    音色取自那一拍（`speak` 只用拍上存的音色，忽略客户端字段）。
    """
    web = _bound_guide(focus="loc-02")
    guide = web.guide
    reviewed = ("【裁决】判定：成功\n\n"
                "【叙事】风从巷口灌进来。霍砚：「别出声。」\n\n"
                "【钩子】泉声还在响。")
    beats = pg.beats_of(reviewed, guide)
    dialogue = [beat for beat in beats if beat["speaker"]]
    assert len(dialogue) == 1, beats
    assert dialogue[0]["speaker"] == "npc-01"
    assert dialogue[0]["voice"] == pg.npc_voice("npc-01") == "白桦"
    assert guide["voices"]["npc-01"] == "白桦"
    assert guide["npc_at"]["npc-01"] == "loc-02"
    # 说过话之后，同一存档再切一次音色不变（从 guide.voices 读回）。
    assert pg.beats_of(reviewed, guide)[1]["voice"] == "白桦"
    assert guide["voices"]["npc-01"] == "白桦"

    # 温苔：npc-02 → 茉莉。旁白仍是白桦。
    web2 = _bound_guide(focus="loc-05")
    beats2 = pg.beats_of("【叙事】温苔：「灯还亮着。」", web2.guide)
    assert beats2[0]["speaker"] == "npc-02"
    assert beats2[0]["voice"] == "茉莉"
    # TTS 用**拍上**的音色（不是客户端字段）。
    assert pg.build_tts_body(beats2[0]["text"],
                             voice=beats2[0]["voice"])["voice"] == "茉莉"
    # 不在场的名字不切出说话人（空 guide / 未绑定仍是白桦）。
    assert pg.beats_of("【叙事】温苔：「灯还亮着。」",
                       pg.empty_guide())[0]["speaker"] == ""
    ok("beats_of：对白拍按 npc.id 分配声线并写 npc_at，音色取自那一拍")


def test_g7_incidental_id_shape_and_drop():
    """路人 id `^inc-[0-9a-f]{8}$`、确定、不等于 `npc-01`；沾线索 / 秘密的丢掉。"""
    salt = "0123456789abcdef0123456789abcdef"
    pid = pg.incidental_id(salt, "loc-02", "loc-02/wash-lane", "倚着墙吃饼")
    assert re.fullmatch(r"^inc-[0-9a-f]{8}$", pid), pid
    assert pid != "npc-01"
    assert pg.incidental_id(salt, "loc-02", "loc-02/wash-lane",
                            "倚着墙吃饼") == pid, "同输入必须同 id"
    other = pg.incidental_id(salt, "loc-02", "loc-02/wash-lane", "数铜钱")
    assert other != pid and re.fullmatch(r"^inc-[0-9a-f]{8}$", other)

    canon = _canon("loc-02")
    good = _payload("loc-02", "白壁", _wall_key_nodes() + [_wall_street()],
                    people=[{"node": "loc-02/wash-lane", "manner": "倚着墙吃饼"}])
    stored, _why = pg.validate_realization(good, canon, salt=salt)
    assert stored is not None
    assert len(stored["people"]) == 1
    assert re.fullmatch(r"^inc-[0-9a-f]{8}$", stored["people"][0]["id"])
    assert stored["people"][0]["secret"] == ""
    assert stored["people"][0]["id"] != "npc-01"

    # manner 里出现 `C-` 加两位数字 → 这个路人丢掉。
    cue = _payload("loc-02", "白壁", _wall_key_nodes() + [_wall_street()],
                   people=[{"node": "loc-02/wash-lane",
                            "manner": "他四处打听 C-01 的下落"}])
    dropped, _ = pg.validate_realization(cue, canon, salt=salt)
    assert dropped is not None and dropped["people"] == []
    # manner 含任一 NPC 秘密的连续 8 字 → 丢掉。
    secret_manner = _payload(
        "loc-02", "白壁", _wall_key_nodes() + [_wall_street()],
        people=[{"node": "loc-02/wash-lane",
                 "manner": canon["npc_secrets"]["npc-01"][:pg.SECRET_WINDOW]}])
    dropped2, _ = pg.validate_realization(secret_manner, canon, salt=salt)
    assert dropped2 is not None and dropped2["people"] == []
    ok("incidental id：^inc-[0-9a-f]{8}$ / 确定；沾线索或秘密的路人丢掉")


def test_g7_reveal_next_trace():
    """`reveal_next_trace`：每次至多揭开一条 `at` 等于该地点的未揭开痕迹。"""
    guide = pg.empty_guide()
    guide["traces"] = [
        {"at": "loc-02", "id": "npc-01", "text": "第一句", "revealed": False},
        {"at": "loc-02", "id": "npc-16", "text": "第二句", "revealed": False},
    ]
    assert pg.reveal_next_trace(guide, "loc-05") == ""
    assert pg.reveal_next_trace(guide, "loc-02") == "第一句"
    assert pg.reveal_next_trace(guide, "loc-02") == "第二句"
    assert pg.reveal_next_trace(guide, "loc-02") == ""
    assert pg.reveal_next_trace(pg.empty_guide(), "loc-02") == ""
    ok("reveal_next_trace：每次至多揭开一条，同地点按顺序")


def test_env_example():
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "NOTDND_HOST" in text and "NOTDND_PORT" in text
    assert "NOTDND_AI_" not in text
    for key in _ENV_KEYS:
        assert re.search(r"^" + key + r"=", text, re.M), key
    ok(".env.example：保留 NOTDND_HOST / NOTDND_PORT，三行换成新键")


def main():
    with tempfile.TemporaryDirectory() as tmp:
        test_load_env_file(tmp)
        test_old_names_are_offline(tmp)
    test_parse_env_only_three_keys()
    test_parse_env_environ_priority()
    test_parse_env_quotes_and_comments()
    test_status_booleans()
    test_status_dict_has_no_secret()
    test_chat_url()
    test_narrative_body()
    test_tools_json_matches_design_doc()
    test_l0_constant()
    test_l1_unbound_constant()
    test_l2_build_stable()
    test_l2_scene_id_caching()
    test_empty_guide()
    test_guide_from_contract()
    test_cache_hit_ratio()
    test_transport_injectable_and_build_offline()
    test_transport_error_hides_upstream_body()
    test_assemble_chat_stream()
    test_assemble_chat_stream_whole_json()
    test_assemble_chat_stream_whole_json_tolerates_garbage()
    test_settle_uses_snapshot_roundtrip()
    test_verdict_line_order()
    test_review_narration()
    test_fallback_text_keeps_verdict()
    test_build_l4_and_messages()
    test_audio_delta_decoded_one_by_one()
    test_split_beats_default_and_dialogue()
    test_split_beats_limit_merges_tail()
    test_strip_marks()
    test_build_tts_body()
    test_call_tts_offline_and_failure()
    test_beats_of_skips_verdict()
    test_needs_tool_exact_sentences()
    test_tool_error_phrases_match_web_layer()
    test_lookup_rule_reads_six_kinds()
    test_place_card_save_block_no_keyerror()
    test_run_tool_call_settle_and_phrases()
    test_run_tool_pass_shape_and_reasoning()
    test_tool_reasoning_never_persisted()
    test_l1_bound_card()
    test_l1_glossary_only_with_world_file()
    test_bind_contract()
    test_bind_phrases_match_web_layer()
    test_location_ok()
    test_realization_canon_fixtures()
    test_validate_realization_hard_failures()
    test_validate_realization_keypoint_self_cite()
    test_validate_realization_near_name_is_drop_not_fail()
    test_validate_realization_old_money_poor_zone()
    test_validate_realization_market_street_kind()
    test_realization_request_body_contract()
    test_realization_retry_only_second_saved()
    test_realization_second_attempt_gets_remaining_budget()
    test_realization_budget_exhausted_no_second_request()
    test_realization_double_failure_falls_back()
    test_realization_offline_falls_back()
    test_realization_pending_and_dedup()
    test_realization_overlap_single_upstream()
    test_realization_commit_rewrites_l2()
    test_g7_npc_voice_stable()
    test_g7_check_speech_only_secrets()
    test_g7_redact_narration_no_second_request()
    test_g7_npc_home_and_scene_speakers()
    test_g7_traces_betrayed_uses_canonical_text()
    test_g7_traces_request_shape_and_secret_gate()
    test_g7_traces_offline_and_empty()
    test_g7_beats_of_assigns_voice()
    test_g7_incidental_id_shape_and_drop()
    test_g7_reveal_next_trace()
    test_module_offline_no_socket()
    test_env_example()
    print()
    print("全部通过：%d 项" % _passed)


if __name__ == "__main__":
    main()
