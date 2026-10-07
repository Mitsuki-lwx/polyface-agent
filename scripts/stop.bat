@echo off
rem ============================================================
rem Polyface 停止
rem
rem 按端口精确结束本应用进程（端口可用 POLYFACE_JAVA_PORT / POLYFACE_PY_PORT 覆盖）。
rem **不误杀**：先查进程映像名，只结束 java / python 进程；
rem 端口被别的程序占用时只提示、不动手（见 docs/23 Q3）。
rem 结束前需确认（docs/25 B2/B3）—— 免得误杀你自己在跑的脚本。
rem ============================================================
setlocal
chcp 65001 >nul

set "ROOT=%~dp0.."
rem 端口可用 POLYFACE_JAVA_PORT / POLYFACE_PY_PORT 覆盖（与 start.bat / start.sh 对齐）
rem ⚠️ 不读环境变量的话：用覆盖端口启动后就停不掉（只能手动 taskkill）
if not defined POLYFACE_JAVA_PORT set "POLYFACE_JAVA_PORT=8080"
if not defined POLYFACE_PY_PORT set "POLYFACE_PY_PORT=8000"
set "JAVA_PORT=%POLYFACE_JAVA_PORT%"
set "PY_PORT=%POLYFACE_PY_PORT%"
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"

echo 即将检查端口 %JAVA_PORT%（Java 后端）与 %PY_PORT%（Python LLM 服务）。
echo 只会结束占用这两个端口的 java / python 进程；其它程序一律跳过。
echo.
rem /T 60 /D N：60 秒无输入则默认「取消」—— 默认不动手，避免脚本被挂起
choice /C YN /T 60 /D N /M "确认继续？"
if errorlevel 2 (
  echo 已取消，未结束任何进程。
  echo.
  pause
  exit /b 0
)
echo.

echo 正在查找 Polyface 进程...
echo.

set "FOUND=0"
call :kill_port %JAVA_PORT% "Java 后端"
call :kill_port %PY_PORT% "Python LLM 服务"

echo.
if "%FOUND%"=="0" (
  echo 没有发现正在运行的 Polyface 服务（端口 %JAVA_PORT% / %PY_PORT% 均空闲）。
) else (
  echo 已停止。正在复查端口...
  call :verify_port %JAVA_PORT% "Java 后端"
  call :verify_port %PY_PORT% "Python LLM 服务"
)
echo.
pause
exit /b 0

rem ------------------------------------------------------------
:verify_port
set "VPORT=%~1"
set "VLABEL=%~2"
set "VPID="
for /f "tokens=5" %%p in ('netstat -ano -p tcp ^| findstr /c:"LISTENING" ^| findstr /c:":%VPORT% "') do if not defined VPID set "VPID=%%p"
if defined VPID (
  echo [警告] %VLABEL% ^(端口 %VPORT%^) 仍被 PID %VPID% 占用 —— 可能未完全退出或需要管理员权限
) else (
  echo [已释放] %VLABEL% ^(端口 %VPORT%^)
)
goto :eof

rem ------------------------------------------------------------
:kill_port
set "PORT=%~1"
set "LABEL=%~2"
set "PID="
for /f "tokens=5" %%p in ('netstat -ano -p tcp ^| findstr /r /c:"LISTENING" ^| findstr /c:":%PORT% "') do if not defined PID set "PID=%%p"
if not defined PID (
  echo [空闲] %LABEL% ^(端口 %PORT%^)
  goto :eof
)

set "IMG="
for /f "tokens=1 delims=," %%i in ('tasklist /FI "PID eq %PID%" /FO CSV /NH 2^>nul') do if not defined IMG set "IMG=%%~i"
if not defined IMG (
  echo [跳过] %LABEL% ^(端口 %PORT%^) 被 PID %PID% 占用，但查不到进程名 —— 不动手
  goto :eof
)

rem 只结束本应用可能用到的解释器，避免误杀
set "SAFE="
if /i "%IMG%"=="java.exe"   set "SAFE=1"
if /i "%IMG%"=="javaw.exe"  set "SAFE=1"
if /i "%IMG%"=="python.exe" set "SAFE=1"
if /i "%IMG%"=="pythonw.exe" set "SAFE=1"

if not defined SAFE (
  echo [跳过] 端口 %PORT% 被 %IMG% ^(PID %PID%^) 占用 —— 不是本应用的进程，未结束。
  echo         若确认要结束，请手动执行：taskkill /PID %PID% /F
  goto :eof
)

echo [停止] %LABEL% ^(%IMG% PID %PID%，端口 %PORT%^)
taskkill /PID %PID% /T /F >nul 2>nul
if errorlevel 1 (
  echo         结束失败，可能需要管理员权限。
) else (
  set "FOUND=1"
)
goto :eof
