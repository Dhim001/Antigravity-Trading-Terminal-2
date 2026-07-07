import { describe, it, expect } from 'vitest';
import {
  getBacktestClientTimeoutMs,
  PORTFOLIO_TIMEOUT_MIN_MS,
  PORTFOLIO_TIMEOUT_PER_SYMBOL_MS,
} from './backtestTimeouts';

describe('getBacktestClientTimeoutMs — portfolio scaling', () => {
  it('uses default single-symbol timeout when count < 2', () => {
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 0 })).toBe(120_000);
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 1 })).toBe(120_000);
  });

  it('enforces 10 min minimum for 2–5 symbols', () => {
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 2 })).toBe(PORTFOLIO_TIMEOUT_MIN_MS);
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 5 })).toBe(PORTFOLIO_TIMEOUT_MIN_MS);
  });

  it('scales 120s per symbol above the minimum', () => {
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 6 }))
      .toBe(PORTFOLIO_TIMEOUT_PER_SYMBOL_MS * 6);
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 8 }))
      .toBe(PORTFOLIO_TIMEOUT_PER_SYMBOL_MS * 8);
  });
});

describe('getBacktestClientTimeoutMs — walk-forward', () => {
  it('uses 15 min+ base for WF rigorous (30d, 3 folds, 1 combo)', () => {
    const ms = getBacktestClientTimeoutMs({
      walkForward: true,
      days: 30,
      rollingFolds: 3,
      comboCount: 1,
    });
    expect(ms).toBeGreaterThanOrEqual(900_000);
    expect(ms).toBe(900_000 + 23 * 45_000 + 3 * 1 * 120_000);
  });

  it('scales with fold and combo count', () => {
    const ms = getBacktestClientTimeoutMs({
      walkForward: true,
      days: 7,
      rollingFolds: 3,
      comboCount: 6,
    });
    expect(ms).toBe(900_000 + 3 * 6 * 120_000);
  });
});
