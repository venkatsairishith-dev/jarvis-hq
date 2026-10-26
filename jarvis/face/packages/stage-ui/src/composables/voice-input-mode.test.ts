import { afterEach, describe, expect, it, vi } from 'vitest'
import { effectScope, nextTick, reactive } from 'vue'

import { useVoiceListening } from './voice-input-mode'

const voice = vi.hoisted(() => ({
  startListening: vi.fn(),
  stopListening: vi.fn(async () => undefined),
}))
const devices = vi.hoisted(() => ({ state: { enabled: false } }))
const hearing = vi.hoisted(() => ({ state: { inputMode: 'wake-word' } }))

vi.mock('../stores/voice', () => ({ useVoiceStore: () => voice }))
vi.mock('../stores/settings/audio-device', () => ({ useSettingsAudioDevice: () => devices.state }))
vi.mock('../stores/modules/hearing', () => ({
  listensContinuously: (mode: string) => mode !== 'push-to-talk',
  useHearingStore: () => hearing.state,
}))
vi.mock('../stores/chat/session-store', () => ({ useChatSessionStore: () => ({}) }))
vi.mock('./voice-hold', () => ({ useVoiceHold: vi.fn() }))

afterEach(() => {
  vi.useRealTimers()
  voice.startListening.mockClear()
  voice.stopListening.mockClear()
})

function setup(enabled: boolean, options?: Parameters<typeof useVoiceListening>[0]) {
  vi.useFakeTimers()
  vi.setSystemTime(1_000_000)
  devices.state = reactive({ enabled })
  const scope = effectScope()
  scope.run(() => useVoiceListening(options))
  return scope
}

describe('useVoiceListening start-up delay (D57)', () => {
  it('starts at once when no delay is asked for', () => {
    const scope = setup(true)
    expect(voice.startListening).toHaveBeenCalledOnce()
    scope.stop()
  })

  it('holds back a microphone that was left on until the delay has passed', async () => {
    const scope = setup(true, { startDelayMs: 8000 })
    expect(voice.startListening).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(7999)
    expect(voice.startListening).not.toHaveBeenCalled()
    await vi.advanceTimersByTimeAsync(2)
    expect(voice.startListening).toHaveBeenCalledOnce()
    scope.stop()
  })

  it('does not start when the microphone is turned off during the delay', async () => {
    const scope = setup(true, { startDelayMs: 8000 })
    devices.state.enabled = false
    await nextTick()
    await vi.advanceTimersByTimeAsync(9000)
    expect(voice.startListening).not.toHaveBeenCalled()
    scope.stop()
  })

  it('does not start when the window closes during the delay', async () => {
    const scope = setup(true, { startDelayMs: 8000 })
    scope.stop()
    await vi.advanceTimersByTimeAsync(9000)
    expect(voice.startListening).not.toHaveBeenCalled()
  })

  it('starts at once for a microphone the owner turns on after the delay', async () => {
    const scope = setup(false, { startDelayMs: 8000 })
    await vi.advanceTimersByTimeAsync(9000)
    devices.state.enabled = true
    await nextTick()
    expect(voice.startListening).toHaveBeenCalledOnce()
    scope.stop()
  })
})
