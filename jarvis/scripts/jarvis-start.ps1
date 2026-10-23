# Start JARVIS's local services and the face. Windows. Run from the repo (this script lives in jarvis\scripts).
# Data and the Python environments stay on D:. Safe to run twice: a service that already listens is left alone. Stop with jarvis-stop.ps1.
#
# One-time setup of the agent environment (plugins, tool server, watch runner; nothing outside D:):
#   $env:UV_PYTHON_INSTALL_DIR = "D:\jarvis\tools\uv-python"; $env:UV_CACHE_DIR = "D:\jarvis\cache\uv"
#   D:\jarvis\tools\uv\uv.exe venv D:\jarvis\tools\agent-venv --python 3.11
#   D:\jarvis\tools\uv\uv.exe pip install --python D:\jarvis\tools\agent-venv\Scripts\python.exe -r jarvis\tools\requirements\agent-venv.txt
# The speech server has its own environment (D:\jarvis\tools\jarvis-venv); the privacy gateway has privacy-venv.
param(
  [string]$Venv = "D:\jarvis\tools\agent-venv",
  [string]$Data = "D:\jarvis\data",
  [string]$Vault = "D:\jarvis\vault",
  [string]$Face = "$env:LOCALAPPDATA\Programs\JARVIS\JARVIS.exe",   # the installed face (jarvis\face: pnpm run build:win)
  [switch]$NoFace                  # start the services only
)
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$py   = Join-Path $Venv "Scripts\python.exe"
$logs = Join-Path $Data "logs"
if (-not (Test-Path $py)) { throw "Python environment not found: $py (see the setup note at the top of this script)" }
New-Item -ItemType Directory $logs -Force | Out-Null
$env:JARVIS_DATA_DIR = $Data
$env:JARVIS_SECRET_REF_ROOTS = "D:\jarvis-secrets"   # lets a stored API key be a "file:<path>" reference into this folder
$env:PYTHONUTF8 = "1"
$env:HF_HOME = "D:\jarvis\cache\hf"          # local speech-recognition model weights stay on D:
$env:HF_HUB_DISABLE_TELEMETRY = "1"
. (Join-Path $PSScriptRoot "d-env.ps1")        # TEMP, TMP and every cache on D: (owner rule)
& (Join-Path $PSScriptRoot "start-verdict.ps1") | Out-Null      # the Jev decision model (OpenJev Verdict, CPU, 127.0.0.1:8091); idempotent

function Listening($port) { [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) }

