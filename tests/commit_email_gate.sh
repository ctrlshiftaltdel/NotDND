#!/usr/bin/env bash
#
# 提交邮箱门（CI「提交邮箱门」）—— 铁律 3：作者邮箱一律 GitHub noreply
#
# 对「进入 master 的新增提交」逐个校验 **author 与 committer 邮箱**，
# 命中允许集合之外的邮箱（例如真实 gmail）即非零退出，该 PR / master 推送变红。
#
# 允许集合：
#   *@users.noreply.github.com   GitHub 用户 / Bot 的 noreply（本地 git 身份应配成这个）
#   noreply@github.com           GitHub 网页 / API 合并提交的 committer
#
# 只校验**新增提交范围**，**不回看旧历史**——历史里既有的真实邮箱属于旧债
# （由「历史清理」另议），不得因此把后来的正常 PR 误红。
#
# 范围推断优先级（前者优先）：
#   1. 命令行参数 $1（显式 range，如 `origin/master..HEAD`）
#   2. 环境变量 GATE_RANGE
#   3. 推送事件：BEFORE_SHA..AFTER_SHA；before 为全 0（新分支首推）时只看 AFTER_SHA 这一个提交
#   4. PR 事件：BASE_SHA..HEAD_SHA（PR 自己的提交，不含 CI 的临时合并提交）
#   5. 本地回退：origin/master..HEAD
# 推断不出范围时 **fail-closed**（非零退出），不静默放行。
#
# 零依赖：bash + git。
#
# 仓库目录由 GATE_REPO 指定（默认本仓库根；供回归测试指向临时仓库）。
#
# 用法：
#   bash tests/commit_email_gate.sh                    # CI / 本地自动推断
#   bash tests/commit_email_gate.sh origin/master..HEAD
set -u

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${GATE_REPO:-$REPO}"
ZERO_SHA="0000000000000000000000000000000000000000"

git_in() { git -C "$REPO" "$@"; }

# 该提交在本仓库中可解析吗？（全 0 SHA、未拉取的 SHA 都会返回非零）
have_commit() { git_in rev-parse --verify --quiet "${1}^{commit}" >/dev/null 2>&1; }

# --- 1) 推断提交范围 ------------------------------------------------------
range=""
if [ "$#" -ge 1 ] && [ -n "${1:-}" ]; then
    range="$1"
elif [ -n "${GATE_RANGE:-}" ]; then
    range="$GATE_RANGE"
fi

if [ -z "$range" ]; then
    if [ -n "${BEFORE_SHA:-}" ] && [ -n "${AFTER_SHA:-}" ] && have_commit "$AFTER_SHA"; then
        if [ "$BEFORE_SHA" != "$ZERO_SHA" ] && have_commit "$BEFORE_SHA"; then
            range="${BEFORE_SHA}..${AFTER_SHA}"     # 推送：只验本次新推的提交
        else
            range="${AFTER_SHA}^!"                  # 新分支首推（before 全 0）：只看这一个提交
        fi
    elif [ -n "${BASE_SHA:-}" ] && [ -n "${HEAD_SHA:-}" ] \
        && have_commit "$BASE_SHA" && have_commit "$HEAD_SHA"; then
        range="${BASE_SHA}..${HEAD_SHA}"            # PR：只验 PR 自己的提交，不含 CI 临时合并提交
    elif have_commit "origin/master" && have_commit "HEAD"; then
        range="origin/master..HEAD"                 # 本地 / 兜底
    fi
fi

if [ -z "$range" ]; then
    echo "提交邮箱门：无法推断提交范围（fail-closed）。" >&2
    echo "  请在 CI 中提供事件 SHA 环境变量，或显式传入 range，例如：" >&2
    echo "    bash tests/commit_email_gate.sh origin/master..HEAD" >&2
    exit 2
fi

# --- 2) 展开范围内的提交 --------------------------------------------------
if ! log_out="$(git_in log --no-color --format='%H%x09%ae%x09%ce' "$range")"; then
    echo "提交邮箱门：无法解析提交范围 '$range'（fail-closed，git 报错见上）。" >&2
    exit 2
fi

# --- 3) 逐个校验 author / committer 邮箱 ---------------------------------
is_allowed() {
    local email="${1,,}"   # 统一小写，避免 User@Users.Noreply.GitHub.com 之类变体绕过
    case "$email" in
        *@users.noreply.github.com) return 0 ;;
        noreply@github.com) return 0 ;;
        *) return 1 ;;
    esac
}

hits=0
checked=0
note_hit() {
    printf '  ✗ [%s] %s\n' "$1" "$2"
    hits=$((hits + 1))
}

echo "提交邮箱门：范围 '$range'（仓库 ${REPO}）"
while IFS=$'\t' read -r sha ae ce; do
    [ -z "${sha:-}" ] && continue
    checked=$((checked + 1))
    short="${sha:0:12}"
    is_allowed "$ae" || note_hit "author" "$short  $ae"
    is_allowed "$ce" || note_hit "committer" "$short  $ce"
done <<< "$log_out"

# --- 4) 汇总 --------------------------------------------------------------
if [ "$checked" -eq 0 ]; then
    echo "提交邮箱门：范围内 0 个提交，通过。"
    exit 0
fi
if [ "$hits" -eq 0 ]; then
    echo "提交邮箱门：通过（$checked 个提交，author / committer 邮箱均合规）"
    exit 0
fi
echo "提交邮箱门：失败（$checked 个提交中，$hits 处邮箱不合规）"
echo "  允许：*@users.noreply.github.com、noreply@github.com"
echo "  修复：git config user.email '<ID>+<username>@users.noreply.github.com'，再 amend / rebase 相关提交后强推分支。"
exit 1
