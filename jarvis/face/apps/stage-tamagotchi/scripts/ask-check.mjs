/* eslint-disable no-console, style/max-statements-per-line */
import http from 'node:http'
import process from 'node:process'

/**
 * JARVIS: renders the ask panel on its own in Chromium from forms the real Python backend built, measures it and takes screenshots.
 *
 *   node scripts/ask-check.mjs [screenshot-folder]     default folder: docs/ask-panel (next to this script's app)
 *
 * It fails (exit 1) when
 *   - the panel takes 100 ms or more from "the server has a form" to "drawn" (two animation frames), for any form,
 *   - a long task (50 ms or more) of 200 ms or longer happens while drawing or scrolling the 450-row review list,
 *   - anything in the page asks for an address that is not the local harness (no network),
 *   - there is horizontal scroll at 360, 480 or 1280 px wide, in dark or light,
 *   - the keyboard does not work (focus into the panel, Space toggles, Enter = primary, Escape = cancel, arrows in a choice, focus back),
 *   - a long list is not virtualized (more than 120 rows in the page, or the last row cannot be reached),
 *   - reduced motion does not stop the animations,
 *   - (D63, voice) a form cannot be used by voice as it can by keyboard: every text, number, date and heading field has a microphone, the
 *     recognizer starts only when a microphone is pressed and stops when the form closes, interim text shows in the field and the final text
 *     replaces it, Alt+M / Alt+R / Alt+Z and Escape work, a spoken command is highlighted and can be undone, "Did you mean A or B?" shows,
 *     "clean" presses the primary button only with something ticked, a PIN, "approve" and ambient speech do nothing, and none of it adds
 *     a horizontal scroll at 360, 480 or 1280 px in dark or light (a fake transcript source stands in for the recognizer; no network).
 * Needs Chromium (Playwright's own, or CHROMIUM_PATH), python3 with Pillow, and no network.
 */
