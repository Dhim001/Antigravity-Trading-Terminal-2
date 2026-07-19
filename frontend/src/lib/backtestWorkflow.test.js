/** @vitest-environment node */
import { describe, expect, it } from 'vitest';
import { trimBacktestPayload, slimBacktestForDock } from './backtestSlim';
import { diffBacktestConfigs } from './backtestConfigDiff';
import { formatRunEstimate } from './backtestRunEstimate';

describe('backtestSlim', () => {
  it('downsamples equity curve', () => {
    const curve = Array.from({ length: 5000 }, (_, i) => ({ t: i, v: i }));
    const out = trimBacktestPayload({ equity_curve: curve });
    expect(out.equity_curve.length).toBeLessThanOrEqual(2000);
  });

  it('slim dock preview caps trades', () => {
    const trades = Array.from({ length: 100 }, (_, i) => ({ id: i }));
    const slim = slimBacktestForDock({ total_pnl: 10, trades, trade_count: 100 });
    expect(slim.trades).toHaveLength(10);
    expect(slim.trades_total).toBe(100);
  });

  it('trimBacktestPayload caps sweep leaderboard', () => {
    const results = Array.from({ length: 80 }, (_, i) => ({ i }));
    const out = trimBacktestPayload({ sweep: { results } });
    expect(out.sweep.results).toHaveLength(48);
    expect(out.sweep.results_truncated).toBe(80);
  });
});

describe('backtestConfigDiff', () => {
  it('finds changed keys', () => {
    const rows = diffBacktestConfigs(
      { min_confidence: 0.55, allocation: 1000 },
      { min_confidence: 0.65, allocation: 1000 },
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].key).toBe('min_confidence');
  });
});

describe('backtestRunEstimate', () => {
  it('formats portfolio estimate', () => {
    const label = formatRunEstimate({
      days: 7,
      portfolioSymbols: ['AAPL', 'MSFT', 'NVDA'],
    });
    expect(label).toMatch(/Est\./);
    expect(label).toMatch(/symbol/i);
  });
});
