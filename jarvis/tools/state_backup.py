"""Backup and restore of what JARVIS has LEARNED about this owner and this PC (D71), so a bad night is recoverable. Local only, nothing is sent anywhere.

    python jarvis/tools/state_backup.py backup [--keep N] [--max-mb M]   copy the learned state to <backups>\\<timestamp>\\ with a sha256 manifest
    python jarvis/tools/state_backup.py list                             the backups, newest first (what is in each, how big, why it was made)
    python jarvis/tools/state_backup.py verify [ID|latest]               every file against the manifest; exit 1 on any problem
    python jarvis/tools/state_backup.py restore [ID|latest] [--only a,b] [--apply]
                                                                         without --apply: the plan only. With it: verify first, back up the CURRENT state, then replace

What is in a backup (the closed table ITEMS below; nothing else is ever copied):
  names       <data>\\names.json                             the names JARVIS learned (D62)
  lessons     <data>\\plugins\\lessons.json                   what worked in which app (D65, D67)
  playbooks   <data>\\plugins\\site_playbooks\\<host>.<name>.json   learned and repaired site playbooks (D64)
  config      jarvis\\config\\decider.json, specialists.json   the measured decider and specialist settings (D51, D59)
  prefs       <data>\\language.json, plugins\\ask_prefs.json   the owner's reply language and answers kept for forms
  journal     <data>\\journal\\journal-*.jsonl, newest 30      the timeline's step index (labels and results; NEVER the pictures in shots\\)

NEVER in a backup, and refused again if a file is listed anyway (at backup, at verify and at restore): anything under the secrets folder, the kernel's
site grants (trusted_sites.json), identity.json, the auto-click and sensitive-site lists, providers.json, PIN material, tokens, credentials, keys, cookies,
the safe-mode flag, the kernel's database, pictures, and the owner's documents. A file whose text holds a key-shaped string is skipped, not copied.
A restore can only write the files of this table, at the places THIS program computes (never a path read from the backup); it cannot create, widen or
extend a site grant or any permission, and it never touches safe mode. JARVIS never runs this tool; the owner does (or the Task Scheduler for `backup`).

Backups go to D:\\jarvis\\backups (JARVIS_BACKUP_DIR overrides; on Windows the folder must be on D:). Each backup is built in a temp folder and renamed into place,
so a stop in the middle leaves no half backup. The last N (default 14) are kept, plus the last 5 made just before a restore. Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from jarvis.pluginapi.paths import data_dir  # noqa: E402

VERSION = 1
MANIFEST = "manifest.json"
MANIFEST_SUM = "manifest.sha256"
FILES = "files"
ID_RX = re.compile(r"^\d{8}-\d{6}(-\d{1,3})?$")
KEEP_DEFAULT, KEEP_MIN, KEEP_PRE_RESTORE = 14, 3, 5
MAX_MB_DEFAULT = 200
MAX_FILE_BYTES = 20 * 1024 * 1024
JOURNAL_FILES = 30

# Names and places that are never copied, never listed in a manifest and never written by a restore. Deliberately broad: a false alarm costs a missing
# backup of one file, a miss costs a secret or a permission in a backup folder.
FORBIDDEN_NAME = re.compile(
    r"(trusted_sites|identity|auto_click|sensitive_sites|admin_verbs|providers|protected|safe_mode|kernel_secrets|sitegrants|site_grants)"
    r"|(^|[^a-z0-9])(secrets?|credentials?|passw(or)?d|passwd|tokens?|cookies?|pin|seal|otp|keystore|wallet)([^a-z0-9]|$)"
    r"|\.(pem|key|p12|pfx|cred|kdbx|jks|keystore|sqlite3?|db|env)$|(^|[\\/])\.env|login data|web data|id_(rsa|ed25519|ecdsa|dsa)", re.I)
FORBIDDEN_DIR = re.compile(r"^(jarvis-secrets|secrets?|\.ssh|\.gnupg|\.aws|kernel|sites|shots|credentials?)$", re.I)


class BackupError(RuntimeError):
    pass


# ---- where things are ---------------------------------------------------------------------------------------------------------------------

def _is_on_d(path) -> bool:
    from jarvis.supervisor.budget import is_on_d
    return is_on_d(path)


def backups_root(env=None, windows: bool | None = None) -> Path:
    env = os.environ if env is None else env
    windows = (os.name == "nt") if windows is None else windows
    raw = (env.get("JARVIS_BACKUP_DIR") or "").strip()
    root = Path(raw) if raw else data_dir().parent / "backups"
    if windows and not _is_on_d(root):
        raise BackupError(f"backups go on D: only (D50); {root} is not on D:")
    return root


def secrets_roots() -> list[Path]:
    out = [Path(r"D:\jarvis-secrets")]
    for name in ("JARVIS_SECRETS_DIR", "JARVIS_SECRET_REF_ROOTS"):
        for part in (os.environ.get(name) or "").split(os.pathsep):
            if part.strip():
                out.append(Path(part.strip()))
    out.append(Path.home() / ".jarvis" / "secrets.env")
    return out


def forbidden(path) -> str:
    """Why this path may never be in a backup, or '' when nothing forbids it. The file's name, every folder above it, and the secrets roots are looked at."""
    p = Path(str(path))
    if FORBIDDEN_NAME.search(p.name):
        return "its name belongs to a secret, a grant, a permission list or the kernel"
    if any(FORBIDDEN_DIR.match(part) for part in p.parts[:-1]):
        return "it sits in a secrets, kernel or picture folder"
    try:
        rp = p.resolve()
    except (OSError, RuntimeError):
        return "its path cannot be resolved"
    for s in secrets_roots():
        try:
            rp.relative_to(s.resolve())
            return "it is under the secrets folder"
        except (ValueError, OSError):
            continue
    return ""


