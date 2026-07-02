/** Client-side guard while waiting for backtest_result / job completion. */

const DEFAULT_BACKTEST_TIMEOUT_MS = 120_000;
const DEFAULT_BACKTEST_REASONING_TIMEOUT_MS = 900_000;
/** CHART_AGENT meta-label WF runs ~1 + (3 × folds) full replays — needs a longer guard. */
const DEFAULT_BACKTEST_META_LABEL_WF_TIMEOUT_MS = 600_000;

let _activeTimeoutId = null;

function readEnvMs(key, fallback) {
  const raw = import.meta.env[key];
  if (raw === undefined || raw === '') return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * @param {{ reasoning?: boolean, metaLabelWalkForward?: boolean, days?: number }} [opts]
 * @returns {number}
 */
export function getBacktestClientTimeoutMs({
  reasoning = false,
  metaLabelWalkForward = false,
  days = 7,
} = {}) {
  const parsedDays = Number(days) || 7;
  const extraPerDayMs = 30_000;

  if (reasoning) {
    const base = readEnvMs('VITE_BACKTEST_REASONING_TIMEOUT_MS', DEFAULT_BACKTEST_REASONING_TIMEOUT_MS);
    return base + Math.max(0, parsedDays - 7) * extraPerDayMs;
  }

  if (metaLabelWalkForward) {
    const base = readEnvMs(
      'VITE_BACKTEST_META_LABEL_WF_TIMEOUT_MS',
      DEFAULT_BACKTEST_META_LABEL_WF_TIMEOUT_MS,
    );
    return base + Math.max(0, parsedDays - 7) * 45_000;
  }

  return readEnvMs('VITE_BACKTEST_TIMEOUT_MS', DEFAULT_BACKTEST_TIMEOUT_MS);
}

export function clearBacktestClientTimeout() {
  if (_activeTimeoutId != null) {
    clearTimeout(_activeTimeoutId);
    _activeTimeoutId = null;
  }
}

/**
 * @param {{ reasoning?: boolean, metaLabelWalkForward?: boolean, days?: number, timeoutMs?: number, onTimeout: (timeoutMs: number) => void }} opts
 * @returns {number} scheduled timeoutMs
 */
export function scheduleBacktestClientTimeout({
  reasoning,
  metaLabelWalkForward,
  days,
  timeoutMs,
  onTimeout,
}) {
  clearBacktestClientTimeout();
  const resolvedMs = timeoutMs ?? getBacktestClientTimeoutMs({ reasoning, metaLabelWalkForward, days });
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
export function backtestTimeoutHint({ reasoning = false, metaLabelWalkForward = false, timeoutMs } = {}) {
  const label = formatBacktestTimeoutLabel(timeoutMs);
  if (reasoning) {
    return `Backtest timed out after ${label} — increase VITE_BACKTEST_REASONING_TIMEOUT_MS, reduce days, or lower BACKTEST_REASONING_MAX_TRADES`;
  }
  if (metaLabelWalkForward) {
    return `Backtest timed out after ${label} — meta-label walk-forward runs multiple replays; increase VITE_BACKTEST_META_LABEL_WF_TIMEOUT_MS or reduce days`;
  }
  return `Backtest timed out after ${label} — try a shorter range or increase VITE_BACKTEST_TIMEOUT_MS`;
}
