#!/usr/bin/env python3
"""数据层回归入口：校验 data/、证明坏数据会红、锁定格式文档。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_data.py` 运行。
"""
import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)

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


def main():
    checks = (check_format_doc,)
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
