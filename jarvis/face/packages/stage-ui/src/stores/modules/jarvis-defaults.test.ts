import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { useProviderConfigStore } from '../providers/config'
import { useConsciousnessStore } from './consciousness'
import { useHearingStore } from './hearing'
import { configureJarvisDefaultsIfEmpty } from './jarvis-defaults'
import { useSpeechStore } from './speech'

vi.mock('../../composables/use-analytics', () => ({
  useAnalytics: () => ({
    trackAudioDeviceUnavailable: vi.fn(),
    trackMicrophonePermissionDenied: vi.fn(),
    trackSttFailed: vi.fn(),
    trackSttStarted: vi.fn(),
    trackSttSucceeded: vi.fn(),
    trackVoiceInputStarted: vi.fn(),
  }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: { value: 'en-US' },
    t: (_key: string, fallback?: string) => fallback ?? _key,
  }),
}))

describe('jarvis provider defaults', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    // Nothing may be fetched while the defaults are applied.
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new Error('no network in this test')
    }))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('points empty chat, hearing and speech at the JARVIS services on this PC', async () => {
    await expect(configureJarvisDefaultsIfEmpty()).resolves.toBe(true)

    const providers = useProviderConfigStore().providers
    expect(providers['openai-compatible']?.config).toMatchObject({ baseUrl: 'http://127.0.0.1:48950/v1/' })
    expect(providers['openai-compatible']?.status).toBe('configured')
    expect(providers['openai-compatible-audio-speech']?.config).toMatchObject({ baseUrl: 'http://127.0.0.1:48930/v1/' })
    expect(providers['openai-compatible-audio-transcription']?.config).toMatchObject({ baseUrl: 'http://127.0.0.1:48930/v1/' })

    expect(useConsciousnessStore().activeProvider).toBe('openai-compatible')
    expect(useHearingStore().activeTranscriptionProvider).toBe('openai-compatible-audio-transcription')
    expect(useHearingStore().activeTranscriptionModel).toBe('jarvis-local')
    expect(useSpeechStore().activeSpeechProvider).toBe('openai-compatible-audio-speech')
    expect(useSpeechStore().activeSpeechVoiceId).toBe('auto')
    expect(Object.keys(providers).some(id => id.includes('official'))).toBe(false)
  })

  it('never changes what the owner already set up', async () => {
    const providerConfigStore = useProviderConfigStore()
    await providerConfigStore.ensureProvider('openai-compatible', 'openai-compatible', { baseUrl: 'http://127.0.0.1:48952/v1/', apiKey: 'owner' })
    useConsciousnessStore().activeProvider = 'ollama'
    useSpeechStore().activeSpeechProvider = 'kokoro-local'

    await configureJarvisDefaultsIfEmpty()

    expect(providerConfigStore.providers['openai-compatible']?.config).toEqual({ baseUrl: 'http://127.0.0.1:48952/v1/', apiKey: 'owner' })
    expect(useConsciousnessStore().activeProvider).toBe('ollama')
    expect(useSpeechStore().activeSpeechProvider).toBe('kokoro-local')
    await expect(configureJarvisDefaultsIfEmpty()).resolves.toBe(false)
  })

  it('sends the placeholder key and the default chat model, so the owner types neither', async () => {
    await configureJarvisDefaultsIfEmpty()
    expect(useProviderConfigStore().providers['openai-compatible']?.config).toMatchObject({ apiKey: 'jarvis-local' })
    expect(useConsciousnessStore().activeModel).toBe('zai-org/GLM-5.3-Flash')
  })

  it('repairs a profile from an earlier build once: empty key, and chat pointed at a cloud provider JARVIS does not have', async () => {
    const providerConfigStore = useProviderConfigStore()
    await providerConfigStore.ensureProvider('openai-compatible', 'openai-compatible', { baseUrl: 'http://127.0.0.1:48950/v1/', apiKey: '' })
    useConsciousnessStore().activeProvider = 'official-provider'

    await expect(configureJarvisDefaultsIfEmpty()).resolves.toBe(true)

    expect(providerConfigStore.providers['openai-compatible']?.config).toMatchObject({ apiKey: 'jarvis-local' })
    expect(useConsciousnessStore().activeProvider).toBe('openai-compatible')
    expect(useConsciousnessStore().activeModel).toBe('zai-org/GLM-5.3-Flash')
    await expect(configureJarvisDefaultsIfEmpty()).resolves.toBe(false)
  })

  it('moves a profile that still holds the old default chat model to the new one, and leaves another choice alone', async () => {
    const providerConfigStore = useProviderConfigStore()
    await providerConfigStore.ensureProvider('openai-compatible', 'openai-compatible', { baseUrl: 'http://127.0.0.1:48950/v1/', apiKey: 'jarvis-local' })
    useConsciousnessStore().activeProvider = 'openai-compatible'
    useConsciousnessStore().activeModel = 'MiniMaxAI/MiniMax-M2.7'
    await configureJarvisDefaultsIfEmpty()
    expect(useConsciousnessStore().activeModel).toBe('zai-org/GLM-5.3-Flash')

    useConsciousnessStore().activeModel = 'deepseek-ai/DeepSeek-V4-Flash-0731'
    await configureJarvisDefaultsIfEmpty()
    expect(useConsciousnessStore().activeModel).toBe('deepseek-ai/DeepSeek-V4-Flash-0731')
  })
})
