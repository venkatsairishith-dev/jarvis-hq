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
  [string]$Face = "",              # the installed face (jarvis\face: pnpm run build:win); empty = look in the usual places
  [switch]$NoFace                  # start the services only
)
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
# Where the face is installed: -Face, then $env:JARVIS_FACE_EXE, then the usual places (the installer lets the owner pick the folder).
if (-not $Face) {
  $candidates = @($env:JARVIS_FACE_EXE, 'D:\APPS\jarvis\jarvis.exe', "$env:LOCALAPPDATA\Programs\JARVIS\JARVIS.exe", "$env:LOCALAPPDATA\Programs\jarvis\jarvis.exe", "$env:ProgramFiles\JARVIS\JARVIS.exe") | Where-Object { $_ }
  $Face = ($candidates | Where-Object { Test-Path $_ } | Select-Object -First 1)
  if (-not $Face) { $Face = 'D:\APPS\jarvis\jarvis.exe' }
}

$py   = Join-Path $Venv "Scripts\python.exe"
$logs = Join-Path $Data "logs"
# D50 (owner, 2026-10-25: "for storage use only the D: drive"): refuse to start when a configured path is not on D:. One line, paths only.
foreach ($p in @($Venv, $Data, $Vault)) { if ($p -notmatch '^[dD]:\\') { throw "JARVIS: refusing to start, not on the D: drive: $p" } }
if (-not (Test-Path $py)) { throw "Python environment not found: $py (see the setup note at the top of this script)" }
New-Item -ItemType Directory $logs -Force | Out-Null
$env:JARVIS_DATA_DIR = $Data
$env:JARVIS_SECRET_REF_ROOTS = "D:\jarvis-secrets"   # lets a stored API key be a "file:<path>" reference into this folder
$env:PYTHONUTF8 = "1"
$env:HF_HOME = "D:\jarvis\cache\hf"          # local speech-recognition model weights stay on D:
$env:HF_HUB_DISABLE_TELEMETRY = "1"
. (Join-Path $PSScriptRoot "d-env.ps1")        # TEMP, TMP and every cache on D: (owner rule)
foreach ($n in 'JARVIS_DATA_DIR','TEMP','TMP','HF_HOME','PIP_CACHE_DIR','UV_CACHE_DIR','npm_config_cache','PLAYWRIGHT_BROWSERS_PATH') {
  $v = [Environment]::GetEnvironmentVariable($n, 'Process'); if ($v -and $v -notmatch '^[dD]:\\') { throw "JARVIS: refusing to start, $n is not on the D: drive: $v" }
}
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
# No Claude at run time (owner, 2026-10-23): the plugins' agent model (JARVIS_AGENT_BASE_URL) is the privacy gateway on 48950, whose only
# provider is Dahl. Claude is used to build JARVIS, never called by it.
# The planner's model and its key. JARVIS_AGENT_MODEL is the model name sent to the provider (Dahl's GLM-5.3-Flash by default, chosen by measurement, reports/BASE_MODEL_2026-10-24.md; before that the
# N.E.K.O era used). JARVIS_AGENT_KEY_FILE is the PATH of a file holding the provider key, never the key itself; the gateway passes it
# on as the caller's own key. Set either variable before running this script to use something else.
if (-not $env:JARVIS_AGENT_MODEL) { $env:JARVIS_AGENT_MODEL = "zai-org/GLM-5.3-Flash" }
if (-not $env:JARVIS_AGENT_KEY_FILE) {
  foreach ($k in "D:\jarvis-secrets\providers\dahl.key", "D:\jarvis-secrets\agent.key") { if (Test-Path $k) { $env:JARVIS_AGENT_KEY_FILE = $k; break } }
}
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
    # Neither piped nor -Wait: the console it starts inherits a pipe's handle, and -Wait also waits for the whole process tree, so both
    # block for as long as the console runs (found on the laptop, 2026-10-23). The loop below waits for the port instead.
    try { Start-Process powershell -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$consoleScript`"" -WindowStyle Hidden | Out-Null } catch { }
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
# jarvis\plugins (research, deliverables, doc_tools, doc_reader, learn_vault, workbench, local_team, desktop, main_agent, web_search),
# plus the read-only views of the data folder. Start it after the console: its tools call the console.
if (-not (Listening 48970)) {
  Start-Process $py -ArgumentList '-m', 'jarvis.bridge.face_tools', '--data', $muse, '--vault', $Vault -WorkingDirectory $repo -WindowStyle Hidden `
    -RedirectStandardOutput "$logs\face-tools.out.log" -RedirectStandardError "$logs\face-tools.err.log" | Out-Null
  foreach ($i in 1..40) { if (Listening 48970) { break }; Start-Sleep 1 }
}
# The local translation service (D58) on 127.0.0.1:48960: IndicTrans2 for Telugu and Hindi, one process that both the gateway and the face's language layer call.
# Its own venv (D:\jarvis\tools\translate-venv: onnxruntime, tokenizers, IndicTransToolkit as plain Python) and models (D:\jarvis\models\translate). A missing venv or
# model means no translation (the original text goes on), never an error. The models load on the first request (5 s) and go after 120 s idle.
$trPy = "D:\jarvis\tools\translate-venv\Scripts\python.exe"
if ((Test-Path $trPy) -and (Test-Path "D:\jarvis\models\translate") -and -not (Listening 48960)) {
  $env:PYTHONPATH = $repo
  Start-Process $trPy -ArgumentList (Join-Path $repo "jarvis\scripts\translate_server.py") -WorkingDirectory $repo -WindowStyle Hidden -RedirectStandardOutput "$logs\translate.out.log" -RedirectStandardError "$logs\translate.err.log" | Out-Null
  Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
}
# Footprint governor (D50: JARVIS may use 20% of the laptop): BELOW_NORMAL + EcoQoS for every JARVIS process, one Job Object with the CPU hard cap and a
# memory backstop, and a soft loop that unloads idle models when the total is over budget. One copy only (it refuses a second). Log: data\logs\governor.jsonl.
$govScript = Join-Path $PSScriptRoot "..\tools\governor.py"
if ((Test-Path $govScript) -and -not (Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'tools[\\/]governor\.py' })) {
  Start-Process $py -ArgumentList (Resolve-Path $govScript).Path -WorkingDirectory $repo -WindowStyle Hidden -RedirectStandardError "$logs\governor.err.log" | Out-Null
}
$node = try { (Get-ScheduledTask JarvisNode -ErrorAction Stop).State } catch { "task not found" }
$faceState = if ($NoFace) { "not started (-NoFace)" } elseif (-not (Test-Path $Face)) { "not installed ($Face; build it in jarvis\face)" }
             elseif (Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($Face)) -ErrorAction SilentlyContinue) { "already running" }
             else { Start-Process $Face -ArgumentList "--user-data-dir=$($env:APP_USER_DATA_PATH)"; "started" }

"repo   : $repo"
"tools  48970: " + (Listening 48970) + "   (the face's tool server: JARVIS tasks and the plugins)"
"speech 48930: " + (Listening 48930) + "   (local speech to text and text to speech)"
"privacy 48950: " + (Listening 48950) + "   (redacts private values before the public model)"
"local  8080 : " + (Listening 8080) + "   (local specialist models; load on first use)"
"jarvis : $jarvis | laptop node task: $node"
"face   : $faceState"
"approvals are given in the JARVIS console http://127.0.0.1:8799/ with your PIN, never in the face"
