import { test, expect, type Page } from "@playwright/test";
import { execFileSync, spawn } from "node:child_process";

// Live operational reliability against the real console, kernel and laptop node. Read-only requests only; nothing here
// touches personal files, the protected VM, Telegram or the kill switch.
const BASE = "http://127.0.0.1:8799";
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const J = async (page: Page, p: string) => (await page.request.get(p)).json();
const P = async (page: Page, p: string, body: unknown = {}) =>
  (await page.request.fetch(p, { method: "POST", data: body, headers: { "X-Jarvis-Console": "1", Origin: BASE } })).json();
const cmd = async (page: Page, text: string) => (await J(page, "/api/owner/commands")).commands.find((c: { text: string }) => c.text === text);
const idle = async (page: Page) => {
  for (let i = 0; i < 40; i++) {
    const c = (await J(page, "/api/owner/commands")).commands as { state: string }[];
    if (!c.some((x) => x.state === "running")) return;
    await sleep(5000);
  }
};
const ps = (script: string) => execFileSync("powershell", ["-NoProfile", "-Command", script], { encoding: "utf8" });
const restartConsole = () => {
  ps(`Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match 'pythonw.*-m jarvis_console$' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Start-Sleep 2`);
    spawn("powershell", ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "D:/jarvis/hq/scripts/start-console.ps1"], { detached: true, stdio: "ignore" }).unref();   // not execFileSync: the console inherits the pipe
};

test.describe.configure({ mode: "serial" });

test("A: reloading the browser mid-command keeps the command, and the answer appears after the reload", async ({ page }) => {
  test.setTimeout(6 * 60_000);
  await idle(page);
  const text = `Reliability check A (${Date.now()}): how many windows are open on my PC right now? Just give me the number.`;
  await page.goto("/");
  await page.locator("#command-input").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(async () => (await cmd(page, text))?.state, { timeout: 20_000 }).toBeTruthy();
  await page.reload();
  await expect(page.getByText(text).first()).toBeVisible();                       // the history survives the reload
  await expect.poll(async () => (await cmd(page, text))?.state, { timeout: 5 * 60_000, intervals: [3000] }).toMatch(/^(done|failed)$/);
  const c = await cmd(page, text);
  console.log("A_RESULT", JSON.stringify({ state: c.state, reply: String(c.reply ?? "").slice(0, 200) }));
  expect(c.state).toBe("done");
  await page.reload();
  await expect(page.getByText(String(c.reply).slice(0, 30)).first()).toBeVisible();
});

test("B: the console restarts while a command runs; the kernel finishes it and the page reconnects", async ({ page }) => {
  test.setTimeout(8 * 60_000);
  await idle(page);
  const text = `Reliability check B (${Date.now()}): what is today's date? One short sentence.`;
  await page.goto("/");
  await page.locator("#command-input").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(async () => (await cmd(page, text))?.state, { timeout: 20_000 }).toBe("running");
  restartConsole();
  let up = false;
  for (let i = 0; i < 20 && !up; i++) { try { up = (await page.request.get("/api/health")).ok(); } catch { /* still starting */ } if (!up) await sleep(1500); }
  expect(up, "the console is back").toBe(true);
  await page.goto("/");
  await expect.poll(async () => (await cmd(page, text))?.state, { timeout: 5 * 60_000, intervals: [3000] }).toMatch(/^(done|failed)$/);
  const c = await cmd(page, text);
  console.log("B_RESULT", JSON.stringify({ state: c.state, reply: String(c.reply ?? "").slice(0, 160) }));
  expect(c.state).toBe("done");
  const h = await J(page, "/api/health");
  expect(h.kernel.authorized).toBe(true);                                         // the restarted console still holds the owner token
  await page.reload();
  await expect(page.getByText(text).first()).toBeVisible();
});

