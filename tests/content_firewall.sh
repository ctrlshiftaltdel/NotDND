#!/usr/bin/env bash
#
# 内容防火墙（CI「内容防火墙门」）
#
# 对**已跟踪文件**扫描四类高风险内容，命中即非零退出：
#   1. 禁止路径：reference/、corpus/，以及第三方数据的翻译 / 改名 / 微调派生文件名；
#   2. 高风险第三方品牌标识：清单见 tests/content_firewall_terms.txt（可编辑）；
#   3. 密钥：sk- / ghp_ / github_pat_ / AKIA / hf_ / AIza / PRIVATE KEY；
#   4. 隐私：真实用户主目录路径、C:\Users\、内网 IP、真实邮箱（排除 GitHub noreply）。
#
# 设计要点：扫描逻辑与清单分开放置；脚本自身、清单文件、.gitignore 不参与扫描，
# 避免「扫描规则命中自己」。禁止路径按**路径**判断而非正文，
# 以免 AGENTS.md 中作为反面示例的 `reference/` / `corpus/` 字样被误判。
#
# 用法：bash tests/content_firewall.sh
set -u

cd "$(dirname "$0")/.." || exit 2

TERMS_FILE="tests/content_firewall_terms.txt"
SELF="tests/content_firewall.sh"

hits=0
note_hit() {
    printf '  ✗ [%s] %s\n' "$1" "$2"
    hits=$((hits + 1))
}

mapfile -t all_files < <(git ls-files)
if [ "${#all_files[@]}" -eq 0 ]; then
    echo "内容防火墙：没有已跟踪文件，跳过。"
    exit 0
fi

scan_files=()
for f in "${all_files[@]}"; do
    case "$f" in
        "$SELF" | "$TERMS_FILE" | ".gitignore") continue ;;
    esac
    scan_files+=("$f")
done

echo "内容防火墙：扫描 ${#scan_files[@]} 个已跟踪文件（已排除防火墙自身 / 清单 / .gitignore）"

# 对选定文件跑一次 git grep，逐行记为命中。
# 用法：grep_hits <类别> <git-grep 参数...>
grep_hits() {
    local category="$1"
    shift
    local out
    if out="$(git grep -n -i "$@" -- "${scan_files[@]}" 2>/dev/null)"; then
        while IFS= read -r line; do
            [ -n "$line" ] && note_hit "$category" "$line"
        done <<< "$out"
    fi
}

# --- 1) 禁止路径 ---------------------------------------------------------
echo "· 禁止路径"
for f in "${all_files[@]}"; do
    case "/$f" in
        */reference/* | */corpus/*)
            note_hit "禁止路径" "$f（位于第三方参考目录）"
            continue
            ;;
    esac
    base="${f##*/}"
    case "$base" in
        *翻译* | *译文* | *译本* | *改名* | *微调* | *派生* | *衍生* | *山寨* | *复刻* | \
            *translated* | *renamed* | *derivative* | *finetun*)
            note_hit "禁止路径" "$f（疑似第三方数据派生文件名）"
            ;;
    esac
done

# --- 2) 高风险第三方品牌标识 --------------------------------------------
echo "· 品牌标识（清单：$TERMS_FILE）"
if [ ! -f "$TERMS_FILE" ]; then
    note_hit "品牌标识" "缺少清单文件 $TERMS_FILE"
else
    while IFS= read -r raw || [ -n "$raw" ]; do
        term="${raw%%#*}"
        term="$(printf '%s' "$term" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
        [ -z "$term" ] && continue
        grep_hits "品牌标识" -F -e "$term"
    done < "$TERMS_FILE"
fi

# --- 3) 密钥 -------------------------------------------------------------
echo "· 密钥"
grep_hits "密钥" -E -e 'sk-[A-Za-z0-9_-]{16,}'
grep_hits "密钥" -E -e 'ghp_[A-Za-z0-9]{36}'
grep_hits "密钥" -E -e 'github_pat_[A-Za-z0-9_]{20,}'
grep_hits "密钥" -E -e 'AKIA[0-9A-Z]{16}'
grep_hits "密钥" -E -e 'hf_[A-Za-z0-9]{20,}'
grep_hits "密钥" -E -e 'AIza[0-9A-Za-z_-]{35}'
grep_hits "密钥" -E -e 'BEGIN [A-Z ]*PRIVATE KEY'

# --- 4) 隐私 -------------------------------------------------------------
echo "· 隐私"
# 真实用户主目录（AGENTS.md 中的 /home/<用户名> 占位符不会命中：< 不在字符类内）
grep_hits "隐私" -E -e '/home/[A-Za-z0-9._-]+'
grep_hits "隐私" -E -e '[Cc]:\\Users\\'
# 内网 IP：要求完整四段，避免误伤文档里的 10.1 / 10.5 等小节编号
grep_hits "隐私" -E -e '(^|[^0-9])10\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)'
grep_hits "隐私" -E -e '(^|[^0-9])192\.168\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)'
grep_hits "隐私" -E -e '(^|[^0-9])172\.(1[6-9]|2[0-9]|3[01])\.[0-9]{1,3}\.[0-9]{1,3}([^0-9]|$)'

# 真实邮箱（排除 GitHub 的 *.users.noreply.github.com）
email_re='[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}'
if out="$(git grep -n -iE "$email_re" -- "${scan_files[@]}" 2>/dev/null)"; then
    while IFS= read -r line; do
        [ -z "$line" ] && continue
        emails="$(grep -oE "$email_re" <<< "$line")"
        bad=0
        while IFS= read -r mail; do
            [ -z "$mail" ] && continue
            case "$mail" in
                *@users.noreply.github.com) ;;
                *) bad=1 ;;
            esac
        done <<< "$emails"
        [ "$bad" -eq 1 ] && note_hit "隐私" "$line"
    done <<< "$out"
fi

# --- 汇总 ---------------------------------------------------------------
echo
if [ "$hits" -eq 0 ]; then
    echo "内容防火墙：通过（0 命中）"
    exit 0
fi
echo "内容防火墙：失败（$hits 命中）"
exit 1
