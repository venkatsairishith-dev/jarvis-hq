/**
 * JARVIS: loads the built renderer (out/renderer) in Chromium and checks the Content-Security-Policy in index.html.
 *
 *   node scripts/csp-check.mjs            exit 1 when the policy lets injected script run, or the page itself breaks it
 *
 * 1. Injected markup: an <img onerror=...> and a <script> element are added to the page; neither may run.
 * 2. The app's own start-up: no "Refused to ..." message may appear (the policy must not break the app).
 *
 * The page runs outside Electron here, so the parts that need Electron's preload (window.electron) fail on their own;
 * only security-policy messages are counted.
 */
import { createReadStream, existsSync, readFileSync, statSync } from 'node:fs'
import http from 'node:http'
import { extname, join, normalize, resolve } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'

const root = resolve(fileURLToPath(new URL('../out/renderer', import.meta.url)))
const types = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.wasm': 'application/wasm', '.svg': 'image/svg+xml', '.png': 'image/png', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf' }

if (!existsSync(join(root, 'index.html'))) {
  console.error(`no build at ${root}; run electron-vite build first`)
  process.exit(2)
}

const server = http.createServer((request, response) => {
  const path = normalize(decodeURIComponent(new URL(request.url, 'http://x').pathname)).replace(/^([/\\])+/, '')
  let file = join(root, path)
  if (!file.startsWith(root) || !existsSync(file) || statSync(file).isDirectory())
    file = join(root, 'index.html')
  response.setHeader('Content-Type', types[extname(file)] ?? 'application/octet-stream')
  if (process.env.WITHOUT_CSP && file.endsWith('index.html')) {
    // A/B comparison: the same page without its policy, to tell what the policy breaks from what Electron's absence breaks.
    response.end(readFileSync(file, 'utf8').replace(/<meta\s+http-equiv="Content-Security-Policy"[\s\S]*?\/>/, ''))
    return
  }
  createReadStream(file).pipe(response)
})
await new Promise(done => server.listen(0, '127.0.0.1', done))
const url = `http://127.0.0.1:${server.address().port}/`

// CHROMIUM_PATH points at an installed Chromium when Playwright's own download is not there.
const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
const page = await browser.newPage()
const refused = []
page.on('console', (message) => {
  const text = message.text()
  if (/Content Security Policy|Refused to/i.test(text))
    refused.push(text.slice(0, 220))
})
page.on('pageerror', () => {})
await page.goto(url, { waitUntil: 'load' })
await page.waitForTimeout(4000)
const startupRefused = [...refused]
refused.length = 0

await page.evaluate(() => {
  window.__xss = []
  const holder = document.createElement('div')
  holder.innerHTML = '<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" onload="window.__xss.push(\'img-onload\')"><img src="x:y" onerror="window.__xss.push(\'img-onerror\')">'
  document.body.appendChild(holder)
  const script = document.createElement('script')
  script.textContent = 'window.__xss.push("inline-script")'
  document.body.appendChild(script)
  const remote = document.createElement('script')
  remote.src = 'https://evil.example/x.js'
  document.body.appendChild(remote)
  const anchor = document.createElement('a')
  anchor.href = 'javascript:window.__xss.push("javascript-url")'
  document.body.appendChild(anchor)
  anchor.click()
})
await page.waitForTimeout(1500)
const ran = await page.evaluate(() => window.__xss)
const mounted = await page.evaluate(() => document.querySelector('#app')?.children.length ?? 0)
await browser.close()
server.close()

console.log(`app mounted: ${mounted > 0 ? 'yes' : 'no'}`)
console.log(`policy messages during start-up: ${startupRefused.length}`)
for (const line of startupRefused)
  console.log(`  ${line}`)
console.log(`injected code that ran: ${ran.length ? ran.join(', ') : 'none'}`)
console.log(`injected code that the policy refused: ${refused.length}`)

process.exit(ran.length === 0 && startupRefused.length === 0 ? 0 : 1)
