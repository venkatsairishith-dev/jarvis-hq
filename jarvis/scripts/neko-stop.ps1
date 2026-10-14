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
$speech = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*local_speech_server.py*" }
foreach ($p in $speech) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "stopped speech server pid " + $p.ProcessId } catch { } }
if (-not $procs -and -not $speech) { "no N.E.K.O server processes running" }
