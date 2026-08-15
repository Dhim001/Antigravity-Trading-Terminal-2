import { describe, it, expect } from 'vitest';
import { autoAggStep, footprintPriceStep } from './useOrderBookDepth';

describe('footprintPriceStep', () => {
  it('uses a coarse step for high-priced symbols so a 1h heatmap stays bounded', () => {
    expect(footprintPriceStep(100_000, 'BTCUSDT')).toBe(10);
    expect(footprintPriceStep(4_200, 'ETHUSDT')).toBe(1);
    expect(footprintPriceStep(180, 'AAPL')).toBe(0.1);
  });

  it('falls back to 10 when last is unknown instead of a sub-cent grid', () => {
    expect(footprintPriceStep(0, 'BTCUSDT')).toBe(10);
    expect(footprintPriceStep(Number.NaN, 'AAPL')).toBe(10);
  });

  it('matches autoAggStep once a last price exists', () => {
    expect(footprintPriceStep(12.5, 'SOLUSDT')).toBe(autoAggStep(12.5, 2));
  });
});
