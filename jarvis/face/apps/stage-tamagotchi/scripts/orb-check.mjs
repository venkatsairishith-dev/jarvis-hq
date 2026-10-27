import process from 'node:process'

/* eslint-disable no-console */
/**
 * JARVIS (D78): starts the real built app (out/main) under a virtual display, with REAL X11 input, and checks the orb look and the hidden mode
 * end to end in Electron: the window it creates, the window gate in front of every show path, the global shortcuts, a drag with snapping, the
 * menu, the live switch to the avatar, an existing profile, and "start hidden".
 *
 *   xvfb-run -a -s "-screen 0 1280x800x24" node scripts/orb-check.mjs [--shots DIR]     Linux, Xvfb, python3 and the Electron binary
 *
 * Needs `pnpm run build` first. Exit 1 when a check fails. It runs the app several times, each with a fresh profile folder. It starts no
 * program but the app, uses no network, and sends input only to its own virtual display.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { _electron as electron } from 'playwright'

const app = fileURLToPath(new URL('..', import.meta.url))
const args = process.argv.slice(2)
const shots = args.includes('--shots') ? resolve(args[args.indexOf('--shots') + 1]) : ''
if (shots)
  mkdirSync(shots, { recursive: true })
const failures = []
function check(ok, name, detail = '') {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? `  (${detail})` : ''}`)
  if (!ok)
    failures.push(name)
}
const sleep = ms => new Promise(done => setTimeout(done, ms))
const xtest = (...a) => execFileSync('python3', [join(app, 'scripts', 'xtest.py'), ...a.map(String)])
const env = { ...process.env }
delete env.ELECTRON_RUN_AS_NODE

async function launch(userData) {
  const launched = await electron.launch({ env, cwd: app, args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', `--user-data-dir=${userData}`, 'out/main/index.js'] })
  // the app's own log lines about the look, so a failing check says why
  launched.lookLog = []
  for (const stream of [launched.process().stdout, launched.process().stderr]) {
    stream?.on('data', (chunk) => {
      for (const line of String(chunk).replace(/\u001B\[[0-9;]*m/g, '').split('\n')) {
        if (/jarvis:look|jarvis:window-gate/.test(line))
          launched.lookLog.push(line.trim().slice(0, 200))
      }
    })
  }
  return launched
}
const windowsOf = launched => launched.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().map(w => ({ id: w.id, url: w.webContents.getURL(), visible: w.isVisible(), bounds: w.getBounds(), onTop: w.isAlwaysOnTop(), resizable: w.isResizable() })))
async function waitFor(fn, ms = 20000, step = 250) {
  const end = Date.now() + ms
  while (Date.now() < end) {
    const value = await fn()
    if (value)
      return value
    await sleep(step)
  }
  return undefined
}
const mainOf = windows => windows.find(w => /jarvis-look=/.test(w.url))
const inside = (b, a) => b.x >= a.x && b.y >= a.y && b.x + b.width <= a.x + a.width && b.y + b.height <= a.y + a.height
const rootShot = name => shots && execFileSync('import', ['-window', 'root', join(shots, `${name}.png`)], { env })

// ---- 1. a NEW profile: the orb, in the corner, small, above the taskbar; the gate, the shortcuts, a drag, the menu, the switch to the avatar
{
  const userData = mkdtempSync(join(tmpdir(), 'jarvis-orb-new-'))
  const launched = await launch(userData)
  try {
    const found = await waitFor(async () => mainOf(await windowsOf(launched)))
    check(!!found, 'new profile: the main window opens', launched.lookLog.slice(0, 2).join(' / '))
    const work = await launched.evaluate(({ screen }) => screen.getPrimaryDisplay().workArea)
    const main = found
    check(/jarvis-look=orb/.test(main.url), 'new profile: the page starts as the orb', main.url.slice(-80))
    check(main.bounds.width <= 200 && main.bounds.height <= 200, 'the orb window is small', JSON.stringify(main.bounds))
    check(inside(main.bounds, work), 'the orb window is inside the work area', `${JSON.stringify(main.bounds)} in ${JSON.stringify(work)}`)
    check(main.resizable === false, 'the orb window is not resizable')
    const shownNow = await waitFor(async () => (await windowsOf(launched)).find(w => w.id === main.id)?.visible, 10000)
    check(!!shownNow, 'the orb is shown')
    if (process.env.VERBOSE)
      console.log((await windowsOf(launched)).map(w => `${w.id} ${w.visible ? 'visible' : 'hidden '} ${JSON.stringify(w.bounds)} ${w.url.slice(-60)}`).join('\n'))
    const page = launched.windows().find(w => /jarvis-look=/.test(w.url()))
    if (process.env.VERBOSE) {
      for (let i = 0; i < 14; i++) {
        const list = await windowsOf(launched)
        console.log(`t+${i * 0.5}s`, list.map(w => `${w.id}:${w.visible ? 'V' : 'h'}:${w.bounds.x},${w.bounds.y},${w.bounds.width}x${w.bounds.height}`).join('  '))
        await sleep(500)
      }
    }
    await page.waitForSelector('[data-testid="orb"] .orb-disc', { timeout: 30000 })
    await sleep(1500)
    const probe = await page.evaluate(() => ({
      canvases: document.querySelectorAll('canvas').length,
      resources: performance.getEntriesByType('resource').map(r => r.name.split('/').pop()),
      label: document.querySelector('.orb-disc')?.getAttribute('aria-label'),
      cubism: typeof window.Live2DCubismCore,
    }))
    check(probe.canvases === 0, 'no canvas in the orb window')
    check(probe.cubism === 'undefined' && !probe.resources.some(r => /live2dcubismcore/.test(r)), 'the Live2D core is not loaded in the orb window')
    check(!probe.resources.some(r => /^(?:AvatarStage|live2d-preview|live2d-zip-loader|live2d-opfs|vrm-preview|mmd-preview)/.test(r)), 'no avatar chunk is fetched in the orb window', probe.resources.filter(r => /live2d|AvatarStage|vrm|mmd|three|pixi/.test(r)).join(', '))
    check(/Ready|Offline|Something is wrong/.test(probe.label ?? ''), 'the orb button has a name', probe.label)
    rootShot('orb-new-profile')

    const shortcuts = await launched.evaluate(({ globalShortcut }) => [globalShortcut.isRegistered('Control+Alt+J'), globalShortcut.isRegistered('Control+Alt+Shift+J')])
    check(shortcuts[0] && shortcuts[1], 'the hide and reset shortcuts are registered with globalShortcut', JSON.stringify(shortcuts))

    // a drag with real pointer input: the orb follows the cursor and snaps to the nearest edge
    const before = (await windowsOf(launched)).find(w => w.id === main.id).bounds
    if (process.env.VERBOSE)
      console.log('before the drag', JSON.stringify(before), (await windowsOf(launched)).map(w => `${w.id}:${w.visible}:${JSON.stringify(w.bounds)}`).join(' '))
    const cx = before.x + Math.round(before.width / 2)
    const cy = before.y + Math.round(before.height / 2)
    xtest('move', cx, cy)
    await sleep(300)
    xtest('down')
    await sleep(150)
    for (let i = 1; i <= 12; i++) {
      xtest('move', cx - i * 20, cy - i * 20)
      await sleep(40)
    }
    await sleep(150)
    xtest('up')
    await sleep(800)
    const after = (await windowsOf(launched)).find(w => w.id === main.id).bounds
    check(after.x < before.x - 100 && after.y < before.y - 60, 'a drag moves the orb', `${before.x},${before.y} -> ${after.x},${after.y}`)
    check(inside(after, work), 'the dragged orb is inside the work area')
    // a second drag to within 28 px of the left edge: it snaps to the edge, leaving the 12 px margin
    const at = after
    const px = at.x + Math.round(at.width / 2)
    const py = at.y + Math.round(at.height / 2)
    xtest('move', px, py)
    await sleep(300)
    xtest('down')
    await sleep(150)
    for (let i = 1; i <= 10; i++) {
      xtest('move', px - Math.round((at.x - 20) * i / 10), py)
      await sleep(40)
    }
    await sleep(150)
    xtest('up')
    await sleep(800)
    const snapped = (await windowsOf(launched)).find(w => w.id === main.id).bounds
    check(snapped.x === work.x + 12, 'the orb snaps to the left edge with a 12 px margin', `${at.x} -> ${snapped.x}`)

    // the reset shortcut puts it back in the bottom right corner
    xtest('key', 'ctrl+alt+shift+j')
    const reset = await waitFor(async () => {
      const b = (await windowsOf(launched)).find(w => w.id === main.id).bounds
      return b.x > work.x + work.width / 2 && b.y > work.y + work.height / 2 ? b : undefined
    }, 5000)
    check(!!reset && inside(reset, work), 'the reset shortcut puts the orb back in the bottom right corner', JSON.stringify(reset))

    // right-click: the menu opens, the window grows, Escape closes it
    const zone = (await windowsOf(launched)).find(w => w.id === main.id).bounds
    xtest('move', zone.x + Math.round(zone.width / 2), zone.y + Math.round(zone.height / 2))
    await sleep(300)
    xtest('click', 'right')
    const menu = await page.waitForSelector('[role="menu"]', { timeout: 8000 }).catch(() => null)
    check(!!menu, 'right-click opens the menu')
    await sleep(600)
    const grown = (await windowsOf(launched)).find(w => w.id === main.id).bounds
    check(grown.width > zone.width && inside(grown, work), 'the window grows for the menu and stays inside the work area', JSON.stringify(grown))
    rootShot('orb-menu-open')
    await page.keyboard.press('Escape')
    const closed = await waitFor(async () => (await windowsOf(launched)).find(w => w.id === main.id).bounds.width <= 200, 5000)
    check(!!closed, 'Escape closes the menu and the window shrinks back')

    // ---- hide: a REAL global shortcut press hides every window, and nothing can spawn while hidden
    xtest('key', 'ctrl+alt+j')
    const hidden = await waitFor(async () => (await windowsOf(launched)).every(w => !w.visible), 6000)
    check(!!hidden, 'Ctrl+Alt+J hides every window (BrowserWindow.hide)', JSON.stringify((await windowsOf(launched)).map(w => w.visible)))
    const spawned = await launched.evaluate(({ BrowserWindow, Notification }) => {
      const w = new BrowserWindow({ show: true, width: 300, height: 200 })
      w.show()
      w.showInactive()
      w.focus()
      const n = new Notification({ title: 'x', body: 'y' })
      n.show()
      return { id: w.id }
    })
    await sleep(1500)
    const afterSpawn = await windowsOf(launched)
    check(afterSpawn.find(w => w.id === spawned.id)?.visible === false, 'a window created while hidden, with show: true, show() and focus(), stays off the screen')
    check(afterSpawn.every(w => !w.visible), 'still nothing on the screen after the attempts')
    const devtools = await launched.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows().some(w => w.webContents.isDevToolsOpened()))
    check(devtools === false, 'no DevTools window is open while hidden')
    rootShot('hidden')
    // unhide: the orb comes back, the held window is shown once
    xtest('key', 'ctrl+alt+j')
    const back = await waitFor(async () => {
      const list = await windowsOf(launched)
      return list.find(w => w.id === main.id)?.visible && list.find(w => w.id === spawned.id)?.visible
    }, 6000)
    check(!!back, 'unhide shows the orb again and delivers the window that was held, once')

    // ---- the live switch to the avatar, from the menu
    const z2 = (await windowsOf(launched)).find(w => w.id === main.id).bounds
    xtest('move', z2.x + Math.round(z2.width / 2), z2.y + Math.round(z2.height / 2))
    await sleep(300)
    xtest('click', 'right')
    await page.waitForSelector('[role="menu"]', { timeout: 8000 })
    await page.click('text=Avatar')
    const avatar = await waitFor(async () => {
      const w = (await windowsOf(launched)).find(x => x.id === main.id)
      return w && w.bounds.width >= 300 && w.resizable ? w : undefined
    }, 10000)
    check(!!avatar, 'switching to the Avatar look makes the window the avatar window again, live', JSON.stringify(avatar?.bounds))
    const loaded = await waitFor(() => page.evaluate(() => typeof window.Live2DCubismCore === 'object' || document.querySelectorAll('canvas').length > 0), 30000)
    check(!!loaded, 'the avatar page loads on demand (Live2D core and canvas appear only now)')
    rootShot('avatar-after-switch')
  }
  catch (error) {
    check(false, 'new profile run', String(error?.message ?? error).split('\n')[0])
  }
  finally {
    await launched.close().catch(() => {})
    rmSync(userData, { recursive: true, force: true })
  }
}

// ---- 2. an EXISTING profile keeps the avatar
let appVersion = '0.0.0'
{
  const userData = mkdtempSync(join(tmpdir(), 'jarvis-orb-old-'))
  writeFileSync(join(userData, 'app-config.json'), JSON.stringify({ windows: [] }))
  const launched = await launch(userData)
  try {
    const main = await waitFor(async () => mainOf(await windowsOf(launched)))
    appVersion = await launched.evaluate(({ app: electronApp }) => electronApp.getVersion())
    check(!!main && /jarvis-look=avatar/.test(main.url), 'existing profile: the page starts as the avatar', main?.url.slice(-60))
    check(!!main && main.bounds.width >= 300 && main.resizable === true, 'existing profile: the avatar window is the avatar window', JSON.stringify(main?.bounds))
  }
  catch (error) {
    check(false, 'existing profile run', String(error?.message ?? error).split('\n')[0])
  }
  finally {
    await launched.close().catch(() => {})
    rmSync(userData, { recursive: true, force: true })
  }
}

// ---- 3. "start hidden": only when chosen AND the last run ended hidden AND the version is the same; otherwise the orb is shown
const hiddenCases = [
  ['start hidden on and a hidden state of this version', true, { hidden: true, since: Date.now() - 1000, version: appVersion }, true],
  ['start hidden OFF with a hidden state on disk', false, { hidden: true, since: Date.now() - 1000, version: appVersion }, false],
  ['start hidden on but the state is from another version (an update)', true, { hidden: true, since: Date.now() - 1000, version: `${appVersion}-older` }, false],
]
for (const [label, startHidden, hiddenState, expectHidden] of hiddenCases) {
  const userData = mkdtempSync(join(tmpdir(), 'jarvis-orb-hidden-'))
  writeFileSync(join(userData, 'app-config.json'), JSON.stringify({ windows: [] }))
  writeFileSync(join(userData, 'jarvis-look-config.json'), JSON.stringify({ look: 'orb', startHidden }))
  writeFileSync(join(userData, 'jarvis-hidden-state.json'), JSON.stringify(hiddenState))
  const launched = await launch(userData)
  try {
    const main = await waitFor(async () => mainOf(await windowsOf(launched)))
    await sleep(expectHidden ? 4000 : 3000)
    const list = await windowsOf(launched)
    const shown = list.filter(w => w.visible).length
    check(expectHidden ? shown === 0 : list.find(w => w.id === main?.id)?.visible === true, `restart: ${label} -> ${expectHidden ? 'hidden' : 'the orb is shown'}`, `${shown} of ${list.length} windows visible: ${list.filter(w => w.visible).map(w => w.url.slice(-50)).join(' | ')}; ${launched.lookLog.slice(0, 3).join(' / ')}`)
  }
  catch (error) {
    check(false, `restart run (${label})`, String(error?.message ?? error).split('\n')[0])
  }
  finally {
    await launched.close().catch(() => {})
    rmSync(userData, { recursive: true, force: true })
  }
}

console.log(failures.length ? `\nFAIL: ${failures.length} check(s)` : '\nall checks passed')
process.exit(failures.length ? 1 : 0)
