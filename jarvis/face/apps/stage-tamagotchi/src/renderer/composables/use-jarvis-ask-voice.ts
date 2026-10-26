/**
 * Using a form by voice (D63). The owner can do with his voice everything he can do with keyboard and mouse, with the same result:
 *
 *   dictation   a small microphone on every text, number and date field (and on an editable heading) fills that field with what he says, in
 *               Telugu, Hindi or English, as he said it. Interim text shows live in the field and the final text replaces it. "delete that",
 *               "clear", "new line", "next field", "previous field" and "stop" work while dictating.
 *   commands    "tick temp files", "temp files tick chey", "set volume to 40", "next", "read it", "undo that change", "cancel", and at last
 *               "clean" or "submit". The grammar (jarvis-ask-voice-logic.ts) decides first; only when it is unsure the decision engine picks among
 *               the candidates; below its bar the panel asks "Did you mean A or B?" in words and on screen.
 *
 * How it is wired
 *   - The speech recognizer is the one the face already has (the voice host: the owner's Hearing provider). It starts when the owner presses a
 *     microphone, never when a form opens, and stops when the form closes, he stops it, or nothing is said for a while. No cloud ASR is added.
 *   - Spoken commands reach this controller by two doors, both the owner's own voice: the panel's microphone (`panel-mic`), and, through the
 *     voice store's hook (voice-command-sink.ts), a push-to-talk hold or the wake word. Ambient speech (voice activity detection alone) is ignored.
 *   - Nothing here can approve anything. A PIN, a password or an approval said aloud is refused. A button is pressed only for an explicit submit
 *     word in the owner's OWN speech (an `OwnerSpeech`, which only the voice pipeline can make), never one that approves, pays, sends or deletes.
 *     The form itself only collects choices (D55); a cleaner "clean" still cleans only what is ticked, and the backend checks it again.
 *   - Words from the form (labels, option names, hints) are matched against, never obeyed; what JARVIS speaks is not obeyed if the microphone
 *     hears it back (echo guard).
 */
import type { OwnerSpeech } from '@proj-airi/stage-ui/composables/voice-command-sink'
import type { Ref } from 'vue'

import type { AskButton, AskForm, AskNode, AskValue } from './jarvis-ask-logic'
import type { FormAction, Intent, Parsed, Resolution, ResolveContext, Scored, SpeechPlans, VoiceTarget } from './jarvis-ask-voice-logic'

import { isOwnerSpeech as isOwnerSpeechDefault, ownerSpeech } from '@proj-airi/stage-ui/composables/voice-command-sink'
import { ref, shallowRef, watch } from 'vue'

import { selectedTotal, setGroup } from './jarvis-ask-logic'
import {
  actionsFor,
  answerChoice,
  buttonBlockedForVoice,
  CONSOLE_ONLY,
  createEchoGuard,
  describeTarget,
  dictatable,
  dictationOps,
  gate,
  isFormCommand,
  joinDictation,
  keyField,
  keyOption,
  newlineText,
  nodeMap,
  parseClock,
  parseDateTime,
  parseDay,
  parseNumber,
  parseUtterance,
  readAloudPlan,
  resolveIntent,
  scriptLanguage,
  speechRefusal,
  voiceTargets,
} from './jarvis-ask-voice-logic'

// ---- what the controller needs from outside ----------------------------------------------------------------------------------------------

/** One speech input at a time from the face's voice host (the shape of `useVoiceComposer` in dictation mode). */
export interface VoiceSource {
  available: Readonly<Ref<boolean>>
  configured: Readonly<Ref<boolean>>
  phase: Readonly<Ref<'idle' | 'starting' | 'recording' | 'processing'>>
  /** The text of the input so far; it changes while the owner speaks. */
  transcript: Readonly<Ref<string>>
  start: () => Promise<void>
  /** Ends the input and returns its final text ('' when nothing was said). */
  finish: () => Promise<string>
  cancel: () => Promise<void>
}

export interface TranslateResult { english: string, sourceLang: string, translated: boolean }
export interface DecideResult { pick: string | null, confidence: number, ask: boolean, options: string[] }

