import type { OrbSignals } from './orb-state'

import { describe, expect, it } from 'vitest'

import { followsLevel, goalActive, idleBreath, idleTimerWanted, levelDue, ORB_ANIMATION_FRAME_LOOPS, ORB_BREATH_PERIOD_MS, ORB_IDLE_TICK_MS, ORB_LEVEL_MS, ORB_STATES, orbButtonLabel, orbProgress, orbStatusText, resolveOrbState, ringScale } from './orb-state'

const calm: OrbSignals = { hud: 'idle', link: 'up', approvalsWaiting: 0, askOpen: false, voiceError: false }
const state = (patch: Partial<OrbSignals>) => resolveOrbState({ ...calm, ...patch })

describe('resolveOrbState: events -> state', () => {
  it('is idle when nothing happens', () => {
    expect(state({})).toBe('idle')
  })
  it.each([
    [{ hud: 'listening' }, 'listening'],
    [{ hud: 'thinking' }, 'thinking'],
    [{ hud: 'speaking' }, 'speaking'],
    [{ hud: 'acting' }, 'working'],
    [{ approvalsWaiting: 1 }, 'needs-you'],
    [{ askOpen: true }, 'needs-you'],
    [{ hud: 'offline', link: 'down' }, 'error'],
    [{ link: 'killed' }, 'error'],
    [{ voiceError: true }, 'error'],
  ] as const)('%j -> %s', (patch, expected) => {
    expect(state(patch as Partial<OrbSignals>)).toBe(expected)
  })

  it('what the owner hears and says beats everything, even an approval', () => {
    expect(state({ hud: 'speaking', approvalsWaiting: 2 })).toBe('speaking')
    expect(state({ hud: 'listening', askOpen: true })).toBe('listening')
  })
  it('an approval or a question beats the work and the thinking', () => {
    expect(state({ hud: 'acting', approvalsWaiting: 1 })).toBe('needs-you')
    expect(state({ hud: 'thinking', askOpen: true })).toBe('needs-you')
  })
  it('work beats thinking, and an error shows only when nothing else is going on', () => {
    expect(state({ hud: 'acting' })).toBe('working')
    expect(state({ hud: 'thinking', link: 'down' })).toBe('thinking')
    expect(state({ hud: 'idle', link: 'down' })).toBe('error')
  })
  it('every result is one of the seven states', () => {
    const huds = ['idle', 'listening', 'thinking', 'speaking', 'acting', 'offline'] as const
    const links = ['unknown', 'up', 'down', 'killed'] as const
    for (const hud of huds) {
      for (const link of links) {
        for (const approvalsWaiting of [0, 3]) {
          for (const askOpen of [false, true]) {
            for (const voiceError of [false, true])
              expect(ORB_STATES).toContain(resolveOrbState({ hud, link, approvalsWaiting, askOpen, voiceError }))
          }
        }
      }
    }
  })
})

describe('text', () => {
  it('says each state in a few words', () => {
    expect(orbStatusText('idle', '')).toBe('Ready')
    expect(orbStatusText('listening', undefined)).toBe('Listening')
    expect(orbStatusText('thinking', undefined)).toBe('Thinking')
    expect(orbStatusText('speaking', undefined)).toBe('Speaking')
  })
  it('working and needs-you use the line of the task when there is one', () => {
    expect(orbStatusText('working', 'Step 2 of 3: reading the window')).toBe('Step 2 of 3: reading the window')
    expect(orbStatusText('working', '   ')).toBe('Working on it')
    expect(orbStatusText('needs-you', 'Waiting for your approval')).toBe('Waiting for your approval')
    expect(orbStatusText('needs-you', undefined)).toBe('Waiting for you')
  })
  it('error says why in a word', () => {
    expect(orbStatusText('error', '', 'killed')).toBe('Halted')
    expect(orbStatusText('error', '', 'down')).toBe('Offline')
    expect(orbStatusText('error', '', 'up')).toBe('Something is wrong')
  })
  it('the button names what pressing does', () => {
    expect(orbButtonLabel('idle')).toContain('Press to talk')
    expect(orbButtonLabel('listening')).toContain('Press to stop')
    expect(orbButtonLabel('speaking')).toContain('stop talking')
  })
})

