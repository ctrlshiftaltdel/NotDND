#!/usr/bin/env python3
"""数据层回归入口：校验 data/、证明坏数据会红、锁定格式文档。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_data.py` 运行。
"""
import glob
import json
import os
import sys

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
