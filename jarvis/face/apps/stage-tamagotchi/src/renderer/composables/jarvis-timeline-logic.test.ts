import { describe, expect, it } from 'vitest'

import {
  applyFilter,
  buildRows,
  cleanStep,
  formatClock,
  formatDay,
  formatDuration,
  HEIGHTS,
  keyCommand,
  layoutFor,
  layoutRows,
  parseTimelineWanted,
  parseUndoReport,
  scrollToRow,
  shotDataUrl,
  startOfDay,
  taskLine,
  visibleWindow,
} from './jarvis-timeline-logic'

const NOW = new Date(2026, 9, 26, 15, 30, 0).getTime() / 1000

function rawStep(over: Record<string, unknown> = {}) {
  return {
    id: 's1',
    task_id: 't1',
    ts: NOW - 60,
    step_label: 'Open Calculator',
    tool: 'open_app',
    class: 'B',
    target: 'Calculator',
    result: 'a window for Calculator appeared',
    verified: 'verified',
    duration_ms: 1800,
    undoable: true,
    undo_text: 'Close the window JARVIS opened',
    cannot: '',
    shots: { before: { ref: 'job1-b.jpg', token: 'abc123' }, after: { ref: 'job1-a.jpg', token: 'def456' } },
    ...over,
  }
}

function rawView(tasks: unknown[]) {
  return { structuredContent: { version: 3, open_seq: 1, token: 'panel1', view: { available: true, tasks, summary: { steps: 2, tasks: 1, failed: 1, unconfirmed: 0, undoable: 1, undone: 0 }, now: NOW, screenshots: true } } }
}

const task = (id: string, steps: unknown[], extra: Record<string, unknown> = {}) => ({ task_id: id, goal_label: `goal ${id}`, ts: NOW - 100, last_ts: NOW - 10, steps, failed: 0, unconfirmed: 0, undoable: 1, ...extra })

describe('parsing what the server sends', () => {
  it('reads a view into plain, bounded values', () => {
    const got = parseTimelineWanted(rawView([task('t1', [rawStep(), rawStep({ id: 's2', verified: 'failed', undoable: false, shots: null })])]))
    expect(got).toMatchObject({ version: 3, openSeq: 1, token: 'panel1', unchanged: false })
    expect(got?.view?.tasks[0]).toMatchObject({ id: 't1', title: 'goal t1' })
    expect(got?.view?.tasks[0].steps[0]).toMatchObject({ id: 's1', label: 'Open Calculator', cls: 'B', verified: 'verified', durationMs: 1800, undoable: true })
    expect(got?.view?.tasks[0].steps[0].shots?.before).toEqual({ ref: 'job1-b.jpg', token: 'abc123' })
    expect(got?.view?.tasks[0].steps[1].shots).toBeNull()
  })

  it('knows "unchanged" and "no view yet"', () => {
    expect(parseTimelineWanted({ structuredContent: { version: 4, open_seq: 0, token: 'p', unchanged: true } })).toMatchObject({ unchanged: true, view: null, version: 4 })
    expect(parseTimelineWanted({ structuredContent: { version: 4, open_seq: 2, token: 'p', view: null } })).toMatchObject({ unchanged: false, view: null, openSeq: 2 })
    expect(parseTimelineWanted(undefined)).toBeNull()
    expect(parseTimelineWanted({ isError: true })).toBeNull()
  })

  it('refuses a picture reference that is not one of the journal\'s and a token with odd characters', () => {
    const s = cleanStep(rawStep({ shots: { before: { ref: '../../etc/passwd', token: 'x' }, after: { ref: 'job1-a.jpg', token: '<script>' } } }))
    expect(s?.shots).toBeNull()
    expect(cleanStep(rawStep({ id: '../x' }))).toBeNull()
    expect(cleanStep(null)).toBeNull()
  })

  it('clamps unknown classes and verdicts, and strips control and direction characters from every text', () => {
    const s = cleanStep(rawStep({ class: 'Z', verified: 'great', step_label: 'Open‮\u0000 gpj.exe  now', result: 'x'.repeat(900) }))
    expect(s).toMatchObject({ cls: 'B', verified: 'failed', label: 'Open gpj.exe now' })
    expect(s?.result.length).toBeLessThanOrEqual(200)
  })

  it('keeps markup as text: nothing is interpreted here', () => {
    const s = cleanStep(rawStep({ step_label: '<img src=x onerror=alert(1)>' }))
    expect(s?.label).toBe('<img src=x onerror=alert(1)>')
  })

  it('reads an undo report', () => {
    const r = parseUndoReport({ structuredContent: { ok: false, undone: 1, not_undone: 1, summary: 'Undone: A. Not undone: B (changed).', results: [{ label: 'A', status: 'undone', reason: '' }, { label: 'B', status: 'refused', reason: 'changed' }] } })
    expect(r).toMatchObject({ ok: false, undone: 1, notUndone: 1 })
    expect(r?.results[1]).toEqual({ label: 'B', status: 'refused', reason: 'changed' })
    expect(parseUndoReport({ structuredContent: { ok: false, message: 'That is not a click from the Timeline panel.' } })?.summary).toContain('not a click')
    expect(parseUndoReport(undefined)).toBeNull()
  })

  it('makes a picture URL only from a JPEG of sane size and shape', () => {
    expect(shotDataUrl({ structuredContent: { ok: true, mime: 'image/jpeg', data: '/9j/4AAQ' } })).toBe('data:image/jpeg;base64,/9j/4AAQ')
    expect(shotDataUrl({ structuredContent: { ok: true, mime: 'image/svg+xml', data: 'PHN2Zz4=' } })).toBeUndefined()
    expect(shotDataUrl({ structuredContent: { ok: true, mime: 'image/jpeg', data: 'a b"onerror="x' } })).toBeUndefined()
    expect(shotDataUrl({ structuredContent: { ok: true, mime: 'image/jpeg', data: 'A'.repeat(900_001) } })).toBeUndefined()
    expect(shotDataUrl({ structuredContent: { ok: false, message: 'gone' } })).toBeUndefined()
  })
})

