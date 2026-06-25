/** @file 编写中页与首页共用的会话工具 */

import { MOCK_TASK_TITLE } from './mock-session-bootstrap.js';
import { TOKEN_STORAGE_KEY } from './config.js';

function hasAuthToken() {
  return Boolean(localStorage.getItem(TOKEN_STORAGE_KEY));
}

/**
 * @param {string} [blueprint]
 * @param {string} [fallback]
 */
export function deriveTaskTitle(blueprint, fallback = MOCK_TASK_TITLE) {
  const text = (blueprint || '').trim();
  if (!text) return fallback;
  const match = text.match(/^##\s+(.+)$/m);
  if (match?.[1]) return match[1].trim().slice(0, 80);
  const firstLine = text.split('\n')[0]?.replace(/^#+\s*/, '').trim();
  return (firstLine || fallback).slice(0, 80);
}

/**
 * @param {string} userConcept
 * @param {string} optimizedText
 * @param {boolean} optimizedActive
 */
export function buildReadableBlueprint(userConcept, optimizedText, optimizedActive) {
  if (optimizedActive && optimizedText?.trim()) return optimizedText.trim();
  const text = (userConcept || '').trim();
  if (!text) return '';
  if (/^#+\s/m.test(text)) return text;
  const title = deriveTaskTitle(text, '模组任务');
  return `## ${title}\n\n${text}`;
}

/**
 * @param {object} payload
 * @param {string} finalPrompt
 * @param {string} readableBlueprint
 * @param {string} [taskTitle]
 */
export function buildSessionBootstrap(payload, finalPrompt, readableBlueprint, taskTitle) {
  return {
    sessionId: null,
    mode: payload.mode || 'build',
    payload,
    finalPrompt,
    readableBlueprint,
    taskTitle: taskTitle || deriveTaskTitle(readableBlueprint, finalPrompt),
    createdAt: Date.now(),
  };
}

/** @deprecated 旧版全局键，仅用于登出时清理残留 */
export const SESSION_LIST_KEY = 'moduscript_session_list';

const SESSION_LIST_PREFIX = 'moduscript_session_list_';

const ACTIVE_LIST_CAP = 100;
const TRASHED_LIST_CAP = 50;

/**
 * @returns {string | null}
 */
export function sessionListStorageKey() {
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (!token) return null;
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return `${SESSION_LIST_PREFIX}${payload.sub || payload.id || 'guest'}`;
  } catch {
    return null;
  }
}

/** 登出时清理当前用户列表缓存及旧版全局键 */
export function clearSessionListStorage() {
  const key = sessionListStorageKey();
  if (key) localStorage.removeItem(key);
  localStorage.removeItem(SESSION_LIST_KEY);
}

/**
 * @param {object} item
 */
function normalizeSessionListItem(item) {
  let deletedAt = item.deletedAt || null;
  if (!deletedAt && item.trashed) {
    deletedAt = new Date(0).toISOString();
  }
  return {
    sessionId: item.sessionId,
    taskTitle: item.taskTitle ?? '未命名任务',
    mode: item.mode ?? 'build',
    status: item.status ?? 'running',
    createdAt: item.createdAt ?? Date.now(),
    pinned: Boolean(item.pinned),
    deletedAt,
  };
}

/**
 * @param {Array<object>} entries
 */
function saveSessionList(entries) {
  if (!hasAuthToken()) return;
  const key = sessionListStorageKey();
  if (!key) return;
  const normalized = entries.map(normalizeSessionListItem);
  const active = normalized.filter((e) => !e.deletedAt);
  const trashed = normalized.filter((e) => e.deletedAt);
  localStorage.setItem(
    key,
    JSON.stringify([
      ...active.slice(0, ACTIVE_LIST_CAP),
      ...trashed.slice(0, TRASHED_LIST_CAP),
    ]),
  );
}

/**
 * @param {Array<object>} remoteSessions API 返回的 sessions
 * @param {{ recycled?: boolean }} options
 */
export function replaceSessionListFromRemote(remoteSessions, { recycled = false } = {}) {
  if (!hasAuthToken()) return;
  const remoteEntries = (remoteSessions || []).map((s) =>
    normalizeSessionListItem({
      sessionId: s.session_id,
      taskTitle: s.task_title,
      mode: s.mode,
      status: s.status,
      createdAt: Date.parse(s.created_at) || 0,
      pinned: Boolean(s.pinned),
      deletedAt: recycled ? s.deleted_at || new Date(0).toISOString() : s.deleted_at || null,
    }),
  );
  const kept = loadSessionList().filter((item) => {
    if (recycled ? !item.deletedAt : Boolean(item.deletedAt)) return true;
    const isPlan = item.mode === 'plan' || String(item.sessionId || '').startsWith('plan-');
    if (isPlan) return true;
    return false;
  });
  saveSessionList([...remoteEntries, ...kept]);
}

/**
 * @param {{ sessionId: string, taskTitle: string, mode?: string, status?: string, createdAt?: number, pinned?: boolean, deletedAt?: string|null }} entry
 */
export function upsertSessionListEntry(entry) {
  try {
    const list = loadSessionList();
    const existing = list.find((item) => item.sessionId === entry.sessionId);
    const merged = {
      sessionId: entry.sessionId,
      taskTitle: entry.taskTitle ?? existing?.taskTitle ?? '未命名任务',
      mode: entry.mode ?? existing?.mode ?? 'build',
      status: entry.status ?? existing?.status ?? 'running',
      createdAt: entry.createdAt ?? existing?.createdAt ?? Date.now(),
      pinned: entry.pinned ?? existing?.pinned ?? false,
      deletedAt:
        entry.deletedAt !== undefined ? entry.deletedAt : existing?.deletedAt ?? null,
    };
    const filtered = list.filter((item) => item.sessionId !== entry.sessionId);
    if (!merged.deletedAt) {
      filtered.unshift(merged);
    } else {
      filtered.push(merged);
    }
    saveSessionList(filtered);
  } catch {
    /* ignore */
  }
}

/**
 * @returns {Array<{ sessionId: string, taskTitle: string, mode: string, status: string, createdAt: number, pinned: boolean, deletedAt: string|null }>}
 */
export function loadSessionList() {
  if (!hasAuthToken()) return [];

  const key = sessionListStorageKey();
  if (!key) return [];

  try {
    const raw = localStorage.getItem(key);
    const list = raw ? JSON.parse(raw) : [];
    return list.map(normalizeSessionListItem);
  } catch {
    return [];
  }
}
