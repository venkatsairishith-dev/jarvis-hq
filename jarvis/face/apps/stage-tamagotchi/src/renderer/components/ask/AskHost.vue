<script setup lang="ts">
/**
 * Connects the ask panel to the JARVIS tool server: the panel's calls go through the face's MCP connection like every other jarvis tool,
 * and the panel appears by itself when the chat model calls `jarvis_ask` or `jarvis_cleaner` (a long poll, so within milliseconds).
 * Mounted once, in the main stage window. Closing the app, or the window, stops the poll.
 */
import { useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { onBeforeUnmount, onMounted } from 'vue'

import AskPanel from './AskPanel.vue'

import { electronMcpCallTool } from '../../../shared/eventa'
import { askEnabled, useAskController } from '../../composables/use-jarvis-ask'

const callMcpTool = useElectronEventaInvoke(electronMcpCallTool)
const controller = useAskController({
  call: (name, args) => callMcpTool({ name, arguments: args }),
  enabled: () => askEnabled(globalThis.localStorage),
  onError: error => console.warn('[JARVIS] ask panel failed:', error),
})

onMounted(() => controller.start())
onBeforeUnmount(() => controller.stop())
</script>

<template>
  <AskPanel :controller="controller" />
</template>
