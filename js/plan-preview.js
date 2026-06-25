/** @file 规划模式页面 */

import { getToken } from './auth.js';
import {
  connectPlanEvents,
  createPlanSession,
  fetchPlanSnapshot,
  finalizePlan,
  handoffPlan,
  isAdminViewMode,
  regeneratePlanQuestion,
  regeneratePlanTurn,
  retryPlanFirstTurn,
  retryPlanReference,
  skipPlanReferenceWait,
  submitPlanTurn,
  updatePlanKnowledgeL1,
} from './plan-api.js';
import { initConceptDrafts } from './concept-drafts.js';
import { hasCodeReferenceMods } from './reference-mods.js';
import { collectL1FromForm, renderL1Form } from './plan-l1.js';
import { showPlanNodePopover } from './plan-node-popover.js';
import { renderPlanQuestionForm } from './plan-question-form.js';
import { hideStreamPanel, showStreamPanel } from './plan-stream-panel.js';
import { renderPlanTreeGraph } from './plan-tree-graph.js';
import { initSessionListPanel, renderSessionListPanel } from './session-list-panel.js';
import { upsertSessionListEntry } from './session-utils.js';
import { renderMarkdown } from './markdown-render.js';

export const PLAN_STORAGE_BOOTSTRAP = 'plan_bootstrap';

const params = new URLSearchParams(window.location.search);
const isCreating = params.get('creating') === '1';
let activePlanId = params.get('plan_id');
const adminOwnerId = params.get('owner_id') || '';
let adminViewMode = false;

const MOBILE_MQ = window.matchMedia('(max-width: 767px)');

const els = {
  title: document.getElementById('planTaskTitle'),
  difficulty: document.getElementById('planDifficulty'),
  readiness: document.getElementById('planReadiness'),
  processing: document.getElementById('planProcessing'),
  history: document.getElementById('planHistory'),
  tree: document.getElementById('planTree'),
  treeGraphWrap: document.getElementById('planTreeGraphWrap'),
  treeSvg: document.getElementById('planTreeSvg'),
  treeViewList: document.getElementById('planTreeViewList'),
  treeViewGraph: document.getElementById('planTreeViewGraph'),
  message: document.getElementById('planAssistantMessage'),
  questions: document.getElementById('planQuestions'),
  finalizeBtn: document.getElementById('planFinalizeBtn'),
  l1Overlay: document.getElementById('planL1Overlay'),
  l1Body: document.getElementById('planL1Body'),
  l1Save: document.getElementById('planL1Save'),
  l1Cancel: document.getElementById('planL1Cancel'),
  finalizeOverlay: document.getElementById('planFinalizeOverlay'),
  finalizePreview: document.getElementById('planFinalizePreview'),
  finalizeConfirm: document.getElementById('planFinalizeConfirm'),
  finalizeCancel: document.getElementById('planFinalizeCancel'),
  finalizeRegenerate: document.getElementById('planFinalizeRegenerate'),
  contextBody: document.getElementById('planContextBody'),
  referenceBody: document.getElementById('planReferenceBody'),
};

let planData = null;
let lastSnapshotAt = 0;
/** @type {(() => void) | null} */
let planEventsCleanup = null;
let streamBuffer = '';
let streamingActive = false;
/** @type {{ append: (t: string) => void, destroy: () => void } | null} */
let questionStreamPanel = null;
/** @type {{ append: (t: string) => void, destroy: () => void } | null} */
let finalizeStreamPanel = null;
let treeViewMode = 'list';
/** @type {(() => void) | null} */
let treeGraphCleanup = null;
/** 创建后等待首轮 LLM，避免 idle snapshot 误判为 orphan */
let expectingFirstTurn = false;
/** -1 = 跟随最新轮；否则为 turns 下标 */
let selectedTurnIndex = -1;

const FIRST_TURN_GRACE_MS = 3 * 60 * 1000;

const DIFFICULTY_LABELS = {
  low: '较低',
  medium: '中等',
  high: '较高',
  extreme: '极高',
};

const STATUS_LABELS = {
  draft: '草稿',
  open: '进行中',
  done: '完成',
};

const FINALIZE_BTN_LABEL = {
  default: '结束规划并编写',
  hasFinal: '查看规划并编写',
  handedOff: '查看规划并编写',
};

function syncFinalizeButtonState() {
  if (!els.finalizeBtn) return;
  if (adminViewMode) {
    if (planData?.final_markdown) {
      els.finalizeBtn.hidden = false;
      els.finalizeBtn.disabled = false;
      els.finalizeBtn.textContent = FINALIZE_BTN_LABEL.handedOff;
    } else {
      els.finalizeBtn.hidden = true;
    }
    return;
  }
  els.finalizeBtn.hidden = false;
  const processing = !!planData?.processing;
  const handedOff = planData?.status === 'handed_off';
  const hasFinal = Boolean(planData?.final_markdown);
  els.finalizeBtn.disabled = processing || streamingActive;
  els.finalizeBtn.textContent = handedOff
    ? FINALIZE_BTN_LABEL.handedOff
    : hasFinal
      ? FINALIZE_BTN_LABEL.hasFinal
      : FINALIZE_BTN_LABEL.default;
}

function syncFinalizeModalActions() {
  const handedOff = planData?.status === 'handed_off';
  const readOnly = adminViewMode;
  if (els.finalizeRegenerate) els.finalizeRegenerate.hidden = readOnly;
  if (els.finalizeConfirm) els.finalizeConfirm.hidden = readOnly;
  if (els.finalizeCancel) {
    els.finalizeCancel.textContent = readOnly ? '关闭' : '继续规划';
  }
  const title = document.getElementById('planFinalizeTitle');
  if (title) {
    title.textContent = handedOff || readOnly ? '规划文档' : '确认规划并进入编写';
  }
}

