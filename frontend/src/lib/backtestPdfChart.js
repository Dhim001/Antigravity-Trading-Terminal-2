/**
 * Static SVG charts for backtest PDF export — price candles + trade execution markers.
 */

import { getCandles, chartTimeframeSecs, toUnixSeconds } from '../services/candleBuffer';
import { ensureBacktestChartHistory, normalizeTradingSymbol } from './backtestDisplay';

const MAX_PDF_BARS = 320;
const CHART_WIDTH = 720;
const PRICE_HEIGHT = 240;
const EQUITY_HEIGHT = 140;

function bucketCandles(raw, intervalSecs) {
  const buckets = new Map();
  for (const c of raw) {
    const sec = toUnixSeconds(c.time);
    if (sec == null) continue;
    const t = Math.floor(sec / intervalSecs) * intervalSecs;
    if (!buckets.has(t)) {
      buckets.set(t, {
        time: t,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
        volume: c.volume || 0,
      });
    } else {
      const b = buckets.get(t);
      b.high = Math.max(b.high, c.high);
      b.low = Math.min(b.low, c.low);
      b.close = c.close;
      b.volume += c.volume || 0;
    }
  }
  return Array.from(buckets.values()).sort((a, b) => a.time - b.time);
}

function downsampleCandles(candles, maxBars) {
  if (candles.length <= maxBars) return candles;
  const stride = Math.ceil(candles.length / maxBars);
  const out = [];
  for (let i = 0; i < candles.length; i += stride) out.push(candles[i]);
  const last = candles[candles.length - 1];
  if (out[out.length - 1]?.time !== last.time) out.push(last);
  return out;
}

function findBarIndexForBarTime(candles, barTimeSec, bucketSecs = 60) {
  if (!candles.length) return -1;
  const tsSec = toUnixSeconds(barTimeSec);
  if (tsSec == null) return -1;
  const bucketTs = Math.floor(tsSec / bucketSecs) * bucketSecs;
  const first = toUnixSeconds(candles[0].time);
  const last = toUnixSeconds(candles[candles.length - 1].time);
  if (first != null && bucketTs < first) return -1;
  if (last != null && bucketTs > last) return -1;
  for (let i = 0; i < candles.length; i++) {
    if (toUnixSeconds(candles[i].time) === bucketTs) return i;
  }
  for (let i = candles.length - 1; i >= 0; i--) {
    if (toUnixSeconds(candles[i].time) <= tsSec) return i;
  }
  return -1;
}

function markerYForTrade(candle, side, { isExit = false, fillPrice } = {}) {
  if (isExit) return fillPrice ?? candle?.close;
  if (side === 'BUY') return candle?.low ?? fillPrice;
  if (side === 'SELL') return candle?.high ?? fillPrice;
  return fillPrice ?? candle?.close;
}

