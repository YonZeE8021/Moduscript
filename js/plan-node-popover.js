/** @file 方案树导图节点就近气泡 */

/**
 * @param {HTMLElement} _container
 * @param {{ x: number, y: number, title?: string, summary: string }} opts
 * @returns {() => void}
 */
export function showPlanNodePopover(_container, opts) {
  hidePlanNodePopover(_container);

  const pop = document.createElement('div');
  pop.className = 'plan-tree-node-popover';
  pop.setAttribute('role', 'tooltip');

  const titleEl = document.createElement('div');
  titleEl.className = 'plan-tree-node-popover-title';
  titleEl.textContent = opts.title || '节点说明';

  const bodyEl = document.createElement('div');
  bodyEl.className = 'plan-tree-node-popover-body';
  bodyEl.textContent = opts.summary;

  pop.append(titleEl, bodyEl);
  document.body.appendChild(pop);

  const maxW = Math.min(280, window.innerWidth - 24);
  pop.style.maxWidth = `${maxW}px`;

  let left = opts.x + 12;
  let top = opts.y - 8;
  const popRect = pop.getBoundingClientRect();
  if (left + popRect.width > window.innerWidth - 12) {
    left = Math.max(12, window.innerWidth - popRect.width - 12);
  }
  if (top + popRect.height > window.innerHeight - 12) {
    top = Math.max(12, opts.y - popRect.height - 12);
  }
  pop.style.left = `${left}px`;
  pop.style.top = `${top}px`;

  const onDocClick = (e) => {
    if (!pop.contains(e.target)) dismiss();
  };
  const onKey = (e) => {
    if (e.key === 'Escape') dismiss();
  };

  function dismiss() {
    pop.remove();
    document.removeEventListener('click', onDocClick, true);
    document.removeEventListener('keydown', onKey);
    document.body._planNodePopoverCleanup = null;
  }

  document.body._planNodePopoverCleanup = dismiss;
  requestAnimationFrame(() => {
    document.addEventListener('click', onDocClick, true);
    document.addEventListener('keydown', onKey);
  });

  return dismiss;
}

/** @param {HTMLElement} container */
export function hidePlanNodePopover(container) {
  document.body._planNodePopoverCleanup?.();
  container?.closest('.plan-tree-graph-wrap')?._planNodePopoverCleanup?.();
}
