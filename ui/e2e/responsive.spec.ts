import { test, expect } from "@playwright/test";

const SCREENS = ["command", "desktop", "agents", "tasks", "automations", "workspace", "memory", "models", "settings"];

test("no screen scrolls sideways at phone width, and navigation stays reachable", async ({ page }) => {
  for (const s of SCREENS) {
    await page.goto(`/#/${s}`);
    await page.waitForTimeout(250);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(over, `horizontal overflow on ${s}`).toBeLessThanOrEqual(1);
  }
  await page.goto("/");
  await expect(page.getByRole("navigation", { name: "Screens" }).getByRole("link", { name: /^Tasks/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Talk" })).toBeVisible();
});
