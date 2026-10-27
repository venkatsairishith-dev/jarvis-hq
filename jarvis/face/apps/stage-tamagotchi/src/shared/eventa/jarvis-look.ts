/**
 * D78: the messages between the main process (look controller, window gate, hidden mode) and the windows. Presentation only. A hidden-mode
 * message carries a flag and counts, never the content of anything that waits.
 */

import type { HiddenState, LookSettings, OrbLayoutRequest, OrbLayoutSnapshot, ShortcutStatus } from '../jarvis-look'

import { defineEventa, defineInvokeEventa } from '@moeru/eventa'

/** Everything a window needs to draw the look and the hidden-mode choices. */
export interface JarvisLookSnapshot {
  settings: LookSettings
  layout: OrbLayoutSnapshot
  shortcuts: ShortcutStatus
  hidden: HiddenState
}

export const electronJarvisLookGet = defineInvokeEventa<JarvisLookSnapshot>('eventa:invoke:electron:jarvis-look:get')
export const electronJarvisLookUpdate = defineInvokeEventa<JarvisLookSnapshot, Partial<Record<keyof LookSettings, unknown>>>('eventa:invoke:electron:jarvis-look:update')
export const electronJarvisLookChanged = defineEventa<JarvisLookSnapshot>('eventa:event:electron:jarvis-look:changed')

/** What is open next to the orb (pill, card, menu): the main process makes the window big enough and answers where everything is. */
export const electronJarvisOrbRequest = defineInvokeEventa<OrbLayoutSnapshot, OrbLayoutRequest>('eventa:invoke:electron:jarvis-look:orb-request')
/** A drag of the orb or the bar. The main process reads the cursor itself, so the page needs no screen coordinates. */
export const electronJarvisOrbDrag = defineInvokeEventa<void, { phase: 'start' | 'move' | 'end' }>('eventa:invoke:electron:jarvis-look:orb-drag')
export const electronJarvisOrbNudge = defineInvokeEventa<void, { direction: 'left' | 'right' | 'up' | 'down' }>('eventa:invoke:electron:jarvis-look:orb-nudge')
export const electronJarvisOrbReset = defineInvokeEventa<void>('eventa:invoke:electron:jarvis-look:orb-reset')

/** Hide or show JARVIS (the orb menu, the voice phrase, the settings page). The global shortcut is handled in the main process. */
export const electronJarvisHiddenSet = defineInvokeEventa<HiddenState, { hidden: boolean }>('eventa:invoke:electron:jarvis-look:hidden-set')
