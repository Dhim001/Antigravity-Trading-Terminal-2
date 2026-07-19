/**
 * Tier 4 — strategy-aware optimizer defaults (INDICATOR_STRATEGIES.md).
 */

import { getStrategyCategory, getMLSubtype } from '@/config/strategies';

/** Risk-adjusted default; avoids overfitting to raw PnL. */
export const DEFAULT_SWEEP_OBJECTIVE = 'calmar_ratio';

/** Suggested first params to sweep per strategy (2–3 keys). */
export const STRATEGY_SWEEP_DEFAULTS = {
  MACD_RSI: ['rsi_length', 'macd_slow', 'trailing_stop_percent'],
  BRS_SCALPING: ['bb_std', 'rsi_oversold', 'take_profit_percent'],
  SUPERTREND_ADX: ['adx_threshold', 'st_multiplier', 'block_elevated_vol'],
  VWAP_PULLBACK: ['rsi_overbought_gate', 'trailing_stop_percent'],
  DONCHIAN_BREAKOUT: ['breakout_length', 'exit_length', 'atr_confirm_mult'],
  ICT_SMC: ['sweep_lookback', 'fvg_min_gap_pct', 'ob_lookback'],
  MARKET_MAKING: ['spread_pct', 'max_skew', 'vol_shutdown_mult'],
  CHART_AGENT: ['min_confidence', 'trailing_stop_percent', 'require_trend_alignment'],
  ABSORPTION_AGENT: ['min_confidence', 'min_score', 'trailing_stop_percent'],
  ML_SIGNAL_BOOST: ['min_confidence', 'triple_barrier_atr_mult', 'trailing_stop_percent', 'gbm_learning_rate', 'gbm_max_depth'],
  LSTM_DIRECTION: ['lookback', 'min_confidence', 'trailing_stop_percent'],
  RL_PPO_AGENT: ['gamma', 'min_confidence', 'trailing_stop_percent'],
  TCN_MULTI_HORIZON: ['lookback', 'min_return', 'min_confidence'],
  VAE_REGIME_DETECTOR: ['anomaly_threshold', 'suppress_threshold', 'trailing_stop_percent'],
  TRANSFORMER_SIGNAL: ['lookback', 'min_confidence', 'trailing_stop_percent'],
  GNN_CROSS_ASSET: ['min_corr', 'min_confidence', 'trailing_stop_percent'],
  HYBRID_ENSEMBLE: ['ensemble_threshold', 'ensemble_weight_ml', 'trailing_stop_percent'],
};

const FALLBACK_SWEEP_KEYS = ['trailing_stop_percent', 'take_profit_percent'];

const ML_FALLBACK_SWEEP_KEYS = ['min_confidence', 'trailing_stop_percent'];
const AGENT_FALLBACK_SWEEP_KEYS = ['min_confidence', 'min_score', 'trailing_stop_percent'];

/**
 * Default sweep objective by strategy category.
 * @param {string | undefined} strategy
 */
export function getDefaultObjective(strategy) {
  const category = getStrategyCategory(strategy);
  if (category === 'ml') return 'robust_score';
  return DEFAULT_SWEEP_OBJECTIVE;
}

/**
 * Minimum trades filter for sweep leaderboard.
 * @param {string | undefined} strategy
 */
export function getDefaultMinTrades(strategy) {
  const category = getStrategyCategory(strategy);
  if (category === 'ml') return 5;
  if (category === 'agent') return 3;
  return 1;
}

/**
 * Enabled map for sweep checkboxes — only 2–3 strategy-specific params on.
 */
export function defaultSweepEnabled(strategy, paramDefs) {
  const strat = String(strategy || '').toUpperCase();
  const category = getStrategyCategory(strat);
  const preferred = STRATEGY_SWEEP_DEFAULTS[strat]
    || (category === 'ml' ? ML_FALLBACK_SWEEP_KEYS
      : category === 'agent' ? AGENT_FALLBACK_SWEEP_KEYS
        : FALLBACK_SWEEP_KEYS);
  const keys = new Set((paramDefs || []).map((d) => d.key));
  const enabled = {};
  for (const def of paramDefs || []) {
    enabled[def.key] = preferred.includes(def.key) && keys.has(def.key);
  }
  if (!Object.values(enabled).some(Boolean)) {
    const fallback = category === 'ml'
      ? ML_FALLBACK_SWEEP_KEYS
      : category === 'agent'
        ? AGENT_FALLBACK_SWEEP_KEYS
        : FALLBACK_SWEEP_KEYS;
    for (const key of fallback) {
      if (keys.has(key)) enabled[key] = true;
    }
  }
  return enabled;
}

