import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  stopBacktestJobPolling,
  scheduleBacktestJobPoll,
  backtestJobProgressFingerprint,
  backtestJobProgressUpdatedAtMs,
  isBacktestJobProgressStalled,
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

  it('fingerprint advances when elapsed_sec changes at bar 0', () => {
    const a = backtestJobProgressFingerprint({
      status: 'running',
      progress: { pct: 10, bar: 0, phase: 'simulate', elapsed_sec: 14, message: 'Simulating: bar 0/56824…' },
    });
    const b = backtestJobProgressFingerprint({
      status: 'running',
      progress: { pct: 10, bar: 0, phase: 'simulate', elapsed_sec: 20, message: 'Simulating: bar 0/56824…' },
    });
    expect(a).not.toEqual(b);
  });

  it('fingerprint advances when updated_at heartbeat changes', () => {
    const a = backtestJobProgressFingerprint({
      status: 'running',
      progress: {
        pct: 10,
        bar: 0,
        phase: 'features',
        elapsed_sec: 42,
        message: 'Building ML features…',
        updated_at: '2026-08-01T17:00:00Z',
      },
    });
    const b = backtestJobProgressFingerprint({
      status: 'running',
      progress: {
        pct: 10,
        bar: 0,
        phase: 'features',
        elapsed_sec: 42,
        message: 'Building ML features…',
        updated_at: '2026-08-01T17:00:05Z',
      },
    });
    expect(a).not.toEqual(b);
  });

  it('fingerprint is stable when server progress is unchanged (dead worker)', () => {
    const snap = {
      status: 'running',
      progress: {
        pct: 10,
        bar: 0,
        phase: 'features',
        elapsed_sec: 42,
        message: 'Building ML features… 0/50000',
        updated_at: '2026-08-01T17:00:00Z',
      },
    };
    expect(backtestJobProgressFingerprint(snap)).toEqual(
      backtestJobProgressFingerprint({ ...snap, progress: { ...snap.progress } }),
    );
  });

  it('parses progress.updated_at ISO to epoch ms', () => {
    const ms = backtestJobProgressUpdatedAtMs({
      progress: { updated_at: '2026-08-01T17:00:00.000Z' },
    });
    expect(ms).toBe(Date.parse('2026-08-01T17:00:00.000Z'));
  });

  it('stalls when server updated_at is older than stallMs', () => {
    const nowMs = Date.parse('2026-08-01T17:20:00.000Z');
    const job = {
      status: 'running',
      progress: {
        pct: 12,
        bar: 494,
        phase: 'features',
        updated_at: '2026-08-01T17:00:00.000Z',
      },
    };
    expect(isBacktestJobProgressStalled(job, {
      nowMs,
      stallMs: 15 * 60 * 1000,
    })).toBe(true);
  });

  it('does not stall when server updated_at is fresh', () => {
    const nowMs = Date.parse('2026-08-01T17:00:10.000Z');
    const job = {
      status: 'running',
      progress: {
        pct: 12,
        bar: 494,
        phase: 'features',
        updated_at: '2026-08-01T17:00:05.000Z',
      },
    };
    expect(isBacktestJobProgressStalled(job, {
      nowMs,
      stallMs: 15 * 60 * 1000,
    })).toBe(false);
  });

  it('does not stall completed jobs even with old updated_at', () => {
    const nowMs = Date.parse('2026-08-01T18:00:00.000Z');
    const job = {
      status: 'completed',
      progress: { pct: 100, updated_at: '2026-08-01T17:00:00.000Z' },
    };
    expect(isBacktestJobProgressStalled(job, {
      nowMs,
      stallMs: 15 * 60 * 1000,
    })).toBe(false);
  });
});
