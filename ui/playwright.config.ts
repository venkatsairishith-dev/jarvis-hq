import { defineConfig } from "@playwright/test";
import path from "node:path";

// The tests drive the real UI, the real console and a real (throwaway) kernel owner plane with seeded records.
// Voice tests use a fake microphone fed from a speech file made by the same local TTS the app uses.
const PORT = 8811;
const fixture = path.resolve(import.meta.dirname, "e2e/fixtures/open-downloads.wav");
const py = process.env.JARVIS_PYTHON ?? "D:\\jarvis\\tools\\jarvis-venv\\Scripts\\python.exe";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  workers: 1,
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "test-results/results.json" }]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, channel: "msedge", trace: "retain-on-failure", screenshot: "only-on-failure" },
  webServer: {
    command: `"${py}" "${path.resolve(import.meta.dirname, "../console/tests/e2e_server.py")}"`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 120_000,
    env: { E2E_CONSOLE_PORT: String(PORT), PYTHONIOENCODING: "utf-8", E2E_STATE_FILE: "D:\\jarvis\\cache\\e2e-state.json" },
  },
  projects: [
    { name: "app", testMatch: /(app|a11y|approvals)\.spec\.ts/, use: { viewport: { width: 1360, height: 860 } } },
    { name: "narrow", testMatch: /responsive\.spec\.ts/, use: { viewport: { width: 390, height: 844 } } },
    { name: "nomic", testMatch: /nomic\.spec\.ts/, use: { viewport: { width: 1360, height: 860 } } },
    {
      name: "voice", testMatch: /voice\.spec\.ts/, use: {
        viewport: { width: 1360, height: 860 }, permissions: ["microphone"],
        launchOptions: { args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${fixture}`, "--autoplay-policy=no-user-gesture-required"] },
      },
    },
    { name: "zz-kill", testMatch: /kill\.spec\.ts/, use: { viewport: { width: 1360, height: 860 } } },
  ],
});
