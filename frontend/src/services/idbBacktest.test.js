import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import 'fake-indexeddb/auto';
import {
  idbSaveBacktest,
  idbLoadBacktest,
  idbClearBacktest,
  idbClearAllForTests,
} from './idbBacktest';

describe('idbBacktest', () => {
  beforeAll(async () => {
    await idbClearAllForTests();
  });

  afterAll(async () => {
    await idbClearAllForTests();
  });

  it('saves and loads a backtest run', async () => {
    const payload = { run_id: 'run-1', total_pnl: 42, trades: [{ id: 't1' }] };
    const saved = await idbSaveBacktest('run-1', payload);
    expect(saved).toBe(true);
    const loaded = await idbLoadBacktest('run-1');
    expect(loaded?.run_id).toBe('run-1');
    expect(loaded?.total_pnl).toBe(42);
  });

  it('clears a stored run', async () => {
    await idbSaveBacktest('run-2', { run_id: 'run-2' });
    await idbClearBacktest('run-2');
    const loaded = await idbLoadBacktest('run-2');
    expect(loaded).toBeNull();
  });

  it('prunes to the newest MAX_IDB_RUNS via the key-only cursor (#17)', async () => {
    for (let i = 0; i < 12; i++) {
      await idbSaveBacktest(`prune-${i}`, {
        run_id: `prune-${i}`,
        blob: 'x'.repeat(2000),
      });
      // Ensure distinct savedAt ordering for deterministic eviction.
      await new Promise((r) => setTimeout(r, 2));
    }
    // MAX_IDB_RUNS = 10 → the oldest runs were pruned.
    expect(await idbLoadBacktest('prune-0')).toBeNull();
    expect(await idbLoadBacktest('prune-1')).toBeNull();
    expect((await idbLoadBacktest('prune-11'))?.run_id).toBe('prune-11');
    expect((await idbLoadBacktest('prune-10'))?.run_id).toBe('prune-10');
  });
});
