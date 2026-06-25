import { API, MAX_REFERENCE_MODS } from './config.js';
import { isGuest } from './auth.js';

export const REFERENCE_MODS_STORAGE_KEY = 'mcmod_agent_reference_mods';

const IDENTIFIER_REGEX = /^[\w!@$()`.+,"\-']{1,64}$/i;
const GITHUB_REPO_RE = /^https?:\/\/(?:www\.)?github\.com\/([^/]+)\/([^/#?]+)/i;

/**
 * @typedef {'code' | 'feature'} ReferenceType
 */

/**
 * @typedef {object} ReferenceMod
 * @property {string} project_id
 * @property {string} slug
 * @property {string} permalink
 * @property {string} [title]
 * @property {string} [description]
 * @property {string} [source_url]
 * @property {ReferenceType} reference_type
 * @property {boolean} [versionWarning]
 * @property {boolean} [decompile_attempt]
 * @property {string} [note]
 * @property {string[]} [loaders]
 * @property {string[]} [game_versions]
 */

/**
 * @typedef {object} ReferenceModsState
 * @property {ReferenceMod[]} manual
 * @property {boolean} autoSearch
 * @property {boolean} collapsed
 */

const defaultState = () => ({
  manual: [],
  autoSearch: false,
  collapsed: true,
});

/**
 * 从 permanent link、旧版 /mod/ 链接或项目 ID 解析标识符
 * @param {string} input
 * @returns {string | null}
 */
export function parseModrinthPermalink(input) {
  const trimmed = (input || '').trim();
  if (!trimmed) return null;

  try {
    if (/^https?:\/\//i.test(trimmed)) {
      const url = new URL(trimmed);
      const host = url.hostname.replace(/^www\./i, '').toLowerCase();
      if (host !== 'modrinth.com') return null;

      const projectMatch = url.pathname.match(/^\/project\/([^/]+)/i);
      if (projectMatch) return projectMatch[1];

      const modMatch = url.pathname.match(/^\/mod\/([^/]+)/i);
      if (modMatch) return modMatch[1];

      return null;
    }
  } catch {
    return null;
  }

  return IDENTIFIER_REGEX.test(trimmed) ? trimmed : null;
}

export function buildModrinthProjectUrl(projectId) {
  return `https://modrinth.com/project/${projectId}`;
}

/**
 * 从 GitHub 仓库 URL 解析 owner/repo（规则对齐后端 GITHUB_REPO_RE）
 * @param {string} input
 * @returns {{ owner: string, repo: string, source_url: string, project_id: string, slug: string, title: string } | null}
 */
export function parseGithubRepo(input) {
  const trimmed = (input || '').trim().replace(/\/+$/, '');
  if (!trimmed) return null;

  const match = GITHUB_REPO_RE.exec(trimmed);
  if (!match) return null;

  let repo = match[2];
  if (repo.endsWith('.git')) repo = repo.slice(0, -4);

  const owner = match[1];
  const sourceUrl = `https://github.com/${owner}/${repo}`;

  return {
    owner,
    repo,
    source_url: sourceUrl,
    project_id: `${owner}/${repo}`,
    slug: repo,
    title: repo,
  };
}

/**
 * 是否为直链 GitHub 参考（非 Modrinth 来源）
 * @param {object} mod
 */
export function isDirectSourceRef(mod) {
  const src = parseGithubRepo((mod.source_url || '').trim());
  if (!src) return false;
  const link = (mod.permalink || mod.url || '').trim();
  if (!link) return false;
  return parseGithubRepo(link) !== null;
}

function apiUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return API.baseUrl ? `${API.baseUrl.replace(/\/$/, '')}${p}` : p;
}

/**
 * 通过后端解析 Modrinth 项目
 * @param {string} urlOrId permanent link 或项目 ID
 */
export async function resolveModrinthProject(urlOrId) {
  const identifier = parseModrinthPermalink(urlOrId);
  const url = identifier && !/^https?:\/\//i.test(urlOrId.trim())
    ? buildModrinthProjectUrl(identifier)
    : urlOrId.trim();

  const res = await fetch(apiUrl(API.modrinthResolve), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url }),
  });

  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }

  if (!res.ok) {
    const detail = data?.detail;
    const msg = typeof detail === 'string'
      ? detail
      : Array.isArray(detail)
        ? detail.map((d) => d.msg || d).join('; ')
        : data?.message || `请求失败 (${res.status})`;
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }

  if (!data || !data.project_id) {
    const err = new Error(
      'API 返回无效数据，请确认已通过 uvicorn 启动后端（见 README）'
    );
    err.status = res.status;
    throw err;
  }

  return data;
}

/**
 * @param {object} project
 * @param {string} mcVersion
 * @param {string} modLoader
 * @param {string} [loaderLabel] 展示用加载器名称
 * @returns {{ type: 'warn' | 'error', text: string }[]}
 */
