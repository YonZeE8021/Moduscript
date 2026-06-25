/** @file 编写中页 — 连接 mock 后端并动态渲染会话 */

import { renderMarkdown } from './markdown-render.js';
import {
  SESSION_STORAGE_BOOTSTRAP,
} from './mock-session-bootstrap.js';
import {
  deriveTaskTitle,
  upsertSessionListEntry,
} from './session-utils.js';
import {
  fetchSessionSnapshot,
  isAdminViewMode,
  patchSessionTitle,
  sendSessionMessage,
  stopSession,
  submitSessionAction,
  regenerateSession,
  rewindSession,
  switchSessionBranch,
  subscribeSession,
  buildSessionArtifact,
  downloadSessionArtifact,
  stopSessionTestServer,
} from './session-api.js';
import { PLATFORMS } from './config.js';
import { BUILD_STAGES } from './session-stages.js';
import { getToken, requireAuth } from './auth.js';
import { initAccountStatus } from './account-status.js';
import {
  initWorkspaceBrowserOverlay,
  openWorkspaceBrowser,
} from './session-workspace-browser.js';
import { initSessionListPanel, renderSessionListPanel, setSessionListActiveId } from './session-list-panel.js';
import { initConceptDrafts } from './concept-drafts.js';

const THEME_KEY = 'mcmod_agent_theme';
const TASK_TITLE_KEY = 'moduscript_session_task_title';
const TOPBAR_GLASS_KEY = 'moduscript_session_topbar_glass';
const TOPBAR_MODE_KEY = 'moduscript_session_topbar_mode';
const MOBILE_MQ = window.matchMedia('(max-width: 767px)');
const HISTORY_REMOTE_DEBOUNCE_MS = 30000;

let historyRemoteTimer = null;
let lastHistoryRemoteFetch = 0;
let adminViewMode = false;
let adminInterventionMode = false;

const ADMIN_INTERVENTION_STORAGE_PREFIX = 'adminIntervention:';

function adminInterventionStorageKey(sid) {
  return `${ADMIN_INTERVENTION_STORAGE_PREFIX}${sid || ''}`;
}

function isAdminInterventionActive() {
  return adminViewMode && adminInterventionMode;
}

/** 管理员是否可看到与普通用户相同的 Agent 完成/错误提示（快捷回复、绿框等） */
function canShowUserTurnHints() {
  return !adminViewMode || adminInterventionMode;
}

function loadAdminInterventionMode(sid) {
  if (!sid || !adminViewMode) return false;
  try {
    return sessionStorage.getItem(adminInterventionStorageKey(sid)) === '1';
  } catch {
    return false;
  }
}

function saveAdminInterventionMode(sid, on) {
  if (!sid) return;
  try {
    if (on) {
      sessionStorage.setItem(adminInterventionStorageKey(sid), '1');
    } else {
      sessionStorage.removeItem(adminInterventionStorageKey(sid));
    }
  } catch {
    /* ignore */
  }
}

const LOADER_LABELS = {
  fabric: 'Fabric',
  forge: 'Forge',
  neoforge: 'NeoForge',
  quilt: 'Quilt',
};

function platformShortLabel(value) {
  const full = PLATFORMS.find((p) => p.value === value)?.label || value || '—';
  if (full === '客户端与服务端均必装') return '双端必装';
  if (full.length > 8) return full.replace('客户端与服务端', '双端');
  return full;
}

/** @type {object | null} */
let snapshot = null;
let lastReferenceStep = null;
/** @type {string | null} */
let sessionId = null;
/** @type {(() => void) | null} */
let unsubscribe = null;
/** @type {string | null} */
let pendingChoiceId = null;
/** @type {object | null} */
let pendingSnapshot = null;
/** @type {number | null} */
let snapshotRafId = null;
let silentHintTimer = null;
let lastErrorNoticeKey = null;

const SILENT_HINT_THRESHOLD_MS = 2 * 60 * 1000;
const SILENT_HINT_REFRESH_MS = 30000;

function formatDurationMs(ms) {
  if (ms == null) return '';
  const sec = Math.floor(Number(ms) / 1000);
  if (sec < 60) return `${sec}s`;
  const minutes = Math.floor(sec / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h${minutes % 60}m`;
}

function formatAgentRunHint(agentRun) {
  if (!agentRun) return '';
  const num = agentRun.num_turns;
  const max = agentRun.max_turns;
  const dur = formatDurationMs(agentRun.duration_ms);
  const reason = agentRun.exit_reason || '';
  const turns = num != null && max ? `${num}/${max} 轮` : '';
  return [turns, dur, reason].filter(Boolean).join(' · ');
}

function isAutoContinuing(data) {
  return Boolean(data?.agent_run?.auto_continuing);
}

function formatRunningHint(data) {
  if (isAutoContinuing(data)) {
    return '上下文已压缩，正在自动继续编写…';
  }
  const silentMin = formatSilentMinutes(data.updated_at);
  const lastTool = data.agent_run?.last_tool;
  if (silentMin != null) {
    let activity = '可能仍在执行耗时操作';
    if (lastTool === 'Bash') activity = '可能正在跑 Gradle';
    else if (lastTool) activity = `可能仍在执行 ${lastTool}`;
    return `Agent 编写中（已静默 ${silentMin} 分钟，${activity}）`;
  }
  if (lastTool) return `Agent 编写中 · 最近工具：${lastTool}`;
  return 'Agent 编写中，请稍候…';
}

/** @returns {{ title: string, body: string, hint: string, quickReplies: { label: string, text: string }[] }} */
function formatCompactedPause(data) {
  return {
    title: '上下文已压缩，编写已暂停',
    body: data?.error_message || 'Agent 会话上下文已自动压缩，当前编写进度已暂停。',
    hint: '发送跟进消息即可继续；服务端将通过 resume 恢复 CLI 会话记忆。',
    quickReplies: [
      {
        label: '继续编写',
        text: '请根据当前项目进度继续完成 mod 开发与编译，无需重复已完成的部分。',
      },
      {
        label: '执行编译',
        text: '请在工作区执行 `./gradlew build`，确保 build/libs 下生成 mod jar 产物。',
      },
    ],
  };
}

function isCompactedEarlyStop(data) {
  return data?.agent_run?.exit_reason === 'compacted_early_stop';
}

/** @returns {{ title: string, body: string, hint: string, quickReplies: { label: string, text: string }[] }} */
function formatSessionError(data) {
  const raw = data?.error_message || '';
  const run = data?.agent_run || {};
  const reason = run.exit_reason || '';
  const runHint = formatAgentRunHint(run);
  const bodyWithRun = runHint && raw && !raw.includes('Agent 已结束')
    ? `${raw}（${runHint}）`
    : runHint && !raw
      ? runHint
      : raw;

  if (reason === 'completed_no_jar' || raw.includes('编译未完成')) {
    return {
      title: '编译未完成',
      body: bodyWithRun || 'build/libs 中未找到 mod jar。',
      hint: '点击下方快捷回复发送给 Agent，或在项目文件中查看 build 日志。',
      quickReplies: [
        {
          label: '执行编译',
          text: '请在工作区执行 `./gradlew build`，确保 build/libs 下生成 mod jar 产物。',
        },
        {
          label: '检查编译错误',
          text: '请检查最近的 Gradle 编译错误日志，修复问题后重新执行 build 并生成 jar。',
        },
      ],
    };
  }
  if (reason === 'max_turns_reached' || raw.includes('轮次上限')) {
    return {
      title: 'Agent 轮次已达上限',
      body: bodyWithRun || 'Agent 已达到配置的最大轮次。',
      hint: '可点击快捷回复让 Agent 继续开发与编译。',
      quickReplies: [
        {
          label: '继续完成编译',
          text: '请根据当前项目进度继续完成剩余开发，并执行 `./gradlew build` 生成 jar。',
        },
        {
          label: '继续修改代码',
          text: '请根据上文上下文继续修改代码，无需重复已完成的部分，完成后执行 gradlew build。',
        },
      ],
    };
  }
  if (
    raw.includes("await wasn't used with future")
    || raw.includes('推送更新时出错')
    || raw.includes('推送更新')
  ) {
    return {
      title: '页面更新异常',
      body: bodyWithRun || '服务端推送会话更新时出错，不代表 Agent 一定失败。',
      hint: data?.artifact_ready
        ? 'mod jar 可能已生成，可先下载 jar 或刷新页面。'
        : '请刷新页面查看最新进度。',
      quickReplies: data?.artifact_ready
        ? [
            {
              label: '检查项目状态',
              text: '请检查当前项目状态与 build/libs 中的 jar 是否完整，并说明建议的下一步操作。',
            },
          ]
        : [
            {
              label: '继续任务',
              text: '请分析当前工作区状态，继续完成 mod 开发与编译。',
            },
          ],
    };
  }
  if (reason === 'exception' && data?.artifact_ready) {
    return {
      title: '任务遇到异常，但可能已有产物',
      body: bodyWithRun || 'Agent 执行过程中出错。',
      hint: '可先下载 jar 验证，或发送快捷回复让 Agent 检查状态。',
      quickReplies: [
        {
          label: '检查产物与状态',
          text: '请检查当前项目与 build/libs 中的 jar 产物是否完整可用，并给出后续建议。',
        },
        {
          label: '继续完善 mod',
          text: '请根据当前代码状态继续完善 mod，如有问题请说明并修复。',
        },
      ],
    };
  }
  if (reason === 'stopped') {
    return {
      title: '已停止编写',
      body: bodyWithRun || '任务已被停止。',
      hint: '可点击快捷回复让 Agent 从当前进度继续。',
      quickReplies: [
        {
          label: '继续开发',
          text: '请从当前项目进度继续完成 mod 开发与编译。',
        },
      ],
    };
  }
  return {
    title: '会话出错',
    body: bodyWithRun || '未知错误',
    hint: '可点击快捷回复尝试恢复，或刷新页面后重试。',
    quickReplies: [
      {
        label: '分析并恢复',
        text: '请分析上述错误原因，并尝试从当前进度恢复 mod 开发任务。',
      },
      {
        label: '检查编译日志',
        text: '请检查 build 目录与 Gradle 编译日志，修复问题后重新执行 `./gradlew build`。',
      },
    ],
  };
}

function fillComposeQuickReply(text) {
  const input = els.sessionComposeInput();
  if (!input || input.disabled || !text) return;
  input.value = text;
  input.focus();
  input.dispatchEvent(new Event('input', { bubbles: true }));
}

function maybeNotifySessionError(data) {
  if (data?.status !== 'error') return;
  const key = `${data.session_id}:${data.updated_at}:${data.error_message || ''}`;
  if (lastErrorNoticeKey === key) return;
  lastErrorNoticeKey = key;
  const err = formatSessionError(data);
  showToast(`${err.title}：${err.hint}`, 'error');
}
function formatSilentMinutes(updatedAt) {
  if (!updatedAt) return null;
  const ms = Date.now() - Date.parse(updatedAt);
  if (Number.isNaN(ms) || ms < SILENT_HINT_THRESHOLD_MS) return null;
  return Math.floor(ms / 60000);
}

function stopSilentHintTimer() {
  if (silentHintTimer) {
    window.clearInterval(silentHintTimer);
    silentHintTimer = null;
  }
}

function startSilentHintTimer() {
  stopSilentHintTimer();
  silentHintTimer = window.setInterval(() => {
    if (snapshot?.status === 'running') updateComposeState(snapshot);
  }, SILENT_HINT_REFRESH_MS);
}
/** @type {boolean} */
let messagesHasRendered = false;

const expandedUiState = {
  turnDetails: new Set(),
  nestedDetails: new Set(),
};

function captureExpandedState(container) {
  if (!container) return;
  container.querySelectorAll('.session-turn-details').forEach((el) => {
    if (!el.open) return;
    const turnId = el.closest('[data-turn-id]')?.dataset.turnId;
    if (turnId) expandedUiState.turnDetails.add(turnId);
  });
  container.querySelectorAll('[data-nested-key]').forEach((el) => {
    if (el.open && el.dataset.nestedKey) {
      expandedUiState.nestedDetails.add(el.dataset.nestedKey);
    }
  });
}

function restoreExpandedState(container) {
  if (!container) return;
  container.querySelectorAll('.session-turn-details').forEach((el) => {
    const turnId = el.closest('[data-turn-id]')?.dataset.turnId;
    if (turnId && expandedUiState.turnDetails.has(turnId)) {
      el.open = true;
      const label = el.querySelector('.session-turn-details-label');
      if (label) label.textContent = '收起执行细节';
    }
  });
  container.querySelectorAll('[data-nested-key]').forEach((el) => {
    if (el.dataset.nestedKey && expandedUiState.nestedDetails.has(el.dataset.nestedKey)) {
      el.open = true;
    }
  });
}

function bindTurnDetailsToggle(details) {
  const detailsLabel = details?.querySelector('.session-turn-details-label');
  if (!details || details.dataset.boundToggle) return;
  details.dataset.boundToggle = '1';
  details.addEventListener('toggle', () => {
    const turnId = details.closest('[data-turn-id]')?.dataset.turnId;
    if (turnId) {
      if (details.open) expandedUiState.turnDetails.add(turnId);
      else expandedUiState.turnDetails.delete(turnId);
    }
    if (details.open && turnId && snapshot) {
      const turn = snapshot.turns?.find((t) => t.id === turnId);
      const body = details.querySelector('.session-turn-details-body');
      if (turn && body) {
        body.innerHTML = buildTurnDetailsBodyHtml(turn, turnId);
        bindNestedDetailsToggles(body);
      }
    }
    if (!details.open) {
      details.querySelector('.session-turn-details-summary')?.scrollIntoView({
        block: 'nearest',
        behavior: 'smooth',
      });
    }
    if (detailsLabel) {
      detailsLabel.textContent = details.open ? '收起执行细节' : '查看执行细节';
    }
  });
}

function bindNestedDetailsToggles(container) {
  container?.querySelectorAll('[data-nested-key]').forEach((el) => {
    if (el.dataset.boundNestedToggle) return;
    el.dataset.boundNestedToggle = '1';
    el.addEventListener('toggle', () => {
      const key = el.dataset.nestedKey;
      if (!key) return;
      if (el.open) expandedUiState.nestedDetails.add(key);
      else expandedUiState.nestedDetails.delete(key);
    });
  });
}

function isQuestionMultiSelect(q) {
  return Boolean(q?.multiSelect ?? q?.multi_select);
}

function isNearBottom(scrollEl, threshold = 120) {
  if (!scrollEl) return true;
  return scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight <= threshold;
}

function maybeScrollToBottom(force = false) {
  const scrollEl = document.getElementById('sessionScroll');
  if (!scrollEl) return;
  if (force || isNearBottom(scrollEl)) {
    requestAnimationFrame(() => {
      scrollEl.scrollTop = scrollEl.scrollHeight;
    });
  }
}

const els = {
  sessionMessages: () => document.getElementById('sessionMessages'),
  sessionStatus: () => document.getElementById('sessionStatus'),
  sessionStageText: () => document.getElementById('sessionStageText'),
  sessionComposeInput: () => document.getElementById('sessionComposeInput'),
  sessionComposeHint: () => document.getElementById('sessionComposeHint'),
  sessionStopBtn: () => document.getElementById('sessionStopBtn'),
  sessionSendBtn: () => document.getElementById('sessionSendBtn'),
  toastContainer: () => document.getElementById('toastContainer'),
  historyPanelBody: () => document.getElementById('historyPanelBody'),
  historyRecycleBtn: () => document.getElementById('historyRecycleBtn'),
  historyPanelTitle: () => document.getElementById('historyPanelTitle'),
};

function showToast(message, type = 'info', duration = 4000) {
  const container = els.toastContainer();
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast--${type}`;
  toast.setAttribute('role', 'alert');
  const msg = document.createElement('span');
  msg.className = 'toast-message';
  msg.textContent = message;
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'toast-close';
  close.setAttribute('aria-label', '关闭');
  close.textContent = '×';
  close.addEventListener('click', () => toast.remove());
  toast.append(msg, close);
  container.appendChild(toast);
  if (duration > 0) setTimeout(() => toast.remove(), duration);
}

function applyTheme(theme) {
  document.documentElement.classList.toggle('dark', theme === 'dark');
  localStorage.setItem(THEME_KEY, theme);
}

function initTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = stored === 'dark' || stored === 'light' ? stored : prefersDark ? 'dark' : 'light';
  applyTheme(theme);
}

