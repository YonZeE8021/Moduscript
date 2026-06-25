/** @file 管理后台（独立管理口令，手动 URL 访问） */

import { ADMIN_PANEL_TOKEN_KEY, DEFAULT_REQUIREMENTS, REQUIREMENT_TITLE_MAX_CHARS } from './config.js';
import {
  validateTitle,
  countChineseChars,
  generateRequirementId,
} from './requirements-store.js';
import { attachDragSort, createDragHandle } from './list-drag-sort.js';
import {
  normalizeRequirementCost,
} from './requirement-cost.js';

function showToast(msg, type = 'info') {
  const c = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  c?.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

export function getAdminPanelToken() {
  return sessionStorage.getItem(ADMIN_PANEL_TOKEN_KEY);
}

export function setAdminPanelToken(token) {
  if (token) sessionStorage.setItem(ADMIN_PANEL_TOKEN_KEY, token);
  else sessionStorage.removeItem(ADMIN_PANEL_TOKEN_KEY);
}

function adminHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getAdminPanelToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function adminFetch(path, options = {}) {
  const extra = { ...(options.headers || {}) };
  if (options.body && !extra['Content-Type']) {
    extra['Content-Type'] = 'application/json';
  }
  const res = await fetch(path, { ...options, headers: adminHeaders(extra) });
  const data = await res.json().catch(() => ({}));
  if (res.status === 401) {
    setAdminPanelToken(null);
    showLogin();
    throw new Error('管理后台登录已过期，请重新输入口令');
  }
  if (!res.ok) throw new Error(data.detail || data.message || '请求失败');
  return data;
}

function showLogin() {
  document.getElementById('adminLoginSection')?.removeAttribute('hidden');
  document.getElementById('adminPanelContent')?.setAttribute('hidden', '');
  document.getElementById('adminLogoutBtn')?.setAttribute('hidden', '');
}

function showPanel() {
  document.getElementById('adminLoginSection')?.setAttribute('hidden', '');
  document.getElementById('adminPanelContent')?.removeAttribute('hidden');
  document.getElementById('adminLogoutBtn')?.removeAttribute('hidden');
}

async function loadOverview() {
  const data = await adminFetch('/api/v1/admin/overview');
  document.getElementById('adminStats').innerHTML = `
    <div class="admin-stat"><span>用户</span><strong>${data.user_count}</strong></div>
    <div class="admin-stat"><span>会话总数</span><strong>${data.session_count}</strong></div>
    <div class="admin-stat"><span>活跃会话</span><strong>${data.active_session_count}</strong></div>
  `;
}

async function loadSettings() {
  const s = await adminFetch('/api/v1/admin/settings');
  document.getElementById('registrationEnabled').checked = !!s.registration_enabled;
  document.getElementById('sharedLlmEnabled').checked = s.shared_llm_enabled !== false;
  document.getElementById('betaBannerEnabled').checked = !!s.beta_banner_enabled;
  document.getElementById('betaBannerText').value = s.beta_banner_text || '';
  document.getElementById('feedbackEmail').value = s.feedback_email || '';
}

async function loadLlm() {
  const llm = await adminFetch('/api/v1/admin/llm');
  document.getElementById('llmBaseUrl').value = llm.base_url || '';
  document.getElementById('llmModel').value = llm.model || '';
  document.getElementById('llmSubagent').value = llm.subagent_model || '';
  const keyInput = document.getElementById('llmApiKey');
  keyInput.value = '';
  if (llm.api_key_masked) {
    keyInput.placeholder = `已配置 (${llm.api_key_masked})，留空则不修改`;
  } else {
    keyInput.placeholder = '必填（或填写 Base URL）';
  }
  const hint = document.getElementById('llmStatusHint');
  if (hint) {
    hint.textContent = llm.configured
      ? `当前已配置统一 API（${llm.model || '默认模型'}）`
      : '尚未配置：请填写 API Key 或 Base URL 后保存';
  }
}

async function loadPromptOptimize() {
  const cfg = await adminFetch('/api/v1/admin/prompt-optimize');
  document.getElementById('promptOptimizeEnabled').checked = cfg.enabled !== false;
  document.getElementById('promptOptimizeBaseUrl').value = cfg.base_url || '';
  document.getElementById('promptOptimizeModel').value = cfg.model || '';
  document.getElementById('promptOptimizeSystemPrompt').value = cfg.system_prompt || '';
  document.getElementById('promptOptimizeReasoningEffort').value = cfg.reasoning_effort || 'high';
  document.getElementById('promptOptimizeThinkingEnabled').checked = cfg.thinking_enabled !== false;
  const keyInput = document.getElementById('promptOptimizeApiKey');
  keyInput.value = '';
  if (cfg.api_key_masked) {
    keyInput.placeholder = `已配置 (${cfg.api_key_masked})，留空则不修改`;
  } else {
    keyInput.placeholder = '必填';
  }
  const hint = document.getElementById('promptOptimizeStatusHint');
  if (hint) {
    if (!cfg.enabled) {
      hint.textContent = '描述优化已禁用，首页「优化描述」按钮将不可用';
    } else if (cfg.configured) {
      hint.textContent = `当前已配置（${cfg.model || '默认模型'}）`;
    } else {
      hint.textContent = '尚未配置 API Key：请填写后保存';
    }
  }
}

const userInfoById = new Map();

function formatAdminUserCell(user) {
  const name = user?.username || '';
  const email = user?.email || '';
  if (name) {
    return email
      ? `${escapeHtml(name)}<br><span class="admin-session-email">${escapeHtml(email)}</span>`
      : escapeHtml(name);
  }
  return email
    ? escapeHtml(email)
    : `<code>${escapeHtml(user?.id || '—')}</code>`;
}

function formatUserLabel(userId) {
  if (!userId) return '—';
  const info = userInfoById.get(userId);
  if (info) return formatAdminUserCell({ id: userId, ...info });
  return `<code>${escapeHtml(userId)}</code>`;
}

function truncateText(text, maxLen = 120) {
  const s = String(text || '').trim();
  if (s.length <= maxLen) return s;
  return `${s.slice(0, maxLen)}…`;
}

async function loadUsers() {
  const { users } = await adminFetch('/api/v1/admin/users');
  userInfoById.clear();
  for (const u of users) {
    if (u.id) {
      userInfoById.set(u.id, { username: u.username || '', email: u.email || '' });
    }
  }
  document.getElementById('usersTable').innerHTML = `
    <table><thead><tr><th>用户名</th><th>邮箱</th><th>角色</th><th>状态</th><th></th></tr></thead>
    <tbody>${users
      .map(
        (u) => `<tr>
        <td>${escapeHtml(u.username || '—')}</td><td>${u.email ? escapeHtml(u.email) : '—'}</td><td>${escapeHtml(u.role || '')}</td><td>${u.disabled ? '已禁用' : '正常'}</td>
        <td><button type="button" class="btn btn-outline btn-sm" data-user-id="${u.id}" data-disabled="${!u.disabled}">
          ${u.disabled ? '启用' : '禁用'}
        </button></td></tr>`,
      )
      .join('')}</tbody></table>`;

  document.getElementById('usersTable').querySelectorAll('[data-user-id]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await adminFetch(`/api/v1/admin/users/${btn.dataset.userId}`, {
        method: 'PATCH',
        body: JSON.stringify({ disabled: btn.dataset.disabled === 'true' }),
      });
      showToast('已更新', 'success');
      loadUsers();
    });
  });
}

