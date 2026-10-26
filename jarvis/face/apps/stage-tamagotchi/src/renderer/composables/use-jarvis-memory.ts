/**
 * Memory across days: after the conversation has been quiet for a while, the last turns go to the JARVIS tool server, which keeps a few plain
 * bullets in the vault (`jarvis_remember_chat`). The next conversation recalls them (`jarvis_recall`, asked for by the character card).
 *
 * Only the main window runs this. Nothing is sent while the owner is active or an answer is running, nothing is sent twice, and the
 * server decides what is worth keeping (and keeps no verbatim text). Switch off: localStorage 'jarvis:memory' = 'off'.
 */

export const JARVIS_REMEMBER_TOOL = 'jarvis::jarvis_remember_chat'
export const JARVIS_MEMORY_OFF_KEY = 'jarvis:memory'
export const NOTICE_PREFIX = '[A notice from JARVIS\'s own systems'
const CHECK_MS = 60_000
const QUIET_MS = 10 * 60_000
const MIN_NEW_TURNS = 4
const MAX_TURNS = 40

export interface MemoryTurn {
  role: 'user' | 'assistant'
  text: string
}

/** Only real conversation: user and assistant turns with text, without the notices JARVIS handed the character itself. */
export function conversationTurns(messages: ReadonlyArray<{ role?: string, text?: string }>): MemoryTurn[] {
  return messages
    .filter(m => (m.role === 'user' || m.role === 'assistant') && typeof m.text === 'string')
    .map(m => ({ role: m.role as 'user' | 'assistant', text: (m.text as string).trim() }))
    .filter(t => t.text && !t.text.startsWith(NOTICE_PREFIX) && !t.text.startsWith('Notice ('))
    .slice(-MAX_TURNS)
}

export interface MemorySaverDeps {
  turns: () => MemoryTurn[]
  save: (turns: MemoryTurn[]) => Promise<unknown>
  busy: () => boolean
  lastOwnerActivity: () => number
  now?: () => number
  enabled?: () => boolean
  /** True while nobody sees the window (minimized, hidden): the check skips its turn. */
  paused?: () => boolean
  onError?: (error: unknown) => void
}

export function createMemorySaver(deps: MemorySaverDeps) {
  const now = deps.now ?? Date.now
  let saved = 0
  let timer: ReturnType<typeof setInterval> | undefined
  /** A save that is still going is not started again by the next check. */
  let running = false

  async function checkOnce(): Promise<boolean> {
    if (running || (deps.enabled && !deps.enabled()) || deps.paused?.())
      return false
    const turns = deps.turns()
    if (turns.length < saved)
      saved = 0 // another conversation (or a cleared one) started
    if (turns.length - saved < MIN_NEW_TURNS || deps.busy() || now() - deps.lastOwnerActivity() < QUIET_MS)
      return false
    running = true
    try {
      await deps.save(turns)
      saved = turns.length
      return true
    }
    catch (error) {
      deps.onError?.(error)
      return false
    }
    finally {
      running = false
    }
  }

  return {
    checkOnce,
    start() {
      if (!timer)
        timer = setInterval(() => void checkOnce(), CHECK_MS)
    },
    stop() {
      if (timer)
        clearInterval(timer)
      timer = undefined
    },
  }
}

export function memoryEnabled(storage: Pick<Storage, 'getItem'> | undefined): boolean {
  try {
    return storage?.getItem(JARVIS_MEMORY_OFF_KEY) !== 'off'
  }
  catch {
    return true
  }
}
