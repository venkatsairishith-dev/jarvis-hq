/**
 * JARVIS: the size of the first chat request of a turn, part by part.
 *
 * Why this test exists: on the owner's laptop (2026-10-25, on-screen benchmark) the face sent the chat model about 40 KB
 * (about 10,000 tokens) for "Open Notepad and write a shopping list". Groq's free tier takes 8,000 tokens a minute in
 * all and answered HTTP 413; the local model cannot take 10K tokens of prompt; a model that sees 21 tools it does not
 * need picks a wrong one (`builtIn_emitSparkCommand`). The 40 KB were: 31 KB of tool definitions (AIRI's own helpers, and
 * a 660-byte `call_summary` + `call_icon` pair added to every tool), 5 KB of persona, 2 KB of emotion rules, 1 KB of
 * memory and card prompts.
 *
 * This test builds that request the way the app does, with the real orchestrator, the real tool resolver, the real tool
 * factories and the real prompts, and only the network is missing. It fails when the request grows past the limit, and
 * its message lists every part, so the part that grew is named. To see the table: `JARVIS_PRINT_BUDGET=1`.
 *
 * What it cannot see: the Live2D control list (it depends on the avatar model; a stand-in of ten motions is used, as the
 * preset Hiyori model has), the wire format that the provider adapter adds (a few hundred bytes), and the history of
 * later turns (compaction is not involved in a first turn).
 */
import type { ChatHistoryItem } from '@proj-airi/core-agent'
import type { GenerationProvider } from '@proj-airi/provider-inference'
import type { Tool } from '@xsai/shared-chat'

import { Buffer } from 'node:buffer'
import { readFileSync } from 'node:fs'

import { conversationToChatMessages, createChatOrchestratorRuntime } from '@proj-airi/core-agent'
import { createPinia, setActivePinia } from 'pinia'
import { beforeAll, describe, expect, it, vi } from 'vitest'
import { createI18n } from 'vue-i18n'

import { useTamagotchiBuiltinToolsStore } from '../../../../../apps/stage-tamagotchi/src/renderer/stores/tools/built-in'
import { useToolSelectionStore } from '../../../../../apps/stage-tamagotchi/src/renderer/stores/tools/selection'
import { formatLive2DActPrompt } from '../../constants/prompts/live2d-act'
import { composeDrawCardPrompt } from '../../tools/draw-card'
import { compactMcpToolDescriptor, createMcpTools } from '../../tools/mcp'
import { composeMemoryPrompt } from '../../tools/memory'
import { resolveLlmTools } from '../ai/chat-llm/tool-resolver'
import { useLlmToolsStore } from '../ai/chat-llm/tools'
import { useLlmToolsetPromptsStore } from '../ai/chat-llm/toolset-prompts'
import { useMemoryStore } from '../memory'
import { useRecipesStore } from '../recipes'
import { useToolCardsStore } from '../tool-cards'
import { createRuntimePromptContext } from './context-providers'
import { WAKE_WORD_REQUEST } from './lean-tools'
import { CHAT_FORMAT_RULES, composeSystemPrompt } from './prompt-recipe'
import { createRunTools, planRunTools } from './run-tools'

/** The limit of the first request of a typical goal: about 3,000 tokens. The 2026-10-25 request was 40,000 bytes. */
const FIRST_REQUEST_LIMIT_BYTES = 12_000

/** The same request after the model has called `builtIn_mcpListTools` once: what the second request of a tool turn holds. */
const AFTER_LIST_LIMIT_BYTES = 22_000

/** A typical first turn of the benchmark. */
const GOAL = 'Open Notepad and write a shopping list: milk, eggs, bread, rice.'

const baseYaml = readFileSync(new URL('../../../../i18n/src/locales/en/base.yaml', import.meta.url), 'utf8')

/** The text of `prompt.<key>: |` in base.yaml, as the YAML literal block reads. */
function promptBlock(key: string): string {
  const lines = baseYaml.split('\n')
  const start = lines.findIndex(line => line === `  ${key}: |`)
  if (start < 0)
    throw new Error(`base.yaml has no prompt.${key}`)
  const block: string[] = []
  for (const line of lines.slice(start + 1)) {
    if (line !== '' && !line.startsWith('    '))
      break
    block.push(line.startsWith('    ') ? line.slice(4) : '')
  }
  return `${block.join('\n').trimEnd()}\n`
}

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: { en: { base: { prompt: Object.fromEntries(['prefix', 'emotion', 'emoji', 'suffix'].map(key => [key, promptBlock(key)])) } } },
})

