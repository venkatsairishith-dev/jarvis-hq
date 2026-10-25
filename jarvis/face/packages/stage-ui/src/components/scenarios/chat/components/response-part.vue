<script setup lang="ts">
import type { ChatAssistantMessage } from '../../../../types/chat'

import { Truncatable } from '@proj-airi/ui'
import { computed } from 'vue'

import { MarkdownRenderer } from '../../../markdown'
import { reasoningToShow } from './reasoning-visibility'

const props = defineProps<{
  message: ChatAssistantMessage
  variant?: 'desktop' | 'mobile'
}>()

// JARVIS: the model's thinking is not shown (reasoning-visibility.ts)
const reasoningContent = computed(() => reasoningToShow(props.message))
const hasReasoning = computed(() => reasoningContent.value.length > 0)

const containerClasses = computed(() => [
  props.variant === 'mobile' ? 'text-xs' : 'text-sm',
])
</script>

<template>
  <div v-if="hasReasoning" :class="containerClasses" flex="~ col" gap-1>
    <Truncatable :line-clamp="1">
      <MarkdownRenderer
        :content="reasoningContent"
        :class="['break-words']"
        text="sm neutral-700/50 dark:neutral-300/50"
      />
    </Truncatable>
  </div>
</template>
