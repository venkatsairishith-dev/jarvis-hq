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

/** The name of the one MCP server the face connects to. */
export const JARVIS_TOOL_SERVER_NAME = 'jarvis'

/** The local speech server's model id, and its voice that picks English, Hindi or Telugu from the text. */
export const JARVIS_SPEECH_MODEL = 'jarvis-local'
export const JARVIS_SPEECH_VOICE = 'auto'
