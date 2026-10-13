# Move the live JARVIS services on this laptop from D:\jarvis\hq (branch jarvis-hq-legacy) to this repository's jarvis\ folder. Windows.
#
#   powershell -File cutover-to-neko-base.ps1            dry run: checks everything, changes nothing
#   powershell -File cutover-to-neko-base.ps1 -Apply     does it
#
# What it changes (and nothing else): the working directory of the scheduled task JarvisNode, the script path of the task
# JarvisLab, and which folder the console runs from. The kernel on the server is not touched. It refuses to run unless JARVIS is
# idle (no queued or running job or task, no pending approval, no running command), backs both tasks up first, and rolls back by
# itself if the checks after the move fail. Manual rollback: rollback-from-neko-base.ps1 -BackupDir <the folder it printed>.
param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$new     = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path          # <repo>\jarvis
$old     = 'D:\jarvis\hq'
$console = 'http://127.0.0.1:8799'
$nodePy  = 'D:\jarvis\tools\browser-venv\Scripts\python.exe'
$home_   = 'D:\jarvis'                                                  # the workspace folder that holds cache\ and outputs\

function Fail($m) { Write-Host "STOP: $m" -ForegroundColor Red; exit 1 }
function Api($path) { Invoke-RestMethod -Uri ($console + $path) -TimeoutSec 25 }

Write-Host "from: $old"
Write-Host "to  : $new"
Write-Host ("mode: " + $(if ($Apply) { 'APPLY' } else { 'dry run (nothing will change)' }))

# 1. The new tree is complete
foreach ($f in 'ui\dist\index.html', 'node\jarvis_node\paths.py', 'scripts\start-console.ps1', 'scripts\start-jarvislab.vbs') {
  if (-not (Test-Path (Join-Path $new $f))) { Fail "missing in the new tree: $f (build the UI with 'npm ci; npm run build' in $new\ui if it is dist)" }
}
Write-Host "ok  new tree has the built UI, the node path helper and the start scripts"

# 2. The git-ignored local connector allowlist travels with the services
$list_old = Join-Path $old 'config\connectors.local.json'
$list_new = Join-Path $new 'config\connectors.local.json'
if ((Test-Path $list_old) -and -not (Test-Path $list_new)) {
  if ($Apply) { Copy-Item $list_old $list_new; Write-Host "ok  copied the connector allowlist" } else { Write-Host "todo copy the connector allowlist to the new tree" }
} else { Write-Host "ok  connector allowlist present or not in use" }

# 3. The node resolves the same workspace folder from the new tree
$resolved = (& $nodePy -I -c "import sys; sys.path.insert(0, r'$new\node'); import jarvis_node.paths as p; print(p.jarvis_home())").Trim()
if ($resolved -ne $home_) { Fail "the node would use '$resolved' as its workspace instead of '$home_'; delivered files would move" }
Write-Host "ok  the node's workspace folder resolves to $resolved (cache\ and outputs\ stay where they are)"

# 4. JARVIS is idle
try { $s = Api '/api/owner/state' } catch { Fail "the console is not answering on $console" }
$jobs  = @($s.jobs     | Where-Object { $_.state -in 'queued', 'running' })
$tasks = @($s.tasks    | Where-Object { $_.state -notin 'completed', 'failed', 'cancelled' })
$cmds  = @($s.commands | Where-Object { $_.state -eq 'running' })
if ($s.busy -or $jobs.Count -or $tasks.Count -or $cmds.Count -or @($s.approvals).Count) {
  Fail ("JARVIS is not idle: busy={0} jobs={1} open tasks={2} running commands={3} pending approvals={4}. Wait and run again." -f $s.busy, $jobs.Count, $tasks.Count, $cmds.Count, @($s.approvals).Count)
}
Write-Host "ok  JARVIS is idle (no job, task, command or approval outstanding)"

if (-not $Apply) { Write-Host "`nDry run finished: every check passed, nothing was changed. Run again with -Apply to move the services."; exit 0 }

# 5. Apply
$backup = 'D:\jarvis-apps\backups\tasks-pre-neko-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
New-Item -ItemType Directory $backup -Force | Out-Null
foreach ($t in 'JarvisNode', 'JarvisLab') { Export-ScheduledTask -TaskName $t | Out-File (Join-Path $backup "$t.xml") -Encoding utf8 }
Write-Host "ok  backed up both tasks to $backup"
try {
  $a = (Get-ScheduledTask JarvisNode).Actions[0]
  Set-ScheduledTask -TaskName JarvisNode -Action (New-ScheduledTaskAction -Execute $a.Execute -Argument $a.Arguments -WorkingDirectory (Join-Path $new 'node')) | Out-Null
  $l = (Get-ScheduledTask JarvisLab).Actions[0]
  Set-ScheduledTask -TaskName JarvisLab -Action (New-ScheduledTaskAction -Execute $l.Execute -Argument ('"' + (Join-Path $new 'scripts\start-jarvislab.vbs') + '"')) | Out-Null
  Write-Host "ok  repointed JarvisNode (working directory) and JarvisLab (script)"

  Stop-ScheduledTask JarvisNode -ErrorAction SilentlyContinue
  Start-Sleep 3
  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*-m jarvis_node*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep 2
  Start-ScheduledTask JarvisNode

  Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*-m jarvis_console*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
  Start-Sleep 3
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $new 'scripts\start-console.ps1') | Out-Null

  # 6. Verify; any failure rolls back
  $online = $false
  foreach ($i in 1..30) {
    Start-Sleep 3
    try { $n = (Api '/api/owner/models').nodes; if ($n.online -and $n.seconds_since_report -lt 60) { $online = $true; break } } catch { }
  }
  if (-not $online) { throw "the laptop node did not report to the kernel within 90 s" }
  $h = Api '/api/health'
  if (-not ($h.kernel.reachable -and $h.kernel.authorized)) { throw "the console cannot reach or is not authorized by the kernel" }
  $page = Invoke-WebRequest -UseBasicParsing -Uri "$console/" -TimeoutSec 15
  if ($page.StatusCode -ne 200) { throw "the console page did not load" }
  if (@(Get-NetTCPConnection -LocalPort 8799 -State Listen -ErrorAction SilentlyContinue).Count -ne 1) { throw "expected exactly one console listener on 8799" }
  Write-Host "ok  node online, kernel reachable and authorized, console page served, one listener"
  Write-Host "`nDONE. Services now run from $new . Rollback if ever needed: rollback-from-neko-base.ps1 -BackupDir $backup"
} catch {
  Write-Host ("FAILED after the move: " + $_.Exception.Message + " -> rolling back") -ForegroundColor Red
  & (Join-Path $PSScriptRoot 'rollback-from-neko-base.ps1') -BackupDir $backup
  exit 2
}
