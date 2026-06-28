/**
 * Pure analytics helpers — unit-testable without DOM.
 */

export const ANALYTICS_PERIODS = [
  { label: '1D', days: 1 },
  { label: '1W', days: 7 },
  { label: '1M', days: 30 },
  { label: 'ALL', days: Infinity },
];

export function fmtUsd(n, d = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  const v = Number(n);
  const sign = v > 0 ? '+' : '';
  return `${sign}$${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`;
}

export function fmtPct(n, d = 1) {
  if (n == null || Number.isNaN(Number(n))) return '—';
  return `${Number(n).toFixed(d)}%`;
}

export function pnlTone(n) {
  if (n > 0) return 'up';
  if (n < 0) return 'down';
  return 'neutral';
}

/** Sort breakdown rows by field with asc/desc/clear cycle. */
export function nextSortState(sort, field) {
  if (sort.field !== field) return { field, dir: 'desc' };
  if (sort.dir === 'desc') return { field, dir: 'asc' };
  return { field: null, dir: null };
}

export function sortBreakdownRows(rows, sort) {
  if (!sort?.field || !sort?.dir) return rows;
  const dir = sort.dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const av = a[sort.field] ?? 0;
    const bv = b[sort.field] ?? 0;
    if (typeof av === 'string') return dir * av.localeCompare(bv);
    return dir * (av - bv);
  });
}

/**
 * Fingerprint for portfolio dashboard auto-refresh — changes when live portfolio
 * data or the user's asset universe (symbolsList) updates.
 */
export function buildPortfolioInvalidateKey({
  tradeHistory = [],
  tradeStats,
  positions = {},
  activeBots = [],
  symbolsList = [],
  settingsUpdatedAt = '',
} = {}) {
  const last = tradeHistory[tradeHistory.length - 1];
  const posSig = Object.keys(positions)
    .sort()
    .map((sym) => {
      const p = positions[sym];
      return `${sym}:${p?.size ?? 0}`;
    })
    .join('|');
  const botsSig = [...activeBots]
    .map((b) => `${b.id}:${b.status}`)
    .sort()
    .join('|');
  return [
    tradeHistory.length,
    last?.timestamp ?? 0,
    tradeStats?.total_pnl ?? 0,
    tradeStats?.total_sells ?? 0,
    posSig,
    botsSig,
    [...symbolsList].sort().join(','),
    settingsUpdatedAt,
  ].join('::');
}

/** Map daily P&L to ECharts calendar heatmap cells [[date, value], ...]. */
export function calendarHeatmapData(days) {
  if (!Array.isArray(days)) return [];
  return days.map((d) => [d.date, d.pnl]);
}

/** Max absolute P&L for symmetric diverging visualMap. */
export function calendarPnlRange(days) {
  if (!Array.isArray(days) || !days.length) return 1;
  const maxAbs = Math.max(...days.map((d) => Math.abs(Number(d.pnl) || 0)), 1);
  return maxAbs;
}

/** Rebase equity + benchmark series to aligned percent-from-start overlay. */
export function alignBenchmarkOverlay(equitySeries, benchmarkSeries) {
  if (!equitySeries?.length || !benchmarkSeries?.length) return [];
  const eqBase = equitySeries[0]?.value ?? 0;
  const bmBase = benchmarkSeries[0]?.value ?? 0;
  const len = Math.min(equitySeries.length, benchmarkSeries.length);
  const out = [];
  for (let i = 0; i < len; i++) {
    const eq = equitySeries[i];
    const bm = benchmarkSeries[i];
    out.push({
      time: eq.time ?? bm.time,
      equity_pct: eqBase !== 0 ? ((eq.value - eqBase) / Math.abs(eqBase)) * 100 : eq.value,
      benchmark_pct: bm.value - bmBase,
    });
  }
  return out;
}

/** Compress screenshot data-URL client-side before upload (journal). */
export async function compressScreenshotDataUrl(dataUrl, maxDim = 1280, quality = 0.72) {
  if (!dataUrl?.startsWith('data:image')) return dataUrl;
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      let { width, height } = img;
      if (width <= maxDim && height <= maxDim) {
        resolve(dataUrl);
        return;
      }
      const scale = maxDim / Math.max(width, height);
      width = Math.round(width * scale);
      height = Math.round(height * scale);
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, width, height);
      resolve(canvas.toDataURL('image/jpeg', quality));
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
}
