<script setup lang="ts">
/**
 * JARVIS's ask panel (D55: generated UI = declarative forms only): the one trusted component that draws a form JARVIS built for the task
 * (a cleanup list, a question, a settings page, a plan with steps, code to hand over). State and tool-server calls live in
 * `useAskController` (../../composables/use-jarvis-ask.ts), the rules in ../../composables/jarvis-ask-logic.ts, the parts in AskNode.vue.
 *
 * It appears the moment the server has a form (a long poll), shows what is known at once and a progress strip while JARVIS works, never
 * blocks on a long call, and draws only text (no markup, no link, no script). Keyboard: Tab/Shift+Tab, Space toggles, arrow keys inside
 * a choice, Enter = the primary button, Escape = Cancel (or Close). Focus moves into the panel on open and back where it was on close.
 *
 * Voice (D63, only when the window has a voice host and `voice` is given): every text, number and date field has a small microphone, the header
 * has one for spoken commands and one that reads the form aloud. Each has a key: Alt+M microphone (dictates into the focused field, else listens
 * for commands; again to stop), Alt+R read aloud, Alt+Z undo the last voice change. Escape first stops the microphone, the reading or a question,
 * and only then cancels the form. The microphone is started by the owner, never by a form opening, and stops when the form closes.
 */
import type { AskController } from '../../composables/use-jarvis-ask'
import type { AskVoice } from '../../composables/use-jarvis-ask-voice'

import { computed, nextTick, onBeforeUnmount, provide, ref, watch } from 'vue'

import AskNode from './AskNode.vue'
import AskVoiceBar from './AskVoiceBar.vue'

import { formatSize, keyAction, plural, progressFraction, progressText, selectedTotal, walk } from '../../composables/jarvis-ask-logic'
import { ASK_VOICE_KEY } from '../../composables/use-jarvis-ask-voice'

import '../jarvis-ui-tokens.css'
import './ask.css'

const props = defineProps<{ controller: AskController, voice?: AskVoice | null }>()
const { form, values, busy, error } = props.controller
const voice = props.voice ?? null
provide(ASK_VOICE_KEY, voice)

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

/** Voice keys. Returns true when the key was taken. */
function onVoiceKey(event: KeyboardEvent): boolean {
  if (!voice)
    return false
  const k = event.key.toLowerCase()
  if (event.altKey && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
    if (k === 'm') {
      voice.toggleMic()
      return true
    }
    if (k === 'r') {
      if (voice.reading.value)
        voice.stopReading()
      else
        void voice.readAloud()
      return true
    }
    if (k === 'z') {
      void voice.undo()
      return true
    }
  }
  const target = event.target as HTMLElement
  const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
  if ((event.ctrlKey || event.metaKey) && !event.shiftKey && !event.altKey && k === 'z' && !typing && voice.undoStack.value.length) {
    void voice.undo()
    return true
  }
  // Escape first ends what voice is doing (listening, reading, a question), then it cancels the form as before
  if (event.key === 'Escape' && (voice.mode.value !== 'off' || voice.reading.value || voice.question.value)) {
    voice.stop()
    return true
  }
  return false
}

function onKey(event: KeyboardEvent) {
  const f = form.value
  if (!f)
    return
  if (onVoiceKey(event)) {
    event.preventDefault()
    event.stopPropagation()
    return
  }
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
        <button
          v-if="voice"
          type="button"
          class="ask-hbtn ask-mic"
          :class="`ask-mic-${voice.mode.value === 'command' ? voice.micState() : 'idle'}`"
          :aria-pressed="voice.mode.value === 'command'"
          aria-label="Speak a command (Alt+M)"
          title="Speak a command: tick, untick, set, next, undo, cancel, and at last clean or submit (Alt+M)"
          aria-keyshortcuts="Alt+M"
          :disabled="working"
          @click="voice.mode.value === 'command' ? voice.stop() : voice.startCommand()"
        >
          <svg v-if="!(voice.mode.value === 'command' && voice.understanding.value)" class="ask-mic-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
            <rect x="5.5" y="1.5" width="5" height="8" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.4" />
            <path d="M3 7.5a5 5 0 0 0 10 0M8 12.5v2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
          </svg>
          <span v-else class="ask-mic-dots" aria-hidden="true"><i /><i /><i /></span>
        </button>
        <button
          v-if="voice"
          type="button"
          class="ask-hbtn ask-read"
          :aria-pressed="voice.reading.value"
          :aria-label="voice.reading.value ? 'Stop reading (Alt+R)' : 'Read the form aloud (Alt+R)'"
          :title="voice.reading.value ? 'Stop reading (Alt+R)' : 'Read the form aloud (Alt+R)'"
          aria-keyshortcuts="Alt+R"
          @click="voice.reading.value ? voice.stopReading() : voice.readAloud()"
        >
          <svg class="ask-mic-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
            <path d="M2 6h3l4-3v10l-4-3H2z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round" />
            <path v-if="voice.reading.value" d="M11.5 5.5a3.5 3.5 0 0 1 0 5M13.5 3.5a6.5 6.5 0 0 1 0 9" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
            <path v-else d="M11.5 5.5a3.5 3.5 0 0 1 0 5" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
          </svg>
        </button>
        <button type="button" class="ask-x" aria-label="Close" title="Close (Esc)" @click="controller.close()">
          &#10005;
        </button>
      </header>

      <AskVoiceBar v-if="voice" :voice="voice" :field-label="id => (form ? [...walk(form.fields)].find(n => n.id === id)?.label : '') || ''" />

      <div class="ask-body">
        <div v-if="working" class="ask-working" role="status" aria-live="polite">
          <p class="ask-working-text">
            <span>{{ form.status || 'Working…' }}</span>
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
