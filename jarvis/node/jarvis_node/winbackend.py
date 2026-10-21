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
__PICK__
$root = [System.Windows.Automation.AutomationElement]::FromHandle($p.MainWindowHandle)
if ($env:JARVIS_UI_ELEM -eq '*') {   # "the window's first text field": Edit or Document, never a password field
  $AEc = [System.Windows.Automation.AutomationElement]; $CT = [System.Windows.Automation.ControlType]
  $cond = New-Object System.Windows.Automation.OrCondition(
    (New-Object System.Windows.Automation.PropertyCondition($AEc::ControlTypeProperty, $CT::Edit)),
    (New-Object System.Windows.Automation.PropertyCondition($AEc::ControlTypeProperty, $CT::Document)),
    (New-Object System.Windows.Automation.PropertyCondition($AEc::ClassNameProperty, 'Edit')),
    (New-Object System.Windows.Automation.PropertyCondition($AEc::ClassNameProperty, 'RichEdit20W')),
    (New-Object System.Windows.Automation.PropertyCondition($AEc::ClassNameProperty, 'RICHEDIT50W')))
  $el = @($root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond) | Where-Object { -not $_.Current.IsPassword })[0]
  if (-not $el) { throw 'no element' }
} else {
$cond = New-Object System.Windows.Automation.OrCondition(
  (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, $env:JARVIS_UI_ELEM)),
  (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $env:JARVIS_UI_ELEM)))
