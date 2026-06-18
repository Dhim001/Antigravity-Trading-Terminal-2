/** Agent insight cache keys — aligned with backend symbol:timeframe cache. */

const TF_ALIASES = {
  '1H': '1h',
  '4H': '4h',
  '1D': '1d',
  '1min': '1m',
  '5min': '5m',
  '15min': '15m',
};

export function normalizeAnalystTimeframe(tf) {
  if (!tf || tf === 'tick') return '1m';
  const raw = String(tf).trim();
  if (!raw) return '1m';
  const key = TF_ALIASES[raw] || raw.toLowerCase();
  return key;
}

export function agentInsightKey(symbol, timeframe = '1m') {
  const sym = String(symbol || '').toUpperCase();
  const tf = normalizeAnalystTimeframe(timeframe);
  return `${sym}:${tf}`;
}

/** Resolve insight for symbol + timeframe (falls back to legacy symbol-only 1m key). */
export function selectAgentInsight(insights, symbol, timeframe = '1m') {
  if (!insights || !symbol) return null;
  const key = agentInsightKey(symbol, timeframe);
  if (insights[key]) return insights[key];
  if (normalizeAnalystTimeframe(timeframe) === '1m' && insights[symbol]) {
    return insights[symbol];
  }
  return null;
}
