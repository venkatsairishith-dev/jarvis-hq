// @vitest-environment jsdom

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

type CaptureProvider = {
  provider: string;
  nativeFrameCapture: boolean;
  getSources: (options?: { types?: string[] }) => Promise<unknown>;
  captureSourceAsDataUrl: (
    sourceId: string,
    options?: { maxWidth?: number; quality?: number },
  ) => Promise<unknown>;
};

const tauriBridgeSource = readFileSync(
  resolve(process.cwd(), 'src-tauri/src/screen_capture.js'),
  'utf8',
);

function windowRecord() {
  return window as unknown as Record<string, unknown>;
}

describe('Tauri screen capture bridge', () => {
  beforeEach(() => {
    const target = windowRecord();
    delete target.__NEKO_TAURI_SCREEN_CAPTURE_INSTALLED__;
    delete target.__TAURI__;
    delete target.tauriDesktopCapturer;
    delete target.electronDesktopCapturer;
    delete target.getDesktopCaptureProvider;
  });

  it('maps the shared provider API to Tauri commands', async () => {
    const invoke = vi.fn(() => Promise.resolve([]));
    windowRecord().__TAURI__ = { core: { invoke } };

    window.eval(tauriBridgeSource);
    const provider = windowRecord().tauriDesktopCapturer as CaptureProvider;

    expect(provider.provider).toBe('tauri-native');
    expect(provider.nativeFrameCapture).toBe(true);

    await provider.getSources({ types: ['screen'] });
    expect(invoke).toHaveBeenCalledWith('get_screen_capture_sources', {
      types: ['screen'],
    });

    await provider.captureSourceAsDataUrl('screen:42', {
      maxWidth: 960,
      quality: 75,
    });
    expect(invoke).toHaveBeenCalledWith('capture_screen_source', {
      sourceId: 'screen:42',
      maxWidth: 960,
      quality: 75,
    });
  });
});
