import { describe, it, expect } from 'vitest';
import {
  getBacktestClientTimeoutMs,
  getMlBacktestTimeoutFloorMs,
  PORTFOLIO_TIMEOUT_MIN_MS,
  PORTFOLIO_TIMEOUT_PER_SYMBOL_MS,
} from './backtestTimeouts';

describe('getBacktestClientTimeoutMs — portfolio scaling', () => {
  it('uses default single-symbol timeout when count < 2', () => {
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 0 })).toBe(120_000);
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 1 })).toBe(120_000);
  });

  it('enforces 10 min minimum for 2–5 symbols', () => {
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 2 })).toBe(PORTFOLIO_TIMEOUT_MIN_MS);
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 5 })).toBe(PORTFOLIO_TIMEOUT_MIN_MS);
  });

  it('scales 120s per symbol above the minimum', () => {
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 6 }))
      .toBe(PORTFOLIO_TIMEOUT_PER_SYMBOL_MS * 6);
    expect(getBacktestClientTimeoutMs({ portfolioSymbolCount: 8 }))
      .toBe(PORTFOLIO_TIMEOUT_PER_SYMBOL_MS * 8);
  });
});

describe('getBacktestClientTimeoutMs — walk-forward', () => {
  it('uses 15 min+ base for WF rigorous (30d, 3 folds, 1 combo)', () => {
    const ms = getBacktestClientTimeoutMs({
      walkForward: true,
      days: 30,
      rollingFolds: 3,
      comboCount: 1,
    });
    expect(ms).toBeGreaterThanOrEqual(900_000);
    expect(ms).toBe(900_000 + 23 * 45_000 + 3 * 1 * 120_000);
  });

  it('scales with fold and combo count', () => {
    const ms = getBacktestClientTimeoutMs({
      walkForward: true,
      days: 7,
      rollingFolds: 3,
      comboCount: 6,
    });
    expect(ms).toBe(900_000 + 3 * 6 * 120_000);
  });
});

describe('getBacktestClientTimeoutMs — ML strategies', () => {
  it('gives ML_SIGNAL_BOOST 10 minutes (not the 2 min default)', () => {
    expect(getBacktestClientTimeoutMs({ strategy: 'ML_SIGNAL_BOOST', days: 7 }))
      .toBe(600_000);
  });

  it('gives deep ML strategies 15 minutes', () => {
    expect(getBacktestClientTimeoutMs({ strategy: 'LSTM_DIRECTION', days: 7 }))
      .toBe(900_000);
    expect(getBacktestClientTimeoutMs({ strategy: 'TRANSFORMER_SIGNAL', days: 7 }))
      .toBe(900_000);
    expect(getBacktestClientTimeoutMs({ strategy: 'TCN_MULTI_HORIZON', days: 7 }))
      .toBe(900_000);
  });

  it('gives RL_PPO_AGENT 20 minutes', () => {
    expect(getBacktestClientTimeoutMs({ strategy: 'RL_PPO_AGENT', days: 7 }))
      .toBe(1_200_000);
  });

  it('scales ML floors with history length', () => {
    expect(getBacktestClientTimeoutMs({ strategy: 'LSTM_DIRECTION', days: 30 }))
      .toBe(900_000 + 23 * 45_000);
  });

  it('raises walk-forward floor when strategy is ML', () => {
    const plainWf = getBacktestClientTimeoutMs({
      walkForward: true,
      days: 7,
      rollingFolds: 3,
      comboCount: 1,
    });
    const mlWf = getBacktestClientTimeoutMs({
      walkForward: true,
      days: 7,
      rollingFolds: 3,
      comboCount: 1,
      strategy: 'RL_PPO_AGENT',
    });
    expect(mlWf).toBeGreaterThan(plainWf);
    expect(mlWf).toBe(
      Math.max(
        plainWf,
        getMlBacktestTimeoutFloorMs('RL_PPO_AGENT', 7, {
          walkForward: true,
          folds: 3,
          combos: 1,
        }),
      ),
    );
  });

  it('uses CHART_AGENT timeout when strategy is passed through', () => {
    expect(getBacktestClientTimeoutMs({ strategy: 'CHART_AGENT', days: 7 }))
      .toBe(300_000);
  });
});
