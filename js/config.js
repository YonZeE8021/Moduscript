/** @file Moduscript 模词成稿 — 前端配置 */

export const API = {
  baseUrl: '',
  createSession: '/api/v1/sessions',
  getSession: '/api/v1/sessions',
  listSessions: '/api/v1/sessions',
  modrinthResolve: '/api/v1/modrinth/resolve',
  suggestModMetadata: '/api/v1/mod/suggest-metadata',
  optimizeDescription: '/api/v1/prompt/optimize',
  authRegister: '/api/v1/auth/register',
  authLogin: '/api/v1/auth/login',
  authMe: '/api/v1/auth/me',
  userProfile: '/api/v1/user/profile',
  userPassword: '/api/v1/user/password',
  siteSettings: '/api/v1/site/settings',
  defaultRequirements: '/api/v1/site/default-requirements',
  userPreferences: '/api/v1/user/preferences',
  knowledgeL1: '/api/v1/user/knowledge-l1',
  planSessions: '/api/v1/plan/sessions',
  adminOverview: '/api/v1/admin/overview',
};

/** true 时首页提交走 client mock；false 时使用服务端 API */
export const DEBUG_MOCK = false;

export const VALIDATION = {
  minPromptLength: 10,
};

export const DEFAULTS = {
  mcVersion: '1.20.1',
  modLoader: 'fabric',
  platform: 'unspecified',
};

/** 部署环境（客户端/服务端分布） */
export const PLATFORMS = [
  { value: 'unspecified', label: '不指定' },
  { value: 'client_only', label: '纯客户端' },
  { value: 'server_only', label: '纯服务端' },
  { value: 'server_required_client_optional', label: '服务端必装客户端可选' },
  { value: 'client_required_server_optional', label: '客户端必装服务端可选' },
  { value: 'both_required', label: '客户端与服务端均必装' },
  { value: 'dedicated_server_only', label: '仅专用服务器' },
];

const MOD_LOADERS = [
  { value: 'neoforge', label: 'NeoForge' },
  { value: 'forge', label: 'Forge' },
  { value: 'fabric', label: 'Fabric' },
  { value: 'quilt', label: 'Quilt' },
];

const PLUGIN_LOADERS = [
  { value: 'paper', label: 'Paper' },
  { value: 'purpur', label: 'Purpur' },
  { value: 'spigot', label: 'Spigot' },
  { value: 'bukkit', label: 'Bukkit' },
  { value: 'folia', label: 'Folia' },
];

const DATAPACK = { value: 'datapack', label: 'Data Pack' };

const PLUGIN_LOADER_VALUES = new Set(PLUGIN_LOADERS.map((l) => l.value));

/** 项目类型中文标签 */
export const PROJECT_TYPE_LABELS = {
  mod: '模组',
  plugin: '插件',
  datapack: '数据包',
};

/**
 * 根据加载器 value 判断项目类型
 * @param {string} loader
 * @returns {'mod' | 'plugin' | 'datapack'}
 */
export function getProjectType(loader) {
  if (loader === DATAPACK.value) return 'datapack';
  if (PLUGIN_LOADER_VALUES.has(loader)) return 'plugin';
  return 'mod';
}

/** 下拉中 disabled 项的后缀文案 */
export const UNSUPPORTED_SUFFIX = '暂不支持';

/**
 * @template {{ disabled?: boolean }} T
 * @param {T[]} items
 * @returns {T | undefined}
 */
export function findFirstEnabled(items) {
  return items.find((item) => !item.disabled);
}

/**
 * @param {Array<{ value: string, label: string }>} loaders
 * @param {{ enabledValue?: string, allDisabled?: boolean }} options
 */
function markLoaders(loaders, { enabledValue = null, allDisabled = false } = {}) {
  return loaders.map((loader) => ({
    ...loader,
    disabled: allDisabled || (enabledValue !== null && loader.value !== enabledValue),
  }));
}

/**
 * MC 版本与可用加载器（value 为小写 API 字段）
 */
export const VERSION_LOADERS = {
  '1.21.1': markLoaders([...MOD_LOADERS, ...PLUGIN_LOADERS, DATAPACK], { allDisabled: true }),
  '1.20.1': markLoaders(
    [
      { value: 'fabric', label: 'Fabric' },
      { value: 'forge', label: 'Forge' },
      { value: 'neoforge', label: 'NeoForge' },
      { value: 'quilt', label: 'Quilt' },
      ...PLUGIN_LOADERS,
      DATAPACK,
    ],
    { enabledValue: 'fabric' }
  ),
  '1.19.2': markLoaders(
    [
      { value: 'fabric', label: 'Fabric' },
      { value: 'forge', label: 'Forge' },
      { value: 'quilt', label: 'Quilt' },
      { value: 'paper', label: 'Paper' },
      { value: 'purpur', label: 'Purpur' },
      { value: 'spigot', label: 'Spigot' },
      { value: 'bukkit', label: 'Bukkit' },
      DATAPACK,
    ],
    { allDisabled: true }
  ),
  '1.18.2': markLoaders(
    [
      { value: 'fabric', label: 'Fabric' },
      { value: 'forge', label: 'Forge' },
      { value: 'quilt', label: 'Quilt' },
      { value: 'paper', label: 'Paper' },
      { value: 'purpur', label: 'Purpur' },
      { value: 'spigot', label: 'Spigot' },
      { value: 'bukkit', label: 'Bukkit' },
      DATAPACK,
    ],
    { allDisabled: true }
  ),
};

