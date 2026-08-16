/**
 * Server-side batch train orchestration (pure helpers; no DOM / RTL).
 * Covers the Phase 2 integration contract: server batch path, fallback to the
 * local queue when the backend predates the API, and server cancel / retry.
 */
import { describe, it, expect, vi } from 'vitest';
import {
  buildBatchItems,
  deriveServerProgress,
  isBatchApiUnavailableError,
  isServerBatchTerminal,
  makeBatchIdempotencyKey,
  retryServerBatch,
  runServerBatchTrain,
  summarizeServerBatch,
  trySubmitServerBatch,
} from './batchTrainServerRunner';

const noSleep = async () => {};

function batchPayload({ status = 'running', items = [], cancelRequested = false } = {}) {
  const completed = items.filter((it) => it.status === 'done').length;
  const failed = items.filter((it) => it.status === 'error').length;
  const cancelled = items.filter((it) => it.status === 'cancelled' || it.status === 'skipped').length;
  return {
    ok: true,
    batch_id: 'batch-1',
    symbol: 'ETHUSDT',
    status,
    total: items.length,
    completed,
    failed,
    cancelled,
    cancel_requested: cancelRequested,
    items: items.map((it, i) => ({ item_id: `it-${i}`, seq: i, ...it })),
  };
}

describe('isBatchApiUnavailableError', () => {
  it('flags 404 route-not-found and network failures as unavailable', () => {
    expect(isBatchApiUnavailableError(new TypeError('fetch failed'))).toBe(true);
    expect(isBatchApiUnavailableError(new Error('Failed to fetch'))).toBe(true);
    expect(isBatchApiUnavailableError(new Error('HTTP 404'))).toBe(true);
    expect(isBatchApiUnavailableError(new Error(
      'API route not found (/api/v1/ml/batch-train) — restart the backend',
    ))).toBe(true);
    expect(isBatchApiUnavailableError(new Error('NetworkError when attempting to fetch resource.'))).toBe(true);
    expect(isBatchApiUnavailableError(new Error('connect ECONNREFUSED 127.0.0.1:8000'))).toBe(true);
  });

  it('does not flag genuine API errors as unavailable', () => {
    expect(isBatchApiUnavailableError(new Error('symbol required'))).toBe(false);
    expect(isBatchApiUnavailableError(new Error('items capped at 50 per batch'))).toBe(false);
    // A 404 from a *new* backend for an unknown batch id is not "API missing".
    expect(isBatchApiUnavailableError(new Error('batch not found'))).toBe(false);
    expect(isBatchApiUnavailableError(new Error('Request timed out after 120000ms'))).toBe(false);
    expect(isBatchApiUnavailableError(null)).toBe(false);
  });
});

describe('buildBatchItems', () => {
  it('merges timeframe/window with per-strategy overrides and validate_after', () => {
    const items = buildBatchItems(['ML_SIGNAL_BOOST', 'LSTM_DIRECTION'], {
      timeframe: '5m',
      trainingWindow: '6',
      autoValidate: true,
      configOverrides: {
        ML_SIGNAL_BOOST: { gbm_max_iter: 500 },
        LSTM_DIRECTION: { epochs: 40, hidden_dim: 128 },
      },
    });
    expect(items).toEqual([
      {
        strategy: 'ML_SIGNAL_BOOST',
        config: { timeframe: '5m', training_window_months: 6, gbm_max_iter: 500 },
        validate_after: true,
      },
      {
        strategy: 'LSTM_DIRECTION',
        config: { timeframe: '5m', training_window_months: 6, epochs: 40, hidden_dim: 128 },
        validate_after: true,
      },
    ]);
  });

  it('lets per-strategy overrides win on key conflicts', () => {
    const items = buildBatchItems(['A'], {
      timeframe: '1m',
      trainingWindow: '3',
      configOverrides: { A: { timeframe: '15m', training_window_months: 12 } },
    });
    expect(items[0].config).toEqual({ timeframe: '15m', training_window_months: 12 });
    expect(items[0].validate_after).toBe(false);
  });

  it('tolerates missing overrides and drops invalid windows', () => {
    const items = buildBatchItems(['A'], { timeframe: '1m', trainingWindow: 'nope' });
    expect(items).toEqual([{ strategy: 'A', config: { timeframe: '1m' }, validate_after: false }]);
  });
});

