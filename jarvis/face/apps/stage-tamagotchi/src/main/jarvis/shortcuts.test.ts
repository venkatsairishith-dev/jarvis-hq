import { describe, expect, it, vi } from 'vitest'

import { isValidGlobalAccelerator, registerLookShortcuts, unregisterLookShortcuts } from './shortcuts'

function driver(taken: string[] = [], throwOn: string[] = []) {
  const held = new Set<string>()
  return {
    held,
    register: vi.fn((accelerator: string, _cb: () => void) => {
      if (throwOn.includes(accelerator))
        throw new Error('boom')
      if (taken.includes(accelerator) || held.has(accelerator))
        return false
      held.add(accelerator)
      return true
    }),
    unregister: vi.fn((accelerator: string) => { held.delete(accelerator) }),
  }
}

function wanted(hide = 'Control+Alt+J', reset = 'Control+Alt+Shift+J') {
  return {
    hide: { accelerator: hide, onTriggered: () => {} },
    reset: { accelerator: reset, onTriggered: () => {} },
  }
}

describe('isValidGlobalAccelerator', () => {
  it.each(['Control+Alt+J', 'Ctrl+Alt+Shift+J', 'CommandOrControl+Alt+F12', 'Alt+Space', 'Super+Home'])('accepts %s', (a) => {
    expect(isValidGlobalAccelerator(a)).toBe(true)
  })
  it.each(['J', 'Shift+J', 'Control+', 'Control+Alt', 'Foo+J', '', 'Control+Alt+JJ', 'Control+Alt+F25', 12, undefined])('refuses %s', (a) => {
    expect(isValidGlobalAccelerator(a)).toBe(false)
  })
})

describe('registerLookShortcuts', () => {
  it('registers both', () => {
    const d = driver()
    const status = registerLookShortcuts(d, wanted())
    expect(status.hide).toEqual({ accelerator: 'Control+Alt+J', ok: true })
    expect(status.reset).toEqual({ accelerator: 'Control+Alt+Shift+J', ok: true })
  })

  it('reports a conflict (another program holds it) without throwing', () => {
    const d = driver(['Control+Alt+J'])
    const log = vi.fn()
    const status = registerLookShortcuts(d, wanted(), undefined, log)
    expect(status.hide).toEqual({ accelerator: 'Control+Alt+J', ok: false, reason: 'conflict' })
    expect(status.reset.ok).toBe(true)
    expect(log).toHaveBeenCalled()
  })

  it('reports an invalid accelerator and does not call the driver for it', () => {
    const d = driver()
    const status = registerLookShortcuts(d, wanted('J'))
    expect(status.hide).toEqual({ accelerator: 'J', ok: false, reason: 'invalid' })
    expect(d.register).toHaveBeenCalledTimes(1)
  })

  it('survives a driver that throws', () => {
    const d = driver([], ['Control+Alt+J'])
    const status = registerLookShortcuts(d, wanted())
    expect(status.hide).toMatchObject({ ok: false, reason: 'error' })
    expect(status.reset.ok).toBe(true)
  })

  it('refuses the same accelerator for both', () => {
    const d = driver()
    const status = registerLookShortcuts(d, wanted('Control+Alt+J', 'control+alt+j'))
    expect(status.hide.ok).toBe(true)
    expect(status.reset).toMatchObject({ ok: false, reason: 'conflict' })
  })

  it('a change that fails puts the earlier working shortcut back', () => {
    const d = driver()
    const first = registerLookShortcuts(d, wanted())
    d.register.mockClear()
    const second = registerLookShortcuts({ ...d, register: vi.fn((a: string) => a === 'Control+Alt+K' ? false : d.register(a, () => {})) }, wanted('Control+Alt+K'), first)
    expect(second.hide).toEqual({ accelerator: 'Control+Alt+J', ok: true })
    expect(d.held.has('Control+Alt+J')).toBe(true)
  })

  it('a change that works releases the old accelerator', () => {
    const d = driver()
    const first = registerLookShortcuts(d, wanted())
    const second = registerLookShortcuts(d, wanted('Control+Alt+K'), first)
    expect(second.hide).toEqual({ accelerator: 'Control+Alt+K', ok: true })
    expect(d.held.has('Control+Alt+J')).toBe(false)
  })

  it('unregister releases only what was registered', () => {
    const d = driver(['Control+Alt+J'])
    const status = registerLookShortcuts(d, wanted())
    unregisterLookShortcuts(d, status)
    expect(d.unregister).toHaveBeenCalledWith('Control+Alt+Shift+J')
    expect(d.unregister).not.toHaveBeenCalledWith('Control+Alt+J')
    expect(d.held.size).toBe(0)
  })
})
