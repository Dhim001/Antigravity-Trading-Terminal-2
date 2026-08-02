/** Scope selection + sequential batch runner (pure helpers; no DOM / RTL). */
import { describe, it, expect, vi } from 'vitest';
import { selectStrategiesForScope } from './batchTrainScope';
import { formatBatchTrainSummary, runBatchTrainQueue } from './batchTrainRunner';

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

describe('runBatchTrainQueue', () => {
  it('trains sequentially and counts successes', async () => {
    const order = [];
    const onTrainStrategy = vi.fn(async (id) => { order.push(id); });
    const summary = await runBatchTrainQueue({
      queue: ['A', 'B', 'C'],
      onTrainStrategy,
    });
    expect(order).toEqual(['A', 'B', 'C']);
    expect(summary).toMatchObject({ ok: 3, failed: 0, cancelled: false, total: 3 });
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
    expect(summary.cancelled).toBe(true);
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
  it('formats success and cancel messages', () => {
    expect(formatBatchTrainSummary({ ok: 3, failed: 0, cancelled: false, total: 3 }))
      .toBe('Trained 3/3 strategies.');
    expect(formatBatchTrainSummary({ ok: 2, failed: 1, cancelled: false, total: 3 }))
      .toBe('Trained 2/3 strategies. 1 failed.');
    expect(formatBatchTrainSummary({ ok: 1, failed: 0, cancelled: true, total: 3 }))
      .toBe('Stopped early. Trained 1/3. 0 failed.');
  });
});
