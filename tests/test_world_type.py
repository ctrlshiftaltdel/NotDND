#!/usr/bin/env python3
"""world_type 契约（Issue #316）：显式声明、缺省 modern、战役覆盖、快照只读。

三份世界模组的取值写在数据里，不从 engine / 名称 / tone_pack 推。
会话快照只在读模型里给出解析结果，不把主题写回存档，也不迁移老档。
"""
import json
import os
import pathlib
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

import notdnd_web  # noqa: E402
import validate_data  # noqa: E402

SCHEMA_DIR = os.path.join(ROOT, "data", "schema")

# 文件上的显式取值。threshold 与 emberfall 同为 tactics，主题必须不同。
EXPLICIT = {
    "threshold": ("modern", "tactics"),
    "emberfall": ("fantasy", "tactics"),
    "entropic-net": ("future", "build"),
}

# 已发布剧本没有 world_type：同 engine 或同 tone_pack 仍必须跟模板走。
BOUND_SCENARIOS = (
    # world_key, scenario_id, 期望主题, engine, tone_pack
    ("threshold", "rain_line_seven", "modern", "tactics", "冷硬"),
    ("emberfall", "well_temperature", "fantasy", "tactics", "抒情"),
    ("entropic-net", "backup_forty_one", "future", "build", "冷硬"),
)


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _session(sid, world_key="", scenario_id=""):
    session = notdnd_web.Session(sid)
    if world_key:
        session.guide["world_key"] = world_key
    if scenario_id:
        session.guide["scenario_id"] = scenario_id
    return session


def check_modules_declare_world_type():
    """三份模组各有显式 world_type；两个 tactics 世界主题不同。"""
    seen_engines = {}
    for key, (theme, engine) in EXPLICIT.items():
        path = os.path.join(ROOT, "data", "worlds", key + ".json")
        block = _load(path)["world"]
        assert block.get("world_type") == theme, (key, block.get("world_type"))
        assert block.get("engine") == engine, (key, block.get("engine"))
        assert block["world_type"] in notdnd_web.WORLD_TYPE_VALUES
        seen_engines.setdefault(engine, set()).add(theme)
    assert seen_engines["tactics"] == {"modern", "fantasy"}, seen_engines
    errors = []
    for key in EXPLICIT:
        errors.extend(validate_data.validate_file(
            os.path.join(ROOT, "data", "worlds", key + ".json"), SCHEMA_DIR, ROOT))
    assert not errors, errors


def check_resolve_priority_and_default():
    """纯函数：有效覆盖 > 模板 > modern。engine / 名称 / 脏值都不是主题。"""
    cases = (
        (None, None, "modern"),
        ("", None, "modern"),
        ("tactics", None, "modern"),
        ("build", None, "modern"),
        ("余烬纪元", None, "modern"),
        ("Fantasy", None, "modern"),
        (" fantasy ", None, "modern"),
        ("noir", None, "modern"),
        ("fantasy", None, "fantasy"),
        ("modern", None, "modern"),
        ("future", None, "future"),
        ("fantasy", "future", "future"),
        ("future", "fantasy", "fantasy"),
        ("fantasy", "modern", "modern"),
        ("fantasy", "noir", "fantasy"),
        ("fantasy", "tactics", "fantasy"),
        ("fantasy", "", "fantasy"),
        ("fantasy", None, "fantasy"),
        ("nope", "future", "future"),
        ("nope", "nope", "modern"),
        (None, "space-opera", "modern"),
        ("fantasy", 1, "fantasy"),
        ("fantasy", True, "fantasy"),
        ("fantasy", "future ", "fantasy"),
    )
    for template, override, expected in cases:
        got = notdnd_web.resolve_world_type(template, override)
        assert got == expected, (template, override, got, expected)


def check_snapshot_of_three_worlds():
    """只绑世界、不绑剧本：快照读模板声明，world 块原有键不变。"""
    for key, (theme, _engine) in EXPLICIT.items():
        session = _session("wt-" + key, world_key=key)
        before = json.dumps(session.guide, sort_keys=True, ensure_ascii=False)
        view = notdnd_web.rules_view(session)
        assert view["world_type"] == theme, (key, view["world_type"])
        assert set(view["world"]) == {"key", "name"}, view["world"]
        assert view["world"]["key"] == key
        assert view["scenario"] == {}
        assert "world_type" not in session.guide
        after = json.dumps(session.guide, sort_keys=True, ensure_ascii=False)
        assert after == before, "读快照不得改 guide"
    unbound = notdnd_web.rules_view(_session("wt-unbound"))
    assert unbound["world"] == {} and unbound["scenario"] == {}
    assert unbound["world_type"] == "modern"


