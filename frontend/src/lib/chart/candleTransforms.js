/**
 * Candle type transforms — Heikin-Ashi and Renko.
 *
 * All transforms accept and return arrays of bars shaped like
 * { time, open, high, low, close, volume } so the result can flow through the
 * existing candlestick rendering pipeline unchanged.
 */

/** Chart types that render as candlesticks with transformed OHLC data. */
export const CANDLE_CHART_TYPES = new Set(['candle', 'heikin', 'renko']);

export function isCandleChartType(chartType) {
  return CANDLE_CHART_TYPES.has(chartType);
}

/**
 * Heikin-Ashi transform.
 * HA close = avg(O,H,L,C); HA open = avg(prev HA open, prev HA close);
 * HA high/low extend to include the HA open/close.
 * Volume and time are preserved from the source bar.
 */
export function toHeikinAshi(bars) {
  if (!Array.isArray(bars) || bars.length === 0) return [];
  const out = new Array(bars.length);
  let prevOpen;
  let prevClose;
  for (let i = 0; i < bars.length; i++) {
    const ha = heikinAshiBar(bars[i], prevOpen, prevClose, i === 0);
    out[i] = ha;
    prevOpen = ha.open;
    prevClose = ha.close;
  }
  return out;
}

/**
 * Single-bar Heikin-Ashi. When `isFirst`, open = avg(O,C); else avg(prev HA open/close).
 */
export function heikinAshiBar(sourceBar, prevHaOpen, prevHaClose, isFirst = false) {
  const o = Number(sourceBar.open);
  const h = Number(sourceBar.high);
  const l = Number(sourceBar.low);
  const cl = Number(sourceBar.close);
  const haClose = (o + h + l + cl) / 4;
  const haOpen = isFirst || prevHaOpen == null || prevHaClose == null
    ? (o + cl) / 2
    : (prevHaOpen + prevHaClose) / 2;
  return {
    ...sourceBar,
    open: haOpen,
    high: Math.max(h, haOpen, haClose),
    low: Math.min(l, haOpen, haClose),
    close: haClose,
  };
}

/**
 * Estimate a sensible Renko brick size from recent price range when the user
 * has not specified one. Uses ~0.5% of the latest close, floored to avoid
 * zero-size bricks on tiny-priced assets.
 */
export function estimateRenkoBrickSize(bars) {
  if (!Array.isArray(bars) || bars.length === 0) return 0;
  const last = Number(bars[bars.length - 1]?.close) || 0;
  if (last <= 0) return 0;
  const raw = last * 0.005;
  // Round to 2 significant figures for a clean brick size.
  const mag = Math.pow(10, Math.floor(Math.log10(raw)) - 1);
  return Math.max(mag, Math.round(raw / mag) * mag);
}

/**
 * Time-aligned Renko transform.
 *
 * Classic Renko discards time; to remain compatible with the terminal's shared
 * category (time) x-axis, this produces at most one synthesized brick per source
 * bar that keys to the source bar's time. Each output brick reflects the net
 * brick direction achieved by that bar's close. Bars that do not move a full
 * brick from the last brick close are skipped (no new brick), which keeps the
 * series aligned to meaningful moves while preserving chronological order.
 *
 * @param {Array} bars
 * @param {number} [brickSize] absolute price per brick; auto-estimated if omitted
 */
export function toRenko(bars, brickSize) {
  if (!Array.isArray(bars) || bars.length === 0) return [];
  const size = brickSize && brickSize > 0 ? brickSize : estimateRenkoBrickSize(bars);
  if (!size || size <= 0) return bars.map((c) => ({ ...c }));

  const out = [];
  let anchor = Number(bars[0].close); // last brick close
  for (let i = 0; i < bars.length; i++) {
    const c = bars[i];
    const close = Number(c.close);
    const diff = close - anchor;
    const steps = Math.trunc(diff / size);
    if (steps === 0) continue;

    const dir = steps > 0 ? 1 : -1;
    const brickOpen = anchor;
    const brickClose = anchor + steps * size;
    out.push({
      time: c.time,
      open: brickOpen,
      close: brickClose,
      high: Math.max(brickOpen, brickClose),
      low: Math.min(brickOpen, brickClose),
      volume: c.volume || 0,
      brickDir: dir,
      brickCount: Math.abs(steps),
    });
    anchor = brickClose;
  }
  return out;
}

