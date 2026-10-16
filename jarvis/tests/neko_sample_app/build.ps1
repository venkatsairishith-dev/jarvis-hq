# Windows. Builds the Neko sample app with the .NET Framework compiler that ships with Windows (nothing installed), writes a
# BROKEN config.json (so it shows its error dialog), and adds a Start-menu shortcut in "Neko Tests" so open_app can start it.
#   powershell -File build.ps1            build + broken config + shortcut
#   powershell -File build.ps1 -Remove    remove the shortcut and the built folder again
#   powershell -File build.ps1 -Canary    build only NekoCrashCanary.exe (crashes on purpose, no popup) for the monitor test
param([string]$Out = "D:\jarvis\tests\neko-sample-app", [switch]$Remove, [switch]$Canary)
if ($Canary) {
  $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
  New-Item -ItemType Directory $Out -Force | Out-Null
  & $csc /nologo /target:exe /out:"$Out\NekoCrashCanary.exe" (Join-Path $PSScriptRoot "NekoCrashCanary.cs")
  if ($LASTEXITCODE -ne 0) { throw "compile failed" }
  "built $Out\NekoCrashCanary.exe"
  return
}
$startDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Neko Tests"
if ($Remove) {
  if (Test-Path $startDir) { Remove-Item $startDir -Recurse -Force; "removed $startDir" }
  if (Test-Path $Out) { Remove-Item $Out -Recurse -Force; "removed $Out" }
  return
}
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw "the .NET Framework 4 compiler is missing: $csc" }
New-Item -ItemType Directory $Out -Force | Out-Null
& $csc /nologo /target:winexe /out:"$Out\NekoSampleApp.exe" /reference:System.Web.Extensions.dll /reference:System.Windows.Forms.dll `
  (Join-Path $PSScriptRoot "NekoSampleApp.cs")
if ($LASTEXITCODE -ne 0) { throw "compile failed" }
# the reproducible fault: a typo makes "port" not a number
Set-Content -Path "$Out\config.json" -Value "{`n  `"name`": `"neko sample`",`n  `"port`": 80x80`n}" -Encoding ascii
New-Item -ItemType Directory $startDir -Force | Out-Null
$sh = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startDir "Neko Sample App.lnk"))
$sh.TargetPath = "$Out\NekoSampleApp.exe"; $sh.WorkingDirectory = $Out; $sh.Save()
"built $Out\NekoSampleApp.exe; config.json is broken on purpose; Start menu: Neko Tests\Neko Sample App"
