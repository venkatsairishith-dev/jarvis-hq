import type { Ref } from 'vue'

import { getCurrentScope, onScopeDispose, shallowRef, watch } from 'vue'

import { ACTIVE_HOLD_MS, ACTIVE_FPS, stageFrameRateFor } from '../utils/stage-frame-rate'

/** Interactions closer together than this count as one, so a cursor sweep does not recompute the rate on every position. */
const INTERACTION_MERGE_MS = 250

export interface StageFrameRateSignals {
  speaking: Ref<boolean>
  thinking: Ref<boolean>
  listening: Ref<boolean>
  /** Changes whenever the cursor moves over the window (x and y in window pixels). */
  cursorX: Ref<number>
  cursorY: Ref<number>
  /** True while the cursor is outside the window. Moves outside it do not count as use. */
  cursorOutside: Ref<boolean>
  now?: () => number
  hidden?: () => boolean
}

/**
 * The frame rate to ask the avatar stage for: 60 while the character speaks, thinks or listens or the owner used the window in the
 * last 3 s, 30 otherwise (see `utils/stage-frame-rate.ts`). It starts active, so the model load and the first seconds are smooth.
 *
 * Call `touch()` for a key press or click in the window. The caller wires those to DOM events.
 */
export function useStageFrameRate(signals: StageFrameRateSignals) {
  const now = signals.now ?? Date.now
  const frameRate = shallowRef(ACTIVE_FPS)
  let lastInteractionAt = now()
  let holdTimer: ReturnType<typeof setTimeout> | undefined

  function refresh() {
    if (holdTimer)
      clearTimeout(holdTimer)
    holdTimer = undefined
    const at = now()
    frameRate.value = stageFrameRateFor({
      speaking: signals.speaking.value,
      thinking: signals.thinking.value,
      listening: signals.listening.value,
      lastInteractionAt,
      now: at,
      hidden: signals.hidden?.() ?? false,
    })
    // Back to the idle rate when the hold runs out, without waiting for the next signal.
    const left = lastInteractionAt + ACTIVE_HOLD_MS - at
    if (frameRate.value === ACTIVE_FPS && left > 0)
      holdTimer = setTimeout(refresh, left + 20)
  }

  function touch() {
    const at = now()
    if (at - lastInteractionAt < INTERACTION_MERGE_MS)
      return
    lastInteractionAt = at
    refresh()
  }

  const stopSignals = watch([signals.speaking, signals.thinking, signals.listening], refresh)
  const stopCursor = watch([signals.cursorX, signals.cursorY], () => {
    if (!signals.cursorOutside.value)
      touch()
  })
  refresh()

  function stop() {
    stopSignals()
    stopCursor()
    if (holdTimer)
      clearTimeout(holdTimer)
    holdTimer = undefined
  }
  if (getCurrentScope())
    onScopeDispose(stop)

  return { frameRate, touch, refresh, stop }
}
