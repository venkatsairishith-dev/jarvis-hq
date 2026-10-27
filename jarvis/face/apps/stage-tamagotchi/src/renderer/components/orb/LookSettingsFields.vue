<script setup lang="ts">
import type { Look, LookSize } from '../../../shared/jarvis-look'

import { FieldCheckbox, FieldInput, FieldSelect } from '@proj-airi/ui'
import { computed, shallowRef, watch } from 'vue'

import { LOOK_LABELS, LOOKS } from '../../../shared/jarvis-look'
import { useJarvisLookStore } from '../../stores/settings/jarvis-look'

/**
 * Settings > System > General (D78): how JARVIS looks (Orb, Avatar, Minimal bar), its size, and the hidden mode: the shortcut, the tray icon
 * while hidden, "start hidden", "speak while hidden" and the forgotten-hidden limit. A change applies at once, with no restart. The main process
 * keeps the values (see main/jarvis/look-config.ts); this page only asks for a change and shows the answer.
 */
const look = useJarvisLookStore()
look.start()

const lookOptions = LOOKS.map(value => ({
  label: LOOK_LABELS[value],
  value,
  description: value === 'orb'
    ? 'A small calm orb. Click to talk, double-click for the text box, right-click for the menu.'
    : value === 'avatar'
      ? 'The character (Live2D, VRM or MMD). Uses more of the PC.'
      : 'A thin one-line bar with the status and a microphone button.',
}))
const sizeOptions = (['S', 'M', 'L'] as const).map(value => ({ label: { S: 'Small', M: 'Medium', L: 'Large' }[value], value }))

const current = computed<Look>({ get: () => look.look, set: value => void look.update({ look: value }) })
const size = computed<LookSize>({ get: () => look.settings.size, set: value => void look.update({ size: value }) })

const hideShortcut = shallowRef(look.settings.hideShortcut)
const resetShortcut = shallowRef(look.settings.resetShortcut)
watch(() => look.settings.hideShortcut, v => hideShortcut.value = v)
watch(() => look.settings.resetShortcut, v => resetShortcut.value = v)
const hours = shallowRef(String(look.settings.forgottenHours))
watch(() => look.settings.forgottenHours, v => hours.value = String(v))

const shortcutProblem = computed(() => {
  const status = look.shortcuts
  const bad = [status.hide, status.reset].find(entry => entry.accelerator && !entry.ok)
  if (!bad)
    return ''
  return bad.reason === 'invalid'
    ? `${bad.accelerator} is not a usable shortcut (use Ctrl, Alt or Super with a key).`
    : `${bad.accelerator} is not available (another program uses it). The menu and the tray icon still hide and show JARVIS.`
})
</script>

<template>
  <FieldSelect
    v-model="current"
    label="Look"
    description="How JARVIS appears on the screen. The change applies at once."
    :options="lookOptions"
  />
  <FieldSelect
    v-model="size"
    label="Size"
    description="Small, medium or large orb or bar."
    :options="sizeOptions"
  />
  <FieldInput
    v-model="hideShortcut"
    label="Hide / show shortcut"
    description="Hides every JARVIS window and shows them again. Default Ctrl+Alt+J (written Control+Alt+J)."
    @update:model-value="(v: string | undefined) => v && look.update({ hideShortcut: v })"
  />
  <FieldInput
    v-model="resetShortcut"
    label="Reset position shortcut"
    description="Puts the orb back in the bottom right corner. Default Control+Alt+Shift+J."
    @update:model-value="(v: string | undefined) => v && look.update({ resetShortcut: v })"
  />
  <p v-if="shortcutProblem" class="text-sm text-amber-700 dark:text-amber-300" role="status">
    {{ shortcutProblem }}
  </p>
  <FieldCheckbox
    :model-value="look.settings.showTrayWhileHidden"
    label="Show the tray icon while hidden"
    description="Off by default: while hidden JARVIS has no window, no taskbar button and no tray icon. If the shortcut is not available the tray icon stays."
    @update:model-value="(v: boolean) => look.update({ showTrayWhileHidden: v })"
  />
  <FieldCheckbox
    :model-value="look.settings.startHidden"
    label="Start hidden"
    description="Off by default: after a restart JARVIS always shows its orb, so it can never be lost. When on, a restart keeps it hidden (never on a first run or after an update)."
    @update:model-value="(v: boolean) => look.update({ startHidden: v })"
  />
  <FieldCheckbox
    :model-value="look.settings.speakWhileHidden"
    label="Speak while hidden"
    description="Off by default: while hidden JARVIS makes no sound. Work goes on in the background and the results wait for you."
    @update:model-value="(v: boolean) => look.update({ speakWhileHidden: v })"
  />
  <FieldInput
    v-model="hours"
    type="number"
    label="Forgotten-hidden reminder (hours)"
    description="After a hide longer than this, the next time you show JARVIS the orb pulses once. 0 turns it off."
    @update:model-value="(v: string | number | undefined) => v !== undefined && v !== '' && look.update({ forgottenHours: Number(v) })"
  />
</template>
