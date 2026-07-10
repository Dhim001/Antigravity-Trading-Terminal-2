import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  isSnapshotPaused,
  resetMemoryGuardForTests,
  runMemoryGuardTickForTests,
} from './memoryGuard';
import { resetCandleBufferStateForTests, setCandleHistory } from './candleBuffer';

describe('memoryGuard snapshot pause', () => {
  beforeEach(() => {
    resetMemoryGuardForTests();
    resetCandleBufferStateForTests();
  });

  afterEach(() => {
    resetMemoryGuardForTests();
    resetCandleBufferStateForTests();
  });

  it('starts with snapshots enabled', () => {
    expect(isSnapshotPaused()).toBe(false);
  });

  it('does not pause snapshots when only buffer cap is reached', () => {
    for (let i = 0; i < 4; i += 1) {
      setCandleHistory(`SYM${i}`, [{ time: i, open: 1, high: 1, low: 1, close: 1, volume: 1 }]);
    }
    const marketStore = { setState: vi.fn() };
    const researchStore = { setState: vi.fn(), getState: () => ({}) };
    runMemoryGuardTickForTests(() => marketStore, () => researchStore);
    expect(isSnapshotPaused()).toBe(false);
    expect(researchStore.setState).toHaveBeenCalled();
  });
});
