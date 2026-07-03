import { describe, it, expect } from 'vitest';
import {
  uniqueSymbols,
  defaultPortfolioSymbols,
  portfolioModeBlocked,
  canRunPortfolioBacktest,
  togglePortfolioSymbol,
  PORTFOLIO_BACKTEST_MAX,
} from './portfolioBacktest';

describe('portfolioBacktest', () => {
  it('dedupes and uppercases symbols', () => {
    expect(uniqueSymbols(['aapl', 'AAPL', ' msft '])).toEqual(['AAPL', 'MSFT']);
  });

  it('defaults from active + watchlist capped at max', () => {
    const wl = Array.from({ length: 12 }, (_, i) => `S${i}`);
    const out = defaultPortfolioSymbols('AAPL', wl);
    expect(out[0]).toBe('AAPL');
    expect(out.length).toBe(PORTFOLIO_BACKTEST_MAX);
  });

  it('blocks portfolio mode when oos or walk-forward', () => {
    expect(portfolioModeBlocked({ oos: true, walkForward: false })).toBe(true);
    expect(portfolioModeBlocked({ oos: false, walkForward: true })).toBe(true);
    expect(portfolioModeBlocked({ oos: false, walkForward: false })).toBe(false);
  });

  it('requires at least two symbols to run', () => {
    expect(canRunPortfolioBacktest(['AAPL'])).toBe(false);
    expect(canRunPortfolioBacktest(['AAPL', 'MSFT'])).toBe(true);
  });

  it('toggles symbol selection with cap', () => {
    const full = Array.from({ length: PORTFOLIO_BACKTEST_MAX }, (_, i) => `S${i}`);
    expect(togglePortfolioSymbol(full, 'NEW')).toEqual(full);
    expect(togglePortfolioSymbol(['AAPL', 'MSFT'], 'AAPL')).toEqual(['MSFT']);
  });
});
