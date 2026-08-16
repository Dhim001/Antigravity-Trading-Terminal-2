/**
 * Batch item observability helpers (ML Lab Phase 3): error→reason mapping for
 * per-item toasts, poll diffing for newly failed items, and the drawer row
 * view models that BatchDetailsDrawer renders.
 */
import { describe, it, expect } from 'vitest';
import {
  batchItemDurationMs,
  buildBatchDrawerRows,
  describeBatchItemError,
  diffNewBatchItemFailures,
  synthesizeLocalBatchSummary,
  truncateBatchError,
} from './batchItemStatus';

function item(over = {}) {
  return {
    item_id: 'it-1',
    seq: 0,
    strategy: 'ML_SIGNAL_BOOST',
    status: 'pending',
    error: null,
    job_id: null,
    created_at: '2026-08-01T10:00:00Z',
    updated_at: '2026-08-01T10:00:00Z',
    ...over,
  };
}

function batch(items, over = {}) {
  return {
    batch_id: 'batch-1',
    symbol: 'ETHUSDT',
    status: 'running',
    total: items.length,
    items,
    ...over,
  };
}

describe('describeBatchItemError — actionable reason mapping', () => {
  it('maps insufficient candles / data history', () => {
    expect(describeBatchItemError('insufficient candles (123)')).toBe('not enough historical data');
    expect(describeBatchItemError('Need >= 450 candles for 5m validation (got 120 after expanding to 6mo)'))
      .toBe('not enough historical data');
    expect(describeBatchItemError('not enough data to build features')).toBe('not enough historical data');
  });

  it('maps rate limits / full queue', () => {
    expect(describeBatchItemError('HTTP 429 too many requests')).toBe('training queue full');
    expect(describeBatchItemError('train queue is full, try later')).toBe('training queue full');
    expect(describeBatchItemError('rate limit exceeded')).toBe('training queue full');
  });

  it('maps OOM / worker-killed / RSS cap', () => {
    expect(describeBatchItemError('worker killed: RSS limit exceeded (2048MB)')).toBe('out of memory');
    expect(describeBatchItemError('torch.cuda.OutOfMemoryError: out of memory')).toBe('out of memory');
    expect(describeBatchItemError('MemoryError: cannot allocate array')).toBe('out of memory');
  });

  it('maps DQ gate rejections', () => {
    expect(describeBatchItemError('DQ gate rejected batch: 42% gaps')).toBe('data quality gate rejected');
    expect(describeBatchItemError('data quality gate rejected')).toBe('data quality gate rejected');
  });

  it('maps missing dependencies', () => {
    expect(describeBatchItemError("ModuleNotFoundError: No module named 'stable_baselines3'"))
      .toBe('missing dependency');
    expect(describeBatchItemError('ImportError: torch is not installed')).toBe('missing dependency');
  });

  it('maps timeouts', () => {
    expect(describeBatchItemError('job timed out after 900s')).toBe('timed out');
    expect(describeBatchItemError('TimeoutError: deadline exceeded')).toBe('timed out');
  });

  it('falls back to a truncated raw snippet for unknown errors', () => {
    expect(describeBatchItemError('weird bespoke failure')).toBe('weird bespoke failure');
    const long = `x ${'y'.repeat(200)}`;
    const out = describeBatchItemError(long);
    expect(out.length).toBeLessThanOrEqual(90);
    expect(out.endsWith('…')).toBe(true);
    expect(describeBatchItemError('')).toBe('training failed');
    expect(describeBatchItemError(null)).toBe('training failed');
  });
});

describe('truncateBatchError', () => {
  it('collapses whitespace and caps length', () => {
    expect(truncateBatchError('line one\n  line   two')).toBe('line one line two');
    expect(truncateBatchError('short')).toBe('short');
    expect(truncateBatchError('')).toBe('');
    const capped = truncateBatchError('z'.repeat(200), 40);
    expect(capped.length).toBe(40);
  });
});

describe('diffNewBatchItemFailures', () => {
  it('surfaces items that newly transitioned to error, once', () => {
    const prev = batch([
      item({ item_id: 'a', status: 'done' }),
      item({ item_id: 'b', status: 'running' }),
    ]);
    const next = batch([
      item({ item_id: 'a', status: 'done' }),
      item({ item_id: 'b', status: 'error', error: 'boom' }),
    ]);
    const fresh = diffNewBatchItemFailures(prev, next);
    expect(fresh.map((i) => i.item_id)).toEqual(['b']);
    // Polling the same payload again must not re-toast.
    expect(diffNewBatchItemFailures(next, next)).toEqual([]);
  });

  it('treats a null previous batch as all-new (first poll)', () => {
    const next = batch([item({ item_id: 'a', status: 'error', error: 'x' })]);
    expect(diffNewBatchItemFailures(null, next).map((i) => i.item_id)).toEqual(['a']);
  });

  it('re-toasts after a server retry (error → pending → error)', () => {
    const failed = batch([item({ item_id: 'a', status: 'error', error: 'x' })]);
    const retried = batch([item({ item_id: 'a', status: 'pending' })]);
    const failedAgain = batch([item({ item_id: 'a', status: 'error', error: 'x again' })]);
    expect(diffNewBatchItemFailures(failed, retried)).toEqual([]);
    const fresh = diffNewBatchItemFailures(retried, failedAgain);
    expect(fresh.map((i) => i.item_id)).toEqual(['a']);
  });
});

