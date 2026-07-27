import { describe, expect, it } from 'vitest';
import {
  formatMlJobBudgetLabel,
  isTransientMlPollError,
  mlJobPollDeadlineMs,
  mlJobPollIntervalMs,
  mlJobTimeoutMs,
  mlJobWindowScale,
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

  it('scales budgets with training window months', () => {
    expect(mlJobWindowScale(3)).toBe(1);
    expect(mlJobWindowScale(12)).toBe(1.6);
    expect(mlJobWindowScale(36)).toBe(3);
    const base = mlJobTimeoutMs('ML_SIGNAL_BOOST', 'validate', { months: 3 });
    const long = mlJobTimeoutMs('ML_SIGNAL_BOOST', 'validate', { months: 36 });
    expect(long).toBeGreaterThan(base);
    expect(long).toBe(Math.round(base * 3));
  });

  it('poll deadline exceeds train budget by scaled buffer', () => {
    const train = mlJobTimeoutMs('LSTM_DIRECTION', 'train', { months: 12 });
    const deadline = mlJobPollDeadlineMs('LSTM_DIRECTION', 'train', { months: 12 });
    expect(deadline).toBeGreaterThan(train + ML_JOB_POLL_BUFFER_MS);
  });

  it('slows poll interval for long GPU jobs after warm-up', () => {
    expect(mlJobPollIntervalMs(0, 3_600_000)).toBe(2_500);
    expect(mlJobPollIntervalMs(90_000, 3_600_000)).toBe(4_000);
    expect(mlJobPollIntervalMs(180_000, 3_600_000)).toBe(5_000);
    expect(mlJobPollIntervalMs(200_000, 5_400_000)).toBe(8_000);
  });

  it('formats budgets for toasts', () => {
    expect(formatMlJobBudgetLabel(90 * 60_000)).toBe('90 min');
    expect(formatMlJobBudgetLabel(120 * 60_000)).toBe('2 h');
  });

  it('treats per-request job poll timeouts and server overload as transient', () => {
    expect(isTransientMlPollError(new Error(
      'Request timed out after 60000ms: /api/v1/ml/jobs/abc',
    ))).toBe(true);
    expect(isTransientMlPollError(new Error('HTTP 500'))).toBe(true);
    expect(isTransientMlPollError(new Error('HTTP 429'))).toBe(true);
    expect(isTransientMlPollError(new Error('HTTP 404'))).toBe(false);
    const budget = new MlJobPollBudgetError('budget', { jobId: 'x', budgetMs: 1 });
    expect(budget.code).toBe('ML_JOB_POLL_BUDGET');
  });
});
