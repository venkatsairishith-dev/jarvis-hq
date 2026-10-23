<script setup lang="ts">
import type { MemoryEntry } from '@proj-airi/stage-ui/stores/memory'

import { useMemoryStore } from '@proj-airi/stage-ui/stores/memory'
import { Button } from '@proj-airi/ui'
import { storeToRefs } from 'pinia'
import { shallowRef } from 'vue'
import { useI18n } from 'vue-i18n'

/**
 * JARVIS: the page where the owner decides what JARVIS remembers.
 * JARVIS suggests facts; nothing it suggests is seen by the model until it is accepted here.
 */
const { t } = useI18n()
const memory = useMemoryStore()
const { enabled, proposals, accepted } = storeToRefs(memory)

// The owner's own memories belong to the default character card; the page lists every card's.
const OWNER_CARD = 'default'
const draft = shallowRef({ name: '', description: '', body: '' })

function save() {
  if (memory.write(draft.value, OWNER_CARD))
    draft.value = { name: '', description: '', body: '' }
}

function who(entry: MemoryEntry) {
  return entry.persona ? t('settings.pages.memory.entries.own') : t('settings.pages.memory.entries.owner_general')
}
</script>

<template>
  <div :class="['flex flex-col gap-6 pb-4']">
    <label :class="['flex items-start gap-3']">
      <input v-model="enabled" type="checkbox" data-testid="memory-enabled" :class="['mt-1']">
      <span>
        <span :class="['block font-medium']">{{ t('settings.pages.memory.enabled.label') }}</span>
        <span :class="['block text-sm opacity-70']">{{ t('settings.pages.memory.enabled.description') }}</span>
      </span>
    </label>

    <section data-testid="memory-proposals" :class="['flex flex-col gap-2']">
      <h2 :class="['text-lg font-medium']">
        {{ t('settings.pages.memory.proposals.title') }}
      </h2>
      <p :class="['text-sm opacity-70']">
        {{ t('settings.pages.memory.proposals.description') }}
      </p>
      <p v-if="!proposals.length" :class="['text-sm opacity-60']">
        {{ t('settings.pages.memory.proposals.empty') }}
      </p>
      <ul v-else :class="['flex flex-col gap-2']">
        <li v-for="entry in proposals" :key="`${entry.persona}:${entry.name}`" :class="['rounded-lg border border-neutral-300/50 p-3 dark:border-neutral-700/50']">
          <div :class="['font-medium']">
            {{ entry.name }}
          </div>
          <div :class="['text-sm opacity-80']">
            {{ entry.description }}
          </div>
          <div :class="['mt-1 whitespace-pre-wrap text-sm']">
            {{ entry.body }}
          </div>
          <div :class="['mt-2 flex gap-2']">
            <Button data-testid="memory-accept" @click="memory.accept(entry)">
              {{ t('settings.pages.memory.proposals.accept') }}
            </Button>
            <Button variant="secondary" data-testid="memory-reject" @click="memory.remove(entry)">
              {{ t('settings.pages.memory.proposals.reject') }}
            </Button>
          </div>
        </li>
      </ul>
    </section>

    <section data-testid="memory-entries" :class="['flex flex-col gap-2']">
      <h2 :class="['text-lg font-medium']">
        {{ t('settings.pages.memory.entries.title') }}
      </h2>
      <p v-if="!accepted.length" :class="['text-sm opacity-60']">
        {{ t('settings.pages.memory.entries.empty') }}
      </p>
      <ul v-else :class="['flex flex-col gap-2']">
        <li v-for="entry in accepted" :key="`${entry.persona}:${entry.name}`" :class="['rounded-lg border border-neutral-300/50 p-3 dark:border-neutral-700/50']">
          <div :class="['flex items-baseline justify-between gap-2']">
            <span :class="['font-medium']">{{ entry.name }}</span>
            <span :class="['text-xs opacity-60']">{{ who(entry) }}</span>
          </div>
          <div :class="['text-sm opacity-80']">
            {{ entry.description }}
          </div>
          <div :class="['mt-1 whitespace-pre-wrap text-sm']">
            {{ entry.body }}
          </div>
          <div :class="['mt-2 flex gap-2']">
            <Button v-if="entry.persona" variant="secondary" :title="t('settings.pages.memory.entries.general_hint')" @click="memory.makeGeneral(entry)">
              {{ t('settings.pages.memory.entries.general') }}
            </Button>
            <Button variant="secondary" data-testid="memory-remove" @click="memory.remove(entry)">
              {{ t('settings.pages.memory.entries.remove') }}
            </Button>
          </div>
        </li>
      </ul>
    </section>

    <section :class="['flex flex-col gap-2']">
      <h2 :class="['text-lg font-medium']">
        {{ t('settings.pages.memory.add.title') }}
      </h2>
      <input v-model="draft.name" data-testid="memory-new-name" :placeholder="t('settings.pages.memory.add.name')" :class="['rounded-lg border border-neutral-300/50 bg-transparent p-2 dark:border-neutral-700/50']">
      <input v-model="draft.description" :placeholder="t('settings.pages.memory.add.description')" maxlength="150" :class="['rounded-lg border border-neutral-300/50 bg-transparent p-2 dark:border-neutral-700/50']">
      <textarea v-model="draft.body" data-testid="memory-new-body" :placeholder="t('settings.pages.memory.add.body')" maxlength="2000" rows="3" :class="['rounded-lg border border-neutral-300/50 bg-transparent p-2 dark:border-neutral-700/50']" />
      <div>
        <Button data-testid="memory-save" @click="save">
          {{ t('settings.pages.memory.add.save') }}
        </Button>
      </div>
    </section>
  </div>
</template>

<route lang="yaml">
meta:
  layout: settings
  titleKey: settings.pages.memory.title
  subtitleKey: settings.title
  descriptionKey: settings.pages.memory.description
  icon: i-solar:leaf-bold-duotone
  settingsEntry: true
  order: 5
  stageTransition:
    name: slide
</route>
