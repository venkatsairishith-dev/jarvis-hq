<script setup lang="ts">
/**
 * What the voice part of a form says (D63): that it is listening, what it heard and what it understood (with Undo), a question
 * ("Did you mean A or B?") the owner answers by voice or by click, a notice (the microphone is not set up), and the text of "read the form"
 * with the reason when something was not spoken. All text is drawn as text. The bar is not drawn when there is nothing to say.
 */
import type { AskVoice } from '../../composables/use-jarvis-ask-voice'

import { computed } from 'vue'

const props = defineProps<{ voice: AskVoice, fieldLabel?: (id: string) => string }>()
const v = props.voice

const listeningText = computed(() => {
  if (v.mode.value === 'command')
    return 'Listening for a command. Say what to tick, or "clean" when you are done.'
  if (v.mode.value === 'dictate')
    return `Listening in ${props.fieldLabel?.(v.activeField.value ?? '') || 'the field'}. Say "next field", "delete that" or "stop".`
  return ''
})
const visible = computed(() => v.mode.value !== 'off' || !!v.did.value || !!v.notice.value || !!v.question.value || !!v.readShown.value || v.reading.value)
</script>

<template>
  <div v-if="visible" class="ask-voice" role="group" aria-label="Voice">
    <p v-if="v.mode.value !== 'off'" class="ask-voice-line ask-voice-listening" role="status" aria-live="polite">
      <span class="ask-voice-dot" :class="`ask-voice-dot-${v.micState()}`" aria-hidden="true" />
      <span class="ask-voice-text">{{ v.understanding.value ? 'Understanding...' : listeningText }}</span>
    </p>
    <p v-if="v.mode.value !== 'off' && v.interim.value && v.mode.value === 'command'" class="ask-voice-interim" aria-hidden="true">
      {{ v.interim.value }}
    </p>
    <p v-else-if="v.heard.value || v.did.value" class="ask-voice-line" role="status" aria-live="polite">
      <span v-if="v.heard.value" class="ask-voice-heard" :title="v.heard.value">&ldquo;{{ v.heard.value }}&rdquo;</span>
      <span v-if="v.understoodAs.value" class="ask-voice-dim" :title="v.understoodAs.value">understood as &ldquo;{{ v.understoodAs.value }}&rdquo;</span>
      <span v-if="v.did.value && !v.question.value" class="ask-voice-did">{{ v.did.value }}</span>
    </p>
    <p v-if="v.notice.value" class="ask-voice-notice" role="alert">
      {{ v.notice.value }}
    </p>

    <div v-if="v.question.value" class="ask-voice-q" role="group" :aria-label="v.question.value.text">
      <p class="ask-voice-qtext" role="status" aria-live="assertive">
        {{ v.question.value.text }}
      </p>
      <div class="ask-voice-choices">
        <button v-for="(o, i) in v.question.value.options" :key="o.key" type="button" class="ask-small ask-voice-choice" @click="v.answerQuestion(o.key)">
          <span class="ask-mono ask-dim">{{ i + 1 }}</span> {{ o.label }}
        </button>
        <button type="button" class="ask-small ask-voice-choice ask-voice-neither" @click="v.answerQuestion('dismiss')">
          Neither
        </button>
      </div>
    </div>

    <div v-if="v.readShown.value" class="ask-voice-read">
      <pre class="ask-voice-readtext" tabindex="0" aria-label="The form as read aloud">{{ v.readShown.value }}</pre>
      <p v-for="n in v.readNotes.value" :key="n" class="ask-voice-notice" role="status">
        {{ n }}
      </p>
      <div class="ask-voice-choices">
        <button v-if="v.reading.value" type="button" class="ask-small" @click="v.stopReading()">
          Stop reading
        </button>
        <button type="button" class="ask-small" @click="v.dismissReading()">
          Hide text
        </button>
      </div>
    </div>

    <div v-if="v.undoStack.value.length" class="ask-voice-actions">
      <button type="button" class="ask-small" aria-keyshortcuts="Alt+Z" title="Undo the last change made by voice (Alt+Z)" @click="v.undo()">
        Undo that change
      </button>
    </div>
  </div>
</template>
