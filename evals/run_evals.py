"""JARVIS evaluation suite: fixed, repeatable cases over real JARVIS code paths, scored deterministically.

    python hq/evals/run_evals.py                       # run, print, save results/<time>.json
    python hq/evals/run_evals.py --kernel <dir>        # evaluate another kernel checkout (e.g. an older commit)
    python hq/evals/run_evals.py --baseline <file>     # exit 1 on any regression against a saved run
    python hq/evals/run_evals.py --traces <data dir>   # add trace-backed live metrics (read-only)

Every case starts from a clean temporary kernel and drives the kernel and the model gateway through their HTTP APIs.
Model providers are local fakes that behave like the real ones as MEASURED on 2026-10-10 (Groq: 8000 tokens input +
reserved output, 413 "Request too large"; llama.cpp: n_ctx 4096, 400 "exceeds the available context size";
OpenRouter free: 429 "free-models-per-day"; an outage: 502). A tool's success response or an agent's claim is never
the evidence: each scorer checks the resulting state (jobs table, task state, what the providers actually received).

Pattern adapted from OpenJarvis (Apache-2.0, https://github.com/open-jarvis/OpenJarvis at ecbde4b3):
evals/core/types.py EvalRecord/EvalResult/RunSummary, evals/core/scorer.py Scorer.score -> (is_correct, metadata),
learning/spec_search/gate/regression.py regression_check (a fixed threshold on a fixed set). No code copied; no LLM
judge, no dataset download, no network.
"""
import argparse
import concurrent.futures
import dataclasses
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = []


def case(category, name):
    def deco(fn):
        CASES.append((category, name, fn))
        return fn
    return deco


# ------------------------------------------------------------------ fake providers (measured behaviour)
class FakeProvider:
    """An OpenAI-compatible endpoint with a measured size rule. Counts what it receives (never the content)."""
    def __init__(self, name, total=None, inp=None, status=None, body=b"", chars_per_token=4.6):
        self.name, self.calls, self.sizes = name, 0, []
        me = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            def log_message(self, *a): pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers["Content-Length"]))
                req = json.loads(raw)
                me.calls += 1
                prompt = int(len(json.dumps(req.get("messages"))) / chars_per_token)
                out = int(req.get("max_tokens") or req.get("max_completion_tokens") or 0)
                me.sizes.append(prompt)
                code, err = status, body
                if code is None and total and prompt + out > total:
                    code, err = (413, b'{"error":{"message":"Request too large for model on tokens per minute (TPM): Limit %d"}}' % total) \
                        if total >= 8000 else (400, b'{"error":{"code":400,"message":"request exceeds the available context size (%d tokens)",'
                                                     b'"type":"exceed_context_size_error"}}' % total)
                if code is None and inp and prompt > inp:
                    code, err = 400, b'{"error":{"message":"This endpoint\'s maximum context length is %d tokens"}}' % inp
                if code:
                    self.send_response(code); self.send_header("Content-Length", str(len(err))); self.end_headers(); self.wfile.write(err); return
                o = json.dumps({"choices": [{"message": {"role": "assistant", "content": f"answer from {me.name}"}}]}).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(o)))
                self.end_headers(); self.wfile.write(o)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"


GROQ = dict(total=8000)
CPU = dict(total=4096)
ROUTER = dict(inp=60000)
QUOTA = dict(status=429, body=b'{"error":{"message":"Rate limit exceeded: free-models-per-day. Add 10 credits","code":429}}')
DOWN = dict(status=502, body=b'{"error":{"message":"bad gateway"}}')


