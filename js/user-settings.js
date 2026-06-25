/** @file 用户设置页 */

import {
  authHeaders,
  changePassword,
  displayUsername,
  fetchMe,
  logout,
  requireAuth,
  updateProfile,
} from './auth.js';
import { collectL1FromForm, fetchKnowledgeL1, renderL1Form, saveKnowledgeL1 } from './plan-l1.js';

function showToast(msg, type = 'info') {
  const c = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast toast-${type}`;
  el.textContent = msg;
  c?.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

async function loadProfile() {
  const user = await fetchMe();
  if (!user) return;
  document.getElementById('profileUsername').value = displayUsername(user);
  document.getElementById('profileEmail').value = user.email || '';
}

async function loadKnowledgeL1() {
  const body = document.getElementById('knowledgeL1Body');
  if (!body) return;
  try {
    const l1 = await fetchKnowledgeL1();
    renderL1Form(body, { values: l1 || undefined });
  } catch {
    renderL1Form(body, {});
  }
}

async function loadLlm() {
  const res = await fetch('/api/v1/user/llm', { headers: authHeaders() });
  const data = await res.json();
  const hint = document.getElementById('llmHint');
  const form = document.getElementById('llmForm');
  const readonly = document.getElementById('llmReadonly');

  if (data.shared_enabled) {
    hint.textContent = '当前使用平台统一 LLM API（由管理后台配置），个人无需填写。';
    form.hidden = true;
    if (readonly) {
      readonly.hidden = false;
      const llm = data.llm || {};
      readonly.innerHTML = `
        <dl class="settings-readonly-dl">
          <dt>状态</dt><dd>${llm.configured ? '已配置' : '未配置（请联系管理员在 /admin.html 设置）'}</dd>
          <dt>Base URL</dt><dd>${llm.base_url || '—'}</dd>
          <dt>Model</dt><dd>${llm.model || '—'}</dd>
          <dt>Subagent</dt><dd>${llm.subagent_model || '—'}</dd>
          <dt>API Key</dt><dd>${llm.api_key_masked || '未设置'}</dd>
        </dl>
      `;
    }
    return;
  }

  hint.textContent = '共享 API 已关闭，请在此配置您自己的 LLM 端点。';
  form.hidden = false;
  if (readonly) readonly.hidden = true;
  const llm = data.llm || {};
  document.getElementById('baseUrl').value = llm.base_url || '';
  document.getElementById('model').value = llm.model || '';
  document.getElementById('subagentModel').value = llm.subagent_model || '';
  if (llm.api_key_masked) {
    document.getElementById('apiKey').placeholder = `已配置 (${llm.api_key_masked})，留空则不修改`;
  }
}

document.getElementById('profileForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const username = document.getElementById('profileUsername').value.trim();
  const email = document.getElementById('profileEmail').value.trim();
  try {
    await updateProfile({ username, email });
    showToast('账号资料已保存', 'success');
    await loadProfile();
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('passwordForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const currentPassword = document.getElementById('currentPassword').value;
  const newPassword = document.getElementById('newPassword').value;
  if (!currentPassword || !newPassword) {
    showToast('请填写当前密码与新密码', 'error');
    return;
  }
  try {
    await changePassword(currentPassword, newPassword);
    document.getElementById('currentPassword').value = '';
    document.getElementById('newPassword').value = '';
    showToast('密码已更新', 'success');
  } catch (err) {
    showToast(err.message, 'error');
  }
});

document.getElementById('logoutBtn')?.addEventListener('click', () => {
  logout();
});

document.getElementById('knowledgeL1SaveBtn')?.addEventListener('click', async () => {
  const form = document.getElementById('knowledgeL1Body')?.querySelector('#planL1Form');
  const l1 = collectL1FromForm(form);
  try {
    await saveKnowledgeL1(l1);
    showToast('知识水平已保存', 'success');
    await loadKnowledgeL1();
  } catch (err) {
    showToast(err.message || '保存失败', 'error');
  }
});

document.getElementById('llmForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const body = {
    base_url: document.getElementById('baseUrl').value.trim(),
    model: document.getElementById('model').value.trim(),
    subagent_model: document.getElementById('subagentModel').value.trim(),
  };
  const key = document.getElementById('apiKey').value.trim();
  if (key) body.api_key = key;

  const res = await fetch('/api/v1/user/llm', {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) {
    showToast(data.detail || data.message || '保存失败', 'error');
    return;
  }
  if (data.shared_enabled) {
    showToast(data.message || '当前使用平台统一 API', 'info');
    await loadLlm();
    return;
  }
  showToast('已保存', 'success');
  await loadLlm();
});

if (requireAuth()) {
  loadProfile();
  loadKnowledgeL1();
  loadLlm();
}
