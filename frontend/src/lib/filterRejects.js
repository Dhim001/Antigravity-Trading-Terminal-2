/** Shared labels for CHART_AGENT filter + live signal-reject buckets. */
export const FILTER_REJECT_LABELS = {
  min_score: 'Score',
  trend: 'Trend',
  vol: 'Vol',
  htf: 'HTF',
  confidence: 'Conf',
  calibration: 'Cal',
  other: 'Other',
  none: 'No edge',
  low_confidence: 'Low conf',
  htf_gate: 'HTF gate',
  meta_label: 'Meta-label',
  conformal: 'Conformal',
  regime_gate: 'Regime',
  stacking: 'Stacking',
  llm_firewall: 'LLM firewall',
  filter: 'Filter',
  duplicate: 'Duplicate',
  pretrade_streak: 'Streak',
};

export const FILTER_REJECT_ORDER = [
  'min_score',
  'trend',
  'vol',
  'htf',
  'confidence',
  'calibration',
  'none',
  'low_confidence',
  'htf_gate',
  'meta_label',
  'conformal',
  'regime_gate',
  'stacking',
  'llm_firewall',
  'filter',
  'duplicate',
  'pretrade_streak',
  'other',
];

export function filterRejectTotal(rejects) {
  if (!rejects || typeof rejects !== 'object') return 0;
  return Object.values(rejects).reduce((sum, n) => sum + (Number(n) || 0), 0);
}

export function filterRejectEntries(rejects) {
  if (!rejects) return [];
  const seen = new Set();
  const entries = [];
  for (const key of FILTER_REJECT_ORDER) {
    const n = Number(rejects[key]) || 0;
    if (n > 0) {
      entries.push([key, n]);
      seen.add(key);
    }
  }
  for (const [key, count] of Object.entries(rejects)) {
    if (seen.has(key)) continue;
    const n = Number(count) || 0;
    if (n > 0) entries.push([key, n]);
  }
  return entries;
}
