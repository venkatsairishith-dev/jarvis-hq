<#
  One-click installer for JARVIS on the owner's Windows PC. Double-click Install-JARVIS.cmd (repository root), or run this file.
  Safe to run again: every step checks what is already there and leaves it alone.

  What it does, in order (the user part needs no administrator rights):
    1  checks Windows, the D: drive and free space (JARVIS keeps everything large on D:)
    2  makes the folders (D:\jarvis, D:\jarvis-secrets) and checks, WITHOUT reading it, that the model key file is there
    3  Python 3.11 and the four environments (agent, speech, privacy, voice) from jarvis\tools\requirements
    4  the console's web UI (Node, npm run build)
    5  the pinned local models and tools from jarvis\tools\jarvis.lock.json (checksums verified), unless -NoModels
    6  the face (the installer built from jarvis\face, or a build from source), unless -NoFace
    7  start at login (JarvisStack), the watchdog and the daily report, then starts JARVIS
    8  the admin service ("second admin", see below), after one Windows permission prompt, unless -NoAdmin

  The admin service is NOT an admin shell. It is a small elevated process that runs only the typed admin verbs in
  jarvis\config\admin_verbs.json that you approve one by one, each time with your admin passphrase typed at a terminal.
  It can never turn off Defender, UAC or the firewall, change boot settings, create users, touch credentials, clear logs
  or wipe disks (refused in code). Its code lives in C:\Program Files\JARVIS (a folder normal programs cannot change),
  its state in C:\ProgramData\JARVIS\admin. See docs\05_OPERATIONS.md, "Admin service".

  Secrets: this script never asks for, reads, prints or downloads a key. The one manual step is copying your model key
  FILE to D:\jarvis-secrets\providers\dahl.key (and the kernel owner token to D:\jarvis-secrets\kernel\owner.token).

    Install-JARVIS.ps1 [-NoModels] [-NoFace] [-NoAdmin] [-NoStart] [-FaceInstaller path\to\JARVIS-setup.exe]
    Install-JARVIS.ps1 -AdminOnly         only the admin service (this is what the elevated copy runs)
    Install-JARVIS.ps1 -ResetAdmin        choose a new admin passphrase and go through the verbs again
    Install-JARVIS.ps1 -Verify            check an existing install (nothing is changed)
    Install-JARVIS.ps1 -Uninstall         remove the admin service and the start-at-login tasks (data and models stay)
#>
[CmdletBinding()]
param(
  [switch]$NoModels, [switch]$NoFace, [switch]$NoAdmin, [switch]$NoStart,
  [string]$FaceInstaller = "",
  [switch]$AdminOnly, [switch]$ResetAdmin, [switch]$Verify, [switch]$Uninstall, [switch]$RemoveAdminOnly
)
$ErrorActionPreference = 'Stop'
$repo    = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$jarvis  = Join-Path $repo "jarvis"
$Root    = "D:\jarvis"
$Secrets = "D:\jarvis-secrets"
$AdminCode = Join-Path $env:ProgramFiles "JARVIS\admin"
$AdminData = Join-Path $env:ProgramData "JARVIS\admin"
$TaskName  = "JarvisAdmin"
$script:Problems = New-Object System.Collections.Generic.List[string]
$script:Todo = New-Object System.Collections.Generic.List[string]

function Say($m)  { Write-Host $m }
function Ok($m)   { Write-Host "  ok    $m" -ForegroundColor Green }
function Skip($m) { Write-Host "  --    $m" -ForegroundColor DarkGray }
function Fail($m) { Write-Host "  FAIL  $m" -ForegroundColor Red; $script:Problems.Add($m) }
function Head($m) { Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Is-Admin { ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) }
function Free-GB($drive) { [math]::Round((Get-PSDrive -Name $drive -ErrorAction Stop).Free / 1GB, 1) }
function Run-Exe([string]$exe, [string[]]$arguments) { & $exe @arguments; if ($LASTEXITCODE -ne 0) { throw "$exe exited with $LASTEXITCODE" } }

