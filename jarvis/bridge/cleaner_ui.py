"""The cleaner as a form (D55): the first use of the generic ask panel. `CleanerService` owns what is ticked, builds the form from a scan,
runs scan and clean in the background (the panel shows progress, never waits on a long call) and answers the one chat tool `jarvis_cleaner`.

Rules (the owner's words, 2026-10-26: "for cleanup open a UI where I have options like a cleaner, or I tell JARVIS 'check this and that' and
at last 'clean up'"):
  - `clean` removes only what is ticked, and only when the owner's own words say clean / clean up / clean now (the panel's Clean button is the
    owner's click and counts). The tool cannot see the owner's words, so the model passes them in `items`; the check is a guard against a
    stray call, the click is the real confirmation.
  - "clean up my PC" with nothing ticked opens the panel with the SAFE rows ticked and runs only because the same sentence says "clean up"
    (then it is the safe list and nothing else).
  - Review rows are never ticked by default, by "all" or by "everything". Only the owner's words naming one ('also check the old installers')
    or a click tick one.
  - The chat model gets category ids, fixed labels, sizes and counts. File names go to the owner's panel only.
"""
from __future__ import annotations

import re
import threading
from typing import Any, Callable

from jarvis.bridge.cleaner import Cleaner, model_view

KEY = "cleaner"
SAY_CLEAN = re.compile(r"(?i)\bclean(?:ing)?(?:\s*up)?\b|\bcleanup\b")
NOT_CLEAN = re.compile(r"(?i)\b(don'?t|do not|never|not yet|not now|stop|cancel|wait|hold on)\b[^.!?]{0,24}\bclean")
LATER = re.compile(r"(?i)\bclean(?:\s*up)?\b[^.!?]{0,20}\b(later|tomorrow|tonight|someday|eventually)\b")
CLEAN_UP_PC = re.compile(r"(?i)\bclean\s*up\b|\bclean (?:my|the|this) (?:pc|computer|laptop|machine|system)\b")
ALL_WORDS = {"all", "everything", "all safe", "every safe one", "safe", "safe ones", "the safe ones", "all of them"}

# plain words for each row; a name matches when all words of one phrase are in it. The longest matching phrase wins (so "windows temp"
# is only Windows\Temp while "temp" is both temp rows).
WORDS: dict[str, tuple[str, ...]] = {
    "user_temp": ("temp", "temporary", "temp files", "user temp", "my temp", "temporary files"),
    "windows_temp": ("temp", "temporary", "windows temp", "windows temporary"),
    "browser_cache": ("cache", "caches", "browser cache", "browser", "chrome cache", "edge cache", "web cache"),
    "app_cache": ("cache", "caches", "app cache", "apps cache", "application cache", "discord cache"),
    "thumbnail_cache": ("cache", "thumbnail", "thumbnails", "thumbnail cache", "icon cache", "icons"),
    "crash_logs": ("crash", "crash dumps", "dumps", "logs", "old logs", "crash logs", "error reports"),
    "update_cache": ("windows update", "update cache", "update files", "update downloads", "updates"),
    "recycle_bin": ("recycle bin", "recycle", "bin", "trash", "recycle bin contents"),
    "dev_caches": ("cache", "caches", "pip", "npm", "nuget", "package caches", "dev caches", "developer caches"),
    "duplicates": ("duplicates", "duplicate", "duplicate files", "dupes", "copies"),
    "old_installers": ("installers", "old installers", "installer", "setup files", "old setups", "installer files"),
    "big_unused": ("big files", "large files", "big unused", "old big files", "huge files", "big old files"),
    "empty_folders": ("empty folders", "empty folder", "empty directories"),
}


