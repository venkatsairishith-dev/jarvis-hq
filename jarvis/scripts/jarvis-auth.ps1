# Shared helpers for the JARVIS servers' credentials (Windows PowerShell). Dot-source it:  . (Join-Path $PSScriptRoot 'jarvis-auth.ps1')
#
# Each server (console 8799, widget 48940, wake 48945, speech 48930) writes its MASTER token to <name>.token in the per-user run folder
# %LOCALAPPDATA%\jarvis\run (per-user by default). First-party scripts read it and send it in the server's header. The browser never
# gets a token in a page: Get-LaunchUrl asks the server for a ONE-TIME launch code (valid 60 s, used once) and returns the URL with
# the code in the fragment, which the login page exchanges for an HttpOnly session cookie (jarvis/scripts/control_auth.py).
# A program of the same Windows user can read these files, as it can read the browser profile: this blocks other users, other sites
# and programs that cannot read the file; the owner's PIN stays the boundary for approvals.
function Get-JarvisRunDir { if ($env:JARVIS_RUN_DIR) { $env:JARVIS_RUN_DIR } else { Join-Path $(if ($env:LOCALAPPDATA) { $env:LOCALAPPDATA } else { Join-Path $HOME "AppData\Local" }) "jarvis\run" } }
function Get-JarvisToken([string]$Name) {
  $f = Join-Path (Get-JarvisRunDir) "$Name.token"
  if (Test-Path $f) { (Get-Content $f -Raw).Trim() } else { $null }
}
function Get-JarvisHeaders([string]$Name) {
  $t = Get-JarvisToken $Name
  if (-not $t) { return @{} }
  $header = if ($Name -eq "console") { "X-Jarvis-Console" } else { "X-Jarvis-Control" }
  @{ $header = $t }
}
function Get-LaunchUrl([string]$Name, [int]$Port, [string]$Path = "/") {
  $url = "http://127.0.0.1:$Port$Path"
  $h = Get-JarvisHeaders $Name
  if ($h.Count -eq 0) { return $url }      # no token file: the login page says how to open it
  try {
    $r = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/auth/code" -Headers $h -Body "{}" -ContentType "application/json" -TimeoutSec 10
    return "$url#code=$($r.code)"
  } catch { return $url }
}
