import { API, DEFAULT_REQUIREMENTS, REQUIREMENT_TITLE_MAX_CHARS } from './config.js';
import { isGuest } from './auth.js';

export const REQUIREMENTS_STORAGE_KEY = 'mcmod_agent_requirements';
/** 上次与服务端默认项同步的时间戳（对应 API 的 updated_at） */
export const REQUIREMENTS_SYNCED_AT_KEY = 'mcmod_agent_requirements_synced_at';
const LEGACY_CUSTOM_KEY = 'mcmod_agent_custom_requirements';

/** @type {ReturnType<typeof normalizeItem>[] | null} */
let cachedServerDefaults = null;
/** @type {string | null} */
let cachedServerDefaultsUpdatedAt = null;

const DEFAULTS_FETCH_TIMEOUT_MS = 4000;

/**
 * 统计标题中的汉字数量
 */
export function countChineseChars(title) {
  const matches = title.match(/[\u4e00-\u9fff]/g);
  return matches ? matches.length : 0;
}

/**
 * 校验标题（至少 1 个汉字，最多 REQUIREMENT_TITLE_MAX_CHARS 个汉字）
 */
export function validateTitle(title) {
  const trimmed = (title || '').trim();
  if (!trimmed) {
    return { valid: false, message: '标题不能为空' };
  }
  const count = countChineseChars(trimmed);
  if (count === 0) {
    return { valid: false, message: '标题须包含至少 1 个汉字' };
  }
  if (count > REQUIREMENT_TITLE_MAX_CHARS) {
    return { valid: false, message: `标题最多 ${REQUIREMENT_TITLE_MAX_CHARS} 个汉字（当前 ${count} 个）` };
  }
  return { valid: true };
}

/**
 * 展示用标题（超出截断）
 */
export function displayTitle(title) {
  const trimmed = (title || '').trim();
  let count = 0;
  let result = '';
  for (const ch of trimmed) {
    if (/[\u4e00-\u9fff]/.test(ch)) {
      count += 1;
      if (count > REQUIREMENT_TITLE_MAX_CHARS) break;
    }
    result += ch;
  }
  if (countChineseChars(trimmed) > REQUIREMENT_TITLE_MAX_CHARS) {
    return result + '…';
  }
  return trimmed;
}

function normalizeItem(item) {
  return {
    id: item.id,
    title: (item.title || '').trim(),
    description: (item.description || '').trim(),
    detail: item.detail && typeof item.detail === 'object' ? { ...item.detail } : {},
    enabled: item.enabled !== false,
  };
}

function getDefaultsSource() {
  if (cachedServerDefaults?.length) return cachedServerDefaults;
  return DEFAULT_REQUIREMENTS;
}

function cloneDefaults() {
  return getDefaultsSource().map((r) => normalizeItem({ ...r }));
}

/**
 * 从服务端拉取默认「其他要求」（失败时保留 config.js 回退）
 * @returns {Promise<boolean>} 是否成功加载服务端默认项
 */
export async function initDefaultRequirements() {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DEFAULTS_FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(`${API.baseUrl || ''}${API.defaultRequirements}`, {
      signal: controller.signal,
    });
    if (!res.ok) return false;
    const data = await res.json();
    if (!Array.isArray(data?.items) || data.items.length === 0) return false;
    const items = data.items
      .filter((item) => item && typeof item.id === 'string')
      .map((item) => normalizeItem(item));
    if (items.length === 0) return false;
    cachedServerDefaults = items;
    cachedServerDefaultsUpdatedAt =
      typeof data.updated_at === 'string' ? data.updated_at : null;
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * 本地无有效要求列表时，用当前默认源（服务端或 config.js）写入 localStorage
 * @returns {boolean} 是否已刷新本地列表
 */
export function reconcileRequirementsWithServerDefaults() {
  if (isGuest()) return false;
  const raw = localStorage.getItem(REQUIREMENTS_STORAGE_KEY);
  if (!raw) {
    saveRequirements(cloneDefaults());
    return true;
  }
  try {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length === 0) {
      saveRequirements(cloneDefaults());
      return true;
    }
    const normalized = parsed
      .filter((item) => item && typeof item.id === 'string')
      .map(normalizeItem);
    if (normalized.length === 0) {
      saveRequirements(cloneDefaults());
      return true;
    }
    if (normalized.every((r) => r.enabled === false)) {
      saveRequirements(cloneDefaults());
      return true;
    }
  } catch {
    saveRequirements(cloneDefaults());
    return true;
  }
  return false;
}

export function isUserCustomRequirementId(id) {
  return typeof id === 'string' && id.startsWith('req_');
}

/**
 * 将服务端默认项合并进本地列表（管理员在后台保存后，用户刷新即可看到）
 * - 与服务端同 id 的预设项：用服务端最新 title/description/detail/enabled 覆盖
 * - 用户自建项（req_*）：保留不动
 * - 服务端新增的预设 id：追加到列表
 * @returns {boolean} 是否写入了 localStorage
 */
export function syncRequirementsFromServerDefaults() {
  if (isGuest()) return false;
  const serverItems = cloneDefaults();
  if (!serverItems.length) return false;

  const serverAt = cachedServerDefaultsUpdatedAt || '';
  const localAt = localStorage.getItem(REQUIREMENTS_SYNCED_AT_KEY) || '';
  if (serverAt && localAt && serverAt === localAt) return false;

  const local = loadRequirements();
  const serverIds = new Set(serverItems.map((r) => r.id));
  const customItems = local.filter(
    (item) => isUserCustomRequirementId(item.id) && !serverIds.has(item.id),
  );

  const merged = [...serverItems.map((r) => normalizeItem({ ...r })), ...customItems];
  saveRequirements(merged);
  if (serverAt) {
    localStorage.setItem(REQUIREMENTS_SYNCED_AT_KEY, serverAt);
  } else {
    localStorage.removeItem(REQUIREMENTS_SYNCED_AT_KEY);
  }
  return true;
}

