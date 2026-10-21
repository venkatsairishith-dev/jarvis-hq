# Open JARVIS / N.E.K.O as a desktop-style app window (Microsoft Edge app mode: own window, no tabs or address bar). Windows.
#
#   open-desktop-app.ps1                 the N.E.K.O window (her, voice, chat, plugin manager)   http://127.0.0.1:48911/
#   open-desktop-app.ps1 -Target jarvis  the JARVIS console (approvals, connectors, memory)       http://127.0.0.1:8799/
#
# If the servers are not running yet they are started first (neko-start.ps1), which also starts the console. Closing the window
# does not stop anything: the servers keep running in the background (stop them with neko-stop.ps1).
param([ValidateSet('neko', 'jarvis')][string]$Target = 'neko')
$url = if ($Target -eq 'jarvis') { 'http://127.0.0.1:8799/' } else { 'http://127.0.0.1:48911/' }
$port = if ($Target -eq 'jarvis') { 8799 } else { 48911 }
if ($Target -eq 'jarvis') { . (Join-Path $PSScriptRoot 'jarvis-auth.ps1') }
function Listening($p) { [bool](Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue) }

if (-not (Listening $port)) {
  Write-Host "Starting the servers (about a minute the first time)..."
  & (Join-Path $PSScriptRoot 'neko-start.ps1') | Out-Null
}
$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe", "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") |
  Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $edge) { throw "Microsoft Edge was not found; open $url in any browser instead." }
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
if ($Target -eq 'jarvis') { $url = Get-LaunchUrl 'console' 8799 '/' }   # a one-time launch code in the fragment signs the window in
Start-Process $edge -ArgumentList "--app=$url", "--window-size=1100,820"
