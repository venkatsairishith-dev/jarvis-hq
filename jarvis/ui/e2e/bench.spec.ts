import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";

// Phone viewport (360x740, touch) from playwright.bench.config.ts. BENCH_SHOTS=<folder> also saves two screenshots there.
const SHOTS = process.env.BENCH_SHOTS;

async function noSidewaysScroll(page: Page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(over, "horizontal overflow").toBeLessThanOrEqual(1);
}

async function smallTargets(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const panel = document.querySelector("main");
    if (!panel) return ["no main"];
    const els = panel.querySelectorAll<HTMLElement>("button, [role=tab], summary, label:has(input), select, textarea");
    return [...els].filter((e) => e.offsetParent !== null).map((e) => ({ e, r: e.getBoundingClientRect() }))
      .filter(({ r }) => r.height < 44 || r.width < 44).map(({ e, r }) => `${e.tagName} "${(e.textContent || e.getAttribute("aria-label") || "").trim().slice(0, 30)}" ${Math.round(r.width)}x${Math.round(r.height)}`);
  });
}

async function axe(page: Page, where: string) {
  const r = await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  const bad = r.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(bad.map((v) => `${where} ${v.id}: ${v.nodes.slice(0, 2).map((n) => n.target.join(" ")).join(" | ")}`)).toEqual([]);
}

