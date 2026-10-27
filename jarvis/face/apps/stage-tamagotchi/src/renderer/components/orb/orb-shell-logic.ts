/**
 * D78: the decisions of the orb shell that need no DOM: when the pill shows, what the window must make room for, and which clicks are
 * click-through. Pure, so they are tested directly (orb-shell-logic.test.ts). No avatar, no store, no WebGL.
 */

import type { OrbLayoutRequest } from '../../../shared/jarvis-look'
import type { OrbState } from './orb-state'

import { ORB_CARD } from '../../../shared/jarvis-look'

/** The panels that open beside the orb: the ask form, the timeline, the preview (their own root classes). */
export const CARD_SELECTOR = '.ask, .tl, .pv'

/**
 * The text of the pill (D72 "what I am doing"): the task's line while JARVIS works or waits for the owner, else a short notice from the
 * orb itself ("Microphone is off"), else nothing. English, short, no private text.
 */
export function pillText(state: OrbState, line: string | undefined, notice: string | undefined): string {
  if (notice && notice.trim())
    return notice.trim()
  if ((state === 'working' || state === 'needs-you') && line && line.trim())
    return line.trim()
  return ''
}

export interface OrbRequestInput {
  state: OrbState
  line?: string
  notice?: string
  /** The size a panel beside the orb wants, or null when none is open. */
  card: { width: number, height: number } | null
  menu: boolean
}

/** What the renderer asks the main process to make room for. */
export function orbRequest(input: OrbRequestInput): OrbLayoutRequest {
  return {
    pill: pillText(input.state, input.line, input.notice) !== '',
    card: input.card,
    menu: input.menu,
  }
}

/** True when two requests ask for the same thing, so the main process is not asked again. */
export function sameRequest(a: OrbLayoutRequest | undefined, b: OrbLayoutRequest): boolean {
  return !!a && a.pill === b.pill && a.menu === b.menu
    && ((a.card === null && b.card === null) || (!!a.card && !!b.card && a.card.width === b.card.width && a.card.height === b.card.height))
}

/** The part of a document the card check needs. */
export interface CardRoot {
  querySelector: (selector: string) => unknown
}

/** The card that is open now (its size is the panels' own width and the height of a window), or null. */
export function cardFromDom(root: CardRoot | undefined): { width: number, height: number } | null {
  if (!root)
    return null
  return root.querySelector(CARD_SELECTOR) ? { width: ORB_CARD.width, height: ORB_CARD.height } : null
}

/** An ask form is open: the orb is in its amber "needs you" state until it is answered. */
export function askOpenFromDom(root: CardRoot | undefined): boolean {
  return !!root?.querySelector('.ask')
}

/**
 * Whether the window passes the mouse through at this point. The window is bigger than the orb while a card, the pill or the menu is open,
 * and the empty parts of it must not catch clicks meant for the apps behind. Over any `data-orb-hit` element the window takes the mouse.
 */
export function shouldIgnoreMouse(overHit: boolean, windowIsBiggerThanZone: boolean): boolean {
  return windowIsBiggerThanZone && !overHit
}
