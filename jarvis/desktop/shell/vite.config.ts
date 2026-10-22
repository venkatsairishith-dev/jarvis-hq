import { defineConfig } from "vitest/config";
import process from "node:process";

const host = process.env.TAURI_DEV_HOST;

export default defineConfig(async () => ({
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || "127.0.0.1",
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    proxy: {
      '/memory': {
        target: 'http://127.0.0.1:48912',
        changeOrigin: true,
      },
      '/agent': {
        target: 'http://127.0.0.1:48915',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:48911',
        changeOrigin: true,
      },
      '/ws': {
        target: 'http://127.0.0.1:48911',
        ws: true,
      }
    },
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  test: {
    globals: true,
    environment: "jsdom",
  },
}));
