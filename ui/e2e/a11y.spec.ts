import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const SCREENS = ["command", "desktop", "agents", "tasks", "automations", "workspace", "memory", "models", "settings"];

for (const theme of ["dark", "light"]) {
  for (const s of SCREENS) {
    test(`${s} (${theme}) has no serious or critical accessibility violations`, async ({ page }) => {
      await page.addInitScript((t) => localStorage.setItem("jarvis.prefs.v1", JSON.stringify({ theme: t })), theme);
      await page.goto(`/#/${s}`);
      await page.waitForTimeout(500);
      const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
      const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
      expect(bad.map((v) => `${v.id}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
    });
  }
}

test("keyboard: tab order reaches the main controls", async ({ page }) => {
  await page.goto("/");
  const seen = new Set<string>();
  for (let i = 0; i < 30; i++) {
    await page.keyboard.press("Tab");
    seen.add(await page.evaluate(() => (document.activeElement?.getAttribute("aria-label") || document.activeElement?.textContent || "").trim().slice(0, 30)));
  }
  expect([...seen].some((x) => /Talk/.test(x))).toBe(true);
  expect([...seen].some((x) => /Mute microphone|Stop all/.test(x))).toBe(true);
  expect(await page.evaluate(() => document.getElementById("command-input") !== null)).toBe(true);
});
