/** Scope selection + sequential batch runner (pure helpers; no DOM / RTL). */
import { describe, it, expect, vi } from 'vitest';
import { selectStrategiesForScope } from './batchTrainScope';
import {
  BATCH_QUEUE_MAX_AGE_MS,
  BATCH_QUEUE_STORAGE_KEY,
  MlJobCancelledError,
  clearSavedBatchQueue,
  formatBatchTrainSummary,
  isMlJobCancelledError,
  readSavedBatchQueue,
  remainingAfterSummary,
  requestBatchCancel,
  runBatchTrainQueue,
  writeBatchQueueState,
} from './batchTrainRunner';
import { isModelStale, modelAgeHours } from '@/lib/modelHealth';

const inventory = [
  { strategy: 'ML_SIGNAL_BOOST', trained: false },
  { strategy: 'LSTM_DIRECTION', trained: false },
  { strategy: 'TCN_MULTI_HORIZON', trained: true, trained_at: new Date(Date.now() - 2 * 3600_000).toISOString() },
  { strategy: 'RL_PPO_AGENT', trained: true, trained_at: new Date(Date.now() - 72 * 3600_000).toISOString() },
  { strategy: 'VAE_REGIME_DETECTOR', trained: true, trained_at: new Date(Date.now() - 100 * 3600_000).toISOString() },
];

const IDS = [
  'ML_SIGNAL_BOOST',
  'LSTM_DIRECTION',
  'TCN_MULTI_HORIZON',
  'RL_PPO_AGENT',
  'VAE_REGIME_DETECTOR',
];

describe('selectStrategiesForScope', () => {
  it('selects untrained only', () => {
    expect(selectStrategiesForScope(inventory, 'untrained', [], IDS)).toEqual([
      'ML_SIGNAL_BOOST',
      'LSTM_DIRECTION',
    ]);
  });

  it('selects stale (>48h) trained models', () => {
    expect(selectStrategiesForScope(inventory, 'stale', [], IDS)).toEqual([
      'RL_PPO_AGENT',
      'VAE_REGIME_DETECTOR',
    ]);
  });

  it('selects all strategy ids', () => {
    expect(selectStrategiesForScope(inventory, 'all', [], IDS)).toEqual(IDS);
  });

  it('uses customSelected for custom scope', () => {
    expect(selectStrategiesForScope(
      inventory,
      'custom',
      ['LSTM_DIRECTION', 'RL_PPO_AGENT'],
      IDS,
    )).toEqual(['LSTM_DIRECTION', 'RL_PPO_AGENT']);
  });

  it('treats missing inventory rows as untrained', () => {
    expect(selectStrategiesForScope([], 'untrained', [], ['A', 'B'])).toEqual(['A', 'B']);
  });
});

describe('stale age for batch inventory rows', () => {
  it('flags 15d-old GBDT / ML_SIGNAL_BOOST as stale (>48h)', () => {
    const row = {
      strategy: 'ML_SIGNAL_BOOST',
      trained: true,
      trained_at: new Date(Date.now() - 15 * 24 * 3600_000).toISOString(),
    };
    expect(modelAgeHours(row)).toBeGreaterThan(48);
    expect(isModelStale(row, 48)).toBe(true);
  });

  it('does not flag models under 48h as stale', () => {
    const fresh = {
      strategy: 'TCN_MULTI_HORIZON',
      trained: true,
      trained_at: new Date(Date.now() - 12 * 3600_000).toISOString(),
    };
    expect(isModelStale(fresh, 48)).toBe(false);
    const fourDays = {
      ...fresh,
      trained_at: new Date(Date.now() - 4 * 24 * 3600_000).toISOString(),
    };
    expect(isModelStale(fourDays, 48)).toBe(true);
  });
});

