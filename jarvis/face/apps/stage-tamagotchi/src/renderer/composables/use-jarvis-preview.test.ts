import { describe, expect, it, vi } from 'vitest'

import { JARVIS_OPEN_OUTPUT_TOOL, JARVIS_OUTPUT_GET_TOOL, JARVIS_OUTPUTS_TOOL, JARVIS_WANTED_TOOL } from './jarvis-preview-logic'
import { previewEnabled, usePreviewController } from './use-jarvis-preview'

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
const ITEMS = [
  { id: 'deck1', title: 'Q3 deck', kind: 'deck', mime: 'm', size: 1, modified: 3, passed: true, pages: 3 },
  { id: 'rep1', title: 'Flights', kind: 'pdf', mime: 'application/pdf', size: 1, modified: 2, passed: false, pages: null },
  { id: 'img1', title: 'Chart', kind: 'image', mime: 'image/png', size: 1, modified: 1, passed: null, pages: null },
]

function pages(id: string, page: number, thumbs: boolean) {
  return {
    structuredContent: {
      ok: true,
      id,
      title: id,
      kind: 'deck',
      mime: 'm',
      size: 1,
      view: 'pages',
      page,
      page_count: 3,
      open_path: `/out/${id}.pptx`,
      image: { mime: 'image/png', data: PNG },
      ...(thumbs ? { thumbs: [0, 1, 2].map(p => ({ page: p, mime: 'image/png', data: PNG })) } : {}),
    },
  }
}

function fakeServer(wanted: string[][] = []) {
  const calls: Array<{ name: string, args?: Record<string, unknown> }> = []
  const queue = [...wanted]
  const call = vi.fn(async (name: string, args?: Record<string, unknown>) => {
    calls.push({ name, args })
    if (name === JARVIS_OUTPUTS_TOOL)
      return { structuredContent: { items: ITEMS } }
    if (name === JARVIS_WANTED_TOOL)
      return { structuredContent: { ids: queue.shift() ?? [] } }
    if (name === JARVIS_OUTPUT_GET_TOOL) {
      const id = String(args?.id)
      if (id === 'deck1')
        return pages(id, Number(args?.page ?? 0), args?.thumbs === true)
      if (id === 'bad1')
        return { structuredContent: { ok: false, message: 'links are not followed; that file is not shown' } }
      return { structuredContent: { ok: true, id, title: id, kind: 'text', mime: 'text/markdown', size: 4, view: 'text', language: 'markdown', text: '# Hi', open_path: `/out/${id}.md` } }
    }
    if (name === JARVIS_OPEN_OUTPUT_TOOL)
      return { structuredContent: { result: { opened: args?.path } } }
    throw new Error(`unexpected ${name}`)
  })
  return { call, calls }
}

