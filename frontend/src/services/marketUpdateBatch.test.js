import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { queueMarketUpdate, resetMarketUpdateBatchForTests, shouldBatchMarketUpdates } from './marketUpdateBatch';
import { useStore } from '../store/useStore';

describe('shouldBatchMarketUpdates', () => {
  it('batches for high-frequency terminal modes', () => {
    expect(shouldBatchMarketUpdates('LIVE_MASSIVE')).toBe(true);
    expect(shouldBatchMarketUpdates('LIVE_IB')).toBe(true);
    expect(shouldBatchMarketUpdates('LIVE_ALPACA')).toBe(true);
    expect(shouldBatchMarketUpdates('SIMULATED')).toBe(true);
  });

  it('does not batch for unknown modes', () => {
    expect(shouldBatchMarketUpdates('OFFLINE')).toBe(false);
    expect(shouldBatchMarketUpdates(undefined)).toBe(false);
  });
});

describe('marketUpdateBatch', () => {
  /** @type {FrameRequestCallback | null} */
  let rafCb = null;

  beforeEach(() => {
    resetMarketUpdateBatchForTests();
    rafCb = null;
    vi.stubGlobal('requestAnimationFrame', (cb) => {
      rafCb = cb;
      return 1;
    });
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    useStore.setState({ terminalMode: 'LIVE_MASSIVE' });
  });

  afterEach(() => {
    resetMarketUpdateBatchForTests();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('merges multiple symbols into one apply call per frame', () => {
    const apply = vi.fn();
    queueMarketUpdate({ BTCUSDT: { price: 1 } }, apply);
    queueMarketUpdate({ ETHUSDT: { price: 2 } }, apply);

    expect(apply).not.toHaveBeenCalled();
    rafCb?.(0);

    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledWith({
      BTCUSDT: { price: 1, symbol: 'BTCUSDT' },
      ETHUSDT: { price: 2, symbol: 'ETHUSDT' },
    });
  });

  it('reuses symbol entry object when merging ticks in one frame', () => {
    const apply = vi.fn();
    queueMarketUpdate({ BTCUSDT: { price: 1 } }, apply);
    queueMarketUpdate({ BTCUSDT: { price: 2, change_24h: 0.5 } }, apply);
    rafCb?.(0);

    expect(apply).toHaveBeenCalledTimes(1);
    const entry = apply.mock.calls[0][0].BTCUSDT;
    expect(entry.price).toBe(2);
    expect(entry.change_24h).toBe(0.5);
    expect(entry.symbol).toBe('BTCUSDT');
  });

  it('applies immediately when batching is disabled for the terminal mode', () => {
    useStore.setState({ terminalMode: 'OFFLINE' });

    const apply = vi.fn();
    queueMarketUpdate({ BTCUSDT: { price: 99 } }, apply);

    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledWith({ BTCUSDT: { price: 99 } });
    expect(rafCb).toBeNull();
  });
});
