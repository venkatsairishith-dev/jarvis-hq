/* eslint-disable style/max-statements-per-line */
import { describe, expect, it, vi } from 'vitest'

import { JARVIS_TIMELINE_SHOT_TOOL, JARVIS_TIMELINE_UNDO_TOOL, JARVIS_TIMELINE_WANTED_TOOL } from './jarvis-timeline-logic'
import { timelineEnabled, useTimelineController } from './use-jarvis-timeline'

const NOW = new Date(2026, 9, 26, 15, 30, 0).getTime() / 1000

function step(id: string, over: Record<string, unknown> = {}) {
  return { id, task_id: 't1', ts: NOW - 30, step_label: `Step ${id}`, tool: 'open_app', class: 'B', target: '', result: 'ok', verified: 'verified', duration_ms: 500, undoable: false, undo_text: '', cannot: '', shots: null, ...over }
}

function server() {
  const state = { version: 1, openSeq: 0, steps: [step('s1'), step('s2', { verified: 'failed', ts: NOW - 5 * 86400 })], undoReply: { ok: true, undone: 1, not_undone: 0, summary: 'Undone: Step s1.', results: [{ label: 'Step s1', status: 'undone', reason: '' }] }, shot: { ok: true, mime: 'image/jpeg', data: '/9j/4AAQ' } as Record<string, unknown> }
  const calls: Array<{ name: string, args?: Record<string, unknown> }> = []
  const view = () => ({ available: true, tasks: [{ task_id: 't1', goal_label: 'Open Calculator', ts: NOW - 30, last_ts: NOW - 5, steps: state.steps, failed: 1, unconfirmed: 0, undoable: 0 }], summary: { steps: 2, tasks: 1, failed: 1, unconfirmed: 0, undoable: 0, undone: 0 }, now: NOW, screenshots: true })
  const call = vi.fn(async (name: string, args?: Record<string, unknown>) => {
    calls.push({ name, args })
    if (name === JARVIS_TIMELINE_WANTED_TOOL) {
      const wantsView = args?.full === true || state.openSeq > (args?.seen_open as number)
      if ((args?.seen as number) >= state.version && args?.seen !== 0)
        return { structuredContent: { version: state.version, open_seq: state.openSeq, token: 'panel1', unchanged: true } }
      return { structuredContent: { version: state.version, open_seq: state.openSeq, token: 'panel1', view: wantsView ? view() : null } }
    }
    if (name === JARVIS_TIMELINE_SHOT_TOOL)
      return { structuredContent: state.shot }
    if (name === JARVIS_TIMELINE_UNDO_TOOL)
      return { structuredContent: state.undoReply }
    throw new Error(`unexpected ${name}`)
  })
  return { state, calls, call }
}

