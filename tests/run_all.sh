#!/usr/bin/env bash
#
# 一键回归（CI「回归门」）
#
# 串行执行 tests/ 下的各测试脚本（tests/test_*.py），逐个打印「通过 / 失败」行，
# 汇总后：全过返回 0，有失败返回非零。
#
# 零依赖：只用 Python 3 标准库。当前尚无后端，不得因缺少后端文件而误红——
# 只要 tests/ 下暂时没有脚本，本脚本同样返回 0。
#
# 用法：bash tests/run_all.sh
set -u

cd "$(dirname "$0")/.." || exit 2

mapfile -t scripts < <(find tests -maxdepth 1 -type f -name 'test_*.py' | sort)

total=0
passed=0
failed=0

echo "回归测试：发现 ${#scripts[@]} 个测试脚本"
for script in "${scripts[@]}"; do
    total=$((total + 1))
    echo "── $script"
    if python3 "$script"; then
        echo "通过: $script"
        passed=$((passed + 1))
    else
        echo "失败: $script"
        failed=$((failed + 1))
    fi
done

echo
echo "汇总: 共 $total 个，通过 $passed 个，失败 $failed 个"
if [ "$failed" -ne 0 ]; then
    exit 1
fi
exit 0
