# Write the daily health report (daily_report.py) every morning. Windows, current user only: no administrator rights.
#
#   install-daily-report.ps1            create or update the task "JarvisDailyReport" (09:00 every day; runs when the laptop wakes if it missed it)
#   install-daily-report.ps1 -Remove    delete that one task
#   install-daily-report.ps1 -Status    show the task and the newest report
param([switch]$Remove, [switch]$Status, [string]$At = "09:00")
$name = "JarvisDailyReport"
$py = "D:\jarvis\tools\agent-venv\Scripts\python.exe"
$script = (Resolve-Path (Join-Path $PSScriptRoot "daily_report.py")).Path
$data = "D:\jarvis\data"
if ($Status) {
  $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if (-not $t) { "not installed"; return }
  $i = Get-ScheduledTaskInfo -TaskName $name
  "task   : $name state=$($t.State) last run=$($i.LastRunTime) result=$($i.LastTaskResult) next=$($i.NextRunTime)"
  $r = Get-ChildItem "$data\logs\daily_report_*.md" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($r) { "newest : $($r.FullName)" }
  return
}
if ($Remove) {
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; "removed task $name" } else { "task $name was not installed" }
  return
}
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "& ''{0}'' ''{1}''"' -f $py, $script)
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "Writes a one-page health report for JARVIS (daily_report.py). No model calls." -Force | Out-Null
"installed task $name (daily at $At): $script"