function applyAdminViewBanner() {
  const banner = document.getElementById('planAdminBanner');
  const textEl = document.getElementById('planAdminBannerText');
  if (!banner) return;
  if (!adminViewMode) {
    banner.hidden = true;
    return;
  }
  banner.hidden = false;
  const owner = planData?.owner_email || adminOwnerId || '—';
  const username = planData?.owner_username;
  const ownerLabel = username ? `${username} (${owner})` : owner;
  if (textEl) textEl.textContent = `管理员只读查看 · 所属用户：${ownerLabel}`;
}

function syncAdminViewUi() {
  applyAdminViewBanner();
  if (!adminViewMode) return;
  document.getElementById('conceptNewBtn')?.setAttribute('hidden', '');
  document.getElementById('historyRecycleBtn')?.setAttribute('hidden', '');
  syncFinalizeButtonState();
}

function showToast(message, type = 'info') {
  let el = document.getElementById('planToast');
  if (!el) {
    el = document.createElement('div');
    el.id = 'planToast';
    el.className = 'plan-toast';
    document.body.appendChild(el);
  }
  el.className = `plan-toast plan-toast--${type}`;
  el.textContent = message;
  el.hidden = false;
  clearTimeout(showToast._t);
  showToast._t = setTimeout(() => { el.hidden = true; }, 4500);
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

function getCurrentTurn() {
  const turns = planData?.turns || [];
  return turns.length ? turns[turns.length - 1] : null;
}

function getLatestTurnIndex() {
  const turns = planData?.turns || [];
  return turns.length ? turns.length - 1 : -1;
}

function getDisplayTurnIndex() {
  const latest = getLatestTurnIndex();
  if (latest < 0) return -1;
  if (selectedTurnIndex < 0 || selectedTurnIndex > latest) return latest;
  return selectedTurnIndex;
}

function getDisplayTurn() {
  const idx = getDisplayTurnIndex();
  if (idx < 0) return null;
  return (planData?.turns || [])[idx] || null;
}

function isViewingHistoryTurn() {
  const latest = getLatestTurnIndex();
  const idx = getDisplayTurnIndex();
  return latest >= 0 && idx >= 0 && idx < latest;
}

function isTurnEditable(index) {
  const latest = getLatestTurnIndex();
  if (index !== latest) return false;
  const turn = (planData?.turns || [])[index];
  if (!turn || turn.user_reply != null) return false;
  if (planData?.processing || streamingActive) return false;
  return true;
}

function getDisplayBlueprintTree() {
  const idx = getDisplayTurnIndex();
  const turns = planData?.turns || [];
  if (idx >= 0 && idx < turns.length && isViewingHistoryTurn()) {
    return turns[idx].blueprint_tree_snapshot || [];
  }
  return planData?.blueprint_tree || [];
}

function selectTurn(index) {
  const latest = getLatestTurnIndex();
  if (index < 0 || index > latest) return;
  selectedTurnIndex = index;
  renderHistory();
  renderTree();
  renderCurrentTurnBadge();
  renderCurrentTurn();
}

function isPlanRecentlyCreated(data = planData) {
  const created = Date.parse(data?.created_at || '') || 0;
  return Boolean(created && Date.now() - created < FIRST_TURN_GRACE_MS);
}

function isAwaitingFirstTurn(data = planData) {
  if (!data || (data.turns || []).length) return false;
  if (data.status !== 'active' && data.status !== 'ready') return false;
  if (data.last_error) return false;
  return expectingFirstTurn || !!data.processing || isPlanRecentlyCreated(data);
}

function needsFirstTurnRetry() {
  if (!planData || planData.status === 'awaiting_l1') return false;
  if (streamingActive) return false;
  if ((planData.turns || []).length) return false;
  if (isAwaitingFirstTurn()) return false;
  return planData.status === 'active' || planData.status === 'ready';
}

function getFirstTurnRetryMessage() {
  return planData?.last_error || '参考索引与首轮出题可能因并发异常中断，规划仍可继续。';
}

function hasUnsubmittedCurrentTurn(data = planData) {
  const turns = data?.turns || [];
  if (!turns.length) return false;
  return turns[turns.length - 1]?.user_reply == null;
}

function shouldApplyIdleSnapshot(data) {
  if (!data) return false;
  if (data.processing) return false;
  if (!(data.turns || []).length && isAwaitingFirstTurn(data)) return false;
  if (hasUnsubmittedCurrentTurn(data)) return false;
  return true;
}

function shouldShowReferenceIndexStream(data = planData) {
  return isWaitingReferenceIndex(data) && !(data?.turns || []).length;
}

function shouldUseQuestionsStreamPanel() {
  if (hasUnsubmittedCurrentTurn()) return false;
  if (finalizeStreamPanel) return false;
  return true;
}

function recoverQuestionsPanelIfStuck() {
  if (!hasUnsubmittedCurrentTurn() || !streamingActive) return;
  endStreaming();
  if (planData) planData.processing = false;
  renderCurrentTurn();
}

function handleReferenceBackgroundUpdate(data) {
  if (hasUnsubmittedCurrentTurn(planData)) {
    recoverQuestionsPanelIfStuck();
    patchReferenceFieldsFromSnapshot(data);
    return;
  }
  applySnapshotIfReady(data);
}

function patchReferenceFieldsFromSnapshot(data) {
  if (!planData || !data) return;
  if (data.reference_index) planData.reference_index = data.reference_index;
  if (data.reference_cards) planData.reference_cards = data.reference_cards;
  if (data.research_findings?.length) planData.research_findings = data.research_findings;
  renderContextPanel();
  renderReferencePanel();
}

function findOptionLabel(turn, qid, optId) {
  const q = (turn?.questions || []).find((item) => item.id === qid);
  const opt = (q?.options || []).find((item) => item.id === optId);
  return opt?.label || optId;
}

function formatHistoryReply(turn, reply) {
  if (!reply) return '';
  const parts = [];
  const answers = reply.answers || {};
  Object.entries(answers).forEach(([qid, val]) => {
    const ids = Array.isArray(val) ? val : [val];
    const labels = ids.map((id) => findOptionLabel(turn, qid, id)).join('、');
    parts.push(labels);
  });
  Object.entries(reply.custom || {}).forEach(([, text]) => {
    if (text) parts.push(String(text));
  });
  let line = parts.length ? `已回答：${parts.join('；')}` : '已回答';
  if (reply.overall_remarks) line += ` · 自由回复：${reply.overall_remarks}`;
  return line;
}

function isWaitingReferenceIndex(data) {
  if (!data?.processing || (data.turns || []).length) return false;
  if (data.reference_wait_skipped) return false;
  return hasCodeReferenceMods(data.context?.reference_mods?.manual);
}

function beginQuestionStream(label, opts = {}) {
  streamingActive = true;
  streamBuffer = '';
  if (els.processing) els.processing.hidden = false;
  if (els.questions) {
    const mode = opts.mode || 'json';
    questionStreamPanel = showStreamPanel(els.questions, {
      label,
      mode,
      showSkip: opts.showSkip,
      onSkip: opts.onSkip,
    });
  }
}

function beginReferenceIndexStream(planId) {
  if (!shouldShowReferenceIndexStream()) return;
  beginQuestionStream('正在索引参考模组…', {
    mode: 'tools',
    showSkip: true,
    onSkip: async () => {
      questionStreamPanel?.disableSkip?.();
      questionStreamPanel?.switchToJson?.();
      questionStreamPanel?.setLabel?.('已跳过索引等待，正在生成首轮问题…');
      const updated = await skipPlanReferenceWait(planId);
      if (updated) {
        planData = {
          ...planData,
          ...updated,
          reference_wait_skipped: true,
          processing: true,
        };
      } else if (planData) {
        planData.reference_wait_skipped = true;
        planData.processing = true;
      }
    },
  });
}

function beginFinalizeStream() {
  streamingActive = true;
  streamBuffer = '';
  if (els.finalizePreview) {
    finalizeStreamPanel = showStreamPanel(els.finalizePreview, {
      label: 'LLM 正在生成规划 Markdown…',
      mode: 'markdown',
    });
  }
}

function appendStreamDelta(text) {
  if (!text) return;
  streamBuffer += text;
  questionStreamPanel?.append(text);
  finalizeStreamPanel?.append(text);
}

function endStreaming() {
  streamingActive = false;
  streamBuffer = '';
  questionStreamPanel?.destroy();
  questionStreamPanel = null;
  finalizeStreamPanel?.destroy();
  finalizeStreamPanel = null;
  if (els.questions) hideStreamPanel(els.questions);
}

function renderHistory() {
  if (!els.history) return;
  const turns = planData?.turns || [];
  if (!turns.length) {
    els.history.innerHTML = '<p class="plan-empty">等待首轮分析…</p>';
    return;
  }

  const activeIdx = getDisplayTurnIndex();
  els.history.innerHTML = turns
    .map((turn, i) => {
      const reply = turn.user_reply;
      const replyText = reply
        ? `<div class="plan-history-reply">${escapeHtml(formatHistoryReply(turn, reply))}</div>`
        : '<div class="plan-history-reply plan-history-reply--pending">待回答</div>';
      const activeClass = i === activeIdx ? ' is-active' : '';
      return `
        <div class="plan-history-item${activeClass}" role="button" tabindex="0"
          data-turn-index="${i}" aria-pressed="${i === activeIdx ? 'true' : 'false'}">
          <div class="plan-history-round">第 ${i + 1} 轮</div>
          <div class="plan-history-msg">${escapeHtml(turn.assistant_message || '')}</div>
          ${replyText}
        </div>
      `;
    })
    .join('');

  els.history.querySelectorAll('.plan-history-item[data-turn-index]').forEach((el) => {
    const idx = Number(el.getAttribute('data-turn-index'));
    const activate = () => selectTurn(idx);
    el.addEventListener('click', activate);
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        activate();
      }
    });
  });
}

