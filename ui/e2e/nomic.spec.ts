import { test, expect } from "@playwright/test";

// No microphone device and no permission grant: the page must say so and typing must keep working.
test("a missing or blocked microphone is explained and the text path still works", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Talk" }).click();
  await expect(page.getByRole("alert").filter({ hasText: /microphone/i }).first()).toBeVisible();
  await page.locator("#command-input").fill("what are you working on right now?");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText(/opened your Downloads folder/i).first()).toBeVisible();
});
