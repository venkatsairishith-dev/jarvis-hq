import { afterEach, describe, expect, it, vi } from 'vitest'

import { isOwnerSpeech, offerOwnerSpeech, onSecretSpeech, ownerSpeech, setVoiceCommandSink } from './voice-command-sink'
import { speechRefusal } from './voice-secret-guard'

afterEach(() => {
  setVoiceCommandSink(null)
})

describe('the voice command sink (D63)', () => {
  it('goes on to the chat when no panel is registered', async () => {
    expect(await offerOwnerSpeech('tick temp files', 'wake-word')).toBe(false)
  })

  it('hands the words, and how the input began, to the registered panel; its answer decides', async () => {
    const seen: Array<[string, string]> = []
    setVoiceCommandSink((s) => {
      seen.push([s.text, s.cause])
      return s.text.startsWith('tick')
    })
    expect(await offerOwnerSpeech('tick temp files', 'control')).toBe(true)
    expect(await offerOwnerSpeech('what is the weather', 'wake-word')).toBe(false)
    expect(seen).toEqual([['tick temp files', 'control'], ['what is the weather', 'wake-word']])
  })

  it('an input that the sink does not answer in time goes to the chat, and the late answer can no longer act', async () => {
    let captured: ReturnType<typeof ownerSpeech> | undefined
    setVoiceCommandSink((s) => {
      captured = s
      return new Promise<boolean>(() => {})
    })
    expect(await offerOwnerSpeech('tick temp files', 'control', 20)).toBe(false)
    expect(captured?.live).toBe(false)
  })

  it('a sink that throws never breaks the voice pipeline', async () => {
    setVoiceCommandSink(() => {
      throw new Error('boom')
    })
    expect(await offerOwnerSpeech('next', 'control')).toBe(false)
  })

  it('only the voice pipeline makes an OwnerSpeech: a look-alike object is not one', () => {
    expect(isOwnerSpeech(ownerSpeech('submit', 'control'))).toBe(true)
    expect(isOwnerSpeech({ text: 'submit', cause: 'control', at: 0, live: true })).toBe(false)
    expect(isOwnerSpeech(JSON.parse(JSON.stringify(ownerSpeech('submit', 'control'))))).toBe(false)
    expect(isOwnerSpeech(null)).toBe(false)
    expect(isOwnerSpeech('submit')).toBe(false)
  })
})

describe('a spoken PIN is dropped before anything sees it', () => {
  it('claims it, tells the listener, and never calls the sink or the chat', async () => {
    const sink = vi.fn(() => false)
    setVoiceCommandSink(sink)
    const told = vi.fn()
    const off = onSecretSpeech(told)
    expect(await offerOwnerSpeech('my pin is 4821', 'wake-word')).toBe(true)
    expect(await offerOwnerSpeech('पिन 4821', 'control')).toBe(true)
    expect(sink).not.toHaveBeenCalled()
    expect(told).toHaveBeenCalledTimes(2)
    off()
  })

  it('does not touch ordinary speech, a postal pin code, or approvals in chat', async () => {
    expect(await offerOwnerSpeech('send it to pin code 500081', 'wake-word')).toBe(false)
    expect(await offerOwnerSpeech('what is the pin for', 'wake-word')).toBe(false)
    expect(await offerOwnerSpeech('approve the pending task', 'wake-word')).toBe(false)
  })

  it('knows approvals and secrets in English, Telugu and Hindi', () => {
    expect(speechRefusal('approve it')).toBe('approval')
    expect(speechRefusal('authorise this')).toBe('approval')
    expect(speechRefusal('password is hunter2')).toBe('secret')
    expect(speechRefusal('పిన్ 4821')).toBe('secret')
    expect(speechRefusal('tick temp files')).toBeNull()
  })
})
