/* eslint-disable regexp/no-obscure-range -- the ranges name the control, zero-width and direction characters on purpose */
/**
 * The ask panel's rules as plain functions (D55: generated UI = declarative forms only).
 *
 * The tool server (jarvis/bridge/ask_spec.py) has already cleaned the spec: ids, types, limits and plain text. Everything here checks again
 * what arrives, because a form can be made from text a web page or a file supplied: the panel only ever draws text as text, shows images
 * only as `data:` URLs of four raster types, and never turns anything into markup, a link or a script.
 *
 * Also here: the pure parts of the keyboard (arrow keys in a list), the virtual list (which rows to draw), sorting, totals, a line diff and
 * the geometry of the small charts, so they can be tested without a browser.
 */
import type { PreviewImage, ToolResult } from './jarvis-preview-logic'

import { imageDataUrl, toolData } from './jarvis-preview-logic'

export const JARVIS_ASK_WANTED_TOOL = 'jarvis::jarvis_ask_wanted'
export const JARVIS_ASK_SUBMIT_TOOL = 'jarvis::jarvis_ask_submit'
export const JARVIS_ASK_CLOSE_TOOL = 'jarvis::jarvis_ask_close'

export const NODE_TYPES = [
  'section',
  'row',
  'column',
  'tabs',
  'grid',
  'checkbox_group',
  'radio',
  'select',
  'text',
  'number',
  'slider',
  'toggle',
  'date',
  'file_choice',
  'gallery',
  'table',
  'cards',
  'split',
  'info_table',
  'chart',
  'code_view',
  'diff_view',
  'steps',
  'progress',
  'callout',
] as const
export type NodeType = (typeof NODE_TYPES)[number]
export const INPUT_TYPES: ReadonlySet<string> = new Set(['checkbox_group', 'radio', 'select', 'text', 'number', 'slider', 'toggle', 'date', 'file_choice', 'gallery', 'table', 'cards', 'split'])
export const LAYOUT_TYPES: ReadonlySet<string> = new Set(['section', 'row', 'column', 'grid'])
const TYPE_SET: ReadonlySet<string> = new Set(NODE_TYPES)

export interface AskOption {
  id: string
  label: string
  detail?: string
  badge?: string
  group?: string
  body?: string
  size?: number | string
  checked?: boolean
  pros?: string[]
  cons?: string[]
  items?: Array<{ label: string, size?: number }>
  thumb?: PreviewImage
}

export interface AskNode {
  type: NodeType
  id?: string
  label?: string
  hint?: string
  required?: boolean
  collapsed?: boolean
  children?: AskNode[]
  tabs?: Array<{ id: string, label: string, children: AskNode[] }>
  options?: AskOption[]
  variant?: 'list' | 'segmented'
  multiple?: boolean
  [key: string]: unknown
}

export interface AskButton { id: string, label: string, style: 'primary' | 'secondary' | 'quiet' }
export interface AskProgress { done?: number, total?: number, bytes_done?: number, bytes_total?: number, label?: string }

export interface AskForm {
  id: string
  token: string
  title: string
  intro: string
  fields: AskNode[]
  buttons: AskButton[]
  busy: boolean
  status: string
  progress: AskProgress | null
  values: Record<string, unknown>
  notes: string[]
  timeoutS: number
}

export type AskValue = string | number | boolean | string[] | null

export function oneLine(value: unknown, limit: number): string {
  return String(value ?? '').replace(/[\u0000-\u001F\u007F-\u009F\u200B-\u200F‪-‮⁠-⁯\uFEFF]/g, ' ').replace(/\s+/g, ' ').trim().slice(0, limit)
}

function block(value: unknown, limit: number): string {
  return String(value ?? '').replace(/\r\n?/g, '\n').replace(/[\u0000-\u0008\u000B-\u001F\u007F-\u009F\u200B-\u200F‪-‮⁠-⁯\uFEFF]/g, ' ').slice(0, limit)
}

const ID = /^[\w.:-]{1,64}$/