describe('runBatchTrainQueue', () => {
  it('trains sequentially and counts successes', async () => {
    const order = [];
    const onTrainStrategy = vi.fn(async (id) => { order.push(id); });
    const summary = await runBatchTrainQueue({
      queue: ['A', 'B', 'C'],
      onTrainStrategy,
    });
    expect(order).toEqual(['A', 'B', 'C']);
    expect(summary).toMatchObject({ ok: 3, failed: 0, cancelled: 0, stoppedEarly: false, total: 3 });
    expect(summary.completed).toEqual(['A', 'B', 'C']);
  });

  it('isolates failures and continues the queue', async () => {
    const onTrainStrategy = vi.fn(async (id) => {
      if (id === 'B') throw new Error('boom');
    });
    const errors = [];
    const summary = await runBatchTrainQueue({
      queue: ['A', 'B', 'C'],
      onTrainStrategy,
      onStrategyError: (id, err) => errors.push([id, err.message]),
    });
    expect(summary.ok).toBe(2);
    expect(summary.failed).toBe(1);
    expect(summary.completed).toEqual(['A', 'C']);
    expect(errors).toEqual([['B', 'boom']]);
    expect(onTrainStrategy).toHaveBeenCalledTimes(3);
  });

  it('cancels mid-queue without starting remaining strategies', async () => {
    let cancel = false;
    const trained = [];
    const onTrainStrategy = vi.fn(async (id) => {
      trained.push(id);
      if (id === 'A') cancel = true;
    });
    const summary = await runBatchTrainQueue({
      queue: ['A', 'B', 'C'],
      onTrainStrategy,
      shouldCancel: () => cancel,
    });
    expect(trained).toEqual(['A']);
    expect(summary.ok).toBe(1);
    expect(summary.stoppedEarly).toBe(true);
    expect(summary.cancelled).toBe(0);
    expect(summary.total).toBe(3);
    expect(onTrainStrategy).toHaveBeenCalledTimes(1);
  });

  it('auto-validates after each successful train when enabled', async () => {
    const train = vi.fn(async () => {});
    const validate = vi.fn(async () => {});
    const summary = await runBatchTrainQueue({
      queue: ['A', 'B'],
      onTrainStrategy: train,
      onValidateStrategy: validate,
      autoValidate: true,
    });
    expect(train).toHaveBeenCalledTimes(2);
    expect(validate).toHaveBeenCalledTimes(2);
    expect(validate.mock.calls.map((c) => c[0])).toEqual(['A', 'B']);
    expect(summary.ok).toBe(2);
  });

  it('does not validate when autoValidate is false', async () => {
    const validate = vi.fn(async () => {});
    await runBatchTrainQueue({
      queue: ['A'],
      onTrainStrategy: async () => {},
      onValidateStrategy: validate,
      autoValidate: false,
    });
    expect(validate).not.toHaveBeenCalled();
  });

  it('counts validate failure as strategy failure', async () => {
    const summary = await runBatchTrainQueue({
      queue: ['A', 'B'],
      onTrainStrategy: async () => {},
      onValidateStrategy: async (id) => {
        if (id === 'A') throw new Error('val fail');
      },
      autoValidate: true,
    });
    expect(summary.ok).toBe(1);
    expect(summary.failed).toBe(1);
    expect(summary.completed).toEqual(['B']);
  });

  it('skips validate when cancel flips after train', async () => {
    let cancel = false;
    const validate = vi.fn(async () => {});
    await runBatchTrainQueue({
      queue: ['A'],
      onTrainStrategy: async () => { cancel = true; },
      onValidateStrategy: validate,
      autoValidate: true,
      shouldCancel: () => cancel,
    });
    expect(validate).not.toHaveBeenCalled();
  });

  it('reports progress for each strategy', async () => {
    const progress = [];
    await runBatchTrainQueue({
      queue: ['X', 'Y'],
      onTrainStrategy: async () => {},
      onProgress: (p) => progress.push(p),
    });
    expect(progress).toEqual([
      { index: 1, total: 2, strategy: 'X' },
      { index: 2, total: 2, strategy: 'Y' },
    ]);
  });
});

describe('formatBatchTrainSummary', () => {
  it('formats success, failure, and cancel messages with ok/failed/cancelled counts', () => {
    expect(formatBatchTrainSummary({ ok: 3, failed: 0, cancelled: 0, total: 3 }))
      .toBe('Trained 3/3 strategies.');
    expect(formatBatchTrainSummary({ ok: 2, failed: 1, cancelled: 0, total: 3 }))
      .toBe('Trained 2/3 strategies. 1 failed.');
    expect(formatBatchTrainSummary({ ok: 1, failed: 0, cancelled: 1, stoppedEarly: true, total: 3 }))
      .toBe('Stopped early. Trained 1/3. 0 failed. 1 cancelled.');
    // Legacy shape: cancelled as a boolean still reads as stopped-early.
    expect(formatBatchTrainSummary({ ok: 1, failed: 0, cancelled: true, total: 3 }))
      .toBe('Stopped early. Trained 1/3. 0 failed. 1 cancelled.');
  });
});

