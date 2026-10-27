/**
 * D78: what the orb (and the minimal bar) shows, as pure functions: which of the seven states wins for the signals the face already has,
 * the one line of text, the progress of a task, and the rules for the (very few) things that move.
 *
 * No store, no DOM, no avatar: this module is on the orb's import path, which must never reach the Live2D / VRM / WebGL code (a static
 * import-graph test, `orb-imports.test.ts`, keeps it so). It only imports the HUD's own pure model, which is also free of the avatar.
 */

import type { GoalState, HudLink, HudState, JarvisStatus } from '../hud/hud-model'

export type OrbState = 'idle' | 'listening' | 'thinking' | 'speaking' | 'working' | 'needs-you' | 'error'

export const ORB_STATES: readonly OrbState[] = ['idle', 'listening', 'thinking', 'speaking', 'working', 'needs-you', 'error']

export interface OrbSignals {
  /** The HUD's resolved state (speaking, listening, thinking, acting, offline or idle). */
  hud: HudState
  link: HudLink
  /** Approvals waiting for the owner's PIN (a number from the status poll; never what they are). */
  approvalsWaiting: number
  /** An ask form (D55 / D63) is open and waits for an answer. */
  askOpen: boolean
  /** A voice or microphone problem the face reports. */
  voiceError: boolean
}

/**
 * Which state the orb shows.
 *
 * What the owner hears and says right now wins (speaking, then listening). Then anything that waits for HIM (an approval or a question):
 * an amber ring that he must not miss. Then the work JARVIS does for him (working), then thinking. A broken link, a stopped kernel or a
 * voice problem is the soft red state, shown only when nothing of the above is going on, like the HUD's ring. Otherwise idle.
 */
export function resolveOrbState(s: OrbSignals): OrbState {
  if (s.hud === 'speaking')
    return 'speaking'
  if (s.hud === 'listening')
    return 'listening'
  if (s.approvalsWaiting > 0 || s.askOpen)
    return 'needs-you'
  if (s.hud === 'acting')
    return 'working'
  if (s.hud === 'thinking')
    return 'thinking'
  if (s.hud === 'offline' || s.link === 'down' || s.link === 'killed' || s.voiceError)
    return 'error'
  return 'idle'
}

/** The words for a screen reader and the minimal bar: short, in English, no private text. */
export function orbStatusText(state: OrbState, line: string | undefined, link: HudLink = 'up'): string {
  switch (state) {
    case 'listening': return 'Listening'
    case 'speaking': return 'Speaking'
    case 'thinking': return 'Thinking'
    case 'working': return line && line.trim() ? line.trim() : 'Working on it'
    case 'needs-you': return line && line.trim() ? line.trim() : 'Waiting for you'
    case 'error': return link === 'killed' ? 'Halted' : link === 'down' ? 'Offline' : 'Something is wrong'
    default: return 'Ready'
  }
}

/** The accessible name of the orb button: what it is and what pressing it does. */
export function orbButtonLabel(state: OrbState, line?: string, link: HudLink = 'up'): string {
  const status = orbStatusText(state, line, link)
  return state === 'listening'
    ? `JARVIS: ${status}. Press to stop.`
    : state === 'speaking'
      ? `JARVIS: ${status}. Press to stop talking.`
      : `JARVIS: ${status}. Press to talk.`
}

/** The thin progress arc of a task: a fraction (0 to 1) of the steps done, or `null` when the length is not known (the arc then turns slowly). */
export function orbProgress(status: Pick<JarvisStatus, 'goalN' | 'goalTotal' | 'goalState'> | null | undefined): number | null {
  if (!status || status.goalTotal < 2 || status.goalN < 1)
    return null
  const doneSteps = Math.min(status.goalTotal, Math.max(0, status.goalN - 1))
  // the step that runs now counts as half done, so the arc is never empty while something happens and never full before the end
  return Math.min(0.98, (doneSteps + 0.5) / status.goalTotal)
}

export function goalActive(state: GoalState | undefined): boolean {
  return state === 'planning' || state === 'running' || state === 'waiting'
}

// ---- the few things that move

/** A level (the microphone while listening, the voice while speaking) reaches the screen at most this often: 15 times a second. */
export const ORB_LEVEL_MS = 1000 / 15

/** Only these two states follow a level; the rest are drawn by CSS alone, with no script running. */
export function followsLevel(state: OrbState): boolean {
  return state === 'listening' || state === 'speaking'
}

/**
 * The ring scale for a level: a calm range (1 to 1.3), smoothed toward the target so a loud syllable does not jump. Pure, so it is tested.
 */
export function ringScale(level: number, previous = 1): number {
  const target = 1 + Math.min(1, Math.max(0, Number.isFinite(level) ? level : 0)) * 0.3
  return Math.round((previous + (target - previous) * 0.5) * 1000) / 1000
}

/** Whether a new level may be drawn: at most one per `ORB_LEVEL_MS`. */
export function levelDue(now: number, lastDrawn: number): boolean {
  return now - lastDrawn >= ORB_LEVEL_MS
}

/**
 * The idle breathing (D78): a calm glow, drawn by a 2 Hz timer, not by an animation. Measured in the render harness (software compositing, so
 * the absolute numbers are only for comparing): a CSS animation costs 4.7 % of a core in the renderer and 4.8 % in the GPU process; the same
 * animation in 24 steps 2.2 % and 2.3 %; this 2 Hz timer 0.8 % and 0.3 %; nothing at all 0 %. A step of the timer changes the glow by about 2 %,
 * which the eye does not see as a step.
 */
export const ORB_IDLE_TICK_MS = 500
/** One breath takes this long. */
export const ORB_BREATH_PERIOD_MS = 8000

/** The glow's phase, 0 to 1 (0.5 is the middle), for the n-th tick of the idle timer. Pure. */
export function idleBreath(tick: number): number {
  const t = (tick * ORB_IDLE_TICK_MS) / ORB_BREATH_PERIOD_MS
  return Math.round((0.5 + 0.5 * Math.sin(t * Math.PI * 2)) * 1000) / 1000
}

/** The idle timer runs only while the orb is idle, the owner has not asked for reduced motion and the window can be seen. */
export function idleTimerWanted(state: OrbState, reducedMotion: boolean, pageHidden: boolean): boolean {
  return state === 'idle' && !reducedMotion && !pageHidden
}

/** How many frames a second the orb asks for: none. Everything is a CSS animation on transform and opacity, and the levels above are throttled timers. */
export const ORB_ANIMATION_FRAME_LOOPS = 0

/** The single quiet pulse after a forgotten hide, in milliseconds. */
export const FORGOTTEN_PULSE_MS = 2400
/** How long the "N things waited" count stays after an unhide. */
export const QUEUE_BADGE_MS = 6000
