# Make the desktop widget easy to open, and optionally open it at every login. Windows, current user only.
#
#   install-widget.ps1              create the shortcut D:\jarvis\JARVIS Widget.lnk
#   install-widget.ps1 -Autostart  also open the widget about a minute after every login (scheduled task "JarvisWidget")
#   install-widget.ps1 -Remove      remove the shortcut and the task
param([switch]$Autostart, [switch]$Remove)
$name = "JarvisWidget"
$script = (Resolve-Path (Join-Path $PSScriptRoot "open-widget.ps1")).Path
$ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$lnkPath = "D:\jarvis\JARVIS Widget.lnk"
if ($Remove) {
  if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; "removed task $name" }
  if (Test-Path $lnkPath) { Remove-Item $lnkPath; "removed $lnkPath" }
  return
}
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
$sh = New-Object -ComObject WScript.Shell
$l = $sh.CreateShortcut($lnkPath)
$l.TargetPath = $ps
$l.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
$l.WorkingDirectory = "D:\jarvis"
$l.IconLocation = "$edge,0"
$l.Description = "Small always-on-top JARVIS status and quick-task widget"
$l.WindowStyle = 7
$l.Save()
"created $lnkPath"
if ($Autostart) {
  $user = "$env:USERDOMAIN\$env:USERNAME"
  $action = New-ScheduledTaskAction -Execute $ps -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
  $trigger.Delay = "PT60S"
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
    -Description "Opens the JARVIS desktop widget after login (open-widget.ps1)." -Force | Out-Null
  "installed task $name (opens the widget about a minute after login; remove with: install-widget.ps1 -Remove)"
}
