import {
  API,
  DEBUG_MOCK,
  VALIDATION,
  DEFAULTS,
  VERSION_LOADERS,
  MC_VERSIONS,
  UNSUPPORTED_SUFFIX,
  findFirstEnabled,
  PLATFORMS,
  REQUIREMENT_TITLE_MAX_CHARS,
  REF_MOD_DESCRIPTION_PREVIEW_LEN,
  LOCALE,
  TOKEN_STORAGE_KEY,
  MAX_REFERENCE_MODS,
  INTERRUPTION_LEVELS,
  DEFAULT_INTERRUPTION_LEVEL,
  INTERRUPTION_LEVEL_KEY,
  DEFAULT_MAX_TURNS,
  MAX_TURNS_MIN,
  MAX_TURNS_MAX,
  MAX_TURNS_KEY,
  getProjectType,
  PROJECT_TYPE_LABELS,
} from './config.js';
import {
  getActiveRequirements,
  initDefaultRequirements,
  reconcileRequirementsWithServerDefaults,
  syncRequirementsFromServerDefaults,
  loadRequirements,
  addRequirement,
  updateRequirement,
  deleteRequirement,
  reorderRequirements,
  resetRequirementsToDefaults,
  validateTitle,
  countChineseChars,
  displayTitle,
} from './requirements-store.js';
import {
  loadReferenceModsState,
  saveReferenceModsState,
  parseModrinthPermalink,
  parseGithubRepo,
  checkVersionCompatibility,
  getVersionCompatibilityIssues,
  buildReferenceModsPayload,
  hasCodeReferenceMods,
  hasReferenceEntry,
  resolveModrinthProject,
  isRefModSourceMissing,
  isRefModIncludedInPrompt,
  isDirectSourceRef,
  refModKey,
} from './reference-mods.js';
import { MOCK_OPTIMIZED_DESCRIPTION } from './mock-optimized-desc.js';
import { renderMarkdown } from './markdown-render.js';
import { buildFinalPrompt, buildHandoffAppendix } from './prompt-builder.js';
import {
  buildReadableBlueprint,
  buildSessionBootstrap,
  deriveTaskTitle,
  upsertSessionListEntry,
} from './session-utils.js';
import { SESSION_STORAGE_BOOTSTRAP } from './mock-session-bootstrap.js';
import { createSession } from './session-api.js';
import { createPlanSession } from './plan-api.js';
import { collectL1FromForm, fetchKnowledgeL1, renderL1Form, saveKnowledgeL1 } from './plan-l1.js';
import { authHeaders, getToken, isGuest, fetchSiteSettings } from './auth.js';
import { initAccountStatus } from './account-status.js';
import {
  initConceptDrafts,
  getActiveDraft,
  getUiState,
  setUiState,
  updateActiveDraft,
  flushConceptDraftSave,
  touchDraftAccess,
} from './concept-drafts.js';
import { initSessionListPanel, renderSessionListPanel } from './session-list-panel.js';
import { attachDragSort, createDragHandle } from './list-drag-sort.js';
import {
  formatRequirementCostText,
  getRequirementCostBadge,
} from './requirement-cost.js';
import {
  deriveModId,
  derivePackageName,
  setModTemplatePackage,
  validateModName,
  validateModId,
  validatePackageName,
} from './mod-metadata-validation.js';

// --- State ---
const state = {
  mcVersion: DEFAULTS.mcVersion,
  modLoader: DEFAULTS.modLoader,
  platform: DEFAULTS.platform,
  requirements: new Set(),
  popoverReqId: null,
  refModPopoverKey: null,
  /** @type {{ key: string, rect: DOMRect } | null} */
  refModPopoverAnchor: null,
  isSubmitting: false,
  /** @type {object | null | undefined} */
  cachedKnowledgeL1: undefined,
  editingCustomId: null,
  referenceMods: isGuest()
    ? { manual: [], autoSearch: false, collapsed: true }
    : loadReferenceModsState(),
  historyOpen: false,
  descOptimizeEnabled: true,
  descOptimize: {
    visible: false,
    bodyCollapsed: false,
    loading: false,
    text: '',
    adoptConfirming: false,
  },
};

const INFO_ICON_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>';

// --- DOM refs ---
const $ = (sel) => document.querySelector(sel);

const els = {
  html: document.documentElement,
  appLayout: $('#appLayout'),
  themeToggle: $('#themeToggle'),
  historyToggle: $('#historyToggle'),
  historyBackdrop: $('#historyBackdrop'),
  sessionListPanel: $('#sessionListPanel'),
  sessionListPanelBody: $('#sessionListPanelBody'),
  sessionListPanelTitle: $('#sessionListPanelTitle'),
  sessionListRecycleBtn: $('#sessionListRecycleBtn'),
  conceptNewBtn: $('#conceptNewBtn'),
  promptForm: $('#promptForm'),
  textareaWrap: $('#textareaWrap'),
  promptInput: $('#promptInput'),
  promptTextareaResize: $('#promptTextareaResize'),
  optimizeDescBtn: $('#optimizeDescBtn'),
  optimizeDescLoading: $('#optimizeDescLoading'),
  descOptimizePanel: $('#descOptimizePanel'),
  descOptimizeCollapseBtn: $('#descOptimizeCollapseBtn'),
  descOptimizeBody: $('#descOptimizeBody'),
  descOptimizeContent: $('#descOptimizeContent'),
  adoptDescBtn: $('#adoptDescBtn'),
  adoptDescConfirm: $('#adoptDescConfirm'),
  adoptDescConfirmBtn: $('#adoptDescConfirmBtn'),
  adoptDescCancelBtn: $('#adoptDescCancelBtn'),
  planDepthPopover: $('#planDepthPopover'),
  planDepthOptions: $('#planDepthOptions'),
  promptActions: $('#promptActions'),
  promptActionsSlot: $('#promptActionsSlot'),
  descOptimizeActionsSlot: $('#descOptimizeActionsSlot'),
  startBuildBtn: $('#startBuildBtn'),
  startPlanBtn: $('#startPlanBtn'),
  submitLoading: $('#submitLoading'),
  requirementPopover: $('#requirementPopover'),
  requirementPopoverTitle: $('#requirementPopoverTitle'),
  requirementPopoverCost: $('#requirementPopoverCost'),
  requirementPopoverBody: $('#requirementPopoverBody'),
  mcVersionTrigger: $('#mcVersionTrigger'),
  mcVersionValue: $('#mcVersionValue'),
  mcVersionMenu: $('#mcVersionMenu'),
  modLoaderTrigger: $('#modLoaderTrigger'),
  modLoaderValue: $('#modLoaderValue'),
  modLoaderMenu: $('#modLoaderMenu'),
  platformTrigger: $('#platformTrigger'),
  platformValue: $('#platformValue'),
  platformMenu: $('#platformMenu'),
  promptPreviewBtn: $('#promptPreviewBtn'),
  promptPreviewOverlay: $('#promptPreviewOverlay'),
  promptPreviewContent: $('#promptPreviewContent'),
  promptPreviewClose: $('#promptPreviewClose'),
  promptPreviewDone: $('#promptPreviewDone'),
  homeL1Overlay: $('#homeL1Overlay'),
  homeL1Body: $('#homeL1Body'),
  homeL1Cancel: $('#homeL1Cancel'),
  homeL1Confirm: $('#homeL1Confirm'),
  requirementsGrid: $('#requirementsGrid'),
  requirementsConfigBtn: $('#requirementsConfigBtn'),
  requirementsModalOverlay: $('#requirementsModalOverlay'),
  requirementsModal: $('#requirementsModal'),
  requirementsModalClose: $('#requirementsModalClose'),
  requirementsModalDone: $('#requirementsModalDone'),
  reqList: $('#reqList'),
  reqListEmpty: $('#reqListEmpty'),
  resetCustomReqBtn: $('#resetCustomReqBtn'),
  customFormTitle: $('#customFormTitle'),
  customFormEditId: $('#customFormEditId'),
  customReqTitle: $('#customReqTitle'),
  customReqTitleCounter: $('#customReqTitleCounter'),
  customReqDescription: $('#customReqDescription'),
  customReqPrompt: $('#customReqPrompt'),
  customFormCancelBtn: $('#customFormCancelBtn'),
  customFormSaveBtn: $('#customFormSaveBtn'),
  referenceModsSection: $('#referenceModsSection'),
  referenceModsBody: $('#referenceModsBody'),
  referenceModsToggle: $('#referenceModsToggle'),
  referenceModInput: $('#referenceModInput'),
  referenceModCodeBtn: $('#referenceModCodeBtn'),
  referenceModFeatureBtn: $('#referenceModFeatureBtn'),
  referenceModChips: $('#referenceModChips'),
  referenceModAutoSearch: $('#referenceModAutoSearch'),
  refModPopover: $('#refModPopover'),
  refModPopoverTitle: $('#refModPopoverTitle'),
  refModPopoverBody: $('#refModPopoverBody'),
  toastContainer: $('#toastContainer'),
  advancedSettingsSection: $('#advancedSettingsSection'),
  advancedSettingsBody: $('#advancedSettingsBody'),
  advancedSettingsToggle: $('#advancedSettingsToggle'),
  advancedInterruptOptions: $('#advancedInterruptOptions'),
  advancedMaxTurns: $('#advancedMaxTurns'),
  advancedMaxTurnsError: $('#advancedMaxTurnsError'),
  advancedModName: $('#advancedModName'),
  advancedModId: $('#advancedModId'),
  advancedPackageName: $('#advancedPackageName'),
  advancedModNameError: $('#advancedModNameError'),
  advancedModIdError: $('#advancedModIdError'),
  advancedPackageNameError: $('#advancedPackageNameError'),
};

const THEME_KEY = 'mcmod_agent_theme';
const MOBILE_MQ = window.matchMedia('(max-width: 767px)');

const NAV_ACTION_MESSAGES = {
  'mod-search': '模组查找功能即将推出',
  'mod-source': '模组源码分析功能即将推出',
  'mod-redev': '模组二次开发功能即将推出',
  'help-docs': '使用说明即将推出',
  'help-support': '联系客服功能即将推出',
  'lang-zh': '语言切换功能即将推出',
  'lang-en': '语言切换功能即将推出',
};

// --- Theme ---
/** 五套切换动画，每次点击随机其一（不写入 localStorage） */
const THEME_SCHEMES = ['circle', 'fade', 'icon-only', 'wipe', 'circle-minimal'];

const THEME_ICON_ANIM_MS = {
  circle: 500,
  fade: 350,
  'icon-only': 300,
  wipe: 450,
  'circle-minimal': 350,
};

function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function setThemeTransitionOrigin() {
  const rect = els.themeToggle.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  const endRadius = Math.hypot(
    Math.max(x, window.innerWidth - x),
    Math.max(y, window.innerHeight - y),
  );
  els.html.style.setProperty('--theme-transition-x', `${x}px`);
  els.html.style.setProperty('--theme-transition-y', `${y}px`);
  els.html.style.setProperty('--theme-transition-r', `${endRadius}px`);
}

function pickRandomThemeScheme() {
  return THEME_SCHEMES[Math.floor(Math.random() * THEME_SCHEMES.length)];
}

function clearThemeTransitionAttrs() {
  els.html.removeAttribute('data-theme-scheme');
  els.html.removeAttribute('data-theme-transition');
}

function pulseThemeToggle(nextTheme, scheme) {
  const dir = nextTheme === 'dark' ? 'to-dark' : 'to-light';
  const duration = THEME_ICON_ANIM_MS[scheme] ?? 400;
  els.themeToggle.classList.remove('is-animating', 'to-dark', 'to-light');
  els.themeToggle.classList.add('is-animating', dir);
  els.themeToggle.dataset.scheme = scheme;
  window.setTimeout(() => {
    els.themeToggle.classList.remove('is-animating', 'to-dark', 'to-light');
    delete els.themeToggle.dataset.scheme;
  }, duration);
}

function applyTheme(theme, { animate = false, scheme = null } = {}) {
  const isDark = theme === 'dark';
  els.html.classList.toggle('dark', isDark);
  els.themeToggle.setAttribute('aria-label', isDark ? '切换到日间模式' : '切换到夜间模式');
  localStorage.setItem(THEME_KEY, theme);
  if (animate && scheme) pulseThemeToggle(theme, scheme);
}

function initTheme() {
  const stored = localStorage.getItem(THEME_KEY);
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  const theme = stored === 'dark' || stored === 'light' ? stored : prefersDark ? 'dark' : 'light';
  applyTheme(theme);
}

function toggleTheme() {
  const isDark = els.html.classList.contains('dark');
  const nextTheme = isDark ? 'light' : 'dark';
  const transitionDir = nextTheme === 'dark' ? 'to-dark' : 'to-light';

  if (prefersReducedMotion()) {
    applyTheme(nextTheme);
    return;
  }

  setThemeTransitionOrigin();
  let scheme = pickRandomThemeScheme();
  if (!document.startViewTransition) {
    scheme = 'icon-only';
  }

  els.html.setAttribute('data-theme-scheme', scheme);
  els.html.setAttribute('data-theme-transition', transitionDir);

  if (scheme === 'icon-only') {
    applyTheme(nextTheme, { animate: true, scheme });
    window.setTimeout(clearThemeTransitionAttrs, THEME_ICON_ANIM_MS['icon-only']);
    return;
  }

  const transition = document.startViewTransition(() => {
    applyTheme(nextTheme, { animate: true, scheme });
  });

  transition.finished.finally(clearThemeTransitionAttrs);
}

