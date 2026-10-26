<script setup lang="ts">
/**
 * One part of a form (D55). The ask panel draws a declarative spec with this ONE component, which calls itself for the parts inside a part.
 * Everything that comes from the spec is drawn as text (Vue's `{{ }}` and attribute bindings, never raw markup); images are `data:` URLs of
 * four raster types and nothing else; there is no link, no iframe, no script and no network. A choice sends its new value up at once
 * (the checkbox reacts in the same frame), and nothing is sent to JARVIS until the owner presses a button.
 *
 * Long lists (more than 60 rows) draw only the rows in view (see `windowRange`), a collapsed section draws nothing until it is opened and
 * only the open tab is drawn, so a form with hundreds of rows paints and scrolls at once.
 */
import type { AskNode, AskOption, AskValue, ListRow } from '../../composables/jarvis-ask-logic'
import type { AskVoice } from '../../composables/use-jarvis-ask-voice'

import { computed, inject, nextTick, ref, watch } from 'vue'

import AskMic from './AskMic.vue'

import {
  barGeometry,
  chartMax,
  formatSize,
  isMulti,
  lineDiff,
  linePoints,
  listRows,
  moveIndex,
  nextSort,
  pieSlices,
  progressFraction,
  setGroup,
  sortRows,
  toggleIn,
  VIRTUAL_FROM,
  windowRange,
} from '../../composables/jarvis-ask-logic'
import { dictatable, joinDictation } from '../../composables/jarvis-ask-voice-logic'
import { imageDataUrl } from '../../composables/jarvis-preview-logic'
import { ASK_VOICE_KEY } from '../../composables/use-jarvis-ask-voice'

defineOptions({ name: 'AskNode' })

const props = defineProps<{ node: AskNode, values: Record<string, AskValue>, disabled?: boolean }>()
const emit = defineEmits<{ (e: 'set', id: string, value: AskValue): void }>()

let counter = 0
const uid = `ask-${Date.now().toString(36)}-${++counter}-${Math.random().toString(36).slice(2, 6)}`
const ROW_H = 48
const VIEW_H = 336

// Voice (D63): present only in a window that has a voice host. `hit` marks what a spoken command just changed.
const voice = inject<AskVoice | null>(ASK_VOICE_KEY, null)
function hit(key: string): boolean {
  return voice?.hit.value === key
}
const node = computed(() => props.node)
const id = computed(() => node.value.id ?? '')
const micOk = computed(() => !!voice && dictatable(node.value))
const listening = computed(() => voice?.listeningOn(id.value) ?? false)
const interimText = computed(() => voice?.interimFor(id.value) ?? '')
const value = computed(() => props.values[id.value])
const options = computed<AskOption[]>(() => node.value.options ?? [])
const picked = computed(() => new Set(Array.isArray(value.value) ? value.value as string[] : []))
const single = computed(() => typeof value.value === 'string' ? value.value : '')
/** The text a field shows: what is in it, and while it is being dictated the interim words after it. The final text replaces the interim. */
const shownText = computed(() => {
  const base = String(value.value ?? '')
  return interimText.value && node.value.type === 'text' ? joinDictation(base, interimText.value) : base
})

function set(v: AskValue) {
  if (id.value)
    emit('set', id.value, v)
}
function relay(i: string, v: AskValue) {
  emit('set', i, v)
}

// ---- section and tabs ---------------------------------------------------------------------------------------------------------------------
const open = ref(node.value.collapsed !== true)
const tab = ref(node.value.tabs?.[0]?.id ?? '')
function onTabKey(event: KeyboardEvent) {
  const tabs = node.value.tabs ?? []
  const i = tabs.findIndex(t => t.id === tab.value)
  const next = moveIndex(i, event.key, tabs.length)
  if (next >= 0 && next !== i && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
    event.preventDefault()
    tab.value = tabs[next].id
    void nextTick(() => (event.currentTarget as HTMLElement | null)?.querySelectorAll<HTMLElement>('[role=tab]')[next]?.focus())
  }
}

// ---- virtual lists (checkbox groups, radio lists, tables) --------------------------------------------------------------------------------------
const scrollTop = ref(0)
const rows = computed<ListRow[]>(() => listRows(options.value))
const virtual = computed(() => rows.value.length > VIRTUAL_FROM)
const win = computed(() => windowRange(scrollTop.value, VIEW_H, ROW_H, rows.value.length))
const visible = computed(() => rows.value.slice(win.value.start, win.value.end))
function onScroll(event: Event) {
  scrollTop.value = (event.target as HTMLElement).scrollTop
}
watch(() => node.value.options, () => {
  if (!virtual.value)
    scrollTop.value = 0
})

