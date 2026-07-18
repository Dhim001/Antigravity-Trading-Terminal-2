import { describe, expect, it, vi, beforeEach } from 'vitest';
import {
  beginMlJob,
  finishMlJob,
  getCachedModelStatus,
  getMlTrainingSession,
  resolveModelStatusFetch,
  setCachedModelStatus,
  statusCacheKey,
} from './mlTrainingSession';

describe('mlTrainingSession', () => {
  beforeEach(() => {
    finishMlJob(getMlTrainingSession().jobToken, {});
  });

  it('caches model status by symbol|strategy', () => {
    setCachedModelStatus('BNBUSDT', 'TRANSFORMER_SIGNAL', {
      trained: true,
      trained_at: '2026-07-18T10:00:00Z',
    });
    expect(statusCacheKey('bnbusdt', 'transformer_signal')).toBe('BNBUSDT|TRANSFORMER_SIGNAL');
    expect(getCachedModelStatus('BNBUSDT', 'TRANSFORMER_SIGNAL')?.trained).toBe(true);
  });

  it('keeps last good status on transient fetch failure', () => {
    setCachedModelStatus('ETHUSDT', 'LSTM_DIRECTION', {
      trained: true,
      trained_at: '2026-07-18T09:00:00Z',
      metrics: { val_accuracy: 0.6 },
    });
    const next = resolveModelStatusFetch('ETHUSDT', 'LSTM_DIRECTION', {
      error: new Error('Status unavailable'),
      previous: null,
    });
    expect(next.trained).toBe(true);
    expect(next.stale).toBe(true);
    expect(next.fetch_error).toMatch(/unavailable/i);
  });

  it('tracks in-flight train job across finish', () => {
    const { jobToken } = beginMlJob({
      kind: 'train',
      strategy: 'LSTM_DIRECTION',
      symbol: 'BTCUSDT',
      jobProgress: { active: true, kind: 'train', label: 'Retraining' },
    });
    expect(getMlTrainingSession().training).toBe(true);
    expect(getMlTrainingSession().symbol).toBe('BTCUSDT');
    finishMlJob(jobToken, {});
    expect(getMlTrainingSession().training).toBe(false);
  });
});
