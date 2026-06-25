import { describe, it, expect, beforeEach } from 'vitest';
import {
  setCandleHistory,
  getCandles,
  hasCandleHistory,
  setPinnedCandleSymbol,
  initCandleBufferCache,
  resetCandleBufferStateForTests,
  getBufferedSymbolCountForTests,
  CANDLE_LRU_MAX_SYMBOLS,
  CANDLE_BUFFER_MAX_BARS,
  CANDLE_ARCHIVE_MAX_BARS,
} from './candleBuffer';
import { prependCandleHistory } from './candleBuffer';

function makeBars(symbol, count, start = 1_700_000_000) {
  return Array.from({ length: count }, (_, i) => ({
    time: start + i * 60,
    open: 100,
    high: 101,
    low: 99,
    close: 100 + i * 0.01,
    volume: 1,
  }));
}

describe('candleBuffer LRU', () => {
  beforeEach(() => {
    resetCandleBufferStateForTests();
    setPinnedCandleSymbol('BTCUSDT');
  });

  it('evicts oldest non-pinned symbol when LRU exceeds cap', () => {
    const symbols = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'AAPL', 'TSLA'];
    for (const sym of symbols) {
      setCandleHistory(sym, makeBars(sym, 10));
    }
    expect(getBufferedSymbolCountForTests()).toBeLessThanOrEqual(CANDLE_LRU_MAX_SYMBOLS);
    expect(hasCandleHistory('BTCUSDT')).toBe(true);
    expect(hasCandleHistory('TSLA')).toBe(true);
    expect(hasCandleHistory('ETHUSDT')).toBe(false);
  });

  it('never evicts pinned active symbol', () => {
    setPinnedCandleSymbol('ETHUSDT');
    for (const sym of ['ETHUSDT', 'BTCUSDT', 'SOLUSDT', 'AAPL', 'TSLA']) {
      setCandleHistory(sym, makeBars(sym, 5));
    }
    expect(hasCandleHistory('ETHUSDT')).toBe(true);
    expect(getBufferedSymbolCountForTests()).toBe(CANDLE_LRU_MAX_SYMBOLS);
  });

  it('trims 1m buffer to CANDLE_BUFFER_MAX_BARS', () => {
    setCandleHistory('BTCUSDT', makeBars('BTCUSDT', CANDLE_BUFFER_MAX_BARS + 500));
    expect(getCandles('BTCUSDT').length).toBe(CANDLE_BUFFER_MAX_BARS);
  });

  it('caps archive prepend at CANDLE_ARCHIVE_MAX_BARS', () => {
    setCandleHistory('BTCUSDT', makeBars('BTCUSDT', 1000, 1_700_100_000));
    const older = makeBars('BTCUSDT', 6000, 1_700_000_000);
    prependCandleHistory('BTCUSDT', older);
    expect(getCandles('BTCUSDT').length).toBeLessThanOrEqual(CANDLE_ARCHIVE_MAX_BARS);
  });

  it('initCandleBufferCache prunes after session hydrate', () => {
    for (const sym of ['BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'AAPL', 'TSLA', 'MSFT']) {
      setCandleHistory(sym, makeBars(sym, 5));
    }
    initCandleBufferCache('BTCUSDT');
    expect(getBufferedSymbolCountForTests()).toBeLessThanOrEqual(CANDLE_LRU_MAX_SYMBOLS);
    expect(hasCandleHistory('BTCUSDT')).toBe(true);
  });
});
