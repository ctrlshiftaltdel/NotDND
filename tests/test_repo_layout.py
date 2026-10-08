#!/usr/bin/env python3
"""占位回归测试：锁定仓库骨架不变量。

当前尚无后端，本测试对「已跟踪的仓库骨架」做**真实断言**（不是 `true` / 空壳）：
一旦必需文件缺失、被改名，或 README 的「仅限本机 / 局域网」警告被削弱，
本脚本即返回非零，`tests/run_all.sh` 随之变红。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_repo_layout.py` 运行。
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 仓库骨架：这些文件缺失 / 改名即视为回归。
REQUIRED_FILES = (
    "README.md",
    "LICENSE",
    "NOTICE",
    "AGENTS.md",
    "CONTRIBUTING.md",
    ".gitignore",
)

# README 必须保留「仅限本机 / 局域网」警告（不得削弱显著性）。
README_WARNING_MARKERS = (
    "仅限本机",
    "局域网",
    "请勿暴露到公网",
)


def check_required_files():
    """必需文件齐备。"""
    missing = [name for name in REQUIRED_FILES if not os.path.isfile(os.path.join(ROOT, name))]
    assert not missing, "仓库骨架缺少文件: %s" % ", ".join(missing)


def check_readme_warning():
    """README 仍带有本机 / 局域网警告。"""
    with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as handle:
        text = handle.read()
    missing = [marker for marker in README_WARNING_MARKERS if marker not in text]
    assert not missing, "README 警告被削弱或删除: 缺少 %s" % ", ".join(missing)


def main():
    checks = (check_required_files, check_readme_warning)
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
