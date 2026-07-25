import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import { lazyImport, prefetchLazyImport, prefetchDockPanels } from './lazyImport';

describe('lazyImport prefetch', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('registers and prefetches by label', async () => {
    const importFn = vi.fn(async () => ({ default: () => null }));
    lazyImport(importFn, 'test-prefetch-panel');
    const p = prefetchLazyImport('test-prefetch-panel');
    expect(p).toBeTruthy();
    await p;
    expect(importFn).toHaveBeenCalledTimes(1);
    prefetchLazyImport('test-prefetch-panel');
    expect(importFn).toHaveBeenCalledTimes(1);
  });

  it('prefetchDockPanels schedules warm-up via timeout fallback', async () => {
    vi.stubGlobal('requestIdleCallback', undefined);
    const importFn = vi.fn(async () => ({ default: () => null }));
    lazyImport(importFn, 'algo-idle-warm');
    prefetchDockPanels(['algo-idle-warm']);
    await vi.runAllTimersAsync();
    expect(importFn).toHaveBeenCalled();
  });
});
