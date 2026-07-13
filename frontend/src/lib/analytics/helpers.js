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
  const sign = v < 0 ? '-' : (v > 0 ? '+' : '');
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

/** Approximate inverse normal CDF (Abramowitz & Stegun). */
function invNormApprox(p) {
  const pp = Math.min(Math.max(p, 1e-6), 1 - 1e-6);
  const a = [
    -3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
    1.383577891891766e+02, -3.066479806614716e+01, 2.506628277459239e+00,
  ];
  const b = [
    -5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
    6.680131188771972e+01, -1.328068155288572e+01,
  ];
  const c = [
    -7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
    -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00,
  ];
  const d = [
    7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
    3.754408661907416e+00,
  ];
  const plow = 0.02425;
  const phigh = 1 - plow;
  let q;
  if (pp < plow) {
    q = Math.sqrt(-2 * Math.log(pp));
    return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
      / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  if (pp > phigh) {
    q = Math.sqrt(-2 * Math.log(1 - pp));
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
      / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
  }
  q = pp - 0.5;
  const r = q * q;
  return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q
    / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1);
}

/**
 * Build density + QQ overlays from a numeric return series (e.g. daily portfolio PnL).
 * Used when the API portfolio payload is missing (stale backend) or for local fallback.
 */
export function buildReturnDistribution(values, maxBins = 30) {
  const nums = (values || []).map(Number).filter((v) => Number.isFinite(v));
  const empty = { bins: [], moments: {}, density: [], qq: [], n: 0 };
  if (nums.length < 2) return empty;

  const n = nums.length;
  const sorted = [...nums].sort((a, b) => a - b);
  const mean = nums.reduce((s, v) => s + v, 0) / n;
  const median = n % 2
    ? sorted[Math.floor(n / 2)]
    : 0.5 * (sorted[n / 2 - 1] + sorted[n / 2]);
  const variance = nums.reduce((s, v) => s + (v - mean) ** 2, 0) / (n - 1);
  const std = Math.sqrt(Math.max(variance, 0));
  let skewness = 0;
  let excessKurtosis = 0;
  if (std > 0 && n >= 3) {
    const m3 = nums.reduce((s, v) => s + ((v - mean) / std) ** 3, 0);
    skewness = (n / ((n - 1) * (n - 2))) * m3;
  }
  if (std > 0 && n >= 4) {
    const m4 = nums.reduce((s, v) => s + ((v - mean) / std) ** 4, 0);
    excessKurtosis = (
      (n * (n + 1) / ((n - 1) * (n - 2) * (n - 3))) * m4
      - (3 * (n - 1) ** 2) / ((n - 2) * (n - 3))
    );
  }
  const moments = {
    n,
    mean: +mean.toFixed(4),
    median: +median.toFixed(4),
    std: +std.toFixed(4),
    skewness: +skewness.toFixed(4),
    excess_kurtosis: +excessKurtosis.toFixed(4),
    min: +sorted[0].toFixed(4),
    max: +sorted[n - 1].toFixed(4),
  };

  const lo = sorted[0];
  const hi = sorted[n - 1];
  if (lo === hi) {
    return {
      bins: [{ edge: lo, upper: hi, count: n, is_positive: lo >= 0 }],
      moments,
      density: [],
      qq: [],
      n,
    };
  }

  const q1 = sorted[Math.floor(n / 4)];
  const q3 = sorted[Math.floor((3 * n) / 4)];
  const iqr = q3 - q1;
  let numBins;
  if (iqr > 0) {
    const width = (2 * iqr) / (n ** (1 / 3));
    numBins = Math.min(Math.max(Math.ceil((hi - lo) / width), 5), maxBins);
  } else {
    numBins = Math.min(Math.max(Math.ceil(Math.sqrt(n)), 5), maxBins);
  }
  const binWidth = (hi - lo) / numBins;
  const bins = [];
  for (let i = 0; i < numBins; i += 1) {
    const edge = lo + i * binWidth;
    const upper = edge + binWidth;
    const count = nums.filter((p) => (
      i === numBins - 1 ? p >= edge && p <= upper : p >= edge && p < upper
    )).length;
    bins.push({
      edge: +edge.toFixed(2),
      upper: +upper.toFixed(2),
      count,
      is_positive: (edge + upper) / 2 >= 0,
    });
  }

  const density = bins.map((b) => {
    const width = Math.max(b.upper - b.edge, 1e-12);
    const mid = (b.edge + b.upper) / 2;
    const empirical = (b.count / n) / width;
    let normal = 0;
    if (std > 0) {
      const z = (mid - mean) / std;
      normal = Math.exp(-0.5 * z * z) / (std * Math.sqrt(2 * Math.PI));
    }
    return {
      x: +mid.toFixed(4),
      empirical: +empirical.toFixed(8),
      normal: +normal.toFixed(8),
    };
  });

  const qq = [];
  if (std > 0 && n >= 3) {
    for (let i = 0; i < n; i += 1) {
      let p = (i + 0.375) / (n + 0.25);
      p = Math.min(Math.max(p, 1e-6), 1 - 1e-6);
      qq.push({
        theoretical: +(mean + std * invNormApprox(p)).toFixed(4),
        sample: +sorted[i].toFixed(4),
      });
    }
  }

  return { bins, moments, density, qq, n };
}

