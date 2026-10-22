# Run the pre-flight and then the 10-task desktop baseline IN THIS WINDOW, where you can watch it. Nothing runs hidden.
#   test-visible.ps1                 pre-flight (about a minute), then the baseline
#   test-visible.ps1 -Only T01,T03   only some baseline tasks
#   test-visible.ps1 -SkipLiveCheck  a shorter pre-flight (scheduled tasks, wake listener, disk)
# The window stays open at the end; a copy of everything printed is kept in D:\jarvis\outputs\visible-tests (nothing is written outside D:).
param([string]$Only = "", [switch]$SkipLiveCheck)
. (Join-Path $PSScriptRoot "d-env.ps1")
$hq = Split-Path -Parent $PSScriptRoot
$py = 'D:\jarvis\tools\neko-venv\Scripts\python.exe'
$Host.UI.RawUI.WindowTitle = "JARVIS visible test"
$log = 'D:\jarvis\outputs\visible-tests'
New-Item -ItemType Directory -Force $log | Out-Null
Start-Transcript -Path (Join-Path $log ("test-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))) | Out-Null
Write-Host "JARVIS visible test: watch this screen. Windows will open and close by themselves." -ForegroundColor Cyan
if (-not (Test-Path $py)) { Write-Host "RED: the N.E.K.O Python is missing: $py" -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
$pre = @(); if ($SkipLiveCheck) { $pre += '--no-live-check' }
& $py (Join-Path $PSScriptRoot "preflight.py") @pre
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nNOT READY: the red lines above would show up as errors. Nothing was started." -ForegroundColor Red
    Stop-Transcript | Out-Null; Read-Host "Press Enter to close"; exit 1
}
Write-Host "`nPre-flight is all green. Starting the 10 tasks in 5 seconds; do not click while a task runs." -ForegroundColor Green
Start-Sleep 5
$b = @(); if ($Only) { $b += @('--only', $Only) }
& $py (Join-Path $hq "evals\neko95\task_baseline.py") @b
$code = $LASTEXITCODE
Write-Host ("`nFinished (exit {0}). The numbers above are the truth: pass, fail and the reason for each." -f $code) -ForegroundColor Cyan
Stop-Transcript | Out-Null
Read-Host "Press Enter to close this window"
exit $code
