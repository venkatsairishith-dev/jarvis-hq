<script setup lang="ts">
/**
 * The small microphone of one field (D63): press it (or Alt+M while the field has the focus) and say what goes in the field, in Telugu,
 * Hindi or English. States: idle, listening (a ring), understanding (three dots). Pressing it again, Escape, or closing the form stops it.
 * The speech recognizer starts when this is pressed, never when a form opens. Without a voice controller (a window that has no voice host) it
 * is not drawn at all.
 */
import type { AskVoice } from '../../composables/use-jarvis-ask-voice'

import { computed, inject } from 'vue'

import { ASK_VOICE_KEY } from '../../composables/use-jarvis-ask-voice'

const props = defineProps<{ fieldId: string, label: string, disabled?: boolean }>()
const voice = inject<AskVoice | null>(ASK_VOICE_KEY, null)

const listening = computed(() => voice?.listeningOn(props.fieldId) ?? false)
const state = computed(() => !listening.value ? 'idle' : voice?.understanding.value ? 'understanding' : 'listening')
const text = computed(() => {
  const name = props.label || 'this field'
  return listening.value ? `Stop dictating ${name} (Alt+M)` : `Dictate ${name}: say it in Telugu, Hindi or English (Alt+M)`
})
</script>

<template>
  <button
    v-if="voice"
    type="button"
    class="ask-mic"
    :class="`ask-mic-${state}`"
    :aria-pressed="listening"
    :aria-label="text"
    :title="text"
    aria-keyshortcuts="Alt+M"
    :disabled="disabled"
    @click="voice.toggleField(fieldId)"
  >
    <svg v-if="state !== 'understanding'" class="ask-mic-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true" focusable="false">
      <rect x="5.5" y="1.5" width="5" height="8" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.4" />
      <path d="M3 7.5a5 5 0 0 0 10 0M8 12.5v2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" />
    </svg>
    <span v-else class="ask-mic-dots" aria-hidden="true"><i /><i /><i /></span>
  </button>
</template>
