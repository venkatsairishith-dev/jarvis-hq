/**
 * D78: where the orb (or the minimal bar) is, how it snaps, how a position is remembered per display, and how big the window must be
 * to hold the orb plus the pill, a menu or a card beside it. Pure geometry: no electron, no DOM. The Electron side
 * (`orb-window.ts`) feeds it `screen.getAllDisplays()` and applies the result with `setBounds`.
 *
 * Rules the module keeps (each is a test):
 * - everything is inside the display's WORK AREA (that excludes the taskbar), so the orb never covers the taskbar;
 * - a saved position is stored as fractions of the free space of its display, so a DPI change, a new resolution or a moved taskbar
 *   keeps the orb where the owner put it (a corner stays the same corner);
 * - a saved position whose display is gone is dropped (the avatar-off-screen bug of Phase 3 must stay fixed: `usableSavedBounds`);
 * - a drag snaps to an edge or a corner within SNAP_THRESHOLD, leaving SNAP_MARGIN.
 */

import type { Anchor, Look, LookSize, Rect } from './jarvis-look'

import { ORB_CARD, ORB_MENU, ORB_PILL, SNAP_MARGIN, SNAP_THRESHOLD, zoneSize } from './jarvis-look'

export interface DisplayInfo {
  id: number | string
  workArea: Rect
  scaleFactor?: number
}

/** A remembered place: fractions (0 to 1) of the free space of one display's work area, so it survives a size or DPI change. */
export interface SavedSpot {
  fx: number
  fy: number
  /** Set when the orb sat on an edge (with the margin): it stays on that edge when the size or the display changes. */
  sx?: 'left' | 'right'
  sy?: 'top' | 'bottom'
}

export interface PlacementStore {
  /** The display the orb was on last. */
  lastDisplayId?: string
  byDisplay: Record<string, SavedSpot>
}

export const EMPTY_PLACEMENTS: PlacementStore = { byDisplay: {} }

const displayKey = (display: Pick<DisplayInfo, 'id'>) => String(display.id)

const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n)

export function isRect(value: unknown): value is Rect {
  const r = value as Rect | undefined
  return !!r && finite(r.x) && finite(r.y) && finite(r.width) && finite(r.height) && r.width > 0 && r.height > 0
}

/** Moves `rect` inside `area`; a rect larger than the area shrinks to it first. */
export function clampRect(rect: Rect, area: Rect): Rect {
  const width = Math.min(rect.width, area.width)
  const height = Math.min(rect.height, area.height)
  return {
    x: Math.round(Math.min(Math.max(rect.x, area.x), area.x + area.width - width)),
    y: Math.round(Math.min(Math.max(rect.y, area.y), area.y + area.height - height)),
    width: Math.round(width),
    height: Math.round(height),
  }
}

const centerOf = (r: Rect) => ({ x: r.x + r.width / 2, y: r.y + r.height / 2 })

function overlapArea(a: Rect, b: Rect): number {
  const w = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x)
  const h = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y)
  return w > 0 && h > 0 ? w * h : 0
}

/** The display whose work area holds the centre of `rect`; else the one it overlaps most; else the one nearest to it. */
export function displayFor(rect: Rect, displays: readonly DisplayInfo[]): DisplayInfo | undefined {
  if (displays.length === 0)
    return undefined
  const c = centerOf(rect)
  const holder = displays.find(d => c.x >= d.workArea.x && c.x < d.workArea.x + d.workArea.width && c.y >= d.workArea.y && c.y < d.workArea.y + d.workArea.height)
  if (holder)
    return holder
  let best = displays[0]!
  let bestOverlap = -1
  let bestDistance = Infinity
  for (const d of displays) {
    const overlap = overlapArea(rect, d.workArea)
    const dc = centerOf(d.workArea)
    const distance = (dc.x - c.x) ** 2 + (dc.y - c.y) ** 2
    if (overlap > bestOverlap || (overlap === bestOverlap && distance < bestDistance)) {
      best = d
      bestOverlap = overlap
      bestDistance = distance
    }
  }
  return best
}

// ---- snapping

export interface SnapResult {
  rect: Rect
  /** Which edges it snapped to, for the tests and the settle animation. */
  edges: { x: 'left' | 'right' | null, y: 'top' | 'bottom' | null }
}

/**
 * Snaps `rect` to the edges of `area` (and so to the corners) when it is within `threshold` of them, leaving `margin`.
 * Each axis snaps on its own. The result is always inside the area.
 */