/** The backend's read-only help (jarvis_ask_voice): translation on this laptop, a pick among candidates, and what may be spoken. */
export interface VoiceBridge {
  translate: (text: string) => Promise<TranslateResult | null>
  decide: (request: { phrase: string, english: string, candidates: string[] }) => Promise<DecideResult | null>
  speechPlans: () => Promise<SpeechPlans | null>
}

export interface FocusPort {
  /** Focus a field's input; false when it is not on screen. */
  focusField: (id: string) => boolean
  /** The field after or before `from` (dictatable fields in page order), or null at the end. */
  adjacentField: (from: string | null, direction: 'next' | 'previous') => string | null
  /** Move the keyboard focus one control on, like Tab; returns the label of the control. */
  moveFocus: (direction: 'next' | 'previous') => string
  focusedField: () => string | null
  /** Bring the control with this voice key into view. */
  reveal: (key: string) => void
}

export interface AskLike {
  form: Readonly<Ref<AskForm | null>>
  values: Readonly<Ref<Record<string, AskValue>>>
  busy: Readonly<Ref<boolean>>
  error: Readonly<Ref<string>>
  setValue: (id: string, value: AskValue) => void
  press: (buttonId: string) => Promise<boolean>
  close: () => Promise<void>
}

export interface AskVoiceDeps {
  ask: AskLike
  /** Called the first time a microphone is pressed (so nothing is started when a form opens). */
  source: () => VoiceSource | null
  bridge?: VoiceBridge | null
  /** Speaks a sentence through the face's own speech output and resolves when it has been played. */
  speak?: (text: string) => Promise<void>
  stopSpeaking?: () => void
  focus?: FocusPort
  now?: () => number
  today?: () => Date
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
  settings?: { punctuation?: () => boolean, speakBack?: () => boolean }
  isOwnerSpeech?: (value: unknown) => boolean
  /** Nothing said for this long ends a listening session (default 20 s). */
  idleMs?: number
  /** The most the decision engine and the translator may take (default 3 s each). */
  helpMs?: number
}

export interface Question {
  text: string
  options: Array<{ key: string, label: string, match: string }>
  intent: Intent
}

interface UndoEntry { fieldId: string, before: AskValue, after: AskValue, label: string, kind: 'voice' | 'dictation' }

export type MicState = 'idle' | 'listening' | 'understanding'

const HINT = /\b(?:chey|cheyyi|cheyi|cheyandi|chesey|karo|kar do|kardo|kijiye|vaddu|teesey|tisey|hatao|pettu|rayi|ledu|nahi|hai|undi|lo|ni|ko|ki|mein|ke|ka)\b/i

/** Words that may be Telugu, Hindi or a mix: worth a translation when the grammar found nothing. */
export function looksForeign(text: string): boolean {
  return scriptLanguage(text) !== 'en' || HINT.test(text)
}