describe('orbProgress', () => {
  it('is unknown without a plan or for one step', () => {
    expect(orbProgress(null)).toBeNull()
    expect(orbProgress({ goalN: 1, goalTotal: 1, goalState: 'running' })).toBeNull()
    expect(orbProgress({ goalN: 0, goalTotal: 3, goalState: 'planning' })).toBeNull()
  })
  it('counts the finished steps and half of the running one, never empty and never full', () => {
    expect(orbProgress({ goalN: 1, goalTotal: 4, goalState: 'running' })).toBeCloseTo(0.125)
    expect(orbProgress({ goalN: 2, goalTotal: 4, goalState: 'running' })).toBeCloseTo(0.375)
    expect(orbProgress({ goalN: 4, goalTotal: 4, goalState: 'running' })).toBeCloseTo(0.875)
    expect(orbProgress({ goalN: 9, goalTotal: 4, goalState: 'running' })!).toBeLessThan(1)
  })
  it('knows an active goal', () => {
    expect(goalActive('running')).toBe(true)
    expect(goalActive('done')).toBe(false)
    expect(goalActive(undefined)).toBe(false)
  })
})

describe('the few things that move', () => {
  it('only listening and speaking follow a level', () => {
    expect(ORB_STATES.filter(followsLevel)).toEqual(['listening', 'speaking'])
  })
  it('a level is drawn at most 15 times a second', () => {
    expect(ORB_LEVEL_MS).toBeCloseTo(66.67, 1)
    expect(levelDue(100, 100 + 0)).toBe(false)
    expect(levelDue(150, 100)).toBe(false)
    expect(levelDue(167, 100)).toBe(true)
  })
  it('the ring scale stays in a calm range and smooths', () => {
    expect(ringScale(0)).toBe(1)
    expect(ringScale(1, 1)).toBeCloseTo(1.15)
    expect(ringScale(5)).toBeLessThanOrEqual(1.3)
    expect(ringScale(Number.NaN, 1.2)).toBeLessThan(1.2)
    let scale = 1
    for (let i = 0; i < 30; i++)
      scale = ringScale(1, scale)
    expect(scale).toBeCloseTo(1.3, 2)
  })
  it('the idle breath is a slow wave between 0 and 1 that returns after one period', () => {
    const ticks = ORB_BREATH_PERIOD_MS / ORB_IDLE_TICK_MS
    expect(ORB_IDLE_TICK_MS).toBeGreaterThanOrEqual(500)
    expect(idleBreath(0)).toBe(0.5)
    expect(idleBreath(ticks / 4)).toBe(1)
    expect(idleBreath(ticks * 3 / 4)).toBe(0)
    expect(idleBreath(ticks)).toBeCloseTo(0.5, 2)
    const values = Array.from({ length: ticks + 1 }, (_, i) => idleBreath(i))
    expect(Math.min(...values)).toBeGreaterThanOrEqual(0)
    expect(Math.max(...values)).toBeLessThanOrEqual(1)
    // one tick changes the glow by less than a tenth: it does not look like a step
    for (let i = 1; i < values.length; i++)
      expect(Math.abs(values[i]! - values[i - 1]!)).toBeLessThan(0.4)
  })
  it('the idle timer runs only when idle, in motion, and visible', () => {
    expect(idleTimerWanted('idle', false, false)).toBe(true)
    expect(idleTimerWanted('idle', true, false)).toBe(false)
    expect(idleTimerWanted('idle', false, true)).toBe(false)
    for (const state of ORB_STATES.filter(state => state !== 'idle'))
      expect(idleTimerWanted(state, false, false)).toBe(false)
  })
  it('there is no animation frame loop', () => {
    expect(ORB_ANIMATION_FRAME_LOOPS).toBe(0)
  })
})
