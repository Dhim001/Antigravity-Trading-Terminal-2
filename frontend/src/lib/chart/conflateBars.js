/**
 * Display conflation — merge OHLC bars power-of-2 when zoomed out beyond ~1 bar/px
 * (MEMORY_CENTRIC_REVIEW #23). Render cost scales with viewport pixels, not history length.
 */

/** @type {Map<string, { factor: number, bars: object[] }>} */
const _cache = new Map();
const CACHE_MAX = 24;

export function nextPowerOf2(n) {
  const x = Math.max(1, Math.ceil(Number(n) || 1));
  if (x <= 1) return 1;
  let p = 1;
  while (p < x) p <<= 1;
  return p;
}

/**
 * @param {number} barCount
 * @param {number} pixelWidth chart plot width in CSS px
 * @returns {number} merge factor (1 = no conflation)
 */
export function conflationFactor(barCount, pixelWidth) {
  const bars = Math.max(0, Math.floor(Number(barCount) || 0));
  const width = Math.max(1, Math.floor(Number(pixelWidth) || 1));
  if (bars <= width) return 1;
  return nextPowerOf2(Math.ceil(bars / width));
}

/**
 * Merge every `factor` consecutive bars into one OHLC bucket (first open, last close).
 * @param {Array<{time:number,open:number,high:number,low:number,close:number,volume?:number}>} bars
 * @param {number} factor
 */
export function conflateBars(bars, factor) {
  if (!Array.isArray(bars) || bars.length === 0) return bars ?? [];
  const f = Math.max(1, Math.floor(Number(factor) || 1));
  if (f <= 1 || bars.length <= f) return bars;

  const out = [];
  for (let i = 0; i < bars.length; i += f) {
    const end = Math.min(bars.length, i + f);
    const first = bars[i];
    let high = first.high;
    let low = first.low;
    let volume = 0;
    for (let j = i; j < end; j += 1) {
      const b = bars[j];
      if (b.high > high) high = b.high;
      if (b.low < low) low = b.low;
      volume += b.volume || 0;
    }
    const last = bars[end - 1];
    out.push({
      time: first.time,
      open: first.open,
      high,
      low,
      close: last.close,
      volume,
    });
  }
  return out;
}

/**
 * Cached conflation keyed by zoom/history bucket (MEMORY #23).
 * @param {object[]} bars
 * @param {number} pixelWidth
 * @param {string} [cacheKey] e.g. symbol|tf|len|lastTime|zoomBucket
 */
export function conflateForDisplay(bars, pixelWidth, cacheKey = '') {
  const factor = conflationFactor(bars?.length ?? 0, pixelWidth);
  if (factor <= 1) return { bars: bars ?? [], factor: 1 };

  const key = cacheKey
    ? `${cacheKey}|f${factor}|n${bars.length}`
    : `anon|f${factor}|n${bars.length}|t${bars[bars.length - 1]?.time ?? 0}`;

  const hit = _cache.get(key);
  if (hit && hit.factor === factor && hit.bars?.length) {
    return { bars: hit.bars, factor };
  }

  const merged = conflateBars(bars, factor);
  if (_cache.size >= CACHE_MAX) {
    const oldest = _cache.keys().next().value;
    _cache.delete(oldest);
  }
  _cache.set(key, { factor, bars: merged });
  return { bars: merged, factor };
}

/** Visible-window bar estimate from dataZoom percent. */
export function visibleBarCount(totalBars, zoomStart, zoomEnd) {
  const n = Math.max(0, Math.floor(Number(totalBars) || 0));
  if (n === 0) return 0;
  const start = Number.isFinite(zoomStart) ? zoomStart : 0;
  const end = Number.isFinite(zoomEnd) ? zoomEnd : 100;
  const frac = Math.max(0.01, Math.min(1, (end - start) / 100));
  return Math.max(1, Math.ceil(n * frac));
}

/** @internal */
export function clearConflationCacheForTests() {
  _cache.clear();
}
