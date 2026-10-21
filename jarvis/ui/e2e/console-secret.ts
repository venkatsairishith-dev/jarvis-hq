import type { APIRequestContext, Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// A test that talks to the console like a PROGRAM does (page.request) sends the console's MASTER token in X-Jarvis-Console. Since the
// 2026-10-19 re-review the console no longer puts any credential in `GET /`: the token is in a 0600 file the console writes at start
// (console.token in the per-user run folder, jarvis/scripts/control_auth.py), which is where every first-party program reads it.
// The browser itself is signed in once by global-setup.ts (a one-time launch code -> session cookie -> storageState).
export function runDir(): string {
  if (process.env.JARVIS_RUN_DIR) return process.env.JARVIS_RUN_DIR;
  if (process.platform === "win32") return path.join(process.env.LOCALAPPDATA ?? path.join(os.homedir(), "AppData", "Local"), "jarvis", "run");
  return process.env.XDG_RUNTIME_DIR ? path.join(process.env.XDG_RUNTIME_DIR, "jarvis") : path.join(os.homedir(), ".jarvis", "run");
}

export function masterToken(): string {
  const file = path.join(runDir(), "console.token");
  try {
    const t = fs.readFileSync(file, "utf8").trim();
    if (t) return t;
  } catch { /* handled below */ }
  throw new Error(`no console token at ${file}: is the console running (and JARVIS_RUN_DIR the same for it and for this test)?`);
}

export async function consoleSecret(_from?: Page | APIRequestContext): Promise<string> {
  return masterToken();
}

export async function consoleHeaders(from: Page | APIRequestContext, extra: Record<string, string> = {}): Promise<Record<string, string>> {
  return { "X-Jarvis-Console": await consoleSecret(from), ...extra };
}
