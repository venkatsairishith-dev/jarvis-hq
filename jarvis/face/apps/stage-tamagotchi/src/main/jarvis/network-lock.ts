import type { Session } from 'electron'

import { env } from 'node:process'

/**
 * The face may reach this PC and the owner's tailnet, and nothing else.
 *
 * AIRI ships a cloud account, product analytics, an auto-updater and several web integrations. JARVIS removes the ones it
 * can find, and this lock covers the rest: every request from every window, and every `fetch` in the main process, is
 * checked here. Anything else is cancelled before it leaves the machine.
 *
 * Allowed: loopback (localhost, 127.0.0.0/8, ::1), the tailnet range 100.64.0.0/10, and the hosts the owner lists in
 * `JARVIS_FACE_ALLOW_HOSTS` (comma separated; `*.example.com` allows sub-domains). Non-network schemes (file:, data:,
 * blob:, devtools:, the app's own protocols) are local and allowed.
 *
 * Not covered: native modules that open sockets themselves. The face carries none that talk to the network.
 */

const NETWORK_SCHEMES = new Set(['http:', 'https:', 'ws:', 'wss:', 'ftp:'])

function bare(host: string): string {
  const h = host.toLowerCase()
  return h.startsWith('[') && h.endsWith(']') ? h.slice(1, -1) : h
}

function isLoopback(host: string): boolean {
  const h = bare(host)
  if (h === 'localhost' || h.endsWith('.localhost') || h === '::1' || h === '::ffff:127.0.0.1')
    return true
  return /^127(?:\.\d{1,3}){3}$/.test(h)
}

function isTailnet(host: string): boolean {
  const m = /^(\d{1,3})\.(\d{1,3})\.\d{1,3}\.\d{1,3}$/.exec(bare(host))
  if (!m)
    return false
  const a = Number(m[1])
  const b = Number(m[2])
  return a === 100 && b >= 64 && b <= 127
}

/** Parses `JARVIS_FACE_ALLOW_HOSTS`. */
export function parseAllowHosts(value: string | undefined): string[] {
  return (value ?? '')
    .split(',')
    .map(item => item.trim().toLowerCase())
    .filter(item => item.length > 0)
}

function isListed(host: string, allowHosts: string[]): boolean {
  const h = bare(host)
  return allowHosts.some((entry) => {
    if (entry.startsWith('*.'))
      return h.endsWith(entry.slice(1))
    return h === entry
  })
}

/**
 * Hosts the face's own weather card needs (owner, 2026-10-25: "tell me the weather in Paris" failed because this lock refused them). A request carries
 * only a city name and coordinates, no key and nothing private. Source: https://open-meteo.com/en/docs
 */
export const BUILT_IN_ALLOW_HOSTS = ['api.open-meteo.com', 'geocoding-api.open-meteo.com']

/** True when the face may talk to `host` (a name or an address, with or without IPv6 brackets). */
export function isAllowedHost(host: string, allowHosts: string[] = parseAllowHosts(env.JARVIS_FACE_ALLOW_HOSTS)): boolean {
  return isLoopback(host) || isTailnet(host) || isListed(host, BUILT_IN_ALLOW_HOSTS) || isListed(host, allowHosts)
}

/** True when the face may open `url`. A URL that cannot be parsed is refused. */
export function isAllowedUrl(url: string, allowHosts: string[] = parseAllowHosts(env.JARVIS_FACE_ALLOW_HOSTS)): boolean {
  let parsed: URL
  try {
    parsed = new URL(url)
  }
  catch {
    return false
  }
  if (!NETWORK_SCHEMES.has(parsed.protocol))
    return true
  return isAllowedHost(parsed.hostname, allowHosts)
}

function urlOf(input: unknown): string {
  if (typeof input === 'string')
    return input
  if (input instanceof URL)
    return input.href
  if (input && typeof input === 'object' && 'url' in input)
    return String((input as { url: unknown }).url)
  return ''
}

/** Wraps the main process's global `fetch` so it refuses hosts outside the allowlist. */
export function lockGlobalFetch(log: (message: string) => void = () => {}): void {
  const original = globalThis.fetch
  if (!original || (original as { jarvisLocked?: boolean }).jarvisLocked)
    return
  const locked = ((input: Parameters<typeof fetch>[0], init?: Parameters<typeof fetch>[1]) => {
    const url = urlOf(input)
    if (!isAllowedUrl(url)) {
      const host = (() => {
        try {
          return new URL(url).host
        }
        catch {
          return 'an unreadable address'
        }
      })()
      log(`blocked a main-process request to ${host}`)
      return Promise.reject(new Error(`JARVIS network lock: ${host} is not on the allowlist`))
    }
    return original(input, init)
  }) as typeof fetch & { jarvisLocked?: boolean }
  locked.jarvisLocked = true
  globalThis.fetch = locked
}

/** Cancels every request of `session` whose address is outside the allowlist. */
export function installNetworkLock(session: Session, log: (message: string) => void = () => {}): void {
  session.webRequest.onBeforeRequest((details, callback) => {
    const allowed = isAllowedUrl(details.url)
    if (!allowed) {
      let host = 'an unreadable address'
      try {
        host = new URL(details.url).host
      }
      catch {}
      log(`blocked a request to ${host}`)
    }
    callback({ cancel: !allowed })
  })
}
