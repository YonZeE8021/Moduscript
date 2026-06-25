/** @file 编写中页 API 客户端 */

import { API, ADMIN_PANEL_TOKEN_KEY, DEBUG_MOCK, TOKEN_STORAGE_KEY } from './config.js';

function getApiUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return API.baseUrl ? `${API.baseUrl.replace(/\/$/, '')}${p}` : p;
}

export function isAdminViewMode() {
  const params = new URLSearchParams(window.location.search);
  return params.get('from') === 'admin' && !!sessionStorage.getItem(ADMIN_PANEL_TOKEN_KEY);
}

function getSessionAuthToken() {
  if (isAdminViewMode()) {
    return sessionStorage.getItem(ADMIN_PANEL_TOKEN_KEY);
  }
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getSessionAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function redirectLogin() {
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login.html?next=${next}`;
}

async function parseJsonResponse(res) {
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return { message: text };
  }
}

function handleAuthError(res) {
  if (res.status !== 401) return;
  if (isAdminViewMode()) {
    throw new Error('管理后台登录已过期，请返回 /admin.html 重新登录');
  }
  redirectLogin();
  throw new Error('请先登录');
}

/**
 * @param {object} payload
 * @param {{ readableBlueprint?: string, taskTitle?: string }} [meta]
 */
export async function createSession(payload, meta = {}) {
  const body = {
    ...payload,
    readable_blueprint: meta.readableBlueprint,
    task_title: meta.taskTitle,
  };

  const res = await fetch(getApiUrl(API.createSession), {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return { source: 'server', ...data };
}

export async function fetchSessionSnapshot(sessionId) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}`), {
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return { source: 'server', snapshot: data };
}

export async function fetchSessionList(recycled = false) {
  const q = recycled ? '?recycled=true' : '';
  const res = await fetch(getApiUrl(`${API.listSessions}${q}`), { headers: authHeaders() });
  if (res.status === 401) return { source: 'server', sessions: [] };
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error('list failed');
  return { source: 'server', sessions: data?.sessions || [] };
}

export function subscribeSession(sessionId, onEvent, onError) {
  const token = getSessionAuthToken();
  let url = getApiUrl(`${API.getSession}/${sessionId}/stream`);
  if (token) url += `?token=${encodeURIComponent(token)}`;

  let es = null;
  let closed = false;
  let reconnectTimer = null;

  const connect = () => {
    if (closed) return;
    es?.close();
    es = new EventSource(url);

    es.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === 'ping') return;
        onEvent(payload);
      } catch (err) {
        onError?.(err);
      }
    };

    es.onerror = () => {
      es?.close();
      if (closed) return;
      onError?.(new Error('SSE 连接中断'));
      reconnectTimer = window.setTimeout(() => {
        reconnectTimer = null;
        connect();
      }, 3000);
    };
  };

  const close = () => {
    if (closed) return;
    closed = true;
    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    es?.close();
  };

  connect();
  return close;
}

export async function patchSessionTitle(sessionId, title) {
  return patchSessionMeta(sessionId, { title });
}

export async function patchSessionMeta(sessionId, meta) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}`), {
    method: 'PATCH',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(meta),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return data;
}

export async function trashSession(sessionId) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/trash`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return data;
}

export async function restoreSession(sessionId) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/restore`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return data;
}

export async function stopSession(sessionId) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/stop`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return data;
}

export async function sendSessionMessage(sessionId, content) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/messages`), {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ content }),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return data;
}

export async function regenerateSession(sessionId) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/regenerate`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `重新生成失败 (${res.status})`);
  return data;
}

export async function rewindSession(sessionId, body) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/rewind`), {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `重新输入失败 (${res.status})`);
  return data;
}

export async function switchSessionBranch(sessionId, nodeId) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/branch/switch`), {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ node_id: nodeId }),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `切换分支失败 (${res.status})`);
  return data;
}

export async function submitSessionAction(sessionId, choiceId, extra = {}) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/actions`), {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ choice_id: choiceId, ...extra }),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return data;
}

export function getArtifactDownloadUrl(sessionId) {
  return getApiUrl(`${API.getSession}/${sessionId}/artifact`);
}

export async function buildSessionArtifact(sessionId) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/build`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `编译失败 (${res.status})`);
  return data;
}

export async function listWorkspaceEntries(sessionId, path = '') {
  const q = new URLSearchParams({ path });
  const res = await fetch(
    getApiUrl(`${API.getSession}/${sessionId}/workspace/entries?${q}`),
    { headers: authHeaders() },
  );
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `列表失败 (${res.status})`);
  return data;
}

export async function fetchWorkspaceFile(sessionId, path) {
  const q = new URLSearchParams({ path });
  const res = await fetch(
    getApiUrl(`${API.getSession}/${sessionId}/workspace/file?${q}`),
    { headers: authHeaders() },
  );
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `读取失败 (${res.status})`);
  return data;
}

function workspaceDownloadUrl(sessionId, path, kind) {
  const q = new URLSearchParams({ path });
  const suffix = kind === 'archive' ? 'archive' : 'download';
  return getApiUrl(`${API.getSession}/${sessionId}/workspace/${suffix}?${q}`);
}

export async function downloadWorkspaceFile(sessionId, path, filename) {
  const res = await fetch(workspaceDownloadUrl(sessionId, path, 'download'), {
    headers: authHeaders(),
  });
  handleAuthError(res);
  if (!res.ok) {
    let detail = 'download failed';
    try {
      const data = await res.json();
      detail = data?.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  return { name: filename || path.split('/').pop() || 'file', blob };
}

export async function downloadWorkspaceArchive(sessionId, path, filename) {
  const res = await fetch(workspaceDownloadUrl(sessionId, path, 'archive'), {
    headers: authHeaders(),
  });
  handleAuthError(res);
  if (!res.ok) {
    let detail = 'archive failed';
    try {
      const data = await res.json();
      detail = data?.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  return { name: filename || 'workspace.zip', blob };
}

export async function downloadSessionArtifact(sessionId, artifactName) {
  const res = await fetch(getArtifactDownloadUrl(sessionId), { headers: authHeaders() });
  handleAuthError(res);
  if (!res.ok) {
    let detail = 'download failed';
    try {
      const data = await res.json();
      detail = data?.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  const blob = await res.blob();
  return { name: artifactName || 'mod.jar', blob };
}

export async function startSessionTestServer(sessionId) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/test-server`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return data;
}

export async function stopSessionTestServer(sessionId) {
  const res = await fetch(getApiUrl(`${API.getSession}/${sessionId}/test-server`), {
    method: 'DELETE',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw new Error(data?.detail || data?.message || `请求失败 (${res.status})`);
  return data;
}

export { DEBUG_MOCK };