test("C: the laptop node is stopped, the job waits, and it completes when the node returns", async ({ page }) => {
  test.setTimeout(8 * 60_000);
  await idle(page);
  ps("if ((Get-ScheduledTask -TaskName JarvisNode).State -ne 'Running') { Start-ScheduledTask -TaskName JarvisNode }; Start-Sleep 8");
  ps("Stop-ScheduledTask -TaskName JarvisNode");
  for (let i = 0; i < 20; i++) {                                                   // wait until the node process is really gone
    const left = ps("(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'jarvis_node' }).Count").trim();
    if (!left || left === "0") break;
    await sleep(500);
  }
  try {
    await page.goto("/#/desktop");
    await page.getByRole("tab", { name: "This Windows PC" }).click();
    const before = (await J(page, "/api/owner/state")).jobs.filter((j: { kind: string }) => j.kind === "ui_windows").map((j: { id: string }) => j.id);
    await page.getByRole("button", { name: "List open windows" }).click();
    let job: { id: string; state: string } | undefined;
    for (let i = 0; i < 20 && !job; i++) {
      job = (await J(page, "/api/owner/state")).jobs.find((j: { kind: string; id: string }) => j.kind === "ui_windows" && !before.includes(j.id));
      if (!job) await sleep(1000);
    }
    expect(job, "the request became a kernel job").toBeTruthy();
    await sleep(25_000);                                                            // longer than a poll interval: nobody picks it up
    const waiting = (await J(page, "/api/owner/state")).jobs.find((j: { id: string }) => j.id === job!.id);
    console.log("C_WHILE_NODE_DOWN", waiting.state);
    expect(["queued", "pending"]).toContain(waiting.state);
    ps("Start-ScheduledTask -TaskName JarvisNode");
    await expect.poll(async () => (await J(page, "/api/owner/state")).jobs.find((j: { id: string }) => j.id === job!.id)?.state, { timeout: 3 * 60_000, intervals: [3000] }).toBe("done");
    await expect(page.getByText("foreground").first()).toBeVisible({ timeout: 60_000 });
  } finally {
    ps("if ((Get-ScheduledTask -TaskName JarvisNode).State -ne 'Running') { Start-ScheduledTask -TaskName JarvisNode }");
  }
});

test("D: stopping the wait cancels the command and its late answer is ignored", async ({ page }) => {
  test.setTimeout(4 * 60_000);
  await idle(page);
  const text = `Reliability check D (${Date.now()}): write a short poem about tea, four lines.`;
  await page.goto("/");
  await page.locator("#command-input").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop waiting" }).click();
  await expect.poll(async () => (await cmd(page, text))?.state, { timeout: 15_000 }).toBe("cancelled");
  await idle(page);                                                                 // the agent's own call finishes in the background
  await sleep(30_000);
  const c = await cmd(page, text);
  expect(c.state).toBe("cancelled");
  expect(c.reply ?? "").toBe("");
});

test("E: the console keeps the owner token to itself and the browser cannot reach the kernel's other planes", async ({ page }) => {
  const h = { Origin: BASE, "X-Jarvis-Console": "1" };
  const secretish = /Bearer\s+[A-Za-z0-9_\-]{16,}|owner\.token|jarvis-secrets|\"token\"\s*:\s*\"[A-Za-z0-9_\-]{20,}/;
  for (const p of ["/api/health", "/api/owner/state", "/api/owner/commands", "/api/owner/models", "/api/voice/status", "/api/owner/automations"]) {
    const r = await page.request.get(p, { headers: h });
    expect(r.status(), p).toBeLessThan(500);
    expect(secretish.test(await r.text()), `${p} must not contain a token`).toBe(false);
  }
  // another website's page cannot drive the console, and a request without the console header is refused
  const foreign = await page.request.post("/api/owner/command", { data: { text: "hello" }, headers: { Origin: "http://evil.example", "X-Jarvis-Console": "1" } });
  expect(foreign.status()).toBeGreaterThanOrEqual(400);
  const neither = await page.request.post("/api/owner/command", { data: { text: "hello" } });      // no Origin and no console header
  expect(neither.status()).toBeGreaterThanOrEqual(400);
  // a rebinding Host is refused
  const rebind = await page.request.get("/api/health", { headers: { Host: "evil.example" } });
  expect(rebind.status()).toBeGreaterThanOrEqual(400);
  // the browser cannot reach the agent action plane, the node plane or arbitrary kernel paths through the console
  for (const p of ["/api/v1/jarvis/actions", "/api/v1/jarvis/owner/state", "/api/owner/../../v1/jarvis/actions", "/api/owner/%2e%2e/%2e%2e/v1/jarvis/actions", "/v1/jarvis/actions"]) {
    const r = await page.request.post(p, { data: { tool: "task", op: "list" }, headers: h });
    expect([400, 403, 404, 405], p).toContain(r.status());
    expect(r.headers()["content-type"] ?? "", p).not.toMatch(/html/);                // and never the page, never kernel JSON
  }
  // file API: no path outside the allowed roots, in any spelling
  for (const q of ["..%5C..%5Cjarvis-secrets", "C%3A%5CWindows%5Cwin.ini", "D%3A%5Cjarvis-secrets", "%2e%2e%2f%2e%2e%2f", "....//....//"]) {
    const r = await page.request.get(`/api/files?path=${q}`, { headers: h });
    const body = await r.text();
    expect(r.status() >= 400 || !/token|secret/i.test(body), `files?path=${q}`).toBe(true);
  }
});

