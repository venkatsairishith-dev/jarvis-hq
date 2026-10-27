/**
 * The signals of the JARVIS HUD, collected from what the face already knows, and the one small poll for the rest.
 *
 * Real signals only:
 * - speaking: the character's voice is playing (`useSpeakingStore().nowSpeaking`; its mouth level drives the ring's pulse);
 * - listening: a speech input is being captured (`useVoiceControlsStore().snapshot.input.phase === 'capturing'`);
 * - thinking: a chat turn runs (`useChatStore().activeTurns`) or the speech is being transcribed (`phase === 'finalizing'`);
 * - the model name: `useConsciousnessStore().activeModel`;
 * - kernel up or down, the kill switch, the PC task that is running, waiting approvals, unread plugin messages: the tool
 *   server's `jarvis_status` (jarvis/bridge/face_tools.py), which reads the desktop plugin's `pc_operations.json` and holds no
 *   title, id or text.
 *
 * Nothing is sent anywhere new: the poll goes through the same MCP tool route as `jarvis_spoken` (loopback). Only the main stage
 * window collects. With the setting off, nothing is polled.
 */

import type { StatusPoll } from '../components/hud/hud-model'

import { useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { useSpeakingStore } from '@proj-airi/stage-ui/stores/audio'
import { useChatStore } from '@proj-airi/stage-ui/stores/chat'
import { useJarvisSpeedStore } from '@proj-airi/stage-ui/stores/jarvis-speed'
import { useConsciousnessStore } from '@proj-airi/stage-ui/stores/modules/consciousness'
import { useVoiceControlsStore } from '@proj-airi/stage-ui/stores/voice-controls'
import { defineStore, storeToRefs } from 'pinia'
import { computed, onScopeDispose, ref, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'

import { electronMcpCallTool } from '../../shared/eventa'
import {
  hudAriaLabel,
  hudLine,
  hudLook,
  hudStateLabel,
  JARVIS_STATUS_TOOL,
  reduceStatusPoll,
  resolveHudLink,
  resolveHudState,
  shortModel,
} from '../components/hud/hud-model'
import { useJarvisHudSettingsStore } from '../stores/settings/jarvis-hud'
import { useJarvisLookStore } from '../stores/settings/jarvis-look'
import { resolveInitialRendererRoutePath, resolveRendererWindowContext } from '../window-context'
import { pageIsHidden, POLL_CALL_TIMEOUT_MS, POLL_WAIT_MS, shouldPollNow, withDeadline } from './poll-guard'

/** How often the status is asked for, in milliseconds. A local read; the PC registry file itself is rewritten every 5 s. */
export const HUD_POLL_MS = 4_000
/** D60: while a task is under way (the instant "On it", or a goal's steps) the status is asked for this often, even when another app has the focus, so the step lines keep up. */
export const HUD_ACTIVE_POLL_MS = 1_500
/** While the window does not have the keyboard focus (the owner works in another app) the status is asked for this rarely. */
export const HUD_UNFOCUSED_POLL_MS = 15_000

/** The HUD's live state. The signals are written by {@link useJarvisHud}; the components only read it. */
export const useJarvisHudStore = defineStore('jarvis-hud', () => {
  const poll = shallowRef<StatusPoll>({ status: null, failures: 0 })
  /** When the last good status arrived, so the strip can keep a task's age moving between polls. */
  const statusAt = ref(0)
  const speaking = ref(false)
  const listening = ref(false)
  const thinking = ref(false)
  const voiceError = ref(false)
  /** D60: the instant acknowledgement of a task message ("On it"), set by the chat store before any model answers. */
  const ack = shallowRef<{ line: string, kind: string, turnId: string } | null>(null)
  const model = ref('')
  /** The character's mouth, 0 to 1, for the pulse of the ring. Written often; read only by a watcher that sets one CSS variable. */
  const level = ref(0)

  const link = computed(() => resolveHudLink(poll.value))
  const status = computed(() => poll.value.status)
  const state = computed(() => resolveHudState({
    speaking: speaking.value,
    listening: listening.value,
    thinking: thinking.value,
    voiceError: voiceError.value,
    link: link.value,
    pcBusy: poll.value.status?.pcBusy ?? false,
    ack: ack.value !== null,
  }))
  /** The one short line under way: the goal's step from the status, else the acknowledgement. */
  const line = computed(() => hudLine(ack.value?.line, poll.value.status))
  /** A task is being acknowledged or a goal runs: the status is worth asking for more often. */
  const taskActive = computed(() => ack.value !== null || ['planning', 'running', 'waiting'].includes(poll.value.status?.goalState ?? 'none'))
  const stateLabel = computed(() => hudStateLabel(state.value, link.value))
  const ariaLabel = computed(() => hudAriaLabel(state.value, link.value, poll.value.status))
  const modelLabel = computed(() => shortModel(model.value))

  function setPoll(next: StatusPoll) {
    poll.value = next
    if (next.failures === 0)
      statusAt.value = Date.now()
  }

  return { poll, statusAt, speaking, listening, thinking, voiceError, ack, line, taskActive, model, level, link, status, state, stateLabel, ariaLabel, modelLabel, setPoll }
})

/**
 * Starts the HUD's signals in the main stage window. Call it once from App.vue. Returns whether the HUD is drawn
 * (the window is the main stage and the setting is not "off") and how it should look.
 */
export function useJarvisHud() {
  const settings = useJarvisHudSettingsStore()
  const route = useRoute()
  const windowContext = resolveRendererWindowContext()
  // Only the main stage window (the full runtime on the stage page). Settings, chat and the other windows have no HUD,
  // and they do not even create the stores that the signals come from.
  const eligible = windowContext.stageRuntime === 'full' && resolveInitialRendererRoutePath(route.path) === '/'
  const look = computed(() => hudLook(settings.mode))
  // D78: in the orb and bar looks the orb is the interface, so the HUD layer is not drawn, but its signals are still collected (the orb reads them)
  const jarvisLook = useJarvisLookStore()
  const visible = computed(() => eligible && look.value.visible && jarvisLook.look === 'avatar')
  const collecting = computed(() => eligible && (look.value.visible || jarvisLook.look !== 'avatar'))

  if (!eligible)
    return { visible, look, mode: computed(() => settings.mode) }

  const hud = useJarvisHudStore()
  const speakingStore = useSpeakingStore()
  const voiceControls = useVoiceControlsStore()
  const chatStore = useChatStore()
  const consciousness = useConsciousnessStore()
  const speed = useJarvisSpeedStore()
  const { nowSpeaking, mouthOpenSize } = storeToRefs(speakingStore)
  const { snapshot: voiceSnapshot } = storeToRefs(voiceControls)
  const { activeModel } = storeToRefs(consciousness)
  const callMcpTool = useElectronEventaInvoke(electronMcpCallTool)

  watch(nowSpeaking, v => hud.speaking = !!v, { immediate: true })
  watch(mouthOpenSize, v => hud.level = Math.min(1, Math.max(0, Number(v) || 0)))
  watch(() => voiceSnapshot.value.input?.phase, (phase) => {
    hud.listening = phase === 'capturing'
  }, { immediate: true })
  watch(
    [() => voiceSnapshot.value.input?.phase, () => chatStore.activeTurns.length],
    ([phase, turns]) => {
      hud.thinking = phase === 'finalizing' || turns > 0
    },
    { immediate: true },
  )
  watch(() => voiceSnapshot.value.microphone?.error, e => hud.voiceError = !!e, { immediate: true })
  watch(activeModel, v => hud.model = v ?? '', { immediate: true })
  watch(() => speed.ack, v => hud.ack = v, { immediate: true })

  let timer: ReturnType<typeof setInterval> | undefined
  let running = false
  let lastPollAt = 0
  async function pollOnce() {
    // No work for a window nobody sees; the next visible tick catches up. A window the owner is not using polls less often.
    const now = Date.now()
    if (running || !shouldPollNow({ hidden: pageIsHidden(), focused: globalThis.document?.hasFocus?.() ?? true, now, lastPollAt, unfocusedMs: hud.taskActive ? HUD_ACTIVE_POLL_MS : HUD_UNFOCUSED_POLL_MS }))
      return
    running = true
    lastPollAt = now
    try {
      // A status is worth a few seconds, not the 180 s of a real tool call: a stuck tool server must not hold the poll.
      const result = await withDeadline(callMcpTool({ name: JARVIS_STATUS_TOOL, timeoutMs: POLL_CALL_TIMEOUT_MS }), POLL_WAIT_MS, 'the status poll')
      hud.setPoll(reduceStatusPoll(hud.poll, result))
    }
    catch {
      hud.setPoll(reduceStatusPoll(hud.poll, undefined))
    }
    finally {
      running = false
    }
  }
  function stop() {
    if (timer)
      clearInterval(timer)
    timer = undefined
  }
  watch([collecting, () => hud.taskActive], ([on, active], previous) => {
    stop()
    if (!on)
      return
    // a task just began: ask now, so the first step line does not wait for the next tick
    if (active && !previous?.[1])
      lastPollAt = 0
    void pollOnce()
    timer = setInterval(() => void pollOnce(), active ? HUD_ACTIVE_POLL_MS : HUD_POLL_MS)
  }, { immediate: true })
  onScopeDispose(stop)

  return { visible, look, mode: computed(() => settings.mode) }
}
