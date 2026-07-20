import { describe, it, expect } from 'vitest';
import {
  toHeikinAshi,
  toRenko,
  toRenkoAligned,
  estimateRenkoBrickSize,
  applyCandleTransform,
  isCandleChartType,
  heikinAshiBar,
  renkoAlignedBar,
  patchLastTransformedMain,
} from './candleTransforms';
import { computeVolumeProfile } from './volumeProfile';
import {
  toPercentChangeSeries,
  alignComparisonSeries,
  correlation,
} from './comparison';
import {
  createDrawing,
  isValidDrawing,
  fibLevels,
  distToSegment,
  hitTestDrawings,
  drawingsToGraphic,
  timeToFractionalIndex,
} from './drawings';

function bar(time, open, high, low, close, volume = 100) {
  return { time, open, high, low, close, volume };
}

describe('candleTransforms — Heikin-Ashi', () => {
  it('first HA bar uses (open+close)/2 for open and OHLC/4 for close', () => {
    const bars = [bar(1, 10, 12, 9, 11)];
    const ha = toHeikinAshi(bars);
    expect(ha[0].open).toBe((10 + 11) / 2);
    expect(ha[0].close).toBe((10 + 12 + 9 + 11) / 4);
    expect(ha[0].high).toBeGreaterThanOrEqual(ha[0].open);
    expect(ha[0].low).toBeLessThanOrEqual(ha[0].close);
  });

  it('subsequent HA open averages previous HA open/close', () => {
    const bars = [bar(1, 10, 12, 9, 11), bar(2, 11, 13, 10, 12)];
    const ha = toHeikinAshi(bars);
    expect(ha[1].open).toBe((ha[0].open + ha[0].close) / 2);
  });

  it('preserves time and volume', () => {
    const bars = [bar(7, 10, 12, 9, 11, 555)];
    const ha = toHeikinAshi(bars);
    expect(ha[0].time).toBe(7);
    expect(ha[0].volume).toBe(555);
  });

  it('returns [] for empty input', () => {
    expect(toHeikinAshi([])).toEqual([]);
  });

  it('heikinAshiBar matches full-series last bar on forming update', () => {
    const bars = [
      bar(1, 10, 12, 9, 11),
      bar(2, 11, 13, 10, 12),
    ];
    const full = toHeikinAshi(bars);
    const last = heikinAshiBar(bars[1], full[0].open, full[0].close, false);
    expect(last.open).toBeCloseTo(full[1].open);
    expect(last.close).toBeCloseTo(full[1].close);
    expect(last.high).toBeCloseTo(full[1].high);
    expect(last.low).toBeCloseTo(full[1].low);
  });

  it('patchLastTransformedMain mutates only the last HA slot', () => {
    const bars = [
      bar(1, 10, 12, 9, 11),
      bar(2, 11, 13, 10, 12),
    ];
    const full = toHeikinAshi(bars);
    const cache = {
      main: full.map((c) => [c.open, c.close, c.low, c.high]),
    };
    const first = [...cache.main[0]];
    const updated = { ...bars[1], high: 14, close: 13 };
    expect(patchLastTransformedMain(cache, updated, 'heikin', 1)).toBe(true);
    expect(cache.main[0]).toEqual(first);
    const expected = heikinAshiBar(updated, full[0].open, full[0].close, false);
    expect(cache.main[1][1]).toBeCloseTo(expected.close);
    expect(cache.main[1][3]).toBeCloseTo(expected.high);
  });
});

