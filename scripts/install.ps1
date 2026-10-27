$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Prefix = Join-Path $Root ".conda\env"
$Python = Join-Path $Prefix "python.exe"

$env:CONDA_PKGS_DIRS = Join-Path $Root ".cache\conda-pkgs"
$env:PIP_CACHE_DIR = Join-Path $Root ".cache\pip"
$env:TEMP = Join-Path $Root "temp\install-temp"
$env:TMP = $env:TEMP
$env:CONDA_REGISTER_ENVS = "false"
New-Item -ItemType Directory -Force $env:CONDA_PKGS_DIRS, $env:PIP_CACHE_DIR, $env:TEMP | Out-Null

if (-not (Test-Path $Python)) {
  $Conda = (Get-Command conda.exe -ErrorAction SilentlyContinue).Source
  if (-not $Conda) {
    $Candidate = "D:\working\conda3\Scripts\conda.exe"
    if (Test-Path $Candidate) { $Conda = $Candidate }
  }
  if (-not $Conda) { throw "conda.exe not found. Set CONDA_EXE or add conda to PATH." }

  Write-Host "Creating project conda prefix: $Prefix"
  & $Conda create -p $Prefix python=3.11 -y
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Standard conda create failed; retrying without plugins/classic solver..."
    & $Conda --no-plugins create -p $Prefix python=3.11 -y --solver classic
  }
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Python)) {
    throw "Could not create the project conda environment."
  }
}

Write-Host "Using interpreter: $Python"
& $Python -c "import sys; print(sys.executable); print(sys.version)"

Write-Host "Installing Python dependencies into the project prefix..."
& $Python (Join-Path $PSScriptRoot "pip_workspace.py") install --disable-pip-version-check --no-input --no-warn-script-location -r (Join-Path $Root "requirements.txt")
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

& (Join-Path $PSScriptRoot "fetch_visual.ps1")

Write-Host ""
Write-Host "Installation complete. Nothing was installed with -g or --user."
& $Python (Join-Path $PSScriptRoot "verify_install.py")
