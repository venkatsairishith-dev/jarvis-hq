import { describe, expect, it, vi } from 'vitest'

import { POLL_WAIT_MS } from './poll-guard'
import { buildSpokenTurn, createSpokenPoller, JARVIS_SPOKEN_TOOL, parseSpoken, proactiveEnabled } from './use-jarvis-spoken'

const result = (notices: unknown[]) => ({ structuredContent: { count: notices.length, notices } })

describe('parseSpoken', () => {
  it('reads structured content and the JSON text fallback', () => {
    expect(parseSpoken(result([{ urgency: 'urgent', message: 'A service is down.' }]))).toEqual([{ urgency: 'urgent', message: 'A service is down.' }])
    expect(parseSpoken({ content: [{ type: 'text', text: JSON.stringify({ notices: [{ urgency: 'normal', message: 'Hi' }] }) }] })).toHaveLength(1)
  })

  it('returns nothing for errors, junk and empty messages, and cuts long ones', () => {
    expect(parseSpoken({ isError: true, structuredContent: { notices: [{ message: 'x' }] } })).toEqual([])
    expect(parseSpoken({ content: [{ type: 'text', text: 'not json' }] })).toEqual([])
    expect(parseSpoken(result([{ message: '   ' }]))).toEqual([])
    expect(parseSpoken(result([{ urgency: 'weird', message: 'y'.repeat(900) }]))[0]).toMatchObject({ urgency: 'normal' })
    expect(parseSpoken(result([{ message: 'y'.repeat(900) }]))[0].message).toHaveLength(300)
  })
})

describe('buildSpokenTurn', () => {
  it('says it is information from JARVIS, not the owner, and asks for no tools', () => {
    const turn = buildSpokenTurn({ urgency: 'normal', message: 'Dentist starts in 20 minutes.' })
    expect(turn).toContain('not a message from the owner')
    expect(turn).toContain('Do not call any tool')
    expect(turn.endsWith('Notice (normal): Dentist starts in 20 minutes.')).toBe(true)
  })
})

describe('createSpokenPoller', () => {
  function make(over: Partial<Parameters<typeof createSpokenPoller>[0]> = {}) {
    const say = vi.fn(async (_text: string) => undefined)
    const call = vi.fn(async (_name: string) => result([{ urgency: 'normal', message: '1 task is waiting for your approval in the console.' }]))
    const poller = createSpokenPoller({ call, say, busy: () => false, lastOwnerActivity: () => 0, now: () => 1_000_000, ...over })
    return { poller, say, call }
  }

  it('asks the server for the next notice and has the character say it', async () => {
    const { poller, say, call } = make()
    expect(await poller.pollOnce()).toBe(1)
    expect(call).toHaveBeenCalledWith(JARVIS_SPOKEN_TOOL)
    expect(say.mock.calls[0][0]).toContain('waiting for your approval')
  })

  it('does not even ask while the character is busy or the owner is typing', async () => {
    const busy = make({ busy: () => true })
    expect(await busy.poller.pollOnce()).toBe(0)
    expect(busy.call).not.toHaveBeenCalled()
    const typing = make({ lastOwnerActivity: () => 1_000_000 - 5_000 })
    expect(await typing.poller.pollOnce()).toBe(0)
    expect(typing.call).not.toHaveBeenCalled()
  })

  it('is silent when switched off, and a failing server is not an error for the owner', async () => {
    const off = make({ enabled: () => false })
    expect(await off.poller.pollOnce()).toBe(0)
    expect(off.call).not.toHaveBeenCalled()
    const onError = vi.fn()
    const down = make({
      call: async () => {
        throw new Error('tool server down')
      },
      onError,
    })
    expect(await down.poller.pollOnce()).toBe(0)
    expect(onError).toHaveBeenCalledOnce()
  })

  it('skips its turn while the window cannot be seen (D57)', async () => {
    const hidden = make({ paused: () => true })
    expect(await hidden.poller.pollOnce()).toBe(0)
    expect(hidden.call).not.toHaveBeenCalled()
  })

  it('stops waiting for a tool server that never answers, and polls again afterwards (D57)', async () => {
    vi.useFakeTimers()
    try {
      const onError = vi.fn()
      let calls = 0
      const stuck = make({
        call: () => {
          calls += 1
          return calls === 1 ? new Promise(() => {}) : Promise.resolve(result([]))
        },
        onError,
      })
      const first = stuck.poller.pollOnce()
      await vi.advanceTimersByTimeAsync(POLL_WAIT_MS + 10)
      expect(await first).toBe(0)
      expect(onError).toHaveBeenCalledOnce()
      expect(String((onError.mock.calls[0][0] as Error).message)).toContain('did not answer')
      // the stuck poll released the lock: the next one runs
      expect(await stuck.poller.pollOnce()).toBe(0)
      expect(calls).toBe(2)
    }
    finally {
      vi.useRealTimers()
    }
  })

  it('never runs two polls at once', async () => {
    let release: () => void = () => {}
    const slow = make({
      call: () => new Promise((resolve) => {
        release = () => resolve(result([]))
      }),
    })
    const first = slow.poller.pollOnce()
    expect(await slow.poller.pollOnce()).toBe(0)
    release()
    await first
  })
})

describe('proactiveEnabled', () => {
  it('is on unless the owner set it to off', () => {
    expect(proactiveEnabled({ getItem: () => null })).toBe(true)
    expect(proactiveEnabled({ getItem: () => 'off' })).toBe(false)
    expect(proactiveEnabled(undefined)).toBe(true)
    expect(proactiveEnabled({
      getItem: () => {
        throw new Error('blocked')
      },
    })).toBe(true)
  })
})
