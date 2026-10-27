/**
 * D78: the controller of the look. It owns the settings (Orb / Avatar / Minimal bar, size, always on top, hidden-mode choices), the place
 * of the orb on the screen, and the window around it. The Electron window, the screens, the cursor and the disk are passed in (ports), so
 * the whole behaviour is tested with stand-ins (look-controller.test.ts). `look-service.ts` connects it to the real ones.
 *
 * Presentation and shell only: nothing here reads a secret, opens a socket or changes a policy.
 */

import type { Look, LookSettings, OrbLayoutRequest, OrbLayoutSnapshot, Rect } from '../../shared/jarvis-look'
import type { Direction, DisplayInfo, PlacementStore } from '../../shared/orb-layout'
import type { LookChange, StoredLook } from './look-config'

import { ORB_CARD, zoneSize } from '../../shared/jarvis-look'
import {
  clampRect,
  computeOrbWindow,
  defaultRect,
  displayFor,
  EMPTY_PLACEMENTS,
  fromSpot,
  moveByKey,
  pruneStore,
  recordPlacement,
  reflowAfterDisplayChange,
  restorePlacement,
  snapRect,
  toSpot,
} from '../../shared/orb-layout'
import { describeLookChange, mergeLookSettings, resolveLookSettings, windowKind } from './look-config'

/** The part of the main window that the controller drives. */
export interface WindowPort {
  isDestroyed: () => boolean
  getBounds: () => Rect
  setBounds: (bounds: Rect) => void
  setResizable: (resizable: boolean) => void
}

export interface LookPorts {
  window: () => WindowPort | undefined
  displays: () => DisplayInfo[]
  primaryId: () => number | string
  /** The cursor on the screen, in the same units as the displays. */
  cursor: () => { x: number, y: number }
  load: () => StoredLook | undefined
  save: (stored: StoredLook) => void
  /** True when a profile existed before the orb did. */
  existingProfile: boolean
  /** The avatar window's own bounds (saved by the owner, or the default), for the switch back to the avatar. */
  avatarBounds: () => Rect
  /** Tells every window about a new snapshot. */
  emit: (snapshot: OrbLayoutSnapshot) => void
  log?: (message: string) => void
}

export interface LookController {
  settings: () => LookSettings
  layout: () => OrbLayoutSnapshot
  /** The bounds the main window is created with. */
  initialBounds: () => Rect
  /** The owner changed settings (or the hidden-mode controller did). Returns what changed. */
  update: (patch: Partial<Record<keyof LookSettings, unknown>>) => LookChange
  /** The renderer says what is open next to the orb. */
  request: (request: OrbLayoutRequest) => OrbLayoutSnapshot
  /** `press`: the button went down on the orb (the cursor and the place at that moment are the start of a possible drag); `start`: the press became a drag. */
  drag: (phase: 'press' | 'start' | 'move' | 'end') => void
  nudge: (direction: Direction) => void
  resetPosition: () => void
  /** Displays were added, removed or changed (size, DPI, taskbar). */
  displaysChanged: (previous: DisplayInfo[]) => void
  /** A hidden-mode setting or the first run changed what must be written. */
  persist: () => void
}

const NO_REQUEST: OrbLayoutRequest = { pill: false, card: null, menu: false }

