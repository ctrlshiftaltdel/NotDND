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
- `assemble_chat_stream` / `read_usage`（流式与非流式回包）；
- `settle`：注水 → 结算 → 写回 → save，异常原样抛；
- `verdict_line` 取句顺序与「判定：成功」；
- `review_narration` 的骰子审查与裁决替换；`fallback_text` 保留裁决；
- `build_l4` / `build_narrative_messages` 的段落顺序与上限。
"""

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import prism_core  # noqa: E402
import prism_guide as pg  # noqa: E402

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
    # tool_calls 与 reasoning_content 的处理：前者收集，后者不落盘。
    streamed = pg.assemble_chat_stream(
        b'data: {"choices":[{"delta":{"tool_calls":[{"id":"t1"}]}}]}\n\n'
        b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n\n')
    assert streamed["tool_calls"] == [{"id": "t1"}]
    assert streamed["content"] == ""
    assert "think" not in json.dumps(streamed, ensure_ascii=False)
    # 非流式回包（message 而非 delta）。
    plain = pg.assemble_chat_stream(
        b'data: {"choices":[{"message":{"content":"\xe5\x81\x9c"}}]}\n\n')
    assert plain["content"] == "停"
    # 缺字段按 0。
    assert pg.read_usage({}) == {"prompt_tokens": 0, "cached_tokens": 0,
                                 "completion_tokens": 0, "reasoning_tokens": 0}
    ok("assemble_chat_stream：delta / message / usage / 坏块 / [DONE]")


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
    test_settle_uses_snapshot_roundtrip()
    test_verdict_line_order()
    test_review_narration()
    test_fallback_text_keeps_verdict()
    test_build_l4_and_messages()
    test_module_offline_no_socket()
    test_env_example()
    print()
    print("全部通过：%d 项" % _passed)


if __name__ == "__main__":
    main()
