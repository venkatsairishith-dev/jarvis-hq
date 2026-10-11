import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// The console (hq/console) serves dist/ and answers /api/*. In dev, /api is forwarded to it.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: { outDir: "dist", sourcemap: false, target: "es2022" },
  server: { port: 5173, proxy: { "/api": "http://127.0.0.1:8799" } },
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
