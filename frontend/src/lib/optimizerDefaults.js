/**
 * Tier 4 — strategy-aware optimizer defaults (INDICATOR_STRATEGIES.md).
 */

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
};

const FALLBACK_SWEEP_KEYS = ['trailing_stop_percent', 'take_profit_percent'];

/**
 * Enabled map for sweep checkboxes — only 2–3 strategy-specific params on.
 */
export function defaultSweepEnabled(strategy, paramDefs) {
  const strat = String(strategy || '').toUpperCase();
  const preferred = STRATEGY_SWEEP_DEFAULTS[strat] || FALLBACK_SWEEP_KEYS;
  const keys = new Set((paramDefs || []).map((d) => d.key));
  const enabled = {};
  for (const def of paramDefs || []) {
    enabled[def.key] = preferred.includes(def.key) && keys.has(def.key);
  }
  if (!Object.values(enabled).some(Boolean)) {
    for (const key of FALLBACK_SWEEP_KEYS) {
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
