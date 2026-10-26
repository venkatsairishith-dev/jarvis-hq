/* eslint-disable style/max-statements-per-line */
import { describe, expect, it, vi } from 'vitest'

import { JARVIS_ASK_CLOSE_TOOL, JARVIS_ASK_SUBMIT_TOOL, JARVIS_ASK_WANTED_TOOL } from './jarvis-ask-logic'
import { askEnabled, MIN_POLL_GAP_MS, useAskController } from './use-jarvis-ask'

function form(over: Record<string, unknown> = {}) {
  return {
    id: 'q1',
    token: 'tok1',
    title: 'Pick',
    intro: '',
    busy: false,
    status: '',
    progress: null,
    values: { pick: 'a', many: ['x'] },
    buttons: [{ id: 'ok', label: 'OK', style: 'primary' }, { id: 'cancel', label: 'Cancel', style: 'secondary' }],
    fields: [
      { type: 'radio', id: 'pick', label: 'Pick', required: true, options: [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }] },
      { type: 'checkbox_group', id: 'many', label: 'Many', options: [{ id: 'x', label: 'X', size: 5 }, { id: 'y', label: 'Y', size: 7 }] },
    ],
    ...over,
  }
}

function server(initial: Record<string, unknown> | null = form(), reply: Record<string, unknown> = { ok: true, done: true }) {
  const state = { form: initial, version: initial ? 1 : 0, reply }
  const calls: Array<{ name: string, args?: Record<string, unknown> }> = []
  const call = vi.fn(async (name: string, args?: Record<string, unknown>) => {
    calls.push({ name, args })
    if (name === JARVIS_ASK_WANTED_TOOL) {
      if ((args?.seen as number) >= state.version && state.version > 0)
        return { structuredContent: { version: state.version, unchanged: true } }
      return { structuredContent: { version: state.version, form: state.form } }
    }
    if (name === JARVIS_ASK_SUBMIT_TOOL)
      return { structuredContent: state.reply }
    if (name === JARVIS_ASK_CLOSE_TOOL)
      return { structuredContent: { ok: true } }
    throw new Error(`unexpected ${name}`)
  })
  return { state, calls, call }
}

