"""Read-only audit repros (temp dirs only). Run inside neko-test with /src = repo."""
import os, sys, tempfile, json, subprocess, time
sys.path.insert(0, "/src/jarvis/kernel")
from jarvis_kernel.store import Kernel
from jarvis_kernel.memory import Memories
from jarvis_kernel import gateway, sizing
from jarvis_kernel.llm_proxy import AgentTokens, OWNER_REQUEST_TOKENS

print("== R1 render(): which entries reach the session prompt")
with tempfile.TemporaryDirectory() as d:
    k = Kernel(os.path.join(d, "k")); m = Memories(k)
    for i in range(30):
        m.add("owner", f"Preference number {i:02d}: the owner likes option {i} for recurring situation {i} in daily life")
        time.sleep(0.002)
    newest = m.add("owner", "NEWEST: the owner now prefers aisle seats on every flight")
    out = m.render("owner")
    lines = out.splitlines()
    print("rendered lines:", len(lines), "| chars:", len(out))
    print("first:", lines[0][:70]); print("last:", lines[-1][:90])
    print("newest entry in session block:", "NEWEST" in out)
    print("resolve('aisle seats flight') top:", m.resolve("owner", "aisle seats flight")["candidates"][0]["text"][:60])
    k.db.close()

print("== R2 memory is keyed by token NAME, not lane: re-issuing a name in another lane keeps its memories")
with tempfile.TemporaryDirectory() as d:
    k = Kernel(os.path.join(d, "k")); m = Memories(k); t = AgentTokens(k)
    t1 = t.issue("agentx", "private"); m.add("agentx", "Private fact: the PAN card is in the blue folder")
    t2 = t.issue("agentx", "public")
    print("old token still valid:", t.identify(t1) != (None, None), "| new token ->", t.identify(t2))
    print("public-lane token now reads:", [e["text"][:40] for e in m.list("agentx")])
    t3 = t.issue("labtest")  # CLI default lane
    print("issue() default lane:", t.identify(t3)[1])
    k.db.close()

print("== R3 which configured providers can take the owner's agent request (~12k tokens + 1024 out)")
provs = gateway.load("/src/jarvis/config/providers.json")
for lane in ("private", "public"):
    rows = []
    for p in provs:
        if p.enabled and p.lane in ("local", lane):
            rows.append((p.name, p.lane, p.priority, p.os_only, p.max_input_tokens, p.max_total_tokens,
                         sizing.fits_with_output(p, OWNER_REQUEST_TOKENS, sizing.MIN_OUTPUT)))
    print(lane, "lane:")
    for r in sorted(rows, key=lambda r: r[2]):
        print("   ", r)
    print("   lane_context_length:", sizing.lane_context_length([p for p in provs if p.enabled and p.lane in ("local", lane)]))

print("== R4 subprocess zombie: a discarded Popen that exited still has /proc/<pid> (lab_task_result 'running')")
def start():
    p = subprocess.Popen(["true"], stdout=subprocess.DEVNULL, start_new_session=True)
    return p.pid
pid = start(); time.sleep(1.0)
st = open(f"/proc/{pid}/stat").read().split()[2] if os.path.exists(f"/proc/{pid}") else "gone"
print("exists:", os.path.exists(f"/proc/{pid}"), "state:", st)
