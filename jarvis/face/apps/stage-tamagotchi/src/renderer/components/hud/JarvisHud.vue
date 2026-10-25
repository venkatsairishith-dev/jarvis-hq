<script setup lang="ts">
import { storeToRefs } from 'pinia'

import HudLayer from './HudLayer.vue'

import { useJarvisHudStore } from '../../composables/use-jarvis-hud'
import { useJarvisHudSettingsStore } from '../../stores/settings/jarvis-hud'
import { hudLook } from './hud-model'

/** The HUD in the main stage window: the live state from use-jarvis-hud.ts, drawn by HudLayer. App.vue mounts it when the setting is not "off". */
const hud = useJarvisHudStore()
const settings = useJarvisHudSettingsStore()
const { state, stateLabel, ariaLabel, link, status, statusAt, modelLabel, level } = storeToRefs(hud)
</script>

<template>
  <HudLayer
    :look="hudLook(settings.mode)"
    :state="state"
    :state-label="stateLabel"
    :label="ariaLabel"
    :link="link"
    :status="status"
    :status-at="statusAt"
    :model-label="modelLabel"
    :level="level"
  />
</template>
