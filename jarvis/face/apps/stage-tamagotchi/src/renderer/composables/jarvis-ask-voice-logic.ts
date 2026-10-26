/**
 * Voice for the ask panel's forms (D63): the pure rules. Everything that decides what the owner's words MEAN lives here, deterministic and
 * testable without a microphone, a model or a browser. `use-jarvis-ask-voice.ts` runs it.
 *
 * What is in this file
 *   - the controls of an open form as voice targets (`voiceTargets`), with the same keys the backend builds (jarvis/bridge/ask_voice.py)
 *   - fuzzy matching of a spoken phrase to a label (`labelScore`, `rank`)
 *   - the command grammar (`parseUtterance`, `resolveIntent`): English, Telugu, Hindi and the Latin-script mixes (Tenglish, Hinglish)
 *   - value parsers for numbers, dates and times, and dictation editing ("new line", "delete that", punctuation words)
 *   - the safety rules: who may command (`gate`), what is never done by voice (PIN, approvals, risky buttons), echo of our own speech
 *   - the read-aloud script (`readAloudPlan`)
 *
 * Safety in one place (D55: a form only collects choices; D63: voice cannot approve anything)
 *   - A button is pressed by voice only for an explicit submit word that is the WHOLE phrase, in the owner's own speech (never a translation
 *     of it, never mixed with other commands, never negated or postponed), and never a button that approves, pays, sends, deletes or installs.
 *   - A phrase that carries a PIN, a password or an approval is refused here and never reaches a model, a field or a button.
 *   - Text read from the form (labels, hints, option names) is only ever matched AGAINST; it is never parsed as a command.
 */
import type { AskButton, AskNode, AskOption, AskValue } from './jarvis-ask-logic'

import { CONSOLE_ONLY, speechRefusal } from '@proj-airi/stage-ui/composables/voice-secret-guard'

import { INPUT_TYPES, isMulti, walk } from './jarvis-ask-logic'

export { CONSOLE_ONLY, speechRefusal }

// ---- text -----------------------------------------------------------------------------------------------------------------------------------

const DIGIT_BASES = [0x0966, 0x0C66] // Devanagari, Telugu
const JOINERS = /[\u200B-\u200D\u2060\uFEFF]/g

export function asciiDigits(text: string): string {
  let out = ''
  for (const ch of text) {
    const code = ch.codePointAt(0) ?? 0
    const base = DIGIT_BASES.find(b => code >= b && code <= b + 9)
    out += base === undefined ? ch : String(code - base)
  }
  return out
}

