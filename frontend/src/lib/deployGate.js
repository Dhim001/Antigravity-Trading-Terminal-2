/** Deploy gate — forward-test before capital (mirrors backend deploy_gate.py). */

import { backtestFingerprint } from './backtestDisplay';
import { formatDirectionModeLabel, normalizeDirectionMode } from './botConfigDisplay';

export { normalizeDirectionMode, formatDirectionModeLabel };

export const DEPLOY_GATE_DEFAULTS = {
  minTrades: 1,
  minPnl: 0,
  minStabilityScore: 0.5,
  maxDrawdownWarnPct: 25,
};

function check({ id, level, ok, message, detail }) {
  return { id, level, ok, message, detail };
}

/** sim_mode from backtest wire payload (top-level or meta.config). */
export function extractBacktestSimMode(results) {
  if (!results) return 'live_aligned';
  const cfg = results.meta?.config ?? {};
  return String(results.sim_mode ?? cfg.sim_mode ?? 'live_aligned').toLowerCase();
}

/** direction_mode used for the backtest run (meta.config, sweep best, or explicit override). */
export function extractBacktestDirectionMode(results, backtestConfig) {
  if (backtestConfig?.direction_mode != null) {
    return normalizeDirectionMode(backtestConfig.direction_mode);
  }
  const cfg = results?.meta?.config ?? {};
  if (cfg.direction_mode != null) {
    return normalizeDirectionMode(cfg.direction_mode);
  }
  const best = results?.sweep?.best?.config;
  if (best?.direction_mode != null) {
    return normalizeDirectionMode(best.direction_mode);
  }
  return 'LONG_ONLY';
}

function symbolSlice(results, symbol) {
  if (!results?.portfolio || !symbol) return { scoped: results, symbolError: null };
  const sym = String(symbol).trim().toUpperCase();
  const row = (results.symbol_results || []).find(
    (r) => String(r.symbol || '').toUpperCase() === sym,
  );
  if (!row) return { scoped: results, symbolError: null, missingSymbol: sym };
  if (row.error) return { scoped: null, symbolError: row.error };
  return {
    scoped: {
      ...results,
      total_pnl: row.total_pnl,
      trade_count: row.trade_count,
      summary: row.summary || {},
      walk_forward: row.walk_forward,
      _portfolioSymbol: sym,
    },
    symbolError: null,
  };
}

function validateWalkForwardOos(results, { minPnl, minTrades, minStabilityScore }) {
  const wf = results?.walk_forward || {};
  const oos = wf.out_of_sample || {};
  const summary = results?.summary || {};
  const aggregate = wf.aggregate || {};

  let oosPnl = oos.total_pnl;
  if (oosPnl == null) oosPnl = results?.total_pnl;
  oosPnl = Number(oosPnl || 0);

  let oosTrades = oos.trade_count ?? oos.summary?.total_trades;
  if (oosTrades == null) oosTrades = summary.total_trades ?? results?.trade_count;
  oosTrades = Number(oosTrades || 0);

  const foldCount = Number(aggregate.fold_count || 1);
  const stability = aggregate.stability_score;

  const metrics = {
    oos_pnl: oosPnl,
    oos_trades: oosTrades,
    stability_score: stability,
    fold_count: foldCount,
  };

  if (oosTrades < minTrades) {
    return {
      ok: false,
      reason: `OOS trades ${oosTrades} below minimum ${minTrades}`,
      metrics,
    };
  }
  if (oosPnl < minPnl) {
    return {
      ok: false,
      reason: `OOS PnL ${oosPnl.toFixed(2)} below minimum ${minPnl.toFixed(2)}`,
      metrics,
    };
  }
  if (
    minStabilityScore > 0
    && foldCount >= 3
    && stability != null
    && Number(stability) < minStabilityScore
  ) {
    return {
      ok: false,
      reason: `OOS stability ${(Number(stability) * 100).toFixed(0)}% below ${(minStabilityScore * 100).toFixed(0)}%`,
      metrics,
    };
  }
  return { ok: true, reason: 'OK', metrics };
}

