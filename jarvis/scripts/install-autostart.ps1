# Start the JARVIS local services (privacy gateway, speech, tool server, console) and the face at Windows login.
# Windows, current user only: no administrator rights, nothing outside the user's own scheduled tasks.
#
#   install-autostart.ps1            # create or update the task "JarvisStack" (runs jarvis-start.ps1 at login)
#   install-autostart.ps1 -Remove    # delete that one task
#   install-autostart.ps1 -Status    # show the task and what it runs
#
# jarvis-start.ps1 is idempotent (a server that already listens is left alone), so a manual start and the login start do not clash.
# The existing JarvisNode task (the laptop node) is not touched.
param([switch]$Remove, [switch]$Status)
$name = "JarvisStack"
$start = (Resolve-Path (Join-Path $PSScriptRoot "jarvis-start.ps1")).Path

if ($Status) {
  $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if (-not $t) { "not installed"; return }
  $a = $t.Actions[0]
  "task    : $name  state=$($t.State)"
  "runs    : $($a.Execute) $($a.Arguments)"
  "trigger : at logon of $($t.Principal.UserId)"
  return
}
if ($Remove) {
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false
    "removed task $name"
  } else { "task $name was not installed" }
  return
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
  -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $start)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$trigger.Delay = "PT30S"                       # let the desktop and network settle first
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
  -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
  -Description "Starts JARVIS's local services and the face at login (jarvis-start.ps1)." -Force | Out-Null
"installed task $name (at login of $user, after 30 s): $start"
