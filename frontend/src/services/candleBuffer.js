/** In-memory OHLCV buffers — mutated in place; persisted across Vite HMR. */

const MAX_BARS = 10080;
const HT_MAX_BARS = 600;
/** Bars requested on subscribe / REST candles (matches backend MARKET_CANDLE_SNAPSHOT_LIMIT). */
export const CHART_SNAPSHOT_BARS = 600;
/** Skip duplicate HTTP fetch when buffer already has enough for chart first paint. */
export const CHART_READY_MIN_BARS = 20;
/** Larger cap when prepending archived history (supports ~5y of 1h bars). */
const MAX_ARCHIVE_BARS = 50000;
const DEFAULT_BAR_SECS = 60;
const buffers = new Map();
/** LIVE_MASSIVE native HT buffers: key `${symbol}|${timeframeLabel}` */
const htBuffers = new Map();

/** Coerce ms or s timestamps to unix seconds. */
export function toUnixSeconds(t) {
  if (t == null || !Number.isFinite(t)) return null;
  return t > 9999999999 ? Math.floor(t / 1000) : Math.floor(t);
}

/** Revision / buffer key for a symbol at optional chart timeframe. */
export function candleBufferKey(symbol, timeframe = '1m') {
  if (!symbol) return '';
  if (!timeframe || timeframe === '1m') return symbol;
  return `${symbol}|${timeframe}`;
}

export function isHigherTimeframe(timeframe) {
  return Boolean(timeframe && timeframe !== '1m');
}

/** Floor a timestamp to the bar interval (default 1m). */
export function normalizeBarTime(t, intervalSecs = DEFAULT_BAR_SECS) {
  const sec = toUnixSeconds(t);
  if (sec == null) return null;
  return Math.floor(sec / intervalSecs) * intervalSecs;
}

export function normalizeCandle(c, intervalSecs = DEFAULT_BAR_SECS) {
  if (!c || c.time == null) return c;
  const time = normalizeBarTime(c.time, intervalSecs);
  return time == null ? c : { ...c, time };
}

/**
 * Collapse bars that share the same bucket (e.g. misaligned seed + live minute).
 * Preserves chronological open; merges high/low/close/volume.
 */
export function dedupeCandles(candles, intervalSecs = DEFAULT_BAR_SECS) {
  if (!Array.isArray(candles) || candles.length === 0) return [];

  const sorted = [...candles].sort(
    (a, b) => (toUnixSeconds(a.time) ?? 0) - (toUnixSeconds(b.time) ?? 0),
  );
  const buckets = new Map();
  const order = [];

  for (const c of sorted) {
    const t = normalizeBarTime(c.time, intervalSecs);
    if (t == null) continue;
    if (!buckets.has(t)) {
      buckets.set(t, { ...c, time: t });
      order.push(t);
      continue;
    }
    const b = buckets.get(t);
    b.high = Math.max(b.high, c.high);
    b.low = Math.min(b.low, c.low);
    b.close = c.close;
    b.volume = (b.volume || 0) + (c.volume || 0);
  }

  return order.map((t) => buckets.get(t));
}

function storeBars(symbol, candles, intervalSecs = DEFAULT_BAR_SECS, maxBars = MAX_BARS) {
  const deduped = dedupeCandles(candles, intervalSecs);
  if (deduped.length > maxBars) {
    deduped.splice(0, deduped.length - maxBars);
  }
  buffers.set(symbol, deduped);
  return deduped;
}

function storeHtBars(key, candles, intervalSecs) {
  const deduped = dedupeCandles(candles, intervalSecs);
  if (deduped.length > HT_MAX_BARS) {
    deduped.splice(0, deduped.length - HT_MAX_BARS);
  }
  htBuffers.set(key, deduped);
  return deduped;
}

function restoreBuffersFromHmr() {
  if (!import.meta.hot?.data?.candleBuffers) return;
  const saved = import.meta.hot.data.candleBuffers;
  if (!(saved instanceof Map)) return;
  for (const [sym, bars] of saved) {
    if (Array.isArray(bars) && bars.length) buffers.set(sym, dedupeCandles(bars));
  }
  const htSaved = import.meta.hot?.data?.htCandleBuffers;
  if (htSaved instanceof Map) {
    for (const [key, bars] of htSaved) {
      if (Array.isArray(bars) && bars.length) htBuffers.set(key, bars);
    }
  }
}

restoreBuffersFromHmr();

if (import.meta.hot) {
  import.meta.hot.accept();
  import.meta.hot.dispose((data) => {
    data.candleBuffers = new Map(buffers);
    data.htCandleBuffers = new Map(htBuffers);
  });
}

