/** Immediate chart paint hook — bypasses React state for forming-bar updates. */

const listeners = new Set();

/** @param {(symbol: string, price: number) => void} fn */
export function onLivePrice(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function emitLivePrice(symbol, price) {
  if (!symbol || price == null || !Number.isFinite(Number(price))) return;
  for (const fn of listeners) {
    try {
      fn(symbol, Number(price));
    } catch (_) {
      /* ignore */
    }
  }
}
