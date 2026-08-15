/**
 * dockFormatters.js — shared formatting utilities for dock tab panels.
 * Extracted from ResizableDock.jsx to keep panel modules lightweight.
 */

import { getPriceDecimals } from './formatPrice';

/** Decimal count — delegates to symbol-stable getPriceDecimals (no live-price flip). */
export const priceDecimals = (sym, _price) => getPriceDecimals(sym);

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

/**
 * Build a presentable balance view from raw OMS balance map.
 *
 * Dual paper ledger: USD (equities) and USDT (crypto) are independent —
 * both rows are shown and cash available is their unlocked sum. Do **not**
 * treat equal balances as a Binance alias (seeded dual ledgers often match).
 *
 * @param {Record<string, {balance: number, locked: number}>} balances
 * @param {Record<string, number>} assetMark — current mark prices keyed by base asset
 */
export function buildBalanceView(balances, assetMark) {
  const map = balances || {};
  let cashAvailable = 0;
  let cashLocked = 0;
  for (const asset of QUOTE_ASSETS) {
    const row = map[asset];
    if (!row) continue;
    const bal = Number(row.balance) || 0;
    const locked = Number(row.locked) || 0;
    cashAvailable += bal - locked;
    cashLocked += locked;
  }

  let holdingsUsd = 0;
  let totalEquity = 0;
  const rows = [];

  for (const [asset, bal] of Object.entries(map)) {
    if (!bal) continue;
    const balance = Number(bal.balance) || 0;
    const locked = Number(bal.locked) || 0;
    if (Math.abs(balance) < 1e-8 && locked === 0) continue;

    const avail = balance - locked;
    const isQuote = QUOTE_ASSETS.has(asset);
    const mark = isQuote ? 1 : assetMark?.[asset];
    const usdValue = mark != null ? balance * mark : null;

    if (usdValue != null) totalEquity += usdValue;
    if (!isQuote && usdValue != null) holdingsUsd += usdValue;

    rows.push({ asset, bal: { balance, locked }, avail, usdValue, isQuote });
  }

  rows.sort((a, b) => {
    if (a.isQuote !== b.isQuote) return a.isQuote ? -1 : 1;
    return (b.usdValue ?? 0) - (a.usdValue ?? 0);
  });

  return { rows, stats: { cashAvailable, cashLocked, holdingsUsd, totalEquity } };
}