describe('makeBatchIdempotencyKey', () => {
  it('returns unique non-empty keys', () => {
    const a = makeBatchIdempotencyKey();
    const b = makeBatchIdempotencyKey();
    expect(typeof a).toBe('string');
    expect(a.length).toBeGreaterThan(8);
    expect(a).not.toBe(b);
  });
});

describe('trySubmitServerBatch', () => {
  const items = [{ strategy: 'A', config: {}, validate_after: false }];

  it('posts the batch and returns the batch_id (server path available)', async () => {
    const submit = vi.fn(async (body) => ({ ok: true, batch_id: 'b-1', status: 'queued', body }));
    const res = await trySubmitServerBatch({
      symbol: 'ethusdt', items, submit, idempotencyKey: 'key-1',
    });
    expect(res).toEqual({ batchId: 'b-1', idempotent: false });
    expect(submit).toHaveBeenCalledTimes(1);
    expect(submit).toHaveBeenCalledWith({
      symbol: 'ethusdt',
      items,
      concurrency: 1,
      fail_fast: false,
      idempotency_key: 'key-1',
    });
  });

  it('marks idempotent replays', async () => {
    const submit = vi.fn(async () => ({ ok: true, batch_id: 'b-9', status: 'running', idempotent: true }));
    const res = await trySubmitServerBatch({ symbol: 'ETHUSDT', items, submit });
    expect(res).toEqual({ batchId: 'b-9', idempotent: true });
  });

  it('passes an explicit Phase 4 schedule through (omitted otherwise)', async () => {
    const submit = vi.fn(async () => ({ ok: true, batch_id: 'b-2', status: 'queued' }));
    await trySubmitServerBatch({ symbol: 'ETHUSDT', items, submit, schedule: 'cost_desc' });
    expect(submit).toHaveBeenCalledWith({
      symbol: 'ETHUSDT',
      items,
      concurrency: 1,
      fail_fast: false,
      schedule: 'cost_desc',
    });

    submit.mockClear();
    await trySubmitServerBatch({ symbol: 'ETHUSDT', items, submit });
    expect(submit).toHaveBeenCalledWith({
      symbol: 'ETHUSDT',
      items,
      concurrency: 1,
      fail_fast: false,
    });
  });

  it('signals fallback on 404 / network errors (older backend)', async () => {
    for (const err of [
      new Error('API route not found (/api/v1/ml/batch-train) — restart the backend'),
      new Error('HTTP 404'),
      new TypeError('fetch failed'),
      new Error('Failed to fetch'),
    ]) {
      const submit = vi.fn(async () => { throw err; });
      const res = await trySubmitServerBatch({ symbol: 'ETHUSDT', items, submit });
      expect(res.unavailable).toBe(true);
      expect(res.batchId).toBeUndefined();
    }
  });

  it('falls back when no symbol is available, without calling submit', async () => {
    const submit = vi.fn();
    const res = await trySubmitServerBatch({ symbol: '', items, submit });
    expect(res).toMatchObject({ unavailable: true, reason: 'no-symbol' });
    expect(submit).not.toHaveBeenCalled();
  });

  it('rethrows genuine API errors (400/500) instead of falling back', async () => {
    const submit = vi.fn(async () => { throw new Error('items capped at 50 per batch'); });
    await expect(trySubmitServerBatch({ symbol: 'ETHUSDT', items, submit }))
      .rejects.toThrow('items capped at 50 per batch');
  });

  it('throws when the server omits batch_id', async () => {
    const submit = vi.fn(async () => ({ ok: true, status: 'queued' }));
    await expect(trySubmitServerBatch({ symbol: 'ETHUSDT', items, submit }))
      .rejects.toThrow('batch_id');
  });
});

describe('deriveServerProgress', () => {
  it('points at the running item (1-based) and totals from the batch', () => {
    const batch = batchPayload({
      items: [
        { strategy: 'A', status: 'done' },
        { strategy: 'B', status: 'running' },
        { strategy: 'C', status: 'pending' },
      ],
    });
    expect(deriveServerProgress(batch)).toEqual({ index: 2, total: 3, strategy: 'B' });
  });

  it('reports finished count when between items', () => {
    const batch = batchPayload({
      status: 'done',
      items: [
        { strategy: 'A', status: 'done' },
        { strategy: 'B', status: 'error' },
      ],
    });
    expect(deriveServerProgress(batch)).toEqual({ index: 2, total: 2, strategy: null });
  });
});