def _inside(path: Path, folder: Path) -> bool:
    try:
        path.resolve().relative_to(folder.resolve())
        return True
    except (ValueError, OSError):
        return False


# ---- the closed table of what is backed up --------------------------------------------------------------------------------------------------

def _json_obj(raw: bytes) -> dict:
    d = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(d, dict):
        raise ValueError("not a JSON object")
    return d


def _v_names(raw: bytes, rel: str) -> str:
    d = _json_obj(raw)
    return "" if isinstance(d.get("names", []), list) and len(raw) <= 256 * 1024 else "not a names file"


def _v_lessons(raw: bytes, rel: str) -> str:
    d = _json_obj(raw)
    return "" if isinstance(d.get("entries"), dict) and len(raw) <= 64 * 1024 + 4096 else "not a lessons file"


PLAYBOOK_KEYS = {"name", "host", "hosts", "version", "source", "goal", "start", "params", "needs", "formats", "terms_note", "terms_read", "terms_source",
                 "verified_live", "stats", "repairs", "notes", "phases"}
PLAYBOOK_NAME = re.compile(r"^((?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62})\.([a-z0-9][a-z0-9_-]{0,40})\.json$")


def _v_playbook(raw: bytes, rel: str) -> str:
    d = _json_obj(raw)
    m = PLAYBOOK_NAME.match(rel)
    if not m or d.get("host") != m.group(1) or d.get("name") != m.group(2):
        return "the playbook does not match its file name"
    extra = set(d) - PLAYBOOK_KEYS
    if extra:
        return "unknown keys " + ", ".join(sorted(extra)[:4]) + " (a grant, a seal or a permission is not part of a playbook)"
    if not isinstance(d.get("phases"), list) or not d["phases"]:
        return "no phases"
    from jarvis.tools.doctor import SECRET_SHAPE
    if SECRET_SHAPE.search(raw.decode("utf-8", "replace")):
        return "holds a key-shaped string"
    try:                                                      # the engine's own reader, when this checkout has it
        from jarvis.plugins.desktop import browser_playbooks as PB
        PB.Playbook.from_dict(d, "learned")
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        return f"the playbook reader refuses it ({type(e).__name__})"
    return ""


DECIDER_KEYS = {"thresholds", "models", "timeout_s", "rule_sure", "cache_s", "cache_max", "use_model"}


def _v_decider(raw: bytes, rel: str) -> str:
    d = _json_obj(raw)
    bad = [k for k in d if k not in DECIDER_KEYS and not k.startswith("_")]
    return "" if not bad else "unknown keys " + ", ".join(bad[:4])