function migrateLegacyStorage() {
  if (localStorage.getItem(REQUIREMENTS_STORAGE_KEY)) return;

  try {
    const legacyRaw = localStorage.getItem(LEGACY_CUSTOM_KEY);
    if (legacyRaw) {
      const legacy = JSON.parse(legacyRaw);
      if (Array.isArray(legacy) && legacy.length > 0) {
        const byId = new Map(cloneDefaults().map((r) => [r.id, r]));
        legacy.forEach((item) => {
          if (item && typeof item.id === 'string') {
            byId.set(item.id, normalizeItem(item));
          }
        });
        saveRequirements([...byId.values()]);
        return;
      }
    }
  } catch {
    /* ignore */
  }

  saveRequirements(cloneDefaults());
}

/**
 * 读取全部要求项（无数据时用默认项初始化）
 */
export function loadRequirements() {
  if (isGuest()) return cloneDefaults();

  migrateLegacyStorage();

  try {
    const raw = localStorage.getItem(REQUIREMENTS_STORAGE_KEY);
    if (!raw) {
      const defaults = cloneDefaults();
      saveRequirements(defaults);
      return defaults;
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed) || parsed.length === 0) {
      const defaults = cloneDefaults();
      saveRequirements(defaults);
      return defaults;
    }
    const normalized = parsed
      .filter((item) => item && typeof item.id === 'string')
      .map(normalizeItem);
    if (normalized.length === 0) {
      const defaults = cloneDefaults();
      saveRequirements(defaults);
      return defaults;
    }
    return normalized;
  } catch {
    const defaults = cloneDefaults();
    saveRequirements(defaults);
    return defaults;
  }
}

export function saveRequirements(items) {
  if (isGuest()) return;
  localStorage.setItem(REQUIREMENTS_STORAGE_KEY, JSON.stringify(items.map(normalizeItem)));
}

export function resetRequirementsToDefaults() {
  const defaults = cloneDefaults();
  saveRequirements(defaults);
  if (cachedServerDefaultsUpdatedAt) {
    localStorage.setItem(REQUIREMENTS_SYNCED_AT_KEY, cachedServerDefaultsUpdatedAt);
  } else {
    localStorage.removeItem(REQUIREMENTS_SYNCED_AT_KEY);
  }
  return defaults;
}

/** @deprecated 兼容旧引用 */
export function clearCustomRequirements() {
  resetRequirementsToDefaults();
}

/** @deprecated 兼容旧引用 */
export function loadCustomRequirements() {
  return loadRequirements();
}

/**
 * 当前启用的要求项（主界面展示）
 */
export function getActiveRequirements() {
  return loadRequirements().filter((r) => r.enabled !== false);
}

export function getRequirementById(id) {
  return loadRequirements().find((r) => r.id === id) || null;
}

export function generateRequirementId() {
  return `req_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
}

function buildDetailFromForm(customPrompt) {
  const detail = {};
  if (customPrompt?.trim()) {
    detail.custom_prompt = customPrompt.trim();
  }
  return detail;
}

export function addRequirement(data) {
  const validation = validateTitle(data.title);
  if (!validation.valid) throw new Error(validation.message);

  const items = loadRequirements();
  const item = normalizeItem({
    id: generateRequirementId(),
    title: data.title.trim(),
    description: (data.description || '').trim(),
    detail: buildDetailFromForm(data.customPrompt),
    enabled: true,
  });
  items.push(item);
  saveRequirements(items);
  return item;
}

export function updateRequirement(id, data) {
  const validation = validateTitle(data.title);
  if (!validation.valid) throw new Error(validation.message);

  const items = loadRequirements();
  const index = items.findIndex((i) => i.id === id);
  if (index === -1) throw new Error('未找到该要求项');

  const detail = { ...(items[index].detail || {}) };
  if (data.customPrompt !== undefined) {
    if (data.customPrompt.trim()) {
      detail.custom_prompt = data.customPrompt.trim();
    } else {
      delete detail.custom_prompt;
    }
  }

  items[index] = normalizeItem({
    ...items[index],
    title: data.title.trim(),
    description: (data.description || '').trim(),
    detail,
  });
  saveRequirements(items);
  return items[index];
}

export function deleteRequirement(id) {
  const items = loadRequirements().filter((i) => i.id !== id);
  saveRequirements(items);
}

/**
 * 按 id 顺序重排要求项
 * @param {string[]} orderedIds
 */
export function reorderRequirements(orderedIds) {
  if (!Array.isArray(orderedIds) || orderedIds.length === 0) return loadRequirements();
  const items = loadRequirements();
  const byId = new Map(items.map((i) => [i.id, i]));
  const reordered = [];
  orderedIds.forEach((id) => {
    const item = byId.get(id);
    if (item) {
      reordered.push(item);
      byId.delete(id);
    }
  });
  byId.forEach((item) => reordered.push(item));
  saveRequirements(reordered);
  return reordered;
}

/** @deprecated */
export const addCustomRequirement = addRequirement;
export const updateCustomRequirement = updateRequirement;
export const deleteCustomRequirement = deleteRequirement;
