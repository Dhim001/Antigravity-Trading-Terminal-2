/** Compare two saved optimization runs (Tier 4). */

import { diffBacktestConfigs, formatConfigValue } from './backtestConfigDiff';

function num(val, fallback = null) {
  if (val == null || val === '') return fallback;
  const n = Number(val);
  return Number.isFinite(n) ? n : fallback;
}

function wfMetric(run, path) {
  const wf = run?.walk_forward ?? run?.request?.walk_forward_result ?? {};
  const agg = wf.aggregate ?? {};
  const parts = path.split('.');
  let cur = { aggregate: agg, walk_forward: wf, ...wf };
  for (const p of parts) {
    cur = cur?.[p];
  }
  return cur;
}

/**
 * Build comparison summary for two optimization runs (same symbol/strategy preferred).
 */
export function compareOptimizationRuns(left, right) {
  const leftCfg = left?.best_config ?? {};
  const rightCfg = right?.best_config ?? {};
  const configDiff = diffBacktestConfigs(leftCfg, rightCfg, { maxRows: 32 });

  const leftWf = left?.walk_forward ?? left?.request?.walk_forward_result;
  const rightWf = right?.walk_forward ?? right?.request?.walk_forward_result;

  const metrics = [
    {
      id: 'objective',
      label: 'Objective',
      left: left?.objective,
      right: right?.objective,
    },
    {
      id: 'combos',
      label: 'Configs tested',
      left: left?.results?.length ?? '—',
      right: right?.results?.length ?? '—',
    },
    {
      id: 'oos_pnl',
      label: 'OOS PnL',
      left: num(leftWf?.out_of_sample?.total_pnl),
      right: num(rightWf?.out_of_sample?.total_pnl),
      format: 'currency',
    },
    {
      id: 'stability',
      label: 'OOS stability',
      left: num(leftWf?.aggregate?.stability_score),
      right: num(rightWf?.aggregate?.stability_score),
      format: 'pct',
    },
    {
      id: 'wfe',
      label: 'WFE',
      left: num(leftWf?.aggregate?.walk_forward_efficiency ?? leftWf?.aggregate?.selection_bias?.walk_forward_efficiency),
      right: num(rightWf?.aggregate?.walk_forward_efficiency ?? rightWf?.aggregate?.selection_bias?.walk_forward_efficiency),
      format: 'ratio',
    },
    {
      id: 'dsr',
      label: 'DSR',
      left: num(leftWf?.aggregate?.deflated_sharpe_ratio ?? leftWf?.aggregate?.selection_bias?.deflated_sharpe_ratio),
      right: num(rightWf?.aggregate?.deflated_sharpe_ratio ?? rightWf?.aggregate?.selection_bias?.deflated_sharpe_ratio),
      format: 'pct',
    },
    {
      id: 'pbo',
      label: 'PBO',
      left: num(leftWf?.pbo_audit?.pbo ?? left?.pbo_audit?.pbo),
      right: num(rightWf?.pbo_audit?.pbo ?? right?.pbo_audit?.pbo),
      format: 'pct',
    },
  ];

  const leftRegime = leftWf?.aggregate?.regime_analysis ?? {};
  const rightRegime = rightWf?.aggregate?.regime_analysis ?? {};

  return {
    configDiff,
    metrics,
    leftRegime,
    rightRegime,
    leftCfg,
    rightCfg,
    comparable: Boolean(left?.symbol && right?.symbol
      && String(left.symbol).toUpperCase() === String(right.symbol).toUpperCase()
      && String(left.strategy || '').toUpperCase() === String(right.strategy || '').toUpperCase()),
    formatConfigValue,
  };
}

export function formatCompareMetric(val, format) {
  if (val == null || val === '—') return '—';
  if (format === 'currency') return `$${Number(val).toFixed(2)}`;
  if (format === 'pct') return `${(Number(val) * 100).toFixed(1)}%`;
  if (format === 'ratio') return Number(val).toFixed(2);
  return String(val);
}
