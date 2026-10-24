import { describe, expect, it } from 'vitest'

import { isCloudOnlyProviderId, JARVIS_ENDPOINTS } from './jarvis'

describe('isCloudOnlyProviderId', () => {
  it('hides the cloud account providers for chat, speech and vision only', () => {
    for (const id of ['official-provider', 'official-provider-speech', 'official-provider-speech-streaming', 'vision-official-provider'])
      expect(isCloudOnlyProviderId(id)).toBe(true)
    for (const id of ['openai', 'ollama', 'kokoro-local', 'speech-noop', 'my-official-provider-clone'])
      expect(isCloudOnlyProviderId(id)).toBe(false)
  })
})

describe('jARVIS_ENDPOINTS', () => {
  it('points only at this PC', () => {
    for (const url of Object.values(JARVIS_ENDPOINTS))
      expect(new URL(url).hostname).toBe('127.0.0.1')
  })
})
