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
 * D74 (ask, do not guess): the tool server's quick_command hook also runs the ambiguity rules (jarvis/plugins/desktop/gate.py over config/ambiguity.json) BEFORE any
 * chat model sees the message: "delete that", "send it to him", "make a pdf", "Notepad leda Wordpad teruvu". So a message that starts with a verb that acts on something (or
 * holds a Telugu or Hindi verb in English letters) is sent to the hook too. The hook answers `handled: false` at once when there is nothing to ask and nothing to run, so a
 * wrong guess here costs one fast local call. The rules themselves are in Python, in one place; this is only the door.
 */
const ASK_VERB = /^(?:delete|remove|erase|trash|send|e-?mail|forward|share|move|copy|rename|make|create|generate|search|find|look\s+up|print|upload|close|quit|exit|remind|schedule|whatsapp)\b/i
const INDIC_VERB = /\b(?:kholo|khol\s+do|band\s+karo|hatao|hata\s+do|bhejo|bhej\s+do|banao|bajao|chalao|dikhao|dhundo|dhoondo|teruvu|teruvandi|muyyi|teesey|teeseyyi|pampu|pampandi|cheyyi|cheyandi|chupinchu|vetuku|mail\s+karo|(?:delete|save|open|close|rename|move|copy|send|play|share|search|create|make)\s+(?:karo|kar\s+do|cheyyi|cheyandi|chey))\b/i

/**
 * D75 (the owner: "it should do complex tasks ... can't we just do that by talking to JARVIS?"): a plain question the Python hook answers by itself, at once, with no app and no
 * model (jarvis/plugins/_shared/direct_answer.py): arithmetic ("23 times 47"), percentages, units, the date and the clock, tip / GST / EMI. The face only has to ASK the hook, so
 * a message that holds a number (or a number word) next to a maths or unit word, or that starts like a date question, is sent to it. The hook answers `handled: false` at once when it
 * is not maths ("call 555-1234", "I have 3 apples"), so a wrong guess here costs one fast local call. The rules themselves are in Python, in one place.
 */
const NUMBERISH = /\d|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakhs?|crores?|million|half|quarter|double|twice|triple|thrice)\b/i
const MATH_WORD = /[+*/^×÷%=!]|\d\s*-\s*\d|\b(?:plus|minus|times|multipl\w+|divid\w+|over|into|per\s?cent|power|squared|cubed|roots?|factorial|mod|average|sum|total|emi|gst|vat|tip|discount|interest|split|convert|how\s+many|how\s+much|lakhs?|crores?|guna+|bhaa?g|jodo?|taggindi|tagginchu|ghatao|kalupu|days?|weeks?|months?|years?|hours?|minutes?|celsius|fahrenheit|km|kg|miles?|inch(?:es)?|feet|foot|pounds?|lit(?:re|er)s?|[kmgt]b|mph|kmph)\b/i
const DATE_Q = /^(?:what(?:'s|\s+is)?\s+(?:the\s+)?(?:date|day|month|year)\b|which\s+day\b|today'?s\s+date|(?:tomorrow|yesterday)'?s\s+date|(?:the\s+)?day\s+(?:after|before)\s+(?:tomorrow|yesterday)|days?\s+(?:until|till|since|between|left)|weeks?\s+(?:until|till)|how\s+many\s+days|is\s+\d{4}\s+a\s+leap\s+year)/i

/**
 * Whether the rules-first router is worth asking: the message starts like a PC command, starts with a verb the ambiguity rules must see (D74), or asks the clock. Everything
 * else goes straight to the chat model, so an ordinary chat turn does not wait for one more local call.
 */
export function mightBeQuickCommand(text: string, guess: TaskGuess | null = guessTask(text)): boolean {
  if (guess?.kind === 'pc')
    return true
  const body = String(text ?? '').replace(/\s+/g, ' ').trim().replace(FILLER, '')
  return CLOCK.test(body) || TASK_TALK.test(body) || ASK_VERB.test(body) || INDIC_VERB.test(body) || (body.length <= 200 && ((NUMBERISH.test(body) && MATH_WORD.test(body)) || DATE_Q.test(body)))
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
