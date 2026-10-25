import { mkdirSync } from 'node:fs'
import { win32 } from 'node:path'
import { platform as processPlatform } from 'node:process'

/**
 * The face keeps everything it writes on the D: drive (owner, 2026-10-25: "for storage use only the D: drive").
 *
 * Electron writes its profile (settings, caches, local storage, GPU shader cache, logs, crash dumps) under `%APPDATA%\JARVIS` and
 * temporary files under `%TEMP%`, both on C:, which is small. On Windows this moves `userData`, `sessionData`, `temp`, `logs` and
 * `crashDumps` to `D:\jarvis\data\face\...` and `D:\jarvis\tmp` before the app is ready. It is the same list as
 * `storage_env()` in jarvis/supervisor/budget.py (the start scripts set `APP_USER_DATA_PATH` and `TEMP` to these folders).
 *
 * - Only on Windows and only when the D: drive exists; elsewhere (Linux CI, a developer's Mac) nothing changes.
 * - `JARVIS_FACE_STORAGE=off` turns it off; `JARVIS_STORAGE_ROOT` moves the root (it must be on D: too).
 * - A path from the environment (`APP_USER_DATA_PATH`, `TEMP`) is used only when it is on D:; anything else is ignored with a warning.
 * - It touches no network setting: the network lock (network-lock.ts, socket-lock.ts) and the CSP are applied by their own modules.
 */

export const DEFAULT_ROOT = 'D:\\jarvis'

export interface JarvisPaths {
  userData: string
  sessionData: string
  temp: string
  logs: string
  crashDumps: string
}

export interface StorageEnv {
  [key: string]: string | undefined
}

/** True for an absolute path on drive D: (either slash). Relative, UNC and other-drive paths, and `..` escapes, are false. */
export function isOnD(path: string | undefined): boolean {
  if (!path)
    return false
  const p = path.trim().replaceAll('/', '\\').replace(/^\\\\\?\\/, '')
  if (!/^[A-Z]:\\/i.test(p))
    return false
  return /^D:\\/i.test(win32.normalize(p))
}

export interface ResolveOptions {
  platform?: string
  env?: StorageEnv
  driveExists?: (drive: string) => boolean
}

export interface Resolved {
  paths: JarvisPaths | null
  warnings: string[]
}

export function resolveJarvisPaths(options: ResolveOptions = {}): Resolved {
  const platform = options.platform ?? processPlatform
  const env = options.env ?? {}
  const warnings: string[] = []
  if (platform !== 'win32' || env.JARVIS_FACE_STORAGE?.trim().toLowerCase() === 'off')
    return { paths: null, warnings }

  const root = (env.JARVIS_STORAGE_ROOT?.trim() || DEFAULT_ROOT).replace(/[\\/]+$/, '')
  if (!isOnD(root)) {
    warnings.push(`JARVIS_STORAGE_ROOT is not on the D: drive (${root}); storage lock not applied`)
    return { paths: null, warnings }
  }
  if (options.driveExists && !options.driveExists('D:\\')) {
    warnings.push('the D: drive was not found; storage lock not applied')
    return { paths: null, warnings }
  }

  const pick = (name: string, value: string | undefined, fallback: string): string => {
    const v = value?.trim()
    if (!v)
      return fallback
    if (isOnD(v))
      return win32.normalize(v)
    warnings.push(`${name} is not on the D: drive (${v}); using ${fallback}`)
    return fallback
  }

  const userData = pick('APP_USER_DATA_PATH', env.APP_USER_DATA_PATH, `${root}\\data\\face\\user-data`)
  return {
    paths: {
      userData,
      sessionData: userData,
      temp: pick('TEMP', env.TEMP, `${root}\\tmp`),
      logs: `${userData}\\logs`,
      crashDumps: `${userData}\\crash-dumps`,
    },
    warnings,
  }
}

export interface PathSetter {
  setPath: (name: 'userData' | 'sessionData' | 'temp' | 'logs' | 'crashDumps', path: string) => void
}

/**
 * Call once, first thing in the main process, before anything reads `app.getPath(...)`.
 * Returns the paths it set, or null when it did nothing (not Windows, switched off, no D:).
 */
export function applyStorageLock(
  app: PathSetter,
  env: StorageEnv,
  warn: (message: string) => void = () => {},
  options: { platform?: string, driveExists?: (drive: string) => boolean, mkdir?: (path: string) => void } = {},
): JarvisPaths | null {
  const { paths, warnings } = resolveJarvisPaths({ platform: options.platform, env, driveExists: options.driveExists })
  for (const w of warnings)
    warn(`[jarvis:storage-lock] ${w}`)
  if (!paths)
    return null
  const mkdir = options.mkdir ?? ((p: string) => { mkdirSync(p, { recursive: true }) })
  for (const [name, path] of Object.entries(paths) as [keyof JarvisPaths, string][]) {
    try {
      mkdir(path)
      app.setPath(name, path)
    }
    catch (error) {
      warn(`[jarvis:storage-lock] could not set ${name} to ${path}: ${String(error)}`)
    }
  }
  // Child processes and libraries that read the variables (node's os.tmpdir(), the MCP client's helpers) follow the same folders.
  env.TEMP = paths.temp
  env.TMP = paths.temp
  env.TMPDIR = paths.temp
  return paths
}