/** Lower case, no punctuation, Indic digits as 0-9, joiners removed, "don't" as "do not". Letters and combining marks of every script stay. */
export function normalize(text: string): string {
  return asciiDigits(String(text ?? '').normalize('NFKC').replace(JOINERS, ''))
    .toLowerCase()
    .replace(/n['’]t\b/g, ' not')
    .replace(/[^\p{L}\p{M}\p{N}\s]/gu, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}

export interface Tok { raw: string, norm: string }

/** Words of an utterance with their original spelling (the owner's text for a field is taken from `raw`). A comma or full stop becomes a ',' token. */
export function tokenize(text: string): Tok[] {
  const spaced = String(text ?? '').replace(/(?<=\p{L})[-/](?=\p{L})/gu, ' ').replace(/([,;।])/g, ' $1 ')
  const out: Tok[] = []
  for (const piece of spaced.split(/\s+/)) {
    if (!piece)
      continue
    if (/^[,;।]$/.test(piece)) {
      out.push({ raw: ',', norm: ',' })
      continue
    }
    const raw = piece.replace(/^["'“‘]+|["'”’]+$/g, '')
    let first = true
    for (const norm of normalize(piece).split(' ')) {
      if (!norm)
        continue
      out.push({ raw: first ? raw : '', norm }) // "Satish's" is two words to the grammar and one in the owner's text
      first = false
    }
  }
  return out
}

// ---- who may command; what is never done by voice -------------------------------------------------------------------------------------

/** Where a spoken input came from. `speech` is voice activity detection in the always-on mode: it is ambient and never commands a form. */
export type VoiceCause = 'control' | 'wake-word' | 'panel-mic' | 'speech' | string

export interface GateInput { formOpen: boolean, cause: VoiceCause | undefined }

/**
 * Commands are accepted only while a form is open AND the owner pressed push-to-talk, said the wake word, or turned the panel's microphone on.
 * Ambient speech (a TV, a call in the room) arrives as `speech` and is ignored.
 */
export function gate(input: GateInput): { ok: boolean, reason?: 'no-form' | 'ambient' } {
  if (!input.formOpen)
    return { ok: false, reason: 'no-form' }
  if (input.cause === 'control' || input.cause === 'wake-word' || input.cause === 'panel-mic')
    return { ok: true }
  return { ok: false, reason: 'ambient' }
}

/** The backend refuses these labels too (ask_spec.SECRET_FIELD). Defense in depth: a field like that is never a voice target and has no mic. */
const SECRET_FIELD = /\b(?:pin|passw\w*|passcode|otp|one[- ]time|2fa|secret|token|api[ _-]?key|cvv|card number|seed phrase)\b/i
export function isSecretLabel(label: string): boolean {
  return SECRET_FIELD.test(label)
}

/** Buttons that only the owner's click may press: they approve, pay, send, delete, install or sign in. */
const RISKY_BUTTON = /approv|authori[sz]|\bpay(?:ment)?\b|purchase|\bbuy\b|\bdelete|uninstall|\binstall|\bsend\b|sign ?in|log ?in|password|\bpin\b/i
export function buttonBlockedForVoice(button: Pick<AskButton, 'id' | 'label'>): boolean {
  return RISKY_BUTTON.test(`${button.id} ${button.label}`)
}

// ---- the controls of a form as targets --------------------------------------------------------------------------------------------------

export type TargetKind = 'field' | 'option' | 'button'

export interface VoiceTarget {
  key: string
  kind: TargetKind
  label: string
  fieldId?: string
  optionId?: string
  fieldLabel?: string
  nodeType?: string
  multi?: boolean
  required?: boolean
  button?: AskButton
}

export const keyField = (id: string): string => `f:${id}`
export const keyOption = (field: string, option: string): string => `o:${field}/${option}`
export const keyButton = (id: string): string => `b:${id}`

function rowLabel(node: AskNode, row: { id: string, cells?: Record<string, string> }): string {
  const cols = ((node.columns as Array<{ id: string }> | undefined) ?? []).slice(0, 2).map(c => c.id)
  return cols.map(c => row.cells?.[c] ?? '').filter(Boolean).join(' ') || row.id
}

/** Every control of the form the owner could name: inputs, the options of a choice, the rows of a table, the buttons. Secret-looking fields are left out. */
export function voiceTargets(fields: AskNode[], buttons: AskButton[]): VoiceTarget[] {
  const out: VoiceTarget[] = []
  for (const n of walk(fields)) {
    if (!n.id || !INPUT_TYPES.has(n.type) || isSecretLabel(n.label ?? ''))
      continue
    const fieldLabel = n.label || n.id
    out.push({ key: keyField(n.id), kind: 'field', label: fieldLabel, fieldId: n.id, fieldLabel, nodeType: n.type, multi: isMulti(n), required: n.required === true })
    for (const o of (n.options ?? []) as AskOption[])
      out.push({ key: keyOption(n.id, o.id), kind: 'option', label: o.label, fieldId: n.id, optionId: o.id, fieldLabel, nodeType: n.type, multi: isMulti(n) })
    if (n.type === 'table') {
      for (const r of (n.rows as Array<{ id: string, cells?: Record<string, string> }> | undefined) ?? [])
        out.push({ key: keyOption(n.id, r.id), kind: 'option', label: rowLabel(n, r), fieldId: n.id, optionId: r.id, fieldLabel, nodeType: 'table', multi: isMulti(n) })
    }
  }
  for (const b of buttons)
    out.push({ key: keyButton(b.id), kind: 'button', label: b.label, button: b })
  return out
}

export function describeTarget(t: VoiceTarget): string {
  return t.kind === 'option' && t.fieldLabel && t.fieldLabel !== t.label ? `${t.label} (${t.fieldLabel})` : t.label
}

// ---- fuzzy matching of a phrase to a label ---------------------------------------------------------------------------------------------

const LABEL_STOP = new Set(['the', 'a', 'an', 'of', 'and', 'for', 'to', 'in', 'on', 'my', 'your', 'or', 'with'])
const PHRASE_STOP = new Set([...LABEL_STOP, 'option', 'options', 'box', 'checkbox', 'check', 'field', 'setting', 'settings', 'item', 'items', 'row', 'button', 'toggle', 'one', 'please', 'it', 'this', 'that', 'those', 'these', 'all', 'everything'])

function stem(word: string): string {
  if (word.length > 4 && word.endsWith('ies'))
    return `${word.slice(0, -3)}y`
  if (word.length > 4 && word.endsWith('es') && /(?:ch|sh|x|ss)es$/.test(word))
    return word.slice(0, -2)
  if (word.length > 3 && word.endsWith('s') && !word.endsWith('ss'))
    return word.slice(0, -1)
  return word
}

function words(text: string, stop: ReadonlySet<string>): string[] {
  return normalize(text).split(' ').filter(w => w && !stop.has(w)).map(stem)
}

function distance(a: string, b: string): number {
  const prev = Array.from({ length: b.length + 1 }, (_, j) => j)
  for (let i = 1; i <= a.length; i++) {
    let diag = prev[0]
    prev[0] = i
    for (let j = 1; j <= b.length; j++) {
      const up = prev[j]
      prev[j] = Math.min(prev[j] + 1, prev[j - 1] + 1, diag + (a[i - 1] === b[j - 1] ? 0 : 1))
      diag = up
    }
  }
  return prev[b.length]
}

function similarity(p: string, l: string): number {
  if (p === l)
    return 1
  const short = Math.min(p.length, l.length)
  if (short >= 3 && (p.startsWith(l) || l.startsWith(p)))
    return 0.9
  const m = Math.max(p.length, l.length)
  const d = distance(p, l)
  if (m >= 4 && d <= 2 && d / m <= 0.34)
    return 1 - d / m
  return 0
}

/** 0 to 1: how well a spoken phrase names a label. Order does not matter; a prefix ("temp" for "temporary") and a small slip of the recognizer count. */
export function labelScore(phrase: string, label: string, coverageWeight = 0.4): number {
  const p = words(phrase, PHRASE_STOP)
  const l = words(label, LABEL_STOP)
  if (!p.length || !l.length)
    return 0
  const best = (w: string, list: string[]) => Math.max(...list.map(x => similarity(w, x)))
  const recall = p.reduce((s, w) => s + best(w, l), 0) / p.length
  const coverage = l.reduce((s, w) => s + best(w, p), 0) / l.length
  return recall >= 0.999 && coverage >= 0.999 ? 1 : (1 - coverageWeight) * recall + coverageWeight * coverage
}

export interface Scored { target: VoiceTarget, score: number }

export const SURE = 0.82
export const MARGIN = 0.12
export const FLOOR = 0.45

/** Rank targets for a phrase. For buttons pass `coverageWeight` 0: "clean" names "Clean selected" even though the label has more words. */
export function rank(phrase: string, candidates: VoiceTarget[], coverageWeight = 0.4): Scored[] {
  return candidates
    .map(target => ({ target, score: labelScore(phrase, target.label, coverageWeight) }))
    .filter(s => s.score > 0)
    .sort((a, b) => b.score - a.score)
}

export type Match = { kind: 'sure', best: Scored } | { kind: 'unsure', ranked: Scored[] } | { kind: 'none' }

export function judge(ranked: Scored[]): Match {
  const top = ranked[0]
  if (!top || top.score < FLOOR)
    return { kind: 'none' }
  const second = ranked[1]
  if (top.score >= SURE && (!second || top.score - second.score >= MARGIN))
    return { kind: 'sure', best: top }
  return { kind: 'unsure', ranked: ranked.filter(r => r.score >= FLOOR).slice(0, 8) }
}

// ---- the lexicon ----------------------------------------------------------------------------------------------------------------------
// English, Telugu and Hindi in their own scripts, and the Latin-script mixes owners really speak. A phrase is a list of normalized words.

const L = (...phrases: string[]): string[][] => phrases.map(p => normalize(p).split(' '))

const ON_VERBS = L('tick', 'check', 'select', 'choose', 'pick', 'mark', 'enable', 'include', 'add', 'turn on', 'switch on', 'enchuko', 'ennuko', 'enchukondi', 'chuno', 'chunein', 'chuniye', 'టిక్', 'ఎంచుకో', 'ఎంచుకోండి', 'సెలెక్ట్', 'చెక్', 'चुनो', 'चुनें', 'टिक', 'सेलेक्ट', 'चेक')
const OFF_VERBS = L('untick', 'uncheck', 'deselect', 'unselect', 'unmark', 'exclude', 'disable', 'skip', 'turn off', 'switch off', 'teesey', 'teeseyi', 'teesiveyyi', 'tisey', 'tiseyyi', 'teesivey', 'hatao', 'hatado', 'hata', 'vaddu', 'తీసేయి', 'తీసివేయి', 'వద్దు', 'అన్టిక్', 'हटाओ', 'हटा', 'अनटिक', 'अनचेक')
const SET_VERBS = L('set', 'make', 'change', 'update', 'fill', 'fill in', 'type', 'write', 'enter', 'input', 'put', 'pettu', 'pettandi', 'rayi', 'raayi', 'rayandi', 'vrayi', 'likho', 'daalo', 'dalo', 'bharo', 'రాయి', 'రాయండి', 'పెట్టు', 'लिखो', 'डालो', 'भरो')
const PRESS_VERBS = L('press', 'click', 'tap', 'hit', 'push')
const NEGATIONS = new Set(['not', 'no', 'never', 'without', 'vaddu', 'mat', 'nahi', 'nahin', 'వద్దు', 'मत', 'नहीं'])
const POSTPONE = new Set(['later', 'wait', 'hold', 'yet', 'before', 'after', 'if', 'when', 'unless', 'first', 'tomorrow', 'ruko', 'aagu', 'ఆగు', 'रुको', 'बाद'])
const LIGHT = new Set(['chey', 'cheyyi', 'cheyi', 'cheyandi', 'chesey', 'chei', 'cheyyandi', 'chestha', 'karo', 'kar', 'kardo', 'karna', 'karein', 'kijiye', 'kijie', 'kariye', 'do', 'dijiye', 'చేయి', 'చేయండి', 'చెయ్యి', 'చెయ్', 'करो', 'करें', 'कीजिए', 'कर', 'दो', 'कीजिये'])
const FILLER = new Set(['please', 'jarvis', 'hey', 'hi', 'now', 'just', 'kindly', 'can', 'you', 'could', 'would', 'will', 'me', 'ippudu', 'abhi', 'ab', 'the', 'a', 'an', 'and', 'also', 'then', 'ok jarvis'])
const POSTPOSITIONS = new Set(['lo', 'loo', 'ni', 'ki', 'ko', 'me', 'mein', 'par', 'pe', 'లో', 'ని', 'కి', 'को', 'में', 'पर', 'पे'])
const CONJUNCTIONS = [...L('and', 'then', 'and then', 'also', 'plus', 'after that', 'tarvata', 'taruvata', 'aur', 'phir', 'uske baad', 'తర్వాత', 'और', 'फिर'), [',']]

const SUBMIT_GENERIC = L('submit', 'ok', 'okay', 'done', 'continue', 'proceed', 'go ahead', 'ok done', 'okay done', 'సబ్మిట్', 'ఓకే', 'ఓకె', 'సరే', 'అయిపోయింది', 'సరి', 'sare', 'सबमिट', 'ओके', 'ठीक है', 'हो गया', 'theek hai')
const SUBMIT_SPECIFIC = L('set', 'clean', 'clean up', 'cleanup', 'clean selected', 'clean it', 'clean now', 'apply', 'apply changes', 'save', 'save it', 'save here', 'set it', 'క్లీన్', 'క్లీన్ అప్', 'సేవ్', 'అప్లై', 'क्लीन', 'साफ', 'सेव', 'अप्लाई')
const CANCEL_PHRASES = L('cancel', 'close', 'dismiss', 'never mind', 'nevermind', 'forget it', 'exit', 'close it', 'cancel it', 'raddu', 'raddu chey', 'band', 'band karo', 'radd', 'radd karo', 'రద్దు', 'క్లోజ్', 'కాన్సిల్', 'रद्द', 'बंद', 'कैंसल', 'क्लोज')
const NEXT_PHRASES = L('next', 'next field', 'next one', 'next input', 'go next', 'move next', 'tab', 'tarvata', 'tarvata di', 'agla', 'agle', 'aage', 'నెక్స్ట్', 'అగ్లా', 'नेक्स्ट', 'अगला', 'आगे')
const PREV_PHRASES = L('back', 'previous', 'previous field', 'previous one', 'go back', 'last field', 'venakki', 'venaka', 'pichla', 'peeche', 'బ్యాక్', 'వెనక్కి', 'వెనుక', 'बैक', 'पिछला', 'पीछे')
const READ_PHRASES = L('read', 'read it', 'read it aloud', 'read the form', 'read the form aloud', 'read form', 'read this', 'read out', 'read aloud', 'read it out', 'read out loud', 'read it to me', 'what does it say', 'chadavu', 'chadivi vinipinchu', 'chadivi cheppu', 'padho', 'padh ke sunao', 'padhkar sunao', 'sunao', 'చదువు', 'చదివి వినిపించు', 'పढ़ो', 'पढ़ो', 'पढ़कर सुनाओ', 'पढ़ के सुनाओ')
const UNDO_PHRASES = L('undo', 'undo that', 'undo that change', 'undo it', 'undo last', 'undo the last change', 'undo last change', 'revert', 'revert that', 'take that back', 'wapas', 'wapas karo', 'venakki teesuko', 'అన్డూ', 'అన్‌డూ', 'अनडू', 'वापस', 'वापस करो')
const STOP_PHRASES = L('stop', 'stop listening', 'mic off', 'turn the mic off', 'turn mic off', 'enough', 'that is all', 'thats all', 'aapu', 'ruko', 'bas', 'ఆపు', 'रुको', 'बस')
const ALL_WORDS = new Set(['all', 'everything', 'every', 'anni', 'అన్నీ', 'sab', 'सब', 'sabhi', 'सभी'])

function startsWith(tokens: string[], phrase: string[], at: number): boolean {
  return phrase.every((w, i) => tokens[at + i] === w)
}

function findPhrase(tokens: string[], phrases: string[][]): { at: number, len: number } | null {
  let best: { at: number, len: number } | null = null
  for (const p of phrases) {
    for (let i = 0; i + p.length <= tokens.length; i++) {
      if (startsWith(tokens, p, i) && (!best || p.length > best.len || (p.length === best.len && i < best.at))) {
        best = { at: i, len: p.length }
        break
      }
    }
  }
  return best
}

function equalsPhrase(tokens: string[], phrases: string[][]): boolean {
  return phrases.some(p => p.length === tokens.length && p.every((w, i) => tokens[i] === w))
}

/** Words that never matter to what a whole-phrase command means. */
function stripSoft(tokens: string[]): string[] {
  return tokens.filter(t => !FILLER.has(t) && !LIGHT.has(t) && !['it', 'this', 'that', 'form', 'to', 'me', 'for'].includes(t))
}

// ---- intents --------------------------------------------------------------------------------------------------------------------------

export type Intent
  = { type: 'on' | 'off', phrase: string, all: boolean }
    | { type: 'set', fieldPhrase: string, value: string, verb: string }
    | { type: 'submit', word: string, generic: boolean }
    | { type: 'press', phrase: string }
    | { type: 'cancel' }
    | { type: 'next' }
    | { type: 'previous' }
    | { type: 'read' }
    | { type: 'undo' }
    | { type: 'stop' }
    | { type: 'bare', phrase: string }

export interface Parsed {
  intents: Intent[]
  /** A submit word was spoken with something that negates or postpones it ("don't clean yet"). Nothing is submitted. */
  withheld: boolean
  /** A submit word was mixed with other commands. The other commands run; the submit does not. */
  mixedSubmit: boolean
  refusal: 'secret' | 'approval' | null
}

function cleanTokens(tokens: Tok[]): Tok[] {
  const t = tokens.slice()
  // a leading wake-ish "hey jarvis" / "ok jarvis" is not a command
  const norm = t.map(x => x.norm)
  const j = norm.indexOf('jarvis')
  if (j >= 0 && j <= 1 && norm.slice(0, j).every(w => ['hey', 'hi', 'ok', 'okay', 'oh'].includes(w)))
    t.splice(0, j + 1)
  return t.filter(x => x.norm !== 'jarvis')
}

function splitSegments(tokens: Tok[]): Tok[][] {
  const segs: Tok[][] = []
  let cur: Tok[] = []
  const norm = tokens.map(t => t.norm)
  for (let i = 0; i < tokens.length;) {
    const hit = findPhrase(norm.slice(i), CONJUNCTIONS)
    if (hit && hit.at === 0) {
      if (cur.length)
        segs.push(cur)
      cur = []
      i += hit.len
      continue
    }
    cur.push(tokens[i])
    i++
  }
  if (cur.length)
    segs.push(cur)
  return segs
}

interface SegmentRead {
  intent: Intent | null
  verb: 'on' | 'off' | null
  pressed?: boolean
  rest: Tok[]
  negated: boolean
  postponed: boolean
}

function removeAt(tokens: Tok[], at: number, len: number): Tok[] {
  return [...tokens.slice(0, at), ...tokens.slice(at + len)]
}

function wholeCommand(norm: string[]): Intent | null {
  const soft = stripSoft(norm)
  if (!soft.length)
    return null
  if (equalsPhrase(soft, SUBMIT_GENERIC))
    return { type: 'submit', word: soft.join(' '), generic: true }
  if (equalsPhrase(soft, SUBMIT_SPECIFIC))
    return { type: 'submit', word: soft.join(' '), generic: false }
  if (equalsPhrase(soft, CANCEL_PHRASES))
    return { type: 'cancel' }
  if (equalsPhrase(soft, NEXT_PHRASES))
    return { type: 'next' }
  if (equalsPhrase(soft, PREV_PHRASES))
    return { type: 'previous' }
  if (equalsPhrase(soft, READ_PHRASES) || equalsPhrase(norm, READ_PHRASES))
    return { type: 'read' }
  if (equalsPhrase(soft, UNDO_PHRASES) || equalsPhrase(norm, UNDO_PHRASES))
    return { type: 'undo' }
  if (equalsPhrase(soft, STOP_PHRASES) || equalsPhrase(norm, STOP_PHRASES))
    return { type: 'stop' }
  return null
}

const ENGLISH_SET = new Set(['set', 'make', 'change', 'update', 'fill', 'fill in', 'type', 'write', 'enter', 'input', 'put'])

/** The text of a value in the owner's own spelling, without a final full stop that the recognizer added. */
function valueText(tokens: Tok[]): string {
  return tokens.filter(t => t.raw).map(t => t.raw).join(' ').replace(/\s+([,;])/g, '$1').replace(/[.?!]+$/, '').trim()
}

function stripSetNoise(tokens: Tok[]): Tok[] {
  const noise = findPhrase(tokens.map(t => t.norm), SET_VERBS)
  return noise ? removeAt(tokens, noise.at, noise.len) : tokens
}

function readSegment(seg: Tok[]): SegmentRead {
  const norm = seg.map(t => t.norm)
  const negated = norm.some(w => NEGATIONS.has(w))
  const postponed = negated || norm.some(w => POSTPONE.has(w))
  const whole = wholeCommand(norm.filter(w => !NEGATIONS.has(w) && !POSTPONE.has(w)))
  if (whole)
    return { intent: whole, verb: null, rest: [], negated, postponed }
  const off = findPhrase(norm, OFF_VERBS)
  const on = findPhrase(norm, ON_VERBS)
  const set = findPhrase(norm, SET_VERBS)
  const press = findPhrase(norm, PRESS_VERBS)
  const setVerb = set ? norm.slice(set.at, set.at + set.len).join(' ') : ''
  // "turn night light on", "switch the mic off"
  const turn = norm.findIndex(w => w === 'turn' || w === 'switch')
  const flip = turn >= 0 ? norm.findIndex((w, i) => i > turn && (w === 'on' || w === 'off')) : -1
  if (flip > 0 && !set) {
    const rest = seg.filter((_, i) => i !== turn && i !== flip)
    return { intent: null, verb: norm[flip] === 'on' ? 'on' : 'off', rest, negated: false, postponed }
  }
  // a Telugu or Hindi "put" next to a tick word is a light verb ("tick pettu"); an English set word, or a lone one, sets a field
  if (set && ((!on && !off) || ENGLISH_SET.has(setVerb))) {
    const { field, value } = splitFieldValue(removeAt(seg, set.at, set.len), setVerb)
    return { intent: { type: 'set', fieldPhrase: field, value, verb: setVerb }, verb: null, rest: [], negated, postponed }
  }
  if (press && !on && !off)
    return { intent: null, verb: null, pressed: true, rest: removeAt(seg, press.at, press.len), negated, postponed }
  if (off) {
    let rest = removeAt(seg, off.at, off.len)
    const on2 = findPhrase(rest.map(t => t.norm), ON_VERBS)
    if (on2)
      rest = removeAt(rest, on2.at, on2.len)
    return { intent: null, verb: 'off', rest: stripSetNoise(rest), negated, postponed }
  }
  if (on)
    return { intent: null, verb: negated ? 'off' : 'on', rest: stripSetNoise(removeAt(seg, on.at, on.len)), negated, postponed }
  return { intent: null, verb: null, rest: seg, negated, postponed }
}

function phraseOf(tokens: Tok[]): { text: string, all: boolean } {
  let all = false
  const kept: string[] = []
  for (const t of tokens) {
    if (ALL_WORDS.has(t.norm)) {
      all = true
      continue
    }
    if (NEGATIONS.has(t.norm) || FILLER.has(t.norm) || LIGHT.has(t.norm) || POSTPOSITIONS.has(t.norm) || t.norm === ',')
      continue
    kept.push(t.norm)
  }
  return { text: kept.join(' '), all }
}

const VALUE_SPLIT_TO = new Set(['to', 'as', 'equals'])
const VALUE_SPLIT_WITH = new Set(['with'])
const VALUE_SPLIT_IN = new Set(['in', 'into', 'on', 'inside'])
const VALUE_FIRST = new Set(['type', 'write', 'enter', 'input', 'put', 'rayi', 'raayi', 'rayandi', 'vrayi', 'likho', 'daalo', 'dalo', 'రాయి', 'लिखो', 'डालो'])

/** "set note to hello" / "fill note with hello" / "type hello in note" / "note lo hello rayi" -> the field phrase and the value in the owner's own spelling. */
function splitFieldValue(rest: Tok[], verb: string): { field: string, value: string } {
  const norm = rest.map(t => t.norm)
  const fieldOf = (a: Tok[]) => phraseOf(a).text
  const find = (set: ReadonlySet<string>) => norm.findIndex(w => set.has(w))
  if (verb === 'fill' || verb === 'fill in') {
    const i = find(VALUE_SPLIT_WITH)
    if (i >= 0)
      return { field: fieldOf(rest.slice(0, i)), value: valueText(rest.slice(i + 1)) }
  }
  if (VALUE_FIRST.has(verb)) {
    const i = find(VALUE_SPLIT_IN)
    if (i >= 0)
      return { field: fieldOf(rest.slice(i + 1)), value: valueText(rest.slice(0, i)) }
  }
  const to = find(VALUE_SPLIT_TO)
  if (to >= 0)
    return { field: fieldOf(rest.slice(0, to)), value: valueText(rest.slice(to + 1)) }
  const post = norm.findIndex(w => POSTPOSITIONS.has(w))
  if (post > 0) {
    const value = rest.slice(post + 1).filter(t => !LIGHT.has(t.norm) && !['ga', 'గా', 'ke', 'के'].includes(t.norm))
    return { field: fieldOf(rest.slice(0, post)), value: valueText(value) }
  }
  return { field: '', value: valueText(rest) }
}

/** Parse an utterance into intents. Pure: it knows nothing about the form except through `resolveIntent`. */
export function parseUtterance(text: string): Parsed {
  const refusal = speechRefusal(text)
  const out: Parsed = { intents: [], withheld: false, mixedSubmit: false, refusal }
  if (refusal)
    return out
  const tokens = cleanTokens(tokenize(text))
  if (!tokens.length)
    return out
  // one command with a value ("set note to bread and milk") is not cut at its "and"
  const norms = tokens.map(t => t.norm)
  const hasSet = findPhrase(norms, SET_VERBS) !== null && findPhrase(norms, [...ON_VERBS, ...OFF_VERBS]) === null
  const reads = (hasSet ? [tokens] : splitSegments(tokens)).map(readSegment)
  // "wait, clean" and "not yet, clean it" are two segments but one thought
  const brake = tokens.some(t => NEGATIONS.has(t.norm) || POSTPONE.has(t.norm))
  // a verb carries over to the plain phrases around it ("tick temp files, browser cache and thumbnails"; "temp files, thumbnails tick chey")
  let carry: 'on' | 'off' | null = null
  for (const r of reads) {
    if (r.verb)
      carry = r.verb
    else if (!r.intent && !r.pressed && carry)
      r.verb = carry
  }
  const first = reads.findIndex(r => r.verb && !r.intent)
  for (let i = 0; i < first; i++) {
    if (!reads[i].verb && !reads[i].intent && !reads[i].pressed)
      reads[i].verb = reads[first].verb
  }
  let submits = 0
  for (const r of reads) {
    if (r.intent) {
      if (r.intent.type === 'submit') {
        submits++
        if (r.negated || r.postponed || brake)
          out.withheld = true
        else
          out.intents.push(r.intent)
      }
      else {
        out.intents.push(r.intent)
      }
      continue
    }
    const p = phraseOf(r.rest)
    if (r.verb === 'on' || r.verb === 'off')
      out.intents.push({ type: r.verb, phrase: p.text, all: p.all })
    else if (r.pressed)
      out.intents.push({ type: 'press', phrase: p.text })
    else if (p.text && p.text.split(' ').length <= 5)
      out.intents.push({ type: 'bare', phrase: p.text })
  }
  const submit = out.intents.find(i => i.type === 'submit')
  if (submit && out.intents.some(i => ['on', 'off', 'set', 'press', 'bare'].includes(i.type))) {
    out.mixedSubmit = true
    out.intents = out.intents.filter(i => i.type !== 'submit')
  }
  else if (submits > 1) {
    out.intents = out.intents.filter((i, idx, all) => i.type !== 'submit' || all.findIndex(x => x.type === 'submit') === idx)
  }
  return out
}

// ---- values ---------------------------------------------------------------------------------------------------------------------------

const NUM_WORDS: Record<string, number> = {
  zero: 0,
  one: 1,
  two: 2,
  three: 3,
  four: 4,
  five: 5,
  six: 6,
  seven: 7,
  eight: 8,
  nine: 9,
  ten: 10,
  eleven: 11,
  twelve: 12,
  thirteen: 13,
  fourteen: 14,
  fifteen: 15,
  sixteen: 16,
  seventeen: 17,
  eighteen: 18,
  nineteen: 19,
  twenty: 20,
  thirty: 30,
  forty: 40,
  fifty: 50,
  sixty: 60,
  seventy: 70,
  eighty: 80,
  ninety: 90,
}
const NUM_SCALE: Record<string, number> = { hundred: 100, thousand: 1000, lakh: 100_000, million: 1_000_000, crore: 10_000_000 }

/** "40", "3.5", "1,000", "forty", "twenty five", "three point five", "minus two", Telugu and Devanagari digits. null when there is no number. */
export function parseNumber(text: string): number | null {
  const t = normalize(text)
  const digits = /-?\d+(?:\.\d+)?/.exec(asciiDigits(String(text)).replace(/(\d),(\d{3})(?!\d)/g, '$1$2'))
  if (digits && !/[a-z]/.test(t.replace(/\b(?:percent|pc|points?|units?|gb|mb|kb)\b/g, '').replace(/\b[ap]m\b/g, '').trim()))
    return Number.parseFloat(digits[0])
  if (digits && /^\D*\d/.test(asciiDigits(String(text)).trim()))
    return Number.parseFloat(digits[0])
  const w = t.split(' ').filter(Boolean)
  let negative = false
  let total = 0
  let current = 0
  let seen = false
  let i = 0
  for (; i < w.length; i++) {
    const word = w[i]
    if (word === 'minus' || word === 'negative') {
      negative = true
      continue
    }
    if (word === 'point') {
      i++
      break
    }
    if (word in NUM_WORDS) {
      current += NUM_WORDS[word]
      seen = true
    }
    else if (word in NUM_SCALE) {
      current = (current || 1) * NUM_SCALE[word]
      if (NUM_SCALE[word] >= 1000) {
        total += current
        current = 0
      }
      seen = true
    }
    else if (word === 'and' || word === 'percent' || word === 'a') {
      continue
    }
    else if (seen) {
      return finishNumber(total + current, negative)
    }
  }
  if (!seen)
    return null
  let value = total + current
  const frac = w.slice(i).map(x => (x in NUM_WORDS && NUM_WORDS[x] < 10 ? String(NUM_WORDS[x]) : '')).join('')
  if (frac)
    value = Number.parseFloat(`${value}.${frac}`)
  return finishNumber(value, negative)
}

function finishNumber(n: number, negative: boolean): number {
  return negative ? -n : n
}

const pad = (n: number): string => String(n).padStart(2, '0')
const iso = (d: Date): string => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
const WEEKDAYS = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday']
const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december']
const TODAY_WORDS = new Set(['today', 'ivala', 'eroju', 'aaj', 'ఈరోజు', 'ఇవాళ', 'आज'])
const TOMORROW_WORDS = new Set(['tomorrow', 'repu', 'రేపు'])
const YESTERDAY_WORDS = new Set(['yesterday', 'ninna', 'నిन్న'])

/** A day as YYYY-MM-DD. "today", "tomorrow", "next monday", "in 3 days", "25 december", "december 25 2026", "2026-12-25". null when unclear (the face then asks). */
export function parseDay(text: string, today: Date): string | null {
  const raw = asciiDigits(String(text))
  const m = /(\d{4})-(\d{2})-(\d{2})/.exec(raw)
  if (m)
    return `${m[1]}-${m[2]}-${m[3]}`
  const w = normalize(text).split(' ').filter(Boolean)
  const base = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const shift = (days: number) => {
    const d = new Date(base)
    d.setDate(d.getDate() + days)
    return iso(d)
  }
  if (w.some(x => TODAY_WORDS.has(x)))
    return iso(base)
  if (w.includes('day') && w.includes('after') && w.includes('tomorrow'))
    return shift(2)
  if (w.some(x => TOMORROW_WORDS.has(x)))
    return shift(1)
  if (w.some(x => YESTERDAY_WORDS.has(x)))
    return shift(-1)
  const inN = /\bin (\w+) (day|days|week|weeks)\b/.exec(normalize(text))
  if (inN) {
    const n = parseNumber(inN[1])
    if (n !== null)
      return shift(n * (inN[2].startsWith('week') ? 7 : 1))
  }
  const wd = w.findIndex(x => WEEKDAYS.includes(x))
  if (wd >= 0) {
    const target = WEEKDAYS.indexOf(w[wd])
    let delta = (target - base.getDay() + 7) % 7
    if (delta === 0 && w.includes('next'))
      delta = 7
    else if (w.includes('next') && delta < 7 && w[wd - 1] === 'next' && delta !== 0 && target <= base.getDay())
      delta += 0
    return shift(delta)
  }
  const mi = w.findIndex(x => MONTHS.includes(x) || (x.length >= 3 && MONTHS.some(mo => mo.startsWith(x) && x.length >= 3)))
  if (mi >= 0) {
    const month = MONTHS.findIndex(mo => mo.startsWith(w[mi]))
    const nums = w.map((x, idx) => ({ idx, n: /^\d+/.test(x) ? Number.parseInt(x, 10) : Number.NaN })).filter(x => !Number.isNaN(x.n))
    const day = nums.find(x => x.n >= 1 && x.n <= 31)
    if (month >= 0 && day) {
      const yearTok = nums.find(x => x.n >= 1000 && x.n <= 9999)
      let year = yearTok ? yearTok.n : base.getFullYear()
      if (!yearTok && new Date(year, month, day.n) < base)
        year += 1
      return `${year}-${pad(month + 1)}-${pad(day.n)}`
    }
  }
  return null
}

/** A time as HH:MM. "5 pm", "5:30 pm", "17:30", "noon", "midnight", "half past five", "quarter to six", "five thirty pm". null when unclear. */
export function parseClock(text: string): string | null {
  const t = normalize(asciiDigits(String(text)).replace(/(\d)\.(\d{2})\b/g, '$1:$2').replace(/(\d):(\d{2})/g, '$1 $2'))
  const tt = t.replace(/\b[ap] m\b/g, m => m.replace(' ', ''))
  const pm = /\b(?:pm|evening|night|afternoon)\b/.test(tt)
  const am = /\b(?:am|morning)\b/.test(tt)
  if (/\bnoon\b/.test(tt))
    return '12:00'
  if (/\bmidnight\b/.test(tt))
    return '00:00'
  const fix = (h: number, mi: number): string | null => {
    if (h > 24 || mi > 59)
      return null
    let hour = h
    if (pm && hour < 12)
      hour += 12
    if (am && hour === 12)
      hour = 0
    return hour > 23 ? null : `${pad(hour)}:${pad(mi)}`
  }
  const half = /\bhalf past (\w+)\b/.exec(tt)
  if (half) {
    const h = parseNumber(half[1])
    return h === null ? null : fix(h, 30)
  }
  const quarterPast = /\bquarter past (\w+)\b/.exec(tt)
  if (quarterPast) {
    const h = parseNumber(quarterPast[1])
    return h === null ? null : fix(h, 15)
  }
  const quarterTo = /\bquarter to (\w+)\b/.exec(tt)
  if (quarterTo) {
    const h = parseNumber(quarterTo[1])
    return h === null ? null : fix(h - 1, 45)
  }
  const digits = /\b(\d{1,2})(?: (\d{2}))?\b/.exec(tt)
  if (digits)
    return fix(Number.parseInt(digits[1], 10), digits[2] ? Number.parseInt(digits[2], 10) : 0)
  const parts = tt.split(' ').filter(w => w in NUM_WORDS || w in NUM_SCALE)
  if (parts.length) {
    const h = parseNumber(parts[0])
    const rest = parts.slice(1).join(' ')
    const mi = rest ? parseNumber(rest) : 0
    return h === null || mi === null ? null : fix(h, mi)
  }
  return null
}

export function parseTimeOfDay(text: string): string | null {
  return parseClock(text)
}

export function parseDateTime(text: string, today: Date): string | null {
  const day = parseDay(text, today)
  const clock = parseClock(text.replace(/\d{4}-\d{2}-\d{2}/, ''))
  return day && clock ? `${day}T${clock}` : null
}

const ON_WORDS = new Set(['on', 'true', 'yes', 'enable', 'enabled', 'yeah', 'avunu', 'haan', 'ha', 'అవును', 'हाँ', 'हां'])
const OFF_WORDS = new Set(['off', 'false', 'no', 'disable', 'disabled', 'kadu', 'ledu', 'nahi', 'nahin', 'కాదు', 'లేదు', 'नहीं'])

export function parseSwitch(text: string): boolean | null {
  const w = normalize(text).split(' ')
  if (w.some(x => OFF_WORDS.has(x)))
    return false
  if (w.some(x => ON_WORDS.has(x)))
    return true
  return null
}

// ---- resolving an intent against the form ------------------------------------------------------------------------------------------------

export type FormAction
  = { type: 'check', fieldId: string, optionId: string, on: boolean, label: string }
    | { type: 'choose', fieldId: string, optionId: string, label: string, fieldLabel: string }
    | { type: 'toggle', fieldId: string, on: boolean, label: string }
    | { type: 'group', fieldId: string, on: boolean, ids: string[], label: string }
    | { type: 'set', fieldId: string, value: AskValue, label: string, shown: string }
    | { type: 'press', buttonId: string, role: 'submit' | 'cancel' | 'button', label: string }
    | { type: 'focus', direction: 'next' | 'previous' }
    | { type: 'read' }
    | { type: 'undo' }
    | { type: 'stop' }

export interface ResolveContext {
  targets: VoiceTarget[]
  nodes: Map<string, AskNode>
  values: Record<string, AskValue>
  today: Date
  focusedFieldId?: string | null
  /** The words reached us by translation: a submit is never taken from a translation. */
  viaTranslation?: boolean
}

export type Resolution
  = { kind: 'actions', actions: FormAction[], say: string, hit?: string }
    | { kind: 'unsure', intent: Intent, ranked: Scored[], phrase: string }
    | { kind: 'notfound', say: string }
    | { kind: 'need', say: string }
    | { kind: 'refuse', say: string }

export const NOT_FOUND = 'I did not find that field.'

export function nodeMap(fields: AskNode[]): Map<string, AskNode> {
  const map = new Map<string, AskNode>()
  for (const n of walk(fields)) {
    if (n.id)
      map.set(n.id, n)
  }
  return map
}

const GROUP_ALL_MAX = 40

function groupAll(ctx: ResolveContext, phrase: string, on: boolean): Resolution {
  const groups = ctx.targets.filter(t => t.kind === 'field' && t.nodeType === 'checkbox_group')
  const named = phrase ? judge(rank(phrase, groups)) : { kind: 'none' as const }
  let group: VoiceTarget | undefined
  if (named.kind === 'sure')
    group = named.best.target
  else if (!phrase && groups.length === 1)
    group = groups[0]
  else if (named.kind === 'unsure' || (!phrase && groups.length > 1))
    return { kind: 'unsure', intent: { type: on ? 'on' : 'off', phrase, all: true }, ranked: (named.kind === 'unsure' ? named.ranked : groups.map(target => ({ target, score: 0.5 }))), phrase }
  if (!group)
    return { kind: 'notfound', say: NOT_FOUND }
  const node = ctx.nodes.get(group.fieldId!)
  const ids = (node?.options ?? []).map(o => o.id)
  if (on && (/review/i.test(group.label) || ids.length > GROUP_ALL_MAX))
    return { kind: 'refuse', say: `I will not tick all of ${group.label} by voice. Say the items you want.` }
  return { kind: 'actions', actions: [{ type: 'group', fieldId: group.fieldId!, on, ids, label: group.label }], say: `${on ? 'Ticking all of' : 'Unticking all of'} ${group.label}`, hit: group.key }
}

/** The actions for an intent and ONE chosen target. Used directly when the match is sure, and after the owner (or the decision engine) picked one. */
export function actionsFor(intent: Intent, target: VoiceTarget, ctx: ResolveContext): Resolution {
  if (intent.type === 'on' || intent.type === 'off' || intent.type === 'bare') {
    const on = intent.type !== 'off'
    if (target.kind === 'option') {
      const node = ctx.nodes.get(target.fieldId!)
      if (target.multi)
        return { kind: 'actions', actions: [{ type: 'check', fieldId: target.fieldId!, optionId: target.optionId!, on, label: target.label }], say: `${on ? 'Ticking' : 'Unticking'} ${target.label}`, hit: target.key }
      if (!on)
        return { kind: 'refuse', say: `${target.label} is a choice. Choose another one instead.` }
      void node
      return { kind: 'actions', actions: [{ type: 'choose', fieldId: target.fieldId!, optionId: target.optionId!, label: target.label, fieldLabel: target.fieldLabel ?? '' }], say: `Choosing ${target.label}`, hit: target.key }
    }
    if (target.kind === 'field' && target.nodeType === 'toggle')
      return { kind: 'actions', actions: [{ type: 'toggle', fieldId: target.fieldId!, on, label: target.label }], say: `Turning ${target.label} ${on ? 'on' : 'off'}`, hit: target.key }
    if (target.kind === 'field' && target.nodeType === 'checkbox_group')
      return groupAll(ctx, target.label, on)
    return { kind: 'refuse', say: `${target.label} cannot be ticked. Say what it should be set to.` }
  }
  if (intent.type === 'set')
    return setField(intent, target, ctx)
  if (intent.type === 'press' || intent.type === 'submit') {
    const b = target.button
    if (!b)
      return { kind: 'notfound', say: 'I did not find that button.' }
    return buttonAction(b, intent.type === 'submit' ? 'submit' : 'button', ctx)
  }
  return { kind: 'notfound', say: NOT_FOUND }
}

function buttonAction(b: AskButton, role: 'submit' | 'cancel' | 'button', ctx: ResolveContext): Resolution {
  void ctx
  if (buttonBlockedForVoice(b))
    return { kind: 'refuse', say: `${b.label} can only be pressed with a click, and approvals are given in the JARVIS console.` }
  return { kind: 'actions', actions: [{ type: 'press', buttonId: b.id, role, label: b.label }], say: role === 'submit' ? `Pressing ${b.label}` : role === 'cancel' ? 'Cancelling' : `Pressing ${b.label}`, hit: keyButton(b.id) }
}

function setField(intent: Extract<Intent, { type: 'set' }>, target: VoiceTarget, ctx: ResolveContext): Resolution {
  if (target.kind !== 'field')
    return { kind: 'notfound', say: NOT_FOUND }
  const node = ctx.nodes.get(target.fieldId!)
  if (!node)
    return { kind: 'notfound', say: NOT_FOUND }
  if (isSecretLabel(node.label ?? ''))
    return { kind: 'refuse', say: CONSOLE_ONLY }
  const value = intent.value.trim()
  const label = target.label
  const needs = (what: string): Resolution => ({ kind: 'need', say: `What should ${label} be? I did not get ${what}.` })
  if (!value && node.type !== 'toggle')
    return { kind: 'need', say: `What should ${label} be?` }
  switch (node.type) {
    case 'text': {
      const max = Number(node.max_len) || 200
      return { kind: 'actions', actions: [{ type: 'set', fieldId: node.id!, value: value.slice(0, max), label, shown: value.slice(0, 60) }], say: `Filling ${label}`, hit: target.key }
    }
    case 'number':
    case 'slider': {
      const n = parseNumber(value)
      if (n === null)
        return needs('a number')
      const lo = Number(node.min)
      const hi = Number(node.max)
      const clamped = Math.min(Number.isFinite(hi) ? hi : n, Math.max(Number.isFinite(lo) ? lo : n, n))
      return { kind: 'actions', actions: [{ type: 'set', fieldId: node.id!, value: clamped, label, shown: String(clamped) }], say: `Setting ${label} to ${clamped}`, hit: target.key }
    }
    case 'toggle': {
      const on = value ? parseSwitch(value) : true
      if (on === null)
        return needs('on or off')
      return { kind: 'actions', actions: [{ type: 'toggle', fieldId: node.id!, on, label }], say: `Turning ${label} ${on ? 'on' : 'off'}`, hit: target.key }
    }
    case 'date': {
      const mode = String(node.mode ?? 'date')
      const v = mode === 'time' ? parseClock(value) : mode === 'datetime' ? parseDateTime(value, ctx.today) : parseDay(value, ctx.today)
      if (!v)
        return needs(mode === 'time' ? 'a time' : mode === 'datetime' ? 'a day and time' : 'a day')
      return { kind: 'actions', actions: [{ type: 'set', fieldId: node.id!, value: v, label, shown: v.replace('T', ' ') }], say: `Setting ${label} to ${v.replace('T', ' ')}`, hit: target.key }
    }
    case 'select':
    case 'radio':
    case 'cards':
    case 'split':
    case 'gallery':
    case 'file_choice': {
      const options = ctx.targets.filter(t => t.kind === 'option' && t.fieldId === node.id)
      const m = judge(rank(value, options))
      if (m.kind === 'sure')
        return actionsFor({ type: 'on', phrase: value, all: false }, m.best.target, ctx)
      if (m.kind === 'unsure')
        return { kind: 'unsure', intent: { type: 'on', phrase: value, all: false }, ranked: m.ranked, phrase: value }
      return { kind: 'notfound', say: `${label} has no choice like "${value.slice(0, 40)}".` }
    }
    default:
      return { kind: 'refuse', say: `${label} cannot be set by voice. Use the keyboard or the mouse.` }
  }
}

function resolveSubmit(intent: Extract<Intent, { type: 'submit' }>, ctx: ResolveContext): Resolution {
  if (ctx.viaTranslation)
    return { kind: 'refuse', say: 'I did not take that as a submit. Say submit, clean or ok, or press Enter.' }
  const buttons = ctx.targets.filter(t => t.kind === 'button')
  const primary = buttons.find(t => t.button?.style === 'primary')
  const word = intent.word.replace(/^(?:ok|okay) done$/, 'done')
  const named = judge(rank(word, buttons, 0))
  if (named.kind === 'sure')
    return buttonAction(named.best.target.button!, 'submit', ctx)
  if (intent.generic && primary)
    return buttonAction(primary.button!, 'submit', ctx)
  if (named.kind === 'unsure')
    return { kind: 'unsure', intent: { type: 'press', phrase: word }, ranked: named.ranked, phrase: word }
  return { kind: 'notfound', say: `There is no ${intent.word} button on this form.` }
}

function resolveCancel(ctx: ResolveContext): Resolution {
  const buttons = ctx.targets.filter(t => t.kind === 'button')
  const cancel = buttons.find(t => ['cancel', 'close', 'no'].includes(t.button!.id))
  if (cancel)
    return { kind: 'actions', actions: [{ type: 'press', buttonId: cancel.button!.id, role: 'cancel', label: cancel.label }], say: 'Cancelling', hit: cancel.key }
  return { kind: 'actions', actions: [{ type: 'press', buttonId: '', role: 'cancel', label: 'Close' }], say: 'Closing the form' }
}

/** One intent -> actions, a question, or a sentence. `viaTranslation` forbids a submit. */
export function resolveIntent(intent: Intent, ctx: ResolveContext): Resolution {
  switch (intent.type) {
    case 'submit': return resolveSubmit(intent, ctx)
    case 'cancel': return resolveCancel(ctx)
    case 'next': return { kind: 'actions', actions: [{ type: 'focus', direction: 'next' }], say: 'Next' }
    case 'previous': return { kind: 'actions', actions: [{ type: 'focus', direction: 'previous' }], say: 'Back' }
    case 'read': return { kind: 'actions', actions: [{ type: 'read' }], say: 'Reading the form' }
    case 'undo': return { kind: 'actions', actions: [{ type: 'undo' }], say: 'Undoing that change' }
    case 'stop': return { kind: 'actions', actions: [{ type: 'stop' }], say: 'Stopped listening' }
    case 'press': {
      const buttons = ctx.targets.filter(t => t.kind === 'button')
      const m = judge(rank(intent.phrase, buttons, 0))
      if (m.kind === 'sure')
        return buttonAction(m.best.target.button!, m.best.target.button!.style === 'primary' ? 'submit' : 'button', ctx)
      if (m.kind === 'unsure')
        return { kind: 'unsure', intent, ranked: m.ranked, phrase: intent.phrase }
      return { kind: 'notfound', say: 'I did not find that button.' }
    }
    case 'on':
    case 'off': {
      if (intent.all || !intent.phrase)
        return intent.phrase || intent.all ? groupAll(ctx, intent.phrase, intent.type === 'on') : { kind: 'need', say: `What should I ${intent.type === 'on' ? 'tick' : 'untick'}?` }
      const candidates = ctx.targets.filter(t => t.kind === 'option' || (t.kind === 'field' && (t.nodeType === 'toggle' || t.nodeType === 'checkbox_group')))
      const m = judge(rank(intent.phrase, candidates))
      if (m.kind === 'sure')
        return actionsFor(intent, m.best.target, ctx)
      if (m.kind === 'unsure')
        return { kind: 'unsure', intent, ranked: m.ranked, phrase: intent.phrase }
      return { kind: 'notfound', say: NOT_FOUND }
    }
    case 'set': {
      const fields = ctx.targets.filter(t => t.kind === 'field')
      let phrase = intent.fieldPhrase
      let candidate: VoiceTarget | undefined
      if (!phrase && intent.value) { // "set quantity": no splitter, the words are the field
        const named = judge(rank(intent.value, fields))
        if (named.kind === 'sure')
          return setField({ ...intent, value: '' }, named.best.target, ctx)
      }
      if (!phrase) {
        const focused = ctx.focusedFieldId ? fields.find(f => f.fieldId === ctx.focusedFieldId) : undefined
        if (!focused)
          return { kind: 'notfound', say: NOT_FOUND }
        candidate = focused
        phrase = focused.label
      }
      if (!candidate) {
        const m = judge(rank(phrase, fields))
        if (m.kind === 'sure')
          candidate = m.best.target
        else if (m.kind === 'unsure')
          return { kind: 'unsure', intent, ranked: m.ranked, phrase }
        else
          return { kind: 'notfound', say: NOT_FOUND }
      }
      return setField(intent, candidate, ctx)
    }
    case 'bare': {
      const singles = ctx.targets.filter(t => t.kind === 'option' && !t.multi)
      const m = judge(rank(intent.phrase, singles))
      if (m.kind === 'sure' && m.best.score >= 0.9)
        return actionsFor(intent, m.best.target, ctx)
      return { kind: 'notfound', say: NOT_FOUND }
    }
  }
}

/** True when the grammar found a form verb or a clear command, so the words are for the form and not for the chat. */
export function isFormCommand(parsed: Parsed): boolean {
  return parsed.refusal !== null || parsed.withheld || parsed.mixedSubmit || parsed.intents.some(i => i.type !== 'bare')
}

// ---- answering "Did you mean A or B?" ---------------------------------------------------------------------------------------------------

const ORDINALS: Array<[string[], number]> = [[['first', '1', 'one', 'a', 'pehla', 'modati', 'okati'], 0], [['second', '2', 'two', 'b', 'doosra', 'rendu', 'rendo'], 1], [['third', '3', 'three', 'c', 'teesra', 'moodu'], 2]]

export function answerChoice(text: string, options: Array<{ key: string, label: string }>): string | 'dismiss' | null {
  const drop = new Set(['the', 'option', 'number', 'please', 'jarvis', 'i', 'mean', 'meant', 'want', 'it', 'is', 'choose', 'pick', 'select', 'tick'])
  let w = normalize(text).split(' ').filter(x => x && !drop.has(x))
  if (w.length > 1)
    w = w.filter(x => x !== 'one')
  const joined = w.join(' ')
  if (equalsPhrase(w, L('neither', 'none', 'cancel', 'never mind', 'nevermind', 'no', 'stop', 'forget', 'forget it', 'raddu', 'vaddu')))
    return 'dismiss'
  for (const [names, index] of ORDINALS) {
    if ((names.includes(joined) || (w.length === 1 && names.includes(w[0]))) && options[index])
      return options[index].key
  }
  const m = judge(rank(text, options.map(o => ({ key: o.key, kind: 'option' as const, label: o.label }))))
  return m.kind === 'sure' ? m.best.target.key : null
}

// ---- dictation ------------------------------------------------------------------------------------------------------------------------

export type DictOp = { type: 'text', text: string } | { type: 'newline', paragraph: boolean } | { type: 'clear' } | { type: 'delete' } | { type: 'next' } | { type: 'previous' } | { type: 'stop' }

const DELETE_THAT = L('delete that', 'delete last', 'scratch that', 'undo that', 'clear that', 'delete it', 'remove that')
const CLEAR_ALL = L('clear', 'clear all', 'clear field', 'clear the field', 'clear everything', 'clear it', 'clear this', 'clear chey', 'clear karo', 'saaf karo')
const DICT_NEXT = L('next field', 'next', 'next one', 'tarvata field', 'agla field')
const DICT_PREV = L('previous field', 'previous', 'go back', 'back', 'last field', 'pichla field')
const DICT_STOP = L('stop', 'stop dictation', 'stop dictating', 'stop listening', 'done dictating', 'aapu', 'bas', 'ruko')
const NEW_LINE = L('new line', 'newline', 'next line')
const NEW_PARA = L('new paragraph')
const PUNCT: Array<[string[], string, boolean]> = [
  [['full', 'stop'], '.', false],
  [['period'], '.', true],
  [['comma'], ',', false],
  [['question', 'mark'], '?', false],
  [['exclamation', 'mark'], '!', false],
  [['exclamation', 'point'], '!', false],
  [['semicolon'], ';', false],
  [['colon'], ':', false],
]

/** What a piece of dictated speech means: text to insert, or an editing command. Commands are recognised only as the WHOLE utterance (or a trailing "next field"), so "I will clear the table" is text. */
export function dictationOps(finalText: string, options: { punctuation: boolean, multiline: boolean }): DictOp[] {
  const text = String(finalText ?? '').trim()
  if (!text)
    return []
  const whole = normalize(text).split(' ').filter(w => w && !['please', 'jarvis', 'kindly', 'now'].includes(w))
  if (equalsPhrase(whole, DELETE_THAT))
    return [{ type: 'delete' }]
  if (equalsPhrase(whole, CLEAR_ALL))
    return [{ type: 'clear' }]
  if (equalsPhrase(whole, DICT_NEXT))
    return [{ type: 'next' }]
  if (equalsPhrase(whole, DICT_PREV))
    return [{ type: 'previous' }]
  if (equalsPhrase(whole, DICT_STOP))
    return [{ type: 'stop' }]
  const ops: DictOp[] = []
  let tokens = tokenize(text).filter(t => t.norm !== ',' || true)
  // a trailing "next field" / "previous field" after text
  let trailing: DictOp | null = null
  const normAll = tokens.map(t => t.norm)
  for (const [phrase, op] of [[['next', 'field'], { type: 'next' }], [['previous', 'field'], { type: 'previous' }]] as Array<[string[], DictOp]>) {
    const n = phrase.length
    if (normAll.length > n && phrase.every((w, i) => normAll[normAll.length - n + i] === w)) {
      trailing = op
      tokens = tokens.slice(0, -n)
    }
  }
  let current = ''
  const flush = () => {
    const t = current.replace(/\s+([,.;:?!])/g, '$1').trim()
    if (t)
      ops.push({ type: 'text', text: t })
    current = ''
  }
  const norm = tokens.map(t => t.norm)
  for (let i = 0; i < tokens.length;) {
    const nl = findPhrase(norm.slice(i), NEW_LINE)
    const np = findPhrase(norm.slice(i), NEW_PARA)
    if (np && np.at === 0) {
      flush()
      ops.push({ type: 'newline', paragraph: true })
      i += np.len
      continue
    }
    if (nl && nl.at === 0) {
      flush()
      ops.push({ type: 'newline', paragraph: false })
      i += nl.len
      continue
    }
    if (options.punctuation) {
      const hit = PUNCT.find(([words, , trailingOnly]) => words.every((w, k) => norm[i + k] === w) && (!trailingOnly || i + words.length === norm.length) && i > 0)
      if (hit) {
        current = `${current.trimEnd()}${hit[1]} `
        i += hit[0].length
        continue
      }
    }
    current += `${tokens[i].raw} `
    i++
  }
  flush()
  if (trailing)
    ops.push(trailing)
  return ops
}

/** Add dictated text after what is in the field: one space between words, none before punctuation, a new line as it is. */
export function joinDictation(base: string, chunk: string): string {
  if (!chunk)
    return base
  if (chunk.startsWith('\n'))
    return `${base.replace(/[ \t]+$/, '')}${chunk}`
  if (!base || base.endsWith('\n'))
    return `${base}${chunk}`
  if (/^[,.;:?!]/.test(chunk))
    return `${base.trimEnd()}${chunk}`
  return `${base.trimEnd()} ${chunk}`
}

export function newlineText(op: { paragraph: boolean }, multiline: boolean): string {
  return multiline ? (op.paragraph ? '\n\n' : '\n') : ' '
}

/** The script of a text: Telugu, Hindi or other. */
export function scriptLanguage(text: string): 'te' | 'hi' | 'en' {
  let te = 0
  let hi = 0
  let other = 0
  for (const ch of String(text)) {
    const c = ch.codePointAt(0) ?? 0
    if (c >= 0x0C00 && c <= 0x0C7F)
      te++
    else if (c >= 0x0900 && c <= 0x097F)
      hi++
    else if (/\p{L}/u.test(ch))
      other++
  }
  if (Math.max(te, hi) === 0 || Math.max(te, hi) < other / 4)
    return 'en'
  return hi >= te ? 'hi' : 'te'
}

// ---- echo of our own speech ------------------------------------------------------------------------------------------------------------

/**
 * JARVIS speaks what it understood and, on request, the form. A microphone that hears that speech must not obey it, in particular a label that
 * says "say submit now". While JARVIS speaks, and for a moment after, anything that is (nearly) made of the words just spoken is dropped.
 */
export function createEchoGuard(tailMs = 2_000, now: () => number = () => Date.now(), maxSpeakingMs = 60_000) {
  let words = new Set<string>()
  let startedAt = 0
  let endedAt: number | null = null
  return {
    /** JARVIS starts speaking `text`. Call `finished()` when the playback has ended. */
    said(text: string) {
      const t = now()
      words = t - startedAt <= maxSpeakingMs && endedAt === null ? new Set([...words, ...normalize(text).split(' ').filter(Boolean)]) : new Set(normalize(text).split(' ').filter(Boolean))
      startedAt = t
      endedAt = null
    },
    finished() {
      endedAt = now()
    },
    isEcho(heard: string): boolean {
      const spoken = words.size > 0 && (endedAt === null ? now() - startedAt <= maxSpeakingMs : now() - endedAt <= tailMs)
      if (!spoken)
        return false
      const w = normalize(heard).split(' ').filter(Boolean)
      return w.length > 0 && w.filter(x => words.has(x)).length / w.length >= 0.8
    },
    reset() {
      words = new Set()
      endedAt = now()
    },
  }
}

// ---- read aloud -----------------------------------------------------------------------------------------------------------------------

export interface SpeechPlans { [lang: string]: { speak: boolean, note: string } | undefined }

export interface ReadPlan { spoken: string, shown: string, notes: string[] }

const LANG_NAME: Record<string, string> = { te: 'Telugu', hi: 'Hindi' }
const MAX_READ_FIELDS = 12

function clip(text: string, n: number): string {
  const t = text.replace(/\s+/g, ' ').trim()
  return t.length > n ? `${t.slice(0, n - 1)}...` : t
}

/**
 * What "read the form" says: the heading, the one-line intro, then each label with its current value, options summarised with counts, then the
 * button names. Short on purpose. Telugu or Hindi text is spoken only when its voice is verified (`plans`, from language.tts_verified);
 * otherwise the text is shown, English is spoken, and the notes say which.
 */
export function readAloudPlan(form: { title: string, intro: string, fields: AskNode[], buttons: AskButton[] }, values: Record<string, AskValue>, plans: SpeechPlans | null): ReadPlan {
  const notes: string[] = []
  const shownLines: string[] = []
  const spokenLines: string[] = []
  const unspoken = new Set<string>()

  function line(label: string, value: string, lang: 'te' | 'hi' | 'en' = scriptLanguage(`${label} ${value}`)): void {
    const shown = value ? `${label}: ${value}` : label
    shownLines.push(shown)
    if (lang === 'en') {
      spokenLines.push(shown)
      return
    }
    const plan = plans?.[lang]
    if (plan?.speak) {
      spokenLines.push(shown)
      return
    }
    unspoken.add(lang)
    if (plan?.note && !notes.includes(plan.note))
      notes.push(plan.note)
    spokenLines.push(`${scriptLanguage(label) === 'en' ? label : `text in ${LANG_NAME[lang]}`}${value ? `: ${scriptLanguage(value) === 'en' ? value : `text in ${LANG_NAME[lang]}`}` : ''}`)
  }

  line(form.title || 'JARVIS asks', '', scriptLanguage(form.title))
  if (form.intro)
    line(clip(form.intro, 160), '', scriptLanguage(form.intro))
  let count = 0
  let extra = 0
  for (const n of walk(form.fields)) {
    if (!n.id || !INPUT_TYPES.has(n.type) || isSecretLabel(n.label ?? ''))
      continue
    if (count >= MAX_READ_FIELDS) {
      extra++
      continue
    }
    count++
    const label = n.label || n.id
    const v = values[n.id]
    const options = (n.options ?? []) as AskOption[]
    if (n.type === 'checkbox_group') {
      const picked = new Set(Array.isArray(v) ? v as string[] : [])
      const names = options.filter(o => picked.has(o.id)).map(o => o.label)
      const some = names.length === 0 ? 'none ticked' : names.length <= 3 ? `${names.length} ticked: ${names.join(', ')}` : `${names.length} ticked: ${names.slice(0, 2).join(', ')} and ${names.length - 2} more`
      line(label, `${options.length} options, ${some}`)
    }
    else if (n.type === 'toggle') {
      line(label, v === true ? 'on' : 'off')
    }
    else if (n.type === 'text') {
      line(label, v ? clip(String(v), 60) : 'empty')
    }
    else if (n.type === 'number' || n.type === 'slider') {
      const val = v ?? n.value
      line(label, val === null || val === undefined || val === '' ? 'empty' : `${val}${n.unit ? ` ${String(n.unit)}` : ''}`)
    }
    else if (n.type === 'date') {
      line(label, v ? String(v).replace('T', ' ') : 'not set')
    }
    else if (n.type === 'table') {
      const rows = (n.rows as Array<{ id: string }> | undefined) ?? []
      const chosen = Array.isArray(v) ? v.length : v ? 1 : 0
      line(label, `${rows.length} rows, ${chosen} chosen`)
    }
    else {
      const picked = options.find(o => o.id === v)
      line(label, picked ? picked.label : `nothing chosen, ${options.length} options`)
    }
  }
  if (extra)
    line(`and ${extra} more fields`, '', 'en')
  const names = form.buttons.map(b => b.label)
  if (names.length)
    line(`Buttons: ${names.join(', ')}`, '', 'en')
  for (const lang of unspoken) {
    const text = `${LANG_NAME[lang]} text is shown, not spoken: its voice is not verified yet.`
    if (!notes.some(n => n.includes(LANG_NAME[lang])))
      notes.push(text)
  }
  return { spoken: spokenLines.join('. ').replace(/\.\./g, '.'), shown: shownLines.join('\n'), notes }
}

// ---- which fields can be dictated ----------------------------------------------------------------------------------------------------

export function dictatable(node: AskNode): boolean {
  return !!node.id && (node.type === 'text' || node.type === 'number' || node.type === 'date') && !isSecretLabel(`${node.label ?? ''} ${String(node.placeholder ?? '')}`)
}

export function dictatableFields(fields: AskNode[]): AskNode[] {
  return [...walk(fields)].filter(dictatable)
}