# ---------------------------------------------------------------------------------------------------------------------------
# Admin service (elevated)
# ---------------------------------------------------------------------------------------------------------------------------
function Find-SystemPython {
  # A machine-wide Python under Program Files: ordinary programs cannot change it, so they cannot change what the elevated service runs.
  Get-ChildItem -Path $env:ProgramFiles -Filter "Python3*" -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name -Descending | ForEach-Object { Join-Path $_.FullName "python.exe" } | Where-Object { Test-Path $_ } | Select-Object -First 1
}

function Set-AdminAcl {
  $admins = "*S-1-5-32-544"; $system = "*S-1-5-18"; $users = "*S-1-5-32-545"
  # code: administrators change it, everyone may read and run it
  Run-Exe icacls.exe @($AdminCode, "/inheritance:r", "/grant:r", "${admins}:(OI)(CI)F", "${system}:(OI)(CI)F", "${users}:(OI)(CI)RX") | Out-Null
  Run-Exe icacls.exe @((Split-Path $AdminCode), "/inheritance:r", "/grant:r", "${admins}:(OI)(CI)F", "${system}:(OI)(CI)F", "${users}:(OI)(CI)RX") | Out-Null
  # state: readable by everyone, changed only by administrators; the passphrase key and the lock state are not even readable
  Run-Exe icacls.exe @($AdminData, "/inheritance:r", "/grant:r", "${admins}:(OI)(CI)F", "${system}:(OI)(CI)F", "${users}:(OI)(CI)RX") | Out-Null
  $private = Join-Path $AdminData "private"
  Run-Exe icacls.exe @($private, "/inheritance:r", "/grant:r", "${admins}:(OI)(CI)F", "${system}:(OI)(CI)F") | Out-Null
  # the owner's normal programs may drop requests in inbox\ and nothing else
  Run-Exe icacls.exe @((Join-Path $AdminData "inbox"), "/grant:r", "${users}:(OI)(CI)M") | Out-Null
}

