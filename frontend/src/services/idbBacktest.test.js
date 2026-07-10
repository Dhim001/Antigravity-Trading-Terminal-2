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
});
