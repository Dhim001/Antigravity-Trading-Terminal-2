import { isDeepMlStrategy, ML_STRATEGY_IDS } from '@/config/strategies';

export const ML_STRATEGIES = ML_STRATEGY_IDS;
export const DEEP_ML_STRATEGIES = new Set(
  ML_STRATEGY_IDS.filter((id) => isDeepMlStrategy(id)),
);

export const ML_LAB_TRAIN_INIT_KEY = 'ml-lab-train-init';

/** Lab train init: resume champion weights vs random init + full budget. */
export function parseTrainInit(value, fallback = 'warm') {
  const raw = String(value ?? fallback).trim().toLowerCase();
  if (raw === 'scratch' || raw === 'from_scratch' || raw === 'cold' || raw === 'random') {
    return 'scratch';
  }
  return 'warm';
}

export function readStoredTrainInit() {
  try {
    const v = window.localStorage.getItem(ML_LAB_TRAIN_INIT_KEY);
    if (v === 'scratch' || v === 'warm') return v;
  } catch {
    /* ignore */
  }
  return 'warm';
}

export function persistTrainInit(value) {
  const next = parseTrainInit(value);
  try {
    window.localStorage.setItem(ML_LAB_TRAIN_INIT_KEY, next);
  } catch {
    /* ignore */
  }
  return next;
}

/** Defaults: Train + Validate share production capacity (accuracy-first). */
export function defaultAdvancedKnobs(strategy, kind = 'validate') {
  const isRl = strategy === 'RL_PPO_AGENT';
  // kind retained for callers; capacity knobs match Train either way.
  void kind;
  let epochs = 100;
  if (strategy === 'TCN_MULTI_HORIZON') epochs = 100;
  else if (strategy === 'VAE_REGIME_DETECTOR') epochs = 120;
  else if (strategy === 'GNN_CROSS_ASSET') epochs = 60;
  else if (strategy === 'TRANSFORMER_SIGNAL') epochs = 80;
  else if (strategy === 'LSTM_DIRECTION') epochs = 100;
  return {
    nFolds: isRl ? 2 : 3,
    validateMaxBars: isRl ? 4000 : 12_000,
    pboSegments: 4,
    pboMaxCombos: 4,
    totalTimesteps: 200_000,
    epochs,
    // Stop when val loss plateaus — budget epochs is a ceiling, not a requirement.
    earlyStopPatience: 10,
    hiddenDim: isRl ? 256 : 128,
    gbmMaxIter: 300,
    gbmMaxDepth: 6,
    eventFilter: 'cusum',
    cusumThreshold: 1,
    featureScheme: 'v8',
    trainInit: readStoredTrainInit(),
  };
}

export function normalizeTopFeatures(top) {
  if (!Array.isArray(top)) return [];
  return top
    .map((f) => {
      if (typeof f === 'string') return { name: f, importance: 1 };
      const name = f?.name || f?.feature;
      if (!name) return null;
      const importance = Number(f.importance ?? f.gain ?? f.weight ?? 0);
      return {
        name: String(name),
        importance: Number.isFinite(importance) ? importance : 0,
        category: f.category,
      };
    })
    .filter(Boolean);
}

export function parsePositiveInt(value, fallback, { min = 1, max = 1_000_000 } = {}) {
  const n = Number.parseInt(String(value), 10);
  if (!Number.isFinite(n)) return fallback;
  return Math.min(max, Math.max(min, n));
}

export const FEATURE_SCHEME_OPTIONS = [
  { value: 'v8', label: 'v8 current (84 cols)' },
  { value: 'v7', label: 'v7 legacy (zero v8 extras)' },
  { value: 'v8_no_ict', label: 'v8 minus ICT' },
  { value: 'v8_no_ofi', label: 'v8 minus OFI' },
  { value: 'v8_no_profile', label: 'v8 minus profile' },
  { value: 'v8_no_hygiene', label: 'v8 minus YZ/AVWAP/FFD' },
  { value: 'v8_no_events', label: 'v8 minus earnings/macro' },
  { value: 'v8_no_vpin', label: 'v8 minus VPIN' },
];

export const TRAINING_WINDOWS = [
  { value: '1', label: '1 month', targetBars1m: 12000 },
  { value: '3', label: '3 months', targetBars1m: 25000 },
  { value: '6', label: '6 months', targetBars1m: 40000 },
  { value: '12', label: '12 months', targetBars1m: 50000 },
  { value: '18', label: '18 months', targetBars1m: 65000 },
  { value: '24', label: '24 months', targetBars1m: 80000 },
  { value: '36', label: '36 months', targetBars1m: 100000 },
];

export const TRAINING_TIMEFRAMES = [
  { value: '1m', label: '1 minute', secs: 60 },
  { value: '5m', label: '5 minutes', secs: 300 },
  { value: '15m', label: '15 minutes', secs: 900 },
  { value: '1h', label: '1 hour', secs: 3600 },
  { value: '4h', label: '4 hours', secs: 14400 },
];

