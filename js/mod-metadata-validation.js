/** @file Fabric mod 名称 / ID / 包名校验（与 server/mod_metadata_validation.py 规则一致） */

const MOD_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9 &'.\-]{0,39}$/;
const MOD_ID_RE = /^[a-z0-9_]{2,32}$/;
const PACKAGE_SEGMENT_RE = /^[a-z][a-z0-9_]*$/;
const INVALID_MOD_NAME_CHARS = /[\\/:*?"<>|]/;
const CJK_RE = /[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]/;

/** @typedef {{ valid: boolean, message?: string }} ValidationResult */

/** @param {string} modId */
export function normalizeModId(modId) {
  let s = (modId || '').trim().toLowerCase().replace(/\s+/g, '_');
  s = s.replace(/[^a-z0-9_]/g, '').replace(/_+/g, '_').replace(/^_|_$/g, '');
  if (!s || !/^[a-z]/.test(s)) {
    s = `mod_${s || 'uscript'}`.replace(/^mod_+/, 'mod_');
  }
  if (s.length < 2) s = `${s}mod`.slice(0, 32);
  return s.slice(0, 32);
}

/** @param {string} modName */
export function deriveModId(modName) {
  let s = (modName || '')
    .trim()
    .toLowerCase()
    .replace(/[^\w]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '');
  return normalizeModId(s || 'moduscript');
}

let _modTemplatePackage = 'com.example';

/** @param {string} pkg */
export function setModTemplatePackage(pkg) {
  const trimmed = (pkg || '').trim();
  _modTemplatePackage = trimmed || 'com.example';
}

export function getModTemplatePackage() {
  return _modTemplatePackage;
}

/** @param {string} modId @param {string} [basePackage] */
export function derivePackageName(modId, basePackage = _modTemplatePackage) {
  const suffix = (modId || '').replace(/_/g, '').toLowerCase() || 'mod';
  return `${basePackage}.${suffix}`;
}

/** @param {string} name @returns {ValidationResult} */
export function validateModName(name) {
  const value = (name || '').trim();
  if (!value) return { valid: true };
  if (value.length < 2) return { valid: false, message: 'Mod 名称至少 2 个字符' };
  if (value.length > 40) return { valid: false, message: 'Mod 名称最多 40 个字符' };
  if (INVALID_MOD_NAME_CHARS.test(value)) {
    return { valid: false, message: 'Mod 名称不能包含 \\ / : * ? " < > |' };
  }
  if (CJK_RE.test(value)) {
    return {
      valid: false,
      message: 'Mod 名称须为英文（拉丁字母、数字及 & . \' - 空格），请留空以自动生成',
    };
  }
  if (!MOD_NAME_RE.test(value)) {
    return {
      valid: false,
      message: "Mod 名称仅允许拉丁字母、数字、空格及 & . ' -，且须以字母或数字开头",
    };
  }
  return { valid: true };
}

/** @param {string} modId @returns {ValidationResult} */
export function validateModId(modId) {
  const raw = (modId || '').trim();
  if (!raw) return { valid: true };
  if (raw.length < 2) return { valid: false, message: 'Mod ID 至少 2 个字符' };
  if (raw.length > 32) return { valid: false, message: 'Mod ID 最多 32 个字符' };
  if (/[A-Z]/.test(raw)) return { valid: false, message: 'Mod ID 不允许大写字母' };
  if (/\s/.test(raw)) return { valid: false, message: 'Mod ID 不能包含空格，请使用下划线' };
  if (!MOD_ID_RE.test(raw)) {
    return {
      valid: false,
      message: 'Mod ID 仅含小写字母、数字与下划线（如 my_mod）',
    };
  }
  return { valid: true };
}

/** @param {string} packageName @returns {ValidationResult} */
export function validatePackageName(packageName) {
  const raw = (packageName || '').trim();
  const value = raw.toLowerCase();
  if (!value) return { valid: true };
  if (value.length > 80) return { valid: false, message: '包名最多 80 个字符' };
  if (raw !== value) return { valid: false, message: '包名须为小写' };
  if (/\s/.test(raw)) return { valid: false, message: '包名不能包含空格' };
  const segments = value.split('.');
  if (segments.length < 2) {
    return { valid: false, message: '包名至少包含两段，如 com.example.mymod' };
  }
  for (const seg of segments) {
    if (!seg) return { valid: false, message: '包名不能包含连续的点或首尾点' };
    if (!PACKAGE_SEGMENT_RE.test(seg)) {
      return {
        valid: false,
        message: '包名每段须以小写字母开头，仅含小写字母、数字与下划线',
      };
    }
  }
  return { valid: true };
}

/** @param {{ modName?: string, modId?: string, packageName?: string }} fields @returns {ValidationResult} */
export function validateModMetadataFields(fields = {}) {
  for (const check of [
    validateModName(fields.modName || ''),
    validateModId(fields.modId || ''),
    validatePackageName(fields.packageName || ''),
  ]) {
    if (!check.valid) return check;
  }
  return { valid: true };
}
