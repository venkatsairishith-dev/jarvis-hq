// Startup probe (D59): after the face process was started, when does the page first HANDLE an input?
//   node probe_startup.mjs <timeoutSeconds> [main|chat]  -> {"t_page_ms": epoch, "t_first_handled_ms": epoch, "polls": n}
// It polls the face's debug port until the page exists, then sends a pointer press every 100 ms into the page and counts it handled when the page's own
// listener ran within 120 ms. Epoch times (Date.now()) are returned so the Python side can subtract the launch time and the moment the window appeared.
import { createRequire } from 'node:module'

const FACE_DIR = process.env.JARVIS_FACE_DIR || 'D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi'
const CDP = process.env.JARVIS_FACE_CDP || 'http://127.0.0.1:9333'
const limit = Number(process.argv[2] || 30) * 1000
const which = process.argv[3] || 'main'
const out = (o) => { console.log(JSON.stringify(o)) }
const require = createRequire(`${FACE_DIR}/package.json`)
let chromium
try { ({ chromium } = require('playwright')) } catch { out({ harness_error: 'playwright not found' }); process.exit(2) }
const sleep = ms => new Promise(r => setTimeout(r, ms))
const start = Date.now()
let page = null; let browser = null; let tPage = 0; let polls = 0
while (Date.now() - start < limit) {
  try {
    browser ||= await chromium.connectOverCDP(CDP)
    const pages = browser.contexts().flatMap(c => c.pages())
    page = which === 'main' ? pages.find(p => /#\/$/.test(p.url())) : pages.find(p => p.url().includes('chat-floating'))
    if (page) { tPage = Date.now(); break }
  } catch { browser = null }
  await sleep(100)
}
if (!page) { out({ harness_error: 'the face page never appeared', waited_ms: Date.now() - start }); process.exit(2) }
await page.evaluate(() => { window.__relCount = 0; document.addEventListener('pointerdown', () => { window.__relCount++ }, true) }).catch(() => {})
let handled = 0
while (Date.now() - start < limit) {
  polls++
  const before = await page.evaluate(() => window.__relCount).catch(() => null)
  if (before === null) { await sleep(100); await page.evaluate(() => { window.__relCount = 0; document.addEventListener('pointerdown', () => { window.__relCount++ }, true) }).catch(() => {}); continue }
  await page.mouse.click(4, 4).catch(() => {})
  await sleep(120)
  const after = await page.evaluate(() => window.__relCount).catch(() => before)
  if (after > before) { handled = Date.now(); break }
}
out(handled ? { t_page_ms: tPage, t_first_handled_ms: handled, polls } : { harness_error: 'no input was handled within the time limit', t_page_ms: tPage, polls })
await browser.close()
process.exit(0)
