import type { GateElectron, GatedWindowLike } from './window-gate-electron'

import { afterEach, describe, expect, it, vi } from 'vitest'

import { createWindowGate } from './window-gate'
import { installWindowGate } from './window-gate-electron'

/** A stand-in for Electron's BrowserWindow: the same show paths, recorded. */
function makeElectron() {
  const created: Array<(window: GatedWindowLike) => void> = []
  class FakeWindow {
    static nextId = 100
    static all: FakeWindow[] = []
    id = FakeWindow.nextId++
    visible = false
    destroyed = false
    skipTaskbar = false
    calls: string[] = []
    devtools = 0
    webContents = {
      openDevTools: () => { this.devtools += 1 },
      toggleDevTools: () => { this.devtools += 1 },
      closeDevTools: () => { this.devtools -= 1 },
      on: (_event: string, _listener: () => void) => {},
    }

    constructor(options: { show?: boolean } = {}) {
      FakeWindow.all.push(this)
      // like Electron: a window with show: true is on the screen when 'browser-window-created' fires
      if (options.show === true)
        this.visible = true
      for (const listener of created)
        listener(this as unknown as GatedWindowLike)
    }

    isDestroyed() { return this.destroyed }
    isVisible() { return this.visible }
    hide() { this.visible = false }
    show() { this.visible = true; this.calls.push('show') }
    showInactive() { this.visible = true; this.calls.push('showInactive') }
    focus() { this.calls.push('focus') }
    restore() { this.calls.push('restore') }
    moveTop() { this.calls.push('moveTop') }
    setSkipTaskbar(skip: boolean) { this.skipTaskbar = skip }
    static getAllWindows() { return FakeWindow.all as unknown as GatedWindowLike[] }
  }
  class FakeNotification {
    static shown = 0
    show() { FakeNotification.shown += 1 }
  }
  const dialog = {
    showOpenDialog: vi.fn(async () => ({ canceled: false, filePaths: ['x'] })),
    showSaveDialog: vi.fn(async () => ({ canceled: false, filePath: 'x' })),
    showMessageBox: vi.fn(async () => ({ response: 0 })),
    showErrorBox: vi.fn(),
  }
  const electron = {
    BrowserWindow: FakeWindow as unknown as GateElectron['BrowserWindow'],
    Notification: FakeNotification as unknown as GateElectron['Notification'],
    dialog,
    app: { on: (_event: 'browser-window-created', listener: (e: unknown, w: GatedWindowLike) => void) => { created.push(w => listener({}, w)) } },
  } satisfies GateElectron
  return { electron, FakeWindow, FakeNotification, dialog }
}

let uninstall: (() => void) | undefined
afterEach(() => uninstall?.())

describe('installWindowGate', () => {
  it('lets every show path through while shown', () => {
    const { electron, FakeWindow } = makeElectron()
    const gate = createWindowGate()
    uninstall = installWindowGate(gate, electron)
    const w = new FakeWindow()
    w.show(); w.focus(); w.showInactive(); w.restore(); w.moveTop()
    expect(w.calls).toEqual(['show', 'focus', 'showInactive', 'restore', 'moveTop'])
  })

  it('refuses show, showInactive, focus, restore and moveTop while hidden, and replays them once at unhide', () => {
    const { electron, FakeWindow } = makeElectron()
    const gate = createWindowGate()
    uninstall = installWindowGate(gate, electron)
    gate.hide([])
    const w = new FakeWindow()
    w.show(); w.focus(); w.showInactive(); w.show(); w.restore(); w.moveTop()
    expect(w.calls).toEqual([])
    expect(w.visible).toBe(false)
    expect(gate.state().queued).toBe(1)
    gate.unhide()
    expect(w.visible).toBe(true)
    expect(w.calls.sort()).toEqual(['focus', 'moveTop', 'restore', 'show', 'showInactive'].sort())
    // once
    gate.hide([])
    gate.unhide()
    expect(w.calls).toHaveLength(5)
  })

  it('a window created while hidden, even one that shows itself, never reaches the screen', () => {
    const { electron, FakeWindow } = makeElectron()
    const gate = createWindowGate()
    uninstall = installWindowGate(gate, electron)
    gate.hide([])
    const w = new FakeWindow({ show: true })
    expect(w.visible).toBe(false)
    expect(w.skipTaskbar).toBe(true)
    w.show()
    expect(w.visible).toBe(false)
  })

  it('keeps the taskbar button away while hidden and applies the window\'s own wish afterwards', () => {
    const { electron, FakeWindow } = makeElectron()
    const gate = createWindowGate()
    uninstall = installWindowGate(gate, electron)
    const w = new FakeWindow()
    w.visible = true
    gate.hide([w as unknown as GatedWindowLike])
    w.setSkipTaskbar(false)
    expect(w.skipTaskbar).toBe(true)
    gate.unhide()
    expect(w.skipTaskbar).toBe(false)
  })

  it('holds notifications while hidden and delivers them once', () => {
    const { electron, FakeNotification } = makeElectron()
    const gate = createWindowGate()
    uninstall = installWindowGate(gate, electron)
    gate.hide([])
    new (FakeNotification as unknown as new () => { show: () => void })().show()
    new (FakeNotification as unknown as new () => { show: () => void })().show()
    expect(FakeNotification.shown).toBe(0)
    expect(gate.state().queued).toBe(2)
    gate.unhide()
    expect(FakeNotification.shown).toBe(2)
  })

  it('refuses DevTools while hidden', () => {
    const { electron, FakeWindow } = makeElectron()
    const gate = createWindowGate()
    uninstall = installWindowGate(gate, electron)
    const w = new FakeWindow()
    w.webContents.openDevTools()
    expect(w.devtools).toBe(1)
    gate.hide([])
    w.webContents.openDevTools()
    w.webContents.toggleDevTools()
    expect(w.devtools).toBe(1)
    expect(gate.state().queued).toBe(2)
  })

  it('refuses dialogs while hidden and lets them through while shown', async () => {
    const { electron, dialog } = makeElectron()
    const open = dialog.showOpenDialog
    const gate = createWindowGate()
    uninstall = installWindowGate(gate, electron)
    expect((await electron.dialog.showOpenDialog()).canceled).toBe(false)
    gate.hide([])
    expect(await electron.dialog.showOpenDialog()).toEqual({ canceled: true, filePaths: [] })
    await expect(electron.dialog.showMessageBox()).rejects.toThrow('hidden')
    expect(open).toHaveBeenCalledTimes(1)
  })

  it('restores the original methods when uninstalled', () => {
    const { electron, FakeWindow } = makeElectron()
    const original = FakeWindow.prototype.show
    const gate = createWindowGate()
    const undo = installWindowGate(gate, electron)
    expect(FakeWindow.prototype.show).not.toBe(original)
    undo()
    expect(FakeWindow.prototype.show).toBe(original)
  })

  it('skips a window that was destroyed while its show was held', () => {
    const { electron, FakeWindow } = makeElectron()
    const gate = createWindowGate()
    uninstall = installWindowGate(gate, electron)
    gate.hide([])
    const w = new FakeWindow()
    w.show()
    w.destroyed = true
    expect(() => gate.unhide()).not.toThrow()
    expect(w.calls).toEqual([])
  })
})
