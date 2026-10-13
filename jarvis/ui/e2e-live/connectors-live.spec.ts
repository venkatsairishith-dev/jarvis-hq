import { test, expect, type Page } from "@playwright/test";
import fs from "node:fs";

// Live: the connector manager against the REAL console, the REAL kernel on the server and the REAL laptop node, with two real
// servers: the official `mcp-server-time` (a local program, no account) and DeepWiki (a hosted remote MCP server, no account,
// documentation about PUBLIC repositories). Read-only throughout. No credential is involved or read by this test, except the
// throwaway public-lane test agent's token file (as the approval spec does), which is never printed.
const KERNEL = "http://100.125.91.51:8790";
const TOKEN_FILE = "D:\\jarvis-secrets\\kernel\\approval-test-agent.token";
const O = (page: Page, p: string, body?: unknown) =>
  body === undefined ? page.request.get(`/api/owner/${p}`).then((r) => r.json())
    : page.request.fetch(`/api/owner/${p}`, { method: "POST", data: body, headers: { "X-Jarvis-Console": "1", Origin: "http://127.0.0.1:8799" }, failOnStatusCode: false }).then(async (r) => ({ ...(await r.json()), http: r.status() }));
const C = (page: Page, p: string, body?: unknown) =>
  page.request.fetch(`/api/connectors/${p}`, { method: body === undefined ? "GET" : "POST", data: body, headers: { "X-Jarvis-Console": "1", Origin: "http://127.0.0.1:8799" }, failOnStatusCode: false })
    .then(async (r) => ({ ...(await r.json()), http: r.status() }));
async function agent(page: Page, target: string, args: Record<string, unknown>) {
  const r = await page.request.fetch(`${KERNEL}/v1/jarvis/actions`, {
    method: "POST", failOnStatusCode: false, data: { tool: "connector", op: "call", target, params: { arguments: args } },
    headers: { Authorization: `Bearer ${fs.readFileSync(TOKEN_FILE, "utf-8").trim()}`, "Content-Type": "application/json" },
  });
  return { status: r.status(), json: await r.json() };
}

test.describe.configure({ mode: "serial" });
const TIME_EXE = "D:\\jarvis\\tools\\mcp-venv\\Scripts\\mcp-server-time.exe";

test("the time server: add, test, classify, enable, try, and a lab agent's call through the kernel and the node", async ({ page }) => {
  test.setTimeout(5 * 60_000);
  await page.goto("/#/connectors");
  const existing = (await O(page, "connectors")).connectors.map((c: { name: string }) => c.name);
  if (existing.includes("time")) { await O(page, "connectors/delete", { name: "time", confirm: "time" }); await C(page, "local/remove", { name: "time" }); }
  const add = await C(page, "add", {
    definition: { name: "time", label: "Time and time zones", purpose: "Current time and time-zone conversion (read only).", transport: "stdio", node: "laptop", lanes: ["private", "public"], max_calls_per_hour: 60, timeout_s: 30, max_output: 4000 },
    local: { command: [TIME_EXE], env_refs: {}, env_static: {}, timeout_s: 30 },
  });
  console.log("ADD", add.http);
  expect(add.http).toBe(201);

  const t0 = Date.now();
  const test_ = await O(page, "connectors/test", { name: "time" });
  console.log("TEST", JSON.stringify({ ok: test_.ok, ms: Date.now() - t0, era: test_.connector?.era, server: test_.connector?.server, tools: test_.connector?.tools?.map((t: { tool: string; risk: string; enabled: boolean; suggested: string; claimed: unknown }) => [t.tool, t.risk, t.enabled, t.suggested, t.claimed]) }));
  expect(test_.ok).toBe(true);
  const tools = test_.connector.tools as { tool: string; risk: string; enabled: boolean }[];
  expect(tools.map((t) => t.tool).sort()).toEqual(["convert_time", "get_current_time"]);
  expect(tools.every((t) => !t.enabled && t.risk === "unknown")).toBe(true);                           // nothing is on until the owner says so

  // before it is enabled an agent cannot call it
  expect((await agent(page, "time.get_current_time", { timezone: "Asia/Kolkata" })).status).toBe(403);

  for (const t of ["get_current_time", "convert_time"]) {
    const r = await O(page, "connectors/tool", { name: "time", tool: t, risk: "read", enabled: true });
    expect(r.http).toBe(200);
  }
  await page.goto("/#/connectors");
  await page.getByRole("button", { name: "Open Time and time zones" }).click();
  await page.screenshot({ path: "D:\\jarvis\\outputs\\console-evidence\\40-connectors-time.png" });

  const tried = await O(page, "connectors/try", { name: "time", tool: "get_current_time", arguments: { timezone: "Asia/Kolkata" } });
  console.log("TRY", JSON.stringify({ status: tried.http, text: tried.result?.text?.slice(0, 160), untrusted: tried.result?.untrusted }));
  expect(tried.http).toBe(200);
  expect(tried.result.untrusted).toBe(true);
  expect(tried.result.text).toMatch(/Asia\/Kolkata/);

  // a lab (public-lane) agent, through the real kernel, the real node and the real server
  const t1 = Date.now();
  const viaAgent = await agent(page, "time.convert_time", { source_timezone: "Asia/Kolkata", time: "10:00", target_timezone: "UTC" });
  console.log("AGENT", JSON.stringify({ status: viaAgent.status, ms: Date.now() - t1, text: viaAgent.json.result?.text?.slice(0, 200) }));
  expect(viaAgent.status).toBe(200);
  expect(viaAgent.json.result.text).toMatch(/04:30/);                                                   // 10:00 in Kolkata is 04:30 UTC
  expect(viaAgent.json.result.untrusted).toBe(true);

  // the owner's classification is law: an unknown tool and a tool the server does not have are refused
  expect((await agent(page, "time.no_such_tool", {})).status).toBe(403);
  expect((await agent(page, "time.get_current_time", { timezone: 5 })).status).toBe(400);               // wrong argument type, nothing sent
  const calls = (await O(page, "connectors/calls?limit=10")).calls as { connector: string; tool: string; outcome: string }[];
  expect(calls.some((c) => c.connector === "time" && c.tool === "convert_time" && c.outcome === "ok")).toBe(true);
  const status = (await O(page, "connectors")).connectors.find((c: { name: string }) => c.name === "time");
  console.log("STATUS", status.status, status.era, JSON.stringify(status.server));
  await O(page, "connectors/disconnect", { name: "time" });
});

