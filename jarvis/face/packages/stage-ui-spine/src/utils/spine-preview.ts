/**
 * JARVIS: no preview for Spine avatars. Rendering one needs the Spine Runtimes, which JARVIS does not ship (they need a
 * Spine Editor licence).
 */
export async function loadSpineModelPreview(_file: File): Promise<string | undefined> {
  return undefined
}
