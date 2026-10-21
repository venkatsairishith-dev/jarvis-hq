# Start (or stop) JARVIS's local models: llama-swap on 127.0.0.1:8080 with jarvis\local_models\llama-swap.yaml. Windows.
#
#   local-models.ps1          start if nothing listens on 8080 (no model is loaded until one is asked for)
#   local-models.ps1 -Stop    stop llama-swap and any llama-server it started
#
# Endpoint: http://127.0.0.1:8080/v1 (OpenAI-compatible; model = general | code | math | router). Loopback only.
# These models see your real text: they never leave the PC. Dahl only ever sees text passed through the privacy gateway.
#
# The model set follows the hardware profile chosen by jarvis\local_models\hwdetect.py (saved in <data>\neko\hardware.json;
# run it again after a hardware change, or set NEKO_MODEL_PROFILE). vram4 (this laptop) uses llama-swap.yaml; the other
# profiles use local_models\profiles\<profile>.yaml. remote_only starts no local models.
param([switch]$Stop, [string]$Profile = "")
. (Join-Path $PSScriptRoot "d-env.ps1")
$exe = "D:\jarvis\tools\llama-swap\v261\llama-swap.exe"
$logs = "D:\jarvis\N.E.K.O-data\logs"
$hwFile = "D:\jarvis\N.E.K.O-data\neko\hardware.json"
if (-not $Profile -and -not $Stop) {
  if (-not (Test-Path $hwFile)) {
    & "D:\jarvis\tools\neko-venv\Scripts\python.exe" (Join-Path $PSScriptRoot "..\local_models\hwdetect.py") --json | Out-Null
  }
  try { $Profile = (Get-Content $hwFile -Raw | ConvertFrom-Json).profile } catch { $Profile = "vram4" }
}
if ($Profile -eq "remote_only") { "local models: profile remote_only, none started"; return }
$cfgName = if (-not $Profile -or $Profile -eq "vram4") { "llama-swap.yaml" } else { "profiles\$Profile.yaml" }
$cfgPath = Join-Path $PSScriptRoot "..\local_models\$cfgName"
if (-not $Stop -and -not (Test-Path $cfgPath)) { throw "no model config for profile '$Profile' ($cfgPath)" }
$cfg = if (Test-Path $cfgPath) { (Resolve-Path $cfgPath).Path } else { "" }
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
"local models 8080: " + (Listening 8080) + "   (profile $Profile, $cfgName; models load on first use, unload when idle)"