def check_published_scenarios_inherit_not_infer():
    """已发布剧本不写 world_type。同 engine、同 tone_pack 也不改主题。"""
    themes_by_engine = {}
    themes_by_tone = {}
    for world_key, scenario_id, theme, engine, tone in BOUND_SCENARIOS:
        meta = notdnd_web._scenario_meta(scenario_id) or {}
        assert "world_type" not in meta, (scenario_id, meta)
        assert meta.get("engine") == engine, (scenario_id, meta.get("engine"))
        assert meta.get("tone_pack") == tone, (scenario_id, meta.get("tone_pack"))
        assert meta.get("world") == world_key
        session = _session("wt-" + scenario_id, world_key, scenario_id)
        view = notdnd_web.rules_view(session)
        assert view["world_type"] == theme, (scenario_id, view["world_type"])
        assert view["scenario"]["id"] == scenario_id
        assert "world_type" not in session.guide
        themes_by_engine.setdefault(engine, set()).add(theme)
        themes_by_tone.setdefault(tone, set()).add(theme)
    assert themes_by_engine["tactics"] == {"modern", "fantasy"}, themes_by_engine
    assert len(themes_by_tone["冷硬"]) == 2, themes_by_tone


def check_campaign_override_beats_template():
    """临时战役 meta：有效覆盖优先；不认识的覆盖回退模板，不看 engine / tone_pack。"""
    fixture_id = "wt-fixture-override"
    base = {
        "world": "emberfall",
        "engine": "tactics",
        "campaign": "夹具覆盖",
        "tone_pack": "noir",
        "world_type": "future",
    }
    session = _session("wt-override", "emberfall", fixture_id)
    notdnd_web._SCENARIO_META_CACHE[fixture_id] = dict(base)
    try:
        view = notdnd_web.rules_view(session)
        assert view["world_type"] == "future", view["world_type"]
        assert view["world"]["key"] == "emberfall"
        assert notdnd_web._load_world("emberfall")["world"]["engine"] == "tactics"
        assert "world_type" not in session.guide

        rejected = dict(base)
        rejected["world_type"] = "noir"
        notdnd_web._SCENARIO_META_CACHE[fixture_id] = rejected
        assert notdnd_web.rules_view(session)["world_type"] == "fantasy"

        rejected["world_type"] = "tactics"
        assert notdnd_web.rules_view(session)["world_type"] == "fantasy"

        inherited = dict(base)
        del inherited["world_type"]
        notdnd_web._SCENARIO_META_CACHE[fixture_id] = inherited
        assert "world_type" not in inherited
        assert notdnd_web.rules_view(session)["world_type"] == "fantasy"
    finally:
        notdnd_web._SCENARIO_META_CACHE.pop(fixture_id, None)


def check_unknown_name_is_not_a_theme():
    """世界键看起来像奇幻，也没有模组文件：缺省 modern，不按名字猜。"""
    session = _session("wt-named", world_key="fantasy-kingdom")
    view = notdnd_web.rules_view(session)
    assert view["world_type"] == "modern", view["world_type"]
    assert view["world"] == {"key": "fantasy-kingdom", "name": "fantasy-kingdom"}
    assert "world_type" not in session.guide


def check_missing_or_unknown_field_ignores_engine():
    """模板缺字段或不认识时落 modern，即使 engine 仍是 tactics。"""
    block = notdnd_web._load_world("threshold")["world"]
    saved = block["world_type"]
    assert saved == "modern" and block["engine"] == "tactics"
    session = _session("wt-field", world_key="threshold")
    try:
        del block["world_type"]
        assert notdnd_web.rules_view(session)["world_type"] == "modern"
        block["world_type"] = "future"
        assert block["engine"] == "tactics"
        assert notdnd_web.rules_view(session)["world_type"] == "future"
        block["world_type"] = "tactics"
        assert notdnd_web.rules_view(session)["world_type"] == "modern"
    finally:
        block["world_type"] = saved
    assert notdnd_web._load_world("threshold")["world"]["world_type"] == "modern"


