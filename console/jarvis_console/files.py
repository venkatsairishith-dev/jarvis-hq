"""Delivered files and lab workspace files, listed and previewed without ever running them.

Delivered = what the kernel's verified delivery wrote under the outputs folder (the owner's laptop).
Workspace = the lab agents' temporary task folders inside the WSL box (read through wsl.exe, read-only).
A preview is served as inert content: text as text/plain, HTML and SVG with a sandboxing Content-Security-Policy and no
scripts, everything with nosniff. Paths are checked after resolving links, so nothing outside the root is readable.
"""
import mimetypes
import os
import re
import subprocess
import time

MAX_PREVIEW = 5 * 1024 * 1024
MAX_LIST = 500
TEXT = {".md", ".txt", ".json", ".csv", ".log", ".py", ".js", ".ts", ".css", ".yml", ".yaml", ".toml", ".ini", ".sh", ".ps1", ".xml"}
RENDER = {".html", ".htm"}                          # shown in a sandboxed frame (no scripts) or as source
IMAGE = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
BLOCKED_NAME = re.compile(r"(?i)(^|[\\/])(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)")
LAB_ROOT = "/home/agent/work/jobs"
SANDBOX_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"


def kind_of(name: str) -> str:
    e = os.path.splitext(name)[1].lower()
    return "text" if e in TEXT else "html" if e in RENDER else "image" if e in IMAGE else "other"


def _safe_rel(rel: str) -> str:
    rel = str(rel or "").replace("\\", "/").strip("/")
    if (not rel or ".." in rel.split("/") or ":" in rel or "\x00" in rel or BLOCKED_NAME.search(rel) or len(rel) > 300
            or not re.fullmatch(r"[A-Za-z0-9._ /()+@=,\[\]-]+", rel)):        # no quotes, $, backticks, ; & | or control characters
        raise ValueError("bad path")
    return rel


def list_delivered(root: str):
    out = []
    base = os.path.realpath(root)
    if not os.path.isdir(base):
        return []
    for dp, dn, fn in os.walk(base):
        depth = dp[len(base):].count(os.sep)
        if depth >= 3:
            dn[:] = []
        for f in fn:
            full = os.path.join(dp, f)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, base).replace("\\", "/")
            out.append({"path": rel, "name": f, "group": rel.split("/")[0] if "/" in rel else "", "bytes": st.st_size,
                        "modified": st.st_mtime, "kind": kind_of(f), "area": "delivered"})
            if len(out) >= MAX_LIST * 4:
                break
    out.sort(key=lambda r: -r["modified"])
    return out[:MAX_LIST]


def read_delivered(root: str, rel: str):
    """(bytes, name) of one delivered file, only if it really lives under root."""
    rel = _safe_rel(rel)
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, rel))
    if os.path.commonpath([base, full]) != base or not os.path.isfile(full) or os.path.islink(os.path.join(base, rel)):
        raise FileNotFoundError(rel)
    if os.path.getsize(full) > MAX_PREVIEW:
        raise OverflowError("file is larger than the 5 MB preview limit; open it from the folder")
    with open(full, "rb") as fh:
        return fh.read(), os.path.basename(full)


def preview_headers(name: str, as_source: bool):
    """(content_type, extra headers) that make a preview inert."""
    e = os.path.splitext(name)[1].lower()
    h = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store", "Content-Security-Policy": SANDBOX_CSP,
         "Cross-Origin-Resource-Policy": "same-origin", "Referrer-Policy": "no-referrer"}
    if e in IMAGE:
        return ("image/svg+xml" if e == ".svg" else mimetypes.guess_type(name)[0] or "application/octet-stream"), h
    if e in RENDER and not as_source:
        return "text/html; charset=utf-8", h
    return "text/plain; charset=utf-8", h


# ---- lab workspace through wsl.exe (read-only; a missing or stopped box is a clear answer, not an error)
def _wsl(args, timeout=12):
    return subprocess.run(["wsl.exe", "-d", "JarvisLab", "-u", "agent", "--", *args], capture_output=True, timeout=timeout)


def list_workspace(limit=200):
    try:
        r = _wsl(["bash", "-lc", f"find {LAB_ROOT} -maxdepth 3 -type f -printf '%T@\\t%s\\t%P\\n' 2>/dev/null | sort -rn | head -n {int(limit)}"])
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "files": [], "why": "the lab box did not answer"}
    if r.returncode != 0:
        return {"available": False, "files": [], "why": "the lab box is not running"}
    files = []
    for line in r.stdout.decode("utf-8", "replace").splitlines():
        try:
            ts, size, rel = line.split("\t", 2)
        except ValueError:
            continue
        if not rel or ".." in rel.split("/"):
            continue
        files.append({"path": rel, "name": os.path.basename(rel), "group": rel.split("/")[0], "bytes": int(size), "modified": float(ts),
                      "kind": kind_of(rel), "area": "workspace"})
    return {"available": True, "files": files, "temporary": True}


def read_workspace(rel: str):
    rel = _safe_rel(rel)
    # the path goes in as a positional argument ($1), never inside the script text
    script = ('p=$(realpath -e -- "$1") || exit 3; case "$p" in ' + LAB_ROOT + '/*) ;; *) exit 3;; esac; '
              '[ -f "$p" ] && [ ! -L "$1" ] || exit 3; head -c ' + str(MAX_PREVIEW + 1) + ' -- "$p"')
    r = _wsl(["bash", "-c", script, "_", f"{LAB_ROOT}/{rel}"], timeout=20)
    if r.returncode != 0:
        raise FileNotFoundError(rel)
    if len(r.stdout) > MAX_PREVIEW:
        raise OverflowError("file is larger than the 5 MB preview limit")
    return r.stdout, os.path.basename(rel)
