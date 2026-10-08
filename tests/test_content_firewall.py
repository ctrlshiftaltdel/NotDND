#!/usr/bin/env python3
"""内容防火墙 · 禁止路径回归测试。

把「铁律 1」点名的第三方数据路径从「记录在 AGENTS.md 里」升级为「CI 真会拦」：

  1. 命中：临时放入已知第三方派生数据路径的探针 → 防火墙**非零退出**并逐个点名；
  2. 不误伤：NotDND 自己合法的 `data/` 文件（含 `data/schema/monsters.schema.json`
     这类「名字像但合法」的命名）必须放行；
  3. 恢复：探针撤销后防火墙恢复绿灯，且探针路径在索引与工作区中都不留痕。

探针用 `git add -N -f`（intent-to-add）登记进索引——防火墙扫的是 `git ls-files`，
无需 commit 即可被看见；`-f` 是因为 `corpus/` 已被 `.gitignore` 忽略。
用例无论成功还是断言失败，`finally` 都会撤销登记并删除文件。

为控制耗时，三类场景各只跑一次防火墙（共三次）。

零依赖：仅 Python 3 标准库；直接 `python3 tests/test_content_firewall.py` 运行。
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 应当被防火墙拦下的路径（AGENTS.md 铁律 1 点名的第三方派生数据路径）。
HIT_PROBES = (
    "data/monsters.json",
    "data/spells.json",
    "data/species.json",
    "data/backgrounds.json",
    "data/campaigns/lost-mine.json",
    "data/corpus/srd.json",
)

# 应当被放行的路径（NotDND 自己的合法数据，含「名字像但合法」的 schema 文件）。
SAFE_PROBES = (
    "data/system/attributes.json",
    "data/worlds/ember.json",
    "data/schema/monsters.schema.json",
)

PROBE_CONTENT = "{}\n"
FIREWALL_TIMEOUT = 300  # 秒；本地 Windows 上 git grep 较慢，CI 上远快于此


def git(*args):
    """在工作区根跑一条 git 命令，失败即抛错（不静默）。"""
    result = subprocess.run(
        ["git"] + list(args), cwd=ROOT, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            "git %s 失败(%d): %s" % (" ".join(args), result.returncode, result.stderr.strip())
        )
    return result.stdout


def run_firewall():
    """跑一次内容防火墙，返回 (退出码, 合并后的输出)。"""
    result = subprocess.run(
        ["bash", "tests/content_firewall.sh"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=FIREWALL_TIMEOUT,
    )
    return result.returncode, result.stdout + result.stderr


def stage_probes(paths):
    """建立探针文件并登记进索引（不 commit），返回**本次新建**的路径。

    已在索引里的文件（如仓库自带的 data/system/attributes.json）跳过：
    防火墙本就能看见它们，改写或删除都会破坏仓库内容。
    """
    tracked = set(git("ls-files").splitlines())
    created = []
    for path in paths:
        if path in tracked:
            continue
        full = os.path.join(ROOT, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(PROBE_CONTENT)
        git("add", "-N", "-f", "--", path)
        created.append(path)
    return created


def unstage_probes(paths):
    """撤销索引登记、只删除本次新建的文件，并清理由此变空的目录。"""
    for path in paths:
        git("reset", "-q", "--", path)
        full = os.path.join(ROOT, path)
        if os.path.isfile(full):
            os.remove(full)
    for path in paths:
        parent = os.path.dirname(os.path.join(ROOT, path))
        while parent.startswith(ROOT) and parent != ROOT:
            if os.path.isdir(parent) and not os.listdir(parent):
                os.rmdir(parent)
            else:
                break
            parent = os.path.dirname(parent)


def check_hit_probes():
    """禁止路径探针必须让防火墙变红，并逐个点名。"""
    created = []
    try:
        created = stage_probes(HIT_PROBES)
        code, output = run_firewall()
        assert code != 0, "防火墙未拦截第三方数据路径（退出码 0）"
        missed = [path for path in HIT_PROBES if path not in output]
        assert not missed, "防火墙未点名: %s\n输出：\n%s" % (", ".join(missed), output)
    finally:
        unstage_probes(created)


def check_safe_probes():
    """合法的 data/ 文件（含疑似命名）必须放行。"""
    created = []
    try:
        created = stage_probes(SAFE_PROBES)
        code, output = run_firewall()
        assert code == 0, "防火墙误伤合法 data/ 文件（退出码 %d）：\n%s" % (code, output)
    finally:
        unstage_probes(created)


def check_workspace_clean():
    """探针撤销后：防火墙恢复绿灯，且探针路径在索引与工作区中都不留痕。"""
    code, output = run_firewall()
    assert code == 0, "撤销探针后防火墙仍红（退出码 %d）：\n%s" % (code, output)
    leftovers = git("status", "--porcelain", "--", *(HIT_PROBES + SAFE_PROBES)).strip()
    assert not leftovers, "探针撤销后仍有残留：\n%s" % leftovers
    for path in HIT_PROBES + SAFE_PROBES:
        if path in set(git("ls-files").splitlines()):
            continue  # 仓库自带文件，本就不该被删
        assert not os.path.exists(os.path.join(ROOT, path)), "探针文件未删除: %s" % path


def main():
    checks = (check_hit_probes, check_safe_probes, check_workspace_clean)
    failures = 0
    for check in checks:
        try:
            check()
        except (AssertionError, RuntimeError, subprocess.TimeoutExpired) as error:
            print("  断言失败: %s" % error)
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(checks) - failures, len(checks), failures))
        return 1
    print("%d/%d 项通过" % (len(checks), len(checks)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
