import { describe, it, expect, beforeEach } from 'vitest';
import {
  bumpLiveRevision,
  getLiveRevision,
  bumpHistoryRevision,
  getHistoryRevision,
  clearRevisionsForKey,
  seedRevisions,
  resetCandleRevisionsForTests,
} from './candleRevisions';

describe('candleRevisions', () => {
  beforeEach(() => {
    resetCandleRevisionsForTests();
  });

  it('bumps live revision per key', () => {
    expect(getLiveRevision('BTCUSDT')).toBe(0);
    bumpLiveRevision('BTCUSDT');
    expect(getLiveRevision('BTCUSDT')).toBe(1);
    bumpLiveRevision('BTCUSDT');
    expect(getLiveRevision('BTCUSDT')).toBe(2);
  });

  it('bumps history revision independently', () => {
    bumpHistoryRevision('ETHUSDT|1m');
    expect(getHistoryRevision('ETHUSDT|1m')).toBe(1);
    expect(getLiveRevision('ETHUSDT|1m')).toBe(0);
  });

  it('clears revisions on buffer evict', () => {
    bumpLiveRevision('AAPL');
    bumpHistoryRevision('AAPL');
    clearRevisionsForKey('AAPL');
    expect(getLiveRevision('AAPL')).toBe(0);
    expect(getHistoryRevision('AAPL')).toBe(0);
  });

  it('seeds from snapshot objects', () => {
    seedRevisions({ BTCUSDT: 3 }, { BTCUSDT: 1 });
    expect(getLiveRevision('BTCUSDT')).toBe(3);
    expect(getHistoryRevision('BTCUSDT')).toBe(1);
  });
});
