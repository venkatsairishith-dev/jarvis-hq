<script setup lang="ts">
import type { HudMode } from './hud-model'

import { FieldSelect } from '@proj-airi/ui'
import { storeToRefs } from 'pinia'

import { useJarvisHudSettingsStore } from '../../stores/settings/jarvis-hud'

/** The HUD setting for Settings > System > General: off, subtle (default) or full. Kept in localStorage like the other window settings. */
const { mode } = storeToRefs(useJarvisHudSettingsStore())

const options: { label: string, value: HudMode, description: string }[] = [
  { label: 'Off', value: 'off', description: 'No ring, no strip, no tint.' },
  { label: 'Subtle', value: 'subtle', description: 'Ring and strip, a soft light behind the character.' },
  { label: 'Full', value: 'full', description: 'Adds faint scanlines, a slow sweep, corner marks and a vignette.' },
]
</script>

<template>
  <FieldSelect
    v-model="mode"
    label="JARVIS HUD"
    description="The state ring and the status strip over the stage: listening, thinking, speaking, acting, offline."
    :options="options"
  />
</template>
