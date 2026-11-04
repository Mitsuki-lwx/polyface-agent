#!/usr/bin/env bash
# ============================================================
# Polyface 启动（macOS / Linux / Git Bash）
#
# 与 start.bat 逐项对齐（见 docs/56 §3.2）：
#   - Java 走 `java -jar`，**运行时不需要 Maven**
#   - 启动前体检（scripts/doctor.py）：缺依赖/端口占用 -> 明确提示并中止
#   - 浏览器在**两个服务都就绪后**才打开（轮询，不是固定等待）
#   - 数据目录显式指定为 <root>/data，避免 java -jar 的默认 `../data`
#     把用户数据写到包外面
#
# Ctrl-C 会一并结束两个服务。
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# POSIX 路径 -> 原生程序（python.exe / java）能理解的路径。
#
# 为什么必须显式转：Git Bash(MSYS) 默认会自动翻译「看起来像 POSIX 路径」的
# 参数与环境变量，但用户可以关掉它（MSYS_NO_PATHCONV=1 /
# MSYS2_ARG_CONV_EXCL=* —— 为了 docker/kubectl 全局设置的人不少）。
# 一旦关掉，下面 POLYFACE_DATA_DIR 会以 `/d/.../data` 交给 Java，Java 会把它
# 当成「当前盘根下的 d/...」，于是数据**静默写到包外** —— 这正是本脚本要防的事。
# Linux/macOS 上没有 cygpath，原样返回。
native_path() {
  if [ -n "${MSYSTEM:-}${CYGWIN:-}" ] && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

# ---------------------------------------------------------- 找 Python
venv_python() {
  for p in "$ROOT/python-service/.venv/bin/python" \
           "$ROOT/python-service/.venv/Scripts/python.exe"; do
    [ -x "$p" ] && { printf '%s' "$p"; return 0; }
  done
  return 1
}

if PY="$(venv_python)"; then
  :
elif command -v python3 >/dev/null 2>&1; then
  PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PY="$(command -v python)"
else
  echo "[阻塞] 未找到 Python，无法启动。"
  echo "        => 需 Python 3.11+：https://www.python.org/downloads/"
  echo "           或先运行 scripts/setup.sh"
  exit 1
fi

# ---------------------------------------------------------- 端口
# 为什么可配：本机 8000 常被第三方程序（如某些游戏平台）占用，8080 也常被别的
# 开发服务占用。端口写死时用户唯一的出路是改脚本 —— 那是把配置问题变成代码问题。
# 覆盖方式（两个都要给，Java 要按 Java 的端口回调 Python）：
#   POLYFACE_PY_PORT=18000 POLYFACE_JAVA_PORT=18080 bash scripts/start.sh
PY_PORT="${POLYFACE_PY_PORT:-8000}"
JAVA_PORT="${POLYFACE_JAVA_PORT:-8080}"

# ---------------------------------------------------------- 体检
# 把端口透给 doctor.py，否则它检查的是默认端口，与下面实际启动的端口不一致。
if ! "$PY" "$(native_path "$ROOT/scripts/doctor.py")" --quiet \
     --java-port "$JAVA_PORT" --py-port "$PY_PORT"; then
  echo
  echo "存在阻塞项，已中止启动。请按上方提示处理后重试。"
  # 只有"环境还没建"时才提 setup —— 端口被占之类的问题跑 setup 也解决不了 ✗
  if [ ! -x "$PY" ] || { [ ! -f "$ROOT/polyface.jar" ] && [ ! -f "$ROOT/java-backend/target/polyface.jar" ]; }; then
    echo "若这是第一次运行，请先执行 scripts/setup.sh"
  fi
  exit 1
fi

# ---------------------------------------------------------- 找 jar
JAR_DIR="$ROOT"
[ -f "$ROOT/polyface.jar" ] || JAR_DIR="$ROOT/java-backend/target"
if [ ! -f "$JAR_DIR/polyface.jar" ]; then
  echo "[阻塞] 未找到 polyface.jar。请先运行 scripts/setup.sh"
  exit 1
fi

# ---------------------------------------------------------- 就绪探测
# 注意：Java 的 /health 在 Python 不可达时**仍返回 200**（内部吞掉异常），
# 所以必须分别探测两个端口，不能只看 Java。
# 等端口就绪。第三个参数是**我们自己的子进程 pid** ——
# 只看端口是错的：别人（如 grant-dev.exe）占着 8080 时，旧版会立刻报
# "已就绪（0s）"，紧接着自己那个却绑不上端口退出了 ✗（实测 2026-11-01）。
# 所以先确认"我们的进程还活着"，死了就立刻返回 2，不装作就绪。
wait_port() {
  local port="$1" label="$2" pid="$3" limit=120 waited=0
  while [ "$waited" -lt "$limit" ]; do
    if [ -n "$pid" ] && ! kill -0 "$pid" 2>/dev/null; then
      return 2
    fi
    if "$PY" -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',$port))==0 else 1)" 2>/dev/null; then
      echo "  $label :$port 已就绪（${waited}s）"
      return 0
    fi
    waited=$((waited + 1))
    sleep 1
  done
  return 1
}

# ---------------------------------------------------------- 启动
PIDS=()
PIDS_NAME=()   # 与 PIDS 同序：哪个进程是哪个服务（openspec: service-crash-forensics）
cleanup() {
  echo
  echo "正在停止服务..."
  for pid in ${PIDS[@]+"${PIDS[@]}"}; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  wait 2>/dev/null || true
  echo "已停止。"
}
trap cleanup EXIT INT TERM

echo
echo "正在启动服务..."
echo "  端口：Python $PY_PORT / Java $JAVA_PORT"
echo "  日志：$LOG_DIR/python-service.log"
echo "        $LOG_DIR/java-backend.log"
echo

# 数据目录：显式指定，保证数据落在包内（Spring Boot 松绑定：POLYFACE_DATA_DIR）
# 用 native_path：环境变量的值**不一定**被 MSYS 自动翻译（见文件头说明），
# 而这个值是交给 Java（原生程序）用的，必须是 Windows 形式。
export POLYFACE_DATA_DIR="$(native_path "$ROOT/data")"

# Python LLM 服务 —— 必须在 python-service 目录下启动（.env 是按当前目录找的）
(
  cd "$ROOT/python-service"
  exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port "$PY_PORT"
) >"$LOG_DIR/python-service.log" 2>&1 &
PIDS+=($!); PIDS_NAME+=("Python LLM 服务")

# Langfuse：`.env` 是 **Python 侧**的配置，Java 读的是**环境变量**（application.yml 里
# `langfuse.enabled: ${LANGFUSE_ENABLED:false}`）。不在这里导出的话，
# `.env` 里配了也白配 —— Java 永远不建根 trace，Python 的子 span 就没有父节点，
# 一个请求会在 Langfuse 里裂成好几条互不相干的 trace ✗（实测 2026-10-30）。
# 只导这四项、且**已被外部显式设置时以外部为准**（不覆盖用户的选择）。
if [ -f "$ROOT/python-service/.env" ]; then
  for _k in LANGFUSE_ENABLED LANGFUSE_HOST LANGFUSE_PUBLIC_KEY LANGFUSE_SECRET_KEY; do
    if [ -z "$(printenv "$_k" 2>/dev/null)" ]; then
      _v="$(sed -n "s/^[[:space:]]*${_k}[[:space:]]*=[[:space:]]*//p" "$ROOT/python-service/.env" | tail -1 | tr -d '\r"')"
      [ -n "$_v" ] && export "$_k=$_v"
    fi
  done
  unset _k _v
fi

# Java 后端 —— java -jar，cwd 设为 jar 所在目录
# 必须显式传两个端口：Java 的 server.port 决定 Web 端口，
# polyface.llm.base-url 决定它去哪里找 Python（不传就还是默认 8000）。
(
  cd "$JAR_DIR"
  exec java -jar polyface.jar \
    "--server.port=$JAVA_PORT" \
    "--polyface.llm.base-url=http://127.0.0.1:$PY_PORT"
) >"$LOG_DIR/java-backend.log" 2>&1 &
PIDS+=($!); PIDS_NAME+=("Java 后端")

# ---------------------------------------------------------- 等就绪
echo "等待服务就绪（最多 120 秒 / 个）..."
rc=0
wait_port "$PY_PORT" "Python LLM 服务" "${PIDS[0]}" || rc=$?
if [ "$rc" -ne 0 ]; then
  echo
  if [ "$rc" -eq 2 ]; then
    echo "[警告] Python LLM 服务**没能启动**（进程已退出）。"
  else
    echo "[警告] Python 服务 120 秒内未就绪。"
  fi
  echo "       日志：$LOG_DIR/python-service.log"
  echo "       常见原因：端口 $PY_PORT 被别的程序占用（换端口：POLYFACE_PY_PORT=<端口>）。"
  exit 1
fi

rc=0
wait_port "$JAVA_PORT" "Java 后端" "${PIDS[1]}" || rc=$?
if [ "$rc" -ne 0 ]; then
  echo
  if [ "$rc" -eq 2 ]; then
    echo "[警告] Java 后端**没能启动**（进程已退出）。"
  else
    echo "[警告] Java 后端 120 秒内未就绪。"
  fi
  echo "       日志：$LOG_DIR/java-backend.log"
  echo "       最常见原因：端口 $JAVA_PORT 已被别的程序占用 —— 先查是谁："
  echo "         powershell -c \"Get-NetTCPConnection -LocalPort $JAVA_PORT -State Listen | Select-Object OwningProcess\""
  echo "       换端口启动：POLYFACE_JAVA_PORT=18080 POLYFACE_PY_PORT=18000 bash scripts/start.sh"
  exit 1
fi

echo
echo "服务已就绪，打开浏览器..."
URL="http://127.0.0.1:$JAVA_PORT"
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "$URL" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
  open "$URL" >/dev/null 2>&1 || true
elif command -v start >/dev/null 2>&1; then
  start "$URL" >/dev/null 2>&1 || true
else
  echo "（未能自动打开浏览器，请手动访问 $URL）"
fi

echo
echo "============================================================"
echo " Polyface 运行中：$URL"
echo " 按 Ctrl-C 停止两个服务。"
echo "============================================================"

# ---------------------------------------------------------- 监管子进程
# 以前这里是一句裸 `wait` —— 它等的是**全部**子进程，所以"Python 死了、Java 还活着"
# 这种半死状态，脚本会一直安静地等下去 ✗（openspec: service-crash-forensics）。
# 现在：任一退出 → 立刻指名道姓报出来（含退出码与日志路径）→ 收掉其余 → 非零退出。
# **故意不自动重启**：崩溃要被看见，不能被重启掩盖。
LOG_FOR=("$LOG_DIR/python-service.log" "$LOG_DIR/java-backend.log")

have_wait_n=0
if (( BASH_VERSINFO[0] > 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] >= 3) )); then
  have_wait_n=1
