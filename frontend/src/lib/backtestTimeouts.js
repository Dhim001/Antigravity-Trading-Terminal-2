/** Client-side guard while waiting for backtest_result / job completion. */

const DEFAULT_BACKTEST_TIMEOUT_MS = 120_000;
const DEFAULT_BACKTEST_REASONING_TIMEOUT_MS = 900_000;

let _activeTimeoutId = null;

function readEnvMs(key, fallback) {
  const raw = import.meta.env[key];
  if (raw === undefined || raw === '') return fallback;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * @param {{ reasoning?: boolean, days?: number }} [opts]
 * @returns {number}
 */
export function getBacktestClientTimeoutMs({ reasoning = false, days = 7 } = {}) {
  const base = reasoning
    ? readEnvMs('VITE_BACKTEST_REASONING_TIMEOUT_MS', DEFAULT_BACKTEST_REASONING_TIMEOUT_MS)
    : readEnvMs('VITE_BACKTEST_TIMEOUT_MS', DEFAULT_BACKTEST_TIMEOUT_MS);

  if (!reasoning) return base;

  const parsedDays = Number(days) || 7;
  return base + Math.max(0, parsedDays - 7) * 30_000;
}

export function clearBacktestClientTimeout() {
  if (_activeTimeoutId != null) {
    clearTimeout(_activeTimeoutId);
    _activeTimeoutId = null;
  }
}

/**
 * @param {{ reasoning?: boolean, days?: number, timeoutMs?: number, onTimeout: (timeoutMs: number) => void }} opts
 * @returns {number} scheduled timeoutMs
 */
export function scheduleBacktestClientTimeout({ reasoning, days, timeoutMs, onTimeout }) {
  clearBacktestClientTimeout();
  const resolvedMs = timeoutMs ?? getBacktestClientTimeoutMs({ reasoning, days });
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
