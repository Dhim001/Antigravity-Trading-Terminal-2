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
  pollMlJob,
  cancelMlJob,
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
});
