import { describe, expect, it } from 'vitest';
import {
  backtestFingerprint,
  backtestStaleReason,
  isBacktestStale,
} from './backtestDisplay';

describe('backtestFingerprint ML identity', () => {
  const base = {
    symbol: 'ETHUSDT',
    strategy: 'LSTM_DIRECTION',
    days: 'holdout',
    timeframe: '1m',
    config: {
      allocation: 1000,
      min_confidence: 0.55,
      model_version: 'v1',
      model_artifact: 'lstm.onnx',
      model_symbol: 'ETHUSDT',
    },
    simMode: 'live_aligned',
  };

  it('changes when model_version changes', () => {
    const a = backtestFingerprint(base);
    const b = backtestFingerprint({
      ...base,
      config: { ...base.config, model_version: 'v2' },
    });
    expect(isBacktestStale(a, b)).toBe(true);
    expect(backtestStaleReason(a, b)).toBe('model');
  });

  it('reports config when non-model fields change', () => {
    const a = backtestFingerprint(base);
    const b = backtestFingerprint({
      ...base,
      config: { ...base.config, allocation: 2000 },
    });
    expect(backtestStaleReason(a, b)).toBe('config');
  });

  it('is stable when only unrelated config keys change', () => {
    const a = backtestFingerprint(base);
    const b = backtestFingerprint({
      ...base,
      config: { ...base.config, learning_rate: 0.01 },
    });
    expect(isBacktestStale(a, b)).toBe(false);
  });

  it('treats holdout sentinel and numeric days as different (callers must stay consistent)', () => {
    const withSentinel = backtestFingerprint(base);
    const withNumeric = backtestFingerprint({ ...base, days: '14' });
    expect(isBacktestStale(withSentinel, withNumeric)).toBe(true);
  });
});
