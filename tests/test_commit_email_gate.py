#!/usr/bin/env python3
"""回归测试：提交邮箱门（tests/commit_email_gate.sh）。

在临时 git 仓库里**真造提交**，断言门禁行为可机器复现：

  1. author / committer 均为 GitHub noreply → 门通过（退出 0）；
  2. author 为真实邮箱 → 门失败（非零）并点名该邮箱；
  3. committer 为真实邮箱 → 同样失败（author 合规也救不了）；
  4. `noreply@github.com`（GitHub 网页 / API 合并提交）在允许集合内 → 通过；
  5. **旧历史里的真实邮箱不误红**：真实邮箱只在范围之外，范围内全 noreply → 通过；
  6. 大小写变体（`Noreply@Users.Noreply.GitHub.com`）不得绕过 → 通过。

零依赖：仅 Python 3 标准库 + git。直接 `python3 tests/test_commit_email_gate.py` 运行。
"""

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GATE = os.path.join(ROOT, "tests", "commit_email_gate.sh")

NOREPLY = "123456+bot@users.noreply.github.com"        # 本地实现 Agent 应配的形状
NOREPLY_MIXED = "123456+Bot@Users.Noreply.GitHub.com"  # 大小写变体
WEB_MERGE = "noreply@github.com"                       # GitHub 网页 / API 合并
# 真实邮箱：应当被拦。用拼接构造——本文件也在内容防火墙的扫描范围内，
# 写成整串会被「真实邮箱」规则扫成命中（同 test_content_firewall.py 的假密钥做法）。
REAL = "someone" + "@" + "gmail" + ".com"


def _run(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True)


def _git(cwd, *args):
    result = _run(["git", *args], cwd)
    if result.returncode != 0:
        raise RuntimeError("git %s 失败: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout


def _init(cwd):
    _git(cwd, "init", "-q")
    _git(cwd, "config", "user.name", "Tester")
    _git(cwd, "config", "user.email", NOREPLY)
    _git(cwd, "config", "commit.gpgsign", "false")   # 免去本地全局签名配置干扰


def _commit(cwd, message, author_email, committer_email):
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="Tester",
        GIT_AUTHOR_EMAIL=author_email,
        GIT_COMMITTER_NAME="Tester",
        GIT_COMMITTER_EMAIL=committer_email,
    )
    result = _run(["git", "commit", "--allow-empty", "-q", "-m", message], cwd, env)
    assert result.returncode == 0, "造提交失败: %s" % result.stderr
    return _git(cwd, "rev-parse", "HEAD").strip()


def _gate(repo, rev_range):
    env = dict(os.environ)
    env["GATE_REPO"] = repo.replace("\\", "/")   # Windows 反斜杠路径交给 git -C 不稳妥
    return _run(["bash", GATE, rev_range], repo, env)


def _repo():
    """建一个临时空仓库，返回 (临时目录上下文, 目录路径)。调用方负责关闭。"""
    return tempfile.TemporaryDirectory()


def check_pass_for_noreply():
    """范围内 author / committer 均 noreply → 绿灯。"""
    with _repo() as d:
        _init(d)
        base = _commit(d, "base", NOREPLY, NOREPLY)
        _commit(d, "good", NOREPLY, NOREPLY)
        result = _gate(d, "%s..HEAD" % base)
        assert result.returncode == 0, "noreply 提交应绿灯，实际 rc=%d\n%s%s" % (
            result.returncode, result.stdout, result.stderr)


def check_fail_for_real_author():
    """author 为真实邮箱 → 红灯，且点名该邮箱。"""
    with _repo() as d:
        _init(d)
        base = _commit(d, "base", NOREPLY, NOREPLY)
        _commit(d, "bad-author", REAL, NOREPLY)
        result = _gate(d, "%s..HEAD" % base)
        assert result.returncode != 0, "真实 author 邮箱应红灯\n%s%s" % (result.stdout, result.stderr)
        assert REAL in result.stdout, "失败信息应点名 %s\n%s" % (REAL, result.stdout)


def check_fail_for_real_committer():
    """committer 为真实邮箱 → 红灯（author 合规也救不了）。"""
    with _repo() as d:
        _init(d)
        base = _commit(d, "base", NOREPLY, NOREPLY)
        _commit(d, "bad-committer", NOREPLY, REAL)
        result = _gate(d, "%s..HEAD" % base)
        assert result.returncode != 0, "真实 committer 邮箱应红灯\n%s%s" % (result.stdout, result.stderr)
        assert REAL in result.stdout, "失败信息应点名 %s" % REAL


def check_web_merge_allowed():
    """noreply@github.com（网页合并）在允许集合内 → 绿灯。"""
    with _repo() as d:
        _init(d)
        base = _commit(d, "base", NOREPLY, NOREPLY)
        _commit(d, "web-merge", NOREPLY, WEB_MERGE)
        result = _gate(d, "%s..HEAD" % base)
        assert result.returncode == 0, "noreply@github.com 应绿灯，实际 rc=%d\n%s%s" % (
            result.returncode, result.stdout, result.stderr)


def check_case_variant_allowed():
    """大小写变体不得绕过（也不应误红）。"""
    with _repo() as d:
        _init(d)
        base = _commit(d, "base", NOREPLY, NOREPLY)
        _commit(d, "mixed-case", NOREPLY_MIXED, NOREPLY)
        result = _gate(d, "%s..HEAD" % base)
        assert result.returncode == 0, "大小写变体应绿灯，实际 rc=%d\n%s%s" % (
            result.returncode, result.stdout, result.stderr)


def check_old_history_not_reviewed():
    """旧历史里的真实邮箱（范围之外）不得把新 PR 误红。"""
    with _repo() as d:
        _init(d)
        base = _commit(d, "legacy-real-email", REAL, REAL)   # 范围外的旧债
        _commit(d, "good", NOREPLY, NOREPLY)
        # 只看 base..HEAD：旧的 REAL 提交被排除 → 绿灯
        result = _gate(d, "%s..HEAD" % base)
        assert result.returncode == 0, "范围外旧邮箱不应误红，实际 rc=%d\n%s%s" % (
            result.returncode, result.stdout, result.stderr)
        # 反向对照：同一仓库把旧债纳入范围（HEAD 全历史）→ 必须变红，
        # 证明「绿灯」来自范围裁剪，而不是门恒绿。
        result2 = _gate(d, "HEAD")
        assert result2.returncode != 0, "纳入范围后应红灯（证明门不是恒绿）\n%s" % result2.stdout


CHECKS = (
    check_pass_for_noreply,
    check_fail_for_real_author,
    check_fail_for_real_committer,
    check_web_merge_allowed,
    check_case_variant_allowed,
    check_old_history_not_reviewed,
)


def main():
    failures = 0
    for check in CHECKS:
        try:
            check()
        except (AssertionError, RuntimeError) as error:
            print("  断言失败: %s" % error)
            failures += 1
    if failures:
        print("%d/%d 项通过，%d 项失败" % (len(CHECKS) - failures, len(CHECKS), failures))
        return 1
    print("%d/%d 项通过" % (len(CHECKS), len(CHECKS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
