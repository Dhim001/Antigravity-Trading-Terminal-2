/**
 * Stable per-symbol price decimals for ticker / strip UI.
 * Intentionally symbol-based only — never derived from live price,
 * so formatting does not flip when price crosses $2.00 etc.
 */
export function getPriceDecimals(sym) {
  if (!sym) return 2;
  const upper = sym.toUpperCase();
  if (upper.includes('XRP') || upper.includes('ADA') || upper.includes('DOGE')) return 4;
  if (upper.includes('USDT')) {
    if (upper === 'BTCUSDT' || upper === 'ETHUSDT') return 2;
    return 4;
  }
  return 2;
}

export function formatPrice(sym, price) {
  if (price == null || Number.isNaN(price)) return '—';
  const dec = getPriceDecimals(sym);
  return price.toLocaleString(undefined, {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  });
}

/** Fixed-width change string for tape / watchlist cells. */
export function formatChangePct(change) {
  if (change == null || Number.isNaN(change)) return '—';
  const sign = change >= 0 ? '+' : '';
  return `${sign}${change.toFixed(2)}%`;
}

/** Absolute price change from current price and percent change vs reference open. */
export function absoluteChangeFromPct(price, changePct) {
  if (price == null || changePct == null || Number.isNaN(price) || Number.isNaN(changePct)) return null;
  if (changePct === 0) return 0;
  const denom = 1 + changePct / 100;
  if (!denom) return null;
  return price - price / denom;
}

export function formatChangeAbs(sym, price, changePct) {
  const delta = absoluteChangeFromPct(price, changePct);
  if (delta == null || Number.isNaN(delta)) return '—';
  const sign = delta >= 0 ? '+' : '';
  const dec = getPriceDecimals(sym);
  return `${sign}${delta.toLocaleString(undefined, {
    minimumFractionDigits: dec,
    maximumFractionDigits: dec,
  })}`;
}

export function formatVolCompact(v) {
  if (v == null || Number.isNaN(v) || v === 0) return '—';
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`;
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return v.toFixed(0);
}

export function stripSymbolLabel(sym, compact = false) {
  if (!sym) return '—';
  const base = sym.replace('USDT', '');
  return compact ? base.slice(0, 5) : base;
}
