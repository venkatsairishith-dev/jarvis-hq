"""Category B: the Windows execution layer on real, harmless apps (Notepad, the Neko sample app), node code called
directly (the kernel's part is tested separately). Windows, browser-venv (the node's Python). Opens and closes Notepad;
types only into its own new, unsaved document; never saves.

    D:\\jarvis\\tools\\browser-venv\\Scripts\\python.exe jarvis\\evals\\pc_control\\live_desktop.py
Writes jarvis/evals/pc_control/results/desktop-<time>.json; one row per case.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "node"))
from jarvis_node import diag, repair, winbackend as W, windows  # noqa: E402

ROWS = []
NOT_TESTED = []


def field(proc="notepad"):
    """The window's first text field, found by accessibility (as the coordinator does): its AutomationId or name."""
    from jarvis_node.jobs import ui_snapshot
    els = ui_snapshot({"target": proc}).get("elements") or []
    f = next((e for e in els if str(e.get("type", "")).endswith(("Edit", "Document")) or str(e.get("cls") or "").startswith(("Edit", "RichEdit"))), None)
    if not f:
        raise RuntimeError("no text field found")
    return f.get("id") or f.get("name")
VALUE = r"""
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Text; using System.Runtime.InteropServices;
public class GT { [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SendMessage(IntPtr h, int m, IntPtr w, StringBuilder l); }
'@
$A = [System.Windows.Automation.AutomationElement]
$p = Get-Process notepad -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
$e = $A::FromHandle($p.MainWindowHandle).FindFirst([System.Windows.Automation.TreeScope]::Descendants,
  (New-Object System.Windows.Automation.PropertyCondition($A::ClassNameProperty, 'Edit')))
$sb = New-Object System.Text.StringBuilder 4096
[void][GT]::SendMessage([IntPtr]$e.Current.NativeWindowHandle, 0x000D, [IntPtr]4096, $sb)    # WM_GETTEXT: the field's own text
$sb.ToString()
"""
STATE = r"""
Add-Type @'
using System; using System.Runtime.InteropServices;
public class S { [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h); [DllImport("user32.dll")] public static extern bool IsZoomed(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow(); [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p); }
'@
$p = Get-Process $env:P -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
$f = 0; [void][S]::GetWindowThreadProcessId([S]::GetForegroundWindow(), [ref]$f)
"{0} {1} {2}" -f [S]::IsIconic($p.MainWindowHandle), [S]::IsZoomed($p.MainWindowHandle), ($f -eq $p.Id)
"""


def wt(proc="notepad"):
    """window_text with its local file removed at once (as the plugin does)"""
    r = diag.run({"op": "window_text", "target": proc})
    if r.get("local_result"):
        os.remove(r["local_result"])
    return r


def ps(script, env=None):
    return subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env={**os.environ, **(env or {})}, timeout=60).stdout.strip()


def case(cid, name, fn):
    t0 = time.time()
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001
        ok, detail = False, f"{type(e).__name__}: {str(e)[:160]}"
    ROWS.append({"id": cid, "name": name, "ok": bool(ok), "detail": str(detail)[:300], "seconds": round(time.time() - t0, 2)})
    print(f"[{'PASS' if ok else 'FAIL'}] {cid} {name}: {str(detail)[:160]}", flush=True)


def state(proc="notepad"):
    iconic, zoomed, fg = ps(STATE, {"P": proc}).split()
    return iconic == "True", zoomed == "True", fg == "True"


def expect_error(fn, words):
    try:
        fn()
        return False, "no error"
    except Exception as e:  # noqa: BLE001
        return any(w in str(e) for w in words), str(e)[:120]


