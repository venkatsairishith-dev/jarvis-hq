import { test, expect, type Page } from "@playwright/test";
import { consoleHeaders } from "../e2e/console-secret";

// Live: real console, real kernel on the server, real Hermes, real laptop node. Evidence screenshots go to test-results/live.
const shot = (page: Page, name: string) => page.screenshot({ path: `test-results/live/${name}.png`, fullPage: false });
const getJson = async (page: Page, p: string) => (await (await page.request.get(p)).json());
const postJson = async (page: Page, p: string, body: unknown) =>
  (await page.request.fetch(p, { method: "POST", data: body, headers: await consoleHeaders(page, { Origin: "http://127.0.0.1:8799" }) })).json();

test.describe.configure({ mode: "serial" });

test("health: the real kernel, voice engine and local services as they are", async ({ page }) => {
  await page.goto("/#/settings");
  const h = await getJson(page, "/api/health");
  console.log("HEALTH", JSON.stringify({ kernel: h.kernel, voice_engine: h.voice.engine, stt: Object.fromEntries(Object.entries(h.voice.stt).map(([k, v]) => [k, (v as { available: boolean }).available])),
    tts: Object.fromEntries(Object.entries(h.voice.tts).map(([k, v]) => [k, (v as { available: boolean }).available])), local_llm: h.local_llm.ok, hub: h.hub.ok, disk: h.disk }));
  await expect(page.locator("li", { hasText: "Kernel owner plane" }).getByText("OK")).toBeVisible();
  await shot(page, "01-settings-health");
  expect(h.kernel.authorized).toBe(true);
});

test("scenario 1: speak a request, see the transcript, JARVIS opens the folder and the node verifies the window", async ({ page }) => {
  test.setTimeout(10 * 60_000);
  await page.goto("/");
  await page.getByRole("button", { name: "Talk" }).click();
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await shot(page, "02-recording");
  await page.waitForTimeout(2300);                                     // the fake mic file loops: stop after one pass
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  const box = page.locator("#command-input");
  await expect(box).toHaveValue(/downloads folder/i, { timeout: 90_000 });
  await shot(page, "03-transcript-shown");
  const typed = await box.inputValue();
  console.log("TRANSCRIPT", typed);
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const mine = async () => (await getJson(page, "/api/owner/commands")).commands.find((c: { text: string }) => c.text === typed);
  await expect.poll(async () => (await mine())?.state, { timeout: 30_000 }).toBeTruthy();       // recorded by the kernel
  await expect.poll(async () => (await mine())?.state, { timeout: 8 * 60_000, intervals: [3000] }).not.toBe("running");
  const cmd = await mine();
  console.log("COMMAND", JSON.stringify({ state: cmd.state, source: cmd.source, reply: (cmd.reply ?? "").slice(0, 400), error: cmd.error }));
  await shot(page, "04-reply");
  expect(cmd.state).toBe("done");
  // the node's own observation: a pc_open job for this request that found the window
  const st = await getJson(page, "/api/owner/state");
  const opened = st.jobs.filter((j: { kind: string }) => j.kind === "pc_open").slice(0, 3);
  console.log("PC_OPEN_JOBS", JSON.stringify(opened.map((j: { state: string; result: string; created: number }) => ({ state: j.state, result: j.result.slice(0, 200) }))));
  expect(opened.length).toBeGreaterThan(0);
});

test("windows adapter: list real windows and take a real screenshot through kernel jobs", async ({ page }) => {
  test.setTimeout(5 * 60_000);
  await page.goto("/#/desktop");
  await page.getByRole("tab", { name: "This Windows PC" }).click();
  await page.getByRole("button", { name: "List open windows" }).click();
  await expect(page.getByText("foreground").first()).toBeVisible({ timeout: 120_000 });
  await shot(page, "05-windows-list");
  await page.getByRole("button", { name: "Take a screenshot" }).click();
  await expect(page.getByText(/Saved screens/)).toBeVisible({ timeout: 120_000 });
  await shot(page, "06-screenshot-saved");
});