fi

if (( have_wait_n )); then
  # `wait -n` 在子进程异常退出时返回非零 —— 本脚本开着 `set -e`，必须吞掉它，
  # 否则会**直接跳出脚本**、连"是哪个退了"都来不及打（实测 2026-11-01）。
  wait -n "${PIDS[@]}" 2>/dev/null || true
else
  # 老 bash 没有 `wait -n`：轮询 + 尽力拿退出码
  echo "（本 bash 不支持 wait -n，改用轮询监管）"
  while :; do
    for _pid in "${PIDS[@]}"; do
      kill -0 "$_pid" 2>/dev/null || break 2
    done
    sleep 2
  done
fi

rc=0
for _i in "${!PIDS[@]}"; do
  if ! kill -0 "${PIDS[$_i]}" 2>/dev/null; then
    rc=0
    wait "${PIDS[$_i]}" 2>/dev/null || rc=$?   # 同样是 set -e：非零必须显式接住
    echo
    echo "============================================================"
    echo " [已退出] ${PIDS_NAME[$_i]}（退出码 $rc）"
    echo " 日志：${LOG_FOR[$_i]}"
    echo " 其余服务将被停止。请查看日志定位，再用 start.sh 显式重启。"
    echo "============================================================"
    break
  fi
done
exit "${rc:-1}"
