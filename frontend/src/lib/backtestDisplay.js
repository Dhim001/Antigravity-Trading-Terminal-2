/** Display helpers for backtest results and deploy parity checks. */

import { getOldestBarTime, toUnixSeconds } from '../services/candleBuffer';
import { fetchOlderCandles } from '../api/endpoints';
import { normalizeDirectionMode } from './botConfigDisplay';

export const BACKTEST_OVERLAY_EVENT = 'backtest-overlay-changed';

export function normalizeTradingSymbol(symbol) {
  return String(symbol ?? '').trim().toUpperCase();
}

export function symbolsMatch(a, b) {
  const left = normalizeTradingSymbol(a);
  const right = normalizeTradingSymbol(b);
  return Boolean(left && right && left === right);
}

export function notifyBacktestOverlayChanged() {
  window.dispatchEvent(new CustomEvent(BACKTEST_OVERLAY_EVENT));
}

const CHART_HISTORY_CHUNK = 1000;
const CHART_HISTORY_MAX_LOOPS = 24;

/** Prepend archived candles until chart covers the backtest period (or archive exhausted). */
export async function ensureBacktestChartHistory(symbol, meta) {
  const targetOldest = meta?.oldest;
  if (!targetOldest || !symbol) return 0;

  let total = 0;
  for (let i = 0; i < CHART_HISTORY_MAX_LOOPS; i += 1) {
    const oldest = getOldestBarTime(symbol);
    const oldestSec = toUnixSeconds(oldest);
    if (oldestSec != null && oldestSec <= targetOldest) break;

    const anchor = oldestSec ?? Math.floor(Date.now() / 1000);
    const from = anchor - CHART_HISTORY_CHUNK * 60;
    const to = anchor - 60;
    const added = await fetchOlderCandles(symbol, from, to, 'auto');
    if (added <= 0) break;
    total += added;
  }
  return total;
}

export function resolveBacktestRange(meta = {}) {
  const requested = meta.days_requested ?? meta.days ?? null;
  const effective = meta.effective_days ?? requested;

  let replayedDays = meta.replayed_days;
  if (replayedDays == null && meta.oldest != null && meta.newest != null) {
    replayedDays = Math.max(0, (meta.newest - meta.oldest) / 86400);
  }

  const hasMismatch = requested != null
    && replayedDays != null
    && replayedDays < requested * 0.9;

  return {
    requested,
    effective,
    replayedDays,
    hasMismatch,
    rangeNote: meta.range_note ?? null,
    timeframeNote: meta.timeframe_note ?? null,
  };
}

function formatReplayedSpan(replayedDays) {
  if (replayedDays == null) return '—';
  if (replayedDays >= 1) {
    const rounded = replayedDays >= 10
      ? Math.round(replayedDays)
      : Number(replayedDays.toFixed(1));
    return `~${rounded} day${rounded === 1 ? '' : 's'}`;
  }
  return `~${(replayedDays * 24).toFixed(1)} hours`;
}

export function formatBacktestRangeLabel(meta, { fallbackDays } = {}) {
  const { requested, replayedDays, hasMismatch } = resolveBacktestRange(meta);

  if (replayedDays != null && hasMismatch) {
    const requestedLabel = requested ?? fallbackDays;
    return `${formatReplayedSpan(replayedDays)} (requested ${requestedLabel}d)`;
  }
  if (requested != null) return `${requested} day${requested === 1 ? '' : 's'}`;
  if (fallbackDays != null) return `${fallbackDays} day${fallbackDays === 1 ? '' : 's'}`;
  return '—';
}

export function formatBacktestDaysChip(meta, fallbackDays) {
  const { requested, replayedDays, hasMismatch } = resolveBacktestRange(meta);
  if (hasMismatch && replayedDays != null) {
    if (replayedDays >= 1) {
      const rounded = Math.max(1, Math.round(replayedDays));
      return `~${rounded}d`;
    }
    return `~${(replayedDays * 24).toFixed(0)}h`;
  }
  return `${requested ?? fallbackDays}d`;
}

export function formatBacktestTitle(meta, { fallbackDays, fallbackTimeframe } = {}) {
  const tf = meta?.timeframe ?? fallbackTimeframe ?? '1m';
  const { requested, replayedDays, hasMismatch } = resolveBacktestRange(meta);

  if (hasMismatch && replayedDays != null) {
    if (replayedDays >= 1) {
      const rounded = Math.max(1, Math.round(replayedDays));
      return `${rounded}-Day · ${tf} Backtest`;
    }
    return `${(replayedDays * 24).toFixed(0)}h · ${tf} Backtest`;
  }
  const days = requested ?? fallbackDays ?? '?';
  return `${days}-Day · ${tf} Backtest`;
}

export function fmtBacktestRange(meta) {
  if (!meta?.oldest || !meta?.newest) return null;
  const from = new Date(meta.oldest * 1000);
  const to = new Date(meta.newest * 1000);
  const opts = { month: 'short', day: 'numeric', year: 'numeric' };
  return `${from.toLocaleDateString(undefined, opts)} → ${to.toLocaleDateString(undefined, opts)}`;
}

export function backtestFingerprint({
  symbol,
  strategy,
  days,
  timeframe,
  config = {},
  simMode,
}) {
  return JSON.stringify({
    symbol,
    strategy,
    days: String(days),
    timeframe,
    allocation: config.allocation,
    trailing_stop_percent: config.trailing_stop_percent,
    take_profit_percent: config.take_profit_percent,
    tp_mode: config.tp_mode,
    min_confidence: config.min_confidence,
    direction_mode: normalizeDirectionMode(config.direction_mode),
    sim_mode: String(simMode ?? config.sim_mode ?? 'live_aligned').toLowerCase(),
  });
}

export function isBacktestStale(snapshot, current) {
  if (!snapshot || !current) return false;
  return snapshot !== current;
}