const sessionsPageSize = 25;
let sessionsOffset = 0;
let sessionsTotal = 0;

const plansPageSize = 25;
let plansOffset = 0;
let plansTotal = 0;

function sessionViewHref(s) {
  const params = new URLSearchParams({
    session_id: s.session_id,
    from: 'admin',
  });
  if (s.owner_id) params.set('owner_id', s.owner_id);
  return `/session.html?${params}`;
}

function planViewHref(p) {
  const params = new URLSearchParams({
    plan_id: p.plan_id,
    from: 'admin',
  });
  if (p.owner_id) params.set('owner_id', p.owner_id);
  return `/plan.html?${params}`;
}

function formatSessionCreatedAt(iso) {
  if (!iso) return '—';
  return String(iso).replace('T', ' ').slice(0, 16);
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function loadSessions() {
  const table = document.getElementById('sessionsTable');
  const meta = document.getElementById('sessionsMeta');
  const pageInfo = document.getElementById('sessionsPageInfo');
  if (!table) return;

  const q = document.getElementById('sessionsSearch')?.value.trim() || '';
  const status = document.getElementById('sessionsStatusFilter')?.value || '';
  const params = new URLSearchParams({
    limit: String(sessionsPageSize),
    offset: String(sessionsOffset),
  });
  if (q) params.set('q', q);
  if (status) params.set('status', status);

  try {
    const data = await adminFetch(`/api/v1/admin/sessions?${params}`);
    const sessions = data.sessions || [];
    sessionsTotal = data.total ?? sessions.length;

    if (meta) {
      meta.textContent = sessionsTotal
        ? `共 ${sessionsTotal} 条会话${q || status ? '（已筛选）' : ''}`
        : '暂无会话';
    }

    if (!sessions.length) {
      table.innerHTML = '<p class="history-panel-empty">没有匹配的会话</p>';
    } else {
      table.innerHTML = `
    <table><thead><tr>
      <th>标题</th><th>用户</th><th>状态</th><th>模式</th><th>创建时间</th><th>操作</th>
    </tr></thead>
    <tbody>${sessions
      .map((s) => {
        const ownerName = s.owner_username || '';
        const ownerEmail = s.owner_email || '';
        const ownerCell = ownerName
          ? `${escapeHtml(ownerName)}<br><span class="admin-session-email">${escapeHtml(ownerEmail || s.owner_id || '')}</span>`
          : escapeHtml(ownerEmail || s.owner_id || '—');
        const title = escapeHtml(s.task_title || s.session_id);
        const sid = escapeHtml(s.session_id);
        return `<tr>
        <td><a class="admin-session-view-link" href="${sessionViewHref(s)}">${title}</a><br><code>${sid}</code></td>
        <td>${ownerCell}</td>
        <td>${escapeHtml(s.status_label || s.status || '')}</td>
        <td>${escapeHtml(s.mode || 'build')}</td>
        <td>${formatSessionCreatedAt(s.created_at)}</td>
        <td class="admin-session-actions">
          <a class="btn btn-outline btn-sm" href="${sessionViewHref(s)}">查看</a>
          <button type="button" class="btn btn-outline btn-sm" data-stop="${s.session_id}">停止</button>
          <button type="button" class="btn btn-outline btn-sm" data-del="${s.session_id}">删除</button>
        </td></tr>`;
      })
      .join('')}</tbody></table>`;

      table.querySelectorAll('[data-stop]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          await adminFetch(`/api/v1/admin/sessions/${btn.dataset.stop}/stop`, { method: 'POST' });
          showToast('已停止', 'success');
          loadSessions();
        });
      });
      table.querySelectorAll('[data-del]').forEach((btn) => {
        btn.addEventListener('click', async () => {
          if (!confirm('确定删除此会话？')) return;
          await adminFetch(`/api/v1/admin/sessions/${btn.dataset.del}`, { method: 'DELETE' });
          showToast('已删除', 'success');
          loadSessions();
        });
      });
    }

    if (pageInfo) {
      const end = sessionsOffset + sessions.length;
      pageInfo.textContent =
        sessions.length > 0
          ? `第 ${sessionsOffset + 1}–${end} 条，共 ${sessionsTotal} 条`
          : '无数据';
    }
    document.getElementById('sessionsPrevBtn')?.toggleAttribute('disabled', sessionsOffset <= 0);
    document.getElementById('sessionsNextBtn')?.toggleAttribute(
      'disabled',
      sessionsOffset + sessionsPageSize >= sessionsTotal,
    );
  } catch (err) {
    table.innerHTML = `<p class="history-panel-empty">${escapeHtml(err.message || '加载失败')}</p>`;
  }
}

