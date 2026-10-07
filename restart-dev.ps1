<#
  restart-dev.ps1 — 一键重启「智学中枢」前后端开发环境

  作用：
    1. 杀掉占用开发端口的所有进程（后端 8000 + 前端 3000-3005）；
    2. 在两个独立 PowerShell 窗口分别启动后端（uvicorn）与前端（vite），日志可见、可 Ctrl+C。

  用法（任选其一）：
    右键“使用 PowerShell 运行”，或在终端执行：
      powershell -ExecutionPolicy Bypass -File .\restart-dev.ps1
    在本会话里可直接：  ! powershell -ExecutionPolicy Bypass -File restart-dev.ps1
#>

$ErrorActionPreference = 'SilentlyContinue'

# 脚本所在目录即项目根目录（无论从哪运行都能定位 backend/ 与 frontend/）
$Root     = $PSScriptRoot
$Backend  = Join-Path $Root 'backend'
$Frontend = Join-Path $Root 'frontend'
$BackendPort = 8000
$FrontendPorts = 3000,3001,3002,3003,3004,3005   # vite 会自动顺延到第一个空闲端口

Write-Host "==> 释放开发端口..." -ForegroundColor Cyan
foreach ($port in (@($BackendPort) + $FrontendPorts)) {
    $pids = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess | Sort-Object -Unique
    foreach ($procId in $pids) {
        if ($procId -and $procId -ne 0) {
            $name = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
            Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
            Write-Host ("    端口 {0}: 已结束 PID {1} ({2})" -f $port, $procId, $name) -ForegroundColor DarkGray
        }
    }
}

Start-Sleep -Seconds 2

# 兜底确认后端端口已释放（SQLite 被强杀后偶有 WAL 残留，重启时会自动恢复）
if (Get-NetTCPConnection -LocalPort $BackendPort -State Listen -ErrorAction SilentlyContinue) {
    Write-Host "!! 端口 $BackendPort 仍被占用，请手动检查后重试。" -ForegroundColor Yellow
}

Write-Host "==> 启动后端 (uvicorn :$BackendPort, --reload)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command',
    "Set-Location -LiteralPath '$Backend'; uvicorn app.main:app --reload --port $BackendPort"
)

Write-Host "==> 启动前端 (vite, 默认 :3000)..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList @(
    '-NoExit', '-Command',
    "Set-Location -LiteralPath '$Frontend'; npm run dev"
)

Write-Host ""
Write-Host "已在两个新窗口分别启动前后端。" -ForegroundColor Green
Write-Host "  后端:  http://localhost:$BackendPort/api/v1/health" -ForegroundColor Green
Write-Host "  前端:  http://localhost:3000/  (端口被占时看前端窗口实际输出)" -ForegroundColor Green
Write-Host "  账号:  learner_001 / 123456" -ForegroundColor Green
