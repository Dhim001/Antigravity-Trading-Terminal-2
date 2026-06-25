import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { queueMarketUpdate, resetMarketUpdateBatchForTests } from './marketUpdateBatch';

vi.mock('../lib/massiveMarket', () => ({
  isLiveMassiveMode: vi.fn(() => true),
}));

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

  it('applies immediately when not in Massive mode', async () => {
    const { isLiveMassiveMode } = await import('../lib/massiveMarket');
    isLiveMassiveMode.mockReturnValue(false);

    const apply = vi.fn();
    queueMarketUpdate({ BTCUSDT: { price: 99 } }, apply);

    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledWith({ BTCUSDT: { price: 99 } });
    expect(rafCb).toBeNull();
  });
});
