#!/usr/bin/env python3
"""数据层回归入口：校验 data/、证明坏数据会红、锁定格式文档。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_data.py` 运行。
"""
import glob
import json
import os
import subprocess
import sys
import tempfile

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.path.insert(0, TESTS)

import validate_data  # noqa: E402  (tests/ 已加入 sys.path)

# FORMAT.md 必须覆盖的关键字子集与 canonical 口径（缺一即红）。
FORMAT_KEYWORDS = (
    "type", "required", "properties", "additionalProperties", "items",
    "enum", "const", "pattern", "minLength", "minimum", "maximum",
    "uniqueItems", "$ref", "$defs", "anyOf", "oneOf",
)
FORMAT_MARKERS = (
    "party", "hooks", "nemeses", "affinity", "awe", "flags", "evidence", "factions",
    "cast", "pending_hooks", "nemesis", "affinity_to_party", "awe_to_party",
    "docs/", "散文", "schema_version", "kind", "registry.json",
    "未支持的关键字",
)


def check_format_doc():
    """data/FORMAT.md 存在，且覆盖关键字子集与 canonical 口径。"""
    path = os.path.join(ROOT, "data", "FORMAT.md")
    assert os.path.isfile(path), "缺少 data/FORMAT.md"
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    missing = [m for m in FORMAT_MARKERS if m not in text]
    assert not missing, "FORMAT.md 缺少标记: %s" % ", ".join(missing)
    missing_kw = [k for k in FORMAT_KEYWORDS if k not in text]
    assert not missing_kw, "FORMAT.md 未覆盖关键字: %s" % ", ".join(missing_kw)


# 关键字子集：(名字, schema, 合法实例, 非法实例)
KEYWORD_CASES = (
    ("type", {"type": "string"}, "x", 1),
    ("integer-not-bool", {"type": "integer"}, 1, True),
    ("required", {"type": "object", "required": ["a"]}, {"a": 1}, {}),
    ("properties", {"type": "object", "properties": {"a": {"type": "integer"}}},
     {"a": 1}, {"a": "x"}),
    ("additionalProperties-false",
     {"type": "object", "properties": {"a": {}}, "additionalProperties": False},
     {"a": 1}, {"a": 1, "b": 2}),
    ("items", {"type": "array", "items": {"type": "integer"}}, [1, 2], [1, "x"]),
    ("enum", {"enum": ["掩体", "危险"]}, "掩体", "机关"),
    ("const", {"const": 1}, 1, 2),
    ("pattern", {"type": "string", "pattern": "^docs/"}, "docs/x.md:1", "x.md:1"),
    ("minLength", {"type": "string", "minLength": 2}, "ab", "a"),
    ("minimum", {"type": "integer", "minimum": 2}, 2, 1),
    ("maximum", {"type": "integer", "maximum": 2}, 2, 3),
    ("uniqueItems", {"type": "array", "uniqueItems": True}, [1, 2], [1, 1]),
    ("$ref-$defs",
     {"$defs": {"s": {"type": "string"}}, "type": "object",
      "properties": {"a": {"$ref": "#/$defs/s"}}},
     {"a": "x"}, {"a": 1}),
    ("anyOf", {"anyOf": [{"type": "integer"}, {"type": "null"}]}, None, "x"),
    # oneOf：恰好一个。good 只匹配 string；bad 两个都不匹配。
    ("oneOf", {"oneOf": [{"type": "string"}, {"type": "integer"}]}, "x", True),
    # $ref 的兄弟关键字必须一并生效（draft 2020-12）。
    ("$ref-with-sibling",
     {"$defs": {"s": {"type": "string"}}, "$ref": "#/$defs/s", "minLength": 5},
     "abcdef", "ab"),
)