export const ML_LAB_WINDOW_KEY = 'ml-lab-training-window';
export const ML_LAB_TF_KEY = 'ml-lab-training-timeframe';

export function estimateTrainingBars(monthsValue, tfValue) {
  // Mirror backend ``bar_limit_for_training_window`` (train purpose).
  const months = Number(monthsValue) || 3;
  const tf = TRAINING_TIMEFRAMES.find((t) => t.value === tfValue) || TRAINING_TIMEFRAMES[0];
  const secs = tf.secs || 60;
  const hard = 100_000;
  const ideal = Math.floor(months * 30 * 86400 / secs);
  if (secs > 60) {
    // HTF: honor calendar window up to hard max (do not scale-crush from 1m caps).
    return Math.max(500, Math.min(ideal, hard));
  }
  const win = TRAINING_WINDOWS.find((w) => w.value === String(monthsValue));
  const cap1m = win?.targetBars1m ?? 25000;
  return Math.max(500, Math.min(ideal, cap1m, hard));
}

/**
 * Validate bar budget at capacity parity — same calendar window as Train so
 * walk-forward OOS reflects production data depth (not a lean smoke slice).
 */
export function estimateValidateBars(monthsValue, tfValue, strategy) {
  const trainBars = estimateTrainingBars(monthsValue, tfValue);
  if (strategy === 'RL_PPO_AGENT') {
    // RL env steps dominate wall-clock; still use a deep window, not 1.2k lean.
    return Math.max(2_000, Math.min(trainBars, 20_000));
  }
  return trainBars;
}

export function suggestedNFolds(monthsValue, strategy) {
  if (strategy === 'RL_PPO_AGENT') return 2;
  const months = Number(monthsValue) || 3;
  if (months >= 24) return 5;
  if (months >= 12) return 4;
  if (months >= 6) return 3;
  return 3;
}

export function suggestedPboSegments(monthsValue, strategy) {
  if (strategy === 'RL_PPO_AGENT') return 4;
  const months = Number(monthsValue) || 3;
  if (months >= 24) return 8;
  if (months >= 12) return 6;
  if (months >= 6) return 5;
  return 4;
}

/** Apply window/TF-driven defaults onto Advanced knobs (keeps architecture fields). */
export function syncAdvancedForWindow(prev, strategy, monthsValue, tfValue) {
  const base = defaultAdvancedKnobs(strategy, 'train');
  return {
    ...base,
    ...prev,
    // Always re-derive data-budget knobs from the Lab window pick.
    nFolds: String(suggestedNFolds(monthsValue, strategy)),
    validateMaxBars: String(estimateValidateBars(monthsValue, tfValue, strategy)),
    pboSegments: String(suggestedPboSegments(monthsValue, strategy)),
    // Keep user architecture / epochs if they already edited them this session.
    epochs: prev?.epochs ?? base.epochs,
    earlyStopPatience: prev?.earlyStopPatience ?? base.earlyStopPatience,
    hiddenDim: prev?.hiddenDim ?? base.hiddenDim,
    totalTimesteps: prev?.totalTimesteps ?? base.totalTimesteps,
    gbmMaxIter: prev?.gbmMaxIter ?? base.gbmMaxIter,
    gbmMaxDepth: prev?.gbmMaxDepth ?? base.gbmMaxDepth,
    eventFilter: prev?.eventFilter ?? base.eventFilter,
    cusumThreshold: prev?.cusumThreshold ?? base.cusumThreshold,
    featureScheme: prev?.featureScheme ?? base.featureScheme,
    trainInit: parseTrainInit(prev?.trainInit ?? base.trainInit),
    pboMaxCombos: prev?.pboMaxCombos ?? base.pboMaxCombos,
  };
}

export function readStoredTrainingWindow() {
  try {
    const v = window.localStorage.getItem(ML_LAB_WINDOW_KEY);
    if (TRAINING_WINDOWS.some((w) => w.value === v)) return v;
  } catch {
    /* ignore */
  }
  return '3';
}

export function readStoredTrainingTimeframe(fallback) {
  try {
    const v = window.localStorage.getItem(ML_LAB_TF_KEY);
    if (TRAINING_TIMEFRAMES.some((t) => t.value === v)) return v;
  } catch {
    /* ignore */
  }
  return fallback;
}

/** RL 1m trains fail the DQ gap gate (~21% gaps). Prefer 5m unless the user pinned a TF. */
export function preferredTrainingTimeframe(strategy, fallback = '1m') {
  const stored = readStoredTrainingTimeframe(null);
  if (stored) return stored;
  if (String(strategy || '').toUpperCase() === 'RL_PPO_AGENT') return '5m';
  return fallback || '1m';
}

