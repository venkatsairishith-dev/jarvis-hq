/**
 * The JARVIS HUD, as pure functions: which state the ring shows, what the telemetry strip says, how the settings map to looks.
 *
 * Nothing here touches the DOM, a store or the network, so it is tested directly (hud-model.test.ts). The composable
 * (use-jarvis-hud.ts) feeds it real signals; the components only draw what it returns.
 */

/** What the ring shows. One at a time; see {@link resolveHudState} for which wins. */
export type HudState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'acting' | 'offline'

/** The setting: off draws nothing, subtle is the default, full adds the stronger glow, scanlines and corner marks. */
export type HudMode = 'off' | 'subtle' | 'full'

export const HUD_MODES: readonly HudMode[] = ['off', 'subtle', 'full']
export const HUD_DEFAULT_MODE: HudMode = 'subtle'
export const HUD_MODE_KEY = 'settings/jarvis-hud/mode'

/** The tool that the JARVIS tool server (jarvis/bridge/face_tools.py) answers with content-free facts. */
export const JARVIS_STATUS_TOOL = 'jarvis::jarvis_status'

/** A failed poll is forgiven once (a restart of the tool server takes a moment); two in a row is "offline". */
export const HUD_OFFLINE_AFTER_FAILURES = 2

export function normalizeHudMode(raw: unknown): HudMode {
  return HUD_MODES.includes(raw as HudMode) ? raw as HudMode : HUD_DEFAULT_MODE
}

// ---- the status tool

export type PcKind = 'none' | 'task' | 'mission' | 'action' | 'step' | 'check' | 'other'

/** What `jarvis_status` returns. Counts, flags, ages and one fixed word: never a title, an id or text. */
export interface JarvisStatus {
  ok: boolean
  consoleUp: boolean
  kernelUp: boolean
  killSwitchOn: boolean
  approvalsWaiting: number
  messagesUnread: number
  pcBusy: boolean
  pcKind: PcKind
  pcAgeS: number
  pcStopping: boolean
  /** D60: which step of a goal runs now (jarvis_status goal_*): a state word, two counts and one fixed executor id. Never text. */
  goalState: GoalState
  goalN: number
  goalTotal: number
  goalExec: string
}

export type GoalState = 'none' | 'planning' | 'running' | 'done' | 'failed' | 'waiting'
const GOAL_STATES: readonly GoalState[] = ['none', 'planning', 'running', 'done', 'failed', 'waiting']

interface ToolResult {
  structuredContent?: Record<string, unknown>
  content?: Array<Record<string, unknown>>
  isError?: boolean
}

const PC_KINDS: readonly PcKind[] = ['none', 'task', 'mission', 'action', 'step', 'check', 'other']

function count(value: unknown): number {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? Math.min(Math.floor(n), 999) : 0
}

/** The status in a tool result (structured part, or the JSON text of the first content item). `null` when it is not one. */
export function parseJarvisStatus(result: ToolResult | undefined | null): JarvisStatus | null {
  if (!result || result.isError)
    return null
  let data: unknown = result.structuredContent
  if (!data) {
    const text = result.content?.find(item => item.type === 'text')?.text
    if (typeof text !== 'string')
      return null
    try {
      data = JSON.parse(text)
    }
    catch {
      return null
    }
  }
  if (!data || typeof data !== 'object' || Array.isArray(data))
    return null
  const d = data as Record<string, unknown>
  if (typeof d.kernel_up !== 'boolean' && typeof d.console_up !== 'boolean')
    return null
  const kind = PC_KINDS.includes(d.pc_kind as PcKind) ? d.pc_kind as PcKind : 'other'
  const pcBusy = d.pc_busy === true
  return {
    ok: d.ok !== false,
    consoleUp: d.console_up === true,
    kernelUp: d.kernel_up === true,
    killSwitchOn: d.kill_switch_on === true,
    approvalsWaiting: count(d.approvals_waiting),
    messagesUnread: count(d.messages_unread),
    pcBusy,
    pcKind: pcBusy ? kind : 'none',
    pcAgeS: pcBusy ? count(d.pc_age_s) : 0,
    pcStopping: pcBusy && d.pc_stopping === true,
    goalState: GOAL_STATES.includes(d.goal_state as GoalState) ? d.goal_state as GoalState : 'none',
    goalN: count(d.goal_n),
    goalTotal: count(d.goal_total),
    goalExec: typeof d.goal_exec === 'string' ? d.goal_exec : '',
  }
}

