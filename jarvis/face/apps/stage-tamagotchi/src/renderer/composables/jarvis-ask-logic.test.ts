import type { AskButton, AskNode } from './jarvis-ask-logic'

import { describe, expect, it } from 'vitest'

import {
  barGeometry,
  cleanNode,
  formatSize,
  inputs,
  keyAction,
  lineDiff,
  listRows,
  missingRequired,
  moveIndex,
  nextSort,
  parseAskWanted,
  pieSlices,
  progressFraction,
  progressText,
  selectedTotal,
  setGroup,
  sortRows,
  toggleIn,
  VIRTUAL_FROM,
  windowRange,
} from './jarvis-ask-logic'

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=='

function wanted(form: Record<string, unknown> | null, extra: Record<string, unknown> = {}) {
  return { structuredContent: { version: 3, form, ...extra } }
}

const FORM = { id: 'q1', token: 'tok', title: 'T', intro: 'I', fields: [], buttons: [{ id: 'ok', label: 'OK', style: 'primary' }], values: {} }

describe('parseAskWanted', () => {
  it('reads a form and keeps only what the panel draws', () => {
    const got = parseAskWanted(wanted({ ...FORM, busy: true, status: 'Working', progress: { done: 1, total: 4, label: 'x' }, notes: ['a'], timeout_s: 60, extra: 'ignored' }))!
    expect(got.version).toBe(3)
    expect(got.form).toMatchObject({ id: 'q1', token: 'tok', title: 'T', busy: true, status: 'Working', notes: ['a'], timeoutS: 60 })
    expect(got.form!.progress).toMatchObject({ done: 1, total: 4, label: 'x' })
    expect((got.form as any).extra).toBeUndefined()
  })

  it('says unchanged, and no form', () => {
    expect(parseAskWanted({ structuredContent: { version: 5, unchanged: true } })).toEqual({ version: 5, unchanged: true, form: null })
    expect(parseAskWanted(wanted(null))).toEqual({ version: 3, unchanged: false, form: null })
    expect(parseAskWanted(undefined)).toBeNull()
    expect(parseAskWanted({ isError: true })).toBeNull()
  })

  it('refuses a form with a bad id or token', () => {
    expect(parseAskWanted(wanted({ ...FORM, id: '../x' }))!.form).toBeNull()
    expect(parseAskWanted(wanted({ ...FORM, id: 'a b' }))!.form).toBeNull()
    expect(parseAskWanted(wanted({ ...FORM, token: 'x'.repeat(100) }))!.form).toBeNull()
    expect(parseAskWanted(wanted({ ...FORM, buttons: [{ id: 'bad id!', label: 'x' }, { id: 'go', label: 'Go', style: 'weird' }] }))!.form!.buttons).toEqual([{ id: 'go', label: 'Go', style: 'secondary' }])
  })

  it('draws text as text: control and direction characters go, markup stays as characters', () => {
    const f = parseAskWanted(wanted({ ...FORM, title: 'Hi‮<b>x</b>\u0000\n', intro: 'a​b' }))!.form!
    expect(f.title).toBe('Hi <b>x</b>')
    expect(f.intro).toBe('a b')
  })

  it('turns an unknown component into a plain callout, never an error', () => {
    const f = parseAskWanted(wanted({ ...FORM, fields: [{ type: 'hologram', label: 'Spin it' }, { type: 'callout', text: 'fine' }, 'nonsense', null] }))!.form!
    expect(f.fields).toHaveLength(2)
    expect(f.fields[0]).toMatchObject({ type: 'callout', text: 'Spin it' })
  })

  it('keeps a thumbnail only when it is a real raster data URL', () => {
    const node = cleanNode({ type: 'gallery', id: 'g', options: [{ id: 'a', label: 'a', thumb: { mime: 'image/jpeg', data: PNG } }, { id: 'b', label: 'b', thumb: { mime: 'image/svg+xml', data: PNG } }, { id: 'c', label: 'c', thumb: { mime: 'image/png', data: 'javascript:alert(1)' } }, { id: '../d', label: 'd' }] })!
    expect(node.options!.map(o => [o.id, !!o.thumb])).toEqual([['a', true], ['b', false], ['c', false]])
  })
})

