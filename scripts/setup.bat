@echo off
setlocal
chcp 65001 >nul
set ROOT=%~dp0..
cd /d "%ROOT%"

echo [1/3] Preparing Python venv...
cd python-service
if not exist .venv (
    python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --disable-pip-version-check -q -r requirements.txt
cd ..

echo [2/3] Creating .env if missing...
if not exist python-service\.env (
    copy python-service\.env.example python-service\.env >nul
    echo   .env created. Edit python-service\.env to add LLM_API_KEY (or keep LLM_MOCK=true for offline demo).
)

echo [3/3] Building Java backend (first run downloads dependencies, may take minutes)...
cd java-backend
call mvn -q -DskipTests package 2>nul
if errorlevel 1 (
    echo   PATH mvn unavailable, trying D:\apache-maven-3.9.11...
    call "D:\apache-maven-3.9.11\bin\mvn.cmd" -q -DskipTests package
)
cd ..

echo.
echo Setup done. Run scripts\start.bat to launch.
pause
