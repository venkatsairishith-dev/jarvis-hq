"""Read-only UI observation for Windows via UI Automation (the accessibility tree: ladder rung above vision and pixels).
Nothing here clicks or types. Window titles and element names are scrubbed for secret patterns and stay on the laptop
unless a task is approved to send them. Uses only built-in .NET/PowerShell: no extra dependencies.
"""
import json
import subprocess
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "kernel"))

PS = r"""
$ErrorActionPreference='SilentlyContinue'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Runtime.InteropServices;
public class W { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); }
'@
$procName = '__PROC__'; $maxN = __MAX__; $maxD = __DEPTH__; $wantValues = __VALUES__
if ($procName -eq '') { $h = [W]::GetForegroundWindow() }
else { $p = Get-Process -Name $procName | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; $h = if ($p) { $p.MainWindowHandle } else { [IntPtr]::Zero } }
if ($h -eq [IntPtr]::Zero) { '{"error":"no window"}'; exit }
$root = [System.Windows.Automation.AutomationElement]::FromHandle($h)
$walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
$out = New-Object System.Collections.ArrayList
function Walk($el, $depth) {
  if ($out.Count -ge $maxN -or $depth -gt $maxD) { return }
  $c = $walker.GetFirstChild($el)
  while ($c -ne $null -and $out.Count -lt $maxN) {
    $i = $c.Current
    $val = $null
    if ($wantValues -and -not $i.IsPassword) { try { $pat = $null
      if ($c.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pat)) { $val = $pat.Current.Value }
      elseif ($c.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pat)) { $val = $pat.DocumentRange.GetText(300) } } catch {} }
    [void]$out.Add([ordered]@{ depth=$depth; type=($i.ControlType.ProgrammaticName -replace 'ControlType\.',''); name=$i.Name; id=$i.AutomationId; enabled=$i.IsEnabled; offscreen=$i.IsOffscreen; value=$val })
    Walk $c ($depth+1)
    $c = $walker.GetNextSibling($c)
  }
}
Walk $root 1
$pid2 = $root.Current.ProcessId
[ordered]@{ window=$root.Current.Name; process=(Get-Process -Id $pid2).ProcessName; class=$root.Current.ClassName; elements=$out; truncated=($out.Count -ge $maxN) } | ConvertTo-Json -Depth 6 -Compress
"""


def snapshot(process: str = "", max_elements: int = 60, max_depth: int = 4, values: bool = False) -> dict:
    """Return a compact structured snapshot of one window (foreground if process is empty).
    values=True also reads text-field values (never for password fields), scrubbed and truncated."""
    if not all(c.isalnum() or c in "-_." for c in process):
        raise ValueError("bad process name")
    script = (PS.replace("__PROC__", process).replace("__MAX__", str(int(max_elements))).replace("__DEPTH__", str(int(max_depth)))
              .replace("__VALUES__", "$true" if values else "$false"))
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True, encoding="utf-8", timeout=60,
                       creationflags=0x08000000 if os.name == "nt" else 0)  # CREATE_NO_WINDOW: no flashing console
    snap = json.loads(r.stdout)
    from jarvis_kernel.scrub import scrub  # same scrubber as the gateway and the vault
    def clean(s):
        return scrub(s or "")[0] if s else s
    if "elements" in snap:
        snap["window"] = clean(snap.get("window"))
        for e in snap["elements"]:
            e["name"] = clean(e.get("name"))
            v = clean(e.get("value"))
            e["value"] = v if not isinstance(v, str) or len(v) <= 200 else v[:200] + "...[truncated]"
    return snap


if __name__ == "__main__":
    print(json.dumps(snapshot(sys.argv[1] if len(sys.argv) > 1 else ""), indent=1)[:4000])
