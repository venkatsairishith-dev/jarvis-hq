<script setup lang="ts">
/**
 * Connects the Preview panel to the JARVIS tool server: the panel's calls go through the face's MCP connection like every other
 * jarvis tool, and the panel opens by itself when the chat model calls `jarvis_show`. Mounted once, in the main stage window.
 */
import { useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { onBeforeUnmount, onMounted } from 'vue'

import PreviewPanel from './PreviewPanel.vue'

import { electronMcpCallTool } from '../../shared/eventa'
import { pageIsHidden, POLL_CALL_TIMEOUT_MS } from '../composables/poll-guard'
import { JARVIS_WANTED_TOOL } from '../composables/jarvis-preview-logic'
import { previewEnabled, usePreviewController } from '../composables/use-jarvis-preview'

const callMcpTool = useElectronEventaInvoke(electronMcpCallTool)
// The "anything to show?" poll gets a few seconds; opening an item (a page of a deck is rendered by the tool server) gets a minute.
const OPEN_CALL_TIMEOUT_MS = 60_000
const controller = usePreviewController({
  call: (name, args) => callMcpTool({ name, arguments: args, timeoutMs: name === JARVIS_WANTED_TOOL ? POLL_CALL_TIMEOUT_MS : OPEN_CALL_TIMEOUT_MS }),
  enabled: () => previewEnabled(globalThis.localStorage),
  paused: pageIsHidden,
  onError: error => console.warn('[JARVIS] preview failed:', error),
})

onMounted(() => controller.start())
onBeforeUnmount(() => controller.stop())
</script>

<template>
  <PreviewPanel :controller="controller" />
</template>