/** The poll history: the last good status and how many polls in a row failed. */
export interface StatusPoll {
  status: JarvisStatus | null
  failures: number
}

export function reduceStatusPoll(previous: StatusPoll, result: ToolResult | undefined | null): StatusPoll {
  const status = parseJarvisStatus(result)
  return status ? { status, failures: 0 } : { status: previous.status, failures: previous.failures + 1 }
}

export type HudLink = 'unknown' | 'up' | 'down' | 'killed'

/** Whether JARVIS can be reached: the tool server, the console and the kernel behind it, and the kill switch. */
export function resolveHudLink(poll: StatusPoll): HudLink {
  if (poll.failures >= HUD_OFFLINE_AFTER_FAILURES)
    return 'down'
  const s = poll.status
  if (!s)
    return 'unknown'
  if (s.killSwitchOn)
    return 'killed'
  return s.consoleUp && s.kernelUp ? 'up' : 'down'
}

// ---- the ring

export interface HudSignals {
  /** The character's voice is playing now. */
  speaking: boolean
  /** The owner is being heard now (a speech input is being captured). */
  listening: boolean
  /** A turn is running in the chat: the model is working on an answer. */
  thinking: boolean
  /** A voice or microphone problem the face itself reports. */
  voiceError: boolean
  link: HudLink
  /** A PC task runs (from the desktop plugin's registry). */
  pcBusy: boolean
  /** D60: the owner's message is a PC, screen or document task and the instant "On it" is showing; no model has answered yet. */
  ack?: boolean
}

/**
 * Which state the ring shows.
 *
 * What the owner sees and hears right now wins (speaking, then listening, then thinking), so the ring answers him at once; then the
 * PC work JARVIS does for him (acting). A broken link or a kill switch shows only when nothing of that is happening, and it
 * overrides rest, because "idle" must never hide that JARVIS cannot act.
 */
export function resolveHudState(s: HudSignals): HudState {
  if (s.speaking)
    return 'speaking'
  if (s.listening)
    return 'listening'
  if (s.ack && (s.link === 'up' || s.link === 'unknown'))
    return 'acting' // D60: "On it" at once; the ring does not wait for the model or for the next status poll
  if (s.thinking)
    return 'thinking'
  if (s.link === 'up' && s.pcBusy)
    return 'acting'
  if (s.link === 'down' || s.link === 'killed' || s.voiceError)
    return 'offline'
  return 'idle'
}

const STATE_LABELS: Record<HudState, string> = {
  idle: 'STANDBY',
  listening: 'LISTENING',
  thinking: 'THINKING',
  speaking: 'SPEAKING',
  acting: 'ACTING',
  offline: 'OFFLINE',
}

export function hudStateLabel(state: HudState, link: HudLink = 'up'): string {
  if (state === 'offline')
    return link === 'killed' ? 'HALTED' : link === 'down' ? 'OFFLINE' : 'FAULT'
  return STATE_LABELS[state]
}

// ---- the progress line (D60)

/** What each fixed executor id of a goal's plan is called in a progress line. The plan's own words never reach the face (they can hold file names). */
const EXECUTOR_WORDS: Record<string, string> = {
  pc: 'working on the PC',
  screen_read: 'reading the window',
  screen_locate: 'looking for it on screen',
  doc_read: 'reading the document',
  deliverable_list: 'checking what I made',
  deliverable_open: 'opening it',
  recall: 'recalling',
  tasks: 'checking tasks',
  rules: 'doing it',
}

