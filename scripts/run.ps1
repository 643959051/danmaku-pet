$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".conda\env\python.exe"

if (-not (Test-Path $Python)) {
  throw "Project Python not found: $Python. Run scripts\install.ps1 first."
}

$env:TEMP = Join-Path $Root "temp\process-temp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $Root ".cache\pip"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Force $env:TEMP | Out-Null

& $Python -m danmaku_pet @args
exit $LASTEXITCODE
