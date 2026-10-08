#!/usr/bin/env python3
"""数据层回归入口：校验 data/、证明坏数据会红、锁定格式文档。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_data.py` 运行。
"""
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


def main():
    checks = (
        check_format_doc,
        check_keyword_engine,
        check_one_of_edges,
        check_unsupported_keyword,
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
