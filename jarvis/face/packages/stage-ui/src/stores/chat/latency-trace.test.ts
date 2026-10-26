import type { TimingMark } from './latency-trace'

import { describe, expect, it } from 'vitest'

import { createLatencyTrace } from './latency-trace'

function setup(enabled = true) {
  let t = 1000
  const batches: TimingMark[][] = []
  const trace = createLatencyTrace({ now: () => t, enabled: () => enabled, sink: () => marks => void batches.push(marks) })
  const advance = (ms: number) => {
    t += ms
  }
  return { trace, advance, batches }
}

describe('the content-free timing trace of a chat turn', () => {
  it('records the phases of a tool turn in order with milliseconds since the message', () => {
    const { trace, advance, batches } = setup()
    trace.sent('turn-a')
    advance(40)
    trace.mark('turn-a', 'ack')
    advance(3000)
    trace.streamEvent('turn-a', { type: 'tool-call-start', toolCallId: 'c1', toolName: 'desktop__do_goal' })
    advance(200)
    trace.streamEvent('turn-a', { type: 'tool-call', toolCallId: 'c1', toolName: 'desktop__do_goal' })
    advance(5000)
    trace.streamEvent('turn-a', { type: 'tool-result', toolCallId: 'c1' })
    advance(2500)
    trace.streamEvent('turn-a', { type: 'text-delta', text: 'Opened ' })
    advance(300)
    trace.streamEvent('turn-a', { type: 'text-delta', text: 'Notepad.' })
    trace.finish('turn-a', 'finished')

    expect(batches).toHaveLength(1)
    expect(batches[0].map(m => [m.phase, m.ms, m.round, m.tool])).toEqual([
      ['sent', 0, undefined, undefined],
      ['ack', 40, undefined, undefined],
      ['model_first', 3040, 1, undefined],
      ['tool_start', 3240, 1, 'desktop__do_goal'],
      ['tool_end', 8240, 1, 'desktop__do_goal'],
      ['model_first', 10740, 2, undefined],
      ['first_token', 10740, 2, undefined],
      ['final_token', 11040, undefined, undefined],
      ['done', 11040, undefined, undefined],
    ])
  })

  it('holds only a phase word, numbers and a tool function name, never text', () => {
    const { trace, batches } = setup()
    trace.sent('turn-b')
    trace.streamEvent('turn-b', { type: 'text-delta', text: 'my password is hunter2' })
    trace.streamEvent('turn-b', { type: 'tool-call', toolCallId: 'c', toolName: 'jarvis_do' })
    trace.finish('turn-b', 'failed')
    const text = JSON.stringify(batches)
    expect(text).not.toContain('hunter2')
    expect(batches[0].at(-1)?.phase).toBe('error')
    for (const mark of batches[0])
      expect(Object.keys(mark).every(key => ['phase', 'ms', 'turn', 'tool', 'round'].includes(key))).toBe(true)
  })

  it('numbers the turns and keeps them apart', () => {
    const { trace, batches } = setup()
    trace.sent('a')
    trace.sent('b')
    trace.finish('b', 'finished')
    trace.finish('a', 'cancelled')
    expect(batches[0][0].turn).toBe(2)
    expect(batches[1][0].turn).toBe(1)
    expect(batches[1].at(-1)?.phase).toBe('error')
  })

  it('does nothing when it is off or has no writer, and a broken writer never reaches the turn', () => {
    const off = setup(false)
    off.trace.sent('x')
    off.trace.finish('x', 'finished')
    expect(off.batches).toEqual([])
    const trace = createLatencyTrace({ now: () => 0, sink: () => undefined })
    trace.sent('y')
    trace.finish('y', 'finished')
    const broken = () => {
      throw new Error('disk full')
    }
    const throwing = createLatencyTrace({ now: () => 0, sink: () => broken })
    throwing.sent('z')
    expect(() => throwing.finish('z', 'finished')).not.toThrow()
  })

  it('ignores events of a turn it never saw and forgets the oldest turns', () => {
    const { trace, batches } = setup()
    trace.streamEvent('unknown', { type: 'text-delta', text: 'x' })
    trace.finish('unknown', 'finished')
    expect(batches).toEqual([])
    for (let i = 0; i < 40; i++)
      trace.sent(`t${i}`)
    trace.finish('t0', 'finished')
    expect(batches).toEqual([])
    trace.finish('t39', 'finished')
    expect(batches).toHaveLength(1)
  })
})
