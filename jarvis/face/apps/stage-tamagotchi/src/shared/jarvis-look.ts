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
export const ORB_PILL = { width: 264, height: 48, gap: 4 } as const
export const ORB_MENU = { width: 232, height: 404 } as const
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

/**
 * The look the page starts with, from the `jarvis-look` query the main process puts on the main window's address. A window without it (settings,
 * chat) is not the orb's window: it reads `avatar` until the store has the real answer. Pure, so it is tested.
 */
export function initialLookFromSearch(search: string | undefined): Look {
  try {
    const raw = new URLSearchParams(search ?? '').get('jarvis-look')
    return raw ? normalizeLook(raw, 'avatar') : 'avatar'
  }
  catch {
    return 'avatar'
  }
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
  hideShortcut: DEFAULT_HIDE_SHORTCUT,
  resetShortcut: DEFAULT_RESET_SHORTCUT,
  showTrayWhileHidden: false,
  startHidden: false,
  speakWhileHidden: false,
  forgottenHours: DEFAULT_FORGOTTEN_HOURS,
}

/** What the renderer wants next to the orb: the pill, a card of some size, the menu. The main process makes the window big enough. */
export interface OrbLayoutRequest {
  pill: boolean
  card: { width: number, height: number } | null
  menu: boolean
}

/** Where everything is, as the main process worked it out: `window` is on the screen, the other rectangles are inside the window. All `null` for the avatar look. */
export interface OrbLayoutSnapshot {
  look: Look
  size: LookSize
  window: Rect | null
  zone: Rect | null
  pill: Rect | null
  card: Rect | null
  cardSide: Anchor | null
  menu: Rect | null
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
