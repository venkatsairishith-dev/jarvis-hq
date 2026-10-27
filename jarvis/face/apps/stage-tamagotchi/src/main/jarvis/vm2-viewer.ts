import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { env } from 'node:process'

/**
 * The one address the face's network lock opens for VM2's takeover viewer (D79, 2026-10-27).
 *
 * The owner can take over VM2's desktop (a noVNC / KasmVNC page on the Tailscale address) to solve a CAPTCHA. The lock stays shut for everything else: the ONLY thing added is the exact
 * host and port written in `jarvis/config/vm2.json` (`viewer_url`), and only when that file exists, says `enabled: true`, and the address is a Tailscale address or this machine,
 * http(s), with no user name, password, token or key in it (the viewer asks for its own password, which JARVIS never reads). The file is console-only: the kernel's
 * SAFETY_COMPONENTS and the push gate list it, so an agent cannot edit it. Nothing a page, a model or a message says can add an address here: it comes from the file alone.
 *
 * Same rules as plugins/_shared/vm2.py `check_endpoint` (each side has its own test table with the same kinds of address).
 */

export interface ViewerTarget {
  host: string
  port: number
}

const SECRETY = /pass|pwd|token|secret|key|auth|sid|session|cookie|credential|bearer|jwt|otp|code/i

function bare(host: string): string {
  const h = host.toLowerCase()
  return h.startsWith('[') && h.endsWith(']') ? h.slice(1, -1) : h
}

function hostAllowed(host: string): boolean {
  const h = bare(host)
  if (h === 'localhost' || h.endsWith('.localhost') || h === '::1')
    return true
  if (/^127(?:\.\d{1,3}){3}$/.test(h))
    return true
  if (/^[a-z0-9][a-z0-9.-]{0,78}\.ts\.net$/.test(h))
    return true
  const m = /^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/.exec(h)
  return !!m && Number(m[1]) === 100 && Number(m[2]) >= 64 && Number(m[2]) <= 127
}

/** The viewer's host and port from the text of vm2.json, or null (not enabled, no address, or an address the rules refuse). */
export function parseViewerTarget(text: string | null | undefined): ViewerTarget | null {
  if (!text)
    return null
  let data: unknown
  try {
    data = JSON.parse(text.replace(/^\uFEFF/, ''))
  }
  catch {
    return null
  }
  if (!data || typeof data !== 'object')
    return null
  const d = data as Record<string, unknown>
  if (d.enabled !== true || typeof d.viewer_url !== 'string' || !d.viewer_url.trim())
    return null
  let url: URL
  try {
    url = new URL(d.viewer_url.trim())
  }
  catch {
    return null
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:')
    return null
  if (url.username || url.password || !hostAllowed(url.hostname))
    return null
  for (const [k, v] of url.searchParams) {
    if (SECRETY.test(k) || SECRETY.test(v))
      return null
  }
  if (SECRETY.test(url.hash.replace(/^#/, '')))
    return null
  const port = url.port ? Number(url.port) : (url.protocol === 'https:' ? 443 : 80)
  return { host: bare(url.hostname), port }
}

/** Where vm2.json may be: JARVIS_VM2_CONFIG, then the repository's config next to the install the owner started the face from. */
export function viewerConfigPaths(e: Record<string, string | undefined> = env): string[] {
  const out: string[] = []
  if (e.JARVIS_VM2_CONFIG?.trim())
    out.push(e.JARVIS_VM2_CONFIG.trim())
  if (e.JARVIS_HQ?.trim())
    out.push(join(e.JARVIS_HQ.trim(), 'jarvis', 'config', 'vm2.json'))
  out.push('D:\\jarvis\\hq\\jarvis\\config\\vm2.json')
  return out
}

type Reader = (path: string) => string | null

const fileReader: Reader = (path) => {
  try {
    return readFileSync(path, 'utf8')
  }
  catch {
    return null
  }
}

let reader: Reader = fileReader
let cache: { at: number, target: ViewerTarget | null } | null = null
const TTL_MS = 5000

/** For tests: replace the file reader and forget the cache. */
export function setViewerReader(next: Reader | null): void {
  reader = next ?? fileReader
  cache = null
}

/** The viewer target now (the first config file that exists decides; cached for a few seconds because every request asks). */
export function loadViewerTarget(e: Record<string, string | undefined> = env, now: number = Date.now()): ViewerTarget | null {
  if (cache && now - cache.at < TTL_MS)
    return cache.target
  let target: ViewerTarget | null = null
  for (const p of viewerConfigPaths(e)) {
    const text = reader(p)
    if (text !== null) {
      target = parseViewerTarget(text)
      break
    }
  }
  cache = { at: now, target }
  return target
}

/** The port a URL really uses (the default of its scheme when none is written; ws and wss follow http and https). */
export function urlPort(u: URL): number {
  if (u.port)
    return Number(u.port)
  return u.protocol === 'https:' || u.protocol === 'wss:' ? 443 : 80
}

/** True only for exactly the viewer's host AND port. */
export function isViewerTarget(host: string, port: number | string | undefined, target: ViewerTarget | null = loadViewerTarget()): boolean {
  if (!target)
    return false
  const p = port === undefined || port === '' ? NaN : Number(port)
  return bare(host) === target.host && p === target.port
}