describe('filters, rows and the virtual window', () => {
  const view = parseTimelineWanted(rawView([
    task('t2', [cleanStep(rawStep({ id: 'a', ts: NOW - 20, verified: 'failed', undoable: false }))]),
    task('t1', [
      cleanStep(rawStep({ id: 'b', ts: NOW - 40 })),
      cleanStep(rawStep({ id: 'c', ts: NOW - 3 * 86400, verified: 'failed', undoable: false })),
      cleanStep(rawStep({ id: 'd', ts: NOW - 50, verified: 'unconfirmed', undoable: false })),
    ].map(x => ({ ...x, id: x!.id, task_id: 't1', step_label: x!.label, class: x!.cls, duration_ms: x!.durationMs, verified: x!.verified, ts: x!.ts, undoable: x!.undoable }))),
  ]))!.view!

  it('today keeps this day\'s steps, failed-only keeps the failures, both together keep both rules', () => {
    expect(applyFilter(view.tasks, { today: false, failedOnly: false }, NOW)).toBe(view.tasks)
    const today = applyFilter(view.tasks, { today: true, failedOnly: false }, NOW)
    expect(today.flatMap(t => t.steps.map(s => s.id))).toEqual(['a', 'b', 'd'])
    const failed = applyFilter(view.tasks, { today: false, failedOnly: true }, NOW)
    expect(failed.flatMap(t => t.steps.map(s => s.id))).toEqual(['a', 'c'])
    expect(failed[1]).toMatchObject({ failed: 1, undoable: 0, unconfirmed: 0 })
    const both = applyFilter(view.tasks, { today: true, failedOnly: true }, NOW)
    expect(both.map(t => t.id)).toEqual(['t2'])
  })

  it('formats times, days and durations the way the owner reads them', () => {
    expect(formatClock(new Date(2026, 9, 26, 9, 5).getTime() / 1000)).toBe('09:05')
    expect(startOfDay(NOW)).toBe(new Date(2026, 9, 26).getTime() / 1000)
    expect(formatDay(NOW - 60, NOW)).toBe('Today')
    expect(formatDay(NOW - 86400, NOW)).toBe('Yesterday')
    expect(formatDay(NOW - 4 * 86400, NOW)).toMatch(/^[A-Z][a-z]{2} \d{1,2} [A-Z][a-z]{2}$/)
    expect(formatDuration(0)).toBe('')
    expect(formatDuration(420)).toBe('420 ms')
    expect(formatDuration(1800)).toBe('1.8 s')
    expect(formatDuration(12_400)).toBe('12 s')
    expect(formatDuration(65_000)).toBe('1 m 5 s')
    expect(taskLine({ ...view.tasks[1], failed: 1, unconfirmed: 2 })).toBe('3 steps · 1 failed · 2 unconfirmed')
  })

  it('builds rows newest first with the detail right under the open step, and a collapsed task shows only its header', () => {
    const rows = buildRows(view.tasks, new Set(['b']))
    expect(rows.map(r => r.key)).toEqual(['t:t2', 's:a', 't:t1', 's:b', 'd:b', 's:c', 's:d'])
    expect(buildRows(view.tasks, new Set(), new Set(['t1'])).map(r => r.key)).toEqual(['t:t2', 's:a', 't:t1'])
  })

  it('picks the layout from the window width: the small face window gets the narrow rows', () => {
    expect(layoutFor(252)).toBe('narrow')
    expect(layoutFor(360)).toBe('narrow')
    expect(layoutFor(460)).toBe('narrow')
    expect(layoutFor(520)).toBe('wide')
    expect(layoutFor(560)).toBe('wide')
    expect(layoutFor(0)).toBe('wide')
  })

  function many(n: number) {
    const steps = Array.from({ length: n }, (_, i) => cleanStep(rawStep({ id: `s${i}`, ts: NOW - i })))!
    return buildRows([{ id: 'big', title: 'big', ts: NOW, lastTs: NOW, steps: steps as never, failed: 0, unconfirmed: 0, undoable: n }], new Set(['s5']))
  }

  it('draws only the rows in view, however many there are, and the paddings add up to the whole list', () => {
    const rows = many(3000)
    for (const name of ['wide', 'narrow'] as const) {
      const layout = layoutRows(rows, name)
      expect(layout.total).toBe(HEIGHTS[name].task + 3000 * HEIGHTS[name].step + HEIGHTS[name].detail)
      for (const top of [0, 500, 7777, layout.total - 400, layout.total]) {
        const w = visibleWindow(layout, rows, top, 400)
        const drawn = rows.slice(w.start, w.end).reduce((a, r) => a + HEIGHTS[name][r.kind], 0)
        expect(w.end - w.start).toBeLessThan(40)
        expect(w.padTop + drawn + w.padBottom).toBe(layout.total)
        if (top < layout.total - 1)
          expect(w.end).toBeGreaterThan(w.start)
      }
    }
    expect(visibleWindow(layoutRows([], 'wide'), [], 0, 400)).toEqual({ start: 0, end: 0, padTop: 0, padBottom: 0 })
  })

  it('the first and the last row can always be reached', () => {
    const rows = many(200)
    const layout = layoutRows(rows, 'wide')
    const first = visibleWindow(layout, rows, 0, 300)
    const last = visibleWindow(layout, rows, layout.total - 300, 300)
    expect(first.start).toBe(0)
    expect(last.end).toBe(rows.length)
  })

  it('scrolls a selected row into view and leaves the scroll alone when it is already in view', () => {
    const rows = many(100)
    const layout = layoutRows(rows, 'wide')
    expect(scrollToRow(layout, rows, 'wide', 60, 0, 400)).toBe(layout.offsets[60] + HEIGHTS.wide.step - 400)
    expect(scrollToRow(layout, rows, 'wide', 2, 5000, 400)).toBe(layout.offsets[2])
    expect(scrollToRow(layout, rows, 'wide', 3, 0, 400)).toBe(0)
    expect(scrollToRow(layout, rows, 'wide', -1, 12, 400)).toBe(12)
  })
})