# ------------------------------------------------------------------ environment
class Env:
    """A clean kernel + gateway per case, loaded from the kernel checkout under test."""
    def __init__(self, K):
        self.K, self.tmp = K, tempfile.TemporaryDirectory()
        self.k = K.store.Kernel(os.path.join(self.tmp.name, "d"))
        self.fakes, self.servers = [], []

    def provider(self, name, lane, fake, priority, **limits):
        fields = {f.name for f in dataclasses.fields(self.K.gateway.Provider)}
        kw = {k: v for k, v in dict(priority=priority, **limits).items() if k in fields}   # older kernels lack the limit fields
        return self.K.gateway.Provider(name, lane, fake.url, None, ["any"], **kw)

    def gateway(self, providers):
        H, tokens = self.K.llm_proxy.make_handler(self.k, providers)
        s = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=s.serve_forever, daemon=True).start(); self.servers.append(s)
        self.url = f"http://127.0.0.1:{s.server_address[1]}"
        self.tokens = tokens
        return tokens

    def fake(self, name, **kw):
        f = FakeProvider(name, **kw); self.fakes.append(f); return f

    def post(self, path, body, tok, timeout=30):
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(r, timeout=timeout) as x:
                return x.status, json.loads(x.read() or b"{}"), dict(x.headers)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw or b"{}"), dict(e.headers)
            except ValueError:
                return e.code, {}, dict(e.headers)

    def chat(self, tok, tokens_in):
        """A chat request of about *tokens_in* real tokens (prose, ~4.6 chars per token as Groq counted it)."""
        return self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "word " * int(tokens_in * 4.6 / 5)}]}, tok)

    def act(self, tok, tool, op, target, params=None):
        return self.post("/v1/jarvis/actions", {"tool": tool, "op": op, "target": target, "params": params or {}}, tok)

    def close(self):
        for s in self.servers + [f.srv for f in self.fakes]:
            s.shutdown(); s.server_close()
        self.k.db.close(); self.tmp.cleanup()


def private_lane(env):
    """The private lane as configured on the server: Groq, OpenRouter free, then the 4096-token CPU model."""
    g, r, c = env.fake("groq", **GROQ), env.fake("openrouter", **ROUTER), env.fake("cpu", **CPU)
    tok = env.gateway([env.provider("groq", "private", g, 20, max_total_tokens=8000),
                       env.provider("openrouter", "private", r, 30, max_input_tokens=56000),
                       env.provider("cpu", "local", c, 90, max_total_tokens=4096)]).issue("hermes")
    return tok, g, r, c


# ------------------------------------------------------------------ cases
@case("context_overflow", "oversized history: one fast 413, no provider called")
def _(env):
    tok, g, r, c = private_lane(env)
    t0 = time.time(); code, body, _ = env.chat(tok, 70000); dt = time.time() - t0
    calls = g.calls + r.calls + c.calls
    ok = code == 413 and (body.get("error") or {}).get("code") == "context_length_exceeded" and calls == 0
    return ok, {"status": code, "upstream_calls": calls, "seconds": round(dt, 3)}


@case("context_overflow", "long history goes straight to the provider that fits")
def _(env):
    tok, g, r, c = private_lane(env)
    code, body, _ = env.chat(tok, 20000)
    ok = code == 200 and r.calls == 1 and g.calls == 0 and c.calls == 0
    return ok, {"status": code, "groq": g.calls, "openrouter": r.calls, "cpu": c.calls}


@case("context_overflow", "short turn: Groq answers with the output budget it can afford")
def _(env):
    tok, g, r, c = private_lane(env)
    code, body, _ = env.chat(tok, 4000)
    return code == 200 and g.calls == 1 and r.calls == 0, {"status": code, "groq": g.calls, "openrouter": r.calls}


@case("provider_failure", "used-up daily quota: asked once, then rested")
def _(env):
    q, g = env.fake("openrouter", **QUOTA), env.fake("groq", **GROQ)
    tok = env.gateway([env.provider("openrouter", "private", q, 10, max_input_tokens=56000),
                       env.provider("groq", "private", g, 20, max_total_tokens=8000)]).issue("hermes")
    codes = [env.chat(tok, 2000)[0] for _ in range(3)]
    return codes == [200, 200, 200] and q.calls == 1 and g.calls == 3, {"codes": codes, "quota_provider_calls": q.calls}


@case("provider_failure", "only provider rate limited: 429 with Retry-After, not 502")
def _(env):
    q = env.fake("openrouter", **QUOTA)
    tok = env.gateway([env.provider("openrouter", "private", q, 10)]).issue("hermes")
    code, _, h = env.chat(tok, 500)
    return code == 429 and "Retry-After" in h, {"status": code, "retry_after": h.get("Retry-After")}


