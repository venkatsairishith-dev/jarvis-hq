import { describe, expect, it, vi } from 'vitest'

import {
  cleanPreviewHtml,
  fenceCode,
  formatSize,
  HTML_FRAME_ATTRS,
  HTML_FRAME_CSP,
  htmlFrameDocument,
  imageDataUrl,
  isDataImageSrc,
  isPreviewId,
  movedIndex,
  parseOutputs,
  parsePreview,
  parseWanted,
  stopLinkNavigation,
  stripCssLoads,
} from './jarvis-preview-logic'

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='
const ok = (data: Record<string, unknown>) => ({ structuredContent: { ok: true, id: 'abc_DEF-1', title: 'Q3 deck', kind: 'deck', mime: 'application/x', size: 1234, view: 'none', ...data } })

describe('ids and images', () => {
  it('accepts only the URL-safe ids the server hands out', () => {
    expect(isPreviewId('MjAyNi0xMC0yNC9hLnBuZw')).toBe(true)
    for (const bad of ['', '../x.png', 'a/b', 'a b', 'a.png', 'C:\\x', '%2e%2e', 'a'.repeat(601), 5, null, undefined])
      expect(isPreviewId(bad)).toBe(false)
  })

  it('makes a data URL only for four raster types with plain base64', () => {
    expect(imageDataUrl({ mime: 'image/png', data: PNG })).toBe(`data:image/png;base64,${PNG}`)
    for (const mime of ['image/jpeg', 'image/gif', 'image/webp'])
      expect(imageDataUrl({ mime, data: PNG })).not.toBeNull()
    // svg can carry script, html and pdf are not images, a made-up type is not on the list
    for (const mime of ['image/svg+xml', 'text/html', 'application/pdf', 'image/png; charset=x', 'image/x-icon', ''])
      expect(imageDataUrl({ mime, data: PNG })).toBeNull()
    // the data part cannot smuggle a second URL, quotes, spaces or an oversize blob
    for (const data of ['', 'a b', `${PNG}"onerror="x`, 'AA;base64,AA', '<svg>', 'A'.repeat(9_000_001)])
      expect(imageDataUrl({ mime: 'image/png', data })).toBeNull()
    expect(imageDataUrl(undefined)).toBeNull()
  })

  it('only lets an img keep a src that is its own png, jpeg, gif or webp bytes', () => {
    expect(isDataImageSrc(`data:image/png;base64,${PNG}`)).toBe(true)
    for (const bad of ['https://evil.example/x.png', 'http://127.0.0.1/x.png', '//evil.example/x.png', 'file:///etc/passwd', 'data:image/svg+xml;base64,PHN2Zz4=', 'data:text/html;base64,PGI+', 'javascript:alert(1)', 'x.png'])
      expect(isDataImageSrc(bad)).toBe(false)
  })
})

describe('parseOutputs', () => {
  it('reads the list from structured content or JSON text and drops rows it cannot trust', () => {
    const items = [
      { id: 'abc', title: 'Q3   deck', kind: 'deck', mime: 'm', size: 10, modified: 5, passed: true, pages: 3, preview: 'pages' },
      { id: '../etc/passwd', title: 'bad id', kind: 'text' },
      { id: 'def', title: 'bad kind', kind: 'executable' },
      { id: 'ghi', title: 'x'.repeat(500), kind: 'image', passed: 'yes' },
    ]
    const out = parseOutputs({ structuredContent: { items } })
    expect(out.map(i => i.id)).toEqual(['abc', 'ghi'])
    expect(out[0]).toMatchObject({ title: 'Q3 deck', passed: true, pages: 3 })
    expect(out[1].title).toHaveLength(120)
    expect(out[1].passed).toBeNull()
    expect(parseOutputs({ content: [{ type: 'text', text: JSON.stringify({ items: [items[0]] }) }] })).toHaveLength(1)
  })

  it('returns nothing for errors and junk', () => {
    expect(parseOutputs({ isError: true, structuredContent: { items: [{ id: 'a', kind: 'text' }] } })).toEqual([])
    expect(parseOutputs({ content: [{ type: 'text', text: 'nope' }] })).toEqual([])
    expect(parseOutputs(undefined)).toEqual([])
    expect(parseOutputs({ structuredContent: { items: 'x' } })).toEqual([])
  })

  it('reads the ids that jarvis_show queued, ids only', () => {
    expect(parseWanted({ structuredContent: { ids: ['abc', '../x', 'def'] } })).toEqual(['abc', 'def'])
    expect(parseWanted({ structuredContent: { ids: 'abc' } })).toEqual([])
    expect(parseWanted({ structuredContent: { ids: ['a', 'b', 'c', 'd', 'e', 'f', 'g'] } })).toHaveLength(5)
  })
})

