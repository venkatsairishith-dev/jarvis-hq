<script setup lang="ts">
import type { Row, TlStep } from '../../composables/jarvis-timeline-logic'
/**
 * JARVIS's Timeline panel (D61): what JARVIS did on this PC, newest first, grouped by the owner's request. State and tool-server calls live in
 * `useTimelineController` (../../composables/use-jarvis-timeline.ts), the rules in ../../composables/jarvis-timeline-logic.ts.
 *
 * It is one virtual list of rows with fixed heights (a task header, a step, the open step's detail), so a month of steps costs the same as a
 * dozen: only the rows in view are in the page. It answers the width of ITS window (a container query and a measured layout name), so the
 * face's small default window gets two-line rows and a large one gets one-line rows. Draws text only (no markup, no link, no script); the
 * pictures are local JPEGs from the owner's own journal.
 *
 * Keyboard: Tab into the list, Up/Down/Home/End/PageUp/PageDown move, Enter or Space opens a step, U undoes it, Esc closes; Ctrl+Alt+T opens or
 * closes the panel from anywhere in the window. Reduced motion stops the entrance and the skeleton. Dark and light follow `html.dark`.
 */
import type { TimelineController } from '../../composables/use-jarvis-timeline'

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import {
  buildRows,
  CLASS_TEXT,
  expectedPanelWidth,
  formatClock,
  formatDay,
  formatDuration,
  HEIGHTS,
  keyCommand,
  layoutFor,
  layoutRows,
  plural,
  scrollToRow,
  stepAria,
  taskLine,
  undoneText,
  VERIFIED_TEXT,
  visibleWindow,
} from '../../composables/jarvis-timeline-logic'

import '../jarvis-ui-tokens.css'
import './timeline.css'

const props = defineProps<{ controller: TimelineController }>()
const c = props.controller

const panel = ref<HTMLElement | null>(null)
const scroller = ref<HTMLElement | null>(null)
// what the window says now; the measured sizes replace these as soon as the browser has laid the panel out (no forced layout on the way in)
const width = ref(typeof window === 'undefined' ? 0 : expectedPanelWidth(window.innerWidth))
const viewport = ref(typeof window === 'undefined' ? 400 : Math.max(200, window.innerHeight - 190))
const scrollTop = ref(0)
const selKey = ref('')
let returnTo: HTMLElement | null = null
let observer: ResizeObserver | undefined
let frame = 0

const layoutName = computed(() => layoutFor(width.value))
const rows = computed<Row[]>(() => buildRows(c.tasks.value, c.expanded.value, c.collapsedTasks.value))
const layout = computed(() => layoutRows(rows.value, layoutName.value))
const win = computed(() => visibleWindow(layout.value, rows.value, scrollTop.value, viewport.value))
const visible = computed(() => rows.value.slice(win.value.start, win.value.end))
const selIndex = computed(() => rows.value.findIndex(r => r.key === selKey.value))
const now = computed(() => c.view.value?.now || Date.now() / 1000)
const summary = computed(() => c.view.value?.summary)
const loading = computed(() => c.open.value && !c.view.value)
const unavailable = computed(() => c.view.value && !c.view.value.available ? c.view.value.reason || 'The activity journal is not available on this PC.' : '')
const empty = computed(() => !!c.view.value && c.view.value.available && rows.value.length === 0)
const subtitle = computed(() => {
  const s = summary.value
  if (!s)
    return 'Looking...'
  const bits = [`${plural(s.steps, 'step')} in the last day`]
  if (s.failed)
    bits.push(`${s.failed} failed`)
  if (s.unconfirmed)
    bits.push(`${s.unconfirmed} unconfirmed`)
  return bits.join(' · ')
})
const filterNote = computed(() => {
  const f = c.filter.value
  if (!f.today && !f.failedOnly)
    return ''
  const n = c.tasks.value.reduce((a, t) => a + t.steps.length, 0)
  return `${plural(n, 'step')} shown`
})
const activeId = computed(() => {
  const r = rows.value[selIndex.value]
  return r && visible.value.includes(r) ? `tl-${r.key}` : undefined
})

function heightOf(row: Row): string {
  return `${HEIGHTS[layoutName.value][row.kind]}px`
}

