import { describe, expect, it } from 'vitest'

import { reasoningToShow, SHOW_MODEL_REASONING } from './reasoning-visibility'

const message = { categorization: { speech: 'Hello', reasoning: '  The user said hi. Provide a short greeting.  ' } } as never

describe('reasoning visibility', () => {
  it('shows no reasoning text in JARVIS', () => {
    expect(SHOW_MODEL_REASONING).toBe(false)
    expect(reasoningToShow(message)).toBe('')
  })

  it('still returns the trimmed text when the switch is turned on', () => {
    expect(reasoningToShow(message, true)).toBe('The user said hi. Provide a short greeting.')
  })

  it('copes with a message that has no categorization', () => {
    expect(reasoningToShow({} as never, true)).toBe('')
  })
})