async function loadPlans() {
  const table = document.getElementById('plansTable');
  const meta = document.getElementById('plansMeta');
  const pageInfo = document.getElementById('plansPageInfo');
  if (!table) return;

  const q = document.getElementById('plansSearch')?.value.trim() || '';
  const status = document.getElementById('plansStatusFilter')?.value || '';
  const params = new URLSearchParams({
    limit: String(plansPageSize),
    offset: String(plansOffset),
  });
  if (q) params.set('q', q);
  if (status) params.set('status', status);

  try {
    const data = await adminFetch(`/api/v1/admin/plans?${params}`);
    const plans = data.plans || [];
    plansTotal = data.total ?? plans.length;

    if (meta) {
      meta.textContent = plansTotal
        ? `共 ${plansTotal} 条规划${q || status ? '（已筛选）' : ''}`
        : '暂无规划';
    }

    if (!plans.length) {
      table.innerHTML = '<p class="history-panel-empty">没有匹配的规划</p>';
    } else {
      table.innerHTML = `
    <table><thead><tr>
      <th>标题</th><th>用户</th><th>状态</th><th>轮次</th><th>创建时间</th><th>操作</th>
    </tr></thead>
    <tbody>${plans
      .map((p) => {
        const ownerName = p.owner_username || '';
        const ownerEmail = p.owner_email || '';
        const ownerCell = ownerName
          ? `${escapeHtml(ownerName)}<br><span class="admin-session-email">${escapeHtml(ownerEmail || p.owner_id || '')}</span>`
          : escapeHtml(ownerEmail || p.owner_id || '—');
        const title = escapeHtml(p.task_title || p.plan_id);
        const pid = escapeHtml(p.plan_id);
        return `<tr>
        <td><a class="admin-session-view-link" href="${planViewHref(p)}">${title}</a><br><code>${pid}</code></td>
        <td>${ownerCell}</td>
        <td>${escapeHtml(p.status_label || p.status || '')}</td>
        <td>${escapeHtml(String(p.turn_count ?? 0))}</td>
        <td>${formatSessionCreatedAt(p.created_at)}</td>
        <td class="admin-session-actions">
          <a class="btn btn-outline btn-sm" href="${planViewHref(p)}">查看</a>
        </td></tr>`;
      })
      .join('')}</tbody></table>`;
    }

    if (pageInfo) {
      const end = plansOffset + plans.length;
      pageInfo.textContent =
        plans.length > 0
          ? `第 ${plansOffset + 1}–${end} 条，共 ${plansTotal} 条`
          : '无数据';
    }
    document.getElementById('plansPrevBtn')?.toggleAttribute('disabled', plansOffset <= 0);
    document.getElementById('plansNextBtn')?.toggleAttribute(
      'disabled',
      plansOffset + plansPageSize >= plansTotal,
    );
  } catch (err) {
    table.innerHTML = `<p class="history-panel-empty">${escapeHtml(err.message || '加载失败')}</p>`;
  }
}

async function loadPlanLlm() {
  const cfg = await adminFetch('/api/v1/admin/plan-llm');
  document.getElementById('planLlmEnabled').checked = cfg.enabled !== false;
  document.getElementById('planLlmBaseUrl').value = cfg.base_url || '';
  document.getElementById('planLlmModel').value = cfg.model || '';
  document.getElementById('planLlmSystemPrompt').value = cfg.system_prompt || '';
  document.getElementById('planLlmFinalizeSystemPrompt').value = cfg.finalize_system_prompt || '';
  document.getElementById('planLlmTemperature').value = cfg.temperature ?? 0.4;
  const keyInput = document.getElementById('planLlmApiKey');
  keyInput.value = '';
  if (cfg.api_key_masked) {
    keyInput.placeholder = `已配置 (${cfg.api_key_masked})，留空则不修改`;
  } else {
    keyInput.placeholder = '必填（未配置时规划页将显示错误并提示重试）';
  }
  const hint = document.getElementById('planLlmStatusHint');
  if (hint) {
    if (!cfg.enabled) {
      hint.textContent = '规划模式 LLM 已禁用，用户将无法生成规划问题与定稿';
    } else if (cfg.configured) {
      hint.textContent = `当前已配置（${cfg.model || '默认模型'}）`;
    } else {
      hint.textContent = '尚未配置 API Key：规划 LLM 调用将失败，用户需在规划页重试';
    }
  }
  return cfg;
}

function updateAdminLlmTabBadges() {
  document.querySelectorAll('.admin-llm-tab-badge[data-hint-for]').forEach((badge) => {
    const hintId = badge.dataset.hintFor;
    const hintEl = document.getElementById(hintId || '');
    const text = hintEl?.textContent || '';
    badge.className = 'admin-llm-tab-badge';
    if (/已禁用|禁用/.test(text)) badge.classList.add('admin-llm-tab-badge--muted');
    else if (/已配置|当前已配置/.test(text)) badge.classList.add('admin-llm-tab-badge--ok');
    else badge.classList.add('admin-llm-tab-badge--warn');
  });
}

function initAdminLlmTabs() {
  const tabs = document.querySelectorAll('.admin-llm-tab[data-llm-tab]');
  tabs.forEach((tab) => {
    tab.addEventListener('click', () => {
      const targetId = tab.dataset.llmTab;
      tabs.forEach((t) => t.classList.toggle('is-active', t === tab));
      document.querySelectorAll('.admin-llm-tab-panel').forEach((panel) => {
        const active = panel.id === targetId;
        panel.classList.toggle('is-active', active);
        panel.hidden = !active;
      });
    });
  });
  updateAdminLlmTabBadges();
}

async function loadModNameSuggest() {
  const cfg = await adminFetch('/api/v1/admin/mod-name-suggest');
  document.getElementById('modNameSuggestEnabled').checked = cfg.enabled !== false;
  document.getElementById('modNameSuggestBaseUrl').value = cfg.base_url || '';
  document.getElementById('modNameSuggestModel').value = cfg.model || '';
  document.getElementById('modNameSuggestSystemPrompt').value = cfg.system_prompt || '';
  document.getElementById('modNameSuggestTemperature').value = cfg.temperature ?? 0.3;
  const keyInput = document.getElementById('modNameSuggestApiKey');
  keyInput.value = '';
  if (cfg.api_key_masked) {
    keyInput.placeholder = `已配置 (${cfg.api_key_masked})，留空则不修改`;
  } else {
    keyInput.placeholder = '必填（未配置时将使用规则回退名称）';
  }
  const hint = document.getElementById('modNameSuggestStatusHint');
  if (hint) {
    if (!cfg.enabled) {
      hint.textContent = 'Mod 名称 LLM 生成已禁用，将使用任务标题回退为英文名称';
    } else if (cfg.configured) {
      hint.textContent = `当前已配置（${cfg.model || '默认模型'}）`;
    } else {
      hint.textContent = '尚未配置 API Key：将使用任务标题回退为英文名称';
    }
  }
}

