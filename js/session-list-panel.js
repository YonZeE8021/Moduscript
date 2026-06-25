/** @file 对话列表侧栏 — 对话与构想合并 / 置顶 / 回收站 */

import {
  fetchSessionList,
  patchSessionMeta,
  restoreSession,
  trashSession,
} from './session-api.js';
import { fetchPlanList, patchPlanMeta, restorePlan, trashPlan } from './plan-api.js';
import {
  loadSessionList,
  replaceSessionListFromRemote,
  upsertSessionListEntry,
} from './session-utils.js';
import { openSidebarItemMenu } from './sidebar-item-menu.js';
import {
  getActiveDraft,
  listDrafts,
  switchDraft,
  createDraft,
  renameDraft,
  deleteDraft,
  toggleDraftPin,
} from './concept-drafts.js';
import { isGuest } from './auth.js';

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

let panelBody = null;
let panelTitle = null;
let footerBtn = null;
let newConceptBtn = null;
let viewMode = 'active';
let activeSessionId = null;
let showToastFn = (msg) => window.alert(msg);
let onSessionNavigate = null;
let onConceptNavigate = null;
let onBeforeConceptSwitch = null;
let onAfterConceptSwitch = null;
let includeConcepts = true;
/** 仅主页构想页高亮当前构想；编写/规划页只高亮对应对话 */
let highlightActiveConcept = true;

function mergeRemoteSessions(sessions) {
  replaceSessionListFromRemote(sessions, { recycled: viewMode === 'recycled' });
}

function sortSessions(items) {
  return [...items].sort((a, b) => {
    const ap = a.pinned ? 1 : 0;
    const bp = b.pinned ? 1 : 0;
    if (ap !== bp) return bp - ap;
    return (b.createdAt || 0) - (a.createdAt || 0);
  });
}

function filteredSessions() {
  const all = loadSessionList();
  if (viewMode === 'recycled') {
    return sortSessions(all.filter((s) => s.deletedAt));
  }
  return sortSessions(all.filter((s) => !s.deletedAt));
}

/** @param {{ kind: 'session' | 'concept', session?: object, draft?: object }} row */
function rowPinned(row) {
  if (row.kind === 'concept') return Boolean(row.draft?.pinned);
  return Boolean(row.session?.pinned);
}

/** @param {{ kind: 'session' | 'concept', session?: object, draft?: object }} row */
function rowSortAt(row) {
  if (row.kind === 'concept') {
    return row.draft?.accessedAt || row.draft?.updatedAt || 0;
  }
  return row.session?.createdAt || 0;
}

/** @param {{ kind: 'session' | 'concept', session?: object, draft?: object }} a @param {typeof a} b */
function compareRows(a, b) {
  const ap = rowPinned(a) ? 1 : 0;
  const bp = rowPinned(b) ? 1 : 0;
  if (ap !== bp) return bp - ap;
  return rowSortAt(b) - rowSortAt(a);
}

function buildMergedItems() {
  const rows = filteredSessions().map((s) => ({ kind: 'session', session: s }));

  if (includeConcepts && viewMode === 'active' && !isGuest()) {
    listDrafts().forEach((d) => rows.push({ kind: 'concept', draft: d }));
  }

  return rows.sort(compareRows);
}

function upsertPlanListEntries(plans, { recycled = false } = {}) {
  plans.forEach((p) => {
    upsertSessionListEntry({
      sessionId: p.plan_id,
      taskTitle: p.task_title || '未命名规划',
      mode: 'plan',
      status: p.status || '',
      createdAt: Date.parse(p.updated_at || p.created_at) || Date.now(),
      pinned: Boolean(p.pinned),
      deletedAt: recycled ? p.deleted_at || new Date(0).toISOString() : p.deleted_at || null,
    });
  });
}

async function refreshList() {
  if (!panelBody) return;
  if (!isGuest()) {
    if (viewMode === 'recycled') {
      try {
        const remote = await fetchSessionList(true);
        mergeRemoteSessions(remote.sessions);
      } catch {
        /* keep local */
      }
      try {
        const plans = await fetchPlanList(true);
        upsertPlanListEntries(plans, { recycled: true });
      } catch {
        /* keep local */
      }
    } else {
      try {
        const remote = await fetchSessionList(false);
        mergeRemoteSessions(remote.sessions);
      } catch {
        /* keep local */
      }
      try {
        const plans = await fetchPlanList(false);
        window.__planListCache = plans;
        upsertPlanListEntries(plans);
      } catch {
        /* keep local */
      }
    }
  }
  renderList();
}

function isPlanSession(item) {
  return item.mode === 'plan' || String(item.sessionId || '').startsWith('plan-');
}

