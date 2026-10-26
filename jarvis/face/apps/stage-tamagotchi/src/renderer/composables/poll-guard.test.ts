import { afterEach, describe, expect, it, vi } from 'vitest'

import { pageIsHidden, POLL_CALL_TIMEOUT_MS, POLL_WAIT_MS, shouldPollNow, withDeadline } from './poll-guard'

afterEach(() => vi.useRealTimers())

describe('withDeadline', () => {
  it('passes an answer that comes in time, and clears its timer', async () => {
    vi.useFakeTimers()
    await expect(withDeadline(Promise.resolve(7), 1000)).resolves.toBe(7)
    expect(vi.getTimerCount()).toBe(0)
  })

  it('rejects when nothing comes, and keeps the wait shorter than the long tool-call limit', async () => {
    vi.useFakeTimers()
    const result = withDeadline(new Promise(() => {}), 2000, 'the poll')
    const caught = result.catch((error: Error) => error.message)
    await vi.advanceTimersByTimeAsync(2001)
    expect(await caught).toBe('the poll did not answer in 2 s')
    expect(POLL_WAIT_MS).toBeGreaterThan(POLL_CALL_TIMEOUT_MS)
    expect(POLL_WAIT_MS).toBeLessThan(15_000)
  })
})

describe('pageIsHidden', () => {
  it('is true only for a hidden page', () => {
    expect(pageIsHidden({ hidden: true })).toBe(true)
    expect(pageIsHidden({ hidden: false })).toBe(false)
    expect(pageIsHidden(undefined)).toBe(false)
  })
})

describe('shouldPollNow', () => {
  const base = { hidden: false, focused: true, now: 100_000, lastPollAt: 99_000, unfocusedMs: 15_000 }
  it('never polls a hidden window', () => {
    expect(shouldPollNow({ ...base, hidden: true })).toBe(false)
  })
  it('polls on every tick while the owner uses the window', () => {
    expect(shouldPollNow(base)).toBe(true)
  })
  it('polls a window without focus only after the slow interval, and a first poll at once', () => {
    expect(shouldPollNow({ ...base, focused: false })).toBe(false)
    expect(shouldPollNow({ ...base, focused: false, lastPollAt: 85_000 })).toBe(true)
    expect(shouldPollNow({ ...base, focused: false, lastPollAt: 0 })).toBe(true)
  })
})
