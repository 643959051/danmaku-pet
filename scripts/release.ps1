<#
.SYNOPSIS
  一键发布：打包便携版 → 生成发行包 → 创建 Gitee 发行版并上传附件。

.DESCRIPTION
  为什么是两个包：Gitee 发行版单个附件上限 100 MB，完整便携包压缩后约 118 MB。
  所以拆成「主程序 + 桌宠素材」两个 zip，都解压到同一个文件夹即可合并：

      DanmakuPet-<版本>-win64.zip          主程序（exe + 运行时）
      DanmakuPet-<版本>-win64-assets.zip   桌宠素材（_internal/vendor/.../assets）

.EXAMPLE
  $env:GITEE_TOKEN = "你的 Gitee 私人令牌"
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release.ps1 -Tag v0.1.0

.EXAMPLE
  # 只打包不上传，之后在网页上手动拖拽两个 zip
  powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\release.ps1 -NoUpload

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
  [switch]$SkipBuild,
  [switch]$Prerelease,
  [switch]$NoUpload
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root ".conda\env\python.exe"
$Portable = Join-Path $Root "dist\DanmakuPet"
$ReleaseDir = Join-Path $Root "dist\release"
$AssetsRelative = "_internal\vendor\dafeiyu\package\assets"

if (-not (Test-Path $Python)) { throw "找不到项目 Python：$Python，请先运行 scripts\install.ps1" }

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
  & (Join-Path $PSScriptRoot "build_exe.ps1") -Clean
  if ($LASTEXITCODE -ne 0) { throw "build_exe.ps1 失败（退出码 $LASTEXITCODE）" }
}
if (-not (Test-Path (Join-Path $Portable "DanmakuPet.exe"))) {
  throw "找不到 $Portable\DanmakuPet.exe，先运行 scripts\build_exe.ps1"
}

# 3. 生成两个 zip
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

$CoreZip = Join-Path $ReleaseDir "DanmakuPet-$Version-win64.zip"
$AssetsZip = Join-Path $ReleaseDir "DanmakuPet-$Version-win64-assets.zip"

Write-Host "生成主程序包 …"
New-PortableZip -ZipPath $CoreZip -BaseDir $Portable -Include {
  param($relative)
  -not $relative.StartsWith($AssetsRelative, [System.StringComparison]::OrdinalIgnoreCase)
}

Write-Host "生成桌宠素材包 …"
New-PortableZip -ZipPath $AssetsZip -BaseDir $Portable -Include {
  param($relative)
  $relative.StartsWith($AssetsRelative, [System.StringComparison]::OrdinalIgnoreCase)
}

$LimitMb = 100
foreach ($file in @($CoreZip, $AssetsZip)) {
  $mb = [math]::Round((Get-Item $file).Length / 1MB, 1)
  Write-Host ("  {0}  {1} MB" -f (Split-Path $file -Leaf), $mb)
  if ($mb -gt $LimitMb) { throw "$file 超过 Gitee 单附件 ${LimitMb}MB 限制" }
}

# 4. 发行说明
if ($NotesFile -and (Test-Path $NotesFile)) {
  $Notes = Get-Content $NotesFile -Raw -Encoding UTF8
} else {
  $Notes = @"
## 下载与安装

1. 解压 DanmakuPet-$Version-win64.zip 到任意目录（例如 D:\DanmakuPet）
2. 把 DanmakuPet-$Version-win64-assets.zip **解压到同一个目录**（合并 _internal\vendor\...\assets 桌宠素材）
3. 双击 DanmakuPet.exe

> 分两个包是因为 Gitee 单附件上限 100 MB，完整便携包压缩后约 118 MB。

## 首次运行

- 会在 exe 旁边生成 .env / data/ / assets/ / temp/ / .cache/
- DeepSeek Key 在网页右上角「设置」里填写，保存到同目录 .env
- 没有 Key 时自动使用本地模拟决策，界面会明确标注
- 从受限终端（低完整性/沙箱）启动时浏览器不会自动弹出，见 README「2.3.1」

## 说明

- 便携版，删除目录即卸载，不写注册表
- 内含 PySide6 / edge-tts / pygame（LGPL），许可见仓库 THIRD_PARTY_NOTICES.md
"@
}

# 5. 本地标签（发行版需要 tag）
Write-Host ""
if ($NoUpload) {
  Write-Host "已生成发行包（未上传）："
  Write-Host "  $CoreZip"
  Write-Host "  $AssetsZip"
  Write-Host ""
  Write-Host "手动发布：仓库页 → 发行版 → 创建发行版，tag 填 $Tag，把两个 zip 拖进附件。"
  exit 0
}

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

foreach ($zip in @($CoreZip, $AssetsZip)) {
  Write-Host "上传 $(Split-Path $zip -Leaf) …"
  $response = & $Curl -sS -X POST "$Api/releases/$($Release.id)/attach_files" `
    -F "access_token=$Token" -F "file=@$zip"
  if ($response -notmatch "browser_download_url|download_url") {
    throw "上传失败：$(($response | Out-String).Trim())"
  }
  Write-Host "  完成"
}

Write-Host ""
Write-Host "发布完成：https://gitee.com/$Owner/$Repo/releases/tag/$Tag"
