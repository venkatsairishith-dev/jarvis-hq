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
  # D65 (human feel, not evasion): a short text is typed in small pieces with a short beat between them (about 8 characters every 40 to 80 ms), so the owner
  # sees it being written; beyond JARVIS_UI_PACE_CAP characters (default 240) it goes in one piece, as before. The text is cut BEFORE the SendKeys escaping,
  # so an escaped group such as {+} is never split. JARVIS_UI_PACE_MS=0 turns the pace off.
  $raw = $env:JARVIS_UI_TEXT; $cap = if ($env:JARVIS_UI_PACE_CAP) { [int]$env:JARVIS_UI_PACE_CAP } else { 240 }; $pace = if ($env:JARVIS_UI_PACE_MS) { [int]$env:JARVIS_UI_PACE_MS } else { 55 }
  if ($pace -gt 0 -and $raw.Length -le $cap) {
    for ($i = 0; $i -lt $raw.Length; $i += 8) {
      $piece = $raw.Substring($i, [Math]::Min(8, $raw.Length - $i))
      [System.Windows.Forms.SendKeys]::SendWait([regex]::Replace($piece, '[\+\^%~\(\)\{\}\[\]]', '{$0}'))
      Start-Sleep -Milliseconds ($pace - 15 + (($i / 8) % 3) * 15)
    }
  } else {
    $t = [regex]::Replace($raw, '[\+\^%~\(\)\{\}\[\]]', '{$0}')
    [System.Windows.Forms.SendKeys]::SendWait($t)
  }
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
    from . import runtime, windowpick                      # stoppable: a cancelled job kills this PowerShell
    env = dict(env)
    if env.get("JARVIS_UI_PROC") and "JARVIS_UI_HWND" not in env:
        # which window: one shared rule (windowpick.py); a read may take the front or newest of several, a change never guesses
        env.update(windowpick.pick_env(env["JARVIS_UI_PROC"], mutating=script not in READ_ONLY))     # raises SeveralWindowsSayWhich for an ambiguous change
    r = runtime.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout=30, env={**runtime.safe_environ(), **env})
    if r.returncode:
        # Only fixed reason codes raised by our own scripts are passed on; other error text could contain screen content.
        text = (r.stderr or "") + (r.stdout or "")
        if os.environ.get("JARVIS_UI_DEBUG"):  # developer switch: print the raw error locally, never to the kernel or logs
            import sys
            print("UI-DEBUG:", text.strip()[:400], file=sys.stderr)
        code =next((c for c in REASONS if c in text), "other")
        raise RuntimeError("ui-action-failed:" + code)
    return r.stdout or ""                                      # a script may report what it observed on its last line (type_focused)


def launch(target, params):
    exe = LAUNCH_ALLOWLIST.get(target.strip().lower())
    if not exe:
        raise ValueError("app not in the launch allowlist")
    from . import windowpick
    # remember the window this launch creates: it is the only one of its kind JARVIS may later type into when the owner has others open
    windowpick.launch_and_record(os.path.splitext(exe)[0], lambda: subprocess.Popen([exe], close_fds=True))


def click(target, params):
    proc, elem = _split(target)
    from . import uia                                      # D68: the persistent UI Automation worker first; None = it is off, down or cannot, so PowerShell as before
    if uia.act("click", proc, True, elem=elem) is not None:
        return
    _run(CLICK, {"JARVIS_UI_PROC": proc, "JARVIS_UI_ELEM": elem})


