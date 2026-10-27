import { afterEach, describe, expect, it } from 'vitest'

import { chromiumLockSwitches } from './socket-lock'
import { isAllowedUrl } from './network-lock'
import { isViewerTarget, parseViewerTarget, setViewerReader, viewerConfigPaths } from './vm2-viewer'

const cfg = (o: Record<string, unknown>) => JSON.stringify({ enabled: true, viewer_url: 'http://jarvis-vm2.tail1234.ts.net:6080/vnc.html?autoconnect=1&resize=scale', ...o })

afterEach(() => setViewerReader(null))

describe('vm2 viewer address', () => {
  it('reads the exact host and port from an enabled config', () => {
    expect(parseViewerTarget(cfg({}))).toEqual({ host: 'jarvis-vm2.tail1234.ts.net', port: 6080 })
    expect(parseViewerTarget(cfg({ viewer_url: 'https://100.101.102.103/vnc.html' }))).toEqual({ host: '100.101.102.103', port: 443 })
    expect(parseViewerTarget(`\uFEFF${cfg({})}`)).not.toBeNull()
  })

  it('refuses anything that is not a plain Tailscale or local address without secrets', () => {
    for (const bad of [
      { enabled: false },
      { viewer_url: '' },
      { viewer_url: 'http://example.com:6080/vnc.html' },
      { viewer_url: 'http://192.168.1.5:6080/' },
      { viewer_url: 'http://100.128.0.1:6080/' },
      { viewer_url: 'http://user:pw@100.101.102.103:6080/' },
      { viewer_url: 'http://100.101.102.103:6080/vnc.html?password=abc' },
      { viewer_url: 'http://100.101.102.103:6080/vnc.html?token=abc' },
      { viewer_url: 'http://100.101.102.103:6080/vnc.html#key=abc' },
      { viewer_url: 'ftp://100.101.102.103/' },
      { viewer_url: 'not a url' },
    ])
      expect(parseViewerTarget(cfg(bad)), JSON.stringify(bad)).toBeNull()
    expect(parseViewerTarget('{broken')).toBeNull()
    expect(parseViewerTarget(null)).toBeNull()
  })

  it('opens exactly that host and port in the network lock, and nothing next to it', () => {
    setViewerReader(() => cfg({}))
    expect(isAllowedUrl('http://jarvis-vm2.tail1234.ts.net:6080/vnc.html', [])).toBe(true)
    expect(isAllowedUrl('ws://jarvis-vm2.tail1234.ts.net:6080/websockify', [])).toBe(true)
    expect(isAllowedUrl('http://jarvis-vm2.tail1234.ts.net:6081/', [])).toBe(false)
    expect(isAllowedUrl('http://other.tail1234.ts.net:6080/', [])).toBe(false)
    expect(isAllowedUrl('http://jarvis-vm2.tail1234.ts.net.evil.example:6080/', [])).toBe(false)
    expect(isViewerTarget('jarvis-vm2.tail1234.ts.net', 6080)).toBe(true)
    expect(isViewerTarget('jarvis-vm2.tail1234.ts.net', undefined)).toBe(false)
    expect(chromiumLockSwitches([])[0][1]).toContain('EXCLUDE jarvis-vm2.tail1234.ts.net')
  })

  it('adds nothing when the config is missing, disabled or invalid', () => {
    setViewerReader(() => null)
    expect(isAllowedUrl('http://jarvis-vm2.tail1234.ts.net:6080/', [])).toBe(false)
    setViewerReader(() => cfg({ enabled: false }))
    expect(isAllowedUrl('http://jarvis-vm2.tail1234.ts.net:6080/', [])).toBe(false)
    setViewerReader(() => cfg({ viewer_url: 'http://jarvis-vm2.tail1234.ts.net:6080/?password=x' }))
    expect(isAllowedUrl('http://jarvis-vm2.tail1234.ts.net:6080/', [])).toBe(false)
    expect(chromiumLockSwitches([])[0][1]).not.toContain('ts.net')
  })

  it('looks for the file where the owner points at it', () => {
    expect(viewerConfigPaths({ JARVIS_VM2_CONFIG: 'X:\\v.json', JARVIS_HQ: 'Y:\\hq' })).toEqual(['X:\\v.json', expect.stringContaining('vm2.json'), 'D:\\jarvis\\hq\\jarvis\\config\\vm2.json'])
  })
})
