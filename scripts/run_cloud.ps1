$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".conda\env\python.exe"

if (-not (Test-Path $Python)) {
  throw "Project Python not found: $Python. Run scripts\install.ps1 first."
}

# 本地开发直接用内存队列，单实例单 Worker（与 Docker 中一致）。
if (-not $env:APP_ENV) { $env:APP_ENV = "dev" }
if (-not $env:GATEWAY_MODE) { $env:GATEWAY_MODE = "mock" }
if (-not $env:DEFAULT_ROOM_ID) { $env:DEFAULT_ROOM_ID = "dev-room" }
if (-not $env:ENABLE_TEST_ENDPOINTS) { $env:ENABLE_TEST_ENDPOINTS = "true" }

$env:TEMP = Join-Path $Root "temp\cloud-temp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $Root ".cache\pip"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Force $env:TEMP | Out-Null

& $Python -m danmaku_pet.cloud @args
exit $LASTEXITCODE
