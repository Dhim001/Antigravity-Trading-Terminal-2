/**
 * Batch WS market_update frames to one store flush per animation frame.
 * Keeps UI live (~60 Hz) while avoiding dozens of separate React commits/sec.
 *
 * IMPORTANT: never rely on requestAnimationFrame alone. Hidden Electron windows,
 * background tabs, and some GPU/fallback paths throttle or pause rAF — if the
 * scheduled callback never runs, a sticky rafId would drop live ticks forever.
 */

import { useStore } from '../store/useStore';

/** Terminal modes with high-frequency synthetic or live tick streams. */
const RAF_BATCH_MODES = new Set([
  'LIVE_MASSIVE',
  'LIVE_IB',
  'LIVE_ALPACA',
  'SIMULATED',
]);

/** Fallback when rAF is paused (hidden window / background tab). */
const FLUSH_TIMEOUT_MS = 32;

export function shouldBatchMarketUpdates(terminalMode) {
  return RAF_BATCH_MODES.has(terminalMode);
}

/** @type {Record<string, object> | null} */
let pending = null;
let rafId = null;
let timerId = null;
let flushScheduled = false;

const TICKER_FIELDS = ['price', 'change_24h', 'volume_24h', 'high_24h', 'low_24h'];

function mergeSymbol(target, symbol, info) {
  if (!info) return;
  const prev = target[symbol];
  if (!prev) {
    target[symbol] = { ...info, symbol };
    return;
  }
  for (const key of TICKER_FIELDS) {
    if (info[key] !== undefined) prev[key] = info[key];
  }
  if (info.candle !== undefined) prev.candle = info.candle;
  if (info.orderbook !== undefined) prev.orderbook = info.orderbook;
  prev.symbol = symbol;
}

function clearFlushHandles() {
  if (rafId != null) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
  if (timerId != null) {
    clearTimeout(timerId);
    timerId = null;
  }
}

function flushPending(apply) {
  if (!flushScheduled) return;
  flushScheduled = false;
  clearFlushHandles();
  const batch = pending;
  pending = null;
  if (batch && Object.keys(batch).length > 0) {
    apply(batch);
  }
}

export function queueMarketUpdate(data, apply) {
  if (!data || typeof data !== 'object') return;

  const mode = useStore.getState().terminalMode;
  if (!shouldBatchMarketUpdates(mode)) {
    apply(data);
    return;
  }

  if (!pending) pending = {};
  for (const [symbol, info] of Object.entries(data)) {
    mergeSymbol(pending, symbol, info);
  }

  if (flushScheduled) return;
  flushScheduled = true;

  const run = () => flushPending(apply);
  rafId = requestAnimationFrame(run);
  timerId = setTimeout(run, FLUSH_TIMEOUT_MS);
}

/** @internal */
export function resetMarketUpdateBatchForTests() {
  pending = null;
  flushScheduled = false;
  clearFlushHandles();
}
