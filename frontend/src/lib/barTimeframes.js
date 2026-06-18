/** Bar timeframes for bot deploy + backtest (matches ChartWidget TF_CONFIGS labels). */

export const BAR_TIMEFRAMES = ['1m', '5m', '15m', '1H', '4H', '1D'];

export function formatBarTimeframeLabel(tf) {
  const key = String(tf || '1m').toLowerCase();
  const labels = { '1m': '1m', '5m': '5m', '15m': '15m', '1h': '1H', '4h': '4H', '1d': '1D', tick: 'tick' };
  return labels[key] || tf || '1m';
}

export function deployTimeframeSummary(mode, tf) {
  if (mode === 'TICK') return 'tick (sub-minute)';
  return `${formatBarTimeframeLabel(tf)} closed-bar signals`;
}
