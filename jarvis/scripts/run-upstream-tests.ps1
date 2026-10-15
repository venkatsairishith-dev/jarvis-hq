# Run N.E.K.O's upstream unit tests the way this laptop needs. Windows. Does not touch the running stack or its data.
#
#   run-upstream-tests.ps1                       the whole tests\unit suite (about 30 minutes), 4 workers
#   run-upstream-tests.ps1 tests\unit\test_x.py  just those files / pytest arguments
#
# What it sets, and why (each was a real failure before):
#   UV_PROJECT_ENVIRONMENT + UV_NO_SYNC  tests that launch "uv run --no-sync python ..." must use the existing environment; without
#                                        this uv creates an EMPTY .venv in the repo, the workers lack httpx, and upstream's
#                                        launcher.py then switches into that .venv and fails to start the servers (see neko-start.ps1)
#   NODE_OPTIONS=--test-reporter=tap     Node 24 stopped printing the TAP lines some tests parse
#   a throw-away NEKO_STORAGE_SELECTED_ROOT, NEKO_LANGUAGE unset, TEMP on D:   tests never touch real data
# Known failures that remain on this machine and on untouched upstream: two avatar tests that need the Windows symlink privilege
# (Developer Mode), and test_pr3078_review_evidence::test_c1 (a missing method inside upstream's own code).
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PytestArgs)
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venv = "D:\jarvis\tools\neko-venv"
$env:UV_PROJECT_ENVIRONMENT = $venv
$env:UV_NO_SYNC = "1"
$env:PATH = "D:\jarvis\tools\uv;" + $env:PATH
$env:NODE_OPTIONS = "--test-reporter=tap"
$env:TEMP = "D:\jarvis-apps\tmp"; $env:TMP = "D:\jarvis-apps\tmp"
$env:PYTHONUTF8 = "1"
$env:NEKO_STORAGE_SELECTED_ROOT = "D:\jarvis-apps\tmp\neko-test-data"
Remove-Item Env:NEKO_LANGUAGE -ErrorAction SilentlyContinue
Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
Set-Location $repo
if (-not $PytestArgs) { $PytestArgs = @("tests/unit", "-n", "4", "--dist", "loadfile") }
$quiet = @()
if (-not ($PytestArgs | Where-Object { $_ -match '^-v+$|^--verbose$' })) { $quiet = @("-q") }   # -q would cancel a requested -v
& (Join-Path $venv "Scripts\python.exe") -m pytest @PytestArgs @quiet -p no:cacheprovider
$code = $LASTEXITCODE
if (Test-Path (Join-Path $repo ".venv")) { Write-Host "NOTE: a project .venv appeared in $repo; remove it if empty (neko-start.ps1 does)." }
exit $code
