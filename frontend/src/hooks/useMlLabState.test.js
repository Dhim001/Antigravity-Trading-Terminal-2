import { describe, it, expect } from 'vitest';
import useMlLabState, {
  deriveMlLabJobFlags,
  matchesRetrainTarget,
  normalizeRetrainActions,
  normalizeRetrainPending,
  resolveMlLabSymbolOptions,
  retrainQueueKey,
  sessionMatchesLab,
} from './useMlLabState';
import {
  ML_STRATEGIES,
  readStoredTrainingWindow,
  defaultAdvancedKnobs,
} from '@/components/ml-lab/MlLabConstants';

describe('useMlLabState module', () => {
  it('exports useMlLabState as a function', () => {
    expect(typeof useMlLabState).toBe('function');
  });

  it('relies on ML_STRATEGIES inventory list', () => {
    expect(Array.isArray(ML_STRATEGIES)).toBe(true);
    expect(ML_STRATEGIES.length).toBeGreaterThan(0);
    expect(ML_STRATEGIES).toContain('ML_SIGNAL_BOOST');
  });

  it('training window helper returns a string value', () => {
    const win = readStoredTrainingWindow();
    expect(typeof win).toBe('string');
    expect(win.length).toBeGreaterThan(0);
  });

  it('defaultAdvancedKnobs returns knobs for ML strategies', () => {
    const knobs = defaultAdvancedKnobs('ML_SIGNAL_BOOST', 'train');
    expect(knobs).toMatchObject({
      nFolds: expect.any(Number),
      epochs: expect.any(Number),
      trainInit: expect.stringMatching(/^(warm|scratch)$/),
    });
  });
});

describe('sessionMatchesLab / deriveMlLabJobFlags', () => {
  const session = {
    symbol: 'BTCUSDT',
    strategy: 'ML_SIGNAL_BOOST',
    training: true,
    validating: false,
    tuning: false,
    jobId: 'job-1',
    jobProgress: { pct: 40 },
    serverProgress: { pct: 40, phase: 'train' },
    pollLog: [{ t: 1 }],
    validation: { mean_accuracy: 0.6 },
  };

  it('matches only when symbol and strategy align', () => {
    expect(sessionMatchesLab(session, 'BTCUSDT', 'ML_SIGNAL_BOOST')).toBe(true);
    expect(sessionMatchesLab(session, 'ETHUSDT', 'ML_SIGNAL_BOOST')).toBe(false);
    expect(sessionMatchesLab(session, 'BTCUSDT', 'LSTM_DIRECTION')).toBe(false);
  });

  it('exposes job flags for the matched session', () => {
    const flags = deriveMlLabJobFlags(session, 'BTCUSDT', 'ML_SIGNAL_BOOST');
    expect(flags.jobMatches).toBe(true);
    expect(flags.training).toBe(true);
    expect(flags.validating).toBe(false);
    expect(flags.activeJobId).toBe('job-1');
    expect(flags.jobProgress).toEqual({ pct: 40 });
    expect(flags.pollLog).toHaveLength(1);
    expect(flags.busyElsewhere).toBe(false);
    expect(flags.sessionTuningHint).toBe(false);
  });

  it('clears progress when session belongs elsewhere', () => {
    const flags = deriveMlLabJobFlags(session, 'ETHUSDT', 'ML_SIGNAL_BOOST');
    expect(flags.jobMatches).toBe(false);
    expect(flags.training).toBe(false);
    expect(flags.jobProgress).toBeNull();
    expect(flags.serverProgress).toBeNull();
    expect(flags.pollLog).toEqual([]);
    expect(flags.activeJobId).toBeNull();
    expect(flags.validation).toBeNull();
    expect(flags.busyElsewhere).toBe(true);
  });

  it('marks sessionTuningHint only when matched and tuning', () => {
    const flags = deriveMlLabJobFlags(
      { ...session, training: false, tuning: true },
      'BTCUSDT',
      'ML_SIGNAL_BOOST',
    );
    expect(flags.sessionTuningHint).toBe(true);
    expect(flags.busyElsewhere).toBe(false);
    expect(flags.jobProgress).toBeNull();
    expect(flags.serverProgress).toBeNull();
    expect(flags.pollLog).toEqual([]);
    expect(flags.activeJobId).toBeNull();
  });
});

