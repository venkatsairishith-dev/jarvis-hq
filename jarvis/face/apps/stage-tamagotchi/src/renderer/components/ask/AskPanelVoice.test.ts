import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'
import { createSSRApp, ref } from 'vue'
// The voice markup of the ask panel (D63), rendered on the server side of Vue (no browser): a microphone on every text, number and date
// field and on a heading, one for commands and one for reading aloud in the header, none without a voice host, none on a field that asks for a
// secret, all text escaped. The live behaviour at 360 to 1280 px is checked by scripts/ask-check.mjs with Chromium and a fake transcript source.
import { renderToString } from 'vue/server-renderer'

import AskPanel from './AskPanel.vue'

import { useAskController } from '../../composables/use-jarvis-ask'
import { useAskVoice } from '../../composables/use-jarvis-ask-voice'

async function render(form: Record<string, unknown>, values: Record<string, unknown> = {}, withVoice = true) {
  const controller = useAskController({ call: async () => ({ structuredContent: { version: 1, form: { id: 'q1', token: 't', title: 'T', intro: '', busy: false, status: '', progress: null, values, buttons: [{ id: 'ok', label: 'Go', style: 'primary' }, { id: 'cancel', label: 'Cancel', style: 'secondary' }], ...form } } }) })
  await controller.pollOnce()
  const voice = withVoice
    ? useAskVoice({
        ask: controller,
        source: () => ({ available: ref(true), configured: ref(true), phase: ref('idle' as const), transcript: ref(''), start: async () => {}, finish: async () => '', cancel: async () => {} }),
      })
    : null
  return renderToString(createSSRApp(AskPanel, { controller, voice }))
}

const FIELDS = [
  { type: 'text', id: 'name', label: 'Deck title', heading: true, max_len: 80 },
  { type: 'text', id: 'note', label: 'Note', multiline: true, max_len: 200 },
  { type: 'number', id: 'qty', label: 'Quantity', min: 0, max: 9 },
  { type: 'date', id: 'day', label: 'Day', mode: 'date' },
  { type: 'toggle', id: 'night', label: 'Night light' },
]

describe('askPanel voice markup', () => {
  it('has a microphone on each text, number and date field and the heading, with a name and a key', async () => {
    const html = await render({ fields: FIELDS })
    expect(html.match(/class="ask-mic /g)?.length).toBe(4) // the heading, the note, the number and the date
    expect(html.match(/ask-hbtn ask-mic/g)?.length).toBe(1) // the header's, for spoken commands
    expect(html).toContain('aria-label="Dictate Deck title')
    expect(html).toContain('aria-label="Dictate Note')
    expect(html).toContain('aria-label="Dictate Quantity')
    expect(html).toContain('aria-label="Dictate Day')
    expect(html).not.toContain('aria-label="Dictate Night light')
    expect(html).toContain('aria-keyshortcuts="Alt+M"')
    expect(html).toContain('aria-label="Speak a command (Alt+M)"')
    expect(html).toContain('aria-label="Read the form aloud (Alt+R)"')
  })

  it('draws an editable heading as plain text with its own look', async () => {
    const html = await render({ fields: FIELDS }, { name: 'Q3 <b>review</b>' })
    expect(html).toContain('ask-heading-input')
    expect(html).toContain('ask-heading-field')
    expect(html).not.toContain('<b>review')
    expect(html).toContain('data-voice-input="name"')
  })

  it('has no microphone anywhere without a voice host', async () => {
    const html = await render({ fields: FIELDS }, {}, false)
    expect(html).not.toContain('ask-mic')
    expect(html).not.toContain('Alt+M')
    expect(html).not.toContain('ask-voice')
  })

  it('has no microphone on a field that asks for a secret', async () => {
    const html = await render({ fields: [{ type: 'text', id: 'p', label: 'Your PIN' }, { type: 'text', id: 'n', label: 'Name' }] })
    expect(html).toContain('aria-label="Dictate Name')
    expect(html).not.toContain('Dictate Your PIN')
  })

  it('keeps the voice strip out of the page until there is something to say', async () => {
    const html = await render({ fields: FIELDS })
    expect(html).not.toContain('class="ask-voice"')
  })
})

describe('the voice code cannot reach an approval (source check)', () => {
  const here = dirname(fileURLToPath(import.meta.url))
  const read = (rel: string) => readFileSync(join(here, rel), 'utf-8')
  const files = {
    logic: read('../../composables/jarvis-ask-voice-logic.ts'),
    controller: read('../../composables/use-jarvis-ask-voice.ts'),
    wiring: read('../../composables/ask-voice-wiring.ts'),
    dom: read('../../composables/ask-voice-dom.ts'),
    sink: read('../../../../../../packages/stage-ui/src/composables/voice-command-sink.ts'),
    guard: read('../../../../../../packages/stage-ui/src/composables/voice-secret-guard.ts'),
  }

  it('imports no console, kernel or approval code and calls no approval tool', () => {
    for (const [name, src] of Object.entries(files)) {
      expect(src, name).not.toMatch(/from\s+['"][^'"]*(console|kernel|jarvis_bridge|approve)[^'"]*['"]/i)
      expect(src, name).not.toMatch(/jarvis_(approve|waiting|do|cancel)\b|waiting_approvals|pin_hash|\/api\/owner\/approve/)
    }
    // the only tool the voice code calls is the read-only voice help
    expect(files.wiring.match(/jarvis::\w+/g)).toEqual(['jarvis::jarvis_ask_voice'])
  })

  it('presses a form button in exactly one place, which needs the owner\'s live speech', () => {
    expect(files.controller.match(/ask\.press\(/g)).toHaveLength(1)
    expect(files.controller).toMatch(/by === 'voice' && \(!speech \|\| !isOwner\(speech\) \|\| !speech\.live\)/)
    expect(files.logic).not.toMatch(/\.press\(/)
  })

  it('makes an OwnerSpeech only in the voice pipeline: the hook and the panel\'s own microphone', () => {
    expect(files.controller.match(/ownerSpeech\(/g)).toHaveLength(1)
    expect(files.controller).toContain('ownerSpeech(text, \'panel-mic\')')
    expect(files.sink.match(/ownerSpeech\(/g)).toHaveLength(2) // its definition and the one use in offerOwnerSpeech
    for (const name of ['logic', 'wiring', 'dom', 'guard'] as const)
      expect(files[name], name).not.toContain('ownerSpeech(')
  })

  it('takes nothing a model, a tool or the screen wrote as the owner\'s speech', () => {
    // the controller reads form text only to match against it
    expect(files.controller).not.toMatch(/parseUtterance\([^)]*(label|title|intro|detail|hint|notes)/)
    // the panel tool results are read as data in the wiring, never as speech
    expect(files.wiring).not.toContain('ownerSpeech')
  })
})