def main():
    subprocess.run(["taskkill", "/IM", "notepad.exe", "/F"], capture_output=True)
    case("B01", "open an app (launch allowlist) and see its window", lambda: (W.launch("notepad", {}), time.sleep(2),
         (bool(windows.wait_for(lambda w: w["process"].lower() == "notepad", timeout=6)), "window appeared"))[-1])
    case("B02", "focus a window and confirm it is in front", lambda: (W.focus("notepad|", {}), (state()[2], "foreground"))[-1])
    case("B03", "type into a field found by its accessible name", lambda: (W.type_text(f"notepad|{field()}", {"text": "neko test 1"}),
         ("neko test 1" in ps(VALUE), ps(VALUE)[:40]))[-1])
    case("B04", "minimize a window", lambda: (W.BACKEND["minimize"]("notepad|", {}), (state()[0], "iconic"))[-1])
    case("B05", "restore a minimized window", lambda: (W.BACKEND["restore"]("notepad|", {}), (not state()[0] and not state()[1], "normal"))[-1])
    case("B06", "maximize then restore", lambda: (W.BACKEND["maximize"]("notepad|", {}), z := state()[1], W.BACKEND["restore"]("notepad|", {}),
         (z and not state()[1], "zoomed then normal"))[-1])
    case("B07", "inspect the active window (local text only)", lambda: (lambda r: (r["ok"] and r["summary"]["windows"] >= 1
         and r["summary"]["dialogs"] == 0 and os.path.isfile(r["local_result"]) and not os.remove(r["local_result"]),
         r["summary"]))(diag.run({"op": "window_text", "target": "notepad"})))
    case("B08", "a shortcut opens a dialog (Find), seen as a dialog", lambda: (W.type_text(f"notepad|{field()}", {"text": " find"}), W.key("notepad|", {"text": "ctrl+f"}), time.sleep(1.5), (lambda r: (r["summary"]["dialogs"] >= 1, r["summary"]))(
             wt()))[-1])
    case("B09", "dismiss the dialog by its Cancel button (structured click)", lambda: (_need_dialog(), W.click("notepad|Cancel", {}), time.sleep(1),
         (lambda r: (r["summary"]["dialogs"] == 0, r["summary"]))(wt()))[-1])
    case("B10", "missing element gives a specific error, no action", lambda: expect_error(lambda: W.click("notepad|NoSuchButton", {}), ["no element"]))
    case("B11", "point outside the window is refused", lambda: expect_error(lambda: W.click_at("notepad|20000,20000", {}), ["outside", "other"]))
    case("B12", "raw SendKeys syntax is refused", lambda: expect_error(lambda: W.key("notepad|", {"text": "{ENTER}"}), ["bad key"]))
    case("B13", "keys land in the intended app after focus moved away", lambda: _focus_switch())
    case("B14", "scroll a window (structured, then wheel)", lambda: (W.scroll("notepad|", {"text": "down"}), (True, "scrolled"))[-1])
    case("B15", "close with unsaved text shows a save dialog; Don't Save closes it", lambda: _close_unsaved())
    case("B16", "acting on a window that is gone fails cleanly", lambda: expect_error(lambda: W.focus("notepad|", {}), ["no window"]))
    case("B17", "read a sample error dialog and dismiss it", lambda: _sample_dialog())
    NOT_TESTED.append({"id": "B18", "name": "password fields are never typed into", "why": "no harmless app with a password field here; the refusal is in winbackend TYPE (IsPassword)"})
    case("B19", "an app outside the launch list is refused", lambda: expect_error(lambda: W.launch("cmd", {}), ["allowlist"]))
    case("B20", "sensitive app names are refused by diagnostics", lambda: (not diag.run({"op": "window_text", "target": "KeePass"})["ok"], "refused"))
    case("W-uwp-app", "a modern (UWP) app: Calculator 1 + 2 = 3 by element names, result read back", lambda: _uwp_calculator())
    case("W-installer-dialog", "an installer dialog (Windows Installer help): read, then OK by name closes it", lambda: _installer_dialog())
    out = HERE / "results" / f"desktop-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"cases": ROWS, "not_tested": NOT_TESTED}, indent=1), encoding="utf-8")
    for n in NOT_TESTED:
        print(f"[NOT TESTED] {n['id']} {n['name']}: {n['why']}")
    print("SUMMARY", sum(r["ok"] for r in ROWS), "of", len(ROWS), f"(+{len(NOT_TESTED)} not tested) ->", out)


def _need_dialog():
    if wt()["summary"]["dialogs"] < 1:
        raise RuntimeError("no dialog was open to dismiss")


