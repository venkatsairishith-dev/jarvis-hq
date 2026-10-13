# Stop the N.E.K.O servers started from the neko Python environment (only those; nothing else is touched). Windows.
param([string]$Venv = "D:\jarvis\tools\neko-venv")
$names = @('app.main_server', 'app.memory_server', 'app.agent_server')
$procs = Get-CimInstance Win32_Process | Where-Object {
  $cl = $_.CommandLine
  if (-not $cl) { return $false }
  $inVenv = ($cl -like "*$Venv*") -or ($_.ExecutablePath -like "$Venv*")
  $isNeko = $false
  foreach ($n in $names) { if ($cl -like "*-m $n*") { $isNeko = $true } }
  $inVenv -and $isNeko
}
foreach ($p in $procs) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "stopped pid " + $p.ProcessId } catch { "could not stop pid " + $p.ProcessId } }
if (-not $procs) { "no N.E.K.O server processes running" }
