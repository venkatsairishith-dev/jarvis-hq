<script setup lang="ts">
import type { LookSize } from '../../../shared/jarvis-look'
import type { OrbState } from './orb-state'

import { computed, onBeforeUnmount, onMounted, useTemplateRef, watch } from 'vue'

import { ORB_DISC } from '../../../shared/jarvis-look'
import { createOrbGesture } from './orb-gesture'
import { followsLevel, idleBreath, idleTimerWanted, levelDue, ORB_IDLE_TICK_MS, orbButtonLabel, ringScale } from './orb-state'

import './orb.css'

/**
 * The orb (D78): a calm disc with a glow, drawn by CSS alone. It is the whole interface of JARVIS in the orb look, so everything works from the
 * keyboard too:
 *   Enter / Space  talk or stop          T  open the text box          Menu key / Shift+F10  the menu
 *   Alt + arrows   move it               Escape  stop listening
 * and with the mouse: a click talks or stops, a double-click opens the text box, a right-click opens the menu, a drag moves it (never a click).
 *
 * Presentational: it gets the state and a level as props and says what the owner did through events. No store, no avatar, no WebGL, no
 * animation frame loop: a level reaches the screen through one CSS variable, at most 15 times a second, and only while listening or speaking.
 */
const props = withDefaults(defineProps<{
  state: OrbState
  size: LookSize
  /** The share of a task done, 0 to 1; `null` turns a slow arc. */
  progress?: number | null
  /** The microphone (listening) or the voice (speaking), 0 to 1. */
  level?: number
  line?: string
  link?: 'unknown' | 'up' | 'down' | 'killed'
  /** The single quiet pulse after a long hide. */
  pulse?: boolean
}>(), { progress: null, level: 0, line: '', link: 'up', pulse: false })

const emit = defineEmits<{
  talk: []
  text: []
  menu: []
  nudge: [direction: 'left' | 'right' | 'up' | 'down']
  press: []
  dragStart: []
  dragMove: []
  dragEnd: []
}>()

const zone = useTemplateRef<HTMLElement>('zone')
const disc = useTemplateRef<HTMLButtonElement>('disc')

const label = computed(() => orbButtonLabel(props.state, props.line, props.link))
const arcRadius = computed(() => ORB_DISC[props.size] / 2 + 11)
const arcLength = computed(() => 2 * Math.PI * arcRadius.value)
const arcOffset = computed(() => props.progress === null ? arcLength.value * 0.72 : arcLength.value * (1 - Math.min(1, Math.max(0.03, props.progress))))
const arcBox = computed(() => (arcRadius.value + 4) * 2)

// ---- the level: one CSS variable, at most 15 times a second, only while it matters
let lastDrawn = 0
let scale = 1
watch(() => props.level, (value) => {
  if (!followsLevel(props.state))
    return
  const now = performance.now()
  if (!levelDue(now, lastDrawn))
    return
  lastDrawn = now
  scale = ringScale(value, scale)
  zone.value?.style.setProperty('--orb-scale', String(scale))
})
watch(() => props.state, () => {
  scale = 1
  zone.value?.style.setProperty('--orb-scale', '1')
})

// ---- the idle breathing: a 2 Hz timer while the orb is idle and can be seen, nothing otherwise
let breathTimer: ReturnType<typeof setInterval> | undefined
let breathTick = 0
const reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)')
function breathe() {
  breathTick += 1
  zone.value?.style.setProperty('--orb-breath', String(idleBreath(breathTick)))
}
function syncBreath() {
  const wanted = idleTimerWanted(props.state, reducedMotion?.matches === true, globalThis.document?.hidden === true)
  if (wanted && !breathTimer) {
    breathTimer = setInterval(breathe, ORB_IDLE_TICK_MS)
  }
  else if (!wanted && breathTimer) {
    clearInterval(breathTimer)
    breathTimer = undefined
    zone.value?.style.removeProperty('--orb-breath')
  }
}
watch(() => props.state, syncBreath)
onMounted(() => {
  syncBreath()
  document.addEventListener('visibilitychange', syncBreath)
  reducedMotion?.addEventListener?.('change', syncBreath)
})

// ---- gestures
const gesture = createOrbGesture({
  onPress: () => emit('press'),
  onClick: () => emit('talk'),
  onDoubleClick: () => emit('text'),
  onDragStart: () => emit('dragStart'),
  onDragMove: () => emit('dragMove'),
  onDragEnd: () => emit('dragEnd'),
})

function onPointerDown(event: PointerEvent) {
  gesture.down(event.button, event.screenX, event.screenY)
  if (event.button === 0)
    disc.value?.setPointerCapture?.(event.pointerId)
}
function onPointerMove(event: PointerEvent) {
  gesture.move(event.screenX, event.screenY)
}
function onPointerUp(event: PointerEvent) {
  disc.value?.releasePointerCapture?.(event.pointerId)
  gesture.up()
}
function onKeydown(event: KeyboardEvent) {
  const arrows: Record<string, 'left' | 'right' | 'up' | 'down'> = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }
  if (event.altKey && arrows[event.key]) {
    event.preventDefault()
    emit('nudge', arrows[event.key]!)
    return
  }
  if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
    event.preventDefault()
    emit('menu')
    return
  }
  if ((event.key === 't' || event.key === 'T') && !event.ctrlKey && !event.metaKey && !event.altKey) {
    event.preventDefault()
    emit('text')
    return
  }
  if (event.key === 'Escape' && props.state === 'listening') {
    event.preventDefault()
    emit('talk')
  }
}
// Enter and Space are a native button's click, which would talk twice with the pointer path, so the keyboard path is its own
function onKeyActivate(event: KeyboardEvent) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    emit('talk')
  }
}

onBeforeUnmount(() => {
  gesture.cancel()
  if (breathTimer)
    clearInterval(breathTimer)
  document.removeEventListener('visibilitychange', syncBreath)
  reducedMotion?.removeEventListener?.('change', syncBreath)
})
defineExpose({ focus: () => disc.value?.focus() })
</script>

<template>
  <div ref="zone" class="orb-zone" :class="`orb-s-${size}`" :data-state="state" data-testid="orb">
    <span class="orb-glow" aria-hidden="true" />
    <span v-if="pulse" class="orb-forgotten" aria-hidden="true" />
    <span class="orb-attention" aria-hidden="true" />
    <svg
      class="orb-arc"
      :viewBox="`0 0 ${arcBox} ${arcBox}`"
      :width="arcBox"
      :height="arcBox"
      :data-indeterminate="progress === null"
      aria-hidden="true"
    >
      <circle class="orb-arc-track" :cx="arcBox / 2" :cy="arcBox / 2" :r="arcRadius" />
      <circle
        class="orb-arc-bar"
        :cx="arcBox / 2"
        :cy="arcBox / 2"
        :r="arcRadius"
        :stroke-dasharray="arcLength"
        :stroke-dashoffset="arcOffset"
      />
    </svg>
    <span class="orb-ring" aria-hidden="true" />
    <span class="orb-level" aria-hidden="true" />
    <button
      ref="disc"
      class="orb-disc orb-hit"
      type="button"
      data-orb-hit
      :aria-label="label"
      :aria-pressed="state === 'listening'"
      :title="label"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointercancel="gesture.cancel()"
      @contextmenu.prevent="emit('menu')"
      @keydown="onKeydown"
      @keyup="onKeyActivate"
      @click.prevent
    >
      <span class="orb-swirl" aria-hidden="true" />
      <span class="orb-shine" aria-hidden="true" />
    </button>
  </div>
</template>