/** @type {{ active_profile_id: string, profiles: Array<{ id: string, name: string, updated_at?: string, is_active?: boolean }> }} */
let llmProfilesState = { active_profile_id: '', profiles: [] };

/** @type {string | null} */
let llmProfileSelectedId = null;

async function loadAllLlmTabConfigs() {
  await Promise.all([loadLlm(), loadPromptOptimize(), loadModNameSuggest(), loadPlanLlm()]);
  updateAdminLlmTabBadges();
}

function getActiveLlmProfile() {
  const activeId = llmProfilesState.active_profile_id;
  return llmProfilesState.profiles.find((p) => p.id === activeId) || null;
}

function getSelectedLlmProfile() {
  const id = llmProfileSelectedId || llmProfilesState.active_profile_id;
  return llmProfilesState.profiles.find((p) => p.id === id) || getActiveLlmProfile();
}

function updateLlmProfileActions() {
  const active = getActiveLlmProfile();
  const deleteBtn = document.getElementById('llmProfileDeleteBtn');
  const renameBtn = document.getElementById('llmProfileRenameBtn');
  const canDelete = llmProfilesState.profiles.some(
    (p) => p.id !== llmProfilesState.active_profile_id,
  );

  if (renameBtn) {
    renameBtn.disabled = !active;
    renameBtn.title = active ? '' : '暂无生效中的预设';
  }
  if (deleteBtn) {
    deleteBtn.disabled = !canDelete;
    deleteBtn.title = canDelete ? '' : '至少保留一条生效中的预设';
  }
}

function renderLlmProfileChips() {
  const chips = document.getElementById('llmProfileChips');
  const hint = document.getElementById('llmProfilesHint');
  if (!chips) return;

  const profiles = llmProfilesState.profiles || [];
  if (!profiles.length) {
    chips.innerHTML = '';
    if (hint) hint.textContent = '暂无预设';
    updateLlmProfileActions();
    return;
  }

  if (!llmProfileSelectedId || !profiles.some((p) => p.id === llmProfileSelectedId)) {
    llmProfileSelectedId = llmProfilesState.active_profile_id || profiles[0].id;
  }

  const active = getActiveLlmProfile();
  if (hint) {
    hint.textContent = active
      ? `当前生效：${active.name}。点击其他预设可一键切换（将覆盖未保存的表单修改）。`
      : '选择预设以切换四套 LLM 配置';
  }

  chips.innerHTML = profiles
    .map((profile) => {
      const isActive = profile.id === llmProfilesState.active_profile_id;
      const isSelected = profile.id === llmProfileSelectedId;
      const classes = [
        'admin-llm-profile-chip',
        isActive ? 'is-active' : '',
        isSelected && !isActive ? 'is-selected' : '',
      ]
        .filter(Boolean)
        .join(' ');
      return `<button type="button" class="${classes}" data-profile-id="${escapeHtml(profile.id)}" ${
        isActive ? 'aria-current="true"' : ''
      }>
        ${escapeHtml(profile.name)}
        ${isActive ? '<span class="admin-llm-profile-chip-badge">生效中</span>' : ''}
      </button>`;
    })
    .join('');

  chips.querySelectorAll('[data-profile-id]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const profileId = btn.getAttribute('data-profile-id');
      if (!profileId) return;
      if (profileId === llmProfilesState.active_profile_id) {
        llmProfileSelectedId = profileId;
        renderLlmProfileChips();
        return;
      }
      activateLlmProfile(profileId);
    });
  });

  updateLlmProfileActions();
}

async function loadLlmProfiles() {
  const data = await adminFetch('/api/v1/admin/llm-profiles');
  llmProfilesState = {
    active_profile_id: data.active_profile_id || '',
    profiles: data.profiles || [],
  };
  renderLlmProfileChips();
}