describe('resolveMlLabSymbolOptions', () => {
  it('uses watchlist order and prepends active when missing', () => {
    expect(resolveMlLabSymbolOptions(['AAPL', 'BTCUSDT'], 'AAPL')).toEqual([
      'AAPL',
      'BTCUSDT',
    ]);
    expect(resolveMlLabSymbolOptions(['AAPL', 'BTCUSDT'], 'ETHUSDT')).toEqual([
      'ETHUSDT',
      'AAPL',
      'BTCUSDT',
    ]);
  });

  it('dedupes blanks and tolerates non-arrays', () => {
    expect(resolveMlLabSymbolOptions(['AAPL', '', 'AAPL', null], 'AAPL')).toEqual(['AAPL']);
    expect(resolveMlLabSymbolOptions(null, 'MSFT')).toEqual(['MSFT']);
    expect(resolveMlLabSymbolOptions(undefined, '')).toEqual([]);
  });
});

describe('normalizeRetrainPending / normalizeRetrainActions', () => {
  it('maps pending entries and filters non-ML strategies', () => {
    const rows = normalizeRetrainPending({
      'BTCUSDT:ML_SIGNAL_BOOST:1m': {
        strategy: 'ML_SIGNAL_BOOST',
        symbol: 'BTCUSDT',
        timeframe: '1m',
        reasons: ['stale'],
        requested_at: '2026-01-01',
      },
      'AAPL:SMA_CROSS:1m': {
        strategy: 'SMA_CROSS',
        symbol: 'AAPL',
        reasons: ['x'],
      },
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      key: 'BTCUSDT:ML_SIGNAL_BOOST:1m',
      strategy: 'ML_SIGNAL_BOOST',
      symbol: 'BTCUSDT',
      timeframe: '1m',
      reasons: ['stale'],
    });
  });

  it('builds backend-compatible retrain queue keys with timeframe', () => {
    expect(retrainQueueKey('btcusdt', 'ml_signal_boost', 'tick')).toBe(
      'BTCUSDT:ML_SIGNAL_BOOST:1m',
    );
    expect(retrainQueueKey('ETHUSDT', 'LSTM_DIRECTION', '15m')).toBe(
      'ETHUSDT:LSTM_DIRECTION:15m',
    );
  });

  it('matches pending rows by SYMBOL:STRATEGY:TF (and legacy keys)', () => {
    expect(matchesRetrainTarget(
      { key: 'BTCUSDT:ML_SIGNAL_BOOST:1m', symbol: 'BTCUSDT', strategy: 'ML_SIGNAL_BOOST' },
      'BTCUSDT',
      'ML_SIGNAL_BOOST',
      '1m',
    )).toBe(true);
    // Legacy optimistic key without TF must still clear the row.
    expect(matchesRetrainTarget(
      { key: 'BTCUSDT:ML_SIGNAL_BOOST:1m', symbol: 'BTCUSDT', strategy: 'ML_SIGNAL_BOOST' },
      'BTCUSDT',
      'ML_SIGNAL_BOOST',
      'tick',
    )).toBe(true);
    expect(matchesRetrainTarget(
      { key: 'BTCUSDT:ML_SIGNAL_BOOST:15m', symbol: 'BTCUSDT', strategy: 'ML_SIGNAL_BOOST', timeframe: '15m' },
      'BTCUSDT',
      'ML_SIGNAL_BOOST',
      '1m',
    )).toBe(false);
  });

  it('returns empty array for invalid pending maps', () => {
    expect(normalizeRetrainPending(null)).toEqual([]);
    expect(normalizeRetrainPending(undefined)).toEqual([]);
    expect(normalizeRetrainPending('x')).toEqual([]);
  });

  it('filters retrain actions to ML strategies', () => {
    expect(normalizeRetrainActions([
      { strategy: 'LSTM_DIRECTION' },
      { strategy: 'SMA_CROSS' },
      null,
    ])).toEqual([{ strategy: 'LSTM_DIRECTION' }]);
    expect(normalizeRetrainActions(null)).toEqual([]);
  });
});
