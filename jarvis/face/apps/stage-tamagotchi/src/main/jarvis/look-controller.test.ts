import type { OrbLayoutSnapshot, Rect } from '../../shared/jarvis-look'
import type { DisplayInfo } from '../../shared/orb-layout'
import type { StoredLook } from './look-config'
import type { LookPorts } from './look-controller'

import { describe, expect, it } from 'vitest'

import { SNAP_MARGIN, zoneSize } from '../../shared/jarvis-look'
import { createLookController } from './look-controller'

const laptop: DisplayInfo = { id: 1, workArea: { x: 0, y: 0, width: 1536, height: 824 } }
const monitor: DisplayInfo = { id: 2, workArea: { x: 1536, y: 0, width: 1920, height: 1040 } }

const inside = (r: Rect, a: Rect) => r.x >= a.x && r.y >= a.y && r.x + r.width <= a.x + a.width && r.y + r.height <= a.y + a.height

function setup(options: { stored?: StoredLook, existing?: boolean, displays?: DisplayInfo[] } = {}) {
  let displays = options.displays ?? [laptop]
  let cursor = { x: 0, y: 0 }
  let saved: StoredLook | undefined = options.stored
  const win = {
    bounds: { x: 0, y: 0, width: 450, height: 600 } as Rect,
    resizable: true,
    destroyed: false,
    setBoundsCalls: 0,
    isDestroyed() { return this.destroyed },
    getBounds() { return this.bounds },
    setBounds(b: Rect) {
      this.bounds = b
      this.setBoundsCalls += 1
    },
    setResizable(r: boolean) { this.resizable = r },
  }
  const emitted: OrbLayoutSnapshot[] = []
  const ports: LookPorts = {
    window: () => win,
    displays: () => displays,
    primaryId: () => 1,
    cursor: () => cursor,
    load: () => saved,
    save: (s) => { saved = JSON.parse(JSON.stringify(s)) },
    existingProfile: options.existing ?? false,
    avatarBounds: () => ({ x: 900, y: 100, width: 450, height: 600 }),
    emit: s => emitted.push(s),
  }
  const controller = createLookController(ports)
  return {
    controller,
    win,
    emitted,
    saved: () => saved,
    setDisplays: (d: DisplayInfo[]) => { displays = d },
    moveCursor: (x: number, y: number) => { cursor = { x, y } },
  }
}

describe('look controller: start', () => {
  it('a new profile starts as an orb in the default corner, above the taskbar', () => {
    const { controller } = setup()
    expect(controller.settings().look).toBe('orb')
    const b = controller.initialBounds()
    expect(inside(b, laptop.workArea)).toBe(true)
    expect(b.x + b.width).toBe(1536 - SNAP_MARGIN)
    expect(b.y + b.height).toBe(824 - SNAP_MARGIN)
  })

  it('an existing profile starts as the avatar and the controller does not touch its window', () => {
    const { controller, win } = setup({ existing: true })
    expect(controller.settings().look).toBe('avatar')
    expect(controller.layout().window).toBeNull()
    expect(controller.initialBounds()).toEqual({ x: 900, y: 100, width: 450, height: 600 })
    controller.request({ pill: true, card: { width: 460, height: 600 }, menu: true })
    controller.drag('start')
    controller.drag('move')
    controller.nudge('left')
    expect(win.setBoundsCalls).toBe(0)
  })

  it('a saved position that no display shows is dropped at start', () => {
    const { controller } = setup({ stored: { look: 'orb', placements: { lastDisplayId: '7', byDisplay: { 7: { fx: 0.4, fy: 0.4 } } } } })
    const b = controller.initialBounds()
    expect(b.x + b.width).toBe(1536 - SNAP_MARGIN)
  })

  it('a saved position on a display that is there is used', () => {
    const { controller } = setup({ stored: { look: 'orb', size: 'M', placements: { lastDisplayId: '1', byDisplay: { 1: { fx: 0, fy: 0 } } } } })
    expect(controller.initialBounds()).toMatchObject({ x: 0, y: 0 })
  })
})