function measure() {
  const p = panel.value
  const s = scroller.value
  if (p)
    width.value = p.getBoundingClientRect().width
  if (s) {
    viewport.value = s.clientHeight || viewport.value
    scrollTop.value = s.scrollTop
  }
}

function onScroll() {
  if (frame)
    return
  frame = requestAnimationFrame(() => {
    frame = 0
    if (scroller.value)
      scrollTop.value = scroller.value.scrollTop
  })
}

function keep(index: number) {
  const s = scroller.value
  if (!s || index < 0)
    return
  const to = scrollToRow(layout.value, rows.value, layoutName.value, index, s.scrollTop, s.clientHeight)
  if (to !== s.scrollTop) {
    s.scrollTop = to
    scrollTop.value = to
  }
}

function select(key: string) {
  selKey.value = key
}

function openStep(step: TlStep) {
  c.toggleStep(step.id)
  if (c.expanded.value.has(step.id)) {
    c.loadShotsOf(step)
    // the detail opens under the step: bring all of it into view
    void nextTick(() => keep(rows.value.findIndex(r => r.key === `d:${step.id}`)))
  }
}

function onRow(row: Row) {
  select(row.key)
  if (row.kind === 'step')
    openStep(row.step)
  else if (row.kind === 'task')
    c.toggleTask(row.task.id)
}

function currentStep(): TlStep | null {
  const r = rows.value[selIndex.value]
  return r && (r.kind === 'step' || r.kind === 'detail') ? r.step : null
}

function close() {
  c.setOpen(false)
}

function onKey(event: KeyboardEvent) {
  if (event.ctrlKey || event.metaKey || event.altKey)
    return
  const target = event.target as HTMLElement
  const cmd = keyCommand(event.key, selIndex.value, rows.value.length, { tag: target.tagName, role: target.getAttribute('role') ?? undefined })
  if (!cmd)
    return
  event.preventDefault()
  event.stopPropagation()
  if (cmd.kind === 'close') {
    close()
  }
  else if (cmd.kind === 'move') {
    selKey.value = rows.value[cmd.to]?.key ?? ''
    keep(cmd.to)
  }
  else if (cmd.kind === 'page') {
    const per = Math.max(1, Math.floor(viewport.value / HEIGHTS[layoutName.value].step))
    const to = Math.min(rows.value.length - 1, Math.max(0, (selIndex.value < 0 ? 0 : selIndex.value) + cmd.dir * per))
    selKey.value = rows.value[to]?.key ?? ''
    keep(to)
  }
  else if (cmd.kind === 'toggle') {
    const r = rows.value[selIndex.value]
    if (r)
      onRow(r)
  }
  else if (cmd.kind === 'undo') {
    const s = currentStep()
    if (s?.undoable)
      void c.undo({ id: s.id })
  }
}

function shotState(ref_: string | undefined): 'none' | 'loading' | 'ready' | 'failed' {
  if (!ref_)
    return 'none'
  const v = c.shots.value[ref_]
  return v === undefined || v === '' ? 'loading' : v === '!' ? 'failed' : 'ready'
}

// Focus: into the panel when it opens, back where it was when it closes.
watch(() => c.open.value, async (isOpen) => {
  if (isOpen) {
    returnTo = document.activeElement instanceof HTMLElement ? document.activeElement : null
    await nextTick()
    if (!observer && typeof ResizeObserver !== 'undefined' && panel.value) {
      observer = new ResizeObserver(() => measure()) // reports the size after layout, before the first paint
      observer.observe(panel.value)
      if (scroller.value)
        observer.observe(scroller.value)
    }
    else {
      measure()
    }
    ;(scroller.value ?? panel.value)?.focus({ preventScroll: true })
  }
  else {
    observer?.disconnect()
    observer = undefined
    if (returnTo?.isConnected)
      returnTo.focus({ preventScroll: true })
    returnTo = null
  }
}, { flush: 'post' })

// A new first row (a step just happened) keeps the view where it is, unless the owner is at the top.
watch(rows, async () => {
  await nextTick()
  if (scroller.value && scroller.value.clientHeight !== viewport.value)
    measure()
})

