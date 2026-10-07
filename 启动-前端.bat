@echo off
chcp 65001 >nul
REM ============================================================
REM  智学中枢 · 前端一键启动（U 盘便携，任意盘符均可）
REM ============================================================
cd /d "%~dp0frontend"

where npm >nul 2>nul
if errorlevel 1 (
  echo [错误] 未检测到 npm。请先在本机安装 Node.js 18+（自带 npm）。
  echo        下载：https://nodejs.org/
  pause
  exit /b 1
)

if not exist "node_modules\vite\package.json" (
  echo [1/2] 未发现前端依赖，开始安装（首次较慢）...
  call npm install
  if errorlevel 1 ( echo [错误] npm install 失败，请检查网络。 & pause & exit /b 1 )
) else (
  echo [1/2] 前端依赖已存在，跳过安装。
  echo       若启动报 esbuild / rollup 原生模块错误（多见于跨系统/架构），请手动执行一次：npm install
)

echo.
echo [2/2] 启动前端：http://localhost:3001  （Ctrl+C 停止；需后端先在 :8000 运行）
echo.
call npm run dev
pause
