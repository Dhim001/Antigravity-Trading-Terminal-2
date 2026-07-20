/** Labels, grouping, and editable field schema for bot strategy config. */

import { normalizeConfirmTimeframe } from '@/lib/barTimeframes';
import { getStrategyCategory, getMLSubtype } from '@/config/strategies';

export const DIRECTION_MODE_OPTIONS = [
  { value: 'LONG_ONLY', label: 'Long only' },
  { value: 'SHORT_ONLY', label: 'Short only' },
  { value: 'BOTH', label: 'Both (long & short)' },
];

const DIRECTION_MODE_LABELS = Object.fromEntries(
  DIRECTION_MODE_OPTIONS.map((o) => [o.value, o.label]),
);

/** Normalize deploy/backtest direction_mode to LONG_ONLY | SHORT_ONLY | BOTH. */
export function normalizeDirectionMode(value) {
  const mode = String(value || 'LONG_ONLY').trim().toUpperCase();
  if (mode === 'SHORT_ONLY' || mode === 'BOTH') return mode;
  return 'LONG_ONLY';
}

export function formatDirectionModeLabel(value) {
  return DIRECTION_MODE_LABELS[normalizeDirectionMode(value)] ?? normalizeDirectionMode(value);
}

export const GROUP_ORDER = ['risk', 'signal', 'agent', 'agent_gate', 'agent_llm', 'indicators', 'tick', 'ml_model', 'rl_policy', 'other'];

export const GROUP_LABELS = {
  risk: 'Risk & exits',
  signal: 'Signal gate',
  agent: 'Chart agent',
  agent_gate: 'Agent gates',
  agent_llm: 'LLM settings',
  indicators: 'Indicators',
  tick: 'Tick execution',
  ml_model: 'ML model',
  rl_policy: 'RL policy',
  other: 'Other',
};

