import type { Rect } from './jarvis-look'
import type { DisplayInfo, PlacementStore } from './orb-layout'

import { describe, expect, it } from 'vitest'

import { SNAP_MARGIN, SNAP_THRESHOLD, zoneSize } from './jarvis-look'
import {
  clampRect,
  computeOrbWindow,
  defaultRect,
  displayFor,
  fromSpot,
  moveByKey,
  pruneStore,
  recordPlacement,
  reflowAfterDisplayChange,
  restorePlacement,
  snapRect,
  toSpot,
} from './orb-layout'

// A laptop display whose taskbar takes the bottom 48 px, and a second monitor on its right.
const laptop: DisplayInfo = { id: 1, workArea: { x: 0, y: 0, width: 1536, height: 824 }, scaleFactor: 1.25 }
const monitor: DisplayInfo = { id: 2, workArea: { x: 1536, y: 0, width: 1920, height: 1040 }, scaleFactor: 1 }
const M = zoneSize('orb', 'M')

const orbAt = (x: number, y: number): Rect => ({ x, y, ...M })
const inside = (r: Rect, a: Rect) => r.x >= a.x && r.y >= a.y && r.x + r.width <= a.x + a.width && r.y + r.height <= a.y + a.height

describe('clampRect', () => {
  it('moves a rect that sticks out back into the work area (never over the taskbar)', () => {
    const r = clampRect(orbAt(1500, 800), laptop.workArea)
    expect(inside(r, laptop.workArea)).toBe(true)
    expect(r.y + r.height).toBeLessThanOrEqual(824)
  })
  it('shrinks a rect larger than the area', () => {
    expect(clampRect({ x: -50, y: -50, width: 5000, height: 5000 }, laptop.workArea)).toEqual(laptop.workArea)
  })
})

describe('snapRect', () => {
  const area = laptop.workArea
  it('snaps to the left edge with the margin', () => {
    const { rect, edges } = snapRect(orbAt(10, 300), area)
    expect(rect.x).toBe(area.x + SNAP_MARGIN)
    expect(edges.x).toBe('left')
    expect(edges.y).toBeNull()
    expect(rect.y).toBe(300)
  })
  it('snaps to a corner (both axes)', () => {
    const { rect, edges } = snapRect(orbAt(area.width - M.width - 5, area.height - M.height - 5), area)
    expect(edges).toEqual({ x: 'right', y: 'bottom' })
    expect(rect).toMatchObject({ x: area.width - M.width - SNAP_MARGIN, y: area.height - M.height - SNAP_MARGIN })
  })
  it('does not snap in the middle of the screen', () => {
    const start = orbAt(600, 300)
    const { rect, edges } = snapRect(start, area)
    expect(rect).toEqual(start)
    expect(edges).toEqual({ x: null, y: null })
  })
  it('snaps at the threshold and not beyond it', () => {
    expect(snapRect(orbAt(SNAP_THRESHOLD, 300), area).edges.x).toBe('left')
    expect(snapRect(orbAt(SNAP_THRESHOLD + 1, 300), area).edges.x).toBeNull()
  })
  it('a rect dragged off the screen lands inside, snapped', () => {
    const { rect } = snapRect(orbAt(-400, 9000), area)
    expect(inside(rect, area)).toBe(true)
    expect(rect.x).toBe(area.x + SNAP_MARGIN)
    expect(rect.y + rect.height).toBe(area.y + area.height - SNAP_MARGIN)
  })
  it('works on a second monitor with an offset origin', () => {
    const { rect } = snapRect(orbAt(1536 + 8, 20), monitor.workArea)
    expect(rect).toMatchObject({ x: 1536 + SNAP_MARGIN, y: SNAP_MARGIN })
  })
})

