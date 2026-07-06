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

function mergeSymbol(target, symbol, info) {
  if (!info) return;
  const prev = target[symbol];
  if (!prev) {
    target[symbol] = { ...info, symbol };
    return;
  }
  target[symbol] = {
    ...prev,
    ...info,
    symbol,
    orderbook: info.orderbook ?? prev.orderbook,
  };
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
  rafId = requestAnimationFrame(() => {
    rafId = null;
    const batch = pending;
    pending = null;
    if (batch && Object.keys(batch).length > 0) {
      apply(batch);
    }
  });
}

/** @internal */
export function resetMarketUpdateBatchForTests() {
  pending = null;
  if (rafId != null) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
}
