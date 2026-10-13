import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawnSync } from "node:child_process";

// Live scenarios 2, 3, 4, 6 and 7: real Hermes, real lab (Dahl models, public lane), real laptop node.
test.describe.configure({ mode: "serial" });
const shot = (page: Page, name: string) => page.screenshot({ path: `test-results/live/${name}.png` });
const J = async (page: Page, p: string) => (await page.request.get(p)).json();
const P = async (page: Page, p: string, body: unknown = {}) =>
  (await page.request.fetch(p, { method: "POST", data: body, headers: { "X-Jarvis-Console": "1", Origin: "http://127.0.0.1:8799" } })).json();
const FINAL = new Set(["completed", "failed", "cancelled"]);
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function waitTask(page: Page, match: (t: { goal: string; owner: string; created: number }) => boolean, since: number, minutes: number) {
  const end = Date.now() + minutes * 60_000;
  let t: { id: string; state: string; goal: string; attempts: number } | undefined;
  while (Date.now() < end) {
    const st = await J(page, "/api/owner/state");
    t = st.tasks.find((x: { goal: string; owner: string; created: number }) => x.created >= since - 5 && match(x));
    if (t && FINAL.has(t.state)) return t;
    await sleep(8000);
  }
  return t;
}

test("scenario 2: a request becomes a lab task that is built, tested, independently verified and delivered", async ({ page }) => {
  test.setTimeout(35 * 60_000);
  const since = Date.now() / 1000;
  await page.goto("/");
  const ask = "Please build a small Python command line tool called vowelcount.py that prints how many vowels are in the text it reads from standard input. " +
    "Include unit tests in test_vowelcount.py and a README.md. Do the work in the lab, have it independently verified, and deliver the files to me.";
  const r = await P(page, "/api/owner/command", { text: ask, source: "text" });
  console.log("COMMAND_ID", r.id);
  const t = await waitTask(page, (x) => /vowel/i.test(x.goal), since, 30);
  console.log("TASK", JSON.stringify(t));
  await page.goto("/#/tasks");
  await shot(page, "07-tasks-after-build");
  expect(t, "a task was created for the request").toBeTruthy();
  const full = await J(page, `/api/owner/task/${t!.id}`);
  console.log("TASK_STATE", JSON.stringify({ state: full.state, attempts: full.attempts, verified: full.verification?.passed, result: String(full.result).slice(0, 200) }));
  expect(full.state).toBe("completed");
  expect(full.verification?.passed).toBe(true);
  // the delivered files exist, and the tests pass when run from a clean folder (independent of the lab)
  const dir = `D:\\jarvis\\outputs\\task-${t!.id}`;
  const files = fs.readdirSync(dir);
  console.log("DELIVERED", JSON.stringify(files));
  expect(files).toEqual(expect.arrayContaining(["vowelcount.py", "test_vowelcount.py"]));
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "jarvis-verify-"));
  for (const f of files) fs.copyFileSync(path.join(dir, f), path.join(tmp, f));
  const run = spawnSync("D:\\jarvis-apps\\python\\cpython-3.12.13-windows-x86_64-none\\python.exe", ["-I", "-m", "unittest", "-v", "test_vowelcount"], { cwd: tmp, encoding: "utf-8" });
  console.log("CLEAN_RUN exit", run.status, (run.stderr || "").split("\n").slice(-4).join(" | "));
  expect(run.status).toBe(0);
  await page.goto("/#/workspace");
  await expect(page.getByText(/vowelcount\.py/).first()).toBeVisible();
  await shot(page, "08-workspace-delivered");
});

