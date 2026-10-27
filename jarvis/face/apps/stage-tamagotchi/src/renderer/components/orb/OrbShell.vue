<script setup lang="ts">
/**
 * D78: the main window when the look is the orb or the minimal bar.
 *
 * It replaces the avatar page: no Live2D, VRM or MMD scene, no WebGL, no model files, no animation frame loop. It connects the pure parts
 * (orb-state, orb-gesture, orb-shell-logic) and the presentational ones (OrbView, MinimalBar, OrbMenu) to what the face already knows:
 * the HUD's signals (state, status line, approvals), the microphone level, the voice output, the chat box and the panels.
 *
 *  - click: talk or stop; double-click: the text box (the chat window, beside the orb); right-click or the menu key: the small menu;
 *  - the ask form, the timeline and the preview open as cards beside the orb: the main process makes the window big enough and says where;
 *  - the window passes the mouse through everywhere that is not the orb, the pill, the menu or a card, so it never blocks the apps behind;
 *  - hidden mode: the main process hides the windows; the orb only shows, once, how many things waited, and one quiet pulse after a long hide.
 */
import type { LookSettings } from '../../../shared/jarvis-look'
import type { UnhideNotice } from '../../composables/hidden-mode-logic'

import { electron } from '@proj-airi/electron-eventa'
import { useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { useChatSessionStore } from '@proj-airi/stage-ui/stores/chat/session-store'
import { useHearingStore } from '@proj-airi/stage-ui/stores/modules/hearing'
import { useSettings } from '@proj-airi/stage-ui/stores/settings'
import { useSettingsAudioDevice } from '@proj-airi/stage-ui/stores/settings/audio-device'
import { useSpeechOutputControlStore } from '@proj-airi/stage-ui/stores/speech-output-control'
import { useVoiceStore } from '@proj-airi/stage-ui/stores/voice'
import { useVoiceControlsStore } from '@proj-airi/stage-ui/stores/voice-controls'
import { storeToRefs } from 'pinia'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, useTemplateRef, watch } from 'vue'

import MinimalBar from './MinimalBar.vue'
import OrbMenu from './OrbMenu.vue'
import OrbSpeechHost from './OrbSpeechHost.vue'
import OrbView from './OrbView.vue'

import {
  electronAppQuit,
  electronGetChatButtonState,
  electronOpenChat,
  electronOpenSettings,
  electronWindowSetAlwaysOnTop,
} from '../../../shared/eventa'
import { zoneSize } from '../../../shared/jarvis-look'
import { computeOrbWindow, zoneRect } from '../../../shared/orb-layout'
import { useHiddenModeEffects } from '../../composables/use-hidden-mode-effects'
import { useJarvisHudStore } from '../../composables/use-jarvis-hud'
import { useMainWindowVoice } from '../../composables/use-main-window-voice'
import { useJarvisLookStore } from '../../stores/settings/jarvis-look'
import { askOpenFromDom, CARD_SELECTOR, cardFromDom, orbRequest, pillText, sameRequest, shouldIgnoreMouse } from './orb-shell-logic'
import { orbProgress, resolveOrbState } from './orb-state'

import './orb.css'

const look = useJarvisLookStore()
look.start()
const hud = useJarvisHudStore()
const { notice: unhideNotice, pulse } = useHiddenModeEffects()
useMainWindowVoice()

const voice = useVoiceStore()
const voiceControls = useVoiceControlsStore()
const devices = useSettingsAudioDevice()
const hearing = useHearingStore()
const sessions = useChatSessionStore()
const speechOutput = useSpeechOutputControlStore()
const settingsStore = useSettings()
const { alwaysOnTop } = storeToRefs(settingsStore)

const openChat = useElectronEventaInvoke(electronOpenChat)
const chatState = useElectronEventaInvoke(electronGetChatButtonState)
const openSettings = useElectronEventaInvoke(electronOpenSettings)
const setAlwaysOnTop = useElectronEventaInvoke(electronWindowSetAlwaysOnTop)
const setIgnoreMouse = useElectronEventaInvoke(electron.window.setIgnoreMouseEvents)
const quitApp = useElectronEventaInvoke(electronAppQuit)

const settings = computed<LookSettings>(() => look.settings)
const lookKind = computed(() => look.look === 'minimal' ? 'minimal' : 'orb')
const size = computed(() => settings.value.size)

// ---- the state
const askOpen = ref(false)
const cardSize = ref<{ width: number, height: number } | null>(null)
const menuOpen = ref(false)
const notice = ref('')
let noticeTimer: ReturnType<typeof setTimeout> | undefined
function say(text: string) {
  notice.value = text
  clearTimeout(noticeTimer)
  noticeTimer = setTimeout(() => {
    notice.value = ''
  }, 5000)
}

