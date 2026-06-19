/** Display helpers for backtest results and deploy parity checks. */

import { getOldestBarTime, toUnixSeconds } from '../services/candleBuffer';
import { fetchOlderCandles } from '../api/endpoints';

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
  });
}

export function isBacktestStale(snapshot, current) {
  if (!snapshot || !current) return false;
  return snapshot !== current;
}
