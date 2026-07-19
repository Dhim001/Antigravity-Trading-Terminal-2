import { describe, it, expect } from 'vitest';
import { buildBacktestOverlay, trimBacktestPayload } from '../lib/backtestSlim';

describe('buildBacktestOverlay equity cap', () => {
  it('downsamples equity curve to dock budget not full store budget', () => {
    const curve = Array.from({ length: 3000 }, (_, i) => ({ t: i, v: i }));
    const overlay = buildBacktestOverlay({
      run_id: 'r1',
      meta: { symbol: 'BTCUSDT' },
      trades: [],
      equity_curve: curve,
    });
    expect(overlay.equityCurve.length).toBeLessThanOrEqual(400);
  });

  it('caps overlay trades at MAX_OVERLAY_TRADES', () => {
    const trades = Array.from({ length: 800 }, (_, i) => ({ id: i }));
    const overlay = buildBacktestOverlay({
      run_id: 'r2',
      meta: { symbol: 'ETHUSDT' },
      trades,
      trades_total: 800,
      equity_curve: [],
    });
    expect(overlay.trades).toHaveLength(200);
    expect(overlay.tradesTotal).toBe(800);
  });
});

describe('trimBacktestPayload sweep restore', () => {
  it('caps sweep.results when loading a large optimization session', () => {
    const results = Array.from({ length: 120 }, (_, i) => ({ rank: i, total_pnl: i }));
    const out = trimBacktestPayload({
      sweep: { results, configs_tested: 120 },
    });
    expect(out.sweep.results).toHaveLength(48);
    expect(out.sweep.results_truncated).toBe(120);
  });
});