function syncHistory(open) {
  const appLayout = document.getElementById('appLayout');
  const historyToggle = document.getElementById('historyToggle');
  const historyPanel = document.getElementById('historyPanel');
  const historyBackdrop = document.getElementById('historyBackdrop');
  historyToggle?.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (historyPanel) historyPanel.hidden = !open;
  if (historyBackdrop) historyBackdrop.hidden = !(open && MOBILE_MQ.matches);
  appLayout?.classList.toggle('app-layout--history-open', open);
}

function closeAllNavMenus(except) {
  document.querySelectorAll('.nav-dropdown').forEach((dd) => {
    if (dd === except) return;
    const trigger = dd.querySelector('.nav-dropdown-trigger');
    const menu = dd.querySelector('.nav-dropdown-menu');
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
    if (menu) menu.hidden = true;
  });
}

function initNavigation() {
  document.querySelectorAll('.nav-dropdown-trigger').forEach((trigger) => {
    const menu = document.getElementById(trigger.getAttribute('aria-controls') || '');
    if (!menu) return;
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      const isOpen = trigger.getAttribute('aria-expanded') === 'true';
      closeAllNavMenus(isOpen ? null : trigger.closest('.nav-dropdown'));
      trigger.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
      menu.hidden = isOpen;
    });
  });

  document.querySelectorAll('[data-nav-action]').forEach((btn) => {
    btn.addEventListener('click', () => closeAllNavMenus());
  });

  document.addEventListener('click', () => closeAllNavMenus());

  document.getElementById('historyToggle')?.addEventListener('click', () => {
    const appLayout = document.getElementById('appLayout');
    const opening = !appLayout?.classList.contains('app-layout--history-open');
    syncHistory(opening);
    if (opening) refreshHistoryPanelRemote(true);
  });

  document.getElementById('historyBackdrop')?.addEventListener('click', () => syncHistory(false));
}

