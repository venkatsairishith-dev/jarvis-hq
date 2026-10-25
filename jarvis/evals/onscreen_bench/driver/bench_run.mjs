// One on-screen benchmark run: a clean chat session in the face, the goal typed into the chat box like the owner would, everything the face sends to the gateway timed.
// usage: node bench_run.mjs "<goal text>" [maxSeconds]   -> one JSON line on stdout (rows for results/onscreen-<date>.json)
import { createRequire } from 'node:module'

const require = createRequire('D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi/package.json')
const { chromium } = require('playwright')
const goal = process.argv[2]
const maxSeconds = Number(process.argv[3] || 240)
const browser = await chromium.connectOverCDP('http://127.0.0.1:9333')
const pages = browser.contexts().flatMap(c => c.pages())
const main = pages.find(p => /#\/$/.test(p.url()))
const chat = pages.find(p => p.url().includes('chat-floating'))
if (!main || !chat) { console.log(JSON.stringify({ error: 'face pages not found', pages: pages.map(p => p.url().split('#')[1]) })); process.exit(2) }

// 1. a clean conversation
await main.evaluate(async () => {
  const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia
  const store = pinia._s.get('chat-session')
  store.cleanupMessages(store.activeSessionId)
})
await new Promise(r => setTimeout(r, 1500))
await chat.evaluate(() => { const b = document.querySelector('button[aria-label="Show the message box"]'); if (b) b.click() })
await new Promise(r => setTimeout(r, 800))

// 2. watch the gateway traffic
const t0 = Date.now(); const secs = () => Math.round((Date.now() - t0) / 100) / 10
const reqs = []; const resps = []; let inflight = 0; let lastActivity = Date.now()
for (const p of pages) {
  p.on('request', (r) => {
    if (!r.url().includes('48950')) return
    let d = {}; try { d = JSON.parse(r.postData() || '{}') } catch {}
    inflight++; lastActivity = Date.now()
    reqs.push({ t: secs(), bytes: (r.postData() || '').length, tools: (d.tools || []).length, model: d.model, messages: (d.messages || []).length })
  })
  p.on('response', async (res) => {
    if (!res.url().includes('48950')) return
    const row = { status: res.status(), t_headers: secs(), provider: res.headers()['x-jarvis-provider'] || '' }
    try {
      const body = await res.text()
      row.t_done = secs(); row.bytes = body.length
      if (process.env.BENCH_DUMP) (await import('node:fs')).writeFileSync(process.env.BENCH_DUMP + '-' + resps.length + '.txt', body)
      const calls = {}; const fields = {}; let content = ''; let reasoning = 0
      for (const line of body.split(String.fromCharCode(10))) {
        if (!line.startsWith('data:') || line.includes('[DONE]')) continue
        let d; try { d = JSON.parse(line.slice(5)) } catch { continue }
        for (const ch of d.choices || []) {
          const delta = ch.delta || {}
          for (const [k, v] of Object.entries(delta)) if (v) fields[k] = (fields[k] || 0) + 1
          content += delta.content || ''
          reasoning += (delta.reasoning_content || delta.reasoning || '').length
          for (const tc of delta.tool_calls || []) {
            const c = calls[tc.index ?? 0] ||= { name: '', args: '' }
            if (tc.function?.name) c.name += tc.function.name
            if (tc.function?.arguments) c.args += tc.function.arguments
          }
        }
      }
      row.calls = Object.values(calls).map((c) => {
        let inner = ''
        if (c.name === 'builtIn_mcpCallTool') { try { inner = JSON.parse(JSON.parse(c.args).arguments ? c.args : c.args).name || '' } catch { const m = /"name"\s*:\s*"([^"]+)"/.exec(c.args); inner = m ? m[1] : '' } }
        return c.name + (inner ? ` -> ${inner}` : '')
      })
      row.content_chars = content.length; row.reasoning_chars = reasoning
      if (res.status() !== 200 || !Object.keys(calls).length) row.content_sample = content.slice(0, 160)
    } catch (e) { row.body_error = String(e).slice(0, 80) }
    resps.push(row)
    inflight = Math.max(0, inflight - 1); lastActivity = Date.now()
  })
}

// 3. the goal, typed into the chat box
await chat.fill('textarea', goal)
await chat.keyboard.press('Enter')
const start = Date.now()
while (Date.now() - start < maxSeconds * 1000) {
  await new Promise(r => setTimeout(r, 2000))
  if (inflight === 0 && Date.now() - lastActivity > 25000 && reqs.length > 0) break
}
const elapsed = secs()
await new Promise(r => setTimeout(r, 1500))
const text = await chat.evaluate(() => document.body.innerText.replace(/\n{2,}/g, '\n'))
const tail = text.split('\n').slice(-14).join(' | ').slice(-900)
const errs = [...text.matchAll(/(Remote sent \d+ response|Error from server[^|\n]{0,120}|didn't finish)/g)].map(m => m[1])
console.log(JSON.stringify({
  goal, seconds: elapsed, timed_out: Date.now() - start >= maxSeconds * 1000,
  rounds: reqs.filter(r => r.tools > 1).length, requests: reqs.length,
  statuses: resps.map(r => r.status), providers: [...new Set(resps.map(r => r.provider).filter(Boolean))],
  calls: resps.flatMap(r => r.calls || []), errors_on_screen: errs,
  reasoning_chars: resps.reduce((a, r) => a + (r.reasoning_chars || 0), 0), request_kb: reqs.map(r => Math.round(r.bytes / 1024)), messages: reqs.map(r => r.messages),
  chat_tail: tail,
}))
await browser.close()
process.exit(0)
