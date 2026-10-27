// The HUD's markup on the server side of Vue (no browser). Where the status line sits, that it is a status for screen readers, that it is
// plain text and that nothing is drawn when there is nothing to say. The layout at real window sizes is checked in Chromium by scripts/hud-check.mjs.
import { describe, expect, it } from 'vitest'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'

import HudLayer from './HudLayer.vue'

import { hudLook, parseJarvisStatus } from './hud-model'

const status = parseJarvisStatus({ structuredContent: { ok: true, console_up: true, kernel_up: true, pc_busy: true, pc_kind: 'task', pc_age_s: 5 } })

function render(line?: string) {
  return renderToString(createSSRApp(HudLayer, {
    look: hudLook('subtle'),
    state: 'acting',
    stateLabel: 'ACTING',
    label: 'JARVIS acting',
    link: 'up',
    status,
    statusAt: 0,
    modelLabel: 'GLM-5.3-Flash',
    line,
  }))
}

describe('hudLayer status line (D72)', () => {
  it('draws what JARVIS is doing in its own element, as a polite status, not inside the strip', async () => {
    const html = await render('I am typing into notepad')
    expect(html).toMatch(/<div class="jhud-line" role="status"[^>]*>\s*I am typing into notepad\s*<\/div>/)
    const strip = html.slice(html.indexOf('jhud-strip'), html.indexOf('jhud-line'))
    expect(strip).not.toContain('I am typing into notepad')
  })

  it('keeps the tooltip for a line that wraps, and marks it as painted by the app (the ambient light must not read it as desktop light)', async () => {
    const html = await render('Step 2 of 3: reading the window')
    expect(html).toContain('title="Step 2 of 3: reading the window"')
    expect(html).toMatch(/class="jhud-line"[^>]*data-ambient-light-opaque="true"/)
  })

  it('draws no line when there is nothing to say, and escapes text that is not plain', async () => {
    expect(await render('')).not.toContain('jhud-line')
    expect(await render(undefined)).not.toContain('jhud-line')
    const html = await render('<img src=x onerror=alert(1)>')
    expect(html).not.toContain('<img')
    expect(html).toContain('&lt;img')
  })
})
