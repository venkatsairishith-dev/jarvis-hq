(function installNekoTauriClickThroughBridge() {
  'use strict';

  if (window.__NEKO_TAURI_CLICK_THROUGH_INSTALLED__) return;

  var tauri = window.__TAURI__;
  var listen = tauri && tauri.event && tauri.event.listen;
  var invoke = tauri && tauri.core && tauri.core.invoke;
  if (typeof listen !== 'function' || typeof invoke !== 'function') return;

  window.__NEKO_TAURI_CLICK_THROUGH_INSTALLED__ = true;

  var BACKGROUND_IDS = {
    app: true,
    root: true,
    'live2d-container': true,
    'live2d-canvas': true,
    'vrm-container': true,
    'vrm-canvas': true,
    'mmd-container': true,
    'mmd-canvas': true,
    'pngtuber-container': true,
    'react-chat-window-overlay': true,
    'react-chat-window-backdrop': true
  };
  var BACKGROUND_CLASSES = [
    'app-root',
    'transparent-overlay',
    'model-overlay',
    'pet-overlay',
    'live2d-container',
    'vrm-container',
    'mmd-container',
    'pngtuber-container'
  ];
  var MODEL_SOURCES = [
    { containerId: 'live2d-container', managerName: 'live2dManager' },
    { containerId: 'vrm-container', managerName: 'vrmManager' },
    { containerId: 'mmd-container', managerName: 'mmdManager' },
    { containerId: 'pngtuber-container', managerName: 'pngtuberManager' }
  ];
  var MODEL_EXIT_MARGIN = 10;

  var isIgnoring = false;
  var wasOverModel = false;
  var pointerIsDown = false;

  function isFiniteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  function isElementVisible(element) {
    if (!element || !element.isConnected) return false;
    if (element.hidden) return false;

    var style = window.getComputedStyle(element);
    if (!style) return true;
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    return Number(style.opacity || '1') > 0.01;
  }

  function isBackgroundElement(element) {
    if (!element || element.nodeType !== 1) return true;
    if (element === document.body || element === document.documentElement) return true;
    if (element.tagName === 'HTML' || BACKGROUND_IDS[element.id]) return true;

    for (var index = 0; index < BACKGROUND_CLASSES.length; index += 1) {
      if (element.classList && element.classList.contains(BACKGROUND_CLASSES[index])) {
        return true;
      }
    }
    return false;
  }

  function isInteractiveDomPoint(x, y) {
    var elements = [];
    if (typeof document.elementsFromPoint === 'function') {
      elements = document.elementsFromPoint(x, y);
    } else if (typeof document.elementFromPoint === 'function') {
      var element = document.elementFromPoint(x, y);
      if (element) elements = [element];
    }

    for (var index = 0; index < elements.length; index += 1) {
      var candidate = elements[index];
      if (!isBackgroundElement(candidate) && isElementVisible(candidate)) {
        return true;
      }
    }
    return false;
  }

  function normalizeBounds(rawBounds) {
    if (!rawBounds || typeof rawBounds !== 'object') return null;

    var left = Number(rawBounds.left != null ? rawBounds.left : rawBounds.x);
    var top = Number(rawBounds.top != null ? rawBounds.top : rawBounds.y);
    var right = Number(
      rawBounds.right != null
        ? rawBounds.right
        : left + Number(rawBounds.width)
    );
    var bottom = Number(
      rawBounds.bottom != null
        ? rawBounds.bottom
        : top + Number(rawBounds.height)
    );

    if (
      !Number.isFinite(left) ||
      !Number.isFinite(top) ||
      !Number.isFinite(right) ||
      !Number.isFinite(bottom) ||
      right <= left ||
      bottom <= top
    ) {
      return null;
    }
    return { left: left, top: top, right: right, bottom: bottom };
  }

  function pointIsInsideBounds(bounds, x, y, margin) {
    return (
      bounds &&
      x >= bounds.left - margin &&
      x <= bounds.right + margin &&
      y >= bounds.top - margin &&
      y <= bounds.bottom + margin
    );
  }

  function getManagerModelBounds(source) {
    var container = document.getElementById(source.containerId);
    if (!isElementVisible(container)) return null;

    var manager = window[source.managerName];
    if (!manager) return null;

    if (typeof manager.getModelScreenBounds === 'function') {
      try {
        var managerBounds = normalizeBounds(manager.getModelScreenBounds());
        if (managerBounds) return managerBounds;
      } catch (_) {}
    }

    if (source.managerName === 'pngtuberManager') {
      var image = container.querySelector('.pngtuber-image, img');
      if (isElementVisible(image)) return normalizeBounds(image.getBoundingClientRect());
    }

    return null;
  }

  function isPointOverModel(x, y) {
    var margin = wasOverModel ? MODEL_EXIT_MARGIN : 0;
    for (var index = 0; index < MODEL_SOURCES.length; index += 1) {
      var bounds = getManagerModelBounds(MODEL_SOURCES[index]);
      if (pointIsInsideBounds(bounds, x, y, margin)) return true;
    }
    return false;
  }

  function applyIgnoreState(shouldIgnore) {
    if (shouldIgnore === isIgnoring) return;
    var previous = isIgnoring;
    isIgnoring = shouldIgnore;
    Promise.resolve(invoke('set_click_through', { ignore: shouldIgnore })).catch(function(error) {
      isIgnoring = previous;
      console.error('切换 Tauri 鼠标穿透状态失败:', error);
    });
  }

  function handleRadarPoint(x, y) {
    if (!isFiniteNumber(x) || !isFiniteNumber(y)) return;

    // Once an interaction starts in this window, keep receiving input until
    // the button is released. Otherwise crossing a model edge mid-drag would
    // make the native window ignore the matching mouseup event.
    if (pointerIsDown) {
      applyIgnoreState(false);
      return;
    }

    var overModel = isPointOverModel(x, y);
    wasOverModel = overModel;
    var overUi = isInteractiveDomPoint(x, y);
    applyIgnoreState(!(overModel || overUi));
  }

  window.addEventListener('pointerdown', function() {
    pointerIsDown = true;
  }, true);
  window.addEventListener('mousedown', function() {
    pointerIsDown = true;
  }, true);
  window.addEventListener('pointerup', function() {
    pointerIsDown = false;
  }, true);
  window.addEventListener('mouseup', function() {
    pointerIsDown = false;
  }, true);
  window.addEventListener('blur', function() {
    pointerIsDown = false;
  });

  listen('neko_radar_tick', function(event) {
    var payload = event && event.payload;
    if (!Array.isArray(payload) || payload.length < 2) return;
    handleRadarPoint(Number(payload[0]), Number(payload[1]));
  }).catch(function(error) {
    console.error('监听 Tauri 鼠标雷达失败:', error);
  });

  // Kept intentionally small so the bridge can be verified in jsdom without
  // duplicating the production hit-test rules in a separate test-only module.
  window.__NEKO_TAURI_CLICK_THROUGH_TEST__ = {
    handleRadarPoint: handleRadarPoint,
    isInteractiveDomPoint: isInteractiveDomPoint,
    isPointOverModel: isPointOverModel
  };
})();