function loadBootstrap() {
  try {
    const raw = sessionStorage.getItem(SESSION_STORAGE_BOOTSTRAP);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

/** @param {object | null | undefined} prev @param {object | null | undefined} incoming */
function mergeSnapshotHeavyFields(prev, incoming) {
  if (!incoming) return prev || null;
  if (!prev) return incoming;
  const merged = { ...incoming };
  for (const key of ['final_prompt', 'readable_blueprint', 'payload']) {
    const nextVal = merged[key];
    const prevVal = prev[key];
    if ((nextVal === undefined || nextVal === null || nextVal === '') && prevVal) {
      merged[key] = prevVal;
    }
  }
  return merged;
}

/** @param {object | null | undefined} data */
function resolveUserMessageContent(data) {
  const finalPrompt = (data?.final_prompt || '').trim();
  const readableBlueprint = (data?.readable_blueprint || '').trim();
  if (finalPrompt || readableBlueprint) {
    return {
      finalPrompt: data.final_prompt || '',
      readableBlueprint: data.readable_blueprint || '',
    };
  }
  const bootstrap = loadBootstrap();
  const sid = data?.session_id || sessionId;
  if (bootstrap && bootstrap.sessionId === sid) {
    return {
      finalPrompt: bootstrap.finalPrompt || '',
      readableBlueprint: bootstrap.readableBlueprint || '',
    };
  }
  return { finalPrompt: '', readableBlueprint: '' };
}

function getSessionIdFromUrl() {
  return new URLSearchParams(window.location.search).get('session_id');
}

function renderEnvChips(payload) {
  const container = document.getElementById('sessionEnvChips');
  if (!container || !payload) return;

  const mc = payload.minecraft_version || '—';
  const loader = LOADER_LABELS[payload.mod_loader] || payload.mod_loader || '—';
  const platform = platformShortLabel(payload.platform);

  container.innerHTML = '';
  [mc, loader, platform].forEach((label) => {
    const chip = document.createElement('span');
    chip.className = 'session-env-chip';
    chip.textContent = label;
    container.appendChild(chip);
  });
}

function renderProgressBar(stages, stageIndex, container) {
  if (!container) return;
  container.innerHTML = '';
  stages.forEach((name, index) => {
    const step = document.createElement('div');
    step.className = 'session-progress-step';
    step.setAttribute('role', 'listitem');
    step.title = name;
    if (index < stageIndex) step.classList.add('session-progress-step--done');
    else if (index === stageIndex) step.classList.add('session-progress-step--active');
    const bar = document.createElement('span');
    bar.className = 'session-progress-bar';
    step.appendChild(bar);
    container.appendChild(step);
  });
}

const TITLE_GENERATING_PLACEHOLDER = '标题生成中……';

function shouldShowSetupNotice(data) {
  const progress = data?.setup_progress;
  if (!progress) return false;
  return Boolean(progress.failed || progress.retrying);
}

function getReferenceIndexHint(data) {
  const tools = data?.turns?.[data.turns.length - 1]?.tools || [];
  const refTool = [...tools].reverse().find((t) => {
    const name = t?.name || '';
    return name.includes('反编译') || name.includes('克隆') || name.includes('索引') || name.includes('参考');
  });
  if (refTool) {
    return `正在索引参考模组：${(refTool.name || '').replace(/^[◉✓·]\s*/, '')}…`;
  }
  if (lastReferenceStep?.label) {
    return `正在索引参考模组：${lastReferenceStep.label}…`;
  }
  const indexing = Object.values(data?.reference_index || {}).some(
    (m) => m && typeof m === 'object' && m.status === 'indexing',
  );
  if (indexing) return '正在索引参考模组…';
  return '';
}

function handleReferenceStreamEvent(event) {
  if (event.type === 'reference_step') {
    lastReferenceStep = event.data || null;
    if (snapshot?.status === 'starting') {
      updateComposeState(snapshot);
    }
  } else if (event.type === 'reference_indexing' && snapshot && event.data?.project_id) {
    snapshot.reference_index = snapshot.reference_index || {};
    const pid = event.data.project_id;
    snapshot.reference_index[pid] = {
      ...(snapshot.reference_index[pid] || {}),
      status: 'indexing',
      project_id: pid,
    };
  } else if (
    (event.type === 'reference_ready' || event.type === 'reference_failed') &&
    snapshot &&
    event.data
  ) {
    snapshot.reference_index = snapshot.reference_index || {};
    const pid = event.data.project_id || event.data.slug;
    if (pid) snapshot.reference_index[pid] = { ...event.data };
    if (event.type === 'reference_failed') {
      showToast('参考源码索引失败，编写仍可继续', 'info');
    }
  }
}

function getSetupRetryLine(data) {
  const progress = data?.setup_progress;
  if (!progress) return '';
  const attempt = progress.retry_attempt || 0;
  const retryMax = progress.retry_max || 0;
  if (progress.retrying && attempt > 0 && retryMax > 0) {
    return `正在重试获取模板（第 ${attempt}/${retryMax} 次）…`;
  }
  if (progress.failed && progress.notice?.retry_text) {
    return progress.notice.retry_text;
  }
  return '';
}

function renderSetupNotice(data) {
  const noticeEl = document.getElementById('sessionSetupNotice');
  if (!noticeEl) return;

  if (!shouldShowSetupNotice(data)) {
    noticeEl.hidden = true;
    noticeEl.innerHTML = '';
    return;
  }

  const progress = data.setup_progress;
  const notice = progress.notice || {};
  const retryLine = getSetupRetryLine(data);

  noticeEl.hidden = false;
  noticeEl.classList.toggle('session-setup-notice--retrying', Boolean(progress.retrying));

  if (progress.retrying) {
    noticeEl.innerHTML = `
      <p class="session-setup-notice-title">正在重新获取 Fabric 模板</p>
      <p class="session-setup-notice-body">网络可能暂时不稳定，Moduscript 正在自动重试，请稍候。</p>
      <p class="session-setup-notice-retry">${escapeHtml(retryLine)}</p>
    `;
    return;
  }

  noticeEl.innerHTML = `
    <p class="session-setup-notice-title">${escapeHtml(notice.title || '环境创建失败')}</p>
    <p class="session-setup-notice-body">${escapeHtml(notice.body || 'Moduscript 服务器无法获取到 Fabric 模板，工作区未初始化')}</p>
    <p class="session-setup-notice-hint">${escapeHtml(notice.hint || '这不是你的错，通常是网络波动导致。Moduscript 会持续尝试继续获取，并执行接下来的任务。')}</p>
    ${retryLine ? `<p class="session-setup-notice-retry">${escapeHtml(retryLine)}</p>` : ''}
  `;
}

function resolveSessionHeroTitle(data) {
  const modName = (data?.mod_name || data?.payload?.mod_name || '').trim();
  if (modName) return modName;
  const taskTitle = (data?.task_title || '').trim();
  if (taskTitle && taskTitle !== TITLE_GENERATING_PLACEHOLDER) return taskTitle;
  return deriveTaskTitle(data?.readable_blueprint, '') || '加载中…';
}

function renderHero(data) {
  const titleEl = document.getElementById('sessionTaskTitle');
  const title = resolveSessionHeroTitle(data);
  if (titleEl) {
    titleEl.textContent = title;
    titleEl.classList.toggle(
      'session-hero-title--pending',
      title === TITLE_GENERATING_PLACEHOLDER,
    );
  }

  const statusEl = els.sessionStatus();
  const stageEl = els.sessionStageText();
  if (statusEl) statusEl.textContent = data.status_label || '正在编写…';
  if (stageEl) {
    const total = data.stage_total ?? data.stages?.length ?? BUILD_STAGES.length;
    stageEl.textContent = `${data.stage_name || '—'} · ${(data.stage_index ?? 0) + 1}/${total}`;
  }

  if (data.payload) renderEnvChips(data.payload);
}

function createUserMessageNode(finalPrompt, readableBlueprint, data, nodeId) {
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-user';
  if (nodeId) wrap.dataset.nodeId = nodeId;
  wrap.innerHTML = `
    <p class="msg-label">你</p>
    <div class="session-user-tabs" role="group" aria-label="任务稿视图">
      <button type="button" class="btn btn-outline btn-sm session-user-tab is-active" data-view="final" aria-pressed="true">原始文本</button>
      <button type="button" class="btn btn-outline btn-sm session-user-tab" data-view="readable" aria-pressed="false">易读蓝图</button>
    </div>
    <div class="session-user-body session-user-body--collapsed">
      <pre class="session-user-final"></pre>
      <div class="desc-optimize-md markdown-body session-user-blueprint" hidden></div>
    </div>
    <button type="button" class="session-user-expand-btn" aria-expanded="false">
      <svg class="session-user-expand-icon" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
      <span class="session-user-expand-label">展开全文</span>
    </button>
  `;

  const finalEl = wrap.querySelector('.session-user-final');
  const readableEl = wrap.querySelector('.session-user-blueprint');
  if (finalEl) finalEl.textContent = finalPrompt;
  if (readableEl) readableEl.innerHTML = renderMarkdown(readableBlueprint);

  bindUserMessageInteractions(wrap);
  const actions = createMessageActionsBar({ data, nodeId, kind: 'user' });
  if (actions) wrap.appendChild(actions);
  return wrap;
}

function bindUserMessageInteractions(wrap) {
  const body = wrap.querySelector('.session-user-body');
  const btn = wrap.querySelector('.session-user-expand-btn');
  const label = btn?.querySelector('.session-user-expand-label');
  const finalEl = wrap.querySelector('.session-user-final');
  const readableEl = wrap.querySelector('.session-user-blueprint');

  btn?.addEventListener('click', () => {
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    const next = !expanded;
    btn.setAttribute('aria-expanded', next ? 'true' : 'false');
    body?.classList.toggle('session-user-body--collapsed', !next);
    body?.classList.toggle('session-user-body--expanded', next);
    if (label) label.textContent = next ? '收起' : '展开全文';
  });

  wrap.querySelectorAll('.session-user-tab').forEach((tab) => {
    tab.addEventListener('click', () => {
      const view = tab.dataset.view;
      wrap.querySelectorAll('.session-user-tab').forEach((t) => {
        const active = t === tab;
        t.classList.toggle('is-active', active);
        t.setAttribute('aria-pressed', active ? 'true' : 'false');
      });
      if (finalEl) finalEl.hidden = view !== 'final';
      if (readableEl) readableEl.hidden = view !== 'readable';
    });
  });
}

function createUserFollowUpNode(message, data, nodeId) {
  const content = typeof message === 'string' ? message : message?.content || '';
  const sentByAdmin = Boolean(typeof message === 'object' && message?.sent_by_admin);
  const wrap = document.createElement('div');
  wrap.className = sentByAdmin ? 'msg msg-user msg-user--admin-intervention' : 'msg msg-user';
  if (nodeId) wrap.dataset.nodeId = nodeId;
  const adminNote = sentByAdmin
    ? '<p class="msg-admin-intervention-note">由管理员介入并发送</p>'
    : '';
  wrap.innerHTML = `<p class="msg-label">你</p>${adminNote}<p class="session-user-reply">${escapeHtml(content)}</p>`;
  if (!sentByAdmin) {
    const actions = createMessageActionsBar({ data, nodeId, kind: 'user' });
    if (actions) wrap.appendChild(actions);
  }
  return wrap;
}

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

function canUseBranchActions(data) {
  if (adminViewMode && !adminInterventionMode) return false;
  if (data?.branch_processing) return false;
  if (['completed', 'stopped', 'error', 'waiting_user', 'interrupted'].includes(data?.status)) {
    return true;
  }
  return false;
}

function getBranchMeta(data, nodeId) {
  const nav = data?.branch_nav || [];
  return nav.find((item) => item.node_id === nodeId) || null;
}

function createBranchNavigator(data, nodeId) {
  const meta = getBranchMeta(data, nodeId);
  if (!meta || meta.branch_total <= 1) return null;

  const wrap = document.createElement('div');
  wrap.className = 'session-branch-nav';
  wrap.dataset.nodeId = nodeId;

  const prevBtn = document.createElement('button');
  prevBtn.type = 'button';
  prevBtn.className = 'session-branch-btn';
  prevBtn.title = '上一版本';
  prevBtn.innerHTML = '‹';
  prevBtn.disabled = !meta.has_prev || !canUseBranchActions(data);

  const label = document.createElement('span');
  label.className = 'session-branch-label';
  label.textContent = `${meta.branch_index + 1} / ${meta.branch_total}`;

  const nextBtn = document.createElement('button');
  nextBtn.type = 'button';
  nextBtn.className = 'session-branch-btn';
  nextBtn.title = '下一版本';
  nextBtn.innerHTML = '›';
  nextBtn.disabled = !meta.has_next || !canUseBranchActions(data);

  const goSibling = async (delta) => {
    const siblings = meta.sibling_ids || [];
    const nextIndex = meta.branch_index + delta;
    if (nextIndex < 0 || nextIndex >= siblings.length) return;
    const targetId = siblings[nextIndex];
    if (!targetId || !sessionId) return;
    try {
      const snap = await switchSessionBranch(sessionId, targetId);
      applyBranchSnapshot(snap);
    } catch (err) {
      showToast(err.message || '切换失败', 'error');
    }
  };

  if (!prevBtn.disabled) prevBtn.addEventListener('click', () => goSibling(-1));
  if (!nextBtn.disabled) nextBtn.addEventListener('click', () => goSibling(1));

  wrap.append(prevBtn, label, nextBtn);
  return wrap;
}

function createMessageActionsBar({ data, nodeId, kind, isLatestAssistant }) {
  const bar = document.createElement('div');
  bar.className = 'session-msg-actions';

  const branchNav = createBranchNavigator(data, nodeId);
  if (branchNav) bar.appendChild(branchNav);

  const actions = document.createElement('div');
  actions.className = 'session-msg-actions-btns';

  if (kind === 'user' && canUseBranchActions(data)) {
    const editBtn = document.createElement('button');
    editBtn.type = 'button';
    editBtn.className = 'session-msg-action-btn';
    editBtn.title = '重新输入';
    editBtn.textContent = '编辑';
    editBtn.addEventListener('click', () => startUserMessageEdit(bar.closest('.msg'), nodeId, data));
    actions.appendChild(editBtn);
  }

  if (kind === 'assistant' && isLatestAssistant && canUseBranchActions(data)) {
    const regenBtn = document.createElement('button');
    regenBtn.type = 'button';
    regenBtn.className = 'session-msg-action-btn';
    regenBtn.title = '重新生成';
    regenBtn.textContent = '重新生成';
    regenBtn.addEventListener('click', () => handleRegenerate());
    actions.appendChild(regenBtn);
  }

  if (actions.childElementCount) bar.appendChild(actions);
  return bar.childElementCount ? bar : null;
}

function ensureMessageActionsBar(wrap, { data, nodeId, kind, isLatestAssistant }) {
  if (!wrap || !isLatestAssistant) return;
  const bar = createMessageActionsBar({ data, nodeId, kind, isLatestAssistant });
  const existing = wrap.querySelector('.session-msg-actions');
  if (bar) {
    if (existing) {
      existing.replaceWith(bar);
    } else {
      wrap.appendChild(bar);
    }
  } else if (existing) {
    existing.remove();
  }
}

async function handleRegenerate() {
  if (!sessionId) return;
  try {
    const snap = await regenerateSession(sessionId);
    applyBranchSnapshot(snap);
  } catch (err) {
    showToast(err.message || '重新生成失败', 'error');
  }
}

function startUserMessageEdit(msgEl, nodeId, data) {
  if (!msgEl || msgEl.querySelector('.session-user-edit-form')) return;
  const isInitial = msgEl.classList.contains('msg-user') && msgEl.querySelector('.session-user-final');
  let currentText = '';
  if (isInitial) {
    currentText = msgEl.querySelector('.session-user-final')?.textContent || '';
  } else {
    currentText = msgEl.querySelector('.session-user-reply')?.textContent || '';
  }

  const form = document.createElement('div');
  form.className = 'session-user-edit-form';
  const textarea = document.createElement('textarea');
  textarea.className = 'session-user-edit-input';
  textarea.rows = 3;
  textarea.value = currentText;

  const actions = document.createElement('div');
  actions.className = 'session-user-edit-actions';
  const cancelBtn = document.createElement('button');
  cancelBtn.type = 'button';
  cancelBtn.className = 'btn btn-outline btn-sm';
  cancelBtn.textContent = '取消';
  const submitBtn = document.createElement('button');
  submitBtn.type = 'button';
  submitBtn.className = 'btn btn-primary btn-sm';
  submitBtn.textContent = '发送';

  cancelBtn.addEventListener('click', () => form.remove());
  submitBtn.addEventListener('click', async () => {
    const text = textarea.value.trim();
    if (!text) {
      showToast('请输入内容', 'error');
      return;
    }
    const isRoot = (data?.conversation_tree?.active_path || [])[0] === nodeId;
    if (isRoot) {
      const ok = window.confirm('修改首条任务稿将重置工作区并重新创建环境，是否继续？');
      if (!ok) return;
    }
    try {
      const snap = await rewindSession(sessionId, { node_id: nodeId, new_content: text });
      applyBranchSnapshot(snap);
    } catch (err) {
      showToast(err.message || '重新输入失败', 'error');
    }
  });

  actions.append(cancelBtn, submitBtn);
  form.append(textarea, actions);
  msgEl.appendChild(form);
  textarea.focus();
}
function renderTurnDetailsMeta(turn) {
  const parts = [];
  const thinkingCount = turn.thinking?.length || 0;
  const toolCount = turn.tools?.length || 0;
  if (thinkingCount) parts.push(`${thinkingCount} 思考`);
  if (toolCount) parts.push(`${toolCount} 工具`);
  if (turn.user_reply) parts.push('1 确认');
  return parts.join(' · ') || '无记录';
}

function buildTurnDetailsBodyHtml(turn, turnId = '') {
  const thinkingBlocks = (turn.thinking || [])
    .map(
      (text, index) => {
        const key = `${turnId}:thinking:${index}`;
        const openAttr = expandedUiState.nestedDetails.has(key) ? ' open' : '';
        return `
      <details class="session-thinking-block"${openAttr} data-nested-key="${escapeHtml(key)}">
        <summary>思考过程</summary>
        <pre class="session-mono">${escapeHtml(text)}</pre>
      </details>`;
      },
    )
    .join('');

  const toolCards = (turn.tools || [])
    .map(
      (tool) => `
      <div class="session-tool-card">
        <strong class="session-tool-name">${escapeHtml(tool.name)}</strong>
        <pre class="session-mono session-tool-preview">${escapeHtml(tool.preview || '')}</pre>
      </div>`,
    )
    .join('');

  let decisionHtml = '';
  if (turn.user_reply) {
    const question = turn.pending_action?.question || '';
    const label = turn.user_reply.label || '';
    const nestedKey = `${turnId}:decision`;
    const openAttr = expandedUiState.nestedDetails.has(nestedKey) ? ' open' : '';
    const adminNote = turn.user_reply.sent_by_admin
      ? '<p class="msg-admin-intervention-note">由管理员介入并发送</p>'
      : '';
    decisionHtml = `
      <details class="session-decision-block"${openAttr} data-nested-key="${escapeHtml(nestedKey)}">
        <summary>用户确认 · ${escapeHtml(label)}</summary>
        <div class="session-decision-body">
          ${adminNote}
          ${question ? `<p class="session-decision-q">${escapeHtml(question)}</p>` : ''}
          <p class="session-decision-answer"><span class="session-decision-answer-label">你的选择</span> ${escapeHtml(label)}</p>
        </div>
      </details>`;
  }

  return thinkingBlocks + toolCards + decisionHtml;
}

function buildThinkingBlockHtml(text, turnId, index) {
  const key = `${turnId}:thinking:${index}`;
  const openAttr = expandedUiState.nestedDetails.has(key) ? ' open' : '';
  return `
      <details class="session-thinking-block"${openAttr} data-nested-key="${escapeHtml(key)}">
        <summary>思考过程</summary>
        <pre class="session-mono">${escapeHtml(text)}</pre>
      </details>`;
}

function buildToolCardHtml(tool) {
  return `
      <div class="session-tool-card">
        <strong class="session-tool-name">${escapeHtml(tool.name)}</strong>
        <pre class="session-mono session-tool-preview">${escapeHtml(tool.preview || '')}</pre>
      </div>`;
}

function buildDecisionBlockHtml(turn, turnId) {
  const question = turn.pending_action?.question || '';
  const label = turn.user_reply.label || '';
  const nestedKey = `${turnId}:decision`;
  const openAttr = expandedUiState.nestedDetails.has(nestedKey) ? ' open' : '';
  return `
      <details class="session-decision-block"${openAttr} data-nested-key="${escapeHtml(nestedKey)}">
        <summary>用户确认 · ${escapeHtml(label)}</summary>
        <div class="session-decision-body">
          ${question ? `<p class="session-decision-q">${escapeHtml(question)}</p>` : ''}
          <p class="session-decision-answer"><span class="session-decision-answer-label">你的选择</span> ${escapeHtml(label)}</p>
        </div>
      </details>`;
}

function syncTurnDetailsBody(detailsBody, turn, turnId) {
  const thinking = turn.thinking || [];
  const tools = turn.tools || [];

  let thinkingEls = detailsBody.querySelectorAll('.session-thinking-block');
  for (let i = thinkingEls.length; i < thinking.length; i += 1) {
    detailsBody.insertAdjacentHTML('beforeend', buildThinkingBlockHtml(thinking[i], turnId, i));
  }
  thinkingEls = detailsBody.querySelectorAll('.session-thinking-block');
  if (thinking.length > 0 && thinkingEls.length === thinking.length) {
    const lastIdx = thinking.length - 1;
    const pre = thinkingEls[lastIdx]?.querySelector('pre');
    if (pre) pre.textContent = thinking[lastIdx];
  }

  let toolEls = detailsBody.querySelectorAll('.session-tool-card');
  while (toolEls.length > tools.length) {
    toolEls[toolEls.length - 1].remove();
    toolEls = detailsBody.querySelectorAll('.session-tool-card');
  }
  for (let i = toolEls.length; i < tools.length; i += 1) {
    detailsBody.insertAdjacentHTML('beforeend', buildToolCardHtml(tools[i]));
  }
  toolEls = detailsBody.querySelectorAll('.session-tool-card');
  if (tools.length > 0 && toolEls.length === tools.length) {
    tools.forEach((tool, index) => {
      const el = toolEls[index];
      const nameEl = el?.querySelector('.session-tool-name');
      const previewEl = el?.querySelector('.session-tool-preview');
      if (nameEl) nameEl.textContent = tool.name || '';
      if (previewEl) previewEl.textContent = tool.preview || '';
    });
  }

  if (turn.user_reply && !detailsBody.querySelector('.session-decision-block')) {
    detailsBody.insertAdjacentHTML('beforeend', buildDecisionBlockHtml(turn, turnId));
  }

  bindNestedDetailsToggles(detailsBody);
}

function getServerAddress(data) {
  if (!data) return '';
  if (data.test_server?.address) return data.test_server.address;
  const host = data.test_server?.host || '127.0.0.1';
  const port = data.test_server?.port;
  return port ? `${host}:${port}` : '';
}

const DOWNLOAD_JAR_LABEL = '下载模组 jar';

function setDownloadButtonPacking(packing) {
  const downloadBtn = document.getElementById('topBarDownloadJar');
  if (!downloadBtn) return;
  if (packing) {
    if (!downloadBtn.dataset.defaultLabel) {
      downloadBtn.dataset.defaultLabel = downloadBtn.textContent.trim() || DOWNLOAD_JAR_LABEL;
    }
    downloadBtn.disabled = true;
    downloadBtn.classList.add('top-bar-btn--packing');
    downloadBtn.innerHTML = `<svg class="spinner" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg><span>打包中</span>`;
  } else {
    downloadBtn.classList.remove('top-bar-btn--packing');
    downloadBtn.textContent = downloadBtn.dataset.defaultLabel || DOWNLOAD_JAR_LABEL;
  }
}

async function runDownloadArtifact(data) {
  if (!sessionId) return;
  if (data?.workspace_git_stale) {
    showToast('工作区尚未同步到当前分支，正在准备…', 'info');
  }
  if (!data?.artifact_ready && !data?.workspace_git_stale) {
    showToast('构建产物尚未就绪', 'error');
    return;
  }
  setDownloadButtonPacking(true);
  try {
    showToast('正在执行 gradlew build…', 'info', 120000);
    const buildResult = await buildSessionArtifact(sessionId);
    if (snapshot && buildResult) {
      snapshot.artifact_ready = Boolean(buildResult.artifact_ready);
      if (buildResult.artifact_file) snapshot.artifact_file = buildResult.artifact_file;
      syncTopBarProjectActions(snapshot);
    }
    const fileName =
      buildResult?.artifact_file ||
      data.artifact_file ||
      data.delivery?.artifact_name ||
      'mod.jar';
    const { name, blob } = await downloadSessionArtifact(sessionId, fileName);
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
    showToast(`已开始下载 ${name}`, 'success');
  } catch (err) {
    showToast(err.message || '下载失败', 'error');
  } finally {
    setDownloadButtonPacking(false);
    const downloadBtn = document.getElementById('topBarDownloadJar');
    if (downloadBtn && (snapshot?.artifact_ready ?? data?.artifact_ready)) {
      downloadBtn.disabled = false;
    }
  }
}

const TEST_SERVER_LABEL = '启动测试服务器';

function promptTestServerComingSoon() {
  showToast('测试服务器功能即将推出，请参阅 docs/TEST_SERVER.md 在本地手动验证 mod', 'info');
}

async function runStopTestServer() {
  if (!sessionId) return null;
  const result = await stopSessionTestServer(sessionId);
  showToast(result.message || '测试服务器已关闭', 'success');
  if (result.snapshot) applySnapshot(result.snapshot);
  else if (snapshot) syncTopBarProjectActions(snapshot);
  return result;
}

async function runCopyServerAddress(data) {
  const addr = getServerAddress(data || snapshot);
  if (!addr) {
    showToast('测试服务器未运行', 'error');
    return;
  }
  try {
    await navigator.clipboard.writeText(addr);
    showToast(`已复制：${addr}`, 'success');
  } catch {
    showToast(`地址：${addr}`, 'info');
  }
}

function runOpenProjectFolder(data) {
  if (!sessionId || !data?.delivery?.project_path) {
    showToast('项目路径不可用', 'error');
    return;
  }
  if (data?.workspace_git_stale) {
    showToast('正在同步工作区到当前分支…', 'info', 3000);
  }
  openWorkspaceBrowser(sessionId, data, { showToast });
}

function syncTopBarProjectActions(data) {
  const stale = Boolean(data?.workspace_git_stale);
  const artifactReady = Boolean(data?.artifact_ready);
  const hasProjectPath = Boolean(data?.delivery?.project_path);

  const downloadBtn = document.getElementById('topBarDownloadJar');
  const startBtn = document.getElementById('topBarStartTestServer');
  const folderBtn = document.getElementById('topBarOpenFolder');
  const submenu = document.getElementById('topBarTestServerSubmenu');
  const control = document.getElementById('topBarTestServerControl');

  if (downloadBtn) {
    downloadBtn.disabled = !artifactReady;
    downloadBtn.title = stale && artifactReady
      ? '工作区将同步到当前分支后打包下载'
      : '';
  }
  if (folderBtn) folderBtn.disabled = !hasProjectPath;

  if (startBtn) {
    startBtn.disabled = !artifactReady || stale;
    startBtn.textContent = TEST_SERVER_LABEL;
    startBtn.title = '即将推出';
    startBtn.classList.remove('is-server-running');
  }

  if (submenu) submenu.hidden = true;
  if (control) control.classList.remove('is-server-running');
}

function initTopBarToolbar() {
  const projectGroup = document.getElementById('topBarProjectGroup');
  const generalGroup = document.getElementById('topBarGeneralGroup');
  const switchBtn = document.getElementById('topBarToolbarSwitch');
  if (!projectGroup || !generalGroup) return;

  let mode = localStorage.getItem(TOPBAR_MODE_KEY);
  if (mode !== 'project' && mode !== 'general') mode = 'project';

  const applyMode = (next) => {
    const isProject = next === 'project';
    projectGroup.hidden = !isProject;
    generalGroup.hidden = isProject;
    if (switchBtn) {
      switchBtn.dataset.toolbarPanel = isProject ? 'project' : 'general';
      const stateEl = document.getElementById('topBarToolbarSwitchState');
      if (stateEl) stateEl.textContent = isProject ? '项目' : '菜单';
      const label = isProject
        ? '当前：项目管理，切换到站点菜单'
        : '当前：站点菜单，切换到项目管理';
      switchBtn.setAttribute('aria-label', label);
      switchBtn.title = label;
    }
    closeAllNavMenus();
  };

  switchBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    mode = mode === 'project' ? 'general' : 'project';
    localStorage.setItem(TOPBAR_MODE_KEY, mode);
    applyMode(mode);
  });

  applyMode(mode);

  document.getElementById('topBarDownloadJar')?.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (e.currentTarget.disabled) return;
    await runDownloadArtifact(snapshot);
  });

  document.getElementById('topBarStartTestServer')?.addEventListener('click', (e) => {
    e.stopPropagation();
    if (e.currentTarget.disabled) return;
    promptTestServerComingSoon();
  });

  document.getElementById('topBarStopTestServer')?.addEventListener('click', async (e) => {
    e.stopPropagation();
    try {
      await runStopTestServer();
    } catch (err) {
      showToast(err.message || '关闭失败', 'error');
    }
  });

  document.getElementById('topBarCopyServerAddr')?.addEventListener('click', (e) => {
    e.stopPropagation();
    runCopyServerAddress(snapshot);
  });

  document.getElementById('topBarOpenFolder')?.addEventListener('click', (e) => {
    e.stopPropagation();
    runOpenProjectFolder(snapshot);
  });
  initWorkspaceBrowserOverlay();
  initSessionListPanel({
    bodyEl: document.getElementById('historyPanelBody'),
    titleEl: document.getElementById('historyPanelTitle'),
    footerEl: document.getElementById('historyRecycleBtn'),
    newConceptBtnEl: document.getElementById('conceptNewBtn'),
    sessionId,
    showToast,
    onSessionNavigate: (item) => {
      const id = item?.sessionId || item;
      if (id === sessionId) return;
      const mode = item?.mode || (String(id).startsWith('plan-') ? 'plan' : 'build');
      if (mode === 'plan') {
        window.location.href = `/plan.html?plan_id=${encodeURIComponent(id)}`;
      } else {
        window.location.href = `/session.html?session_id=${encodeURIComponent(id)}`;
      }
    },
    onConceptNavigate: () => {
      window.location.href = '/index.html';
    },
  });
}

