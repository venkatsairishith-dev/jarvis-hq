/**
 * The voice host duties of the main window, the same for every look (avatar, orb, minimal bar).
 *
 * The main window owns the voice host: spoken turns start in it and use the tools that the chat window composer grants; a microphone
 * left on starts listening a few seconds after the window opens (D57); push-to-talk is registered here; the voice inlay is opened when
 * a capture starts; a voice error is shown as a toast; the first start opens onboarding.
 *
 * It lived in the avatar page. The orb has no avatar, but the voice must work exactly the same, so the lines moved here unchanged.
 */

import { useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { useVoiceListening } from '@proj-airi/stage-ui/composables/voice-input-mode'
import { useOnboardingStore } from '@proj-airi/stage-ui/stores/onboarding'
import { useVoiceStore } from '@proj-airi/stage-ui/stores/voice'
import { onMounted, onScopeDispose, watch } from 'vue'
import { toast } from 'vue-sonner'

import { electronOpenInlay, electronOpenOnboarding } from '../../shared/eventa'
import { useToolSelectionStore } from '../stores/tools'
import { usePushToTalkShortcut } from './use-push-to-talk-shortcut'

export function useMainWindowVoice() {
  const onboardingStore = useOnboardingStore()
  const openOnboarding = useElectronEventaInvoke(electronOpenOnboarding)
  const voice = useVoiceStore()
  const toolSelection = useToolSelectionStore()
  // Spoken turns start in this window. They use the tools that the chat window composer grants.
  onScopeDispose(voice.connectTools(() => toolSelection.requestTools))
  const openInlay = useElectronEventaInvoke(electronOpenInlay)
  // D57: a microphone left on starts listening 8 s after the window opens, not inside the avatar's model load (the voice activity model
  // and the wake word spotter are ONNX/WASM Workers).
  useVoiceListening({ startDelayMs: 8_000 })
  usePushToTalkShortcut()
  // The inlay stays shown from startup and draws a capsule when any capture starts.
  // This call shows it again after the user hid it. A failed begin never reaches `capturing`.
  watch(
    () => voice.state?.phase === 'capturing',
    (capturing) => {
      if (capturing)
        void openInlay()
    },
  )
  watch(() => voice.error, (error) => {
    if (error)
      toast.error(error)
  })
  // A draft that the host is sending needs no inlay. A failed send clears `sending`, so the draft opens the inlay again.
  watch(
    () => voice.drafts.filter(draft => !draft.sending).map(draft => `${draft.id}:${draft.rawText}`).join('\0'),
    (speechDrafts) => {
      if (speechDrafts)
        void openInlay()
    },
  )
  onMounted(() => {
    if (onboardingStore.needsOnboarding)
      openOnboarding()
  })
}
