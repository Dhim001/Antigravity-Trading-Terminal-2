/** Bar timeframes for bot deploy + backtest (matches ChartWidget TF_CONFIGS labels). */

export const BAR_TIMEFRAMES = ['1m', '5m', '15m', '1H', '4H', '1D'];

const CANONICAL_TF = {
  '1m': '1m',
  '5m': '5m',
  '15m': '15m',
  '1h': '1h',
  '4h': '4h',
  '1d': '1d',
};

const TF_ALIASES = {
  '1min': '1m',
  '5min': '5m',
  '15min': '15m',
  '1hour': '1h',
  '4hour': '4h',
  '1day': '1d',
};

/** Validate/normalize confirm_timeframe for bot config saves. */
export function normalizeConfirmTimeframe(value) {
  const raw = String(value ?? '').trim();
  if (!raw) return { ok: true, value: '' };

  const candidates = [raw.toLowerCase()];
  if (/^\d+$/.test(raw)) candidates.push(`${raw}m`);

  for (const candidate of candidates) {
    const aliased = TF_ALIASES[candidate] || candidate;
    const canonical = CANONICAL_TF[aliased];
    if (canonical) return { ok: true, value: canonical };
  }

  const hint = /^\d+$/.test(raw) ? ` Did you mean "${raw}m"?` : '';
  return {
    ok: false,
    error: `Invalid confirm timeframe "${raw}".${hint} Use ${BAR_TIMEFRAMES.join(', ')} or leave empty.`,
  };
}

export function formatBarTimeframeLabel(tf) {
  const key = String(tf || '1m').toLowerCase();
  const labels = { '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1H', '4h': '4H', '1d': '1D', tick: 'tick' };
  return labels[key] || tf || '1m';
}

export function deployTimeframeSummary(mode, tf) {
  if (mode === 'TICK') return 'tick (sub-minute)';
  return `${formatBarTimeframeLabel(tf)} closed-bar signals`;
}
