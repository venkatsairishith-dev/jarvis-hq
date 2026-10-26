# The reliability gate on the laptop (D59). One entry point for the laptop session.
#
#   .\run-reliability.ps1 -Mode selftest                 FIRST: do the harness's own eyes and hands work here
#   .\run-reliability.ps1 -Mode list                     what will be measured and how long it takes
#   .\run-reliability.ps1 -Mode quick -Capability screen_text,final_answer      3 repeats of two capabilities (a smoke run, never a gate result)
#   .\run-reliability.ps1 -Mode gate                     the real thing: every goal x 10 repeats, face driver (hours; resumes where it stopped)
#   .\run-reliability.ps1 -Mode gate -Attended           the owner is at the PC and approves PIN cards: the delete goals' EFFECTS are then measured
#   .\run-reliability.ps1 -Mode report                   the owner's page: each capability against 90% (also written to results\STATUS.md)
#   .\run-reliability.ps1 -Mode latency                  which goals are over their latency budget and which lever applies
#
# Needs: the JARVIS tool server up (127.0.0.1:48970), the face started with --remote-debugging-port=9333 (face_reset.sh does that), Node on PATH.
# For the capability local_moe_only: start the gateway with JARVIS_GATEWAY_PIN_FILE=D:\jarvis\cache\gateway-pin.txt and pass -PinFile with the same path.
# Results: jarvis\evals\reliability\results\reliability-<date>.json (+ .md). Nothing here approves a PIN card, ever.
param(
    [ValidateSet('selftest', 'list', 'validate', 'quick', 'gate', 'report', 'latency')][string]$Mode = 'list',
    [string]$Capability = '',
    [string]$Goal = '',
    [ValidateSet('face', 'mcp')][string]$Driver = 'face',
    [int]$Repeats = 10,
    [switch]$Attended,
    [switch]$Fresh,
    [switch]$KeepGoing,
    [double]$MaxMinutes = 0,
    [string]$Reset = 'inplace',
    [string]$PinFile = $env:JARVIS_GATEWAY_PIN_FILE,
    [string]$Python = 'D:\jarvis\tools\agent-venv\Scripts\python.exe',
    [string]$Repo = ''
)
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Repo) { $Repo = (Resolve-Path (Join-Path $here '..\..\..')).Path }          # the folder that contains jarvis\
if (-not (Test-Path $Python)) { $Python = 'python' }
Set-Location $Repo
if (-not $env:JARVIS_FACE_DIR) { $env:JARVIS_FACE_DIR = Join-Path $Repo 'jarvis\face\apps\stage-tamagotchi' }
if (-not $env:PYTHONUTF8) { $env:PYTHONUTF8 = '1' }

function Test-Port($port) {
    try { $c = New-Object System.Net.Sockets.TcpClient; $c.Connect('127.0.0.1', $port); $c.Close(); return $true } catch { return $false }
}

if ($Mode -in @('selftest', 'quick', 'gate')) {
    $tool = Test-Port 48970; $face = Test-Port 9333; $gw = Test-Port 48950
    Write-Host ("preflight: tool server 48970 {0}, face debug port 9333 {1}, privacy gateway 48950 {2}" -f $(if ($tool) { 'UP' } else { 'DOWN' }), $(if ($face) { 'UP' } else { 'DOWN' }), $(if ($gw) { 'UP' } else { 'DOWN' }))
    if (-not $tool) { Write-Warning 'the tool server is down: python -m jarvis.bridge.face_tools (or the JARVIS start script) first' }
    if ($Driver -eq 'face' -and -not $face) { Write-Warning 'the face is not listening on 9333: start it with --remote-debugging-port=9333 --remote-debugging-address=127.0.0.1 (see evals\onscreen_bench\driver\face_reset.sh)' }
}

$m = 'jarvis.evals.reliability'
$common = @('--driver', $Driver, '--reset', $Reset)
if ($PinFile) { $common += @('--pin-file', $PinFile) }
switch ($Mode) {
    'list'     { & $Python -m "$m.runner" --list --repeats $Repeats }
    'validate' { & $Python -m "$m.runner" --validate }
    'selftest' { & $Python -m "$m.runner" --selftest @common }
    'report'   { & $Python -m "$m.report" --write }
    'latency'  { & $Python -m "$m.latency" }
    default {
        $a = @('-m', "$m.runner") + $common
        if ($Mode -eq 'quick') { $a += '--quick' } else { $a += @('--repeats', $Repeats) }
        if ($Capability) { $a += @('--capability', $Capability) }
        if ($Goal) { $a += @('--goal', $Goal) }
        if ($Attended) { $a += '--attended' }
        if ($Fresh) { $a += '--fresh' }
        if ($KeepGoing) { $a += '--keep-going' }
        if ($MaxMinutes -gt 0) { $a += @('--max-minutes', $MaxMinutes) }
        & $Python @a
        $code = $LASTEXITCODE
        & $Python -m "$m.report" --write
        if ($code -eq 3) { Write-Warning 'STOPPED for a critical unsafe outcome: read results\STATUS.md and look at the PC before anything else.' }
        exit $code
    }
}
