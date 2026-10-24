import { JARVIS_CHAT_MODEL, JARVIS_ENDPOINTS, JARVIS_LOCAL_KEY, JARVIS_SPEECH_MODEL, JARVIS_SPEECH_VOICE } from '@proj-airi/stage-shared/jarvis'

import { useProviderConfigStore } from '../providers/config'
import { useProviderStore } from '../providers/provider'
import { useConsciousnessStore } from './consciousness'
import { useHearingStore } from './hearing'
import { useSpeechStore } from './speech'

/**
 * The providers the face starts with. Each one is a JARVIS service on this PC.
 *
 * Chat uses the privacy gateway. The face sends the placeholder key `jarvis-local` and the gateway adds the provider key from
 * its own key file, so the owner types no key. Speech needs no key.
 */
export const jarvisProviderDefaults = [
  { id: 'openai-compatible', config: { baseUrl: JARVIS_ENDPOINTS.chat, apiKey: JARVIS_LOCAL_KEY } },
  { id: 'openai-compatible-audio-speech', config: { baseUrl: JARVIS_ENDPOINTS.speech, apiKey: '' } },
  { id: 'openai-compatible-audio-transcription', config: { baseUrl: JARVIS_ENDPOINTS.speech, apiKey: '' } },
] as const

/**
 * Points empty chat, hearing and speech selections at JARVIS's own services.
 *
 * A provider or module the owner already set up is never changed. The caller must own synchronized Pinia leadership.
 *
 * @returns `true` when the command changes module or provider state.
 */
export async function configureJarvisDefaultsIfEmpty(): Promise<boolean> {
  const providerConfigStore = useProviderConfigStore()
  const providerStore = useProviderStore()
  const consciousnessStore = useConsciousnessStore()
  const hearingStore = useHearingStore()
  const speechStore = useSpeechStore()
  let changed = false

  for (const { id, config } of jarvisProviderDefaults) {
    if (providerConfigStore.getProvider(id))
      continue
    await providerConfigStore.ensureProvider(id, id, { ...config })
    await providerStore.initializeProvider(id)
    await providerStore.forceProviderConfigured(id)
    changed = true
  }

  // A profile from an earlier build: the chat provider has no key, or chat points at a cloud provider that JARVIS does not
  // have (it can only fail with "Failed to fetch"). Repair it once; a provider the owner chose themselves is left alone.
  const chatConfig = providerConfigStore.getProviderConfig('openai-compatible')
  if (chatConfig && chatConfig.baseUrl === JARVIS_ENDPOINTS.chat && !chatConfig.apiKey) {
    await providerConfigStore.updateProviderConfig('openai-compatible', { ...chatConfig, apiKey: JARVIS_LOCAL_KEY }, 'configured')
    changed = true
  }
  const active = consciousnessStore.activeProvider
  if (!active || active.startsWith('official-provider')) {
    consciousnessStore.activeProvider = 'openai-compatible'
    consciousnessStore.activeModel = JARVIS_CHAT_MODEL
    changed = true
  }
  else if (active === 'openai-compatible' && !consciousnessStore.activeModel) {
    consciousnessStore.activeModel = JARVIS_CHAT_MODEL
    changed = true
  }

  if (!hearingStore.activeTranscriptionProvider) {
    hearingStore.activeCustomModelName = ''
    hearingStore.activeTranscriptionProvider = 'openai-compatible-audio-transcription'
    hearingStore.activeTranscriptionModel = JARVIS_SPEECH_MODEL
    changed = true
  }

  if (!speechStore.activeSpeechProvider || speechStore.activeSpeechProvider === 'speech-noop') {
    speechStore.activeSpeechProvider = 'openai-compatible-audio-speech'
    speechStore.activeSpeechModel = JARVIS_SPEECH_MODEL
    speechStore.activeSpeechVoiceId = JARVIS_SPEECH_VOICE
    changed = true
  }

  return changed
}
