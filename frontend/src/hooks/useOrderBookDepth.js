import { useMemo } from 'react';
import { useStore } from '../store/useStore';

export function priceDecimalsFor(symbol, ticker) {
  if (
    symbol.includes('XRP') ||
    symbol.includes('ADA') ||
    symbol.includes('DOGE') ||
    (ticker && ticker.price < 2.0)
  ) {
    return 4;
  }
  return 2;
}

export function qtyDecimalsFor(symbol) {
  return symbol.includes('USDT') ? 4 : 2;
}

/** Auto bucket size from mid price. */
export function autoAggStep(midPrice, priceDecimals = 2) {
  if (!midPrice || midPrice <= 0) return 10 ** -priceDecimals;
  if (midPrice >= 10_000) return 10;
  if (midPrice >= 1_000) return 1;
  if (midPrice >= 100) return 0.1;
  if (midPrice >= 10) return 0.01;
  if (midPrice >= 1) return 0.001;
  return 10 ** -priceDecimals;
}

/** Merge adjacent price levels into buckets (same step for bids/asks). */
export function aggregateLevels(levels, step) {
  if (!levels?.length || !step || step <= 0) return levels || [];
  const buckets = new Map();
  for (const [price, qty] of levels) {
    const bucket = Math.round(price / step) * step;
    buckets.set(bucket, (buckets.get(bucket) || 0) + qty);
  }
  return Array.from(buckets.entries()).sort((a, b) => b[0] - a[0]);
}

function processSide(levels) {
  let cumulative = 0;
  return levels.map(([price, qty]) => {
    cumulative += qty;
    return { price, qty, cumulative };
  });
}

/**
 * Shared order-book depth view — spread, imbalance, cumulative sides.
 * @param {string} symbol
 * @param {{ maxLevels?: number, aggStep?: number | null }} opts
 */
export function useOrderBookDepth(symbol, { maxLevels = 24, aggStep = null } = {}) {
  const ob = useStore((state) => state.orderBooks[symbol]);
  const ticker = useStore((state) => state.tickerData[symbol]);

  const priceDecimals = priceDecimalsFor(symbol, ticker);
  const qtyDecimals = qtyDecimalsFor(symbol);

  return useMemo(() => {
    if (!ob?.bids?.length && !ob?.asks?.length) return null;

    const rawBids = aggStep ? aggregateLevels(ob.bids || [], aggStep) : (ob.bids || []);
    const rawAsks = aggStep
      ? aggregateLevels(ob.asks || [], aggStep).sort((a, b) => a[0] - b[0])
      : (ob.asks || []);

    const bids = processSide(rawBids);
    const asks = processSide(rawAsks);
    if (!bids.length && !asks.length) return null;

    const bidSlice = bids.slice(0, maxLevels);
    const askSlice = asks.slice(0, maxLevels);
    const maxCumulative = Math.max(
      bidSlice[bidSlice.length - 1]?.cumulative ?? 0,
      askSlice[askSlice.length - 1]?.cumulative ?? 0,
      1,
    );

    const bestBid = bids[0]?.price ?? 0;
    const bestAsk = asks[0]?.price ?? 0;
    const mid = bestBid && bestAsk
      ? (bestBid + bestAsk) / 2
      : (ticker?.price ?? bestBid ?? bestAsk);

    const bidVol = bidSlice.reduce((s, r) => s + r.qty, 0);
    const askVol = askSlice.reduce((s, r) => s + r.qty, 0);
    const totalVol = bidVol + askVol || 1;
    const bidPct = (bidVol / totalVol) * 100;
    const askPct = 100 - bidPct;

    return {
      bids: bidSlice,
      asks: askSlice,
      askRows: [...askSlice].reverse(),
      bidRows: bidSlice,
      maxCumulative,
      bestBid,
      bestAsk,
      mid,
      spread: bestAsk && bestBid ? bestAsk - bestBid : 0,
      spreadPct: bestAsk > 0 && bestBid ? ((bestAsk - bestBid) / bestAsk) * 100 : 0,
      bidPct,
      askPct,
      skew: bidPct - 50,
      priceDecimals,
      qtyDecimals,
    };
  }, [ob, ticker?.price, maxLevels, aggStep, priceDecimals, qtyDecimals]);
}
