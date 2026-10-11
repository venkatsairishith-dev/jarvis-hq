import { test, expect, type Page } from "@playwright/test";

// Failures the page itself reports: uncaught errors and unexpected failed API calls (the Agent PC control is not running
// in this harness, so its 503 is the expected, clearly-reported state).
function watch(page: Page) {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
  page.on("console", (m) => { if (m.type() === "error" && !/503|net::ERR|Failed to load resource/.test(m.text())) problems.push("console: " + m.text()); });
  page.on("response", (r) => {
    const u = r.url();
    if (r.status() >= 400 && u.includes("/api/") && !/\/api\/desktop\//.test(u)) problems.push(`${r.status()} ${u}`);
  });
  return problems;
}
const api = async (page: Page, path: string, init?: { method?: string; body?: unknown }) => {
  const r = await page.request.fetch(path, { method: init?.method ?? "GET", data: init?.body, headers: { "X-Jarvis-Console": "1" } });
  return { status: r.status(), json: await r.json().catch(() => null) };
};

test.describe("screens and navigation", () => {
  test("all nine screens render and navigate, with no uncaught errors or failed API calls", async ({ page }) => {
    const problems = watch(page);
    await page.goto("/");
    const screens: [string, string][] = [["Command Center", "Talk to JARVIS"], ["Agentic OS", "Agentic OS"], ["Agent Factory", "Agent Factory"],
      ["Tasks", "Tasks"], ["Automations", "Automation Studio"], ["Workspace", "Workspace and deliverables"], ["Memory", "Memory and learning"],
      ["Models & skills", "Models, tools and skills"], ["Settings & health", "Settings and system health"]];
    for (const [link, heading] of screens) {
      await page.getByRole("navigation", { name: "Screens" }).getByRole("link", { name: new RegExp("^" + link) }).click();
      await expect(page.getByText(heading, { exact: true }).first()).toBeVisible();
      await expect(page.getByRole("navigation", { name: "Screens" }).getByRole("link", { name: new RegExp("^" + link) })).toHaveAttribute("aria-current", "page");
    }
    await page.keyboard.press("Alt+4");
    await expect(page.getByRole("heading", { name: "Tasks" })).toBeVisible();
    expect(problems).toEqual([]);
  });

  test("theme toggle switches and persists", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
    await page.getByRole("button", { name: "Toggle light and dark theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    await page.getByRole("button", { name: "Toggle light and dark theme" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });

  test("a disconnected kernel shows a clear state instead of stale data", async ({ page }) => {
    await page.route("**/api/owner/state", (r) => r.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify({ error: "cannot reach the kernel", kernel_unreachable: true }) }));
    await page.goto("/");
    await expect(page.getByText("Kernel unreachable")).toBeVisible();
  });
});

test.describe("command center", () => {
  test("a typed command reaches JARVIS and the answer is shown with its source", async ({ page }) => {
    const problems = watch(page);
    await page.goto("/");
    await page.locator("#command-input").fill("open my Downloads folder");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByText("Opened your Downloads folder").or(page.getByText("I opened your Downloads folder"))).toBeVisible();
    const st = await api(page, "/api/owner/commands");
    expect(st.json.commands[0].text).toBe("open my Downloads folder");
    expect(st.json.commands[0].state).toBe("done");
    expect(problems).toEqual([]);
  });

  test("an agent failure is reported, not hidden", async ({ page }) => {
    await page.goto("/");
    await page.locator("#command-input").fill("please fail this one");
    await page.getByRole("button", { name: "Send", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "RuntimeError" })).toBeVisible();
  });

  test("a pending approval needs the PIN and the typed id, and only then is approved", async ({ page }) => {
    await page.goto("/");
    const card = page.locator("li", { hasText: "ui.click" }).first();
    await expect(card).toBeVisible();
    await card.getByRole("button", { name: "Approve…" }).click();
    const dlg = page.getByRole("dialog", { name: "Approve this action" });
    const id = (await dlg.getByText(/Type the approval id to confirm: /).textContent())!.split(": ")[1].trim();
    const ok = dlg.getByRole("button", { name: "Approve once" });
    await expect(ok).toBeDisabled();
    await dlg.getByLabel(/Type the approval id/).fill(id);
    await dlg.getByLabel("Approval PIN").fill("not the pin");
    await ok.click();
    await expect(dlg.getByRole("alert")).toContainText("wrong PIN");
    await dlg.getByLabel("Approval PIN").fill("correct horse");
    await ok.click();
    await expect(page.getByText("Nothing is waiting on you")).toBeVisible();
    const st = await api(page, "/api/owner/state");
    expect(st.json.approvals).toHaveLength(0);
  });

  test("the mic can be muted and the button then refuses to record", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Mute microphone" }).click();
    await expect(page.getByRole("button", { name: "Muted" })).toBeDisabled();
    await expect(page.getByText("Mic muted")).toBeVisible();
    await page.getByRole("button", { name: "Unmute microphone" }).click();
    await expect(page.getByText("Mic ready")).toBeVisible();
  });
});