export const FIELD_META = {
  trailing_stop_percent: { label: 'Trailing stop', group: 'risk', kind: 'percent', hint: 'Exits when price retraces this % from the best price since entry.' },
  stop_loss_percent: { label: 'Stop loss', group: 'risk', kind: 'percent' },
  take_profit_percent: { label: 'Take profit', group: 'risk', kind: 'percent', hint: 'Closes the position when price reaches this % target.' },
  take_profit_price: { label: 'Take profit price', group: 'risk', kind: 'price', readOnly: true },
  tp_mode: { label: 'Take profit mode', group: 'risk', kind: 'tp_mode' },
  min_confidence: {
    label: 'Min confidence',
    group: 'signal',
    kind: 'range',
    hint: 'Only enter when the strategy confidence / score meets this threshold (scale depends on strategy).',
  },
  use_vol_sizing: { label: 'Vol sizing', group: 'agent', kind: 'boolean', hint: 'Scale entry size by risk sub-report suggested_size_factor.' },
  require_trend_alignment: { label: 'Trend alignment', group: 'agent', kind: 'boolean', hint: 'BUY only when trend score ≥ +1; SELL when ≤ −1.' },
  use_rsi_confirmation: { label: 'RSI confirmation', group: 'indicators', kind: 'boolean', hint: 'Require RSI not overbought/oversold on VWAP cross entries.' },
  rsi_overbought_gate: { label: 'RSI overbought gate', group: 'indicators', kind: 'integer', hint: 'Block VWAP buy when RSI above this (default 60).' },
  rsi_oversold_gate: { label: 'RSI oversold gate', group: 'indicators', kind: 'integer', hint: 'Block VWAP sell when RSI below this (default 40).' },
  block_elevated_vol: { label: 'Block elevated vol', group: 'indicators', kind: 'boolean', hint: 'Skip entries when ATR is ≥1.5× its 20-bar median.' },
  min_score: { label: 'Min score', group: 'agent', kind: 'integer', hint: 'Require |composite score| ≥ this value.' },
  confirm_timeframe: { label: 'Confirm TF', group: 'agent', kind: 'confirm_timeframe', hint: 'Higher timeframe trend must confirm entry (e.g. 15m, 1h). Leave empty to disable.' },
  calibration_gate_enabled: { label: 'Calibration gate', group: 'agent', kind: 'boolean', hint: 'Block entries when the setup bucket underperforms in closed-trade history.' },
  calibration_min_samples: { label: 'Gate min samples', group: 'agent', kind: 'integer', hint: 'Minimum closed trades in a bucket before the gate can block.' },
  calibration_min_wilson: { label: 'Gate min Wilson', group: 'agent', kind: 'confidence', hint: 'Wilson lower-bound win rate required to allow entry (0–1).' },
  meta_label_model_mode: { label: 'Meta-label mode', group: 'agent', kind: 'meta_label_mode', hint: 'Requires Calibration gate ON. wilson = bucket stats; gbm = P(win) model; hybrid = GBM when trained else Wilson. Legacy meta_label_model_enabled upgrades wilson→hybrid.' },
  meta_label_min_prob: { label: 'Meta-label min P(win)', group: 'agent', kind: 'confidence', hint: 'Block entries when the GBM win probability is below this (0–1).' },
  meta_label_min_train_samples: { label: 'Meta-label min trades', group: 'agent', kind: 'integer', hint: 'Minimum closed trades before training the GBM classifier.' },
  meta_label_shadow_mode: { label: 'Meta-label shadow', group: 'agent', kind: 'boolean', hint: 'Log GBM blocks without rejecting entries (evaluate before going live).' },
  use_meta_label_sizing: { label: 'Meta-label sizing', group: 'agent', kind: 'boolean', hint: 'Scale entry size by GBM P(win) when a model is loaded.' },
  use_confidence_sizing: { label: 'Confidence sizing', group: 'agent', kind: 'boolean', hint: 'Scale entry size by signal confidence.' },
  regime_routing_enabled: { label: 'Regime routing', group: 'agent', kind: 'boolean', hint: 'Apply stricter thresholds in elevated/compressed ATR regimes.' },
  elevated_min_confidence: { label: 'Elevated min conf', group: 'agent', kind: 'confidence', hint: 'Minimum confidence when ATR regime is elevated.' },
  elevated_min_score: { label: 'Elevated min score', group: 'agent', kind: 'integer', hint: 'Minimum |score| when ATR regime is elevated.' },
  elevated_block_entries: { label: 'Block elevated entries', group: 'agent', kind: 'boolean', hint: 'Hard-block all entries in elevated vol (overrides routing thresholds).' },
  compressed_min_confidence: { label: 'Compressed min conf', group: 'agent', kind: 'confidence', hint: 'Minimum confidence when ATR regime is compressed.' },
  use_llm: { label: 'LLM analysis', group: 'agent', kind: 'boolean', hint: 'Use the LLM narrator for chart explanations (Ollama local or OpenRouter).' },
  rsi_length: { label: 'RSI period', group: 'indicators', kind: 'integer' },
  macd_fast: { label: 'MACD fast', group: 'indicators', kind: 'integer' },
  macd_slow: { label: 'MACD slow', group: 'indicators', kind: 'integer' },
  macd_signal: { label: 'MACD signal', group: 'indicators', kind: 'integer' },
  atr_length: { label: 'ATR period', group: 'indicators', kind: 'integer' },
  bb_length: { label: 'Bollinger period', group: 'indicators', kind: 'integer' },
  bb_std: { label: 'Bollinger σ', group: 'indicators', kind: 'decimal' },
  stoch_k: { label: 'Stoch %K', group: 'indicators', kind: 'integer' },
  stoch_d: { label: 'Stoch %D', group: 'indicators', kind: 'integer' },
  stoch_smooth: { label: 'Stoch smooth', group: 'indicators', kind: 'integer' },
  rsi_oversold: { label: 'RSI oversold', group: 'indicators', kind: 'integer' },
  rsi_overbought: { label: 'RSI overbought', group: 'indicators', kind: 'integer' },
  stoch_oversold: { label: 'Stoch oversold', group: 'indicators', kind: 'integer' },
  stoch_overbought: { label: 'Stoch overbought', group: 'indicators', kind: 'integer' },
  st_length: { label: 'SuperTrend period', group: 'indicators', kind: 'integer' },
  st_multiplier: { label: 'SuperTrend mult', group: 'indicators', kind: 'decimal' },
  adx_length: { label: 'ADX period', group: 'indicators', kind: 'integer' },
  adx_threshold: { label: 'ADX threshold', group: 'indicators', kind: 'integer' },
  lookback_ticks: { label: 'Lookback ticks', group: 'tick', kind: 'integer' },
  tick_cooldown_sec: { label: 'Cooldown', group: 'tick', kind: 'seconds' },
  module: { label: 'Custom module', group: 'other', kind: 'text' },
  direction_mode: { label: 'Trade direction', group: 'risk', kind: 'direction_mode', hint: 'LONG_ONLY, SHORT_ONLY, or BOTH. Controls which trade directions are allowed.' },
  filter_strategy: { label: 'Filter strategy', group: 'other', kind: 'text', hint: 'Gate signals through a secondary strategy (e.g. SUPERTREND_ADX or VAE_REGIME_DETECTOR).' },
  filter_mode: { label: 'Filter mode', group: 'other', kind: 'text', hint: 'TREND_GATE (bias) or REGIME_GATE (VAE suppress). Auto REGIME_GATE when filter is VAE.' },
  vae_regime_gate_enabled: { label: 'VAE regime gate', group: 'ml_model', kind: 'boolean', hint: 'Meta-layer: suppress entries when VAE anomaly score is unstable. Also auto-on if filter_strategy is VAE_REGIME_DETECTOR.' },
  vae_regime_rotation_hint: { label: 'VAE rotation hint', group: 'ml_model', kind: 'boolean', hint: 'Let RegimeRotation skip swaps in unstable VAE regimes and confirm faster when anomalous.' },
  ob_lookback: { label: 'OB lookback', group: 'indicators', kind: 'integer', hint: 'Bars to scan for order blocks.' },
  fvg_min_gap_pct: { label: 'FVG min gap %', group: 'indicators', kind: 'decimal', hint: 'Minimum gap as % of price for a fair value gap.' },
  sweep_lookback: { label: 'Sweep lookback', group: 'indicators', kind: 'integer', hint: 'Rolling window for liquidity sweep detection.' },
  breakout_length: { label: 'Entry channel', group: 'indicators', kind: 'integer', hint: 'Donchian entry channel lookback.' },
  exit_length: { label: 'Exit channel', group: 'indicators', kind: 'integer', hint: 'Donchian exit channel lookback (shorter = faster exit).' },
  atr_confirm_mult: { label: 'ATR confirm mult', group: 'indicators', kind: 'decimal', hint: 'ATR must be ≥ this × median ATR to confirm breakout.' },
  spread_pct: { label: 'Spread %', group: 'indicators', kind: 'decimal', hint: 'Minimum bid-ask spread to capture (0.002 = 0.2%).' },
  max_skew: { label: 'Max inventory skew', group: 'risk', kind: 'decimal', hint: 'Maximum inventory imbalance before one-sided quoting.' },
  vol_shutdown_mult: { label: 'Vol shutdown mult', group: 'risk', kind: 'decimal', hint: 'Shut down MM when ATR > this × median (too volatile).' },
  inventory_target: { label: 'Inventory target', group: 'risk', kind: 'decimal', hint: 'Target inventory level (0 = neutral).' },
  pivot_lookback: { label: 'Pivot lookback', group: 'indicators', kind: 'integer', hint: 'Bars for CVD pivot detection.' },
  range_lookback: { label: 'Range lookback', group: 'indicators', kind: 'integer', hint: 'Wyckoff range window.' },
  volume_surge_mult: { label: 'Volume surge mult', group: 'indicators', kind: 'decimal', hint: 'Volume vs MA multiplier for surge.' },
  profile_lookback: { label: 'Profile lookback', group: 'indicators', kind: 'integer', hint: 'Volume profile window.' },
  value_area_pct: { label: 'Value area %', group: 'indicators', kind: 'decimal', hint: 'Value-area fraction of volume profile.' },
  adx_trend_filter: { label: 'ADX trend filter', group: 'indicators', kind: 'integer', hint: 'Skip when ADX above this (ranging filter).' },
  bair_threshold: { label: 'BAIR threshold', group: 'indicators', kind: 'decimal', hint: 'Bid/ask imbalance ratio threshold.' },
  mlofi_threshold: { label: 'MLOFI threshold', group: 'indicators', kind: 'decimal', hint: 'Multi-level OFI threshold.' },
  allow_candle_proxy: { label: 'Candle proxy', group: 'indicators', kind: 'boolean', hint: 'Allow OHLCV proxy when L2 book missing.' },
  book_levels: { label: 'Book levels', group: 'indicators', kind: 'integer', hint: 'Orderbook depth levels for imbalance.' },
  lookback: { label: 'Lookback window', group: 'ml_model', kind: 'integer', hint: 'Sliding input window size in bars (e.g. 60).' },
  min_return: { label: 'Min return (decimal)', group: 'ml_model', kind: 'decimal', hint: 'TCN: minimum forecast magnitude to fire (0.002 = 0.2%, not percent units).' },
  hidden_dim: { label: 'Hidden dim', group: 'ml_model', kind: 'integer', hint: 'Neural network hidden layer size.' },
  num_layers: { label: 'Num layers', group: 'ml_model', kind: 'integer', hint: 'Stacked layer count for sequence models.' },
  learning_rate: { label: 'Learning rate', group: 'ml_model', kind: 'decimal', hint: 'Optimizer step size.' },
  batch_size: { label: 'Batch size', group: 'ml_model', kind: 'integer', hint: 'Training mini-batch size.' },
  d_model: { label: 'Model dim', group: 'ml_model', kind: 'integer', hint: 'Transformer embedding dimension.' },
  nhead: { label: 'Attention heads', group: 'ml_model', kind: 'integer', hint: 'Multi-head attention count.' },
  latent_dim: { label: 'Latent dim', group: 'ml_model', kind: 'integer', hint: 'VAE bottleneck dimension.' },
  anomaly_threshold: { label: 'Anomaly threshold', group: 'ml_model', kind: 'decimal', hint: 'VAE reconstruction error to flag regime shift.' },
  suppress_threshold: { label: 'Suppress threshold', group: 'ml_model', kind: 'decimal', hint: 'VAE error level to suppress entries.' },
  n_heads: { label: 'GNN heads', group: 'ml_model', kind: 'integer', hint: 'Graph attention head count.' },
  min_corr: { label: 'Min correlation', group: 'ml_model', kind: 'decimal', hint: 'Minimum cross-asset correlation for graph edges.' },
  basket_id: { label: 'Basket ID', group: 'ml_model', kind: 'text', hint: 'Correlated asset basket identifier.' },
  triple_barrier_atr_mult: { label: 'Barrier ATR mult', group: 'ml_model', kind: 'decimal', hint: 'Triple-barrier label width in ATR multiples.' },
  triple_barrier_max_bars: { label: 'Barrier max bars', group: 'ml_model', kind: 'integer', hint: 'Max bars before triple-barrier timeout.' },
  min_train_samples: { label: 'Min train samples', group: 'ml_model', kind: 'integer', hint: 'Minimum labeled samples before training.' },
  val_fraction: { label: 'Validation fraction', group: 'ml_model', kind: 'decimal', hint: 'Holdout fraction for validation metrics.' },
  retrain_interval_hours: { label: 'Retrain interval (h)', group: 'ml_model', kind: 'integer', hint: 'Hours between scheduled retrains.' },
  model_symbol: { label: 'Model symbol', group: 'ml_model', kind: 'text', hint: 'Symbol key for persisted model artifacts (defaults to bot symbol).' },
  model_version: {
    label: 'Model version',
    group: 'ml_model',
    kind: 'model_version',
    hint: 'Pin a trained snapshot, or leave Latest to always use the activated model.',
  },
  model_artifact: { label: 'Model artifact', group: 'ml_model', kind: 'text', readOnly: true, hint: 'Pinned filename (.onnx / .joblib) for this deploy.' },
  ta_strategy: { label: 'TA leg', group: 'ml_model', kind: 'text', hint: 'Technical strategy id for the ensemble TA vote (e.g. MACD_RSI).' },
  ml_strategy: { label: 'ML leg', group: 'ml_model', kind: 'text', hint: 'ML strategy id for the ensemble ML vote (e.g. ML_SIGNAL_BOOST). Train this in Model Training.' },
  rl_strategy: { label: 'RL leg', group: 'ml_model', kind: 'text', hint: 'RL strategy id for the ensemble RL vote (default RL_PPO_AGENT).' },
  ensemble_weight_ta: { label: 'TA weight', group: 'ml_model', kind: 'decimal', hint: 'Relative weight of the TA vote (normalized with ML + RL).' },
  ensemble_weight_ml: { label: 'ML weight', group: 'ml_model', kind: 'decimal', hint: 'Relative weight of the ML vote (normalized with TA + RL).' },
  ensemble_weight_rl: { label: 'RL weight', group: 'ml_model', kind: 'decimal', hint: 'Relative weight of the RL vote (normalized with TA + ML).' },
  ensemble_threshold: { label: 'Ensemble threshold', group: 'signal', kind: 'confidence', hint: 'Minimum weighted confidence to fire BUY/SELL.' },
  ensemble_require_agreement: { label: 'Require agreement', group: 'ml_model', kind: 'boolean', hint: 'Require ≥2 components on the same side before firing.' },
  gamma: { label: 'Discount factor', group: 'rl_policy', kind: 'decimal', hint: 'Gamma for reward discounting (0–1).' },
  gae_lambda: { label: 'GAE lambda', group: 'rl_policy', kind: 'decimal', hint: 'Generalized advantage estimation λ.' },
  clip_epsilon: { label: 'PPO clip ε', group: 'rl_policy', kind: 'decimal', hint: 'PPO policy ratio clip bound.' },
  ppo_epochs: { label: 'PPO epochs', group: 'rl_policy', kind: 'integer', hint: 'Optimization epochs per rollout.' },
  n_steps: { label: 'Rollout steps', group: 'rl_policy', kind: 'integer', hint: 'Environment steps per PPO rollout.' },
  total_timesteps: { label: 'Total timesteps', group: 'rl_policy', kind: 'integer', hint: 'Total RL training timesteps.' },
  vf_coef: { label: 'Value loss coef', group: 'rl_policy', kind: 'decimal', hint: 'Weight of value function loss.' },
  ent_coef: { label: 'Entropy coef', group: 'rl_policy', kind: 'decimal', hint: 'Exploration entropy bonus weight.' },
  llm_temperature: { label: 'LLM temperature', group: 'agent_llm', kind: 'decimal', hint: 'Reasoning temperature (0.0–1.0).' },
  max_reasoning_tokens: { label: 'Max reasoning tokens', group: 'agent_llm', kind: 'integer', hint: 'Cap LLM response length.' },
  require_multi_domain: { label: 'Multi-domain confirm', group: 'agent_gate', kind: 'integer', hint: 'Require ≥ N sub-report domains to agree.' },
};

