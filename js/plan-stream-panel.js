/** @file 规划模式 LLM 原始流式输出面板 */

/**
 * @param {HTMLElement} container
 * @param {{ label?: string, mode?: 'json' | 'markdown' | 'tools', showSkip?: boolean, onSkip?: () => void | Promise<void> }} [opts]
 * @returns {{ pre?: HTMLPreElement, append: (text: string) => void, appendToolStep: (step: object) => void, setLabel: (text: string) => void, disableSkip: () => void, switchToJson: () => void, destroy: () => void }}
 */
export function showStreamPanel(container, opts = {}) {
  container.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'plan-stream-wrap';

  const header = document.createElement('div');
  header.className = 'plan-stream-header';

  const label = document.createElement('div');
  label.className = 'plan-stream-label';
  label.textContent = opts.label || (
    opts.mode === 'markdown' ? 'LLM 正在生成 Markdown…'
      : opts.mode === 'tools' ? '正在索引参考模组…'
        : 'LLM 原始输出（JSON 流）'
  );

  header.appendChild(label);

  let skipBtn = null;
  if (opts.mode === 'tools' && opts.showSkip !== false) {
    skipBtn = document.createElement('button');
    skipBtn.type = 'button';
    skipBtn.className = 'btn btn-outline btn-sm plan-ref-skip-btn';
    skipBtn.textContent = '暂时跳过索引，先进行首轮规划';
    skipBtn.addEventListener('click', async () => {
      if (skipBtn?.disabled) return;
      skipBtn.disabled = true;
      try {
        await opts.onSkip?.();
      } catch {
        skipBtn.disabled = false;
      }
    });
    header.appendChild(skipBtn);
  }

  wrap.appendChild(header);

  let pre = null;
  let toolsRoot = null;
  const stepEls = new Map();

  if (opts.mode === 'tools') {
    toolsRoot = document.createElement('div');
    toolsRoot.className = 'plan-ref-tools-list';
    wrap.appendChild(toolsRoot);
  } else {
    pre = document.createElement('pre');
    pre.className = 'plan-llm-stream';
    pre.setAttribute('aria-live', 'polite');
    wrap.appendChild(pre);
  }

  container.appendChild(wrap);

  function statusIcon(status) {
    if (status === 'ok') return '✓';
    if (status === 'failed') return '✗';
    if (status === 'skipped') return '−';
    return '…';
  }

  return {
    pre: pre || undefined,
    append(text) {
      if (!pre) return;
      pre.textContent += text;
      pre.scrollTop = pre.scrollHeight;
    },
    appendToolStep(step) {
      if (!toolsRoot) return;
      const key = `${step.project_id || ''}:${step.step || step.label || ''}`;
      let card = stepEls.get(key);
      if (!card) {
        card = document.createElement('div');
        card.className = 'session-tool-card plan-ref-tool-card';
        card.innerHTML = `
          <span class="session-tool-name plan-ref-tool-name"></span>
          <pre class="session-mono session-tool-preview plan-ref-tool-preview"></pre>`;
        toolsRoot.appendChild(card);
        stepEls.set(key, card);
      }
      const st = step.status || 'running';
      card.className = `session-tool-card plan-ref-tool-card plan-ref-tool-card--${st}`;
      const nameEl = card.querySelector('.plan-ref-tool-name');
      const previewEl = card.querySelector('.plan-ref-tool-preview');
      if (nameEl) {
        nameEl.textContent = `${statusIcon(st)} ${step.label || step.step || '步骤'}`;
      }
      const preview = (step.preview || '').trim();
      if (previewEl) {
        previewEl.textContent = preview;
        previewEl.hidden = !preview;
      }
      toolsRoot.scrollTop = toolsRoot.scrollHeight;
    },
    setLabel(text) {
      label.textContent = text;
    },
    disableSkip() {
      if (skipBtn) {
        skipBtn.disabled = true;
        skipBtn.textContent = '已跳过索引等待';
      }
    },
    switchToJson() {
      if (pre) return;
      if (toolsRoot) {
        toolsRoot.classList.add('plan-ref-tools-list--dimmed');
      }
      pre = document.createElement('pre');
      pre.className = 'plan-llm-stream';
      pre.setAttribute('aria-live', 'polite');
      wrap.appendChild(pre);
    },
    destroy() {
      wrap.remove();
    },
  };
}

/** @param {HTMLElement} container */
export function hideStreamPanel(container) {
  container.innerHTML = '';
}
