/** @typedef {'low' | 'medium' | 'high' | 'unknown'} RequirementCostValue */

export const REQUIREMENT_COST_LEVELS = [
  { value: 'low', label: '低' },
  { value: 'medium', label: '中' },
  { value: 'high', label: '高' },
  { value: 'unknown', label: '未知' },
];

const COST_VALUE_SET = new Set(REQUIREMENT_COST_LEVELS.map((l) => l.value));

/**
 * @param {unknown} value
 * @returns {RequirementCostValue | null}
 */
export function normalizeRequirementCost(value) {
  const normalized = String(value || '')
    .trim()
    .toLowerCase();
  if (COST_VALUE_SET.has(normalized)) return /** @type {RequirementCostValue} */ (normalized);
  return null;
}

/**
 * 仅当 detail.cost 有值时显示（用户自建项表单不会写入 cost）
 * @param {{ detail?: { cost?: string } }} req
 * @returns {{ value: RequirementCostValue, label: string } | null}
 */
export function getRequirementCostBadge(req) {
  if (!req) return null;
  const value = normalizeRequirementCost(req.detail?.cost);
  if (!value) return null;
  const level = REQUIREMENT_COST_LEVELS.find((l) => l.value === value);
  return { value, label: level?.label || '未知' };
}

/**
 * @param {{ value: RequirementCostValue, label: string }} badge
 */
export function formatRequirementCostText(badge) {
  return `开销：${badge.label}`;
}