const TURN_COMPLETE_DOWNLOAD_SUB =
  '若本次对话涉及功能修改，请手动重新下载模组 jar';

function buildTurnCompleteHint(
  title,
  subText = null,
  {
    variant = 'success',
    actionsHtml = '',
    repliesHtml = '',
  } = {},
) {
  const sub =
    subText != null && subText !== ''
      ? `<p class="session-turn-complete-hint-sub">${escapeHtml(subText)}</p>`
      : '';
  const variantClass =
    variant === 'error' ? ' session-turn-complete-hint--error' : '';
  const role = variant === 'error' ? 'alert' : 'status';
  return `<div class="session-turn-complete-hint${variantClass}" role="${role}">
    <p class="session-turn-complete-hint-title">${escapeHtml(title)}</p>
    ${sub}
    ${actionsHtml}
    ${repliesHtml}
  </div>`;
}

function buildArtifactDownloadActionsHtml() {
  return `
    <div class="session-turn-complete-hint-actions">
      <button type="button" class="btn btn-primary btn-sm" data-hint-download>下载模组 jar</button>
      <button type="button" class="btn btn-outline btn-sm" data-hint-test-server title="即将推出">${TEST_SERVER_LABEL}</button>
    </div>`;
}

function buildFirstBuildTurnHintHtml(data) {
  const jarName = escapeHtml(
    data.artifact_file || data.delivery?.artifact_name || 'mod.jar',
  );
  return buildTurnCompleteHint(
    '首次编译已完成',
    `${jarName} 已生成，可快捷下载；测试服务器即将推出。`,
    { actionsHtml: buildArtifactDownloadActionsHtml() },
  );
}