// ---- checkbox groups ------------------------------------------------------------------------------------------------------------------------
function groupIds(group?: string): string[] {
  return options.value.filter(o => o.group === group).map(o => o.id)
}
function groupState(group?: string): 'all' | 'some' | 'none' {
  const ids = groupIds(group)
  const n = ids.filter(i => picked.value.has(i)).length
  return n === 0 ? 'none' : n === ids.length ? 'all' : 'some'
}

// ---- radio-like lists (radio, segmented, cards, gallery, split) -------------------------------------------------------------------------------
const groupRef = ref<HTMLElement | null>(null)
function chooseOne(oid: string) {
  if (props.disabled)
    return
  if (isMulti(node.value))
    set(toggleIn(value.value, oid))
  else
    set(single.value === oid ? (node.value.required ? oid : '') : oid)
}
function onRadioKey(event: KeyboardEvent) {
  const list = options.value
  if (!list.length || !['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key))
    return
  event.preventDefault()
  const items = [...(groupRef.value?.querySelectorAll<HTMLElement>('[data-opt]') ?? [])]
  const at = items.findIndex(el => el === document.activeElement)
  const current = at >= 0 ? at : Math.max(0, list.findIndex(o => o.id === single.value))
  const next = moveIndex(current, event.key, items.length)
  const target = items[next]
  if (!target)
    return
  target.focus()
  if (!isMulti(node.value))
    set(target.dataset.opt ?? '')
}
function tabbable(o: AskOption, index: number): number {
  const has = options.value.some(x => single.value === x.id || picked.value.has(x.id))
  return (has ? (single.value === o.id || picked.value.has(o.id)) : index === 0) ? 0 : -1
}
const chosen = computed(() => options.value.find(o => o.id === single.value))

// ---- tables --------------------------------------------------------------------------------------------------------------------------------
const sort = ref<{ column: string | null, direction: 'asc' | 'desc' }>({ column: null, direction: 'asc' })
const tableRows = computed(() => sortRows((node.value.rows as Array<{ id: string, cells: Record<string, string> }> | undefined) ?? [], sort.value.column, sort.value.direction))
const tableCols = computed(() => (node.value.columns as Array<{ id: string, label: string, align?: string }> | undefined) ?? [])
const tableWin = computed(() => windowRange(scrollTop.value, VIEW_H, ROW_H, tableRows.value.length))
const tableVisible = computed(() => tableRows.value.slice(tableWin.value.start, tableWin.value.end))
const tableTemplate = computed(() => `${node.value.select === 'none' ? '' : '28px '}${tableCols.value.map((_c, i) => i === 0 ? 'minmax(0,1.6fr)' : 'minmax(0,1fr)').join(' ')}`)
const tablePicked = computed(() => node.value.select === 'many' ? picked.value : new Set([single.value]))
function pickRow(rid: string) {
  if (node.value.select === 'many')
    set(toggleIn(value.value, rid))
  else if (node.value.select === 'one')
    set(rid)
}
function onRowKey(event: KeyboardEvent, rid: string) {
  if (event.key === ' ' || event.key === 'Enter') {
    event.preventDefault()
    pickRow(rid)
    return
  }
  if (!['ArrowUp', 'ArrowDown'].includes(event.key))
    return
  event.preventDefault()
  const rowsEl = [...((event.currentTarget as HTMLElement).parentElement?.querySelectorAll<HTMLElement>('[role=row][data-row]') ?? [])]
  const at = rowsEl.indexOf(event.currentTarget as HTMLElement)
  rowsEl[moveIndex(at, event.key, rowsEl.length, false)]?.focus()
}

// ---- code, diff, charts, steps -----------------------------------------------------------------------------------------------------------
const copied = ref('')
async function copyCode() {
  try {
    await navigator.clipboard.writeText(String(node.value.text ?? ''))
    copied.value = 'Copied'
  }
  catch {
    copied.value = 'Select the text and press Ctrl+C'
  }
  setTimeout(() => (copied.value = ''), 2200)
}
const codeLines = computed(() => String(node.value.text ?? '').replace(/\n$/, '').split('\n'))
const diff = computed(() => node.value.type === 'diff_view' ? lineDiff(String(node.value.before ?? ''), String(node.value.after ?? '')) : [])
const chart = computed(() => node.value.type === 'chart'
  ? { kind: node.value.kind as 'bar' | 'line' | 'pie', labels: (node.value.labels as string[]) ?? [], series: (node.value.series as Array<{ name: string, values: number[] }>) ?? [] }
  : null)
const CW = 320
const CH = 140
const bars = computed(() => chart.value?.kind === 'bar' ? barGeometry(chart.value, CW, CH - 18) : [])
const lines = computed(() => chart.value?.kind === 'line' ? linePoints(chart.value, CW, CH - 18) : [])
const slices = computed(() => chart.value?.kind === 'pie' ? pieSlices(chart.value, 56) : [])
const chartTop = computed(() => chart.value ? chartMax(chart.value.series) : 1)
const chartSummary = computed(() => chart.value ? `${node.value.label || 'Chart'}: ${chart.value.series.map(s => `${s.name || 'values'} ${s.values.join(', ')}`).join('; ')}` : '')
const fraction = computed(() => node.value.type === 'progress' && node.value.indeterminate !== true
  ? progressFraction({ done: Number(node.value.value), total: Number(node.value.max) })
  : null)
const stepMark: Record<string, string> = { pending: '○', running: '◐', done: '✓', failed: '✕', skipped: '–' }

function url(o: AskOption): string | null {
  return o.thumb ? imageDataUrl(o.thumb) : null
}
function size(o: { size?: number | string }): string {
  return o.size === undefined ? '' : formatSize(o.size)
}
</script>

<template>
  <!-- layout -->
  <section v-if="node.type === 'section'" class="ask-section" :class="{ 'ask-open': open }">
    <button type="button" class="ask-section-head" :aria-expanded="open" @click="open = !open">
      <span class="ask-chevron" aria-hidden="true" />
      <span class="ask-section-title">{{ node.label }}</span>
    </button>
    <div v-if="open" class="ask-section-body">
      <AskNode v-for="(c, i) in node.children ?? []" :key="c.id ?? `${c.type}${i}`" :node="c" :values="values" :disabled="disabled" @set="relay" />
    </div>
  </section>

  <div v-else-if="node.type === 'row' || node.type === 'column' || node.type === 'grid'" :class="`ask-lay-${node.type}`">
    <AskNode v-for="(c, i) in node.children ?? []" :key="c.id ?? `${c.type}${i}`" :node="c" :values="values" :disabled="disabled" @set="relay" />
  </div>

  <div v-else-if="node.type === 'tabs'" class="ask-tabs">
    <div class="ask-tablist" role="tablist" @keydown="onTabKey">
      <button
        v-for="t in node.tabs"
        :id="`${uid}-t-${t.id}`"
        :key="t.id"
        type="button"
        role="tab"
        class="ask-tab"
        :aria-selected="tab === t.id"
        :aria-controls="`${uid}-p-${t.id}`"
        :tabindex="tab === t.id ? 0 : -1"
        @click="tab = t.id"
      >
        {{ t.label }}
      </button>
    </div>
    <template v-for="t in node.tabs" :key="t.id">
      <div v-if="tab === t.id" :id="`${uid}-p-${t.id}`" role="tabpanel" class="ask-tabpanel" :aria-labelledby="`${uid}-t-${t.id}`">
        <AskNode v-for="(c, i) in t.children" :key="c.id ?? `${c.type}${i}`" :node="c" :values="values" :disabled="disabled" @set="relay" />
      </div>
    </template>
  </div>

  <!-- checkbox group -->
  <fieldset v-else-if="node.type === 'checkbox_group'" class="ask-field" :class="{ 'ask-hit': hit(`f:${id}`) }" :data-voice-key="`f:${id}`" :disabled="disabled">
    <legend class="ask-sr">
      {{ node.label }}
    </legend>
    <div class="ask-list" :class="{ 'ask-virtual': virtual }" :style="virtual ? { maxHeight: `${VIEW_H}px` } : undefined" @scroll.passive="virtual && onScroll($event)">
      <div v-if="virtual" :style="{ height: `${win.padTop}px` }" aria-hidden="true" />
      <template v-for="r in virtual ? visible : rows" :key="r.key">
        <label v-if="r.kind === 'header'" class="ask-row ask-group-row">
          <input
            type="checkbox"
            :checked="groupState(r.group) === 'all'"
            :indeterminate="groupState(r.group) === 'some'"
            :aria-label="`Select all in ${r.group}`"
            @change="set(setGroup(value, groupIds(r.group), ($event.target as HTMLInputElement).checked))"
          >
          <span class="ask-row-text"><span class="ask-row-title">{{ r.group }}</span></span>
          <span class="ask-row-size">{{ r.groupCount }} items</span>
        </label>
        <label v-else class="ask-row" :class="{ 'ask-row-on': picked.has(r.option!.id), 'ask-row-indent': !!r.option!.group, 'ask-hit': hit(`o:${id}/${r.option!.id}`) }" :data-voice-key="`o:${id}/${r.option!.id}`">
          <input type="checkbox" :checked="picked.has(r.option!.id)" @change="set(toggleIn(value, r.option!.id))">
          <span class="ask-row-text">
            <span class="ask-row-title">{{ r.option!.label }}</span>
            <span v-if="r.option!.detail" class="ask-row-detail">{{ r.option!.detail }}</span>
          </span>
          <span v-if="r.option!.size !== undefined" class="ask-row-size">{{ size(r.option!) }}</span>
        </label>
      </template>
      <div v-if="virtual" :style="{ height: `${win.padBottom}px` }" aria-hidden="true" />
    </div>
  </fieldset>

  <!-- radio, segmented, split, cards, gallery, file choice -->
  <div
    v-else-if="node.type === 'radio' && node.variant === 'segmented'"
    class="ask-field"
  >
    <span :id="`${uid}-l`" class="ask-label">{{ node.label }}</span>
    <div ref="groupRef" class="ask-segmented" role="radiogroup" :aria-labelledby="`${uid}-l`" @keydown="onRadioKey">
      <button
        v-for="(o, i) in options"
        :key="o.id"
        type="button"
        role="radio"
        class="ask-seg"
        :class="{ 'ask-hit': hit(`o:${id}/${o.id}`) }"
        :data-voice-key="`o:${id}/${o.id}`"
        :data-opt="o.id"
        :aria-checked="single === o.id"
        :tabindex="tabbable(o, i)"
        :disabled="disabled"
        @click="chooseOne(o.id)"
      >
        {{ o.label }}
      </button>
    </div>
  </div>

  <div v-else-if="node.type === 'radio' || node.type === 'file_choice' || node.type === 'split'" class="ask-field" :class="{ 'ask-split': node.type === 'split' }">
    <span :id="`${uid}-l`" class="ask-label">{{ node.label }}</span>
    <div class="ask-splitwrap">
      <div
        ref="groupRef"
        class="ask-list"
        :class="{ 'ask-virtual': virtual }"
        :style="virtual ? { maxHeight: `${VIEW_H}px` } : undefined"
        role="radiogroup"
        :aria-labelledby="`${uid}-l`"
        @keydown="onRadioKey"
        @scroll.passive="virtual && onScroll($event)"
      >
        <div v-if="virtual" :style="{ height: `${win.padTop}px` }" aria-hidden="true" />
        <div
          v-for="(r, i) in virtual ? visible : rows"
          :key="r.key"
          class="ask-row ask-radio-row"
          :class="{ 'ask-row-on': single === r.option?.id, 'ask-group-row': r.kind === 'header', 'ask-hit': !!r.option && hit(`o:${id}/${r.option.id}`) }"
          :data-voice-key="r.option ? `o:${id}/${r.option.id}` : undefined"
          :role="r.kind === 'header' ? undefined : 'radio'"
          :data-opt="r.option?.id"
          :aria-checked="r.kind === 'header' ? undefined : single === r.option?.id"
          :tabindex="r.kind === 'header' ? undefined : tabbable(r.option!, i)"
          @click="r.option && chooseOne(r.option.id)"
          @keydown.space.prevent="r.option && chooseOne(r.option.id)"
        >
          <template v-if="r.kind === 'header'">
            <span class="ask-row-text"><span class="ask-row-title">{{ r.group }}</span></span>
          </template>
          <template v-else>
            <span class="ask-dot" aria-hidden="true" />
            <img v-if="url(r.option!)" class="ask-thumb" :src="url(r.option!)!" alt="" decoding="async">
            <span class="ask-row-text">
              <span class="ask-row-title">{{ r.option!.label }}</span>
              <span v-if="r.option!.detail" class="ask-row-detail">{{ r.option!.detail }}</span>
            </span>
            <span v-if="r.option!.size !== undefined" class="ask-row-size">{{ size(r.option!) }}</span>
          </template>
        </div>
        <div v-if="virtual" :style="{ height: `${win.padBottom}px` }" aria-hidden="true" />
        <p v-if="!options.length" class="ask-empty">
          Nothing to choose from.
        </p>
      </div>
      <div v-if="node.type === 'split'" class="ask-detail" aria-live="polite">
        <template v-if="chosen">
          <strong>{{ chosen.label }}</strong>
          <p>{{ chosen.body || chosen.detail || '' }}</p>
        </template>
        <p v-else class="ask-dim">
          Pick one to read it here.
        </p>
      </div>
    </div>
  </div>

  <div v-else-if="node.type === 'cards'" class="ask-field">
    <span :id="`${uid}-l`" class="ask-label">{{ node.label }}</span>
    <div ref="groupRef" class="ask-cards" :role="node.multiple ? 'group' : 'radiogroup'" :aria-labelledby="`${uid}-l`" @keydown="onRadioKey">
      <button
        v-for="(o, i) in options"
        :key="o.id"
        type="button"
        :role="node.multiple ? 'checkbox' : 'radio'"
        class="ask-card"
        :class="{ 'ask-hit': hit(`o:${id}/${o.id}`) }"
        :data-voice-key="`o:${id}/${o.id}`"
        :data-opt="o.id"
        :aria-checked="node.multiple ? picked.has(o.id) : single === o.id"
        :tabindex="tabbable(o, i)"
        :disabled="disabled"
        @click="chooseOne(o.id)"
      >
        <span class="ask-card-title">{{ o.label }}<span v-if="o.badge" class="ask-badge">{{ o.badge }}</span></span>
        <span v-if="o.detail" class="ask-row-detail ask-wrap">{{ o.detail }}</span>
        <ul v-if="o.pros?.length || o.cons?.length" class="ask-proscons">
          <li v-for="p in o.pros" :key="`p${p}`" class="ask-pro">
            {{ p }}
          </li>
          <li v-for="c in o.cons" :key="`c${c}`" class="ask-con">
            {{ c }}
          </li>
        </ul>
      </button>
    </div>
  </div>

  <div v-else-if="node.type === 'gallery'" class="ask-field">
    <span :id="`${uid}-l`" class="ask-label">{{ node.label }}</span>
    <div ref="groupRef" class="ask-gallery" :role="node.multiple ? 'group' : 'radiogroup'" :aria-labelledby="`${uid}-l`" @keydown="onRadioKey">
      <button
        v-for="(o, i) in options"
        :key="o.id"
        type="button"
        :role="node.multiple ? 'checkbox' : 'radio'"
        class="ask-tile"
        :class="{ 'ask-hit': hit(`o:${id}/${o.id}`) }"
        :data-voice-key="`o:${id}/${o.id}`"
        :data-opt="o.id"
        :aria-checked="node.multiple ? picked.has(o.id) : single === o.id"
        :tabindex="tabbable(o, i)"
        :disabled="disabled"
        @click="chooseOne(o.id)"
      >
        <img v-if="url(o)" :src="url(o)!" :alt="o.label" decoding="async" loading="lazy">
        <span v-else class="ask-tile-blank" aria-hidden="true">?</span>
        <span class="ask-tile-name">{{ o.label }}</span>
      </button>
      <p v-if="!options.length" class="ask-empty">
        No pictures here.
      </p>
    </div>
  </div>

  <div v-else-if="node.type === 'select'" class="ask-field" :class="{ 'ask-hit': hit(`f:${id}`) || options.some(o => hit(`o:${id}/${o.id}`)) }" :data-voice-key="`f:${id}`">
    <label :for="`${uid}-i`" class="ask-label">{{ node.label }}</label>
    <select :id="`${uid}-i`" class="ask-input" :value="single" :disabled="disabled" @change="set(($event.target as HTMLSelectElement).value)">
      <option v-if="!node.required" value="">
        -
      </option>
      <option v-for="o in options" :key="o.id" :value="o.id">
        {{ o.label }}
      </option>
    </select>
  </div>

  <!-- simple inputs -->
  <div v-else-if="node.type === 'text'" class="ask-field" :class="{ 'ask-heading-field': node.heading === true, 'ask-hit': hit(`f:${id}`), 'ask-listening': listening }" :data-voice-key="`f:${id}`">
    <div class="ask-labelrow">
      <label :for="`${uid}-i`" class="ask-label">{{ node.label }}</label>
      <AskMic v-if="micOk" :field-id="id" :label="node.label ?? ''" :disabled="disabled" />
    </div>
    <textarea
      v-if="node.multiline"
      :id="`${uid}-i`"
      class="ask-input ask-textarea"
      rows="4"
      :maxlength="Number(node.max_len)"
      :placeholder="String(node.placeholder ?? '')"
      :value="shownText"
      :readonly="listening"
      :data-voice-input="micOk ? id : undefined"
      :disabled="disabled"
      @input="set(($event.target as HTMLTextAreaElement).value)"
    />
    <input
      v-else
      :id="`${uid}-i`"
      class="ask-input"
      :class="{ 'ask-heading-input': node.heading === true }"
      type="text"
      autocomplete="off"
      :maxlength="Number(node.max_len)"
      :placeholder="String(node.placeholder ?? '')"
      :value="shownText"
      :readonly="listening"
      :data-voice-input="micOk ? id : undefined"
      :disabled="disabled"
      @input="set(($event.target as HTMLInputElement).value)"
    >
  </div>

  <div v-else-if="node.type === 'number'" class="ask-field" :class="{ 'ask-hit': hit(`f:${id}`), 'ask-listening': listening }" :data-voice-key="`f:${id}`">
    <div class="ask-labelrow">
      <label :for="`${uid}-i`" class="ask-label">{{ node.label }}<span v-if="node.unit" class="ask-dim"> ({{ node.unit }})</span></label>
      <AskMic v-if="micOk" :field-id="id" :label="node.label ?? ''" :disabled="disabled" />
    </div>
    <input
      :id="`${uid}-i`"
      class="ask-input ask-mono"
      type="number"
      :min="Number(node.min)"
      :max="Number(node.max)"
      :step="Number(node.step)"
      :value="value === null || value === undefined ? '' : String(value)"
      :readonly="listening"
      :data-voice-input="micOk ? id : undefined"
      :disabled="disabled"
      @input="set(($event.target as HTMLInputElement).value === '' ? null : Number(($event.target as HTMLInputElement).value))"
    >
    <p v-if="interimText" class="ask-voice-interim" aria-hidden="true">
      {{ interimText }}
    </p>
  </div>

  <div v-else-if="node.type === 'slider'" class="ask-field" :class="{ 'ask-hit': hit(`f:${id}`) }" :data-voice-key="`f:${id}`">
    <label :for="`${uid}-i`" class="ask-label ask-between">
      <span>{{ node.label }}</span>
      <output class="ask-mono ask-live" :for="`${uid}-i`">{{ value ?? node.value }}{{ node.unit ? ` ${node.unit}` : '' }}</output>
    </label>
    <input
      :id="`${uid}-i`"
      class="ask-range"
      type="range"
      :min="Number(node.min)"
      :max="Number(node.max)"
      :step="Number(node.step)"
      :value="Number(value ?? node.value)"
      :disabled="disabled"
      @input="set(Number(($event.target as HTMLInputElement).value))"
    >
  </div>

  <div v-else-if="node.type === 'toggle'" class="ask-field ask-toggle-row" :class="{ 'ask-hit': hit(`f:${id}`) }" :data-voice-key="`f:${id}`">
    <span :id="`${uid}-l`" class="ask-label">{{ node.label }}</span>
    <button
      type="button"
      role="switch"
      class="ask-switch"
      :aria-checked="value === true"
      :aria-labelledby="`${uid}-l`"
      :disabled="disabled"
      @click="set(value !== true)"
    >
      <span class="ask-switch-knob" />
    </button>
  </div>

  <div v-else-if="node.type === 'date'" class="ask-field" :class="{ 'ask-hit': hit(`f:${id}`), 'ask-listening': listening }" :data-voice-key="`f:${id}`">
    <div class="ask-labelrow">
      <label :for="`${uid}-i`" class="ask-label">{{ node.label }}</label>
      <AskMic v-if="micOk" :field-id="id" :label="node.label ?? ''" :disabled="disabled" />
    </div>
    <input
      :id="`${uid}-i`"
      class="ask-input ask-mono"
      :type="node.mode === 'time' ? 'time' : node.mode === 'datetime' ? 'datetime-local' : 'date'"
      :value="String(value ?? '')"
      :readonly="listening"
      :data-voice-input="micOk ? id : undefined"
      :disabled="disabled"
      @input="set(($event.target as HTMLInputElement).value)"
    >
    <p v-if="interimText" class="ask-voice-interim" aria-hidden="true">
      {{ interimText }}
    </p>
  </div>

  <!-- table -->
  <div v-else-if="node.type === 'table'" class="ask-field">
    <span v-if="node.label" class="ask-label">{{ node.label }}</span>
    <div class="ask-table" role="table" :aria-label="node.label || 'Table'">
      <div class="ask-trow ask-thead" role="row" :style="{ gridTemplateColumns: tableTemplate }">
        <span v-if="node.select !== 'none'" role="columnheader" />
        <span v-for="c in tableCols" :key="c.id" role="columnheader" :aria-sort="sort.column === c.id ? (sort.direction === 'asc' ? 'ascending' : 'descending') : 'none'" :class="{ 'ask-right': c.align === 'right' }">
          <button v-if="node.sortable" type="button" class="ask-sort" @click="sort = nextSort(sort, c.id)">
            {{ c.label }}<span class="ask-sortmark" aria-hidden="true">{{ sort.column === c.id ? (sort.direction === 'asc' ? '▲' : '▼') : '' }}</span>
          </button>
          <template v-else>{{ c.label }}</template>
        </span>
      </div>
      <div class="ask-tbody" :class="{ 'ask-virtual': tableRows.length > VIRTUAL_FROM }" :style="tableRows.length > VIRTUAL_FROM ? { maxHeight: `${VIEW_H}px` } : undefined" @scroll.passive="tableRows.length > VIRTUAL_FROM && onScroll($event)">
        <div v-if="tableRows.length > VIRTUAL_FROM" :style="{ height: `${tableWin.padTop}px` }" aria-hidden="true" />
        <div
          v-for="r in tableRows.length > VIRTUAL_FROM ? tableVisible : tableRows"
          :key="r.id"
          class="ask-trow"
          :class="{ 'ask-row-on': tablePicked.has(r.id), 'ask-selectable': node.select !== 'none' }"
          role="row"
          data-row="1"
          :aria-selected="node.select === 'none' ? undefined : tablePicked.has(r.id)"
          :tabindex="node.select === 'none' ? undefined : 0"
          :style="{ gridTemplateColumns: tableTemplate }"
          @click="pickRow(r.id)"
          @keydown="onRowKey($event, r.id)"
        >
          <span v-if="node.select !== 'none'" class="ask-tick" aria-hidden="true">{{ tablePicked.has(r.id) ? '●' : '○' }}</span>
          <span v-for="c in tableCols" :key="c.id" role="cell" :class="{ 'ask-right': c.align === 'right', 'ask-mono': c.align === 'right' }">{{ r.cells[c.id] }}</span>
        </div>
        <div v-if="tableRows.length > VIRTUAL_FROM" :style="{ height: `${tableWin.padBottom}px` }" aria-hidden="true" />
        <p v-if="!tableRows.length" class="ask-empty">
          Nothing to show.
        </p>
      </div>
    </div>
  </div>

  <!-- display -->
  <div v-else-if="node.type === 'info_table'" class="ask-field">
    <span v-if="node.label" class="ask-label">{{ node.label }}</span>
    <dl class="ask-info">
      <div v-for="(r, i) in (node.rows as string[][] | undefined) ?? []" :key="i" class="ask-info-row">
        <dt>{{ r[0] }}</dt>
        <dd class="ask-mono">
          {{ r[1] }}
        </dd>
      </div>
    </dl>
  </div>

  <div v-else-if="node.type === 'chart' && chart" class="ask-field">
    <span v-if="node.label" class="ask-label">{{ node.label }}</span>
    <svg class="ask-chart" :viewBox="`0 0 ${CW} ${CH}`" role="img" :aria-label="chartSummary" preserveAspectRatio="xMidYMid meet">
      <g v-if="chart.kind === 'bar'">
        <line :x1="4" :x2="CW - 4" :y1="CH - 22" :y2="CH - 22" class="ask-axis" />
        <rect v-for="(b, i) in bars" :key="i" :x="b.x" :y="b.y" :width="b.w" :height="b.h" rx="2" :class="`ask-s${(b.series % 6) + 1}`">
          <title>{{ b.label }}: {{ b.value }}</title>
        </rect>
        <text v-for="(l, i) in chart.labels" :key="`l${i}`" :x="4 + (i + 0.5) * ((CW - 8) / chart.labels.length)" :y="CH - 6" text-anchor="middle" class="ask-axis-text">{{ chart.labels.length > 10 && i % 2 ? '' : l }}</text>
        <text :x="CW - 4" y="10" text-anchor="end" class="ask-axis-text">{{ chartTop }}</text>
      </g>
      <g v-else-if="chart.kind === 'line'">
        <line :x1="4" :x2="CW - 4" :y1="CH - 22" :y2="CH - 22" class="ask-axis" />
        <polyline v-for="(p, i) in lines" :key="i" :points="p" fill="none" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" :class="`ask-stroke-s${(i % 6) + 1}`" />
        <text :x="CW - 4" y="10" text-anchor="end" class="ask-axis-text">{{ chartTop }}</text>
        <text v-for="(l, i) in chart.labels" :key="`l${i}`" :x="6 + i / Math.max(1, chart.labels.length - 1) * (CW - 12)" :y="CH - 6" text-anchor="middle" class="ask-axis-text">{{ chart.labels.length > 8 && i % 2 ? '' : l }}</text>
      </g>
      <g v-else :transform="`translate(${CW / 2 - 40} ${CH / 2 - 8})`">
        <path v-for="s in slices" :key="s.index" :d="s.path" :class="`ask-s${(s.index % 6) + 1}`" stroke="var(--ask-bg)" stroke-width="1.5">
          <title>{{ s.label }}: {{ s.value }}</title>
        </path>
      </g>
    </svg>
    <ul class="ask-legend">
      <li v-for="(s, i) in chart.kind === 'pie' ? slices : chart.series" :key="i">
        <span class="ask-swatch" :class="`ask-s${(i % 6) + 1}`" aria-hidden="true" />{{ chart.kind === 'pie' ? `${(s as any).label} · ${Math.round((s as any).fraction * 100)}%` : (s as any).name }}
      </li>
    </ul>
  </div>

  <div v-else-if="node.type === 'code_view'" class="ask-field">
    <div class="ask-codehead">
      <span class="ask-label ask-mono">{{ node.label || node.language }}</span>
      <button type="button" class="ask-small" @click="copyCode">
        {{ copied || 'Copy' }}
      </button>
    </div>
    <pre class="ask-code" tabindex="0" :aria-label="`${node.language} code`"><code><span v-for="(line, i) in codeLines" :key="i" class="ask-line">{{ line }}</span></code></pre>
  </div>

  <div v-else-if="node.type === 'diff_view'" class="ask-field">
    <span v-if="node.label" class="ask-label">{{ node.label }}</span>
    <pre class="ask-code ask-diff" tabindex="0" aria-label="Changes"><code><span v-for="(d, i) in diff" :key="i" class="ask-line" :class="`ask-d-${d.kind}`"><span class="ask-dmark" aria-hidden="true">{{ d.kind === 'add' ? '+' : d.kind === 'del' ? '-' : ' ' }}</span>{{ d.text }}</span></code></pre>
  </div>

  <ol v-else-if="node.type === 'steps'" class="ask-steps" aria-label="Steps">
    <li v-for="(s, i) in (node.items as Array<{ label: string, status: string, detail?: string }> | undefined) ?? []" :key="i" :class="`ask-step ask-step-${s.status}`">
      <span class="ask-stepmark" aria-hidden="true">{{ stepMark[s.status] ?? '○' }}</span>
      <span class="ask-row-text">
        <span class="ask-row-title">{{ s.label }}</span>
        <span v-if="s.detail" class="ask-row-detail">{{ s.detail }}</span>
      </span>
      <span class="ask-sr">{{ s.status }}</span>
    </li>
  </ol>

  <div v-else-if="node.type === 'progress'" class="ask-field">
    <span v-if="node.label" class="ask-label ask-between"><span>{{ node.label }}</span><span v-if="fraction !== null" class="ask-mono">{{ Math.round(fraction * 100) }}%</span></span>
    <div class="ask-bar" role="progressbar" :aria-valuemin="0" :aria-valuemax="Number(node.max)" :aria-valuenow="fraction === null ? undefined : Number(node.value)" :aria-label="node.label || 'Progress'">
      <span v-if="fraction !== null" class="ask-bar-fill" :style="{ transform: `scaleX(${fraction})` }" />
      <span v-else class="ask-bar-run" />
    </div>
  </div>

  <p v-else-if="node.type === 'callout'" class="ask-callout" :class="`ask-callout-${node.level === 'warn' ? 'warn' : 'info'}`" role="note">
    {{ node.text }}
  </p>
</template>
