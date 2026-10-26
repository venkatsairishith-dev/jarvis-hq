/**
 * The Timeline panel's state (D61). It talks to the JARVIS tool server (jarvis/bridge/timeline.py) over the face's MCP connection:
 *   jarvis_timeline_wanted   a long poll: returns within milliseconds of a new step or of the chat model asking to show the panel (or after ~20 s
 *                            with "unchanged"). A closed panel asks with full=false and is sent the journal only when it is asked to open.
 *   jarvis_timeline_shot     one before/after picture of the local journal (a JPEG as base64), only for a step the owner opened
 *   jarvis_timeline_undo     the owner's click: one step, one task, or the newest n. The server runs exactly that and reports what was and was not undone.
 *
 * The panel opens when the server's open counter goes up (the first answer after the page loads only sets the baseline, so an old request
 * does not pop up), or when the owner presses Ctrl+Alt+T in the window. Switch off with localStorage `jarvis:timeline` = `off`.
 */
import type { ToolResult } from './jarvis-preview-logic'
import type { TlFilter, TlShot, TlStep, TlTask, TlView, UndoReport } from './jarvis-timeline-logic'

import { computed, ref, shallowRef } from 'vue'

import { applyFilter, JARVIS_TIMELINE_SHOT_TOOL, JARVIS_TIMELINE_UNDO_TOOL, JARVIS_TIMELINE_WANTED_TOOL, parseTimelineWanted, parseUndoReport, shotDataUrl } from './jarvis-timeline-logic'

export const JARVIS_TIMELINE_OFF_KEY = 'jarvis:timeline'
export const MIN_POLL_GAP_MS = 1_000
const MAX_BACKOFF_MS = 30_000
const WAIT_S = 20
const SHOT_CACHE = 40
const BASELINE_OPEN = 1_000_000_000 // the first poll: "I have seen every open request", so the server sends the counter and no journal

export interface TimelineDeps {
  call: (name: string, args?: Record<string, unknown>) => Promise<ToolResult>
  enabled?: () => boolean
  paused?: () => boolean
  onError?: (error: unknown) => void
  now?: () => number
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
  waitS?: number
}

export function timelineEnabled(storage: Pick<Storage, 'getItem'> | undefined): boolean {
  try {
    return storage?.getItem(JARVIS_TIMELINE_OFF_KEY) !== 'off'
  }
  catch {
    return true
  }
}

