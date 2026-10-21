import { defineConfig } from "@playwright/test";
import path from "node:path";

// Live checks against the REAL console (127.0.0.1:8799), the real kernel on the server and the real laptop node.
// Not part of the default suite: run it deliberately (`npx playwright test -c playwright.live.config.ts`).
const fixture = path.resolve(import.meta.dirname, "e2e/fixtures/open-downloads.wav");

// The REAL console publishes console.token in the per-user run folder; global-setup.ts reads it (set JARVIS_RUN_DIR only to override).
export default defineConfig({
  testDir: "./e2e-live",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 40 * 60_000,
  expect: { timeout: 30_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "test-results/live-results.json" }]],
  outputDir: "test-results/live",
  use: {
    storageState: path.resolve(import.meta.dirname, "test-results", "console-session.json"),
    baseURL: "http://127.0.0.1:8799", channel: "msedge", viewport: { width: 1360, height: 900 }, permissions: ["microphone"],
    launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${fixture}`, "--autoplay-policy=no-user-gesture-required"] },
  },
});
