import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * Owner, 2026-10-28: "I still can't move the Jarvis widget freely." The widgets window is
 * frameless (`frame: false` in `main/windows/widgets/index.ts`, no native title bar) and never
 * calls `setIgnoreMouseEvents`, so the only way to drag it is the `-webkit-app-region: drag`
 * handle below. That handle also carried `pointer-events-none`, which excludes an element from
 * all mouse hit-testing including the mousedown that starts the native drag -- so the handle could
 * never actually be grabbed. `caption.vue` has the identical handle without that class and drags
 * fine; this pins the same shape for widgets.vue.
 */

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, 'widgets.vue'), 'utf-8')

describe('widgets window drag handle', () => {
  it('keeps the app-region:drag handle and never pairs it with pointer-events-none', () => {
    const handleLine = source.split('\n').find(line => line.includes('-webkit-app-region:drag'))
    expect(handleLine).toBeDefined()
    expect(handleLine).not.toContain('pointer-events-none')
  })
})
