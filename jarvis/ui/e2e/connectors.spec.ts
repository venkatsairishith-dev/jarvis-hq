import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

// The connector workflows through the real UI, console, kernel and a real MCP server process (a deterministic fake, started by
// the node's own connector code from the owner's local allowlist). Nothing is mocked: every state shown is read back from the kernel.
const info = () => JSON.parse(fs.readFileSync("D:\\jarvis\\cache\\e2e-state.json", "utf-8")) as
  { kernel: number; agent_token: string; secrets_dir: string; fixture: string; python: string; connectors_file: string };

async function agentCall(page: Page, body: Record<string, unknown>) {
  const i = info();
  const r = await page.request.fetch(`http://127.0.0.1:${i.kernel}/v1/jarvis/actions`, {
    method: "POST", data: body, failOnStatusCode: false, headers: { Authorization: `Bearer ${i.agent_token}`, "Content-Type": "application/json" },
  });
  return { status: r.status(), json: await r.json() };
}
const call = (tool: string, args: Record<string, unknown>, approval_id?: string) =>
  ({ tool: "connector", op: "call", target: `fake.${tool}`, params: { arguments: args }, ...(approval_id ? { approval_id } : {}) });
const owner = async (page: Page, p: string) => (await page.request.get(`/api/owner/${p}`)).json();

test.describe.configure({ mode: "serial" });

test("an empty catalogue explains itself, and Alt+0 opens the screen", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("Alt+0");
  await expect(page.getByRole("heading", { name: "Connectors", level: 1 })).toBeVisible();
  await expect(page.getByText("No connectors yet")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Screens" }).getByRole("link", { name: /^Connectors/ })).toHaveAttribute("aria-current", "page");
});

test("a program that is not allowed is refused in the form and nothing is added", async ({ page }) => {
  await page.goto("/#/connectors");
  await page.getByRole("button", { name: "Add connector" }).first().click();
  const dlg = page.getByRole("dialog", { name: "Add connector" });
  await dlg.getByLabel(/^Name/).fill("evil");
  await dlg.getByLabel(/^Program/).fill("C:\\Windows\\System32\\cmd.exe");
  await dlg.getByLabel("Arguments (one per line)").fill("/c\ncalc");
  await dlg.getByRole("button", { name: "Add connector" }).click();
  await expect(dlg.getByRole("alert")).toContainText(/shell|Windows folder/);
  await dlg.getByRole("button", { name: "Cancel" }).click();
  const list = await owner(page, "connectors");
  expect(list.connectors).toEqual([]);
  expect(fs.existsSync(info().connectors_file)).toBe(false);
});

test("add, test (missing secret is a setup problem), fix, test again: tools appear switched off and unclassified", async ({ page }) => {
  const i = info();
  await page.goto("/#/connectors");
  await page.getByRole("button", { name: "Add connector" }).first().click();
  const dlg = page.getByRole("dialog", { name: "Add connector" });
  await dlg.getByLabel(/^Name/).fill("fake");
  await dlg.getByLabel(/^Label/).fill("Fake server");
  await dlg.getByLabel("What it is for").fill("A deterministic test server");
  await dlg.getByLabel(/^Program/).fill(i.python);
  await dlg.getByLabel("Arguments (one per line)").fill(i.fixture);
  await dlg.getByLabel(/Credentials as secret-file references/).fill("FAKE_TOKEN=connectors/fake.token");
  await dlg.getByRole("button", { name: "Add connector" }).click();
  await expect(page.getByText("Added. Press Test connection")).toBeVisible();
  await page.getByRole("button", { name: "Open Fake server" }).click();
  await expect(page.getByText("Configured, not tested").first()).toBeVisible();
  await expect(page.getByText("secret file missing")).toBeVisible();                              // a boolean, never a value

  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.locator("#main").getByRole("alert").filter({ hasText: /secret file/ })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByText("Needs setup").first()).toBeVisible();

  fs.writeFileSync(path.join(i.secrets_dir, "connectors", "fake.token"), "E2E-CANARY-SECRET-5551212");
  await page.reload();
  await page.getByRole("button", { name: "Open Fake server" }).click();
  await expect(page.getByText("secret file present")).toBeVisible();
  await page.getByRole("button", { name: "Test connection" }).click();
  await expect(page.getByText(/Connection works: \d+ tool/)).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("table", { name: /Tools of Fake server/ })).toBeVisible();
  const row = (t: string) => page.getByRole("row").filter({ has: page.locator("span.font-mono", { hasText: new RegExp("^" + t + "$") }) });
  await expect(row("echo").getByRole("combobox")).toHaveValue("unknown");
  await expect(row("echo").getByRole("checkbox")).toBeDisabled();                                // unclassified cannot be switched on
  await expect(row("delete_all")).toContainText("claims readOnly");                              // the server's lie is shown as a claim
  await expect(row("delete_all")).toContainText("name suggests destructive");
  const state = JSON.stringify(await owner(page, "connectors"));
  expect(state).not.toContain("E2E-CANARY-SECRET-5551212");                                      // the value is nowhere in the kernel's view
  const local = JSON.stringify(await (await page.request.get("/api/connectors/local")).json());
  expect(local).not.toContain("E2E-CANARY-SECRET-5551212");
});

