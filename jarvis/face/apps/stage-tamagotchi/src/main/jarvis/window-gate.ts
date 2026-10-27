/**
 * D78: the ONE window gate of the main process.
 *
 * Every way a window, a widget or a notification can appear on the screen goes through `request()` (the Electron adapter in
 * `window-gate-electron.ts` routes `BrowserWindow.show / showInactive / focus / restore / moveTop`, `Notification.show`, DevTools and the tray
 * through it). While the owner has hidden JARVIS the gate refuses to show and QUEUES the request: only a count is kept in the open, the
 * request itself (a closure over the window) stays in memory and is never logged or written. Unhide delivers the queue in order, once.
 *
 * "Hidden" means NOT DISPLAYED: `BrowserWindow.hide()` on every window, no taskbar button, no always-on-top overlay, no tray unless the owner
 * chose it. It is not a way to be unseen by screen sharing, recording or monitoring software, and nothing here detects such software
 * (D78, explicit non-goal).
 *
 * Pure: no electron import. The windows are anything with the small `GateWindow` surface, so the whole contract is tested with fakes.
 */

export type GateKind = 'window' | 'notification' | 'devtools' | 'tray' | 'other'

/** The part of a BrowserWindow that the gate needs. */
export interface GateWindow {
  isDestroyed: () => boolean
  isVisible: () => boolean
  hide: () => void
  /** Shows the window without taking the focus; falls back to `show`. */
  showInactive?: () => void
  show?: () => void
  setSkipTaskbar?: (skip: boolean) => void
  /** A stable identity for de-duplicating requests of one window. */
  id?: number
}

export interface GateState {
  hidden: boolean
  /** Epoch milliseconds when the current hiding began, or `null`. */
  hiddenSince: number | null
  /** How many requests wait. A number only. */
  queued: number
}

export interface UnhideResult {
  /** Windows that were on the screen before the hide and are shown again (not counted as waiting requests). */
  restored: number
  /** Requests that were held back and are delivered now, in the order they arrived. */
  delivered: number
  /** The hiding lasted longer than the forgotten-hidden limit: show one quiet pulse, nothing else. */
  forgotten: boolean
  hiddenForMs: number
}

export interface WindowGateOptions {
  now?: () => number
  /** Hours after which an unhide reports `forgotten`. 0 or less turns the guard off. Read at each unhide, so a setting change applies at once. */
  forgottenHours?: () => number
  /** Called after every change of the hidden flag or the queue count (a state with numbers only). */
  onChange?: (state: GateState) => void
  /** A log line; it never receives the content of a request, only a kind and a count. */
  log?: (message: string) => void
  /** The most requests kept; the oldest are dropped beyond it, so a stream of requests cannot grow the memory without limit. */
  maxQueued?: number
  /** Runs `fn` after `ms` milliseconds. Default: `setTimeout`, which never keeps the process alive. */
  schedule?: (fn: () => void, ms: number) => void
}

interface Pending {
  kind: GateKind
  key?: string
  deliver: () => void
  /** Delivered like the others, but not counted: a window that the start-up itself asked to show while the face starts hidden. */
  silent?: boolean
}

export interface HideOptions {
  /** The moment the hiding began, when it began in an earlier run (start hidden). Default: now. */
  since?: number
  /** Window requests that arrive before this time (epoch ms) are held but not counted: the start-up windows of a face that starts hidden. */
  quietUntil?: number
}

export interface WindowGate {
  isHidden: () => boolean
  state: () => GateState
  /**
   * The one door. Runs `deliver` at once and returns true while JARVIS is shown; while hidden, keeps it and returns false.
   * A second request with the same `key` replaces the first one (one window asked to show twice is shown once) and keeps its place.
   */
  request: (kind: GateKind, deliver: () => void, key?: string) => boolean
  /** Starts the hidden mode: hides every window that is on the screen and takes the taskbar buttons away. Returns false if it was hidden already. */
  hide: (windows: Iterable<GateWindow>, options?: HideOptions) => boolean
  /** A window that was created while hidden (or turns up): kept off the screen and out of the taskbar. */
  adopt: (window: GateWindow) => void
  /** Ends the hidden mode. Windows that were shown before come back first, then the held requests in order, once. */
  unhide: () => UnhideResult
  /** The taskbar request of a window while hidden is remembered here, so the unhide applies what the window wanted. */
  noteSkipTaskbar: (window: GateWindow, skip: boolean) => boolean
}

const DEFAULT_MAX_QUEUED = 200
const HOUR_MS = 3_600_000