function finalize(checks, workflowStage, metrics = {}) {
  const blocking = checks.some((c) => c.level === 'block' && !c.ok);
  const failed = checks.filter((c) => c.level === 'block' && !c.ok);
  return {
    passed: !blocking,
    blocking,
    checks,
    workflow_stage: workflowStage,
    block_reason: blocking ? (failed[0]?.message || 'Deploy gate blocked') : null,
    metrics,
  };
}

/**
 * Evaluate deploy prerequisites for the current backtest results + config.
 */
export function evaluateDeployGate({
  results,
  symbol,
  config,
  backtestConfig,
  snapshot,
  days,
  timeframe,
  strategy,
  thresholds = DEPLOY_GATE_DEFAULTS,
} = {}) {
  const {
    minTrades = DEPLOY_GATE_DEFAULTS.minTrades,
    minPnl = DEPLOY_GATE_DEFAULTS.minPnl,
    minStabilityScore = DEPLOY_GATE_DEFAULTS.minStabilityScore,
    maxDrawdownWarnPct = DEPLOY_GATE_DEFAULTS.maxDrawdownWarnPct,
  } = thresholds;

  const checks = [];

  if (!results) {
    checks.push(check({
      id: 'backtest_linked',
      level: 'warn',
      ok: false,
      message: 'No backtest loaded',
      detail: 'Run a backtest before deploying capital, or confirm deploy anyway.',
    }));
    return finalize(checks, 'backtest');
  }

  const { scoped, symbolError, missingSymbol } = symbolSlice(results, symbol);
  if (symbolError) {
    checks.push(check({
      id: 'symbol_backtest',
      level: 'block',
      ok: false,
      message: `Portfolio backtest failed for ${symbol}`,
      detail: symbolError,
    }));
    return finalize(checks, 'blocked');
  }

  let metrics = {};

  if (scoped?.walk_forward) {
    const { ok, reason, metrics: wfMetrics } = validateWalkForwardOos(scoped, {
      minPnl,
      minTrades,
      minStabilityScore,
    });
    metrics = wfMetrics;
    checks.push(check({
      id: 'wf_oos',
      level: ok ? 'pass' : 'block',
      ok,
      message: ok ? 'Walk-forward OOS validation passed' : reason,
      detail: ok ? null : reason,
    }));
    if (wfMetrics.stability_score != null && wfMetrics.fold_count >= 3) {
      const stab = Number(wfMetrics.stability_score);
      const stabOk = stab >= minStabilityScore;
      checks.push(check({
        id: 'wf_stability',
        level: stabOk ? 'pass' : 'block',
        ok: stabOk,
        message: stabOk
          ? `OOS stability ${(stab * 100).toFixed(0)}% across ${wfMetrics.fold_count} folds`
          : `OOS stability ${(stab * 100).toFixed(0)}% below ${(minStabilityScore * 100).toFixed(0)}%`,
      }));
    }
  } else {
    const summary = scoped?.summary || {};
    const pnl = Number(scoped?.total_pnl ?? summary.total_pnl ?? 0);
    const trades = Number(scoped?.trade_count ?? summary.total_trades ?? 0);
    const oosNote = scoped?.meta?.oos_pct
      ? `Results use ${scoped.meta.oos_pct}% OOS holdout window`
      : null;
    metrics = { pnl, trades };

    const tradesOk = trades >= minTrades;
    checks.push(check({
      id: 'trade_count',
      level: tradesOk ? 'pass' : 'block',
      ok: tradesOk,
      message: tradesOk
        ? `${trades} trades (minimum ${minTrades})`
        : `Only ${trades} trades — need at least ${minTrades}`,
      detail: oosNote,
    }));

    const pnlOk = pnl >= minPnl;
    checks.push(check({
      id: 'pnl',
      level: pnlOk ? 'pass' : 'block',
      ok: pnlOk,
      message: pnlOk
        ? `PnL $${pnl.toFixed(2)} meets minimum $${minPnl.toFixed(2)}`
        : `PnL $${pnl.toFixed(2)} below minimum $${minPnl.toFixed(2)}`,
      detail: oosNote,
    }));

    const maxDd = summary.max_drawdown_pct;
    if (maxDd != null && Number(maxDd) > maxDrawdownWarnPct) {
      checks.push(check({
        id: 'max_drawdown',
        level: 'warn',
        ok: false,
        message: `Max drawdown ${Number(maxDd).toFixed(1)}% exceeds ${maxDrawdownWarnPct}% guideline`,
      }));
    }
  }

  if (missingSymbol) {
    checks.push(check({
      id: 'portfolio_symbol',
      level: 'warn',
      ok: false,
      message: `No per-symbol result for ${missingSymbol}`,
      detail: 'Gate used aggregate portfolio metrics.',
    }));
  }

  const corr = results.correlation_summary;
  if (corr?.warning) {
    checks.push(check({
      id: 'correlation',
      level: 'warn',
      ok: false,
      message: 'High portfolio correlation',
      detail: corr.message,
    }));
  }

  if (snapshot && config) {
    const current = backtestFingerprint({
      symbol,
      strategy,
      days: String(days),
      timeframe,
      config,
      simMode: backtestConfig?.sim_mode ?? extractBacktestSimMode(results),
    });
    if (snapshot !== current) {
      checks.push(check({
        id: 'config_fingerprint',
        level: 'warn',
        ok: false,
        message: 'Config changed since last backtest',
        detail: 'Re-run backtest or deploy anyway to accept drift risk.',
      }));
    }
  }

  if (results && config) {
    const simMode = extractBacktestSimMode(results);
    if (simMode === 'research') {
      checks.push(check({
        id: 'research_sim_mode',
        level: 'warn',
        ok: false,
        message: 'Backtest used research mode',
        detail: 'Research allows shorts without live risk gates — re-run live-aligned before deploy, or confirm bypass.',
      }));
    }

    const deployDir = normalizeDirectionMode(config.direction_mode);
    const btDir = extractBacktestDirectionMode(results, backtestConfig);
    if (deployDir !== btDir) {
      checks.push(check({
        id: 'direction_mode_mismatch',
        level: 'warn',
        ok: false,
        message: `Trade direction mismatch: backtest ${formatDirectionModeLabel(btDir)}, deploy ${formatDirectionModeLabel(deployDir)}`,
        detail: 'Live bot risk gate uses deploy direction — shorts may be blocked or allowed differently than the backtest.',
      }));
    } else if (deployDir === 'BOTH' && simMode === 'live_aligned') {
      checks.push(check({
        id: 'direction_mode_both',
        level: 'pass',
        ok: true,
        message: 'Trade direction: both long & short (live-aligned)',
      }));
    }
  }

  let stage = 'ready';
  if (checks.some((c) => c.level === 'block' && !c.ok)) stage = 'blocked';
  else if (scoped?.walk_forward) stage = 'oos_validated';

  return finalize(checks, stage, metrics);
}

export function buildDeployPayload({
  strategy,
  symbol,
  timeframe,
  allocation,
  executionMode,
  config,
  results,
  snapshot,
  days,
  forceDeploy = false,
}) {
  const fingerprint = backtestFingerprint({
    symbol,
    strategy,
    days: String(days),
    timeframe,
    config,
    simMode: config?.sim_mode,
  });
  return {
    strategy,
    symbol,
    timeframe,
    allocation,
    execution_mode: executionMode,
    force_deploy: forceDeploy,
    backtest_fingerprint: snapshot || fingerprint,
    config: {
      ...config,
      trailing_stop_percent: config?.trailing_stop_percent ?? 2,
      backtest_run_id: results?.run_id ?? config?.backtest_run_id,
      backtest_fingerprint: snapshot || fingerprint,
    },
  };
}
