import type { StatusPoll } from './hud-model'

import { describe, expect, it } from 'vitest'

import {
  formatClock,
  formatDuration,
  goalLine,
  HUD_DEFAULT_MODE,
  hudAriaLabel,
  hudLine,
  hudLook,
  hudStateLabel,
  kernelLabel,
  normalizeHudMode,
  noticeCount,
  noticeText,
  parseJarvisStatus,
  reduceStatusPoll,
  resolveHudLink,
  resolveHudState,
  shortModel,
  taskLabel,
} from './hud-model'

const base = { speaking: false, listening: false, thinking: false, voiceError: false, link: 'up' as const, pcBusy: false }
function wire(over: Record<string, unknown> = {}) {
  return {
    structuredContent: {
      ok: true,
      console_up: true,
      kernel_up: true,
      kill_switch_on: false,
      console_busy: false,
      approvals_waiting: 0,
      messages_unread: 0,
      pc_busy: false,
      pc_kind: 'none',
      pc_age_s: 0,
      pc_stopping: false,
      pc_ops: 0,
      ...over,
    },
  }
}

describe('resolveHudState', () => {
  it('rests when nothing happens and JARVIS is reachable', () => {
    expect(resolveHudState(base)).toBe('idle')
  })

  it('shows what the owner sees and hears before anything else', () => {
    expect(resolveHudState({ ...base, speaking: true, listening: true, thinking: true, pcBusy: true })).toBe('speaking')
    expect(resolveHudState({ ...base, listening: true, thinking: true, pcBusy: true })).toBe('listening')
    expect(resolveHudState({ ...base, thinking: true, pcBusy: true })).toBe('thinking')
    expect(resolveHudState({ ...base, pcBusy: true })).toBe('acting')
  })

  it('does not call PC work "acting" when the link is not up, and says offline instead of resting', () => {
    expect(resolveHudState({ ...base, link: 'down', pcBusy: true })).toBe('offline')
    expect(resolveHudState({ ...base, link: 'killed' })).toBe('offline')
    expect(resolveHudState({ ...base, voiceError: true })).toBe('offline')
  })

  it('keeps answering the owner while the link is down', () => {
    expect(resolveHudState({ ...base, link: 'down', speaking: true })).toBe('speaking')
    expect(resolveHudState({ ...base, link: 'down', thinking: true })).toBe('thinking')
  })

  it('rests while the first answer is still on its way', () => {
    expect(resolveHudState({ ...base, link: 'unknown' })).toBe('idle')
  })
})

describe('parseJarvisStatus', () => {
  it('reads the structured part and the JSON text fallback', () => {
    expect(parseJarvisStatus(wire({ pc_busy: true, pc_kind: 'task', pc_age_s: 71, approvals_waiting: 1, messages_unread: 2 }))).toEqual({
      ok: true,
      consoleUp: true,
      kernelUp: true,
      killSwitchOn: false,
      approvalsWaiting: 1,
      messagesUnread: 2,
      pcBusy: true,
      pcKind: 'task',
      pcAgeS: 71,
      pcStopping: false,
      goalState: 'none',
      goalN: 0,
      goalTotal: 0,
      goalExec: '',
    })
    const viaText = parseJarvisStatus({ content: [{ type: 'text', text: JSON.stringify(wire().structuredContent) }] })
    expect(viaText?.kernelUp).toBe(true)
  })

  it('returns null for errors, junk and answers of another tool', () => {
    expect(parseJarvisStatus(undefined)).toBeNull()
    expect(parseJarvisStatus({ isError: true, ...wire() })).toBeNull()
    expect(parseJarvisStatus({ content: [{ type: 'text', text: 'nope' }] })).toBeNull()
    expect(parseJarvisStatus({ content: [{ type: 'text', text: '[1,2]' }] })).toBeNull()
    expect(parseJarvisStatus({ structuredContent: { notices: [] } })).toBeNull()
  })

  it('clamps counts, drops an unknown kind to "other" and ignores kind and age while the PC is idle', () => {
    const s = parseJarvisStatus(wire({ approvals_waiting: -4, messages_unread: 'many', pc_busy: true, pc_kind: 'rm -rf', pc_age_s: 'x' }))
    expect(s).toMatchObject({ approvalsWaiting: 0, messagesUnread: 0, pcKind: 'other', pcAgeS: 0 })
    expect(parseJarvisStatus(wire({ pc_busy: false, pc_kind: 'task', pc_age_s: 50 }))).toMatchObject({ pcKind: 'none', pcAgeS: 0 })
    expect(parseJarvisStatus(wire({ approvals_waiting: 5000 }))?.approvalsWaiting).toBe(999)
  })
})

