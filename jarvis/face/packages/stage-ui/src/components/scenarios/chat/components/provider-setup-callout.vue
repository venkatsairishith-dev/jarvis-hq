<script setup lang="ts">
import { BasicButton, Callout } from '@proj-airi/ui'
import { useI18n } from 'vue-i18n'

const emit = defineEmits<{
  /** The host opens the provider settings. Each host has its own way to open them. */
  configure: []
}>()

const { t } = useI18n()
</script>

<template>
  <Callout
    theme="orange"
    :label="t('stage.chat.provider-configuration.title')"
  >
    <div :class="['flex flex-wrap items-center justify-between gap-2']">
      <span :class="['min-w-48 flex-1 text-sm']">
        {{ t('stage.chat.provider-configuration.description') }}
      </span>
      <BasicButton
        size="unset"
        :class="['min-h-8 rounded-lg bg-primary-700 px-3 py-1 text-sm text-white hover:bg-primary-800 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary-500']"
        @click="emit('configure')"
      >
        {{ t('stage.chat.provider-configuration.action') }}
      </BasicButton>
    </div>
  </Callout>
</template>
