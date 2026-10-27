/**
 * D78: the pure rules of the hidden mode in the page (no store, no DOM), tested directly in hidden-mode-logic.test.ts.
 */

import type { HiddenState, LookSettings } from '../../shared/jarvis-look'

/** Whether the page must stay silent: hidden, and the owner did not allow sound while hidden. Pure. */
export function silentWhileHidden(hidden: Pick<HiddenState, 'hidden'>, settings: Pick<LookSettings, 'speakWhileHidden'>): boolean {
  return hidden.hidden && !settings.speakWhileHidden
}

/** What the orb shows after an unhide: a count (when something waited) and a pulse (after a long hide). `seq` is the unhide it belongs to. */
export interface UnhideNotice {
  seq: number
  queued: number
  forgotten: boolean
}

export function noticeAfterUnhide(previous: HiddenState | undefined, next: HiddenState): UnhideNotice | undefined {
  if (next.hidden || next.unhideSeq === 0 || next.unhideSeq === previous?.unhideSeq)
    return undefined
  if (next.queued <= 0 && !next.forgotten)
    return undefined
  return { seq: next.unhideSeq, queued: next.queued, forgotten: next.forgotten }
}