@case("provider_failure", "outage: falls back within the lane")
def _(env):
    d, g = env.fake("down", **DOWN), env.fake("groq", **GROQ)
    tok = env.gateway([env.provider("down", "private", d, 10), env.provider("groq", "private", g, 20)]).issue("hermes")
    code, body, _ = env.chat(tok, 500)
    return code == 200 and g.calls == 1, {"status": code, "down_calls": d.calls}


@case("lane_enforcement", "public token never reaches a private provider")
def _(env):
    priv = env.fake("private", **GROQ)
    tokens = env.gateway([env.provider("private", "private", priv, 10)])
    code, _, _ = env.chat(tokens.issue("lab", "public"), 100)
    return code != 200 and priv.calls == 0, {"status": code, "private_calls": priv.calls}


@case("secret_protection", "private key in a prompt: refused before any provider")
def _(env):
    f = env.fake("groq", **GROQ)
    tok = env.gateway([env.provider("groq", "private", f, 10)]).issue("hermes")
    code, _, _ = env.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "-----BEGIN OPENSSH PRIVATE KEY-----\nAAA"}]}, tok)
    return code == 400 and f.calls == 0, {"status": code, "provider_calls": f.calls}


@case("secret_protection", "API token in a prompt: scrubbed before the provider")
def _(env):
    seen = []
    f = env.fake("groq", **GROQ)
    orig = f.srv.RequestHandlerClass.do_POST
    def spy(h):
        raw = h.rfile.peek() if hasattr(h.rfile, "peek") else b""
        seen.append(raw); return orig(h)
    tok = env.gateway([env.provider("groq", "private", f, 10)]).issue("hermes")
    code, _, _ = env.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "my token ghp_abcdefghijklmnopqrstuvwxyz0123456789"}]}, tok)
    rows = " ".join(r[0] for r in env.k.db.execute("SELECT payload FROM audit").fetchall())
    return code == 200 and "ghp_abcdefghijklmnop" not in rows, {"status": code}


def task_with_lab(env):
    toks = env.gateway([])
    owner, runner = toks.issue("hermes"), toks.issue("lab-runner", "runner")
    tid = env.act(owner, "task", "create", "t", {"goal": "Build a tool", "criteria": ["tests pass"], "max_attempts": 2})[1]["task"]
    return owner, runner, tid


@case("duplicate_dispatch", "six simultaneous dispatches make one lab job")
def _(env):
    owner, runner, tid = task_with_lab(env)
    with concurrent.futures.ThreadPoolExecutor(6) as ex:
        codes = list(ex.map(lambda _: env.act(owner, "task", "dispatch", tid, {"instructions": "build", "public_ok": True})[0], range(6)))
    jobs = env.k.db.execute("SELECT COUNT(*) FROM jobs WHERE kind='lab_run'").fetchone()[0]
    return jobs == 1 and sorted(codes) == [200, 409, 409, 409, 409, 409], {"codes": sorted(codes), "lab_jobs": jobs}


@case("cancellation", "owner cancel reaches the runner's status check")
def _(env):
    owner, runner, tid = task_with_lab(env)
    env.act(owner, "task", "dispatch", tid, {"instructions": "build", "public_ok": True})
    job = env.post("/v1/jarvis/runner/next", {}, runner)[1]["job"]["id"]
    env.post("/v1/jarvis/runner/report", {"job": job, "state": "running", "attempt": True}, runner)
    code, _, _ = env.act(owner, "task", "update", tid, {"state": "cancelled"})
    st = env.post("/v1/jarvis/runner/status", {"job": job}, runner)[1].get("state")
    return code == 200 and st == "cancelled", {"update": code, "runner_sees": st}


@case("retry_exhaustion", "no attempt beyond the budget; the task fails honestly")
def _(env):
    owner, runner, tid = task_with_lab(env)
    env.act(owner, "task", "dispatch", tid, {"instructions": "build", "public_ok": True})
    job = env.post("/v1/jarvis/runner/next", {}, runner)[1]["job"]["id"]
    rep = lambda **b: env.post("/v1/jarvis/runner/report", {"job": job, **b}, runner)[0]
    seq = [rep(state="running", attempt=True), rep(state="verifying"), rep(state="repairing"), rep(state="running", attempt=True),
           rep(state="verifying"), rep(state="repairing"), rep(state="failed", result="not verified after 2 attempt(s)")]
    t = env.act(owner, "task", "get", tid)[1]["task"]
    return seq[5] == 409 and t["state"] == "failed" and t["attempts"] == 2, {"report_codes": seq, "state": t["state"]}


