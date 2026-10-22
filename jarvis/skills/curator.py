"""Skill curator: retire unused skills, never silently delete them. Pure logic over a small JSON registry.

Lifecycle: staged -> active -> stale -> archived, plus a ``pinned`` flag that exempts a skill from automatic moves.
``plan()`` proposes moves without applying them; ``apply(plan)`` is a dry run unless told otherwise. Real applies
write a timestamped backup of the previous store next to it (newest 10 kept) and replace the store atomically.
Archived skills only come back as ``staged`` (``restore``); becoming active again needs a new owner promotion record.

INVARIANT: no skill is active without a ``promotion_record``; ``load`` rejects such a store and ``promote`` requires one.
Reports, plans and errors carry names, states and reasons only, never skill contents or store contents.
No network, no model calls, standard library only; the clock is injected.
"""
import json
import os
import re
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

STATES = ("staged", "active", "stale", "archived")
SOURCES = ("owner", "agent", "community")
DAY = 86400.0
KEEP_BACKUPS = 10
MAX_HISTORY = 200
_FIELDS = ("name", "state", "pinned", "created", "promoted_at", "last_used", "use_count", "source",
           "content_sha256", "promotion_record")


class CuratorError(Exception):
    """Invalid store, invalid transition or refused operation. Messages never include store contents."""


class Tampered(CuratorError):
    """The skill's content hash changed since it was staged."""


@dataclass(frozen=True)
class Move:
    name: str
    from_state: str
    to_state: str
    reason: str


@dataclass(frozen=True)
class Plan:
    moves: Tuple[Move, ...] = ()
    consolidation_candidates: Tuple[Tuple[str, str, str], ...] = ()    # (name_a, name_b, why); proposals only


@dataclass(frozen=True)
class Report:
    dry_run: bool
    moves: Tuple[Move, ...]
    before: Dict[str, str]
    after: Dict[str, str]
    backup: Optional[str] = None
    consolidation_candidates: Tuple[Tuple[str, str, str], ...] = ()


def _norm(name: str) -> str:
    return re.sub(r"[-_\s]", "", name).lower()


