"""Context reliability, end to end: REAL Hermes compaction + the REAL JARVIS gateway and kernel (this checkout), with
fake providers that behave like the measured ones. Runs on the server as the Hermes user, in a disposable profile.

    sudo -u jarvis-agent -H env HERMES_HOME=<profile> JK=<kernel dir> <hermes venv>/bin/python ctx_recovery.py

Scenarios: (1) a ~219k-character history with a working summarizer; (2) a summarizer that only returns truncated
summaries; (3) the large provider out of quota and the small one too small, then recovery in a fresh session;
(4) every provider down; (5) a session restart. For each: the conversation size before and after, what the
providers saw (did the system prompt carry the kernel checkpoint and the saved preference?), the agent's answer,
and how many upstream calls were made (bounded, no retry storm). Prints one JSON report.
"""
import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.environ["JK"])
from jarvis_kernel import gateway                                  # noqa: E402
from jarvis_kernel.llm_proxy import make_handler                   # noqa: E402
from jarvis_kernel.memory import Memories                          # noqa: E402
from jarvis_kernel.store import Kernel                             # noqa: E402
from jarvis_kernel.tasks import Tasks                              # noqa: E402

GOAL, PREF = "Ship the invoice exporter", "Owner wants answers as bullet points"


class Fake:
    """A provider. mode: ok | cut (summaries truncated) | down (502) | quota (429 per-day)."""
    def __init__(self, name, mode="ok"):
        self.name, self.mode, self.calls, self.seen = name, mode, 0, []
        me = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"
            def log_message(self, *a): pass
            def do_POST(self):
                req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                me.calls += 1
                msgs = req.get("messages") or []
                sys_txt = " ".join(str(m.get("content")) for m in msgs if m.get("role") == "system")
                first = str(msgs[0].get("content")) if msgs else ""
                is_sum = first.startswith("You are a summarization agent")
                me.seen.append({"summary": is_sum, "chars": len(json.dumps(msgs)), "n": len(msgs),
                                "sys_checkpoint": GOAL in sys_txt, "sys_pref": PREF in sys_txt})
                if me.mode == "down":
                    return self._send(502, {"error": {"message": "bad gateway"}})
                if me.mode == "quota":
                    return self._send(429, {"error": {"message": "Rate limit exceeded: free-models-per-day"}})
                if is_sum:
                    body = "## Goal\n" + GOAL + "\n## Progress\n" + "earlier work summarised. " * 60
                    fin = "length" if me.mode == "cut" else "stop"
                    return self._reply(req, body, fin)
                text = (f"ACK sys_checkpoint={GOAL in sys_txt} sys_pref={PREF in sys_txt} msgs={len(msgs)} "
                        f"chars={len(json.dumps(msgs))} from={me.name}")
                return self._reply(req, text, "stop")

            def _send(self, code, obj):
                b = json.dumps(obj).encode()
                self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
                self.end_headers(); self.wfile.write(b)

            def _reply(self, req, text, fin):
                if not req.get("stream"):
                    return self._send(200, {"id": "x", "object": "chat.completion", "model": "fake", "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": fin}],
                        "usage": {"prompt_tokens": len(json.dumps(req.get("messages"))) // 4, "completion_tokens": len(text) // 4,
                                  "total_tokens": 0}})
                self.send_response(200); self.send_header("Content-Type", "text/event-stream"); self.end_headers()
                for c in ({"choices": [{"index": 0, "delta": {"role": "assistant", "content": text}, "finish_reason": None}]},
                          {"choices": [{"index": 0, "delta": {}, "finish_reason": fin}]}):
                    self.wfile.write(b"data: " + json.dumps(dict(c, id="x", object="chat.completion.chunk", model="fake")).encode() + b"\n\n")
                self.wfile.write(b"data: [DONE]\n\n"); self.wfile.flush()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"


def start_gateway(k, providers):
    H, tokens = make_handler(k, providers)
    s = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, tokens, f"http://127.0.0.1:{s.server_address[1]}"


def long_history(chars=219_000):
    out, i = [], 0
    while sum(len(m["content"]) for m in out) < chars:
        out.append({"role": "user", "content": f"Step {i}: please look at part {i} of the invoice exporter. " + "detail " * 300})
        out.append({"role": "assistant", "content": f"Done with part {i}; the totals in module {i} now round correctly. " + "note " * 300})
        i += 1
    return out