// --- Toast ---
function showToast(message, type = 'info', duration = 5000) {
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
  els.toastContainer.appendChild(toast);

  if (duration > 0) {
    setTimeout(() => toast.remove(), duration);
  }
}

// --- Dropdown ---
function closeAllDropdowns(except) {
  document.querySelectorAll('.dropdown').forEach((dd) => {
    if (dd === except) return;
    const trigger = dd.querySelector('.dropdown-trigger');
    const menu = dd.querySelector('.dropdown-menu');
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
    if (menu) menu.hidden = true;
  });
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

function toggleNavMenu(trigger, menu) {
  const isOpen = trigger.getAttribute('aria-expanded') === 'true';
  closeAllNavMenus(isOpen ? null : trigger.closest('.nav-dropdown'));
  closeAllDropdowns();
  if (isOpen) {
    trigger.setAttribute('aria-expanded', 'false');
    menu.hidden = true;
  } else {
    trigger.setAttribute('aria-expanded', 'true');
    menu.hidden = false;
  }
}

function syncHistoryPanelUi(open) {
  state.historyOpen = open;
  els.historyToggle?.setAttribute('aria-expanded', open ? 'true' : 'false');
  if (els.sessionListPanel) els.sessionListPanel.hidden = !open;
  if (els.historyBackdrop) {
    els.historyBackdrop.hidden = !(open && MOBILE_MQ.matches);
  }
  if (open) renderSessionListPanel(true);
}

function openHistoryPanel() {
  els.appLayout?.classList.add('app-layout--history-open');
  syncHistoryPanelUi(true);
}

function closeHistoryPanel() {
  els.appLayout?.classList.remove('app-layout--history-open');
  syncHistoryPanelUi(false);
}

function toggleHistoryPanel() {
  if (state.historyOpen) closeHistoryPanel();
  else openHistoryPanel();
}

function appendUnsupportedSuffix(btn) {
  const suffix = document.createElement('span');
  suffix.className = 'dropdown-item-suffix';
  suffix.textContent = UNSUPPORTED_SUFFIX;
  btn.appendChild(suffix);
}

function renderMcVersionMenu() {
  els.mcVersionMenu.innerHTML = '';
  MC_VERSIONS.forEach((version) => {
    const li = document.createElement('li');
    li.setAttribute('role', 'presentation');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dropdown-item';
    btn.setAttribute('role', 'option');
    btn.setAttribute(
      'aria-selected',
      !version.disabled && version.value === state.mcVersion ? 'true' : 'false'
    );
    btn.dataset.value = version.value;

    const labelSpan = document.createElement('span');
    labelSpan.className = 'dropdown-item-label';
    labelSpan.textContent = version.value;
    btn.appendChild(labelSpan);

    if (version.disabled) {
      btn.disabled = true;
      btn.setAttribute('aria-disabled', 'true');
      appendUnsupportedSuffix(btn);
    } else {
      btn.addEventListener('click', () => selectMcVersion(version.value));
    }

    li.appendChild(btn);
    els.mcVersionMenu.appendChild(li);
  });
}

function createLoaderDot(loaderValue) {
  const dot = document.createElement('span');
  dot.className = 'loader-dot';
  dot.setAttribute('aria-hidden', 'true');
  dot.style.setProperty(
    '--loader-dot-color',
    `var(--loader-color-${loaderValue}, var(--muted-foreground))`
  );
  return dot;
}

function updateModLoaderValueDisplay(label, value) {
  els.modLoaderValue.replaceChildren();
  const inner = document.createElement('span');
  inner.className = 'dropdown-value-inner';
  const text = document.createElement('span');
  text.className = 'dropdown-value-text';
  text.textContent = label;
  inner.append(createLoaderDot(value), text);
  els.modLoaderValue.appendChild(inner);
}

function renderModLoaderMenu() {
  const loaders = VERSION_LOADERS[state.mcVersion] || [];
  els.modLoaderMenu.innerHTML = '';

  loaders.forEach((loader) => {
    const li = document.createElement('li');
    li.setAttribute('role', 'presentation');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dropdown-item';
    btn.setAttribute('role', 'option');
    btn.setAttribute(
      'aria-selected',
      !loader.disabled && loader.value === state.modLoader ? 'true' : 'false'
    );
    btn.dataset.value = loader.value;
    const labelSpan = document.createElement('span');
    labelSpan.className = 'dropdown-item-label';
    labelSpan.textContent = loader.label;
    btn.append(createLoaderDot(loader.value), labelSpan);

    if (loader.disabled) {
      btn.disabled = true;
      btn.setAttribute('aria-disabled', 'true');
      appendUnsupportedSuffix(btn);
    } else {
      btn.addEventListener('click', () => selectModLoader(loader.value, loader.label));
    }

    li.appendChild(btn);
    els.modLoaderMenu.appendChild(li);
  });
}

function selectMcVersion(version) {
  const versionOption = MC_VERSIONS.find((v) => v.value === version);
  if (!versionOption || versionOption.disabled) return;

  state.mcVersion = version;
  els.mcVersionValue.textContent = version;

  const loaders = VERSION_LOADERS[version] || [];
  const current = loaders.find((l) => l.value === state.modLoader);
  const currentValid = current && !current.disabled;
  if (!currentValid) {
    const fallback = findFirstEnabled(loaders);
    if (fallback) selectModLoader(fallback.value, fallback.label);
  } else {
    updateModLoaderValueDisplay(current.label, current.value);
  }

  renderMcVersionMenu();
  renderModLoaderMenu();
  closeDropdown(els.mcVersionTrigger, els.mcVersionMenu);
  renderReferenceModChips();
  saveDraftFromForm();
}

function selectModLoader(value, label) {
  const loaders = VERSION_LOADERS[state.mcVersion] || [];
  const loader = loaders.find((l) => l.value === value);
  if (!loader || loader.disabled) return;

  state.modLoader = value;
  updateModLoaderValueDisplay(label, value);
  renderModLoaderMenu();
  closeDropdown(els.modLoaderTrigger, els.modLoaderMenu);
  renderReferenceModChips();
  saveDraftFromForm();
}

function renderPlatformMenu() {
  els.platformMenu.innerHTML = '';
  PLATFORMS.forEach((p) => {
    const li = document.createElement('li');
    li.setAttribute('role', 'presentation');
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'dropdown-item';
    btn.setAttribute('role', 'option');
    btn.setAttribute('aria-selected', p.value === state.platform ? 'true' : 'false');
    btn.textContent = p.label;
    btn.dataset.value = p.value;
    btn.addEventListener('click', () => selectPlatform(p.value, p.label));
    li.appendChild(btn);
    els.platformMenu.appendChild(li);
  });
}

function selectPlatform(value, label) {
  state.platform = value;
  els.platformValue.textContent = label;
  renderPlatformMenu();
  closeDropdown(els.platformTrigger, els.platformMenu);
  saveDraftFromForm();
}

function getPlatformLabel(value) {
  return PLATFORMS.find((p) => p.value === value)?.label || value;
}

function getLoaderLabel(value) {
  const loaders = VERSION_LOADERS[state.mcVersion] || [];
  return loaders.find((l) => l.value === value)?.label || value;
}

function getConceptLabelShort() {
  const type = getProjectType(state.modLoader);
  return `${PROJECT_TYPE_LABELS[type]}构想`;
}

function closeDropdown(trigger, menu) {
  trigger.setAttribute('aria-expanded', 'false');
  menu.hidden = true;
}

function toggleDropdown(trigger, menu) {
  const isOpen = trigger.getAttribute('aria-expanded') === 'true';
  closeAllDropdowns(trigger.closest('.dropdown'));
  closeAllNavMenus();
  if (isOpen) {
    closeDropdown(trigger, menu);
  } else {
    trigger.setAttribute('aria-expanded', 'true');
    menu.hidden = false;
  }
}

function initDropdowns() {
  renderMcVersionMenu();
  renderModLoaderMenu();
  renderPlatformMenu();

  const defaultPlatform = PLATFORMS.find((p) => p.value === DEFAULTS.platform);
  if (defaultPlatform) {
    els.platformValue.textContent = defaultPlatform.label;
  }

  els.mcVersionTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleDropdown(els.mcVersionTrigger, els.mcVersionMenu);
  });

  els.modLoaderTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleDropdown(els.modLoaderTrigger, els.modLoaderMenu);
  });

  els.platformTrigger.addEventListener('click', (e) => {
    e.stopPropagation();
    toggleDropdown(els.platformTrigger, els.platformMenu);
  });

  document.addEventListener('click', () => {
    closeAllDropdowns();
    closeAllNavMenus();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      closeAllDropdowns();
      closeAllNavMenus();
      closeHistoryPanel();
      closeInterruptPicker();
      closeRequirementPopover();
      closeRefModPopover();
      if (!els.requirementsModalOverlay.hidden) closeRequirementsModal();
      if (!els.promptPreviewOverlay.hidden) closePromptPreview();
    }
  });
}

function initNavigation() {
  els.historyToggle?.addEventListener('click', (e) => {
    e.stopPropagation();
    closeAllNavMenus();
    toggleHistoryPanel();
  });

  els.historyBackdrop?.addEventListener('click', closeHistoryPanel);

  document.querySelectorAll('.nav-dropdown-trigger').forEach((trigger) => {
    const menu = trigger.nextElementSibling;
    if (!menu?.classList.contains('nav-dropdown-menu')) return;
    trigger.addEventListener('click', (e) => {
      e.stopPropagation();
      closeHistoryPanel();
      toggleNavMenu(trigger, menu);
    });
  });

  document.querySelectorAll('.nav-dropdown-item').forEach((item) => {
    item.addEventListener('click', () => {
      const action = item.dataset.navAction;
      const msg = NAV_ACTION_MESSAGES[action];
      if (msg) showToast(msg, 'info');
      closeAllNavMenus();
    });
  });

  MOBILE_MQ.addEventListener('change', () => {
    if (state.historyOpen) syncHistoryPanelUi(true);
  });
}

// --- Popover positioning ---
function isValidAnchorRect(rect) {
  if (!rect) return false;
  if (rect.width > 0 || rect.height > 0) return true;
  return !(rect.top === 0 && rect.left === 0);
}

function resolveAnchorRect(anchorEl, anchorRect) {
  if (anchorRect && isValidAnchorRect(anchorRect)) return anchorRect;
  if (anchorEl?.isConnected) {
    const live = anchorEl.getBoundingClientRect();
    if (isValidAnchorRect(live)) return live;
  }
  return null;
}

function waitForPopoverLayout() {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(resolve));
  });
}

function resetPopoverBodyScroll(popoverEl) {
  const body = popoverEl?.querySelector('.req-popover-body');
  if (!body) return;
  body.style.maxHeight = '';
  body.style.overflowY = '';
  body.classList.remove('req-popover-body--scroll');
  popoverEl.style.maxHeight = '';
}

/** 空间足够时展示完整内容；仅超出按钮上方可用高度时才限制 body 并滚动 */
function fitPopoverToAvailableSpace(popoverEl, anchorRect) {
  const body = popoverEl.querySelector('.req-popover-body');
  if (!body) return;

  const margin = 12;
  const gap = 8;
  const maxPopoverHeight = Math.max(
    96,
    Math.min(anchorRect.top - margin - gap, window.innerHeight - margin * 2)
  );

  resetPopoverBodyScroll(popoverEl);

  const naturalHeight = popoverEl.getBoundingClientRect().height;
  if (naturalHeight <= maxPopoverHeight) {
    return;
  }

  const titleEl = popoverEl.querySelector('.req-popover-title');
  const popStyles = getComputedStyle(popoverEl);
  const paddingY =
    parseFloat(popStyles.paddingTop) + parseFloat(popStyles.paddingBottom);
  const borderY =
    parseFloat(popStyles.borderTopWidth) + parseFloat(popStyles.borderBottomWidth);
  let titleBlock = 0;
  if (titleEl) {
    const titleStyles = getComputedStyle(titleEl);
    titleBlock = titleEl.offsetHeight + (parseFloat(titleStyles.marginBottom) || 0);
  }

  const bodyMax = maxPopoverHeight - paddingY - borderY - titleBlock;
  body.style.maxHeight = `${Math.max(48, Math.floor(bodyMax))}px`;
  body.style.overflowY = 'auto';
  body.classList.add('req-popover-body--scroll');
}

/**
 * @param {HTMLElement} popoverEl
 * @param {HTMLElement | null} anchorEl
 * @param {{ placement?: 'above', anchorRect?: DOMRectReadOnly }} [options]
 */
