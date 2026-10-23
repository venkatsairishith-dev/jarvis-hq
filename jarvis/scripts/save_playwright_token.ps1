# Windows. Save one Chrome profile's Playwright-extension connection token so JARVIS can connect to that profile without the
# "Allow & select" prompt each time. The OWNER runs this; the token is never shown, logged or sent anywhere.
#
#   1. In the Chrome profile, click the Playwright MCP extension icon and press "Copy" next to the token
#      (the extension shows one token per profile).
#   2. powershell -File jarvis\scripts\save_playwright_token.ps1 -Profile "Profile 1"      (folder name: Default, Profile 1, ...)
#
# The token goes from the clipboard straight into D:\jarvis-secrets\playwright\extension-token-<folder>.txt (readable
# only by you), then the clipboard is cleared. Only its length is printed. To undo: delete that file.
param([Parameter(Mandatory = $true)][string]$Profile)
Add-Type -AssemblyName System.Windows.Forms
$dir = "D:\jarvis-secrets\playwright"
$name = "extension-token-" + ($Profile -replace '[^A-Za-z0-9]+', '_') + ".txt"
$path = Join-Path $dir $name
$tok = [System.Windows.Forms.Clipboard]::GetText().Trim()
if (-not $tok -or $tok.Length -lt 16 -or $tok -match '\s') {
  "The clipboard does not hold a token (copy it from the extension first). Nothing was saved."
  exit 1
}
New-Item -ItemType Directory $dir -Force | Out-Null
[System.IO.File]::WriteAllText($path, $tok)
$tok = $null
[System.Windows.Forms.Clipboard]::Clear()
# only the owner (and SYSTEM) may read it
$acl = Get-Acl $path
$acl.SetAccessRuleProtection($true, $false)
foreach ($id in @("$env:USERDOMAIN\$env:USERNAME", "NT AUTHORITY\SYSTEM")) {
  $acl.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($id, "FullControl", "Allow")))
}
Set-Acl $path $acl
"saved $name ($((Get-Item $path).Length) characters); clipboard cleared"
