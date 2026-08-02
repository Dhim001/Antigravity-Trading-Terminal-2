import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  __resetMlHyperparamSweepPollingForTests,
  isMlHyperparamSweepPolling,
  startMlHyperparamSweepPolling,
  stopMlHyperparamSweepPolling,
} from './mlHyperparamSweepPolling';
import {
  beginMlJob,
  finishMlJob,
  getMlTrainingSession,
} from './mlTrainingSession';

vi.mock('@/api/client', () => ({
  apiRequest: vi.fn(),
}));

vi.mock('sonner', () => ({
  toast: { message: vi.fn(), error: vi.fn(), success: vi.fn() },
}));

describe('mlHyperparamSweepPolling', () => {
  beforeEach(() => {
    __resetMlHyperparamSweepPollingForTests();
    const { jobToken } = beginMlJob({
      kind: 'hyperparam_sweep',
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      jobId: 'sweep-job-1',
      jobProgress: { active: true, kind: 'hyperparam_sweep', label: 'Auto-tune' },
    });
    finishMlJob(jobToken); // reset busy; start fresh below
    __resetMlHyperparamSweepPollingForTests();
  });

  afterEach(() => {
    __resetMlHyperparamSweepPollingForTests();
    vi.clearAllMocks();
  });

  it('tracks active job id while polling', async () => {
    const { apiRequest } = await import('@/api/client');
    apiRequest.mockResolvedValue({
      job: { status: 'running', progress: { pct: 20, phase: 'hyperparam_trial', trial: 2, max_trials: 12 } },
    });

    const { jobToken } = beginMlJob({
      kind: 'hyperparam_sweep',
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      jobId: 'sweep-job-1',
      jobProgress: { active: true, kind: 'hyperparam_sweep', label: 'Auto-tune', startedAt: Date.now() },
    });

    startMlHyperparamSweepPolling('sweep-job-1', { jobToken, timeBudgetSec: 120 });
    expect(isMlHyperparamSweepPolling('sweep-job-1')).toBe(true);

    await vi.waitFor(() => {
      expect(apiRequest).toHaveBeenCalled();
    });

    expect(getMlTrainingSession().serverProgress?.pct).toBe(20);
    expect(getMlTrainingSession().serverProgress?.trial).toBe(2);

    stopMlHyperparamSweepPolling();
    expect(isMlHyperparamSweepPolling()).toBe(false);
    finishMlJob(jobToken);
  });

  it('finalizes and clears tuning on done', async () => {
    const { apiRequest } = await import('@/api/client');
    apiRequest.mockResolvedValue({
      job: {
        status: 'done',
        result: {
          ok: true,
          best_score: 0.71,
          trials_completed: 8,
          max_trials: 12,
          best_hyperparams: { learning_rate: 0.001 },
        },
        progress: { pct: 100, phase: 'done' },
      },
    });

    const { jobToken } = beginMlJob({
      kind: 'hyperparam_sweep',
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      jobId: 'sweep-job-2',
      jobProgress: { active: true, kind: 'hyperparam_sweep', label: 'Auto-tune', startedAt: Date.now() },
    });

    startMlHyperparamSweepPolling('sweep-job-2', { jobToken, timeBudgetSec: 60 });

    await vi.waitFor(() => {
      expect(getMlTrainingSession().tuning).toBe(false);
    });

    expect(getMlTrainingSession().tuneResult?.best_score).toBe(0.71);
    expect(isMlHyperparamSweepPolling()).toBe(false);
  });
});
