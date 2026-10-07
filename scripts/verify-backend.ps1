[CmdletBinding()]
param(
    [string]$Python = "",
    [string[]]$Tests = @(),
    [switch]$Install
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$backendRoot = Join-Path $repoRoot "backend"
$requirements = Join-Path $backendRoot "requirements-dev.txt"
$baseTemp = Join-Path $backendRoot ".pytest_verify_$PID"
$pythonPrefix = @()

if ($Python) {
    if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
        throw "The specified Python executable does not exist: $Python"
    }
    $pythonExe = (Resolve-Path -LiteralPath $Python).Path
}
else {
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $pythonExe = $pyLauncher.Source
        $pythonPrefix = @("-3")
    }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) {
            throw "Python was not found. Install Python 3 or pass python.exe with -Python."
        }
        $pythonExe = $pythonCommand.Source
    }
}

if (-not (Test-Path -LiteralPath $requirements -PathType Leaf)) {
    throw "The development requirements file is missing: $requirements"
}

$exitCode = 1
Push-Location $backendRoot
try {
    & $pythonExe @pythonPrefix --version
    if ($LASTEXITCODE -ne 0) {
        throw "Python failed to start: $pythonExe"
    }

    if ($Install) {
        & $pythonExe @pythonPrefix -m pip install -r $requirements
        if ($LASTEXITCODE -ne 0) {
            throw "Development dependency installation failed."
        }
    }

    & $pythonExe @pythonPrefix -m pytest -q "--basetemp=$baseTemp" @Tests
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
    if (Test-Path -LiteralPath $baseTemp) {
        $resolvedBackend = [IO.Path]::GetFullPath($backendRoot).TrimEnd('\')
        $resolvedTemp = [IO.Path]::GetFullPath($baseTemp)
        if (-not $resolvedTemp.StartsWith("$resolvedBackend\.pytest_verify_", [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean an unverified temporary directory: $resolvedTemp"
        }
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}

exit $exitCode
