/**
 * D78: puts the window gate (`window-gate.ts`) in front of every Electron path that can put something on the screen.
 *
 * The gate is installed ONCE, on the prototypes, so a window created by any module, now or later (chat, ask forms, timeline, preview,
 * notifications, settings, DevTools, a window created while hidden) cannot show itself without asking the gate. The Electron classes are
 * passed in, so the contract is tested against stand-in classes (`window-gate-electron.test.ts`); nothing here imports `electron`.
 *
 * Nothing in this file hides JARVIS from screen capture, a task list or monitoring software: it only decides whether JARVIS draws anything.
 */

import type { GateWindow, WindowGate } from './window-gate'

type AnyFn = (...args: any[]) => any

/** The window methods that put a window on the screen or bring it to the front. */
const SHOW_METHODS = ['show', 'showInactive', 'focus', 'restore', 'moveTop'] as const

export interface GatedWindowLike extends GateWindow {
  id: number
  webContents?: {
    openDevTools?: AnyFn
    toggleDevTools?: AnyFn
    closeDevTools?: AnyFn
    on?: AnyFn
  }
  once?: AnyFn
}

export interface GateElectron {
  BrowserWindow: { prototype: Record<string, any>, getAllWindows: () => GatedWindowLike[] }
  Notification?: { prototype: Record<string, any> }
  dialog?: Record<string, AnyFn>
  app: { on: (event: 'browser-window-created', listener: (event: unknown, window: GatedWindowLike) => void) => unknown, off?: AnyFn }
}

interface WindowWants {
  /** The show-type calls this window made while hidden, in order; each method once, with its last arguments. */
  calls: Map<string, unknown[]>
}

/**
 * Routes the show paths through `gate`. Returns the function that restores the original methods (tests, and a clean shutdown).
 */
export function installWindowGate(gate: WindowGate, electron: GateElectron): () => void {
  const restore: Array<() => void> = []
  const proto = electron.BrowserWindow.prototype
  const wants = new WeakMap<object, WindowWants>()

  for (const method of SHOW_METHODS) {
    const original = proto[method] as AnyFn | undefined
    if (typeof original !== 'function')
      continue
    proto[method] = function gatedShow(this: GatedWindowLike, ...args: unknown[]) {
      if (!gate.isHidden())
        return original.apply(this, args)
      let entry = wants.get(this)
      if (!entry) {
        entry = { calls: new Map() }
        wants.set(this, entry)
      }
      entry.calls.set(method, args)
      const self = this
      // one pending request per window: every show-type call of it while hidden is replayed when the gate opens
      gate.request('window', () => {
        const pending = wants.get(self)
        wants.delete(self)
        if (!pending || self.isDestroyed())
          return
        for (const [name, callArgs] of pending.calls)
          (proto[name] as AnyFn).apply(self, callArgs)
      }, `win:${self.id}`)
    }
    restore.push(() => { proto[method] = original })
  }

  const originalSkip = proto.setSkipTaskbar as AnyFn | undefined
  if (typeof originalSkip === 'function') {
    proto.setSkipTaskbar = function gatedSkipTaskbar(this: GatedWindowLike, skip: boolean) {
      // while hidden the taskbar button stays away; what the window asked for is applied when it comes back
      if (gate.noteSkipTaskbar(this, Boolean(skip)))
        return originalSkip.call(this, true)
      return originalSkip.call(this, skip)
    }
    restore.push(() => { proto.setSkipTaskbar = originalSkip })
  }

  if (electron.Notification) {
    const notificationProto = electron.Notification.prototype
    const originalNotify = notificationProto.show as AnyFn | undefined
    if (typeof originalNotify === 'function') {
      notificationProto.show = function gatedNotify(this: object, ...args: unknown[]) {
        const self = this
        gate.request('notification', () => originalNotify.apply(self, args))
      }
      restore.push(() => { notificationProto.show = originalNotify })
    }
  }

  if (electron.dialog) {
    const dialog = electron.dialog
    const refusals: Record<string, () => unknown> = {
      showOpenDialog: () => ({ canceled: true, filePaths: [] }),
      showSaveDialog: () => ({ canceled: true, filePath: '' }),
      showMessageBox: () => { throw new Error('JARVIS is hidden: no dialog is shown') },
      showErrorBox: () => undefined,
    }
    for (const [name, refuse] of Object.entries(refusals)) {
      const original = dialog[name]
      if (typeof original !== 'function')
        continue
      dialog[name] = function gatedDialog(this: unknown, ...args: unknown[]) {
        if (gate.isHidden())
          return name === 'showErrorBox' ? refuse() : Promise.resolve().then(refuse)
        return original.apply(this, args)
      }
      restore.push(() => { dialog[name] = original })
    }
  }

  // a window that is created while hidden never gets onto the screen or the taskbar; its DevTools do not open either
  function guardNewWindow(window: GatedWindowLike) {
    gate.adopt(window)
    const contents = window.webContents
    if (!contents)
      return
    for (const name of ['openDevTools', 'toggleDevTools'] as const) {
      const original = contents[name]
      if (typeof original !== 'function')
        continue
      contents[name] = function gatedDevTools(this: unknown, ...args: unknown[]) {
        const self = this
        gate.request('devtools', () => original.apply(self, args))
      }
    }
    contents.on?.('devtools-opened', () => {
      if (gate.isHidden())
        contents.closeDevTools?.()
    })
  }
  const listener = (_event: unknown, window: GatedWindowLike) => guardNewWindow(window)
  electron.app.on('browser-window-created', listener)
  restore.push(() => electron.app.off?.('browser-window-created', listener))
  // windows that exist already (the gate may be installed after the first one)
  for (const window of electron.BrowserWindow.getAllWindows())
    guardNewWindow(window)

  return () => {
    for (const undo of restore.reverse())
      undo()
  }
}
