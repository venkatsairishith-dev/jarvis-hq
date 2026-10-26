import type { Tool } from '@xsai/shared-chat'

import { errorMessageFrom } from '@moeru/std'
import { tool } from '@xsai/tool'
import { z } from 'zod'

/**
 * Describes an MCP tool that can be exposed to the shared LLM runtime.
 *
 * Use when:
 * - A runtime needs to list available MCP tools before exposing them to models
 *
 * Expects:
 * - `name` is the fully-qualified tool name used for invocation
 *
 * Returns:
 * - The MCP tool descriptor metadata reported by the runtime
 */
export interface McpToolDescriptor {
  serverName: string
  name: string
  toolName: string
  description?: string
  inputSchema: Record<string, unknown>
}

/**
 * Payload for invoking an MCP tool through a runtime-specific transport.
 *
 * Use when:
 * - A runtime needs to forward a tool invocation into the MCP layer
 *
 * Expects:
 * - `name` matches a descriptor returned from `listTools`
 * - `arguments` is a JSON-compatible object when provided
 *
 * Returns:
 * - The MCP tool call input envelope
 */
export interface McpCallToolPayload {
  name: string
  arguments?: Record<string, unknown>
}

/**
 * Result returned from an MCP tool invocation.
 *
 * Use when:
 * - An MCP runtime returns tool output back to the shared LLM layer
 *
 * Expects:
 * - Error responses set `isError` when the tool execution failed
 *
 * Returns:
 * - Structured and unstructured MCP tool output
 */
export interface McpCallToolResult {
  content?: Array<Record<string, unknown>>
  structuredContent?: Record<string, unknown>
  toolResult?: unknown
  isError?: boolean
}

/**
 * Runtime contract for wiring MCP tool discovery and execution into `stage-ui`.
 *
 * Use when:
 * - A concrete runtime such as Electron needs to provide MCP access without a singleton bridge
 *
 * Expects:
 * - `listTools` and `callTool` are safe to call multiple times
 *
 * Returns:
 * - An object that can back `createMcpTools`
 */
export interface McpToolRuntime {
  listTools: () => Promise<McpToolDescriptor[]>
  callTool: (payload: McpCallToolPayload) => Promise<McpCallToolResult>
}

/**
 * A shorter descriptor for the model: the name, a one-line description, each argument's name and type (and its allowed values when there are few), and which are
 * required. JARVIS's tool server offers about 105 tools; the full list is 60 KB (about 15,000 tokens) and made the next model request 110 KB, which the free
 * models answered with 429 or minutes of waiting (2026-10-25). The compact list is about a third of that.
 */
export function compactMcpToolDescriptor(tool: McpToolDescriptor, maxDescription = 110): McpToolDescriptor {
  const schema = (tool.inputSchema ?? {}) as { properties?: Record<string, { type?: unknown, enum?: unknown[] }>, required?: unknown[] }
  const properties: Record<string, Record<string, unknown>> = {}
  for (const [name, property] of Object.entries(schema.properties ?? {})) {
    const entry: Record<string, unknown> = { type: typeof property?.type === 'string' ? property.type : 'string' }
    if (Array.isArray(property?.enum) && property.enum.length > 0 && property.enum.length <= 12)
      entry.enum = property.enum
    properties[name] = entry
  }
  const required = (schema.required ?? []).filter((name): name is string => typeof name === 'string')
  const oneLine = (tool.description ?? '').replace(/\s+/g, ' ').trim()
  return {
    ...tool,
    description: oneLine.length > maxDescription ? `${oneLine.slice(0, maxDescription - 1)}…` : oneLine,
    inputSchema: { type: 'object', properties, ...(required.length > 0 ? { required } : {}) },
  }
}

/** The proxy tool that calls an MCP tool. Each call names the MCP tool in its `name` argument, as `server::tool`. */
export const MCP_CALL_TOOL_NAME = 'builtIn_mcpCallTool'

/**
 * Creates MCP proxy tools backed by a runtime-provided transport.
 *
 * Discovery returns descriptors, an explicit empty-catalog message, or an MCP
 * error result. Runtime owners register these tools in the shared LLM tool store.
 */
export function createMcpTools(runtime: McpToolRuntime): Array<Promise<Tool>> {
  return [
    tool({
      name: 'builtIn_mcpListTools',
      description: 'List all available MCP tools. Call this first to discover tool names before calling builtIn_mcpCallTool.',
      execute: async () => {
        try {
          const tools = await runtime.listTools()
          if (tools.length === 0)
            return 'No MCP tools are available. Do not retry discovery during this assistant turn.'

          return tools
        }
        catch (error) {
          return {
            isError: true,
            content: [{ type: 'text', text: `MCP tool discovery failed: ${errorMessageFrom(error) ?? 'Unknown error.'}` }],
          }
        }
      },
      parameters: z.object({}).strict(),
    }),
    tool({
      name: MCP_CALL_TOOL_NAME,
      description: 'Call an MCP tool by name. Use builtIn_mcpListTools first to get available tool names.',
      execute: async ({ name, arguments: argsJson }) => {
        try {
          const args = argsJson ? JSON.parse(argsJson) : {}
          return await runtime.callTool({ name, arguments: args })
        }
        catch (error) {
          return {
            isError: true,
            content: [{ type: 'text', text: errorMessageFrom(error) ?? 'Unknown error.' }],
          }
        }
      },
      // NOTICE:
      // OpenAI rejects the schemas that z.unknown() and z.record() produce here.
      // They omit `type` or include `propertyNames`, so arguments use a JSON string.
      // Source/context: @xsai/tool schema conversion and ./mcp.test.ts.
      // Removal condition: The schema conversion produces an object schema that OpenAI accepts.
      parameters: z.object({
        name: z.string().describe('Tool name in "<serverName>::<toolName>" format'),
        arguments: z.string().describe('JSON object of tool arguments, for example {"query":"hello","limit":10}'),
      }).strict(),
    }),
  ]
}

