import { describe, expect, it } from 'vitest'
import { createSSRApp } from 'vue'
// The panel's markup, rendered on the server side of Vue (no browser): what is drawn for a form, that text is escaped, that nothing can be
// a link or a script, and that a long list is virtualized. The behaviour in a real page (focus, keys, scrolling, size, speed) is checked by
// scripts/ask-check.mjs with Chromium.
import { renderToString } from 'vue/server-renderer'

import AskPanel from './AskPanel.vue'

import { useAskController } from '../../composables/use-jarvis-ask'

async function render(form: Record<string, unknown> | null, values: Record<string, unknown> = {}) {
  const controller = useAskController({ call: async () => ({ structuredContent: { version: 1, form: form && { id: 'q1', token: 't', title: 'T', intro: '', busy: false, status: '', progress: null, values, buttons: [{ id: 'ok', label: 'Go', style: 'primary' }, { id: 'cancel', label: 'Cancel', style: 'secondary' }], ...form } } }) })
  await controller.pollOnce()
  return renderToString(createSSRApp(AskPanel, { controller }))
}

describe('askPanel markup', () => {
  it('draws nothing without a form', async () => {
    expect(await render(null)).not.toContain('class="ask"')
  })

  it('draws the title, the intro, the buttons with verbs and a dialog name', async () => {
    const html = await render({ title: 'Which one?', intro: 'Pick one.', fields: [{ type: 'radio', id: 'c', label: 'Choose', variant: 'segmented', options: [{ id: 'a', label: 'Notepad' }, { id: 'b', label: 'WordPad' }] }] }, { c: 'a' })
    expect(html).toContain('role="dialog"')
    expect(html).toContain('aria-label="Which one?"')
    expect(html).toContain('Pick one.')
    expect(html).toContain('role="radiogroup"')
    expect(html).toMatch(/aria-checked="true"[^>]*>\s*Notepad/)
    expect(html).toContain('>Go<')
    expect(html).toContain('>Cancel<')
  })

  it('escapes every text and has no link, frame, script or form element', async () => {
    const evil = '<img src=x onerror=alert(1)><a href="javascript:alert(2)">x</a><script>alert(3)</script>'
    const html = await render({ title: evil, intro: evil, fields: [
      { type: 'callout', text: evil },
      { type: 'info_table', label: evil, rows: [[evil, evil]] },
      { type: 'code_view', text: evil, language: 'html' },
      { type: 'diff_view', before: evil, after: `${evil}!` },
      { type: 'checkbox_group', id: 'g', label: evil, options: [{ id: 'a', label: evil, detail: evil }] },
      { type: 'cards', id: 'k', label: evil, options: [{ id: 'a', label: evil, pros: [evil], cons: [evil] }] },
    ] })
    expect(html).not.toMatch(/<(a|script|iframe|form|object|embed|link|meta|base)[\s>]/i)
    expect(html).not.toContain('<img src=x')
    expect(html).toContain('&lt;img src=x onerror=alert(1)&gt;')
  })

  it('draws a thumbnail only for a real raster data URL', async () => {
    const png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
    const html = await render({ fields: [{ type: 'gallery', id: 'g', label: 'Pictures', options: [
      { id: 'a', label: 'ok.png', thumb: { mime: 'image/png', data: png } },
      { id: 'b', label: 'svg', thumb: { mime: 'image/svg+xml', data: png } },
      { id: 'c', label: 'remote', thumb: { mime: 'image/png', data: 'http://evil.example/x' } },
    ] }] })
    expect(html.match(/<img /g)).toHaveLength(1)
    expect(html).toContain('data:image/png;base64,')
    expect(html).not.toContain('evil.example')
  })

  it('draws only the rows in view of a 450-row list, and nothing of a collapsed section', async () => {
    const options = Array.from({ length: 450 }, (_, i) => ({ id: `o${i}`, label: `Item ${i}`, size: 1024 }))
    const open = await render({ fields: [{ type: 'checkbox_group', id: 'g', label: 'Many', options }] })
    expect(open.match(/<label class="ask-row/g)!.length).toBeLessThan(20)
    expect(open).toContain('Item 0')
    expect(open).not.toContain('Item 449')
    const closed = await render({ fields: [{ type: 'section', label: 'Review', collapsed: true, children: [{ type: 'checkbox_group', id: 'g', label: 'Many', options }] }] })
    expect(closed).toContain('Review')
    expect(closed).not.toContain('Item 0')
    expect(closed).toContain('aria-expanded="false"')
  })

  it('shows the total of the ticked sizes and disables the primary button when nothing is ticked', async () => {
    const fields = [{ type: 'checkbox_group', id: 'g', label: 'Rows', options: [{ id: 'a', label: 'A', size: 3 * 1024 ** 2 }, { id: 'b', label: 'B', size: 1024 ** 3 }] }]
    const some = await render({ fields }, { g: ['a'] })
    expect(some).toContain('3.0 MB')
    expect(some).toContain('selected in 1 item')
    const none = await render({ fields }, { g: [] })
    expect(none).toContain('Nothing selected yet')
    expect(none).toMatch(/class="ask-btn-primary ask-btn"[^>]*disabled/)
  })

  it('shows progress with real numbers while JARVIS works, locks the buttons but Cancel, and a skeleton before anything is known', async () => {
    const working = await render({ busy: true, status: 'Cleaning Browser cache', progress: { done: 5, total: 13, bytes_done: 1024 ** 3, bytes_total: 2 * 1024 ** 3 }, fields: [{ type: 'callout', text: 'x' }] })
    expect(working).toContain('Cleaning Browser cache')
    expect(working).toContain('1.0 GB of 2.0 GB')
    expect(working).toContain('scaleX(0.5)')
    expect(working).toMatch(/class="ask-btn-primary ask-btn"[^>]*disabled/)
    expect(working).not.toMatch(/class="ask-btn-secondary ask-btn"[^>]*disabled/)
    const skeleton = await render({ busy: true, status: 'Looking...', fields: [] })
    expect(skeleton.match(/ask-skel/g)).toHaveLength(4)
  })

  it('names every control for a screen reader', async () => {
    const html = await render({ fields: [
      { type: 'toggle', id: 't', label: 'Night light' },
      { type: 'slider', id: 's', label: 'Volume', min: 0, max: 10, step: 1, value: 3 },
      { type: 'select', id: 'o', label: 'Output', options: [{ id: 'a', label: 'A' }] },
      { type: 'text', id: 'x', label: 'Name' },
      { type: 'progress', label: 'Disk', value: 5, max: 10 },
      { type: 'steps', items: [{ label: 'One', status: 'done' }] },
    ] })
    expect(html).toContain('role="switch"')
    expect(html).toMatch(/aria-labelledby="[^"]+"/)
    expect(html).toMatch(/<label for="[^"]+"[^>]*>Name/)
    expect(html).toContain('role="progressbar"')
    expect(html).toContain('aria-label="Steps"')
    expect(html).toContain('aria-label="Close"')
  })
})
