import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useAuthStore } from '../stores/auth'
import { authedFetch } from './auth-fetch'

describe('authedFetch', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    setActivePinia(createPinia())
    useAuthStore().token = 'access-token'
  })

  // JARVIS: AIRI attached an analytics device id and session id to API requests. JARVIS has no analytics, so none is sent.
  it.each([
    'https://api.airi.build/api/v1/stripe/checkout',
    'https://third-party.example.test/resource',
    'http://127.0.0.1:1/api/v1/anything',
  ])('sends the access token and no analytics identifier to %s', async (url) => {
    const fetchMock = vi.fn<(input: RequestInfo | URL, init?: RequestInit) => Promise<Response>>(async () => new Response('{}', { status: 200 }))
    vi.stubGlobal('fetch', fetchMock)

    await authedFetch(url, { method: 'POST' })

    const headers = fetchMock.mock.calls[0]?.[1]?.headers
    expect(headers).toBeInstanceOf(Headers)
    expect((headers as Headers).get('Authorization')).toBe('Bearer access-token')
    expect([...(headers as Headers).keys()].filter(name => /openpanel|analytics|distinct|device-id|session-id/i.test(name))).toEqual([])
  })
})
