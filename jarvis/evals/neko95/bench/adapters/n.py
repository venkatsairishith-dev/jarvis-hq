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


# ================================================================ 2026-10-17: duplicates, races on the real store, restart, cancel,
# timeouts and bounded retries for jobs, tasks, owner commands, automations and the node's job runner (N-034..N-073).
T0 = 1791676800.0          # 2026-10-11 00:00 UTC
JOB = {"name": "repo check", "action_kind": "job", "trigger_kind": "interval", "schedule": "600", "tz": "UTC",
       "action": {"kind": "project_check", "params": {"project": "jarvis-hq"}, "node": "laptop"}}


@contextlib.contextmanager
def two_kernels():
    """Two Kernel instances (two SQLite connections) on one data folder, as two kernel processes would have."""
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        k1, k2 = Kernel(dd), Kernel(dd)
        try:
            yield k1, k2, dd
        finally:
            k1.db.close()
            k2.db.close()


def _tj(k):
    from jarvis_kernel.jobs import Jobs
    from jarvis_kernel.tasks import Tasks
    return Tasks(k), Jobs(k)


def _lab_job(J, tid="a1b2c3d4e5f6"):
    return J.create("lab", "lab_run", {"task": tid, "agent": "hermes", "instructions": "build it", "criteria": ["tests pass"],
                                       "max_attempts": 2, "timeout_s": 600}, owner="hermes", lane="public")


def _state(k, jid):
    return k.db.execute("SELECT state FROM jobs WHERE id=?", (jid,)).fetchone()[0]


def _at(start):
    return f"START={start!r}\nwhile time.time() < START:\n    time.sleep(0.002)\n"


# ---------------------------------------------------------------- duplicates and idempotency
def owner_approval_reuse(a):
    from jarvis_kernel.owner import OwnerError
    with kernel() as k:
        o = _owner(k)
        body = {"op": "repair", "action": "edit_file", "target": "C:/notes/todo.txt", "args": {"find": "teh", "replace": "the"}}
        first = o.pc(dict(body))
        aid = first.get("approval_id")
        k.approve(aid)
        job = o.pc(dict(body, approval_id=aid))
        try:
            o.pc(dict(body, approval_id=aid))                       # the same request retried (double tap, network retry)
            again = "queued again"
        except OwnerError as e:
            again = f"{e.code} {e}"
        n = k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='repair'").fetchone()[0]
    ok = first["status"] == "needs_approval" and job["status"] == "queued" and again.startswith("403") and n == 1
    return ok, f"first {first['status']}; with the approval {job['status']}; the same approval again -> '{again[:80]}'; repair jobs {n}"


def duplicate_results_concurrent(a):
    with kernel() as k:
        _, J = _tj(k)
        jid = J.create("laptop", "project_check", {"project": "a"})
        J.next_for("laptop")
        out, errs = _threads(8, lambda i: J.finish("laptop", jid, {"ok": True, "copy": i}))
        acc = [json.loads(p)["accepted"] for kd, p in k.db.execute("SELECT kind, payload FROM audit").fetchall() if kd == "job_finished"]
        res = json.loads(k.db.execute("SELECT result FROM jobs WHERE id=?", (jid,)).fetchone()[0])
    ok = out.count(True) == 1 and not errs and acc.count(True) == 1 and len(acc) == 8 and res["copy"] == out.index(True)
    return ok, f"8 identical result posts at once: accepted {out.count(True)}; audit accepted flags {acc.count(True)}/{len(acc)}; errors {errs[:1]}"


def file_event_once(a):
    from jarvis_kernel.automations import Automations
    from jarvis_kernel.nodes import record_watch_events
    with kernel() as k:
        T, J = _tj(k)
        A = Automations(k, T, J, owner=None, dispatch=None)
        aid = A.create({"name": "pdf arrivals", "action_kind": "job", "trigger_kind": "event", "event_spec": {"watch": "inbox", "glob": "*.pdf"}, "overlap": "queue",
                        "action": {"kind": "project_check", "params": {"project": "jarvis-hq"}, "node": "laptop"}})["id"]
        f = {"watch": "inbox", "files": [{"rel": "invoice.pdf", "size": 1200, "mtime_ns": 1791676800000000000}]}
        recorded = [record_watch_events(k, "laptop", f) for _ in range(3)]        # the node retried its report
        A.tick()
        recorded.append(record_watch_events(k, "laptop", f))
        A.tick()
        runs1 = len(A.runs(aid))
        record_watch_events(k, "laptop", {"watch": "inbox", "files": [{"rel": "receipt.pdf", "size": 900, "mtime_ns": 1791676900000000000}]})
        A.tick()
        runs2 = len(A.runs(aid))
        jobs = k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    ok = recorded == [1, 0, 0, 0] and runs1 == 1 and runs2 == 2 and jobs == 2
    return ok, f"same file reported 4 times -> new events {recorded}; runs {runs1}; a second file -> runs {runs2}; jobs {jobs}"


def fire_fence(a):
    from jarvis_kernel.automations import Automations
    now = [T0]
    with two_kernels() as (k1, k2, _):
        T1, J1 = _tj(k1)
        T2, J2 = _tj(k2)
        A1 = Automations(k1, T1, J1, owner=None, dispatch=None, clock=lambda: now[0])
        A2 = Automations(k2, T2, J2, owner=None, dispatch=None, clock=lambda: now[0])
        aid = A1.create(JOB)["id"]
        a1 = A1.get(aid)
        key = f"{aid}:retry-slot"
        A1._claim(a1, key, a1["next_run"], "schedule")
        s1 = A1._fire(a1, key)
        s2 = A2._fire(A2.get(aid), key)                          # a retry loop in another process fires the same run
        s3 = A1._fire(a1, key)
        jobs = k1.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    return s1 == "fired" and s2 == s3 == "skipped" and jobs == 1, f"first {s1}, other process {s2}, again {s3}; jobs {jobs}"


