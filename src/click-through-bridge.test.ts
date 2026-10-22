// @vitest-environment jsdom

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

type ClickThroughTestApi = {
  handleRadarPoint: (x: number, y: number) => void;
  isInteractiveDomPoint: (x: number, y: number) => boolean;
  isPointOverModel: (x: number, y: number) => boolean;
};

const bridgeSource = readFileSync(
  resolve(process.cwd(), 'src-tauri/src/click_through.js'),
  'utf8',
);

function installBridge() {
  let radarHandler: ((event: { payload: [number, number] }) => void) | null = null;
  const invoke = vi.fn(() => Promise.resolve());
  const listen = vi.fn(
    (_eventName: string, handler: (event: { payload: [number, number] }) => void) => {
      radarHandler = handler;
      return Promise.resolve(() => {});
    },
  );

  Object.assign(window, {
    __TAURI__: {
      core: { invoke },
      event: { listen },
    },
  });

  window.eval(bridgeSource);

  return {
    api: (window as unknown as { __NEKO_TAURI_CLICK_THROUGH_TEST__: ClickThroughTestApi })
      .__NEKO_TAURI_CLICK_THROUGH_TEST__,
    invoke,
    emitRadar(x: number, y: number) {
      radarHandler?.({ payload: [x, y] });
    },
  };
}

describe('Tauri click-through bridge', () => {
  beforeEach(() => {
    document.body.innerHTML = '';
    delete (window as unknown as Record<string, unknown>).__NEKO_TAURI_CLICK_THROUGH_INSTALLED__;
    delete (window as unknown as Record<string, unknown>).__NEKO_TAURI_CLICK_THROUGH_TEST__;
    delete (window as unknown as Record<string, unknown>).__TAURI__;
    delete (window as unknown as Record<string, unknown>).live2dManager;

    Object.defineProperty(document, 'elementsFromPoint', {
      configurable: true,
      value: vi.fn(() => [document.body]),
    });
  });

  it('makes transparent background click-through', () => {
    const { emitRadar, invoke } = installBridge();

    emitRadar(40, 50);

    expect(invoke).toHaveBeenCalledWith('set_click_through', { ignore: true });
  });

  it('restores mouse input over the visible character bounds', () => {
    const container = document.createElement('div');
    container.id = 'live2d-container';
    document.body.appendChild(container);
    Object.assign(window, {
      live2dManager: {
        getModelScreenBounds: () => ({
          left: 100,
          top: 80,
          right: 300,
          bottom: 480,
        }),
      },
    });
    const { emitRadar, invoke } = installBridge();

    emitRadar(20, 20);
    emitRadar(180, 220);

    expect(invoke).toHaveBeenNthCalledWith(1, 'set_click_through', { ignore: true });
    expect(invoke).toHaveBeenNthCalledWith(2, 'set_click_through', { ignore: false });
  });

  it('restores mouse input over ordinary UI controls', () => {
    const button = document.createElement('button');
    button.textContent = '设置';
    document.body.appendChild(button);
    vi.mocked(document.elementsFromPoint).mockReturnValue([button, document.body]);
    const { emitRadar, invoke } = installBridge();

    emitRadar(20, 20);

    expect(invoke).not.toHaveBeenCalled();

    vi.mocked(document.elementsFromPoint).mockReturnValue([document.body]);
    emitRadar(500, 500);
    vi.mocked(document.elementsFromPoint).mockReturnValue([button, document.body]);
    emitRadar(20, 20);

    expect(invoke).toHaveBeenNthCalledWith(1, 'set_click_through', { ignore: true });
    expect(invoke).toHaveBeenNthCalledWith(2, 'set_click_through', { ignore: false });
  });
});
