// One reliability run through the REAL face chat: the goal is typed into the chat box like the owner would, and what the owner would SEE is read back
// from the face's own stores (the last assistant message, the tool calls, any ask form). It never calls a model and never chooses a tool.
// usage: node reliability_drive.mjs "<goal text>" [maxSeconds] [--keep-chat]      -> ONE json line on stdout
// needs: the face started with --remote-debugging-port=9333 (loopback), Node, the face's node_modules (playwright).
// env:   JARVIS_FACE_DIR (default D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi), JARVIS_FACE_CDP (default http://127.0.0.1:9333)
// Written from the shape of ../../onscreen_bench/driver/bench_run.mjs plus the stores in jarvis/face/packages/stage-ui (chat.ts: `sending`;
// chat/session-store.ts: sessionMessages). The store shapes were read, not run: `runner.py --selftest` prints what this script saw for a
// harmless goal so the laptop session can confirm the extraction before trusting a gate run.
import { createRequire } from 'node:module'

const FACE_DIR = process.env.JARVIS_FACE_DIR || 'D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi'
const CDP = process.env.JARVIS_FACE_CDP || 'http://127.0.0.1:9333'
const goal = process.argv[2]
const maxSeconds = Number(process.argv[3] || 240)
const keepChat = process.argv.includes('--keep-chat')
const out = (o) => { console.log(JSON.stringify(o)) }
if (!goal) { out({ harness_error: 'no goal given' }); process.exit(2) }

const require = createRequire(`${FACE_DIR}/package.json`)
let chromium
try { ({ chromium } = require('playwright')) } catch (e) { out({ harness_error: `playwright not found under ${FACE_DIR}` }); process.exit(2) }
let browser
try { browser = await chromium.connectOverCDP(CDP) } catch (e) { out({ harness_error: `cannot reach the face at ${CDP}` }); process.exit(2) }
const pages = browser.contexts().flatMap(c => c.pages())
const main = pages.find(p => /#\/$/.test(p.url()))
const chat = pages.find(p => p.url().includes('chat-floating'))
if (!main || !chat) { out({ harness_error: 'face pages not found', pages: pages.map(p => p.url().split('#')[1]) }); await browser.close(); process.exit(2) }

const sleep = ms => new Promise(r => setTimeout(r, ms))
const t0 = Date.now()
const secs = () => Math.round((Date.now() - t0) / 100) / 10
// D81: page.evaluate() (Playwright) has NO timeout of its own - if the face's renderer is frozen, an `await ...evaluate(...)` below can hang forever,
// and since the goal loop's own deadline check only runs BETWEEN awaits, a hung evaluate would hold the whole script past `maxSeconds` even though the
// loop looks bounded. This wraps a promise with its own timeout so the loop's deadline is always reachable; a timed-out read behaves like any other
// failed read already does here (resolves to `fallback`, same as the existing `.catch(() => null)` style).
const withTimeout = (p, ms, fallback) => Promise.race([p, new Promise(r => setTimeout(() => r(fallback), ms))])

// 1. a clean conversation (in place; the on-screen bench found that this does not reset everything: use the runner's --reset face for the final gate run)
if (!keepChat) {
  await withTimeout(main.evaluate(async () => {
    const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia
    const store = pinia._s.get('chat-session')
    store.cleanupMessages(store.activeSessionId)
  }).catch(() => {}), 10000, null)
  await sleep(1200)
}
await chat.evaluate(() => { const b = document.querySelector('button[aria-label="Show the message box"]'); if (b) b.click() }).catch(() => {})
await sleep(600)

// 2. count what the face sends to the gateway (requests per goal, errors)
const reqs = []; const statuses = []; const calls = []; let inflight = 0; let lastActivity = Date.now()
for (const p of pages) {
  p.on('request', (r) => {
    if (!r.url().includes('48950')) return
    inflight++; lastActivity = Date.now()
    let d = {}; try { d = JSON.parse(r.postData() || '{}') } catch {}
    reqs.push({ t: secs(), bytes: (r.postData() || '').length, tools: (d.tools || []).length })
  })
  p.on('response', async (res) => {
    if (!res.url().includes('48950')) return
    statuses.push(res.status())
    try {
      const body = await res.text()
      const acc = {}
      for (const line of body.split('\n')) {
        if (!line.startsWith('data:') || line.includes('[DONE]')) continue
        let d; try { d = JSON.parse(line.slice(5)) } catch { continue }
        for (const ch of d.choices || []) for (const tc of ch.delta?.tool_calls || []) {
          const c = acc[tc.index ?? 0] ||= { name: '', args: '' }
          if (tc.function?.name) c.name += tc.function.name
          if (tc.function?.arguments) c.args += tc.function.arguments
        }
      }
      for (const c of Object.values(acc)) {
        let inner = ''
        if (c.name === 'builtIn_mcpCallTool') { const m = /"name"\s*:\s*"([^"]+)"/.exec(c.args); inner = m ? m[1] : '' }
        calls.push(c.name + (inner ? ` -> ${inner}` : ''))
      }
    } catch {}
    inflight = Math.max(0, inflight - 1); lastActivity = Date.now()
  })
}