def _v_specialists(raw: bytes, rel: str) -> str:
    d = _json_obj(raw)
    try:
        from jarvis.plugins._shared import specialists
        specialists.parse(d)
    except ImportError:
        return "" if isinstance(d.get("kinds"), dict) else "no kinds"
    except Exception as e:  # noqa: BLE001
        return f"the specialist table refuses it ({str(e)[:80]})"
    return ""


def _v_language(raw: bytes, rel: str) -> str:
    d = _json_obj(raw)
    return "" if d.get("reply_language") in ("en", "match", "te", "hi") else "not a language setting"


def _v_prefs(raw: bytes, rel: str) -> str:
    _json_obj(raw)
    return "" if len(raw) <= 256 * 1024 else "too large for a preference file"


def _v_journal(raw: bytes, rel: str) -> str:
    for ln in raw.decode("utf-8", "replace").splitlines()[:50]:
        if ln.strip():
            if not isinstance(json.loads(ln), dict):
                return "a line is not an object"
    return ""


def _jarvis_config() -> Path:
    return ROOT / "jarvis" / "config"


def _playbooks_dir() -> Path:
    env = (os.environ.get("JARVIS_PLAYBOOKS_DIR") or "").strip()
    return Path(env) if env else data_dir() / "plugins" / "site_playbooks"


def _names_file() -> Path:
    env = (os.environ.get("JARVIS_NAMES_FILE") or "").strip()
    return Path(env) if env else data_dir() / "names.json"


def _lessons_file() -> Path:
    return data_dir() / "plugins" / "lessons.json"


def _language_file() -> Path:
    env = (os.environ.get("JARVIS_LANGUAGE_FILE") or "").strip()
    return Path(env) if env else data_dir() / "language.json"


@dataclass(frozen=True)
class Item:
    id: str
    what: str
    folder: Callable[[], Path]               # where the files live (and the only place a restore writes them)
    name: re.Pattern                         # which file names in that folder belong to the item (the whole name)
    validate: Callable[[bytes, str], str]
    newest: int = 0                          # keep only the N newest files by name (the journal)
    max_bytes: int = MAX_FILE_BYTES

    def candidates(self) -> list[Path]:
        d = self.folder()
        try:
            names = sorted(n for n in os.listdir(d) if self.name.fullmatch(n))
        except OSError:
            return []
        if self.newest:
            names = names[-self.newest:]
        return [d / n for n in names]

    def target(self, rel: str) -> Path | None:
        """Where a restore writes `rel`, computed from the current settings. None when `rel` is not a name of this item (a path, a parent, another kind of file)."""
        if rel != os.path.basename(rel) or rel in ("", ".", "..") or not self.name.fullmatch(rel):
            return None
        return self.folder() / rel


def items() -> list[Item]:
    names_f, lessons_f, lang_f = _names_file(), _lessons_file(), _language_file()
    return [
        Item("names", "the names JARVIS learned", lambda: _names_file().parent, re.compile(re.escape(names_f.name)), _v_names),
        Item("lessons", "what worked in which app", lambda: _lessons_file().parent, re.compile(re.escape(lessons_f.name)), _v_lessons),
        Item("playbooks", "learned and repaired site playbooks", _playbooks_dir, PLAYBOOK_NAME, _v_playbook),
        Item("decider", "the decision engine's measured settings", _jarvis_config, re.compile(r"decider\.json"), _v_decider),
        Item("specialists", "which local model does which work", _jarvis_config, re.compile(r"specialists\.json"), _v_specialists),
        Item("language", "the owner's reply language", lambda: _language_file().parent, re.compile(re.escape(lang_f.name)), _v_language),
        Item("ask_prefs", "answers kept for forms", lambda: data_dir() / "plugins", re.compile(r"ask_prefs\.json"), _v_prefs),
        Item("journal", "the timeline's step index (no pictures)", lambda: data_dir() / "journal", re.compile(r"journal-\d{4}-\d{2}-\d{2}(\.\d+)?\.jsonl"),
             _v_journal, newest=JOURNAL_FILES, max_bytes=9 * 1024 * 1024),
    ]


ITEM_IDS = ("names", "lessons", "playbooks", "decider", "specialists", "language", "ask_prefs", "journal")