describe('requestBatchCancel', () => {
  it('calls cancelMlJob for the active job id', async () => {
    const cancelJob = vi.fn(async () => ({ ok: true }));
    const res = await requestBatchCancel({ jobId: 'job-123', cancelJob });
    expect(cancelJob).toHaveBeenCalledTimes(1);
    expect(cancelJob).toHaveBeenCalledWith('job-123');
    expect(res).toEqual({ requested: true, jobId: 'job-123' });
  });

  it('falls back to soft-stop when no job id is available', async () => {
    const cancelJob = vi.fn(async () => ({ ok: true }));
    const res = await requestBatchCancel({ jobId: null, cancelJob });
    expect(cancelJob).not.toHaveBeenCalled();
    expect(res.requested).toBe(false);
    expect(res.jobId).toBeNull();
  });

  it('reports failure (soft-stop fallback) when the cancel request rejects', async () => {
    const cancelJob = vi.fn(async () => { throw new Error('network down'); });
    const res = await requestBatchCancel({ jobId: 'job-9', cancelJob });
    expect(cancelJob).toHaveBeenCalledWith('job-9');
    expect(res).toEqual({ requested: false, jobId: 'job-9' });
  });
});

describe('isMlJobCancelledError', () => {
  it('detects MlJobCancelledError and generic cancelled markers', () => {
    expect(isMlJobCancelledError(new MlJobCancelledError('A'))).toBe(true);
    expect(isMlJobCancelledError(Object.assign(new Error('x'), { cancelled: true }))).toBe(true);
    expect(isMlJobCancelledError(Object.assign(new Error('x'), { code: 'cancelled' }))).toBe(true);
    expect(isMlJobCancelledError(new Error('boom'))).toBe(false);
    expect(isMlJobCancelledError(null)).toBe(false);
  });
});

describe('runBatchTrainQueue — cancelled jobs', () => {
  it('counts a cancelled job separately from failures and stops the queue', async () => {
    const onTrainStrategy = vi.fn(async (id) => {
      if (id === 'B') throw new MlJobCancelledError('B');
    });
    const errors = [];
    const cancels = [];
    const summary = await runBatchTrainQueue({
      queue: ['A', 'B', 'C'],
      onTrainStrategy,
      onStrategyError: (id) => errors.push(id),
      onStrategyCancelled: (id) => cancels.push(id),
    });
    expect(summary.ok).toBe(1);
    expect(summary.failed).toBe(0);
    expect(summary.cancelled).toBe(1);
    expect(summary.cancelledIds).toEqual(['B']);
    expect(summary.failedIds).toEqual([]);
    expect(summary.stoppedEarly).toBe(true);
    // 'C' must never start after the cancel.
    expect(onTrainStrategy).toHaveBeenCalledTimes(2);
    expect(errors).toEqual([]);
    expect(cancels).toEqual(['B']);
  });

  it('treats a generic cancelled-marked error as cancelled, not failed', async () => {
    const summary = await runBatchTrainQueue({
      queue: ['A'],
      onTrainStrategy: async () => {
        throw Object.assign(new Error('user abort'), { code: 'cancelled' });
      },
    });
    expect(summary).toMatchObject({ ok: 0, failed: 0, cancelled: 1, stoppedEarly: true });
  });
});

describe('runBatchTrainQueue — configOverrides', () => {
  it('passes the per-strategy config snapshot to train and validate', async () => {
    const trained = [];
    const validated = [];
    const overrides = { A: { epochs: 5 }, B: { epochs: 9 } };
    await runBatchTrainQueue({
      queue: ['A', 'B'],
      configOverrides: overrides,
      onTrainStrategy: async (id, cfg) => { trained.push([id, cfg]); },
      onValidateStrategy: async (id, cfg) => { validated.push([id, cfg]); },
      autoValidate: true,
    });
    expect(trained).toEqual([['A', { epochs: 5 }], ['B', { epochs: 9 }]]);
    expect(validated).toEqual([['A', { epochs: 5 }], ['B', { epochs: 9 }]]);
  });

  it('passes undefined config when no overrides map is provided', async () => {
    const trained = [];
    await runBatchTrainQueue({
      queue: ['A'],
      onTrainStrategy: async (id, cfg) => { trained.push(cfg); },
    });
    expect(trained).toEqual([undefined]);
  });
});

