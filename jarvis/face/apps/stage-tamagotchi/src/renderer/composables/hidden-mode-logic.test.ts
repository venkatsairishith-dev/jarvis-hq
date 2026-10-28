import type { HiddenState } from '../../shared/jarvis-look'

import { afterEach, describe, expect, it } from 'vitest'

import { initialLookFromSearch } from '../../shared/jarvis-look'
import { noticeAfterUnhide, silentWhileHidden } from './hidden-mode-logic'
import { pageIsHidden, setHostHidden, spokenPollPaused } from './poll-guard'

const state = (patch: Partial<HiddenState>): HiddenState => ({ hidden: false, queued: 0, forgotten: false, unhideSeq: 0, ...patch })

afterEach(() => setHostHidden(false))

describe('silence while hidden', () => {
  it('nothing makes sound while hidden unless "speak while hidden" is on', () => {
    expect(silentWhileHidden({ hidden: true }, { speakWhileHidden: false })).toBe(true)
    expect(silentWhileHidden({ hidden: true }, { speakWhileHidden: true })).toBe(false)
    expect(silentWhileHidden({ hidden: false }, { speakWhileHidden: false })).toBe(false)
  })
})

describe('what the orb shows after an unhide', () => {
  it('shows nothing while hidden or before any unhide', () => {
    expect(noticeAfterUnhide(undefined, state({ hidden: true, queued: 3 }))).toBeUndefined()
    expect(noticeAfterUnhide(undefined, state({}))).toBeUndefined()
  })
  it('shows the count of what waited, once per unhide', () => {
    const next = state({ queued: 3, unhideSeq: 1 })
    expect(noticeAfterUnhide(state({ hidden: true }), next)).toEqual({ seq: 1, queued: 3, forgotten: false })
    expect(noticeAfterUnhide(next, next)).toBeUndefined()
  })
  it('shows nothing when nothing waited and the hide was short', () => {
    expect(noticeAfterUnhide(state({ hidden: true }), state({ unhideSeq: 1 }))).toBeUndefined()
  })
  it('a long hide gives the single pulse even with nothing waiting', () => {
    expect(noticeAfterUnhide(state({ hidden: true }), state({ unhideSeq: 2, forgotten: true }))).toEqual({ seq: 2, queued: 0, forgotten: true })
  })
})

describe('the polls follow the hidden mode', () => {
  it('every poll stops while hidden', () => {
    expect(pageIsHidden({ hidden: false })).toBe(false)
    setHostHidden(true)
    expect(pageIsHidden({ hidden: false })).toBe(true)
  })
  it('the proactive speaking waits while hidden, unless the owner allowed sound', () => {
    expect(spokenPollPaused({ hidden: false })).toBe(false)
    setHostHidden(true, false)
    expect(spokenPollPaused({ hidden: false })).toBe(true)
    setHostHidden(true, true)
    expect(spokenPollPaused({ hidden: true })).toBe(false)
  })
  it('a window the page cannot see keeps waiting as before', () => {
    expect(spokenPollPaused({ hidden: true })).toBe(true)
  })
})

describe('the look a window starts with', () => {
  it('reads the address the main process gave', () => {
    expect(initialLookFromSearch('?synced-leader=true&jarvis-look=orb')).toBe('orb')
    expect(initialLookFromSearch('?jarvis-look=minimal')).toBe('minimal')
    expect(initialLookFromSearch('?jarvis-look=avatar')).toBe('avatar')
  })
  it('is the avatar for any window without it, or with nonsense', () => {
    expect(initialLookFromSearch('')).toBe('avatar')
    expect(initialLookFromSearch(undefined)).toBe('avatar')
    expect(initialLookFromSearch('?jarvis-look=hologram')).toBe('avatar')
  })
})
