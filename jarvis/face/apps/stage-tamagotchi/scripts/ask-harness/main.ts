// The ask panel on its own, fed by forms that the real backend built (see make_fixtures.py). scripts/ask-check.mjs drives this page with
// Playwright. window.__ask has: show(name), the calls the panel made, the controller, and (D63) a voice controller wired to a FAKE transcript
// source: no microphone, no recognizer, no network. `say(text)` is what the recognizer would return; `command(text, cause)` is a spoken command.
import { ownerSpeech } from '@proj-airi/stage-ui/composables/voice-command-sink'
import { createApp, ref } from 'vue'

import AskPanel from '../../src/renderer/components/ask/AskPanel.vue'

import { createDomFocus } from '../../src/renderer/composables/ask-voice-dom'
import { JARVIS_ASK_CLOSE_TOOL, JARVIS_ASK_SUBMIT_TOOL, JARVIS_ASK_WANTED_TOOL } from '../../src/renderer/composables/jarvis-ask-logic'
import { useAskController } from '../../src/renderer/composables/use-jarvis-ask'
import { useAskVoice } from '../../src/renderer/composables/use-jarvis-ask-voice'

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

// ---- a fake transcript source: the recognizer's side of the voice host ----
const phase = ref<'idle' | 'starting' | 'recording' | 'processing'>('idle')
const transcript = ref('')
const stats = { starts: 0, cancels: 0, final: '' }
const source = {
  available: ref(true),
  configured: ref(true),
  phase,
  transcript,
  start: async () => {
    stats.starts++
    phase.value = 'starting'
    transcript.value = ''
  },
  finish: async () => {
    const out = stats.final
    stats.final = ''
    phase.value = 'idle'
    transcript.value = ''
    return out
  },
  cancel: async () => {
    stats.cancels++
    phase.value = 'idle'
    transcript.value = ''
  },
}
const spoken: string[] = []
const translations: Record<string, string> = { 'టెంపరరీ ఫైల్స్ టిక్ చేయి': 'tick temporary files' }
const voice = useAskVoice({
  ask: controller,
  source: () => source,
  bridge: {
    translate: async text => translations[text] ? { english: translations[text], sourceLang: 'te', translated: true } : null,
    decide: async () => null,
    speechPlans: async () => ({ te: { speak: false, note: 'The reply is shown in Telugu but not spoken: the voice is installed but not yet verified by the owner.' } }),
  },
  speak: async (text) => {
    spoken.push(text)
    await new Promise(resolve => setTimeout(resolve, 20))
  },
  stopSpeaking: () => spoken.push('<stop>'),
  focus: createDomFocus(() => document.querySelector<HTMLElement>('.ask-root .ask')),
  idleMs: 60_000,
})

createApp(AskPanel, { controller, voice }).mount('#app')
;(window as any).__ask = {
  controller,
  voice,
  calls,
  spoken,
  stats,
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
  /** The recognizer hears `text` (after the `interim` words), and the host ends the input by itself. */
  async say(text: string, interim: string[] = []) {
    phase.value = 'recording'
    for (const i of interim)
      transcript.value = i
    transcript.value = text
    stats.final = text
    phase.value = 'processing'
    await new Promise(resolve => setTimeout(resolve, 60))
  },
  /** Only the interim words: the owner is still speaking. */
  hear(interim: string) {
    phase.value = 'recording'
    transcript.value = interim
  },
  command: (text: string, cause = 'wake-word') => voice.command(ownerSpeech(text, cause)),
}
