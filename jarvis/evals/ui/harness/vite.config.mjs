import process from 'node:process'

import { fileURLToPath } from 'node:url'

import vue from '@vitejs/plugin-vue'
import UnoCss from 'unocss/vite'
import VueI18n from '@intlify/unplugin-vue-i18n/vite'
import Info from 'unplugin-info/vite'
import Yaml from 'unplugin-yaml/vite'
import { resolve } from 'node:path'

// Builds the render harness (render.mjs). Mirrors what the app's own browser tests use (apps/stage-tamagotchi/vitest.config.ts): Vue, UnoCSS with
// the app's config, YAML locales. No telemetry, no remote assets: it bundles only what the components import, and nothing is served but files.
const here = fileURLToPath(new URL('.', import.meta.url))
const face = resolve(here, '..', '..', '..', 'face')
const app = resolve(face, 'apps', 'stage-tamagotchi')

export default {
  root: here,
  base: './',
  logLevel: 'error',
  plugins: [
    Info({ time: new Date(0) }),
    Yaml(),
    vue(),
    // the app's own config; utilities are extracted from every component the harness imports
    UnoCss({ configFile: resolve(app, 'uno.config.ts') }),
    VueI18n({ runtimeOnly: false, compositionOnly: true, fullInstall: true }),
  ],
  define: { 'import.meta.env.RUNTIME_ENVIRONMENT': '\'electron\'', 'import.meta.env.URL_MODE': '\'file\'' },
  resolve: {
    alias: {
      '@proj-airi/electron-vueuse': resolve(here, 'electron-vueuse-mock.ts'),
      '@proj-airi/i18n': resolve(face, 'packages', 'i18n', 'src'),
      '@proj-airi/server-sdk': resolve(face, 'packages', 'server-sdk', 'src'),
      '@proj-airi/stage-ui/stores/mcp-tool-bridge': resolve(face, 'packages', 'stage-ui', 'src', 'stores', 'mcp-tool-bridge.ts'),
      '@proj-airi/stage-ui': resolve(face, 'packages', 'stage-ui', 'src'),
      '@proj-airi/stage-pages': resolve(face, 'packages', 'stage-pages', 'src'),
      '@proj-airi/stage-shared': resolve(face, 'packages', 'stage-shared', 'src'),
    },
  },
  build: { outDir: process.env.UI_HARNESS_OUT, emptyOutDir: true, target: 'es2022', reportCompressedSize: false, chunkSizeWarningLimit: 100000 },
}
