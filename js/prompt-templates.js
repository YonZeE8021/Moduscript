/** @file 提示词各区块默认可编辑文案 — 开发者在此集中修改 */

/**
 * @typedef {'mod' | 'plugin' | 'datapack'} ProjectType
 */

export const PROMPT_TEMPLATES = {
  title: {
    mod: '# Minecraft 模组生成请求',
    plugin: '# Minecraft 插件生成请求',
    datapack: '# Minecraft 数据包生成请求',
  },
  conceptHeading: {
    mod: '## 模组构想',
    plugin: '## 插件构想',
    datapack: '## 数据包构想',
  },
  requirementsHeading: '## 其他要求',
  referenceHeading: {
    mod: '## 参考模组',
    plugin: '## 参考插件',
    datapack: '## 参考数据包',
  },
  referenceCodeSubheading: '### 参考代码',
  referenceFeatureSubheading: '### 参考功能',
  referenceCodeItem: `- **{title}**
  - 链接：{permalink}
{sourceLine}{descriptionLine}{noteLine}`,
  referenceFeatureItem: `- **{title}**
  - 链接：{permalink}
{descriptionLine}{noteLine}`,
  referenceSourceOpen: '  - 开源：{source_url}',
  referenceSourceModrinthFetch:
    '  - 获取方式：请通过 Modrinth API 获取该模组的开源代码并下载后参考',
  referenceSourceServerPrepared:
    '  - 获取方式：会话启动时由服务端下载/反编译到工作区 references/{slug}/',
  referenceSourceServerClone:
    '  - 获取方式：会话启动时由服务端克隆开源仓库到工作区 references/{slug}/',
  referenceSourceDecompile: '  - 获取方式：尝试下载并反编译+反混淆（无开源仓库）',
  referenceCodeSectionNote:
    '编写 Agent 须通过 Modrinth API 获取上述参考模组开源代码并下载后参考实现。',
  referenceCodeSectionNoteBuild:
    '编写 Agent 须优先阅读工作区 references/ 下已 materialize 的源码；仅当某参考索引失败时再自行搜索。',
  referenceDescription: '  - 说明：{description}',
  referenceNote: '  - 备注：{note}',
  modrinthAutoSearch:
    '## 你需要使用 Modrinth API 自行搜索相关 mod/插件并参考',
};

/**
 * 简单占位符替换：{key} → vars[key]
 * @param {string} template
 * @param {Record<string, string>} vars
 */
export function fillTemplate(template, vars) {
  return template.replace(/\{(\w+)\}/g, (_, key) => vars[key] ?? '');
}