describe('parsePreview', () => {
  it('turns a refusal into its plain message', () => {
    expect(parsePreview({ structuredContent: { ok: false, message: 'links are not followed; that file is not shown' } })).toEqual({ ok: false, message: 'links are not followed; that file is not shown' })
    expect(parsePreview({ isError: true })).toMatchObject({ ok: false })
    expect(parsePreview(undefined)).toMatchObject({ ok: false })
    expect(parsePreview({ content: [{ type: 'text', text: 'oops' }] })).toMatchObject({ ok: false })
  })

  it('refuses an answer with a bad id, kind or view', () => {
    expect(parsePreview(ok({ id: '../x' }))).toMatchObject({ ok: false })
    expect(parsePreview(ok({ kind: 'exe' }))).toMatchObject({ ok: false })
    expect(parsePreview(ok({ view: 'script' }))).toMatchObject({ ok: false })
  })

  it('accepts an image answer only with an image it will draw', () => {
    const good = parsePreview(ok({ kind: 'image', view: 'image', image: { mime: 'image/png', data: PNG }, open_path: '/x/a.png' }))
    expect(good.ok && good.preview.image?.mime).toBe('image/png')
    expect(good.ok && good.preview.openPath).toBe('/x/a.png')
    expect(parsePreview(ok({ view: 'image', image: { mime: 'image/svg+xml', data: PNG } }))).toMatchObject({ ok: false })
    expect(parsePreview(ok({ view: 'image' }))).toMatchObject({ ok: false })
  })

  it('reads pages, keeps only drawable thumbnails and clamps the numbers', () => {
    const r = parsePreview(ok({
      view: 'pages',
      image: { mime: 'image/png', data: PNG },
      page: -4,
      page_count: 0,
      thumbs: [{ page: 0, mime: 'image/png', data: PNG }, { page: 1, mime: 'text/html', data: PNG }, { page: 2, mime: 'image/png', data: '<b>' }],
    }))
    expect(r.ok && r.preview).toMatchObject({ page: 0, pageCount: 1 })
    expect(r.ok && r.preview.thumbs).toHaveLength(1)
  })

  it('reads text with a safe language name and html as source', () => {
    const t = parsePreview(ok({ kind: 'text', view: 'text', text: 'hello', language: 'py thon"><x', truncated: true }))
    expect(t.ok && t.preview).toMatchObject({ text: 'hello', language: 'text', truncated: true })
    const h = parsePreview(ok({ kind: 'html', view: 'html', html: '<p>x</p>' }))
    expect(h.ok && h.preview.html).toBe('<p>x</p>')
  })

  it('says what to do when a type has no preview', () => {
    const r = parsePreview(ok({ view: 'none', message: 'There is no preview for this kind of document here; open it in its app.' }))
    expect(r.ok && r.preview.message).toContain('open it in its app')
  })
})

describe('code blocks', () => {
  it('fences text longer than any backtick run inside it', () => {
    expect(fenceCode('print(1)\n', 'python')).toBe('```python\nprint(1)\n```')
    const tricky = 'a\n```\nb\n````\nc'
    const fenced = fenceCode(tricky, 'md')
    expect(fenced.startsWith('`````md\n')).toBe(true)
    expect(fenced.endsWith('\n`````')).toBe(true)
  })

  it('never lets a language name break out of the fence line', () => {
    expect(fenceCode('x', 'python\n<script>')).toBe('```text\nx\n```')
  })
})

