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
  echo "首次使用请先运行 scripts/setup.sh"
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
wait_port() {
  local port="$1" label="$2" limit=120 waited=0
  while [ "$waited" -lt "$limit" ]; do
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
PIDS+=($!)

# Java 后端 —— java -jar，cwd 设为 jar 所在目录
# 必须显式传两个端口：Java 的 server.port 决定 Web 端口，
# polyface.llm.base-url 决定它去哪里找 Python（不传就还是默认 8000）。
(
  cd "$JAR_DIR"
  exec java -jar polyface.jar \
    "--server.port=$JAVA_PORT" \
    "--polyface.llm.base-url=http://127.0.0.1:$PY_PORT"
) >"$LOG_DIR/java-backend.log" 2>&1 &
PIDS+=($!)

# ---------------------------------------------------------- 等就绪
echo "等待服务就绪（最多 120 秒 / 个）..."
if ! wait_port "$PY_PORT" "Python LLM 服务"; then
  echo
  echo "[警告] Python 服务 120 秒内未就绪。请查看 $LOG_DIR/python-service.log"
  exit 1
fi
if ! wait_port "$JAVA_PORT" "Java 后端"; then
  echo
  echo "[警告] Java 后端 120 秒内未就绪。请查看 $LOG_DIR/java-backend.log"
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
wait
