import { describe, it, expect } from 'vitest';
import { CompactBarSeries } from './compactBarSeries';

function makeBars(count, start = 1_700_000_000) {
  return Array.from({ length: count }, (_, i) => ({
    time: start + i * 60,
    open: 100,
    high: 101,
    low: 99,
    close: 100 + i * 0.01,
    volume: 1,
  }));
}

describe('CompactBarSeries', () => {
  it('round-trips candles via toArray', () => {
    const candles = makeBars(3);
    const series = CompactBarSeries.fromCandles(candles);
    const out = series.toArray();
    expect(out).toHaveLength(3);
    expect(out[2].close).toBeCloseTo(100.02);
  });

  it('updates last bar in place without reallocating view', () => {
    const series = CompactBarSeries.fromCandles(makeBars(2));
    const view = series.toArray();
    series.updateLast({
      time: view[1].time,
      open: view[1].open,
      high: 105,
      low: 98,
      close: 104,
      volume: 2,
    });
    expect(view[1].close).toBe(104);
    expect(series.getLast().close).toBe(104);
  });

  it('pushes and shifts within max length', () => {
    const series = CompactBarSeries.fromCandles(makeBars(2));
    series.push({
      time: series.getLast().time + 60,
      open: 1,
      high: 2,
      low: 0.5,
      close: 1.5,
      volume: 1,
    });
    expect(series.length).toBe(3);
    series.shift();
    expect(series.length).toBe(2);
  });

  it('replaceFrom works without a pre-built view', () => {
    const series = CompactBarSeries.fromCandles(makeBars(2));
    series._invalidateView();
    expect(() => series.replaceFrom(makeBars(3, 1_700_000_120))).not.toThrow();
    expect(series.length).toBe(3);
    expect(series.toArray()[2].close).toBeCloseTo(100.02);
  });

  it('replaceFrom defers view rebuild until toArray', () => {
    const series = CompactBarSeries.fromCandles(makeBars(2));
    series.toArray();
    series.replaceFrom(makeBars(3, 1_700_000_120));
    expect(series._view).toBeNull();
    expect(series.toArray()).toHaveLength(3);
  });

  it('patches forming bar from live price', () => {
    const series = CompactBarSeries.fromCandles(makeBars(1));
    expect(series.patchLastFromPrice(150)).toBe(true);
    expect(series.getLast().close).toBe(150);
    expect(series.patchLastFromPrice(150)).toBe(false);
  });
});
