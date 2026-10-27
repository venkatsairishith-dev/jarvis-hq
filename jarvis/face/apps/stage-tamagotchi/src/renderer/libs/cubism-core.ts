/**
 * Loads the Live2D Cubism core on demand (D78).
 *
 * The core (`live2dcubismcore.min.js`, Live2D's proprietary runtime, downloaded at build time and checked against `downloads.lock.json`) used to
 * be a `<script>` in index.html, so every window loaded it. The orb has no avatar, so its window must not load it: it is added to the page
 * the first time an avatar is about to be drawn. The script is the same file from the same place (the content policy allows `'self'` only),
 * and a window that never draws an avatar never fetches it.
 */

/** Relative to the page, like the `<script src="/assets/...">` tag was after Vite rewrote it (the packaged app loads index.html from a file: address, base './'). */
export const CUBISM_CORE_SRC = 'assets/js/CubismSdkForWeb-5-r.3/Core/live2dcubismcore.min.js'

let loading: Promise<void> | undefined

export function cubismCoreLoaded(scope: { Live2DCubismCore?: unknown } = globalThis as { Live2DCubismCore?: unknown }): boolean {
  return scope.Live2DCubismCore !== undefined
}

/** Resolves when the core is on the page. Safe to call many times; one script is added. */
export function ensureCubismCore(doc: Document | undefined = globalThis.document): Promise<void> {
  if (cubismCoreLoaded())
    return Promise.resolve()
  if (!doc)
    return Promise.reject(new Error('There is no document to load the Live2D core into'))
  loading ??= new Promise<void>((resolve, reject) => {
    const script = doc.createElement('script')
    script.src = new URL(CUBISM_CORE_SRC, doc.baseURI).href
    script.async = true
    script.onload = () => resolve()
    script.onerror = () => {
      loading = undefined
      script.remove()
      reject(new Error('The Live2D core could not be loaded'))
    }
    doc.head.append(script)
  })
  return loading
}

/** The windows that draw an avatar (everything but the orb's own window) load the core before their page starts, as the old script tag did. */
export function windowNeedsCubismAtStart(search: string | undefined): boolean {
  try {
    const look = new URLSearchParams(search ?? '').get('jarvis-look')
    return look !== 'orb' && look !== 'minimal'
  }
  catch {
    return true
  }
}
