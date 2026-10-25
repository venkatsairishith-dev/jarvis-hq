# Closes the windows the node recorded as opened by JARVIS (launched-windows.json) since $Since (epoch seconds). Only recorded handles; nothing else.
param([double]$Since = 0)
Add-Type -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
[DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
'@ -Name CL -Namespace Close
$path = Join-Path $env:LOCALAPPDATA 'jarvis\run\launched-windows.json'
if (-not (Test-Path $path)) { "no registry"; exit 0 }
$entries = Get-Content $path -Raw | ConvertFrom-Json
$n = 0
foreach ($e in @($entries)) {
  if ([double]$e.t -lt $Since) { continue }
  $h = [IntPtr][int64]$e.hwnd
  if ([Close.CL]::IsWindow($h)) { [void][Close.CL]::SendMessage($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero); $n++; "closed $($e.proc) $($e.hwnd)" }
}
Start-Sleep 1
"closed $n recorded window(s)"