function buildFollowUpTurnHintHtml(data) {
  const sub = data?.workspace_git_stale
    ? `${TURN_COMPLETE_DOWNLOAD_SUB}（将先同步工作区）`
    : TURN_COMPLETE_DOWNLOAD_SUB;
  return buildTurnCompleteHint('Agent 已完成本次回复', sub, {
    actionsHtml: buildArtifactDownloadActionsHtml(),
  });
}

function buildCompactedTurnHintHtml(data) {
  const info = formatCompactedPause(data);
  const composeEnabled = Boolean(data.compose_enabled) && canShowUserTurnHints();
  const replies = info.quickReplies || [];
  const repliesHtml = replies.length
    ? `<div class="session-turn-complete-hint-replies" role="group" aria-label="快捷恢复">
        ${replies
          .map(
            (r, i) =>
              `<button type="button" class="btn btn-outline btn-sm session-quick-reply-btn" data-reply-index="${i}"${composeEnabled ? '' : ' disabled'}>${escapeHtml(r.label)}</button>`,
          )
          .join('')}
      </div>`
    : '';
  const body = info.body && info.body !== info.title ? info.body : '';
  const sub = [body, info.hint].filter(Boolean).join(' · ');
  return buildTurnCompleteHint(info.title, sub || null, { repliesHtml });
}

function buildErrorTurnHintHtml(data) {
  const err = formatSessionError(data);
  const composeEnabled = Boolean(data.compose_enabled) && canShowUserTurnHints();
  const replies = err.quickReplies || [];
  const repliesHtml = replies.length
    ? `<div class="session-turn-complete-hint-replies" role="group" aria-label="快捷恢复">
        ${replies
          .map(
            (r, i) =>
              `<button type="button" class="btn btn-outline btn-sm session-quick-reply-btn" data-reply-index="${i}"${composeEnabled ? '' : ' disabled'}>${escapeHtml(r.label)}</button>`,
          )
          .join('')}
      </div>`
    : '';
  const body = err.body && err.body !== err.title ? err.body : '';
  const sub = [body, err.hint].filter(Boolean).join(' · ');
  return buildTurnCompleteHint(err.title, sub || null, {
    variant: 'error',
    repliesHtml,
  });
}

function bindTurnCompleteHint(wrap, data) {
  const hint = wrap?.querySelector('.session-turn-complete-hint');
  if (!hint) return;

  hint.querySelector('[data-hint-download]')?.addEventListener('click', async (e) => {
    const btn = e.currentTarget;
    if (btn instanceof HTMLButtonElement) btn.disabled = true;
    try {
      await runDownloadArtifact(data);
    } catch (err) {
      showToast(err.message || '下载失败', 'error');
    } finally {
      if (btn instanceof HTMLButtonElement && data?.artifact_ready) btn.disabled = false;
    }
  });

  hint.querySelector('[data-hint-test-server]')?.addEventListener('click', (e) => {
    e.stopPropagation();
    promptTestServerComingSoon();
  });

  if (data?.status === 'error') {
    const err = formatSessionError(data);
    hint.querySelectorAll('.session-quick-reply-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const idx = Number(btn.dataset.replyIndex);
        const reply = err.quickReplies?.[idx];
        if (reply?.text) fillComposeQuickReply(reply.text);
      });
    });
  } else if (isCompactedEarlyStop(data)) {
    const info = formatCompactedPause(data);
    hint.querySelectorAll('.session-quick-reply-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const idx = Number(btn.dataset.replyIndex);
        const reply = info.quickReplies?.[idx];
        if (reply?.text) fillComposeQuickReply(reply.text);
      });
    });
  }
}

function buildTurnCompleteHintHtml(turn, data, isLatest) {
  const runningStatuses = ['starting', 'running', 'waiting_user'];
  if (isLatest && runningStatuses.includes(data.status)) return '';

  if (isLatest && data.status === 'error' && canShowUserTurnHints()) {
    return buildErrorTurnHintHtml(data);
  }

  if (isLatest && data.status === 'completed' && isCompactedEarlyStop(data) && canShowUserTurnHints()) {
    return buildCompactedTurnHintHtml(data);
  }

  const progressKind = turn.progress?.interaction_kind;

  if (
    isLatest
    && data.status === 'completed'
    && canShowUserTurnHints()
    && (progressKind ?? data.interaction_kind) === 'build'
    && data.artifact_ready
  ) {
    return buildFirstBuildTurnHintHtml(data);
  }

  if (isLatest && data.status === 'completed') {
    const kind = progressKind ?? data.interaction_kind;
    if (kind === 'follow_up') {
      if (data.artifact_ready) {
        return buildFollowUpTurnHintHtml(data);
      }
      return buildTurnCompleteHint('Agent 已完成本次回复', TURN_COMPLETE_DOWNLOAD_SUB);
    }
    return '';
  }

  return '';
}

function resolveTurnProgress(turn, sessionData, isLatest) {
  const runningStatuses = ['starting', 'running', 'waiting_user'];
  const sessionStages = sessionData.stages || BUILD_STAGES;
  const sessionIndex = sessionData.stage_index ?? 0;
  const sessionTotal = sessionData.stage_total ?? sessionStages.length;
  const sessionName = sessionData.stage_name || sessionStages[sessionIndex] || '—';

  if (!isLatest) {
    const frozen = turn.progress;
    if (!frozen) return null;
    const stages = frozen.stages || sessionStages;
    const stageIndex = frozen.stage_index ?? 0;
    return {
      stageIndex,
      stageTotal: frozen.stage_total ?? stages.length,
      stageName: frozen.stage_name || stages[stageIndex] || '—',
      stages,
      showBar: false,
    };
  }

  if (runningStatuses.includes(sessionData.status)) {
    return {
      stageIndex: sessionIndex,
      stageTotal: sessionTotal,
      stageName: sessionName,
      stages: sessionStages,
      showBar: true,
    };
  }

  const frozen = turn.progress;
  if (frozen) {
    const stages = frozen.stages || sessionStages;
    const stageIndex = frozen.stage_index ?? 0;
    return {
      stageIndex,
      stageTotal: frozen.stage_total ?? stages.length,
      stageName: frozen.stage_name || stages[stageIndex] || '—',
      stages,
      showBar: false,
    };
  }

  if (sessionData.status === 'completed') {
    const stages = sessionStages;
    const stageIndex = Math.min(sessionIndex, stages.length - 1);
    return {
      stageIndex,
      stageTotal: sessionTotal,
      stageName: sessionName,
      stages,
      showBar: false,
    };
  }

  return null;
}