const COMMON_FIELD_KEYS = ['trailing_stop_percent', 'tp_mode', 'take_profit_percent'];

export const STRATEGY_FIELD_KEYS = {
  MACD_RSI: ['rsi_length', 'macd_fast', 'macd_slow', 'macd_signal', 'atr_length', 'direction_mode'],
  SUPERTREND_ADX: ['st_length', 'st_multiplier', 'adx_length', 'adx_threshold', 'atr_length', 'block_elevated_vol', 'direction_mode', 'filter_strategy', 'vae_regime_gate_enabled'],
  BRS_SCALPING: [
    'bb_length', 'bb_std', 'rsi_length', 'stoch_k', 'stoch_d', 'stoch_smooth',
    'rsi_oversold', 'rsi_overbought', 'stoch_oversold', 'stoch_overbought', 'atr_length', 'direction_mode', 'vae_regime_gate_enabled',
  ],
  VWAP_PULLBACK: [
    'atr_length', 'rsi_length', 'use_rsi_confirmation', 'rsi_overbought_gate', 'rsi_oversold_gate', 'direction_mode', 'vae_regime_gate_enabled',
  ],
  CHART_AGENT: ['min_confidence', 'use_vol_sizing', 'use_confidence_sizing', 'require_trend_alignment', 'block_elevated_vol', 'min_score', 'confirm_timeframe', 'regime_routing_enabled', 'elevated_min_confidence', 'elevated_min_score', 'elevated_block_entries', 'compressed_min_confidence', 'calibration_gate_enabled', 'calibration_min_samples', 'calibration_min_wilson', 'meta_label_model_mode', 'meta_label_min_prob', 'meta_label_min_train_samples', 'meta_label_shadow_mode', 'use_meta_label_sizing', 'use_llm', 'llm_temperature', 'max_reasoning_tokens', 'require_multi_domain', 'rsi_length', 'macd_fast', 'macd_slow', 'macd_signal', 'atr_length', 'direction_mode'],
  ABSORPTION_AGENT: ['min_confidence', 'min_score', 'confirm_timeframe', 'calibration_gate_enabled', 'calibration_min_samples', 'calibration_min_wilson', 'trailing_stop_percent', 'direction_mode'],
  // Deploy / live inference only — training hyperparams live in Model Training.
  ML_SIGNAL_BOOST: ['min_confidence', 'model_version', 'model_symbol', 'direction_mode'],
  LSTM_DIRECTION: ['min_confidence', 'model_version', 'model_symbol', 'direction_mode'],
  RL_PPO_AGENT: ['min_confidence', 'model_version', 'model_symbol', 'direction_mode'],
  TCN_MULTI_HORIZON: ['min_return', 'min_confidence', 'model_version', 'model_symbol', 'direction_mode'],
  VAE_REGIME_DETECTOR: [
    'anomaly_threshold', 'suppress_threshold', 'model_version', 'model_symbol',
    'direction_mode',
  ],
  TRANSFORMER_SIGNAL: ['min_confidence', 'model_version', 'model_symbol', 'direction_mode'],
  GNN_CROSS_ASSET: ['min_confidence', 'min_corr', 'basket_id', 'model_version', 'model_symbol', 'direction_mode'],
  HYBRID_ENSEMBLE: [
    'ta_strategy', 'ml_strategy', 'rl_strategy',
    'ensemble_weight_ta', 'ensemble_weight_ml', 'ensemble_weight_rl',
    'ensemble_threshold', 'ensemble_require_agreement',
    'model_symbol', 'direction_mode', 'calibration_gate_enabled',
  ],
  CVD_DIVERGENCE: ['pivot_lookback', 'adx_length', 'adx_threshold', 'atr_length', 'direction_mode'],
  WYCKOFF_SPRING: ['range_lookback', 'atr_length', 'volume_surge_mult', 'direction_mode'],
  VPOC_REVERSION: ['profile_lookback', 'value_area_pct', 'rsi_length', 'adx_length', 'adx_trend_filter', 'atr_length', 'direction_mode'],
  ORDERFLOW_IMBALANCE: [
    'bair_threshold', 'mlofi_threshold', 'volume_surge_mult', 'volume_ma_length',
    'rsi_length', 'rsi_overbought', 'rsi_oversold', 'atr_length', 'allow_candle_proxy', 'book_levels', 'direction_mode',
  ],
  TICK_MOMENTUM: ['lookback_ticks', 'tick_cooldown_sec'],
  TICK_MEAN_REVERT: ['lookback_ticks', 'tick_cooldown_sec'],
  TICK_BREAKOUT: ['lookback_ticks', 'tick_cooldown_sec'],
  ICT_SMC: ['ob_lookback', 'fvg_min_gap_pct', 'sweep_lookback', 'atr_length', 'direction_mode', 'confirm_timeframe', 'filter_strategy', 'vae_regime_gate_enabled'],
  DONCHIAN_BREAKOUT: ['breakout_length', 'exit_length', 'atr_confirm_mult', 'atr_length', 'direction_mode', 'confirm_timeframe', 'filter_strategy', 'vae_regime_gate_enabled'],
  MARKET_MAKING: ['spread_pct', 'max_skew', 'vol_shutdown_mult', 'inventory_target', 'atr_length', 'direction_mode'],
};

