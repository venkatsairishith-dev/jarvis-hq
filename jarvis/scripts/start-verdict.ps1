# Starts the OpenJev "Verdict" decision service on 127.0.0.1:8091 (CPU). JARVIS's local_team and the privacy gateway use it as the Jev decision
# model (JARVIS_JEV_URL defaults to http://127.0.0.1:8091/v1/systemone). It was a WSL service; the WSL lab disk is gone, so this runs it natively.
# Weights (heman10x/rlcd-modernbert-151m, about 600 MB) download once into D:\jarvis\cache\hf. Loopback only, no key.
#   start-verdict.ps1           start if nothing listens on 8091
. (Join-Path $PSScriptRoot "d-env.ps1")
if ((Test-NetConnection 127.0.0.1 -Port 8091 -WarningAction SilentlyContinue).TcpTestSucceeded) { "verdict already listening on 8091"; return }
$app = 'D:\jarvis-apps\openjev'
$py = "$app\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "OpenJev venv not found: $py" }
$env:OPENJEV_BACKEND = 'verdict'; $env:OPENJEV_HOST = '127.0.0.1'; $env:OPENJEV_PORT = '8091'; $env:OPENJEV_DEVICE = 'cpu'
New-Item -ItemType Directory -Force "$app\logs" | Out-Null
Start-Process -FilePath $py -ArgumentList '-m', 'openjev' -WorkingDirectory $app -WindowStyle Hidden `
  -RedirectStandardOutput "$app\logs\verdict.out.log" -RedirectStandardError "$app\logs\verdict.err.log"
"verdict starting on 127.0.0.1:8091 (first start downloads the weights)"