function clip(text: string, n: number): string {
  const t = text.replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n - 1)}...` : t
}

function withDeadline<T>(promise: Promise<T>, ms: number): Promise<T | null> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, ms, null)
    promise.then((v) => {
      clearTimeout(timer)
      resolve(v)
    }, () => {
      clearTimeout(timer)
      resolve(null)
    })
  })
}

function same(a: AskValue | undefined, b: AskValue | undefined): boolean {
  return JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
}

export function useAskVoice(deps: AskVoiceDeps) {
  const { ask } = deps
  const now = deps.now ?? (() => Date.now())
  const today = deps.today ?? (() => new Date())
  const setTimer = deps.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms))
  const clearTimer = deps.clearTimer ?? ((h: unknown) => clearTimeout(h as ReturnType<typeof setTimeout>))
  const isOwner = deps.isOwnerSpeech ?? isOwnerSpeechDefault
  const idleMs = deps.idleMs ?? 20_000
  const helpMs = deps.helpMs ?? 3_000
  const echo = createEchoGuard(700, now)
  /** The one-sentence result of the last action, when it is not simply "done" (a button that was refused, nothing to undo). */
  let note = ''

  const mode = ref<'off' | 'dictate' | 'command'>('off')
  const activeField = ref<string | null>(null)
  const understanding = ref(false)
  const interim = ref('')
  const heard = ref('')
  const understoodAs = ref('')
  const did = ref('')
  const hit = ref('')
  const notice = ref('')
  const question = shallowRef<Question | null>(null)
  const reading = ref(false)
  const readShown = ref('')
  const readNotes = ref<string[]>([])
  const undoStack = shallowRef<UndoEntry[]>([])

  let runSeq = 0
  let source: VoiceSource | null | undefined
  let stopWaiting: (() => void) | undefined
  let hitTimer: unknown
  let questionTimer: unknown

  // ---- the form ------------------------------------------------------------------------------------------------------------------------

  let memo: { form: AskForm, targets: VoiceTarget[], nodes: Map<string, AskNode> } | null = null
  function view() {
    const form = ask.form.value
    if (!form)
      return null
    if (!memo || memo.form !== form)
      memo = { form, targets: voiceTargets(form.fields, form.buttons), nodes: nodeMap(form.fields) }
    return memo
  }

  function context(viaTranslation = false): ResolveContext {
    const v = view()!
    return { targets: v.targets, nodes: v.nodes, values: ask.values.value, today: today(), focusedFieldId: deps.focus?.focusedField() ?? activeField.value, viaTranslation }
  }

  // ---- what the panel shows --------------------------------------------------------------------------------------------------------------

  function micState(): MicState {
    return mode.value === 'off' ? 'idle' : understanding.value ? 'understanding' : 'listening'
  }

  function flash(key: string | undefined) {
    if (!key)
      return
    hit.value = key
    deps.focus?.reveal(key)
    if (hitTimer !== undefined)
      clearTimer(hitTimer)
    hitTimer = setTimer(() => {
      if (hit.value === key)
        hit.value = ''
    }, 2_400)
  }

  /** Shows a sentence and, when the owner asked by voice, says it. Resolves when it has been spoken. */
  async function say(text: string, options: { speak?: boolean } = {}): Promise<void> {
    did.value = text
    const speakIt = options.speak !== false && (deps.settings?.speakBack?.() ?? true) && !!deps.speak
    if (!speakIt)
      return
    echo.said(text)
    try {
      await deps.speak!(text)
    }
    catch {
      // the sentence is on screen; a speaker that fails is not an error for the form
    }
    finally {
      echo.finished()
    }
  }

  // ---- changing values, with undo --------------------------------------------------------------------------------------------------------

  function emptyFor(node: AskNode | undefined): AskValue {
    if (!node)
      return ''
    if (node.type === 'checkbox_group')
      return []
    if (node.type === 'toggle')
      return false
    if (node.type === 'number' || node.type === 'slider')
      return null
    return ''
  }

  function current(fieldId: string): AskValue {
    const v = ask.values.value[fieldId]
    return v === undefined ? emptyFor(view()?.nodes.get(fieldId)) : v
  }

  function change(fieldId: string, next: AskValue, label: string, kind: UndoEntry['kind'] = 'voice'): boolean {
    const before = current(fieldId)
    if (same(before, next))
      return false
    undoStack.value = [...undoStack.value, { fieldId, before: Array.isArray(before) ? [...before] : before, after: next, label, kind }].slice(-40)
    ask.setValue(fieldId, next)
    return true
  }

  function undoLast(): string {
    const entry = undoStack.value.at(-1)
    if (!entry)
      return 'There is nothing to undo.'
    undoStack.value = undoStack.value.slice(0, -1)
    ask.setValue(entry.fieldId, entry.before)
    flash(keyField(entry.fieldId))
    return `Undone: ${entry.label}`
  }

  async function undo(): Promise<void> {
    await say(undoLast())
  }

  // ---- doing what was understood ---------------------------------------------------------------------------------------------------------

  async function press(action: Extract<FormAction, { type: 'press' }>, by: 'voice' | 'click', speech?: OwnerSpeech): Promise<boolean> {
    const f = ask.form.value
    if (!f)
      return false
    if (by === 'voice' && (!speech || !isOwner(speech) || !speech.live))
      return false // a button is pressed by voice only for the owner's own, live speech
    if (action.buttonId === '') {
      await ask.close()
      return true
    }
    const button: AskButton | undefined = f.buttons.find(b => b.id === action.buttonId)
    if (!button)
      return false
    if (by === 'voice' && buttonBlockedForVoice(button)) {
      note = `${button.label} can only be pressed with a click. Approvals are given in the JARVIS console.`
      return false
    }
    if (action.role !== 'cancel') {
      const total = selectedTotal(f.fields, ask.values.value)
      if (button.style === 'primary' && total && total.count === 0) {
        note = 'Nothing is ticked yet.'
        return false
      }
    }
    const ok = await ask.press(button.id)
    if (!ok && ask.error.value)
      note = ask.error.value
    return ok
  }

  function keyFor(action: FormAction): string | undefined {
    switch (action.type) {
      case 'check': return keyOption(action.fieldId, action.optionId)
      case 'choose': return keyOption(action.fieldId, action.optionId)
      case 'toggle': case 'set': case 'group': return keyField(action.fieldId)
      default: return undefined
    }
  }

  async function perform(actions: FormAction[], by: 'voice' | 'click', speech?: OwnerSpeech): Promise<void> {
    for (const a of actions) {
      if (speech && !speech.live)
        return
      switch (a.type) {
        case 'check': {
          const list = Array.isArray(current(a.fieldId)) ? current(a.fieldId) as string[] : []
          const has = list.includes(a.optionId)
          if (has === a.on) {
            did.value = `${a.label} is already ${a.on ? 'ticked' : 'unticked'}.`
            break
          }
          change(a.fieldId, a.on ? [...list, a.optionId] : list.filter(x => x !== a.optionId), `${a.on ? 'Ticking' : 'Unticking'} ${a.label}`)
          break
        }
        case 'choose':
          change(a.fieldId, a.optionId, `Choosing ${a.label}`)
          break
        case 'toggle':
          change(a.fieldId, a.on, `Turning ${a.label} ${a.on ? 'on' : 'off'}`)
          break
        case 'group':
          change(a.fieldId, setGroup(current(a.fieldId), a.ids, a.on), `${a.on ? 'Ticking all of' : 'Unticking all of'} ${a.label}`)
          break
        case 'set':
          change(a.fieldId, a.value, `Setting ${a.label}`)
          break
        case 'press':
          await press(a, by, speech)
          break
        case 'focus': {
          const label = deps.focus?.moveFocus(a.direction) ?? ''
          if (label)
            did.value = `${a.direction === 'next' ? 'Next' : 'Back'}: ${label}`
          break
        }
        case 'read':
          void readAloud()
          break
        case 'undo':
          note = undoLast()
          break
        case 'stop':
          stopAll()
          break
      }
      flash(keyFor(a))
    }
  }

  // ---- understanding a spoken command ----------------------------------------------------------------------------------------------------

  function setQuestion(q: Question | null) {
    question.value = q
    if (questionTimer !== undefined)
      clearTimer(questionTimer)
    questionTimer = q
      ? setTimer(() => {
          if (question.value === q)
            question.value = null
        }, 20_000)
      : undefined
  }

  function labelOf(key: string): string {
    const t = view()?.targets.find(x => x.key === key)
    return t ? describeTarget(t) : key
  }

  async function askWhich(intent: Intent, ranked: Scored[], phrase: string, english: string, speech: OwnerSpeech | null): Promise<string> {
    const candidates = ranked.slice(0, 8).map(r => r.target.key)
    let options = candidates.slice(0, 3)
    if (deps.bridge && candidates.length >= 2) {
      understanding.value = true
      const d = await withDeadline(deps.bridge.decide({ phrase, english, candidates }), helpMs)
      understanding.value = false
      if (speech && !speech.live)
        return ''
      if (d?.pick && !d.ask && candidates.includes(d.pick)) {
        const target = view()?.targets.find(t => t.key === d.pick)
        if (target) {
          const res = actionsFor(intent, target, context())
          if (res.kind === 'actions') {
            note = ''
            await perform(res.actions, 'voice', speech ?? undefined)
            const sentence = note || res.say
            note = ''
            return sentence
          }
          return res.kind === 'refuse' || res.kind === 'notfound' || res.kind === 'need' ? res.say : ''
        }
      }
      if (d?.options?.length)
        options = d.options.filter(k => candidates.includes(k)).slice(0, 3)
      if (!options.length)
        options = candidates.slice(0, 3)
    }
    const labels = options.map(k => ({ key: k, label: labelOf(k), match: view()?.targets.find(x => x.key === k)?.label ?? labelOf(k) }))
    const text = labels.length === 1 ? `Did you mean ${labels[0].label}?` : `Did you mean ${labels.slice(0, -1).map(l => l.label).join(', ')} or ${labels.at(-1)!.label}?`
    setQuestion({ text, options: labels, intent })
    return text
  }

  async function answerQuestion(keyOrDismiss: string, speech?: OwnerSpeech): Promise<void> {
    const q = question.value
    if (!q)
      return
    setQuestion(null)
    if (keyOrDismiss === 'dismiss') {
      await say('Okay, I left it as it was.')
      return
    }
    const target = view()?.targets.find(t => t.key === keyOrDismiss)
    if (!target || !q.options.some(o => o.key === keyOrDismiss))
      return
    const res = actionsFor(q.intent, target, context())
    if (res.kind === 'actions') {
      const by = speech ? 'voice' : 'click'
      note = ''
      await perform(res.actions, by, speech)
      await say(note || res.say)
      note = ''
    }
    else if (res.kind !== 'unsure') {
      await say(res.say)
    }
  }

  function describeFailure(res: Resolution): string {
    return res.kind === 'notfound' || res.kind === 'need' || res.kind === 'refuse' ? res.say : ''
  }

  /** The grammar's verdict on a parsed utterance: do it, ask, or say why not. Resolves to the sentence for the owner. */
  async function execute(parsed: Parsed, speech: OwnerSpeech | null, viaTranslation: boolean, english: string): Promise<string> {
    if (parsed.refusal)
      return CONSOLE_ONLY
    if (!parsed.intents.length)
      return parsed.withheld ? 'I did not submit. Say it again when you are ready.' : ''
    if (ask.busy.value)
      return 'JARVIS is working on this form. Try again in a moment.'
    const said: string[] = []
    for (const intent of parsed.intents) {
      if (speech && !speech.live)
        return ''
      const res = resolveIntent(intent, context(viaTranslation))
      if (res.kind === 'actions') {
        did.value = res.say
        note = ''
        await perform(res.actions, 'voice', speech ?? undefined)
        said.push(note || res.say)
        note = ''
      }
      else if (res.kind === 'unsure') {
        said.push(await askWhich(intent, res.ranked, res.phrase, english, speech))
      }
      else {
        said.push(describeFailure(res))
      }
    }
    if (parsed.withheld)
      said.push('I did not submit. Say it again when you are ready.')
    if (parsed.mixedSubmit)
      said.push('Say clean or submit on its own when you are ready.')
    return said.filter(Boolean).join('. ')
  }

  /**
   * The owner's words as a command for the open form. Returns true when they were taken (so they do not go to the chat), false when they are not
   * for the form. Only an `OwnerSpeech` that the voice pipeline made, while a form is open, from push-to-talk, the wake word or the panel's
   * own microphone, is looked at.
   */
  async function command(speech: OwnerSpeech): Promise<boolean> {
    if (!isOwner(speech) || !speech.live)
      return false
    const g = gate({ formOpen: !!ask.form.value, cause: speech.cause })
    if (!g.ok)
      return false
    if (mode.value === 'dictate' && speech.cause !== 'panel-mic')
      return false
    const text = speech.text.trim()
    if (!text)
      return false
    if (echo.isEcho(text))
      return true
    understoodAs.value = ''
    notice.value = ''
    if (speechRefusal(text)) {
      heard.value = '' // a PIN or an approval word is not kept or shown
      await say(CONSOLE_ONLY)
      return true
    }
    heard.value = clip(text, 100)
    if (question.value) {
      const a = answerChoice(text, question.value.options.map(o => ({ key: o.key, label: o.match })))
      if (a) {
        await answerQuestion(a, speech)
        return true
      }
      setQuestion(null)
    }

    let parsed = parseUtterance(text)
    let english = text
    let viaTranslation = false
    const resolvesSomething = (p: Parsed, via: boolean) => p.intents.some(i => ['actions', 'unsure'].includes(resolveIntent(i, context(via)).kind))
    if (deps.bridge && looksForeign(text) && !resolvesSomething(parsed, false)) {
      // Telugu, Hindi or a mix that the word lists did not understand: the English of the owner's words, from the translator on this laptop
      understanding.value = true
      const t = await withDeadline(deps.bridge.translate(text), helpMs)
      understanding.value = false
      if (!speech.live)
        return false
      if (t?.translated && t.english) {
        const other = parseUtterance(t.english)
        if (resolvesSomething(other, true) || (isFormCommand(other) && !isFormCommand(parsed))) {
          english = t.english
          understoodAs.value = t.english
          parsed = other
          viaTranslation = true
        }
      }
    }
    if (!isFormCommand(parsed) && !resolvesSomething(parsed, viaTranslation))
      return false // not for the form: the chat gets it

    const sentence = await execute(parsed, speech, viaTranslation, english)
    if (speech.live && sentence)
      await say(sentence)
    return true
  }

  /** A PIN or password was said aloud and dropped before anything saw it: say where a PIN is typed. Nothing of it is kept or shown. */
  function secretHeard(): void {
    heard.value = ''
    understoodAs.value = ''
    notice.value = CONSOLE_ONLY
    if (ask.form.value)
      void say(CONSOLE_ONLY)
  }

  // ---- dictation -------------------------------------------------------------------------------------------------------------------------

  function numberFor(node: AskNode, text: string): AskValue | undefined {
    if (node.type === 'number') {
      const n = parseNumber(text)
      if (n === null)
        return undefined
      const lo = Number(node.min)
      const hi = Number(node.max)
      return Math.min(Number.isFinite(hi) ? hi : n, Math.max(Number.isFinite(lo) ? lo : n, n))
    }
    const mode2 = String(node.mode ?? 'date')
    const v = mode2 === 'time' ? parseClock(text) : mode2 === 'datetime' ? parseDateTime(text, today()) : parseDay(text, today())
    return v ?? undefined
  }

  async function applyDictation(text: string): Promise<void> {
    const fieldId = activeField.value
    const node = fieldId ? view()?.nodes.get(fieldId) : undefined
    if (!fieldId || !node)
      return
    if (speechRefusal(text) === 'secret') {
      notice.value = CONSOLE_ONLY
      return
    }
    if (echo.isEcho(text))
      return
    const label = node.label || fieldId
    const multiline = node.multiline === true
    const ops = dictationOps(text, { punctuation: deps.settings?.punctuation?.() ?? true, multiline })
    for (const op of ops) {
      switch (op.type) {
        case 'text': {
          if (node.type === 'text') {
            const max = Number(node.max_len) || 2000
            change(fieldId, joinDictation(String(current(fieldId) ?? ''), op.text).slice(0, max), `Dictated text in ${label}`, 'dictation')
            did.value = `Added to ${label}`
          }
          else {
            const v = numberFor(node, op.text)
            if (v === undefined)
              notice.value = node.type === 'number' ? `I did not get a number for ${label}.` : `I did not get a ${String(node.mode ?? 'date') === 'time' ? 'time' : 'day'} for ${label}.`
            else if (change(fieldId, v, `Dictated ${label}`, 'dictation'))
              did.value = `${label}: ${String(v).replace('T', ' ')}`
          }
          break
        }
        case 'newline':
          if (node.type === 'text')
            change(fieldId, joinDictation(String(current(fieldId) ?? ''), newlineText(op, multiline)), `New line in ${label}`, 'dictation')
          break
        case 'clear':
          change(fieldId, emptyFor(node), `Cleared ${label}`, 'dictation')
          did.value = `Cleared ${label}`
          break
        case 'delete': {
          const entry = [...undoStack.value].reverse().find(e => e.kind === 'dictation' && e.fieldId === fieldId)
          if (entry) {
            undoStack.value = undoStack.value.filter(e => e !== entry)
            ask.setValue(fieldId, entry.before)
            did.value = `Deleted the last thing said in ${label}`
          }
          else if (node.type === 'text') {
            const cut = String(current(fieldId) ?? '').replace(/\s*\S+\s*$/, '')
            change(fieldId, cut, `Deleted a word in ${label}`, 'dictation')
          }
          break
        }
        case 'next':
        case 'previous': {
          const to = deps.focus?.adjacentField(fieldId, op.type)
          if (to) {
            activeField.value = to
            deps.focus?.focusField(to)
          }
          else {
            did.value = op.type === 'next' ? 'That was the last field.' : 'That was the first field.'
          }
          return // the rest of the speech belongs to the field it was said in
        }
        case 'stop':
          stopAll()
          return
      }
    }
  }

  // ---- the listening loop ----------------------------------------------------------------------------------------------------------------

  type End = 'ended' | 'idle' | 'error' | 'stopped'

  function waitForEnd(src: VoiceSource): Promise<End> {
    return new Promise<End>((resolve) => {
      let finished = false
      let timer: unknown
      let started = false
      const stops: Array<() => void> = []
      const done = (how: End) => {
        if (finished)
          return
        finished = true
        if (timer !== undefined)
          clearTimer(timer)
        stops.forEach(s => s())
        stopWaiting = undefined
        resolve(how)
      }
      const arm = () => {
        if (timer !== undefined)
          clearTimer(timer)
        timer = setTimer(() => done(src.transcript.value.trim() ? 'ended' : 'idle'), idleMs)
      }
      stopWaiting = () => done('stopped')
      stops.push(watch(src.phase, (p) => {
        if (p !== 'idle')
          started = true
        if (p === 'processing')
          done('ended')
        else if (p === 'idle' && started)
          done('error')
      }, { flush: 'sync', immediate: true }))
      stops.push(watch(src.transcript, arm, { flush: 'sync' }))
      arm()
    })
  }

  function ensureSource(): VoiceSource | null {
    if (source === undefined)
      source = deps.source()
    if (!source || !source.available.value) {
      notice.value = 'The microphone is not connected yet.'
      return null
    }
    if (!source.configured.value) {
      notice.value = 'Set up a speech provider in Settings > Hearing to use the microphone.'
      return null
    }
    return source
  }

  async function listen(kind: 'dictate' | 'command', run: number, src: VoiceSource): Promise<void> {
    const stopInterim = watch(src.transcript, (t) => {
      if (run === runSeq)
        interim.value = t
    }, { flush: 'sync' })
    let idleSince = now()
    try {
      // eslint-disable-next-line no-unmodified-loop-condition -- runSeq changes while the awaits below are pending
      while (run === runSeq && ask.form.value) {
        try {
          await src.start()
        }
        catch {
          notice.value = 'I could not start the microphone.'
          break
        }
        if (run !== runSeq)
          break
        const end = await waitForEnd(src)
        if (run !== runSeq || end === 'stopped')
          break
        if (end === 'error') {
          notice.value ||= 'The microphone stopped.'
          break
        }
        understanding.value = true
        let text = ''
        try {
          text = (await src.finish()) ?? ''
        }
        catch {
          text = ''
        }
        understanding.value = false
        interim.value = ''
        if (run !== runSeq)
          break
        if (text.trim()) {
          idleSince = now()
          if (kind === 'dictate')
            await applyDictation(text)
          else
            await command(ownerSpeech(text, 'panel-mic'))
        }
        else if (now() - idleSince >= idleMs || end === 'idle') {
          notice.value = 'The microphone is off: nothing was said.'
          break
        }
      }
    }
    finally {
      stopInterim()
      if (run === runSeq) {
        runSeq++
        mode.value = 'off'
        activeField.value = null
        understanding.value = false
        interim.value = ''
        if (src.phase.value !== 'idle')
          void src.cancel().catch(() => {})
      }
    }
  }

  // ---- the controls the panel calls ------------------------------------------------------------------------------------------------------

  /** Stops listening, speaking and asking. Safe to call at any time. */
  function stopAll(): void {
    runSeq++
    stopWaiting?.()
    const wasListening = mode.value !== 'off'
    mode.value = 'off'
    activeField.value = null
    understanding.value = false
    interim.value = ''
    setQuestion(null)
    if (wasListening && source)
      void source.cancel().catch(() => {})
    if (reading.value)
      stopReading()
  }

  function startDictation(fieldId: string): void {
    const node = view()?.nodes.get(fieldId)
    if (!node || !dictatable(node)) {
      notice.value = 'This field cannot be filled by voice.'
      return
    }
    if (ask.busy.value)
      return
    stopAll()
    notice.value = ''
    const src = ensureSource()
    if (!src)
      return
    mode.value = 'dictate'
    activeField.value = fieldId
    deps.focus?.focusField(fieldId)
    void listen('dictate', ++runSeq, src)
  }

  function startCommand(): void {
    if (!ask.form.value || ask.busy.value)
      return
    stopAll()
    notice.value = ''
    const src = ensureSource()
    if (!src)
      return
    mode.value = 'command'
    void listen('command', ++runSeq, src)
  }

  /** The microphone of a field: starts dictation there, or stops it. */
  function toggleField(fieldId: string): void {
    if (mode.value === 'dictate' && activeField.value === fieldId)
      stopAll()
    else
      startDictation(fieldId)
  }

  /** Alt+M: stops if listening; otherwise dictates into the focused field, or listens for commands when no field is focused. */
  function toggleMic(): void {
    if (mode.value !== 'off') {
      stopAll()
      return
    }
    const focused = deps.focus?.focusedField()
    const node = focused ? view()?.nodes.get(focused) : undefined
    if (node && dictatable(node))
      startDictation(focused!)
    else
      startCommand()
  }

  // ---- reading the form aloud ------------------------------------------------------------------------------------------------------------

  function anyForeign(form: AskForm): boolean {
    const texts = [form.title, form.intro, ...Object.values(ask.values.value).filter((v): v is string => typeof v === 'string')]
    return texts.some(t => scriptLanguage(t) !== 'en') || voiceTargets(form.fields, form.buttons).some(t => scriptLanguage(t.label) !== 'en')
  }

  async function readAloud(): Promise<void> {
    const form = ask.form.value
    if (!form)
      return
    if (reading.value)
      stopReading()
    let plans: SpeechPlans | null = null
    if (anyForeign(form) && deps.bridge)
      plans = await withDeadline(deps.bridge.speechPlans(), helpMs)
    const plan = readAloudPlan(form, ask.values.value, plans)
    readShown.value = plan.shown
    readNotes.value = plan.notes
    if (!deps.speak) {
      readNotes.value = [...plan.notes, 'Speech output is not available, so the text is only shown.']
      return
    }
    reading.value = true
    echo.said(plan.spoken)
    try {
      await deps.speak(plan.spoken)
    }
    catch {
      readNotes.value = [...readNotes.value, 'I could not speak it; the text is shown.']
    }
    finally {
      echo.finished()
      reading.value = false
    }
  }

  function stopReading(): void {
    deps.stopSpeaking?.()
    reading.value = false
    echo.finished()
  }

  function dismissReading(): void {
    stopReading()
    readShown.value = ''
    readNotes.value = []
  }

  // ---- lifecycle -------------------------------------------------------------------------------------------------------------------------

  // A form that closes or is replaced ends everything: the microphone, the speech, the question. The undo list belongs to one form.
  watch(() => ask.form.value?.id ?? null, (id, old) => {
    if (id === old)
      return
    stopAll()
    undoStack.value = []
    heard.value = ''
    understoodAs.value = ''
    did.value = ''
    hit.value = ''
    notice.value = ''
    readShown.value = ''
    readNotes.value = []
    echo.reset()
    memo = null
  }, { flush: 'sync' })

  return {
    mode,
    activeField,
    understanding,
    interim,
    heard,
    understoodAs,
    did,
    hit,
    notice,
    question,
    reading,
    readShown,
    readNotes,
    undoStack,
    micState,
    command,
    secretHeard,
    answerQuestion,
    startDictation,
    startCommand,
    toggleField,
    toggleMic,
    stop: stopAll,
    undo,
    readAloud,
    stopReading,
    dismissReading,
    /** The interim text for a field while it is being dictated, else ''. */
    interimFor: (fieldId: string): string => mode.value === 'dictate' && activeField.value === fieldId ? interim.value : '',
    listeningOn: (fieldId: string): boolean => mode.value === 'dictate' && activeField.value === fieldId,
    dispose: stopAll,
  }
}

export type AskVoice = ReturnType<typeof useAskVoice>

/** The key under which the panel hands the voice controller to its parts (AskNode). */
export const ASK_VOICE_KEY = Symbol('jarvis-ask-voice')
