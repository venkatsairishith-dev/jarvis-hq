<script setup lang="ts">
import type { HudLink, HudLook, HudState, JarvisStatus } from './hud-model'

import { useTemplateRef, watch } from 'vue'

import HudReactor from './HudReactor.vue'
import HudStrip from './HudStrip.vue'

import './hud-tokens.css'

/**
 * The HUD, drawn from plain props (the connected wrapper is JarvisHud.vue).
 *
 * Two parts, both ignoring the pointer, so the window stays click-through where it was:
 * - the atmosphere: a soft light behind the character, faint scanlines and, in full, a slow sweep, corner marks and a vignette.
 *   All of it fades out well inside the window edges, so no rectangle shows over the desktop;
 * - the chrome: the reactor and the strip, at the top centre. Only these are marked as painted by the app, so the screen
 *   sampler for the ambient light does not read them as desktop light.
 */
const props = defineProps<{
  look: HudLook
  state: HudState
  stateLabel: string
  label: string
  link: HudLink
  status: JarvisStatus | null
  statusAt: number
  modelLabel: string
  /** The character's mouth, 0 to 1. */
  level?: number
}>()

const root = useTemplateRef<HTMLElement>('root')

// The pulse of the speaking ring follows the voice through one CSS variable, set directly: no re-render for each audio frame.
watch(() => props.level, (v) => {
  root.value?.style.setProperty('--jhud-level', String(Math.round((v ?? 0) * 100) / 100))
})
</script>

<template>
  <div
    ref="root"
    class="jarvis-hud"
    :data-state="state"
    :data-frame="look.frame"
    :style="{ '--jhud-glow': look.glow, '--jhud-scan': look.scan }"
  >
    <div class="jhud-atmosphere" aria-hidden="true">
      <div class="jhud-masked">
        <div class="jhud-glow" />
        <div class="jhud-scan" />
        <div v-if="look.frame" class="jhud-sweep-line" />
      </div>
      <div v-if="look.frame" class="jhud-vignette" />
      <div v-if="look.frame" class="jhud-corners">
        <span /><span /><span /><span />
      </div>
    </div>
    <div class="jhud-chrome" role="group" :aria-label="label">
      <div class="jhud-reactor-wrap" data-ambient-light-opaque="true">
        <HudReactor :state="state" />
      </div>
      <HudStrip
        :state="state"
        :state-label="stateLabel"
        :link="link"
        :status="status"
        :status-at="statusAt"
        :model-label="modelLabel"
      />
    </div>
  </div>
</template>
