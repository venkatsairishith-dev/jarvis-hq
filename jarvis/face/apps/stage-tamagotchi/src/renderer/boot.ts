// The start of every renderer window (D78).
//
// index.html used to load the Live2D Cubism core with a blocking <script> tag, so every window fetched and parsed it, including the orb, which
// has no avatar. Now the core is loaded here, and only by a window that draws an avatar; the application starts after it, as it did after the
// tag. The orb's own window (address query `jarvis-look=orb` or `minimal`) starts at once and loads no Live2D code at all.
import { ensureCubismCore, windowNeedsCubismAtStart } from './libs/cubism-core'

async function boot() {
  if (windowNeedsCubismAtStart(globalThis.location?.search)) {
    try {
      await ensureCubismCore()
    }
    catch (error) {
      // the avatar shows its own error surface when it cannot start; the other windows do not need the core
      console.warn('[JARVIS] the Live2D core could not be loaded:', error)
    }
  }
  await import('./main')
}

void boot()
