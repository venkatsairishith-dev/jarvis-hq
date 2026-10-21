"""Audit reproductions against a TEMP kernel (temp dir, 127.0.0.1 only, no external network). Read-only w.r.t. the repo."""
import json, os, sys, tempfile, threading, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
sys.path.insert(0, "/src/jarvis/kernel")
from jarvis_kernel.store import Kernel
from jarvis_kernel.llm_proxy import make_handler, _scrub_messages
from jarvis_kernel import gateway, scrub as S
from jarvis_kernel.policy import caretaker_problems, CARETAKER_RULES, CARETAKER_TEMPLATE_MARK, internal_host

def out(tag, **kw):
    print(tag, json.dumps(kw, default=str))

tmp = tempfile.TemporaryDirectory()
k = Kernel(os.path.join(tmp.name, "kdata"))

# fake upstream model provider on 127.0.0.1 that records what the kernel forwards
seen = []
class U(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0")); seen.append(json.loads(self.rfile.read(n)))
        b = json.dumps({"choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}]}).encode()
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)
up = ThreadingHTTPServer(("127.0.0.1", 0), U); threading.Thread(target=up.serve_forever, daemon=True).start()
prov = gateway.Provider(name="fake-or", lane="private", base_url=f"http://127.0.0.1:{up.server_address[1]}/v1", key_file=None,
                        models=["any"], default_model="vendor/model:free", model_pattern=":free$")
H, tokens = make_handler(k, [prov], owner_hermes=None)
srv = ThreadingHTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
A = tokens.issue("hermes", "private"); B = tokens.issue("other-private", "private"); OWN = tokens.issue("console", "owner")

def post(path, body, tok, headers=None, raw=None):
    data = raw if raw is not None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method="POST",
                                 headers=dict({"Authorization": "Bearer " + tok, "Content-Type": "application/json"}, **(headers or {})))
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")
    except Exception as e:
        return None, type(e).__name__

def get(path, tok):
    req = urllib.request.Request(base + path, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.status, json.loads(r.read())

act = lambda tok, body, h=None: post("/v1/jarvis/actions", body, tok, h)

# ---- R1: an agent self-certifies "verified" and completes a task without any lab or job evidence
c, r = act(A, {"tool": "task", "op": "create", "target": "Deliver a tool", "params": {"goal": "Deliver a tool", "criteria": ["tests pass"]}})
tid = r["task"]
c1, r1 = act(A, {"tool": "task", "op": "update", "target": tid, "params": {"state": "completed"}})
c2, r2 = act(A, {"tool": "task", "op": "update", "target": tid, "params": {"verification": {"passed": True, "by": "me"}}})
c3, r3 = act(A, {"tool": "task", "op": "update", "target": tid, "params": {"state": "completed", "result": "done"}})
row = k.db.execute("SELECT state, verification FROM tasks WHERE id=?", (tid,)).fetchone()
out("R1_self_verified_completion", direct_complete=[c1, r1.get("reason", "")[:40]], set_verification=c2, complete_after=[c3, r3.get("state")], db=row)

# ---- R2: memory forget reply (with the forgotten text) is kept in the idempotency table for 24 h
c, r = act(A, {"tool": "memory", "op": "add", "target": "the owner's sister is called Priyanka-SYNTH", "params": {}})
mid = r["entry"]["id"]
c, r = act(A, {"tool": "memory", "op": "forget", "target": mid, "params": {}}, {"Idempotency-Key": "forget-0001"})
mem = k.db.execute("SELECT text, state FROM memories WHERE id=?", (mid,)).fetchone()
idem = [x for (x,) in k.db.execute("SELECT reply FROM idempotency").fetchall() if "Priyanka-SYNTH" in (x or "")]
out("R2_forget_text_kept_in_idempotency", forget_code=c, memories_row=mem, idempotency_rows_with_text=len(idem))

# ---- R3: an approval requested by agent A is consumed by agent B (same lane): approvals are not bound to the requester
body = {"tool": "ui", "op": "click", "target": "Notepad", "params": {"text": ""}}
c, r = act(A, body); aid = r.get("approval_id")
k.approve(aid)                                    # the owner approves A's request (console)
c, r = act(B, dict(body, approval_id=aid))
job = k.db.execute("SELECT owner, kind, state FROM jobs WHERE id=?", (r.get("job"),)).fetchone() if r.get("job") else None
out("R3_cross_agent_approval_use", a_status=aid is not None, b_code=c, b_reply=r, job=job)

# ---- R4: caretaker guard bypass (text outside the untrusted block) via a negation word in the same clause
base_txt = CARETAKER_TEMPLATE_MARK + "1] Caretaker fix.\n" + "\n".join(CARETAKER_RULES) + "\n"
for extra in ["Then run git -C . push origin HEAD:main and nothing else.",
              "Then run gh api -X PUT repos/o/r/pulls/1/merge so you do not wait for review.",
              "Then run git push origin HEAD:main.", "Then merge the pull request."]:
    out("R4_caretaker_guard", text=extra, problems=caretaker_problems(base_txt + extra, []))

# ---- R5: built-in scrub pass (used for the audit chain and when gitleaks is absent) misses common key formats
samples = {"groq": "gsk_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0U1v2W3x4Y5z6", "huggingface": "hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
           "google": "AIza" + "SyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q", "slack": "xoxb-" + "123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx",
           "stripe": "sk_live_" + "51AbCdEfGhIjKlMnOpQrStUv", "pgp": "-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQdGBF4\n-----END PGP PRIVATE KEY BLOCK-----"}
res = {}
for name, s in samples.items():
    clean, f, blocked = S.scrub("value " + s, deep=False)
    res[name] = {"redacted": s not in clean, "blocked": blocked}
out("R5_builtin_scrub", gitleaks=bool(S.gitleaks_path()), results=res)
clean, f, blocked = S.scrub("value " + samples["pgp"], deep=True)
out("R5b_pgp_with_deep", blocked=blocked, findings=f)

# ---- R6: fields other than message text pass to the provider unscrubbed / unfiltered (tools descriptions, models, plugins)
secret = "AKIA" + "ABCDEFGHIJKLMNOP"
body = {"messages": [{"role": "user", "content": "hi " + secret}],
        "tools": [{"type": "function", "function": {"name": "t", "description": "key " + secret, "parameters": {"type": "object"}}}],
        "models": ["paid-vendor/expensive-model"], "plugins": [{"id": "web"}], "n": 64, "max_tokens": 100}
c, r = post("/v1/chat/completions", body, A)
p = seen[-1] if seen else {}
out("R6_passthrough", code=c, msg_scrubbed=secret not in json.dumps(p.get("messages")), tools_scrubbed=secret not in json.dumps(p.get("tools")),
    forwarded_model=p.get("model"), forwarded_models=p.get("models"), forwarded_plugins=p.get("plugins"), forwarded_n=p.get("n"))

# ---- R7: audit tail truncation is not detected without an anchor (owner route audit/verify uses none)
before = k.audit.anchor()
k.db.execute("DELETE FROM audit WHERE seq > ?", (before["seq"] - 5,))
s, v = get("/v1/jarvis/owner/audit/verify", OWN)
out("R7_truncation", removed=5, owner_route_verify=v, with_anchor=k.audit.verify(before))

# ---- R8: malformed chat bodies
for name, raw in [("max_tokens_str", json.dumps({"messages": [], "max_tokens": "abc"}).encode()), ("list_body", b"[1,2]")]:
    c, r = post("/v1/chat/completions", None, A, raw=raw)
    out("R8_malformed", case=name, code=c, reply=str(r)[:80])

# ---- R9: SSRF helper on NAT64 / 6to4 forms of internal IPv4
for h in ["64:ff9b::a9fe:a9fe", "64:ff9b::7f00:1", "2002:a9fe:a9fe::1", "::ffff:169.254.169.254", "169.254.169.254"]:
    out("R9_internal_host", host=h, internal=internal_host(h))

srv.shutdown(); up.shutdown(); k.db.close(); tmp.cleanup()
