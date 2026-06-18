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
  use_llm: { label: 'LLM analysis', group: 'agent', kind: 'boolean', hint: 'Use the LLM layer for chart narrative (requires API key).' },
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
};

const COMMON_FIELD_KEYS = ['trailing_stop_percent', 'tp_mode', 'take_profit_percent'];

const STRATEGY_FIELD_KEYS = {
  MACD_RSI: ['rsi_length', 'macd_fast', 'macd_slow', 'macd_signal', 'atr_length'],
  SUPERTREND_ADX: ['st_length', 'st_multiplier', 'adx_length', 'adx_threshold'],
  BRS_SCALPING: [
    'bb_length', 'bb_std', 'rsi_length', 'stoch_k', 'stoch_d', 'stoch_smooth',
    'rsi_oversold', 'rsi_overbought', 'stoch_oversold', 'stoch_overbought', 'atr_length',
  ],
  VWAP_PULLBACK: ['atr_length'],
  CHART_AGENT: ['min_confidence', 'use_llm', 'rsi_length', 'macd_fast', 'macd_slow', 'macd_signal', 'atr_length'],
  TICK_MOMENTUM: ['lookback_ticks', 'tick_cooldown_sec'],
  TICK_MEAN_REVERT: ['lookback_ticks', 'tick_cooldown_sec'],
  TICK_BREAKOUT: ['lookback_ticks', 'tick_cooldown_sec'],
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
  if (/^(min_confidence|use_llm)/.test(key)) return 'agent';
  if (/^(lookback_ticks|tick_)/.test(key)) return 'tick';
  if (/^(rsi|macd|atr|bb_|stoch|st_|adx)/.test(key)) return 'indicators';
  return 'other';
}

function getInputType(key, meta) {
  if (meta?.readOnly) return 'readonly';
  if (key === 'tp_mode') return 'select';
  if (meta?.kind === 'boolean') return 'checkbox';
  if (meta?.kind === 'confidence') return 'range';
  if (['percent', 'integer', 'decimal', 'seconds', 'price'].includes(meta?.kind)) return 'number';
  return 'text';
}

function fieldMeta(key) {
  return FIELD_META[key] ?? { label: humanizeKey(key), group: inferGroup(key), kind: 'text' };
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
      draft[f.key] = v ?? 'percent';
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