test("dashboard loads with readable cards, sample sizes and the official v1 score", async ({ page }) => {
  await page.goto("/");
  const nav = page.getByRole("navigation", { name: "Screens" });
  const box = await nav.boundingBox();
  expect(box && box.height, "the screen strip is not squashed on a phone").toBeGreaterThanOrEqual(40);
  await nav.getByRole("link", { name: "Bench" }).tap();
  await expect(page).toHaveURL(/#\/bench$/);
  await expect(page.getByRole("heading", { name: "Bench", level: 1 })).toBeVisible();
  await expect(page.getByText("Newest full run:")).toBeVisible();
  await expect(page.getByText("548 passed").first()).toBeVisible();
  await expect(page.getByText("24 deferred").first()).toBeVisible();
  const v1 = page.locator("section", { has: page.getByRole("heading", { name: "Official score (v1, frozen rules)" }) });
  await expect(v1.getByText("7.61")).toBeVisible();
  await expect(v1.getByText("official", { exact: true })).toBeVisible();
  await noSidewaysScroll(page);
  if (SHOTS) await page.screenshot({ path: path.join(SHOTS, "bench-dashboard-360x740.png") });

  const desktop = page.getByRole("button", { name: /^Desktop control/ });
  await desktop.scrollIntoViewIfNeeded();
  await expect(desktop).toContainText("9.0 / 10");
  await expect(desktop).toContainText("32/32 executed passed · 5 deferred · 37 cases");
  await expect(desktop).toContainText("Why not 10: critical cases deferred");
  await expect(desktop).toContainText("verified");
  // no score without its evidence: every dimension row names its sample and its supporting cases
  const rows = page.getByRole("button", { name: /supporting cases/ });
  expect(await rows.count()).toBe(16);
  for (const t of await rows.allTextContents()) expect(t).toMatch(/executed passed/);

  await page.getByRole("button", { name: /^Intent/ }).click();
  const list = page.getByRole("list", { name: "Cases for Intent" });
  await expect(list.getByText("A-001", { exact: true })).toBeVisible();
  await expect(list.getByText("mocked").first()).toBeVisible();
  await list.getByText("What was observed").first().click();
  await expect(list.getByText(/Observed:/).first()).toBeVisible();
  expect(await smallTargets(page)).toEqual([]);
  await noSidewaysScroll(page);
  await axe(page, "dashboard");
});

test("a run can be started, shows live progress, and can be cancelled", async ({ page }) => {
  await page.goto("/#/bench/run");
  await page.getByRole("checkbox", { name: /^Security/ }).check();
  await page.getByRole("checkbox", { name: /^Memory/ }).check();
  await page.getByRole("button", { name: /^Start/ }).click();
  const job = page.getByRole("region", { name: "Benchmark job" });
  await expect(job.getByText("running", { exact: true })).toBeVisible();
  await expect(job.getByText(/^[1-9]\d* of \d+ cases done · now F-\d{3}$/)).toBeVisible();
  await expect(job.getByLabel("Log tail")).toContainText("fake runner for F");
  expect(await smallTargets(page)).toEqual([]);
  await axe(page, "run (job running)");
  await job.getByRole("button", { name: "Cancel job" }).click();
  await expect(job.getByText("cancelled", { exact: true })).toBeVisible();
  await expect(job.getByRole("button", { name: "Cancel job" })).toHaveCount(0);
  // the next job may start once the first one is cancelled
  await expect(page.getByRole("button", { name: /^Start/ })).toBeEnabled();
  await noSidewaysScroll(page);
});

test("challenge lab grades six questions with evidence, saves untrusted, and deletes", async ({ page }) => {
  await page.goto("/#/bench/challenge");
  await page.getByLabel("Request to try").fill("open notepad please");
  await page.getByRole("button", { name: "Submit" }).click();
  const res = page.getByLabel("Challenge result");
  await expect(res.getByText("model not available, rules only")).toBeVisible();
  await expect(res.getByText("untrusted", { exact: true })).toBeVisible();
  for (const q of ["Understood?", "Right tools?", "Constraints respected?", "Verification step present?", "Completion honestly reported?", "Security boundaries respected?"])
    await expect(res.getByText(q)).toBeVisible();
  await expect(res.getByText("6/6 yes")).toBeVisible();
  await expect(res.getByText("YELLOW_AUTO").first()).toBeVisible();
  await expect(res.getByText(/SIMULATED/)).toBeVisible();
  await noSidewaysScroll(page);
  await axe(page, "challenge result");
  if (SHOTS) {
    await res.getByText("Understood?").scrollIntoViewIfNeeded();
    await page.locator("main").evaluate((m) => { const r = document.querySelector("[aria-label='Challenge result']"); if (r) m.scrollTop += r.getBoundingClientRect().top - 120; });
    await page.screenshot({ path: path.join(SHOTS, "bench-challenge-360x740.png") });
  }
  const del = page.getByRole("button", { name: /^Delete challenge open notepad/ });
  await expect(del).toHaveCount(1);
  await del.click();
  await expect(page.getByText("No saved challenges")).toBeVisible();
});

test("models and security tabs: only pinned candidates, nothing runs without a local file; security cases with status", async ({ page }) => {
  await page.goto("/#/bench/models");
  await expect(page.getByText("qwen3-4b-2507", { exact: true })).toBeVisible();
  await expect(page.getByText("not downloaded").first()).toBeVisible();
  await expect(page.getByRole("button", { name: /^Compare/ })).toBeDisabled();
  await expect(page.getByText("No model comparison has been run yet")).toBeVisible();
  await noSidewaysScroll(page);
  await axe(page, "models");
  await page.getByRole("tab", { name: "Security" }).click();
  await expect(page.getByRole("heading", { name: /^Security and reliability cases/ })).toBeVisible();
  await expect(page.getByText("K-001", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Deferred" }).click();
  await expect(page.getByText("deferred", { exact: true }).first()).toBeVisible();
  expect(await smallTargets(page)).toEqual([]);
  await noSidewaysScroll(page);
  await axe(page, "security");
});

test("light theme: no serious accessibility violations on the dashboard and run tabs", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("jarvis.prefs.v1", JSON.stringify({ theme: "light" })));
  for (const t of ["", "/run"]) {
    await page.goto(`/#/bench${t}`);
    await expect(page.getByRole("heading", { name: "Bench", level: 1 })).toBeVisible();
    await page.waitForTimeout(300);
    await axe(page, `light ${t || "dashboard"}`);
  }
});
