import json, os, time, collections
S = os.path.dirname(os.path.abspath(__file__))
rows = []
for l in open(os.path.join(S, "bench_rows.jsonl"), encoding="utf-8"):
    try:
        rows.append(json.loads(l))
    except ValueError:                       # a run whose driver crashed (the face was not ready): kept as a row with no result
        m = l.split('"model": "', 1)[-1].split('"', 1)[0] if '"model"' in l else "unknown"
        g = int(l.split('"goal_no": ', 1)[-1].split(",", 1)[0]) if '"goal_no"' in l else 0
        rows.append({"model": m, "goal_no": g, "run": {"error": "the test driver crashed before the run (face not ready); repeated below"}})
REAL = ("jarvis::desktop__", "jarvis::deliverables__", "jarvis::doc_reader__", "jarvis::doc_tools__", "jarvis::jarvis_", "jarvis::web_search__")
NOT_THE_TOOL = {"builtIn_emitSparkCommand", "stage_widgets"}


def score(r):
    run = r["run"]
    if "error" in run or "goal" not in run:
        return None
    calls = run.get("calls", [])
    wrong = []
    for c in calls:
        base, _, inner = c.partition(" -> ")
        if inner and not inner.startswith(REAL):
            wrong.append(c)
        elif base in NOT_THE_TOOL:
            wrong.append(c)
    errors = [s for s in run.get("statuses", []) if s != 200]
    return {"tool_rounds": run.get("rounds"), "seconds": run.get("seconds"), "http_errors": errors, "wrong_tools": wrong, "calls": calls,
            "on_screen_errors": run.get("errors_on_screen", []), "first_request_messages": (run.get("messages") or [None])[0], "request_kb": run.get("request_kb"),
            "chat_tail": run.get("chat_tail", "")[-300:], "timed_out": run.get("timed_out")}


# What the screen showed, judged by me after each run (screenshots in scratchpad/shots_bench): done = the goal's result was really on screen.
DONE = {}  # nothing finished on screen for any model in this run: see notes
out = {"date": time.strftime("%Y-%m-%d"), "protocol": "jarvis/evals/onscreen_bench/PROTOCOL.md",
       "how": "A clean chat per run (stored chat deleted, face restarted), the goal typed into the face chat box, the gateway pinned to one provider with no failover "
              "(pin file D:\\jarvis\\cache\\gateway-pin.txt through JARVIS_GATEWAY_PIN_FILE, because the secrets folder is not writable from the test session).",
       "candidates_not_run": {"cerebras": "no providers\\cerebras.key: the pinned request answered 502 'no key file'",
                              "local Ling-heretic / Granite abliterated": "not installed on this laptop (no local-chat alias in llama-swap.yaml); the installed Ling-3.0-tiny was not asked to run the face's 40 KB request"},
       "runs": []}
for r in rows:
    s = score(r)
    out["runs"].append({"model": r["model"], "goal_no": r["goal_no"], "goal": r["run"].get("goal"), "done": 0, "score": s, "raw_error": r["run"].get("error")})
by = collections.defaultdict(list)
for r in out["runs"]:
    by[r["model"].replace("-repeat", "").replace("-dump", "").replace("-recheck", "")].append(r)
summary = {}
for m, rs in by.items():
    ok = [r for r in rs if r["score"]]
    summary[m] = {"runs": len(rs), "goals_done": sum(r["done"] for r in rs),
                  "http_error_responses": sum(len(r["score"]["http_errors"]) for r in ok), "wrong_tool_calls": sum(len(r["score"]["wrong_tools"]) for r in ok),
                  "median_seconds": sorted(r["score"]["seconds"] for r in ok)[len(ok) // 2] if ok else None}
out["summary"] = summary
out["decision"] = {
    "protocol_pick": "no model finished any of the five goals on screen (0 done everywhere), so the protocol's tie-breaks decide: fewest errors would be MiniMax (0 HTTP errors), but its runs were empty answers or the wrong tool "
                     "(builtIn_emitSparkCommand), so that is not a usable pick.",
    "what_the_runs_show": {
        "dahl GLM-5.3-Flash": "the only model that used the right tool names and the new persona (jarvis_tasks, jarvis_recall, doc_reader); every failure was Dahl's own capacity limit (HTTP 429 model_concurrency: "
                              "'paid accounts are admitted first'), 24 of them, and one run took 7 rounds; 70-170 s per goal",
        "dahl MiniMax-M2.7": "no HTTP errors, 30-110 s, but empty replies or builtIn_emitSparkCommand (a spark-command tool that is not the task) on 7 of 7 runs",
        "groq gpt-oss-120b": "HTTP 413 on every goal: the face's request is 40 KB (about 10,000 tokens) and Groq's free tier takes 8,000 in all; it stays the private lane's model for the small Hermes prompt",
        "cerebras": "not benchmarked: no key file", "local": "Ling-heretic and Granite abliterated are not installed"},
    "provider_order": "unchanged: dahl first (the face's model, GLM-5.3-Flash), then the fallbacks in the gateway order, local last. MiniMax is not set first because it did not use tools in the face.",
    "followups": ["the face sends a second request of 8 KB next to every turn; with Dahl's concurrency cap it doubles the 429s",
                  "a model that follows the tool list in the face is the open problem: try Cerebras once its key exists (room for the 40 KB request: 30K tokens a minute)"]}
path = os.path.join(r"D:\jarvis\hq\jarvis\evals\onscreen_bench\results", "onscreen-" + time.strftime("%Y-%m-%d") + ".json")
os.makedirs(os.path.dirname(path), exist_ok=True)
json.dump(out, open(path, "w", encoding="utf-8"), indent=1)
print(path)
print(json.dumps(summary, indent=1))
