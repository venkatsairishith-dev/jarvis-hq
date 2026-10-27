// A stand-in for @proj-airi/electron-vueuse: the renderer's bridge to Electron's main process. In the render harness there is no main
// process, so every invoke answers "unsupported" (what the app's own browser tests do) and every event never fires. Nothing here touches
// the network. Add an export only when the build says a component imports one.
import { computed, ref } from 'vue'

const noop = () => {}
const context = ref({ on: () => noop, emit: noop, off: noop })

export function useElectronEventaInvoke() {
  return async () => ({ state: 'unsupported', reason: 'render harness' })
}
export function getElectronEventaContext() {
  return context.value
}
export function useElectronEventaContext() {
  return context
}
export function resetElectronEventaContextForTesting() {}

export function useElectronMouse() {
  return { x: ref(0), y: ref(0), tracking: ref(false) }
}
export function useElectronMouseEventTarget() {
  return { addEventListener: noop, removeEventListener: noop }
}
export function useElectronMouseAroundWindowBorder() {
  return { nearTop: ref(false), nearBottom: ref(false), nearLeft: ref(false), nearRight: ref(false) }
}
export function useElectronMouseInElement() {
  return { isOutside: ref(true), elementX: ref(0), elementY: ref(0) }
}
export function useElectronMouseInWindow() {
  return { isOutside: ref(true), x: ref(0), y: ref(0) }
}
export function useElectronRelativeMouse() {
  return { x: ref(0), y: ref(0) }
}
export function useElectronWindowBounds() {
  return { x: ref(0), y: ref(0), width: ref(window.innerWidth), height: ref(window.innerHeight), bounds: computed(() => ({ x: 0, y: 0, width: window.innerWidth, height: window.innerHeight })) }
}
export function useElectronAllDisplays() {
  return { allDisplays: ref([]), displays: ref([]) }
}
export function useElectronWindowResize() {
  return { resize: noop }
}
export function useElectronAutoUpdater() {
  return { state: ref({ status: 'idle' }), check: noop, download: noop, install: noop }
}