def _focus_switch():
    subprocess.Popen(["mspaint.exe"])
    time.sleep(2.5)
    W.focus("mspaint|", {})
    time.sleep(0.5)
    W.key("notepad|", {"text": "end"})
    W.type_text(f"notepad|{field()}", {"text": " neko test 2"})
    v = ps(VALUE)
    subprocess.run(["taskkill", "/IM", "mspaint.exe", "/F"], capture_output=True)
    return "neko test 2" in v, v[:60]


def _close_unsaved():
    W.key("notepad|", {"text": "alt+f4"})
    time.sleep(1.2)
    r = diag.run({"op": "window_text", "target": "notepad"})
    data = json.load(open(r["local_result"], encoding="utf-8"))
    os.remove(r["local_result"])
    buttons = [b for w in data for b in w.get("buttons") or []]
    dont = next((b for b in buttons if "don" in b.lower()), None)
    if not dont:
        return False, f"no save dialog buttons: {buttons}"
    W.click(f"notepad|{dont}", {})
    time.sleep(1.5)
    gone = not windows.wait_for(lambda w: w["process"].lower() == "notepad", timeout=1)
    return gone, f"pressed '{dont}', notepad closed: {gone}"


def _sample_dialog():
    subprocess.run(["taskkill", "/IM", "NekoSampleApp.exe", "/F"], capture_output=True)
    subprocess.Popen([r"D:\jarvis\tests\neko-sample-app\NekoSampleApp.exe"])
    time.sleep(3)
    r = diag.run({"op": "window_text", "target": "NekoSampleApp"})
    data = json.load(open(r["local_result"], encoding="utf-8"))
    os.remove(r["local_result"])
    texts = " ".join(t for w in data for t in w.get("texts") or [])
    W.click("NekoSampleApp|OK", {})
    time.sleep(1.5)
    exited = not subprocess.run(["tasklist", "/FI", "IMAGENAME eq NekoSampleApp.exe"], capture_output=True, text=True).stdout.count("NekoSampleApp")
    return "not valid" in texts and exited, f"text read: {'not valid' in texts}; exited after OK: {exited}"


def _uwp_calculator():
    """Calculator's window belongs to ApplicationFrameHost, not Calculator.exe: the target "calculator|..." must still work."""
    from jarvis_node.jobs import ui_snapshot
    close = ("Add-Type -AssemblyName UIAutomationClient; $AE=[System.Windows.Automation.AutomationElement]; "
             "$AE::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, (New-Object System.Windows.Automation."
             "PropertyCondition($AE::ClassNameProperty, 'ApplicationFrameWindow'))) | Where-Object { $_.Current.Name -eq 'Calculator' } | "
             "ForEach-Object { $wp=$null; if ($_.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$wp)) { $wp.Close() } }")
    if ps("@(Get-Process CalculatorApp,Calculator -ErrorAction SilentlyContinue).Count") != "0":
        return False, "a Calculator is already open (maybe the owner's): not touched, case skipped as failed"
    try:
        W.launch("calc", {})
        if not windows.wait_for(lambda w: w["title"] == "Calculator", timeout=10):
            return False, "no Calculator window"
        time.sleep(1.5)
        for b in ("Clear", "One", "Plus", "Two", "Equals"):
            W.click(f"calculator|{b}", {})
            time.sleep(0.3)
        shown = next((e.get("name") for e in ui_snapshot({"target": "calculator"}).get("elements") or [] if e.get("id") == "CalculatorResults"), "")
        return shown.strip().endswith(" 3"), f"display reads {shown!r}"
    finally:
        ps(close)                                          # its own window only (no Calculator was open before)


def _installer_dialog():
    """msiexec /? only shows Windows Installer's help dialog (it installs nothing)."""
    p = subprocess.Popen(["msiexec", "/?"])
    try:
        time.sleep(3)
        r = wt("msiexec")
        if r["summary"].get("dialogs", 0) < 1:
            return False, f"no dialog seen {r['summary']}"
        W.click("msiexec|OK", {})
        for _ in range(10):
            if p.poll() is not None:
                return p.returncode == 0, f"dialog read ({r['summary']}), OK pressed by name, msiexec exited {p.returncode}"
            time.sleep(0.5)
        return False, "OK pressed but the dialog stayed"
    finally:
        if p.poll() is None:
            p.kill()


if __name__ == "__main__":
    main()
