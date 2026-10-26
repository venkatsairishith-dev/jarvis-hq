/**
 * The Timeline panel's rules (D61), as plain functions: what the tool server's answer looks like (jarvis/bridge/timeline.py), how steps are grouped,
 * filtered and laid out as one virtual list, how times and durations read, and what the keyboard does. No state and no tool calls here
 * (use-jarvis-timeline.ts has those).
 *
 * Everything the server sends is data to draw as text. A picture is shown only as a `data:image/jpeg` URL of the base64 the server returned,
 * and only after its shape is checked; nothing in a step is ever used as markup, a link or a path.
 */
import type { ToolResult } from './jarvis-preview-logic'

import { toolData } from './jarvis-preview-logic'

export const JARVIS_TIMELINE_WANTED_TOOL = 'jarvis::jarvis_timeline_wanted'
export const JARVIS_TIMELINE_SHOT_TOOL = 'jarvis::jarvis_timeline_shot'
export const JARVIS_TIMELINE_UNDO_TOOL = 'jarvis::jarvis_timeline_undo'

export type Verified = 'verified' | 'unconfirmed' | 'failed'
export type StepClass = 'A' | 'B' | 'C'

export interface TlShot { ref: string, token: string }
export interface TlStep {
  id: string
  taskId: string
  ts: number
  label: string
  tool: string
  cls: StepClass
  target: string
  result: string
  verified: Verified
  durationMs: number
  undoable: boolean
  undoText: string
  cannot: string
  undone: { status: string, ts: number } | null
  tried: { status: string, ts: number } | null
  isUndo: boolean
  shots: { before?: TlShot, after?: TlShot } | null
}
export interface TlTask {
  id: string
  title: string
  ts: number
  lastTs: number
  steps: TlStep[]
  failed: number
  unconfirmed: number
  undoable: number
}
export interface TlSummary { steps: number, tasks: number, failed: number, unconfirmed: number, undoable: number, undone: number }
export interface TlView {
  available: boolean
  reason: string
  tasks: TlTask[]
  summary: TlSummary
  now: number
  screenshots: boolean
}
export interface TlWanted { version: number, openSeq: number, token: string, unchanged: boolean, view: TlView | null }
export interface UndoReport {
  ok: boolean
  undone: number
  notUndone: number
  summary: string
  results: Array<{ label: string, status: string, reason: string }>
}

const ID = /^[\w.:-]{1,64}$/
const REF = /^[A-Za-z0-9][\w-]{2,70}\.jpg$/
const TOKEN = /^[\w-]{1,64}$/
const BASE64 = /^[A-Z0-9+/]+={0,2}$/i
export const MAX_SHOT_BASE64 = 900_000

// control characters, zero-width and direction marks and the byte-order mark: written as escapes in a string so the source holds no invisible character
// eslint-disable-next-line prefer-regex-literals -- a literal would need the invisible characters themselves, or ranges the linter calls obscure
const INVISIBLE = new RegExp('[\\u0000-\\u001F\\u007F-\\u009F\\u200B-\\u200F\\u202A-\\u202E\\u2060-\\u206F\\uFEFF]', 'g')

export function oneLine(value: unknown, limit: number): string {
  return String(value ?? '').replace(INVISIBLE, ' ').replace(/\s+/g, ' ').trim().slice(0, limit)
}

function num(value: unknown, fallback = 0): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function shot(raw: unknown): TlShot | undefined {
  const s = raw as Record<string, unknown> | null
  if (!s || typeof s !== 'object' || typeof s.ref !== 'string' || !REF.test(s.ref) || typeof s.token !== 'string' || !TOKEN.test(s.token))
    return undefined
  return { ref: s.ref, token: s.token }
}

function status(raw: unknown): { status: string, ts: number } | null {
  const u = raw as Record<string, unknown> | null
  return u && typeof u === 'object' ? { status: oneLine(u.status, 20) || 'undone', ts: num(u.ts) } : null
}