# ---- manifest ----------------------------------------------------------------------------------------------------------------------------

def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "wb") as fh:
        fh.write(raw)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _manifest_bytes(m: dict) -> bytes:
    return (json.dumps(m, indent=1, sort_keys=True, ensure_ascii=True) + "\n").encode("ascii")


def _stamp(now: float) -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now))


def _new_id(root: Path, now: float) -> str:
    base, n = _stamp(now), 0
    while (root / (base + (f"-{n}" if n else ""))).exists():
        n += 1
    return base + (f"-{n}" if n else "")


# ---- backup ------------------------------------------------------------------------------------------------------------------------------

def make_backup(root: Path | None = None, *, keep: int = KEEP_DEFAULT, max_mb: int = MAX_MB_DEFAULT, reason: str = "backup", table: list[Item] | None = None,
                now: Callable[[], float] = time.time, windows: bool | None = None, free_check: bool = True, check_content: bool = True, protect: tuple = ()) -> dict:
    """Copy the learned state. Returns the manifest (plus "id" and "path"). Raises BackupError when nothing safe can be written.
    `check_content=False` (the copy made just before a restore) keeps a file even if its JSON is damaged: it is about to be replaced and the owner may want it back."""
    root = Path(root) if root else backups_root(windows=windows)
    if (os.name == "nt" if windows is None else windows) and not _is_on_d(root):
        raise BackupError(f"backups go on D: only (D50); {root} is not on D:")
    t = now()
    table = table if table is not None else items()
    from jarvis.tools.doctor import SECRET_SHAPE
    entries, skipped, blobs = [], [], []
    total = 0
    cap = max_mb * 1024 * 1024
    for it in table:
        allowed_folder = it.folder()
        for src in it.candidates():
            rel = src.name
            why = forbidden(src)
            if why:
                skipped.append({"item": it.id, "file": rel, "reason": "refused: " + why})
                continue
            if src.is_symlink() or not src.is_file():
                skipped.append({"item": it.id, "file": rel, "reason": "not a plain file"})
                continue
            if not _inside(src, allowed_folder):
                skipped.append({"item": it.id, "file": rel, "reason": "refused: it resolves to a place outside the item's folder"})
                continue
            try:
                size = src.stat().st_size
                if size > it.max_bytes:
                    skipped.append({"item": it.id, "file": rel, "reason": f"too large ({size // 1024} KB)"})
                    continue
                raw = src.read_bytes()
            except OSError as e:
                skipped.append({"item": it.id, "file": rel, "reason": f"unreadable ({type(e).__name__})"})
                continue
            if SECRET_SHAPE.search(raw.decode("utf-8", "replace")):
                skipped.append({"item": it.id, "file": rel, "reason": "refused: holds a key-shaped string (value not shown)"})
                continue
            try:
                bad = it.validate(raw, rel) if check_content else ""
            except (ValueError, UnicodeDecodeError) as e:
                bad = f"not valid ({type(e).__name__})"
            if bad:
                skipped.append({"item": it.id, "file": rel, "reason": "not saved: " + bad})
                continue
            entries.append({"item": it.id, "file": rel, "sha256": sha256_bytes(raw), "bytes": len(raw)})
            blobs.append((it.id, rel, raw))
            total += len(raw)
    # over the cap: the oldest journal files go first (the index is the only part that can shrink); anything else over the cap is an error
    while total > cap:
        j = [i for i, e in enumerate(entries) if e["item"] == "journal"]
        if not j:
            raise BackupError(f"the learned state is {total // 1024 // 1024} MB, over the {max_mb} MB cap")
        i = min(j, key=lambda k: entries[k]["file"])
        total -= entries[i]["bytes"]
        skipped.append({"item": "journal", "file": entries[i]["file"], "reason": "dropped to stay under the size cap"})
        del entries[i], blobs[i]
    if not entries:
        raise BackupError("nothing to back up: no learned state was found" + (f" ({len(skipped)} file(s) were refused or skipped)" if skipped else ""))
    root.mkdir(parents=True, exist_ok=True)
    if free_check:
        free = shutil.disk_usage(root).free
        if free < 2 * total + 10 * 1024 * 1024:
            raise BackupError(f"not enough free space on the backup drive ({free // 1024 // 1024} MB free, {total // 1024 // 1024} MB needed twice over)")
    bid = _new_id(root, t)
    tmp = root / f".tmp-{bid}-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        for item_id, rel, raw in blobs:
            _atomic_write(tmp / FILES / item_id / rel, raw)
        manifest = {"version": VERSION, "tool": "state_backup", "id": bid, "created": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)), "created_epoch": int(t),
                    "reason": reason, "files": sorted(entries, key=lambda e: (e["item"], e["file"])), "skipped": skipped, "total_bytes": total,
                    "never_included": ["secrets folder", "trusted_sites.json (site grants)", "identity.json", "auto_click.json", "PIN material", "tokens and credentials",
                                       "the safe-mode flag", "pictures (journal shots)", "the owner's documents"]}
        mb = _manifest_bytes({k: v for k, v in manifest.items() if k not in ("id",)})
        _atomic_write(tmp / MANIFEST, mb)
        _atomic_write(tmp / MANIFEST_SUM, (sha256_bytes(mb) + "  " + MANIFEST + "\n").encode("ascii"))
        os.replace(tmp, root / bid)                           # the one step that makes the backup exist
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    manifest["path"] = str(root / bid)
    prune(root, keep, protect)
    return manifest


