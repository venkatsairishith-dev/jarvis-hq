// The Preview panel on its own, fed by fixtures that jarvis.bridge.output_preview produced (see make_fixtures.py).
// scripts/preview-check.mjs drives this page with Playwright. It reaches window.__preview for the checks.
import { createApp } from 'vue'

import PreviewPanel from '../../src/renderer/components/PreviewPanel.vue'

import { JARVIS_OPEN_OUTPUT_TOOL, JARVIS_OUTPUT_GET_TOOL, JARVIS_OUTPUTS_TOOL, JARVIS_WANTED_TOOL } from '../../src/renderer/composables/jarvis-preview-logic'
import { usePreviewController } from '../../src/renderer/composables/use-jarvis-preview'

const fixtures = await (await fetch('./fixtures.json')).json()
const wanted: string[] = []
const opened: string[] = []

const controller = usePreviewController({
  call: async (name, args) => {
    if (name === JARVIS_OUTPUTS_TOOL)
      return { structuredContent: fixtures.outputs }
    if (name === JARVIS_WANTED_TOOL)
      return { structuredContent: { count: wanted.length, ids: wanted.splice(0) } }
    if (name === JARVIS_OPEN_OUTPUT_TOOL) {
      opened.push(String(args?.path))
      return { structuredContent: { result: { opened: args?.path } } }
    }
    if (name === JARVIS_OUTPUT_GET_TOOL) {
      const byPage = fixtures.get[String(args?.id)]
      const hit = byPage?.[String(args?.page ?? 0)] ?? byPage?.['0']
      const result = hit ? { ...hit } : { ok: false, message: 'I cannot find that file' }
      if (args?.thumbs !== true)
        delete result.thumbs
      return { structuredContent: result }
    }
    throw new Error(`unexpected call ${name}`)
  },
})

createApp(PreviewPanel, { controller }).mount('#app')
;(window as any).__preview = {
  controller,
  wanted,
  opened,
  ids: Object.fromEntries((fixtures.outputs.items as Array<{ id: string, title: string }>).map(i => [i.title, i.id])),
}
