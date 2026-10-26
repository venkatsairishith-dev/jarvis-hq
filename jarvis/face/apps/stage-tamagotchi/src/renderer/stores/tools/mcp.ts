import type { ExecutableTool } from '@proj-airi/stage-ui/stores/ai/chat-llm/tools'
import type { Tool } from '@xsai/shared-chat'

import { useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { setToolsWithoutCallSummary } from '@proj-airi/stage-ui/stores/ai/chat-llm/tool-call-summary'
import { useLlmToolsStore } from '@proj-airi/stage-ui/stores/ai/chat-llm/tools'
import { setTimingSink } from '@proj-airi/stage-ui/stores/chat/latency-trace'
import { QUICK_TOOL_NAME } from '@proj-airi/stage-ui/stores/chat/quick-reply'
import { useJarvisSpeedStore } from '@proj-airi/stage-ui/stores/jarvis-speed'
import { TOOL_ICONS } from '@proj-airi/stage-ui/tools/icons'
import { compactMcpToolDescriptor, createDirectMcpTools, createMcpTools, DIRECT_TOOL_SERVER } from '@proj-airi/stage-ui/tools/mcp'
import { defineStore } from 'pinia'
import { watch } from 'vue'

import { electronMcpCallTool, electronMcpListTools, jarvisSpeedEnv, jarvisTimingMarks } from '../../../shared/eventa'

/** Waits for the tool server again after a failed list: 5 s, then 10, 20, 30 and every 30 s while direct mode is on and the list is not there. */
export const DIRECT_RETRY_MS = [5_000, 10_000, 20_000, 30_000]

/** The environment's answer is a nicety; a main process that does not answer must not hold the tools back. */
const ENV_WAIT_MS = 1_500

/** The tool server's rules-first router, as the face calls it before it asks a chat model (jarvis/plugins/desktop/quick.py, D60). */
export const QUICK_COMMAND_MCP_NAME = 'jarvis::desktop__quick_command'
const QUICK_TOOL_ID = 'jarvis:quick-command'

export const useTamagotchiMcpToolsStore = defineStore('tamagotchi-mcp-tools', () => {
  const llmToolsStore = useLlmToolsStore()
  const speed = useJarvisSpeedStore()
  const listMcpTools = useElectronEventaInvoke(electronMcpListTools)
  const callMcpTool = useElectronEventaInvoke(electronMcpCallTool)
  const readSpeedEnv = useElectronEventaInvoke(jarvisSpeedEnv)
  const sendTimingMarks = useElectronEventaInvoke(jarvisTimingMarks)
  const toolIdPrefix = 'mcp:'

  let active = false
  let refreshing = false
  let again = false
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  let retries = 0

  function registeredToolIds() {
    return llmToolsStore.tools
      .filter(tool => tool.id.startsWith(toolIdPrefix))
      .map(tool => tool.id)
  }

  function stopRetry() {
    if (retryTimer)
      clearTimeout(retryTimer)
    retryTimer = undefined
  }

  function scheduleRetry() {
    stopRetry()
    if (!active || !speed.directTools)
      return
    const wait = DIRECT_RETRY_MS[Math.min(retries, DIRECT_RETRY_MS.length - 1)]
    retries += 1
    retryTimer = setTimeout(() => void refresh().catch(error => console.warn('[mcp] direct tool retry failed:', error)), wait)
  }

  async function loadEnv() {
    try {
      const env = await Promise.race([
        readSpeedEnv(),
        new Promise<undefined>(resolve => setTimeout(resolve, ENV_WAIT_MS, undefined)),
      ])
      speed.setEnv(env)
    }
    catch {
      // the defaults stay
    }
  }

  async function refresh() {
    active = true
    if (refreshing) {
      again = true
      return
    }
    refreshing = true
    try {
      await refreshOnce()
    }
    finally {
      refreshing = false
    }
    if (again) {
      again = false
      await refresh()
    }
  }

  async function refreshOnce() {
    stopRetry()
    // D60: this window runs the chat turns (the leader), so its marks go to the main process, which appends them to the timing log
    setTimingSink((marks) => {
      void Promise.resolve(sendTimingMarks(marks)).catch(() => {})
    })
    await loadEnv()

    const proxies = await Promise.all(createMcpTools({
      // JARVIS: 105 tools at full size is 60 KB; the model gets names, short descriptions and argument names (see compactMcpToolDescriptor)
      listTools: async () => (await listMcpTools()).map(tool => compactMcpToolDescriptor(tool)),
      callTool: payload => callMcpTool(payload),
    }))

    // JARVIS D60: direct mode. The first request carries the real tools of the JARVIS tool server, so the model calls them in round one (no
    // builtIn_mcpListTools round trip). If the tool server cannot be listed yet, the proxies stay as the fallback and the list is tried again.
    let direct: Tool[] = []
    let othersListed = false
    let listFailed = false
    if (speed.directTools) {
      try {
        const descriptors = await listMcpTools()
        direct = createDirectMcpTools(descriptors, { callTool: payload => callMcpTool(payload) }, DIRECT_TOOL_SERVER)
        othersListed = descriptors.some(tool => tool.serverName !== DIRECT_TOOL_SERVER)
      }
      catch (error) {
        listFailed = true
        console.warn('[mcp] the tool list is not there yet; the proxy tools stay until it is:', error)
      }
      if (listFailed)
        scheduleRetry()
      else
        retries = 0
    }

    // The proxies stay when there is no direct list, and when another MCP server has tools that only the proxies reach.
    const registered: Tool[] = direct.length > 0 && !othersListed ? direct : [...proxies, ...direct]
    llmToolsStore.removeToolsByIds(...registeredToolIds())
    llmToolsStore.addTools(...registered.map(tool => ({
      ...tool,
      icon: TOOL_ICONS.mcp,
      id: `${toolIdPrefix}${tool.function.name}`,
    } satisfies ExecutableTool)))
    setToolsWithoutCallSummary(direct.map(tool => tool.function.name))

    // Hidden: no request offers it (it requires explicit selection and nobody selects it), but the chat store finds it by name and calls it first.
    llmToolsStore.addTools({
      id: QUICK_TOOL_ID,
      type: 'function',
      function: { name: QUICK_TOOL_NAME, description: 'Runs one plain PC command by rules, with no model.', parameters: { type: 'object', properties: { text: { type: 'string' } }, required: ['text'] } },
      requiresExplicitSelection: true,
      defaultActive: false,
      execute: input => callMcpTool({ name: QUICK_COMMAND_MCP_NAME, arguments: { text: String((input as { text?: unknown } | null)?.text ?? '') } }),
    })
  }

  function dispose() {
    active = false
    stopRetry()
    llmToolsStore.removeToolsByIds(...registeredToolIds(), QUICK_TOOL_ID)
    setToolsWithoutCallSummary([])
    setTimingSink(undefined)
  }

  // The owner turns direct mode on or off in Settings: the tool list follows without a restart (only after the first refresh, so only in the leader).
  watch(() => speed.directToolsSetting, () => {
    if (active)
      void refresh().catch(error => console.warn('[mcp] refresh after a setting change failed:', error))
  })

  return {
    dispose,
    refresh,
  }
}, {
  synced: {
    actions: ['dispose', 'refresh'],
    state: false,
  },
})
