/**
 * dockFormatters.js — shared formatting utilities for dock tab panels.
 * Extracted from ResizableDock.jsx to keep panel modules lightweight.
 */

/** Decimal count for sub-dollar symbols. */
export const priceDecimals = (sym, price) =>
  (sym?.includes('XRP') || sym?.includes('ADA') || sym?.includes('DOGE') || (price != null && price < 2.0)) ? 4 : 2;

/** Format a number with fixed decimals (locale-aware). */
export const fmtP = (n, d = 2) =>
  n == null ? '—' : Number(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });

/** Unrealized P&L — long and short (size sign drives direction). */
export function positionUnrealizedPnl(pos, mark) {
  const size = Number(pos?.size ?? 0);
  const entry = Number(pos?.avg_price ?? 0);
  const m = Number(mark ?? entry);
  return size * (m - entry);
}

/** Return % on deployed notional — same sign as unrealized P&L. */
export function positionReturnPct(pos, mark) {
  const size = Number(pos?.size ?? 0);
  const entry = Number(pos?.avg_price ?? 0);
  const costBasis = Math.abs(size) * entry;
  if (costBasis <= 0) return 0;
  return (positionUnrealizedPnl(pos, mark) / costBasis) * 100;
}

export const QUOTE_ASSETS = new Set(['USD', 'USDT']);

/** Strip quote suffix to get the base asset ticker. */
export const assetFromSymbol = (sym) =>
  sym.includes('USDT') && sym !== 'USDT' ? sym.replace('USDT', '') : sym;

/** Binance maps USD → USDT; skip duplicate row/total when values match. */
export const isQuoteAlias = (usd, usdt) =>
  Boolean(usd && usdt && usd.balance === usdt.balance && usd.locked === usdt.locked);

/**
 * Build a presentable balance view from raw OMS balance map.
 * @param {Record<string, {balance: number, locked: number}>} balances
 * @param {Record<string, number>} assetMark — current mark prices keyed by base asset
 */
export function buildBalanceView(balances, assetMark) {
  const usd = balances.USD;
  const usdt = balances.USDT;
  const alias = isQuoteAlias(usd, usdt);

  let cashAvailable = 0;
  let cashLocked = 0;
  if (usdt) {
    cashAvailable += usdt.balance - usdt.locked;
    cashLocked += usdt.locked;
  } else if (usd) {
    cashAvailable += usd.balance - usd.locked;
    cashLocked += usd.locked;
  }
  if (usd && !alias && usdt) {
    cashAvailable += usd.balance - usd.locked;
    cashLocked += usd.locked;
  }

  let holdingsUsd = 0;
  let totalEquity = 0;
  const rows = [];

  for (const [asset, bal] of Object.entries(balances)) {
    if (asset === 'USD' && alias) continue;
    if (Math.abs(bal.balance) < 1e-8 && bal.locked === 0) continue;

    const avail = bal.balance - bal.locked;
    const isQuote = QUOTE_ASSETS.has(asset);
    const mark = isQuote ? 1 : assetMark[asset];
    const usdValue = mark != null ? bal.balance * mark : null;

    if (usdValue != null) totalEquity += usdValue;
    if (!isQuote && usdValue != null) holdingsUsd += usdValue;

    rows.push({ asset, bal, avail, usdValue, isQuote });
  }

  rows.sort((a, b) => {
    if (a.isQuote !== b.isQuote) return a.isQuote ? -1 : 1;
    return (b.usdValue ?? 0) - (a.usdValue ?? 0);
  });

  return { rows, stats: { cashAvailable, cashLocked, holdingsUsd, totalEquity } };
}
