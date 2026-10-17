"""J: provider routing and resources. Drives the real gateway.py (lanes, fallback), sizing.py (token estimates, refusal
classes), llm_proxy.py (the agents' model endpoint) against MOCK upstream providers on local ports, local_team (routing,
memory guard, adviser only through the privacy gateway) and the privacy gateway itself (jarvis/privacy/gateway.py).

Nothing here reaches a real provider. Every key is synthetic ("SYNTH-..."), written to a temp dir. The privacy gateway's
Presidio/spaCy/GLiNER detector is not installed in the Linux test image, so it is replaced AT THE MODEL BOUNDARY by a
small redactor that uses redactor.py's own cue-word pattern and real Mapping; the gateway's forwarding, fallback and
restoring code is the real one.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import sys
import tempfile
import threading
import time
import types
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]

from jarvis_kernel import gateway, sizing  # noqa: E402
from jarvis_kernel.gateway import Provider, RouteError  # noqa: E402

REPLY = {"id": "r1", "object": "chat.completion", "model": "mock",
         "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hello from mock"}}],
         "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}


def reply(text="hello from mock", finish="stop"):
    r = json.loads(json.dumps(REPLY))
    r["choices"][0]["message"]["content"] = text
    r["choices"][0]["finish_reason"] = finish
    return r


# ---------------------------------------------------------------- mock upstream providers

class Mock:
    """An OpenAI-style upstream on 127.0.0.1:<free port>. `steps` is a list of behaviours consumed one per request; the
    last one repeats. Each behaviour is a dict: {"ok": reply} | {"status": code, "body": bytes, "headers": {}} |
    {"sleep": s, then...} | {"raw": bytes} (a 200 with exactly these bytes) | {"stream": [chunks], "gap": s} |
    {"echo_auth": code} (an error page that echoes the Authorization header back, like a careless proxy) |
    {"drop": True} (reads the request, then closes the connection without any answer, like a provider process dying).
    {"hook": fn} runs fn() when the request arrives (to change the world while the caller waits). Each seen entry is
    (method, path, body, Authorization, monotonic arrival time)."""

    def __init__(self, name, steps=None, port=0):
        self.name, self.steps, self.seen, self.n = name, list(steps or [{"ok": REPLY}]), [], 0
        self.write_failed = threading.Event()
        mock = self

        class U(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _send(self, code, data, ctype="application/json", headers=None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except OSError:                      # the caller gave up (timeout tests): expected
                    mock.write_failed.set()

            def do_GET(self):
                mock.seen.append(("GET", self.path, None, self.headers.get("Authorization"), time.monotonic()))
                self._send(200, json.dumps({"data": [{"id": "mock"}]}).encode())

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                with threading.Lock():
                    i = min(mock.n, len(mock.steps) - 1)
                    mock.n += 1
                st = mock.steps[i]
                mock.seen.append(("POST", self.path, body, self.headers.get("Authorization"), time.monotonic()))
                if st.get("hook"):
                    st["hook"]()
                if st.get("sleep"):
                    time.sleep(st["sleep"])
                if st.get("drop"):
                    self.close_connection = True
                    return
                if "status" in st:
                    return self._send(st["status"], st.get("body", b""), headers=st.get("headers"))
                if "echo_auth" in st:
                    return self._send(st["echo_auth"], json.dumps({"error": {"message": "bad key "
                                      + str(self.headers.get("Authorization"))}}).encode())
                if "raw" in st:
                    return self._send(200, st["raw"])
                if "stream" in st:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.end_headers()
                    try:
                        for c in st["stream"]:
                            self.wfile.write(c if isinstance(c, bytes) else c.encode())
                            self.wfile.flush()
                            time.sleep(st.get("gap", 0))
                    except OSError:
                        mock.write_failed.set()
                    return
                out = st.get("ok", REPLY)
                if callable(out):
                    out = out(body)
                self._send(200, json.dumps(out).encode())

        from jarvis_kernel.llm_proxy import ProxyServer       # mock provider: a real provider's queue is not 5 either
        self.srv = ProxyServer(("127.0.0.1", port), U)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"

    def posts(self):
        return [s for s in self.seen if s[0] == "POST"]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def dead_url():
    """An address where nothing listens (connection refused)."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}/v1"


