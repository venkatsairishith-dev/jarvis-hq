import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
// The UI's tsconfig has no Node types (this file is the only one that touches Node), so these three are declared by hand.
// @ts-ignore
import fs from "node:fs";
// @ts-ignore
import os from "node:os";
// @ts-ignore
import path from "node:path";
declare const process: { env: Record<string, string | undefined>; platform: string };

// The console (hq/console) serves dist/ and answers /api/*. In dev, /api is forwarded to it. The console needs a credential on every
// request since the 2026-10-19 re-review; the dev proxy reads the console's MASTER token from its 0600 file (console.token in the
// per-user run folder, written by the running console) and adds it as X-Jarvis-Console, like any first-party program. The token is
// read when the dev server starts: restart `npm run dev` after restarting the console. Nothing is written into a page.
function consoleToken(): string {
  const dir = process.env.JARVIS_RUN_DIR ??
    (process.platform === "win32" ? path.join(process.env.LOCALAPPDATA ?? path.join(os.homedir(), "AppData", "Local"), "jarvis", "run")
      : process.env.XDG_RUNTIME_DIR ? path.join(process.env.XDG_RUNTIME_DIR, "jarvis") : path.join(os.homedir(), ".jarvis", "run"));
  try { return fs.readFileSync(path.join(dir, "console.token"), "utf8").trim(); } catch { return ""; }
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8799", headers: { "X-Jarvis-Console": consoleToken() } } } },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