function positionPopover(popoverEl, anchorEl, options = {}) {
  const rect = resolveAnchorRect(anchorEl, options.anchorRect);
  if (!rect) return false;

  popoverEl.hidden = false;
  popoverEl.style.visibility = 'hidden';
  popoverEl.style.top = '0';
  popoverEl.style.left = '0';

  fitPopoverToAvailableSpace(popoverEl, rect);

  const popRect = popoverEl.getBoundingClientRect();
  const gap = 8;
  const margin = 12;

  let top = rect.top - popRect.height - gap;
  let left = rect.left + rect.width / 2 - popRect.width / 2;
  left = Math.max(margin, Math.min(left, window.innerWidth - popRect.width - margin));
  top = Math.max(margin, Math.min(top, window.innerHeight - popRect.height - margin));

  const arrowOffset = `${rect.left + rect.width / 2 - left}px`;
  popoverEl.style.top = `${top}px`;
  popoverEl.style.left = `${left}px`;
  popoverEl.dataset.placement = 'top';
  popoverEl.style.setProperty('--arrow-offset', arrowOffset);
  popoverEl.style.visibility = '';
  return true;
}

async function positionPopoverAfterLayout(popoverEl, anchorEl, options = {}) {
  await waitForPopoverLayout();
  return positionPopover(popoverEl, anchorEl, options);
}

function truncateDescription(text, maxLen) {
  const t = (text || '').trim();
  if (t.length <= maxLen) return t;
  return `${t.slice(0, maxLen)}…\n\n（完整说明见 Modrinth 项目页）`;
}

// --- Reference mods ---
function persistReferenceMods() {
  saveReferenceModsState(state.referenceMods);
  saveDraftFromForm();
  flushConceptDraftSave();
}

function updateReferenceModsCollapseUI() {
  const collapsed = state.referenceMods.collapsed;
  els.referenceModsBody.hidden = collapsed;
  els.referenceModsToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  els.referenceModsToggle.textContent = collapsed ? '展开' : '收起';
}

function isScrollInsidePopover(e) {
  const t = e.target;
  if (!(t instanceof Node)) return false;
  return (
    els.refModPopover.contains(t)
    || els.requirementPopover.contains(t)
    || els.planDepthPopover?.contains(t)
  );
}

function closeRefModPopover() {
  state.refModPopoverKey = null;
  state.refModPopoverAnchor = null;
  resetPopoverBodyScroll(els.refModPopover);
  els.refModPopover.hidden = true;
  document.querySelectorAll('.ref-mod-chip-info[aria-expanded="true"]').forEach((btn) => {
    btn.setAttribute('aria-expanded', 'false');
  });
}

function refreshOpenRefModPopover(mod) {
  const key = refModKey(mod);
  if (state.refModPopoverKey !== key) return;
  const entry = findReferenceModEntry(mod) || mod;
  els.refModPopoverBody.replaceChildren(buildRefModPopoverContent(entry));
  void repositionRefModPopover(key);
}

function buildRefModPopoverContent(mod) {
  const frag = document.createDocumentFragment();
  const issues = [];
  const sourceMissing = isRefModSourceMissing(mod);

  const versionIssues = getVersionCompatibilityIssues(
    { loaders: mod.loaders, game_versions: mod.game_versions },
    state.mcVersion,
    state.modLoader,
    getLoaderLabel(state.modLoader)
  );
  issues.push(...versionIssues);

  if (sourceMissing) {
    issues.push({
      type: 'error',
      text: '未提供开源地址，无法作为代码参考',
    });
  }

  if (issues.length > 0) {
    const alerts = document.createElement('div');
    alerts.className = 'req-popover-alerts';
    issues.forEach((issue) => {
      const alert = document.createElement('div');
      alert.className = `req-popover-alert req-popover-alert--${issue.type}`;
      alert.textContent = issue.text;
      alerts.appendChild(alert);
    });
    frag.appendChild(alerts);
  }

  if (mod.reference_type === 'code') {
    const sourceSection = document.createElement('div');
    sourceSection.className = 'req-popover-section';
    const sourceLabel = document.createElement('div');
    sourceLabel.className = 'req-popover-label';
    sourceLabel.textContent = '开源地址';
    sourceSection.appendChild(sourceLabel);

    const sourceUrl = (mod.source_url || '').trim();
    if (sourceUrl) {
      const link = document.createElement('a');
      link.className = 'req-popover-link';
      link.href = sourceUrl;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      link.textContent = sourceUrl;
      sourceSection.appendChild(link);
    } else {
      const missing = document.createElement('p');
      missing.className = 'req-popover-missing';
      missing.textContent = '该项目未在 Modrinth 填写开源仓库地址';
      sourceSection.appendChild(missing);
    }
    frag.appendChild(sourceSection);
  }

  if (sourceMissing) {
    const decompileSection = document.createElement('div');
    decompileSection.className = 'ref-mod-decompile-section';

    const toggleRow = document.createElement('div');
    toggleRow.className = 'ref-mod-decompile-row';

    const toggleLabel = document.createElement('div');
    toggleLabel.className = 'ref-mod-decompile-copy';

    const toggleTitle = document.createElement('span');
    toggleTitle.className = 'ref-mod-decompile-title';
    toggleTitle.textContent = '尝试下载并反编译+反混淆';

    const toggleHint = document.createElement('span');
    toggleHint.className = 'ref-mod-decompile-hint';
    toggleHint.textContent = '高开销 易出错，建议优先使用开源模组';

    toggleLabel.append(toggleTitle, toggleHint);

    const switchLabel = document.createElement('label');
    switchLabel.className = 'ios-switch';

    const toggleInput = document.createElement('input');
    toggleInput.type = 'checkbox';
    toggleInput.checked = Boolean(mod.decompile_attempt);
    toggleInput.addEventListener('change', () => {
      const entry = findReferenceModEntry(mod);
      if (!entry) return;
      entry.decompile_attempt = toggleInput.checked;
      persistReferenceMods();
      renderReferenceModChips();
      refreshOpenRefModPopover(entry);
    });

    const switchSlider = document.createElement('span');
    switchSlider.className = 'ios-switch-slider';
    switchSlider.setAttribute('aria-hidden', 'true');

    switchLabel.append(toggleInput, switchSlider);
    toggleRow.append(toggleLabel, switchLabel);

    const skipHint = document.createElement('p');
    skipHint.className = 'req-popover-skip-hint';
    if (mod.decompile_attempt) {
      skipHint.textContent = '\u00a0';
      skipHint.classList.add('req-popover-skip-hint--placeholder');
      skipHint.setAttribute('aria-hidden', 'true');
    } else {
      skipHint.textContent = '除非开启本设置，否则此模组不会被写入提示词';
    }
    decompileSection.append(toggleRow, skipHint);

    frag.appendChild(decompileSection);
  }

  const noteSection = document.createElement('div');
  noteSection.className = 'req-popover-section';
  const noteLabel = document.createElement('label');
  noteLabel.className = 'req-popover-label';
  noteLabel.textContent = '备注';
  const noteInput = document.createElement('textarea');
  noteInput.className = 'req-popover-note form-textarea';
  noteInput.rows = 3;
  noteInput.placeholder = '可选：补充希望 Agent 如何使用该参考… （如：重点参考其方块物理化功能如何实现）';
  noteInput.value = mod.note || '';
  let noteSaveTimer = null;
  noteInput.addEventListener('input', () => {
    const entry = findReferenceModEntry(mod);
    if (!entry) return;
    clearTimeout(noteSaveTimer);
    noteSaveTimer = setTimeout(() => {
      entry.note = noteInput.value;
      persistReferenceMods();
    }, 300);
  });
  noteSection.append(noteLabel, noteInput);
  frag.appendChild(noteSection);

  const descSection = document.createElement('div');
  descSection.className = 'req-popover-section';
  const descLabel = document.createElement('div');
  descLabel.className = 'req-popover-label';
  descLabel.textContent = '描述';
  descSection.appendChild(descLabel);

  const descText = document.createElement('p');
  descText.className = 'req-popover-text';
  const description = truncateDescription(mod.description, REF_MOD_DESCRIPTION_PREVIEW_LEN);
  descText.textContent = description || '（无描述）';
  descSection.appendChild(descText);
  frag.appendChild(descSection);

  return frag;
}

function findReferenceModEntry(mod) {
  const key = refModKey(mod);
  return state.referenceMods.manual.find((m) => refModKey(m) === key);
}

function getRefModInfoButton(refKey) {
  return document.querySelector(`.ref-mod-chip-info[data-ref-key="${CSS.escape(refKey)}"]`);
}

function resolveRefModAnchorRect(refKey) {
  const btn = getRefModInfoButton(refKey);
  if (btn?.isConnected) {
    const live = btn.getBoundingClientRect();
    if (isValidAnchorRect(live)) {
      if (state.refModPopoverAnchor?.key === refKey) {
        state.refModPopoverAnchor.rect = live;
      }
      return { anchor: btn, rect: live };
    }
  }
  if (
    state.refModPopoverAnchor?.key === refKey &&
    isValidAnchorRect(state.refModPopoverAnchor.rect)
  ) {
    return { anchor: btn, rect: state.refModPopoverAnchor.rect };
  }
  return { anchor: btn, rect: null };
}

async function repositionRefModPopover(refKey) {
  const { anchor, rect } = resolveRefModAnchorRect(refKey);
  if (!rect) return;
  if (anchor?.isConnected) anchor.setAttribute('aria-expanded', 'true');
  await positionPopoverAfterLayout(els.refModPopover, anchor, {
    placement: 'above',
    anchorRect: rect,
  });
}

async function openRefModPopover(mod, anchorEl) {
  const key = refModKey(mod);
  const isOpen = state.refModPopoverKey === key && !els.refModPopover.hidden;
  if (isOpen) {
    closeRefModPopover();
    return;
  }

  if (state.refModPopoverKey && els.refModPopover.hidden) {
    closeRefModPopover();
  }

  closeRequirementPopover();
  closeRefModPopover();
  closePlanDepthPopover();
  state.refModPopoverKey = key;
  state.refModPopoverAnchor = { key, rect: anchorEl.getBoundingClientRect() };
  anchorEl.setAttribute('aria-expanded', 'true');

  const entry = findReferenceModEntry(mod) || mod;
  const typeLabel = isDirectSourceRef(entry)
    ? 'Github'
    : entry.reference_type === 'code'
      ? '参考代码'
      : '参考功能';
  els.refModPopoverTitle.textContent = `${entry.title || entry.slug}（${typeLabel}）`;
  els.refModPopoverBody.replaceChildren(buildRefModPopoverContent(entry));

  await repositionRefModPopover(key);
}

function renderReferenceModChips() {
  els.referenceModChips.innerHTML = '';

  state.referenceMods.manual.forEach((mod) => {
    const versionWarning =
      mod.loaders?.length || mod.game_versions?.length
        ? checkVersionCompatibility(
            { loaders: mod.loaders, game_versions: mod.game_versions },
            state.mcVersion,
            state.modLoader
          )
        : mod.versionWarning;
    mod.versionWarning = versionWarning;

    const sourceMissing = isRefModSourceMissing(mod);

    const chip = document.createElement('div');
    chip.className = 'ref-mod-chip';
    chip.dataset.refKey = refModKey(mod);
    chip.setAttribute('role', 'listitem');
    if (sourceMissing && mod.decompile_attempt) chip.classList.add('ref-mod-chip--decompile');
    else if (sourceMissing) chip.classList.add('ref-mod-chip--error');
    else if (versionWarning) chip.classList.add('ref-mod-chip--warn');

    const isGithubRef = isDirectSourceRef(mod);
    const typeVariant = isGithubRef ? 'github' : mod.reference_type;

    const typeBadge = document.createElement('span');
    typeBadge.className = `ref-mod-chip-type ref-mod-chip-type--${typeVariant}`;
    typeBadge.textContent = isGithubRef
      ? 'Github'
      : mod.reference_type === 'code'
        ? '代码'
        : '功能';

    const label = document.createElement('span');
    label.className = 'ref-mod-chip-label';
    label.textContent = mod.title || mod.slug;
    label.title = sourceMissing && mod.decompile_attempt
      ? `${mod.title || mod.slug}（将尝试反编译参考）`
      : sourceMissing
        ? `${mod.title || mod.slug}（缺少开源地址，不写入提示词）`
        : versionWarning
        ? `${mod.title || mod.slug}（可能与当前版本/加载器不匹配）`
        : mod.title || mod.slug;

    const isPopoverOpen = state.refModPopoverKey === refModKey(mod);
    const infoBtn = document.createElement('button');
    infoBtn.type = 'button';
    infoBtn.className = 'ref-mod-chip-info';
    infoBtn.dataset.refKey = refModKey(mod);
    infoBtn.setAttribute('aria-expanded', isPopoverOpen ? 'true' : 'false');
    infoBtn.setAttribute('aria-label', `查看「${mod.title || mod.slug}」说明`);
    infoBtn.innerHTML = INFO_ICON_SVG;
    infoBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openRefModPopover(mod, infoBtn);
    });

    const removeBtn = document.createElement('button');
    removeBtn.type = 'button';
    removeBtn.className = 'ref-mod-chip-remove';
    removeBtn.setAttribute('aria-label', `移除 ${mod.title || mod.slug}`);
    removeBtn.textContent = '×';
    removeBtn.addEventListener('click', () => {
      if (state.refModPopoverKey === refModKey(mod)) closeRefModPopover();
      state.referenceMods.manual = state.referenceMods.manual.filter(
        (m) => refModKey(m) !== refModKey(mod)
      );
      persistReferenceMods();
      renderReferenceModChips();
    });

    chip.append(typeBadge, label, infoBtn, removeBtn);
    els.referenceModChips.appendChild(chip);
  });
}

