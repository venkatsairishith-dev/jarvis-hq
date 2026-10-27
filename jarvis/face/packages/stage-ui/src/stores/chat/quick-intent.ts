/**
 * JARVIS D60 (speed): what the face can know about the owner's message without asking a model.
 *
 * 1. `guessTask`: is the message a PC, screen, document or weather task? A cheap keyword rule (no model, microseconds), used only to show "On it" and
 *    a progress line at once. A wrong guess costs one wrong word on the HUD for a few seconds; it changes nothing that runs.
 * 2. `parseQuickResult`: reads the answer of the tool server's `desktop__quick_command` (the rules-first router in jarvis/plugins/desktop/quick.py).
 *    That router, not this file, decides whether a message is one plain command; the face only asks it, before it asks any chat model.
 *
 * The words are English, like `lean-tools.ts`; a Hindi or Telugu message gets no instant line (the Tenglish verb lexicon plugs into the router on the
 * Python side, `quick.register_normaliser`, once the translate module exists).
 */

export type TaskKind = 'pc' | 'screen' | 'doc' | 'weather'

export interface TaskGuess {
  kind: TaskKind
  /** The short line the HUD shows at once. */
  line: string
}

const FILLER = /^(?:(?:hey|ok|okay|hi)\s+jarvis[,\s]+|jarvis[,\s]+|please\s+|can you\s+|could you\s+|would you\s+|will you\s+|go ahead and\s+|just\s+)+/i

