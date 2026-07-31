/** @vitest-environment node */
import { describe, it, expect } from 'vitest';
import { updateLiveSeriesCache, patchMainSlotInPlace } from './chartHelpers';

describe('updateLiveSeriesCache live paint refs', () => {
  it('mutates the last slot in place and re-refs outer arrays only on change', () => {
    const bars = [
      { time: 1, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
      { time: 2, open: 1.5, high: 2.5, low: 1.4, close: 2.0, volume: 12 },
    ];
    const cache = {};
    const active = { volume: true };
    const theme = {
      volume: { up: '#0f0', down: '#f00', opacity: 0.5 },
    };

    updateLiveSeriesCache(cache, bars, 'candles', active, theme, { forceRebuild: true });
    const mainRef = cache.main;
    const volRef = cache.volume;
    const ohlcRef = cache.main[1];

    // Quiet tick: nothing moved → same refs, no allocation, reports no change.
    const quietChanged = updateLiveSeriesCache(cache, bars, 'candles', active, theme);
    expect(quietChanged).toBe(false);
    expect(cache.main).toBe(mainRef);
    expect(cache.volume).toBe(volRef);

    // Changed tick: fresh outer refs so ECharts re-reads, but the OHLC slot
    // itself is mutated in place (no per-tick inner-array churn).
    bars[1] = { time: 2, open: 1.5, high: 3, low: 1.4, close: 2.8, volume: 20 };
    const changed = updateLiveSeriesCache(cache, bars, 'candles', active, theme);
    expect(changed).toBe(true);
    expect(cache.main).not.toBe(mainRef);
    expect(cache.volume).not.toBe(volRef);
    expect(cache.main[1]).toBe(ohlcRef);
    expect(cache.main[1]).toEqual([1.5, 2.8, 1.4, 3]);
    expect(cache.volume[1].value).toBe(20);
  });

  it('line charts mutate close in place and skip unchanged ticks', () => {
    const bars = [
      { time: 1, open: 1, high: 2, low: 0.5, close: 1.5, volume: 10 },
      { time: 2, open: 1.5, high: 2.5, low: 1.4, close: 2.0, volume: 12 },
    ];
    const cache = {};
    const active = { volume: false };

    updateLiveSeriesCache(cache, bars, 'line', active, null, { forceRebuild: true });
    const mainRef = cache.main;

    // High/low/volume moved but close did not → line series untouched.
    bars[1] = { ...bars[1], high: 9, low: 0.1, volume: 999 };
    expect(updateLiveSeriesCache(cache, bars, 'line', active, null)).toBe(false);
    expect(cache.main).toBe(mainRef);

    bars[1] = { ...bars[1], close: 2.5 };
    expect(updateLiveSeriesCache(cache, bars, 'line', active, null)).toBe(true);
    expect(cache.main).not.toBe(mainRef);
    expect(cache.main[1]).toBe(2.5);
  });
});

describe('patchMainSlotInPlace', () => {
  it('reports unchanged and keeps the reference when values match', () => {
    const main = [[1, 1.5, 0.5, 2]];
    const bar = { open: 1, close: 1.5, low: 0.5, high: 2 };
    const res = patchMainSlotInPlace(main, 0, bar, 'candles');
    expect(res.changed).toBe(false);
    expect(res.data).toBe(main);
  });

  it('mutates the slot in place and returns a sliced outer array on change', () => {
    const slot = [1, 1.5, 0.5, 2];
    const main = [slot, slot];
    const bar = { open: 1.5, close: 2.8, low: 1.4, high: 3 };
    const res = patchMainSlotInPlace(main, 1, bar, 'candles');
    expect(res.changed).toBe(true);
    expect(res.data).not.toBe(main);
    expect(res.data[1]).toBe(slot);
    expect(res.data[1]).toEqual([1.5, 2.8, 1.4, 3]);
  });

  it('replaces non-array slots (e.g. future padding) with a fresh OHLC cell', () => {
    const main = [[1, 1.5, 0.5, 2], '-'];
    const bar = { open: 1.5, close: 2.8, low: 1.4, high: 3 };
    const res = patchMainSlotInPlace(main, 1, bar, 'candles');
    expect(res.changed).toBe(true);
    expect(res.data[1]).toEqual([1.5, 2.8, 1.4, 3]);
  });

  it('handles line series and out-of-range indices safely', () => {
    const line = [1.5, 2.0];
    const same = patchMainSlotInPlace(line, 1, { close: 2.0 }, 'line');
    expect(same.changed).toBe(false);
    expect(same.data).toBe(line);
    const upd = patchMainSlotInPlace(line, 1, { close: 2.4 }, 'line');
    expect(upd.changed).toBe(true);
    expect(upd.data[1]).toBe(2.4);

    const oor = patchMainSlotInPlace(line, 5, { close: 3 }, 'line');
    expect(oor.changed).toBe(false);
    expect(oor.data).toBe(line);
  });
});
