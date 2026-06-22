/** Shared labels for CHART_AGENT filter-reject buckets. */
export const FILTER_REJECT_LABELS = {
  min_score: 'Score',
  trend: 'Trend',
  vol: 'Vol',
  htf: 'HTF',
  confidence: 'Conf',
  calibration: 'Cal',
  other: 'Other',
};

export const FILTER_REJECT_ORDER = [
  'min_score',
  'trend',
  'vol',
  'htf',
  'confidence',
  'calibration',
  'other',
];

export function filterRejectTotal(rejects) {
  if (!rejects || typeof rejects !== 'object') return 0;
  return Object.values(rejects).reduce((sum, n) => sum + (Number(n) || 0), 0);
}

export function filterRejectEntries(rejects) {
  if (!rejects) return [];
  return FILTER_REJECT_ORDER
    .map((key) => [key, rejects[key] || 0])
    .filter(([, count]) => count > 0);
}
