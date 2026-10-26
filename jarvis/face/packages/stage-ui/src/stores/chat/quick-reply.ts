import type { Conversation, StreamOptions } from '@proj-airi/core-agent'

/**
 * JARVIS D60 (speed): the pieces of the quick-command path that the chat store uses.
 *
 * The tool server's rules-first router (`desktop__quick_command`, jarvis/plugins/desktop/quick.py) is registered by the Electron app as a hidden tool
 * (never offered to a model). Before a chat model is asked, the chat store asks the router whether the owner's message is one plain command; if it is,
 * the router has already run it (through the same coordinator and kernel as every PC task) and its reply is shown and spoken as the answer.
 */

/** The hidden tool that the app registers; `getToolsByNames` finds it although no request offers it. */
export const QUICK_TOOL_NAME = 'jarvis_quick_command'

/** A router that does not answer in this time is given up on and the message goes to the chat model. A command that really runs can take a minute. */
export const QUICK_WAIT_MS = 120_000

// `[2026-10-26 14:05] ` is put before every owner message (core-agent formatTimePrefix).
const TIME_PREFIX = /^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\]\s*/

interface ContentPart { type?: string, text?: string }

/**
 * The text of the owner's latest message in a conversation, as typed or spoken, or `''` when there is none or it is not plain text (an image or a
 * recording came with it, or it starts with a reply or notice marker). Only that case may be answered by rules.
 */
export function ownerTextOf(context: Conversation): string {
  const turn = [...context.turns].reverse().find(item => item.type === 'user') as { content?: ContentPart[] } | undefined
  if (!turn || !Array.isArray(turn.content))
    return ''
  const parts = turn.content.filter(part => part?.type !== 'runtime-context')
  if (parts.length === 0 || parts.some(part => part?.type !== 'text' || typeof part.text !== 'string'))
    return ''
  const text = parts.map(part => part.text as string).join('\n').replace(TIME_PREFIX, '').trim()
  // a reply marker ("[Replying to ...]") or a notice is not a plain command
  return text.startsWith('[') ? '' : text
}

/** Shows a locally made reply the way a model's reply arrives: as stream events, so the chat, the speech and the history take it as they take any answer. */
export async function emitLocalReply(text: string, options: StreamOptions | undefined): Promise<void> {
  await options?.onStreamEvent?.({ type: 'text-delta', text })
  await options?.onStreamEvent?.({ type: 'finish' })
}