export function snapRect(rect: Rect, area: Rect, margin = SNAP_MARGIN, threshold = SNAP_THRESHOLD): SnapResult {
  const inside = clampRect(rect, area)
  let { x, y } = inside
  const edges: SnapResult['edges'] = { x: null, y: null }
  const left = inside.x - area.x
  const right = area.x + area.width - (inside.x + inside.width)
  const top = inside.y - area.y
  const bottom = area.y + area.height - (inside.y + inside.height)
  if (left <= threshold && left <= right) {
    x = area.x + margin
    edges.x = 'left'
  }
  else if (right <= threshold) {
    x = area.x + area.width - inside.width - margin
    edges.x = 'right'
  }
  if (top <= threshold && top <= bottom) {
    y = area.y + margin
    edges.y = 'top'
  }
  else if (bottom <= threshold) {
    y = area.y + area.height - inside.height - margin
    edges.y = 'bottom'
  }
  return { rect: clampRect({ ...inside, x, y }, area), edges }
}

// ---- remembering a position per display

export function toSpot(rect: Rect, area: Rect): SavedSpot {
  const freeX = area.width - rect.width
  const freeY = area.height - rect.height
  const fx = freeX > 0 ? (rect.x - area.x) / freeX : 0.5
  const fy = freeY > 0 ? (rect.y - area.y) / freeY : 0.5
  const spot: SavedSpot = { fx: Math.min(1, Math.max(0, fx)), fy: Math.min(1, Math.max(0, fy)) }
  const left = rect.x - area.x
  const right = area.x + area.width - (rect.x + rect.width)
  const top = rect.y - area.y
  const bottom = area.y + area.height - (rect.y + rect.height)
  // within a pixel of the snap margin: that edge is kept exactly from now on
  if (Math.abs(left - SNAP_MARGIN) <= 1)
    spot.sx = 'left'
  else if (Math.abs(right - SNAP_MARGIN) <= 1)
    spot.sx = 'right'
  if (Math.abs(top - SNAP_MARGIN) <= 1)
    spot.sy = 'top'
  else if (Math.abs(bottom - SNAP_MARGIN) <= 1)
    spot.sy = 'bottom'
  return spot
}

export function fromSpot(spot: SavedSpot, size: { width: number, height: number }, area: Rect): Rect {
  const freeX = Math.max(0, area.width - size.width)
  const freeY = Math.max(0, area.height - size.height)
  let x = area.x + spot.fx * freeX
  let y = area.y + spot.fy * freeY
  if (spot.sx === 'left')
    x = area.x + SNAP_MARGIN
  else if (spot.sx === 'right')
    x = area.x + area.width - size.width - SNAP_MARGIN
  if (spot.sy === 'top')
    y = area.y + SNAP_MARGIN
  else if (spot.sy === 'bottom')
    y = area.y + area.height - size.height - SNAP_MARGIN
  return clampRect({ x, y, width: size.width, height: size.height }, area)
}

/** Stores where the orb is now, under the display that holds it. */
export function recordPlacement(store: PlacementStore, rect: Rect, displays: readonly DisplayInfo[]): PlacementStore {
  const display = displayFor(rect, displays)
  if (!display || !isRect(rect))
    return store
  const key = displayKey(display)
  return { lastDisplayId: key, byDisplay: { ...store.byDisplay, [key]: toSpot(rect, display.workArea) } }
}

/** Drops every remembered place whose display is not there any more, and any damaged entry. */
export function pruneStore(store: PlacementStore | undefined, displays: readonly DisplayInfo[]): PlacementStore {
  const present = new Set(displays.map(displayKey))
  const byDisplay: Record<string, SavedSpot> = {}
  for (const [key, spot] of Object.entries(store?.byDisplay ?? {})) {
    if (present.has(key) && finite(spot?.fx) && finite(spot?.fy)) {
      const clean: SavedSpot = { fx: Math.min(1, Math.max(0, spot.fx)), fy: Math.min(1, Math.max(0, spot.fy)) }
      if (spot.sx === 'left' || spot.sx === 'right')
        clean.sx = spot.sx
      if (spot.sy === 'top' || spot.sy === 'bottom')
        clean.sy = spot.sy
      byDisplay[key] = clean
    }
  }
  const last = store?.lastDisplayId
  return { lastDisplayId: last && byDisplay[last] ? last : undefined, byDisplay }
}

/** The default place: the bottom right corner of the primary display's work area, with the margin. */
export function defaultRect(size: { width: number, height: number }, displays: readonly DisplayInfo[], primaryId?: number | string): Rect {
  const display = displays.find(d => primaryId !== undefined && displayKey(d) === String(primaryId)) ?? displays[0]
  if (!display)
    return { x: 0, y: 0, width: size.width, height: size.height }
  const a = display.workArea
  return clampRect({ x: a.x + a.width - size.width - SNAP_MARGIN, y: a.y + a.height - size.height - SNAP_MARGIN, width: size.width, height: size.height }, a)
}

