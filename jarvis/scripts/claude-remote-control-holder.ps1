# Standalone `claude remote-control` session for the laptop (laptop 2026-10-28): lives outside VS Code, so closing or restarting VS Code does not drop the cloud session's messages.
# Start it detached from any terminal:  Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = 'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File D:jarvishqjarvisscriptsaude-remote-control-holder.ps1'; CurrentDirectory = 'D:jarvishq' }
# Stop it: end the powershell holder and its claude.exe child. Mode: bypassPermissions (same as the VS Code session). Not started at boot (no scheduled task).
# Holds one `claude remote-control` session: starts it with stdin/stdout piped, answers its one-time y/n consent, then keeps stdin open and copies its output to a log.
$log = 'D:\jarvis\cache\claude-remote-control.log'
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = 'C:\Users\venkat\.local\bin\claude.exe'
$psi.Arguments = 'remote-control --name jarvis-laptop-standalone --permission-mode bypassPermissions --spawn same-dir --no-chrome --verbose --debug-file D:\jarvis\cache\claude-remote-control.debug.log'
$psi.WorkingDirectory = 'D:\jarvis\hq'
$psi.UseShellExecute = $false; $psi.CreateNoWindow = $true
$psi.RedirectStandardInput = $true; $psi.RedirectStandardOutput = $true; $psi.RedirectStandardError = $true
$p = [System.Diagnostics.Process]::Start($psi)
$w = [System.IO.StreamWriter]::new($log, $true); $w.AutoFlush = $true; $md = [pscustomobject]@{ Writer = $w; Started = Get-Date }
$h = { param($s,$e) if ($e.Data -ne $null) { $script:w.WriteLine($e.Data) } }
Register-ObjectEvent -InputObject $p -EventName OutputDataReceived -Action { if (((Get-Date) - $Event.MessageData.Started).TotalSeconds -lt 90) { $Event.MessageData.Writer.WriteLine($EventArgs.Data) } } -MessageData $md | Out-Null
Register-ObjectEvent -InputObject $p -EventName ErrorDataReceived  -Action { if (((Get-Date) - $Event.MessageData.Started).TotalSeconds -lt 90) { $Event.MessageData.Writer.WriteLine($EventArgs.Data) } } -MessageData $md | Out-Null
$p.BeginOutputReadLine(); $p.BeginErrorReadLine()
Start-Sleep -Seconds 6
$p.StandardInput.WriteLine('y'); $p.StandardInput.Flush()
$w.WriteLine("[holder] answered y at $(Get-Date -Format s); claude pid $($p.Id); holder pid $PID")
while (-not $p.HasExited) { Start-Sleep -Milliseconds 500 }; Start-Sleep -Seconds 1
$w.WriteLine("[holder] claude exited with code $($p.ExitCode) at $(Get-Date -Format s)")