describe('css and html clean-up', () => {
  it('removes every css load that is not an inline image', () => {
    expect(stripCssLoads('@import url(https://evil.example/a.css); p{color:red}')).toBe(' p{color:red}')
    expect(stripCssLoads('a{background:url(https://evil.example/x.png)}')).toBe('a{background:none}')
    expect(stripCssLoads('a{background:url("//evil.example/x.png")}')).toBe('a{background:none}')
    expect(stripCssLoads('a{background:url(\'x.png\')}')).toBe('a{background:none}')
    expect(stripCssLoads('a{background:url(data:image/png;base64,AAAA)}')).toContain('data:image/png')
    expect(stripCssLoads('a{background:image-set(url(x.png) 1x)}')).toBe('a{background:none}')
  })

  it('runs the purifier with the strict settings and removes its hooks afterwards', () => {
    const hooks: string[] = []
    const removed: string[] = []
    const sanitize = vi.fn((source: string) => `<p>${source.length}</p>`)
    const purify = { sanitize, addHook: (n: string) => hooks.push(n), removeHook: (n: string) => removed.push(n) }
    expect(cleanPreviewHtml('<b>x</b>', purify)).toBe('<p>8</p>')
    const config = (sanitize.mock.calls[0] as unknown as [string, { FORBID_TAGS: string[], FORBID_ATTR: string[] }])[1]
    for (const tag of ['script', 'meta', 'base', 'link', 'iframe', 'object', 'embed', 'form'])
      expect(config.FORBID_TAGS).toContain(tag)
    for (const attr of ['href', 'xlink:href', 'srcset', 'target', 'action', 'formaction'])
      expect(config.FORBID_ATTR).toContain(attr)
    expect(hooks.sort()).toEqual(removed.sort())
  })

  it('removes its hooks even when the purifier throws', () => {
    const removed: string[] = []
    const boom = () => {
      throw new Error('boom')
    }
    const purify = { sanitize: boom, addHook: () => {}, removeHook: (n: string) => removed.push(n) }
    expect(() => cleanPreviewHtml('<b>', purify)).toThrow('boom')
    expect(removed.length).toBeGreaterThan(0)
  })

  it('hooks drop a src that is not an inline image and a style that loads something', () => {
    const hooks = new Map<string, (node: any, data: any) => void>()
    const purify = { sanitize: () => '', addHook: (n: string, h: (node: any, data: any) => void) => hooks.set(n, h), removeHook: () => {} }
    cleanPreviewHtml('', purify)
    const attr = hooks.get('uponSanitizeAttribute')!
    const remote = { attrName: 'src', attrValue: 'https://evil.example/x.png', keepAttr: true }
    attr({ nodeName: 'IMG' }, remote)
    expect(remote.keepAttr).toBe(false)
    const own = { attrName: 'src', attrValue: `data:image/png;base64,${PNG}`, keepAttr: true }
    attr({ nodeName: 'IMG' }, own)
    expect(own.keepAttr).toBe(true)
    const onAudio = { attrName: 'src', attrValue: `data:image/png;base64,${PNG}`, keepAttr: true }
    attr({ nodeName: 'SCRIPT' }, onAudio)
    expect(onAudio.keepAttr).toBe(false)
    const style = { attrName: 'style', attrValue: 'color:red;background:url(https://evil.example/x)', keepAttr: true }
    attr({ nodeName: 'DIV' }, style)
    expect(style.attrValue).toBe('color:red;background:none')
    const replaced: string[] = []
    const img = (src: string | null, alt: string | null) => ({
      nodeName: 'IMG',
      getAttribute: (n: string) => (n === 'src' ? src : alt),
      ownerDocument: { createTextNode: (t: string) => t },
      replaceWith: (t: string) => replaced.push(t),
    })
    hooks.get('afterSanitizeAttributes')!(img(null, 'remote picture'), undefined)
    hooks.get('afterSanitizeAttributes')!(img(`data:image/png;base64,${PNG}`, 'mine'), undefined)
    expect(replaced).toEqual(['[image: remote picture]'])
    const sheet = { nodeName: 'STYLE', textContent: '@import "x.css"; b{color:blue}' }
    hooks.get('afterSanitizeElements')!(sheet, undefined)
    expect(sheet.textContent).not.toContain('@import')
  })

  it('builds a frame document with the policy before any content', () => {
    const purify = { sanitize: () => '<p>hi</p>', addHook: () => {}, removeHook: () => {} }
    const doc = htmlFrameDocument('<p>hi</p>', purify)
    expect(doc.indexOf('Content-Security-Policy')).toBeGreaterThan(-1)
    expect(doc.indexOf('Content-Security-Policy')).toBeLessThan(doc.indexOf('<p>hi</p>'))
    expect(doc).toContain(HTML_FRAME_CSP)
    expect(doc).not.toMatch(/<script|<link|<base|<iframe/i)
  })

  it('has a frame policy that loads nothing and a sandbox with no permissions', () => {
    expect(HTML_FRAME_CSP).toContain('default-src \'none\'')
    expect(HTML_FRAME_CSP).not.toMatch(/script-src|connect-src|frame-src|https?:|\*/)
    expect(HTML_FRAME_ATTRS.sandbox).toBe('')
    expect(HTML_FRAME_ATTRS.referrerpolicy).toBe('no-referrer')
  })
})

describe('links and paging', () => {
  it('stops a click on a link, or on anything inside one', () => {
    const preventDefault = vi.fn()
    const stopPropagation = vi.fn()
    const inLink = { closest: (s: string) => s.includes('a') ? {} : null }
    expect(stopLinkNavigation({ target: inLink, preventDefault, stopPropagation })).toBe(true)
    expect(preventDefault).toHaveBeenCalled()
    expect(stopLinkNavigation({ target: { closest: () => null }, preventDefault, stopPropagation })).toBe(false)
    expect(stopLinkNavigation({ target: null, preventDefault, stopPropagation })).toBe(false)
  })

  it('moves inside the bounds', () => {
    expect(movedIndex(0, -1, 5)).toBe(0)
    expect(movedIndex(4, 1, 5)).toBe(4)
    expect(movedIndex(2, 1, 5)).toBe(3)
    expect(movedIndex(0, 1, 0)).toBe(0)
  })

  it('formats sizes', () => {
    expect(formatSize(12)).toBe('12 B')
    expect(formatSize(2048)).toBe('2.0 KB')
    expect(formatSize(300 * 1024)).toBe('300 KB')
    expect(formatSize(5 * 1024 * 1024)).toBe('5.0 MB')
  })
})
