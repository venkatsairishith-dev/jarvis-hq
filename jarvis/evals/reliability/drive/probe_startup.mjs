// Startup probe (D59): after the face process was started, when does the page first HANDLE an input?
//   node probe_startup.mjs <timeoutSeconds> [main|chat]  -> {"t_page_ms": epoch, "t_first_handled_ms": epoch, "polls": n}
// It polls the face's debug port until the page exists, then sends a pointer press every 100 ms into the page and counts it handled when the page's own
// listener ran within 120 ms. Epoch times (Date.now()) are returned so the Python side can subtract the launch time and the moment the window appeared.
//
// 2026-10-26 (live): this speaks the DevTools protocol itself (http /json/list + one WebSocket + Input.dispatchMouseEvent). The first version used Playwright's
// connectOverCDP, which only returned about 20 s after the launch while the page took input after about 1 s (raw protocol, same machine, same build): the
// "startup 24 s" it reported was Playwright's attach time, not the face's.
import { createRequire } from 'node:module'
import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'

const FACE_DIR = process.env.JARVIS_FACE_DIR || 'D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi'
const CDP = process.env.JARVIS_FACE_CDP || 'http://127.0.0.1:9333'
const limit = Number(process.argv[2] || 30) * 1000
const which = process.argv[3] || 'main'
const out = (o) => { console.log(JSON.stringify(o)) }
const sleep = ms => new Promise(r => setTimeout(r, ms))

function loadWs() {
  const req = createRequire(`${FACE_DIR}/package.json`)
  try { return req('ws') } catch {}
  const pnpm = path.join(FACE_DIR, '..', '..', 'node_modules', '.pnpm')           // the face workspace's pnpm store
  try {
    const dir = fs.readdirSync(pnpm).find(d => /^ws@/.test(d))
    if (dir) return createRequire(path.join(pnpm, dir, 'node_modules', 'ws', 'package.json'))('ws')
  } catch {}
  return null
}
const WebSocket = loadWs()
if (!WebSocket) { out({ harness_error: 'the ws package was not found in the face workspace' }); process.exit(2) }

const list = () => new Promise((res, rej) => http.get(`${CDP}/json/list`, (r) => {
  let b = ''
  r.on('data', d => b += d)
  r.on('end', () => { try { res(JSON.parse(b)) } catch (e) { rej(e) } })
}).on('error', rej))

const start = Date.now()
let target = null; let tPage = 0; let polls = 0
while (Date.now() - start < limit) {
  try {
    const pages = (await list()).filter(t => t.type === 'page')
    target = which === 'main' ? pages.find(p => /#\/$/.test(p.url)) : pages.find(p => p.url.includes('chat-floating'))
    if (target) { tPage = Date.now(); break }
  } catch {}
  await sleep(50)
}
if (!target) { out({ harness_error: 'the face page never appeared', waited_ms: Date.now() - start }); process.exit(2) }

const ws = new WebSocket(target.webSocketDebuggerUrl)
await new Promise(r => ws.on('open', r))
let id = 0; const pending = new Map()
ws.on('message', (m) => { const o = JSON.parse(m); if (o.id && pending.has(o.id)) { pending.get(o.id)(o); pending.delete(o.id) } })
const send = (method, params = {}) => new Promise((res) => { const i = ++id; pending.set(i, res); ws.send(JSON.stringify({ id: i, method, params })) })
const count = async () => (await send('Runtime.evaluate', { expression: 'window.__relCount', returnByValue: true })).result?.result?.value
await send('Runtime.evaluate', { expression: 'window.__relCount = 0; document.addEventListener("pointerdown", () => { window.__relCount++ }, true); 1' })
let handled = 0
while (Date.now() - start < limit) {
  polls++
  const before = await count()
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', x: 4, y: 4, button: 'left', clickCount: 1 })
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: 4, y: 4, button: 'left', clickCount: 1 })
  await sleep(120)
  const after = await count()
  if (after > before) { handled = Date.now(); break }
}
out(handled ? { t_page_ms: tPage, t_first_handled_ms: handled, polls } : { harness_error: 'no input was handled within the time limit', t_page_ms: tPage, polls })
ws.close()
process.exit(0)
