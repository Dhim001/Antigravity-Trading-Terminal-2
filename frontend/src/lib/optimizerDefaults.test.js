/** @vitest-environment node */
import { describe, expect, it } from 'vitest';
import {
  defaultSweepEnabled,
  DEFAULT_SWEEP_OBJECTIVE,
  getDefaultMinTrades,
  getDefaultObjective,
  isExploratorySweep,
} from './optimizerDefaults';
import { compareOptimizationRuns } from './optimizationCompare';

describe('optimizerDefaults', () => {
  it('defaults to calmar objective', () => {
    expect(DEFAULT_SWEEP_OBJECTIVE).toBe('calmar_ratio');
  });

  it('uses robust_score for ML strategies', () => {
    expect(getDefaultObjective('LSTM_DIRECTION')).toBe('robust_score');
    expect(getDefaultObjective('MACD_RSI')).toBe('calmar_ratio');
  });

  it('uses higher min trades for ML and agent strategies', () => {
    expect(getDefaultMinTrades('ML_SIGNAL_BOOST')).toBe(5);
    expect(getDefaultMinTrades('CHART_AGENT')).toBe(3);
    expect(getDefaultMinTrades('MACD_RSI')).toBe(1);
  });

  it('enables strategy-specific params for MACD_RSI', () => {
    const defs = [
      { key: 'rsi_length' },
      { key: 'macd_slow' },
      { key: 'trailing_stop_percent' },
      { key: 'allocation' },
    ];
    const enabled = defaultSweepEnabled('MACD_RSI', defs);
    expect(enabled.rsi_length).toBe(true);
    expect(enabled.macd_slow).toBe(true);
    expect(enabled.trailing_stop_percent).toBe(true);
    expect(enabled.allocation).toBe(false);
  });

  it('detects exploratory sweep', () => {
    expect(isExploratorySweep({ sweep: { results: [{}] } })).toBe(true);
    expect(isExploratorySweep({ sweep: { results: [{}] }, walk_forward: {} })).toBe(false);
  });
});

describe('optimizationCompare', () => {
  it('diffs best configs and metrics', () => {
    const cmp = compareOptimizationRuns(
      {
        symbol: 'AAPL',
        strategy: 'MACD_RSI',
        objective: 'calmar_ratio',
        best_config: { trailing_stop_percent: 1.5 },
        walk_forward: { aggregate: { stability_score: 0.8, walk_forward_efficiency: 0.6 } },
      },
      {
        symbol: 'AAPL',
        strategy: 'MACD_RSI',
        objective: 'calmar_ratio',
        best_config: { trailing_stop_percent: 2.0 },
        walk_forward: { aggregate: { stability_score: 0.5, walk_forward_efficiency: 0.4 } },
      },
    );
    expect(cmp.comparable).toBe(true);
    expect(cmp.configDiff.some((r) => r.key === 'trailing_stop_percent')).toBe(true);
    expect(cmp.metrics.find((m) => m.id === 'wfe')?.left).toBe(0.6);
  });
});
