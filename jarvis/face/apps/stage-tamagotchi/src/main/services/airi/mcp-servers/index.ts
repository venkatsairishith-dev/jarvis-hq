import type { createContext } from '@moeru/eventa/adapters/electron/main'

import type {
  ElectronMcpApplyResult,
  ElectronMcpCallToolPayload,
  ElectronMcpCallToolResult,
  ElectronMcpConfigFile,
  ElectronMcpConfigText,
  ElectronMcpRuntimeStatus,
  ElectronMcpServerConfig,
  ElectronMcpServerRuntimeStatus,
  ElectronMcpTestPayload,
  ElectronMcpTestResult,
  ElectronMcpToolDescriptor,
} from '../../../../shared/eventa'

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { join } from 'node:path'

import { useLogg } from '@guiiai/logg'
import { Client } from '@modelcontextprotocol/sdk/client/index.js'
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js'
import { StreamableHTTPClientTransport } from '@modelcontextprotocol/sdk/client/streamableHttp.js'
import { defineInvokeHandler } from '@moeru/eventa'
import { errorMessageFrom } from '@moeru/std'
import { JARVIS_ENDPOINTS, JARVIS_TOOL_SERVER_NAME } from '@proj-airi/stage-shared/jarvis'
import { app, shell } from 'electron'

import {
  electronMcpApplyAndRestart,
  electronMcpCallTool,
  electronMcpGetRuntimeStatus,
  electronMcpListTools,
  electronMcpOpenConfigFile,
  electronMcpReadConfigText,
  electronMcpTestServer,
  electronMcpWriteConfigText,
} from '../../../../shared/eventa'
import { isHttpServerConfig, parseElectronMcpConfigText } from '../../../../shared/mcp-config'
import { isAllowedUrl } from '../../../jarvis/network-lock'
import { onAppBeforeQuit } from '../../../libs/bootkit/lifecycle'

/** A transport that the MCP client can drive. Both kinds expose `close`. */
type McpTransport = StdioClientTransport | StreamableHTTPClientTransport

interface McpServerSession {
  client: Client
  transport: McpTransport
  config: ElectronMcpServerConfig
}

export interface McpManager {
  ensureConfigFile: () => Promise<{ path: string }>
  openConfigFile: () => Promise<{ path: string }>
  applyAndRestart: () => Promise<ElectronMcpApplyResult>
  /** Returns the complete active-server catalog. Any discovery failure rejects with server names and reasons. */
  listTools: () => Promise<ElectronMcpToolDescriptor[]>
  callTool: (payload: ElectronMcpCallToolPayload) => Promise<ElectronMcpCallToolResult>
  stopAll: () => Promise<void>
  getRuntimeStatus: () => ElectronMcpRuntimeStatus
  readConfigText: () => Promise<ElectronMcpConfigText>
  writeConfigText: (text: string) => Promise<ElectronMcpConfigText>
  testServer: (payload: ElectronMcpTestPayload) => Promise<ElectronMcpTestResult>
  /** Connects every enabled server that has no live connection (the tool server may start after the face). Never throws. */
  reconnectMissing: () => Promise<void>
}

// JARVIS: the face starts with one tool server, JARVIS's own. The kernel decides every action that server is asked for.
const defaultMcpConfig: ElectronMcpConfigFile = {
  mcpServers: {
    [JARVIS_TOOL_SERVER_NAME]: { url: JARVIS_ENDPOINTS.tools },
  },
}
const toolNameSeparator = '::'
const mcpRequestTimeoutMsec = 10_000
const mcpRequestMaxTotalTimeoutMsec = 15_000
const mcpTestStderrMaxChars = 16_000
/** A server that does not answer `initialize` in this time counts as down (the SDK default is 60 s). */
const mcpConnectTimeoutMsec = 8_000
/** A failed connection is not tried again sooner than this, so a burst of calls does not hammer a server that is down. */
const mcpReconnectCooldownMsec = 1_000
/** While a configured server is not connected, the main process tries again at this interval. */
const mcpReconnectIntervalMsec = 5_000

function stringifyError(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }

  return String(error)
}

/** The server answered, but it does not know our session any more: it was restarted. Nothing ran, so a retry is safe. */
function isDeadSessionError(error: unknown) {
  const code = (error as { code?: unknown } | null)?.code
  return code === 404 || /session not found/i.test(stringifyError(error))
}

