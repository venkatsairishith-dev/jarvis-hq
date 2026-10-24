import { describe, expect, it } from 'vitest'

import { JARVIS_WAKE_WORDS } from './jarvis-wake-words'
import { kwsVocabularyModel } from './kws-model'
import { validateWakeWords } from './wake-words'

describe('jARVIS wake words', () => {
  it('are "Hey JARVIS" and "JARVIS", spelled with tokens of the pinned keyword pack', () => {
    const words = validateWakeWords(JARVIS_WAKE_WORDS, kwsVocabularyModel)
    expect(words.map(word => word.text)).toEqual(['Hey JARVIS', 'JARVIS'])
    expect(words.every(word => word.pronunciations.length >= 1)).toBe(true)
  })

  it('never share a pronunciation, so neither is paused as a conflict', () => {
    const keys = JARVIS_WAKE_WORDS.flatMap(word => word.pronunciations.map(tokens => tokens.join(' ')))
    expect(new Set(keys).size).toBe(keys.length)
  })
})
