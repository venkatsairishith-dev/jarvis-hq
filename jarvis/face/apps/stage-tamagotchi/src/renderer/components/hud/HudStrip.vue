<script setup lang="ts">
import type { HudLink, HudState, JarvisStatus } from './hud-model'

import { computed, onBeforeUnmount, ref, watch } from 'vue'

import { formatClock, kernelLabel, noticeCount, noticeText, taskLabel } from './hud-model'

/**
 * The telemetry strip: one slim line. State, clock, kernel link, the PC task (or idle), the model, unread notices.
 *
 * Every segment has a fixed width in characters, so a changing value (the clock, a task's age) never moves its neighbours.
 */
const props = defineProps<{
  state: HudState
  stateLabel: string
  link: HudLink
  status: JarvisStatus | null
  /** Epoch ms of the last good status; keeps a task's age moving between polls. */
  statusAt: number
  modelLabel: string
  /** D60: one short line for the task under way; it takes the place of the model name while it shows. */
  line?: string
}>()

const now = ref(Date.now())
const busy = computed(() => props.status?.pcBusy === true)
let timer: ReturnType<typeof setInterval> | undefined

// One tick per second while a task's age shows, one every 15 s otherwise (the clock shows minutes only).
watch(busy, (b) => {
  if (timer)
    clearInterval(timer)
  now.value = Date.now()
  timer = setInterval(() => now.value = Date.now(), b ? 1000 : 15_000)
}, { immediate: true })
onBeforeUnmount(() => timer && clearInterval(timer))

const clock = computed(() => formatClock(new Date(now.value)))
const task = computed(() => {
  const elapsed = busy.value && props.statusAt ? Math.max(0, Math.min(10, Math.floor((now.value - props.statusAt) / 1000))) : 0
  return taskLabel(props.status, elapsed)
})
const notices = computed(() => noticeCount(props.status))
const waitingApprovals = computed(() => (props.status?.approvalsWaiting ?? 0) > 0)
</script>

<template>
  <div class="jhud-strip" data-ambient-light-opaque="true">
    <span class="jhud-seg jhud-seg-state">{{ stateLabel }}</span>
    <span class="jhud-seg jhud-seg-clock">{{ clock }}</span>
    <span class="jhud-seg jhud-seg-kernel" :data-link="link">
      <i class="jhud-dot" />{{ kernelLabel(link) }}
    </span>
    <span class="jhud-seg jhud-seg-task" :data-busy="busy">{{ task }}</span>
    <span class="jhud-seg jhud-seg-model" :title="line || modelLabel" :data-line="!!line" aria-live="polite">{{ line || modelLabel || '—' }}</span>
    <span class="jhud-seg jhud-seg-notices" :data-count="notices" :data-approval="waitingApprovals">
      <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true" focusable="false"><path d="M1.5 3h9v6.5h-9zM1.5 3l4.5 3.6L10.5 3" fill="none" stroke="currentColor" stroke-width="1" stroke-linejoin="round" /></svg>
      {{ noticeText(notices) }}
    </span>
  </div>
</template>