describe('candleTransforms — Renko', () => {
  it('emits bricks only after a full brick move', () => {
    const size = 1;
    const bars = [
      bar(1, 100, 100, 100, 100),
      bar(2, 100, 100, 100, 100.5), // < brick, no emit
      bar(3, 100, 101, 100, 101),   // +1 brick
      bar(4, 101, 102, 101, 102),   // +1 brick
    ];
    const renko = toRenko(bars, size);
    expect(renko.length).toBe(2);
    expect(renko[0].brickDir).toBe(1);
    expect(renko[0].close).toBe(101);
    expect(renko[1].close).toBe(102);
  });

  it('handles downward bricks', () => {
    const bars = [bar(1, 100, 100, 100, 100), bar(2, 100, 100, 98, 98)];
    const renko = toRenko(bars, 1);
    expect(renko[renko.length - 1].brickDir).toBe(-1);
    expect(renko[renko.length - 1].close).toBe(98);
  });

  it('estimateRenkoBrickSize is positive for positive prices', () => {
    expect(estimateRenkoBrickSize([bar(1, 100, 100, 100, 200)])).toBeGreaterThan(0);
  });

  it('toRenkoAligned keeps one bar per source bar', () => {
    const bars = [
      bar(1, 100, 100, 100, 100),
      bar(2, 100, 100, 100, 100.5),
      bar(3, 100, 101, 100, 101),
      bar(4, 101, 102, 101, 102),
    ];
    const aligned = toRenkoAligned(bars, 1);
    expect(aligned.length).toBe(bars.length);
    expect(aligned[1].open).toBe(aligned[1].close); // no full brick → flat
    expect(aligned[2].brickDir).toBe(1);
    expect(aligned[3].close).toBe(102);
  });

  it('renkoAlignedBar + patch matches full aligned last bar', () => {
    const bars = [
      bar(1, 100, 100, 100, 100),
      bar(2, 100, 101, 100, 101.2),
    ];
    const aligned = toRenkoAligned(bars, 1);
    const cache = {
      main: aligned.map((c) => [c.open, c.close, c.low, c.high]),
    };
    const updated = { ...bars[1], close: 102.1, high: 102.1 };
    expect(patchLastTransformedMain(cache, updated, 'renko', 1, { renkoBrickSize: 1 })).toBe(true);
    const expected = renkoAlignedBar(updated, aligned[1].open, 1);
    expect(cache.main[1][0]).toBe(expected.open);
    expect(cache.main[1][1]).toBe(expected.close);
  });

  it('applyCandleTransform routes by chart type', () => {
    const bars = [bar(1, 10, 12, 9, 11)];
    expect(applyCandleTransform(bars, 'candle')).toBe(bars);
    expect(applyCandleTransform(bars, 'heikin')[0].close).toBeCloseTo(10.5);
    expect(isCandleChartType('renko')).toBe(true);
    expect(isCandleChartType('line')).toBe(false);
  });
});

describe('volumeProfile', () => {
  it('computes POC at the most-traded price bin', () => {
    const bars = [
      bar(1, 10, 11, 10, 10.5, 10),
      bar(2, 10, 11, 10, 10.5, 1000), // heavy volume around 10-11
      bar(3, 14, 15, 14, 14.5, 5),
    ];
    const vp = computeVolumeProfile(bars, { bins: 10 });
    expect(vp.poc).toBeGreaterThanOrEqual(10);
    expect(vp.poc).toBeLessThanOrEqual(11);
    expect(vp.maxVolume).toBeGreaterThan(0);
  });

  it('value area bounds straddle the POC', () => {
    const bars = Array.from({ length: 20 }, (_, i) =>
      bar(i, 10 + i * 0.1, 10 + i * 0.1 + 0.2, 10 + i * 0.1 - 0.1, 10 + i * 0.1, 100));
    const vp = computeVolumeProfile(bars, { bins: 12 });
    expect(vp.val).toBeLessThanOrEqual(vp.poc);
    expect(vp.vah).toBeGreaterThanOrEqual(vp.poc);
  });

  it('returns empty profile for no bars', () => {
    const vp = computeVolumeProfile([]);
    expect(vp.bins).toEqual([]);
    expect(vp.poc).toBeNull();
  });
});