export const TP_MODE_OPTIONS = [
  { value: 'percent', label: 'Fixed % from entry' },
  { value: 'strategy', label: 'Strategy target (BRS mid-band)', strategies: ['BRS_SCALPING'] },
  { value: 'none', label: 'None — trailing stop only' },
];

const TP_MODE_LABELS = {
  percent: 'Fixed %',
  strategy: 'Strategy target',
  none: 'None (trailing only)',
  auto: 'Auto',
};

function humanizeKey(key) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function inferGroup(key) {
  if (/^(trailing_stop|stop_loss|take_profit|tp_)/.test(key)) return 'risk';
  if (/^(lookback|hidden_dim|num_layers|learning_rate|batch_size|d_model|nhead|latent_dim|anomaly_|suppress_|n_heads|min_corr|basket_id|triple_barrier|min_train|val_fraction|retrain_interval|model_symbol|model_version|model_artifact|min_return)/.test(key)) return 'ml_model';
  if (/^(gamma|gae_lambda|clip_epsilon|ppo_epochs|n_steps|total_timesteps|vf_coef|ent_coef)/.test(key)) return 'rl_policy';
  if (/^(llm_temperature|max_reasoning_tokens)/.test(key)) return 'agent_llm';
  if (/^(require_multi_domain)/.test(key)) return 'agent_gate';
  if (/^(min_confidence|min_return)/.test(key)) return 'signal';
  if (/^(use_llm|use_vol_sizing|use_confidence|use_meta_label|require_trend|block_elevated|confirm_timeframe|min_score|calibration_|meta_label_|regime_|elevated_|compressed_)/.test(key)) return 'agent';
  if (/^(lookback_ticks|tick_)/.test(key)) return 'tick';
  if (/^(rsi|macd|atr|bb_|stoch|st_|adx)/.test(key)) return 'indicators';
  return 'other';
}