export function setCandleHistory(symbol, candles, timeframe = '1m', intervalSecs = DEFAULT_BAR_SECS) {
  if (!symbol || !Array.isArray(candles)) return;
  if (isHigherTimeframe(timeframe)) {
    storeHtBars(candleBufferKey(symbol, timeframe), candles, intervalSecs);
    return;
  }
  storeBars(symbol, candles, intervalSecs);
}

/**
 * Merge server history with client buffer. Avoids chart "rewind" on reconnect.
 * @returns {{ changed: boolean, fullRebuild: boolean }}
 */
export function mergeCandleHistory(symbol, incoming, timeframe = '1m', intervalSecs = DEFAULT_BAR_SECS) {
  if (!symbol || !Array.isArray(incoming) || incoming.length === 0) {
    return { changed: false, fullRebuild: false };
  }

  if (isHigherTimeframe(timeframe)) {
    return mergeHtCandleHistory(symbol, incoming, timeframe, intervalSecs);
  }

  const existing = buffers.get(symbol);
  const normalizedIncoming = dedupeCandles(incoming, intervalSecs);

  if (!existing?.length) {
    storeBars(symbol, normalizedIncoming, intervalSecs);
    return { changed: true, fullRebuild: true };
  }

  const lastIncoming = normalizedIncoming[normalizedIncoming.length - 1]?.time;
  const lastExisting = existing[existing.length - 1]?.time;
  if (lastIncoming == null || lastExisting == null) {
    storeBars(symbol, normalizedIncoming, intervalSecs);
    return { changed: true, fullRebuild: true };
  }

  if (lastIncoming < lastExisting - 120) {
    return { changed: false, fullRebuild: false };
  }

  const merged = dedupeCandles([...existing, ...normalizedIncoming], intervalSecs);
  if (merged.length > MAX_BARS) {
    merged.splice(0, merged.length - MAX_BARS);
  }

  const lastMerged = merged[merged.length - 1];
  const changed =
    merged.length !== existing.length
    || lastMerged?.time !== lastExisting
    || lastMerged?.close !== existing[existing.length - 1]?.close;

  buffers.set(symbol, merged);

  const fullRebuild =
    Math.abs(merged.length - existing.length) > 5
    || lastIncoming > lastExisting + 60;

  return { changed, fullRebuild };
}

function mergeHtCandleHistory(symbol, incoming, timeframe, intervalSecs) {
  const key = candleBufferKey(symbol, timeframe);
  const existing = htBuffers.get(key);
  const normalizedIncoming = dedupeCandles(incoming, intervalSecs);

  if (!existing?.length) {
    storeHtBars(key, normalizedIncoming, intervalSecs);
    return { changed: true, fullRebuild: true };
  }

  const lastIncoming = normalizedIncoming[normalizedIncoming.length - 1]?.time;
  const lastExisting = existing[existing.length - 1]?.time;
  if (lastIncoming == null || lastExisting == null) {
    storeHtBars(key, normalizedIncoming, intervalSecs);
    return { changed: true, fullRebuild: true };
  }

  if (lastIncoming < lastExisting - intervalSecs * 2) {
    return { changed: false, fullRebuild: false };
  }

  const merged = dedupeCandles([...existing, ...normalizedIncoming], intervalSecs);
  if (merged.length > HT_MAX_BARS) {
    merged.splice(0, merged.length - HT_MAX_BARS);
  }

  const lastMerged = merged[merged.length - 1];
  const changed =
    merged.length !== existing.length
    || lastMerged?.time !== lastExisting
    || lastMerged?.close !== existing[existing.length - 1]?.close;

  htBuffers.set(key, merged);
  const fullRebuild = Math.abs(merged.length - existing.length) > 3;
  return { changed, fullRebuild };
}

export function hasCandleHistory(symbol, timeframe = '1m') {
  if (isHigherTimeframe(timeframe)) {
    const buf = htBuffers.get(candleBufferKey(symbol, timeframe));
    return Boolean(buf && buf.length > 0);
  }
  return buffers.has(symbol) && buffers.get(symbol).length > 0;
}

/** True when WS/REST history is sufficient — safe to skip redundant HTTP candles fetch. */
export function hasChartReadyHistory(symbol, minBars = CHART_READY_MIN_BARS, timeframe = '1m') {
  if (isHigherTimeframe(timeframe)) {
    const buf = htBuffers.get(candleBufferKey(symbol, timeframe));
    return Boolean(buf && buf.length >= minBars);
  }
  const buf = buffers.get(symbol);
  return Boolean(buf && buf.length >= minBars);
}

