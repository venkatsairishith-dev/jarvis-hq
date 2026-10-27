import type { GateWindow } from './window-gate'

import { describe, expect, it, vi } from 'vitest'

import { createWindowGate } from './window-gate'

class FakeWindow implements GateWindow {
  static nextId = 1
  id = FakeWindow.nextId++
  visible: boolean
  destroyed = false
  skipTaskbar = false
  shows: string[] = []
  constructor(visible = true) {
    this.visible = visible
  }

  isDestroyed() { return this.destroyed }
  isVisible() { return this.visible }
  hide() { this.visible = false }
  showInactive() {
    this.visible = true
    this.shows.push('showInactive')
  }

  show() {
    this.visible = true
    this.shows.push('show')
  }

  setSkipTaskbar(skip: boolean) { this.skipTaskbar = skip }
}

function clock(start = 1_000_000) {
  let t = start
  return {
    now: () => t,
    advance: (ms: number) => {
      t += ms
    },
  }
}

describe('window gate: hidden refuses every show path', () => {
  it('delivers at once while shown', () => {
    const gate = createWindowGate()
    const deliver = vi.fn()
    expect(gate.request('window', deliver)).toBe(true)
    expect(deliver).toHaveBeenCalledOnce()
    expect(gate.state().queued).toBe(0)
  })

  it('hides every visible window, takes the taskbar button away and holds requests', () => {
    const gate = createWindowGate()
    const a = new FakeWindow()
    const b = new FakeWindow()
    const c = new FakeWindow(false)
    expect(gate.hide([a, b, c])).toBe(true)
    expect([a.visible, b.visible, c.visible]).toEqual([false, false, false])
    expect([a.skipTaskbar, b.skipTaskbar, c.skipTaskbar]).toEqual([true, true, true])
    const deliver = vi.fn()
    expect(gate.request('window', deliver, 'win:9')).toBe(false)
    expect(deliver).not.toHaveBeenCalled()
    expect(gate.state()).toMatchObject({ hidden: true, queued: 1 })
  })

  it('a second hide does nothing', () => {
    const gate = createWindowGate()
    expect(gate.hide([])).toBe(true)
    expect(gate.hide([])).toBe(false)
  })

  it('a window created while hidden is kept off the screen and out of the taskbar', () => {
    const gate = createWindowGate()
    gate.hide([])
    const created = new FakeWindow(true)
    gate.adopt(created)
    expect(created.visible).toBe(false)
    expect(created.skipTaskbar).toBe(true)
  })

  it('a window that was still mapping when it was first hidden is hidden again a moment later', () => {
    const later: Array<[() => void, number]> = []
    const gate = createWindowGate({ schedule: (fn, ms) => later.push([fn, ms]) })
    gate.hide([])
    const created = new FakeWindow(false)
    gate.adopt(created)
    // the toolkit finishes showing it after our first hide
    created.visible = true
    for (const [fn] of later)
      fn()
    expect(created.visible).toBe(false)
  })

  it('does not hide a window again once the gate has opened', () => {
    const later: Array<[() => void, number]> = []
    const gate = createWindowGate({ schedule: (fn, ms) => later.push([fn, ms]) })
    gate.hide([])
    const created = new FakeWindow(false)
    gate.adopt(created)
    gate.unhide()
    created.visible = true
    for (const [fn] of later)
      fn()
    expect(created.visible).toBe(true)
  })

  it('adopting a window while shown changes nothing', () => {
    const gate = createWindowGate()
    const created = new FakeWindow(true)
    gate.adopt(created)
    expect(created.visible).toBe(true)
    expect(created.skipTaskbar).toBe(false)
  })

  it('counts requests and de-duplicates by key, keeping the first place', () => {
    const gate = createWindowGate()
    gate.hide([])
    const order: string[] = []
    gate.request('window', () => order.push('chat-1'), 'win:1')
    gate.request('notification', () => order.push('note'))
    gate.request('window', () => order.push('chat-2'), 'win:1')
    expect(gate.state().queued).toBe(2)
    const result = gate.unhide()
    expect(order).toEqual(['chat-2', 'note'])
    expect(result.delivered).toBe(2)
  })

  it('unhide delivers in order, once, and the queue is empty after it', () => {
    const gate = createWindowGate()
    gate.hide([])
    const order: number[] = []
    for (let i = 1; i <= 5; i++)
      gate.request('window', () => order.push(i), `win:${i}`)
    const first = gate.unhide()
    expect(order).toEqual([1, 2, 3, 4, 5])
    expect(first.delivered).toBe(5)
    expect(gate.state()).toMatchObject({ hidden: false, queued: 0 })
    // nothing is delivered a second time
    const second = gate.unhide()
    expect(second.delivered).toBe(0)
    expect(order).toEqual([1, 2, 3, 4, 5])
  })

  it('a request that throws does not stop the ones behind it', () => {
    const gate = createWindowGate()
    gate.hide([])
    const after = vi.fn()
    gate.request('window', () => {
      throw new Error('boom')
    })
    gate.request('window', after)
    const result = gate.unhide()
    expect(after).toHaveBeenCalledOnce()
    expect(result.delivered).toBe(1)
  })

  it('shows again the windows that were on the screen, not the ones that were not', () => {
    const gate = createWindowGate()
    const orb = new FakeWindow(true)
    const closedChat = new FakeWindow(false)
    gate.hide([orb, closedChat])
    const result = gate.unhide()
    expect(orb.visible).toBe(true)
    expect(orb.skipTaskbar).toBe(false)
    expect(closedChat.visible).toBe(false)
    expect(result.restored).toBe(1)
  })

  it('does not count the own show request of a window it shows again', () => {
    const gate = createWindowGate()
    const orb = new FakeWindow(true)
    gate.hide([orb])
    const deliver = vi.fn()
    gate.request('window', deliver, `win:${orb.id}`)
    expect(gate.unhide().delivered).toBe(0)
    expect(deliver).not.toHaveBeenCalled()
  })

  it('a window that was destroyed while hidden is skipped', () => {
    const gate = createWindowGate()
    const orb = new FakeWindow(true)
    gate.hide([orb])
    orb.destroyed = true
    expect(() => gate.unhide()).not.toThrow()
    expect(orb.shows).toEqual([])
  })

  it('remembers a taskbar request made while hidden and applies it at unhide', () => {
    const gate = createWindowGate()
    const orb = new FakeWindow(true)
    gate.hide([orb])
    expect(gate.noteSkipTaskbar(orb, false)).toBe(true)
    expect(orb.skipTaskbar).toBe(true)
    gate.unhide()
    expect(orb.skipTaskbar).toBe(false)
    expect(gate.noteSkipTaskbar(orb, true)).toBe(false)
  })

  it('caps the queue and keeps the newest', () => {
    const gate = createWindowGate({ maxQueued: 3 })
    gate.hide([])
    const seen: number[] = []
    for (let i = 1; i <= 6; i++)
      gate.request('notification', () => seen.push(i))
    expect(gate.state().queued).toBe(3)
    gate.unhide()
    expect(seen).toEqual([4, 5, 6])
  })

  it('reports counts only, to the log and to onChange', () => {
    const logs: string[] = []
    const changes: unknown[] = []
    const gate = createWindowGate({ log: m => logs.push(m), onChange: s => changes.push(s) })
    gate.hide([])
    gate.request('notification', () => {}, undefined)
    const text = JSON.stringify(logs)
    expect(text).toContain('1 waiting')
    expect(JSON.stringify(changes)).not.toContain('deliver')
    expect(Object.keys(changes.at(-1) as object).sort()).toEqual(['hidden', 'hiddenSince', 'queued'])
  })
})

