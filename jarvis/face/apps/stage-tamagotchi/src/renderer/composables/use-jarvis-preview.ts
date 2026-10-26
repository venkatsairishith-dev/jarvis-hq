/**
 * The Preview panel's state: the list of things JARVIS made, the one that is open, paging through a deck or PDF, and "show me X".
 *
 * It asks the JARVIS tool server (jarvis/bridge/face_tools.py, over the face's MCP connection, like the other jarvis composables):
 *   jarvis_outputs          the recent deliverables (title, kind, size, checks passed)
 *   jarvis_output_get       one item ready to display: image, page image, text or HTML source
 *   jarvis_preview_wanted   ids that the chat model queued with jarvis_show ("show me the Q3 deck"); each arrives once
 * and hands "open in app" to the existing deliverables entry `open_output`. It decides nothing about what may be read: the server only
 * serves files inside the deliverables folder, and logic in ./jarvis-preview-logic.ts checks again what comes back.
 *
 * Polling costs one small local call every few seconds, backs off while the server does not answer, and can be switched off:
 * localStorage `jarvis:preview` = `off`.
 */
import type { Preview, PreviewItem, ToolResult } from './jarvis-preview-logic'

import { computed, ref, shallowRef } from 'vue'

import { POLL_WAIT_MS, withDeadline } from './poll-guard'

import {
  isPreviewId,
  JARVIS_OPEN_OUTPUT_TOOL,
  JARVIS_OUTPUT_GET_TOOL,
  JARVIS_OUTPUTS_TOOL,
  JARVIS_WANTED_TOOL,
  movedIndex,
  parseOutputs,
  parsePreview,
  parseWanted,
} from './jarvis-preview-logic'

export const JARVIS_PREVIEW_OFF_KEY = 'jarvis:preview'
const POLL_MS = 3_000
const MAX_POLL_MS = 60_000

export interface PreviewDeps {
  call: (name: string, args?: Record<string, unknown>) => Promise<ToolResult>
  enabled?: () => boolean
  /** True while nobody sees the window (minimized, hidden): the poll skips its turn and asks again at the usual time. */
  paused?: () => boolean
  onError?: (error: unknown) => void
  pollMs?: number
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
}

/** The owner can switch the panel's polling off from the browser tools' console: localStorage `jarvis:preview` = `off`. */
export function previewEnabled(storage: Pick<Storage, 'getItem'> | undefined): boolean {
  try {
    return storage?.getItem(JARVIS_PREVIEW_OFF_KEY) !== 'off'
  }
  catch {
    return true
  }
}

