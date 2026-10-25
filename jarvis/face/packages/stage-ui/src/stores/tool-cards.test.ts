import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useToolCardsStore } from './tool-cards'

vi.mock('vue-i18n', async importOriginal => ({
  ...await importOriginal<typeof import('vue-i18n')>(),
  useI18n: () => ({ locale: { value: 'en' }, t: (key: string) => key, te: () => true }),
}))

const call = {
  callId: 'call-1',
  key: 'jarvis::desktop__check_pc',
  toolName: 'builtIn_mcpCallTool',
  args: '{"name":"jarvis::desktop__check_pc","arguments":"{}"}',
  data: { disk: 'C: 40% free' },
  locale: 'en',
}

// JARVIS: a card is a second model request of about 8 KB on the chat model. On 2026-10-25 it ran beside every turn that
// called a new tool and took the rate limit of a free provider, so a card is never made for a call that just finished.
describe('tool cards', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.spyOn(console, 'warn').mockImplementation(() => {})
  })

  it('are off by default, and so are reply cards', () => {
    const cards = useToolCardsStore()

    expect(cards.enabled).toBe(false)
    expect(cards.replyCardsEnabled).toBe(false)
  })

  it('make no request for a call that finished while the reply streams', () => {
    const cards = useToolCardsStore()
    cards.enabled = true

    cards.want(call, { fresh: true, open: false })

    expect(cards.viewOf(call)).toEqual({ status: 'none' })
  })

  it('make a request when the owner unfolds the row of the call', () => {
    const cards = useToolCardsStore()
    cards.enabled = true

    cards.want(call, { fresh: false, open: true })

    expect(cards.viewOf(call).status).not.toBe('none')
  })

  it('make no request at all while the owner has them off', () => {
    const cards = useToolCardsStore()

    cards.want(call, { fresh: true, open: true })

    expect(cards.viewOf(call)).toEqual({ status: 'none' })
  })
})