test("classify, enable, try a read tool; destructive tools cannot be switched on", async ({ page }) => {
  await page.goto("/#/connectors");
  await page.getByRole("button", { name: "Open Fake server" }).click();
  const row = (t: string) => page.getByRole("row").filter({ has: page.locator("span.font-mono", { hasText: new RegExp("^" + t + "$") }) });
  await row("echo").getByRole("combobox").selectOption("read");
  await expect(row("echo").getByRole("checkbox")).toBeEnabled();
  await row("echo").getByRole("checkbox").check();
  await expect(row("echo").getByText("on", { exact: true })).toBeVisible();
  await row("create_note").getByRole("combobox").selectOption("write");
  await row("create_note").getByRole("checkbox").check();
  await expect(row("create_note")).toContainText("Needs your approval of the exact arguments");
  await row("delete_all").getByRole("combobox").selectOption("destructive");
  await expect(row("delete_all").getByRole("checkbox")).toBeDisabled();
  await expect(row("delete_all")).toContainText("Can never be enabled");

  await row("echo").getByRole("button", { name: "Try…" }).click();
  const dlg = page.getByRole("dialog", { name: "Try echo" });
  await dlg.getByLabel("Arguments (JSON)").fill('{"text": "hello from the owner"}');
  await dlg.getByRole("button", { name: "Run this read tool" }).click();
  await expect(dlg.getByLabel("Result")).toContainText("hello from the owner", { timeout: 30_000 });
  await expect(dlg.getByText(/data from an external service/)).toBeVisible();
  await dlg.getByRole("button", { name: "Close" }).click();
});

test("an agent's read runs; its write waits in the Command Center with the exact arguments, runs once after approval", async ({ page }) => {
  const read = await agentCall(page, call("echo", { text: "agent read" }));
  expect(read.status).toBe(200);
  expect(read.json.result.untrusted).toBe(true);
  expect(read.json.result.text).toContain("agent read");

  const args = { title: "Release plan", body: "ship on Friday, notify the team" };
  const ask = await agentCall(page, call("create_note", args));
  expect(ask.status).toBe(202);
  const aid = ask.json.approval_id as string;
  await page.goto("/");
  const card = page.locator("li", { hasText: "connector.call" }).filter({ hasText: "Release plan" }).first();
  await expect(card).toBeVisible();
  await expect(card.getByLabel("Exact arguments")).toContainText("ship on Friday, notify the team");
  expect((await agentCall(page, call("create_note", args, aid))).status).not.toBe(200);          // not approved yet

  await card.getByRole("button", { name: "Approve…" }).click();
  const dlg = page.getByRole("dialog", { name: "Approve this action" });
  await dlg.getByLabel(/Type the approval id/).fill(aid);
  await dlg.getByLabel("Approval PIN").fill("correct horse");
  await dlg.getByRole("button", { name: "Approve once" }).click();
  await expect(page.locator("li", { hasText: "approved, not used yet" }).first()).toBeVisible();

  expect((await agentCall(page, call("create_note", { ...args, body: "changed" }, aid))).status).not.toBe(200);   // different arguments
  const done = await agentCall(page, call("create_note", args, aid));
  expect(done.status).toBe(200);
  expect(done.json.result.text).toContain("note created: Release plan");
  expect((await agentCall(page, call("create_note", args, aid))).status).not.toBe(200);          // replay
});