def type_text(target, params):
    proc, elem = _split(target)
    text = str(params.get("text", ""))
    if len(text) > 2000:
        raise ValueError("text too long")
    from . import uia
    if uia.act("type", proc, True, elem=elem, text=text) is not None:       # D68: focus, set the value or type the keys, read it back: ONE worker request
        return {"observed": "text read back from the field"}
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
GLOBAL_KEY = WIN32 + r"""
$pairs = $env:JARVIS_UI_VKS.Split(',') | ForEach-Object { $p = $_.Split(':'); ,@([byte][int]$p[0], [int]$p[1]) }
foreach ($p in $pairs) { [W32]::keybd_event($p[0], 0, $p[1], [UIntPtr]::Zero) }
[array]::Reverse($pairs)
foreach ($p in $pairs) { [W32]::keybd_event($p[0], 0, ($p[1] -bor 2), [UIntPtr]::Zero) }
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
# D65 (human feel, not evasion): the pointer travels to the target in a short eased curve (about a quarter of a second) instead of jumping, so the owner can
# SEE where JARVIS is clicking; the pause after arriving is the beat a person takes before pressing. JARVIS_UI_SMOOTH=0 turns it off.
SMOOTH_MOVE = r"""
Add-Type -AssemblyName System.Windows.Forms
function Move-Smooth([int]$tx, [int]$ty) {
  if ($env:JARVIS_UI_SMOOTH -eq '0') { [void][W32]::SetCursorPos($tx, $ty); return }
  $c = [System.Windows.Forms.Cursor]::Position; $sx = $c.X; $sy = $c.Y
  $steps = [Math]::Max(6, [Math]::Min(18, [int]([Math]::Sqrt(($tx - $sx) * ($tx - $sx) + ($ty - $sy) * ($ty - $sy)) / 40)))
  for ($i = 1; $i -le $steps; $i++) {
    $t = $i / $steps; $e = $t * $t * (3 - 2 * $t)                      # ease in and out
    [void][W32]::SetCursorPos([int]($sx + ($tx - $sx) * $e), [int]($sy + ($ty - $sy) * $e)); Start-Sleep -Milliseconds 12
  }
  [void][W32]::SetCursorPos($tx, $ty); Start-Sleep -Milliseconds 70
}
"""
CLICK_AT = FOCUS + SMOOTH_MOVE + r"""
$r = New-Object W32+RECT; [void][W32]::GetWindowRect($h, [ref]$r)
$x = $r.L + [int]$env:JARVIS_UI_X; $y = $r.T + [int]$env:JARVIS_UI_Y
if ($x -ge $r.R -or $y -ge $r.B) { throw 'point outside the window' }
Move-Smooth $x $y
[W32]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero); [W32]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
"""
# close (D54): the polite close, WM_CLOSE to the picked window - the app asks about unsaved work itself, so nothing is ever force-killed. The script
# says whether the window is gone a moment later; a window that is still there is an app asking something (an unsaved document): left for the owner.
CLOSE = FOCUS + r"""
Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public class CW { [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l); [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h); }'
[void][CW]::PostMessage($h, 0x0010, [IntPtr]::Zero, [IntPtr]::Zero)
Start-Sleep -Milliseconds 1500
"JARVIS_CLOSED:" + (-not [CW]::IsWindow($h))
"""
# drag (D54, any task: drawing, selecting, moving a slider): press at one point of the window, move to another in a few steps, release. Points are in the
# window's own pixels and must lie inside it, like click_at.
DRAG = FOCUS + SMOOTH_MOVE + r"""
$r = New-Object W32+RECT; [void][W32]::GetWindowRect($h, [ref]$r)
$a = $env:JARVIS_UI_PTS -split ','
$x1 = $r.L + [int]$a[0]; $y1 = $r.T + [int]$a[1]; $x2 = $r.L + [int]$a[2]; $y2 = $r.T + [int]$a[3]
foreach ($pt in @(@($x1, $y1), @($x2, $y2))) { if ($pt[0] -ge $r.R -or $pt[1] -ge $r.B) { throw 'point outside the window' } }
Move-Smooth $x1 $y1
[W32]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
for ($i = 1; $i -le 12; $i++) { [void][W32]::SetCursorPos([int]($x1 + ($x2 - $x1) * $i / 12), [int]($y1 + ($y2 - $y1) * $i / 12)); Start-Sleep -Milliseconds 15 }
[W32]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
"""
REASONS = REASONS + ("point outside the window",)
SENDKEYS = {"enter": "{ENTER}", "tab": "{TAB}", "esc": "{ESC}", "escape": "{ESC}", "space": " ", "backspace": "{BS}",
            "delete": "{DEL}", "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}", "home": "{HOME}",
            "end": "{END}", "pageup": "{PGUP}", "pagedown": "{PGDN}", **{f"f{i}": "{F%d}" % i for i in range(1, 13)}}
MODS = {"ctrl": "^", "alt": "%", "shift": "+"}
# D54 (simple tasks): a few Windows shortcuts that go to the desktop, not to a window: target "desktop|". Only these, fixed; the flag 1 marks the arrows as extended keys.
GLOBAL_KEYS = {"win+d": "91:0,68:0", "win+m": "91:0,77:0", "win+left": "91:0,37:1", "win+right": "91:0,39:1", "win+up": "91:0,38:1", "win+down": "91:0,40:1",
               # D65 (problem solving): switch to the previous window, and to the next / previous virtual desktop. Nothing else may carry these keys.
               "alt+tab": "18:0,9:0", "ctrl+win+left": "17:0,91:0,37:1", "ctrl+win+right": "17:0,91:0,39:1"}
GLOBAL_ALIASES = {"win+ctrl+left": "ctrl+win+left", "win+ctrl+right": "ctrl+win+right"}
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
    from . import uia
    if uia.act("focus", _proc(target), True) is not None:
        return {"observed": "the target is the foreground window", "foreground": True}
    _run(FOCUS, {"JARVIS_UI_PROC": _proc(target)})
    return {"observed": "the target is the foreground window", "foreground": True}      # FOCUS throws when it is not


def key(target, params):
    chord = str(params.get("text", "")).strip().lower()
    chord = GLOBAL_ALIASES.get(chord, chord)
    if chord in GLOBAL_KEYS:                               # win+d, win+left ...: to the desktop (target "desktop|"), nothing else may carry the Windows key
        if _proc(target).lower() != "desktop":
            raise ValueError("bad key")
        return _run(GLOBAL_KEY, {"JARVIS_UI_VKS": GLOBAL_KEYS[chord]})
    if chord in MEDIA_KEYS:                                # media keys go to whatever is playing: no window needed
        return _run(MEDIA_KEY, {"JARVIS_UI_VK": str(MEDIA_KEYS[chord])})
    _run(KEY, {"JARVIS_UI_PROC": _proc(target), "JARVIS_UI_KEYS": sendkeys(chord)})


def scroll(target, params):
    proc, _, elem = target.partition("|")
    direction = str(params.get("text", "down"))
    if direction not in ("up", "down"):
        raise ValueError("bad direction")
    _run(SCROLL, {"JARVIS_UI_PROC": _proc(proc), "JARVIS_UI_ELEM": elem[:120], "JARVIS_UI_DIR": direction})


def close(target, params):
    out = _run(CLOSE, {"JARVIS_UI_PROC": _proc(target)})
    return {"closed": "JARVIS_CLOSED:True" in (out or ""), **({"observed": "the window closed"} if "JARVIS_CLOSED:True" in (out or "") else {})}


def drag(target, params):
    proc, _, pts = target.partition("|")
    m = re.fullmatch(r"(\d{1,5}),(\d{1,5}),(\d{1,5}),(\d{1,5})", pts)
    if not m:
        raise ValueError("bad points")
    _run(DRAG, {"JARVIS_UI_PROC": _proc(proc), "JARVIS_UI_PTS": ",".join(m.groups())})


def click_at(target, params):
    proc, _, point = target.partition("|")
    m = re.fullmatch(r"(\d{1,5}),(\d{1,5})", point)
    if not m:
        raise ValueError("bad point")
    _run(CLICK_AT, {"JARVIS_UI_PROC": _proc(proc), "JARVIS_UI_X": m.group(1), "JARVIS_UI_Y": m.group(2)})


# ---- D54 (2026-10-26): "a save is part of the request". Two composite actions, each ONE kernel job and ONE verified step -------------------------
# type_focused: put text into whatever has the keyboard focus in the target window, by pasting it (the clipboard's text is put back afterwards).
# Used for editors UI Automation cannot fill by value (VS Code's Monaco) and for text longer than a value-set. Keystrokes would trigger Monaco's
# auto-closing brackets and auto-indent and so change the code; a paste inserts it as it is. After the paste the focused control is read back
# when UI Automation can (Notepad); Monaco cannot be read, so the step then says "not observed" and the save's disk read-back is the proof.
TYPE_FOCUSED = FOCUS + r"""
Add-Type -AssemblyName System.Windows.Forms, UIAutomationClient, UIAutomationTypes
$text = $env:JARVIS_UI_TEXT
$had = $false; $old = $null
try { $old = Get-Clipboard -Raw -ErrorAction Stop; $had = ($old -is [string]) } catch { }
try {
  Set-Clipboard -Value $text -ErrorAction Stop
  Start-Sleep -Milliseconds 150
  $fpid = 0; [void][W32]::GetWindowThreadProcessId([W32]::GetForegroundWindow(), [ref]$fpid)
  if ($fpid -ne $p.Id) { throw 'target is not the foreground window' }      # re-checked right before the keys go out
  [System.Windows.Forms.SendKeys]::SendWait('^v')
  Start-Sleep -Milliseconds 400
} catch { if ("$_" -like '*foreground*') { throw 'target is not the foreground window' }; throw 'clipboard paste failed' }
finally { if ($had) { try { Set-Clipboard -Value $old } catch { } } }
$seen = 'none'
try {
  $fe = [System.Windows.Automation.AutomationElement]::FocusedElement; $pat = $null; $have = $null
  if ($fe -and $fe.TryGetCurrentPattern([System.Windows.Automation.TextPattern]::Pattern, [ref]$pat)) { $have = $pat.DocumentRange.GetText(-1) }
  elseif ($fe -and $fe.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pat)) { $have = $pat.Current.Value }
  if ($have -ne $null) {
    $norm = { param($t) ([regex]::Replace([string]$t, '\s+', ' ')).Trim() }
    $tail = & $norm $text; if ($tail.Length -gt 60) { $tail = $tail.Substring($tail.Length - 60) }
    if ((& $norm $have).Contains($tail)) { $seen = 'read back' }
    elseif ($env:JARVIS_UI_PROC -notin @('Code', 'Code - Insiders', 'VSCodium', 'Cursor')) { throw 'text not in field' }      # Monaco's hidden textarea holds a fragment, not the document (seen live 2026-10-26: the paste landed, the file's text matched): not observed, the save's disk read-back is the proof
  }
} catch { if ("$_" -like '*text not in field*') { throw 'text not in field' } }
"JARVIS_TYPED:" + $seen
"""
# save_as: Save As (ctrl+shift+s, never plain ctrl+s: ctrl+s on a document that already has a name would silently overwrite it), fill the file
# name box of the Save As dialog with the full path, press its Save button, and look at what happens. A "Confirm Save As" / replace prompt gets
# the NO button and the dialog is cancelled: this script never presses Yes. The path was chosen by savefile.py to be a name that does not exist.
SAVE_AS = FOCUS + r"""
Add-Type -AssemblyName System.Windows.Forms, UIAutomationClient, UIAutomationTypes
$AE = [System.Windows.Automation.AutomationElement]; $TS = [System.Windows.Automation.TreeScope]
$PC = [System.Windows.Automation.PropertyCondition]; $CT = [System.Windows.Automation.ControlType]
$path = $env:JARVIS_SAVE_PATH; $mainH = [int64]$h; $mypid = $p.Id
Add-Type -Namespace JV -Name Msg -MemberDefinition '[System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, string l); [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, System.Text.StringBuilder l); [System.Runtime.InteropServices.DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, IntPtr l); [System.Runtime.InteropServices.DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, int m, IntPtr w, IntPtr l);'
if (Test-Path -LiteralPath $path) { throw 'path exists' }                    # nothing is ever saved over an existing file
function Get-Dialogs {
  $r = @()
  foreach ($w in $AE::RootElement.FindAll($TS::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
    if ($w.Current.ProcessId -eq $mypid -and [int64]$w.Current.NativeWindowHandle -ne $mainH) { $r += $w } }
  $main = $AE::FromHandle([IntPtr]$mainH)
  foreach ($d in $main.FindAll($TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::ClassNameProperty, '#32770')))) { $r += $d }
  $r
}
function Find-Button($dlg, $ids, $names) {
  foreach ($b in $dlg.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition)) {
    $c = $b.Current
    $isButton = ($c.ControlType -eq $CT::Button) -or ($c.ClassName -eq 'Button') -or ($c.ClassName -eq 'CCPushButton')       # Windows 11: the common dialog's buttons are Panes of class Button; the Replace / Save changes prompts' are CCPushButton (AutomationIds CommandButton_6 Yes, CommandButton_7 No)
    if ($isButton -and ($ids -contains $c.AutomationId -or $names -contains ($c.Name -replace '&', ''))) { return $b } }
  return $null
}
function Press($b) {
  $ip = $null
  if ($b.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$ip)) { $ip.Invoke(); return }
  $bh = [int64]$b.Current.NativeWindowHandle                                            # no Invoke pattern: a real click message to that button's own window
  if ($bh -eq 0) { throw 'no save button' }
  [void][JV.Msg]::PostMessage([IntPtr]$bh, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)     # BM_CLICK, posted: a SendMessage would wait for the Replace prompt it opens and never return
}
$before = @(Get-Dialogs | ForEach-Object { [int64]$_.Current.NativeWindowHandle })
[System.Windows.Forms.SendKeys]::SendWait('^+s')                              # Save As...
$dlg = $null
for ($i = 0; $i -lt 40 -and -not $dlg; $i++) {
  Start-Sleep -Milliseconds 250
  $dlg = @(Get-Dialogs | Where-Object { $before -notcontains [int64]$_.Current.NativeWindowHandle -and $_.Current.Name -match '(?i)^save' })[0]
}
if (-not $dlg) { throw 'no save dialog' }
$dlgH = [int64]$dlg.Current.NativeWindowHandle
# Windows 11 Notepad (checked live 2026-10-26): the common dialog sits inside the Notepad window; its file name box is AutomationId 1001, class 'Edit',
# ControlType Pane with NO patterns at all (no ValuePattern), and a second element with the same id 1001 is the address bar (ToolbarWindow32).
# So the box is the 1001 element whose class is Edit (or whose type is Edit), and when it has no ValuePattern its own window handle is written with WM_SETTEXT.
$cands = @($dlg.FindAll($TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::AutomationIdProperty, '1001'))))
$edit = @($cands | Where-Object { $_.Current.ClassName -eq 'Edit' -or $_.Current.ControlType -eq $CT::Edit })[0]
if (-not $edit) { $edit = @($dlg.FindAll($TS::Descendants, (New-Object System.Windows.Automation.PropertyCondition($AE::ControlTypeProperty, $CT::Edit))) | Where-Object { $_.Current.Name -match '(?i)file na' })[0] }
if (-not $edit) { throw 'no file name box' }
$vp = $null
if ($edit.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$vp)) {
  $vp.SetValue($path)
  if ($vp.Current.Value -ne $path) { throw 'no file name box' }
} else {
  $eh = [int64]$edit.Current.NativeWindowHandle
  if ($eh -eq 0) { throw 'no file name box' }
  [void][JV.Msg]::SendMessage([IntPtr]$eh, 0x000C, [IntPtr]::Zero, $path)                  # WM_SETTEXT
  $sb = New-Object System.Text.StringBuilder 1024
  [void][JV.Msg]::SendMessage([IntPtr]$eh, 0x000D, [IntPtr]1024, $sb)                       # WM_GETTEXT: read back what the box holds
  if ($sb.ToString() -ne $path) { throw 'no file name box' }
}
$save = Find-Button $dlg @('1') @('Save')
if (-not $save) { throw 'no save button' }
function Cancel-Save { $c = Find-Button $dlg @('2') @('Cancel'); if ($c) { try { Press $c } catch { } } }
Press $save
$ended = $false
for ($i = 0; $i -lt 28 -and -not $ended; $i++) {
  Start-Sleep -Milliseconds 250
  $now = @(Get-Dialogs)
  $other = @($now | Where-Object { $before -notcontains [int64]$_.Current.NativeWindowHandle -and [int64]$_.Current.NativeWindowHandle -ne $dlgH })
  if ($other.Count -gt 0) {                                                      # a prompt on top of the dialog: replace? an error?
    $q = $other[0]; $said = ($q.Current.Name + ' ' + (($q.FindAll($TS::Descendants, [System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object { $_.Current.Name }) -join ' '))
    $no = Find-Button $q @() @('No', 'Cancel'); $ok = Find-Button $q @() @('OK')
    if ($no) { try { Press $no } catch { } } elseif ($ok) { try { Press $ok } catch { } }
    Start-Sleep -Milliseconds 300; Cancel-Save
    if ($said -match '(?i)already exists|replace|overwrite|confirm save as') { throw 'replace prompt' }
    throw 'save dialog error'
  }
  if (-not ($now | Where-Object { [int64]$_.Current.NativeWindowHandle -eq $dlgH })) { $ended = $true }
}
if (-not $ended) { Cancel-Save; throw 'save dialog error' }
"""
REASONS = REASONS + ("path exists", "no save dialog", "no file name box", "no save button", "replace prompt", "save dialog error",
                     "clipboard paste failed")


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
        from . import uia
        if uia.act("window_state", _proc(target), True, show=int(n), want=want) is not None:
            return {"observed": "window state read back", "window_state": want}
        _run(WINDOW_STATE, {"JARVIS_UI_PROC": _proc(target), "JARVIS_UI_SHOW": n, "JARVIS_UI_WANT": want})   # checked after
        # WINDOW_STATE throws unless the window now is in the wanted state, so reaching here IS the node's observation of it
        return {"observed": "window state read back", "window_state": want}
    return run


# The window a "process|..." target means. A modern (UWP) app's window belongs to ApplicationFrameHost, not to the app's
# own process, so when no process of that name owns a window, a frame window titled with the name is used (one only).
PICK = r"""
if ($env:JARVIS_UI_HWND) { $p = [pscustomobject]@{ Id = [int]$env:JARVIS_UI_PID; MainWindowHandle = [IntPtr][int64]$env:JARVIS_UI_HWND } }   # chosen by windowpick.py
if (-not $p) {
  $p = Get-Process -Name $env:JARVIS_UI_PROC -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1 }
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
FIND, CLICK, TYPE, READBACK, FOCUS, KEY, SCROLL, CLICK_AT, WINDOW_STATE, TYPE_FOCUSED, SAVE_AS, DRAG, CLOSE = (
    s.replace("__PICK__", PICK) for s in (FIND, CLICK, TYPE, READBACK, FOCUS, KEY, SCROLL, CLICK_AT, WINDOW_STATE, TYPE_FOCUSED, SAVE_AS, DRAG, CLOSE))
READ_ONLY = {FIND, READBACK}                  # every other script changes something (focus included: a focus on a guessed window would aim the next keystroke)

MAX_FOCUSED = 4000          # characters pasted by one type_focused job (the kernel's validator has the same limit)


def type_focused(target, params):
    """Paste the text into the focused control of the target window (see TYPE_FOCUSED). Says "observed" only when it read the text back."""
    proc = _proc(target)
    text = str(params.get("text", ""))
    if not text or len(text) > MAX_FOCUSED:
        raise ValueError("bad text")
    from . import uia
    got = uia.act("paste", proc, True, text=text)
    if got is not None:                                    # D68: the worker pasted it and read the focused control back (or said it could not)
        return {"observed": "text read back from the editor"} if got.get("typed") == "read back" else {}
    out = _run(TYPE_FOCUSED, {"JARVIS_UI_PROC": proc, "JARVIS_UI_TEXT": text})
    return {"observed": "text read back from the editor"} if "JARVIS_TYPED:read back" in (out or "") else {}


def save_as(target, params):
    """Save the target window's document under a new name in a known folder, then verify it on disk (savefile.py)."""
    from . import savefile
    return savefile.save_as(target, params)


BACKEND = {"launch": launch, "click": click, "type": type_text, "focus": focus, "key": key, "scroll": scroll, "click_at": click_at,
           "minimize": _window_state("minimize"), "maximize": _window_state("maximize"), "restore": _window_state("restore"),
           "type_focused": type_focused, "save_as": save_as, "drag": drag, "close": close}