class Curator:
    def __init__(self, store_path: str, clock: Callable[[], float] = time.time, stale_after_days: float = 30,
                 archive_after_days: float = 90, max_active: int = 40,
                 promotion_verifier: Optional[Callable[[str], bool]] = None):
        # promotion_verifier(record_id) -> bool looks the owner's promotion up in the audit log. Without it the curator can only check that a
        # record is present, not that it is real; the integration must pass one (plan P5: "no skill activates without an owner promotion
        # record in the audit log"). With it, promote() refuses unknown records and load() refuses a store whose active skills have none.
        self.promotion_verifier = promotion_verifier
        self.store_path = store_path
        self.clock = clock
        self.stale_after_days = stale_after_days
        self.archive_after_days = archive_after_days
        self.max_active = max_active
        self.skills: Dict[str, dict] = {}
        self._history: Dict[str, List[dict]] = {}
        if os.path.exists(store_path):
            self.load()

    # ---- store ----
    @staticmethod
    def _validate(data) -> Tuple[Dict[str, dict], Dict[str, List[dict]]]:
        if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
            raise CuratorError("store has an invalid structure")
        history = data.get("history", {})
        if not isinstance(history, dict):
            raise CuratorError("store has an invalid history")
        for key, rec in data["skills"].items():
            if not isinstance(rec, dict) or set(rec) != set(_FIELDS) or rec["name"] != key:
                raise CuratorError(f"skill record {key!r} is malformed")
            if rec["state"] not in STATES or rec["source"] not in SOURCES or not isinstance(rec["pinned"], bool):
                raise CuratorError(f"skill record {key!r} has an invalid state, source or flag")
            if rec["state"] == "active" and not rec["promotion_record"]:
                raise CuratorError(f"skill {key!r} is active without a promotion_record")
        return data["skills"], {k: list(v) for k, v in history.items()}

    def load(self) -> None:
        try:
            with open(self.store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            raise CuratorError("store is unreadable or not valid JSON") from None
        skills, history = self._validate(data)
        if self.promotion_verifier is not None:
            for rec in skills.values():
                if rec["state"] == "active" and not self.promotion_verifier(rec["promotion_record"]):
                    raise CuratorError(f"skill {rec['name']!r} is active but its promotion record is not in the audit log")
        self.skills, self._history = skills, history

    def _backups(self) -> List[str]:
        d, base = os.path.split(os.path.abspath(self.store_path))
        return sorted(os.path.join(d, n) for n in os.listdir(d) if n.startswith(base + ".bak-"))

    def _backup(self) -> Optional[str]:
        if not os.path.exists(self.store_path):
            return None
        stamp = f"{int(self.clock() * 1000):015d}"
        path, n = f"{self.store_path}.bak-{stamp}", 0
        while os.path.exists(path):
            n += 1
            path = f"{self.store_path}.bak-{stamp}-{n}"
        with open(self.store_path, "rb") as src, open(path, "wb") as dst:
            dst.write(src.read())
        for old in self._backups()[:-KEEP_BACKUPS]:
            os.remove(old)
        return path

    def _write_bytes(self, payload: bytes) -> None:
        tmp = self.store_path + ".tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(payload)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.store_path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def _save(self) -> Optional[str]:
        payload = json.dumps({"skills": self.skills, "history": self._history}, indent=1, sort_keys=True).encode()
        backup = self._backup()
        self._write_bytes(payload)
        return backup

    # ---- helpers ----
    def _get(self, name: str) -> dict:
        if name not in self.skills:
            raise CuratorError(f"unknown skill {name!r}")
        return self.skills[name]

    def _transition(self, rec: dict, to: str, reason: str, actor: str, now: float) -> None:
        h = self._history.setdefault(rec["name"], [])
        h.append({"time": now, "from": rec["state"], "to": to, "reason": reason, "actor": actor})
        del h[:-MAX_HISTORY]                       # per-skill history is capped so the store cannot grow without bound
        rec["state"] = to

    # ---- operations ----
    def stage(self, name: str, content_sha256: str, source: str = "agent") -> None:
        if name in self.skills:
            raise CuratorError(f"skill {name!r} already exists")
        if source not in SOURCES:
            raise CuratorError(f"invalid source {source!r}")
        now = self.clock()
        self.skills[name] = {"name": name, "state": "staged", "pinned": False, "created": now, "promoted_at": None,
                             "last_used": None, "use_count": 0, "source": source,
                             "content_sha256": content_sha256, "promotion_record": None}
        self._history.setdefault(name, []).append(
            {"time": now, "from": None, "to": "staged", "reason": "staged", "actor": "owner"})
        self._save()

    def promote(self, name: str, promotion_record: str, *, content_sha256: Optional[str] = None,
                accept_new_sha256: Optional[str] = None) -> None:
        """staged -> active. ``content_sha256`` is the hash observed now; a mismatch is Tampered unless the caller
        explicitly accepts that same new hash via ``accept_new_sha256``."""
        rec = self._get(name)
        if rec["state"] != "staged":
            raise CuratorError(f"skill {name!r} is {rec['state']}, only staged skills can be promoted")
        if not promotion_record or not str(promotion_record).strip():
            raise CuratorError("promotion requires a promotion_record")
        if self.promotion_verifier is not None and not self.promotion_verifier(promotion_record):
            raise CuratorError("the promotion record is not in the audit log")
        if content_sha256 is not None and content_sha256 != rec["content_sha256"]:
            if accept_new_sha256 != content_sha256:
                raise Tampered(f"content of {name!r} changed since it was staged")
            rec["content_sha256"] = content_sha256
        now = self.clock()
        self._transition(rec, "active", "promoted", "owner", now)
        rec["promoted_at"] = now
        rec["promotion_record"] = promotion_record
        self._save()

    def record_use(self, name: str) -> None:
        rec = self._get(name)
        if rec["state"] != "active":
            raise CuratorError(f"skill {name!r} is {rec['state']}, only active skills record use")
        rec["last_used"] = self.clock()
        rec["use_count"] += 1
        self._save()

    def restore(self, name: str) -> None:
        rec = self._get(name)
        if rec["state"] != "archived":
            raise CuratorError(f"skill {name!r} is {rec['state']}, only archived skills can be restored")
        rec["promotion_record"] = None
        self._transition(rec, "staged", "restored; re-promotion needs a new promotion_record", "owner", self.clock())
        self._save()

    def pin(self, name: str) -> None:
        self._get(name)["pinned"] = True
        self._save()

    def unpin(self, name: str) -> None:
        self._get(name)["pinned"] = False
        self._save()

    def history(self, name: str) -> List[dict]:
        self._get(name)
        return [dict(h) for h in self._history.get(name, [])]

    # ---- planning ----
    @staticmethod
    def _last_activity(rec: dict) -> float:
        return rec["last_used"] or rec["promoted_at"] or rec["created"]

    def plan(self, now: Optional[float] = None) -> Plan:
        now = self.clock() if now is None else now
        moves: List[Move] = []
        moving = set()
        for rec in sorted(self.skills.values(), key=lambda r: r["name"]):
            if rec["pinned"]:
                continue
            idle = (now - self._last_activity(rec)) / DAY
            if rec["state"] == "active" and idle >= self.stale_after_days:
                moves.append(Move(rec["name"], "active", "stale",
                                  f"unused for {idle:.0f} days (limit {self.stale_after_days})"))
                moving.add(rec["name"])
            elif rec["state"] == "stale" and idle >= self.archive_after_days:
                moves.append(Move(rec["name"], "stale", "archived",
                                  f"unused for {idle:.0f} days (limit {self.archive_after_days})"))
        active = [r for r in self.skills.values() if r["state"] == "active" and r["name"] not in moving]
        excess = len(active) - self.max_active
        if excess > 0:
            candidates = sorted((r for r in active if not r["pinned"]),
                                key=lambda r: (self._last_activity(r), r["name"]))
            for rec in candidates[:excess]:
                moves.append(Move(rec["name"], "active", "stale",
                                  f"active count exceeds max_active={self.max_active}; least recently used"))
        return Plan(tuple(moves), self._consolidation())

    def _consolidation(self) -> Tuple[Tuple[str, str, str], ...]:
        act = sorted((r for r in self.skills.values() if r["state"] == "active"), key=lambda r: r["name"])
        out = []
        for i, a in enumerate(act):
            for b in act[i + 1:]:
                if a["content_sha256"] == b["content_sha256"]:
                    out.append((a["name"], b["name"], "identical content hash"))
                elif _norm(a["name"]) == _norm(b["name"]):
                    out.append((a["name"], b["name"], "names equal ignoring case, dashes and underscores"))
        return tuple(out)

    def apply(self, plan: Plan, *, dry_run: bool = True) -> Report:
        before: Dict[str, str] = {}
        after: Dict[str, str] = {}
        for m in plan.moves:
            rec = self._get(m.name)
            if rec["state"] != m.from_state or rec["pinned"]:
                raise CuratorError(f"plan is out of date for skill {m.name!r}")
            before[m.name] = m.from_state
            after[m.name] = m.to_state
        cands = plan.consolidation_candidates
        if dry_run:
            return Report(True, tuple(plan.moves), before, after, None, cands)
        now = self.clock()
        for m in plan.moves:
            self._transition(self.skills[m.name], m.to_state, m.reason, "curator", now)
        backup = self._save()
        return Report(False, tuple(plan.moves), before, after, backup, cands)

    def rollback_to(self, backup_path: str) -> None:
        try:
            with open(backup_path, "rb") as f:
                payload = f.read()
            data = json.loads(payload.decode("utf-8"))
        except (OSError, ValueError):
            raise CuratorError("backup is unreadable or not valid JSON") from None
        self._validate(data)
        self._backup()
        self._write_bytes(payload)
        self.load()
