<script setup lang="ts">
import type { Look, LookSize } from '../../../shared/jarvis-look'

import { nextTick, onMounted, useTemplateRef } from 'vue'

import { LOOK_LABELS, LOOK_SIZES, LOOKS } from '../../../shared/jarvis-look'

import './orb.css'

/**
 * The small menu of the orb (D78): Hide, Look (Orb / Avatar / Minimal bar), Size, Always on top, Reset position, Settings, Quit. A real menu for
 * the keyboard: arrows move, Enter chooses, Escape closes and gives the focus back. Presentational: it only says what the owner chose.
 */
const props = defineProps<{
  look: Look
  size: LookSize
  alwaysOnTop: boolean
  hideShortcut: string
  /** The hide shortcut could not be registered (another program holds it): the menu says so, and Hide stays here. */
  hideShortcutOk: boolean
}>()

const emit = defineEmits<{
  hide: []
  look: [look: Look]
  size: [size: LookSize]
  alwaysOnTop: [value: boolean]
  reset: []
  settings: []
  quit: []
  close: []
}>()

const root = useTemplateRef<HTMLElement>('root')

function items(): HTMLElement[] {
  return Array.from(root.value?.querySelectorAll<HTMLElement>('.orb-menu-item') ?? [])
}
function onKeydown(event: KeyboardEvent) {
  const all = items()
  const index = all.indexOf(document.activeElement as HTMLElement)
  if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
    event.preventDefault()
    all[(index + 1) % all.length]?.focus()
  }
  else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
    event.preventDefault()
    all[(index - 1 + all.length) % all.length]?.focus()
  }
  else if (event.key === 'Home') {
    event.preventDefault()
    all[0]?.focus()
  }
  else if (event.key === 'End') {
    event.preventDefault()
    all.at(-1)?.focus()
  }
  else if (event.key === 'Escape') {
    event.preventDefault()
    event.stopPropagation()
    emit('close')
  }
  else if (event.key === 'Tab') {
    emit('close')
  }
}
onMounted(async () => {
  await nextTick()
  items()[0]?.focus({ preventScroll: true })
})
const shortcutText = props.hideShortcut.replace('Control', 'Ctrl')
</script>

<template>
  <div
    ref="root"
    class="orb-menu orb-hit"
    data-orb-hit
    data-testid="orb-menu"
    role="menu"
    aria-label="JARVIS menu"
    @keydown="onKeydown"
    @contextmenu.prevent
  >
    <button class="orb-menu-item" type="button" role="menuitem" @click="emit('hide')">
      <span>Hide</span>
      <span class="orb-menu-key">{{ hideShortcutOk ? shortcutText : 'no shortcut' }}</span>
    </button>
    <div class="orb-menu-sep" role="separator" />
    <div class="orb-menu-head" role="presentation">
      Look
    </div>
    <button
      v-for="option in LOOKS"
      :key="option"
      class="orb-menu-item"
      type="button"
      role="menuitemradio"
      :aria-checked="look === option"
      @click="emit('look', option)"
    >
      {{ LOOK_LABELS[option] }}
    </button>
    <div class="orb-menu-head" role="presentation">
      Size
    </div>
    <div class="orb-menu-sizes" role="group" aria-label="Size">
      <button
        v-for="option in LOOK_SIZES"
        :key="option"
        class="orb-menu-item"
        type="button"
        role="menuitem"
        :aria-pressed="size === option"
        :aria-label="`Size ${option}`"
        @click="emit('size', option)"
      >
        {{ option }}
      </button>
    </div>
    <div class="orb-menu-sep" role="separator" />
    <button class="orb-menu-item" type="button" role="menuitemcheckbox" :aria-checked="alwaysOnTop" @click="emit('alwaysOnTop', !alwaysOnTop)">
      Always on top
    </button>
    <button class="orb-menu-item" type="button" role="menuitem" @click="emit('reset')">
      Reset position
    </button>
    <button class="orb-menu-item" type="button" role="menuitem" @click="emit('settings')">
      Settings
    </button>
    <button class="orb-menu-item" type="button" role="menuitem" @click="emit('quit')">
      Quit
    </button>
  </div>
</template>
