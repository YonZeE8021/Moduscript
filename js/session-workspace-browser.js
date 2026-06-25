/** @file 会话工作区只读文件浏览弹窗 */

import {
  downloadWorkspaceArchive,
  downloadWorkspaceFile,
  fetchWorkspaceFile,
  listWorkspaceEntries,
} from './session-api.js';

let overlayEl = null;
let state = {
  sessionId: null,
  projectPath: '',
  currentPath: '.',
  selectedEntry: null,
  entries: [],
  showToast: null,
};

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

function normalizePath(path) {
  const p = (path || '').trim().replace(/\\/g, '/').replace(/^\/+/, '');
  return p || '.';
}

function joinPath(base, name) {
  if (!base || base === '.') return name;
  return `${base.replace(/\/$/, '')}/${name}`;
}

function parentPath(path) {
  const p = normalizePath(path);
  if (p === '.' || !p.includes('/')) return '.';
  return p.replace(/\/[^/]+$/, '') || '.';
}

function triggerBlobDownload(name, blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

function renderBreadcrumb() {
  const el = overlayEl?.querySelector('#workspaceBreadcrumb');
  if (!el) return;
  const parts =
    state.currentPath === '.'
      ? []
      : state.currentPath.split('/').filter(Boolean);
  const crumbs = [{ label: '根目录', path: '.' }];
  let acc = '';
  for (const part of parts) {
    acc = acc ? `${acc}/${part}` : part;
    crumbs.push({ label: part, path: acc });
  }
  el.innerHTML = crumbs
    .map(
      (c, i) =>
        `<button type="button" class="workspace-breadcrumb-item" data-path="${escapeHtml(c.path)}"${i === crumbs.length - 1 ? ' aria-current="page"' : ''}>${escapeHtml(c.label)}</button>`,
    )
    .join('<span class="workspace-breadcrumb-sep" aria-hidden="true">/</span>');
  el.querySelectorAll('[data-path]').forEach((btn) => {
    btn.addEventListener('click', () => navigateTo(btn.dataset.path || '.'));
  });
}

function renderFileList() {
  const listEl = overlayEl?.querySelector('#workspaceFileList');
  const metaEl = overlayEl?.querySelector('#workspaceListMeta');
  if (!listEl) return;

  if (!state.entries.length) {
    listEl.innerHTML = '<p class="workspace-empty">此目录为空</p>';
    if (metaEl) metaEl.textContent = normalizePath(state.currentPath);
    return;
  }

  const rows = state.entries.map((entry) => {
    const icon = entry.type === 'dir' ? '📁' : '📄';
    const size =
      entry.type === 'file' && entry.size != null
        ? `${Math.round(entry.size / 1024)} KB`
        : '—';
    const selected =
      state.selectedEntry?.name === entry.name ? ' workspace-file-row--selected' : '';
    return `<button type="button" class="workspace-file-row${selected}" data-name="${escapeHtml(entry.name)}" data-type="${entry.type}">
      <span class="workspace-file-row-icon" aria-hidden="true">${icon}</span>
      <span class="workspace-file-row-name">${escapeHtml(entry.name)}</span>
      <span class="workspace-file-row-size">${escapeHtml(size)}</span>
    </button>`;
  });
  listEl.innerHTML = rows.join('');
  if (metaEl) metaEl.textContent = `${normalizePath(state.currentPath)} · ${state.entries.length} 项`;

  listEl.querySelectorAll('.workspace-file-row').forEach((row) => {
    row.addEventListener('click', () => onRowClick(row.dataset.name, row.dataset.type));
  });
}

function setPreview(html, className = '') {
  const preview = overlayEl?.querySelector('#workspaceFilePreview');
  if (!preview) return;
  preview.className = `workspace-file-preview ${className}`.trim();
  preview.innerHTML = html;
}

async function loadDirectory(path) {
  const listEl = overlayEl?.querySelector('#workspaceFileList');
  if (listEl) listEl.innerHTML = '<p class="workspace-empty">加载中…</p>';
  const data = await listWorkspaceEntries(state.sessionId, path === '.' ? '' : path);
  state.currentPath = data.path || path || '.';
  state.entries = data.entries || [];
  state.selectedEntry = null;
  renderBreadcrumb();
  renderFileList();
  setPreview('<p class="workspace-preview-placeholder">选择文件以预览内容</p>');
  updateFooterActions();
}

async function navigateTo(path) {
  state.currentPath = normalizePath(path);
  try {
    await loadDirectory(state.currentPath === '.' ? '' : state.currentPath);
  } catch (err) {
    state.showToast?.(err.message || '加载失败', 'error');
  }
}

async function onRowClick(name, type) {
  if (!name) return;
  const rel =
    state.currentPath === '.' ? name : joinPath(state.currentPath, name);
  if (type === 'dir') {
    await navigateTo(rel);
    return;
  }
  state.selectedEntry = { name, type, path: rel };
  renderFileList();
  setPreview('<p class="workspace-empty">加载预览…</p>');
  updateFooterActions();
  try {
    const data = await fetchWorkspaceFile(state.sessionId, rel);
    setPreview(
      `<pre class="workspace-preview-code">${escapeHtml(data.content || '')}</pre>`,
      'workspace-file-preview--code',
    );
  } catch (err) {
    setPreview(
      `<p class="workspace-preview-error">${escapeHtml(err.message || '无法预览')}</p>`,
      'workspace-file-preview--error',
    );
  }
}

function updateFooterActions() {
  const fileBtn = overlayEl?.querySelector('#workspaceDownloadFile');
  const zipBtn = overlayEl?.querySelector('#workspaceDownloadFolder');
  const upBtn = overlayEl?.querySelector('#workspaceGoUp');
  if (fileBtn) fileBtn.disabled = state.selectedEntry?.type !== 'file';
  if (zipBtn) zipBtn.disabled = false;
  if (upBtn) upBtn.disabled = state.currentPath === '.';
}

async function downloadSelectedFile() {
  if (state.selectedEntry?.type !== 'file') return;
  try {
    const { name, blob } = await downloadWorkspaceFile(
      state.sessionId,
      state.selectedEntry.path,
      state.selectedEntry.name,
    );
    triggerBlobDownload(name, blob);
    state.showToast?.(`已下载 ${name}`, 'success');
  } catch (err) {
    state.showToast?.(err.message || '下载失败', 'error');
  }
}

async function downloadCurrentFolderZip() {
  const path = state.currentPath === '.' ? '' : state.currentPath;
  const baseName =
    path && path !== '.' ? path.split('/').pop() || 'workspace' : 'workspace';
  try {
    const { name, blob } = await downloadWorkspaceArchive(
      state.sessionId,
      path,
      `${baseName}.zip`,
    );
    triggerBlobDownload(name, blob);
    state.showToast?.(`已下载 ${name}`, 'success');
  } catch (err) {
    state.showToast?.(err.message || '打包下载失败', 'error');
  }
}

function copyProjectPath() {
  if (!state.projectPath) {
    state.showToast?.('项目路径不可用', 'error');
    return;
  }
  navigator.clipboard
    .writeText(state.projectPath)
    .then(() => state.showToast?.('服务器路径已复制', 'success'))
    .catch(() => state.showToast?.(state.projectPath, 'info'));
}

export function closeWorkspaceBrowser() {
  if (!overlayEl) return;
  overlayEl.hidden = true;
  document.body.classList.remove('workspace-browser-open');
}

export function openWorkspaceBrowser(sessionId, snapshot, { showToast } = {}) {
  if (!overlayEl) return;
  state.sessionId = sessionId;
  state.projectPath = snapshot?.delivery?.project_path || '';
  state.showToast = showToast;
  state.currentPath = '.';
  state.selectedEntry = null;
  const pathEl = overlayEl.querySelector('#workspaceServerPath');
  if (pathEl) pathEl.textContent = state.projectPath || '—';
  overlayEl.hidden = false;
  document.body.classList.add('workspace-browser-open');
  loadDirectory('').catch((err) => showToast?.(err.message || '无法加载工作区', 'error'));
}

export function initWorkspaceBrowserOverlay() {
  overlayEl = document.getElementById('projectFilesOverlay');
  if (!overlayEl || overlayEl.dataset.bound) return;
  overlayEl.dataset.bound = '1';

  overlayEl.querySelector('#workspaceBrowserClose')?.addEventListener('click', closeWorkspaceBrowser);
  overlayEl.querySelector('#workspaceRefresh')?.addEventListener('click', () => {
    loadDirectory(state.currentPath === '.' ? '' : state.currentPath).catch((err) =>
      state.showToast?.(err.message || '刷新失败', 'error'),
    );
  });
  overlayEl.querySelector('#workspaceCopyPath')?.addEventListener('click', copyProjectPath);
  overlayEl.querySelector('#workspaceDownloadFile')?.addEventListener('click', downloadSelectedFile);
  overlayEl.querySelector('#workspaceDownloadFolder')?.addEventListener('click', downloadCurrentFolderZip);
  overlayEl.querySelector('#workspaceGoUp')?.addEventListener('click', () => {
    navigateTo(parentPath(state.currentPath));
  });

  overlayEl.addEventListener('click', (e) => {
    if (e.target === overlayEl) closeWorkspaceBrowser();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !overlayEl.hidden) closeWorkspaceBrowser();
  });
}
