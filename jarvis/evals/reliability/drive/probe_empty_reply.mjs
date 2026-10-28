// Investigation probe (read-only on the face): types ONE goal in the face chat and records, every 1.5 s, what the store holds and what the chat panel
// shows, plus screenshots of the chat window at the start, mid-run and at the end. Output: <outdir>/trace.json, shot-*.png
// usage: node probe_empty_reply.mjs "<goal>" <outdir> [maxSeconds] [--noSend]
import { createRequire } from 'node:module'
import fs from 'node:fs'

const FACE_DIR = process.env.JARVIS_FACE_DIR || 'D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi'
const CDP = 'http://127.0.0.1:9333'
const goal = process.argv[2]
const outdir = process.argv[3]
const maxSeconds = Number(process.argv[4] || 180)
const noSend = process.argv.includes('--noSend')
fs.mkdirSync(outdir, { recursive: true })
const require = createRequire(`${FACE_DIR}/package.json`)
const { chromium } = require('playwright')
const browser = await chromium.connectOverCDP(CDP)
const pages = browser.contexts().flatMap(c => c.pages())
const main = pages.find(p => /#\/$/.test(p.url()))
const chat = pages.find(p => p.url().includes('chat-floating'))
const sleep = ms => new Promise(r => setTimeout(r, ms))
const t0 = Date.now()
const secs = () => Math.round((Date.now() - t0) / 100) / 10
const withTimeout = (p, ms, fb) => Promise.race([p, new Promise(r => setTimeout(() => r(fb), ms))])

const readStore = () => withTimeout(main.evaluate(() => {
  const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia
  const chatStore = pinia._s.get('chat')
  const sess = pinia._s.get('chat-session')
  const msgs = (sess?.sessionMessages?.[sess.activeSessionId]) || []
  const brief = msgs.map((m) => {
    const slices = (m.slices || []).map(s => s.type === 'text' ? { t: 'text', len: (s.text || '').length, text: (s.text || '').slice(0, 200) }
      : s.type === 'tool-call' ? { t: 'tool-call', name: s.toolCall?.toolName } : { t: s.type })
    const tr = (m.tool_results || []).map(r => ({ id: String(r.id).slice(0, 12), isError: !!r.isError, res: JSON.stringify(r.result ?? '').slice(0, 300) }))
    const content = typeof m.content === 'string' ? m.content : JSON.stringify(m.content ?? '')
    return { role: m.role, id: String(m.id || '').slice(0, 10), content_len: content.length, content: content.slice(0, 200), slices, tool_results: tr, keys: Object.keys(m) }
  })
  return { sending: chatStore ? !!chatStore.sending : null, n: msgs.length, brief }
}).catch(e => ({ err: String(e) })), 10000, { err: 'timeout' })

const readPanel = () => withTimeout(chat.evaluate(() => {
  const t = document.body.innerText.replace(/\n{2,}/g, '\n')
  const bubbles = [...document.querySelectorAll('.chat-message-item-container')].map(b => ({ text: b.innerText.replace(/\s+/g, ' ').trim().slice(0, 300), html_len: b.innerHTML.length, has_loader: !!b.querySelector('[class*="three-dots-loading"]') }))
  return { text_tail: t.split('\n').slice(-25), n_buttons: document.querySelectorAll('button').length, bubbles }
}).catch(e => ({ err: String(e) })), 10000, { err: 'timeout' })

const shot = async (name) => { try { await chat.screenshot({ path: `${outdir}/shot-${name}.png`, timeout: 8000 }) } catch (e) { fs.writeFileSync(`${outdir}/shot-${name}.err`, String(e)) } }
const shotMain = async (name) => { try { await main.screenshot({ path: `${outdir}/shotmain-${name}.png`, timeout: 8000 }) } catch (e) { fs.writeFileSync(`${outdir}/shotmain-${name}.err`, String(e)) } }

// clean conversation, like the harness
await withTimeout(main.evaluate(async () => {
  const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia
  const store = pinia._s.get('chat-session'); store.cleanupMessages(store.activeSessionId)
}).catch(() => {}), 10000, null)
await sleep(1200)
await chat.evaluate(() => { const b = document.querySelector('button[aria-label="Show the message box"]'); if (b) b.click() }).catch(() => {})
await sleep(600)
await shot('0-idle'); await shotMain('0-idle')

// raw capture of every call the face makes to the gateway (48950): request summary + full response body
let netN = 0
for (const pg of pages) {
  pg.on('response', async (res) => {
    if (!res.url().includes('48950')) return
    const i = ++netN
    try {
      const rq = res.request(); let d = {}; try { d = JSON.parse(rq.postData() || '{}') } catch {}
      const msgs = d.messages || []
      const summary = { i, t: secs(), status: res.status(), url: res.url().slice(-40), model: d.model, n_messages: msgs.length, n_tools: (d.tools || []).length, last_roles: msgs.slice(-3).map(m => m.role + ':' + (typeof m.content === 'string' ? m.content.slice(0, 160) : JSON.stringify(m.content).slice(0, 160))) }
      fs.writeFileSync(`${outdir}/net-${i}-req.json`, JSON.stringify(summary, null, 1))
      fs.writeFileSync(`${outdir}/net-${i}-res.txt`, (await res.text()).slice(0, 60000))
    } catch (e) { fs.writeFileSync(`${outdir}/net-${i}-err.txt`, String(e)) }
  })
}
const trace = { goal, started: new Date().toISOString(), samples: [] }
trace.idle = { store: await readStore(), panel: await readPanel() }
if (noSend) { fs.writeFileSync(`${outdir}/trace.json`, JSON.stringify(trace, null, 1)); await browser.close(); process.exit(0) }

await chat.fill('textarea', goal)
await chat.keyboard.press('Enter')
let sawSending = false; let quietSince = Date.now(); let lastN = -1; let midShot = false
while (true) {
  await sleep(1500)
  if (Date.now() - t0 > maxSeconds * 1000) { trace.timed_out = true; break }
  const s = await readStore(); const p = await readPanel()
  trace.samples.push({ t: secs(), sending: s.sending, n: s.n, last: s.brief?.[s.brief.length - 1] ? { role: s.brief[s.brief.length - 1].role, clen: s.brief[s.brief.length - 1].content_len, slices: s.brief[s.brief.length - 1].slices?.map(x => x.t + (x.len !== undefined ? `:${x.len}` : '')) } : null, panel_tail: (p.text_tail || []).slice(-6), bubbles: p.bubbles })
  if (s.sending && !midShot && secs() > 4) { midShot = true; await shot('1-mid'); await shotMain('1-mid') }
  if (s.sending) { sawSending = true; quietSince = Date.now() } else if (sawSending && Date.now() - quietSince > 4000) break
  if (s.n !== lastN) { lastN = s.n; quietSince = Date.now() }
  if (!sawSending && Date.now() - t0 > 25000) break
}
await sleep(1500)
await shot('2-end'); await shotMain('2-end')
trace.final = { store: await readStore(), panel: await readPanel(), seconds: secs() }
fs.writeFileSync(`${outdir}/trace.json`, JSON.stringify(trace, null, 1))
console.log(JSON.stringify({ seconds: secs(), samples: trace.samples.length, final_n: trace.final.store.n, sending: trace.final.store.sending }))
await browser.close()
process.exit(0)
