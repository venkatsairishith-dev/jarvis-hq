"""N: reliability under adverse conditions (concurrency, crashes, corrupt files, disk full, clock jumps, bad inputs), against the
real kernel store, audit chain, owner plane, node endpoints, MCP result handling and the node's job runner. Stores live in temp
folders; child processes and servers are always stopped."""
import contextlib
import errno
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

from jarvis_kernel import mcpwire as W
from jarvis_kernel.policy import Action
from jarvis_kernel.store import Kernel, Killed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
KPATH = os.path.join(ROOT, "jarvis", "kernel")
PIN = "synthetic-pin-4471"


@contextlib.contextmanager
def kernel():
    with tempfile.TemporaryDirectory() as d:
        k = Kernel(os.path.join(d, "kdata"))
        try:
            yield k
        finally:
            k.db.close()


def _owner(k, clock=time.time, pin=True):
    from jarvis_kernel.jobs import Jobs
    from jarvis_kernel.owner import Owner
    from jarvis_kernel.tasks import Tasks
    o = Owner(k, Tasks(k), Jobs(k), clock=clock)
    if pin:
        o.set_pin(PIN)
    return o


def _threads(n, fn):
    barrier = threading.Barrier(n)
    out, errs = [None] * n, []

    def run(i):
        barrier.wait()
        try:
            out[i] = fn(i)
        except Exception as e:  # noqa: BLE001
            errs.append(f"{type(e).__name__}: {e}")
    th = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    [t.start() for t in th]
    [t.join(60) for t in th]
    return out, errs


