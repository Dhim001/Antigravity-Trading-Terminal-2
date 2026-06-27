/**
 * Chart drawing model + geometry.
 *
 * Drawings are stored in DATA coordinates so they stay anchored to price/time
 * across zoom, pan, and timeframe changes:
 *   - point: { time: <unix sec>, price: <number> }
 *   - trendline / rectangle: two points (p1, p2)
 *   - hline: single price (full-width horizontal level)
 *   - fib: two points; levels derived between p1.price and p2.price
 *
 * Conversion to pixel space (for ECharts `graphic` elements) is done by the
 * caller-supplied `convert(point) -> [x, y] | null` so this module stays pure
 * and unit-testable.
 */

export const DRAWING_TOOLS = ['trendline', 'hline', 'rectangle', 'fib'];

export const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];

const DEFAULT_COLOR = '#3b82f6';
const FIB_COLOR = '#a855f7';
const HANDLE_HIT_PX = 8;
const LINE_HIT_PX = 6;

let _idCounter = 0;
function nextId() {
  _idCounter += 1;
  return `dw_${Date.now().toString(36)}_${_idCounter}`;
}

/** Create a drawing in data coordinates. */
export function createDrawing(tool, points, opts = {}) {
  const base = {
    id: opts.id || nextId(),
    tool,
    color: opts.color || (tool === 'fib' ? FIB_COLOR : DEFAULT_COLOR),
    createdAt: opts.createdAt || Date.now(),
  };
  if (tool === 'hline') {
    return { ...base, price: Number(points?.[0]?.price ?? points?.price) };
  }
  return {
    ...base,
    p1: normalizePoint(points?.[0]),
    p2: normalizePoint(points?.[1]),
  };
}

function normalizePoint(p) {
  if (!p) return null;
  return { time: Number(p.time), price: Number(p.price) };
}

/** Validate a drawing has the coordinates its tool requires. */
export function isValidDrawing(d) {
  if (!d || !DRAWING_TOOLS.includes(d.tool)) return false;
  if (d.tool === 'hline') return Number.isFinite(d.price);
  return Boolean(
    d.p1 && d.p2
    && Number.isFinite(d.p1.time) && Number.isFinite(d.p1.price)
    && Number.isFinite(d.p2.time) && Number.isFinite(d.p2.price),
  );
}

/**
 * Fibonacci retracement levels between two prices.
 * Returns [{ ratio, price, label }] ordered by ratio.
 */
export function fibLevels(priceFrom, priceTo, ratios = FIB_RATIOS) {
  const from = Number(priceFrom);
  const to = Number(priceTo);
  if (!Number.isFinite(from) || !Number.isFinite(to)) return [];
  const span = to - from;
  return ratios.map((r) => ({
    ratio: r,
    price: from + span * r,
    label: `${(r * 100).toFixed(1)}%`,
  }));
}

/**
 * Convert drawings to ECharts `graphic` elements.
 * @param {Array} drawings
 * @param {(point:{time:number,price:number}) => [number,number]|null} convert
 * @param {{ width:number, height:number, left:number, right:number, top:number, bottom:number, priceToY:(p:number)=>number|null, selectedId?:string }} ctx
 */
export function drawingsToGraphic(drawings, convert, ctx = {}) {
  if (!Array.isArray(drawings)) return [];
  const elements = [];
  for (const d of drawings) {
    if (!isValidDrawing(d)) continue;
    const selected = d.id === ctx.selectedId;
    if (d.tool === 'trendline') {
      const a = convert(d.p1);
      const b = convert(d.p2);
      if (a && b) elements.push(lineEl(d.id, a, b, d.color, selected));
    } else if (d.tool === 'rectangle') {
      const a = convert(d.p1);
      const b = convert(d.p2);
      if (a && b) elements.push(rectEl(d.id, a, b, d.color, selected));
    } else if (d.tool === 'hline') {
      const y = ctx.priceToY ? ctx.priceToY(d.price) : null;
      if (y != null) {
        const left = ctx.left ?? 0;
        const right = ctx.right ?? (ctx.width ?? 0);
        elements.push(lineEl(d.id, [left, y], [right, y], d.color, selected));
      }
    } else if (d.tool === 'fib') {
      elements.push(...fibEls(d, convert, ctx, selected));
    }
  }
  return elements;
}

function lineEl(id, a, b, color, selected) {
  return {
    type: 'line',
    id,
    shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] },
    style: { stroke: color, lineWidth: selected ? 2.5 : 1.5 },
    z: 50,
    silent: false,
    info: { drawingId: id },
  };
}

function rectEl(id, a, b, color, selected) {
  const x = Math.min(a[0], b[0]);
  const y = Math.min(a[1], b[1]);
  const w = Math.abs(b[0] - a[0]);
  const h = Math.abs(b[1] - a[1]);
  return {
    type: 'rect',
    id,
    shape: { x, y, width: w, height: h },
    style: {
      stroke: color,
      lineWidth: selected ? 2.5 : 1.5,
      fill: hexToRgba(color, 0.08),
    },
    z: 49,
    info: { drawingId: id },
  };
}

