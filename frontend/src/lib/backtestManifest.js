/**
 * Reproducibility manifest — JSON bundle for audit / re-run parity.
 */
import { backtestFingerprint } from './backtestDisplay';
import { buildBacktestAssumptionDetails } from './backtestAssumptions';
import { resolveBacktestSummary } from './metricComparison';

function stableStringify(obj) {
  return JSON.stringify(obj, Object.keys(obj).sort(), 2);
}

export function buildBacktestManifest({
  results,
  symbol,
  strategy,
  days,
  timeframe,
  config = {},
}) {
  if (!results) return null;

  const meta = results.meta ?? {};
  const summary = resolveBacktestSummary(results);
  const sym = symbol ?? meta.symbol ?? null;
  const strat = strategy ?? meta.strategy ?? null;
  const tf = timeframe ?? meta.timeframe ?? '1m';
  const dayCount = days ?? meta.days ?? null;
  const mergedConfig = { ...config, ...(meta.config ?? {}) };

  return {
    schema: 'backtest-manifest/v1',
    exported_at: new Date().toISOString(),
    run_id: results.run_id ?? null,
    git_revision: meta.git_revision ?? null,
    symbol: sym,
    strategy: strat,
    days: dayCount,
    timeframe: tf,
    sim_mode: results.sim_mode ?? meta.sim_mode ?? 'live_aligned',
    live_parity: results.live_parity ?? meta.live_parity ?? null,
    config_fingerprint: backtestFingerprint({
      symbol: sym,
      strategy: strat,
      days: dayCount,
      timeframe: tf,
      config: mergedConfig,
    }),
    data_slice: {
      oldest: meta.oldest ?? null,
      newest: meta.newest ?? null,
      bar_count: meta.count ?? null,
      oos_pct: meta.oos_pct ?? null,
      walk_forward: Boolean(meta.walk_forward),
      train_pct: meta.train_pct ?? null,
    },
    assumptions: buildBacktestAssumptionDetails(results),
    summary: {
      total_pnl: summary.total_pnl,
      win_rate: summary.win_rate,
      total_trades: summary.total_trades,
      max_drawdown: summary.max_drawdown,
      profit_factor: summary.profit_factor,
      sharpe_ratio: summary.sharpe_ratio,
      blocked_entries: results.summary?.blocked_entries ?? 0,
      filter_rejects: results.summary?.filter_rejects ?? {},
      blocked_events_total: results.summary?.blocked_events_total ?? 0,
    },
    costs: results.costs ?? {},
    job_tier: meta.job_tier ?? null,
    estimated_sec: meta.estimated_sec ?? null,
    execution_runtime: results.execution_runtime ?? null,
    config: mergedConfig,
  };
}

export function exportBacktestManifest(opts) {
  const manifest = buildBacktestManifest(opts);
  if (!manifest) {
    return { ok: false, error: 'No backtest results to export' };
  }

  const sym = String(opts.symbol ?? manifest.symbol ?? 'sym').replace(/[^\w.-]+/g, '_');
  const strat = String(opts.strategy ?? manifest.strategy ?? 'strategy').replace(/[^\w.-]+/g, '_');
  const date = new Date().toISOString().slice(0, 10);
  const filename = `backtest_manifest_${sym}_${strat}_${date}.json`;
  const blob = new Blob([stableStringify(manifest)], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
  return { ok: true, filename };
}