export function cleanStep(raw: unknown): TlStep | null {
  const s = raw as Record<string, unknown> | null
  if (!s || typeof s !== 'object' || typeof s.id !== 'string' || !ID.test(s.id))
    return null
  const shots = s.shots as Record<string, unknown> | null
  const before = shot(shots?.before)
  const after = shot(shots?.after)
  return {
    id: s.id,
    taskId: oneLine(s.task_id, 64),
    ts: num(s.ts),
    label: oneLine(s.step_label, 90) || 'A step',
    tool: oneLine(s.tool, 40),
    cls: s.class === 'A' || s.class === 'C' ? s.class : 'B',
    target: oneLine(s.target, 80),
    result: oneLine(s.result, 200),
    verified: s.verified === 'verified' || s.verified === 'unconfirmed' ? s.verified : 'failed',
    durationMs: Math.max(0, Math.trunc(num(s.duration_ms))),
    undoable: s.undoable === true,
    undoText: oneLine(s.undo_text, 160),
    cannot: oneLine(s.cannot, 160),
    undone: status(s.undone),
    tried: status(s.undo_tried),
    isUndo: typeof s.undo_of === 'string' && s.undo_of.length > 0,
    shots: before || after ? { ...(before ? { before } : {}), ...(after ? { after } : {}) } : null,
  }
}

function cleanSummary(raw: unknown): TlSummary {
  const s = (raw ?? {}) as Record<string, unknown>
  return { steps: num(s.steps), tasks: num(s.tasks), failed: num(s.failed), unconfirmed: num(s.unconfirmed), undoable: num(s.undoable), undone: num(s.undone) }
}

export function parseTimelineWanted(result: ToolResult | undefined | null): TlWanted | null {
  const d = toolData(result)
  if (!d)
    return null
  const version = Math.max(0, Math.trunc(num(d.version)))
  const openSeq = Math.max(0, Math.trunc(num(d.open_seq)))
  const token = typeof d.token === 'string' && TOKEN.test(d.token) ? d.token : ''
  if (d.unchanged === true)
    return { version, openSeq, token, unchanged: true, view: null }
  const v = d.view as Record<string, unknown> | null | undefined
  if (!v || typeof v !== 'object')
    return { version, openSeq, token, unchanged: false, view: null }
  const tasks: TlTask[] = (Array.isArray(v.tasks) ? v.tasks as Array<Record<string, unknown>> : []).slice(0, 200).map((t) => {
    const steps = (Array.isArray(t.steps) ? t.steps : []).slice(0, 400).map(cleanStep).filter((x): x is TlStep => !!x)
    return {
      id: oneLine(t.task_id, 64),
      title: oneLine(t.goal_label, 90),
      ts: num(t.ts),
      lastTs: num(t.last_ts),
      steps,
      failed: num(t.failed),
      unconfirmed: num(t.unconfirmed),
      undoable: num(t.undoable),
    }
  }).filter(t => t.id && t.steps.length > 0)
  return {
    version,
    openSeq,
    token,
    unchanged: false,
    view: { available: v.available !== false, reason: oneLine(v.reason, 200), tasks, summary: cleanSummary(v.summary), now: num(v.now), screenshots: v.screenshots !== false },
  }
}

export function parseUndoReport(result: ToolResult | undefined | null): UndoReport | null {
  const d = toolData(result)
  if (!d)
    return null
  const results = (Array.isArray(d.results) ? d.results as Array<Record<string, unknown>> : []).slice(0, 20).map(r => ({
    label: oneLine(r?.label, 90),
    status: oneLine(r?.status, 20),
    reason: oneLine(r?.reason, 200),
  }))
  const message = oneLine(d.message, 200)
  return { ok: d.ok === true, undone: Math.trunc(num(d.undone)), notUndone: Math.trunc(num(d.not_undone)), summary: oneLine(d.summary, 1200) || message, results }
}

/** A picture's `data:` URL from the base64 the server returned, or undefined when it is not a JPEG of a sane size. */
export function shotDataUrl(result: ToolResult | undefined | null): string | undefined {
  const d = toolData(result)
  if (!d || d.ok !== true || d.mime !== 'image/jpeg' || typeof d.data !== 'string' || d.data.length > MAX_SHOT_BASE64 || !BASE64.test(d.data))
    return undefined
  return `data:image/jpeg;base64,${d.data}`
}

