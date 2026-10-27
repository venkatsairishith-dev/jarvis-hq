/**
 * D78: connects the look controller, the window gate, the hidden mode and the global shortcuts to the real Electron app.
 *
 * Everything that decides is in the pure modules next to this file (and tested there). This file only reads the screens and the cursor,
 * persists settings in the face's own config files under the user data folder, installs the gate on Electron's prototypes, registers the
 * two global shortcuts, and answers the windows. Presentation and shell only: no network, no secret, no change to the content policy.
 *
 * NOT done here, on purpose (D78 non-goal): nothing hides JARVIS from screen sharing, recording or monitoring software (no
 * setContentProtection, no display-affinity flag, no renamed process, no window-class trick) and nothing detects such software.
 */

import type { BrowserWindow, Display } from 'electron'

import type { HiddenState, LookSettings, OrbLayoutSnapshot, Rect, ShortcutStatus } from '../../shared/jarvis-look'
import type { JarvisLookSnapshot } from '../../shared/eventa/jarvis-look'
import type { StoredLook } from './look-config'
import type { DisplayInfo } from '../../shared/orb-layout'

import { existsSync } from 'node:fs'
import { join } from 'node:path'

import { useLogg } from '@guiiai/logg'
import { defineInvokeHandler } from '@moeru/eventa'
import { app, BrowserWindow as ElectronBrowserWindow, dialog, globalShortcut, Notification, screen } from 'electron'
import { any, boolean, number, object, optional, string } from 'valibot'

import {
  electronJarvisHiddenSet,
  electronJarvisLookChanged,
  electronJarvisLookGet,
  electronJarvisLookUpdate,
  electronJarvisOrbDrag,
  electronJarvisOrbNudge,
  electronJarvisOrbRequest,
  electronJarvisOrbReset,
  electronJarvisQuit,
} from '../../shared/eventa/jarvis-look'
import { createConfig } from '../libs/electron/persistence'
import { createWindowContext } from '../libs/electron/window-context'
import { createHiddenMode, resolveStartHidden } from './hidden-mode'
import { profileExisted } from './look-config'
import { createLookController } from './look-controller'
import { registerLookShortcuts, unregisterLookShortcuts } from './shortcuts'
import { createWindowGate } from './window-gate'
import { installWindowGate } from './window-gate-electron'

const lookConfigSchema = object({
  look: optional(string()),
  size: optional(string()),
  hideShortcut: optional(string()),
  resetShortcut: optional(string()),
  showTrayWhileHidden: optional(boolean()),
  startHidden: optional(boolean()),
  speakWhileHidden: optional(boolean()),
  forgottenHours: optional(number()),
  placements: optional(any()),
})

const hiddenStateSchema = object({
  hidden: boolean(),
  since: number(),
  version: string(),
})

export interface TrayPort {
  setVisible: (visible: boolean) => void
}

export interface LookService {
  readonly gate: ReturnType<typeof createWindowGate>
  readonly controller: ReturnType<typeof createLookController>
  /** The main window exists: the controller drives its bounds from now on. */
  attachMainWindow: (window: BrowserWindow, options: { avatarBounds: () => Rect }) => void
  /** The tray exists (or is created again): the hidden mode removes and restores it. */
  registerTray: (tray: TrayPort) => void
  snapshot: () => JarvisLookSnapshot
  /** The owner or a voice phrase hid or showed JARVIS. */
  setHidden: (hidden: boolean) => HiddenState
}

function displaysOf(list: Display[]): DisplayInfo[] {
  return list.map(d => ({ id: d.id, workArea: { ...d.workArea }, scaleFactor: d.scaleFactor }))
}

/**
 * Starts the look service. Call it ONCE, as early as possible after `app.whenReady()` and BEFORE any window is created, so the window gate is in
 * front of every window from the first one on.
 */