describe('look controller: moving', () => {
  it('a drag follows the cursor, then snaps to the nearest edge and is remembered', () => {
    const t = setup()
    const { controller, win } = t
    t.moveCursor(1400, 700)
    controller.drag('start')
    t.moveCursor(1000, 400)
    controller.drag('move')
    expect(win.bounds.x).toBeLessThan(1400)
    t.moveCursor(30, 300)
    controller.drag('move')
    controller.drag('end')
    const size = zoneSize('orb', 'M')
    expect(win.bounds.x).toBe(SNAP_MARGIN)
    expect(win.bounds.width).toBe(size.width)
    expect(t.saved()?.placements?.byDisplay['1']).toBeDefined()
  })

  it('never leaves the work area, even when the cursor does', () => {
    const t = setup()
    t.moveCursor(1000, 400)
    t.controller.drag('start')
    t.moveCursor(-5000, 9000)
    t.controller.drag('move')
    expect(inside(t.win.bounds, laptop.workArea)).toBe(true)
    t.controller.drag('end')
    expect(inside(t.win.bounds, laptop.workArea)).toBe(true)
  })

  it('a move without a start is ignored', () => {
    const t = setup()
    const before = t.win.bounds
    t.controller.drag('move')
    t.controller.drag('end')
    expect(t.win.bounds).toBe(before)
  })

  it('alt+arrow moves it by a step and keeps it on the screen', () => {
    const t = setup()
    const before = t.controller.layout().window!
    t.controller.nudge('left')
    expect(t.controller.layout().window!.x).toBe(before.x - 16)
    for (let i = 0; i < 200; i++)
      t.controller.nudge('down')
    expect(inside(t.controller.layout().window!, laptop.workArea)).toBe(true)
  })

  it('reset position puts it back in the default corner and forgets the saved places', () => {
    const t = setup()
    t.moveCursor(500, 500)
    t.controller.drag('start')
    t.moveCursor(100, 100)
    t.controller.drag('move')
    t.controller.drag('end')
    t.controller.resetPosition()
    const b = t.win.bounds
    expect(b.x + b.width).toBe(1536 - SNAP_MARGIN)
    expect(b.y + b.height).toBe(824 - SNAP_MARGIN)
  })
})

describe('look controller: cards, pill and menu', () => {
  it('grows the window beside the orb and keeps the orb where it was', () => {
    const t = setup({ stored: { look: 'orb', placements: { lastDisplayId: '1', byDisplay: { 1: { fx: 0.3, fy: 0.3 } } } } })
    const before = t.controller.layout()
    const orbX = before.window!.x + before.zone!.x
    const orbY = before.window!.y + before.zone!.y
    const after = t.controller.request({ pill: false, card: { width: 460, height: 600 }, menu: false })
    expect(after.window!.width).toBeGreaterThan(before.window!.width)
    expect(after.window!.x + after.zone!.x).toBe(orbX)
    expect(after.window!.y + after.zone!.y).toBe(orbY)
    expect(inside(after.window!, laptop.workArea)).toBe(true)
    expect(t.win.bounds).toEqual(after.window)
    // closing it returns the window to the zone
    const closed = t.controller.request({ pill: false, card: null, menu: false })
    expect(closed.window!.width).toBe(zoneSize('orb', 'M').width)
  })

  it('caps an absurd card size from the renderer', () => {
    const t = setup()
    const s = t.controller.request({ pill: false, card: { width: 1e9, height: 1e9 }, menu: false })
    expect(inside(s.window!, laptop.workArea)).toBe(true)
  })
})

