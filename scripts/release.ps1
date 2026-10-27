<#
.SYNOPSIS
  一键发布：打包便携版 → 生成一个带版本号的 zip → 创建 Gitee 发行版并上传附件。

.DESCRIPTION
  产物是一个目录（里面只有一个 DanmakuPet.exe）压成一个包：

      dist/release/DanmakuPet-<版本>-win64.zip

  解压 → 双击 DanmakuPet.exe 即可运行，删除目录即卸载。

  注意：Gitee 发行版单个附件上限 100 MB。正常构建约 98 MB，在限制内；
  如果某次依赖变大导致超限，可以加 -Split 拆成「主程序 + 桌宠素材」两个包。

.EXAMPLE
  $env:GITEE_TOKEN = "你的 Gitee 私人令牌"
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release.ps1 -Tag v0.1.0

.EXAMPLE
  # 只打包不上传，之后在网页上手动拖拽 zip
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release.ps1 -NoUpload

.EXAMPLE
  # 附件超过 100 MB 时拆成两个包
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release.ps1 -Split

.NOTES
  令牌在 Gitee → 设置 → 私人令牌 生成，勾选 projects 权限；用完可以撤销。
#>
param(
  [string]$Tag = "",
  [string]$Title = "",
  [string]$NotesFile = "",
  [string]$Token = $env:GITEE_TOKEN,
  [string]$Owner = "duruixuan",
  [string]$Repo = "danmaku-pet",
  [string]$Branch = "master",
  [string]$Python = "",
  [switch]$SkipBuild,
  [switch]$Prerelease,
  [switch]$NoUpload,
  [switch]$NoTag,
  [switch]$Split
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $Root ".conda\env\python.exe" }
$Portable = Join-Path $Root "dist\DanmakuPet"
$ReleaseDir = Join-Path $Root "dist\release"
$LimitBytes = 100MB

if (-not (Test-Path $Python)) { throw "找不到 Python：$Python，请用 -Python 指定，或先运行 scripts\install.ps1" }

# 1. 版本号 / 标题
Push-Location $Root
try {
  $Version = (& $Python -c "import danmaku_pet,sys;sys.stdout.write(danmaku_pet.__version__)").Trim()
} finally {
  Pop-Location
}
if (-not $Tag) { $Tag = "v$Version" }
if (-not $Title) { $Title = "弹幕小人 $Tag" }
Write-Host "版本：$Version    Tag：$Tag"

# 2. 打包
if (-not $SkipBuild) {
  Write-Host "开始打包便携版 …"
  & (Join-Path $PSScriptRoot "build_exe.ps1") -Clean -Python $Python
  if ($LASTEXITCODE -ne 0) { throw "build_exe.ps1 失败（退出码 $LASTEXITCODE）" }
}
if (-not (Test-Path (Join-Path $Portable "DanmakuPet.exe"))) {
  throw "找不到 $Portable\DanmakuPet.exe，先运行 scripts\build_exe.ps1"
}

# 3. 压缩包
New-Item -ItemType Directory -Force $ReleaseDir | Out-Null
Add-Type -AssemblyName System.IO.Compression.FileSystem

function New-PortableZip {
  param(
    [Parameter(Mandatory = $true)][string]$ZipPath,
    [Parameter(Mandatory = $true)][string]$BaseDir,
    [Parameter(Mandatory = $true)][scriptblock]$Include
  )
  if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
  $zip = [System.IO.Compression.ZipFile]::Open($ZipPath, "Create")
  try {
    Get-ChildItem $BaseDir -Recurse -File | ForEach-Object {
      $relative = $_.FullName.Substring($BaseDir.Length + 1)
      if (& $Include $relative) {
        $entry = $relative -replace "\\", "/"
        [void][System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
          $zip, $_.FullName, $entry, [System.IO.Compression.CompressionLevel]::Optimal)
      }
    }
  } finally {
    $zip.Dispose()
  }
}

