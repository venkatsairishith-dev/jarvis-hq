import { test, expect } from "@playwright/test";

// Runs last: the kill switch stays on for the rest of the throwaway kernel's life, exactly as designed.
test("the kill switch is one-way from the browser", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Kill", exact: true }).click();
  await page.getByRole("dialog", { name: "Turn the kill switch on?" }).getByRole("button", { name: "Kill switch ON" }).click();
  await expect(page.getByText("Kill switch ON").first()).toBeVisible();
  const r = await page.request.fetch("/api/owner/resume", { method: "POST", data: {}, headers: { "X-Jarvis-Console": "1" } });
  expect(r.status()).toBe(404);
  await page.locator("#command-input").fill("anything");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "kill switch is on" })).toBeVisible();
});
