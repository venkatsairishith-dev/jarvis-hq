# Start N.E.K.O from this repository with upstream's launcher.py (memory 48912, main + web UI 48911, agent + plugin runtime 48915/48916).
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
# Upstream's launcher.py switches into a project-local .venv whenever one exists. Tools such as "uv run" or the upstream test
# suite create an EMPTY one, and the servers then fail with "No module named fastapi". An empty one is removed here; one that
# holds packages is left alone and reported (run uv with UV_PROJECT_ENVIRONMENT=$Venv so it never makes its own).
$strayVenv = Join-Path $repo ".venv"
if (Test-Path (Join-Path $strayVenv "Scripts\python.exe")) {
  $pkgs = @(Get-ChildItem (Join-Path $strayVenv "Lib\site-packages") -Force -ErrorAction SilentlyContinue).Count
  if ($pkgs -eq 0) { Remove-Item $strayVenv -Recurse -Force; "removed an empty project .venv that would have broken the launcher" }
  else { throw "A project .venv with $pkgs packages exists at $strayVenv; upstream's launcher.py would switch into it. Move or delete it, or use -Venv." }
}
$env:NEKO_STORAGE_SELECTED_ROOT = $Data
$env:NEKO_LANGUAGE = "en"        # force English regardless of region/system detection
$env:NEKO_SECRET_REF_ROOTS = "D:\jarvis-secrets"   # lets a stored API key be a "file:<path>" reference into this folder
$env:PYTHONUTF8 = "1"
$env:HF_HOME = "D:\jarvis\cache\hf"          # local speech-recognition model weights stay on D:
# Speech recognition for N.E.K.O goes to JARVIS's int8 recognizers (Parakeet; IndicConformer for Hindi and Telugu) through the
# local speech server instead of loading Whisper in-process. Loopback only. Remove this line to go back to Whisper base.
$env:NEKO_WHISPER_ENDPOINT = "http://127.0.0.1:48930/v1/audio/transcriptions"
# The small helper calls (emotion enrichment, suggested-reply chips, topic search) have budgets sized for a model that answers
# directly. Dahl's MiniMax reasons inline first, which used up the time and the output cap (timeouts, cut-off JSON). Remove to restore upstream's.
$env:NEKO_AUX_LLM_TIMEOUT_SCALE = "3"
$env:NEKO_AUX_LLM_TOKEN_SCALE = "4"
# A new chat waits for the previous chat's settle summary (consistency). With a slow model that takes a minute or more; upstream gives up after 5 s.
$env:NEKO_MEMORY_FETCH_TIMEOUT_S = "150"
$env:HF_HUB_DISABLE_TELEMETRY = "1"

function Listening($port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) }

# The three N.E.K.O servers (memory 48912, main 48911, agent 48915 with the plugin runtime 48916) are started and watched by
# upstream's own launcher.py, exactly as upstream intends from source. A server that already listens is reused by the launcher.
if (-not ((Listening 48912) -and (Listening 48911) -and (Listening 48915))) {
  Start-Process $py -ArgumentList 'launcher.py' -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs "launcher.out.log") -RedirectStandardError (Join-Path $logs "launcher.err.log") | Out-Null
  foreach ($i in 1..240) { if ((Listening 48912) -and (Listening 48911) -and (Listening 48915) -and (Listening 48916)) { break }; Start-Sleep 1 }
}
# Local speech output: an OpenAI-compatible endpoint over JARVIS's own voice models (Kokoro, Piper), loopback only.
$speechPy = "D:\jarvis\tools\jarvis-venv\Scripts\python.exe"
if (-not (Listening 48930) -and (Test-Path $speechPy)) {
  Start-Process $speechPy -ArgumentList (Join-Path $PSScriptRoot "local_speech_server.py"), "--port", "48930" -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\speech.out.log" -RedirectStandardError "$logs\speech.err.log" | Out-Null
  foreach ($i in 1..20) { if (Listening 48930) { break }; Start-Sleep 1 }
}
# The desktop widget's backend (stdlib only, loopback only): status and a quick task box for the always-on-top widget window.
$widgetScript = Join-Path $PSScriptRoot "..\widget\widget_server.py"
if (-not (Listening 48940) -and (Test-Path $widgetScript)) {
  Start-Process $py -ArgumentList (Resolve-Path $widgetScript).Path, "--port", "48940" -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\widget.out.log" -RedirectStandardError "$logs\widget.err.log" | Out-Null
  foreach ($i in 1..10) { if (Listening 48940) { break }; Start-Sleep 1 }
}
# The agent server (started by the launcher above) hosts the plugin runtime (48916) and routes tasks; the JARVIS plugins live there.

# The startup scan can come up empty, so ask for a rescan and make sure both plugins are running (idempotent).
$plugins = "not started"
if (Listening 48916) {
  try {
    Invoke-RestMethod -Method POST http://127.0.0.1:48916/plugins/refresh -ContentType 'application/json' -Body '{}' -TimeoutSec 60 | Out-Null
    $states = @()
    foreach ($id in 'jarvis_bridge', 'doc_tools', 'learn_vault', 'main_agent') {   # the bridge and the tool plugins first: the main agent calls them
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
