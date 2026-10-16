"""Perception for one window, on this laptop only (2026-10-16). Windows.

Ladder (cheapest and most certain first): UI Automation (ui.py / diag window_text) -> Windows' built-in OCR on a capture
of that one window -> a local vision model (llama-swap "vision", UI-Venus-1.5-2B) for what has no readable text.
  * capture: PrintWindow of the target window only (never the desktop, never other windows), saved under
    outputs\\neko-local and deleted after use; nothing leaves the PC.
  * observed vs inferred: OCR text is OBSERVED (it is on the screen); anything the vision model says is INFERRED and is
    labelled so; a point from the model is checked before use (inside the window) and its click is verified afterwards.
Reused: Windows.Media.Ocr (built into Windows 10, via PowerShell's WinRT bridge), llama.cpp's multimodal server.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import urllib.request

from . import runtime
from .paths import local_result_dir, sweep_local

VISION_URL = os.environ.get("NEKO_VISION_URL", "http://127.0.0.1:8080/v1/chat/completions")
VISION_MODEL = os.environ.get("NEKO_VISION_MODEL", "vision-ground")

CAPTURE = r"""
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing, UIAutomationClient, UIAutomationTypes
Add-Type @'
using System; using System.Runtime.InteropServices;
public class PW { [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint f);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  public struct RECT { public int L, T, R, B; } }
'@
__PICK__
$h = $p.MainWindowHandle
if ([PW]::IsIconic($h)) { throw 'window is minimized' }
$r = New-Object PW+RECT; [void][PW]::GetWindowRect($h, [ref]$r)
$w = $r.R - $r.L; $hh = $r.B - $r.T
if ($w -le 0 -or $hh -le 0 -or $w -gt 8000 -or $hh -gt 8000) { throw 'bad window size' }
$bmp = New-Object System.Drawing.Bitmap $w, $hh
$g = [System.Drawing.Graphics]::FromImage($bmp); $dc = $g.GetHdc()
[void][PW]::PrintWindow($h, $dc, 2)               # PW_RENDERFULLCONTENT: this window's own pixels, even if covered
$g.ReleaseHdc($dc); $g.Dispose()
$s = [int]$env:NEKO_SCALE                         # small UI text reads far better enlarged (OCR coordinates are scaled back)
$big = New-Object System.Drawing.Bitmap ($w * $s), ($hh * $s)
$g2 = [System.Drawing.Graphics]::FromImage($big)
$g2.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$g2.DrawImage($bmp, 0, 0, $w * $s, $hh * $s); $g2.Dispose(); $bmp.Dispose()
$big.Save($env:NEKO_PNG, [System.Drawing.Imaging.ImageFormat]::Png); $big.Dispose()
@{ width = $w; height = $hh; left = $r.L; top = $r.T; scale = $s } | ConvertTo-Json -Compress
"""
OCR = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$t) { $task = $asTask.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
$file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($env:NEKO_PNG)) ([Windows.Storage.StorageFile])
$stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
try {
  $dec = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $sb = Await ($dec.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
  $eng = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
  $res = Await ($eng.RecognizeAsync($sb)) ([Windows.Media.Ocr.OcrResult])
  @($res.Lines | ForEach-Object { @{ text = $_.Text; words = @($_.Words | ForEach-Object {
      @{ text = $_.Text; x = [int]$_.BoundingRect.X; y = [int]$_.BoundingRect.Y; w = [int]$_.BoundingRect.Width; h = [int]$_.BoundingRect.Height } }) } }) |
    ConvertTo-Json -Depth 5 -Compress
} finally { $stream.Dispose() }
"""


class VisionError(Exception):
    pass


def _pick() -> str:
    from .winbackend import PICK
    return PICK


def capture(proc: str, scale: int = 2) -> tuple[str, dict]:
    """PNG of that one window (local file), enlarged `scale` times, and the window's own size/position."""
    if not re.fullmatch(r"[A-Za-z0-9_.\- ]{1,80}", proc or ""):
        raise VisionError("bad target")
    sweep_local()
    path = os.path.join(local_result_dir(), f"window-{secrets.token_hex(4)}.png")
    r = runtime.powershell(CAPTURE.replace("__PICK__", _pick()), {"JARVIS_UI_PROC": proc, "NEKO_PNG": path, "NEKO_SCALE": str(int(scale))}, 30)
    if r.returncode or not os.path.isfile(path):
        msg = "no window" if "no window" in (r.stderr or "") else ("window is minimized" if "minimized" in (r.stderr or "") else "capture failed")
        raise VisionError(msg)
    return path, json.loads(r.stdout.strip().splitlines()[-1])