function getInputType(key, meta) {
  if (meta?.readOnly) return 'readonly';
  if (key === 'tp_mode') return 'select';
  if (key === 'direction_mode') return 'select';
  if (key === 'meta_label_model_mode') return 'select';
  if (key === 'model_version' || meta?.kind === 'model_version') return 'model_version';
  if (key === 'confirm_timeframe' || meta?.kind === 'confirm_timeframe') return 'confirm_timeframe';
  if (meta?.kind === 'boolean') return 'checkbox';
  if (meta?.kind === 'confidence' || meta?.kind === 'range' || meta?.kind === 'probability') return 'range';
  if (['percent', 'integer', 'decimal', 'seconds', 'price'].includes(meta?.kind)) return 'number';
  return 'text';
}

function fieldMeta(key) {
  return FIELD_META[key] ?? { label: humanizeKey(key), group: inferGroup(key), kind: 'text' };
}

const SWEEP_EXCLUDED_KEYS = new Set([
  'use_llm',
  'take_profit_price',
  'tp_mode',
  'model_version',
  'model_symbol',
  'model_artifact',
]);

const SWEEP_EXTRA_KEYS = ['allocation', 'slippage_bps', 'fee_bps', 'stop_loss_percent'];

const SWEEP_DEFAULT_PLACEHOLDERS = {
  trailing_stop_percent: '1, 2, 3',
  take_profit_percent: '2, 3, 5',
  stop_loss_percent: '1, 2',
  min_confidence: '0.55, 0.6, 0.65',
  min_score: '2, 3, 4',
  calibration_min_samples: '3, 5, 8',
  calibration_min_wilson: '0.4, 0.45, 0.5',
  allocation: '5000, 10000',
  slippage_bps: '0, 5, 10',
  fee_bps: '0, 5',
  rsi_length: '10, 14, 21',
  macd_fast: '8, 12',
  macd_slow: '21, 26',
  macd_signal: '7, 9',
  atr_length: '10, 14, 20',
  require_trend_alignment: 'true, false',
  block_elevated_vol: 'true, false',
  use_vol_sizing: 'true, false',
  confirm_timeframe: '15m, 1h',
  lookback_ticks: '15, 20, 30',
  tick_cooldown_sec: '5, 10, 15',
  lookback: '30, 60, 90',
  min_return: '0.0005, 0.001, 0.002',
  hidden_dim: '32, 64, 128',
  num_layers: '1, 2, 3',
  learning_rate: '0.0005, 0.001',
  batch_size: '32, 64',
  gamma: '0.95, 0.99',
  clip_epsilon: '0.1, 0.2',
  triple_barrier_atr_mult: '1.5, 2, 2.5',
  triple_barrier_max_bars: '20, 30, 40',
  val_fraction: '0.15, 0.2',
  min_train_samples: '200, 300',
  anomaly_threshold: '1.5, 2, 2.5',
  llm_temperature: '0, 0.3, 0.7',
  max_reasoning_tokens: '256, 512',
  require_multi_domain: '1, 2, 3',
};

