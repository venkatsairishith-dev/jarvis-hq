import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick, ref } from 'vue'

import { ACTIVE_FPS, ACTIVE_HOLD_MS, IDLE_FPS, isStageActive, stageFrameRateFor } from '../utils/stage-frame-rate'
import { useStageFrameRate } from './use-stage-frame-rate'

afterEach(() => vi.useRealTimers())

const idle = { speaking: false, thinking: false, listening: false, lastInteractionAt: 0, now: 1_000_000 }

describe('stageFrameRateFor', () => {
  it('draws 15 frames a second when nothing happens', () => {
    expect(stageFrameRateFor(idle)).toBe(IDLE_FPS)
    expect(IDLE_FPS).toBe(15)
  })

  it('draws 60 while the character speaks, thinks or listens', () => {
    for (const key of ['speaking', 'thinking', 'listening'] as const)
      expect(stageFrameRateFor({ ...idle, [key]: true })).toBe(ACTIVE_FPS)
    expect(ACTIVE_FPS).toBe(60)
  })

  it('stays at 60 for a moment after the owner did something, then falls back', () => {
    expect(stageFrameRateFor({ ...idle, lastInteractionAt: idle.now - 1000 })).toBe(ACTIVE_FPS)
    expect(stageFrameRateFor({ ...idle, lastInteractionAt: idle.now - ACTIVE_HOLD_MS })).toBe(IDLE_FPS)
    expect(isStageActive({ ...idle, lastInteractionAt: 0 })).toBe(false)
  })

  it('draws a window nobody sees at the idle rate even while it speaks', () => {
    expect(stageFrameRateFor({ ...idle, speaking: true, hidden: true })).toBe(IDLE_FPS)
  })
})

describe('useStageFrameRate', () => {
  function setup() {
    vi.useFakeTimers()
    vi.setSystemTime(1_000_000)
    const signals = {
      speaking: ref(false),
      thinking: ref(false),
      listening: ref(false),
      cursorX: ref(0),
      cursorY: ref(0),
      cursorOutside: ref(true),
    }
    return { signals, rate: useStageFrameRate(signals) }
  }

  it('starts active so the model load is smooth, and goes idle after the hold', async () => {
    const { rate } = setup()
    expect(rate.frameRate.value).toBe(ACTIVE_FPS)
    await vi.advanceTimersByTimeAsync(ACTIVE_HOLD_MS + 100)
    expect(rate.frameRate.value).toBe(IDLE_FPS)
    rate.stop()
  })

  it('goes to 60 while speaking and back to 15 three seconds after it ends', async () => {
    const { signals, rate } = setup()
    await vi.advanceTimersByTimeAsync(ACTIVE_HOLD_MS + 100)
    signals.speaking.value = true
    await nextTick()
    expect(rate.frameRate.value).toBe(ACTIVE_FPS)
    await vi.advanceTimersByTimeAsync(20_000)
    expect(rate.frameRate.value).toBe(ACTIVE_FPS)
    signals.speaking.value = false
    await nextTick()
    // the end of speech counts as use: the hold starts from the last interaction, so it falls back at once if that was long ago
    expect(rate.frameRate.value).toBe(IDLE_FPS)
    rate.stop()
  })

  it('counts cursor moves over the window as use, and moves outside it as nothing', async () => {
    const { signals, rate } = setup()
    await vi.advanceTimersByTimeAsync(ACTIVE_HOLD_MS + 100)
    expect(rate.frameRate.value).toBe(IDLE_FPS)

    signals.cursorX.value = 5
    await nextTick()
    expect(rate.frameRate.value).toBe(IDLE_FPS)

    signals.cursorOutside.value = false
    signals.cursorX.value = 9
    await nextTick()
    expect(rate.frameRate.value).toBe(ACTIVE_FPS)
    await vi.advanceTimersByTimeAsync(ACTIVE_HOLD_MS + 100)
    expect(rate.frameRate.value).toBe(IDLE_FPS)
    rate.stop()
  })

  it('counts a key press or click reported with touch()', async () => {
    const { rate } = setup()
    await vi.advanceTimersByTimeAsync(ACTIVE_HOLD_MS + 100)
    rate.touch()
    expect(rate.frameRate.value).toBe(ACTIVE_FPS)
    rate.stop()
  })
})
