# Count (and group) the programs that start on this PC during a window of time, ignoring conhost and the WMI host.
# Usage: powershell -File count_new_programs.ps1 -Seconds 140
param([int]$Seconds = 120)
$known = @{}
Get-CimInstance Win32_Process | ForEach-Object { $known[$_.ProcessId] = 1 }
$rows = New-Object System.Collections.ArrayList
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
  foreach ($p in (Get-CimInstance Win32_Process)) {
    if (-not $known.ContainsKey($p.ProcessId)) {
      $known[$p.ProcessId] = 1
      if ($p.Name -notin 'conhost.exe', 'WmiPrvSE.exe') {
        $cmd = ([string]$p.CommandLine) -replace '\s+', ' '
        [void]$rows.Add($p.Name + '  <-  ' + $cmd.Substring(0, [Math]::Min(60, $cmd.Length)))
      }
    }
  }
  Start-Sleep -Milliseconds 150
}
"new programs in ${Seconds}s (excluding conhost and the WMI host): " + $rows.Count
$rows | Group-Object | Sort-Object Count -Descending | ForEach-Object { "{0,3}  {1}" -f $_.Count, $_.Name }