def check_keyword_engine():
    """校验器子集：合法实例过、非法实例红。"""
    failures = []
    for name, schema, good, bad in KEYWORD_CASES:
        if validate_data.check_schema(good, schema, TESTS):
            failures.append("%s: 合法实例被判失败" % name)
        if not validate_data.check_schema(bad, schema, TESTS):
            failures.append("%s: 非法实例未被判失败" % name)
    assert not failures, "; ".join(failures)


def check_one_of_edges():
    """oneOf：0 匹配 / 2 匹配都失败，恰好 1 匹配通过。"""
    schema = {"oneOf": [{"type": "number"}, {"type": "integer"}]}
    assert validate_data.check_schema(5, schema, TESTS), "2 匹配未被判失败"
    assert validate_data.check_schema("x", schema, TESTS), "0 匹配未被判失败"
    assert not validate_data.check_schema(1.5, schema, TESTS), "恰好 1 匹配被判失败"


def check_unsupported_keyword():
    """未知关键字被拒绝；支持的关键字 / 注解不误报。"""
    bad = {"type": "object", "minItems": 1}
    assert validate_data.collect_unsupported(bad), "未识别 minItems 为未支持关键字"
    ok = {"type": "array", "items": {"type": "string"},
          "$defs": {"s": {"type": "string"}}, "title": "t"}
    assert not validate_data.collect_unsupported(ok), "误报支持的关键字"


SCHEMA_DIR = os.path.join(ROOT, "data", "schema")


def check_schemas_keyword_clean():
    """data/schema/*.schema.json 只使用文档化子集。"""
    bad = []
    for path in sorted(glob.glob(os.path.join(SCHEMA_DIR, "*.schema.json"))):
        with open(path, encoding="utf-8") as handle:
            schema = json.load(handle)
        for where, kw in validate_data.collect_unsupported(schema):
            bad.append("%s %s: %s" % (os.path.basename(path), where, kw))
    assert not bad, "schema 使用了未支持关键字: %s" % "; ".join(bad)


def check_registry():
    """登记表覆盖四族，且指向存在的 schema 文件。"""
    with open(os.path.join(SCHEMA_DIR, "registry.json"), encoding="utf-8") as handle:
        registry = json.load(handle)
    kinds = registry["kinds"]
    for expected in ("system.attributes", "world.module",
                     "random_tables.catalog", "scenario.campaign"):
        assert expected in kinds, "registry 缺少 kind: %s" % expected
        assert os.path.isfile(os.path.join(SCHEMA_DIR, kinds[expected])), \
            "registry 指向不存在的 schema: %s" % kinds[expected]


def check_system_schema_validation():
    """system.schema.json 接受合法样本、拒绝违反样本。"""
    with open(os.path.join(SCHEMA_DIR, "system.schema.json"), encoding="utf-8") as handle:
        schema = json.load(handle)
    good = {
        "schema_version": 1, "kind": "system.attributes",
        "source": "docs/system/01-内核CORE.md:30",
        "attributes": [{"id": "MGT", "name": "力道", "en": "Might",
                        "question": "你能施加多大的外力？",
                        "source": "docs/system/01-内核CORE.md:36"}],
        "levels": [{"value": 1, "label": "严重缺陷",
                    "source": "docs/system/01-内核CORE.md:49"}],
        "modifiers": [{"value": 1, "modifier": -2,
                       "source": "docs/system/01-内核CORE.md:64"}],
    }
    assert not validate_data.check_schema(good, schema, SCHEMA_DIR), \
        "合法样本被判失败"
    bad = json.loads(json.dumps(good))
    bad["attributes"][0]["id"] = "STR"
    assert validate_data.check_schema(bad, schema, SCHEMA_DIR), \
        "非法属性 id 未被判失败"