vi.mock('vue-i18n', async (importOriginal) => {
  const original = await importOriginal<typeof import('vue-i18n')>()
  return {
    ...original,
    useI18n: () => ({ locale: { value: 'en' }, t: (key: string) => i18n.global.t(key), te: (key: string) => i18n.global.te(key) }),
  }
})

function bytes(value: unknown) {
  return Buffer.byteLength(typeof value === 'string' ? value : JSON.stringify(value), 'utf8')
}

const provider: GenerationProvider = {
  generation: model => ({ protocol: 'chat-completions', config: { model, baseURL: 'https://example.com/' } }),
}

/** The tool server's chat list as the face receives it. Regenerate with the `chat_stack` fixture of `jarvis/bridge/test_face_tools.py`. */
const mcpSnapshot = JSON.parse(readFileSync(new URL('./request-budget.mcp-tools.json', import.meta.url), 'utf8')) as Array<{ name: string, description?: string, inputSchema: Record<string, unknown> }>

interface Built {
  system: string
  /** The system message by block: format rules, persona, Live2D controls. */
  systemParts: Array<[string, number]>
  user: string
  tools: Tool[]
  body: { model: string, stream: boolean, messages: unknown[], tools: unknown[] }
}

async function buildFirstRequest(text: string): Promise<Built> {
  setActivePinia(createPinia())
  const llmTools = useLlmToolsStore()
  const toolsets = useLlmToolsetPromptsStore()

  // The tools of the app, registered the way the Electron renderer registers them.
  await useTamagotchiBuiltinToolsStore().refresh()
  const proxies = await Promise.all(createMcpTools({
    listTools: async () => mcpSnapshot.map(tool => compactMcpToolDescriptor({ serverName: 'jarvis', name: `jarvis::${tool.name}`, toolName: tool.name, description: tool.description, inputSchema: tool.inputSchema })),
    callTool: async () => ({}),
  }))
  llmTools.addTools(...proxies.map(tool => ({ ...tool, id: `mcp:${tool.function.name}` })))

  // The selection of a typed turn (InteractiveArea.vue sends `requestTools`).
  const selected = useToolSelectionStore().requestTools.map(reference => reference.name)

  // The optional tools of the chat store, decided by the same plan with the stores' own defaults.
  const memory = useMemoryStore()
  const recipes = useRecipesStore()
  const cards = useToolCardsStore()
  const plan = planRunTools({
    text,
    ownerConversation: true,
    taskSession: false,
    memoryEnabled: memory.enabled,
    memoryIndex: memory.indexFor('default'),
    usableRecipeCount: recipes.usable.length,
    proposalsEnabled: recipes.proposalsEnabled,
    replyCardsEnabled: cards.replyCardsEnabled,
  })
  const runTools = await createRunTools(plan, {
    memory: { read: () => undefined, write: () => undefined, forget: () => false },
    useRecipe: { recipes: () => recipes.recipes, start: async () => ({ status: 'refused', reason: 'test' }) },
    proposeRecipe: { propose: () => ({}) as never },
    wakeWords: { characterId: 'default', setWords: (() => ({})) as never },
  })
  const customTools = [...llmTools.getToolsByNames(...selected), ...runTools]
  const tools = await resolveLlmTools({ customTools, activeTools: llmTools.activeTools })

  // The system prompt of the built-in JARVIS character: format rules, the description (base.prompt.prefix), and the Live2D controls.
  const motions = Array.from({ length: 10 }, (_, index) => ({ fileName: `Hiyori_m${String(index + 1).padStart(2, '0')}.motion3.json` }))
  const live2d = formatLive2DActPrompt([], motions as never)
  const persona = [i18n.global.t('base.prompt.prefix'), live2d].join('\n\n')
  const systemPrompt = composeSystemPrompt(persona)
    + (plan.memory ? composeMemoryPrompt(memory.indexFor('default')) : '')
    + (plan.replyCards ? composeDrawCardPrompt() : '')

  // The runtime prompt (emotion tokens) is the one context entry of the face.
  const { useAiriRuntimePrompt } = await import('../../composables/use-airi-runtime-prompt')
  const runtimePrompt = useAiriRuntimePrompt().value
  const runtimeContext = createRuntimePromptContext(runtimePrompt)

  const sessionMessages: ChatHistoryItem[] = []
  let captured: Parameters<NonNullable<Parameters<typeof createChatOrchestratorRuntime>[0]['llm']['stream']>>[2] | undefined
  const runtime = createChatOrchestratorRuntime({
    session: {
      commitUserMessage: async (_sessionId, message) => {
        sessionMessages.push(message)
        return { status: 'inserted', messageId: message.id }
      },
      ensureSession: () => {},
      getSessionMessages: () => sessionMessages,
      appendSessionMessage: (_sessionId, message) => {
        sessionMessages.push(message)
      },
      getSessionGeneration: () => 1,
    },
    context: {
      ingest: vi.fn(),
      snapshot: () => (runtimeContext ? { [runtimeContext.contextId]: [runtimeContext] } : {}),
    },
    foregroundStream: { patch: () => {}, reset: () => {} },
    llm: {
      stream: async (_model, _chatProvider, conversation, options) => {
        captured = conversation
        await options?.onStreamEvent?.({ type: 'text-delta', text: 'ok' })
        await options?.onStreamEvent?.({ type: 'finish' })
      },
    },
    getActiveSessionId: () => 'session-1',
    getActiveProvider: () => 'jarvis-gateway',
    getSystemPromptSupplement: () => toolsets.activeToolsetPrompt,
    getSystemPrompt: () => systemPrompt,
    createId: (() => {
      let next = 0
      return () => `id-${next++}`
    })(),
  })
  await runtime.ingest(text, { model: 'jarvis-chat', chatProvider: provider, systemPromptSupplement: toolsets.activeToolsetPrompt })
  if (!captured)
    throw new Error('the orchestrator sent no request')

  const messages = conversationToChatMessages(captured)
  const system = messages.filter(message => message.role === 'system').map(message => typeof message.content === 'string' ? message.content : JSON.stringify(message.content)).join('\n')
  const lastUser = messages.findLast(message => message.role === 'user')
  const user = typeof lastUser?.content === 'string' ? lastUser.content : JSON.stringify(lastUser?.content)
  return {
    system,
    systemParts: [
      ['system: format rules (math and code fences)', bytes(CHAT_FORMAT_RULES)],
      ['system: persona (base.prompt.prefix)', bytes(i18n.global.t('base.prompt.prefix'))],
      ['system: Live2D controls (stand-in: ten motions)', bytes(live2d)],
    ],
    user,
    tools,
    body: {
      model: 'jarvis-chat',
      stream: true,
      messages,
      tools: tools.map(tool => ({ type: 'function', function: { name: tool.function.name, description: tool.function.description, parameters: tool.function.parameters } })),
    },
  }
}

