import { describe, expect, it } from 'vitest'

import { askOpenFromDom, CARD_SELECTOR, cardFromDom, orbRequest, pillText, sameRequest, shouldIgnoreMouse } from './orb-shell-logic'

describe('pillText', () => {
  it('shows the task line while working or waiting for the owner', () => {
    expect(pillText('working', 'Step 2 of 3: reading the window', '')).toBe('Step 2 of 3: reading the window')
    expect(pillText('needs-you', 'Waiting for your approval', undefined)).toBe('Waiting for your approval')
  })
  it('shows nothing for the calm states, even with a stale line', () => {
    for (const state of ['idle', 'listening', 'thinking', 'speaking', 'error'] as const)
      expect(pillText(state, 'Step 1 of 2', '')).toBe('')
  })
  it('a notice of the orb itself comes first', () => {
    expect(pillText('working', 'Step 1 of 2', 'Microphone is off.')).toBe('Microphone is off.')
    expect(pillText('idle', '', '  Set up hearing in Settings first.  ')).toBe('Set up hearing in Settings first.')
  })
  it('keeps Telugu and Hindi as they are', () => {
    expect(pillText('working', 'నోట్‌ప్యాడ్ తెరుస్తున్నాను', '')).toBe('నోట్‌ప్యాడ్ తెరుస్తున్నాను')
    expect(pillText('working', 'नोटपैड खोल रहा हूँ', '')).toBe('नोटपैड खोल रहा हूँ')
  })
})

describe('orbRequest', () => {
  it('asks for room only for what is open', () => {
    expect(orbRequest({ state: 'idle', card: null, menu: false })).toEqual({ pill: false, card: null, menu: false })
    expect(orbRequest({ state: 'working', line: 'On it', card: null, menu: false }).pill).toBe(true)
    expect(orbRequest({ state: 'idle', card: { width: 460, height: 600 }, menu: true })).toEqual({ pill: false, card: { width: 460, height: 600 }, menu: true })
  })
  it('knows when a request is the same as the last one', () => {
    const a = orbRequest({ state: 'working', line: 'On it', card: { width: 460, height: 600 }, menu: false })
    expect(sameRequest(a, { ...a })).toBe(true)
    expect(sameRequest(a, { ...a, pill: false })).toBe(false)
    expect(sameRequest(a, { ...a, card: null })).toBe(false)
    expect(sameRequest(undefined, a)).toBe(false)
    expect(sameRequest({ pill: false, card: null, menu: false }, { pill: false, card: null, menu: false })).toBe(true)
  })
})

describe('cards in the DOM', () => {
  const root = (present: string[]) => ({ querySelector: (selector: string) => selector.split(',').map(part => part.trim()).some(part => present.includes(part)) ? {} : null })
  it('finds the three panels by their own classes', () => {
    expect(CARD_SELECTOR).toBe('.ask, .tl, .pv')
    for (const klass of ['.ask', '.tl', '.pv'])
      expect(cardFromDom(root([klass]))).toEqual({ width: 460, height: 600 })
    expect(cardFromDom(root([]))).toBeNull()
    expect(cardFromDom(undefined)).toBeNull()
  })
  it('an open ask form is a question for the owner', () => {
    expect(askOpenFromDom(root(['.ask']))).toBe(true)
    expect(askOpenFromDom(root(['.tl']))).toBe(false)
  })
})

describe('click-through', () => {
  it('passes the mouse through only when the window is bigger than the orb and the pointer is not over a hit target', () => {
    expect(shouldIgnoreMouse(false, true)).toBe(true)
    expect(shouldIgnoreMouse(true, true)).toBe(false)
    expect(shouldIgnoreMouse(false, false)).toBe(false)
    expect(shouldIgnoreMouse(true, false)).toBe(false)
  })
})
