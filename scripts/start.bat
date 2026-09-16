@echo off
rem ============================================================
rem Polyface 启动
rem
rem 设计（docs/23 v1.1）：
rem   - Java 走 `java -jar`，**运行时不需要 Maven**
rem   - 启动前体检（scripts\doctor.py）：缺依赖/端口占用 → 明确提示并中止
rem   - 浏览器在**两个服务都就绪后**才打开（轮询，不是固定等待）
rem   - 数据目录显式指定为 <root>\data，避免 java -jar 的默认 `../data`
rem     把用户数据写到包外面（见 docs/48 现状核查）
rem ============================================================
setlocal
chcp 65001 >nul

set "ROOT=%~dp0.."
if "%ROOT:~-1%"=="\" set "ROOT=%ROOT:~0,-1%"
cd /d "%ROOT%"

rem ---------------------------------------------------------- 找 Python
set "PY_EXE="
if exist "%ROOT%\python-service\.venv\Scripts\python.exe" set "PY_EXE=%ROOT%\python-service\.venv\Scripts\python.exe"
if not defined PY_EXE for /f "delims=" %%p in ('where python 2^>nul') do if not defined PY_EXE set "PY_EXE=%%p"
if not defined PY_EXE (
  echo [阻塞] 未找到 Python，无法启动。
  echo         =^> 需 Python 3.11+：https://www.python.org/downloads/
  echo            或先运行 scripts\setup.bat
  pause
  exit /b 1
)

rem ---------------------------------------------------------- 体检
"%PY_EXE%" "%ROOT%\scripts\doctor.py" --quiet
if errorlevel 1 (
  echo.
  echo 存在阻塞项，已中止启动。请按上方提示处理后重试。
  echo 首次使用请先运行 scripts\setup.bat
  echo.
  pause
  exit /b 1
)

echo.
echo 正在启动服务（两个窗口，请勿关闭）...

rem 数据目录：显式指定，保证数据落在包内（Spring Boot 松绑定：POLYFACE_DATA_DIR）
set "POLYFACE_DATA_DIR=%ROOT%\data"

rem ---------------------------------------------------------- Python LLM 服务
rem 在 python-service 目录下启动 —— .env 是相对 CWD 读取的
start "polyface-python" cmd /k "cd /d "%ROOT%\python-service" && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

rem ---------------------------------------------------------- Java 后端（java -jar）
start "polyface-java" cmd /k "cd /d "%ROOT%" && java -jar "%ROOT%\polyface.jar""

rem ---------------------------------------------------------- 等就绪
rem 注意：Java 的 /health 在 Python 不可达时**仍返回 200**（内部吞掉异常），
rem 所以必须分别探测两个服务，不能只看 Java。
set "CURL=curl"
where curl >nul 2>nul || set "CURL="

if not defined CURL (
  echo [提示] 未找到 curl，无法自动检测就绪状态。
  echo         等待 20 秒后打开浏览器（若页面打不开，稍等片刻刷新即可）。
  timeout /t 20 /nobreak >nul
  goto :open
)

set /a WAITED=0
:wait_python
%PY_EXE% -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',8000))==0 else 1)" 2>nul
if not errorlevel 1 goto :wait_java
set /a WAITED+=1
if %WAITED% GEQ 120 goto :timeout
timeout /t 1 /nobreak >nul
goto :wait_python

:wait_java
set /a WAITED=0
:wait_java_loop
%PY_EXE% -c "import socket,sys; s=socket.socket(); s.settimeout(0.5); sys.exit(0 if s.connect_ex(('127.0.0.1',8080))==0 else 1)" 2>nul
if not errorlevel 1 goto :open
set /a WAITED+=1
if %WAITED% GEQ 120 goto :timeout
timeout /t 1 /nobreak >nul
goto :wait_java_loop

:open
echo 服务已就绪，打开浏览器...
start "" "http://127.0.0.1:8080"
exit /b 0

:timeout
echo.
echo [警告] 等待 120 秒后仍未就绪。
echo         =^> 请查看那两个服务窗口里的报错信息；
echo            常见原因：端口被占用、LLM Key 无效、依赖缺失。
echo            可运行 scripts\stop.bat 清理后重试。
pause
exit /b 1