const WEATHER = /\b(?:weather|temperature|forecast|humidity)\b|\b(?:is it|will it)\s+(?:rain|snow|hot|cold|sunny)/i
const SCREEN = /\b(?:on|at)\s+(?:my|the)\s+(?:screen|display|monitor)\b|\bwhich\s+(?:window|app|program)s?\b|\bwhat(?:'s|\s+is|\s+are)\s[^.?!]{1,30}\s(?:windows?|apps?|programs?)\s[^.?!]{1,20}\s(?:open|running|in\s+front)\b|\bwhat\s+am\s+i\s+(?:looking|working)\s+(?:at|on)\b/i
const DOC = /\b(?:pdf|docx?|invoice|receipt|scanned?|spreadsheet|xlsx?|powerpoint|pptx?)\b|\b(?:make|create|build|prepare|generate)\s[^.?!]{1,30}\s(?:deck|slides?|presentation|report)\b/i
// A verb that starts a command to the PC. "write", "play" and "start" also start ordinary chat ("write a poem", "start by explaining"), so they need an object.
const PC_VERB = /^(?:open|launch|run|close|quit|exit|minimi[sz]e|maximi[sz]e|restore|focus|switch\s+to|click|press|scroll|pause|resume|unpause|mute|unmute|volume|take\s+a\s+screenshot|screenshot|skip|next\s+(?:song|track)|previous\s+(?:song|track)|go\s+to|visit|save)\b/i
const PC_OBJECT = /^(?:play|type|write|enter|start|turn\s+(?:up|down)|put\s+on)\s+.+/i
const PC_OBJECT_NEEDS = /\b(?:in|into|on|using)\s+(?:the\s+|my\s+)?(?:notepad|wordpad|word|excel|chrome|edge|youtube|spotify|calculator|paint|terminal|code|vs\s*code|explorer)\b|^(?:play|put\s+on)\s+\S+|^turn\s+(?:up|down)\b|^start\s+(?:the\s+)?(?:notepad|calculator|paint|chrome|spotify|explorer)\b/i

/** The guess for a message, or `null` for an ordinary chat message. */
export function guessTask(text: string | undefined | null): TaskGuess | null {
  const t = String(text ?? '').replace(/\s+/g, ' ').trim()
  if (!t || t.length > 400)
    return null
  const body = t.replace(FILLER, '')
  if (WEATHER.test(body))
    return { kind: 'weather', line: 'Checking the weather' }
  if (SCREEN.test(body))
    return { kind: 'screen', line: 'Looking at your screen' }
  if (PC_VERB.test(body) || (PC_OBJECT.test(body) && PC_OBJECT_NEEDS.test(body)))
    return { kind: 'pc', line: 'On it' }
  if (DOC.test(body))
    return { kind: 'doc', line: 'On it' }
  return null
}

const CLOCK = /^(?:what(?:'s|\s+is)\s+(?:the\s+)?(?:current\s+|today'?s\s+)?(?:time|date)|what\s+time\s+is\s+it|what\s+day\s+is\s+it|tell\s+me\s+the\s+(?:time|date))\b/i

/**
 * D65: the owner talks to JARVIS about the PC task that is under way ("what are you doing", "why did you stop", "what went wrong"), steers it ("try another way", "skip this
 * step", "stop") or asks what it has learned. WHOLE phrases only. The Python hook (jarvis/plugins/desktop/taskvoice.py, behind the same hidden quick_command tool) decides:
 * it answers `handled: false` at once when no task is live, and the message then goes to the chat model as any other. Only the owner's own session reaches this code.
 */
const TASK_TALK = /^(?:what(?:'s| is| are)? you(?:'re| are)? doing(?: right now| now)?|what are you up to|what are you working on|why did you stop(?:ped)?|why are you waiting|what went wrong|what(?:'s| is) wrong|what happened|what(?:'s| is) the problem|try (?:it )?(?:another|a different) way|try something else|skip (?:this|that|the) step|stop|stop (?:it|that|now|the task)|what have you learn(?:ed|t)(?: about [\w. -]{1,40})?|what did you learn(?: about [\w. -]{1,40})?|forget (?:that|it|this|everything about [\w. -]{1,40}))[\s.!?]*$/i

/**
 * Whether the rules-first router is worth asking: the message starts like a PC command, or asks the clock. Everything else goes straight to the chat model,
 * so an ordinary chat turn does not wait for one more local call.
 */
export function mightBeQuickCommand(text: string, guess: TaskGuess | null = guessTask(text)): boolean {
  if (guess?.kind === 'pc')
    return true
  const body = String(text ?? '').replace(/\s+/g, ' ').trim().replace(FILLER, '')
  return CLOCK.test(body) || TASK_TALK.test(body)
}

export interface QuickResult {
  handled: boolean
  /** The reply to show and speak. */
  reply?: string
  status?: string
  /** The short line for the acknowledgement. */
  label?: string
}

interface ToolResultLike {
  structuredContent?: unknown
  content?: Array<Record<string, unknown>>
  isError?: boolean
}

/**
 * The quick-command tool's answer from an MCP result (structured part, or the JSON text of the first content item). Anything that is not clearly a
 * handled answer with a reply reads as `{ handled: false }`, so the message goes to the chat model as usual.
 */
export function parseQuickResult(result: unknown): QuickResult {
  const r = result as ToolResultLike | null | undefined
  if (!r || typeof r !== 'object' || r.isError)
    return { handled: false }
  let data: unknown = r.structuredContent
  if (!data) {
    const text = r.content?.find(item => item?.type === 'text')?.text
    if (typeof text !== 'string')
      return { handled: false }
    try {
      data = JSON.parse(text)
    }
    catch {
      return { handled: false }
    }
  }
  if (!data || typeof data !== 'object' || Array.isArray(data))
    return { handled: false }
  // The tool server wraps a plugin entry's answer as {ok, result: {...}} (seen live 2026-10-26: handled was never read, so every plain command went to a model).
  const outer = data as Record<string, unknown>
  const d = (outer.result && typeof outer.result === 'object' && !Array.isArray(outer.result) ? outer.result : outer) as Record<string, unknown>
  const reply = typeof d.reply === 'string' ? d.reply.replace(/\s+/g, ' ').trim().slice(0, 600) : ''
  if (d.handled !== true || !reply)
    return { handled: false }
  return {
    handled: true,
    reply,
    status: typeof d.status === 'string' ? d.status.slice(0, 40) : undefined,
    label: typeof d.label === 'string' ? d.label.replace(/\s+/g, ' ').trim().slice(0, 60) : undefined,
  }
}