describe('the keyboard', () => {
  const div = { tag: 'DIV' }
  it('moves, jumps, pages, opens, undoes and closes', () => {
    expect(keyCommand('ArrowDown', -1, 10, div)).toEqual({ kind: 'move', to: 0 })
    expect(keyCommand('ArrowDown', 9, 10, div)).toEqual({ kind: 'move', to: 9 })
    expect(keyCommand('ArrowUp', 0, 10, div)).toEqual({ kind: 'move', to: 0 })
    expect(keyCommand('Home', 5, 10, div)).toEqual({ kind: 'move', to: 0 })
    expect(keyCommand('End', 5, 10, div)).toEqual({ kind: 'move', to: 9 })
    expect(keyCommand('PageDown', 5, 10, div)).toEqual({ kind: 'page', dir: 1 })
    expect(keyCommand('Enter', 3, 10, div)).toEqual({ kind: 'toggle' })
    expect(keyCommand(' ', 3, 10, div)).toEqual({ kind: 'toggle' })
    expect(keyCommand('u', 3, 10, div)).toEqual({ kind: 'undo' })
    expect(keyCommand('Escape', 3, 10, div)).toEqual({ kind: 'close' })
  })

  it('leaves buttons and fields to the browser, but Escape always closes', () => {
    expect(keyCommand('Enter', 3, 10, { tag: 'BUTTON' })).toBeNull()
    expect(keyCommand('u', 3, 10, { tag: 'BUTTON' })).toBeNull()
    expect(keyCommand('u', 3, 10, { tag: 'INPUT' })).toBeNull()
    expect(keyCommand('Escape', 3, 10, { tag: 'BUTTON' })).toEqual({ kind: 'close' })
    expect(keyCommand('ArrowDown', 0, 0, div)).toBeNull()
    expect(keyCommand('x', 3, 10, div)).toBeNull()
  })
})
