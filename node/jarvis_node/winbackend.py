"""Real Windows backend for the controller. Runs ONLY after the kernel authorized the action.
Values reach PowerShell through environment variables, never by string interpolation, so they cannot inject code.
Targets look like "process|element" where element is an AutomationId or exact element name.
"""
import os
import re
import subprocess

LAUNCH_ALLOWLIST = {"notepad": "notepad.exe", "calc": "calc.exe", "mspaint": "mspaint.exe"}
REASONS = ("no window", "no element", "password field", "target is not the foreground window", "does not support", "pattern")
_SAFE = re.compile(r"^[A-Za-z0-9_.\- ]{1,80}$")

FIND = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$p = Get-Process -Name $env:JARVIS_UI_PROC | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { throw 'no window' }
$root = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
$cond = New-Object System.Windows.Automation.OrCondition(
  (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, $env:JARVIS_UI_ELEM)),
  (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $env:JARVIS_UI_ELEM)))
$el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
if (-not $el) { throw 'no element' }
"""
CLICK = FIND + "($el.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)).Invoke()\n"
TYPE = FIND + r"""
$pat = $null
if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pat) -and -not $el.Current.IsPassword) {
  $pat.SetValue($env:JARVIS_UI_TEXT)          # best rung: structured value
} else {
  # fallback rung: keyboard. Only if the target app really owns the foreground window, so keys cannot land elsewhere.
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type @'
using System; using System.Runtime.InteropServices;
public class FW { [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h); }
'@
  if ($el.Current.IsPassword) { throw 'password field' }
  try { $el.SetFocus() } catch { [void][FW]::SetForegroundWindow($p.MainWindowHandle) }
  Start-Sleep -Milliseconds 400
  $fpid = 0; [void][FW]::GetWindowThreadProcessId([FW]::GetForegroundWindow(), [ref]$fpid)
  if ($fpid -ne $p.Id) { throw 'target is not the foreground window' }
  $t = [regex]::Replace($env:JARVIS_UI_TEXT, '[\+\^%~\(\)\{\}\[\]]', '{$0}')
  [System.Windows.Forms.SendKeys]::SendWait($t)
}
"""


def _split(target: str):
    proc, _, elem = target.partition("|")
    if not _SAFE.match(proc) or not elem or len(elem) > 120 or "\n" in elem:
        raise ValueError("bad target")
    return proc, elem


def _run(script, env):
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True, text=True,
                       encoding="utf-8", timeout=30, env={**os.environ, **env})
    if r.returncode:
        # Only fixed reason codes raised by our own scripts are passed on; other error text could contain screen content.
        text = (r.stderr or "") + (r.stdout or "")
        if os.environ.get("JARVIS_UI_DEBUG"):  # developer switch: print the raw error locally, never to the kernel or logs
            import sys
            print("UI-DEBUG:", text.strip()[:400], file=sys.stderr)
        code =next((c for c in REASONS if c in text), "other")
        raise RuntimeError("ui-action-failed:" + code)


def launch(target, params):
    exe = LAUNCH_ALLOWLIST.get(target.strip().lower())
    if not exe:
        raise ValueError("app not in the launch allowlist")
    subprocess.Popen([exe], close_fds=True)


def click(target, params):
    proc, elem = _split(target)
    _run(CLICK, {"JARVIS_UI_PROC": proc, "JARVIS_UI_ELEM": elem})


def type_text(target, params):
    proc, elem = _split(target)
    text = str(params.get("text", ""))
    if len(text) > 2000:
        raise ValueError("text too long")
    _run(TYPE, {"JARVIS_UI_PROC": proc, "JARVIS_UI_ELEM": elem, "JARVIS_UI_TEXT": text})


BACKEND = {"launch": launch, "click": click, "type": type_text}
