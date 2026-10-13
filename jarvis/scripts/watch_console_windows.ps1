# Count distinct VISIBLE console windows (cmd, PowerShell, Windows Terminal host) that appear while this runs.
# Usage: powershell -File watch_console_windows.ps1 -Seconds 180
param([int]$Seconds = 120)
Add-Type @'
using System; using System.Text; using System.Collections.Generic; using System.Runtime.InteropServices;
public class WinWatch {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  public static List<string> Snapshot() {
    var r = new List<string>();
    EnumWindows((h, l) => {
      if (!IsWindowVisible(h)) return true;
      var sb = new StringBuilder(256); GetClassName(h, sb, 256); var c = sb.ToString();
      if (c == "ConsoleWindowClass" || c == "CASCADIA_HOSTING_WINDOW_CLASS" || c == "PseudoConsoleWindow") { uint pid; GetWindowThreadProcessId(h, out pid); r.Add(h.ToInt64() + "|" + c + "|" + pid); }
      return true; }, IntPtr.Zero);
    return r;
  }
}
'@
$seen = @{}
$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
  foreach ($w in [WinWatch]::Snapshot()) {
    $h = $w.Split('|')[0]
    if (-not $seen.ContainsKey($h)) {
      $procId = [int]$w.Split('|')[2]
      $name = (Get-Process -Id $procId -ErrorAction SilentlyContinue).ProcessName
      $seen[$h] = "{0} (pid {1}, owner {2})" -f $w.Split('|')[1], $procId, $name
    }
  }
  Start-Sleep -Milliseconds 40
}
"distinct visible console windows in ${Seconds}s: " + $seen.Count
$seen.Values | Group-Object | ForEach-Object { "  {0} x{1}" -f $_.Name, $_.Count }
