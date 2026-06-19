/**
 * Technical Indicator Engine
 * Pure math functions — all operate on arrays of OHLCV candle objects.
 * Each function returns an array of { time, value } points for lightweight-charts.
 */

import { toUnixSeconds } from '../services/candleBuffer';

/** UTC day bucket — TradingView-style session VWAP resets each calendar day. */
export function vwapSessionKey(timeSec) {
  return Math.floor(timeSec / 86400);
}

// ─── Simple Moving Average ────────────────────────────────────────────────────
export function calcSMA(candles, period) {
  const result = [];
  for (let i = period - 1; i < candles.length; i++) {
    const slice = candles.slice(i - period + 1, i + 1);
    const avg = slice.reduce((s, c) => s + c.close, 0) / period;
    result.push({ time: candles[i].time, value: parseFloat(avg.toFixed(6)) });
  }
  return result;
}

// ─── Exponential Moving Average ───────────────────────────────────────────────
export function calcEMA(candles, period) {
  if (candles.length < period) return [];
  const k = 2 / (period + 1);
  const result = [];
  let ema = candles.slice(0, period).reduce((s, c) => s + c.close, 0) / period;
  result.push({ time: candles[period - 1].time, value: parseFloat(ema.toFixed(6)) });
  for (let i = period; i < candles.length; i++) {
    ema = candles[i].close * k + ema * (1 - k);
    result.push({ time: candles[i].time, value: parseFloat(ema.toFixed(6)) });
  }
  return result;
}

// ─── Bollinger Bands ──────────────────────────────────────────────────────────
export function calcBollingerBands(candles, period = 20, multiplier = 2) {
  const upper = [], middle = [], lower = [];
  for (let i = period - 1; i < candles.length; i++) {
    const slice = candles.slice(i - period + 1, i + 1);
    const avg = slice.reduce((s, c) => s + c.close, 0) / period;
    const variance = slice.reduce((s, c) => s + Math.pow(c.close - avg, 2), 0) / period;
    const stdDev = Math.sqrt(variance);
    const t = candles[i].time;
    upper.push({ time: t, value: parseFloat((avg + multiplier * stdDev).toFixed(6)) });
    middle.push({ time: t, value: parseFloat(avg.toFixed(6)) });
    lower.push({ time: t, value: parseFloat((avg - multiplier * stdDev).toFixed(6)) });
  }
  return { upper, middle, lower };
}

// ─── RSI (Relative Strength Index) ───────────────────────────────────────────
export function calcRSI(candles, period = 14) {
  if (candles.length < period + 1) return [];
  const result = [];
  let gains = 0, losses = 0;

  for (let i = 1; i <= period; i++) {
    const delta = candles[i].close - candles[i - 1].close;
    if (delta >= 0) gains += delta;
    else losses -= delta;
  }

  let avgGain = gains / period;
  let avgLoss = losses / period;

  const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
  result.push({ time: candles[period].time, value: parseFloat((100 - 100 / (1 + rs)).toFixed(2)) });

  for (let i = period + 1; i < candles.length; i++) {
    const delta = candles[i].close - candles[i - 1].close;
    const gain = delta > 0 ? delta : 0;
    const loss = delta < 0 ? -delta : 0;
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    const rs2 = avgLoss === 0 ? 100 : avgGain / avgLoss;
    result.push({ time: candles[i].time, value: parseFloat((100 - 100 / (1 + rs2)).toFixed(2)) });
  }
  return result;
}

// ─── MACD (Moving Average Convergence Divergence) ────────────────────────────
export function calcMACD(candles, fastPeriod = 12, slowPeriod = 26, signalPeriod = 9) {
  if (candles.length < slowPeriod + signalPeriod) return { macdLine: [], signalLine: [], histogram: [] };

  const fastEMA = calcEMA(candles, fastPeriod);
  const slowEMA = calcEMA(candles, slowPeriod);

  // Align by time — find intersection
  const slowTimeSet = new Map(slowEMA.map(p => [p.time, p.value]));
  const fastTimeMap = new Map(fastEMA.map(p => [p.time, p.value]));

  const macdRaw = [];
  for (const [t, sv] of slowTimeSet.entries()) {
    if (fastTimeMap.has(t)) {
      macdRaw.push({ time: t, value: parseFloat((fastTimeMap.get(t) - sv).toFixed(6)) });
    }
  }
  macdRaw.sort((a, b) => a.time - b.time);

  // Signal line = EMA of MACD values
  if (macdRaw.length < signalPeriod) return { macdLine: [], signalLine: [], histogram: [] };

  const k = 2 / (signalPeriod + 1);
  let signalEma = macdRaw.slice(0, signalPeriod).reduce((s, p) => s + p.value, 0) / signalPeriod;
  const signalLine = [{ time: macdRaw[signalPeriod - 1].time, value: parseFloat(signalEma.toFixed(6)) }];

  for (let i = signalPeriod; i < macdRaw.length; i++) {
    signalEma = macdRaw[i].value * k + signalEma * (1 - k);
    signalLine.push({ time: macdRaw[i].time, value: parseFloat(signalEma.toFixed(6)) });
  }

  // Histogram
  const sigMap = new Map(signalLine.map(p => [p.time, p.value]));
  const histogram = macdRaw
    .filter(p => sigMap.has(p.time))
    .map(p => ({
      time: p.time,
      value: parseFloat((p.value - sigMap.get(p.time)).toFixed(6)),
    }));

  const macdLine = macdRaw.filter(p => sigMap.has(p.time));

  return { macdLine, signalLine, histogram };
}

