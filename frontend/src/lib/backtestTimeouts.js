/** Client-side guard while waiting for backtest_result / job completion. */

import { isDeepMlStrategy, isMlStrategy } from '@/config/strategies';

const DEFAULT_BACKTEST_TIMEOUT_MS = 120_000;
const DEFAULT_BACKTEST_CHART_AGENT_TIMEOUT_MS = 300_000;
const DEFAULT_BACKTEST_REASONING_TIMEOUT_MS = 900_000;
/** Parameter sweep + walk-forward (IS/OOS per fold) — well above the 2 min default guard. */
const DEFAULT_BACKTEST_WALK_FORWARD_TIMEOUT_MS = 900_000;
/** CHART_AGENT meta-label WF runs ~1 + (3 × folds) full replays — needs a longer guard. */
const DEFAULT_BACKTEST_META_LABEL_WF_TIMEOUT_MS = 600_000;
/**
 * ML strategy backtests — per-bar model inference is far slower than TA.
 * Aligned with Model Training panel budgets (train/validate).
 */
const DEFAULT_BACKTEST_ML_TIMEOUT_MS = 600_000; // 10 min — GBDT / ML_SIGNAL_BOOST
const DEFAULT_BACKTEST_ML_DEEP_TIMEOUT_MS = 900_000; // 15 min — LSTM/TCN/Transformer/VAE/GNN
const DEFAULT_BACKTEST_ML_RL_TIMEOUT_MS = 1_200_000; // 20 min — RL_PPO_AGENT
/** Extra budget per WF fold×combo when the strategy is ML. */
const ML_WALK_FORWARD_PER_FOLD_COMBO_MS = 180_000;

/** Portfolio backtest runs one full replay per symbol (sequential). */
export const PORTFOLIO_TIMEOUT_PER_SYMBOL_MS = 120_000;
export const PORTFOLIO_TIMEOUT_MIN_MS = 600_000;
export const PORTFOLIO_TIMEOUT_MIN_SYMBOLS = 2;

let _activeTimeoutId = null;

