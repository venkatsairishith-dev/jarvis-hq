import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";

// The approval workflow through the real UI, the real console and a real (throwaway) kernel. An agent token (held by the
// test, as an agent would hold it) asks for actions; the owner decides in the browser; the agent then tries to use the
// decision. Nothing is mocked: every outcome is read back from the kernel.
const info = () => JSON.parse(fs.readFileSync("D:\\jarvis\\cache\\e2e-state.json", "utf-8")) as { kernel: number; agent_token: string };
const PIN = "correct horse";

async function agent(page: Page, body: Record<string, unknown>) {
  const i = info();
  const r = await page.request.fetch(`http://127.0.0.1:${i.kernel}/v1/jarvis/actions`, {
    method: "POST", data: body, failOnStatusCode: false, headers: { Authorization: `Bearer ${i.agent_token}`, "Content-Type": "application/json" },
  });
  return { status: r.status(), json: await r.json() };
}
const typeAction = (text: string, target = "notepad|Edit", approval_id?: string) =>
  ({ tool: "ui", op: "type", target, params: { text }, ...(approval_id ? { approval_id } : {}) });
const state = async (page: Page) => (await (await page.request.get("/api/owner/state")).json());

test.describe.configure({ mode: "serial" });

test("an agent asks; the UI shows the exact action; only the PIN and the typed id approve it; it runs once", async ({ page }) => {
  const text = "Dear team, the release is approved for Friday. " + "details ".repeat(30) + "END-OF-MESSAGE";
  const ask = await agent(page, typeAction(text));
  expect(ask.status).toBe(202);
  const aid = ask.json.approval_id as string;

  await page.goto("/");
  const card = page.locator("li", { hasText: aid.slice(0, 0) }).filter({ hasText: "ui.type" }).filter({ hasText: "END-OF-MESSAGE" }).first();
  await expect(card).toBeVisible();
  await expect(card.getByLabel("Exact arguments")).toContainText("Dear team, the release is approved for Friday.");
  await expect(card.getByLabel("Exact arguments")).toContainText("END-OF-MESSAGE");           // the whole value, not the first 200 characters
  await expect(card).toContainText("Why it asks:");
  await expect(card).toContainText(/digest [0-9a-f]{12}/);
  await expect(card).toContainText("notepad|Edit");

  // the agent cannot use it yet
  expect((await agent(page, typeAction(text, "notepad|Edit", aid))).status).not.toBe(200);

  await card.getByRole("button", { name: "Approve…" }).click();
  const dlg = page.getByRole("dialog", { name: "Approve this action" });
  await expect(dlg).toContainText("END-OF-MESSAGE");                                           // repeated in front of the owner at the PIN prompt
  await expect(dlg).toContainText("different action needs its own approval");
  await dlg.getByLabel(/Type the approval id/).fill(aid);
  await dlg.getByLabel("Approval PIN").fill("wrong pin value");
  await dlg.getByRole("button", { name: "Approve once" }).click();
  await expect(dlg.getByRole("alert")).toContainText("wrong PIN");
  await dlg.getByLabel("Approval PIN").fill(PIN);
  await dlg.getByRole("button", { name: "Approve once" }).click();
  await expect(page.locator("li", { hasText: "END-OF-MESSAGE" }).filter({ hasText: "Why it asks" })).toHaveCount(0);   // no longer pending

  // approved and still unused: the history says so
  await expect(page.locator("li", { hasText: "approved, not used yet" }).first()).toContainText("ui.type");   // newest first

  // a changed action does not ride on this approval
  const changed = await agent(page, typeAction(text + " (edited)", "notepad|Edit", aid));
  expect(changed.status).not.toBe(200);

  // the real action: used once
  const use = await agent(page, typeAction(text, "notepad|Edit", aid));
  expect(use.status).toBe(200);
  expect(use.json.status).toBe("queued");
  await expect(page.locator("li", { hasText: "approved and used once" }).first()).toContainText(`job ${use.json.job}`);

  // replay is refused and creates nothing
  const replay = await agent(page, typeAction(text, "notepad|Edit", aid));
  expect(replay.status).not.toBe(200);
  const jobs = (await state(page)).jobs.filter((j: { kind: string; id: string }) => j.kind === "ui_action");
  expect(jobs.filter((j: { id: string }) => j.id === use.json.job)).toHaveLength(1);
  expect(jobs).toHaveLength(1);
});

test("denial is final: nothing runs and the history says denied", async ({ page }) => {
  const ask = await agent(page, typeAction("Delete the quarterly figures", "excel|Sheet1"));
  const aid = ask.json.approval_id as string;
  await page.goto("/");
  const card = page.locator("li", { hasText: "Delete the quarterly figures" }).first();
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Deny" }).click();
  await expect(page.locator("li", { hasText: "denied: nothing ran" }).first()).toBeVisible();
  const after = await agent(page, typeAction("Delete the quarterly figures", "excel|Sheet1", aid));
  expect(after.status).not.toBe(200);
  const jobsBefore = (await state(page)).jobs.filter((j: { kind: string }) => j.kind === "ui_action").length;
  expect(jobsBefore).toBe(1);                                       // only the first test's job exists
});

test("the audit chain records the decisions and still verifies", async ({ page }) => {
  await page.goto("/#/settings");
  const ev = await (await page.request.get("/api/owner/events?since=0&limit=500")).json();
  const kinds = ev.events.map((e: { kind: string }) => e.kind);
  for (const k of ["request", "approved", "owner_approved_http", "owner_approve_bad_pin", "denied", "owner_denied_http", "authorize", "job_queued"]) {
    expect(kinds, k).toContain(k);
  }
  await page.getByRole("button", { name: "Verify the audit chain" }).click();
  await expect(page.getByText(/^ok: \d+ events/)).toBeVisible();
});
