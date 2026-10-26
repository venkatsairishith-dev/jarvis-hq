"""Learned names (D62): JARVIS learns the owner's name, his family's, friends' and places he uses often from what he says, keeps them LOCAL, and uses them.

The owner will not edit config/glossary.json ("I will not [do] anything; it will know by conversion with me"). So:

  learn       observe(original, english)   the owner's OWN words after translation (Telugu, Hindi and Tenglish statements count too). Deterministic rules,
                                           no model: "my name is X", "my wife is Priya", "Ravi is my brother", "call me X", and a capitalised unknown token
                                           after a possessive ("Priya's folder") or a verb ("call Priya") only at the THIRD sighting. The same name in two
                                           scripts, or a native-script token the translator answered with a capitalised word, becomes an alias.
              learn_profiles(profiles)     Chrome profile names and accounts from the desktop plugin's registry (source "profile", the lowest rank).
  keep        NameStore                    <data>/names.json, atomic writes, a lock, caps on every list and on the file. The owner can read and edit it.
              forget(name)                 removes the entry and records a TOMBSTONE: inference (repeated, profile, pairing) never learns it again.
  use         NameIndex.find(text)         spans for translate.mask: a name is kept as written, a known native-script or romanized alias becomes ONE
                                           canonical English spelling. Manual glossary terms win. Tenglish verbs ("rayi") are never names.
              hint_for(request)            "brother: Ravi; mother: Lakshmi": relations and canonical names only, max 20, for the LOCAL decider and
                                           resolvers when a request says "my brother" or "call Amma". The online model never gets this table.
              resolve(reference)           the entries a phrase like "my brother", "Amma" or "Priya's" points to.
  talk        list_text / quick_answer     "what names do you know", "forget Priya" (a rule, no model). pending_question: a low-confidence guess is asked
                                           ONCE (ask_form), never again.

HARD RULES
  * Names are NEVER learned from tool results, web pages, files, screen text or model output (prompt injection): the only caller is the gateway's face path
    (translate.observe_owner), which sees the owner's typed or spoken message, and the profile registry the node read from his own Chrome. Nothing here takes text
    from a tool, and no tool can add a name: the plugin has list, forget and answer only.
  * Everything stays on this PC. Nothing in this file opens a network connection.
  * A broken or hand-edited file is never overwritten: the store says so and learns nothing until the file parses again.

Standard library only (this file is also loaded BY PATH, like translate.py, by processes that have no `jarvis` package).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

VERSION = 1
KINDS = ("self", "family", "friend", "place", "app", "other")
SOURCES = ("profile", "repeated", "stated", "confirmed")          # low to high
RANK = {s: i for i, s in enumerate(SOURCES)}

LEARN_AT = 3                       # a name seen only by repeated use is learned at this count, not before
MAX_ENTRIES = 200
MAX_ALIASES = 8
MAX_CANDIDATES = 200
MAX_TOMBSTONES = 300
MAX_RECENT = 64
RECENT_WINDOW_S = 600.0            # the same message seen again inside this window is one sighting (a retry, a resend), not another
MAX_FILE_BYTES = 256 * 1024
MAX_TEXT = 2000
MAX_HINT = 20

NOTICE_PREFIXES = ("[A notice from JARVIS", "Notice (")      # the face's own proactive turns are user-role messages that are not the owner's words
_STAMP = re.compile(r"^\s*\[\d{4}-\d\d-\d\d[ T]\d\d:\d\d(?::\d\d)?\]\s*")

NATIVE = "\u0900-\u097f\u0c00-\u0c7f\u200c\u200d"
_NATIVE_CH = re.compile(f"[{NATIVE}]")
_NATIVE_RUN = re.compile(f"[{NATIVE}]+")
_ZW = dict.fromkeys((0x200C, 0x200D))


# ====================================================================================================================
# 1. Words: relations, and what is never a name
# ====================================================================================================================

# relation -> (kind, surface forms). Telugu and Hindi kin words in Latin letters are here too: the translator may leave "Amma" as it is.
_REL_GROUPS: dict[str, tuple[str, str]] = {
    "wife": ("family", "wife bharya bhaarya patni biwi bivi"),
    "husband": ("family", "husband bhartha bharta pati mogudu"),
    "mother": ("family", "mother mom mum mummy mommy amma maa mata"),
    "father": ("family", "father dad daddy papa nanna appa pitaji"),
    "brother": ("family", "brother bro anna annayya thammudu bhaiya bhaiyya bhai"),
    "sister": ("family", "sister sis akka chelli chelleli didi behen behan"),
    "son": ("family", "son koduku beta"),
    "daughter": ("family", "daughter kuthuru kooturu beti"),
    "grandmother": ("family", "grandmother grandma granny ammamma nani dadi"),
    "grandfather": ("family", "grandfather grandpa thatha tatha"),
    "uncle": ("family", "uncle mama mavayya chacha babai"),
    "aunt": ("family", "aunt aunty auntie atta pinni chachi massi"),
    "cousin": ("family", "cousin"),
    "nephew": ("family", "nephew"),
    "niece": ("family", "niece"),
    "father-in-law": ("family", "father-in-law"),
    "mother-in-law": ("family", "mother-in-law"),
    "brother-in-law": ("family", "brother-in-law"),
    "sister-in-law": ("family", "sister-in-law"),
    "friend": ("friend", "friend buddy dost mitrudu snehithudu snehitudu"),
    "roommate": ("friend", "roommate flatmate"),
    "boss": ("other", "boss manager"),
    "colleague": ("other", "colleague coworker"),
    "teacher": ("other", "teacher"),
    "doctor": ("other", "doctor"),
    "neighbour": ("other", "neighbour neighbor"),
    "landlord": ("other", "landlord"),
}
REL: dict[str, tuple[str, str]] = {}                         # surface -> (relation, kind)
for _rel, (_kind, _forms) in _REL_GROUPS.items():
    for _f in _forms.split():
        REL[_f] = (_rel, _kind)
_REL_ALT = "|".join(sorted((re.escape(s) for s in REL), key=len, reverse=True))
_MODIFIER = r"(?:(?:younger|elder|older|little|big|eldest|youngest|best|close|dear|own)\s+)?"

# the same words in the owner's scripts, so a pair can be found next to them (a name sits beside its relation word)
NATIVE_REL: dict[str, str] = {}
for _rel, _words in {
    "wife": "భార్య భార్యా पत्नी बीवी", "husband": "భర్త पति", "mother": "అమ్మ తల్లి माँ मां माता", "father": "నాన్న తండ్రి पिता पापा",
    "brother": "అన్న అన్నయ్య తమ్ముడు भाई", "sister": "అక్క చెల్లి చెల్లెలు बहन दीदी", "son": "కొడుకు బిడ్డ बेटा", "daughter": "కూతురు बेटी",
    "friend": "స్నేహితుడు స్నేహితురాలు మిత్రుడు दोस्त मित्र", "uncle": "మామయ్య బాబాయ్ चाचा मामा", "aunt": "అత్త పిన్ని चाची मौसी",
    "grandmother": "అమ్మమ్మ నానమ్మ दादी नानी", "grandfather": "తాతయ్య తాత दादा नाना",
}.items():
    for _w in _words.split():
        NATIVE_REL[_w] = _rel

# possessives and glue words of the owner's languages: never the name itself
NATIVE_FUNCTION = frozenset("""నా మా నీ మీ మన వాళ్ళ పేరు ఈ ఆ ఒక కి కు ను ని తో లో నుండి యొక్క గారు గారి మరియు చేసి చేయి చేయండి ఉంది ఉన్నాడు ఉన్నారు కాల్ మెయిల్ ఫోన్ ఫోల్డర్ ఫైల్ ఓపెన్ ఓపెన్చేయి ఇవ్వు పంపు చెప్పు
मेरा मेरी मेरे हमारा हमारी का की के को से में पर और है हैं नाम यह वह एक करो करें कीजिए कॉल मेल फोन फोल्डर फाइल खोलो भेजो बताओ""".split())

# case endings a name takes: Telugu "ప్రియకి" (to Priya), Hindi "प्रिया को"/"प्रियाजी". Longest first.
_TE_SUFFIX = ("నుండి", "యొక్క", "గారి", "గారు", "కోసం", "తో", "కి", "కు", "ను", "ని", "లో", "ల", "గా")
_HI_SUFFIX = ("जी", "को", "का", "की", "के", "से", "में", "ने", "पर")
_SUFFIX_RX = "|".join(re.escape(s) for s in sorted(_TE_SUFFIX + _HI_SUFFIX, key=len, reverse=True))

_HARD_WORDS = """i me my mine we us our you your he him his she her it its they them their this that these those the a an and or but so if as at by for from in
 into of on onto to up with is are was were be been am do does did has have had not no yes please also then now just only still again already actually really
 hello hi hey thanks thank bye ok okay oh well sure sorry fine good great nice bad best ready busy tired done late early free hungry happy sad sick here there
 back home work office school college today tomorrow tonight yesterday morning evening night noon what when where who whom why how which can could would should
 shall may might must let call mail email message text phone ring ping whatsapp open close save delete show play set type write read send make create find
 search go start stop pause resume run copy paste clean remind remember forget list name names jarvis monday tuesday wednesday thursday friday saturday sunday
 january february march april may june july august september october november december mr mrs ms sir madam dr""".split()
HARD = frozenset(_HARD_WORDS) | frozenset(REL) | frozenset(NATIVE_REL)
NAME_TOKEN = r"[A-Z][A-Za-z'’\-]{1,24}"
_NAME_RX = rf"{NAME_TOKEN}(?:\s+{NAME_TOKEN}){{0,2}}"


# ====================================================================================================================
# 2. The other modules of this folder, loaded by name or by path (stdlib only on both routes)
# ====================================================================================================================

_mods: dict[str, Any] = {}


def _sibling(name: str):
    if name in _mods:
        return _mods[name]
    import sys
    mod = None
    try:
        import importlib
        mod = importlib.import_module(f"jarvis.plugins._shared.{name}")
    except Exception:                                                          # noqa: BLE001 - no package: load by path, or do without
        try:
            import importlib.util
            key = f"jarvis_{name}"
            mod = sys.modules.get(key)
            if mod is None:
                spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(f"{name}.py"))
                mod = importlib.util.module_from_spec(spec)
                sys.modules[key] = mod
                spec.loader.exec_module(mod)
        except Exception:                                                      # noqa: BLE001
            sys.modules.pop(f"jarvis_{name}", None)
            mod = None
    _mods[name] = mod
    return mod


def _fold(s: str) -> str:
    return unicodedata.normalize("NFC", str(s or "")).translate(_ZW).casefold()


def norm(s: str) -> str:
    """The key of a name: Unicode NFC, zero-width joiners dropped, case folded, spaces collapsed."""
    return " ".join(_fold(s).split())


def variant_key(s: str) -> str:
    """A key under which spelling variants meet: "Priyaa", "Priya" and "Priyah" are one name. Repeated letters collapse, a final h goes."""
    k = re.sub(r"(.)\1+", r"\1", norm(s))
    return k[:-1] if len(k) > 3 and k.endswith("h") else k


_never_cache: dict[str, frozenset] = {}


def _never_sets() -> tuple[frozenset, frozenset]:
    """(never a name at all, words of English that are not names unless the owner's own sentence says so). The first holds the Tenglish and Hindi verbs
    and function words of the language layer and the lexicon ('rayi' and the like), so a verb is never taken for a person."""
    if "hard" not in _never_cache:
        hard: set[str] = set(HARD)
        soft: set[str] = set()
        tr = _sibling("translate")
        if tr is not None:
            for attr in ("_TE_STRONG", "_HI_STRONG"):
                hard |= {_fold(w) for w in getattr(tr, attr, ())}
            soft |= {_fold(w) for w in getattr(tr, "_EN_COMMON", ())}
            hard |= {_fold(w) for w in getattr(tr, "_BUILTIN_APPS", ())}
        tg = _sibling("tenglish")
        if tg is not None:
            try:
                for forms in tg.LEXICON.values():
                    hard |= {_fold(f) for f, _ in forms}
                for attr in ("AUX", "CONJ"):
                    hard |= {_fold(w) for w in getattr(tg, attr, ())}
                soft |= {_fold(w) for w in getattr(tg, "STOP", ())}
            except Exception:                                                  # noqa: BLE001
                pass
        _never_cache["hard"], _never_cache["soft"] = frozenset(hard), frozenset(soft - hard)
    return _never_cache["hard"], _never_cache["soft"]


def never_name(word: str, *, strict: bool = True) -> bool:
    """True for a word that cannot be a name: a Telugu or Hindi verb in Latin letters, a relation word, a pronoun, a day, an app. `strict` also refuses the plain
    English words of the language layer's list (a stray capitalised 'Open'); an explicit "my son is Will" is allowed through with strict=False."""
    w = _fold(word)
    hard, soft = _never_sets()
    if not w or w in hard:
        return True
    return strict and w in soft


def _manual_terms() -> set[str]:
    tr = _sibling("translate")
    out: set[str] = set()
    if tr is not None:
        try:
            out |= {norm(t) for t in tr.load_glossary()}
        except Exception:                                                      # noqa: BLE001
            pass
    return out


# ====================================================================================================================
# 3. The store: <data>/names.json
# ====================================================================================================================

def data_dir() -> Path:
    env = os.environ.get("JARVIS_DATA_DIR", "").strip()
    return Path(env) if env else (Path(r"D:\jarvis\data") if os.name == "nt" else Path.home() / ".jarvis-data")


def names_path() -> Path:
    env = os.environ.get("JARVIS_NAMES_FILE", "").strip()
    return Path(env) if env else data_dir() / "names.json"


def _num(v: Any, default: float = 0.0) -> float:
    try:
        return float(v) if not isinstance(v, bool) else default
    except (TypeError, ValueError):
        return default


def clean_entry(raw: Any) -> dict | None:
    """One entry of the file in its one shape, or None. Tolerant: the owner may have written it by hand."""
    if not isinstance(raw, dict):
        return None
    canonical = " ".join(str(raw.get("canonical") or raw.get("name") or "").split())[:60]
    if len(canonical) < 2:
        return None
    aliases: list[str] = []
    for a in raw.get("aliases") or []:
        a = " ".join(str(a or "").split())[:60]
        if len(a) >= 2 and norm(a) != norm(canonical) and a not in aliases:
            aliases.append(a)
    kind = raw.get("kind") if raw.get("kind") in KINDS else "other"
    source = raw.get("source") if raw.get("source") in SOURCES else "stated"
    now = time.time()
    e = {"canonical": canonical, "aliases": aliases[:MAX_ALIASES], "kind": kind, "relation": " ".join(str(raw.get("relation") or "").lower().split())[:40],
         "source": source, "count": max(0, int(_num(raw.get("count"), 1))), "first_seen": _num(raw.get("first_seen"), now), "last_seen": _num(raw.get("last_seen"), now),
         "confidence": max(0.0, min(1.0, _num(raw.get("confidence"), 0.8)))}
    if raw.get("asked"):
        e["asked"] = True
    return e


class NameStore:
    """names.json: {"version", "names": [...], "candidates": {...}, "tombstones": [...], "recent": {...}, "profiles_sig"}. Reads are cached by (mtime, size); writes are
    atomic (a temp file in the same folder, then os.replace) and under a thread lock plus a lock file; every list has a cap and so has the file. A file that
    does not parse is NEVER overwritten: `broken` says so and every learning call does nothing until the owner fixes it."""

    def __init__(self, path: Path | str | None = None, clock: Callable[[], float] = time.time):
        self.path = Path(path) if path else names_path()
        self.clock = clock
        self._lock = threading.RLock()
        self._sig: tuple | None = None
        self._state: dict = self._blank()
        self.broken = ""

    @staticmethod
    def _blank() -> dict:
        return {"version": VERSION, "names": [], "candidates": {}, "tombstones": [], "recent": {}}

    # ---- reading
    def _load(self) -> dict:
        try:
            st = self.path.stat()
        except OSError:
            self._sig, self._state, self.broken = None, self._blank(), ""
            return self._state
        sig = (st.st_mtime_ns, st.st_size)
        if sig == self._sig:
            return self._state
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("not an object")
        except (OSError, ValueError) as e:
            self._sig, self._state, self.broken = sig, self._blank(), f"names.json cannot be read ({type(e).__name__}); nothing will be learned until it is fixed"
            return self._state
        state = self._blank()
        seen: set[str] = set()
        for item in raw.get("names") if isinstance(raw.get("names"), list) else []:
            e = clean_entry(item)
            if e and norm(e["canonical"]) not in seen:
                seen.add(norm(e["canonical"]))
                state["names"].append(e)
        cands = raw.get("candidates")
        if isinstance(cands, dict):
            for k, v in list(cands.items())[:MAX_CANDIDATES]:
                if isinstance(v, dict) and isinstance(k, str):
                    state["candidates"][norm(k)] = {"name": " ".join(str(v.get("name") or k).split())[:60], "count": max(0, int(_num(v.get("count")))),
                                                    "first_seen": _num(v.get("first_seen")), "last_seen": _num(v.get("last_seen")),
                                                    "aliases": [str(a)[:60] for a in (v.get("aliases") or []) if isinstance(a, str)][:MAX_ALIASES],
                                                    "relation": str(v.get("relation") or "")[:40], "kind": v.get("kind") if v.get("kind") in KINDS else "other"}
        tombs = raw.get("tombstones")
        if isinstance(tombs, list):
            state["tombstones"] = [norm(t) for t in tombs if isinstance(t, str) and t.strip()][-MAX_TOMBSTONES:]
        rec = raw.get("recent")
        if isinstance(rec, dict):
            state["recent"] = {str(k)[:16]: _num(v) for k, v in rec.items()}
        if isinstance(raw.get("profiles_sig"), str):
            state["profiles_sig"] = raw["profiles_sig"][:40]
        self._sig, self._state, self.broken = sig, state, ""
        return state

    def entries(self) -> list[dict]:
        with self._lock:
            return [dict(e, aliases=list(e["aliases"])) for e in self._load()["names"]]

    def tombstones(self) -> list[str]:
        with self._lock:
            return list(self._load()["tombstones"])

    def candidates(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._load()["candidates"]))

    def find(self, name: str) -> dict | None:
        """The entry whose canonical spelling or alias is `name` (spelling variants included)."""
        with self._lock:
            return _find(self._load()["names"], name, copy=True)

    # ---- writing
    def update(self, fn: Callable[[dict], bool]) -> bool:
        """fn(state) mutates the state and returns True if it changed. Reads the file again first (another process may have written it), writes atomically."""
        with self._lock, _FileLock(self.path):
            self._sig = None
            state = self._load()
            if self.broken:
                return False
            if not fn(state):
                return False
            return self._write(state)

    def _write(self, state: dict) -> bool:
        _cap(state)
        out = {"version": VERSION, "names": state["names"], "candidates": state["candidates"], "tombstones": state["tombstones"], "recent": state["recent"]}
        if state.get("profiles_sig"):
            out["profiles_sig"] = state["profiles_sig"]
        text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
        while len(text.encode("utf-8")) > MAX_FILE_BYTES and (state["candidates"] or state["recent"] or state["tombstones"] or len(state["names"]) > 1):
            if state["recent"]:
                state["recent"].clear()
            elif state["candidates"]:
                oldest = min(state["candidates"], key=lambda k: state["candidates"][k].get("last_seen", 0))
                state["candidates"].pop(oldest)
            elif state["tombstones"]:
                state["tombstones"] = state["tombstones"][len(state["tombstones"]) // 2 + 1:]
            else:
                weakest = min(state["names"], key=lambda e: (RANK[e["source"]], e["confidence"], e["last_seen"]))
                state["names"].remove(weakest)
            out.update(names=state["names"], candidates=state["candidates"], tombstones=state["tombstones"], recent=state["recent"])
            text = json.dumps(out, ensure_ascii=False, indent=1) + "\n"
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".names-", suffix=".tmp", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(text)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
        except OSError:
            return False
        self._sig = None
        return True

    # ---- the owner's commands
    def forget(self, query: str) -> dict | None:
        """Remove the entry `query` names (a name, an alias, or a relation held by exactly one entry) and tombstone every spelling of it. Returns the removed entry."""
        removed: list[dict] = []

        def go(state: dict) -> bool:
            hit = _find(state["names"], query)
            if hit is None:
                same = [e for e in state["names"] if _rel_match(e["relation"], query)]
                hit = same[0] if len(same) == 1 else None
            cand = state["candidates"].get(norm(query)) or state["candidates"].get(variant_key(query))
            if hit is None and cand is None:
                return False
            keys: list[str] = []
            if hit is not None:
                state["names"].remove(hit)
                removed.append(hit)
                keys += [hit["canonical"], *hit["aliases"]]
            if cand is not None:
                state["candidates"] = {k: v for k, v in state["candidates"].items() if v is not cand}
                keys.append(cand["name"])
            keys.append(query)
            for k in keys:
                for form in {norm(k), variant_key(k)}:
                    if form and form not in state["tombstones"]:
                        state["tombstones"].append(form)
            return True

        self.update(go)
        return removed[0] if removed else None

    def answer(self, name: str, choice: str) -> str:
        """The owner answered a question about `name`: family | friend | place | other | self | yes | no | not a name. Returns a sentence."""
        choice = norm(choice)
        msg = {"v": ""}

        def go(state: dict) -> bool:
            e = _find(state["names"], name)
            if e is None:
                return False
            e["asked"] = True
            if choice in ("no", "not a name", "not-a-name", "none"):
                state["names"].remove(e)
                for k in [e["canonical"], *e["aliases"]]:
                    for form in {norm(k), variant_key(k)}:
                        if form not in state["tombstones"]:
                            state["tombstones"].append(form)
                msg["v"] = f"Okay, {e['canonical']} is not a name. I will not learn it again."
                return True
            if choice in ("yes", "y"):
                e["source"], e["confidence"] = "confirmed", max(e["confidence"], 0.95)
            elif choice in ("family", "friend", "place", "other", "self"):
                e["kind"], e["source"], e["confidence"] = choice, "confirmed", max(e["confidence"], 0.95)
            else:
                return False
            msg["v"] = f"Got it: {e['canonical']}" + (f" is {e['kind']}" if e["kind"] in ("family", "friend", "place") else " is noted") + "."
            return True

        self.update(go)
        return msg["v"]

    def remember_asked(self, name: str) -> None:
        def go(state: dict) -> bool:
            e = _find(state["names"], name)
            if e is None or e.get("asked"):
                return False
            e["asked"] = True
            return True
        self.update(go)


def _cap(state: dict) -> None:
    names = state["names"]
    if len(names) > MAX_ENTRIES:
        names.sort(key=lambda e: (RANK[e["source"]], e["count"], e["last_seen"]), reverse=True)
        del names[MAX_ENTRIES:]
    for e in names:
        del e["aliases"][MAX_ALIASES:]
    if len(state["candidates"]) > MAX_CANDIDATES:
        keep = sorted(state["candidates"].items(), key=lambda kv: kv[1].get("last_seen", 0), reverse=True)[:MAX_CANDIDATES]
        state["candidates"] = dict(keep)
    del state["tombstones"][:-MAX_TOMBSTONES]
    if len(state["recent"]) > MAX_RECENT:
        keep = sorted(state["recent"].items(), key=lambda kv: kv[1], reverse=True)[:MAX_RECENT]
        state["recent"] = dict(keep)


class _FileLock:
    """A lock file next to names.json (the gateway and the plugin host are two processes). Waits up to ~1.5 s, drops a lock older than 10 s, and then goes on
    anyway: an unlocked write is still atomic, at worst the later of two near-simultaneous writes wins."""

    def __init__(self, path: Path):
        self.p = path.with_name(path.name + ".lock")
        self.fd: int | None = None

    def __enter__(self):
        end = time.monotonic() + 1.5
        while True:
            try:
                self.p.parent.mkdir(parents=True, exist_ok=True)
                self.fd = os.open(str(self.p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.p.stat().st_mtime > 10:
                        self.p.unlink()
                        continue
                except OSError:
                    pass
                if time.monotonic() > end:
                    return self
                time.sleep(0.02)
            except OSError:
                return self

    def __exit__(self, *exc):
        if self.fd is not None:
            try:
                os.close(self.fd)
                self.p.unlink()
            except OSError:
                pass
        return False


_stores: dict[str, NameStore] = {}
_stores_lock = threading.Lock()


def get_store(path: Path | str | None = None) -> NameStore:
    """The store for names.json (one per path; JARVIS_DATA_DIR and JARVIS_NAMES_FILE are read on every call, so a test's folder is its own store)."""
    p = Path(path) if path else names_path()
    with _stores_lock:
        st = _stores.get(str(p))
        if st is None:
            st = _stores[str(p)] = NameStore(p)
        return st


def _find(names: list[dict], query: str, *, copy: bool = False) -> dict | None:
    q, v = norm(query), variant_key(query)
    if not q:
        return None
    for key in (norm, variant_key):
        for e in names:
            if key(e["canonical"]) == (q if key is norm else v) or any(key(a) == (q if key is norm else v) for a in e["aliases"]):
                return dict(e, aliases=list(e["aliases"])) if copy else e
    return None


def _rel_match(entry_rel: str, wanted: str) -> bool:
    """'younger brother' matches 'brother'; 'amma' matches a mother."""
    entry_rel, wanted = norm(entry_rel), norm(wanted)
    if not entry_rel or not wanted:
        return False
    want = REL.get(wanted, (wanted, ""))[0]
    return entry_rel == want or entry_rel.endswith(" " + want) or entry_rel == wanted


# ====================================================================================================================
# 4. Using the names: spans for the translator's masking
# ====================================================================================================================

class NameIndex:
    """What the translator's mask() asks: where do learned names sit in this text, and what does each go back to. A name written in Latin letters is kept as
    written; a known alias (native script, a romanized variant) goes back as the ONE canonical English spelling. Surfaces the manual glossary lists are skipped
    (manual wins: the glossary terms are masked first and are left exactly as written). A Tenglish verb or a relation word is never a name."""

    def __init__(self, entries: Iterable[dict], manual: Iterable[str] = ()):
        self.table: dict[str, tuple[str, bool, bool]] = {}          # norm(surface) -> (canonical, is_canonical, needs_capital)
        manual_keys = {norm(m) for m in manual}
        hard, soft = _never_sets()
        latin: list[str] = []
        native: list[str] = []
        for e in entries:
            canon = e["canonical"]
            for surface in [canon, *e["aliases"]]:
                k = norm(surface)
                if len(k) < 2 or k in manual_keys or k in self.table:
                    continue
                if not _NATIVE_CH.search(surface):
                    if " " not in k and (k in hard or k in REL):
                        continue                                    # 'rayi' and kin words are never names, whatever the file says
                self.table[k] = (canon, norm(surface) == norm(canon), (k in soft) and not _NATIVE_CH.search(surface))
                (native if _NATIVE_CH.search(surface) else latin).append(surface)
        self.latin_re = self._compile(latin, native=False)
        self.native_re = self._compile(native, native=True)

    @staticmethod
    def _compile(surfaces: list[str], native: bool) -> re.Pattern | None:
        if not surfaces:
            return None
        alt = "|".join(re.escape(s).replace(r"\ ", r"\s+") for s in sorted(set(surfaces), key=lambda s: (-len(s), s)))
        if native:
            return re.compile(rf"(?<![{NATIVE}\w])(?:{alt})(?=(?:{_SUFFIX_RX})?(?![{NATIVE}\w]))")
        return re.compile(rf"(?<![\w])(?:{alt})(?![\w])", re.I)

    @property
    def empty(self) -> bool:
        return not self.table

    def find(self, text: str) -> list[tuple[int, int, str]]:
        """[(start, end, text it goes back to)] without overlaps, in order."""
        out: list[tuple[int, int, str]] = []
        for rx in (self.latin_re, self.native_re):
            if rx is None:
                continue
            for m in rx.finditer(text):
                hit = self.table.get(norm(m.group(0)))
                if hit is None:
                    continue
                canon, is_canon, needs_cap = hit
                if needs_cap and not m.group(0)[:1].isupper():
                    continue
                out.append((m.start(), m.end(), m.group(0) if is_canon and not _NATIVE_CH.search(m.group(0)) else canon))
        out.sort(key=lambda t: (t[0], -(t[1] - t[0])))
        res: list[tuple[int, int, str]] = []
        for s, e, r in out:
            if not res or s >= res[-1][1]:
                res.append((s, e, r))
        return res

    def mentions(self, text: str) -> list[str]:
        """The canonical names a text mentions."""
        seen: list[str] = []
        for s, e, _ in self.find(text):
            canon = self.table[norm(text[s:e])][0]
            if canon not in seen:
                seen.append(canon)
        return seen


_index_cache: dict[tuple, NameIndex] = {}


def index(store: NameStore | None = None, manual: Iterable[str] | None = None) -> NameIndex | None:
    """The index for the default store, rebuilt when the file or the manual list changes. None when there are no names (the common case costs nothing)."""
    try:
        st = store or get_store()
        entries = st.entries()
        if not entries:
            return None
        man = tuple(sorted(norm(m) for m in (manual if manual is not None else ())))
        key = (str(st.path), st._sig, man)
        ix = _index_cache.get(key)
        if ix is None:
            if len(_index_cache) > 8:
                _index_cache.clear()
            ix = _index_cache[key] = NameIndex(entries, man)
        return None if ix.empty else ix
    except Exception:                                                          # noqa: BLE001 - the language layer never breaks for a names file
        return None


def merged_surfaces(manual: Iterable[str], entries: Iterable[dict]) -> dict[str, str]:
    """surface -> canonical for the manual list and the learned entries together: a manual term maps to itself and wins over a learned alias for the same surface."""
    out: dict[str, str] = {}
    for m in manual:
        out[norm(m)] = m
    for e in entries:
        for s in [e["canonical"], *e["aliases"]]:
            out.setdefault(norm(s), e["canonical"])
    return out


# ====================================================================================================================
# 5. Learning
# ====================================================================================================================

@dataclass
class Observation:
    learned: list[str] = field(default_factory=list)          # canonical names newly stored
    updated: list[str] = field(default_factory=list)          # existing entries changed (alias, relation, count)
    candidates: list[str] = field(default_factory=list)       # seen but not yet learned
    skipped: str = ""

    def __bool__(self) -> bool:
        return bool(self.learned or self.updated or self.candidates)


_SENT_SPLIT = re.compile(r"(?<=[.!?।])\s+|\n+")
_LEAD = re.compile(r"(?i)^(?:(?:hey |hi |hello |ok |okay |so |and |also |well |oh )+)?(?:jarvis[, ]+)?")

_SELF_MY_NAME = re.compile(rf"(?i:\bmy\s+name(?:['’]s|\s+is)\s+|\bi\s+am\s+called\s+|\bi['’]m\s+called\s+|\bcall\s+me\s+)(?P<n>{_NAME_RX})")
_SELF_I_AM = re.compile(rf"^(?i:(?:hi|hello|hey)\W+)?(?i:i\s+am|i['’]m)\s+(?P<n>{_NAME_RX})(?:\s*[,.!;]|\s+(?i:and|here|from|speaking|again)\b.*)?\W*$")
_SELF_THIS_IS = re.compile(rf"^(?i:(?:hi|hello|hey)\W+)?(?i:this\s+is)\s+(?P<n>{_NAME_RX})\W*$")
_REL_MY = re.compile(rf"(?i:\bmy\s+){_MODIFIER}(?P<r>(?i:{_REL_ALT}))(?i:(?:['’]s\s+name)?(?:\s+(?:is|was))?(?:\s+(?:called|named))?[\s,]+)(?P<n>{_NAME_RX})")
# Tenglish and Hinglish said in Latin letters ("naa bharya peru Priya", "maa amma Lakshmi", "mera bhai Ravi hai", "naa peru Satish"): the language detector may call
# such a line English and never translate it, so the same facts are read from the original too
_MY_ROM = r"(?i:\b(?:naa|na|maa|ma|naadhi|mera|meri|mere|hamara|hamari)\s+)"
_ROM_REL = re.compile(rf"{_MY_ROM}(?P<r>(?i:{_REL_ALT}))(?i:(?:\s+(?:peru|pEru|pera|naam|ka\s+naam|ki\s+naam|name|ante|hai))*[\s,]+)(?P<n>{_NAME_RX})")
_ROM_SELF = re.compile(rf"(?i:\b(?:naa|na|maa)\s+(?:peru|pera)\s+|\bmera\s+naam\s+|\bmeraa\s+naam\s+|\bmeri\s+naam\s+|\bnaam\s+hai\s+)(?P<n>{_NAME_RX})")
_REL_IS_MY = re.compile(rf"(?P<n>{_NAME_RX})(?i:\s*,?\s*(?:is|was|are)?\s*(?:my|our)\s+){_MODIFIER}(?P<r>(?i:{_REL_ALT}))\b")
_CAND_POSS = re.compile(rf"(?<![\w'’])(?P<n>{NAME_TOKEN})(?:['’]s)\s+(?i:folder|phone|number|birthday|email|mail|address|photos?|files?|laptop|house|car|documents?|"
                        r"profile|account|calendar|chat|messages?|contact|mobile|whatsapp)\b")
_CAND_VERB = re.compile(rf"(?i:\b(?:call|phone|text|mail|email|message|whatsapp|ping|ring)\s+)(?P<n>{NAME_TOKEN})\b(?!['’]?[a-z])")
_DUAL_LOOSE = [re.compile(rf"(?P<l>\b{NAME_TOKEN})\s*[(\[/=\-–,]?\s*(?P<n>[{NATIVE}]{{2,}})\s*[)\]]?"),
               re.compile(rf"(?P<n>[{NATIVE}]{{2,}})\s*[(\[/=\-–,]?\s*(?P<l>{NAME_TOKEN})\b")]
# the owner glossing a name in both scripts with nothing else to go on: a bracket, slash, equals or dash between them
_DUAL_STRICT = [re.compile(rf"(?P<l>\b{NAME_TOKEN})\s*(?:\(\s*|\[\s*|/|=|\s[-–]\s)\s*(?P<n>[{NATIVE}]{{2,}})\s*[)\]]?"),
                re.compile(rf"(?P<n>[{NATIVE}]{{2,}})\s*(?:\(\s*|\[\s*|/|=|\s[-–]\s)\s*(?P<l>{NAME_TOKEN})\b\s*[)\]]?"),
                re.compile(rf"^\W*(?P<l>{NAME_TOKEN})\s+(?P<n>[{NATIVE}]{{2,}})\W*$"), re.compile(rf"^\W*(?P<n>[{NATIVE}]{{2,}})\s+(?P<l>{NAME_TOKEN})\W*$")]


def _clean_name(raw: str, *, strict: bool) -> str | None:
    """A captured name without the words that clung to it ("Hi Priya", "Ravi Please"), or None when what is left cannot be a name."""
    s = re.sub(r"['’]s$", "", raw.strip())
    toks = [t.strip("'’-") for t in s.split()]
    while toks and (never_name(toks[0], strict=strict) or len(toks[0]) < 2):
        toks.pop(0)
    out: list[str] = []
    for t in toks:
        if never_name(t, strict=strict) or len(t) < 2:
            break
        out.append(t)
    if not out:
        return None
    name = " ".join(out)
    return name if 2 <= len(name) <= 40 else None


def _blocked(name: str, manual: set[str], tombs: list[str], *, honour_tombstone: bool) -> bool:
    k = norm(name)
    if k in manual:
        return True                                              # the manual glossary already covers it
    return honour_tombstone and (k in tombs or variant_key(name) in tombs)


def _upsert(state: dict, name: str, *, kind: str, relation: str = "", source: str, confidence: float, aliases: Sequence[str] = (), now: float) -> tuple[dict, bool]:
    """Add the name or merge into the entry it already is (spelling variants meet). A higher source upgrades; a lower one never overwrites. (entry, created)"""
    e = _find(state["names"], name)
    created = e is None
    if created:
        e = {"canonical": name, "aliases": [], "kind": kind, "relation": relation, "source": source, "count": 0, "first_seen": now, "last_seen": now,
             "confidence": confidence}
        state["names"].append(e)
    elif norm(name) != norm(e["canonical"]) and name not in e["aliases"]:
        e["aliases"].append(name)                                 # a spelling variant of a known name: one canonical spelling, the rest are aliases
    e["count"] += 1
    e["last_seen"] = now
    if not created:
        stronger = RANK[source] > RANK[e["source"]] or (RANK[source] == RANK[e["source"]] and source in ("stated", "confirmed"))
        if stronger:
            if kind != "other" or e["kind"] == "other":
                e["kind"] = kind if not (e["kind"] == "self" and kind != "self") else "self"
            if relation:
                e["relation"] = relation
            e["source"] = source
        elif relation and not e["relation"]:
            e["relation"] = relation
            if e["kind"] == "other" and kind != "other":
                e["kind"] = kind
        e["confidence"] = round(max(e["confidence"], confidence), 2)
    for a in aliases:
        if a and norm(a) != norm(e["canonical"]) and norm(a) not in {norm(x) for x in e["aliases"]} and len(e["aliases"]) < MAX_ALIASES:
            e["aliases"].append(a)
    return e, created


def _strip_suffix(tok: str) -> str:
    for suf in sorted(_TE_SUFFIX + _HI_SUFFIX, key=len, reverse=True):
        if tok.endswith(suf) and len(tok) - len(suf) >= 2:
            return tok[: -len(suf)]
    return tok


def _unexplained_native(original: str) -> list[str]:
    """Native-script tokens that are neither a function word, a relation word nor a known verb: what could be a name."""
    tg = _sibling("tenglish")
    known: set[str] = set(NATIVE_FUNCTION) | set(NATIVE_REL)
    if tg is not None:
        for attr in ("NATIVE_CONJ", "NATIVE_AUX", "NATIVE_STOP", "WEATHER"):
            known |= {w for w in getattr(tg, attr, ()) if isinstance(w, str)}
        try:
            known |= {k for k in tg._VERBS if _NATIVE_CH.search(k)}
        except Exception:                                                      # noqa: BLE001
            pass
    out = []
    for t in _NATIVE_RUN.findall(original):
        if t in known or _strip_suffix(t) in known or len(t) < 2:
            continue
        out.append(t)
    return out


def _native_alias_for(name: str, original: str, *, relation: str = "") -> str | None:
    """The native-script spelling of `name` in the owner's original text, or None. Three signals, strongest first: the name written in both scripts side by side,
    the token beside a native relation word, and the only unexplained native token of the message."""
    if not _NATIVE_CH.search(original):
        return None
    nl = norm(name)
    for rx in _DUAL_LOOSE:
        for m in rx.finditer(original):
            if variant_key(m.group("l")) == variant_key(name) or nl.startswith(norm(m.group("l"))):
                return _strip_suffix(m.group("n"))
    toks = _NATIVE_RUN.findall(original)
    for i, t in enumerate(toks):
        if t in NATIVE_REL:
            for j in (i + 1, i - 1):
                if 0 <= j < len(toks) and toks[j] not in NATIVE_FUNCTION and toks[j] not in NATIVE_REL:
                    return _strip_suffix(toks[j])
    left = _unexplained_native(original)
    if len(left) == 1:
        return _strip_suffix(left[0])
    return None


def _latin_variant_for(name: str, original: str, english: str) -> str | None:
    """A romanized spelling of `name` the owner used ("Priyaa" for "Priya") in a message that was translated."""
    if original == english or _NATIVE_CH.search(original) and not re.search(r"[A-Za-z]", original):
        return None
    for tok in re.findall(r"[A-Z][A-Za-z'’\-]{2,24}", original):
        if norm(tok) == norm(name) or never_name(tok):
            continue
        if difflib.SequenceMatcher(None, variant_key(tok), variant_key(name)).ratio() >= 0.75 and tok not in re.findall(r"[A-Z][A-Za-z]+", english):
            return tok
    return None


def observe(original: str, english: str | None = None, *, store: NameStore | None = None, manual: Iterable[str] | None = None) -> Observation:
    """Learn from ONE message of the owner: `original` as he said it, `english` as the translator made it (the same text when he wrote English). Only the
    owner's own words may come here; the caller (translate.observe_owner, the gateway's face path) is the one place that knows they are his. Never raises."""
    try:
        return _observe(original, english, store, manual)
    except Exception as e:                                                     # noqa: BLE001 - learning must never break a chat
        return Observation(skipped=f"error {type(e).__name__}")


def _observe(original: str, english: str | None, store: NameStore | None, manual: Iterable[str] | None) -> Observation:
    obs = Observation()
    original = _STAMP.sub("", str(original or "")).strip()[:MAX_TEXT]
    english = _STAMP.sub("", str(english if english is not None else original)).strip()[:MAX_TEXT]
    if not english or original.startswith(NOTICE_PREFIXES) or english.startswith(NOTICE_PREFIXES):
        obs.skipped = "not the owner's words"
        return obs
    st = store or get_store()
    man = {norm(m) for m in manual} if manual is not None else _manual_terms()
    now = st.clock()
    key = hashlib.sha256((original + "\0" + english).encode("utf-8")).hexdigest()[:16]

    sentences = [s.strip() for s in _SENT_SPLIT.split(english) if s.strip()]
    found: list[dict] = []                                        # {name, kind, relation, source, conf, tier}
    for sent in (x.strip() for x in _SENT_SPLIT.split(original) if x.strip()):
        for m in _ROM_SELF.finditer(sent):
            _add(found, m.group("n"), "self", "", "stated", 0.9, man, strict=True)
        for m in _ROM_REL.finditer(sent):
            rel, kind = REL[m.group("r").lower()]
            _add(found, m.group("n"), kind, rel, "stated", 0.85, man, strict=False)
    for sent in sentences:
        sent = _LEAD.sub("", sent).strip()
        for m in _SELF_MY_NAME.finditer(sent):
            _add(found, m.group("n"), "self", "", "stated", 0.95, man, strict=True)
        m = _SELF_I_AM.match(sent)
        if m:
            _add(found, m.group("n"), "self", "", "stated", 0.8, man, strict=True)
        m = _SELF_THIS_IS.match(sent)
        if m:
            _add(found, m.group("n"), "self", "", "stated", 0.6, man, strict=True)
        for rx in (_REL_MY, _REL_IS_MY):
            for m in rx.finditer(sent):
                rel, kind = REL[m.group("r").lower()]
                prefix = re.search(r"(?i)\b(younger|elder|older|little|big|eldest|youngest)\s+" + re.escape(m.group("r")), sent)
                if prefix and kind == "family":
                    rel = f"{prefix.group(1).lower()} {rel}"
                _add(found, m.group("n"), kind, rel, "stated", 0.9, man, strict=False)
        for rx in (_CAND_POSS, _CAND_VERB):
            for m in rx.finditer(sent):
                _add(found, m.group("n"), "other", "", "repeated", 0.5, man, strict=True)

    dual = [mm for rx in _DUAL_STRICT for mm in rx.finditer(original)]
    if not found and not dual:                                    # nothing to learn: only a mention of a known name moves its count; otherwise no file is touched
        ix0 = index(st, man)
        if ix0 is None or not (ix0.mentions(english) or ix0.mentions(original)):
            return obs
    changed_holder = {"learned": [], "updated": [], "cands": [], "dup": False}

    def go(state: dict) -> bool:
        tombs = state["tombstones"]
        recent = state["recent"]
        for h, ts in list(recent.items()):
            if now - ts > RECENT_WINDOW_S:
                recent.pop(h)
        duplicate = key in recent
        recent[key] = now
        changed = not duplicate                                   # the recent table itself changed
        changed_holder["dup"] = duplicate
        ix = NameIndex(state["names"], man) if state["names"] else None
        for f in found:
            name = f["name"]
            existing = _find(state["names"], name)
            explicit = f["source"] == "stated"
            if existing is None and _blocked(name, man, tombs, honour_tombstone=True) and not explicit:
                continue
            if existing is None and explicit and _blocked(name, man, [], honour_tombstone=False):
                continue
            if duplicate:
                continue                                          # a resend of a message already counted
            if explicit and (norm(name) in tombs or variant_key(name) in tombs):
                for form in (norm(name), variant_key(name)):
                    if form in tombs:
                        tombs.remove(form)                       # the owner's own sentence is newer than "forget"
            native = _native_alias_for(name, original, relation=f["relation"])
            latin = _latin_variant_for(name, original, english)
            aliases = [a for a in (native, latin) if a]
            if f["source"] == "repeated" and existing is None:
                ck = variant_key(name)
                c = state["candidates"].get(ck) or {"name": name, "count": 0, "first_seen": now, "last_seen": now, "aliases": [], "relation": "", "kind": "other"}
                c["count"] += 1
                c["last_seen"] = now
                for a in aliases:
                    if a not in c["aliases"] and len(c["aliases"]) < MAX_ALIASES:
                        c["aliases"].append(a)
                state["candidates"][ck] = c
                changed = True
                if c["count"] >= LEARN_AT:
                    e, _ = _upsert(state, c["name"], kind="other", source="repeated", confidence=0.5, aliases=c["aliases"], now=now)
                    e["count"] = c["count"]
                    e["first_seen"] = c["first_seen"]
                    state["candidates"].pop(ck, None)
                    changed_holder["learned"].append(e["canonical"])
                else:
                    changed_holder["cands"].append(name)
                continue
            if f["kind"] == "self":
                me = next((e for e in state["names"] if e["kind"] == "self"), None)
                if me is not None and not _find(state["names"], name):
                    if f["conf"] < 0.7:
                        continue                                  # "this is Priya": a guess never makes a second self
                    if me["confidence"] < 0.7 and me["source"] == "stated":
                        me["canonical"], me["confidence"] = name, f["conf"]      # the earlier self was only a guess ("this is Meena"): he now says who he is
                        me["aliases"] = [a for a in me["aliases"] if norm(a) != norm(name)]
                        me.pop("asked", None)
                        changed_holder["updated"].append(name)
                        changed = True
                        continue
                    e, _ = _upsert(state, me["canonical"], kind="self", source="stated", confidence=f["conf"], aliases=[name, *aliases], now=now)
                    changed_holder["updated"].append(e["canonical"])
                    changed = True
                    continue
            e, created = _upsert(state, name, kind=f["kind"], relation=f["relation"], source=f["source"], confidence=f["conf"], aliases=aliases, now=now)
            state["candidates"].pop(variant_key(name), None)
            (changed_holder["learned"] if created else changed_holder["updated"]).append(e["canonical"])
            changed = True
        # dual-script pair with no sentence pattern: the owner glossed a name in both scripts
        for m in dual:
            lat, nat = m.group("l"), _strip_suffix(m.group("n"))
            if never_name(lat) or _blocked(lat, man, tombs, honour_tombstone=True) or duplicate:
                continue
            e = _find(state["names"], lat)
            if e is not None:
                if norm(nat) not in {norm(a) for a in e["aliases"]} and len(e["aliases"]) < MAX_ALIASES:
                    e["aliases"].append(nat)
                    changed_holder["updated"].append(e["canonical"])
                    changed = True
            elif not _blocked(lat, man, tombs, honour_tombstone=True):
                e, created = _upsert(state, lat, kind="other", source="stated", confidence=0.6, aliases=[nat], now=now)
                changed_holder["learned" if created else "updated"].append(e["canonical"])
                changed = True
        # a known name mentioned again: its count and last_seen move (no new learning)
        if ix is not None and not duplicate:
            for canon in ix.mentions(english) + ix.mentions(original):
                e = _find(state["names"], canon)
                if e is not None and canon not in changed_holder["learned"] and canon not in changed_holder["updated"]:
                    e["count"] += 1
                    e["last_seen"] = now
                    changed = True
        return changed

    st.update(go)
    obs.learned = list(dict.fromkeys(changed_holder["learned"]))
    obs.updated = list(dict.fromkeys(changed_holder["updated"]))
    obs.candidates = list(dict.fromkeys(changed_holder["cands"]))
    if changed_holder["dup"]:
        obs.skipped = "seen a moment ago"
    return obs


def _add(found: list[dict], raw: str, kind: str, relation: str, source: str, conf: float, manual: set[str], *, strict: bool) -> None:
    name = _clean_name(raw, strict=strict)
    if name and norm(name) not in manual and not any(f["name"] == name and f["kind"] == kind for f in found):
        found.append({"name": name, "kind": kind, "relation": relation, "source": source, "conf": conf})


# ---- the desktop plugin's Chrome profile registry ------------------------------------------------------------------

_GENERIC_PROFILE = frozenset("""work personal default home school office main guest test profile person chrome google gmail college business study shopping travel
 banking finance dev jarvis family kids private public temp new old first second third admin user""".split())


def _person_like(s: str) -> str | None:
    s = " ".join(str(s or "").split())
    if not 2 <= len(s) <= 24 or not re.fullmatch(r"[A-Za-z\u0900-\u097f\u0c00-\u0c7f]+(?: [A-Za-z\u0900-\u097f\u0c00-\u0c7f]+)?", s):
        return None
    if any(norm(w) in _GENERIC_PROFILE or never_name(w, strict=False) for w in s.split()):
        return None
    return s[:1].upper() + s[1:] if s[:1].islower() and s.isascii() else s


def learn_profiles(profiles: Iterable[dict], *, store: NameStore | None = None, manual: Iterable[str] | None = None) -> Observation:
    """Names from the Chrome profile registry the node read from the owner's own browser: a profile named like a person (and the first name in its account's
    address). The default profile or one whose name matches the account is the OWNER (self); others are 'other'. Source "profile", the lowest rank: it never
    overrides anything the owner said. Skips when the registry is unchanged since the last call."""
    obs = Observation()
    try:
        rows = [p for p in profiles if isinstance(p, dict)][:30]
        sig = hashlib.sha256(json.dumps([[p.get("name"), p.get("account"), bool(p.get("default"))] for p in rows], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
        st = store or get_store()
        man = {norm(m) for m in manual} if manual is not None else _manual_terms()
        now = st.clock()
        plan: list[tuple[str, str, float, list[str]]] = []
        for p in rows:
            pname = _person_like(p.get("name"))
            acct = str(p.get("account") or "")
            local = re.split(r"[@]", acct)[0] if "@" in acct else ""
            m = re.match(r"[A-Za-z]{3,}", local)
            token = m.group(0).capitalize() if m and not never_name(m.group(0), strict=False) and norm(m.group(0)) not in _GENERIC_PROFILE else ""
            owner = bool(p.get("default")) or bool(pname and token and norm(pname).startswith(norm(token)[:4]))
            if pname:
                plan.append((pname, "self" if owner else "other", 0.6 if owner else 0.4, [token] if token and norm(token) != norm(pname) and owner else []))
            elif token and owner:
                plan.append((token, "self", 0.5, []))

        def go(state: dict) -> bool:
            if state.get("profiles_sig") == sig:
                return False
            state["profiles_sig"] = sig
            for name, kind, conf, aliases in plan:
                if _blocked(name, man, state["tombstones"], honour_tombstone=True):
                    continue
                me = next((e for e in state["names"] if e["kind"] == "self"), None)
                if kind == "self" and me is not None and not _find(state["names"], name):
                    kind = "other"                                # the owner already told us who he is
                existing = _find(state["names"], name)
                e, created = _upsert(state, name, kind=kind, source="profile", confidence=conf, aliases=aliases, now=now)
                (obs.learned if created else obs.updated).append(e["canonical"])
            return True

        st.update(go)
    except Exception as e:                                                     # noqa: BLE001
        obs.skipped = f"error {type(e).__name__}"
    return obs


# ====================================================================================================================
# 6. References: "my brother", "Amma", "Priya's folder"; the compact hint
# ====================================================================================================================

_REF_REL = re.compile(rf"(?i)(?<![\w-])(?:my|our)\s+{_MODIFIER}(?P<r>{_REL_ALT})\b|(?<![\w-])(?:call|phone|text|mail|email|message|whatsapp|ping|ring|ask|tell|"
                      rf"open|show|send)\s+(?:to\s+)?(?P<b>{_REL_ALT})\b")


def reference_relations(text: str) -> list[str]:
    """The relations a request refers to ('my brother', 'call Amma'): canonical relation words, in order."""
    out: list[str] = []
    for m in _REF_REL.finditer(str(text or "")[:1000]):
        rel = REL[(m.group("r") or m.group("b")).lower()][0]
        if rel not in out:
            out.append(rel)
    return out


def resolve(reference: str, store: NameStore | None = None) -> list[dict]:
    """The entries a phrase points to: by relation ('my brother', 'Amma'), by name or alias ('Priya', 'Priya's folder'). Several brothers: several entries (the
    caller asks which)."""
    st = store or get_store()
    entries = st.entries()
    if not entries:
        return []
    ref = str(reference or "")[:300]
    hits: list[dict] = []
    for rel in reference_relations(ref) or ([REL[norm(ref)][0]] if norm(ref) in REL else []):
        hits += [e for e in entries if _rel_match(e["relation"], rel) and e not in hits]
    ix = NameIndex(entries, ())
    for canon in ix.mentions(ref):
        e = _find(entries, canon)
        if e is not None and e not in hits:
            hits.append(e)
    if re.search(r"(?i)\b(?:my ?self|me)\b", ref) and not hits:
        hits += [e for e in entries if e["kind"] == "self"]
    return hits


def hint_for(request: str | None = None, store: NameStore | None = None, limit: int = MAX_HINT) -> str:
    """The compact 'known people' line for the LOCAL decider and resolvers: "brother: Ravi; mother: Lakshmi". Relations and canonical names only, at most
    `limit` (20) entries, no aliases, counts, dates or places. With a request it lists only the people the request points to ('' when it points to none, so an
    ordinary command costs no prompt space); without one, the people in order of use. Never sent to an online model: callers are the loopback decider and resolvers."""
    try:
        st = store or get_store()
        entries = [e for e in st.entries() if e["kind"] in ("self", "family", "friend", "other")]
        if request is not None:
            if not entries:
                return ""
            picked = [e for e in resolve(request, st) if e["kind"] in ("self", "family", "friend", "other")]
        else:
            picked = sorted(entries, key=lambda e: (RANK[e["source"]], e["count"], e["last_seen"]), reverse=True)
        parts = []
        for e in picked[: max(0, min(limit, MAX_HINT))]:
            rel = "owner" if e["kind"] == "self" else e["relation"]
            parts.append(f"{rel}: {e['canonical']}" if rel else e["canonical"])
        return "; ".join(parts)
    except Exception:                                                          # noqa: BLE001
        return ""


# ====================================================================================================================
# 7. Talking about names: "what names do you know", "forget X", the one question
# ====================================================================================================================

_LIST_RX = re.compile(r"(?i)^\W*(?:jarvis[, ]+)?(?:(?:what|which)\s+(?:names|people|persons)\s+do\s+you\s+(?:know|remember)|who\s+(?:all\s+)?do\s+you\s+know|"
                      r"(?:list|show)\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?(?:names|people)(?:\s+you\s+(?:know|remember))?|what\s+do\s+you\s+know\s+about\s+(?:my\s+)?(?:names|people))\W*$")
_FORGET_RX = re.compile(r"(?i)^\W*(?:jarvis[, ]+)?(?:please\s+)?(?:forget|remove|delete|stop\s+remembering|(?:do\s*n['’]?t|do\s+not)\s+remember)\s+(?:about\s+)?(?:the\s+)?"
                        r"(?:name\s+|person\s+)?(?P<q>.{2,40}?)(?:\s+from\s+(?:my\s+|your\s+)?(?:names|people|memory))?\W*$")
_NOT_A_NAME = frozenset("it that this them everything all those these".split())


def intent(text: str, store: NameStore | None = None) -> tuple[str, str] | None:
    """("list", "") for 'what names do you know'; ("forget", name) for 'forget Priya' ONLY when Priya is a known name (so 'forget it' is not this); else None."""
    t = " ".join(str(text or "").split())[:200]
    if _LIST_RX.match(t):
        return ("list", "")
    m = _FORGET_RX.match(t)
    if m:
        q = m.group("q").strip(" '\"“”‘’")
        if q and norm(q) not in _NOT_A_NAME:
            st = store or get_store()
            known = _find(st.entries(), q) or any(_rel_match(e["relation"], re.sub(r"(?i)^(?:my|our)\s+", "", q)) for e in st.entries()) or norm(q) in st.candidates()
            if known:
                return ("forget", re.sub(r"(?i)^(?:my|our)\s+", "", q) if not _find(st.entries(), q) else q)
    return None


def list_text(store: NameStore | None = None) -> str:
    """The owner's own view: who JARVIS knows, grouped, with the other spellings. Shown to the owner only (never handed to a model)."""
    st = store or get_store()
    entries = st.entries()
    if not entries:
        return "I do not know any names yet. I learn them as you tell me: your name, your family, your friends."
    order = {"self": 0, "family": 1, "friend": 2, "other": 3, "place": 4, "app": 5}
    lines = []
    for e in sorted(entries, key=lambda e: (order[e["kind"]], -e["count"], e["canonical"])):
        label = "you" if e["kind"] == "self" else (e["relation"] or e["kind"])
        extra = f" ({', '.join(e['aliases'])})" if e["aliases"] else ""
        guess = " (not sure)" if e["confidence"] < 0.6 and e["source"] != "confirmed" else ""
        lines.append(f"{e['canonical']}{extra}: {label}{guess}")
    cands = [c["name"] for c in st.candidates().values()]
    tail = f" I have seen {', '.join(cands[:5])} but will wait until I am sure." if cands else ""
    return "Names I know: " + "; ".join(lines[:40]) + "." + tail


def pending_question(store: NameStore | None = None, *, mark: bool = True) -> dict | None:
    """ONE question about a low-confidence guess, built as an ask form (ask_form.build_form) with a plain `sentence`, or None. Asked once: the entry is marked
    asked when this returns it (`mark`), so it never comes back, answered or not."""
    st = store or get_store()
    for e in sorted(st.entries(), key=lambda e: (e["last_seen"]), reverse=True):
        if e.get("asked") or e["confidence"] >= 0.7 or e["source"] in ("confirmed", "profile"):
            continue
        name = e["canonical"]
        if e["kind"] == "self":
            q, opts = f"Is {name} your name?", ["Yes", "No, not a name"]
        else:
            q, opts = f"Who is {name} to you?", ["Family", "Friend", "A place", "Not a name"]
        form = None
        af = _sibling("ask_form")
        if af is not None:
            try:
                form = af.build_form(q, opts, reason="I picked this name up from what you said and want to be sure.", source="names", allow_other=False)
            except Exception:                                                  # noqa: BLE001
                form = None
        if mark:
            st.remember_asked(name)
        return {"name": name, "sentence": q + " (" + " / ".join(opts) + ")", "form": form}
    return None


def quick_answer(text: str, store: NameStore | None = None) -> str | None:
    """The reply to 'what names do you know' or 'forget <known name>', or None when the text is neither. A rule, no model: names never reach a model for this."""
    st = store or get_store()
    got = intent(text, st)
    if got is None:
        return None
    if got[0] == "list":
        reply = list_text(st)
        q = pending_question(st)
        return reply + (f" By the way: {q['sentence']}" if q else "")
    gone = st.forget(got[1])
    if gone:
        return f"Okay, I forgot {gone['canonical']}. I will not pick it up again."
    return f"Okay, I will not pick up {got[1]} again."
