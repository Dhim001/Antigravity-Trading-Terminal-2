import { describe, expect, it } from 'vitest';
import { errorAffectsBacktestRun } from './dispatch';

describe('errorAffectsBacktestRun', () => {
  it('ignores chart analyst rate limits during backtest', () => {
    expect(errorAffectsBacktestRun('Rate limited — wait before analyzing this symbol again')).toBe(false);
  });

  it('treats unknown errors as backtest failures when running', () => {
    expect(errorAffectsBacktestRun('Insufficient candle data')).toBe(true);
    expect(errorAffectsBacktestRun('Backtest failed')).toBe(true);
  });
});