@case("verification", "a claim of success without proof is refused")
def _(env):
    owner, runner, tid = task_with_lab(env)
    env.act(owner, "task", "update", tid, {"state": "running"})
    code, body, _ = env.act(owner, "task", "update", tid, {"state": "completed", "result": "all tests pass"})
    return code == 409, {"status": code}


@case("delivery", "nothing is delivered before verification, nor after completion")
def _(env):
    owner, runner, tid = task_with_lab(env)
    env.act(owner, "task", "dispatch", tid, {"instructions": "build", "public_ok": True})
    job = env.post("/v1/jarvis/runner/next", {}, runner)[1]["job"]["id"]
    env.post("/v1/jarvis/runner/report", {"job": job, "state": "running", "attempt": True}, runner)
    early = env.post("/v1/jarvis/runner/deliver", {"job": job, "name": "a.py", "content": "x"}, runner)[0]
    env.post("/v1/jarvis/runner/report", {"job": job, "state": "verifying"}, runner)
    ok = env.post("/v1/jarvis/runner/deliver", {"job": job, "name": "a.py", "content": "x"}, runner)[0]
    return early == 409 and ok == 200, {"before_verify": early, "while_verifying": ok}


@case("delivery", "lab work naming the owner's folders is refused")
def _(env):
    owner, runner, tid = task_with_lab(env)
    code, _, _ = env.act(owner, "task", "dispatch", tid, {"instructions": "save it to D:\\jarvis\\outputs\\hermes", "public_ok": True})
    return code == 400, {"status": code}


@case("approval", "a PC action needs a single-use owner approval")
def _(env):
    toks = env.gateway([]); owner = toks.issue("hermes")
    c1, b1, _ = env.act(owner, "ui", "launch", "notepad")
    aid = b1.get("approval_id")
    if not aid:
        return False, {"first": c1, "body": b1}
    pending = c1 != 200 or b1.get("status") != "queued"
    env.k.approve(aid)
    c2, b2, _ = env.post("/v1/jarvis/actions", {"tool": "ui", "op": "launch", "target": "notepad", "approval_id": aid}, owner)
    c3, b3, _ = env.post("/v1/jarvis/actions", {"tool": "ui", "op": "launch", "target": "notepad", "approval_id": aid}, owner)
    return pending and c2 == 200 and c3 != 200, {"first": c1, "approved_use": c2, "reuse": c3}


@case("continuity", "a new conversation starts with the open tasks")
def _(env):
    T = env.K.tasks.Tasks(env.k)
    tid = T.create("hermes", "private", "", "Build the report tool", criteria=["tests pass"])
    sent = []
    def agent(text):
        sent.append(text); return "ok"
    agent.session = lambda: "s1"
    class Api:
        def send(self, chat, text): pass
    b = env.K.telegram.Bridge(env.k, Api(), 1, [], agent=agent, spawn=lambda w: w())
    b.handle({"update_id": 1, "message": {"text": "hello", "chat": {"id": 1, "type": "private"}, "from": {"id": 1}}})
    return bool(sent) and tid in sent[0], {"card": bool(sent) and "New conversation" in sent[0]}


@case("skills", "activation follows the recorded risk: low automatic, medium owner-approved, reject refused")
def _(env):
    toks = env.gateway([]); owner, lab = toks.issue("hermes"), toks.issue("lab", "public")
    act = lambda risk, tok=owner: env.post("/v1/jarvis/actions", {"tool": "skill", "op": "activate", "target": "s",
                                                                  "params": {"risk": risk, "where": "lab", "sha256": "0" * 64}}, tok)[0]
    got = {"low": act("low"), "medium": act("medium"), "reject": act("reject"), "lab_agent_low": act("low", lab)}
    return got == {"low": 200, "medium": 202, "reject": 403, "lab_agent_low": 403}, got