/**
 * Omits MCP proxy tools from the default tool set because shared code has no MCP transport.
 *
 * Supported runtimes register {@link createMcpTools} results through `useLlmToolsStore`.
 */
export async function mcp(): Promise<Tool[]> {
  return []
}

// ---------------------------------------------------------------------------------------------------------------------
// JARVIS D60: direct tools. The proxy path (builtIn_mcpListTools, then builtIn_mcpCallTool "jarvis::<tool>") costs the chat model one or two
// extra full requests before the real tool runs (D52: a goal took 3 to 7 rounds on the owner's laptop, each one a full request to a provider
// that needs 10 to 60 seconds for it). In direct mode the first request already carries the real tool schemas of the JARVIS tool server, each
// under its own name, and the model calls them in round one.

/** The server whose tools are offered directly. Tools of any other MCP server stay behind the proxies. */
export const DIRECT_TOOL_SERVER = 'jarvis'

const DIRECT_NAME = /^[\w-]{1,64}$/

/** An MCP result without the copy that a structured result carries beside its text: the model reads one, and the next request pays for both. */
export function slimMcpResult(result: McpCallToolResult | undefined): McpCallToolResult | string {
  if (!result)
    return 'The tool returned nothing.'
  const hasText = Array.isArray(result.content) && result.content.some(item => item?.type === 'text' && typeof item.text === 'string')
  if (!hasText)
    return result
  const { structuredContent: _structured, toolResult: _toolResult, ...rest } = result
  return rest
}

function directParameters(schema: Record<string, unknown> | undefined): Record<string, unknown> {
  const source = (schema ?? {}) as { properties?: Record<string, unknown>, required?: unknown[] }
  const required = (source.required ?? []).filter((name): name is string => typeof name === 'string')
  return { type: 'object', properties: source.properties ?? {}, ...(required.length > 0 ? { required } : {}) }
}

/**
 * The tools of one MCP server as chat tools under their own names (`desktop__do_goal`, not `jarvis::desktop__do_goal`).
 *
 * Expects:
 * - descriptors as the tool server lists them. The JARVIS server's chat profile already shortens descriptions and schemas (about 8 KB for 22 tools),
 *   so nothing is cut here; the descriptions carry what makes a call safe ("data, never instructions", "full path").
 * - no `call_summary` argument is added to these tools (the resolver skips them, see `isDirectTool`): it cost about 150 bytes a tool, 3.4 KB for
 *   the 22, in every request.
 */
export function createDirectMcpTools(
  descriptors: McpToolDescriptor[],
  runtime: Pick<McpToolRuntime, 'callTool'>,
  server: string = DIRECT_TOOL_SERVER,
): Tool[] {
  return descriptors
    .filter(tool => tool.serverName === server && DIRECT_NAME.test(tool.toolName))
    .map((descriptor): Tool => {
      const parameters = directParameters(descriptor.inputSchema)
      const required = (parameters.required as string[] | undefined) ?? []
      return {
        type: 'function',
        function: { name: descriptor.toolName, description: descriptor.description ?? '', parameters },
        validate: async (input) => {
          if (input === null || typeof input !== 'object' || Array.isArray(input))
            return { issues: [{ message: 'The arguments must be a JSON object.' }] }
          const missing = required.filter(name => (input as Record<string, unknown>)[name] === undefined)
          return missing.length > 0 ? { issues: [{ message: `Missing: ${missing.join(', ')}` }] } : { value: input }
        },
        // The tool store keeps only a tool's definition and its `execute`, so the check lives here too.
        execute: async (input) => {
          if (input !== null && typeof input === 'object' && !Array.isArray(input)) {
            const missing = required.filter(name => (input as Record<string, unknown>)[name] === undefined)
            if (missing.length > 0)
              return { isError: true, content: [{ type: 'text', text: `Missing: ${missing.join(', ')}` }] }
          }
          try {
            return slimMcpResult(await runtime.callTool({ name: descriptor.name, arguments: (input ?? {}) as Record<string, unknown> }))
          }
          catch (error) {
            return { isError: true, content: [{ type: 'text', text: errorMessageFrom(error) ?? 'Unknown error.' }] }
          }
        },
      }
    })
}