function fibEls(d, convert, ctx, selected) {
  const a = convert(d.p1);
  const b = convert(d.p2);
  if (!a || !b) return [];
  const left = Math.min(a[0], b[0]);
  const right = Math.max(a[0], b[0]);
  const levels = fibLevels(d.p1.price, d.p2.price);
  const els = [];
  for (const lvl of levels) {
    const y = ctx.priceToY ? ctx.priceToY(lvl.price) : null;
    if (y == null) continue;
    els.push({
      type: 'line',
      id: `${d.id}:${lvl.ratio}`,
      shape: { x1: left, y1: y, x2: right, y2: y },
      style: { stroke: d.color, lineWidth: selected ? 2 : 1, lineDash: [4, 3] },
      z: 48,
      info: { drawingId: d.id },
    });
    els.push({
      type: 'text',
      id: `${d.id}:${lvl.ratio}:t`,
      style: {
        text: `${lvl.label}  ${formatPrice(lvl.price)}`,
        x: left + 4,
        y: y - 10,
        fill: d.color,
        font: '10px sans-serif',
      },
      z: 48,
      info: { drawingId: d.id },
    });
  }
  return els;
}

/**
 * Hit-test a pixel point against drawings (for selection). Returns the topmost
 * drawing id under the cursor, or null. `convert` and `priceToY` map data→pixel.
 */
export function hitTestDrawings(px, py, drawings, convert, ctx = {}) {
  if (!Array.isArray(drawings)) return null;
  for (let i = drawings.length - 1; i >= 0; i--) {
    const d = drawings[i];
    if (!isValidDrawing(d)) continue;
    if (d.tool === 'trendline') {
      const a = convert(d.p1);
      const b = convert(d.p2);
      if (a && b && distToSegment(px, py, a, b) <= LINE_HIT_PX) return d.id;
    } else if (d.tool === 'hline') {
      const y = ctx.priceToY ? ctx.priceToY(d.price) : null;
      if (y != null && Math.abs(py - y) <= LINE_HIT_PX) return d.id;
    } else if (d.tool === 'rectangle') {
      const a = convert(d.p1);
      const b = convert(d.p2);
      if (a && b && pointNearRect(px, py, a, b)) return d.id;
    } else if (d.tool === 'fib') {
      const a = convert(d.p1);
      const b = convert(d.p2);
      if (a && b && distToSegment(px, py, a, b) <= LINE_HIT_PX) return d.id;
    }
  }
  return null;
}

/**
 * Map a unix-second timestamp to a (possibly fractional) ordinal index within a
 * bar array whose times are ascending. ECharts category axes interpret a numeric
 * `convertToPixel` input as the ORDINAL INDEX (not the category value), so
 * drawings stored in {time, price} must be translated to an index before
 * pixel conversion. Extrapolates linearly outside the bar range.
 *
 * @param {Array<{time:number}>} bars
 * @param {number} t  unix seconds
 * @returns {number|null}
 */
export function timeToFractionalIndex(bars, t) {
  if (!Array.isArray(bars) || bars.length === 0) return null;
  const tt = Number(t);
  if (!Number.isFinite(tt)) return null;
  const n = bars.length;
  if (n === 1) return 0;
  const first = Number(bars[0].time);
  const last = Number(bars[n - 1].time);
  const spacing = (last - first) / (n - 1) || 1;
  if (tt <= first) return (tt - first) / spacing;
  if (tt >= last) return (n - 1) + (tt - last) / spacing;

  let lo = 0;
  let hi = n - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const mt = Number(bars[mid].time);
    if (mt === tt) return mid;
    if (mt < tt) lo = mid + 1;
    else hi = mid - 1;
  }
  const i = Math.max(0, Math.min(n - 2, hi));
  const t0 = Number(bars[i].time);
  const t1 = Number(bars[i + 1].time);
  const frac = t1 > t0 ? (tt - t0) / (t1 - t0) : 0;
  return i + frac;
}

/** Distance from point to a line segment in pixels. */
export function distToSegment(px, py, a, b) {
  const [x1, y1] = a;
  const [x2, y2] = b;
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  if (lenSq === 0) return Math.hypot(px - x1, py - y1);
  let t = ((px - x1) * dx + (py - y1) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  const projX = x1 + t * dx;
  const projY = y1 + t * dy;
  return Math.hypot(px - projX, py - projY);
}

function pointNearRect(px, py, a, b) {
  const x = Math.min(a[0], b[0]);
  const y = Math.min(a[1], b[1]);
  const w = Math.abs(b[0] - a[0]);
  const h = Math.abs(b[1] - a[1]);
  const edges = [
    [[x, y], [x + w, y]],
    [[x + w, y], [x + w, y + h]],
    [[x + w, y + h], [x, y + h]],
    [[x, y + h], [x, y]],
  ];
  return edges.some(([p, q]) => distToSegment(px, py, p, q) <= LINE_HIT_PX);
}

export { HANDLE_HIT_PX, LINE_HIT_PX };

function hexToRgba(hex, alpha) {
  const m = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex || '');
  if (!m) return hex;
  const r = parseInt(m[1], 16);
  const g = parseInt(m[2], 16);
  const b = parseInt(m[3], 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function formatPrice(p) {
  const n = Number(p);
  if (!Number.isFinite(n)) return '';
  if (Math.abs(n) >= 1000) return n.toFixed(0);
  if (Math.abs(n) >= 1) return n.toFixed(2);
  return n.toFixed(4);
}