// ─── VWAP (Volume Weighted Average Price) ────────────────────────────────────
/** Session VWAP — resets at each UTC day (matches TradingView default for 24h markets). */
export function calcVWAP(candles) {
  const result = [];
  let cumTPV = 0;
  let cumVol = 0;
  let sessionKey = null;
  let sessionVwap = null;

  for (const c of candles) {
    const sec = toUnixSeconds(c.time);
    if (sec == null) {
      result.push({ time: c.time, value: null });
      continue;
    }

    const dayKey = vwapSessionKey(sec);
    if (dayKey !== sessionKey) {
      cumTPV = 0;
      cumVol = 0;
      sessionKey = dayKey;
      sessionVwap = null;
    }

    const typicalPrice = (c.high + c.low + c.close) / 3;
    const vol = Number(c.volume) || 0;
    if (vol > 0) {
      cumTPV += typicalPrice * vol;
      cumVol += vol;
      sessionVwap = parseFloat((cumTPV / cumVol).toFixed(6));
    } else if (sessionVwap == null) {
      // No volume yet this session — anchor to typical price so the line does not gap
      sessionVwap = parseFloat(typicalPrice.toFixed(6));
    }

    result.push({ time: c.time, value: sessionVwap });
  }
  return result;
}

/** Bar-aligned VWAP values (one per candle, no session-break nulls). */
export function buildVwapSeriesValues(candles) {
  return calcVWAP(candles).map((p) => p.value);
}

// ─── ATR (Average True Range) ─────────────────────────────────────────────────
export function calcATR(candles, period = 14) {
  if (candles.length < period + 1) return [];
  const trs = [];
  for (let i = 1; i < candles.length; i++) {
    const high = candles[i].high;
    const low = candles[i].low;
    const prevClose = candles[i - 1].close;
    trs.push(Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose)));
  }
  let atr = trs.slice(0, period).reduce((s, v) => s + v, 0) / period;
  const result = [{ time: candles[period].time, value: parseFloat(atr.toFixed(6)) }];
  for (let i = period; i < trs.length; i++) {
    atr = (atr * (period - 1) + trs[i]) / period;
    result.push({ time: candles[i + 1].time, value: parseFloat(atr.toFixed(6)) });
  }
  return result;
}

// ─── Signal Engine ────────────────────────────────────────────────────────────
/**
 * Generates a composite trading signal (STRONG BUY / BUY / NEUTRAL / SELL / STRONG SELL)
 * based on current RSI, MACD, and EMA alignment.
 */
export function generateSignal(candles) {
  if (!candles || candles.length < 30) return { signal: 'NEUTRAL', score: 0, reasons: [] };

  const ema9 = calcEMA(candles, 9);
  const ema21 = calcEMA(candles, 21);
  const ema50 = calcEMA(candles, 50);
  const rsi = calcRSI(candles, 14);
  const { macdLine, signalLine } = calcMACD(candles, 12, 26, 9);

  let score = 0;
  const reasons = [];

  // RSI signals
  const lastRsi = rsi[rsi.length - 1]?.value;
  if (lastRsi !== undefined) {
    if (lastRsi < 30) { score += 2; reasons.push(`RSI oversold (${lastRsi.toFixed(1)})`); }
    else if (lastRsi < 45) { score += 1; reasons.push(`RSI bullish zone (${lastRsi.toFixed(1)})`); }
    else if (lastRsi > 70) { score -= 2; reasons.push(`RSI overbought (${lastRsi.toFixed(1)})`); }
    else if (lastRsi > 55) { score -= 1; reasons.push(`RSI bearish zone (${lastRsi.toFixed(1)})`); }
    else reasons.push(`RSI neutral (${lastRsi.toFixed(1)})`);
  }

  // MACD crossover signal
  const lastMACD = macdLine[macdLine.length - 1]?.value;
  const lastSignal = signalLine[signalLine.length - 1]?.value;
  const prevMACD = macdLine[macdLine.length - 2]?.value;
  const prevSignal = signalLine[signalLine.length - 2]?.value;
  if (lastMACD !== undefined && lastSignal !== undefined) {
    if (lastMACD > lastSignal && prevMACD <= prevSignal) { score += 2; reasons.push('MACD bullish crossover'); }
    else if (lastMACD < lastSignal && prevMACD >= prevSignal) { score -= 2; reasons.push('MACD bearish crossover'); }
    else if (lastMACD > lastSignal) { score += 1; reasons.push('MACD above signal'); }
    else if (lastMACD < lastSignal) { score -= 1; reasons.push('MACD below signal'); }
  }

  // EMA alignment (trend)
  const lastE9 = ema9[ema9.length - 1]?.value;
  const lastE21 = ema21[ema21.length - 1]?.value;
  const lastE50 = ema50[ema50.length - 1]?.value;
  const price = candles[candles.length - 1].close;
  if (lastE9 && lastE21 && lastE50) {
    if (price > lastE9 && lastE9 > lastE21 && lastE21 > lastE50) { score += 2; reasons.push('Price above all EMAs (uptrend)'); }
    else if (price < lastE9 && lastE9 < lastE21 && lastE21 < lastE50) { score -= 2; reasons.push('Price below all EMAs (downtrend)'); }
    else if (price > lastE21) { score += 1; reasons.push('Price above EMA21'); }
    else if (price < lastE21) { score -= 1; reasons.push('Price below EMA21'); }
  }

  let signal;
  if (score >= 4) signal = 'STRONG BUY';
  else if (score >= 2) signal = 'BUY';
  else if (score <= -4) signal = 'STRONG SELL';
  else if (score <= -2) signal = 'SELL';
  else signal = 'NEUTRAL';

  return { signal, score, reasons };
}
