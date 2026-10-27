/* eslint-disable no-console */
// Renders the JARVIS face's real components in Chromium, takes screenshots and checks the layout with the DOM (no pixel diffs).
//
//   node render.mjs [options]
//     --out DIR        screenshots (default: ./.work/shots)           --label NAME   added to the file names (before / after)
//     --only REGEX     only screens whose name matches (e.g. "^ask:")  --sizes 450x600,900x700   --themes light,dark
//     --format png|webp   webp needs python3 + Pillow (compressed, for the committed pairs)
//     --report FILE    write the findings as JSON                      --audit   also run the slow audit (focus rings, contrast list)
//     --no-shots       only the checks
//
// It FAILS (exit 1) when, in any screen at any size and theme:
//   - the page scrolls sideways, or a visible element sticks out of the window on the left or right
//   - a text is clipped (overflow hidden/clip without an ellipsis) so that letters are cut off
//   - the page raises an error, or asks for anything that is not a file of the harness itself (the network lock in miniature)
// It only WARNS about: an ellipsis without a tooltip, a touch target under 32 px, text under WCAG AA contrast (4.5, or 3 for large text).
import http from 'node:http'
import process from 'node:process'

import { execFileSync } from 'node:child_process'
import { createReadStream, existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const here = resolve(fileURLToPath(new URL('.', import.meta.url)))
const face = resolve(here, '..', '..', 'face')
const args = process.argv.slice(2)
const opt = (name, fallback) => {
  const i = args.indexOf(`--${name}`)
  return i >= 0 ? (args[i + 1]?.startsWith('--') || args[i + 1] === undefined ? true : args[i + 1]) : fallback
}
const outDir = resolve(String(opt('out', join(here, '.work', 'shots'))))
const label = opt('label', '')
const only = opt('only', '') ? new RegExp(String(opt('only', ''))) : null
const sizes = String(opt('sizes', '450x600,900x700,1400x900')).split(',').map(s => s.split('x').map(Number))
const themes = String(opt('themes', 'light,dark')).split(',')
const format = String(opt('format', 'png'))
const reportFile = opt('report', '')
const audit = args.includes('--audit')
const noShots = args.includes('--no-shots')
mkdirSync(outDir, { recursive: true })

// The harness resolves its packages through two links to the face's node_modules (gitignored).
function link(target, at) {
  if (!existsSync(at) && !(() => { try { return lstatSync(at).isSymbolicLink() } catch { return false } })())
    symlinkSync(target, at, 'dir')
}
link('../face/node_modules', join(here, '..', 'node_modules'))
link('../../face/apps/stage-tamagotchi/node_modules', join(here, 'node_modules'))
if (!existsSync(join(face, 'apps', 'stage-tamagotchi', 'node_modules')))
  throw new Error('run `pnpm install` in jarvis/face first')

const requireFromFace = createRequire(join(face, 'package.json'))
const { chromium } = requireFromFace('playwright')
const { build } = await import(pathToFileURL(createRequire(join(here, 'x.js')).resolve('vite')).href)

const work = mkdtempSync(join(tmpdir(), 'ui-render-'))
const dist = join(work, 'dist')
const failures = []
const warnings = []
const report = { screens: [], failures, warnings }
const log = (ok, name, detail = '') => {
  if (!ok || process.env.VERBOSE)
    console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? `  (${detail})` : ''}`)
  if (!ok)
    failures.push(`${name}${detail ? `: ${detail}` : ''}`)
}

// ---- fixtures from the real Python side, then the harness build
execFileSync(process.env.PYTHON ?? 'python3', [join(here, 'make_fixtures.py'), join(work, 'fixtures.json')], { stdio: 'inherit' })
process.env.UI_HARNESS_OUT = dist
await build({ configFile: join(here, 'harness', 'vite.config.mjs') })
writeFileSync(join(dist, 'fixtures.json'), readFileSync(join(work, 'fixtures.json')))
const fixtures = JSON.parse(readFileSync(join(work, 'fixtures.json'), 'utf8'))

const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.svg': 'image/svg+xml', '.png': 'image/png' }
const stray = []
const server = http.createServer((request, response) => {
  const path = decodeURIComponent(new URL(request.url, 'http://x').pathname)
  const file = join(dist, normalize(path === '/' ? 'index.html' : path).replace(/^([/\\])+/, ''))
  if (file.startsWith(dist) && existsSync(file) && statSync(file).isFile()) {
    response.setHeader('Content-Type', types[extname(file)] ?? 'application/octet-stream')
    createReadStream(file).pipe(response)
    return
  }
  stray.push(`${request.method} ${path}`)
  response.statusCode = 404
  response.end()
})
await new Promise(done => server.listen(0, '127.0.0.1', done))
const origin = `http://127.0.0.1:${server.address().port}`

// ---- the screens
const screens = [
  ...['mixed', 'tools', 'waiting', 'empty', 'offline'].map(n => `chat:${n}`),
  ...['idle', 'acting', 'safe', 'pin', 'offline', 'halted', 'listening', 'speaking'].map(n => `hud:${n}`),
  ...Object.keys(fixtures.ask).map(n => `ask:${n}`),
  ...Object.keys(fixtures.timeline.views).map(n => `timeline:${n}`),
  'preview:closed',
  'preview:list',
  ...fixtures.preview.outputs.items.map(i => `preview:${i.title}`),
].filter(s => !only || only.test(s))

/** Runs in the page. Returns what is wrong with the layout, plus audit lists. */
function inspect(withAudit) {
  const W = window.innerWidth
  const out = { overflowX: false, sticksOut: [], clipped: [], noTooltip: [], smallTargets: [], contrast: [], noFocusRing: [], textNodes: 0 }
  out.overflowX = document.documentElement.scrollWidth > W + 1 || document.body.scrollWidth > W + 1
  const describe = (el) => {
    const cls = typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 3).join('.') : ''
    const text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40)
    return `${el.tagName.toLowerCase()}${cls ? `.${cls}` : ''}${text ? ` "${text}"` : ''}`
  }
  const shown = (el) => {
    const cs = getComputedStyle(el)
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0)
      return false
    const r = el.getBoundingClientRect()
    if (r.width < 3 || r.height < 3)
      return false // also the 1 px screen-reader-only text (.sr-only, .ask-sr): it is meant to be clipped
    for (let p = el; p && p !== document.body; p = p.parentElement) {
      const c = getComputedStyle(p)
      if (c.display === 'none' || c.visibility === 'hidden' || Number(c.opacity) === 0 || p.getAttribute('aria-hidden') === 'true' || p.hasAttribute('inert'))
        return false
    }
    return true
  }
  const insideXScroller = (el) => {
    for (let p = el.parentElement; p && p !== document.body; p = p.parentElement) {
      const c = getComputedStyle(p)
      if (/(auto|scroll)/.test(c.overflowX) && p.scrollWidth > p.clientWidth)
        return true
      if (/(hidden|clip)/.test(c.overflowX))
        return true // a parent clips it: that parent is checked on its own
    }
    return false
  }
  const hasText = el => [...el.childNodes].some(n => n.nodeType === 3 && n.textContent.trim())
  const all = [...document.querySelectorAll('body *')]
  const tooltipOf = (el) => {
    for (let p = el; p && p !== document.body; p = p.parentElement) {
      if (p.getAttribute('title') || p.getAttribute('aria-label'))
        return true
    }
    return false
  }
  for (const el of all) {
    if (!shown(el))
      continue
    const r = el.getBoundingClientRect()
    const cs = getComputedStyle(el)
    if ((r.right > W + 1 || r.left < -1) && !insideXScroller(el) && cs.position !== 'fixed' && !el.closest('[data-reka-scroll-area-viewport]'))
      out.sticksOut.push(`${describe(el)} [${Math.round(r.left)}..${Math.round(r.right)} of ${W}]`)
    if (hasText(el)) {
      out.textNodes++
      const clipsX = /(hidden|clip)/.test(cs.overflowX)
      if (clipsX && el.scrollWidth > el.clientWidth + 1) {
        if (cs.textOverflow === 'ellipsis') {
          if (!tooltipOf(el))
            out.noTooltip.push(describe(el))
        }
        else if (cs.display !== 'inline') {
          out.clipped.push(`${describe(el)} [${el.scrollWidth} > ${el.clientWidth}]`)
        }
      }
      const lineClamp = cs.webkitLineClamp && cs.webkitLineClamp !== 'none'
      if (/(hidden|clip)/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + 2 && !lineClamp && !/(auto|scroll)/.test(cs.overflowY) && cs.display !== 'inline' && cs.textOverflow !== 'ellipsis')
        out.clipped.push(`${describe(el)} [height ${el.scrollHeight} > ${el.clientHeight}]`)
    }
    if (/^(BUTTON|A|INPUT|SELECT|TEXTAREA|SUMMARY)$/.test(el.tagName) || /^(button|checkbox|radio|tab|switch|menuitem|option)$/.test(el.getAttribute('role') || '')) {
      if (el.tagName === 'INPUT' && /(checkbox|radio)/.test(el.type) && el.closest('label'))
        continue // the whole label is the target
      if (el.tagName === 'A' && cs.display === 'inline')
        continue
      const target = el.tagName === 'INPUT' && el.labels?.[0] ? el.labels[0].getBoundingClientRect() : r
      if (target.width < 32 || target.height < 32)
        out.smallTargets.push(`${describe(el)} ${Math.round(target.width)}x${Math.round(target.height)}`)
    }
  }
  if (withAudit) {
    const canvas = document.createElement('canvas')
    canvas.width = canvas.height = 1
    const g = canvas.getContext('2d', { willReadFrequently: true })
    const parse = (c) => {
      if (!c || c === 'transparent')
        return null
      g.clearRect(0, 0, 1, 1)
      g.fillStyle = '#000'
      g.fillStyle = c
      g.fillRect(0, 0, 1, 1)
      const [r, gr, b, a] = g.getImageData(0, 0, 1, 1).data
      return { r, g: gr, b, a: a / 255 }
    }
    const over = (top, bottom) => ({ r: top.r * top.a + bottom.r * (1 - top.a), g: top.g * top.a + bottom.g * (1 - top.a), b: top.b * top.a + bottom.b * (1 - top.a), a: 1 })
    const lum = (c) => {
      const f = v => (v /= 255) <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
      return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b)
    }
    const backgroundOf = (el) => {
      const layers = []
      for (let p = el; p; p = p.parentElement) {
        const c = parse(getComputedStyle(p).backgroundColor)
        if (c && c.a > 0) {
          layers.push(c)
          if (c.a >= 1)
            break
        }
      }
      let base = { r: 255, g: 255, b: 255, a: 1 }
      if (document.documentElement.classList.contains('dark'))
        base = { r: 20, g: 24, b: 36, a: 1 }
      for (const c of layers.reverse()) base = over(c, base)
      return base
    }
    for (const el of all) {
      if (!shown(el) || !hasText(el))
        continue
      if (el.disabled || el.closest('[disabled],[aria-disabled="true"]'))
        continue // a disabled control is exempt from WCAG 1.4.3
      const cs = getComputedStyle(el)
      const fg0 = parse(cs.color)
      if (!fg0)
        continue
      const bg = backgroundOf(el)
      const fg = over({ ...fg0, a: fg0.a * Number(cs.opacity) }, bg)
      const l1 = lum(fg)
      const l2 = lum(bg)
      const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
      const px = Number.parseFloat(cs.fontSize)
      const large = px >= 24 || (px >= 18.66 && Number(cs.fontWeight) >= 700)
      if (ratio < (large ? 3 : 4.5))
        out.contrast.push(`${describe(el)} ${ratio.toFixed(2)}:1 (${cs.color} on ${cs.backgroundColor === 'rgba(0, 0, 0, 0)' ? 'inherited' : cs.backgroundColor}, ${px}px)`)
    }
  }
  return out
}