def check_family_schemas():
    """world / random_tables / scenario 三族：最小样本过，缺 kind 红。"""
    samples = {
        "world.schema.json": {
            "schema_version": 1, "kind": "world.module",
            "source": "docs/scenario/05-世界模组I-阈界都市.md:27",
            "world": {"key": "threshold", "name": "阈界都市", "engine": "tactics"},
            "factions": [], "tracks": [], "careers": [], "abilities": [],
            "equipment": [], "enemies": [], "random_tables": [],
        },
        "random_tables.schema.json": {
            "schema_version": 1, "kind": "random_tables.catalog",
            "source": "docs/scenario/S7-随机生成器.md:2702",
            "resource": "prism.random_tables", "tables": [], "generators": [],
            "ledger_bridge": {},
        },
        "scenario.schema.json": {
            "schema_version": 1, "kind": "scenario.campaign",
            "source": "docs/scenario/S5-沉层-遗迹与地窟.md:2423",
            "meta": {"world": "foundered-strata", "engine": "tactics"},
            "acts": [], "threads": [],
            "ledger_template": {
                "meta": {"world": "foundered-strata", "engine": "tactics"},
                "clock": {}, "party": [], "npcs": [], "threads": [],
                "facts": [], "hooks": [],
            },
        },
    }
    for filename, sample in samples.items():
        with open(os.path.join(SCHEMA_DIR, filename), encoding="utf-8") as handle:
            schema = json.load(handle)
        assert not validate_data.check_schema(sample, schema, SCHEMA_DIR), \
            "%s 拒绝了合法样本" % filename
        broken = json.loads(json.dumps(sample))
        del broken["kind"]
        assert validate_data.check_schema(broken, schema, SCHEMA_DIR), \
            "%s 未对缺 kind 报错" % filename


FIXTURES = os.path.join(TESTS, "fixtures")


def check_good_fixture_passes():
    """最小合法 fixture 通过；真实 data/ 无错误。

    真实 data/ 目前（试点数据之前）没有数据文件，故此处不断言文件数 ≥1；
    Task 6 落试点数据后另测真实树。
    """
    assert not validate_data.validate_file(
        os.path.join(FIXTURES, "good", "system_attributes_min.json"), SCHEMA_DIR, ROOT)
    _checked, errors = validate_data.validate_tree(
        os.path.join(ROOT, "data"), SCHEMA_DIR, ROOT)
    assert not errors, "真实 data/ 校验失败: %s" % "; ".join(errors)


BAD_FIXTURE_MARKERS = {
    "unknown_kind.json": "kind",
    "missing_required.json": "required",
    "bad_enum.json": "enum",
    "bad_type.json": "type",
    "bad_source_format.json": "pattern",
    "missing_source_file.json": "不存在",
    "source_line_overflow.json": "行",
    "duplicate_id.json": "重复",
}


def check_bad_fixtures_fail():
    """每份坏数据都被点名报错。"""
    problems = []
    for name, marker in BAD_FIXTURE_MARKERS.items():
        errors = validate_data.validate_file(
            os.path.join(FIXTURES, "bad", name), SCHEMA_DIR, ROOT)
        if not errors:
            problems.append("%s: 未被判失败" % name)
        elif not any(marker in e for e in errors):
            problems.append("%s: 报错未含 %r（实为 %s）" % (name, marker, errors))
    assert not problems, "; ".join(problems)


def check_empty_data_dir_ok():
    """空数据目录打印提示并 exit 0。"""
    with tempfile.TemporaryDirectory() as tmp:
        result = subprocess.run(
            [sys.executable, os.path.join(TESTS, "validate_data.py"),
             "--data-dir", tmp, "--schema-dir", SCHEMA_DIR, "--root", ROOT],
            capture_output=True, text=True)
    assert result.returncode == 0, "空数据目录应 exit 0，实为 %d" % result.returncode
    assert "未发现数据文件" in result.stdout, "缺少空数据提示"


