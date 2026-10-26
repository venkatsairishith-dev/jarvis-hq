// The Timeline panel on its own, fed by views that the real backend built (see make_fixtures.py). scripts/timeline-check.mjs drives this page with Playwright.
// window.__tl has: show(name), the calls the panel made, and the controller.
import { createApp, nextTick } from 'vue'

import TimelinePanel from '../../src/renderer/components/timeline/TimelinePanel.vue'

import { JARVIS_TIMELINE_SHOT_TOOL, JARVIS_TIMELINE_UNDO_TOOL, JARVIS_TIMELINE_WANTED_TOOL } from '../../src/renderer/composables/jarvis-timeline-logic'
import { useTimelineController } from '../../src/renderer/composables/use-jarvis-timeline'

const fixtures = await (await fetch('./fixtures.json')).json()
const calls: Array<{ name: string, args?: Record<string, unknown> }> = []
let current: any = null
let version = 1
let openSeq = 0
let undoReply: any = fixtures.undo.ok

const controller = useTimelineController({
  waitS: 0,
  call: async (name, args) => {
    calls.push({ name, args })
    if (name === JARVIS_TIMELINE_WANTED_TOOL) {
      const wantsView = args?.full === true || openSeq > (args?.seen_open as number)
      return { structuredContent: { version, open_seq: openSeq, token: fixtures.token, view: wantsView ? (current?.structuredContent?.view ?? null) : null } }
    }
    if (name === JARVIS_TIMELINE_SHOT_TOOL)
      return fixtures.shots[args?.ref as string] ?? { structuredContent: { ok: false, message: 'gone' } }
    if (name === JARVIS_TIMELINE_UNDO_TOOL)
      return undoReply
    throw new Error(`unexpected call ${name}`)
  },
})

createApp(TimelinePanel, { controller }).mount('#app')
// the first answer of a page only sets the baseline (an old open request must not pop the panel up)
await controller.pollOnce()

;(window as any).__tl = {
  controller,
  calls,
  names: Object.keys(fixtures.views),
  setUndoReply: (name: string) => { undoReply = fixtures.undo[name] },
  /** Put a view on the "server" and let the panel open for it, like jarvis_timeline(open) plus the long poll would. Returns the ms until it was drawn. */
  async show(name: string) {
    const t0 = performance.now()
    controller.setOpen(false)
    controller.expanded.value = new Set()
    controller.collapsedTasks.value = new Set()
    controller.setFilter({ today: false, failedOnly: false })
    controller.dismissReport()
    current = JSON.parse(JSON.stringify(fixtures.views[name]))
    version += 1
    openSeq += 1
    await controller.pollOnce()
    const t1 = performance.now()
    await nextTick()
    const t2 = performance.now()
    await new Promise(resolve => requestAnimationFrame(resolve))
    const t3 = performance.now()
    await new Promise(resolve => requestAnimationFrame(resolve))
    const t4 = performance.now()
    ;(window as any).__tl.phases = { poll: t1 - t0, render: t2 - t1, frame1: t3 - t2, frame2: t4 - t3 }
    return performance.now() - t0
  },
  clear() {
    controller.setOpen(false)
  },
}