$el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
if (-not $el) { throw 'no element' }
}
"""
CLICK = FIND + r"""
$pat = $null
if ($el.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pat)) { $pat.Invoke() }
elseif ($el.Current.ClassName -in 'Button', 'CCPushButton' -and $el.Current.NativeWindowHandle -ne 0) {
  # a classic Win32 button (MessageBox OK) without UIA's Invoke: its own click message, still by element, not by position
  Add-Type @'
using System; using System.Runtime.InteropServices;
public class BM { [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l); }
'@
  [void][BM]::PostMessage([IntPtr]$el.Current.NativeWindowHandle, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)
} else { throw 'does not support Invoke' }
"""
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


READBACK = FIND + r"""
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public class RB { [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, StringBuilder l); }
'@
Start-Sleep -Milliseconds 200
$have = $null; $v = $null
if ($el.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$v)) { $have = $v.Current.Value }
if (-not $have -and $el.Current.NativeWindowHandle -ne 0) {
  $sb = New-Object System.Text.StringBuilder 65536
  [void][RB]::SendMessage([IntPtr]$el.Current.NativeWindowHandle, 0x000D, [IntPtr]65536, $sb)   # WM_GETTEXT
  $have = $sb.ToString()
}
if (-not $have -or -not $have.Contains($env:JARVIS_UI_TEXT)) { throw 'text not in field' }
"""
REASONS = REASONS + ("text not in field",)


def _split(target: str):
    proc, _, elem = target.partition("|")
    if not _SAFE.match(proc) or not elem or len(elem) > 120 or "\n" in elem:
        raise ValueError("bad target")
    return proc, elem


def _run(script, env):
    from . import runtime                                  # stoppable: a cancelled job kills this PowerShell
    r = runtime.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=30, env={**runtime.safe_environ(), **env})
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
    # bring the window forward the way FOCUS does (Windows' foreground lock): a field without a value pattern (classic
    # Edit controls show up as Pane) is typed into with keys, which only go to the foreground window
    _run(FOCUS, {"JARVIS_UI_PROC": proc})
    _run(TYPE, {"JARVIS_UI_PROC": proc, "JARVIS_UI_ELEM": elem, "JARVIS_UI_TEXT": text})
    # "typed" means the text is in the field now: read it back (value pattern, else the control's own window text).
    # Only yes/no leaves this machine. A live run on 2026-10-16 reported success while nothing had arrived.
    _run(READBACK, {"JARVIS_UI_PROC": proc, "JARVIS_UI_ELEM": elem, "JARVIS_UI_TEXT": text})
    return {"observed": "text read back from the field"}      # the script above throws when the text is not there


# ---- 2026-10-16: focus, keys, scroll and (last resort) a click at a point, for the owner's direct requests ----------
WIN32 = r"""
Add-Type @'
using System; using System.Runtime.InteropServices;
public class W32 {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int n);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, int data, UIntPtr extra);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L, T, R, B; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
  [DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
}
'@
"""
FOCUS = WIN32 + r"""
$ErrorActionPreference='Stop'
__PICK__
$h = $p.MainWindowHandle
$fpid = 0; [void][W32]::GetWindowThreadProcessId([W32]::GetForegroundWindow(), [ref]$fpid)
for ($try = 0; $try -lt 3 -and ($fpid -ne $p.Id -or [W32]::IsIconic($h)); $try++) {   # already in front: do nothing (an Alt tap would close an open menu)
  if ([W32]::IsIconic($h)) { [void][W32]::ShowWindow($h, 9) }
  [W32]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero); [W32]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)   # lets a background process take the foreground
  if ($try -gt 0) {                                # Windows' foreground lock: join the foreground thread's input for the hand-over
    $ft = [W32]::GetWindowThreadProcessId([W32]::GetForegroundWindow(), [ref]$fpid); $me = [W32]::GetCurrentThreadId()
    [void][W32]::AttachThreadInput($me, $ft, $true); [void][W32]::BringWindowToTop($h); [void][W32]::SetForegroundWindow($h); [void][W32]::AttachThreadInput($me, $ft, $false)
  } else { [void][W32]::SetForegroundWindow($h) }
  Start-Sleep -Milliseconds 300
  $fpid = 0; [void][W32]::GetWindowThreadProcessId([W32]::GetForegroundWindow(), [ref]$fpid)
}
$fpid = 0; [void][W32]::GetWindowThreadProcessId([W32]::GetForegroundWindow(), [ref]$fpid)
if ($fpid -ne $p.Id) { throw 'target is not the foreground window' }
"""
KEY = FOCUS + r"""
Add-Type -AssemblyName System.Windows.Forms
[System.Windows.Forms.SendKeys]::SendWait($env:JARVIS_UI_KEYS)
"""
MEDIA_KEY = WIN32 + r"""
$vk = [byte]([int]$env:JARVIS_UI_VK)
[W32]::keybd_event($vk, 0, 0, [UIntPtr]::Zero); [W32]::keybd_event($vk, 0, 2, [UIntPtr]::Zero)
"""
SCROLL = FOCUS + r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
$root = [System.Windows.Automation.AutomationElement]::FromHandle($h)
$el = $root
if ($env:JARVIS_UI_ELEM) {
  $cond = New-Object System.Windows.Automation.OrCondition(
    (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::AutomationIdProperty, $env:JARVIS_UI_ELEM)),
    (New-Object System.Windows.Automation.PropertyCondition([System.Windows.Automation.AutomationElement]::NameProperty, $env:JARVIS_UI_ELEM)))
  $el = $root.FindFirst([System.Windows.Automation.TreeScope]::Descendants, $cond)
  if (-not $el) { throw 'no element' }
}
$pat = $null
$amount = if ($env:JARVIS_UI_DIR -eq 'up') { [System.Windows.Automation.ScrollAmount]::LargeDecrement } else { [System.Windows.Automation.ScrollAmount]::LargeIncrement }
if ($el.TryGetCurrentPattern([System.Windows.Automation.ScrollPattern]::Pattern, [ref]$pat)) {
  $pat.Scroll([System.Windows.Automation.ScrollAmount]::NoAmount, $amount)                   # structured first
} else {
  $r = New-Object W32+RECT; [void][W32]::GetWindowRect($h, [ref]$r)                           # then the mouse wheel over the window
  [void][W32]::SetCursorPos([int](($r.L + $r.R) / 2), [int](($r.T + $r.B) / 2))
  $d = if ($env:JARVIS_UI_DIR -eq 'up') { 360 } else { -360 }
  [W32]::mouse_event(0x0800, 0, 0, $d, [UIntPtr]::Zero)
}
"""
CLICK_AT = FOCUS + r"""
$r = New-Object W32+RECT; [void][W32]::GetWindowRect($h, [ref]$r)
$x = $r.L + [int]$env:JARVIS_UI_X; $y = $r.T + [int]$env:JARVIS_UI_Y
if ($x -ge $r.R -or $y -ge $r.B) { throw 'point outside the window' }
[void][W32]::SetCursorPos($x, $y)
[W32]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero); [W32]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
"""
REASONS = REASONS + ("point outside the window",)
SENDKEYS = {"enter": "{ENTER}", "tab": "{TAB}", "esc": "{ESC}", "escape": "{ESC}", "space": " ", "backspace": "{BS}",
            "delete": "{DEL}", "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}", "home": "{HOME}",
            "end": "{END}", "pageup": "{PGUP}", "pagedown": "{PGDN}", **{f"f{i}": "{F%d}" % i for i in range(1, 13)}}