describe('batchItemDurationMs', () => {
  const now = Date.parse('2026-08-01T10:10:00Z');

  it('is null for pending items', () => {
    expect(batchItemDurationMs(item({ status: 'pending' }), now)).toBeNull();
  });

  it('measures created→updated for terminal items', () => {
    const done = item({
      status: 'done',
      created_at: '2026-08-01T10:00:00Z',
      updated_at: '2026-08-01T10:04:30Z',
    });
    expect(batchItemDurationMs(done, now)).toBe(270_000);
  });

  it('measures created→now for running items', () => {
    const running = item({
      status: 'running',
      created_at: '2026-08-01T10:09:00Z',
      updated_at: '2026-08-01T10:09:00Z',
    });
    expect(batchItemDurationMs(running, now)).toBe(60_000);
  });

  it('returns null for unparsable timestamps', () => {
    expect(batchItemDurationMs(item({ status: 'done', created_at: null }), now)).toBeNull();
    expect(batchItemDurationMs(item({ status: 'done', updated_at: 'not-a-date' }), now)).toBeNull();
  });
});

describe('buildBatchDrawerRows — drawer rendering view models', () => {
  it('maps items to rows with status, duration, truncated error and hint', () => {
    const rows = buildBatchDrawerRows(batch([
      item({ item_id: 'a', seq: 0, status: 'done', updated_at: '2026-08-01T10:01:00Z', job_id: 'j-1' }),
      item({ item_id: 'b', seq: 1, strategy: 'LSTM_DIRECTION', status: 'error', error: 'insufficient candles (12)' }),
      item({ item_id: 'c', seq: 2, strategy: 'RL_PPO_AGENT', status: 'pending' }),
    ]));
    expect(rows).toHaveLength(3);
    expect(rows[0]).toMatchObject({
      key: 'a', status: 'done', durationMs: 60_000, jobId: 'j-1', errorHint: null,
    });
    expect(rows[1]).toMatchObject({
      key: 'b',
      status: 'error',
      errorHint: 'not enough historical data',
      error: 'insufficient candles (12)',
    });
    expect(rows[1].errorFull).toBe('insufficient candles (12)');
    expect(rows[2]).toMatchObject({ key: 'c', status: 'pending', durationMs: null });
  });

  it('keeps server order and tolerates unknown statuses', () => {
    const rows = buildBatchDrawerRows(batch([
      item({ item_id: 'x', seq: 5, status: 'weird' }),
      item({ item_id: 'y', seq: 2, status: 'skipped' }),
    ]));
    expect(rows.map((r) => r.key)).toEqual(['x', 'y']);
    expect(rows[0].status).toBe('pending'); // unknown → pending fallback
    expect(rows[1].status).toBe('skipped');
  });

  it('handles a missing/empty batch', () => {
    expect(buildBatchDrawerRows(null)).toEqual([]);
    expect(buildBatchDrawerRows({})).toEqual([]);
  });
});

describe('synthesizeLocalBatchSummary — local-queue drawer fallback', () => {
  it('maps queue + summary onto a batch-shaped payload', () => {
    const out = synthesizeLocalBatchSummary({
      queue: ['A', 'B', 'C', 'D'],
      summary: {
        completed: ['A'], failedIds: ['B'], cancelledIds: ['C'], stoppedEarly: true,
      },
      errors: { B: 'job timed out after 900s' },
      symbol: 'ETHUSDT',
    });
    expect(out.local).toBe(true);
    expect(out.status).toBe('cancelled');
    expect(out.items.map((i) => [i.strategy, i.status])).toEqual([
      ['A', 'done'],
      ['B', 'error'],
      ['C', 'cancelled'],
      ['D', 'pending'],
    ]);
    expect(out.items[1].error).toBe('job timed out after 900s');
    // Rows render through the same builder as server batches.
    const rows = buildBatchDrawerRows(out);
    expect(rows[1].errorHint).toBe('timed out');
  });

  it('derives terminal status like the server runner', () => {
    const allFailed = synthesizeLocalBatchSummary({
      queue: ['A'],
      summary: { completed: [], failedIds: ['A'], cancelledIds: [] },
    });
    expect(allFailed.status).toBe('failed');
    const allDone = synthesizeLocalBatchSummary({
      queue: ['A'],
      summary: { completed: ['A'], failedIds: [], cancelledIds: [] },
    });
    expect(allDone.status).toBe('done');
  });
});
