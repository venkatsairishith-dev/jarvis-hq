<script setup lang="ts">
/**
 * JARVIS's ask panel (D55: generated UI = declarative forms only): the one trusted component that draws a form JARVIS built for the task
 * (a cleanup list, a question, a settings page, a plan with steps, code to hand over). State and tool-server calls live in
 * `useAskController` (../../composables/use-jarvis-ask.ts), the rules in ../../composables/jarvis-ask-logic.ts, the parts in AskNode.vue.
 *
 * It appears the moment the server has a form (a long poll), shows what is known at once and a progress strip while JARVIS works, never
 * blocks on a long call, and draws only text (no markup, no link, no script). Keyboard: Tab/Shift+Tab, Space toggles, arrow keys inside
 * a choice, Enter = the primary button, Escape = Cancel (or Close). Focus moves into the panel on open and back where it was on close.
 */
import type { AskController } from '../../composables/use-jarvis-ask'

import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import AskNode from './AskNode.vue'

import { formatSize, keyAction, plural, progressFraction, progressText, selectedTotal } from '../../composables/jarvis-ask-logic'

import './ask.css'

const props = defineProps<{ controller: AskController }>()
const { form, values, busy, error } = props.controller

const panel = ref<HTMLElement | null>(null)
const ended = ref('')
let endedTimer: ReturnType<typeof setTimeout> | undefined
let returnTo: HTMLElement | null = null

const total = computed(() => form.value ? selectedTotal(form.value.fields, values.value) : null)
const fraction = computed(() => progressFraction(form.value?.progress ?? null))
const working = computed(() => busy.value)
const skeleton = computed(() => working.value && (form.value?.fields.length ?? 0) === 0)
const bar = computed(() => {
  const f = fraction.value
  return f === null ? null : f
})

function disabledButton(id: string, style: string): boolean {
  if (working.value)
    return !['close', 'cancel'].includes(id)
  return style === 'primary' && total.value !== null && total.value.count === 0
}

function onSet(id: string, value: Parameters<AskController['setValue']>[1]) {
  props.controller.setValue(id, value)
}

function onKey(event: KeyboardEvent) {
  const f = form.value
  if (!f)
    return
  const target = event.target as HTMLElement
  const action = keyAction(event.key, f.buttons, { tag: target.tagName, type: (target as HTMLInputElement).type, role: target.getAttribute('role') ?? undefined })
  if (!action)
    return
  event.preventDefault()
  event.stopPropagation()
  if (action.kind === 'button')
    void props.controller.press(action.id)
  else
    void props.controller.close()
}

// Focus: into the panel when a form opens, back where it was when it closes.
watch(() => form.value?.id, async (id, old) => {
  if (id && id !== old) {
    returnTo = document.activeElement instanceof HTMLElement ? document.activeElement : null
    ended.value = ''
    await nextTick()
    const first = panel.value?.querySelector<HTMLElement>('.ask-body input, .ask-body select, .ask-body textarea, .ask-body [role=radio][tabindex="0"], .ask-body [role=tab][tabindex="0"]')
    ;(first ?? panel.value)?.focus({ preventScroll: true })
  }
  else if (!id && old) {
    if (returnTo?.isConnected)
      returnTo.focus({ preventScroll: true })
    returnTo = null
  }
}, { flush: 'post' })

// A form that went away without the owner pressing anything (timed out, or replaced): say so for a few seconds.
watch(() => props.controller.endedAt.value, (at) => {
  if (endedTimer)
    clearTimeout(endedTimer)
  ended.value = at ? 'That question is no longer open. It timed out or was replaced; ask JARVIS again.' : ''
  if (at)
    endedTimer = setTimeout(() => (ended.value = ''), 6000)
})
onBeforeUnmount(() => {
  if (endedTimer)
    clearTimeout(endedTimer)
})
</script>

<template>
  <div class="ask-root">
    <section
      v-if="form"
      ref="panel"
      class="ask"
      role="dialog"
      :aria-label="form.title"
      :aria-busy="working"
      tabindex="-1"
      @keydown="onKey"
    >
      <header class="ask-head">
        <div class="ask-titles">
          <h2 class="ask-title" :title="form.title">
            {{ form.title }}
          </h2>
          <p v-if="form.intro" class="ask-intro">
            {{ form.intro }}
          </p>
        </div>
        <button type="button" class="ask-x" aria-label="Close" title="Close (Esc)" @click="controller.close()">
          &#10005;
        </button>
      </header>

      <div class="ask-body">
        <div v-if="working" class="ask-working" role="status" aria-live="polite">
          <p class="ask-working-text">
            <span>{{ form.status || 'Working...' }}</span>
            <span class="ask-mono">{{ progressText(form.progress) }}</span>
          </p>
          <div class="ask-bar" role="progressbar" aria-label="Progress" :aria-valuemin="0" :aria-valuemax="100" :aria-valuenow="bar === null ? undefined : Math.round(bar * 100)">
            <span v-if="bar !== null" class="ask-bar-fill" :style="{ transform: `scaleX(${bar})` }" />
            <span v-else class="ask-bar-run" />
          </div>
          <template v-if="skeleton">
            <div v-for="n in 4" :key="n" class="ask-skel" aria-hidden="true" />
          </template>
        </div>
        <p v-else-if="form.status" class="ask-notes" role="status">
          {{ form.status }}
        </p>
        <p v-if="error" class="ask-error" role="alert">
          {{ error }}
        </p>
        <AskNode v-for="(n, i) in form.fields" :key="n.id ?? `${n.type}${i}`" :node="n" :values="values" :disabled="working" @set="onSet" />
        <p v-if="form.notes.length" class="ask-notes">
          {{ form.notes.join(' ') }}
        </p>
      </div>

      <footer class="ask-foot">
        <p v-if="total" class="ask-total" aria-live="polite">
          <strong>{{ formatSize(total.bytes) }}</strong>
          {{ total.count ? `selected in ${plural(total.count, 'item')}` : 'Nothing selected yet' }}
        </p>
        <div class="ask-buttons">
          <button
            v-for="b in form.buttons"
            :key="b.id"
            type="button"
            class="ask-btn"
            :class="`ask-btn-${b.style}`"
            :disabled="disabledButton(b.id, b.style)"
            :title="b.style === 'primary' && total && !total.count ? 'Tick something first' : undefined"
            @click="controller.press(b.id)"
          >
            {{ b.label }}
          </button>
        </div>
      </footer>
    </section>
    <p v-else-if="ended" class="ask-pill" role="status">
      {{ ended }}
    </p>
  </div>
</template>

<style>
.ask-pill {
  position: fixed;
  right: 12px;
  bottom: 12px;
  z-index: 62;
  max-width: calc(100vw - 24px);
  margin: 0;
  padding: 8px 12px;
  border: 1px solid var(--ask-line);
  border-radius: 999px;
  background: var(--ask-bg);
  color: var(--ask-fg);
  font: 12.5px var(--ask-font);
  box-shadow: var(--ask-shadow);
}
</style>
