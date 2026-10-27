import type { JarvisLookSnapshot } from '../../../shared/eventa/jarvis-look'
import type { HiddenState, Look, LookSettings, OrbLayoutRequest, OrbLayoutSnapshot, ShortcutStatus } from '../../../shared/jarvis-look'

import { useElectronEventaContext, useElectronEventaInvoke } from '@proj-airi/electron-vueuse'
import { defineStore } from 'pinia'
import { computed, onScopeDispose, shallowRef } from 'vue'

import {
  electronJarvisHiddenSet,
  electronJarvisLookChanged,
  electronJarvisLookGet,
  electronJarvisLookUpdate,
  electronJarvisOrbDrag,
  electronJarvisOrbNudge,
  electronJarvisOrbRequest,
  electronJarvisOrbReset,
} from '../../../shared/eventa/jarvis-look'
import { DEFAULT_LOOK_SETTINGS, initialLookFromSearch } from '../../../shared/jarvis-look'

const EMPTY_LAYOUT: OrbLayoutSnapshot = { look: 'avatar', size: 'M', window: null, zone: null, pill: null, card: null, cardSide: null, menu: null }
const NO_HIDDEN: HiddenState = { hidden: false, queued: 0, forgotten: false, unhideSeq: 0 }
const NO_SHORTCUTS: ShortcutStatus = { hide: { accelerator: '', ok: true }, reset: { accelerator: '', ok: true } }

/**
 * The look of the face (D78), as the main process keeps it: Orb, Avatar or Minimal bar, the size, the hidden-mode choices, and where everything
 * is inside the window. The main process is the owner of this state (it also owns the window); this store reads it, asks for changes, and hears
 * every change. Nothing here is stored in the page, so two windows can never disagree.
 */
export const useJarvisLookStore = defineStore('jarvis-look', () => {
  const snapshot = shallowRef<JarvisLookSnapshot | undefined>()
  const initialLook = initialLookFromSearch(globalThis.location?.search)

  const settings = computed<LookSettings>(() => snapshot.value?.settings ?? { ...DEFAULT_LOOK_SETTINGS, look: initialLook })
  /** What the page draws now. The start-up look is used until the first answer, so there is no flash of the wrong look. */
  const look = computed<Look>(() => snapshot.value?.settings.look ?? initialLook)
  const layout = computed<OrbLayoutSnapshot>(() => snapshot.value?.layout ?? EMPTY_LAYOUT)
  const hidden = computed<HiddenState>(() => snapshot.value?.hidden ?? NO_HIDDEN)
  const shortcuts = computed<ShortcutStatus>(() => snapshot.value?.shortcuts ?? NO_SHORTCUTS)

  const invokeGet = useElectronEventaInvoke(electronJarvisLookGet)
  const invokeUpdate = useElectronEventaInvoke(electronJarvisLookUpdate)
  const invokeRequest = useElectronEventaInvoke(electronJarvisOrbRequest)
  const invokeDrag = useElectronEventaInvoke(electronJarvisOrbDrag)
  const invokeNudge = useElectronEventaInvoke(electronJarvisOrbNudge)
  const invokeReset = useElectronEventaInvoke(electronJarvisOrbReset)
  const invokeHidden = useElectronEventaInvoke(electronJarvisHiddenSet)
  const context = useElectronEventaContext()

  let started = false
  function start() {
    if (started)
      return
    started = true
    const stop = context.value.on(electronJarvisLookChanged, (event) => {
      if (event?.body)
        snapshot.value = event.body
    })
    onScopeDispose(stop)
    void invokeGet().then((next) => {
      if (next && typeof next === 'object' && 'settings' in next)
        snapshot.value = next as JarvisLookSnapshot
    }).catch(error => console.warn('[JARVIS] the look could not be read:', error))
  }

  /** The harness and the tests give the store a snapshot directly. */
  function setSnapshot(next: JarvisLookSnapshot | undefined) {
    snapshot.value = next
  }

  async function update(patch: Partial<Record<keyof LookSettings, unknown>>) {
    const next = await invokeUpdate(patch)
    if (next && typeof next === 'object' && 'settings' in next)
      snapshot.value = next as JarvisLookSnapshot
  }

  async function request(next: OrbLayoutRequest) {
    const answer = await invokeRequest(next)
    if (answer && typeof answer === 'object' && 'look' in answer && snapshot.value)
      snapshot.value = { ...snapshot.value, layout: answer as OrbLayoutSnapshot }
  }

  return {
    snapshot,
    settings,
    look,
    layout,
    hidden,
    shortcuts,
    start,
    setSnapshot,
    update,
    request,
    drag: (phase: 'press' | 'start' | 'move' | 'end') => invokeDrag({ phase }),
    nudge: (direction: 'left' | 'right' | 'up' | 'down') => invokeNudge({ direction }),
    resetPosition: () => invokeReset(),
    setHidden: (value: boolean) => invokeHidden({ hidden: value }),
  }
})