onMounted(() => {
  if (c.open.value)
    nextTick(measure)
})
onBeforeUnmount(() => {
  observer?.disconnect()
  if (frame)
    cancelAnimationFrame(frame)
})
</script>

<template>
  <div class="tl-root">
    <section
      v-if="c.open.value"
      ref="panel"
      class="tl"
      :class="`tl-${layoutName}`"
      role="dialog"
      aria-label="What JARVIS did"
      tabindex="-1"
      @keydown="onKey"
    >
      <header class="tl-head">
        <div class="tl-titles">
          <h2 class="tl-title">
            What JARVIS did
          </h2>
          <p class="tl-sub">
            {{ subtitle }}
          </p>
        </div>
        <button type="button" class="tl-x" aria-label="Close" title="Close (Esc)" @click="close">
          &#10005;
        </button>
      </header>

      <div class="tl-bar" role="group" aria-label="Filter the steps">
        <button type="button" class="tl-chip" :aria-pressed="c.filter.value.today" @click="c.setFilter({ today: !c.filter.value.today })">
          Today
        </button>
        <button type="button" class="tl-chip" :aria-pressed="c.filter.value.failedOnly" @click="c.setFilter({ failedOnly: !c.filter.value.failedOnly })">
          Failed only
        </button>
        <span v-if="filterNote" class="tl-note" aria-live="polite">{{ filterNote }}</span>
      </div>

      <div v-if="c.report.value" class="tl-report" role="status">
        <p class="tl-report-text">
          {{ c.report.value.summary }}
        </p>
        <button type="button" class="tl-report-x" aria-label="Dismiss" @click="c.dismissReport()">
          &#10005;
        </button>
      </div>
      <p v-if="c.error.value" class="tl-error" role="alert">
        {{ c.error.value }}
      </p>

      <div
        ref="scroller"
        class="tl-list"
        role="list"
        tabindex="0"
        aria-label="Steps, newest first"
        :aria-activedescendant="activeId"
        :aria-busy="loading"
        @scroll.passive="onScroll"
      >
        <template v-if="loading">
          <div v-for="n in 6" :key="n" class="tl-skel" aria-hidden="true" />
        </template>
        <p v-else-if="unavailable" class="tl-empty">
          {{ unavailable }}
        </p>
        <p v-else-if="empty" class="tl-empty">
          {{ c.filter.value.today || c.filter.value.failedOnly ? 'Nothing matches this filter.' : 'Nothing yet. When JARVIS does something on this PC, it shows up here.' }}
        </p>
        <template v-else>
          <div :style="{ height: `${win.padTop}px` }" aria-hidden="true" />
          <template v-for="row in visible" :key="row.key">
            <div
              v-if="row.kind === 'task'"
              :id="`tl-${row.key}`"
              class="tl-task"
              :class="{ 'tl-sel': selKey === row.key, 'tl-collapsed': c.collapsedTasks.value.has(row.task.id) }"
              role="listitem"
              :style="{ height: heightOf(row) }"
              @click="onRow(row)"
            >
              <div class="tl-task-main">
                <span class="tl-twist" aria-hidden="true">&#9662;</span>
                <span class="tl-task-title" :title="row.task.title">{{ row.task.title || 'A step JARVIS took' }}</span>
              </div>
              <div class="tl-task-meta">
                <span class="tl-task-info">{{ formatDay(row.task.ts, now) }} {{ formatClock(row.task.ts) }} &middot; {{ taskLine(row.task) }}</span>
                <button
                  v-if="row.task.undoable > 0"
                  type="button"
                  class="tl-btn"
                  :disabled="c.undoing.value.has(`task:${row.task.id}`)"
                  :title="`Undo ${plural(row.task.undoable, 'step')} of this task, newest first`"
                  @click.stop="c.undo({ task: row.task.id })"
                >
                  {{ c.undoing.value.has(`task:${row.task.id}`) ? 'Undoing...' : 'Undo this task' }}
                </button>
              </div>
            </div>

            <div
              v-else-if="row.kind === 'step'"
              :id="`tl-${row.key}`"
              class="tl-step"
              :class="{ 'tl-sel': selKey === row.key, 'tl-open': c.expanded.value.has(row.step.id), 'tl-was-undone': !!row.step.undone, 'tl-isundo': row.step.isUndo }"
              role="listitem"
              :aria-label="stepAria(row.step)"
              :aria-expanded="c.expanded.value.has(row.step.id)"
              :data-verified="row.step.verified"
              :style="{ height: heightOf(row) }"
              @click="onRow(row)"
            >
              <span class="tl-time">{{ formatClock(row.step.ts) }}</span>
              <span class="tl-ico" :data-v="row.step.verified" role="img" :aria-label="VERIFIED_TEXT[row.step.verified]" :title="VERIFIED_TEXT[row.step.verified]">
                <svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
                  <circle cx="8" cy="8" r="6.4" />
                  <path v-if="row.step.verified === 'verified'" d="M4.9 8.3l2.2 2.2 4-4.4" />
                  <path v-else-if="row.step.verified === 'failed'" d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8" />
                  <path v-else d="M6.3 6.4a1.8 1.8 0 113 1.3c-.7.5-1.2.8-1.2 1.6M8.1 11.3v.1" />
                </svg>
              </span>
              <span class="tl-label" :title="row.step.label">{{ row.step.label }}</span>
              <span class="tl-meta">
                <span class="tl-badge" :data-c="row.step.cls" role="img" :aria-label="CLASS_TEXT[row.step.cls]" :title="CLASS_TEXT[row.step.cls]">{{ row.step.cls }}</span>
                <span class="tl-dur">{{ formatDuration(row.step.durationMs) }}</span>
              </span>
              <span class="tl-act">
                <span v-if="row.step.undone" class="tl-done">{{ undoneText(row.step) }}</span>
                <button
                  v-else-if="row.step.undoable"
                  type="button"
                  class="tl-btn tl-btn-undo"
                  :disabled="c.undoing.value.has(row.step.id)"
                  :title="row.step.undoText"
                  @click.stop="c.undo({ id: row.step.id })"
                >
                  {{ c.undoing.value.has(row.step.id) ? '...' : 'Undo' }}
                </button>
              </span>
            </div>

            <div v-else :id="`tl-${row.key}`" class="tl-detail" :style="{ height: heightOf(row) }" role="listitem">
              <p class="tl-detail-text">
                {{ row.step.result || 'No details were kept for this step.' }}
              </p>
              <p v-if="row.step.undone" class="tl-detail-undo">
                {{ undoneText(row.step) }}.
              </p>
              <p v-else-if="row.step.undoable" class="tl-detail-undo">
                Undo: {{ row.step.undoText }}.
              </p>
              <p v-else-if="row.step.cannot" class="tl-detail-undo tl-cannot">
                {{ row.step.cannot }}.
              </p>
              <p v-if="row.step.tried && !row.step.undone" class="tl-detail-undo tl-cannot">
                The last undo did not go through ({{ row.step.tried.status }}).
              </p>
              <div class="tl-shots">
                <figure v-for="which in (['before', 'after'] as const)" :key="which" class="tl-shot">
                  <figcaption>{{ which === 'before' ? 'Before' : 'After' }}</figcaption>
                  <div class="tl-frame">
                    <img v-if="shotState(row.step.shots?.[which]?.ref) === 'ready'" :src="c.shots.value[row.step.shots![which]!.ref]" :alt="`${which === 'before' ? 'Before' : 'After'}: the window this step acted on`" draggable="false">
                    <span v-else-if="shotState(row.step.shots?.[which]?.ref) === 'loading'" class="tl-skel-img" aria-hidden="true" />
                    <span v-else class="tl-noshot">{{ shotState(row.step.shots?.[which]?.ref) === 'failed' ? 'Picture deleted (kept 7 days)' : 'No picture' }}</span>
                  </div>
                </figure>
              </div>
            </div>
          </template>
          <div :style="{ height: `${win.padBottom}px` }" aria-hidden="true" />
        </template>
      </div>

      <footer class="tl-foot">
        <span class="tl-keys">&#8593;&#8595; move &middot; Enter details &middot; U undo &middot; Esc close</span>
      </footer>
    </section>
  </div>
</template>
