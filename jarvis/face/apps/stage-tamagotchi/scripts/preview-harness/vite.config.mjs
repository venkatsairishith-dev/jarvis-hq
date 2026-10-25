import process from 'node:process'

import { fileURLToPath } from 'node:url'

import vue from '@vitejs/plugin-vue'

// Builds the harness page for scripts/preview-check.mjs. No telemetry, no remote assets: it bundles only what the panel imports.
export default {
  root: fileURLToPath(new URL('.', import.meta.url)),
  base: './',
  plugins: [vue()],
  logLevel: 'warn',
  build: { outDir: process.env.PREVIEW_HARNESS_OUT, emptyOutDir: true, target: 'es2022', reportCompressedSize: false, chunkSizeWarningLimit: 100000 },
}