function setReferenceModButtonsDisabled(disabled) {
  els.referenceModCodeBtn.disabled = disabled;
  els.referenceModFeatureBtn.disabled = disabled;
}

async function addReferenceModFromInput(referenceType) {
  const raw = els.referenceModInput.value;

  if (referenceType === 'code') {
    const gh = parseGithubRepo(raw);
    if (gh) {
      if (state.referenceMods.manual.length >= MAX_REFERENCE_MODS) return;
      if (hasReferenceEntry(state.referenceMods.manual, gh.project_id, 'code')) return;

      state.referenceMods.manual.push({
        project_id: gh.project_id,
        slug: gh.slug,
        permalink: gh.source_url,
        title: gh.title,
        description: '',
        source_url: gh.source_url,
        reference_type: 'code',
        decompile_attempt: false,
        loaders: [],
        game_versions: [],
        versionWarning: false,
      });

      els.referenceModInput.value = '';
      setUiState({ referenceModsCollapsed: false });
      syncWorkspaceUi();
      persistReferenceMods();
      renderReferenceModChips();
      return;
    }
  }

  const identifier = parseModrinthPermalink(raw);
  if (!identifier) {
    showToast('请输入有效的 Modrinth permanent link（/project/…）', 'error');
    return;
  }
  if (state.referenceMods.manual.length >= MAX_REFERENCE_MODS) {
    showToast(`最多添加 ${MAX_REFERENCE_MODS} 个参考模组`, 'error');
    return;
  }

  setReferenceModButtonsDisabled(true);
  try {
    const project = await resolveModrinthProject(raw);
    const projectId = project.project_id;

    if (hasReferenceEntry(state.referenceMods.manual, projectId, referenceType)) {
      showToast('该参考类型已添加', 'info');
      return;
    }

    const versionWarning = checkVersionCompatibility(
      project,
      state.mcVersion,
      state.modLoader
    );

    state.referenceMods.manual.push({
      project_id: projectId,
      slug: project.slug,
      permalink: project.url,
      title: project.title,
      description: project.description || '',
      source_url: project.source_url || '',
      reference_type: referenceType,
      decompile_attempt: false,
      loaders: project.loaders || [],
      game_versions: project.game_versions || [],
      versionWarning,
    });

    els.referenceModInput.value = '';
    setUiState({ referenceModsCollapsed: false });
    syncWorkspaceUi();
    persistReferenceMods();
    renderReferenceModChips();
    showToast(
      referenceType === 'code' ? '已添加参考代码' : '已添加参考功能',
      'success'
    );
  } catch (err) {
    if (err.status === 404) {
      showToast('未找到该项目，请检查链接', 'error');
    } else if (err.message?.includes('fetch') || err.name === 'TypeError') {
      showToast('无法连接 API 服务，请启动后端（见 README）', 'error');
    } else {
      showToast(err.message || '解析失败，请稍后重试', 'error');
    }
  } finally {
    setReferenceModButtonsDisabled(false);
  }
}

function initRefModPopover() {
  document.addEventListener('click', (e) => {
    if (
      els.refModPopover.hidden ||
      e.target.closest('.ref-mod-chip-info') ||
      e.target.closest('#refModPopover')
    ) {
      return;
    }
    closeRefModPopover();
  });
  window.addEventListener('resize', closeRefModPopover);
  window.addEventListener(
    'scroll',
    (e) => {
      if (isScrollInsidePopover(e)) return;
      closeRefModPopover();
    },
    true
  );
}

function initReferenceMods() {
  els.referenceModAutoSearch.checked = state.referenceMods.autoSearch;
  updateReferenceModsCollapseUI();
  renderReferenceModChips();
  initRefModPopover();

  els.referenceModsToggle.addEventListener('click', () => {
    setUiState({ referenceModsCollapsed: !state.referenceMods.collapsed });
    syncWorkspaceUi();
  });

  els.referenceModCodeBtn.addEventListener('click', () => addReferenceModFromInput('code'));
  els.referenceModFeatureBtn.addEventListener('click', () => addReferenceModFromInput('feature'));

  els.referenceModInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addReferenceModFromInput('feature');
    }
  });

  els.referenceModAutoSearch.addEventListener('change', () => {
    state.referenceMods.autoSearch = els.referenceModAutoSearch.checked;
    if (state.referenceMods.autoSearch) {
      setUiState({ referenceModsCollapsed: false });
      syncWorkspaceUi();
    }
    persistReferenceMods();
  });
}

// --- Requirements ---
function toggleRequirementSelection(id) {
  if (state.requirements.has(id)) {
    state.requirements.delete(id);
  } else {
    state.requirements.add(id);
  }
  saveDraftFromForm();
}

function closeRequirementPopover() {
  state.popoverReqId = null;
  resetPopoverBodyScroll(els.requirementPopover);
  els.requirementPopover.hidden = true;
  document.querySelectorAll('.requirement-info[aria-expanded="true"]').forEach((btn) => {
    btn.setAttribute('aria-expanded', 'false');
  });
}

function updateRequirementPopoverCost(req) {
  const badge = getRequirementCostBadge(req);
  if (!els.requirementPopoverCost) return;
  if (!badge) {
    els.requirementPopoverCost.hidden = true;
    els.requirementPopoverCost.textContent = '';
    els.requirementPopoverCost.className = 'req-popover-cost';
    return;
  }
  els.requirementPopoverCost.textContent = formatRequirementCostText(badge);
  els.requirementPopoverCost.className = `req-popover-cost req-popover-cost--${badge.value}`;
  els.requirementPopoverCost.hidden = false;
}

async function openRequirementPopover(req, anchorEl) {
  const isOpen = state.popoverReqId === req.id && !els.requirementPopover.hidden;
  if (isOpen) {
    closeRequirementPopover();
    return;
  }

  if (state.popoverReqId && els.requirementPopover.hidden) {
    closeRequirementPopover();
  }

  closeRequirementPopover();
  state.popoverReqId = req.id;
  anchorEl.setAttribute('aria-expanded', 'true');

  closeRefModPopover();
  closePlanDepthPopover();
  els.requirementPopoverTitle.textContent = req.title;
  updateRequirementPopoverCost(req);
  els.requirementPopoverBody.textContent = req.description || '（无详细说明）';
  await positionPopoverAfterLayout(els.requirementPopover, anchorEl, { placement: 'above' });
}

function renderRequirements() {
  closeRequirementPopover();
  if (!els.requirementsGrid) return;
  els.requirementsGrid.innerHTML = '';
  const items = getActiveRequirements();

  items.forEach((req) => {
    const isChecked = state.requirements.has(req.id);
    const isPopoverOpen = state.popoverReqId === req.id;

    const item = document.createElement('div');
    item.className = 'requirement-item';
    item.setAttribute('role', 'group');
    item.setAttribute('aria-checked', isChecked ? 'true' : 'false');
    item.dataset.id = req.id;

    const row = document.createElement('div');
    row.className = 'requirement-item-row';

    const toggleBtn = document.createElement('button');
    toggleBtn.type = 'button';
    toggleBtn.className = 'requirement-toggle';
    toggleBtn.setAttribute('aria-label', isChecked ? '取消选中' : '选中');
    toggleBtn.innerHTML =
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>';
    toggleBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleRequirementSelection(req.id);
      item.setAttribute('aria-checked', state.requirements.has(req.id) ? 'true' : 'false');
      toggleBtn.setAttribute('aria-label', state.requirements.has(req.id) ? '取消选中' : '选中');
    });

    const mainBtn = document.createElement('button');
    mainBtn.type = 'button';
    mainBtn.className = 'requirement-main';
    const titleSpan = document.createElement('span');
    titleSpan.className = 'requirement-title-text';
    titleSpan.textContent = displayTitle(req.title);
    titleSpan.title = req.title;
    mainBtn.appendChild(titleSpan);

    mainBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleRequirementSelection(req.id);
      item.setAttribute('aria-checked', state.requirements.has(req.id) ? 'true' : 'false');
      toggleBtn.setAttribute('aria-label', state.requirements.has(req.id) ? '取消选中' : '选中');
    });

    const infoBtn = document.createElement('button');
    infoBtn.type = 'button';
    infoBtn.className = 'requirement-info';
    infoBtn.setAttribute('aria-expanded', isPopoverOpen ? 'true' : 'false');
    infoBtn.setAttribute('aria-label', `查看「${req.title}」说明`);
    infoBtn.innerHTML = INFO_ICON_SVG;
    infoBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openRequirementPopover(req, infoBtn);
    });

    row.append(toggleBtn, mainBtn, infoBtn);
    item.appendChild(row);
    els.requirementsGrid.appendChild(item);
  });
}

function initRequirementPopover() {
  document.addEventListener('click', (e) => {
    if (
      els.requirementPopover.hidden ||
      e.target.closest('.requirement-info') ||
      e.target.closest('#requirementPopover')
    ) {
      return;
    }
    closeRequirementPopover();
  });

  window.addEventListener('resize', closeRequirementPopover);
  window.addEventListener(
    'scroll',
    (e) => {
      if (isScrollInsidePopover(e)) return;
      closeRequirementPopover();
    },
    true
  );
}

// --- Requirements modal ---
function updateTitleCounter() {
  const count = countChineseChars(els.customReqTitle.value);
  els.customReqTitleCounter.textContent = `${count}/${REQUIREMENT_TITLE_MAX_CHARS} 汉字`;
  els.customReqTitleCounter.classList.toggle('is-invalid', count > REQUIREMENT_TITLE_MAX_CHARS);
}

function resetCustomForm() {
  state.editingCustomId = null;
  els.customFormEditId.value = '';
  els.customReqTitle.value = '';
  els.customReqDescription.value = '';
  els.customReqPrompt.value = '';
  els.customFormTitle.textContent = '添加要求';
  els.customFormSaveBtn.textContent = '添加';
  els.customFormCancelBtn.hidden = true;
  updateTitleCounter();
}

function fillCustomForm(item) {
  state.editingCustomId = item.id;
  els.customFormEditId.value = item.id;
  els.customReqTitle.value = item.title;
  els.customReqDescription.value = item.description || '';
  els.customReqPrompt.value = item.detail?.custom_prompt || '';
  els.customFormTitle.textContent = '编辑要求';
  els.customFormSaveBtn.textContent = '保存';
  els.customFormCancelBtn.hidden = false;
  updateTitleCounter();
  scrollRequirementsModalToBottom();
}

function buildModalReqListItem(req, { onEdit, onDelete }) {
  const li = document.createElement('li');
  li.className = 'modal-req-item';
  li.dataset.id = req.id;

  const layout = document.createElement('div');
  layout.className = 'modal-req-item-layout';
  layout.appendChild(createDragHandle());

  const content = document.createElement('div');
  content.className = 'modal-req-item-content';

  const title = document.createElement('div');
  title.className = 'modal-req-item-title';
  title.textContent = req.title;
  const desc = document.createElement('div');
  desc.className = 'modal-req-item-desc';
  desc.textContent = req.description || '（无说明）';
  const actions = document.createElement('div');
  actions.className = 'modal-req-item-actions';
  const editBtn = document.createElement('button');
  editBtn.type = 'button';
  editBtn.textContent = '编辑';
  editBtn.addEventListener('click', () => onEdit(req));
  const delBtn = document.createElement('button');
  delBtn.type = 'button';
  delBtn.className = 'btn-delete';
  delBtn.textContent = '删除';
  delBtn.addEventListener('click', () => onDelete(req));
  actions.append(editBtn, delBtn);
  content.append(title, desc, actions);
  layout.appendChild(content);
  li.appendChild(layout);
  return li;
}

function renderModalLists() {
  const items = loadRequirements();
  if (!els.reqList) return;
  els.reqList.innerHTML = '';
  if (els.reqListEmpty) els.reqListEmpty.hidden = items.length > 0;
  const sortHint = document.getElementById('reqListSortHint');
  if (sortHint) sortHint.hidden = items.length < 2;

  items.forEach((req) => {
    els.reqList.appendChild(
      buildModalReqListItem(req, {
        onEdit: fillCustomForm,
        onDelete: (item) => {
          deleteRequirement(item.id);
          state.requirements.delete(item.id);
          if (state.popoverReqId === item.id) closeRequirementPopover();
          if (state.editingCustomId === item.id) resetCustomForm();
          renderModalLists();
          renderRequirements();
          showToast('已删除要求项', 'info');
          syncPreferencesToServer();
        },
      }),
    );
  });
}

function initRequirementsListDragSort() {
  if (!els.reqList) return;
  attachDragSort(els.reqList, {
    getItemId: (el) => el.dataset.id || '',
    onReorder: (ids) => {
      reorderRequirements(ids);
      renderRequirements();
      syncPreferencesToServer();
    },
  });
}

