<script setup lang="ts">
/**
 * Connects the Preview panel to the JARVIS tool server: the panel's calls go through the face's MCP connection like every other
 * jarvis tool, and the panel opens by itself when the chat model calls `jarvis_show`. Mounted once, in the main stage window.
 */
import { useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { onBeforeUnmount, onMounted } from 'vue'

import PreviewPanel from './PreviewPanel.vue'

import { electronMcpCallTool } from '../../shared/eventa'
import { previewEnabled, usePreviewController } from '../composables/use-jarvis-preview'

const callMcpTool = useElectronEventaInvoke(electronMcpCallTool)
const controller = usePreviewController({
  call: (name, args) => callMcpTool({ name, arguments: args }),
  enabled: () => previewEnabled(globalThis.localStorage),
  onError: error => console.warn('[JARVIS] preview failed:', error),
})

onMounted(() => controller.start())
onBeforeUnmount(() => controller.stop())
</script>

<template>
  <PreviewPanel :controller="controller" />
</template>
