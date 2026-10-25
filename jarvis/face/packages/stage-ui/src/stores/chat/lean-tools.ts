/**
 * JARVIS: which of AIRI's optional chat tools a request carries.
 *
 * The first request of a chat turn was 40 KB on the owner's laptop (2026-10-25), 31 KB of it tool definitions, and free
 * providers answered 413/429. AIRI offers every one of its helper tools in every request. JARVIS offers a helper only on
 * the turn that can use it: the owner's own words say so (a word below), or the owner has data for it (a memory entry,
 * a usable recipe). The tools stay registered; only the request is smaller. `jarvis/face/.../chat-request-budget.test.ts`
 * keeps the total under its limit.
 *
 * The words are English; a Hindi or Telugu message that needs one of these tools can name it in English or turn the
 * feature on in Settings.
 */

/** The owner asks JARVIS to keep or drop a fact. The memory tools and the memory index go into this request. */
export const MEMORY_REQUEST = /\b(?:remember|memor(?:y|ies|ize|ise)|forget|don'?t forget|keep in mind|note (?:that|this))\b/i

/** The owner talks about saving or running a reusable procedure. The propose-recipe tool goes into this request. */
export const RECIPE_REQUEST = /\b(?:recipes?|skills?|routines?|workflows?|automations?|automate|slash command)\b/i

/** The owner talks about the words that wake JARVIS by voice. The wake word editor goes into this request. */
export const WAKE_WORD_REQUEST = /\b(?:wake[- ]?words?|hey jarvis|wake you|call you by voice|activation (?:word|phrase))\b/i

interface TextPart { type?: unknown, text?: unknown }

/** The text of the latest owner message in a session, or an empty string. */
export function latestUserText(messages: readonly { role: string, content?: unknown }[]): string {
  const message = messages.findLast(item => item.role === 'user')
  const content = message?.content
  if (typeof content === 'string')
    return content
  if (!Array.isArray(content))
    return ''
  return (content as TextPart[])
    .map(part => (part?.type === 'text' && typeof part.text === 'string' ? part.text : ''))
    .filter(Boolean)
    .join('\n')
}
