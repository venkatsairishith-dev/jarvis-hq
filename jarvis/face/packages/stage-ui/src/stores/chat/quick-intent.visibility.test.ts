import { describe, expect, it } from 'vitest'

import { mightBeQuickCommand, parseQuickResult, parseVisibilityCommand } from './quick-intent'

describe('parseVisibilityCommand (D78: hide yourself / show yourself)', () => {
  it.each([
    ['hide yourself', 'hide'],
    ['Hide yourself.', 'hide'],
    ['please hide yourself', 'hide'],
    ['hey Jarvis, hide yourself', 'hide'],
    ['go hidden yourself', 'hide'],
    ['Jarvis, hide', 'hide'],
    ['jarvis hide now', 'hide'],
    ['show yourself', 'show'],
    ['Show yourself!', 'show'],
    ['please show yourself', 'show'],
    ['ok jarvis show yourself', 'show'],
    ['unhide yourself', 'show'],
    ['Jarvis, come back', 'show'],
    ['jarvis show', 'show'],
  ] as const)('%s -> %s', (text, expected) => {
    expect(parseVisibilityCommand(text)).toBe(expected)
  })

  it.each([
    'hide',
    'show',
    'show me the weather',
    'hide the notepad window',
    'hide yourself and open notepad',
    'show yourself the file',
    'can you hide the taskbar',
    'show my files',
    'I will hide it',
    '',
    'hide yourself from the screen recording software',
    'hide from the proctor',
    'x'.repeat(100),
  ])('%s is not a visibility command', (text) => {
    expect(parseVisibilityCommand(text)).toBeNull()
  })

  it('handles missing input', () => {
    expect(parseVisibilityCommand(undefined)).toBeNull()
    expect(parseVisibilityCommand(null)).toBeNull()
  })

  it('goes through the quick-command door so no chat model is asked', () => {
    expect(mightBeQuickCommand('hide yourself')).toBe(true)
    expect(mightBeQuickCommand('show yourself')).toBe(true)
    expect(mightBeQuickCommand('tell me a story')).toBe(false)
  })
})

describe('a silent quick answer', () => {
  it('is handled with no reply', () => {
    expect(parseQuickResult({ structuredContent: { handled: true, silent: true } })).toEqual({ handled: true, silent: true })
  })
  it('a handled answer with no reply and no silent flag is still not handled', () => {
    expect(parseQuickResult({ structuredContent: { handled: true } })).toEqual({ handled: false })
    expect(parseQuickResult({ structuredContent: { handled: true, silent: 'yes' } })).toEqual({ handled: false })
  })
  it('a normal answer is unchanged', () => {
    expect(parseQuickResult({ structuredContent: { handled: true, reply: 'Opened Notepad.' } })).toMatchObject({ handled: true, reply: 'Opened Notepad.' })
  })
})
