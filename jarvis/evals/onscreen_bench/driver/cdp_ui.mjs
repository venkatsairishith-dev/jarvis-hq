// Look at / drive the face's own UI through its debug port (my own face window). usage: node cdp_ui.mjs dump | click "<selector>" | type "<selector>" "<text>" | press "<key>"
import { createRequire } from 'node:module'

const require = createRequire('D:/jarvis/hq/jarvis/face/apps/stage-tamagotchi/package.json')
const { chromium } = require('playwright')
const browser = await chromium.connectOverCDP('http://127.0.0.1:9333')
const pages = browser.contexts().flatMap(c => c.pages())
const wanted = process.env.PAGE ? pages.find(p => p.url().includes(process.env.PAGE)) : null
const main = wanted || pages.find(p => /#\/$|#\/?$/.test(p.url()) && !p.url().includes('inlay') && !p.url().includes('settings')) || pages.find(p => p.url().includes('index.html') && !p.url().includes('#/inlay'))
const [cmd, a, b] = process.argv.slice(2)
console.log('pages:', pages.map(p => p.url().split('#')[1] || '(root)').join(' | '))
if (cmd === 'dump') {
  for (const p of pages) {
    const info = await p.evaluate(() => ({
      title: document.title,
      buttons: [...document.querySelectorAll('button,[role=button],textarea,input,[contenteditable=true]')].slice(0, 40).map(e => `${e.tagName.toLowerCase()}|${(e.getAttribute('aria-label') || e.getAttribute('title') || e.getAttribute('placeholder') || e.innerText || '').trim().slice(0, 40)}|${e.className.toString().slice(0, 50)}|${Math.round(e.getBoundingClientRect().left)},${Math.round(e.getBoundingClientRect().top)}`),
    }))
    console.log('--- page', p.url().split('#')[1] || '(root)', JSON.stringify(info.title))
    info.buttons.forEach(x => console.log('   ', x))
  }
}
else if (cmd === 'jsclick') { for (const pg of pages) { const ok = await pg.evaluate(sel => { const e = document.querySelector(sel); if (e) { e.click(); return true } return false }, a); if (ok) { console.log('js-clicked on', pg.url().split('#')[1] || '(root)'); break } } }
else if (cmd === 'click') { await main.click(a, { timeout: 5000 }); console.log('clicked', a) }
else if (cmd === 'type') { await main.fill(a, b, { timeout: 5000 }); console.log('typed') }
else if (cmd === 'press') { await main.keyboard.press(a); console.log('pressed', a) }
await browser.close()
