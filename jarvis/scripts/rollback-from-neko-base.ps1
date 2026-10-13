# Put the live JARVIS services back on D:\jarvis\hq (branch jarvis-hq-legacy) from a backup made by cutover-to-neko-base.ps1. Windows.
#   powershell -File rollback-from-neko-base.ps1 -BackupDir D:\jarvis-apps\backups\tasks-pre-neko-<timestamp>
param([Parameter(Mandatory = $true)][string]$BackupDir)
$ErrorActionPreference = 'Stop'
foreach ($t in 'JarvisNode', 'JarvisLab') {
  $x = Join-Path $BackupDir "$t.xml"
  if (-not (Test-Path $x)) { throw "backup file not found: $x" }
}
foreach ($t in 'JarvisNode', 'JarvisLab') {
  Register-ScheduledTask -TaskName $t -Xml ([IO.File]::ReadAllText((Join-Path $BackupDir "$t.xml"))) -Force | Out-Null
  Write-Host "restored task $t"
}
Stop-ScheduledTask JarvisNode -ErrorAction SilentlyContinue
Start-Sleep 3
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*-m jarvis_node*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 2
Start-ScheduledTask JarvisNode
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*-m jarvis_console*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 3
& powershell -NoProfile -ExecutionPolicy Bypass -File 'D:\jarvis\hq\scripts\start-console.ps1' | Out-Null
Write-Host "rolled back: node task and console run from D:\jarvis\hq again"