describe('status polling and the link', () => {
  const empty: StatusPoll = { status: null, failures: 0 }

  it('is unknown before the first answer', () => {
    expect(resolveHudLink(empty)).toBe('unknown')
  })

  it('forgives one failed poll, then goes down, and recovers on the next good answer', () => {
    const good = reduceStatusPoll(empty, wire())
    expect(resolveHudLink(good)).toBe('up')
    const one = reduceStatusPoll(good, { isError: true })
    expect(one.failures).toBe(1)
    expect(resolveHudLink(one)).toBe('up')
    const two = reduceStatusPoll(one, undefined)
    expect(resolveHudLink(two)).toBe('down')
    expect(resolveHudLink(reduceStatusPoll(two, wire()))).toBe('up')
  })

  it('reads the kill switch and a kernel that is down from the answer itself', () => {
    expect(resolveHudLink(reduceStatusPoll(empty, wire({ kill_switch_on: true })))).toBe('killed')
    expect(resolveHudLink(reduceStatusPoll(empty, wire({ kernel_up: false })))).toBe('down')
    expect(resolveHudLink(reduceStatusPoll(empty, wire({ ok: false, console_up: false, kernel_up: false })))).toBe('down')
  })

  it('never answers up for a poll that has only failed', () => {
    let poll = empty
    for (let i = 0; i < 3; i++)
      poll = reduceStatusPoll(poll, undefined)
    expect(resolveHudLink(poll)).toBe('down')
  })
})

describe('labels', () => {
  it('names each state and the halted kill switch', () => {
    expect(hudStateLabel('idle')).toBe('STANDBY')
    expect(hudStateLabel('offline', 'down')).toBe('OFFLINE')
    expect(hudStateLabel('offline', 'killed')).toBe('HALTED')
    expect(hudStateLabel('offline', 'up')).toBe('FAULT')
    expect(kernelLabel('up')).toBe('KERNEL UP')
    expect(kernelLabel('down')).toBe('KERNEL DOWN')
    expect(kernelLabel('killed')).toBe('KILL SWITCH')
    expect(kernelLabel('unknown')).toBe('KERNEL …')
  })

  it('formats the clock and durations', () => {
    expect(formatClock(new Date(2026, 9, 25, 7, 5))).toBe('07:05')
    expect(formatDuration(7)).toBe('7s')
    expect(formatDuration(75)).toBe('1m 15s')
    expect(formatDuration(3700)).toBe('1h 01m')
    expect(formatDuration(-3)).toBe('0s')
    expect(formatDuration(Number.NaN)).toBe('0s')
  })

  it('says idle, or the kind and age of the PC work, or that it is stopping', () => {
    const task = parseJarvisStatus(wire({ pc_busy: true, pc_kind: 'task', pc_age_s: 71 }))
    expect(taskLabel(null)).toBe('IDLE')
    expect(taskLabel(parseJarvisStatus(wire()))).toBe('IDLE')
    expect(taskLabel(task)).toBe('TASK 1m 11s')
    expect(taskLabel(task, 4)).toBe('TASK 1m 15s')
    expect(taskLabel(parseJarvisStatus(wire({ pc_busy: true, pc_kind: 'mission', pc_stopping: true })))).toBe('STOPPING')
  })

  it('shortens model names and counts notices', () => {
    expect(shortModel('zai-org/GLM-5.3-Flash')).toBe('GLM-5.3-Flash')
    expect(shortModel('a-very-long-model-name-indeed')).toHaveLength(18)
    expect(shortModel('')).toBe('')
    expect(shortModel(undefined)).toBe('')
    expect(noticeCount(null)).toBe(0)
    expect(noticeCount(parseJarvisStatus(wire({ approvals_waiting: 1, messages_unread: 2 })))).toBe(3)
    expect(noticeText(12)).toBe('9+')
    expect(noticeText(3)).toBe('3')
  })

  it('gives the ring a readable name for screen readers, without any task text', () => {
    const s = parseJarvisStatus(wire({ pc_busy: true, pc_kind: 'task', pc_age_s: 9, messages_unread: 1 }))
    expect(hudAriaLabel('acting', 'up', s)).toBe('JARVIS acting, kernel up, PC task 9s, 1 notice waiting')
  })
})