/** Playwright's own browser when it is installed; else the first Chromium under PLAYWRIGHT_BROWSERS_PATH or /opt/pw-browsers (an older build works). */
async function launch() {
  if (process.env.CHROMIUM_PATH)
    return chromium.launch({ executablePath: process.env.CHROMIUM_PATH })
  try {
    return await chromium.launch()
  }
  catch {
    const roots = [process.env.PLAYWRIGHT_BROWSERS_PATH, '/opt/pw-browsers'].filter(Boolean)
    for (const root of roots) {
      for (const sub of readdirSync(root).sort().reverse()) {
        const exe = ['chrome-linux64/chrome', 'chrome-linux/chrome', 'chrome-win/chrome.exe'].map(f => join(root, sub, f)).find(f => sub.startsWith('chromium-') && existsSync(f))
        if (exe)
          return chromium.launch({ executablePath: exe })
      }
    }
    throw new Error('no Chromium found: set CHROMIUM_PATH')
  }
}
const browser = await launch()
const safeName = s => s.replace(/[^\w.-]+/g, '-').replace(/^-|-$/g, '').toLowerCase()
const tasks = []
for (const screen of screens) {
  for (const [w, h] of sizes) {
    for (const theme of themes) tasks.push([screen, w, h, theme])
  }
}
async function one([screen, w, h, theme]) {
  {
    {
      {
        const id = `${screen} @${w}x${h} ${theme}`
        const context = await browser.newContext({ viewport: { width: w, height: h }, colorScheme: theme, reducedMotion: 'reduce' })
        const page = await context.newPage()
        const errors = []
        page.on('pageerror', error => !/ResizeObserver loop/.test(String(error)) && errors.push(process.env.VERBOSE ? String(error.stack || error) : String(error)))
        page.on('console', (m) => { if (m.type() === 'error' && !/Failed to load resource|ResizeObserver loop|Content Security Policy/.test(m.text())) errors.push(m.text()) })
        // 127.0.0.1:1 is where AIRI's unreachable cloud clients point (SECURITY.md): it fails at once, so it is not an outside request
        page.on('request', (r) => { if (!r.url().startsWith(origin) && !r.url().startsWith('data:') && !r.url().startsWith('blob:') && !r.url().startsWith('http://127.0.0.1:1/')) errors.push(`outside request ${r.url()}`) })
        try {
          await page.goto(`${origin}/index.html?${new URLSearchParams({ screen, theme })}`, { waitUntil: 'load' })
          await page.waitForFunction(() => window.__ready === true, null, { timeout: 15000 })
        }
        catch (error) {
          log(false, id, `did not render: ${String(error).split('\n')[0]} ${errors.join(' | ')}`)
          await context.close()
          return
        }
        const m = await page.evaluate(inspect, audit)
        errors.push(...await page.evaluate(() => (window.__errors || []).filter(e => !/ResizeObserver loop/.test(e))))
        if (!noShots) {
          const base = join(outDir, `${safeName(screen)}-${w}x${h}-${theme}${label ? `-${label}` : ''}`)
          await page.screenshot({ path: `${base}.png` })
          if (format === 'webp') {
            execFileSync(process.env.PYTHON ?? 'python3', [join(here, 'webp.py'), `${base}.png`, `${base}.webp`])
            rmSync(`${base}.png`)
          }
        }
        const problems = []
        if (m.overflowX) problems.push('page scrolls sideways')
        if (m.sticksOut.length) problems.push(`sticks out: ${m.sticksOut.slice(0, 3).join('; ')}`)
        if (m.clipped.length) problems.push(`clipped text: ${m.clipped.slice(0, 3).join('; ')}`)
        if (errors.length) problems.push(`errors: ${[...new Set(errors)].slice(0, 2).join(' | ')}`)
        log(problems.length === 0, id, problems.join(' | '))
        report.screens.push({ id, problems, warn: { noTooltip: m.noTooltip, smallTargets: m.smallTargets, contrast: m.contrast } })
        for (const [k, list] of Object.entries({ noTooltip: m.noTooltip, smallTargets: m.smallTargets, contrast: m.contrast })) {
          for (const item of list) warnings.push(`${id} ${k}: ${item}`)
        }
        await context.close()
      }
    }
  }
}
try {
  // a few pages at a time (the machine has few cores: JARVIS_UI_JOBS to change)
  const jobs = Number(process.env.JARVIS_UI_JOBS || 3)
  let next = 0
  await Promise.all(Array.from({ length: jobs }, async () => {
    while (next < tasks.length) await one(tasks[next++])
  }))
}
finally {
  await browser.close()
  server.close()
  rmSync(work, { recursive: true, force: true })
}
if (stray.length) {
  log(false, 'requests the harness did not serve', [...new Set(stray)].slice(0, 5).join(', '))
}
const warnKinds = {}
for (const w of warnings) {
  const kind = w.split(' ').find(p => p.endsWith(':'))
  warnKinds[kind] = (warnKinds[kind] ?? 0) + 1
}
console.log(`\n${screens.length} screens x ${sizes.length} sizes x ${themes.length} themes; ${failures.length} failure(s); warnings: ${JSON.stringify(warnKinds)}`)
if (reportFile) {
  mkdirSync(dirname(resolve(String(reportFile))), { recursive: true })
  writeFileSync(resolve(String(reportFile)), JSON.stringify(report, null, 1))
}
process.exit(failures.length ? 1 : 0)