async function activateLlmProfile(profileId) {
  if (
    !confirm('切换将覆盖当前生效配置，未保存的表单修改将丢失。继续？')
  ) {
    return;
  }
  try {
    const data = await adminFetch(`/api/v1/admin/llm-profiles/${profileId}/activate`, {
      method: 'POST',
    });
    llmProfilesState = {
      active_profile_id: data.active_profile_id || profileId,
      profiles: data.profiles || [],
    };
    llmProfileSelectedId = profileId;
    await loadAllLlmTabConfigs();
    renderLlmProfileChips();
    showToast('已切换 LLM 配置预设', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
}

/** @type {Array<{ id: string, title: string, description: string, detail: object, enabled: boolean }>} */
let defaultRequirementsItems = [];

function normalizeAdminRequirement(item) {
  return {
    id: String(item?.id || '').trim(),
    title: String(item?.title || '').trim(),
    description: String(item?.description || '').trim(),
    detail: item?.detail && typeof item.detail === 'object' ? { ...item.detail } : {},
    enabled: item?.enabled !== false,
  };
}

function cloneBuiltinDefaultRequirements() {
  return DEFAULT_REQUIREMENTS.map((r) => normalizeAdminRequirement(r));
}

function updateDefaultReqTitleCounter() {
  const counter = document.getElementById('defaultReqTitleCounter');
  const title = document.getElementById('defaultReqTitle')?.value || '';
  if (counter) {
    counter.textContent = `${countChineseChars(title)}/${REQUIREMENT_TITLE_MAX_CHARS} 汉字`;
  }
}

function resetDefaultReqForm() {
  const editId = document.getElementById('defaultReqEditId');
  if (editId) editId.value = '';
  const title = document.getElementById('defaultReqTitle');
  const desc = document.getElementById('defaultReqDescription');
  const prompt = document.getElementById('defaultReqPrompt');
  const enabled = document.getElementById('defaultReqEnabled');
  const cancelBtn = document.getElementById('defaultReqCancelBtn');
  const addBtn = document.getElementById('defaultReqAddBtn');
  if (title) title.value = '';
  if (desc) desc.value = '';
  if (prompt) prompt.value = '';
  if (enabled) enabled.checked = true;
  const cost = document.getElementById('defaultReqCost');
  if (cost) cost.value = 'medium';
  if (cancelBtn) cancelBtn.hidden = true;
  if (addBtn) addBtn.textContent = '添加项';
  updateDefaultReqTitleCounter();
}

function fillDefaultReqForm(item) {
  const editId = document.getElementById('defaultReqEditId');
  if (editId) editId.value = item.id;
  const title = document.getElementById('defaultReqTitle');
  const desc = document.getElementById('defaultReqDescription');
  const prompt = document.getElementById('defaultReqPrompt');
  const enabled = document.getElementById('defaultReqEnabled');
  const cancelBtn = document.getElementById('defaultReqCancelBtn');
  const addBtn = document.getElementById('defaultReqAddBtn');
  if (title) title.value = item.title;
  if (desc) desc.value = item.description;
  if (prompt) prompt.value = item.detail?.custom_prompt || '';
  if (enabled) enabled.checked = item.enabled !== false;
  const cost = document.getElementById('defaultReqCost');
  if (cost) cost.value = normalizeRequirementCost(item.detail?.cost) || 'medium';
  if (cancelBtn) cancelBtn.hidden = false;
  if (addBtn) addBtn.textContent = '保存修改';
  updateDefaultReqTitleCounter();
}

function readDefaultReqFormData() {
  return {
    title: document.getElementById('defaultReqTitle')?.value || '',
    description: document.getElementById('defaultReqDescription')?.value || '',
    customPrompt: document.getElementById('defaultReqPrompt')?.value || '',
    cost: document.getElementById('defaultReqCost')?.value || 'medium',
    enabled: document.getElementById('defaultReqEnabled')?.checked !== false,
  };
}

function applyFormDetail(baseDetail, customPrompt, cost) {
  const detail = { ...(baseDetail || {}) };
  const trimmed = customPrompt?.trim();
  if (trimmed) detail.custom_prompt = trimmed;
  else delete detail.custom_prompt;
  const normalizedCost = normalizeRequirementCost(cost);
  if (normalizedCost) detail.cost = normalizedCost;
  else delete detail.cost;
  return detail;
}

function reorderDefaultRequirements(orderedIds) {
  if (!Array.isArray(orderedIds) || orderedIds.length === 0) return;
  const byId = new Map(defaultRequirementsItems.map((item) => [item.id, item]));
  const reordered = [];
  orderedIds.forEach((id) => {
    const item = byId.get(id);
    if (item) {
      reordered.push(item);
      byId.delete(id);
    }
  });
  byId.forEach((item) => reordered.push(item));
  defaultRequirementsItems = reordered;
}

function renderDefaultRequirementsList() {
  const list = document.getElementById('defaultRequirementsList');
  const empty = document.getElementById('defaultRequirementsListEmpty');
  if (!list) return;
  list.innerHTML = '';
  if (empty) empty.hidden = defaultRequirementsItems.length > 0;
  const sortHint = list.previousElementSibling;
  if (sortHint?.classList.contains('modal-req-sort-hint')) {
    sortHint.hidden = defaultRequirementsItems.length < 2;
  }

  defaultRequirementsItems.forEach((item) => {
    const li = document.createElement('li');
    li.className = 'modal-req-item';
    li.dataset.id = item.id;

    const layout = document.createElement('div');
    layout.className = 'modal-req-item-layout';
    layout.appendChild(createDragHandle());

    const content = document.createElement('div');
    content.className = 'modal-req-item-content';
    const enabledLabel = item.enabled !== false ? '启用' : '禁用';
    content.innerHTML = `
      <div class="modal-req-item-title">${escapeHtml(item.title)} <span class="form-hint">(${enabledLabel})</span></div>
      <div class="modal-req-item-desc">${escapeHtml(item.description || '—')}</div>
      <div class="modal-req-item-actions">
        <button type="button" data-action="edit" data-id="${escapeHtml(item.id)}">编辑</button>
        <button type="button" data-action="delete" data-id="${escapeHtml(item.id)}">删除</button>
      </div>
    `;
    layout.appendChild(content);
    li.appendChild(layout);
    list.appendChild(li);
  });

  list.querySelectorAll('button[data-action]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const id = btn.getAttribute('data-id');
      const action = btn.getAttribute('data-action');
      if (!id) return;
      if (action === 'edit') {
        const item = defaultRequirementsItems.find((r) => r.id === id);
        if (item) fillDefaultReqForm(item);
        return;
      }
      if (action === 'delete') {
        if (!confirm(`确定删除「${id}」？`)) return;
        defaultRequirementsItems = defaultRequirementsItems.filter((r) => r.id !== id);
        if (document.getElementById('defaultReqEditId')?.value === id) resetDefaultReqForm();
        renderDefaultRequirementsList();
      }
    });
  });
}

function initDefaultRequirementsDragSort() {
  const list = document.getElementById('defaultRequirementsList');
  if (!list) return;
  attachDragSort(list, {
    getItemId: (el) => el.dataset.id || '',
    onReorder: (ids) => {
      reorderDefaultRequirements(ids);
    },
  });
}

async function persistDefaultRequirements(items) {
  const result = await adminFetch('/api/v1/admin/default-requirements', {
    method: 'PATCH',
    body: JSON.stringify({ items }),
  });
  defaultRequirementsItems = (result.items || []).map(normalizeAdminRequirement);
  const meta = document.getElementById('defaultRequirementsMeta');
  if (meta) {
    meta.textContent = result.updated_at
      ? `共 ${defaultRequirementsItems.length} 项 · 上次保存 ${result.updated_at}`
      : `共 ${defaultRequirementsItems.length} 项`;
  }
  renderDefaultRequirementsList();
  resetDefaultReqForm();
  return result;
}

async function loadDefaultRequirements() {
  const data = await adminFetch('/api/v1/admin/default-requirements');
  defaultRequirementsItems = (data.items || []).map(normalizeAdminRequirement);
  const meta = document.getElementById('defaultRequirementsMeta');
  if (meta) {
    meta.textContent = data.updated_at
      ? `共 ${defaultRequirementsItems.length} 项 · 上次保存 ${data.updated_at}`
      : `共 ${defaultRequirementsItems.length} 项`;
  }
  renderDefaultRequirementsList();
  resetDefaultReqForm();
}

async function loadAllPanelData() {
  await Promise.all([
    loadOverview(),
    loadSettings(),
    loadLlmProfiles(),
    loadLlm(),
    loadPromptOptimize(),
    loadModNameSuggest(),
    loadPlanLlm(),
    loadDefaultRequirements(),
    loadUsers(),
    loadPlans(),
    loadSessions(),
    loadAuditLogs(),
  ]);
  await loadPromptOptimizeRecords();
  initAdminLlmTabs();
}

const optimizeRecordsLimit = 20;
let optimizeRecordsOffset = 0;