/** One short line for the step that runs now, or '' when no goal is under way ("Planning it", "Step 2 of 3: reading the window"). */
export function goalLine(status: JarvisStatus | null): string {
  if (!status)
    return ''
  switch (status.goalState) {
    case 'planning': return 'Planning it'
    case 'running': {
      const what = EXECUTOR_WORDS[status.goalExec] ?? 'working'
      return status.goalTotal > 1 && status.goalN > 0 ? `Step ${status.goalN} of ${status.goalTotal}: ${what}` : `${what.charAt(0).toUpperCase()}${what.slice(1)}`
    }
    case 'waiting': return 'Waiting for your approval'
    default: return ''
  }
}

/** The line the HUD shows: the goal's step when there is one, else the instant acknowledgement, else nothing. */
export function hudLine(ack: string | null | undefined, status: JarvisStatus | null): string {
  return goalLine(status) || (ack ?? '')
}

// ---- the strip

export function formatClock(date: Date): string {
  const two = (n: number) => String(n).padStart(2, '0')
  return `${two(date.getHours())}:${two(date.getMinutes())}`
}

/** 7 -> 7s, 75 -> 1m 15s, 3700 -> 1h 01m. */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(Number.isFinite(totalSeconds) ? totalSeconds : 0))
  if (s < 60)
    return `${s}s`
  if (s < 3600)
    return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
}

export function kernelLabel(link: HudLink): string {
  switch (link) {
    case 'up': return 'KERNEL UP'
    case 'killed': return 'KILL SWITCH'
    case 'down': return 'KERNEL DOWN'
    default: return 'KERNEL …'
  }
}

const PC_KIND_WORDS: Record<PcKind, string> = {
  none: 'IDLE',
  task: 'TASK',
  mission: 'MISSION',
  action: 'ACTION',
  step: 'STEP',
  check: 'CHECK',
  other: 'PC TASK',
}

/** "IDLE", or the kind of PC work and for how long ("TASK 1m 12s"); a stop under way says so. */
export function taskLabel(status: JarvisStatus | null, extraSeconds = 0): string {
  if (!status?.pcBusy)
    return 'IDLE'
  if (status.pcStopping)
    return 'STOPPING'
  return `${PC_KIND_WORDS[status.pcKind]} ${formatDuration(status.pcAgeS + extraSeconds)}`
}

/** The model name without its provider path ("zai-org/GLM-5.3-Flash" -> "GLM-5.3-Flash"), cut to fit the strip. */
export function shortModel(model: string | undefined | null, max = 18): string {
  const name = String(model ?? '').trim().split('/').pop() ?? ''
  if (!name)
    return ''
  return name.length > max ? `${name.slice(0, max - 1)}…` : name
}

/** Approvals waiting for the owner's PIN and plugin messages he has not read, as one number. */
export function noticeCount(status: JarvisStatus | null): number {
  return status ? status.approvalsWaiting + status.messagesUnread : 0
}

/** "9+" past nine, so the slot keeps its width. */
export function noticeText(n: number): string {
  return n > 9 ? '9+' : String(Math.max(0, n))
}

// ---- looks

export interface HudLook {
  /** The whole layer is drawn. */
  visible: boolean
  /** Strength of the soft light behind the character, 0 to 1. */
  glow: number
  /** Strength of the scanlines, 0 to 1. */
  scan: number
  /** The slow sweep line and the corner marks. */
  frame: boolean
}

export function hudLook(mode: HudMode): HudLook {
  switch (mode) {
    case 'off': return { visible: false, glow: 0, scan: 0, frame: false }
    case 'full': return { visible: true, glow: 1, scan: 1, frame: true }
    default: return { visible: true, glow: 0.45, scan: 0.35, frame: false }
  }
}

/** The accessible name of the ring. */
export function hudAriaLabel(state: HudState, link: HudLink, status: JarvisStatus | null): string {
  const bits = [`JARVIS ${hudStateLabel(state, link).toLowerCase()}`, kernelLabel(link).toLowerCase(), `PC ${taskLabel(status).toLowerCase()}`]
  const n = noticeCount(status)
  if (n > 0)
    bits.push(`${n} ${n === 1 ? 'notice' : 'notices'} waiting`)
  return bits.join(', ')
}
