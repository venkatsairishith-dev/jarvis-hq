import type { Tool } from '@xsai/shared-chat'

import { useLlmToolsStore } from '@proj-airi/stage-ui/stores/ai/chat-llm/tools'
import { useJarvisSpeedStore } from '@proj-airi/stage-ui/stores/jarvis-speed'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const invokeMocks = vi.hoisted(() => ({
  timingMarks: vi.fn(async () => undefined),
  speedEnv: vi.fn(async () => ({ directTools: true, quickAck: true, quickCommand: true, timing: true })),
  callMcpTool: vi.fn(async () => ({
    content: [{ type: 'text', text: 'ok' }],
    isError: false,
  })),
  listMcpTools: vi.fn(async () => [{
    serverName: 'filesystem',
    name: 'filesystem::search',
    toolName: 'search',
    description: 'Search files.',
    inputSchema: {
      type: 'object',
      properties: {},
    },
  }]),
}))

vi.mock('@proj-airi/electron-vueuse', () => ({
  useElectronEventaInvoke: (event: { receiveEvent?: { id?: string } }) => {
    if (event?.receiveEvent?.id === 'eventa:invoke:electron:mcp:list-tools-receive')
      return invokeMocks.listMcpTools
    if (event?.receiveEvent?.id === 'eventa:invoke:electron:mcp:call-tool-receive')
      return invokeMocks.callMcpTool
    if (event?.receiveEvent?.id === 'eventa:invoke:electron:jarvis:speed-env-receive')
      return invokeMocks.speedEnv
    if (event?.receiveEvent?.id === 'eventa:invoke:electron:jarvis:timing-marks-receive')
      return invokeMocks.timingMarks

    throw new Error(`Unexpected eventa invoke: ${JSON.stringify(event)}`)
  },
}))