async function loadPromptOptimizeRecords() {
  const table = document.getElementById('optimizeRecordsTable');
  const meta = document.getElementById('optimizeRecordsMeta');
  const pageInfo = document.getElementById('optimizeRecordsPageInfo');
  if (!table) return;

  const userId = document.getElementById('optimizeRecordsUserId')?.value.trim() || '';
  const level = document.getElementById('optimizeRecordsLevel')?.value || '';
  const params = new URLSearchParams({
    limit: String(optimizeRecordsLimit),
    offset: String(optimizeRecordsOffset),
    category: 'prompt',
    action: 'prompt.optimize',
  });
  if (userId) params.set('user_id', userId);
  if (level) params.set('level', level);

  try {
    const data = await adminFetch(`/api/v1/admin/audit-logs?${params}`);
    const items = data.items || [];
    const total = data.total ?? items.length;

    if (meta) {
      meta.textContent = total
        ? `共 ${total} 条记录${userId || level ? '（已筛选）' : ''}`
        : '暂无描述优化记录';
    }

    if (!items.length) {
      table.innerHTML = '<p class="history-panel-empty">暂无描述优化记录</p>';
    } else {
      table.innerHTML = `
        <table class="admin-optimize-records-table"><thead><tr>
          <th>时间</th><th>用户</th><th>模型</th><th>输入</th><th>输出</th><th>状态</th><th></th>
        </tr></thead><tbody>${items
          .map((e, idx) => {
            const detail = e.detail || {};
            const success = detail.success !== false && e.level !== 'error';
            const outputPreview = truncateText(detail.output || '');
            const thinkingLen = detail.thinking_len ?? (detail.thinking || '').length;
            const rowId = `optimize-record-${optimizeRecordsOffset + idx}`;
            return `<tr>
              <td>${(e.ts || '').replace('T', ' ').slice(0, 19)}</td>
              <td>${formatUserLabel(e.user_id)}</td>
              <td>${escapeHtml(detail.model || '—')}</td>
              <td>${detail.input_len ?? '—'} 字</td>
              <td class="admin-optimize-output-preview">${escapeHtml(outputPreview || '—')}</td>
              <td><span class="admin-optimize-status admin-optimize-status--${success ? 'ok' : 'fail'}">${success ? '成功' : '失败'}</span></td>
              <td><button type="button" class="btn btn-outline btn-sm" data-toggle-record="${rowId}">详情</button></td>
            </tr>
            <tr class="admin-optimize-detail-row" id="${rowId}" hidden>
              <td colspan="7">
                <div class="admin-optimize-detail">
                  ${detail.error ? `<p class="admin-optimize-detail-error"><strong>错误：</strong>${escapeHtml(detail.error)}</p>` : ''}
                  <p class="admin-optimize-detail-meta">输出 ${detail.output_len ?? (detail.output || '').length} 字${thinkingLen ? ` · Thinking ${thinkingLen} 字` : ''}</p>
                  ${
                    detail.output
                      ? `<details open><summary>优化结果</summary><pre class="admin-optimize-detail-pre">${escapeHtml(detail.output)}</pre></details>`
                      : ''
                  }
                  ${
                    detail.thinking
                      ? `<details><summary>Thinking</summary><pre class="admin-optimize-detail-pre">${escapeHtml(detail.thinking)}</pre></details>`
                      : ''
                  }
                </div>
              </td>
            </tr>`;
          })
          .join('')}</tbody></table>`;

      table.querySelectorAll('[data-toggle-record]').forEach((btn) => {
        btn.addEventListener('click', () => {
          const row = document.getElementById(btn.dataset.toggleRecord);
          if (!row) return;
          const open = row.hasAttribute('hidden');
          row.toggleAttribute('hidden', !open);
          btn.textContent = open ? '收起' : '详情';
        });
      });
    }

    if (pageInfo) {
      const end = optimizeRecordsOffset + items.length;
      pageInfo.textContent =
        items.length > 0
          ? `第 ${optimizeRecordsOffset + 1}–${end} 条，共 ${total} 条`
          : '无数据';
    }
    document.getElementById('optimizeRecordsPrevBtn')?.toggleAttribute(
      'disabled',
      optimizeRecordsOffset <= 0,
    );
    document.getElementById('optimizeRecordsNextBtn')?.toggleAttribute(
      'disabled',
      optimizeRecordsOffset + optimizeRecordsLimit >= total,
    );
  } catch (err) {
    table.innerHTML = `<p class="history-panel-empty">${escapeHtml(err.message || '加载失败')}</p>`;
  }
}

let auditOffset = 0;
const auditLimit = 50;

async function loadAuditLogs() {
  const table = document.getElementById('auditLogsTable');
  const pageInfo = document.getElementById('auditPageInfo');
  if (!table) return;

  const category = document.getElementById('auditCategory')?.value || '';
  const sessionId = document.getElementById('auditSessionId')?.value.trim() || '';
  const params = new URLSearchParams({
    limit: String(auditLimit),
    offset: String(auditOffset),
  });
  if (category) params.set('category', category);
  if (sessionId) params.set('session_id', sessionId);

  try {
    const data = await adminFetch(`/api/v1/admin/audit-logs?${params}`);
    const items = data.items || [];
    if (!items.length) {
      table.innerHTML = '<p class="history-panel-empty">暂无操作日志</p>';
    } else {
      table.innerHTML = `
        <table><thead><tr>
          <th>时间</th><th>级别</th><th>分类</th><th>操作</th><th>用户</th><th>会话</th><th>摘要</th>
        </tr></thead><tbody>${items
          .map(
            (e) => `<tr>
              <td>${(e.ts || '').replace('T', ' ').slice(0, 19)}</td>
              <td>${e.level || ''}</td>
              <td>${e.category || ''}</td>
              <td>${e.action || ''}</td>
              <td>${e.user_id || '—'}</td>
              <td>${e.session_id ? `<code>${e.session_id}</code>` : '—'}</td>
              <td>${e.message || ''}</td>
            </tr>`,
          )
          .join('')}</tbody></table>`;
    }
    if (pageInfo) {
      const end = auditOffset + items.length;
      pageInfo.textContent = `第 ${auditOffset + 1}–${end} 条，共 ${data.total ?? items.length} 条匹配`;
    }
    document.getElementById('auditPrevBtn')?.toggleAttribute('disabled', auditOffset <= 0);
    document.getElementById('auditNextBtn')?.toggleAttribute(
      'disabled',
      auditOffset + auditLimit >= (data.total ?? 0),
    );
  } catch (err) {
    table.innerHTML = `<p class="history-panel-empty">${err.message || '加载失败'}</p>`;
  }
}

