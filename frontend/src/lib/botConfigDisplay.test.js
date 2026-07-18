/** @vitest-environment node */
import { describe, expect, it } from 'vitest';
import {
  buildAppliedDeployConfig,
  confidenceRangeForStrategy,
  getEditableConfigFields,
  getSweepEligibleFields,
  pickDeployConfig,
} from './botConfigDisplay';

describe('getSweepEligibleFields', () => {
  it('returns TA indicator fields for normal strategies', () => {
    const fields = getSweepEligibleFields('MACD_RSI', {});
    const keys = fields.map((f) => f.key);
    expect(keys).toContain('rsi_length');
    expect(keys).toContain('macd_slow');
    expect(keys).not.toContain('lookback');
  });

  it('hides TA indicators for ML strategies', () => {
    const fields = getSweepEligibleFields('LSTM_DIRECTION', {});
    const keys = fields.map((f) => f.key);
    expect(keys).toContain('lookback');
    expect(keys).toContain('min_confidence');
    expect(keys).not.toContain('rsi_length');
    expect(keys).not.toContain('macd_fast');
  });

  it('includes RL policy fields for PPO', () => {
    const fields = getSweepEligibleFields('RL_PPO_AGENT', {});
    const keys = fields.map((f) => f.key);
    expect(keys).toContain('gamma');
    expect(keys).toContain('clip_epsilon');
    expect(keys).not.toContain('rsi_length');
  });

  it('returns agent gate fields without indicator internals', () => {
    const fields = getSweepEligibleFields('CHART_AGENT', {});
    const keys = fields.map((f) => f.key);
    expect(keys).toContain('min_confidence');
    expect(keys).toContain('calibration_min_wilson');
    expect(keys).toContain('meta_label_model_mode');
    expect(keys).not.toContain('rsi_length');
    expect(keys).not.toContain('macd_fast');
  });

  it('always includes shared risk sweep fields', () => {
    for (const strategy of ['MACD_RSI', 'LSTM_DIRECTION', 'CHART_AGENT']) {
      const keys = getSweepEligibleFields(strategy, {}).map((f) => f.key);
      expect(keys).toContain('trailing_stop_percent');
      expect(keys).toContain('stop_loss_percent');
    }
  });

  it('excludes model pin fields from sweeps', () => {
    const keys = getSweepEligibleFields('RL_PPO_AGENT', {
      model_version: '2026-07-17T00:00:00Z',
      model_symbol: 'BNBUSDT',
    }).map((f) => f.key);
    expect(keys).not.toContain('model_version');
    expect(keys).not.toContain('model_symbol');
  });
});

describe('getEditableConfigFields', () => {
  it('allows editing model_version pin for ML strategies', () => {
    const fields = getEditableConfigFields('ML_SIGNAL_BOOST', {});
    const keys = fields.map((f) => f.key);
    expect(keys).toContain('model_version');
    expect(keys).toContain('model_symbol');
    expect(keys).toContain('min_confidence');
    expect(keys).toContain('direction_mode');
    const ver = fields.find((f) => f.key === 'model_version');
    expect(ver?.input).toBe('model_version');
    const conf = fields.find((f) => f.key === 'min_confidence');
    expect(conf?.group).toBe('signal');
    expect(conf?.input).toBe('range');
  });

  it('maps RL/TCN confidence to range input', () => {
    expect(getEditableConfigFields('RL_PPO_AGENT', {}).find((f) => f.key === 'min_confidence')?.input)
      .toBe('range');
    expect(getEditableConfigFields('TCN_MULTI_HORIZON', {}).find((f) => f.key === 'min_confidence')?.input)
      .toBe('range');
  });

  it('does not surface train hyperparams from polluted ML catalog config', () => {
    const fields = getEditableConfigFields('LSTM_DIRECTION', {
      lookback: 60,
      hidden_dim: 128,
      learning_rate: 0.001,
      batch_size: 64,
      n_estimators: 200,
      min_confidence: 0.55,
    });
    const keys = fields.map((f) => f.key);
    expect(keys).not.toContain('lookback');
    expect(keys).not.toContain('hidden_dim');
    expect(keys).not.toContain('learning_rate');
    expect(keys).not.toContain('batch_size');
    expect(keys).not.toContain('n_estimators');
    expect(keys).toContain('min_confidence');
  });
});