# Privacy gateway (127.0.0.1:48950, Presidio): the face's chat and the plugins' agent model point here, so passwords, keys, card
# numbers, phone and e-mail leave the PC as placeholders like [PASSWORD_1] and are put back in the reply locally. Started first.
$privPy = "D:\jarvis\tools\privacy-venv\Scripts\python.exe"
$privScript = Join-Path $PSScriptRoot "..\privacy\gateway.py"
if (-not (Listening 48950) -and (Test-Path $privPy) -and (Test-Path $privScript)) {
  Start-Process $privPy -ArgumentList (Resolve-Path $privScript).Path, "--port", "48950" -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\privacy.out.log" -RedirectStandardError "$logs\privacy.err.log" | Out-Null
  foreach ($i in 1..30) { if (Listening 48950) { break }; Start-Sleep 1 }
}
# Planner gateway (127.0.0.1:48952): the same redaction, but Claude is asked first (owner exception until 2026-10-31) and Dahl answers if
# Claude cannot. Only the plugins' agent model (JARVIS_AGENT_BASE_URL, set below) points here; the default gateway never asks Claude.
$plannerOn = ((Get-Date).ToUniversalTime().Date -le [datetime]"2026-10-31")
if ($plannerOn -and -not (Listening 48952) -and (Test-Path $privPy) -and (Test-Path $privScript)) {
  Start-Process $privPy -ArgumentList (Resolve-Path $privScript).Path, "--port", "48952", "--providers", (Resolve-Path (Join-Path $PSScriptRoot "..\privacy\providers.planner.json")).Path -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\privacy-planner.out.log" -RedirectStandardError "$logs\privacy-planner.err.log" | Out-Null
  foreach ($i in 1..30) { if (Listening 48952) { break }; Start-Sleep 1 }
}
if ($plannerOn -and (Listening 48952)) { $env:JARVIS_AGENT_BASE_URL = "http://127.0.0.1:48952/v1" }
$agentKey = "D:\jarvis-secrets\agent.key"       # the key the gateway passes on as the caller's own (never the key itself here)
if (Test-Path $agentKey) { $env:JARVIS_AGENT_KEY_FILE = $agentKey }
# Second-opinion gateway (127.0.0.1:48954, plan item 9): the same redaction, but its only provider is the reviewer (Groq, free tier).
if (-not (Listening 48954) -and (Test-Path $privPy) -and (Test-Path $privScript)) {
  Start-Process $privPy -ArgumentList (Resolve-Path $privScript).Path, "--port", "48954", "--providers", (Resolve-Path (Join-Path $PSScriptRoot "..\privacy\providers.second.json")).Path -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\privacy-second.out.log" -RedirectStandardError "$logs\privacy-second.err.log" | Out-Null
  foreach ($i in 1..30) { if (Listening 48954) { break }; Start-Sleep 1 }
}
# Page-watch runner (plan P7 step 9): checks due watches through the egress guard and proxy, files notices in the inbox. No port, so it is found
# by its command line; a second copy would refuse to start anyway (OS lock). The phone ping is added only when the topic file exists.
$muse = Join-Path $Data "muse"
New-Item -ItemType Directory $muse -Force | Out-Null
if (-not (Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'jarvis\.muse\.runner' })) {
  $runnerArgs = @('-m', 'jarvis.muse.runner', '--watches', (Join-Path $muse 'watches.json'), '--inbox', (Join-Path $muse 'inbox.jsonl'))
  $ntfyTopic = 'D:\jarvis-secrets\ntfy.topic'
  if (Test-Path $ntfyTopic) { $runnerArgs += @('--push-topic-file', $ntfyTopic) }
  Start-Process $py -ArgumentList $runnerArgs -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\muse-runner.out.log" -RedirectStandardError "$logs\muse-runner.err.log" | Out-Null
}
# Local specialist models (llama-swap on 127.0.0.1:8080): nothing is loaded until a model is asked for. Never piped (it starts a server).
$localModels = Join-Path $PSScriptRoot "local-models.ps1"
if (-not (Listening 8080) -and (Test-Path "D:\jarvis\tools\llama-swap\v261\llama-swap.exe")) {
  Start-Process powershell -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$localModels`"" -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\local_models.start.log" | Out-Null
  foreach ($i in 1..20) { if (Listening 8080) { break }; Start-Sleep 1 }
}
# Local speech: an OpenAI-compatible endpoint over JARVIS's own voice models (Kokoro, Piper; speech to text), loopback only.
$speechPy = "D:\jarvis\tools\jarvis-venv\Scripts\python.exe"
if (-not (Listening 48930) -and (Test-Path $speechPy)) {
  Start-Process $speechPy -ArgumentList (Join-Path $PSScriptRoot "local_speech_server.py"), "--port", "48930" -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\speech.out.log" -RedirectStandardError "$logs\speech.err.log" | Out-Null
  foreach ($i in 1..20) { if (Listening 48930) { break }; Start-Sleep 1 }
}
# JARVIS's console (127.0.0.1:8799), started only if nothing listens there, so a console that is already running (from any
# folder) is left alone. The laptop node is a scheduled task: reported, never touched.
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
    . (Join-Path $PSScriptRoot 'jarvis-auth.ps1')
    $h = Invoke-RestMethod http://127.0.0.1:8799/api/health -Headers (Get-JarvisHeaders "console") -TimeoutSec 10     # the full health needs the console token
    $jarvis = "console up, kernel " + $(if ($h.kernel.reachable -and $h.kernel.authorized) { "reachable and authorized" } else { "NOT reachable/authorized" })
  } catch { $jarvis = "console listening but not answering" }
}
# The tool server for the face (127.0.0.1:48970/mcp): jarvis_do / jarvis_task_result / ... and every entry of the plugins in
# jarvis\plugins (research, deliverables, doc_tools, learn_vault, workbench, local_team, desktop, main_agent, web_search),
# plus the read-only views of the data folder. Start it after the console: its tools call the console.
if (-not (Listening 48970)) {
  Start-Process $py -ArgumentList '-m', 'jarvis.bridge.face_tools', '--data', $muse, '--vault', $Vault -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\face-tools.out.log" -RedirectStandardError "$logs\face-tools.err.log" | Out-Null
  foreach ($i in 1..40) { if (Listening 48970) { break }; Start-Sleep 1 }
}
$node = try { (Get-ScheduledTask JarvisNode -ErrorAction Stop).State } catch { "task not found" }
$faceState = if ($NoFace) { "not started (-NoFace)" } elseif (-not (Test-Path $Face)) { "not installed ($Face; build it in jarvis\face)" }
             elseif (Get-Process -Name "JARVIS" -ErrorAction SilentlyContinue) { "already running" }
             else { Start-Process $Face; "started" }

"repo   : $repo"
"tools  48970: " + (Listening 48970) + "   (the face's tool server: JARVIS tasks and the plugins)"
"speech 48930: " + (Listening 48930) + "   (local speech to text and text to speech)"
"privacy 48950: " + (Listening 48950) + "   (redacts private values before the public model)"
"planner 48952: " + (Listening 48952) + "   (Claude first, owner exception until 2026-10-31)"
"local  8080 : " + (Listening 8080) + "   (local specialist models; load on first use)"
"jarvis : $jarvis | laptop node task: $node"
"face   : $faceState"
"approvals are given in the JARVIS console http://127.0.0.1:8799/ with your PIN, never in the face"
