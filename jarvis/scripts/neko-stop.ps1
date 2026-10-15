# Stop the N.E.K.O servers started from the neko Python environment (only those; nothing else is touched). Windows.
# Upstream's launcher.py is the supervisor: ending it and its process tree stops memory, main and agent together. The older
# per-module processes (python -m app.main_server ...) are also handled, and so is the local speech server.
param([string]$Venv = "D:\jarvis\tools\neko-venv")
$names = @('app.main_server', 'app.memory_server', 'app.agent_server')
$all = Get-CimInstance Win32_Process
function InVenv($p) { $cl = $p.CommandLine; ($cl -like "*$Venv*") -or ($p.ExecutablePath -like "$Venv*") }
$stopped = 0
# 1. the launcher (and, with /T, everything it started)
foreach ($p in ($all | Where-Object { $_.CommandLine -and (InVenv $_) -and ($_.CommandLine -like "*launcher.py*") })) {
  & taskkill.exe /PID $p.ProcessId /T /F | Out-Null
  "stopped launcher pid " + $p.ProcessId + " and its servers"; $stopped++
}
# 2. anything left from a start without the launcher
$left = Get-CimInstance Win32_Process | Where-Object {
  $cl = $_.CommandLine
  if (-not $cl) { return $false }
  $isNeko = $false
  foreach ($n in $names) { if ($cl -like "*-m $n*") { $isNeko = $true } }
  (InVenv $_) -and $isNeko
}
foreach ($p in $left) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "stopped pid " + $p.ProcessId; $stopped++ } catch { "could not stop pid " + $p.ProcessId } }
# 3. the local speech server
$speech = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*local_speech_server.py*" }
foreach ($p in $speech) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "stopped speech server pid " + $p.ProcessId; $stopped++ } catch { } }
# 4. the desktop widget's backend
$widget = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*widget_server.py*" }
foreach ($p in $widget) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "stopped widget server pid " + $p.ProcessId; $stopped++ } catch { } }
# 5. the privacy gateway in front of Dahl
$priv = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like "*privacy*gateway.py*" }
foreach ($p in $priv) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "stopped privacy gateway pid " + $p.ProcessId; $stopped++ } catch { } }
# 6. the local specialist models (llama-swap and the llama-server it started)
$local = Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "D:\jarvis\tools\llama-swap\*" -or $_.ExecutablePath -like "D:\jarvis\tools\llama.cpp\*llama-server.exe" }
foreach ($p in $local) { try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "stopped local model process pid " + $p.ProcessId; $stopped++ } catch { } }
if ($stopped -eq 0) { "no N.E.K.O server processes running" }