export function useTimelineController(deps: TimelineDeps) {
  const now = deps.now ?? (() => Date.now())
  const setTimer = deps.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms))
  const clearTimer = deps.clearTimer ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>))
  const open = ref(false)
  const view = shallowRef<TlView | null>(null)
  const filter = ref<TlFilter>({ today: false, failedOnly: false })
  const expanded = ref<Set<string>>(new Set())
  const collapsedTasks = ref<Set<string>>(new Set())
  const undoing = ref<Set<string>>(new Set())
  const report = shallowRef<UndoReport | null>(null)
  const error = ref('')
  const openedAt = ref(0)
  const shots = ref<Record<string, string>>({}) // ref -> data URL, or '' while loading, or '!' when it could not be had

  let token = ''
  let version = 0
  let openSeen = 0
  let baselined = false
  let failures = 0
  let timer: unknown
  let started = false
  let polling = false
  let lastPollAt = 0
  const order: string[] = []

  const tasks = computed<TlTask[]>(() => view.value ? applyFilter(view.value.tasks, filter.value, view.value.now || now() / 1000) : [])

  function setOpen(value: boolean) {
    if (value && !open.value)
      openedAt.value = now()
    open.value = value
  }

  function apply(parsed: NonNullable<ReturnType<typeof parseTimelineWanted>>) {
    token = parsed.token || token
    if (!baselined) {
      baselined = true // an open request from before this page loaded does not pop the panel up
      openSeen = parsed.openSeq
    }
    else if (parsed.openSeq > openSeen) {
      openSeen = parsed.openSeq
      setOpen(true)
    }
    if (!parsed.unchanged) {
      version = Math.max(version, parsed.version)
      if (parsed.view)
        view.value = parsed.view
    }
  }

  /** Fetch the journal now, beside the long poll that may be waiting (the owner opened the panel by keyboard, or an undo finished). */
  async function refresh(): Promise<boolean> {
    if (deps.enabled && !deps.enabled())
      return false
    try {
      const parsed = parseTimelineWanted(await deps.call(JARVIS_TIMELINE_WANTED_TOOL, { seen: 0, wait_s: 0, full: true, seen_open: openSeen }))
      if (!parsed)
        return false
      apply(parsed)
      return true
    }
    catch (e) {
      deps.onError?.(e)
      return false
    }
  }

  /** One long poll. Returns true when the server answered. */
  async function pollOnce(): Promise<boolean> {
    if (polling || (deps.enabled && !deps.enabled()) || (deps.paused && deps.paused()))
      return false
    polling = true
    lastPollAt = now()
    try {
      const parsed = parseTimelineWanted(await deps.call(JARVIS_TIMELINE_WANTED_TOOL, { seen: version, wait_s: deps.waitS ?? WAIT_S, full: open.value, seen_open: baselined ? openSeen : BASELINE_OPEN }))
      failures = 0
      if (!parsed)
        return false
      apply(parsed)
      return true
    }
    catch (e) {
      failures = Math.min(failures + 1, 10)
      deps.onError?.(e)
      return false
    }
    finally {
      polling = false
    }
  }

  function schedule() {
    if (!started)
      return
    const wait = failures ? Math.min(MAX_BACKOFF_MS, MIN_POLL_GAP_MS * 2 ** failures) : Math.max(0, MIN_POLL_GAP_MS - (now() - lastPollAt))
    timer = setTimer(() => {
      void pollOnce().finally(schedule)
    }, wait)
  }

  function toggleStep(id: string) {
    const next = new Set(expanded.value)
    if (next.has(id))
      next.delete(id)
    else
      next.add(id)
    expanded.value = next
  }

  function toggleTask(id: string) {
    const next = new Set(collapsedTasks.value)
    if (next.has(id))
      next.delete(id)
    else
      next.add(id)
    collapsedTasks.value = next
  }

  function setFilter(patch: Partial<TlFilter>) {
    filter.value = { ...filter.value, ...patch }
  }

  /** The picture for a ref, fetched once (a small cache); the panel shows a skeleton while `shots[ref]` is ''. */
  async function loadShot(s: TlShot): Promise<void> {
    if (shots.value[s.ref] !== undefined || !token)
      return
    shots.value = { ...shots.value, [s.ref]: '' }
    let url = '!'
    try {
      url = shotDataUrl(await deps.call(JARVIS_TIMELINE_SHOT_TOOL, { ref: s.ref, token: s.token, panel: token })) ?? '!'
    }
    catch (e) {
      deps.onError?.(e)
    }
    order.push(s.ref)
    const next = { ...shots.value, [s.ref]: url }
    while (order.length > SHOT_CACHE) {
      const old = order.shift() as string
      delete next[old]
    }
    shots.value = next
  }

  function loadShotsOf(step: TlStep) {
    for (const s of [step.shots?.before, step.shots?.after]) {
      if (s)
        void loadShot(s)
    }
  }

  /** The owner's click on Undo. `target` is exactly one step, one task, or a count; the server runs only that. */
  async function undo(target: { id: string } | { task: string } | { n: number }): Promise<UndoReport | null> {
    if (!token)
      return null
    const key = 'id' in target ? target.id : 'task' in target ? `task:${target.task}` : `n:${target.n}`
    if (undoing.value.has(key))
      return null
    undoing.value = new Set(undoing.value).add(key)
    error.value = ''
    try {
      const args: Record<string, unknown> = { panel: token }
      if ('id' in target)
        args.ids = [target.id]
      else if ('task' in target)
        args.task = target.task
      else
        args.n = target.n
      const rep = parseUndoReport(await deps.call(JARVIS_TIMELINE_UNDO_TOOL, args))
      if (!rep) {
        error.value = 'JARVIS did not answer.'
        return null
      }
      report.value = rep
      void refresh() // the journal has a new line: ask at once
      return rep
    }
    catch (e) {
      deps.onError?.(e)
      error.value = 'JARVIS did not answer.'
      return null
    }
    finally {
      const next = new Set(undoing.value)
      next.delete(key)
      undoing.value = next
    }
  }

  return {
    open,
    view,
    tasks,
    filter,
    expanded,
    collapsedTasks,
    undoing,
    report,
    error,
    openedAt,
    shots,
    pollOnce,
    refresh,
    setOpen,
    /** The owner's own key: open (or close) now, with the journal in hand. */
    toggle() {
      const opening = !open.value
      setOpen(opening)
      if (opening)
        void refresh()
    },
    toggleStep,
    toggleTask,
    setFilter,
    loadShot,
    loadShotsOf,
    undo,
    dismissReport() {
      report.value = null
    },
    start() {
      if (started)
        return
      started = true
      void pollOnce().finally(schedule)
    },
    stop() {
      started = false
      if (timer !== undefined)
        clearTimer(timer)
      timer = undefined
    },
  }
}

export type TimelineController = ReturnType<typeof useTimelineController>