describe('summarizeServerBatch', () => {
  it('maps counts and id lists onto the local runner summary shape', () => {
    const batch = batchPayload({
      status: 'failed',
      items: [
        { strategy: 'A', status: 'done' },
        { strategy: 'B', status: 'error' },
        { strategy: 'C', status: 'done' },
      ],
    });
    expect(summarizeServerBatch(batch)).toMatchObject({
      ok: 2,
      failed: 1,
      cancelled: 0,
      stoppedEarly: false,
      total: 3,
      completed: ['A', 'C'],
      failedIds: ['B'],
      cancelledIds: [],
      batchId: 'batch-1',
      server: true,
    });
  });

  it('marks cancelled batches as stopped early (skipped items count as cancelled)', () => {
    const batch = batchPayload({
      status: 'cancelled',
      cancelRequested: true,
      items: [
        { strategy: 'A', status: 'done' },
        { strategy: 'B', status: 'cancelled' },
        { strategy: 'C', status: 'skipped' },
      ],
    });
    const summary = summarizeServerBatch(batch);
    expect(summary).toMatchObject({ ok: 1, failed: 0, cancelled: 2, stoppedEarly: true });
    expect(summary.cancelledIds).toEqual(['B', 'C']);
  });

  it('derives counts from items when top-level counters are missing', () => {
    const batch = batchPayload({
      status: 'done',
      items: [{ strategy: 'A', status: 'done' }],
    });
    delete batch.completed;
    delete batch.failed;
    delete batch.cancelled;
    expect(summarizeServerBatch(batch)).toMatchObject({ ok: 1, failed: 0, cancelled: 0 });
  });
});

describe('isServerBatchTerminal', () => {
  it('only stops on done/failed/cancelled', () => {
    expect(isServerBatchTerminal({ status: 'done' })).toBe(true);
    expect(isServerBatchTerminal({ status: 'failed' })).toBe(true);
    expect(isServerBatchTerminal({ status: 'cancelled' })).toBe(true);
    expect(isServerBatchTerminal({ status: 'queued' })).toBe(false);
    expect(isServerBatchTerminal({ status: 'running' })).toBe(false);
    expect(isServerBatchTerminal(null)).toBe(false);
  });
});

