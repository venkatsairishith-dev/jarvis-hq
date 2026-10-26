<script setup lang="ts">
/**
 * Connects the Timeline panel to the JARVIS tool server: the panel's calls go through the face's MCP connection like every other jarvis tool, and
 * the panel appears by itself when the chat model calls `jarvis_timeline` with action open (a long poll, so within milliseconds). Ctrl+Alt+T opens
 * and closes it from the keyboard. Mounted once, in the main stage window. Closing the app, or the window, stops the poll.
 */
import { useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { onBeforeUnmount, onMounted } from 'vue'

import TimelinePanel from './TimelinePanel.vue'

import { electronMcpCallTool } from '../../../shared/eventa'
import { pageIsHidden } from '../../composables/poll-guard'
import { timelineEnabled, useTimelineController } from '../../composables/use-jarvis-timeline'

const callMcpTool = useElectronEventaInvoke(electronMcpCallTool)
const controller = useTimelineController({
  call: (name, args) => callMcpTool({ name, arguments: args }),
  enabled: () => timelineEnabled(globalThis.localStorage),
  paused: pageIsHidden,
  onError: error => console.warn('[JARVIS] timeline panel failed:', error),
})

function onKey(event: KeyboardEvent) {
  if (event.ctrlKey && event.altKey && !event.shiftKey && !event.metaKey && event.key.toLowerCase() === 't') {
    event.preventDefault()
    controller.toggle()
  }
}

onMounted(() => {
  controller.start()
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  controller.stop()
  window.removeEventListener('keydown', onKey)
})
</script>

<template>
  <TimelinePanel :controller="controller" />
</template>
