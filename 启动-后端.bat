@echo off
chcp 65001 >nul
REM ============================================================
REM  智学中枢 · 后端一键启动（U 盘便携，任意盘符均可）
REM  %~dp0 = 本脚本所在目录，自动定位，无需关心盘符
REM ============================================================
cd /d "%~dp0backend"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 python。请先在本机安装 Python 3.11+ 并勾选 "Add to PATH"。
  echo        下载：https://www.python.org/downloads/
  pause
  exit /b 1
)

echo [1/2] 安装/校验后端依赖（首次换机器较慢，已装好则数秒跳过）...
python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [错误] 依赖安装失败，请检查网络后重试。
  pause
  exit /b 1
)

echo.
echo [2/2] 启动后端：http://localhost:8000  （Ctrl+C 停止）
echo       provider 由 backend\.env 的 LLM_PROVIDER 决定（deepseek=真实 / mock=离线免key）
echo.
python -m uvicorn app.main:app --port 8000
pause