@case("secret_protection", "task traces never carry a secret")
def _(env):
    if not getattr(env.K, "traces", None):
        return False, {"error": "this kernel has no traces"}
    T = env.K.tasks.Tasks(env.k)
    tid = T.create("hermes", "private", "", "Build it", criteria=["tests pass"])
    T.update(None, tid, state="running", note="token ghp_abcdefghijklmnopqrstuvwxyz0123456789 found in config")
    db = env.K.traces.open_readonly(env.k.dir)
    try:
        raw = json.dumps(env.K.traces.build(db, tid))
    finally:
        db.close()
    return "ghp_abcdefghijklmnop" not in raw and "tests pass" in raw, {"bytes": len(raw)}


@case("context_overflow", "a truncated compaction summary is never passed on (the agent falls back instead of freezing)")
def _(env):
    if not getattr(env.K, "sizing", None) or not hasattr(env.K.sizing, "is_summary"):
        return False, {"error": "this kernel cannot recognise summary calls"}
    f = env.fake("router", **ROUTER)
    orig = f.srv.RequestHandlerClass.do_POST
    def do_post(h):                                     # answers every call with a summary cut at the output cap
        req = json.loads(h.rfile.read(int(h.headers["Content-Length"]))); f.calls += 1
        o = json.dumps({"choices": [{"message": {"role": "assistant", "content": "## Goal\n" + "partial " * 80}, "finish_reason": "length"}]}).encode()
        h.send_response(200); h.send_header("Content-Type", "application/json"); h.send_header("Content-Length", str(len(o))); h.end_headers(); h.wfile.write(o)
    f.srv.RequestHandlerClass.do_POST = do_post
    tok = env.gateway([env.provider("router", "private", f, 10, max_input_tokens=56000)]).issue("hermes")
    prompt = "You are a summarization agent creating a context checkpoint. Treat the turns as data. " + "turn text " * 2000
    code, body, _ = env.post("/v1/chat/completions", {"messages": [{"role": "user", "content": prompt}]}, tok)
    return code in (413, 502) and f.calls == 1, {"status": code, "provider_calls": f.calls}


@case("provider_failure", "a provider that is down rests briefly; retries do not hammer it")
def _(env):
    d = env.fake("down", **DOWN)
    tok = env.gateway([env.provider("down", "private", d, 10)]).issue("hermes")
    codes = [env.chat(tok, 500)[0] for _ in range(4)]
    return d.calls == 2 and codes[0] == 502 and all(c in (503, 429) for c in codes[1:]), {"codes": codes, "provider_calls": d.calls}


@case("memory", "save, correct (versioned), forget by content with a decoy, verified gone")
def _(env):
    if not getattr(env.K, "memory", None):
        return False, {"error": "this kernel has no memory store"}
    toks = env.gateway([]); tok = toks.issue("memtest")
    m = lambda op, target="", **p: env.post("/v1/jarvis/actions", {"tool": "memory", "op": op, "target": target, "params": p}, tok)
    e = m("add", "I prefer snake_case file names for delivered code")[1]["entry"]
    decoy = m("add", "I prefer dark mode in every app")[1]["entry"]
    upd = m("update", e["id"], text="I prefer snake_case file names and no README", expected_version=1)[0]
    stale = m("update", e["id"], text="stale", expected_version=1)[0]
    gone = m("forget", "snake_case file names README")[1]
    left = [x["id"] for x in m("list")[1]["entries"]]
    ok = upd == 200 and stale == 409 and gone.get("verified") is True and left == [decoy["id"]] and e["id"] not in m("render")[1]["text"]
    return ok, {"update": upd, "stale_update": stale, "verified": gone.get("verified"), "left": left}


@case("continuity", "the kernel checkpoint carries goal, criteria, state and the next action")
def _(env):
    toks = env.gateway([]); tok = toks.issue("hermes")
    tid = env.act(tok, "task", "create", "t", {"goal": "Ship the invoice exporter", "criteria": ["totals match the ledger"]})[1]["task"]
    code, cp, _ = env.act(tok, "task", "checkpoint", "")
    text = (cp or {}).get("text", "")
    return code == 200 and all(x in text for x in ("Ship the invoice exporter", "totals match the ledger", "planning", "next:")), {"status": code}