# ---- list, verify -------------------------------------------------------------------------------------------------------------------------

def read_manifest(folder: Path) -> dict:
    try:
        d = json.loads((folder / MANIFEST).read_text(encoding="ascii"))
    except (OSError, ValueError) as e:
        raise BackupError(f"{folder.name}: manifest unreadable ({type(e).__name__})") from None
    if not isinstance(d, dict) or d.get("version") != VERSION or not isinstance(d.get("files"), list):
        raise BackupError(f"{folder.name}: not a manifest this tool wrote")
    d["id"] = folder.name
    return d


def list_backups(root: Path | None = None) -> list[dict]:
    root = Path(root) if root else backups_root()
    out = []
    try:
        names = sorted((n for n in os.listdir(root) if ID_RX.match(n) and (root / n / MANIFEST).is_file()), reverse=True)
    except OSError:
        return []
    for n in names:
        try:
            m = read_manifest(root / n)
            out.append({"id": n, "created": m.get("created", ""), "reason": m.get("reason", ""), "files": len(m["files"]), "bytes": int(m.get("total_bytes", 0)),
                        "skipped": len(m.get("skipped", []))})
        except BackupError as e:
            out.append({"id": n, "created": "", "reason": "UNREADABLE", "files": 0, "bytes": 0, "skipped": 0, "problem": str(e)})
    return out


def resolve(root: Path, ident: str | None) -> Path:
    items_ = list_backups(root)
    if not items_:
        raise BackupError(f"no backups in {root}")
    if not ident or ident == "latest":
        pick = items_[0]["id"]
    elif ident in {i["id"] for i in items_}:
        pick = ident
    else:
        raise BackupError(f"no backup called {ident!r}; `list` shows them")
    return Path(root) / pick