/** Nothing was sent: the port is closed. */
function isRefusedError(error: unknown) {
  const cause = (error as { cause?: { code?: unknown } } | null)?.cause
  return cause?.code === 'ECONNREFUSED' || /ECONNREFUSED/.test(stringifyError(error))
}

/** An idle connection that the other side had already closed (the server restarted): the request did not get through. */
function isStaleSocketError(error: unknown) {
  const cause = (error as { cause?: { code?: unknown } } | null)?.cause
  return cause?.code === 'UND_ERR_SOCKET' || cause?.code === 'ECONNRESET' || /other side closed|socket hang up/i.test(stringifyError(error))
}

/** The connection broke or never opened. The request may or may not have reached the server. */
function isConnectionError(error: unknown) {
  const cause = (error as { cause?: { code?: unknown } } | null)?.cause
  const text = `${stringifyError(error)} ${typeof cause?.code === 'string' ? cause.code : ''}`
  return /fetch failed|terminated|ECONNREFUSED|ECONNRESET|UND_ERR|socket|connection closed|SSE stream disconnected|aborted/i.test(text)
}

function getConfigPath() {
  return join(app.getPath('userData'), 'mcp.json')
}

function parseQualifiedToolName(name: string) {
  const separatorIndex = name.indexOf(toolNameSeparator)
  if (separatorIndex <= 0 || separatorIndex === name.length - toolNameSeparator.length) {
    throw new Error(`invalid qualified tool name: ${name}`)
  }

  return {
    serverName: name.slice(0, separatorIndex),
    toolName: name.slice(separatorIndex + toolNameSeparator.length),
  }
}

function resolveFallbackToolName(toolName: string): string | undefined {
  const normalizedTransportPrefix = toolName
    .replace(/^\.(?:stdio|stdo)::/, '')
    .replace(/^(?:stdio|stdo)::/, '')
  if (normalizedTransportPrefix !== toolName) {
    return normalizedTransportPrefix
  }

  const lastSeparatorIndex = toolName.lastIndexOf(toolNameSeparator)
  if (lastSeparatorIndex <= 0 || lastSeparatorIndex === toolName.length - toolNameSeparator.length) {
    return undefined
  }

  return toolName.slice(lastSeparatorIndex + toolNameSeparator.length)
}

async function closeSession(session: McpServerSession) {
  try {
    await session.client.close()
  }
  catch {
    await session.transport.close()
  }
}

/**
 * Creates the transport for one server configuration.
 *
 * Use when:
 * - Starting a server from `mcp.json` or from a connection test
 *
 * Expects:
 * - `config` already passed the MCP config schema
 *
 * Returns:
 * - A stdio transport that spawns `config.command`, or a streamable HTTP
 *   transport that reaches `config.url` and sends `config.headers` on every request
 */
function createTransport(config: ElectronMcpServerConfig): McpTransport {
  // JARVIS: the face never starts a program. A stdio server would run any command written into mcp.json, which is
  // exactly what a chat or card exploit would aim for. Tools come over HTTP from this PC or the tailnet only.
  if (!isHttpServerConfig(config))
    throw new Error('JARVIS: the face does not start programs. Connect to the JARVIS tool server over HTTP instead.')
  if (!isAllowedUrl(config.url))
    throw new Error('JARVIS: an MCP server must be on this PC or the tailnet.')

  return new StreamableHTTPClientTransport(new URL(config.url), {
    requestInit: { headers: config.headers },
  })
}

/**
 * Builds the runtime status row for one server.
 *
 * Use when:
 * - Recording that a server started, stopped, or failed
 *
 * Expects:
 * - `config` already passed the MCP config schema
 * - `options.pid` is known for stdio servers only
 *
 * Returns:
 * - A status that reports a process for stdio servers and an endpoint for HTTP servers
 */
function describeRuntime(
  name: string,
  config: ElectronMcpServerConfig,
  state: ElectronMcpServerRuntimeStatus['state'],
  options: { pid?: number | null, lastError?: string } = {},
): ElectronMcpServerRuntimeStatus {
  const lastError = options.lastError === undefined ? {} : { lastError: options.lastError }

  if (isHttpServerConfig(config)) {
    return { name, state, transport: 'http', url: config.url, ...lastError }
  }

  return {
    name,
    state,
    transport: 'stdio',
    command: config.command,
    args: config.args ?? [],
    pid: options.pid ?? null,
    ...lastError,
  }
}

