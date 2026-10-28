/**
 * D78: what the main window does with the hidden mode, for every look.
 *
 * The main process hides the windows (the window gate). This composable is the other half, in the page: while JARVIS is hidden it makes no sound
 * (unless the owner turned on "speak while hidden"), it stops the background polls and the proactive speaking, and after an unhide it tells the
 * orb what to show once: the count of what waited, and the single quiet pulse after a long hide. Nothing here shows anything while hidden, and
 * nothing here reads or logs the content of what waited: only numbers arrive.
 */

import type { UnhideNotice } from './hidden-mode-logic'

import { useSpeechOutputControlStore } from '@proj-airi/stage-ui/stores/speech-output-control'
import { storeToRefs } from 'pinia'
import { computed, onScopeDispose, shallowRef, watch } from 'vue'

import { FORGOTTEN_PULSE_MS, QUEUE_BADGE_MS } from '../components/orb/orb-state'
import { useJarvisLookStore } from '../stores/settings/jarvis-look'
import { noticeAfterUnhide, silentWhileHidden } from './hidden-mode-logic'
import { setHostHidden } from './poll-guard'

export function useHiddenModeEffects() {
  const look = useJarvisLookStore()
  look.start()
  const output = useSpeechOutputControlStore()
  const { hostSilenced } = storeToRefs(output)

  const silent = computed(() => silentWhileHidden(look.hidden, look.settings))
  watch([() => look.hidden.hidden, () => look.settings.speakWhileHidden], ([hidden, speak]) => {
    setHostHidden(hidden, speak)
  }, { immediate: true })
  watch(silent, (value) => {
    hostSilenced.value = value
  }, { immediate: true })

  const notice = shallowRef<UnhideNotice>()
  const pulse = shallowRef(false)
  let badgeTimer: ReturnType<typeof setTimeout> | undefined
  let pulseTimer: ReturnType<typeof setTimeout> | undefined
  watch(() => look.hidden, (next, previous) => {
    const found: UnhideNotice | undefined = noticeAfterUnhide(previous, next)
    if (!found)
      return
    notice.value = found.queued > 0 ? found : undefined
    pulse.value = found.forgotten
    clearTimeout(badgeTimer)
    clearTimeout(pulseTimer)
    badgeTimer = setTimeout(() => {
      notice.value = undefined
    }, QUEUE_BADGE_MS)
    pulseTimer = setTimeout(() => {
      pulse.value = false
    }, FORGOTTEN_PULSE_MS)
  })

  onScopeDispose(() => {
    clearTimeout(badgeTimer)
    clearTimeout(pulseTimer)
    hostSilenced.value = false
    setHostHidden(false)
  })

  return { notice, pulse }
}
