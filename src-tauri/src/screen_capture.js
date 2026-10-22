(function installNekoTauriScreenCaptureBridge() {
  'use strict';

  if (window.__NEKO_TAURI_SCREEN_CAPTURE_INSTALLED__) return;

  var tauri = window.__TAURI__;
  var invoke = tauri && tauri.core && tauri.core.invoke;
  if (typeof invoke !== 'function') return;

  function getSources(options) {
    var types = options && Array.isArray(options.types)
      ? options.types
      : ['screen', 'window'];
    return invoke('get_screen_capture_sources', { types: types });
  }

  function captureSourceAsDataUrl(sourceId, options) {
    options = options || {};
    return invoke('capture_screen_source', {
      sourceId: sourceId,
      maxWidth: options.maxWidth || 1280,
      quality: options.quality || 80
    });
  }

  window.__NEKO_TAURI_SCREEN_CAPTURE_INSTALLED__ = true;
  window.tauriDesktopCapturer = {
    provider: 'tauri-native',
    nativeFrameCapture: true,
    getCapabilities: function() {
      return invoke('get_screen_capture_capabilities');
    },
    getSources: getSources,
    captureSourceAsDataUrl: captureSourceAsDataUrl
  };
})();