function Install-Admin {
  if (-not (Is-Admin)) { throw "the admin service must be installed from an elevated window" }
  Head "Admin service (second admin)"
  $py = Find-SystemPython
  if (-not $py) {
    Say "  installing Python 3.11 for all users (needed so that ordinary programs cannot change the elevated code)"
    Run-Exe winget.exe @("install", "--id", "Python.Python.3.11", "--scope", "machine", "--exact", "--silent", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity")
    $py = Find-SystemPython
  }
  if (-not $py) { throw "no machine-wide Python found under $env:ProgramFiles" }
  Ok "machine-wide Python: $py"

  # 1. code: a clean copy of exactly three files
  $pkg = Join-Path $AdminCode "jarvis_node"
  if (Test-Path $AdminCode) { Remove-Item $AdminCode -Recurse -Force }
  New-Item -ItemType Directory $pkg -Force | Out-Null
  foreach ($f in "__init__.py", "adminctl.py", "adminsvc.py") { Copy-Item (Join-Path $jarvis "node\jarvis_node\$f") $pkg }
  foreach ($d in "inbox", "outbox", "private") { New-Item -ItemType Directory (Join-Path $AdminData $d) -Force | Out-Null }
  Set-AdminAcl
  Ok "code in $AdminCode, state in $AdminData, permissions set (administrators change, everyone reads)"

  $svc = Join-Path $pkg "adminsvc.py"
  $env:JARVIS_ADMIN_DIR = $AdminData
  # 2. the verbs: a changed verb loses its approval; a new one starts off
  Run-Exe $py @("-I", $svc, "sync-catalogue", "--shipped", (Join-Path $jarvis "config\admin_verbs.json"))
  # 3. the passphrase and the verbs, typed by the owner
  $haveKey = Test-Path (Join-Path $AdminData "private\verifier.json")
  if ($ResetAdmin -or -not $haveKey) {
    Say ""
    Say "  Choose your ADMIN PASSPHRASE (at least 10 characters). You type it each time JARVIS asks to do an admin task; it is never stored"
    Say "  in a place a normal program can read, and five wrong tries lock the service for 15 minutes."
    Run-Exe $py @("-I", $svc, "set-passphrase")
  } else { Skip "admin passphrase already set (use -ResetAdmin to change it)" }
  if ($ResetAdmin -or -not $haveKey) {
    Say ""
    Run-Exe $py @("-I", $svc, "approve-verbs")
  } else { Skip "verbs already reviewed (use -ResetAdmin to go through them again; a verb that changed is off until you approve it)" }

  # 4. the task: the owner's own account, highest privileges, at logon
  $user = "$env:USERDOMAIN\$env:USERNAME"
  $action = New-ScheduledTaskAction -Execute $py -Argument ('-I "{0}" run' -f $svc)
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
  $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
  $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force `
    -Description "JARVIS admin service: runs only the admin verbs the owner approved (adminsvc.py)." | Out-Null
  Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  Start-ScheduledTask -TaskName $TaskName
  Ok "scheduled task $TaskName (highest privileges, at logon of $user) started"

  # 5. it must really be alive, and the folders must really be closed
  $beat = Join-Path $AdminData "heartbeat.json"
  $alive = $false
  foreach ($i in 1..30) { if (Test-Path $beat) { $alive = $true; break }; Start-Sleep 1 }
  if ($alive) { Ok "the admin service answers" } else { Fail "the admin service did not start (run: Get-ScheduledTaskInfo $TaskName)" }
  Test-AdminAcl
}

function Test-AdminAcl {
  foreach ($p in $AdminCode, (Join-Path $AdminCode "jarvis_node\adminsvc.py"), $AdminData, (Join-Path $AdminData "catalogue.json"), (Join-Path $AdminData "private")) {
    if (-not (Test-Path $p)) { Fail "missing $p"; continue }
    $bad = (Get-Acl $p).Access | Where-Object {
      $_.AccessControlType -eq 'Allow' -and $_.IdentityReference.Value -match '(?i)\\Users$|^Everyone$|Authenticated Users' -and
      ($_.FileSystemRights.ToString() -match 'Write|Modify|FullControl|Delete|TakeOwnership|ChangePermissions')
    }
    if ($bad) { Fail "ordinary users can change $p (the elevated service would not be safe)" } else { Ok "only administrators can change $p" }
  }
  $private = Get-Acl (Join-Path $AdminData "private")
  if ($private.Access | Where-Object { $_.IdentityReference.Value -match '(?i)\\Users$|^Everyone$|Authenticated Users' }) { Fail "private\ is readable by ordinary users" } else { Ok "private\ (passphrase key) is closed to ordinary users" }
}

function Remove-Admin {
  if (-not (Is-Admin)) { throw "removing the admin service needs an elevated window" }
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Ok "removed task $TaskName"
  } else { Skip "task $TaskName was not installed" }
  $t = Join-Path $AdminData "transcript.jsonl"
  if (Test-Path $t) {
    New-Item -ItemType Directory "$Root\data\logs" -Force | Out-Null
    Copy-Item $t "$Root\data\logs\admin_transcript-$(Get-Date -Format yyyyMMdd).jsonl"
    Ok "audit transcript kept in $Root\data\logs"
  }
  foreach ($p in $AdminCode, $AdminData) { if (Test-Path $p) { Remove-Item $p -Recurse -Force; Ok "removed $p" } }
}

# ---------------------------------------------------------------------------------------------------------------------------
# Entry points that only touch the admin service
# ---------------------------------------------------------------------------------------------------------------------------
if ($AdminOnly -or $ResetAdmin) {
  if (-not (Is-Admin)) {                                        # asked from a normal window: one Windows permission prompt, then the same thing elevated
    $more = if ($ResetAdmin) { " -ResetAdmin" } else { "" }
    $p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -AdminOnly$more"
    exit $p.ExitCode
  }
  try { Install-Admin } catch { Fail $_.Exception.Message }
  if ($script:Problems.Count) { Say ""; Say "The admin service is NOT ready:"; $script:Problems | ForEach-Object { Say "  - $_" }; Read-Host "Press Enter to close" | Out-Null; exit 1 }
  Say ""; Say "Admin service ready. Ask for an admin task with:  python -m jarvis_node.adminclient run <verb> name=value"
  Read-Host "Press Enter to close" | Out-Null
  exit 0
}
if ($RemoveAdminOnly) {                                          # the elevated half of -Uninstall
  try { Remove-Admin } catch { Fail $_.Exception.Message }
  Read-Host "Press Enter to close" | Out-Null
  exit $script:Problems.Count
}
if ($Uninstall) {
  Head "Uninstall (data, models and the face stay)"
  foreach ($s in "install-autostart.ps1", "install-watchdog.ps1", "install-daily-report.ps1") { try { & (Join-Path $PSScriptRoot $s) -Remove } catch { Say "  $s : $($_.Exception.Message)" } }
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Start-Process powershell.exe -Verb RunAs -Wait -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -RemoveAdminOnly"
    Ok "admin service removed"
  }
  & (Join-Path $PSScriptRoot "jarvis-stop.ps1") 2>$null
  exit 0
}

# ---------------------------------------------------------------------------------------------------------------------------
# User part
# ---------------------------------------------------------------------------------------------------------------------------
Head "1  Checks"
if ($PSVersionTable.PSVersion.Major -lt 5) { throw "PowerShell 5.1 or newer is needed" }
if ([Environment]::OSVersion.Version.Build -lt 17763) { throw "Windows 10 version 1809 or newer is needed" }
if (-not (Test-Path "D:\")) { throw "JARVIS keeps everything large on drive D:, and there is no D: on this PC" }
if ($Verify) { $NoModels = $true; $NoFace = $true }
$freeD = Free-GB "D"
$need = if ($NoModels) { 6 } else { 15 }
if ($freeD -lt $need) { throw "D: has $freeD GB free; about $need GB are needed" }
Ok "Windows build $([Environment]::OSVersion.Version.Build), D: has $freeD GB free"
if (Is-Admin) { Say "  note  this window is elevated; the user part is meant to run as you. It will still work." }

Head "2  Folders and secrets"
foreach ($d in "$Root", "$Root\data", "$Root\data\logs", "$Root\models", "$Root\cache", "$Root\tools", "$Root\downloads", "$Root\vault", "$Secrets", "$Secrets\providers", "$Secrets\kernel") {
  if (-not (Test-Path $d)) { New-Item -ItemType Directory $d -Force | Out-Null }
}
Ok "$Root and $Secrets exist"
# Existence and size only: the content of a key file is never read here.
$dahl = Get-Item "$Secrets\providers\dahl.key" -ErrorAction SilentlyContinue
if ($dahl -and $dahl.Length -gt 0) { Ok "model key file is in place (content not read)" }
else { $script:Todo.Add("Copy your model key FILE to $Secrets\providers\dahl.key (copy the file; never paste a key into a chat or a command)") }
$tok = Get-Item "$Secrets\kernel\owner.token" -ErrorAction SilentlyContinue
if ($tok -and $tok.Length -gt 0) { Ok "kernel owner token is in place (content not read)" }
else { $script:Todo.Add("Put the kernel owner token file at $Secrets\kernel\owner.token (docs\05_OPERATIONS.md, 'Console': issue it once on the server, copy the file, delete the server copy)") }

Head "3  Python and the environments"
function Find-Python311 {
  $cands = @()
  try { $p = (& py.exe -3.11 -c "import sys;print(sys.executable)" 2>$null); if ($LASTEXITCODE -eq 0 -and $p) { $cands += $p.Trim() } } catch { }
  $cands += "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe", "$env:ProgramFiles\Python311\python.exe"
  $cands | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}
$pyExe = Find-Python311
if (-not $pyExe) {
  Say "  installing Python 3.11 for this user (winget)"
  Run-Exe winget.exe @("install", "--id", "Python.Python.3.11", "--scope", "user", "--exact", "--silent", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity")
  $pyExe = Find-Python311
}
if (-not $pyExe) { throw "Python 3.11 could not be found or installed" }
Ok "Python 3.11: $pyExe"
. (Join-Path $PSScriptRoot "d-env.ps1")        # TEMP, TMP and every cache on D: (owner rule)
foreach ($v in @(@("agent-venv", "agent-venv.txt"), @("jarvis-venv", "jarvis-venv.txt"), @("privacy-venv", "privacy-venv.txt"), @("piper-venv", "piper-venv.txt"))) {
  $dir = "$Root\tools\$($v[0])"; $vpy = "$dir\Scripts\python.exe"; $req = Join-Path $jarvis "tools\requirements\$($v[1])"
  if ($Verify) { if (Test-Path $vpy) { Ok "$($v[0]) present" } else { Fail "$($v[0]) missing" }; continue }
  try {
    if (-not (Test-Path $vpy)) { Run-Exe $pyExe @("-m", "venv", $dir) }
    $marker = "$dir\.jarvis-requirements.sha256"
    $hash = (Get-FileHash $req -Algorithm SHA256).Hash
    if ((Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq $hash)) { Skip "$($v[0]) is up to date"; continue }
    Say "  installing $($v[0]) (a few minutes)"
    Run-Exe $vpy @("-m", "pip", "install", "--disable-pip-version-check", "--quiet", "-r", $req)
    if ($v[0] -eq "privacy-venv") { Run-Exe $vpy @("-m", "spacy", "download", "en_core_web_sm-3.8.0", "--direct") }
    Set-Content $marker $hash
    Ok "$($v[0]) ready"
  } catch { Fail "$($v[0]): $($_.Exception.Message)" }
}

Head "4  Console web UI"
$ui = Join-Path $jarvis "ui"
if (Test-Path (Join-Path $ui "dist\index.html")) { Skip "the UI is already built" }
elseif ($Verify) { Fail "the console UI is not built" }
else {
  try {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
      Say "  installing Node.js LTS (winget)"
      Run-Exe winget.exe @("install", "--id", "OpenJS.NodeJS.LTS", "--scope", "user", "--exact", "--silent", "--accept-package-agreements", "--accept-source-agreements", "--disable-interactivity")
      $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
    }
    Push-Location $ui
    Run-Exe npm.cmd @("ci", "--no-audit", "--no-fund")
    Run-Exe npm.cmd @("run", "build")
    Pop-Location
    Ok "UI built"
  } catch { Pop-Location -ErrorAction SilentlyContinue; Fail "console UI: $($_.Exception.Message)" }
}

Head "5  Local models and tools (pinned, checksum-verified)"
if ($NoModels) { Skip "skipped (-NoModels)" }
else {
  # Essentials only. The rest of jarvis.lock.json is fetched on demand: python jarvis\tools\fetch.py --list --machine laptop
  $want = "llama-swap-v261-windows-amd64", "llama-cpp-b11714-win-vulkan-x64", "llama-cpp-b11714-win-cpu-x64", "qwen3-4b-2507-q4km", "qwen3-1.7b-q4km",
          "bge-small-en-v1.5-q8", "gitleaks-v8.30.1-windows-x64"
  $lock = Get-Content (Join-Path $jarvis "tools\jarvis.lock.json") -Raw | ConvertFrom-Json
  foreach ($name in $want) {
    $e = $lock.entries | Where-Object { $_.name -eq $name } | Select-Object -First 1
    if (-not $e) { Fail "$name is not in jarvis.lock.json"; continue }
    try {
      Run-Exe $pyExe @((Join-Path $jarvis "tools\fetch.py"), "--fetch", $name, "--root", $Root) | Out-Null
      if ($e.kind -eq "archive" -and $e.unpack_to -and $e.filename -like "*.zip") {
        $to = Join-Path $Root $e.unpack_to
        if (-not (Test-Path $to) -or -not (Get-ChildItem $to -ErrorAction SilentlyContinue)) {
          New-Item -ItemType Directory $to -Force | Out-Null
          Expand-Archive -Path (Join-Path $Root $e.dest) -DestinationPath $to -Force
        }
      }
      Ok $name
    } catch { Fail "${name}: $($_.Exception.Message)" }
  }
  Say ("  D: now has " + (Free-GB "D") + " GB free")
}

Head "6  The face"
$faceExe = @($env:JARVIS_FACE_EXE, 'D:\APPS\jarvis\jarvis.exe', "$env:LOCALAPPDATA\Programs\JARVIS\JARVIS.exe", "$env:LOCALAPPDATA\Programs\jarvis\jarvis.exe") |
  Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if ($NoFace) { Skip "skipped (-NoFace)" }
elseif ($faceExe) { Skip "the face is installed: $faceExe" }
else {
  try {
    if (-not $FaceInstaller) {
      $FaceInstaller = Get-ChildItem (Join-Path $jarvis "face\apps\stage-tamagotchi\dist") -Filter "*-setup.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $FaceInstaller) {
      Say "  building the face from source (Node 24 + pnpm; several minutes)"
      if (-not (Get-Command pnpm.cmd -ErrorAction SilentlyContinue)) { Run-Exe npm.cmd @("install", "--global", "pnpm") }
      Push-Location (Join-Path $jarvis "face")
      Run-Exe pnpm.cmd @("install", "--frozen-lockfile")
      Run-Exe pnpm.cmd @("run", "build:win")
      Pop-Location
      $FaceInstaller = Get-ChildItem (Join-Path $jarvis "face\apps\stage-tamagotchi\dist") -Filter "*-setup.exe" | Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $FaceInstaller) { throw "no face installer was produced" }
    Say "  installing the face to D:\APPS\jarvis (silent; it does not start by itself)"
    $p = Start-Process $FaceInstaller -ArgumentList "/S", "/D=D:\APPS\jarvis" -Wait -PassThru      # a GUI program: & would not wait for it
    if ($p.ExitCode -ne 0) { throw "the face installer exited with $($p.ExitCode)" }
    Ok "face installed"
  } catch { Pop-Location -ErrorAction SilentlyContinue; Fail "face: $($_.Exception.Message)" }
}

Head "7  Start at login, then start"
if ($Verify) { Skip "(verify only)" }
else {
  foreach ($s in "install-autostart.ps1", "install-watchdog.ps1", "install-daily-report.ps1") {
    try { & (Join-Path $PSScriptRoot $s) | Out-Host; Ok $s } catch { Fail "${s}: $($_.Exception.Message)" }
  }
  if ($NoStart) { Skip "not started (-NoStart)" }
  else {
    try { & (Join-Path $PSScriptRoot "jarvis-start.ps1") | Out-Host } catch { Fail "start: $($_.Exception.Message)" }
  }
}

if ($Verify) {
  Head "Admin service"
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Ok "task $TaskName exists"; Test-AdminAcl } else { Skip "not installed" }
}
elseif ($NoAdmin) { Head "8  Admin service"; Skip "skipped (-NoAdmin); install later with: Install-JARVIS.ps1 -AdminOnly (from an elevated window)" }
else {
  Head "8  Admin service (second admin)"
  Say "  Windows will ask once for permission. JARVIS then gets a service that can run ONLY the admin verbs you approve, each time"
  Say "  with your passphrase. You can say no and install it later."
  $a = Read-Host "  Install it now? [Y/n]"
  if ($a -match '^(n|no)$') { Skip "not installed" }
  else {
    try {
      $p = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -AdminOnly"
      if ($p.ExitCode -eq 0) { Ok "admin service installed" } else { Fail "the admin service was not installed (exit $($p.ExitCode))" }
    } catch { Fail "the admin service was not installed: $($_.Exception.Message)" }
  }
}

Head "Result"
if ($script:Problems.Count) { Say "Problems:"; $script:Problems | ForEach-Object { Write-Host "  - $_" -ForegroundColor Red } } else { Write-Host "  no problems" -ForegroundColor Green }
if ($script:Todo.Count) { Say "Still to do by hand:"; $script:Todo | ForEach-Object { Say "  - $_" } }
Say ""
Say "Check everything:  $Root\tools\agent-venv\Scripts\python.exe jarvis\scripts\preflight.py"
Say "Console: http://127.0.0.1:8799/   (approvals are given there with your PIN)"
if ($script:Problems.Count) { exit 1 }
