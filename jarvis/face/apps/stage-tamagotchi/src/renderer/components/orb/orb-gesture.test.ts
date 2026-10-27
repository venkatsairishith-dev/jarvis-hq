import { describe, expect, it, vi } from 'vitest'

import { createOrbGesture, DOUBLE_CLICK_MS } from './orb-gesture'

function setup() {
  vi.useFakeTimers()
  const calls: string[] = []
  const gesture = createOrbGesture({
    onClick: () => calls.push('click'),
    onDoubleClick: () => calls.push('double'),
    onDragStart: () => calls.push('drag-start'),
    onDragMove: () => calls.push('drag-move'),
    onDragEnd: () => calls.push('drag-end'),
  })
  return { gesture, calls }
}

function click(g: ReturnType<typeof setup>['gesture'], x = 10, y = 10) {
  g.down(0, x, y)
  g.up()
}

describe('orb gestures', () => {
  it('one click is a click, after the double-click window', () => {
    const { gesture, calls } = setup()
    click(gesture)
    expect(calls).toEqual([])
    vi.advanceTimersByTime(DOUBLE_CLICK_MS + 1)
    expect(calls).toEqual(['click'])
    vi.useRealTimers()
  })

  it('two clicks inside the window are one double-click and no click', () => {
    const { gesture, calls } = setup()
    click(gesture)
    vi.advanceTimersByTime(100)
    click(gesture)
    vi.advanceTimersByTime(1000)
    expect(calls).toEqual(['double'])
    vi.useRealTimers()
  })

  it('two clicks far apart are two clicks', () => {
    const { gesture, calls } = setup()
    click(gesture)
    vi.advanceTimersByTime(DOUBLE_CLICK_MS + 50)
    click(gesture)
    vi.advanceTimersByTime(DOUBLE_CLICK_MS + 50)
    expect(calls).toEqual(['click', 'click'])
    vi.useRealTimers()
  })

  it('a small wobble is still a click', () => {
    const { gesture, calls } = setup()
    gesture.down(0, 10, 10)
    gesture.move(12, 11)
    gesture.up()
    vi.advanceTimersByTime(DOUBLE_CLICK_MS + 1)
    expect(calls).toEqual(['click'])
    vi.useRealTimers()
  })

  it('a drag never ends as a click (moving the orb does not start the microphone)', () => {
    const { gesture, calls } = setup()
    gesture.down(0, 10, 10)
    gesture.move(40, 10)
    gesture.move(80, 30)
    gesture.up()
    vi.advanceTimersByTime(2000)
    expect(calls).toEqual(['drag-start', 'drag-move', 'drag-move', 'drag-end'])
    expect(gesture.dragging()).toBe(false)
    vi.useRealTimers()
  })

  it('a drag right after a click cancels the click that was waiting for its double', () => {
    const { gesture, calls } = setup()
    click(gesture)
    vi.advanceTimersByTime(80)
    gesture.down(0, 10, 10)
    gesture.move(60, 10)
    gesture.up()
    vi.advanceTimersByTime(2000)
    expect(calls).toEqual(['drag-start', 'drag-move', 'drag-end'])
    vi.useRealTimers()
  })

  it('the right button is not a gesture (it opens the menu through contextmenu)', () => {
    const { gesture, calls } = setup()
    gesture.down(2, 10, 10)
    gesture.up()
    vi.advanceTimersByTime(2000)
    expect(calls).toEqual([])
    vi.useRealTimers()
  })

  it('moves without a press do nothing', () => {
    const { gesture, calls } = setup()
    gesture.move(500, 500)
    gesture.up()
    expect(calls).toEqual([])
    vi.useRealTimers()
  })

  it('cancel ends a drag and forgets a waiting click', () => {
    const a = setup()
    a.gesture.down(0, 0, 0)
    a.gesture.move(50, 0)
    a.gesture.cancel()
    expect(a.calls).toEqual(['drag-start', 'drag-move', 'drag-end'])
    const b = setup()
    click(b.gesture)
    b.gesture.cancel()
    vi.advanceTimersByTime(2000)
    expect(b.calls).toEqual([])
    vi.useRealTimers()
  })
})