/**
 * Prefer API portfolio daily-return overlays; fall back to calendar day PnL.
 */
export function resolvePortfolioReturnDist(distribution, calendarDays) {
  const portfolio = distribution?.portfolio;
  if (portfolio?.density?.length || portfolio?.qq?.length) {
    return portfolio;
  }
  if (portfolio?.moments?.n >= 2 && !portfolio?.density?.length) {
    // Degenerate variance (all days equal) — still expose moments.
    return portfolio;
  }
  const daily = (calendarDays || [])
    .map((d) => Number(d?.pnl))
    .filter((v) => Number.isFinite(v));
  if (daily.length < 2) {
    // Last resort: trade-level overlays from an older API payload.
    if (distribution?.density?.length || distribution?.qq?.length) {
      return {
        ...distribution,
        unit: 'trade_pnl',
        n_days: distribution?.moments?.n || 0,
      };
    }
    return { bins: [], moments: {}, density: [], qq: [], n: 0, unit: 'daily_pnl' };
  }
  return {
    ...buildReturnDistribution(daily),
    unit: 'daily_pnl',
    n_days: daily.length,
  };
}

/** Max absolute P&L for symmetric diverging visualMap. */
export function calendarPnlRange(days) {
  if (!Array.isArray(days) || !days.length) return 1;
  const maxAbs = Math.max(...days.map((d) => Math.abs(Number(d.pnl) || 0)), 1);
  return maxAbs;
}

/** ECharts heatmap cells; optional lower triangle hides redundant upper half. */
export function buildCorrelationHeatmapCells(matrix, { lowerTriangleOnly = true } = {}) {
  if (!Array.isArray(matrix)) return [];
  return matrix.flatMap((row, rowIdx) =>
    row.map((value, colIdx) => {
      if (lowerTriangleOnly && colIdx > rowIdx) return null;
      return [colIdx, rowIdx, value];
    }).filter(Boolean),
  );
}

export function correlationCellSize(symbolCount) {
  if (symbolCount <= 4) return 52;
  if (symbolCount <= 6) return 44;
  if (symbolCount <= 10) return 36;
  return 30;
}

export function correlationAxisFontSize(symbolCount) {
  if (symbolCount <= 4) return 11;
  if (symbolCount <= 8) return 10;
  if (symbolCount <= 12) return 9;
  return 8;
}

export function correlationStrengthLabel(value) {
  const v = Number(value);
  if (Number.isNaN(v)) return 'Unknown';
  const abs = Math.abs(v);
  const sign = v > 0.05 ? 'positive' : v < -0.05 ? 'negative' : 'neutral';
  if (abs >= 0.7) return `Strong ${sign}`;
  if (abs >= 0.4) return `Moderate ${sign}`;
  if (abs >= 0.2) return `Weak ${sign}`;
  return 'Negligible';
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