describe('totals, sizes and progress', () => {
  const fields: AskNode[] = [{ type: 'section', label: 's', children: [{ type: 'checkbox_group', id: 'safe', label: '', options: [
    { id: 'a', label: 'A', size: 1024 * 1024, checked: true },
    { id: 'b', label: 'B', size: 2048 },
    { id: 'c', label: 'C', size: 10 },
  ] }] }]

  it('sums the ticked sizes, also inside a section', () => {
    expect(selectedTotal(fields, { safe: ['a', 'b'] })).toEqual({ bytes: 1024 * 1024 + 2048, count: 2 })
    expect(selectedTotal(fields, { safe: [] })).toEqual({ bytes: 0, count: 0 })
    expect(selectedTotal([{ type: 'checkbox_group', id: 'x', label: '', options: [{ id: 'a', label: 'A' }] }], { x: ['a'] })).toBeNull()
  })

  it('formats sizes for people', () => {
    expect(formatSize(0)).toBe('0 B')
    expect(formatSize(2048)).toBe('2 KB')
    expect(formatSize(5.5 * 1024 ** 2)).toBe('5.5 MB')
    expect(formatSize(962 * 1024 ** 2)).toBe('962 MB')
    expect(formatSize(1.7 * 1024 ** 3)).toBe('1.7 GB')
    expect(formatSize('3 files')).toBe('3 files')
    expect(formatSize(Number.NaN)).toBe('0 B')
  })

  it('turns progress into a fraction and words', () => {
    expect(progressFraction({ bytes_done: 50, bytes_total: 200, done: 1, total: 1 })).toBe(0.25)
    expect(progressFraction({ done: 3, total: 4 })).toBe(0.75)
    expect(progressFraction({ done: 9, total: 4 })).toBe(1)
    expect(progressFraction({})).toBeNull()
    expect(progressFraction(null)).toBeNull()
    expect(progressText({ bytes_done: 1024 ** 3, bytes_total: 2 * 1024 ** 3, done: 5, total: 13 })).toBe('1.0 GB of 2.0 GB · 5 of 13')
  })
})

describe('the keyboard', () => {
  const buttons: AskButton[] = [{ id: 'clean', label: 'Clean', style: 'primary' }, { id: 'cancel', label: 'Cancel', style: 'secondary' }]

  it('moves with the arrow keys, wraps in a radio group, stops at the ends in a table', () => {
    expect(moveIndex(0, 'ArrowDown', 3)).toBe(1)
    expect(moveIndex(2, 'ArrowDown', 3)).toBe(0)
    expect(moveIndex(0, 'ArrowUp', 3)).toBe(2)
    expect(moveIndex(2, 'ArrowRight', 3, false)).toBe(2)
    expect(moveIndex(0, 'ArrowLeft', 3, false)).toBe(0)
    expect(moveIndex(1, 'Home', 5)).toBe(0)
    expect(moveIndex(1, 'End', 5)).toBe(4)
    expect(moveIndex(1, 'a', 5)).toBe(1)
    expect(moveIndex(0, 'ArrowDown', 0)).toBe(-1)
  })

  it('enter presses the primary button, but not inside a text area, on a real button or a checkbox', () => {
    expect(keyAction('Enter', buttons, { tag: 'INPUT', type: 'text' })).toEqual({ kind: 'button', id: 'clean' })
    expect(keyAction('Enter', buttons, { tag: 'DIV', role: 'radio' })).toEqual({ kind: 'button', id: 'clean' })
    expect(keyAction('Enter', buttons, { tag: 'BUTTON', role: 'radio' })).toEqual({ kind: 'button', id: 'clean' })
    expect(keyAction('Enter', buttons, { tag: 'TEXTAREA' })).toBeNull()
    expect(keyAction('Enter', buttons, { tag: 'BUTTON' })).toBeNull()
    expect(keyAction('Enter', buttons, { tag: 'INPUT', type: 'checkbox' })).toBeNull()
    expect(keyAction('Enter', [{ id: 'x', label: 'x', style: 'secondary' }], { tag: 'INPUT', type: 'text' })).toBeNull()
  })

  it('escape presses Cancel or Close, else closes the form', () => {
    expect(keyAction('Escape', buttons, { tag: 'DIV' })).toEqual({ kind: 'button', id: 'cancel' })
    expect(keyAction('Escape', [{ id: 'close', label: 'Close', style: 'quiet' }], { tag: 'DIV' })).toEqual({ kind: 'button', id: 'close' })
    expect(keyAction('Escape', [{ id: 'go', label: 'Go', style: 'primary' }], { tag: 'DIV' })).toEqual({ kind: 'close' })
    expect(keyAction('a', buttons, { tag: 'DIV' })).toBeNull()
  })
})

describe('the virtual list', () => {
  it('draws everything up to the threshold', () => {
    expect(windowRange(0, 300, 48, VIRTUAL_FROM)).toEqual({ start: 0, end: VIRTUAL_FROM, padTop: 0, padBottom: 0 })
    expect(windowRange(500, 300, 48, 10)).toMatchObject({ start: 0, end: 10 })
  })

  it('draws only the rows in view for a long list, and the padding keeps the scroll height', () => {
    const total = 450
    const w = windowRange(4800, 336, 48, total)
    expect(w.start).toBeLessThanOrEqual(100)
    expect(w.end - w.start).toBeLessThan(25)
    expect(w.padTop + (w.end - w.start) * 48 + w.padBottom).toBe(total * 48)
    const end = windowRange(total * 48, 336, 48, total)
    expect(end.end).toBe(total)
    expect(end.padBottom).toBe(0)
    expect(windowRange(0, 336, 48, total).start).toBe(0)
  })

  it('puts a header row before each group', () => {
    const rows = listRows([{ id: 'a', label: 'A', group: 'G1', size: 10 }, { id: 'b', label: 'B', group: 'G1', size: 20 }, { id: 'c', label: 'C', group: 'G2' }, { id: 'd', label: 'D' }])
    expect(rows.map(r => r.kind)).toEqual(['header', 'option', 'option', 'header', 'option', 'option'])
    expect(rows[0]).toMatchObject({ group: 'G1', groupBytes: 30, groupCount: 2 })
  })
})