def run(label, providers_spec, history, k, token_file, timeout_s=420):
    fakes = {name: Fake(name, mode) for name, mode, _ in providers_spec}
    provs = [gateway.Provider(name, "private", fakes[name].url, None, ["any"], priority=i + 1, **limits)
             for i, (name, mode, limits) in enumerate(providers_spec)]
    srv, tokens, url = start_gateway(k, provs)
    tok = tokens.issue("jtest-agent")
    with open(token_file, "w") as fh:
        fh.write(tok)
    os.environ["JARVIS_ENDPOINT"] = url
    from run_agent import AIAgent
    agent = AIAgent(base_url=url + "/v1", api_key=tok, provider="custom", model="jarvis", enabled_toolsets=["memory"],
                    quiet_mode=True, skip_context_files=True, session_id=f"ctx-{label}-{int(time.time())}", max_iterations=4)
    before = len(json.dumps(history))
    t0, res, err = time.time(), None, None
    box = {}
    th = threading.Thread(target=lambda: box.update(r=agent.run_conversation("What is my open task, and what is the next step? One line.",
                                                                              conversation_history=list(history))), daemon=True)
    th.start(); th.join(timeout_s)
    res = box.get("r") or {}
    msgs = res.get("messages") or []
    srv.shutdown(); [f.srv.shutdown() for f in fakes.values()]
    calls = {n: {"calls": f.calls, "summary_calls": sum(1 for s in f.seen if s["summary"]),
                 "max_request_chars": max([s["chars"] for s in f.seen] or [0])} for n, f in fakes.items()}
    last_chat = next((s for f in fakes.values() for s in reversed(f.seen) if not s["summary"]), {})
    fails = [json.loads(p)["class"] if "class" in json.loads(p) else json.loads(p).get("error") for (p,) in
             k.db.execute("SELECT payload FROM audit WHERE kind='agent_model_call_failed' AND ts>?", (t0,)).fetchall()]
    return {"scenario": label, "seconds": round(time.time() - t0), "finished": not th.is_alive(),
            "history_chars_before": before, "messages_after": len(msgs), "conversation_chars_after": len(json.dumps(msgs)),
            "compressed": any("context checkpoint" in str(m.get("content", ""))[:400].lower() or "summary" in str(m.get("content", ""))[:200].lower()
                              for m in msgs if m.get("role") in ("user", "assistant", "system")),
            "completed": res.get("completed"), "failed": res.get("failed"), "error": str(res.get("error") or "")[:200],
            "answer": str(res.get("final_response") or "")[:220], "providers": calls,
            "last_model_input": last_chat, "gateway_failures": fails[:12],
            "too_large_answers": k.db.execute("SELECT COUNT(*) FROM audit WHERE kind='agent_model_too_large' AND ts>?", (t0,)).fetchone()[0]}


def main():
    home = os.environ["HERMES_HOME"]
    token_file = os.path.join(home, "jarvis-memory.token")
    tmp = tempfile.mkdtemp(prefix="ctxk-")
    k = Kernel(tmp)
    Tasks(k).create("jtest-agent", "private", "", GOAL, plan=["export CSV", "verify totals"], criteria=["totals match the ledger"])
    Memories(k).add("jtest-agent", PREF)
    hist = long_history()
    GROQ, ROUTER = {"max_total_tokens": 8000}, {"max_input_tokens": 56000}
    only = os.environ.get("SCEN", "")
    plan = [
        lambda: run("1-long-history-summarizer-ok", [("groq", "ok", GROQ), ("router", "ok", ROUTER)], hist, k, token_file),
        lambda: run("2-summarizer-truncates", [("groq", "ok", GROQ), ("router", "cut", ROUTER)], hist, k, token_file),
        lambda: run("3a-router-out-of-quota-groq-too-small", [("router", "quota", ROUTER), ("groq", "ok", GROQ)], hist, k, token_file),
        lambda: run("3b-recovery-in-a-fresh-session", [("router", "quota", ROUTER), ("groq", "ok", GROQ)], [], k, token_file),
        lambda: run("4-all-providers-down", [("groq", "down", GROQ), ("router", "down", ROUTER)], hist[:40], k, token_file, timeout_s=300),
        lambda: run("5-session-restart", [("groq", "ok", GROQ), ("router", "ok", ROUTER)], [], k, token_file),
    ]
    labels = ["1-", "2-", "3a-", "3b-", "4-", "5-"]
    report = [f() for f, lab in zip(plan, labels) if not only or lab.startswith(only)]
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
