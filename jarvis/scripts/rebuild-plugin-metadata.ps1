# Rebuild the packaged metadata (plugin.meta.json) of bundled plugins whose snapshot the host refuses. Windows laptop only:
# the snapshot records the machine that built it, and the laptop is where Neko runs.
#
#   powershell -ExecutionPolicy Bypass -File jarvis\scripts\rebuild-plugin-metadata.ps1            # every refused plugin
#   powershell -ExecutionPolicy Bypass -File jarvis\scripts\rebuild-plugin-metadata.ps1 research   # just these
#
# Per plugin: a git-ignored .vscode folder inside the plugin is MOVED (never deleted) to D:\jarvis\backup\plugin-vscode\<id>,
# because the fingerprint would include it and the snapshot would then only match on this machine; then the project's own
# packager builds it ("neko-plugin build <id> --keep-staging"), the staged plugin.meta.json is copied back, the staging and
# the package are removed, and check_plugin_metadata.py must report the plugin as OK. Stop N.E.K.O first (neko-stop.ps1).
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Plugins)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$venv = "D:\jarvis\tools\neko-venv"
$py = Join-Path $venv "Scripts\python.exe"
$env:UV_PROJECT_ENVIRONMENT = $venv; $env:UV_NO_SYNC = "1"; $env:PYTHONUTF8 = "1"
$work = "D:\jarvis-apps\tmp\plugin-meta-build"; New-Item -ItemType Directory -Force $work | Out-Null
Set-Location $repo

if (-not $Plugins) {
    $report = & $py jarvis\scripts\check_plugin_metadata.py --json | ConvertFrom-Json
    $Plugins = @($report.plugins | Where-Object { -not $_.accepted } | ForEach-Object { $_.plugin })
}
if (-not $Plugins) { Write-Host "Nothing to rebuild: every snapshot is accepted."; exit 0 }
Write-Host "Rebuilding: $($Plugins -join ', ')"

foreach ($id in $Plugins) {
    $dir = Join-Path $repo "plugin\plugins\$id"
    if (-not (Test-Path (Join-Path $dir "plugin.toml"))) { Write-Warning "$id: no plugin.toml, skipped"; continue }
    $vs = Join-Path $dir ".vscode"
    if ((Test-Path $vs) -and (git check-ignore -q "plugin/plugins/$id/.vscode"; $LASTEXITCODE -eq 0)) {
        $dest = "D:\jarvis\backup\plugin-vscode\$id-$(Get-Date -Format yyyyMMdd-HHmmss)"
        New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
        Move-Item $vs $dest
        Write-Host "  moved $id\.vscode -> $dest"
    }
    $out = Join-Path $work "$id.neko-plugin"
    $log = & $py -m plugin.neko_plugin_cli.cli build $id --keep-staging --out $out 2>&1
    $log | Write-Host
    $staging = ($log | Select-String -Pattern "staging_dir=(.+)$" | Select-Object -First 1).Matches.Groups[1].Value
    if (-not $staging -or -not (Test-Path $staging)) { Write-Warning "$id: build gave no staging dir"; continue }
    $meta = Get-ChildItem $staging -Recurse -Filter plugin.meta.json |
        Where-Object { Test-Path (Join-Path $_.DirectoryName "plugin.toml") } |
        Where-Object { (Get-Content (Join-Path $_.DirectoryName "plugin.toml") -Raw) -match "(?m)^id\s*=\s*`"$id`"" } |
        Select-Object -First 1
    if (-not $meta) { Write-Warning "$id: no staged plugin.meta.json found under $staging"; continue }
    Copy-Item $meta.FullName (Join-Path $dir "plugin.meta.json") -Force
    Remove-Item $staging -Recurse -Force; Remove-Item $out -Force -ErrorAction SilentlyContinue
    Write-Host "  $id: plugin.meta.json updated"
}

& $py jarvis\scripts\check_plugin_metadata.py @Plugins
if ($LASTEXITCODE -ne 0) { Write-Warning "Some snapshots are still refused: see above."; exit 1 }
Write-Host "All rebuilt snapshots are accepted. Commit them: git add plugin/plugins/*/plugin.meta.json"
