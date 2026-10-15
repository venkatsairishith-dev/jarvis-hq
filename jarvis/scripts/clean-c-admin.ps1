# One-off C: cleanup that needs administrator rights. Windows. Run elevated (it asks for UAC when started normally).
#
#   clean-c-admin.ps1                    the three safe steps below, logged to D:\jarvis-apps\tmp\clean_c_admin.log
#   clean-c-admin.ps1 -HibernateOff      also turn hibernation off (frees hiberfil.sys, ~13 GB here; the laptop then shuts
#                                        down instead of hibernating on critical battery, and Fast Startup is off).
#                                        Undo any time with:  powercfg /hibernate on
#
# Steps: (1) delete one orphaned folder in the Recycle Bin (an old installer, deleted 2026-08-22, which Windows can no longer
# show or empty), (2) Windows' own component-store cleanup (DISM StartComponentCleanup: removes superseded update files),
# (3) empty the Windows Update download cache (stops and restarts the update service around it).
# It does not touch AlbionOnline, Chrome, your documents or any program.
param([switch]$HibernateOff)
$log = "D:\jarvis-apps\tmp\clean_c_admin.log"
New-Item -ItemType Directory (Split-Path $log) -Force | Out-Null
function Log($m) { $line = "{0}  {1}" -f (Get-Date -Format "HH:mm:ss"), $m; Add-Content -Path $log -Value $line -Encoding UTF8; Write-Host $line }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  $args2 = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"") + $(if ($HibernateOff) { @("-HibernateOff") } else { @() })
  Start-Process powershell -Verb RunAs -ArgumentList $args2
  "asked Windows for administrator rights (UAC); the cleanup runs in that window and logs to $log"
  return
}
$start = (Get-PSDrive C).Free
Log "start: C: free $([math]::Round($start/1GB,1)) GB (elevated)"

$orphan = 'C:\$Recycle.Bin\S-1-5-21-460663378-2445816101-3938296602-1001\$R5Z3H3L'
if (Test-Path -LiteralPath $orphan) {
  $b = (Get-PSDrive C).Free
  takeown /f "$orphan" /r /d y | Out-Null
  icacls "$orphan" /grant "*S-1-5-32-544:F" /t /c /q | Out-Null
  Remove-Item -LiteralPath $orphan -Recurse -Force -ErrorAction SilentlyContinue
  Log ("1. orphaned Recycle Bin folder: " + $(if (Test-Path -LiteralPath $orphan) { "could not remove" } else { "removed" }) + ", freed $([math]::Round(((Get-PSDrive C).Free - $b)/1GB,2)) GB")
} else { Log "1. orphaned Recycle Bin folder: already gone" }

$b = (Get-PSDrive C).Free
Log "2. component store cleanup (DISM, can take several minutes)..."
$d = Start-Process dism.exe -ArgumentList "/Online", "/Cleanup-Image", "/StartComponentCleanup" -Wait -PassThru -WindowStyle Hidden
Log "2. DISM exit $($d.ExitCode), freed $([math]::Round(((Get-PSDrive C).Free - $b)/1GB,2)) GB"

$b = (Get-PSDrive C).Free
try {
  Stop-Service wuauserv -Force -ErrorAction Stop
  Stop-Service bits -Force -ErrorAction SilentlyContinue
  Get-ChildItem "C:\Windows\SoftwareDistribution\Download" -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
} finally {
  Start-Service bits -ErrorAction SilentlyContinue
  Start-Service wuauserv -ErrorAction SilentlyContinue
}
Log "3. Windows Update download cache emptied, freed $([math]::Round(((Get-PSDrive C).Free - $b)/1GB,2)) GB (update service running again: $((Get-Service wuauserv).Status))"

if ($HibernateOff) {
  $b = (Get-PSDrive C).Free
  powercfg /hibernate off
  Log "4. hibernation turned off, freed $([math]::Round(((Get-PSDrive C).Free - $b)/1GB,2)) GB (undo: powercfg /hibernate on)"
}
Log "done: C: free $([math]::Round((Get-PSDrive C).Free/1GB,1)) GB, freed $([math]::Round(((Get-PSDrive C).Free - $start)/1GB,2)) GB in total"
