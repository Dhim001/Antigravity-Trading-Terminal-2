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

describe('trade chart markers', () => {
  const base = 1_700_000_000;
  const candles = Array.from({ length: 8 }, (_, i) => ({
    time: base + i * 60,
    open: 100,
    high: 101,
    low: 99,
    close: 100.5,
    volume: 1,
  }));

  it('tradeSymbolsMatch accepts Alpaca wire crypto symbols', async () => {
    const { tradeSymbolsMatch } = await import('./chartHelpers');
    expect(tradeSymbolsMatch('BTC/USD', 'BTCUSDT')).toBe(true);
    expect(tradeSymbolsMatch('BTCUSD', 'BTCUSDT')).toBe(true);
    expect(tradeSymbolsMatch('AAPL', 'AAPL')).toBe(true);
    expect(tradeSymbolsMatch('ETHUSDT', 'BTCUSDT')).toBe(false);
  });

  it('emits category-index scatter points that survive conflation', async () => {
    const { buildTradeMarkers } = await import('./chartHelpers');
    const { conflateBars } = await import('./conflateBars');
    const trades = [{
      symbol: 'BTC/USD',
      status: 'FILLED',
      side: 'BUY',
      timestamp: (base + 3 * 60) * 1000,
      quantity: 0.01,
      average_fill_price: 100.2,
    }];
    const full = buildTradeMarkers(trades, 'BTCUSDT', candles, 60);
    expect(full).toHaveLength(1);
    expect(full[0].value[0]).toBe(3);

    const conflated = conflateBars(candles, 2);
    const markers = buildTradeMarkers(trades, 'BTCUSDT', conflated, 120);
    expect(markers).toHaveLength(1);
    expect(markers[0].value[0]).toBeGreaterThanOrEqual(0);
    expect(markers[0].value[0]).toBeLessThan(conflated.length);
    expect(Number.isFinite(markers[0].value[1])).toBe(true);
  });

  it('toSignalScatterPoint uses bar index not unix category key', async () => {
    const { toSignalScatterPoint } = await import('./chartHelpers');
    const pt = toSignalScatterPoint(candles, 2, 99.5, {
      value: 'BUY',
      symbol: 'circle',
      symbolSize: 8,
      itemStyle: { color: '#0f0' },
    });
    expect(pt.value).toEqual([2, 99.5]);
  });
});