function renderCurrentTurnBadge() {
  const badge = document.getElementById('planCurrentTurnBadge');
  if (!badge) return;
  if (isViewingHistoryTurn()) {
    badge.hidden = false;
    badge.textContent = `第 ${getDisplayTurnIndex() + 1} 轮（回看）`;
  } else {
    badge.hidden = true;
    badge.textContent = '';
  }
}

function renderTreeSnapshotBadge() {
  const badge = document.getElementById('planTreeSnapshotBadge');
  if (!badge) return;
  if (isViewingHistoryTurn()) {
    badge.hidden = false;
    badge.textContent = `第 ${getDisplayTurnIndex() + 1} 轮快照`;
  } else {
    badge.hidden = true;
    badge.textContent = '';
  }
}

function renderTreeNode(node, depth = 0) {
  const status = node.status || 'open';
  const statusLabel = STATUS_LABELS[status] || status;
  const children = node.children || [];
  const summaryHtml = node.summary
    ? `<p class="plan-tree-node-summary">${escapeHtml(node.summary)}</p>`
    : '';
  const childrenHtml = children.length
    ? `<div class="plan-tree-children">${children.map((c) => renderTreeNode(c, depth + 1)).join('')}</div>`
    : '';

  return `
    <details class="plan-tree-details plan-tree-node plan-tree-node--${escapeHtml(status)}" open>
      <summary class="plan-tree-node-head">
        <span class="plan-tree-node-title">${escapeHtml(node.title || node.id || '节点')}</span>
        <span class="plan-tree-node-status">${escapeHtml(statusLabel)}</span>
      </summary>
      ${summaryHtml}
      ${childrenHtml}
    </details>
  `;
}

function renderTreeList() {
  if (!els.tree) return;
  const tree = getDisplayBlueprintTree();
  if (!tree.length) {
    els.tree.innerHTML = '<p class="plan-empty">方案树尚未生成</p>';
    return;
  }
  els.tree.innerHTML = tree.map((node) => renderTreeNode(node)).join('');
}

