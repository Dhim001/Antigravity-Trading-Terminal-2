import { describe, expect, it, beforeEach } from 'vitest';
import {
  appendMlPollLog,
  beginMlJob,
  clearMlPollLog,
  finishMlJob,
  getCachedModelStatus,
  getMlTrainingSession,
  resolveModelStatusFetch,
  setCachedModelStatus,
  setMlServerProgress,
  statusCacheKey,
} from './mlTrainingSession';

describe('mlTrainingSession', () => {
  beforeEach(() => {
    finishMlJob(getMlTrainingSession().jobToken, {});
  });

  it('caches model status by symbol|strategy|timeframe', () => {
    setCachedModelStatus('BNBUSDT', 'TRANSFORMER_SIGNAL', {
      trained: true,
      trained_at: '2026-07-18T10:00:00Z',
      timeframe: '1m',
    }, '1m');
    expect(statusCacheKey('bnbusdt', 'transformer_signal', '1m')).toBe(
      'BNBUSDT|TRANSFORMER_SIGNAL|1m',
    );
    expect(getCachedModelStatus('BNBUSDT', 'TRANSFORMER_SIGNAL', '1m')?.trained).toBe(true);
  });

  it('keeps 1m and 15m model status separate', () => {
    setCachedModelStatus('ETHUSDT', 'LSTM_DIRECTION', {
      trained: true,
      trained_at: '2026-07-18T09:00:00Z',
      timeframe: '1m',
    }, '1m');
    setCachedModelStatus('ETHUSDT', 'LSTM_DIRECTION', {
      trained: false,
      timeframe: '15m',
    }, '15m');
    expect(getCachedModelStatus('ETHUSDT', 'LSTM_DIRECTION', '1m')?.trained).toBe(true);
    expect(getCachedModelStatus('ETHUSDT', 'LSTM_DIRECTION', '15m')?.trained).toBe(false);
    expect(statusCacheKey('ETHUSDT', 'LSTM_DIRECTION', '15m')).toBe(
      'ETHUSDT|LSTM_DIRECTION|15m',
    );
  });

  it('keeps last good status on transient fetch failure for that TF', () => {
    setCachedModelStatus('ETHUSDT', 'LSTM_DIRECTION', {
      trained: true,
      trained_at: '2026-07-18T09:00:00Z',
      metrics: { val_accuracy: 0.6 },
      timeframe: '5m',
    }, '5m');
    const next = resolveModelStatusFetch('ETHUSDT', 'LSTM_DIRECTION', {
      error: new Error('Status unavailable'),
      previous: null,
      timeframe: '5m',
    });
    expect(next.trained).toBe(true);
    expect(next.stale).toBe(true);
    expect(next.fetch_error).toMatch(/unavailable/i);
  });

  it('does not reuse 1m previous status when fetching 15m fails', () => {
    const next = resolveModelStatusFetch('ETHUSDT', 'LSTM_DIRECTION', {
      error: new Error('Status unavailable'),
      previous: { trained: true, timeframe: '1m', trained_at: '2026-07-01T00:00:00Z' },
      timeframe: '15m',
    });
    expect(next.trained).toBe(false);
    expect(next.timeframe).toBe('15m');
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
    expect(getMlTrainingSession().pollLog).toEqual([]);
    finishMlJob(jobToken, {});
    expect(getMlTrainingSession().training).toBe(false);
  });

  it('records poll log snapshots from server progress', () => {
    beginMlJob({
      kind: 'train',
      strategy: 'TRANSFORMER_SIGNAL',
      symbol: 'BTCUSDT',
      jobProgress: { active: true, kind: 'train', label: 'Retraining' },
    });
    setMlServerProgress({ pct: 10, phase: 'epoch', detail: '1/40', status: 'running' });
    setMlServerProgress({ pct: 10, phase: 'epoch', detail: '1/40', status: 'running' });
    setMlServerProgress({ pct: 25, phase: 'epoch', detail: '3/40', status: 'running' });
    appendMlPollLog({ note: 'poll_err', phase: 'waiting', status: 'running' });
    const log = getMlTrainingSession().pollLog;
    expect(log.length).toBe(3);
    expect(log[0].detail).toBe('1/40');
    expect(log[1].detail).toBe('3/40');
    expect(log[2].note).toBe('poll_err');
    clearMlPollLog();
    expect(getMlTrainingSession().pollLog).toEqual([]);
  });
});
