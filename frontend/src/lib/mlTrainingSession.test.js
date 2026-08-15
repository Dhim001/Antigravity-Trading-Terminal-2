import { describe, expect, it, beforeEach } from 'vitest';
import {
  appendMlPollLog,
  applyMlJobProgressMessage,
  beginMlJob,
  clearMlPollLog,
  finishMlJob,
  getCachedModelStatus,
  getMlTrainingSession,
  invalidateModelStatusCache,
  markModelFreshAfterTrain,
  normalizeStatusTimeframe,
  resolveModelStatusFetch,
  setCachedModelStatus,
  setMlJobId,
  setMlServerProgress,
  setMlTuneResult,
  hydrateMlTuneSession,
  STATUS_CACHE_MAX,
  statusCacheKey,
} from './mlTrainingSession';
import { useResearchStore } from '@/store/useResearchStore';

describe('mlTrainingSession', () => {
  beforeEach(() => {
    finishMlJob(getMlTrainingSession().jobToken, {});
    setMlTuneResult(null);
    clearMlPollLog();
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

  it('maps tick timeframe to 1m for cache keys (matches backend storage)', () => {
    expect(normalizeStatusTimeframe('tick')).toBe('1m');
    expect(statusCacheKey('ETHUSDT', 'TCN_MULTI_HORIZON', 'tick')).toBe(
      'ETHUSDT|TCN_MULTI_HORIZON|1m',
    );
    setCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', {
      trained: true,
      trained_at: '2026-08-01T09:33:33Z',
      timeframe: '1m',
    }, 'tick');
    expect(getCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', 'tick')?.trained).toBe(true);
    expect(getCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', '1m')?.trained).toBe(true);
  });

  it('keeps last good status when AbortError resolves via resolveModelStatusFetch', () => {
    setCachedModelStatus('AAPL', 'RL_PPO_AGENT', {
      trained: true,
      trained_at: '2026-08-01T12:00:00Z',
      timeframe: '1m',
    }, '1m');
    const abortErr = new Error('aborted');
    abortErr.name = 'AbortError';
    const next = resolveModelStatusFetch('AAPL', 'RL_PPO_AGENT', {
      error: abortErr,
      previous: null,
      timeframe: '1m',
    });
    expect(next?.trained).toBe(true);
  });

  it('keeps 1m and 15m model status separate', () => {    setCachedModelStatus('ETHUSDT', 'LSTM_DIRECTION', {
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

  it('hydrateMlTuneSession binds a completed sweep without bumping jobToken', () => {
    const before = getMlTrainingSession().jobToken;
    finishMlJob(before, { error: 'Hyperparam sweep timed out' });
    hydrateMlTuneSession({
      symbol: 'AAPL',
      strategy: 'RL_PPO_AGENT',
      result: { ok: true, best_score: 0.76, best_hyperparams: { learning_rate: 0.0002 } },
      lastError: null,
    });
    const sess = getMlTrainingSession();
    expect(sess.jobToken).toBe(before);
    expect(sess.symbol).toBe('AAPL');
    expect(sess.strategy).toBe('RL_PPO_AGENT');
    expect(sess.tuneResult.best_score).toBe(0.76);
    expect(sess.lastError).toBeNull();
    expect(sess.tuning).toBe(false);
    expect(sess.jobId).toBeNull();
  });

  it('hydrateMlTuneSession does not clobber an in-flight train job', () => {
    beginMlJob({
      kind: 'train',
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      jobId: 'train-1',
      jobProgress: { active: true, kind: 'train', label: 'Retraining' },
    });
    hydrateMlTuneSession({
      symbol: 'AAPL',
      strategy: 'RL_PPO_AGENT',
      result: { ok: true, best_score: 1 },
    });
    const sess = getMlTrainingSession();
    expect(sess.training).toBe(true);
    expect(sess.symbol).toBe('ETHUSDT');
    expect(sess.jobId).toBe('train-1');
    expect(sess.tuneResult.best_score).toBe(1);
  });

  it('tracks hyperparam sweep as tuning', () => {
    const { jobToken } = beginMlJob({
      kind: 'hyperparam_sweep',
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      jobId: 'job-tune-1',
      jobProgress: { active: true, kind: 'hyperparam_sweep', label: 'Auto-tune' },
    });
    expect(getMlTrainingSession().tuning).toBe(true);
    expect(getMlTrainingSession().training).toBe(false);
    expect(getMlTrainingSession().jobId).toBe('job-tune-1');
    finishMlJob(jobToken, {});
    expect(getMlTrainingSession().tuning).toBe(false);
  });

  it('does not treat interrupted resumable jobs as busy on bootstrap', async () => {
    const { resumeActiveMlJobs } = await import('./mlTrainingSession');
    finishMlJob(getMlTrainingSession().jobToken, {});
    resumeActiveMlJobs([
      {
        job_id: 'zombie-validate',
        kind: 'validate',
        strategy: 'ML_SIGNAL_BOOST',
        symbol: 'BTCUSDT',
        status: 'error',
        resumable: true,
        checkpoint: { version: 1, resume_ok: true, kind: 'walk_forward' },
        progress: { pct: 5, phase: 'fold 1/4' },
      },
    ]);
    const sess = getMlTrainingSession();
    expect(sess.validating).toBe(false);
    expect(sess.training).toBe(false);
    expect(sess.jobId).toBeNull();
  });

  it('reattaches only queued/running jobs from session bootstrap', async () => {
    const { resumeActiveMlJobs } = await import('./mlTrainingSession');
    finishMlJob(getMlTrainingSession().jobToken, {});
    resumeActiveMlJobs([
      {
        job_id: 'live-validate',
        kind: 'validate',
        strategy: 'ML_SIGNAL_BOOST',
        symbol: 'BTCUSDT',
        status: 'running',
        progress: { pct: 20, phase: 'fold 1/4' },
      },
    ]);
    const sess = getMlTrainingSession();
    expect(sess.validating).toBe(true);
    expect(sess.symbol).toBe('BTCUSDT');
    expect(sess.jobId).toBe('live-validate');
    finishMlJob(sess.jobToken, {});
  });

  it('keeps rich sweep fields on serverProgress for remount', () => {
    beginMlJob({
      kind: 'hyperparam_sweep',
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      jobId: 'job-tune-rich',
      jobProgress: { active: true, kind: 'hyperparam_sweep', label: 'Auto-tune' },
    });
    setMlServerProgress({
      pct: 40,
      phase: 'hyperparam_trial',
      detail: 'trial 4/12',
      status: 'running',
      trial: 4,
      max_trials: 12,
      best_score: 0.55,
      last_score: 0.51,
    });
    const prog = getMlTrainingSession().serverProgress;
    expect(prog.trial).toBe(4);
    expect(prog.max_trials).toBe(12);
    expect(prog.best_score).toBe(0.55);
    expect(getMlTrainingSession().pollLog.at(-1)?.trial).toBe(4);
  });

  it('keeps tuning=true on WS done for hyperparam so poller can finish', () => {
    beginMlJob({
      kind: 'hyperparam_sweep',
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      jobId: 'job-tune-ws',
      jobProgress: { active: true, kind: 'hyperparam_sweep', label: 'Auto-tune' },
    });
    applyMlJobProgressMessage({
      job_id: 'job-tune-ws',
      status: 'done',
      kind: 'hyperparam_sweep',
      pct: 100,
      phase: 'done',
      trial: 10,
      best_score: 0.8,
    });
    expect(getMlTrainingSession().tuning).toBe(true);
    expect(getMlTrainingSession().jobId).toBe('job-tune-ws');
    expect(getMlTrainingSession().serverProgress?.best_score).toBe(0.8);
  });

  it('clears tuning on WS cancel for hyperparam', () => {
    beginMlJob({
      kind: 'hyperparam_sweep',
      strategy: 'LSTM_DIRECTION',
      symbol: 'ETHUSDT',
      jobId: 'job-tune-cancel',
      jobProgress: { active: true, kind: 'hyperparam_sweep', label: 'Auto-tune' },
    });
    applyMlJobProgressMessage({
      job_id: 'job-tune-cancel',
      status: 'cancelled',
      kind: 'hyperparam_sweep',
      pct: 30,
      phase: 'cancelled',
    });
    expect(getMlTrainingSession().tuning).toBe(false);
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

  it('invalidates matching backtest results when train job completes via WS', () => {
    useResearchStore.setState({
      backtestResults: {
        run_id: 'r1',
        meta: { symbol: 'BTCUSDT', strategy: 'LSTM_DIRECTION' },
      },
      backtestSnapshot: '{}',
      backtestOverlay: { trades: [] },
    });
    beginMlJob({
      kind: 'train',
      strategy: 'LSTM_DIRECTION',
      symbol: 'BTCUSDT',
      jobId: 'job-1',
      jobProgress: { active: true, kind: 'train', label: 'Retraining' },
    });
    setMlJobId('job-1');
    applyMlJobProgressMessage({
      job_id: 'job-1',
      status: 'done',
      kind: 'train',
      pct: 100,
      phase: 'done',
    });
    expect(useResearchStore.getState().backtestResults).toBeNull();
  });

  it('does not let a late untrained fetch clobber a trained cache entry', () => {
    setCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', {
      trained: true,
      trained_at: '2026-08-01T16:00:00Z',
      timeframe: '1m',
    }, '1m');
    setCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', {
      trained: false,
      timeframe: '1m',
    }, '1m');
    expect(getCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', '1m')?.trained).toBe(true);
  });

  it('invalidateModelStatusCache clears entry so untrained can be written again', () => {
    setCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', {
      trained: true,
      trained_at: '2026-08-01T16:00:00Z',
      timeframe: '1m',
    }, '1m');
    invalidateModelStatusCache('ETHUSDT', 'TCN_MULTI_HORIZON', '1m');
    expect(getCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', '1m')).toBeNull();
    setCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', {
      trained: false,
      timeframe: '1m',
    }, '1m');
    expect(getCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', '1m')?.trained).toBe(false);
  });

  it('clears model status cache when train job completes via WS but keeps race guard', () => {
    setCachedModelStatus('BTCUSDT', 'LSTM_DIRECTION', {
      trained: false,
      timeframe: '1m',
    }, '1m');
    beginMlJob({
      kind: 'train',
      strategy: 'LSTM_DIRECTION',
      symbol: 'BTCUSDT',
      jobId: 'job-status-1',
      jobProgress: { active: true, kind: 'train', label: 'Retraining' },
    });
    setMlJobId('job-status-1');
    // Post-train refresh wrote trained=true…
    setCachedModelStatus('BTCUSDT', 'LSTM_DIRECTION', {
      trained: true,
      trained_at: '2026-08-01T17:00:00Z',
      timeframe: '1m',
    }, '1m');
    applyMlJobProgressMessage({
      job_id: 'job-status-1',
      status: 'done',
      kind: 'train',
      pct: 100,
      phase: 'done',
      timeframe: '1m',
    });
    // Body cleared so badges refetch…
    expect(getCachedModelStatus('BTCUSDT', 'LSTM_DIRECTION', '1m')).toBeNull();
    // …but late untrained must not undo Fresh during the guard window.
    setCachedModelStatus('BTCUSDT', 'LSTM_DIRECTION', {
      trained: false,
      timeframe: '1m',
    }, '1m');
    expect(getCachedModelStatus('BTCUSDT', 'LSTM_DIRECTION', '1m')).toBeNull();
  });

  it('markModelFreshAfterTrain blocks untrained until invalidate clears the guard', () => {
    markModelFreshAfterTrain('ETHUSDT', 'TCN_MULTI_HORIZON', '1m', {
      trained: true,
      trained_at: '2026-08-01T18:00:00Z',
      timeframe: '1m',
    });
    expect(getCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', '1m')?.trained).toBe(true);
    markModelFreshAfterTrain('ETHUSDT', 'TCN_MULTI_HORIZON', '1m');
    expect(getCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', '1m')).toBeNull();
    setCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', {
      trained: false,
      timeframe: '1m',
    }, '1m');
    expect(getCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', '1m')).toBeNull();
    invalidateModelStatusCache('ETHUSDT', 'TCN_MULTI_HORIZON', '1m');
    setCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', {
      trained: false,
      timeframe: '1m',
    }, '1m');
    expect(getCachedModelStatus('ETHUSDT', 'TCN_MULTI_HORIZON', '1m')?.trained).toBe(false);
  });

  it('resolveModelStatusFetch does not surface untrained during post-train guard', () => {
    markModelFreshAfterTrain('BNBUSDT', 'TRANSFORMER_SIGNAL', '1m');
    const next = resolveModelStatusFetch('BNBUSDT', 'TRANSFORMER_SIGNAL', {
      body: { trained: false, timeframe: '1m' },
      previous: { trained: true, trained_at: '2026-08-01T18:30:00Z', timeframe: '1m' },
      timeframe: '1m',
    });
    expect(next?.trained).toBe(true);
    expect(getCachedModelStatus('BNBUSDT', 'TRANSFORMER_SIGNAL', '1m')).toBeNull();
  });

  it(`statusCache evicts least-recently-used keys beyond ${STATUS_CACHE_MAX} (#40)`, () => {
    const n = STATUS_CACHE_MAX + 2;
    for (let i = 0; i < n; i++) {
      setCachedModelStatus(`SYM${i}`, 'LSTM_DIRECTION', { trained: true, timeframe: '1m' }, '1m');
    }
    expect(getCachedModelStatus('SYM0', 'LSTM_DIRECTION', '1m')).toBeNull();
    expect(getCachedModelStatus('SYM1', 'LSTM_DIRECTION', '1m')).toBeNull();
    expect(getCachedModelStatus(`SYM${n - 1}`, 'LSTM_DIRECTION', '1m')?.trained).toBe(true);
  });

  it('statusCache read refreshes LRU position (#40)', () => {
    for (let i = 0; i < STATUS_CACHE_MAX; i++) {
      setCachedModelStatus(`R${i}`, 'S', { trained: true, timeframe: '1m' }, '1m');
    }
    // Read R0 → becomes most-recently-used, so it survives the next insert.
    expect(getCachedModelStatus('R0', 'S', '1m')?.trained).toBe(true);
    setCachedModelStatus(`R${STATUS_CACHE_MAX}`, 'S', { trained: true, timeframe: '1m' }, '1m');
    expect(getCachedModelStatus('R0', 'S', '1m')?.trained).toBe(true);
    expect(getCachedModelStatus('R1', 'S', '1m')).toBeNull();
  });
});