export function createWindowGate(options: WindowGateOptions = {}): WindowGate {
  const now = options.now ?? Date.now
  const maxQueued = Math.max(1, options.maxQueued ?? DEFAULT_MAX_QUEUED)
  let hidden = false
  let hiddenSince: number | null = null
  let queue: Pending[] = []
  /** The windows that were on the screen when the hide began, in the order they were found. */
  let wasVisible: GateWindow[] = []
  /** The taskbar choice of each window that was changed while hidden. */
  let wantedSkip = new Map<GateWindow, boolean>()
  let dropped = 0
  let quietUntil = 0
  /** True while the gate itself changes a taskbar button, so the Electron adapter does not take that call for the window's own wish. */
  let own = 0
  function asGate(fn: () => void) {
    own += 1
    try {
      fn()
    }
    finally {
      own -= 1
    }
  }

  const counted = () => queue.filter(entry => !entry.silent).length
  const state = (): GateState => ({ hidden, hiddenSince, queued: counted() })
  const changed = () => options.onChange?.(state())
  const log = (message: string) => options.log?.(`[jarvis:window-gate] ${message}`)

  function request(kind: GateKind, deliver: () => void, key?: string): boolean {
    if (!hidden) {
      deliver()
      return true
    }
    const silent = kind === 'window' && now() < quietUntil
    if (key !== undefined) {
      const same = queue.findIndex(entry => entry.key === key && entry.kind === kind)
      if (same >= 0) {
        queue[same] = { kind, key, deliver, silent: queue[same]!.silent && silent }
        return false
      }
    }
    queue.push({ kind, key, deliver, silent })
    if (queue.length > maxQueued) {
      queue.shift()
      dropped += 1
    }
    log(`held a ${kind} request (${counted()} waiting)`)
    if (!silent)
      changed()
    return false
  }

  function keepOff(window: GateWindow) {
    try {
      if (!window.isDestroyed()) {
        // always, not only when it reports visible: a window that was just created with `show: true` can still be mapping and not say so yet
        window.hide()
        asGate(() => window.setSkipTaskbar?.(true))
      }
    }
    catch (error) {
      log(`could not keep a window off the screen: ${String((error as Error)?.message ?? error)}`)
    }
  }

  function hide(windows: Iterable<GateWindow>, hideOptions: HideOptions = {}): boolean {
    if (hidden)
      return false
    hidden = true
    hiddenSince = hideOptions.since ?? now()
    quietUntil = hideOptions.quietUntil ?? 0
    queue = []
    dropped = 0
    wantedSkip = new Map()
    wasVisible = []
    for (const window of windows) {
      let visible = false
      try {
        visible = !window.isDestroyed() && window.isVisible()
      }
      catch {
        visible = false
      }
      if (visible)
        wasVisible.push(window)
      keepOff(window)
    }
    log(`hidden (${wasVisible.length} windows taken off the screen)`)
    changed()
    return true
  }

  /**
   * A window that was just created with `show: true` is shown by the toolkit itself, before any JavaScript can stop it, and on some systems it is
   * still mapping when the first `hide()` runs (Linux). So a new window is kept off now and checked again a moment later.
   */
  const RECHECK_MS = [0, 80, 300, 1000]
  const schedule = options.schedule ?? ((fn: () => void, ms: number) => {
    setTimeout(fn, ms).unref?.()
  })

  function adopt(window: GateWindow) {
    if (!hidden)
      return
    keepOff(window)
    for (const ms of RECHECK_MS) {
      schedule(() => {
        try {
          if (hidden && !window.isDestroyed() && window.isVisible())
            keepOff(window)
        }
        catch {}
      }, ms)
    }
  }

  function noteSkipTaskbar(window: GateWindow, skip: boolean): boolean {
    if (!hidden || own > 0)
      return false
    wantedSkip.set(window, skip)
    try {
      if (!window.isDestroyed())
        asGate(() => window.setSkipTaskbar?.(true))
    }
    catch {}
    return true
  }

  function unhide(): UnhideResult {
    if (!hidden)
      return { restored: 0, delivered: 0, forgotten: false, hiddenForMs: 0 }
    const hiddenForMs = Math.max(0, now() - (hiddenSince ?? now()))
    const limitHours = options.forgottenHours?.() ?? 0
    const forgotten = limitHours > 0 && hiddenForMs > limitHours * HOUR_MS
    const pending = queue
    const before = wasVisible
    const skips = wantedSkip
    hidden = false
    hiddenSince = null
    queue = []
    wasVisible = []
    wantedSkip = new Map()
    if (dropped > 0)
      log(`${dropped} oldest requests were dropped while hidden`)
    dropped = 0

    let restored = 0
    for (const window of before) {
      try {
        if (window.isDestroyed())
          continue
        // the taskbar button the window had before the hide (a window the owner never wanted in the taskbar stays out of it)
        asGate(() => window.setSkipTaskbar?.(skips.get(window) ?? false))
        if (window.showInactive)
          window.showInactive()
        else
          window.show?.()
        restored += 1
      }
      catch (error) {
        log(`could not show a window again: ${String((error as Error)?.message ?? error)}`)
      }
    }
    for (const [window, skip] of skips) {
      try {
        if (!window.isDestroyed() && !before.includes(window))
          asGate(() => window.setSkipTaskbar?.(skip))
      }
      catch {}
    }

    // A window that was on the screen before the hide has just been shown again: its own held "show" is the same thing, not one more request.
    const shownAgain = new Set(before.map(window => window.id).filter((id): id is number => typeof id === 'number'))
    const waiting = pending.filter(entry => entry.kind !== 'window' || !entry.key?.startsWith('win:') || !shownAgain.has(Number(entry.key.slice(4))))

    // in order, once; a request that fails does not stop the ones behind it
    let delivered = 0
    for (const entry of waiting) {
      try {
        entry.deliver()
        if (!entry.silent)
          delivered += 1
      }
      catch (error) {
        log(`a held ${entry.kind} request failed: ${String((error as Error)?.message ?? error)}`)
      }
    }
    log(`shown again (${restored} windows, ${delivered} held requests${forgotten ? ', hidden for a long time' : ''})`)
    changed()
    return { restored, delivered, forgotten, hiddenForMs }
  }

  return {
    isHidden: () => hidden,
    state,
    request,
    hide,
    adopt,
    unhide,
    noteSkipTaskbar,
  }
}