/**
 * Index-aligned Renko for rendering on a shared time (category) x-axis.
 *
 * Returns exactly one output bar per source bar so it lines up with the
 * terminal's category axis. Each output bar is a candlestick spanning the brick
 * move achieved at that source bar: open = previous brick close, close = new
 * brick close. Bars with no full-brick move are flat (open === close) and carry
 * the last brick close forward.
 */
export function toRenkoAligned(bars, brickSize) {
  if (!Array.isArray(bars) || bars.length === 0) return [];
  const size = brickSize && brickSize > 0 ? brickSize : estimateRenkoBrickSize(bars);
  if (!size || size <= 0) return bars.map((c) => ({ ...c }));

  const out = new Array(bars.length);
  let anchor = Number(bars[0].close);
  for (let i = 0; i < bars.length; i++) {
    const brick = renkoAlignedBar(bars[i], anchor, size);
    out[i] = brick;
    anchor = brick.close;
  }
  return out;
}

/**
 * Single index-aligned Renko bar from a fixed brick open (anchor) + source close.
 */
export function renkoAlignedBar(sourceBar, anchorClose, brickSize) {
  const size = brickSize && brickSize > 0 ? brickSize : 0;
  const close = Number(sourceBar.close);
  const open = Number(anchorClose);
  if (!size || size <= 0) {
    return {
      time: sourceBar.time,
      open: Number(sourceBar.open),
      close,
      high: Number(sourceBar.high),
      low: Number(sourceBar.low),
      volume: sourceBar.volume || 0,
      brickDir: 0,
      brickCount: 0,
    };
  }
  const steps = Math.trunc((close - open) / size);
  const newClose = steps !== 0 ? open + steps * size : open;
  return {
    time: sourceBar.time,
    open,
    close: newClose,
    high: Math.max(open, newClose),
    low: Math.min(open, newClose),
    volume: sourceBar.volume || 0,
    brickDir: steps === 0 ? 0 : (steps > 0 ? 1 : -1),
    brickCount: Math.abs(steps),
  };
}

/**
 * Patch the last transformed main OHLC slot for HA/Renko forming-bar updates.
 * `cache.main[idx]` open is treated as fixed while the source bar is forming.
 * @returns {boolean} true if patched
 */
export function patchLastTransformedMain(cache, rawBar, chartType, idx, { renkoBrickSize } = {}) {
  if (!cache?.main || !rawBar || idx < 0 || idx >= cache.main.length) return false;
  let transformed;
  if (chartType === 'heikin') {
    const prev = idx > 0 ? cache.main[idx - 1] : null;
    const prevOpen = Array.isArray(prev) ? prev[0] : undefined;
    const prevClose = Array.isArray(prev) ? prev[1] : undefined;
    transformed = heikinAshiBar(rawBar, prevOpen, prevClose, idx === 0);
  } else if (chartType === 'renko') {
    const slot = cache.main[idx];
    const fixedOpen = Array.isArray(slot) ? slot[0] : Number(rawBar.open);
    transformed = renkoAlignedBar(rawBar, fixedOpen, renkoBrickSize);
  } else {
    return false;
  }
  const prev = cache.main[idx];
  if (Array.isArray(prev) && prev.length === 4) {
    prev[0] = transformed.open;
    prev[1] = transformed.close;
    prev[2] = transformed.low;
    prev[3] = transformed.high;
  } else {
    cache.main[idx] = [transformed.open, transformed.close, transformed.low, transformed.high];
  }
  return true;
}

/**
 * Apply the candle transform matching the chart type. Returns the source bars
 * unchanged for 'candle' and 'line'. Renko uses the index-aligned variant so it
 * renders on the shared category x-axis.
 */
export function applyCandleTransform(bars, chartType, opts = {}) {
  if (chartType === 'heikin') return toHeikinAshi(bars);
  if (chartType === 'renko') return toRenkoAligned(bars, opts.renkoBrickSize);
  return bars;
}