export function getCandles(symbol, timeframe = '1m', intervalSecs = DEFAULT_BAR_SECS) {
  if (isHigherTimeframe(timeframe)) {
    const buf = htBuffers.get(candleBufferKey(symbol, timeframe));
    if (!buf?.length) return [];
    const needsNormalize = buf.some((c) => c.time !== normalizeBarTime(c.time, intervalSecs));
    if (!needsNormalize) return buf;
    const fixed = dedupeCandles(buf, intervalSecs);
    htBuffers.set(candleBufferKey(symbol, timeframe), fixed);
    return fixed;
  }

  const buf = buffers.get(symbol);
  if (!buf?.length) return [];

  const needsNormalize = buf.some(
    (c) => c.time !== normalizeBarTime(c.time, intervalSecs),
  );
  if (!needsNormalize) return buf;

  const fixed = dedupeCandles(buf, intervalSecs);
  buffers.set(symbol, fixed);
  return fixed;
}

/** @returns {boolean} true if buffer changed */
export function applyLiveCandle(symbol, incoming, timeframe = '1m', intervalSecs = DEFAULT_BAR_SECS) {
  if (!symbol || !incoming) return false;

  if (isHigherTimeframe(timeframe)) {
    return applyLiveHtCandle(symbol, incoming, timeframe, intervalSecs);
  }

  const buf = buffers.get(symbol);
  if (!buf?.length) return false;

  const bar = normalizeCandle(incoming, intervalSecs);
  const last = buf[buf.length - 1];
  const lastBucket = normalizeBarTime(last.time, intervalSecs);

  if (lastBucket === bar.time) {
    const updated = {
      time: bar.time,
      open: last.open,
      high: Math.max(last.high, bar.high),
      low: Math.min(last.low, bar.low),
      close: bar.close,
      volume: bar.volume ?? last.volume,
    };
    if (
      last.time === updated.time
      && last.open === updated.open
      && last.high === updated.high
      && last.low === updated.low
      && last.close === updated.close
      && last.volume === updated.volume
    ) {
      return false;
    }
    buf[buf.length - 1] = updated;
    return true;
  }

  if (bar.time < lastBucket) return false;

  buf.push(bar);
  if (buf.length > MAX_BARS) buf.shift();
  return true;
}

function applyLiveHtCandle(symbol, incoming, timeframe, intervalSecs) {
  const key = candleBufferKey(symbol, timeframe);
  const buf = htBuffers.get(key);
  if (!buf?.length) return false;

  const bar = normalizeCandle(incoming, intervalSecs);
  const last = buf[buf.length - 1];
  const lastBucket = normalizeBarTime(last.time, intervalSecs);

  if (lastBucket === bar.time) {
    const updated = {
      time: bar.time,
      open: last.open,
      high: Math.max(last.high, bar.high),
      low: Math.min(last.low, bar.low),
      close: bar.close,
      volume: (last.volume || 0) + (bar.volume || 0),
    };
    if (
      last.open === updated.open
      && last.high === updated.high
      && last.low === updated.low
      && last.close === updated.close
      && last.volume === updated.volume
    ) {
      return false;
    }
    buf[buf.length - 1] = updated;
    return true;
  }

  if (bar.time < lastBucket) return false;
  buf.push(bar);
  if (buf.length > HT_MAX_BARS) buf.shift();
  return true;
}

/**
 * Aggregate recent 1m bars into the current HT bucket and patch the HT buffer (LIVE_MASSIVE).
 * @returns {boolean} true if HT buffer changed
 */
export function patchHtFormingBar(symbol, timeframe, intervalSecs) {
  if (!symbol || !timeframe || timeframe === '1m' || intervalSecs <= 60) return false;

  const raw1m = buffers.get(symbol);
  const htKey = candleBufferKey(symbol, timeframe);
  const htBuf = htBuffers.get(htKey);
  if (!raw1m?.length || !htBuf?.length) return false;

  const tailBars = Math.min(raw1m.length, Math.ceil(intervalSecs / 60) + 2);
  const rawSlice = raw1m.slice(-tailBars);
  const aggregated = aggregateBucketFrom1m(rawSlice, intervalSecs);
  if (!aggregated) return false;

  const last = htBuf[htBuf.length - 1];
  if (last.time === aggregated.time) {
    if (
      last.open === aggregated.open
      && last.high === aggregated.high
      && last.low === aggregated.low
      && last.close === aggregated.close
      && (last.volume || 0) === (aggregated.volume || 0)
    ) {
      return false;
    }
    htBuf[htBuf.length - 1] = aggregated;
    return true;
  }

  if (aggregated.time > last.time) {
    htBuf.push(aggregated);
    if (htBuf.length > HT_MAX_BARS) htBuf.shift();
    return true;
  }

  return false;
}

