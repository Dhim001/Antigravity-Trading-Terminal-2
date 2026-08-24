import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/api/client', () => ({
  apiRequest: vi.fn(),
  isAbortError: (err) => err?.name === 'AbortError',
}));

vi.mock('@/lib/mlTrainingSession', () => ({
  getCachedModelStatus: vi.fn(() => null),
  setCachedModelStatus: vi.fn(),
}));

vi.mock('@/lib/mlJobTimeouts', () => ({
  ML_JOB_STATUS_POLL_TIMEOUT_MS: 5000,
  ML_JOB_SUBMIT_TIMEOUT_MS: 10000,
}));

import { apiRequest } from '@/api/client';
import { setCachedModelStatus } from '@/lib/mlTrainingSession';
import {
  fetchMlInventory,
  submitMlTrainJob,
  submitMlValidateJob,
  fetchMlRetrainQueue,
  fetchMlTrainRuns,
  pollMlJob,
  cancelMlJob,
  fetchLatestMlHyperparamSweep,
  submitMlBatchTrain,
  fetchMlBatch,
  cancelMlBatch,
  retryMlBatch,
  submitMlPipeline,
  fetchMlPipeline,
  fetchActiveMlPipeline,
  cancelMlPipeline,
  approveMlPipeline,
} from './mlLabApi';