function updateAssistantTurnNode(wrap, turn, data, isLatest) {
  const summary = wrap.querySelector('.assistant-summary');
  const nextSummary = turn.summary || '';
  if (summary && wrap.dataset.summaryText !== nextSummary) {
    summary.innerHTML = renderMarkdown(nextSummary);
    wrap.dataset.summaryText = nextSummary;
  }

  const progress = resolveTurnProgress(turn, data, isLatest);
  const progressWrap = wrap.querySelector('.agent-turn-progress');
  if (progress) {
    if (!progressWrap) {
      wrap.querySelector('.assistant-summary')?.insertAdjacentHTML(
        'afterend',
        `
    <div class="agent-turn-progress" aria-label="编写进度">
      <div class="agent-turn-progress-head">
        <span class="agent-turn-progress-stage"></span>
        <span class="agent-turn-progress-fraction"></span>
      </div>
      <div class="session-progress session-progress--compact turn-progress-bar" role="list" aria-label="构建阶段"></div>
    </div>`,
      );
    }
    const stageEl = wrap.querySelector('.agent-turn-progress-stage');
    const fractionEl = wrap.querySelector('.agent-turn-progress-fraction');
    if (stageEl) stageEl.textContent = progress.stageName;
    if (fractionEl) {
      fractionEl.textContent = `${progress.stageIndex + 1}/${progress.stageTotal}`;
    }
    const progressBar = wrap.querySelector('.turn-progress-bar');
    if (progressBar) {
      if (progress.showBar) {
        progressBar.hidden = false;
        renderProgressBar(progress.stages, progress.stageIndex, progressBar);
      } else {
        progressBar.hidden = true;
        progressBar.innerHTML = '';
      }
    }
    wrap.querySelector('.agent-turn-progress')?.removeAttribute('hidden');
  } else if (progressWrap) {
    progressWrap.remove();
  }

  const details = wrap.querySelector('.session-turn-details');
  const wasOpen = details?.open || expandedUiState.turnDetails.has(turn.id);
  const detailsBody = wrap.querySelector('.session-turn-details-body');
  const meta = wrap.querySelector('.session-turn-details-meta');
  if (meta) meta.textContent = renderTurnDetailsMeta(turn);
  if (detailsBody && wasOpen) {
    syncTurnDetailsBody(detailsBody, turn, turn.id);
  }
  if (details) {
    details.open = wasOpen;
    bindTurnDetailsToggle(details);
    const detailsLabel = details.querySelector('.session-turn-details-label');
    if (detailsLabel) {
      detailsLabel.textContent = details.open ? '收起执行细节' : '查看执行细节';
    }
  }

  const pendingEl = wrap.querySelector('.session-pending-action');
  const action =
    isLatest && data.pending_action && !turn.user_reply ? data.pending_action : turn.pending_action;
  renderInteractionAction(pendingEl, action, turn);

  renderInteractionAction(pendingEl, action, turn);

  const hintHtml = buildTurnCompleteHintHtml(turn, data, isLatest);
  let hintEl = wrap.querySelector('.session-turn-complete-hint');
  if (hintHtml) {
    if (hintEl) {
      hintEl.outerHTML = hintHtml;
    } else {
      wrap.insertAdjacentHTML('beforeend', hintHtml);
    }
    bindTurnCompleteHint(wrap, data);
  } else if (hintEl) {
    hintEl.remove();
  }

  if (isLatest) {
    ensureMessageActionsBar(wrap, {
      data,
      nodeId: wrap.dataset.nodeId,
      kind: 'assistant',
      isLatestAssistant: true,
    });
  }
}

function createAssistantInteractionNode(turn, data, isLatest, nodeId) {
  const progress = resolveTurnProgress(turn, data, isLatest);
  const wrap = document.createElement('div');
  wrap.className = 'msg msg-assistant';
  wrap.dataset.turnId = turn.id;
  if (nodeId) wrap.dataset.nodeId = nodeId;

  const progressHtml = progress
    ? `
    <div class="agent-turn-progress" aria-label="编写进度">
      <div class="agent-turn-progress-head">
        <span class="agent-turn-progress-stage">${escapeHtml(progress.stageName)}</span>
        <span class="agent-turn-progress-fraction">${progress.stageIndex + 1}/${progress.stageTotal}</span>
      </div>
      <div class="session-progress session-progress--compact turn-progress-bar" role="list" aria-label="构建阶段"${progress.showBar ? '' : ' hidden'}></div>
    </div>`
    : '';

  const completeHintHtml = buildTurnCompleteHintHtml(turn, data, isLatest);

  const detailsOpenAttr =
    expandedUiState.turnDetails.has(turn.id) ||
    (isLatest && data.status === 'starting')
      ? ' open'
      : '';

  if (isLatest && data.status === 'starting') {
    expandedUiState.turnDetails.add(turn.id);
  }

  wrap.innerHTML = `
    <p class="msg-label">Agent</p>
    <div class="assistant-summary desc-optimize-md markdown-body">${renderMarkdown(turn.summary || '')}</div>
    ${progressHtml}
    <details class="session-turn-details"${detailsOpenAttr}>
      <summary class="session-turn-details-summary">
        <svg class="session-turn-chevron" xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
        <span class="session-turn-details-label">${detailsOpenAttr ? '收起执行细节' : '查看执行细节'}</span>
        <span class="session-turn-details-meta">${renderTurnDetailsMeta(turn)}</span>
      </summary>
      <div class="session-turn-details-body">${buildTurnDetailsBodyHtml(turn, turn.id)}</div>
    </details>
    <div class="session-pending-action" hidden></div>
    ${completeHintHtml}
  `;

  const details = wrap.querySelector('.session-turn-details');
  bindTurnDetailsToggle(details);
  bindNestedDetailsToggles(wrap.querySelector('.session-turn-details-body'));

  const pendingEl = wrap.querySelector('.session-pending-action');
  const action =
    isLatest && data.pending_action && !turn.user_reply ? data.pending_action : turn.pending_action;
  renderInteractionAction(pendingEl, action, turn);

  if (progress?.showBar) {
    const progressBar = wrap.querySelector('.turn-progress-bar');
    if (progressBar) renderProgressBar(progress.stages, progress.stageIndex, progressBar);
  }

  bindTurnCompleteHint(wrap, data);
  wrap.dataset.summaryText = turn.summary || '';
  const actions = createMessageActionsBar({
    data,
    nodeId,
    kind: 'assistant',
    isLatestAssistant: isLatest,
  });
  if (actions) wrap.appendChild(actions);
  return wrap;
}

function renderInteractionAction(pendingEl, action, turn) {
  if (turn?.user_reply || !pendingEl || !action) {
    if (pendingEl) pendingEl.hidden = true;
    return;
  }

  const actionsLocked = adminViewMode && !adminInterventionMode;

  pendingEl.hidden = false;

  if (action.type === 'ask_user' && Array.isArray(action.questions) && action.questions.length) {
    renderAskUserAction(pendingEl, action, turn);
    return;
  }

  const choices = action.choices || [];
  const selected = action.selected || pendingChoiceId;

  pendingEl.innerHTML = `
    <p class="session-permission-q">${escapeHtml(action.question || '请确认')}</p>
    <div class="session-perm-grid" role="group"></div>
    <p class="session-actions-footnote" hidden></p>
  `;

  const grid = pendingEl.querySelector('.session-perm-grid');
  const footnote = pendingEl.querySelector('.session-actions-footnote');

  choices.forEach((choice) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'interrupt-picker-option';
    btn.dataset.permChoice = choice.id;
    btn.innerHTML = `<span class="interrupt-picker-option-label">${escapeHtml(choice.label)}</span>`;
    if (selected === choice.id) btn.classList.add('is-selected');
    if (action.selected || actionsLocked) {
      btn.disabled = true;
    } else {
      btn.addEventListener('click', () => handlePermissionChoice(choice.id, btn, grid, footnote, turn, action));
    }
    grid?.appendChild(btn);
  });

  if (actionsLocked && footnote) {
    footnote.hidden = false;
    footnote.textContent = '只读查看；点击「介入编辑」后可代用户确认';
  }
}

function renderAskUserAction(pendingEl, action, turn) {
  const actionsLocked = adminViewMode && !adminInterventionMode;

  pendingEl.innerHTML = `
    <p class="session-permission-q">Agent 需要您的回答</p>
    <form class="session-ask-user-form"></form>
    <p class="session-actions-footnote" hidden></p>
  `;
  const form = pendingEl.querySelector('.session-ask-user-form');
  const footnote = pendingEl.querySelector('.session-actions-footnote');

  action.questions.forEach((q, qi) => {
    const qtext = q.question || q.header || `问题 ${qi + 1}`;
    const multiSelect = isQuestionMultiSelect(q);
    const field = document.createElement('fieldset');
    field.className = 'session-ask-user-field';
    field.dataset.multiSelect = multiSelect ? '1' : '0';
    const legendHint = multiSelect ? ' <span class="session-ask-user-hint">（可多选）</span>' : '';
    field.innerHTML = `<legend>${escapeHtml(qtext)}${legendHint}</legend>`;
    const options = q.options || q.choices || [];
    if (options.length) {
      options.forEach((opt, oi) => {
        const label = opt.label || opt;
        const description = opt.description || '';
        const id = `ask-${qi}-${oi}`;
        const inputType = multiSelect ? 'checkbox' : 'radio';
        const inputName = multiSelect ? `q-${qi}[]` : `q-${qi}`;
        const row = document.createElement('label');
        row.className = 'session-ask-user-option';
        row.innerHTML = `<input type="${inputType}" name="${inputName}" value="${escapeHtml(String(label))}" id="${id}"> ${escapeHtml(String(label))}`;
        if (description) {
          const desc = document.createElement('span');
          desc.className = 'session-ask-user-option-desc';
          desc.textContent = description;
          row.appendChild(desc);
        }
        field.appendChild(row);
      });
    } else {
      const input = document.createElement('input');
      input.type = 'text';
      input.className = 'session-ask-user-input';
      input.dataset.question = qtext;
      input.placeholder = '请输入…';
      field.appendChild(input);
    }
    form?.appendChild(field);
  });

  const submitBtn = document.createElement('button');
  submitBtn.type = 'submit';
  submitBtn.className = 'btn btn-primary btn-sm session-ask-user-submit';
  submitBtn.textContent = '提交回答';
  submitBtn.disabled = actionsLocked;
  form?.appendChild(submitBtn);

  if (actionsLocked) {
    form?.querySelectorAll('input, textarea, select, button').forEach((el) => {
      if (el !== submitBtn) el.disabled = true;
    });
    if (footnote) {
      footnote.hidden = false;
      footnote.textContent = '只读查看；点击「介入编辑」后可代用户回答';
    }
  }

  form?.addEventListener('submit', async (e) => {
    if (actionsLocked) return;
    e.preventDefault();
    const answers = {};
    let missingRequired = false;
    action.questions.forEach((q, qi) => {
      const qtext = q.question || q.header || `问题 ${qi + 1}`;
      const multiSelect = isQuestionMultiSelect(q);
      const field = form.querySelectorAll('.session-ask-user-field')[qi];
      const textInput = field?.querySelector('.session-ask-user-input');
      if (textInput?.value.trim()) {
        answers[qtext] = textInput.value.trim();
        return;
      }
      if (multiSelect) {
        const checked = [...(field?.querySelectorAll('input[type="checkbox"]:checked') || [])].map(
          (el) => el.value,
        );
        if (checked.length) answers[qtext] = checked;
        else if ((q.options || q.choices || []).length) missingRequired = true;
      } else {
        const checked = field?.querySelector('input[type="radio"]:checked');
        if (checked) answers[qtext] = checked.value;
        else if ((q.options || q.choices || []).length) missingRequired = true;
      }
    });
    if (missingRequired) {
      showToast('请完成所有必答问题', 'error');
      return;
    }
    if (footnote) {
      footnote.hidden = false;
      footnote.textContent = '提交中…';
    }
    try {
      await submitSessionAction(sessionId, null, {
        request_id: action.request_id,
        answers,
      });
      turn.user_reply = { answers, created_at: new Date().toISOString() };
    } catch (err) {
      showToast(err.message || '提交失败', 'error');
      if (footnote) footnote.hidden = true;
    }
  });
}

