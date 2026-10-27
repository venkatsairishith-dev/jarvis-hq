/**
 * D78: the two global shortcuts of the look (hide / show, and reset position), registered through Electron's `globalShortcut`.
 *
 * Pure: the register / unregister functions are passed in, so the failure handling is tested without Electron. A shortcut can fail for
 * three reasons: the text is not an accelerator (`invalid`), another program holds it (`conflict`: Electron only answers false) or the
 * call threw (`error`). A failure never throws into start-up and never leaves the owner without a way to hide or show: the menu, the
 * voice phrase and (when the hide shortcut failed) the tray icon, which then stays while hidden, are the other doors (hidden-mode.ts).
 */

import type { ShortcutFailure, ShortcutStatus } from '../../shared/jarvis-look'

const MODIFIERS = /^(?:control|ctrl|commandorcontrol|cmdorctrl|alt|option|altgr|shift|super|meta|command|cmd)$/i
const NEEDS_ONE_OF = /^(?:control|ctrl|commandorcontrol|cmdorctrl|alt|option|altgr|super|meta|command|cmd)$/i
const KEY = /^(?:[a-z0-9]|f(?:[1-9]|1\d|2[0-4])|space|tab|backspace|delete|insert|return|enter|up|down|left|right|home|end|pageup|pagedown|escape|esc|plus|[-=[\]\\;',./`])$/i

/**
 * Whether `accelerator` is an Electron accelerator that is safe as a GLOBAL shortcut: at least one of Ctrl, Alt, Super or Command (Shift alone
 * would take a letter away from every other program), then one key.
 */
export function isValidGlobalAccelerator(accelerator: unknown): accelerator is string {
  if (typeof accelerator !== 'string' || accelerator.length > 64)
    return false
  const parts = accelerator.split('+')
  if (parts.length < 2)
    return false
  const key = parts.pop() ?? ''
  if (!KEY.test(key))
    return false
  if (!parts.every(part => MODIFIERS.test(part)))
    return false
  return parts.some(part => NEEDS_ONE_OF.test(part))
}

export interface ShortcutDriver {
  register: (accelerator: string, callback: () => void) => boolean
  unregister: (accelerator: string) => void
}

export interface WantedShortcuts {
  hide: { accelerator: string, onTriggered: () => void }
  reset: { accelerator: string, onTriggered: () => void }
}

type Slot = 'hide' | 'reset'
const SLOTS: readonly Slot[] = ['hide', 'reset']

/**
 * Registers both shortcuts and answers what happened. `previous` is what an earlier call registered: it is released first, and when the
 * new accelerator cannot be taken the old one is put back, so a mistyped change never leaves a working shortcut dead.
 */
export function registerLookShortcuts(driver: ShortcutDriver, wanted: WantedShortcuts, previous?: ShortcutStatus, log?: (message: string) => void): ShortcutStatus {
  const held = new Map<Slot, string>()
  const result = {} as ShortcutStatus

  for (const slot of SLOTS) {
    const old = previous?.[slot]
    if (old?.ok) {
      try {
        driver.unregister(old.accelerator)
      }
      catch {}
    }
  }

  for (const slot of SLOTS) {
    const { accelerator, onTriggered } = wanted[slot]
    let reason: ShortcutFailure | undefined
    if (!isValidGlobalAccelerator(accelerator)) {
      reason = 'invalid'
    }
    else if ([...held.values()].some(taken => taken.toLowerCase() === accelerator.toLowerCase())) {
      reason = 'conflict'
    }
    else {
      try {
        if (driver.register(accelerator, onTriggered))
          held.set(slot, accelerator)
        else
          reason = 'conflict'
      }
      catch {
        reason = 'error'
      }
    }
    if (!reason) {
      result[slot] = { accelerator, ok: true }
      continue
    }
    log?.(`the ${slot} shortcut ${accelerator} is not available (${reason})`)
    // put the earlier working accelerator back, so a bad change does not take the shortcut away
    const old = previous?.[slot]
    if (old?.ok && old.accelerator !== accelerator && ![...held.values()].some(taken => taken.toLowerCase() === old.accelerator.toLowerCase())) {
      try {
        if (driver.register(old.accelerator, onTriggered)) {
          held.set(slot, old.accelerator)
          result[slot] = { accelerator: old.accelerator, ok: true }
          continue
        }
      }
      catch {}
    }
    result[slot] = { accelerator, ok: false, reason }
  }
  return result
}

/** Releases what `registerLookShortcuts` registered. */
export function unregisterLookShortcuts(driver: ShortcutDriver, status: ShortcutStatus | undefined): void {
  for (const slot of SLOTS) {
    const entry = status?.[slot]
    if (entry?.ok) {
      try {
        driver.unregister(entry.accelerator)
      }
      catch {}
    }
  }
}
