/** @vitest-environment node */
import { describe, expect, it } from 'vitest';
import {
  actionLabel,
  actionTone,
  findNextTradeAction,
  normalizeAction,
  sparkIndexForStep,
} from './rlEpisodeReplay';
import { getStrategyCategory } from '@/config/strategies';
import { getSweepEligibleFields } from './botConfigDisplay';
import { getDefaultObjective, getDefaultMinTrades } from './optimizerDefaults';

describe('rlEpisodeReplay helpers', () => {
  it('normalizes discrete actions', () => {
    expect(normalizeAction(1)).toBe(1);
    expect(normalizeAction([2])).toBe(2);
    expect(actionLabel(1)).toBe('BUY');
    expect(actionLabel([0])).toBe('HOLD');
    expect(actionTone(1)).toBe('up');
    expect(actionTone(2)).toBe('down');
  });

  it('finds next trade action', () => {
    const steps = [
      { action: 0 },
      { action: 0 },
      { action: 1 },
      { action: 2 },
    ];
    expect(findNextTradeAction(steps, 0)).toBe(2);
    expect(findNextTradeAction(steps, 2)).toBe(3);
    expect(findNextTradeAction(steps, 3)).toBe(-1);
  });

  it('maps step index onto sparkline', () => {
    expect(sparkIndexForStep(0, 11, 5)).toBe(0);
    expect(sparkIndexForStep(10, 11, 5)).toBe(4);
    expect(sparkIndexForStep(5, 11, 5)).toBe(2);
  });
});

describe('cross-category lab switching', () => {
  const cases = [
    {
      strategy: 'MACD_RSI',
      category: 'normal',
      expectField: 'rsi_length',
      hideField: 'lookback',
      objective: 'calmar_ratio',
      minTrades: 1,
    },
    {
      strategy: 'LSTM_DIRECTION',
      category: 'ml',
      expectField: 'lookback',
      hideField: 'rsi_length',
      objective: 'robust_score',
      minTrades: 5,
    },
    {
      strategy: 'RL_PPO_AGENT',
      category: 'ml',
      expectField: 'gamma',
      hideField: 'macd_fast',
      objective: 'robust_score',
      minTrades: 5,
    },
    {
      strategy: 'CHART_AGENT',
      category: 'agent',
      expectField: 'calibration_min_wilson',
      hideField: 'rsi_length',
      objective: 'calmar_ratio',
      minTrades: 3,
    },
  ];

  it('keeps category, sweep fields, and defaults consistent when switching', () => {
    // Simulate mid-session switches: each strategy resolves independently (no stale bleed).
    for (const c of cases) {
      expect(getStrategyCategory(c.strategy)).toBe(c.category);
      const keys = getSweepEligibleFields(c.strategy, {}).map((f) => f.key);
      expect(keys).toContain(c.expectField);
      expect(keys).not.toContain(c.hideField);
      expect(getDefaultObjective(c.strategy)).toBe(c.objective);
      expect(getDefaultMinTrades(c.strategy)).toBe(c.minTrades);
    }
  });

  it('ML and agent never share TA indicator internals across consecutive switches', () => {
    const sequence = ['MACD_RSI', 'ML_SIGNAL_BOOST', 'CHART_AGENT', 'MACD_RSI'];
    for (const strategy of sequence) {
      const keys = getSweepEligibleFields(strategy, {}).map((f) => f.key);
      const cat = getStrategyCategory(strategy);
      if (cat === 'normal') {
        expect(keys).toContain('rsi_length');
      } else {
        expect(keys).not.toContain('rsi_length');
        expect(keys).not.toContain('macd_fast');
      }
    }
  });
});