export function createLookController(ports: LookPorts): LookController {
  const stored = ports.load()
  let settings = resolveLookSettings(stored, ports.existingProfile)
  let placements: PlacementStore = pruneStore(stored?.placements, ports.displays())
  let request: OrbLayoutRequest = { ...NO_REQUEST }
  let zone: Rect = initialZone()
  let snapshot: OrbLayoutSnapshot = buildSnapshot()
  let dragging: { cursor: { x: number, y: number }, zone: Rect } | undefined

  function sizeOfZone() {
    return zoneSize(settings.look, settings.size)
  }

  function initialZone(): Rect {
    const size = sizeOfZone()
    return restorePlacement(placements, size, ports.displays(), ports.primaryId())
  }

  function workAreaFor(rect: Rect): Rect {
    const displays = ports.displays()
    return displayFor(rect, displays)?.workArea ?? displays[0]?.workArea ?? rect
  }

  function buildSnapshot(): OrbLayoutSnapshot {
    if (windowKind(settings.look) === 'stage') {
      return { look: settings.look, size: settings.size, window: null, zone: null, pill: null, card: null, cardSide: null, menu: null }
    }
    const workArea = workAreaFor(zone)
    const layout = computeOrbWindow({ zone, look: settings.look, pill: request.pill, card: request.card, menu: request.menu, workArea })
    return { look: settings.look, size: settings.size, window: layout.window, zone: layout.zone, pill: layout.pill, card: layout.card, cardSide: layout.cardSide, menu: layout.menu }
  }

  function save() {
    ports.save({ ...settings, placements })
  }

  /** What the windows read from a snapshot: the place of everything INSIDE the window. A plain move of the window does not change it. */
  const relativeKey = (value: OrbLayoutSnapshot) => JSON.stringify([value.look, value.size, value.zone, value.pill, value.card, value.cardSide, value.menu])
  let lastRelative = ''

  /** Puts the window where the layout says; tells the windows only when something inside it moved (a drag sends no message at all). */
  function apply(options: { quiet?: boolean } = {}) {
    snapshot = buildSnapshot()
    const window = ports.window()
    if (window && !window.isDestroyed() && snapshot.window) {
      // always the whole rectangle: setting the position alone lets Windows grow a window at a scale above 100 %
      window.setBounds(snapshot.window)
    }
    const key = relativeKey(snapshot)
    if (options.quiet && key === lastRelative)
      return
    lastRelative = key
    ports.emit(snapshot)
  }

  function remember() {
    placements = recordPlacement(placements, zone, ports.displays())
    save()
  }

  return {
    settings: () => settings,
    layout: () => snapshot,
    initialBounds: () => snapshot.window ?? ports.avatarBounds(),
    persist: save,

    update(patch) {
      const next = mergeLookSettings(settings, patch)
      const change = describeLookChange(settings, next)
      if (!change.any)
        return change
      const previousLook: Look = settings.look
      settings = next
      const window = ports.window()
      if (change.look || change.size) {
        if (windowKind(next.look) === 'stage') {
          // back to the avatar: its own bounds, resizable as before
          if (window && !window.isDestroyed()) {
            window.setResizable(true)
            window.setBounds(clampRect(ports.avatarBounds(), workAreaFor(ports.avatarBounds())))
          }
          request = { ...NO_REQUEST }
          snapshot = buildSnapshot()
          ports.emit(snapshot)
        }
        else {
          // the orb or the bar: the same relative place on the same display, so a corner stays a corner when the size or the look changes
          const size = sizeOfZone()
          if (windowKind(previousLook) === 'stage') {
            zone = restorePlacement(placements, size, ports.displays(), ports.primaryId())
          }
          else {
            const area = workAreaFor(zone)
            zone = fromSpot(toSpot(zone, area), size, area)
          }
          request = { ...NO_REQUEST }
          window?.setResizable(false)
          remember()
          apply()
        }
      }
      save()
      return change
    },

    request(next) {
      request = {
        pill: next.pill === true,
        card: next.card && next.card.width > 0 && next.card.height > 0
          ? { width: Math.min(next.card.width, ORB_CARD.width * 2), height: Math.min(next.card.height, ORB_CARD.height * 2) }
          : null,
        menu: next.menu === true,
      }
      if (windowKind(settings.look) === 'zone')
        apply()
      return snapshot
    },

    drag(phase) {
      if (windowKind(settings.look) !== 'zone')
        return
      if (phase === 'press' || (phase === 'start' && !dragging)) {
        dragging = { cursor: ports.cursor(), zone: { ...zone } }
        // what the windows have now: a move that changes nothing inside the window sends no message
        lastRelative = relativeKey(snapshot)
        return
      }
      if (!dragging || phase === 'start')
        return
      const now = ports.cursor()
      const moved: Rect = { ...dragging.zone, x: dragging.zone.x + (now.x - dragging.cursor.x), y: dragging.zone.y + (now.y - dragging.cursor.y) }
      // anywhere on any screen while dragging, but always inside a work area: never over the taskbar, never off the screens
      const area = workAreaFor({ ...moved })
      if (phase === 'move') {
        zone = clampRect(moved, area)
        apply({ quiet: true })
        return
      }
      // end: snap to the nearest edge or corner and remember it for this display
      zone = snapRect(clampRect(moved, area), area).rect
      dragging = undefined
      remember()
      apply()
    },

    nudge(direction) {
      if (windowKind(settings.look) !== 'zone')
        return
      zone = moveByKey(zone, direction, workAreaFor(zone))
      remember()
      apply()
    },

    resetPosition() {
      if (windowKind(settings.look) !== 'zone')
        return
      zone = defaultRect(sizeOfZone(), ports.displays(), ports.primaryId())
      placements = EMPTY_PLACEMENTS
      remember()
      apply()
      ports.log?.('the orb was put back at its default place')
    },

    displaysChanged(previous) {
      if (windowKind(settings.look) !== 'zone') {
        // the avatar window keeps the rules it had (usableSavedBounds at start, the tray's recovery); nothing to move here
        return
      }
      const current = ports.displays()
      ports.log?.(`the screens changed: ${current.length} display(s), work areas ${JSON.stringify(current.map(d => d.workArea))}`)
      zone = reflowAfterDisplayChange(zone, sizeOfZone(), previous, current, ports.primaryId())
      placements = pruneStore(placements, current)
      remember()
      apply()
    },
  }
}