def fmt_bytes(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") or n >= 100 else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _tokens(text: str) -> list[str]:
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", text.lower())]


def says_clean(words: str) -> bool:
    return bool(words and SAY_CLEAN.search(words) and not NOT_CLEAN.search(words) and not LATER.search(words))


class CleanerService:
    def __init__(self, cleaner: Cleaner, broker: Any, spawn: Callable[[Callable[[], None]], Any] | None = None):
        self.cleaner, self.broker = cleaner, broker
        self.spawn = spawn or (lambda fn: threading.Thread(target=fn, daemon=True, name="cleaner").start())
        self.lock = threading.RLock()
        self.rows: list[dict] = []
        self.safe: set[str] = set()                    # ticked safe rows (category ids)
        self.items: set[str] = set()                   # ticked review items ("category:item")
        self.phase = "idle"                            # idle | scanning | ready | cleaning | done
        self.result: dict | None = None
        self.form_id = ""
        self.stop = False
        self.idle = threading.Event()
        self.idle.set()

    # -- the form ------------------------------------------------------------------------------------------------------------------------
    def _ticked_bytes(self) -> tuple[int, int]:
        by = {r["id"]: r for r in self.rows}
        total = sum(by[c]["bytes"] for c in self.safe if c in by)
        total += sum(i["bytes"] for r in self.rows for i in r.get("items", []) if f"{r['id']}:{i['id']}" in self.items)
        return total, len(self.safe) + len(self.items)

    def spec(self) -> dict:
        if self.phase == "done" and self.result:
            return self._result_spec(self.result)
        safe = [r for r in self.rows if r["kind"] == "safe"]
        review = [r for r in self.rows if r["kind"] == "review"]
        fields: list[dict] = []
        if safe:
            fields.append({"type": "section", "label": f"Safe to clean · {fmt_bytes(sum(r['bytes'] for r in safe))}", "children": [{
                "id": "safe", "type": "checkbox_group", "label": "Safe to clean",
                "options": [{"id": r["id"], "label": r["label"], "detail": r["what_it_is"], "size": r["bytes"], "checked": r["id"] in self.safe} for r in safe]}]})
        listed = [(r, i) for r in review for i in r.get("items", [])]
        if review and self.phase != "scanning":
            fields.append({"type": "section", "label": f"Review first · {fmt_bytes(sum(r['bytes'] for r in review))}", "collapsed": True, "children": [
                {"type": "callout", "level": "info", "text": "Nothing here is ticked for you. Ticked files go to the Recycle Bin, so you can put them back."},
                {"id": "review", "type": "checkbox_group", "label": "Review first",
                 "options": [{"id": f"{r['id']}:{i['id']}", "label": i.get("name") or i["label"], "detail": " · ".join(x for x in (i.get("folder", ""), i.get("note", "")) if x), "group": f"{r['label']} · {fmt_bytes(r['bytes'])}",
                              "size": i["bytes"], "checked": f"{r['id']}:{i['id']}" in self.items} for r, i in listed[:450]]
                 } if listed else {"type": "callout", "level": "info", "text": "Nothing to review."}]})
        intro = "One moment. Your files are not touched while I look." if self.phase == "scanning" else "Safe rows are ticked. Tick the ones you want and press Clean selected."
        return {"title": "Clean up this PC", "intro": intro, "fields": fields, "timeout_s": 150, "buttons": [
            {"id": "clean", "label": "Clean selected", "style": "primary"}, {"id": "scan", "label": "Scan again", "style": "secondary"},
            {"id": "close", "label": "Close", "style": "quiet"}]}

    def _result_spec(self, res: dict) -> dict:
        rows = [["Freed for good", fmt_bytes(res["freed_bytes"]) + f" ({res['deleted']} files)"]]
        if res["binned"]:
            rows.append(["Moved to the Recycle Bin", f"{fmt_bytes(res['to_bin_bytes'])} ({res['binned']} items)"])
        if res["skipped"]:
            rows.append(["Skipped", f"{res['skipped']} (in use or protected)"])
        fields: list[dict] = [{"type": "info_table", "label": "Result", "rows": rows}]
        if res.get("stopped"):
            fields.append({"type": "callout", "level": "info", "text": "Stopped early, as you asked."})
        if res["skipped"]:
            fields.append({"type": "callout", "level": "warn", "text": "Some files were in use. Close the programs that hold them and scan again."})
        if res["undo"]:
            undo = [[u["label"], fmt_bytes(u["bytes"])] for u in res["undo"][:30]]
            more = res["undo_more"] + max(0, len(res["undo"]) - 30)
            if more:
                undo.append([f"and {more} more", ""])
            fields.append({"type": "info_table", "label": "In the Recycle Bin", "rows": undo})
            fields.append({"type": "callout", "level": "info", "text": "Undo: open the Recycle Bin and choose Restore to put these back."})
        return {"title": "Done", "intro": f"Freed {fmt_bytes(res['freed_bytes'])}.", "fields": fields, "timeout_s": 150, "buttons": [
            {"id": "close", "label": "Done", "style": "primary"}, {"id": "scan", "label": "Scan again", "style": "secondary"}]}

    def _push(self, busy: bool | None = None, status: str | None = None, progress: dict | None = None, clear: bool = False) -> bool:
        if not self.form_id:
            return False
        return self.broker.update(self.form_id, self.spec(), busy=busy, status=status, progress=progress, clear_progress=clear)

    # -- background work -----------------------------------------------------------------------------------------------------------------
    def _begin(self, phase: str) -> bool:
        with self.lock:
            if not self.idle.is_set():
                return False
            self.idle.clear()
            self.phase, self.stop = phase, False
            return True

    def _scan_job(self, auto_clean_safe: bool = False) -> None:
        handoff = False
        try:
            self.rows = []

            def on_category(row: dict, done: int, total: int) -> None:
                if self.stop:
                    raise _Stopped()
                self.rows.append(row)
                self._push(busy=True, status=f"Looking at {row['label']}", progress={"done": done, "total": total, "label": row["label"]})
            try:
                self.rows = self.cleaner.scan(on_category)["categories"]
            except _Stopped:
                self.phase = "idle"
                return
            ids = {r["id"] for r in self.rows}
            self.safe = {r["id"] for r in self.rows if r["default_checked"]} if not self.safe else self.safe & ids
            self.items = {i for i in self.items if i.split(":")[0] in ids}
            self.phase = "ready"
            if auto_clean_safe:                                    # "clean up my PC": the safe list, nothing from the review list
                self.safe, self.items, self.phase, handoff = {r["id"] for r in self.rows if r["kind"] == "safe"}, set(), "cleaning", True
                self._push(busy=True, status="Cleaning...", progress={"done": 0, "total": 1, "label": "Starting"})
            else:
                self._push(busy=False, status="", clear=True)
        finally:
            if not handoff:
                self.idle.set()
        if handoff:
            self._clean_job({"categories": sorted(self.safe), "items": {}})        # keeps the busy flag until it ends

    def _start_clean(self) -> bool:
        if not self._begin("cleaning"):
            return False
        selection = {"categories": sorted(self.safe), "items": {}}
        for tick in self.items:
            cat, _, item = tick.partition(":")
            selection["items"].setdefault(cat, []).append(item)
        self._push(busy=True, status="Cleaning...", progress={"done": 0, "total": 1, "label": "Starting"})
        self.spawn(lambda: self._clean_job(selection))
        return True

    def _clean_job(self, selection: dict) -> None:
        try:
            def progress(p: dict) -> None:
                self._push(busy=True, status=f"Cleaning {p['label']}", progress={"done": p["done_items"], "total": p["total_items"], "bytes_done": p["done_bytes"],
                                                                                  "bytes_total": p["total_bytes"], "label": p["label"]})
            res = self.cleaner.clean(selection, on_progress=progress, should_stop=lambda: self.stop)
            self.result = res
            self.rows = (res.get("scan") or {}).get("categories", self.rows)
            self.safe, self.items = set(), set()
            self.phase = "done"
            self._push(busy=False, status="", clear=True)
        except Exception as exc:  # noqa: BLE001 - the panel says it failed; the details stay out of the model's reach
            self.result = {"freed_bytes": 0, "to_bin_bytes": 0, "deleted": 0, "binned": 0, "skipped": 0, "undo": [], "undo_more": 0, "error": type(exc).__name__}
            self.phase = "done"
            self._push(busy=False, status="Something went wrong", clear=True)
        finally:
            self.idle.set()

    def _rescan(self, auto_clean_safe: bool = False) -> bool:
        if not self._begin("scanning"):
            return False
        self.result = None
        self._push(busy=True, status="Looking for things to clean...", progress={"done": 0, "total": len(self.cleaner.catalog), "label": "Starting"})
        self.spawn(lambda: self._scan_job(auto_clean_safe))
        return True

    # -- the panel's clicks --------------------------------------------------------------------------------------------------------------
    def on_click(self, button: str, values: dict, form_id: str) -> dict:
        if button == "close":
            self.stop = True
            self.phase = "idle" if self.phase in ("scanning", "ready", "done") else self.phase
            self.form_id = ""
            return {"close": True}
        if button == "scan":
            self.safe, self.items = set(), set()
            self._rescan()
        elif button == "clean":
            self._apply_values(values)
            if not self.safe and not self.items:
                self._push(busy=False, status="Tick something first.")
            else:
                self._start_clean()
        return {}

    def _apply_values(self, values: dict) -> None:
        known = {r["id"] for r in self.rows}
        self.safe = {c for c in values.get("safe", []) if c in known and self.cleaner.by_id[c].kind == "safe"}
        self.items = {t for t in values.get("review", []) if t.split(":")[0] in known and self.cleaner.by_id[t.split(":")[0]].kind == "review"}

    # -- the chat tool -------------------------------------------------------------------------------------------------------------------
    def _open_form(self) -> None:
        cur = self.broker.current(KEY)
        if cur is not None:
            self.form_id = cur.id
            self.broker.update(cur.id, self.spec())
        else:
            self.form_id = self.broker.show(self.spec(), handler=self.on_click, key=KEY)
            self.broker.update(self.form_id, busy=self.phase in ("scanning", "cleaning"))

    def wait(self, seconds: float) -> bool:
        return self.idle.wait(seconds)

    def tool(self, action: str, items: str = "", wait_s: float = 25.0) -> dict:
        action, words = str(action or "").strip().lower(), str(items or "")[:400]
        if action == "open":
            return self.open(words, wait_s)
        if action in ("check", "uncheck"):
            return self.check(words, action == "check")
        if action == "list":
            return self.list()
        if action == "clean":
            return self.clean(words, wait_s)
        return {"ok": False, "message": "action is open, check, uncheck, list or clean"}

    def open(self, words: str = "", wait_s: float = 25.0) -> dict:
        auto = bool(CLEAN_UP_PC.search(words) and says_clean(words)) and not (self.safe or self.items)
        fresh = self.phase in ("idle", "done") or not self.rows
        if fresh:
            self.safe, self.items, self.phase = set(), set(), "idle"
        self._open_form()
        if fresh and self._rescan(auto_clean_safe=auto):
            self.wait(wait_s)
        out = self.list()
        out["message"] = ("The cleaner is open and has cleaned the safe list." if auto and self.phase == "done" else
                          "The cleaner is open and is cleaning the safe list." if auto and self.phase == "cleaning" else
                          "The cleaner is open. The safe rows are ticked; nothing from the review list is." if self.phase == "ready" else
                          "The cleaner is open and still looking." if self.phase == "scanning" else "The cleaner is open.")
        return out

    def _match(self, name: str) -> list[str]:
        toks = _tokens(name)
        if not toks:
            return []
        if " ".join(toks) in {" ".join(_tokens(w)) for w in ALL_WORDS}:
            return [r["id"] for r in self.rows if r["kind"] == "safe"]                        # "all" and "everything" mean the safe rows only
        best, hits = 0, {}
        for cid, phrases in WORDS.items():
            if cid not in {r["id"] for r in self.rows}:
                continue
            for phrase in phrases:
                pt = _tokens(phrase)
                if pt and set(pt) <= set(toks):
                    hits[cid] = max(hits.get(cid, 0), len(pt))
        if hits:
            best = max(hits.values())
        return [c for c, n in hits.items() if n == best]

    def check(self, words: str, on: bool = True) -> dict:
        if not self.rows:
            return {"ok": False, "message": "Open the cleaner first (action open)."}
        names = [n.strip() for n in re.split(r",|;|&|\band\b|\bplus\b|\balso\b", words, flags=re.I) if n.strip()]
        matched: list[str] = []
        unknown: list[str] = []
        for name in names:
            ids = self._match(name)
            if not ids:
                unknown.append(name[:40])
            matched += ids
        by = {r["id"]: r for r in self.rows}
        for cid in matched:
            if by[cid]["kind"] == "safe":
                (self.safe.add if on else self.safe.discard)(cid)
            else:
                for i in by[cid].get("items", []):
                    (self.items.add if on else self.items.discard)(f"{cid}:{i['id']}")
        if self.form_id and self.phase in ("ready", "done"):
            if self.phase == "done":
                self.phase = "ready"
            self._push()
        out = self.list()
        out.update(ok=bool(matched), matched=sorted(set(matched)), unknown=unknown)
        out["message"] = ("Ticked" if on else "Unticked") + f" {len(set(matched))} row(s)." + (f" I did not find: {', '.join(unknown)}." if unknown else "")
        return out

    def list(self) -> dict:
        total, n = self._ticked_bytes()
        by = {r["id"]: r for r in self.rows}
        ticked = [{"id": c, "label": by[c]["label"], "bytes": by[c]["bytes"], "files": by[c]["files"], "kind": "safe"} for c in sorted(self.safe) if c in by]
        for cat in sorted({t.split(":")[0] for t in self.items}):
            mine = [i for i in by.get(cat, {}).get("items", []) if f"{cat}:{i['id']}" in self.items]
            ticked.append({"id": cat, "label": by[cat]["label"], "bytes": sum(i["bytes"] for i in mine), "files": len(mine), "kind": "review"})
        return {"ok": True, "phase": self.phase, "ticked": ticked, "ticked_bytes": total, "ticked_text": fmt_bytes(total),
                "rows": [{"id": r["id"], "label": r["label"], "bytes": r["bytes"], "files": r["files"], "kind": r["kind"]} for r in self.rows],
                **({"result": model_view(_short(self.result))} if self.phase == "done" and self.result else {})}

    def clean(self, words: str = "", wait_s: float = 60.0) -> dict:
        if not says_clean(words):
            return {"ok": False, "message": "I only clean when the owner says clean, clean up or clean now. Ask the owner, or they can press Clean selected."}
        if not self.rows or self.phase in ("idle", "scanning"):
            return {"ok": False, "message": "Open the cleaner and let it finish looking first."}
        if self.phase == "cleaning":
            return {"ok": True, "message": "It is already cleaning.", **self.list()}
        if not self.safe and not self.items:
            return {"ok": False, "message": "Nothing is ticked. Tick rows first (action check), or say 'clean up my PC' for the safe list."}
        self._open_form()
        if not self._start_clean():
            return {"ok": False, "message": "The cleaner is busy."}
        self.wait(wait_s)
        out = self.list()
        out["message"] = ("Done." if self.phase == "done" else "Still cleaning; the panel shows the progress.")
        return out


class _Stopped(Exception):
    pass


def _short(res: dict | None) -> dict:
    res = res or {}
    return {k: res.get(k, 0) for k in ("freed_bytes", "to_bin_bytes", "deleted", "binned", "skipped")}
