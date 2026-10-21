# Run the JARVIS x N.E.K.O test suites on the laptop. Everything stays on D: (temp and caches).
#   dev-test.ps1 quick   chat frontend (vitest) + main_agent + jarvis_bridge + jarvis console   (about 6 min)
#   dev-test.ps1 full    quick + desktop + caretaker/tools + the kernel unit tests               (about 40 min)
param([ValidateSet('quick','full')][string]$Mode = 'quick')
. (Join-Path $PSScriptRoot "d-env.ps1")
$repo = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$nv = 'D:\jarvis\tools\neko-venv\Scripts\python.exe'; $jv = 'D:\jarvis\tools\jarvis-venv\Scripts\python.exe'
$fail = 0
function Step($name, [scriptblock]$cmd) {
  "=== $name"; & $cmd 2>&1 | Select-Object -Last 3
  if ($LASTEXITCODE -ne 0) { "FAILED: $name (exit $LASTEXITCODE)"; $script:fail++ }
}
Set-Location $repo
Step 'chat frontend (vitest)' { Push-Location frontend\react-neko-chat; npx vitest run; $c = $LASTEXITCODE; Pop-Location; exit $c }
Step 'main_agent'   { & $nv -m pytest plugin/plugins/main_agent -q -p no:cacheprovider }
Step 'jarvis_bridge' { & $nv -m pytest plugin/plugins/jarvis_bridge -q -p no:cacheprovider }
Step 'jarvis console' { & $nv -m pytest jarvis/console -q -p no:cacheprovider }
if ($Mode -eq 'full') {
  Step 'desktop'           { & $nv -m pytest plugin/plugins/desktop -q -p no:cacheprovider }
  Step 'caretaker + tools' { & $nv -m pytest jarvis/caretaker jarvis/tools -q -p no:cacheprovider }
  Step 'kernel (unittest)' { Push-Location jarvis\kernel; & $jv -m unittest discover -s tests; $c = $LASTEXITCODE; Pop-Location; exit $c }
}
"`n$fail suite(s) failed"; exit $fail