function renderTreeGraph() {
  if (!els.treeSvg || !window.d3) return;
  treeGraphCleanup?.();
  const tree = getDisplayBlueprintTree();
  if (!tree.length) return;
  treeGraphCleanup = renderPlanTreeGraph(els.treeSvg, tree, {
    onNodeClick: (node, pos) => {
      if (!node.summary || !pos) return;
      showPlanNodePopover(els.treeGraphWrap || els.treeSvg, {
        x: pos.x,
        y: pos.y,
        title: node.title || node.id,
        summary: node.summary,
      });
    },
  });
}

function setTreeView(mode) {
  treeViewMode = mode;
  els.treeViewList?.classList.toggle('is-active', mode === 'list');
  els.treeViewGraph?.classList.toggle('is-active', mode === 'graph');
  if (els.tree) els.tree.hidden = mode !== 'list';
  if (els.treeGraphWrap) els.treeGraphWrap.hidden = mode !== 'graph';
  if (mode === 'list') renderTreeList();
  else renderTreeGraph();
}

function renderTree() {
  renderTreeSnapshotBadge();
  if (treeViewMode === 'graph') renderTreeGraph();
  else renderTreeList();
}

function renderReadiness(hint, processing) {
  if (!els.readiness) return;
  if (!hint.message) {
    els.readiness.hidden = true;
    els.readiness.innerHTML = '';
    return;
  }
  els.readiness.hidden = false;
  els.readiness.classList.toggle('plan-readiness--ok', !!hint.sufficient);

  const showCta = hint.sufficient && !processing && planData?.status !== 'handed_off' && !adminViewMode;
  els.readiness.innerHTML = `
    <span class="plan-readiness-text">${escapeHtml(hint.message)}</span>
    ${showCta ? '<button type="button" class="btn btn-primary btn-sm plan-readiness-cta" id="planReadinessCta">结束规划并编写</button>' : ''}
  `;
  if (showCta) {
    document.getElementById('planReadinessCta')?.addEventListener('click', openFinalizeFlow);
  }
}

function renderCurrentTurn() {
  if (streamingActive) return;

  renderCurrentTurnBadge();

  const turnIdx = getDisplayTurnIndex();
  const turn = getDisplayTurn();
  const processing = !!planData?.processing;
  const editable = turnIdx >= 0 && isTurnEditable(turnIdx);

  if (!turn) {
    if (els.message) {
      els.message.textContent = needsFirstTurnRetry()
        ? (planData?.last_error ? '规划 LLM 调用失败，请重试或检查后台配置。' : '首轮问题生成未完成，请点击下方重试。')
        : planData?.status === 'awaiting_l1'
          ? '请先完成知识水平问卷。'
          : '正在准备首轮问题…';
    }
    if (els.questions) {
      if (needsFirstTurnRetry()) {
        els.questions.innerHTML = `
          <div class="plan-retry-banner">
            <p class="plan-retry-banner-text">${escapeHtml(getFirstTurnRetryMessage())}</p>
            <button type="button" class="btn btn-outline btn-sm" id="planRetryFirstBtn">重新生成首轮问题</button>
          </div>`;
        document.getElementById('planRetryFirstBtn')?.addEventListener('click', handleRetryFirstTurn);
      } else {
        els.questions.innerHTML = '';
      }
    }
    if (els.readiness) {
      els.readiness.hidden = true;
      els.readiness.innerHTML = '';
    }
    return;
  }

  if (els.message) els.message.textContent = turn.assistant_message || '';

  const latestDifficulty = turn.difficulty || {};
  if (els.difficulty) {
    const level = DIFFICULTY_LABELS[latestDifficulty.level] || latestDifficulty.level || '—';
    els.difficulty.textContent = `难度：${level}`;
    els.difficulty.title = latestDifficulty.reason || '';
  }

  renderReadiness(turn.readiness_hint || {}, processing || streamingActive || isViewingHistoryTurn());

  if (els.questions) {
    const formOpts = {
      disabled: !editable || adminViewMode,
      openQuestions: turn.open_questions || [],
      prefill: turn.user_reply || undefined,
    };
    if (editable && !adminViewMode) {
      formOpts.onSubmit = handleSubmitTurn;
      formOpts.onRegenerateTurn = handleRegenerateTurn;
      formOpts.onRegenerateQuestion = handleRegenerateQuestion;
    }
    renderPlanQuestionForm(els.questions, turn.questions || [], formOpts);
  }
}

function renderContextPanel() {
  if (!els.contextBody) return;
  const ctx = planData?.context || {};
  const reqs = ctx.requirements || [];
  const refs = ctx.reference_mods?.manual || [];
  const reqHtml = reqs.length
    ? reqs.map((r) => `<li>${escapeHtml(r.title || r.id)}</li>`).join('')
    : '<li class="plan-empty-inline">无</li>';
  const refHtml = refs.length
    ? refs.map((m) => `<li>${escapeHtml(m.title || m.slug)} (${escapeHtml(m.reference_type || '')})</li>`).join('')
    : '<li class="plan-empty-inline">无</li>';
  els.contextBody.innerHTML = `
    <p class="plan-footer-heading">已选要求 / 参考模组</p>
    <p><strong>要求</strong></p><ul class="plan-context-list">${reqHtml}</ul>
    <p><strong>参考模组</strong></p><ul class="plan-context-list">${refHtml}</ul>
  `;
}

function formatSourceReadLabel(step) {
  const tool = step.tool || '';
  const path = step.path || '';
  const labels = {
    read_reference_file: '读取源码',
    search_reference: '搜索源码',
    list_reference_files: '列出文件',
    get_reference_index: '查看索引',
    finish_reading: '完成阅读',
  };
  const base = labels[tool] || tool || '阅读参考';
  return path ? `${base}: ${path}` : base;
}

