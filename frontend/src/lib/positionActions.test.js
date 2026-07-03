import { describe, it, expect } from 'vitest';
import {
  partialCloseQuantity,
  buildCloseOrderPayload,
  buildReverseOrderPayload,
  needsOrderConfirm,
} from './positionActions';

describe('partialCloseQuantity', () => {
  it('returns half size rounded down', () => {
    expect(partialCloseQuantity(1.234567, 0.5)).toBe(0.617283);
  });

  it('returns zero for flat', () => {
    expect(partialCloseQuantity(0, 0.5)).toBe(0);
  });
});

describe('buildCloseOrderPayload', () => {
  it('builds sell for long', () => {
    expect(buildCloseOrderPayload('AAPL', 10, 0.5)).toEqual({
      symbol: 'AAPL',
      type: 'MARKET',
      side: 'SELL',
      quantity: 5,
    });
  });

  it('builds buy for short', () => {
    expect(buildCloseOrderPayload('AAPL', -4, 1)).toEqual({
      symbol: 'AAPL',
      type: 'MARKET',
      side: 'BUY',
      quantity: 4,
    });
  });
});

describe('buildReverseOrderPayload', () => {
  it('doubles qty on sell for long reverse', () => {
    expect(buildReverseOrderPayload('BTCUSDT', 2)).toEqual({
      symbol: 'BTCUSDT',
      type: 'MARKET',
      side: 'SELL',
      quantity: 4,
    });
  });
});

describe('needsOrderConfirm', () => {
  it('requires confirm for large notional', () => {
    expect(needsOrderConfirm({ notional: 15_000 })).toBe(true);
  });

  it('requires confirm when SL/TP set', () => {
    expect(needsOrderConfirm({ notional: 100, stop_loss_price: 99 })).toBe(true);
  });

  it('skips confirm for small naked orders', () => {
    expect(needsOrderConfirm({ notional: 500 })).toBe(false);
  });
});