describe('runServerBatchTrain — server batch path', () => {
  it('polls until terminal and summarizes the outcome', async () => {
    const fetchBatch = vi.fn()
      .mockResolvedValueOnce(batchPayload({
        status: 'running',
        items: [
          { strategy: 'A', status: 'running' },
          { strategy: 'B', status: 'pending' },
        ],
      }))
      .mockResolvedValueOnce(batchPayload({
        status: 'running',
        items: [
          { strategy: 'A', status: 'done' },
          { strategy: 'B', status: 'running' },
        ],
      }))
      .mockResolvedValueOnce(batchPayload({
        status: 'done',
        items: [
          { strategy: 'A', status: 'done' },
          { strategy: 'B', status: 'done' },
        ],
      }));
    const progress = [];
    const updates = [];
    const summary = await runServerBatchTrain({
      batchId: 'batch-1',
      fetchBatch,
      onProgress: (p) => progress.push(p),
      onBatchUpdate: (b) => updates.push(b.status),
      sleep: noSleep,
    });
    expect(fetchBatch).toHaveBeenCalledTimes(3);
    expect(fetchBatch).toHaveBeenCalledWith('batch-1');
    expect(updates).toEqual(['running', 'running', 'done']);
    expect(progress[0]).toEqual({ index: 1, total: 2, strategy: 'A' });
    expect(progress[1]).toEqual({ index: 2, total: 2, strategy: 'B' });
    expect(summary).toMatchObject({
      ok: 2, failed: 0, cancelled: 0, stoppedEarly: false, total: 2,
      completed: ['A', 'B'], server: true, batchId: 'batch-1',
    });
  });

  it('surfaces failed items in the summary', async () => {
    const fetchBatch = vi.fn().mockResolvedValueOnce(batchPayload({
      status: 'done',
      items: [
        { strategy: 'A', status: 'done' },
        { strategy: 'B', status: 'error', error: 'insufficient candles' },
      ],
    }));
    const summary = await runServerBatchTrain({ batchId: 'b', fetchBatch, sleep: noSleep });
    expect(summary).toMatchObject({ ok: 1, failed: 1, failedIds: ['B'], stoppedEarly: false });
  });

  it('sends a server cancel when shouldCancel flips, then stops on cancelled', async () => {
    let cancelWanted = false;
    const cancelBatch = vi.fn(async () => ({ ok: true }));
    const fetchBatch = vi.fn()
      .mockImplementationOnce(async () => {
        cancelWanted = true; // user pressed Cancel while this poll was in flight
        return batchPayload({
          status: 'running',
          items: [
            { strategy: 'A', status: 'done' },
            { strategy: 'B', status: 'running' },
            { strategy: 'C', status: 'pending' },
          ],
        });
      })
      .mockImplementationOnce(async () => batchPayload({
        status: 'cancelled',
        cancelRequested: true,
        items: [
          { strategy: 'A', status: 'done' },
          { strategy: 'B', status: 'cancelled' },
          { strategy: 'C', status: 'skipped' },
        ],
      }));
    const summary = await runServerBatchTrain({
      batchId: 'batch-1',
      fetchBatch,
      cancelBatch,
      shouldCancel: () => cancelWanted,
      sleep: noSleep,
    });
    expect(cancelBatch).toHaveBeenCalledTimes(1);
    expect(cancelBatch).toHaveBeenCalledWith('batch-1');
    expect(summary).toMatchObject({
      ok: 1, failed: 0, cancelled: 2, stoppedEarly: true,
      cancelledIds: ['B', 'C'],
    });
  });

  it('tolerates transient poll errors and keeps tracking', async () => {
    const fetchBatch = vi.fn()
      .mockRejectedValueOnce(new Error('Failed to fetch'))
      .mockResolvedValueOnce(batchPayload({
        status: 'done',
        items: [{ strategy: 'A', status: 'done' }],
      }));
    const summary = await runServerBatchTrain({ batchId: 'b', fetchBatch, sleep: noSleep });
    expect(fetchBatch).toHaveBeenCalledTimes(2);
    expect(summary.ok).toBe(1);
  });

  it('gives up after too many consecutive poll failures', async () => {
    const fetchBatch = vi.fn(async () => { throw new Error('Failed to fetch'); });
    await expect(runServerBatchTrain({
      batchId: 'b', fetchBatch, sleep: noSleep, maxConsecutivePollErrors: 3,
    })).rejects.toThrow('Failed to fetch');
    expect(fetchBatch).toHaveBeenCalledTimes(3);
  });

  it('throws immediately on non-transient poll errors (e.g. batch not found)', async () => {
    const fetchBatch = vi.fn(async () => { throw new Error('batch not found'); });
    await expect(runServerBatchTrain({ batchId: 'b', fetchBatch, sleep: noSleep }))
      .rejects.toThrow('batch not found');
    expect(fetchBatch).toHaveBeenCalledTimes(1);
  });

  it('validates arguments', async () => {
    await expect(runServerBatchTrain({ fetchBatch: async () => ({}) })).rejects.toThrow('batchId');
    await expect(runServerBatchTrain({ batchId: 'b' })).rejects.toThrow('fetchBatch');
  });
});

describe('retryServerBatch — server retry calls', () => {
  it('calls the retry endpoint with the batch id and normalizes the reply', async () => {
    const retry = vi.fn(async (id) => ({ ok: true, batch_id: id, status: 'queued', requeued: 2 }));
    const res = await retryServerBatch({ batchId: 'batch-1', retry });
    expect(retry).toHaveBeenCalledTimes(1);
    expect(retry).toHaveBeenCalledWith('batch-1');
    expect(res).toEqual({ batchId: 'batch-1', status: 'queued', requeued: 2 });
  });

  it('throws when the server reports failure', async () => {
    const retry = vi.fn(async () => ({ ok: false, error: 'batch not found' }));
    await expect(retryServerBatch({ batchId: 'gone', retry })).rejects.toThrow('batch not found');
  });

  it('propagates endpoint errors so the caller can fall back to the local queue', async () => {
    const retry = vi.fn(async () => { throw new Error('HTTP 404'); });
    await expect(retryServerBatch({ batchId: 'b', retry })).rejects.toThrow('HTTP 404');
    expect(isBatchApiUnavailableError(new Error('HTTP 404'))).toBe(true);
  });
});