function formatRefIndexSummary(meta) {
  const st = meta.status || 'pending';
  const fk = meta.failure_kind || '';
  if (st === 'failed') {
    if (fk === 'missing_tools') return '缺少反编译工具（将自动下载或请检查网络）';
    if (fk === 'missing_java') return '未检测到 Java 17+';
    if (fk === 'decompile_error' && (meta.error || '').includes('0 个文件')) {
      return '反编译未产生源码，请重试索引';
    }
    return meta.error || '索引失败';
  }
  if (st === 'indexing' || st === 'pending') return '索引中…';
  const kind = meta.source_kind || '';
  if (kind === 'metadata_only') {
    return '仅元数据（反编译未成功，已生成补救 Card）';
  }
  const parts = [];
  if (kind === 'decompiled') parts.push('反编译+Yarn');
  else if (kind === 'decompiled_obfuscated') parts.push('反编译（混淆）');
  else if (kind === 'git') parts.push('开源 clone');
  const n = meta.file_count;
  const eps = (meta.entry_points || []).slice(0, 2);
  if (n != null) parts.push(`${n} 个文件`);
  if (eps.length) parts.push(`入口 ${eps.join('、')}`);
  return parts.join(' · ') || '已就绪';
}

function renderReferencePanel() {
  if (!els.referenceBody) return;
  const index = planData?.reference_index || {};
  const findings = planData?.research_findings || [];
  const pids = Object.keys(index);
  if (!pids.length && !findings.length) {
    els.referenceBody.innerHTML = '<p class="plan-footer-heading">参考源码索引</p><p class="plan-empty">无代码参考或未启用索引</p>';
    return;
  }
  let html = '<p class="plan-footer-heading">参考源码索引</p>';
  if (pids.length) {
    html += '<ul class="plan-context-list plan-ref-index-list">';
    pids.forEach((pid) => {
      const meta = index[pid] || {};
      const st = meta.status || 'pending';
      const summary = formatRefIndexSummary(meta);
      html += `<li class="plan-ref-index-line plan-ref-index-line--${escapeHtml(st)}">`
        + `<span class="plan-ref-index-title">${escapeHtml(meta.title || pid)}</span>`
        + `<span class="plan-ref-index-sep"> — </span>`
        + `<span class="plan-ref-index-summary">${escapeHtml(summary)}</span>`;
      if (st === 'failed' && activePlanId) {
        html += ` <button type="button" class="btn btn-outline btn-sm plan-ref-retry-btn" data-ref-retry="${escapeHtml(pid)}">重试索引</button>`;
      }
      html += '</li>';
    });
    html += '</ul>';
  }
  if (findings.length) {
    html += '<p><strong>已检索片段</strong></p><ul class="plan-context-list">';
    findings.slice(-5).forEach((f) => {
      html += `<li>${escapeHtml(f.query || '')}: ${escapeHtml((f.snippet_preview || '').slice(0, 120))}…</li>`;
    });
    html += '</ul>';
  }
  els.referenceBody.innerHTML = html;
  if (adminViewMode) return;
  els.referenceBody.querySelectorAll('[data-ref-retry]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const pid = btn.getAttribute('data-ref-retry');
      if (!pid || !activePlanId) return;
      btn.disabled = true;
      try {
        await retryPlanReference(activePlanId, pid);
        showToast('正在重试参考索引…', 'info');
      } catch (err) {
        showToast(err.message || '重试失败', 'error');
        btn.disabled = false;
      }
    });
  });
}

function renderAll() {
  if (els.title) els.title.textContent = planData?.task_title || '规划模式';
  if (els.processing) els.processing.hidden = !planData?.processing && !streamingActive;
  renderHistory();
  renderTree();
  renderContextPanel();
  renderReferencePanel();
  renderCurrentTurn();
  syncAdminViewUi();
  syncFinalizeButtonState();
}

async function loadPlan(id) {
  planData = await fetchPlanSnapshot(id);
  renderAll();
  if (!adminViewMode && planData?.status === 'awaiting_l1') openL1Modal();
}

function applySnapshot(data) {
  endStreaming();
  const prevTurnCount = (planData?.turns || []).length;
  planData = data;
  if ((data.turns || []).length > prevTurnCount) {
    selectedTurnIndex = -1;
  }
  if ((data.turns || []).length) expectingFirstTurn = false;
  lastSnapshotAt = Date.now();
  renderAll();
  upsertSessionListEntry({
    sessionId: data.plan_id,
    taskTitle: data.task_title,
    mode: 'plan',
    status: data.status,
    createdAt: Date.parse(data.created_at) || Date.now(),
  });
}

function applySnapshotIfReady(data) {
  if (shouldApplyIdleSnapshot(data)) {
    applySnapshot(data);
    return;
  }
  if (data.processing) {
    planData = data;
    if (els.processing) els.processing.hidden = false;
    renderTree();
    patchReferenceFieldsFromSnapshot(data);
    return;
  }
  patchReferenceFieldsFromSnapshot(data);
}