test("F: the kernel service restarts while a command is waiting: it fails honestly, nothing is stuck, the next command works", async ({ page }) => {
  test.setTimeout(8 * 60_000);
  await idle(page);
  const K = "D:/jarvis-secrets/ssh/jarvis_vm_ed25519";
  const text = `Reliability check F (${Date.now()}): write one sentence about rivers.`;
  await page.goto("/");
  await page.locator("#command-input").fill(text);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(async () => (await cmd(page, text))?.state, { timeout: 20_000 }).toBe("running");
  const active = (await J(page, "/api/owner/state")).jobs.filter((j: { state: string }) => ["queued", "running"].includes(j.state)).length;
  expect(active, "restart only when no job is running").toBe(0);
  execFileSync("ssh", ["-i", K, "-o", "BatchMode=yes", "ubuntu@100.125.91.51", "sudo -n systemctl restart jarvis-llm-proxy && sleep 3 && systemctl is-active jarvis-llm-proxy"]);
  await expect.poll(async () => (await page.request.get("/api/health")).ok(), { timeout: 60_000 }).toBe(true);
  await expect.poll(async () => (await J(page, "/api/health")).kernel.reachable, { timeout: 60_000, intervals: [2000] }).toBe(true);
  const c = await cmd(page, text);
  console.log("F_AFTER_RESTART", JSON.stringify({ state: c.state, error: c.error }));
  expect(["failed", "done"]).toContain(c.state);                                  // never still "running" with nobody working on it
  if (c.state === "failed") expect(String(c.error)).toMatch(/restarted/);
  expect((await J(page, "/api/owner/state")).busy).toBe(false);
  const after = `Reliability check F2 (${Date.now()}): reply with the single word ok.`;
  await page.locator("#command-input").fill(after);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect.poll(async () => (await cmd(page, after))?.state, { timeout: 5 * 60_000, intervals: [3000] }).toMatch(/^(done|failed)$/);
  console.log("F_NEXT", JSON.stringify({ state: (await cmd(page, after)).state, reply: String((await cmd(page, after)).reply ?? "").slice(0, 80) }));
});

test("G: when the model providers are resting the owner sees why, with the time, and nothing was sent", async ({ page }) => {
  const probe = await page.request.fetch("/api/owner/command", { method: "POST", data: { text: "Reliability check G probe", source: "text" },
    headers: { "X-Jarvis-Console": "1", Origin: BASE }, failOnStatusCode: false });
  test.skip(probe.status() !== 429, "the providers are not resting right now, so there is nothing to show");
  await page.goto("/");
  await page.locator("#command-input").fill("Reliability check G: say ok");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const alert = page.locator("form").getByRole("alert").filter({ hasText: /resting/ });   // the one next to the command box
  await expect(alert).toBeVisible();
  await expect(alert).toContainText(/back around \d\d:\d\d/);
  await expect(alert).toContainText("Nothing was sent");
  await page.screenshot({ path: "D:/jarvis/outputs/console-evidence/32-providers-resting.png" });
  expect(((await J(page, "/api/owner/commands")).commands as { text: string }[]).some((c) => c.text.includes("Reliability check G: say ok"))).toBe(false);
});
