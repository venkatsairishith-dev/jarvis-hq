/**
 * Where the face finds the rest of JARVIS. Everything is on this PC (loopback) or on the owner's tailnet.
 *
 * - Chat goes through the privacy gateway, which removes private values before any model sees them. The gateway passes
 *   the caller's own provider key through, so the owner enters that key once in the face's provider settings.
 * - Speech (text to speech and speech to text) goes to the local speech server. It needs no key.
 * - Tools come from the JARVIS tool server over MCP. The kernel decides every action there; the face only asks.
 */
export const JARVIS_ENDPOINTS = {
  chat: 'http://127.0.0.1:48950/v1/',
  speech: 'http://127.0.0.1:48930/v1/',
  tools: 'http://127.0.0.1:48970/mcp',
} as const

/**
 * Where AIRI's cloud client (account, notifications inbox, feature flags) points. JARVIS has no cloud, so this is
 * a loopback address with nothing behind it: those code paths fail at once, locally, and nothing leaves the PC.
 */
export const JARVIS_NO_CLOUD_URL = 'http://127.0.0.1:1'

/**
 * What the face sends as the provider key. The privacy gateway swaps it for the key in `D:\jarvis-secrets\providers\dahl.key`,
 * so the face never holds a provider key and the owner types none.
 */
export const JARVIS_LOCAL_KEY = 'jarvis-local'

/** The chat model the gateway's provider (Dahl) serves. */
export const JARVIS_CHAT_MODEL = 'MiniMaxAI/MiniMax-M2.7'

/** The name of the one MCP server the face connects to. */
export const JARVIS_TOOL_SERVER_NAME = 'jarvis'

/** The local speech server's model id, and its voice that picks English, Hindi or Telugu from the text. */
export const JARVIS_SPEECH_MODEL = 'jarvis-local'
export const JARVIS_SPEECH_VOICE = 'auto'
