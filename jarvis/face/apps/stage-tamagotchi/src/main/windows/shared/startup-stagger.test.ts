import { afterEach, describe, expect, it, vi } from 'vitest'

import { chatPrewarmEnabled, deferStartup, STARTUP_STAGGER } from './startup-stagger'

afterEach(() => vi.useRealTimers())

describe('deferStartup', () => {
  it('runs the task after the delay and not before', async () => {
    vi.useFakeTimers()
    const task = vi.fn()
    deferStartup(task, 5000)
    await vi.advanceTimersByTimeAsync(4999)
    expect(task).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2)
    expect(task).toHaveBeenCalledOnce()
  })

  it('does not run for a window that is gone, and can be cancelled', async () => {
    vi.useFakeTimers()
    const gone = vi.fn()
    deferStartup(gone, 1000, { isAlive: () => false })
    const cancelled = vi.fn()
    const cancel = deferStartup(cancelled, 1000)
    cancel()
    await vi.advanceTimersByTimeAsync(2000)
    expect(gone).not.toHaveBeenCalled()
    expect(cancelled).not.toHaveBeenCalled()
  })

  it('reports a failing task and never throws into the caller', async () => {
    vi.useFakeTimers()
    const onError = vi.fn()
    deferStartup(async () => {
      throw new Error('window could not load')
    }, 10, { onError })
    deferStartup(() => {
      throw new Error('sync failure')
    }, 10, { onError })
    await vi.advanceTimersByTimeAsync(20)
    expect(onError).toHaveBeenCalledTimes(2)
  })
})

describe('the start-up order', () => {
  it('brings the avatar first, then the inlay, the chat and Beat Sync one after another', () => {
    expect(STARTUP_STAGGER.inlayMs).toBeGreaterThan(0)
    expect(STARTUP_STAGGER.chatPrewarmMs).toBeGreaterThan(STARTUP_STAGGER.inlayMs)
    expect(STARTUP_STAGGER.beatSyncMs).toBeGreaterThan(STARTUP_STAGGER.chatPrewarmMs)
  })

  it('pre-creates the chat unless the owner turns it off', () => {
    expect(chatPrewarmEnabled({})).toBe(true)
    expect(chatPrewarmEnabled({ JARVIS_FACE_PREWARM_CHAT: '1' })).toBe(true)
    expect(chatPrewarmEnabled({ JARVIS_FACE_PREWARM_CHAT: '0' })).toBe(false)
  })
})