describe('runBatchTrainQueue — retry failed', () => {
  it('exposes failedIds so a retry re-queues only the failures', async () => {
    const trainedFirst = [];
    const first = await runBatchTrainQueue({
      queue: ['A', 'B', 'C'],
      onTrainStrategy: async (id) => {
        trainedFirst.push(id);
        if (id === 'B') throw new Error('boom');
      },
    });
    expect(trainedFirst).toEqual(['A', 'B', 'C']);
    expect(first.failedIds).toEqual(['B']);
    expect(first.failed).toBe(1);

    const trainedRetry = [];
    const retry = await runBatchTrainQueue({
      queue: first.failedIds,
      onTrainStrategy: async (id) => { trainedRetry.push(id); },
    });
    expect(trainedRetry).toEqual(['B']);
    expect(retry).toMatchObject({ ok: 1, failed: 0, cancelled: 0, stoppedEarly: false });
    expect(retry.completed).toEqual(['B']);
  });
});

describe('batch queue persistence', () => {
  const fakeStorage = () => {
    const map = new Map();
    return {
      getItem: (k) => (map.has(k) ? map.get(k) : null),
      setItem: (k, v) => { map.set(k, String(v)); },
      removeItem: (k) => { map.delete(k); },
    };
  };

  it('round-trips the in-progress queue for resume', () => {
    const storage = fakeStorage();
    const startedAt = 1_000_000;
    const stored = writeBatchQueueState(storage, {
      symbol: 'BTCUSDT',
      timeframe: '5m',
      trainingWindow: '6',
      queue: ['A', 'B', 'C'],
      remaining: ['B', 'C'],
      completed: ['A'],
      failedIds: [],
      autoValidate: true,
      configOverrides: { B: { epochs: 7 } },
      startedAt,
    });
    expect(stored).toBe(true);
    const saved = readSavedBatchQueue(storage, startedAt + 60_000);
    expect(saved).toMatchObject({
      symbol: 'BTCUSDT',
      timeframe: '5m',
      queue: ['A', 'B', 'C'],
      remaining: ['B', 'C'],
      completed: ['A'],
      autoValidate: true,
      startedAt,
    });
    expect(saved.configOverrides).toEqual({ B: { epochs: 7 } });
    expect(storage.getItem(BATCH_QUEUE_STORAGE_KEY)).toBeTruthy();
  });

  it('ignores saved queues older than 24h', () => {
    const storage = fakeStorage();
    writeBatchQueueState(storage, { queue: ['A'], remaining: ['A'], startedAt: 1_000 });
    expect(readSavedBatchQueue(storage, 1_000 + BATCH_QUEUE_MAX_AGE_MS - 1)).not.toBeNull();
    expect(readSavedBatchQueue(storage, 1_000 + BATCH_QUEUE_MAX_AGE_MS + 1)).toBeNull();
  });

  it('returns null for corrupt payloads and empty storage', () => {
    const storage = fakeStorage();
    expect(readSavedBatchQueue(storage)).toBeNull();
    storage.setItem(BATCH_QUEUE_STORAGE_KEY, '{not-json');
    expect(readSavedBatchQueue(storage)).toBeNull();
    storage.setItem(BATCH_QUEUE_STORAGE_KEY, JSON.stringify({ remaining: 'nope' }));
    expect(readSavedBatchQueue(storage)).toBeNull();
  });

  it('clears the entry when nothing remains, and via clearSavedBatchQueue', () => {
    const storage = fakeStorage();
    writeBatchQueueState(storage, { queue: ['A'], remaining: ['A'], startedAt: 5 });
    expect(readSavedBatchQueue(storage, 6)).not.toBeNull();
    // Fully completed batch: empty remaining must not offer a resume.
    writeBatchQueueState(storage, { queue: ['A'], remaining: [], startedAt: 5 });
    expect(readSavedBatchQueue(storage, 6)).toBeNull();
    writeBatchQueueState(storage, { queue: ['A'], remaining: ['A'], startedAt: 5 });
    clearSavedBatchQueue(storage);
    expect(readSavedBatchQueue(storage, 6)).toBeNull();
  });

  it('computes resume remaining: unstarted + cancelled re-run, failures excluded', () => {
    // A ok, B failed, C cancelled, D never started.
    expect(remainingAfterSummary(['A', 'B', 'C', 'D'], { ok: 1, failed: 1, cancelled: 1 }))
      .toEqual(['C', 'D']);
    expect(remainingAfterSummary(['A', 'B'], { ok: 2, failed: 0, cancelled: 0 })).toEqual([]);
  });

  it('tolerates missing storage (SSR / privacy mode)', () => {
    expect(readSavedBatchQueue(null)).toBeNull();
    expect(writeBatchQueueState(null, { queue: ['A'], remaining: ['A'] })).toBe(false);
    expect(() => clearSavedBatchQueue(null)).not.toThrow();
  });
});
