/**
 * Chart comparison mode — overlay a second symbol rebased to the primary series.
 *
 * Because two instruments have different absolute prices, the comparison series
 * is rebased to percent change from its first visible value. The primary series
 * can be rebased the same way for an apples-to-apples correlation view, or the
 * comparison can be plotted on a secondary axis.
 */

/** Convert a bar series to percent-change from the first close. */
export function toPercentChangeSeries(bars) {
  if (!Array.isArray(bars) || bars.length === 0) return [];
  const base = Number(bars.find((b) => Number.isFinite(Number(b.close)))?.close);
  if (!Number.isFinite(base) || base === 0) return bars.map(() => null);
  return bars.map((b) => {
    const c = Number(b.close);
    if (!Number.isFinite(c)) return null;
    return ((c - base) / base) * 100;
  });
}

/**
 * Align a comparison series to the primary bars' timeline. Returns one value per
 * primary bar (percent change), carrying the last known comparison value forward
 * across gaps and null before the comparison series starts.
 *
 * @param {Array} primaryBars  primary symbol bars (define the x timeline)
 * @param {Array} compareBars  comparison symbol bars
 * @returns {Array<number|null>} percent-change values aligned to primaryBars
 */
export function alignComparisonSeries(primaryBars, compareBars) {
  if (!Array.isArray(primaryBars) || primaryBars.length === 0) return [];
  if (!Array.isArray(compareBars) || compareBars.length === 0) {
    return primaryBars.map(() => null);
  }

  const pct = toPercentChangeSeries(compareBars);
  const sorted = compareBars
    .map((b, i) => ({ time: Number(b.time), pct: pct[i] }))
    .filter((x) => Number.isFinite(x.time))
    .sort((a, b) => a.time - b.time);

  const out = new Array(primaryBars.length).fill(null);
  let j = 0;
  let lastVal = null;
  for (let i = 0; i < primaryBars.length; i++) {
    const t = Number(primaryBars[i].time);
    while (j < sorted.length && sorted[j].time <= t) {
      lastVal = sorted[j].pct;
      j += 1;
    }
    out[i] = lastVal;
  }
  return out;
}

/** Correlation (Pearson) between two aligned percent-change arrays. */
export function correlation(seriesA, seriesB) {
  if (!Array.isArray(seriesA) || !Array.isArray(seriesB)) return null;
  const pairs = [];
  const n = Math.min(seriesA.length, seriesB.length);
  for (let i = 0; i < n; i++) {
    const a = seriesA[i];
    const b = seriesB[i];
    if (Number.isFinite(a) && Number.isFinite(b)) pairs.push([a, b]);
  }
  if (pairs.length < 2) return null;

  const meanA = pairs.reduce((s, p) => s + p[0], 0) / pairs.length;
  const meanB = pairs.reduce((s, p) => s + p[1], 0) / pairs.length;
  let cov = 0;
  let varA = 0;
  let varB = 0;
  for (const [a, b] of pairs) {
    const da = a - meanA;
    const db = b - meanB;
    cov += da * db;
    varA += da * da;
    varB += db * db;
  }
  if (varA === 0 || varB === 0) return null;
  return cov / Math.sqrt(varA * varB);
}
