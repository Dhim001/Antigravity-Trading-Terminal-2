import { describe, expect, it } from 'vitest';
import { formatBacktestProgressLabel } from './BacktestProgressBar';

describe('formatBacktestProgressLabel', () => {
  it('clarifies Starting… when a background job id exists', () => {
    const label = formatBacktestProgressLabel(
      { pct: 0, phase: 'resolve', message: 'Starting backtest…' },
      { jobId: 'abcdef12-9999' },
    );
    expect(label).toMatch(/background job|waiting for worker/i);
  });

  it('clarifies bar-0 simulate as ML feature build', () => {
    const label = formatBacktestProgressLabel({
      pct: 3,
      phase: 'simulate',
      message: 'Simulating: bar 0/50000…',
    });
    expect(label).toMatch(/Building ML features/i);
  });

  it('keeps mid-run simulate messages and appends ETA', () => {
    const label = formatBacktestProgressLabel({
      pct: 40,
      phase: 'simulate',
      message: 'Running simulation… bar 20000/50000',
      eta_sec: 90,
    });
    expect(label).toMatch(/Running simulation/i);
    expect(label).toMatch(/left/i);
  });

  it('uses phase label when message missing', () => {
    const label = formatBacktestProgressLabel({ pct: 10, phase: 'features' });
    expect(label).toMatch(/Building ML features/i);
  });
});