def verify_backup(folder: Path, table: list[Item] | None = None) -> tuple[dict | None, list[str]]:
    """(manifest, problems). Problems are plain sentences naming files, never contents. Empty list = the backup is whole and every file is allowed."""
    problems: list[str] = []
    folder = Path(folder)
    try:
        m = read_manifest(folder)
        raw = (folder / MANIFEST).read_bytes()
    except BackupError as e:
        return None, [str(e)]
    try:
        want = (folder / MANIFEST_SUM).read_text(encoding="ascii").split()[0]
    except (OSError, IndexError):
        want = ""
    if want != sha256_bytes(raw):
        problems.append("the manifest does not match its checksum file (changed or damaged)")
    by_id = {it.id: it for it in (table if table is not None else items())}
    listed: set[str] = set()
    total = 0
    for e in m["files"]:
        if not isinstance(e, dict) or not all(isinstance(e.get(k), str) for k in ("item", "file", "sha256")) or not isinstance(e.get("bytes"), int):
            problems.append("a manifest entry is malformed")
            continue
        item, rel = e["item"], e["file"]
        label = f"{item}/{rel}"
        it = by_id.get(item)
        if it is None:
            problems.append(f"{label}: not a kind of file a backup may hold")
            continue
        if it.target(rel) is None:
            problems.append(f"{label}: not an allowed file name for {item}")
            continue
        why = forbidden(Path(item) / rel)
        if why:
            problems.append(f"{label}: refused, {why}")
            continue
        src = folder / FILES / item / rel
        listed.add(f"{item}/{rel}")
        if src.is_symlink() or not src.is_file():
            problems.append(f"{label}: missing from the backup")
            continue
        data = src.read_bytes()
        if len(data) != e["bytes"] or sha256_bytes(data) != e["sha256"]:
            problems.append(f"{label}: changed since the backup was made (checksum differs)")
            continue
        total += len(data)
    present = set()
    base = folder / FILES
    for dp, _dn, fn in os.walk(base):
        for f in fn:
            present.add(Path(dp, f).relative_to(base).as_posix())
    for extra in sorted(present - listed):
        problems.append(f"{extra}: in the folder but not in the manifest")
    if not problems and total != int(m.get("total_bytes", -1)):
        problems.append("the manifest's total size is wrong")
    return m, problems


# ---- restore -----------------------------------------------------------------------------------------------------------------------------

def plan_restore(folder: Path, only: list[str] | None = None, table: list[Item] | None = None) -> tuple[dict, list[dict]]:
    """Verify the backup and work out what a restore would do. Raises BackupError on any verify problem: a damaged or tampered backup is never applied."""
    table = table if table is not None else items()
    m, problems = verify_backup(folder, table)
    if problems:
        raise BackupError("the backup does not verify, nothing was changed: " + "; ".join(problems[:5]) + (" ..." if len(problems) > 5 else ""))
    by_id = {it.id: it for it in table}
    if only:
        unknown = [o for o in only if o not in by_id]
        if unknown:
            raise BackupError("unknown part: " + ", ".join(unknown) + " (one of " + ", ".join(ITEM_IDS) + ")")
    steps = []
    for e in m["files"]:
        if only and e["item"] not in only:
            continue
        it = by_id[e["item"]]
        tgt = it.target(e["file"])
        if tgt is None or forbidden(tgt):
            raise BackupError(f"{e['item']}/{e['file']}: the place it would be written is not allowed")
        try:
            same = tgt.is_file() and sha256_bytes(tgt.read_bytes()) == e["sha256"]
        except OSError:
            same = False
        steps.append({"item": e["item"], "file": e["file"], "target": str(tgt), "action": "same" if same else ("replace" if tgt.exists() else "create"),
                      "sha256": e["sha256"], "bytes": e["bytes"]})
    return m, steps


def restore(root: Path | None, ident: str | None, *, only: list[str] | None = None, apply: bool = False, keep: int = KEEP_DEFAULT, table: list[Item] | None = None,
            now: Callable[[], float] = time.time, windows: bool | None = None, free_check: bool = True) -> dict:
    root = Path(root) if root else backups_root(windows=windows)
    folder = resolve(root, ident)
    table = table if table is not None else items()
    m, steps = plan_restore(folder, only, table)
    out = {"id": folder.name, "applied": False, "steps": steps, "safety_backup": ""}
    if not apply:
        return out
    todo = [s for s in steps if s["action"] != "same"]
    if not todo:
        return out
    by_id = {it.id: it for it in table}
    # 1. the current state is backed up first, and every file about to be replaced must be in that copy; if not, the restore does not start
    existing = [s for s in todo if Path(s["target"]).exists()]
    if existing:
        try:
            safety = make_backup(root, keep=keep, reason=f"pre-restore of {folder.name}", table=table, now=now, windows=windows, free_check=free_check, check_content=False, protect=(folder.name,))
        except BackupError as e:
            raise BackupError("the current state could not be backed up first, so nothing was changed: " + str(e)) from None
        saved = {(e["item"], e["file"]) for e in safety["files"]}
        lost = [f"{s['item']}/{s['file']}" for s in existing if (s["item"], s["file"]) not in saved]
        if lost:
            shutil.rmtree(Path(safety["path"]), ignore_errors=True)
            raise BackupError("these files could not be backed up first (see `backup` for why), so nothing was changed: " + ", ".join(lost[:5]))
        out["safety_backup"] = safety["id"]
    # 2. read and validate every file BEFORE the first write, so a bad one stops the restore while nothing has changed
    staged = []
    for s in todo:
        src = folder / FILES / s["item"] / s["file"]
        raw = src.read_bytes()
        if sha256_bytes(raw) != s["sha256"]:
            raise BackupError(f"{s['item']}/{s['file']}: changed while restoring, nothing was changed")
        try:
            bad = by_id[s["item"]].validate(raw, s["file"])
        except (ValueError, UnicodeDecodeError) as e:
            bad = f"not valid ({type(e).__name__})"
        if bad:
            raise BackupError(f"{s['item']}/{s['file']}: {bad}; nothing was changed")
        staged.append((Path(s["target"]), raw))
    # 3. replace, file by file, each atomic
    for tgt, raw in staged:
        _atomic_write(tgt, raw)
    out["applied"] = True
    return out


