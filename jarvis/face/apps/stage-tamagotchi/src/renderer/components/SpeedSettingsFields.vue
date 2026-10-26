<script setup lang="ts">
import type { SpeedSwitch } from '@proj-airi/stage-ui/stores/jarvis-speed'

import { useJarvisSpeedStore } from '@proj-airi/stage-ui/stores/jarvis-speed'
import { FieldSelect } from '@proj-airi/ui'
import { storeToRefs } from 'pinia'

/**
 * JARVIS D60: the three speed switches in Settings > System > General. Automatic follows the environment (JARVIS_FACE_DIRECT_TOOLS,
 * JARVIS_FACE_QUICK_ACK, JARVIS_FACE_QUICK_COMMAND; each is on unless set to 0); On and Off win over it. Kept in localStorage like the HUD setting.
 */
const { directToolsSetting, quickAckSetting, quickCommandSetting } = storeToRefs(useJarvisSpeedStore())

const options: { label: string, value: SpeedSwitch, description: string }[] = [
  { label: 'Automatic', value: 'auto', description: 'Follow the environment (on unless it says 0).' },
  { label: 'On', value: 'on', description: 'Always on.' },
  { label: 'Off', value: 'off', description: 'Always off.' },
]
</script>

<template>
  <FieldSelect
    v-model="directToolsSetting"
    label="Direct tools"
    description="The chat model gets JARVIS's real tools in its first request, so it calls them at once instead of listing them first (one model round fewer)."
    :options="options"
  />
  <FieldSelect
    v-model="quickAckSetting"
    label="Instant acknowledgement"
    description="A PC, screen or document message shows 'On it' and the step under way at once, before any model answers."
    :options="options"
  />
  <FieldSelect
    v-model="quickCommandSetting"
    label="Plain commands without a model"
    description="A message that is one plain command (open notepad, pause, volume up, what time is it) is run by rules at once, with no chat model request."
    :options="options"
  />
</template>
