import { describe, it, expect } from 'vitest';
import {
  collectClientMemoryStats,
  memoryPressureLevel,
  heapPressureLevel,
  bufferPressureNeedsTrim,
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

  it('flags buffer trim at LRU symbol cap without heap pressure', () => {
    resetCandleBufferStateForTests();
    for (let i = 0; i < 4; i += 1) {
      setCandleHistory(`SYM${i}`, [{ time: i, open: 1, high: 1, low: 1, close: 1, volume: 1 }]);
    }
    const stats = collectClientMemoryStats();
    expect(stats.symbols1m).toBe(4);
    expect(bufferPressureNeedsTrim(stats)).toBe(true);
    expect(heapPressureLevel(stats)).toBe('ok');
    expect(memoryPressureLevel(stats)).toBe('warn');
  });
});
