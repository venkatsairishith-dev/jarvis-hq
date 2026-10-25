import type { ChatAssistantMessage } from '../../../../types/chat'

/**
 * JARVIS: the model's thinking is not shown in the chat.
 *
 * Dahl's GLM streams its thinking as `reasoning` deltas (about 1,900 characters before a 65-character answer, measured on the real stream 2026-10-25), and the face
 * showed it above the answer for minutes ("the model thinks aloud"). The owner reads answers, not working notes. The text stays in the message for the app's own use;
 * only the display is switched off here. Flip this to true to bring the thinking line back.
 */
export const SHOW_MODEL_REASONING = false

/** The reasoning text the chat may show for one assistant message: empty unless SHOW_MODEL_REASONING. */
export function reasoningToShow(message: Pick<ChatAssistantMessage, 'categorization'>, show = SHOW_MODEL_REASONING): string {
  return show ? (message.categorization?.reasoning?.trim() ?? '') : ''
}