describe('useTimelineController', () => {
  it('a closed panel asks without the journal; the first answer only sets the baseline', async () => {
    const s = server()
    s.state.openSeq = 3 // an old request from before this page loaded
    const c = useTimelineController({ call: s.call })
    expect(await c.pollOnce()).toBe(true)
    expect(s.calls[0].args).toMatchObject({ seen: 0, wait_s: 20, full: false, seen_open: 1_000_000_000 })
    expect(c.open.value).toBe(false)
    expect(c.view.value).toBeNull()
  })

  it('opens when the server\'s open counter goes up, with the journal in the same answer', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    await c.pollOnce()
    s.state.openSeq = 1
    s.state.version = 2
    await c.pollOnce()
    expect(c.open.value).toBe(true)
    expect(c.view.value?.tasks[0].steps.map(x => x.id)).toEqual(['s1', 's2'])
    expect(c.openedAt.value).toBeGreaterThan(0)
    expect(c.tasks.value[0].title).toBe('Open Calculator')
  })

  it('an open panel asks for the full view and follows new steps; unchanged changes nothing', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    await c.pollOnce()
    c.setOpen(true)
    s.state.version = 2
    await c.pollOnce()
    expect(s.calls.at(-1)?.args).toMatchObject({ seen: 1, full: true })
    const first = c.view.value
    expect(first?.tasks[0].steps).toHaveLength(2)
    expect(await c.pollOnce()).toBe(true)
    expect(c.view.value).toBe(first)
  })

  it('the keyboard opens it with the journal at once, even while a long poll is waiting', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    await c.pollOnce()
    c.toggle()
    expect(c.open.value).toBe(true)
    await vi.waitFor(() => expect(c.view.value).not.toBeNull())
    expect(s.calls.at(-1)?.args).toMatchObject({ seen: 0, wait_s: 0, full: true })
    c.toggle()
    expect(c.open.value).toBe(false)
  })

  it('does nothing while switched off or while the page is hidden', async () => {
    const s = server()
    expect(await useTimelineController({ call: s.call, enabled: () => false }).pollOnce()).toBe(false)
    expect(await useTimelineController({ call: s.call, paused: () => true }).pollOnce()).toBe(false)
    expect(s.call).not.toHaveBeenCalled()
    expect(timelineEnabled({ getItem: () => 'off' })).toBe(false)
    expect(timelineEnabled({ getItem: () => null })).toBe(true)
    expect(timelineEnabled(undefined)).toBe(true)
    expect(timelineEnabled({ getItem: () => { throw new Error('blocked') } })).toBe(true)
  })

  it('a failing server backs off and reports once per try', async () => {
    const onError = vi.fn()
    const c = useTimelineController({ call: vi.fn(async () => { throw new Error('down') }), onError })
    expect(await c.pollOnce()).toBe(false)
    expect(onError).toHaveBeenCalledTimes(1)
  })

  it('filters are applied to what the server sent', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    await c.pollOnce()
    c.setOpen(true)
    await c.refresh()
    expect(c.tasks.value[0].steps).toHaveLength(2)
    c.setFilter({ today: true })
    expect(c.tasks.value[0].steps.map(x => x.id)).toEqual(['s1'])
    c.setFilter({ today: false, failedOnly: true })
    expect(c.tasks.value[0].steps.map(x => x.id)).toEqual(['s2'])
  })

  it('opens a step and a task, one at a time', () => {
    const c = useTimelineController({ call: server().call })
    c.toggleStep('s1')
    expect(c.expanded.value.has('s1')).toBe(true)
    c.toggleStep('s1')
    expect(c.expanded.value.has('s1')).toBe(false)
    c.toggleTask('t1')
    expect(c.collapsedTasks.value.has('t1')).toBe(true)
  })

  it('fetches a picture once, shows a skeleton while it loads, and remembers a failure', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    await c.pollOnce()
    const shot = { ref: 'job1-b.jpg', token: 'tok' }
    const p = c.loadShot(shot)
    expect(c.shots.value['job1-b.jpg']).toBe('')
    await p
    expect(c.shots.value['job1-b.jpg']).toBe('data:image/jpeg;base64,/9j/4AAQ')
    await c.loadShot(shot)
    expect(s.calls.filter(x => x.name === JARVIS_TIMELINE_SHOT_TOOL)).toHaveLength(1)
    expect(s.calls.find(x => x.name === JARVIS_TIMELINE_SHOT_TOOL)?.args).toEqual({ ref: 'job1-b.jpg', token: 'tok', panel: 'panel1' })
    s.state.shot = { ok: false, message: 'deleted' }
    await c.loadShot({ ref: 'job2-a.jpg', token: 'tok2' })
    expect(c.shots.value['job2-a.jpg']).toBe('!')
  })

  it('keeps at most 40 pictures', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    await c.pollOnce()
    for (let i = 0; i < 45; i++)
      await c.loadShot({ ref: `job${i}-a.jpg`, token: 't' })
    expect(Object.keys(c.shots.value).length).toBe(40)
    expect(c.shots.value['job0-a.jpg']).toBeUndefined()
    expect(c.shots.value['job44-a.jpg']).toContain('data:image/jpeg')
  })

  it('does not ask for a picture before it has the panel token', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    await c.loadShot({ ref: 'job1-b.jpg', token: 'tok' })
    expect(s.call).not.toHaveBeenCalled()
  })

  it('undo sends exactly what was clicked with the panel token, shows the report, and refreshes', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    await c.pollOnce()
    const rep = await c.undo({ id: 's1' })
    expect(rep).toMatchObject({ ok: true, undone: 1 })
    expect(s.calls.find(x => x.name === JARVIS_TIMELINE_UNDO_TOOL)?.args).toEqual({ panel: 'panel1', ids: ['s1'] })
    expect(c.report.value?.summary).toBe('Undone: Step s1.')
    await c.undo({ task: 't1' })
    await c.undo({ n: 3 })
    const sent = s.calls.filter(x => x.name === JARVIS_TIMELINE_UNDO_TOOL).map(x => x.args)
    expect(sent[1]).toEqual({ panel: 'panel1', task: 't1' })
    expect(sent[2]).toEqual({ panel: 'panel1', n: 3 })
    expect(c.undoing.value.size).toBe(0)
    c.dismissReport()
    expect(c.report.value).toBeNull()
  })

  it('a second click on the same Undo while it runs is ignored', async () => {
    const s = server()
    let release!: () => void
    const base = s.call.getMockImplementation()!
    s.call.mockImplementation(async (name, args) => {
      if (name === JARVIS_TIMELINE_UNDO_TOOL)
        await new Promise<void>((resolve) => { release = resolve })
      return base(name, args)
    })
    const c = useTimelineController({ call: s.call })
    await c.pollOnce()
    const first = c.undo({ id: 's1' })
    expect(c.undoing.value.has('s1')).toBe(true)
    expect(await c.undo({ id: 's1' })).toBeNull()
    release()
    await first
    expect(s.calls.filter(x => x.name === JARVIS_TIMELINE_UNDO_TOOL)).toHaveLength(1)
  })

  it('an undo that gets no answer says so, without a report', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    await c.pollOnce()
    s.call.mockImplementation(async (name) => {
      if (name === JARVIS_TIMELINE_UNDO_TOOL)
        throw new Error('down')
      return { structuredContent: { version: 1, open_seq: 0, token: 'panel1', unchanged: true } }
    })
    expect(await c.undo({ id: 's1' })).toBeNull()
    expect(c.error.value).toBe('JARVIS did not answer.')
  })

  it('there is no undo before the panel token is known', async () => {
    const s = server()
    const c = useTimelineController({ call: s.call })
    expect(await c.undo({ id: 's1' })).toBeNull()
    expect(s.call).not.toHaveBeenCalled()
  })

  it('start polls and stop ends the timer', async () => {
    const s = server()
    const timers: Array<() => void> = []
    const cleared: unknown[] = []
    const c = useTimelineController({ call: s.call, setTimer: (fn) => { timers.push(fn); return timers.length }, clearTimer: h => cleared.push(h) })
    c.start()
    await vi.waitFor(() => expect(timers.length).toBe(1))
    c.stop()
    expect(cleared).toEqual([1])
  })
})
