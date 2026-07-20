/** @vitest-environment node */
import { describe, it, expect } from 'vitest';
import { updateLiveSeriesCache } from './chartHelpers';

describe('updateLiveSeriesCache in-place mutation', () => {
  it('mutates last OHLC tuple without replacing main/volume arrays', () => {
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

    bars[1] = { time: 2, open: 1.5, high: 3, low: 1.4, close: 2.8, volume: 20 };
    updateLiveSeriesCache(cache, bars, 'candles', active, theme);

    expect(cache.main).toBe(mainRef);
    expect(cache.volume).toBe(volRef);
    expect(cache.main[1]).toBe(ohlcRef);
    expect(cache.main[1]).toEqual([1.5, 2.8, 1.4, 3]);
    expect(cache.volume[1].value).toBe(20);
  });
});