export function isExploratorySweep(results) {
  const sweep = results?.sweep;
  const hasSweep = Boolean(sweep?.results?.length || sweep?.best_config);
  const hasWf = Boolean(results?.walk_forward);
  return hasSweep && !hasWf;
}

/** ML optimizer objectives — scored via row.ml_metrics in backend sweep/Optuna. */
export function getMlObjectiveOptions(baseOptions) {
  const extra = [
    { value: 'auc_roc', label: 'AUC-ROC (classification)' },
    { value: 'log_loss', label: 'Log loss (lower better; ranked as −loss)' },
    { value: 'alpha_decay_half_life', label: 'Alpha decay half-life' },
    { value: 'oos_is_ratio', label: 'OOS/IS ratio' },
  ];
  const seen = new Set(baseOptions.map((o) => o.value));
  return [...baseOptions, ...extra.filter((o) => !seen.has(o.value))];
}

export function getMlSubtypeSweepHint(strategy) {
  const subtype = getMLSubtype(strategy);
  if (subtype === 'rl') return 'Tune PPO policy thresholds and risk exits — training lives in Model Training.';
  if (subtype === 'unsupervised') return 'Tune VAE regime thresholds and risk exits.';
  return 'Tune inference hyperparameters and risk exits — retrain models in Model Training.';
}

/**
 * Hyperparameter sensitivity analysis for sweep results.
 *
 * Groups sweep results by each swept param, computes the coefficient of
 * variation (CV) of the objective for each param value, and flags params
 * where small changes cause large objective swings (CV > 0.3).
 *
 * @param {Array} sweepResults — Array of sweep result rows with `config` and objective score.
 * @param {string} objectiveKey — Key of the objective in each row (e.g. 'robust_score').
 * @param {string[]} sweptParams — List of param keys that were swept.
 * @returns {{ perParam: Array<{key: string, sensitivity: number, values: number, warning: boolean}>, bestIsOutlier: boolean }}
 */
export function getSensitivityAnalysis(sweepResults, objectiveKey, sweptParams) {
  if (!sweepResults?.length || !sweptParams?.length) {
    return { perParam: [], bestIsOutlier: false };
  }

  const results = sweepResults.filter((r) => r && (r[objectiveKey] != null || r.config));
  if (results.length < 3) {
    return { perParam: [], bestIsOutlier: false };
  }

  const getScore = (r) => Number(r[objectiveKey] ?? r.summary?.[objectiveKey] ?? 0);
  const getConfig = (r) => r.config || {};

  const perParam = [];

  for (const key of sweptParams) {
    // Group results by this param's value
    const groups = new Map();
    for (const r of results) {
      const val = String(getConfig(r)[key] ?? 'default');
      if (!groups.has(val)) groups.set(val, []);
      groups.get(val).push(getScore(r));
    }

    if (groups.size < 2) continue;

    // Compute mean score per group, then CV of those means
    const means = [];
    for (const scores of groups.values()) {
      const mean = scores.reduce((a, b) => a + b, 0) / scores.length;
      means.push(mean);
    }

    const globalMean = means.reduce((a, b) => a + b, 0) / means.length;
    const variance = means.reduce((a, m) => a + (m - globalMean) ** 2, 0) / means.length;
    const std = Math.sqrt(variance);
    const cv = Math.abs(globalMean) > 1e-8 ? std / Math.abs(globalMean) : 0;

    perParam.push({
      key,
      sensitivity: Math.round(cv * 1000) / 1000,
      values: groups.size,
      warning: cv > 0.3,
    });
  }

  // Sort by sensitivity descending
  perParam.sort((a, b) => b.sensitivity - a.sensitivity);

  // Check if the best config is an outlier (top score is >2σ above mean)
  const allScores = results.map(getScore);
  const mean = allScores.reduce((a, b) => a + b, 0) / allScores.length;
  const std = Math.sqrt(
    allScores.reduce((a, s) => a + (s - mean) ** 2, 0) / allScores.length,
  );
  const best = Math.max(...allScores);
  const bestIsOutlier = std > 1e-8 && (best - mean) / std > 2.0;

  return { perParam, bestIsOutlier };
}