const state = computed(() => resolveOrbState({
  hud: hud.state,
  link: hud.link,
  approvalsWaiting: hud.status?.approvalsWaiting ?? 0,
  askOpen: askOpen.value,
  voiceError: hud.voiceError,
}))
const progress = computed(() => orbProgress(hud.status))
const pill = computed(() => pillText(state.value, hud.line, notice.value))
const level = computed(() => state.value === 'listening' ? voiceControls.level : state.value === 'speaking' ? hud.level : 0)

// ---- what is open beside the orb (the panels are drawn by their own hosts; the shell only watches that they are there)
let observer: MutationObserver | undefined
function readCards() {
  askOpen.value = askOpenFromDom(document)
  cardSize.value = cardFromDom(document)
}
onMounted(() => {
  document.documentElement.setAttribute('data-jarvis-zone', '')
  readCards()
  observer = new MutationObserver(readCards)
  observer.observe(document.body, { childList: true, subtree: true })
  window.addEventListener('mousemove', onMouseMove, { passive: true })
  void alwaysOnTopApply()
})
onBeforeUnmount(() => {
  observer?.disconnect()
  window.removeEventListener('mousemove', onMouseMove)
  document.documentElement.removeAttribute('data-jarvis-zone')
  document.documentElement.removeAttribute('data-jarvis-card-ready')
  clearTimeout(noticeTimer)
})
function alwaysOnTopApply() {
  return setAlwaysOnTop(alwaysOnTop.value)
}
watch(alwaysOnTop, () => void alwaysOnTopApply())

// ---- the window around the orb: the main process places it; until it answers, a local layout keeps the orb where it belongs
const request = computed(() => orbRequest({ state: state.value, line: hud.line, notice: notice.value, card: cardSize.value, menu: menuOpen.value }))
let lastRequest: ReturnType<typeof orbRequest> | undefined
watch(request, (next) => {
  if (sameRequest(lastRequest, next))
    return
  lastRequest = next
  void look.request(next).catch(error => console.warn('[JARVIS] the orb could not ask for room:', error))
}, { immediate: true })

const layout = computed(() => {
  const given = look.layout
  if (given.zone)
    return given
  // no main process (a test, the render harness): work it out here, with the zone at the top left of a window as large as the page
  const z = zoneSize(lookKind.value, size.value)
  const area = { x: 0, y: 0, width: window.innerWidth, height: window.innerHeight }
  const local = computeOrbWindow({ zone: zoneRect(lookKind.value, size.value, { x: 0, y: 0 }), look: lookKind.value, pill: request.value.pill, card: request.value.card, menu: request.value.menu, workArea: area })
  return { look: lookKind.value, size: size.value, window: local.window, zone: local.zone ?? { x: 0, y: 0, ...z }, pill: local.pill, card: local.card, cardSide: local.cardSide, menu: local.menu }
})

function px(rect: { x: number, y: number, width: number, height: number } | null, extra: Record<string, string> = {}) {
  if (!rect)
    return undefined
  return { left: `${rect.x}px`, top: `${rect.y}px`, width: `${rect.width}px`, height: `${rect.height}px`, ...extra }
}
const zoneStyle = computed(() => layout.value.zone ? { left: `${layout.value.zone.x}px`, top: `${layout.value.zone.y}px`, ...(lookKind.value === 'minimal' ? { width: `${layout.value.zone.width}px`, height: `${layout.value.zone.height}px` } : {}) } : undefined)
const pillStyle = computed(() => px(layout.value.pill))
const menuStyle = computed(() => px(layout.value.menu))
const badgeStyle = computed(() => layout.value.zone ? { left: `${layout.value.zone.x + layout.value.zone.width - 22}px`, top: `${layout.value.zone.y + 2}px` } : undefined)

// the panels take the rectangle the main process gave them
watch(() => layout.value.card, (card) => {
  const root = document.documentElement
  if (!card) {
    root.removeAttribute('data-jarvis-card-ready')
    return
  }
  root.style.setProperty('--orb-card-x', `${card.x}px`)
  root.style.setProperty('--orb-card-y', `${card.y}px`)
  root.style.setProperty('--orb-card-w', `${card.width}px`)
  root.style.setProperty('--orb-card-h', `${card.height}px`)
  root.setAttribute('data-jarvis-card-ready', '')
}, { immediate: true })

// ---- click-through: the window takes the mouse only over the orb, the pill, the menu and the cards
const HIT = `[data-orb-hit], ${CARD_SELECTOR}`
const biggerThanZone = computed(() => {
  const w = layout.value.window
  const z = layout.value.zone
  return !!w && !!z && (w.width > z.width + 1 || w.height > z.height + 1)
})
let ignoring = false
function onMouseMove(event: MouseEvent) {
  const target = document.elementFromPoint(event.clientX, event.clientY)
  const ignore = shouldIgnoreMouse(!!target?.closest(HIT), biggerThanZone.value)
  if (ignore !== ignoring) {
    ignoring = ignore
    void setIgnoreMouse([ignore, { forward: true }])
  }
}
watch(biggerThanZone, (bigger) => {
  if (!bigger && ignoring) {
    ignoring = false
    void setIgnoreMouse([false, { forward: true }])
  }
})