export const MC_VERSIONS = [
  { value: '1.21.1', disabled: true },
  { value: '1.20.1', disabled: false },
  { value: '1.19.2', disabled: true },
  { value: '1.18.2', disabled: true },
];

/** 标题最多汉字数 */
export const REQUIREMENT_TITLE_MAX_CHARS = 8;

/**
 * 默认「其他要求」选项（首次使用或恢复默认时使用）
 */
export const DEFAULT_REQUIREMENTS = [
  {
    id: 'usage_docs',
    title: '详细使用文档',
    description:
      '完成后创建简要版与详细技术版 MD。简要版面向玩家与非技术用户：简介、基本原理、使用方法、配置介绍（若有）、常见问题、环境依赖安装方法；详细技术版供其他开发者参考。',
    detail: {
      brief_sections: [
        '简介',
        '基本原理',
        '使用方法',
        '配置介绍',
        '常见问题',
        '环境依赖安装',
      ],
      technical_for: 'developers',
      cost: 'medium',
    },
    enabled: true,
  },
  {
    id: 'production_arch',
    title: '生产级架构质量',
    description: '模组按生产级标准构建，保证清晰的模块边界、可维护性与可测试性。',
    detail: {
      standard: 'production',
      focus: ['architecture', 'maintainability', 'module_boundaries'],
      cost: 'high',
    },
    enabled: true,
  },
  {
    id: 'rich_config',
    title: '充足配置项',
    description: '通过配置文件（如 common/client 分离）暴露充足可调参数，便于玩家与整合包作者自定义。',
    detail: {
      config_style: 'file_based',
      coverage: 'comprehensive',
      cost: 'medium',
    },
    enabled: true,
  },
  {
    id: 'graceful_upgrade',
    title: '优雅升级兼容',
    description: '在 mod 版本升级时保持优雅兼容，考虑数据迁移、配置迁移与 API 变更的向后兼容策略。',
    detail: {
      strategy: 'graceful_upgrade',
      focus: ['data_migration', 'config_migration', 'api_compatibility'],
      cost: 'high',
    },
    enabled: true,
  },
  {
    id: 'evaluate_difficulty',
    title: '评估实现难度',
    description:
      '对复杂 mod 与复杂功能评估实现难度；优先实现最小可行版本（MVP）验证核心机制，再迭代完善。',
    detail: {
      strategy: 'mvp_first',
      evaluate_difficulty: true,
      cost: 'low',
    },
    enabled: true,
  },
];

/** @deprecated 使用 DEFAULT_REQUIREMENTS */
export const BUILTIN_REQUIREMENTS = DEFAULT_REQUIREMENTS;

export const LOCALE = 'zh-CN';

export const TOKEN_STORAGE_KEY = 'mcmod_agent_token';

/** 手动参考模组数量上限 */
export const MAX_REFERENCE_MODS = 5;

/** 参考模组详情气泡最大字符数 */
export const REF_MOD_DESCRIPTION_PREVIEW_LEN = 800;

/** Agent 打扰强度（开始编写时选择） */
export const INTERRUPTION_LEVELS = [
  {
    value: 0,
    label: '勿扰',
    hint: '严格禁止打扰',
    promptText:
      '## Agent 沟通策略\n严格禁止向用户提问或请求确认，所有问题全部自行推断。',
  },
  {
    value: 1,
    label: '极少',
    hint: '非极度必要，否则绝不打扰',
    promptText:
      '## Agent 沟通策略\n仅在遇到无法继续的严重阻塞时才向用户提问；能自行合理推断的决策不要打扰用户。',
  },
  {
    value: 2,
    label: '标准',
    hint: '不添加额外提示词',
    omitFromPrompt: true,
  },
  {
    value: 3,
    label: '欢迎',
    hint: '欢迎询问',
    promptText:
      '## Agent 沟通策略\n开发过程中尽可能多主动询问澄清需求、设计取舍与实现细节。',
  },
];

export const DEFAULT_INTERRUPTION_LEVEL = 2;

export const INTERRUPTION_LEVEL_KEY = 'moduscript_interruption_level';

export const DEFAULT_MAX_TURNS = 150;
export const MAX_TURNS_MIN = 10;
export const MAX_TURNS_MAX = 500;
export const MAX_TURNS_KEY = 'moduscript_max_turns';

/** 管理后台独立口令令牌（与站点用户 JWT 分离） */
export const ADMIN_PANEL_TOKEN_KEY = 'mcmod_admin_panel_token';
