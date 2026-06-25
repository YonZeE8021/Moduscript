/** @file 将 Markdown 渲染为 HTML（优化结果预览） */

import { marked } from 'https://esm.sh/marked@15.0.7';

let configured = false;

function ensureMarked() {
  if (configured) return;
  marked.setOptions({
    gfm: true,
    breaks: false,
    headerIds: false,
    mangle: false,
  });
  configured = true;
}

/**
 * @param {string} source
 * @returns {string}
 */
export function renderMarkdown(source) {
  if (!source) return '';
  try {
    ensureMarked();
    return marked.parse(source, { async: false });
  } catch {
    const esc = source
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    return `<pre class="markdown-fallback">${esc}</pre>`;
  }
}
