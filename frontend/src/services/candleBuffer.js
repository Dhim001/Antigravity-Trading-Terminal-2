/** In-memory OHLCV buffers — mutated in place; persisted across Vite HMR. */

const MAX_BARS = 10080;
/** Larger cap when prepending archived history (supports ~5y of 1h bars). */
const MAX_ARCHIVE_BARS = 50000;
const DEFAULT_BAR_SECS = 60;
const buffers = new Map();

/** Coerce ms or s timestamps to unix seconds. */
export function toUnixSeconds(t) {
  if (t == null || !Number.isFinite(t)) return null;
  return t > 9999999999 ? Math.floor(t / 1000) : Math.floor(t);
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

function storeBars(symbol, candles, intervalSecs = DEFAULT_BAR_SECS) {
  const deduped = dedupeCandles(candles, intervalSecs);
  if (deduped.length > MAX_BARS) {
    deduped.splice(0, deduped.length - MAX_BARS);
  }
  buffers.set(symbol, deduped);
  return deduped;
}

function restoreBuffersFromHmr() {
  if (!import.meta.hot?.data?.candleBuffers) return;
  const saved = import.meta.hot.data.candleBuffers;
  if (!(saved instanceof Map)) return;
  for (const [sym, bars] of saved) {
    if (Array.isArray(bars) && bars.length) buffers.set(sym, dedupeCandles(bars));
  }
}

restoreBuffersFromHmr();

if (import.meta.hot) {
  import.meta.hot.accept();
  import.meta.hot.dispose((data) => {
    data.candleBuffers = new Map(buffers);
  });
}

export function setCandleHistory(symbol, candles) {
  if (!symbol || !Array.isArray(candles)) return;
  storeBars(symbol, candles);
}

/**
 * Merge server history with client buffer. Avoids chart "rewind" on reconnect.
 * @returns {{ changed: boolean, fullRebuild: boolean }}
 */
export function mergeCandleHistory(symbol, incoming) {
  if (!symbol || !Array.isArray(incoming) || incoming.length === 0) {
    return { changed: false, fullRebuild: false };
  }

  const existing = buffers.get(symbol);
  const normalizedIncoming = dedupeCandles(incoming);

  if (!existing?.length) {
    storeBars(symbol, normalizedIncoming);
    return { changed: true, fullRebuild: true };
  }

  const lastIncoming = normalizedIncoming[normalizedIncoming.length - 1]?.time;
  const lastExisting = existing[existing.length - 1]?.time;
  if (lastIncoming == null || lastExisting == null) {
    storeBars(symbol, normalizedIncoming);
    return { changed: true, fullRebuild: true };
  }

  /* Server snapshot clearly older than what the client already has */
  if (lastIncoming < lastExisting - 120) {
    return { changed: false, fullRebuild: false };
  }

  const merged = dedupeCandles([...existing, ...normalizedIncoming]);
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

export function hasCandleHistory(symbol) {
  return buffers.has(symbol) && buffers.get(symbol).length > 0;
}

export function getCandles(symbol) {
  const buf = buffers.get(symbol);
  if (!buf?.length) return [];

  const needsNormalize = buf.some(
    (c) => c.time !== normalizeBarTime(c.time),
  );
  if (!needsNormalize) return buf;

  const fixed = dedupeCandles(buf);
  buffers.set(symbol, fixed);
  return fixed;
}

/** @returns {boolean} true if buffer changed */
export function applyLiveCandle(symbol, incoming) {
  if (!symbol || !incoming) return false;

  const buf = buffers.get(symbol);
  if (!buf?.length) return false;

  const bar = normalizeCandle(incoming);
  const last = buf[buf.length - 1];
  const lastBucket = normalizeBarTime(last.time);

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

export function getOldestBarTime(symbol) {
  const buf = getCandles(symbol);
  return buf.length ? buf[0].time : null;
}

/**
 * Prepend older archived bars (chart scroll-left). Ignores merge guard for stale snapshots.
 * @returns {{ changed: boolean, added: number }}
 */
export function prependCandleHistory(symbol, incoming) {
  if (!symbol || !Array.isArray(incoming) || incoming.length === 0) {
    return { changed: false, added: 0 };
  }

  const existing = buffers.get(symbol) || [];
  const normalized = dedupeCandles(incoming);
  if (!existing.length) {
    storeBars(symbol, normalized);
    return { changed: true, added: normalized.length };
  }

  const oldestExisting = existing[0].time;
  const olderOnly = normalized.filter((c) => c.time < oldestExisting);
  if (!olderOnly.length) {
    return { changed: false, added: 0 };
  }

  const merged = dedupeCandles([...olderOnly, ...existing]);
  const cap = MAX_ARCHIVE_BARS;
  if (merged.length > cap) {
    merged.splice(0, merged.length - cap);
  }
  buffers.set(symbol, merged);
  return { changed: true, added: olderOnly.length };
}

export function clearCandleBuffer(symbol) {
  buffers.delete(symbol);
}