class Env:
    """A temp kernel + key files + mock upstreams + (optionally) the real llm_proxy on a free port."""

    def __init__(self):
        from jarvis_kernel.store import Kernel
        self.tmp = tempfile.TemporaryDirectory()
        self.k = Kernel(os.path.join(self.tmp.name, "kernel"))
        self.mocks, self.servers, self.now = [], [], [1_000_000.0]
        self.keys = {}

    def key(self, name):
        val = f"SYNTH-{name.upper()}-KEY-" + os.urandom(6).hex()
        p = os.path.join(self.tmp.name, name + ".key")
        with open(p, "w") as f:
            f.write(val)
        self.keys[name] = val
        return p

    def mock(self, name, steps=None):
        m = Mock(name, steps)
        self.mocks.append(m)
        return m

    def proxy(self, providers, hermes=None):
        """hermes: the owner's agent as seen by the owner plane (a fake callable text -> reply), or None (no agent)."""
        from jarvis_kernel.llm_proxy import make_handler
        H, tokens = make_handler(self.k, providers, clock=lambda: self.now[0], owner_hermes=hermes)
        from jarvis_kernel.llm_proxy import ProxyServer
        s = ProxyServer(("127.0.0.1", 0), H)                 # the server class llm_proxy.serve() runs in production
        threading.Thread(target=s.serve_forever, daemon=True).start()
        self.servers.append(s)
        self.url = f"http://127.0.0.1:{s.server_address[1]}"
        self.H, self.tokens = H, tokens
        return tokens

    def post(self, tok, body, path="/v1/chat/completions", timeout=30):
        """(status, headers, body bytes) - errors are returned, not raised."""
        r = urllib.request.Request(self.url + path, data=json.dumps(body).encode(), method="POST",
                                   headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers or {}), e.read()

    def get(self, tok, path):
        r = urllib.request.Request(self.url + path, headers={"Authorization": "Bearer " + tok})
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def audit_text(self):
        return " ".join(str(r[0]) for r in self.k.db.execute("SELECT payload FROM audit"))

    def audit(self, kind):
        return [json.loads(r[0]) for r in self.k.db.execute("SELECT payload FROM audit WHERE kind=?", (kind,))]

    def close(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        for m in self.mocks:
            m.close()
        self.k.db.close()
        self.tmp.cleanup()


@contextlib.contextmanager
def env():
    e = Env()
    try:
        yield e
    finally:
        e.close()


def msg(text="hi"):
    return {"model": "agent-choice", "messages": [{"role": "user", "content": text}]}


def P(name, lane, url, key=None, **kw):
    return Provider(name, lane, url, key, ["any"], default_model=kw.pop("default_model", name + "-model"), **kw)


# ---------------------------------------------------------------- gateway.py: lanes and planning

def lane_plan(a):
    """Plan a chain for a task lane from a provider table; compare the names (or the RouteError)."""
    with tempfile.TemporaryDirectory() as d:
        provs = []
        for spec in a["providers"]:
            spec = dict(spec)
            if spec.pop("key", None) == "present":
                p = os.path.join(d, spec["name"] + ".key")
                open(p, "w").write("SYNTH-KEY")
                spec["key_file"] = p
            elif spec.get("key_file") == "missing":
                spec["key_file"] = os.path.join(d, "nope.key")
            provs.append(Provider(**{"key_file": None, "models": ["any"], "base_url": "http://127.0.0.1:9/v1", **spec}))
        try:
            got = [p.name for p in gateway.plan(provs, a["lane"], a.get("purpose"))]
        except RouteError as e:
            got = "RouteError"
            if a.get("want") == "RouteError":
                return True, f"refused: {e}"
        return got == a["want"], f"chain {got}, want {a['want']}"


def explain_codes(a):
    """gateway.explain gives a reason code per provider; check the codes and the chosen provider."""
    provs = [Provider(**{"key_file": None, "models": ["any"], "base_url": "http://127.0.0.1:9/v1", **s}) for s in a["providers"]]
    out = gateway.explain(provs, a["lane"], a.get("purpose"), a.get("est_tokens"), a.get("rest"), a.get("preferred"))
    codes = {c["provider"]: c["codes"] for c in out["candidates"]}
    bad = [f"{n}: {codes.get(n)} lacks {w}" for n, ws in a["want_codes"].items() for w in ws if w not in codes.get(n, [])]
    ok = not bad and out["chosen"] == a["want_chosen"]
    return ok, f"chosen {out['chosen']} chain {out['chain']}; " + ("; ".join(bad) or "codes as expected")


def gateway_call_scrub_and_lane(a):
    """gateway.call on a private task: the public (Dahl-like) mock is never asked; a token in the prompt is redacted,
    and a private key blocks the call before any request."""
    with env() as e:
        pub = e.mock("dahl")
        priv_fail = e.mock("priv1", [{"status": 500}])
        priv_ok = e.mock("priv2", [{"ok": reply("private answer")}])
        provs = [P("dahl", "public", pub.url, priority=0), P("priv1", "private", priv_fail.url, priority=1),
                 P("priv2", "private", priv_ok.url, priority=2)]
        secret = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
        out = gateway.call(e.k, provs, "private", f"my account note, token {secret} please summarise", max_tokens=64)
        sent = json.dumps([s[2] for s in priv_ok.posts()])
        blocked = False
        try:
            gateway.call(e.k, provs, "private", "-----BEGIN RSA PRIVATE KEY-----\nMIIE...")
        except RouteError:
            blocked = True
        ok = (out == "private answer" and not pub.posts() and secret not in sent and "[REDACTED]" in sent and blocked
              and len(priv_ok.posts()) == 1 and secret not in e.audit_text())
        return ok, (f"answer={out!r}; dahl asked {len(pub.posts())}x; priv1 {len(priv_fail.posts())}x; token leaked={secret in sent}; "
                    f"private key blocked={blocked}; requests after block={len(priv_ok.posts())}")


def gateway_call_free_model_guard(a):
    """A provider whose model must match ':free$' is refused before any request when a paid model is configured."""
    with env() as e:
        m = e.mock("openrouter")
        ok_m = e.mock("groq")
        provs = [P("openrouter", "public", m.url, default_model="anthropic/claude-big", model_pattern=":free$", priority=1),
                 P("groq", "public", ok_m.url, priority=2)]
        out = gateway.call(e.k, provs, "public", "hello")
        refused = e.audit("model_refused")
        return (not m.posts() and len(ok_m.posts()) == 1 and refused and out), \
            f"paid-model provider asked {len(m.posts())}x; fallback asked {len(ok_m.posts())}x; refusals audited {len(refused)}"


def gateway_call_empty_and_all_fail(a):
    """An empty reply counts as a failure (next provider); when every provider fails the error names the class only."""
    with env() as e:
        empty = e.mock("empty", [{"ok": reply("")}])
        dead = dead_url()
        good = e.mock("good", [{"ok": reply("filled")}])
        provs = [P("empty", "private", empty.url, priority=1), P("dead", "private", dead, priority=2), P("good", "private", good.url, priority=3)]
        out = gateway.call(e.k, provs, "private", "hi")
        try:
            gateway.call(e.k, provs[:2], "private", "hi")
            err = "no error"
        except RouteError as x:
            err = str(x)
        fails = [f["error"] for f in e.audit("model_call_failed")]
        ok = out == "filled" and err.startswith("all providers failed") and "ValueError" in fails and "URLError" in fails
        return ok, f"answer {out!r}; all-fail error {err!r}; failure classes {fails}"


def gateway_timeout_falls_through(a):
    """A provider that hangs past its own timeout_s is abandoned and the next one answers, within the timeout plus margin."""
    with env() as e:
        slow = e.mock("slow", [{"sleep": 3, "ok": REPLY}])
        fast = e.mock("fast", [{"ok": reply("fast answer")}])
        provs = [P("slow", "private", slow.url, priority=1, timeout_s=1), P("fast", "private", fast.url, priority=2)]
        t0 = time.perf_counter()
        out = gateway.call(e.k, provs, "private", "hi")
        dt = time.perf_counter() - t0
        return (out == "fast answer" and dt < a.get("max_s", 2.5)), f"answer {out!r} in {dt:.2f}s (limit {a.get('max_s', 2.5)}s)"


# ---------------------------------------------------------------- sizing.py

def sizing_estimate(a):
    """Token estimate for prose: within the stated band of chars/4.66 (measured Groq ratio), counts tools, grows with size."""
    prose = ("The quick brown fox jumps over the lazy dog while the owner plans a trip to Chennai next week. " * 250)
    body = {"messages": [{"role": "system", "content": "You are helpful."}, {"role": "user", "content": prose}]}
    est = sizing.estimate_tokens(body)
    measured_like = len(prose) / 4.66
    with_tools = sizing.estimate_tokens(dict(body, tools=[{"type": "function", "function": {"name": "search", "description": "x" * 4000}}]))
    ratio = est / measured_like
    ok = a["lo"] <= ratio <= a["hi"] and with_tools - est >= 900 and sizing.estimate_tokens({"messages": []}) >= 1
    return ok, f"estimate {est} vs measured-ratio {measured_like:.0f} (ratio {ratio:.2f}, band {a['lo']}-{a['hi']}); tools add {with_tools - est}"


def sizing_classify(a):
    got = [sizing.classify(c, t) for c, t in a["cases"]]
    return got == a["want"], f"classes {got}"


def sizing_cooldown(a):
    got = [sizing.cooldown_s(r, t) for r, t in a["cases"]]
    return got == a["want"], f"cooldowns {got}"


def sizing_fit_budget(a):
    """fits / fits_with_output / output_budget for an 8000-token-total provider (Groq TPM style)."""
    p = Provider("groq", "private", "http://x/v1", None, ["any"], max_total_tokens=8000)
    q = Provider("big", "private", "http://x/v1", None, ["any"], max_input_tokens=120000)
    got = {"fits_6000": sizing.fits(p, 6000), "fits_7500": sizing.fits(p, 7500), "budget_6000_ask_4096": sizing.output_budget(p, 6000, 4096),
           "big_fits_100k": sizing.fits(q, 100_000), "big_fits_130k": sizing.fits(q, 130_000),
           "lane_ctx": sizing.lane_context_length([p, q]), "reported_small_lane": sizing.reported_context_length([p])}
    want = {"fits_6000": True, "fits_7500": False, "budget_6000_ask_4096": 2000, "big_fits_100k": True, "big_fits_130k": False,
            "lane_ctx": 120000, "reported_small_lane": 64000}
    return got == want, f"{got}"


def sizing_summary(a):
    body = {"messages": [{"role": "user", "content": sizing.SUMMARY_PREFIX + " for this chat..."}]}
    not_summary = dict(body, tools=[{"type": "function", "function": {"name": "x"}}])
    cut = sizing.summary_ok(reply("x" * 500, "length"))
    empty = sizing.summary_ok(reply("short", "stop"))
    good = sizing.summary_ok(reply("y" * 500, "stop"))
    broken = sizing.summary_ok({"nope": 1})
    ok = sizing.is_summary(body) and not sizing.is_summary(not_summary) and cut == (False, "truncated") and empty == (False, "empty") \
        and good == (True, "") and broken == (False, "no choices")
    return ok, f"summary detect ok={sizing.is_summary(body)}, with tools={sizing.is_summary(not_summary)}; cut={cut} empty={empty} good={good} broken={broken}"


# ---------------------------------------------------------------- llm_proxy.py with mock upstreams

def proxy_primary_success(a):
    with env() as e:
        up = e.mock("groq", [{"ok": reply("relayed text")}])
        kf = e.key("groq")
        tok = e.proxy([P("groq", "private", up.url, kf, default_model="openai/gpt-oss-120b")]).issue("hermes")
        t0 = time.perf_counter()
        code, h, body = e.post(tok, msg())
        dt = (time.perf_counter() - t0) * 1000
        sent = up.posts()[-1]
        ok = (code == 200 and json.loads(body) == reply("relayed text") and sent[3] == "Bearer " + e.keys["groq"]
              and sent[2]["model"] == "openai/gpt-oss-120b" and e.keys["groq"] not in e.audit_text() and tok not in e.audit_text()
              and dt < a.get("max_ms", 500))
        return ok, f"HTTP {code} in {dt:.0f} ms; upstream model {sent[2]['model']}; key added by proxy={sent[3] == 'Bearer ' + e.keys['groq']}"


def proxy_429_fallback_and_rest(a):
    """Primary says 429 with Retry-After: the same-lane fallback answers, and the primary is not asked again until its rest ends."""
    with env() as e:
        lim = e.mock("groq", [{"status": 429, "body": b'{"error":{"message":"rate limit reached"}}', "headers": {"Retry-After": "20"}},
                              {"ok": reply("groq back")}])
        fb = e.mock("cerebras", [{"ok": reply("fallback")}])
        tok = e.proxy([P("groq", "private", lim.url, priority=1), P("cerebras", "private", fb.url, priority=2)]).issue("hermes")
        c1, _, b1 = e.post(tok, msg())
        c2, _, b2 = e.post(tok, msg("again"))
        asked_during_rest = len(lim.posts())
        e.now[0] += 21
        c3, _, b3 = e.post(tok, msg("later"))
        ok = (c1 == c2 == c3 == 200 and b"fallback" in b1 and b"fallback" in b2 and asked_during_rest == 1
              and b"groq back" in b3 and len(lim.posts()) == 2)
        return ok, f"1st {c1} {json.loads(b1)['choices'][0]['message']['content']!r}; primary asked {asked_during_rest}x during its 20 s rest; after rest: {json.loads(b3)['choices'][0]['message']['content']!r}"


def proxy_all_rate_limited(a):
    with env() as e:
        lim = e.mock("groq", [{"status": 429, "body": b'{"error":{"message":"slow down"}}', "headers": {"Retry-After": "45"}}])
        tok = e.proxy([P("groq", "public", lim.url)]).issue("lab", "public")
        c1, h1, b1 = e.post(tok, msg())
        c2, h2, b2 = e.post(tok, msg("again"))
        ok = c1 == 429 and c2 == 429 and h1.get("Retry-After") and int(h2.get("Retry-After", 0)) <= 45 and len(lim.posts()) == 1
        return ok, f"{c1} Retry-After {h1.get('Retry-After')}; 2nd {c2} Retry-After {h2.get('Retry-After')} without asking upstream (asked {len(lim.posts())}x)"


def proxy_timeout_fallback(a):
    with env() as e:
        slow = e.mock("slow", [{"sleep": 3, "ok": REPLY}])
        fb = e.mock("fb", [{"ok": reply("fallback ok")}])
        tok = e.proxy([P("slow", "private", slow.url, priority=1, timeout_s=1), P("fb", "private", fb.url, priority=2)]).issue("hermes")
        t0 = time.perf_counter()
        c, _, b = e.post(tok, msg())
        dt = time.perf_counter() - t0
        fails = e.audit("agent_model_call_failed")
        ok = c == 200 and b"fallback ok" in b and dt < a.get("max_s", 2.5) and any(f["provider"] == "slow" for f in fails)
        return ok, f"HTTP {c} in {dt:.2f}s (limit {a.get('max_s', 2.5)}s); failures audited {[(f['provider'], f['error']) for f in fails]}"


def proxy_malformed_summary(a):
    """A context-compression (summary) call: the primary answers 200 with invalid JSON; the proxy must not pass it on, the
    next provider's complete summary is used."""
    with env() as e:
        bad = e.mock("bad", [{"raw": b"<html>502 Bad gateway</html>{not json"}])
        good = e.mock("good", [{"ok": reply("S" * 400)}])
        tok = e.proxy([P("bad", "private", bad.url, priority=1), P("good", "private", good.url, priority=2)]).issue("hermes")
        c, _, b = e.post(tok, {"messages": [{"role": "user", "content": sizing.SUMMARY_PREFIX + ". Summarise: ..."}]})
        try:
            content = json.loads(b)["choices"][0]["message"]["content"]
        except Exception:  # noqa: BLE001
            content = None
        ok = c == 200 and content == "S" * 400 and len(bad.posts()) == 1
        return ok, f"HTTP {c}; summary from fallback={content == 'S' * 400}; bad provider asked {len(bad.posts())}x"


def proxy_malformed_plain_then_healthy(a):
    """A normal call whose provider returns invalid JSON: the proxy relays bytes (it never parses normal replies) without
    crashing, and the next request is served normally."""
    with env() as e:
        up = e.mock("up", [{"raw": b"{broken json"}, {"ok": reply("fine")}])
        tok = e.proxy([P("up", "private", up.url)]).issue("hermes")
        c1, _, b1 = e.post(tok, msg())
        c2, _, b2 = e.post(tok, msg("next"))
        ok = c1 == 200 and b1 == b"{broken json" and c2 == 200 and b"fine" in b2
        return ok, f"1st HTTP {c1} body {b1[:20]!r}; 2nd HTTP {c2}"


def proxy_401_fallback_no_key_leak(a):
    """The primary rejects its key (401) and its error page echoes the Authorization header; the fallback answers. With only
    the bad provider, the agent gets 502 naming the HTTP class. Neither reply nor audit contains any configured key."""
    with env() as e:
        k1, k2 = e.key("groq"), e.key("cerebras")
        bad = e.mock("groq", [{"echo_auth": 401}])
        good = e.mock("cerebras", [{"ok": reply("ok via fallback")}])
        tok = e.proxy([P("groq", "private", bad.url, k1, priority=1), P("cerebras", "private", good.url, k2, priority=2)]).issue("hermes")
        c1, _, b1 = e.post(tok, msg())
        e.servers[-1].shutdown()
        tok2 = e.proxy([P("groq", "private", bad.url, k1)]).issue("hermes2")
        c2, h2, b2 = e.post(tok2, msg())
        everything = (b1 + b2).decode("utf-8", "replace") + json.dumps(h2) + e.audit_text()
        leaked = [n for n, v in e.keys.items() if v in everything]
        ok = c1 == 200 and b"ok via fallback" in b1 and c2 == 502 and b"401" in b2 and not leaked and len(bad.posts()) == 2
        return ok, f"with fallback HTTP {c1}; alone HTTP {c2} {b2[:80]!r}; keys leaked: {leaked or 'none'}"


def proxy_keys_never_leak_anywhere(a):
    """Three providers with three distinct synthetic keys across success, 429, 500, timeout and stream: no key appears in any
    reply, header, error or audit row; each provider received only its own key."""
    with env() as e:
        names = ["p1", "p2", "p3"]
        kf = {n: e.key(n) for n in names}
        m1 = e.mock("p1", [{"status": 429, "body": b"{}"}])
        m2 = e.mock("p2", [{"status": 500, "body": b'{"error":"boom"}'}])
        m3 = e.mock("p3", [{"ok": reply("three")}, {"stream": [b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n', b"data: [DONE]\n\n"]}])
        tok = e.proxy([P(n, "private", m.url, kf[n], priority=i) for i, (n, m) in enumerate(zip(names, (m1, m2, m3)))]).issue("hermes")
        outs = [e.post(tok, msg()), e.post(tok, dict(msg(), stream=True))]
        e.now[0] += 3600
        m3.steps = [{"status": 503}]
        m3.n = 0
        outs.append(e.post(tok, msg("all down")))
        blob = "".join(o[2].decode("utf-8", "replace") + json.dumps(o[1]) for o in outs) + e.audit_text()
        leaked = [n for n, v in e.keys.items() if v in blob]
        own = all(s[3] == "Bearer " + e.keys[m.name] for m in (m1, m2, m3) for s in m.posts())
        ok = not leaked and own and outs[0][0] == 200 and outs[1][0] == 200 and outs[2][0] in (502, 503)
        return ok, f"statuses {[o[0] for o in outs]}; keys leaked: {leaked or 'none'}; each provider got only its own key: {own}"


def proxy_down_then_recovery(a):
    """A provider that is unreachable: the fallback answers, the down one rests 30 s (not asked), and once it is back and
    the rest is over it is used again first."""
    with env() as e:
        # reserve a port, keep it closed for now
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        url = f"http://127.0.0.1:{port}/v1"
        fb = e.mock("fb", [{"ok": reply("fallback")}])
        tok = e.proxy([P("main", "private", url, priority=1), P("fb", "private", fb.url, priority=2)]).issue("hermes")
        c1, _, b1 = e.post(tok, msg())
        # bring the provider up on that port
        main2 = Mock("main", [{"ok": reply("main is back")}], port=port)
        e.mocks.append(main2)
        c2, _, b2 = e.post(tok, msg("during rest"))
        asked_during_rest = len(main2.posts())
        e.now[0] += 31
        c3, _, b3 = e.post(tok, msg("after rest"))
        ok = c1 == 200 and b"fallback" in b1 and b"fallback" in b2 and asked_during_rest == 0 and c3 == 200 and b"main is back" in b3
        return ok, f"down: {json.loads(b1)['choices'][0]['message']['content']!r}; during rest main asked {asked_during_rest}x; after rest: {b3[:120]!r}"


def proxy_5xx_retry_same_once(a):
    with env() as e:
        flaky = e.mock("flaky", [{"status": 500, "body": b'{"error":"error parsing tool call"}'}, {"ok": reply("second sample")}])
        other = e.mock("other")
        tok = e.proxy([P("flaky", "private", flaky.url, priority=1), P("other", "private", other.url, priority=2)]).issue("hermes")
        c, _, b = e.post(tok, msg())
        ok = c == 200 and b"second sample" in b and len(flaky.posts()) == 2 and not other.posts()
        return ok, f"HTTP {c}; flaky asked {len(flaky.posts())}x, other {len(other.posts())}x"


def proxy_context_too_large_fast(a):
    """A request larger than every provider in the lane: one fast 413 context_length_exceeded, no provider called."""
    with env() as e:
        m1 = e.mock("small"); m2 = e.mock("mid")
        tok = e.proxy([P("small", "private", m1.url, max_total_tokens=8000), P("mid", "private", m2.url, max_input_tokens=32000)]).issue("hermes")
        t0 = time.perf_counter()
        c, _, b = e.post(tok, msg("word " * 40000))
        dt = (time.perf_counter() - t0) * 1000
        err = json.loads(b).get("error", {})
        ok = c == 413 and err.get("code") == "context_length_exceeded" and not m1.posts() and not m2.posts() and dt < a.get("max_ms", 1000)
        return ok, f"HTTP {c} {err.get('code')} in {dt:.0f} ms (limit {a.get('max_ms', 1000)}); providers called {len(m1.posts()) + len(m2.posts())}"


def proxy_small_provider_skipped(a):
    with env() as e:
        small = e.mock("small"); big = e.mock("big", [{"ok": reply("big took it")}])
        tok = e.proxy([P("small", "private", small.url, priority=1, max_total_tokens=8000),
                       P("big", "private", big.url, priority=2, max_input_tokens=128000)]).issue("hermes")
        c, _, b = e.post(tok, msg("word " * 12000))
        skipped = e.audit("agent_model_skipped")
        ok = c == 200 and b"big took it" in b and not small.posts() and skipped and "small" in skipped[-1]["providers"]
        return ok, f"HTTP {c}; small asked {len(small.posts())}x; skip audited {skipped[-1] if skipped else None}"


def proxy_upstream_size_refusals_413(a):
    """Every provider refuses for size (413 / 400 'maximum context length'): the agent gets 413 to compress, not a 502."""
    with env() as e:
        m1 = e.mock("a", [{"status": 413, "body": b'{"error":{"message":"Request too large for model"}}'}])
        m2 = e.mock("b", [{"status": 400, "body": b'{"error":{"message":"This model\'s maximum context length is 8192 tokens"}}'}])
        tok = e.proxy([P("a", "private", m1.url, priority=1), P("b", "private", m2.url, priority=2)]).issue("hermes")
        c, _, b = e.post(tok, msg())
        ok = c == 413 and b"context_length_exceeded" in b and len(m1.posts()) == 1 and len(m2.posts()) == 1
        return ok, f"HTTP {c}; a asked {len(m1.posts())}x, b {len(m2.posts())}x (no same-provider retry on a size refusal)"


def proxy_client_disconnect(a):
    """The agent disconnects in the middle of a slow stream: the proxy stops reading from the provider (the upstream's
    write fails soon after), stays up, and serves the next request."""
    with env() as e:
        chunks = [b'data: {"choices":[{"delta":{"content":"tok"}}]}\n\n' + b" " * 4096] * 400
        up = e.mock("slowstream", [{"stream": chunks, "gap": 0.02}, {"ok": reply("next one")}])
        tok = e.proxy([P("slowstream", "private", up.url)]).issue("hermes")
        host, port = e.url.split("//")[1].split(":")
        body = json.dumps(dict(msg(), stream=True)).encode()
        s = socket.create_connection((host, int(port)), timeout=5)
        s.sendall(b"POST /v1/chat/completions HTTP/1.0\r\nHost: x\r\nContent-Type: application/json\r\nAuthorization: Bearer "
                  + tok.encode() + b"\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
        first = s.recv(2048)
        t0 = time.perf_counter()
        s.close()
        stopped = up.write_failed.wait(a.get("max_s", 6))
        dt = time.perf_counter() - t0
        c, _, b = e.post(tok, msg("after disconnect"))
        ok = first.startswith(b"HTTP/1.0 200") and stopped and c == 200 and b"next one" in b
        return ok, (f"stream started={first[:15]!r}; upstream stopped {('after %.2fs' % dt) if stopped else 'NOT within %ss' % a.get('max_s', 6)}; "
                    f"next request HTTP {c}")


def proxy_lane_binding_dahl(a):
    """The owner's private token never reaches the public provider (Dahl), even when every private provider is down; a
    public test token reaches Dahl only, and its secrets are scrubbed first."""
    with env() as e:
        dahl = e.mock("dahl", [{"ok": reply("public answer")}])
        priv = e.mock("groq", [{"status": 503}])
        toks = e.proxy([P("dahl", "public", dahl.url, priority=0), P("groq", "private", priv.url, priority=1)])
        owner, lab = toks.issue("hermes", "private"), toks.issue("lab", "public")
        private_text = "Owner's bank note: account 4111 1111 1111 1111, API_KEY=SYNTHsecret12345"  # gitleaks:allow (synthetic test value)
        c1, _, b1 = e.post(owner, msg(private_text))
        dahl_after_owner = len(dahl.posts())
        c2, _, b2 = e.post(lab, msg("public question, token ghp_" + "Z" * 30))
        sent = json.dumps([s[2] for s in dahl.posts()])
        ok = c1 in (502, 503) and dahl_after_owner == 0 and c2 == 200 and "ghp_ZZZZ" not in sent and "SYNTHsecret" not in sent \
            and "4111" not in sent
        return ok, f"owner token: HTTP {c1}, Dahl asked {dahl_after_owner}x; lab token: HTTP {c2}, token reached Dahl={'ghp_ZZZZ' in sent}"


def proxy_prefer_stays_in_lane(a):
    with env() as e:
        dahl = e.mock("dahl"); groq = e.mock("groq"); cer = e.mock("cerebras", [{"ok": reply("preferred one")}])
        toks = e.proxy([P("dahl", "public", dahl.url), P("groq", "private", groq.url, priority=1), P("cerebras", "private", cer.url, priority=5)])
        owner, lab = toks.issue("hermes", "private"), toks.issue("lab", "public")
        act = lambda t, target: e.post(t, {"tool": "models", "op": "prefer", "target": target}, path="/v1/jarvis/actions")
        r_cross = act(lab, "groq")
        r_cross2 = act(owner, "dahl")
        r_ok = act(owner, "cerebras")
        c, _, b = e.post(owner, msg())
        ok = r_cross[0] == 400 and r_cross2[0] == 400 and r_ok[0] == 200 and b"preferred one" in b and not groq.posts() and not dahl.posts()
        return ok, f"lab->groq {r_cross[0]}; owner->dahl {r_cross2[0]}; owner->cerebras {r_ok[0]}; next call served by cerebras={b'preferred one' in b}"


def proxy_non_agent_tokens(a):
    """Dashboard and owner-console tokens cannot make model calls; a bad token is 401; no provider is asked."""
    with env() as e:
        up = e.mock("up")
        toks = e.proxy([P("up", "private", up.url)])
        codes = [e.post(toks.issue("hub", "dashboard"), msg())[0], e.post(toks.issue("me", "owner"), msg())[0],
                 e.post("not-a-token", msg())[0], e.post(toks.issue("run", "runner"), msg())[0]]
        ok = codes[:3] == [403, 403, 401] and codes[3] in (403, 404) and not up.posts()
        return ok, f"dashboard/owner/bad/runner -> {codes}; provider asked {len(up.posts())}x"


def proxy_rate_limit_and_kill(a):
    from jarvis_kernel.llm_proxy import PER_MINUTE
    with env() as e:
        up = e.mock("up")
        tok = e.proxy([P("up", "private", up.url)]).issue("hermes")
        codes = [e.post(tok, msg(str(i)))[0] for i in range(PER_MINUTE + 1)]
        e.now[0] += 61
        after = e.post(tok, msg("later"))[0]
        e.k.kill("bench")
        n_before = len(up.posts())
        killed = e.post(tok, msg("while killed"))[0]
        ok = codes[:-1] == [200] * PER_MINUTE and codes[-1] == 429 and after == 200 and killed == 503 and len(up.posts()) == n_before
        return ok, f"{PER_MINUTE} ok then {codes[-1]}; after a minute {after}; kill switch {killed}, provider asked while killed: {len(up.posts()) - n_before}"


def proxy_models_context(a):
    with env() as e:
        up = e.mock("up")
        toks = e.proxy([P("small", "private", up.url, max_total_tokens=8000), P("big", "private", up.url, max_input_tokens=131072),
                        P("pubsmall", "public", up.url, max_total_tokens=8000)])
        c1, b1 = e.get(toks.issue("hermes"), "/v1/models")
        c2, b2 = e.get(toks.issue("lab", "public"), "/v1/models")
        p1, p2 = json.loads(b1)["data"][0].get("context_length"), json.loads(b2)["data"][0].get("context_length")
        ok = c1 == c2 == 200 and p1 == 131072 and p2 == 64000
        return ok, f"private lane context {p1}; public lane context {p2} (raised to the agent's 64K minimum)"


def proxy_long_history_latency(a):
    """A 40-message agent conversation (a normal long session) through the proxy to an instant mock: the proxy's own
    overhead (scrubbing, sizing, audit) stays under the limit."""
    with env() as e:
        up = e.mock("up")
        tok = e.proxy([P("up", "private", up.url)]).issue("hermes")
        body = {"messages": [{"role": "user" if i % 2 == 0 else "assistant", "content": f"message {i} about the trip plan and budget"}
                             for i in range(a.get("n", 40))]}
        t0 = time.perf_counter()
        c, _, _ = e.post(tok, body, timeout=120)
        dt = (time.perf_counter() - t0) * 1000
        return c == 200 and dt < a["max_ms"], f"HTTP {c}; {a.get('n', 40)} messages in {dt:.0f} ms (limit {a['max_ms']} ms)"


def proxy_concurrent_load(a):
    """20 parallel agent calls (two agents) through the proxy to a mock with 50 ms latency: all answered, p95 under the limit."""
    with env() as e:
        up = e.mock("up", [{"sleep": 0.05, "ok": REPLY}])
        toks = e.proxy([P("up", "private", up.url)])
        t1, t2 = toks.issue("a1"), toks.issue("a2")
        lat, codes = [], []

        def one(i):
            t0 = time.perf_counter()
            c, _, _ = e.post(t1 if i % 2 else t2, msg(f"q{i}"))
            lat.append((time.perf_counter() - t0) * 1000)
            codes.append(c)
        th = [threading.Thread(target=one, args=(i,)) for i in range(20)]
        [t.start() for t in th]
        [t.join(30) for t in th]
        lat.sort()
        p95 = lat[int(len(lat) * 0.95) - 1] if lat else 1e9
        ok = codes.count(200) == 20 and p95 < a.get("p95_ms", 1500)
        return ok, f"{codes.count(200)}/20 ok; p95 {p95:.0f} ms (limit {a.get('p95_ms', 1500)})"


# ---------------------------------------------------------------- local_team (routing, memory guard, adviser)

def _lt():
    from plugin.plugins.local_team import core
    return core


class FakePost:
    """The model boundary for local_team: records every call; answers like llama-swap / the Jev service."""

    def __init__(self, jev=None, router="general", answer="local answer", jev_depth=None):
        self.calls, self.jev, self.router, self.answer, self.jev_depth = [], jev, router, answer, jev_depth

    def __call__(self, url, body, headers=None, timeout=0):
        core = _lt()
        self.calls.append((url, body))
        if url == core.JEV_URL:
            q = body["questions"]["q"]
            pick = self.jev_depth if "reasoning" in q["instructions"] else self.jev
            if pick is None:
                raise core.TeamError("no answer from jev")
            ch, pr = pick
            return {"answers": {"q": {"choice": ch, "probabilities": {ch: pr}}}}
        if body.get("grammar"):
            return {"choices": [{"message": {"content": self.router}}]}
        return {"choices": [{"message": {"content": self.answer}}], "timings": {"predicted_per_second": 12.0}}


def team_route(a):
    core = _lt()
    out = []
    for task, jev, router, want in a["cases"]:
        fp = FakePost(jev=tuple(jev) if jev else None, router=router)
        got = core.route(task, fp)
        out.append((task[:30], got[0], got[1].split(" ")[0], want))
    bad = [o for o in out if o[1] != o[3][0] or o[2] != o[3][1]]
    return not bad, "; ".join(f"{t!r}->{s} by {h}" for t, s, h, _ in out) + (f" | WRONG: {bad}" if bad else "")


def team_memory_guard(a):
    core = _lt()
    old = os.environ.pop("NEKO_MODEL_RAM_GB_GENERAL", None)
    try:
        r1 = core.memory_ok("general", free_gb=6.0)                 # 6 - 5.5 < 1.5 reserve -> refuse
        r2 = core.memory_ok("code", free_gb=6.0)                    # 6 - 1.8 >= 1.5 -> ok
        r3 = core.memory_ok("general", free_gb=2.0, loaded={"general"})   # already loaded: costs nothing
        os.environ["NEKO_MODEL_RAM_GB_GENERAL"] = "2"
        r4 = core.memory_ok("general", free_gb=6.0)                 # owner override
    finally:
        os.environ.pop("NEKO_MODEL_RAM_GB_GENERAL", None)
        if old is not None:
            os.environ["NEKO_MODEL_RAM_GB_GENERAL"] = old
    ok = (not r1[0]) and "5.5 GB" in r1[1] and r2[0] and r3[0] and r4[0]
    return ok, f"general@6GB={r1[0]} ({r1[1][:60]}...); code@6GB={r2[0]}; loaded={r3[0]}; override={r4[0]}"


def team_ask_local_refuses_low_memory(a):
    """ask_local checks the memory guard before calling the model: with too little free RAM nothing is sent."""
    core = _lt()
    import psutil
    fp = FakePost()
    real = psutil.virtual_memory
    orig_loaded = core.loaded_models
    try:
        psutil.virtual_memory = lambda: types.SimpleNamespace(available=int(3.0 * 2**30))
        core.loaded_models = lambda get=None: set()
        try:
            core.ask_local("write a polite note to my landlord", "general", post=fp)
            err = None
        except core.TeamError as e:
            err = str(e)
    finally:
        psutil.virtual_memory = real
        core.loaded_models = orig_loaded
    ok = err is not None and "Not enough free memory" in err and not fp.calls
    return ok, f"error={err[:80] if err else None!r}; model calls {len(fp.calls)}"


def team_ask_local_limits(a):
    core = _lt()
    fp = FakePost()
    orig = core.loaded_models
    core.loaded_models = lambda get=None: {"general", "code", "math"}
    errs = []
    try:
        for task, spec in (("", "auto"), ("x" * (core.MAX_TASK + 1), "auto"), ("hello", "lawyer")):
            try:
                core.ask_local(task, spec, post=fp)
                errs.append(None)
            except core.TeamError as e:
                errs.append(str(e)[:50])
        fp2 = FakePost(answer="<think>long hidden reasoning</think>Final: 42")
        res = core.ask_local("what is 6*7", "math", post=fp2)
    finally:
        core.loaded_models = orig
    ok = all(errs) and not fp.calls and res["answer"] == "Final: 42" and res["left_this_pc"] is False
    return ok, f"refusals {errs}; reasoning stripped -> {res['answer']!r}; calls for refused tasks {len(fp.calls)}"


def team_adviser_needs_gateway(a):
    core = _lt()
    sent = []
    stream = lambda url, body, headers=None, timeout=0: sent.append(url) or "1. plan"
    refusals = []
    for url in ("https://inference.dahl.global/v1", "http://127.0.0.1:8080/v1", "", "http://evil.example/127.0.0.1:4895/v1"):
        try:
            core.advise("my password is hunter22", url, "minimax", "SYNTH-KEY", stream=stream)
            refusals.append(False)
        except core.TeamError:
            refusals.append(True)
    via_gw = core.advise("task", "http://127.0.0.1:48950/v1", "minimax", "", stream=stream)
    ok = all(refusals) and len(sent) == 1 and sent[0].startswith("http://127.0.0.1:48950/") and via_gw["plan"] == "1. plan"
    return ok, f"refused direct/other URLs: {refusals}; requests sent {len(sent)} ({sent})"


def team_solve_adviser_policy(a):
    """solve(): 'never' skips the adviser; 'auto' asks Jev and skips the adviser for a direct task; Jev down -> adviser asked;
    an adviser failure falls back to the local model with a note."""
    core = _lt()
    orig = core.loaded_models
    core.loaded_models = lambda get=None: {"general", "code", "math"}
    calls = []

    def adviser(task):
        calls.append(task)
        return {"plan": "1. step", "hidden_from_adviser": {"PASSWORD": 1}}

    def broken(task):
        calls.append("broken")
        raise core.TeamError("adviser timed out")
    try:
        r_never = core.solve("plan my week of meetings", adviser=adviser, post=FakePost(), use_adviser="never")
        n_never = len(calls)
        r_direct = core.solve("plan my week of meetings", adviser=adviser, post=FakePost(jev_depth=("direct", 0.9)), use_adviser="auto")
        n_direct = len(calls)
        r_down = core.solve("plan my week of meetings", adviser=adviser, post=FakePost(jev_depth=None), use_adviser="auto")
        n_down = len(calls)
        r_broken = core.solve("plan my week of meetings", adviser=broken, post=FakePost(), use_adviser="always")
    finally:
        core.loaded_models = orig
    ok = (n_never == 0 and not r_never["adviser_used"] and n_direct == 0 and not r_direct["adviser_used"] and n_down == 1
          and r_down["adviser_used"] and not r_broken["adviser_used"] and "adviser skipped" in r_broken["note"] and r_broken["answer"])
    return ok, (f"never: adviser calls {n_never}; auto+direct: {n_direct - n_never}; auto+jev down: {n_down - n_direct}; "
                f"broken adviser -> {r_broken['note']!r}")


# ---------------------------------------------------------------- privacy gateway (jarvis/privacy/gateway.py)

_PG = {}


def _privacy_modules():
    """Import the real privacy gateway. Presidio is not installed in the Linux image: stub its import (only the detector
    class uses it; we never construct that class) and drop the stubs from sys.modules afterwards."""
    if _PG:
        return _PG["gw"], _PG["red"]
    pdir = str(ROOT / "jarvis" / "privacy")
    stubs = {}
    try:
        import presidio_analyzer  # noqa: F401
    except ImportError:
        pa = types.ModuleType("presidio_analyzer")
        for n in ("AnalyzerEngine", "Pattern", "PatternRecognizer", "RecognizerResult"):
            setattr(pa, n, type(n, (), {"__init__": lambda self, *a, **k: None}))
        ne = types.ModuleType("presidio_analyzer.nlp_engine")
        ne.NlpEngineProvider = type("NlpEngineProvider", (), {})
        stubs = {"presidio_analyzer": pa, "presidio_analyzer.nlp_engine": ne}
        sys.modules.update(stubs)
    sys.path.insert(0, pdir)
    try:
        import importlib
        red = importlib.import_module("redactor")
        gw = importlib.import_module("gateway")
    finally:
        sys.path.remove(pdir)
        for n in stubs:
            sys.modules.pop(n, None)
    # these two generic names must not shadow anything else in the bench process
    sys.modules.pop("redactor", None)
    sys.modules.pop("gateway", None)
    _PG.update(gw=gw, red=red)
    return gw, red


class CueRedactor:
    """Model-boundary stand-in for the Presidio detector: redactor.py's own cue-word pattern ("password is X") + real Mapping."""

    def __init__(self, red):
        self.rx = re.compile(red._SECRET_AFTER_CUE)
        self.model, self.stats = None, {}

    def redact(self, text, mapping, deep=True):
        def sub(m):
            v = m.group("v")
            return m.group(0)[: m.start("v") - m.start(0)] + mapping.tag("PASSWORD", v)
        return self.rx.sub(sub, text)


@contextlib.contextmanager
def privacy_gateway(upstreams, cooldown=60.0, secret_root=None, timeout=5):
    gw, red = _privacy_modules()
    gw._cooldown.clear()
    gw._keys.clear()
    old_root = gw.SECRET_ROOT
    if secret_root:
        gw.SECRET_ROOT = secret_root
    H = gw.make_handler(CueRedactor(red), upstreams, timeout=timeout, cooldown=cooldown)
    H.log_message = lambda *a, **k: None
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    s.daemon_threads = True
    threading.Thread(target=s.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{s.server_address[1]}/v1"
    finally:
        s.shutdown()
        s.server_close()
        gw.SECRET_ROOT = old_root


def privacy_placeholders_roundtrip(a):
    """local_team.advise through the real privacy gateway to a mock Dahl: Dahl sees [PASSWORD_1], never the password; the
    plan that comes back has the real value restored; the caller's key is passed through, never stored."""
    core = _lt()
    pw = "Sunny#Day2024x"
    dahl = Mock("dahl", [{"stream": [
        b'data: {"choices":[{"index":0,"delta":{"content":"1. Log in with [PASS"}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"content":"WORD_1] then"}}]}\n\n',
        b'data: {"choices":[{"index":0,"delta":{"content":" change it."},"finish_reason":"stop"}]}\n\n', b"data: [DONE]\n\n"]}])
    try:
        with privacy_gateway(dahl.url) as gurl:
            real_port = gurl.split("//")[1].split("/")[0]

            def stream(url, body, headers=None, timeout=0):                     # the gateway's real address, same path
                return core.post_stream(url.replace("127.0.0.1:48950", real_port), body, headers, timeout=10)
            res = core.advise(f"Change my bank login. My password is {pw} and I keep forgetting it.",
                              "http://127.0.0.1:48950/v1", "minimax", "SYNTH-DAHL-KEY", stream=stream)
        sent = json.dumps(dahl.posts()[0][2])
        auth = dahl.posts()[0][3]
    finally:
        dahl.close()
    ok = pw not in sent and "[PASSWORD_1]" in sent and pw in res["plan"] and "[PASSWORD_1]" not in res["plan"] and auth == "Bearer SYNTH-DAHL-KEY"
    return ok, f"Dahl saw password={pw in sent}, placeholder={'[PASSWORD_1]' in sent}; plan restored={pw in res['plan']}: {res['plan'][:70]!r}"


def privacy_nonstream_restore_and_tools(a):
    """Non-streamed reply: placeholders restored in content and tool-call arguments; same value -> same placeholder."""
    def answer(body):
        txt = json.dumps(body["messages"])
        tags = re.findall(r"\[PASSWORD_\d+\]", txt)
        r = reply(f"use {tags[0] if tags else 'NONE'} twice: {tags[0] if tags else ''}")
        r["choices"][0]["message"]["tool_calls"] = [{"id": "c", "type": "function",
                                                     "function": {"name": "login", "arguments": json.dumps({"pw": tags[0] if tags else ""})}}]
        return r
    dahl = Mock("dahl", [{"ok": answer}])
    try:
        with privacy_gateway(dahl.url) as gurl:
            req = urllib.request.Request(gurl + "/chat/completions", method="POST", data=json.dumps({"model": "m", "messages": [
                {"role": "user", "content": "password is Tr0ub4dor&3 and again password: Tr0ub4dor&3"}]}).encode(),
                headers={"Content-Type": "application/json", "Authorization": "Bearer SYNTH"})
            out = json.loads(urllib.request.urlopen(req, timeout=10).read())
        sent = json.dumps(dahl.posts()[0][2])
    finally:
        dahl.close()
    content = out["choices"][0]["message"]["content"]
    args = out["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    ok = "Tr0ub4dor" not in sent and sent.count("[PASSWORD_1]") == 2 and "[PASSWORD_2]" not in sent \
        and content.count("Tr0ub4dor&3") == 2 and "Tr0ub4dor&3" in json.loads(args)["pw"]
    return ok, f"sent placeholders: {re.findall(r'PASSWORD_[0-9]', sent)}; restored content {content!r}; tool args {args!r}"


def privacy_fallback_own_key(a):
    """Dahl answers 429: the gateway falls back to the next provider with that provider's own key from the secrets folder
    (the caller's Dahl key is not sent there). By design the first provider (the caller's own key and model) is tried on
    every call; the cooldown applies to the fallbacks."""
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "groq.key"), "w").write("SYNTH-GROQ-FALLBACK-KEY")
        dahl = Mock("dahl", [{"status": 429, "body": b'{"error":"busy"}'}])
        groq = Mock("groq", [{"ok": reply("from fallback")}])
        try:
            provs = [{"name": "dahl", "base_url": dahl.url, "key": "from_request"},
                     {"name": "groq", "base_url": groq.url, "key_file": "groq.key", "model": "llama-fallback"}]
            with privacy_gateway(provs, secret_root=d) as gurl:
                def call():
                    req = urllib.request.Request(gurl + "/chat/completions", method="POST", data=json.dumps(
                        {"model": "minimax", "messages": [{"role": "user", "content": "password is Abc!2345q"}]}).encode(),
                        headers={"Content-Type": "application/json", "Authorization": "Bearer SYNTH-CALLER-DAHL-KEY"})
                    return json.loads(urllib.request.urlopen(req, timeout=10).read())
                o1 = call()
                o2 = call()
            g = groq.posts()
            ok = (o1["choices"][0]["message"]["content"] == "from fallback" and g[0][3] == "Bearer SYNTH-GROQ-FALLBACK-KEY"
                  and g[0][2]["model"] == "llama-fallback" and "Abc!2345q" not in json.dumps(g[0][2])
                  and len(dahl.posts()) == 2 and len(g) == 2 and o2["choices"][0]["message"]["content"] == "from fallback")
            return ok, (f"fallback answered={o1['choices'][0]['message']['content']!r}; fallback got its own key={g[0][3] == 'Bearer SYNTH-GROQ-FALLBACK-KEY'}; "
                        f"Dahl asked {len(dahl.posts())}x, fallback {len(g)}x over 2 calls")
        finally:
            dahl.close()
            groq.close()


# ---------------------------------------------------------------- 2026-10-17: provider failure and recovery, constrained resources

def _content(b):
    try:
        return json.loads(b)["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001 - not a chat reply
        return None


def _post_safe(e, tok, body, timeout=30):
    """e.post, but a connection the proxy dropped without answering is a result ("dropped"), not a crash of the check."""
    import http.client
    try:
        return e.post(tok, body, timeout=timeout)
    except (http.client.HTTPException, ConnectionError, urllib.error.URLError, TimeoutError) as x:
        return f"dropped ({type(x).__name__})", {}, b""


def _order(*mocks):
    """Provider names in the order their POSTs arrived (several mocks, one timeline)."""
    return [m.name for m, _ in sorted(((m, s) for m in mocks for s in m.posts()), key=lambda t: t[1][4])]


def proxy_fallback_order(a):
    """Four same-lane providers fail in turn, the fifth answers: asked in plan order (priority, then local first on a tie),
    at most twice each (one same-provider retry on 5xx), and the public Dahl never."""
    with env() as e:
        dahl = e.mock("dahl", [{"ok": reply("public answer")}])
        loc = e.mock("local", [{"status": 503}])
        groq = e.mock("groq", [{"status": 503}])
        cer = e.mock("cer", [{"status": 500, "body": b'{"error":"boom"}'}])
        last = e.mock("slowcloud", [{"ok": reply("last one answered")}])
        tok = e.proxy([P("dahl", "public", dahl.url, priority=0), P("slowcloud", "private", last.url, priority=5),
                       P("cer", "private", cer.url, priority=3), P("groq", "private", groq.url, priority=1),
                       P("local", "local", loc.url, priority=1)]).issue("hermes")
        c, _, b = e.post(tok, msg())
        order = _order(dahl, loc, groq, cer, last)
        ok = c == 200 and _content(b) == "last one answered" and order == a["want"]
        return ok, f"HTTP {c} {_content(b)!r}; asked in order {order} (want {a['want']}); Dahl asked {len(dahl.posts())}x"


def proxy_outage_bounded(a):
    """Every provider of the lane answers 503 for a while: the first request tries each twice and says all failed; the next
    requests are answered at once with 503 + Retry-After and reach no provider (no retry storm); after the rest the
    providers are asked again and the lane recovers."""
    with env() as e:
        ms = [e.mock(n, [{"status": 503, "body": b'{"error":"overloaded"}'}]) for n in ("p1", "p2", "p3")]
        tok = e.proxy([P(m.name, "private", m.url, priority=i) for i, m in enumerate(ms)]).issue("hermes")
        n = int(a.get("requests", 10))
        outs = [e.post(tok, msg("same question")) for _ in range(n)]
        asked = sum(len(m.posts()) for m in ms)
        later_ok = all(o[0] == 503 and 1 <= int(o[1].get("Retry-After", 0) or 0) <= 30 and b"retry in" in o[2] for o in outs[1:])
        e.now[0] += 31
        for m in ms:
            m.steps, m.n = [{"ok": reply(f"{m.name} is back")}], 0
        c3, _, b3 = e.post(tok, msg("same question"))
        ok = (outs[0][0] == 502 and b"all providers failed" in outs[0][2] and asked == a["max_upstream"] and later_ok
              and c3 == 200 and _content(b3) == "p1 is back")
        return ok, (f"1st: HTTP {outs[0][0]} {outs[0][2][:60]!r}; upstream requests for {n} agent calls: {asked} (limit {a['max_upstream']}); "
                    f"later: {sorted({(o[0], o[1].get('Retry-After')) for o in outs[1:]})}; after the rest: HTTP {c3} {_content(b3)!r}")


def proxy_all_fail_truthful(a):
    """Three providers fail three ways (429, 500 with internal details, connection refused): the agent gets an error that
    says all failed (never a 200), without the providers' own error text; the audit records each failure class; the next
    request does not ask anyone and says when to retry."""
    with env() as e:
        p1 = e.mock("p1", [{"status": 429, "body": b'{"error":{"message":"rate limit reached"}}', "headers": {"Retry-After": "120"}}])
        p2 = e.mock("p2", [{"status": 500, "body": b'{"error":"internal: db at 10.1.2.3 refused"}'}])
        tok = e.proxy([P("p1", "private", p1.url, priority=1), P("p2", "private", p2.url, priority=2),
                       P("p3", "private", dead_url(), priority=3)]).issue("hermes")
        c1, _, b1 = e.post(tok, msg())
        n1 = len(p1.posts()) + len(p2.posts())
        c2, h2, b2 = e.post(tok, msg("again"))
        n2 = len(p1.posts()) + len(p2.posts())
        req = e.audit("agent_model_request_failed")
        classes = req[-1]["classes"] if req else None
        ok = (c1 == 502 and b"all providers failed" in b1 and b"10.1.2.3" not in b1 and classes == ["rate_limited", "other", "other"]
              and c2 in (429, 503) and 1 <= int(h2.get("Retry-After", 0) or 0) <= 120 and n2 == n1)
        return ok, f"1st HTTP {c1} {b1[:70]!r}; classes audited {classes}; 2nd HTTP {c2} Retry-After {h2.get('Retry-After')}, providers asked again: {n2 - n1}"


def _stream_req(e, tok, body):
    return urllib.request.Request(e.url + "/v1/chat/completions", data=json.dumps(dict(body, stream=True)).encode(), method="POST",
                                  headers={"Content-Type": "application/json", "Authorization": "Bearer " + tok})


def proxy_slow_stream_progressive(a):
    """A provider streams five tokens 0.5 s apart: the agent receives each as it comes (not one blob at the end), and the
    whole stream including [DONE]."""
    with env() as e:
        ch = [f'data: {{"choices":[{{"delta":{{"content":"t{i}"}}}}]}}\n\n'.encode() for i in range(5)] + [b"data: [DONE]\n\n"]
        up = e.mock("up", [{"stream": ch, "gap": a.get("gap", 0.5)}])
        tok = e.proxy([P("up", "private", up.url)]).issue("hermes")
        arrivals, got = [], b""
        with urllib.request.urlopen(_stream_req(e, tok, msg()), timeout=30) as resp:
            while True:
                c = resp.read1(65536)
                if not c:
                    break
                got += c
                arrivals.append(time.monotonic())
        spread = arrivals[-1] - arrivals[0] if arrivals else 0
        ok = all(f"t{i}".encode() in got for i in range(5)) and b"[DONE]" in got and spread >= a["min_spread_s"]
        return ok, f"{len(arrivals)} reads; first to last {spread:.2f}s (needs >= {a['min_spread_s']}s: relayed as it streams); complete={b'[DONE]' in got}"


def proxy_stalled_stream(a):
    """A provider sends one token and then stalls past its timeout_s: the proxy ends the stream within the timeout plus a
    margin, does not invent an ending ([DONE]), stays up for the next request, and records the failed call in the audit."""
    with env() as e:
        up = e.mock("up", [{"stream": [b'data: {"choices":[{"delta":{"content":"a"}}]}\n\n', b'data: {"choices":[{"delta":{"content":"b"}}]}\n\n',
                                       b"data: [DONE]\n\n"], "gap": 5}, {"ok": reply("next one")}])
        tok = e.proxy([P("up", "private", up.url, timeout_s=1)]).issue("hermes")
        got, first, end = b"", None, None
        try:
            with urllib.request.urlopen(_stream_req(e, tok, msg()), timeout=30) as resp:
                while True:
                    c = resp.read1(65536)
                    if not c:
                        break
                    got += c
                    first = first or time.monotonic()
        except Exception:  # noqa: BLE001 - a cut connection is what a stall may look like to the agent
            pass
        end = time.monotonic()
        held = (end - first) if first else None
        c2, _, b2 = e.post(tok, msg("next"))
        fails = [f for f in e.audit("agent_model_call_failed") + e.audit("agent_model_request_failed") if f.get("provider", "up") == "up"]
        ok = (first is not None and held <= 1 + a["margin_s"] and b"[DONE]" not in got and b'"b"' not in got and c2 == 200
              and _content(b2) == "next one" and bool(fails))
        return ok, (f"stream held {held if held is None else round(held, 2)}s after the first token (limit {1 + a['margin_s']}s); "
                    f"fake [DONE]={b'[DONE]' in got}; next request HTTP {c2}; failure audited: {fails or 'NO'}")


def proxy_key_withdrawn_midrequest(a):
    """While the first provider is answering (slowly, then 503), the second provider's key file is removed (the owner
    withdraws that provider). The proxy must skip it and use the third, and answer the agent; never drop the connection."""
    with env() as e:
        k1, k2, k3 = e.key("a"), e.key("b"), e.key("c")
        gone = lambda: os.path.exists(k2) and os.remove(k2)  # noqa: E731
        pa = e.mock("a", [{"hook": gone, "sleep": 0.3, "status": 503}])
        pb = e.mock("b", [{"ok": reply("from b")}])
        pc = e.mock("c", [{"ok": reply("from c")}])
        tok = e.proxy([P("a", "private", pa.url, k1, priority=1), P("b", "private", pb.url, k2, priority=2),
                       P("c", "private", pc.url, k3, priority=3)]).issue("hermes")
        c, _, b = _post_safe(e, tok, msg())
        ok = c == 200 and _content(b) == "from c" and not pb.posts()
        return ok, f"agent got {c} {_content(b)!r}; withdrawn provider asked {len(pb.posts())}x; third asked {len(pc.posts())}x"


def gateway_key_withdrawn_midrequest(a):
    """gateway.call: the second provider's key file disappears while the first is answering. The call must go on to the
    third provider (or end in RouteError), never crash with a file error."""
    with env() as e:
        k1, k2, k3 = e.key("a"), e.key("b"), e.key("c")
        gone = lambda: os.path.exists(k2) and os.remove(k2)  # noqa: E731
        pa = e.mock("a", [{"hook": gone, "sleep": 0.3, "status": 500}])
        pb = e.mock("b", [{"ok": reply("from b")}])
        pc = e.mock("c", [{"ok": reply("from c")}])
        provs = [P("a", "private", pa.url, k1, priority=1), P("b", "private", pb.url, k2, priority=2), P("c", "private", pc.url, k3, priority=3)]
        try:
            out = gateway.call(e.k, provs, "private", "summarise my notes")
        except RouteError as x:
            out = f"RouteError: {x}"
        except Exception as x:  # noqa: BLE001 - the finding: a crash instead of a fallback
            out = f"CRASH {type(x).__name__}: {str(x)[:80]}"
        ok = out == "from c" and not pb.posts()
        return ok, f"result {out!r}; withdrawn provider asked {len(pb.posts())}x; third asked {len(pc.posts())}x"


def gateway_malformed_replies(a):
    """gateway.call: a 200 with HTML, a 200 with no choices, a 200 with the wrong shape, then a good reply: each bad one is
    a failure of its provider (audited by class), the good one answers."""
    with env() as e:
        ms = [e.mock("html", [{"raw": b"<html>upstream maintenance</html>"}]), e.mock("nochoices", [{"ok": {"choices": []}}]),
              e.mock("shape", [{"ok": {"nope": 1}}]), e.mock("good", [{"ok": reply("good one")}])]
        provs = [P(m.name, "private", m.url, priority=i) for i, m in enumerate(ms)]
        try:
            out = gateway.call(e.k, provs, "private", "hi")
        except Exception as x:  # noqa: BLE001
            out = f"{type(x).__name__}: {x}"
        fails = [(f["provider"], f["error"]) for f in e.audit("model_call_failed")]
        ok = out == "good one" and fails == [tuple(x) for x in a["want_failures"]]
        return ok, f"answer {out!r}; failures audited {fails}"


def gateway_private_all_fail_never_dahl(a):
    """gateway.call for a private task: Dahl (public, first by priority, healthy) is never asked; the private providers fail
    with 429, 500 and a hang; each is asked once, the hang is cut at its timeout, and the error says all failed."""
    with env() as e:
        dahl = e.mock("dahl", [{"ok": reply("public answer")}])
        r429 = e.mock("r429", [{"status": 429, "headers": {"Retry-After": "30"}}])
        r500 = e.mock("r500", [{"status": 500}])
        hang = e.mock("hang", [{"sleep": a.get("hang_s", 8), "ok": REPLY}])
        provs = [P("dahl", "public", dahl.url, priority=0), P("r429", "private", r429.url, priority=1),
                 P("r500", "private", r500.url, priority=2), P("hang", "private", hang.url, priority=3, timeout_s=1)]
        t0 = time.perf_counter()
        try:
            gateway.call(e.k, provs, "private", "my private note")
            err = "no error"
        except RouteError as x:
            err = str(x)
        dt = time.perf_counter() - t0
        counts = [len(m.posts()) for m in (r429, r500, hang)]
        ok = err.startswith("all providers failed") and not dahl.posts() and counts == [1, 1, 1] and dt < a["max_s"]
        return ok, f"{err!r} after {dt:.1f}s (limit {a['max_s']}s); Dahl asked {len(dahl.posts())}x; private providers asked {counts}"


def proxy_public_never_private(a):
    """A public (lab) token whose only public provider, Dahl, fails: the agent gets an error; the private provider is never
    asked, then or on the next request."""
    with env() as e:
        dahl = e.mock("dahl", [{"status": 500, "body": b'{"error":"down"}'}])
        groq = e.mock("groq", [{"ok": reply("private answer")}])
        toks = e.proxy([P("dahl", "public", dahl.url, priority=5), P("groq", "private", groq.url, priority=0)])
        lab = toks.issue("lab", "public")
        c1, _, b1 = e.post(lab, msg("public question"))
        c2, _, _ = e.post(lab, msg("public question"))
        ok = c1 == 502 and c2 in (502, 503) and not groq.posts() and len(dahl.posts()) == 2
        return ok, f"HTTP {c1} then {c2}; private provider asked {len(groq.posts())}x; Dahl asked {len(dahl.posts())}x (one retry, then rested)"


def proxy_dropped_connection_fallback(a):
    """The first provider reads the request and closes the connection without answering (its process died): the next
    provider answers, the dead one is asked once (no blind retry) and the failure is audited by class."""
    with env() as e:
        d = e.mock("dies", [{"drop": True}])
        ok_m = e.mock("ok", [{"ok": reply("second provider")}])
        tok = e.proxy([P("dies", "private", d.url, priority=1), P("ok", "private", ok_m.url, priority=2)]).issue("hermes")
        c, _, b = _post_safe(e, tok, msg())
        fails = [(f["provider"], f["error"]) for f in e.audit("agent_model_call_failed")]
        ok = c == 200 and _content(b) == "second provider" and len(d.posts()) == 1 and any(p == "dies" for p, _ in fails)
        return ok, f"HTTP {c} {_content(b)!r}; dead provider asked {len(d.posts())}x; failures audited {fails}"


def proxy_truncated_summary_fallback(a):
    """A context-compression (summary) call: the first provider's summary is cut at its output cap (finish_reason
    'length'); it is not passed on, the next provider's complete summary is, and the cut is audited."""
    with env() as e:
        cut = e.mock("cut", [{"ok": reply("C" * 400, "length")}])
        full = e.mock("full", [{"ok": reply("F" * 400)}])
        tok = e.proxy([P("cut", "private", cut.url, priority=1), P("full", "private", full.url, priority=2)]).issue("hermes")
        c, _, b = e.post(tok, {"messages": [{"role": "user", "content": sizing.SUMMARY_PREFIX + ". Summarise: ..."}]})
        classes = [f.get("class") for f in e.audit("agent_model_call_failed")]
        ok = c == 200 and _content(b) == "F" * 400 and "summary_truncated" in classes and len(cut.posts()) == 1
        return ok, f"HTTP {c}; summary from the complete provider={_content(b) == 'F' * 400}; failure classes {classes}"


def _pg_call(gurl, auth="Bearer SYNTH-CALLER-DAHL-KEY", text="password is Abc!2345q, plan my week"):
    req = urllib.request.Request(gurl + "/chat/completions", method="POST", data=json.dumps(
        {"model": "minimax", "messages": [{"role": "user", "content": text}]}).encode(),
        headers={"Content-Type": "application/json", "Authorization": auth})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as x:
        return x.code, dict(x.headers or {}), x.read()


def privacy_all_fail_truthful(a):
    """Privacy gateway: Dahl answers 500 and the fallback is unreachable: the caller gets a 502 that names each provider and
    its failure class, not a success and not a provider's raw page."""
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "groq.key"), "w").write("SYNTH-GROQ-KEY")
        dahl = Mock("dahl", [{"status": 500, "body": b'{"error":"internal stack trace at /srv/app.py"}'}])
        try:
            provs = [{"name": "dahl", "base_url": dahl.url, "key": "from_request"},
                     {"name": "groq", "base_url": dead_url(), "key_file": "groq.key", "model": "m"}]
            with privacy_gateway(provs, secret_root=d) as gurl:
                c, _, b = _pg_call(gurl)
        finally:
            dahl.close()
    txt = b.decode("utf-8", "replace")
    ok = c == 502 and "dahl: HTTP 500" in txt and "groq: URLError" in txt and "/srv/app.py" not in txt
    return ok, f"HTTP {c} {txt[:160]!r}"


def privacy_fallback_key_not_echoed(a):
    """Privacy gateway: Dahl fails, the fallback rejects ITS OWN key (read from the secrets folder) with an error page that
    echoes the Authorization header. That key must not reach the caller (N.E.K.O, its logs, the screen)."""
    with tempfile.TemporaryDirectory() as d:
        fb_key = "SYNTH-GROQ-" + "FALLBACK-" + os.urandom(6).hex()
        open(os.path.join(d, "groq.key"), "w").write(fb_key)
        dahl = Mock("dahl", [{"status": 503, "body": b'{"error":"busy"}'}])
        groq = Mock("groq", [{"echo_auth": 401}])
        try:
            provs = [{"name": "dahl", "base_url": dahl.url, "key": "from_request"},
                     {"name": "groq", "base_url": groq.url, "key_file": "groq.key", "model": "m"}]
            with privacy_gateway(provs, secret_root=d) as gurl:
                c, h, b = _pg_call(gurl)
        finally:
            dahl.close()
            groq.close()
    leaked = fb_key in (b.decode("utf-8", "replace") + json.dumps(h))
    ok = c >= 400 and not leaked
    return ok, f"HTTP {c}; fallback key in the reply to the caller: {leaked}; body {b[:90]!r}"


def privacy_fallback_cooldown_bounded(a):
    """Privacy gateway, two calls: Dahl 500 (the caller's own provider, tried every call by design), the first fallback 429,
    the second fallback answers. On the second call the rate-limited fallback rests (not asked again)."""
    with tempfile.TemporaryDirectory() as d:
        for n in ("groq", "cerebras"):
            open(os.path.join(d, n + ".key"), "w").write("SYNTH-" + n.upper())
        dahl = Mock("dahl", [{"status": 500}])
        groq = Mock("groq", [{"status": 429, "body": b'{"error":"slow down"}'}])
        cer = Mock("cerebras", [{"ok": reply("from cerebras")}])
        try:
            provs = [{"name": "dahl", "base_url": dahl.url, "key": "from_request"},
                     {"name": "groq", "base_url": groq.url, "key_file": "groq.key", "model": "g"},
                     {"name": "cerebras", "base_url": cer.url, "key_file": "cerebras.key", "model": "c"}]
            with privacy_gateway(provs, secret_root=d) as gurl:
                outs = [_pg_call(gurl), _pg_call(gurl)]
            counts = {m.name: len(m.posts()) for m in (dahl, groq, cer)}
        finally:
            for m in (dahl, groq, cer):
                m.close()
    answers = [_content(o[2]) for o in outs]
    ok = answers == ["from cerebras"] * 2 and counts == a["want_counts"]
    return ok, f"answers {answers}; requests per provider over 2 calls {counts} (want {a['want_counts']})"


def privacy_timeout_fallback(a):
    """Privacy gateway: Dahl hangs past the gateway's timeout; the fallback answers within the timeout plus a margin."""
    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "groq.key"), "w").write("SYNTH-GROQ")
        dahl = Mock("dahl", [{"sleep": a.get("hang_s", 6), "ok": REPLY}])
        groq = Mock("groq", [{"ok": reply("fallback in time")}])
        try:
            provs = [{"name": "dahl", "base_url": dahl.url, "key": "from_request"},
                     {"name": "groq", "base_url": groq.url, "key_file": "groq.key", "model": "g"}]
            with privacy_gateway(provs, secret_root=d, timeout=1) as gurl:
                t0 = time.perf_counter()
                c, _, b = _pg_call(gurl)
                dt = time.perf_counter() - t0
        finally:
            dahl.close()
            groq.close()
    ok = c == 200 and _content(b) == "fallback in time" and dt < a["max_s"]
    return ok, f"HTTP {c} {_content(b)!r} after {dt:.2f}s (gateway timeout 1 s, limit {a['max_s']}s)"


# ---- local model profile (jarvis/local_models/hwdetect.py) from fake device readings

def _hwdetect(llama_path=None):
    """A fresh copy of hwdetect.py (its llama-server path is read at import time, so a fake one needs a fresh import)."""
    import importlib.util
    old = os.environ.get("NEKO_LLAMA_SERVER")
    if llama_path:
        os.environ["NEKO_LLAMA_SERVER"] = llama_path
    try:
        spec = importlib.util.spec_from_file_location("bench_hwdetect", ROOT / "jarvis" / "local_models" / "hwdetect.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if llama_path:
            if old is None:
                os.environ.pop("NEKO_LLAMA_SERVER", None)
            else:
                os.environ["NEKO_LLAMA_SERVER"] = old


def hw_profile_boundaries(a):
    """choose() at the edges of each profile: RAM in GB and the free memory (MiB) of each GPU llama.cpp reports."""
    hw = _hwdetect()
    got, bad = [], []
    for total, frees, want in a["cases"]:
        devs = [{"backend": "Vulkan", "name": f"gpu{i}", "total_mb": f + 512, "free_mb": f} for i, f in enumerate(frees)]
        p = hw.choose({"total_gb": total}, devs)[0]
        got.append(p)
        if p != want:
            bad.append(f"{total} GB + GPUs {frees} -> {p}, want {want}")
    return not bad, ("; ".join(bad) if bad else f"all {len(got)} readings -> {got}")


def hw_detect_fake_devices(a):
    """detect() end to end with a fake llama-server (its --list-devices text) and fake RAM readings: two GPUs (the one with
    more free memory counts), a hung llama-server (timeout: no GPU, no crash), and the owner override (a bad value ignored)."""
    import subprocess
    from unittest import mock
    with tempfile.TemporaryDirectory() as d:
        exe = os.path.join(d, "llama-server")
        open(exe, "w").write("fake")
        hw = _hwdetect(exe)
        seen = []

        def runner(out=None, exc=None):
            def run(cmd, **kw):
                seen.append(cmd)
                if exc:
                    raise exc
                return types.SimpleNamespace(stdout="", stderr=out)
            return run
        listing = ("ggml_vulkan: Found 2 Vulkan devices\nAvailable devices:\n"
                   "  Vulkan0: Intel(R) UHD Graphics 630 (2048 MiB, 1500 MiB free)\n  Vulkan1: AMD Radeon RX 6600 (8176 MiB, 7100 MiB free)\n")
        vm = lambda gb: types.SimpleNamespace(total=int(gb * 2**30), available=int(gb * 0.6 * 2**30))  # noqa: E731
        env_keep = os.environ.pop("NEKO_MODEL_PROFILE", None)
        try:
            with mock.patch("psutil.virtual_memory", lambda: vm(16)):
                r1 = hw.detect(runner=runner(listing))
                r2 = hw.detect(runner=runner(exc=subprocess.TimeoutExpired("llama-server", 60)))
                os.environ["NEKO_MODEL_PROFILE"] = "gpu99"
                r3 = hw.detect(runner=runner(listing))
                os.environ["NEKO_MODEL_PROFILE"] = "cpu8"
                r4 = hw.detect(runner=runner(listing))
            with mock.patch("psutil.virtual_memory", lambda: vm(4)):
                os.environ.pop("NEKO_MODEL_PROFILE", None)
                r5 = hw.detect(runner=runner(listing))
        finally:
            os.environ.pop("NEKO_MODEL_PROFILE", None)
            if env_keep is not None:
                os.environ["NEKO_MODEL_PROFILE"] = env_keep
    got = [(r["profile"], len(r["gpus"])) for r in (r1, r2, r3, r4, r5)]
    want = [tuple(x) for x in a["want"]]
    ok = got == want and "7100" in r1["why"] and "detected" in r4["why"] and len(seen) == 5
    return ok, f"profiles/GPUs seen {got} (want {want}); why: {r1['why']!r}, {r4['why']!r}; llama-server asked {len(seen)}x"


def hw_profile_configs_bounded(a, root=None):
    """Every profile the detector can choose that runs local models has its llama-swap config (vram4: llama-swap.yaml,
    others: profiles/<name>.yaml, as local-models.ps1 picks them); in each config every model is in an exclusive swap group
    (one model in memory at a time), and every server command has one request slot (-np 1) and listens on 127.0.0.1."""
    import yaml
    base = Path(root) if root else ROOT / "jarvis" / "local_models"
    hw = _hwdetect()
    bad, seen = [], {}
    for prof in hw.PROFILES:
        if prof == "remote_only":
            continue
        f = base / ("llama-swap.yaml" if prof == "vram4" else f"profiles/{prof}.yaml")
        if not f.is_file():
            bad.append(f"{prof}: no config {f.name}")
            continue
        cfg = yaml.safe_load(f.read_text(encoding="utf-8"))
        macros = cfg.get("macros") or {}
        models = cfg.get("models") or {}
        grouped = set()
        for g in (cfg.get("groups") or {}).values():
            if g.get("exclusive") and g.get("swap"):
                grouped |= set(g.get("members") or [])
        loose = sorted(set(models) - grouped)
        if loose:
            bad.append(f"{prof}: models outside an exclusive swap group {loose}")
        for name, m in models.items():
            cmd = str(m.get("cmd", ""))
            for k, v in macros.items():
                cmd = cmd.replace("${" + k + "}", str(v))
            if not re.search(r"(?<![\w-])-np 1(?!\d)", cmd) or "--host 127.0.0.1" not in cmd:
                bad.append(f"{prof}/{name}: no single slot or not loopback")
        seen[prof] = len(models)
    return not bad, ("; ".join(bad) if bad else f"configs checked {seen}")


def team_adviser_url_host(a):
    """The public adviser is reached only through the privacy gateway: an address that merely CONTAINS the gateway's
    host:port (in its path, query or a longer host name) is someone else's server and must be refused, nothing sent."""
    core = _lt()
    sent, out = [], []
    stream = lambda url, body, headers=None, timeout=0: sent.append(url) or "1. plan"  # noqa: E731
    for url in a["urls"]:
        try:
            core.advise("my password is hunter22", url, "minimax", "SYNTH-KEY", stream=stream)
            out.append(f"SENT to {url}")
        except core.TeamError:
            out.append("refused")
    ok = not sent
    return ok, f"{out}; requests sent {sent}"


def team_local_down_truthful(a):
    """Local Team with llama-swap down (and the decision model down): solve() fails with an error that says the local
    model did not answer. The adviser's plan is never handed back as if it were the answer."""
    core = _lt()
    adv = []

    def post(url, body, headers=None, timeout=0):
        raise core.TeamError(f"no answer from {url.split('/v1')[0]} (URLError)")

    def adviser(task):
        adv.append(task)
        return {"plan": "1. a plan from the public adviser", "hidden_from_adviser": {}}
    orig = core.loaded_models
    core.loaded_models = lambda get=None: {"general", "code", "math"}   # llama-swap's last known list: memory is not the issue
    try:
        try:
            res = core.solve("plan a birthday dinner for six", adviser=adviser, post=post, use_adviser="always")
            err = None
        except core.TeamError as x:
            res, err = None, str(x)
    finally:
        core.loaded_models = orig
    ok = res is None and err is not None and "no answer" in err and "8080" in err and len(adv) <= 1
    return ok, f"error {err!r}; result {res}; adviser asked {len(adv)}x"


def proxy_agent_limits_isolated(a):
    """The per-agent rate limit is per agent: one agent that uses up its minute is refused (429), another agent is still
    served in the same minute, and the provider is not asked for the refused call."""
    from jarvis_kernel.llm_proxy import PER_MINUTE
    with env() as e:
        up = e.mock("up")
        toks = e.proxy([P("up", "private", up.url)])
        t1, t2 = toks.issue("busy-agent"), toks.issue("other-agent")
        codes = [e.post(t1, msg("same status question"))[0] for _ in range(PER_MINUTE + 1)]
        n = len(up.posts())
        other = e.post(t2, msg("same status question"))[0]
        ok = codes[:-1] == [200] * PER_MINUTE and codes[-1] == 429 and n == PER_MINUTE and other == 200
        return ok, f"busy agent: {PER_MINUTE} x 200 then {codes[-1]}; provider asked {n}x; other agent in the same minute: {other}"


def owner_route_after_failure(a):
    """The owner's route explanation after a provider said 429 (Retry-After 300): it is shown resting and excluded, the
    fallback is chosen, Dahl is a lane mismatch; after the rest it is chosen again."""
    with env() as e:
        groq = e.mock("groq", [{"status": 429, "body": b'{"error":"rate"}', "headers": {"Retry-After": "300"}}, {"ok": REPLY}])
        cer = e.mock("cerebras", [{"ok": reply("fallback")}])
        dahl = e.mock("dahl")
        toks = e.proxy([P("groq", "private", groq.url, priority=1), P("cerebras", "private", cer.url, priority=2),
                        P("dahl", "public", dahl.url, priority=0)])
        agent, me = toks.issue("hermes"), toks.issue("me", "owner")
        e.post(agent, msg())
        c1, b1 = e.get(me, "/v1/jarvis/owner/route?lane=private")
        r1 = json.loads(b1)
        e.now[0] += 301
        c2, b2 = e.get(me, "/v1/jarvis/owner/route?lane=private")
        r2 = json.loads(b2)
    codes = {c["provider"]: c["codes"] for c in r1.get("candidates", [])}
    ok = (c1 == c2 == 200 and "RESTING_AFTER_ERRORS" in codes.get("groq", []) and r1.get("chosen") == "cerebras"
          and "groq" not in r1.get("chain", []) and "LANE_MISMATCH" in codes.get("dahl", []) and r2.get("chosen") == "groq")
    return ok, f"during the rest: chosen {r1.get('chosen')}, chain {r1.get('chain')}, groq {codes.get('groq')}; after: chosen {r2.get('chosen')}"
