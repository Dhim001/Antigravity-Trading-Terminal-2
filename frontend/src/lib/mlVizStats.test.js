/** @vitest-environment node */
import { describe, expect, it } from 'vitest';
import { computeConfusionStats } from '../components/ConfusionMatrixGrid';

describe('computeConfusionStats', () => {
  it('computes accuracy and per-class F1', () => {
    const matrix = [
      [10, 1, 0],
      [2, 8, 1],
      [0, 1, 12],
    ];
    const stats = computeConfusionStats(matrix);
    expect(stats.total).toBe(35);
    expect(stats.accuracy).toBeCloseTo(30 / 35, 5);
    expect(stats.perClass).toHaveLength(3);
    expect(stats.perClass[0].label).toBe('BUY');
    expect(stats.perClass[0].precision).toBeCloseTo(10 / 12, 5);
  });

  it('handles empty matrix', () => {
    const stats = computeConfusionStats([]);
    expect(stats.accuracy).toBe(0);
    expect(stats.total).toBe(0);
  });
});
