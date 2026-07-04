/** Config diff helpers for backtest run comparison. */

import { FIELD_META } from './botConfigDisplay';

const DIFF_SKIP = new Set([
  'backtest_run_id',
  'backtest_fingerprint',
  'deploy_gate_passed_at',
  'deploy_workflow',
  'pipeline_source',
  'walk_forward_deploy',
  'portfolio_deploy',
  'portfolio_weight',
  'scanner_insight_id',
]);

function stable(val) {
  if (val === undefined) return null;
  if (typeof val === 'object' && val !== null) return JSON.stringify(val);
  return val;
}

function labelFor(key) {
  return FIELD_META[key]?.label ?? key.replace(/_/g, ' ');
}

/**
 * Diff two config objects for A/B compare tables.
 * @returns {Array<{ key: string, label: string, left: unknown, right: unknown }>}
 */
export function diffBacktestConfigs(left = {}, right = {}, { maxRows = 24 } = {}) {
  const keys = new Set([
    ...Object.keys(left || {}),
    ...Object.keys(right || {}),
  ]);
  const rows = [];
  for (const key of keys) {
    if (DIFF_SKIP.has(key)) continue;
    const a = left?.[key];
    const b = right?.[key];
    if (stable(a) === stable(b)) continue;
    rows.push({
      key,
      label: labelFor(key),
      left: a,
      right: b,
    });
  }
  rows.sort((x, y) => x.label.localeCompare(y.label));
  return rows.slice(0, maxRows);
}

export function formatConfigValue(val) {
  if (val == null || val === '') return '—';
  if (typeof val === 'boolean') return val ? 'yes' : 'no';
  if (typeof val === 'number') return String(val);
  if (typeof val === 'object') return JSON.stringify(val);
  return String(val);
}