function scrollRequirementsModalToBottom() {
  const body = els.requirementsModal?.querySelector('.modal-body');
  if (!body) return;
  const scroll = () => {
    body.scrollTop = body.scrollHeight;
  };
  scroll();
  requestAnimationFrame(() => {
    scroll();
    requestAnimationFrame(scroll);
  });
}

function openRequirementsModal() {
  resetCustomForm();
  renderModalLists();
  els.requirementsModalOverlay.hidden = false;
  document.body.style.overflow = 'hidden';
  scrollRequirementsModalToBottom();
  els.customReqTitle?.focus({ preventScroll: true });
}

function closeRequirementsModal() {
  els.requirementsModalOverlay.hidden = true;
  document.body.style.overflow = '';
  const body = els.requirementsModal?.querySelector('.modal-body');
  if (body) body.scrollTop = 0;
  resetCustomForm();
  renderRequirements();
  syncPreferencesToServer();
}

function saveCustomForm() {
  const title = els.customReqTitle.value;
  const description = els.customReqDescription.value;
  const customPrompt = els.customReqPrompt.value;
  const validation = validateTitle(title);
  if (!validation.valid) {
    showToast(validation.message, 'error');
    return;
  }

  try {
    if (state.editingCustomId) {
      updateRequirement(state.editingCustomId, { title, description, customPrompt });
      showToast('已更新要求项', 'success');
    } else {
      addRequirement({ title, description, customPrompt });
      showToast('已添加要求项', 'success');
    }
    resetCustomForm();
    renderModalLists();
    renderRequirements();
    syncPreferencesToServer();
  } catch (err) {
    showToast(err.message || '保存失败', 'error');
  }
}

function initRequirements() {
  els.requirementsConfigBtn?.addEventListener('click', openRequirementsModal);
  els.requirementsModalClose?.addEventListener('click', closeRequirementsModal);
  els.requirementsModalDone?.addEventListener('click', closeRequirementsModal);
  els.requirementsModalOverlay?.addEventListener('click', (e) => {
    if (e.target === els.requirementsModalOverlay) closeRequirementsModal();
  });

  els.resetCustomReqBtn?.addEventListener('click', () => {
    if (!confirm('确定恢复为默认要求列表？当前所有修改将被覆盖。')) return;
    const defaults = resetRequirementsToDefaults();
    const defaultIds = new Set(defaults.map((r) => r.id));
    [...state.requirements].forEach((id) => {
      if (!defaultIds.has(id)) state.requirements.delete(id);
    });
    if (state.popoverReqId && !defaultIds.has(state.popoverReqId)) {
      closeRequirementPopover();
    }
    resetCustomForm();
    renderModalLists();
    renderRequirements();
    showToast('已恢复默认要求列表', 'info');
    syncPreferencesToServer();
  });

  els.customReqTitle?.addEventListener('input', updateTitleCounter);
  els.customFormSaveBtn?.addEventListener('click', saveCustomForm);
  els.customFormCancelBtn?.addEventListener('click', resetCustomForm);

  initRequirementPopover();
  initRequirementsListDragSort();
  renderRequirements();
}

// --- Form validation ---
function getSavedInterruptionLevel() {
  if (isGuest()) return DEFAULT_INTERRUPTION_LEVEL;
  const stored = localStorage.getItem(INTERRUPTION_LEVEL_KEY);
  if (stored === null) return DEFAULT_INTERRUPTION_LEVEL;
  const n = Number(stored);
  return INTERRUPTION_LEVELS.some((l) => l.value === n) ? n : DEFAULT_INTERRUPTION_LEVEL;
}

function saveInterruptionLevel(level) {
  if (isGuest()) return;
  localStorage.setItem(INTERRUPTION_LEVEL_KEY, String(level));
}

function getSavedPlanReadDepth() {
  if (isGuest()) return DEFAULT_PLAN_READ_DEPTH;
  const stored = localStorage.getItem(PLAN_READ_DEPTH_KEY);
  if (!stored) return DEFAULT_PLAN_READ_DEPTH;
  return PLAN_READ_DEPTHS.some((d) => d.value === stored) ? stored : DEFAULT_PLAN_READ_DEPTH;
}

function savePlanReadDepth(depth) {
  if (isGuest()) return;
  localStorage.setItem(PLAN_READ_DEPTH_KEY, depth);
}

function getSelectedPlanReadDepth() {
  const selected = els.planDepthOptions?.querySelector('.interrupt-picker-option.is-selected');
  const value = selected?.dataset.depth;
  return PLAN_READ_DEPTHS.some((d) => d.value === value) ? value : getSavedPlanReadDepth();
}

function syncPlanDepthSelection(depth) {
  const resolved = PLAN_READ_DEPTHS.some((d) => d.value === depth) ? depth : DEFAULT_PLAN_READ_DEPTH;
  els.planDepthOptions?.querySelectorAll('.interrupt-picker-option').forEach((btn) => {
    btn.classList.toggle('is-selected', btn.dataset.depth === resolved);
  });
}

function clampMaxTurns(value) {
  const n = Number(value);
  if (Number.isNaN(n)) return DEFAULT_MAX_TURNS;
  return Math.max(MAX_TURNS_MIN, Math.min(MAX_TURNS_MAX, Math.round(n)));
}

function getSavedMaxTurns() {
  if (isGuest()) return DEFAULT_MAX_TURNS;
  const stored = localStorage.getItem(MAX_TURNS_KEY);
  if (stored === null) return DEFAULT_MAX_TURNS;
  return clampMaxTurns(stored);
}

function saveMaxTurns(value) {
  if (isGuest()) return;
  localStorage.setItem(MAX_TURNS_KEY, String(clampMaxTurns(value)));
}

function getMaxTurnsFromForm() {
  return clampMaxTurns(els.advancedMaxTurns?.value ?? getSavedMaxTurns());
}

function setMaxTurnsFieldError(message) {
  const input = els.advancedMaxTurns;
  const errorEl = els.advancedMaxTurnsError;
  if (!input || !errorEl) return;
  const invalid = Boolean(message);
  input.classList.toggle('is-invalid', invalid);
  input.setAttribute('aria-invalid', String(invalid));
  if (invalid) {
    errorEl.textContent = message;
    errorEl.hidden = false;
  } else {
    errorEl.textContent = '';
    errorEl.hidden = true;
  }
}

function validateMaxTurnsForm({ showErrors = true } = {}) {
  const raw = els.advancedMaxTurns?.value;
  if (raw === '' || raw == null) {
    if (showErrors) setMaxTurnsFieldError('');
    return { valid: true, value: DEFAULT_MAX_TURNS };
  }
  const n = Number(raw);
  if (!Number.isInteger(n) || n < MAX_TURNS_MIN || n > MAX_TURNS_MAX) {
    const message = `请输入 ${MAX_TURNS_MIN}–${MAX_TURNS_MAX} 之间的整数`;
    if (showErrors) setMaxTurnsFieldError(message);
    return { valid: false, message };
  }
  if (showErrors) setMaxTurnsFieldError('');
  return { valid: true, value: n };
}

function getEffectivePromptText() {
  const raw = els.promptInput.value.trim();
  if (
    state.descOptimize.visible &&
    !state.descOptimize.bodyCollapsed &&
    state.descOptimize.text.trim()
  ) {
    return state.descOptimize.text.trim();
  }
  return raw;
}

function getEffectivePromptLength() {
  return getEffectivePromptText().length;
}

function isPromptLengthValid() {
  return getEffectivePromptLength() >= VALIDATION.minPromptLength;
}

const START_PLAN_BTN_LABEL = '开始规划';

const PLAN_READ_DEPTHS = [
  { value: 'fast', label: '快速', hint: '仅索引参考模组' },
  { value: 'standard', label: '标准', hint: '首轮阅读参考源码', isDefault: true },
  { value: 'deep', label: '深度', hint: '每轮阅读参考源码' },
];
const DEFAULT_PLAN_READ_DEPTH = 'standard';
const PLAN_READ_DEPTH_KEY = 'moduscript_plan_read_depth';

/** @type {Promise<object | null> | null} */
let knowledgeL1LoadPromise = null;

async function ensureKnowledgeL1Loaded() {
  if (state.cachedKnowledgeL1?.programming != null) {
    return state.cachedKnowledgeL1;
  }
  if (!knowledgeL1LoadPromise) {
    knowledgeL1LoadPromise = fetchKnowledgeL1()
      .then((l1) => {
        state.cachedKnowledgeL1 = l1;
        return l1;
      })
      .catch(() => {
        state.cachedKnowledgeL1 = null;
        return null;
      })
      .finally(() => {
        knowledgeL1LoadPromise = null;
      });
  }
  return knowledgeL1LoadPromise;
}

function setStartPlanBtnBusy(busy) {
  if (!els.startPlanBtn) return;
  els.startPlanBtn.setAttribute('aria-busy', busy ? 'true' : 'false');
  els.startPlanBtn.textContent = busy ? '进入中…' : START_PLAN_BTN_LABEL;
}

function closePlanDepthPopover() {
  if (!els.planDepthPopover) return;
  resetPopoverBodyScroll(els.planDepthPopover);
  els.planDepthPopover.hidden = true;
}

async function openPlanDepthPopover() {
  if (!els.planDepthPopover || !els.startPlanBtn) return;
  closeRequirementPopover();
  closeRefModPopover();
  syncPlanDepthSelection(getSavedPlanReadDepth());
  await positionPopoverAfterLayout(els.planDepthPopover, els.startPlanBtn, { placement: 'above' });
}

function syncPromptActionsPlacement() {
  const usePanel =
    state.descOptimize.visible && !state.descOptimize.bodyCollapsed;
  const target = usePanel ? els.descOptimizeActionsSlot : els.promptActionsSlot;
  if (els.promptActions.parentElement !== target) {
    target.appendChild(els.promptActions);
  }
  els.textareaWrap?.classList.toggle('textarea-wrap--optimize-visible', state.descOptimize.visible);
  els.textareaWrap?.classList.toggle('textarea-wrap--optimize-expanded', usePanel);
}

function updateOptimizeDescBtnLabel() {
  if (!els.optimizeDescBtn) return;
  const label = els.optimizeDescBtn.querySelector('.optimize-desc-btn-label');
  if (!label) return;
  label.textContent = state.descOptimize.visible ? '重新优化' : '优化描述';
}

function setOptimizeLoading(loading) {
  state.descOptimize.loading = loading;
  els.optimizeDescBtn?.classList.toggle('is-loading', loading);
  if (els.optimizeDescLoading) els.optimizeDescLoading.hidden = !loading;
  if (els.optimizeDescBtn) els.optimizeDescBtn.disabled = loading;
  updatePromptActions();
}

function resetDescOptimizeUi() {
  restoreDescOptimizeFromDraft({ descOptimize: { text: '', visible: false, collapsed: false } });
}

function snapshotDescOptimizeToDraft() {
  updateActiveDraft({
    descOptimize: {
      text: state.descOptimize.text,
      visible: state.descOptimize.visible,
      collapsed: state.descOptimize.bodyCollapsed,
    },
  });
}

function restoreDescOptimizeFromDraft(draft) {
  const opt = draft?.descOptimize || { text: '', visible: false, collapsed: false };
  state.descOptimize.visible = Boolean(opt.visible && (opt.text || '').trim());
  state.descOptimize.bodyCollapsed = Boolean(opt.collapsed);
  state.descOptimize.loading = false;
  state.descOptimize.text = (opt.text || '').trim() ? opt.text : '';
  state.descOptimize.adoptConfirming = false;
  syncDescOptimizeUi();
}

function updateAdoptDescVisibility() {
  const collapsed = state.descOptimize.bodyCollapsed;
  if (collapsed && state.descOptimize.adoptConfirming) {
    state.descOptimize.adoptConfirming = false;
  }
  if (els.adoptDescBtn) {
    els.adoptDescBtn.hidden = collapsed || state.descOptimize.adoptConfirming;
  }
  if (els.adoptDescConfirm) {
    els.adoptDescConfirm.hidden = collapsed || !state.descOptimize.adoptConfirming;
  }
}

function resetAdoptConfirm() {
  state.descOptimize.adoptConfirming = false;
  updateAdoptDescVisibility();
}

function showDescOptimizePanel(text) {
  state.descOptimize.text = text;
  state.descOptimize.visible = true;
  state.descOptimize.bodyCollapsed = false;
  syncDescOptimizeUi();
  resetAdoptConfirm();
  snapshotDescOptimizeToDraft();
}

function toggleDescOptimizeCollapse() {
  if (!state.descOptimize.visible) return;
  state.descOptimize.bodyCollapsed = !state.descOptimize.bodyCollapsed;
  syncDescOptimizeUi();
  snapshotDescOptimizeToDraft();
}

