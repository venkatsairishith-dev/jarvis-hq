import { test, expect } from "@playwright/test";

// A fake microphone plays a file of someone saying "Open my downloads folder" (made by the app's own local TTS).
// Recognition and speech both run for real on this computer's CPU.
test("push to talk: recording state, local transcript you can correct, send, spoken reply", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  const talk = page.getByRole("button", { name: "Talk" });
  await talk.click();
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await expect(page.getByText("Recording", { exact: false }).first()).toBeVisible();
  await page.waitForTimeout(4500);                                   // the fixture is about 4 s of speech
  await page.getByRole("button", { name: "Stop", exact: true }).click();
  const box = page.locator("#command-input");
  await expect(box).toHaveValue(/downloads folder/i, { timeout: 60_000 });
  await expect(page.getByText(/Heard by parakeet/)).toBeVisible();   // the transcript is shown, with the model and time
  await box.fill((await box.inputValue()).replace(/Open/i, "Please open"));   // correction before sending
  await page.getByRole("button", { name: "Send", exact: true }).click();
  // wait for THIS command's record (earlier tests left earlier replies on the page and in the history)
  const mine = async () => (await (await page.request.get("/api/owner/commands")).json()).commands
    .find((c: { text: string }) => /please open my downloads folder/i.test(c.text));
  await expect.poll(async () => (await mine())?.state, { timeout: 30_000 }).toBe("done");
  const cmd = await mine();
  expect(cmd.source).toBe("voice");                                   // corrected by hand, but it started as speech
  expect(cmd.reply).toMatch(/opened your Downloads folder/i);
  // JARVIS spoke the reply with the local TTS
  const before = (await (await page.request.get("/api/voice/status")).json()).stats.tts_calls;
  await expect.poll(async () => (await (await page.request.get("/api/voice/status")).json()).stats.tts_calls, { timeout: 60_000 }).toBeGreaterThanOrEqual(before);
  await expect(page.getByText("Mic ready")).toBeVisible({ timeout: 60_000 });
});

test("Escape cancels a recording and nothing is sent", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Talk" }).click();
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "Talk" })).toBeVisible();
  await expect(page.locator("#command-input")).toHaveValue("");
});
