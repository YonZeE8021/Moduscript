import { INTERRUPTION_LEVELS, PLATFORMS } from './config.js';
import { isDirectSourceRef, isRefModIncludedInPrompt, isRefModSourceMissing } from './reference-mods.js';
import { PROMPT_TEMPLATES, fillTemplate } from './prompt-templates.js';

/**
 * @typedef {'mod' | 'plugin' | 'datapack'} ProjectType
 */

/**
 * @typedef {object} PromptContext
 * @property {ProjectType} projectType
 * @property {string} mcVersion
 * @property {string} modLoader
 * @property {string} modLoaderLabel
 * @property {string} platform
 * @property {string} platformLabel
 * @property {string} userConcept
 * @property {'build' | 'plan'} mode
 * @property {number} [interruptionLevel]
 * @property {string} [modName]
 * @property {string} [modId]
 * @property {string} [packageName]
 * @property {Array<{ title: string, description?: string, detail?: { custom_prompt?: string } }>} selectedRequirements
 * @property {Array<object>} referenceModsManual
 * @property {boolean} autoSearch
 */

function escapeXml(text) {
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/**
 * @param {PromptContext} ctx
 */
function buildProjectConstraintsXml(ctx) {
  const lines = ['<project_constraints>'];
  lines.push(`  <task_type>${escapeXml(ctx.projectType)}</task_type>`);
  lines.push(`  <minecraft_version>${escapeXml(ctx.mcVersion)}</minecraft_version>`);
  lines.push(
    `  <loader api="${escapeXml(ctx.modLoader)}">${escapeXml(ctx.modLoaderLabel)}</loader>`,
  );
  if (ctx.platform !== 'unspecified') {
    lines.push(`  <deployment>${escapeXml(ctx.platformLabel)}</deployment>`);
  }
  if (ctx.modName || ctx.modId || ctx.packageName) {
    lines.push('  <mod_metadata>');
    if (ctx.modName) lines.push(`    <mod_name>${escapeXml(ctx.modName)}</mod_name>`);
    if (ctx.modId) lines.push(`    <mod_id>${escapeXml(ctx.modId)}</mod_id>`);
    if (ctx.packageName) lines.push(`    <package_name>${escapeXml(ctx.packageName)}</package_name>`);
    lines.push('  </mod_metadata>');
  }
  lines.push('  <global_language>中文</global_language>');
  lines.push('</project_constraints>');
  return lines.join('\n');
}

/**
 * @param {PromptContext['selectedRequirements'][number]} req
 */
function getRequirementBody(req) {
  const custom = req.detail?.custom_prompt?.trim();
  if (custom) return custom;
  return req.description?.trim() || '';
}

/**
 * @param {object} mod
 * @param {'build' | 'plan'} mode
 */
function buildReferenceCodeItem(mod, mode = 'plan') {
  let sourceLine = '';
  const slug = mod.slug || mod.project_id || 'unknown';
  if (mode === 'build') {
    if (mod.source_url) {
      sourceLine = `${fillTemplate(PROMPT_TEMPLATES.referenceSourceOpen, { source_url: mod.source_url })}\n`;
      sourceLine += `${fillTemplate(PROMPT_TEMPLATES.referenceSourceServerClone, { slug })}\n`;
    } else if (isRefModSourceMissing(mod) && mod.decompile_attempt) {
      sourceLine = `${fillTemplate(PROMPT_TEMPLATES.referenceSourceServerPrepared, { slug })}\n`;
    } else {
      sourceLine = `${fillTemplate(PROMPT_TEMPLATES.referenceSourceServerPrepared, { slug })}\n`;
    }
  } else {
    if (isRefModSourceMissing(mod) && mod.decompile_attempt) {
      sourceLine = `${PROMPT_TEMPLATES.referenceSourceDecompile}\n`;
    } else if (mod.source_url) {
      sourceLine = `${fillTemplate(PROMPT_TEMPLATES.referenceSourceOpen, { source_url: mod.source_url })}\n`;
    }
    if (!isDirectSourceRef(mod)) {
      sourceLine += `${PROMPT_TEMPLATES.referenceSourceModrinthFetch}\n`;
    }
  }
  const descriptionLine = mod.description
    ? `${fillTemplate(PROMPT_TEMPLATES.referenceDescription, { description: mod.description })}\n`
    : '';
  const noteLine = mod.note?.trim()
    ? `${fillTemplate(PROMPT_TEMPLATES.referenceNote, { note: mod.note.trim() })}\n`
    : '';
  return fillTemplate(PROMPT_TEMPLATES.referenceCodeItem, {
    title: mod.title || mod.slug,
    permalink: mod.permalink,
    sourceLine,
    descriptionLine,
    noteLine,
  }).trimEnd();
}

/**
 * @param {object} mod
 */
function buildReferenceFeatureItem(mod) {
  const descriptionLine = mod.description
    ? `${fillTemplate(PROMPT_TEMPLATES.referenceDescription, { description: mod.description })}\n`
    : '';
  const noteLine = mod.note?.trim()
    ? `${fillTemplate(PROMPT_TEMPLATES.referenceNote, { note: mod.note.trim() })}\n`
    : '';
  return fillTemplate(PROMPT_TEMPLATES.referenceFeatureItem, {
    title: mod.title || mod.slug,
    permalink: mod.permalink,
    descriptionLine,
    noteLine,
  }).trimEnd();
}

/**
 * @param {PromptContext} ctx
 * @returns {string[]}
 */
function buildPromptAppendixLines(ctx) {
  const lines = [];
  const type = ctx.projectType;

  if (ctx.mode === 'build' && ctx.interruptionLevel !== undefined) {
    const level = INTERRUPTION_LEVELS.find((l) => l.value === ctx.interruptionLevel);
    if (level?.promptText && !level.omitFromPrompt) {
      lines.push(level.promptText, '');
    }
  }

  if ((ctx.selectedRequirements || []).length > 0) {
    lines.push(PROMPT_TEMPLATES.requirementsHeading);
    (ctx.selectedRequirements || []).forEach((req) => {
      lines.push(`### ${req.title}`);
      const body = getRequirementBody(req);
      if (body) lines.push(body);
      lines.push('');
    });
  }

  const codeRefs = (ctx.referenceModsManual || []).filter((m) => m.reference_type === 'code');
  const featureRefs = (ctx.referenceModsManual || []).filter((m) => m.reference_type === 'feature');
  const codeInPrompt = codeRefs.filter(isRefModIncludedInPrompt);

  if (codeInPrompt.length > 0 || featureRefs.length > 0) {
    lines.push(PROMPT_TEMPLATES.referenceHeading[type]);
    if (codeInPrompt.length > 0) {
      lines.push(PROMPT_TEMPLATES.referenceCodeSubheading);
      codeInPrompt.forEach((m) => lines.push(buildReferenceCodeItem(m, ctx.mode)));
      if (ctx.mode === 'build') {
        lines.push(PROMPT_TEMPLATES.referenceCodeSectionNoteBuild);
      } else if (codeInPrompt.some((m) => !isDirectSourceRef(m))) {
        lines.push(PROMPT_TEMPLATES.referenceCodeSectionNote);
      }
    }
    if (featureRefs.length > 0) {
      lines.push(PROMPT_TEMPLATES.referenceFeatureSubheading);
      featureRefs.forEach((m) => lines.push(buildReferenceFeatureItem(m)));
    }
    lines.push('');
  }

  if (ctx.autoSearch) {
    lines.push(PROMPT_TEMPLATES.modrinthAutoSearch);
  }

  return lines;
}

/**
 * Handoff appendix appended after plan_summary (build-mode reference wording).
 * @param {PromptContext} ctx
 * @returns {string}
 */
export function buildHandoffAppendix(ctx) {
  return buildPromptAppendixLines({ ...ctx, mode: 'build' }).join('\n').trimEnd();
}

/**
 * @param {PromptContext} ctx
 * @returns {string}
 */
export function buildFinalPrompt(ctx) {
  const lines = [];
  const type = ctx.projectType;

  lines.push(PROMPT_TEMPLATES.title[type], '');
  lines.push(buildProjectConstraintsXml(ctx), '');
  lines.push(PROMPT_TEMPLATES.conceptHeading[type]);
  lines.push(ctx.userConcept || '（未填写）', '');
  lines.push(...buildPromptAppendixLines(ctx));

  return lines.join('\n').trimEnd();
}

/**
 * @param {string} platformValue
 */
export function getPlatformLabel(platformValue) {
  return PLATFORMS.find((p) => p.value === platformValue)?.label || platformValue;
}