describe('mlLabApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('fetchMlInventory returns empty when symbol missing', async () => {
    expect(await fetchMlInventory('', ['ML_SIGNAL_BOOST'], '1m')).toEqual([]);
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('fetchMlInventory maps model-status rows and caches them', async () => {
    apiRequest.mockResolvedValueOnce({
      trained: true,
      trained_at: '2026-01-01T00:00:00Z',
      metrics: { accuracy: 0.7 },
      timeframe: '1m',
    });

    const rows = await fetchMlInventory('ETHUSDT', ['ML_SIGNAL_BOOST'], '1m');
    expect(apiRequest).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/ml/model-status?symbol=ETHUSDT&strategy=ML_SIGNAL_BOOST&timeframe=1m'),
    );
    expect(setCachedModelStatus).toHaveBeenCalled();
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      strategy: 'ML_SIGNAL_BOOST',
      trained: true,
      timeframe: '1m',
    });
  });

  it('fetchMlInventory marks failed strategy with error', async () => {
    apiRequest.mockRejectedValueOnce(new Error('network'));
    const rows = await fetchMlInventory('BTCUSDT', ['LSTM_DIRECTION'], '5m');
    expect(rows[0]).toMatchObject({
      strategy: 'LSTM_DIRECTION',
      trained: false,
      error: 'network',
      timeframe: '5m',
    });
  });

  it('submitMlTrainJob posts to /api/v1/ml/train', async () => {
    apiRequest.mockResolvedValueOnce({ job_id: 'j1' });
    const body = { symbol: 'ETHUSDT', strategy: 'LSTM_DIRECTION' };
    const out = await submitMlTrainJob(body);
    expect(out).toEqual({ job_id: 'j1' });
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/train', {
      method: 'POST',
      body,
      timeoutMs: 10000,
    });
  });

  it('submitMlValidateJob posts to /api/v1/ml/validate', async () => {
    apiRequest.mockResolvedValueOnce({ ok: true });
    await submitMlValidateJob({ symbol: 'ETHUSDT' });
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/validate', expect.objectContaining({
      method: 'POST',
    }));
  });

  it('fetchMlRetrainQueue hits retrain-status', async () => {
    apiRequest.mockResolvedValueOnce({ pending: [] });
    await fetchMlRetrainQueue();
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/retrain-status');
  });

  it('pollMlJob and cancelMlJob use job id paths', async () => {
    apiRequest.mockResolvedValue({});
    await pollMlJob('abc');
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/jobs/abc', expect.any(Object));
    await cancelMlJob('abc');
    expect(apiRequest).toHaveBeenCalledWith(
      '/api/v1/ml/jobs/abc/cancel',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('fetchLatestMlHyperparamSweep returns null without symbol/strategy', async () => {
    expect(await fetchLatestMlHyperparamSweep('', 'RL_PPO_AGENT')).toBeNull();
    expect(await fetchLatestMlHyperparamSweep('AAPL', '')).toBeNull();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it('fetchLatestMlHyperparamSweep picks newest matching sweep then loads result', async () => {
    apiRequest
      .mockResolvedValueOnce({
        jobs: [
          { job_id: 'train-1', kind: 'train', symbol: 'AAPL', strategy: 'RL_PPO_AGENT' },
          {
            job_id: 'sweep-new',
            kind: 'hyperparam_sweep',
            symbol: 'aapl',
            strategy: 'rl_ppo_agent',
            status: 'done',
          },
          {
            job_id: 'sweep-old',
            kind: 'hyperparam_sweep',
            symbol: 'AAPL',
            strategy: 'RL_PPO_AGENT',
            status: 'done',
          },
        ],
      })
      .mockResolvedValueOnce({
        ok: true,
        job: {
          job_id: 'sweep-new',
          status: 'done',
          result: { ok: true, best_score: 0.76, best_hyperparams: { learning_rate: 0.0002 } },
        },
      });

    const job = await fetchLatestMlHyperparamSweep('AAPL', 'RL_PPO_AGENT');
    expect(apiRequest).toHaveBeenNthCalledWith(1, '/api/v1/ml/jobs?limit=50', expect.any(Object));
    expect(apiRequest).toHaveBeenNthCalledWith(
      2,
      '/api/v1/ml/hyperparam-sweep/sweep-new',
      expect.any(Object),
    );
    expect(job.result.best_score).toBe(0.76);
  });

  it('fetchLatestMlHyperparamSweep returns null when no matching sweep', async () => {
    apiRequest.mockResolvedValueOnce({
      jobs: [{ job_id: 'x', kind: 'train', symbol: 'AAPL', strategy: 'RL_PPO_AGENT' }],
    });
    expect(await fetchLatestMlHyperparamSweep('AAPL', 'RL_PPO_AGENT')).toBeNull();
    expect(apiRequest).toHaveBeenCalledTimes(1);
  });

  it('submitMlBatchTrain posts items to /api/v1/ml/batch-train', async () => {
    apiRequest.mockResolvedValueOnce({ ok: true, batch_id: 'b-1', status: 'queued' });
    const items = [
      { strategy: 'ML_SIGNAL_BOOST', config: { timeframe: '5m', training_window_months: 6 }, validate_after: true },
      { strategy: 'LSTM_DIRECTION', config: { timeframe: '5m', training_window_months: 6, epochs: 40 }, validate_after: true },
    ];
    const out = await submitMlBatchTrain({
      symbol: 'ETHUSDT',
      items,
      concurrency: 1,
      fail_fast: false,
      idempotency_key: 'idem-1',
    });
    expect(out).toMatchObject({ ok: true, batch_id: 'b-1' });
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/batch-train', {
      method: 'POST',
      body: {
        symbol: 'ETHUSDT',
        items,
        concurrency: 1,
        fail_fast: false,
        idempotency_key: 'idem-1',
      },
      timeoutMs: 10000,
    });
  });

  it('submitMlBatchTrain defaults concurrency/fail_fast and omits a missing idempotency key', async () => {
    apiRequest.mockResolvedValueOnce({ ok: true, batch_id: 'b-2', status: 'queued' });
    await submitMlBatchTrain({ symbol: 'ETHUSDT', items: [{ strategy: 'A' }] });
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/batch-train', {
      method: 'POST',
      body: { symbol: 'ETHUSDT', items: [{ strategy: 'A' }], concurrency: 1, fail_fast: false },
      timeoutMs: 10000,
    });
  });

  it('fetchMlBatch gets the batch status path', async () => {
    apiRequest.mockResolvedValueOnce({ ok: true, batch_id: 'b-1', status: 'running' });
    await fetchMlBatch('b-1');
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/batch-train/b-1', {
      timeoutMs: 5000,
    });
  });

  it('cancelMlBatch and retryMlBatch post to the batch action paths', async () => {
    apiRequest.mockResolvedValue({ ok: true });
    await cancelMlBatch('b-1');
    expect(apiRequest).toHaveBeenCalledWith(
      '/api/v1/ml/batch-train/b-1/cancel',
      expect.objectContaining({ method: 'POST' }),
    );
    await retryMlBatch('b-1');
    expect(apiRequest).toHaveBeenCalledWith(
      '/api/v1/ml/batch-train/b-1/retry',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('submitMlPipeline posts the research pipeline body', async () => {
    apiRequest.mockResolvedValueOnce({ ok: true, pipeline_id: 'p-1' });
    await submitMlPipeline({
      symbol: 'ETHUSDT',
      strategy: 'ML_SIGNAL_BOOST',
      profile: 'research',
      auto_deploy_mode: 'paper',
    });
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/pipeline', {
      method: 'POST',
      body: {
        symbol: 'ETHUSDT',
        strategy: 'ML_SIGNAL_BOOST',
        profile: 'research',
        auto_deploy_mode: 'paper',
      },
      timeoutMs: 10000,
    });
  });

  it('fetch/cancel/approve pipeline hit the durable routes', async () => {
    apiRequest.mockResolvedValue({ ok: true });
    await fetchMlPipeline('p-1');
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/pipeline/p-1', expect.any(Object));
    await fetchActiveMlPipeline('ETHUSDT');
    expect(apiRequest).toHaveBeenCalledWith(
      '/api/v1/ml/pipeline/active?symbol=ETHUSDT',
      expect.any(Object),
    );
    await cancelMlPipeline('p-1');
    expect(apiRequest).toHaveBeenCalledWith(
      '/api/v1/ml/pipeline/p-1/cancel',
      expect.objectContaining({ method: 'POST' }),
    );
    await approveMlPipeline('p-1');
    expect(apiRequest).toHaveBeenCalledWith(
      '/api/v1/ml/pipeline/p-1/approve',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('url-encodes batch ids', async () => {
    apiRequest.mockResolvedValueOnce({ ok: true });
    await fetchMlBatch('a/b c');
    expect(apiRequest).toHaveBeenCalledWith('/api/v1/ml/batch-train/a%2Fb%20c', expect.any(Object));
  });

  it('fetchMlTrainRuns builds the legacy strategy/timeframe query', async () => {
    apiRequest.mockResolvedValueOnce({ ok: true, runs: [{ id: 'r1' }] });
    const runs = await fetchMlTrainRuns('ETHUSDT', 'ML_SIGNAL_BOOST', '5m');
    expect(runs).toEqual([{ id: 'r1' }]);
    const url = apiRequest.mock.calls[0][0];
    expect(url).toContain('/api/v1/ml/runs?');
    expect(url).toContain('symbol=ETHUSDT');
    expect(url).toContain('strategy=ML_SIGNAL_BOOST');
    expect(url).toContain('timeframe=5m');
    expect(url).toContain('limit=15');
    expect(url).not.toContain('batch_id');
  });

  it('fetchMlTrainRuns adds batch_id and raises the cap when a batch filter is set', async () => {
    apiRequest.mockResolvedValueOnce({ ok: true, runs: [] });
    const runs = await fetchMlTrainRuns('ETHUSDT', null, null, { batchId: 'batch-9' });
    expect(runs).toEqual([]);
    const url = apiRequest.mock.calls[0][0];
    expect(url).toContain('batch_id=batch-9');
    expect(url).toContain('limit=100');
    expect(url).not.toContain('strategy=');
    expect(url).not.toContain('timeframe=');
  });

  it('fetchMlTrainRuns returns empty without a symbol', async () => {
    expect(await fetchMlTrainRuns('', 'A', '1m', { batchId: 'b' })).toEqual([]);
    expect(apiRequest).not.toHaveBeenCalled();
  });
});
