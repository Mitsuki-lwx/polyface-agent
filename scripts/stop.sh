#!/usr/bin/env bash
# ============================================================
# Polyface 停止（macOS / Linux / Git Bash —— Windows 也可用 stop.bat）
#
# 按端口（8080 / 8000）精确结束本应用进程。
# **不误杀**：先查进程名，只结束 java / python 系；其它程序只提示、不动手。
# 结束前需确认（docs/25 B2/B3）—— 免得误杀你自己在跑的脚本。
# ============================================================
set -euo pipefail

# 与 start.sh 保持同一组端口覆盖变量 —— 否则用非默认端口启动的服务停不掉。
PORTS=("${POLYFACE_JAVA_PORT:-8080}" "${POLYFACE_PY_PORT:-8000}")
LABELS=("Java 后端" "Python LLM 服务")
SAFE_PROCS="java javaw python python3 pythonw"

is_windows() {
  case "$(uname -s 2>/dev/null || echo unknown)" in
    MINGW*|MSYS*|CYGWIN*) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------- 端口 -> PID
# 与 scripts/doctor.py::port_pid() 保持同一套探测顺序，避免两处实现不一致
port_pid() {
  local port="$1"
  if is_windows; then
    netstat -ano -p tcp 2>/dev/null \
      | grep -i 'LISTENING' \
      | grep -E ":${port}[[:space:]]" \
      | awk '{print $NF}' | head -1
    return 0
  fi
  # Linux：ss 优先（iproute2 自带）；否则退回 lsof
  if command -v ss >/dev/null 2>&1; then
    ss -lptnH "sport = :${port}" 2>/dev/null \
      | grep -oE 'pid=[0-9]+' | head -1 | cut -d= -f2
    return 0
  fi
  lsof -ti "tcp:${port}" -s TCP:LISTEN 2>/dev/null | head -1
}

# PID -> 进程名
proc_name() {
  local pid="$1"
  if is_windows; then
    # CSV 形如 "java.exe","38088","Console",... 取第一个字段
    tasklist /FI "PID eq ${pid}" /FO CSV /NH 2>/dev/null \
      | head -1 | cut -d, -f1 | tr -d '"'
    return 0
  fi
  ps -p "$pid" -o comm= 2>/dev/null | head -1
}

kill_pid() {
  local pid="$1"
  if is_windows; then
    MSYS_NO_PATHCONV=1 taskkill /PID "$pid" /T /F >/dev/null 2>&1
  else
    kill -TERM "$pid" >/dev/null 2>&1 || kill -KILL "$pid" >/dev/null 2>&1
  fi
}

# ---------------------------------------------------------- 确认
echo "即将检查端口 ${PORTS[0]}（Java 后端）与 ${PORTS[1]}（Python LLM 服务）。"
echo "只会结束占用这两个端口的 java / python 进程；其它程序一律跳过。"
echo
printf '确认继续？[y/N] '
if ! read -r -t 60 ans; then
  echo
  echo "（60 秒无输入）已取消，未结束任何进程。"
  exit 0
fi
case "$ans" in
  [yY]*) ;;
  *) echo "已取消，未结束任何进程。"; exit 0 ;;
esac
echo

echo "正在查找 Polyface 进程..."
echo
FOUND=0

for i in "${!PORTS[@]}"; do
  port="${PORTS[$i]}"
  label="${LABELS[$i]}"
  pid="$(port_pid "$port" || true)"

  if [ -z "$pid" ]; then
    echo "[空闲] $label（端口 $port）"
    continue
  fi

  img="$(proc_name "$pid" || true)"
  if [ -z "$img" ]; then
    echo "[跳过] $label（端口 $port）被 PID $pid 占用，但查不到进程名 —— 不动手"
    continue
  fi

  # 只结束本应用可能用到的解释器，避免误杀
  safe=0
  for p in $SAFE_PROCS; do
    case "$img" in "$p"|"$p.exe") safe=1 ;; esac
  done
  if [ "$safe" -eq 0 ]; then
    echo "[跳过] 端口 $port 被 $img（PID $pid）占用 —— 不是本应用的进程，未结束。"
    echo "        若确认要结束，请手动执行：kill -TERM $pid（Windows：taskkill /PID $pid /F）"
    continue
  fi

  echo "[停止] $label（$img PID $pid，端口 $port）"
  if kill_pid "$pid"; then
    FOUND=1
  else
    echo "        结束失败，可能需要管理员权限。"
  fi
done

# ---------------------------------------------------------- 复查
echo
if [ "$FOUND" -eq 0 ]; then
  echo "没有发现正在运行的 Polyface 服务（端口 8080 / 8000 均空闲）。"
else
  echo "已停止。正在复查端口..."
  sleep 1
  for i in "${!PORTS[@]}"; do
    port="${PORTS[$i]}"
    label="${LABELS[$i]}"
    pid="$(port_pid "$port" || true)"
    if [ -n "$pid" ]; then
      echo "[警告] $label（端口 $port）仍被 PID $pid 占用 —— 可能未完全退出或需要管理员权限"
    else
      echo "[已释放] $label（端口 $port）"
    fi
  done
fi