// ---- what the owner does
const orb = useTemplateRef<InstanceType<typeof OrbView>>('orb')
const bar = useTemplateRef<InstanceType<typeof MinimalBar>>('bar')

function onTalk() {
  if (state.value === 'speaking') {
    void speechOutput.requestStopSpeaking({ reason: 'manual-all' }).catch(() => {})
    return
  }
  if (voice.state?.phase === 'capturing') {
    void voice.endInput()
    return
  }
  if (!devices.enabled) {
    say('Microphone is off. Turn it on in Settings > Hearing.')
    return
  }
  if (!hearing.configured) {
    say('Set up hearing in Settings first.')
    return
  }
  const sessionId = sessions.activeSessionId
  if (!sessionId)
    return
  if (!voice.beginManual(sessionId))
    say('The microphone is busy in another window.')
}

async function onText() {
  try {
    const shown = await chatState()
    if (!shown || typeof shown !== 'object' || !('floatingShown' in shown) || !(shown as { floatingShown: boolean }).floatingShown)
      await openChat()
  }
  catch (error) {
    console.warn('[JARVIS] the text box could not be opened:', error)
  }
}

function openMenu() {
  menuOpen.value = true
}
async function closeMenu(refocus = true) {
  menuOpen.value = false
  if (refocus) {
    await nextTick()
    ;(orb.value ?? bar.value)?.focus()
  }
}

let lastMove = 0
function onDragStart() {
  void look.drag('start')
}
function onDragMove() {
  const now = performance.now()
  if (now - lastMove < 16)
    return
  lastMove = now
  void look.drag('move')
}
function onDragEnd() {
  void look.drag('end')
}
function onNudge(direction: 'left' | 'right' | 'up' | 'down') {
  void look.nudge(direction)
}

function chooseHide() {
  void closeMenu(false)
  void look.setHidden(true)
}
function chooseLook(next: LookSettings['look']) {
  void closeMenu(false)
  void look.update({ look: next })
}
function chooseSize(next: LookSettings['size']) {
  void look.update({ size: next })
}
function chooseReset() {
  void closeMenu()
  void look.resetPosition()
}
function chooseSettings() {
  void closeMenu(false)
  void openSettings({ route: '/settings' })
}
function chooseQuit() {
  void closeMenu(false)
  void quitApp()
}

const unhideText = computed(() => {
  const n: UnhideNotice | undefined = unhideNotice.value
  if (!n)
    return ''
  return n.queued > 9 ? '9+' : String(n.queued)
})
const unhideLabel = computed(() => unhideNotice.value ? `${unhideNotice.value.queued} ${unhideNotice.value.queued === 1 ? 'thing' : 'things'} waited while JARVIS was hidden` : '')
</script>

<template>
  <div class="orb-root" :data-look="lookKind">
    <OrbSpeechHost />
    <OrbView
      v-if="lookKind === 'orb'"
      ref="orb"
      class="orb-place"
      :style="zoneStyle"
      :state="state"
      :size="size"
      :progress="progress"
      :level="level"
      :line="hud.line"
      :link="hud.link"
      :pulse="pulse"
      @talk="onTalk"
      @text="onText"
      @menu="openMenu"
      @nudge="onNudge"
      @drag-start="onDragStart"
      @drag-move="onDragMove"
      @drag-end="onDragEnd"
    />
    <MinimalBar
      v-else
      ref="bar"
      class="orb-place"
      :style="zoneStyle"
      :state="state"
      :size="size"
      :line="hud.line"
      :level="level"
      :link="hud.link"
      @talk="onTalk"
      @text="onText"
      @menu="openMenu"
      @nudge="onNudge"
      @drag-start="onDragStart"
      @drag-move="onDragMove"
      @drag-end="onDragEnd"
    />
    <div v-if="pill && lookKind === 'orb' && layout.pill" class="orb-pill orb-hit" data-orb-hit role="status" :title="pill" :style="pillStyle">
      {{ pill }}
    </div>
    <div v-if="unhideText" class="orb-badge" role="status" :aria-label="unhideLabel" :title="unhideLabel" :style="badgeStyle">
      {{ unhideText }}
    </div>
    <OrbMenu
      v-if="menuOpen && layout.menu"
      :style="menuStyle"
      :look="look.look"
      :size="size"
      :always-on-top="alwaysOnTop"
      :hide-shortcut="settings.hideShortcut"
      :hide-shortcut-ok="look.shortcuts.hide.ok"
      @hide="chooseHide"
      @look="chooseLook"
      @size="chooseSize"
      @always-on-top="alwaysOnTop = $event"
      @reset="chooseReset"
      @settings="chooseSettings"
      @quit="chooseQuit"
      @close="closeMenu()"
    />
  </div>
</template>
