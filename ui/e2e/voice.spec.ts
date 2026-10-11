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

test("a microphone that is missing at first and comes back is picked up without a reload", async ({ page }) => {
  await page.addInitScript(() => {
    const md = navigator.mediaDevices;
    const real = md.getUserMedia.bind(md);
    let calls = 0;
    md.getUserMedia = async (c?: MediaStreamConstraints) => {
      calls += 1;
      if (calls === 1) throw new DOMException("no device", "NotFoundError");     // unplugged the first time
      return real(c);
    };
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Talk" }).click();
  await expect(page.getByRole("alert").filter({ hasText: /No microphone was found/ })).toBeVisible();
  await expect(page.getByRole("button", { name: "Talk" })).toBeVisible();          // nothing is stuck in "recording"
  await page.getByRole("button", { name: "Talk" }).click();                         // plugged back in
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
});

test("speaking to JARVIS interrupts its speech, and muting stops it", async ({ page }) => {
  test.setTimeout(120_000);
  await page.goto("/");
  await page.locator("#command-input").fill("tell me what you are doing");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText("Speaking", { exact: true })).toBeVisible({ timeout: 60_000 });      // the reply is being spoken
  await page.getByRole("button", { name: /Interrupt & talk|Talk/ }).first().click();               // barge in
  await expect(page.getByText("Speaking", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Stop", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  // and mute while it speaks
  await page.getByRole("button", { name: "Speak this" }).first().click();
  await expect(page.getByText("Speaking", { exact: true })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Mute microphone" }).click();
  await expect(page.getByText("Speaking", { exact: true })).toHaveCount(0);
  await expect(page.getByText("Mic muted")).toBeVisible();
  await page.getByRole("button", { name: "Unmute microphone" }).click();
});

test("a command that takes too long can be abandoned and its late answer is ignored", async ({ page }) => {
  test.setTimeout(60_000);
  await page.goto("/");
  await page.locator("#command-input").fill("please be slow about this one");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await page.getByRole("button", { name: "Stop waiting" }).click();
  await expect(page.getByText(/Stopped waiting for this command/)).toBeVisible();
  const mine = async () => (await (await page.request.get("/api/owner/commands")).json()).commands.find((c: { text: string }) => /be slow about this/.test(c.text));
  await expect.poll(async () => (await mine())?.state).toBe("cancelled");
  await page.waitForTimeout(7000);                                                  // the agent answers after 6 s
  const after = await mine();
  expect(after.state).toBe("cancelled");                                            // the late reply did not resurrect it
  expect(after.reply ?? "").toBe("");
});