function renderSessionRow(item, list) {
  const li = document.createElement('li');
  const typeClass = item.mode === 'plan' ? 'session-history-item--plan' : 'session-history-item--build';
  li.className = `session-history-item ${typeClass}`;
  if (item.sessionId === activeSessionId) li.classList.add('is-active');
  if (item.pinned) li.classList.add('is-pinned');

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'session-history-main';
  const kind = item.mode === 'plan' ? 'plan' : 'build';
  const kindLabel = kind === 'plan' ? '规划' : '编写';
  const statusHtml = item.status
    ? `<span class="session-history-meta-sep" aria-hidden="true">·</span><span class="session-history-meta-detail">${escapeHtml(item.status)}</span>`
    : '';
  main.innerHTML = `
    <span class="session-history-title">${escapeHtml(item.taskTitle || '未命名任务')}</span>
    <span class="session-history-meta">
      <span class="session-history-kind session-history-kind--${kind}">${kindLabel}</span>${statusHtml}
    </span>
  `;
  main.addEventListener('click', () => {
    if (viewMode === 'recycled') return;
    if (onSessionNavigate) onSessionNavigate(item);
    else if (item.mode === 'plan' || String(item.sessionId || '').startsWith('plan-')) {
      window.location.href = `/plan.html?plan_id=${encodeURIComponent(item.sessionId)}`;
    } else {
      window.location.href = `/session.html?session_id=${encodeURIComponent(item.sessionId)}`;
    }
  });

  const actions = document.createElement('div');
  actions.className = 'sidebar-item-actions';

  if (viewMode === 'recycled') {
    const restoreBtn = document.createElement('button');
    restoreBtn.type = 'button';
    restoreBtn.className = 'sidebar-item-menu-trigger';
    restoreBtn.title = '恢复';
    restoreBtn.textContent = '恢复';
    restoreBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      try {
        if (isPlanSession(item)) await restorePlan(item.sessionId);
        else await restoreSession(item.sessionId);
        upsertSessionListEntry({ ...item, deletedAt: null });
        showToastFn('已恢复对话', 'success');
        await refreshList();
      } catch (err) {
        showToastFn(err.message || '恢复失败', 'error');
      }
    });
    actions.appendChild(restoreBtn);
  } else {
    const menuBtn = document.createElement('button');
    menuBtn.type = 'button';
    menuBtn.className = 'sidebar-item-menu-trigger';
    menuBtn.title = '更多';
    menuBtn.setAttribute('aria-label', '更多操作');
    menuBtn.textContent = '⋯';
    menuBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const planRow = isPlanSession(item);
      openSidebarItemMenu(
        menuBtn,
        [
          {
            label: item.pinned ? '取消置顶' : '置顶',
            action: async () => {
              if (planRow) await patchPlanMeta(item.sessionId, { pinned: !item.pinned });
              else await patchSessionMeta(item.sessionId, { pinned: !item.pinned });
              upsertSessionListEntry({ ...item, pinned: !item.pinned });
              await refreshList();
            },
          },
          {
            label: '重命名',
            action: async () => {
              const next = window.prompt('重命名对话', item.taskTitle || '');
              if (!next?.trim()) return;
              if (planRow) await patchPlanMeta(item.sessionId, { task_title: next.trim() });
              else await patchSessionMeta(item.sessionId, { title: next.trim() });
              upsertSessionListEntry({ ...item, taskTitle: next.trim() });
              await refreshList();
              showToastFn('已重命名', 'success');
            },
          },
          {
            label: '移至回收站',
            action: async () => {
              if (!window.confirm('确定将此对话移至回收站？')) return;
              const trashedAt = new Date().toISOString();
              upsertSessionListEntry({
                ...item,
                deletedAt: trashedAt,
                pinned: false,
              });
              renderList();
              try {
                if (planRow) await trashPlan(item.sessionId);
                else await trashSession(item.sessionId);
                showToastFn('已移至回收站', 'info');
                await refreshList();
              } catch (err) {
                upsertSessionListEntry({ ...item, deletedAt: null, pinned: item.pinned });
                renderList();
                showToastFn(err.message || '移入回收站失败', 'error');
              }
            },
          },
        ],
        li,
      );
    });
    actions.appendChild(menuBtn);
  }

  li.append(main, actions);
  list.appendChild(li);
}

function selectConcept(id) {
  onBeforeConceptSwitch?.();
  switchDraft(id);
  onAfterConceptSwitch?.();
  if (onConceptNavigate) {
    onConceptNavigate(id);
    return;
  }
  updateConceptActiveState();
}

function updateConceptActiveState() {
  if (!panelBody || !highlightActiveConcept) return;
  const activeId = getActiveDraft()?.id;
  panelBody.querySelectorAll('.session-history-item--concept').forEach((li) => {
    li.classList.toggle('is-active', li.dataset.draftId === activeId);
  });
}

