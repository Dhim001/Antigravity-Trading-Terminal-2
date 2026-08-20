import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  dismissMlBatchTerminal,
  getMlBatchTracker,
  ML_BATCH_TRACKER_STORAGE_KEY,
  rehydrateMlBatchTracker,
  resetMlBatchTrackerForTests,
  setMlBatchTrackerStorageForTests,
  startMlBatchTracking,
  stopMlBatchTracking,
  subscribeMlBatchTracker,
} from '@/lib/mlBatchTracker';

function makeFakeStorage() {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
  };
}

let fakeStorage;

function makeBatch(over = {}) {
  return {
    ok: true,
    batch_id: 'b-1',
    symbol: 'META',
    status: 'running',
    total: 2,
    completed: 1,
    failed: 0,
    cancelled: 0,
    items: [
      { item_id: 'i-0', seq: 0, strategy: 'ML_SIGNAL_BOOST', status: 'done', job_id: 'j-0' },
      { item_id: 'i-1', seq: 1, strategy: 'LSTM_DIRECTION', status: 'running', job_id: 'j-1' },
    ],
    ...over,
  };
}

function makeDeps({ batches = [], jobs = {} } = {}) {
  const queue = [...batches];
  return {
    fetchBatch: vi.fn(async () => (queue.length ? queue.shift() : makeBatch())),
    pollJob: vi.fn(async (jobId) => ({
      job: {
        job_id: jobId,
        status: 'running',
        progress: { pct: 42, phase: 'epoch', detail: '4/10' },
      },
    })),
    sleep: vi.fn(async () => {}),
    pollIntervalMs: 1,
  };
}

/** Flush the microtask queue so the fire-and-forget poll loop advances. */
async function flush(times = 8) {
  for (let i = 0; i < times; i += 1) {
    // eslint-disable-next-line no-await-in-loop
    await Promise.resolve();
  }
}

