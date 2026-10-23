import { JARVIS_ENDPOINTS, JARVIS_SPEECH_MODEL, JARVIS_SPEECH_VOICE } from '@proj-airi/stage-shared/jarvis'

import { useProviderConfigStore } from '../providers/config'
import { useProviderStore } from '../providers/provider'
import { useConsciousnessStore } from './consciousness'
import { useHearingStore } from './hearing'
import { useSpeechStore } from './speech'

/**
 * The providers the face starts with. Each one is a JARVIS service on this PC.
 *
 * Chat uses the privacy gateway, which forwards the caller's own provider key; the owner types that key once in the
 * provider settings (it is stored by the face on this PC, never in the repository). Speech needs no key.
 */
export const jarvisProviderDefaults = [
  { id: 'openai-compatible', config: { baseUrl: JARVIS_ENDPOINTS.chat, apiKey: '' } },
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

  if (!consciousnessStore.activeProvider) {
    // The model stays empty: the owner picks one the gateway's provider offers.
    consciousnessStore.activeProvider = 'openai-compatible'
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
