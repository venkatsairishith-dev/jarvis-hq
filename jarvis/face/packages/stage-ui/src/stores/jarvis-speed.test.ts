import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it } from 'vitest'

import { normalizeSwitch, resolveSwitch, useJarvisSpeedStore } from './jarvis-speed'

describe('the speed switches', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('reads anything that is not on or off as automatic', () => {
    expect(normalizeSwitch('on')).toBe('on')
    expect(normalizeSwitch('off')).toBe('off')
    for (const raw of ['auto', '', 'ON', null, undefined, 3, {}])
      expect(normalizeSwitch(raw)).toBe('auto')
  })

  it('lets On and Off win over the environment and Automatic follow it', () => {
    expect(resolveSwitch('auto', true)).toBe(true)
    expect(resolveSwitch('auto', false)).toBe(false)
    expect(resolveSwitch('on', false)).toBe(true)
    expect(resolveSwitch('off', true)).toBe(false)
    expect(resolveSwitch('garbage', false)).toBe(false)
  })

  it('is on until the environment says otherwise, and the setting beats the environment', () => {
    const speed = useJarvisSpeedStore()
    expect([speed.directTools, speed.quickAck, speed.quickCommand, speed.timing]).toEqual([true, true, true, true])
    speed.setEnv({ directTools: false, quickCommand: false })
    expect([speed.directTools, speed.quickAck, speed.quickCommand]).toEqual([false, true, false])
    speed.directToolsSetting = 'on'
    expect(speed.directTools).toBe(true)
    speed.setEnv({ directTools: 'yes' as never, timing: false })
    expect(speed.env.directTools).toBe(false)
    expect(speed.timing).toBe(false)
    speed.setEnv(undefined)
    expect(speed.env.timing).toBe(false)
  })

  it('keeps the acknowledgement of a newer turn when an older turn ends', () => {
    const speed = useJarvisSpeedStore()
    speed.setAck({ line: 'On it', kind: 'pc', turnId: 'new' })
    speed.clearAck('old')
    expect(speed.ack?.turnId).toBe('new')
    speed.clearAck('new')
    expect(speed.ack).toBeNull()
  })
})
