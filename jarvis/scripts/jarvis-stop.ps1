# Stop the JARVIS local services started by jarvis-start.ps1 (only those; the console, the kernel and the laptop node are not touched). Windows.
param([string]$Venv = "D:\jarvis\tools\agent-venv", [switch]$Face)
$stopped = 0
function StopMatching($label, $filter, [switch]$Tree) {
  foreach ($p in (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and (& $filter $_) })) {
    try {
      if ($Tree) { & taskkill.exe /PID $p.ProcessId /T /F | Out-Null } else { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop }
      "stopped $label pid " + $p.ProcessId; $script:stopped++
    } catch { "could not stop $label pid " + $p.ProcessId }
  }
}
StopMatching "face tool server" { param($p) $p.CommandLine -like "*jarvis.bridge.face_tools*" }
StopMatching "watch runner"     { param($p) $p.CommandLine -match 'jarvis\.muse\.runner' }
StopMatching "speech server"    { param($p) $p.CommandLine -like "*local_speech_server.py*" }
StopMatching "privacy gateway"  { param($p) $p.CommandLine -like "*privacy*gateway.py*" }
foreach ($p in (Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "D:\jarvis\tools\llama-swap\*" -or $_.ExecutablePath -like "D:\jarvis\tools\llama.cpp\*llama-server.exe" })) {
  try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; "stopped local model process pid " + $p.ProcessId; $stopped++ } catch { }
}
if ($Face) { foreach ($p in (Get-Process -Name "JARVIS" -ErrorAction SilentlyContinue)) { try { Stop-Process -Id $p.Id -Force; "stopped the face pid " + $p.Id; $stopped++ } catch { } } }
if ($stopped -eq 0) { "no JARVIS local services running" }