describe('settings', () => {
  it('defaults to subtle for anything that is not a mode', () => {
    expect(HUD_DEFAULT_MODE).toBe('subtle')
    expect(normalizeHudMode(undefined)).toBe('subtle')
    expect(normalizeHudMode('loud')).toBe('subtle')
    expect(normalizeHudMode('off')).toBe('off')
    expect(normalizeHudMode('full')).toBe('full')
  })

  it('draws nothing when off, and more when full than when subtle', () => {
    expect(hudLook('off').visible).toBe(false)
    const subtle = hudLook('subtle')
    const full = hudLook('full')
    expect(subtle.visible && full.visible).toBe(true)
    expect(full.glow).toBeGreaterThan(subtle.glow)
    expect(full.scan).toBeGreaterThan(subtle.scan)
    expect(subtle.frame).toBe(false)
    expect(full.frame).toBe(true)
  })
})

describe('jARVIS D60: the instant acknowledgement and the progress line', () => {
  const goal = (over: Record<string, unknown>) => parseJarvisStatus(wire(over))

  it('shows the ring at work as soon as a task message is acknowledged, before a model answers or the status is polled', () => {
    expect(resolveHudState({ ...base, ack: true })).toBe('acting')
    expect(resolveHudState({ ...base, ack: true, thinking: true })).toBe('acting')
    expect(resolveHudState({ ...base, thinking: true })).toBe('thinking')
  })

  it('lets what the owner hears and says win, and never claims work while JARVIS is down or halted', () => {
    expect(resolveHudState({ ...base, ack: true, speaking: true })).toBe('speaking')
    expect(resolveHudState({ ...base, ack: true, listening: true })).toBe('listening')
    expect(resolveHudState({ ...base, ack: true, link: 'down' })).toBe('offline')
    expect(resolveHudState({ ...base, ack: true, link: 'killed' })).toBe('offline')
    expect(resolveHudState({ ...base, ack: true, link: 'unknown' })).toBe('acting')
  })

  it('reads the goal fields of the status and treats anything unknown as no goal', () => {
    expect(goal({ goal_state: 'running', goal_n: 2, goal_total: 3, goal_exec: 'screen_read' })).toMatchObject({ goalState: 'running', goalN: 2, goalTotal: 3, goalExec: 'screen_read' })
    expect(goal({ goal_state: 'rm -rf', goal_n: 'x', goal_total: -1 })).toMatchObject({ goalState: 'none', goalN: 0, goalTotal: 0 })
  })

  it('says the step that runs, from fixed words', () => {
    expect(goalLine(goal({ goal_state: 'planning' }))).toBe('Planning it')
    expect(goalLine(goal({ goal_state: 'running', goal_n: 2, goal_total: 3, goal_exec: 'screen_read' }))).toBe('Step 2 of 3: reading the window')
    expect(goalLine(goal({ goal_state: 'running', goal_n: 1, goal_total: 1, goal_exec: 'pc' }))).toBe('Working on the PC')
    expect(goalLine(goal({ goal_state: 'running', goal_n: 1, goal_total: 2, goal_exec: 'something new' }))).toBe('Step 1 of 2: working')
    expect(goalLine(goal({ goal_state: 'waiting' }))).toBe('Waiting for your approval')
    expect(goalLine(goal({ goal_state: 'done' }))).toBe('')
    expect(goalLine(goal({}))).toBe('')
    expect(goalLine(null)).toBe('')
  })

  it('prefers the goal step over the acknowledgement and shows the acknowledgement until there is one', () => {
    const running = goal({ goal_state: 'running', goal_n: 1, goal_total: 2, goal_exec: 'pc' })
    expect(hudLine('On it', running)).toBe('Step 1 of 2: working on the PC')
    expect(hudLine('On it', goal({}))).toBe('On it')
    expect(hudLine(null, goal({}))).toBe('')
    expect(hudLine(undefined, null)).toBe('')
  })
})
