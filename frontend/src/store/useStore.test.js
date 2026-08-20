/** @vitest-environment node */
import { describe, it, expect, beforeEach } from 'vitest';
import { useStore } from './useStore';

/** MEMORY_CENTRIC_REVIEW #38 — tickerData / tickData must not grow unbounded. */
describe('tickerData interest-gated LRU (#38)', () => {
  beforeEach(() => {
    useStore.setState({
      tickerData: {},
      priceDirections: {},
      symbolsList: [],
      positions: {},
      activeSymbol: 'BTCUSDT',
      tickData: {},
      tickMeta: null,
    });
  });

  it('evicts least-recently-updated extras beyond the cap but protects the interest set', () => {
    useStore.setState({ symbolsList: ['KEEPME'] });
    const update = {};
    for (let i = 0; i < 100; i++) {
      update[`X${i}`] = { price: i };
    }
    update.KEEPME = { price: 1 };

    useStore.getState().updateMarketData(update);

    const tickers = useStore.getState().tickerData;
    const keys = Object.keys(tickers);
    expect(keys.length).toBeLessThanOrEqual(96);
    // Watchlist-protected symbol survives even though it was written last-but-one.
    expect(tickers.KEEPME).toBeTruthy();
    // Most-recently-updated extras survive; the oldest were evicted.
    expect(tickers.X99).toBeTruthy();
    expect(tickers.X0).toBeUndefined();
    // priceDirections pruned in tandem.
    expect(useStore.getState().priceDirections.X0).toBeUndefined();
    expect(useStore.getState().priceDirections.X99).toBeTruthy();
  });

  it('keeps every symbol while under the cap', () => {
    const update = { AAA: { price: 1 }, BBB: { price: 2 } };
    useStore.getState().updateMarketData(update);
    const tickers = useStore.getState().tickerData;
    expect(tickers.AAA.price).toBe(1);
    expect(tickers.BBB.price).toBe(2);
  });
});

describe('setTickData caps (#38)', () => {
  beforeEach(() => {
    useStore.setState({ tickData: {}, tickMeta: null, activeSymbol: 'T0' });
  });

  it('caps per-symbol entries to the newest 500', () => {
    const data = {
      T0: Array.from({ length: 600 }, (_, j) => ({ p: j })),
    };
    useStore.getState().setTickData(data, null);
    const ticks = useStore.getState().tickData.T0;
    expect(ticks.length).toBe(500);
    expect(ticks[0].p).toBe(100);
    expect(ticks[499].p).toBe(599);
  });

  it('caps symbol count and protects the active symbol', () => {
    const data = {};
    for (let i = 0; i < 10; i++) {
      data[`T${i}`] = [{ p: i }];
    }
    useStore.getState().setTickData(data, null);
    const keys = Object.keys(useStore.getState().tickData);
    // Active symbol (T0) + the 8 most recent keys.
    expect(keys).toContain('T0');
    expect(keys).toContain('T9');
    expect(keys).not.toContain('T1');
    expect(keys.length).toBeLessThanOrEqual(9);
  });

  it('handles non-object payloads safely', () => {
    useStore.getState().setTickData(null, null);
    expect(useStore.getState().tickData).toEqual({});
  });
});

describe('setBotHistory cap (#40b)', () => {
  it('caps mirrored bot history at 200 entries', () => {
    const bots = Array.from({ length: 250 }, (_, i) => ({ id: `b${i}` }));
    useStore.getState().setBotHistory(bots);
    const hist = useStore.getState().botHistory;
    expect(hist).toHaveLength(200);
    expect(hist[0].id).toBe('b0');
    expect(hist[199].id).toBe('b199');
  });

  it('handles non-array payloads safely', () => {
    useStore.getState().setBotHistory(null);
    expect(useStore.getState().botHistory).toEqual([]);
  });
});

describe('setTradeHistory realized P&L', () => {
  it('aggregates Realized P&L from the full payload, not the table cap', () => {
    const trades = Array.from({ length: 6 }, (_, i) => ({
      id: `t${i}`,
      status: 'FILLED',
      realized_pnl: i === 5 ? -40 : 1,
      trade_value: 10,
      timestamp: `2026-08-16 10:0${i}:00`,
    }));
    useStore.getState().setTradeHistory(trades);
    const stats = useStore.getState().tradeStats;
    expect(stats.total_pnl).toBe(-35);
    expect(stats.total_exits).toBe(6);
    expect(useStore.getState().tradeHistory.length).toBe(6);
  });
});

describe('safeMode store sync (H1)', () => {
  beforeEach(() => {
    useStore.setState({
      safeMode: { active: true, reason: 'startup_recovery:unclean_shutdown' },
      systemStats: {
        clients: 1,
        runtime: { safe_mode: { active: true, reason: 'startup_recovery:unclean_shutdown' } },
      },
    });
  });

  it('setSafeMode keeps Admin runtime.safe_mode in sync', () => {
    useStore.getState().setSafeMode({ active: false });
    const s = useStore.getState();
    expect(s.safeMode.active).toBe(false);
    expect(s.systemStats.runtime.safe_mode.active).toBe(false);
  });
});

describe('updateOrderBooks', () => {
  it('retains L2 snapshots even when no Book/Depth consumer is mounted', async () => {
    const { resetOrderBookInterestForTests, isOrderBookRetentionEnabled } = await import(
      '../services/orderBookInterest'
    );
    resetOrderBookInterestForTests();
    expect(isOrderBookRetentionEnabled()).toBe(false);
    useStore.setState({ orderBooks: {}, activeSymbol: 'BTCUSDT' });
    useStore.getState().updateOrderBooks({
      BTCUSDT: { bids: [[100, 1]], asks: [[101, 1]] },
    });
    expect(useStore.getState().orderBooks.BTCUSDT.bids[0][0]).toBe(100);
  });
});
