import { describe, it, expect } from 'vitest';
import { buildBacktestAssumptions, buildBacktestAssumptionDetails } from './backtestAssumptions';
import {
  formatBacktestDaysChip,
  formatBacktestRangeLabel,
  formatBacktestTitle,
  resolveBacktestRange,
} from './backtestDisplay';
import { formatPortfolioRunEstimate } from './portfolioBacktest';

describe('buildBacktestAssumptions', () => {
  it('shows direction chip when not long-only', () => {
    const chips = buildBacktestAssumptions({
      sim_mode: 'live_aligned',
      meta: { config: { direction_mode: 'BOTH' } },
    });
    expect(chips.some((c) => c.key === 'direction' && c.label.includes('Both'))).toBe(true);
  });

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

  it('flags truncated replay window vs requested range', () => {
    const meta = {
      days: 7,
      days_requested: 7,
      replayed_days: 1.04,
      timeframe: '5m',
      count: 300,
      oldest: 1783036800,
      newest: 1783126800,
      range_note: 'Replayed ~1.04d of 7d requested',
    };
    const range = resolveBacktestRange(meta);
    expect(range.hasMismatch).toBe(true);
    expect(formatBacktestDaysChip(meta, 7)).toBe('~1d');
    expect(formatBacktestTitle(meta, { fallbackDays: 7, fallbackTimeframe: '5m' }))
      .toBe('1-Day · 5m Backtest');
    const details = buildBacktestAssumptionDetails({ meta });
    const data = details.sections.find((s) => s.id === 'data');
    expect(data.rows.some((r) => r.label === 'Requested range' && r.value === '7 days')).toBe(true);
    expect(data.rows.some((r) => r.label === 'Replayed span' && r.warn)).toBe(true);
    const chips = buildBacktestAssumptions({ meta });
    expect(chips.find((c) => c.key === 'range')?.warn).toBe(true);
  });

  it('shows requested range when replay matches', () => {
    const meta = { days: 7, days_requested: 7, replayed_days: 6.9, timeframe: '1m', count: 9000 };
    expect(formatBacktestRangeLabel(meta)).toBe('7 days');
    expect(resolveBacktestRange(meta).hasMismatch).toBe(false);
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
