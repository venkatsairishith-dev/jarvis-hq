import { describe, expect, it } from 'vitest'

import { CUBISM_CORE_SRC, cubismCoreLoaded, windowNeedsCubismAtStart } from './cubism-core'

describe('cubism core on demand', () => {
  it('only the orb and the bar windows start without it', () => {
    expect(windowNeedsCubismAtStart('?synced-leader=true&jarvis-look=orb')).toBe(false)
    expect(windowNeedsCubismAtStart('?jarvis-look=minimal')).toBe(false)
    expect(windowNeedsCubismAtStart('?jarvis-look=avatar')).toBe(true)
    // every other window (settings, chat, widgets) has no look in its address and loads it as before
    expect(windowNeedsCubismAtStart('?synced-leader=false')).toBe(true)
    expect(windowNeedsCubismAtStart('')).toBe(true)
    expect(windowNeedsCubismAtStart(undefined)).toBe(true)
  })

  it('knows whether the core is on the page', () => {
    expect(cubismCoreLoaded({})).toBe(false)
    expect(cubismCoreLoaded({ Live2DCubismCore: {} })).toBe(true)
  })

  it('loads the same file the old script tag loaded, relative to the page', () => {
    expect(CUBISM_CORE_SRC).toBe('assets/js/CubismSdkForWeb-5-r.3/Core/live2dcubismcore.min.js')
    expect(CUBISM_CORE_SRC.startsWith('/')).toBe(false)
    expect(new URL(CUBISM_CORE_SRC, 'file:///C:/jarvis/out/renderer/index.html').href).toBe('file:///C:/jarvis/out/renderer/assets/js/CubismSdkForWeb-5-r.3/Core/live2dcubismcore.min.js')
  })
})
