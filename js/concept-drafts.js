/** @file 主页构想草稿 — 内容隔离 + 工作区 UI 全局态 */

import { API, DEFAULTS, DEFAULT_INTERRUPTION_LEVEL, DEFAULT_MAX_TURNS, TOKEN_STORAGE_KEY } from './config.js';
import { authHeaders, isGuest } from './auth.js';

const STORAGE_PREFIX = 'moduscript_concept_drafts_';
const GUEST_STORAGE_KEY = `${STORAGE_PREFIX}guest`;
let store = null;
let saveTimer = null;
let onChange = null;

function storageKey() {
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (!token) return `${STORAGE_PREFIX}guest`;
  try {
    const payload = JSON.parse(atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/')));
    return `${STORAGE_PREFIX}${payload.sub || payload.id || 'guest'}`;
  } catch {
    return `${STORAGE_PREFIX}guest`;
  }
}

function newDraftId() {
  return `draft-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

function defaultWorkspaceUi() {
  return {
    advancedOpen: false,
    referenceModsCollapsed: true,
  };
}

function defaultDescOptimize() {
  return {
    text: '',
    visible: false,
    collapsed: false,
  };
}

function normalizeDescOptimize(draft) {
  const legacyText = draft.descOptimizeText ?? draft.descOptimize?.text ?? '';
  const src = draft.descOptimize || {};
  const text = typeof legacyText === 'string' ? legacyText : '';
  const visible = Boolean(src.visible ?? (text.trim() ? true : false));
  return {
    text,
    visible: text.trim() ? visible : false,
    collapsed: Boolean(src.collapsed),
  };
}

function defaultDraft(title = '新构想') {
  return {
    id: newDraftId(),
    title,
    prompt: '',
    mcVersion: DEFAULTS.mcVersion,
    modLoader: DEFAULTS.modLoader,
    platform: DEFAULTS.platform,
    requirements: [],
    referenceMods: { manual: [], autoSearch: false },
    modMeta: { modName: '', modId: '', packageName: '' },
    interruptionLevel: DEFAULT_INTERRUPTION_LEVEL,
    maxTurns: DEFAULT_MAX_TURNS,
    planReadDepth: 'standard',
    descOptimize: defaultDescOptimize(),
    pinned: false,
    pinnedAt: 0,
    updatedAt: Date.now(),
    accessedAt: Date.now(),
  };
}

function defaultStore() {
  const draft = defaultDraft('构想 1');
  return {
    activeId: draft.id,
    drafts: [draft],
    workspaceUi: defaultWorkspaceUi(),
  };
}

function emptyGuestStore() {
  return {
    activeId: null,
    drafts: [],
    workspaceUi: defaultWorkspaceUi(),
  };
}

function normalizeDraft(draft) {
  return {
    id: draft.id,
    title: draft.title || '未命名构想',
    prompt: draft.prompt || '',
    mcVersion: draft.mcVersion || DEFAULTS.mcVersion,
    modLoader: draft.modLoader || DEFAULTS.modLoader,
    platform: draft.platform || DEFAULTS.platform,
    requirements: Array.isArray(draft.requirements) ? [...draft.requirements] : [],
    referenceMods: {
      manual: Array.isArray(draft.referenceMods?.manual) ? draft.referenceMods.manual : [],
      autoSearch: Boolean(draft.referenceMods?.autoSearch),
    },
    modMeta: {
      modName: draft.modMeta?.modName || '',
      modId: draft.modMeta?.modId || '',
      packageName: draft.modMeta?.packageName || '',
    },
    interruptionLevel: draft.interruptionLevel ?? DEFAULT_INTERRUPTION_LEVEL,
    maxTurns: draft.maxTurns ?? DEFAULT_MAX_TURNS,
    planReadDepth: draft.planReadDepth === 'fast' || draft.planReadDepth === 'deep'
      ? draft.planReadDepth
      : 'standard',
    descOptimize: normalizeDescOptimize(draft),
    pinned: Boolean(draft.pinned),
    pinnedAt: draft.pinnedAt || 0,
    updatedAt: draft.updatedAt || Date.now(),
    accessedAt: draft.accessedAt || draft.updatedAt || Date.now(),
  };
}

function draftContentKey(draft) {
  return JSON.stringify({
    prompt: draft.prompt || '',
    mcVersion: draft.mcVersion,
    modLoader: draft.modLoader,
    platform: draft.platform,
    requirements: draft.requirements || [],
    referenceMods: draft.referenceMods || { manual: [], autoSearch: false },
    modMeta: draft.modMeta || { modName: '', modId: '', packageName: '' },
    interruptionLevel: draft.interruptionLevel,
    maxTurns: draft.maxTurns ?? DEFAULT_MAX_TURNS,
    planReadDepth: draft.planReadDepth || 'standard',
    descOptimize: draft.descOptimize || defaultDescOptimize(),
  });
}

function migrateWorkspaceUi(parsed) {
  const oldUi = parsed.uiState || {};
  const ws = parsed.workspaceUi || {};
  let referenceModsCollapsed = ws.referenceModsCollapsed;
  if (referenceModsCollapsed === undefined) {
    if (oldUi.referenceModsOpen === false) referenceModsCollapsed = true;
    else if (oldUi.referenceModsOpen === true) referenceModsCollapsed = false;
    else referenceModsCollapsed = true;
  }
  return {
    ...defaultWorkspaceUi(),
    advancedOpen: ws.advancedOpen ?? oldUi.advancedOpen ?? false,
    referenceModsCollapsed,
  };
}

function migrateStore(parsed) {
  if (!parsed?.drafts?.length) return defaultStore();
  return {
    activeId: parsed.activeId || parsed.drafts[0].id,
    drafts: parsed.drafts.map(normalizeDraft),
    workspaceUi: migrateWorkspaceUi(parsed),
  };
}

function loadLocal() {
  try {
    const raw = localStorage.getItem(storageKey());
    if (!raw) return defaultStore();
    const parsed = JSON.parse(raw);
    if (!parsed?.drafts?.length) return defaultStore();
    return migrateStore(parsed);
  } catch {
    return defaultStore();
  }
}

function persistLocal() {
  if (!store || isGuest()) return;
  localStorage.setItem(storageKey(), JSON.stringify(store));
}

async function syncToServer() {
  if (!localStorage.getItem(TOKEN_STORAGE_KEY) || !store) return;
  try {
    await fetch(`${API.baseUrl || ''}${API.userPreferences}`, {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ concept_drafts: store }),
    });
  } catch {
    /* ignore */
  }
}

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    persistLocal();
    syncToServer();
  }, 300);
}

export function flushConceptDraftSave() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = null;
  persistLocal();
  syncToServer();
}

function notifyStoreChange() {
  onChange?.(store);
}

export async function initConceptDrafts({ onStoreChange } = {}) {
  onChange = onStoreChange;
  if (isGuest()) {
    localStorage.removeItem(GUEST_STORAGE_KEY);
    store = emptyGuestStore();
    notifyStoreChange();
    return store;
  }
  store = loadLocal();
  if (localStorage.getItem(TOKEN_STORAGE_KEY)) {
    try {
      const res = await fetch(`${API.baseUrl || ''}${API.userPreferences}`, {
        headers: authHeaders(),
      });
      if (res.ok) {
        const prefs = await res.json();
        if (prefs?.concept_drafts?.drafts?.length) {
          store = migrateStore(prefs.concept_drafts);
          persistLocal();
        }
      }
    } catch {
      /* local only */
    }
  }
  notifyStoreChange();
  return store;
}

export function getConceptStore() {
  return store;
}

export function getActiveDraft() {
  if (!store) return null;
  return store.drafts.find((d) => d.id === store.activeId) || store.drafts[0];
}

export function getUiState() {
  return store?.workspaceUi || defaultWorkspaceUi();
}

export function getWorkspaceUi() {
  return getUiState();
}

export function setUiState(patch) {
  if (!store) return;
  store.workspaceUi = { ...getUiState(), ...patch };
  scheduleSave();
}

export function setWorkspaceUi(patch) {
  setUiState(patch);
}

export function switchDraft(id) {
  if (!store) return;
  const draft = store.drafts.find((d) => d.id === id);
  if (!draft) return;
  store.activeId = id;
  draft.accessedAt = Date.now();
  persistLocal();
  scheduleSave();
}

/** @param {string} [id] 默认当前 active 构想 */
export function touchDraftAccess(id) {
  if (!store || isGuest()) return;
  const draftId = id || store.activeId;
  const draft = store.drafts.find((d) => d.id === draftId);
  if (!draft) return;
  draft.accessedAt = Date.now();
  flushConceptDraftSave();
}

export function createDraft(title) {
  if (isGuest()) return null;
  if (!store) store = defaultStore();
  const draft = defaultDraft(title || `构想 ${store.drafts.length + 1}`);
  store.drafts.unshift(draft);
  store.activeId = draft.id;
  persistLocal();
  scheduleSave();
  notifyStoreChange();
  return draft;
}

export function renameDraft(id, title) {
  const draft = store?.drafts.find((d) => d.id === id);
  if (!draft || !title?.trim()) return;
  draft.title = title.trim().slice(0, 40);
  draft.updatedAt = Date.now();
  scheduleSave();
  notifyStoreChange();
}

export function deleteDraft(id) {
  if (!store || store.drafts.length <= 1) return false;
  store.drafts = store.drafts.filter((d) => d.id !== id);
  if (store.activeId === id) store.activeId = store.drafts[0].id;
  persistLocal();
  scheduleSave();
  notifyStoreChange();
  return true;
}

export function updateActiveDraft(patch) {
  if (isGuest()) return;
  const draft = getActiveDraft();
  if (!draft) return;
  const before = draftContentKey(draft);
  Object.assign(draft, patch);
  const changed = before !== draftContentKey(draft);
  if (changed) {
    draft.updatedAt = Date.now();
    notifyStoreChange();
  }
  scheduleSave();
}

export function toggleDraftPin(id) {
  const draft = store?.drafts.find((d) => d.id === id);
  if (!draft) return;
  draft.pinned = !draft.pinned;
  draft.pinnedAt = draft.pinned ? Date.now() : 0;
  persistLocal();
  scheduleSave();
  notifyStoreChange();
}

export function listDrafts() {
  return store?.drafts || [];
}
