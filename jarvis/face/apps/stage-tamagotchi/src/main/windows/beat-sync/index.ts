import { initScreenCaptureForWindow } from '@proj-airi/electron-screen-capture/main'
import { BrowserWindow } from 'electron'

import { baseUrl, getPreloadPath, getRendererRoot, load } from '../../libs/electron/location'
import { excludeWindowFromTaskbar } from '../shared/app-icon'
import { deferStartup, STARTUP_STAGGER } from '../shared/startup-stagger'
import { protectPrivilegedWindowNavigation } from '../shared/window'

/**
 * Creates the hidden renderer that owns the Beat Sync capture and detector.
 *
 * D57: the window object exists at once, but its page loads a few seconds after start-up. The main window waits for this function,
 * so awaiting the page load here held the avatar back, and the page is a second copy of the renderer start-up that runs beside the
 * avatar's model load. Beat Sync is off until the owner turns it on in settings.
 */
export async function setupBeatSync() {
  const window = new BrowserWindow({
    show: false,
    skipTaskbar: true,
    webPreferences: {
      preload: getPreloadPath('beat-sync.mjs'),
      sandbox: false,
    },
  })

  excludeWindowFromTaskbar(window)
  protectPrivilegedWindowNavigation(window)

  deferStartup(async () => {
    await load(window, baseUrl(getRendererRoot(), 'beat-sync.html'))
    initScreenCaptureForWindow(window)
  }, STARTUP_STAGGER.beatSyncMs, {
    isAlive: () => !window.isDestroyed(),
    onError: error => console.error('Failed to load the Beat Sync page', error),
  })

  return window
}
