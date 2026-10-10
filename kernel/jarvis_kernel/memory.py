"""Durable memory for agents: stable ids, versions, verified deletion. The kernel is the store of record.

Why: Hermes's built-in memory has no identity: a remove matches a substring of the stored text, so when the model
reworded an entry and dropped a label, "remove the TEST PREFERENCE entry" found nothing (live test, 2026-10-10).
Here every entry has an id (m-xxxxxxxx) and a version:
- update needs the version it read (a stale update is a conflict, never a silent overwrite);
- forget is by id, or by content only when exactly one entry clearly matches (unrelated entries are never touched);
- every change is re-read and returned, so the caller sees that it took effect; history is kept per entry.
Entries belong to the agent whose token wrote them: a test agent's memories never mix with the owner's.
Texts that look like secrets are refused; memory must never hold one.
"""
import json
import re
import secrets
import time

from .scrub import scrub

MAX_TEXT = 500
MAX_ACTIVE = 200
KINDS = ("preference", "fact", "instruction")


class MemoryError(Exception):
    pass


class Conflict(MemoryError):
    def __init__(self, current):
        super().__init__(f"version conflict: the entry is at version {current['version']}; read it again and retry")
        self.current = current


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 2}


class Memories:
    def __init__(self, kernel):
        self.k = kernel
        kernel.db.execute("CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, owner TEXT, scope TEXT, kind TEXT, text TEXT, "
                          "version INT, state TEXT, created REAL, updated REAL, history TEXT)")

    # ---- reads ----
    def get(self, owner, mid, include_deleted=False):
        r = self.k.db.execute("SELECT id, owner, scope, kind, text, version, state, created, updated, history FROM memories "
                              "WHERE id=? AND owner=?", (str(mid), owner)).fetchone()
        if not r or (r[6] != "active" and not include_deleted):
            return None
        return {"id": r[0], "scope": r[2], "kind": r[3], "text": r[4], "version": r[5], "state": r[6], "created": r[7],
                "updated": r[8], "history": json.loads(r[9] or "[]")}

    def list(self, owner, scope="default", query=None):
        rows = self.k.db.execute("SELECT id FROM memories WHERE owner=? AND scope=? AND state='active' ORDER BY created",
                                 (owner, scope)).fetchall()
        out = [self.get(owner, r[0]) for r in rows]
        if query:
            q = _words(query)
            out = [m for m in out if q & _words(m["text"])] or []
        return [{k: m[k] for k in ("id", "kind", "text", "version", "updated")} for m in out]

    def resolve(self, owner, query, scope="default"):
        """Entries that match *query*, best first, with a score; 'unique' only when one clearly stands out."""
        q = _words(query)
        scored = []
        for m in self.list(owner, scope):
            w = _words(m["text"])
            if q and w:
                scored.append((len(q & w) / len(q | w), m))
        scored.sort(key=lambda x: -x[0])
        best = scored[0][0] if scored else 0
        second = scored[1][0] if len(scored) > 1 else 0
        unique = bool(scored) and best >= 0.34 and best - second >= 0.15
        return {"unique": unique, "candidates": [dict(m, score=round(s, 2)) for s, m in scored[:5]]}

    # ---- writes ----
    def _check_text(self, text):
        text = " ".join(str(text or "").split())
        if not text:
            raise MemoryError("empty text")
        if len(text) > MAX_TEXT:
            raise MemoryError(f"too long (at most {MAX_TEXT} characters)")
        clean, found, blocked = scrub(text, deep=True)
        if blocked or found:
            raise MemoryError("refused: the text looks like it contains a secret")
        return text

    def add(self, owner, text, kind="preference", scope="default"):
        text = self._check_text(text)
        if kind not in KINDS:
            raise MemoryError(f"kind must be one of {', '.join(KINDS)}")
        norm = text.lower()
        for m in self.list(owner, scope):
            if m["text"].lower() == norm:                 # the same entry again: no duplicate
                return dict(self.get(owner, m["id"]), duplicate=True)
        if len(self.list(owner, scope)) >= MAX_ACTIVE:
            raise MemoryError("memory is full; forget something first")
        mid, now = "m-" + secrets.token_hex(4), time.time()
        hist = [{"v": 1, "at": now, "op": "add", "text": text}]
        self.k.db.execute("INSERT INTO memories VALUES (?,?,?,?,?,?,?,?,?,?)",
                          (mid, owner, scope, kind, text, 1, "active", now, now, json.dumps(hist)))
        self.k.audit.append("memory_add", {"owner": owner, "id": mid, "scope": scope, "kind": kind, "chars": len(text)})
        return self.get(owner, mid)

    def update(self, owner, mid, text, expected_version):
        text = self._check_text(text)
        cur = self.get(owner, mid)
        if not cur:
            raise MemoryError("no such entry")
        if int(expected_version) != cur["version"]:
            raise Conflict(cur)
        hist = cur["history"] + [{"v": cur["version"] + 1, "at": time.time(), "op": "update", "text": text}]
        r = self.k.db.execute("UPDATE memories SET text=?, version=version+1, updated=?, history=? WHERE id=? AND owner=? AND version=? "
                              "AND state='active'", (text, time.time(), json.dumps(hist[-20:]), mid, owner, cur["version"]))
        if r.rowcount != 1:                                # changed between the read and the write
            raise Conflict(self.get(owner, mid) or cur)
        self.k.audit.append("memory_update", {"owner": owner, "id": mid, "version": cur["version"] + 1})
        return self.get(owner, mid)

    def forget(self, owner, mid, expected_version=None):
        cur = self.get(owner, mid)
        if not cur:
            raise MemoryError("no such entry (already forgotten, or not yours)")
        if expected_version is not None and int(expected_version) != cur["version"]:
            raise Conflict(cur)
        hist = cur["history"] + [{"v": cur["version"], "at": time.time(), "op": "forget"}]
        self.k.db.execute("UPDATE memories SET state='deleted', updated=?, history=? WHERE id=? AND owner=? AND state='active'",
                          (time.time(), json.dumps(hist[-20:]), mid, owner))
        after = self.get(owner, mid, include_deleted=True)
        if not after or after["state"] != "deleted":
            raise MemoryError("the entry could not be forgotten")
        self.k.audit.append("memory_forget", {"owner": owner, "id": mid})
        return {"forgotten": {k: cur[k] for k in ("id", "kind", "text", "version")}, "verified": self.get(owner, mid) is None}

    def render(self, owner, scope="default", limit=1600):
        """The block an agent loads at the start of every session: each entry with its id and version."""
        lines = [f"[{m['id']} v{m['version']}] ({m['kind']}) {m['text']}" for m in self.list(owner, scope)]
        out, used = [], 0
        for ln in lines:
            if used + len(ln) > limit:
                out.append(f"... {len(lines) - len(out)} more (list them with jarvis_memory_list)")
                break
            out.append(ln); used += len(ln) + 1
        return "\n".join(out)