// helpers that read the stores in the main window
const readState = () => withTimeout(main.evaluate(() => {
  const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia
  const chatStore = pinia._s.get('chat')
  const sess = pinia._s.get('chat-session')
  const msgs = (sess?.sessionMessages?.[sess.activeSessionId]) || []
  const textOf = (m) => {
    if (!m) return ''
    if (typeof m.content === 'string') return m.content
    if (Array.isArray(m.content)) return m.content.map(p => (typeof p === 'string' ? p : p?.text || '')).join('')
    if (Array.isArray(m.slices)) return m.slices.filter(s => s?.type === 'text').map(s => s.text || '').join('')
    return ''
  }
  const flat = JSON.stringify(msgs)
  const assistants = msgs.filter(m => m?.role === 'assistant')
  const last = assistants[assistants.length - 1]
  const tools = []
  for (const m of msgs) for (const s of (m?.slices || [])) if (s?.type === 'tool-call') tools.push(String(s.toolCall?.toolName || s.toolName || ''))
  return {
    sending: chatStore ? !!chatStore.sending : null,
    messages: msgs.length,
    last_assistant: textOf(last),
    tool_slices: tools,
    has_ask_form: flat.includes('"kind":"jarvis_ask"') || flat.includes('\\"kind\\":\\"jarvis_ask\\"'),
    ask_form_json: (() => { const i = flat.indexOf('"kind":"jarvis_ask"'); if (i < 0) return ''; const a = flat.lastIndexOf('{', i); let depth = 0; for (let k = a; k < flat.length; k++) { if (flat[k] === '{') depth++; else if (flat[k] === '}') { depth--; if (depth === 0) return flat.slice(a, k + 1) } } return '' })(),
  }
}).catch(() => null), 10000, null)

// 3. the goal, typed into the chat box. In the orb look the island's "Open chat" button does not exist: a double-click on the orb opens (or folds) the
// floating chat, so when the message box is not visible the orb is double-clicked once and the box is waited for.
const boxShown = () => withTimeout(chat.evaluate(() => { const t = document.querySelector('textarea'); const r = t && t.getBoundingClientRect(); return !!r && r.width > 0 && r.height > 0 }).catch(() => false), 5000, false)
if (!(await boxShown())) {
  await withTimeout(main.locator('.orb-disc').first().dblclick({ timeout: 5000 }).catch(() => {}), 8000, null)
  for (let i = 0; i < 10 && !(await boxShown()); i++) await sleep(500)
}
await chat.fill('textarea', goal)
const start = Date.now()
await chat.keyboard.press('Enter')
let sawSending = false; let quietSince = Date.now(); let lastMsgs = -1
let timedOut = false
while (true) {
  await sleep(1500)
  if (Date.now() - start > maxSeconds * 1000) { timedOut = true; break }
  const st = await readState()
  if (st && st.sending !== null) {
    if (st.sending) { sawSending = true; quietSince = Date.now() }
    else if (sawSending && Date.now() - quietSince > 3500) break          // the chat finished sending and stayed quiet
  } else if (reqs.length > 0 && inflight === 0 && Date.now() - lastActivity > 25000) break      // no store: the bench's rule (25 s of silence)
  if (st && st.messages !== lastMsgs) { lastMsgs = st.messages; quietSince = Date.now() }
  if (!sawSending && Date.now() - start > 20000 && reqs.length === 0) break     // nothing was ever sent
}
const final = await readState()
await sleep(300)
let askForm = null
try { if (final?.ask_form_json) askForm = JSON.parse(final.ask_form_json) } catch {}
const body = await withTimeout(chat.evaluate(() => document.body.innerText.replace(/\n{2,}/g, '\n')).catch(() => ''), 10000, '')
const errs = [...body.matchAll(/(Remote sent \d+ response|Error from server[^|\n]{0,120}|didn't finish)/g)].map(m => m[1])
out({
  goal, seconds: secs(), timed_out: timedOut, requests: reqs.length, statuses, calls: [...calls, ...((final && final.tool_slices) || [])],
  final_text: final?.last_assistant ?? '', ask_form: askForm, messages: final?.messages ?? 0, store_found: final !== null && final.sending !== null,
  errors_on_screen: errs, chat_tail: body.split('\n').slice(-14).join(' | ').slice(-900),
})
await browser.close()
process.exit(0)
