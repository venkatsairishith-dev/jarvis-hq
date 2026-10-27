/**
 * D78: the hidden mode of the face, on top of the window gate.
 *
 * Hidden = JARVIS is NOT DISPLAYED: every window is hidden (BrowserWindow.hide(), not minimised, not transparent), there is no taskbar
 * button, no always-on-top overlay, the tray icon is removed unless the owner chose to keep it, and nothing can open a window (the gate
 * holds the requests and counts them). Work goes on in the background. It is NOT a way to be unseen by screen sharing, recording or
 * monitoring software: nothing here touches content protection, the process, the task list or any window class, and nothing detects such
 * software. The tray, the process and the taskbar are what they honestly are.
 *
 * Safety rules, each with a test (hidden-mode.test.ts):
 * - the hidden state survives a restart ONLY when the owner turned on "start hidden"; otherwise JARVIS always shows its orb at start;
 * - a first run and the first run after an update always show;
 * - if the hide shortcut could not be registered, the tray icon stays while hidden (and the voice phrase still works), so JARVIS can
 *   never be lost invisible;
 * - a long hide (default 12 hours) ends with ONE quiet pulse on the next unhide, nothing else.
 *
 * Pure: windows, tray and persistence are passed in.
 */

import type { HiddenState, LookSettings } from '../../shared/jarvis-look'
import type { GateWindow, UnhideResult, WindowGate } from './window-gate'

export interface PersistedHiddenState {
  hidden: boolean
  /** Epoch ms of the hide, for the forgotten-hidden guard after a restart that stays hidden. */
  since: number
  /** The face version that wrote it: the first start after an update always shows. */
  version: string
}

export interface StartupInputs {
  persisted: PersistedHiddenState | undefined | null
  startHidden: boolean
  version: string
  /** True when this profile never ran before (no saved state at all). */
  firstRun: boolean
}

/** Whether the face starts hidden. Everything that is not a clear, deliberate yes is no. */
export function resolveStartHidden(input: StartupInputs): boolean {
  if (input.firstRun || !input.startHidden)
    return false
  const saved = input.persisted
  if (!saved || saved.hidden !== true)
    return false
  // a first start after an update, or after a downgrade, always shows
  if (!input.version || saved.version !== input.version)
    return false
  return true
}

export interface HiddenModeDeps {
  gate: WindowGate
  /** Every window of the face that exists now. */
  getWindows: () => GateWindow[]
  settings: () => Pick<LookSettings, 'showTrayWhileHidden' | 'startHidden'>
  /** Whether the hide shortcut is registered. When it is not, the tray stays while hidden. */
  hideShortcutOk: () => boolean
  /** Shows or removes the tray icon. */
  setTrayVisible: (visible: boolean) => void
  /** Writes the state (only called while "start hidden" is on) or removes it (null). */
  persist: (state: PersistedHiddenState | null) => void
  /** Tells the windows (counts only). */
  emit: (state: HiddenState) => void
  /** After an unhide: makes sure the orb (the main window) is on the screen even if it was folded before the hide. */
  ensureOrbShown: () => void
  version: string
  now?: () => number
  log?: (message: string) => void
}

export interface HiddenMode {
  state: () => HiddenState
  hide: () => boolean
  show: () => UnhideResult | undefined
  toggle: () => void
  /** The owner changed "show tray icon while hidden" or "start hidden" while hidden: apply it now. */
  settingsChanged: () => void
  /** Called once at start-up with the answer of `resolveStartHidden`. */
  startHidden: (since: number) => void
  /** The tray was created (maybe after the hide began): put its visibility right for the current state. */
  syncTray: () => void
}

/** How long after a start-hidden the windows that the start-up itself shows are not counted as waiting requests. */
export const START_QUIET_MS = 90_000

export function createHiddenMode(deps: HiddenModeDeps): HiddenMode {
  const now = deps.now ?? Date.now
  let unhideSeq = 0
  let lastUnhide: { queued: number, forgotten: boolean } = { queued: 0, forgotten: false }

  function trayShouldShow(): boolean {
    // the owner's choice, or the safety net when the shortcut is not there
    return deps.settings().showTrayWhileHidden || !deps.hideShortcutOk()
  }

  function state(): HiddenState {
    return { hidden: deps.gate.isHidden(), queued: deps.gate.isHidden() ? deps.gate.state().queued : lastUnhide.queued, forgotten: lastUnhide.forgotten, unhideSeq }
  }

  function emit() {
    deps.emit(state())
  }

  function persistNow(since: number) {
    // only a deliberate "start hidden" lets the hidden state outlive the process
    if (deps.settings().startHidden)
      deps.persist({ hidden: true, since, version: deps.version })
    else
      deps.persist(null)
  }

  function hide(): boolean {
    const started = deps.gate.hide(deps.getWindows())
    if (!started)
      return false
    deps.setTrayVisible(trayShouldShow())
    persistNow(now())
    lastUnhide = { queued: 0, forgotten: false }
    emit()
    return true
  }

  function show(): UnhideResult | undefined {
    if (!deps.gate.isHidden())
      return undefined
    const result = deps.gate.unhide()
    deps.persist(null)
    deps.setTrayVisible(true)
    deps.ensureOrbShown()
    unhideSeq += 1
    lastUnhide = { queued: result.delivered, forgotten: result.forgotten }
    emit()
    return result
  }

  return {
    state,
    hide,
    show,
    toggle: () => {
      if (deps.gate.isHidden())
        show()
      else
        hide()
    },
    settingsChanged: () => {
      if (!deps.gate.isHidden())
        return
      deps.setTrayVisible(trayShouldShow())
      const since = deps.gate.state().hiddenSince ?? now()
      persistNow(since)
    },
    syncTray: () => deps.setTrayVisible(deps.gate.isHidden() ? trayShouldShow() : true),
    startHidden: (since) => {
      // keep the old time, so the forgotten-hidden guard counts from the real hide; the windows the start-up shows are not "waiting"
      deps.gate.hide(deps.getWindows(), { since, quietUntil: now() + START_QUIET_MS })
      deps.setTrayVisible(trayShouldShow())
      emit()
    },
  }
}
