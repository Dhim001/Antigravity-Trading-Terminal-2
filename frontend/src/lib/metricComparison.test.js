import { describe, expect, it } from 'vitest';
import {
  formatMetricDelta,
  formatSignedValue,
  metricDelta,
  resolveBacktestSummary,
} from './metricComparison';

describe('resolveBacktestSummary', () => {
  it('merges top-level PnL when summary field is missing', () => {
    const s = resolveBacktestSummary({ total_pnl: -820, summary: { win_rate: 42 } });
    expect(s.total_pnl).toBe(-820);
    expect(s.win_rate).toBe(42);
  });

  it('prefers summary PnL including negative values', () => {
    const s = resolveBacktestSummary({ total_pnl: -100, summary: { total_pnl: -500 } });
    expect(s.total_pnl).toBe(-500);
  });

  it('does not treat zero PnL as missing', () => {
    const s = resolveBacktestSummary({ total_pnl: -50, summary: { total_pnl: 0 } });
    expect(s.total_pnl).toBe(0);
  });

  it('reads PnL from nested results on API run rows', () => {
    const s = resolveBacktestSummary({
      id: 'run-1',
      summary: {},
      results: { total_pnl: -640, summary: { win_rate: 38 } },
    });
    expect(s.total_pnl).toBe(-640);
    expect(s.win_rate).toBe(38);
  });
});

describe('metricDelta', () => {
  it('marks less-negative PnL as improvement', () => {
    const d = metricDelta(-200, -800, { higherIsBetter: true });
    expect(d.delta).toBe(600);
    expect(d.improved).toBe(true);
  });

  it('marks more-negative PnL as worse', () => {
    const d = metricDelta(-900, -200, { higherIsBetter: true });
    expect(d.delta).toBe(-700);
    expect(d.worsened).toBe(true);
  });

  it('treats lower drawdown magnitude as improvement', () => {
    const d = metricDelta(12, 20, { higherIsBetter: false });
    expect(d.delta).toBe(-8);
    expect(d.improved).toBe(true);
  });
});

describe('formatMetricDelta', () => {
  it('keeps negative delta sign in text', () => {
    const { text, tone } = formatMetricDelta(-900, -200, { prefix: '$' });
    expect(text).toBe('-$700.00');
    expect(tone).toBe('down');
  });

  it('shows positive delta when current beat baseline on losses', () => {
    const { text, tone } = formatMetricDelta(-200, -800, { prefix: '$' });
    expect(text).toBe('+$600.00');
    expect(tone).toBe('up');
  });
});

describe('formatSignedValue', () => {
  it('preserves negative sign for currency-like values', () => {
    expect(formatSignedValue(-500, { prefix: '$' })).toBe('-$500.00');
  });
});
