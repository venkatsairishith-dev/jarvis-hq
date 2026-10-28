import net from 'node:net'

import { env } from 'node:process'

import { BUILT_IN_ALLOW_HOSTS, isAllowedHost, parseAllowHosts } from './network-lock'
import { isViewerTarget, loadViewerTarget } from './vm2-viewer'

/**
 * The lower half of the face's network lock (network-lock.ts is the upper half, for requests that Chromium and `fetch` make).
 *
 * Outbound: every TCP connection the main process makes (http, https, WebSocket, undici and anything else built on
 * `net.Socket`) may reach only loopback, the tailnet and hosts the owner listed in JARVIS_FACE_ALLOW_HOSTS.
 * Inbound: every server the main process starts listens on loopback only. AIRI let its channel server listen on all
 * interfaces (for phone pairing); here a request for 0.0.0.0, `::` or a LAN address is quietly turned into 127.0.0.1.
 * Local pipes and sockets (named pipes, Unix sockets) are left alone.
 *
 * Chromium's own switches close what a request filter cannot see: name resolution for hosts that are not allowed, and
 * WebRTC's UDP, which does not go through the request filter at all.
 */

export type ConnectTarget = { kind: 'pipe' } | { kind: 'tcp', host: string, port?: number }

function isPipeName(value: unknown): boolean {
  return typeof value === 'string' && Number.isNaN(Number(value))
}

/** What `socket.connect(...args)` is about to connect to, read the way Node reads its arguments. */
export function connectTarget(args: unknown[]): ConnectTarget {
  const first = args[0]
  // net.createConnection and tls.connect pass one already-normalized array: [options, callback].
  if (Array.isArray(first))
    return connectTarget(first)
  if (first && typeof first === 'object') {
    const options = first as { path?: string, host?: string, port?: number | string }
    if (options.path)
      return { kind: 'pipe' }
    return { kind: 'tcp', host: String(options.host ?? 'localhost'), port: options.port === undefined ? undefined : Number(options.port) }
  }
  if (isPipeName(first))
    return { kind: 'pipe' }
  if (typeof first === 'number' || (typeof first === 'string' && first !== ''))
    return { kind: 'tcp', host: typeof args[1] === 'string' ? args[1] : 'localhost', port: Number(first) }
  // A shape that is not understood is treated as a connection to an unknown host, which the lock refuses.
  return { kind: 'tcp', host: '(unreadable connect arguments)' }
}

export interface ListenRewrite { args: unknown[], rewritten: boolean }

/** `server.listen(...args)` with the interface forced to loopback. Pipes, Unix sockets and handles are returned unchanged. */
export function loopbackListenArgs(args: unknown[]): ListenRewrite {
  const first = args[0]
  const isLoopbackName = (host: unknown) => typeof host === 'string' && isAllowedHost(host, []) && !/^100\./.test(host)
  if (first && typeof first === 'object') {
    const options = first as { path?: string, fd?: number, host?: string, port?: number | string }
    if (options.path || options.fd !== undefined || options.port === undefined)
      return { args, rewritten: false }
    if (isLoopbackName(options.host))
      return { args, rewritten: false }
    return { args: [{ ...options, host: '127.0.0.1' }, ...args.slice(1)], rewritten: true }
  }
  if (first === undefined || first === null || (typeof first === 'string' && isPipeName(first)))
    return { args, rewritten: false }
  // listen(port[, host][, backlog][, callback])
  const rest = args.slice(1)
  if (typeof rest[0] === 'string') {
    if (isLoopbackName(rest[0]))
      return { args, rewritten: false }
    return { args: [first, '127.0.0.1', ...rest.slice(1)], rewritten: true }
  }
  return { args: [first, '127.0.0.1', ...rest], rewritten: true }
}

/** Patches the main process's sockets and servers. Call once, before any window or service starts. */
export function lockNodeSockets(log: (message: string) => void = () => {}): void {
  const socketProto = net.Socket.prototype as unknown as { connect: (...a: unknown[]) => unknown, jarvisLocked?: boolean }
  if (!socketProto.jarvisLocked) {
    const connect = socketProto.connect
    socketProto.connect = function lockedConnect(this: net.Socket, ...args: unknown[]) {
      const target = connectTarget(args)
      if (target.kind === 'tcp' && !isAllowedHost(target.host) && !isViewerTarget(target.host, target.port)) {
        log(`blocked a main-process connection to ${target.host}`)
        const error = new Error(`JARVIS network lock: ${target.host} is not on the allowlist`)
        queueMicrotask(() => this.destroy(error))
        return this
      }
      return connect.apply(this, args)
    }
    socketProto.jarvisLocked = true
  }

  const serverProto = net.Server.prototype as unknown as { listen: (...a: unknown[]) => unknown, jarvisLocked?: boolean }
  if (!serverProto.jarvisLocked) {
    const listen = serverProto.listen
    serverProto.listen = function lockedListen(this: net.Server, ...args: unknown[]) {
      const { args: next, rewritten } = loopbackListenArgs(args)
      if (rewritten)
        log('a server asked to listen outside this PC; it listens on 127.0.0.1 only')
      return listen.apply(this, next)
    }
    serverProto.jarvisLocked = true
  }
}

/**
 * Chromium switches that back up the request filter: hostnames other than loopback and the owner's list do not resolve,
 * and WebRTC may not use UDP outside a proxy. Tailnet hosts work by address; to use a MagicDNS name, list it in
 * JARVIS_FACE_ALLOW_HOSTS (a `.ts.net` name can also be a public Funnel address, so none is allowed by default).
 */
export function chromiumLockSwitches(allowHosts: string[] = parseAllowHosts(env.JARVIS_FACE_ALLOW_HOSTS)): Array<[string, string]> {
  // JARVIS: the weather card's hosts are resolved too; without them "Failed to fetch" was all the owner saw (2026-10-26).
  const viewer = loadViewerTarget()      // D79: VM2's viewer name must resolve too (a Tailscale address needs no lookup; a MagicDNS name does)
  const exclude = ['localhost', '*.localhost', '127.0.0.1', ...BUILT_IN_ALLOW_HOSTS, ...allowHosts, ...(viewer ? [viewer.host] : [])].map(host => `EXCLUDE ${host}`)
  return [
    ['host-resolver-rules', ['MAP * ~NOTFOUND', ...exclude].join(', ')],
    ['force-webrtc-ip-handling-policy', 'disable_non_proxied_udp'],
  ]
}

export function applyChromiumLock(commandLine: { appendSwitch: (name: string, value?: string) => void }): void {
  for (const [name, value] of chromiumLockSwitches())
    commandLine.appendSwitch(name, value)
}
