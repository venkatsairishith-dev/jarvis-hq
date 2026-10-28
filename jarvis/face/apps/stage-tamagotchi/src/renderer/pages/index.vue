<script setup lang="ts">
/**
 * The main window (D78). What it shows depends on the owner's "Look":
 *
 *  - Orb (the default of a new profile) and Minimal bar: `OrbShell`, drawn by CSS alone. The avatar page below is never loaded in these looks:
 *    no stage, no Live2D / VRM / MMD scene, no WebGL, no model files, no frame loop.
 *  - Avatar (what an existing profile keeps until the owner switches): the page this window always had, `AvatarStage`, loaded on demand.
 *
 * The switch is live: the main process sends the new look and this page swaps without a restart. Leaving the avatar unmounts its stage, which
 * releases its WebGL context and its timers; coming back loads it again.
 */
import { defineAsyncComponent } from 'vue'

import OrbShell from '../components/orb/OrbShell.vue'

import { useHiddenModeEffects } from '../composables/use-hidden-mode-effects'
import { ensureCubismCore } from '../libs/cubism-core'
import { useJarvisLookStore } from '../stores/settings/jarvis-look'

const look = useJarvisLookStore()
look.start()
// Hidden mode, for every look: silence, polls, the count after an unhide. (The orb draws the count; the avatar gets a toast from App.)
useHiddenModeEffects()

// The avatar page and everything it pulls in (the stage, the scenes, the Live2D core) load only when this look is shown.
const AvatarStage = defineAsyncComponent(async () => {
  await ensureCubismCore()
  return import('../components/avatar/AvatarStage.vue')
})
</script>

<template>
  <AvatarStage v-if="look.look === 'avatar'" />
  <OrbShell v-else />
</template>

<route lang="yaml">
meta:
  layout: stage
</route>
