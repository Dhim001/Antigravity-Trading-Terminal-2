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

export function stripSymbolLabel(sym, compact = false) {
  if (!sym) return '—';
  const base = sym.replace('USDT', '');
  return compact ? base.slice(0, 5) : base;
}
