import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  stopBacktestJobPolling,
  scheduleBacktestJobPoll,
} from './backtestPolling';

describe('backtestPolling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    stopBacktestJobPolling();
  });

  afterEach(() => {
    stopBacktestJobPolling();
    vi.useRealTimers();
  });

  it('stopBacktestJobPolling clears scheduled poll', () => {
    const fn = vi.fn();
    scheduleBacktestJobPoll(fn, 1000);
    stopBacktestJobPolling();
    vi.advanceTimersByTime(2000);
    expect(fn).not.toHaveBeenCalled();
  });

  it('scheduleBacktestJobPoll replaces prior timer', () => {
    const first = vi.fn();
    const second = vi.fn();
    scheduleBacktestJobPoll(first, 1000);
    scheduleBacktestJobPoll(second, 1000);
    vi.advanceTimersByTime(1000);
    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledTimes(1);
  });
});