def firing_crash(a):
    from jarvis_kernel.automations import CLAIM_GRACE, Automations
    now = [T0]
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        k = Kernel(dd)
        T, J = _tj(k)
        A = Automations(k, T, J, owner=None, dispatch=None, clock=lambda: now[0])
        aid = A.create(dict(JOB, schedule="86400"))["id"]
        a_ = A.get(aid)
        slot = a_["next_run"]
        A._claim(a_, f"{aid}:crash", slot, "schedule")
        k.db.execute("UPDATE automation_runs SET status='firing', fired_at=? WHERE run_key=?", (slot, f"{aid}:crash"))   # died after the fence
        k.db.close()
        k = Kernel(dd)                                            # restart
        T, J = _tj(k)
        A = Automations(k, T, J, owner=None, dispatch=None, clock=lambda: now[0])
        try:
            now[0] = slot + 60
            A.tick()
            mid = [(r["status"], r["job_id"]) for r in A.runs(aid) if r["run_key"].endswith(":crash")]
            now[0] = slot + CLAIM_GRACE + 120
            A.tick()
            late = [(r["status"], r["job_id"]) for r in A.runs(aid) if r["run_key"].endswith(":crash")]
            streak = A.get(aid)["failure_streak"]
        finally:
            k.db.close()
    ok = mid == [("firing", None)] and late == [("unknown", None)] and streak >= 1
    return ok, f"run that died after 'firing': {mid} within the grace, {late} after it (never re-fired); failure streak {streak}"


def job_cancelled_before_start(a):
    with kernel() as k:
        _, J = _tj(k)
        jid = J.create("laptop", "project_check", {"project": "a"})
        c = J.cancel(jid)
        got = J.next_for("laptop")
        late = J.finish("laptop", jid, {"ok": True})
        st, res = k.db.execute("SELECT state, result FROM jobs WHERE id=?", (jid,)).fetchone()
    ok = c["state"] == "cancelled" and got is None and not late and st == "cancelled" and "before it started" in res
    return ok, f"cancel -> {c['state']}; handed to the node {got}; a result for it accepted {late}; stored {st} {res[:60]}"


# ---------------------------------------------------------------- races on the real store
def next_for_two_connections(a):
    with two_kernels() as (k1, k2, _):
        _, J1 = _tj(k1)
        _, J2 = _tj(k2)
        ids = [J1.create("laptop", "project_check", {"project": f"p{i}"}) for i in range(10)]

        def work(i):
            J = J1 if i % 2 else J2
            got = []
            while True:
                j = J.next_for("laptop")
                if not j:
                    return got
                got.append(j["id"])
        out, errs = _threads(8, work)
        handed = [x for lst in out if lst for x in lst]
    ok = sorted(handed) == sorted(ids) and len(set(handed)) == 10 and not errs
    return ok, f"10 jobs, 8 pollers on 2 connections: handed out {len(handed)} ({len(set(handed))} distinct); errors {errs[:1]}"


