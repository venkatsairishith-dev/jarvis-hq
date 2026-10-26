import type { SpeechPlans } from './jarvis-ask-voice-logic'
import type { ToolResult } from './jarvis-preview-logic'
/**
 * The real parts behind the ask panel's voice (D63): the face's own speech recognizer (the voice host and the owner's Hearing provider, through
 * `useVoiceComposer` in dictation mode), the face's own speech output (`SpeechClient`, the path that reads text aloud), and the backend's
 * read-only voice help (`jarvis_ask_voice`). Nothing here talks to a cloud recognizer or opens a microphone by itself: the recognizer is only
 * asked to begin when the owner presses a microphone.
 *
 * Switches (localStorage): `jarvis:ask-voice` = `off` hides every microphone; `jarvis:ask-voice-speak` = `off` stops the spoken confirmations
 * ("Ticking temp files"; the text still shows); `jarvis:ask-voice-punct` = `off` stops "comma" and "full stop" from becoming marks.
 */
import type { VoiceBridge, VoiceSource } from './use-jarvis-ask-voice'

import { useVoiceComposer } from '@proj-airi/stage-ui/components/scenarios/chat/composables/use-voice-composer'
import { SpeechClient } from '@proj-airi/stage-ui/services/speech/speech-client'
import { useChatSessionStore } from '@proj-airi/stage-ui/stores/chat/session-store'

import { toolData } from './jarvis-preview-logic'

export const JARVIS_ASK_VOICE_TOOL = 'jarvis::jarvis_ask_voice'

export function switchedOff(key: string, storage: Pick<Storage, 'getItem'> | undefined = globalThis.localStorage): boolean {
  try {
    return storage?.getItem(key) === 'off'
  }
  catch {
    return false
  }
}

/** Must run inside a component's setup (it creates the composer's watchers). Starts nothing. */
export function createComposerSource(): VoiceSource {
  const sessions = useChatSessionStore()
  let last = ''
  const composer = useVoiceComposer({
    sessionId: () => sessions.activeSessionId,
    onTranscript: (text) => {
      last = text
    },
    onError: message => console.warn('[JARVIS] voice input:', message),
  })
  return {
    available: composer.available,
    configured: composer.transcriptionConfigured,
    phase: composer.phase,
    transcript: composer.transcript,
    start: async () => {
      last = ''
      await composer.start('transcription')
    },
    finish: async () => {
      last = ''
      await composer.finish()
      return last
    },
    cancel: () => composer.cancel(),
  }
}

/** Speaks a sentence through the face's own speech output; the same path that reads a text aloud. */
export function createSpeaker(sessionId: () => string) {
  let active: SpeechClient | null = null
  return {
    async speak(text: string): Promise<void> {
      const client = new SpeechClient({ sessionId: sessionId(), turnId: `ask-voice-${globalThis.crypto.randomUUID()}` }, 'read-aloud')
      active = client
      try {
        await client.write(text)
        await client.end()
        await client.finish()
      }
      finally {
        if (active === client)
          active = null
      }
    },
    stop() {
      const client = active
      active = null
      void client?.cancel('Stopped by the owner').catch(() => {})
    },
  }
}

type Call = (name: string, args: Record<string, unknown>, timeoutMs: number) => Promise<ToolResult>

/** The backend's read-only help for the open form. Each call carries the form's one-time token, so only the open form gets answers. */
export function createVoiceBridge(call: Call, form: () => { id: string, token: string } | null): VoiceBridge {
  async function run(args: Record<string, unknown>, timeoutMs: number): Promise<Record<string, unknown> | null> {
    const f = form()
    if (!f)
      return null
    try {
      const data = toolData(await call(JARVIS_ASK_VOICE_TOOL, { form_id: f.id, token: f.token, ...args }, timeoutMs))
      return data && data.ok === true ? data : null
    }
    catch {
      return null
    }
  }
  return {
    async translate(text) {
      const d = await run({ op: 'translate', text }, 3_500)
      return d ? { english: String(d.english ?? ''), sourceLang: String(d.source_lang ?? 'en'), translated: d.translated === true } : null
    },
    async decide(request) {
      const d = await run({ op: 'decide', text: request.english, phrase: request.phrase, candidates: request.candidates.map(key => ({ key })) }, 4_000)
      if (!d)
        return null
      return {
        pick: typeof d.pick === 'string' ? d.pick : null,
        confidence: Number(d.confidence) || 0,
        ask: d.ask !== false,
        options: Array.isArray(d.options) ? d.options.map(String).slice(0, 3) : [],
      }
    },
    async speechPlans() {
      const d = await run({ op: 'speech' }, 3_000)
      const plans = d?.plans as Record<string, { speak?: boolean, note?: string }> | undefined
      if (!plans)
        return null
      const out: SpeechPlans = {}
      for (const [lang, p] of Object.entries(plans))
        out[lang] = { speak: p?.speak === true, note: String(p?.note ?? '').slice(0, 200) }
      return out
    },
  }
}
