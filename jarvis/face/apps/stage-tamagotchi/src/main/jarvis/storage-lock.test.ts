import { describe, expect, it } from 'vitest'

import { applyStorageLock, isOnD, resolveJarvisPaths } from './storage-lock'

describe('jarvis storage lock', () => {
  it('knows what is on D:', () => {
    for (const p of ['D:\\jarvis\\tmp', 'd:/jarvis/tmp', 'D:\\', '\\\\?\\D:\\jarvis', 'D:\\jarvis\\a\\..\\b'])
      expect(isOnD(p), p).toBe(true)
    for (const p of ['C:\\Users\\x\\AppData\\Roaming\\JARVIS', 'E:\\jarvis', '\\\\server\\share\\jarvis', 'jarvis\\tmp', '.\\tmp', '', undefined, 'D:jarvis', 'C:\\x\\D:\\jarvis'])
      expect(isOnD(p), String(p)).toBe(false)
  })

  it('moves userData, sessionData, temp, logs and crash dumps to D: on Windows', () => {
    const { paths, warnings } = resolveJarvisPaths({ platform: 'win32', env: {}, driveExists: () => true })
    expect(warnings).toEqual([])
    expect(paths).toEqual({
      userData: 'D:\\jarvis\\data\\face\\user-data',
      sessionData: 'D:\\jarvis\\data\\face\\user-data',
      temp: 'D:\\jarvis\\tmp',
      logs: 'D:\\jarvis\\data\\face\\user-data\\logs',
      crashDumps: 'D:\\jarvis\\data\\face\\user-data\\crash-dumps',
    })
  })

  it('does nothing off Windows, when switched off, or without a D: drive', () => {
    expect(resolveJarvisPaths({ platform: 'linux', env: {} }).paths).toBeNull()
    expect(resolveJarvisPaths({ platform: 'darwin', env: {} }).paths).toBeNull()
    expect(resolveJarvisPaths({ platform: 'win32', env: { JARVIS_FACE_STORAGE: 'off' } }).paths).toBeNull()
    const noD = resolveJarvisPaths({ platform: 'win32', env: {}, driveExists: () => false })
    expect(noD.paths).toBeNull()
    expect(noD.warnings[0]).toContain('D: drive was not found')
  })

  it('uses environment paths only when they are on D:', () => {
    const ok = resolveJarvisPaths({ platform: 'win32', env: { APP_USER_DATA_PATH: 'D:/x/profile', TEMP: 'D:\\scratch' } })
    expect(ok.paths?.userData).toBe('D:\\x\\profile')
    expect(ok.paths?.temp).toBe('D:\\scratch')
    const bad = resolveJarvisPaths({ platform: 'win32', env: { APP_USER_DATA_PATH: 'C:\\Users\\me\\AppData\\Roaming\\JARVIS', TEMP: 'C:\\Temp' } })
    expect(bad.paths?.userData).toBe('D:\\jarvis\\data\\face\\user-data')
    expect(bad.paths?.temp).toBe('D:\\jarvis\\tmp')
    expect(bad.warnings).toHaveLength(2)
  })

  it('refuses a storage root that is not on D:', () => {
    const r = resolveJarvisPaths({ platform: 'win32', env: { JARVIS_STORAGE_ROOT: 'C:\\jarvis' } })
    expect(r.paths).toBeNull()
    expect(r.warnings[0]).toContain('not on the D: drive')
  })

  it('applies the paths to the app and the process environment', () => {
    const set: Record<string, string> = {}
    const made: string[] = []
    const env: Record<string, string | undefined> = { TEMP: 'C:\\Users\\me\\AppData\\Local\\Temp' }
    const warnings: string[] = []
    const paths = applyStorageLock({ setPath: (n, p) => { set[n] = p } }, env, m => warnings.push(m), { platform: 'win32', driveExists: () => true, mkdir: p => made.push(p) })
    expect(paths?.userData).toBe('D:\\jarvis\\data\\face\\user-data')
    expect(Object.keys(set).sort()).toEqual(['crashDumps', 'logs', 'sessionData', 'temp', 'userData'])
    expect(Object.values(set).every(p => p.startsWith('D:\\'))).toBe(true)
    expect(made).toHaveLength(5)
    expect(env.TEMP).toBe('D:\\jarvis\\tmp')
    expect(env.TMP).toBe('D:\\jarvis\\tmp')
    expect(warnings.join('\n')).toContain('TEMP is not on the D: drive')
  })

  it('leaves the app alone when it does nothing', () => {
    const set: string[] = []
    expect(applyStorageLock({ setPath: n => set.push(n) }, {}, () => {}, { platform: 'linux' })).toBeNull()
    expect(set).toEqual([])
  })
})
