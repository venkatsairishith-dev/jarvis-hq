# Start N.E.K.O from this repository (memory 48912, main + web UI 48911, agent + plugin runtime 48915/48916).
# Windows. Runs from the repo root two levels above this script. Data and the Python environment stay on D:.
# Safe to run twice: a server that already listens is left alone. Stop with neko-stop.ps1.
#
# One-time setup of the environment (the repo's own tooling; nothing outside D:):
#   $env:UV_PYTHON_INSTALL_DIR = "D:\jarvis\tools\uv-python"; $env:UV_CACHE_DIR = "D:\jarvis\cache\uv"
#   $env:UV_PROJECT_ENVIRONMENT = "D:\jarvis\tools\neko-venv"
#   D:\jarvis\tools\uv\uv.exe sync --frozen --python 3.11      # run from the repo root
param(
  [string]$Venv = "D:\jarvis\tools\neko-venv",
  [string]$Data = "D:\jarvis\N.E.K.O-data"
)
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$py   = Join-Path $Venv "Scripts\python.exe"
$logs = Join-Path $Data "logs"
if (-not (Test-Path $py)) { throw "Python environment not found: $py (see the setup note at the top of this script)" }
New-Item -ItemType Directory $logs -Force | Out-Null
$env:NEKO_STORAGE_SELECTED_ROOT = $Data
$env:NEKO_LANGUAGE = "en"        # force English regardless of region/system detection
$env:NEKO_SECRET_REF_ROOTS = "D:\jarvis-secrets"   # lets a stored API key be a "file:<path>" reference into this folder
$env:PYTHONUTF8 = "1"
$env:HF_HOME = "D:\jarvis\cache\hf"          # local speech-recognition model weights stay on D:
$env:HF_HUB_DISABLE_TELEMETRY = "1"

function Listening($port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) }
function Launch($module, $name) {
  Start-Process $py -ArgumentList '-m', $module -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "$name.out.log") -RedirectStandardError (Join-Path $logs "$name.err.log") | Out-Null
}

if (-not (Listening 48912)) { Launch 'app.memory_server' 'memory'; foreach ($i in 1..20) { if (Listening 48912) { break }; Start-Sleep 1 } }
if (-not (Listening 48911)) { Launch 'app.main_server' 'main';     foreach ($i in 1..40) { if (Listening 48911) { break }; Start-Sleep 1 } }
# Local speech output: an OpenAI-compatible endpoint over JARVIS's own voice models (Kokoro, Piper), loopback only.
$speechPy = "D:\jarvis\tools\jarvis-venv\Scripts\python.exe"
if (-not (Listening 48930) -and (Test-Path $speechPy)) {
  Start-Process $speechPy -ArgumentList (Join-Path $PSScriptRoot "local_speech_server.py"), "--port", "48930" -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\speech.out.log" -RedirectStandardError "$logs\speech.err.log" | Out-Null
  foreach ($i in 1..20) { if (Listening 48930) { break }; Start-Sleep 1 }
}
# The agent server hosts the plugin runtime (48916) and routes tasks; the JARVIS plugins live there.
if (-not (Listening 48915)) { Launch 'app.agent_server' 'agent';   foreach ($i in 1..40) { if ((Listening 48915) -and (Listening 48916)) { break }; Start-Sleep 1 } }

# The startup scan can come up empty, so ask for a rescan and make sure both plugins are running (idempotent).
$plugins = "not started"
if (Listening 48916) {
  try {
    Invoke-RestMethod -Method POST http://127.0.0.1:48916/plugins/refresh -ContentType 'application/json' -Body '{}' -TimeoutSec 60 | Out-Null
    $states = @()
    foreach ($id in 'jarvis_bridge', 'main_agent') {          # the bridge first: the main agent calls it for JARVIS steps
      $p = (Invoke-RestMethod http://127.0.0.1:48916/plugins -TimeoutSec 15).plugins | Where-Object { $_.id -eq $id }
      if ($p -and $p.status -ne 'running') {
        Invoke-RestMethod -Method POST "http://127.0.0.1:48916/plugin/$id/start" -ContentType 'application/json' -Body '{}' -TimeoutSec 60 | Out-Null
      }
      $p = (Invoke-RestMethod http://127.0.0.1:48916/plugins -TimeoutSec 15).plugins | Where-Object { $_.id -eq $id }
      $states += ("{0}={1}" -f $id, $(if ($p) { $p.status } else { "not found" }))
    }
    $plugins = $states -join ", "
  } catch { $plugins = "error: " + $_.Exception.Message.Split("`n")[0] }
}
# JARVIS itself: one command starts everything. The console (127.0.0.1:8799) is started only if nothing listens there, so a
# console that is already running (from any folder) is left alone. The laptop node is a scheduled task: reported, never touched.
$jarvis = "console not started"
if (-not (Listening 8799)) {
  $consoleScript = Join-Path $PSScriptRoot 'start-console.ps1'
  if (Test-Path $consoleScript) {
    try { & powershell -NoProfile -ExecutionPolicy Bypass -File $consoleScript | Out-Null } catch { }
    foreach ($i in 1..15) { if (Listening 8799) { break }; Start-Sleep 1 }
  }
}
if (Listening 8799) {
  try {
    $h = Invoke-RestMethod http://127.0.0.1:8799/api/health -TimeoutSec 10
    $jarvis = "console up, kernel " + $(if ($h.kernel.reachable -and $h.kernel.authorized) { "reachable and authorized" } else { "NOT reachable/authorized" })
  } catch { $jarvis = "console listening but not answering" }
}
$node = try { (Get-ScheduledTask JarvisNode -ErrorAction Stop).State } catch { "task not found" }

"repo   : $repo"
"memory 48912: " + (Listening 48912)
"main   48911: " + (Listening 48911) + "   ->  http://127.0.0.1:48911/"
"speech 48930: " + (Listening 48930) + "   (local text-to-speech)"
"agent  48915: " + (Listening 48915) + "   plugins 48916: " + (Listening 48916)
"plugins: $plugins"
"jarvis : $jarvis | laptop node task: $node"
$assets = (Test-Path (Join-Path $repo "static\yui-origin\yui-origin.moc3")) -and (Test-Path (Join-Path $repo "static\react\neko-chat\neko-chat-window.es.js"))
if (-not $assets) { "WARNING: built frontend assets are missing (avatar models, chat bundle). Run build_frontend.bat in $repo, or copy static\ from a built checkout." }
"open   : http://127.0.0.1:48911/  (talk to her here; tasks go to the main agent and JARVIS; approvals are in the JARVIS console http://127.0.0.1:8799/)"
