/**
 * D78: the face's "Look" (Orb, Avatar or Minimal bar), its size, and the hidden mode: the types and tables that the main process and the
 * renderer share. Pure: no electron, no DOM, no store. Presentation only: nothing here touches a security rule, the network lock or a policy.
 */

export interface Rect { x: number, y: number, width: number, height: number }

/** What JARVIS looks like. `orb` is the default for a NEW profile; a profile that already exists keeps `avatar` until the owner switches. */
export type Look = 'orb' | 'avatar' | 'minimal'
export const LOOKS: readonly Look[] = ['orb', 'avatar', 'minimal']
export const LOOK_LABELS: Record<Look, string> = { orb: 'Orb', avatar: 'Avatar', minimal: 'Minimal bar' }

export type LookSize = 'S' | 'M' | 'L'
export const LOOK_SIZES: readonly LookSize[] = ['S', 'M', 'L']

/** The orb zone (the square the orb and its glow live in) and the minimal bar, per size, in CSS pixels. */
export const ORB_ZONE: Record<LookSize, number> = { S: 96, M: 128, L: 176 }
/** The orb itself inside its zone; the rest is room for the glow ring, the progress arc and the attention ring. */
export const ORB_DISC: Record<LookSize, number> = { S: 64, M: 88, L: 120 }
export const BAR_SIZE: Record<LookSize, { width: number, height: number }> = {
  S: { width: 300, height: 36 },
  M: { width: 380, height: 44 },
  L: { width: 480, height: 56 },
}
/** The "what I am doing" pill (D72) under the orb, and the small right-click menu, as the window must make room for them. */
export const ORB_PILL = { width: 264, height: 34, gap: 4 } as const
export const ORB_MENU = { width: 216, height: 232 } as const
/** The card beside the orb (ask form, timeline, preview): the width the panels already use, and at most this tall. */
export const ORB_CARD = { width: 460, height: 600, gap: 8 } as const
/** The space kept between a snapped orb and the screen edge. */
export const SNAP_MARGIN = 12
/** Within this distance of an edge a drag snaps to it. */
export const SNAP_THRESHOLD = 28

export type Anchor = 'right' | 'left' | 'below' | 'above'

export function normalizeLook(raw: unknown, fallback: Look = 'orb'): Look {
  return LOOKS.includes(raw as Look) ? raw as Look : fallback
}

export function normalizeLookSize(raw: unknown, fallback: LookSize = 'M'): LookSize {
  return LOOK_SIZES.includes(raw as LookSize) ? raw as LookSize : fallback
}

/** The size, in CSS pixels, of the zone that the look occupies on the screen. */
export function zoneSize(look: Look, size: LookSize): { width: number, height: number } {
  if (look === 'minimal')
    return { ...BAR_SIZE[size] }
  return { width: ORB_ZONE[size], height: ORB_ZONE[size] }
}

/** The settings the owner can change, as the renderer sees them. */
export interface LookSettings {
  look: Look
  size: LookSize
  alwaysOnTop: boolean
  /** Electron accelerator of the global hide / show shortcut. */
  hideShortcut: string
  /** Electron accelerator of the global "reset position" shortcut. */
  resetShortcut: string
  showTrayWhileHidden: boolean
  startHidden: boolean
  speakWhileHidden: boolean
  /** Hours of hiding after which the next unhide shows one quiet pulse. 0 turns the guard off. */
  forgottenHours: number
}

export const DEFAULT_HIDE_SHORTCUT = 'Control+Alt+J'
export const DEFAULT_RESET_SHORTCUT = 'Control+Alt+Shift+J'
export const DEFAULT_FORGOTTEN_HOURS = 12

export const DEFAULT_LOOK_SETTINGS: LookSettings = {
  look: 'orb',
  size: 'M',
  alwaysOnTop: true,
  hideShortcut: DEFAULT_HIDE_SHORTCUT,
  resetShortcut: DEFAULT_RESET_SHORTCUT,
  showTrayWhileHidden: false,
  startHidden: false,
  speakWhileHidden: false,
  forgottenHours: DEFAULT_FORGOTTEN_HOURS,
}

/** What the main process tells the renderer about the hidden mode. Counts only: no content of anything that waits. */
export interface HiddenState {
  hidden: boolean
  /** Requests that arrived while hidden and were held back. Shown to the owner after he unhides. */
  queued: number
  /** Set once by an unhide after a long time hidden: the orb shows one quiet pulse and nothing else. */
  forgotten: boolean
  /** The unhide that carried `queued` and `forgotten`; the renderer shows the count once per unhide. */
  unhideSeq: number
}

/** What the shortcuts did at the last registration, so the menu can say when one is not available. */
export interface ShortcutStatus {
  hide: { accelerator: string, ok: boolean, reason?: ShortcutFailure }
  reset: { accelerator: string, ok: boolean, reason?: ShortcutFailure }
}

export type ShortcutFailure = 'invalid' | 'conflict' | 'error'

const HIDE_YOURSELF = /^(?:please\s+)?(?:hide|go\s+hidden)\s+yourself(?:\s+please)?$/i
const SHOW_YOURSELF = /^(?:please\s+)?(?:show|unhide)\s+yourself(?:\s+please)?$/i
/** Short forms count only after the name ("Jarvis, hide"), so an ordinary sentence that starts with "show" or "hide" is never taken. */
const HIDE_SHORT = /^(?:please\s+)?(?:hide|go\s+hidden)(?:\s+now)?(?:\s+please)?$/i
const SHOW_SHORT = /^(?:please\s+)?(?:show|unhide|come\s+back|appear)(?:\s+now)?(?:\s+please)?$/i
const FILLER = /^(?:(?:hey|ok|okay|hi)\s+jarvis[,\s]+|jarvis[,\s]+)+/i

/**
 * "hide yourself" / "show yourself" (and, after the name, "hide" / "show") as the owner says or types them; `null` for anything else.
 * WHOLE phrases only. Used by the face before any model is asked, like the other quick commands (D60). It only changes what is DRAWN:
 * a hide is never a permission and never approves anything (voice cannot approve: unchanged).
 */
export function parseVisibilityCommand(text: string | undefined | null): 'hide' | 'show' | null {
  const clean = String(text ?? '').replace(/[.!?,]+$/g, '').replace(/\s+/g, ' ').trim()
  const body = clean.replace(FILLER, '').replace(/[.!?,]+$/g, '').trim()
  const named = body.length !== clean.length
  if (!body || body.length > 40)
    return null
  if (HIDE_YOURSELF.test(body) || (named && HIDE_SHORT.test(body)))
    return 'hide'
  if (SHOW_YOURSELF.test(body) || (named && SHOW_SHORT.test(body)))
    return 'show'
  return null
}
