import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import { execFileSync } from "node:child_process";

// Live approval workflow on the real kernel. A throwaway PUBLIC-lane test agent asks for a harmless, reversible action
// (launch Notepad). Part A needs no PIN (denial). Part B needs the owner's approval PIN in JARVIS_APPROVAL_PIN, supplied at
// run time by the owner and never stored or printed; without it Part B is skipped, not faked.
const KERNEL = "http://100.125.91.51:8790";
const TOKEN_FILE = "D:\\jarvis-secrets\\kernel\\approval-test-agent.token";
const token = () => fs.readFileSync(TOKEN_FILE, "utf-8").trim();
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

async function agent(page: Page, body: Record<string, unknown>) {
  const r = await page.request.fetch(`${KERNEL}/v1/jarvis/actions`, {
    method: "POST", data: body, failOnStatusCode: false, headers: { Authorization: `Bearer ${token()}`, "Content-Type": "application/json" },
  });
  return { status: r.status(), json: await r.json() };
}
const launch = (approval_id?: string) => ({ tool: "ui", op: "launch", target: "notepad", params: {}, ...(approval_id ? { approval_id } : {}) });
const state = async (page: Page) => (await page.request.get("/api/owner/state")).json();
const notepadPids = () => {
  try {
    return execFileSync("powershell", ["-NoProfile", "-Command", "(Get-Process notepad -ErrorAction SilentlyContinue).Id -join ','"], { encoding: "utf-8" })
      .trim().split(",").filter(Boolean).map(Number);
  } catch { return []; }
};

test.describe.configure({ mode: "serial" });

test("A: a pending approval shows the exact action on the real UI, and denial is final", async ({ page }) => {
  const ask = await agent(page, launch());
  console.log("ASK", JSON.stringify({ status: ask.status, level: ask.json.level, reason: String(ask.json.next ?? "").slice(0, 60) }));
  expect(ask.status).toBe(202);
  const aid = ask.json.approval_id as string;
  await page.goto("/");
  const card = page.locator("li", { hasText: "ui.launch" }).first();
  await expect(card).toBeVisible({ timeout: 30_000 });
  await expect(card).toContainText("notepad");
  await expect(card).toContainText("Why it asks:");
  await page.screenshot({ path: "D:\\jarvis\\outputs\\console-evidence\\20-live-approval-pending.png" });
  const before = (await state(page)).jobs.length;
  await card.getByRole("button", { name: "Deny" }).click();
  await expect(page.locator("li", { hasText: "denied: nothing ran" }).first()).toBeVisible({ timeout: 30_000 });
  await page.screenshot({ path: "D:\\jarvis\\outputs\\console-evidence\\21-live-approval-denied.png" });
  const after = await agent(page, launch(aid));
  console.log("RESEND_AFTER_DENY", after.status);
  expect(after.status).not.toBe(200);
  const st = await state(page);
  expect(st.jobs.length).toBe(before);                                            // nothing was queued
  expect(st.approval_history.find((h: { id: string }) => h.id === aid).state).toBe("denied");
});

test("B: with the owner's PIN the approval is single use and the outcome is read back", async ({ page }) => {
  const pin = process.env.JARVIS_APPROVAL_PIN;
  test.skip(!pin, "JARVIS_APPROVAL_PIN is not set: only the owner can set the approval PIN, so approving was not exercised live");
  test.setTimeout(5 * 60_000);
  const known = new Set(notepadPids());
  const ask = await agent(page, launch());
  expect(ask.status).toBe(202);
  const aid = ask.json.approval_id as string;
  await page.goto("/");
  const card = page.locator("li", { hasText: "ui.launch" }).first();
  await expect(card).toBeVisible({ timeout: 30_000 });
  await card.getByRole("button", { name: "Approve…" }).click();
  const dlg = page.getByRole("dialog", { name: "Approve this action" });
  await dlg.getByLabel(/Type the approval id/).fill(aid);
  await dlg.getByLabel("Approval PIN").fill(pin!);
  await dlg.getByRole("button", { name: "Approve once" }).click();
  await expect(page.locator("li", { hasText: "approved, not used yet" }).first()).toBeVisible({ timeout: 30_000 });
  const use = await agent(page, launch(aid));
  console.log("USE", JSON.stringify({ status: use.status, job: use.json.job }));
  expect(use.json.status).toBe("queued");
  // the node's own answer is the authoritative outcome
  let job: { state: string; result: Record<string, unknown> | null } | undefined;
  for (let i = 0; i < 40; i++) {
    job = await (await page.request.get(`/api/owner/job/${use.json.job}`)).json();
    if (job && ["done", "failed", "expired"].includes(job.state)) break;
    await sleep(3000);
  }
  console.log("JOB", JSON.stringify(job));
  expect(job?.state).toBe("done");
  await expect(page.locator("li", { hasText: "approved and used once" }).first()).toContainText(`job ${use.json.job}`, { timeout: 30_000 });
  await page.screenshot({ path: "D:\\jarvis\\outputs\\console-evidence\\22-live-approval-used.png" });
  const replay = await agent(page, launch(aid));
  expect(replay.status).not.toBe(200);
  // reversible: close only the Notepad windows this test opened
  for (const pid of notepadPids().filter((p) => !known.has(p))) {
    try { execFileSync("powershell", ["-NoProfile", "-Command", `Stop-Process -Id ${pid}`]); } catch { /* already closed */ }
  }
});