# ---- prune -------------------------------------------------------------------------------------------------------------------------------

def prune(root: Path, keep: int = KEEP_DEFAULT, protect: tuple = ()) -> list[str]:
    """Remove the oldest backups beyond `keep` (and beyond 5 of the kind made just before a restore). Only folders this tool made are touched."""
    keep = max(KEEP_MIN, int(keep))
    removed = []
    regular, pre = [], []
    for b in list_backups(root):
        (pre if str(b.get("reason", "")).startswith("pre-restore") else regular).append(b["id"])
    for ids, limit in ((regular, keep), (pre, KEEP_PRE_RESTORE)):
        for old in [i for i in sorted(ids, reverse=True)[limit:] if i not in protect]:
            shutil.rmtree(Path(root) / old, ignore_errors=True)
            removed.append(old)
    return removed


# ---- command line --------------------------------------------------------------------------------------------------------------------------

def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.2f} MB"


def main(argv=None, out=print) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--keep", type=int, default=KEEP_DEFAULT)
    b.add_argument("--max-mb", type=int, default=MAX_MB_DEFAULT)
    sub.add_parser("list")
    v = sub.add_parser("verify")
    v.add_argument("id", nargs="?", default="latest")
    r = sub.add_parser("restore")
    r.add_argument("id", nargs="?", default="latest")
    r.add_argument("--only", default="", help="comma separated: " + ", ".join(ITEM_IDS))
    r.add_argument("--apply", action="store_true", help="really replace the files (without it only the plan is shown)")
    args = ap.parse_args(argv)
    try:
        root = backups_root()
        if args.cmd == "backup":
            m = make_backup(root, keep=args.keep, max_mb=args.max_mb)
            out(f"backup {m['id']}: {len(m['files'])} file(s), {_mb(m['total_bytes'])} -> {m['path']}")
            for s in m["skipped"]:
                out(f"  skipped {s['item']}/{s['file']}: {s['reason']}")
            return 0
        if args.cmd == "list":
            rows = list_backups(root)
            for r_ in rows:
                out(f"{r_['id']}  {r_['created']}  {r_['files']:>3} file(s)  {_mb(r_['bytes']):>9}  {r_['reason']}" + (f"  [{r_['skipped']} skipped]" if r_["skipped"] else ""))
            if not rows:
                out(f"no backups in {root}")
            return 0
        if args.cmd == "verify":
            folder = resolve(root, args.id)
            m, problems = verify_backup(folder)
            for p in problems:
                out("PROBLEM: " + p)
            out(f"{folder.name}: " + ("every file matches its checksum" if not problems else f"{len(problems)} problem(s)"))
            return 1 if problems else 0
        only = [x for x in args.only.split(",") if x]
        res = restore(root, args.id, only=only, apply=args.apply)
        for s in res["steps"]:
            out(f"  {s['action']:<8} {s['item']}/{s['file']}")
        if res["applied"]:
            out(f"restored from {res['id']}" + (f"; the state before it is backup {res['safety_backup']}" if res["safety_backup"] else ""))
        else:
            out("nothing was changed" + ("" if args.apply else " (this was the plan; add --apply to restore)"))
        return 0
    except BackupError as e:
        out("refused: " + str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
