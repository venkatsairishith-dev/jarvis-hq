"""Durable memory for agents: stable ids, versions, verified deletion. The kernel is the store of record.

Why: Hermes's built-in memory has no identity: a remove matches a substring of the stored text, so when the model
reworded an entry and dropped a label, "remove the TEST PREFERENCE entry" found nothing (live test, 2026-10-10).
Here every entry has an id (m-xxxxxxxx) and a version:
- update needs the version it read (a stale update is a conflict, never a silent overwrite);
- forget is by id, or by content only when exactly one entry clearly matches (unrelated entries are never touched);
- every change is re-read and returned, so the caller sees that it took effect; history is kept per entry.
Entries belong to the agent whose token wrote them: a test agent's memories never mix with the owner's.
Each entry also records the LANE of that token (MEM-M2, 2026-10-18) and is read only in that lane: a token name re-issued
in another lane (private -> public) never reads the private entries written under that name. Rows from before the lane
column existed are private (unknown means private).
Texts that look like secrets are refused; memory must never hold one.
"""
import datetime
import json
import re
import secrets
import sqlite3
import time

from . import idempotency
from .scrub import scrub

MAX_TEXT = 500
MAX_ACTIVE = 200
KINDS = ("preference", "fact", "instruction", "lesson")
LANES = ("private", "public", "dashboard", "runner", "owner")   # the lanes a token can have (llm_proxy.AgentTokens.issue)
CLASH = 0.6                  # word overlap (Jaccard) at which two entries repeat or contradict each other: the lesson gate's
                             # no_duplicate rule, and render()'s conflict marks use the same measure
RENDER_LIMIT = 1600          # characters of the session block (render)
IMPORTANT_BONUS_S = 30 * 86400   # render(): a lesson or a correction ranks as if it were 30 days newer


class MemoryError(Exception):
    pass


class Conflict(MemoryError):
    def __init__(self, current):
        super().__init__(f"version conflict: the entry is at version {current['version']}; read it again and retry")
        self.current = current


MAX_QUERY = 4 * MAX_TEXT          # a query longer than this matches no better: the rest is not read (a 2 MB query stays fast)


def _words(s: str) -> set:
    return {w for w in re.findall(r"[a-z0-9]+", str(s)[:MAX_QUERY].lower()) if len(w) > 2}


def _clash(a: set, b: set) -> bool:
    """Two entries' words overlap so much that one repeats or contradicts the other (CLASH)."""
    return bool(a and b) and len(a & b) / len(a | b) >= CLASH


