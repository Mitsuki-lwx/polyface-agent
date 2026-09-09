#!/usr/bin/env bash
# Polyface 本地一键启动（开发模式，git-bash / mac / linux 通用）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY=python3; [ -x "$ROOT/python-service/.venv/bin/python" ] && PY="$ROOT/python-service/.venv/bin/python"
[ -x "$ROOT/python-service/.venv/Scripts/python.exe" ] && PY="$ROOT/python-service/.venv/Scripts/python.exe"

echo "[1/2] Python LLM service -> :8000"
( cd "$ROOT/python-service" && "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 ) &
echo "[2/2] Java backend -> :8080"
( cd "$ROOT/java-backend" && mvn -q spring-boot:run -Dspring-boot.run.arguments=--server.port=8080 ) &

trap 'kill 0' EXIT
sleep 8
start http://127.0.0.1:8080/health 2>/dev/null || open http://127.0.0.1:8080/health 2>/dev/null || true
wait