/** Build one HT OHLCV bucket from 1m bars (forming bar). */
export function aggregateBucketFrom1m(raw, intervalSecs) {
  if (!raw?.length || intervalSecs <= 0) return null;

  const lastSec = toUnixSeconds(raw[raw.length - 1].time);
  if (lastSec == null) return null;
  const bucketTime = Math.floor(lastSec / intervalSecs) * intervalSecs;

  let open = null;
  let high = null;
  let low = null;
  let close = null;
  let volume = 0;
  let found = false;

  for (let i = raw.length - 1; i >= 0; i--) {
    const c = raw[i];
    const sec = toUnixSeconds(c.time);
    if (sec == null) continue;
    const bt = Math.floor(sec / intervalSecs) * intervalSecs;
    if (bt < bucketTime) break;
    if (bt !== bucketTime) continue;
    if (!found) {
      open = c.open;
      high = c.high;
      low = c.low;
      close = c.close;
      volume = c.volume || 0;
      found = true;
      continue;
    }
    open = c.open;
    high = Math.max(high, c.high);
    low = Math.min(low, c.low);
    volume += c.volume || 0;
  }

  if (!found) return null;
  return { time: bucketTime, open, high, low, close, volume };
}

export function getOldestBarTime(symbol, timeframe = '1m', intervalSecs = DEFAULT_BAR_SECS) {
  const buf = getCandles(symbol, timeframe, intervalSecs);
  return buf.length ? buf[0].time : null;
}

/**
 * Prepend older archived bars (chart scroll-left). Ignores merge guard for stale snapshots.
 * @returns {{ changed: boolean, added: number }}
 */
export function prependCandleHistory(symbol, incoming, timeframe = '1m', intervalSecs = DEFAULT_BAR_SECS) {
  if (!symbol || !Array.isArray(incoming) || incoming.length === 0) {
    return { changed: false, added: 0 };
  }

  if (isHigherTimeframe(timeframe)) {
    const key = candleBufferKey(symbol, timeframe);
    const existing = htBuffers.get(key) || [];
    const normalized = dedupeCandles(incoming, intervalSecs);
    if (!existing.length) {
      storeHtBars(key, normalized, intervalSecs);
      return { changed: true, added: normalized.length };
    }
    const oldestExisting = existing[0].time;
    const olderOnly = normalized.filter((c) => c.time < oldestExisting);
    if (!olderOnly.length) return { changed: false, added: 0 };
    const merged = dedupeCandles([...olderOnly, ...existing], intervalSecs);
    if (merged.length > HT_MAX_BARS) merged.splice(0, merged.length - HT_MAX_BARS);
    htBuffers.set(key, merged);
    return { changed: true, added: olderOnly.length };
  }

  const existing = buffers.get(symbol) || [];
  const normalized = dedupeCandles(incoming, intervalSecs);
  if (!existing.length) {
    storeBars(symbol, normalized, intervalSecs);
    return { changed: true, added: normalized.length };
  }

  const oldestExisting = existing[0].time;
  const olderOnly = normalized.filter((c) => c.time < oldestExisting);
  if (!olderOnly.length) {
    return { changed: false, added: 0 };
  }

  const merged = dedupeCandles([...olderOnly, ...existing], intervalSecs);
  const cap = MAX_ARCHIVE_BARS;
  if (merged.length > cap) {
    merged.splice(0, merged.length - cap);
  }
  buffers.set(symbol, merged);
  return { changed: true, added: olderOnly.length };
}

export function chartTimeframeSecs(timeframeLabel) {
  const map = {
    '1m': 60,
    '5m': 300,
    '15m': 900,
    '1H': 3600,
    '4H': 14400,
    '1D': 86400,
    '1h': 3600,
    '4h': 14400,
    '1d': 86400,
  };
  return map[timeframeLabel] || 60;
}

export function resolveHistoryTimeframe(meta) {
  const raw = meta?.interval || meta?.timeframe || '1m';
  if (raw === '1m' || raw === '1min') return '1m';
  const alias = {
    '1h': '1H',
    '4h': '4H',
    '1d': '1D',
    '1hour': '1H',
    '4hour': '4H',
    '1day': '1D',
  };
  return alias[String(raw).toLowerCase()] || raw;
}

export function clearCandleBuffer(symbol, timeframe = '1m') {
  if (isHigherTimeframe(timeframe)) {
    htBuffers.delete(candleBufferKey(symbol, timeframe));
    return;
  }
  buffers.delete(symbol);
}