describe('tables', () => {
  const rows = [{ id: 'a', cells: { n: 'Aero', p: '$899', ram: '16 GB' } }, { id: 'b', cells: { n: 'Forge', p: '$1,200', ram: '8 GB' } }, { id: 'c', cells: { n: 'slate', p: '$949', ram: '32 GB' } }]

  it('sorts numbers as numbers (money, units) and words without case', () => {
    expect(sortRows(rows, 'p', 'asc').map(r => r.id)).toEqual(['a', 'c', 'b'])
    expect(sortRows(rows, 'p', 'desc').map(r => r.id)).toEqual(['b', 'c', 'a'])
    expect(sortRows(rows, 'ram', 'asc').map(r => r.id)).toEqual(['b', 'a', 'c'])
    expect(sortRows(rows, 'n', 'asc').map(r => r.id)).toEqual(['a', 'b', 'c'])
    expect(sortRows(rows, null, 'asc')).toBe(rows)
  })

  it('cycles ascending, descending, off', () => {
    let s = nextSort({ column: null, direction: 'asc' }, 'p')
    expect(s).toEqual({ column: 'p', direction: 'asc' })
    s = nextSort(s, 'p')
    expect(s).toEqual({ column: 'p', direction: 'desc' })
    s = nextSort(s, 'p')
    expect(s.column).toBeNull()
    expect(nextSort({ column: 'p', direction: 'desc' }, 'n')).toEqual({ column: 'n', direction: 'asc' })
  })
})

describe('diff and charts', () => {
  it('diffs lines', () => {
    expect(lineDiff('a\nb\nc', 'a\nx\nc').map(d => `${d.kind}:${d.text}`)).toEqual(['same:a', 'del:b', 'add:x', 'same:c'])
    expect(lineDiff('', 'new')).toEqual([{ kind: 'add', text: 'new' }])
    expect(lineDiff('old', '')).toEqual([{ kind: 'del', text: 'old' }])
    expect(lineDiff('same', 'same')).toEqual([{ kind: 'same', text: 'same' }])
  })

  it('keeps bars inside the chart and pie slices adding up', () => {
    const data = { kind: 'bar' as const, labels: ['a', 'b', 'c'], series: [{ name: 's', values: [0, 5, 12] }, { name: 't', values: [3, 3, 3] }] }
    const bars = barGeometry(data, 320, 120)
    expect(bars).toHaveLength(6)
    for (const b of bars) {
      expect(b.x).toBeGreaterThanOrEqual(0)
      expect(b.x + b.w).toBeLessThanOrEqual(320)
      expect(b.y).toBeGreaterThanOrEqual(0)
      expect(b.y + b.h).toBeLessThanOrEqual(120)
    }
    const slices = pieSlices({ kind: 'pie', labels: ['x', 'y', 'z'], series: [{ name: '', values: [1, 1, 2] }] }, 50)
    expect(slices.map(s => Math.round(s.fraction * 100))).toEqual([25, 25, 50])
    expect(slices.every(s => s.path.startsWith('M '))).toBe(true)
    expect(pieSlices({ kind: 'pie', labels: [], series: [{ name: '', values: [0, 0] }] }, 50)).toEqual([])
  })
})

describe('answers', () => {
  it('finds a required input without an answer, also inside tabs', () => {
    const fields: AskNode[] = [{ type: 'tabs', tabs: [{ id: 't', label: 'T', children: [{ type: 'text', id: 'name', label: 'Name', required: true }] }] }, { type: 'toggle', id: 'x', label: 'X', required: true }]
    expect(inputs(fields).map(n => n.id)).toEqual(['name', 'x'])
    expect(missingRequired(fields, { name: '', x: false })?.id).toBe('name')
    expect(missingRequired(fields, { name: 'a', x: false })).toBeNull()
    expect(missingRequired([{ type: 'checkbox_group', id: 'c', label: '', required: true }], { c: [] })?.id).toBe('c')
  })

  it('toggles and sets groups without duplicates', () => {
    expect(toggleIn(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggleIn(['a', 'b'], 'a')).toEqual(['b'])
    expect(toggleIn(undefined, 'a')).toEqual(['a'])
    expect(setGroup(['a'], ['a', 'b', 'c'], true).sort()).toEqual(['a', 'b', 'c'])
    expect(setGroup(['a', 'b', 'x'], ['a', 'b'], false)).toEqual(['x'])
  })
})