export function getVersionCompatibilityIssues(project, mcVersion, modLoader, loaderLabel = modLoader) {
  const issues = [];
  const loaders = (project.loaders || []).map((l) => String(l).toLowerCase());
  const versions = project.game_versions || [];
  const loaderNorm = modLoader.toLowerCase();

  if (loaders.length > 0 && !loaders.includes(loaderNorm)) {
    issues.push({
      type: 'warn',
      text: `加载器不匹配：当前为 ${loaderLabel}，该项目支持 ${loaders.join('、')}`,
    });
  }
  if (versions.length > 0 && !versions.includes(mcVersion)) {
    const preview = versions.slice(0, 6).join('、');
    const suffix = versions.length > 6 ? ' 等' : '';
    issues.push({
      type: 'warn',
      text: `MC 版本不匹配：当前为 ${mcVersion}，该项目支持 ${preview}${suffix}`,
    });
  }
  return issues;
}

/**
 * @param {object} project
 * @param {string} mcVersion
 * @param {string} modLoader
 */
export function checkVersionCompatibility(project, mcVersion, modLoader) {
  return getVersionCompatibilityIssues(project, mcVersion, modLoader).length > 0;
}

/** 参考代码且无开源地址 */
export function isRefModSourceMissing(mod) {
  return mod.reference_type === 'code' && !(mod.source_url || '').trim();
}

/** 是否写入最终提示词 / 提交 payload */
export function isRefModIncludedInPrompt(mod) {
  if (mod.reference_type !== 'code') return true;
  if ((mod.source_url || '').trim()) return true;
  return Boolean(mod.decompile_attempt);
}

/** @param {ReferenceMod[] | undefined} manual */
export function hasCodeReferenceMods(manual) {
  if (!Array.isArray(manual)) return false;
  return manual.some(
    (m) => m && m.reference_type === 'code' && isRefModIncludedInPrompt(m),
  );
}

/**
 * @returns {ReferenceModsState}
 */
export function loadReferenceModsState() {
  if (isGuest()) return defaultState();

  try {
    const raw = localStorage.getItem(REFERENCE_MODS_STORAGE_KEY);
    if (!raw) return defaultState();
    const parsed = JSON.parse(raw);
    const manual = Array.isArray(parsed.manual)
      ? parsed.manual
          .filter((m) => m && (m.project_id || m.slug))
          .map((m) => {
            const projectId = m.project_id || m.slug;
            const slug = m.slug || projectId;
            const refType = m.reference_type === 'code' ? 'code' : 'feature';
            return {
              project_id: projectId,
              slug,
              permalink: m.permalink || m.url || buildModrinthProjectUrl(projectId),
              title: m.title || slug,
              description: m.description || '',
              source_url: m.source_url || '',
              reference_type: refType,
              loaders: Array.isArray(m.loaders) ? m.loaders : [],
              game_versions: Array.isArray(m.game_versions) ? m.game_versions : [],
              versionWarning: Boolean(m.versionWarning),
              decompile_attempt: Boolean(m.decompile_attempt),
              note: typeof m.note === 'string' ? m.note : '',
            };
          })
      : [];
    return {
      manual,
      autoSearch: Boolean(parsed.autoSearch),
      collapsed: true,
    };
  } catch {
    return defaultState();
  }
}

/**
 * @param {ReferenceModsState} state
 */
export function saveReferenceModsState(state) {
  if (isGuest()) return;
  localStorage.setItem(
    REFERENCE_MODS_STORAGE_KEY,
    JSON.stringify({
      manual: state.manual,
      autoSearch: state.autoSearch,
    })
  );
}

/**
 * @param {ReferenceModsState} state
 */
export function buildReferenceModsPayload(state) {
  return {
    manual: state.manual.map((m) => {
      const included = isRefModIncludedInPrompt(m);
      const item = {
        project_id: m.project_id,
        slug: m.slug,
        url: m.permalink,
        title: m.title,
        reference_type: m.reference_type,
        include_in_prompt: included,
        ...(m.description ? { description: m.description } : {}),
        ...(m.note?.trim() ? { note: m.note.trim() } : {}),
        ...(m.reference_type === 'code' && m.source_url ? { source_url: m.source_url } : {}),
      };
      if (isRefModSourceMissing(m) && m.decompile_attempt) {
        item.decompile_attempt = true;
      }
      return item;
    }),
    auto_search: state.autoSearch,
  };
}

/**
 * @param {ReferenceMod[]} manual
 * @param {string} projectId
 * @param {ReferenceType} referenceType
 */
export function hasReferenceEntry(manual, projectId, referenceType) {
  return manual.some(
    (m) => m.project_id === projectId && m.reference_type === referenceType
  );
}

export function refModKey(mod) {
  return `${mod.project_id}:${mod.reference_type}`;
}
