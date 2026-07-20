/** @vitest-environment node */
import { describe, it, expect, afterEach, vi } from 'vitest';
import { cappedDevicePixelRatio, resetEchartsInstanceCountForTests } from './echartsInit';
import { resetMemoryPressureForTests, applyMemoryWarnLadder } from '../services/memoryPressureSignals';

describe('cappedDevicePixelRatio', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    resetMemoryPressureForTests();
    resetEchartsInstanceCountForTests();
  });

  it('caps single-pane DPR at 1.5', () => {
    vi.stubGlobal('window', { devicePixelRatio: 3 });
    expect(cappedDevicePixelRatio()).toBe(1.5);
  });

  it('caps multi-pane DPR at 1', () => {
    vi.stubGlobal('window', { devicePixelRatio: 2 });
    expect(cappedDevicePixelRatio({ multiPane: true })).toBe(1);
  });

  it('passes through low DPR', () => {
    vi.stubGlobal('window', { devicePixelRatio: 1 });
    expect(cappedDevicePixelRatio()).toBe(1);
  });

  it('forces DPR 1 under memory warn ladder', () => {
    vi.stubGlobal('window', {
      devicePixelRatio: 3,
      dispatchEvent: () => true,
    });
    applyMemoryWarnLadder();
    expect(cappedDevicePixelRatio()).toBe(1);
  });
});
