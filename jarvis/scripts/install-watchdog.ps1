# Run the self-healing watchdog (watchdog.py) every 10 minutes. Windows, current user only: no administrator rights.
#
#   install-watchdog.ps1            create or update the task "JarvisWatchdog"
#   install-watchdog.ps1 -Remove    delete that one task
#   install-watchdog.ps1 -Status    show the task and the watchdog's last verdict
#
# The watchdog checks the stack, starts whatever is down, never touches the JARVIS kernel and makes no model calls. Its log is
# <data>\logs\watchdog.log and its last verdict <data>\logs\watchdog_status.json.
param([switch]$Remove, [switch]$Status)
$name = "JarvisWatchdog"
$py = "D:\jarvis\tools\agent-venv\Scripts\python.exe"
$script = (Resolve-Path (Join-Path $PSScriptRoot "watchdog.py")).Path
$data = "D:\jarvis\data"

if ($Status) {
  $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if (-not $t) { "not installed"; return }
  $i = Get-ScheduledTaskInfo -TaskName $name
  "task    : $name  state=$($t.State)  last run=$($i.LastRunTime)  next=$($i.NextRunTime)"
  if (Test-Path "$data\logs\watchdog_status.json") { "verdict : " + (Get-Content "$data\logs\watchdog_status.json" -Raw | ConvertFrom-Json | ConvertTo-Json -Compress) }
  return
}
if ($Remove) {
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; "removed task $name" } else { "task $name was not installed" }
  return
}
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "& ''{0}'' ''{1}''"' -f $py, $script)
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 10)
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "Checks JARVIS's local services and the console every 10 minutes and restarts what is down (watchdog.py)." -Force | Out-Null
"installed task $name (every 10 minutes, first run in 2 minutes): $script"