function syncDescOptimizeUi() {
  if (els.descOptimizePanel) {
    els.descOptimizePanel.hidden = !state.descOptimize.visible;
    els.descOptimizePanel.classList.toggle('is-collapsed', state.descOptimize.bodyCollapsed);
  }
  if (els.descOptimizeBody) {
    els.descOptimizeBody.hidden = !state.descOptimize.visible || state.descOptimize.bodyCollapsed;
  }
  if (els.descOptimizeCollapseBtn) {
    const expanded = state.descOptimize.visible && !state.descOptimize.bodyCollapsed;
    els.descOptimizeCollapseBtn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
  }
  if (state.descOptimize.visible && state.descOptimize.text && els.descOptimizeContent) {
    els.descOptimizeContent.innerHTML = renderMarkdown(state.descOptimize.text);
  } else if (els.descOptimizeContent && !state.descOptimize.visible) {
    els.descOptimizeContent.innerHTML = '';
  }
  updateOptimizeDescBtnLabel();
  updateAdoptDescVisibility();
  syncPromptActionsPlacement();
  updatePromptActions();
}

async function fetchOptimizedDescription(rawText) {
  if (DEBUG_MOCK) {
    await new Promise((r) => setTimeout(r, 1200));
    return MOCK_OPTIMIZED_DESCRIPTION;
  }

  const headers = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API.baseUrl}${API.optimizeDescription}`, {
    method: 'POST',
    headers,
    body: JSON.stringify({ prompt: rawText }),
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data?.detail || data?.message || data?.error || `优化失败 (${res.status})`);
  }

  const text = data?.optimized_prompt || data?.text || data?.content;
  if (!text || typeof text !== 'string') {
    throw new Error('优化结果格式无效');
  }
  return text;
}

async function runOptimizeDescription() {
  if (!state.descOptimizeEnabled) {
    showToast('管理员未启用描述优化', 'error');
    return;
  }
  const raw = els.promptInput.value.trim();
  if (raw.length < VALIDATION.minPromptLength) {
    showToast(`请至少输入 ${VALIDATION.minPromptLength} 个字符后再优化`, 'error');
    return;
  }
  if (state.descOptimize.loading || state.isSubmitting) return;

  setOptimizeLoading(true);
  try {
    const text = await fetchOptimizedDescription(raw);
    showDescOptimizePanel(text);
    showToast('描述已优化', 'success');
  } catch (err) {
    showToast(err.message || '优化失败，请稍后重试', 'error');
  } finally {
    setOptimizeLoading(false);
  }
}

function confirmAdoptDescription() {
  if (!state.descOptimize.text.trim()) return;
  els.promptInput.value = state.descOptimize.text;
  resetAdoptConfirm();
  updatePromptActions();
  saveDraftFromForm();
  showToast('已采纳到输入框', 'success');
}

const MOD_META_FIELD_CONFIG = {
  modName: {
    input: () => els.advancedModName,
    error: () => els.advancedModNameError,
    validate: validateModName,
  },
  modId: {
    input: () => els.advancedModId,
    error: () => els.advancedModIdError,
    validate: validateModId,
  },
  packageName: {
    input: () => els.advancedPackageName,
    error: () => els.advancedPackageNameError,
    validate: validatePackageName,
  },
};

function getModMetaFromForm() {
  return {
    modName: els.advancedModName?.value.trim() || '',
    modId: els.advancedModId?.value.trim() || '',
    packageName: els.advancedPackageName?.value.trim() || '',
  };
}

function setModMetaFieldError(fieldKey, message) {
  const cfg = MOD_META_FIELD_CONFIG[fieldKey];
  const input = cfg?.input();
  const errorEl = cfg?.error();
  if (!input || !errorEl) return;
  const invalid = Boolean(message);
  input.classList.toggle('is-invalid', invalid);
  input.setAttribute('aria-invalid', String(invalid));
  if (invalid) {
    errorEl.textContent = message;
    errorEl.hidden = false;
  } else {
    errorEl.textContent = '';
    errorEl.hidden = true;
  }
}

function clearModMetaFieldErrors() {
  Object.keys(MOD_META_FIELD_CONFIG).forEach((key) => setModMetaFieldError(key, ''));
}

function validateModMetaForm({ showErrors = true } = {}) {
  const meta = getModMetaFromForm();
  const checks = [
    ['modName', validateModName(meta.modName)],
    ['modId', validateModId(meta.modId)],
    ['packageName', validatePackageName(meta.packageName)],
  ];
  let firstInvalid = null;
  for (const [key, check] of checks) {
    if (!check.valid) {
      if (showErrors) setModMetaFieldError(key, check.message);
      if (!firstInvalid) firstInvalid = check;
    } else if (showErrors) {
      setModMetaFieldError(key, '');
    }
  }
  return firstInvalid ? { valid: false, message: firstInvalid.message } : { valid: true };
}

function applyModMetaToForm({ modName = '', modId = '', packageName = '' } = {}) {
  if (els.advancedModName) els.advancedModName.value = modName;
  if (els.advancedModId) els.advancedModId.value = modId;
  if (els.advancedPackageName) els.advancedPackageName.value = packageName;
  clearModMetaFieldErrors();
  saveDraftFromForm();
}

function maybeDeriveModIdAndPackage() {
  const meta = getModMetaFromForm();
  if (meta.modName && !meta.modId && els.advancedModId) {
    els.advancedModId.value = deriveModId(meta.modName);
  }
  const modId = els.advancedModId?.value.trim() || '';
  if (modId && !els.advancedPackageName?.value.trim()) {
    els.advancedPackageName.value = derivePackageName(modId);
  }
}

function saveDraftFromForm() {
  if (isGuest()) return;
  updateActiveDraft({
    prompt: els.promptInput?.value || '',
    mcVersion: state.mcVersion,
    modLoader: state.modLoader,
    platform: state.platform,
    requirements: [...state.requirements],
    referenceMods: {
      manual: state.referenceMods.manual,
      autoSearch: state.referenceMods.autoSearch,
    },
    modMeta: {
      modName: els.advancedModName?.value.trim() || '',
      modId: els.advancedModId?.value.trim() || '',
      packageName: els.advancedPackageName?.value.trim() || '',
    },
    interruptionLevel: getSelectedInterruptionLevel(),
    maxTurns: getMaxTurnsFromForm(),
    planReadDepth: getSelectedPlanReadDepth(),
  });
  snapshotDescOptimizeToDraft();
}

function applyDraftToForm(draft) {
  closePlanDepthPopover();
  if (!draft) return;
  if (els.promptInput) els.promptInput.value = draft.prompt || '';
  state.mcVersion = draft.mcVersion || DEFAULTS.mcVersion;
  state.modLoader = draft.modLoader || DEFAULTS.modLoader;
  state.platform = draft.platform || DEFAULTS.platform;
  state.requirements = new Set(draft.requirements || []);
  state.referenceMods = {
    manual: draft.referenceMods?.manual || [],
    autoSearch: Boolean(draft.referenceMods?.autoSearch),
    collapsed: getUiState().referenceModsCollapsed,
  };
  saveReferenceModsState(state.referenceMods);
  if (els.advancedModName) els.advancedModName.value = draft.modMeta?.modName || '';
  if (els.advancedModId) els.advancedModId.value = draft.modMeta?.modId || '';
  if (els.advancedPackageName) els.advancedPackageName.value = draft.modMeta?.packageName || '';
  if (els.advancedMaxTurns) els.advancedMaxTurns.value = String(draft.maxTurns ?? getSavedMaxTurns());
  if (draft.interruptionLevel != null) saveInterruptionLevel(draft.interruptionLevel);
  if (draft.maxTurns != null) saveMaxTurns(draft.maxTurns);
  restoreDescOptimizeFromDraft(draft);
  renderMcVersionMenu();
  renderModLoaderMenu();
  renderPlatformMenu();
  els.mcVersionValue.textContent = state.mcVersion;
  const loader = (VERSION_LOADERS[state.mcVersion] || []).find((l) => l.value === state.modLoader);
  if (loader) updateModLoaderValueDisplay(loader.label, loader.value);
  els.platformValue.textContent = getPlatformLabel(state.platform);
  renderRequirements();
  renderReferenceModChips();
  if (els.referenceModAutoSearch) els.referenceModAutoSearch.checked = state.referenceMods.autoSearch;
  syncInterruptSelection(draft.interruptionLevel ?? getSavedInterruptionLevel());
  if (draft.planReadDepth != null) savePlanReadDepth(draft.planReadDepth);
  syncPlanDepthSelection(draft.planReadDepth ?? getSavedPlanReadDepth());
  syncWorkspaceUi();
  updatePromptActions();
  touchDraftAccess(draft.id);
}

function applyGuestDefaultsToForm() {
  if (els.promptInput) els.promptInput.value = '';
  state.mcVersion = DEFAULTS.mcVersion;
  state.modLoader = DEFAULTS.modLoader;
  state.platform = DEFAULTS.platform;
  state.requirements = new Set();
  state.referenceMods = { manual: [], autoSearch: false, collapsed: true };
  if (els.advancedModName) els.advancedModName.value = '';
  if (els.advancedModId) els.advancedModId.value = '';
  if (els.advancedPackageName) els.advancedPackageName.value = '';
  if (els.advancedMaxTurns) els.advancedMaxTurns.value = String(DEFAULT_MAX_TURNS);
  restoreDescOptimizeFromDraft(null);
  renderMcVersionMenu();
  renderModLoaderMenu();
  renderPlatformMenu();
  els.mcVersionValue.textContent = state.mcVersion;
  const loader = (VERSION_LOADERS[state.mcVersion] || []).find((l) => l.value === state.modLoader);
  if (loader) updateModLoaderValueDisplay(loader.label, loader.value);
  els.platformValue.textContent = getPlatformLabel(state.platform);
  renderRequirements();
  renderReferenceModChips();
  if (els.referenceModAutoSearch) els.referenceModAutoSearch.checked = false;
  syncInterruptSelection(DEFAULT_INTERRUPTION_LEVEL);
  if (els.advancedSettingsBody) els.advancedSettingsBody.hidden = true;
  if (els.advancedSettingsToggle) {
    els.advancedSettingsToggle.setAttribute('aria-expanded', 'false');
    els.advancedSettingsToggle.textContent = '展开';
  }
  updateReferenceModsCollapseUI();
  updatePromptActions();
}

function syncWorkspaceUi() {
  const ui = getUiState();
  syncAdvancedSettingsUi();
  state.referenceMods.collapsed = Boolean(ui.referenceModsCollapsed);
  updateReferenceModsCollapseUI();
}

function syncInterruptSelection(level) {
  els.advancedInterruptOptions?.querySelectorAll('.interrupt-picker-option').forEach((btn) => {
    btn.classList.toggle('is-selected', Number(btn.dataset.level) === level);
  });
}

function syncAdvancedSettingsUi() {
  const ui = getUiState();
  const open = Boolean(ui.advancedOpen);
  if (els.advancedSettingsBody) els.advancedSettingsBody.hidden = !open;
  els.advancedSettingsToggle?.setAttribute('aria-expanded', String(open));
  if (els.advancedSettingsToggle) {
    els.advancedSettingsToggle.textContent = open ? '收起' : '展开';
  }
}

function getSelectedInterruptionLevel() {
  const selected = els.advancedInterruptOptions?.querySelector('.interrupt-picker-option.is-selected');
  if (selected) return Number(selected.dataset.level);
  return getSavedInterruptionLevel();
}

async function submitBuild() {
  if (state.isSubmitting) return;
  if (!isPromptLengthValid()) {
    showToast(`请至少输入 ${VALIDATION.minPromptLength} 个字符描述您的 ${getConceptLabelShort()}`, 'error');
    return;
  }
  if (
    els.advancedInterruptOptions
    && !els.advancedInterruptOptions.querySelector('.interrupt-picker-option.is-selected')
  ) {
    showToast('请在高级设置中选择打扰强度', 'error');
    if (els.advancedSettingsBody?.hidden) {
      els.advancedSettingsBody.hidden = false;
      setUiState({ advancedOpen: true });
      syncAdvancedSettingsUi();
    }
    return;
  }
  const metaCheck = validateModMetaForm({ showErrors: true });
  if (!metaCheck.valid) {
    showToast(metaCheck.message, 'error');
    if (els.advancedSettingsBody?.hidden) {
      els.advancedSettingsBody.hidden = false;
      setUiState({ advancedOpen: true });
      syncAdvancedSettingsUi();
    }
    return;
  }
  const maxTurnsCheck = validateMaxTurnsForm({ showErrors: true });
  if (!maxTurnsCheck.valid) {
    showToast(maxTurnsCheck.message, 'error');
    if (els.advancedSettingsBody?.hidden) {
      els.advancedSettingsBody.hidden = false;
      setUiState({ advancedOpen: true });
      syncAdvancedSettingsUi();
    }
    els.advancedMaxTurns?.focus();
    return;
  }
  const modMeta = getModMetaFromForm();
  const interruptionLevel = getSelectedInterruptionLevel();
  saveInterruptionLevel(interruptionLevel);
  saveMaxTurns(maxTurnsCheck.value);
  saveDraftFromForm();
  await handleSubmit('build', interruptionLevel, modMeta, maxTurnsCheck.value);
}

function initPlanDepthPicker() {
  if (!els.planDepthOptions) return;
  els.planDepthOptions.innerHTML = '';
  const saved = getSavedPlanReadDepth();
  PLAN_READ_DEPTHS.forEach((depth) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'interrupt-picker-option';
    btn.dataset.depth = depth.value;
    if (depth.value === saved) btn.classList.add('is-selected');
    const hintHtml = depth.hint
      ? `<span class="interrupt-picker-option-hint">${depth.hint}</span>`
      : '';
    btn.innerHTML = `
      <span class="interrupt-picker-option-label">${depth.label}</span>
      ${hintHtml}
    `;
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      await confirmPlanWithDepth(btn.dataset.depth);
    });
    els.planDepthOptions.appendChild(btn);
  });

  document.addEventListener('click', (e) => {
    if (
      els.planDepthPopover?.hidden ||
      els.startPlanBtn?.contains(e.target) ||
      els.planDepthPopover?.contains(e.target)
    ) {
      return;
    }
    closePlanDepthPopover();
  });

  window.addEventListener('resize', closePlanDepthPopover);
  window.addEventListener(
    'scroll',
    (e) => {
      if (isScrollInsidePopover(e)) return;
      closePlanDepthPopover();
    },
    true,
  );
}

function initAdvancedSettings() {
  if (!els.advancedInterruptOptions) return;
  els.advancedInterruptOptions.innerHTML = '';
  const saved = getSavedInterruptionLevel();
  els.advancedInterruptOptions.classList.add('interrupt-picker-grid--inline');
  INTERRUPTION_LEVELS.forEach((level, index) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'interrupt-picker-option';
    btn.dataset.level = String(level.value);
    if (level.value === DEFAULT_INTERRUPTION_LEVEL) btn.classList.add('is-default');
    if (index === INTERRUPTION_LEVELS.length - 1) btn.classList.add('is-last');
    if (level.value === saved) btn.classList.add('is-selected');
    const hintHtml = level.hint
      ? `<span class="interrupt-picker-option-hint">${level.hint}</span>`
      : '';
    btn.innerHTML = `
      <span class="interrupt-picker-option-num">${level.value}</span>
      <span class="interrupt-picker-option-label">${level.label}</span>
      ${hintHtml}
    `;
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      els.advancedInterruptOptions.querySelectorAll('.interrupt-picker-option').forEach((b) => {
        b.classList.toggle('is-selected', b === btn);
      });
      saveInterruptionLevel(Number(btn.dataset.level));
      saveDraftFromForm();
    });
    els.advancedInterruptOptions.appendChild(btn);
  });

  els.advancedSettingsToggle?.addEventListener('click', () => {
    const open = els.advancedSettingsBody?.hidden;
    if (els.advancedSettingsBody) els.advancedSettingsBody.hidden = !open;
    setUiState({ advancedOpen: Boolean(open) });
    syncAdvancedSettingsUi();
  });

  [els.advancedModName, els.advancedModId, els.advancedPackageName].forEach((el) => {
    el?.addEventListener('input', () => {
      const fieldKey = el.id === 'advancedModName'
        ? 'modName'
        : el.id === 'advancedModId'
          ? 'modId'
          : 'packageName';
      const cfg = MOD_META_FIELD_CONFIG[fieldKey];
      const check = cfg.validate(el.value.trim());
      setModMetaFieldError(fieldKey, check.valid ? '' : check.message);
      saveDraftFromForm();
    });
    el?.addEventListener('blur', () => {
      validateModMetaForm({ showErrors: true });
      maybeDeriveModIdAndPackage();
      saveDraftFromForm();
    });
  });

  els.advancedModName?.addEventListener('blur', () => {
    if (els.advancedModName?.value.trim() && !els.advancedModId?.value.trim()) {
      maybeDeriveModIdAndPackage();
    }
  });

  const ui = getUiState();
  if (els.advancedSettingsBody) els.advancedSettingsBody.hidden = !ui.advancedOpen;
  if (els.advancedMaxTurns) {
    els.advancedMaxTurns.value = String(getSavedMaxTurns());
    els.advancedMaxTurns.addEventListener('input', () => {
      validateMaxTurnsForm({ showErrors: true });
      saveDraftFromForm();
    });
    els.advancedMaxTurns.addEventListener('blur', () => {
      const check = validateMaxTurnsForm({ showErrors: true });
      if (check.valid) {
        els.advancedMaxTurns.value = String(check.value);
        saveMaxTurns(check.value);
      }
      saveDraftFromForm();
    });
  }
  syncAdvancedSettingsUi();
}

function initDescOptimize() {
  els.optimizeDescBtn?.addEventListener('click', runOptimizeDescription);

  els.descOptimizeCollapseBtn?.addEventListener('click', toggleDescOptimizeCollapse);

  els.adoptDescBtn?.addEventListener('click', () => {
    if (!state.descOptimize.text.trim() || state.descOptimize.bodyCollapsed) return;
    state.descOptimize.adoptConfirming = true;
    updateAdoptDescVisibility();
    els.adoptDescConfirmBtn?.focus();
  });

  els.adoptDescConfirmBtn?.addEventListener('click', confirmAdoptDescription);
  els.adoptDescCancelBtn?.addEventListener('click', resetAdoptConfirm);
}

function updatePromptActions() {
  const rawLen = els.promptInput.value.trim().length;
  const valid = isPromptLengthValid() && !state.isSubmitting;
  els.startBuildBtn.disabled = !valid;
  if (els.startPlanBtn) els.startPlanBtn.disabled = !valid;
  if (els.optimizeDescBtn) {
    els.optimizeDescBtn.disabled =
      !state.descOptimizeEnabled ||
      rawLen < VALIDATION.minPromptLength ||
      state.isSubmitting ||
      state.descOptimize.loading;
    els.optimizeDescBtn.title = state.descOptimizeEnabled ? '' : '管理员未启用描述优化';
  }
}

function setSubmitting(submitting) {
  state.isSubmitting = submitting;
  const rawLen = els.promptInput.value.trim().length;
  const valid = isPromptLengthValid();
  els.startBuildBtn.disabled = submitting || !valid;
  if (els.startPlanBtn) els.startPlanBtn.disabled = submitting || !valid;
  setStartPlanBtnBusy(submitting);
  els.promptInput.disabled = submitting;
  els.submitLoading.hidden = !submitting;
  if (els.optimizeDescBtn) {
    els.optimizeDescBtn.disabled =
      !state.descOptimizeEnabled ||
      submitting ||
      state.descOptimize.loading ||
      rawLen < VALIDATION.minPromptLength;
  }
  if (!submitting) updatePromptActions();
}

// --- Payload & API ---
function buildPromptContext(mode, interruptionLevel, modMeta = {}) {
  const projectType = getProjectType(state.modLoader);
  const selectedRequirements = getActiveRequirements().filter((r) =>
    state.requirements.has(r.id),
  );

  return {
    projectType,
    mcVersion: state.mcVersion,
    modLoader: state.modLoader,
    modLoaderLabel: getLoaderLabel(state.modLoader),
    platform: state.platform,
    platformLabel: getPlatformLabel(state.platform),
    userConcept: getEffectivePromptText(),
    mode,
    modName: modMeta.modName || '',
    modId: modMeta.modId || '',
    packageName: modMeta.packageName || '',
    interruptionLevel:
      mode === 'build'
        ? interruptionLevel !== undefined
          ? interruptionLevel
          : getSavedInterruptionLevel()
        : undefined,
    selectedRequirements,
    referenceModsManual: state.referenceMods.manual,
    autoSearch: state.referenceMods.autoSearch,
  };
}

function buildPayload(mode, interruptionLevel, modMeta = {}, maxTurns = getSavedMaxTurns()) {
  const requirements = [...state.requirements];
  const requirements_detail = {};
  const active = getActiveRequirements();

  active.forEach((req) => {
    if (state.requirements.has(req.id)) {
      requirements_detail[req.id] = req.detail ? { ...req.detail } : {};
    }
  });

  const reference_mods = buildReferenceModsPayload(state.referenceMods);
  const ctx = buildPromptContext(mode, interruptionLevel, modMeta);

  const payload = {
    prompt: buildFinalPrompt(ctx),
    mode,
    minecraft_version: state.mcVersion,
    mod_loader: state.modLoader,
    platform: state.platform,
    reference_mods,
    requirements,
    requirements_detail,
    locale: LOCALE,
  };

  if (modMeta.modName) payload.mod_name = modMeta.modName;
  if (modMeta.modId) payload.mod_id = modMeta.modId;
  if (modMeta.packageName) payload.package_name = modMeta.packageName;

  if (mode === 'build') {
    payload.interruption_level =
      interruptionLevel !== undefined ? interruptionLevel : getSavedInterruptionLevel();
    payload.max_turns = clampMaxTurns(maxTurns);
  }

  return payload;
}

function buildPromptPreviewText() {
  return buildFinalPrompt(buildPromptContext('build', getSavedInterruptionLevel()));
}

function openPromptPreview() {
  els.promptPreviewContent.textContent = buildPromptPreviewText();
  els.promptPreviewOverlay.hidden = false;
  document.body.style.overflow = 'hidden';
}

function closePromptPreview() {
  els.promptPreviewOverlay.hidden = true;
  document.body.style.overflow = '';
}

function getApiUrl(path = API.createSession) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return API.baseUrl ? `${API.baseUrl.replace(/\/$/, '')}${p}` : p;
}

async function submitSession(payload) {
  const url = getApiUrl();
  const headers = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem(TOKEN_STORAGE_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  });

  let data = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { message: text };
    }
  }

  if (!res.ok) {
    const msg = data?.message || data?.error || `请求失败 (${res.status})`;
    throw new Error(msg);
  }

  return data;
}

async function suggestModMetadata({ prompt, taskTitle, modName, modId, packageName }) {
  const res = await fetch(apiUrl(API.suggestModMetadata), {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({
      prompt,
      task_title: taskTitle,
      mod_name: modName || undefined,
      mod_id: modId || undefined,
      package_name: packageName || undefined,
    }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data?.detail;
    const msg = Array.isArray(detail)
      ? detail.map((d) => d.msg || d).join('；')
      : detail || data?.message || '自动补全失败';
    throw new Error(msg);
  }
  return {
    modName: data.mod_name || '',
    modId: data.mod_id || '',
    packageName: data.package_name || '',
  };
}

function apiUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return API.baseUrl ? `${API.baseUrl.replace(/\/$/, '')}${p}` : p;
}

function buildPlanContextPayload(knowledgeL1) {
  const active = getActiveRequirements().filter((r) => state.requirements.has(r.id));
  const requirements_detail = {};
  active.forEach((req) => {
    requirements_detail[req.id] = req.detail ? { ...req.detail } : {};
  });
  const modMeta = getModMetaFromForm();
  const interruptionLevel = getSavedInterruptionLevel();
  const maxTurns = getSavedMaxTurns();
  const promptCtx = buildPromptContext('build', interruptionLevel, modMeta);
  const ctx = {
    user_concept: getEffectivePromptText(),
    minecraft_version: state.mcVersion,
    mod_loader: state.modLoader,
    platform: state.platform,
    requirements: active,
    requirements_detail,
    reference_mods: buildReferenceModsPayload(state.referenceMods),
    locale: LOCALE,
    interruption_level: interruptionLevel,
    max_turns: clampMaxTurns(maxTurns),
    handoff_appendix: buildHandoffAppendix(promptCtx),
  };
  if (modMeta.modName) ctx.mod_name = modMeta.modName;
  if (modMeta.modId) ctx.mod_id = modMeta.modId;
  if (modMeta.packageName) ctx.package_name = modMeta.packageName;
  if (knowledgeL1) ctx.knowledge_l1 = knowledgeL1;
  ctx.plan_read_depth = hasCodeReferenceMods(state.referenceMods.manual)
    ? getSelectedPlanReadDepth()
    : 'fast';
  return ctx;
}

let pendingPlanAfterL1 = false;

function openHomeL1Modal(existingL1) {
  renderL1Form(els.homeL1Body, { values: existingL1 || undefined });
  const intro = els.homeL1Body?.querySelector('.plan-l1-intro');
  if (intro) {
    intro.textContent = '为了向您解释为什么，我们必须首先假设您知道些什么。请允许我们大致了解您的知识水平。确认后将进入规划页。';
  }
  els.homeL1Overlay.hidden = false;
  document.body.style.overflow = 'hidden';
  requestAnimationFrame(() => {
    els.homeL1Confirm?.focus();
  });
}

function closeHomeL1Modal() {
  els.homeL1Overlay.hidden = true;
  document.body.style.overflow = '';
  pendingPlanAfterL1 = false;
  closePlanDepthPopover();
  if (els.homeL1Confirm) els.homeL1Confirm.disabled = false;
}

async function confirmHomeL1AndPlan() {
  if (state.isSubmitting) return;
  const form = els.homeL1Body?.querySelector('#planL1Form');
  const l1 = collectL1FromForm(form);
  if (els.homeL1Confirm) els.homeL1Confirm.disabled = true;
  setSubmitting(true);
  try {
    const saved = await saveKnowledgeL1(l1);
    state.cachedKnowledgeL1 = saved || l1;
    closeHomeL1Modal();
    await startPlanWithContext(state.cachedKnowledgeL1);
  } catch (err) {
    showToast(err.message || '保存知识水平失败', 'error');
    setSubmitting(false);
    if (els.homeL1Confirm) els.homeL1Confirm.disabled = false;
  }
}

async function startPlanWithContext(knowledgeL1) {
  const promptText = getEffectivePromptText();
  const taskTitle = deriveTaskTitle(
    buildReadableBlueprint(
      promptText,
      state.descOptimize.text,
      state.descOptimize.visible && !state.descOptimize.bodyCollapsed,
    ),
    promptText,
  );

  sessionStorage.setItem(
    'plan_bootstrap',
    JSON.stringify({
      context: buildPlanContextPayload(knowledgeL1),
      task_title: taskTitle,
    }),
  );
  window.location.href = '/plan.html?creating=1';
}

async function onStartPlanClick() {
  if (state.isSubmitting) return;

  if (!getToken()) {
    showToast('请先登录后再开始规划', 'error');
    const next = encodeURIComponent(window.location.pathname);
    window.location.href = `/login.html?next=${next}`;
    return;
  }

  if (!isPromptLengthValid()) {
    showToast(`请至少输入 ${VALIDATION.minPromptLength} 个字符描述您的 ${getConceptLabelShort()}`, 'error');
    return;
  }

  if (!els.planDepthPopover?.hidden) {
    closePlanDepthPopover();
    return;
  }

  if (!hasCodeReferenceMods(state.referenceMods.manual)) {
    await submitPlanProceed();
    return;
  }

  await openPlanDepthPopover();
}

async function confirmPlanWithDepth(depth) {
  if (state.isSubmitting) return;
  if (!PLAN_READ_DEPTHS.some((d) => d.value === depth)) return;

  savePlanReadDepth(depth);
  syncPlanDepthSelection(depth);
  saveDraftFromForm();
  closePlanDepthPopover();
  await submitPlanProceed();
}

async function submitPlanProceed() {
  if (state.isSubmitting) return;

  setSubmitting(true);
  closeRequirementPopover();
  closeRefModPopover();

  let navigated = false;
  try {
    const cached = await ensureKnowledgeL1Loaded();
    if (cached?.programming != null) {
      await startPlanWithContext(cached);
      navigated = true;
      return;
    }
    pendingPlanAfterL1 = true;
    showToast('首次规划需确认知识水平', 'info');
    openHomeL1Modal(null);
  } catch (err) {
    showToast(err.message || '网络错误', 'error');
  } finally {
    if (!navigated) setSubmitting(false);
  }
}

async function handleSubmit(mode, interruptionLevel, modMeta = {}, maxTurns = getSavedMaxTurns()) {
  if (!getToken()) {
    showToast('请先登录后再开始编写', 'error');
    const next = encodeURIComponent(window.location.pathname);
    window.location.href = `/login.html?next=${next}`;
    return;
  }

  if (!isPromptLengthValid()) {
    showToast(`请至少输入 ${VALIDATION.minPromptLength} 个字符描述您的 ${getConceptLabelShort()}`, 'error');
    return;
  }

  const readableBlueprint = buildReadableBlueprint(
    els.promptInput.value.trim(),
    state.descOptimize.text,
    state.descOptimize.visible && !state.descOptimize.bodyCollapsed,
  );
  const promptText = getEffectivePromptText();
  const readableBlueprintFallbackTitle = deriveTaskTitle(readableBlueprint, promptText);

  let resolvedModMeta = { ...modMeta };

  setSubmitting(true);
  closeRequirementPopover();
  closeRefModPopover();

  const modeLabel = mode === 'plan' ? '规划' : '编写';

  try {
    if (mode === 'build' && !resolvedModMeta.modName?.trim()) {
      resolvedModMeta = await suggestModMetadata({
        prompt: promptText,
        taskTitle: readableBlueprintFallbackTitle,
        modId: resolvedModMeta.modId || undefined,
        packageName: resolvedModMeta.packageName || undefined,
      });
      applyModMetaToForm(resolvedModMeta);
    }

    const taskTitle =
      resolvedModMeta.modName?.trim() || readableBlueprintFallbackTitle;

    const payload = buildPayload(mode, interruptionLevel, resolvedModMeta, maxTurns);
    const bootstrap = buildSessionBootstrap(payload, payload.prompt, readableBlueprint, taskTitle);

    const result = await createSession(payload, { readableBlueprint, taskTitle });
    bootstrap.sessionId = result.session_id || result.id;
    sessionStorage.setItem(SESSION_STORAGE_BOOTSTRAP, JSON.stringify(bootstrap));
    upsertSessionListEntry({
      sessionId: bootstrap.sessionId,
      taskTitle,
      mode,
      status: 'starting',
      createdAt: Date.now(),
    });
    syncPreferencesToServer();
    showToast(`已开始${modeLabel}，正在进入编写页…`, 'success');
    window.location.href = result.redirect_url || `session.html?session_id=${bootstrap.sessionId}`;
  } catch (err) {
    showToast(err.message || '网络错误，请稍后重试', 'error');
  } finally {
    setSubmitting(false);
  }
}

function getPromptTextareaFloorHeight() {
  const sm = window.matchMedia('(min-width: 640px)').matches;
  const rootPx = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  const btnVar = getComputedStyle(document.documentElement).getPropertyValue('--prompt-btn-height').trim();
  const btnPx = btnVar.endsWith('rem') ? parseFloat(btnVar) * rootPx : parseFloat(btnVar) || 34;
  return Math.round((sm ? 200 : 140) + btnPx);
}

function computeAutoTextareaHeight() {
  const vh = window.innerHeight;
  const sm = window.matchMedia('(min-width: 640px)').matches;
  const floor = getPromptTextareaFloorHeight();
  const preferred = Math.round(vh * (sm ? 0.28 : 0.24));
  const cap = Math.round(vh * 0.4);
  return Math.max(floor, Math.min(preferred, cap));
}

function initPromptTextareaResize() {
  const textarea = els.promptInput;
  const handle = els.promptTextareaResize;
  if (!textarea || !handle) return;

  let isManualHeight = false;

  const getMaxHeight = () => Math.round(window.innerHeight * 0.72);
  const getMinHeight = () => getPromptTextareaFloorHeight();

  const applyAutoTextareaHeight = () => {
    if (isManualHeight) return;
    const h = computeAutoTextareaHeight();
    textarea.style.removeProperty('height');
    textarea.style.setProperty('--prompt-textarea-auto-height', `${h}px`);
    textarea.classList.remove('prompt-textarea--manual');
  };

  const applyManualHeight = (heightPx) => {
    const clamped = Math.min(getMaxHeight(), Math.max(getMinHeight(), heightPx));
    textarea.style.removeProperty('--prompt-textarea-auto-height');
    textarea.style.height = `${clamped}px`;
    textarea.classList.add('prompt-textarea--manual');
    isManualHeight = true;
  };

  applyAutoTextareaHeight();
  requestAnimationFrame(applyAutoTextareaHeight);

  window.addEventListener('resize', () => {
    if (!isManualHeight) {
      applyAutoTextareaHeight();
      return;
    }
    const current = textarea.getBoundingClientRect().height;
    if (current > getMaxHeight()) applyManualHeight(getMaxHeight());
  });

  let dragging = false;
  let dragMoved = false;
  let startY = 0;
  let startHeight = 0;

  const onPointerMove = (e) => {
    if (!dragging) return;
    if (Math.abs(e.clientY - startY) > 2) dragMoved = true;
    applyManualHeight(startHeight + (e.clientY - startY));
  };

  const endDrag = () => {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('is-prompt-resizing');
    document.removeEventListener('pointermove', onPointerMove);
    document.removeEventListener('pointerup', endDrag);
    document.removeEventListener('pointercancel', endDrag);
    if (!dragMoved) return;
    isManualHeight = true;
    textarea.classList.add('prompt-textarea--manual');
    textarea.style.removeProperty('--prompt-textarea-auto-height');
    const h = textarea.getBoundingClientRect().height;
    if (Math.abs(h - computeAutoTextareaHeight()) < 2) {
      isManualHeight = false;
      textarea.classList.remove('prompt-textarea--manual');
      textarea.style.height = '';
      applyAutoTextareaHeight();
      return;
    }
    textarea.style.height = `${h}px`;
  };

  handle.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    dragging = true;
    dragMoved = false;
    startY = e.clientY;
    startHeight = textarea.getBoundingClientRect().height;
    document.body.classList.add('is-prompt-resizing');
    handle.setPointerCapture(e.pointerId);
    document.addEventListener('pointermove', onPointerMove);
    document.addEventListener('pointerup', endDrag);
    document.addEventListener('pointercancel', endDrag);
  });
}

function redirectGuestFromPromptInput() {
  if (getToken()) return;
  showToast('请先登录后再输入构想', 'error');
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login.html?next=${next}`;
}

