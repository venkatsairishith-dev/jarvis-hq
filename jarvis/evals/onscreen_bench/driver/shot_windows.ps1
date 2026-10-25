param([string]$ProcName = "jarvis", [string]$Out = "C:\Users\venkat\AppData\Local\Temp\claude\d--jarvis-hq-neko\2df73532-30c6-4bc7-9332-a7aee7af8fc9\scratchpad\shots")
Add-Type -AssemblyName System.Drawing
Add-Type -MemberDefinition '[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();' -Name DPI -Namespace Shot
[void][Shot.DPI]::SetProcessDPIAware()
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text; using System.Collections.Generic;
public class W {
  public delegate bool EnumProc(IntPtr h, IntPtr l);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr dc, uint flags);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  public static List<IntPtr> ForPids(HashSet<uint> pids) {
    var res = new List<IntPtr>();
    EnumWindows((h, l) => { uint pid; GetWindowThreadProcessId(h, out pid); if (pids.Contains(pid) && IsWindowVisible(h)) res.Add(h); return true; }, IntPtr.Zero);
    return res;
  }
}
"@
New-Item -ItemType Directory -Force $Out | Out-Null
$pids = New-Object 'System.Collections.Generic.HashSet[uint32]'
Get-Process $ProcName -ErrorAction SilentlyContinue | % { [void]$pids.Add([uint32]$_.Id) }
$i = 0
foreach ($h in [W]::ForPids($pids)) {
  $r = New-Object W+RECT
  [void][W]::GetWindowRect($h, [ref]$r)
  $w = $r.R - $r.L; $ht = $r.B - $r.T
  if ($w -lt 40 -or $ht -lt 40) { continue }
  $sb = New-Object System.Text.StringBuilder 256; [void][W]::GetWindowText($h, $sb, 256)
  $bmp = New-Object System.Drawing.Bitmap $w, $ht
  $g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
  [void][W]::PrintWindow($h, $dc, 2)
  $g.ReleaseHdc($dc); $g.Dispose()
  $f = Join-Path $Out ("win$i.png"); $bmp.Save($f, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
  "{0} {1}x{2} at ({3},{4}) title='{5}'" -f $f, $w, $ht, $r.L, $r.T, $sb.ToString()
  $i++
}
if ($i -eq 0) { "no visible window of $ProcName" }
