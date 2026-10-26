// The ask panel on its own, fed by forms that the real backend built (see make_fixtures.py). scripts/ask-check.mjs drives this page with
// Playwright. window.__ask has: show(name), the calls the panel made, and the controller.
import { createApp } from 'vue'

import AskPanel from '../../src/renderer/components/ask/AskPanel.vue'

import { JARVIS_ASK_CLOSE_TOOL, JARVIS_ASK_SUBMIT_TOOL, JARVIS_ASK_WANTED_TOOL } from '../../src/renderer/composables/jarvis-ask-logic'
import { useAskController } from '../../src/renderer/composables/use-jarvis-ask'

const fixtures = await (await fetch('./fixtures.json')).json()
const calls: Array<{ name: string, args?: Record<string, unknown> }> = []
let current: { structuredContent: Record<string, unknown> } | null = null
let version = 0
let reply: Record<string, unknown> = { ok: true, done: true }

const controller = useAskController({
  waitS: 0,
  call: async (name, args) => {
    calls.push({ name, args })
    if (name === JARVIS_ASK_WANTED_TOOL) {
      const form = (current?.structuredContent as any)?.form ?? null
      return { structuredContent: { version, form } }
    }
    if (name === JARVIS_ASK_SUBMIT_TOOL)
      return { structuredContent: reply }
    if (name === JARVIS_ASK_CLOSE_TOOL)
      return { structuredContent: { ok: true } }
    throw new Error(`unexpected call ${name}`)
  },
})

createApp(AskPanel, { controller }).mount('#app')
;(window as any).__ask = {
  controller,
  calls,
  names: Object.keys(fixtures),
  setReply: (r: Record<string, unknown>) => { reply = r },
  /** Put a fixture on the "server" and let the panel fetch it, like the long poll would. Returns the ms until it was drawn. */
  async show(name: string, overrides?: Record<string, unknown>) {
    const t0 = performance.now()
    const base = JSON.parse(JSON.stringify(fixtures[name]))
    if (overrides)
      Object.assign(base.structuredContent.form, overrides)
    current = base
    version += 1
    await controller.pollOnce()
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    return performance.now() - t0
  },
  clear() {
    current = null
    version += 1
    return controller.pollOnce()
  },
}
