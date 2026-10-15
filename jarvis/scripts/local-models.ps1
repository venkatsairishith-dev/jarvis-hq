# Start (or stop) JARVIS's local models: llama-swap on 127.0.0.1:8080 with jarvis\local_models\llama-swap.yaml. Windows.
#
#   local-models.ps1          start if nothing listens on 8080 (no model is loaded until one is asked for)
#   local-models.ps1 -Stop    stop llama-swap and any llama-server it started
#
# Endpoint: http://127.0.0.1:8080/v1 (OpenAI-compatible; model = general | code | math | router). Loopback only.
# These models see your real text: they never leave the PC. Dahl only ever sees text passed through the privacy gateway.
param([switch]$Stop)
$exe = "D:\jarvis\tools\llama-swap\v261\llama-swap.exe"
$cfg = (Resolve-Path (Join-Path $PSScriptRoot "..\local_models\llama-swap.yaml")).Path
$logs = "D:\jarvis\N.E.K.O-data\logs"
function Listening($port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) }
if ($Stop) {
  $n = 0
  foreach ($p in (Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like "D:\jarvis\tools\llama-swap\*" -or $_.ExecutablePath -like "D:\jarvis\tools\llama.cpp\*llama-server.exe" })) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $n++ } catch { }
  }
  "stopped $n local-model process(es)"
  return
}
if (Listening 8080) { "local models: something already listens on 8080"; return }
if (-not (Test-Path $exe)) { throw "llama-swap not found: $exe" }
New-Item -ItemType Directory $logs -Force | Out-Null
Start-Process $exe -ArgumentList "--config", "`"$cfg`"", "--listen", "127.0.0.1:8080" -WindowStyle Hidden `
  -RedirectStandardOutput "$logs\local_models.out.log" -RedirectStandardError "$logs\local_models.err.log" | Out-Null
foreach ($i in 1..20) { if (Listening 8080) { break }; Start-Sleep 1 }
"local models 8080: " + (Listening 8080) + "   (models load on first use, unload when idle)"
