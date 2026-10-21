import { test, expect, type Page } from "@playwright/test";
import { consoleHeaders } from "../e2e/console-secret";

// Live: preferences are added, corrected and forgotten through the real UI; the kernel's own list is read back each time.
// A correction made from a stale copy is refused (409), never silently applied. The audit chain still verifies at the end.
const J = async (page: Page, p: string) => (await page.request.get(p)).json();
const P = async (page: Page, p: string, body: unknown) =>
  page.request.fetch(p, { method: "POST", data: body, headers: await consoleHeaders(page, { Origin: "http://127.0.0.1:8799" }) });

test.describe.configure({ mode: "serial" });
test.beforeAll(async ({ request }) => {                                                 // leftovers of an interrupted run
  const h = await consoleHeaders(request, { Origin: "http://127.0.0.1:8799" });
  const m = await (await request.get("/api/owner/memory")).json();
  for (const e of m.entries ?? []) if (String(e.text).startsWith("Live test marker")) await request.post("/api/owner/memory", { data: { op: "forget", owner: m.owner, id: e.id }, headers: h });
});
let who = "";
let id = "";
let v1 = 0;
const OLD = "Live test marker: the owner's favourite test colour is teal.";
const NEW = "Live test marker: the owner's favourite test colour is indigo.";

const listing = async (page: Page) => (await J(page, `/api/owner/memory?owner=${encodeURIComponent(who)}`)).entries as { id: string; text: string; version: number }[];

test("add: the preference appears in the UI and in the kernel's list", async ({ page }) => {
  await page.goto("/#/memory");
  who = (await J(page, "/api/owner/memory")).owner;
  expect(who).toBeTruthy();
  await page.getByRole("button", { name: "Add a preference" }).click();
  await page.getByRole("dialog").getByRole("textbox").fill(OLD);
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(OLD)).toBeVisible();
  await expect.poll(async () => (await listing(page)).some((e) => e.text === OLD), { message: "the kernel lists it", timeout: 15_000 }).toBe(true);
  const mine = (await listing(page)).find((e) => e.text === OLD)!;
  id = mine.id; v1 = mine.version;
});

test("correct: the new text replaces the old one, the version moves, and a stale correction is refused", async ({ page }) => {
  await page.goto("/#/memory");
  await page.locator("li", { hasText: OLD }).getByRole("button", { name: "Correct" }).click();
  await page.getByRole("dialog").getByRole("textbox").fill(NEW);
  await page.getByRole("dialog").getByRole("button", { name: "Save" }).click();
  await expect(page.getByText(NEW)).toBeVisible();
  await expect(page.locator("li", { hasText: NEW }).locator("p").first()).toHaveText(NEW);       // the entry itself now says the new thing
  const now = await listing(page);
  expect(now.some((e) => e.text === OLD)).toBe(false);
  const cur = now.find((e) => e.id === id)!;
  expect(cur.text).toBe(NEW);
  expect(cur.version).toBeGreaterThan(v1);
  // a second window holding the old version tries to overwrite: refused, nothing changes
  const stale = await P(page, "/api/owner/memory", { op: "update", owner: who, id, text: "stale overwrite attempt", expected_version: v1 });
  expect(stale.status()).toBe(409);
  expect((await listing(page)).find((e) => e.id === id)!.text).toBe(NEW);
});

test("forget: it disappears from the list and a later change to it is refused", async ({ page }) => {
  await page.goto("/#/memory");
  await page.locator("li", { hasText: NEW }).getByRole("button", { name: "Forget" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "Forget it" }).click();
  await expect(page.getByText(NEW)).toHaveCount(0);
  expect((await listing(page)).some((e) => e.id === id)).toBe(false);
  await expect(page.getByText(OLD)).toHaveCount(0);                                     // erased: the earlier version is gone too
  const everything = JSON.stringify(await J(page, `/api/owner/memory?owner=${encodeURIComponent(who)}`));
  expect(everything.includes("favourite test colour")).toBe(false);
  const again = await P(page, "/api/owner/memory", { op: "update", owner: who, id, text: "resurrect" });
  expect(again.status()).toBeGreaterThanOrEqual(400);
  expect((await listing(page)).some((e) => e.id === id)).toBe(false);
  const v = await J(page, "/api/owner/audit/verify");
  console.log("AUDIT_VERIFY", JSON.stringify(v));
  expect(v.ok).toBe(true);
});
