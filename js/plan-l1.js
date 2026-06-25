/** @file L1 四维知识水平问卷 */

import { API } from './config.js';
import { authHeaders } from './auth.js';
import { formatApiDetail } from './plan-api.js';

export const L1_DIMENSIONS = [
  {
    key: 'programming',
    title: '对「编程」的了解，您处于哪一档？',
    options: [
      { level: 0, label: '完全不了解', hint: '没系统学过，不明白计算机程序是如何运行的' },
      { level: 1, label: '理解核心概念，并能动手写一点', hint: '懂变量、数据类型、流程控制、函数复用；并能用至少一门语言写可运行小型程序、查文档/API、读结构简单的他人代码' },
      { level: 2, label: '有较深基础或工程经验', hint: '面向对象；堆栈与引用/指针；常见数据结构；模块/包与接口；调试、异常、日志；Git 与构建/依赖等' },
    ],
  },
  {
    key: 'ai_literacy',
    title: '您对 AI 的使用经验如何？',
    options: [
      { level: 0, label: '仅必要时使用', hint: '偶尔用来应付短文需求（如写检讨、润色作业），不当作日常工具' },
      { level: 1, label: '日常会使用', hint: '常用来查资料、聊天、生成文字/图片/视频；会简单多轮改说法' },
      { level: 2, label: '深度依赖，了解 LLM 通识', hint: '工作流深度依赖 AI；了解上下文窗口 / token、幻觉与过度迎合、系统提示与用户消息的区别；知晓提示词注入、越狱等安全风险' },
    ],
  },
  {
    key: 'general_tech',
    title: '在「玩游戏 mod / 折腾环境」（不限于 Minecraft）方面，您能做到哪一档？',
    options: [
      { level: 0, label: '会装整合包、加 mod 游玩', hint: '能装整合包或往文件夹加 mod；知道游戏有版本区分；崩溃、版本/加载器冲突难以自行排查' },
      { level: 1, label: '能折腾整合包与联机环境', hint: '能制作或维护整合包、改配置文件（options、kubejs、toml/json 等）；会内网穿透或基础联机' },
      { level: 2, label: '熟悉专用服务端并可排查错误', hint: '了解多种专用服务端、网络架构、数据包含义；能进行日志与兼容排查；对加载器、注册内容、资源与数据驱动有整体认识' },
    ],
  },
  {
    key: 'mc_mechanics',
    title: '对 Minecraft 游戏机制（不限于原版）了解到哪一档？',
    options: [
      { level: 0, label: '配方与流程仍不熟', hint: '尚未基本掌握合成、熔炼、酿造等常见配方与流程；生存能玩，但说不清「怎么做出某样东西」' },
      { level: 1, label: '懂基础运作与常见机制', hint: '知道 tick、区块 等节奏概念；会用 title、sidebar、scoreboard 等基础指令；大致理解方块更新、实体碰撞/伤害判定、红石/容器等；玩 mod 时能感觉「改了掉落/配方/生物行为」' },
      { level: 2, label: '机制向较深入', hint: '能描述 tick / 事件（交互、伤害、定时）如何串成玩法；熟悉 NBT、战利品表、标签（tags） 等数据概念；能区分扩展原版机制 vs 全新系统；对 mod 常见改法（新工序、新实体 AI、新进度）有整体认识' },
    ],
  },
];

function apiUrl(path) {
  const p = path.startsWith('/') ? path : `/${path}`;
  return API.baseUrl ? `${API.baseUrl.replace(/\/$/, '')}${p}` : p;
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text ?? '';
  return div.innerHTML;
}

/**
 * @returns {Promise<object | null>}
 */
export async function fetchKnowledgeL1() {
  const res = await fetch(apiUrl(API.knowledgeL1), { headers: authHeaders() });
  if (!res.ok) return null;
  const data = await res.json();
  return data?.knowledge_l1 || null;
}

/**
 * @param {object} l1
 */
export async function saveKnowledgeL1(l1) {
  const res = await fetch(apiUrl(API.knowledgeL1), {
    method: 'PUT',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(l1),
  });
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(formatApiDetail(data?.detail ?? data?.message, '保存知识水平失败'));
  }
  const data = await res.json();
  return data?.knowledge_l1;
}

/**
 * @param {HTMLElement} container
 * @param {{ values?: object, onChange?: (values: object) => void }} [opts]
 */
export function renderL1Form(container, opts = {}) {
  const values = { programming: 1, ai_literacy: 1, general_tech: 0, mc_mechanics: 0, ...opts.values };

  container.innerHTML = `
    <p class="plan-l1-intro">为了向您解释为什么，我们必须首先假设您知道些什么。请允许我们大致了解您的知识水平：</p>
    <form class="plan-l1-form" id="planL1Form"></form>
  `;

  const form = container.querySelector('#planL1Form');
  L1_DIMENSIONS.forEach((dim) => {
    const field = document.createElement('fieldset');
    field.className = 'plan-l1-field';
    field.innerHTML = `<legend class="plan-l1-legend">${escapeHtml(dim.title)}</legend>`;

    dim.options.forEach((opt) => {
      const id = `l1-${dim.key}-${opt.level}`;
      const checked = values[dim.key] === opt.level;
      const row = document.createElement('label');
      row.className = 'plan-l1-option';
      row.innerHTML = `
        <input type="radio" name="${dim.key}" value="${opt.level}" id="${id}" ${checked ? 'checked' : ''}>
        <span class="plan-l1-option-body">
          <span class="plan-l1-option-label">${escapeHtml(opt.label)}</span>
          <span class="plan-l1-option-hint">${escapeHtml(opt.hint)}</span>
        </span>
      `;
      field.appendChild(row);
    });
    form?.appendChild(field);
  });

  form?.addEventListener('change', () => {
    if (opts.onChange) opts.onChange(collectL1FromForm(form));
  });
}

/**
 * @param {HTMLFormElement | null} form
 */
export function collectL1FromForm(form) {
  const result = {};
  L1_DIMENSIONS.forEach((dim) => {
    const checked = form?.querySelector(`input[name="${dim.key}"]:checked`);
    result[dim.key] = checked ? parseInt(checked.value, 10) : 1;
  });
  return result;
}
