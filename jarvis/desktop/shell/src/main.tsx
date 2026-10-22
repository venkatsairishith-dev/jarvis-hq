import { invoke } from '@tauri-apps/api/core';
import './startup.css';

const rootElement = document.getElementById('root');

if (rootElement) {
  const startupScreen = document.createElement('main');
  startupScreen.className = 'startup-screen';
  startupScreen.setAttribute('aria-live', 'polite');
  startupScreen.setAttribute('aria-busy', 'true');

  const startupCard = document.createElement('section');
  startupCard.className = 'startup-card';
  startupCard.setAttribute('role', 'status');

  const spinner = document.createElement('span');
  spinner.className = 'startup-spinner';
  spinner.setAttribute('aria-hidden', 'true');

  const productName = document.createElement('span');
  productName.textContent = 'N.E.K.O';

  startupCard.append(spinner, productName);
  startupScreen.append(startupCard);
  rootElement.replaceChildren(startupScreen);

  // 启动页没有可交互控件。让全屏透明窗口先穿透鼠标，避免后端启动期间
  // 阻挡用户操作其他应用；正式页面跳转前由 Rust 恢复交互。
  void invoke('set_click_through', {
    ignore: true,
    reason: 'startup-page-transparent',
    details: JSON.stringify({
      path: window.location.pathname,
      focused: document.hasFocus(),
      visibility: document.visibilityState,
    }),
  }).catch((error) => {
    console.warn('[Startup] 无法启用启动页鼠标穿透:', error);
  });
}
