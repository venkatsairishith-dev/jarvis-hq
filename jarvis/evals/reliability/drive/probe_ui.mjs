// Responsiveness probe for the face window (D59): how long from a real input event until the page has painted a frame that follows it.
//   node probe_ui.mjs input <samples> [chat|main]    key presses into the chat box           -> {"samples":[ms...]}
//   node probe_ui.mjs click <samples> [chat|main]    pointer presses on a neutral spot         -> {"samples":[ms...]}
// How it measures: a capture-phase listener takes the event's own timeStamp (set by the browser when the input arrived, so a busy main
// thread shows up as delay), asks for the next animation frame and then a timer after it (which runs after that frame was produced), and
// records performance.now() - event.timeStamp. The events are sent through the browser's input pipeline (CDP Input.dispatch*), the same
// path a real device event takes inside the renderer; they are NOT OS-level, so they never click anything outside the face.
// Run it while a load is active (a local model generating, an online stream, a PC task) to measure "input latency with a model generating".
import { createRequire } from 'node:module'

const FACE_DIR = process.env.JARVIS_FACE_DIR || 'D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi'
const CDP = process.env.JARVIS_FACE_CDP || 'http://127.0.0.1:9333'
const mode = process.argv[2]
const n = Math.max(1, Number(process.argv[3] || 20))
const which = process.argv[4] || 'chat'
const out = (o) => { console.log(JSON.stringify(o)) }
const require = createRequire(`${FACE_DIR}/package.json`)
let chromium
try { ({ chromium } = require('playwright')) } catch { out({ harness_error: 'playwright not found' }); process.exit(2) }
let browser
try { browser = await chromium.connectOverCDP(CDP) } catch { out({ harness_error: `cannot reach the face at ${CDP}` }); process.exit(2) }
const pages = browser.contexts().flatMap(c => c.pages())
const page = which === 'main' ? pages.find(p => /#\/$/.test(p.url())) : pages.find(p => p.url().includes('chat-floating'))
if (!page) { out({ harness_error: `face page ${which} not found` }); await browser.close(); process.exit(2) }

await page.evaluate((evName) => {
  window.__rel = []
  document.addEventListener(evName, (e) => {
    const t = e.timeStamp
    requestAnimationFrame(() => setTimeout(() => window.__rel.push(performance.now() - t), 0))
  }, true)
}, mode === 'input' ? 'keydown' : 'pointerdown')

if (mode === 'input') {
  await page.evaluate(() => { const b = document.querySelector('button[aria-label="Show the message box"]'); if (b) b.click() }).catch(() => {})
  await page.waitForTimeout(500)
  const box = await page.$('textarea')
  if (box) await box.focus()
  for (let i = 0; i < n; i++) { await page.keyboard.press('x'); await page.waitForTimeout(60) }
  for (let i = 0; i < n; i++) { await page.keyboard.press('Backspace'); await page.waitForTimeout(10) }     // leave the box empty
} else {
  for (let i = 0; i < n; i++) { await page.mouse.click(4, 4); await page.waitForTimeout(80) }
}
await page.waitForTimeout(400)
const samples = (await page.evaluate(() => window.__rel)).map(v => Math.round(v * 10) / 10)
out({ mode, page: which, samples: mode === 'input' ? samples.slice(0, n) : samples })
await browser.close()
process.exit(0)
