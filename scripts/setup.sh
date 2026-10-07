#!/usr/bin/env bash
# ============================================================
# Polyface 环境准备（macOS / Linux —— Windows 请用 setup.bat）
#
# 首次使用跑一次。做四件事：建 venv → 装 Python 依赖 → 生成 .env → 确认 jar
#
# 设计（与 setup.bat 逐项对齐，见 docs/56 §3.1）：
#   - 无任何机器相关硬编码路径（Maven / JDK 全靠探测）
#   - 检查逻辑在 scripts/doctor.py（可测试），本文件只负责编排
#   - 失败必须给出「缺什么 + 怎么装」，不允许静默退出
# ============================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$ROOT/python-service/.venv"

# POSIX 路径 -> 原生程序（python.exe）能理解的路径。
#
# 为什么必须显式转：Git Bash(MSYS) 默认会把「看起来像 POSIX 路径」的**参数**
# 自动翻成 Windows 形式，但这个自动转换可以被用户关掉
# （MSYS_NO_PATHCONV=1 / MSYS2_ARG_CONV_EXCL=* —— 很多人为了 docker/kubectl
# 会全局设上）。一旦关掉，`python -m venv /d/x/.venv` 会静默建到
# `D:\d\x\.venv`，而 `python -m venv` 仍返回 0，脚本会误判成「成功但找不到」。
#
# 不依赖隐式行为：这里显式转换。Linux/macOS 上没有 cygpath，原样返回。
native_path() {
  if [ -n "${MSYSTEM:-}${CYGWIN:-}" ] && command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

echo "============================================================"
echo " Polyface 环境准备"
echo "============================================================"
echo

# ---------------------------------------------------------- 1. 找 Python
#
# ⚠️ **不能只看 `command -v`**：Windows 上 `python3` 常常解析到 Microsoft Store 的
# **占位程序**（`...\WindowsApps\python3.exe`）—— 它什么都不输出、退出码非 0，
# 但"命令确实存在"。只看 command -v 就会选到它，接着版本检查拿到**空串**，
# 用户看到的是一句没法排查的「Python 版本过低（当前 ）」。
# 实测（2026-10-07）就这么卡住了首次 setup。
# 所以：**逐个候选真的执行一次**，能跑起来且版本够的才采用。
PY=""
found_but_unusable=""
for cand in python3 python py; do
  command -v "$cand" >/dev/null 2>&1 || continue
  p="$(command -v "$cand")"
  if "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
    PY="$p"
    break
  fi
  [ -z "$found_but_unusable" ] && found_but_unusable="$p"
done
if [ -z "$PY" ]; then
  echo "[阻塞] 未找到可用的 Python（需 3.11 或更高）。"
  if [ -n "$found_but_unusable" ]; then
    echo "        探测到的 $found_but_unusable 跑不起来或版本过低。"
    echo "        ⚠️ Windows 上常见原因：PATH 里的 python3 是 Microsoft Store 的占位程序，"
    echo "           它会干扰探测 —— 请安装真实 Python 并把它放到 PATH 前面，"
    echo "           或关闭「应用执行别名」里的 python3。"
  fi
  echo "        => https://www.python.org/downloads/"
  exit 1
fi
echo "[OK  ] Python $("$PY" -V 2>&1 | awk '{print $2}')（$PY）"

# ---------------------------------------------------------- 3. venv
venv_python() {
  # POSIX 与 Windows(Git Bash) 两种布局都认
  for p in "$VENV_DIR/bin/python" "$VENV_DIR/Scripts/python.exe"; do
    [ -x "$p" ] && { printf '%s' "$p"; return 0; }
  done
  return 1
}

if ! VPY="$(venv_python)"; then
  echo "[....] 创建 Python 虚拟环境..."
  if ! "$PY" -m venv "$(native_path "$VENV_DIR")"; then
    echo "[阻塞] 创建 venv 失败。"
    echo "        => 确认磁盘可写；Debian/Ubuntu 可能需先装 python3-venv："
    echo "             sudo apt install python3-venv"
    exit 1
  fi
  if ! VPY="$(venv_python)"; then
    # 到这里说明 venv 命令「成功」了但预期位置没有 python —— 几乎只有两种可能：
    # 路径没被正确翻译（Windows 上路径被当成相对当前盘），或磁盘/权限异常。
    echo "[阻塞] venv 命令返回成功，但 $VENV_DIR 下找不到 python 可执行文件。"
    echo "        => 若你在 Windows 的 Git Bash 里运行，多半是路径未被翻译："
    echo "             请确认没有设置 MSYS_NO_PATHCONV / MSYS2_ARG_CONV_EXCL，"
    echo "             然后删掉可能被建到别处的 .venv 目录，重跑本脚本。"
    echo "        => 若你在 macOS / Linux 上运行，请检查该目录是否可写。"
    echo "           诊断： ls -la \"$VENV_DIR\""
    exit 1
  fi
fi

# ---------------------------------------------------------- 4. pip 自检
# `python -m venv` 在少数环境下会建出**没有 pip** 的 venv（精简发行版、
# ensurepip 被策略禁用）。此时若直接装依赖，报错是「No module named pip」，
# 容易误导成网络问题。所以先确认 pip 存在，缺了用 ensurepip 补。
if ! "$VPY" -m pip --version >/dev/null 2>&1; then
  echo "[....] venv 内没有 pip，尝试用 ensurepip 修复..."
  if ! "$VPY" -m ensurepip --upgrade --default-pip >/dev/null 2>&1 \
     || ! "$VPY" -m pip --version >/dev/null 2>&1; then
    echo "[阻塞] 虚拟环境里没有 pip，且自动修复失败。"
    echo "        => 手动修复：\"$VPY\" -m ensurepip --upgrade --default-pip"
    echo "           若仍失败，请重装 Python（安装时勾选 pip）后删除"
    echo "           python-service/.venv 目录，再重跑本脚本。"
    exit 1
  fi
  echo "[OK  ] 已修复 pip"
fi
echo "[OK  ] Python 虚拟环境"

# ---------------------------------------------------------- 5. 依赖
echo "[....] 安装 Python 依赖（首次需联网，可能要一两分钟）..."
if ! "$VPY" -m pip install --disable-pip-version-check -q -r "$(native_path "$ROOT/python-service/requirements.txt")"; then
  echo
  echo "[阻塞] Python 依赖安装失败。常见原因与处理："
  echo "        · 网络不通 / 需要代理   -> 确认能访问 pypi.org"
  echo "        · 公司网络拦截         -> 换镜像源后重试："
  echo "            \"$VPY\" -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple"
  echo "        · 系统 Python 受管控   -> 确认 venv 目录可写"
  exit 1
fi
echo "[OK  ] Python 依赖"

# ---------------------------------------------------------- 6. .env
if [ ! -f "$ROOT/python-service/.env" ]; then
  cp "$ROOT/python-service/.env.example" "$ROOT/python-service/.env"
  echo "[提示] 已生成 python-service/.env"
  echo "        不填 LLM_API_KEY 也能启动 —— 走「离线演示模式」，产出是演示结果"
  echo "        要真实生成：编辑该文件填入 LLM_API_KEY（生成时素材正文会发送给该服务商）"
fi

# ---------------------------------------------------------- 7. Java jar
if [ -f "$ROOT/polyface.jar" ]; then
  echo "[OK  ] Java 后端 polyface.jar（已存在，跳过构建）"
elif [ -f "$ROOT/java-backend/target/polyface.jar" ]; then
  echo "[OK  ] Java 后端 polyface.jar（已构建，跳过）"
else
  # 注意：Windows/Git Bash 下必须走 scripts/mvn.sh；Linux/macOS 直接用 mvn
  MVN_LAUNCHER="mvn"
  if [ -x "$ROOT/scripts/mvn.sh" ] && [ -n "${MSYSTEM:-}" ]; then
    MVN_LAUNCHER="$ROOT/scripts/mvn.sh"
  fi
  if ! command -v mvn >/dev/null 2>&1; then
    echo
    echo "[阻塞] 未找到 Maven，无法构建 Java 后端。"
    echo "        => 二选一："
    echo "           1) 安装 Maven：https://maven.apache.org/download.cgi"
    echo "           2) 改用 Release 发布包（自带预构建 jar，**不需要 Maven**）"
    exit 1
  fi
  echo "[....] 构建 Java 后端（首次会下载依赖，可能需要几分钟）..."
  if ! bash "$MVN_LAUNCHER" -q -f "$ROOT/java-backend/pom.xml" -DskipTests package; then
    echo "[阻塞] Java 后端构建失败。请查看上方 Maven 报错。"
    echo "        => 常见原因：网络无法访问 Maven 中央仓库、JDK 版本低于 17"
    exit 1
  fi
  echo "[OK  ] Java 后端 polyface.jar"
fi

# ---------------------------------------------------------- 8. 收尾体检
echo
echo "---------------- 启动前体检 ----------------"
if ! "$VPY" "$(native_path "$ROOT/scripts/doctor.py")" --quiet; then
  echo
  echo "存在阻塞项，请按上方提示处理后重试。"
  exit 1
fi
# doctor.py --quiet 成功时**什么都不打印**，标题下面会是一片空白 ——
# 补一行明确回执，否则用户看不出体检到底过没过（docs/62 §8 跟进项）。
echo "[OK  ] 体检通过"
echo
echo "============================================================"
echo " 准备完成。运行 scripts/start.sh 启动。"
echo "============================================================"
