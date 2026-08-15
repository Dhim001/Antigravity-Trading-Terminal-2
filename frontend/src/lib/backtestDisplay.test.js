import { describe, expect, it } from 'vitest';
import {
  backtestContextMismatch,
  backtestFingerprint,
  backtestStaleReason,
  isBacktestStale,
  resolveBacktestResultIdentity,
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

  it('reports symbol when only ticker changes', () => {
    const a = backtestFingerprint(base);
    const b = backtestFingerprint({ ...base, symbol: 'BTCUSDT' });
    expect(backtestStaleReason(a, b)).toBe('symbol');
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

describe('resolveBacktestResultIdentity / context mismatch', () => {
  it('prefers results.meta over live UI fallbacks', () => {
    const id = resolveBacktestResultIdentity(
      { meta: { symbol: 'ADAUSDT', strategy: 'RL_PPO_AGENT', timeframe: '5m', days: 14 } },
      { symbol: 'ETHUSDT', strategy: 'SUPERTREND_ADX', timeframe: '1m', days: 7 },
    );
    expect(id.symbol).toBe('ADAUSDT');
    expect(id.strategy).toBe('RL_PPO_AGENT');
    expect(id.timeframe).toBe('5m');
    expect(id.days).toBe(14);
  });

  it('flags symbol mismatch when selection diverges from the run', () => {
    const mismatch = backtestContextMismatch(
      { meta: { symbol: 'BTCUSDT', strategy: 'ML_SIGNAL_BOOST' } },
      { symbol: 'ETHUSDT', strategy: 'ML_SIGNAL_BOOST' },
    );
    expect(mismatch).not.toBeNull();
    expect(mismatch.issues.some((i) => i.field === 'symbol')).toBe(true);
    expect(mismatch.identity.symbol).toBe('BTCUSDT');
  });

  it('does not flag portfolio runs as single-symbol mismatch', () => {
    expect(backtestContextMismatch(
      { meta: { portfolio: true, symbol: 'BTCUSDT' }, portfolio: true },
      { symbol: 'ETHUSDT', strategy: 'X' },
    )).toBeNull();
  });
});