function cleanOption(raw: unknown): AskOption | null {
  const o = raw as Record<string, unknown> | null
  if (!o || typeof o !== 'object' || typeof o.id !== 'string' || !ID.test(o.id))
    return null
  const out: AskOption = { id: o.id, label: oneLine(o.label, 100) || o.id }
  for (const key of ['detail', 'badge', 'group'] as const) {
    if (o[key])
      out[key] = oneLine(o[key], 160)
  }
  if (o.body)
    out.body = block(o.body, 4000)
  if (typeof o.size === 'number' && Number.isFinite(o.size) && o.size >= 0)
    out.size = o.size
  else if (typeof o.size === 'string')
    out.size = oneLine(o.size, 24)
  if (o.checked === true)
    out.checked = true
  for (const key of ['pros', 'cons'] as const) {
    if (Array.isArray(o[key]))
      out[key] = (o[key] as unknown[]).slice(0, 5).map(x => oneLine(x, 100)).filter(Boolean)
  }
  if (Array.isArray(o.items))
    out.items = (o.items as Array<Record<string, unknown>>).slice(0, 8).map(s => ({ label: oneLine(s?.label, 100), ...(typeof s?.size === 'number' ? { size: s.size } : {}) }))
  const thumb = o.thumb as PreviewImage | undefined
  if (thumb && imageDataUrl(thumb))
    out.thumb = { mime: thumb.mime, data: thumb.data }
  return out
}

/** One node, checked again. A type the panel does not know becomes a callout with its label (never an error screen, never markup). */
export function cleanNode(raw: unknown, depth = 0): AskNode | null {
  const n = raw as Record<string, unknown> | null
  if (!n || typeof n !== 'object' || depth > 6)
    return null
  if (typeof n.type !== 'string' || !TYPE_SET.has(n.type))
    return { type: 'callout', level: 'info', text: oneLine(n.label ?? n.text, 200) || 'Part of this form could not be shown.' } as AskNode
  const out: AskNode = { ...n, type: n.type as NodeType, label: oneLine(n.label, 120) }
  if (typeof n.id === 'string')
    out.id = oneLine(n.id, 64)
  if (Array.isArray(n.children))
    out.children = n.children.map(c => cleanNode(c, depth + 1)).filter((c): c is AskNode => !!c)
  if (Array.isArray(n.tabs)) {
    out.tabs = (n.tabs as Array<Record<string, unknown>>).slice(0, 6).map(t => ({
      id: oneLine(t?.id, 40),
      label: oneLine(t?.label, 40),
      children: Array.isArray(t?.children) ? (t.children as unknown[]).map(c => cleanNode(c, depth + 1)).filter((c): c is AskNode => !!c) : [],
    })).filter(t => t.id)
  }
  if (Array.isArray(n.options))
    out.options = (n.options as unknown[]).slice(0, 500).map(cleanOption).filter((o): o is AskOption => !!o)
  if (typeof n.text === 'string' && ['callout', 'code_view'].includes(n.type))
    out.text = n.type === 'code_view' ? block(n.text, 20_000) : oneLine(n.text, 2000)
  if (n.type === 'diff_view') {
    out.before = block(n.before, 20_000)
    out.after = block(n.after, 20_000)
  }
  return out
}

