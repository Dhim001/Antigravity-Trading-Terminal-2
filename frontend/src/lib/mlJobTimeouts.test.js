import { describe, expect, it } from 'vitest';
import {
  formatMlJobBudgetLabel,
  isTransientMlPollError,
  mlJobPollDeadlineMs,
  mlJobPollIntervalMs,
  mlJobTimeoutMs,
  MlJobPollBudgetError,
  ML_JOB_POLL_BUFFER_MS,
  ML_TRAIN_TIMEOUT_MS,
} from './mlJobTimeouts';

describe('mlJobTimeouts', () => {
  it('gives RL train the longest GPU budget', () => {
    expect(mlJobTimeoutMs('RL_PPO_AGENT', 'train')).toBe(ML_TRAIN_TIMEOUT_MS.RL_PPO_AGENT);
    expect(mlJobTimeoutMs('LSTM_DIRECTION', 'train')).toBe(ML_TRAIN_TIMEOUT_MS.deep);
    expect(mlJobTimeoutMs('ML_SIGNAL_BOOST', 'train')).toBe(ML_TRAIN_TIMEOUT_MS.default);
  });

  it('poll deadline exceeds train budget by buffer', () => {
    const train = mlJobTimeoutMs('LSTM_DIRECTION', 'train');
    expect(mlJobPollDeadlineMs('LSTM_DIRECTION', 'train')).toBe(train + ML_JOB_POLL_BUFFER_MS);
  });

  it('slows poll interval for long GPU jobs after warm-up', () => {
    expect(mlJobPollIntervalMs(0, 3_600_000)).toBe(2_500);
    expect(mlJobPollIntervalMs(90_000, 3_600_000)).toBe(4_000);
    expect(mlJobPollIntervalMs(180_000, 3_600_000)).toBe(5_000);
  });

  it('formats budgets for toasts', () => {
    expect(formatMlJobBudgetLabel(90 * 60_000)).toBe('90 min');
    expect(formatMlJobBudgetLabel(120 * 60_000)).toBe('2 h');
  });

  it('treats per-request job poll timeouts as transient', () => {
    expect(isTransientMlPollError(new Error(
      'Request timed out after 60000ms: /api/v1/ml/jobs/abc',
    ))).toBe(true);
    expect(isTransientMlPollError(new Error('HTTP 500'))).toBe(false);
    const budget = new MlJobPollBudgetError('budget', { jobId: 'x', budgetMs: 1 });
    expect(budget.code).toBe('ML_JOB_POLL_BUDGET');
  });
});
