import { describe, expect, it, vi } from 'vitest'

import { conversationTurns, createMemorySaver, memoryEnabled } from './use-jarvis-memory'

const chat = (n: number) => Array.from({ length: n }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', text: `turn ${i}` }) as const)

describe('conversationTurns', () => {
  it('keeps real turns only and drops JARVIS notices and empty text', () => {
    const turns = conversationTurns([
      { role: 'system', text: 'x' },
      { role: 'user', text: 'hello' },
      { role: 'user', text: '[A notice from JARVIS\'s own systems, not a message from the owner. ...' },
      { role: 'assistant', text: '   ' },
      { role: 'assistant', text: 'Hi.' },
    ])
    expect(turns).toEqual([{ role: 'user', text: 'hello' }, { role: 'assistant', text: 'Hi.' }])
  })

  it('keeps the last 40 turns', () => {
    expect(conversationTurns(chat(100))).toHaveLength(40)
    expect(conversationTurns(chat(100)).at(-1)?.text).toBe('turn 99')
  })
})

describe('createMemorySaver', () => {
  function make(over: Partial<Parameters<typeof createMemorySaver>[0]> = {}) {
    const save = vi.fn(async (_turns: unknown) => undefined)
    let turns = conversationTurns(chat(6))
    const saver = createMemorySaver({ turns: () => turns, save, busy: () => false, lastOwnerActivity: () => 0, now: () => 10_000_000, ...over })
    return { saver, save, set: (t: ReturnType<typeof conversationTurns>) => {
      turns = t
    } }
  }

  it('saves once after a quiet spell and not again until there is something new', async () => {
    const { saver, save, set } = make()
    expect(await saver.checkOnce()).toBe(true)
    expect(await saver.checkOnce()).toBe(false)
    set(conversationTurns(chat(11)))
    expect(await saver.checkOnce()).toBe(true)
    expect(save).toHaveBeenCalledTimes(2)
  })

  it('does not check while the window cannot be seen, and does not start a save while one is still going (D57)', async () => {
    const hidden = make({ paused: () => true })
    expect(await hidden.saver.checkOnce()).toBe(false)
    expect(hidden.save).not.toHaveBeenCalled()

    let release: () => void = () => {}
    const slow = make({ save: () => new Promise<void>((resolve) => { release = resolve }) })
    const first = slow.saver.checkOnce()
    expect(await slow.saver.checkOnce()).toBe(false)
    release()
    expect(await first).toBe(true)
  })

  it('waits while the owner is active, an answer runs, or there is too little new', async () => {
    const active = make({ lastOwnerActivity: () => 10_000_000 - 60_000 })
    expect(await active.saver.checkOnce()).toBe(false)
    const busy = make({ busy: () => true })
    expect(await busy.saver.checkOnce()).toBe(false)
    const tiny = make({ turns: () => conversationTurns(chat(3)) })
    expect(await tiny.saver.checkOnce()).toBe(false)
    expect(active.save).not.toHaveBeenCalled()
  })

  it('tries again after a failure, and a new conversation starts the count over', async () => {
    const onError = vi.fn()
    const flaky = make({ onError })
    flaky.save.mockRejectedValueOnce(new Error('tool server down'))
    expect(await flaky.saver.checkOnce()).toBe(false)
    expect(onError).toHaveBeenCalledOnce()
    expect(await flaky.saver.checkOnce()).toBe(true)
    flaky.set(conversationTurns(chat(5)))
    expect(await flaky.saver.checkOnce()).toBe(true) // 5 turns after 6 saved: a different conversation, counted from its start
  })

  it('is off when switched off', async () => {
    const off = make({ enabled: () => false })
    expect(await off.saver.checkOnce()).toBe(false)
    expect(memoryEnabled({ getItem: () => 'off' })).toBe(false)
    expect(memoryEnabled(undefined)).toBe(true)
  })
})
