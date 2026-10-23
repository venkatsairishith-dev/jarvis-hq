/**
 * JARVIS: starts the real built app (out/main) under a virtual display and watches it.
 *
 *   node scripts/launch-check.mjs [seconds]        default 45; Linux with xvfb-run and the Electron binary installed
 *
 * It exits 1 when, during that time,
 *   - the Content-Security-Policy refused something the app itself did,
 *   - the network lock had to block a request (the app tried to reach a host that is not this PC, the tailnet or
 *     the owner's list: telemetry, an update check, an account server ...),
 *   - the app exited before the time was up.
 * It prints every blocked host, so a new outside call shows up here before it reaches an owner's PC.
 */
import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const seconds = Number(process.argv[2] ?? 45)
const app = fileURLToPath(new URL('..', import.meta.url))
const electron = join(app, 'node_modules', 'electron', 'dist', 'electron')
const userData = mkdtempSync(join(tmpdir(), 'jarvis-face-launch-'))

const child = spawn('xvfb-run', ['-a', '-s', '-screen 0 1280x800x24', electron, '--no-sandbox', `--user-data-dir=${userData}`, '--enable-logging=stderr', 'out/main/index.js'], { cwd: app, stdio: ['ignore', 'pipe', 'pipe'] })
let log = ''
child.stdout.on('data', chunk => log += chunk)
child.stderr.on('data', chunk => log += chunk)
let exited = null
child.on('exit', (code, signal) => exited = { code, signal })

await new Promise(done => setTimeout(done, seconds * 1000))
const aliveAtTheEnd = exited === null
if (aliveAtTheEnd)
  child.kill('SIGTERM')
await new Promise(done => setTimeout(done, 1500))
rmSync(userData, { recursive: true, force: true })

const strip = text => text.replace(/\u001B\[[0-9;]*m/g, '')
const lines = strip(log).split('\n')
const refused = lines.filter(line => /Refused to|Content Security Policy/i.test(line))
const blocked = new Map()
for (const line of lines) {
  const m = /jarvis:network-lock\].*blocked a[^ ]* (?:main-process )?(?:request|connection) to (\S+)/.exec(line)
  if (m)
    blocked.set(m[1], (blocked.get(m[1]) ?? 0) + 1)
}

console.log(`app alive after ${seconds} s: ${aliveAtTheEnd ? 'yes' : `NO (${JSON.stringify(exited)})`}`)
console.log(`policy refusals: ${refused.length}`)
for (const line of refused.slice(0, 10))
  console.log(`  ${line.slice(0, 200)}`)
console.log(`hosts the network lock had to block: ${blocked.size}`)
for (const [host, count] of blocked)
  console.log(`  ${host} x${count}`)

process.exit(aliveAtTheEnd && refused.length === 0 && blocked.size === 0 ? 0 : 1)