describe('mlBatchTracker', () => {
  beforeEach(() => {
    resetMlBatchTrackerForTests();
    fakeStorage = makeFakeStorage();
    setMlBatchTrackerStorageForTests(fakeStorage);
  });

  it('tracks a batch until terminal and summarizes', async () => {
    const deps = makeDeps({
      batches: [
        makeBatch(),
        makeBatch({ status: 'done', completed: 2, items: [
          { item_id: 'i-0', seq: 0, strategy: 'ML_SIGNAL_BOOST', status: 'done', job_id: 'j-0' },
          { item_id: 'i-1', seq: 1, strategy: 'LSTM_DIRECTION', status: 'done', job_id: 'j-1' },
        ] }),
      ],
    });
    startMlBatchTracking({ batchId: 'b-1', symbol: 'meta', meta: { queue: ['ML_SIGNAL_BOOST'] }, ...deps });

    let s = getMlBatchTracker();
    expect(s.active).toBe(true);
    expect(s.batchId).toBe('b-1');
    expect(s.symbol).toBe('META');

    await flush();
    s = getMlBatchTracker();
    expect(s.active).toBe(false);
    expect(s.terminal).not.toBeNull();
    expect(s.terminal.status).toBe('done');
    expect(s.terminal.completed).toEqual(['ML_SIGNAL_BOOST', 'LSTM_DIRECTION']);
    expect(s.activeJobId).toBeNull();
    // Terminal clears the persisted entry.
    expect(fakeStorage.getItem(ML_BATCH_TRACKER_STORAGE_KEY)).toBeNull();
  });

  it('persists the tracked batch in sessionStorage while active', async () => {
    const deps = makeDeps();
    startMlBatchTracking({ batchId: 'b-1', symbol: 'META', ...deps });
    const raw = fakeStorage.getItem(ML_BATCH_TRACKER_STORAGE_KEY);
    expect(raw).toBeTruthy();
    expect(JSON.parse(raw).batchId).toBe('b-1');
    await flush();
  });

  it('polls the running item job for live per-item progress', async () => {
    const deps = makeDeps();
    startMlBatchTracking({ batchId: 'b-1', symbol: 'META', ...deps });
    await flush();
    const s = getMlBatchTracker();
    expect(deps.pollJob).toHaveBeenCalledWith('j-1');
    expect(s.activeJobId).toBe('j-1');
    expect(s.activeJobProgress?.pct).toBe(42);
  });

  it('keeps polling through transient errors (never abandons)', async () => {
    const doneBatch = makeBatch({ status: 'done', completed: 2, items: [
      { item_id: 'i-0', seq: 0, strategy: 'ML_SIGNAL_BOOST', status: 'done', job_id: 'j-0' },
      { item_id: 'i-1', seq: 1, strategy: 'LSTM_DIRECTION', status: 'done', job_id: 'j-1' },
    ] });
    const responses = [new Error('Failed to fetch'), new Error('Failed to fetch'), doneBatch];
    const deps = makeDeps();
    deps.fetchBatch = vi.fn(async () => {
      const next = responses.shift() || doneBatch;
      if (next instanceof Error) throw next;
      return next;
    });
    startMlBatchTracking({ batchId: 'b-1', symbol: 'META', ...deps });
    await flush();
    const s = getMlBatchTracker();
    expect(s.active).toBe(false);
    expect(s.terminal?.status).toBe('done');
  });

  it('marks the batch lost on non-transient errors', async () => {
    const deps = makeDeps();
    deps.fetchBatch = vi.fn(async () => {
      const err = new Error('batch not found (http 404)');
      err.status = 404;
      throw err;
    });
    startMlBatchTracking({ batchId: 'b-gone', symbol: 'META', ...deps });
    await flush();
    const s = getMlBatchTracker();
    expect(s.active).toBe(false);
    expect(s.terminal?.status).toBe('lost');
    expect(fakeStorage.getItem(ML_BATCH_TRACKER_STORAGE_KEY)).toBeNull();
  });

  it('restart with the same batchId is a no-op', async () => {
    const deps = makeDeps();
    startMlBatchTracking({ batchId: 'b-1', symbol: 'META', ...deps });
    const first = getMlBatchTracker();
    startMlBatchTracking({ batchId: 'b-1', symbol: 'META', ...deps });
    expect(getMlBatchTracker()).toBe(first);
    await flush();
  });

  it('stop clears state and storage', async () => {
    const deps = makeDeps();
    startMlBatchTracking({ batchId: 'b-1', symbol: 'META', ...deps });
    stopMlBatchTracking();
    const s = getMlBatchTracker();
    expect(s.active).toBe(false);
    expect(s.batchId).toBeNull();
    expect(fakeStorage.getItem(ML_BATCH_TRACKER_STORAGE_KEY)).toBeNull();
  });

  it('rehydrate resumes from sessionStorage', async () => {
    fakeStorage.setItem(ML_BATCH_TRACKER_STORAGE_KEY, JSON.stringify({
      batchId: 'b-saved', symbol: 'META', meta: { queue: ['ML_SIGNAL_BOOST'] }, trackingSince: 1,
    }));
    const deps = makeDeps();
    await rehydrateMlBatchTracker({ symbol: 'META', ...deps });
    const s = getMlBatchTracker();
    expect(s.active).toBe(true);
    expect(s.batchId).toBe('b-saved');
    expect(s.meta?.queue).toEqual(['ML_SIGNAL_BOOST']);
    await flush();
  });

  it('rehydrate falls back to the server active-batch endpoint', async () => {
    const deps = makeDeps();
    const fetchActive = vi.fn(async () => ({ ok: true, batch: makeBatch({ status: 'running' }) }));
    await rehydrateMlBatchTracker({ symbol: 'META', fetchActive, ...deps });
    const s = getMlBatchTracker();
    expect(fetchActive).toHaveBeenCalledWith('META');
    expect(s.active).toBe(true);
    expect(s.batchId).toBe('b-1');
    await flush();
  });

  it('rehydrate is a no-op when nothing is active anywhere', async () => {
    const fetchActive = vi.fn(async () => ({ ok: true, batch: null }));
    await rehydrateMlBatchTracker({ symbol: 'META', fetchActive });
    expect(getMlBatchTracker().active).toBe(false);
  });

  it('rehydrate ignores a terminal server batch', async () => {
    const fetchActive = vi.fn(async () => ({ ok: true, batch: makeBatch({ status: 'done' }) }));
    await rehydrateMlBatchTracker({ symbol: 'META', fetchActive });
    expect(getMlBatchTracker().active).toBe(false);
  });

  it('notifies subscribers on state changes', async () => {
    const listener = vi.fn();
    const unsub = subscribeMlBatchTracker(listener);
    const deps = makeDeps();
    startMlBatchTracking({ batchId: 'b-1', symbol: 'META', ...deps });
    expect(listener).toHaveBeenCalled();
    unsub();
    await flush();
  });

  it('dismiss clears terminal state but not a live run', async () => {
    const deps = makeDeps({
      batches: [makeBatch({ status: 'done', completed: 2, items: [
        { item_id: 'i-0', seq: 0, strategy: 'ML_SIGNAL_BOOST', status: 'done', job_id: 'j-0' },
        { item_id: 'i-1', seq: 1, strategy: 'LSTM_DIRECTION', status: 'done', job_id: 'j-1' },
      ] })],
    });
    startMlBatchTracking({ batchId: 'b-1', symbol: 'META', ...deps });
    await flush();
    expect(getMlBatchTracker().terminal).not.toBeNull();
    dismissMlBatchTerminal();
    expect(getMlBatchTracker().terminal).toBeNull();
    expect(getMlBatchTracker().batchId).toBeNull();
  });
});
