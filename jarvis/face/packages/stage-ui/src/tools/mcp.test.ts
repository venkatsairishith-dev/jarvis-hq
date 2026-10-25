import type { JsonSchema } from 'xsschema'

import type { McpToolRuntime } from './mcp'

import { describe, expect, it, vi } from 'vitest'

import { compactMcpToolDescriptor, createMcpTools, mcp } from './mcp'

function createRuntime(): McpToolRuntime {
  return {
    listTools: vi.fn(async () => []),
    callTool: vi.fn(async () => ({ content: [{ type: 'text', text: 'ok' }] })),
  }
}

describe('tools mcp schema', () => {
  it('emits strict parameter objects', async () => {
    const tools = await Promise.all(createMcpTools(createRuntime()))
    for (const name of ['builtIn_mcpListTools', 'builtIn_mcpCallTool']) {
      const t = tools.find(entry => entry.function.name === name)
      expect(t, `missing tool: ${name}`).toBeDefined()
      expect(t?.function.parameters.additionalProperties).toBe(false)
    }
  })

  it('builtIn_mcpCallTool uses flat name+arguments schema', async () => {
    const tools = await Promise.all(createMcpTools(createRuntime()))
    const callTool = tools.find(entry => entry.function.name === 'builtIn_mcpCallTool')
    expect(callTool).toBeDefined()

    const props = (callTool!.function.parameters as JsonSchema).properties!
    expect((props.name as JsonSchema).type).toBe('string')
    expect((props.arguments as JsonSchema).type).toBe('string')
  })
})

describe('mcp discovery', () => {
  // https://github.com/moeru-ai/airi/issues/2779
  // ROOT CAUSE:
  //
  // The default tool set exposed proxies backed by an unavailable MCP runtime.
  // We fixed this by leaving proxy registration to runtimes with an MCP transport.
  it('omits proxy tools without an MCP runtime (Issue #2779)', async () => {
    expect(await mcp()).toEqual([])
  })

  // https://github.com/moeru-ai/airi/issues/2779
  // ROOT CAUSE:
  //
  // An empty descriptor array gave the model no terminal discovery result.
  // We fixed this with an explicit message that asks the model to stop discovery.
  it('reports an empty catalog explicitly (Issue #2779)', async () => {
    const runtime = createRuntime()
    const [listTools] = await Promise.all(createMcpTools(runtime))

    const result = await listTools.execute({}, { messages: [], toolCallId: 'list-empty' })

    expect(result).toBe('No MCP tools are available. Do not retry discovery during this assistant turn.')
    expect(runtime.listTools).toHaveBeenCalledTimes(1)
  })

  // https://github.com/moeru-ai/airi/issues/2779
  // ROOT CAUSE:
  //
  // Discovery caught the transport error and returned an empty string.
  // We fixed this with an MCP error result that retains the failure reason.
  it('returns the discovery failure to the model (Issue #2779)', async () => {
    const runtime = createRuntime()
    vi.mocked(runtime.listTools).mockRejectedValue(new Error('MCP connection closed.'))
    const [listTools] = await Promise.all(createMcpTools(runtime))

    const result = await listTools.execute({}, { messages: [], toolCallId: 'list-failed' })

    expect(result).toEqual({
      isError: true,
      content: [{ type: 'text', text: 'MCP tool discovery failed: MCP connection closed.' }],
    })
    expect(runtime.listTools).toHaveBeenCalledTimes(1)
  })

  it('retains available tool descriptors and proxies invocation', async () => {
    const runtime = createRuntime()
    const descriptors = [{
      serverName: 'memory',
      name: 'memory::read_graph',
      toolName: 'read_graph',
      description: 'Read the memory graph.',
      inputSchema: { type: 'object', properties: {} },
    }]
    vi.mocked(runtime.listTools).mockResolvedValue(descriptors)
    const [listTools, callTool] = await Promise.all(createMcpTools(runtime))

    expect(await listTools.execute({}, { messages: [], toolCallId: 'list-success' })).toEqual(descriptors)
    expect(await callTool.execute({ name: 'memory::read_graph', arguments: '{}' }, { messages: [], toolCallId: 'call-success' }))
      .toEqual({ content: [{ type: 'text', text: 'ok' }] })
    expect(runtime.callTool).toHaveBeenCalledWith({ name: 'memory::read_graph', arguments: {} })
  })

  it('retains invocation failures as MCP error results', async () => {
    const runtime = createRuntime()
    vi.mocked(runtime.callTool).mockRejectedValue(new Error('Unknown tool.'))
    const [, callTool] = await Promise.all(createMcpTools(runtime))

    const result = await callTool.execute({ name: 'memory::missing', arguments: '{}' }, { messages: [], toolCallId: 'call-failed' })

    expect(result).toEqual({ isError: true, content: [{ type: 'text', text: 'Unknown tool.' }] })
  })
})

describe('compact tool descriptors (JARVIS)', () => {
  const full = {
    serverName: 'jarvis',
    name: 'jarvis::deliverables__make_report',
    toolName: 'deliverables__make_report',
    description: ['Make a report.', '  It takes a spec with a title and sections, writes a PDF and checks it. '].join(String.fromCharCode(10)).repeat(4),
    inputSchema: {
      type: 'object',
      title: 'make_reportArguments',
      properties: {
        spec: { type: 'object', description: 'A long description of the spec that the model does not need here.', title: 'Spec' },
        open_after: { type: 'boolean', default: false, description: 'x' },
        kind: { type: 'string', enum: ['report', 'memo'] },
        many: { type: 'string', enum: Array.from({ length: 20 }, (_, i) => `v${i}`) },
      },
      required: ['spec'],
    },
  }

  it('keeps the names, the argument types and what is required, and drops the rest', () => {
    const compact = compactMcpToolDescriptor(full)
    expect(compact.name).toBe('jarvis::deliverables__make_report')
    expect(compact.toolName).toBe('deliverables__make_report')
    expect(compact.inputSchema).toEqual({
      type: 'object',
      properties: { spec: { type: 'object' }, open_after: { type: 'boolean' }, kind: { type: 'string', enum: ['report', 'memo'] }, many: { type: 'string' } },
      required: ['spec'],
    })
  })

  it('puts the description on one line of at most 110 characters', () => {
    const compact = compactMcpToolDescriptor(full)
    expect(compact.description.includes(String.fromCharCode(10))).toBe(false)
    expect(compact.description.length).toBeLessThanOrEqual(110)
    expect(compact.description.endsWith('…')).toBe(true)
  })

  it('makes a catalog of 105 such tools about a third of the full size', () => {
    const catalog = Array.from({ length: 105 }, (_, i) => ({ ...full, name: `jarvis::t${i}`, toolName: `t${i}` }))
    const before = JSON.stringify(catalog).length
    const after = JSON.stringify(catalog.map(t => compactMcpToolDescriptor(t))).length
    expect(after).toBeLessThan(before / 2)
  })

  it('copes with a tool that has no schema or description', () => {
    const compact = compactMcpToolDescriptor({ serverName: 's', name: 's::t', toolName: 't', inputSchema: {} } as never)
    expect(compact.description).toBe('')
    expect(compact.inputSchema).toEqual({ type: 'object', properties: {} })
  })
})
