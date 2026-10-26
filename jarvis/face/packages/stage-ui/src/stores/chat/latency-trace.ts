/**
 * JARVIS D60 (speed): the face's end-to-end timing trace of one chat turn, content-free.
 *
 * It records only a phase word, milliseconds since the owner's message reached the chat store, a small turn counter, the model round and a tool's
 * function name. Never text, never arguments or results. The marks go to the main process (one eventa call, batched at the end of the turn) which
 * appends them to `<data dir>/logs/latency.jsonl` beside the gateway's own lines; `jarvis/tools/latency_report.py` prints p50/p90 per phase.
 *
 * Phases:
 *   sent         the message reached the chat store (time 0)
 *   ack          the HUD showed "On it" (D60 instant acknowledgement)
 *   quick_start  the rules-first router was asked (quick_command)        quick_end   it answered (handled or not)
 *   model_first  the first thing of a model round arrived (round = 1, 2, ...)
 *   first_token  the first word of the reply for the owner
 *   tool_start   a tool call is complete and starts to run (tool = its function name)     tool_end   its result is back
 *   final_token  the last word of the reply
 *   done         the turn ended (finished)        error   it failed or was cancelled
 */

export interface TimingMark {
  phase: string
  ms: number
  turn: number
  tool?: string
  round?: number
}

export type TimingSink = (marks: TimingMark[]) => void

interface StreamEventLike {
  type?: string
  text?: string
  toolCallId?: string
  toolName?: string
}

interface TurnState {
  n: number
  t0: number
  round: number
  roundSeen: boolean
  first: boolean
  lastText: number
  tools: Map<string, string>
  marks: TimingMark[]
}

const MAX_TURNS = 16

let globalSink: TimingSink | undefined

/** The main-process writer, registered once by the app (the stage window). Without one, nothing is recorded. */
export function setTimingSink(sink: TimingSink | undefined) {
  globalSink = sink
}

export function createLatencyTrace(options: { now?: () => number, enabled?: () => boolean, sink?: () => TimingSink | undefined } = {}) {
  const now = options.now ?? (() => globalThis.performance?.now?.() ?? Date.now())
  const enabled = options.enabled ?? (() => true)
  const sinkOf = options.sink ?? (() => globalSink)
  const turns = new Map<string, TurnState>()
  let counter = 0

  function on() {
    return enabled() && sinkOf() !== undefined
  }

  function add(state: TurnState, phase: string, extra: Partial<Pick<TimingMark, 'tool' | 'round'>> = {}) {
    state.marks.push({ phase, ms: Math.max(0, Math.round(now() - state.t0)), turn: state.n, ...extra })
  }

  function sent(turnId: string) {
    if (!on() || turns.has(turnId))
      return
    if (turns.size >= MAX_TURNS)
      turns.delete(turns.keys().next().value as string)
    const state: TurnState = { n: ++counter, t0: now(), round: 1, roundSeen: false, first: false, lastText: -1, tools: new Map(), marks: [] }
    turns.set(turnId, state)
    add(state, 'sent')
  }

  function mark(turnId: string, phase: string, extra: Partial<Pick<TimingMark, 'tool' | 'round'>> = {}) {
    const state = turns.get(turnId)
    if (state)
      add(state, phase, extra)
  }

  function streamEvent(turnId: string, event: StreamEventLike) {
    const state = turns.get(turnId)
    if (!state || !event?.type)
      return
    const visible = event.type === 'text-delta' || event.type === 'tool-call-start'
    if (visible && !state.roundSeen) {
      state.roundSeen = true
      add(state, 'model_first', { round: state.round })
    }
    switch (event.type) {
      case 'text-delta':
        if (!state.first) {
          state.first = true
          add(state, 'first_token', { round: state.round })
        }
        state.lastText = Math.max(0, Math.round(now() - state.t0))
        break
      case 'tool-call':
        if (event.toolCallId && event.toolName)
          state.tools.set(event.toolCallId, event.toolName)
        add(state, 'tool_start', { tool: event.toolName, round: state.round })
        break
      case 'tool-result':
      case 'tool-error': {
        add(state, 'tool_end', { tool: event.toolCallId ? state.tools.get(event.toolCallId) : undefined, round: state.round })
        state.round += 1
        state.roundSeen = false
        break
      }
    }
  }

  /** Ends the turn: the last word, the outcome, and one batch to the sink. */
  function finish(turnId: string, outcome: 'finished' | 'cancelled' | 'failed') {
    const state = turns.get(turnId)
    if (!state)
      return
    turns.delete(turnId)
    if (state.lastText >= 0)
      state.marks.push({ phase: 'final_token', ms: state.lastText, turn: state.n })
    add(state, outcome === 'finished' ? 'done' : 'error')
    try {
      sinkOf()?.(state.marks)
    }
    catch {
      // a broken writer must never touch a chat turn
    }
  }

  return { sent, mark, streamEvent, finish }
}

export type LatencyTrace = ReturnType<typeof createLatencyTrace>