test("recent calls list what happened, without the content", async ({ page }) => {
  await page.goto("/#/connectors");
  await expect(page.getByRole("table", { name: "Recent connector calls" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "fake.echo" }).first()).toContainText("read");
  await expect(page.getByRole("row").filter({ hasText: "fake.create_note" }).first()).toContainText("write");
  await expect(page.getByText("ship on Friday")).toHaveCount(0);
});

test("connect and disconnect are reflected, and a disabled connector refuses every agent", async ({ page }) => {
  await page.goto("/#/connectors");
  await page.getByRole("button", { name: "Open Fake server" }).click();
  await page.getByRole("button", { name: "Connect", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Fake server/ }).getByText("Connected")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Disconnect" }).click();
  await expect(page.getByRole("heading", { name: /Fake server/ }).getByText("Installed, not running")).toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "Disable", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Fake server/ }).getByText("Disabled")).toBeVisible();
  const refused = await agentCall(page, call("echo", { text: "x" }));
  expect(refused.status).toBe(403);
  await page.getByRole("button", { name: "Enable", exact: true }).click();
  await expect(page.getByRole("heading", { name: /Fake server/ }).getByText("Disabled")).toHaveCount(0);       // read back from the kernel
  expect((await agentCall(page, call("echo", { text: "x" }))).status).toBe(200);
});

test("filters and search narrow the catalogue; an unreachable program is shown as degraded or unconfigured, not connected", async ({ page }) => {
  await page.goto("/#/connectors");
  await page.getByLabel("Search connectors").fill("nothing-like-this");
  await expect(page.getByText("Nothing matches")).toBeVisible();
  await page.getByLabel("Search connectors").fill("");
  await page.getByRole("button", { name: "Disabled", exact: true }).click();
  await expect(page.getByText("Nothing matches")).toBeVisible();
  await page.getByRole("button", { name: "All", exact: true }).click();

  // break the program path in the owner's allowlist, as if it had been uninstalled
  const f = info().connectors_file;
  const saved = fs.readFileSync(f, "utf-8");
  const broken = JSON.parse(saved);
  broken.connectors.fake.command[0] = path.join(path.dirname(f), "uninstalled-server.exe");
  fs.writeFileSync(f, JSON.stringify(broken));
  try {
    await page.reload();
    await page.getByRole("button", { name: "Open Fake server" }).click();
    await expect(page.getByText("not found", { exact: true })).toBeVisible();                     // the program is gone: shown, not hidden
    await page.getByRole("button", { name: "Test connection" }).click();
    await expect(page.locator("#main").getByRole("alert").filter({ hasText: /not installed|could not be started|program/ })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("heading", { name: /Fake server/ }).getByText("Connected")).toHaveCount(0);
  } finally {
    fs.writeFileSync(f, saved);
  }
});

test("removing needs the name typed, and the connector and its program entry are both gone", async ({ page }) => {
  await page.goto("/#/connectors");
  await page.getByRole("button", { name: "Open Fake server" }).click();
  await page.getByRole("button", { name: "Remove…" }).click();
  const dlg = page.getByRole("dialog", { name: "Remove this connector?" });
  await expect(dlg.getByRole("button", { name: "Remove" })).toBeDisabled();
  await dlg.getByLabel(/Type fake to confirm/).fill("fake");
  await dlg.getByRole("button", { name: "Remove" }).click();
  await expect(page.getByText("No connectors yet")).toBeVisible();
  expect((await owner(page, "connectors")).connectors).toEqual([]);
  expect(JSON.parse(fs.readFileSync(info().connectors_file, "utf-8")).connectors).toEqual({});
  expect((await agentCall(page, call("echo", { text: "x" }))).status).toBe(403);                  // nothing left to call
});
