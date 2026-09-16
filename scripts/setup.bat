@echo off
rem ============================================================
rem Polyface 环境准备（首次使用跑一次）
rem
rem 做四件事：建 venv → 装 Python 依赖 → 生成 .env → 构建 Java jar
rem
rem 设计（docs/23 v1.1）：
rem   - **无任何机器相关硬编码路径**（Maven / JDK 全靠探测）
rem   - 检查逻辑在 scripts\doctor.py（可测试），本文件只负责编排
rem   - 失败必须给出「缺什么 + 怎么装」，不允许一闪而过
rem
rem 注：不使用 enabledelayedexpansion —— 避免路径含 ! 时被吞。
rem ============================================================
setlocal
chcp 65001 >nul

set "ROOT=%~dp0.."
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

echo ============================================================
echo  Polyface 环境准备
echo ============================================================
echo.

rem ---------------------------------------------------------- 1. 找到 Python
rem 本文件唯一必须自己做的事：doctor.py 需要 Python 才能运行
set "PY_EXE="
if exist "%ROOT%\python-service\.venv\Scripts\python.exe" set "PY_EXE=%ROOT%\python-service\.venv\Scripts\python.exe"
if not defined PY_EXE for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY_EXE set "PY_EXE=%%p"
if not defined PY_EXE (
  echo [阻塞] 未找到 Python。
  echo         =^> 需 Python 3.11 或更高：https://www.python.org/downloads/
  echo            安装时务必勾选 "Add python.exe to PATH"，装完请重开本窗口。
  echo.
  pause
  exit /b 1
)

rem ---------------------------------------------------------- 2. 版本检查
set "PY_RAW="
for /f "tokens=2" %%v in ('"%PY_EXE%" -V 2^>^&1') do if not defined PY_RAW set "PY_RAW=%%v"
set "PY_MAJOR="
set "PY_MINOR="
for /f "tokens=1,2 delims=." %%a in ("%PY_RAW%") do (
  set "PY_MAJOR=%%a"
  set "PY_MINOR=%%b"
)
if not defined PY_MAJOR (
  echo [阻塞] 无法解析 Python 版本（输出：%PY_RAW%）
  echo         =^> 请确认 `python -V` 能正常输出。
  pause
  exit /b 1
)
if %PY_MAJOR% LSS 3 goto :py_too_old
if %PY_MAJOR% EQU 3 if not defined PY_MINOR goto :py_too_old
if %PY_MAJOR% EQU 3 if %PY_MINOR% LSS 11 goto :py_too_old
echo [OK  ] Python %PY_RAW%

rem ---------------------------------------------------------- 3. venv
set "VPY=%ROOT%\python-service\.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo [....] 创建 Python 虚拟环境...
  "%PY_EXE%" -m venv "%ROOT%\python-service\.venv"
  if errorlevel 1 (
    echo [阻塞] 创建 venv 失败。
    echo         =^> 确认磁盘可写；若 Python 装在受保护目录，请用普通用户权限重试。
    pause
    exit /b 1
  )
)

rem pip 自检：`python -m venv` 在少数环境下会建出**没有 pip** 的 venv
rem （如精简版 Python、ensurepip 被策略禁用、企业镜像）。此时若直接进第 4 步，
rem 报错是「No module named pip」，而下方的提示却会误导成网络问题。
rem 所以这里先确认 pip 存在，缺了就用 ensurepip 补。
"%VPY%" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo [....] venv 内没有 pip，尝试用 ensurepip 修复...
  "%VPY%" -m ensurepip --upgrade --default-pip >nul 2>nul
  "%VPY%" -m pip --version >nul 2>nul
  if errorlevel 1 (
    echo.
    echo [阻塞] 虚拟环境里没有 pip，且自动修复失败。
    echo         =^> 手动修复：
    echo            "%VPY%" -m ensurepip --upgrade --default-pip
    echo            若仍失败，请重装 Python（安装时勾选 pip）后删除
    echo            python-service\.venv 目录，再重跑本脚本。
    echo.
    pause
    exit /b 1
  )
  echo [OK  ] 已修复 pip
)
echo [OK  ] Python 虚拟环境

rem ---------------------------------------------------------- 4. 依赖
echo [....] 安装 Python 依赖（首次需联网，可能要一两分钟）...
"%VPY%" -m pip install --disable-pip-version-check -q -r "%ROOT%\python-service\requirements.txt"
if errorlevel 1 (
  echo.
  echo [阻塞] Python 依赖安装失败。常见原因与处理：
  echo         · 网络不通 / 需要代理   -^> 确认能访问 pypi.org
  echo         · 公司网络拦截         -^> 换镜像源后重试：
  echo             "%VPY%" -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
  echo         · 磁盘空间不足         -^> 清理后重试
  echo.
  pause
  exit /b 1
)
echo [OK  ] Python 依赖

rem ---------------------------------------------------------- 5. .env
if not exist "%ROOT%\python-service\.env" (
  copy /y "%ROOT%\python-service\.env.example" "%ROOT%\python-service\.env" >nul
  echo [提示] 已生成 python-service\.env
  echo         不填 LLM_API_KEY 也能启动 —— 走「离线演示模式」，产出是演示结果
  echo         要真实生成：编辑该文件填入 LLM_API_KEY（生成时素材正文会发送给该服务商）
)

rem ---------------------------------------------------------- 6. Java jar
if exist "%ROOT%\polyface.jar" (
  echo [OK  ] Java 后端 polyface.jar（已存在，跳过构建）
  goto :report
)
if exist "%ROOT%\java-backend\target\polyface.jar" (
  echo [OK  ] Java 后端 polyface.jar（已构建，跳过）
  goto :report
)

set "MVN="
for /f "delims=" %%m in ('where mvn 2^>nul') do if not defined MVN set "MVN=%%m"
if not defined MVN (
  echo.
  echo [阻塞] 未找到 Maven，无法构建 Java 后端。
  echo         =^> 二选一：
  echo            1^) 安装 Maven：https://maven.apache.org/download.cgi
  echo               装完把 bin 目录加入 PATH，重开本窗口后重跑本脚本
  echo            2^) 改用 Release 发布包（自带预构建 jar，**不需要 Maven**）
  echo.
  pause
  exit /b 1
)

echo [....] 构建 Java 后端（首次会下载依赖，可能需要几分钟，请勿关闭窗口）...
call "%MVN%" -q -f "%ROOT%\java-backend\pom.xml" -DskipTests package
if errorlevel 1 (
  echo.
  echo [阻塞] Java 后端构建失败。请查看上方 Maven 报错。
  echo         =^> 常见原因：网络无法访问 Maven 中央仓库、JDK 版本低于 17
  echo.
  pause
  exit /b 1
)
echo [OK  ] Java 后端 polyface.jar

rem ---------------------------------------------------------- 7. 收尾体检
:report
echo.
echo ---------------- 启动前体检 ----------------
"%VPY%" "%ROOT%\scripts\doctor.py" --quiet
if errorlevel 1 (
  echo.
  echo 存在阻塞项，请按上方提示处理后重试。
  pause
  exit /b 1
)
echo.
echo ============================================================
echo  准备完成。运行 scripts\start.bat 启动。
echo ============================================================
pause
exit /b 0

:py_too_old
echo [阻塞] Python 版本过低（当前 %PY_RAW%）。
echo         =^> 需 3.11 或更高：https://www.python.org/downloads/
pause
exit /b 1
