/**
 * JARVIS D60: the quick-command path of a chat turn. A locally made reply must go through the real orchestrator as an ordinary answer (stored
 * assistant message, speech hooks), and the owner's text must be read back from the conversation the orchestrator really builds.
 */
import type { ChatHistoryItem } from '@proj-airi/core-agent'
import type { GenerationProvider } from '@proj-airi/provider-inference'

import { createChatOrchestratorRuntime } from '@proj-airi/core-agent'
import { describe, expect, it } from 'vitest'

import { guessTask, mightBeQuickCommand, parseQuickResult } from './quick-intent'
import { emitLocalReply, ownerTextOf } from './quick-reply'

const provider: GenerationProvider = {
  generation: model => ({ protocol: 'chat-completions', config: { model, baseURL: 'https://example.com/' } }),
}

function makeRuntime(llm: Parameters<typeof createChatOrchestratorRuntime>[0]['llm']['stream']) {
  const messages: ChatHistoryItem[] = []
  const runtime = createChatOrchestratorRuntime({
    session: {
      commitUserMessage: async (_id, message) => {
        messages.push(message)
        return { status: 'inserted', messageId: message.id }
      },
      ensureSession: () => {},
      getSessionMessages: () => messages,
      appendSessionMessage: (_id, message) => {
        messages.push(message)
      },
      getSessionGeneration: () => 1,
    },
    context: { ingest: () => {}, snapshot: () => ({}) },
    foregroundStream: { patch: () => {}, reset: () => {} },
    llm: { stream: llm },
    getActiveSessionId: () => 'session-1',
    getActiveProvider: () => 'jarvis-gateway',
    createId: (() => {
      let next = 0
      return () => `id-${next++}`
    })(),
    now: () => Date.parse('2026-10-26T14:05:00'),
  })
  return { runtime, messages }
}

describe('a reply made by rules in place of a model answer', () => {
  it('reads the owners plain text back from the conversation the orchestrator builds, without the time prefix or the runtime context', async () => {
    let seen = ''
    const { runtime } = makeRuntime(async (_model, _provider, conversation, options) => {
      seen = ownerTextOf(conversation)
      await emitLocalReply('ok', options)
    })
    await runtime.ingest('Open Notepad and write hello', { model: 'jarvis-chat', chatProvider: provider })
    expect(seen).toBe('Open Notepad and write hello')
  })

  it('is stored and spoken like any answer: an assistant message, the token hooks and the end hooks', async () => {
    const { runtime, messages } = makeRuntime(async (_model, _provider, _conversation, options) => {
      await emitLocalReply('Opened Notepad.', options)
    })
    const tokens: string[] = []
    let ended = 0
    runtime.hooks.onTokenLiteral(async (literal) => {
      tokens.push(literal)
    })
    runtime.hooks.onAssistantResponseEnd(async () => {
      ended += 1
    })
    await runtime.ingest('open notepad', { model: 'jarvis-chat', chatProvider: provider })
    const assistant = messages.findLast(message => message.role === 'assistant')
    expect(JSON.stringify(assistant)).toContain('Opened Notepad.')
    expect(tokens.join('')).toContain('Opened Notepad.')
    expect(ended).toBe(1)
  })

  it('returns an empty text when the latest message is not plain text', () => {
    const conversation = { turns: [{ id: 'u', type: 'user', content: [{ type: 'text', text: 'what is this' }, { type: 'image', url: 'data:image/png;base64,AA' }] }] } as never
    expect(ownerTextOf(conversation)).toBe('')
    const reply = { turns: [{ id: 'u', type: 'user', content: [{ type: 'text', text: '[2026-10-26 14:05] [Replying to: hi] open notepad' }] }] } as never
    expect(ownerTextOf(reply)).toBe('')
    expect(ownerTextOf({ turns: [] } as never)).toBe('')
  })
})

describe('the instant guess', () => {
  it.each([
    ['Open Notepad and write a shopping list: milk, eggs, bread, rice.', 'pc'],
    ['hey jarvis, please minimize chrome', 'pc'],
    ['pause the music', 'pc'],
    ['play despacito', 'pc'],
    ['type hello into notepad', 'pc'],
    ['What is on my screen right now?', 'screen'],
    ['which windows are open', 'screen'],
    ['what is the weather in Paris', 'weather'],
    ['read this invoice for me', 'doc'],
    ['make a deck about electric buses', 'doc'],
  ])('%s -> %s', (text, kind) => {
    expect(guessTask(text)?.kind).toBe(kind)
  })

  it.each([
    'how are you today',
    'write a poem about autumn',
    'start by explaining what a monad is',
    'tell me a joke',
    '',
  ])('leaves an ordinary message alone: %s', (text) => {
    expect(guessTask(text)).toBeNull()
  })

  it('knows the false positive it accepts: a wrong "On it" for a few seconds changes nothing that runs', () => {
    expect(guessTask('play it safe and tell me the plan')?.kind).toBe('pc')
  })
})

describe('when the router is worth asking', () => {
  it.each([
    'open notepad',
    'Hey JARVIS, pause',
    'volume up',
    'what time is it',
    'What\'s the date today?',
    'tell me the time',
  ])('asks for %s', (text) => {
    expect(mightBeQuickCommand(text)).toBe(true)
  })

  it.each([
    'how are you today',
    'what is the capital of France',
    'what is on my screen',
    'what is the weather',
    'explain what time complexity means',
    'write a poem about autumn',
    '',
  ])('goes straight to the chat model for %s', (text) => {
    expect(mightBeQuickCommand(text)).toBe(false)
  })
})

describe('the router answer', () => {
  it('reads a handled answer from the structured part or from the JSON text', () => {
    const body = { handled: true, status: 'completed', reply: 'Opened Notepad.', label: 'Opening notepad' }
    expect(parseQuickResult({ structuredContent: body })).toEqual({ handled: true, reply: 'Opened Notepad.', status: 'completed', label: 'Opening notepad' })
    expect(parseQuickResult({ content: [{ type: 'text', text: JSON.stringify(body) }] }).reply).toBe('Opened Notepad.')
    // the tool server wraps a plugin's answer as { ok, result } (live 2026-10-26)
    expect(parseQuickResult({ content: [{ type: 'text', text: JSON.stringify({ ok: true, result: body }) }] }).reply).toBe('Opened Notepad.')
  })

  it.each([
    undefined,
    null,
    'text',
    { isError: true, content: [{ type: 'text', text: JSON.stringify({ handled: true, reply: 'x' }) }] },
    { structuredContent: { handled: false } },
    { structuredContent: { handled: true } },
    { structuredContent: { handled: true, reply: '   ' } },
    { content: [{ type: 'text', text: 'not json' }] },
    { content: [] },
  ])('reads anything else as "not handled": %j', (raw) => {
    expect(parseQuickResult(raw)).toEqual({ handled: false })
  })
})
