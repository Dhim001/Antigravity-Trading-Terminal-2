/**
 * Batch WS market_update frames to one store flush per animation frame.
 * Keeps UI live (~60 Hz) while avoiding dozens of separate React commits/sec.
 */

import { useStore } from '../store/useStore';

/** Terminal modes with high-frequency synthetic or live tick streams. */
const RAF_BATCH_MODES = new Set([
  'LIVE_MASSIVE',
  'LIVE_IB',
  'LIVE_ALPACA',
  'SIMULATED',
]);

export function shouldBatchMarketUpdates(terminalMode) {
  return RAF_BATCH_MODES.has(terminalMode);
}

/** @type {Record<string, object> | null} */
let pending = null;
let rafId = null;

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

/**
 * @param {Record<string, object>} data
 * @param {(data: Record<string, object>) => void} apply
 */
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

  if (rafId != null) return;
  rafId = setTimeout(() => {
    rafId = null;
    const batch = pending;
    pending = null;
    if (batch && Object.keys(batch).length > 0) {
      apply(batch);
    }
  }, 250);
}

/** @internal */
export function resetMarketUpdateBatchForTests() {
  pending = null;
  if (rafId != null) {
    clearTimeout(rafId);
    rafId = null;
  }
}
