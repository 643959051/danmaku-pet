<#
.SYNOPSIS
  把「弹幕小人」打包成便携版 exe（onedir）。

.EXAMPLE
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1 -Clean

.EXAMPLE
  # 重新打包并压缩成便于分发的 zip
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1 -Clean -Zip

.EXAMPLE
  # CI 用：显式指定解释器，不依赖 .conda
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build_exe.ps1 -Clean -Python C:\hostedtoolcache\python\3.11\python.exe
#>
param(
  [switch]$Clean,
  [switch]$Zip,
  [string]$Python = ""
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $Root ".conda\env\python.exe" }
$Spec = Join-Path $Root "packaging\DanmakuPet.spec"
$Dist = Join-Path $Root "dist"
$Build = Join-Path $Root "build"
$Output = Join-Path $Dist "DanmakuPet"

if (-not (Test-Path $Python)) {
  throw "找不到项目 Python：$Python。请先运行 scripts\install.ps1，或用 -Python 指定解释器。"
}
if (-not (Test-Path $Spec)) {
  throw "找不到打包脚本：$Spec"
}

# 干净克隆里没有 data/persona.json（被 gitignore）。客户端不再需要人设，
# 人设只存在于云端；这里保留 data 目录只是为了让旧的便携版目录结构兼容。
# 所有缓存/临时文件都留在工作区内，和 run.ps1 保持一致。
$env:TEMP = Join-Path $Root "temp\build-temp"
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $Root ".cache\pip"
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
New-Item -ItemType Directory -Force $env:TEMP, $env:PIP_CACHE_DIR | Out-Null

# 1. 构建工具（只装进项目环境，不碰全局 Python）
& $Python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
  Write-Host "安装 PyInstaller 到项目环境 …"
  & $Python -m pip install --timeout 30 "pyinstaller>=6,<7"
  if ($LASTEXITCODE -ne 0) { throw "PyInstaller 安装失败" }
}

# 2. 清理旧产物
if ($Clean) {
  foreach ($path in @($Build, $Dist)) {
    if (Test-Path $path) { Remove-Item $path -Recurse -Force }
  }
}

# 3. 打包
Write-Host "开始打包（首次会慢一些，PySide6 体积大）…"
& $Python -m PyInstaller $Spec --noconfirm --clean --distpath $Dist --workpath $Build
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

# 4. 便携目录里放一份客户端配置样例（不含任何密钥）
$Example = Join-Path $Root ".env.example"
if (Test-Path $Example) {
  Copy-Item $Example (Join-Path $Output ".env.example") -Force
}

$Exe = Join-Path $Output "DanmakuPet.exe"
if (-not (Test-Path $Exe)) { throw "打包结束但没有找到 $Exe" }

$SizeMb = [math]::Round(((Get-ChildItem $Output -Recurse -File | Measure-Object Length -Sum).Sum / 1MB), 1)
Write-Host ""
Write-Host "打包完成：$Exe"
Write-Host "目录大小：$SizeMb MB"
Write-Host "双击 exe 即可启动；首次会生成 temp / assets 等运行目录。"
Write-Host "把 .env.example 复制为 .env 填写 ROOM_ID / CLOUD_URL / CLIENT_TOKEN；本包不含 DeepSeek Key。"

if ($Zip) {
  Push-Location $Root
  try {
    $Version = (& $Python -c "import danmaku_pet,sys;sys.stdout.write(danmaku_pet.__version__)").Trim()
  } finally {
    Pop-Location
  }
  $Archive = Join-Path $Dist "DanmakuPet-$Version-win64.zip"
  if (Test-Path $Archive) { Remove-Item $Archive -Force }

  # 只打构建产物；.cache / temp / assets / data 是运行时生成的，不能进包。
  # .env 必须排除：exe 旁边的 .env 可能存着用户自己的 API Key。
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $Skip = @(".cache/", "temp/", "assets/", "data/")
  $SecretFiles = @(".env", ".env.tmp")
  $zip = [System.IO.Compression.ZipFile]::Open($Archive, "Create")
  try {
    Get-ChildItem $Output -Recurse -File | ForEach-Object {
      $relative = $_.FullName.Substring($Output.Length + 1)
      $normalized = $relative -replace "\\", "/"
      $leaf = ($normalized -split "/")[-1]
      $skip = $SecretFiles -contains $leaf
      if (-not $skip) {
        foreach ($prefix in $Skip) {
          if ($normalized.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { $skip = $true; break }
        }
      }
      if (-not $skip) {
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
          $zip, $_.FullName, $normalized, [System.IO.Compression.CompressionLevel]::Optimal)
      }
    }
  } finally {
    $zip.Dispose()
  }
  $ZipMb = [math]::Round(((Get-Item $Archive).Length / 1MB), 1)
  Write-Host "已压缩：$Archive（$ZipMb MB）"
}

exit 0

