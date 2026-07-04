import { describe, it, expect } from 'vitest';
import { buildBacktestAssumptions } from './backtestAssumptions';
import { formatPortfolioRunEstimate } from './portfolioBacktest';

describe('buildBacktestAssumptions', () => {
  it('flags research mode', () => {
    const chips = buildBacktestAssumptions({ sim_mode: 'research' });
    expect(chips.some((c) => c.warn && c.label.includes('Research'))).toBe(true);
  });

  it('shows live parity when enabled', () => {
    const chips = buildBacktestAssumptions({
      sim_mode: 'live_aligned',
      live_parity: true,
      meta: { days: 7, timeframe: '1m', count: 1000 },
      costs: { slippage_bps: 5, fee_bps: 10 },
    });
    expect(chips.length).toBeGreaterThan(2);
  });
});

describe('formatPortfolioRunEstimate', () => {
  it('returns estimate for 2+ symbols', () => {
    const label = formatPortfolioRunEstimate(['A', 'B', 'C'], { days: 7 });
    expect(label).toMatch(/Est\. wait/);
    expect(label).toMatch(/3 symbols/);
  });

  it('returns null for single symbol', () => {
    expect(formatPortfolioRunEstimate(['A'])).toBeNull();
  });
});