describe('placement per display', () => {
  it('stores fractions of the free space and restores the same corner after a resolution or DPI change', () => {
    const corner = defaultRect(M, [laptop])
    const store = recordPlacement({ byDisplay: {} }, corner, [laptop])
    expect(store.byDisplay['1']).toMatchObject({ fx: expect.closeTo(1 - SNAP_MARGIN / (1536 - M.width), 5), fy: expect.closeTo(1 - SNAP_MARGIN / (824 - M.height), 5), sx: 'right', sy: 'bottom' })
    // the same display, now 125 % -> 150 %: a smaller work area
    const smaller: DisplayInfo = { id: 1, workArea: { x: 0, y: 0, width: 1280, height: 672 }, scaleFactor: 1.5 }
    const restored = restorePlacement(store, M, [smaller])
    expect(inside(restored, smaller.workArea)).toBe(true)
    // a corner stays exactly the same corner, with the same margin
    expect(restored.x + restored.width).toBe(1280 - SNAP_MARGIN)
    expect(restored.y + restored.height).toBe(672 - SNAP_MARGIN)
  })

  it('remembers one place per display and returns to the display it was on last', () => {
    let store: PlacementStore = { byDisplay: {} }
    store = recordPlacement(store, orbAt(100, 100), [laptop, monitor])
    store = recordPlacement(store, orbAt(1700, 200), [laptop, monitor])
    expect(Object.keys(store.byDisplay).sort()).toEqual(['1', '2'])
    expect(store.lastDisplayId).toBe('2')
    const back = restorePlacement(store, M, [laptop, monitor])
    expect(displayFor(back, [laptop, monitor])?.id).toBe(2)
    expect(back.x).toBeCloseTo(1700, -1)
  })

  it('drops a saved position that no display shows (the avatar-off-screen bug stays fixed)', () => {
    const store: PlacementStore = { lastDisplayId: '3', byDisplay: { 3: { fx: 0.5, fy: 0.5 } } }
    const rect = restorePlacement(store, M, [laptop])
    expect(rect).toEqual(defaultRect(M, [laptop]))
    expect(pruneStore(store, [laptop]).byDisplay).toEqual({})
  })

  it('falls back to another saved display when the last one was unplugged', () => {
    const store: PlacementStore = { lastDisplayId: '2', byDisplay: { 1: { fx: 0, fy: 0 }, 2: { fx: 1, fy: 1 } } }
    const rect = restorePlacement(store, M, [laptop])
    expect(rect).toMatchObject({ x: 0, y: 0 })
  })

  it('ignores damaged entries', () => {
    const store = { lastDisplayId: '1', byDisplay: { 1: { fx: Number.NaN, fy: 0.2 } } } as unknown as PlacementStore
    expect(restorePlacement(store, M, [laptop])).toEqual(defaultRect(M, [laptop]))
    expect(restorePlacement(undefined, M, [laptop])).toEqual(defaultRect(M, [laptop]))
  })

  it('the default place is inside the work area, above the taskbar', () => {
    const r = defaultRect(M, [laptop])
    expect(inside(r, laptop.workArea)).toBe(true)
    expect(r.y + r.height).toBe(824 - SNAP_MARGIN)
  })

  it('does not record a position without a display', () => {
    const store: PlacementStore = { byDisplay: {} }
    expect(recordPlacement(store, orbAt(0, 0), [])).toBe(store)
  })

  it('a place away from the edges has no edge flags and survives a round trip', () => {
    expect(toSpot(orbAt(400, 300), laptop.workArea)).toEqual({ fx: expect.any(Number), fy: expect.any(Number) })
  })

  it('toSpot and fromSpot round trip', () => {
    const r = orbAt(400, 123)
    expect(fromSpot(toSpot(r, laptop.workArea), M, laptop.workArea)).toEqual(r)
  })
})

describe('reflowAfterDisplayChange', () => {
  it('keeps the relative place when the display changes size (DPI change)', () => {
    const before = laptop
    const after: DisplayInfo = { id: 1, workArea: { x: 0, y: 0, width: 1024, height: 700 } }
    const spot = fromSpot({ fx: 1, fy: 0 }, M, before.workArea)
    const r = reflowAfterDisplayChange(spot, M, [before], [after])
    expect(r.x + r.width).toBeLessThanOrEqual(1024)
    expect(r.x).toBeGreaterThan(1024 - M.width - 2)
    expect(r.y).toBe(0)
  })

  it('moves an orb whose display was unplugged to the default place on what is left', () => {
    const r = reflowAfterDisplayChange(orbAt(1800, 300), M, [laptop, monitor], [laptop])
    expect(r).toEqual(defaultRect(M, [laptop]))
    expect(inside(r, laptop.workArea)).toBe(true)
  })

  it('always ends inside a work area, whatever happened', () => {
    const tiny: DisplayInfo = { id: 1, workArea: { x: 0, y: 0, width: 150, height: 120 } }
    expect(inside(reflowAfterDisplayChange(orbAt(1400, 700), M, [laptop], [tiny]), tiny.workArea)).toBe(true)
  })
})

describe('displayFor', () => {
  it('finds the display that holds the centre, else the nearest', () => {
    expect(displayFor(orbAt(1700, 100), [laptop, monitor])?.id).toBe(2)
    expect(displayFor(orbAt(5000, 5000), [laptop, monitor])?.id).toBe(2)
    expect(displayFor(orbAt(-3000, 100), [laptop, monitor])?.id).toBe(1)
    expect(displayFor(orbAt(0, 0), [])).toBeUndefined()
  })
})

describe('moveByKey (Alt+arrows)', () => {
  it('moves one step and stops at the work area', () => {
    const start = orbAt(100, 100)
    expect(moveByKey(start, 'right', laptop.workArea)).toMatchObject({ x: 116, y: 100 })
    expect(moveByKey(start, 'up', laptop.workArea, 500)).toMatchObject({ y: 0 })
    expect(moveByKey(orbAt(0, 0), 'left', laptop.workArea).x).toBe(0)
    expect(moveByKey(orbAt(1536 - M.width, 824 - M.height), 'down', laptop.workArea)).toMatchObject({ y: 824 - M.height })
  })
})

