import type { createContext } from '@moeru/eventa/adapters/electron/main'

import { appendFileSync, mkdirSync, renameSync, statSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import { platform, env as processEnv } from 'node:process'

import { defineInvokeHandler } from '@moeru/eventa'

import { jarvisSpeedEnv, jarvisTimingMarks } from '../../../../shared/eventa'

/**
 * JARVIS D60 (speed): the switches the owner or the laptop session can set with environment variables, and the content-free timing trace.
 *
 * Environment (read once per call from the main process; a setting in Settings overrides it, see stage-ui `jarvis-speed`):
 * - `JARVIS_FACE_DIRECT_TOOLS`   1 (default) | 0: the chat model gets the real JARVIS tools in its first request, not the list-then-call proxies
 * - `JARVIS_FACE_QUICK_ACK`      1 (default) | 0: an instant "On it" state and line for a PC task, before any model answers
 * - `JARVIS_FACE_QUICK_COMMAND`  1 (default) | 0: a plain command ("open notepad") is run by the rules at once, with no chat model request
 * - `JARVIS_LATENCY_LOG`         a file path, or `off`: where the timing lines go (default: <data dir>/logs/latency.jsonl, the gateway's file)
 */

export interface SpeedEnv {
  directTools: boolean
  quickAck: boolean
  quickCommand: boolean
  timing: boolean
}

type EnvLike = Record<string, string | undefined>

function flag(value: string | undefined, fallback: boolean): boolean {
  const v = value?.trim().toLowerCase()
  if (v === '0' || v === 'false' || v === 'no' || v === 'off')
    return false
  if (v === '1' || v === 'true' || v === 'yes' || v === 'on')
    return true
  return fallback
}

/** The defaults the environment gives. A value that is not a clear yes or no keeps the default. */
export function parseSpeedEnv(env: EnvLike = processEnv): SpeedEnv {
  const log = env.JARVIS_LATENCY_LOG?.trim().toLowerCase()
  return {
    directTools: flag(env.JARVIS_FACE_DIRECT_TOOLS, true),
    quickAck: flag(env.JARVIS_FACE_QUICK_ACK, true),
    quickCommand: flag(env.JARVIS_FACE_QUICK_COMMAND, true),
    timing: log !== 'off' && log !== '0' && log !== 'no' && log !== 'false',
  }
}

/** Where the timing lines go: the same file as the gateway's (`src` tells them apart), or `undefined` when switched off. */
export function latencyLogPath(env: EnvLike = processEnv, os: string = platform, home: string = homedir()): string | undefined {
  const raw = env.JARVIS_LATENCY_LOG?.trim()
  if (raw && ['off', '0', 'no', 'false'].includes(raw.toLowerCase()))
    return undefined
  if (raw)
    return raw
  const base = env.JARVIS_DATA_DIR?.trim() || (os === 'win32' ? 'D:\\jarvis\\data' : join(home, '.jarvis', 'data'))
  return join(base, 'logs', 'latency.jsonl')
}

export interface TimingMark {
  /** A short word: sent, ack, quick_start, quick_end, first_token, tool_start, tool_end, final_token, done, error. */
  phase: string
  /** Milliseconds since the owner's message was sent. */
  ms: number
  /** The turn in this window, a small counter. */
  turn: number
  /** A tool's function name for tool_start and tool_end. */
  tool?: string
  /** Which round of the turn (1 = the first model request). */
  round?: number
}

const PHASE = /^[a-z_]{1,24}$/
const TOOL = /^[\w-]{1,64}$/

/** One JSON line for a mark, or `null` when the mark holds anything but a phase word, small numbers and a tool's name. No text ever passes. */
export function formatTimingLine(mark: unknown, now: number = Date.now()): string | null {
  if (mark === null || typeof mark !== 'object')
    return null
  const m = mark as Record<string, unknown>
  if (typeof m.phase !== 'string' || !PHASE.test(m.phase))
    return null
  if (typeof m.ms !== 'number' || !Number.isFinite(m.ms) || m.ms < 0 || m.ms > 3_600_000)
    return null
  const line: Record<string, unknown> = { t: Math.round(now) / 1000, src: 'face', phase: m.phase, ms: Math.round(m.ms) }
  if (typeof m.turn === 'number' && Number.isInteger(m.turn) && m.turn >= 0 && m.turn < 1_000_000)
    line.turn = m.turn
  if (typeof m.round === 'number' && Number.isInteger(m.round) && m.round >= 0 && m.round < 100)
    line.round = m.round
  if (typeof m.tool === 'string' && TOOL.test(m.tool))
    line.tool = m.tool
  return JSON.stringify(line)
}

const MAX_LOG_BYTES = 5 * 1024 * 1024

/** Appends the lines of some marks to the log file. Never throws: a full disk must not touch a chat turn. */
export function writeTimingMarks(marks: unknown, path: string | undefined, now: () => number = Date.now): number {
  if (!path || !Array.isArray(marks))
    return 0
  const lines = marks.slice(0, 64).map(mark => formatTimingLine(mark, now())).filter((line): line is string => line !== null)
  if (lines.length === 0)
    return 0
  try {
    mkdirSync(dirname(path), { recursive: true })
    try {
      if (statSync(path).size > MAX_LOG_BYTES)
        renameSync(path, `${path}.1`)
    }
    catch {}
    appendFileSync(path, `${lines.join('\n')}\n`, 'utf8')
    return lines.length
  }
  catch {
    return 0
  }
}

export function createJarvisSpeedService(params: { context: ReturnType<typeof createContext>['context'], env?: EnvLike }) {
  const env = params.env ?? processEnv
  defineInvokeHandler(params.context, jarvisSpeedEnv, () => parseSpeedEnv(env))
  defineInvokeHandler(params.context, jarvisTimingMarks, (marks) => {
    writeTimingMarks(marks, latencyLogPath(env))
  })
}
