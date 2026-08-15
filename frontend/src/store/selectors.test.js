import { describe, expect, it } from 'vitest';
import { selectCashTotal } from './selectors';

describe('selectCashTotal', () => {
  it('sums only unlocked USD and USDT', () => {
    const total = selectCashTotal({
      balances: {
        USD: { balance: 10_000, locked: 500 },
        USDT: { balance: 100_000, locked: 0 },
        BTC: { balance: 2.5, locked: 0 },
      },
    });
    expect(total).toBe(109_500);
  });

  it('returns 0 when balances missing', () => {
    expect(selectCashTotal({})).toBe(0);
  });
});