function number(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

export function parseAskWanted(result: ToolResult | undefined | null): { version: number, unchanged: boolean, form: AskForm | null } | null {
  const d = toolData(result)
  if (!d)
    return null
  const version = Math.max(0, Math.trunc(number(d.version) ?? 0))
  const f = d.form as Record<string, unknown> | null | undefined
  if (d.unchanged === true)
    return { version, unchanged: true, form: null }
  if (!f || typeof f !== 'object' || typeof f.id !== 'string' || !ID.test(f.id) || typeof f.token !== 'string' || f.token.length > 64)
    return { version, unchanged: false, form: null }
  const progress = f.progress as Record<string, unknown> | null
  return {
    version,
    unchanged: false,
    form: {
      id: f.id,
      token: f.token,
      title: oneLine(f.title, 80) || 'JARVIS asks',
      intro: oneLine(f.intro, 400),
      fields: Array.isArray(f.fields) ? f.fields.map(n => cleanNode(n)).filter((n): n is AskNode => !!n) : [],
      buttons: (Array.isArray(f.buttons) ? f.buttons as Array<Record<string, unknown>> : []).slice(0, 4).filter(b => typeof b?.id === 'string' && ID.test(b.id as string)).map(b => ({
        id: b.id as string,
        label: oneLine(b.label, 40) || (b.id as string),
        style: (['primary', 'secondary', 'quiet'].includes(String(b.style)) ? b.style : 'secondary') as AskButton['style'],
      })),
      busy: f.busy === true,
      status: oneLine(f.status, 120),
      progress: progress && typeof progress === 'object'
        ? { done: number(progress.done), total: number(progress.total), bytes_done: number(progress.bytes_done), bytes_total: number(progress.bytes_total), label: oneLine(progress.label, 80) }
        : null,
      values: f.values && typeof f.values === 'object' ? { ...(f.values as Record<string, unknown>) } : {},
      notes: Array.isArray(f.notes) ? f.notes.slice(0, 6).map(x => oneLine(x, 160)) : [],
      timeoutS: number(f.timeout_s) ?? 90,
    },
  }
}

// ---- walking the tree ---------------------------------------------------------------------------------------------------------------------

export function* walk(nodes: AskNode[]): Generator<AskNode> {
  for (const n of nodes) {
    yield n
    if (n.children)
      yield* walk(n.children)
    for (const t of n.tabs ?? [])
      yield* walk(t.children)
  }
}

export function inputs(nodes: AskNode[]): AskNode[] {
  return [...walk(nodes)].filter(n => INPUT_TYPES.has(n.type) && n.id)
}

export function isMulti(n: AskNode): boolean {
  return n.type === 'checkbox_group' || ((n.type === 'cards' || n.type === 'gallery') && n.multiple === true) || (n.type === 'table' && n.select === 'many')
}

// ---- numbers and text -----------------------------------------------------------------------------------------------------------------------

export function formatSize(n: unknown): string {
  if (typeof n === 'string')
    return n
  let v = typeof n === 'number' && Number.isFinite(n) ? Math.max(0, n) : 0
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${i <= 1 || v >= 100 ? Math.round(v) : v.toFixed(1)} ${units[i]}`
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`
}

/** Sum of the sizes of the ticked options of every checkbox group that lists sizes, and how many are ticked. */
export function selectedTotal(fields: AskNode[], values: Record<string, unknown>): { bytes: number, count: number } | null {
  let bytes = 0
  let count = 0
  let any = false
  for (const n of inputs(fields)) {
    if (n.type !== 'checkbox_group' || !n.options?.some(o => typeof o.size === 'number'))
      continue
    any = true
    const picked = new Set(Array.isArray(values[n.id!]) ? values[n.id!] as string[] : [])
    for (const o of n.options) {
      if (picked.has(o.id)) {
        count++
        bytes += typeof o.size === 'number' ? o.size : 0
      }
    }
  }
  return any ? { bytes, count } : null
}

export function progressFraction(p: AskProgress | null): number | null {
  if (!p)
    return null
  if (p.bytes_total && p.bytes_total > 0 && p.bytes_done !== undefined)
    return Math.min(1, Math.max(0, p.bytes_done / p.bytes_total))
  if (p.total && p.total > 0 && p.done !== undefined)
    return Math.min(1, Math.max(0, p.done / p.total))
  return null
}

export function progressText(p: AskProgress | null): string {
  if (!p)
    return ''
  const parts: string[] = []
  if (p.bytes_total && p.bytes_done !== undefined)
    parts.push(`${formatSize(p.bytes_done)} of ${formatSize(p.bytes_total)}`)
  if (p.total && p.done !== undefined)
    parts.push(`${p.done} of ${p.total}`)
  return parts.join(' · ')
}

// ---- keyboard ---------------------------------------------------------------------------------------------------------------------------

/** Where an arrow key moves in a list of `length` items (Home and End jump; Left/Up and Right/Down step; the ends do not wrap in a table, they do in a radio group). */
export function moveIndex(index: number, key: string, length: number, wrap = true): number {
  if (length <= 0)
    return -1
  if (key === 'Home')
    return 0
  if (key === 'End')
    return length - 1
  const step = key === 'ArrowDown' || key === 'ArrowRight' ? 1 : key === 'ArrowUp' || key === 'ArrowLeft' ? -1 : 0
  if (!step)
    return index
  const next = index + step
  if (wrap)
    return (next + length) % length
  return Math.min(length - 1, Math.max(0, next))
}

/** Enter presses the form's primary button; Escape presses its cancel (or close) button, else closes the form. */
export function keyAction(key: string, buttons: AskButton[], target: { tag: string, type?: string, role?: string }): { kind: 'button', id: string } | { kind: 'close' } | null {
  if (key === 'Escape') {
    const cancel = buttons.find(b => ['cancel', 'close', 'no'].includes(b.id))
    return cancel ? { kind: 'button', id: cancel.id } : { kind: 'close' }
  }
  if (key === 'Enter') {
    const tag = target.tag.toLowerCase()
    // Enter on a choice (a radio-like button, as in a native radio group) sends the form; on a real button or link it presses that button
    if (target.role !== 'radio' && (tag === 'textarea' || tag === 'button' || tag === 'a' || tag === 'select' || target.type === 'checkbox' || target.type === 'radio'))
      return null
    const primary = buttons.find(b => b.style === 'primary')
    return primary ? { kind: 'button', id: primary.id } : null
  }
  return null
}

// ---- the virtual list --------------------------------------------------------------------------------------------------------------------

export const VIRTUAL_FROM = 60

export interface RowWindow { start: number, end: number, padTop: number, padBottom: number }

/** Which rows of `total` to draw for a scroll position. Below VIRTUAL_FROM rows everything is drawn. */
export function windowRange(scrollTop: number, viewport: number, rowHeight: number, total: number, overscan = 6): RowWindow {
  if (total <= VIRTUAL_FROM)
    return { start: 0, end: total, padTop: 0, padBottom: 0 }
  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan)
  const last = Math.min(total, Math.ceil((scrollTop + Math.max(viewport, rowHeight)) / rowHeight) + overscan)
  return { start: first, end: last, padTop: first * rowHeight, padBottom: (total - last) * rowHeight }
}