describe('useAskController', () => {
  it('opens the panel from a form the server has, with the server\'s own values', async () => {
    const s = server()
    const c = useAskController({ call: s.call })
    expect(await c.pollOnce()).toBe(true)
    expect(c.form.value).toMatchObject({ id: 'q1', title: 'Pick' })
    expect(c.values.value).toEqual({ pick: 'a', many: ['x'] })
    expect(s.calls[0].args).toMatchObject({ seen: 0, wait_s: 20 })
  })

  it('does nothing while switched off', async () => {
    const s = server()
    const c = useAskController({ call: s.call, enabled: () => false })
    expect(await c.pollOnce()).toBe(false)
    expect(s.call).not.toHaveBeenCalled()
    expect(askEnabled({ getItem: () => 'off' })).toBe(false)
    expect(askEnabled({ getItem: () => null })).toBe(true)
    expect(askEnabled(undefined)).toBe(true)
    expect(askEnabled({ getItem: () => { throw new Error('blocked') } })).toBe(true)
  })

  it('reacts at once to the owner: a changed value is local until a button is pressed', async () => {
    const s = server()
    const c = useAskController({ call: s.call })
    await c.pollOnce()
    c.setValue('pick', 'b')
    c.setValue('many', ['x', 'y'])
    expect(c.values.value).toEqual({ pick: 'b', many: ['x', 'y'] })
    expect(s.calls.filter(x => x.name === JARVIS_ASK_SUBMIT_TOOL)).toHaveLength(0)
  })

  it('keeps the owner\'s ticks when the server only changes progress, and takes the server\'s values when it changed them', async () => {
    const s = server()
    const c = useAskController({ call: s.call })
    await c.pollOnce()
    c.setValue('many', ['x', 'y'])
    s.state.form = form({ busy: true, status: 'Looking', progress: { done: 2, total: 9 } })
    s.state.version = 2
    await c.pollOnce()
    expect(c.form.value?.busy).toBe(true)
    expect(c.values.value.many).toEqual(['x', 'y'])
    s.state.form = form({ values: { pick: 'a', many: [] } }) // a chat command: "uncheck everything"
    s.state.version = 3
    await c.pollOnce()
    expect(c.values.value.many).toEqual([])
  })

  it('a primary button needs the required answers and says what is missing', async () => {
    const s = server(form({ values: { pick: '', many: [] } }))
    const c = useAskController({ call: s.call })
    await c.pollOnce()
    expect(await c.press('ok')).toBe(false)
    expect(c.error.value).toBe('Pick needs an answer.')
    expect(s.calls.some(x => x.name === JARVIS_ASK_SUBMIT_TOOL)).toBe(false)
    c.setValue('pick', 'b')
    expect(await c.press('ok')).toBe(true)
    expect(s.calls.find(x => x.name === JARVIS_ASK_SUBMIT_TOOL)!.args).toMatchObject({ form_id: 'q1', token: 'tok1', button: 'ok', values: { pick: 'b' } })
    expect(c.form.value).toBeNull()
  })

  it('a secondary button (Cancel) needs no answers; an unknown button is never sent', async () => {
    const s = server(form({ values: { pick: '', many: [] } }))
    const c = useAskController({ call: s.call })
    await c.pollOnce()
    expect(await c.press('nope')).toBe(false)
    expect(await c.press('cancel')).toBe(true)
    expect(s.calls.filter(x => x.name === JARVIS_ASK_SUBMIT_TOOL)).toHaveLength(1)
  })

  it('shows the server\'s refusal and keeps the form', async () => {
    const s = server(form(), { ok: false, message: 'an option that is not on the form' })
    const c = useAskController({ call: s.call })
    await c.pollOnce()
    expect(await c.press('ok')).toBe(false)
    expect(c.error.value).toBe('an option that is not on the form')
    expect(c.form.value).not.toBeNull()
    expect(c.submitting.value).toBe(false)
  })

  it('a form that stays open after a click (the cleaner) is refreshed at once and is busy meanwhile', async () => {
    const s = server(form(), { ok: true, done: false })
    const c = useAskController({ call: s.call })
    await c.pollOnce()
    s.state.form = form({ busy: true, status: 'Cleaning...' })
    s.state.version = 2
    expect(await c.press('ok')).toBe(true)
    await vi.waitFor(() => expect(c.form.value?.busy).toBe(true))
    expect(c.busy.value).toBe(true)
    expect(await c.press('ok')).toBe(false) // a second click while it works is ignored
  })

  it('close clears the screen first and then tells the server', async () => {
    const s = server()
    const c = useAskController({ call: s.call })
    await c.pollOnce()
    const done = c.close()
    expect(c.form.value).toBeNull()
    await done
    expect(s.calls.find(x => x.name === JARVIS_ASK_CLOSE_TOOL)!.args).toEqual({ form_id: 'q1', token: 'tok1' })
  })

  it('notes when the server takes a form away (timeout or replaced), but not after the owner closed it', async () => {
    const s = server()
    const c = useAskController({ call: s.call, now: () => 123 })
    await c.pollOnce()
    s.state.form = null
    s.state.version = 2
    await c.pollOnce()
    expect(c.form.value).toBeNull()
    expect(c.endedAt.value).toBe(123)
    const s2 = server()
    const c2 = useAskController({ call: s2.call, now: () => 5 })
    await c2.pollOnce()
    await c2.close()
    s2.state.form = null
    s2.state.version = 2
    await c2.pollOnce()
    expect(c2.endedAt.value).toBe(0)
  })

  it('never polls twice at once and never sooner than once a second; backs off while the server is silent', async () => {
    const timers: Array<{ fn: () => void, ms: number }> = []
    let clock = 10_000
    let fail = false
    const call = vi.fn(async (_name: string) => {
      if (fail)
        throw new Error('down')
      return { structuredContent: { version: 0, form: null } }
    })
    const c = useAskController({ call, now: () => clock, setTimer: (fn, ms) => { timers.push({ fn, ms }); return timers.length }, clearTimer: () => {}, onError: () => {} })
    c.start()
    await vi.waitFor(() => expect(timers).toHaveLength(1))
    expect(call).toHaveBeenCalledTimes(1)
    expect(timers[0].ms).toBe(MIN_POLL_GAP_MS) // the answer came at once: wait the rest of the second
    clock += 20_000 // a long poll that lasted 20 s: ask again straight away
    timers[0].fn()
    await vi.waitFor(() => expect(timers).toHaveLength(2))
    expect(timers[1].ms).toBe(MIN_POLL_GAP_MS)
    fail = true
    timers[1].fn()
    await vi.waitFor(() => expect(timers).toHaveLength(3))
    expect(timers[2].ms).toBeGreaterThanOrEqual(2 * MIN_POLL_GAP_MS)
    timers[2].fn()
    await vi.waitFor(() => expect(timers).toHaveLength(4))
    expect(timers[3].ms).toBeGreaterThan(timers[2].ms)
    c.stop()
    const count = timers.length
    timers[3].fn()
    await Promise.resolve()
    expect(timers.length).toBe(count) // stopped: nothing is scheduled again
  })

  it('the first poll of a form never carries the owner\'s typed values anywhere new', async () => {
    const s = server()
    const c = useAskController({ call: s.call })
    await c.pollOnce()
    expect(JSON.stringify(s.calls)).not.toContain('typed')
  })
})