function fmtAxisTime(sec) {
  if (sec == null) return '';
  try {
    return new Date(sec * 1000).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}

function buildYScale(values, pad, plotH) {
  let min = Infinity;
  let max = -Infinity;
  for (const v of values) {
    if (v == null || !Number.isFinite(v)) continue;
    min = Math.min(min, v);
    max = Math.max(max, v);
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    min = 0;
    max = 1;
  }
  const span = max - min || Math.max(Math.abs(max), 1) * 0.01;
  const margin = span * 0.06;
  min -= margin;
  max += margin;
  const y = (v) => pad.top + (1 - (v - min) / (max - min)) * plotH;
  return { y, min, max };
}

function tradeMarkerSvg({ x, y, isExit, pnl, side }) {
  if (isExit) {
    const fill = (pnl ?? 0) >= 0 ? '#d97706' : '#dc2626';
    return `<circle cx="${x}" cy="${y}" r="4.5" fill="${fill}" stroke="#1e3a5f" stroke-width="1"/>`
      + `<line x1="${x}" y1="${y - 7}" x2="${x}" y2="${y + 7}" stroke="${fill}" stroke-width="1.5"/>`;
  }
  const tipY = side === 'SELL' ? y - 6 : y + 6;
  const baseY = side === 'SELL' ? y + 4 : y - 4;
  return `<polygon points="${x},${tipY} ${x - 5},${baseY} ${x + 5},${baseY}" fill="#2563eb" stroke="#1e3a5f" stroke-width="1"/>`;
}

/** Shared marker points for panel (ECharts) and PDF (SVG). */
export function buildTradeMarkerPoints(candles, trades, bucketSecs) {
  return (trades ?? []).map((t) => {
    const isExit = t.is_exit === 1 || t.is_exit === true;
    const idx = findBarIndexForBarTime(candles, t.time, bucketSecs);
    if (idx < 0) return null;
    const candle = candles[idx];
    const yPrice = markerYForTrade(candle, t.side, { isExit, fillPrice: t.price });
    return {
      idx,
      yPrice,
      isExit,
      pnl: t.pnl,
      side: t.side,
      price: t.price,
      reason: t.reason,
    };
  }).filter(Boolean);
}

export const BACKTEST_MARKER_COLORS = {
  entry: '#2563eb',
  exitWin: '#d97706',
  exitLoss: '#dc2626',
};

export function buildPriceChartSvg(candles, trades, bucketSecs, {
  width = CHART_WIDTH,
  height = PRICE_HEIGHT,
} = {}) {
  if (!candles?.length) return '';

  const pad = { top: 14, right: 14, bottom: 28, left: 52 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const barW = plotW / candles.length;

  const priceVals = [];
  for (const c of candles) {
    priceVals.push(c.low, c.high, c.open, c.close);
  }
  for (const t of trades ?? []) {
    if (t.price != null) priceVals.push(Number(t.price));
  }
  const { y } = buildYScale(priceVals, pad, plotH);

  const candleEls = candles.map((c, i) => {
    const cx = pad.left + i * barW + barW / 2;
    const bull = c.close >= c.open;
    const color = bull ? '#16a34a' : '#dc2626';
    const bodyTop = Math.min(y(c.open), y(c.close));
    const bodyH = Math.max(1, Math.abs(y(c.close) - y(c.open)));
    const wick = `<line x1="${cx}" y1="${y(c.high)}" x2="${cx}" y2="${y(c.low)}" stroke="${color}" stroke-width="1"/>`;
    const bodyW = Math.max(1.2, barW * 0.62);
    const body = `<rect x="${cx - bodyW / 2}" y="${bodyTop}" width="${bodyW}" height="${bodyH}" fill="${color}" rx="0.4"/>`;
    return wick + body;
  }).join('');

  const markerEls = buildTradeMarkerPoints(candles, trades, bucketSecs).map((m) => {
    const x = pad.left + m.idx * barW + barW / 2;
    return tradeMarkerSvg({
      x,
      y: y(m.yPrice),
      isExit: m.isExit,
      pnl: m.pnl,
      side: m.side,
    });
  }).join('');

  const xLabels = [
    { i: 0, label: fmtAxisTime(toUnixSeconds(candles[0].time)) },
    { i: Math.floor(candles.length / 2), label: fmtAxisTime(toUnixSeconds(candles[Math.floor(candles.length / 2)]?.time)) },
    { i: candles.length - 1, label: fmtAxisTime(toUnixSeconds(candles[candles.length - 1].time)) },
  ].map(({ i, label }) => {
    const x = pad.left + i * barW + barW / 2;
    return `<text x="${x}" y="${height - 8}" text-anchor="middle" font-size="9" fill="#666">${label}</text>`;
  }).join('');

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="Backtest price chart with trade markers">
    <rect x="0" y="0" width="${width}" height="${height}" fill="#fafafa" rx="4"/>
    <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="#fff" stroke="#e5e7eb"/>
    ${candleEls}
    ${markerEls}
    ${xLabels}
  </svg>`;
}

export function buildEquityChartSvg(equityCurve, trades, {
  width = CHART_WIDTH,
  height = EQUITY_HEIGHT,
} = {}) {
  if (!equityCurve?.length) return '';

  const pad = { top: 12, right: 14, bottom: 24, left: 52 };
  const plotW = width - pad.left - pad.right;
  const plotH = height - pad.top - pad.bottom;
  const n = equityCurve.length;
  const xAt = (i) => pad.left + (i / Math.max(n - 1, 1)) * plotW;

  const equities = equityCurve.map((p) => p.equity);
  const { y } = buildYScale(equities, pad, plotH);

  const pathPts = equityCurve.map((p, i) => `${xAt(i)},${y(p.equity)}`).join(' ');
  const line = `<polyline points="${pathPts}" fill="none" stroke="#2563eb" stroke-width="1.8"/>`;

  const markers = (trades ?? []).map((t) => {
    const isExit = t.is_exit === 1 || t.is_exit === true;
    const ts = toUnixSeconds(t.time);
    if (ts == null) return '';
    let best = 0;
    let bestDiff = Math.abs(toUnixSeconds(equityCurve[0].time) - ts);
    for (let i = 1; i < equityCurve.length; i++) {
      const diff = Math.abs(toUnixSeconds(equityCurve[i].time) - ts);
      if (diff < bestDiff) {
        bestDiff = diff;
        best = i;
      }
    }
    const eq = equityCurve[best]?.equity;
    if (eq == null) return '';
    return tradeMarkerSvg({ x: xAt(best), y: y(eq), isExit, pnl: t.pnl });
  }).join('');

  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" width="100%" role="img" aria-label="Equity curve with trade markers">
    <rect x="0" y="0" width="${width}" height="${height}" fill="#fafafa" rx="4"/>
    <rect x="${pad.left}" y="${pad.top}" width="${plotW}" height="${plotH}" fill="#fff" stroke="#e5e7eb"/>
    ${line}
    ${markers}
  </svg>`;
}

export async function resolveBacktestPdfCandles(symbol, meta, timeframe) {
  const sym = normalizeTradingSymbol(symbol);
  if (!sym || !meta?.oldest) return { candles: [], bucketSecs: 60 };

  await ensureBacktestChartHistory(sym, meta);

  const bucketSecs = chartTimeframeSecs(timeframe || meta?.timeframe || '1m');
  const raw = getCandles(sym, '1m', 60);
  const oldest = meta.oldest;
  const newest = meta.newest;

  let filtered = raw.filter((c) => {
    const t = toUnixSeconds(c.time);
    return t != null && t >= oldest && (newest == null || t <= newest);
  });
  if (!filtered.length) {
    filtered = raw.slice(-Math.min(raw.length, MAX_PDF_BARS * 2));
  }

  let candles = bucketSecs === 60 ? filtered : bucketCandles(filtered, bucketSecs);
  candles = downsampleCandles(candles, MAX_PDF_BARS);
  return { candles, bucketSecs };
}

export function buildChartSectionHtml({
  candles,
  trades,
  equityCurve,
  bucketSecs,
  symbol,
  timeframe,
  escHtml = (s) => String(s ?? ''),
}) {
  const priceSvg = buildPriceChartSvg(candles, trades, bucketSecs);
  const equitySvg = equityCurve?.length
    ? buildEquityChartSvg(equityCurve, trades)
    : '';

  if (!priceSvg && !equitySvg) return '';

  const sym = escHtml(symbol);
  const tf = escHtml(timeframe || '1m');

  const legend = `
    <div class="chart-legend">
      <span><i class="lg lg-entry"></i> Entry</span>
      <span><i class="lg lg-exit-win"></i> Exit (win)</span>
      <span><i class="lg lg-exit-loss"></i> Exit (loss)</span>
    </div>`;

  const priceBlock = priceSvg ? `
    <h3>Price &amp; trade execution — ${sym} · ${tf}</h3>
    ${legend}
    <div class="chart-wrap">${priceSvg}</div>
  ` : '';

  const equityBlock = equitySvg ? `
    <h3>Equity curve</h3>
    <div class="chart-wrap chart-wrap--equity">${equitySvg}</div>
  ` : '';

  return `<section class="charts">${priceBlock}${equityBlock}</section>`;
}