export const METRIC_LABELS = {
  total_timesteps: 'Timesteps',
  episodes: 'Episodes',
  mean_return_pct: 'Mean return',
  best_mean_return: 'Best return',
  mean_trades_per_episode: 'Trades / ep',
  hidden_dim: 'Hidden dim',
  val_accuracy: 'Val accuracy',
  accuracy: 'Accuracy',
  auc_roc: 'AUC-ROC',
  val_loss: 'Val loss',
  train_loss: 'Train loss',
  log_loss: 'Log loss',
  sharpe: 'Sharpe',
  pbo: 'PBO',
  mean_oos_accuracy: 'Mean OOS acc',
  epochs_trained: 'Epochs trained',
  epochs_budget: 'Epoch budget',
  early_stop_patience: 'Early-stop patience',
};

export const INT_METRIC_KEYS = new Set([
  'total_timesteps',
  'episodes',
  'hidden_dim',
  'n_folds',
  'successful_folds',
  'sample_count',
  'train_samples',
  'val_samples',
  'n_samples',
  'epochs_trained',
  'epochs_budget',
  'early_stop_patience',
]);

export const PCT_METRIC_KEYS = new Set([
  'val_accuracy',
  'accuracy',
  'auc_roc',
  'pbo',
  'mean_oos_accuracy',
  'mean_return_pct',
  'best_mean_return',
]);

export function fmtMetric(v, digits = 3, key = '') {
  if (v == null || Number.isNaN(Number(v))) return null;
  const n = Number(v);
  if (INT_METRIC_KEYS.has(key) || Number.isInteger(n)) {
    return Math.abs(n) >= 1000 ? n.toLocaleString() : String(Math.round(n));
  }
  if (PCT_METRIC_KEYS.has(key)) {
    // RL returns are stored as percent points (e.g. -0.086 = -0.086%);
    // classifier probs are 0–1 fractions.
    if (key === 'mean_return_pct' || key === 'best_mean_return') {
      if (Math.abs(n) <= 1) return `${n.toFixed(3)}%`;
      return `${n.toFixed(2)}%`;
    }
    if (n >= 0 && n <= 1) return `${(n * 100).toFixed(1)}%`;
  }
  if (Math.abs(n) >= 100) return n.toFixed(1);
  if (Math.abs(n) >= 10) return n.toFixed(2);
  return n.toFixed(digits);
}

export function metricLabel(key) {
  return METRIC_LABELS[key] || key.replace(/_/g, ' ');
}

export function pickMetricEntries(metrics) {
  if (!metrics || typeof metrics !== 'object') return [];
  const preferred = [
    'train_accuracy',
    'val_accuracy',
    'overfitting_gap',
    'signal_rate',
    'total_timesteps',
    'episodes',
    'mean_return_pct',
    'best_mean_return',
    'mean_trades_per_episode',
    'hidden_dim',
    'accuracy',
    'auc_roc',
    'val_loss',
    'epochs_trained',
    'epochs_budget',
    'sharpe',
    'pbo',
  ];
  const entries = preferred
    .filter((k) => metrics[k] != null && typeof metrics[k] !== 'object')
    .map((k) => [k, metrics[k]]);
  Object.entries(metrics).forEach(([k, v]) => {
    if (
      typeof v === 'number'
      && Number.isFinite(v)
      && !preferred.includes(k)
      && !k.startsWith('last_')
      && entries.length < 10
    ) {
      entries.push([k, v]);
    }
  });
  return entries;
}

export function trainJobPhases(strategy) {
  if (strategy === 'RL_PPO_AGENT') {
    return [
      { until: 12, label: 'Fetching & enriching candles…' },
      { until: 88, label: 'Running PPO rollouts / policy updates…' },
      { until: 96, label: 'Exporting ONNX policy…' },
      { until: 100, label: 'Saving model artifacts…' },
    ];
  }
  if (DEEP_ML_STRATEGIES.has(strategy)) {
    return [
      { until: 15, label: 'Fetching & enriching candles…' },
      { until: 85, label: 'Training neural network…' },
      { until: 100, label: 'Exporting & saving artifacts…' },
    ];
  }
  return [
    { until: 20, label: 'Fetching & enriching candles…' },
    { until: 80, label: 'Fitting model…' },
    { until: 100, label: 'Saving artifacts…' },
  ];
}

export function validateJobPhases(strategy) {
  if (strategy === 'RL_PPO_AGENT') {
    return [
      { until: 10, label: 'Loading candles for validation…' },
      { until: 90, label: 'Walk-forward folds (RL episode returns)…' },
      { until: 100, label: 'Aggregating fold returns…' },
    ];
  }
  return [
    { until: 12, label: 'Loading candles for validation…' },
    { until: 70, label: 'Walk-forward folds…' },
    { until: 92, label: 'Computing PBO…' },
    { until: 100, label: 'Aggregating results…' },
  ];
}

export function formatElapsed(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  return m > 0 ? `${m}m ${String(r).padStart(2, '0')}s` : `${r}s`;
}

export function formatDurationMs(ms) {
  if (ms == null || Number.isNaN(Number(ms))) return '—';
  return formatElapsed(Number(ms));
}
