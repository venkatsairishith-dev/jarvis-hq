# One-screen health view: git, ports, scheduled tasks, disk. Read only. Prints no secrets.
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
"branch: " + (git -C $repo branch --show-current) + "   HEAD " + (git -C $repo log --oneline -1)
"uncommitted: " + @(git -C $repo status --short).Count + " file(s)"
$names = @{48911='N.E.K.O UI';48912='memory';48915='agent';48916='plugin runtime';48930='speech';48940='widget';48945='wake word';48950='privacy gateway';8080='llama-swap';8799='JARVIS console'}
foreach ($p in $names.Keys | Sort-Object) { "{0,6} {1,-16} {2}" -f $p, $names[$p], $(if ((Test-NetConnection 127.0.0.1 -Port $p -WarningAction SilentlyContinue).TcpTestSucceeded) {'up'} else {'DOWN'}) }
try {
  $pl = (Invoke-RestMethod http://127.0.0.1:48916/plugins -TimeoutSec 10).plugins | Where-Object { $_.id -in 'jarvis_bridge','main_agent','desktop','research','doc_tools','web_search','local_team','learn_vault','deliverables' }
  "plugins: " + (($pl | ForEach-Object { "{0}={1}" -f $_.id, $_.status }) -join ", ")
  if (@($pl | Where-Object { $_.status -ne 'running' }).Count) { "  (a plugin is still starting: wait about a minute after a restart before giving tasks)" }
} catch { "plugins: runtime not answering" }
Get-ScheduledTask | Where-Object TaskName -match 'Jarvis' | ForEach-Object { "{0,-22} {1}" -f $_.TaskName, $_.State }
"free: D: {0:N1} GB   C: {1:N1} GB" -f ((Get-PSDrive D).Free/1GB), ((Get-PSDrive C).Free/1GB)
$tg = Join-Path $env:LOCALAPPDATA 'jarvis'; "telegram: " + $(if (Test-Path (Join-Path $repo 'jarvis\config\telegram.off')) {'OFF'} else {'check jarvis\config\telegram.off'})
