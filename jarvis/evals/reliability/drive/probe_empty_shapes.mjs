// SIMULATION (not a model run): appends three assistant messages of the "empty final text" shapes to the live chat store and records what the chat panel renders.
import { createRequire } from 'node:module'
import fs from 'node:fs'
const require = createRequire('D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi/package.json')
const { chromium } = require('playwright')
const b = await chromium.connectOverCDP('http://127.0.0.1:9333')
const pages = b.contexts().flatMap(c => c.pages())
const main = pages.find(p => /#\/$/.test(p.url())); const chat = pages.find(p => p.url().includes('chat-floating'))
const sleep = ms => new Promise(r => setTimeout(r, ms))
await main.evaluate(() => { const s = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('chat-session'); s.cleanupMessages(s.activeSessionId) })
await sleep(800)
const shapes = {
  A_tool_call_only: { role: 'assistant', content: '', slices: [{ type: 'tool-call', toolCall: { toolCallId: 'sim1', toolName: 'desktop__read_window_text', args: '{"app":"notepad"}' } }], tool_results: [{ id: 'sim1', result: '{"ok":true}' }] },
  B_zero_slices: { role: 'assistant', content: '', slices: [], tool_results: [] },
  C_text_only_whitespace: { role: 'assistant', content: '  ', slices: [{ type: 'text', text: '  ' }], tool_results: [] },
}
const out = {}
for (const [name, m] of Object.entries(shapes)) {
  await main.evaluate(([name, m]) => { const s = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('chat-session'); s.cleanupMessages(s.activeSessionId); s.appendSessionMessage(s.activeSessionId, { id: 'sim-u-' + name, role: 'user', content: 'SIM goal: read the Notepad window' }); s.appendSessionMessage(s.activeSessionId, { ...m, id: 'sim-a-' + name, createdAt: Date.now() }) }, [name, m])
  await sleep(1500)
  out[name] = await chat.evaluate(() => [...document.querySelectorAll('.chat-message-item-container')].map(e => ({ text: e.innerText.replace(/\s+/g, ' ').trim(), html_len: e.innerHTML.length, loader: !!e.querySelector('[class*=three-dots-loading]') })))
}
await main.evaluate(() => { const s = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('chat-session'); s.cleanupMessages(s.activeSessionId) })
fs.writeFileSync(process.argv[2] || 'empty-shapes.json', JSON.stringify(out, null, 1)); console.log(JSON.stringify(out, null, 1))
await b.close(); process.exit(0)