document.getElementById('adminLoginForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const password = document.getElementById('adminPassword')?.value || '';
  try {
    const res = await fetch('/api/v1/admin/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || '口令错误');
    setAdminPanelToken(data.token);
    showPanel();
    await loadAllPanelData();
    showToast('已进入管理后台', 'success');
  } catch (err) {
    showToast(err.message || '登录失败', 'error');
  }
});

document.getElementById('adminLogoutBtn')?.addEventListener('click', () => {
  setAdminPanelToken(null);
  showLogin();
});

document.getElementById('settingsForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  try {
    await adminFetch('/api/v1/admin/settings', {
      method: 'PATCH',
      body: JSON.stringify({
        registration_enabled: document.getElementById('registrationEnabled').checked,
        shared_llm_enabled: document.getElementById('sharedLlmEnabled').checked,
        beta_banner_enabled: document.getElementById('betaBannerEnabled').checked,
        beta_banner_text: document.getElementById('betaBannerText').value,
        feedback_email: document.getElementById('feedbackEmail').value,
      }),
    });
    showToast('站点设置已保存', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('llmForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {
    base_url: document.getElementById('llmBaseUrl').value.trim(),
    model: document.getElementById('llmModel').value.trim(),
    subagent_model: document.getElementById('llmSubagent').value.trim(),
  };
  const key = document.getElementById('llmApiKey').value.trim();
  if (key) body.api_key = key;

  try {
    const result = await adminFetch('/api/v1/admin/llm', {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    showToast(result.configured ? 'LLM 配置已保存' : '已保存', 'success');
    await loadLlm();
    updateAdminLlmTabBadges();
    await loadLlmProfiles();
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('promptOptimizeForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {
    enabled: document.getElementById('promptOptimizeEnabled').checked,
    base_url: document.getElementById('promptOptimizeBaseUrl').value.trim(),
    model: document.getElementById('promptOptimizeModel').value.trim(),
    system_prompt: document.getElementById('promptOptimizeSystemPrompt').value.trim(),
    reasoning_effort: document.getElementById('promptOptimizeReasoningEffort').value,
    thinking_enabled: document.getElementById('promptOptimizeThinkingEnabled').checked,
  };
  const key = document.getElementById('promptOptimizeApiKey').value.trim();
  if (key) body.api_key = key;

  try {
    await adminFetch('/api/v1/admin/prompt-optimize', {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    showToast('描述优化配置已保存', 'success');
    await loadPromptOptimize();
    updateAdminLlmTabBadges();
    await loadLlmProfiles();
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('defaultReqTitle')?.addEventListener('input', updateDefaultReqTitleCounter);

document.getElementById('defaultReqCancelBtn')?.addEventListener('click', resetDefaultReqForm);

document.getElementById('defaultReqAddBtn')?.addEventListener('click', () => {
  const data = readDefaultReqFormData();
  const validation = validateTitle(data.title);
  if (!validation.valid) {
    showToast(validation.message, 'error');
    return;
  }
  const editId = document.getElementById('defaultReqEditId')?.value?.trim();
  if (editId) {
    const index = defaultRequirementsItems.findIndex((r) => r.id === editId);
    if (index === -1) {
      showToast('未找到该项', 'error');
      return;
    }
    const prev = defaultRequirementsItems[index];
    defaultRequirementsItems[index] = normalizeAdminRequirement({
      ...prev,
      title: data.title.trim(),
      description: data.description.trim(),
      detail: applyFormDetail(prev.detail, data.customPrompt, data.cost),
      enabled: data.enabled,
    });
  } else {
    defaultRequirementsItems.push(
      normalizeAdminRequirement({
        id: generateRequirementId(),
        title: data.title.trim(),
        description: data.description.trim(),
        detail: applyFormDetail({}, data.customPrompt, data.cost),
        enabled: data.enabled,
      }),
    );
  }
  renderDefaultRequirementsList();
  resetDefaultReqForm();
  showToast(editId ? '已更新列表（请点击「保存到服务器」）' : '已添加（请点击「保存到服务器」）', 'info');
});

document.getElementById('defaultReqRestoreBuiltinBtn')?.addEventListener('click', async () => {
  if (!confirm('确定恢复为内置默认要求列表？将立即写入服务器。')) return;
  try {
    await persistDefaultRequirements(cloneBuiltinDefaultRequirements());
    showToast('已恢复内置默认', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('defaultReqSaveBtn')?.addEventListener('click', async () => {
  if (defaultRequirementsItems.length === 0) {
    showToast('至少保留一项默认要求', 'error');
    return;
  }
  try {
    await persistDefaultRequirements(defaultRequirementsItems);
    showToast('默认其他要求已保存', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('modNameSuggestForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {
    enabled: document.getElementById('modNameSuggestEnabled').checked,
    base_url: document.getElementById('modNameSuggestBaseUrl').value.trim(),
    model: document.getElementById('modNameSuggestModel').value.trim(),
    system_prompt: document.getElementById('modNameSuggestSystemPrompt').value.trim(),
    temperature: Number(document.getElementById('modNameSuggestTemperature').value),
  };
  const key = document.getElementById('modNameSuggestApiKey').value.trim();
  if (key) body.api_key = key;

  try {
    await adminFetch('/api/v1/admin/mod-name-suggest', {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    showToast('Mod 名称生成配置已保存', 'success');
    await loadModNameSuggest();
    updateAdminLlmTabBadges();
    await loadLlmProfiles();
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('planLlmForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {
    enabled: document.getElementById('planLlmEnabled').checked,
    base_url: document.getElementById('planLlmBaseUrl').value.trim(),
    model: document.getElementById('planLlmModel').value.trim(),
    system_prompt: document.getElementById('planLlmSystemPrompt').value.trim(),
    finalize_system_prompt: document.getElementById('planLlmFinalizeSystemPrompt').value.trim(),
    temperature: Number(document.getElementById('planLlmTemperature').value),
  };
  const key = document.getElementById('planLlmApiKey').value.trim();
  if (key) body.api_key = key;

  try {
    await adminFetch('/api/v1/admin/plan-llm', {
      method: 'PATCH',
      body: JSON.stringify(body),
    });
    showToast('规划 LLM 配置已保存', 'success');
    await loadPlanLlm();
    updateAdminLlmTabBadges();
    await loadLlmProfiles();
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('llmProfileSaveAsBtn')?.addEventListener('click', async () => {
  const name = window.prompt('请输入新预设名称');
  if (name == null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    showToast('预设名称不能为空', 'error');
    return;
  }
  try {
    const data = await adminFetch('/api/v1/admin/llm-profiles', {
      method: 'POST',
      body: JSON.stringify({ name: trimmed }),
    });
    llmProfilesState = {
      active_profile_id: data.active_profile_id || llmProfilesState.active_profile_id,
      profiles: data.profiles || [],
    };
    const created = llmProfilesState.profiles.find((p) => p.name === trimmed);
    if (created) llmProfileSelectedId = created.id;
    renderLlmProfileChips();
    showToast('已另存为预设', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('llmProfileRenameBtn')?.addEventListener('click', async () => {
  const active = getActiveLlmProfile();
  if (!active) {
    showToast('暂无生效中的预设', 'error');
    return;
  }
  const name = window.prompt('请输入新名称', active.name);
  if (name == null) return;
  const trimmed = name.trim();
  if (!trimmed) {
    showToast('预设名称不能为空', 'error');
    return;
  }
  try {
    const data = await adminFetch(`/api/v1/admin/llm-profiles/${active.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ name: trimmed }),
    });
    llmProfilesState = {
      active_profile_id: data.active_profile_id || llmProfilesState.active_profile_id,
      profiles: data.profiles || [],
    };
    llmProfileSelectedId = active.id;
    renderLlmProfileChips();
    showToast('预设已重命名', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('llmProfileDeleteBtn')?.addEventListener('click', async () => {
  const deletable = llmProfilesState.profiles.filter(
    (p) => p.id !== llmProfilesState.active_profile_id,
  );
  if (!deletable.length) {
    showToast('至少保留一条生效中的预设，当前无可删除项', 'error');
    return;
  }

  let target = getSelectedLlmProfile();
  if (!target || target.id === llmProfilesState.active_profile_id) {
    if (deletable.length === 1) {
      target = deletable[0];
    } else {
      const options = deletable.map((p, index) => `${index + 1}. ${p.name}`).join('\n');
      const pick = window.prompt(`选择要删除的预设（输入序号）:\n${options}`);
      if (pick == null) return;
      const index = Number.parseInt(pick, 10) - 1;
      if (!Number.isFinite(index) || index < 0 || index >= deletable.length) {
        showToast('无效的选择', 'error');
        return;
      }
      target = deletable[index];
    }
  }

  if (!confirm(`确定删除预设「${target.name}」？`)) return;
  try {
    const data = await adminFetch(`/api/v1/admin/llm-profiles/${target.id}`, {
      method: 'DELETE',
    });
    llmProfilesState = {
      active_profile_id: data.active_profile_id || llmProfilesState.active_profile_id,
      profiles: data.profiles || [],
    };
    llmProfileSelectedId = llmProfilesState.active_profile_id;
    renderLlmProfileChips();
    showToast('预设已删除', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('optimizeRecordsRefreshBtn')?.addEventListener('click', () => {
  optimizeRecordsOffset = 0;
  loadPromptOptimizeRecords();
});
document.getElementById('optimizeRecordsLevel')?.addEventListener('change', () => {
  optimizeRecordsOffset = 0;
  loadPromptOptimizeRecords();
});
document.getElementById('optimizeRecordsUserId')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    optimizeRecordsOffset = 0;
    loadPromptOptimizeRecords();
  }
});
document.getElementById('optimizeRecordsPrevBtn')?.addEventListener('click', () => {
  optimizeRecordsOffset = Math.max(0, optimizeRecordsOffset - optimizeRecordsLimit);
  loadPromptOptimizeRecords();
});
document.getElementById('optimizeRecordsNextBtn')?.addEventListener('click', () => {
  optimizeRecordsOffset += optimizeRecordsLimit;
  loadPromptOptimizeRecords();
});

document.getElementById('auditRefreshBtn')?.addEventListener('click', () => {
  auditOffset = 0;
  loadAuditLogs();
});
document.getElementById('auditCategory')?.addEventListener('change', () => {
  auditOffset = 0;
  loadAuditLogs();
});
document.getElementById('auditSessionId')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    auditOffset = 0;
    loadAuditLogs();
  }
});
document.getElementById('auditPrevBtn')?.addEventListener('click', () => {
  auditOffset = Math.max(0, auditOffset - auditLimit);
  loadAuditLogs();
});
document.getElementById('auditNextBtn')?.addEventListener('click', () => {
  auditOffset += auditLimit;
  loadAuditLogs();
});

document.getElementById('sessionsRefreshBtn')?.addEventListener('click', () => {
  sessionsOffset = 0;
  loadSessions();
});
document.getElementById('sessionsSearch')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    sessionsOffset = 0;
    loadSessions();
  }
});
document.getElementById('sessionsStatusFilter')?.addEventListener('change', () => {
  sessionsOffset = 0;
  loadSessions();
});
document.getElementById('sessionsPrevBtn')?.addEventListener('click', () => {
  sessionsOffset = Math.max(0, sessionsOffset - sessionsPageSize);
  loadSessions();
});
document.getElementById('sessionsNextBtn')?.addEventListener('click', () => {
  sessionsOffset += sessionsPageSize;
  loadSessions();
});

document.getElementById('plansRefreshBtn')?.addEventListener('click', () => {
  plansOffset = 0;
  loadPlans();
});
document.getElementById('plansSearch')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') {
    plansOffset = 0;
    loadPlans();
  }
});
document.getElementById('plansStatusFilter')?.addEventListener('change', () => {
  plansOffset = 0;
  loadPlans();
});
document.getElementById('plansPrevBtn')?.addEventListener('click', () => {
  plansOffset = Math.max(0, plansOffset - plansPageSize);
  loadPlans();
});
document.getElementById('plansNextBtn')?.addEventListener('click', () => {
  plansOffset += plansPageSize;
  loadPlans();
});

async function boot() {
  initDefaultRequirementsDragSort();
  if (getAdminPanelToken()) {
    showPanel();
    try {
      await loadAllPanelData();
    } catch (err) {
      showToast(err.message, 'error');
    }
  } else {
    showLogin();
  }
}

boot();
