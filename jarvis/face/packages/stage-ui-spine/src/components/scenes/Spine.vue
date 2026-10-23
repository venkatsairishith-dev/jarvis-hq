<script setup lang="ts">
/**
 * JARVIS stand-in for AIRI's Spine avatar scene.
 *
 * The Spine Runtimes (Esoteric Software) may only be integrated by a holder of a Spine Editor licence, so JARVIS does
 * not ship them. This component keeps the props, models, events and exposed methods of the original, renders a short
 * notice instead of a model, and reports an error so the stage can tell the owner to pick a Live2D, VRM or MMD avatar.
 */
import type { Emotion } from '../../constants/emotions'

import { onMounted } from 'vue'

withDefaults(defineProps<{
  backgroundUrl?: string | null
  modelSrc?: string
  modelId?: string
  paused?: boolean
  premultipliedAlpha?: boolean
  defaultMixDuration?: number
  idleAnimationEnabled?: boolean
  maxFps?: number
  renderScale?: number
}>(), {
  paused: false,
})

const emit = defineEmits<{ (e: 'error', error: Error): void }>()

const componentState = defineModel<'pending' | 'loading' | 'mounted'>('state', { default: 'pending' })
defineModel<'pending' | 'loading' | 'mounted'>('canvasState', { default: 'pending' })
defineModel<'pending' | 'loading' | 'mounted'>('modelState', { default: 'pending' })

onMounted(() => {
  componentState.value = 'loading'
  emit('error', new Error('Spine avatars are not available in JARVIS (the Spine Runtimes need a Spine Editor licence). Choose a Live2D, VRM or MMD avatar.'))
})

defineExpose({
  canvasElement: (): HTMLCanvasElement | undefined => undefined,
  captureFrame: async (): Promise<Blob | undefined> => undefined,
  setEmotion: (_emotion: Emotion, _intensity?: number): void => {},
  listAnimations: (): string[] => [],
  listSkins: (): string[] => [],
})
</script>

<template>
  <div :class="['h-full w-full flex items-center justify-center p-4 text-center text-sm opacity-70']">
    Spine avatars are not available in JARVIS. Choose a Live2D, VRM or MMD avatar.
  </div>
</template>