describe('look controller: switching the look live', () => {
  it('orb to avatar gives the window back its own bounds and makes it resizable', () => {
    const t = setup()
    expect(t.win.resizable).toBe(true)
    t.controller.update({ look: 'avatar' })
    expect(t.controller.layout().window).toBeNull()
    expect(t.win.bounds).toEqual({ x: 900, y: 100, width: 450, height: 600 })
    expect(t.win.resizable).toBe(true)
    expect(t.emitted.at(-1)?.look).toBe('avatar')
    expect(t.saved()?.look).toBe('avatar')
  })

  it('avatar to orb shrinks the window to the zone at the remembered place and locks its size', () => {
    const t = setup({ existing: true })
    t.controller.update({ look: 'orb' })
    expect(t.controller.layout().window).not.toBeNull()
    expect(t.win.bounds.width).toBe(zoneSize('orb', 'M').width)
    expect(t.win.resizable).toBe(false)
    expect(inside(t.win.bounds, laptop.workArea)).toBe(true)
  })

  it('orb to minimal bar uses the bar size', () => {
    const t = setup()
    t.controller.update({ look: 'minimal', size: 'L' })
    expect(t.win.bounds).toMatchObject(zoneSize('minimal', 'L'))
    expect(inside(t.win.bounds, laptop.workArea)).toBe(true)
  })

  it('a bigger size does not grow off the edge it was snapped to', () => {
    const t = setup()
    t.controller.update({ size: 'S' })
    expect(t.win.bounds.x + t.win.bounds.width).toBe(1536 - SNAP_MARGIN)
    t.controller.update({ size: 'L' })
    expect(t.win.bounds.x + t.win.bounds.width).toBe(1536 - SNAP_MARGIN)
    expect(t.win.bounds.y + t.win.bounds.height).toBe(824 - SNAP_MARGIN)
  })

  it('a change that is not a change does nothing', () => {
    const t = setup()
    const calls = t.win.setBoundsCalls
    expect(t.controller.update({ look: 'orb', size: 'M' }).any).toBe(false)
    expect(t.win.setBoundsCalls).toBe(calls)
  })

  it('writes the settings and the places, and no secret or content', () => {
    const t = setup()
    t.controller.update({ startHidden: true })
    const text = JSON.stringify(t.saved())
    expect(Object.keys(t.saved()!).sort()).toEqual(['forgottenHours', 'hideShortcut', 'look', 'placements', 'resetShortcut', 'showTrayWhileHidden', 'size', 'speakWhileHidden', 'startHidden'])
    expect(text).not.toMatch(/token|key|password/i)
  })
})

describe('look controller: displays change', () => {
  it('clamps to the work area when the resolution or DPI changes (a corner stays a corner)', () => {
    const t = setup()
    const old = [laptop]
    const smaller: DisplayInfo = { id: 1, workArea: { x: 0, y: 0, width: 1024, height: 600 } }
    t.setDisplays([smaller])
    t.controller.displaysChanged(old)
    const b = t.win.bounds
    expect(inside(b, smaller.workArea)).toBe(true)
    expect(b.x + b.width).toBe(1024 - SNAP_MARGIN)
    expect(b.y + b.height).toBe(600 - SNAP_MARGIN)
  })

  it('moves the orb to the default place of the remaining display when its monitor is unplugged', () => {
    const t = setup({ displays: [laptop, monitor], stored: { look: 'orb', placements: { lastDisplayId: '2', byDisplay: { 2: { fx: 0.5, fy: 0.5 } } } } })
    expect(t.controller.initialBounds().x).toBeGreaterThan(1536)
    const old = [laptop, monitor]
    t.setDisplays([laptop])
    t.controller.displaysChanged(old)
    expect(inside(t.win.bounds, laptop.workArea)).toBe(true)
    expect(t.saved()?.placements?.byDisplay['2']).toBeUndefined()
  })

  it('moves the work area when the taskbar moves (it never covers the new taskbar)', () => {
    const t = setup()
    const old = [laptop]
    const withTaskbarOnTop: DisplayInfo = { id: 1, workArea: { x: 0, y: 48, width: 1536, height: 776 } }
    t.setDisplays([withTaskbarOnTop])
    t.controller.displaysChanged(old)
    expect(inside(t.win.bounds, withTaskbarOnTop.workArea)).toBe(true)
  })

  it('leaves the avatar window alone', () => {
    const t = setup({ existing: true })
    t.setDisplays([{ id: 1, workArea: { x: 0, y: 0, width: 800, height: 600 } }])
    t.controller.displaysChanged([laptop])
    expect(t.win.setBoundsCalls).toBe(0)
  })
})