/**
 * Owns MCP sessions and runtime status in the main process.
 * `applyAndRestart` replaces the sessions from disk. `stopAll` closes them.
 * Discovery returns a complete catalog or rejects with all server failures.
 */
export function createMcpManager(): McpManager {
  const log = useLogg('main/mcp').useGlobalConfig()
  const sessions = new Map<string, McpServerSession>()
  /** The enabled servers of the last applied config: the ones that must be connected. */
  const wanted = new Map<string, ElectronMcpServerConfig>()
  const connecting = new Map<string, Promise<void>>()
  const lastAttempt = new Map<string, { at: number, error: string }>()
  let applying: Promise<unknown> | null = null
  const runtimeStatuses = new Map<string, ElectronMcpServerRuntimeStatus>()
  let updatedAt = Date.now()

  const setRuntimeStatus = (status: ElectronMcpServerRuntimeStatus) => {
    runtimeStatuses.set(status.name, status)
    updatedAt = Date.now()
  }

  const ensureConfigFile = async () => {
    const path = getConfigPath()
    await mkdir(app.getPath('userData'), { recursive: true })

    let raw: string | undefined
    try {
      raw = await readFile(path, 'utf-8')
    }
    catch {
      await writeFile(path, `${JSON.stringify(defaultMcpConfig, null, 2)}\n`)
      return { path }
    }

    // JARVIS: an mcp.json from an earlier face (AIRI's default is an empty list) lacks the tool server and is never rewritten.
    // Add the entry when missing; other servers and `enabled: false` stay. A file that does not parse is left to the owner.
    try {
      const config = parseElectronMcpConfigText(raw)
      if (!(JARVIS_TOOL_SERVER_NAME in config.mcpServers)) {
        const merged: ElectronMcpConfigFile = { mcpServers: { ...defaultMcpConfig.mcpServers, ...config.mcpServers } }
        await writeFile(path, `${JSON.stringify(merged, null, 2)}\n`)
        log.withFields({ path }).warn('added the JARVIS tool server to mcp.json (it was missing)')
      }
    }
    catch {
      // unreadable config: applyAndRestart reports it
    }

    return { path }
  }

  const openConfigFile = async () => {
    const { path } = await ensureConfigFile()
    shell.showItemInFolder(path)
    return { path }
  }

  const readConfigFile = async (path: string): Promise<ElectronMcpConfigFile> => {
    const raw = await readFile(path, 'utf-8')
    return parseElectronMcpConfigText(raw)
  }

  const stopAll = async () => {
    const entries = [...sessions.entries()]
    wanted.clear()
    for (const [name, session] of entries) {
      sessions.delete(name) // before the close, so the close handler does not report a lost connection
      await closeSession(session)
      setRuntimeStatus(describeRuntime(name, session.config, 'stopped'))
    }
  }

  const dropSession = async (name: string, session: McpServerSession) => {
    if (sessions.get(name) !== session)
      return
    sessions.delete(name)
    await session.transport.close().catch(() => {})
    setRuntimeStatus(describeRuntime(name, session.config, 'error', { lastError: 'the connection was lost' }))
  }

  const startServer = async (name: string, config: ElectronMcpServerConfig) => {
    const transport = createTransport(config)
    const client = new Client({
      name: `proj-airi:stage-tamagotchi:mcp:${name}`,
      version: app.getVersion(),
    })
    let timer: NodeJS.Timeout | undefined

    try {
      // A server that accepts the connection but never answers must not hold the start-up (or a chat turn) for the SDK's 60 s.
      await Promise.race([
        client.connect(transport),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error(`MCP server "${name}" did not answer in ${mcpConnectTimeoutMsec / 1000} s`)), mcpConnectTimeoutMsec)
        }),
      ])
      if (transport instanceof StdioClientTransport) {
        transport.stderr?.on('data', (data) => {
          const text = data.toString('utf-8').trim()
          if (text) {
            log.withFields({ serverName: name }).warn(text)
          }
        })
      }
      const session: McpServerSession = { client, transport, config }
      client.onclose = () => void dropSession(name, session)
      // The SDK gives up on its server-to-client stream after two tries (the server went away): the session is dead, start a new one on the next request.
      client.onerror = (error) => {
        if (/maximum reconnection attempts/i.test(stringifyError(error)))
          void dropSession(name, session)
      }
      sessions.set(name, session)
      lastAttempt.delete(name)
      setRuntimeStatus(describeRuntime(name, config, 'running', {
        pid: transport instanceof StdioClientTransport ? transport.pid : null,
      }))
    }
    catch (error) {
      await transport.close().catch(() => {})
      throw error
    }
    finally {
      if (timer)
        clearTimeout(timer)
    }
  }

  /** Connects one wanted server unless it is connected. Concurrent callers share one attempt; a recent failure is reported again without a new attempt. */
  const ensureConnected = async (name: string): Promise<void> => {
    if (sessions.has(name))
      return
    const config = wanted.get(name)
    if (!config)
      return
    const pending = connecting.get(name)
    if (pending)
      return pending
    const recent = lastAttempt.get(name)
    if (recent && Date.now() - recent.at < mcpReconnectCooldownMsec)
      throw new Error(recent.error)

    const attempt = (async () => {
      try {
        try {
          await startServer(name, config)
        }
        catch (error) {
          // Opening a session repeats safely. The first try can land on an idle connection that the restarted server had closed.
          if (!isStaleSocketError(error))
            throw error
          await startServer(name, config)
        }
      }
      catch (error) {
        const message = stringifyError(error)
        lastAttempt.set(name, { at: Date.now(), error: message })
        setRuntimeStatus(describeRuntime(name, config, 'error', { lastError: message }))
        throw error
      }
    })().finally(() => connecting.delete(name))
    connecting.set(name, attempt)
    return attempt
  }

  const applyAndRestart = async (): Promise<ElectronMcpApplyResult> => {
    const run = async (): Promise<ElectronMcpApplyResult> => {
      const { path } = await ensureConfigFile()
      const config = await readConfigFile(path)

      await stopAll()
      runtimeStatuses.clear()
      wanted.clear()
      lastAttempt.clear()

      const result: ElectronMcpApplyResult = {
        path,
        started: [],
        failed: [],
        skipped: [],
      }

      for (const [name, server] of Object.entries(config.mcpServers)) {
        if (server.enabled === false) {
          result.skipped.push({ name, reason: 'disabled' })
          setRuntimeStatus(describeRuntime(name, server, 'stopped'))
          continue
        }

        wanted.set(name, server)
        try {
          await ensureConnected(name)
          result.started.push({ name })
        }
        catch (error) {
          result.failed.push({ name, error: stringifyError(error) })
        }
      }

      updatedAt = Date.now()

      return result
    }

    // Later calls wait for a start-up that is still running, so a chat turn never sees a half-started set of servers.
    const previous = applying
    const current = (async () => {
      await previous?.catch(() => {})
      return run()
    })()
    applying = current
    try {
      return await current
    }
    finally {
      if (applying === current)
        applying = null
    }
  }

  /** Waits for a running start-up, then tries every wanted server that is not connected. Returns the failures by server name. */
  const connectWanted = async (): Promise<Map<string, string>> => {
    await applying?.catch(() => {})
    const failures = new Map<string, string>()
    await Promise.all([...wanted.keys()].map(async (name) => {
      try {
        await ensureConnected(name)
      }
      catch (error) {
        failures.set(name, stringifyError(error))
      }
    }))
    return failures
  }

  const reconnectMissing = async () => {
    await connectWanted()
  }

  /**
   * One request to one server. A server that forgot our session (it restarted) gets a new session and the request once more.
   * A read-only request (`repeatable`) is also sent again when the connection it used was already closed by the other side; a tool call is not (it may have run).
   */
  const withSession = async <V>(name: string, send: (session: McpServerSession) => Promise<V>, repeatable = false): Promise<V> => {
    let session = sessions.get(name)
    if (!session) {
      await ensureConnected(name)
      session = sessions.get(name)
      if (!session)
        throw new Error(`mcp server is not running: ${name}`)
    }
    try {
      return await send(session)
    }
    catch (error) {
      if (isDeadSessionError(error)) {
        // The server answered 404 before it ran anything, so asking again on a new session cannot run it twice.
        await dropSession(name, session)
        lastAttempt.delete(name)
        await ensureConnected(name)
        const fresh = sessions.get(name)
        if (!fresh)
          throw error
        return send(fresh)
      }
      if (isConnectionError(error)) {
        await dropSession(name, session) // the next request opens a new connection
        if (isRefusedError(error) || (repeatable && isStaleSocketError(error))) {
          lastAttempt.delete(name)
          await ensureConnected(name)
          const fresh = sessions.get(name)
          if (fresh)
            return send(fresh)
        }
      }
      throw error
    }
  }

  const listTools = async (): Promise<ElectronMcpToolDescriptor[]> => {
    const connectFailures = await connectWanted()
    const names = [...new Set([...sessions.keys(), ...connectFailures.keys()])].sort((left, right) => left.localeCompare(right))
    const listResult = await Promise.allSettled(names.map(async (serverName) => {
      const failure = connectFailures.get(serverName)
      if (failure !== undefined && !sessions.has(serverName))
        throw new Error(`not connected: ${failure}`)
      const response = await withSession(serverName, session => session.client.listTools(undefined, {
        timeout: mcpRequestTimeoutMsec,
        maxTotalTimeout: mcpRequestMaxTotalTimeoutMsec,
      }), true)
      return response.tools.map<ElectronMcpToolDescriptor>(item => ({
        serverName,
        name: `${serverName}${toolNameSeparator}${item.name}`,
        toolName: item.name,
        description: item.description,
        inputSchema: item.inputSchema,
      }))
    }))
    const tools: ElectronMcpToolDescriptor[] = []
    const failures: Error[] = []

    // Each result matches the sorted server list. A partial catalog must
    // not report successful discovery while a server's tools remain unknown.
    for (const [index, result] of listResult.entries()) {
      if (result.status === 'fulfilled') {
        tools.push(...result.value)
        continue
      }

      const serverName = names[index]
      log.withFields({ serverName }).withError(result.reason).warn('failed to list tools from mcp server')
      failures.push(new Error(`MCP server "${serverName}": ${errorMessageFrom(result.reason) ?? 'Unknown error.'}`, { cause: result.reason }))
    }

    if (failures.length > 0)
      throw new AggregateError(failures, failures.map(error => error.message).join('\n'))

    return tools
  }

  const callTool = async (payload: ElectronMcpCallToolPayload): Promise<ElectronMcpCallToolResult> => {
    const { serverName, toolName } = parseQualifiedToolName(payload.name)
    await applying?.catch(() => {})
    if (!wanted.has(serverName) && !sessions.has(serverName)) {
      throw new Error(`mcp server is not running: ${serverName}`)
    }

    const call = (name: string) => withSession(serverName, session => session.client.callTool({
      name,
      arguments: payload.arguments ?? {},
    }, undefined, {
      timeout: mcpRequestTimeoutMsec,
      maxTotalTimeout: mcpRequestMaxTotalTimeoutMsec,
    }))

    let result
    try {
      result = await call(toolName)
    }
    catch (error) {
      const fallbackToolName = resolveFallbackToolName(toolName)
      // A broken connection is not a naming problem, and a second try could run the tool twice.
      if (!fallbackToolName || fallbackToolName === toolName || isConnectionError(error)) {
        throw error
      }

      log.withFields({
        serverName,
        requestedToolName: toolName,
        fallbackToolName,
      }).warn('retrying mcp tool call with normalized tool name')

      result = await call(fallbackToolName)
    }

    const normalized: ElectronMcpCallToolResult = {}
    if ('content' in result && Array.isArray(result.content)) {
      normalized.content = result.content as Array<Record<string, unknown>>
    }
    if ('structuredContent' in result && result.structuredContent && typeof result.structuredContent === 'object' && !Array.isArray(result.structuredContent)) {
      normalized.structuredContent = result.structuredContent as Record<string, unknown>
    }
    if ('isError' in result && typeof result.isError === 'boolean') {
      normalized.isError = result.isError
    }
    if ('toolResult' in result) {
      normalized.toolResult = result.toolResult
    }

    return normalized
  }

  const getRuntimeStatus = (): ElectronMcpRuntimeStatus => {
    return {
      path: getConfigPath(),
      servers: [...runtimeStatuses.values()].sort((left, right) => left.name.localeCompare(right.name)),
      updatedAt,
    }
  }

  const readConfigText = async (): Promise<ElectronMcpConfigText> => {
    const { path } = await ensureConfigFile()
    const text = await readFile(path, 'utf-8')
    return { path, text }
  }

  const writeConfigText = async (text: string): Promise<ElectronMcpConfigText> => {
    const { path } = await ensureConfigFile()
    const validated = parseElectronMcpConfigText(text)
    const normalized = `${JSON.stringify(validated, null, 2)}\n`
    await writeFile(path, normalized)
    return { path, text: normalized }
  }

  const testServer = async (payload: ElectronMcpTestPayload): Promise<ElectronMcpTestResult> => {
    const startedAt = Date.now()
    let transport: McpTransport | null = null
    let client: Client | null = null
    const stderrChunks: string[] = []

    const withDeadline = <V>(promise: Promise<V>, ms: number, label: string): Promise<V> => {
      let timer: NodeJS.Timeout | undefined
      const timeout = new Promise<never>((_, reject) => {
        timer = setTimeout(() => reject(new Error(`${label} timed out after ${ms}ms`)), ms)
      })
      return Promise.race([promise, timeout]).finally(() => {
        if (timer)
          clearTimeout(timer)
      })
    }

    try {
      transport = createTransport(payload.config)
      client = new Client({
        name: `proj-airi:stage-tamagotchi:mcp:test:${payload.name}`,
        version: app.getVersion(),
      })

      if (transport instanceof StdioClientTransport) {
        transport.stderr?.on('data', (data) => {
          const text = data.toString('utf-8')
          if (text)
            stderrChunks.push(text)
        })
      }

      await withDeadline(client.connect(transport), mcpRequestMaxTotalTimeoutMsec, 'connect')

      const response = await client.listTools(undefined, {
        timeout: mcpRequestTimeoutMsec,
        maxTotalTimeout: mcpRequestMaxTotalTimeoutMsec,
      })

      if (stderrChunks.length > 0) {
        log.withFields({ serverName: payload.name }).debug(stderrChunks.join('').trim())
      }

      return {
        ok: true,
        tools: response.tools.map(tool => tool.name),
        durationMs: Date.now() - startedAt,
      }
    }
    catch (error) {
      const message = stringifyError(error)
      // Keep only the tail so a noisy failed server cannot flood the settings UI.
      const stderr = stderrChunks.join('').trim().slice(-mcpTestStderrMaxChars)
      return {
        ok: false,
        error: stderr ? `${message}\n\n${stderr}` : message,
        durationMs: Date.now() - startedAt,
      }
    }
    finally {
      if (client) {
        await client.close().catch(() => {})
      }
      if (transport) {
        await transport.close().catch(() => {})
      }
    }
  }

  return {
    ensureConfigFile,
    openConfigFile,
    applyAndRestart,
    listTools,
    callTool,
    stopAll,
    getRuntimeStatus,
    readConfigText,
    writeConfigText,
    testServer,
    reconnectMissing,
  }
}