function readEnvMs(key, fallback) {
  const raw = import.meta.env[key];
  if (raw === undefined || raw === '') return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * Floor timeout for ML strategies (0 when not ML).
 * @param {string} strategy
 * @param {number} parsedDays
 * @param {{ walkForward?: boolean, folds?: number, combos?: number }} [extra]
 */
export function getMlBacktestTimeoutFloorMs(
  strategy,
  parsedDays = 7,
  { walkForward = false, folds = 1, combos = 1 } = {},
) {
  const key = String(strategy || '').toUpperCase();
  if (!key || !isMlStrategy(key)) return 0;

  let base;
  if (key === 'RL_PPO_AGENT') {
    base = readEnvMs('VITE_BACKTEST_ML_RL_TIMEOUT_MS', DEFAULT_BACKTEST_ML_RL_TIMEOUT_MS);
  } else if (isDeepMlStrategy(key)) {
    base = readEnvMs('VITE_BACKTEST_ML_DEEP_TIMEOUT_MS', DEFAULT_BACKTEST_ML_DEEP_TIMEOUT_MS);
  } else {
    base = readEnvMs('VITE_BACKTEST_ML_TIMEOUT_MS', DEFAULT_BACKTEST_ML_TIMEOUT_MS);
  }

  let ms = base + Math.max(0, parsedDays - 7) * 45_000;
  if (walkForward) {
    ms += Math.max(1, folds) * Math.max(1, combos) * ML_WALK_FORWARD_PER_FOLD_COMBO_MS;
  }
  return ms;
}

/**
 * @param {{
 *   reasoning?: boolean,
 *   metaLabelWalkForward?: boolean,
 *   walkForward?: boolean,
 *   rollingFolds?: number,
 *   comboCount?: number,
 *   chartAgent?: boolean,
 *   strategy?: string,
 *   days?: number,
 *   portfolioSymbolCount?: number,
 * }} [opts]
 * @returns {number}
 */
export function getBacktestClientTimeoutMs({
  reasoning = false,
  metaLabelWalkForward = false,
  walkForward = false,
  rollingFolds = 1,
  comboCount = 1,
  chartAgent = false,
  strategy = '',
  days = 7,
  portfolioSymbolCount = 0,
} = {}) {
  const parsedDays = Number(days) || 7;
  const extraPerDayMs = 30_000;
  const symbolCount = Math.max(0, Math.floor(Number(portfolioSymbolCount) || 0));
  const isChartAgent = chartAgent || String(strategy).toUpperCase() === 'CHART_AGENT';
  const folds = Math.max(1, Math.floor(Number(rollingFolds) || 1));
  const combos = Math.max(1, Math.min(12, Math.floor(Number(comboCount) || 1)));

  let ms;
  if (reasoning) {
    const base = readEnvMs('VITE_BACKTEST_REASONING_TIMEOUT_MS', DEFAULT_BACKTEST_REASONING_TIMEOUT_MS);
    ms = base + Math.max(0, parsedDays - 7) * extraPerDayMs;
  } else if (metaLabelWalkForward) {
    const base = readEnvMs(
      'VITE_BACKTEST_META_LABEL_WF_TIMEOUT_MS',
      DEFAULT_BACKTEST_META_LABEL_WF_TIMEOUT_MS,
    );
    ms = base + Math.max(0, parsedDays - 7) * 45_000;
  } else if (walkForward) {
    const base = readEnvMs(
      'VITE_BACKTEST_WALK_FORWARD_TIMEOUT_MS',
      DEFAULT_BACKTEST_WALK_FORWARD_TIMEOUT_MS,
    );
    ms = (
      base
      + Math.max(0, parsedDays - 7) * 45_000
      + folds * combos * 120_000
    );
  } else if (isChartAgent) {
    const base = readEnvMs(
      'VITE_BACKTEST_CHART_AGENT_TIMEOUT_MS',
      DEFAULT_BACKTEST_CHART_AGENT_TIMEOUT_MS,
    );
    ms = base + Math.max(0, parsedDays - 7) * 45_000;
  } else {
    const perSymbolMs = readEnvMs('VITE_BACKTEST_TIMEOUT_MS', DEFAULT_BACKTEST_TIMEOUT_MS);
    if (symbolCount >= PORTFOLIO_TIMEOUT_MIN_SYMBOLS) {
      ms = Math.max(
        PORTFOLIO_TIMEOUT_MIN_MS,
        perSymbolMs * symbolCount,
      );
    } else {
      ms = combos > 1 ? perSymbolMs * combos : perSymbolMs;
    }
  }

  const mlFloor = getMlBacktestTimeoutFloorMs(strategy, parsedDays, {
    walkForward,
    folds,
    combos,
  });
  return Math.max(ms, mlFloor);
}

export function clearBacktestClientTimeout() {
  if (_activeTimeoutId != null) {
    clearTimeout(_activeTimeoutId);
    _activeTimeoutId = null;
  }
}

/**
 * @param {{
 *   reasoning?: boolean,
 *   metaLabelWalkForward?: boolean,
 *   walkForward?: boolean,
 *   rollingFolds?: number,
 *   comboCount?: number,
 *   chartAgent?: boolean,
 *   strategy?: string,
 *   days?: number,
 *   portfolioSymbolCount?: number,
 *   timeoutMs?: number,
 *   onTimeout: (timeoutMs: number) => void,
 * }} opts
 * @returns {number} scheduled timeoutMs
 */
export function scheduleBacktestClientTimeout({
  reasoning,
  metaLabelWalkForward,
  walkForward,
  rollingFolds,
  comboCount,
  chartAgent,
  strategy,
  days,
  portfolioSymbolCount,
  timeoutMs,
  onTimeout,
}) {
  clearBacktestClientTimeout();
  const resolvedMs = timeoutMs ?? getBacktestClientTimeoutMs({
    reasoning,
    metaLabelWalkForward,
    walkForward,
    rollingFolds,
    comboCount,
    chartAgent,
    strategy,
    days,
    portfolioSymbolCount,
  });
  _activeTimeoutId = setTimeout(() => {
    _activeTimeoutId = null;
    onTimeout(resolvedMs);
  }, resolvedMs);
  return resolvedMs;
}

export function formatBacktestTimeoutLabel(ms) {
  if (ms >= 60_000) return `${Math.round(ms / 60_000)} min`;
  return `${Math.round(ms / 1000)}s`;
}

/** User-facing hint when the client guard fires. */
export function backtestTimeoutHint({
  reasoning = false,
  metaLabelWalkForward = false,
  walkForward = false,
  chartAgent = false,
  strategy = '',
  portfolioSymbolCount = 0,
  timeoutMs,
} = {}) {
  const label = formatBacktestTimeoutLabel(timeoutMs);
  const isChartAgent = chartAgent || String(strategy).toUpperCase() === 'CHART_AGENT';
  const strat = String(strategy || '').toUpperCase();
  if (reasoning) {
    return `Backtest timed out after ${label} — increase VITE_BACKTEST_REASONING_TIMEOUT_MS, reduce days, or lower BACKTEST_REASONING_MAX_TRADES`;
  }
  if (metaLabelWalkForward) {
    return `Backtest timed out after ${label} — meta-label walk-forward runs multiple replays; increase VITE_BACKTEST_META_LABEL_WF_TIMEOUT_MS or reduce days`;
  }
  if (walkForward && isMlStrategy(strat)) {
    return `ML walk-forward timed out after ${label} — increase VITE_BACKTEST_ML_RL_TIMEOUT_MS / VITE_BACKTEST_ML_DEEP_TIMEOUT_MS / VITE_BACKTEST_WALK_FORWARD_TIMEOUT_MS, or reduce folds/days`;
  }
  if (walkForward) {
    return `Walk-forward timed out after ${label} — runs IS/OOS sweeps per fold; increase VITE_BACKTEST_WALK_FORWARD_TIMEOUT_MS, reduce folds/combos, or shorten days`;
  }
  if (strat === 'RL_PPO_AGENT') {
    return `RL_PPO backtest timed out after ${label} — increase VITE_BACKTEST_ML_RL_TIMEOUT_MS or reduce days`;
  }
  if (isDeepMlStrategy(strat)) {
    return `Deep ML backtest timed out after ${label} — increase VITE_BACKTEST_ML_DEEP_TIMEOUT_MS or reduce days`;
  }
  if (isMlStrategy(strat)) {
    return `ML backtest timed out after ${label} — increase VITE_BACKTEST_ML_TIMEOUT_MS or reduce days`;
  }
  if (isChartAgent) {
    return `CHART_AGENT backtest timed out after ${label} — increase VITE_BACKTEST_CHART_AGENT_TIMEOUT_MS or reduce days`;
  }
  if (Math.floor(Number(portfolioSymbolCount) || 0) >= PORTFOLIO_TIMEOUT_MIN_SYMBOLS) {
    return `Portfolio backtest timed out after ${label} — runs one replay per symbol; reduce symbols/days or increase VITE_BACKTEST_TIMEOUT_MS`;
  }
  return `Backtest timed out after ${label} — try a shorter range or increase VITE_BACKTEST_TIMEOUT_MS`;
}