function initPromptInputGuestGuard() {
  const textarea = els.promptInput;
  if (!textarea) return;

  textarea.addEventListener('pointerdown', (e) => {
    if (getToken()) return;
    e.preventDefault();
    redirectGuestFromPromptInput();
  });

  textarea.addEventListener('focus', (e) => {
    if (getToken()) return;
    e.target.blur();
    redirectGuestFromPromptInput();
  });
}

// --- Misc handlers ---
function initHandlers() {
  els.themeToggle.addEventListener('click', toggleTheme);
  initPromptTextareaResize();
  initPromptInputGuestGuard();

  els.promptInput.addEventListener('input', updatePromptActions);

  els.promptInput.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (!els.startBuildBtn.disabled) handleSubmit('build', getSavedInterruptionLevel());
    }
  });

  els.promptForm.addEventListener('submit', (e) => e.preventDefault());

  els.startBuildBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    submitBuild();
  });
  els.startPlanBtn.addEventListener('click', (e) => {
    e.stopPropagation();
    onStartPlanClick();
  });

  els.homeL1Cancel?.addEventListener('click', closeHomeL1Modal);
  els.homeL1Confirm?.addEventListener('click', confirmHomeL1AndPlan);
  els.homeL1Overlay?.addEventListener('click', (e) => {
    if (e.target === els.homeL1Overlay) closeHomeL1Modal();
  });

  els.promptPreviewBtn.addEventListener('click', openPromptPreview);
  els.promptPreviewClose.addEventListener('click', closePromptPreview);
  els.promptPreviewDone.addEventListener('click', closePromptPreview);
  els.promptPreviewOverlay.addEventListener('click', (e) => {
    if (e.target === els.promptPreviewOverlay) closePromptPreview();
  });
}

