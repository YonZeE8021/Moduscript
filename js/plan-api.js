/** @file 规划模式 API 客户端 */

import { API, ADMIN_PANEL_TOKEN_KEY, TOKEN_STORAGE_KEY } from './config.js';

function getApiUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return API.baseUrl ? `${API.baseUrl.replace(/\/$/, '')}${p}` : p;
}

export function isAdminViewMode() {
  const params = new URLSearchParams(window.location.search);
  return params.get('from') === 'admin' && !!sessionStorage.getItem(ADMIN_PANEL_TOKEN_KEY);
}

function getPlanAuthToken() {
  if (isAdminViewMode()) {
    return sessionStorage.getItem(ADMIN_PANEL_TOKEN_KEY);
  }
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getPlanAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

function redirectLogin() {
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login.html?next=${next}`;
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
 * @param {unknown} detail
 * @param {string} [fallback]
 */
export function formatApiDetail(detail, fallback = '请求失败') {
  if (detail == null || detail === '') return fallback;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) {
    const parts = detail.map((item) => {
      if (typeof item === 'string') return item;
      if (item && typeof item === 'object') {
        const loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
        const msg = item.msg || item.message || '';
        return loc && msg ? `${loc}: ${msg}` : msg || JSON.stringify(item);
      }
      return String(item);
    });
    return parts.filter(Boolean).join('；') || fallback;
  }
  if (typeof detail === 'object') {
    const obj = /** @type {{ message?: string }} */ (detail);
    if (obj.message) return obj.message;
    try {
      return JSON.stringify(detail);
    } catch {
      return fallback;
    }
  }
  return String(detail);
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

function apiError(data, fallback) {
  return new Error(formatApiDetail(data?.detail ?? data?.message, fallback));
}

export async function fetchPlanList(recycled = false) {
  const qs = recycled ? '?recycled=true' : '';
  const res = await fetch(getApiUrl(`${API.planSessions}${qs}`), { headers: authHeaders() });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `请求失败 (${res.status})`);
  return data?.items || [];
}

/**
 * @param {object} body
 */
export async function createPlanSession(body) {
  const res = await fetch(getApiUrl(API.planSessions), {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `请求失败 (${res.status})`);
  return data;
}

export async function fetchPlanSnapshot(planId) {
  if (isAdminViewMode()) {
    const ownerId = new URLSearchParams(window.location.search).get('owner_id') || '';
    if (!ownerId) throw new Error('管理员查看缺少 owner_id 参数');
    const qs = new URLSearchParams({ owner_id: ownerId });
    const res = await fetch(getApiUrl(`/api/v1/admin/plans/${planId}?${qs}`), {
      headers: authHeaders(),
    });
    handleAuthError(res);
    const data = await parseJsonResponse(res);
    if (!res.ok) throw apiError(data, `请求失败 (${res.status})`);
    return data;
  }
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}`), { headers: authHeaders() });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `请求失败 (${res.status})`);
  return data;
}

/**
 * @param {string} planId
 * @param {(event: object) => void} onEvent
 */
export function connectPlanEvents(planId, onEvent) {
  if (isAdminViewMode()) {
    return () => {};
  }
  const noop = () => {};
  const url = getApiUrl(`${API.planSessions}/${planId}/events`);
  const token = getPlanAuthToken();
  if (token) {
    return connectPlanEventsFetch(planId, onEvent) || noop;
  }

  const es = new EventSource(url, { withCredentials: false });
  es.onmessage = (ev) => {
    try {
      const payload = JSON.parse(ev.data);
      if (payload?.type === 'ping') return;
      onEvent(payload);
    } catch {
      /* ignore */
    }
  };
  es.onerror = () => es.close();
  return () => es.close();
}

async function connectPlanEventsFetch(planId, onEvent) {
  const url = getApiUrl(`${API.planSessions}/${planId}/events`);
  const controller = new AbortController();

  (async () => {
    try {
      const res = await fetch(url, {
        headers: authHeaders({ Accept: 'text/event-stream' }),
        signal: controller.signal,
      });
      handleAuthError(res);
      if (!res.ok || !res.body) return;

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split('\n\n');
        buffer = parts.pop() || '';
        for (const part of parts) {
          const line = part.split('\n').find((l) => l.startsWith('data: '));
          if (!line) continue;
          try {
            const payload = JSON.parse(line.slice(6));
            if (payload?.type === 'ping') continue;
            onEvent(payload);
          } catch {
            /* ignore */
          }
        }
      }
    } catch (err) {
      if (err.name !== 'AbortError') console.warn('plan SSE error', err);
    }
  })();

  return () => controller.abort();
}

export async function skipPlanReferenceWait(planId) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/skip-reference-wait`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `跳过索引等待失败 (${res.status})`);
  return data;
}

export async function retryPlanReference(planId, projectId) {
  const res = await fetch(
    getApiUrl(`${API.planSessions}/${planId}/references/${encodeURIComponent(projectId)}/retry`),
    { method: 'POST', headers: authHeaders() },
  );
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `重试索引失败 (${res.status})`);
  return data;
}

export async function retryPlanFirstTurn(planId) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/turns/retry-first`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `重试首轮失败 (${res.status})`);
  return data;
}

export async function regeneratePlanTurn(planId, body = {}) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/turns/regenerate`), {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `重新生成失败 (${res.status})`);
  return data;
}

export async function regeneratePlanQuestion(planId, questionId, body = {}) {
  const res = await fetch(
    getApiUrl(`${API.planSessions}/${planId}/turns/questions/${encodeURIComponent(questionId)}/regenerate`),
    {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify(body),
    },
  );
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `重新生成失败 (${res.status})`);
  return data;
}

/**
 * @param {string} planId
 * @param {object} body
 */
export async function submitPlanTurn(planId, body) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/turns`), {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `提交失败 (${res.status})`);
  return data;
}

export async function finalizePlan(planId) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/finalize`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `定稿失败 (${res.status})`);
  return data;
}

export async function handoffPlan(planId) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/handoff`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `移交失败 (${res.status})`);
  return data;
}

/**
 * @param {string} planId
 * @param {object} l1
 */
export async function updatePlanKnowledgeL1(planId, l1) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/knowledge-l1`), {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(l1),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `更新失败 (${res.status})`);
  return data;
}

export async function fetchPlanReferences(planId) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/references`), {
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `获取参考信息失败 (${res.status})`);
  return data;
}

export async function lookupPlanReference(planId, projectId, query) {
  const res = await fetch(
    getApiUrl(`${API.planSessions}/${planId}/references/${encodeURIComponent(projectId)}/lookup`),
    {
      method: 'POST',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ query }),
    },
  );
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `检索失败 (${res.status})`);
  return data;
}

/**
 * @param {string} planId
 * @param {{ task_title?: string, pinned?: boolean }} body
 */
export async function patchPlanMeta(planId, body) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}`), {
    method: 'PATCH',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `更新失败 (${res.status})`);
  return data;
}

export async function trashPlan(planId) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/trash`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `移入回收站失败 (${res.status})`);
  return data;
}

export async function restorePlan(planId) {
  const res = await fetch(getApiUrl(`${API.planSessions}/${planId}/restore`), {
    method: 'POST',
    headers: authHeaders(),
  });
  handleAuthError(res);
  const data = await parseJsonResponse(res);
  if (!res.ok) throw apiError(data, `恢复失败 (${res.status})`);
  return data;
}
