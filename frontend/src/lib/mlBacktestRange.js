/**
 * ML Algo backtest range — holdout sentinel vs free day windows.
 * Keep UI selection aligned with what the backend actually replays.
 */

export const ML_BACKTEST_RANGE_HOLDOUT = 'holdout';

/** Free-range options for ML (no bare 7d — that was the silent-remap trap). */
export const ML_FREE_RANGE_DAYS = ['14', '30', '90', '180', '365'];

/** Shared TA / non-ML options. */
export const TA_BACKTEST_RANGE_DAYS = ['7', '14', '30', '90', '180', '365'];

/** Mirror backend `default_holdout_days` when calendar is missing. */
export function estimateHoldoutDays(months = 3) {
  const m = Number(months);
  const monthsSafe = Number.isFinite(m) && m > 0 ? m : 3;
  if (monthsSafe <= 1) return 7;
  return Math.min(60, Math.max(14, Math.round(monthsSafe * 30 * 0.15)));
}

export function resolveHoldoutDaysFromStatus(status, botConfig = {}) {
  const cal = status?.data_calendar;
  if (cal?.holdout_days != null) {
    const n = Number(cal.holdout_days);
    if (Number.isFinite(n) && n > 0) return Math.max(1, Math.min(365, Math.round(n)));
  }
  if (botConfig?.holdout_days != null) {
    const n = Number(botConfig.holdout_days);
    if (Number.isFinite(n) && n > 0) return Math.max(1, Math.min(365, Math.round(n)));
  }
  return estimateHoldoutDays(botConfig?.training_window_months ?? 3);
}

/**
 * When switching to an ML strategy, coerce legacy "7" into the holdout sentinel.
 */
export function coerceBacktestDaysForStrategy(days, { isMl } = {}) {
  const raw = days == null ? '7' : String(days);
  if (!isMl) {
    if (raw === ML_BACKTEST_RANGE_HOLDOUT) return '14';
    return raw;
  }
  if (raw === '7') return ML_BACKTEST_RANGE_HOLDOUT;
  return raw;
}

export function isHoldoutBacktestDays(days) {
  return String(days) === ML_BACKTEST_RANGE_HOLDOUT;
}

/**
 * Resolve numeric days + config flag for RUN_BACKTEST.
 * @returns {{ days: number, ml_backtest_range: 'holdout'|'free'|undefined }}
 */
export function resolveMlBacktestDaysPayload(backtestDays, holdoutDays, { isMl } = {}) {
  if (!isMl) {
    return {
      days: parseInt(String(backtestDays), 10) || 7,
      ml_backtest_range: undefined,
    };
  }
  const hd = Math.max(1, Math.min(365, Number(holdoutDays) || 14));
  if (isHoldoutBacktestDays(backtestDays)) {
    return { days: hd, ml_backtest_range: 'holdout' };
  }
  const days = parseInt(String(backtestDays), 10);
  return {
    days: Number.isFinite(days) && days > 0 ? days : hd,
    ml_backtest_range: 'free',
  };
}

export function mlBacktestRangeHint({ isMl, backtestDays, holdoutDays } = {}) {
  if (!isMl) return null;
  const hd = Number(holdoutDays) || 14;
  if (isHoldoutBacktestDays(backtestDays)) {
    return `Runs locked holdout (${hd}d) — not nested inside train FIT.`;
  }
  const days = parseInt(String(backtestDays), 10);
  return `Free ${days || '?'}d window (may overlap FIT — check in_sample flag on results).`;
}