export async function setupMcpManager() {
  const log = useLogg('main/mcp').useGlobalConfig()
  const manager = createMcpManager()

  // While a configured server (the JARVIS tool server) is not connected, try again every few seconds. The tool server loads its
  // plugins for up to a minute and the supervisor restarts it on a failure; the face must not need a restart to find it.
  const retry = setInterval(() => void manager.reconnectMissing(), mcpReconnectIntervalMsec)
  retry.unref?.()

  onAppBeforeQuit(async () => {
    clearInterval(retry)
    await manager.stopAll()
  })

  await manager.ensureConfigFile()

  try {
    await manager.applyAndRestart()
  }
  catch (error) {
    log.withError(error).warn('failed to apply mcp config during startup')
  }

  return manager
}

export function createMcpServersService(params: { context: ReturnType<typeof createContext>['context'], manager: McpManager }) {
  defineInvokeHandler(params.context, electronMcpOpenConfigFile, async () => {
    return params.manager.openConfigFile()
  })

  defineInvokeHandler(params.context, electronMcpApplyAndRestart, async () => {
    return params.manager.applyAndRestart()
  })

  defineInvokeHandler(params.context, electronMcpGetRuntimeStatus, async () => {
    return params.manager.getRuntimeStatus()
  })

  defineInvokeHandler(params.context, electronMcpListTools, async () => {
    return params.manager.listTools()
  })

  defineInvokeHandler(params.context, electronMcpCallTool, async (payload) => {
    return params.manager.callTool(payload)
  })

  defineInvokeHandler(params.context, electronMcpReadConfigText, async () => {
    return params.manager.readConfigText()
  })

  defineInvokeHandler(params.context, electronMcpWriteConfigText, async (payload) => {
    return params.manager.writeConfigText(payload.text)
  })

  defineInvokeHandler(params.context, electronMcpTestServer, async (payload) => {
    return params.manager.testServer(payload)
  })
}
