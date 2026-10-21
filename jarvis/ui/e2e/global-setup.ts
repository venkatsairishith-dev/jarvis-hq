import { request, type FullConfig } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { masterToken } from "./console-secret";

// Signs the browser in the way the launcher does (re-review M3): the master token (from the console's 0600 token file) asks the
// console for a ONE-TIME launch code, the code is exchanged for a session cookie, and the cookie jar is saved as the storage state
// that every test's browser context starts from. The tests themselves never see a credential in a page.
export const STATE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "test-results", "console-session.json");

export default async function globalSetup(config: FullConfig): Promise<void> {
  const baseURL = config.projects[0].use.baseURL;
  if (!baseURL) throw new Error("global-setup needs a baseURL");
  const ctx = await request.newContext({ baseURL });
  const issued = await ctx.post("/auth/code", { headers: { "X-Jarvis-Console": masterToken() }, data: {} });
  if (!issued.ok()) throw new Error(`the console refused to issue a launch code (HTTP ${issued.status()})`);
  const { code } = (await issued.json()) as { code: string };
  const session = await ctx.post("/auth/session", { data: { code } });
  if (!session.ok()) throw new Error(`the console refused the launch code (HTTP ${session.status()})`);
  fs.mkdirSync(path.dirname(STATE), { recursive: true });
  await ctx.storageState({ path: STATE });
  await ctx.dispose();
}