def _child(code, data_dir, wait=True, timeout=60):
    """Run a Python snippet in a child process with the kernel on its path; `K` is a Kernel on data_dir."""
    src = f"import sys,os,time,json\nsys.path.insert(0,{KPATH!r})\nfrom jarvis_kernel.store import Kernel\nK=Kernel({data_dir!r})\n" + code
    p = subprocess.Popen([sys.executable, "-c", src], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if wait:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out, err
    return p


# ---------------------------------------------------------------- concurrency
def one_approval_many_threads(a):
    n = int(a.get("threads", 16))
    with kernel() as k:
        act = Action("fs", "write", "notes/x.md", {"text": "once"})
        _, aid = k.request(act)
        k.approve(aid)
        out, errs = _threads(n, lambda i: k.authorize(act, aid).allowed)
        ok_chain = k.audit.verify()[0]
    return out.count(True) == 1 and not errs and ok_chain, f"{n} threads: allowed {out.count(True)}, errors {len(errs)}, chain ok={ok_chain}"


def approve_deny_race(a):
    trials, bad = int(a.get("trials", 20)), []
    with kernel() as k:
        act = Action("fs", "write", "notes/x.md")
        for t in range(trials):
            _, aid = k.request(act)
            out, errs = _threads(2, lambda i: k.approve(aid) if i == 0 else k.deny(aid))
            state = k.approval_info(aid)["state"]
            ran = k.authorize(act, aid).allowed
            if errs or out.count(True) != 1 or (out[0] and state != "approved" and not ran) or (out[1] and (state != "denied" or ran)):
                bad.append(f"trial {t}: approve={out[0]} deny={out[1]} state={state} ran={ran} errs={errs[:1]}")
        ok_chain = k.audit.verify()[0]
    return not bad and ok_chain, (f"{trials} races: exactly one decision won each time; denied never ran" if not bad else "; ".join(bad)[:400])


def duplicate_decisions(a):
    with kernel() as k:
        act = Action("fs", "write", "notes/x.md")
        _, a1 = k.request(act)
        r = [k.approve(a1), k.approve(a1), k.deny(a1)]
        _, a2 = k.request(Action("fs", "write", "notes/y.md"))
        r += [k.deny(a2), k.deny(a2), k.approve(a2)]
        s1, s2 = k.approval_info(a1)["state"], k.approval_info(a2)["state"]
        kinds = [x[0] for x in k.db.execute("SELECT kind FROM audit").fetchall()]
    want = [True, False, False, True, False, False]
    return (r == want and s1 == "approved" and s2 == "denied" and kinds.count("approve_refused") == 2 and kinds.count("deny_refused") == 2,
            f"results={r}; states={s1}/{s2}; refused events audited={kinds.count('approve_refused')}+{kinds.count('deny_refused')}")


def concurrent_requests(a):
    n, per = 20, 5
    with kernel() as k:
        def work(i):
            return [k.request(Action("fs", "write", f"notes/{i}-{j}.md"))[1] for j in range(per)]
        out, errs = _threads(n, work)
        ids = [x for lst in out if lst for x in lst]
        pending = k.db.execute("SELECT COUNT(*) FROM approvals WHERE state='pending'").fetchone()[0]
        ok, msg = k.audit.verify()
        reqs = k.db.execute("SELECT COUNT(*) FROM audit WHERE kind='request'").fetchone()[0]
    return (not errs and len(set(ids)) == n * per and pending == n * per and ok and reqs == n * per,
            f"ids={len(set(ids))}/{n * per}, pending={pending}, request rows={reqs}, chain {msg}, errors={errs[:1]}")


def concurrent_pairing(a):
    from jarvis_kernel.nodes import Nodes
    trials, multi = int(a.get("trials", 10)), []
    with kernel() as k:
        n = Nodes(k)
        for t in range(trials):
            code = n.new_code()
            out, errs = _threads(10, lambda i: n.pair(code, f"node{t}-{i}"))
            toks = [x for x in out if x]
            if len(toks) != 1 or errs:
                multi.append(len(toks))
        nodes = k.db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    return not multi, f"{trials} races of 10 pairings with one code: tokens issued per race (bad ones)={multi or 'always 1'}; nodes={nodes}"


def concurrent_wrong_pins(a):
    from jarvis_kernel.owner import OwnerError, PIN_TRIES
    with kernel() as k:
        o = _owner(k)
        _, aid = k.request(Action("fs", "write", "notes/x.md"))
        codes = []

        def guess(i):
            try:
                o.approve(aid, aid, f"guess-{i:04d}")
            except OwnerError as e:
                codes.append(e.code)
        _, errs = _threads(30, guess)
        checked = sum(1 for kd, in k.db.execute("SELECT kind FROM audit").fetchall() if kd == "owner_approve_bad_pin")
    return checked <= PIN_TRIES and not errs, f"30 simultaneous wrong PINs: checked {checked} (limit {PIN_TRIES}), 429s {codes.count(429)}"


def audit_two_connections(a):
    with tempfile.TemporaryDirectory() as d:
        k1, k2 = Kernel(os.path.join(d, "k")), Kernel(os.path.join(d, "k"))
        try:
            def work(i):
                k = k1 if i % 2 else k2
                for j in range(25):
                    k.audit.append("w", {"t": i, "j": j})
            _, errs = _threads(8, work)
            ok, msg = k1.audit.verify()
            n = k1.audit.anchor()["seq"]
        finally:
            k1.db.close()
            k2.db.close()
    return ok and n == 200 and not errs, f"2 connections x 8 threads x 25: {msg}, head seq {n}, errors {errs[:1]}"


def audit_many_processes(a):
    procs, per = int(a.get("procs", 4)), 60
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        Kernel(dd).db.close()
        ps = [_child(f"for j in range({per}): K.audit.append('p', {{'pid': os.getpid(), 'j': j}})\nprint('ok')", dd, wait=False) for _ in range(procs)]
        outs = [p.communicate(timeout=120) + (p.returncode,) for p in ps]
        k = Kernel(dd)
        try:
            ok, msg = k.audit.verify()
            n = k.audit.anchor()["seq"]
        finally:
            k.db.close()
    rc = [o[2] for o in outs]
    return ok and n == procs * per and rc == [0] * procs, f"{procs} processes x {per}: {msg}; exit codes {rc}; {outs[0][1][-200:] if rc[0] else ''}"


def crash_mid_append(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        Kernel(dd).db.close()
        p = _child("i=0\nwhile True:\n    K.audit.append('loop', {'i': i, 'pad': 'x'*500}); i+=1\n", dd, wait=False)
        time.sleep(1.5)
        p.send_signal(signal.SIGKILL)
        p.wait(10)
        k = Kernel(dd)
        try:
            ok, msg = k.audit.verify()
            n0 = k.audit.anchor()["seq"]
            k.audit.append("after", {})
            ok2 = k.audit.verify()[0]
        finally:
            k.db.close()
    return ok and ok2 and n0 > 0, f"killed after {n0} appends; chain after crash: {msg}; next append ok={ok2}"


def crash_inside_tx(a):
    """A process dies after writing an approval and its audit row inside one transaction, before commit: neither survives."""
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        k0 = Kernel(dd)
        k0.audit.append("before", {})
        k0.db.close()
        rc, out, err = _child("with K.db.tx():\n    K.db.execute(\"INSERT INTO approvals(id,action_hash,level,expires,state,action) VALUES ('ghost','h',2,1e12,'approved','{}')\")\n"
                              "    K.audit.append('ghost', {})\n    os._exit(9)\n", dd)
        k = Kernel(dd)
        try:
            ghost = k.db.execute("SELECT COUNT(*) FROM approvals WHERE id='ghost'").fetchone()[0]
            kinds = [r[0] for r in k.db.execute("SELECT kind FROM audit").fetchall()]
            ok = k.audit.verify()[0]
        finally:
            k.db.close()
    return rc == 9 and ghost == 0 and "ghost" not in kinds and ok, f"child exit {rc}; ghost approval rows={ghost}; ghost audit row={'ghost' in kinds}; chain ok={ok}"


# ---------------------------------------------------------------- corrupt files
def corrupt_db(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        os.makedirs(dd)
        junk = os.urandom(8192)
        with open(os.path.join(dd, "state.db"), "wb") as fh:
            fh.write(b"not a database " + junk)
        try:
            k = Kernel(dd)
            k.db.close()
            return False, "a kernel opened on a corrupt state.db and carried on"
        except sqlite3.DatabaseError as e:
            same = open(os.path.join(dd, "state.db"), "rb").read() == b"not a database " + junk
            return same, f"refused to start ({type(e).__name__}: {e}); file left untouched for recovery={same}"


def truncated_db(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        k = Kernel(dd)
        for i in range(400):
            k.audit.append("e", {"i": i, "pad": "y" * 300})
        anchor = k.audit.anchor()
        k.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        k.db.close()
        path = os.path.join(dd, "state.db")
        size = os.path.getsize(path)
        with open(path, "r+b") as fh:
            fh.truncate(size // 2)
        try:
            k2 = Kernel(dd)
            try:
                ok, msg = k2.audit.verify(anchor)
            finally:
                k2.db.close()
        except sqlite3.DatabaseError as e:
            return True, f"truncated file refused: {type(e).__name__}: {e}"
    return not ok, f"verify with anchor on a half file: ok={ok} ({msg})"


def corrupt_node_config(a):
    from jarvis_node import connectors as NC
    started = []

    def popen(*x, **kw):
        started.append(x)
        raise OSError("must not start")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "connectors.local.json")
        with open(p, "w") as fh:
            fh.write('{"connectors": {"notes": {"command": ["python", "srv.py"], ')     # cut off mid-write
        cfg = NC.load_config(p)
        r = NC.handle({"connector": "notes", "op": "connect"}, cfg=cfg, popen=popen)
    return cfg == {} and r.get("error_class") == "not_configured" and not started, f"config read as {cfg}; job -> {r.get('error_class')}; processes={len(started)}"


def corrupt_projects(a):
    from jarvis_node import jobs as NJ
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "projects.json")
        with open(p, "w") as fh:
            fh.write('{"projects": {"neko": {"path": ')
        orig = NJ.load_projects
        NJ.load_projects = lambda path=None: orig(p)
        try:
            r = NJ.run_job({"id": "j1", "kind": "project_check", "params": {"project": "neko"}})
        finally:
            NJ.load_projects = orig
    return r.get("ok") is False and r.get("error"), f"run_job -> {r}"


def corrupt_rows_owner(a):
    """Damaged JSON in job, audit and approval rows: the owner plane keeps answering (the bad row degrades, others still show)."""
    with kernel() as k:
        o = _owner(k)
        o.jobs.create("laptop", "project_check", {"project": "neko"})
        jid = k.db.execute("SELECT id FROM jobs").fetchone()[0]
        k.db.execute("UPDATE jobs SET result='{broken', params='[not json' WHERE id=?", (jid,))
        k.db.execute("INSERT INTO audit(seq, ts, kind, payload, prev, hash) VALUES (?,?,?,?,?,?)", (k.audit.anchor()["seq"] + 1, time.time(), "x", "{oops", "p", "h"))
        _, good = k.request(Action("fs", "write", "notes/ok.md"))
        _, bad = k.request(Action("fs", "write", "notes/bad.md"))
        k.db.execute("UPDATE approvals SET action='{half' WHERE id=?", (bad,))
        c1, j = o.handle("GET", f"/v1/jarvis/owner/job/{jid}", {})
        c2, ev = o.handle("GET", "/v1/jarvis/owner/events?since=0", {})
        c3, st = o.handle("GET", "/v1/jarvis/owner/state", {})
        ids = [x["id"] for x in st.get("approvals", [])] if c3 == 200 else []
        c4, ver = o.handle("GET", "/v1/jarvis/owner/audit/verify", {})
    return (c1 == 200 and j.get("result") is None and c2 == 200 and c3 == 200 and good in ids,
            f"job {c1} (result {j.get('result') if c1 == 200 else j}); events {c2}; state {c3} {'' if c3 == 200 else st}; good approval listed={good in ids}")


# ---------------------------------------------------------------- disk full and OS errors
def disk_full_sqlite(a):
    with kernel() as k:
        act = Action("fs", "write", "notes/x.md")
        _, aid = k.request(act)
        k.approve(aid)
        pages = k.db.execute("PRAGMA page_count").fetchone()[0]
        k.db.execute(f"PRAGMA max_page_count={pages}")
        allowed, err = None, None
        filled = False
        for size in (2000, 200, 20, 0):            # fill every page, down to the smallest row
            try:
                for i in range(2000):
                    k.audit.append("fill", {"pad": "z" * size})
            except sqlite3.OperationalError:
                filled = True
        if not filled:
            return False, "the database never filled up"
        try:
            allowed = k.authorize(act, aid).allowed
        except sqlite3.OperationalError as e:
            err = str(e)
        ok_chain = k.audit.verify()[0]
        k.db.execute("PRAGMA max_page_count=1073741823")
        after = k.authorize(Action("fs", "read", "docs/a.md")).allowed
        ok_chain2 = k.audit.verify()[0]
    return (allowed is None and err and ok_chain and after and ok_chain2,
            f"authorize on a full disk: allowed={allowed}, error={err}; chain ok={ok_chain}; after space freed GREEN allowed={after}, chain ok={ok_chain2}")


def scrub_no_tempdir(a):
    from jarvis_kernel import scrub as S
    tok = "ghp_" + "K" * 36
    old = os.environ.get("TMPDIR")
    import tempfile as tf
    saved = tf.tempdir
    try:
        os.environ["TMPDIR"] = "/nonexistent/full-disk"
        tf.tempdir = "/nonexistent/full-disk"
        clean, findings, blocked = S.scrub(f"token {tok}", deep=True)
    finally:
        tf.tempdir = saved
        if old is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = old
    return tok not in clean and "github_token" in findings, f"deep scrub with no usable temp dir: redacted={tok not in clean}, findings={findings}"


def node_output_enospc(a):
    from jarvis_node import jobs as NJ
    with tempfile.TemporaryDirectory() as root:
        os.environ["JARVIS_OUTPUTS"] = root

        def full(*x, **kw):
            raise OSError(errno.ENOSPC, "No space left on device")
        NJ.open = full
        try:
            r = NJ.run_job({"id": "j1", "kind": "output_save", "params": {"agent": "hermes", "name": "report.md", "content": "hello"}})
        finally:
            del NJ.open
            os.environ.pop("JARVIS_OUTPUTS", None)
        left = [f for _, _, fs in os.walk(root) for f in fs]
    return r.get("ok") is False and r.get("error") == "OSError" and not left, f"result={r}; files left={left}"


# ---------------------------------------------------------------- kill switch under stress
def kill_resume_cycles(a):
    n, bad = int(a.get("cycles", 50)), []
    act = Action("fs", "read", "docs/a.md")
    with kernel() as k:
        for i in range(n):
            k.kill(f"cycle {i}")
            try:
                k.authorize(act)
                bad.append(f"ran while killed at {i}")
            except Killed:
                pass
            k.resume()
            if not k.authorize(act).allowed:
                bad.append(f"refused after resume at {i}")
        kinds = [r[0] for r in k.db.execute("SELECT kind FROM audit").fetchall()]
        ok = k.audit.verify()[0]
    return (not bad and kinds.count("kill") == n and kinds.count("resume") == n and ok and not k.killed(),
            f"{n} cycles: problems={bad[:3] or 'none'}; kill rows {kinds.count('kill')}, resume rows {kinds.count('resume')}, chain ok={ok}")


def kill_during_load(a):
    act = Action("fs", "read", "docs/a.md")
    late = []
    with kernel() as k:
        stop = threading.Event()
        killed_at = [None]

        def worker(i):
            n = 0
            while not stop.is_set():
                t0 = time.time()
                try:
                    if k.authorize(act).allowed and killed_at[0] and t0 > killed_at[0]:
                        late.append(t0 - killed_at[0])
                    n += 1
                except Killed:
                    pass
            return n
        th = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        [t.start() for t in th]
        time.sleep(0.5)
        k.kill("under load")
        killed_at[0] = time.time()
        time.sleep(0.5)
        stop.set()
        [t.join(10) for t in th]
        ok = k.audit.verify()[0]
    return not late and ok, f"actions that began after kill() returned and still ran: {len(late)}; chain ok={ok}"


# ---------------------------------------------------------------- locks
def lock_held_by_live_process(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        Kernel(dd).db.close()
        p = _child("K.db._c.execute('BEGIN IMMEDIATE')\nprint('locked', flush=True)\ntime.sleep(1.5)\nK.db._c.execute('COMMIT')\n", dd, wait=False)
        p.stdout.readline()
        k = Kernel(dd)
        t0 = time.perf_counter()
        try:
            k.audit.append("waited", {})
            waited, err = time.perf_counter() - t0, None
        except sqlite3.OperationalError as e:
            waited, err = time.perf_counter() - t0, str(e)
        finally:
            k.db.close()
            p.wait(10)
    return err is None and waited >= 0.5, f"append waited {waited:.2f}s for the other writer, error={err}"


def lock_from_dead_process(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        Kernel(dd).db.close()
        p = _child("K.db._c.execute('BEGIN IMMEDIATE')\nK.db._c.execute(\"INSERT INTO audit(seq,ts,kind,payload,prev,hash) VALUES (999,0,'x','{}','p','h')\")\n"
                   "print('locked', flush=True)\ntime.sleep(60)\n", dd, wait=False)
        p.stdout.readline()
        p.send_signal(signal.SIGKILL)
        p.wait(10)
        k = Kernel(dd)
        t0 = time.perf_counter()
        try:
            k.audit.append("after", {})
            err = None
            ok = k.audit.verify()[0]
        except sqlite3.OperationalError as e:
            err, ok = str(e), False
        finally:
            k.db.close()
    return err is None and ok and time.perf_counter() - t0 < 5, f"write after the lock holder died: error={err}, chain ok={ok}, {time.perf_counter() - t0:.2f}s"


def stale_commands(a):
    with kernel() as k:
        _owner(k, pin=False)
        k.db.execute("INSERT INTO owner_commands(id, ts, source, text, state) VALUES ('c-old', ?, 'console', 'open downloads', 'running')", (time.time() - 300,))
        o = _owner(k, pin=False)
        row = k.db.execute("SELECT state, error FROM owner_commands WHERE id='c-old'").fetchone()
        busy = o.busy.locked()
    return row[0] == "failed" and "restarted" in (row[1] or "") and not busy, f"after restart: {row}; gate busy={busy}"


# ---------------------------------------------------------------- clocks
def stuck_jobs_sweep(a):
    from jarvis_kernel.jobs import JOB_TTL, RUN_TTL, Jobs
    with kernel() as k:
        j = Jobs(k)
        q = j.create("laptop", "project_check", {"project": "a"})
        r = j.create("laptop", "project_check", {"project": "b"})
        j.next_for("laptop")          # the oldest (a) is now running
        now = time.time() + max(JOB_TTL, RUN_TTL) + 60
        out = j.sweep(now=now)
        st = dict(k.db.execute("SELECT id, state FROM jobs").fetchall())
        late = j.finish("laptop", q, {"ok": True})
    return (out == {"expired": 1, "stuck_failed": 1} and st[q] == "failed" and st[r] == "expired" and not late,
            f"sweep after the clock jumped {int(now - time.time())}s: {out}; states {st[q]}/{st[r]}; a late result accepted={late}")


def pin_lock_clock_back(a):
    from jarvis_kernel.owner import OwnerError
    now = [1_800_000_000.0]
    with kernel() as k:
        o = _owner(k, clock=lambda: now[0])
        _, aid = k.request(Action("fs", "write", "notes/x.md"))
        for i in range(5):
            try:
                o.approve(aid, aid, f"bad{i}xyz")
            except OwnerError:
                pass
        now[0] -= 3600                         # the clock is set back an hour
        try:
            o.approve(aid, aid, PIN)
            back = 200
        except OwnerError as e:
            back = e.code
        now[0] += 3600 + 601                   # real time moves past the window
        try:
            o.approve(aid, aid, PIN)
            later = 200
        except OwnerError as e:
            later = e.code
    return back == 429 and later == 200, f"after the clock went back 1 h: {back} (still locked); after the window passed: {later}"


def approval_clock_jump(a):
    import jarvis_kernel.store as S

    class Clock:
        off = 0.0

        @staticmethod
        def time():
            return time.time() + Clock.off
    with kernel() as k:
        act = Action("fs", "write", "notes/x.md")
        _, aid = k.request(act)
        k.approve(aid)
        real = S.time
        S.time = Clock
        try:
            Clock.off = k.APPROVAL_TTL + 3600
            r = k.authorize(act, aid)
            hist = k.approval_history()[0]["state"]
        finally:
            S.time = real
    return not r.allowed and r.reason == "approval expired" and hist == "expired", f"clock +1h10m: allowed={r.allowed} ({r.reason}); history {hist}"


# ---------------------------------------------------------------- bad tool results and bad inputs
def invalid_results(a):
    shapes = [None, [], "text", 7, b"\x00\xff", {"content": {"type": "text", "text": "x"}}, {"content": 5}, {"content": "hello"},
              {"content": [None, 3, "s", {"type": "text"}, {"type": "text", "text": None}]}, {"content": [{"type": "resource", "resource": "x"}]},
              {"isError": "yes", "content": []}, {"structuredContent": {"a": {1, 2}}}]
    bad = []
    for s in shapes:
        try:
            out = W.process_result(s)
            json.dumps(out)
            if out.get("untrusted") is not True:
                bad.append(f"{str(s)[:30]}: not marked untrusted")
        except Exception as e:  # noqa: BLE001
            bad.append(f"{str(s)[:30]}: {type(e).__name__}")
    return not bad, (f"{len(shapes)} malformed results handled, all untrusted" if not bad else "; ".join(bad))


def huge_result(a):
    text = "A" * 5_000_000
    t0 = time.perf_counter()
    out = W.process_result({"content": [{"type": "text", "text": text}]}, 16000)
    ms = (time.perf_counter() - t0) * 1000
    return out["truncated"] and len(out["text"]) < 16200 and ms < 15000, f"5 MB result -> {len(out['text'])} chars, truncated={out['truncated']}, {ms:.0f} ms (limit 15000 incl. gitleaks)"


def binary_text(a):
    nasty = "ok\x00\x01\x1b[31mred\x1b[0m \ud800 lone surrogate ￾ end"
    try:
        out = W.process_result({"content": [{"type": "text", "text": nasty}, {"type": "text", "text": b"\x89PNG\r\n".decode("latin-1")}]})
        blob = json.dumps(out)
        from jarvis_kernel.scrub import scrub
        scrub(nasty, deep=True)
        return out["untrusted"] and "ok" in out["text"], f"no crash; {len(blob)} bytes of JSON"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


def job_results(a):
    from jarvis_kernel.jobs import Jobs
    with kernel() as k:
        j = Jobs(k)
        jid = j.create("laptop", "project_check", {"project": "a"})
        j.next_for("laptop")
        f1 = j.finish("laptop", jid, {"ok": True, "n": 1})
        f2 = j.finish("laptop", jid, {"ok": False, "error": "retry"})
        f3 = j.finish("other-node", jid, {"ok": True})
        jid2 = j.create("laptop", "project_check", {"project": "b"})
        j.next_for("laptop")
        big = j.finish("laptop", jid2, {"ok": True, "blob": "x" * 200_000})
        st = dict(k.db.execute("SELECT id, result FROM jobs").fetchall())
        accepted = [json.loads(p)["accepted"] for kd, p in k.db.execute("SELECT kind, payload FROM audit").fetchall() if kd == "job_finished"]
    return (f1 and not f2 and not f3 and big and json.loads(st[jid])["n"] == 1 and json.loads(st[jid2])["error"] == "result too large",
            f"first={f1}, duplicate={f2}, wrong node={f3}; oversized stored as {json.loads(st[jid2])}; audit accepted flags={accepted}")


def node_http_malformed(a):
    from http.server import ThreadingHTTPServer
    from jarvis_kernel.nodes import make_handler
    with kernel() as k:
        H, nodes = make_handler(k)
        tok = nodes.pair(nodes.new_code(), "laptop")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        port = srv.server_address[1]
        codes = []
        try:
            def raw(body: bytes, path="/authorize", length=None):
                s = socket.create_connection(("127.0.0.1", port), timeout=10)
                try:
                    hdr = f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer {tok}\r\nContent-Length: {len(body) if length is None else length}\r\n\r\n"
                    s.sendall(hdr.encode() + body)
                    data = b""
                    while True:                     # read the whole reply so the server is not writing into a closed socket
                        chunk = s.recv(4096)
                        if not chunk:
                            break
                        data += chunk
                    return int(data.split()[1])
                finally:
                    s.close()
            codes.append(raw(b"{not json"))
            codes.append(raw(b"", length=0))
            codes.append(raw(b"{}", length=10_000_000))
            codes.append(raw(b"[1,2,3]"))
            codes.append(raw(json.dumps({"action": "fs.read"}).encode()))
            codes.append(raw(json.dumps({"id": 5, "result": "done"}).encode(), path="/jobs/result"))
            codes.append(raw(b"\xff\xfe\x00garbage"))
            ok = raw(json.dumps({"action": {"tool": "fs", "op": "read", "target": "docs/a.md"}}).encode())
        finally:
            srv.shutdown()
            srv.server_close()
    first = codes[:5] + codes[6:]
    return all(400 <= c < 500 for c in first) and codes[5] == 200 and ok == 200, f"malformed -> {codes} (job result with bad id answered accepted:false); valid request after -> {ok}"


def guardproxy_garbage(a):
    from jarvis_node.guardproxy import GuardProxy
    p = GuardProxy().start()
    try:
        for junk in (b"\r\n\r\n", b"GARBAGE\r\n\r\n", b"CONNECT nohost HTTP/1.1\r\n\r\n", b"CONNECT a:notaport HTTP/1.1\r\n\r\n", b"\x00" * 20000):
            s = socket.create_connection(("127.0.0.1", p.port), timeout=5)
            try:
                s.sendall(junk)
                s.settimeout(5)
                try:
                    s.recv(100)
                except (socket.timeout, OSError):
                    pass
            finally:
                s.close()
        s = socket.create_connection(("127.0.0.1", p.port), timeout=5)
        s.sendall(b"CONNECT 127.0.0.1:1 HTTP/1.1\r\n\r\n")
        r = s.recv(64)
        s.close()
    finally:
        p.stop()
    return r.startswith(b"HTTP/1.1 403"), f"after 5 malformed connections the proxy still answers: {r[:20]!r}"


def connector_busy(a):
    from jarvis_kernel.connector_service import ConnectorService
    from jarvis_kernel.jobs import Jobs
    gate, calls = threading.Event(), []

    def runner(op, endpoint, bearer, tool, args, digest, timeout, max_output):
        calls.append(args)
        gate.wait(5)
        return {"ok": True, "op": op, "result": {"text": "ok"}, "ms": 1}
    with kernel() as k:
        svc = ConnectorService(k, Jobs(k), runner=runner)
        svc.create({"name": "fake", "transport": "https", "endpoint": "https://mcp.example.test/mcp", "lanes": ["private"]})
        t = {"name": "echo", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}
        svc.apply_discovery("fake", {"ok": True, "tools": [W.compact_tool(W.sanitize_tool(t))]})
        svc.set_tool("fake", "echo", enabled=True, risk="read")
        res = []
        th = threading.Thread(target=lambda: res.append(svc.call_tool("hermes", "private", "fake", "echo", {"text": "1"})[0]))
        th.start()
        end = time.time() + 5
        while not calls and time.time() < end:
            time.sleep(0.01)
        c2 = svc.call_tool("hermes", "private", "fake", "echo", {"text": "2"})[0]
        gate.set()
        th.join(10)
        c3 = svc.call_tool("hermes", "private", "fake", "echo", {"text": "3"})[0]
    return res == [200] and c2 == 429 and c3 == 200 and len(calls) == 2, f"first={res}, overlapping={c2}, after={c3}, server calls={len(calls)}"
