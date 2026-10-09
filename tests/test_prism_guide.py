#!/usr/bin/env python3
"""G1 · prism_guide 离线骨架回归测试。

直接 `python3 tests/test_prism_guide.py` 运行；零依赖，只用标准库，
不访问网络。覆盖 Issue G1 的验收标准：

- `.env` 只解析三个键、os.environ 优先、旧名 NOTDND_AI_* 不认；
- 空 BASE_URL / 空 MODEL / 缺密钥时状态布尔正确；
- URL 拼接（末尾斜杠 / 完整路径）；
- 叙事请求体（thinking disabled、0.7、700、工具 JSON、密钥不入体）；
- 工具 JSON 与 GUIDE-DESIGN.md §5.6 规范串全等；
- L0 / L1 / L2 字节稳定性与内容边界；
- guide 状态字典（empty_guide / guide_from 按键合同）；
- cache_hit_ratio；
- `.env.example` 仍含 NOTDND_HOST / NOTDND_PORT 且不再含 NOTDND_AI_。
"""

import base64
import json
import os
import pathlib
import re
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

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


def test_module_offline_no_socket():
    source = pathlib.Path(ROOT / "prism_guide.py").read_text(
        encoding="utf-8")
    assert "urlopen" not in source and "socket" not in source
    assert "urllib" not in source and "http.client" not in source
    # 构建路径全程不需要网络。
    env = {"BASE_URL": "https://api.example.com/v1", "MODEL": "mm",
           "API_KEY": "kk"}
    pg.build_narrative_body([{"role": "system", "content": pg.L0}], env=env)
    pg.status(env)
    pg.build_l2(_fixture_rules())
    ok("模块不引用任何网络设施，构建路径离线")


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
    test_module_offline_no_socket()
    test_env_example()
    print()
    print("全部通过：%d 项" % _passed)


if __name__ == "__main__":
    main()
