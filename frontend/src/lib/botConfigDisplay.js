/** Labels, grouping, and editable field schema for bot strategy config. */

export const GROUP_ORDER = ['risk', 'agent', 'indicators', 'tick', 'other'];

export const GROUP_LABELS = {
  risk: 'Risk & exits',
  agent: 'Chart agent',
  indicators: 'Indicators',
  tick: 'Tick execution',
  other: 'Other',
};

export const FIELD_META = {
  trailing_stop_percent: { label: 'Trailing stop', group: 'risk', kind: 'percent', hint: 'Exits when price retraces this % from the best price since entry.' },
  stop_loss_percent: { label: 'Stop loss', group: 'risk', kind: 'percent' },
  take_profit_percent: { label: 'Take profit', group: 'risk', kind: 'percent', hint: 'Closes the position when price reaches this % target.' },
  take_profit_price: { label: 'Take profit price', group: 'risk', kind: 'price', readOnly: true },
  tp_mode: { label: 'Take profit mode', group: 'risk', kind: 'tp_mode' },
  min_confidence: { label: 'Min confidence', group: 'agent', kind: 'confidence', hint: 'Agent only trades when signal confidence meets this threshold.' },
  use_vol_sizing: { label: 'Vol sizing', group: 'agent', kind: 'boolean', hint: 'Scale entry size by risk sub-report suggested_size_factor.' },
  require_trend_alignment: { label: 'Trend alignment', group: 'agent', kind: 'boolean', hint: 'BUY only when trend score ≥ +1; SELL when ≤ −1.' },
  use_rsi_confirmation: { label: 'RSI confirmation', group: 'indicators', kind: 'boolean', hint: 'Require RSI not overbought/oversold on VWAP cross entries.' },
  rsi_overbought_gate: { label: 'RSI overbought gate', group: 'indicators', kind: 'integer', hint: 'Block VWAP buy when RSI above this (default 60).' },
  rsi_oversold_gate: { label: 'RSI oversold gate', group: 'indicators', kind: 'integer', hint: 'Block VWAP sell when RSI below this (default 40).' },
  block_elevated_vol: { label: 'Block elevated vol', group: 'indicators', kind: 'boolean', hint: 'Skip entries when ATR is ≥1.5× its 20-bar median.' },
  min_score: { label: 'Min score', group: 'agent', kind: 'integer', hint: 'Require |composite score| ≥ this value.' },
  confirm_timeframe: { label: 'Confirm TF', group: 'agent', kind: 'text', hint: 'Higher timeframe trend must confirm entry.' },
  calibration_gate_enabled: { label: 'Calibration gate', group: 'agent', kind: 'boolean', hint: 'Block entries when the setup bucket underperforms in closed-trade history.' },
  calibration_min_samples: { label: 'Gate min samples', group: 'agent', kind: 'integer', hint: 'Minimum closed trades in a bucket before the gate can block.' },
  calibration_min_wilson: { label: 'Gate min Wilson', group: 'agent', kind: 'confidence', hint: 'Wilson lower-bound win rate required to allow entry (0–1).' },
  meta_label_model_mode: { label: 'Meta-label mode', group: 'agent', kind: 'meta_label_mode', hint: 'wilson = bucket stats only; gbm = gradient-boosted P(win); hybrid = GBM when trained else Wilson.' },
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
  filter_strategy: { label: 'Filter strategy', group: 'other', kind: 'text', hint: 'Gate signals through a secondary strategy (e.g. SUPERTREND_ADX).' },
  filter_mode: { label: 'Filter mode', group: 'other', kind: 'text', hint: 'How the filter gates signals (TREND_GATE).' },
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
};

const COMMON_FIELD_KEYS = ['trailing_stop_percent', 'tp_mode', 'take_profit_percent'];