const SHARED_RISK_SWEEP_KEYS = [
  'trailing_stop_percent', 'take_profit_percent', 'stop_loss_percent',
  'direction_mode', 'allocation', 'slippage_bps', 'fee_bps',
];

const INDICATOR_KEY_PATTERN = /^(rsi|macd|atr|bb_|stoch|st_|adx|ob_|fvg_|sweep_lookback|breakout_|exit_|atr_confirm|spread_pct|vol_shutdown|use_rsi)/;

const AGENT_SWEEP_ORDERED = [
  'min_confidence', 'min_score', 'confirm_timeframe',
  'require_trend_alignment', 'block_elevated_vol',
  'calibration_gate_enabled', 'calibration_min_samples', 'calibration_min_wilson',
  'meta_label_model_mode', 'meta_label_min_prob', 'meta_label_min_train_samples', 'meta_label_shadow_mode',
  'regime_routing_enabled', 'elevated_min_confidence', 'elevated_min_score', 'elevated_block_entries', 'compressed_min_confidence',
  'use_vol_sizing', 'use_confidence_sizing', 'use_meta_label_sizing',
  'llm_temperature', 'max_reasoning_tokens', 'require_multi_domain',
  ...SHARED_RISK_SWEEP_KEYS,
];

const ML_SWEEP_ORDERED = [
  'lookback', 'min_confidence', 'min_return', 'hidden_dim', 'num_layers',
  'learning_rate', 'batch_size', 'd_model', 'nhead', 'latent_dim',
  'anomaly_threshold', 'suppress_threshold', 'n_heads', 'min_corr',
  'triple_barrier_atr_mult', 'triple_barrier_max_bars', 'val_fraction', 'min_train_samples',
  'gamma', 'gae_lambda', 'clip_epsilon', 'ppo_epochs', 'n_steps', 'ent_coef', 'vf_coef',
  ...SHARED_RISK_SWEEP_KEYS,
];

function sweepPlaceholderFor(strategy, key, meta) {
  if (key === 'min_confidence') {
    const bounds = confidenceRangeForStrategy(strategy);
    const mid = bounds.defaultValue;
    const fmt = (n) => (bounds.max <= 0.1 ? Number(n).toPrecision(2) : String(n));
    return `${fmt(bounds.min)}, ${fmt(mid)}, ${fmt(bounds.max)}`;
  }
  return SWEEP_DEFAULT_PLACEHOLDERS[key]
    ?? (meta?.kind === 'boolean' ? 'true, false' : '1, 2, 3');
}

function buildSweepFieldList(strategy, config, orderedKeys, extraKeys = []) {
  const strat = (strategy || '').toUpperCase();
  const keys = new Set([
    ...(STRATEGY_FIELD_KEYS[strat] || []).filter((k) => !SWEEP_EXCLUDED_KEYS.has(k) && !INDICATOR_KEY_PATTERN.test(k)),
    ...extraKeys.filter((k) => !SWEEP_EXCLUDED_KEYS.has(k)),
    ...SHARED_RISK_SWEEP_KEYS,
  ]);

  for (const key of Object.keys(config || {})) {
    if (key === 'allocation' || SWEEP_EXCLUDED_KEYS.has(key) || INDICATOR_KEY_PATTERN.test(key)) continue;
    const meta = FIELD_META[key];
    if (meta && !meta.readOnly) keys.add(key);
  }

  const seen = new Set();
  const out = [];
  for (const key of orderedKeys) {
    if (!keys.has(key) || seen.has(key)) continue;
    seen.add(key);
    const meta = fieldMeta(key);
    out.push({
      key,
      label: meta.label,
      kind: meta.kind,
      placeholder: sweepPlaceholderFor(strat, key, meta),
      hint: meta.hint,
    });
  }
  for (const key of keys) {
    if (seen.has(key)) continue;
    const meta = fieldMeta(key);
    out.push({
      key,
      label: meta.label,
      kind: meta.kind,
      placeholder: sweepPlaceholderFor(strat, key, meta),
      hint: meta.hint,
    });
  }
  return out;
}

/** Strategy-aware sweep param definitions for BacktestSweepPanel. */
export function getSweepEligibleFields(strategy, config = {}) {
  const category = getStrategyCategory(strategy);

  if (category === 'ml') {
    const subtype = getMLSubtype(strategy);
    const ordered = subtype === 'rl'
      ? ['gamma', 'clip_epsilon', 'ppo_epochs', 'n_steps', 'min_confidence', ...ML_SWEEP_ORDERED]
      : ML_SWEEP_ORDERED;
    // Train hyperparams stay sweepable even though deploy STRATEGY_FIELD_KEYS is inference-only.
    return buildSweepFieldList(strategy, config, ordered, ML_SWEEP_ORDERED);
  }

  if (category === 'agent') {
    return buildSweepFieldList(strategy, config, AGENT_SWEEP_ORDERED);
  }

  const strat = (strategy || '').toUpperCase();
  const keys = new Set([
    ...COMMON_FIELD_KEYS.filter((k) => !SWEEP_EXCLUDED_KEYS.has(k)),
    ...(STRATEGY_FIELD_KEYS[strat] || []).filter((k) => !SWEEP_EXCLUDED_KEYS.has(k)),
    ...SWEEP_EXTRA_KEYS,
  ]);

  for (const key of Object.keys(config || {})) {
    if (key === 'allocation' || SWEEP_EXCLUDED_KEYS.has(key)) continue;
    const meta = FIELD_META[key];
    if (meta && !meta.readOnly) keys.add(key);
  }

  const ordered = [
    'trailing_stop_percent', 'take_profit_percent', 'stop_loss_percent',
    'min_confidence', 'min_score', 'require_trend_alignment', 'block_elevated_vol',
    'calibration_gate_enabled', 'calibration_min_samples', 'calibration_min_wilson',
    'confirm_timeframe', 'use_vol_sizing',
    'rsi_length', 'macd_fast', 'macd_slow', 'macd_signal', 'atr_length',
    'bb_length', 'bb_std', 'stoch_k', 'stoch_d', 'stoch_smooth',
    'rsi_oversold', 'rsi_overbought', 'stoch_oversold', 'stoch_overbought',
    'st_length', 'st_multiplier', 'adx_length', 'adx_threshold',
    'lookback_ticks', 'tick_cooldown_sec',
    'allocation', 'slippage_bps', 'fee_bps',
  ];

  const seen = new Set();
  const out = [];
  for (const key of ordered) {
    if (!keys.has(key) || seen.has(key)) continue;
    seen.add(key);
    const meta = fieldMeta(key);
    out.push({
      key,
      label: meta.label,
      kind: meta.kind,
      placeholder: sweepPlaceholderFor(strat, key, meta),
      hint: meta.hint,
    });
  }
  for (const key of keys) {
    if (seen.has(key)) continue;
    const meta = fieldMeta(key);
    out.push({
      key,
      label: meta.label,
      kind: meta.kind,
      placeholder: sweepPlaceholderFor(strat, key, meta),
      hint: meta.hint,
    });
  }
  return out;
}

