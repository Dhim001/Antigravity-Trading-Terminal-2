import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cancelWatchedBacktestJob, formatBacktestProgressLabel } from './BacktestProgressBar';
import { useResearchStore } from '../store/useResearchStore';

vi.mock('../api/transport', () => ({
  sendAction: vi.fn(async () => ({ ok: true })),
}));

vi.mock('sonner', () => ({
  toast: { message: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

describe('formatBacktestProgressLabel', () => {
  it('clarifies Starting… when a background job id exists', () => {
    const label = formatBacktestProgressLabel(
      { pct: 0, phase: 'resolve', message: 'Starting backtest…' },
      { jobId: 'abcdef12-9999' },
    );
    expect(label).toMatch(/background job|waiting for worker/i);
  });

  it('clarifies bar-0 simulate as ML feature build', () => {
    const label = formatBacktestProgressLabel({
      pct: 3,
      phase: 'simulate',
      message: 'Simulating: bar 0/50000…',
    });
    expect(label).toMatch(/Building ML features/i);
  });

  it('keeps mid-run simulate messages and appends ETA', () => {
    const label = formatBacktestProgressLabel({
      pct: 40,
      phase: 'simulate',
      message: 'Running simulation… bar 20000/50000',
      eta_sec: 90,
    });
    expect(label).toMatch(/Running simulation/i);
    expect(label).toMatch(/left/i);
  });

  it('uses phase label when message missing', () => {
    const label = formatBacktestProgressLabel({ pct: 10, phase: 'features' });
    expect(label).toMatch(/Building ML features/i);
  });

  it('clarifies Starting sweep… before first server tick', () => {
    const label = formatBacktestProgressLabel(
      { pct: 0, phase: 'sweep', message: 'Starting sweep…' },
      { jobId: 'sweep-abc' },
    );
    expect(label).toMatch(/background job|waiting for worker/i);
  });
});

describe('cancelWatchedBacktestJob', () => {
  beforeEach(() => {
    useResearchStore.setState({
      backtestRunning: true,
      backtestProgress: { pct: 40, phase: 'sweep', message: 'Sweep 2/10' },
      backtestJobId: 'job-sweep-1',
      backtestJobsById: {
        'job-sweep-1': { jobId: 'job-sweep-1', status: 'running', running: true },
      },
    });
  });

  it('clears running UI and cancels by job_id', async () => {
    const { sendAction } = await import('../api/transport');
    const { Action } = await import('../api/protocol');
    const out = await cancelWatchedBacktestJob();
    expect(out.ok).toBe(true);
    expect(useResearchStore.getState().backtestRunning).toBe(false);
    expect(useResearchStore.getState().backtestProgress).toBeNull();
    expect(sendAction).toHaveBeenCalledWith(
      Action.CANCEL_BACKTEST,
      { job_id: 'job-sweep-1' },
    );
  });
});
