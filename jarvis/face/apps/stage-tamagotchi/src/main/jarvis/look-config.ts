/**
 * D78: the saved settings of the look (Orb / Avatar / Minimal bar), as pure functions: which look a profile starts with, how a change
 * from the owner is checked and merged, and what a change means for the windows. No electron, no file access (the service reads and writes).
 *
 * "Existing profiles keep what they have until the owner switches": a profile that ran before this change has the avatar, so the look
 * it gets is `avatar`; only a profile that never ran gets `orb`. The decision is written down the first time, so it never flips later.
 */

import type { Look, LookSettings, LookSize } from '../../shared/jarvis-look'
import type { PlacementStore } from '../../shared/orb-layout'

import { DEFAULT_LOOK_SETTINGS, LOOKS, normalizeLook, normalizeLookSize } from '../../shared/jarvis-look'
import { isValidGlobalAccelerator } from './shortcuts'

/** What is on disk. Every field is optional: a file from an older build, or a damaged one, still reads. */
export interface StoredLook {
  look?: string
  size?: string
  hideShortcut?: string
  resetShortcut?: string
  showTrayWhileHidden?: boolean
  startHidden?: boolean
  speakWhileHidden?: boolean
  forgottenHours?: number
  placements?: PlacementStore
}

export const MAX_FORGOTTEN_HOURS = 24 * 14

/** Whether this PC already has a face profile (it ran before this build). */
export function profileExisted(signs: { appConfig: boolean, appOptions: boolean, localStorage: boolean, lookConfig: boolean }): boolean {
  return signs.lookConfig || signs.appConfig || signs.appOptions || signs.localStorage
}

/**
 * The look at start-up. A look the owner (or an earlier start) saved wins. Without one: `orb` for a new profile, `avatar` for a profile that
 * existed before the orb did, so nothing changes for him until he switches.
 */
export function initialLook(stored: StoredLook | undefined, existingProfile: boolean): Look {
  if (stored?.look && LOOKS.includes(stored.look as Look))
    return stored.look as Look
  return existingProfile ? 'avatar' : 'orb'
}

/** The settings with every value checked; a bad value is replaced by the default (or by `fallbackLook` for the look). */
export function resolveLookSettings(stored: StoredLook | undefined, existingProfile: boolean): LookSettings {
  const d = DEFAULT_LOOK_SETTINGS
  const hours = Number(stored?.forgottenHours)
  return {
    look: initialLook(stored, existingProfile),
    size: normalizeLookSize(stored?.size, d.size),
    hideShortcut: isValidGlobalAccelerator(stored?.hideShortcut) ? stored.hideShortcut : d.hideShortcut,
    resetShortcut: isValidGlobalAccelerator(stored?.resetShortcut) ? stored.resetShortcut : d.resetShortcut,
    showTrayWhileHidden: stored?.showTrayWhileHidden === true,
    startHidden: stored?.startHidden === true,
    speakWhileHidden: stored?.speakWhileHidden === true,
    forgottenHours: Number.isFinite(hours) && hours >= 0 ? Math.min(MAX_FORGOTTEN_HOURS, Math.round(hours)) : d.forgottenHours,
  }
}

/** Merges a change from the renderer into the settings. Unknown or bad fields are ignored; nothing here can throw. */
export function mergeLookSettings(current: LookSettings, patch: Partial<Record<keyof LookSettings, unknown>> | undefined): LookSettings {
  if (!patch || typeof patch !== 'object')
    return current
  const next = { ...current }
  if (patch.look !== undefined)
    next.look = normalizeLook(patch.look, current.look)
  if (patch.size !== undefined)
    next.size = normalizeLookSize(patch.size, current.size)
  for (const key of ['showTrayWhileHidden', 'startHidden', 'speakWhileHidden'] as const) {
    if (typeof patch[key] === 'boolean')
      next[key] = patch[key] as boolean
  }
  for (const key of ['hideShortcut', 'resetShortcut'] as const) {
    if (isValidGlobalAccelerator(patch[key]))
      next[key] = patch[key] as string
  }
  if (patch.forgottenHours !== undefined) {
    const hours = Number(patch.forgottenHours)
    if (Number.isFinite(hours) && hours >= 0)
      next.forgottenHours = Math.min(MAX_FORGOTTEN_HOURS, Math.round(hours))
  }
  return next
}

export interface LookChange {
  /** The window must switch between the avatar and the orb or bar (live, no restart). */
  look: boolean
  /** The zone has another size. */
  size: boolean
  shortcuts: boolean
  /** A hidden-mode setting changed (tray, start hidden). */
  hiddenSettings: boolean
  any: boolean
}

export function describeLookChange(prev: LookSettings, next: LookSettings): LookChange {
  const look = prev.look !== next.look
  const size = prev.size !== next.size
  const shortcuts = prev.hideShortcut !== next.hideShortcut || prev.resetShortcut !== next.resetShortcut
  const hiddenSettings = prev.showTrayWhileHidden !== next.showTrayWhileHidden || prev.startHidden !== next.startHidden
  return { look, size, shortcuts, hiddenSettings, any: look || size || shortcuts || hiddenSettings || prev.speakWhileHidden !== next.speakWhileHidden || prev.forgottenHours !== next.forgottenHours }
}

/** The avatar draws a stage (Live2D, VRM, MMD); the orb and the bar never do, and load none of its assets. */
export function looksHaveStage(look: Look): boolean {
  return look === 'avatar'
}

/** What the main window is while the look is on: the avatar window of the owner, or a small zone window that holds the orb or the bar. */
export function windowKind(look: Look): 'stage' | 'zone' {
  return looksHaveStage(look) ? 'stage' : 'zone'
}

/** The next size when the owner presses "bigger" or "smaller". */
export function stepSize(size: LookSize, direction: 1 | -1): LookSize {
  const order: LookSize[] = ['S', 'M', 'L']
  return order[Math.min(2, Math.max(0, order.indexOf(size) + direction))]!
}