test("scenario 3: describe a specialist, review the definition, activate it and run a bounded test through the lab", async ({ page }) => {
  test.setTimeout(35 * 60_000);
  const since = Date.now() / 1000;
  await page.goto("/#/agents");
  const sentence = "Create a Python software engineer agent that writes code, tests it and fixes failures";
  await page.locator("#agent-sentence").fill(sentence);
  await page.getByRole("button", { name: "Propose a definition" }).click();
  await expect(page.getByRole("dialog", { name: "Review the proposed agent" })).toBeVisible();
  await shot(page, "09-agent-definition-review");
  await page.getByRole("dialog", { name: "Review the proposed agent" }).getByRole("button", { name: "Save as draft" }).click();
  const agents = (await J(page, "/api/owner/agents")).agents;
  const a = agents.find((x: { name: string; state: string }) => /Python/i.test(x.name) && x.state === "draft") ?? agents[0];
  console.log("AGENT", JSON.stringify({ id: a.id, name: a.name, state: a.state, tools: a.tools, criteria: a.criteria.length }));
  expect((await P(page, "/api/owner/agents/activate", { id: a.id })).state).toBe("active");
  const test_ = await P(page, "/api/owner/agents/test", { id: a.id, task: "Write a Python function is_palindrome(s) that ignores case and punctuation, with three unit tests, in palindrome.py and test_palindrome.py" });
  console.log("AGENT_TEST", JSON.stringify(test_));
  expect(test_.task).toBeTruthy();
  // scenario 7 runs here: take control of the Agent PC while the agent works, then hand it back
  await sleep(25_000);
  const before = await J(page, "/api/desktop/state");
  const took = await P(page, "/api/desktop/take");
  console.log("TAKE", JSON.stringify({ before: before.mode, after: took.mode, frozen: (took.tasks ?? []).length }));
  await page.goto("/#/desktop");
  await shot(page, "10-operator-has-control");
  expect(took.mode).toBe("owner");
  const during = await J(page, `/api/owner/task/${test_.task}`);
  await sleep(20_000);
  const still = await J(page, `/api/owner/task/${test_.task}`);
  console.log("PAUSED_CHECK", JSON.stringify({ state_before: during.state, state_after: still.state, step_before: during.step, step_after: still.step, updated_same: during.updated === still.updated }));
  const back = await P(page, "/api/desktop/continue");
  console.log("CONTINUE", JSON.stringify({ mode: back.mode }));
  expect(back.mode).toBe("agent");
  const t = await waitTask(page, (x) => x.owner === `agent:${a.name}`, since, 30);
  console.log("AGENT_TASK", JSON.stringify({ state: t?.state, attempts: t?.attempts }));
  await page.goto("/#/agents");
  await page.getByRole("button", { name: a.name }).click();
  await shot(page, "11-agent-with-history");
  expect(t, "the agent's test task exists").toBeTruthy();
  expect(FINAL.has(t!.state)).toBe(true);
});

test("scenario 4: an automation with a schedule, previewed, run, fired by the scheduler, with history", async ({ page }) => {
  test.setTimeout(8 * 60_000);
  await page.goto("/#/automations");
  const def = { name: "Desktop windows every minute (live test)", action_kind: "job", trigger_kind: "interval", schedule: "60", tz: "Asia/Kolkata",
    action: { kind: "ui_windows", params: {}, node: "laptop" }, max_runs_per_day: 20 };
  const pv = await P(page, "/api/owner/automations/preview", { definition: def });
  console.log("PREVIEW", JSON.stringify({ next: pv.next_runs?.slice(0, 2), effects: pv.side_effects }));
  const a = await P(page, "/api/owner/automations", { definition: def });
  console.log("CREATED", JSON.stringify({ id: a.id, next_run: a.next_run, state: a.state }));
  const run = await P(page, "/api/owner/automations/run", { id: a.id });
  console.log("RUN_NOW", JSON.stringify({ status: run.status }));
  // wait for the scheduler (30 s ticks) to fire a scheduled slot, and for the node to answer the jobs
  let runs: { source: string; status: string; job_id: string | null }[] = [];
  for (let i = 0; i < 24; i++) {
    await sleep(10_000);
    const cur = (await J(page, "/api/owner/automations")).automations.find((x: { id: string }) => x.id === a.id);
    runs = cur.runs;
    if (runs.some((r) => r.source === "schedule" && ["done", "failed"].includes(r.status))) break;
  }
  console.log("RUNS", JSON.stringify(runs.map((r) => ({ source: r.source, status: r.status }))));
  await page.goto("/#/automations");
  await page.locator("li", { hasText: "Desktop windows every minute" }).getByRole("button", { name: "History" }).click();
  await shot(page, "12-automation-history");
  await page.keyboard.press("Escape");
  await P(page, "/api/owner/automations/pause", { id: a.id });
  const del = await P(page, "/api/owner/automations/delete", { id: a.id, confirm: a.id });
  console.log("DELETED", JSON.stringify(del));
  expect(runs.some((r) => r.source === "manual" && r.status === "done")).toBe(true);
  expect(runs.some((r) => r.source === "schedule")).toBe(true);
  expect(del.verified).toBe(true);
});

test("scenario 6: lessons the kernel proposes from tasks that failed, were repaired and verified", async ({ page }) => {
  await page.goto("/#/memory");
  const l = await J(page, "/api/owner/lessons");
  console.log("LESSONS", JSON.stringify(l.lessons.map((x: { id: string; owner: string; task: string; text: string }) => ({ id: x.id, task: x.task, text: x.text.slice(0, 160) }))));
  await shot(page, "13-lessons");
  const st = await J(page, "/api/owner/state");
  const repaired = st.tasks.filter((t: { attempts: number; state: string }) => t.attempts >= 2);
  console.log("REPAIRED_TASKS", JSON.stringify(repaired.map((t: { id: string; state: string; attempts: number }) => ({ id: t.id, state: t.state, attempts: t.attempts }))));
});
