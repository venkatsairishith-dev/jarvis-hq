import { useLocalStorage } from '@vueuse/core'
import { defineStore } from 'pinia'
import { computed, ref, shallowRef } from 'vue'

/**
 * JARVIS D60 (speed): the switches. Each has a setting (Settings > System > General: Automatic, On, Off) and an environment default that the main process
 * reads (`JARVIS_FACE_DIRECT_TOOLS`, `JARVIS_FACE_QUICK_ACK`, `JARVIS_FACE_QUICK_COMMAND`, see main/services/airi/jarvis-speed). Automatic follows the
 * environment (which is on unless set to 0); On and Off win over it.
 *
 * - direct tools: the first chat request carries the real JARVIS tools, so the model calls them in round one (no list-then-call proxies).
 * - quick ack:    a PC, screen or document message shows "On it" and a progress line at once, before any model answers.
 * - quick command: a message that is one plain command ("open notepad", "pause", "what time is it") is run by rules at once, with no chat model request.
 */

export type SpeedSwitch = 'auto' | 'on' | 'off'

export const SPEED_KEYS = {
  directTools: 'settings/jarvis-speed/direct-tools',
  quickAck: 'settings/jarvis-speed/quick-ack',
  quickCommand: 'settings/jarvis-speed/quick-command',
} as const

export function normalizeSwitch(raw: unknown): SpeedSwitch {
  return raw === 'on' || raw === 'off' ? raw : 'auto'
}

/** On/Off win; Automatic follows the environment's default. */
export function resolveSwitch(setting: unknown, envDefault: boolean): boolean {
  const s = normalizeSwitch(setting)
  return s === 'auto' ? envDefault : s === 'on'
}

export interface SpeedEnvDefaults {
  directTools: boolean
  quickAck: boolean
  quickCommand: boolean
  timing: boolean
}

/** The instant acknowledgement of a PC, screen or document message: one short line, until its turn ends. */
export interface SpeedAck {
  line: string
  kind: string
  turnId: string
}

export const useJarvisSpeedStore = defineStore('jarvis-speed', () => {
  const directToolsSetting = useLocalStorage<string>(SPEED_KEYS.directTools, 'auto')
  const quickAckSetting = useLocalStorage<string>(SPEED_KEYS.quickAck, 'auto')
  const quickCommandSetting = useLocalStorage<string>(SPEED_KEYS.quickCommand, 'auto')
  /** What the main process says the environment gives. Until it answers, the defaults are on (the same as an unset environment). */
  const env = ref<SpeedEnvDefaults>({ directTools: true, quickAck: true, quickCommand: true, timing: true })

  const directTools = computed(() => resolveSwitch(directToolsSetting.value, env.value.directTools))
  const quickAck = computed(() => resolveSwitch(quickAckSetting.value, env.value.quickAck))
  const quickCommand = computed(() => resolveSwitch(quickCommandSetting.value, env.value.quickCommand))
  const timing = computed(() => env.value.timing)

  function setEnv(next: Partial<SpeedEnvDefaults> | undefined | null) {
    if (!next || typeof next !== 'object')
      return
    env.value = {
      directTools: typeof next.directTools === 'boolean' ? next.directTools : env.value.directTools,
      quickAck: typeof next.quickAck === 'boolean' ? next.quickAck : env.value.quickAck,
      quickCommand: typeof next.quickCommand === 'boolean' ? next.quickCommand : env.value.quickCommand,
      timing: typeof next.timing === 'boolean' ? next.timing : env.value.timing,
    }
  }

  /** Set when the owner's message is a task and the switch is on; cleared when that turn ends. Read by the HUD (one window: the main stage). */
  const ack = shallowRef<SpeedAck | null>(null)

  function setAck(next: SpeedAck | null) {
    ack.value = next
  }

  /** Clears the acknowledgement of one turn (or whichever is showing, without an id). A newer turn's acknowledgement stays. */
  function clearAck(turnId?: string) {
    if (!turnId || ack.value?.turnId === turnId)
      ack.value = null
  }

  return { ack, setAck, clearAck, directToolsSetting, quickAckSetting, quickCommandSetting, env, directTools, quickAck, quickCommand, timing, setEnv }
})