def _day(t) -> str:
    try:
        return datetime.datetime.fromtimestamp(float(t), datetime.timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return "?"


# Ranking for resolve() (2026-10-17): SQLite FTS5 BM25 over the same words _words() keeps, compared after Porter stemming
# ("recipes" = "recipe", "trains" = "train"). BM25 weighs a rare shared word above a common one ("seat" shared by four
# entries counts less than "aisle"). An in-memory index is built per call from the entries being ranked, so no copy of any
# text is kept on disk (forget() stays real erasure). Measured on jarvis/kernel/tests/fixtures/memory_retrieval.json with
# jarvis/evals/memory_retrieval.py. Without FTS5 in this Python's sqlite3, the word-overlap ranking below is used as before.
RANK_TOKENIZER = "porter unicode61"


def _fts_rank(query: str, texts: list):
    """(bm25 by text index for the texts that share at least one stemmed word with the query, the query's stemmed words,
    each text's stemmed words), or None when FTS5 is missing. Lower bm25 is better."""
    import sqlite3

    def toks(s):
        return " ".join(w for w in re.findall(r"[a-z0-9]+", str(s)[:MAX_QUERY].lower()) if len(w) > 2)
    q = toks(query)
    if not q or not texts:
        return {}, set(), {}
    c = sqlite3.connect(":memory:")
    try:
        c.execute(f"CREATE VIRTUAL TABLE d USING fts5(t, tokenize='{RANK_TOKENIZER}')")
        c.execute(f"CREATE VIRTUAL TABLE q USING fts5(t, tokenize='{RANK_TOKENIZER}')")
        c.execute("CREATE VIRTUAL TABLE dv USING fts5vocab(d, 'instance')")
        c.execute("CREATE VIRTUAL TABLE qv USING fts5vocab(q, 'instance')")
        c.executemany("INSERT INTO d(rowid, t) VALUES (?,?)", [(i + 1, toks(t)) for i, t in enumerate(texts)])
        c.execute("INSERT INTO q(rowid, t) VALUES (1, ?)", (q,))
        qterms = {r[0] for r in c.execute("SELECT term FROM qv")}
        terms = {}
        for term, doc in c.execute("SELECT term, doc FROM dv"):
            terms.setdefault(doc - 1, set()).add(term)
        # the query's own words (the tokenizer stems them once; stemming a stem again can change it): [a-z0-9] only
        match = " OR ".join('"' + w + '"' for w in sorted(set(q.split())))
        rows = c.execute("SELECT rowid, bm25(d) FROM d WHERE d MATCH ?", (match,)).fetchall() if match else []
    except sqlite3.OperationalError:
        return None
    finally:
        c.close()
    return {r - 1: s for r, s in rows}, qterms, terms


def _history(raw):
    """An entry's stored history; None when it is damaged (not a JSON list), so one bad row never breaks the others."""
    try:
        h = json.loads(raw or "[]")
    except (ValueError, TypeError):
        return None
    return h if isinstance(h, list) else None


def _version(v) -> int:
    """expected_version as the whole number it must be; anything else is the caller's mistake (a clear refusal, not a crash)."""
    if isinstance(v, int) and not isinstance(v, bool):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    if isinstance(v, str) and re.fullmatch(r"\s*-?\d{1,18}\s*", v):
        return int(v)
    raise MemoryError("expected_version must be the entry's version number (read the entry, then send its version)")


def _more(n: int) -> str:
    """render()'s last line when entries were left out: how many, and how to see them."""
    return f"... {n} more (older or less relevant; list them with jarvis_memory_list, optionally with a query)"


class Memories:
    def __init__(self, kernel):
        self.k = kernel
        kernel.db.execute("CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY, owner TEXT, scope TEXT, kind TEXT, text TEXT, "
                          "version INT, state TEXT, created REAL, updated REAL, history TEXT, lane TEXT NOT NULL DEFAULT 'private')")
        cols = [r[1] for r in kernel.db.execute("PRAGMA table_info(memories)").fetchall()]
        if "lane" not in cols:   # older databases never recorded the writer's lane: unknown means private (MEM-M2)
            rows = kernel.db.execute("SELECT count(*) FROM memories").fetchone()[0]
            kernel.db.execute("ALTER TABLE memories ADD COLUMN lane TEXT NOT NULL DEFAULT 'private'")
            kernel.audit.append("memories_lane_migrated", {"rows": rows, "lane": "private"})
        # Erasure must reach the disk: SQLite otherwise leaves a rewritten row's old bytes in the page's free space, and the
        # WAL keeps every earlier page image until a checkpoint (found by the v2 benchmark: a forgotten text was still in
        # state.db-wal). secure_delete zeroes freed space; forget() checkpoints and truncates the WAL.
        kernel.db.execute("PRAGMA secure_delete=ON")

    def _lane(self, owner, lane=None) -> str:
        """The lane this read or write happens in. The gateway passes the lane of the token it just checked; without it, the
        lane of the token now issued under this name (a name has exactly one token: issuing again replaces it); a name
        with no token at all (the owner's console and tools, tests) is private. Rows are read and changed only in their
        own lane, so a name re-issued from private to public never reaches what it wrote while private."""
        if lane:
            return str(lane)
        try:
            r = self.k.db.execute("SELECT lane FROM agent_tokens WHERE name=?", (str(owner),)).fetchone()
        except sqlite3.OperationalError:                   # no token was ever issued on this kernel
            r = None
        return str(r[0]) if r and r[0] else "private"

    # ---- reads ----
    def get(self, owner, mid, include_deleted=False, lane=None):
        lane = self._lane(owner, lane)
        r = self.k.db.execute("SELECT id, owner, scope, kind, text, version, state, created, updated, history, lane FROM memories "
                              "WHERE id=? AND owner=? AND lane=?", (str(mid), owner, lane)).fetchone()
        if not r or (r[6] != "active" and not include_deleted):
            return None
        hist = _history(r[9])
        return {"id": r[0], "scope": r[2], "kind": r[3], "text": r[4], "version": r[5], "state": r[6], "created": r[7],
                "updated": r[8], "lane": r[10], "history": hist or [], **({"history_damaged": True} if hist is None else {})}

    def list(self, owner, scope="default", query=None, lane=None):
        lane = self._lane(owner, lane)
        rows = self.k.db.execute("SELECT id FROM memories WHERE owner=? AND lane=? AND scope=? AND state='active' ORDER BY created",
                                 (owner, lane, scope)).fetchall()
        out = [m for m in (self.get(owner, r[0], lane=lane) for r in rows) if m]
        if query:
            q = _words(query)
            out = [m for m in out if q & _words(m["text"])] or []
        return [{k: m[k] for k in ("id", "kind", "text", "version", "updated")} for m in out]

    def resolve(self, owner, query, scope="default", lane=None):
        """Entries that match *query*, best first, with a score; 'unique' only when one clearly stands out.
        An entry with no word in common (after stemming) is never a candidate. Order: BM25 (FTS5), then the overlap score.
        score = the share of words in common (Jaccard over stemmed words); unique needs the same margins as before AND
        the BM25 winner to be the overlap winner, so it is never claimed more readily than by the overlap rule alone."""
        entries = self.list(owner, scope, lane=lane)
        ranked = _fts_rank(query, [m["text"] for m in entries])
        if ranked is None:                                 # no FTS5: exact-word overlap only (the ranking before 2026-10-17)
            q, words, bm25 = _words(query), [_words(m["text"]) for m in entries], None
        else:
            bm25, q, words = ranked
            words = [words.get(i, set()) for i in range(len(entries))]
        scored = []
        for i, m in enumerate(entries):
            shared = q & words[i]
            if shared:                                     # an entry with no word in common is not a match at all
                scored.append((len(shared) / len(q | words[i]), bm25.get(i, 0.0) if bm25 is not None else 0.0, i, m))
        by_overlap = sorted(scored, key=lambda x: (-x[0], x[2]))
        scored.sort(key=lambda x: (x[1], -x[0], x[2]))     # bm25 is lower-is-better; ties: more overlap, then older entry
        best = by_overlap[0][0] if by_overlap else 0
        second = by_overlap[1][0] if len(by_overlap) > 1 else 0
        unique = bool(scored) and best >= 0.34 and best - second >= 0.15 and scored[0][2] == by_overlap[0][2]
        return {"unique": unique, "candidates": [dict(m, score=round(s, 2)) for s, _, _, m in scored[:5]]}

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

    def add(self, owner, text, kind="preference", scope="default", lane=None):
        text = self._check_text(text)
        if kind not in KINDS or kind == "lesson":      # a lesson only comes through the gate (add_candidate, decide_lesson)
            raise MemoryError(f"kind must be one of {', '.join(k for k in KINDS if k != 'lesson')}")
        lane = self._lane(owner, lane)
        norm = text.lower()
        with self.k.db.tx():                              # check-then-insert as one step: two threads cannot both add it
            active = self.list(owner, scope, lane=lane)
            for m in active:
                if m["text"].lower() == norm:             # the same entry again: no duplicate
                    return dict(self.get(owner, m["id"], lane=lane), duplicate=True)
            if len(active) >= MAX_ACTIVE:
                raise MemoryError("memory is full; forget something first")
            mid, now = "m-" + secrets.token_hex(4), time.time()
            hist = [{"v": 1, "at": now, "op": "add", "text": text}]
            self.k.db.execute("INSERT INTO memories(id, owner, scope, kind, text, version, state, created, updated, history, lane) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (mid, owner, scope, kind, text, 1, "active", now, now, json.dumps(hist), lane))
            self.k.audit.append("memory_add", {"owner": owner, "id": mid, "scope": scope if scope == "default" else f"[label, {len(scope)} characters]",
                                                  "kind": kind, "chars": len(text), "lane": lane})
        return self.get(owner, mid, lane=lane)

    def update(self, owner, mid, text, expected_version, lane=None):
        text = self._check_text(text)
        lane = self._lane(owner, lane)
        cur = self.get(owner, mid, lane=lane)
        if not cur:
            raise MemoryError("no such entry")
        if _version(expected_version) != cur["version"]:
            raise Conflict(cur)
        hist = cur["history"] + [{"v": cur["version"] + 1, "at": time.time(), "op": "update", "text": text}]
        r = self.k.db.execute("UPDATE memories SET text=?, version=version+1, updated=?, history=? WHERE id=? AND owner=? AND lane=? "
                              "AND version=? AND state='active'", (text, time.time(), json.dumps(hist[-20:]), mid, owner, lane, cur["version"]))
        if r.rowcount != 1:                                # changed between the read and the write
            raise Conflict(self.get(owner, mid, lane=lane) or cur)
        self.k.audit.append("memory_update", {"owner": owner, "id": mid, "version": cur["version"] + 1})
        return self.get(owner, mid, lane=lane)

    def forget(self, owner, mid, expected_version=None, lane=None):
        """Real erasure: the text and every earlier version are overwritten, only a tombstone (id, kind, when) stays.
        The reply names the entry (id, kind, the version that was forgotten) but never repeats its text: a reply can be kept
        (idempotency keeps replies for 24 h), and the text must not outlive the entry (SEC-D2). For the same reason any kept
        reply that showed this entry (an add, update, list... sent with an Idempotency-Key) is redacted here."""
        lane = self._lane(owner, lane)
        with self.k.db.tx():
            cur = self.get(owner, mid, lane=lane)
            if not cur:
                raise MemoryError("no such entry (already forgotten, or not yours)")
            if expected_version is not None and _version(expected_version) != cur["version"]:
                raise Conflict(cur)
            now = time.time()
            tomb = [{"v": cur["version"], "at": now, "op": "forget"}]
            r = self.k.db.execute("UPDATE memories SET state='deleted', text='', updated=?, history=? WHERE id=? AND owner=? AND lane=? "
                                  "AND state='active'", (now, json.dumps(tomb), mid, owner, lane))
            if r.rowcount != 1:
                raise MemoryError("the entry could not be forgotten")
            redacted = idempotency.redact_replies(self.k.db, cur["id"], "a memory entry shown in this reply was forgotten")
            self.k.audit.append("memory_forget", {"owner": owner, "id": mid, **({"replies_redacted": redacted} if redacted else {})})
        after = self.get(owner, mid, include_deleted=True, lane=lane)   # read the store again: gone, and no text left behind
        if not after or after["state"] != "deleted" or after["text"] != "":
            raise MemoryError("the entry could not be forgotten")
        # copy the zeroed pages into the main file and empty the WAL; busy (another reader) means old page images may remain
        busy = self.k.db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return {"forgotten": {k: cur[k] for k in ("id", "kind", "version")}, "verified": self.get(owner, mid, lane=lane) is None,
                "erased": not (busy and busy[0])}

    # ---- lessons: candidates are not rendered into any session until they pass the gate (state 'candidate', not 'active')
    AUTHORITY = re.compile(r"(?i)\b(approve|approval|ignore (the )?(rules|instructions|policy)|disable|bypass|skip (the )?(verification|check|approval)|"
                           r"without (asking|approval|verification)|override|kill switch|grant (yourself|permission)|never ask)\b")

    def add_candidate(self, owner, text, source_task, scope="default", lane=None):
        text = self._check_text(text)
        lane = self._lane(owner, lane)
        with self.k.db.tx():
            seen = self.k.db.execute("SELECT id FROM memories WHERE owner=? AND state IN ('candidate','active','rejected') AND history LIKE ?",
                                     (owner, f'%"task": "{source_task}"%')).fetchone()
            if seen:
                return None
            mid, now = "m-" + secrets.token_hex(4), time.time()
            hist = [{"v": 1, "at": now, "op": "candidate", "task": source_task, "text": text}]
            self.k.db.execute("INSERT INTO memories(id, owner, scope, kind, text, version, state, created, updated, history, lane) "
                              "VALUES (?,?,?,?,?,?,?,?,?,?,?)", (mid, owner, scope, "lesson", text, 1, "candidate", now, now, json.dumps(hist), lane))
            self.k.audit.append("lesson_candidate", {"owner": owner, "id": mid, "task": source_task})
        return mid

    def candidates(self, owner=None):
        q = ("SELECT id, owner, text, history, created, lane FROM memories WHERE state='candidate'" + (" AND owner=? AND lane=?" if owner else "")
             + " ORDER BY created DESC LIMIT 50")
        rows = self.k.db.execute(q, (owner, self._lane(owner)) if owner else ()).fetchall()
        out = []
        for mid, o, text, hist, created, lane in rows:
            h = json.loads(hist or "[]")
            out.append({"id": mid, "owner": o, "lane": lane, "text": text, "created": created,
                        "task": next((x.get("task") for x in h if x.get("task")), None),
                        "last_check": next((x for x in reversed(h) if x.get("op") == "gate"), None)})
        return out

    def gate(self, owner, mid, task, lane=None):
        """The checks a lesson must pass before it can change JARVIS's behaviour. Every check reads recorded facts."""
        lane = self._lane(owner, lane)
        cur = self.k.db.execute("SELECT text, state FROM memories WHERE id=? AND owner=? AND lane=?", (mid, owner, lane)).fetchone()
        if not cur or cur[1] != "candidate":
            raise MemoryError("no such candidate lesson")
        text = cur[0]
        v = (task or {}).get("verification") or {}
        cites = any("job:" in str(e) for e in (task or {}).get("evidence") or [])
        checks = [
            ("origin", bool(task) and task.get("state") == "completed" and (v.get("passed") is True or cites),
             "comes from a task that completed with independent verification or finished-job evidence"),
            ("repaired", bool(task) and int(task.get("attempts") or 0) >= 2, "the task needed a repair attempt (there is something to learn)"),
            ("not_corrected", not any(e.get("kind") == "correction" for e in (task or {}).get("events") or []),
             "the owner's own check did not later correct this task's verification (a corrected task is not a reliable teacher)"),
            ("no_secret", not (lambda r: r[1] or r[2])(scrub(text, deep=True)), "contains nothing that looks like a secret"),
            ("no_authority", not self.AUTHORITY.search(text), "does not grant permission, skip checks or tell the agent to bypass approval"),
            ("fits", len(text) <= MAX_TEXT, "fits the memory entry size"),
        ]
        words = _words(text)
        clash = None
        for m in self.list(owner, lane=lane):
            if _clash(words, _words(m["text"])):
                clash = m["id"]
        checks.append(("no_duplicate", clash is None, "does not repeat or contradict an existing entry" + (f" ({clash})" if clash else "")))
        return [{"check": c, "ok": bool(ok), "what": w} for c, ok, w in checks]

    def decide_lesson(self, owner, mid, task, activate: bool, lane=None):
        """Activate (only if every check passes) or reject a candidate. Returns {state, checks}."""
        lane = self._lane(owner, lane)
        checks = self.gate(owner, mid, task, lane=lane)
        ok = all(c["ok"] for c in checks)
        now = time.time()
        with self.k.db.tx():
            row = self.k.db.execute("SELECT history FROM memories WHERE id=? AND owner=? AND lane=? AND state='candidate'",
                                    (mid, owner, lane)).fetchone()
            if not row:
                raise MemoryError("no such candidate lesson")
            hist = json.loads(row[0] or "[]")
            hist.append({"v": 1, "at": now, "op": "gate", "ok": ok, "checks": checks, "decision": "activate" if activate else "reject"})
            state = "active" if (activate and ok) else "rejected" if not activate else "candidate"
            self.k.db.execute("UPDATE memories SET state=?, updated=?, history=? WHERE id=? AND owner=? AND lane=? AND state='candidate'",
                              (state, now, json.dumps(hist[-12:]), mid, owner, lane))
            self.k.audit.append("lesson_" + ("activated" if state == "active" else "rejected" if state == "rejected" else "blocked"),
                                {"owner": owner, "id": mid, "failed": [c["check"] for c in checks if not c["ok"]]})
        return {"state": state, "checks": checks}

    # ---- what a session starts from
    def render(self, owner, scope="default", limit=RENDER_LIMIT, query=None, lane=None):
        """The block an agent loads at the start of every session and at every compaction: at most *limit* characters.

        Which entries are kept when not all fit (MEM-M1, 2026-10-18: the old oldest-first cut dropped every newer
        preference, correction and lesson after about 14 entries):
        - newest first, by the time the entry last changed (an update brings an entry forward);
        - a lesson or a correction (an updated entry, or the newest of entries that conflict) ranks IMPORTANT_BONUS_S newer;
        - an entry that repeats or contradicts a newer one (the lesson gate's CLASH measure) is kept last, and both are marked:
          if they disagree, the newer one is current;
        - with a *query* (the current task), entries that share a word with it come first, ranked by BM25 relevance (0.7)
          plus their place in the order above (0.3); the rest follow in that order. Deterministic: ties go by id.
        Each line carries its id, version, kind, scope, the day it was added and last updated (UTC), and any conflict, so
        the agent can update or forget the exact entry. Similar (possibly conflicting) entries are marked, never merged. Kept lines are shown
        oldest to newest (the newest last, nearest the conversation); when entries were left out, the last line says how
        many and how to list them."""
        lane = self._lane(owner, lane)
        limit = int(limit)
        rows = self.k.db.execute("SELECT id, kind, text, version, created, updated FROM memories WHERE owner=? AND lane=? AND scope=? "
                                 "AND state='active'", (owner, lane, scope)).fetchall()
        es = []
        for mid, kind, text, ver, created, updated in rows:
            created = float(created or 0)
            es.append({"id": mid, "kind": kind, "text": text or "", "version": int(ver or 1), "created": created,
                       "updated": float(updated or created), "older": [], "newer": []})
        if not es:
            return ""
        es.sort(key=lambda e: (e["updated"], e["created"], e["id"]))                # chronological: the display order
        words = [_words(e["text"]) for e in es]
        for j in range(len(es)):                       # es[i] (i < j) is older than es[j]
            for i in range(j):
                if _clash(words[i], words[j]):
                    es[j]["older"].append(es[i]["id"])
                    es[i]["newer"].append(es[j]["id"])

        def rank_time(e):
            important = e["kind"] == "lesson" or e["version"] > 1 or bool(e["older"])
            return e["updated"] + (IMPORTANT_BONUS_S if important else 0)
        order = sorted(es, key=lambda e: (bool(e["newer"]), -rank_time(e), -e["updated"], -e["created"], e["id"]))
        if query and str(query).strip():
            ranked = _fts_rank(str(query), [e["text"] for e in order])
            if ranked is None:                         # no FTS5: word overlap, as in resolve()
                q = _words(query)
                rel = {i: len(q & w) / len(q | w) for i, w in enumerate(_words(e["text"]) for e in order) if q & w}
                top = max(rel.values(), default=0)
                rel = {i: v / top for i, v in rel.items()} if top else {}
            else:
                bm25 = ranked[0]                       # lower is better (negative); the best match scores 1.0
                best = min(bm25.values(), default=0.0)
                rel = {i: (max(0.0, s / best) if best < 0 else 1.0) for i, s in bm25.items()}
            n = max(1, len(order) - 1)
            score = {i: 0.7 * r + 0.3 * (1 - i / n) for i, r in rel.items()}
            hits = sorted(score, key=lambda i: (-round(score[i], 9), i))
            order = [order[i] for i in hits] + [e for i, e in enumerate(order) if i not in score]

        def line(e):
            meta = f"{scope}, added {_day(e['created'])}"
            if e["version"] > 1 or e["updated"] - e["created"] > 60:
                meta += f", updated {_day(e['updated'])}"
            if e["newer"]:                             # the CLASH measure cannot tell a repeat from a contradiction: say both
                meta += f"; similar to newer {e['newer'][-1]}: if they disagree, {e['newer'][-1]} is current"
            elif e["older"]:
                meta += f"; newest of {len(e['older']) + 1} similar entries: if they disagree, this one is current"
            return f"[{e['id']} v{e['version']}] ({e['kind']}) {{{meta}}} {e['text']}"
        lines = {e["id"]: line(e) for e in es}
        if sum(len(x) + 1 for x in lines.values()) - 1 <= limit:
            keep = set(lines)
        else:
            room = len(_more(len(es))) + 1               # the "... N more" line always fits
            keep, used = set(), 0
            for e in order:
                ln = lines[e["id"]]
                if used + len(ln) + 1 + room <= limit:
                    keep.add(e["id"])
                    used += len(ln) + 1
        out = [lines[e["id"]] for e in es if e["id"] in keep]
        if len(keep) < len(es):
            out.append(_more(len(es) - len(keep)))
        return "\n".join(out)[:max(0, limit)]