def check_read_does_not_rewrite_legacy_save():
    """老档没有 world_type：读快照现算主题，磁盘字节不变。"""
    original = notdnd_web.SAVE_DIR
    try:
        with tempfile.TemporaryDirectory() as tmp:
            notdnd_web.SAVE_DIR = pathlib.Path(tmp)
            samples = (
                ("old-unbound", {}, "modern"),
                ("old-ember", {"world_key": "emberfall", "scenario_id": ""}, "fantasy"),
            )
            for sid, extra, theme in samples:
                guide = {"salt": "ab" * 16}
                guide.update(extra)
                path = pathlib.Path(tmp) / (sid + ".json")
                path.write_text(json.dumps({
                    "sid": sid,
                    "created": 0,
                    "save_name": "",
                    "log": [],
                    "seq": 0,
                    "guide": guide,
                }, ensure_ascii=False), encoding="utf-8")
                before = path.read_bytes()
                assert b"world_type" not in before
                session = notdnd_web.Session.load(sid)
                assert session is not None, sid
                view = notdnd_web.rules_view(session)
                assert view["world_type"] == theme, (sid, view["world_type"])
                assert "world_type" not in session.guide
                assert path.read_bytes() == before, sid
    finally:
        notdnd_web.SAVE_DIR = original


def _schema(name):
    return _load(os.path.join(SCHEMA_DIR, name))


def _world_sample(**world):
    body = {"key": "threshold", "name": "阈界都市", "engine": "tactics"}
    body.update(world)
    return {
        "schema_version": 1,
        "kind": "world.module",
        "source": "docs/scenario/05-world-module-i-threshold.md:27",
        "world": body,
        "factions": [],
        "tracks": [],
        "careers": [],
        "abilities": [],
        "equipment": [],
        "enemies": [],
        "random_tables": [],
    }


def _scenario_sample(meta_extra=None):
    meta = {"world": "foundered-strata", "engine": "tactics"}
    if meta_extra:
        meta.update(meta_extra)
    return {
        "schema_version": 1,
        "kind": "scenario.campaign",
        "source": "docs/scenario/s5-foundered-strata-ruins-and-caverns.md:2423",
        "meta": meta,
        "acts": [],
        "threads": [],
        "ledger_template": {
            "meta": {"world": "foundered-strata", "engine": "tactics"},
            "clock": {},
            "party": [],
            "npcs": [],
            "threads": [],
            "facts": [],
            "hooks": [],
        },
    }


def check_schema_enum_optional():
    """字段可选；在场时只许三个取值。缺省不写仍然合法。"""
    world_schema = _schema("world.schema.json")
    scenario_schema = _schema("scenario.schema.json")
    assert not validate_data.check_schema(_world_sample(), world_schema, SCHEMA_DIR)
    for theme in ("fantasy", "modern", "future"):
        assert not validate_data.check_schema(
            _world_sample(world_type=theme), world_schema, SCHEMA_DIR), theme
        sample = _scenario_sample({"world_type": theme})
        assert not validate_data.check_schema(sample, scenario_schema, SCHEMA_DIR), theme
    assert not validate_data.check_schema(_scenario_sample(), scenario_schema, SCHEMA_DIR)
    for bad in ("tactics", "build", "noir", "Modern", ""):
        assert validate_data.check_schema(
            _world_sample(world_type=bad), world_schema, SCHEMA_DIR), bad
        assert validate_data.check_schema(
            _scenario_sample({"world_type": bad}), scenario_schema, SCHEMA_DIR), bad


def check_format_documents_contract():
    """FORMAT.md 写明枚举、缺省、优先级，以及禁止用 engine / 名称 / tone_pack 推导。"""
    text = pathlib.Path(ROOT, "data", "FORMAT.md").read_text(encoding="utf-8")
    for phrase in (
        "合法值只有 `fantasy`、`modern`、`future`",
        "缺省为 `modern`",
        "有效覆盖优先于模板",
        "**不得**用 `engine`、世界名、叙事文本或 `tone_pack` 推导",
        "只读",
        "ledger_template.meta",
    ):
        assert phrase in text, phrase


def main():
    checks = (
        check_modules_declare_world_type,
        check_resolve_priority_and_default,
        check_snapshot_of_three_worlds,
        check_published_scenarios_inherit_not_infer,
        check_campaign_override_beats_template,
        check_unknown_name_is_not_a_theme,
        check_missing_or_unknown_field_ignores_engine,
        check_read_does_not_rewrite_legacy_save,
        check_schema_enum_optional,
        check_format_documents_contract,
    )
    failures = 0
    for check in checks:
        try:
            check()
        except Exception as error:  # noqa: BLE001 — 逐项报告，不让一项挡住其余
            print("  失败: %s: %s" % (check.__name__, error))
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks), failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
