"""Downloads that happened on VM2 and what may come back to the owner's PC (D79, 2026-10-27).

Owner: "web downloads of unknown things should happen on VM2". The file lands in a quarantine folder ON VM2 (Firefox's download folder is set to it by the provisioning
script). It is looked at THERE (lab/quarantine.py through the lab control service: type by magic bytes, size cap, sha256, the antivirus answer if clamscan is already
installed). Nothing crosses to the PC without the owner's click on a card that names the file:

    card      name, size, type (by content), source host (from his own request), whether the name and the content disagree, the scan answer. A program, script, archive,
              Office file with macros or anything unknown carries a warning and "Do not copy" is the default button. A file the scanner calls infected, or that is too big,
              is never offered.
    copy      the lab's own control service serves the bytes (read-only route, same hub); the PC re-hashes them and the copy exists only if the sha256 equals the one the card
              showed (and the one VM2 stated). A different hash, a different size or a different type than the card promised: nothing is written.
    where     <outputs>\\downloads\\<host>\\ (the same quarantine folder as D64's downloads), a new name, never over a file. A risky file gets ".quarantined" added to its name
              so a double click cannot run it.
    never     opened, run, extracted or previewed on the PC by JARVIS. This module has no code that does any of that (a test makes those calls fail).

The approval here is the owner's explicit button on a card that names the exact file (the ask panel); it is not a PIN approval and it approves nothing else.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .vm2 import Vm2Error, Vm2Port

RISKY_KINDS = {"executable", "script", "archive", "office_macro", "office_old", "html", "unknown"}
SAFE_NAME = re.compile(r"[^A-Za-z0-9 ._()\-]")
HOST_RX = re.compile(r"^(?:[a-z0-9-]{1,63}\.)+[a-z0-9-]{2,63}$")
FALLBACK_HOST = "from-vm2.invalid"


@dataclass
class Item:
    id: str
    name: str
    size: int
    sha256: str
    type: str
    kind: str
    risky: bool
    mismatch: bool
    too_big: bool
    scan: str            # clean | infected | error | not_installed | not_run

    @classmethod
    def from_record(cls, r: dict) -> "Item":
        sc = r.get("scan") if isinstance(r.get("scan"), dict) else {}
        kind = str(r.get("kind") or "unknown")[:20]
        return cls(str(r.get("id") or "")[:24], safe_name(r.get("name")), max(0, int(r.get("size") or 0)), str(r.get("sha256") or "")[:64], str(r.get("type") or "unknown")[:60],
                   kind, bool(r.get("risky")) or kind in RISKY_KINDS, bool(r.get("mismatch")), bool(r.get("too_big")), str(sc.get("result") or "not_run")[:20])

    @property
    def offerable(self) -> bool:
        return bool(self.id) and not self.too_big and self.scan != "infected" and len(self.sha256) == 64


@dataclass
class Result:
    ok: bool
    name: str = ""
    size: int = 0
    type: str = ""
    sha256: str = ""
    risky: bool = False
    error: str = ""
    dest: Path | None = None            # local only: never part of what the chat model is told

    def public(self) -> dict:
        d = {"ok": self.ok, "name": self.name, "size": self.size, "type": self.type, "sha256": self.sha256, "risky": self.risky, "opened": False, "extracted": False}
        if self.error:
            d["error"] = self.error
        return d


def safe_name(name: Any) -> str:
    base = os.path.basename(str(name or "").replace("\\", "/"))
    base = "".join(c for c in base if c.isprintable() and c not in "‪‫‬‭‮⁦⁧⁨⁩​‌‍﻿")
    stem, ext = os.path.splitext(base)
    stem = re.sub(r"\s+", " ", SAFE_NAME.sub("_", stem)).strip(" ._")[:60] or "download"
    return stem + SAFE_NAME.sub("", ext.lower())[:8]


def human(n: int) -> str:
    for unit, div in (("MB", 1 << 20), ("KB", 1 << 10)):
        if n >= div:
            return f"{n / div:.1f} {unit}"
    return f"{n} bytes"


def pc_dir(host: str, outputs: str | None = None) -> Path:
    host = host if HOST_RX.match(str(host or "")) else FALLBACK_HOST
    root = outputs or os.environ.get("JARVIS_OUTPUTS") or ""
    if not root:
        try:
            from jarvis.node.jarvis_node import browserdl
            root = browserdl.outputs_root()
        except Exception:  # noqa: BLE001
            root = str(Path.home() / "jarvis" / "outputs")
    return Path(os.path.realpath(root)) / "downloads" / host


def card(item: Item, host: str) -> dict:
    """The approval card. The default button is the cautious one for anything risky."""
    rows = [["File", item.name], ["Size", human(item.size)], ["Type (by its content)", item.type], ["From (your request)", host or "-"],
            ["Checked on VM2", {"clean": "virus scanner: clean", "infected": "virus scanner: INFECTED", "error": "virus scanner: could not scan",
                                "not_installed": "no virus scanner installed on VM2", "not_run": "not scanned"}.get(item.scan, "not scanned")],
            ["sha256", item.sha256[:16] + "..."]]
    fields: list = [{"type": "info_table", "label": "This file is waiting on VM2", "rows": rows}]
    if item.mismatch:
        fields.append({"type": "callout", "level": "warn", "text": "The name says one thing and the content is another. That is a classic trick."})
    if item.risky:
        fields.append({"type": "callout", "level": "warn", "text": "A program, script, archive, macro file or something unknown. It would be saved as a quarantined copy with '.quarantined' "
                                                                  "added to its name; JARVIS will never open, run or unpack it."})
    copy_style = "secondary" if item.risky else "primary"
    return {"title": "Copy this file from VM2 to your PC?", "intro": "It was downloaded on the sandbox desktop and nothing has crossed yet. Copy it only if you want it.",
            "fields": fields, "buttons": [{"id": "copy", "label": "Copy to my PC quarantine", "style": copy_style},
                                          {"id": "skip", "label": "Do not copy", "style": "primary" if item.risky else "secondary"}]}


def unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, ext = os.path.splitext(path.name)
    for i in range(2, 100):
        cand = path.with_name(f"{stem} ({i}){ext}")
        if not cand.exists():
            return cand
    return path.with_name(f"{stem}-{int(time.time())}{ext}")


def copy_back(port: Vm2Port, item: Item, host: str, outputs: str | None = None, max_bytes: int | None = None) -> Result:
    """Fetch, verify, write. Nothing is written unless the hash, the size and the type match what the card showed."""
    cap = max_bytes or port.cfg.quarantine_max_bytes
    if not item.offerable:
        return Result(False, item.name, error="this file cannot be copied (too big, not scanned clean, or no hash)")
    if item.size > cap:
        return Result(False, item.name, error="bigger than the size cap")
    try:
        data, stated = port.fetch(item.id, cap)
    except Vm2Error as e:
        return Result(False, item.name, error=str(e)[:120])
    got = hashlib.sha256(data).hexdigest()
    if got != item.sha256 or (stated and stated != item.sha256):
        return Result(False, item.name, error="the file changed or did not arrive intact (the sha256 differs from the one shown), so nothing was copied")
    if len(data) != item.size or len(data) > cap:
        return Result(False, item.name, error="the size differs from the one shown, so nothing was copied")
    folder = pc_dir(host, outputs)
    folder.mkdir(parents=True, exist_ok=True)
    name = item.name + (".quarantined" if item.risky else "")
    dest = unique(folder / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}")
    tmp = dest.with_name(dest.name + ".part")
    with open(tmp, "xb") as fh:
        fh.write(data)
    os.replace(tmp, dest)
    if hashlib.sha256(dest.read_bytes()).hexdigest() != item.sha256:         # read back from the disk: the copy on the PC is the one the owner approved
        try:
            dest.unlink()
        except OSError:
            pass
        return Result(False, item.name, error="the copy on disk differs from the one shown, so it was removed")
    return Result(True, dest.name, len(data), item.type, got, item.risky, dest=dest)


def review(port: Vm2Port, ask: Callable[[dict], dict], host: str = "", outputs: str | None = None, only: Callable[[Item], bool] | None = None) -> dict:
    """List what is waiting in VM2's quarantine and, one file at a time, ask whether to copy it. Returns {copied: [...], skipped: [...], refused: [...], error?}. Files are
    offered newest first; an unanswered card is a no. Nothing is opened."""
    out: dict = {"copied": [], "skipped": [], "refused": []}
    try:
        records = port.quarantine()
    except Vm2Error as e:
        out["error"] = str(e)[:120]
        return out
    for r in records:
        item = Item.from_record(r)
        if only is not None and not only(item):
            continue
        if not item.offerable:
            out["refused"].append({"name": item.name, "why": "infected" if item.scan == "infected" else "too big or not readable"})
            continue
        try:
            ans = ask(card(item, host))
        except Exception:  # noqa: BLE001
            ans = {}
        if not (isinstance(ans, dict) and ans.get("answered") and ans.get("button") == "copy"):
            out["skipped"].append({"name": item.name, "type": item.type, "size": item.size})
            continue
        res = copy_back(port, item, host, outputs)
        (out["copied"] if res.ok else out["refused"]).append(res.public() if res.ok else {"name": item.name, "why": res.error})
    return out
