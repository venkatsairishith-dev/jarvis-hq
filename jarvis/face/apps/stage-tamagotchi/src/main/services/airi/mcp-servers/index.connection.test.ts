import type { Server as HttpServer, IncomingMessage, ServerResponse } from 'node:http'

import { Buffer } from 'node:buffer'
import { randomUUID } from 'node:crypto'
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { createServer as createTcpServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js'
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { lockGlobalFetch } from '../../../jarvis/network-lock'
import { createMcpManager, reconnectCooldownMsec, resolveCallTimeoutMsec, setupMcpManager } from './index'

/**
 * These tests use the REAL MCP client of the face (no mocked transport) against a stand-in for the JARVIS tool server
 * (jarvis/bridge/face_tools.py). The stand-in has the same rules as the real one: a session per client, a 404
 * "Session not found" for an unknown session id, the Host must be 127.0.0.1:PORT or localhost:PORT, and no Origin.
 *
 * ROOT CAUSE this file guards (D49 in jarvis/docs/03_DECISIONS.md): the face connected to the tool server once, at start-up.
 * If the tool server was not listening yet (it takes 10 to 60 s to load the plugins), or was restarted later, the connection
 * was never made or its session was dead for good. `builtIn_mcpListTools` then answered "No MCP tools are available" and
 * every call said "mcp server is not running", so no call ever reached the tool server.
 */

const appMock = vi.hoisted(() => ({ getPath: vi.fn(), getVersion: vi.fn() }))

vi.mock('electron', () => ({ app: appMock, shell: { showItemInFolder: vi.fn() } }))
vi.mock('@guiiai/logg', () => {
  const sink: any = { debug: vi.fn(), warn: vi.fn(), error: vi.fn(), log: vi.fn() }
  sink.withError = vi.fn(() => sink)
  sink.withFields = vi.fn(() => sink)
  return { useLogg: vi.fn(() => ({ useGlobalConfig: () => sink })) }
})
vi.mock('../../../libs/bootkit/lifecycle', () => ({ onAppBeforeQuit: vi.fn() }))

interface FakeToolServer {
  port: number
  calls: string[]
  /** Makes every tool call wait this long before it answers. */
  slowBy: (ms: number) => void
  sessions: () => number
  close: () => Promise<void>
}

async function freePort(): Promise<number> {
  const probe = createServer()
  await new Promise<void>(resolve => probe.listen(0, '127.0.0.1', resolve))
  const port = (probe.address() as { port: number }).port
  await new Promise<void>(resolve => probe.close(() => resolve()))
  return port
}

async function startFakeToolServer(port: number): Promise<FakeToolServer> {
  const transports = new Map<string, StreamableHTTPServerTransport>()
  const calls: string[] = []
  let slowMs = 0

  async function bodyOf(req: IncomingMessage): Promise<unknown> {
    const chunks: Buffer[] = []
    for await (const chunk of req)
      chunks.push(chunk as Buffer)
    const text = Buffer.concat(chunks).toString('utf-8')
    return text ? JSON.parse(text) : undefined
  }

  const http: HttpServer = createServer(async (req: IncomingMessage, res: ServerResponse) => {
    const host = String(req.headers.host ?? '')
    if (req.headers.origin !== undefined || ![`127.0.0.1:${port}`, `localhost:${port}`].includes(host)) {
      res.writeHead(403).end('forbidden')
      return
    }
    if (req.url !== '/mcp') {
      res.writeHead(404).end()
      return
    }
    const sessionId = req.headers['mcp-session-id'] as string | undefined
    const body = req.method === 'POST' ? await bodyOf(req) : undefined
    let transport = sessionId ? transports.get(sessionId) : undefined
    if (!transport && sessionId) {
      res.writeHead(404, { 'content-type': 'application/json' })
        .end(JSON.stringify({ jsonrpc: '2.0', id: null, error: { code: -32600, message: 'Session not found' } }))
      return
    }
    if (!transport) {
      transport = new StreamableHTTPServerTransport({
        sessionIdGenerator: () => randomUUID(),
        onsessioninitialized: id => void transports.set(id, transport!),
      })
      const server = new Server({ name: 'jarvis', version: '0' }, { capabilities: { tools: {} } })
      server.setRequestHandler(ListToolsRequestSchema, async () => ({
        tools: [{ name: 'jarvis_ready', description: 'ready?', inputSchema: { type: 'object', properties: {} } }],
      }))
      server.setRequestHandler(CallToolRequestSchema, async (request) => {
        calls.push(request.params.name)
        if (slowMs > 0)
          await new Promise(resolve => setTimeout(resolve, slowMs))
        return { content: [{ type: 'text', text: '{"ready":true}' }] }
      })
      await server.connect(transport)
    }
    await transport.handleRequest(req, res, body)
  })
  await new Promise<void>(resolve => http.listen(port, '127.0.0.1', resolve))
  return {
    port,
    calls,
    slowBy: (ms) => { slowMs = ms },
    sessions: () => transports.size,
    close: async () => {
      for (const t of transports.values())
        await t.close().catch(() => {})
      http.closeAllConnections()
      await new Promise<void>(resolve => http.close(() => resolve()))
    },
  }
}

describe('the face reaching the JARVIS tool server (real MCP client)', () => {
  let userData: string
  let port: number
  const servers: FakeToolServer[] = []
  const managers: Array<ReturnType<typeof createMcpManager>> = []

  beforeEach(async () => {
    userData = await mkdtemp(join(tmpdir(), 'jarvis-face-mcp-'))
    appMock.getPath.mockReturnValue(userData)
    appMock.getVersion.mockReturnValue('0.12.0')
    port = await freePort()
  })

  afterEach(async () => {
    for (const m of managers.splice(0))
      await m.stopAll()
    for (const s of servers.splice(0))
      await s.close()
    await rm(userData, { recursive: true, force: true })
  })

  const writeConfig = (servers: Record<string, unknown>) => writeFile(join(userData, 'mcp.json'), JSON.stringify({ mcpServers: servers }))
  const jarvisEntry = () => ({ jarvis: { url: `http://127.0.0.1:${port}/mcp` } })
  const manager = () => {
    const m = createMcpManager()
    managers.push(m)
    return m
  }
  const up = async () => {
    const s = await startFakeToolServer(port)
    servers.push(s)
    return s
  }

  it('lists the tools and reaches the server (Host 127.0.0.1:PORT, no Origin, JSON or SSE, protocol negotiated)', async () => {
    const server = await up()
    await writeConfig(jarvisEntry())
    const m = manager()
    expect((await m.applyAndRestart()).failed).toEqual([])

    expect((await m.listTools()).map(t => t.name)).toEqual(['jarvis::jarvis_ready'])
    expect(await m.callTool({ name: 'jarvis::jarvis_ready' })).toMatchObject({ content: [{ type: 'text' }] })
    expect(server.calls).toEqual(['jarvis_ready'])
  })

  it('the global fetch lock lets the MCP transport reach 127.0.0.1 (and nothing else)', async () => {
    const server = await up()
    const original = globalThis.fetch
    try {
      lockGlobalFetch()
      await writeConfig(jarvisEntry())
      const m = manager()
      await m.applyAndRestart()
      expect((await m.listTools()).length).toBe(1)
      expect(server.sessions()).toBe(1)
      await expect(fetch('https://example.com/')).rejects.toThrow('not on the allowlist')
    }
    finally {
      globalThis.fetch = original
    }
  })

  // ROOT CAUSE (1): the tool server was not listening when the face started (it loads its plugins first). The connection failed once
  // and was never tried again, and discovery said "no tools" instead of failing.
  it('connects later when the tool server was not up at start-up', async () => {
    await writeConfig(jarvisEntry())
    const m = manager()
    const applied = await m.applyAndRestart()
    expect(applied.failed.map(f => f.name)).toEqual(['jarvis'])

    // while it is down, discovery fails loudly with the server's name; it never answers an empty list
    await expect(m.listTools()).rejects.toThrow('MCP server "jarvis"')

    const server = await up()
    await vi.waitFor(async () => expect((await m.listTools()).map(t => t.name)).toEqual(['jarvis::jarvis_ready']), { timeout: 8000, interval: 500 })
    await m.callTool({ name: 'jarvis::jarvis_ready' })
    expect(server.calls).toEqual(['jarvis_ready'])
    expect(m.getRuntimeStatus().servers).toMatchObject([{ name: 'jarvis', state: 'running' }])
  })

  // ROOT CAUSE (2): the tool server was restarted (the supervisor does that on a failure); the face kept the old session id for good.
  it('recovers when the tool server restarts and forgets the session', async () => {
    const first = await up()
    await writeConfig(jarvisEntry())
    const m = manager()
    await m.applyAndRestart()
    expect((await m.listTools()).length).toBe(1)

    await first.close()
    servers.splice(servers.indexOf(first), 1)
    const second = await up()

    // the next request carries the dead session id; the face starts a new session. A read-only request is simply sent again.
    expect((await m.listTools()).length).toBe(1)
    await m.callTool({ name: 'jarvis::jarvis_ready' })
    expect(second.calls).toEqual(['jarvis_ready'])
  })

  it('a tool call straight after a restart reaches the server at most once and the next call works', async () => {
    const first = await up()
    await writeConfig(jarvisEntry())
    const m = manager()
    await m.applyAndRestart()
    await m.callTool({ name: 'jarvis::jarvis_ready' })

    await first.close()
    servers.splice(servers.indexOf(first), 1)
    const second = await up()

    // the first call may fail on the broken connection (it is not repeated, because it might have run), but never runs twice
    await m.callTool({ name: 'jarvis::jarvis_ready' }).catch(() => {})
    await vi.waitFor(async () => {
      await m.callTool({ name: 'jarvis::jarvis_ready' })
    }, { timeout: 8000, interval: 300 })
    expect(second.calls.length).toBeLessThanOrEqual(2)
    expect(second.calls.length).toBeGreaterThanOrEqual(1)
  })

  // ROOT CAUSE (3): a profile whose mcp.json was written by an earlier face (AIRI's own default is an empty server list)
  // never got the JARVIS entry, because the file is only written when it is missing.
  it('adds the JARVIS tool server to an mcp.json that does not have it, and keeps the owner\'s other servers', async () => {
    await writeConfig({ notes: { url: 'http://127.0.0.1:9/mcp', enabled: false } })
    const m = manager()
    await m.ensureConfigFile()

    const saved = JSON.parse(await readFile(join(userData, 'mcp.json'), 'utf-8'))
    expect(saved.mcpServers.jarvis).toEqual({ url: 'http://127.0.0.1:48970/mcp' })
    expect(saved.mcpServers.notes).toEqual({ url: 'http://127.0.0.1:9/mcp', enabled: false })

    await writeFile(join(userData, 'mcp.json'), '{"mcpServers":{}}')
    await m.ensureConfigFile()
    expect(JSON.parse(await readFile(join(userData, 'mcp.json'), 'utf-8')).mcpServers.jarvis).toBeDefined()
  })

  it('leaves an owner-disabled JARVIS entry disabled', async () => {
    await writeConfig({ jarvis: { url: `http://127.0.0.1:${port}/mcp`, enabled: false } })
    const m = manager()
    const result = await m.applyAndRestart()
    expect(result.skipped).toEqual([{ name: 'jarvis', reason: 'disabled' }])
    expect(JSON.parse(await readFile(join(userData, 'mcp.json'), 'utf-8')).mcpServers.jarvis.enabled).toBe(false)
  })

  // D57: the avatar window is created after the MCP manager exists. A tool server that accepts the connection and never answers
  // (it is loading its plugins) held the start-up for the whole connect time, and the window with it.
  it('start-up does not wait for a tool server that accepts the connection and never answers', async () => {
    const sockets = new Set<import('node:net').Socket>()
    const silent = createTcpServer(socket => void sockets.add(socket))
    await new Promise<void>(resolve => silent.listen(port, '127.0.0.1', resolve))
    try {
      await writeConfig(jarvisEntry())
      const started = Date.now()
      const m = await setupMcpManager()
      managers.push(m)
      // the connect limit is 8 s; the start-up returns at once and the connection is tried in the background
      expect(Date.now() - started).toBeLessThan(1500)
    }
    finally {
      for (const socket of sockets)
        socket.destroy()
      await new Promise<void>(resolve => silent.close(() => resolve()))
    }
  })

  it('a tool call may ask for a shorter limit than the long one (background polls do), and the long call still works', async () => {
    const server = await up()
    await writeConfig(jarvisEntry())
    const m = manager()
    await m.applyAndRestart()
    server.slowBy(2500)

    const started = Date.now()
    await expect(m.callTool({ name: 'jarvis::jarvis_ready', timeoutMs: 1000 })).rejects.toThrow()
    expect(Date.now() - started).toBeLessThan(2300)

    // without a limit of its own a call gets the long one and waits for the slow answer
    await expect(m.callTool({ name: 'jarvis::jarvis_ready' })).resolves.toMatchObject({ content: [{ type: 'text' }] })
  })
})

describe('the limits of the tool connection', () => {
  it('keeps a requested call limit between 1 s and the long limit', () => {
    expect(resolveCallTimeoutMsec(undefined)).toBe(180_000)
    expect(resolveCallTimeoutMsec('5000')).toBe(180_000)
    expect(resolveCallTimeoutMsec(Number.NaN)).toBe(180_000)
    expect(resolveCallTimeoutMsec(8_000)).toBe(8_000)
    expect(resolveCallTimeoutMsec(5)).toBe(1_000)
    expect(resolveCallTimeoutMsec(10 * 60_000)).toBe(180_000)
  })

  it('waits 1 s after the first failed connection and longer after more, up to 10 s', () => {
    expect([1, 2, 3, 4, 5, 6, 20].map(reconnectCooldownMsec)).toEqual([1000, 2000, 4000, 8000, 10_000, 10_000, 10_000])
  })
})
