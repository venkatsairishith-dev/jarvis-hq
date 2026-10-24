/**
 * JARVIS: starts the real built app (out/main) under a virtual display and lists every console error, uncaught error and
 * Content-Security-Policy violation from every window, so a policy that breaks a feature (a model that cannot render, a
 * worker that cannot compile) shows up here and not on the owner's screen.
 *
 *   node scripts/render-smoke.mjs [seconds]        default 30; Linux with xvfb-run and the Electron binary installed
 *
 * Exit 1 when a window reported "Refused to ..." or an EvalError / "unsafe-eval" error.
 */
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

import { _electron as electron } from 'playwright'

const seconds = Number(process.argv[2] ?? 30)
const app = fileURLToPath(new URL('..', import.meta.url))
const userData = mkdtempSync(join(tmpdir(), 'jarvis-face-smoke-'))
const found = []
const watch = (page, label) => {
  page.on('console', async (message) => {
    if (/DataCloneError/.test(message.text()) && process.env.SMOKE_STACKS)
      console.log('STACK', (await message.args()[0]?.evaluate(error => error?.stack).catch(() => '')) ?? '')
    const text = message.text()
    if (message.type() === 'error' || /Refused to|unsafe-eval|Content Security Policy/i.test(text))
      found.push(`${label} console.${message.type()}: ${text.slice(0, 300).replace(/\s+/g, ' ')} @ ${message.location().url.split('/').pop()}:${message.location().lineNumber}`)
  })
  page.on('pageerror', error => found.push(`${label} pageerror: ${String(error.stack ?? error).slice(0, 300).replace(/\s+/g, ' ')}`))
  page.on('requestfailed', request => found.push(`${label} request failed: ${request.url().slice(0, 120)} (${request.failure()?.errorText})`))
}
const launched = await electron.launch({ args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist', `--user-data-dir=${userData}`, 'out/main/index.js'], cwd: app })
launched.windows().forEach((page, i) => watch(page, `window${i}`))
launched.on('window', page => watch(page, `window:${page.url().slice(-40)}`))
await new Promise(done => setTimeout(done, seconds * 1000))
const probes = launched.windows().map(page => page.url().slice(-60))
await launched.close().catch(() => {})
rmSync(userData, { recursive: true, force: true })
console.log('windows:', JSON.stringify(probes))
// AIRI's cloud calls go to http://127.0.0.1:1 on purpose (JARVIS_NO_CLOUD_URL): they fail at once and nothing leaves the PC.
const expected = line => /127\.0\.0\.1:1\/|ERR_UNSAFE_PORT|Autofill\./.test(line)
const unique = [...new Set(found)].filter(line => !expected(line))
unique.forEach(line => console.log(line))
const bad = unique.filter(line => /Refused to|unsafe-eval|EvalError|Content Security Policy/i.test(line))
console.log(bad.length ? `FAIL: ${bad.length} policy problem(s)` : `ok: no policy problems (${unique.length} other message(s))`)
process.exit(bad.length ? 1 : 0)
