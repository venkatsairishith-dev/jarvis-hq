# Starts the JARVIS Console (Windows). It listens on 127.0.0.1 only; open http://127.0.0.1:8799/ in Edge.
# Needs: D:\jarvis-secrets\kernel\owner.token (issued once at the kernel console), the built UI (hq\ui\dist) and, for voice,
# D:\jarvis\models\voice and D:\jarvis\tools\piper-venv. Logs: D:\jarvis\cache\console.out.log / console.err.log.
$ErrorActionPreference = 'Stop'
$hq = Split-Path -Parent $PSScriptRoot
$py = 'D:\jarvis\tools\jarvis-venv\Scripts\pythonw.exe'
if (-not (Test-Path $py)) { throw "console Python not found: $py" }
if (-not (Test-Path "$hq\ui\dist\index.html")) { Write-Warning "The UI is not built: run 'npm install; npm run build' in $hq\ui" }
$env:PYTHONPATH = "$hq\console"
$env:HF_HUB_OFFLINE = '1'
Start-Process -FilePath $py -ArgumentList '-m', 'jarvis_console' -WorkingDirectory "$hq\console" -WindowStyle Hidden `
  -RedirectStandardOutput 'D:\jarvis\cache\console.out.log' -RedirectStandardError 'D:\jarvis\cache\console.err.log'
Start-Sleep 3
try { (Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8799/api/health' -TimeoutSec 8).StatusCode | ForEach-Object { "console answering: HTTP $_" } }
catch { "console did not answer yet; see D:\jarvis\cache\console.err.log" }