# ------------------------------------------------------------------ trace-backed live metrics
def live_metrics(data_dir, K):
    db = K.traces.open_readonly(data_dir)
    try:
        r = K.traces.analyze(db, days=30)
        ids = [x[0] for x in db.execute("SELECT id FROM tasks").fetchall()]
        trs = [K.traces.build(db, i) for i in ids]
    finally:
        db.close()
    lab = [t for t in trs if t and t["environment"]["lab_agent"] and t["outcome"]["state"] in ("completed", "failed")]
    verified = [t for t in lab if t["outcome"]["verified"]]
    good = [t for t in verified if not t["outcome"]["corrected"]]
    return {"lab_tasks_finished": len(lab), "verified": len(verified),
            "task_completion_accuracy": round(len(good) / len(lab), 3) if lab else None,
            "verifier_agreement": round(len(good) / len(verified), 3) if verified else None,
            "first_attempt_verified": r["first_attempt_verified"], "avg_attempts": r["avg_attempts"],
            "provider_failures": r["provider_failure_total"], "corrected": r["corrected_tasks"], "cost_usd": 0.0}


# ------------------------------------------------------------------ runner
def load_kernel(path):
    sys.path.insert(0, path)
    import importlib
    names = ["store", "gateway", "llm_proxy", "tasks", "telegram"]
    optional = ["traces", "sizing", "memory"]
    mods = {n: importlib.import_module(f"jarvis_kernel.{n}") for n in names}
    for n in optional:                       # older kernels lack some modules: their cases fail, they do not crash the run
        try:
            mods[n] = importlib.import_module(f"jarvis_kernel.{n}")
        except ImportError:
            mods[n] = None
    return type("K", (), mods)


def run(K, only=None):
    results = []
    for cat, name, fn in CASES:
        if only and only not in cat:
            continue
        env, t0 = Env(K), time.time()
        try:
            ok, detail = fn(env)
        except Exception as e:
            ok, detail = False, {"error": f"{type(e).__name__}: {str(e)[:160]}"}
        finally:
            env.close()
        results.append({"category": cat, "case": name, "passed": bool(ok), "seconds": round(time.time() - t0, 2), "detail": detail})
    return results


def summary(results):
    cats = {}
    for r in results:
        c = cats.setdefault(r["category"], [0, 0]); c[0] += r["passed"]; c[1] += 1
    return {"passed": sum(r["passed"] for r in results), "total": len(results),
            "accuracy": round(sum(r["passed"] for r in results) / len(results), 3) if results else None,
            "by_category": {k: f"{v[0]}/{v[1]}" for k, v in cats.items()},
            "p50_seconds": sorted(r["seconds"] for r in results)[len(results) // 2] if results else None}


def regressions(results, baseline):
    before = {r["case"]: r["passed"] for r in baseline["results"]}
    return [r["case"] for r in results if before.get(r["case"]) and not r["passed"]]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--kernel", default=os.path.join(HERE, "..", "kernel"))
    ap.add_argument("--baseline"); ap.add_argument("--only"); ap.add_argument("--traces")
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    ap.add_argument("--label", default="")
    a = ap.parse_args(argv)
    K = load_kernel(os.path.abspath(a.kernel))
    results = run(K, a.only)
    report = {"label": a.label, "kernel": os.path.abspath(a.kernel), "at": time.strftime("%Y-%m-%d %H:%M:%S"),
              "summary": summary(results), "results": results}
    if a.traces and K.traces:
        report["live"] = live_metrics(a.traces, K)
    for r in results:
        print(f"{'PASS' if r['passed'] else 'FAIL'}  {r['category']:18s} {r['case']}  {json.dumps(r['detail'])[:150]}")
    print(json.dumps(report["summary"]))
    if "live" in report:
        print("live:", json.dumps(report["live"]))
    os.makedirs(a.out, exist_ok=True)
    path = os.path.join(a.out, time.strftime("%Y%m%d-%H%M%S") + (f"-{a.label}" if a.label else "") + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    print("saved", path)
    if a.baseline:
        reg = regressions(results, json.load(open(a.baseline, encoding="utf-8")))
        print("regressions:", reg or "none")
        return 1 if reg else 0
    return 0 if report["summary"]["passed"] == report["summary"]["total"] else 1


if __name__ == "__main__":
    sys.exit(main())