// ---- filters, grouping, rows -------------------------------------------------------------------------------------------------------------

export interface TlFilter { today: boolean, failedOnly: boolean }

export function startOfDay(now: number): number {
  const d = new Date(now * 1000)
  d.setHours(0, 0, 0, 0)
  return d.getTime() / 1000
}

/** Tasks with only the steps that pass the filter; a task with no step left disappears. */
export function applyFilter(tasks: TlTask[], filter: TlFilter, now: number): TlTask[] {
  if (!filter.today && !filter.failedOnly)
    return tasks
  const since = filter.today ? startOfDay(now) : 0
  const out: TlTask[] = []
  for (const t of tasks) {
    const steps = t.steps.filter(s => s.ts >= since && (!filter.failedOnly || (s.verified === 'failed' && !s.isUndo)))
    if (steps.length === 0)
      continue
    out.push({ ...t, steps, failed: steps.filter(s => s.verified === 'failed' && !s.isUndo).length, unconfirmed: steps.filter(s => s.verified === 'unconfirmed' && !s.isUndo).length, undoable: steps.filter(s => s.undoable).length })
  }
  return out
}

export type Row
  = | { kind: 'task', key: string, task: TlTask }
    | { kind: 'step', key: string, step: TlStep, task: TlTask }
    | { kind: 'detail', key: string, step: TlStep }

/** Newest task first, its steps newest first; the open step is followed by its detail row. */
export function buildRows(tasks: TlTask[], expanded: ReadonlySet<string>, collapsedTasks: ReadonlySet<string> = new Set()): Row[] {
  const rows: Row[] = []
  for (const t of tasks) {
    rows.push({ kind: 'task', key: `t:${t.id}`, task: t })
    if (collapsedTasks.has(t.id))
      continue
    for (const s of t.steps) {
      rows.push({ kind: 'step', key: `s:${s.id}`, step: s, task: t })
      if (expanded.has(s.id))
        rows.push({ kind: 'detail', key: `d:${s.id}`, step: s })
    }
  }
  return rows
}

export type LayoutName = 'wide' | 'narrow'
export const NARROW_BELOW = 520 // below this the panel is too narrow for one-line rows: label, then time, class and duration under it
export const HEIGHTS: Record<LayoutName, Record<Row['kind'], number>> = {
  wide: { task: 58, step: 46, detail: 214 },
  narrow: { task: 74, step: 62, detail: 244 },
}

/** The panel's width from the window's, before anything is measured (mirrors timeline.css), so the first draw already has the right layout. */
export function expectedPanelWidth(viewport: number): number {
  if (viewport < 480)
    return viewport
  return Math.min(viewport >= 900 ? 560 : 460, viewport - 24)
}

export function layoutFor(width: number): LayoutName {
  return width > 0 && width < NARROW_BELOW ? 'narrow' : 'wide'
}

export interface Layout { offsets: number[], total: number }

export function layoutRows(rows: Row[], layout: LayoutName): Layout {
  const offsets: number[] = []
  let y = 0
  for (const r of rows) {
    offsets.push(y)
    y += HEIGHTS[layout][r.kind]
  }
  return { offsets, total: y }
}

export interface RowWindow { start: number, end: number, padTop: number, padBottom: number }

/** The rows to draw for a scroll position: a binary search in the offsets, plus a few rows either side. */
export function visibleWindow(layout: Layout, rows: Row[], scrollTop: number, viewport: number, overscan = 4): RowWindow {
  const n = rows.length
  if (n === 0)
    return { start: 0, end: 0, padTop: 0, padBottom: 0 }
  let lo = 0
  let hi = n - 1
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1
    if (layout.offsets[mid] <= scrollTop)
      lo = mid
    else
      hi = mid - 1
  }
  const first = Math.max(0, lo - overscan)
  let last = lo
  while (last < n && layout.offsets[last] < scrollTop + Math.max(viewport, 1))
    last++
  const end = Math.min(n, last + overscan)
  const endY = end < n ? layout.offsets[end] : layout.total
  return { start: first, end, padTop: layout.offsets[first], padBottom: Math.max(0, layout.total - endY) }
}

