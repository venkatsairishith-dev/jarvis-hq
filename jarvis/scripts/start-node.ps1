# Starts the laptop node (jarvis_node) with every temp and cache folder on D: (owner rule 2026-10-21). Used by the scheduled task JarvisNode.
. (Join-Path $PSScriptRoot "d-env.ps1")
$hq = Split-Path -Parent $PSScriptRoot
# A node that is already running is stopped first (with its UI Automation worker): Stop-ScheduledTask ends only this wrapper, so a restart used to leave the old node
# polling for jobs beside the new one (laptop 2026-10-27: three nodes, the oldest with code from hours earlier). The node also refuses to run twice in one session.
foreach ($p in (Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -and $_.CommandLine -match 'jarvis_node\s+run' -and $_.ProcessId -ne $PID })) {
  & taskkill.exe /PID $p.ProcessId /T /F 2>&1 | Out-Null
}
Start-Sleep -Milliseconds 500
Start-Process -FilePath 'D:\jarvis\tools\browser-venv\Scripts\pythonw.exe' -ArgumentList '-m', 'jarvis_node', 'run' -WorkingDirectory "$hq\node" -WindowStyle Hidden