export function getEditableConfigFields(strategy, config = {}) {
  const strat = (strategy || '').toUpperCase();
  const strategyKeys = STRATEGY_FIELD_KEYS[strat];
  const keys = new Set([
    ...COMMON_FIELD_KEYS,
    ...(strategyKeys || []),
  ]);

  // Only pull extra config keys when there is no schema (custom / unknown strategies).
  // Catalog defaults include train-time hyperparams that must not flood the deploy UI.
  if (!strategyKeys) {
    for (const key of Object.keys(config || {})) {
      if (key === 'allocation') continue;
      if (FIELD_META[key] && !FIELD_META[key].readOnly) keys.add(key);
    }
  }

  return Array.from(keys).map((key) => {
    const meta = fieldMeta(key);
    return {
      key,
      label: meta.label,
      group: meta.group,
      kind: meta.kind,
      hint: meta.hint,
      input: getInputType(key, meta),
    };
  });
}

/** Keys allowed on deploy bot.config for a strategy (risk + strategy schema). */
export function deployConfigKeysForStrategy(strategy) {
  const strat = String(strategy || '').toUpperCase();
  const keys = new Set([
    'allocation',
    ...COMMON_FIELD_KEYS,
    ...(STRATEGY_FIELD_KEYS[strat] || []),
  ]);
  // Optimizer pin extras — keep when the strategy supports model versioning.
  if (keys.has('model_version') || keys.has('model_symbol')) {
    keys.add('model_artifact');
  }
  return keys;
}

/**
 * Build a clean deploy config from catalog defaults — drops train-only / stale keys.
 * @param {string} strategy
 * @param {Record<string, unknown>} raw
 */
export function pickDeployConfig(strategy, raw = {}) {
  const allowed = deployConfigKeysForStrategy(strategy);
  const out = {};
  for (const [key, value] of Object.entries(raw || {})) {
    if (!allowed.has(key)) continue;
    if (value === '' || value === undefined || value === null) continue;
    out[key] = value;
  }
  if (!out.direction_mode) {
    const strat = String(strategy || '').toUpperCase();
    out.direction_mode = (
      strat === 'CHART_AGENT' || strat.startsWith('ML_') || strat === 'LSTM_DIRECTION'
      || strat === 'RL_PPO_AGENT' || strat === 'TCN_MULTI_HORIZON'
      || strat === 'VAE_REGIME_DETECTOR' || strat === 'TRANSFORMER_SIGNAL'
      || strat === 'GNN_CROSS_ASSET' || strat === 'HYBRID_ENSEMBLE'
    ) ? 'BOTH' : 'LONG_ONLY';
  }
  if (out.trailing_stop_percent == null) out.trailing_stop_percent = 2;
  if (out.tp_mode == null) out.tp_mode = 'percent';
  return out;
}

/**
 * Full replace payload for optimizer / chart / scanner apply paths.
 * Seeds risk/pin fields from `current`, overlays winner `cfg` + optional `extras`,
 * then strips keys that are not valid for the target strategy.
 *
 * @param {string} strategy
 * @param {Record<string, unknown>} cfg
 * @param {{ current?: Record<string, unknown>, extras?: Record<string, unknown> | null }} [opts]
 */
export function buildAppliedDeployConfig(strategy, cfg = {}, opts = {}) {
  const current = opts.current && typeof opts.current === 'object' ? opts.current : {};
  const extras = opts.extras && typeof opts.extras === 'object' ? opts.extras : {};
  return pickDeployConfig(strategy, {
    allocation: current.allocation,
    trailing_stop_percent: current.trailing_stop_percent,
    take_profit_percent: current.take_profit_percent,
    tp_mode: current.tp_mode,
    direction_mode: current.direction_mode,
    model_version: current.model_version,
    model_symbol: current.model_symbol,
    model_artifact: current.model_artifact,
    ...(cfg || {}),
    ...extras,
  });
}

/** Confidence slider bounds — RL/TCN use non-probability scales. */
export function confidenceRangeForStrategy(strategy) {
  const strat = String(strategy || '').toUpperCase();
  if (strat === 'RL_PPO_AGENT') return { min: 0.15, max: 0.8, step: 0.01, defaultValue: 0.28 };
  if (strat === 'TCN_MULTI_HORIZON') return { min: 0.0005, max: 0.05, step: 0.0005, defaultValue: 0.002 };
  return { min: 0.4, max: 1, step: 0.05, defaultValue: 0.55 };
}

