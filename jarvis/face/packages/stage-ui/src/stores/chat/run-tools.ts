import type { Tool } from '@xsai/shared-chat'

import type { MemoryEntry } from '../memory'

import { createDrawCardTool } from '../../tools/draw-card'
import { createMemoryTools } from '../../tools/memory'
import { createProposeRecipeTool } from '../../tools/propose-recipe'
import { createSearchIconsTool } from '../../tools/search-icons'
import { createUseRecipeTool } from '../../tools/use-recipe'
import { createWakeWordTool } from '../../tools/wake-words'
import { MEMORY_REQUEST, RECIPE_REQUEST, WAKE_WORD_REQUEST } from './lean-tools'

/**
 * What a chat request carries beyond the tools the owner selected. One place decides it, so the chat store and the request
 * budget test (`request-budget.test.ts`) read the same rules.
 *
 * JARVIS: every flag is false for a plain goal such as "open Notepad". See `lean-tools.ts` for why.
 */
export interface RunToolPlan {
  /** The memory prompt (with its index) and the three memory tools. */
  memory: boolean
  /** The tool that starts a recipe. */
  useRecipe: boolean
  /** The tool that proposes a recipe. */
  proposeRecipe: boolean
  /** The reply card tool, its prompt, and the icon search that it needs. */
  replyCards: boolean
  /** The wake word editor. */
  wakeWords: boolean
}

export interface RunToolPlanInput {
  /** The text of the latest owner message. */
  text: string
  /** The chat is the owner's own conversation: not a scene, and not a recipe's task. */
  ownerConversation: boolean
  /** A recipe's own session runs only that recipe. */
  taskSession: boolean
  memoryEnabled: boolean
  /** The memory index of the character; empty when the owner accepted no entry. */
  memoryIndex: string
  usableRecipeCount: number
  proposalsEnabled: boolean
  replyCardsEnabled: boolean
}

/** Decides which optional tools and prompts this request carries. */
export function planRunTools(input: RunToolPlanInput): RunToolPlan {
  const owner = input.ownerConversation && !input.taskSession
  return {
    memory: input.memoryEnabled && (input.memoryIndex !== '' || MEMORY_REQUEST.test(input.text)),
    useRecipe: owner && input.usableRecipeCount > 0,
    proposeRecipe: owner && input.proposalsEnabled && RECIPE_REQUEST.test(input.text),
    replyCards: owner && input.replyCardsEnabled,
    wakeWords: !input.taskSession && WAKE_WORD_REQUEST.test(input.text),
  }
}

type UseRecipeOptions = Parameters<typeof createUseRecipeTool>[0]
type ProposeRecipeOptions = Parameters<typeof createProposeRecipeTool>[0]
type WakeWordOptions = Parameters<typeof createWakeWordTool>[0]

export interface RunToolDeps {
  memory: { read: (name: string) => MemoryEntry | undefined, write: (entry: { name: string, description: string, body: string }) => MemoryEntry | undefined, forget: (name: string) => boolean }
  useRecipe: UseRecipeOptions
  proposeRecipe: ProposeRecipeOptions
  wakeWords: WakeWordOptions
}

/** The optional tools of a plan, in the order that the request lists them. */
export async function createRunTools(plan: RunToolPlan, deps: RunToolDeps): Promise<Tool[]> {
  return [
    ...(plan.wakeWords ? [await createWakeWordTool(deps.wakeWords)] : []),
    ...(plan.memory ? await createMemoryTools(deps.memory) : []),
    ...(plan.useRecipe ? await createUseRecipeTool(deps.useRecipe) : []),
    ...(plan.proposeRecipe ? await createProposeRecipeTool(deps.proposeRecipe) : []),
    // A card names its icons from a search, so the model never guesses an icon name.
    ...(plan.replyCards ? [...await createDrawCardTool(), ...await createSearchIconsTool()] : []),
  ]
}
