<script setup lang="ts">
/**
 * D78: the voice OUTPUT of the main window when there is no avatar. In the avatar look the Stage component does this (it also draws the
 * model); the orb needs the same speech without any of the drawing, so this is the speech part of the Stage, with the avatar parts left
 * out: no Live2D / VRM / MMD scene, no lip sync model, no expressions, no motion queue.
 *
 *  - speech is produced for the replies that speak (the same hooks and the same `voice.connectOutput` as the Stage),
 *  - the playback state is published on the speech bus (the stop buttons and the chat read it),
 *  - the character's "mouth" level is measured from the audio at 15 times a second, and only while speaking, so the orb's ring can follow the voice,
 *  - the owner's mute and the hidden mode's silence both stop speech before it is made.
 *
 * Mounted only in the orb and bar looks. Imports no avatar code (orb-imports.test.ts keeps it so).
 */
import { defineInvokeHandler } from '@moeru/eventa'
import { sleep } from '@moeru/std'
import { BrowserPlayback } from '@proj-airi/audio/browser'
import { Playback } from '@proj-airi/pipelines-audio'
import { getSpeechBusContext, speechOutputGetPlaybackState, speechOutputPlaybackStateChangedEvent } from '@proj-airi/stage-ui/services/speech/bus'
import { useLlmStreamingControlStore } from '@proj-airi/stage-ui/stores/ai/chat-llm/streaming-control'
import { useAudioContext, useSpeakingStore } from '@proj-airi/stage-ui/stores/audio'
import { useChatStore } from '@proj-airi/stage-ui/stores/chat'
import { useChatSessionStore } from '@proj-airi/stage-ui/stores/chat/session-store'
import { useAiriCardStore } from '@proj-airi/stage-ui/stores/modules/airi-card'
import { useSpeechStore } from '@proj-airi/stage-ui/stores/modules/speech'
import { useSpeechOutputControlStore } from '@proj-airi/stage-ui/stores/speech-output-control'
import { useVoiceStore } from '@proj-airi/stage-ui/stores/voice'
import { storeToRefs } from 'pinia'
import { onUnmounted, shallowRef, watch } from 'vue'

import { ORB_LEVEL_MS } from './orb-state'

const { audioContext } = useAudioContext()
const { mouthOpenSize, nowSpeaking } = storeToRefs(useSpeakingStore())
const voice = useVoiceStore()
const chatSession = useChatSessionStore()
const cards = useAiriCardStore()
const speechStore = useSpeechStore()
const streamingControl = useLlmStreamingControlStore()
const { speechSilent, speechVolume } = storeToRefs(useSpeechOutputControlStore())
const { onBeforeMessageComposed, onTokenLiteral, onTokenSpecial, onStreamEnd, onAssistantResponseEnd } = useChatStore()
const cleanups: Array<() => void> = []

/** `voicing` of `SpeechOutputPlaybackState` in the speech bus. */
const speechVoicing = shallowRef(false)
cleanups.push(defineInvokeHandler(
  getSpeechBusContext(),
  speechOutputGetPlaybackState,
  () => ({ speaking: nowSpeaking.value, voicing: speechVoicing.value }),
))
watch([nowSpeaking, speechVoicing], ([speaking, voicing]) => {
  getSpeechBusContext().emit(speechOutputPlaybackStateChangedEvent, { speaking, voicing })
}, { immediate: true })

// the analysis tap reads before the owner's volume, like the Stage; the destination takes the volume
const speechAnalysis = audioContext.createGain()
const speechDestination = audioContext.createGain()
speechAnalysis.connect(speechDestination)
speechDestination.connect(audioContext.destination)
watch(speechVolume, (volume) => {
  speechDestination.gain.setTargetAtTime(volume, audioContext.currentTime, 0.05)
}, { immediate: true })
const playback = new Playback(new BrowserPlayback(audioContext, { destination: speechAnalysis }))
let playingCount = 0

function resetSpeakingState() {
  nowSpeaking.value = false
  mouthOpenSize.value = 0
}

watch(() => voice.activeTurns, (turns) => {
  if (turns.length === 0)
    speechVoicing.value = false
})