export function setupLookService(): LookService {
  const log = useLogg('jarvis:look').useGlobalConfig()
  const userData = app.getPath('userData')

  // ---- what is on disk
  const signs = {
    appConfig: existsSync(join(userData, 'app-config.json')),
    appOptions: existsSync(join(userData, 'app-options.json')),
    localStorage: existsSync(join(userData, 'Local Storage')),
    lookConfig: existsSync(join(userData, 'jarvis-look-config.json')),
  }
  const existingProfile = profileExisted(signs)
  const lookConfig = createConfig('jarvis-look', 'config.json', lookConfigSchema, { default: {} })
  lookConfig.setup()
  const hiddenConfig = createConfig('jarvis-hidden', 'state.json', hiddenStateSchema)
  hiddenConfig.setup()
  const previousHidden = hiddenConfig.get()

  // ---- the gate goes in front of every window, now
  let broadcast: (() => void) | undefined
  let unhideSeqState: HiddenState = { hidden: false, queued: 0, forgotten: false, unhideSeq: 0 }
  let shortcutStatus: ShortcutStatus = {
    hide: { accelerator: '', ok: false },
    reset: { accelerator: '', ok: false },
  }
  let trayPort: TrayPort | undefined
  let mainWindow: BrowserWindow | undefined

  const gate = createWindowGate({
    forgottenHours: () => controller.settings().forgottenHours,
    log: message => log.log(message),
    onChange: () => broadcast?.(),
  })
  installWindowGate(gate, {
    BrowserWindow: ElectronBrowserWindow as never,
    Notification: Notification as never,
    dialog: dialog as never,
    app: app as never,
  })

  // ---- the controller (settings, position, window around the orb)
  let avatarBounds: () => Rect = () => ({ x: 0, y: 0, width: 450, height: 600 })
  const contexts = new Set<ReturnType<typeof createWindowContext>['context']>()

  const controller = createLookController({
    window: () => mainWindow && !mainWindow.isDestroyed() ? mainWindow : undefined,
    displays: () => displaysOf(screen.getAllDisplays()),
    primaryId: () => screen.getPrimaryDisplay().id,
    cursor: () => screen.getCursorScreenPoint(),
    load: () => lookConfig.get() as StoredLook | undefined,
    save: stored => lookConfig.update(stored as never),
    existingProfile,
    avatarBounds: () => avatarBounds(),
    emit: () => broadcast?.(),
    log: message => log.log(message),
  })
  // the first start writes the choice down, so a profile that started as an avatar never flips to the orb by itself
  controller.persist()

  const hidden = createHiddenMode({
    gate,
    getWindows: () => ElectronBrowserWindow.getAllWindows(),
    settings: () => controller.settings(),
    hideShortcutOk: () => shortcutStatus.hide.ok,
    setTrayVisible: visible => trayPort?.setVisible(visible),
    persist: (state) => {
      if (state)
        hiddenConfig.update(state)
      else
        hiddenConfig.update({ hidden: false, since: 0, version: app.getVersion() })
    },
    emit: (state) => {
      unhideSeqState = state
      broadcast?.()
    },
    ensureOrbShown: () => {
      if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible())
        mainWindow.showInactive()
    },
    version: app.getVersion(),
    log: message => log.log(message),
  })

  function snapshot(): JarvisLookSnapshot {
    return {
      settings: controller.settings(),
      layout: controller.layout() as OrbLayoutSnapshot,
      shortcuts: shortcutStatus,
      hidden: { ...unhideSeqState, hidden: gate.isHidden(), queued: hidden.state().queued },
    }
  }

  broadcast = () => {
    const current = snapshot()
    for (const context of contexts) {
      try {
        context.emit(electronJarvisLookChanged, current)
      }
      catch {}
    }
  }

  // ---- the two global shortcuts (registered through Electron's globalShortcut)
  const driver = {
    register: (accelerator: string, callback: () => void) => globalShortcut.register(accelerator, callback),
    unregister: (accelerator: string) => globalShortcut.unregister(accelerator),
  }
  function registerShortcuts() {
    const settings = controller.settings()
    shortcutStatus = registerLookShortcuts(driver, {
      hide: { accelerator: settings.hideShortcut, onTriggered: () => hidden.toggle() },
      reset: { accelerator: settings.resetShortcut, onTriggered: () => controller.resetPosition() },
    }, shortcutStatus.hide.accelerator ? shortcutStatus : undefined, message => log.warn(message))
  }
  registerShortcuts()
  app.on('will-quit', () => unregisterLookShortcuts(driver, shortcutStatus))

  // ---- start hidden: only when the owner chose it and the last run ended hidden (and not on a first run or after an update)
  const startHidden = resolveStartHidden({
    persisted: previousHidden && previousHidden.hidden ? previousHidden : undefined,
    startHidden: controller.settings().startHidden,
    version: app.getVersion(),
    firstRun: !existingProfile,
  })
  if (startHidden && previousHidden)
    hidden.startHidden(previousHidden.since)
  else if (previousHidden?.hidden)
    hiddenConfig.update({ hidden: false, since: 0, version: app.getVersion() })

  // ---- the screens
  let lastDisplays = displaysOf(screen.getAllDisplays())
  let displayTimer: ReturnType<typeof setTimeout> | undefined
  function onDisplaysChanged() {
    clearTimeout(displayTimer)
    displayTimer = setTimeout(() => {
      const previous = lastDisplays
      lastDisplays = displaysOf(screen.getAllDisplays())
      controller.displaysChanged(previous)
    }, 150)
  }
  screen.on('display-added', onDisplaysChanged)
  screen.on('display-removed', onDisplaysChanged)
  screen.on('display-metrics-changed', onDisplaysChanged)

  // ---- the windows: each one can ask for the snapshot, and hears every change
  function attachWindow(window: BrowserWindow) {
    if (window.isDestroyed())
      return
    const { context } = createWindowContext(window)
    contexts.add(context)
    window.once('closed', () => contexts.delete(context))
    defineInvokeHandler(context, electronJarvisLookGet, () => snapshot())
    defineInvokeHandler(context, electronJarvisLookUpdate, (patch) => {
      const change = controller.update(patch ?? {})
      if (change.shortcuts)
        registerShortcuts()
      if (change.hiddenSettings)
        hidden.settingsChanged()
      broadcast?.()
      return snapshot()
    })
    defineInvokeHandler(context, electronJarvisOrbRequest, request => controller.request(request ?? { pill: false, card: null, menu: false }))
    defineInvokeHandler(context, electronJarvisOrbDrag, (payload) => {
      if (payload?.phase)
        controller.drag(payload.phase)
    })
    defineInvokeHandler(context, electronJarvisOrbNudge, (payload) => {
      if (payload?.direction)
        controller.nudge(payload.direction)
    })
    defineInvokeHandler(context, electronJarvisOrbReset, () => controller.resetPosition())
    defineInvokeHandler(context, electronJarvisHiddenSet, (payload) => {
      if (payload?.hidden)
        hidden.hide()
      else
        hidden.show()
      return snapshot().hidden
    })
    defineInvokeHandler(context, electronJarvisQuit, () => app.quit())
  }
  for (const window of ElectronBrowserWindow.getAllWindows())
    attachWindow(window)
  app.on('browser-window-created', (_event, window) => attachWindow(window))

  return {
    gate,
    controller,
    attachMainWindow: (window, options) => {
      mainWindow = window
      avatarBounds = options.avatarBounds
    },
    registerTray: (tray) => {
      trayPort = tray
      hidden.syncTray()
    },
    snapshot,
    setHidden: (next) => {
      if (next)
        hidden.hide()
      else
        hidden.show()
      return snapshot().hidden
    },
  }
}

export type { LookSettings }