async function handlePermissionChoice(choiceId, btn, grid, footnote, turn, action) {
  grid?.querySelectorAll('[data-perm-choice]').forEach((b) => {
    b.classList.toggle('is-selected', b === btn);
  });
  pendingChoiceId = choiceId;
  if (footnote) {
    footnote.hidden = false;
    footnote.textContent = '提交中…';
  }

  try {
    await submitSessionAction(sessionId, choiceId, {
      request_id: action?.request_id,
    });
    pendingChoiceId = null;
    if (turn) {
      const label = btn?.querySelector('.interrupt-picker-option-label')?.textContent?.trim() || '';
      turn.user_reply = { choice_id: choiceId, label };
    }
  } catch (err) {
    pendingChoiceId = null;
    showToast(err.message || '提交失败', 'error');
    if (footnote) footnote.hidden = true;
  }
}

function resolvePathItems(data) {
  const tree = data.conversation_tree;
  if (!tree?.active_path?.length || !tree.nodes) return null;
  return tree.active_path
    .map((id) => ({ id, node: tree.nodes[id] }))
    .filter((item) => item.node);
}

function resolveTurnForPathItem(node, turns, assistantIndex) {
  if (turns[assistantIndex]) return turns[assistantIndex];
  if (node.turn_snapshot) return node.turn_snapshot;
  return { id: node.turn_id || `interaction-${assistantIndex + 1}` };
}

function deepCopyTurn(turn) {
  if (typeof structuredClone === 'function') {
    try {
      return structuredClone(turn);
    } catch {
      /* fall through */
    }
  }
  return JSON.parse(JSON.stringify(turn));
}

function mirrorTurnToConversationTree(snapshot, turn) {
  const tree = snapshot?.conversation_tree;
  if (!tree?.active_path?.length || !tree.nodes || !turn) return;
  const lastId = tree.active_path[tree.active_path.length - 1];
  const node = tree.nodes[lastId];
  if (node?.type === 'assistant') {
    node.turn_snapshot = deepCopyTurn(turn);
  }
}

function turnContentFingerprint(turn) {
  if (!turn) return '';
  return [
    turn.id,
    turn.summary?.length || 0,
    turn.thinking?.length || 0,
    turn.tools?.length || 0,
    turn.pending_action ? 1 : 0,
    turn.user_reply ? 1 : 0,
  ].join(':');
}

function applyBranchSnapshot(snap) {
  messagesHasRendered = false;
  applySnapshot(snap);
}

function renderMessages(data) {
  const container = els.sessionMessages();
  if (!container) return;

  const scrollEl = document.getElementById('sessionScroll');
  const prevScrollTop = scrollEl?.scrollTop ?? 0;
  const wasNearBottom = !messagesHasRendered || isNearBottom(scrollEl);

  captureExpandedState(container);

  const pathItems = resolvePathItems(data);
  const turns = data.turns || [];
  const followUps = (data.user_messages || []).filter((m) => m.kind === 'follow_up');

  if (pathItems) {
    const newPathIds = pathItems.map((item) => item.id);
    const existingPathIds = [...container.querySelectorAll('[data-node-id]')].map(
      (el) => el.dataset.nodeId,
    );
    const samePath =
      messagesHasRendered
      && existingPathIds.length === newPathIds.length
      && newPathIds.every((id, i) => existingPathIds[i] === id);

    if (samePath && pathItems.length > 0) {
      const lastItem = pathItems[pathItems.length - 1];
      if (lastItem.node.type === 'assistant') {
        const assistantIndex = pathItems.filter((item) => item.node.type === 'assistant').length - 1;
        const turn = resolveTurnForPathItem(lastItem.node, turns, assistantIndex);
        const wrap = container.querySelector(`[data-node-id="${lastItem.id}"]`);
        const prevFingerprint = wrap?.dataset?.turnFingerprint || '';
        const nextFingerprint = turnContentFingerprint(turn);
        const contentOnlyUpdate = prevFingerprint && prevFingerprint !== nextFingerprint;
        if (wrap && (data.status === 'running' || contentOnlyUpdate || !prevFingerprint)) {
          if (data.status === 'starting') {
            expandedUiState.turnDetails.add(turn.id);
          }
          updateAssistantTurnNode(wrap, turn, data, true);
          wrap.dataset.turnFingerprint = nextFingerprint;
          messagesHasRendered = true;
          return;
        }
      }
    }

    container.innerHTML = '';
    let assistantIndex = 0;
    pathItems.forEach((item, index) => {
      const { id, node } = item;
      if (node.type === 'user') {
        if (node.kind === 'initial') {
          const { finalPrompt, readableBlueprint } = resolveUserMessageContent(data);
          container.appendChild(
            createUserMessageNode(
              node.content || finalPrompt,
              node.readable_blueprint || readableBlueprint,
              data,
              id,
            ),
          );
        } else {
          container.appendChild(
            createUserFollowUpNode(
              {
                content: node.content,
                sent_by_admin: node.sent_by_admin,
              },
              data,
              id,
            ),
          );
        }
      } else if (node.type === 'assistant') {
        const turn = resolveTurnForPathItem(node, turns, assistantIndex);
        assistantIndex += 1;
        const isLatest = index === pathItems.length - 1;
        const turnData = {
          ...data,
          pending_action: isLatest && !turn.user_reply ? data.pending_action : null,
          delivery: isLatest && assistantIndex === 1 ? data.delivery : null,
        };
        container.appendChild(createAssistantInteractionNode(turn, turnData, isLatest, id));
      }
    });
    restoreExpandedState(container);
    if (wasNearBottom) {
      maybeScrollToBottom(true);
    } else if (scrollEl) {
      requestAnimationFrame(() => {
        scrollEl.scrollTop = prevScrollTop;
      });
    }
    messagesHasRendered = true;
    return;
  }

  const existingAssistant = [...container.querySelectorAll('.msg-assistant[data-turn-id]')];
  const sameStructure =
    existingAssistant.length === turns.length &&
    turns.every((t, i) => existingAssistant[i]?.dataset.turnId === t.id);

  if (sameStructure && turns.length > 0) {
    const latestTurn = turns[turns.length - 1];
    if (data.status === 'starting') {
      expandedUiState.turnDetails.add(latestTurn.id);
    }
    updateAssistantTurnNode(
      existingAssistant[existingAssistant.length - 1],
      latestTurn,
      data,
      true,
    );
    restoreExpandedState(container);
    if (wasNearBottom) {
      maybeScrollToBottom(true);
    } else if (scrollEl) {
      requestAnimationFrame(() => {
        scrollEl.scrollTop = prevScrollTop;
      });
    }
    messagesHasRendered = true;
    return;
  }

  container.innerHTML = '';
  const { finalPrompt, readableBlueprint } = resolveUserMessageContent(data);
  const rootId = data.conversation_tree?.root_id || null;
  container.appendChild(createUserMessageNode(finalPrompt, readableBlueprint, data, rootId));

  turns.forEach((turn, index) => {
    if (index > 0 && followUps[index - 1]) {
      container.appendChild(createUserFollowUpNode(followUps[index - 1], data));
    }
    const isLatest = index === turns.length - 1;
    const turnData = {
      ...data,
      pending_action: isLatest && !turn.user_reply ? data.pending_action : null,
      delivery: isLatest && index === 0 ? data.delivery : null,
    };
    container.appendChild(createAssistantInteractionNode(turn, turnData, isLatest));
  });

  restoreExpandedState(container);
  if (wasNearBottom) {
    maybeScrollToBottom(true);
  } else if (scrollEl) {
    requestAnimationFrame(() => {
      scrollEl.scrollTop = prevScrollTop;
    });
  }
  messagesHasRendered = true;
}

function applyAdminViewBanner(data) {
  const banner = document.getElementById('sessionAdminBanner');
  const textEl = document.getElementById('sessionAdminBannerText');
  const interveneBtn = document.getElementById('sessionAdminInterveneBtn');
  if (!banner) return;
  if (!adminViewMode) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  const owner = data?.owner_email || data?.owner_id || '—';
  const username = data?.owner_username;
  const ownerLabel = username ? `${username} (${owner})` : owner;
  if (textEl) {
    textEl.textContent = isAdminInterventionActive()
      ? `管理员介入编辑中 · 所属用户：${ownerLabel}`
      : `管理员只读查看 · 所属用户：${ownerLabel}`;
  }
  if (interveneBtn) {
    interveneBtn.textContent = adminInterventionMode ? '退出介入' : '介入编辑';
    interveneBtn.classList.toggle('is-active', adminInterventionMode);
    interveneBtn.setAttribute('aria-pressed', adminInterventionMode ? 'true' : 'false');
  }
  document.getElementById('sessionTitleEditBtn')?.setAttribute('hidden', '');
}

function initAdminInterventionToggle() {
  const btn = document.getElementById('sessionAdminInterveneBtn');
  if (!btn || btn.dataset.bound) return;
  btn.dataset.bound = '1';
  btn.addEventListener('click', () => {
    if (!adminViewMode || !sessionId) return;
    adminInterventionMode = !adminInterventionMode;
    saveAdminInterventionMode(sessionId, adminInterventionMode);
    applyAdminViewBanner(snapshot || {});
    if (snapshot) {
      updateComposeState(snapshot);
      renderMessages(snapshot);
    }
    showToast(
      adminInterventionMode ? '已开启介入编辑，可代用户操作' : '已退出介入编辑',
      'info',
    );
  });
}

function updateComposeState(data) {
  const input = els.sessionComposeInput();
  const stopBtn = els.sessionStopBtn();
  const sendBtn = els.sessionSendBtn();
  const hint = els.sessionComposeHint();

  if (adminViewMode && !adminInterventionMode) {
    if (input) {
      input.disabled = true;
      input.placeholder = '管理员只读查看，无法发送消息';
    }
    if (stopBtn) stopBtn.disabled = true;
    if (sendBtn) sendBtn.disabled = true;
    if (hint) hint.textContent = '管理员只读查看；点击「介入编辑」可代用户操作';
    return;
  }

  if (isAdminInterventionActive()) {
    const enabled = Boolean(data.compose_enabled);
    if (input) {
      input.disabled = !enabled;
      input.placeholder = enabled
        ? '以用户身份介入发送…'
        : 'Agent 编写中，请稍候…';
    }
    if (stopBtn) stopBtn.disabled = !data.can_stop;
    if (sendBtn) sendBtn.disabled = !enabled;
    if (hint) {
      if (data.status === 'waiting_user') {
        hint.textContent = '介入编辑：可代用户确认或发送跟进';
      } else if (data.status === 'error') {
        hint.textContent = '介入编辑：详见上方错误说明，可使用快捷回复';
      } else if (enabled) {
        hint.textContent = '当前为介入编辑模式';
      } else if (data.can_stop) {
        hint.textContent = '介入编辑：Agent 编写中，可停止';
      } else {
        hint.textContent = '当前为介入编辑模式';
      }
    }
    return;
  }

  const enabled = Boolean(data.compose_enabled) && !data.branch_processing;
  if (input) {
    input.disabled = !enabled;
    if (data.branch_processing) {
      input.placeholder = '正在重新生成…';
    } else if (data.status === 'starting') {
      input.placeholder = '正在创建环境…';
    } else {
      input.placeholder = enabled
        ? '描述跟进修改或回复 Agent…'
        : 'Agent 编写中，请稍候…';
    }
  }
  if (stopBtn) stopBtn.disabled = !data.can_stop;
  if (sendBtn) sendBtn.disabled = !enabled;

  if (hint) {
    if (data.branch_processing) {
      hint.textContent = '正在重新生成或切换分支…';
    } else if (data.status === 'starting') {
      const progress = data.setup_progress;
      const activeStep = progress?.steps?.find((step) => step.status === 'active');
      const refHint = getReferenceIndexHint(data);
      if (progress?.retrying) {
        hint.textContent = getSetupRetryLine(data) || '正在重试获取 Fabric 模板…';
      } else if (refHint && !activeStep) {
        hint.textContent = refHint;
      } else if (activeStep && refHint) {
        hint.textContent = `${refHint} · ${activeStep.label}`;
      } else if (activeStep) {
        hint.textContent = `正在创建环境：${activeStep.label}…`;
      } else if (refHint) {
        hint.textContent = refHint;
      } else {
        hint.textContent = '正在创建 Fabric 开发环境…';
      }
    } else if (data.setup_progress?.failed && data.status === 'running') {
      hint.textContent = '模板暂未就绪，Agent 已开始编写代码';
    } else if (data.status === 'waiting_user') {
      hint.textContent = 'Agent 等待你的选择或回复';
    } else if (data.status === 'completed') {
      if (isCompactedEarlyStop(data)) {
        hint.textContent = '上下文已压缩，编写已暂停；发送跟进消息即可继续';
      } else {
        const runHint = formatAgentRunHint(data.agent_run);
        hint.textContent = runHint
          ? `任务已完成 · ${runHint}，可发送跟进修改`
          : '任务已完成，可发送跟进修改';
      }
    } else if (data.status === 'stopped') {
      hint.textContent = '已停止编写';
    } else if (data.status === 'error') {
      hint.textContent = '详见上方 Agent 消息中的错误说明与快捷回复';
    } else if (data.status === 'running') {
      hint.textContent = formatRunningHint(data);
    } else {
      hint.textContent = '正在连接会话…';
    }
  }
}