describe('useTamagotchiMcpToolsStore', async () => {
  const { useTamagotchiMcpToolsStore } = await import('./mcp')

  beforeEach(() => {
    setActivePinia(createPinia())
    invokeMocks.listMcpTools.mockClear()
    invokeMocks.callMcpTool.mockClear()
    invokeMocks.speedEnv.mockClear()
    globalThis.localStorage?.clear()
  })

  it('loads MCP tools, proxies execution, and clears them from the shared llm-tools store', async () => {
    const llmToolsStore = useLlmToolsStore()
    // JARVIS D60: direct tools are the default; this test is about the proxy path, which "Off" keeps.
    useJarvisSpeedStore().directToolsSetting = 'off'
    const store = useTamagotchiMcpToolsStore()
    const toolOptions = {} as Parameters<Tool['execute']>[1]

    await store.refresh()

    const mcpDefinitions = llmToolsStore.tools.filter(tool => tool.id.startsWith('mcp:'))
    const listTools = llmToolsStore.activeTools.find(tool => tool.function.name === 'builtIn_mcpListTools')
    const callTool = llmToolsStore.activeTools.find(tool => tool.function.name === 'builtIn_mcpCallTool')

    expect(mcpDefinitions).toEqual([
      expect.objectContaining({
        id: 'mcp:builtIn_mcpListTools',
        function: expect.objectContaining({ name: 'builtIn_mcpListTools' }),
      }),
      expect.objectContaining({
        id: 'mcp:builtIn_mcpCallTool',
        function: expect.objectContaining({ name: 'builtIn_mcpCallTool' }),
      }),
    ])
    expect(JSON.stringify(llmToolsStore.$state)).not.toContain('execute')

    const listResult = await listTools?.execute({}, toolOptions)
    const callResult = await callTool?.execute({
      name: 'filesystem::search',
      arguments: JSON.stringify({ query: 'hello', limit: 10 }),
    }, toolOptions)

    expect(invokeMocks.listMcpTools).toHaveBeenCalledTimes(1)
    expect(invokeMocks.callMcpTool).toHaveBeenCalledWith({
      name: 'filesystem::search',
      arguments: { query: 'hello', limit: 10 },
    })
    expect(listResult).toEqual([{
      serverName: 'filesystem',
      name: 'filesystem::search',
      toolName: 'search',
      description: 'Search files.',
      inputSchema: {
        type: 'object',
        properties: {},
      },
    }])
    expect(callResult).toEqual({
      content: [{ type: 'text', text: 'ok' }],
      isError: false,
    })

    store.dispose()

    expect(llmToolsStore.tools.filter(tool => tool.id.startsWith('mcp:'))).toEqual([])
  })

  describe('jARVIS D60: direct tools', () => {
    const jarvisTools = [
      { serverName: 'jarvis', name: 'jarvis::desktop__do_goal', toolName: 'desktop__do_goal', description: 'Do a goal.', inputSchema: { type: 'object', properties: { goal: { type: 'string' } }, required: ['goal'] } },
      { serverName: 'jarvis', name: 'jarvis::desktop__whats_on_screen', toolName: 'desktop__whats_on_screen', description: 'Windows.', inputSchema: { type: 'object', properties: {} } },
    ]
    const names = (store: ReturnType<typeof useLlmToolsStore>) => store.activeTools.map(tool => tool.function.name)

    it('offers the real tools under their own names and drops the list-then-call proxies', async () => {
      invokeMocks.listMcpTools.mockResolvedValue(jarvisTools as never)
      const llmToolsStore = useLlmToolsStore()
      await useTamagotchiMcpToolsStore().refresh()
      expect(names(llmToolsStore)).toEqual(['desktop__do_goal', 'desktop__whats_on_screen'])

      const doGoal = llmToolsStore.activeTools.find(tool => tool.function.name === 'desktop__do_goal')!
      await doGoal.execute({ goal: 'open notepad' }, {} as Parameters<Tool['execute']>[1])
      expect(invokeMocks.callMcpTool).toHaveBeenCalledWith({ name: 'jarvis::desktop__do_goal', arguments: { goal: 'open notepad' } })
    })

    it('keeps the proxies beside the direct tools when another MCP server has tools', async () => {
      invokeMocks.listMcpTools.mockResolvedValue([...jarvisTools, { serverName: 'filesystem', name: 'filesystem::search', toolName: 'search', description: 's', inputSchema: { type: 'object', properties: {} } }] as never)
      const llmToolsStore = useLlmToolsStore()
      await useTamagotchiMcpToolsStore().refresh()
      expect(names(llmToolsStore).sort()).toEqual(['builtIn_mcpCallTool', 'builtIn_mcpListTools', 'desktop__do_goal', 'desktop__whats_on_screen'])
    })

    it('off keeps the proxies only', async () => {
      invokeMocks.listMcpTools.mockResolvedValue(jarvisTools as never)
      useJarvisSpeedStore().directToolsSetting = 'off'
      const llmToolsStore = useLlmToolsStore()
      await useTamagotchiMcpToolsStore().refresh()
      expect(names(llmToolsStore).sort()).toEqual(['builtIn_mcpCallTool', 'builtIn_mcpListTools'])
    })

    it('the environment can turn it off, and a setting beats the environment', async () => {
      invokeMocks.listMcpTools.mockResolvedValue(jarvisTools as never)
      invokeMocks.speedEnv.mockResolvedValueOnce({ directTools: false, quickAck: true, quickCommand: true, timing: true })
      const llmToolsStore = useLlmToolsStore()
      const store = useTamagotchiMcpToolsStore()
      await store.refresh()
      expect(names(llmToolsStore).sort()).toEqual(['builtIn_mcpCallTool', 'builtIn_mcpListTools'])
      invokeMocks.speedEnv.mockResolvedValueOnce({ directTools: false, quickAck: true, quickCommand: true, timing: true })
      useJarvisSpeedStore().directToolsSetting = 'on'
      await store.refresh()
      expect(names(llmToolsStore)).toEqual(['desktop__do_goal', 'desktop__whats_on_screen'])
    })

    it('falls back to the proxies while the tool server cannot be listed, and switches to the real tools when it can', async () => {
      vi.useFakeTimers()
      try {
        invokeMocks.listMcpTools.mockRejectedValueOnce(new Error('MCP server jarvis is not running'))
        invokeMocks.listMcpTools.mockResolvedValue(jarvisTools as never)
        const llmToolsStore = useLlmToolsStore()
        const store = useTamagotchiMcpToolsStore()
        await store.refresh()
        expect(names(llmToolsStore).sort()).toEqual(['builtIn_mcpCallTool', 'builtIn_mcpListTools'])
        await vi.advanceTimersByTimeAsync(5_100)
        expect(names(llmToolsStore)).toEqual(['desktop__do_goal', 'desktop__whats_on_screen'])
        store.dispose()
      }
      finally {
        vi.useRealTimers()
      }
    })

    it('adds no call_summary argument to a direct tool and keeps it on the others', async () => {
      invokeMocks.listMcpTools.mockResolvedValue(jarvisTools as never)
      const llmToolsStore = useLlmToolsStore()
      await useTamagotchiMcpToolsStore().refresh()
      const { resolveLlmTools } = await import('@proj-airi/stage-ui/stores/ai/chat-llm/tool-resolver')
      const resolved = await resolveLlmTools({ activeTools: llmToolsStore.activeTools, builtInTools: [], webSearchTools: [] })
      const goal = resolved.find(tool => tool.function.name === 'desktop__do_goal')!
      expect(Object.keys((goal.function.parameters as { properties: object }).properties)).toEqual(['goal'])
    })
  })
})
