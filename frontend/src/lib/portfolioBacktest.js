/** Portfolio backtest symbol selection helpers. */

import {
  getBacktestClientTimeoutMs,
  formatBacktestTimeoutLabel,
} from './backtestTimeouts';

export const PORTFOLIO_BACKTEST_MAX = 8;
export const PORTFOLIO_BACKTEST_MIN = 2;

export function uniqueSymbols(symbols) {
  const seen = new Set();
  const out = [];
  for (const raw of symbols || []) {
    const sym = String(raw || '').trim().toUpperCase();
    if (!sym || seen.has(sym)) continue;
    seen.add(sym);
    out.push(sym);
  }
  return out;
}

export function defaultPortfolioSymbols(activeSymbol, watchlist) {
  return uniqueSymbols([activeSymbol, ...(watchlist || [])]).slice(0, PORTFOLIO_BACKTEST_MAX);
}

export function portfolioModeBlocked({ oos, walkForward }) {
  return Boolean(oos || walkForward);
}

export function canRunPortfolioBacktest(selectedSymbols) {
  return uniqueSymbols(selectedSymbols).length >= PORTFOLIO_BACKTEST_MIN;
}

export function togglePortfolioSymbol(selected, symbol, max = PORTFOLIO_BACKTEST_MAX) {
  const sym = String(symbol || '').trim().toUpperCase();
  if (!sym) return uniqueSymbols(selected);
  const current = uniqueSymbols(selected);
  if (current.includes(sym)) {
    return current.filter((s) => s !== sym);
  }
  if (current.length >= max) return current;
  return [...current, sym];
}

/** Estimated client wait for a portfolio run (matches backtestTimeouts scaling). */
export function estimatePortfolioTimeoutMs(symbolCount, { days = 7 } = {}) {
  const n = Array.isArray(symbolCount)
    ? uniqueSymbols(symbolCount).length
    : Math.max(0, Math.floor(Number(symbolCount) || 0));
  return getBacktestClientTimeoutMs({
    portfolioSymbolCount: n,
    days,
  });
}

export function formatPortfolioRunEstimate(symbolCount, { days = 7 } = {}) {
  const n = uniqueSymbols(symbolCount).length;
  if (n < PORTFOLIO_BACKTEST_MIN) return null;
  const ms = estimatePortfolioTimeoutMs(n, { days });
  return `Est. wait ~${formatBacktestTimeoutLabel(ms)} (${n} symbols)`;
}