describe('comparison', () => {
  it('percent change is zero at base and scales correctly', () => {
    const series = toPercentChangeSeries([bar(1, 0, 0, 0, 100), bar(2, 0, 0, 0, 110)]);
    expect(series[0]).toBe(0);
    expect(series[1]).toBeCloseTo(10);
  });

  it('aligns comparison series to primary timeline carrying forward', () => {
    const primary = [bar(10, 0, 0, 0, 1), bar(20, 0, 0, 0, 1), bar(30, 0, 0, 0, 1)];
    const compare = [bar(10, 0, 0, 0, 100), bar(30, 0, 0, 0, 120)];
    const aligned = alignComparisonSeries(primary, compare);
    expect(aligned[0]).toBe(0);
    expect(aligned[1]).toBe(0); // carried forward (no bar at t=20)
    expect(aligned[2]).toBeCloseTo(20);
  });

  it('correlation is 1 for identical series', () => {
    expect(correlation([1, 2, 3], [1, 2, 3])).toBeCloseTo(1);
  });

  it('correlation is -1 for inverted series', () => {
    expect(correlation([1, 2, 3], [3, 2, 1])).toBeCloseTo(-1);
  });
});

describe('drawings', () => {
  it('createDrawing + isValidDrawing for trendline', () => {
    const d = createDrawing('trendline', [{ time: 1, price: 10 }, { time: 5, price: 20 }]);
    expect(isValidDrawing(d)).toBe(true);
    expect(d.p1.price).toBe(10);
  });

  it('hline only needs a price', () => {
    const d = createDrawing('hline', [{ price: 42 }]);
    expect(isValidDrawing(d)).toBe(true);
    expect(d.price).toBe(42);
  });

  it('invalid drawing detected', () => {
    expect(isValidDrawing({ tool: 'trendline', p1: { time: 1, price: 1 } })).toBe(false);
    expect(isValidDrawing(null)).toBe(false);
  });

  it('fibLevels span from->to with standard ratios', () => {
    const levels = fibLevels(100, 200);
    expect(levels[0].price).toBe(100);
    expect(levels[levels.length - 1].price).toBe(200);
    const half = levels.find((l) => l.ratio === 0.5);
    expect(half.price).toBe(150);
  });

  it('distToSegment computes perpendicular distance', () => {
    expect(distToSegment(0, 5, [0, 0], [10, 0])).toBeCloseTo(5);
    expect(distToSegment(5, 0, [0, 0], [10, 0])).toBeCloseTo(0);
  });

  it('hitTestDrawings selects a nearby trendline', () => {
    const convert = (p) => [p.time, p.price];
    const d = createDrawing('trendline', [{ time: 0, price: 0 }, { time: 10, price: 0 }]);
    const hit = hitTestDrawings(5, 2, [d], convert);
    expect(hit).toBe(d.id);
    const miss = hitTestDrawings(5, 50, [d], convert);
    expect(miss).toBeNull();
  });

  it('timeToFractionalIndex maps times to ordinal indices', () => {
    const bars = [{ time: 100 }, { time: 200 }, { time: 300 }];
    expect(timeToFractionalIndex(bars, 100)).toBe(0);
    expect(timeToFractionalIndex(bars, 300)).toBe(2);
    expect(timeToFractionalIndex(bars, 150)).toBeCloseTo(0.5);
    expect(timeToFractionalIndex(bars, 250)).toBeCloseTo(1.5);
  });

  it('timeToFractionalIndex extrapolates outside the range', () => {
    const bars = [{ time: 100 }, { time: 200 }, { time: 300 }];
    expect(timeToFractionalIndex(bars, 50)).toBeCloseTo(-0.5);
    expect(timeToFractionalIndex(bars, 400)).toBeCloseTo(3);
  });

  it('timeToFractionalIndex handles empty + single-bar inputs', () => {
    expect(timeToFractionalIndex([], 100)).toBeNull();
    expect(timeToFractionalIndex([{ time: 100 }], 999)).toBe(0);
  });

  it('drawingsToGraphic emits an element per drawing', () => {
    const convert = (p) => [p.time, p.price];
    const d = createDrawing('trendline', [{ time: 0, price: 0 }, { time: 10, price: 10 }]);
    const els = drawingsToGraphic([d], convert, { priceToY: (p) => p });
    expect(els.length).toBe(1);
    expect(els[0].type).toBe('line');
  });
});