export function isFieldVisible(field, draft) {
  if (field.key === 'take_profit_percent') {
    return (draft.tp_mode ?? 'percent') === 'percent';
  }
  return true;
}

export function buildConfigDraft(config, fields) {
  const draft = {};
  for (const f of fields) {
    const v = config?.[f.key];
    if (f.input === 'checkbox') {
      draft[f.key] = Boolean(v);
    } else if (f.input === 'select' || f.input === 'confirm_timeframe') {
      const selectDefault = f.key === 'direction_mode'
        ? 'LONG_ONLY'
        : f.key === 'meta_label_model_mode'
          ? 'wilson'
          : f.key === 'confirm_timeframe'
            ? ''
            : 'percent';
      if (f.key === 'confirm_timeframe' && v) {
        const normalized = normalizeConfirmTimeframe(v);
        draft[f.key] = normalized.ok ? normalized.value : String(v);
      } else {
        draft[f.key] = v ?? selectDefault;
      }
    } else if (f.input === 'number' || f.input === 'range') {
      draft[f.key] = v != null && v !== '' ? String(v) : '';
    } else {
      draft[f.key] = v != null ? String(v) : '';
    }
  }
  return draft;
}

function parseFieldValue(field, raw) {
  if (field.input === 'readonly') return raw;
  if (field.key === 'tp_mode') return String(raw || 'percent');
  if (field.input === 'checkbox') return Boolean(raw);
  if (field.input === 'range') {
    const n = parseFloat(raw);
    return Number.isNaN(n) ? null : n;
  }
  if (field.input === 'number') {
    if (raw === '' || raw == null) return null;
    const n = parseFloat(raw);
    return Number.isNaN(n) ? null : n;
  }
  if (raw === '') return null;
  return raw;
}

function valuesEqual(a, b) {
  if (a === b) return true;
  if (a == null && b == null) return true;
  if (typeof a === 'number' && typeof b === 'number') return Math.abs(a - b) < 1e-9;
  return false;
}

/** @returns {string|null} First validation error, or null if patch is valid. */
export function validateConfigPatch(patch, { botTimeframe } = {}) {
  if (!patch || typeof patch !== 'object') return 'Invalid config patch';
  if ('confirm_timeframe' in patch) {
    const result = normalizeConfirmTimeframe(patch.confirm_timeframe);
    if (!result.ok) return result.error;
    const confirmTf = result.value;
    if (confirmTf && botTimeframe) {
      const botTf = normalizeConfirmTimeframe(botTimeframe);
      if (botTf.ok && confirmTf === botTf.value) {
        return `Confirm timeframe must differ from bot timeframe (${botTf.value})`;
      }
    }
  }
  return null;
}

export function buildConfigPatch(draft, originalConfig, fields, options = {}) {
  const patch = {};
  for (const f of fields) {
    if (!isFieldVisible(f, draft)) {
      if (f.key === 'take_profit_percent' && originalConfig?.take_profit_percent != null) {
        patch.take_profit_percent = null;
      }
      continue;
    }
    let parsed = parseFieldValue(f, draft[f.key]);
    if (f.key === 'confirm_timeframe') {
      const result = normalizeConfirmTimeframe(parsed);
      if (!result.ok) {
        throw new Error(result.error);
      }
      parsed = result.value || null;
    }
    const orig = originalConfig?.[f.key];
    if (!valuesEqual(parsed, orig)) {
      patch[f.key] = parsed;
    }
  }
  const err = validateConfigPatch(patch, options);
  if (err) throw new Error(err);
  return patch;
}

export function buildConfigFieldGroups(fields, draft) {
  const buckets = new Map(GROUP_ORDER.map((id) => [id, []]));
  for (const field of fields) {
    if (!isFieldVisible(field, draft)) continue;
    buckets.get(field.group)?.push(field);
  }
  return GROUP_ORDER
    .map((id) => ({
      id,
      label: GROUP_LABELS[id],
      fields: (buckets.get(id) ?? []).sort((a, b) => a.label.localeCompare(b.label)),
    }))
    .filter((g) => g.fields.length > 0);
}

export function formatBotConfigValue(key, value, meta = fieldMeta(key)) {
  if (value === null || value === undefined || value === '') {
    return { text: '—', tone: 'muted' };
  }

  const kind = meta?.kind ?? 'text';

  if (kind === 'boolean' || typeof value === 'boolean') {
    return value ? { text: 'Enabled', tone: 'positive' } : { text: 'Disabled', tone: 'muted' };
  }
  if (kind === 'tp_mode') {
    const mode = String(value).toLowerCase();
    return { text: TP_MODE_LABELS[mode] ?? humanizeKey(mode), tone: 'default' };
  }
  if (kind === 'direction_mode') {
    return { text: formatDirectionModeLabel(value), tone: 'default' };
  }
  if (kind === 'meta_label_mode') {
    const MODE_LABELS = { wilson: 'Wilson buckets', gbm: 'GBM classifier', hybrid: 'Hybrid (GBM + Wilson)' };
    return { text: MODE_LABELS[String(value).toLowerCase()] ?? String(value), tone: 'default' };
  }
  if ((kind === 'confidence' || kind === 'range' || kind === 'probability') && typeof value === 'number') {
    // Probability-style gates (≥0.1) as %; TCN-scale magnitudes stay decimal.
    if (value >= 0.1 && value <= 1) {
      return { text: `${Math.round(value * 100)}%`, tone: 'default' };
    }
    return { text: String(value), tone: 'default' };
  }
  if (kind === 'percent' && typeof value === 'number') {
    return { text: `${value}%`, tone: 'default' };
  }
  if (kind === 'seconds' && typeof value === 'number') {
    return { text: `${value}s`, tone: 'default' };
  }
  if (typeof value === 'number') {
    return { text: Number.isInteger(value) ? String(value) : String(value), tone: 'default' };
  }
  return { text: String(value), tone: 'default' };
}