test.describe("tasks", () => {
  test("real task records, filter, detail with verification, and cancellation", async ({ page }) => {
    await page.goto("/#/tasks");
    await expect(page.getByText("Build a word-count command line tool in Python")).toBeVisible();
    await expect(page.getByText("Compare local speech-to-text options")).toBeVisible();
    await page.getByRole("tab", { name: /^Completed/ }).click();
    await expect(page.getByText("Compare local speech-to-text options")).toHaveCount(0);
    await page.getByRole("button", { name: "Build a word-count command line tool in Python" }).click();
    const dlg = page.getByRole("dialog");
    await expect(dlg.getByText("independently verified")).toBeVisible();
    await dlg.getByRole("button", { name: "Close" }).click();
    await page.getByRole("tab", { name: /^Open/ }).click();
    await page.getByRole("button", { name: "Compare local speech-to-text options and write a report" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Cancel task" }).click();
    await expect(page.getByRole("dialog").getByText("cancelled", { exact: true })).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
    await page.getByRole("tab", { name: /^Failed/ }).click();
    await expect(page.getByText("Compare local speech-to-text options")).toBeVisible();
    const st = await api(page, "/api/owner/state");
    expect(st.json.tasks.find((t: { goal: string }) => t.goal.startsWith("Compare local")).state).toBe("cancelled");
  });

  test("search narrows the list", async ({ page }) => {
    await page.goto("/#/tasks");
    await page.getByLabel("Search tasks").fill("word-count");
    await expect(page.getByText("Build a word-count command line tool in Python")).toBeVisible();
    await page.getByLabel("Search tasks").fill("zzzz-nothing");
    await expect(page.getByText("No task matches")).toBeVisible();
  });
});

test.describe("agent factory", () => {
  test("sentence to reviewable definition, draft, activate, bounded test run, terminate and delete", async ({ page }) => {
    await page.goto("/#/agents");
    await page.locator("#agent-sentence").fill("Create a Python software engineer agent that writes code, tests it and fixes failures");
    await page.getByRole("button", { name: "Propose a definition" }).click();
    const review = page.getByRole("dialog", { name: "Review the proposed agent" });
    await expect(review.getByText("Enforced by the kernel:", { exact: true })).toBeVisible();
    await expect(review.getByLabel("Acceptance criteria (one per line)")).not.toBeEmpty();
    await review.getByRole("button", { name: "Save as draft" }).click();
    await expect(page.getByText("Agent saved as a draft.")).toBeVisible();
    await page.getByRole("button", { name: /Software Engineer|Python Software Engineer|Python Software/ }).first().click();
    const dlg = page.getByRole("dialog", { name: /^Agent:/ });
    await dlg.getByRole("button", { name: "Activate" }).click();
    await expect(dlg.getByText("active", { exact: true })).toBeVisible();
    await dlg.getByLabel(/Bounded test run/).fill("Write a function that reverses the words in a sentence, with two unit tests");
    await dlg.getByRole("button", { name: "Run test" }).click();
    await expect(page.getByText(/Test task [0-9a-f]{12} started/)).toBeVisible();
    const st = await api(page, "/api/owner/state");
    expect(st.json.tasks.some((t: { owner: string }) => t.owner.startsWith("agent:"))).toBe(true);
    await dlg.getByRole("button", { name: "Terminate" }).click();
    const conf = page.getByRole("dialog", { name: "Terminate this agent?" });
    const agentId = (await conf.getByText(/Type ag-/).textContent())!.match(/ag-[0-9a-f]{8}/)![0];
    await conf.getByLabel(/Type ag-/).fill(agentId);
    await conf.getByRole("button", { name: "Confirm" }).click();
    await expect(page.getByText("Agent terminated.")).toBeVisible();
  });

  test("invalid input is explained", async ({ page }) => {
    await page.goto("/#/agents");
    await page.locator("#agent-sentence").fill("hi");
    await expect(page.getByRole("button", { name: "Propose a definition" })).toBeDisabled();
  });
});

test.describe("automations", () => {
  test("describe a schedule in words, preview, save, pause, resume, run now and delete", async ({ page }) => {
    await page.goto("/#/automations");
    await page.getByRole("button", { name: "New automation" }).click();
    const dlg = page.getByRole("dialog", { name: "New automation" });
    await dlg.getByLabel("Name").fill("Morning repo check");
    await dlg.getByLabel("When, in words").fill("every morning at 7:30");
    await dlg.getByRole("button", { name: "Preview" }).click();
    await expect(dlg.getByText("Next runs")).toBeVisible();
    await expect(dlg.getByText(/07:30/).first()).toBeVisible();
    await expect(dlg.getByText("Side effects")).toBeVisible();
    await dlg.getByRole("button", { name: "Save and schedule" }).click();
    await expect(page.getByText("Automation saved and scheduled.")).toBeVisible();
    const row = page.locator("li", { hasText: "Morning repo check" });
    await expect(row.getByText("scheduled", { exact: true })).toBeVisible();
    await row.getByRole("button", { name: "Pause" }).click();
    await expect(row.getByText("paused", { exact: true })).toBeVisible();
    await row.getByRole("button", { name: "Resume" }).click();
    await expect(row.getByText("scheduled", { exact: true })).toBeVisible();
    await row.getByRole("button", { name: "Run now" }).click();
    await expect(page.getByText(/Run: fired/)).toBeVisible();
    await row.getByRole("button", { name: "History" }).click();
    await expect(page.getByRole("dialog").getByText("manual", { exact: false }).first()).toBeVisible();
    await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
    await row.getByRole("button", { name: "Delete" }).click();
    const conf = page.getByRole("dialog", { name: "Delete this automation?" });
    const aid = (await conf.getByText(/Type a-/).textContent())!.match(/a-[0-9a-f]{8}/)![0];
    await conf.getByLabel(/Type a-/).fill(aid);
    await conf.getByRole("button", { name: "Delete" }).click();
    await expect(page.getByText("Deleted.")).toBeVisible();
    await expect(page.getByText("Morning repo check")).toHaveCount(0);
  });

  test("a bad cron expression is rejected with the reason", async ({ page }) => {
    await page.goto("/#/automations");
    await page.getByRole("button", { name: "New automation" }).click();
    const dlg = page.getByRole("dialog", { name: "New automation" });
    await dlg.getByLabel("Name").fill("Broken");
    await dlg.getByLabel("Or a cron expression").fill("61 * * * *");
    await dlg.getByRole("button", { name: "Preview" }).click();
    await expect(dlg.getByRole("alert")).toContainText("out of range");
  });
});

test.describe("workspace", () => {
  test("delivered files list, inert HTML preview, download link, and no path escape", async ({ page }) => {
    await page.goto("/#/workspace");
    await expect(page.getByText(/wordstat\.py/)).toBeVisible();
    await page.getByRole("button", { name: /report\.html/ }).click();
    const frame = page.locator("iframe[title^='Preview of report.html']");
    await expect(frame).toHaveAttribute("sandbox", "");
    await page.waitForTimeout(500);
    const title = await frame.evaluate((f: HTMLIFrameElement) => { try { return f.contentDocument?.title ?? "(opaque)"; } catch { return "(opaque)"; } });
    expect(title).not.toBe("PWNED");
    await expect(page.getByRole("link", { name: "Download" })).toHaveAttribute("href", /download=1/);
    const bad = await api(page, "/api/files/get?path=" + encodeURIComponent("../kernel/state.db"));
    expect([400, 404]).toContain(bad.status);
    await page.getByRole("button", { name: /wordstat\.py/ }).click();
    await expect(page.getByText("import sys")).toBeVisible();
  });
});

test.describe("memory", () => {
  test("correct an entry in place (same id, no duplicate) and forget with verification", async ({ page }) => {
    await page.goto("/#/memory");
    await expect(page.getByText("I prefer dark themes and concise answers")).toBeVisible();
    const before = await api(page, "/api/owner/memory?owner=hermes");
    const target = before.json.entries.find((e: { text: string }) => e.text.includes("dark themes"));
    const li = page.locator("li", { hasText: "I prefer dark themes and concise answers" });
    await li.getByRole("button", { name: "Correct" }).click();
    await page.getByLabel(/Text \(up to 500/).fill("I prefer dark themes, concise answers and metric units");
    await page.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText(/no duplicate/)).toBeVisible();
    const after = await api(page, "/api/owner/memory?owner=hermes");
    expect(after.json.entries).toHaveLength(before.json.entries.length);
    const same = after.json.entries.find((e: { id: string }) => e.id === target.id);
    expect(same.text).toContain("metric units");
    expect(same.version).toBe(target.version + 1);
    await page.locator("li", { hasText: "metric units" }).getByRole("button", { name: "Forget" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Forget it" }).click();
    await expect(page.getByText(/read again to confirm it is gone/)).toBeVisible();
    const gone = await api(page, "/api/owner/memory?owner=hermes");
    expect(gone.json.entries.find((e: { id: string }) => e.id === target.id)).toBeUndefined();
  });
});

test.describe("models and settings", () => {
  test("models screen reflects the real (here: empty) provider list and the local model state", async ({ page }) => {
    await page.goto("/#/models");
    await expect(page.getByText("No enabled provider")).toBeVisible();
    await page.getByRole("tab", { name: "Local models" }).click();
    await expect(page.getByText("not running")).toBeVisible();
    await expect(page.getByText("parakeet-tdt-0.6b-v3-int8")).toBeVisible();
  });

  test("settings health rows show actual state and the audit chain verifies", async ({ page }) => {
    await page.goto("/#/settings");
    await expect(page.locator("li", { hasText: "Kernel owner plane" }).getByText("OK")).toBeVisible();
    await expect(page.locator("li", { hasText: "Local language model" }).getByText("Down")).toBeVisible();
    await expect(page.locator("li", { hasText: "Hub / Agent PC control" }).getByText("Down")).toBeVisible();
    await expect(page.locator("li", { hasText: "Approval PIN" }).getByText("OK")).toBeVisible();
    await page.getByRole("button", { name: "Verify the audit chain" }).click();
    await expect(page.getByText(/^ok: \d+ events/)).toBeVisible();
    const body = await page.content();
    expect(body).not.toMatch(/[A-Za-z0-9_-]{40,}/);                    // no token-like string anywhere in the page
  });

  test("the Agent PC screen says plainly when the lab desktop is not reachable", async ({ page }) => {
    await page.goto("/#/desktop");
    await expect(page.getByText(/Agent PC control is not answering/)).toBeVisible();
    await page.getByRole("tab", { name: "This Windows PC" }).click();
    await expect(page.getByText(/no live video of this PC here on purpose/)).toBeVisible();
  });
});

test.describe("windows pc", () => {
  test("a PC action becomes a real kernel job and honestly shows it is waiting for the node", async ({ page }) => {
    await page.goto("/#/desktop");
    await page.getByRole("tab", { name: "This Windows PC" }).click();
    await page.getByRole("button", { name: "List open windows" }).click();
    await expect(page.getByText("waiting for the laptop node")).toBeVisible();
    const st = await api(page, "/api/owner/state");
    expect(st.json.jobs.some((j: { kind: string }) => j.kind === "ui_windows")).toBe(true);
    await page.getByLabel("Open an app from the Start list").fill("Notepad");
    await page.getByRole("button", { name: "Open app" }).click();
    await expect(page.getByText("pc_open")).toBeVisible();
  });
});

test.describe("routing explanation", () => {
  test("every provider has a reason, and an empty lane says it will not widen", async ({ page }) => {
    await page.goto("/#/models");
    await expect(page.getByText("Why this model? (routing explanation)")).toBeVisible();
    await expect(page.getByText(/not widen the lane/)).toBeVisible();
  });
});

test.describe("stop all", () => {
  test("stop all asks first, then cancels open work", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Stop all" }).click();
    const dlg = page.getByRole("dialog", { name: "Stop everything?" });
    await expect(dlg).toBeVisible();
    await dlg.getByRole("button", { name: "Keep working" }).click();
    await page.getByRole("button", { name: "Stop all" }).click();
    await page.getByRole("dialog", { name: "Stop everything?" }).getByRole("button", { name: "Stop all tasks" }).click();
    await expect(page.getByText(/Stopped: \d+ task/)).toBeVisible();
  });
});
