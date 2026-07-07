import { describe, it, expect } from 'vitest';
import { positionUnrealizedPnl, positionReturnPct } from './dockFormatters';

describe('positionUnrealizedPnl', () => {
  it('profits long when mark rises', () => {
    expect(positionUnrealizedPnl({ size: 1, avg_price: 100 }, 110)).toBe(10);
  });

  it('loses short when mark rises', () => {
    expect(positionUnrealizedPnl({ size: -0.5677, avg_price: 1757.23 }, 1767.96)).toBeCloseTo(-6.09, 1);
  });
});

describe('positionReturnPct', () => {
  it('matches long price move', () => {
    expect(positionReturnPct({ size: 1, avg_price: 100 }, 110)).toBeCloseTo(10, 2);
  });

  it('is negative for losing short when mark rises', () => {
    const pct = positionReturnPct({ size: -0.5677, avg_price: 1757.23 }, 1767.96);
    expect(pct).toBeLessThan(0);
    expect(pct).toBeCloseTo(-0.61, 1);
  });

  it('sign matches unrealized P&L', () => {
    const pos = { size: -0.5, avg_price: 2000 };
    const mark = 2050;
    const pnl = positionUnrealizedPnl(pos, mark);
    const pct = positionReturnPct(pos, mark);
    expect(Math.sign(pnl)).toBe(Math.sign(pct));
  });
});
