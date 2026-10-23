import { defineConfig } from "@playwright/test";
import path from "node:path";

// The Bench screen on a phone: the built UI served by a throwaway console (jarvis/console/tests/e2e_bench_server.py) with
// real benchmark result files and a fake benchmark runner. Runs on Linux (the jarvis-test container) or Windows.
//   JARVIS_PYTHON=python npx playwright test -c playwright.bench.config.ts
// The console writes its master token to <JARVIS_RUN_DIR>/console.token; global-setup.ts reads it to sign the browser in.
process.env.JARVIS_RUN_DIR ??= path.resolve(import.meta.dirname, "test-results", "run");
const PORT = 8812;
const hq = process.env.JARVIS_HQ ?? path.resolve(import.meta.dirname, "..");
const py = process.env.JARVIS_PYTHON ?? (process.platform === "win32" ? "D:\\jarvis\\tools\\jarvis-venv\\Scripts\\python.exe" : "python3");

export default defineConfig({
  testDir: "./e2e",
  testMatch: /bench\.spec\.ts/,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "test-results/bench-results.json" }]],
  globalSetup: "./e2e/global-setup.ts",
  use: {
    storageState: path.resolve(import.meta.dirname, "test-results", "console-session.json"),
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: process.env.PW_CHANNEL ?? (process.platform === "win32" ? "msedge" : "chrome"),
    viewport: { width: 360, height: 740 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2,
    trace: "retain-on-failure", screenshot: "only-on-failure",
  },
  webServer: {
    command: `"${py}" "${path.join(hq, "console", "tests", "e2e_bench_server.py")}"`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { E2E_CONSOLE_PORT: String(PORT), JARVIS_RUN_DIR: process.env.JARVIS_RUN_DIR!, E2E_UI_DIR: path.resolve(import.meta.dirname, "dist"), PYTHONIOENCODING: "utf-8" },
  },
  projects: [{ name: "phone", testMatch: /bench\.spec\.ts/ }],
});
