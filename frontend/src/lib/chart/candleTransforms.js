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
    const c = bars[i];
    const o = Number(c.open);
    const h = Number(c.high);
    const l = Number(c.low);
    const cl = Number(c.close);
    const haClose = (o + h + l + cl) / 4;
    const haOpen = i === 0 ? (o + cl) / 2 : (prevOpen + prevClose) / 2;
    const haHigh = Math.max(h, haOpen, haClose);
    const haLow = Math.min(l, haOpen, haClose);
    out[i] = {
      ...c,
      open: haOpen,
      high: haHigh,
      low: haLow,
      close: haClose,
    };
    prevOpen = haOpen;
    prevClose = haClose;
  }
  return out;
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
    const c = bars[i];
    const close = Number(c.close);
    const steps = Math.trunc((close - anchor) / size);
    const open = anchor;
    const newClose = steps !== 0 ? anchor + steps * size : anchor;
    out[i] = {
      time: c.time,
      open,
      close: newClose,
      high: Math.max(open, newClose),
      low: Math.min(open, newClose),
      volume: c.volume || 0,
      brickDir: steps === 0 ? 0 : (steps > 0 ? 1 : -1),
      brickCount: Math.abs(steps),
    };
    anchor = newClose;
  }
  return out;
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
