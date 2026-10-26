/**
 * How often the avatar stage draws (D57).
 *
 * Before, the Live2D and VRM stages drew every frame the display offered (60 Hz, 144 Hz on a fast panel) for as long as the window
 * existed, also while the character only breathed. The window is transparent and always on top, so every frame also goes through the
 * desktop compositor on a laptop GPU that a local model shares. The stage now draws at 30 frames per second while nothing happens and at
 * 60 while the character is in use. A limit the owner sets in Settings still wins (the stage reads it first).
 */

/** Frames per second while the character is idle. Enough for breathing and blinking. */
export const IDLE_FPS = 30
/** Frames per second while the character speaks, thinks, listens, or the owner is moving the cursor or typing. */
export const ACTIVE_FPS = 60
/** The stage stays at the active rate this long after the last sign of use, so the end of a sentence or a cursor stop is not choppy. */
export const ACTIVE_HOLD_MS = 3_000
/** The pixel tests that decide whether a click goes through the window read the canvas at most this often. */
export const HIT_TEST_SAMPLE_MS = 50

export interface StageActivity {
  speaking: boolean
  thinking: boolean
  listening: boolean
  /** Epoch ms of the last cursor move, key press or click in the window. 0 when none yet. */
  lastInteractionAt: number
  now: number
}

/** True while the character is in use: it speaks, thinks or listens, or the owner did something a moment ago. */
export function isStageActive(activity: StageActivity): boolean {
  if (activity.speaking || activity.thinking || activity.listening)
    return true
  return activity.lastInteractionAt > 0 && activity.now - activity.lastInteractionAt < ACTIVE_HOLD_MS
}

/** The frame rate to ask the stage for. A window nobody sees (minimized, hidden) draws at the idle rate; the browser pauses it anyway. */
export function stageFrameRateFor(activity: StageActivity & { hidden?: boolean }): number {
  if (activity.hidden)
    return IDLE_FPS
  return isStageActive(activity) ? ACTIVE_FPS : IDLE_FPS
}