import { execFileSync } from 'node:child_process'
import { createReadStream, existsSync, mkdirSync, mkdtempSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { extname, join, normalize, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'
import { build } from 'vite'

const here = resolve(fileURLToPath(new URL('.', import.meta.url)))
const harness = join(here, 'ask-harness')
const shots = resolve(process.argv[2] ?? join(here, '..', 'docs', 'ask-panel'))
mkdirSync(shots, { recursive: true })
const work = mkdtempSync(join(tmpdir(), 'ask-check-'))
const dist = join(work, 'dist')
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.png': 'image/png', '.svg': 'image/svg+xml', '.woff2': 'font/woff2' }

const failures = []
function check(name, ok, detail = '') {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? `  (${detail})` : ''}`)
  if (!ok)
    failures.push(name)
}

const unexpected = []
let fixturesFile = ''
const server = http.createServer((request, response) => {
  const path = decodeURIComponent(new URL(request.url, 'http://x').pathname)
  if (path === '/fixtures.json') {
    response.setHeader('Content-Type', 'application/json')
    createReadStream(fixturesFile).pipe(response)
    return
  }
  const file = join(dist, normalize(path === '/' ? 'index.html' : path).replace(/^([/\\])+/, ''))
  if (file.startsWith(dist) && existsSync(file) && statSync(file).isFile()) {
    response.setHeader('Content-Type', types[extname(file)] ?? 'application/octet-stream')
    createReadStream(file).pipe(response)
    return
  }
  unexpected.push(`${request.method} ${path}`)
  response.statusCode = 404
  response.end()
})
await new Promise(done => server.listen(0, '127.0.0.1', done))
const origin = `http://127.0.0.1:${server.address().port}`

const SIZES = [[360, 640], [480, 800], [1280, 800]]
const THEMES = ['dark', 'light']
// shown at every size and theme
const MAIN = ['cleanup-ready', 'gallery', 'cards', 'code', 'steps', 'settings']
// shown at 360 dark and 1280 light
const MORE = ['cleanup-scanning', 'cleanup-ticked', 'cleanup-cleaning', 'cleanup-done', 'radio', 'file', 'text', 'compare', 'data', 'confirm', 'schedule', 'save', 'fallback']

try {
  fixturesFile = join(work, 'fixtures.json')
  execFileSync(process.env.PYTHON ?? 'python3', [join(harness, 'make_fixtures.py'), fixturesFile], { stdio: 'inherit' })
  process.env.ASK_HARNESS_OUT = dist
  await build({ configFile: join(harness, 'vite.config.mjs') })

  const browser = await chromium.launch(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {})
  const page = await browser.newPage({ viewport: { width: 360, height: 640 } })
  const pageErrors = []
  page.on('pageerror', error => pageErrors.push(String(error)))
  page.on('console', (m) => {
    if (m.type() === 'error')
      pageErrors.push(m.text())
  })
  await page.goto(`${origin}/`, { waitUntil: 'load' })
  await page.waitForFunction(() => window.__ask)
  await page.evaluate(() => {
    window.__long = []
    new PerformanceObserver((list) => { for (const e of list.getEntries()) window.__long.push(Math.round(e.duration)) }).observe({ type: 'longtask', buffered: true })
  })

  const slow = []
  const overflow = []
  const setTheme = theme => page.evaluate(t => document.documentElement.classList.toggle('dark', t === 'dark'), theme)
  const show = async (name, overrides) => {
    await page.evaluate(() => window.__ask.clear()) // a fresh panel each time: sections start collapsed, as for the owner
    const ms = await page.evaluate(([n, o]) => window.__ask.show(n, o), [name, overrides])
    await page.waitForTimeout(260) // the 180 ms entrance finishes before the screenshot
    return ms
  }
  const noScroll = async (label) => {
    const w = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth - innerWidth, body: (document.querySelector('.ask-body')?.scrollWidth ?? 0) - (document.querySelector('.ask-body')?.clientWidth ?? 0) }))
    if (w.doc > 0 || w.body > 0)
      overflow.push(`${label} (page +${w.doc}, body +${w.body})`)
  }
  const snap = async (name, w, h, theme, ms) => {
    await page.screenshot({ path: join(shots, `ask-${name}-${w}x${h}-${theme}.png`) })
    if (ms >= 100)
      slow.push(`${name} ${w}x${h} ${theme}: ${ms.toFixed(0)} ms`)
    await noScroll(`${name} ${w}x${h} ${theme}`)
  }

  const openReview = async () => {
    const head = page.locator('.ask-section-head', { hasText: 'Review first' })
    if (await head.getAttribute('aria-expanded') !== 'true')
      await head.click()
  }
  const timings = {}
  // the very first form the page ever draws (cold code): JARVIS shows the skeleton first, so that one must be fast too
  const cold = await page.evaluate(() => window.__ask.show('cleanup-scanning'))
  check('the first form of all (cold) is drawn in under 100 ms', cold < 100, `${cold.toFixed(0)} ms`)
  for (const theme of THEMES) {
    await setTheme(theme)
    for (const [w, h] of SIZES) {
      await page.setViewportSize({ width: w, height: h })
      for (const name of MAIN) {
        const ms = await show(name)
        timings[`${name} ${w}x${h} ${theme}`] = ms
        await snap(name, w, h, theme, ms)
      }
      if ((theme === 'dark' && w === 360) || (theme === 'light' && w === 1280)) {
        for (const name of MORE) {
          const ms = await show(name)
          timings[`${name} ${w}x${h} ${theme}`] = ms
          await snap(name, w, h, theme, ms)
        }
      }
      // the review list opened (450 rows)
      await show('cleanup-ready')
      await openReview()
      await page.locator('.ask-body').evaluate((el) => { el.scrollTop = el.scrollHeight })
      await page.waitForTimeout(260)
      await snap('cleanup-review-open', w, h, theme, 0)
    }
  }
  const times = Object.values(timings)
  console.log(`time to drawn: max ${Math.max(...times).toFixed(0)} ms, median ${times.sort((a, b) => a - b)[Math.floor(times.length / 2)].toFixed(0)} ms over ${times.length} forms`)
  check('every form is drawn in under 100 ms from the moment the server has it', slow.length === 0, slow.join('; '))
  check('no horizontal scroll at 360, 480 and 1280 px, dark and light', overflow.length === 0, overflow.join('; '))

  // virtualization of the review list (450 rows)
  await setTheme('dark')
  await page.setViewportSize({ width: 360, height: 640 })
  await page.evaluate(() => { window.__long.length = 0 })
  await show('cleanup-ready')
  await openReview()
  await page.waitForTimeout(300)
  const rowsInPage = await page.locator('.ask-row').count()
  check('the 450-row review list draws only the rows in view', rowsInPage > 5 && rowsInPage < 120, `${rowsInPage} rows in the page`)
  const total = await page.evaluate(() => window.__ask.controller.form.value.fields.flatMap(f => f.children ?? []).filter(c => c.id === 'review')[0].options.length)
  check('the review list holds 450 options', total === 450, String(total))
  const scroller = page.locator('.ask-virtual').first()
  for (const frac of [0.3, 0.7, 1]) {
    await scroller.evaluate((el, f) => { el.scrollTop = (el.scrollHeight - el.clientHeight) * f }, frac)
    await page.waitForTimeout(60)
  }
  await page.waitForTimeout(150)
  const lastText = await page.locator('.ask-virtual .ask-row').last().textContent()
  check('scrolling to the end reaches the last row', /empty-\d+/.test(lastText ?? ''), (lastText ?? '').trim().slice(0, 60))
  const rowsAfter = await page.locator('.ask-row').count()
  check('after scrolling the page still holds few rows', rowsAfter < 120, String(rowsAfter))
  const longTasks = await page.evaluate(() => window.__long.slice())
  check('no long task of 200 ms or more while drawing and scrolling the long list', longTasks.every(d => d < 200), `long tasks (ms): ${longTasks.join(', ') || 'none'}`)
  await page.locator('.ask-body').evaluate((el) => { el.scrollTop = el.scrollHeight })
  await page.screenshot({ path: join(shots, 'ask-cleanup-review-scrolled-360x640-dark.png') })

  // a checkbox reacts at once; the total follows
  await show('cleanup-ready')
  const first = page.locator('.ask-section').first().locator('input[type=checkbox]').first()
  const before = await page.locator('.ask-total strong').textContent()
  await first.focus()
  await page.keyboard.press('Space')
  const after = await page.locator('.ask-total strong').textContent()
  check('Space toggles a checkbox and the total changes in the same frame', before !== after, `${before} -> ${after}`)

  // keyboard: focus in, Enter = primary, Escape = cancel, arrows in a choice, focus back
  await page.evaluate(() => window.__ask.clear())
  await page.evaluate(() => { const b = document.createElement('button'); b.id = 'opener'; b.textContent = 'x'; document.body.append(b); b.focus() })
  await show('radio')
  check('focus moves into the panel when a form opens', await page.evaluate(() => !!document.activeElement?.closest('.ask')))
  await page.keyboard.press('ArrowRight')
  const checkedBefore = await page.locator('[role=radio][aria-checked=true]').textContent()
  check('an arrow key moves the choice', /wordpad/i.test(checkedBefore ?? ''), (checkedBefore ?? '').trim())
  await page.evaluate(() => { window.__ask.calls.length = 0; window.__ask.setReply({ ok: true, done: true }) })
  await page.keyboard.press('Enter')
  await page.waitForTimeout(100)
  const submit = await page.evaluate(() => window.__ask.calls.find(c => c.name.endsWith('jarvis_ask_submit')))
  check('Enter presses the primary button with the owner\'s choice', submit?.args?.button === 'ok' && submit?.args?.values?.choice === 'b', JSON.stringify(submit?.args?.values))
  check('the panel closes after an answer and focus goes back', await page.locator('.ask').count() === 0 && await page.evaluate(() => document.activeElement?.id === 'opener'))
  await show('radio')
  await page.evaluate(() => { window.__ask.calls.length = 0 })
  await page.keyboard.press('Escape')
  await page.waitForTimeout(100)
  const esc = await page.evaluate(() => window.__ask.calls.find(c => c.name.endsWith('jarvis_ask_submit')))
  check('Escape presses Cancel', esc?.args?.button === 'cancel')

  // required fields: the primary button refuses and says why
  await show('file')
  await page.evaluate(() => { window.__ask.calls.length = 0 })
  await page.locator('.ask-btn-primary').click()
  check('a required choice stops the primary button with a clear message', (await page.locator('.ask-error').textContent())?.includes('needs an answer') && !(await page.evaluate(() => window.__ask.calls.some(c => c.name.endsWith('jarvis_ask_submit')))))
  await page.locator('.ask-radio-row').first().click()
  await page.locator('.ask-btn-primary').click()
  await page.waitForTimeout(100)
  const picked = await page.evaluate(() => window.__ask.calls.find(c => c.name.endsWith('jarvis_ask_submit'))?.args?.values)
  check('a clicked file is sent as its option id (never a path)', picked && /^f\d+$/.test(picked.file ?? ''), JSON.stringify(picked))

  // hostile / odd forms: drawn as text only
  await show('fallback')
  check('an unknown component falls back to a plain list with a note', await page.locator('.ask-info-row').count() >= 2 && (await page.locator('.ask-notes').textContent())?.includes('plain list'))
  check('the panel has no link, frame, script or form element', await page.locator('.ask a, .ask iframe, .ask script, .ask form').count() === 0)

  // a busy form: progress with real numbers, buttons locked except close
  await show('cleanup-cleaning')
  check('cleaning shows a progress bar with real numbers', (await page.locator('.ask-working-text').textContent())?.includes('GB') && await page.locator('.ask-bar-fill').count() === 1)
  check('while it works only Close stays enabled', await page.locator('.ask-btn:not(:disabled)').count() === 1)
  const skeleton = await show('cleanup-scanning')
  check('while JARVIS is still looking, the panel is already there with a skeleton', await page.locator('.ask-skel').count() === 4, `${skeleton.toFixed(0)} ms`)

  // reduced motion
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await show('steps')
  const animations = await page.evaluate(() => [...document.querySelectorAll('.ask, .ask-step-running .ask-stepmark')].map(e => getComputedStyle(e).animationName))
  check('prefers-reduced-motion stops the animations', animations.every(a => a === 'none'), animations.join(','))
  await page.emulateMedia({ reducedMotion: 'no-preference' })

  // ---- D63: the form used by voice (fake transcript source) ----
  await page.setViewportSize({ width: 360, height: 640 })
  await setTheme('dark')
  const A = fn => page.evaluate(fn)
  await A(() => { window.__ask.calls.length = 0 })
  await show('voice-schedule')
  check('opening a form starts no recognizer', await A(() => window.__ask.stats.starts === 0 && window.__ask.voice.mode.value === 'off'))
  const mics = await page.locator('.ask-body .ask-mic').count()
  check('every text, date and heading field has a microphone (heading, day, time, note = 4)', mics === 4, String(mics))
  check('the header has a microphone for commands and a button that reads the form', await page.locator('.ask-head .ask-mic').count() === 1 && await page.locator('.ask-head .ask-read').count() === 1)
  check('the editable heading is a plain input with the heading look', await page.locator('input.ask-heading-input').count() === 1 && (await page.locator('input.ask-heading-input').inputValue()) === 'Call the dentist')
  const headFits = await page.evaluate(() => { const h = document.querySelector('.ask-head'); return h.scrollWidth <= h.clientWidth + 1 })
  check('the header with its two voice buttons fits in 360 px', headFits)

  // dictation into the heading with the mouse
  await page.locator('.ask-heading-field .ask-mic').click()
  await page.waitForTimeout(80)
  check('pressing a field microphone starts the recognizer once and marks it listening', await A(() => window.__ask.stats.starts === 1 && window.__ask.voice.mode.value === 'dictate') && (await page.locator('.ask-heading-field .ask-mic').getAttribute('aria-pressed')) === 'true')
  check('a field that is listening cannot be typed in (it is read-only while the words come)', await page.locator('input.ask-heading-input').getAttribute('readonly') !== null)
  await A(() => window.__ask.hear('call the dent'))
  await page.waitForTimeout(40)
  check('interim words show live in the field', (await page.locator('input.ask-heading-input').inputValue()) === 'Call the dentist call the dent')
  await page.screenshot({ path: join(shots, 'ask-voice-dictating-360x640-dark.png') })
  await page.evaluate(() => window.__ask.say('call the dentist at five'))
  await page.waitForTimeout(80)
  check('the final text replaces the interim words', (await page.locator('input.ask-heading-input').inputValue()) === 'Call the dentist call the dentist at five')
  await page.evaluate(() => window.__ask.say('clear'))
  await page.waitForTimeout(80)
  check('"clear" empties the field', (await page.locator('input.ask-heading-input').inputValue()) === '')
  await page.keyboard.press('Alt+z')
  await page.waitForTimeout(40)
  check('Alt+Z undoes the last change made by voice', (await page.locator('input.ask-heading-input').inputValue()).startsWith('Call the dentist'))
  await page.evaluate(() => window.__ask.say('next field'))
  await page.waitForTimeout(80)
  check('"next field" moves the dictation and the focus to the next field', await A(() => window.__ask.voice.activeField.value === 'day') && await page.evaluate(() => document.activeElement?.dataset?.voiceInput === 'day'))
  await page.evaluate(() => window.__ask.say('tomorrow'))
  await page.waitForTimeout(80)
  const day = await page.locator('input[type=date]').inputValue()
  check('a date field takes "tomorrow" as a date', /^\d{4}-\d{2}-\d{2}$/.test(day), day)
  await page.keyboard.press('Escape')
  await page.waitForTimeout(60)
  check('Escape stops the microphone first', await A(() => window.__ask.voice.mode.value === 'off'))
  check('and does not cancel the form', await page.locator('.ask').count() === 1 && !(await A(() => window.__ask.calls.some(c => c.name.endsWith('jarvis_ask_submit')))), JSON.stringify(await A(() => window.__ask.calls.map(c => c.name))))
  check('the recognizer was told to stop', await A(() => window.__ask.stats.cancels >= 1))

  // keyboard parity: Alt+M in a focused field dictates, Alt+M again stops
  await page.locator('input[data-voice-input="note"]').focus()
  await page.keyboard.press('Alt+m')
  await page.waitForTimeout(80)
  check('Alt+M dictates into the field that has the focus', await A(() => window.__ask.voice.mode.value === 'dictate' && window.__ask.voice.activeField.value === 'note'))
  await page.keyboard.press('Alt+m')
  await page.waitForTimeout(60)
  check('Alt+M again stops it', await A(() => window.__ask.voice.mode.value === 'off'))

  // the form closing ends listening
  await page.locator('.ask-heading-field .ask-mic').click()
  await page.waitForTimeout(60)
  const cancelsBefore = await A(() => window.__ask.stats.cancels)
  await A(() => window.__ask.clear())
  await page.waitForTimeout(60)
  check('closing the form stops the recognizer', await A(() => window.__ask.voice.mode.value === 'off') && await A(() => window.__ask.stats.cancels) > cancelsBefore)

  // spoken commands on the cleaner
  await show('cleanup-ready')
  await A(() => { window.__ask.calls.length = 0; window.__ask.spoken.length = 0; window.__ask.setReply({ ok: true, done: false }) })
  check('ambient speech (voice activity detection only) changes nothing', (await page.evaluate(() => window.__ask.command('untick browser cache', 'speech'))) === false)
  check('a PIN said aloud is refused and goes nowhere', (await page.evaluate(() => window.__ask.command('my pin is 4821'))) === true && (await page.locator('.ask-voice').textContent()).includes('JARVIS console') && !(await page.locator('.ask-voice').textContent()).includes('4821'))
  await page.evaluate(() => window.__ask.command('approve it'))
  await page.evaluate(() => window.__ask.command('say submit now'))
  check('"approve it" and "say submit now" press nothing', !(await A(() => window.__ask.calls.some(c => c.name.endsWith('jarvis_ask_submit')))))
  const totalBefore = await page.locator('.ask-total strong').textContent()
  await page.evaluate(() => window.__ask.command('temp files tick teesey'))
  await page.waitForTimeout(60)
  check('a Tenglish command unticks the row, shows what was heard and highlights it', await page.locator('.ask-hit').count() >= 1 && (await page.locator('.ask-voice').textContent()).includes('Unticking Temporary files') && (await page.locator('.ask-total strong').textContent()) !== totalBefore)
  await page.screenshot({ path: join(shots, 'ask-voice-heard-360x640-dark.png') })
  check('it was confirmed in words', await A(() => window.__ask.spoken.some(t => t.startsWith('Unticking'))))
  await page.waitForTimeout(900) // the echo window of what was just spoken
  await page.evaluate(() => window.__ask.command('undo that change'))
  await page.waitForTimeout(40)
  check('"undo that change" puts the total back', (await page.locator('.ask-total strong').textContent()) === totalBefore)
  await page.waitForTimeout(900)
  await page.evaluate(() => window.__ask.command('tick temp'))
  await page.waitForTimeout(80)
  check('two rows fit "temp": the panel asks "Did you mean A or B?" in words and on screen', (await page.locator('.ask-voice-qtext').textContent()).startsWith('Did you mean') && await page.locator('.ask-voice-choice').count() >= 3 && await A(() => window.__ask.spoken.some(t => t.startsWith('Did you mean'))))
  await page.screenshot({ path: join(shots, 'ask-voice-question-360x640-dark.png') })
  await page.locator('.ask-voice-choice').first().click()
  await page.waitForTimeout(60)
  check('answering the question by click does the action', await page.locator('.ask-voice-qtext').count() === 0)
  await page.waitForTimeout(900)
  await page.evaluate(() => window.__ask.command('tick printer drivers'))
  check('a field that is not there: "I did not find that field."', (await page.locator('.ask-voice').textContent()).includes('I did not find that field.'))
  await A(() => { window.__ask.calls.length = 0 })
  await page.waitForTimeout(900)
  await page.evaluate(() => window.__ask.command('tick browser cache and clean'))
  check('a submit mixed with ticking only ticks', !(await A(() => window.__ask.calls.some(c => c.name.endsWith('jarvis_ask_submit')))))
  await page.waitForTimeout(900)
  await page.evaluate(() => window.__ask.command('do not clean yet'))
  check('"do not clean yet" presses nothing', !(await A(() => window.__ask.calls.some(c => c.name.endsWith('jarvis_ask_submit')))))
  await page.waitForTimeout(900)
  await page.evaluate(() => window.__ask.command('టెంపరరీ ఫైల్స్ టిక్ చేయి'))
  check('a Telugu command is translated on this laptop (fake) and then understood', (await page.locator('.ask-voice').textContent()).includes('understood as'))
  await page.waitForTimeout(900)
  await page.evaluate(() => window.__ask.command('clean'))
  await page.waitForTimeout(100)
  const cleanCall = await A(() => window.__ask.calls.find(c => c.name.endsWith('jarvis_ask_submit')))
  check('"clean" at last presses the primary button with what is ticked', cleanCall?.args?.button === 'clean' && Array.isArray(cleanCall?.args?.values?.safe) && cleanCall.args.values.safe.length > 0, JSON.stringify(cleanCall?.args?.button))

  // nothing ticked: no clean
  await show('cleanup-ready')
  await A(() => { window.__ask.calls.length = 0 })
  await A(() => window.__ask.controller.setValue('safe', []))
  await page.evaluate(() => window.__ask.command('clean'))
  check('"clean" with nothing ticked does not run', !(await A(() => window.__ask.calls.some(c => c.name.endsWith('jarvis_ask_submit')))))

  // read aloud, with a Telugu value that is not spoken
  await show('voice-schedule')
  await A(() => window.__ask.controller.setValue('what', 'డాక్టర్‌కి ఫోన్ చేయి'))
  await A(() => { window.__ask.spoken.length = 0 })
  await page.locator('.ask-body').focus().catch(() => {})
  await page.locator('.ask-heading-input').focus()
  await page.keyboard.press('Alt+r')
  await page.waitForTimeout(200)
  const readText = await page.locator('.ask-voice-read').textContent()
  check('Alt+R reads the form: heading, labels and values are shown and spoken, short', await A(() => window.__ask.spoken.some(t => t.startsWith('Remind me'))) && readText.includes('Reminder') && readText.includes('డాక్టర్'))
  check('the Telugu text is shown but not spoken, and the panel says which', !(await A(() => window.__ask.spoken.join(' ').includes('డాక్టర్'))) && readText.includes('not spoken'))
  await page.screenshot({ path: join(shots, 'ask-voice-read-360x640-dark.png') })

  // the look of the voice parts at every size and theme
  for (const theme of THEMES) {
    await setTheme(theme)
    for (const [w, h] of SIZES) {
      await page.setViewportSize({ width: w, height: h })
      await show('voice-schedule')
      await snap('voice-form', w, h, theme, 0)
      await page.locator('.ask-heading-field .ask-mic').click()
      await A(() => window.__ask.hear('call the dentist at'))
      await page.waitForTimeout(260)
      await snap('voice-listening', w, h, theme, 0)
      await page.keyboard.press('Escape')
      await show('cleanup-ready')
      await page.evaluate(() => window.__ask.command('temp files tick teesey'))
      await page.waitForTimeout(80)
      await snap('voice-heard', w, h, theme, 0)
      await page.waitForTimeout(900)
      await page.evaluate(() => window.__ask.command('tick temp'))
      await page.waitForTimeout(80)
      await snap('voice-question', w, h, theme, 0)
      await show('voice-save')
      await A(() => window.__ask.voice.readAloud())
      await page.waitForTimeout(100)
      await snap('voice-read', w, h, theme, 0)
    }
  }
  check('no horizontal scroll with the voice parts at 360, 480 and 1280 px, dark and light', overflow.length === 0, overflow.join('; '))

  // reduced motion also calms the microphone
  await page.setViewportSize({ width: 360, height: 640 })
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await show('voice-schedule')
  await page.locator('.ask-heading-field .ask-mic').click()
  await page.waitForTimeout(80)
  const micAnim = await page.evaluate(() => getComputedStyle(document.querySelector('.ask-heading-field .ask-mic'), '::after').animationName)
  check('prefers-reduced-motion stops the listening ring', micAnim === 'none', micAnim)
  await page.keyboard.press('Escape')
  await page.emulateMedia({ reducedMotion: 'no-preference' })

  check('no request left the harness', unexpected.length === 0, unexpected.join(', '))
  check('no page error', pageErrors.length === 0, pageErrors.join(' | ').slice(0, 300))
  await browser.close()
}
finally {
  server.close()
}
if (failures.length) {
  console.log(`\n${failures.length} check(s) failed: ${failures.join('; ')}`)
  process.exit(1)
}
console.log(`\nall checks passed; screenshots are in ${shots}`)
