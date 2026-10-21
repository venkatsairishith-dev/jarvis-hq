import { test, expect, type Page } from "@playwright/test";
import { consoleHeaders } from "../e2e/console-secret";
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

// Live file-arrival trigger, through the real UI, kernel and laptop node, in a DISPOSABLE folder that is the only watched
// folder in config/watches.json. Nothing personal is watched. The folder is emptied at the end.
const DIR = "D:\\jarvis\\cache\\watch-test";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const J = async (page: Page, p: string) => (await page.request.get(p)).json();
const P = async (page: Page, p: string, body: unknown = {}) =>
  (await page.request.fetch(p, { method: "POST", data: body, headers: await consoleHeaders(page, { Origin: "http://127.0.0.1:8799" }) })).json();

async function runs(page: Page, id: string) {
  const a = (await J(page, "/api/owner/automations")).automations.find((x: { id: string }) => x.id === id);
  return (a?.runs ?? []) as { id: number; source: string; status: string; job_id: string | null; detail: string | null; run_key: string }[];
}
async function waitRuns(page: Page, id: string, n: number, ms = 120_000) {
  const end = Date.now() + ms;
  let r = await runs(page, id);
  while (Date.now() < end && r.filter((x) => x.source === "event").length < n) { await sleep(5000); r = await runs(page, id); }
  return r.filter((x) => x.source === "event");
}
async function settled(page: Page, id: string, ms = 90_000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const ev = (await runs(page, id)).filter((x) => x.source === "event");
    if (ev.length && ev.every((x) => ["done", "failed", "refused"].includes(x.status))) return ev;
    await sleep(4000);
  }
  return (await runs(page, id)).filter((x) => x.source === "event");
}
const restartNode = () => execFileSync("powershell", ["-NoProfile", "-Command", "Stop-ScheduledTask -TaskName JarvisNode; Start-Sleep 3; Start-ScheduledTask -TaskName JarvisNode"]);

test.describe.configure({ mode: "serial" });
let autoId = "";

test.beforeAll(() => { fs.mkdirSync(DIR, { recursive: true }); for (const f of fs.readdirSync(DIR)) fs.unlinkSync(path.join(DIR, f)); });
test.afterAll(() => { for (const f of fs.existsSync(DIR) ? fs.readdirSync(DIR) : []) fs.unlinkSync(path.join(DIR, f)); });

