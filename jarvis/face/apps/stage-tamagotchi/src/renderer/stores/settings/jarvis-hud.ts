import type { HudMode } from '../../components/hud/hud-model'

import { useLocalStorage } from '@vueuse/core'
import { defineStore } from 'pinia'
import { computed } from 'vue'

import { HUD_DEFAULT_MODE, HUD_MODE_KEY, normalizeHudMode } from '../../components/hud/hud-model'

/**
 * The HUD setting: off, subtle (the default) or full. Stored in localStorage under `settings/jarvis-hud/mode`, the same way the
 * face keeps its other window settings (see stores/settings/server-channel.ts). A value that is not a mode reads as the default.
 */
export const useJarvisHudSettingsStore = defineStore('jarvis-hud-settings', () => {
  const stored = useLocalStorage<string>(HUD_MODE_KEY, HUD_DEFAULT_MODE)
  const mode = computed<HudMode>({
    get: () => normalizeHudMode(stored.value),
    set: (value) => {
      stored.value = normalizeHudMode(value)
    },
  })

  return { mode }
})
