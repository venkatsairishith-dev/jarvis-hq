<script setup lang="ts">
/**
 * Connects the ask panel to the JARVIS tool server: the panel's calls go through the face's MCP connection like every other jarvis tool,
 * and the panel appears by itself when the chat model calls `jarvis_ask` or `jarvis_cleaner` (a long poll, so within milliseconds).
 * Mounted once, in the main stage window. Closing the app, or the window, stops the poll.
 *
 * Voice (D63): the panel gets a voice controller wired to the face's own speech recognizer and speech output. Nothing starts until the owner
 * presses a microphone. The voice store hands push-to-talk and wake-word speech to this controller while a form is open (see
 * @proj-airi/stage-ui voice-command-sink); ambient speech is never a command.
 */
import { useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { onSecretSpeech, setVoiceCommandSink } from '@proj-airi/stage-ui/composables/voice-command-sink'
import { useChatSessionStore } from '@proj-airi/stage-ui/stores/chat/session-store'
import { onBeforeUnmount, onMounted } from 'vue'

import AskPanel from './AskPanel.vue'

import { electronMcpCallTool } from '../../../shared/eventa'
import { createDomFocus } from '../../composables/ask-voice-dom'
import { createComposerSource, createSpeaker, createVoiceBridge, switchedOff } from '../../composables/ask-voice-wiring'
import { askEnabled, useAskController } from '../../composables/use-jarvis-ask'
import { useAskVoice } from '../../composables/use-jarvis-ask-voice'

const callMcpTool = useElectronEventaInvoke(electronMcpCallTool)
const controller = useAskController({
  call: (name, args) => callMcpTool({ name, arguments: args }),
  enabled: () => askEnabled(globalThis.localStorage),
  onError: error => console.warn('[JARVIS] ask panel failed:', error),
})

const voiceOn = !switchedOff('jarvis:ask-voice')
let removeSink: (() => void) | undefined
let removeSecret: (() => void) | undefined
const sessions = useChatSessionStore()
const speaker = createSpeaker(() => sessions.activeSessionId)
const composer = voiceOn ? createComposerSource() : null
const voice = voiceOn
  ? useAskVoice({
      ask: controller,
      source: () => composer,
      bridge: createVoiceBridge(
        (name, args, timeoutMs) => callMcpTool({ name, arguments: args, timeoutMs }),
        () => controller.form.value ? { id: controller.form.value.id, token: controller.form.value.token } : null,
      ),
      speak: text => speaker.speak(text),
      stopSpeaking: () => speaker.stop(),
      focus: createDomFocus(() => document.querySelector<HTMLElement>('.ask-root .ask')),
      settings: {
        punctuation: () => !switchedOff('jarvis:ask-voice-punct'),
        speakBack: () => !switchedOff('jarvis:ask-voice-speak'),
      },
    })
  : null

onMounted(() => {
  controller.start()
  if (voice) {
    removeSink = setVoiceCommandSink(speech => voice.command(speech))
    removeSecret = onSecretSpeech(() => voice.secretHeard())
  }
})
onBeforeUnmount(() => {
  controller.stop()
  removeSink?.()
  removeSecret?.()
  voice?.dispose()
})
</script>

<template>
  <AskPanel :controller="controller" :voice="voice" />
</template>
