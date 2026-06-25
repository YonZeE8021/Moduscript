/** @file 认证 API 与 token 管理 */

import { API, TOKEN_STORAGE_KEY } from './config.js';
import { clearSessionListStorage } from './session-utils.js';

export function getToken() {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function isGuest() {
  return !getToken();
}

export function setToken(token) {
  if (token) localStorage.setItem(TOKEN_STORAGE_KEY, token);
  else localStorage.removeItem(TOKEN_STORAGE_KEY);
}

export function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function apiUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return API.baseUrl ? `${API.baseUrl.replace(/\/$/, '')}${p}` : p;
}

async function parseJson(res) {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

export async function fetchMe() {
  const res = await fetch(apiUrl(API.authMe), { headers: authHeaders() });
  const data = await parseJson(res);
  if (!res.ok) return null;
  return normalizeUser(data?.user);
}

export function displayUsername(user) {
  if (!user) return '用户';
  if (user.username) return user.username;
  const email = user.email || '';
  if (email.includes('@')) return email.split('@', 1)[0];
  return email || '用户';
}

export function formatAccountLabel(user) {
  const name = displayUsername(user);
  const email = user?.email || '';
  return email ? `${name}:${email}` : name;
}

function normalizeUser(user) {
  if (!user) return null;
  return { ...user, username: displayUsername(user) };
}

export async function updateProfile({ username, email }) {
  const body = {};
  if (username != null) body.username = username;
  if (email !== undefined) body.email = email;
  const res = await fetch(apiUrl(API.userProfile), {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  const data = await parseJson(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || '保存失败');
  return normalizeUser(data?.user);
}

export async function changePassword(currentPassword, newPassword) {
  const res = await fetch(apiUrl(API.userPassword), {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  });
  const data = await parseJson(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || '修改密码失败');
}

export async function login(account, password) {
  const res = await fetch(apiUrl(API.authLogin), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account, password }),
  });
  const data = await parseJson(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || '登录失败');
  setToken(data.token);
  return normalizeUser(data.user);
}

export async function register(username, email, password) {
  const body = { username, password };
  const trimmedEmail = (email || '').trim();
  if (trimmedEmail) body.email = trimmedEmail;
  const res = await fetch(apiUrl(API.authRegister), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await parseJson(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || '注册失败');
  setToken(data.token);
  return normalizeUser(data.user);
}

export function logout() {
  clearSessionListStorage();
  setToken(null);
  window.location.href = '/login.html';
}

export function requireAuth(redirectTo = '/login.html') {
  if (!getToken()) {
    const next = encodeURIComponent(window.location.pathname + window.location.search);
    window.location.href = `${redirectTo}?next=${next}`;
    return false;
  }
  return true;
}

export async function fetchSiteSettings() {
  try {
    const res = await fetch(apiUrl(API.siteSettings));
    if (!res.ok) return null;
    return await res.json();
  } catch {
    return null;
  }
}