async function syncPreferencesToServer() {
  if (!getToken()) return;
  try {
    await fetch(API.userPreferences, {
      method: 'PUT',
      headers: authHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        requirements: loadRequirements(),
        reference_mods: buildReferenceModsPayload(loadReferenceModsState()),
      }),
    });
  } catch {
    /* ignore */
  }
}

// --- Init ---
async function init() {
  initTheme();
  initAccountStatus();
  initDropdowns();
  initNavigation();
  initPlanDepthPicker();
  initAdvancedSettings();
  initDescOptimize();
  initReferenceMods();
  initRequirements();
  try {
    const loaded = await initDefaultRequirements();
    const refreshed =
      (loaded && syncRequirementsFromServerDefaults()) ||
      reconcileRequirementsWithServerDefaults();
    if (refreshed) renderRequirements();
  } catch {
    if (reconcileRequirementsWithServerDefaults()) renderRequirements();
  }
  initHandlers();
  initSessionListPanel({
    bodyEl: els.sessionListPanelBody,
    titleEl: els.sessionListPanelTitle,
    footerEl: els.sessionListRecycleBtn,
    newConceptBtnEl: els.conceptNewBtn,
    showToast,
    onBeforeConceptSwitch: saveDraftFromForm,
    onAfterConceptSwitch: () => applyDraftToForm(getActiveDraft()),
  });
  await initConceptDrafts({ onStoreChange: () => renderSessionListPanel() });
  const siteSettings = await fetchSiteSettings();
  if (siteSettings?.mod_template_package) {
    setModTemplatePackage(siteSettings.mod_template_package);
  }
  if (siteSettings && siteSettings.desc_optimize_enabled === false) {
    state.descOptimizeEnabled = false;
  }
  if (isGuest()) {
    applyGuestDefaultsToForm();
  } else {
    applyDraftToForm(getActiveDraft());
  }
  renderSessionListPanel(true);

  if (!isGuest()) {
    els.promptInput?.addEventListener('input', () => {
      updatePromptActions();
      saveDraftFromForm();
    });
    ensureKnowledgeL1Loaded();
  }

  const defaultLoader = (VERSION_LOADERS[DEFAULTS.mcVersion] || []).find(
    (l) => l.value === DEFAULTS.modLoader
  );
  if (defaultLoader) {
    updateModLoaderValueDisplay(defaultLoader.label, defaultLoader.value);
  }

  const defaultPlatform = PLATFORMS.find((p) => p.value === DEFAULTS.platform);
  if (defaultPlatform) {
    els.platformValue.textContent = defaultPlatform.label;
  }

  updatePromptActions();
}

init().catch((err) => {
  console.error('页面初始化失败', err);
});
