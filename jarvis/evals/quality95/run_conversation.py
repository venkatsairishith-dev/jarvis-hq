"""JARVIS 9.5, C2 conversation: follow-ups across requests through the real Coordinator and the real local decider; the
agents are fakes that record what they were asked to do (no PC actions). Windows, agent-venv, llama-swap on 8080.
    python jarvis\\evals\\quality95\\run_conversation.py [--repeat 3]
CV-followup-open-it          "research solar panels and make me slides" -> "open it" opens THAT file
CV-correction-instead        "open gmail" -> "actually use the satish profile instead" -> gmail in Profile 2
CV-stop-then-new-task        a task, then "stop", then "open notepad" runs normally
CV-no-fabricated-completion  a step whose check fails is never reported as done
CV-pronoun-without-context   "open it" with nothing recent asks instead of guessing
Results: jarvis/evals/quality95/results/conversation-<time>.json
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from jarvis.plugins.desktop import coordinator as coord, core  # noqa: E402

DECK = r"D:\jarvis\outputs\deliverables\20261016-solar\solar-panels.pptx"


def fakes(log):
    def rec(tool, res):
        def fn(step):
            log.append((tool, {k: v for k, v in step.items() if k not in ("post",)}))
            return res(step) if callable(res) else dict(res)
        return fn
    return {
        "research": rec("research", lambda s: {"ok": True, "summary": "checked findings", "file": DECK if s.get("deliverable") != "none" else None,
                                               "qa_passed": True, "opened_verified": bool(s.get("open_after"))}),
        "open_output": rec("open_output", lambda s: {"ok": True, "opened": s.get("path"), "opened_verified": True}),
        "open_website": rec("open_website", lambda s: {"ok": True, "profile": s["profile"], "verified": "window", "profile_name_matches": True}),
        "browser": rec("browser", lambda s: {"ok": True, "matches": True, "seen": "x", "expected": "x"}),
        "open_app": rec("open_app", lambda s: {"ok": True, "verified": "verified" if s["app"].lower() != "brokenapp" else "not seen",
                                               "opened": s["app"]}),
        "play_media": rec("play_media", {"ok": True, "playing": True, "same_video": True, "title": "x"}),
        "stop": rec("stop", {"ok": True, "stopped_jobs": 0}),
        "type_text": rec("type_text", {"ok": True}), "open_file": rec("open_file", {"ok": True}),
        "check_pc": rec("check_pc", {"ok": True}),
    }


def session(requests, profiles):
    """Run the requests in order in one fresh conversation (own task folder and lock); returns (records, agent log)."""
    log = []
    with tempfile.TemporaryDirectory() as d:
        c = coord.Coordinator(fakes(log), profiles, tasks_dir=Path(d) / "tasks", lock_path=Path(d) / "lock")
        recs = []
        for r in requests:
            recs.append(c.run(r))
            time.sleep(0.05)
        return recs, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=3)
    a = ap.parse_args()
    profiles = core.profiles()
    checks = {
        "CV-followup-open-it": (["research solar panels and make me slides", "open it"],
                                lambda recs, log: any(t == "open_output" and s.get("path") == DECK for t, s in log)
                                and recs[1]["status"] == "completed"),
        "CV-correction-instead": (["open gmail", "actually use the satish profile instead"],
                                  lambda recs, log: recs[1]["status"] == "completed" and any(
                                      t == "open_website" and s.get("profile") == "Profile 2" and "mail.google" in s.get("url", "")
                                      for t, s in log)),
        "CV-stop-then-new-task": (["open notepad", "stop", "open notepad"],
                                  lambda recs, log: [r["status"] for r in recs] == ["completed", "stopped", "completed"]),
        "CV-no-fabricated-completion": (["open brokenapp"],
                                        lambda recs, log: recs[0]["status"] == "failed" and not coord.summary(recs[0]).startswith("Done")),
        "CV-pronoun-without-context": (["open it"], lambda recs, log: recs[0]["status"] == "needs_clarification" and not log),
    }
    rows, runs = [], {}
    for cid, (reqs, ok) in checks.items():
        res = []
        for rep in range(a.repeat):
            recs, log = session(reqs, profiles)
            passed = bool(ok(recs, log))
            res.append({"rep": rep, "ok": passed, "statuses": [r["status"] for r in recs], "log": log[-4:],
                        "plans": [(r.get("plan") or {}).get("steps") for r in recs]})
            print(("PASS " if passed else "FAIL ") + cid, [r["status"] for r in recs], flush=True)
        runs[cid] = res
        rate = sum(r["ok"] for r in res) / len(res)
        rows.append({"cap": "C2_conversation", "id": cid, "status": "pass" if rate == 1.0 else "fail", "repeat_rate": round(rate, 3),
                     "detail": f"{sum(r['ok'] for r in res)}/{len(res)} runs; real decider + Coordinator, fake agents"})
    out = HERE / "results" / f"conversation-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "rows": rows, "runs": runs}, indent=1, default=str), encoding="utf-8")
    print("saved", out)


if __name__ == "__main__":
    main()
