import { createRequire } from 'node:module'
const require = createRequire('D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi/package.json')
const { chromium } = require('playwright')
const browser = await chromium.connectOverCDP('http://127.0.0.1:9333')
const page = browser.contexts().flatMap(c => c.pages()).find(p => p.url().includes('chat-floating'))
const n = await page.evaluate(async () => {
  const db = await new Promise((ok, no) => { const r = indexedDB.open('keyval-store'); r.onsuccess = () => ok(r.result); r.onerror = no })
  const keys = await new Promise(ok => { const r = db.transaction('keyval').objectStore('keyval').getAllKeys(); r.onsuccess = () => ok(r.result.map(String)) })
  let n = 0
  for (const k of keys.filter(k => k.startsWith('airi-local:chat:'))) {
    await new Promise(ok => { const tx = db.transaction('keyval', 'readwrite'); tx.objectStore('keyval').delete(k); tx.oncomplete = ok })
    n++
  }
  db.close()
  return n
})
console.log('deleted chat records:', n)
await browser.close()