function renderConceptRow(draft, list) {
  const active = getActiveDraft();
  const li = document.createElement('li');
  li.className = 'session-history-item session-history-item--concept';
  li.dataset.draftId = draft.id;
  if (highlightActiveConcept && draft.id === active?.id) li.classList.add('is-active');
  if (draft.pinned) li.classList.add('is-pinned');

  const main = document.createElement('button');
  main.type = 'button';
  main.className = 'session-history-main';
  main.innerHTML = `
    <span class="session-history-title">${escapeHtml(draft.title || '未命名构想')}</span>
    <span class="session-history-meta">
      <span class="session-history-kind session-history-kind--concept">构想</span>
    </span>
  `;
  main.addEventListener('click', () => {
    selectConcept(draft.id);
  });

  const actions = document.createElement('div');
  actions.className = 'sidebar-item-actions';
  const menuBtn = document.createElement('button');
  menuBtn.type = 'button';
  menuBtn.className = 'sidebar-item-menu-trigger';
  menuBtn.title = '更多';
  menuBtn.setAttribute('aria-label', '更多操作');
  menuBtn.textContent = '⋯';
  menuBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    openSidebarItemMenu(
      menuBtn,
      [
        {
          label: draft.pinned ? '取消置顶' : '置顶',
          action: () => {
            toggleDraftPin(draft.id);
            renderList();
          },
        },
        {
          label: '重命名',
          action: () => {
            const next = window.prompt('重命名构想', draft.title);
            if (!next?.trim()) return;
            renameDraft(draft.id, next.trim());
            renderList();
          },
        },
        {
          label: '删除',
          action: () => {
            if (!deleteDraft(draft.id)) {
              showToastFn('至少保留一个构想', 'error');
              return;
            }
            onAfterConceptSwitch?.();
            renderList();
          },
        },
      ],
      li,
    );
  });
  actions.appendChild(menuBtn);
  li.append(main, actions);
  list.appendChild(li);
}

function renderList() {
  if (!panelBody) return;

  if (panelTitle) {
    panelTitle.textContent = viewMode === 'recycled' ? '回收站' : '对话列表';
  }
  if (footerBtn) {
    footerBtn.textContent = viewMode === 'recycled' ? '返回对话列表' : '回收站';
  }
  if (newConceptBtn) {
    newConceptBtn.hidden = viewMode === 'recycled';
  }

  const items = viewMode === 'recycled'
    ? filteredSessions().map((s) => ({ kind: 'session', session: s }))
    : buildMergedItems();

  if (!items.length) {
    panelBody.innerHTML = `<p class="history-panel-empty">${viewMode === 'recycled' ? '回收站为空' : '暂无记录'}</p>`;
    return;
  }

  const list = document.createElement('ul');
  list.className = 'session-history-list';

  items.forEach((row) => {
    if (row.kind === 'concept') {
      renderConceptRow(row.draft, list);
    } else {
      renderSessionRow(row.session, list);
    }
  });

  panelBody.innerHTML = '';
  panelBody.appendChild(list);
}

export function initSessionListPanel({
  bodyEl,
  titleEl,
  footerEl,
  newConceptBtnEl,
  sessionId = null,
  showToast,
  onSessionNavigate: sessionNavigate,
  onConceptNavigate: conceptNavigate,
  onBeforeConceptSwitch: beforeConceptSwitch,
  onAfterConceptSwitch: afterConceptSwitch,
  includeConcepts: withConcepts = true,
} = {}) {
  panelBody = bodyEl;
  panelTitle = titleEl;
  footerBtn = footerEl;
  newConceptBtn = newConceptBtnEl;
  activeSessionId = sessionId;
  highlightActiveConcept = sessionId == null && !conceptNavigate;
  if (showToast) showToastFn = showToast;
  onSessionNavigate = sessionNavigate || null;
  onConceptNavigate = conceptNavigate || null;
  onBeforeConceptSwitch = beforeConceptSwitch || null;
  onAfterConceptSwitch = afterConceptSwitch || null;
  includeConcepts = withConcepts;

  footerBtn?.addEventListener('click', () => {
    viewMode = viewMode === 'recycled' ? 'active' : 'recycled';
    refreshList();
  });

  newConceptBtn?.addEventListener('click', () => {
    if (isGuest()) {
      const next = encodeURIComponent(window.location.pathname + window.location.search);
      window.location.href = `/login.html?next=${next}`;
      return;
    }
    onBeforeConceptSwitch?.();
    const draft = createDraft(`构想 ${listDrafts().length + 1}`);
    if (onConceptNavigate) {
      onConceptNavigate(draft.id);
      return;
    }
    onAfterConceptSwitch?.();
    renderList();
  });
}

export async function renderSessionListPanel() {
  await refreshList();
}

export function setSessionListActiveId(id) {
  activeSessionId = id;
  renderList();
}