describe('window gate: forgotten-hidden guard', () => {
  it('stays quiet for a short hide', () => {
    const t = clock()
    const gate = createWindowGate({ now: t.now, forgottenHours: () => 12 })
    gate.hide([])
    t.advance(11 * 3_600_000)
    expect(gate.unhide().forgotten).toBe(false)
  })

  it('reports once after more than the limit', () => {
    const t = clock()
    const gate = createWindowGate({ now: t.now, forgottenHours: () => 12 })
    gate.hide([])
    t.advance(12 * 3_600_000 + 1)
    const first = gate.unhide()
    expect(first.forgotten).toBe(true)
    expect(first.hiddenForMs).toBeGreaterThan(12 * 3_600_000)
    gate.hide([])
    t.advance(1000)
    expect(gate.unhide().forgotten).toBe(false)
  })

  it('reads the limit at the unhide, and 0 turns the guard off', () => {
    const t = clock()
    let hours = 12
    const gate = createWindowGate({ now: t.now, forgottenHours: () => hours })
    gate.hide([])
    t.advance(30 * 3_600_000)
    hours = 0
    expect(gate.unhide().forgotten).toBe(false)
  })

  it('counts from an earlier hide when the face started hidden', () => {
    const t = clock()
    const gate = createWindowGate({ now: t.now, forgottenHours: () => 12 })
    gate.hide([], { since: t.now() - 20 * 3_600_000 })
    expect(gate.unhide().forgotten).toBe(true)
  })
})

describe('window gate: start-up windows of a face that starts hidden', () => {
  it('are held and delivered but not counted', () => {
    const t = clock()
    const gate = createWindowGate({ now: t.now })
    gate.hide([], { quietUntil: t.now() + 90_000 })
    const shown = vi.fn()
    gate.request('window', shown, 'win:1')
    gate.request('window', () => {}, 'win:2')
    expect(gate.state().queued).toBe(0)
    t.advance(120_000)
    gate.request('notification', () => {})
    gate.request('window', () => {}, 'win:3')
    expect(gate.state().queued).toBe(2)
    const result = gate.unhide()
    expect(shown).toHaveBeenCalledOnce()
    expect(result.delivered).toBe(2)
  })
})
