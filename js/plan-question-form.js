/** @file 规划模式结构化问题表单 */

import { normalizeRequirementCost } from './requirement-cost.js';

const REGEN_TEMP_STORAGE_KEY = 'planRegenTemperature';

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

function bindRadioDeselect(field, inputName) {
  field.querySelectorAll(`input[type="radio"][name="${inputName}"]`).forEach((input) => {
    input.addEventListener('click', (e) => {
      if (input.dataset.wasChecked === '1') {
        input.checked = false;
        input.dataset.wasChecked = '0';
        e.preventDefault();
      } else {
        field.querySelectorAll(`input[type="radio"][name="${input.name}"]`).forEach((el) => {
          el.dataset.wasChecked = '0';
        });
        input.dataset.wasChecked = '1';
      }
    });
  });
}

/**
 * @param {HTMLElement} container
 * @param {object[]} questions
 * @param {{
 *   disabled?: boolean,
 *   openQuestions?: string[],
 *   onSubmit?: (data: object) => void | Promise<void>,
 *   onRegenerateTurn?: (payload?: { temperature?: number }) => void | Promise<void>,
 *   onRegenerateQuestion?: (questionId: string, action: string) => void | Promise<void>,
 *   prefill?: { answers?: Record<string, string | string[]>, custom?: Record<string, string>, overall_remarks?: string },
 * }} [opts]
 */
