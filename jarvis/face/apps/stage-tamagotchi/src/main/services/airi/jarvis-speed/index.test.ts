import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { afterEach, describe, expect, it } from 'vitest'

import { formatTimingLine, latencyLogPath, parseSpeedEnv, writeTimingMarks } from './index'

const dirs: string[] = []
function tmp() {
  const dir = mkdtempSync(join(tmpdir(), 'jarvis-speed-'))
  dirs.push(dir)
  return dir
}
afterEach(() => {
  for (const dir of dirs.splice(0))
    rmSync(dir, { recursive: true, force: true })
})

describe('the speed switches from the environment', () => {
  it('are all on when nothing is set', () => {
    expect(parseSpeedEnv({})).toEqual({ directTools: true, quickAck: true, quickCommand: true, timing: true })
  })

  it('turn off with 0, false, no or off, and keep the default for anything unclear', () => {
    for (const value of ['0', 'false', 'No', 'OFF', ' 0 '])
      expect(parseSpeedEnv({ JARVIS_FACE_DIRECT_TOOLS: value }).directTools).toBe(false)
    for (const value of ['maybe', '', '2'])
      expect(parseSpeedEnv({ JARVIS_FACE_DIRECT_TOOLS: value }).directTools).toBe(true)
    expect(parseSpeedEnv({ JARVIS_FACE_QUICK_ACK: '0' }).quickAck).toBe(false)
    expect(parseSpeedEnv({ JARVIS_FACE_QUICK_COMMAND: 'off' }).quickCommand).toBe(false)
    expect(parseSpeedEnv({ JARVIS_LATENCY_LOG: 'off' }).timing).toBe(false)
  })
})

describe('where the timing lines go', () => {
  it('is the gateway\'s file in the data folder unless the environment says otherwise', () => {
    expect(latencyLogPath({}, 'win32', 'C:\\Users\\x')).toBe(join('D:\\jarvis\\data', 'logs', 'latency.jsonl'))
    expect(latencyLogPath({ JARVIS_DATA_DIR: '/data' }, 'linux', '/home/x')).toBe(join('/data', 'logs', 'latency.jsonl'))
    expect(latencyLogPath({}, 'linux', '/home/x')).toBe(join('/home/x', '.jarvis', 'data', 'logs', 'latency.jsonl'))
    expect(latencyLogPath({ JARVIS_LATENCY_LOG: '/tmp/x.jsonl' })).toBe('/tmp/x.jsonl')
    expect(latencyLogPath({ JARVIS_LATENCY_LOG: 'OFF' })).toBeUndefined()
  })
})

describe('a timing line', () => {
  it('holds a phase word, milliseconds, the turn, the round and a tool\'s function name', () => {
    const line = JSON.parse(formatTimingLine({ phase: 'tool_start', ms: 3240.4, turn: 3, round: 1, tool: 'desktop__do_goal' }, 1_700_000_000_000) as string)
    expect(line).toEqual({ t: 1_700_000_000, src: 'face', phase: 'tool_start', ms: 3240, turn: 3, round: 1, tool: 'desktop__do_goal' })
  })

  it('refuses a mark with text in it, a strange phase or a number out of range', () => {
    expect(formatTimingLine({ phase: 'first token', ms: 1, turn: 1 })).toBeNull()
    expect(formatTimingLine({ phase: 'C:\\secret\\file.txt', ms: 1, turn: 1 })).toBeNull()
    expect(formatTimingLine({ phase: 'done', ms: -1, turn: 1 })).toBeNull()
    expect(formatTimingLine({ phase: 'done', ms: Number.NaN, turn: 1 })).toBeNull()
    expect(formatTimingLine({ phase: 'done', ms: 4_000_000, turn: 1 })).toBeNull()
    expect(formatTimingLine(null)).toBeNull()
    expect(formatTimingLine('done')).toBeNull()
  })

  it('drops any extra field and any tool name that is not a function name', () => {
    const line = JSON.parse(formatTimingLine({ phase: 'done', ms: 5, turn: 1, text: 'my password is hunter2', tool: 'has a space and secrets' }) as string)
    expect(Object.keys(line).sort()).toEqual(['ms', 'phase', 'src', 't', 'turn'])
  })
})

describe('writing the marks', () => {
  it('appends one line for each valid mark and skips the others', () => {
    const path = join(tmp(), 'logs', 'latency.jsonl')
    const written = writeTimingMarks([{ phase: 'sent', ms: 0, turn: 1 }, { phase: 'bad phase', ms: 1, turn: 1 }, { phase: 'done', ms: 900, turn: 1 }], path)
    expect(written).toBe(2)
    writeTimingMarks([{ phase: 'sent', ms: 0, turn: 2 }], path)
    const lines = readFileSync(path, 'utf8').trim().split('\n').map(line => JSON.parse(line))
    expect(lines.map(line => [line.phase, line.turn])).toEqual([['sent', 1], ['done', 1], ['sent', 2]])
    expect(lines.every(line => line.src === 'face')).toBe(true)
  })

  it('does nothing when switched off, for junk, or when the file cannot be made', () => {
    expect(writeTimingMarks([{ phase: 'sent', ms: 0, turn: 1 }], undefined)).toBe(0)
    expect(writeTimingMarks('not a list', join(tmp(), 'x.jsonl'))).toBe(0)
    const blocker = join(tmp(), 'file')
    writeFileSync(blocker, 'x')
    expect(writeTimingMarks([{ phase: 'sent', ms: 0, turn: 1 }], join(blocker, 'sub', 'x.jsonl'))).toBe(0)
  })

  it('starts a new file when the old one is big', () => {
    const path = join(tmp(), 'latency.jsonl')
    writeFileSync(path, 'x'.repeat(5 * 1024 * 1024 + 10))
    writeTimingMarks([{ phase: 'sent', ms: 0, turn: 1 }], path)
    expect(readFileSync(`${path}.1`, 'utf8').length).toBeGreaterThan(5 * 1024 * 1024)
    expect(readFileSync(path, 'utf8').trim().split('\n')).toHaveLength(1)
  })
})
