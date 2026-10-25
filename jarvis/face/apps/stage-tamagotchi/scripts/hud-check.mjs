/* eslint-disable no-console */
import http from 'node:http'
import process from 'node:process'

import { createReadStream, existsSync, mkdirSync, mkdtempSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'
import { build } from 'vite'

/**
 * JARVIS: draws the HUD on its own in Chromium (scripts/hud-harness), takes screenshots at the window sizes of the face and checks the layout.
 *
 *   node scripts/hud-check.mjs [screenshot-folder]     default folder: /tmp/claude-0/hud-shots
 *
 * The face's default window is only 252 x 336 px, so the HUD answers the width of the window. It fails (exit 1) when, at any width from
 * 200 to 1400 px, in any of the six states and any kernel link,
 *   - the HUD, its strip or the page gets a sideways overflow, or a segment that shows is cut off,
 *   - the reactor (the ring) is hidden or out of the window,
 *   - nothing of the strip shows at all (state word, kernel link or notices).
 * Needs Chromium (set CHROMIUM_PATH when Playwright's own download is not installed) and no network.
 */
const here = resolve(fileURLToPath(new URL('.', import.meta.url)))
const harness = join(here, 'hud-harness')
const shots = resolve(process.argv[2] ?? '/tmp/claude-0/hud-shots')
mkdirSync(shots, { recursive: true })
const work = mkdtempSync(join(tmpdir(), 'hud-check-'))
const dist = join(work, 'dist')
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }

const failures = []
function check(name, ok, detail = '') {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? `  (${detail})` : ''}`)
  if (!ok)
    failures.push(name)
}

const unexpected = []
const server = http.createServer((request, response) => {
  const path = decodeURIComponent(new URL(request.url, 'http://x').pathname)
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
const STATES = ['idle', 'listening', 'thinking', 'speaking', 'acting', 'offline']
const SIZES = [[252, 336], [320, 480], [480, 800], [900, 600], [1280, 720]]
const url = o => `${origin}/index.html?${new URLSearchParams(Object.entries(o).map(([k, v]) => [k, String(v)]))}`
function options(state, extra = {}) {
  return {
    state,
    notices: state === 'idle' ? 0 : 2,
    ...(state === 'acting' ? { busy: 1 } : {}),
    ...(state === 'offline' ? { link: 'down' } : {}),
    ...extra,
  }
}

/** Measures the HUD in the page: the overflow of the page, the chrome and the strip, the reactor, and every segment that shows. */
function measure() {
  const W = window.innerWidth
  const chrome = document.querySelector('.jhud-chrome').getBoundingClientRect()
  const strip = document.querySelector('.jhud-strip')
  const stripRect = strip.getBoundingClientRect()
  const reactor = document.querySelector('.jhud-reactor-wrap').getBoundingClientRect()
  const segs = [...strip.querySelectorAll('.jhud-seg')].filter(e => getComputedStyle(e).display !== 'none')
  const clipped = segs.find((e) => {
    if (e.classList.contains('jhud-seg-model'))
      return false // the model name is the one segment that is allowed an ellipsis
    const range = document.createRange()
    range.selectNodeContents(e)
    const box = e.getBoundingClientRect()
    const text = range.getBoundingClientRect()
    return text.right > box.right - Number.parseFloat(getComputedStyle(e).paddingRight) + 0.5 || text.left < box.left - 0.5 || e.scrollWidth > e.clientWidth + 1
  })
  return {
    docOverflow: document.documentElement.scrollWidth > W,
    chromeOut: chrome.left < -0.5 || chrome.right > W + 0.5,
    stripOut: stripRect.left < -0.5 || stripRect.right > W + 0.5,
    stripClip: strip.scrollWidth > strip.clientWidth + 1,
    reactorHidden: !(reactor.width > 20) || !(reactor.height > 20) || !(reactor.left >= -0.5) || !(reactor.right <= W + 0.5),
    clipped: clipped ? `${clipped.className}: ${clipped.textContent.trim()}` : '',
    segments: segs.length,
  }
}

try {
  process.env.HUD_HARNESS_OUT = dist
  await build({ configFile: join(harness, 'vite.config.mjs') })
  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
  const errors = []

  // 1. screenshots: every state, subtle and full, at the five window sizes
  for (const [w, h] of SIZES) {
    for (const look of ['subtle', 'full']) {
      for (const state of STATES) {
        const context = await browser.newContext({ viewport: { width: w, height: h } })
        const page = await context.newPage()
        page.on('pageerror', error => errors.push(String(error)))
        await page.goto(url(options(state, { mode: look })))
        if (state === 'speaking')
          await page.evaluate(() => { window.hud.level = 0.6 })
        await page.waitForTimeout(400)
        await page.screenshot({ path: join(shots, `hud-${w}x${h}-${look}-${state}.png`) })
        const m = await page.evaluate(measure)
        check(`${state} (${look}) fits at ${w}x${h}`, !m.docOverflow && !m.chromeOut && !m.stripOut && !m.stripClip && !m.reactorHidden && !m.clipped && m.segments > 0, m.clipped)
        await context.close()
      }
    }
  }

  // 2. widths from 200 to 1400 px: nothing overflows or is cut off, the ring always shows
  const context = await browser.newContext({ viewport: { width: 1400, height: 400 } })
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(String(error)))
  let samples = 0
  const failed = []
  for (const state of STATES) {
    for (const link of ['up', 'down', 'killed']) {
      for (const notices of [0, 2, 123]) {
        await page.goto(url(options(state, { mode: 'full', link, notices, approvals: notices ? 1 : 0, busy: 1, model: 'a-very-long-model-name-that-keeps-going-GLM-5.3-Flash' })))
        for (let w = 200; w <= 1400; w += 8) {
          await page.setViewportSize({ width: w, height: 400 })
          samples++
          const m = await page.evaluate(measure)
          if (m.docOverflow || m.chromeOut || m.stripOut || m.stripClip || m.reactorHidden || m.clipped || m.segments === 0)
            failed.push(`${state}/${link}/${notices}@${w}: ${JSON.stringify(m)}`)
        }
      }
    }
  }
  check(`no overflow, no cut-off segment, the ring always shows (${samples} samples, 200 to 1400 px)`, failed.length === 0, failed.slice(0, 3).join(' | '))
  await context.close()

  check('no request for any outside address', unexpected.length === 0, unexpected.join(' | '))
  check('no page error', errors.length === 0, errors.join(' | '))
  await browser.close()
}
finally {
  server.close()
  rmSync(work, { recursive: true, force: true })
}

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed: ${failures.join('; ')}`)
  process.exit(1)
}
console.log(`\nall checks passed; screenshots are in ${shots}`)
