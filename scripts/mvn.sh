#!/bin/bash
# Maven 启动封装（Git Bash on Windows）
#
# 为什么需要它：在 Git Bash 里直接跑 `mvn` 会把 POSIX 路径传给 java，
# 导致 `ClassNotFoundException: org.codehaus.plexus.classworlds.launcher.Launcher`。
# 本脚本显式传 Windows 路径给 classworlds。
#
# 用法：scripts/mvn.sh -q compile
#       scripts/mvn.sh test
#       scripts/mvn.sh spring-boot:run -Dspring-boot.run.arguments="--server.port=8080"
#
# Maven 位置按以下顺序确定（**不硬编码任何机器路径**）：
#   1. 环境变量 POLYFACE_MAVEN_HOME
#   2. 从 PATH 上的 `mvn` 反推安装目录
#   3. 常见安装位置（Program Files / Homebrew / 发行版包管理）
# classworlds jar 版本随 Maven 版本变化（3.9.x 为 2.9.0），脚本自动探测。
#
# check-scripts:allow S3 本脚本刻意不用 set -e —— JAR_NAME 的赋值用了
#   `ls | grep | head` 管道，未命中时返回非 0；set -e 会在赋值处直接退出，
#   反而跳过下面那条友好报错。用 set -u 足够。

set -u

die() { echo "错误：$*" >&2; exit 1; }

# POSIX 路径 -> Windows 路径（java 需要 Windows 形式）
to_win_path() {
  if command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
    return
  fi
  # 无 cygpath 时手工转换：/d/apache-maven -> D:\apache-maven
  printf '%s' "$1" | sed 's|^/\([a-zA-Z]\)/|\1:\\|; s|/|\\|g'
}

# POSIX 路径 -> 统一 POSIX 形式（接受用户给 Windows 或 POSIX 形式）
to_posix_path() {
  case "$1" in
    [A-Za-z]:[\\/]*)
      if command -v cygpath >/dev/null 2>&1; then
        cygpath -u "$1"
      else
        printf '%s' "$1" | sed 's|^\([A-Za-z]\):|/\L\1|; s|\\|/|g'
      fi
      ;;
    *) printf '%s' "$1" ;;
  esac
}

MVN_HOME_POSIX=""

# 1) 环境变量
if [ -n "${POLYFACE_MAVEN_HOME:-}" ]; then
  MVN_HOME_POSIX="$(to_posix_path "$POLYFACE_MAVEN_HOME")"
fi

# 2) 从 PATH 上的 mvn 反推（<home>/bin/mvn -> <home>）
if [ -z "$MVN_HOME_POSIX" ]; then
  mvn_bin="$(command -v mvn 2>/dev/null || true)"
  if [ -n "$mvn_bin" ]; then
    MVN_HOME_POSIX="$(cd "$(dirname "$mvn_bin")/.." 2>/dev/null && pwd || true)"
  fi
fi

# 3) 常见安装位置
if [ -z "$MVN_HOME_POSIX" ]; then
  for hit in \
    "/c/Program Files"/apache-maven-* \
    "/c/Program Files (x86)"/apache-maven-* \
    /usr/share/maven \
    /usr/local/opt/maven/libexec \
    /opt/homebrew/opt/maven/libexec \
    /opt/maven
  do
    if [ -x "$hit/bin/mvn" ]; then MVN_HOME_POSIX="$hit"; break; fi
  done
fi

if [ -z "$MVN_HOME_POSIX" ] || [ ! -d "$MVN_HOME_POSIX" ]; then
  if [ -n "${POLYFACE_MAVEN_HOME:-}" ]; then
    # 用户明确设了变量却指向不存在的目录 —— 直接点破，别让他去试其它办法
    cat >&2 <<EOF
错误：POLYFACE_MAVEN_HOME 指向的目录不存在。

  POLYFACE_MAVEN_HOME = $POLYFACE_MAVEN_HOME
  解析为              = ${MVN_HOME_POSIX:-（无法解析）}

请检查路径拼写，或改用 Release 发布包（自带预构建 jar，不需要 Maven）。
EOF
    exit 1
  fi
  cat >&2 <<'EOF'
错误：未找到 Maven 安装目录。

请三选一：
  1) 设置环境变量指向安装目录，例如（Git Bash）：
       export POLYFACE_MAVEN_HOME="D:/apache-maven-3.9.11"
     或（Windows CMD）：
       set POLYFACE_MAVEN_HOME=D:\apache-maven-3.9.11
  2) 把 Maven 的 bin 目录加入 PATH
  3) 改用 Release 发布包 —— 它自带预构建的 polyface.jar，**不需要 Maven**

下载：https://maven.apache.org/download.cgi
EOF
  exit 1
fi

# 自动探测 classworlds jar（不硬编码版本号）
JAR_NAME="$(ls "$MVN_HOME_POSIX/boot/" 2>/dev/null | grep -E '^plexus-classworlds-.*\.jar$' | head -1)"
if [ -z "${JAR_NAME:-}" ]; then
  die "$MVN_HOME_POSIX/boot/ 下找不到 plexus-classworlds jar ——
       请确认该目录是完整的 Maven 安装（应同时含 boot/ 与 bin/）。"
fi

MVN_HOME_WIN="$(to_win_path "$MVN_HOME_POSIX")"
JAR="$MVN_HOME_WIN\\boot\\$JAR_NAME"

exec java -classpath "$JAR" \
  "-Dclassworlds.conf=$MVN_HOME_WIN\\bin\\m2.conf" \
  "-Dmaven.home=$MVN_HOME_WIN" \
  "-Dmaven.multiModuleProjectDirectory=$(pwd -W 2>/dev/null || pwd)" \
  org.codehaus.plexus.classworlds.launcher.Launcher "$@"
