/**
 * Reference-count consumers that need L2 order book data in the Zustand store.
 * When zero, market ticks skip orderbook merges to reduce allocation churn.
 */

let consumers = 0;

/** Call on mount; returns cleanup for unmount. */
export function registerOrderBookConsumer() {
  consumers += 1;
  return () => {
    consumers = Math.max(0, consumers - 1);
  };
}

export function isOrderBookRetentionEnabled() {
  return consumers > 0;
}

/** @internal */
export function resetOrderBookInterestForTests() {
  consumers = 0;
}