/** The scrollTop that brings row `index` fully into view (the current one when it already is). */
export function scrollToRow(layout: Layout, rows: Row[], layoutName: LayoutName, index: number, scrollTop: number, viewport: number): number {
  if (index < 0 || index >= rows.length)
    return scrollTop
  const top = layout.offsets[index]
  const bottom = top + HEIGHTS[layoutName][rows[index].kind]
  if (top < scrollTop)
    return top
  if (bottom > scrollTop + viewport)
    return Math.max(0, bottom - viewport)
  return scrollTop
}

// ---- reading ------------------------------------------------------------------------------------------------------------------------------

export function formatClock(ts: number): string {
  const d = new Date(ts * 1000)
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(d.getHours())}:${p(d.getMinutes())}`
}

export function formatDay(ts: number, now: number): string {
  const day = startOfDay(now)
  if (ts >= day)
    return 'Today'
  if (ts >= day - 86400)
    return 'Yesterday'
  const d = new Date(ts * 1000)
  return `${['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()]} ${d.getDate()} ${['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()]}`
}

export function formatDuration(ms: number): string {
  if (!(ms > 0))
    return ''
  if (ms < 1000)
    return `${Math.round(ms)} ms`
  if (ms < 60_000)
    return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`
  const m = Math.floor(ms / 60_000)
  return `${m} m ${Math.round((ms % 60_000) / 1000)} s`
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

export const VERIFIED_TEXT: Record<Verified, string> = {
  verified: 'Verified: what happened was seen on the PC',
  unconfirmed: 'Unconfirmed: it reported success but it was not seen',
  failed: 'Failed or not done',
}
export const CLASS_TEXT: Record<StepClass, string> = {
  A: 'Class A: ordinary, what you asked for (look, open, type, a new file)',
  B: 'Class B: changed something that was there, a setting, or ran something',
  C: 'Class C: a delete, a repair, or it needed your PIN',
}

export function taskLine(t: TlTask): string {
  const parts = [plural(t.steps.length, 'step')]
  if (t.failed)
    parts.push(`${t.failed} failed`)
  if (t.unconfirmed)
    parts.push(`${t.unconfirmed} unconfirmed`)
  return parts.join(' · ')
}

export function stepAria(s: TlStep): string {
  return `${formatClock(s.ts)}, ${s.label}, ${VERIFIED_TEXT[s.verified].split(':')[0]}${s.undone ? ', undone' : s.undoable ? ', can be undone' : ''}`
}

export function undoneText(s: TlStep): string {
  if (s.undone)
    return s.undone.status === 'partly' ? 'Partly undone' : 'Undone'
  return ''
}

// ---- keyboard -----------------------------------------------------------------------------------------------------------------------------

export type KeyCommand
  = | { kind: 'move', to: number }
    | { kind: 'toggle' }
    | { kind: 'undo' }
    | { kind: 'close' }
    | { kind: 'page', dir: 1 | -1 }

/** What a key does in the list. Typing in a field or pressing a button is left to the browser. */
export function keyCommand(key: string, index: number, count: number, target: { tag: string, role?: string }): KeyCommand | null {
  const tag = target.tag.toLowerCase()
  if (key === 'Escape')
    return { kind: 'close' }
  if (count <= 0 || tag === 'input' || tag === 'textarea' || tag === 'select')
    return null
  const onButton = tag === 'button' && target.role !== 'option'
  if (key === 'ArrowDown')
    return { kind: 'move', to: Math.min(count - 1, index < 0 ? 0 : index + 1) }
  if (key === 'ArrowUp')
    return { kind: 'move', to: Math.max(0, index < 0 ? 0 : index - 1) }
  if (key === 'Home')
    return { kind: 'move', to: 0 }
  if (key === 'End')
    return { kind: 'move', to: count - 1 }
  if (key === 'PageDown')
    return { kind: 'page', dir: 1 }
  if (key === 'PageUp')
    return { kind: 'page', dir: -1 }
  if ((key === 'Enter' || key === ' ') && !onButton)
    return { kind: 'toggle' }
  if ((key === 'u' || key === 'U') && !onButton)
    return { kind: 'undo' }
  return null
}