cleanups.push(voice.connectOutput((turn) => {
  const output = {
    playback,
    onSpecial: (special: string) => void streamingControl.dispatchWith(special, { turnId: turn.turnId }),
    onPlaybackStart: () => {
      playingCount += 1
      nowSpeaking.value = true
      speechVoicing.value = true
    },
    onPlaybackEnd: () => {
      playingCount -= 1
      if (playingCount === 0)
        resetSpeakingState()
    },
  }
  if (speechSilent.value)
    return { ...output, synthesize: async () => null }
  try {
    const characterId = chatSession.sessionMetas[turn.sessionId]?.characterId
    if (!characterId)
      throw new Error('The response session has no character')
    return speechStore.createOutput(turn, cards.getModules(characterId).speech, output, audioContext)
  }
  catch (error) {
    // Text replies remain available when optional speech output is not configured.
    console.error('Speech output is unavailable', error)
    return { ...output, synthesize: async () => null }
  }
}))

// ---- the level of the voice: sampled from the audio at 15 Hz, and only while speaking (no timer, no work, when it is quiet)
const analyser = audioContext.createAnalyser()
analyser.fftSize = 512
speechAnalysis.connect(analyser)
const samples = new Uint8Array(analyser.fftSize)
let levelTimer: ReturnType<typeof setInterval> | undefined
function sampleLevel() {
  analyser.getByteTimeDomainData(samples)
  let sum = 0
  for (const value of samples) {
    const centred = (value - 128) / 128
    sum += centred * centred
  }
  // RMS of speech is small; a gain of 4 brings it to a 0..1 range that fills the ring calmly
  mouthOpenSize.value = Math.min(1, Math.sqrt(sum / samples.length) * 4)
}
watch(nowSpeaking, (speaking) => {
  if (levelTimer) {
    clearInterval(levelTimer)
    levelTimer = undefined
  }
  if (speaking)
    levelTimer = setInterval(sampleLevel, ORB_LEVEL_MS)
  else
    mouthOpenSize.value = 0
}, { immediate: true })

// speech that is being made stops at once when it becomes silent (muted, or hidden)
watch(speechSilent, (silent) => {
  if (silent) {
    for (const turn of voice.activeTurns)
      voice.getSpeech(turn)?.cancel('Speech muted')
  }
})

// "delay" signals of a reply wait here, like in the Stage; the expressions and motions of the avatar do not exist in this look
cleanups.push(streamingControl.onSignal(async (signal) => {
  if (signal.type === 'delay')
    await sleep(signal.seconds * 1000)
}))

// Only a reply that speaks drives speech. Background work and scene replies never speak here.
function holdsVoice(context: { speaks?: boolean }) {
  return context.speaks ?? false
}
cleanups.push(onBeforeMessageComposed(async (_message, context) => {
  if (holdsVoice(context))
    voice.startResponse(context)
}))
cleanups.push(onTokenLiteral(async (literal, context) => {
  if (holdsVoice(context) && !speechSilent.value)
    await voice.getSpeech(context)?.write(literal)
}))
cleanups.push(onTokenSpecial(async (special, context) => {
  if (!holdsVoice(context))
    return
  if (speechSilent.value)
    await streamingControl.dispatchWith(special, { turnId: context.turnId })
  else
    voice.getSpeech(context)?.special(special)
}))
cleanups.push(onStreamEnd(async (context) => {
  void voice.finishResponse(context).catch(error => console.error('Speech response completion failed', error))
}))
cleanups.push(onAssistantResponseEnd(async (_message, context) => {
  if (!holdsVoice(context))
    return
  void voice.finishResponse(context).catch(error => console.error('Speech response completion failed', error))
}))

// the audio context starts on the first interaction (browser rule)
function resume() {
  audioContext.resume().catch(() => {})
}
window.addEventListener('pointerdown', resume, { once: true, passive: true })
window.addEventListener('keydown', resume, { once: true, passive: true })

onUnmounted(() => {
  if (levelTimer)
    clearInterval(levelTimer)
  window.removeEventListener('pointerdown', resume)
  window.removeEventListener('keydown', resume)
  cleanups.forEach(dispose => dispose?.())
  resetSpeakingState()
  speechAnalysis.disconnect()
  speechDestination.disconnect()
  analyser.disconnect()
})
</script>

<template>
  <span hidden />
</template>