MODS = {"ctrl": "^", "alt": "%", "shift": "+"}
MEDIA_KEYS = {"playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1, "stop": 0xB2, "volumeup": 0xAF, "volumedown": 0xAE, "mute": 0xAD}


def sendkeys(chord: str) -> str:
    """'ctrl+shift+t' -> '^+t'. Only named keys, letters and digits: never raw SendKeys syntax from a request."""
    parts = chord.strip().lower().split("+")
    *mods, key = parts
    if any(m not in MODS for m in mods) or len(set(mods)) != len(mods):
        raise ValueError("bad key")
    if key in SENDKEYS:
        k = SENDKEYS[key]
    elif re.fullmatch(r"[a-z0-9]", key):
        k = key
    else:
        raise ValueError("bad key")
    return "".join(MODS[m] for m in mods) + k


def _proc(target):
    proc = target.partition("|")[0].strip()
    if not _SAFE.match(proc):
        raise ValueError("bad target")
    return proc


def focus(target, params):
    _run(FOCUS, {"JARVIS_UI_PROC": _proc(target)})
    return {"observed": "the target is the foreground window", "foreground": True}      # FOCUS throws when it is not


def key(target, params):
    chord = str(params.get("text", "")).strip().lower()
    if chord in MEDIA_KEYS:                                # media keys go to whatever is playing: no window needed
        return _run(MEDIA_KEY, {"JARVIS_UI_VK": str(MEDIA_KEYS[chord])})
    _run(KEY, {"JARVIS_UI_PROC": _proc(target), "JARVIS_UI_KEYS": sendkeys(chord)})


def scroll(target, params):
    proc, _, elem = target.partition("|")
    direction = str(params.get("text", "down"))
    if direction not in ("up", "down"):
        raise ValueError("bad direction")
    _run(SCROLL, {"JARVIS_UI_PROC": _proc(proc), "JARVIS_UI_ELEM": elem[:120], "JARVIS_UI_DIR": direction})


def click_at(target, params):
    proc, _, point = target.partition("|")
    m = re.fullmatch(r"(\d{1,5}),(\d{1,5})", point)
    if not m:
        raise ValueError("bad point")
    _run(CLICK_AT, {"JARVIS_UI_PROC": _proc(proc), "JARVIS_UI_X": m.group(1), "JARVIS_UI_Y": m.group(2)})


WINDOW_STATE = WIN32 + r"""
$ErrorActionPreference='Stop'
Add-Type @'
using System; using System.Runtime.InteropServices;
public class WS { [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h); }
'@
__PICK__
$h = $p.MainWindowHandle
[void][W32]::ShowWindow($h, [int]$env:JARVIS_UI_SHOW)
Start-Sleep -Milliseconds 400
$state = if ([W32]::IsIconic($h)) { 'minimized' } elseif ([WS]::IsZoomed($h)) { 'maximized' } else { 'normal' }
if ($state -ne $env:JARVIS_UI_WANT) { throw "pattern: window is $state" }
"""
SHOW = {"minimize": ("6", "minimized"), "maximize": ("3", "maximized"), "restore": ("9", "normal")}


def _window_state(op):
    def run(target, params):
        n, want = SHOW[op]
        _run(WINDOW_STATE, {"JARVIS_UI_PROC": _proc(target), "JARVIS_UI_SHOW": n, "JARVIS_UI_WANT": want})   # checked after
        # WINDOW_STATE throws unless the window now is in the wanted state, so reaching here IS the node's observation of it
        return {"observed": "window state read back", "window_state": want}
    return run


# The window a "process|..." target means. A modern (UWP) app's window belongs to ApplicationFrameHost, not to the app's
# own process, so when no process of that name owns a window, a frame window titled with the name is used (one only).
PICK = r"""
$p = Get-Process -Name $env:JARVIS_UI_PROC -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) {
  Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
  $AE = [System.Windows.Automation.AutomationElement]
  $frames = $AE::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,
    (New-Object System.Windows.Automation.PropertyCondition($AE::ClassNameProperty, 'ApplicationFrameWindow')))
  $hit = @($frames | Where-Object { $_.Current.Name -eq $env:JARVIS_UI_PROC -or $_.Current.Name.StartsWith($env:JARVIS_UI_PROC + ' ', [StringComparison]::OrdinalIgnoreCase) })
  if ($hit.Count -gt 1) { throw 'no window: more than one matches' }
  if ($hit.Count -eq 1) { $p = [pscustomobject]@{ Id = $hit[0].Current.ProcessId; MainWindowHandle = [IntPtr]$hit[0].Current.NativeWindowHandle } }
}
if (-not $p) { throw 'no window' }
"""
FIND, CLICK, TYPE, READBACK, FOCUS, KEY, SCROLL, CLICK_AT, WINDOW_STATE = (
    s.replace("__PICK__", PICK) for s in (FIND, CLICK, TYPE, READBACK, FOCUS, KEY, SCROLL, CLICK_AT, WINDOW_STATE))

BACKEND = {"launch": launch, "click": click, "type": type_text, "focus": focus, "key": key, "scroll": scroll, "click_at": click_at,
           "minimize": _window_state("minimize"), "maximize": _window_state("maximize"), "restore": _window_state("restore")}
