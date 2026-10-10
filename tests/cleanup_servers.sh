#!/usr/bin/env bash
# 清理**测试遗留**的 notdnd_web.py 服务进程。
#
# ══════════════════════════════════════════════════════════════════════
#  ⚠️ 这份脚本只清理「能当场证明是测试遗留」的进程，绝不搞一刀切。
#
#  最省事的写法是「把所有 notdnd_web.py 都杀掉」。它错，而且出过事故：
#  同一台机器上往往同时跑着别的东西——开发者手边开着的一个长期服务、
#  部署平台按自启动配置托管的正式实例、甚至只是上一次没关干净又起来的
#  一个实例。它们和测试遗留**长得一模一样**（同一个解释器、同一个入口
#  脚本、同一个进程名），区别在于：每一个都在**正常提供服务**。
#  一旦被顺手清掉，外部看到的就是「服务突然连不上」，而最先被怀疑的
#  （测试脚本）恰恰是无辜的，排查要绕很大一圈。
#
#  **教训：「不是我手动起的」不等于「是垃圾」。** 清理脚本必须先证明一个
#  进程属于测试；证不出来就不动它。宁可留一个残留（顶多下次起服务换个
#  端口），也绝不误伤正在服务的进程。
# ══════════════════════════════════════════════════════════════════════
#  判据：测试服务要**同时**满足两个特征（测试自己起服务时就是这么配的）
#
#    ① 存档目录落在临时目录里
#       测试一律把 NOTDND_SAVE 指到临时目录下的独立目录，
#       绝不写进正式存档目录（否则会给玩家真实存档塞满测试数据）。
#    ② 监听端口不是「服务端口」
#       服务端口默认 8600；由环境变量 NOTDND_PROTECTED_PORTS 覆盖
#       （空格分隔可列多个）。测试用的一律是随机高位端口。
#
#  只有①②**都**成立才算测试遗留。任一读不到就按「不是测试」处理、
#  也就是**不清理**——这个保守方向是刻意的。
# ══════════════════════════════════════════════════════════════════════
#
# 用法：
#   bash tests/cleanup_servers.sh          # 清理测试遗留（不动正式服务）
#   bash tests/cleanup_servers.sh -n       # dry-run，只看不动手
#   bash tests/cleanup_servers.sh --all    # 连正式服务一起列出（仅展示）
set -u

DRY=0
SHOW_ALL=0
for a in "$@"; do
  case "$a" in
    -n) DRY=1 ;;
    --all) SHOW_ALL=1 ;;
  esac
done

# 服务端口（不清理的目标）。默认就是入口脚本里的默认端口 8600。
PROTECTED_PORTS="${NOTDND_PROTECTED_PORTS:-8600}"

# 收集所有「解释器是 python，且命令行里有一项恰是 notdnd_web.py」的进程。
#
# ⚠️ 不能用 `pgrep -f notdnd_web.py` / `ps | grep notdnd_web.py`：
#    那样会匹配到**调用它的这个 shell 自己**（shell 的 cmdline 里就含这个
#    字样），于是每次都误报「发现 1 个残留」，而那个 PID 转眼就消失。
#    正确做法：读 /proc，要求 exe 解到 python，且 cmdline 里**独立一项**
#    正是 notdnd_web.py。
collect() {
  for d in /proc/[0-9]*; do
    case "$(readlink -f "$d/exe" 2>/dev/null)" in
      *python3*|*python3.*) ;;
      *) continue ;;
    esac
    if tr '\0' '\n' < "$d/cmdline" 2>/dev/null | grep -qx 'notdnd_web.py'; then
      echo "${d#/proc/}"
    fi
  done | sort -u
}

# 一个进程监听的端口（取第一个）；读不到就为空。
listen_port() {
  ss -lntp 2>/dev/null | grep "pid=$1," | grep -o ':[0-9]* ' | head -1 | tr -d ': '
}

# 它的存档目录：服务是子进程，env 里有 NOTDND_SAVE。
save_dir_of() {
  tr '\0' '\n' < "/proc/$1/environ" 2>/dev/null \
    | grep '^NOTDND_SAVE=' | cut -d= -f2-
}

# 返回 0 = 是测试服务（可清理）；1 = 正式服务或无法判定（不动）。
is_test_service() {
  local p=$1 port save_dir

  save_dir=$(save_dir_of "$p")
  port=$(listen_port "$p")

  # 条件①：存档目录在临时目录下（/tmp 或 $TMPDIR 下的路径均可）。
  local tmp_save=1
  case "${save_dir:-}" in
    /tmp/*) tmp_save=0 ;;
    *)
      if [ -n "${TMPDIR:-}" ]; then
        case "$save_dir" in "$TMPDIR"/*) tmp_save=0 ;; esac
      fi
      ;;
  esac

  # 条件②：端口是个**有效数字**，且不在服务端口清单里。
  local test_port=1
  case "${port:-}" in
    ''|*[!0-9]*) test_port=1 ;;
    *)
      test_port=0
      for protected in $PROTECTED_PORTS; do
        [ "$port" = "$protected" ] && test_port=1
      done
      ;;
  esac

  # ①②都成立才算测试服务。
  # 特别提示：读不到 NOTDND_SAVE（正式实例通常没有这个变量）→ tmp_save 保持
  # 1 → 判为非测试 → 不杀。这个保守方向是刻意的。
  [ "$tmp_save" = "0" ] && [ "$test_port" = "0" ]
}

PIDS=$(collect)

if [ -z "$PIDS" ]; then
  echo "✓ 没有 notdnd_web.py 进程在跑"
  exit 0
fi

TEST_PIDS=""
KEEP_PIDS=""
for p in $PIDS; do
  if is_test_service "$p"; then
    TEST_PIDS="$TEST_PIDS $p"
  else
    KEEP_PIDS="$KEEP_PIDS $p"
  fi
done

# 始终把「不动」的进程列出来，让你一眼看到「我没碰它」。
if [ -n "$KEEP_PIDS" ] && { [ "$SHOW_ALL" = "1" ] || [ -n "$TEST_PIDS" ]; }; then
  echo "○ 非测试进程（**不会清理**）："
  for p in $KEEP_PIDS; do
    echo "    PID=$p  端口=$(listen_port "$p")  存档=$(save_dir_of "$p" || echo '默认')"
  done
fi

if [ -z "$TEST_PIDS" ]; then
  echo "✓ 没有测试遗留进程需要清理"
  exit 0
fi

echo "◇ 测试遗留进程（将清理）："
for p in $TEST_PIDS; do
  echo "    PID=$p  端口=$(listen_port "$p")  存档=$(save_dir_of "$p")"
done

if [ "$DRY" = "1" ]; then
  echo
  echo "（dry-run，未做任何改动。去掉 -n 执行清理。）"
  exit 0
fi

echo
for p in $TEST_PIDS; do
  kill -- "-$p" 2>/dev/null || kill "$p" 2>/dev/null
done
sleep 0.5
for p in $TEST_PIDS; do
  kill -0 "$p" 2>/dev/null && kill -9 "$p" 2>/dev/null
done
sleep 0.3

# 复查：只看测试进程，非测试进程不参与判定。
left=0
for p in $(collect); do
  is_test_service "$p" && left=$((left + 1))
done
if [ "$left" = "0" ]; then
  echo "✓ 测试遗留已清除"
else
  echo "⚠️ 仍有 $left 个测试进程存活"
fi

# ⚠️ 退出码恒为 0。调用方常写 `cleanup_servers.sh && <下一步>`，
#    一旦返回非 0，`&&` 链会断掉、下一步**静默不执行**。
exit 0