# 运行期目录不打进发行包：它们由 exe 首次启动时自动创建，
# 打包时如果带着，会把日志、TTS 缓存、布局文件一起发出去。
# .env 必须排除：exe 旁边的 .env 可能存着用户自己的 API Key。
$RuntimePrefixes = @(".cache/", "temp/", "assets/", "data/")
$SecretFiles = @(".env", ".env.tmp")
function Test-PackageEntry {
  param([string]$Relative, [switch]$AssetsOnly, [switch]$ExcludeAssets)
  $normalized = $Relative -replace "\\", "/"
  $leaf = ($normalized -split "/")[-1]
  foreach ($secret in $SecretFiles) {
    if ($leaf -ieq $secret) { return $false }
  }
  foreach ($prefix in $RuntimePrefixes) {
    if ($normalized.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
  }
  $isAssets = $normalized.StartsWith("_internal/vendor/dafeiyu/package/assets/", [System.StringComparison]::OrdinalIgnoreCase)
  if ($AssetsOnly) { return $isAssets }
  if ($ExcludeAssets) { return -not $isAssets }
  return $true
}

if ($Split) {
  $CoreZip = Join-Path $ReleaseDir "DanmakuPet-$Version-win64.zip"
  $AssetsZip = Join-Path $ReleaseDir "DanmakuPet-$Version-win64-assets.zip"
  Write-Host "生成主程序包 …"
  New-PortableZip -ZipPath $CoreZip -BaseDir $Portable -Include {
    param($relative)
    Test-PackageEntry -Relative $relative -ExcludeAssets
  }
  Write-Host "生成桌宠素材包 …"
  New-PortableZip -ZipPath $AssetsZip -BaseDir $Portable -Include {
    param($relative)
    Test-PackageEntry -Relative $relative -AssetsOnly
  }
  $Zips = @($CoreZip, $AssetsZip)
} else {
  $FullZip = Join-Path $ReleaseDir "DanmakuPet-$Version-win64.zip"
  Write-Host "生成发行包 …"
  New-PortableZip -ZipPath $FullZip -BaseDir $Portable -Include {
    param($relative)
    Test-PackageEntry -Relative $relative
  }
  $Zips = @($FullZip)
}

foreach ($file in $Zips) {
  $size = (Get-Item $file).Length
  Write-Host ("  {0}  {1} MB" -f (Split-Path $file -Leaf), [math]::Round($size / 1MB, 1))
  if ($size -gt $LimitBytes) {
    throw "$file 超过 Gitee 单附件 100MB 限制，改用 -Split 拆包"
  }

  # 兜底检查：任何 .env / .env.tmp 都不允许出现在分发包里（可能含 API Key）。
  $archive = [System.IO.Compression.ZipFile]::OpenRead($file)
  try {
    $leaked = @($archive.Entries | Where-Object { $SecretFiles -contains $_.Name })
  } finally {
    $archive.Dispose()
  }
  if ($leaked.Count -gt 0) {
    Remove-Item $file -Force
    throw "$file 里发现了 .env（可能含密钥），已删除该包；请检查 $Portable 并删除 .env 后重试"
  }
}

# 4. 发行说明
if ($NotesFile -and (Test-Path $NotesFile)) {
  $Notes = Get-Content $NotesFile -Raw -Encoding UTF8
} elseif ($Split) {
  $Notes = @"
## 下载与安装

1. 解压 DanmakuPet-$Version-win64.zip 到任意目录（例如 D:\DanmakuPet）
2. 把 DanmakuPet-$Version-win64-assets.zip **解压到同一个目录**（合并 _internal\vendor\...\assets 桌宠素材）
3. 双击 DanmakuPet.exe

> 分两个包是因为 Gitee 单附件上限 100 MB。

## 首次运行

- 会在 exe 旁边生成 .env / data/ / assets/ / temp/ / .cache/
- DeepSeek Key 在网页右上角「设置」里填写，保存到同目录 .env
- 没有 Key 时自动使用本地模拟决策，界面会明确标注
- 从受限终端（低完整性/沙箱）启动时浏览器不会自动弹出，见 README「2.3.1」

## 说明

- 便携版，删除目录即卸载，不写注册表
- 内含 PySide6 / edge-tts / pygame（LGPL），许可见仓库 THIRD_PARTY_NOTICES.md
"@
} else {
  $Notes = @"
## 下载与安装

1. 解压 DanmakuPet-$Version-win64.zip 到任意目录（例如 D:\DanmakuPet）
2. 双击目录里的 DanmakuPet.exe

目录里只有一个 exe，其余是它需要的运行时（_internal）。

## 首次运行

- 会在 exe 旁边生成 .env / data/ / assets/ / temp/ / .cache/
- DeepSeek Key 在网页右上角「设置」里填写，保存到同目录 .env
- 没有 Key 时自动使用本地模拟决策，界面会明确标注
- 启动后自动用默认浏览器打开管理页面；从受限终端（低完整性/沙箱）启动时不会自动弹出，见 README「2.3.1」

## 说明

- 便携版，删除目录即卸载，不写注册表
- 内含 PySide6 / edge-tts / pygame（LGPL），许可见仓库 THIRD_PARTY_NOTICES.md
"@
}

# 5. 本地标签（发行版需要 tag）
Write-Host ""
if ($NoUpload) {
  Write-Host "已生成发行包（未上传）："
  $Zips | ForEach-Object { Write-Host "  $_" }
  Write-Host ""
  Write-Host "手动发布：仓库页 → 发行版 → 创建发行版，tag 填 $Tag，把上面的 zip 拖进附件。"
  exit 0
}

if (-not $NoTag) {
  Push-Location $Root
  try {
    git tag $Tag 2>$null
    $env:GIT_TERMINAL_PROMPT = "0"
    git push origin $Tag 2>&1 | Out-Null
  } catch {
    Write-Host "提示：推送 tag 失败（已跳过），发行版仍会尝试创建同名 tag"
  } finally {
    Pop-Location
  }
}

if (-not $Token) {
  throw "缺少 Gitee 私人令牌：请设置环境变量 GITEE_TOKEN 或传 -Token（Gitee → 设置 → 私人令牌，勾选 projects）"
}

# 6. 创建（或复用）发行版
$Api = "https://gitee.com/api/v5/repos/$Owner/$Repo"
$Payload = @{
  access_token     = $Token
  tag_name         = $Tag
  name             = $Title
  body             = $Notes
  target_commitish = $Branch
  prerelease       = [bool]$Prerelease
} | ConvertTo-Json -Depth 3

try {
  Write-Host "创建发行版 $Tag …"
  $Release = Invoke-RestMethod -Uri "$Api/releases" -Method Post `
    -ContentType "application/json; charset=utf-8" `
    -Body ([System.Text.Encoding]::UTF8.GetBytes($Payload))
} catch {
  Write-Host "创建失败（可能已存在），尝试复用同名发行版：$($_.Exception.Message)"
  $Release = Invoke-RestMethod -Uri "$Api/releases/tags/$Tag`?access_token=$Token" -Method Get
}
if (-not $Release.id) { throw "没有拿到发行版 id，响应：$($Release | Out-String)" }
Write-Host "发行版 id：$($Release.id)"

# 7. 上传附件（curl 走 multipart，避免 PowerShell 5.1 的 -Form 缺失）
$Curl = (Get-Command curl.exe -ErrorAction SilentlyContinue).Source
if (-not $Curl) { throw "找不到 curl.exe（Windows 10+ 自带），请改用 -NoUpload 手动上传" }

foreach ($zip in $Zips) {
  Write-Host "上传 $(Split-Path $zip -Leaf) …（跨境链路较慢，单包最多等 30 分钟）"
  $response = & $Curl -sS --connect-timeout 30 --max-time 1800 --retry 3 --retry-delay 15 `
    -X POST "$Api/releases/$($Release.id)/attach_files" `
    -F "access_token=$Token" -F "file=@$zip"
  if ($response -notmatch "browser_download_url|download_url") {
    throw "上传失败：$(($response | Out-String).Trim())  提示：若提示超出大小限制，加 -Split 拆包后重试"
  }
  Write-Host "  完成"
}

Write-Host ""
Write-Host "发布完成：https://gitee.com/$Owner/$Repo/releases/tag/$Tag"
