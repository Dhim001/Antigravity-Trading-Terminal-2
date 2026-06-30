/**
 * Signed metric values, deltas, and improvement tone for run comparisons.
 */

export const TONE_CLASS = {
  up: 'text-trading-up',
  down: 'text-trading-down',
  neutral: 'text-muted-foreground',
};

/**
 * Merge backtest summary with top-level result fields (matches backend _summary_from_results).
 * Accepts raw results, `{ summary, total_pnl }`, or API run rows `{ summary, results }`.
 * @param {object|null|undefined} resultsOrSummary
 */
export function resolveBacktestSummary(resultsOrSummary) {
  if (!resultsOrSummary) return {};

  const rowSummary = resultsOrSummary.summary;
  const nestedResults = resultsOrSummary.results;
  const payload = nestedResults && typeof nestedResults === 'object'
    ? nestedResults
    : resultsOrSummary;

  const merged = buildSummaryFromPayload(payload);

  if (rowSummary && typeof rowSummary === 'object' && rowSummary !== payload.summary) {
    for (const key of Object.keys(merged)) {
      const fromRow = rowSummary[key];
      if (fromRow != null && fromRow !== '') {
        merged[key] = Number(fromRow);
      }
    }
  }

  return merged;
}

function buildSummaryFromPayload(payload) {
  const hasNested = payload.summary != null && typeof payload === 'object';
  const summary = hasNested ? (payload.summary ?? {}) : payload;
  const root = hasNested ? payload : {};
  return {
    total_pnl: pickMetric(summary, root, 'total_pnl', 'total_pnl'),
    return_pct: pickMetric(summary, root, 'return_pct', 'return_pct'),
    win_rate: pickMetric(summary, root, 'win_rate', 'win_rate'),
    total_trades: pickMetric(summary, root, 'total_trades', 'trade_count'),
    max_drawdown: pickMetric(summary, root, 'max_drawdown', 'max_drawdown'),
    profit_factor: pickMetric(summary, root, 'profit_factor', 'profit_factor'),
    sharpe_ratio: pickMetric(summary, root, 'sharpe_ratio', 'sharpe_ratio'),
    expectancy: pickMetric(summary, root, 'expectancy', 'expectancy'),
  };
}

function pickMetric(summary, root, summaryKey, rootKey = summaryKey) {
  const fromSummary = summary?.[summaryKey];
  if (fromSummary != null && fromSummary !== '') return Number(fromSummary);
  const fromRoot = root?.[rootKey];
  if (fromRoot != null && fromRoot !== '') return Number(fromRoot);
  return null;
}

export function metricValue(summary, key) {
  const v = summary?.[key];
  if (v == null || v === '') return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * @returns {{ delta: number, improved: boolean, worsened: boolean, neutral: boolean } | null}
 */
export function metricDelta(current, baseline, { higherIsBetter = true } = {}) {
  if (current == null || baseline == null) return null;
  const c = Number(current);
  const b = Number(baseline);
  if (!Number.isFinite(c) || !Number.isFinite(b)) return null;
  const delta = c - b;
  const neutral = Math.abs(delta) < 1e-9;
  const improved = higherIsBetter ? delta > 0 : delta < 0;
  const worsened = higherIsBetter ? delta < 0 : delta > 0;
  return { delta, improved, worsened, neutral };
}

/**
 * Format a signed numeric value (keeps minus for negatives).
 */
export function formatSignedValue(value, {
  prefix = '',
  suffix = '',
  decimals = 2,
  showPlus = false,
} = {}) {
  if (value == null) return '—';
  const n = Number(value);
  if (!Number.isFinite(n)) return '—';
  if (Math.abs(n) < 1e-9) return `±0${suffix}`;
  const sign = n < 0 ? '-' : (n > 0 && showPlus ? '+' : '');
  return `${sign}${prefix}${Math.abs(n).toFixed(decimals)}${suffix}`;
}

/**
 * Format current − baseline with improvement-based tone (not raw sign coloring).
 */
export function formatMetricDelta(current, baseline, {
  prefix = '',
  suffix = '',
  decimals = 2,
  higherIsBetter = true,
} = {}) {
  const d = metricDelta(current, baseline, { higherIsBetter });
  if (!d) {
    return { text: '—', tone: 'neutral', delta: null };
  }
  if (d.neutral) {
    return { text: `±0${suffix}`, tone: 'neutral', delta: 0 };
  }
  const text = formatSignedValue(d.delta, { prefix, suffix, decimals, showPlus: true });
  const tone = d.improved ? 'up' : d.worsened ? 'down' : 'neutral';
  return { text, tone, delta: d.delta };
}

export const BACKTEST_COMPARE_METRICS = [
  { key: 'total_pnl', label: 'PnL', prefix: '$', higherIsBetter: true },
  { key: 'return_pct', label: 'Return', suffix: '%', higherIsBetter: true },
  { key: 'win_rate', label: 'Win rate', suffix: '%', higherIsBetter: true },
  { key: 'max_drawdown', label: 'Max DD', suffix: '%', higherIsBetter: false },
  { key: 'profit_factor', label: 'Profit factor', higherIsBetter: true },
  { key: 'sharpe_ratio', label: 'Sharpe', higherIsBetter: true },
];