export function usePreviewController(deps: PreviewDeps) {
  const setTimer = deps.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms))
  const clearTimer = deps.clearTimer ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>))
  const items = shallowRef<PreviewItem[]>([])
  const current = shallowRef<Preview | null>(null)
  const open = ref(false)
  const loading = ref(false)
  const error = ref('')
  const note = ref('')
  const itemIndex = computed(() => current.value ? items.value.findIndex(i => i.id === current.value!.id) : -1)
  const pageCount = computed(() => current.value?.view === 'pages' ? current.value.pageCount ?? 1 : 1)
  const page = computed(() => current.value?.view === 'pages' ? current.value.page ?? 0 : 0)

  let seq = 0
  let timer: unknown
  let failures = 0
  let running = false
  let started = false

  async function refresh(): Promise<PreviewItem[]> {
    try {
      items.value = parseOutputs(await deps.call(JARVIS_OUTPUTS_TOOL, { limit: 20 }))
      error.value = ''
    }
    catch (e) {
      deps.onError?.(e)
      error.value = 'JARVIS did not answer.'
    }
    return items.value
  }

  /** Open one item (or one page of it). A slow earlier answer never replaces a newer request. */
  async function show(id: string, wantedPage = 0): Promise<boolean> {
    if (!isPreviewId(id))
      return false
    const mine = ++seq
    const keepThumbs = current.value?.id === id ? current.value.thumbs : undefined
    open.value = true
    loading.value = true
    error.value = ''
    try {
      const parsed = parsePreview(await deps.call(JARVIS_OUTPUT_GET_TOOL, { id, page: wantedPage, thumbs: !keepThumbs }))
      if (mine !== seq)
        return false
      if (!parsed.ok) {
        error.value = parsed.message
        return false
      }
      const next = parsed.preview
      if (next.view === 'pages' && !next.thumbs?.length && keepThumbs)
        next.thumbs = keepThumbs
      current.value = next
      return true
    }
    catch (e) {
      if (mine === seq) {
        deps.onError?.(e)
        error.value = 'JARVIS did not answer.'
      }
      return false
    }
    finally {
      if (mine === seq)
        loading.value = false
    }
  }

  /** Open the panel on the list; the newest item opens when nothing is open yet. */
  async function openPanel(): Promise<void> {
    open.value = true
    const list = await refresh()
    if (!current.value && list.length)
      await show(list[0].id)
  }

  function close(): void {
    seq++
    open.value = false
    loading.value = false
  }

  async function goToPage(n: number): Promise<void> {
    if (current.value?.view === 'pages')
      await show(current.value.id, movedIndex(0, n, pageCount.value))
  }

  async function nextItem(): Promise<void> {
    const i = itemIndex.value
    const target = items.value[movedIndex(i < 0 ? -1 : i, 1, items.value.length)]
    if (target && target.id !== current.value?.id)
      await show(target.id)
  }

  async function previousItem(): Promise<void> {
    const i = itemIndex.value
    const target = items.value[movedIndex(i < 0 ? 0 : i, -1, items.value.length)]
    if (target && target.id !== current.value?.id)
      await show(target.id)
  }

  /** Next page of a deck or PDF; at its last page, the next thing in the list. */
  async function next(): Promise<void> {
    if (page.value < pageCount.value - 1)
      await goToPage(page.value + 1)
    else
      await nextItem()
  }

  async function previous(): Promise<void> {
    if (page.value > 0)
      await goToPage(page.value - 1)
    else
      await previousItem()
  }

  /** Hand the open file to the existing `open_output` entry (the owner's own app opens it). */
  async function openInApp(): Promise<boolean> {
    const path = current.value?.openPath
    if (!path)
      return false
    note.value = ''
    try {
      const result = await deps.call(JARVIS_OPEN_OUTPUT_TOOL, { path })
      if (result?.isError) {
        note.value = 'JARVIS could not open it in the app.'
        return false
      }
      note.value = 'Opened in its app.'
      return true
    }
    catch (e) {
      deps.onError?.(e)
      note.value = 'JARVIS could not open it in the app.'
      return false
    }
  }

  /** One check for items the chat model queued with jarvis_show; opens the newest one. Returns how many were waiting. */
  async function pollWanted(): Promise<number> {
    if (running || (deps.enabled && !deps.enabled()) || deps.paused?.())
      return 0
    running = true
    try {
      const ids = parseWanted(await withDeadline(deps.call(JARVIS_WANTED_TOOL), POLL_WAIT_MS, 'the preview poll'))
      failures = 0
      if (ids.length) {
        await refresh()
        await show(ids[ids.length - 1])
      }
      return ids.length
    }
    catch (e) {
      failures = Math.min(failures + 1, 10)
      deps.onError?.(e)
      return 0
    }
    finally {
      running = false
    }
  }

  function schedule() {
    if (!started)
      return
    const base = deps.pollMs ?? POLL_MS
    const delay = Math.min(MAX_POLL_MS, base * 2 ** failures)
    timer = setTimer(() => {
      void pollWanted().finally(schedule)
    }, delay)
  }

  return {
    items,
    current,
    open,
    loading,
    error,
    note,
    itemIndex,
    page,
    pageCount,
    refresh,
    show,
    openPanel,
    close,
    goToPage,
    next,
    previous,
    nextItem,
    previousItem,
    openInApp,
    pollWanted,
    start() {
      if (started)
        return
      started = true
      schedule()
    },
    stop() {
      started = false
      if (timer !== undefined)
        clearTimer(timer)
      timer = undefined
    },
  }
}

export type PreviewController = ReturnType<typeof usePreviewController>
