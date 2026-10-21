# Starts the laptop node (jarvis_node) with every temp and cache folder on D: (owner rule 2026-10-21). Used by the scheduled task JarvisNode.
. (Join-Path $PSScriptRoot "d-env.ps1")
$hq = Split-Path -Parent $PSScriptRoot
Start-Process -FilePath 'D:\jarvis\tools\browser-venv\Scripts\pythonw.exe' -ArgumentList '-m', 'jarvis_node', 'run' -WorkingDirectory "$hq\node" -WindowStyle Hidden