function attachPlanEvents(id) {
  if (typeof planEventsCleanup === 'function') planEventsCleanup();
  const cleanup = connectPlanEvents(id, (event) => {
    if (event.type === 'snapshot' && event.data) {
      applySnapshotIfReady(event.data);
    } else if (event.type === 'processing') {
      if (planData) planData.processing = true;
      const stage = event.data?.stage;
      if (stage === 'finalize') {
        beginFinalizeStream();
      } else if (stage === 'waiting_refs') {
        if (shouldShowReferenceIndexStream() && !questionStreamPanel && els.questions) {
          beginReferenceIndexStream(id);
        }
      } else if (stage === 'generating_first_turn') {
        if (shouldUseQuestionsStreamPanel()) {
          if (!questionStreamPanel && els.questions) {
            beginQuestionStream('已跳过索引等待，正在生成首轮问题…');
          } else {
            questionStreamPanel?.switchToJson?.();
            questionStreamPanel?.setLabel?.('已跳过索引等待，正在生成首轮问题…');
          }
        }
      } else if (shouldUseQuestionsStreamPanel() && !questionStreamPanel && els.questions) {
        beginQuestionStream(
          stage === 'regenerate_question' ? '正在重新生成选项…'
            : stage === 'regenerate_turn' ? '正在重新生成本轮问题…'
              : 'LLM 原始输出（JSON 流）',
        );
      } else if (stage !== 'waiting_refs') {
        questionStreamPanel?.switchToJson?.();
      }
      if (els.processing) els.processing.hidden = false;
    } else if (event.type === 'reference_wait_skipped') {
      if (planData) {
        planData.reference_wait_skipped = true;
        planData.processing = true;
      }
      if (!questionStreamPanel && els.questions) {
        beginQuestionStream('已跳过索引等待，正在生成首轮问题…');
      } else {
        questionStreamPanel?.switchToJson?.();
        questionStreamPanel?.setLabel?.('已跳过索引等待，正在生成首轮问题…');
      }
      if (els.processing) els.processing.hidden = false;
    } else if (event.type === 'reference_step') {
      if (shouldShowReferenceIndexStream()) {
        if (!questionStreamPanel && els.questions) beginReferenceIndexStream(id);
        questionStreamPanel?.appendToolStep?.(event.data || {});
      }
    } else if (event.type === 'source_read_step') {
      const step = event.data || {};
      const readLine = `[阅读] ${formatSourceReadLabel(step)}`;
      if (finalizeStreamPanel) {
        finalizeStreamPanel.append(`${readLine}\n`);
      } else if (shouldUseQuestionsStreamPanel()) {
        if (!questionStreamPanel && els.questions) {
          beginQuestionStream('正在阅读参考源码…', { mode: 'tools', showSkip: false });
        }
        questionStreamPanel?.appendToolStep?.({
          project_id: step.project_id,
          step: step.tool || 'read',
          label: readLine,
          status: step.status || 'running',
          preview: step.preview || step.path || '',
        });
      } else if (hasUnsubmittedCurrentTurn()) {
        recoverQuestionsPanelIfStuck();
      }
    } else if (event.type === 'llm_delta') {
      questionStreamPanel?.switchToJson?.();
      appendStreamDelta(event.data?.text || '');
    } else if (event.type === 'finalize_delta') {
      if (!finalizeStreamPanel && els.finalizePreview) beginFinalizeStream();
      appendStreamDelta(event.data?.text || '');
    } else if (event.type === 'error') {
      const msg = event.data?.message || '处理出错';
      const stageWasFinalize = finalizeStreamPanel != null;
      endStreaming();
      expectingFirstTurn = false;
      showToast(msg, 'error');
      if (planData) {
        planData.processing = false;
        planData.last_error = msg;
      }
      if (stageWasFinalize && els.finalizePreview) {
        els.finalizePreview.innerHTML = `
          <div class="plan-retry-banner">
            <p class="plan-retry-banner-text">${escapeHtml(msg)}</p>
            <button type="button" class="btn btn-outline btn-sm" id="planRetryFinalizeBtn">重试定稿</button>
          </div>`;
        document.getElementById('planRetryFinalizeBtn')?.addEventListener('click', openFinalizeFlow);
      }
      renderAll();
    } else if (event.type === 'turn_ready') {
      expectingFirstTurn = false;
      if (planData) planData.processing = false;
      if (Date.now() - lastSnapshotAt < 800) {
        renderAll();
        return;
      }
      fetchPlanSnapshot(id).then(applySnapshot).catch((err) => showToast(err.message, 'error'));
    } else if (event.type === 'reference_indexing') {
      if (planData && event.data?.project_id) {
        const pid = event.data.project_id;
        planData.reference_index = planData.reference_index || {};
        planData.reference_index[pid] = {
          ...(planData.reference_index[pid] || {}),
          status: 'indexing',
          project_id: pid,
        };
      }
      if (shouldShowReferenceIndexStream()) {
        renderContextPanel();
        renderReferencePanel();
      } else {
        patchReferenceFieldsFromSnapshot(planData);
      }
    } else if (event.type === 'reference_ready' || event.type === 'reference_failed' || event.type === 'research_finding') {
      if (event.type === 'reference_failed' && shouldShowReferenceIndexStream()) {
        showToast('参考源码索引失败，规划仍可继续', 'info');
      }
      if (hasUnsubmittedCurrentTurn(planData)) {
        fetchPlanSnapshot(id)
          .then((data) => handleReferenceBackgroundUpdate(data))
          .catch((err) => showToast(err.message, 'error'));
      } else {
        fetchPlanSnapshot(id)
          .then((data) => applySnapshotIfReady(data))
          .catch((err) => showToast(err.message, 'error'));
      }
    } else if (event.type === 'finalize_ready') {
      endStreaming();
      if (planData) planData.processing = false;
      fetchPlanSnapshot(id).then((data) => {
        applySnapshot(data);
        if (els.finalizePreview && data.final_markdown) {
          els.finalizePreview.innerHTML = renderMarkdown(data.final_markdown);
        }
      }).catch((err) => {
        showToast(err.message, 'error');
        renderAll();
      });
    }
  });
  planEventsCleanup = typeof cleanup === 'function' ? cleanup : null;
}

async function handleSubmitTurn(body) {
  beginQuestionStream('正在分析您的回答…');
  try {
    const updated = await submitPlanTurn(activePlanId, body);
    planData = updated;
    renderHistory();
    syncFinalizeButtonState();
  } catch (err) {
    endStreaming();
    showToast(err.message || '提交失败', 'error');
    if (planData) planData.processing = false;
    renderAll();
  }
}

