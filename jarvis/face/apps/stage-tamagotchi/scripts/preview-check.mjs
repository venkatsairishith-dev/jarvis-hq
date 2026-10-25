/* eslint-disable no-console */
import http from 'node:http'
import process from 'node:process'

/**
 * JARVIS: renders the Preview panel on its own in Chromium, takes screenshots and tries to break it.
 *
 *   node scripts/preview-check.mjs [screenshot-folder]     default folder: /tmp/claude-0/preview-shots
 *
 * Fixtures come from the real tool-server code (jarvis/bridge/output_preview.py via scripts/preview-harness/make_fixtures.py), so the
 * whole chain is exercised: Python payload -> panel logic -> drawn page. It fails (exit 1) when
 *   - anything in the page asks for the "beacon" address (a remote image, a link, a stylesheet, a form, a frame, a refresh),
 *   - a script inside shown HTML or text runs, or the HTML frame is not sandboxed without permissions,
 *   - a click on a link in shown content navigates the window,
 *   - a view does not show what it should (image, page images, thumbnails, highlighted code, table, open in app, paging, close).
 * Needs Chromium (set CHROMIUM_PATH when Playwright's own download is not installed), python3 with Pillow, and no network.
 */
import { execFileSync } from 'node:child_process'
import { createReadStream, existsSync, mkdirSync, mkdtempSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'
import { build } from 'vite'

const here = resolve(fileURLToPath(new URL('.', import.meta.url)))
const harness = join(here, 'preview-harness')
const shots = resolve(process.argv[2] ?? '/tmp/claude-0/preview-shots')
mkdirSync(shots, { recursive: true })
const work = mkdtempSync(join(tmpdir(), 'preview-check-'))
const dist = join(work, 'dist')
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' }

const failures = []
function check(name, ok, detail = '') {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? `  (${detail})` : ''}`)
  if (!ok)
    failures.push(name)
}

// 1. a local server that records every request it should never get
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

try {
  // 2. fixtures from the real Python side, and the harness page
  fixturesFile = join(work, 'fixtures.json')
  execFileSync(process.env.PYTHON ?? 'python3', [join(harness, 'make_fixtures.py'), fixturesFile, origin], { stdio: 'inherit' })
  process.env.PREVIEW_HARNESS_OUT = dist
  await build({ configFile: join(harness, 'vite.config.mjs') })

  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
  const page = await browser.newPage({ viewport: { width: 450, height: 600 } })
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(String(error)))
  await page.goto(`${origin}/`, { waitUntil: 'load' })
  await page.waitForFunction(() => window.__preview)
  const settle = () => page.waitForTimeout(500)
  const shot = name => page.screenshot({ path: join(shots, `${name}.png`) })
  const show = async (title, pageNo = 0) => {
    await page.evaluate(([t, n]) => window.__preview.controller.show(window.__preview.ids[t], n), [title, pageNo])
    await settle()
  }

  // 3. the closed panel, then opened with the button
  await shot('01-closed-450x600')
  await page.click('.pv-launch')
  await page.waitForSelector('.pv')
  await settle()
  check('opens from the launcher and shows something', (await page.locator('.pv-title').textContent()).length > 0)

  // 4. a deck: slide image, thumbnails, paging
  await show('Q3 board deck')
  await shot('02-deck-450x600')
  check('deck shows a slide image', await page.locator('.pv-pages .pv-image img').count() === 1)
  check('deck shows 4 thumbnails', await page.locator('.pv-thumb').count() === 4)
  check('deck says checks passed', (await page.locator('.pv-head').textContent()).includes('checks passed'))
  await page.locator('.pv').focus()
  await page.keyboard.press('ArrowRight')
  await settle()
  check('arrow key goes to slide 2', (await page.locator('.pv-sub').textContent()).includes('page 2 of 4'))
  await page.locator('.pv-thumb').nth(3).click()
  await settle()
  check('a thumbnail jumps to slide 4', (await page.locator('.pv-sub').textContent()).includes('page 4 of 4'))
  await shot('03-deck-slide4-450x600')

  // 5. a PDF drawn from its page image (no saved previews: rendered by the tool server)
  await show('Hyderabad flights')
  await shot('04-pdf-450x600')
  check('pdf shows a page image', await page.locator('.pv-pages .pv-image img').count() === 1)
  check('pdf says checks not passed', (await page.locator('.pv-head').textContent()).includes('checks not passed'))

  // 6. an image
  await show('Chart')
  await shot('05-image-450x600')
  check('image is drawn from a data URL', (await page.locator('.pv-image img').getAttribute('src'))?.startsWith('data:image/png;base64,'))

  // 7. markdown with a remote picture, a link, a table and a code block
  await show('Notes')
  await shot('06-markdown-450x600')
  const md = page.locator('.pv-rich')
  check('markdown has a heading, a list and highlighted code', await md.locator('h1').count() === 1 && await md.locator('li').count() === 2 && await md.locator('.shiki').count() >= 1)
  check('markdown has no image (the remote picture is gone)', await md.locator('img').count() === 0)
  check('markdown links have no target', await md.locator('a[href]').count() === 0)
  const before = page.url()
  for (const a of await md.locator('a').all())
    await a.click({ force: true }).catch(() => {})
  await settle()
  check('clicking a markdown link does not navigate', page.url() === before)

  // 8. code
  await show('Tools')
  await shot('07-code-450x600')
  check('code is highlighted', await page.locator('.pv-rich .shiki').count() >= 1)

  // 9. hostile HTML
  await show('Hostile')
  await page.waitForSelector('iframe.pv-frame')
  await settle()
  await shot('08-html-450x600')
  const frameEl = page.locator('iframe.pv-frame')
  check('html frame is sandboxed with no permissions', (await frameEl.getAttribute('sandbox')) === '')
  check('html frame has no-referrer and a default-src none policy', (await frameEl.getAttribute('referrerpolicy')) === 'no-referrer' && (await frameEl.getAttribute('csp'))?.includes('default-src \'none\''))
  const srcdoc = await frameEl.getAttribute('srcdoc')
  check('html source lost script, link, base, meta refresh, form and frame', !/<script|<link|<base|<form|<iframe|refresh|href=|onerror|onclick|127\.0\.0\.1/i.test(srcdoc.replace(/<meta http-equiv="Content-Security-Policy"[^>]*>/, '')))
  const frame = page.frames().find(f => f !== page.mainFrame())
  check('the heading from the HTML is shown', frame && (await frame.locator('h1').textContent()) === 'Sample page')
  check('no script ran in the frame', frame && (await frame.evaluate(() => window.__pwned)) === undefined)
  check('no script ran in the page', (await page.evaluate(() => window.__pwned)) === undefined)
  await frame.locator('a').first().click({ force: true }).catch(() => {})
  await frame.locator('p', { hasText: 'click me' }).click().catch(() => {})
  await settle()
  check('clicking in the frame does not navigate', page.url() === before)

  // 10. the list, and the second item
  await page.click('button[aria-label="List of recent things"]')
  await settle()
  await shot('09-list-450x600')
  check('the list shows 6 things', await page.locator('.pv-row').count() === 6)
  await page.locator('.pv-row', { hasText: 'Chart' }).click()
  await settle()
  check('picking from the list opens it', (await page.locator('.pv-title').textContent()) === 'Chart')

  // 11. open in app goes to open_output with the path the server named
  await page.click('.pv-primary')
  await settle()
  const opened = await page.evaluate(() => window.__preview.opened)
  check('open in app hands the file to open_output', opened.length === 1 && opened[0].startsWith('FIXTURE/'), opened.join(','))
  check('open in app says so', (await page.locator('.pv-foot').textContent()).includes('Opened in its app'))

  // 12. "show me X" arrives from the model's tool: the panel opens by itself
  await page.keyboard.press('Escape')
  await settle()
  check('Escape closes the panel', await page.locator('.pv').count() === 0)
  await page.evaluate(() => window.__preview.wanted.push(window.__preview.ids['Q3 board deck']))
  await page.evaluate(() => window.__preview.controller.pollWanted())
  await page.waitForSelector('.pv')
  await settle()
  check('a queued id opens the panel on that item', (await page.locator('.pv-title').textContent()) === 'Q3 board deck')

  // 13. a wide window, dark mode, and no sideways scroll at the app window size
  await page.setViewportSize({ width: 1100, height: 760 })
  await page.evaluate(() => document.documentElement.classList.add('dark'))
  check('dark mode switches the panel colors', (await page.evaluate(() => getComputedStyle(document.querySelector('.pv-root')).getPropertyValue('--pv-bg').trim())) === '#1d1c24')
  await page.click('button[aria-label="Make larger or smaller"]')
  await settle()
  await shot('10-deck-expanded-dark-1100x760')
  await show('Notes')
  await shot('11-markdown-expanded-dark-1100x760')
  await page.evaluate(() => document.documentElement.classList.remove('dark'))
  await page.setViewportSize({ width: 450, height: 600 })
  await settle()
  check('no sideways scroll at 450 px', await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))

  // 13b. small windows: the face's default window is 252 x 336 px. Under 480 px the panel is an overlay over the whole window.
  const samples = [['deck', 'Q3 board deck'], ['pdf', 'Hyderabad flights'], ['markdown', 'Notes'], ['code', 'Tools'], ['html', 'Hostile']]
  /** Where everything of the panel is, and whether anything runs out of the window or the panel. */
  const geometry = () => page.evaluate(() => {
    const W = window.innerWidth
    const H = window.innerHeight
    const box = (selector) => {
      const el = document.querySelector(selector)
      if (!el)
        return null
      const r = el.getBoundingClientRect()
      const style = getComputedStyle(el)
      return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height, shown: style.display !== 'none' && style.visibility !== 'hidden' && r.width > 0 && r.height > 0 }
    }
    const inside = b => b && b.shown && b.left >= -0.5 && b.right <= W + 0.5 && b.top >= -0.5 && b.bottom <= H + 0.5
    const pv = document.querySelector('.pv')
    const body = document.querySelector('.pv-body')
    const parts = [...pv.querySelectorAll('.pv-head, .pv-head button, .pv-title, .pv-sub, .pv-foot, .pv-foot button, .pv-pager, .pv-pager button, .pv-thumbs')]
      .filter(el => getComputedStyle(el).display !== 'none')
    const pr = pv.getBoundingClientRect()
    const stray = parts.filter((el) => {
      const r = el.getBoundingClientRect()
      return r.width > 0 && (r.left < pr.left - 0.5 || r.right > pr.right + 0.5)
    }).map(el => el.className || el.tagName)
    const title = document.querySelector('.pv-title')
    return {
      docOverflow: document.documentElement.scrollWidth > W,
      panelOverflow: pv.scrollWidth > pv.clientWidth + 1,
      bodyOverflow: body.scrollWidth > body.clientWidth + 1,
      stray,
      panel: box('.pv'),
      closeInside: inside(box('.pv-close')),
      openInside: inside(box('.pv-primary')),
      titleOneLine: title.getBoundingClientRect().height < 26,
      image: box('.pv-pages .pv-image img'),
      thumbs: box('.pv-thumbs'),
      thumbsToggle: box('.pv-thumbs-toggle'),
      pager: box('.pv-pager'),
      W,
      H,
    }
  })
  const sizes = [[252, 336], [320, 480], [480, 800], [900, 600], [1280, 720]]
  await page.keyboard.press('Escape')
  await page.evaluate(() => window.__preview.controller.openPanel())
  await page.waitForSelector('.pv')
  for (const [w, h] of sizes) {
    await page.setViewportSize({ width: w, height: h })
    for (const [name, title] of samples) {
      await show(title)
      await shot(`panel-${w}x${h}-${name}`)
      const g = await geometry()
      const tag = `${name} at ${w}x${h}`
      check(`no sideways overflow of the panel: ${tag}`, !g.docOverflow && !g.panelOverflow && !g.bodyOverflow && g.stray.length === 0, g.stray.join(','))
      check(`close and open in app are in view: ${tag}`, g.closeInside && g.openInside)
      if (w < 480) {
        check(`it covers the whole window: ${tag}`, Math.abs(g.panel.width - w) < 1 && Math.abs(g.panel.height - h) < 1)
        check(`the title is one line: ${tag}`, g.titleOneLine)
      }
      if (w < 480 && (name === 'deck' || name === 'pdf')) {
        check(`the page image fills the width: ${tag}`, g.image && g.image.width >= w - 12 && g.image.height > 40, JSON.stringify(g.image))
        check(`the pager is in view: ${tag}`, g.pager && g.pager.bottom <= h && g.pager.shown)
      }
      if (w < 480 && name === 'deck') {
        const stripOn = g.thumbs?.shown
        check(`thumbnails are a strip or behind a toggle: ${tag}`, stripOn ? g.thumbs.right <= w + 0.5 : g.thumbsToggle?.shown === true)
      }
    }
  }
  // the thumbnail toggle works in the small window, and the strip is reachable
  await page.setViewportSize({ width: 252, height: 336 })
  await show('Q3 board deck')
  const stripShown = () => page.locator('.pv-thumbs').isVisible()
  check('short window: the thumbnails start hidden', !(await stripShown()))
  await page.click('.pv-thumbs-toggle')
  await settle()
  check('the toggle shows the thumbnail strip', await stripShown())
  await shot('panel-252x336-deck-thumbs')
  await page.locator('.pv-thumb').nth(2).click()
  await settle()
  check('a thumbnail jumps to its page in the small window', (await page.locator('.pv-sub').textContent()).includes('page 3 of 4'))
  await page.click('.pv-thumbs-toggle')
  await settle()
  check('the toggle hides the strip again', !(await stripShown()))
  // a long title is cut by an ellipsis on one line, never wrapped
  const longTitle = 'A very long report title that keeps going well past the width of a small window.pdf'
  await page.evaluate((t) => {
    const c = window.__preview.controller.current.value
    window.__preview.controller.current.value = { ...c, title: t }
  }, longTitle)
  await settle()
  const longGeometry = await geometry()
  check('a long title stays on one line and inside the panel', longGeometry.titleOneLine && longGeometry.stray.length === 0)
  await shot('panel-252x336-long-title')
  // widths from 200 to 1400 px: nothing runs out of the window or the panel
  const widths = []
  for (let w = 200; w <= 1400; w += 40)
    widths.push(w)
  widths.push(479, 480)
  const failedWidths = []
  for (const [name, title] of samples.filter(([n]) => n === 'deck' || n === 'markdown' || n === 'html')) {
    await show(title)
    for (const w of widths) {
      await page.setViewportSize({ width: w, height: w < 480 ? 336 : 700 })
      const g = await geometry()
      if (g.docOverflow || g.panelOverflow || g.bodyOverflow || g.stray.length || !g.closeInside || !g.openInside)
        failedWidths.push(`${name}@${w}`)
    }
  }
  check(`no overflow and buttons in view at ${widths.length} widths from 200 to 1400 px`, failedWidths.length === 0, failedWidths.join(','))
  await page.setViewportSize({ width: 450, height: 600 })
  await page.keyboard.press('Escape')

  // 14. nothing asked for the outside, and nothing threw
  await settle()
  check('no request for any outside address', unexpected.length === 0, unexpected.join(' | '))
  check('no page error', pageErrors.length === 0, pageErrors.join(' | '))
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
