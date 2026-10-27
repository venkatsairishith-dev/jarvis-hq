import { describe, expect, it } from 'vitest'

import { DEFAULT_LOOK_SETTINGS } from '../../shared/jarvis-look'
import { describeLookChange, initialLook, looksHaveStage, MAX_FORGOTTEN_HOURS, mergeLookSettings, profileExisted, resolveLookSettings, stepSize, windowKind } from './look-config'

describe('which look a profile starts with', () => {
  it('a new profile gets the orb', () => {
    expect(initialLook(undefined, false)).toBe('orb')
    expect(resolveLookSettings(undefined, false).look).toBe('orb')
  })
  it('an existing profile keeps the avatar until the owner switches', () => {
    expect(initialLook(undefined, true)).toBe('avatar')
    expect(initialLook({}, true)).toBe('avatar')
  })
  it('a saved choice always wins', () => {
    expect(initialLook({ look: 'minimal' }, true)).toBe('minimal')
    expect(initialLook({ look: 'avatar' }, false)).toBe('avatar')
    expect(initialLook({ look: 'orb' }, true)).toBe('orb')
  })
  it('an unknown saved value reads as the profile default', () => {
    expect(initialLook({ look: 'hologram' }, true)).toBe('avatar')
    expect(initialLook({ look: 'hologram' }, false)).toBe('orb')
  })
  it('a profile counts as existing when any sign of an earlier run is there', () => {
    const none = { appConfig: false, appOptions: false, localStorage: false, lookConfig: false }
    expect(profileExisted(none)).toBe(false)
    for (const key of Object.keys(none) as Array<keyof typeof none>)
      expect(profileExisted({ ...none, [key]: true })).toBe(true)
  })
})

describe('resolveLookSettings', () => {
  it('uses the defaults for a new profile: hidden-mode extras all off', () => {
    const s = resolveLookSettings(undefined, false)
    expect(s).toEqual(DEFAULT_LOOK_SETTINGS)
    expect(s.showTrayWhileHidden).toBe(false)
    expect(s.startHidden).toBe(false)
    expect(s.speakWhileHidden).toBe(false)
    expect(s.forgottenHours).toBe(12)
    expect(s.hideShortcut).toBe('Control+Alt+J')
  })
  it('replaces bad values', () => {
    const s = resolveLookSettings({ size: 'XL', hideShortcut: 'J', resetShortcut: 5 as unknown as string, forgottenHours: -4, startHidden: 'true' as unknown as boolean }, false)
    expect(s.size).toBe('M')
    expect(s.hideShortcut).toBe('Control+Alt+J')
    expect(s.resetShortcut).toBe('Control+Alt+Shift+J')
    expect(s.forgottenHours).toBe(12)
    expect(s.startHidden).toBe(false)
  })
  it('keeps good values', () => {
    const s = resolveLookSettings({ size: 'L', hideShortcut: 'Alt+F9', forgottenHours: 0, startHidden: true, showTrayWhileHidden: true, speakWhileHidden: true }, false)
    expect(s).toMatchObject({ size: 'L', hideShortcut: 'Alt+F9', forgottenHours: 0, startHidden: true, showTrayWhileHidden: true, speakWhileHidden: true })
  })
})

describe('mergeLookSettings (mode switching)', () => {
  const base = resolveLookSettings(undefined, false)
  it('switches the look live between all three', () => {
    let s = base
    for (const look of ['avatar', 'minimal', 'orb'] as const) {
      const next = mergeLookSettings(s, { look })
      expect(next.look).toBe(look)
      expect(describeLookChange(s, next).look).toBe(true)
      s = next
    }
  })
  it('ignores a look that does not exist', () => {
    expect(mergeLookSettings(base, { look: 'hologram' }).look).toBe('orb')
  })
  it('ignores unknown and badly typed fields', () => {
    const next = mergeLookSettings(base, { startHidden: 'yes', hideShortcut: 'nope', forgottenHours: 'x', bogus: 1 } as never)
    expect(next).toEqual(base)
  })
  it('clamps the forgotten-hidden limit', () => {
    expect(mergeLookSettings(base, { forgottenHours: 1e6 }).forgottenHours).toBe(MAX_FORGOTTEN_HOURS)
    expect(mergeLookSettings(base, { forgottenHours: 0 }).forgottenHours).toBe(0)
  })
  it('does not throw on a missing patch', () => {
    expect(mergeLookSettings(base, undefined)).toBe(base)
  })
  it('describes what a change touches', () => {
    const next = mergeLookSettings(base, { size: 'L', hideShortcut: 'Alt+F9', showTrayWhileHidden: true })
    expect(describeLookChange(base, next)).toMatchObject({ look: false, size: true, shortcuts: true, hiddenSettings: true, any: true })
    expect(describeLookChange(base, base).any).toBe(false)
  })
})

describe('what a look means for the window', () => {
  it('only the avatar has a stage', () => {
    expect(looksHaveStage('avatar')).toBe(true)
    expect(looksHaveStage('orb')).toBe(false)
    expect(looksHaveStage('minimal')).toBe(false)
    expect(windowKind('avatar')).toBe('stage')
    expect(windowKind('orb')).toBe('zone')
    expect(windowKind('minimal')).toBe('zone')
  })
  it('sizes step and stop at the ends', () => {
    expect(stepSize('M', 1)).toBe('L')
    expect(stepSize('L', 1)).toBe('L')
    expect(stepSize('S', -1)).toBe('S')
  })
})
