/**
 * Where the owner's spoken words may be offered to the panel that is open, before they go to the chat (D63).
 *
 * A form that JARVIS shows (the ask panel) can be used by voice. The voice store hands every finished spoken input to `offerOwnerSpeech`
 * before it becomes a chat draft. Two things happen there, and nothing else:
 *
 *  1. A PIN or password that was SAID is dropped (never reaches a model, a draft or a log): see voice-secret-guard.ts.
 *  2. If a sink is registered (the ask panel while a form is open), it may CLAIM the words as a command for the form. A claimed input does
 *     not become a draft or a chat message. An unclaimed one goes on exactly as before.
 *
 * `OwnerSpeech` is the only thing a sink can act on. It can be made only here, from a finished spoken input that came out of the voice
 * pipeline (or from the panel's own microphone), so text from a model, a tool result, a file or a screen can never be passed to a sink as if
 * the owner had said it: a plain object with the same fields is not an OwnerSpeech (`isOwnerSpeech` checks a private registry).
 *
 * `cause` says how the input started: `control` (push-to-talk or a button), `wake-word`, `panel-mic` (the panel's own microphone), or
 * `speech` (voice activity detection in the always-on mode: ambient, never a command).
 */
import { speechRefusal } from './voice-secret-guard'

const issued = new WeakSet<object>()

export interface OwnerSpeech {
  readonly text: string
  readonly cause: string
  readonly at: number
  /** False once the offer timed out or was answered: a late result must not act. */
  readonly live: boolean
}

class OwnerSpeechImpl implements OwnerSpeech {
  live = true
  readonly at = Date.now()
  constructor(readonly text: string, readonly cause: string) {}
}

/** Only the voice pipeline calls this (the voice store's submit, and the ask panel's own microphone). */
export function ownerSpeech(text: string, cause: string): OwnerSpeech {
  const speech = new OwnerSpeechImpl(String(text ?? ''), String(cause || 'speech'))
  issued.add(speech)
  return speech
}

export function isOwnerSpeech(value: unknown): value is OwnerSpeech {
  return typeof value === 'object' && value !== null && issued.has(value)
}

/** Ends the life of a speech object: after this a sink that is still working on it must not act. */
function expire(speech: OwnerSpeech) {
  (speech as OwnerSpeechImpl).live = false
}

export type CommandSink = (speech: OwnerSpeech) => Promise<boolean> | boolean

let sink: CommandSink | null = null
const secretListeners = new Set<() => void>()

/** Registers the one sink (the open ask panel). Returns the function that removes it. */
export function setVoiceCommandSink(next: CommandSink | null): () => void {
  sink = next
  return () => {
    if (sink === next)
      sink = null
  }
}

/** Called when a spoken PIN or password was dropped, so the panel can tell the owner where a PIN is typed. */
export function onSecretSpeech(listener: () => void): () => void {
  secretListeners.add(listener)
  return () => secretListeners.delete(listener)
}

export const SINK_DEADLINE_MS = 3_000

/**
 * Offers a finished spoken input. Resolves true when the words were consumed (a spoken secret that was dropped, or a command the panel took),
 * false when they should go on to the chat. Never throws.
 */
export async function offerOwnerSpeech(text: string, cause: string, deadlineMs = SINK_DEADLINE_MS): Promise<boolean> {
  if (speechRefusal(text) === 'secret') {
    for (const listener of secretListeners) {
      try {
        listener()
      }
      catch {}
    }
    return true
  }
  const current = sink
  if (!current)
    return false
  const speech = ownerSpeech(text, cause)
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const answer = await Promise.race([
      Promise.resolve(current(speech)),
      new Promise<boolean>((resolve) => {
        timer = setTimeout(resolve, deadlineMs, false)
      }),
    ])
    return answer === true
  }
  catch {
    return false
  }
  finally {
    if (timer)
      clearTimeout(timer)
    expire(speech)
  }
}