def ocr(png: str, scale: int = 1) -> list[dict]:
    """Lines and words with boxes in the WINDOW's pixels (divided by the capture's enlargement)."""
    r = runtime.powershell(OCR, {"NEKO_PNG": png}, 60)
    if r.returncode:
        raise VisionError("ocr failed")
    out = (r.stdout or "").strip()
    if not out:
        return []
    data = json.loads(out, strict=False)                 # OCR text can carry raw control characters
    lines = data if isinstance(data, list) else [data]
    for ln in lines:
        ln["text"] = re.sub(r"[\x00-\x1f]", "", ln.get("text", "")).strip()
        ws = ln.get("words") or []
        ln["words"] = [dict(w, text=re.sub(r"[\x00-\x1f]", "", w["text"]), x=w["x"] // scale, y=w["y"] // scale,
                            w=max(1, w["w"] // scale), h=max(1, w["h"] // scale)) for w in (ws if isinstance(ws, list) else [ws])
                       if re.sub(r"[\x00-\x1f]", "", w.get("text", ""))]
    return lines


def _norm(s: str) -> str:
    s = s.lower().replace("0", "o").replace("1", "l").replace("|", "l")
    return re.sub(r"[^a-z0-9 ]", "", s).strip()


def find_text(lines: list[dict], label: str) -> dict | None:
    """The box of the words that read like `label` (exact phrase first, then a single word), OCR's 0/O and 1/l forgiven.
    Ambiguous (two equally good places) -> None: never guess where to click."""
    want = _norm(label)
    if not want:
        return None
    hits = []
    for ln in lines:
        ws = ln.get("words") or []
        for i in range(len(ws)):
            for j in range(i, min(len(ws), i + 6)):
                phrase = _norm(" ".join(w["text"] for w in ws[i:j + 1]))
                if phrase == want:
                    x0, y0 = min(w["x"] for w in ws[i:j + 1]), min(w["y"] for w in ws[i:j + 1])
                    x1 = max(w["x"] + w["w"] for w in ws[i:j + 1])
                    y1 = max(w["y"] + w["h"] for w in ws[i:j + 1])
                    hits.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "text": " ".join(w["text"] for w in ws[i:j + 1])})
    if len(hits) != 1:
        return None
    h = hits[0]
    return dict(h, cx=h["x"] + h["w"] // 2, cy=h["y"] + h["h"] // 2)


def ask_vision(png: str, prompt: str, schema: dict | None = None, timeout: float = 120) -> str:
    """The local vision model's answer (INFERRED, never treated as observation)."""
    with open(png, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = {"model": VISION_MODEL, "temperature": 0, "max_tokens": 200,
            "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                                                      {"type": "text", "text": prompt}]}]}
    if schema:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "answer", "schema": schema}}
    req = urllib.request.Request(VISION_URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise VisionError(f"the local vision model did not answer ({type(e).__name__})") from None
    return str(((out.get("choices") or [{}])[0].get("message") or {}).get("content") or "")


GROUND_PROMPT = ("Output the center point of the position corresponding to the following instruction: \n{target}. \n\n"
                 "The output should just be the coordinates of a point, in the format [x,y]. Additionally, if the task is "
                 "infeasible (e.g., the task is not related to the image), the output should be [-1,-1].")


def ground_vision(png: str, size: dict, target: str) -> dict | None:
    """UI-Venus's own prompt; it answers on a 0-1000 grid; scaled to the window's pixels and checked to be inside."""
    raw = ask_vision(png, GROUND_PROMPT.format(target=target[:120]))
    m = re.search(r"\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]", raw)
    if not m:
        return None
    x, y = float(m.group(1)), float(m.group(2))
    if x < 0 or y < 0 or x > 1000 or y > 1000:
        return None
    return {"cx": int(round(x / 1000 * size["width"])), "cy": int(round(y / 1000 * size["height"])), "raw": raw[:60]}


def describe(proc: str, question: str) -> dict:
    """An answer about one window, kept apart: OBSERVED = the OCR lines (on screen); INFERRED = the vision model's answer.
    support = how much of the answer's content words appear in the observed text (0..1); a low value means the model says
    something the screen does not show, and it must not be reported as fact."""
    png, size = capture(proc)
    try:
        lines = ocr(png, size.get("scale", 1))
        answer = ask_vision(png, question[:300] + " Answer briefly, only from what is visible.")
    finally:
        try:
            os.remove(png)
        except OSError:
            pass
    seen = set(re.findall(r"[a-z0-9]{3,}", " ".join(ln["text"] for ln in lines).lower()))
    said = set(re.findall(r"[a-z0-9]{3,}", answer.lower())) - {"the", "and", "this", "that", "with", "for", "are", "was"}
    support = round(len(said & seen) / len(said), 2) if said else 0.0
    return {"observed": [ln["text"] for ln in lines], "inferred": answer[:400], "support": support}


def look(proc: str, label: str = "", use_model: bool = True) -> dict:
    """Read one window (OCR) and, if asked, locate `label` in it: OCR first (observed), the vision model only when the text
    is not found (inferred). The window's text goes to a local file; the summary carries only counts and the point."""
    png, size = capture(proc)
    try:
        lines = ocr(png, size.get("scale", 1))
        res = {"ok": True, "size": {"width": size["width"], "height": size["height"]}, "lines": len(lines),
               "words": sum(len(ln.get("words") or []) for ln in lines)}
        local = os.path.join(local_result_dir(), f"ocr-{secrets.token_hex(4)}.json")
        with open(local, "w", encoding="utf-8") as f:
            json.dump({"observed_lines": [ln["text"] for ln in lines]}, f)
        res["local_result"] = local
        if label:
            hit = find_text(lines, label)
            if hit:
                res.update(point={"x": hit["cx"], "y": hit["cy"]}, found_by="ocr", basis="observed")
            elif use_model:
                g = ground_vision(png, size, label)
                if g:
                    res.update(point={"x": g["cx"], "y": g["cy"]}, found_by="vision model", basis="inferred")
                else:
                    res.update(point=None, found_by="", basis="", note="not found by OCR or the vision model")
            else:
                res.update(point=None, note="not found by OCR")
        return res
    finally:
        try:
            os.remove(png)
        except OSError:
            pass