/**
 * Where the orb starts: the saved place on the display it was on last; else the saved place of another display that is still there;
 * else the default. A place whose display is gone is never used.
 */
export function restorePlacement(store: PlacementStore | undefined, size: { width: number, height: number }, displays: readonly DisplayInfo[], primaryId?: number | string): Rect {
  const clean = pruneStore(store, displays)
  const key = clean.lastDisplayId ?? Object.keys(clean.byDisplay)[0]
  const display = key ? displays.find(d => displayKey(d) === key) : undefined
  const spot = key ? clean.byDisplay[key] : undefined
  if (display && spot)
    return fromSpot(spot, size, display.workArea)
  return defaultRect(size, displays, primaryId)
}

/**
 * The orb after the displays changed (a monitor unplugged, a new resolution, a DPI change, the taskbar moved): the same relative place
 * on the display that now holds it, and always inside a work area. `previous` is where it was on the screen.
 */
export function reflowAfterDisplayChange(previous: Rect, size: { width: number, height: number }, oldDisplays: readonly DisplayInfo[], newDisplays: readonly DisplayInfo[], primaryId?: number | string): Rect {
  const before = displayFor(previous, oldDisplays)
  const sameDisplay = before ? newDisplays.find(d => displayKey(d) === displayKey(before)) : undefined
  if (before && sameDisplay)
    return fromSpot(toSpot(previous, before.workArea), size, sameDisplay.workArea)
  // its display is gone: not off-screen, not lost; it goes to the default place
  return defaultRect(size, newDisplays, primaryId)
}

export type Direction = 'left' | 'right' | 'up' | 'down'

/** Alt+arrow with the orb focused: one step, never out of the work area. */
export function moveByKey(rect: Rect, direction: Direction, area: Rect, step = 16): Rect {
  const dx = direction === 'left' ? -step : direction === 'right' ? step : 0
  const dy = direction === 'up' ? -step : direction === 'down' ? step : 0
  return clampRect({ ...rect, x: rect.x + dx, y: rect.y + dy }, area)
}

// ---- the window around the orb

export interface OrbWindowInput {
  /** The orb zone (or the bar) on the screen. */
  zone: Rect
  look: Look
  /** The "what I am doing" pill is wanted (the orb is working on the PC). */
  pill: boolean
  /** A card (ask form, timeline, preview) is open, with the size it wants. */
  card: { width: number, height: number } | null
  /** The right-click menu is open. */
  menu: boolean
  workArea: Rect
}

export interface OrbWindowLayout {
  /** The window on the screen. */
  window: Rect
  /** The zone inside the window. */
  zone: Rect
  pill: Rect | null
  card: Rect | null
  cardSide: Anchor | null
  menu: Rect | null
}

/** A block placed on a side fits when it is inside the area across that side; along the side it can still slide into the area. */
function fitsSide(rect: Rect, side: Anchor, area: Rect): boolean {
  if (side === 'right' || side === 'left')
    return rect.x >= area.x && rect.x + rect.width <= area.x + area.width && rect.height <= area.height
  return rect.y >= area.y && rect.y + rect.height <= area.y + area.height && rect.width <= area.width
}

function placeBeside(zone: Rect, side: Anchor, want: { width: number, height: number }, gap: number): Rect {
  switch (side) {
    case 'right': return { x: zone.x + zone.width + gap, y: zone.y + Math.round((zone.height - want.height) / 2), ...want }
    case 'left': return { x: zone.x - gap - want.width, y: zone.y + Math.round((zone.height - want.height) / 2), ...want }
    case 'below': return { x: zone.x + Math.round((zone.width - want.width) / 2), y: zone.y + zone.height + gap, ...want }
    case 'above': return { x: zone.x + Math.round((zone.width - want.width) / 2), y: zone.y - gap - want.height, ...want }
  }
}

/** How much room a side has beside the zone (the larger of width and height it could give). */
function room(zone: Rect, side: Anchor, area: Rect, gap: number): { width: number, height: number } {
  switch (side) {
    case 'right': return { width: area.x + area.width - (zone.x + zone.width) - gap, height: area.height }
    case 'left': return { width: zone.x - area.x - gap, height: area.height }
    case 'below': return { width: area.width, height: area.y + area.height - (zone.y + zone.height) - gap }
    case 'above': return { width: area.width, height: zone.y - area.y - gap }
  }
}

