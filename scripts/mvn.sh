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
# 注：Maven 安装路径可用环境变量 POLYFACE_MAVEN_HOME 覆盖（默认 D:\apache-maven-3.9.11）。
#     classworlds jar 版本随 Maven 版本变化（3.9.x 为 2.9.0），脚本会自动探测。

set -u

MVN_HOME_WIN="${POLYFACE_MAVEN_HOME:-D:\\apache-maven-3.9.11}"
MVN_HOME_POSIX="$(echo "$MVN_HOME_WIN" | sed 's|\\\\|/|g; s|^\([A-Za-z]\):|/\L\1|')"

if [ ! -d "$MVN_HOME_POSIX" ]; then
  echo "错误：找不到 Maven 目录 $MVN_HOME_POSIX" >&2
  echo "请设置 POLYFACE_MAVEN_HOME 指向 Maven 安装目录。" >&2
  exit 1
fi

# 自动探测 classworlds jar（不硬编码版本号）
JAR_NAME="$(ls "$MVN_HOME_POSIX/boot/" 2>/dev/null | grep -E '^plexus-classworlds-.*\.jar$' | head -1)"
if [ -z "${JAR_NAME:-}" ]; then
  echo "错误：$MVN_HOME_POSIX/boot/ 下找不到 plexus-classworlds jar" >&2
  exit 1
fi

JAR="$MVN_HOME_WIN\\boot\\$JAR_NAME"

exec java -classpath "$JAR" \
  "-Dclassworlds.conf=$MVN_HOME_WIN\\bin\\m2.conf" \
  "-Dmaven.home=$MVN_HOME_WIN" \
  "-Dmaven.multiModuleProjectDirectory=$(pwd -W 2>/dev/null || pwd)" \
  org.codehaus.plexus.classworlds.launcher.Launcher "$@"
