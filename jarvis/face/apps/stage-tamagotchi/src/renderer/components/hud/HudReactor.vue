<script setup lang="ts">
import type { HudState } from './hud-model'

/**
 * The arc reactor: one small SVG that shows the state of JARVIS.
 *
 * The state is carried by shape and motion as well as by colour (idle breathes; listening sends rings outward; thinking spins a
 * short arc; speaking pulses with the voice; acting turns a longer arc steadily; offline breaks the ring and goes dark), so it
 * reads without telling red from blue. Every animation moves only transform or opacity of a few small shapes, never a filter.
 */
defineProps<{ state: HudState }>()

// Ten coils around the core, 36 degrees apart.
const coils = Array.from({ length: 10 }, (_, i) => i)
</script>

<template>
  <svg
    class="jhud-reactor"
    viewBox="-32 -32 64 64"
    width="44"
    height="44"
    aria-hidden="true"
    focusable="false"
  >
    <circle class="jhud-ripple" r="29" />
    <circle class="jhud-ripple jhud-ripple-late" r="29" />
    <g class="jhud-ticks">
      <circle class="jhud-ring" r="29" />
      <circle class="jhud-tick-dashes" r="29" />
    </g>
    <g class="jhud-sweep">
      <circle class="jhud-sweep-track" r="23.5" />
      <circle class="jhud-sweep-arc" r="23.5" />
    </g>
    <g class="jhud-coils">
      <g v-for="i in coils" :key="i" :transform="`rotate(${i * 36})`">
        <rect
          class="jhud-coil"
          :style="{ '--i': i }"
          x="-2.3"
          y="-19.5"
          width="4.6"
          height="7"
          rx="1.3"
        />
      </g>
    </g>
    <circle class="jhud-inner" r="11.5" />
    <circle class="jhud-halo" r="9" />
    <circle class="jhud-core" r="6.4" />
    <circle class="jhud-hot" r="2.6" />
  </svg>
</template>
