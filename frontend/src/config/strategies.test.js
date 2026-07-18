/** @vitest-environment node */
import { describe, expect, it } from 'vitest';
import {
  getStrategyCategory,
  getMLSubtype,
  getStrategyMeta,
  isMlStrategy,
  isDeepMlStrategy,
  ML_STRATEGY_IDS,
  defaultAllocationFor,
} from '@/config/strategies';

describe('getStrategyCategory', () => {
  it('classifies TA strategies as normal', () => {
    expect(getStrategyCategory('MACD_RSI')).toBe('normal');
    expect(getStrategyCategory('SUPERTREND_ADX')).toBe('normal');
  });

  it('classifies ML strategies as ml', () => {
    expect(getStrategyCategory('ML_SIGNAL_BOOST')).toBe('ml');
    expect(getStrategyCategory('LSTM_DIRECTION')).toBe('ml');
    expect(getStrategyCategory('RL_PPO_AGENT')).toBe('ml');
  });

  it('classifies agent strategies as agent', () => {
    expect(getStrategyCategory('CHART_AGENT')).toBe('agent');
    expect(getStrategyCategory('ABSORPTION_AGENT')).toBe('agent');
    expect(getStrategyMeta('CHART_AGENT').style).toBe('agent');
  });

  it('keeps microstructure / SMC templates out of the agentic tab', () => {
    expect(getStrategyCategory('CVD_DIVERGENCE')).toBe('normal');
    expect(getStrategyCategory('ORDERFLOW_IMBALANCE')).toBe('normal');
    expect(getStrategyCategory('WYCKOFF_SPRING')).toBe('normal');
    expect(getStrategyCategory('VPOC_REVERSION')).toBe('normal');
    expect(getStrategyMeta('CVD_DIVERGENCE').style).toBe('microstructure');
  });

  it('falls back to normal for unknown strategies', () => {
    expect(getStrategyCategory('CUSTOM_XYZ')).toBe('normal');
  });
});

describe('isMlStrategy / allocations', () => {
  it('shares one ML id list', () => {
    expect(ML_STRATEGY_IDS).toHaveLength(7);
    expect(isMlStrategy('LSTM_DIRECTION')).toBe(true);
    expect(isMlStrategy('MACD_RSI')).toBe(false);
    expect(isDeepMlStrategy('ML_SIGNAL_BOOST')).toBe(false);
    expect(isDeepMlStrategy('TRANSFORMER_SIGNAL')).toBe(true);
  });

  it('uses per-strategy allocation presets', () => {
    expect(defaultAllocationFor('RL_PPO_AGENT')).toBe(3000);
    expect(defaultAllocationFor('SUPERTREND_ADX')).toBe(5000);
    expect(defaultAllocationFor('UNKNOWN')).toBe(1000);
  });
});

describe('getMLSubtype', () => {
  it('detects RL subtype', () => {
    expect(getMLSubtype('RL_PPO_AGENT')).toBe('rl');
  });

  it('detects unsupervised subtype', () => {
    expect(getMLSubtype('VAE_REGIME_DETECTOR')).toBe('unsupervised');
  });

  it('defaults supervised for other ML strategies', () => {
    expect(getMLSubtype('LSTM_DIRECTION')).toBe('supervised');
    expect(getMLSubtype('ML_SIGNAL_BOOST')).toBe('supervised');
  });
});
