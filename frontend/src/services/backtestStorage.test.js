import { describe, it, expect } from 'vitest';
import { buildBacktestOverlay } from '../lib/backtestSlim';

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
});