function applySnapshotPatch(patch) {
  if (!snapshot || !patch) return;
  if (patch.updated_at) snapshot.updated_at = patch.updated_at;
  if (patch.status !== undefined) snapshot.status = patch.status;
  if (patch.stage_index !== undefined) snapshot.stage_index = patch.stage_index;
  if (patch.stage_name !== undefined) snapshot.stage_name = patch.stage_name;
  if (patch.compose_enabled !== undefined) snapshot.compose_enabled = patch.compose_enabled;
  if (patch.can_stop !== undefined) snapshot.can_stop = patch.can_stop;
  if (patch.agent_run) {
    snapshot.agent_run = { ...(snapshot.agent_run || {}), ...patch.agent_run };
  }

  const tp = patch.turn_patch;
  if (tp?.turn_id) {
    let turn = snapshot.turns?.find((t) => t.id === tp.turn_id);
    if (!turn) {
      turn = { id: tp.turn_id, summary: '', thinking: [], tools: [] };
      snapshot.turns = snapshot.turns || [];
      snapshot.turns.push(turn);
    }
    if (tp.summary_append) turn.summary = (turn.summary || '') + tp.summary_append;
    if (tp.thinking_append) {
      const idx = tp.thinking_append.index ?? 0;
      turn.thinking = turn.thinking || [];
      while (turn.thinking.length <= idx) turn.thinking.push('');
      turn.thinking[idx] = (turn.thinking[idx] || '') + (tp.thinking_append.text || '');
    }
    if (tp.tool_added) {
      turn.tools = turn.tools || [];
      turn.tools.push(tp.tool_added);
    }
    mirrorTurnToConversationTree(snapshot, turn);
  }

  applySnapshotImmediate(snapshot);
}

function applySnapshotImmediate(data) {
  snapshot = mergeSnapshotHeavyFields(snapshot, data);
  if (data.workspace_git_warning) {
    showToast(data.workspace_git_warning, 'warn', 6000);
  }
  renderHero(data);
  applyAdminViewBanner(data);
  renderSetupNotice(data);
  maybeNotifySessionError(data);
  renderMessages(data);
  updateComposeState(data);
  if (data.status === 'running') {
    startSilentHintTimer();
  } else {
    stopSilentHintTimer();
  }

  upsertSessionListEntry({
    sessionId: data.session_id,
    taskTitle: resolveSessionHeroTitle(data),
    mode: data.mode,
    status: data.status,
    createdAt: Date.parse(data.created_at) || Date.now(),
  });
  renderSessionListPanel(true);
  setSessionListActiveId(data.session_id);
  syncTopBarProjectActions(data);
}

function applySnapshot(data) {
  pendingSnapshot = data;
  if (snapshotRafId !== null) return;
  snapshotRafId = requestAnimationFrame(() => {
    snapshotRafId = null;
    const next = pendingSnapshot;
    pendingSnapshot = null;
    if (next) applySnapshotImmediate(next);
  });
}

function refreshHistoryPanelRemote(force = false) {
  const now = Date.now();
  if (!force && now - lastHistoryRemoteFetch < HISTORY_REMOTE_DEBOUNCE_MS) {
    if (historyRemoteTimer) return;
    historyRemoteTimer = window.setTimeout(() => {
      historyRemoteTimer = null;
      refreshHistoryPanelRemote(true);
    }, HISTORY_REMOTE_DEBOUNCE_MS - (now - lastHistoryRemoteFetch));
    return;
  }
  lastHistoryRemoteFetch = Date.now();
  renderSessionListPanel(true).then(() => setSessionListActiveId(sessionId));
}

async function renderHistoryPanel() {
  await renderSessionListPanel(true);
  setSessionListActiveId(sessionId);
}

function initTitleEdit() {
  const row = document.querySelector('.session-hero-title-row');
  const titleEl = document.getElementById('sessionTaskTitle');
  const inputEl = document.getElementById('sessionTaskTitleInput');
  const editBtn = document.getElementById('sessionTitleEditBtn');
  if (!row || !titleEl || !inputEl || !editBtn) return;

  const finishEdit = async (save) => {
    if (save) {
      const next = inputEl.value.trim();
      if (next) {
        localStorage.setItem(TASK_TITLE_KEY, next);
        titleEl.textContent = next;
        if (sessionId) {
          try {
            await patchSessionTitle(sessionId, next);
          } catch {
            /* local only */
          }
        }
      }
    }
    row.classList.remove('is-editing');
    inputEl.hidden = true;
  };

  editBtn.addEventListener('click', () => {
    inputEl.value = titleEl.textContent?.trim() || '';
    row.classList.add('is-editing');
    inputEl.hidden = false;
    inputEl.focus();
    inputEl.select();
  });

  inputEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      finishEdit(true);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finishEdit(false);
    }
  });
  inputEl.addEventListener('blur', () => finishEdit(true));
}

function initTopBarGlassToggle() {
  const topBar = document.getElementById('sessionTopBar');
  const inner = document.getElementById('sessionTopBarInner');
  if (!topBar || !inner) return;

  const applyGlass = (on) => {
    topBar.classList.toggle('session-top-bar--glass', on);
    localStorage.setItem(TOPBAR_GLASS_KEY, on ? '1' : '0');
  };

  applyGlass(localStorage.getItem(TOPBAR_GLASS_KEY) === '1');

  inner.addEventListener('click', (e) => {
    if (e.target.closest('button, a, input, textarea, select, [role="menu"]')) return;
    applyGlass(!topBar.classList.contains('session-top-bar--glass'));
  });
}

function initTopBarCollapse() {
  const scrollEl = document.getElementById('sessionScroll');
  const topBar = document.getElementById('sessionTopBar');
  if (!scrollEl || !topBar) return;

  const THRESHOLD = 16;
  let ticking = false;
  let scrollEndTimer = null;

  const update = () => {
    if (scrollEl.scrollTop > THRESHOLD) {
      topBar.classList.add('session-top-bar--collapsed');
    } else {
      topBar.classList.remove('session-top-bar--collapsed');
    }
    ticking = false;
  };

  scrollEl.addEventListener(
    'scroll',
    () => {
      scrollEl.classList.add('is-scrolling');
      clearTimeout(scrollEndTimer);
      scrollEndTimer = window.setTimeout(() => {
        scrollEl.classList.remove('is-scrolling');
      }, 150);
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(update);
    },
    { passive: true },
  );
  update();
}

function initCompose() {
  const input = els.sessionComposeInput();
  const sendBtn = els.sessionSendBtn();
  const stopBtn = els.sessionStopBtn();

  sendBtn?.addEventListener('click', async () => {
    const text = input?.value.trim();
    if (!text || !sessionId) return;
    sendBtn.disabled = true;
    try {
      await sendSessionMessage(sessionId, text);
      if (input) input.value = '';
    } catch (err) {
      showToast(err.message || '发送失败', 'error');
    } finally {
      sendBtn.disabled = false;
    }
  });

  input?.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      sendBtn?.click();
    }
  });

  stopBtn?.addEventListener('click', async () => {
    if (!sessionId) return;
    stopBtn.disabled = true;
    try {
      await stopSession(sessionId);
      showToast('已停止编写', 'info');
    } catch (err) {
      showToast(err.message || '停止失败', 'error');
      stopBtn.disabled = false;
    }
  });
}

async function resolveSession() {
  adminViewMode = isAdminViewMode();
  if (!adminViewMode && !getToken()) {
    requireAuth();
    return;
  }

  const urlId = getSessionIdFromUrl();
  const bootstrap = loadBootstrap();

  if (urlId) {
    sessionId = urlId;
    adminInterventionMode = loadAdminInterventionMode(urlId);
    return;
  }

  if (bootstrap?.sessionId) {
    sessionId = bootstrap.sessionId;
    adminInterventionMode = loadAdminInterventionMode(sessionId);
    window.history.replaceState({}, '', `session.html?session_id=${sessionId}`);
    return;
  }

  showToast('缺少会话 ID，请从首页开始编写', 'error');
  window.location.href = '/index.html';
}

async function connectSession() {
  await resolveSession();

  const bootstrap = loadBootstrap();
  if (bootstrap?.payload?.mod_name || bootstrap?.taskTitle) {
    renderHero({
      mod_name: bootstrap.payload?.mod_name,
      task_title: bootstrap.taskTitle,
      readable_blueprint: bootstrap.readableBlueprint,
    });
  }

  try {
    const initial = await fetchSessionSnapshot(sessionId);
    applySnapshot(initial.snapshot);
    refreshHistoryPanelRemote(true);
  } catch (err) {
    showToast(err.message || '无法加载会话', 'error');
    return;
  }

  unsubscribe = subscribeSession(
    sessionId,
    (event) => {
      if (event.type === 'reference_step' || event.type === 'reference_indexing' || event.type === 'reference_ready' || event.type === 'reference_failed') {
        handleReferenceStreamEvent(event);
      }
      if (event.type === 'patch' && event.data) {
        applySnapshotPatch(event.data);
      } else if (event.type === 'snapshot' && event.data) {
        applySnapshot(event.data);
      }
    },
    (err) => {
      showToast('连接中断，正在尝试恢复…', 'error');
      if (!sessionId) return;
      fetchSessionSnapshot(sessionId)
        .then(({ snapshot: snap }) => applySnapshot(snap))
        .catch(() => {
          showToast('无法恢复会话，请刷新页面', 'error');
        });
    },
  );
}

async function init() {
  adminViewMode = isAdminViewMode();
  initAdminInterventionToggle();
  applyAdminViewBanner({});
  initTheme();
  if (!adminViewMode && requireAuth()) {
    initAccountStatus();
  }
  initNavigation();
  initTopBarToolbar();
  initTopBarGlassToggle();
  initTopBarCollapse();
  initTitleEdit();
  initCompose();

  document.getElementById('themeToggle')?.addEventListener('click', () => {
    applyTheme(document.documentElement.classList.contains('dark') ? 'light' : 'dark');
  });

  await initConceptDrafts();

  connectSession();

  window.addEventListener('beforeunload', () => {
    stopSilentHintTimer();
    unsubscribe?.();
  });
}

init();
