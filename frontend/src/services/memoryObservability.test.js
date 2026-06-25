import { describe, it, expect } from 'vitest';
import {
  collectClientMemoryStats,
  memoryPressureLevel,
} from './memoryObservability';
import { resetCandleBufferStateForTests, setCandleHistory } from './candleBuffer';

describe('memoryObservability', () => {
  it('includes budget caps in client stats', () => {
    resetCandleBufferStateForTests();
    const stats = collectClientMemoryStats();
    expect(stats.budgets.maxSymbols).toBe(4);
    expect(stats.budgets.maxBars1m).toBe(3000);
    expect(stats.symbols1m).toBe(0);
  });

  it('flags warn when LRU symbol cap reached', () => {
    resetCandleBufferStateForTests();
    for (let i = 0; i < 4; i += 1) {
      setCandleHistory(`SYM${i}`, [{ time: i, open: 1, high: 1, low: 1, close: 1, volume: 1 }]);
    }
    const stats = collectClientMemoryStats();
    expect(stats.symbols1m).toBe(4);
    expect(memoryPressureLevel(stats)).toBe('warn');
  });
});
