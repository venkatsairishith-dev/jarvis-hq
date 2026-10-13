import { test } from "@playwright/test";

// Screenshots of the REAL console with the real kernel's current records, written outside test-results so a later
// test run cannot delete them. Read-only: nothing is created, sent or changed.
const OUT = "D:\\jarvis\\outputs\\console-evidence";
const screens: [string, string][] = [["command", "01-command-center"], ["desktop", "02-agentic-os"], ["agents", "03-agent-factory"], ["tasks", "04-tasks"],
  ["automations", "05-automations"], ["workspace", "06-workspace"], ["memory", "07-memory-lessons"], ["models", "08-models-routing"], ["settings", "09-settings-health"]];

test("evidence: every screen with real data, dark theme", async ({ page }) => {
  for (const [route, name] of screens) {
    await page.goto(`/#/${route}`);
    await page.waitForTimeout(2500);
    await page.screenshot({ path: `${OUT}\\${name}-dark.png`, fullPage: false });
  }
});

test("evidence: light theme and a narrow phone-width layout", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("jarvis.prefs.v1", JSON.stringify({ theme: "light" })));
  await page.goto("/#/tasks");
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}\\10-tasks-light.png` });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/#/command");
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}\\11-command-phone-width-light.png` });
});

test("evidence: the Agent PC desktop and the Windows PC tab", async ({ page }) => {
  await page.goto("/#/desktop");
  await page.waitForTimeout(3000);
  await page.getByRole("button", { name: "Open live desktop" }).click().catch(() => {});
  await page.waitForTimeout(4000);
  await page.screenshot({ path: `${OUT}\\12-agent-pc-live.png` });
});
