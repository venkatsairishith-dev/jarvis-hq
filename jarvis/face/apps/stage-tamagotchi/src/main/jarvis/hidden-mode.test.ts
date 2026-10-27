import type { HiddenState } from '../../shared/jarvis-look'
import type { HiddenModeDeps, PersistedHiddenState } from './hidden-mode'
import type { GateWindow } from './window-gate'

import { describe, expect, it, vi } from 'vitest'

import { createHiddenMode, resolveStartHidden, START_QUIET_MS } from './hidden-mode'
import { createWindowGate } from './window-gate'

class FakeWindow implements GateWindow {
  static nextId = 1
  id = FakeWindow.nextId++
  visible = true
  skipTaskbar = false
  isDestroyed() { return false }
  isVisible() { return this.visible }
  hide() { this.visible = false }
  showInactive() { this.visible = true }
  setSkipTaskbar(skip: boolean) { this.skipTaskbar = skip }
}

function setup(overrides: Partial<HiddenModeDeps> & { settings?: HiddenModeDeps['settings'] } = {}) {
  let t = 5_000_000
  const windows = [new FakeWindow(), new FakeWindow()]
  const trayCalls: boolean[] = []
  const persisted: Array<PersistedHiddenState | null> = []
  const emitted: HiddenState[] = []
  const ensureOrbShown = vi.fn()
  let hours = 12
  const gate = createWindowGate({ now: () => t, forgottenHours: () => hours })
  const mode = createHiddenMode({
    gate,
    getWindows: () => windows,
    settings: () => ({ showTrayWhileHidden: false, startHidden: false }),
    hideShortcutOk: () => true,
    setTrayVisible: v => trayCalls.push(v),
    persist: s => persisted.push(s),
    emit: s => emitted.push(s),
    ensureOrbShown,
    version: '1.0.0',
    now: () => t,
    ...overrides,
  })
  const advance = (ms: number) => {
    t += ms
  }
  const setHours = (h: number) => {
    hours = h
  }
  return { mode, gate, windows, trayCalls, persisted, emitted, ensureOrbShown, advance, setHours }
}

describe('start-up visibility: a hidden face is never lost', () => {
  const saved: PersistedHiddenState = { hidden: true, since: 1, version: '1.0.0' }

  it('is shown by default, even with a hidden state on disk', () => {
    expect(resolveStartHidden({ persisted: saved, startHidden: false, version: '1.0.0', firstRun: false })).toBe(false)
  })

  it('starts hidden only when the owner chose it AND the last run ended hidden', () => {
    expect(resolveStartHidden({ persisted: saved, startHidden: true, version: '1.0.0', firstRun: false })).toBe(true)
    expect(resolveStartHidden({ persisted: { ...saved, hidden: false }, startHidden: true, version: '1.0.0', firstRun: false })).toBe(false)
    expect(resolveStartHidden({ persisted: undefined, startHidden: true, version: '1.0.0', firstRun: false })).toBe(false)
    expect(resolveStartHidden({ persisted: null, startHidden: true, version: '1.0.0', firstRun: false })).toBe(false)
  })

  it('always shows on a first run and after an update', () => {
    expect(resolveStartHidden({ persisted: saved, startHidden: true, version: '1.0.0', firstRun: true })).toBe(false)
    expect(resolveStartHidden({ persisted: saved, startHidden: true, version: '1.1.0', firstRun: false })).toBe(false)
    expect(resolveStartHidden({ persisted: saved, startHidden: true, version: '', firstRun: false })).toBe(false)
  })

  it('reads a damaged state as "show"', () => {
    expect(resolveStartHidden({ persisted: { hidden: 'yes' } as unknown as PersistedHiddenState, startHidden: true, version: '1.0.0', firstRun: false })).toBe(false)
  })
})

