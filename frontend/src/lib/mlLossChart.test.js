/** @vitest-environment node */
import { describe, expect, it } from 'vitest';
import {
  formatSignedPct,
  hoverIndexFromX,
  rollingMean,
  summarizeReturnCurve,
} from '../components/ml-lab/MlLossChart';

describe('formatSignedPct', () => {
  it('marks sign so gains and losses scan apart', () => {
    expect(formatSignedPct(0.35)).toBe('+0.35%');
    expect(formatSignedPct(-3.06)).toBe('−3.06%');
    expect(formatSignedPct(0)).toBe('0.00%');
  });
});

describe('rollingMean', () => {
  it('builds a 5-episode smoother', () => {
    const sma = rollingMean([1, 2, 3, 4, 5, 6], 5);
    expect(sma[0]).toBe(1);
    expect(sma[4]).toBe(3);
    expect(sma[5]).toBe(4);
  });
});

describe('summarizeReturnCurve', () => {
  it('summarizes last / mean / win rate for a glance strip', () => {
    const rows = [
      { primary: -3 },
      { primary: -1 },
      { primary: 0.2 },
      { primary: 0.1 },
      { primary: 0 },
    ];
    const s = summarizeReturnCurve(rows, { recentWindow: 3 });
    expect(s.n).toBe(5);
    expect(s.last).toBe(0);
    expect(s.min).toBe(-3);
    expect(s.max).toBe(0.2);
    expect(s.wins).toBe(2);
    expect(s.recentN).toBe(3);
    expect(s.recentMean).toBeCloseTo(0.1, 5);
  });

  it('labels a recovered-then-flat run as flat', () => {
    const rows = [];
    for (let i = 0; i < 10; i += 1) rows.push({ primary: -2.5 });
    for (let i = 0; i < 40; i += 1) rows.push({ primary: 0.01 });
    const s = summarizeReturnCurve(rows);
    expect(s.trend).toBe('flat');
    expect(Math.abs(s.recentMean)).toBeLessThan(0.05);
  });

  it('labels a rising late third as improving', () => {
    const rows = [
      ...Array.from({ length: 6 }, () => ({ primary: -1 })),
      ...Array.from({ length: 6 }, () => ({ primary: 0 })),
      ...Array.from({ length: 6 }, () => ({ primary: 1.2 })),
    ];
    expect(summarizeReturnCurve(rows).trend).toBe('improving');
  });
});

describe('hoverIndexFromX', () => {
  it('maps pointer x to the nearest episode', () => {
    expect(hoverIndexFromX(2, 2, 362, 50)).toBe(0);
    expect(hoverIndexFromX(362, 2, 362, 50)).toBe(49);
    expect(hoverIndexFromX(182, 2, 362, 50)).toBe(25);
  });
});