/** Puts a block of a wanted size beside the zone: the first side where it fits, else the side with most room, with the block shrunk to it. */
function placeBlock(zone: Rect, want: { width: number, height: number }, order: readonly Anchor[], area: Rect, gap: number, minWidth: number, minHeight: number): { rect: Rect, side: Anchor } {
  const wanted = { width: Math.min(want.width, area.width), height: Math.min(want.height, area.height) }
  for (const side of order) {
    const rect = placeBeside(zone, side, wanted, gap)
    if (fitsSide(rect, side, area))
      return { rect: shiftInto(rect, area), side }
  }
  // no side holds it whole: take the side with the most room and make the block smaller (never smaller than a readable minimum)
  let bestSide: Anchor = order[0]!
  let bestScore = -1
  for (const side of order) {
    const r = room(zone, side, area, gap)
    const score = Math.min(r.width, wanted.width) * Math.min(r.height, wanted.height)
    if (score > bestScore) {
      bestScore = score
      bestSide = side
    }
  }
  const r = room(zone, bestSide, area, gap)
  const size = { width: Math.max(Math.min(minWidth, wanted.width), Math.min(wanted.width, r.width)), height: Math.max(Math.min(minHeight, wanted.height), Math.min(wanted.height, r.height)) }
  return { rect: shiftInto(placeBeside(zone, bestSide, size, gap), area), side: bestSide }
}

/** Slides a block along its free axis until it is inside the area (the zone stays where it is; the block does not cover it, it moves along its side). */
function shiftInto(rect: Rect, area: Rect): Rect {
  return {
    ...rect,
    x: Math.min(Math.max(rect.x, area.x), area.x + area.width - rect.width),
    y: Math.min(Math.max(rect.y, area.y), area.y + area.height - rect.height),
  }
}

/**
 * The window that holds the orb zone, its pill, the menu and a card, all inside the work area, with the zone where it was whenever
 * that is possible. Positions inside the result are relative to the window.
 */
export function computeOrbWindow(input: OrbWindowInput): OrbWindowLayout {
  const area = input.workArea
  const zone = clampRect(input.zone, area)
  const gap = ORB_CARD.gap
  const sideOrder: readonly Anchor[] = input.look === 'minimal' ? ['below', 'above', 'right', 'left'] : ['right', 'left', 'below', 'above']

  const parts: Rect[] = [zone]
  let card: Rect | null = null
  let cardSide: Anchor | null = null
  let menu: Rect | null = null
  let pill: Rect | null = null

  if (input.card) {
    const placed = placeBlock(zone, input.card, sideOrder, area, gap, 280, 240)
    card = placed.rect
    cardSide = placed.side
    parts.push(card)
  }
  if (input.menu) {
    // the menu goes to a side the card does not use
    const order = sideOrder.filter(side => side !== cardSide)
    const placed = placeBlock(zone, ORB_MENU, order.length ? order : sideOrder, area, gap, 160, 160)
    menu = placed.rect
    parts.push(menu)
  }
  // the pill sits under the orb, or over it near the bottom edge; with a card beside, or for the bar (which holds its own text), it is not drawn
  if (input.pill && input.look === 'orb' && !card) {
    const width = Math.min(ORB_PILL.width, area.width)
    const below = zone.y + zone.height + ORB_PILL.gap + ORB_PILL.height <= area.y + area.height
    const y = below ? zone.y + zone.height + ORB_PILL.gap : zone.y - ORB_PILL.gap - ORB_PILL.height
    pill = shiftInto({ x: zone.x + Math.round((zone.width - width) / 2), y, width, height: ORB_PILL.height }, area)
    parts.push(pill)
  }

  const minX = Math.min(...parts.map(p => p.x))
  const minY = Math.min(...parts.map(p => p.y))
  const maxX = Math.max(...parts.map(p => p.x + p.width))
  const maxY = Math.max(...parts.map(p => p.y + p.height))
  const window = clampRect({ x: minX, y: minY, width: maxX - minX, height: maxY - minY }, area)
  const rel = (r: Rect | null): Rect | null => r ? { x: r.x - window.x, y: r.y - window.y, width: r.width, height: r.height } : null
  return { window, zone: rel(zone)!, pill: rel(pill), card: rel(card), cardSide, menu: rel(menu) }
}

/** The zone for a look, size and top left corner. */
export function zoneRect(look: Look, size: LookSize, at: { x: number, y: number }): Rect {
  return { x: at.x, y: at.y, ...zoneSize(look, size) }
}
