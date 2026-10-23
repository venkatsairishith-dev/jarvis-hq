/**
 * JARVIS: checks every file the build downloaded against downloads.lock.json.
 *
 *   node scripts/verify-downloads.mjs        exit 1 when a file is missing or its SHA-256 differs
 *
 * `pnpm run build` runs it after electron-vite, so an installer is never made from a download that changed.
 */
import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'

const base = fileURLToPath(new URL('..', import.meta.url))
const lock = JSON.parse(readFileSync(resolve(base, 'downloads.lock.json'), 'utf8'))
let bad = 0
for (const [path, { sha256, from }] of Object.entries(lock.files)) {
  const file = resolve(base, path)
  if (!existsSync(file)) {
    console.error(`MISSING  ${path}\n         expected from ${from}`)
    bad++
    continue
  }
  const actual = createHash('sha256').update(readFileSync(file)).digest('hex')
  if (actual !== sha256) {
    console.error(`CHANGED  ${path}\n         pinned ${sha256}\n         found  ${actual}\n         from   ${from}`)
    bad++
  }
  else {
    console.log(`ok       ${path}`)
  }
}
process.exit(bad ? 1 : 0)
