/* eslint-disable no-console, style/max-statements-per-line */
import http from 'node:http'
import process from 'node:process'

/**
 * JARVIS: renders the Timeline panel on its own in Chromium from views the real Python backend built (a journal written like the desktop plugin writes
 * it, real JPEG pictures, the real Timeline class), measures it and takes screenshots.
 *
 *   node scripts/timeline-check.mjs [screenshot-folder]     default folder: docs/timeline-panel (next to this script's app)
 *
 * It fails (exit 1) when
 *   - the panel takes 100 ms or more from "the server has the view" to "drawn" (two animation frames), for any view or size,
 *   - the rows the page draws are not the height the virtual list assumes (a wrong height makes the list jump when scrolled),
 *   - there is horizontal scroll at 252 (the face's own small window), 360, 480 or 1280 px wide, in dark or light,
 *   - a 3000-step journal draws more than 80 rows, cannot reach its last row, or a long task (200 ms or more) happens while drawing and scrolling it,
 *   - the keyboard does not work (focus into the list, arrows move, Enter opens a step and its pictures, U undoes the step, Escape closes, focus goes back),
 *   - a filter, Undo on a step or on a task, or the report of what was and was not undone, does not do what it says,
 *   - a step label that looks like markup becomes markup, anything in the page asks for an address that is not the local harness, or a page error happens,
 *   - reduced motion does not stop the animations.
 * Needs Chromium (Playwright's own, or CHROMIUM_PATH), python3 with Pillow, and no network.
 */