export const STRATEGY_FIELD_KEYS = {
  MACD_RSI: ['rsi_length', 'macd_fast', 'macd_slow', 'macd_signal', 'atr_length', 'direction_mode'],
  SUPERTREND_ADX: ['st_length', 'st_multiplier', 'adx_length', 'adx_threshold', 'atr_length', 'block_elevated_vol', 'direction_mode'],
  BRS_SCALPING: [
    'bb_length', 'bb_std', 'rsi_length', 'stoch_k', 'stoch_d', 'stoch_smooth',
    'rsi_oversold', 'rsi_overbought', 'stoch_oversold', 'stoch_overbought', 'atr_length', 'direction_mode',
  ],
  VWAP_PULLBACK: [
    'atr_length', 'rsi_length', 'use_rsi_confirmation', 'rsi_overbought_gate', 'rsi_oversold_gate', 'direction_mode',
  ],
  CHART_AGENT: ['min_confidence', 'use_vol_sizing', 'use_confidence_sizing', 'require_trend_alignment', 'block_elevated_vol', 'min_score', 'confirm_timeframe', 'regime_routing_enabled', 'elevated_min_confidence', 'elevated_min_score', 'elevated_block_entries', 'compressed_min_confidence', 'calibration_gate_enabled', 'calibration_min_samples', 'calibration_min_wilson', 'meta_label_model_mode', 'meta_label_min_prob', 'meta_label_min_train_samples', 'meta_label_shadow_mode', 'use_meta_label_sizing', 'use_llm', 'rsi_length', 'macd_fast', 'macd_slow', 'macd_signal', 'atr_length', 'direction_mode'],
  TICK_MOMENTUM: ['lookback_ticks', 'tick_cooldown_sec'],
  TICK_MEAN_REVERT: ['lookback_ticks', 'tick_cooldown_sec'],
  TICK_BREAKOUT: ['lookback_ticks', 'tick_cooldown_sec'],
  ICT_SMC: ['ob_lookback', 'fvg_min_gap_pct', 'sweep_lookback', 'atr_length', 'direction_mode', 'confirm_timeframe', 'filter_strategy'],
  DONCHIAN_BREAKOUT: ['breakout_length', 'exit_length', 'atr_confirm_mult', 'atr_length', 'direction_mode', 'confirm_timeframe', 'filter_strategy'],
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
  if (/^(min_confidence|use_llm|use_vol_sizing|use_confidence|use_meta_label|require_trend|block_elevated|confirm_timeframe|min_score|calibration_|meta_label_|regime_|elevated_|compressed_)/.test(key)) return 'agent';
  if (/^(lookback_ticks|tick_)/.test(key)) return 'tick';
  if (/^(rsi|macd|atr|bb_|stoch|st_|adx)/.test(key)) return 'indicators';
  return 'other';
}

function getInputType(key, meta) {
  if (meta?.readOnly) return 'readonly';
  if (key === 'tp_mode') return 'select';
  if (key === 'direction_mode') return 'select';
  if (key === 'meta_label_model_mode') return 'select';
  if (meta?.kind === 'boolean') return 'checkbox';
  if (meta?.kind === 'confidence') return 'range';
  if (['percent', 'integer', 'decimal', 'seconds', 'price'].includes(meta?.kind)) return 'number';
  return 'text';
}

function fieldMeta(key) {
  return FIELD_META[key] ?? { label: humanizeKey(key), group: inferGroup(key), kind: 'text' };
}

const SWEEP_EXCLUDED_KEYS = new Set(['use_llm', 'take_profit_price', 'tp_mode']);

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
};

/** Strategy-aware sweep param definitions for BacktestSweepPanel. */
export function getSweepEligibleFields(strategy, config = {}) {
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
      placeholder: SWEEP_DEFAULT_PLACEHOLDERS[key]
        ?? (meta.kind === 'boolean' ? 'true, false' : '1, 2, 3'),
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
      placeholder: SWEEP_DEFAULT_PLACEHOLDERS[key] ?? '1, 2, 3',
      hint: meta.hint,
    });
  }
  return out;
}

export function getEditableConfigFields(strategy, config = {}) {
  const strat = (strategy || '').toUpperCase();
  const keys = new Set([
    ...COMMON_FIELD_KEYS,
    ...(STRATEGY_FIELD_KEYS[strat] || []),
  ]);

  for (const key of Object.keys(config || {})) {
    if (key === 'allocation') continue;
    if (FIELD_META[key] && !FIELD_META[key].readOnly) keys.add(key);
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
    } else if (f.input === 'select') {
      const selectDefault = f.key === 'direction_mode'
        ? 'LONG_ONLY'
        : f.key === 'meta_label_model_mode'
          ? 'wilson'
          : 'percent';
      draft[f.key] = v ?? selectDefault;
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

export function buildConfigPatch(draft, originalConfig, fields) {
  const patch = {};
  for (const f of fields) {
    if (!isFieldVisible(f, draft)) {
      if (f.key === 'take_profit_percent' && originalConfig?.take_profit_percent != null) {
        patch.take_profit_percent = null;
      }
      continue;
    }
    const parsed = parseFieldValue(f, draft[f.key]);
    const orig = originalConfig?.[f.key];
    if (!valuesEqual(parsed, orig)) {
      patch[f.key] = parsed;
    }
  }
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
    const DIRECTION_LABELS = { LONG_ONLY: 'Long only', SHORT_ONLY: 'Short only', BOTH: 'Both' };
    return { text: DIRECTION_LABELS[String(value).toUpperCase()] ?? String(value), tone: 'default' };
  }
  if (kind === 'meta_label_mode') {
    const MODE_LABELS = { wilson: 'Wilson buckets', gbm: 'GBM classifier', hybrid: 'Hybrid (GBM + Wilson)' };
    return { text: MODE_LABELS[String(value).toLowerCase()] ?? String(value), tone: 'default' };
  }
  if (kind === 'confidence' && typeof value === 'number') {
    return { text: `${Math.round(value * 100)}%`, tone: 'default' };
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
