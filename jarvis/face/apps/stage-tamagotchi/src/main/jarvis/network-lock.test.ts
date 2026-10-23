import { describe, expect, it } from 'vitest'

import { isAllowedUrl, lockGlobalFetch, parseAllowHosts } from './network-lock'

describe('jarvis network lock', () => {
  it('allows this PC and the tailnet', () => {
    for (const url of ['http://127.0.0.1:48950/v1/chat/completions', 'http://localhost:5173/', 'ws://[::1]:6121/ws', 'http://100.125.91.51:48940/', 'https://100.64.0.1/', 'http://100.127.255.255/'])
      expect(isAllowedUrl(url, []), url).toBe(true)
  })

  it('refuses AIRI cloud, analytics, updates and everything else', () => {
    for (const url of ['https://api.airi.build/api/v1/auth', 'https://cloud.airi.build/', 'https://api.openpanel.dev/track', 'https://api.github.com/repos/moeru-ai/airi/releases', 'https://huggingface.co/x', 'http://192.168.1.10:8123/api/', 'http://100.128.0.1/', 'http://10.0.0.1/', 'http://127.0.0.1.evil.example/', 'not a url'])
      expect(isAllowedUrl(url, []), url).toBe(false)
  })

  it('lets the owner add named hosts', () => {
    const allow = parseAllowHosts(' huggingface.co, *.hf.co ,,')
    expect(allow).toEqual(['huggingface.co', '*.hf.co'])
    expect(isAllowedUrl('https://huggingface.co/model', allow)).toBe(true)
    expect(isAllowedUrl('https://cdn-lfs.hf.co/file', allow)).toBe(true)
    expect(isAllowedUrl('https://hf.co.evil.example/', allow)).toBe(false)
    expect(isAllowedUrl('https://evilhuggingface.co/', allow)).toBe(false)
  })

  it('treats local schemes as local', () => {
    for (const url of ['file:///C:/x.png', 'data:text/plain,hi', 'blob:abc', 'devtools://devtools/x', 'airi-sherpaw://assets/x'])
      expect(isAllowedUrl(url, []), url).toBe(true)
  })

  it('makes the main-process fetch refuse outside hosts', async () => {
    const seen: string[] = []
    const original = globalThis.fetch
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      seen.push(String(input))
      return new Response('ok')
    }) as typeof fetch
    try {
      lockGlobalFetch()
      await expect(fetch('https://api.airi.build/x')).rejects.toThrow('network lock')
      await expect(fetch(new URL('https://api.openpanel.dev/track'))).rejects.toThrow('network lock')
      expect(await (await fetch('http://127.0.0.1:48950/v1/models')).text()).toBe('ok')
      expect(seen).toEqual(['http://127.0.0.1:48950/v1/models'])
    }
    finally {
      globalThis.fetch = original
    }
  })
})
