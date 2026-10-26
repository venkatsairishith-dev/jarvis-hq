/**
 * Rules shared by the face's background polls (HUD status, spoken notices, preview, memory save). D57.
 *
 * A poll is a convenience, never something the owner waits for, so it follows three rules:
 * - it asks for a short answer: the tool call carries `timeoutMs` (the main process would otherwise allow 180 s, the limit of a real
 *   tool call such as a deck), and the poll also stops waiting by itself, so one stuck answer cannot hold a poll for minutes;
 * - it does not run while nobody sees the window (minimized or hidden);
 * - it never overlaps itself (each poller keeps its own `running` flag).
 */

/** What a background poll asks the main process for: the tool call is cut after this. */
export const POLL_CALL_TIMEOUT_MS = 8_000
/** The poll stops waiting a little after the main process has cut the call, so the main process's reason (not ours) is the one reported. */
export const POLL_WAIT_MS = POLL_CALL_TIMEOUT_MS + 2_000

/** Rejects when `promise` has not settled in `ms`. The promise itself is left alone; its late answer is dropped. */
export function withDeadline<T>(promise: Promise<T>, ms: number, label = 'the call'): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new Error(`${label} did not answer in ${Math.round(ms / 1000)} s`)), ms)
  })
  return Promise.race([promise, timeout]).finally(() => {
    if (timer)
      clearTimeout(timer)
  })
}

/** True while the page cannot be seen (minimized, hidden). A background poll skips its turn then. */
export function pageIsHidden(doc: Pick<Document, 'hidden'> | undefined = globalThis.document): boolean {
  return doc?.hidden === true
}

/**
 * Whether a status poll that runs on a timer should run now: not while hidden, and when the window is not the one the owner is using
 * (no keyboard focus) not more often than `unfocusedMs`. A poll that never ran runs at once.
 */
export function shouldPollNow(input: { hidden: boolean, focused: boolean, now: number, lastPollAt: number, unfocusedMs: number }): boolean {
  if (input.hidden)
    return false
  if (input.focused || input.lastPollAt <= 0)
    return true
  return input.now - input.lastPollAt >= input.unfocusedMs
}
