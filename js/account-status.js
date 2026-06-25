/** @file 顶栏账号状态展示 */

import { fetchMe, formatAccountLabel, getToken, setToken } from './auth.js';
import { clearSessionListStorage } from './session-utils.js';

function loginHref() {
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  return `/login.html?next=${next}`;
}

function renderGuest(el) {
  el.textContent = '游客，请登录';
  el.href = loginHref();
  el.title = '游客，请登录';
}

function renderUser(el, user) {
  const label = formatAccountLabel(user);
  el.textContent = label;
  el.href = '/settings.html';
  el.title = label;
}

export async function initAccountStatus() {
  const el = document.getElementById('accountStatus');
  if (!el) return;

  if (!getToken()) {
    renderGuest(el);
    return;
  }

  const user = await fetchMe();
  if (!user) {
    clearSessionListStorage();
    setToken(null);
    renderGuest(el);
    return;
  }

  renderUser(el, user);
}
