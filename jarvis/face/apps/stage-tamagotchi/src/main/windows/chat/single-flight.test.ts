import { describe, expect, it } from 'vitest'

import { singleFlight } from './single-flight'

describe('singleFlight', () => {
  it('lets a second press during a slow first one join it instead of running again', async () => {
    let runs = 0
    let finish: () => void = () => {}
    const toggle = singleFlight(() => new Promise<number>((resolve) => {
      runs += 1
      finish = () => resolve(runs)
    }))

    const first = toggle()
    const second = toggle()
    const third = toggle()
    expect(runs).toBe(1)
    finish()
    expect(await Promise.all([first, second, third])).toEqual([1, 1, 1])
  })

  it('runs again for a press after the first one is done, also after a failure', async () => {
    let runs = 0
    const toggle = singleFlight(async () => {
      runs += 1
      if (runs === 1)
        throw new Error('window could not be created')
      return runs
    })

    await expect(toggle()).rejects.toThrow('could not be created')
    expect(await toggle()).toBe(2)
    expect(await toggle()).toBe(3)
  })
})
