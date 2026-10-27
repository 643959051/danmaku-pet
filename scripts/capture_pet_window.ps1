param(
  [Parameter(Mandatory = $true)][int]$ProcessId,
  [Parameter(Mandatory = $true)][string]$Out
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WinCapture {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr extra);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
  public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
"@
$rects = New-Object System.Collections.Generic.List[object]
$callback = [WinCapture+EnumProc]{
  param($hWnd, $lParam)
  [uint32]$procId = 0
  [WinCapture]::GetWindowThreadProcessId($hWnd, [ref]$procId) | Out-Null
  if ($procId -eq $ProcessId -and [WinCapture]::IsWindowVisible($hWnd)) {
    $rect = New-Object WinCapture+RECT
    if ([WinCapture]::GetWindowRect($hWnd, [ref]$rect)) {
      $w = $rect.Right - $rect.Left
      $h = $rect.Bottom - $rect.Top
      if ($w -gt 10 -and $h -gt 10) {
        $rects.Add([pscustomobject]@{ Handle=$hWnd; Left=$rect.Left; Top=$rect.Top; Width=$w; Height=$h })
      }
    }
  }
  return $true
}
[WinCapture]::EnumWindows($callback, [IntPtr]::Zero) | Out-Null
if ($rects.Count -eq 0) { throw "No visible window found for pid $ProcessId" }
$win = $rects | Sort-Object { $_.Width * $_.Height } -Descending | Select-Object -First 1
$bitmap = New-Object System.Drawing.Bitmap $win.Width, $win.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($win.Left, $win.Top, 0, 0, $bitmap.Size)
$path = [System.IO.Path]::GetFullPath($Out)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($path)) | Out-Null
$bitmap.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
$graphics.Dispose()
$bitmap.Dispose()
Write-Host ("saved {0}x{1} at {2}" -f $win.Width, $win.Height, $path)
