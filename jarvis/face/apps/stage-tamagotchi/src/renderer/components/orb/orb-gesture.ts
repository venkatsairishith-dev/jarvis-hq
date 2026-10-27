/**
 * D78: the gestures of the orb, as a small state machine: a click (talk / stop), a double-click (open the text box), a drag (move it). A drag
 * never ends as a click, so moving the orb never starts the microphone; and the drag works without any CSS drag region, which swallows
 * the clicks on the orb on Windows.
 *
 * Pure: the timers are passed in, so the timings are tested without a clock.
 */

export interface OrbGestureOptions {
  /** The left button went down: a click, a double-click or a drag may follow. */
  onPress?: () => void
  /** One left click (after the double-click window has passed without a second one). */
  onClick: () => void
  onDoubleClick: () => void
  onDragStart: () => void
  onDragMove: () => void
  onDragEnd: () => void
  /** The time in which a second click makes a double-click. */
  doubleClickMs?: number
  /** The distance, in pixels, from which a press is a drag. */
  dragThreshold?: number
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
}

export interface OrbGesture {
  down: (button: number, x: number, y: number) => void
  move: (x: number, y: number) => void
  up: () => void
  /** The pointer was lost (window hidden, capture lost): a drag ends, a press is forgotten. */
  cancel: () => void
  dragging: () => boolean
}

export const DOUBLE_CLICK_MS = 260
export const DRAG_THRESHOLD = 5

export function createOrbGesture(options: OrbGestureOptions): OrbGesture {
  const windowMs = options.doubleClickMs ?? DOUBLE_CLICK_MS
  const threshold = options.dragThreshold ?? DRAG_THRESHOLD
  const setTimer = options.setTimer ?? ((fn, ms) => setTimeout(fn, ms))
  const clearTimer = options.clearTimer ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>))
  let pressed: { x: number, y: number } | undefined
  let drag = false
  let pending: unknown

  function clearPending() {
    if (pending !== undefined) {
      clearTimer(pending)
      pending = undefined
    }
  }

  return {
    down(button, x, y) {
      // only the left button; the right one is the menu (a contextmenu event)
      if (button !== 0)
        return
      pressed = { x, y }
      drag = false
      options.onPress?.()
    },
    move(x, y) {
      if (!pressed)
        return
      if (!drag) {
        if (Math.hypot(x - pressed.x, y - pressed.y) < threshold)
          return
        drag = true
        // a drag cancels a click that was waiting for its double
        clearPending()
        options.onDragStart()
      }
      options.onDragMove()
    },
    up() {
      if (!pressed)
        return
      pressed = undefined
      if (drag) {
        drag = false
        options.onDragEnd()
        return
      }
      if (pending !== undefined) {
        clearPending()
        options.onDoubleClick()
        return
      }
      pending = setTimer(() => {
        pending = undefined
        options.onClick()
      }, windowMs)
    },
    cancel() {
      pressed = undefined
      clearPending()
      if (drag) {
        drag = false
        options.onDragEnd()
      }
    },
    dragging: () => drag,
  }
}