describe('computeOrbWindow', () => {
  const area = laptop.workArea
  const base = { look: 'orb' as const, pill: false, card: null, menu: false, workArea: area }

  it('is just the zone when nothing else is open', () => {
    const zone = orbAt(500, 300)
    const l = computeOrbWindow({ ...base, zone })
    expect(l.window).toEqual(zone)
    expect(l.zone).toEqual({ x: 0, y: 0, ...M })
    expect(l.card).toBeNull()
  })

  it('puts the card to the right and keeps the orb where it was', () => {
    const zone = orbAt(100, 200)
    const l = computeOrbWindow({ ...base, zone, card: { width: 460, height: 600 } })
    expect(l.cardSide).toBe('right')
    expect(l.window.x + l.zone.x).toBe(zone.x)
    expect(l.window.y + l.zone.y).toBe(zone.y)
    expect(inside(l.window, area)).toBe(true)
    expect(l.card!.x).toBeGreaterThan(l.zone.x + M.width)
  })

  it('puts the card to the left when the orb sits at the right edge', () => {
    const zone = orbAt(area.width - M.width - SNAP_MARGIN, 200)
    const l = computeOrbWindow({ ...base, zone, card: { width: 460, height: 600 } })
    expect(l.cardSide).toBe('left')
    expect(l.window.x + l.zone.x).toBe(zone.x)
    expect(inside(l.window, area)).toBe(true)
    expect(l.card!.x + l.card!.width).toBeLessThanOrEqual(l.zone.x)
  })

  it('slides a tall card along its side so it stays above the taskbar', () => {
    const zone = orbAt(100, area.height - M.height - SNAP_MARGIN)
    const l = computeOrbWindow({ ...base, zone, card: { width: 460, height: 600 } })
    expect(inside(l.window, area)).toBe(true)
    expect(l.window.y + l.zone.y).toBe(zone.y)
  })

  it('shrinks a card that cannot fit on any side, never off the screen', () => {
    const small: DisplayInfo = { id: 9, workArea: { x: 0, y: 0, width: 700, height: 500 } }
    const zone = orbAt(280, 180)
    const l = computeOrbWindow({ ...base, workArea: small.workArea, zone, card: { width: 460, height: 600 } })
    expect(inside(l.window, small.workArea)).toBe(true)
    expect(l.card!.height).toBeLessThanOrEqual(500)
  })

  it('puts the pill under the orb, and over it near the bottom edge', () => {
    const mid = computeOrbWindow({ ...base, zone: orbAt(300, 200), pill: true })
    expect(mid.pill!.y).toBeGreaterThan(mid.zone.y + M.height - 1)
    const low = computeOrbWindow({ ...base, zone: orbAt(300, area.height - M.height - SNAP_MARGIN), pill: true })
    expect(low.pill!.y + low.pill!.height).toBeLessThanOrEqual(low.zone.y)
    expect(inside(low.window, area)).toBe(true)
  })

  it('keeps the pill inside the screen at a corner', () => {
    const l = computeOrbWindow({ ...base, zone: orbAt(0, 0), pill: true })
    expect(inside(l.window, area)).toBe(true)
  })

  it('has no pill beside a card and none for the bar', () => {
    expect(computeOrbWindow({ ...base, zone: orbAt(100, 100), pill: true, card: { width: 460, height: 400 } }).pill).toBeNull()
    expect(computeOrbWindow({ ...base, look: 'minimal', zone: { x: 100, y: 100, ...zoneSize('minimal', 'M') }, pill: true }).pill).toBeNull()
  })

  it('opens the menu on a side the card does not use', () => {
    const l = computeOrbWindow({ ...base, zone: orbAt(500, 200), card: { width: 460, height: 400 }, menu: true })
    expect(l.menu).not.toBeNull()
    expect(l.cardSide).toBe('right')
    expect(inside(l.window, area)).toBe(true)
    // no overlap between the menu and the card
    const c = l.card!
    const m = l.menu!
    const overlap = Math.min(c.x + c.width, m.x + m.width) > Math.max(c.x, m.x) && Math.min(c.y + c.height, m.y + m.height) > Math.max(c.y, m.y)
    expect(overlap).toBe(false)
  })

  it('the bar prefers a card below it', () => {
    const l = computeOrbWindow({ ...base, look: 'minimal', zone: { x: 400, y: 100, ...zoneSize('minimal', 'M') }, card: { width: 460, height: 400 } })
    expect(l.cardSide).toBe('below')
  })

  it('a zone that starts outside the work area is brought inside first', () => {
    const l = computeOrbWindow({ ...base, zone: orbAt(-500, 5000) })
    expect(inside(l.window, area)).toBe(true)
  })
})
