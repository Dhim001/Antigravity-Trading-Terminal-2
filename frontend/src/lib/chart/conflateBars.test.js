import { describe, it, expect, beforeEach } from 'vitest';
import {
  nextPowerOf2,
  conflationFactor,
  conflateBars,
  conflateForDisplay,
  visibleBarCount,
  clearConflationCacheForTests,
} from './conflateBars';

function makeBars(n) {
  return Array.from({ length: n }, (_, i) => ({
    time: i * 60,
    open: 100 + i,
    high: 110 + i,
    low: 90 + i,
    close: 105 + i,
    volume: 10,
  }));
}

describe('conflateBars', () => {
  beforeEach(() => clearConflationCacheForTests());

  it('nextPowerOf2 rounds up', () => {
    expect(nextPowerOf2(1)).toBe(1);
    expect(nextPowerOf2(3)).toBe(4);
    expect(nextPowerOf2(8)).toBe(8);
  });

  it('factor is 1 when bars fit pixels', () => {
    expect(conflationFactor(400, 800)).toBe(1);
  });

  it('factor grows power-of-2 when zoomed out', () => {
    expect(conflationFactor(2500, 500)).toBe(8);
  });

  it('merges OHLC correctly', () => {
    const bars = makeBars(4);
    const out = conflateBars(bars, 2);
    expect(out).toHaveLength(2);
    expect(out[0].open).toBe(bars[0].open);
    expect(out[0].close).toBe(bars[1].close);
    expect(out[0].high).toBe(Math.max(bars[0].high, bars[1].high));
    expect(out[0].low).toBe(Math.min(bars[0].low, bars[1].low));
    expect(out[0].volume).toBe(20);
  });

  it('caches by key', () => {
    const bars = makeBars(100);
    const a = conflateForDisplay(bars, 10, 'BTC|1m');
    const b = conflateForDisplay(bars, 10, 'BTC|1m');
    expect(a.factor).toBeGreaterThan(1);
    expect(a.bars).toBe(b.bars);
  });

  it('visibleBarCount respects zoom window', () => {
    expect(visibleBarCount(1000, 0, 100)).toBe(1000);
    expect(visibleBarCount(1000, 50, 100)).toBe(500);
  });
});
