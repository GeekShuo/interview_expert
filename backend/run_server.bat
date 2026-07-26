@echo off
REM AI 模拟面试系统 启动脚本
cd /d %~dp0
set NO_PROXY=localhost,127.0.0.1
set no_proxy=localhost,127.0.0.1
if not exist .env copy .env.example .env
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 > server.log 2>&1