export interface ListRow { kind: 'header' | 'option', key: string, option?: AskOption, group?: string, groupBytes?: number, groupCount?: number }

/** Options as rows: a header row before each new `group`, so one fixed row height serves the whole list. */
export function listRows(options: AskOption[]): ListRow[] {
  const rows: ListRow[] = []
  const totals = new Map<string, { bytes: number, count: number }>()
  for (const o of options) {
    if (o.group) {
      const t = totals.get(o.group) ?? { bytes: 0, count: 0 }
      t.bytes += typeof o.size === 'number' ? o.size : 0
      t.count++
      totals.set(o.group, t)
    }
  }
  let last: string | undefined
  for (const o of options) {
    if (o.group && o.group !== last) {
      rows.push({ kind: 'header', key: `g:${o.group}`, group: o.group, groupBytes: totals.get(o.group)?.bytes, groupCount: totals.get(o.group)?.count })
      last = o.group
    }
    rows.push({ kind: 'option', key: o.id, option: o })
  }
  return rows
}

// ---- tables ---------------------------------------------------------------------------------------------------------------------------------

export interface TableRow { id: string, cells: Record<string, string> }

function numeric(text: string): number | null {
  const m = /^\s*[-+]?\$?(?:[\d,]*\.\d+|[\d,]*\d)\s*(?:(%|[KMGT]?B)\s*)?$/i.exec(text)
  if (!m)
    return null
  const n = Number.parseFloat(text.replace(/[^\d.+-]/g, ''))
  if (!Number.isFinite(n))
    return null
  const unit = (m[1] ?? '').toUpperCase()
  return n * (({ KB: 1024, MB: 1024 ** 2, GB: 1024 ** 3, TB: 1024 ** 4 } as Record<string, number>)[unit] ?? 1)
}

export function sortRows(rows: TableRow[], column: string | null, direction: 'asc' | 'desc'): TableRow[] {
  if (!column)
    return rows
  const sign = direction === 'asc' ? 1 : -1
  return [...rows].sort((a, b) => {
    const x = a.cells[column] ?? ''
    const y = b.cells[column] ?? ''
    const nx = numeric(x)
    const ny = numeric(y)
    if (nx !== null && ny !== null)
      return (nx - ny) * sign
    return x.localeCompare(y, undefined, { numeric: true, sensitivity: 'base' }) * sign
  })
}

export function nextSort(current: { column: string | null, direction: 'asc' | 'desc' }, column: string): { column: string | null, direction: 'asc' | 'desc' } {
  if (current.column !== column)
    return { column, direction: 'asc' }
  return current.direction === 'asc' ? { column, direction: 'desc' } : { column: null, direction: 'asc' }
}

// ---- a line diff ------------------------------------------------------------------------------------------------------------------------

export interface DiffLine { kind: 'same' | 'add' | 'del', text: string }

