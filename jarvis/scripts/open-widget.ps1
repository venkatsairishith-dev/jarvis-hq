# Open the JARVIS desktop widget: a small always-on-top window in the bottom-right corner of the screen. Windows.
#
#   open-widget.ps1                  open it (or just bring the open one back on top and into the corner)
#   open-widget.ps1 -Width 400 -Height 360
#
# It is Edge in app mode with its own small profile (D:\jarvis-apps\widget-profile), pointed at the widget server on
# http://127.0.0.1:48940/. The window is pinned on top and placed with a plain window call; closing it stops nothing else.
param([int]$Width = 380, [int]$Height = 340, [int]$Margin = 16)
$ErrorActionPreference = 'Stop'
$py = "D:\jarvis\tools\neko-venv\Scripts\python.exe"
$server = (Resolve-Path (Join-Path $PSScriptRoot "..\widget\widget_server.py")).Path
function Listening($p) { [bool](Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue) }

if (-not (Listening 48940)) {
  $logs = "D:\jarvis\N.E.K.O-data\logs"
  New-Item -ItemType Directory $logs -Force | Out-Null
  Start-Process $py -ArgumentList $server, "--port", "48940" -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\widget.out.log" -RedirectStandardError "$logs\widget.err.log" | Out-Null
  foreach ($i in 1..15) { if (Listening 48940) { break }; Start-Sleep -Milliseconds 500 }
  if (-not (Listening 48940)) { throw "the widget server did not start (see $logs\widget.err.log)" }
}

Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class WidgetWin {
  [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
}
"@
Add-Type -AssemblyName System.Windows.Forms

function Find-Widget { Get-Process msedge -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -like 'JARVIS Widget*' -and $_.MainWindowHandle -ne 0 } | Select-Object -First 1 }
$w = Find-Widget
if (-not $w) {
  $edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
    Where-Object { Test-Path $_ } | Select-Object -First 1
  if (-not $edge) { throw "Microsoft Edge was not found" }
  $profile = "D:\jarvis-apps\widget-profile"
  New-Item -ItemType Directory $profile -Force | Out-Null
  Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
  Start-Process $edge -ArgumentList "--app=http://127.0.0.1:48940/", "--user-data-dir=$profile", "--window-size=$Width,$Height", "--no-first-run", "--no-default-browser-check"
  foreach ($i in 1..40) { Start-Sleep -Milliseconds 500; $w = Find-Widget; if ($w) { break } }
}
if (-not $w) { throw "the widget window did not appear" }

$area = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
$x = $area.Right - $Width - $Margin
$y = $area.Bottom - $Height - $Margin
$HWND_TOPMOST = [IntPtr](-1)
$SWP_SHOWWINDOW = 0x0040
[void][WidgetWin]::SetWindowPos($w.MainWindowHandle, $HWND_TOPMOST, $x, $y, $Width, $Height, $SWP_SHOWWINDOW)
"widget open at ($x, $y) size ${Width}x${Height}, always on top"