test("DeepWiki: a hosted remote MCP server over https, read only, left disabled afterwards", async ({ page }) => {
  test.setTimeout(5 * 60_000);
  await page.goto("/#/connectors");
  const existing = (await O(page, "connectors")).connectors.map((c: { name: string }) => c.name);
  if (existing.includes("deepwiki")) await O(page, "connectors/delete", { name: "deepwiki", confirm: "deepwiki" });
  const add = await C(page, "add", { definition: { name: "deepwiki", label: "DeepWiki", purpose: "Documentation and answers about public GitHub repositories (read only).",
    transport: "https", endpoint: "https://mcp.deepwiki.com/mcp", secret_ref: "", lanes: ["private"], max_calls_per_hour: 30, timeout_s: 60, max_output: 6000 } });
  expect(add.http).toBe(201);
  const t0 = Date.now();
  const r = await O(page, "connectors/test", { name: "deepwiki" });
  console.log("DEEPWIKI TEST", JSON.stringify({ ok: r.ok, ms: Date.now() - t0, era: r.connector?.era, server: r.connector?.server, tools: r.connector?.tools?.map((t: { tool: string; suggested: string }) => [t.tool, t.suggested]) }));
  expect(r.ok).toBe(true);
  expect(r.connector.tools.length).toBeGreaterThan(0);
  // a public-lane agent has no access: it is private only
  expect((await agent(page, "deepwiki.read_wiki_structure", { repoName: "modelcontextprotocol/python-sdk" })).status).toBe(403);
  const s = await O(page, "connectors/tool", { name: "deepwiki", tool: "read_wiki_structure", risk: "read", enabled: true });
  expect(s.http).toBe(200);
  const t1 = Date.now();
  const tried = await O(page, "connectors/try", { name: "deepwiki", tool: "read_wiki_structure", arguments: { repoName: "modelcontextprotocol/python-sdk" } });
  console.log("DEEPWIKI CALL", JSON.stringify({ status: tried.http, ms: Date.now() - t1, chars: tried.result?.text?.length, head: tried.result?.text?.slice(0, 120), truncated: tried.result?.truncated }));
  expect(tried.http).toBe(200);
  expect(tried.result.untrusted).toBe(true);
  expect(tried.result.text.length).toBeGreaterThan(50);
  await O(page, "connectors/disable", { name: "deepwiki" });                                             // left off: it sends questions to a third party
  const after = (await O(page, "connectors")).connectors.find((c: { name: string }) => c.name === "deepwiki");
  expect(after.status).toBe("disabled");
});

test("nothing secret anywhere in what the kernel and console return", async ({ page }) => {
  const all = JSON.stringify(await O(page, "connectors")) + JSON.stringify(await C(page, "local")) + JSON.stringify(await O(page, "state"));
  expect(all).not.toMatch(/ghp_[A-Za-z0-9]{20,}|Bearer\s+[A-Za-z0-9._-]{20,}|BEGIN [A-Z ]*PRIVATE KEY/);
  const v = await O(page, "audit/verify");
  console.log("AUDIT", JSON.stringify(v));
  expect(v.ok).toBe(true);
});