/** Line diff by longest common subsequence; very long inputs are compared line by line from the top instead. */
export function lineDiff(before: string, after: string): DiffLine[] {
  const a = before.length ? before.split('\n') : []
  const b = after.length ? after.split('\n') : []
  if (a.length * b.length > 4_000_000) {
    const out: DiffLine[] = []
    const n = Math.max(a.length, b.length)
    for (let i = 0; i < n; i++) {
      if (a[i] === b[i]) {
        out.push({ kind: 'same', text: a[i] })
      }
      else {
        if (i < a.length)
          out.push({ kind: 'del', text: a[i] })
        if (i < b.length)
          out.push({ kind: 'add', text: b[i] })
      }
    }
    return out
  }
  const lcs: number[][] = Array.from({ length: a.length + 1 }, () => Array.from<number>({ length: b.length + 1 }).fill(0))
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--)
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1])
  }
  const out: DiffLine[] = []
  let i = 0
  let j = 0
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ kind: 'same', text: a[i] })
      i++
      j++
    }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      out.push({ kind: 'del', text: a[i++] })
    }
    else {
      out.push({ kind: 'add', text: b[j++] })
    }
  }
  while (i < a.length) out.push({ kind: 'del', text: a[i++] })
  while (j < b.length) out.push({ kind: 'add', text: b[j++] })
  return out
}

// ---- charts -----------------------------------------------------------------------------------------------------------------------------------

export interface ChartData { kind: 'bar' | 'line' | 'pie', labels: string[], series: Array<{ name: string, values: number[] }> }

export interface Bar { x: number, y: number, w: number, h: number, series: number, label: string, value: number }
export interface Slice { path: string, label: string, value: number, fraction: number, index: number }

export function chartMax(series: ChartData['series']): number {
  const max = Math.max(0, ...series.flatMap(s => s.values))
  if (max <= 0)
    return 1
  const pow = 10 ** Math.floor(Math.log10(max))
  return Math.ceil(max / pow) * pow
}

export function barGeometry(data: ChartData, width: number, height: number, pad = 4): Bar[] {
  const max = chartMax(data.series)
  const groups = data.labels.length || 1
  const slot = (width - pad * 2) / groups
  const bw = Math.max(2, (slot * 0.72) / Math.max(1, data.series.length))
  const bars: Bar[] = []
  data.series.forEach((s, si) => {
    s.values.slice(0, groups).forEach((v, i) => {
      const h = Math.max(0, (v / max) * (height - pad * 2))
      bars.push({ x: pad + i * slot + slot * 0.14 + si * bw, y: height - pad - h, w: bw - 1, h, series: si, label: data.labels[i] ?? '', value: v })
    })
  })
  return bars
}

export function linePoints(data: ChartData, width: number, height: number, pad = 6): string[] {
  const max = chartMax(data.series)
  return data.series.map((s) => {
    const n = Math.max(1, s.values.length - 1)
    return s.values.map((v, i) => `${(pad + (i / n) * (width - pad * 2)).toFixed(1)},${(height - pad - (v / max) * (height - pad * 2)).toFixed(1)}`).join(' ')
  })
}

export function pieSlices(data: ChartData, radius: number): Slice[] {
  const values = (data.series[0]?.values ?? []).map(v => Math.max(0, v))
  const total = values.reduce((a, b) => a + b, 0)
  if (total <= 0)
    return []
  let angle = -Math.PI / 2
  return values.map((v, index) => {
    const fraction = v / total
    const next = angle + fraction * Math.PI * 2
    const [x1, y1, x2, y2] = [Math.cos(angle) * radius, Math.sin(angle) * radius, Math.cos(next) * radius, Math.sin(next) * radius]
    const large = fraction > 0.5 ? 1 : 0
    const path = fraction >= 0.9999
      ? `M 0 ${-radius} A ${radius} ${radius} 0 1 1 0 ${radius} A ${radius} ${radius} 0 1 1 0 ${-radius} Z`
      : `M 0 0 L ${x1.toFixed(2)} ${y1.toFixed(2)} A ${radius} ${radius} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} Z`
    angle = next
    return { path, label: data.labels[index] ?? '', value: v, fraction, index }
  })
}

// ---- answers --------------------------------------------------------------------------------------------------------------------------------------

export function missingRequired(fields: AskNode[], values: Record<string, unknown>): AskNode | null {
  for (const n of inputs(fields)) {
    if (!n.required)
      continue
    const v = values[n.id!]
    if (v === '' || v === null || v === undefined || (Array.isArray(v) && v.length === 0))
      return n
  }
  return null
}

export function toggleIn(list: unknown, id: string): string[] {
  const current = Array.isArray(list) ? list as string[] : []
  return current.includes(id) ? current.filter(x => x !== id) : [...current, id]
}

export function setGroup(list: unknown, ids: string[], on: boolean): string[] {
  const current = new Set(Array.isArray(list) ? list as string[] : [])
  for (const id of ids) {
    if (on)
      current.add(id)
    else
      current.delete(id)
  }
  return [...current]
}
