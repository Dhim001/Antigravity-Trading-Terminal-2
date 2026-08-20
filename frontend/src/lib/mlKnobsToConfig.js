/**
 * Map Lab Advanced knob state (camelCase UI fields) onto the snake_case train
 * / validate config keys the backend trainers read.
 *
 * Single source shared by the Lab Train/Validate buttons and the server-side
 * batch train path — the batch runner used to ship the raw camelCase snapshot,
 * which the backend silently ignored (train fell back to strategy defaults and
 * validate_after ran with default folds / no PBO instead of the authorized
 * walk-forward settings).
 */

import { isDeepMlStrategy } from '@/config/strategies';
import { defaultAdvancedKnobs, parsePositiveInt } from '@/components/ml-lab/MlLabConstants';

function srcOrEmpty(knobs) {
  return knobs && typeof knobs === 'object' ? knobs : {};
}

/** Train-capacity knobs — mirrors the Lab Train payload construction. */
export function knobsToTrainConfig(strategy, knobs) {
  const strat = String(strategy || '').toUpperCase();
  const defaults = defaultAdvancedKnobs(strat, 'train');
  const src = srcOrEmpty(knobs);
  const out = {};
  if (strat === 'RL_PPO_AGENT') {
    out.total_timesteps = parsePositiveInt(
      src.totalTimesteps, defaults.totalTimesteps, { min: 256, max: 500_000 },
    );
    out.hidden_dim = parsePositiveInt(src.hiddenDim, defaults.hiddenDim, { min: 32, max: 1024 });
  }
  if (isDeepMlStrategy(strat)) {
    out.epochs = parsePositiveInt(src.epochs, defaults.epochs, { min: 1, max: 500 });
    out.early_stop_patience = parsePositiveInt(
      src.earlyStopPatience, defaults.earlyStopPatience, { min: 1, max: 100 },
    );
    out.hidden_dim = parsePositiveInt(src.hiddenDim, defaults.hiddenDim, { min: 32, max: 1024 });
    if (strat === 'TRANSFORMER_SIGNAL') {
      out.d_model = parsePositiveInt(src.hiddenDim, 128, { min: 32, max: 512 });
    }
    if (strat === 'TCN_MULTI_HORIZON') out.num_blocks = 6;
  }
  if (strat === 'ML_SIGNAL_BOOST') {
    out.gbm_max_iter = parsePositiveInt(src.gbmMaxIter, 300, { min: 40, max: 1000 });
    out.gbm_max_depth = parsePositiveInt(src.gbmMaxDepth, 6, { min: 3, max: 12 });
  }
  return out;
}

/**
 * Walk-forward knobs for a batch item's validate_after step — mirrors the Lab
 * Validate payload (folds, bar cap, PBO). PBO runs only for GBM, matching the
 * Lab ("deep/RL fold PBO re-trains every combo — too heavy").
 */
export function knobsToValidateConfig(strategy, knobs) {
  const strat = String(strategy || '').toUpperCase();
  const defaults = defaultAdvancedKnobs(strat, 'train');
  const src = srcOrEmpty(knobs);
  return {
    validate_folds: parsePositiveInt(src.nFolds, defaults.nFolds, { min: 2, max: 8 }),
    validate_mode: 'rolling',
    validate_max_bars: parsePositiveInt(
      src.validateMaxBars, defaults.validateMaxBars, { min: 200, max: 100_000 },
    ),
    validate_pbo: strat === 'ML_SIGNAL_BOOST',
    pbo_segments: parsePositiveInt(src.pboSegments, defaults.pboSegments, { min: 2, max: 8 }),
    pbo_max_combos: parsePositiveInt(src.pboMaxCombos, defaults.pboMaxCombos, { min: 1, max: 16 }),
    wf_capacity_parity: true,
  };
}