export function renderPlanQuestionForm(container, questions, opts = {}) {
  const disabled = !!opts.disabled;
  const openQuestions = (opts.openQuestions || []).map((q) => String(q || '').trim()).filter(Boolean);
  const hasQuestions = (questions || []).length > 0;
  const hasOpenQuestions = openQuestions.length > 0;
  container.innerHTML = '';

  if (!hasQuestions && !hasOpenQuestions) {
    container.innerHTML = '<p class="plan-no-questions">本轮暂无结构化问题，可在自由回复中补充或直接结束规划。</p>';
    return;
  }

  const form = document.createElement('form');
  form.className = 'plan-question-form';

  (questions || []).forEach((q, qi) => {
    const field = document.createElement('fieldset');
    field.className = 'plan-question-field';
    field.dataset.questionId = q.id || `q${qi}`;

    const qid = q.id || `q${qi}`;
    const why = q.why ? `<p class="plan-question-why">${escapeHtml(q.why)}</p>` : '';
    const multiHint = q.multi_select ? ' <span class="plan-question-multi-hint">（可多选）</span>' : '';
    const regenBtns = !disabled && opts.onRegenerateQuestion
      ? `<span class="plan-question-regen-btns">
          <button type="button" class="btn btn-outline btn-xs plan-q-regen-expand" data-qid="${escapeHtml(qid)}">扩增选项</button>
          <button type="button" class="btn btn-outline btn-xs plan-q-regen-replace" data-qid="${escapeHtml(qid)}">重新生成</button>
        </span>`
      : '';
    field.innerHTML = `
      <legend class="plan-question-prompt">
        <span>${escapeHtml(q.prompt || `问题 ${qi + 1}`)}${multiHint}</span>
        ${regenBtns}
      </legend>
      ${why}
      <div class="plan-question-options"></div>
    `;

    const optionsEl = field.querySelector('.plan-question-options');
    const options = q.options || [];
    const inputType = q.multi_select ? 'checkbox' : 'radio';
    const inputName = q.multi_select ? `pq-${qi}[]` : `pq-${qi}`;

    options.forEach((opt, oi) => {
      const id = `pq-${qi}-${oi}`;
      const cost = normalizeRequirementCost(opt.complexity) || 'medium';
      const row = document.createElement('label');
      row.className = `plan-question-option plan-question-option--${cost}`;
      row.innerHTML = `
        <input type="${inputType}" name="${inputName}" value="${escapeHtml(opt.id || String(oi))}" id="${id}" ${disabled ? 'disabled' : ''}>
        <span class="plan-question-option-body">
          <span class="plan-question-option-label">${escapeHtml(opt.label || '')}</span>
          ${opt.hint ? `<span class="plan-question-option-hint">${escapeHtml(opt.hint)}</span>` : ''}
        </span>
      `;
      optionsEl?.appendChild(row);
    });

    if (!q.multi_select && options.length) {
      bindRadioDeselect(field, inputName);
    }

    if (q.allow_custom) {
      const customWrap = document.createElement('div');
      customWrap.className = 'plan-question-custom';
      customWrap.innerHTML = `
        <input type="text" class="plan-question-custom-input" data-custom-for="${escapeHtml(q.id || `q${qi}`)}"
          placeholder="${escapeHtml(q.custom_placeholder || '自定义说明…')}" ${disabled ? 'disabled' : ''}>
      `;
      optionsEl?.appendChild(customWrap);
    }

    form.appendChild(field);
  });

  if (hasOpenQuestions) {
    const openWrap = document.createElement('details');
    openWrap.className = 'plan-open-questions';
    const items = openQuestions.map((q) => `<li>${escapeHtml(q)}</li>`).join('');
    openWrap.innerHTML = `
      <summary class="plan-open-questions-summary">开放式提问（${openQuestions.length}）</summary>
      <ol class="plan-open-questions-list">${items}</ol>
    `;
    form.appendChild(openWrap);
  }

  const remarksPlaceholder = hasOpenQuestions
    ? '请在此回答上方开放式提问，或补充其他想法、约束与偏好…'
    : '补充本轮未涵盖的想法、约束或偏好…';
  const remarksWrap = document.createElement('div');
  remarksWrap.className = 'plan-overall-remarks';
  remarksWrap.innerHTML = `
    <label class="plan-overall-remarks-label" for="planOverallRemarks">自由回复</label>
    <textarea id="planOverallRemarks" class="plan-overall-remarks-input" rows="2"
      placeholder="${escapeHtml(remarksPlaceholder)}" ${disabled ? 'disabled' : ''}></textarea>
  `;
  form.appendChild(remarksWrap);

  const savedTemp = localStorage.getItem(REGEN_TEMP_STORAGE_KEY) ?? '0.5';
  const actions = document.createElement('div');
  actions.className = 'plan-question-actions';
  const regenTurnBtn = !disabled && opts.onRegenerateTurn
    ? `<div class="plan-question-actions-right">
        <label class="plan-regen-temp">
          <span class="plan-regen-temp-label">温度</span>
          <input type="number" class="plan-regen-temp-input" min="0" max="2" step="0.1" value="${escapeHtml(savedTemp)}" />
        </label>
        <button type="button" class="btn btn-outline plan-regen-turn-btn">重新生成本轮问题</button>
      </div>`
    : '';
  actions.innerHTML = `
    <div class="plan-question-actions-left">
      <button type="submit" class="btn btn-primary" ${disabled ? 'disabled' : ''}>提交本轮回答</button>
      <button type="button" class="btn btn-outline plan-skip-btn" ${disabled ? 'disabled' : ''}>跳过问题，仅自由回复</button>
    </div>
    ${regenTurnBtn}
  `;
  form.appendChild(actions);

  container.appendChild(form);

  const prefill = opts.prefill;
  if (prefill) {
    const answers = prefill.answers || {};
    (questions || []).forEach((q, qi) => {
      const qid = q.id || `q${qi}`;
      const field = form.querySelectorAll('.plan-question-field')[qi];
      if (!field) return;
      const val = answers[qid];
      if (val == null) return;
      const ids = Array.isArray(val) ? val : [val];
      ids.forEach((optId) => {
        const inputs = field.querySelectorAll('input[type="radio"], input[type="checkbox"]');
        inputs.forEach((input) => {
          if (input.value === String(optId)) input.checked = true;
        });
      });
    });
    Object.entries(prefill.custom || {}).forEach(([qid, text]) => {
      const input = form.querySelector(`.plan-question-custom-input[data-custom-for="${qid}"]`);
      if (input && text) input.value = String(text);
    });
    const remarksEl = form.querySelector('#planOverallRemarks');
    if (remarksEl && prefill.overall_remarks) {
      remarksEl.value = prefill.overall_remarks;
    }
  }

  form.querySelector('.plan-regen-turn-btn')?.addEventListener('click', async (e) => {
    e.preventDefault();
    if (disabled || !opts.onRegenerateTurn) return;
    const tempInput = form.querySelector('.plan-regen-temp-input');
    const raw = tempInput?.value;
    const temperature = raw != null && raw !== '' ? Number(raw) : undefined;
    if (temperature != null && !Number.isNaN(temperature)) {
      localStorage.setItem(REGEN_TEMP_STORAGE_KEY, String(temperature));
    }
    const payload = temperature != null && !Number.isNaN(temperature) ? { temperature } : {};
    await opts.onRegenerateTurn(payload);
  });

  form.querySelectorAll('.plan-q-regen-expand').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      if (disabled || !opts.onRegenerateQuestion) return;
      await opts.onRegenerateQuestion(btn.dataset.qid || '', 'expand');
    });
  });

  form.querySelectorAll('.plan-q-regen-replace').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      e.preventDefault();
      if (disabled || !opts.onRegenerateQuestion) return;
      await opts.onRegenerateQuestion(btn.dataset.qid || '', 'replace');
    });
  });

  form.querySelector('.plan-skip-btn')?.addEventListener('click', async (e) => {
    e.preventDefault();
    if (disabled || !opts.onSubmit) return;
    const remarks = form.querySelector('#planOverallRemarks')?.value?.trim() || '';
    await opts.onSubmit({ answers: {}, custom: {}, overall_remarks: remarks, skip_questions: true });
  });

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (disabled || !opts.onSubmit) return;

    const answers = {};
    const custom = {};
    const missingFields = [];

    (questions || []).forEach((q, qi) => {
      const qid = q.id || `q${qi}`;
      const field = form.querySelectorAll('.plan-question-field')[qi];
      const customInput = field?.querySelector('.plan-question-custom-input');
      if (customInput?.value?.trim()) {
        custom[qid] = customInput.value.trim();
      }

      let answered = false;
      if (q.multi_select) {
        const checked = [...(field?.querySelectorAll('input[type="checkbox"]:checked') || [])].map((el) => el.value);
        if (checked.length) {
          answers[qid] = checked;
          answered = true;
        }
      } else {
        const checked = field?.querySelector('input[type="radio"]:checked');
        if (checked) {
          answers[qid] = checked.value;
          answered = true;
        }
      }

      if (!answered && (q.options || []).length && !custom[qid] && field) {
        missingFields.push(field);
      }
    });

    form.querySelectorAll('.plan-question-field--missing').forEach((el) => {
      el.classList.remove('plan-question-field--missing');
    });

    if (missingFields.length) {
      missingFields.forEach((field) => field.classList.add('plan-question-field--missing'));
      const ok = window.confirm(`仍有 ${missingFields.length} 题未选择选项，确定提交？`);
      if (!ok) return;
    }

    const overall_remarks = form.querySelector('#planOverallRemarks')?.value?.trim() || '';
    await opts.onSubmit({ answers, custom, overall_remarks, skip_questions: false });
  });
}