async function handleRetryFirstTurn() {
  expectingFirstTurn = true;
  if (planData) planData.last_error = null;
  beginQuestionStream('正在重新生成首轮问题…');
  try {
    planData = await retryPlanFirstTurn(activePlanId);
    if (planData) planData.processing = true;
  } catch (err) {
    expectingFirstTurn = false;
    endStreaming();
    showToast(err.message || '重试失败', 'error');
    renderAll();
  }
}

async function handleRegenerateTurn(payload = {}) {
  beginQuestionStream('正在重新生成本轮问题…');
  try {
    planData = await regeneratePlanTurn(activePlanId, payload);
  } catch (err) {
    endStreaming();
    showToast(err.message || '重新生成失败', 'error');
    renderAll();
  }
}

async function handleRegenerateQuestion(questionId, action) {
  beginQuestionStream(action === 'expand' ? '正在扩增选项…' : '正在重新生成选项…');
  try {
    planData = await regeneratePlanQuestion(activePlanId, questionId, { action });
  } catch (err) {
    endStreaming();
    showToast(err.message || '重新生成失败', 'error');
    renderAll();
  }
}

function openL1Modal() {
  if (!els.l1Overlay) return;
  renderL1Form(els.l1Body, { values: planData?.context?.knowledge_l1 });
  const mustComplete = planData?.status === 'awaiting_l1';
  if (els.l1Cancel) els.l1Cancel.hidden = mustComplete;
  els.l1Overlay.hidden = false;
  document.body.style.overflow = 'hidden';
}

function closeL1Modal() {
  if (!els.l1Overlay) return;
  els.l1Overlay.hidden = true;
  document.body.style.overflow = '';
}

async function saveL1() {
  const form = els.l1Body?.querySelector('#planL1Form');
  const l1 = collectL1FromForm(form);
  try {
    const updated = await updatePlanKnowledgeL1(activePlanId, l1);
    applySnapshot(updated);
    closeL1Modal();
    showToast('知识水平已更新', 'success');
  } catch (err) {
    showToast(err.message || '保存失败', 'error');
  }
}

async function regenerateFinalize() {
  try {
    if (els.finalizePreview) els.finalizePreview.innerHTML = '';
    planData = { ...planData, final_markdown: null, processing: true };
    beginFinalizeStream();
    await finalizePlan(activePlanId);
    if (els.processing) els.processing.hidden = false;
    syncFinalizeButtonState();
  } catch (err) {
    endStreaming();
    showToast(err.message || '重新生成失败', 'error');
    if (planData) {
      planData.processing = false;
      planData.last_error = err.message || '重新生成失败';
    }
    if (els.finalizePreview) {
      els.finalizePreview.innerHTML = `
        <div class="plan-retry-banner">
          <p class="plan-retry-banner-text">${escapeHtml(planData?.last_error || '重新生成失败')}</p>
          <button type="button" class="btn btn-outline btn-sm" id="planRetryFinalizeBtn">重试定稿</button>
        </div>`;
      document.getElementById('planRetryFinalizeBtn')?.addEventListener('click', regenerateFinalize);
    }
    renderAll();
  }
}

async function openFinalizeFlow() {
  try {
    syncFinalizeModalActions();
    if (els.finalizeOverlay) {
      els.finalizeOverlay.hidden = false;
      document.body.style.overflow = 'hidden';
    }
    if (adminViewMode) {
      if (els.finalizePreview) {
        els.finalizePreview.innerHTML = renderMarkdown(planData?.final_markdown || '（暂无定稿内容）');
      }
      syncFinalizeModalActions();
      return;
    }
    if (planData?.status === 'handed_off') {
      if (els.finalizePreview) {
        els.finalizePreview.innerHTML = renderMarkdown(planData?.final_markdown || '（暂无定稿内容）');
      }
      syncFinalizeModalActions();
      return;
    }
    if (!planData?.final_markdown) {
      beginFinalizeStream();
      await finalizePlan(activePlanId);
      planData = { ...planData, processing: true };
      if (els.processing) els.processing.hidden = false;
      syncFinalizeButtonState();
    } else if (els.finalizePreview) {
      els.finalizePreview.innerHTML = renderMarkdown(planData.final_markdown || '');
    }
  } catch (err) {
    endStreaming();
    showToast(err.message || '定稿失败', 'error');
    if (planData) {
      planData.processing = false;
      planData.last_error = err.message || '定稿失败';
    }
    if (els.finalizePreview) {
      els.finalizePreview.innerHTML = `
        <div class="plan-retry-banner">
          <p class="plan-retry-banner-text">${escapeHtml(planData?.last_error || '定稿失败')}</p>
          <button type="button" class="btn btn-outline btn-sm" id="planRetryFinalizeBtn">重试定稿</button>
        </div>`;
      document.getElementById('planRetryFinalizeBtn')?.addEventListener('click', openFinalizeFlow);
    }
    renderAll();
  }
}

function closeFinalizeModal() {
  if (els.finalizeOverlay) els.finalizeOverlay.hidden = true;
  document.body.style.overflow = '';
}

async function confirmHandoff() {
  try {
    if (els.finalizeConfirm) els.finalizeConfirm.disabled = true;
    const result = await handoffPlan(activePlanId);
    showToast('正在进入编写…', 'success');
    window.location.href = result.redirect_url || `/session.html?session_id=${result.session_id}`;
  } catch (err) {
    showToast(err.message || '移交失败', 'error');
    if (els.finalizeConfirm) els.finalizeConfirm.disabled = false;
  }
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
  if (open) renderSessionListPanel(true);
}