import { execFileSync } from 'node:child_process'
import { createReadStream, existsSync, mkdirSync, mkdtempSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'
import { build } from 'vite'

const here = resolve(fileURLToPath(new URL('.', import.meta.url)))
const harness = join(here, 'timeline-harness')
const shots = resolve(process.argv[2] ?? join(here, '..', 'docs', 'timeline-panel'))
mkdirSync(shots, { recursive: true })
const work = mkdtempSync(join(tmpdir(), 'timeline-check-'))
const dist = join(work, 'dist')
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' }

const failures = []
function check(name, ok, detail = '') {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? `  (${detail})` : ''}`)
  if (!ok)
    failures.push(name)
}

const unexpected = []
let fixturesFile = ''
const server = http.createServer((request, response) => {
  const path = decodeURIComponent(new URL(request.url, 'http://x').pathname)
  if (path === '/fixtures.json') {
    response.setHeader('Content-Type', 'application/json')
    createReadStream(fixturesFile).pipe(response)
    return
  }
  const file = join(dist, normalize(path === '/' ? 'index.html' : path).replace(/^([/\\])+/, ''))
  if (file.startsWith(dist) && existsSync(file) && statSync(file).isFile()) {
    response.setHeader('Content-Type', types[extname(file)] ?? 'application/octet-stream')
    createReadStream(file).pipe(response)
    return
  }
  unexpected.push(`${request.method} ${path}`)
  response.statusCode = 404
  response.end()
})
await new Promise(done => server.listen(0, '127.0.0.1', done))
const origin = `http://127.0.0.1:${server.address().port}`

// 252 x 336 is the face's own default window; the others are the owner's 360 x 640 phone-like case, a tablet-like one and a desktop one
const SIZES = [[252, 336], [360, 640], [480, 800], [1280, 800]]
const THEMES = ['dark', 'light']
const HEIGHTS = { wide: { task: 58, step: 46, detail: 214 }, narrow: { task: 74, step: 62, detail: 244 } }

try {
  fixturesFile = join(work, 'fixtures.json')
  execFileSync(process.env.PYTHON ?? 'python3', [join(harness, 'make_fixtures.py'), fixturesFile], { stdio: 'inherit' })
  process.env.TIMELINE_HARNESS_OUT = dist
  await build({ configFile: join(harness, 'vite.config.mjs') })

  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
  const page = await browser.newPage({ viewport: { width: 360, height: 640 } })
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(String(error)))
  page.on('console', (m) => {
    if (m.type() === 'error')
      pageErrors.push(m.text())
  })
  await page.goto(`${origin}/`, { waitUntil: 'load' })
  await page.waitForFunction(() => window.__tl)
  await page.evaluate(() => {
    window.__long = []
    new PerformanceObserver((list) => { for (const e of list.getEntries()) window.__long.push(Math.round(e.duration)) }).observe({ type: 'longtask', buffered: true })
  })

  const slow = []
  const overflow = []
  const badHeights = []
  const setTheme = theme => page.evaluate(t => document.documentElement.classList.toggle('dark', t === 'dark'), theme)
  const show = async (name) => {
    const ms = await page.evaluate(n => window.__tl.show(n), name)
    await page.waitForTimeout(260) // the 180 ms entrance finishes before the screenshot
    return ms
  }
  const noScroll = async (label) => {
    const w = await page.evaluate(() => ({
      doc: document.documentElement.scrollWidth - innerWidth,
      panel: (document.querySelector('.tl')?.scrollWidth ?? 0) - (document.querySelector('.tl')?.clientWidth ?? 0),
      list: (document.querySelector('.tl-list')?.scrollWidth ?? 0) - (document.querySelector('.tl-list')?.clientWidth ?? 0),
    }))
    if (w.doc > 0 || w.panel > 0 || w.list > 0)
      overflow.push(`${label} (page +${w.doc}, panel +${w.panel}, list +${w.list})`)
  }
  const rowHeights = async (label) => {
    const got = await page.evaluate(() => {
      const panel = document.querySelector('.tl')
      return {
        layout: panel?.classList.contains('tl-narrow') ? 'narrow' : 'wide',
        task: [...document.querySelectorAll('.tl-task')].map(e => e.getBoundingClientRect().height),
        step: [...document.querySelectorAll('.tl-step')].map(e => e.getBoundingClientRect().height),
        detail: [...document.querySelectorAll('.tl-detail')].map(e => e.getBoundingClientRect().height),
      }
    })
    for (const kind of ['task', 'step', 'detail']) {
      for (const h of got[kind]) {
        if (Math.abs(h - HEIGHTS[got.layout][kind]) > 0.6)
          badHeights.push(`${label}: a ${kind} row is ${h}px, the list assumes ${HEIGHTS[got.layout][kind]}px (${got.layout})`)
      }
    }
  }
  // the list draws only the rows in view: scroll until the step is one of them
  const reveal = async (text, exact = false) => {
    const step = (exact ? page.locator('.tl-step').filter({ has: page.locator('.tl-label', { hasText: new RegExp(`^${text}$`) }) }) : page.locator('.tl-step', { hasText: text })).first()
    for (let i = 0; i < 60 && await step.count() === 0; i++) {
      await page.locator('.tl-list').evaluate((el) => { el.scrollTop += el.clientHeight * 0.6 })
      await page.waitForTimeout(40)
    }
    await step.scrollIntoViewIfNeeded()
    return step
  }
  const openFirstStep = async (text) => {
    await (await reveal(text)).click()
    await page.waitForFunction(() => document.querySelectorAll('.tl-detail img').length > 0 || document.querySelectorAll('.tl-detail .tl-noshot').length > 0, null, { timeout: 5000 })
    await page.waitForTimeout(120)
  }
  const snap = async (name, w, h, theme, ms) => {
    await page.screenshot({ path: join(shots, `timeline-${name}-${w}x${h}-${theme}.png`) })
    if (ms >= 100)
      slow.push(`${name} ${w}x${h} ${theme}: ${ms.toFixed(0)} ms`)
    await noScroll(`${name} ${w}x${h} ${theme}`)
    await rowHeights(`${name} ${w}x${h} ${theme}`)
  }

  const timings = {}
  // the very first view the page ever draws (cold code)
  const cold = await page.evaluate(() => window.__tl.show('small'))
  check('the first view of all (cold) is drawn in under 100 ms', cold < 100, `${cold.toFixed(0)} ms`)

  for (const theme of THEMES) {
    await setTheme(theme)
    for (const [w, h] of SIZES) {
      await page.setViewportSize({ width: w, height: h })
      let ms = await show('small')
      timings[`small ${w}x${h} ${theme}`] = ms
      await snap('small', w, h, theme, ms) // the closed steps
      await openFirstStep('Open Calculator') // one step open: what was seen, how to undo, the before and after pictures
      await snap('open-step', w, h, theme, 0)
      const natural = await page.evaluate(() => [...document.querySelectorAll('.tl-detail img')].map(i => i.naturalWidth))
      check(`the open step shows both pictures at ${w}x${h} ${theme}`, natural.length === 2 && natural.every(n => n > 100), natural.join(','))
      if ((theme === 'dark' && w <= 360) || (theme === 'light' && w === 1280)) {
        for (const name of ['long', 'empty', 'unavailable']) {
          ms = await show(name)
          timings[`${name} ${w}x${h} ${theme}`] = ms
          await snap(name, w, h, theme, ms)
        }
        ms = await show('many')
        timings[`many ${w}x${h} ${theme}`] = ms
        await snap('many', w, h, theme, ms)
      }
    }
  }
  const times = Object.values(timings)
  console.log(`time to drawn: max ${Math.max(...times).toFixed(0)} ms, median ${times.sort((a, b) => a - b)[Math.floor(times.length / 2)].toFixed(0)} ms over ${times.length} views`)
  check('every view is drawn in under 100 ms from the moment the server has it', slow.length === 0, slow.join('; '))
  check('no horizontal scroll at 252, 360, 480 and 1280 px, dark and light', overflow.length === 0, overflow.join('; '))
  check('every row is exactly the height the virtual list assumes (wide and narrow)', badHeights.length === 0, badHeights.slice(0, 3).join('; '))

  // virtualization: 3000 steps in 150 tasks
  await setTheme('dark')
  await page.setViewportSize({ width: 360, height: 640 })
  await page.evaluate(() => { window.__long.length = 0 })
  const manyMs = await show('many')
  await page.waitForTimeout(150)
  const rowsInPage = await page.locator('.tl-step, .tl-task, .tl-detail').count()
  check('3000 steps (150 tasks) draw only the rows in view', rowsInPage > 5 && rowsInPage < 80, `${rowsInPage} rows in the page, drawn in ${manyMs.toFixed(0)} ms`)
  const spacer = await page.evaluate(() => { const l = document.querySelector('.tl-list'); return l.scrollHeight })
  check('the scroll height is the whole list', Math.abs(spacer - (150 * HEIGHTS.narrow.task + 3000 * HEIGHTS.narrow.step)) < 2, String(spacer)) // 360 px is the narrow layout
  const scroller = page.locator('.tl-list')
  for (const frac of [0.2, 0.5, 0.8, 1]) {
    await scroller.evaluate((el, f) => { el.scrollTop = (el.scrollHeight - el.clientHeight) * f }, frac)
    await page.waitForTimeout(80)
  }
  await page.waitForTimeout(150)
  const lastText = await page.locator('.tl-step').last().textContent()
  const where = await page.evaluate(() => { const l = document.querySelector('.tl-list'); return `scrollTop ${l.scrollTop} of ${l.scrollHeight}, ${l.clientHeight} high, ${document.querySelectorAll('.tl-step').length} steps drawn` })
  check('scrolling to the end reaches the oldest step', /Step 20 of task 149/.test(lastText ?? ''), `${(lastText ?? '').trim().slice(0, 40)}; ${where}`)
  check('after scrolling the page still holds few rows', await page.locator('.tl-step, .tl-task, .tl-detail').count() < 80)
  await page.screenshot({ path: join(shots, 'timeline-many-scrolled-360x640-dark.png') })
  const longTasks = await page.evaluate(() => window.__long.slice())
  check('no long task of 200 ms or more while drawing and scrolling 3000 steps', longTasks.every(d => d < 200), `long tasks (ms): ${longTasks.join(', ') || 'none'}`)

  // keyboard
  await page.evaluate(() => { window.__tl.clear(); const b = document.createElement('button'); b.id = 'opener'; b.textContent = 'x'; document.body.append(b); b.focus() })
  await show('small')
  check('focus moves into the list when the panel opens', await page.evaluate(() => document.activeElement?.classList.contains('tl-list')))
  await page.keyboard.press('ArrowDown')
  await page.keyboard.press('ArrowDown')
  const active = await page.locator('.tl-list').getAttribute('aria-activedescendant')
  check('an arrow key moves the selection and the list says which row', !!active && await page.locator(`[id="${active}"]`).count() === 1 && (await page.locator(`[id="${active}"]`).getAttribute('class'))?.includes('tl-sel'), String(active))
  const before = await page.locator('.tl-detail').count()
  await page.keyboard.press('Enter')
  await page.waitForFunction(() => document.querySelectorAll('.tl-detail').length > 0)
  check('Enter opens the selected step', before === 0 && await page.locator('.tl-detail').count() === 1)
  await page.keyboard.press('Enter')
  check('Enter again closes it', await page.locator('.tl-detail').count() === 0)
  await page.keyboard.press('End')
  await page.keyboard.press('Home')
  check('Home goes to the first row', (await page.locator('.tl-list').getAttribute('aria-activedescendant'))?.startsWith('tl-t:'))
  // U undoes the selected step if it can be undone: select the Notepad "Saved a .txt file" step
  const savedStep = await reveal('Saved a .txt file')
  await savedStep.locator('.tl-label').click() // selects it and opens it
  await page.locator('.tl-list').focus()
  await page.evaluate(() => { window.__tl.calls.length = 0 })
  await page.keyboard.press('u')
  await page.waitForTimeout(150)
  const undoCall = await page.evaluate(() => window.__tl.calls.find(c => c.name.endsWith('jarvis_timeline_undo')))
  check('U undoes the selected step, with the panel token and that step only', Array.isArray(undoCall?.args?.ids) && undoCall.args.ids.length === 1 && !!undoCall.args.panel, JSON.stringify(undoCall?.args))
  check('the report of what was undone appears', (await page.locator('.tl-report').textContent())?.includes('Undone'))
  await page.keyboard.press('Escape')
  await page.waitForTimeout(50)
  check('Escape closes the panel and focus goes back', await page.locator('.tl').count() === 0 && await page.evaluate(() => document.activeElement?.id === 'opener'))

  // filters
  await show('small')
  const allSteps = await page.locator('.tl-step').count()
  await page.locator('.tl-chip', { hasText: 'Failed only' }).click()
  const failedVerdicts = await page.locator('.tl-step').evaluateAll(els => els.map(e => e.getAttribute('data-verified')))
  check('Failed only shows only failed steps', failedVerdicts.length > 0 && failedVerdicts.length < allSteps && failedVerdicts.every(v => v === 'failed'), failedVerdicts.join(','))
  await page.screenshot({ path: join(shots, 'timeline-failed-only-360x640-dark.png') })
  await page.locator('.tl-chip', { hasText: 'Failed only' }).click()
  await page.locator('.tl-chip', { hasText: 'Today' }).click()
  const todayTasks = await page.locator('.tl-task-title').allTextContents()
  check('Today leaves out yesterday\'s task', !todayTasks.some(t => t.includes('Kalyani')) && todayTasks.some(t => t.includes('Calculator')), todayTasks.join(' | '))
  await page.locator('.tl-chip', { hasText: 'Today' }).click()

  // Undo this task, Undo on a step, the report
  await page.evaluate(() => { window.__tl.calls.length = 0; window.__tl.setUndoReply('mixed') })
  await (await reveal('Open Notepad', true)).scrollIntoViewIfNeeded()
  await page.locator('.tl-task', { hasText: 'Type hello in Notepad' }).locator('.tl-btn', { hasText: 'Undo this task' }).click()
  await page.waitForTimeout(150)
  const taskCall = await page.evaluate(() => window.__tl.calls.find(c => c.name.endsWith('jarvis_timeline_undo')))
  check('Undo this task sends that task only', taskCall?.args?.task === 't-pad' && !taskCall.args.ids, JSON.stringify(taskCall?.args))
  const reportText = (await page.locator('.tl-report').textContent()) ?? ''
  check('the report says what was and was not undone', reportText.includes('Undone:') && reportText.includes('Not undone:') && reportText.includes('unsaved work'), reportText.trim().slice(0, 80))
  await page.screenshot({ path: join(shots, 'timeline-report-360x640-dark.png') })
  await page.locator('.tl-report-x').click()
  check('the report can be dismissed', await page.locator('.tl-report').count() === 0)
  await page.evaluate(() => { window.__tl.calls.length = 0; window.__tl.setUndoReply('ok') })
  await (await reveal('Saved a .txt file')).locator('.tl-btn-undo').click()
  await page.waitForTimeout(150)
  const stepCall = await page.evaluate(() => window.__tl.calls.find(c => c.name.endsWith('jarvis_timeline_undo')))
  check('Undo on a step sends that step only', Array.isArray(stepCall?.args?.ids) && stepCall.args.ids.length === 1 && !stepCall.args.task, JSON.stringify(stepCall?.args))
  check('a step that cannot be undone has no Undo button and says why when opened', await (await reveal('Type 5 characters')).locator('.tl-btn-undo').count() === 0)
  await (await reveal('Type 5 characters')).click()
  check('the reason is shown', (await page.locator('.tl-detail-undo').first().textContent())?.includes('cannot be taken back'))
  check('an undone step is marked undone and has no button', await (await reveal('Open Notepad', true)).locator('.tl-done').count() === 1)

  // hostile text is drawn as text
  await show('small')
  await page.evaluate(() => {
    const v = JSON.parse(JSON.stringify(window.__tl.controller.view.value))
    v.tasks[0].title = '<b id="pwn-title">task</b>'
    v.tasks[0].steps[0].label = '<img src=x onerror="window.__pwned=1" id="pwn-img">label'
    window.__tl.controller.view.value = v
  })
  await page.waitForTimeout(100)
  check('a label that looks like markup is only text', await page.locator('#pwn-img, #pwn-title').count() === 0 && !(await page.evaluate(() => window.__pwned)))
  check('the panel has no link, frame, script or form element', await page.locator('.tl a, .tl iframe, .tl script, .tl form').count() === 0)

  // reduced motion
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await show('small')
  const animations = await page.evaluate(() => [document.querySelector('.tl'), ...document.querySelectorAll('.tl-skel')].map(e => getComputedStyle(e).animationName))
  check('prefers-reduced-motion stops the animations', animations.every(a => a === 'none'), animations.join(','))
  await page.emulateMedia({ reducedMotion: 'no-preference' })

  check('no request left the harness', unexpected.length === 0, unexpected.join(', '))
  check('no page error', pageErrors.length === 0, pageErrors.join(' | ').slice(0, 300))
  await browser.close()
}
finally {
  server.close()
}
if (failures.length) {
  console.log(`\n${failures.length} check(s) failed: ${failures.join('; ')}`)
  process.exit(1)
}
console.log(`\nall checks passed; screenshots are in ${shots}`)
