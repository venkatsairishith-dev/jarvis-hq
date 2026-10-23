"""JARVIS 9.5, C9 perception, live on real windows (2026-10-16). Windows, browser-venv (the node's Python); llama-swap with
the "vision" model. Uses only JARVIS's own test windows (the sample app's error dialog, Calculator opened by the test);
captures are of those windows only, stay local and are deleted.
    D:\\jarvis\\tools\\browser-venv\\Scripts\\python.exe jarvis\\tests\\live_vision.py
PE-vision-dialog              the error dialog read from PIXELS (OCR), not from the accessibility tree: the exact error
PE-vision-grounding           OCR grounding (click OK at the point OCR found) + vision-model grounding of Calculator
                              buttons, each click verified by Calculator's display; pass: OCR click works and >= 5 of 6
PE-observation-vs-inference   OCR lines are 'observed', model answers 'inferred'; a question about something NOT on the
                              screen gets an answer with low support, so it is not reported as observed
Results: jarvis/evals/quality95/results/vision-<time>.json
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "jarvis" / "node"))
from jarvis_node import vision, winbackend as W, windows  # noqa: E402
from jarvis_node.jobs import ui_snapshot  # noqa: E402

APP = Path(r"D:\jarvis\tests\jarvis-sample-app")
rows = []


def row(cid, ok, detail):
    rows.append({"cap": "C9_perception", "id": cid, "status": "pass" if ok else "fail", "detail": str(detail)[:400]})
    print(("PASS " if ok else "FAIL ") + cid, str(detail)[:300], flush=True)


def ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True).stdout.strip()


CLOSE_CALC = ("Add-Type -AssemblyName UIAutomationClient; $AE=[System.Windows.Automation.AutomationElement]; "
              "$AE::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, (New-Object System.Windows.Automation."
              "PropertyCondition($AE::ClassNameProperty, 'ApplicationFrameWindow'))) | Where-Object { $_.Current.Name -eq 'Calculator' } | "
              "ForEach-Object { $wp=$null; if ($_.TryGetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern, [ref]$wp)) { $wp.Close() } }")


def display():
    return next((e.get("name") for e in ui_snapshot({"target": "calculator"}).get("elements") or [] if e.get("id") == "CalculatorResults"), "")


def main():
    # 1. the error dialog from pixels
    subprocess.run(["taskkill", "/IM", "JARVISSampleApp.exe", "/F"], capture_output=True)
    p = subprocess.Popen([str(APP / "JARVISSampleApp.exe")], cwd=str(APP))
    time.sleep(3)
    try:
        r = vision.look("JARVISSampleApp", "OK", use_model=False)
        seen = json.load(open(r["local_result"], encoding="utf-8"))["observed_lines"]
        os.remove(r["local_result"])
        text = " ".join(seen).lower()
        row("PE-vision-dialog", "could not start" in text and "80x80" in text,
            f"{r['lines']} lines read from the window's pixels; error phrase found: {'could not start' in text}; value 80x80 found: {'80x80' in text}")
        ocr_click = False
        if r.get("point"):
            W.click_at(f"JARVISSampleApp|{r['point']['x']},{r['point']['y']}", {})
            for _ in range(10):
                if p.poll() is not None:
                    ocr_click = True
                    break
                time.sleep(0.5)
        obs = r.get("basis") == "observed"
    finally:
        if p.poll() is None:
            p.kill()
    # 2. vision-model grounding on Calculator (its buttons have names, so every click can be checked by its display)
    if ps("@(Get-Process CalculatorApp,Calculator -ErrorAction SilentlyContinue).Count") != "0":
        row("PE-vision-grounding", False, "a Calculator is already open (maybe the owner's): not touched")
        hits, tried, inferred_ok = 0, 0, False
    else:
        for _ in range(20):                                   # a Calculator closed just before can linger as a frame
            if sum(w["title"] == "Calculator" for w in windows.list_windows()) == 0:
                break
            time.sleep(0.5)
        W.launch("calc", {})
        windows.wait_for(lambda w: w["title"] == "Calculator", timeout=10)
        time.sleep(2)
        hits, tried, inferred_ok, log = 0, 0, True, []
        try:
            for target, want in (("the 7 button", "7"), ("the 4 button", "4"), ("the 9 button", "9"), ("the 2 button", "2"),
                                 ("the 5 button", "5"), ("the 8 button", "8")):
                W.click("calculator|Clear", {})
                time.sleep(0.4)
                tried += 1
                g = vision.look("Calculator", target, use_model=True)
                if g.get("local_result"):
                    os.remove(g["local_result"])
                pt = g.get("point")
                if g.get("found_by") == "vision model" and g.get("basis") != "inferred":
                    inferred_ok = False
                if not pt:
                    log.append((target, "no point"))
                    continue
                W.click_at(f"Calculator|{pt['x']},{pt['y']}", {})
                time.sleep(0.6)
                shown = display()
                ok = shown.strip().endswith(" " + want)
                hits += ok
                log.append((target, g.get("found_by"), pt, shown))
        finally:
            ps(CLOSE_CALC)
        row("PE-vision-grounding", ocr_click and hits >= 5,
            f"OCR click on OK closed the dialog: {ocr_click}; model-grounded clicks verified by the display: {hits}/{tried}; {log}")
    # 3. observed vs inferred
    subprocess.run(["taskkill", "/IM", "JARVISSampleApp.exe", "/F"], capture_output=True)
    p = subprocess.Popen([str(APP / "JARVISSampleApp.exe")], cwd=str(APP))
    time.sleep(3)
    try:
        on = vision.describe("JARVISSampleApp", "What error does this window show?")
        off = vision.describe("JARVISSampleApp", "What is the bank account balance shown in this window?")
    finally:
        p.kill()
    ok = obs and inferred_ok and on["support"] > off["support"] and off["support"] < 0.5 and any("could not start" in o.lower() for o in on["observed"])
    row("PE-observation-vs-inference", ok,
        f"OCR point labelled observed: {obs}; model points labelled inferred: {inferred_ok}; support of the model's answer by the "
        f"screen text: on-screen question {on['support']}, off-screen question {off['support']} (must stay low)")
    out = ROOT / "jarvis" / "evals" / "quality95" / "results" / f"vision-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows}, indent=1), encoding="utf-8")
    print("saved", out)
    return 0 if all(r["status"] == "pass" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
