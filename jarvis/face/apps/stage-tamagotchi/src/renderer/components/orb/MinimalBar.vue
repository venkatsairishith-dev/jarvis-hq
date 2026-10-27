<script setup lang="ts">
import type { LookSize } from '../../../shared/jarvis-look'
import type { OrbState } from './orb-state'

import { computed, onBeforeUnmount, useTemplateRef, watch } from 'vue'

import { createOrbGesture } from './orb-gesture'
import { followsLevel, levelDue, orbButtonLabel, orbStatusText, ringScale } from './orb-state'

import './orb.css'

/**
 * The minimal bar (D78): one thin line with the status text and a microphone button. The same gestures as the orb, on the bar: a drag moves it,
 * a double-click opens the text box, a right-click opens the menu; the microphone button talks or stops. Presentational, no avatar, no frame loop.
 */
const props = withDefaults(defineProps<{
  state: OrbState
  size: LookSize
  line?: string
  level?: number
  link?: 'unknown' | 'up' | 'down' | 'killed'
}>(), { line: '', level: 0, link: 'up' })

const emit = defineEmits<{
  talk: []
  text: []
  menu: []
  nudge: [direction: 'left' | 'right' | 'up' | 'down']
  dragStart: []
  dragMove: []
  dragEnd: []
}>()

const bar = useTemplateRef<HTMLElement>('bar')
const text = computed(() => orbStatusText(props.state, props.line, props.link))
const micLabel = computed(() => orbButtonLabel(props.state, props.line, props.link))

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
  bar.value?.style.setProperty('--orb-scale', String(scale))
})
watch(() => props.state, () => {
  scale = 1
  bar.value?.style.setProperty('--orb-scale', '1')
})

const gesture = createOrbGesture({
  onClick: () => {},
  onDoubleClick: () => emit('text'),
  onDragStart: () => emit('dragStart'),
  onDragMove: () => emit('dragMove'),
  onDragEnd: () => emit('dragEnd'),
})

// a press on the microphone button is that button's own; every other press on the bar is a gesture of the bar
function onPointerDown(event: PointerEvent) {
  if ((event.target as HTMLElement | null)?.closest('.bar-mic'))
    return
  gesture.down(event.button, event.screenX, event.screenY)
  if (event.button === 0)
    bar.value?.setPointerCapture?.(event.pointerId)
}
function onPointerMove(event: PointerEvent) {
  gesture.move(event.screenX, event.screenY)
}
function onPointerUp(event: PointerEvent) {
  bar.value?.releasePointerCapture?.(event.pointerId)
  gesture.up()
}
function onKeydown(event: KeyboardEvent) {
  const arrows: Record<string, 'left' | 'right' | 'up' | 'down'> = { ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down' }
  if (event.altKey && arrows[event.key]) {
    event.preventDefault()
    emit('nudge', arrows[event.key]!)
  }
  else if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
    event.preventDefault()
    emit('menu')
  }
  else if ((event.key === 't' || event.key === 'T') && !event.ctrlKey && !event.metaKey && !event.altKey && event.target === bar.value) {
    event.preventDefault()
    emit('text')
  }
}
onBeforeUnmount(() => gesture.cancel())
defineExpose({ focus: () => bar.value?.focus() })
</script>

<template>
  <div
    ref="bar"
    class="bar orb-hit"
    :class="`bar-size-${size}`"
    :data-state="state"
    data-orb-hit
    data-testid="bar"
    role="group"
    aria-label="JARVIS"
    tabindex="0"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="gesture.cancel()"
    @contextmenu.prevent="emit('menu')"
    @keydown="onKeydown"
  >
    <span class="bar-dot" aria-hidden="true" />
    <span class="bar-text" role="status" :title="text">{{ text }}</span>
    <button class="bar-mic" type="button" :aria-label="micLabel" :aria-pressed="state === 'listening'" :title="micLabel" @click="emit('talk')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <rect x="9" y="3" width="6" height="12" rx="3" />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3" />
      </svg>
    </button>
  </div>
</template>