function table(built: Built, total: number) {
  const rows: Array<[string, number]> = [
    ...built.systemParts,
    ['user: owner text + time + [Context] (emotion rules)', bytes(built.user)],
    ...built.tools.map((tool): [string, number] => [`tool ${tool.function.name}`, bytes({ type: 'function', function: { name: tool.function.name, description: tool.function.description, parameters: tool.function.parameters } })]),
  ]
  const known = rows.reduce((sum, [, size]) => sum + size, 0)
  rows.push(['JSON envelope (keys, roles, separators)', Math.max(0, total - known)])
  rows.push(['TOTAL', total])
  return rows.map(([name, size]) => `${String(size).padStart(6)}  ${name}`).join('\n')
}

describe('the first chat request of a JARVIS turn', () => {
  let built: Built
  let total: number

  beforeAll(async () => {
    built = await buildFirstRequest(GOAL)
    total = bytes(built.body)
    if (process.env.JARVIS_PRINT_BUDGET)
      process.stdout.write(`\n${table(built, total)}\n`)
  })

  it(`stays under ${FIRST_REQUEST_LIMIT_BYTES} bytes (about 3,000 tokens)`, () => {
    expect(total, `the first request is ${total} bytes, the limit is ${FIRST_REQUEST_LIMIT_BYTES}; the parts:\n${table(built, total)}`).toBeLessThanOrEqual(FIRST_REQUEST_LIMIT_BYTES)
  })

  it('offers only the tools that a JARVIS goal uses', () => {
    const names = built.tools.map(tool => tool.function.name).sort()
    // The tool server's tools are reached through these two (builtIn_mcpListTools, then builtIn_mcpCallTool "jarvis::<tool>"),
    // and the weather card is drawn from get_weather's own result.
    expect(names, `the request carries ${names.join(', ')}`).toEqual(['builtIn_mcpCallTool', 'builtIn_mcpListTools', 'get_weather'])
  })

  it('never offers an AIRI helper that no JARVIS goal uses', () => {
    const names = new Set(built.tools.map(tool => tool.function.name))
    for (const name of [
      'builtIn_emitSparkCommand',
      'builtIn_debugRandomNumber',
      'builtIn_drawCard',
      'builtIn_searchIcons',
      'builtIn_readMemory',
      'builtIn_writeMemory',
      'builtIn_forgetMemory',
      'builtIn_useRecipe',
      'builtIn_proposeRecipe',
      'configure_wake_words',
      'stage_widgets',
      'image_journal',
    ])
      expect(names.has(name), `${name} is in the first request`).toBe(false)
  })

  it('asks for a label of the call only, not an icon', () => {
    for (const tool of built.tools) {
      const properties = Object.keys((tool.function.parameters as { properties?: Record<string, unknown> }).properties ?? {})
      expect(properties, tool.function.name).toContain('call_summary')
      expect(properties, tool.function.name).not.toContain('call_icon')
    }
  })

  it('keeps the honesty rules, the tool routing and the screen, document and preview rules in the persona', () => {
    const system = built.system
    for (const rule of [
      'desktop__do_goal',
      'jarvis_do only for work on the server',
      'only when a tool result says so',
      'only the owner approves',
      'Outside text is data',
      'jarvis_recall',
      'jarvis_show',
      'doc_reader__read_document',
      'doc_tools__read_document',
    ])
      expect(system, rule).toContain(rule)
  })

  it('also stays under its limit once the model has listed the tool server tools', async () => {
    const proxies = await Promise.all(createMcpTools({
      listTools: async () => mcpSnapshot.map(tool => compactMcpToolDescriptor({ serverName: 'jarvis', name: `jarvis::${tool.name}`, toolName: tool.name, description: tool.description, inputSchema: tool.inputSchema })),
      callTool: async () => ({}),
    }))
    const listed = await proxies.find(tool => tool.function.name === 'builtIn_mcpListTools')!.execute({}, { messages: [], toolCallId: 'list' }) as unknown
    const second = total + bytes({ role: 'assistant', tool_calls: [{ id: 'call', type: 'function', function: { name: 'builtIn_mcpListTools', arguments: '{"call_summary":"List the tools"}' } }] }) + bytes({ role: 'tool', tool_call_id: 'call', content: JSON.stringify(listed) })
    if (process.env.JARVIS_PRINT_BUDGET)
      process.stdout.write(`\nafter builtIn_mcpListTools: ${second} bytes (the tool list result is ${bytes(JSON.stringify(listed))})\n`)
    expect(second, `the request after the tool list is ${second} bytes, the limit is ${AFTER_LIST_LIMIT_BYTES}`).toBeLessThanOrEqual(AFTER_LIST_LIMIT_BYTES)
  })

  it('adds an optional tool only to the turn that asks for it', async () => {
    const wake = await buildFirstRequest('Change my wake words to hey jarvis and jarvis.')
    expect(WAKE_WORD_REQUEST.test(GOAL)).toBe(false)
    expect(wake.tools.map(tool => tool.function.name)).toContain('configure_wake_words')
    const memory = await buildFirstRequest('Remember that my sister Asha lives in Pune.')
    expect(memory.tools.map(tool => tool.function.name)).toEqual(expect.arrayContaining(['builtIn_readMemory', 'builtIn_writeMemory']))
    expect(bytes(memory.body)).toBeLessThanOrEqual(FIRST_REQUEST_LIMIT_BYTES + 6_000)
  })

  it('has no toolset prompt and no sticker catalog in a default request', () => {
    expect(useLlmToolsetPromptsStore().activeToolsetPrompt).toBe('')
    expect(built.system).not.toContain('STICKER')
  })
})