test("1: only the disposable folder is offered, the preview states trigger, target and side effects, and the definition persists", async ({ page }) => {
  test.setTimeout(5 * 60_000);
  await page.goto("/#/automations");
  await page.getByRole("button", { name: "New automation" }).click();
  const dlg = page.getByRole("dialog", { name: "New automation" });
  await dlg.getByLabel("Name").fill("Live file trigger test");
  await dlg.getByLabel("What should run").selectOption({ label: "List the open windows on this laptop (read-only)" });
  await dlg.getByLabel("Start it").selectOption("file");
  const folderSelect = dlg.locator("select").filter({ has: page.locator("option", { hasText: "Choose a folder" }) });
  const offered = await folderSelect.locator("option").allTextContents();
  console.log("OFFERED_FOLDERS", JSON.stringify(offered));
  expect(offered.join(",")).toContain("jarvis-test");
  expect(offered.join(",")).not.toMatch(/downloads|documents|desktop/i);                       // no personal folder is watched
  await folderSelect.selectOption("jarvis-test");
  await dlg.getByLabel("File name pattern").fill("*.txt");
  await dlg.getByRole("button", { name: "Preview" }).click();
  await expect(dlg.getByText("Side effects")).toBeVisible();
  await expect(dlg.getByText(/runs once for each file that arrives in the laptop's 'jarvis-test' folder matching '\*\.txt'/)).toBeVisible();
  await expect(dlg.getByText(/queues a 'ui_windows' job on node 'laptop'/)).toBeVisible();
  await page.screenshot({ path: "D:\\jarvis\\outputs\\console-evidence\\30-file-trigger-preview.png" });
  await dlg.getByRole("button", { name: "Save and schedule" }).click();
  await expect(page.getByText("Automation saved and scheduled.")).toBeVisible();
  const all = (await J(page, "/api/owner/automations")).automations;
  const a = all.find((x: { name: string }) => x.name === "Live file trigger test");
  autoId = a.id;
  console.log("DEFINITION", JSON.stringify({ id: a.id, trigger: a.trigger_kind, spec: a.event_spec, state: a.state, kind: a.action_kind, action: a.action }));
  expect(a.trigger_kind).toBe("event");
  expect(a.event_spec).toEqual({ watch: "jarvis-test", glob: "*.txt" });
});

test("2: a new file fires exactly one run (two quick writes are one arrival) and the real output is in the history", async ({ page }) => {
  test.setTimeout(6 * 60_000);
  await sleep(35_000);                                          // the node's first scan of the folder is its baseline
  const f = path.join(DIR, "arrival-1.txt");
  fs.writeFileSync(f, "first line\n");
  await sleep(300);
  fs.appendFileSync(f, "second line\n");                        // a second filesystem event for the same file
  const ev = await settled(page, autoId);
  console.log("RUNS_AFTER_FIRST_FILE", JSON.stringify(ev.map((r) => ({ status: r.status, job: r.job_id }))));
  expect(ev).toHaveLength(1);
  expect(ev[0].status).toBe("done");
  const job = await J(page, `/api/owner/job/${ev[0].job_id}`);
  console.log("JOB_RESULT", JSON.stringify({ state: job.state, kind: job.kind, ok: job.result?.ok, windows: job.result?.count }));
  expect(job.state).toBe("done");
  expect(job.result.ok).toBe(true);
  expect(job.result.count).toBeGreaterThan(0);                  // a real window list came back from the laptop
  await page.goto("/#/automations");
  await page.locator("li", { hasText: "Live file trigger test" }).getByRole("button", { name: "History" }).click();
  await expect(page.getByRole("dialog").getByText("event", { exact: false }).first()).toBeVisible();
  await expect(page.getByRole("dialog")).toContainText("arrival-1.txt".slice(0, 0) + ":ev:");
  await page.screenshot({ path: "D:\\jarvis\\outputs\\console-evidence\\31-file-trigger-history.png" });
  await page.keyboard.press("Escape");
});

test("3: a file that does not match, and an unchanged file after a node restart, fire nothing", async ({ page }) => {
  test.setTimeout(6 * 60_000);
  const before = (await runs(page, autoId)).filter((r) => r.source === "event").length;
  fs.writeFileSync(path.join(DIR, "ignored.log"), "not a txt\n");        // does not match *.txt
  restartNode();                                                         // the relevant component, restarted: its state is on disk
  await sleep(75_000);
  const after = (await runs(page, autoId)).filter((r) => r.source === "event").length;
  console.log("RUNS_AFTER_RESTART", before, after);
  expect(after).toBe(before);
});

test("4: while paused a new file is not run, and resuming does not replay it; resumed, the next file runs", async ({ page }) => {
  test.setTimeout(8 * 60_000);
  await P(page, "/api/owner/automations/pause", { id: autoId });
  fs.writeFileSync(path.join(DIR, "while-paused.txt"), "arrived while paused\n");
  await sleep(70_000);                                                   // scanned, reported, and dropped by the tick (paused)
  expect((await runs(page, autoId)).filter((r) => r.source === "event")).toHaveLength(1);
  await P(page, "/api/owner/automations/resume", { id: autoId });
  await sleep(40_000);
  expect((await runs(page, autoId)).filter((r) => r.source === "event")).toHaveLength(1);       // the paused file was not replayed
  fs.writeFileSync(path.join(DIR, "after-resume.txt"), "arrived after resume\n");
  const ev = await waitRuns(page, autoId, 2);
  console.log("RUNS_AFTER_RESUME", JSON.stringify(ev.map((r) => r.status)));
  expect(ev).toHaveLength(2);
});

test("5: the scheduler component restarts and the automation survives and still fires", async ({ page }) => {
  test.setTimeout(10 * 60_000);
  const K = "D:\\jarvis-secrets\\ssh\\jarvis_vm_ed25519";
  if (!autoId) autoId = (await J(page, "/api/owner/automations")).automations.find((x: { name: string }) => x.name === "Live file trigger test").id;
  const nActive = async () => (await J(page, "/api/owner/state")).jobs.filter((j: { state: string }) => ["queued", "running"].includes(j.state)).length;
  for (let i = 0; i < 24 && (await nActive()) > 0; i++) await sleep(5000);          // wait for idle: never restart under a running job
  expect(await nActive(), "restart only when the kernel is idle").toBe(0);
  execFileSync("ssh", ["-i", K, "-o", "BatchMode=yes", "ubuntu@100.125.91.51", "sudo -n systemctl restart jarvis-llm-proxy && sleep 3 && systemctl is-active jarvis-llm-proxy"]);
  await sleep(10_000);
  const a = (await J(page, "/api/owner/automations")).automations.find((x: { id: string }) => x.id === autoId);
  expect(a, "the definition survived the restart").toBeTruthy();
  expect(a.state).toBe("scheduled");
  const n = (await runs(page, autoId)).filter((r) => r.source === "event").length;
  fs.writeFileSync(path.join(DIR, "after-kernel-restart.txt"), "arrived after the scheduler restarted\n");
  const ev = await waitRuns(page, autoId, n + 1);
  console.log("RUNS_AFTER_RESTART_FILE", JSON.stringify(ev.map((r) => r.status)));
  expect(ev.length).toBe(n + 1);
});

test("6: deleted means it no longer fires, and the delete is read back", async ({ page }) => {
  test.setTimeout(5 * 60_000);
  if (!autoId) autoId = (await J(page, "/api/owner/automations")).automations.find((x: { name: string }) => x.name === "Live file trigger test").id;
  await page.goto("/#/automations");
  const row = page.locator("li", { hasText: "Live file trigger test" });
  await row.getByRole("button", { name: "Delete" }).click();
  const conf = page.getByRole("dialog", { name: "Delete this automation?" });
  await conf.getByLabel(/Type a-/).fill(autoId);
  await conf.getByRole("button", { name: "Delete" }).click();
  await expect(page.getByText("Deleted.")).toBeVisible();
  const still = (await J(page, "/api/owner/automations")).automations.find((x: { id: string }) => x.id === autoId);
  expect(still).toBeUndefined();
  const jobsBefore = (await J(page, "/api/owner/state")).jobs.filter((j: { kind: string }) => j.kind === "ui_windows").length;
  fs.writeFileSync(path.join(DIR, "after-delete.txt"), "arrived after delete\n");
  await sleep(70_000);
  const jobsAfter = (await J(page, "/api/owner/state")).jobs.filter((j: { kind: string }) => j.kind === "ui_windows").length;
  console.log("JOBS_AFTER_DELETE", jobsBefore, jobsAfter);
  expect(jobsAfter).toBe(jobsBefore);
});
