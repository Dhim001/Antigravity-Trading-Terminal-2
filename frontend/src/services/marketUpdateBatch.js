/**
 * Batch WS market_update frames to one store flush per animation frame (Massive only).
 * Keeps UI live (~60 Hz) while avoiding 70+ separate React commits/sec.
 */

import { isLiveMassiveMode } from '../lib/massiveMarket';
import { useStore } from '../store/useStore';

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
  if (!isLiveMassiveMode(mode)) {
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
