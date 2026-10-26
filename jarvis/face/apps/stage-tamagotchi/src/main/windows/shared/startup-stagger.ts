/**
 * Staggers the work that is not the avatar itself (D57).
 *
 * The face used to start four renderers in the same second: the avatar window, the voice inlay, the hidden Beat Sync page and, on the
 * first click, the chat. Each one loads the whole renderer bundle and runs the App's start-up. On the owner's laptop the avatar's
 * model load and the first clicks then met three other page loads (and, beside them, a local model). Now the avatar window comes first;
 * the others start after these delays, one after another, so none of them lands inside the avatar's load.
 */

export interface StartupStaggerDelays {
  /** The voice inlay: created hidden-in-plain-sight (transparent, click-through) so the first speech does not wait for a page load. */
  inlayMs: number
  /** The floating chat: created folded and hidden, so the first click on the chat button shows it at once. */
  chatPrewarmMs: number
  /** The hidden Beat Sync page: needed only when the owner turns Beat Sync on. */
  beatSyncMs: number
}

export const STARTUP_STAGGER: StartupStaggerDelays = {
  inlayMs: 6_000,
  chatPrewarmMs: 12_000,
  beatSyncMs: 20_000,
}

export interface DeferOptions {
  /** False once the work has no use any more (the window it belongs to is gone). Checked when the time comes. */
  isAlive?: () => boolean
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
  onError?: (error: unknown) => void
}

/**
 * Runs `task` after `delayMs`, unless `isAlive` says no by then. Returns the function that cancels it. A failing task is reported
 * to `onError` and never reaches the caller: this work is a head start, not something the face depends on.
 */
export function deferStartup(task: () => unknown | Promise<unknown>, delayMs: number, options: DeferOptions = {}): () => void {
  const setTimer = options.setTimer ?? ((fn, ms) => {
    const handle = setTimeout(fn, ms)
    // A pending head start must not keep the app alive at quit.
    handle.unref?.()
    return handle
  })
  const clearTimer = options.clearTimer ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>))
  const handle = setTimer(() => {
    if (options.isAlive && !options.isAlive())
      return
    void Promise.resolve()
      .then(task)
      .catch(error => options.onError?.(error))
  }, Math.max(0, delayMs))
  return () => clearTimer(handle)
}

/** Whether the chat is created before the first click. `JARVIS_FACE_PREWARM_CHAT=0` turns it off (one renderer less at start-up). */
export function chatPrewarmEnabled(env: Record<string, string | undefined>): boolean {
  return env.JARVIS_FACE_PREWARM_CHAT !== '0'
}
