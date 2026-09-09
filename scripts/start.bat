@echo off
setlocal
chcp 65001 >nul
set ROOT=%~dp0..

echo Starting Python LLM service on http://127.0.0.1:8000 ...
start "polyface-python" cmd /k "cd /d "%ROOT%\python-service" && .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000"

echo Starting Java backend on http://127.0.0.1:8080 ...
start "polyface-java" cmd /k "cd /d "%ROOT%\java-backend" && call mvn -q spring-boot:run -Dspring-boot.run.arguments=--server.port=8080 2>nul || call "D:\apache-maven-3.9.11\bin\mvn.cmd" -q spring-boot:run -Dspring-boot.run.arguments=--server.port=8080"

echo Opening browser in 10s...
timeout /t 10 /nobreak >nul
start http://127.0.0.1:8080/health
endlocal