describe('usePreviewController', () => {
  it('opens on the newest item and lists the rest', async () => {
    const s = fakeServer()
    const c = usePreviewController({ call: s.call })
    await c.openPanel()
    expect(c.open.value).toBe(true)
    expect(c.items.value.map(i => i.id)).toEqual(['deck1', 'rep1', 'img1'])
    expect(c.current.value).toMatchObject({ id: 'deck1', view: 'pages', page: 0, pageCount: 3 })
    expect(c.current.value?.thumbs).toHaveLength(3)
  })

  it('pages through a deck without fetching thumbnails again, then moves on to the next item', async () => {
    const s = fakeServer()
    const c = usePreviewController({ call: s.call })
    await c.openPanel()
    await c.next()
    await c.next()
    expect(c.page.value).toBe(2)
    expect(c.current.value?.thumbs).toHaveLength(3)
    const gets = s.calls.filter(x => x.name === JARVIS_OUTPUT_GET_TOOL)
    expect(gets.map(g => [g.args?.page, g.args?.thumbs])).toEqual([[0, true], [1, false], [2, false]])
    await c.next() // last page: on to the next thing in the list
    expect(c.current.value?.id).toBe('rep1')
    await c.previous() // first page of this one: back to the previous thing
    expect(c.current.value?.id).toBe('deck1')
  })

  it('clamps paging at both ends of the list', async () => {
    const s = fakeServer()
    const c = usePreviewController({ call: s.call })
    await c.openPanel()
    await c.previous()
    await c.previousItem()
    expect(c.current.value?.id).toBe('deck1')
    await c.show('img1')
    await c.nextItem()
    expect(c.current.value?.id).toBe('img1')
  })

  it('shows a refusal in words and keeps the panel usable', async () => {
    const c = usePreviewController({ call: fakeServer().call })
    await c.refresh()
    expect(await c.show('bad1')).toBe(false)
    expect(c.error.value).toBe('links are not followed; that file is not shown')
    expect(c.open.value).toBe(true)
    expect(await c.show('deck1')).toBe(true)
    expect(c.error.value).toBe('')
  })

  it('never sends an id that is not an id from the server', async () => {
    const s = fakeServer()
    const c = usePreviewController({ call: s.call })
    for (const bad of ['../../etc/passwd', 'C:\\x.png', 'a/b', ''])
      expect(await c.show(bad)).toBe(false)
    expect(s.call).not.toHaveBeenCalled()
  })

  it('ignores a slow answer that arrives after a newer request', async () => {
    let release!: () => void
    const slow = new Promise<void>(r => (release = r))
    const call = vi.fn(async (name: string, args?: Record<string, unknown>) => {
      if (name === JARVIS_OUTPUT_GET_TOOL && args?.id === 'slow1') {
        await slow
        return { structuredContent: { ok: true, id: 'slow1', title: 'Slow', kind: 'text', mime: 't', size: 1, view: 'text', text: 'late', language: 'text' } }
      }
      return fakeServer().call(name, args)
    })
    const c = usePreviewController({ call })
    const first = c.show('slow1')
    await c.show('img1')
    release()
    await first
    expect(c.current.value?.id).toBe('img1')
  })

  it('closes, and a late answer does not reopen a closed panel into loading', async () => {
    const c = usePreviewController({ call: fakeServer().call })
    await c.openPanel()
    c.close()
    expect(c.open.value).toBe(false)
    expect(c.loading.value).toBe(false)
  })

  it('hands "open in app" to the deliverables open_output entry with the path the server named', async () => {
    const s = fakeServer()
    const c = usePreviewController({ call: s.call })
    await c.show('img1')
    expect(await c.openInApp()).toBe(true)
    expect(s.calls.at(-1)).toEqual({ name: JARVIS_OPEN_OUTPUT_TOOL, args: { path: '/out/img1.md' } })
    expect(c.note.value).toBe('Opened in its app.')
  })

  it('says so when the app could not open it, and when there is nothing open', async () => {
    const call = vi.fn(async (name: string) => name === JARVIS_OPEN_OUTPUT_TOOL ? { isError: true } : { structuredContent: { items: [] } })
    const c = usePreviewController({ call })
    expect(await c.openInApp()).toBe(false)
    expect(call).not.toHaveBeenCalled()
    c.current.value = { id: 'a', title: 'a', kind: 'text', mime: 't', size: 1, view: 'none', openPath: '/x/a.md' }
    expect(await c.openInApp()).toBe(false)
    expect(c.note.value).toContain('could not open')
  })

  it('opens what the chat model queued with jarvis_show, newest last', async () => {
    const s = fakeServer([['img1', 'deck1']])
    const c = usePreviewController({ call: s.call })
    expect(await c.pollWanted()).toBe(2)
    expect(c.open.value).toBe(true)
    expect(c.current.value?.id).toBe('deck1')
    expect(await c.pollWanted()).toBe(0)
  })

  it('does not ask when switched off, and backs off while the server does not answer', async () => {
    const off = fakeServer()
    expect(await usePreviewController({ call: off.call, enabled: () => false }).pollWanted()).toBe(0)
    expect(off.call).not.toHaveBeenCalled()

    const timers: number[] = []
    let fire: (() => void) | undefined
    const call = vi.fn(async () => {
      throw new Error('down')
    })
    const onError = vi.fn()
    const c = usePreviewController({
      call,
      onError,
      pollMs: 1000,
      setTimer: (fn, ms) => {
        timers.push(ms)
        fire = fn
        return 1
      },
      clearTimer: () => {},
    })
    c.start()
    for (let i = 0; i < 4; i++) {
      fire!()
      await vi.waitFor(() => expect(call).toHaveBeenCalledTimes(i + 1))
      await vi.waitFor(() => expect(timers.length).toBe(i + 2))
    }
    expect(timers).toEqual([1000, 2000, 4000, 8000, 16000])
    c.stop()
  })
})

describe('previewEnabled', () => {
  it('is on unless the owner set jarvis:preview to off, and on when storage fails', () => {
    expect(previewEnabled({ getItem: () => null })).toBe(true)
    expect(previewEnabled({ getItem: k => (k === 'jarvis:preview' ? 'off' : null) })).toBe(false)
    const denied = () => {
      throw new Error('denied')
    }
    expect(previewEnabled({ getItem: denied })).toBe(true)
    expect(previewEnabled(undefined)).toBe(true)
  })
})
