/** @file 侧栏列表项悬停菜单 */

/**
 * @param {HTMLElement} anchor
 * @param {{ label: string, action: () => void | Promise<void> }[]} items
 * @param {HTMLElement} [rowEl]
 */
export function openSidebarItemMenu(anchor, items, rowEl = null) {
  document.querySelectorAll('.sidebar-item-menu').forEach((el) => el.remove());
  rowEl?.classList.add('is-menu-open');

  const menu = document.createElement('div');
  menu.className = 'sidebar-item-menu';
  menu.setAttribute('role', 'menu');

  items.forEach((item) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'sidebar-item-menu-btn';
    btn.textContent = item.label;
    btn.setAttribute('role', 'menuitem');
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      close();
      await item.action();
    });
    menu.appendChild(btn);
  });

  document.body.appendChild(menu);
  const rect = anchor.getBoundingClientRect();
  const menuWidth = menu.offsetWidth || 120;
  menu.style.top = `${rect.bottom + 4}px`;
  menu.style.left = `${Math.max(8, Math.min(rect.right - menuWidth, window.innerWidth - menuWidth - 8))}px`;

  const close = () => {
    menu.remove();
    rowEl?.classList.remove('is-menu-open');
    document.removeEventListener('click', onDocClick);
  };

  const onDocClick = (e) => {
    if (!menu.contains(e.target) && e.target !== anchor) close();
  };

  setTimeout(() => document.addEventListener('click', onDocClick), 0);
}
