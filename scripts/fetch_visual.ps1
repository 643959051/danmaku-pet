param(
  [string]$Version = "0.1.21"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Vendor = Join-Path $Root "vendor"
$Package = Join-Path $Vendor "dafeiyu\package"
$Helper = Join-Path $Package "runtime\helper.py"
$Python = Join-Path $Root ".conda\env\python.exe"
$PatchScript = Join-Path $PSScriptRoot "apply_dafeiyu_spin_patch.py"

if (Test-Path $Helper) {
  if (Test-Path $Python) {
    & $Python $PatchScript
  }
  Write-Host "dafeiyu already present: $Package"
  exit 0
}

$Npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
if (-not $Npm) {
  $Candidate = "D:\program\node\node-v24.0.0\npm.cmd"
  if (Test-Path $Candidate) { $Npm = $Candidate }
}
if (-not $Npm) {
  throw "npm.cmd not found. Install Node locally or set NPM_CMD, then rerun. No global install is attempted."
}

New-Item -ItemType Directory -Force $Vendor | Out-Null
$env:npm_config_cache = Join-Path $Root ".cache\npm"
$env:npm_config_update_notifier = "false"
$env:npm_config_fund = "false"
$env:npm_config_audit = "false"
$env:TEMP = Join-Path $Root "temp\fetch-temp"
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force $env:TEMP, $env:npm_config_cache | Out-Null

Write-Host "Downloading dsh-dafeiyu@$Version into $Vendor ..."
& $Npm pack "dsh-dafeiyu@$Version" --pack-destination $Vendor --silent | Out-Null
if ($LASTEXITCODE -ne 0) { throw "npm pack failed" }

$Tarball = Join-Path $Vendor "dsh-dafeiyu-$Version.tgz"
if (-not (Test-Path $Tarball)) { throw "tarball not found: $Tarball" }
New-Item -ItemType Directory -Force (Join-Path $Vendor "dafeiyu") | Out-Null
tar -xzf $Tarball -C (Join-Path $Vendor "dafeiyu")
if (-not (Test-Path $Helper)) { throw "helper.py missing after extraction" }
if (Test-Path $Python) {
  & $Python $PatchScript
} else {
  Write-Warning "Project Python not found; native spin patch was not applied."
}
Write-Host "dafeiyu visual backend installed locally: $Package"
