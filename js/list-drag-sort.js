/**
 * 列表拖动排序（HTML5 DnD，通过左侧手柄触发）
 * @param {HTMLElement} listEl
 * @param {{
 *   itemSelector?: string,
 *   handleSelector?: string,
 *   getItemId: (el: HTMLElement) => string,
 *   onReorder: (ids: string[]) => void,
 * }} options
 */
export function attachDragSort(listEl, options) {
  if (!listEl || listEl.dataset.dragSortBound === '1') return;

  const itemSelector = options.itemSelector || '.modal-req-item';
  const handleSelector = options.handleSelector || '.list-drag-handle';
  let draggedItem = null;

  listEl.dataset.dragSortBound = '1';

  listEl.addEventListener('dragstart', (e) => {
    const handle = e.target.closest(handleSelector);
    if (!handle || !listEl.contains(handle)) return;
    const item = handle.closest(itemSelector);
    if (!item) return;
    draggedItem = item;
    item.classList.add('is-dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', options.getItemId(item));
  });

  listEl.addEventListener('dragend', (e) => {
    const handle = e.target.closest(handleSelector);
    if (!handle) return;
    const item = handle.closest(itemSelector);
    if (item) item.classList.remove('is-dragging');
    draggedItem = null;
    listEl.querySelectorAll(itemSelector).forEach((el) => {
      el.classList.remove('is-drag-over-before', 'is-drag-over-after');
    });
  });

  listEl.addEventListener('dragover', (e) => {
    if (!draggedItem) return;
    e.preventDefault();
    const target = e.target.closest(itemSelector);
    if (!target || target === draggedItem) return;
    const rect = target.getBoundingClientRect();
    const before = e.clientY < rect.top + rect.height / 2;
    listEl.querySelectorAll(itemSelector).forEach((el) => {
      el.classList.remove('is-drag-over-before', 'is-drag-over-after');
    });
    target.classList.add(before ? 'is-drag-over-before' : 'is-drag-over-after');
  });

  listEl.addEventListener('dragleave', (e) => {
    const target = e.target.closest(itemSelector);
    if (target && !listEl.contains(e.relatedTarget)) {
      target.classList.remove('is-drag-over-before', 'is-drag-over-after');
    }
  });

  listEl.addEventListener('drop', (e) => {
    e.preventDefault();
    if (!draggedItem) return;
    const target = e.target.closest(itemSelector);
    if (!target || target === draggedItem) return;

    const rect = target.getBoundingClientRect();
    const before = e.clientY < rect.top + rect.height / 2;
    if (before) {
      listEl.insertBefore(draggedItem, target);
    } else {
      listEl.insertBefore(draggedItem, target.nextSibling);
    }

    listEl.querySelectorAll(itemSelector).forEach((el) => {
      el.classList.remove('is-drag-over-before', 'is-drag-over-after');
    });

    const ids = [...listEl.querySelectorAll(itemSelector)].map((el) => options.getItemId(el));
    options.onReorder(ids);
  });
}

/** 拖动手柄 SVG */
export const DRAG_HANDLE_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="9" cy="6" r="1.5"/><circle cx="15" cy="6" r="1.5"/><circle cx="9" cy="12" r="1.5"/><circle cx="15" cy="12" r="1.5"/><circle cx="9" cy="18" r="1.5"/><circle cx="15" cy="18" r="1.5"/></svg>';

/**
 * @param {string} [label]
 */
export function createDragHandle(label = '拖动排序') {
  const btn = document.createElement('button');
  btn.type = 'button';
  btn.className = 'list-drag-handle';
  btn.draggable = true;
  btn.setAttribute('aria-label', label);
  btn.innerHTML = DRAG_HANDLE_SVG;
  return btn;
}