describe('hidden mode', () => {
  it('hide takes every window off the screen and the tray icon away (default)', () => {
    const { mode, windows, trayCalls, emitted } = setup()
    expect(mode.hide()).toBe(true)
    expect(windows.every(w => !w.visible && w.skipTaskbar)).toBe(true)
    expect(trayCalls).toEqual([false])
    expect(emitted.at(-1)).toMatchObject({ hidden: true })
  })

  it('keeps the tray icon when the owner chose "show tray icon while hidden"', () => {
    const { mode, trayCalls } = setup({ settings: () => ({ showTrayWhileHidden: true, startHidden: false }) })
    mode.hide()
    expect(trayCalls).toEqual([true])
  })

  it('keeps the tray icon when the hide shortcut could not be registered (the way back must exist)', () => {
    const { mode, trayCalls } = setup({ hideShortcutOk: () => false })
    mode.hide()
    expect(trayCalls).toEqual([true])
  })

  it('writes nothing to disk unless "start hidden" is on, and clears any old state', () => {
    const off = setup()
    off.mode.hide()
    expect(off.persisted).toEqual([null])
    const on = setup({ settings: () => ({ showTrayWhileHidden: false, startHidden: true }) })
    on.mode.hide()
    expect(on.persisted).toEqual([{ hidden: true, since: 5_000_000, version: '1.0.0' }])
  })

  it('show brings the orb back, restores the tray, forgets the saved state and carries the count once', () => {
    const { mode, gate, windows, trayCalls, persisted, emitted, ensureOrbShown } = setup()
    mode.hide()
    gate.request('notification', () => {})
    gate.request('window', () => {}, 'win:77')
    const result = mode.show()
    expect(result?.delivered).toBe(2)
    expect(windows.every(w => w.visible && !w.skipTaskbar)).toBe(true)
    expect(trayCalls.at(-1)).toBe(true)
    expect(persisted.at(-1)).toBeNull()
    expect(ensureOrbShown).toHaveBeenCalledOnce()
    expect(emitted.at(-1)).toEqual({ hidden: false, queued: 2, forgotten: false, unhideSeq: 1 })
  })

  it('tells the shell to close what the gate cannot hide (DevTools of a development run), at hide and at a hidden start', () => {
    const onHidden = vi.fn()
    const a = setup({ onHidden })
    a.mode.hide()
    expect(onHidden).toHaveBeenCalledTimes(1)
    const b = setup({ onHidden })
    b.mode.startHidden(1)
    expect(onHidden).toHaveBeenCalledTimes(2)
  })

  it('shows nothing when it is not hidden', () => {
    const { mode, ensureOrbShown } = setup()
    expect(mode.show()).toBeUndefined()
    expect(ensureOrbShown).not.toHaveBeenCalled()
  })

  it('toggle hides and shows', () => {
    const { mode, windows } = setup()
    mode.toggle()
    expect(windows[0]!.visible).toBe(false)
    mode.toggle()
    expect(windows[0]!.visible).toBe(true)
  })

  it('a long hide ends with one forgotten pulse flag, a short one does not', () => {
    const a = setup()
    a.mode.hide()
    a.advance(13 * 3_600_000)
    a.mode.show()
    expect(a.emitted.at(-1)).toMatchObject({ forgotten: true, unhideSeq: 1 })
    a.mode.hide()
    a.advance(1000)
    a.mode.show()
    expect(a.emitted.at(-1)).toMatchObject({ forgotten: false, unhideSeq: 2 })
  })

  it('the guard limit is a setting', () => {
    const a = setup()
    a.setHours(2)
    a.mode.hide()
    a.advance(3 * 3_600_000)
    a.mode.show()
    expect(a.emitted.at(-1)?.forgotten).toBe(true)
  })

  it('an approval that needs the PIN adds nothing to the queue: the face says nothing and shows nothing while hidden', () => {
    // The kernel keeps the approval pending; the face draws only a state ring (in the hidden window) and no request reaches the gate.
    const { mode, gate } = setup()
    mode.hide()
    expect(gate.state().queued).toBe(0)
    expect(mode.state()).toMatchObject({ hidden: true, queued: 0 })
  })

  it('settings changed while hidden apply at once (tray, saved state)', () => {
    let tray = false
    let start = false
    const { mode, trayCalls, persisted } = setup({ settings: () => ({ showTrayWhileHidden: tray, startHidden: start }) })
    mode.hide()
    tray = true
    start = true
    mode.settingsChanged()
    expect(trayCalls.at(-1)).toBe(true)
    expect(persisted.at(-1)).toMatchObject({ hidden: true })
  })

  it('start hidden hides before the windows exist and keeps the real hiding time', () => {
    const { mode, gate, emitted, advance } = setup()
    mode.startHidden(5_000_000 - 20 * 3_600_000)
    expect(gate.isHidden()).toBe(true)
    // the windows the start-up shows are held and not counted
    gate.request('window', () => {}, 'win:1')
    expect(gate.state().queued).toBe(0)
    advance(START_QUIET_MS + 1)
    gate.request('notification', () => {})
    expect(gate.state().queued).toBe(1)
    mode.show()
    expect(emitted.at(-1)).toMatchObject({ hidden: false, forgotten: true, queued: 1 })
  })
})