describe('pickDeployConfig', () => {
  it('keeps deploy keys and drops train-only / stale agent keys', () => {
    const out = pickDeployConfig('RL_PPO_AGENT', {
      min_confidence: 0.28,
      lookback: 64,
      total_timesteps: 30000,
      use_llm: true,
      calibration_gate_enabled: true,
      allocation: 2500,
      trailing_stop_percent: 1.5,
    });
    expect(out.min_confidence).toBe(0.28);
    expect(out.allocation).toBe(2500);
    expect(out.trailing_stop_percent).toBe(1.5);
    expect(out.direction_mode).toBe('BOTH');
    expect(out.lookback).toBeUndefined();
    expect(out.total_timesteps).toBeUndefined();
    expect(out.use_llm).toBeUndefined();
    expect(out.calibration_gate_enabled).toBeUndefined();
  });

  it('defaults LONG_ONLY for TA strategies', () => {
    const out = pickDeployConfig('MACD_RSI', { rsi_length: 14 });
    expect(out.direction_mode).toBe('LONG_ONLY');
    expect(out.rsi_length).toBe(14);
  });
});

describe('buildAppliedDeployConfig', () => {
  it('strips stale keys when applying an optimizer winner', () => {
    const out = buildAppliedDeployConfig(
      'MACD_RSI',
      { rsi_length: 10, macd_fast: 8, trailing_stop_percent: 1.5 },
      {
        current: {
          allocation: 5000,
          use_llm: true,
          lookback: 60,
          min_confidence: 0.55,
          direction_mode: 'BOTH',
        },
        extras: null,
      },
    );
    expect(out.rsi_length).toBe(10);
    expect(out.macd_fast).toBe(8);
    expect(out.allocation).toBe(5000);
    expect(out.trailing_stop_percent).toBe(1.5);
    expect(out.use_llm).toBeUndefined();
    expect(out.lookback).toBeUndefined();
    expect(out.min_confidence).toBeUndefined();
  });

  it('keeps ML pin extras from optimizer deploy', () => {
    const out = buildAppliedDeployConfig(
      'LSTM_DIRECTION',
      { min_confidence: 0.6, lookback: 90 },
      {
        current: { allocation: 2000 },
        extras: {
          model_symbol: 'BNBUSDT',
          model_version: '2026-07-18T00:00:00Z',
          model_artifact: 'lstm_direction.onnx',
        },
      },
    );
    expect(out.min_confidence).toBe(0.6);
    expect(out.model_symbol).toBe('BNBUSDT');
    expect(out.model_version).toBe('2026-07-18T00:00:00Z');
    expect(out.model_artifact).toBe('lstm_direction.onnx');
    expect(out.lookback).toBeUndefined();
    expect(out.direction_mode).toBe('BOTH');
  });
});

describe('confidenceRangeForStrategy', () => {
  it('uses wider / finer ranges for RL and TCN', () => {
    expect(confidenceRangeForStrategy('RL_PPO_AGENT').min).toBeLessThan(0.4);
    expect(confidenceRangeForStrategy('TCN_MULTI_HORIZON').max).toBeLessThan(0.1);
    expect(confidenceRangeForStrategy('ML_SIGNAL_BOOST')).toMatchObject({
      min: 0.4,
      max: 1,
    });
  });

  it('drives sweep placeholders for RL/TCN confidence', () => {
    const rl = getSweepEligibleFields('RL_PPO_AGENT', {}).find((f) => f.key === 'min_confidence');
    expect(rl?.placeholder).toMatch(/0\.15/);
    const tcn = getSweepEligibleFields('TCN_MULTI_HORIZON', {}).find((f) => f.key === 'min_confidence');
    expect(tcn?.placeholder).toMatch(/0\.002|0\.0005/);
  });
});