def next_for_processes(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        k = Kernel(dd)
        _, J = _tj(k)
        ids = [J.create("laptop", "project_check", {"project": f"p{i}"}) for i in range(20)]
        k.db.close()
        start = time.time() + 2.0
        code = ("from jarvis_kernel.jobs import Jobs\nJ = Jobs(K)\n" + _at(start) +
                "got = []\nwhile True:\n    j = J.next_for('laptop')\n    if not j: break\n    got.append(j['id'])\nprint(json.dumps(got))\n")
        ps = [_child(code, dd, wait=False) for _ in range(4)]
        outs = [p.communicate(timeout=120) for p in ps]
    handed = [x for o, _ in outs for x in json.loads(o.strip().splitlines()[-1] or "[]")] if all(o.strip() for o, _ in outs) else []
    ok = sorted(handed) == sorted(ids) and len(set(handed)) == 20
    return ok, f"20 jobs, 4 processes polling at once: handed out {len(handed)} ({len(set(handed))} distinct) {'' if handed else outs[0][1][-200:]}"


def report_cancel_race(a):
    trials, bad = int(a.get("trials", 20)), []
    with two_kernels() as (k1, k2, _):
        T1, _ = _tj(k1)
        T2, _ = _tj(k2)
        for t in range(trials):
            tid = T1.create("hermes", "public", "", f"goal {t}")
            T1.update(None, tid, state="running", attempt=True)
            out, errs = _threads(2, lambda i: (T1 if i == 0 else T2).update(None, tid, state="completed" if i == 0 else "cancelled",
                                                                              result="runner: verified" if i == 0 else None))
            g = T1.get(tid)
            finals = k1.db.execute("SELECT COUNT(*) FROM audit WHERE kind IN ('task_completed','task_cancelled') AND payload LIKE ?", (f"%{tid}%",)).fetchone()[0]
            win = [i for i in (0, 1) if out[i] is not None]
            want = ["completed", "cancelled"][win[0]] if len(win) == 1 else None
            if len(win) != 1 or len(errs) != 1 or g["state"] != want or finals != 1 or g["events"][0]["state"] != want:
                bad.append(f"trial {t}: winners {win} state {g['state']} finals {finals} errs {errs[:1]}")
    return not bad, (f"{trials} races of the runner's 'completed' against the owner's cancel: one winner each time, never overwritten" if not bad
                     else "; ".join(bad)[:400])


def attempts_concurrent(a):
    with two_kernels() as (k1, k2, _):
        T1, _ = _tj(k1)
        T2, _ = _tj(k2)
        tid = T1.create("hermes", "public", "", "flaky build", max_attempts=3)
        T1.update(None, tid, state="running")
        out, errs = _threads(10, lambda i: (T1 if i % 2 else T2).update(None, tid, attempt=True))
        g = T1.get(tid)
    ok_n = sum(1 for x in out if x is not None)
    ok = ok_n == 3 and g["attempts"] == 3 and len(errs) == 7 and all("budget" in e for e in errs)
    return ok, f"10 simultaneous attempt reports, budget 3: accepted {ok_n}, attempts {g['attempts']}, refusals {len(errs)} ({errs[:1]})"


def expire_complete_race(a):
    trials, bad = int(a.get("trials", 20)), []
    with two_kernels() as (k1, k2, _):
        T1, _ = _tj(k1)
        T2, _ = _tj(k2)
        for t in range(trials):
            tid = T1.create("hermes", "public", "", f"goal {t}", budget_s=60)
            T1.update(None, tid, state="running", attempt=True)
            T1.update(None, tid, state="verifying")
            k1.db.execute("UPDATE tasks SET deadline=? WHERE id=?", (time.time() - 1, tid))
            out, errs = _threads(2, lambda i: T1.expire() if i == 0 else T2.update(None, tid, state="completed", result="verified"))
            g = T1.get(tid)
            finals = k1.db.execute("SELECT COUNT(*) FROM audit WHERE kind IN ('task_completed','task_failed') AND payload LIKE ?", (f"%{tid}%",)).fetchone()[0]
            if g["state"] not in ("completed", "failed") or finals != 1 or g["events"][0]["state"] != g["state"] \
                    or (g["state"] == "failed") != (out[0] == [tid]):
                bad.append(f"trial {t}: state {g['state']} finals {finals} expire->{out[0]} errs {errs[:1]}")
    return not bad, (f"{trials} races of the deadline sweep against a completion: exactly one end each time, state, event and audit agree"
                     if not bad else "; ".join(bad)[:400])


def cancel_handout_race(a):
    trials, bad = int(a.get("trials", 20)), []
    with two_kernels() as (k1, k2, _):
        _, J1 = _tj(k1)
        _, J2 = _tj(k2)
        for t in range(trials):
            jid = J1.create("laptop", "project_check", {"project": f"p{t}"})
            out, errs = _threads(2, lambda i: J1.cancel(jid) if i == 0 else J2.next_for("laptop"))
            st, req = k1.db.execute("SELECT state, cancel_requested FROM jobs WHERE id=?", (jid,)).fetchone()
            handed = bool(out[1] and out[1]["id"] == jid)
            fine = (out[0]["state"] == "cancelled" and not handed and st == "cancelled") or \
                   (out[0]["state"] == "cancelling" and handed and st == "running" and req is not None)
            if not fine or errs:
                bad.append(f"trial {t}: cancel {out[0] and out[0]['state']}, handed {handed}, state {st}, errs {errs[:1]}")
            if handed:
                J1.finish("laptop", jid, {"ok": False, "cancelled": True})
    return not bad, (f"{trials} races of cancel against hand-out: cancelled-and-never-run or running-with-a-stop-request, never both"
                     if not bad else "; ".join(bad)[:400])


def expire_two_connections(a):
    with two_kernels() as (k1, k2, _):
        T1, _ = _tj(k1)
        T2, _ = _tj(k2)
        ids = [T1.create("hermes", "public", "", f"g{i}") for i in range(20)]
        k1.db.execute("UPDATE tasks SET deadline=?", (time.time() - 1,))
        out, errs = _threads(2, lambda i: (T1 if i == 0 else T2).expire())
        got = (out[0] or []) + (out[1] or [])
        rows = k1.db.execute("SELECT COUNT(*) FROM audit WHERE kind='task_failed'").fetchone()[0]
        ev = k1.db.execute("SELECT COUNT(*) FROM task_events WHERE note='time budget used up'").fetchone()[0]
    ok = sorted(got) == sorted(ids) and len(set(got)) == 20 and rows == 20 and ev == 20 and not errs
    return ok, f"2 sweepers on 2 connections, 20 overdue tasks: failed {len(got)} ({len(set(got))} distinct), audit rows {rows}, events {ev}"


def mixed_load(a):
    from jarvis_kernel.policy import Action
    with kernel() as k:
        T, J = _tj(k)

        def work(i):
            for n in range(15):
                kind = (i + n) % 3
                if kind == 0:
                    t = T.create("hermes", "public", "", f"g{i}-{n}")
                    T.update(None, t, state="running", attempt=True)
                    T.update(None, t, state="failed", result="x")
                elif kind == 1:
                    jid = J.create(f"node{i}", "project_check", {"project": f"p{i}{n}"})
                    j = J.next_for(f"node{i}")
                    assert j and j["id"] == jid
                    assert J.finish(f"node{i}", jid, {"ok": True})
                else:
                    act = Action("fs", "write", f"notes/{i}-{n}.md")
                    _, aid = k.request(act)
                    assert k.approve(aid) and k.authorize(act, aid).allowed
        _, errs = _threads(6, work)
        ok_chain, msg = k.audit.verify()
        n_t = k.db.execute("SELECT COUNT(*) FROM tasks WHERE state='failed'").fetchone()[0]
        n_j = k.db.execute("SELECT COUNT(*) FROM jobs WHERE state='done'").fetchone()[0]
        n_a = k.db.execute("SELECT COUNT(*) FROM approvals WHERE state='used'").fetchone()[0]
    ok = not errs and ok_chain and n_t == n_j == n_a == 30
    return ok, f"6 threads x 15 mixed operations: tasks {n_t}, jobs {n_j}, approvals used {n_a} (30 each); chain {msg}; errors {errs[:1]}"


def approval_once_processes(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        k = Kernel(dd)
        act = Action("fs", "write", "notes/x.md", {"text": "once"})
        _, aid = k.request(act)
        k.approve(aid)
        k.db.close()
        start = time.time() + 2.0
        code = ("from jarvis_kernel.policy import Action\na = Action('fs', 'write', 'notes/x.md', {'text': 'once'})\n" + _at(start) +
                f"print(K.authorize(a, {aid!r}).allowed)\n")
        ps = [_child(code, dd, wait=False) for _ in range(4)]
        outs = [p.communicate(timeout=120)[0].strip() for p in ps]
        k = Kernel(dd)
        try:
            ok_chain = k.audit.verify()[0]
        finally:
            k.db.close()
    return outs.count("True") == 1 and outs.count("False") == 3 and ok_chain, f"4 processes use one approval at once: {outs}; chain ok={ok_chain}"


def tasks_processes(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        _tj(Kernel(dd))[0].k.db.close()
        start = time.time() + 2.0
        code = ("from jarvis_kernel.tasks import Tasks\nT = Tasks(K)\n" + _at(start) +
                "for i in range(25):\n    t = T.create('p%d' % os.getpid(), 'public', '', 'goal %d' % i)\n    T.update(None, t, state='running')\nprint('ok')\n")
        ps = [_child(code, dd, wait=False) for _ in range(4)]
        outs = [p.communicate(timeout=120) for p in ps]
        k = Kernel(dd)
        try:
            n = k.db.execute("SELECT COUNT(*) FROM tasks WHERE state='running'").fetchone()[0]
            ev = k.db.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
            ok_chain, msg = k.audit.verify()
        finally:
            k.db.close()
    rc = [p.returncode for p in ps]
    return n == 100 and ev == 200 and ok_chain and rc == [0] * 4, f"4 processes x 25 tasks: running {n}, events {ev}, chain {msg}, exit codes {rc} {outs[0][1][-150:] if rc[0] else ''}"


def tick_processes(a):
    from jarvis_kernel.automations import Automations
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        k = Kernel(dd)
        T, J = _tj(k)
        aid = Automations(k, T, J, owner=None, dispatch=None, clock=lambda: T0).create(JOB)["id"]
        k.db.close()
        start = time.time() + 2.0
        code = ("from jarvis_kernel.tasks import Tasks\nfrom jarvis_kernel.jobs import Jobs\nfrom jarvis_kernel.automations import Automations\n"
                f"A = Automations(K, Tasks(K), Jobs(K), owner=None, dispatch=None, clock=lambda: {T0 + 610!r})\n" + _at(start) +
                "print(json.dumps(A.tick()['fired']))\n")
        ps = [_child(code, dd, wait=False) for _ in range(3)]
        outs = [p.communicate(timeout=120) for p in ps]
        k = Kernel(dd)
        try:
            runs = k.db.execute("SELECT COUNT(*) FROM automation_runs WHERE automation_id=?", (aid,)).fetchone()[0]
            jobs = k.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        finally:
            k.db.close()
    fired = [len(json.loads(o.strip().splitlines()[-1])) if o.strip() else -1 for o, _ in outs]
    return sorted(fired) == [0, 0, 1] and runs == 1 and jobs == 1, f"3 kernel processes tick the same due slot at once: fired {fired}; runs {runs}; jobs {jobs} {outs[0][1][-150:] if -1 in fired else ''}"


# ---------------------------------------------------------------- crashes and restarts
def crash_inside_task_update(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        k = Kernel(dd)
        T, _ = _tj(k)
        tid = T.create("hermes", "public", "", "goal")
        T.update(None, tid, state="running", attempt=True)
        k.db.close()
        rc, out, err = _child("from jarvis_kernel.tasks import Tasks\nT = Tasks(K)\norig = K.audit.append\n"
                              "def die(kind, payload):\n    if kind == 'task_completed':\n        os._exit(9)\n    return orig(kind, payload)\n"
                              f"K.audit.append = die\nT.update(None, {tid!r}, state='completed', result='claimed done')\n", dd)
        k = Kernel(dd)
        try:
            g = _tj(k)[0].get(tid)
            ok_chain = k.audit.verify()[0]
        finally:
            k.db.close()
    ok = rc == 9 and g["state"] == "running" and g["result"] == "" and all(e["state"] != "completed" for e in g["events"]) and ok_chain
    return ok, f"child exit {rc}; task after the crash: {g['state']} result '{g['result']}'; completed event {any(e['state'] == 'completed' for e in g['events'])}; chain ok={ok_chain}"


def restart_mid_job(a):
    with tempfile.TemporaryDirectory() as d:
        dd = os.path.join(d, "k")
        k = Kernel(dd)
        _, J = _tj(k)
        jid = J.create("laptop", "project_check", {"project": "a"})
        J.next_for("laptop")
        k.db.close()
        k = Kernel(dd)                                             # the kernel restarts while the laptop works
        try:
            _, J = _tj(k)
            first = J.finish("laptop", jid, {"ok": True, "n": 1})
            dup = J.finish("laptop", jid, {"ok": True, "n": 2})     # the node's retry of the same post
            rep = [(r[0], r[2]) for r in J.to_report()]
            J.mark_notified(jid)
            again = J.to_report()
            n = json.loads(k.db.execute("SELECT result FROM jobs WHERE id=?", (jid,)).fetchone()[0])["n"]
        finally:
            k.db.close()
    ok = first and not dup and rep == [(jid, "done")] and again == [] and n == 1
    return ok, f"after restart: result accepted {first}, duplicate accepted {dup}; reported {rep} then {again}; stored n={n}"


def orphan_rules(a):
    with kernel() as k:
        _, J = _tj(k)
        new = J.create("laptop", "project_check", {"project": "a"})
        J.next_for("laptop")
        old = J.create("laptop", "project_check", {"project": "b"})
        J.next_for("laptop")
        lab = _lab_job(J)
        J.next_for("lab")
        now = time.time()
        k.db.execute("UPDATE jobs SET started=? WHERE id=?", (now - 5, new))
        k.db.execute("UPDATE jobs SET started=? WHERE id=?", (now - 60, old))
        k.db.execute("UPDATE jobs SET started=? WHERE id=?", (now - 600, lab))
        n = J.fail_orphans("laptop", now) + J.fail_orphans("lab", now)
        st = {x: _state(k, x) for x in (new, old, lab)}
        err = json.loads(k.db.execute("SELECT result FROM jobs WHERE id=?", (old,)).fetchone()[0])["error"]
    ok = n == 1 and st[new] == "running" and st[old] == "failed" and st[lab] == "running" and "lost this job" in err
    return ok, f"node says idle: failed {n}; just handed over {st[new]}, 60 s old {st[old]} ('{err[:60]}'), lab run {st[lab]}"


def lost_result_post(a):
    from unittest import mock
    from jarvis_node import agent as NA
    with kernel() as k, tempfile.TemporaryDirectory() as out_dir:
        _, J = _tj(k)
        jid = J.create("laptop", "output_save", {"agent": "hermes", "name": "report.md", "content": "hello"})
        job = J.next_for("laptop")
        posts = []

        def fake_post(url, path, obj, token=None):                   # fake network: the result post never arrives
            posts.append(path)
            raise OSError("network unreachable")
        with mock.patch.dict(os.environ, {"JARVIS_OUTPUTS": out_dir}), mock.patch.object(NA, "_post", fake_post):
            NA._do_job("http://kernel.invalid", "tok", job)
        mid = _state(k, jid)
        n = J.fail_orphans("laptop", time.time() + 60)                 # the node then reports that it is idle
        res = json.loads(k.db.execute("SELECT result FROM jobs WHERE id=?", (jid,)).fetchone()[0] or "{}")
        late = J.finish("laptop", jid, {"ok": True, "saved": "report.md"})
        files = [f for _, _, fs in os.walk(out_dir) for f in fs]
    ok = posts == ["/jobs/result"] and mid == "running" and n == 1 and "nothing was reported" in res.get("error", "") and not late and files == ["report.md"]
    return ok, f"result posts tried {posts}; job while unreported {mid}; idle report failed {n} ('{res.get('error', '')[:70]}'); late result accepted {late}; files {files}"


# ---------------------------------------------------------------- cancellation and timeouts (node runtime, jobs, owner)
def node_loop_cancel(a):
    from unittest import mock
    from jarvis_node import agent as NA, jobs as NJ, runtime as RT, watch as NW

    class FakeWatcher:
        def __init__(self, *x, **kw): pass
        def names(self): return []
        def scan(self): return {}
        def ack(self, r): pass
    out = {}
    slow = lambda script, env=None, timeout=30: RT.run([sys.executable, "-c", "import time; time.sleep(60)"], timeout=timeout)  # noqa: E731
    with tempfile.TemporaryDirectory() as d:
        tok = os.path.join(d, "node_token.txt")
        with open(tok, "w") as fh:
            fh.write("synthetic-node-token")
        posts = []

        def fake_post(url, path, obj, token=None):                   # fake kernel: the owner asked to stop job j-loop
            posts.append(path)
            return {"ok": True, "killed": False, "cancel": ["j-loop"]} if path == "/report" else {}
        with mock.patch.object(RT, "powershell", slow), mock.patch.object(NA, "TOKEN_FILE", tok), mock.patch.object(NA, "_post", fake_post), \
                mock.patch.object(NA, "collect", lambda: {"cpu": 1}), mock.patch.object(NW, "Watcher", FakeWatcher), \
                mock.patch.object(NW, "load_watches", lambda *x: {}):
            th = threading.Thread(target=lambda: out.update(r=NJ.run_job({"id": "j-loop", "kind": "diag", "params": {"op": "processes", "target": ""}})))
            th.start()
            end = time.time() + 10
            while time.time() < end and not RT._current["procs"]:
                time.sleep(0.05)
            child = RT._current["procs"][0] if RT._current["procs"] else None
            t0 = time.time()
            NA.run("http://kernel.invalid", "laptop", once=True, sleep=lambda s: None)
            th.join(20)
            sec = time.time() - t0
    r = out.get("r") or {}
    dead = child is not None and child.poll() is not None
    ok = "/report" in posts and r.get("cancelled") is True and r.get("ok") is False and dead and sec < 15 and RT.current() is None
    return ok, f"report loop posted {posts}; job result {r}; child process stopped {dead}; {sec:.1f} s"


def node_run_timeout(a):
    from jarvis_node import runtime as RT
    RT.begin("j-timeout")
    t0, err = time.time(), None
    try:
        RT.run([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    except subprocess.TimeoutExpired:
        err = "TimeoutExpired"
    child = RT._current["procs"][0] if RT._current["procs"] else None
    sec = time.time() - t0
    RT.end()
    dead = child is not None and child.poll() is not None
    return err == "TimeoutExpired" and dead and sec < 5, f"30 s program with a 0.5 s limit: {err} after {sec:.1f} s; child stopped {dead}"


def node_cancel_children(a):
    from jarvis_node import runtime as RT
    RT.begin("j-two")
    p1 = RT.popen([sys.executable, "-c", "import time; time.sleep(60)"])
    p2 = RT.popen([sys.executable, "-c", "import time; time.sleep(60)"])
    wrong = RT.cancel("j-other")
    right = RT.cancel("j-two")
    t0 = time.time()
    while time.time() - t0 < 5 and (p1.poll() is None or p2.poll() is None):
        time.sleep(0.05)
    try:
        RT.popen([sys.executable, "-c", "print(1)"])
        third = "started"
    except RT.Cancelled:
        third = "refused"
    RT.end()
    ok = not wrong and right and p1.poll() is not None and p2.poll() is not None and third == "refused" and RT.current() is None
    return ok, f"cancel of another id {wrong}; cancel {right}; both children stopped {p1.poll() is not None and p2.poll() is not None}; a new program after cancel {third}"


def node_cancel_no_leak(a):
    from jarvis_node import runtime as RT
    RT.begin("j1")
    RT.end()
    late = RT.cancel("j1")                                    # the owner's stop arrives after j1 finished
    RT.begin("j2")
    flag = RT.cancelled()
    r = RT.run([sys.executable, "-c", "print('fine')"], timeout=20)
    RT.end()
    return not late and not flag and r.stdout.strip() == "fine", f"late cancel of a finished job accepted {late}; next job sees cancelled {flag}; it ran: '{r.stdout.strip()}'"


def node_handler_crash(a):
    from unittest import mock
    from jarvis_node import jobs as NJ, runtime as RT

    def boom(p, root=None):
        raise ValueError("C:/Users/owner/secret path in the message")
    with mock.patch.object(NJ, "output_save", boom):
        r1 = NJ.run_job({"id": "j-crash", "kind": "output_save", "params": {"agent": "hermes", "name": "a.md", "content": "x"}})
    cur = RT.current()
    r2 = NJ.run_job({"id": "j-next", "kind": "nonsense", "params": {}})
    ok = r1 == {"ok": False, "error": "ValueError"} and cur is None and r2 == {"ok": False, "error": "unknown job kind"} and RT.current() is None
    return ok, f"crashing handler -> {r1}; job slot free after {cur is None}; next job -> {r2}"


def job_running_cancel(a):
    with kernel() as k:
        _, J = _tj(k)
        jid = J.create("laptop", "project_check", {"project": "a"})
        J.next_for("laptop")
        c1 = J.cancel(jid)
        told = J.cancels_for("laptop")
        fin = J.finish("laptop", jid, {"ok": False, "cancelled": True, "error": "cancelled by the owner"})
        st = _state(k, jid)
        after = J.cancels_for("laptop")
        c2 = J.cancel(jid)
    ok = c1["state"] == "cancelling" and told == [jid] and fin and st == "cancelled" and after == [] and c2["state"] == "cancelled" and "nothing" in c2.get("note", "")
    return ok, f"cancel while running -> {c1['state']}; node told {told == [jid]}; node confirms -> {st}; still told {after}; cancel again -> {c2}"


def lab_ttl(a):
    from jarvis_kernel.jobs import LAB_RUN_TTL, RUN_TTL
    with kernel() as k:
        _, J = _tj(k)
        pc = J.create("laptop", "project_check", {"project": "a"})
        J.next_for("laptop")
        lab = _lab_job(J)
        J.next_for("lab")
        now = time.time()
        k.db.execute("UPDATE jobs SET started=? WHERE id=?", (now - RUN_TTL - 60, pc))
        k.db.execute("UPDATE jobs SET started=? WHERE id=?", (now - 3600, lab))
        s1 = J.sweep(now)
        mid = (_state(k, pc), _state(k, lab))
        s2 = J.sweep(now - 3600 + LAB_RUN_TTL + 60)
        end = (_state(k, pc), _state(k, lab))
    ok = s1["stuck_failed"] == 1 and mid == ("failed", "running") and s2["stuck_failed"] == 1 and end == ("failed", "failed")
    return ok, f"pc job 31 min silent / lab run 1 h: {mid}; lab run past {LAB_RUN_TTL // 3600} h: {end}"


def ok_must_be_true(a):
    with kernel() as k:
        _, J = _tj(k)
        st = []
        for res in ({"ok": "false"}, {"ok": 1}, {"ok": True}):
            jid = J.create("laptop", "project_check", {"project": "a"})
            J.next_for("laptop")
            J.finish("laptop", jid, res)
            st.append(_state(k, jid))
    return st == ["failed", "failed", "done"], f"node results ok='false', ok=1, ok=true recorded as {st}"


def result_while_killed(a):
    with kernel() as k:
        _, J = _tj(k)
        jid = J.create("laptop", "project_check", {"project": "a"})
        J.next_for("laptop")
        other = J.create("laptop", "project_check", {"project": "b"})
        k.kill("bench")
        fin = J.finish("laptop", jid, {"ok": True})
        handed = J.next_for("laptop")
        k.resume()
        after = J.next_for("laptop")
        again = J.next_for("laptop")
        st = _state(k, jid)
    ok = fin and st == "done" and handed is None and after and after["id"] == other and again is None
    return ok, f"while killed: result recorded {fin}, new job handed out {handed and handed['id']}; after resume: {after and after['id'] == other}, then {again}"


def owner_command_cancel(a):
    from jarvis_kernel.owner import OwnerError
    with kernel() as k:
        gate, calls = threading.Event(), []

        def hermes(text):                                           # fake agent: the first answer is very slow
            calls.append(text)
            if len(calls) == 1:
                gate.wait(20)
                return "first answer, very late"
            return "second answer"
        o = _owner(k, pin=False)
        o.hermes = hermes
        c1 = o.command("open the downloads folder")["id"]
        end = time.time() + 10
        while not calls and time.time() < end:
            time.sleep(0.02)
        try:
            o.command("check the disk please")
            busy = 200
        except OwnerError as e:
            busy = e.code
        cancelled = o.cancel_command(c1)["cancelled"]
        c2 = o.command("check the disk please")["id"]
        end = time.time() + 20
        while time.time() < end and k.db.execute("SELECT state FROM owner_commands WHERE id=?", (c2,)).fetchone()[0] == "running":
            time.sleep(0.05)
        gate.set()
        end = time.time() + 20
        late = []
        while time.time() < end and not late:
            late = [p for kd, p in k.db.execute("SELECT kind, payload FROM audit").fetchall() if kd == "owner_command_late" and c1 in p]
            time.sleep(0.05)
        r1 = k.db.execute("SELECT state, reply FROM owner_commands WHERE id=?", (c1,)).fetchone()
        r2 = k.db.execute("SELECT state FROM owner_commands WHERE id=?", (c2,)).fetchone()[0]
        free = not o.busy.locked()
    ok = busy == 409 and cancelled and r1 == ("cancelled", None) and r2 == "done" and late and free
    return ok, f"second command while busy -> {busy}; cancel -> {cancelled}; new command -> {r2}; late answer of the cancelled one ignored: {r1}, audited {bool(late)}; gate free {free}"


def owner_command_crash(a):
    from jarvis_kernel.owner import OwnerError
    with kernel() as k:
        o = _owner(k, pin=False)
        o.spawn = lambda fn: fn()

        def bad(text):
            raise RuntimeError("provider 500 at https://api.example.test/v1?key=SYNTHETIC")
        o.hermes = bad
        c1 = o.command("summarise my day")["id"]
        row = k.db.execute("SELECT state, error FROM owner_commands WHERE id=?", (c1,)).fetchone()
        o.hermes = lambda t: "fine"
        try:
            c2 = o.command("summarise my day again")["id"]
            st2 = k.db.execute("SELECT state FROM owner_commands WHERE id=?", (c2,)).fetchone()[0]
        except OwnerError as e:
            st2 = f"refused {e.code}"
    ok = row[0] == "failed" and (row[1] or "").startswith("RuntimeError") and st2 == "done"
    return ok, f"agent crashed -> {row[0]} ('{row[1][:60]}'); next command -> {st2}"


def owner_commands_concurrent(a):
    from jarvis_kernel.owner import OwnerError
    with kernel() as k:
        gate = threading.Event()
        o = _owner(k, pin=False)
        o.hermes = lambda text: gate.wait(20) and "ok"

        def send(i):
            try:
                return o.command(f"command number {i} please")["state"]
            except OwnerError as e:
                return e.code
        out, errs = _threads(10, send)
        gate.set()
        end = time.time() + 20
        while time.time() < end and o.busy.locked():
            time.sleep(0.05)
        n = k.db.execute("SELECT COUNT(*) FROM owner_commands").fetchone()[0]
    ok = out.count("running") == 1 and out.count(409) == 9 and n == 1 and not errs
    return ok, f"10 commands at once: accepted {out.count('running')}, 409 {out.count(409)}; rows {n}; errors {errs[:1]}"


def stop_all_state(a):
    with kernel() as k:
        o = _owner(k, pin=False)
        T, J = o.tasks, o.jobs
        tid = T.create("hermes", "public", "", "A tool", criteria=["tests pass"])
        lab = _lab_job(J, tid)
        T.update("hermes", tid, state="queued")
        J.next_for("lab")
        T.update(None, tid, state="running", attempt=True)
        pc = J.create("laptop", "project_check", {"project": "a"})
        act = Action("fs", "write", "notes/x.md")
        _, aid = k.request(act)
        out = o.stop_all()
        try:
            T.update(None, tid, state="verifying")
            late = "accepted"
        except Exception as e:  # noqa: BLE001
            late = str(e)[:40]
        handed = J.next_for("laptop")
        k.approve(aid)
        used = k.authorize(act, aid).allowed
        st = (T.get(tid)["state"], _state(k, pc), k.approval_info(aid)["state"])
    ok = out["cancelled_tasks"] == [tid] and out["denied_approvals"] == 1 and out["expired_jobs"] >= 1 and "already cancelled" in late \
        and handed is None and not used and st == ("cancelled", "expired", "denied")
    return ok, f"stop all -> {out}; runner's late report '{late}'; queued job handed out {handed}; denied approval usable {used}; states {st}"


def refused_runs_pause(a):
    from jarvis_kernel.automations import Automations
    now, calls = [T0], []

    def dispatch(agent, lane, tid, p):                            # fake lab path that keeps refusing
        calls.append(tid)
        return 409, {"reason": "the lab is not reachable"}
    with kernel() as k:
        T, J = _tj(k)
        A = Automations(k, T, J, owner=None, dispatch=dispatch, clock=lambda: now[0])
        aid = A.create({"name": "nightly", "action_kind": "lab_task", "trigger_kind": "interval", "schedule": "600", "pause_after_failures": 2,
                        "action": {"goal": "Build", "instructions": "build the site", "criteria": ["it builds"], "agent": "hermes"}})["id"]
        for _ in range(4):
            now[0] += 610
            A.tick()
        cur = A.get(aid)
        st = [r["status"] for r in A.runs(aid)]
    ok = len(calls) == 2 and st == ["refused", "refused"] and (cur["enabled"], cur["state"]) == (0, "paused") and "2 failed" in (cur["paused_reason"] or "")
    return ok, f"4 slots, the lab refuses: dispatch tried {len(calls)}x; runs {st}; automation {cur['state']} ({cur['paused_reason']})"


def task_budget_bounds(a):
    from jarvis_kernel.tasks import TaskError
    with kernel() as k:
        T, _ = _tj(k)
        bad, good = [], []
        for ma, bs in ((0, 600), (6, 600), (3, 59), (3, 6 * 3600 + 1), (100, 10 ** 9)):
            try:
                T.create("hermes", "public", "", "g", max_attempts=ma, budget_s=bs)
                bad.append((ma, bs))
            except TaskError:
                pass
        for ma, bs in ((1, 60), (5, 6 * 3600)):
            try:
                good.append(T.get(T.create("hermes", "public", "", "g", max_attempts=ma, budget_s=bs))["max_attempts"])
            except TaskError:
                pass
    return not bad and good == [1, 5], f"unbounded budgets accepted {bad or 'none'}; edge budgets accepted max_attempts {good}"


# ---------------------------------------------------------------- the agent/runner HTTP endpoints (127.0.0.1)
@contextlib.contextmanager
def proxy():
    from http.server import ThreadingHTTPServer
    from types import SimpleNamespace
    from jarvis_kernel.llm_proxy import make_handler
    with kernel() as k:
        H, tokens = make_handler(k, [], owner_hermes=None)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}"

        def post(path, body, tok):
            r = urllib.request.Request(url + path, data=json.dumps(body).encode(), method="POST",
                                       headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
            try:
                resp = urllib.request.urlopen(r, timeout=60)
                return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")
        P = SimpleNamespace(k=k, post=post, owner=tokens.issue("hermes"), runner=tokens.issue("lab-runner", "runner"))
        P.T, P.J = _tj(k)
        P.act = lambda op, target, params: post("/v1/jarvis/actions", {"tool": "task", "op": op, "target": target, "params": params}, P.owner)
        P.run = lambda path, body: post("/v1/jarvis/runner/" + path, body, P.runner)
        try:
            yield P
        finally:
            srv.shutdown()
            srv.server_close()


def _dispatched(P, attempts=2):
    tid = P.act("create", "t", {"goal": "A tool", "criteria": ["tests pass"], "max_attempts": attempts, "request": f"tool {time.time()}"})[1]["task"]
    P.act("dispatch", tid, {"instructions": "build it and test it", "public_ok": True})
    return tid


def duplicate_task_create(a):
    with proxy() as P:
        req = "please build me a currency converter with tests"
        out, errs = _threads(5, lambda i: P.act("create", "t", {"goal": "Currency converter", "criteria": ["tests pass"], "request": req}))
        codes = sorted(c for c, _ in out if c)
        n = P.k.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    return codes == [200, 409, 409, 409, 409] and n == 1, f"the same request 5 times at once -> {codes}; tasks created {n}; errors {errs[:1]}"


def runner_attempt_budget(a):
    with proxy() as P:
        tid = _dispatched(P, attempts=2)
        job = P.run("next", {})[1]["job"]["id"]
        seq = []
        for body in ({"state": "running", "attempt": True}, {"state": "verifying"}, {"state": "repairing", "note": "test 3 fails"},
                     {"state": "running", "attempt": True}, {"state": "verifying"}, {"state": "repairing"}, {"attempt": True},
                     {"state": "failed", "result": "not verified after 2 attempt(s): test 3 fails"}):
            seq.append(P.run("report", dict(body, job=job))[0])
        g = P.T.get(tid)
    ok = seq == [200, 200, 200, 200, 200, 409, 409, 200] and (g["state"], g["attempts"]) == ("failed", 2) and "2 attempt" in g["result"]
    return ok, f"runner reports -> {seq}; task {g['state']} after {g['attempts']} attempts: '{g['result'][:60]}'"


def runner_next_race(a):
    with proxy() as P:
        _dispatched(P)
        out, errs = _threads(6, lambda i: P.run("next", {}))
        got = [r.get("job") for c, r in out if c == 200]
    jobs = [j for j in got if j]
    return len(jobs) == 1 and len(got) == 6 and not errs, f"6 runners poll at once for 1 lab job: {len(jobs)} got it, {len(got) - len(jobs)} got none; errors {errs[:1]}"


def lab_success_after_cancel(a):
    with proxy() as P:
        from jarvis_kernel.owner import Owner
        tid = _dispatched(P)
        job = P.run("next", {})[1]["job"]["id"]
        P.run("report", {"job": job, "state": "running", "attempt": True})
        Owner(P.k, P.T, P.J).task_control(tid, "cancel")
        rep = P.run("report", {"job": job, "state": "completed", "result": "verified", "verification": {"passed": True}})[0]
        fin = P.run("finish", {"job": job, "result": {"ok": True, "summary": "verified after 1 attempt(s)"}})[0]
        g = P.T.get(tid)
        js = _state(P.k, job)
    ok = rep == 409 and fin == 200 and g["state"] == "cancelled" and g["verification"] == {} and js == "done"
    return ok, f"runner's 'completed' after the owner's cancel -> {rep}; finish -> {fin}; task stays {g['state']} (verification {g['verification']}); lab job {js}"
