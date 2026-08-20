import { describe, expect, it } from 'vitest';
import { knobsToTrainConfig, knobsToValidateConfig } from './mlKnobsToConfig';

describe('knobsToTrainConfig', () => {
  it('maps RL knobs to snake_case with clamps', () => {
    const out = knobsToTrainConfig('RL_PPO_AGENT', {
      totalTimesteps: '200000',
      hiddenDim: '256',
      epochs: '100', // ignored for RL train
    });
    expect(out.total_timesteps).toBe(200_000);
    expect(out.hidden_dim).toBe(256);
    expect(out.epochs).toBe(100); // deep branch includes RL — trainer ignores it
  });

  it('falls back to Lab defaults when knobs are missing', () => {
    const out = knobsToTrainConfig('RL_PPO_AGENT', null);
    expect(out.total_timesteps).toBe(200_000);
    expect(out.hidden_dim).toBe(256);
  });

  it('maps deep-net knobs including transformer d_model and TCN blocks', () => {
    const t = knobsToTrainConfig('TRANSFORMER_SIGNAL', { hiddenDim: '96', epochs: '90' });
    expect(t.epochs).toBe(90);
    expect(t.hidden_dim).toBe(96);
    expect(t.d_model).toBe(96);
    const tcn = knobsToTrainConfig('TCN_MULTI_HORIZON', {});
    expect(tcn.num_blocks).toBe(6);
    expect(tcn.epochs).toBe(100);
  });

  it('maps GBM knobs', () => {
    const out = knobsToTrainConfig('ML_SIGNAL_BOOST', {
      gbmMaxIter: '450',
      gbmMaxDepth: '8',
    });
    expect(out.gbm_max_iter).toBe(450);
    expect(out.gbm_max_depth).toBe(8);
    expect(out.epochs).toBeUndefined();
    expect(out.event_filter).toBe('cusum');
    expect(out.cusum_threshold).toBe(1);
  });

  it('maps event_filter and cusum_threshold', () => {
    const out = knobsToTrainConfig('LSTM_DIRECTION', {
      eventFilter: 'all',
      cusumThreshold: '1.5',
    });
    expect(out.event_filter).toBe('all');
    expect(out.cusum_threshold).toBe(1.5);
  });

  it('maps feature_scheme for train and validate', () => {
    const train = knobsToTrainConfig('LSTM_DIRECTION', { featureScheme: 'v7' });
    expect(train.feature_scheme).toBe('v7');
    const val = knobsToValidateConfig('ML_SIGNAL_BOOST', { featureScheme: 'v8_no_ict' });
    expect(val.feature_scheme).toBe('v8_no_ict');
    expect(knobsToTrainConfig('RL_PPO_AGENT', {}).feature_scheme).toBe('v8');
  });

  it('clamps out-of-range values', () => {
    const out = knobsToTrainConfig('ML_SIGNAL_BOOST', { gbmMaxIter: '5000' });
    expect(out.gbm_max_iter).toBe(1000);
  });
});

describe('knobsToValidateConfig', () => {
  it('maps fold / bar-cap / PBO knobs the batch validator reads', () => {
    const out = knobsToValidateConfig('ML_SIGNAL_BOOST', {
      nFolds: '4',
      validateMaxBars: '50000',
      pboSegments: '6',
      pboMaxCombos: 4,
    });
    expect(out.validate_folds).toBe(4);
    expect(out.validate_max_bars).toBe(50_000);
    expect(out.pbo_segments).toBe(6);
    expect(out.pbo_max_combos).toBe(4);
    expect(out.validate_pbo).toBe(true); // GBM gets PBO like Lab Validate
    expect(out.wf_capacity_parity).toBe(true);
    expect(out.event_filter).toBe('cusum');
    expect(out.cusum_threshold).toBe(1);
  });

  it('keeps PBO off for deep / RL strategies', () => {
    expect(knobsToValidateConfig('LSTM_DIRECTION', {}).validate_pbo).toBe(false);
    expect(knobsToValidateConfig('RL_PPO_AGENT', {}).validate_pbo).toBe(false);
    expect(knobsToValidateConfig('VAE_REGIME_DETECTOR', {}).validate_pbo).toBe(false);
  });

  it('uses Lab defaults when knobs are absent', () => {
    const out = knobsToValidateConfig('LSTM_DIRECTION', null);
    expect(out.validate_folds).toBe(3);
    expect(out.validate_max_bars).toBe(12_000);
    expect(out.validate_mode).toBe('rolling');
  });
});