function bindUi() {
  els.l1Cancel?.addEventListener('click', closeL1Modal);
  els.l1Save?.addEventListener('click', saveL1);
  els.finalizeBtn?.addEventListener('click', openFinalizeFlow);
  els.finalizeCancel?.addEventListener('click', closeFinalizeModal);
  els.finalizeConfirm?.addEventListener('click', confirmHandoff);
  els.finalizeRegenerate?.addEventListener('click', regenerateFinalize);

  els.l1Overlay?.addEventListener('click', (e) => {
    if (e.target === els.l1Overlay && planData?.status !== 'awaiting_l1') closeL1Modal();
  });
  els.finalizeOverlay?.addEventListener('click', (e) => {
    if (e.target === els.finalizeOverlay) closeFinalizeModal();
  });

  window.addEventListener('plan-toast', (e) => {
    showToast(e.detail?.message, e.detail?.type);
  });

  els.treeViewList?.addEventListener('click', () => setTreeView('list'));
  els.treeViewGraph?.addEventListener('click', () => setTreeView('graph'));

  window.addEventListener('resize', () => {
    if (treeViewMode === 'graph') renderTreeGraph();
  });

  document.getElementById('historyToggle')?.addEventListener('click', () => {
    const appLayout = document.getElementById('appLayout');
    const opening = !appLayout?.classList.contains('app-layout--history-open');
    syncHistory(opening);
  });
  document.getElementById('historyBackdrop')?.addEventListener('click', () => syncHistory(false));
}

function readBootstrap() {
  try {
    const raw = sessionStorage.getItem(PLAN_STORAGE_BOOTSTRAP);
    if (!raw) return null;
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function initSessionPanel(sessionId) {
  initSessionListPanel({
    bodyEl: document.getElementById('historyPanelBody'),
    titleEl: document.getElementById('historyPanelTitle'),
    footerEl: document.getElementById('historyRecycleBtn'),
    newConceptBtnEl: document.getElementById('conceptNewBtn'),
    sessionId,
    showToast,
    includeConcepts: true,
    onSessionNavigate: (item) => {
      const id = item?.sessionId || item;
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

function showBootstrapCreateRetry(message) {
  endStreaming();
  if (els.title) els.title.textContent = '规划创建失败';
  if (els.message) els.message.textContent = message || '创建规划失败，请重试。';
  if (els.processing) els.processing.hidden = true;
  if (els.questions) {
    els.questions.innerHTML = `
      <div class="plan-retry-banner">
        <p class="plan-retry-banner-text">${escapeHtml(message || '创建规划失败，请重试。')}</p>
        <button type="button" class="btn btn-outline btn-sm" id="planRetryCreateBtn">重试创建规划</button>
      </div>`;
    document.getElementById('planRetryCreateBtn')?.addEventListener('click', () => {
      createFromBootstrap();
    });
  }
}

async function createFromBootstrap() {
  const bootstrap = readBootstrap();
  if (!bootstrap) {
    showToast('缺少规划创建数据', 'error');
    window.location.href = '/';
    return;
  }

  if (els.title) els.title.textContent = bootstrap.task_title || '正在创建规划…';
  if (els.message) els.message.textContent = '正在创建规划并分析构想…';
  const codeRefs = hasCodeReferenceMods(bootstrap.context?.reference_mods?.manual);
  if (codeRefs) {
    beginQuestionStream('正在索引参考模组…', { mode: 'tools', showSkip: false });
  } else {
    beginQuestionStream('正在创建规划并分析构想…');
  }

  try {
    const result = await createPlanSession({
      context: bootstrap.context,
      task_title: bootstrap.task_title,
    });
    sessionStorage.removeItem(PLAN_STORAGE_BOOTSTRAP);
    activePlanId = result.plan_id;
    window.history.replaceState(null, '', `/plan.html?plan_id=${encodeURIComponent(activePlanId)}`);

    upsertSessionListEntry({
      sessionId: result.plan_id,
      taskTitle: result.task_title || bootstrap.task_title,
      mode: 'plan',
      status: result.status || 'active',
      createdAt: Date.parse(result.created_at) || Date.now(),
    });
    renderSessionListPanel(true);
    initSessionPanel(activePlanId);

    expectingFirstTurn = true;
    planData = { ...result, processing: true };
    if (codeRefs || hasCodeReferenceMods(result.context?.reference_mods?.manual)) {
      beginReferenceIndexStream(activePlanId);
    } else {
      beginQuestionStream('LLM 原始输出（JSON 流）');
    }
    renderAll();
    attachPlanEvents(activePlanId);
  } catch (err) {
    showBootstrapCreateRetry(err.message || '创建规划失败，请重试。');
    showToast(err.message || '创建规划失败', 'error');
  }
}

async function init() {
  adminViewMode = isAdminViewMode();
  if (!adminViewMode && !getToken()) {
    window.location.href = `/login.html?next=${encodeURIComponent(window.location.pathname + window.location.search)}`;
    return;
  }
  if (adminViewMode && !adminOwnerId) {
    showToast('管理员查看缺少 owner_id 参数', 'error');
    return;
  }

  bindUi();
  setTreeView('list');
  if (!adminViewMode) {
    await initConceptDrafts();
  }

  if (isCreating) {
    if (adminViewMode) {
      showToast('管理员模式不支持创建规划', 'error');
      return;
    }
    await createFromBootstrap();
    return;
  }

  if (!activePlanId) {
    showToast('缺少 plan_id', 'error');
    return;
  }

  if (!adminViewMode) {
    initSessionPanel(activePlanId);
    renderSessionListPanel(true);
  } else {
    document.getElementById('historyToggle')?.setAttribute('hidden', '');
  }
  try {
    await loadPlan(activePlanId);
    if (!adminViewMode) {
      attachPlanEvents(activePlanId);
      if (isAwaitingFirstTurn(planData)) {
        expectingFirstTurn = true;
        if (isWaitingReferenceIndex(planData) && shouldShowReferenceIndexStream(planData)) {
          beginReferenceIndexStream(activePlanId);
        } else if (planData?.processing) {
          beginQuestionStream('LLM 原始输出（JSON 流）');
        }
      }
    }
    syncAdminViewUi();
  } catch (err) {
    showToast(err.message || '加载失败', 'error');
  }
}

init();
