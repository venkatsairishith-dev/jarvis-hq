"""File-arrival detection for allow-listed folders (config/watches.json). Names, sizes and times only: never content.

Design (docs/research/05-memory-database-and-automations.md, section 7.7):
- A folder is only watched if this machine's own allowlist names it; the kernel can refer to a watch but never add one.
- A file is reported once it has stopped changing: same size and mtime on two scans at least `settle` seconds apart.
  (Browsers write `.crdownload` then rename; editors write twice. Those are ignored or wait.)
- The scan result is persisted. At start-up the node compares the folder with what it knew when it last ran, so files that
  arrived while the laptop was off or asleep are reported then. The first run only records a baseline (no flood).
- Polling the folder on the node's own heartbeat is deliberate: it needs no extra service, survives sleep and restarts,
  and the reconcile step is the same code as the normal step.
"""
import json
import os
import time


def load_watches(path=None) -> dict:
    path = path or os.path.join(os.path.dirname(__file__), "..", "..", "config", "watches.json")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            return json.load(fh).get("watches", {})
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:                # a broken allowlist must be loud, not "no watches"
        import sys
        print(f"watches.json is not valid ({type(e).__name__}): no folder is watched", file=sys.stderr)
        return {}


def folder_of(spec: dict):
    p = (spec.get("path") or {}).get(os.name)
    return os.path.expandvars(p) if p else None


class Watcher:
    def __init__(self, watches: dict, state_path: str, clock=time.time, settle=2.0):
        self.watches, self.state_path, self.clock, self.settle = watches, state_path, clock, settle
        self.known = {}          # watch -> {rel: [size, mtime_ns]} already reported (or baseline)
        self.pending = {}        # (watch, rel) -> (size, mtime_ns, first_seen_ts)
        self.fresh_state = True
        try:
            with open(state_path, encoding="utf-8") as fh:
                self.known = json.load(fh).get("known", {})
                self.fresh_state = False
        except (OSError, ValueError):
            pass

    def names(self):
        return sorted(self.watches)

    def _list(self, watch: str):
        spec = self.watches[watch]
        root = folder_of(spec)
        if not root or not os.path.isdir(root):
            return None
        ignore = tuple(x.lower() for x in spec.get("ignore", []))
        depth = int(spec.get("depth", 1))
        out = {}
        base = os.path.realpath(root)
        for dp, dn, fn in os.walk(base):
            if dp[len(base):].count(os.sep) >= depth:
                dn[:] = []
            for f in fn:
                if f.startswith((".", "~$")) or f.lower().endswith(ignore):
                    continue
                full = os.path.join(dp, f)
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                out[os.path.relpath(full, base).replace("\\", "/")] = [st.st_size, st.st_mtime_ns]
        return out

    def scan(self) -> dict:
        """{watch: [{rel, size, mtime_ns}]} of files that are new and have settled since the last scan. Call ack()
        with what was delivered; until then the same files are offered again."""
        now, reports = self.clock(), {}
        for w in self.watches:
            cur = self._list(w)
            if cur is None:
                continue
            if w not in self.known:                       # first time this watch is seen: baseline, report nothing
                self.known[w] = cur
                continue
            known = self.known[w]
            for rel, (size, mt) in cur.items():
                if known.get(rel) == [size, mt]:
                    self.pending.pop((w, rel), None)
                    continue
                key = (w, rel)
                prev = self.pending.get(key)
                if prev and prev[0] == size and prev[1] == mt:
                    if now - prev[2] >= self.settle:      # unchanged for long enough: it has arrived
                        reports.setdefault(w, []).append({"rel": rel, "size": size, "mtime_ns": mt})
                else:
                    self.pending[key] = (size, mt, now)   # new, or still changing: start the clock again
            for rel in [r for r in list(known) if r not in cur]:
                known.pop(rel)                            # deleted: forget, so a re-created file is new again
        self.save()
        return reports

    def ack(self, reports: dict):
        """The kernel has the events: only now are these files 'known'. A failed post leaves them pending, so they are
        reported again on the next scan instead of being lost."""
        for w, files in reports.items():
            for f in files:
                self.known.setdefault(w, {})[f["rel"]] = [f["size"], f["mtime_ns"]]
                self.pending.pop((w, f["rel"]), None)
        self.save()

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"known": self.known}, fh)
            os.replace(tmp, self.state_path)
        except OSError:
            pass
