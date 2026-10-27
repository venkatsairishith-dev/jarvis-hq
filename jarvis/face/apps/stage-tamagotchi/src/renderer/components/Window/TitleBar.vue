<script setup lang="ts">
import { useI18n } from 'vue-i18n'

import { useAppRuntime } from '../../composables/runtime'

defineProps<{
  title: string
  icon: string
  /** The title opens something (the chat window's past chats): it is then a real button, so the keyboard reaches it. */
  clickable?: boolean
}>()

const emit = defineEmits<{
  titleClick: []
}>()

const { platform } = useAppRuntime()
const { t } = useI18n()
</script>

<template>
  <div
    bg="neutral-100 dark:neutral-900" w="100dvw"
    top="0"
    fixed z-100 w-full select-none py-2 pr-4 drag-region
    :class="[
      platform === 'macos' ? 'pl-20' : 'pl-4',
    ]"
  >
    <div flex drag-region>
      <component
        :is="clickable ? 'button' : 'div'"
        :type="clickable ? 'button' : undefined"
        :title="clickable ? t('tamagotchi.stage.chat-window.sessions') : undefined"
        :aria-label="clickable ? `${title}: ${t('tamagotchi.stage.chat-window.sessions')}` : undefined"
        bg="hover:neutral-200 hover:dark:neutral-800"
        transition="all duration-200 ease-in-out motion-reduce:transition-none"
        flex select-none items-center gap-2 rounded-md px-1.5 py-0.5
        class="[-webkit-app-region:no-drag] min-h-8 focus-visible:outline-2 focus-visible:outline-primary-500 focus-visible:outline-offset-1 focus-visible:outline"
        :class="clickable ? 'cursor-pointer' : ''"
        @click="clickable && emit('titleClick')"
      >
        <div :class="icon" select-none text="neutral-500 dark:neutral-400" whitespace-nowrap aria-hidden="true" />
        <div><span select-none whitespace-nowrap text-sm>{{ title }}</span></div>
      </component>
      <div w-full drag-region />
      <div
        flex items-center gap-1
        class="[-webkit-app-region:no-drag]"
      >
        <slot name="actions" />
      </div>
      <!--
        NOTICE:
        The planned collapse feature was not implemented. Keep this icon hidden until the feature exists.
        https://github.com/moeru-ai/airi/issues/1835#issuecomment-4487178613
      -->
      <!--
      <div
        bg="hover:neutral-200 hover:dark:neutral-800"
        transition="all duration-200 ease-in-out"
        flex cursor-pointer items-center gap-2 rounded-md px-1.5 py-0.5
      >
        <div i-solar:info-circle-bold text="neutral-400 dark:neutral-500" whitespace-nowrap />
      </div>
      -->
    </div>
  </div>
</template>
