import http from 'node:http'
import https from 'node:https'
import { createConnection, createServer } from 'node:net'
import tls from 'node:tls'

import { afterAll, beforeAll, describe, expect, it } from 'vitest'

import { chromiumLockSwitches, connectTarget, lockNodeSockets, loopbackListenArgs } from './socket-lock'

describe('socket lock: reading connect and listen arguments', () => {
  it('reads every form of net connect', () => {
    expect(connectTarget([{ host: 'example.com', port: 443 }])).toEqual({ kind: 'tcp', host: 'example.com', port: 443 })
    expect(connectTarget([{ port: 80 }])).toEqual({ kind: 'tcp', host: 'localhost', port: 80 })
    expect(connectTarget([443, 'example.com'])).toEqual({ kind: 'tcp', host: 'example.com', port: 443 })
    expect(connectTarget([8080])).toEqual({ kind: 'tcp', host: 'localhost', port: 8080 })
    expect(connectTarget(['\\\\.\\pipe\\jarvis'])).toEqual({ kind: 'pipe' })
    // net.createConnection and tls.connect hand over one normalized array (this bypassed the first version of the lock)
    expect(connectTarget([[{ host: 'example.com', port: 443 }, () => {}]])).toEqual({ kind: 'tcp', host: 'example.com', port: 443 })
    expect(connectTarget([[{ path: '/tmp/x.sock' }, null]])).toEqual({ kind: 'pipe' })
    expect(connectTarget([undefined])).toEqual({ kind: 'tcp', host: '(unreadable connect arguments)' })
    expect(connectTarget([])).toEqual({ kind: 'tcp', host: '(unreadable connect arguments)' })
    expect(connectTarget([{ path: '/tmp/x.sock' }])).toEqual({ kind: 'pipe' })
  })

  it('forces every listener onto loopback', () => {
    expect(loopbackListenArgs([6121])).toEqual({ args: [6121, '127.0.0.1'], rewritten: true })
    expect(loopbackListenArgs([6121, '0.0.0.0', () => {}]).args.slice(0, 2)).toEqual([6121, '127.0.0.1'])
    expect(loopbackListenArgs([6121, '::']).rewritten).toBe(true)
    expect(loopbackListenArgs([6121, '192.168.1.20']).rewritten).toBe(true)
    expect(loopbackListenArgs([6121, '100.125.91.51']).rewritten).toBe(true)
    expect(loopbackListenArgs([6121, '127.0.0.1']).rewritten).toBe(false)
    expect(loopbackListenArgs([6121, 'localhost']).rewritten).toBe(false)
    expect(loopbackListenArgs([{ port: 6121, host: '0.0.0.0' }]).args).toEqual([{ port: 6121, host: '127.0.0.1' }])
    expect(loopbackListenArgs([{ port: 6121 }]).args).toEqual([{ port: 6121, host: '127.0.0.1' }])
    expect(loopbackListenArgs(['/tmp/x.sock']).rewritten).toBe(false)
    expect(loopbackListenArgs([{ path: '\\\\.\\pipe\\x' }]).rewritten).toBe(false)
    expect(loopbackListenArgs([]).rewritten).toBe(false)
  })

  it('blocks name resolution except for this PC and the owner list, and WebRTC UDP', () => {
    const switches = Object.fromEntries(chromiumLockSwitches(['huggingface.co']))
    expect(switches['host-resolver-rules']).toBe('MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE *.localhost, EXCLUDE 127.0.0.1, EXCLUDE huggingface.co')
    expect(switches['force-webrtc-ip-handling-policy']).toBe('disable_non_proxied_udp')
  })
})

describe('socket lock on real sockets', () => {
  const warnings: string[] = []
  let port = 0
  const server = createServer(socket => socket.end('hello'))

  beforeAll(async () => {
    lockNodeSockets(message => warnings.push(message))
    await new Promise<void>(resolve => server.listen(0, resolve)) // asks for all interfaces; the lock narrows it
    port = (server.address() as { port: number }).port
  })

  afterAll(() => {
    server.close()
  })

  it('makes a server that asked for every interface listen on loopback only', () => {
    expect((server.address() as { address: string }).address).toBe('127.0.0.1')
    expect(warnings.some(w => w.includes('127.0.0.1 only'))).toBe(true)
  })

  it('lets this PC connect', async () => {
    const reply = await new Promise<string>((resolve, reject) => {
      const socket = createConnection({ host: '127.0.0.1', port }, () => {})
      let data = ''
      socket.on('data', chunk => data += chunk)
      socket.on('end', () => resolve(data))
      socket.on('error', reject)
    })
    expect(reply).toBe('hello')
  })

  it('refuses a connection to any other host before a packet is sent', async () => {
    const error = await new Promise<Error>((resolve) => {
      const socket = createConnection({ host: 'telemetry.example.com', port: 443 })
      socket.on('error', resolve)
      socket.on('connect', () => resolve(new Error('connected')))
    })
    expect(error.message).toContain('network lock')
    expect(warnings.some(w => w.includes('telemetry.example.com'))).toBe(true)
  })

  it('refuses http, https and tls clients, which are built on the same sockets', async () => {
    const failures = await Promise.all([
      new Promise<Error>(resolve => http.get('http://telemetry.example.com/', () => resolve(new Error('connected'))).on('error', resolve)),
      new Promise<Error>(resolve => https.get('https://telemetry.example.com/', () => resolve(new Error('connected'))).on('error', resolve)),
      new Promise<Error>((resolve) => {
        const socket = tls.connect({ host: 'telemetry.example.com', port: 443 }, () => resolve(new Error('connected')))
        socket.on('error', resolve)
      }),
    ])
    for (const error of failures)
      expect(error.message).toContain('network lock')
  })

  it('still lets http reach this PC', async () => {
    const web = http.createServer((_request, response) => response.end('ok'))
    await new Promise<void>(resolve => web.listen(0, '127.0.0.1', resolve))
    try {
      const text = await new Promise<string>((resolve, reject) => {
        http.get(`http://127.0.0.1:${(web.address() as { port: number }).port}/`, (response) => {
          let body = ''
          response.on('data', chunk => body += chunk)
          response.on('end', () => resolve(body))
        }).on('error', reject)
      })
      expect(text).toBe('ok')
    }
    finally {
      web.close()
    }
  })

  it('refuses a public address given as a number too', async () => {
    const error = await new Promise<Error>((resolve) => {
      const socket = createConnection(443, '93.184.216.34')
      socket.on('error', resolve)
      socket.on('connect', () => resolve(new Error('connected')))
    })
    expect(error.message).toContain('network lock')
  })
})