def check_pilot_attributes():
    """试点数据字段自洽，且过 schema；真实 data/ 树含至少 1 个数据文件。"""
    path = os.path.join(ROOT, "data", "system", "attributes.json")
    assert os.path.isfile(path), "缺少 data/system/attributes.json"
    errors = validate_data.validate_file(path, SCHEMA_DIR, ROOT)
    assert not errors, "试点数据未过校验: %s" % "; ".join(errors)
    checked, tree_errors = validate_data.validate_tree(
        os.path.join(ROOT, "data"), SCHEMA_DIR, ROOT)
    assert checked >= 1, "真实 data/ 未发现数据文件"
    assert not tree_errors, "真实 data/ 校验失败: %s" % "; ".join(tree_errors)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    assert [a["id"] for a in data["attributes"]] == ["MGT", "FIN", "VIG", "INS", "MND", "PRE"]
    expected = {1: -2, 2: -1, 3: -1, 4: 0, 5: 0, 6: 1, 7: 1, 8: 2, 9: 2, 10: 3}
    got = {m["value"]: m["modifier"] for m in data["modifiers"]}
    assert got == expected, "属性修正表与 docs/system/01 不一致: %s" % got
    assert sorted(l["value"] for l in data["levels"]) == list(range(1, 11)), "等级表应覆盖 1–10"


def check_file_errors_attributed():
    """validate_file 的每条错误都标注文件路径（多文件时不致混淆）。"""
    path = os.path.join(FIXTURES, "bad", "bad_enum.json")
    errors = validate_data.validate_file(path, SCHEMA_DIR, ROOT)
    assert errors, "bad_enum 应报错"
    assert all(e.startswith(path + ":") for e in errors), \
        "错误未标注文件路径: %s" % errors


def check_source_line_boundary():
    """source 行号 == 文件总行数允许；> 总行数报错。"""
    doc = os.path.join(ROOT, "docs", "system", "01-内核CORE.md")
    with open(doc, encoding="utf-8") as handle:
        total = sum(1 for _ in handle)
    base = json.load(open(os.path.join(FIXTURES, "good", "system_attributes_min.json"),
                          encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        cases = {}
        for name, line in (("at", total), ("over", total + 1)):
            data = json.loads(json.dumps(base))
            data["attributes"][0]["source"] = "docs/system/01-内核CORE.md:%d" % line
            cases[name] = os.path.join(tmp, "%s.json" % name)
            with open(cases[name], "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
        assert not validate_data.validate_file(cases["at"], SCHEMA_DIR, ROOT), \
            "行号 == 总行数应允许"
        assert validate_data.validate_file(cases["over"], SCHEMA_DIR, ROOT), \
            "行号 > 总行数应报错"


def check_cli_bad_exit_nonzero():
    """CLI 在坏数据目录上 exit 非零。"""
    result = subprocess.run(
        [sys.executable, os.path.join(TESTS, "validate_data.py"),
         "--data-dir", os.path.join(FIXTURES, "bad"),
         "--schema-dir", SCHEMA_DIR, "--root", ROOT],
        capture_output=True, text=True)
    assert result.returncode != 0, "坏数据目录应 exit 非零，实为 %d" % result.returncode


def check_readme_data_section():
    """README 有数据层小节，且原有本机 / 局域网警告未被削弱。"""
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
        text = handle.read()
    for marker in ("数据层", "data/FORMAT.md", "tests/validate_data.py"):
        assert marker in text, "README 缺少数据层标记: %s" % marker
    for marker in ("仅限本机", "局域网", "请勿暴露到公网"):
        assert marker in text, "README 警告被削弱: %s" % marker


def main():
    checks = (
        check_format_doc,
        check_keyword_engine,
        check_one_of_edges,
        check_unsupported_keyword,
        check_schemas_keyword_clean,
        check_registry,
        check_system_schema_validation,
        check_family_schemas,
        check_good_fixture_passes,
        check_bad_fixtures_fail,
        check_empty_data_dir_ok,
        check_pilot_attributes,
        check_file_errors_attributed,
        check_source_line_boundary,
        check_cli_bad_exit_nonzero,
        check_readme_data_section,
    )
    failures = 0
    for check in checks:
        try:
            check()
        except AssertionError as error:
            print("  断言失败: %s" % error)
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks), failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
