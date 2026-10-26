/**
 * Makes an async action run once at a time: a call made while a run is still going joins that run instead of queuing a second one (D57).
 *
 * The chat button flips the chat: it opens a folded chat and folds an open one. The first press can take seconds (the chat window is
 * created and its page loads), and the owner presses again because nothing seems to happen. Queued behind the first, the second press
 * ran after the chat had opened and folded it again, so the chat flashed and went away. Joining the first run makes the second press
 * the same press.
 */
export function singleFlight<T>(action: () => Promise<T>): () => Promise<T> {
  let running: Promise<T> | undefined
  return () => {
    if (running)
      return running
    const run = action().finally(() => {
      if (running === run)
        running = undefined
    })
    running = run
    return run
  }
}
