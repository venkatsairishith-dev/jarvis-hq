/**
 * The ask panel's state (D55: generated UI = declarative forms only). It talks to the JARVIS tool server (jarvis/bridge/ask_tool.py) over
 * the face's MCP connection, like the other jarvis composables:
 *   jarvis_ask_wanted   a long poll: returns the moment a form opens, changes or closes (or after ~20 s with "unchanged"). That is how the
 *                       panel appears in milliseconds without fast polling; it never asks again sooner than once a second.
 *   jarvis_ask_submit   the owner's click: which button, and the values of the form
 *   jarvis_ask_close    the owner closed the form
 *
 * A form is data. Nothing in it is run. Checkboxes and fields change at once (optimistic) and are sent only with a button. When the
 * server replaces the form (the cleaner's progress and result) the panel follows; the owner's ticks are only overwritten when the server
 * itself changed them (a chat command such as "also check the old installers").
 *
 * Switch off with localStorage `jarvis:ask` = `off`.
 */
import type { AskForm, AskValue } from './jarvis-ask-logic'
import type { ToolResult } from './jarvis-preview-logic'

import { computed, ref, shallowRef } from 'vue'

import { JARVIS_ASK_CLOSE_TOOL, JARVIS_ASK_SUBMIT_TOOL, JARVIS_ASK_WANTED_TOOL, missingRequired, parseAskWanted } from './jarvis-ask-logic'
import { toolData } from './jarvis-preview-logic'

export const JARVIS_ASK_OFF_KEY = 'jarvis:ask'
export const MIN_POLL_GAP_MS = 1_000
const MAX_BACKOFF_MS = 30_000
const WAIT_S = 20

export interface AskDeps {
  call: (name: string, args?: Record<string, unknown>) => Promise<ToolResult>
  enabled?: () => boolean
  onError?: (error: unknown) => void
  now?: () => number
  setTimer?: (fn: () => void, ms: number) => unknown
  clearTimer?: (handle: unknown) => void
  waitS?: number
}

export function askEnabled(storage: Pick<Storage, 'getItem'> | undefined): boolean {
  try {
    return storage?.getItem(JARVIS_ASK_OFF_KEY) !== 'off'
  }
  catch {
    return true
  }
}

export function useAskController(deps: AskDeps) {
  const now = deps.now ?? (() => Date.now())
  const setTimer = deps.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms))
  const clearTimer = deps.clearTimer ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>))
  const form = shallowRef<AskForm | null>(null)
  const values = ref<Record<string, AskValue>>({})
  const version = ref(0)
  const submitting = ref(false)
  const message = ref('')
  const endedAt = ref(0)
  const error = ref('')
  const openedAt = ref(0)
  const busy = computed(() => submitting.value || form.value?.busy === true)

  let serverValues = ''
  let failures = 0
  let timer: unknown
  let started = false
  let polling = false
  let lastPollAt = 0

  function adopt(next: AskForm | null, v: number) {
    version.value = v
    if (!next) {
      if (form.value)
        endedAt.value = now() // the server took it away: timed out, or replaced by another form
      form.value = null
      values.value = {}
      serverValues = ''
      submitting.value = false
      return
    }
    const sv = JSON.stringify(next.values)
    const isNew = form.value?.id !== next.id
    if (isNew) {
      openedAt.value = now()
      message.value = ''
      error.value = ''
    }
    if (isNew || sv !== serverValues)
      values.value = { ...next.values } as Record<string, AskValue>
    serverValues = sv
    form.value = next
    if (!next.busy)
      submitting.value = false
  }

  /** One long poll. Returns true when the server answered (a form, no form, or unchanged). */
  async function pollOnce(): Promise<boolean> {
    if (polling || (deps.enabled && !deps.enabled()))
      return false
    polling = true
    lastPollAt = now()
    try {
      const parsed = parseAskWanted(await deps.call(JARVIS_ASK_WANTED_TOOL, { seen: version.value, wait_s: deps.waitS ?? WAIT_S }))
      failures = 0
      if (!parsed)
        return false
      if (!parsed.unchanged)
        adopt(parsed.form, parsed.version)
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

  function setValue(id: string, value: AskValue) {
    values.value = { ...values.value, [id]: value }
  }

  /** A button. The owner's click is the confirmation; the server checks the values again and refuses anything not on the form. */
  async function press(buttonId: string): Promise<boolean> {
    const f = form.value
    if (!f || submitting.value || f.busy)
      return false
    const button = f.buttons.find(b => b.id === buttonId)
    if (!button)
      return false
    if (button.style === 'primary') {
      const missing = missingRequired(f.fields, values.value)
      if (missing) {
        error.value = `${missing.label || 'This'} needs an answer.`
        return false
      }
    }
    error.value = ''
    submitting.value = true
    try {
      const result = toolData(await deps.call(JARVIS_ASK_SUBMIT_TOOL, { form_id: f.id, token: f.token, button: buttonId, values: values.value }))
      if (!result || result.ok !== true) {
        error.value = String(result?.message ?? 'JARVIS did not take that.').slice(0, 160)
        submitting.value = false
        return false
      }
      if (result.done === true) {
        form.value = null
        values.value = {}
        submitting.value = false
      }
      else {
        void pollOnce() // the server has changed the form (progress): ask at once
      }
      return true
    }
    catch (e) {
      deps.onError?.(e)
      error.value = 'JARVIS did not answer.'
      submitting.value = false
      return false
    }
  }

  async function close(): Promise<void> {
    const f = form.value
    if (!f)
      return
    form.value = null // gone from the screen at once; the server is told next
    values.value = {}
    submitting.value = false
    try {
      await deps.call(JARVIS_ASK_CLOSE_TOOL, { form_id: f.id, token: f.token })
    }
    catch (e) {
      deps.onError?.(e)
    }
  }

  return {
    form,
    values,
    busy,
    submitting,
    error,
    message,
    endedAt,
    version,
    openedAt,
    pollOnce,
    setValue,
    press,
    close,
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

export type AskController = ReturnType<typeof useAskController>
